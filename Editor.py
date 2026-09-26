# File: Editor.py
# -*- coding: utf-8 -*-
"""
Letter Editor — Rich Text Editor

Public contract (required by Message_tab.py)
-------------------------------------------
Message_tab.py calls:
    dlg = Editor(self.current_html, full_pix, parent=self)
    if dlg.exec() == QDialog.Accepted:
        new_html = dlg.get_edited_html()

Therefore this module provides:
    class Editor(QDialog):
        __init__(message_html: str, preview_pixmap: Optional[QPixmap] = None, parent=None)
        get_edited_html() -> str
        apply_changes() -> None   (Save -> accept)

What this editor does
---------------------
- Writes atomically to the canonical message file:
    gallery/user/message/message.html
- Find/Replace (wrap-around, Enter-to-find, match-case).
- Toolbar:
    - Format dropdown: Bold / Italic / Underline / Strike / Clear
    - Lists dropdown: Bullet list / Numbered list
    - Spacing dropdown: (1.0, 1.15, 1.5, 2.0, 2.5, 3.0)
- Font family + size with live sync.
- Color picker (persists last color).
- Word/char counter.
- Unified editing surface painted over the selected wall image.
- Paste/drag-drop images into:
    <project_root>/gallery/message_assets/

Critical export fix
-------------------
To make browser output match line spacing selected in the editor, we inject:
    <div class="ls-linewrap" style="line-height:X;"> ... </div>
into the saved HTML body.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from bisect import bisect_right
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSize, QSettings, QMimeData
from PySide6.QtGui import (
    QFont,
    QFontInfo,
    QRawFont,
    QColor,
    QAction,
    QActionGroup,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
    QTextListFormat,
    QTextBlockFormat,
    QTextDocument,
    QPixmap,
    QImage,
)
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QToolBar,
    QFontComboBox,
    QSpinBox,
    QSlider,
    QLabel,
    QColorDialog,
    QPushButton,
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QMenu,
    QFrame,
    QCheckBox,
    QWidgetAction,
)

from config import (
    GALLERY_DIR,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
)
from language_service import get_language_service
from letter_page import (
    MESSAGE_RENDER_FONT_FAMILY,
    MESSAGE_RENDER_FONT_SIZE,
    MESSAGE_RENDER_HEIGHT,
    MESSAGE_RENDER_LINE_HEIGHT,
    MESSAGE_RENDER_MARGIN_BOTTOM,
    MESSAGE_RENDER_MARGIN_LR,
    MESSAGE_RENDER_MARGIN_TOP,
    MESSAGE_RENDER_WIDTH,
    LETTER_PAGE_PRESETS,
    composite_rgb,
    contrast_ratio,
    effective_letter_page_opacity,
    normalize_letter_page_preset,
    normalized_letter_page_settings,
)
from message_format import normalize_ultralinks_in_document
from project_save import ProjectNotReadyError, ProjectSaveService
from project_state import ProjectStateController
from project_paths import application_paths
from ui_sounds import EDITOR_WINDOW_OBJECT_NAME, UiSound, play_ui_sound
from editor_diagnostics import record_editor_failure
from message_html import (
    embed_lettersmith_style_state,
    extract_lettersmith_style_state,
    has_unsupported_lettersmith_style_state,
    is_ultralink_href,
    make_ultralink_href,
    mark_lettersmith_message_html,
    sanitize_message_html,
    ultralink_message_from_href,
)
from named_text_styles import (
    InvalidStyleSetError,
    SavedStyleSetStore,
    STYLE_KEYS,
    STYLE_LABELS,
    StyleSetOverwriteRequired,
    default_style_set,
)
from named_text_style_document import NamedTextStyleDocument
from message_history import SIGNIFICANT_MESSAGE_CHANGES, message_change_count
from named_text_style_preview import (
    StyleSelectionMenu,
    add_style_preview_submenu,
    preview_font,
    preview_font_stylesheet,
    set_action_style_preview,
    set_slot_style_preview,
)
from settings_store import SettingsStore
from ui_dialogs import (
    LetterSmithConfirmationDialog,
    LetterSmithDialog,
    LetterSmithInputDialog,
    show_lettersmith_message,
)
from window_chrome import StandardTitleBar, place_window_on_launcher

# ─────────────────────────────────────────────────────────────────────────────
# Constants & Helpers
# ─────────────────────────────────────────────────────────────────────────────

TOOLBAR_ICON_SIZE = QSize(16, 16)
DEFAULT_FONT_SIZE = MESSAGE_RENDER_FONT_SIZE
FONT_SIZE_MIN, FONT_SIZE_MAX = 1, 100
FONT_SIZE_STEP = 1


def _toolbar_symbol_font(base: QFont, symbol: str) -> QFont:
    """Use an installed symbol font when the toolbar font lacks a glyph."""
    for family in ("Segoe UI Symbol", "Apple Symbols", "Noto Sans Symbols 2", "DejaVu Sans"):
        font = QFont(base)
        font.setFamily(family)
        if not QFontInfo(font).exactMatch():
            continue
        raw_font = QRawFont.fromFont(font)
        if raw_font.isValid() and all(raw_font.supportsCharacter(ord(char)) for char in symbol):
            font.setPointSizeF(max(14.0, font.pointSizeF()))
            return font
    font = QFont(base)
    font.setPointSizeF(max(14.0, font.pointSizeF()))
    return font

SETTINGS_ORG = "LetterSmith"
SETTINGS_APP = "Editor"
SETTINGS_KEY_COLOR = "textColor"
SETTINGS_KEY_GEOMETRY = "windowGeometry"
SETTINGS_KEY_ULTRALINK_COLOR = "ultralinkColor"
DEFAULT_ULTRALINK_COLOR = QColor("#ffd84d")
MIXED_TEXT_COLOR_INDICATOR = (
    QColor("#ff0000"),
    QColor("#ff7f00"),
    QColor("#ffd700"),
    QColor("#00a651"),
    QColor("#0066ff"),
    QColor("#8f00ff"),
)
ASSET_SUBDIR = "message_assets"  # under gallery/
_LOGGER = logging.getLogger(__name__)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_name(filename: str) -> str:
    bad = '<>:"/\\|?*\0'
    out = "".join(("_" if ch in bad else ch) for ch in filename)
    out = out.strip().strip(".")
    return out or "asset"


def _read_json(fp: Path) -> dict:
    try:
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    except Exception:
        return {}


def _atomic_write(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    _ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".tmp.{int(time.time() * 1000)}")
    tmp.write_text(data, encoding=encoding)

    tries = 4
    for _ in range(tries):
        try:
            if path.exists():
                tmp.replace(path)
            else:
                tmp.rename(path)
            return
        except Exception:
            time.sleep(0.05)

    if path.exists():
        tmp.replace(path)
    else:
        tmp.rename(path)


# ─────────────────────────────────────────────────────────────────────────────
# Find / Replace Dialog
# ─────────────────────────────────────────────────────────────────────────────

class FindReplaceDialog(LetterSmithDialog):
    """Persistent, formatting-safe find/replace tool."""

    def __init__(self, parent: "Editor") -> None:
        super().__init__(
            parent,
            title="Find / Replace",
            modal=False,
            click_outside_dismiss=False,
            width=560,
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._editor: QTextEdit = parent.editor

        self.find_input = QLineEdit(placeholderText="Find…")
        self.replace_input = QLineEdit(placeholderText="Replace with…")
        self.match_case = QCheckBox("Match case")
        self.match_case.setChecked(False)
        self.status_label = QLabel("")
        self.status_label.setObjectName("findStatus")

        self.find_input.returnPressed.connect(self._find_from_return)
        self.replace_input.returnPressed.connect(self.replace_one)

        row = QHBoxLayout()
        row.addWidget(self.find_input, 1)
        row.addWidget(self.replace_input, 1)

        opts = QHBoxLayout()
        opts.addWidget(self.match_case)
        opts.addStretch(1)
        opts.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.replace_button = QPushButton("Replace")
        self.close_button = QPushButton("Close")
        self.previous_button.clicked.connect(self.find_previous)
        self.next_button.clicked.connect(self.find_next)
        self.replace_button.clicked.connect(self.replace_one)
        self.close_button.clicked.connect(self.hide)
        buttons.addWidget(self.previous_button)
        buttons.addWidget(self.next_button)
        buttons.addWidget(self.replace_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        root = self.content_layout
        root.addLayout(row)
        root.addLayout(opts)
        root.addLayout(buttons)

        self.find_input.textChanged.connect(self._clear_status)
        self.match_case.toggled.connect(self._clear_status)
        colors = self.colors
        self.setStyleSheet(
            self.styleSheet()
            + "QLineEdit{"
            f"background:{colors['background']};color:{colors['text']};"
            f"border:1px solid {colors['border']};padding:7px;border-radius:6px;}}"
            "QLabel,QCheckBox{"
            f"color:{colors['text']};padding-left:2px;}}"
            f"QLabel#findStatus{{color:{colors['accent']};}}"
            "QPushButton{"
            f"background:{colors['control_background']};color:{colors['text']};"
            f"border:1px solid {colors['accent']};border-radius:6px;"
            "padding:7px 12px;}"
            f"QPushButton:hover{{background:{colors['hover']};}}"
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.find_input.setFocus(Qt.OtherFocusReason)
        self.find_input.selectAll()

    def _clear_status(self, *_args) -> None:
        self.status_label.clear()

    def _find_from_return(self) -> None:
        if QtWidgets.QApplication.keyboardModifiers() & Qt.ShiftModifier:
            self.find_previous()
        else:
            self.find_next()

    def _find_options(self, *, backward: bool = False) -> QTextDocument.FindFlags:
        flags = QTextDocument.FindFlags()
        if self.match_case.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if backward:
            flags |= QTextDocument.FindBackward
        return flags

    def _find(self, *, backward: bool) -> bool:
        term = self.find_input.text()
        if not term:
            self.status_label.setText("Enter text to find.")
            return False

        doc = self._editor.document()
        flags = self._find_options(backward=backward)

        cur = self._editor.textCursor()
        start_cursor = QTextCursor(cur)
        if cur.hasSelection():
            start_cursor.setPosition(
                cur.selectionStart() if backward else cur.selectionEnd()
            )
        else:
            start_cursor.setPosition(cur.position())

        found = doc.find(term, start_cursor, flags)
        wrapped = False

        if found.isNull():
            wrapped = True
            boundary = QTextCursor(doc)
            boundary.movePosition(
                QTextCursor.End if backward else QTextCursor.Start
            )
            found = doc.find(term, boundary, flags)

        if found.isNull():
            play_ui_sound(UiSound.ERROR)
            self.status_label.setText("No matches found.")
            return False

        self._editor.setTextCursor(found)
        self._editor.ensureCursorVisible()
        if wrapped:
            self.status_label.setText(
                "Wrapped to end." if backward else "Wrapped to beginning."
            )
        else:
            self.status_label.setText("Match found.")
        return True

    def find_next(self) -> bool:
        return self._find(backward=False)

    def find_previous(self) -> bool:
        return self._find(backward=True)

    def replace_one(self) -> None:
        term = self.find_input.text()
        if not term:
            self.status_label.setText("Enter text to find.")
            return

        cur = self._editor.textCursor()
        selected = cur.selectedText()
        matches = (
            selected == term
            if self.match_case.isChecked()
            else selected.casefold() == term.casefold()
        )
        if cur.hasSelection() and matches:
            cur.insertText(self.replace_input.text())
            self._editor.setTextCursor(cur)
        self.find_next()


class UltralinkDialog(QDialog):
    def __init__(
        self,
        message: str = "",
        *,
        selection_text: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setModal(True)

        word_count = len(re.findall(r"\S+", selection_text.strip()))
        selection_reference = "this word" if word_count == 1 else "these words"
        self.instruction_label = QLabel(
            "Ultra Link: Whatever you type here is shown when the user "
            f"hovers over {selection_reference}.",
            self,
        )
        self.instruction_label.setWordWrap(True)

        self.message_edit = QPlainTextEdit(self)
        self.message_edit.setPlainText(message or "")
        self.message_edit.setPlaceholderText("Enter the Ultra Link hover text")
        self.message_edit.setMinimumSize(360, 130)

        self.save_button = QPushButton("Save", self)
        self.save_button.clicked.connect(self._save)

        root = QVBoxLayout(self)
        root.addWidget(self.instruction_label)
        root.addWidget(self.message_edit)
        root.addWidget(self.save_button, 0, Qt.AlignRight)

        self.setStyleSheet(
            "QDialog{background:#141414;border:1px solid #00d0ff;border-radius:8px;}"
            "QLabel{color:#ddd;}"
            "QPlainTextEdit{background:#0f0f0f;color:#eee;border:1px solid #2a2a2a;border-radius:6px;padding:8px;}"
            "QPushButton{background:#232323;color:#fff;border:1px solid #00d0ff;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
        )

    def message_text(self) -> str:
        return self.message_edit.toPlainText().strip()

    def _save(self) -> None:
        if not self.message_text():
            play_ui_sound(UiSound.ERROR)
            self.message_edit.setFocus(Qt.OtherFocusReason)
            return
        self.accept()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if (
            self.isVisible()
            and event.type() == QtCore.QEvent.MouseButtonPress
            and isinstance(watched, QtWidgets.QWidget)
            and not self._contains_popup_widget(watched)
        ):
            self.reject()
        return super().eventFilter(watched, event)

    def _contains_popup_widget(self, widget: QtWidgets.QWidget) -> bool:
        current: Optional[QtWidgets.QWidget] = widget
        while current is not None:
            if current is self:
                return True
            current = current.parentWidget()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RichTextEdit: paste/drop images into <project_root>/gallery/message_assets/
# ─────────────────────────────────────────────────────────────────────────────

class RichTextEdit(QTextEdit):
    language_refresh_requested = QtCore.Signal()

    def __init__(
        self,
        project_root: Path,
        *args,
        language_service: Optional[object] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.project_root = Path(project_root)
        self._language_service = language_service
        self._language_issues: tuple[object, ...] = ()
        self._ignored_language_words: set[str] = set()
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)
        self.viewport().setMouseTracking(True)

    @property
    def language_issues(self) -> tuple[object, ...]:
        return self._language_issues

    def set_language_issues(self, issues) -> None:
        filtered = tuple(
            issue
            for issue in issues
            if self._language_issue_key(issue)
            not in self._ignored_language_words
        )
        self._language_issues = filtered
        selections: list[QTextEdit.ExtraSelection] = []
        source = self.toPlainText()
        qt_offsets = self._python_to_qt_offsets(source)
        document_length = len(source)
        for issue in filtered:
            start = max(0, int(getattr(issue, "start", 0)))
            end = min(document_length, int(getattr(issue, "end", start)))
            if end <= start:
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(qt_offsets[start])
            cursor.setPosition(qt_offsets[end], QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            category = str(getattr(issue, "category", "spelling"))
            selection.format.setUnderlineColor(
                QColor("#ff6b7d" if category == "spelling" else "#f5b84b")
            )
            selection.format.setUnderlineStyle(
                QTextCharFormat.UnderlineStyle.SpellCheckUnderline
            )
            selections.append(selection)
        self.setExtraSelections(selections)

    def _language_issue_at(self, position: int):
        source = self.toPlainText()
        offsets = self._python_to_qt_offsets(source)
        python_position = max(
            0,
            min(len(source), bisect_right(offsets, int(position)) - 1),
        )
        return next(
            (
                issue
                for issue in self._language_issues
                if int(getattr(issue, "start", -1))
                <= python_position
                < int(getattr(issue, "end", -1))
            ),
            None,
        )

    @staticmethod
    def _python_to_qt_offsets(text: str) -> list[int]:
        offsets = [0]
        qt_position = 0
        for character in text:
            qt_position += 2 if ord(character) > 0xFFFF else 1
            offsets.append(qt_position)
        return offsets

    @staticmethod
    def _language_issue_key(issue) -> str:
        return str(getattr(issue, "text", "")).strip().casefold()

    def is_language_issue_ignored(self, issue) -> bool:
        key = self._language_issue_key(issue)
        return bool(key and key in self._ignored_language_words)

    def replace_language_issue(self, issue, replacement: str) -> bool:
        replacement = str(replacement or "")
        start = int(getattr(issue, "start", -1))
        end = int(getattr(issue, "end", -1))
        source = self.toPlainText()
        if start < 0 or end < start or end > len(source):
            return False
        qt_offsets = self._python_to_qt_offsets(source)
        cursor = QTextCursor(self.document())
        cursor.setPosition(qt_offsets[start])
        cursor.setPosition(qt_offsets[end], QTextCursor.KeepAnchor)
        char_format = QTextCharFormat(cursor.charFormat())
        cursor.beginEditBlock()
        try:
            cursor.insertText(replacement, char_format)
        finally:
            cursor.endEditBlock()
        self.language_refresh_requested.emit()
        return True

    def apply_language_replacements(
        self,
        issues,
        *,
        edit_cursor: Optional[QTextCursor] = None,
        emit_refresh: bool = True,
    ) -> int:
        replacements = sorted(
            (
                issue
                for issue in issues
                if getattr(issue, "replacement", None) is not None
            ),
            key=lambda issue: int(getattr(issue, "start", -1)),
            reverse=True,
        )
        if not replacements:
            return 0
        source = self.toPlainText()
        document_length = len(source)
        qt_offsets = self._python_to_qt_offsets(source)
        for issue in replacements:
            start = int(getattr(issue, "start", -1))
            end = int(getattr(issue, "end", -1))
            if start < 0 or end < start or end > document_length:
                raise ValueError("language issue range is outside the editor document")

        cursor = edit_cursor or QTextCursor(self.document())
        owns_edit_block = edit_cursor is None
        if owns_edit_block:
            cursor.beginEditBlock()
        applied = 0
        try:
            for issue in replacements:
                start = int(getattr(issue, "start"))
                end = int(getattr(issue, "end"))
                cursor.setPosition(qt_offsets[start])
                cursor.setPosition(qt_offsets[end], QTextCursor.KeepAnchor)
                char_format = QTextCharFormat(cursor.charFormat())
                cursor.insertText(str(getattr(issue, "replacement")), char_format)
                applied += 1
        finally:
            if owns_edit_block:
                cursor.endEditBlock()
        if applied and emit_refresh:
            self.language_refresh_requested.emit()
        return applied

    def ignore_language_issue(self, issue) -> None:
        word = str(getattr(issue, "text", "")).strip()
        key = word.casefold()
        if key:
            self._ignored_language_words.add(key)
        ignore_word = getattr(self._language_service, "ignore_word", None)
        if (
            word
            and str(getattr(issue, "category", "")) == "spelling"
            and callable(ignore_word)
        ):
            try:
                ignore_word(word)
            except Exception:
                _LOGGER.exception("Could not ignore Editor word for this session")
        self.set_language_issues(self._language_issues)

    def add_language_issue_to_dictionary(self, issue) -> bool:
        if self._language_service is None:
            return False
        word = str(getattr(issue, "text", "")).strip()
        if not word:
            return False
        try:
            added = bool(self._language_service.add_to_dictionary(word))
        except Exception:
            _LOGGER.exception("Could not add Editor word to the shared dictionary")
            return False
        self.language_refresh_requested.emit()
        return added

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu()
        issue = self._language_issue_at(self.cursorForPosition(event.pos()).position())
        if issue is not None:
            writing_menu = QMenu("Writing Suggestions", menu)
            suggestions = tuple(getattr(issue, "suggestions", ()) or ())
            replacement = getattr(issue, "replacement", None)
            if replacement is not None and replacement not in suggestions:
                suggestions = (replacement, *suggestions)
            for suggestion in suggestions[:5]:
                label = (
                    "Remove duplicate"
                    if suggestion == ""
                    else f"Replace with {suggestion}"
                )
                action = writing_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, value=suggestion, current=issue:
                    self.replace_language_issue(current, value)
                )
            if suggestions:
                writing_menu.addSeparator()
            ignore_action = writing_menu.addAction("Ignore")
            ignore_action.triggered.connect(
                lambda _checked=False, current=issue: self.ignore_language_issue(current)
            )
            if str(getattr(issue, "category", "")) == "spelling":
                add_action = writing_menu.addAction("Add to Dictionary")
                add_action.triggered.connect(
                    lambda _checked=False, current=issue:
                    self.add_language_issue_to_dictionary(current)
                )
            first_action = menu.actions()[0] if menu.actions() else None
            menu.insertMenu(first_action, writing_menu)
            menu.insertSeparator(first_action)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def viewportEvent(self, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.ToolTip:
            href = self.anchorAt(event.pos())
            message = ultralink_message_from_href(href)
            if message:
                QtWidgets.QToolTip.showText(
                    event.globalPos(),
                    message,
                    self.viewport(),
                )
                return True
            QtWidgets.QToolTip.hideText()
        return super().viewportEvent(event)

    def _assets_dir(self) -> Path:
        return self.project_root / GALLERY_DIR / ASSET_SUBDIR

    def _copy_into_assets(self, src_path: Path) -> Optional[str]:
        if not src_path.is_file():
            return None

        assets_dir = self._assets_dir()
        _ensure_dir(assets_dir)

        base = _safe_name(src_path.name)
        dst = assets_dir / base

        if dst.exists():
            stem, suf = dst.stem, dst.suffix
            n = 2
            while True:
                cand = assets_dir / f"{stem}_{n}{suf}"
                if not cand.exists():
                    dst = cand
                    break
                n += 1

        try:
            shutil.copy2(src_path, dst)
            rel = (Path(GALLERY_DIR) / ASSET_SUBDIR / dst.name).as_posix()
            return rel
        except Exception:
            return None

    def insert_image_html(self, rel_uri: str, width_px: int = 320) -> None:
        c = self.textCursor()
        c.insertHtml(f'<img src="{rel_uri}" width="{int(width_px)}"/>')

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        handled = False
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                rel = self._copy_into_assets(p)
                if rel:
                    self.insert_image_html(rel, 320)
                    handled = True
                    break
        if handled:
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        controller = getattr(self, "named_style_controller", None)
        if controller is not None and controller.handle_key_press(event):
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                assets_dir = self._assets_dir()
                _ensure_dir(assets_dir)
                name = f"pasted_{int(time.time() * 1000)}.png"
                out = assets_dir / name
                img.save(str(out), "PNG")
                rel = (Path(GALLERY_DIR) / ASSET_SUBDIR / name).as_posix()
                self.insert_image_html(rel, 320)
                return

        if source.hasUrls():
            for url in source.urls():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                    rel = self._copy_into_assets(p)
                    if rel:
                        self.insert_image_html(rel, 320)
                        return

        controller = getattr(self, "named_style_controller", None)
        context = controller.before_paste(source) if controller is not None else None
        try:
            super().insertFromMimeData(source)
        finally:
            if controller is not None:
                controller.after_paste(context)


class FontSizeSpinBox(QSpinBox):
    """Exact point-size input that ignores incidental page scrolling."""

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class EditorSurface(QFrame):
    """The single editing surface shared with the committed message renderer."""

    def __init__(
        self,
        editor: QTextEdit,
        wall_pixmap: Optional[QPixmap] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LetterEditorSurface")
        self.setFrameStyle(QFrame.NoFrame)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self._editor = editor
        self._wall_pixmap = (
            wall_pixmap
            if wall_pixmap is not None and not wall_pixmap.isNull()
            else None
        )
        self._preset = "paper"
        self._opacity = 68
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(0)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(editor)

        editor.setFrameStyle(QFrame.NoFrame)
        editor.setAttribute(Qt.WA_TranslucentBackground, True)
        editor.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        editor.viewport().setAutoFillBackground(False)

    @property
    def wall_pixmap(self) -> Optional[QPixmap]:
        return self._wall_pixmap

    def set_wall_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self._wall_pixmap = (
            pixmap if pixmap is not None and not pixmap.isNull() else None
        )
        self.update()

    def set_surface_settings(self, preset: str, opacity: int) -> None:
        self._preset = normalize_letter_page_preset(preset)
        self._opacity = max(
            0,
            min(100, int(effective_letter_page_opacity(self._preset, opacity))),
        )
        self.update()

    def _scaled_background(self) -> Optional[QPixmap]:
        pixmap = self._wall_pixmap
        if pixmap is None or pixmap.isNull() or self.size().isEmpty():
            return None
        return pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )

    def _overlay_color(self, x_fraction: float, y_fraction: float) -> tuple[int, int, int]:
        definition = LETTER_PAGE_PRESETS[self._preset]
        x = max(0.0, min(1.0, float(x_fraction)))
        y = max(0.0, min(1.0, float(y_fraction)))
        distance = min(1.0, ((x - 0.5) ** 2 + (y - 0.43) ** 2) ** 0.5 / 0.76)
        return tuple(
            round(center + ((edge - center) * distance))
            for center, edge in zip(definition.center_rgb, definition.edge_rgb)
        )

    def readability_backgrounds(self) -> tuple[tuple[int, int, int], ...]:
        """Return representative composited backgrounds for live contrast checks."""
        definition = LETTER_PAGE_PRESETS[self._preset]
        samples = ((0.50, 0.43), (0.20, 0.25), (0.80, 0.75))
        image = self._wall_pixmap.toImage() if self._wall_pixmap is not None else None
        if image is not None and not image.isNull():
            image = image.convertToFormat(QImage.Format_RGB32)

        backgrounds: list[tuple[int, int, int]] = []
        alpha = self._opacity / 100.0
        for x_fraction, y_fraction in samples:
            if image is None or image.isNull():
                wall_rgb = definition.base_rgb
            else:
                x = max(0, min(image.width() - 1, round((image.width() - 1) * x_fraction)))
                y = max(0, min(image.height() - 1, round((image.height() - 1) * y_fraction)))
                color = image.pixelColor(x, y)
                wall_rgb = (color.red(), color.green(), color.blue())
            backgrounds.append(
                composite_rgb(wall_rgb, self._overlay_color(x_fraction, y_fraction), alpha)
            )
        return tuple(backgrounds)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        width_scale = self.width() / float(MESSAGE_RENDER_WIDTH or 1)
        height_scale = self.height() / float(MESSAGE_RENDER_HEIGHT or 1)
        self._layout.setContentsMargins(
            max(12, round(MESSAGE_RENDER_MARGIN_LR * width_scale)),
            max(12, round(MESSAGE_RENDER_MARGIN_TOP * height_scale)),
            max(12, round(MESSAGE_RENDER_MARGIN_LR * width_scale)),
            max(12, round(MESSAGE_RENDER_MARGIN_BOTTOM * height_scale)),
        )
        super().resizeEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        background = self._scaled_background()
        if background is None:
            painter.fillRect(self.rect(), QtGui.QColor(14, 14, 18))
        else:
            source = QtCore.QRect(
                max(0, (background.width() - self.width()) // 2),
                max(0, (background.height() - self.height()) // 2),
                min(self.width(), background.width()),
                min(self.height(), background.height()),
            )
            painter.drawPixmap(self.rect(), background, source)

        if self._opacity > 0:
            definition = LETTER_PAGE_PRESETS[self._preset]
            alpha = round(255 * (self._opacity / 100.0))
            gradient = QtGui.QRadialGradient(
                QtCore.QPointF(self.width() * 0.5, self.height() * 0.43),
                max(1.0, (self.width() ** 2 * 0.25 + self.height() ** 2 * 0.325) ** 0.5),
            )
            gradient.setColorAt(0.0, QtGui.QColor(*definition.center_rgb, alpha))
            gradient.setColorAt(1.0, QtGui.QColor(*definition.edge_rgb, alpha))
            painter.fillRect(self.rect(), gradient)
        painter.end()
        super().paintEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Editor Dialog
# ─────────────────────────────────────────────────────────────────────────────

class Editor(QDialog):
    autosaved = QtCore.Signal(str)

    def __init__(
        self,
        message_html: str,
        preview_pixmap: Optional[QPixmap] = None,
        parent=None,
        *,
        apply_defaults: bool = False,
        language_service: Optional[object] = None,
    ) -> None:
        super().__init__(parent)

        # Resolve project root (authoritative)
        self.project_root = Path(getattr(parent, "project_root", os.getcwd()))
        try:
            self._language_service = language_service or get_language_service(
                self.project_root
            )
        except Exception:
            _LOGGER.exception("Could not initialize the shared language service")
            self._language_service = None

        # Canonical message location (SOURCE OF TRUTH)
        self.message_path = (self.project_root / MESSAGE_HTML_FILE).resolve()
        self.message_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path = application_paths(
            self.project_root
        ).settings_file.resolve()
        self._style_slots = SavedStyleSetStore(SettingsStore(self.project_root))
        self.project_state = getattr(parent, "project_state", None)
        if self.project_state is None:
            self.project_state = ProjectStateController(self.project_root)
            self.project_state.initialize()
        self.project_save_service = getattr(
            parent,
            "project_save_service",
            None,
        ) or ProjectSaveService(
            self.project_root,
            self.project_state,
        )

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        s = _read_json(self.settings_path)
        self.recipient_name = (
            self.project_state.identity.recipient_display_name
            or str(s.get("recipient_name") or "").strip()
        )

        saved = self.settings.value(SETTINGS_KEY_COLOR, QColor("#eeeeee"))
        self.last_color = saved if isinstance(saved, QColor) else QColor("#eeeeee")
        saved_ultralink = self.settings.value(
            SETTINGS_KEY_ULTRALINK_COLOR,
            DEFAULT_ULTRALINK_COLOR,
        )
        ultralink_color = (
            QColor(saved_ultralink)
            if isinstance(saved_ultralink, QColor)
            else QColor(str(saved_ultralink))
        )
        self.ultralink_color = (
            ultralink_color
            if ultralink_color.isValid()
            else QColor(DEFAULT_ULTRALINK_COLOR)
        )

        self.message_html = message_html or ""
        self._unsupported_style_state = has_unsupported_lettersmith_style_state(
            self.message_html
        )
        self._restored_style_state = extract_lettersmith_style_state(self.message_html)
        self._apply_message_defaults = bool(apply_defaults)
        self._initializing = True
        self._last_persisted_html = ""
        self._find_dialog: Optional[FindReplaceDialog] = None
        self._closing = False
        self._save_in_progress = False
        self._discard_changes = False

        # Tracks the most recent spacing selection (used to force identical browser output).
        self._export_line_spacing: Optional[float] = (
            2.0 if self._apply_message_defaults and self._restored_style_state is None
            else None
        )

        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setInterval(5 * 60 * 1000)
        self._autosave_timer.timeout.connect(self._autosave_now)
        self._language_check_timer = QtCore.QTimer(self)
        self._language_check_timer.setSingleShot(True)
        self._language_check_timer.setInterval(550)
        self._language_check_timer.timeout.connect(self._refresh_language_issues)

        self.setWindowTitle("Letter Editor")
        self.setObjectName(EDITOR_WINDOW_OBJECT_NAME)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
        )
        self.setModal(True)
        self.resize(1100, 720)
        self._wall_pixmap = (
            preview_pixmap
            if preview_pixmap is not None and not preview_pixmap.isNull()
            else None
        )

        self._apply_styles()
        self._restore_geometry()
        self._build_ui(self._wall_pixmap)
        theme_service = getattr(parent, "theme_service", None)
        if theme_service is None and isinstance(parent, QtWidgets.QWidget):
            theme_service = getattr(parent.window(), "theme_service", None)
        if theme_service is not None:
            self.title_bar.apply_theme_assets(theme_service)
        self._settings_watcher = QtCore.QFileSystemWatcher(self)
        if self.settings_path.is_file():
            self._settings_watcher.addPath(str(self.settings_path))
        self._settings_watcher.fileChanged.connect(self._settings_file_changed)
        try:
            self._last_persisted_html = (
                self.message_path.read_text(encoding="utf-8")
                if self.message_path.is_file()
                else ""
            )
        except Exception as error:
            self._record_failure("load persisted message", error)
            self._last_persisted_html = ""
        self._persisted_document_html = (
            self._prepared_html() if not self._unsupported_style_state else self.message_html
        )
        self._initializing = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._update_font_controls_height()
        place_window_on_launcher(self, minimum=QSize(640, 420))

    def _settings_file_changed(self, path: str) -> None:
        if self._closing:
            return
        try:
            self.refresh_background_from_settings()
        except Exception as error:
            self._record_failure("refresh editor settings", error)
        if Path(path).is_file() and path not in self._settings_watcher.files():
            self._settings_watcher.addPath(path)

    def _build_ui(self, wall_pixmap: Optional[QPixmap]) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.title_bar = StandardTitleBar(
            self,
            "Letter Editor",
            show_minimize=False,
            on_close=self._request_close,
        )
        main_layout.addWidget(self.title_bar)

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(TOOLBAR_ICON_SIZE)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        main_layout.addWidget(self.toolbar)

        self.editor = RichTextEdit(
            self.project_root,
            language_service=self._language_service,
        )
        self.editor.setObjectName("EditorTextArea")
        self.editor.setProperty("themeFontRole", "letterContent")
        self.editor.document().setDefaultFont(
            QFont(MESSAGE_RENDER_FONT_FAMILY, DEFAULT_FONT_SIZE)
        )
        self.editor.setHtml(self.message_html)
        normalize_ultralinks_in_document(self.editor.document())
        self.named_styles = NamedTextStyleDocument(
            self.editor,
            default_style_set(
                color=QColor(self._editor_background_setting()[2]).name()
            ),
        )
        if self._restored_style_state is not None:
            try:
                self.named_styles.restore(self._restored_style_state)
            except (InvalidStyleSetError, ValueError) as error:
                _LOGGER.warning("Ignoring invalid letter style state: %s", error)
        self.editor_surface = EditorSurface(
            self.editor,
            wall_pixmap=wall_pixmap,
            parent=self,
        )
        main_layout.addWidget(self.editor_surface, 1)

        self._build_toolbar_actions()
        if self._apply_message_defaults and self._restored_style_state is None:
            self._apply_initial_message_defaults()
            if not self.editor.toPlainText().strip():
                self.named_styles.apply_style("normal_text")

        self.editor.textChanged.connect(self.update_word_count)
        self.editor.textChanged.connect(self._schedule_autosave)
        self.editor.textChanged.connect(self._schedule_language_check)
        self.editor.textChanged.connect(self._update_readability_indicator)
        self.editor.textChanged.connect(self._sync_named_style_selector)
        self.editor.language_refresh_requested.connect(self._schedule_language_check)
        self.editor.currentCharFormatChanged.connect(self._sync_format)
        self.editor.currentCharFormatChanged.connect(self._update_readability_indicator)

        hb = QHBoxLayout()
        self.word_label = QLabel()
        hb.addWidget(self.word_label)
        self.readability_indicator = QLabel()
        self.readability_indicator.setObjectName("readabilityIndicator")
        self.readability_indicator.setAccessibleName("Text readability status")
        self.readability_indicator.setToolTip(
            "Live contrast check for the current text color against the letter artwork."
        )
        hb.addWidget(self.readability_indicator)
        hb.addStretch()

        btn_save = QPushButton("Save")
        self.btn_save = btn_save
        btn_save.setShortcut(QKeySequence.Save)
        btn_save.setAutoDefault(False)
        btn_save.setDefault(False)
        btn_save.clicked.connect(self._save_only)

        btn_close = QPushButton("Close")
        self.btn_close = btn_close
        btn_close.setAutoDefault(False)
        btn_close.setDefault(False)
        btn_close.clicked.connect(self._request_close)

        btn_save_close = QPushButton("Save and Close")
        self.btn_save_close = btn_save_close
        btn_save_close.setAutoDefault(False)
        btn_save_close.setDefault(False)
        btn_save_close.clicked.connect(self.save_and_close)

        hb.addWidget(btn_save)
        hb.addWidget(btn_close)
        hb.addWidget(btn_save_close)
        main_layout.addLayout(hb)

        for control in (self.font_combo, self.font_size_spin):
            control.installEventFilter(self)
            if control.lineEdit() is not None:
                control.lineEdit().installEventFilter(self)
        self.editor.cursorPositionChanged.connect(self._sync_current_format)
        self.editor.selectionChanged.connect(self._sync_current_format)
        self.editor.cursorPositionChanged.connect(self._update_readability_indicator)
        self.editor.selectionChanged.connect(self._update_readability_indicator)
        self._apply_editor_background()
        # Restoring the root style metadata and applying the editor stylesheet
        # can reset Qt's insertion format. Re-read the visible text at the cursor.
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(self.editor.textCursor().position())
        self.editor.setTextCursor(cursor)
        self._sync_current_format()
        self._update_readability_indicator()
        self._schedule_language_check()

    def _editor_background_setting(self) -> tuple[str, int, str]:
        data = _read_json(self.settings_path)
        preset, opacity, _rgb, ink = normalized_letter_page_settings(data)
        return preset, opacity, ink

    def _apply_editor_background(self) -> None:
        preset, opacity, foreground = self._editor_background_setting()
        self.editor_surface.set_surface_settings(preset, opacity)
        self.editor.document().setDefaultStyleSheet(
            f"body {{ color: {foreground}; background: transparent; "
            f"font-family: '{MESSAGE_RENDER_FONT_FAMILY}'; "
            f"text-align: center; line-height: {MESSAGE_RENDER_LINE_HEIGHT:g}; }}"
            "p { margin: 0 0 12px 0; }"
            f"br {{ line-height: {MESSAGE_RENDER_LINE_HEIGHT:g}; }}"
        )
        self.editor.setStyleSheet(
            "QTextEdit#EditorTextArea{"
            f"background:transparent;color:{foreground};"
            "border:none;border-radius:0;padding:0;"
            "selection-background-color:#c9a86a;"
            f"selection-color:{foreground};}}"
        )
        self.editor.setAttribute(Qt.WA_TranslucentBackground, True)
        self.editor.viewport().setAttribute(
            Qt.WA_TranslucentBackground,
            True,
        )
        self.editor.viewport().setAutoFillBackground(False)
        self.editor.viewport().update()
        self.editor_surface.update()

    def refresh_background_from_settings(self) -> None:
        self._apply_editor_background()
        self._update_readability_indicator()

    def _update_readability_indicator(self, *_args: object) -> None:
        if not hasattr(self, "readability_indicator") or self._closing:
            return
        try:
            _preset, _opacity, fallback = self._editor_background_setting()
            cursor = self.editor.textCursor()
            if cursor.hasSelection():
                colors = self._selected_text_colors(cursor)
            else:
                brush = self.editor.currentCharFormat().foreground()
                color = (
                    brush.color()
                    if brush.style() != Qt.BrushStyle.NoBrush
                    else QColor(fallback)
                )
                colors = (color if color.isValid() else QColor(fallback),)
            backgrounds = self.editor_surface.readability_backgrounds()
            ratios = tuple(
                contrast_ratio(
                    (color.red(), color.green(), color.blue()),
                    background,
                )
                for color in colors
                for background in backgrounds
            )
            minimum = min(ratios) if ratios else 0.0
            if minimum >= 4.5:
                state, text = "good", "Contrast good"
            elif minimum >= 3.0:
                state, text = "reduced", "Contrast reduced"
            else:
                state, text = "low", "Low contrast"
            self.readability_indicator.setText(text)
            self.readability_indicator.setProperty("readabilityState", state)
            self.readability_indicator.setToolTip(
                f"Lowest sampled contrast: {minimum:.1f}:1."
            )
            style = self.readability_indicator.style()
            style.unpolish(self.readability_indicator)
            style.polish(self.readability_indicator)
            self.readability_indicator.update()
        except Exception as error:
            self._record_failure("update readability indicator", error)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is getattr(self, "font_controls", None) and event.type() in (
            QtCore.QEvent.LayoutRequest, QtCore.QEvent.FontChange,
            QtCore.QEvent.StyleChange,
        ):
            self._update_font_controls_height()
        format_inputs = (
            self.font_combo,
            self.font_size_spin,
            self.font_combo.lineEdit(),
            self.font_size_spin.lineEdit(),
        )
        if watched in format_inputs and event.type() == QtCore.QEvent.KeyPress:
            key_event = event
            if isinstance(key_event, QtGui.QKeyEvent) and key_event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if watched in (self.font_size_spin, self.font_size_spin.lineEdit()):
                    self._run_editor_action(
                        "Change font size",
                        self._commit_font_size_input,
                    )
                else:
                    self._run_editor_action(
                        "Change font family",
                        lambda: self.set_font_family(self.font_combo.currentFont()),
                    )
                self.editor.setFocus(Qt.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def _sync_current_format(self) -> None:
        self.named_styles.sync_empty_insertion_format()
        self._sync_format(self.editor.currentCharFormat())
        self._sync_block_actions()
        self._sync_named_style_selector()
        self.update_word_count()

    def _sync_block_actions(self) -> None:
        if hasattr(self, "alignment_actions"):
            alignment = self.editor.textCursor().blockFormat().alignment()
            for name, action in self.alignment_actions.items():
                expected = {
                    "left": Qt.AlignLeft,
                    "center": Qt.AlignCenter,
                    "right": Qt.AlignRight,
                }[name]
                blocker = QtCore.QSignalBlocker(action)
                action.setChecked(bool(alignment & expected))
                del blocker
        if hasattr(self, "spacing_actions"):
            selected = self._export_line_spacing
            for multiplier, action in self.spacing_actions.items():
                blocker = QtCore.QSignalBlocker(action)
                action.setChecked(
                    selected is not None
                    and abs(float(selected) - multiplier) < 0.001
                )
                del blocker

    def _run_editor_action(
        self,
        label: str,
        callback: Callable[[], None],
    ) -> None:
        """Contain toolbar failures so one broken action cannot close the editor."""
        if self._closing:
            return
        try:
            callback()
        except Exception as error:
            self._record_failure(f"editor action: {label}", error)
            show_lettersmith_message(
                self,
                "Editor Action Failed",
                f"{label} could not be completed. Your letter remains open. "
                "Details were recorded in editor_error.log.",
            )
        finally:
            if not self._closing:
                try:
                    self._sync_current_format()
                except Exception as error:
                    self._record_failure(
                        f"synchronize controls after: {label}",
                        error,
                    )

    def _record_failure(
        self,
        operation: str,
        error: BaseException,
    ) -> Optional[Path]:
        _LOGGER.error(
            "Editor failure during %s",
            operation,
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            return record_editor_failure(self.project_root, operation, error)
        except Exception:
            _LOGGER.exception("Could not write editor_error.log")
            return None

    def _guarded_action(
        self,
        label: str,
        callback: Callable[[], None],
    ) -> Callable[..., None]:
        return lambda *_args: self._run_editor_action(label, callback)

    def _font_family_changed(self, font: QFont) -> None:
        self._run_editor_action(
            "Change font family",
            lambda: self.set_font_family(font),
        )

    def _font_size_changed(self, size: int) -> None:
        self._run_editor_action(
            "Change font size",
            lambda: self.set_font_size(size),
        )

    def _build_toolbar_actions(self) -> None:
        self.act_salutation = QAction("Salutation", self)
        self.act_salutation.triggered.connect(
            self._guarded_action("Insert salutation", self.insert_salutation)
        )
        self.toolbar.addAction(self.act_salutation)
        salutation_font = QFont(self.toolbar.font())
        salutation_font.setBold(True)
        salutation_font.setItalic(True)
        self.act_salutation.setFont(salutation_font)
        self.toolbar.widgetForAction(self.act_salutation).setFont(salutation_font)

        self.toolbar.addSeparator()

        # Format dropdown
        self.btn_format = QToolButton(self)
        self.btn_format.setText("Format")
        self.btn_format.setPopupMode(QToolButton.InstantPopup)
        self.btn_format.setAutoRaise(True)

        fmt_menu = QMenu(self)
        self.act_bold = fmt_menu.addAction("Bold")
        bold_label_font = QFont(fmt_menu.font())
        bold_label_font.setBold(True)
        self.act_bold.setFont(bold_label_font)
        self.act_bold.setCheckable(True)
        self.act_bold.setShortcut(QKeySequence.Bold)
        self.act_bold.triggered.connect(
            self._guarded_action("Bold", self.toggle_bold)
        )

        self.act_italic = fmt_menu.addAction("Italic")
        italic_label_font = QFont(fmt_menu.font())
        italic_label_font.setItalic(True)
        self.act_italic.setFont(italic_label_font)
        self.act_italic.setCheckable(True)
        self.act_italic.setShortcut(QKeySequence.Italic)
        self.act_italic.triggered.connect(
            self._guarded_action("Italic", self.toggle_italic)
        )

        self.act_underline = fmt_menu.addAction("Underline")
        underline_label_font = QFont(fmt_menu.font())
        underline_label_font.setUnderline(True)
        self.act_underline.setFont(underline_label_font)
        self.act_underline.setCheckable(True)
        self.act_underline.setShortcut(QKeySequence.Underline)
        self.act_underline.triggered.connect(
            self._guarded_action("Underline", self.toggle_underline)
        )

        self.act_strike = fmt_menu.addAction("Strike")
        strike_label_font = QFont(fmt_menu.font())
        strike_label_font.setStrikeOut(True)
        self.act_strike.setFont(strike_label_font)
        self.act_strike.setCheckable(True)
        self.act_strike.triggered.connect(
            self._guarded_action("Strikethrough", self.toggle_strikethrough)
        )

        fmt_menu.addSeparator()

        self.act_clear_formatting = fmt_menu.addAction("Clear")
        self.act_clear_formatting.triggered.connect(
            self._guarded_action("Clear formatting", self.clear_formatting)
        )

        self.btn_format.setMenu(fmt_menu)

        # Lists dropdown
        self.btn_lists = QToolButton(self)
        self.btn_lists.setText("Lists")
        self.btn_lists.setPopupMode(QToolButton.InstantPopup)
        self.btn_lists.setAutoRaise(True)

        list_menu = QMenu(self)
        self.act_bullet_list = list_menu.addAction("Bullet list")
        self.act_bullet_list.triggered.connect(
            self._guarded_action("Bullet list", self.insert_bullet_list)
        )
        self.act_number_list = list_menu.addAction("Numbered list")
        self.act_number_list.triggered.connect(
            self._guarded_action("Numbered list", self.insert_number_list)
        )

        self.btn_lists.setMenu(list_menu)

        # Spacing dropdown (browser-matching export)
        self.btn_spacing = QToolButton(self)
        self.btn_spacing.setText("Spacing")
        self.btn_spacing.setPopupMode(QToolButton.InstantPopup)
        self.btn_spacing.setAutoRaise(True)

        sp_menu = QMenu(self)
        self.spacing_action_group = QActionGroup(self)
        self.spacing_action_group.setExclusive(True)
        self.spacing_actions: dict[float, QAction] = {}
        for label, multiplier in (
            ("Single (1.0)", 1.0),
            ("1.15", 1.15),
            ("1.5", 1.5),
            ("Double (2.0)", 2.0),
        ):
            action = sp_menu.addAction(label)
            action.setCheckable(True)
            self.spacing_action_group.addAction(action)
            action.triggered.connect(
                self._guarded_action(
                    f"Line spacing {multiplier:g}",
                    lambda value=multiplier: self.set_line_spacing(value),
                )
            )
            self.spacing_actions[multiplier] = action
        sp_menu.addSeparator()
        for label, multiplier in (("2.5", 2.5), ("3.0", 3.0)):
            action = sp_menu.addAction(label)
            action.setCheckable(True)
            self.spacing_action_group.addAction(action)
            action.triggered.connect(
                self._guarded_action(
                    f"Line spacing {multiplier:g}",
                    lambda value=multiplier: self.set_line_spacing(value),
                )
            )
            self.spacing_actions[multiplier] = action

        self.btn_spacing.setMenu(sp_menu)

        # Align dropdown
        self.btn_align = QToolButton(self)
        self.btn_align.setText("Align")
        self.btn_align.setPopupMode(QToolButton.InstantPopup)
        self.btn_align.setAutoRaise(True)

        menu_align = QMenu(self)
        self.alignment_action_group = QActionGroup(self)
        self.alignment_action_group.setExclusive(True)
        self.alignment_actions: dict[str, QAction] = {}
        for label, alignment in (
            ("Left", Qt.AlignLeft),
            ("Center", Qt.AlignCenter),
            ("Right", Qt.AlignRight),
        ):
            action = menu_align.addAction(label)
            action.setCheckable(True)
            self.alignment_action_group.addAction(action)
            action.triggered.connect(
                self._guarded_action(
                    f"Align {label.lower()}",
                    lambda value=alignment: self.editor.setAlignment(value),
                )
            )
            self.alignment_actions[label.casefold()] = action
        self.btn_align.setMenu(menu_align)

        # Color
        self.act_color = QAction("Color", self)
        self.act_color.triggered.connect(
            self._guarded_action("Text color", self.choose_color)
        )
        color_button_host = QtWidgets.QWidget(self)
        color_button_layout = QHBoxLayout(color_button_host)
        color_button_layout.setContentsMargins(0, 0, 0, 0)
        color_button_layout.setSpacing(0)
        self.btn_color = QToolButton(color_button_host)
        self.btn_color.setObjectName("textColorButton")
        self.btn_color.setAccessibleName("Text color")
        self.btn_color.setAutoRaise(True)
        self.btn_color.setFixedSize(96, 38)
        self.btn_color.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.btn_color.clicked.connect(self.act_color.trigger)
        color_button_layout.addWidget(self.btn_color)
        self._active_text_color = QColor(self.last_color)
        self._text_color_indicator_mixed = False
        self._update_text_color_indicator(self._active_text_color)

        self.btn_links = QToolButton(self)
        self.btn_links.setText("🔗")
        self.btn_links.setFont(_toolbar_symbol_font(self.btn_links.font(), "🔗"))
        self.btn_links.setAccessibleName("Add/edit link")
        self.btn_links.setMinimumSize(44, 36)
        self.btn_links.setPopupMode(QToolButton.InstantPopup)
        self.btn_links.setAutoRaise(True)

        link_menu = QMenu(self)
        self.act_add_edit_link = link_menu.addAction("Add / Edit Link")
        self.act_add_edit_link.setShortcut(QKeySequence("Ctrl+K"))
        self.act_add_edit_link.triggered.connect(
            self._guarded_action("Add or edit link", self.add_or_edit_link)
        )
        self.act_remove_link = link_menu.addAction("Remove Link")
        self.act_remove_link.setShortcut(QKeySequence("Ctrl+Shift+K"))
        self.act_remove_link.triggered.connect(
            self._guarded_action("Remove link", self.remove_link)
        )
        self.btn_links.setToolTip(
            f"Add/edit link ({self.act_add_edit_link.shortcut().toString(QKeySequence.NativeText)})"
        )
        link_metrics = link_menu.fontMetrics()
        menu_style = link_menu.style()
        menu_chrome = (
            2 * menu_style.pixelMetric(QtWidgets.QStyle.PM_MenuHMargin, None, link_menu)
            + 2 * menu_style.pixelMetric(QtWidgets.QStyle.PM_MenuPanelWidth, None, link_menu)
            + menu_style.pixelMetric(QtWidgets.QStyle.PM_SmallIconSize, None, link_menu)
        )
        shortcut_gap = link_metrics.horizontalAdvance("    ")
        link_menu.setMinimumWidth(max(
            link_menu.sizeHint().width() + shortcut_gap,
            *(
                link_metrics.horizontalAdvance(action.text())
                + link_metrics.horizontalAdvance(
                    action.shortcut().toString(QKeySequence.NativeText)
                ) + shortcut_gap + menu_chrome
                for action in (self.act_add_edit_link, self.act_remove_link)
            ),
        ))
        self.btn_links.setMenu(link_menu)
        self.toolbar.addWidget(self.btn_links)
        self.addAction(self.act_add_edit_link)
        self.addAction(self.act_remove_link)
        self.editor.cursorPositionChanged.connect(self._update_link_actions)
        self.editor.selectionChanged.connect(self._update_link_actions)
        self._update_link_actions()

        self.toolbar.addSeparator()

        self.btn_ultralink = QToolButton(self)
        self.btn_ultralink.setObjectName("ultralinkButton")
        self.btn_ultralink.setText("U")
        self.btn_ultralink.setToolTip(
            "Add or edit an Ultra Link tooltip on selected text"
        )
        self.btn_ultralink.setMinimumSize(44, 36)
        self.btn_ultralink.clicked.connect(
            self._guarded_action("Ultralink", self.open_ultralink_dialog)
        )
        self.toolbar.addWidget(self.btn_ultralink)

        self.btn_ultralink_color = QToolButton(self)
        self.btn_ultralink_color.setObjectName("ultralinkColorButton")
        self.btn_ultralink_color.setText("")
        self.btn_ultralink_color.setAccessibleName("Default Ultra Link Color")
        self.btn_ultralink_color.setToolTip(
            "Choose the application-wide default Ultra Link color"
        )
        self.btn_ultralink_color.setFixedSize(30, 30)
        self.btn_ultralink_color.clicked.connect(
            self._guarded_action(
                "Ultra Link color",
                self.choose_ultralink_color,
            )
        )
        self.toolbar.addWidget(self.btn_ultralink_color)
        self.editor.cursorPositionChanged.connect(
            self._update_ultralink_action
        )
        self.editor.selectionChanged.connect(self._update_ultralink_action)
        self._update_ultralink_button_style()
        self._update_ultralink_action()

        self.toolbar.addSeparator()

        # Undo / Redo / Find
        self.act_undo = QAction("↶", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(
            self._guarded_action("Undo", self.editor.undo)
        )
        self.act_undo.setEnabled(self.editor.document().isUndoAvailable())
        self.editor.document().undoAvailable.connect(self.act_undo.setEnabled)
        self.toolbar.addAction(self.act_undo)

        self.act_redo = QAction("↷", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(
            self._guarded_action("Redo", self.editor.redo)
        )
        self.act_redo.setEnabled(self.editor.document().isRedoAvailable())
        self.editor.document().redoAvailable.connect(self.act_redo.setEnabled)
        self.toolbar.addAction(self.act_redo)

        self.toolbar.addSeparator()

        self.act_find = QAction("🔍", self)
        self.act_find.setShortcut(QKeySequence.Find)
        self.act_find.triggered.connect(
            self._guarded_action("Find", self.open_find_replace)
        )
        self.toolbar.addAction(self.act_find)
        for action, label in (
            (self.act_undo, "Undo"),
            (self.act_redo, "Redo"),
            (self.act_find, "Find and replace"),
        ):
            shortcut = action.shortcut().toString(QKeySequence.NativeText)
            tooltip = f"{label} ({shortcut})"
            action.setToolTip(tooltip)
            action.setStatusTip(tooltip)
            widget = self.toolbar.widgetForAction(action)
            widget.setAccessibleName(label)
            widget.setToolTip(tooltip)
            widget.setFont(_toolbar_symbol_font(widget.font(), action.text()))
            widget.setMinimumWidth(38)

        # Font family + size
        self.font_combo = QFontComboBox()
        self.font_combo.setMinimumWidth(180)
        self.font_combo.setMaximumWidth(300)
        self.font_combo.setAccessibleName("Font family")
        self.font_combo.setToolTip("Choose the font for selected text or new typing")
        self.font_combo.currentFontChanged.connect(self._font_family_changed)

        self.font_size_spin = FontSizeSpinBox()
        self.font_size_spin.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
        self.font_size_spin.setMinimumSize(92, 34)
        self.font_size_spin.setSingleStep(FONT_SIZE_STEP)
        self.font_size_spin.setValue(DEFAULT_FONT_SIZE)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.setKeyboardTracking(False)
        self.font_size_spin.setAccelerated(True)
        self.font_size_spin.setAccessibleName("Font size in points")
        self.font_size_spin.setToolTip(
            "Enter an exact point size or use the up and down arrows"
        )
        self.font_size_spin.setStyleSheet(
            "QSpinBox{padding-right:26px;}"
            "QSpinBox::up-button,QSpinBox::down-button{width:24px;}"
        )
        self.font_size_spin.valueChanged.connect(self._font_size_changed)

        self.act_font_size_down = QAction("Decrease font size", self)
        self.act_font_size_down.setShortcut(QKeySequence("Ctrl+["))
        self.act_font_size_down.triggered.connect(
            self._guarded_action(
                "Decrease font size",
                lambda: self.adjust_font_size(-FONT_SIZE_STEP),
            )
        )
        self.addAction(self.act_font_size_down)

        self.act_font_size_up = QAction("Increase font size", self)
        self.act_font_size_up.setShortcut(QKeySequence("Ctrl+]"))
        self.act_font_size_up.triggered.connect(
            self._guarded_action(
                "Increase font size",
                lambda: self.adjust_font_size(FONT_SIZE_STEP),
            )
        )
        self.addAction(self.act_font_size_up)

        self.font_controls = QtWidgets.QFrame(self)
        self.font_controls.setObjectName("fontControls")
        font_layout = QHBoxLayout(self.font_controls)
        font_layout.setContentsMargins(8, 4, 8, 4)
        font_layout.setSpacing(6)
        self._build_named_style_menu()
        font_layout.addWidget(self.btn_named_style)
        font_layout.addSpacing(8)
        font_label = QLabel("Font", self.font_controls)
        font_label.setObjectName("fontControlsLabel")
        size_label = QLabel("Size", self.font_controls)
        size_label.setObjectName("fontControlsLabel")
        font_layout.addWidget(font_label)
        font_layout.addWidget(self.font_combo, 1)
        font_layout.addSpacing(8)
        font_layout.addWidget(size_label)
        font_layout.addWidget(self.font_size_spin)

        character_separator = QtWidgets.QFrame(self.font_controls)
        character_separator.setObjectName("fontControlsSeparator")
        character_separator.setFrameShape(QtWidgets.QFrame.VLine)
        character_separator.setFrameShadow(QtWidgets.QFrame.Plain)
        font_layout.addWidget(character_separator)
        font_layout.addWidget(self.btn_format)
        font_layout.addWidget(color_button_host)

        paragraph_separator = QtWidgets.QFrame(self.font_controls)
        paragraph_separator.setObjectName("fontControlsSeparator")
        paragraph_separator.setFrameShape(QtWidgets.QFrame.VLine)
        paragraph_separator.setFrameShadow(QtWidgets.QFrame.Plain)
        font_layout.addWidget(paragraph_separator)
        font_layout.addWidget(self.btn_align)
        font_layout.addWidget(self.btn_lists)
        font_layout.addWidget(self.btn_spacing)
        font_layout.addStretch(1)
        root_layout = self.layout()
        if isinstance(root_layout, QVBoxLayout):
            self.font_controls_scroll = QtWidgets.QScrollArea(self)
            self.font_controls_scroll.setObjectName("FontControlsScroll")
            self.font_controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.font_controls_scroll.setWidgetResizable(True)
            self.font_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.font_controls_scroll.setWidget(self.font_controls)
            root_layout.insertWidget(1, self.font_controls_scroll)
            self.font_controls.installEventFilter(self)
            self._update_font_controls_height()
        self._sync_font_size_actions()

    def _update_font_controls_height(self) -> None:
        scroll = getattr(self, "font_controls_scroll", None)
        if scroll is not None:
            scroll.setFixedHeight(
                self.font_controls.sizeHint().height()
                + scroll.horizontalScrollBar().sizeHint().height()
            )

    def _build_named_style_menu(self) -> None:
        self.btn_named_style = QToolButton(self.font_controls)
        self.btn_named_style.setObjectName("namedTextStyleButton")
        self.btn_named_style.setAccessibleName("Named text style")
        self.btn_named_style.setText("Normal text")
        self._named_style_selector_base_font = QFont(self.btn_named_style.font())
        self.btn_named_style.setPopupMode(QToolButton.InstantPopup)
        self.btn_named_style.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_named_style.setMinimumWidth(145)
        self.btn_named_style.setAutoRaise(True)
        self.named_style_menu = StyleSelectionMenu(self.btn_named_style)
        self.named_style_menu.setToolTipsVisible(True)
        self._style_update_actions: dict[str, QAction] = {}
        for key in STYLE_KEYS:
            label = STYLE_LABELS[key]
            submenu = add_style_preview_submenu(self.named_style_menu, label)
            self.named_style_menu.actions()[-1].triggered.connect(
                self._guarded_action(
                    f"Apply {label}", lambda key=key: self._apply_named_style(key),
                )
            )
            submenu.setToolTipsVisible(True)
            apply_action = submenu.addAction(f"Apply {label}")
            apply_action.triggered.connect(
                self._guarded_action(
                    f"Apply {label}",
                    lambda key=key: self._apply_named_style(key),
                )
            )
            update_action = submenu.addAction(f"Update {label} to match")
            update_action.triggered.connect(
                self._guarded_action(
                    f"Update {label}",
                    lambda key=key: self._update_named_style(key),
                )
            )
            self._style_update_actions[key] = update_action

        row_action = QWidgetAction(self.named_style_menu)
        row = QtWidgets.QWidget(self.named_style_menu)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(5, 5, 5, 5)
        row_layout.setSpacing(5)
        self._style_slot_buttons: dict[int, QPushButton] = {}
        for slot in (1, 2, 3):
            button = QPushButton(f"Change Style to\nStyle {slot}", row)
            button.setAutoDefault(False)
            button.setMinimumWidth(79)
            button.setAccessibleName(f"Change Style to Style {slot}")
            button.clicked.connect(
                self._guarded_action(
                    f"Load Style {slot}",
                    lambda slot=slot: self._load_style_slot(slot),
                )
            )
            row_layout.addWidget(button)
            self._style_slot_buttons[slot] = button
        row_action.setDefaultWidget(row)
        self.named_style_menu.addAction(row_action)
        self.named_style_menu.addSeparator()
        options = self.named_style_menu.addMenu("Options")
        for slot in (1, 2, 3):
            action = options.addAction(f"Save current style set to Style {slot}")
            action.triggered.connect(
                self._guarded_action(
                    f"Save Style {slot}",
                    lambda slot=slot: self._save_style_slot(slot),
                )
            )
        self.named_style_menu.aboutToShow.connect(self._refresh_named_style_menu)
        self.btn_named_style.setMenu(self.named_style_menu)
        self._sync_named_style_selector()

    def _sync_named_style_selector(self) -> None:
        if not hasattr(self, "btn_named_style"):
            return
        selected = self.named_styles.selected_style()
        self.btn_named_style.setText(
            STYLE_LABELS[selected]
            if selected in STYLE_LABELS
            else "Mixed styles" if selected == "mixed" else "Custom"
        )
        definition = (
            self.named_styles.active_set.get(selected)
            if selected in STYLE_LABELS else None
        )
        if definition != getattr(self, "_named_style_selector_preview", None):
            self._named_style_selector_preview = definition
            if definition is None:
                self.btn_named_style.setFont(self._named_style_selector_base_font)
                self.btn_named_style.setStyleSheet("")
            else:
                font = preview_font(
                    definition, self._named_style_selector_base_font
                )
                self.btn_named_style.setFont(font)
                self.btn_named_style.setStyleSheet(
                    "QToolButton#namedTextStyleButton, "
                    "QToolButton#namedTextStyleButton:hover "
                    f"{{ color: {definition.font_color}; {preview_font_stylesheet(font)} }}"
                )

    def _refresh_named_style_menu(self) -> None:
        for key, action in zip(STYLE_KEYS, self.named_style_menu.actions()[:6]):
            set_action_style_preview(
                action, self.named_styles.active_set.get(key)
            )
        can_update = self.named_styles.can_sample()
        for action in self._style_update_actions.values():
            action.setEnabled(can_update)
            action.setToolTip(
                "Copy formatting from the first selected character and paragraph."
                if can_update else
                "Select uniformly formatted text or place the cursor in representative text."
            )
        for slot, button in self._style_slot_buttons.items():
            try:
                saved_set = self._style_slots.load(slot)
                occupied = saved_set is not None
            except InvalidStyleSetError:
                saved_set = None
                occupied = False
                button.setToolTip("Saved style set is invalid or unsupported.")
            else:
                button.setToolTip(
                    f"Change Style to Style {slot}" if occupied
                    else "Empty — save the current style set through Options."
                )
            set_slot_style_preview(button, saved_set)
            button.setEnabled(occupied)

    def _apply_named_style(self, key: str) -> None:
        self.named_styles.apply_style(key)
        self._sync_current_format()
        self._schedule_autosave()
        self.editor.setFocus(Qt.OtherFocusReason)

    def _update_named_style(self, key: str) -> None:
        self.named_styles.update_style(key)
        self._sync_current_format()
        self._refresh_named_style_menu()
        self._schedule_autosave()
        self.editor.setFocus(Qt.OtherFocusReason)

    def _load_style_slot(self, slot: int) -> None:
        style_set = self._style_slots.load(slot)
        if style_set is None:
            return
        self.named_styles.load_set(style_set)
        self.named_style_menu.hide()
        self._sync_current_format()
        self._refresh_named_style_menu()
        self._schedule_autosave()
        self.editor.setFocus(Qt.OtherFocusReason)

    def _save_style_slot(self, slot: int) -> None:
        active = self.named_styles.active_set
        try:
            self._style_slots.save(slot, active)
        except StyleSetOverwriteRequired:
            confirmation = LetterSmithConfirmationDialog(
                self,
                title=f"Replace Style {slot}?",
                question=f"Replace the saved Style {slot} set with the current styles?",
                primary_text="Replace",
                secondary_text="Keep Saved Set",
                secondary_accepts=True,
                cancel_text="Cancel",
                click_outside_dismiss=False,
                width=500,
            )
            confirmation.exec()
            if confirmation.choice != "primary":
                return
            self._style_slots.save(slot, active, overwrite=True)
        self._refresh_named_style_menu()


    def _apply_styles(self) -> None:
        self.setStyleSheet(
            "QDialog{background:#121212;}"
            "QToolBar{background:#161616;border:1px solid #242424;border-radius:6px;margin:2px;padding:2px;}"
            "QToolBar::separator{background:#454545;width:1px;margin:7px 6px;}"
            "QFrame#LetterEditorSurface{border:1px solid rgba(255,255,255,.16);border-radius:10px;}"
            "QTextEdit#EditorTextArea{background:transparent;border:none;padding:0;selection-background-color:#c9a86a;}"
            "QLabel{color:#bbb;}"
            "QLabel#readabilityIndicator{padding:2px 7px;border:1px solid rgba(255,255,255,.18);border-radius:8px;color:#b9c7d4;background:rgba(15,24,32,.72);font-size:9pt;}"
            "QLabel#readabilityIndicator[readabilityState='good']{color:#9ff0bd;border-color:rgba(91,214,139,.55);background:rgba(18,62,39,.72);}"
            "QLabel#readabilityIndicator[readabilityState='reduced']{color:#f4d98a;border-color:rgba(227,185,78,.55);background:rgba(69,53,17,.72);}"
            "QLabel#readabilityIndicator[readabilityState='low']{color:#ffabb5;border-color:rgba(237,89,109,.65);background:rgba(75,24,33,.78);}"
            "QPushButton{background:#1d1d1d;color:#fff;border:1px solid #00d0ff;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
            "QSpinBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;padding:2px;}"
            "QFontComboBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;}"
            "QMenu{background:#141414;color:#eee;border:1px solid #2a2a2a;}"
            "QToolButton{color:#e6e6e6; padding:4px 8px; border:1px solid #2a2a2a; border-radius:6px; background:#101010;}"
            "QToolButton:hover{border-color:#00d0ff;}"
            "QFrame#fontControls{background:#161616;border:1px solid #242424;border-radius:6px;}"
            "QLabel#fontControlsLabel{color:#d7e7ef;font-weight:700;padding:0 2px;}"
            "QFrame#fontControlsSeparator{color:#454545;}"
            "QCheckBox{color:#ddd;}"
        )

    def _restore_geometry(self) -> None:
        geom = self.settings.value(SETTINGS_KEY_GEOMETRY)
        if isinstance(geom, QtCore.QByteArray):
            try:
                self.restoreGeometry(geom)
            except Exception as error:
                self._record_failure("restore editor geometry", error)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        event.ignore()
        self._request_close()

    def reject(self) -> None:
        self._request_close()

    # ──────────────────────────────────────────────────────────────────────
    # Public API + autosave
    # ──────────────────────────────────────────────────────────────────────

    def _language_protected_terms(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (str(self.recipient_name or "").strip(),)
            if value
        )

    def _schedule_language_check(self) -> None:
        if self._closing or self._language_service is None:
            return
        self._language_check_timer.start()

    def _refresh_language_issues(self) -> None:
        if self._closing or self._language_service is None:
            return
        try:
            issues = self._language_service.get_issues(
                self.editor.toPlainText(),
                context="prose",
                protected_terms=self._language_protected_terms(),
            )
            self.editor.set_language_issues(issues)
        except Exception as error:
            self._record_failure("proofread Editor text", error)

    def _apply_safe_language_corrections(self) -> int:
        if self._language_service is None:
            return 0
        edit_cursor = QTextCursor(self.editor.document())
        edit_cursor.beginEditBlock()
        applied = 0
        try:
            for _pass in range(3):
                before = self.editor.toPlainText()
                issues = self._language_service.get_issues(
                    before,
                    context="prose",
                    protected_terms=self._language_protected_terms(),
                )
                safe_issues = tuple(
                    issue
                    for issue in issues
                    if getattr(issue, "replacement", None) is not None
                    and not self.editor.is_language_issue_ignored(issue)
                    and (
                        bool(getattr(issue, "auto_fix", False))
                        or str(getattr(issue, "confidence", "")).casefold()
                        == "high"
                    )
                )
                if not safe_issues:
                    break
                applied += self.editor.apply_language_replacements(
                    safe_issues,
                    edit_cursor=edit_cursor,
                    emit_refresh=False,
                )
                if self.editor.toPlainText() == before:
                    break
        finally:
            edit_cursor.endEditBlock()
        if applied:
            self.editor.language_refresh_requested.emit()
            self._language_check_timer.stop()
            self._schedule_language_check()
        return applied

    def get_edited_html(self) -> str:
        if self._discard_changes:
            return self._last_persisted_html or self.message_html
        content = self._prepared_html()
        if self._last_persisted_html and content == self._persisted_document_html:
            return self._last_persisted_html
        return content

    def _prepared_html(self) -> str:
        if self._unsupported_style_state:
            raise ValueError(
                "This letter has style data from a newer LetterSmith version. "
                "Update LetterSmith before saving it."
            )
        normalize_ultralinks_in_document(self.editor.document())
        content = self.named_styles.export_html()
        content = self._inject_export_line_spacing_wrapper(content)
        content = embed_lettersmith_style_state(content, self.named_styles.serialize())
        return sanitize_message_html(mark_lettersmith_message_html(content))

    def _sync_message_assets(self) -> None:
        self.project_save_service.copy_workspace_tree(
            self.project_root / GALLERY_DIR / ASSET_SUBDIR,
            Path(GALLERY_DIR) / ASSET_SUBDIR,
        )

    def _schedule_autosave(self) -> None:
        if (
            self._initializing
            or self._save_in_progress
            or self._unsupported_style_state
            or not self.project_state.is_project_ready
        ):
            return
        if not self._autosave_timer.isActive():
            self._autosave_timer.start()

    def _autosave_now(self) -> None:
        if (
            self._initializing
            or self._closing
            or self._save_in_progress
            or self._unsupported_style_state
            or not self.project_state.is_project_ready
        ):
            return
        try:
            content = self._prepared_html()
            changes = message_change_count(self._persisted_document_html, content)
            if changes == 0:
                self._autosave_timer.stop()
                return
            if changes <= SIGNIFICANT_MESSAGE_CHANGES:
                return
            self.project_save_service.save_message(
                content,
                workspace_path=self.message_path,
                reason="autosave",
            )
            self._sync_message_assets()
            self._last_persisted_html = content
            self._persisted_document_html = content
            self._autosave_timer.stop()
            self.autosaved.emit(content)
        except ProjectNotReadyError:
            return
        except Exception as error:
            # Autosave remains quiet in the UI, but the failure is diagnosable.
            self._record_failure("autosave letter", error)
            return

    def _save_document(self) -> bool:
        if self._save_in_progress:
            return False
        if self._unsupported_style_state:
            show_lettersmith_message(
                self,
                "Newer Letter Style Data",
                "Update LetterSmith before saving this letter. Its newer style "
                "data has been preserved.",
            )
            return False
        self._autosave_timer.stop()
        self._save_in_progress = True
        self._set_save_controls_enabled(False)
        try:
            try:
                self._apply_safe_language_corrections()
            except Exception as error:
                self._record_failure("proofread letter before save", error)
            self._autosave_timer.stop()
            content = self._prepared_html()
            normalized_content = content
            if self._last_persisted_html and content == self._persisted_document_html:
                content = self._last_persisted_html
            self.project_save_service.save_message(
                content,
                workspace_path=self.message_path,
                reason="manual-save",
            )
            self._sync_message_assets()
            self._last_persisted_html = content
            self._persisted_document_html = normalized_content
            self.autosaved.emit(content)
            play_ui_sound(UiSound.SAVED)
            return True
        except Exception as error:
            log_path = self._record_failure("save letter", error)
            play_ui_sound(UiSound.ERROR)
            detail = (
                f"\n\nDetails were recorded in:\n{log_path}"
                if log_path is not None
                else ""
            )
            show_lettersmith_message(
                self,
                "Save Error",
                "Could not save the letter. The Editor remains open and your "
                f"changes are preserved.{detail}",
            )
            return False
        finally:
            self._save_in_progress = False
            if not self._closing:
                self._set_save_controls_enabled(True)

    def _set_save_controls_enabled(self, enabled: bool) -> None:
        for button in (
            getattr(self, "btn_save", None),
            getattr(self, "btn_save_close", None),
        ):
            if button is not None:
                button.setEnabled(bool(enabled))

    def _save_only(self) -> None:
        self._save_document()

    def save_and_close(self) -> None:
        if self._save_document():
            self._finish_close()

    def _request_close(self) -> None:
        if self._closing:
            return
        if self._unsupported_style_state:
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Newer Letter Style Data",
                question=(
                    "This letter uses style data from a newer LetterSmith version "
                    "and cannot be saved here. Close without saving changes?"
                ),
                primary_text="Discard and Close",
                secondary_text="Keep Editing",
                secondary_accepts=True,
                cancel_text="Cancel",
                click_outside_dismiss=False,
                width=520,
            )
            confirmation.exec()
            if confirmation.choice == "primary":
                self._discard_changes = True
                self._finish_close()
            return
        try:
            current = self._prepared_html()
        except Exception as error:
            self._record_failure("prepare letter before close", error)
            show_lettersmith_message(
                self,
                "Editor Error",
                "The Editor could not verify the current letter, so it was kept "
                "open. Details were recorded in editor_error.log.",
            )
            return
        if self._last_persisted_html and current == self._persisted_document_html:
            self._finish_close()
            return
        confirmation = LetterSmithConfirmationDialog(
            self,
            title="Unsaved Changes",
            question="Save changes before closing the Editor?",
            primary_text="Save",
            secondary_text="Discard",
            secondary_accepts=True,
            destructive_secondary=True,
            cancel_text="Cancel",
            click_outside_dismiss=False,
            width=500,
        )
        confirmation.exec()
        if confirmation.choice == "primary":
            self.save_and_close()
        elif confirmation.choice == "secondary":
            self._discard_changes = True
            self._finish_close()

    def _finish_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._autosave_timer.stop()
        self._language_check_timer.stop()
        if self._find_dialog is not None:
            self._find_dialog.hide()
        watcher = getattr(self, "_settings_watcher", None)
        if watcher is not None:
            watcher.removePaths(watcher.files())
        try:
            self.settings.setValue(SETTINGS_KEY_GEOMETRY, self.saveGeometry())
        except Exception as error:
            self._record_failure("persist editor geometry", error)
        super().accept()

    def _inject_export_line_spacing_wrapper(self, html: str) -> str:
        if not html:
            return html
        if not self._export_line_spacing:
            return html

        lh = max(0.5, min(4.0, float(self._export_line_spacing)))
        wrapper_open = f'<div class="ls-linewrap" style="line-height:{lh};">'
        wrapper_close = "</div>"

        lower = html.lower()
        body_open = lower.find("<body")
        if body_open == -1:
            return wrapper_open + html + wrapper_close

        body_tag_end = lower.find(">", body_open)
        if body_tag_end == -1:
            return html

        body_close = lower.rfind("</body>")
        if body_close == -1:
            return html[:body_tag_end + 1] + wrapper_open + html[body_tag_end + 1:] + wrapper_close

        inner = html[body_tag_end + 1:body_close]
        if 'class="ls-linewrap"' in inner or "class='ls-linewrap'" in inner:
            def _replace_wrapper(match: re.Match[str]) -> str:
                tag = match.group(0)
                style_match = re.search(r"style=([\"'])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
                if style_match:
                    style = style_match.group(2)
                    if re.search(r"line-height\s*:", style, flags=re.IGNORECASE):
                        style = re.sub(
                            r"line-height\s*:\s*[^;]+",
                            f"line-height:{lh}",
                            style,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        style = style.rstrip().rstrip(";") + f"; line-height:{lh};"
                    return tag[:style_match.start(2)] + style + tag[style_match.end(2):]
                return tag[:-1] + f' style="line-height:{lh};">'

            return re.sub(
                r"<div\b(?=[^>]*class=([\"'])ls-linewrap\1)[^>]*>",
                _replace_wrapper,
                html,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )

        new_inner = wrapper_open + inner + wrapper_close
        return html[:body_tag_end + 1] + new_inner + html[body_close:]

    def _apply_initial_message_defaults(self) -> None:
        """Apply editable defaults without locking later user formatting."""
        font = QFont(MESSAGE_RENDER_FONT_FAMILY, DEFAULT_FONT_SIZE)
        self.editor.document().setDefaultFont(font)
        self.editor.setCurrentFont(font)
        self.editor.setFontPointSize(float(DEFAULT_FONT_SIZE))

        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        try:
            cursor.select(QTextCursor.Document)
            char_format = QTextCharFormat()
            char_format.setFontFamily(MESSAGE_RENDER_FONT_FAMILY)
            char_format.setFontPointSize(float(DEFAULT_FONT_SIZE))
            cursor.mergeCharFormat(char_format)

            block = self.editor.document().begin()
            while block.isValid():
                block_cursor = QTextCursor(block)
                block_format = block_cursor.blockFormat()
                block_format.setAlignment(Qt.AlignCenter)
                block_format.setLineHeight(200.0, QTextBlockFormat.ProportionalHeight.value)
                block_cursor.setBlockFormat(block_format)
                block = block.next()
        finally:
            cursor.endEditBlock()

        end_cursor = self.editor.textCursor()
        end_cursor.movePosition(QTextCursor.End)
        end_block_format = end_cursor.blockFormat()
        end_block_format.setAlignment(Qt.AlignCenter)
        end_block_format.setLineHeight(200.0, QTextBlockFormat.ProportionalHeight.value)
        end_cursor.setBlockFormat(end_block_format)
        end_char_format = end_cursor.charFormat()
        end_char_format.setFontFamily(MESSAGE_RENDER_FONT_FAMILY)
        end_char_format.setFontPointSize(float(DEFAULT_FONT_SIZE))
        end_cursor.setCharFormat(end_char_format)
        self.editor.setTextCursor(end_cursor)
        self.editor.setAlignment(Qt.AlignCenter)

        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(font)
        self.font_combo.blockSignals(False)
        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(DEFAULT_FONT_SIZE)
        self.font_size_spin.blockSignals(False)

    # ──────────────────────────────────────────────────────────────────────
    # Commands
    # ──────────────────────────────────────────────────────────────────────

    def insert_salutation(self) -> None:
        active_cursor = self.editor.textCursor()
        active_block_format = QTextBlockFormat(active_cursor.blockFormat())
        self.named_styles.insert_salutation(
            f"Dear {self.recipient_name},", active_block_format
        )

    def open_find_replace(self) -> None:
        if self._find_dialog is None:
            self._find_dialog = FindReplaceDialog(self)
        self._find_dialog.show()
        place_window_on_launcher(self._find_dialog)
        self._find_dialog.raise_()
        self._find_dialog.activateWindow()
        self._find_dialog.find_input.setFocus(Qt.OtherFocusReason)
        self._find_dialog.find_input.selectAll()

    def _anchor_cursor_at_position(self, position: int) -> Optional[QTextCursor]:
        """Return the complete link at a document position, if one exists."""
        document = self.editor.document()
        spans: list[tuple[int, int, str]] = []
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    href = fmt.anchorHref() if fmt.isAnchor() else ""
                    if href:
                        spans.append(
                            (
                                fragment.position(),
                                fragment.position() + fragment.length(),
                                href,
                            )
                        )
                iterator += 1
            block = block.next()

        match_index: Optional[int] = None
        for index, (start, end, _href) in enumerate(spans):
            if start <= position < end or (
                position > 0 and start <= position - 1 < end
            ):
                match_index = index
                break
        if match_index is None:
            return None

        start, end, href = spans[match_index]
        left = match_index - 1
        while left >= 0 and spans[left][1] == start and spans[left][2] == href:
            start = spans[left][0]
            left -= 1
        right = match_index + 1
        while right < len(spans) and spans[right][0] == end and spans[right][2] == href:
            end = spans[right][1]
            right += 1

        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        return cursor

    def _link_target_cursor(self) -> Optional[QTextCursor]:
        cursor = QTextCursor(self.editor.textCursor())
        if cursor.hasSelection():
            return cursor

        anchor_cursor = self._anchor_cursor_at_position(cursor.position())
        if anchor_cursor is not None:
            href = anchor_cursor.charFormat().anchorHref()
            return None if is_ultralink_href(href) else anchor_cursor

        cursor.select(QTextCursor.WordUnderCursor)
        return cursor if cursor.hasSelection() else None

    def _current_link_href(self) -> str:
        cursor = self.editor.textCursor()
        anchor_cursor = self._anchor_cursor_at_position(cursor.position())
        if anchor_cursor is not None:
            href = anchor_cursor.charFormat().anchorHref()
            return "" if is_ultralink_href(href) else href

        if not cursor.hasSelection():
            fmt = self.editor.currentCharFormat()
            href = fmt.anchorHref() if fmt.isAnchor() else ""
            return "" if is_ultralink_href(href) else href

        hrefs: set[str] = set()
        document = self.editor.document()
        position = cursor.selectionStart()
        while position < cursor.selectionEnd():
            probe = QTextCursor(document)
            probe.setPosition(position)
            probe.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
            fmt = probe.charFormat()
            href = fmt.anchorHref() if fmt.isAnchor() else ""
            if href and not is_ultralink_href(href):
                hrefs.add(href)
            position += 1
        return next(iter(hrefs)) if len(hrefs) == 1 else ""

    def _update_link_actions(self) -> None:
        target = self._link_target_cursor()
        href = self._current_link_href()
        self.act_add_edit_link.setEnabled(target is not None)
        self.act_remove_link.setEnabled(bool(href))

    def add_or_edit_link(self) -> None:
        cursor = self._link_target_cursor()
        if cursor is None:
            show_lettersmith_message(
                self,
                "Link",
                "Select text or place the cursor on a word first.",
            )
            return

        current_href = self._current_link_href() or "https://"
        href, accepted = LetterSmithInputDialog.get_text(
            self,
            "Add or Edit Link",
            "URL:",
            text=current_href,
            accept_text="Apply",
        )
        if not accepted:
            return

        href = href.strip()
        if not href:
            return
        if "://" not in href and not href.casefold().startswith("mailto:"):
            href = f"https://{href}"

        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(href)
        fmt.setFontUnderline(True)
        self.editor.setTextCursor(cursor)
        self._apply_char_format(fmt)
        self._update_link_actions()
        self._update_ultralink_action()

    def remove_link(self) -> None:
        cursor = self._link_target_cursor()
        if cursor is None or not self._current_link_href():
            return

        fmt = QTextCharFormat()
        fmt.setAnchor(False)
        fmt.setAnchorHref("")
        self.editor.setTextCursor(cursor)
        self._apply_char_format(fmt)
        self._update_link_actions()
        self._update_ultralink_action()

    def _ultralink_target_cursor(self) -> Optional[QTextCursor]:
        cursor = QTextCursor(self.editor.textCursor())
        if not cursor.hasSelection():
            anchor = self._anchor_cursor_at_position(cursor.position())
            if (
                anchor is not None
                and is_ultralink_href(anchor.charFormat().anchorHref())
            ):
                return anchor
            return None

        start, end = cursor.selectionStart(), cursor.selectionEnd()
        matches: dict[tuple[int, int], QTextCursor] = {}
        document = self.editor.document()
        block = document.findBlock(start)
        while block.isValid() and block.position() < end:
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    fragment_start = fragment.position()
                    fragment_end = fragment_start + fragment.length()
                    if fragment_start < end and fragment_end > start:
                        fmt = fragment.charFormat()
                        if (
                            fmt.isAnchor()
                            and is_ultralink_href(fmt.anchorHref())
                        ):
                            anchor = self._anchor_cursor_at_position(
                                fragment_start
                            )
                            if anchor is not None:
                                key = (
                                    anchor.selectionStart(),
                                    anchor.selectionEnd(),
                                )
                                matches[key] = anchor
                iterator += 1
            block = block.next()
        return next(iter(matches.values())) if len(matches) == 1 else None

    def _transform_cursor_formats(
        self,
        cursor: QTextCursor,
        transform: Callable[[QTextCharFormat], None],
    ) -> None:
        self._transform_cursors_formats(
            [cursor],
            transform,
            restore_cursor=cursor,
        )

    def _transform_cursors_formats(
        self,
        cursors: list[QTextCursor],
        transform: Callable[[QTextCharFormat], None],
        *,
        restore_cursor: Optional[QTextCursor] = None,
    ) -> None:
        ranges = [
            (cursor.selectionStart(), cursor.selectionEnd())
            for cursor in cursors
            if cursor.selectionStart() < cursor.selectionEnd()
        ]
        if not ranges:
            return

        document = self.editor.document()
        spans: list[tuple[int, int, QTextCharFormat]] = []
        for start, end in ranges:
            block = document.findBlock(start)
            while block.isValid() and block.position() < end:
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid():
                        fragment_start = fragment.position()
                        fragment_end = fragment_start + fragment.length()
                        selected_start = max(start, fragment_start)
                        selected_end = min(end, fragment_end)
                        if selected_start < selected_end:
                            spans.append(
                                (
                                    selected_start,
                                    selected_end,
                                    QTextCharFormat(fragment.charFormat()),
                                )
                            )
                    iterator += 1
                block = block.next()

        work = QTextCursor(document)
        work.beginEditBlock()
        try:
            for selected_start, selected_end, char_format in spans:
                transform(char_format)
                work.setPosition(selected_start)
                work.setPosition(selected_end, QTextCursor.KeepAnchor)
                work.setCharFormat(char_format)
        finally:
            work.endEditBlock()

        if restore_cursor is not None:
            restored = QTextCursor(document)
            restored.setPosition(restore_cursor.selectionStart())
            restored.setPosition(
                restore_cursor.selectionEnd(),
                QTextCursor.KeepAnchor,
            )
            self.editor.setTextCursor(restored)

    def _update_ultralink_button_style(self) -> None:
        color = self.ultralink_color.name()
        self.btn_ultralink.setStyleSheet(
            "QToolButton#ultralinkButton{"
            f"color:{color};font-size:20px;font-weight:700;"
            "padding:2px 10px;border:1px solid #2a2a2a;border-radius:6px;"
            "background:#101010;}"
            "QToolButton#ultralinkButton:hover{border-color:#00d0ff;}"
        )
        self.btn_ultralink_color.setStyleSheet(
            "QToolButton#ultralinkColorButton{"
            f"background:{color};"
            "border:2px solid #e6e6e6;border-radius:5px;padding:0;}"
            "QToolButton#ultralinkColorButton:hover{border-color:#00d0ff;}"
        )

    def _update_ultralink_action(self) -> None:
        cursor = self.editor.textCursor()
        contains_standard_link = (
            cursor.hasSelection()
            and self._selection_contains_standard_link(cursor)
        )
        has_target = (
            (cursor.hasSelection() and not contains_standard_link)
            or self._ultralink_target_cursor() is not None
        )
        if contains_standard_link:
            instruction = "Remove the web link before creating an Ultra Link"
        elif has_target:
            instruction = "Add or edit an Ultra Link tooltip"
        else:
            instruction = "Select text to create an Ultra Link"
        self.btn_ultralink.setEnabled(has_target)
        self.btn_ultralink.setToolTip(
            f"{instruction}. New color: {self.ultralink_color.name()}"
        )

    def choose_ultralink_color(self) -> None:
        color = QColorDialog.getColor(
            self.ultralink_color,
            self,
            "Default Ultra Link Color Picker",
        )
        if not color.isValid():
            return
        self._persist_ultralink_color(color)

    def _persist_ultralink_color(self, color: QColor) -> None:
        self.ultralink_color = color
        self.settings.setValue(SETTINGS_KEY_ULTRALINK_COLOR, color)
        self.settings.sync()
        self._update_ultralink_button_style()
        self._update_ultralink_action()

    def open_ultralink_dialog(self) -> None:
        editor_cursor = QTextCursor(self.editor.textCursor())
        existing_cursor = self._ultralink_target_cursor()
        target = existing_cursor or (
            editor_cursor if editor_cursor.hasSelection() else None
        )
        if target is None:
            play_ui_sound(UiSound.ERROR)
            return
        if self._selection_contains_standard_link(target):
            play_ui_sound(UiSound.ERROR)
            show_lettersmith_message(
                self,
                "Ultra Link",
                "Remove the existing web link before applying an Ultra Link.",
            )
            return

        existing_message = (
            ultralink_message_from_href(
                existing_cursor.charFormat().anchorHref()
            )
            if existing_cursor is not None
            else None
        )
        occurrence_text = target.selectedText()
        dialog = UltralinkDialog(
            existing_message or "",
            selection_text=occurrence_text,
            parent=self,
        )
        dialog.adjustSize()
        dialog.move(
            self.btn_ultralink.mapToGlobal(
                QtCore.QPoint(0, self.btn_ultralink.height())
            )
        )
        if dialog.exec() != QDialog.Accepted:
            return
        applied_color = self.ultralink_color
        if existing_cursor is not None:
            brush = existing_cursor.charFormat().foreground()
            if (
                brush.style() != Qt.BrushStyle.NoBrush
                and brush.color().isValid()
            ):
                applied_color = brush.color()

        applied, skipped = self.apply_ultralink_to_all_occurrences(
            occurrence_text,
            dialog.message_text(),
            color=applied_color,
            restore_cursor=target,
        )
        if not applied:
            self.apply_ultralink(
                target,
                dialog.message_text(),
                apply_default_color=existing_message is None,
            )
            applied = 1

        status = f"Applied Ultra Link to {applied} occurrence"
        status += "" if applied == 1 else "s"
        if skipped:
            status += f"; preserved {skipped} existing web link"
            status += "" if skipped == 1 else "s"
        status += "."
        QtWidgets.QToolTip.showText(
            self.btn_ultralink.mapToGlobal(
                QtCore.QPoint(0, self.btn_ultralink.height())
            ),
            status,
            self.btn_ultralink,
        )

    def apply_ultralink(
        self,
        cursor: QTextCursor,
        message: str,
        *,
        apply_default_color: bool,
    ) -> None:
        href = make_ultralink_href(message)
        color = self.ultralink_color if apply_default_color else None
        self._transform_cursor_formats(
            cursor,
            self._ultralink_format_transform(href, color),
        )
        self._update_link_actions()
        self._update_ultralink_action()

    def apply_ultralink_to_all_occurrences(
        self,
        text: str,
        message: str,
        *,
        color: Optional[QColor] = None,
        restore_cursor: Optional[QTextCursor] = None,
    ) -> tuple[int, int]:
        matches, skipped = self._matching_ultralink_occurrences(text)
        if not matches:
            return 0, skipped

        href = make_ultralink_href(message)
        self._transform_cursors_formats(
            matches,
            self._ultralink_format_transform(
                href,
                color or self.ultralink_color,
            ),
            restore_cursor=restore_cursor or matches[0],
        )
        self._update_link_actions()
        self._update_ultralink_action()
        return len(matches), skipped

    def _matching_ultralink_occurrences(
        self,
        text: str,
    ) -> tuple[list[QTextCursor], int]:
        search_text = text.strip()
        if not search_text or "\u2029" in search_text:
            return [], 0

        document = self.editor.document()
        plain_text = document.toPlainText()
        matches: list[QTextCursor] = []
        skipped = 0
        search_cursor = QTextCursor(document)
        search_cursor.movePosition(QTextCursor.Start)

        while True:
            found = document.find(search_text, search_cursor)
            if found.isNull():
                break

            start = found.selectionStart()
            end = found.selectionEnd()
            if self._is_whole_text_occurrence(
                plain_text,
                start,
                end,
                search_text,
            ):
                if self._selection_contains_standard_link(found):
                    skipped += 1
                else:
                    matches.append(QTextCursor(found))

            next_position = max(end, search_cursor.position() + 1)
            search_cursor.setPosition(next_position)

        return matches, skipped

    @staticmethod
    def _is_whole_text_occurrence(
        plain_text: str,
        start: int,
        end: int,
        search_text: str,
    ) -> bool:
        def is_word_character(value: str) -> bool:
            return value.isalnum() or value == "_"

        if (
            search_text
            and is_word_character(search_text[0])
            and start > 0
            and is_word_character(plain_text[start - 1])
        ):
            return False
        if (
            search_text
            and is_word_character(search_text[-1])
            and end < len(plain_text)
            and is_word_character(plain_text[end])
        ):
            return False
        return True

    def _selection_contains_standard_link(
        self,
        cursor: QTextCursor,
    ) -> bool:
        document = self.editor.document()
        for position in range(
            cursor.selectionStart(),
            cursor.selectionEnd(),
        ):
            probe = QTextCursor(document)
            probe.setPosition(position)
            probe.movePosition(
                QTextCursor.NextCharacter,
                QTextCursor.KeepAnchor,
            )
            char_format = probe.charFormat()
            href = (
                char_format.anchorHref()
                if char_format.isAnchor()
                else ""
            )
            if href and not is_ultralink_href(href):
                return True
        return False

    @staticmethod
    def _ultralink_format_transform(
        href: str,
        color: Optional[QColor],
    ) -> Callable[[QTextCharFormat], None]:
        def transform(char_format: QTextCharFormat) -> None:
            char_format.setAnchor(True)
            char_format.setAnchorHref(href)
            char_format.setFontItalic(True)
            char_format.setFontUnderline(False)
            char_format.setUnderlineStyle(
                QTextCharFormat.UnderlineStyle.NoUnderline
            )
            if color is not None:
                char_format.setForeground(color)

        return transform

    def remove_ultralink(self, cursor: QTextCursor) -> None:
        def transform(char_format: QTextCharFormat) -> None:
            char_format.setAnchor(False)
            char_format.setAnchorHref("")
            char_format.setFontItalic(False)
            char_format.setFontUnderline(False)
            char_format.setUnderlineStyle(
                QTextCharFormat.UnderlineStyle.NoUnderline
            )
            char_format.clearForeground()

        self._transform_cursor_formats(cursor, transform)
        self._update_link_actions()
        self._update_ultralink_action()

    # ──────────────────────────────────────────────────────────────────────
    # Formatting
    # ──────────────────────────────────────────────────────────────────────

    def _apply_char_format(self, fmt: QTextCharFormat) -> None:
        self.named_styles.apply_direct_format(fmt)

    def _toggle_weight(self, wt: int) -> None:
        current = self.editor.currentCharFormat()
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if current.fontWeight() == wt else wt)
        self._apply_char_format(fmt)

    def set_font_family(self, font: QFont) -> None:
        family = font.family().strip()
        if not family:
            return
        fmt = QTextCharFormat()
        fmt.setFontFamilies([family])
        self._apply_char_format(fmt)

    def set_font_size(self, size: int) -> None:
        size_i = int(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, size)))
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(size_i))
        self._apply_char_format(fmt)
        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(size_i)
        self.font_size_spin.blockSignals(False)
        self._sync_font_size_actions()

    def _commit_font_size_input(self) -> None:
        blocker = QtCore.QSignalBlocker(self.font_size_spin)
        self.font_size_spin.interpretText()
        size = self.font_size_spin.value()
        del blocker
        self.set_font_size(size)

    def adjust_font_size(self, delta: int) -> None:
        size = int(self.font_size_spin.value()) + int(delta)
        self.set_font_size(size)

    def _sync_font_size_actions(self) -> None:
        if not hasattr(self, "font_size_spin"):
            return
        size = int(self.font_size_spin.value())
        if hasattr(self, "act_font_size_down"):
            self.act_font_size_down.setEnabled(size > FONT_SIZE_MIN)
        if hasattr(self, "act_font_size_up"):
            self.act_font_size_up.setEnabled(size < FONT_SIZE_MAX)

    def toggle_bold(self) -> None:
        self._toggle_weight(QFont.Bold)

    def toggle_italic(self) -> None:
        current = self.editor.currentCharFormat()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not current.fontItalic())
        self._apply_char_format(fmt)

    def toggle_underline(self) -> None:
        current = self.editor.currentCharFormat()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not current.fontUnderline())
        self._apply_char_format(fmt)

    def toggle_strikethrough(self) -> None:
        current = self.editor.currentCharFormat()
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not current.fontStrikeOut())
        self._apply_char_format(fmt)

    def clear_formatting(self) -> None:
        self.named_styles.clear_direct_format()

    def choose_color(self) -> None:
        initial_color = getattr(self, "_active_text_color", self.last_color)
        color = QColorDialog.getColor(initial_color, parent=self)
        if not color.isValid():
            return
        self.last_color = color
        self._active_text_color = QColor(color)
        try:
            self.settings.setValue(SETTINGS_KEY_COLOR, color)
        except Exception:
            pass

        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._apply_char_format(fmt)
        self._sync_text_color_indicator(self.editor.currentCharFormat())

    @staticmethod
    def _format_foreground_color(
        fmt: QTextCharFormat,
        fallback: QColor,
    ) -> QColor:
        brush = fmt.foreground()
        if (
            brush.style() != Qt.BrushStyle.NoBrush
            and brush.color().isValid()
        ):
            return QColor(brush.color())
        return QColor(fallback)

    def _selected_text_colors(self, cursor: QTextCursor) -> tuple[QColor, ...]:
        colors: dict[int, QColor] = {}
        default_text_color = self.editor.palette().color(
            QtGui.QPalette.ColorRole.Text
        )
        if not default_text_color.isValid():
            default_text_color = QColor(self.last_color)
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = self.editor.document().findBlock(start)
        while block.isValid() and block.position() < end:
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    fragment_start = fragment.position()
                    fragment_end = fragment_start + fragment.length()
                    if fragment_start < end and fragment_end > start:
                        color = self._format_foreground_color(
                            fragment.charFormat(),
                            default_text_color,
                        )
                        colors[color.rgba()] = color
                iterator += 1
            block = block.next()
        return tuple(colors.values())

    def _sync_text_color_indicator(self, fmt: QTextCharFormat) -> None:
        if not hasattr(self, "btn_color"):
            return
        cursor = self.editor.textCursor()
        colors = self._selected_text_colors(cursor) if cursor.hasSelection() else ()
        if len(colors) > 1:
            self._update_text_color_indicator(mixed=True)
            return
        color = (
            colors[0]
            if colors
            else self._format_foreground_color(fmt, self.last_color)
        )
        self._active_text_color = QColor(color)
        self._update_text_color_indicator(color)

    def _update_text_color_indicator(
        self,
        color: Optional[QColor] = None,
        *,
        mixed: bool = False,
    ) -> None:
        label = "COLORS" if mixed else "Color"
        colors = (
            MIXED_TEXT_COLOR_INDICATOR
            if mixed
            else (QColor(color or self.last_color),) * len(label)
        )
        font = QFont(self.btn_color.font())
        font.setBold(True)
        font.setPixelSize(16)
        metrics = QtGui.QFontMetrics(font)
        widths = tuple(metrics.horizontalAdvance(character) for character in label)
        pixmap = QPixmap(sum(widths) + 10, max(28, metrics.height() + 6))
        pixmap.fill(Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setFont(font)
        x = (pixmap.width() - sum(widths)) / 2.0
        baseline = (pixmap.height() + metrics.ascent() - metrics.descent()) / 2.0
        for character, character_color, width in zip(label, colors, widths):
            painter.setPen(character_color)
            painter.drawText(QtCore.QPointF(x, baseline), character)
            x += width
        painter.end()

        self._text_color_indicator_mixed = mixed
        self._text_color_indicator_colors = tuple(
            character_color.name() for character_color in colors
        )
        self.btn_color.setIcon(QtGui.QIcon(pixmap))
        self.btn_color.setIconSize(pixmap.size())
        self.btn_color.setToolTip(
            "Mixed text colors; choose one text color"
            if mixed
            else f"Text color: {colors[0].name()}"
        )

    def insert_bullet_list(self) -> None:
        c = self.editor.textCursor()
        c.beginEditBlock()
        lf = QTextListFormat()
        lf.setStyle(QTextListFormat.ListDisc)
        c.createList(lf)
        c.endEditBlock()

    def insert_number_list(self) -> None:
        c = self.editor.textCursor()
        c.beginEditBlock()
        lf = QTextListFormat()
        lf.setStyle(QTextListFormat.ListDecimal)
        c.createList(lf)
        c.endEditBlock()

    def set_line_spacing(self, multiplier: float) -> None:
        self._export_line_spacing = float(multiplier)

        pct = float(max(0.5, float(multiplier)) * 100.0)
        pct = max(50.0, min(400.0, pct))
        height_type = QTextBlockFormat.ProportionalHeight.value

        cursor = self.editor.textCursor()

        if not cursor.hasSelection():
            bf = cursor.blockFormat()
            bf.setLineHeight(pct, height_type)
            cursor.setBlockFormat(bf)
            self.editor.setTextCursor(cursor)
            return

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        doc = self.editor.document()
        work = QTextCursor(doc)
        work.beginEditBlock()
        try:
            work.setPosition(start)
            blk = work.block()

            work.setPosition(end)
            end_blk = work.block()

            while blk.isValid():
                bc = QTextCursor(blk)
                bf = bc.blockFormat()
                bf.setLineHeight(pct, height_type)
                bc.setBlockFormat(bf)

                if blk == end_blk:
                    break
                blk = blk.next()
        finally:
            work.endEditBlock()

    # ──────────────────────────────────────────────────────────────────────
    # Misc
    # ──────────────────────────────────────────────────────────────────────

    def update_word_count(self) -> None:
        t = self.editor.toPlainText()
        w = len([s for s in t.split() if s.strip()])
        c = len(t)
        self.word_label.setText(f"Words: {w:,}  |  Chars: {c:,}")

    def _sync_format(self, fmt: QTextCharFormat) -> None:
        try:
            self.font_combo.blockSignals(True)
            self.font_size_spin.blockSignals(True)

            self.font_combo.setCurrentFont(fmt.font())

            pt = fmt.fontPointSize()
            if pt and pt > 0:
                sz = int(round(pt))
            else:
                eff = self.editor.fontPointSize()
                sz = int(round(eff)) if eff and eff > 0 else DEFAULT_FONT_SIZE

            self.font_size_spin.setValue(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, sz)))
            self._sync_font_size_actions()

            for action, checked in (
                (getattr(self, "act_bold", None), fmt.fontWeight() >= QFont.Bold),
                (getattr(self, "act_italic", None), fmt.fontItalic()),
                (getattr(self, "act_underline", None), fmt.fontUnderline()),
                (getattr(self, "act_strike", None), fmt.fontStrikeOut()),
            ):
                if action is None:
                    continue
                blocker = QtCore.QSignalBlocker(action)
                action.setChecked(bool(checked))
                del blocker
            self._sync_text_color_indicator(fmt)
        finally:
            self.font_combo.blockSignals(False)
            self.font_size_spin.blockSignals(False)
