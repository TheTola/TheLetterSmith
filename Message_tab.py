# ===============================
# File: Message_tab.py
# Purpose: Message authoring + render to message.png
# ===============================

from __future__ import annotations

import html as _html
import os
import re
import json
import math
import threading
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSizeF, Signal
from PySide6.QtGui import (
    QTextDocument,
    QTextCursor,
    QTextCharFormat,
    QImage,
    QPainter,
    QPixmap,
    QFont,
)

from Editor import Editor
from image_button import ArtworkButton
from project_sync import file_fingerprint
from message_history import (
    delete_revision,
    list_revisions,
    restore_revision,
)
from message_format import normalize_ultralinks_in_document
from message_html import is_lettersmith_message_html, sanitize_message_html
from message_import import (
    MESSAGE_IMPORT_TIMEOUT_MS,
    MessageImportError,
    create_result_path,
    import_message_sync,
    read_worker_result,
    remove_result_path,
    worker_command,
)
from letter_page import (
    DEFAULT_MESSAGE_OVERLAY_OPACITY,
    DEFAULT_MESSAGE_OVERLAY_PRESET,
    LETTER_PAGE_PRESET_LABELS,
    LETTER_PAGE_PRESETS,
    MESSAGE_OVERLAY_OPACITY_KEY,
    MESSAGE_OVERLAY_PRESET_KEY,
    adaptive_text_rgb,
    composite_rgb,
    effective_letter_page_opacity,
    normalized_letter_page_settings,
    relative_luminance,
)
from project_paths import ProjectPathResolver, application_paths
from project_save import ProjectNotReadyError, ProjectSaveService
from project_state import ProjectStateController
from protected_projects import reserved_identity_field_reason
from publishing.expiration import clear_publication_state
from ui_dialogs import (
    LetterSmithConfirmationDialog,
    LetterSmithDialog,
    show_lettersmith_message,
)
from ui_help import set_control_help
from ui_sounds import UiSound, play_ui_sound
from ui_theme import (
    PRIMARY_PAGE_LAYOUT,
    ButtonTier,
    apply_button_tier,
    apply_tab_heading_style,
)
from settings_store import (
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    SettingsStore,
    normalize_published_page_url,
)
from config import (
    SETTINGS_FILE,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
)


IDENTITY_LOCK_KEYS = {
    "title": "recipient_title_locked",
    "recipient": "recipient_name_locked",
    "published_url": "published_page_url_locked",
}


def _downloads_directory() -> str:
    location = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.DownloadLocation
    )
    if location:
        return location

    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


class IdentityLineEdit(QtWidgets.QLineEdit):
    """Line edit that requires a double-click before editing a committed value."""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.isReadOnly():
            self.setReadOnly(False)
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

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


def _plain_text_from_html(raw_html: str) -> str:
    doc = QTextDocument()
    doc.setHtml(raw_html or "")
    return doc.toPlainText()


def _strip_html_for_word_count(html: str) -> str:
    return _plain_text_from_html(html)


def _word_count(text_like: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", text_like or "", flags=re.UNICODE))


def _normalize_imported_message_html(raw_html: str) -> str:
    """Normalize ordinary imported content to editable Message defaults."""
    plain = _plain_text_from_html(raw_html).replace("\r\n", "\n").replace("\r", "\n")
    lines = plain.split("\n")
    blocks: list[str] = []
    for line in lines:
        if line.strip():
            blocks.append(f"<p>{_html.escape(line)}</p>")
        else:
            blocks.append("<p><br></p>")
    if not blocks:
        blocks = ["<p><br></p>"]
    return (
        '<div class="lettersmith-defaults" '
        "style=\"font-family:'Papyrus'; text-align:center; line-height:2;\">"
        + "".join(blocks)
        + "</div>"
    )


def _reading_time_label(word_count: int) -> str:
    if word_count <= 0:
        return "0 min read"
    if word_count < 200:
        return "<1 min read"
    return f"~{math.ceil(word_count / 200)} min read"


def _normalize_published_page_url(value: str) -> str:
    return normalize_published_page_url(
        value
    )


MESSAGE_OVERLAY_PRESETS: dict[str, tuple[tuple[int, int, int], str]] = {
    key: (definition.base_rgb, definition.default_ink)
    for key, definition in LETTER_PAGE_PRESETS.items()
}
MESSAGE_OVERLAY_PRESET_LABELS = LETTER_PAGE_PRESET_LABELS


def _normalized_message_overlay_settings(
    data: dict[str, object],
) -> tuple[str, int, tuple[int, int, int], str]:
    """Return normalized render settings from an existing settings snapshot."""
    return normalized_letter_page_settings(data)


def _message_overlay_settings(settings_path: str | os.PathLike) -> tuple[str, int, tuple[int, int, int], str]:
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8")) if Path(settings_path).exists() else {}
    except Exception:
        data = {}
    return _normalized_message_overlay_settings(data)


def _effective_message_overlay_opacity(
    preset: str,
    opacity: int,
) -> int:
    return effective_letter_page_opacity(preset, opacity)


class _LocalBackgroundSampler:
    """Cache robust local samples for one immutable rendered background."""

    def __init__(self, image: QImage) -> None:
        self.image = image
        self._cache: dict[tuple[int, int, int, int], tuple[int, int, int]] = {}

    def sample(self, rect: QtCore.QRectF) -> tuple[int, int, int]:
        bounds = rect.adjusted(-2.0, -2.0, 2.0, 2.0).toAlignedRect()
        bounds = bounds.intersected(self.image.rect())
        if bounds.isEmpty():
            return (127, 127, 127)
        key = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        samples: list[tuple[float, tuple[int, int, int]]] = []
        for row in range(5):
            y = bounds.top() + round((bounds.height() - 1) * (row / 4.0))
            for column in range(5):
                x = bounds.left() + round(
                    (bounds.width() - 1) * (column / 4.0)
                )
                color = self.image.pixelColor(x, y)
                rgb = (color.red(), color.green(), color.blue())
                samples.append((relative_luminance(rgb), rgb))
        samples.sort(key=lambda item: item[0])
        trimmed = samples[5:-5] or samples
        representative = tuple(
            round(sum(rgb[channel] for _luminance, rgb in trimmed) / len(trimmed))
            for channel in range(3)
        )
        self._cache[key] = representative  # type: ignore[assignment]
        return representative  # type: ignore[return-value]


def _cursor_x(line: QtGui.QTextLine, position: int) -> float:
    value = line.cursorToX(position)
    return float(value[0] if isinstance(value, tuple) else value)


def _apply_adaptive_micro_contrast(
    document: QTextDocument,
    background: QImage,
    *,
    preset: str,
    origin: QtCore.QPointF,
    fallback_ink: str,
) -> int:
    """Subtly adjust each visible glyph without changing any other rich-text format."""
    definition = LETTER_PAGE_PRESETS[preset]
    sampler = _LocalBackgroundSampler(background)
    changes: list[tuple[int, tuple[int, int, int], int]] = []
    document.documentLayout().documentSize()

    block = document.firstBlock()
    while block.isValid():
        layout = block.layout()
        block_rect = document.documentLayout().blockBoundingRect(block)
        text = block.text()
        for offset, character in enumerate(text):
            if character.isspace():
                continue
            line = layout.lineForTextPosition(offset)
            if not line.isValid():
                continue
            left = _cursor_x(line, offset)
            right = _cursor_x(line, offset + 1)
            glyph_rect = QtCore.QRectF(
                origin.x() + block_rect.left() + min(left, right),
                origin.y() + block_rect.top() + line.y(),
                max(1.0, abs(right - left)),
                max(1.0, line.height()),
            )

            probe = QTextCursor(document)
            position = block.position() + offset
            probe.setPosition(position)
            probe.setPosition(position + 1, QTextCursor.KeepAnchor)
            char_format = probe.charFormat()
            brush = char_format.foreground()
            color = (
                brush.color()
                if brush.style() != Qt.BrushStyle.NoBrush
                else QtGui.QColor(fallback_ink)
            )
            if not color.isValid() or color.alpha() == 0:
                continue
            intended = (color.red(), color.green(), color.blue())
            local_background = sampler.sample(glyph_rect)
            background_brush = char_format.background()
            if background_brush.style() != Qt.BrushStyle.NoBrush:
                format_background = background_brush.color()
                if format_background.isValid() and format_background.alpha() > 0:
                    local_background = composite_rgb(
                        local_background,
                        (
                            format_background.red(),
                            format_background.green(),
                            format_background.blue(),
                        ),
                        format_background.alphaF(),
                    )
            adjusted = adaptive_text_rgb(
                intended,
                local_background,
                max_lightness_shift=definition.max_lightness_shift,
            )
            if adjusted != intended:
                changes.append((position, adjusted, color.alpha()))
        block = block.next()

    if not changes:
        return 0
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    try:
        for position, adjusted, alpha in changes:
            cursor.setPosition(position)
            cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
            char_format = QTextCharFormat()
            char_format.setForeground(QtGui.QColor(*adjusted, alpha))
            cursor.mergeCharFormat(char_format)
    finally:
        cursor.endEditBlock()
    return len(changes)


def _render_message_png(
    html: str,
    wall_path: Path,
    staged_path: Path,
    preset: str,
    overlay_opacity: int,
) -> bool:
    """Render one full-resolution Message image outside the GUI thread."""
    full_width, full_height = 2048, 3072
    margin_lr = 100
    margin_top = 100
    margin_bottom = 100
    text_width = full_width - 2 * margin_lr
    text_height = full_height - margin_top - margin_bottom

    canvas = QImage(full_width, full_height, QImage.Format_ARGB32)
    canvas.fill(QtGui.QColor(14, 14, 18, 255))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    definition = LETTER_PAGE_PRESETS[preset]
    ink_color = definition.default_ink
    if wall_path.is_file():
        wall_image = QImage(str(wall_path))
        if not wall_image.isNull():
            wall_image = wall_image.scaled(
                full_width,
                full_height,
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            painter.drawImage(0, 0, wall_image)

    surface_opacity = _effective_message_overlay_opacity(
        preset,
        overlay_opacity,
    )
    if surface_opacity > 0:
        alpha = int(round(255 * (surface_opacity / 100.0)))
        gradient = QtGui.QRadialGradient(
            QtCore.QPointF(full_width * 0.5, full_height * 0.43),
            math.hypot(full_width * 0.5, full_height * 0.57),
        )
        gradient.setColorAt(0.0, QtGui.QColor(*definition.center_rgb, alpha))
        gradient.setColorAt(1.0, QtGui.QColor(*definition.edge_rgb, alpha))
        painter.fillRect(canvas.rect(), gradient)
    painter.end()

    document = QTextDocument()
    document.setDefaultFont(QFont("Papyrus", 12))
    document.setDefaultStyleSheet(
        f"body {{ color: {ink_color}; background: transparent; font-family: 'Papyrus'; "
        "text-align: center; line-height: 2; }}"
        "p { margin: 0 0 12px 0; }"
        "br { line-height: 2; }"
    )
    document.setHtml(html)
    normalize_ultralinks_in_document(document)
    document.setTextWidth(text_width)
    document.setPageSize(QSizeF(text_width, text_height))
    _apply_adaptive_micro_contrast(
        document,
        canvas,
        preset=preset,
        origin=QtCore.QPointF(margin_lr, margin_top),
        fallback_ink=ink_color,
    )

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.save()
    painter.translate(margin_lr, margin_top)
    painter.setClipRect(0, 0, text_width, text_height)
    document.drawContents(
        painter,
        QtCore.QRectF(0, 0, text_width, text_height),
    )
    painter.restore()
    painter.end()

    return _atomic_save_image(canvas, str(staged_path))


class _MessageRenderGate:
    """Serialize revision changes with the final atomic image replacement."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._accepting = True

    def next_revision(self) -> int | None:
        with self._lock:
            if not self._accepting:
                return None
            self._revision += 1
            return self._revision

    def current_revision(self) -> int:
        with self._lock:
            return self._revision

    def is_current(self, revision: int) -> bool:
        with self._lock:
            return self._accepting and revision == self._revision

    def invalidate(self, *, accepting: bool) -> None:
        with self._lock:
            self._revision += 1
            self._accepting = accepting

    def commit_if_current(
        self,
        revision: int,
        staged_path: Path,
        output_path: Path,
    ) -> tuple[bool, str]:
        last_error = ""
        for attempt in range(8):
            with self._lock:
                if not self._accepting or revision != self._revision:
                    return False, ""
                try:
                    os.replace(staged_path, output_path)
                    return True, ""
                except OSError as error:
                    last_error = str(error)
            if attempt < 7:
                QtCore.QThread.msleep(80)
        return False, last_error or "Could not replace message.png"


class _MessageRenderSignals(QtCore.QObject):
    finished = Signal(int, bool, str)


class _MessageRenderTask(QtCore.QRunnable):
    def __init__(
        self,
        *,
        revision: int,
        gate: _MessageRenderGate,
        html: str,
        wall_path: Path,
        output_path: Path,
        staged_path: Path,
        preset: str,
        overlay_opacity: int,
    ) -> None:
        super().__init__()
        self.revision = revision
        self.gate = gate
        self.html = html
        self.wall_path = wall_path
        self.output_path = output_path
        self.staged_path = staged_path
        self.preset = preset
        self.overlay_opacity = overlay_opacity
        self.signals = _MessageRenderSignals()

    @QtCore.Slot()
    def run(self) -> None:
        committed = False
        error_text = ""
        try:
            if not self.gate.is_current(self.revision):
                return
            if not _render_message_png(
                self.html,
                self.wall_path,
                self.staged_path,
                self.preset,
                self.overlay_opacity,
            ):
                raise RuntimeError("Failed to encode message.png")
            committed, error_text = self.gate.commit_if_current(
                self.revision,
                self.staged_path,
                self.output_path,
            )
        except Exception as error:
            error_text = str(error)
        finally:
            try:
                self.staged_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.signals.finished.emit(
                self.revision,
                committed,
                error_text,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Drag-drop button
# ─────────────────────────────────────────────────────────────────────────────

class DropMessageButton(ArtworkButton):
    file_dropped = Signal(str)

    def __init__(self, project_root: str | Path, label: str = "Import") -> None:
        super().__init__(label, project_root, "FButton.png")
        apply_button_tier(self, ButtonTier.STANDARD)
        self.set_artwork_fill(True)
        self.set_artwork_stretch(True)
        self.setAcceptDrops(True)
        self.setAccessibleName("Import")
        set_control_help(
            self,
            "Import a .txt, .docx, .pdf, .odt, or saved Letter Smith .html message. "
            "You may also drag a supported file onto this button.",
        )
        if self.uses_artwork_presentation and not self.has_artwork:
            self.setStyleSheet(self._default_style())

    def _default_style(self) -> str:
        return (
            "QPushButton {background-color:#1c1e26; border:1px solid #00d0ff;"
            " color:#e6e6e6; text-align:center;}"
            "QPushButton:hover {background-color:#00d0ff; color:#0e0f12;}"
        )

    def _glow_style(self) -> str:
        return (
            "QPushButton {background-color:#1c1e26; border:2px solid #00ffff;"
            " color:#ffffff;}"
        )

    def _is_supported_url(self, url: QtCore.QUrl) -> bool:
        if not url.isLocalFile():
            return False
        suffix = url.toLocalFile().lower()
        return suffix.endswith((".txt", ".docx", ".pdf", ".odt", ".html", ".htm"))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(self._is_supported_url(u) for u in event.mimeData().urls()):
            event.acceptProposedAction()
            if self.uses_artwork_presentation and not self.has_artwork:
                self.setStyleSheet(self._glow_style())
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:  # type: ignore[override]
        if self.uses_artwork_presentation and not self.has_artwork:
            self.setStyleSheet(self._default_style())
        self.update()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        if self.uses_artwork_presentation and not self.has_artwork:
            self.setStyleSheet(self._default_style())
        self.update()
        for url in event.mimeData().urls():
            if self._is_supported_url(url):
                self.file_dropped.emit(url.toLocalFile())
                break


# ─────────────────────────────────────────────────────────────────────────────
# Revision history
# ─────────────────────────────────────────────────────────────────────────────

class RevisionHistoryDialog(LetterSmithDialog):
    def __init__(self, message_tab: "MessageTab") -> None:
        super().__init__(
            message_tab,
            title="Revision History",
            modal=True,
            click_outside_dismiss=False,
            width=760,
        )
        self.message_tab = message_tab
        self.resize(760, 480)

        root = self.content_layout
        root.setSpacing(12)

        splitter = QtWidgets.QSplitter(Qt.Horizontal, self)
        self.revision_list = QtWidgets.QListWidget(splitter)
        self.preview = QtWidgets.QTextBrowser(splitter)
        self.preview.setOpenExternalLinks(False)
        splitter.setSizes([290, 470])
        root.addWidget(splitter, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.restore_btn = QtWidgets.QPushButton("Restore", self)
        self.delete_btn = QtWidgets.QPushButton("Delete", self)
        self.delete_btn.setObjectName("RevisionDelete")
        close_btn = QtWidgets.QPushButton("Close", self)
        set_control_help(
            self.revision_list,
            "Select a saved revision to preview it; double-click to restore it.",
        )
        set_control_help(
            self.restore_btn,
            "Replace the current message with the selected saved revision.",
        )
        set_control_help(
            self.delete_btn,
            "Permanently remove the selected saved revision from history.",
        )
        set_control_help(
            close_btn,
            "Close revision history without changing the current message.",
        )
        buttons.addWidget(self.restore_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.revision_list.currentItemChanged.connect(self._show_selected)
        self.revision_list.itemDoubleClicked.connect(lambda _item: self._restore_selected())
        self.restore_btn.clicked.connect(self._restore_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        close_btn.clicked.connect(self.accept)

        colors = self.colors
        self.setStyleSheet(
            self.styleSheet()
            + "QListWidget,QTextBrowser{"
            f"background:{colors['background']};color:{colors['text']};"
            f"border:1px solid {colors['border']};border-radius:7px;}}"
            "QListWidget::item{padding:8px;}"
            "QListWidget::item:selected{"
            f"background:{colors['hover']};color:{colors['text']};}}"
            "QPushButton{"
            f"background:{colors['control_background']};color:{colors['text']};"
            f"border:1px solid {colors['accent']};border-radius:7px;"
            "min-height:38px;padding:0 16px;font:700 10pt 'Segoe UI';}"
            "QPushButton:hover{"
            f"background:{colors['hover']};color:{colors['text']};}}"
            "QPushButton#RevisionDelete{"
            f"color:{colors['error']};border-color:{colors['error']};}}"
            "QPushButton#RevisionDelete:hover{"
            f"background:{colors['error']};color:{colors['background']};}}"
        )
        self.refresh()

    def refresh(self) -> None:
        self.revision_list.clear()
        for revision in list_revisions(self.message_tab._html_path()):
            item = QtWidgets.QListWidgetItem(revision.display_name)
            item.setData(Qt.UserRole, str(revision.path))
            item.setToolTip(str(revision.path))
            self.revision_list.addItem(item)

        has_items = self.revision_list.count() > 0
        self.restore_btn.setEnabled(has_items)
        self.delete_btn.setEnabled(has_items)
        if has_items:
            self.revision_list.setCurrentRow(0)
        else:
            self.preview.setPlainText("No revisions have been created yet.")

    def _selected_path(self) -> Optional[Path]:
        item = self.revision_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return Path(str(value)) if value else None

    def _show_selected(self, current: Optional[QtWidgets.QListWidgetItem], _previous=None) -> None:
        if current is None:
            self.preview.clear()
            return
        path = self._selected_path()
        if path is None or not path.is_file():
            self.preview.setPlainText("This revision is no longer available.")
            return
        try:
            raw = path.read_text(encoding="utf-8")
            self.preview.setPlainText(_plain_text_from_html(raw))
        except Exception as error:
            self.preview.setPlainText(f"Could not read this revision: {error}")

    def _restore_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        if self.message_tab.restore_message_revision(path):
            self.accept()

    def _delete_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        confirmation = LetterSmithConfirmationDialog(
            self,
            title="Delete Revision",
            question="Permanently delete the selected message revision?",
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
        )
        if confirmation.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            delete_revision(path)
        except Exception as error:
            show_lettersmith_message(
                self,
                "Revision History",
                "The selected revision could not be deleted.",
                detail=str(error),
            )
            return
        self.refresh()
        play_ui_sound(UiSound.REMOVED)


# ─────────────────────────────────────────────────────────────────────────────
# Main Message Tab
# ─────────────────────────────────────────────────────────────────────────────

class MessageTab(QtWidgets.QWidget):
    # Public contract used by Nexus/Over_Nexus — DO NOT REMOVE
    text_selected = Signal(str)
    preview_image = Signal(QPixmap)
    wall_preview = Signal(QPixmap)
    published_page_url_changed = Signal(str)
    project_changed = Signal()

    def __init__(
        self,
        project_root: str,
        *,
        project_state: ProjectStateController | None = None,
        project_paths: ProjectPathResolver | None = None,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.settings_path = os.path.join(project_root, SETTINGS_FILE)
        self.settings_store = SettingsStore(project_root)
        self.project_state = project_state
        if self.project_state is None:
            self.project_state = ProjectStateController(project_root)
            self.project_state.initialize()
        self.project_paths = project_paths or ProjectPathResolver(project_root)
        self.project_save_service = ProjectSaveService(
            project_root,
            self.project_state,
            resolver=self.project_paths,
        )

        # Compatibility cache; disk remains authoritative.
        self.current_html: str = ""
        self._content_has_intentional_formatting = False

        self._load_settings()
        (
            self.overlay_preset,
            self.overlay_opacity,
            _overlay_rgb,
            _overlay_ink,
        ) = _normalized_message_overlay_settings(self.settings)
        self.overlay_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._render_gate = _MessageRenderGate()
        self._render_thread_pool = QtCore.QThreadPool(self)
        self._render_thread_pool.setMaxThreadCount(1)
        self._render_thread_pool.setExpiryTimeout(1000)
        self._render_shutdown = False
        self._message_import_process: QtCore.QProcess | None = None
        self._message_import_result_path: Path | None = None
        self._message_import_source: Path | None = None
        self._message_import_error = ""
        self._message_import_timer = QtCore.QTimer(self)
        self._message_import_timer.setSingleShot(True)
        self._message_import_timer.timeout.connect(
            self._on_message_import_timeout
        )
        self._overlay_update_pending = False
        self._overlay_render_timer = QtCore.QTimer(self)
        self._overlay_render_timer.setSingleShot(True)
        self._overlay_render_timer.setInterval(180)
        self._overlay_render_timer.timeout.connect(self._flush_overlay_update)
        self._sync_state = self._capture_sync_state()
        self._tab_active = False

        layout = QtWidgets.QVBoxLayout(self)
        PRIMARY_PAGE_LAYOUT.apply(layout)

        self.message_content_shell = QtWidgets.QFrame(self)
        self.message_content_shell.setObjectName("messageContentShell")
        self.message_content_shell.setMaximumWidth(820)
        self.message_content_shell.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Maximum,
        )
        self.message_content_shell.setStyleSheet(
            "QFrame#messageContentShell{background:transparent;border:none;}"
        )
        shell = QtWidgets.QVBoxLayout(self.message_content_shell)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(5)

        self.heading = QtWidgets.QLabel("Write your letter’s message")
        apply_tab_heading_style(self.heading)
        self.heading.setAlignment(Qt.AlignCenter)
        shell.addWidget(self.heading)

        self.title_recipient_container = QtWidgets.QWidget(self.message_content_shell)
        title_recipient_layout = QtWidgets.QFormLayout(self.title_recipient_container)
        title_recipient_layout.setContentsMargins(0, 0, 0, 0)
        title_recipient_layout.setHorizontalSpacing(10)
        title_recipient_layout.setVerticalSpacing(12)
        title_recipient_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.title_input = IdentityLineEdit(self.settings.get("recipient_title", ""))
        self.title_input.setPlaceholderText("e.g. Letter Title")
        self.name_input = IdentityLineEdit(self.settings.get("recipient_name", ""))
        self.name_input.setPlaceholderText("Recipient name")
        self.url_input = IdentityLineEdit(
            str(self.settings.get(PUBLISHED_PAGE_URL_KEY, ""))
        )
        for identity_input in (
            self.title_input,
            self.name_input,
            self.url_input,
        ):
            identity_input.setProperty("themeFontRole", "userEntry")
        self.url_input.setPlaceholderText("https://your-published-letter-page")
        self.url_input.setToolTip(
            "Save a valid HTTP or HTTPS address for this letter. "
            "Open Letter uses the saved address."
        )

        self._configure_identity_field(
            self.title_input,
            "title",
        )
        self._configure_identity_field(
            self.name_input,
            "recipient",
        )
        self._configure_identity_field(
            self.url_input,
            "published_url",
        )
        self._apply_loaded_identity_locks(
            persist=True
        )

        title_recipient_layout.addRow("Letter Title:", self.title_input)
        title_recipient_layout.addRow("Recipient:", self.name_input)
        title_recipient_layout.addRow("Published Page URL:", self.url_input)
        for identity_input in (
            self.title_input,
            self.name_input,
            self.url_input,
        ):
            controls = (
                title_recipient_layout.labelForField(identity_input),
                identity_input,
            )
            for control in controls:
                font = QFont(control.font())
                font.setPointSizeF(font.pointSizeF() + 1.0)
                control.setFont(font)

        self.title_input.editingFinished.connect(self._save_settings)
        self.name_input.editingFinished.connect(self._save_settings)
        self.url_input.editingFinished.connect(self._save_settings)
        shell.addWidget(self.title_recipient_container)

        shell.addWidget(self._build_message_overlay_controls())

        actions = QtWidgets.QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(10)
        actions.setVerticalSpacing(8)
        actions.setColumnStretch(0, 13)
        actions.setColumnStretch(1, 8)
        actions.setColumnStretch(2, 8)
        actions.setColumnStretch(3, 8)
        actions.setColumnStretch(4, 13)

        compact_button_style = (
            "QPushButton{min-height:40px;padding:0 16px;background:#171b20;color:#e4ebf4;"
            "border:1px solid #38424f;border-radius:7px;font-weight:700;}"
            "QPushButton:hover{border-color:#00d0ff;background:#1c252e;}"
            "QPushButton:disabled{color:#69727e;border-color:#2b3139;background:#15181c;}"
        )

        self.btn = DropMessageButton(self.project_root, "Import")
        self.btn.clicked.connect(self.select_file)
        self.btn.file_dropped.connect(self.handle_drop)
        actions.addWidget(self.btn, 0, 1)

        self.edit_btn = ArtworkButton("Edit", self.project_root, "GButton.png", self)
        apply_button_tier(self.edit_btn, ButtonTier.STANDARD)
        self.edit_btn.set_artwork_fill(True)
        self.edit_btn.set_artwork_stretch(True)
        if (
            self.edit_btn.uses_artwork_presentation
            and not self.edit_btn.has_artwork
        ):
            self.edit_btn.setStyleSheet(compact_button_style)
        set_control_help(
            self.edit_btn,
            "Open the rich-text editor to write or format the current message.",
            accessible_name="Edit message",
        )
        self.edit_btn.setEnabled(True)
        self.edit_btn.clicked.connect(self.open_editor)
        actions.addWidget(self.edit_btn, 0, 2)

        self.revisions_btn = ArtworkButton("Revisions", self.project_root, "HButton.png", self)
        apply_button_tier(self.revisions_btn, ButtonTier.STANDARD)
        self.revisions_btn.set_artwork_fill(True)
        self.revisions_btn.set_artwork_stretch(True)
        if (
            self.revisions_btn.uses_artwork_presentation
            and not self.revisions_btn.has_artwork
        ):
            self.revisions_btn.setStyleSheet(compact_button_style)
        set_control_help(
            self.revisions_btn,
            "Open autosaved message versions to preview or restore an earlier version.",
            accessible_name="Message revisions",
        )
        self.revisions_btn.clicked.connect(self.open_revision_history)
        actions.addWidget(self.revisions_btn, 0, 3)
        shell.addLayout(actions)

        self.status = QtWidgets.QLabel()
        self.status.setFont(QFont("Segoe UI", 9))
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("color:#aeb8c6; min-height:16px;")
        shell.addWidget(self.status)

        self.message_summary = QtWidgets.QLabel("0 words  •  0 characters  •  0 min read")
        self.message_summary.setAlignment(Qt.AlignCenter)
        self.message_summary.setStyleSheet(
            "color:#8794a5; font:9px 'Segoe UI'; padding-top:1px;"
        )
        shell.addWidget(self.message_summary)

        layout.addWidget(self.message_content_shell, 0, Qt.AlignHCenter | Qt.AlignTop)
        layout.addStretch(1)

        # Ensure fallback assets and load the current message immediately.
        self._check_existing()
        self._update_message_summary()

    # ──────────────────────────────────────────────────────────────────
    # Show hook: every time user clicks into Message tab
    # ──────────────────────────────────────────────────────────────────
    def _message_settings_signature(
        self,
        settings: dict[str, object] | None = None,
    ) -> str:
        if settings is None:
            settings = self.settings_store.snapshot()
        relevant = {
            key: settings.get(key)
            for key in (
                "recipient_title",
                "recipient_name",
                PUBLISHED_PAGE_URL_KEY,
                PUBLISHED_PUBLIC_PATH_KEY,
                PUBLISHED_AT_KEY,
                PUBLISHED_EXPIRES_AT_KEY,
                PUBLICATION_PROVIDER_KEY,
                PUBLICATION_VERIFIED_KEY,
                PUBLISHED_SOURCE_FINGERPRINT_KEY,
                PUBLISHED_GITHUB_OWNER_KEY,
                PUBLISHED_GITHUB_REPOSITORY_KEY,
                MESSAGE_OVERLAY_PRESET_KEY,
                MESSAGE_OVERLAY_OPACITY_KEY,
            )
        }
        return json.dumps(relevant, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _message_render_settings_signature(
        settings: dict[str, object],
    ) -> str:
        relevant = {
            key: settings.get(key)
            for key in (
                MESSAGE_OVERLAY_PRESET_KEY,
                MESSAGE_OVERLAY_OPACITY_KEY,
            )
        }
        return json.dumps(
            relevant,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def _capture_sync_state(self) -> dict[str, str]:
        settings = self.settings_store.snapshot()
        return {
            "html": file_fingerprint(self._html_path()),
            "png": file_fingerprint(self._png_path()),
            "wall": file_fingerprint(self._wall_path()),
            "settings": self._message_settings_signature(settings),
            "render_settings": self._message_render_settings_signature(settings),
        }

    def sync_from_disk(
        self,
        *,
        force: bool = False,
        lock_loaded_identity: bool = False,
    ) -> bool:
        """Reconcile Message content, metadata, rendering, and direct disk edits."""
        before = dict(getattr(self, "_sync_state", {}))
        after = self._capture_sync_state()
        changed_keys = {key for key, value in after.items() if force or before.get(key) != value}

        self._load_settings()
        self.title_input.setText(str(self.settings.get("recipient_title", "")))
        self.name_input.setText(str(self.settings.get("recipient_name", "")))
        self.url_input.setText(str(self.settings.get(PUBLISHED_PAGE_URL_KEY, "")))
        if lock_loaded_identity:
            self._apply_loaded_identity_locks(
                persist=True
            )
        else:
            self._sync_identity_field_lock(self.title_input, "title")
            self._sync_identity_field_lock(self.name_input, "recipient")
            self._sync_identity_field_lock(self.url_input, "published_url")
        (
            self.overlay_preset,
            self.overlay_opacity,
            _overlay_rgb,
            _overlay_ink,
        ) = _normalized_message_overlay_settings(self.settings)
        self._sync_overlay_controls()
        self._refresh_message_from_disk()
        self._ensure_wall_exists()

        render_changed = bool(
            changed_keys.intersection(
                {"html", "wall", "render_settings"}
            )
        )
        render_pending = False
        if render_changed and self.current_html.strip():
            render_pending = self._generate_image(self.current_html) is not None
        else:
            self._ensure_message_exists()

        if not render_pending:
            self._emit_best_preview()
        self._update_message_summary()
        self._sync_state = self._capture_sync_state()
        if changed_keys:
            self.project_changed.emit()
        return bool(changed_keys)

    def sync_to_disk(self) -> bool:
        """Persist Message metadata and finish render state before tab exit."""
        self._overlay_render_timer.stop()
        overlay_settings_flushed = self._flush_overlay_settings(
            emit_project_changed=False,
        )
        before = dict(
            getattr(
                self,
                "_sync_state",
                {},
            )
        )
        disk_state = self._capture_sync_state()
        render_changed = any(
            before.get(key)
            != disk_state.get(key)
            for key in (
                "html",
                "wall",
                "render_settings",
            )
        )
        self._save_settings()
        self._refresh_message_from_disk()
        workspace_message_fingerprint = file_fingerprint(self._html_path())
        if (
            self.current_html.strip()
            and not self._project_message_matches_workspace(
                workspace_message_fingerprint
            )
        ):
            try:
                self.project_save_service.save_message(
                    self.current_html,
                    workspace_path=self._html_path(),
                    reason="message-tab-exit",
                )
            except ProjectNotReadyError:
                pass
        self._ensure_wall_exists()
        if (
            self.current_html.strip()
            and (
                render_changed
                or not self._png_path().is_file()
            )
        ):
            render_pending = self._generate_image(self.current_html) is not None
        else:
            render_pending = False
        if not render_pending:
            self._ensure_message_exists()
        self._sync_state = self._capture_sync_state()
        changed = before != self._sync_state
        if changed or overlay_settings_flushed:
            self.project_changed.emit()
        return changed or overlay_settings_flushed

    def _project_message_matches_workspace(
        self,
        workspace_fingerprint: str,
    ) -> bool:
        """Avoid rewriting project metadata when Message content is unchanged."""
        if not self.project_state.is_project_ready:
            return True
        if not self.project_save_service.can_save():
            return True
        try:
            context = self.project_save_service.current_context()
            destination = self.project_save_service.project_file(
                context,
                Path("message") / "message.html",
            )
        except ProjectNotReadyError:
            return True
        except Exception:
            return False
        return file_fingerprint(destination) == workspace_fingerprint

    def activate_for_tab_change(self) -> None:
        if self._tab_active:
            return
        self._tab_active = True
        self.sync_from_disk()

    def deactivate_for_tab_change(self) -> None:
        if not self._tab_active:
            return
        self.sync_to_disk()
        self._tab_active = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.activate_for_tab_change()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self.deactivate_for_tab_change()
        super().hideEvent(event)

    def refresh_from_disk(self) -> None:
        self._overlay_render_timer.stop()
        self._overlay_update_pending = False
        if not self._cancel_pending_message_renders(
            timeout_ms=5000,
            resume=True,
        ):
            raise RuntimeError("Message rendering did not stop during refresh")
        self.sync_from_disk(
            force=True,
            lock_loaded_identity=True,
        )

    def focus_field(self, target: str) -> None:
        """Focus the Message correction requested by Project Readiness."""
        widget = {
            "recipient": self.name_input,
            "title": self.title_input,
            "published_url": self.url_input,
            "message": self.edit_btn,
        }.get(str(target))
        if widget is not None:
            widget.setFocus(Qt.OtherFocusReason)

    # ──────────────────────────────────────────────────────────────────
    # Nexus / Over_Nexus hooks
    # ──────────────────────────────────────────────────────────────────
    def toggle_title_recipient_area(self) -> None:
        self.title_recipient_container.setVisible(not self.title_recipient_container.isVisible())

    def open_message_editor(self) -> None:
        self.open_editor()

    # ──────────────────────────────────────────────────────────────────
    # Message overlay controls
    # ──────────────────────────────────────────────────────────────────
    def _build_message_overlay_controls(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(self.message_content_shell)
        panel.setObjectName("messageOverlayControls")
        panel.setMaximumHeight(86)
        panel.setStyleSheet(
            "QFrame#messageOverlayControls{background:#15191f;border:1px solid #303945;border-radius:8px;}"
            "QLabel{color:#cfd8e5;background:transparent;}"
            "QComboBox{min-height:30px;padding:0 28px 0 9px;background:#1d232b;color:#eef4fb;"
            "border:1px solid #435064;border-radius:6px;}"
            "QComboBox:hover{border-color:#00d0ff;}"
            "QComboBox::drop-down{width:24px;border:none;}"
            "QComboBox QAbstractItemView{background:#151a21;color:#eef4fb;border:1px solid #435064;"
            "selection-background-color:#254252;outline:0;}"
            "QSlider::groove:horizontal{height:4px;background:#272e38;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#447b8a;border-radius:2px;}"
            "QSlider::handle:horizontal{width:13px;margin:-5px 0;background:#d9e4ef;border-radius:6px;}"
            "QSlider:disabled::handle:horizontal{background:#68717c;}"
        )

        root = QtWidgets.QGridLayout(panel)
        root.setContentsMargins(10, 8, 10, 8)
        root.setHorizontalSpacing(10)
        root.setVerticalSpacing(5)
        root.setColumnStretch(1, 1)

        title = QtWidgets.QLabel("Text background", panel)
        title.setStyleSheet("font-weight:700;color:#eef4fb;")
        root.addWidget(title, 0, 0)

        self.overlay_preset_combo = QtWidgets.QComboBox(panel)
        self.overlay_preset_combo.setFixedWidth(210)
        set_control_help(
            self.overlay_preset_combo,
            "Choose the background treatment behind the message text in the finished letter.",
        )
        for key in ("paper", "black", "white", "clear"):
            self.overlay_preset_combo.addItem(MESSAGE_OVERLAY_PRESET_LABELS[key], key)
        self.overlay_preset_combo.currentIndexChanged.connect(self._on_overlay_preset_changed)
        root.addWidget(self.overlay_preset_combo, 0, 1)

        self.overlay_opacity_label = QtWidgets.QLabel(panel)
        self.overlay_opacity_label.setFixedWidth(190)
        self.overlay_opacity_label.setStyleSheet(
            "color:#aeb8c6;font:11pt 'Segoe UI';"
        )
        root.addWidget(self.overlay_opacity_label, 1, 0)

        self.overlay_opacity_slider = QtWidgets.QSlider(Qt.Horizontal, panel)
        self.overlay_opacity_slider.setRange(0, 100)
        self.overlay_opacity_slider.setValue(int(self.overlay_opacity))
        self.overlay_opacity_slider.setMaximumWidth(260)
        set_control_help(
            self.overlay_opacity_slider,
            "Adjust how strongly the selected text background covers the letter artwork.",
            accessible_name="Message background opacity",
        )
        self.overlay_opacity_slider.valueChanged.connect(self._set_overlay_opacity)
        root.addWidget(self.overlay_opacity_slider, 1, 1)

        self._sync_overlay_controls()
        return panel

    def _on_overlay_preset_changed(self, index: int) -> None:
        preset = self.overlay_preset_combo.itemData(index)
        self._set_overlay_preset(str(preset or ""))

    def _set_overlay_preset(self, preset: str) -> None:
        preset = str(preset or "").strip().lower()
        if preset not in MESSAGE_OVERLAY_PRESETS:
            return
        self.overlay_preset = preset
        if preset == "clear":
            self.overlay_opacity = 0
        elif self.overlay_opacity <= 0:
            self.overlay_opacity = DEFAULT_MESSAGE_OVERLAY_OPACITY
        self._persist_overlay_settings()

    def _set_overlay_opacity(self, value: int) -> None:
        self.overlay_opacity = max(0, min(100, int(value)))
        if self.overlay_preset == "clear" and self.overlay_opacity > 0:
            self.overlay_preset = DEFAULT_MESSAGE_OVERLAY_PRESET
        self._persist_overlay_settings()

    def _persist_overlay_settings(self) -> None:
        self.settings[MESSAGE_OVERLAY_PRESET_KEY] = self.overlay_preset
        self.settings[MESSAGE_OVERLAY_OPACITY_KEY] = int(self.overlay_opacity)
        self._overlay_update_pending = True
        self._sync_overlay_controls()
        self._overlay_render_timer.start()

    def _flush_overlay_settings(
        self,
        *,
        emit_project_changed: bool = True,
    ) -> bool:
        if not self._overlay_update_pending:
            return False
        self._overlay_update_pending = False
        return self._persist_settings(
            announce=False,
            emit_project_changed=emit_project_changed,
        )

    def _flush_overlay_update(self) -> None:
        self._overlay_render_timer.stop()
        if not self._overlay_update_pending:
            return
        self._flush_overlay_settings()
        self._render_overlay_preview()

    def _render_overlay_preview(self) -> None:
        html = self.current_html or "<p><br></p>"
        self._generate_image(html)

    def _sync_overlay_controls(self) -> None:
        if hasattr(self, "overlay_preset_combo"):
            target_index = self.overlay_preset_combo.findData(self.overlay_preset)
            self.overlay_preset_combo.blockSignals(True)
            if target_index >= 0:
                self.overlay_preset_combo.setCurrentIndex(target_index)
            self.overlay_preset_combo.blockSignals(False)

        is_transparent = self.overlay_preset == "clear"
        if hasattr(self, "overlay_opacity_slider"):
            self.overlay_opacity_slider.blockSignals(True)
            self.overlay_opacity_slider.setValue(int(self.overlay_opacity))
            self.overlay_opacity_slider.setEnabled(not is_transparent)
            self.overlay_opacity_slider.blockSignals(False)

        if hasattr(self, "overlay_opacity_label"):
            if is_transparent:
                self.overlay_opacity_label.setText("Artwork visible")
            else:
                self.overlay_opacity_label.setText(f"Opacity: {int(self.overlay_opacity)}%")


    # ──────────────────────────────────────────────────────────────────
    # Summary + revision history
    # ──────────────────────────────────────────────────────────────────
    def _update_message_summary(self, html: Optional[str] = None) -> None:
        raw = self.current_html if html is None else html
        plain = _plain_text_from_html(raw or "")
        words = _word_count(plain)
        characters = len(plain)
        self.message_summary.setText(
            f"{words:,} words  •  {characters:,} characters  •  {_reading_time_label(words)}"
        )
        if hasattr(self, "edit_btn"):
            self.edit_btn.setText("Edit" if plain.strip() else "Write Letter")

    def _refresh_message_from_disk(self) -> None:
        path = self._html_path()
        if not path.is_file():
            self.current_html = ""
            self._content_has_intentional_formatting = False
            return
        try:
            self.current_html = sanitize_message_html(
                path.read_text(encoding="utf-8")
            )
            self._content_has_intentional_formatting = True
        except Exception:
            pass

    def open_revision_history(self) -> None:
        RevisionHistoryDialog(self).exec()

    def restore_message_revision(self, revision_path: Path) -> bool:
        try:
            restored = restore_revision(self._html_path(), revision_path)
        except Exception as error:
            show_lettersmith_message(
                self,
                "Revision History",
                "The selected revision could not be restored.",
                detail=str(error),
            )
            return False

        restored = sanitize_message_html(restored)
        self.current_html = restored
        try:
            self.project_save_service.save_message(
                restored,
                workspace_path=self._html_path(),
                reason="revision-restore",
            )
        except Exception as error:
            self.status.setText(
                f"Could not save restored revision: {error}"
            )
            return False
        self._content_has_intentional_formatting = True
        self.text_selected.emit(restored)
        self._update_message_summary(restored)
        self._ensure_wall_exists()
        self._generate_image(restored)
        self.status.setText("Revision restored.")
        self._sync_state = self._capture_sync_state()
        self.project_changed.emit()
        return True

    # ──────────────────────────────────────────────────────────────────
    # Settings
    # ──────────────────────────────────────────────────────────────────
    def _load_settings(self) -> None:
        self.settings = self.settings_store.snapshot()

    @staticmethod
    def _setting_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)

    def _configure_identity_field(
        self,
        field: IdentityLineEdit,
        field_name: str,
    ) -> None:
        lock_key = IDENTITY_LOCK_KEYS[field_name]
        field.setReadOnly(
            self._setting_bool(self.settings.get(lock_key, False))
            and bool(field.text().strip())
        )
        field.double_clicked.connect(
            lambda field=field, field_name=field_name: self._unlock_identity_field(
                field,
                field_name,
            )
        )
        field.returnPressed.connect(
            lambda field=field, field_name=field_name: self._commit_identity_field(
                field,
                field_name,
            )
        )
        self._style_identity_field(field)

    def _sync_identity_field_lock(
        self,
        field: IdentityLineEdit,
        field_name: str,
    ) -> None:
        field.setReadOnly(
            self._setting_bool(
                self.settings.get(IDENTITY_LOCK_KEYS[field_name], False)
            )
            and bool(field.text().strip())
        )
        self._style_identity_field(field)

    def _apply_loaded_identity_locks(
        self,
        *,
        persist: bool,
    ) -> None:
        fields = {
            "title": self.title_input,
            "recipient": self.name_input,
            "published_url": self.url_input,
        }
        lock_updates: dict[str, bool] = {}
        for field_name, field in fields.items():
            lock_key = IDENTITY_LOCK_KEYS[field_name]
            locked = bool(field.text().strip())
            field.setReadOnly(locked)
            self.settings[lock_key] = locked
            lock_updates[lock_key] = locked
            self._style_identity_field(field)

        if not persist:
            return
        try:
            self.settings = self.settings_store.update_fields(
                lock_updates
            )
        except Exception:
            return

    @staticmethod
    def _style_identity_field(field: IdentityLineEdit) -> None:
        locked = field.isReadOnly()
        field.setProperty(
            "themeRole",
            "selectedState" if locked else "input",
        )
        field.setStyleSheet("")
        style = field.style()
        if style is not None:
            style.unpolish(field)
            style.polish(field)
        field.update()
        if locked:
            field.setToolTip(
                "Committed. Double-click to edit, then press Enter to commit again."
            )
        field.setMinimumHeight(
            max(
                field.sizeHint().height(),
                field.fontMetrics().height() + 10,
            )
        )

    def _unlock_identity_field(
        self,
        field: IdentityLineEdit,
        field_name: str,
    ) -> None:
        field.setReadOnly(False)
        self.settings[IDENTITY_LOCK_KEYS[field_name]] = False
        self._style_identity_field(field)
        field.setFocus(Qt.MouseFocusReason)

    def _commit_identity_field(
        self,
        field: IdentityLineEdit,
        field_name: str,
    ) -> None:
        value = field.text().strip()
        if not value:
            return
        if field_name == "published_url" and not _normalize_published_page_url(value):
            self.status.setText(
                "Published Page URL must be a valid HTTP or HTTPS address."
            )
            return
        if not self._save_settings():
            return
        field.setReadOnly(True)
        self.settings[IDENTITY_LOCK_KEYS[field_name]] = True
        if self._persist_settings(announce=False):
            self._style_identity_field(field)

    def reset_identity_locks(self) -> None:
        """Clear identity fields and leave all three controls editable."""
        for field_name, lock_key in IDENTITY_LOCK_KEYS.items():
            field = {
                "title": self.title_input,
                "recipient": self.name_input,
                "published_url": self.url_input,
            }[field_name]
            field.setReadOnly(False)
            self.settings[lock_key] = False
            self._style_identity_field(field)

    def _persist_settings(
        self,
        *,
        announce: bool,
        emit_project_changed: bool = True,
    ) -> bool:
        if not self.project_state.is_project_ready:
            if announce:
                self.status.setText(
                    "A recipient is required before saving."
                )
            return False
        try:
            before = self.settings_store.snapshot()
            fields = {
                key: self.settings[key]
                for key in (
                    "recipient_title",
                    "recipient_name",
                    PUBLISHED_PAGE_URL_KEY,
                    PUBLISHED_PUBLIC_PATH_KEY,
                    PUBLISHED_AT_KEY,
                    PUBLISHED_EXPIRES_AT_KEY,
                    PUBLICATION_PROVIDER_KEY,
                    PUBLICATION_VERIFIED_KEY,
                    PUBLISHED_SOURCE_FINGERPRINT_KEY,
                    PUBLISHED_GITHUB_OWNER_KEY,
                    PUBLISHED_GITHUB_REPOSITORY_KEY,
                    MESSAGE_OVERLAY_PRESET_KEY,
                    MESSAGE_OVERLAY_OPACITY_KEY,
                    *IDENTITY_LOCK_KEYS.values(),
                )
                if key in self.settings
            }
            self.settings = self.settings_store.update_fields(fields)
            changed = any(
                before.get(key) != self.settings.get(key)
                for key in fields
            )
            if announce:
                self.status.setText("Message details saved.")
        except Exception as error:
            if announce:
                self.status.setText(f"Error saving settings: {error}")
            return False
        if changed and emit_project_changed:
            self.project_changed.emit()
        return True

    def _reject_reserved_identity_edit(
        self,
        field_name: str,
        value: str,
        previous_value: str,
    ) -> bool:
        if not reserved_identity_field_reason(field_name, value):
            return False
        field = self.title_input if field_name == "title" else self.name_input
        field.setText(previous_value)
        message = f"{value} is an invalid {field_name}."
        self.status.setText(message)
        show_lettersmith_message(
            self,
            f"Invalid {field_name.title()}",
            message,
        )
        field.setFocus(Qt.OtherFocusReason)
        field.selectAll()
        return True

    def _save_settings(self) -> bool:
        recipient = self.name_input.text().strip()
        title = self.title_input.text().strip()
        current_recipient = (
            self.project_state.identity.recipient_display_name
        )
        current_title = str(
            self.settings.get("recipient_title", "")
        ).strip()
        if (
            title != current_title
            and MessageTab._reject_reserved_identity_edit(
                self,
                "title",
                title,
                current_title,
            )
        ):
            return False
        if (
            recipient != current_recipient
            and MessageTab._reject_reserved_identity_edit(
                self,
                "recipient",
                recipient,
                current_recipient,
            )
        ):
            return False
        if not recipient:
            self.name_input.setText(current_recipient)
            self.status.setText("Recipient is required.")
            return False
        if recipient != current_recipient or title != current_title:
            try:
                matching_recipient = (
                    self.project_state.recipient_registry.find_matching_recipient(
                        recipient
                    )
                )
                if matching_recipient is not None and title:
                    conflict = self.project_paths.find_title_conflict(
                        matching_recipient.recipient_id,
                        title,
                        project_id=self.project_state.identity.project_id,
                    )
                    if conflict is not None:
                        self.name_input.setText(current_recipient)
                        self.title_input.setText(current_title)
                        self.status.setText(
                            f"{matching_recipient.display_name} already has a "
                            f"letter titled {title!r}. "
                            "Enter a different letter title."
                        )
                        return False
            except Exception as error:
                self.name_input.setText(current_recipient)
                self.title_input.setText(current_title)
                self.status.setText(
                    f"Recipient and letter title could not be checked: {error}"
                )
                return False
        if recipient != current_recipient:
            try:
                identity = self.project_state.change_recipient(recipient)
            except Exception as error:
                self.name_input.setText(current_recipient)
                self.status.setText(
                    f"Recipient could not be changed: {error}"
                )
                return False
            recipient = identity.recipient_display_name
            self.name_input.setText(recipient)
            self.settings.update(identity.as_settings())

        self.settings["recipient_title"] = title
        self.settings["recipient_name"] = recipient

        raw_url = self.url_input.text().strip()
        normalized_url = _normalize_published_page_url(raw_url)
        if raw_url and not normalized_url:
            # Preserve the last valid saved URL instead of replacing it with an
            # unusable value. Title and recipient changes are still saved.
            self._persist_settings(announce=False)
            self.status.setText(
                "Title and recipient saved. Published Page URL must be a valid HTTP or HTTPS address."
            )
            return False

        previous_url = normalize_published_page_url(
            self.settings.get(PUBLISHED_PAGE_URL_KEY, "")
        )
        if normalized_url != previous_url:
            self.settings.update(clear_publication_state())
        self.settings[PUBLISHED_PAGE_URL_KEY] = normalized_url
        self.url_input.setText(normalized_url)
        if self._persist_settings(announce=True):
            self.published_page_url_changed.emit(normalized_url)
            return True
        return False

    def set_published_page_url(self, url: str, *, persist: bool = True, announce: bool = True) -> bool:
        raw_url = (url or "").strip()
        normalized_url = _normalize_published_page_url(raw_url)
        if raw_url and not normalized_url:
            if announce:
                self.status.setText("Published Page URL must be a valid HTTP or HTTPS address.")
            return False

        previous_url = normalize_published_page_url(
            self.settings.get(PUBLISHED_PAGE_URL_KEY, "")
        )
        if persist and normalized_url != previous_url:
            self.settings.update(clear_publication_state())
        self.settings[PUBLISHED_PAGE_URL_KEY] = normalized_url
        if hasattr(self, "url_input"):
            self.url_input.setText(normalized_url)
        if persist:
            if not self._persist_settings(announce=announce):
                return False
        else:
            self.settings = self.settings_store.snapshot()
            self.settings[PUBLISHED_PAGE_URL_KEY] = normalized_url
        self.published_page_url_changed.emit(normalized_url)
        return True

    # ──────────────────────────────────────────────────────────────────
    # Paths (AUTHORITATIVE)
    # ──────────────────────────────────────────────────────────────────
    def _html_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/message/message.html
        return Path(self.project_root) / MESSAGE_HTML_FILE

    def _png_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/message/message.png
        return Path(self.project_root) / MESSAGE_IMAGE_FILE

    def _wall_path(self) -> Path:
        # SOURCE OF TRUTH: gallery/user/pages/wall.png
        return Path(self.project_root) / USER_PAGES_DIR / "wall.png"

    def _sync_project_render_assets(self) -> None:
        if not self.project_state.is_project_ready:
            return
        if not self.project_save_service.can_save():
            return
        try:
            context = self.project_save_service.current_context()
        except ProjectNotReadyError:
            return
        for source, relative in (
            (
                self._png_path(),
                Path("message") / "message.png",
            ),
            (
                self._wall_path(),
                Path("pages") / "wall.png",
            ),
        ):
            if not source.is_file():
                continue
            try:
                destination = self.project_save_service.project_file(
                    context,
                    relative,
                )
                if file_fingerprint(destination) == file_fingerprint(source):
                    continue
                self.project_save_service.copy_workspace_file(
                    source,
                    relative,
                )
            except Exception as error:
                self.status.setText(
                    f"Could not save project artwork: {error}"
                )

    def _render_wall_path(self) -> Path:
        return self._wall_path()

    # ──────────────────────────────────────────────────────────────────
    # Selected wall pipeline
    # ──────────────────────────────────────────────────────────────────
    def _ensure_wall_exists(self) -> bool:
        """Report whether the required selected wall artwork exists."""
        return self._render_wall_path().is_file()

    def _ensure_message_exists(self) -> None:
        """
        Ensure message.png exists, scheduling its render when needed.
        - If missing, render using current_html if available,
          else render a blank message (wall-only).
        """
        out_png = self._png_path()
        if out_png.exists():
            return

        if not self._ensure_wall_exists():
            return

        # Render from HTML if any, else blank. The full-resolution work is
        # deliberately asynchronous; callers can display wall.png meanwhile.
        try:
            html = (self._html_path().read_text(encoding="utf-8") if self._html_path().exists() else "").strip()
            if not html:
                html = "<p></p>"
            if self._generate_image(html) is not None:
                return
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # Existing content load
    # ──────────────────────────────────────────────────────────────────
    def _check_existing(self) -> None:
        self._ensure_wall_exists()
        self._ensure_message_exists()

        html_path = self._html_path()
        if html_path.is_file():
            try:
                self.current_html = sanitize_message_html(
                    html_path.read_text(encoding="utf-8")
                )
                # Canonical message.html is a saved Letter Smith message and must
                # retain its intentional user formatting.
                self._content_has_intentional_formatting = True
                self.text_selected.emit(self.current_html)
            except Exception as error:
                self.status.setText(f"Failed to read message.html: {error}")
        else:
            self.current_html = ""
            self._content_has_intentional_formatting = False

        self.edit_btn.setEnabled(True)
        self._update_message_summary()
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Preview helpers
    # ──────────────────────────────────────────────────────────────────
    def _emit_best_preview(self) -> None:
        """
        Rule:
        - If message.png exists, show it.
        - Else show the selected wall.png when available.
        """
        png_path = self._png_path()
        if png_path.is_file():
            full_pix = QPixmap(str(png_path))
            if not full_pix.isNull():
                thumb = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(thumb)
                self.status.setText("🖼️ Showing message.png")
                return

        wall_path = self._render_wall_path()
        if wall_path.is_file():
            full_pix = QPixmap(str(wall_path))
            if not full_pix.isNull():
                thumb = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(thumb)
                self.status.setText("🧱 Showing wall.png")
                return

        self.status.setText("❌ Choose a letter background in Images.")

    # ──────────────────────────────────────────────────────────────────
    # File selection / drop
    # ──────────────────────────────────────────────────────────────────
    def select_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Message File",
            _downloads_directory(),
            "Messages (*.txt *.docx *.pdf *.odt *.html *.htm)",
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
        if not self.project_state.is_project_ready:
            self.status.setText(
                "A recipient is required before editing."
            )
            return
        html_path = self._html_path()
        if html_path.is_file():
            try:
                html_for_editor = sanitize_message_html(
                    html_path.read_text(encoding="utf-8")
                )
            except Exception:
                html_for_editor = self.current_html or ""
        else:
            html_for_editor = self.current_html or ""

        full_pix: Optional[QPixmap] = None
        png_path = self._png_path()
        if png_path.is_file():
            candidate = QPixmap(str(png_path))
            if not candidate.isNull():
                full_pix = candidate

        dlg = Editor(
            html_for_editor,
            full_pix,
            parent=self,
            apply_defaults=not self._content_has_intentional_formatting,
        )
        dlg.autosaved.connect(self._handle_editor_autosaved)
        dlg.finished.connect(lambda _result: self._handle_editor_finished(dlg))
        dlg.exec()

    def _handle_editor_autosaved(self, html: str) -> None:
        if not html:
            return
        html = sanitize_message_html(html)
        self.current_html = html
        self._content_has_intentional_formatting = True
        self._update_message_summary(html)
        self.text_selected.emit(html)
        self._sync_state = self._capture_sync_state()
        self.project_changed.emit()

    def _handle_editor_finished(self, dlg: QtWidgets.QDialog) -> None:
        try:
            new_html = dlg.get_edited_html()  # type: ignore[attr-defined]
        except Exception:
            new_html = self.current_html

        if not new_html:
            return

        new_html = sanitize_message_html(new_html)
        self.current_html = new_html
        self._content_has_intentional_formatting = True
        self._update_message_summary(new_html)
        self._ensure_wall_exists()
        self.text_selected.emit(new_html)
        self._generate_image(new_html)
        self.status.setText("Message saved.")
        self.project_changed.emit()

    # ──────────────────────────────────────────────────────────────────
    # Preview button
    # ──────────────────────────────────────────────────────────────────
    def _emit_preview(self) -> None:
        # Preview button should prefer message.png; otherwise show the selected wall.
        self._ensure_wall_exists()
        self._ensure_message_exists()
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Extraction + processing
    # ──────────────────────────────────────────────────────────────────
    def extract_text(self, path: str) -> str:
        try:
            return import_message_sync(path)
        except MessageImportError as error:
            self.status.setText(str(error))
            return ""

    def _process_file(self, path: str) -> None:
        process = self._message_import_process
        if process is not None:
            self.status.setText("A message is already being imported.")
            return

        result_path: Path | None = None
        try:
            source = Path(os.path.abspath(os.fspath(path)))
            result_path = create_result_path()
            program, arguments = worker_command(source, result_path)
        except (OSError, TypeError, ValueError):
            if result_path is not None:
                remove_result_path(result_path, attempts=3)
            self.status.setText("That file could not be imported.")
            return
        process = QtCore.QProcess(self)
        process.setStandardOutputFile(QtCore.QProcess.nullDevice())
        process.setStandardErrorFile(QtCore.QProcess.nullDevice())
        process.finished.connect(self._on_message_import_finished)
        process.errorOccurred.connect(self._on_message_import_error)
        self._message_import_process = process
        self._message_import_result_path = result_path
        self._message_import_source = source
        self._message_import_error = ""
        self.btn.setEnabled(False)
        self.status.setText(f"Importing {source.name}…")
        self._message_import_timer.start(MESSAGE_IMPORT_TIMEOUT_MS)
        process.start(program, arguments)

    def _on_message_import_error(
        self,
        error: QtCore.QProcess.ProcessError,
    ) -> None:
        if error != QtCore.QProcess.ProcessError.FailedToStart:
            return
        self._message_import_error = "That file could not be imported."
        self._finish_failed_message_import()

    def _on_message_import_timeout(self) -> None:
        process = self._message_import_process
        if process is None:
            return
        self._message_import_error = "That file took too long to import."
        process.kill()
        QtCore.QTimer.singleShot(
            500,
            lambda candidate=process: self._finish_failed_message_import(
                candidate
            ),
        )

    def _on_message_import_finished(
        self,
        exit_code: int,
        exit_status: QtCore.QProcess.ExitStatus,
    ) -> None:
        process = self._message_import_process
        if process is None or self.sender() is not process:
            return
        source = self._message_import_source
        result_path = self._message_import_result_path
        error_message = self._message_import_error
        imported_html = ""
        if (
            not error_message
            and (
                exit_code != 0
                or exit_status != QtCore.QProcess.ExitStatus.NormalExit
            )
        ):
            error_message = "That file could not be imported."
        if not error_message and result_path is not None:
            try:
                imported_html = read_worker_result(result_path)
            except MessageImportError as error:
                error_message = str(error)
        self._release_message_import()
        if error_message:
            self.status.setText(error_message)
            return
        if source is not None:
            self._apply_imported_message(str(source), imported_html)

    def _finish_failed_message_import(
        self,
        expected_process: QtCore.QProcess | None = None,
    ) -> None:
        if (
            self._message_import_process is None
            or (
                expected_process is not None
                and self._message_import_process is not expected_process
            )
        ):
            return
        error_message = (
            self._message_import_error
            or "That file could not be imported."
        )
        self._release_message_import()
        self.status.setText(error_message)

    def _release_message_import(self) -> None:
        self._message_import_timer.stop()
        process = self._message_import_process
        result_path = self._message_import_result_path
        self._message_import_process = None
        self._message_import_result_path = None
        self._message_import_source = None
        self._message_import_error = ""
        if hasattr(self, "btn"):
            self.btn.setEnabled(True)
        if process is not None:
            if process.state() != QtCore.QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(100)
            process.deleteLater()
        if result_path is not None:
            if not remove_result_path(result_path):
                QtCore.QTimer.singleShot(
                    250,
                    lambda candidate=result_path: remove_result_path(
                        candidate,
                        attempts=3,
                    ),
                )

    def _stop_message_import(self, timeout_ms: int) -> bool:
        process = self._message_import_process
        if process is None:
            return True
        try:
            process.finished.disconnect(self._on_message_import_finished)
            process.errorOccurred.disconnect(self._on_message_import_error)
        except (RuntimeError, TypeError):
            pass
        if process.state() != QtCore.QProcess.ProcessState.NotRunning:
            process.kill()
            stopped = process.waitForFinished(max(0, int(timeout_ms)))
        else:
            stopped = True
        self._release_message_import()
        return stopped

    def _apply_imported_message(self, path: str, imported_html: str) -> None:
        if not imported_html.strip():
            self.status.setText("That file had no extractable text.")
            return

        preserve_formatting = is_lettersmith_message_html(imported_html, filename=path)
        html = sanitize_message_html(
            imported_html
            if preserve_formatting
            else _normalize_imported_message_html(imported_html)
        )

        try:
            self.project_save_service.save_message(
                html,
                workspace_path=self._html_path(),
                reason="import",
            )
            self.current_html = html
            self._content_has_intentional_formatting = preserve_formatting
            self.edit_btn.setEnabled(True)
            self.status.setText(f"Message imported from {Path(path).name}.")
            self.text_selected.emit(html)
        except Exception as error:
            self.status.setText(f"Error saving message.html: {error}")
            return

        self._update_message_summary(html)
        self._ensure_wall_exists()
        self._generate_image(html)
        self.project_changed.emit()

    # ──────────────────────────────────────────────────────────────────
    # Render message.png (stable, saved in same folder as message.html)
    # ──────────────────────────────────────────────────────────────────
    def _generate_image(self, html: str) -> int | None:
        """Queue the latest full-resolution message.png render."""
        html = sanitize_message_html(html)
        if self._render_shutdown:
            return None
        if not self._ensure_wall_exists():
            if hasattr(self, "status"):
                self.status.setText("Choose a letter background in Images before rendering.")
            return None
        output_path = self._png_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        revision = self._render_gate.next_revision()
        if revision is None:
            return None
        self._render_thread_pool.clear()
        staged_path = output_path.with_name(
            f".{output_path.name}.render-{os.getpid()}-{id(self):x}-{revision}.tmp"
        )
        preset = self.overlay_preset
        if preset not in MESSAGE_OVERLAY_PRESETS:
            preset = DEFAULT_MESSAGE_OVERLAY_PRESET
        task = _MessageRenderTask(
            revision=revision,
            gate=self._render_gate,
            html=html,
            wall_path=self._render_wall_path(),
            output_path=output_path,
            staged_path=staged_path,
            preset=preset,
            overlay_opacity=self.overlay_opacity,
        )
        task.signals.finished.connect(self._on_message_render_finished)
        self._render_thread_pool.start(task)
        self.status.setText("Rendering message preview…")
        return revision

    @QtCore.Slot(int, bool, str)
    def _on_message_render_finished(
        self,
        revision: int,
        committed: bool,
        error_text: str,
    ) -> None:
        if (
            self._render_shutdown
            or revision != self._render_gate.current_revision()
        ):
            return
        if not committed:
            self._emit_best_preview()
            if error_text:
                self.status.setText(
                    f"Error generating message.png: {error_text}"
                )
            return
        self._sync_project_render_assets()
        self._emit_best_preview()
        self._sync_state = self._capture_sync_state()

    def _wait_for_message_render(self, timeout_ms: int = 5000) -> bool:
        return self._render_thread_pool.waitForDone(max(0, int(timeout_ms)))

    def _cancel_pending_message_renders(
        self,
        *,
        timeout_ms: int,
        resume: bool,
    ) -> bool:
        self._render_gate.invalidate(accepting=False)
        self._render_thread_pool.clear()
        stopped = self._wait_for_message_render(timeout_ms)
        if resume and not self._render_shutdown:
            self._render_gate.invalidate(accepting=True)
        return stopped

    def prepare_for_project_restore(self, timeout_ms: int = 5000) -> None:
        """Release render work before project directories are replaced."""
        self._overlay_render_timer.stop()
        self._flush_overlay_settings()
        if not self._stop_message_import(timeout_ms):
            raise RuntimeError("Message import did not stop before restore")
        if not self._cancel_pending_message_renders(
            timeout_ms=timeout_ms,
            resume=True,
        ):
            raise RuntimeError("Message rendering did not stop before restore")

    def reset_project_message(self) -> None:
        """Clear the live Message workspace after New Project commits."""
        self._overlay_render_timer.stop()
        self._overlay_update_pending = False
        if not self._stop_message_import(5000):
            raise RuntimeError("Message import did not stop during reset")
        if not self._cancel_pending_message_renders(
            timeout_ms=5000,
            resume=True,
        ):
            raise RuntimeError("Message rendering did not stop during reset")
        self._tab_active = False
        self.current_html = ""
        self._content_has_intentional_formatting = False
        self.settings = self.settings_store.snapshot()
        self.title_input.setText("")
        self.name_input.setText("")
        self.url_input.setText("")
        self.reset_identity_locks()
        (
            self.overlay_preset,
            self.overlay_opacity,
            _overlay_rgb,
            _overlay_ink,
        ) = _normalized_message_overlay_settings(self.settings)
        self._sync_overlay_controls()
        self._update_message_summary("")
        self._sync_state = self._capture_sync_state()
        self.text_selected.emit("")
        self.published_page_url_changed.emit("")
        self.status.setText("No message selected.")

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._overlay_render_timer.stop()
        self._flush_overlay_settings()
        import_stopped = self._stop_message_import(timeout_ms)
        try:
            if self._tab_active:
                self.deactivate_for_tab_change()
            else:
                self.sync_from_disk()
        except Exception:
            pass
        stopped = self._wait_for_message_render(timeout_ms)
        if stopped and self._png_path().is_file():
            self._sync_project_render_assets()
        self._render_shutdown = True
        self._render_gate.invalidate(accepting=False)
        self._render_thread_pool.clear()
        return stopped and import_stopped
