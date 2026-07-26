# File: Editor.py
# -*- coding: utf-8 -*-
"""
Letter Smith — Rich Text Editor (dark mode, professional polish)

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
- Live preview painted over wall.png (letter background) or provided pixmap.
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
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSize, QSettings, QMimeData
from PySide6.QtGui import (
    QFont,
    QColor,
    QAction,
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
    QPlainTextEdit,
    QToolBar,
    QFontComboBox,
    QSpinBox,
    QSlider,
    QLabel,
    QColorDialog,
    QPushButton,
    QMessageBox,
    QDialogButtonBox,
    QLineEdit,
    QToolButton,
    QMenu,
    QSplitter,
    QCheckBox,
)

from config import (
    SETTINGS_FILE,
    GALLERY_DIR,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
)

from message_html import ensure_message_html_from_emessage

# ─────────────────────────────────────────────────────────────────────────────
# Constants & Helpers
# ─────────────────────────────────────────────────────────────────────────────

TOOLBAR_ICON_SIZE = QSize(16, 16)
DEFAULT_FONT_SIZE = 16
FONT_SIZE_MIN, FONT_SIZE_MAX = 1, 100
HYPERNOTE_SCHEME = "hypernote:"
HYPERNOTE_DEFAULT_COLOR = QColor("#0563c1")

SETTINGS_ORG = "LetterSmith"
SETTINGS_APP = "Editor"
SETTINGS_KEY_COLOR = "textColor"
SETTINGS_KEY_GEOMETRY = "windowGeometry"
MESSAGE_OVERLAY_PRESET_KEY = "message_overlay_preset"
MESSAGE_OVERLAY_OPACITY_KEY = "message_overlay_opacity"
DEFAULT_MESSAGE_OVERLAY_PRESET = "paper"
DEFAULT_MESSAGE_OVERLAY_OPACITY = 68
MESSAGE_OVERLAY_PRESETS: dict[str, tuple[str, tuple[int, int, int], str]] = {
    "black": ("Black", (0, 0, 0), "#ffffff"),
    "white": ("White", (255, 255, 255), "#221710"),
    "paper": ("Paper", (245, 235, 210), "#221710"),
    "clear": ("Clear", (255, 255, 255), "#221710"),
}

ASSET_SUBDIR = "message_assets"  # under gallery/

FONT_FILE_SUFFIXES = (".ttf", ".otf", ".woff", ".woff2")
BUNDLED_FONT_DIR_CANDIDATES = (
    "gallery/app/fonts",
    "gallery/user/fonts",
    "gallery/fonts",
    "fonts",
    "assets/fonts",
)
COMMON_SYSTEM_FONT_FAMILIES = {
    "arial",
    "calibri",
    "cambria",
    "candara",
    "consolas",
    "courier new",
    "georgia",
    "lucida handwriting",
    "segoe ui",
    "tahoma",
    "times new roman",
    "trebuchet ms",
    "verdana",
}
FONT_STYLE_NAME_TOKENS = {
    "black",
    "bold",
    "book",
    "condensed",
    "demi",
    "extrabold",
    "hairline",
    "heavy",
    "italic",
    "light",
    "medium",
    "regular",
    "semibold",
    "thin",
}


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_name(filename: str) -> str:
    bad = '<>:"/\\|?*\0'
    out = "".join(("_" if ch in bad else ch) for ch in filename)
    out = out.strip().strip(".")
    return out or "asset"


def _font_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _font_file_match_keys(path: Path) -> set[str]:
    stem = path.stem
    raw_parts = [part for part in re.split(r"[\s_\-.]+", stem) if part]
    filtered_parts = [part for part in raw_parts if part.casefold() not in FONT_STYLE_NAME_TOKENS]

    keys = {_font_match_key(stem)}
    if filtered_parts:
        keys.add(_font_match_key(" ".join(filtered_parts)))
    return {key for key in keys if key}


def _hypernote_href(note: str) -> str:
    return HYPERNOTE_SCHEME + quote(note or "", safe="")


def _hypernote_note_from_href(href: str) -> Optional[str]:
    if not href or not href.startswith(HYPERNOTE_SCHEME):
        return None
    raw = href[len(HYPERNOTE_SCHEME):]
    try:
        return unquote(raw)
    except Exception:
        return raw


def _read_json(fp: Path) -> dict:
    try:
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    except Exception:
        return {}


def _coerce_message_overlay_settings(data: dict) -> tuple[str, int]:
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

    return preset, opacity


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

class FindReplaceDialog(QDialog):
    """Find/replace tool for the editor document."""

    def __init__(self, parent: "Editor") -> None:
        super().__init__(parent)
        self.setWindowTitle("Find / Replace")
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._owner = parent
        self._editor: QTextEdit = parent.editor

        self.find_input = QLineEdit(placeholderText="Find…")
        self.replace_input = QLineEdit(placeholderText="Replace with…")
        self.match_case = QCheckBox("Match case")
        self.count_label = QLabel("Matches: 0")

        self.find_btn = QPushButton("Find Next")
        self.count_btn = QPushButton("Count")
        self.replace_btn = QPushButton("Replace")
        self.replace_all_btn = QPushButton("Replace All")
        self.close_btn = QPushButton("Close")

        row = QHBoxLayout()
        row.addWidget(self.find_input, 1)
        row.addWidget(self.replace_input, 1)

        opts = QHBoxLayout()
        opts.addWidget(self.match_case)
        opts.addStretch(1)
        opts.addWidget(self.count_label)

        buttons = QHBoxLayout()
        buttons.addWidget(self.find_btn)
        buttons.addWidget(self.count_btn)
        buttons.addWidget(self.replace_btn)
        buttons.addWidget(self.replace_all_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)

        root = QVBoxLayout(self)
        root.addLayout(row)
        root.addLayout(opts)
        root.addLayout(buttons)

        self.find_input.returnPressed.connect(self.find_next)
        self.replace_input.returnPressed.connect(self.replace_one)
        self.find_btn.clicked.connect(self.find_next)
        self.count_btn.clicked.connect(self.count_matches)
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.close_btn.clicked.connect(self.hide)

        self.find_input.textChanged.connect(lambda *_: self.count_matches(silent=True))
        self.match_case.stateChanged.connect(lambda *_: self.count_matches(silent=True))

        self.setStyleSheet(
            "QDialog{background:#141414;border:1px solid #00d0ff;border-radius:8px;}"
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #2a2a2a;padding:6px;border-radius:4px;}"
            "QLabel,QCheckBox{color:#ddd;}"
            "QPushButton{background:#232323;color:#fff;border:1px solid #00d0ff;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.find_input.setFocus(Qt.OtherFocusReason)
        self.find_input.selectAll()
        self.count_matches(silent=True)

    def _find_options(self) -> QTextDocument.FindFlags:
        flags = QTextDocument.FindFlags()
        if self.match_case.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        return flags

    def _plain_find_flags(self) -> Qt.CaseSensitivity:
        return Qt.CaseSensitive if self.match_case.isChecked() else Qt.CaseInsensitive

    def find_next(self) -> None:
        term = self.find_input.text()
        if not term:
            return

        doc = self._editor.document()
        flags = self._find_options()
        cur = self._editor.textCursor()
        start_cursor = QTextCursor(cur)
        start_cursor.setPosition(cur.selectionEnd() if cur.hasSelection() else cur.position())

        found = doc.find(term, start_cursor, flags)
        if found.isNull():
            top = QTextCursor(doc)
            top.movePosition(QTextCursor.Start)
            found = doc.find(term, top, flags)

        if found.isNull():
            QtWidgets.QApplication.beep()
            self.count_label.setText("Matches: 0")
            return

        self._editor.setTextCursor(found)
        self._editor.setFocus(Qt.OtherFocusReason)
        self.count_matches(silent=True)

    def count_matches(self, *, silent: bool = False) -> int:
        term = self.find_input.text()
        if not term:
            self.count_label.setText("Matches: 0")
            return 0

        text = self._editor.toPlainText()
        count = 0
        pos = 0
        case = self._plain_find_flags()
        while True:
            pos = text.find(term, pos) if case == Qt.CaseSensitive else text.casefold().find(term.casefold(), pos)
            if pos < 0:
                break
            count += 1
            pos += max(1, len(term))

        self.count_label.setText(f"Matches: {count}")
        if not silent and count == 0:
            QtWidgets.QApplication.beep()
        return count

    def replace_one(self) -> None:
        term = self.find_input.text()
        if not term:
            return

        cur = self._editor.textCursor()
        selected = cur.selectedText()
        same = selected == term if self.match_case.isChecked() else selected.casefold() == term.casefold()

        if cur.hasSelection() and same:
            cur.insertText(self.replace_input.text())
            self._editor.setTextCursor(cur)
            self.count_matches(silent=True)
            self.find_next()
            return

        self.find_next()

    def replace_all(self) -> None:
        term = self.find_input.text()
        if not term:
            return

        replacement = self.replace_input.text()
        doc = self._editor.document()
        flags = self._find_options()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        count = 0
        try:
            cursor.movePosition(QTextCursor.Start)
            while True:
                found = doc.find(term, cursor, flags)
                if found.isNull():
                    break
                found.insertText(replacement)
                cursor = QTextCursor(found)
                count += 1
        finally:
            cursor.endEditBlock()

        self.count_label.setText(f"Replaced: {count}")
        if count == 0:
            QtWidgets.QApplication.beep()


# ─────────────────────────────────────────────────────────────────────────────
# RichTextEdit: paste/drop images into <project_root>/gallery/message_assets/
# ─────────────────────────────────────────────────────────────────────────────

class HypernoteDialog(QDialog):
    def __init__(self, note: str = "", *, allow_remove: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hypernote")
        self.setModal(True)
        self.remove_requested = False

        self.note_edit = QPlainTextEdit(self)
        self.note_edit.setPlainText(note or "")
        self.note_edit.setPlaceholderText("Tooltip/note text")
        self.note_edit.setMinimumSize(360, 130)

        buttons = QDialogButtonBox(self)
        buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        if allow_remove:
            self.remove_button = buttons.addButton("Remove", QDialogButtonBox.DestructiveRole)
            self.remove_button.clicked.connect(self._remove)

        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self.note_edit)
        root.addWidget(buttons)

        self.setStyleSheet(
            "QDialog{background:#141414;border:1px solid #00d0ff;border-radius:8px;}"
            "QPlainTextEdit{background:#0f0f0f;color:#eee;border:1px solid #2a2a2a;border-radius:6px;padding:8px;}"
            "QPushButton{background:#232323;color:#fff;border:1px solid #00d0ff;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
        )

    def note_text(self) -> str:
        return self.note_edit.toPlainText().strip()

    def _save(self) -> None:
        if not self.note_text():
            QtWidgets.QApplication.beep()
            self.note_edit.setFocus(Qt.OtherFocusReason)
            return
        self.accept()

    def _remove(self) -> None:
        self.remove_requested = True
        self.accept()


class RichTextEdit(QTextEdit):
    def __init__(self, project_root: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.project_root = Path(project_root)
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)

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

        super().insertFromMimeData(source)


# ─────────────────────────────────────────────────────────────────────────────
# Editor Dialog
# ─────────────────────────────────────────────────────────────────────────────

class Editor(QDialog):
    def __init__(self, message_html: str, preview_pixmap: Optional[QPixmap] = None, parent=None) -> None:
        super().__init__(parent)

        # Resolve project root (authoritative)
        self.project_root = Path(getattr(parent, "project_root", os.getcwd()))

        # Canonical message location (SOURCE OF TRUTH)
        self.message_path = (self.project_root / MESSAGE_HTML_FILE).resolve()
        self.message_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path = (self.project_root / SETTINGS_FILE).resolve()

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        s = _read_json(self.settings_path)
        self.recipient_name = (s.get("recipient_name") or "Friend").strip() or "Friend"
        self.overlay_preset, self.overlay_opacity = _coerce_message_overlay_settings(s)
        self.overlay_buttons: dict[str, QPushButton] = {}

        saved = self.settings.value(SETTINGS_KEY_COLOR, QColor("#eeeeee"))
        self.last_color = saved if isinstance(saved, QColor) else QColor("#eeeeee")

        self.message_html = self._resolve_initial_message_html(message_html)

        # Tracks the most recent spacing selection (used to force identical browser output)
        self._export_line_spacing: Optional[float] = None

        self.setWindowTitle("Letter Smith — Editor")
        self.setModal(True)
        self.resize(1100, 720)

        self._apply_styles()
        self._restore_geometry()
        self._build_ui(preview_pixmap)


    def _apply_default_center_alignment_if_plain(self) -> None:
        """Center plain/default messages without overwriting explicit saved alignment."""
        raw = (self.message_html or "").lower()
        if "text-align" in raw or "align=" in raw:
            return
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        try:
            block = self.editor.document().firstBlock()
            while block.isValid():
                bc = QTextCursor(block)
                bf = bc.blockFormat()
                bf.setAlignment(Qt.AlignCenter)
                bc.setBlockFormat(bf)
                block = block.next()
        finally:
            cursor.endEditBlock()

    def _resolve_initial_message_html(self, message_html: str) -> str:
        """
        Resolve the HTML loaded into the editor.

        If the caller gives usable HTML, use it. If message.html is missing or
        empty, rebuild it from gallery/app/pages/Emessage.docx and load that.
        This keeps the editor openable after Command wipes the message folder.
        """
        incoming = (message_html or "").strip()
        if incoming:
            return message_html

        try:
            ensure_message_html_from_emessage(self.project_root, overwrite=False)
            if self.message_path.is_file():
                loaded = self.message_path.read_text(encoding="utf-8")
                if loaded.strip():
                    return loaded
        except Exception:
            pass

        return "<p></p>"

    def _build_ui(self, preview_pixmap: Optional[QPixmap]) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(TOOLBAR_ICON_SIZE)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        main_layout.addWidget(self.toolbar)

        splitter = QSplitter(Qt.Horizontal)

        self.editor = RichTextEdit(self.project_root)
        self.editor.document().setDefaultStyleSheet("a { text-decoration: none; }")
        self.editor.setHtml(self.message_html)
        self._apply_default_center_alignment_if_plain()
        splitter.addWidget(self.editor)

        # The old second-screen preview was intentionally removed.
        # The right side now holds compact export controls only.
        self.preview = self.editor
        self.overlay_panel = self._build_overlay_panel()
        splitter.addWidget(self.overlay_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([860, 240])
        main_layout.addWidget(splitter, 1)

        self._build_toolbar_actions()

        self.editor.textChanged.connect(self.preview.update)
        self.editor.textChanged.connect(self.update_word_count)
        self.editor.currentCharFormatChanged.connect(self.preview.update)
        self.editor.currentCharFormatChanged.connect(self._sync_format)

        hb = QHBoxLayout()
        self.word_label = QLabel()
        hb.addWidget(self.word_label)
        hb.addStretch()

        self.font_info_button = QToolButton(self)
        self.font_info_button.setObjectName("fontInfoButton")
        self.font_info_button.setText("i")
        self.font_info_button.setCursor(Qt.PointingHandCursor)
        self.font_info_button.setToolTip("Show font export details")
        self.font_info_button.clicked.connect(self.show_font_export_info)
        hb.addWidget(self.font_info_button)

        btn_save = QPushButton("Save")
        btn_save.setShortcut(QKeySequence.Save)
        btn_save.clicked.connect(self.apply_changes)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        hb.addWidget(btn_save)
        hb.addWidget(btn_cancel)
        main_layout.addLayout(hb)

        self.update_word_count()

    def _make_toolbar_button(self, text: str, tooltip: str, callback) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setAutoRaise(False)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(callback)
        return btn

    def _build_toolbar_actions(self) -> None:
        act_sal = QAction("Salutation", self)
        act_sal.triggered.connect(self.insert_salutation)
        self.toolbar.addAction(act_sal)

        self.undo_button = self._make_toolbar_button("↶", "Undo", self.editor.undo)
        self.redo_button = self._make_toolbar_button("↷", "Redo", self.editor.redo)
        self.toolbar.addWidget(self.undo_button)
        self.toolbar.addWidget(self.redo_button)

        self.toolbar.addSeparator()

        self.btn_format = QToolButton(self)
        self.btn_format.setText("Format")
        self.btn_format.setPopupMode(QToolButton.InstantPopup)
        self.btn_format.setAutoRaise(True)

        fmt_menu = QMenu(self)
        a_bold = fmt_menu.addAction("Bold")
        a_bold.setShortcut(QKeySequence.Bold)
        a_bold.triggered.connect(self.toggle_bold)

        a_italic = fmt_menu.addAction("Italic")
        a_italic.setShortcut(QKeySequence.Italic)
        a_italic.triggered.connect(self.toggle_italic)

        a_underline = fmt_menu.addAction("Underline")
        a_underline.setShortcut(QKeySequence.Underline)
        a_underline.triggered.connect(self.toggle_underline)

        a_strike = fmt_menu.addAction("Strike")
        a_strike.triggered.connect(self.toggle_strikethrough)

        fmt_menu.addSeparator()
        a_clear = fmt_menu.addAction("Clear")
        a_clear.triggered.connect(self.clear_formatting)

        self.btn_format.setMenu(fmt_menu)
        self.toolbar.addWidget(self.btn_format)

        self.btn_lists = QToolButton(self)
        self.btn_lists.setText("Lists")
        self.btn_lists.setPopupMode(QToolButton.InstantPopup)
        self.btn_lists.setAutoRaise(True)

        list_menu = QMenu(self)
        list_menu.addAction("Bullet list", self.insert_bullet_list)
        list_menu.addAction("Numbered list", self.insert_number_list)
        self.btn_lists.setMenu(list_menu)
        self.toolbar.addWidget(self.btn_lists)

        self.btn_spacing = QToolButton(self)
        self.btn_spacing.setText("Spacing")
        self.btn_spacing.setPopupMode(QToolButton.InstantPopup)
        self.btn_spacing.setAutoRaise(True)

        sp_menu = QMenu(self)
        sp_menu.addAction("Single (1.0)",  lambda: self.set_line_spacing(1.0))
        sp_menu.addAction("1.15",          lambda: self.set_line_spacing(1.15))
        sp_menu.addAction("1.5",           lambda: self.set_line_spacing(1.5))
        sp_menu.addAction("Double (2.0)",  lambda: self.set_line_spacing(2.0))
        sp_menu.addSeparator()
        sp_menu.addAction("2.5",           lambda: self.set_line_spacing(2.5))
        sp_menu.addAction("3.0",           lambda: self.set_line_spacing(3.0))
        self.btn_spacing.setMenu(sp_menu)
        self.toolbar.addWidget(self.btn_spacing)

        self.toolbar.addSeparator()

        btn_align = QToolButton(self)
        btn_align.setText("Align")
        btn_align.setPopupMode(QToolButton.InstantPopup)
        btn_align.setAutoRaise(True)

        menu_align = QMenu(self)
        menu_align.addAction("Left",   lambda: self.editor.setAlignment(Qt.AlignLeft))
        menu_align.addAction("Center", lambda: self.editor.setAlignment(Qt.AlignCenter))
        menu_align.addAction("Right",  lambda: self.editor.setAlignment(Qt.AlignRight))
        btn_align.setMenu(menu_align)
        self.toolbar.addWidget(btn_align)

        act_col = QAction("Color", self)
        act_col.triggered.connect(self.choose_color)
        self.toolbar.addAction(act_col)

        self.toolbar.addSeparator()

        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(self.set_font_family)
        self.toolbar.addWidget(self.font_combo)

        self.font_down_button = self._make_toolbar_button("A▼", "Decrease selected font size", lambda: self.adjust_font_size(-1))
        self.font_up_button = self._make_toolbar_button("A▲", "Increase selected font size", lambda: self.adjust_font_size(1))
        self.toolbar.addWidget(self.font_down_button)
        self.toolbar.addWidget(self.font_up_button)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
        self.font_size_spin.setValue(DEFAULT_FONT_SIZE)
        self.font_size_spin.setKeyboardTracking(False)
        self.font_size_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.font_size_spin.setToolTip("Type a font size and press Enter to apply exactly.")
        line_edit = self.font_size_spin.lineEdit()
        if line_edit is not None:
            line_edit.returnPressed.connect(lambda: self.set_font_size(self.font_size_spin.value()))
        self.toolbar.addWidget(self.font_size_spin)

        self.hypernote_button = self._make_toolbar_button("H", "Hypernote", self.open_hypernote_dialog)
        self.hypernote_button.setObjectName("hypernoteButton")
        families = {family.casefold(): family for family in QtGui.QFontDatabase.families()}
        display_family = families.get("magneto", "Georgia")
        hypernote_font = QFont(display_family, 24)
        hypernote_font.setStyleHint(QFont.Fantasy)
        hypernote_font.setBold(True)
        self.hypernote_button.setFont(hypernote_font)
        self.hypernote_button.setMinimumSize(46, 36)
        self.toolbar.addWidget(self.hypernote_button)

        act_find = QAction("Find", self)
        act_find.setShortcut(QKeySequence.Find)
        act_find.triggered.connect(self.open_find_replace)
        self.toolbar.addAction(act_find)



    # ──────────────────────────────────────────────────────────────────────
    # Message overlay controls
    # ──────────────────────────────────────────────────────────────────────

    def _overlay_rgb(self) -> tuple[int, int, int]:
        _label, rgb, _ink = MESSAGE_OVERLAY_PRESETS.get(
            self.overlay_preset,
            MESSAGE_OVERLAY_PRESETS[DEFAULT_MESSAGE_OVERLAY_PRESET],
        )
        return rgb

    def _build_overlay_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(self)
        panel.setObjectName("overlayControlPanel")
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(280)

        root = QVBoxLayout(panel)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("Text Background", panel)
        title.setObjectName("overlayPanelTitle")
        root.addWidget(title)

        note = QLabel("Controls the layer behind the message in the final letter.", panel)
        note.setObjectName("overlayPanelNote")
        note.setWordWrap(True)
        root.addWidget(note)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        for key in ("black", "white", "paper", "clear"):
            label, _rgb, _ink = MESSAGE_OVERLAY_PRESETS[key]
            btn = QPushButton(label, panel)
            btn.setObjectName("overlayPresetButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, preset=key: self._set_message_overlay_preset(preset))
            self.overlay_buttons[key] = btn
            if key in ("black", "white"):
                row1.addWidget(btn)
            else:
                row2.addWidget(btn)

        root.addLayout(row1)
        root.addLayout(row2)

        self.overlay_opacity_label = QLabel(panel)
        self.overlay_opacity_label.setObjectName("overlayOpacityLabel")
        root.addWidget(self.overlay_opacity_label)

        self.overlay_opacity_slider = QSlider(Qt.Horizontal, panel)
        self.overlay_opacity_slider.setObjectName("overlayOpacitySlider")
        self.overlay_opacity_slider.setRange(0, 100)
        self.overlay_opacity_slider.setValue(int(self.overlay_opacity))
        self.overlay_opacity_slider.valueChanged.connect(self._set_message_overlay_opacity)
        root.addWidget(self.overlay_opacity_slider)

        preview = QtWidgets.QFrame(panel)
        preview.setObjectName("overlayColorPreview")
        preview.setFixedHeight(34)
        root.addWidget(preview)
        self.overlay_color_preview = preview

        root.addStretch(1)
        self._sync_overlay_controls()
        return panel

    def _set_message_overlay_preset(self, preset: str) -> None:
        preset = str(preset or "").strip().lower()
        if preset not in MESSAGE_OVERLAY_PRESETS:
            return

        self.overlay_preset = preset
        if preset == "clear":
            self.overlay_opacity = 0
            if hasattr(self, "overlay_opacity_slider"):
                self.overlay_opacity_slider.blockSignals(True)
                self.overlay_opacity_slider.setValue(0)
                self.overlay_opacity_slider.blockSignals(False)

        self._save_overlay_settings()
        self._sync_overlay_controls()

    def _set_message_overlay_opacity(self, value: int) -> None:
        opacity = max(0, min(100, int(value)))
        if self.overlay_preset == "clear" and opacity > 0:
            self.overlay_preset = DEFAULT_MESSAGE_OVERLAY_PRESET
        self.overlay_opacity = opacity
        self._save_overlay_settings()
        self._sync_overlay_controls()

    def _sync_overlay_controls(self) -> None:
        for key, btn in getattr(self, "overlay_buttons", {}).items():
            btn.blockSignals(True)
            btn.setChecked(key == self.overlay_preset)
            btn.blockSignals(False)

        if hasattr(self, "overlay_opacity_slider"):
            self.overlay_opacity_slider.blockSignals(True)
            self.overlay_opacity_slider.setValue(int(self.overlay_opacity))
            self.overlay_opacity_slider.blockSignals(False)

        if hasattr(self, "overlay_opacity_label"):
            label, _rgb, _ink = MESSAGE_OVERLAY_PRESETS.get(
                self.overlay_preset,
                MESSAGE_OVERLAY_PRESETS[DEFAULT_MESSAGE_OVERLAY_PRESET],
            )
            self.overlay_opacity_label.setText(f"Opacity: {int(self.overlay_opacity)}%  •  {label}")

        if hasattr(self, "overlay_color_preview"):
            r, g, b = self._overlay_rgb()
            alpha = max(0.0, min(1.0, self.overlay_opacity / 100.0))
            self.overlay_color_preview.setStyleSheet(
                "QFrame#overlayColorPreview {"
                f"background: rgba({r}, {g}, {b}, {alpha:.3f});"
                "border: 1px solid #596273;"
                "border-radius: 8px;"
                "}"
            )

    def _save_overlay_settings(self) -> None:
        data = _read_json(self.settings_path)
        data[MESSAGE_OVERLAY_PRESET_KEY] = self.overlay_preset
        data[MESSAGE_OVERLAY_OPACITY_KEY] = int(self.overlay_opacity)
        try:
            _atomic_write(self.settings_path, json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


    def _apply_styles(self) -> None:
        self.setStyleSheet(
            "QDialog{background:#121212;}"
            "QToolBar{background:#161616;border:1px solid #242424;border-radius:6px;margin:2px;padding:2px;}"
            "QTextEdit,QPlainTextEdit{background:#0f0f0f;color:#eee;border:1px solid #222;border-radius:6px;padding:8px;}"
            "QLabel{color:#bbb;}"
            "QSplitter::handle{background:#1e1e1e;}"
            "QPushButton{background:#1d1d1d;color:#fff;border:1px solid #00d0ff;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
            "QSpinBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;padding:2px;}"
            "QFontComboBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;}"
            "QMenu{background:#141414;color:#eee;border:1px solid #2a2a2a;}"
            "QToolButton{color:#e6e6e6; padding:4px 8px; border:1px solid #2a2a2a; border-radius:6px; background:#101010;}"
            "QToolButton:hover{border-color:#00d0ff;}"
            "QToolButton#hypernoteButton{min-width:46px;min-height:36px;padding:0 8px;color:#8fc7ff;border-color:#1f5b92;background:#111820;font-size:24px;}"
            "QToolButton#hypernoteButton:hover{color:#ffffff;border-color:#69b7ff;background:#17304a;}"
            "QFrame#overlayControlPanel{background:#2b2f36;border:1px solid #414852;border-radius:10px;}"
            "QLabel#overlayPanelTitle{color:#edf1f7;font-size:14px;font-weight:800;}"
            "QLabel#overlayPanelNote{color:#aab4c0;font-size:11px;font-weight:500;}"
            "QLabel#overlayOpacityLabel{color:#dce4ef;font-size:12px;font-weight:700;padding-top:4px;}"
            "QPushButton#overlayPresetButton{background:#1f242c;color:#edf1f7;border:1px solid #4b5563;border-radius:7px;padding:6px 8px;min-height:28px;}"
            "QPushButton#overlayPresetButton:hover{background:#303846;border-color:#748194;color:#fff;}"
            "QPushButton#overlayPresetButton:checked{background:#435061;border-color:#9aa8bb;color:#ffffff;}"
            "QSlider#overlayOpacitySlider::groove:horizontal{height:6px;background:#1b2028;border-radius:3px;}"
            "QSlider#overlayOpacitySlider::handle:horizontal{width:16px;height:16px;margin:-5px 0;background:#d5dde8;border:1px solid #ffffff;border-radius:8px;}"
            "QToolButton#fontInfoButton{min-width:18px;max-width:18px;min-height:18px;max-height:18px;border-radius:9px;padding:0;color:#8290a3;border:1px solid #2f3744;background:#161a20;font-size:10px;font-weight:700;}"
            "QToolButton#fontInfoButton:hover{color:#e6edf6;border-color:#536477;background:#202833;}"
            "QCheckBox{color:#ddd;}"
        )

    def _selected_font_family_for_export(self) -> str:
        """Return the font family currently selected in the editor toolbar."""
        try:
            family = self.font_combo.currentFont().family().strip()
            if family:
                return family
        except Exception:
            pass

        fmt = self.editor.currentCharFormat()
        return (fmt.fontFamily() or fmt.font().family() or self.editor.font().family() or "Default").strip()

    def _selected_font_size_text(self) -> str:
        """Return the visible font-size value without applying it to selected text."""
        try:
            value = int(self.font_size_spin.value())
            if FONT_SIZE_MIN <= value <= FONT_SIZE_MAX:
                return f"{value} pt"
        except Exception:
            pass

        fmt = self.editor.currentCharFormat()
        point_size = fmt.fontPointSize()
        if not point_size or point_size <= 0:
            point_size = self.editor.fontPointSize()
        if not point_size or point_size <= 0:
            point_size = DEFAULT_FONT_SIZE
        return f"{int(round(point_size))} pt"

    def _bundled_font_index(self) -> dict[str, list[Path]]:
        """Build a filename-based index of project-bundled font files."""
        cached = getattr(self, "_bundled_font_cache", None)
        if isinstance(cached, dict):
            return cached

        index: dict[str, list[Path]] = {}
        seen: set[Path] = set()
        for relative_dir in BUNDLED_FONT_DIR_CANDIDATES:
            folder = (self.project_root / relative_dir).resolve()
            if not folder.is_dir():
                continue
            try:
                font_files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.casefold() in FONT_FILE_SUFFIXES]
            except Exception:
                font_files = []
            for font_path in font_files:
                if font_path in seen:
                    continue
                seen.add(font_path)
                for key in _font_file_match_keys(font_path):
                    index.setdefault(key, []).append(font_path)

        self._bundled_font_cache = index
        return index

    def _font_export_status(self, family: str) -> tuple[str, list[Path]]:
        """Return a readable export status and matching bundled font files."""
        key = _font_match_key(family)
        bundled = self._bundled_font_index().get(key, [])
        if bundled:
            return "Bundled", bundled

        if (family or "").strip().casefold() in COMMON_SYSTEM_FONT_FAMILIES:
            return "Not bundled — common system font", []

        return "Not bundled", []

    def _font_export_info_text(self) -> str:
        """Build the text shown by the bottom info button."""
        family = self._selected_font_family_for_export()
        size_text = self._selected_font_size_text()
        status, matches = self._font_export_status(family)

        if matches:
            match_lines = "\n".join(f"- {path}" for path in matches[:8])
            if len(matches) > 8:
                match_lines += f"\n- ...and {len(matches) - 8} more"
        else:
            searched = "\n".join(f"- {self.project_root / item}" for item in BUNDLED_FONT_DIR_CANDIDATES)
            match_lines = "No matching bundled font file found.\n\nSearched folders:\n" + searched

        if status == "Bundled":
            meaning = (
                "This font has a matching font file inside the Letter Smith project font folders. "
                "It is treated as bundled project material rather than depending only on the recipient's computer."
            )
        elif status == "Not bundled — common system font":
            meaning = (
                "This font is common on many Windows systems, but there is no matching bundled font file in the project. "
                "The viewer may still display it correctly on your machine, but recipient devices can fall back if they do not have it."
            )
        else:
            meaning = (
                "This font is not bundled in the project font folders. The saved HTML can name the font, "
                "but the final viewer depends on the recipient/browser having that font or choosing a fallback."
            )

        return (
            f"Selected font: {family}\n"
            f"Selected size: {size_text}\n"
            f"Bundled status: {status}\n\n"
            f"{meaning}\n\n"
            f"Matching bundled file(s):\n{match_lines}"
        )

    def show_font_export_info(self) -> None:
        """Show bundled-font status for the currently selected font."""
        text = self._font_export_info_text()
        try:
            self.font_info_button.setToolTip(text)
        except Exception:
            pass

        dlg = QDialog(self)
        dlg.setWindowTitle("Font Export Info")
        dlg.setModal(True)
        dlg.resize(620, 360)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Font Export Info", dlg)
        title.setStyleSheet("color:#e6f7ff;font-size:16px;font-weight:800;")
        root.addWidget(title)

        body = QLabel(text, dlg)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setWordWrap(True)
        body.setStyleSheet(
            "QLabel{background:#0f0f0f;color:#e6e6e6;border:1px solid #2a2a2a;"
            "border-radius:8px;padding:10px;}"
        )
        root.addWidget(body, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK", dlg)
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        root.addLayout(row)

        dlg.setStyleSheet(
            "QDialog{background:#121212;}"
            "QPushButton{background:#1d1d1d;color:#fff;border:1px solid #00d0ff;border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#00d0ff;color:#111;}"
        )
        dlg.exec()

    def _restore_geometry(self) -> None:
        geom = self.settings.value(SETTINGS_KEY_GEOMETRY)
        if isinstance(geom, QtCore.QByteArray):
            self.restoreGeometry(geom)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.settings.setValue(SETTINGS_KEY_GEOMETRY, self.saveGeometry())
        except Exception:
            pass
        super().closeEvent(event)

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def get_edited_html(self) -> str:
        return self.editor.toHtml()

    def apply_changes(self) -> None:
        """
        Save to message.html, but force browser to match spacing chosen in editor by
        injecting a wrapper with inline line-height.
        """
        content = self.get_edited_html()
        content = self._inject_export_line_spacing_wrapper(content)
        try:
            _atomic_write(self.message_path, content, encoding="utf-8")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save:\n{type(e).__name__}: {e}")

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
        if 'class="ls-linewrap"' in inner:
            return html

        new_inner = wrapper_open + inner + wrapper_close
        return html[:body_tag_end + 1] + new_inner + html[body_close:]

    # ──────────────────────────────────────────────────────────────────────
    # Commands
    # ──────────────────────────────────────────────────────────────────────

    def insert_salutation(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)

        fmt = QTextCharFormat()
        fmt.setFontFamily("Parchment")
        fmt.setFontPointSize(48.0)

        cursor.insertText(f"Dear {self.recipient_name},", fmt)
        cursor.insertBlock()
        self.editor.setTextCursor(cursor)

    def open_find_replace(self) -> None:
        dlg = getattr(self, "_find_dialog", None)
        if dlg is None or not isinstance(dlg, FindReplaceDialog):
            dlg = FindReplaceDialog(self)
            self._find_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _selected_hypernote_note(self) -> Optional[str]:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return _hypernote_note_from_href(cursor.charFormat().anchorHref())

        for _start, _end, fmt in self._iter_selected_fragments():
            note = _hypernote_note_from_href(fmt.anchorHref())
            if note is not None:
                return note
        return None

    def open_hypernote_dialog(self) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            QtWidgets.QApplication.beep()
            return

        existing_note = self._selected_hypernote_note()
        dlg = HypernoteDialog(existing_note or "", allow_remove=existing_note is not None, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        if dlg.remove_requested:
            self.remove_hypernote()
            return

        self.apply_hypernote(dlg.note_text(), reset_style=existing_note is None)

    def apply_hypernote(self, note: str, *, reset_style: bool = True) -> None:
        href = _hypernote_href(note)

        def transform(fmt: QTextCharFormat) -> None:
            fmt.setAnchor(True)
            fmt.setAnchorHref(href)
            if reset_style:
                fmt.setForeground(HYPERNOTE_DEFAULT_COLOR)

        self._apply_to_selected_fragments(transform)

    def remove_hypernote(self) -> None:
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setAnchor(False)
            fmt.setAnchorHref("")

        self._apply_to_selected_fragments(transform)

    # ──────────────────────────────────────────────────────────────────────
    # Formatting
    # ──────────────────────────────────────────────────────────────────────

    def _apply_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
            self.editor.setTextCursor(cursor)
        else:
            self.editor.mergeCurrentCharFormat(fmt)

    def _iter_selected_fragments(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return []

        start, end = cursor.selectionStart(), cursor.selectionEnd()
        doc = self.editor.document()
        block = doc.findBlock(start)
        fragments = []
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    frag_start = fragment.position()
                    frag_end = frag_start + fragment.length()
                    sel_start = max(start, frag_start)
                    sel_end = min(end, frag_end)
                    if sel_start < sel_end:
                        fragments.append((sel_start, sel_end, QTextCharFormat(fragment.charFormat())))
                it += 1
            if block.position() + block.length() >= end:
                break
            block = block.next()
        return fragments

    def _apply_to_selected_fragments(self, transform) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            fmt = QTextCharFormat(self.editor.currentCharFormat())
            transform(fmt)
            self._apply_char_format(fmt)
            return

        fragments = self._iter_selected_fragments()
        if not fragments:
            fmt = QTextCharFormat(self.editor.currentCharFormat())
            transform(fmt)
            self._apply_char_format(fmt)
            return

        doc = self.editor.document()
        work = QTextCursor(doc)
        original_start, original_end = cursor.selectionStart(), cursor.selectionEnd()
        work.beginEditBlock()
        try:
            for start, end, fmt in fragments:
                transform(fmt)
                work.setPosition(start)
                work.setPosition(end, QTextCursor.KeepAnchor)
                work.mergeCharFormat(fmt)
        finally:
            work.endEditBlock()

        restored = QTextCursor(doc)
        restored.setPosition(original_start)
        restored.setPosition(original_end, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(restored)

    def _selected_fragments_all_match(self, predicate) -> bool:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return bool(predicate(self.editor.currentCharFormat()))
        fragments = self._iter_selected_fragments()
        if not fragments:
            return bool(predicate(self.editor.currentCharFormat()))
        return all(predicate(fmt) for _start, _end, fmt in fragments)

    def set_font_family(self, font: QFont) -> None:
        family = font.family()
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontFamily(family)
        self._apply_to_selected_fragments(transform)

    def _effective_font_size(self, fmt: QTextCharFormat) -> float:
        point_size = fmt.fontPointSize()
        if not point_size or point_size <= 0:
            try:
                point_size = fmt.font().pointSizeF()
            except Exception:
                point_size = 0
        if not point_size or point_size <= 0:
            point_size = self.editor.fontPointSize()
        if not point_size or point_size <= 0:
            point_size = DEFAULT_FONT_SIZE
        return float(point_size)

    def set_font_size(self, size: int) -> None:
        size_i = int(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, size)))
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontPointSize(float(size_i))
        self._apply_to_selected_fragments(transform)

    def adjust_font_size(self, delta: int) -> None:
        delta = int(delta)
        def transform(fmt: QTextCharFormat) -> None:
            current = self._effective_font_size(fmt)
            fmt.setFontPointSize(float(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, int(round(current)) + delta))))
        self._apply_to_selected_fragments(transform)

    def _toggle_weight(self, wt: int) -> None:
        make_normal = self._selected_fragments_all_match(lambda fmt: fmt.fontWeight() >= wt)
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontWeight(QFont.Normal if make_normal else wt)
        self._apply_to_selected_fragments(transform)

    def toggle_bold(self) -> None:
        self._toggle_weight(QFont.Bold)

    def toggle_italic(self) -> None:
        turn_off = self._selected_fragments_all_match(lambda fmt: fmt.fontItalic())
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontItalic(not turn_off)
        self._apply_to_selected_fragments(transform)

    def toggle_underline(self) -> None:
        turn_off = self._selected_fragments_all_match(lambda fmt: fmt.fontUnderline())
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontUnderline(not turn_off)
        self._apply_to_selected_fragments(transform)

    def toggle_strikethrough(self) -> None:
        turn_off = self._selected_fragments_all_match(lambda fmt: fmt.fontStrikeOut())
        def transform(fmt: QTextCharFormat) -> None:
            fmt.setFontStrikeOut(not turn_off)
        self._apply_to_selected_fragments(transform)

    def clear_formatting(self) -> None:
        c = self.editor.textCursor()
        if c.hasSelection():
            txt = c.selectedText()
            c.insertText(txt)
            self.editor.setTextCursor(c)
        else:
            self.editor.setCurrentCharFormat(QTextCharFormat())

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.last_color, parent=self)
        if not color.isValid():
            return
        self.last_color = color
        try:
            self.settings.setValue(SETTINGS_KEY_COLOR, color)
        except Exception:
            pass

        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._apply_char_format(fmt)

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
        finally:
            self.font_combo.blockSignals(False)
            self.font_size_spin.blockSignals(False)
