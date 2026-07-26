# ===============================
# File: Message_tab.py
# Purpose: Message authoring + render to message.png
# ===============================

from __future__ import annotations

import html as _html
import os
import re
import shutil
from pathlib import Path
from typing import Optional

# Optional converters (prefer mammoth for DOCX → HTML)
try:
    import mammoth  # type: ignore
except Exception:
    mammoth = None

try:
    from docx import Document  # type: ignore
except Exception:
    Document = None  # type: ignore

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:
    PdfReader = None  # type: ignore

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSizeF, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QTextDocument,
    QImage,
    QPainter,
    QPixmap,
    QFont,
)

from Editor import Editor
from editor_diagnostics import record_editor_failure
from message_format import (
    DEFAULT_MESSAGE_FONT,
    message_statistics,
    normalize_imported_message_html,
)
from message_history import MessageHistory
from settings_store import SettingsStore
from transactional_io import atomic_write_text
from config import (
    SETTINGS_FILE,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
)

from message_html import (
    ensure_message_html_from_emessage,
)

PUBLISHED_PAGE_URL_KEY = "published_page_url"


def _normalize_page_url(value: str) -> tuple[str, str]:
    """
    Normalize a user-entered page URL.

    Returns:
        (normalized_url, error_message)

    Empty input is valid and clears the saved URL.
    Non-empty input must resolve to http or https and include a host.
    """
    raw = (value or "").strip()
    if not raw:
        return "", ""

    parsed = QUrl.fromUserInput(raw)
    if not parsed.isValid() or parsed.isEmpty():
        return "", "Enter a valid http or https URL."

    scheme = parsed.scheme().lower().strip()
    if scheme not in {"http", "https"}:
        return "", "URL must start with http:// or https://."

    if not parsed.host().strip():
        return "", "URL must include a domain name."

    return parsed.toString(), ""

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_save_image(img: QImage, path: str) -> bool:
    """
    Windows-safe atomic PNG save using QtCore.QSaveFile.
    Uses QBuffer + QByteArray to encode first, then commits atomically.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Encode into memory
    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    if not buf.open(QtCore.QIODevice.WriteOnly):
        return False

    ok = img.save(buf, "PNG")
    buf.close()

    if not ok or ba.isEmpty():
        return False

    # Commit with retries (Windows transient locks)
    for _ in range(8):
        sf = QtCore.QSaveFile(str(p))
        if not sf.open(QtCore.QIODevice.WriteOnly):
            QtCore.QThread.msleep(80)
            continue

        written = sf.write(ba)
        if written != ba.size():
            sf.cancelWriting()
            QtCore.QThread.msleep(80)
            continue

        if sf.commit():
            return True

        sf.cancelWriting()
        QtCore.QThread.msleep(80)

    return False


MESSAGE_OVERLAY_PRESET_KEY = "message_overlay_preset"
MESSAGE_OVERLAY_OPACITY_KEY = "message_overlay_opacity"
DEFAULT_MESSAGE_OVERLAY_PRESET = "paper"
DEFAULT_MESSAGE_OVERLAY_OPACITY = 68
MESSAGE_OVERLAY_PRESETS: dict[str, tuple[tuple[int, int, int], str]] = {
    "black": ((0, 0, 0), "#ffffff"),
    "white": ((255, 255, 255), "#221710"),
    "paper": ((245, 235, 210), "#221710"),
    "clear": ((255, 255, 255), "#221710"),
}


def _message_overlay_settings(settings_path: str | os.PathLike) -> tuple[str, int, tuple[int, int, int], str]:
    data = SettingsStore(Path(settings_path).resolve().parent).as_dict()

    preset = str(data.get(MESSAGE_OVERLAY_PRESET_KEY, DEFAULT_MESSAGE_OVERLAY_PRESET)).strip().lower()
    if preset not in MESSAGE_OVERLAY_PRESETS:
        preset = DEFAULT_MESSAGE_OVERLAY_PRESET

    try:
        opacity = int(data.get(MESSAGE_OVERLAY_OPACITY_KEY, DEFAULT_MESSAGE_OVERLAY_OPACITY))
    except Exception:
        opacity = DEFAULT_MESSAGE_OVERLAY_OPACITY
    opacity = max(0, min(100, opacity))

    if preset == "clear":
        opacity = 0

    rgb, ink = MESSAGE_OVERLAY_PRESETS[preset]
    return preset, opacity, rgb, ink


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Drag-drop button
# ─────────────────────────────────────────────────────────────────────────────

class DropMessageButton(QtWidgets.QPushButton):
    file_dropped = Signal(str)

    def __init__(self, label: str = " Select Message File") -> None:
        super().__init__(label)
        self.setFont(QFont("Lucida Handwriting", 11))
        self.setFixedHeight(40)
        self.setAcceptDrops(True)
        self.setToolTip("Click or drag-and-drop a .txt, .docx, .pdf, or .odt file")
        self.setStyleSheet(self._default_style())

    def _default_style(self) -> str:
        return (
            "QPushButton {background-color:#1c1e26; border:1px solid #00d0ff;"
            " border-radius:8px; padding:8px; color:#e6e6e6; text-align:left;}"
            "QPushButton:hover {background-color:#00d0ff; color:#0e0f12;}"
        )

    def _glow_style(self) -> str:
        return (
            "QPushButton {background-color:#1c1e26; border:2px solid #00ffff;"
            " border-radius:8px; padding:8px; color:#ffffff;}"
        )

    def _is_supported_url(self, url: QtCore.QUrl) -> bool:
        if not url.isLocalFile():
            return False
        suffix = url.toLocalFile().lower()
        return suffix.endswith((".txt", ".docx", ".pdf", ".odt"))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(self._is_supported_url(u) for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self.setStyleSheet(self._glow_style())
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:  # type: ignore[override]
        self.setStyleSheet(self._default_style())

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        self.setStyleSheet(self._default_style())
        for url in event.mimeData().urls():
            if self._is_supported_url(url):
                self.file_dropped.emit(url.toLocalFile())
                break




class _CommitLockedLineEdit(QtWidgets.QLineEdit):
    """
    QLineEdit that becomes read-only after an explicit Enter commit.

    Double-click unlocks it for correction. This is used for fields where a
    value should not be accidentally changed by tab switching or focus loss.
    """

    unlocked = Signal()

    def __init__(self, text: str = "", parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(text, parent)
        self._commit_locked = False
        self._base_tooltip = ""

    def setToolTip(self, text: str) -> None:  # type: ignore[override]
        self._base_tooltip = text or ""
        super().setToolTip(self._locked_tooltip() if self._commit_locked else self._base_tooltip)

    def is_commit_locked(self) -> bool:
        return self._commit_locked

    def set_commit_locked(self, locked: bool) -> None:
        locked = bool(locked)
        self._commit_locked = locked
        self.setReadOnly(locked)
        self.setCursor(Qt.ArrowCursor if locked else Qt.IBeamCursor)
        self.setClearButtonEnabled(not locked)
        super().setToolTip(self._locked_tooltip() if locked else self._base_tooltip)
        self._apply_lock_style()

    def _locked_tooltip(self) -> str:
        base = self._base_tooltip.strip()
        suffix = "Double-click to unlock and edit. Press Enter to set again."
        return f"{base}\n{suffix}" if base else suffix

    def _apply_lock_style(self) -> None:
        if self._commit_locked:
            self.setStyleSheet(
                "QLineEdit {"
                " background:#151821;"
                " border:1px solid #3a4558;"
                " border-radius:6px;"
                " color:#bfc5d1;"
                " padding:4px 7px;"
                "}"
            )
        else:
            self.setStyleSheet("")

    def mark_invalid(self) -> None:
        self._commit_locked = False
        self.setReadOnly(False)
        self.setCursor(Qt.IBeamCursor)
        self.setClearButtonEnabled(True)
        self.setStyleSheet(
            "QLineEdit {"
            " border:1px solid #ff4d4f;"
            " border-radius:6px;"
            " padding:4px 7px;"
            "}"
        )

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._commit_locked:
            self.set_commit_locked(False)
            self.selectAll()
            self.unlocked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Main Message Tab
# ─────────────────────────────────────────────────────────────────────────────

class MessageHistoryDialog(QtWidgets.QDialog):
    def __init__(self, history: MessageHistory, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Message History")
        self.resize(620, 360)
        layout = QtWidgets.QVBoxLayout(self)

        self.revision_list = QtWidgets.QListWidget(self)
        for revision in history.list_revisions():
            timestamp = revision.timestamp.strftime("%b %d, %Y  %I:%M %p")
            item = QtWidgets.QListWidgetItem(f"{timestamp}\n{revision.preview}")
            item.setData(Qt.UserRole, str(revision.path))
            self.revision_list.addItem(item)
        layout.addWidget(self.revision_list)

        buttons = QtWidgets.QDialogButtonBox(self)
        self.restore_button = buttons.addButton(
            "Restore",
            QtWidgets.QDialogButtonBox.AcceptRole,
        )
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.restore_button.setEnabled(False)
        self.revision_list.currentItemChanged.connect(
            lambda current, _previous: self.restore_button.setEnabled(current is not None)
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_revision_path(self) -> Optional[Path]:
        item = self.revision_list.currentItem()
        if item is None:
            return None
        value = str(item.data(Qt.UserRole) or "").strip()
        return Path(value) if value else None


class MessageTab(QtWidgets.QWidget):
    """
    Message authoring tab.

    Public signals consumed by Nexus:
    - text_selected: emits HTML for the shared HTML preview.
    - preview_image: emits message.png or wall.png thumbnails for the shared image preview.
    - published_page_url_changed: notifies Forge when the saved page URL changes.
    """
    text_selected = Signal(str)
    preview_image = Signal(QPixmap)
    published_page_url_changed = Signal(str)

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self.project_root = project_root
        self.settings_path = os.path.join(project_root, SETTINGS_FILE)
        self.settings_store = SettingsStore(project_root)
        self.message_history = MessageHistory(project_root)

        # Compatibility cache (disk is authoritative)
        self.current_html: str = ""

        self._load_settings()
        (
            self.overlay_preset,
            self.overlay_opacity,
            _overlay_rgb,
            _overlay_ink,
        ) = _message_overlay_settings(self.settings_path)
        self.overlay_buttons: dict[str, QtWidgets.QPushButton] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QtWidgets.QLabel("Your Letter’s Message")
        header.setFont(QFont("Lucida Handwriting", 14))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        self.title_recipient_container = QtWidgets.QWidget(self)
        title_recipient_layout = QtWidgets.QHBoxLayout(self.title_recipient_container)
        title_recipient_layout.setContentsMargins(0, 0, 0, 0)
        title_recipient_layout.setSpacing(10)

        def _field_label(text: str) -> QtWidgets.QLabel:
            lbl = QtWidgets.QLabel(text)
            lbl.setFont(QFont("Lucida Handwriting", 10))
            lbl.setStyleSheet("color:#d7e7ef;")
            lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
            return lbl

        self.title_input = QtWidgets.QLineEdit(self.settings.get("recipient_title", ""))
        self.title_input.setPlaceholderText("Letter title")
        self.title_input.setMinimumWidth(170)

        saved_name = str(self.settings.get("recipient_name", "")).strip()
        self.name_input = _CommitLockedLineEdit(saved_name)
        self.name_input.setPlaceholderText("Name")
        self.name_input.setMinimumWidth(150)
        self.name_input.setToolTip("Press Enter to set Recipient. Double-click later to edit.")
        self._set_name_locked(bool(saved_name and self.settings.get("recipient_name_locked", True)))

        saved_url, _url_error = _normalize_page_url(str(self.settings.get(PUBLISHED_PAGE_URL_KEY, "")))
        self.url_input = _CommitLockedLineEdit(saved_url)
        self.url_input.setPlaceholderText("https://username.github.io/page/")
        self.url_input.setMinimumWidth(260)
        self.url_input.setToolTip(
            "Press Enter to set the GitHub Pages URL used by Forge → Open Published Letter."
        )
        self._set_url_locked(bool(saved_url and self.settings.get("published_page_url_locked", True)))

        title_recipient_layout.addWidget(_field_label("Letter Title:"), 0)
        title_recipient_layout.addWidget(self.title_input, 2)
        title_recipient_layout.addWidget(_field_label("Recipient:"), 0)
        title_recipient_layout.addWidget(self.name_input, 2)
        title_recipient_layout.addWidget(_field_label("URL:"), 0)
        title_recipient_layout.addWidget(self.url_input, 4)

        self.title_input.editingFinished.connect(self._save_title_settings)
        self.name_input.returnPressed.connect(self._commit_recipient_name)
        self.url_input.returnPressed.connect(self._commit_page_url)
        self.name_input.unlocked.connect(
            lambda: self.status.setText("Recipient unlocked. Press Enter to set again.")
        )
        self.url_input.unlocked.connect(lambda: self.status.setText("URL unlocked. Press Enter to set again."))

        layout.addWidget(self.title_recipient_container)

        self.btn = DropMessageButton()
        self.btn.clicked.connect(self.select_file)
        self.btn.file_dropped.connect(self.handle_drop)
        layout.addWidget(self.btn)

        layout.addWidget(self._build_overlay_controls())

        self.status = QtWidgets.QLabel()
        self.status.setFont(QFont("Lucida Handwriting", 10))
        self.status.setStyleSheet("color:#bfc5d1;")
        layout.addWidget(self.status)

        btns = QtWidgets.QHBoxLayout()
        self.edit_btn = QtWidgets.QPushButton("Edit")
        self.edit_btn.setFont(QFont("Lucida Handwriting", 11))
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self.open_editor)
        btns.addWidget(self.edit_btn)

        self.view_btn = QtWidgets.QPushButton("Preview")
        self.view_btn.setFont(QFont("Lucida Handwriting", 11))
        self.view_btn.setEnabled(False)
        self.view_btn.clicked.connect(self._emit_preview)
        btns.addWidget(self.view_btn)

        self.history_btn = QtWidgets.QPushButton("History")
        self.history_btn.setFont(QFont("Lucida Handwriting", 11))
        self.history_btn.clicked.connect(self.open_history)
        btns.addWidget(self.history_btn)
        layout.addLayout(btns)

        self.statistics_label = QtWidgets.QLabel()
        self.statistics_label.setAlignment(Qt.AlignCenter)
        self.statistics_label.setStyleSheet("color:#98a6b8;font-size:11px;")
        layout.addWidget(self.statistics_label)

        # Ensure fallback assets + show something immediately
        self._check_existing()

    def _build_overlay_controls(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        title = QtWidgets.QLabel("Text background")
        title.setStyleSheet("color:#d7e7ef;")
        row.addWidget(title)

        for key in ("black", "white", "paper", "clear"):
            button = QtWidgets.QPushButton(key.title(), panel)
            button.setCheckable(True)
            button.setMaximumWidth(72)
            button.clicked.connect(
                lambda _checked=False, preset=key: self._set_overlay_preset(preset)
            )
            self.overlay_buttons[key] = button
            row.addWidget(button)

        row.addSpacing(6)
        self.overlay_opacity_label = QtWidgets.QLabel()
        self.overlay_opacity_label.setStyleSheet("color:#98a6b8;")
        row.addWidget(self.overlay_opacity_label)

        self.overlay_opacity_slider = QtWidgets.QSlider(Qt.Horizontal, panel)
        self.overlay_opacity_slider.setRange(0, 100)
        self.overlay_opacity_slider.setMaximumWidth(180)
        self.overlay_opacity_slider.valueChanged.connect(self._set_overlay_opacity)
        row.addWidget(self.overlay_opacity_slider, 1)

        self._overlay_preview_timer = QtCore.QTimer(self)
        self._overlay_preview_timer.setSingleShot(True)
        self._overlay_preview_timer.setInterval(80)
        self._overlay_preview_timer.timeout.connect(self._refresh_overlay_previews)
        self._sync_overlay_controls()
        return panel

    def _set_overlay_preset(self, preset: str) -> None:
        if preset not in MESSAGE_OVERLAY_PRESETS:
            return
        self.overlay_preset = preset
        if preset == "clear":
            self.overlay_opacity = 0
        self._save_overlay_settings()
        self._sync_overlay_controls()
        self._overlay_preview_timer.start()

    def _set_overlay_opacity(self, value: int) -> None:
        self.overlay_opacity = max(0, min(100, int(value)))
        if self.overlay_preset == "clear" and self.overlay_opacity:
            self.overlay_preset = DEFAULT_MESSAGE_OVERLAY_PRESET
        self._save_overlay_settings()
        self._sync_overlay_controls()
        self._overlay_preview_timer.start()

    def _save_overlay_settings(self) -> None:
        self.settings = self.settings_store.update_fields(
            message_overlay_preset=self.overlay_preset,
            message_overlay_opacity=int(self.overlay_opacity),
        )

    def _sync_overlay_controls(self) -> None:
        for key, button in self.overlay_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == self.overlay_preset)
            button.blockSignals(False)
        self.overlay_opacity_slider.blockSignals(True)
        self.overlay_opacity_slider.setValue(int(self.overlay_opacity))
        self.overlay_opacity_slider.blockSignals(False)
        self.overlay_opacity_label.setText(f"Opacity {int(self.overlay_opacity)}%")

    def _preview_html(self, html: str) -> str:
        _preset, opacity, rgb, ink = _message_overlay_settings(self.settings_path)
        red, green, blue = rgb
        alpha = opacity / 100
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            "html,body{min-height:100%;margin:0;}"
            f"body{{padding:32px;color:{ink};background:rgba({red},{green},{blue},{alpha:.3f});"
            f"font-family:{DEFAULT_MESSAGE_FONT},fantasy;text-align:center;line-height:2;}}"
            "</style></head><body>"
            f"{html or '<p></p>'}</body></html>"
        )

    def _refresh_overlay_previews(self) -> None:
        html = self.current_html
        if not html and self._html_path().is_file():
            try:
                html = self._html_path().read_text(encoding="utf-8")
            except OSError:
                html = ""
        self.text_selected.emit(self._preview_html(html))
        if html:
            self._generate_image(html)
        self._emit_best_preview()

    def _update_statistics(self, html: Optional[str] = None) -> None:
        value = self.current_html if html is None else html
        stats = message_statistics(value or "")
        reading = f"About {stats.reading_minutes} min" if stats.reading_minutes else "About 0 min"
        self.statistics_label.setText(
            f"Words: {stats.words:,}  |  Characters: {stats.characters:,}  |  {reading}"
        )

    def open_history(self) -> None:
        dialog = MessageHistoryDialog(self.message_history, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        revision_path = dialog.selected_revision_path()
        if revision_path is not None:
            self._restore_revision(revision_path)

    def _restore_revision(self, revision_path: Path) -> None:
        try:
            html = self.message_history.restore(revision_path)
            self.current_html = html
            self.edit_btn.setEnabled(True)
            self._generate_image(html)
            self.text_selected.emit(self._preview_html(html))
            self._emit_best_preview()
            self._update_statistics(html)
            self.status.setText("Message revision restored.")
        except Exception as error:
            self.status.setText(f"Could not restore revision: {error}")

    # ──────────────────────────────────────────────────────────────────
    # Show hook: every time user clicks into Message tab
    # ──────────────────────────────────────────────────────────────────
    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_inputs_from_settings()
        # When user lands on the tab: force wall/html fallback, then show message.png if present else wall.
        self._ensure_wall_exists()
        self._ensure_message_html_exists()
        self._ensure_message_exists()
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Settings
    # ──────────────────────────────────────────────────────────────────
    def _load_settings(self) -> None:
        self.settings = self.settings_store.as_dict()

    def _sync_inputs_from_settings(self) -> None:
        """Refresh visible fields from settings.json without emitting changes."""
        self._load_settings()
        try:
            self.title_input.setText(str(self.settings.get("recipient_title", "")).strip())

            saved_name = str(self.settings.get("recipient_name", "")).strip()
            self.name_input.setText(saved_name)
            self._set_name_locked(bool(saved_name and self.settings.get("recipient_name_locked", True)))

            saved_url, _ = _normalize_page_url(str(self.settings.get(PUBLISHED_PAGE_URL_KEY, "")).strip())
            self.url_input.setText(saved_url)
            self._set_url_locked(bool(saved_url and self.settings.get("published_page_url_locked", True)))

            (
                self.overlay_preset,
                self.overlay_opacity,
                _overlay_rgb,
                _overlay_ink,
            ) = _message_overlay_settings(self.settings_path)
            self._sync_overlay_controls()
        except Exception:
            pass

    def _write_settings(self, *keys: str) -> None:
        updates = {key: self.settings[key] for key in keys if key in self.settings}
        self.settings = self.settings_store.update_fields(updates)

    def _set_name_locked(self, locked: bool) -> None:
        try:
            self.name_input.set_commit_locked(bool(locked))
        except Exception:
            pass

    def _set_url_locked(self, locked: bool) -> None:
        try:
            self.url_input.set_commit_locked(bool(locked))
        except Exception:
            pass

    def _save_title_settings(self) -> None:
        self.settings["recipient_title"] = self.title_input.text().strip()
        try:
            self._write_settings("recipient_title")
            self.status.setText("💾 Letter title saved.")
        except Exception as e:
            self.status.setText(f"❌ Error saving title: {e}")

    def _commit_recipient_name(self) -> None:
        """Set Recipient only when Enter is pressed, then lock it until double-click."""
        name = self.name_input.text().strip()
        self.name_input.setText(name)

        self.settings["recipient_title"] = self.title_input.text().strip()
        self.settings["recipient_name"] = name
        self.settings["recipient_name_locked"] = bool(name)

        try:
            self._write_settings("recipient_title", "recipient_name", "recipient_name_locked")
            self._set_name_locked(bool(name))
            self.status.setText(
                "Recipient set. Double-click to edit." if name else "Recipient cleared."
            )
        except Exception as e:
            self.status.setText(f"Error saving Recipient: {e}")

    def _commit_page_url(self) -> None:
        """Set URL only when Enter is pressed, then lock it until double-click."""
        normalized_url, url_error = _normalize_page_url(self.url_input.text())
        if url_error:
            self.url_input.mark_invalid()
            self.status.setText(f"❌ {url_error}")
            return

        self.settings["recipient_title"] = self.title_input.text().strip()
        self.settings[PUBLISHED_PAGE_URL_KEY] = normalized_url
        self.settings["published_page_url_locked"] = bool(normalized_url)

        try:
            self.url_input.setText(normalized_url)
            self._write_settings(
                "recipient_title",
                PUBLISHED_PAGE_URL_KEY,
                "published_page_url_locked",
            )
            self._set_url_locked(bool(normalized_url))
            self.published_page_url_changed.emit(normalized_url)
            self.status.setText("💾 Page URL set. Double-click to edit." if normalized_url else "Page URL cleared.")
        except Exception as e:
            self.status.setText(f"❌ Error saving URL: {e}")

    def _save_settings(self) -> None:
        """Backward-compatible save hook. Explicit fields still use Enter commits."""
        self._save_title_settings()

    def set_published_page_url(self, url: str, *, persist: bool = True, announce: bool = True) -> bool:
        normalized_url, url_error = _normalize_page_url(url)
        if url_error:
            if announce:
                self.status.setText(f"❌ {url_error}")
            return False

        self.settings["recipient_title"] = self.title_input.text().strip()
        self.settings[PUBLISHED_PAGE_URL_KEY] = normalized_url
        self.settings["published_page_url_locked"] = bool(normalized_url)

        try:
            self.url_input.setText(normalized_url)
            self._set_url_locked(bool(normalized_url))
        except Exception:
            pass

        if persist:
            try:
                self._write_settings(
                    "recipient_title",
                    PUBLISHED_PAGE_URL_KEY,
                    "published_page_url_locked",
                )
            except Exception as e:
                if announce:
                    self.status.setText(f"❌ Error saving URL: {e}")
                return False

        self.published_page_url_changed.emit(normalized_url)
        if announce:
            self.status.setText("💾 Page URL set. Double-click to edit." if normalized_url else "Page URL cleared.")
        return True

    # ──────────────────────────────────────────────────────────────────
    # Paths (AUTHORITATIVE)
    # ──────────────────────────────────────────────────────────────────
    def _html_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/message/message.html
        return Path(self.project_root) / "gallery" / "user" / "message" / "message.html"

    def _png_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/message/message.png
        return Path(self.project_root) / "gallery" / "user" / "message" / "message.png"

    def _wall_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/pages/wall.png
        return Path(self.project_root) / USER_PAGES_DIR / "wall.png"

    def _default_message_bg_path(self) -> Path:
        # DEFAULT FALLBACK: gallery/app/pages/Dmessage.png
        return Path(self.project_root) / "gallery" / "app" / "pages" / "Dmessage.png"

    def _ensure_message_html_exists(self, *, overwrite: bool = False) -> bool:
        """Guarantee message.html exists by rebuilding it from Emessage.docx when needed."""
        try:
            existed = self._html_path().is_file() and self._html_path().stat().st_size > 0
            html_path = ensure_message_html_from_emessage(self.project_root, overwrite=overwrite)
            if html_path.is_file():
                self.current_html = html_path.read_text(encoding="utf-8")
                if overwrite or not existed:
                    self.current_html = normalize_imported_message_html(self.current_html)
                    atomic_write_text(html_path, self.current_html)
                if hasattr(self, "edit_btn"):
                    self.edit_btn.setEnabled(True)
                return True
        except Exception as e:
            try:
                self.status.setText(f"⚠️ Could not rebuild message.html from Emessage.docx: {e}")
            except Exception:
                pass
        return False

    # ──────────────────────────────────────────────────────────────────
    # Fallback pipeline (Dmessage → wall, then wall → message.png if needed)
    # ──────────────────────────────────────────────────────────────────
    def _ensure_wall_exists(self) -> bool:
        """
        Guarantees wall.png exists by copying:
            gallery/app/pages/Dmessage.png -> gallery/user/pages/wall.png
        if wall.png is missing.
        """
        wall_path = self._wall_path()
        if wall_path.exists():
            return True

        src = self._default_message_bg_path()
        try:
            wall_path.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copyfile(str(src), str(wall_path))
                return True
        except Exception:
            pass
        return wall_path.exists()

    def _ensure_message_exists(self) -> None:
        """
        Guarantees message.png exists.
        - If message.html is missing, recreate it from Emessage.docx first.
        - If missing, render using current_html if available,
          else render a blank message (wall-only).
        - If render fails, force message.png = wall.png (scaled).
        """
        self._ensure_message_html_exists()

        out_png = self._png_path()
        if out_png.exists():
            return

        # Ensure wall is there first
        self._ensure_wall_exists()

        # Try render from HTML if any, else blank
        try:
            html = (self._html_path().read_text(encoding="utf-8") if self._html_path().exists() else "").strip()
            if not html:
                html = "<p></p>"
            self._generate_image(html)
            if out_png.exists():
                return
        except Exception:
            pass

        # Hard fallback: copy wall into message.png (scaled to 2048×3072)
        try:
            wall_path = self._wall_path()
            if wall_path.exists():
                wall_img = QImage(str(wall_path))
                if not wall_img.isNull():
                    FULL_W, FULL_H = 2048, 3072
                    wall_img = wall_img.scaled(
                        FULL_W, FULL_H,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation
                    )
                    canvas = QImage(FULL_W, FULL_H, QImage.Format_ARGB32)
                    canvas.fill(QtGui.QColor(14, 14, 18, 255))
                    p = QPainter(canvas)
                    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                    p.drawImage(0, 0, wall_img)
                    p.end()
                    _atomic_save_image(canvas, str(out_png))
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # Existing content load
    # ──────────────────────────────────────────────────────────────────
    def _check_existing(self) -> None:
        # Force the fallback pipeline first so the tab never shows black/empty.
        self._ensure_wall_exists()
        self._ensure_message_html_exists()
        self._ensure_message_exists()

        html_path = self._html_path()
        loaded_html = False

        if html_path.is_file():
            try:
                self.current_html = html_path.read_text(encoding="utf-8")
                self.edit_btn.setEnabled(True)
                loaded_html = True
            except Exception as e:
                self.status.setText(f"⚠️ Failed to read message.html: {e}")

        # Emit best preview immediately (message.png if exists, else wall)
        self._emit_best_preview()
        self._update_statistics(self.current_html)

        if loaded_html:
            self.text_selected.emit(self._preview_html(self.current_html))

    # ──────────────────────────────────────────────────────────────────
    # Preview helpers
    # ──────────────────────────────────────────────────────────────────
    def _emit_best_preview(self) -> None:
        """
        Rule:
        - If message.png exists, show it.
        - Else show wall.png (which is guaranteed by Dmessage fallback).
        """
        png_path = self._png_path()
        if png_path.is_file():
            full_pix = QPixmap(str(png_path))
            if not full_pix.isNull():
                thumb = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(thumb)
                self.view_btn.setEnabled(True)
                self.status.setText("🖼️ Showing message.png")
                return

        wall_path = self._wall_path()
        if wall_path.is_file():
            full_pix = QPixmap(str(wall_path))
            if not full_pix.isNull():
                thumb = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(thumb)
                self.view_btn.setEnabled(False)
                self.status.setText("🧱 Showing wall.png (fallback)")
                return

        # If we get here, fallback assets are missing/corrupt
        self.status.setText("❌ No message.png or wall.png available (fallback missing).")

    # ──────────────────────────────────────────────────────────────────
    # File selection / drop
    # ──────────────────────────────────────────────────────────────────
    def select_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Message File",
            "",
            "Text/Doc/PDF/ODT (*.txt *.docx *.pdf *.odt)",
        )
        if path:
            self._process_file(path)

    def handle_drop(self, path: str) -> None:
        if os.path.exists(path):
            self._process_file(path)

    # ──────────────────────────────────────────────────────────────────
    # Editor
    # ──────────────────────────────────────────────────────────────────
    def open_editor(self) -> None:
        html_path = self._html_path()
        if not html_path.is_file() or html_path.stat().st_size <= 0:
            self._ensure_message_html_exists()

        if html_path.is_file():
            try:
                html_for_editor = html_path.read_text(encoding="utf-8")
            except Exception:
                html_for_editor = self.current_html or ""
        else:
            html_for_editor = self.current_html or "<p></p>"

        full_pix: Optional[QPixmap] = None
        png_path = self._png_path()
        if png_path.is_file():
            candidate = QPixmap(str(png_path))
            if not candidate.isNull():
                full_pix = candidate

        dlg = Editor(html_for_editor, full_pix, parent=self)
        dlg.autosaved.connect(self._handle_editor_autosaved)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._handle_editor_accepted(dlg)

    def _handle_editor_autosaved(self, html: str) -> None:
        self.current_html = html
        self.edit_btn.setEnabled(True)
        self._update_statistics(html)
        self.text_selected.emit(self._preview_html(html))
        QtCore.QTimer.singleShot(0, lambda saved=html: self._refresh_autosaved_preview(saved))

    def _refresh_autosaved_preview(self, html: str) -> None:
        if html != self.current_html:
            return
        self._generate_image(html)
        self._emit_best_preview()
        self.status.setText("Autosaved.")

    def _handle_editor_accepted(self, dlg: QtWidgets.QDialog) -> None:
        new_html: Optional[str] = None
        try:
            new_html = dlg.get_saved_html()  # type: ignore[attr-defined]
        except Exception:
            new_html = None

        if not new_html:
            self.status.setText("ℹ️ Editor closed without verified saved HTML.")
            return

        self.current_html = new_html
        self.status.setText("💾 message.html saved.")
        self.edit_btn.setEnabled(True)
        self._update_statistics(new_html)

        try:
            # Run preview work only after the modal editor has fully closed.
            self._ensure_wall_exists()
            self.text_selected.emit(self._preview_html(new_html))
            self._generate_image(new_html)
            self._emit_best_preview()
        except Exception as e:
            try:
                log_path = record_editor_failure(self.project_root, "refresh saved letter preview", e)
                self.status.setText(f"⚠️ Saved; preview refresh failed. See {log_path.name}.")
            except Exception:
                self.status.setText(f"⚠️ Saved; preview refresh failed: {e}")

    # ──────────────────────────────────────────────────────────────────
    # Preview button
    # ──────────────────────────────────────────────────────────────────
    def _emit_preview(self) -> None:
        # Preview button should prefer message.png; if missing, show wall fallback.
        self._ensure_wall_exists()
        self._ensure_message_exists()
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Extraction + processing
    # ──────────────────────────────────────────────────────────────────
    def extract_text(self, path: str) -> str:
        low = path.lower()
        try:
            if low.endswith(".txt"):
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
                esc = (
                    raw.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;")
                )
                return esc.replace("\n", "<br>")

            if low.endswith(".docx"):
                if mammoth is not None:
                    try:
                        with open(path, "rb") as docx_file:
                            res = mammoth.convert_to_html(docx_file)
                        return res.value
                    except Exception:
                        pass
                if Document is not None:
                    try:
                        doc = Document(path)
                        parts = []
                        for p in doc.paragraphs:
                            t = p.text.strip()
                            if t:
                                parts.append(_html.escape(t))
                        return "<br>".join(parts)
                    except Exception:
                        pass
                return ""

            if low.endswith(".pdf"):
                if PdfReader is not None:
                    try:
                        reader = PdfReader(path)
                        pages = [(page.extract_text() or "").replace("\n", "<br>") for page in reader.pages]
                        return "<br><br>".join(pages)
                    except Exception:
                        pass
                try:
                    import pdfplumber  # type: ignore
                    txt_pages = []
                    with pdfplumber.open(path) as pdf:
                        for page in pdf.pages:
                            txt = (page.extract_text() or "").strip()
                            if txt:
                                txt_pages.append(_html.escape(txt).replace("\n", "<br>"))
                    return "<br><br>".join(txt_pages)
                except Exception:
                    return ""

            if low.endswith(".odt"):
                try:
                    from odf.opendocument import load as _load  # type: ignore
                    from odf.text import P as _P  # type: ignore
                    doc = _load(path)
                    paras = doc.getElementsByType(_P)
                    chunks = []
                    for p in paras:
                        text = "".join(getattr(n, "data", "") for n in p.childNodes)
                        text = _html.escape(text)
                        if text.strip():
                            chunks.append(text)
                    return "<br>".join(chunks)
                except Exception:
                    return ""
        except Exception as e:
            self.status.setText(f"❌ Error reading file: {e}")
        return ""

    def _process_file(self, path: str) -> None:
        html = self.extract_text(path)
        if not html.strip():
            self.status.setText("⚠️ That file had no extractable text.")
            return

        self.message_history.snapshot_current_if_changed()
        html = normalize_imported_message_html(html)

        html_path = self._html_path()
        try:
            atomic_write_text(html_path, html)
            self.current_html = html
            self.edit_btn.setEnabled(True)
            self.status.setText(f"💾 message.html saved ({Path(path).name}).")
            self._update_statistics(html)
            self.text_selected.emit(self._preview_html(html))
        except Exception as e:
            self.status.setText(f"❌ Error saving message.html: {e}")
            return

        # Always guarantee wall before rendering
        self._ensure_wall_exists()

        self._generate_image(html)
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Render message.png (stable, saved in same folder as message.html)
    # ──────────────────────────────────────────────────────────────────
    def _generate_image(self, html: str) -> None:
        """
        Produces (SOURCE):
            gallery/user/message/message.png

        Fallback rules:
        - wall.png is guaranteed (Dmessage -> wall if missing)
        - if save fails, we also ensure message.png exists via _ensure_message_exists()
        """
        try:
            # Guarantee wall exists first
            self._ensure_wall_exists()

            FULL_W, FULL_H = 2048, 3072
            MARGIN_LR = 100
            MARGIN_TOP = 100
            MARGIN_BOTTOM = 100
            TEXT_WIDTH = FULL_W - 2 * MARGIN_LR
            TEXT_HEIGHT = FULL_H - MARGIN_TOP - MARGIN_BOTTOM

            out_png = self._png_path()
            out_png.parent.mkdir(parents=True, exist_ok=True)

            # Opaque base first (prevents “transparent saved as black” surprises)
            canvas = QImage(FULL_W, FULL_H, QImage.Format_ARGB32)
            canvas.fill(QtGui.QColor(14, 14, 18, 255))

            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            # Background wall (SOURCE): gallery/user/pages/wall.png
            wall_path = self._wall_path()
            if wall_path.exists():
                wall_img = QImage(str(wall_path))
                if not wall_img.isNull():
                    wall_img = wall_img.scaled(
                        FULL_W,
                        FULL_H,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation
                    )
                    painter.drawImage(0, 0, wall_img)

            # User-controlled text background overlay.
            _preset, overlay_opacity, overlay_rgb, ink_color = _message_overlay_settings(self.settings_path)
            if overlay_opacity > 0:
                r, g, b = overlay_rgb
                overlay = QtGui.QColor(r, g, b, int(round(255 * (overlay_opacity / 100.0))))
                painter.fillRect(0, 0, FULL_W, FULL_H, overlay)

            # HTML text
            doc = QTextDocument()
            default_font = QFont(DEFAULT_MESSAGE_FONT, 12)
            default_font.setStyleHint(QFont.Fantasy)
            doc.setDefaultFont(default_font)

            doc.setDefaultStyleSheet(
                f"body {{ color: {ink_color}; background: transparent;"
                f" font-family: {DEFAULT_MESSAGE_FONT}, fantasy;"
                " text-align: center; line-height: 2; }}"
                "p { margin: 0 0 12px 0; }"
                "br { line-height: 2; }"
            )

            doc.setHtml(html)
            doc.setTextWidth(TEXT_WIDTH)
            doc.setPageSize(QSizeF(TEXT_WIDTH, TEXT_HEIGHT))

            painter.save()
            painter.translate(MARGIN_LR, MARGIN_TOP)
            painter.setClipRect(0, 0, TEXT_WIDTH, TEXT_HEIGHT)
            doc.drawContents(painter, QtCore.QRectF(0, 0, TEXT_WIDTH, TEXT_HEIGHT))
            painter.restore()

            painter.end()

            if not _atomic_save_image(canvas, str(out_png)):
                raise RuntimeError("Failed to save message.png")

            self.view_btn.setEnabled(True)
            self.status.setText("🔍 message.png generated.")
        except Exception as e:
            # Force existence (wall->message) if generation fails
            self.status.setText(f"❌ Error generating message.png: {e}")
            self._ensure_message_exists()
