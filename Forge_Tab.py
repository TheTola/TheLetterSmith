# ===============================
# File: Forge_Tab.py
# Purpose: Forge tab — deterministic Play build + Load (Recipient → Title)
#
# FINAL BUTTON BEHAVIOR:
# - Generate:
#     Builds Play bundle, opens browser (index.html). Does NOT open folders.
# - Seal the Letter:
#     Builds Play bundle, opens the Play folder (GitHub Pages target).
#
# LOAD (FINAL SPEC):
# - Clicking Load opens a drop-down menu (QMenu)
# - Menu lists ALL recipient folders under: output/Play/<recipient>/
# - Hovering a recipient expands a submenu listing ALL titles (from <title> in index.html)
# - Clicking a title loads that build back into canonical SOURCE:
#     gallery/user/pages/*
#     gallery/user/message/*
#     gallery/user/sounds/appssong/*  (restored single track or playlist state)
# - Does not touch app-owned controls or sound effects.
# - Updates settings.json:
#     recipient_name, recipient_title
#
# NEW:
# - When a saved letter is loaded, ForgeTab emits a signal so Nexus can immediately
#   refresh the Forge preview (cover.png) + caption (project title).
# ===============================

from __future__ import annotations

import json
import re
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QTextEdit
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QUrl

import generate  # <-- IMPORTANT: module import only
from message_html import read_text_normalized
from project_sync import project_fingerprint
from saved_letters import SavedLetter, SavedLetterCatalog
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    import_runtime_track,
    load_library,
    save_project_state,
    sync_current_compatibility,
)

from config import (
    SETTINGS_FILE,
    PUBLISHED_PAGE_URL_KEY,
    PLAY_METADATA_FILE,
    OUTPUT_PLAY_DIR,
    ensure_output_dirs,
    validate_required_images,
    MESSAGE_HTML_FILE,
    USER_PAGES_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
    MUSIC_FILE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_text(path: Path) -> str:
    try:
        return read_text_normalized(path)
    except Exception:
        return ""


def _extract_html_title(index_html: Path) -> str:
    txt = _read_text(index_html)
    m = re.search(r"<title>\s*(.*?)\s*</title>", txt, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return index_html.parent.name
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t or index_html.parent.name


def _safe_clear_dir_contents(dir_path: Path) -> Tuple[int, int]:
    files_deleted = 0
    dirs_deleted = 0
    if not dir_path.exists() or not dir_path.is_dir():
        return files_deleted, dirs_deleted

    for entry in dir_path.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink(missing_ok=True)
                files_deleted += 1
            elif entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                dirs_deleted += 1
        except Exception:
            pass

    return files_deleted, dirs_deleted


def _load_settings(root: Path) -> dict:
    p = root / SETTINGS_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _write_settings(root: Path, data: dict) -> None:
    p = root / SETTINGS_FILE
    try:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _normalize_page_url(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    parsed = QUrl.fromUserInput(candidate)
    if not parsed.isValid():
        return ""
    if parsed.scheme().lower() not in {"http", "https"}:
        return ""
    if not parsed.host():
        return ""
    return parsed.toString()


def _metadata_path(play_dir: Path) -> Path:
    return play_dir / PLAY_METADATA_FILE


def _read_play_metadata(play_dir: Path) -> dict:
    path = _metadata_path(play_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _humanize_slug(s: str) -> str:
    s2 = s.replace("_", " ").replace("-", " ").strip()
    s2 = re.sub(r"\s+", " ", s2)
    return s2.title() if s2 else s


def _get_generate_fn():
    """Return the canonical Play-bundle generator."""
    fn = getattr(generate, "generate_play_bundle", None)
    return fn if callable(fn) else None


def _open_folder(path: Path) -> None:
    QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


class SavedLetterCard(QtWidgets.QFrame):
    """Compact clickable saved-letter card with cover and metadata."""

    activated = QtCore.Signal(object)

    def __init__(self, entry: SavedLetter, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("savedLetterCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(184, 250)
        self.setMaximumWidth(220)
        self.setAccessibleName(f"Load {entry.title} for {entry.recipient}")
        self.setToolTip(str(entry.path))
        self.setStyleSheet(
            "QFrame#savedLetterCard{background:#111820;border:1px solid #334756;"
            "border-radius:10px;}"
            "QFrame#savedLetterCard:hover,QFrame#savedLetterCard:focus{"
            "background:#16242d;border:2px solid #00d0ff;}"
            "QLabel{background:transparent;}"
            "QPushButton{min-height:30px;background:#17313b;color:#e8fbff;"
            "border:1px solid #00b8d4;border-radius:6px;font-weight:700;}"
            "QPushButton:hover{background:#00b8d4;color:#081014;}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(5)

        self.cover = QLabel(self)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setFixedHeight(148)
        self.cover.setMinimumWidth(150)
        self.cover.setStyleSheet(
            "background:#0b1016;border:1px solid #273745;border-radius:7px;"
            "color:#72808e;font-weight:700;"
        )
        self._set_cover(entry.cover_path)
        root.addWidget(self.cover)

        title = QLabel(entry.title or "Untitled Letter", self)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:#f1fbff;font:700 11px 'Segoe UI';")
        title.setMaximumHeight(38)
        root.addWidget(title)

        recipient = QLabel(entry.recipient or "Unknown recipient", self)
        recipient.setAlignment(Qt.AlignCenter)
        recipient.setStyleSheet("color:#a9c3cf;font:9px 'Segoe UI';")
        recipient.setMaximumHeight(18)
        root.addWidget(recipient)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(4)
        date_label = QLabel(entry.modified_at.strftime("%b %d, %Y"), self)
        date_label.setStyleSheet("color:#778b98;font:8px 'Segoe UI';")
        status_label = QLabel(
            "Recovery" if entry.recovery else ("Published" if entry.published else "Local"),
            self,
        )
        status_label.setAlignment(Qt.AlignCenter)
        if entry.recovery:
            status_css = "color:#ffd8a8;background:#49341c;border:1px solid #9b6a28;"
        elif entry.published:
            status_css = "color:#9dffbd;background:#173925;border:1px solid #2d8a4d;"
        else:
            status_css = "color:#b9dfff;background:#173044;border:1px solid #326786;"
        status_label.setStyleSheet(
            status_css + "border-radius:5px;padding:2px 6px;font:700 8px 'Segoe UI';"
        )
        meta_row.addWidget(date_label)
        meta_row.addStretch(1)
        meta_row.addWidget(status_label)
        root.addLayout(meta_row)

        load_button = QPushButton("Load", self)
        load_button.clicked.connect(lambda: self.activated.emit(self.entry))
        root.addWidget(load_button)

    def _set_cover(self, cover_path: Optional[Path]) -> None:
        if cover_path is None or not cover_path.is_file():
            self.cover.setText("No cover\navailable")
            return
        pixmap = QtGui.QPixmap(str(cover_path))
        if pixmap.isNull():
            self.cover.setText("Cover could not\nbe loaded")
            return
        self.cover.setPixmap(
            pixmap.scaled(
                154,
                144,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.entry)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.entry)
            event.accept()
            return
        super().keyPressEvent(event)


class SavedLettersDialog(QtWidgets.QDialog):
    """Responsive, automatically refreshed gallery of saved letters."""

    letter_selected = QtCore.Signal(object)
    CARD_WIDTH = 202
    GRID_GAP = 10

    def __init__(self, project_root: Path, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.catalog = SavedLetterCatalog(self.project_root)
        self._signature: tuple = ()
        self._cards: list[SavedLetterCard] = []
        self._last_columns = 0

        self.setWindowTitle("Load Letters")
        self.setModal(False)
        self.setMinimumSize(560, 480)
        self.resize(760, 560)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setStyleSheet(
            "QDialog{background:#0d1117;color:#e8f8ff;}"
            "QLabel#savedLettersTitle{color:#00d0ff;font:700 17px 'Segoe UI';}"
            "QLabel#savedLettersCount{color:#8ea6b3;font:9px 'Segoe UI';}"
            "QScrollArea{background:transparent;border:none;}"
            "QWidget#savedLettersCanvas{background:transparent;}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Load Letters", self)
        title.setObjectName("savedLettersTitle")
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("savedLettersCount")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.count_label)
        root.addLayout(header)

        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas = QtWidgets.QWidget(self.scroll)
        self.canvas.setObjectName("savedLettersCanvas")
        self.grid = QtWidgets.QGridLayout(self.canvas)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setHorizontalSpacing(self.GRID_GAP)
        self.grid.setVerticalSpacing(self.GRID_GAP)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("No saved letters were found.", self.canvas)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color:#778996;font:11px 'Segoe UI';padding:40px;")
        self.empty_label.hide()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(1200)
        self.refresh_timer.timeout.connect(self.refresh_from_disk)

    @staticmethod
    def _entry_signature(entry: SavedLetter) -> tuple:
        cover_stamp = 0
        if entry.cover_path is not None:
            try:
                cover_stamp = entry.cover_path.stat().st_mtime_ns
            except OSError:
                cover_stamp = 0
        return (
            str(entry.path),
            entry.recipient,
            entry.title,
            entry.modified_at.timestamp(),
            entry.published_url,
            str(entry.cover_path or ""),
            cover_stamp,
            entry.recovery,
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh_from_disk(force=True)
        self.refresh_timer.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self.refresh_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self.refresh_timer.stop()
        super().closeEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._reflow_cards)

    def refresh_from_disk(self, *, force: bool = False) -> None:
        try:
            entries = self.catalog.list_entries()
        except Exception as error:
            self.count_label.setText("Catalog unavailable")
            self._show_empty(f"Saved letters could not be read:\n{error}")
            return

        signature = tuple(self._entry_signature(entry) for entry in entries)
        if not force and signature == self._signature:
            return
        self._signature = signature
        self._clear_cards()

        for entry in entries:
            card = SavedLetterCard(entry, self.canvas)
            card.activated.connect(self._select_entry)
            self._cards.append(card)

        self.count_label.setText(f"{len(entries)} saved letter{'s' if len(entries) != 1 else ''}")
        if entries:
            self.empty_label.hide()
            self._reflow_cards(force=True)
        else:
            self._show_empty("No saved letters were found.")

    def _clear_cards(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.hide()
                widget.deleteLater()
        self._cards.clear()
        self._last_columns = 0

    def _show_empty(self, message: str) -> None:
        self.empty_label.setText(message)
        self.empty_label.show()
        self.grid.addWidget(self.empty_label, 0, 0, 1, 1)

    def _reflow_cards(self, *, force: bool = False) -> None:
        if not self._cards:
            return
        viewport_width = max(1, self.scroll.viewport().width() - 8)
        columns = max(1, viewport_width // (self.CARD_WIDTH + self.GRID_GAP))
        if not force and columns == self._last_columns:
            return
        self._last_columns = columns
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def _select_entry(self, entry: SavedLetter) -> None:
        self.letter_selected.emit(entry)
        self.hide()


# ─────────────────────────────────────────────────────────────────────────────
# ForgeTab
# ─────────────────────────────────────────────────────────────────────────────

class ForgeTab(QtWidgets.QWidget):
    """
    - Generate:
        Builds Play bundle, opens browser
    - Seal the Letter:
        Builds Play bundle, opens Play folder (GitHub Pages target)
    - Load:
        Dropdown menu: Recipient -> Title -> Load build into gallery/user/*
    """

    # Public Nexus integration contract.
    letter_loaded = QtCore.Signal(dict)
    project_restored = QtCore.Signal(dict)
    correction_requested = QtCore.Signal(str, str)
    preview_requested = QtCore.Signal(str, str)
    preview_files_release_requested = QtCore.Signal()
    preview_visibility_changed = QtCore.Signal(bool)
    published_url_changed = QtCore.Signal(str)
    sync_requested = QtCore.Signal(str)
    preview_restart_requested = QtCore.Signal(str)

    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.saved_page_url = ""
        self.preview_mode_value = "portrait"
        self.preview_refresh_pending = True
        self._current_play_dir: Optional[Path] = None
        self._project_fingerprint = project_fingerprint(self.project_root)
        self._operation_active = False
        self._tab_active = False
        self._host_window: Optional[QtWidgets.QWidget] = None
        self._saved_letters_dialog: Optional[SavedLettersDialog] = None
        self._init_ui()
        self.refresh_saved_page_url()

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Forge")
        title.setFont(QFont("Segoe UI Semibold", 18))
        title.setStyleSheet("color:#00d0ff;")
        title.setAlignment(Qt.AlignCenter)
        title.setGraphicsEffect(self._shadow_effect(12))
        layout.addWidget(title)

        # Nexus mounts this compact control beside the shared preview.
        self.preview_format_panel = QtWidgets.QFrame()
        self.preview_format_panel.setObjectName("forgePreviewFormatPanel")
        format_layout = QHBoxLayout(self.preview_format_panel)
        format_layout.setContentsMargins(7, 4, 7, 4)
        format_layout.setSpacing(6)
        format_label = QLabel("Preview")
        format_label.setStyleSheet("color:#bfeef3;font-weight:700;")
        self.preview_mode_combo = QtWidgets.QComboBox(self.preview_format_panel)
        self.preview_mode_combo.addItem("Portrait", "portrait")
        self.preview_mode_combo.addItem("Landscape", "landscape")
        self.preview_mode_combo.addItem("Window", "window")
        self.preview_mode_combo.currentIndexChanged.connect(self._preview_mode_changed)
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.preview_mode_combo)
        self.preview_format_panel.setStyleSheet(
            "QFrame#forgePreviewFormatPanel{background:#121920;border:1px solid #31515f;border-radius:8px;}"
            "QComboBox{min-height:28px;padding:0 24px 0 8px;background:#1b252d;color:#e7fbff;"
            "border:1px solid #3b5966;border-radius:5px;}"
        )

        # Saved letters use the current card-gallery workflow. The popup
        # refreshes itself from disk and never needs a manual Refresh button.
        load_row = QHBoxLayout()
        load_row.setContentsMargins(0, 0, 0, 0)
        load_row.addStretch(1)
        self.load_btn = self._tiny_button("Load Letters")
        self.load_btn.setMinimumWidth(178)
        self.load_btn.setFixedHeight(40)
        self.load_btn.setToolTip("Browse saved letters by cover, title, recipient, and publication status")
        self.load_btn.clicked.connect(self.open_saved_letters)
        load_row.addWidget(self.load_btn)
        layout.addLayout(load_row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)

        self.generate_btn = self._styled_button(
            "Preview Letter",
            "#d66f00",
            "#ff9d22",
            "#fff8df",
        )
        self.generate_btn.setToolTip(
            "Rebuild the local letter and open it in the default browser"
        )
        self.generate_btn.clicked.connect(self.generate)
        actions.addWidget(self.generate_btn, 1)

        self.seal_btn = self._styled_button(
            "Publish Letter",
            "#51459b",
            "#7868db",
            "#ffffff",
        )
        self.seal_btn.setToolTip(
            "Build the local letter and publish it through the configured GitHub repository"
        )
        self.seal_btn.clicked.connect(self.seal_the_letter)
        actions.addWidget(self.seal_btn, 1)

        self.go_to_page_btn = self._page_button("Open Letter")
        self.go_to_page_btn.setMinimumWidth(210)
        self.go_to_page_btn.setMaximumWidth(16777215)
        self.go_to_page_btn.setToolTip(
            "Open the saved published URL, or the current local letter when no URL is saved"
        )
        self.go_to_page_btn.clicked.connect(self.go_to_page)
        actions.addWidget(self.go_to_page_btn, 1)

        # Compatibility aliases for newer and older Nexus code.
        self.preview_btn = self.generate_btn
        self.publish_btn = self.seal_btn
        self.open_letter_btn = self.go_to_page_btn

        layout.addLayout(actions)

        self.status = QTextEdit(readOnly=True)
        self.status.setFont(QFont("Segoe UI", 10))
        self.status.setMinimumHeight(150)
        self.status.setStyleSheet(
            "background:#11171d;border:1px solid #31515f;border-radius:7px;color:#dbe8ee;padding:6px;"
        )
        layout.addWidget(self.status, 1)

        utility_row = QHBoxLayout()
        utility_row.addStretch(1)
        self.go_to_gallery_btn = self._utility_button("Go to Gallery")
        self.go_to_gallery_btn.setToolTip("Open the folder containing all published letters")
        self.go_to_gallery_btn.clicked.connect(self.go_to_gallery)
        utility_row.addWidget(self.go_to_gallery_btn)
        utility_row.addStretch(1)
        layout.addLayout(utility_row)

        self._log("Ready.")

    def _preview_mode_changed(self, index: int) -> None:
        mode = str(self.preview_mode_combo.itemData(index) or "portrait")
        self.preview_mode_value = mode if mode in {"portrait", "landscape", "window"} else "portrait"
        current = self.current_play_index()
        if current is not None:
            self.preview_requested.emit(str(current), self.preview_mode_value)

    # ─────────────────────────────────────────────────────────────────────
    # Saved-letter card gallery
    # ─────────────────────────────────────────────────────────────────────
    def open_saved_letters(self) -> None:
        """Open the current card-based saved-letter browser."""
        dialog = self._saved_letters_dialog
        if dialog is None:
            dialog = SavedLettersDialog(self.project_root, self)
            dialog.letter_selected.connect(self._load_saved_letter)
            self._saved_letters_dialog = dialog
        dialog.refresh_from_disk(force=True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_load_menu(self) -> None:
        """Compatibility alias retained for older callers."""
        self.open_saved_letters()

    @staticmethod
    def _first_existing_directory(*candidates: Path) -> Optional[Path]:
        return next((candidate for candidate in candidates if candidate.is_dir()), None)

    def _load_saved_letter(self, entry: SavedLetter) -> None:
        play_dir = Path(entry.path).resolve()
        if not play_dir.is_dir():
            self._log("❌ The selected saved-letter folder is missing.")
            return

        src_gallery = play_dir / "gallery"
        src_pages = self._first_existing_directory(
            src_gallery / "pages",
            src_gallery / "user" / "pages",
            play_dir / "pages",
        )
        src_message = self._first_existing_directory(
            src_gallery / "message",
            src_gallery / "user" / "message",
            play_dir / "message",
        )
        src_sounds = self._first_existing_directory(
            src_gallery / "sounds",
            src_gallery / "user" / "sounds",
            play_dir / "sounds",
        )

        if src_pages is None:
            self._log("❌ Invalid saved letter: page images were not found.")
            return
        if src_message is None:
            self._log("❌ Invalid saved letter: message files were not found.")
            return

        dst_pages = (self.project_root / USER_PAGES_DIR).resolve()
        dst_message = (self.project_root / USER_MESSAGE_DIR).resolve()
        dst_sounds = (self.project_root / USER_SOUNDS_DIR).resolve()
        dst_pages.mkdir(parents=True, exist_ok=True)
        dst_message.mkdir(parents=True, exist_ok=True)
        dst_sounds.mkdir(parents=True, exist_ok=True)

        try:
            self.preview_files_release_requested.emit()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents
                | QtCore.QEventLoop.ExcludeSocketNotifiers
            )

            page_files, page_dirs = _safe_clear_dir_contents(dst_pages)
            message_files, message_dirs = _safe_clear_dir_contents(dst_message)

            copied_pages = 0
            for source in src_pages.iterdir():
                target = dst_pages / source.name
                if source.is_file():
                    shutil.copy2(source, target)
                    copied_pages += 1
                elif source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                    copied_pages += 1

            copied_message = 0
            for source in src_message.iterdir():
                target = dst_message / source.name
                if source.is_file():
                    shutil.copy2(source, target)
                    copied_message += 1
                elif source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                    copied_message += 1

            self._restore_saved_sound(src_sounds)

            metadata = _read_play_metadata(play_dir)
            settings = _load_settings(self.project_root)
            settings["recipient_name"] = str(
                metadata.get("recipient_name") or entry.recipient
            ).strip()
            settings["recipient_title"] = str(
                metadata.get("recipient_title") or entry.title
            ).strip()
            settings[PUBLISHED_PAGE_URL_KEY] = _normalize_page_url(
                str(
                    metadata.get(PUBLISHED_PAGE_URL_KEY)
                    or metadata.get("published_url")
                    or entry.published_url
                    or ""
                )
            )
            _write_settings(self.project_root, settings)
        except Exception as error:
            self._log(f"❌ Could not load the saved letter: {error}")
            return

        self.refresh_saved_page_url()
        self._current_play_dir = play_dir
        self._project_fingerprint = project_fingerprint(self.project_root)
        self.schedule_refresh()

        payload = {
            "recipient_name": settings["recipient_name"],
            "recipient_title": settings["recipient_title"],
            "published_page_url": settings[PUBLISHED_PAGE_URL_KEY],
            "play_dir": str(play_dir),
        }
        state = "Published" if settings[PUBLISHED_PAGE_URL_KEY] else "Local"
        self._log(
            "✅ Loaded saved letter\n"
            f"Recipient: {settings['recipient_name']}\n"
            f"Title: {settings['recipient_title']}\n"
            f"Status: {state}\n"
            f"From: {play_dir}\n\n"
            f"Replaced pages: {page_files} files, {page_dirs} folders\n"
            f"Replaced message: {message_files} files, {message_dirs} folders\n"
            f"Copied: {copied_pages} page items, {copied_message} message items"
        )
        QtCore.QTimer.singleShot(0, lambda: self.letter_loaded.emit(payload))
        QtCore.QTimer.singleShot(0, lambda: self.project_restored.emit(payload))

    def _restore_saved_sound(self, src_sounds: Optional[Path]) -> None:
        """Restore the saved sound mode without touching app-owned effects."""
        sound_payload: dict = {}
        source_music: Optional[Path] = None
        if src_sounds is not None:
            manifest = src_sounds / BUILD_SOUND_MANIFEST_NAME
            if manifest.is_file():
                try:
                    value = json.loads(manifest.read_text(encoding="utf-8"))
                    sound_payload = value if isinstance(value, dict) else {}
                except (OSError, json.JSONDecodeError):
                    sound_payload = {}
            candidate_music = src_sounds / MUSIC_FILE
            if candidate_music.is_file():
                source_music = candidate_music

        raw_tracks = sound_payload.get("tracks", [])
        if not isinstance(raw_tracks, list):
            raw_tracks = []
        if not raw_tracks and source_music is not None:
            raw_tracks = [{"filename": source_music.name, "display_title": "Music"}]

        imported_ids: list[str] = []
        for raw_track in raw_tracks:
            if not isinstance(raw_track, dict) or src_sounds is None:
                continue
            filename = Path(str(raw_track.get("filename", ""))).name
            source = src_sounds / filename
            if not filename or not source.is_file():
                continue
            record = import_runtime_track(
                self.project_root,
                source,
                display_title=str(raw_track.get("display_title", "")),
                original_name=str(raw_track.get("original_name", filename)),
                content_hash=str(raw_track.get("content_hash", "")),
                duration_seconds=float(raw_track.get("duration_seconds", 0.0) or 0.0),
            )
            imported_ids.append(record.track_id)

        mode = (
            "playlist"
            if str(sound_payload.get("mode", "single")) == "playlist"
            else "single"
        )
        sound_state = ProjectSoundState(
            mode=mode,
            single_track_id=(
                imported_ids[0]
                if mode == "single" and imported_ids
                else ""
            ),
            playlist=imported_ids if mode == "playlist" else [],
            playlist_expanded=True,
            selected_track_id=imported_ids[0] if imported_ids else "",
        )
        save_project_state(self.project_root, sound_state)
        sync_current_compatibility(
            self.project_root,
            sound_state,
            load_library(self.project_root),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Utility actions
    # ─────────────────────────────────────────────────────────────────────
    def go_to_gallery(self) -> None:
        """Open the directory containing every published Play letter."""
        ensure_output_dirs(self.project_root)
        gallery_dir = (self.project_root / OUTPUT_PLAY_DIR).resolve()
        try:
            gallery_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log(f"❌ Could not create the gallery folder: {exc}")
            return
        if QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(gallery_dir))):
            self._log(f"✅ Opened published-letter gallery.\n• Folder: {gallery_dir}")
            return
        self._log(f"❌ Could not open the published-letter gallery.\n• Folder: {gallery_dir}")

    def open_output_folder(self) -> None:
        """Compatibility alias for older Nexus connections."""
        self.go_to_gallery()

    def _call_host_hook(self, *names: str) -> bool:
        """Call the first compatible Nexus hook without hard-coupling Forge."""
        host = self.window()
        for name in names:
            callback = getattr(host, name, None)
            if callable(callback):
                try:
                    callback()
                    return True
                except TypeError:
                    continue
                except Exception:
                    return False
        return False

    def sync_all_from_disk(self, *, force: bool = False, notify_host: bool = True) -> bool:
        """Reconcile every Forge input with project files and mark stale builds."""
        before = self._project_fingerprint
        after = project_fingerprint(self.project_root)
        changed = force or before != after
        self.refresh_saved_page_url()
        self._project_fingerprint = after
        if changed:
            self.schedule_refresh()
        self.sync_requested.emit("changed" if changed else "checked")
        if notify_host:
            self._call_host_hook("sync_all_project_state", "refresh_all_project_state")
        return changed

    def schedule_refresh(self, *_args) -> None:
        self.preview_refresh_pending = True

    def attach_readiness_window(self, host: QtWidgets.QWidget) -> None:
        self._host_window = host

    def refresh_project_state(self) -> None:
        self.sync_all_from_disk(notify_host=False)

    def refresh_saved_letters(self) -> None:
        dialog = self._saved_letters_dialog
        if dialog is not None:
            dialog.refresh_from_disk(force=True)

    def current_play_index(self) -> Optional[Path]:
        if self._current_play_dir is not None:
            index = self._current_play_dir / "index.html"
            if index.is_file():
                return index
        latest = self._discover_latest_play_dir()
        if latest is not None:
            self._current_play_dir = latest
            index = latest / "index.html"
            if index.is_file():
                return index
        return None

    def _discover_latest_play_dir(self) -> Optional[Path]:
        root = (self.project_root / OUTPUT_PLAY_DIR).resolve()
        if not root.is_dir():
            return None
        indexes = [path for path in root.rglob("index.html") if path.is_file()]
        if not indexes:
            return None
        try:
            latest = max(indexes, key=lambda path: path.stat().st_mtime_ns)
        except OSError:
            latest = indexes[0]
        return latest.parent

    def ensure_preview_current(self) -> Optional[Path]:
        if self.preview_refresh_pending or self.current_play_index() is None:
            return self._run_pipeline(mode="refresh", announce=False)
        index = self.current_play_index()
        if index is not None:
            self.preview_visibility_changed.emit(True)
            self.preview_requested.emit(str(index), self.preview_mode_value)
        return index.parent if index is not None else None

    def shutdown_operations(self) -> bool:
        # Forge uses synchronous subprocess calls, so no QThread can outlive it.
        return not self._operation_active

    def restart_preview(self, reason: str) -> None:
        self.preview_restart_requested.emit(reason)
        self._call_host_hook(
            "restart_forge_preview",
            "restart_preview",
            "reset_forge_preview",
        )

    def activate_for_tab_change(self) -> None:
        if self._tab_active:
            return
        self._tab_active = True
        self.sync_all_from_disk(notify_host=False)
        self.restart_preview("enter")

    def deactivate_for_tab_change(self) -> None:
        if not self._tab_active:
            return
        self.sync_all_from_disk(notify_host=False)
        self.restart_preview("leave")
        self._tab_active = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.activate_for_tab_change()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self.deactivate_for_tab_change()
        super().hideEvent(event)

    def refresh_saved_page_url(self) -> str:
        settings = _load_settings(self.project_root)
        self.saved_page_url = _normalize_page_url(str(settings.get(PUBLISHED_PAGE_URL_KEY, "")).strip())
        self._sync_go_to_page_button()
        return self.saved_page_url

    def set_saved_page_url(self, url: str) -> None:
        self.saved_page_url = _normalize_page_url(url)
        self._sync_go_to_page_button()

    def _sync_go_to_page_button(self) -> None:
        has_url = bool(self.saved_page_url)
        has_local = self.current_play_index() is not None if hasattr(self, "go_to_page_btn") else False
        if hasattr(self, "go_to_page_btn"):
            self.go_to_page_btn.setEnabled(has_url or has_local)
            if has_url:
                self.go_to_page_btn.setToolTip(self.saved_page_url)
            elif has_local:
                self.go_to_page_btn.setToolTip("Open the current local letter")
            else:
                self.go_to_page_btn.setToolTip("Preview or publish a letter first")

    def go_to_page(self) -> None:
        url = self.refresh_saved_page_url()
        if url:
            if QtGui.QDesktopServices.openUrl(QUrl(url)):
                self._log(f"✅ Opened published letter.\n• URL: {url}")
                return
            self._log(f"❌ Could not open the saved page URL.\n• URL: {url}")
            return

        index = self.current_play_index()
        if index is None:
            self._log("❌ No local or published letter is available. Select Preview Letter first.")
            return
        if QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(index.resolve()))):
            self._log(f"✅ Opened local letter.\n• File: {index}")
            return
        self._log(f"❌ Could not open the local letter.\n• File: {index}")

    # ─────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────
    def generate(self) -> None:
        self._run_pipeline(mode="preview")

    def seal_the_letter(self) -> None:
        self._run_pipeline(mode="publish")

    def _set_operation_buttons_enabled(self, enabled: bool) -> None:
        for button in (self.load_btn, self.generate_btn, self.seal_btn, self.go_to_page_btn, self.go_to_gallery_btn):
            button.setEnabled(enabled if button is not self.go_to_page_btn else (enabled and (bool(self.saved_page_url) or self.current_play_index() is not None)))

    def _build_local_bundle(self, *, open_in_browser: bool) -> Path:
        ensure_output_dirs(self.project_root)
        gen_fn = _get_generate_fn()
        if gen_fn is None:
            raise RuntimeError("generate.py is missing generate_play_bundle.")

        missing = validate_required_images(self.project_root)
        if missing:
            raise FileNotFoundError(
                f"Missing {', '.join(missing)} in {self.project_root / 'gallery/user/pages'}"
            )

        msg_path = (self.project_root / MESSAGE_HTML_FILE).resolve()
        message_html = self._read_message_html(msg_path)
        if not message_html.strip():
            raise ValueError(f"Message is empty or missing: {msg_path}")

        self.preview_files_release_requested.emit()
        play_dir = Path(
            gen_fn(
                str(self.project_root),
                message_html=message_html,
                open_in_browser=open_in_browser,
            )
        ).resolve()
        index = play_dir / "index.html"
        if not index.is_file():
            raise FileNotFoundError(f"Generated letter is missing index.html: {index}")

        self._current_play_dir = play_dir
        self.preview_refresh_pending = False
        self._project_fingerprint = project_fingerprint(self.project_root)
        self.preview_visibility_changed.emit(True)
        self.preview_requested.emit(str(index), self.preview_mode_value)
        self._sync_go_to_page_button()
        return play_dir

    def _run_pipeline(self, *, mode: str, announce: bool = True) -> Optional[Path]:
        if self._operation_active:
            if announce:
                self._log("A Forge operation is already running. Wait for it to finish before starting another.")
            return None

        self._operation_active = True
        self._set_operation_buttons_enabled(False)
        try:
            open_in_browser = mode == "preview"
            play_dir = self._build_local_bundle(open_in_browser=open_in_browser)

            if mode in {"preview", "refresh"}:
                if announce:
                    self._log(
                        "✅ Local letter generated successfully."
                        + (" It was opened in the default browser." if open_in_browser else "")
                        + f"\n• Letter: {play_dir / 'index.html'}"
                        + self._font_export_note()
                    )
                return play_dir

            if mode == "publish":
                self._publish_with_github_cli(play_dir)
                return play_dir

            raise RuntimeError(f"Unknown Forge operation: {mode}")
        except Exception as error:
            if announce:
                tb = traceback.format_exc(limit=20)
                self._log(f"❌ Forge error: {type(error).__name__}: {error}\n\n{tb}")
            return None
        finally:
            self._operation_active = False
            self._set_operation_buttons_enabled(True)

    def _run_command(self, args: list[str], *, cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def _publish_with_github_cli(self, play_dir: Path) -> None:
        gh = shutil.which("gh")
        git = shutil.which("git")
        if not gh:
            self._log(
                "✅ The local letter was generated successfully.\n"
                f"• Letter: {play_dir / 'index.html'}\n\n"
                "Online publishing was not configured because GitHub CLI is required for first-time publishing. "
                "Preview Letter and Open Letter remain available."
            )
            return
        if not git:
            self._log(
                "✅ The local letter was generated successfully.\n"
                "Online publishing could not continue because Git is not available. "
                "Preview Letter and Open Letter remain available."
            )
            return

        auth = self._run_command([gh, "auth", "status"], cwd=self.project_root, timeout=30)
        if auth.returncode != 0:
            detail = (auth.stderr or auth.stdout).strip()
            self._log(
                "✅ The local letter was generated successfully.\n"
                "Online publishing could not continue because GitHub CLI is not authenticated.\n"
                f"{detail or 'Run gh auth login, then select Publish Letter again.'}"
            )
            return

        repo_root_result = self._run_command([git, "rev-parse", "--show-toplevel"], cwd=self.project_root, timeout=20)
        if repo_root_result.returncode != 0:
            self._log(
                "✅ The local letter was generated successfully.\n"
                "Online publishing could not continue because this project is not inside a configured Git repository. "
                "No background worker was left running."
            )
            return

        repo_root = Path(repo_root_result.stdout.strip()).resolve()
        try:
            relative_play = play_dir.relative_to(repo_root)
        except ValueError:
            self._log(
                "✅ The local letter was generated successfully.\n"
                "Online publishing could not continue because the generated gallery is outside the configured Git repository."
            )
            return

        repo_view = self._run_command([gh, "repo", "view", "--json", "nameWithOwner"], cwd=repo_root, timeout=30)
        if repo_view.returncode != 0:
            self._log(
                "✅ The local letter was generated successfully.\n"
                "Online publishing could not identify the GitHub repository. "
                "Confirm the repository has a GitHub remote, then retry."
            )
            return
        try:
            name_with_owner = str(json.loads(repo_view.stdout).get("nameWithOwner", "")).strip()
        except (json.JSONDecodeError, AttributeError):
            name_with_owner = ""
        if "/" not in name_with_owner:
            self._log("✅ The local letter was generated successfully.\nOnline publishing could not resolve the GitHub repository owner and name.")
            return

        add = self._run_command([git, "add", "--", relative_play.as_posix()], cwd=repo_root)
        if add.returncode != 0:
            self._log(f"✅ The local letter was generated successfully.\nOnline publishing failed while staging files:\n{(add.stderr or add.stdout).strip()}")
            return

        diff = self._run_command(
            [git, "diff", "--cached", "--quiet", "--", relative_play.as_posix()],
            cwd=repo_root,
            timeout=30,
        )
        if diff.returncode == 1:
            commit = self._run_command(
                [git, "commit", "-m", "Publish Letter Smith gallery", "--", relative_play.as_posix()],
                cwd=repo_root,
            )
            if commit.returncode != 0:
                self._log(f"✅ The local letter was generated successfully.\nOnline publishing failed while committing:\n{(commit.stderr or commit.stdout).strip()}")
                return
        elif diff.returncode not in {0, 1}:
            self._log(f"✅ The local letter was generated successfully.\nOnline publishing could not inspect staged changes:\n{(diff.stderr or diff.stdout).strip()}")
            return

        branch_result = self._run_command([git, "branch", "--show-current"], cwd=repo_root, timeout=20)
        branch = branch_result.stdout.strip() or "main"
        push = self._run_command([git, "push", "-u", "origin", branch], cwd=repo_root, timeout=120)
        if push.returncode != 0:
            self._log(f"✅ The local letter was generated successfully.\nOnline publishing failed while pushing to GitHub:\n{(push.stderr or push.stdout).strip()}")
            return

        pages = self._run_command([gh, "api", f"repos/{name_with_owner}/pages"], cwd=repo_root, timeout=30)
        if pages.returncode != 0:
            pages = self._run_command(
                [gh, "api", "--method", "POST", f"repos/{name_with_owner}/pages", "-f", f"source[branch]={branch}", "-f", "source[path]=/"],
                cwd=repo_root,
                timeout=45,
            )
        if pages.returncode != 0:
            self._log(
                "✅ The local letter was generated and pushed to GitHub.\n"
                "GitHub Pages configuration did not complete, so no published URL was saved.\n"
                f"{(pages.stderr or pages.stdout).strip()}"
            )
            return

        owner, repo = name_with_owner.split("/", 1)
        base = f"https://{owner}.github.io/" if repo.casefold() == f"{owner}.github.io".casefold() else f"https://{owner}.github.io/{repo}/"
        relative_url = relative_play.as_posix().strip("/")
        published_url = base + (relative_url + "/" if relative_url else "")
        settings = _load_settings(self.project_root)
        settings[PUBLISHED_PAGE_URL_KEY] = published_url
        _write_settings(self.project_root, settings)
        self.saved_page_url = published_url
        self.published_url_changed.emit(published_url)
        self._sync_go_to_page_button()
        self._log(
            "✅ Local letter generated and pushed to GitHub.\n"
            f"• Local: {play_dir / 'index.html'}\n"
            f"• Published URL: {published_url}\n\n"
            "GitHub Pages may take a short time to finish deploying the update."
        )

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────
    def _read_message_html(self, msg_path: Path) -> str:
        try:
            if not msg_path.exists():
                return ""
            return read_text_normalized(msg_path)
        except Exception:
            return ""

    def _font_export_note(self) -> str:
        reporter = getattr(generate, "get_last_font_export_report", None)
        if not callable(reporter):
            return ""

        report = reporter()
        fallback = tuple(report.get("fallback", ())) if isinstance(report, dict) else ()
        if not fallback:
            return ""
        return "\n• Font fallback used: " + ", ".join(fallback)

    def _log(self, text: str) -> None:
        self.status.setPlainText(text)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.shutdown_operations():
            event.accept()
        else:
            event.ignore()

    # ─────────────────────────────────────────────────────────────────────
    # Button styles
    # ─────────────────────────────────────────────────────────────────────
    def _styled_button(self, text: str, bg_color: str, border_color: str, text_color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 14))
        btn.setMinimumHeight(52)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"background:{bg_color}; border:2px solid {border_color};"
            f"border-radius:10px; padding:14px 20px;"
            f"color:{text_color}; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{border_color}; }}"
        )
        btn.setGraphicsEffect(self._shadow_effect(16))
        return btn

    def _page_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 13))
        btn.setMinimumHeight(52)
        btn.setMinimumWidth(164)
        btn.setMaximumWidth(186)
        btn.setStyleSheet(
            "QPushButton {"
            "background:#24292f; color:#f0f6fc;"
            "border:2px solid #57606a; border-radius:10px; padding:14px 16px;"
            "font-weight:700;"
            "}"
            "QPushButton:hover { background:#30363d; border-color:#8b949e; }"
            "QPushButton:disabled { background:#161b22; color:#6e7681; border-color:#30363d; }"
        )
        btn.setGraphicsEffect(self._shadow_effect(16))
        return btn

    def _utility_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 12))
        btn.setMinimumHeight(44)
        btn.setStyleSheet(
            "QPushButton { background:#0f0f12; color:#e6e6e6;"
            "border:1px solid #00d0ff; border-radius:8px; padding:10px 14px; }"
            "QPushButton:hover { background:#113945; }"
        )
        btn.setGraphicsEffect(self._shadow_effect(12))
        return btn

    def _tiny_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        btn.setFixedHeight(30)
        btn.setStyleSheet(
            "QPushButton { background:#0f0f12; color:#e6e6e6;"
            "border:1px solid #00d0ff; border-radius:8px; padding:6px 12px; }"
            "QPushButton:hover { background:#113945; }"
        )
        return btn

    def _shadow_effect(self, blur_radius: int) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur_radius)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        return shadow
