# File: Editor.py
# -*- coding: utf-8 -*-
"""
Letter Smith — Rich Text Editor (dark mode, professional polish)

Highlights
- Writes atomically to the project message HTML (config.MESSAGE_HTML_FILE)
- Loads recipient/title hints from settings.json
- Drag & drop / paste images: copies into gallery/message_assets/ and inserts relative <img>
- Find/Replace dialog (Ctrl+F), Undo/Redo, Bold/Italic/Underline (Ctrl+B/I/U)
- Font family + size controls with live sync
- Word/char counter, persistent window geometry & last chosen text color (QSettings)
- Live preview over the wall.png background or a provided pixmap

Safe paths/filenames come from config: SETTINGS_FILE, GALLERY_DIR, MESSAGE_HTML_FILE
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt, QSize, QSettings, QEvent, QMimeData
from PySide6.QtGui import (
    QFont, QColor, QIcon, QAction, QKeySequence,
    QTextCharFormat, QTextListFormat, QTextCursor, QDragEnterEvent, QDropEvent,
    QPixmap, QImage
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QToolBar,
    QFontComboBox, QSpinBox, QLabel, QColorDialog,
    QPushButton, QMessageBox, QDialogButtonBox, QLineEdit, QToolButton, QMenu, QSplitter
)

from config import (
    SETTINGS_FILE,
    GALLERY_DIR,
    MESSAGE_HTML_FILE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants & Helpers
# ─────────────────────────────────────────────────────────────────────────────

TOOLBAR_ICON_SIZE = QSize(16, 16)
DEFAULT_FONT_SIZE = 16
FONT_SIZE_MIN, FONT_SIZE_MAX = 1, 100

SETTINGS_ORG = "LetterSmith"
SETTINGS_APP = "Editor"
SETTINGS_KEY_COLOR = "textColor"
SETTINGS_KEY_GEOMETRY = "windowGeometry"

ASSET_SUBDIR = "message_assets"  # under gallery/

# Atomic write for safety on Windows (replace in place)
def _atomic_write(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{int(time.time()*1000)}")
    tmp.write_text(data, encoding=encoding)
    # Try a couple retries for Windows replace corner cases
    tries = 3
    for i in range(tries):
        try:
            if path.exists():
                tmp.replace(path)
            else:
                tmp.rename(path)
            return
        except Exception:
            time.sleep(0.05)
    # final attempt or raise
    if path.exists():
        tmp.replace(path)
    else:
        tmp.rename(path)

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _safe_name(filename: str) -> str:
    # Basic guard: strip control chars and replace path separators
    bad = '<>:"/\\|?*'
    out = "".join(("_" if ch in bad else ch) for ch in filename)
    out = out.strip().strip(".")
    return out or "asset"

def _read_json(fp: Path) -> dict:
    try:
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    except Exception:
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# Find / Replace
# ─────────────────────────────────────────────────────────────────────────────

class FindReplaceDialog(QDialog):
    """Dialog for finding & replacing text."""
    def __init__(self, parent: 'Editor') -> None:
        super().__init__(parent)
        self.setWindowTitle("Find / Replace")
        self.setModal(True)
        self.editor: QTextEdit = parent.editor
        self._init_ui()
        self.setStyleSheet(
            "QDialog{background:#141414;border:1px solid #00d0ff;border-radius:8px;}"
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #2a2a2a;padding:6px;border-radius:4px;}"
            "QLabel{color:#ddd}"
            "QPushButton{background:#232323;color:#fff;border:1px solid #00d0ff;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111}"
        )

    def _init_ui(self) -> None:
        vbox = QVBoxLayout(self)
        hbox = QHBoxLayout()
        self.find_input = QLineEdit(placeholderText="Find…")
        self.replace_input = QLineEdit(placeholderText="Replace with…")
        hbox.addWidget(self.find_input)
        hbox.addWidget(self.replace_input)
        vbox.addLayout(hbox)

        box = QDialogButtonBox(
            QDialogButtonBox.Find | QDialogButtonBox.Replace | QDialogButtonBox.Close
        )
        box.button(QDialogButtonBox.Find).clicked.connect(self.find_next)
        box.button(QDialogButtonBox.Replace).clicked.connect(self.replace)
        box.rejected.connect(self.reject)
        vbox.addWidget(box)

    def find_next(self) -> None:
        term = self.find_input.text()
        if not term:
            return
        cursor = self.editor.textCursor()
        start = cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
        found = self.editor.document().find(term, start)
        if found.isNull():
            found = self.editor.document().find(term, 0)
        if not found.isNull():
            self.editor.setTextCursor(found)

    def replace(self) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.insertText(self.replace_input.text())
        self.find_next()

# ─────────────────────────────────────────────────────────────────────────────
# Custom TextEdit that handles image paste/drop into gallery/message_assets/
# ─────────────────────────────────────────────────────────────────────────────

class RichTextEdit(QTextEdit):
    def __init__(self, project_root: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.project_root = Path(project_root)
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)

    # Centralized asset copy -> returns relative URI for HTML (gallery/...)
    def _copy_into_assets(self, src_path: Path) -> Optional[str]:
        if not src_path.is_file():
            return None
        gallery = self.project_root / GALLERY_DIR
        assets_dir = gallery / ASSET_SUBDIR
        _ensure_dir(assets_dir)
        base = _safe_name(src_path.name)
        dst = assets_dir / base
        # Avoid collisions by appending a counter
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
            rel = (gallery / ASSET_SUBDIR / dst.name).as_posix()
            return rel  # e.g. 'gallery/message_assets/foo.png'
        except Exception:
            return None

    def insert_image_html(self, rel_uri: str, width_px: int = 320) -> None:
        c = self.textCursor()
        c.insertHtml(f'<img src="{rel_uri}" width="{int(width_px)}"/>')

    # Drag & drop files
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
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

    # Paste images from clipboard (QImage) or file URLs
    def insertFromMimeData(self, source: QMimeData) -> None:
        # Image in clipboard
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                gallery = self.project_root / GALLERY_DIR
                assets_dir = gallery / ASSET_SUBDIR
                _ensure_dir(assets_dir)
                name = f"pasted_{int(time.time()*1000)}.png"
                out = assets_dir / name
                img.save(str(out), "PNG")
                rel = (gallery / ASSET_SUBDIR / name).as_posix()
                self.insert_image_html(rel, 320)
                return
        # File URLs
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
# Preview Widget
# ─────────────────────────────────────────────────────────────────────────────

class PreviewWidget(QtWidgets.QWidget):
    """HTML preview painted over a background image or provided pixmap."""
    def __init__(self, background_path: Optional[Path], editor: QTextEdit,
                 preview_pixmap: Optional[QPixmap] = None, parent=None) -> None:
        super().__init__(parent)
        self._bg_path = Path(background_path) if background_path else None
        self._bg_pm: Optional[QPixmap] = preview_pixmap if (preview_pixmap and not preview_pixmap.isNull()) else None
        self._editor = editor
        self.setMinimumWidth(320)

    def setBackgroundPixmap(self, pm: Optional[QPixmap]) -> None:
        self._bg_pm = pm if (pm and not pm.isNull()) else None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()

        # Draw background
        pm = self._bg_pm
        if pm is None and self._bg_path and self._bg_path.exists():
            pm = QPixmap(str(self._bg_path))
        if pm and not pm.isNull():
            bg = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(0, 0, bg)
        else:
            painter.fillRect(self.rect(), Qt.black)

        # Draw HTML document
        doc = QtGui.QTextDocument()
        doc.setDefaultStyleSheet("body{color:#f4f4f4;font-family:serif;line-height:1.5;} img{max-width:100%;}")
        doc.setHtml(self._editor.toHtml())
        margin = 16
        doc.setTextWidth(w - (margin * 2))
        painter.save()
        painter.translate(margin, margin)
        doc.drawContents(painter, QtCore.QRectF(0, 0, w - (margin * 2), h - (margin * 2)))
        painter.restore()
        painter.end()

# ─────────────────────────────────────────────────────────────────────────────
# Editor Dialog
# ─────────────────────────────────────────────────────────────────────────────

class Editor(QDialog):
    """Rich-text editor dialog for Letter Smith."""
    def __init__(
        self,
        message_html: str,
        preview_pixmap: Optional[QtGui.QPixmap] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)

        # Resolve project root from parent or cwd
        self.project_root = Path(getattr(parent, 'project_root', os.getcwd()))
        self.settings_json = self.project_root / SETTINGS_FILE
        self.message_path = self.project_root / MESSAGE_HTML_FILE

        # Load recipient name (for Salutation)
        data = _read_json(self.settings_json)
        self.recipient_name = data.get("recipient_name") or "Friend"

        # QSettings for small UI prefs
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        self.setWindowTitle("Letter Smith — Editor")
        self.resize(1000, 650)
        self._apply_dark_style()
        self._load_geometry()

        self.message_html = message_html or ""
        self._init_ui(preview_pixmap)

    # ── Window dressing ──────────────────────────────────
    def _apply_dark_style(self) -> None:
        self.setStyleSheet(
            "QDialog{background:#121212;}"
            "QToolBar{background:#161616;border:1px solid #242424;border-radius:6px;margin:2px;padding:2px;}"
            "QTextEdit{background:#0f0f0f;color:#eee;border:1px solid #222;border-radius:6px;padding:8px;}"
            "QLabel{color:#bbb}"
            "QSplitter::handle{background:#1e1e1e}"
            "QPushButton{background:#1d1d1d;color:#fff;border:1px solid #00d0ff;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111}"
            "QSpinBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;padding:2px;}"
            "QFontComboBox{background:#181818;color:#eee;border:1px solid #333;border-radius:4px;}"
            "QMenu{background:#141414;color:#eee;border:1px solid #2a2a2a;}"
            "QToolButton{color:#eee}"
        )

    def _load_geometry(self) -> None:
        geom = self.settings.value(SETTINGS_KEY_GEOMETRY)
        if isinstance(geom, QtCore.QByteArray):
            self.restoreGeometry(geom)

    def closeEvent(self, event: QEvent) -> None:
        self.settings.setValue(SETTINGS_KEY_GEOMETRY, self.saveGeometry())
        super().closeEvent(event)

    # ── UI build ─────────────────────────────────────────
    def _init_ui(self, preview_pixmap: Optional[QPixmap]) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Toolbar
        self.toolbar = QToolBar()
        self.toolbar.setIconSize(TOOLBAR_ICON_SIZE)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        main_layout.addWidget(self.toolbar)

        # restore last color
        saved_color = self.settings.value(SETTINGS_KEY_COLOR, QColor("#eeeeee"), type=QColor)
        self.last_color: QColor = saved_color

        # Salutation
        act_sal = QAction("Salutation", self)
        act_sal.setToolTip("Insert 'Dear <Name>,' at the top")
        act_sal.triggered.connect(self.insert_salutation)
        self.toolbar.addAction(act_sal)

        # Style menu
        btn_style = QToolButton(self)
        btn_style.setText("Style")
        btn_style.setPopupMode(QToolButton.MenuButtonPopup)
        btn_style.setAutoRaise(True)
        btn_style.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        menu_style = QMenu(self)
        menu_style.addAction("Bold", self.toggle_bold).setShortcut(QKeySequence.Bold)
        menu_style.addAction("Italic", self.toggle_italic).setShortcut(QKeySequence.Italic)
        menu_style.addAction("Underline", self.toggle_underline).setShortcut(QKeySequence.Underline)
        menu_style.addAction("Strike", self.toggle_strikethrough)
        menu_style.addSeparator()
        menu_style.addAction("Clear Formatting", self.clear_formatting)
        btn_style.setMenu(menu_style)
        self.toolbar.addWidget(btn_style)

        # Lists menu
        btn_list = QToolButton(self)
        btn_list.setText("Lists")
        btn_list.setPopupMode(QToolButton.MenuButtonPopup)
        btn_list.setAutoRaise(True)
        btn_list.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        menu_list = QMenu(self)
        menu_list.addAction("Bullet List", self.insert_bullet_list)
        menu_list.addAction("Number List", self.insert_number_list)
        btn_list.setMenu(menu_list)
        self.toolbar.addWidget(btn_list)

        # Align menu
        btn_align = QToolButton(self)
        btn_align.setText("Align")
        btn_align.setPopupMode(QToolButton.MenuButtonPopup)
        btn_align.setAutoRaise(True)
        btn_align.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        menu_align = QMenu(self)
        menu_align.addAction("Left",   lambda: self.editor.setAlignment(Qt.AlignLeft))
        menu_align.addAction("Center", lambda: self.editor.setAlignment(Qt.AlignCenter))
        menu_align.addAction("Right",  lambda: self.editor.setAlignment(Qt.AlignRight))
        btn_align.setMenu(menu_align)
        self.toolbar.addWidget(btn_align)

        # Color
        act_col = QAction(QIcon.fromTheme("format-text-color"), "Color", self)
        act_col.triggered.connect(self.choose_color)
        self.toolbar.addAction(act_col)

        # Insert Image (file dialog)
        act_img = QAction("Insert Image…", self)
        act_img.setShortcut("Ctrl+Shift+I")
        act_img.triggered.connect(self._insert_image_dialog)
        self.toolbar.addAction(act_img)

        # Undo / Redo / Find
        act_undo = QAction("Undo", self)
        act_undo.setShortcut(QKeySequence.Undo)
        act_undo.triggered.connect(lambda: self.editor.undo())
        self.toolbar.addAction(act_undo)

        act_redo = QAction("Redo", self)
        act_redo.setShortcut(QKeySequence.Redo)
        act_redo.triggered.connect(lambda: self.editor.redo())
        self.toolbar.addAction(act_redo)

        act_find = QAction("Find/Replace", self)
        act_find.setShortcut(QKeySequence.Find)
        act_find.triggered.connect(self.open_find_replace)
        self.toolbar.addAction(act_find)

        self.toolbar.addSeparator()

        # Font family + size
        self.font_combo = QFontComboBox(currentFontChanged=self.set_font_family)
        self.font_size_spin = QSpinBox(
            value=DEFAULT_FONT_SIZE, minimum=FONT_SIZE_MIN, maximum=FONT_SIZE_MAX
        )
        self.font_size_spin.valueChanged.connect(self.set_font_size)
        self.toolbar.addWidget(self.font_combo)
        self.toolbar.addWidget(self.font_size_spin)

        # Split editor/preview
        splitter = QSplitter(Qt.Horizontal)
        # Editor as custom class (handles images to assets)
        self.editor = RichTextEdit(self.project_root)
        self.editor.setHtml(self.message_html)
        splitter.addWidget(self.editor)

        wall_path = (self.project_root / GALLERY_DIR / "wall.png")
        self.preview = PreviewWidget(wall_path, editor=self.editor, preview_pixmap=preview_pixmap, parent=self)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        # Connect signals
        self.editor.textChanged.connect(self.preview.update)
        self.editor.textChanged.connect(self.update_word_count)
        self.editor.currentCharFormatChanged.connect(self.preview.update)
        self.editor.currentCharFormatChanged.connect(self._sync_format)

        # Bottom bar: word count + Save/Cancel
        hb = QHBoxLayout()
        self.word_label = QLabel()
        hb.addWidget(self.word_label)
        hb.addStretch()
        btn_save = QPushButton("Save")
        btn_save.setShortcut(QKeySequence.Save)  # Ctrl+S
        btn_save.clicked.connect(self.apply_changes)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        hb.addWidget(btn_save)
        hb.addWidget(btn_cancel)
        main_layout.addLayout(hb)

        self.update_word_count()

    # ── Commands ─────────────────────────────────────────
    def insert_salutation(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        fmt = QTextCharFormat()
        fmt.setFontFamily("Parchment")
        fmt.setFontPointSize(48)
        cursor.insertText(f"Dear {self.recipient_name},", fmt)
        cursor.insertBlock()
        self.editor.setTextCursor(cursor)

    def _insert_image_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Image", str(self.project_root),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        if not path:
            return
        rel = self.editor._copy_into_assets(Path(path))
        if rel:
            self.editor.insert_image_html(rel, 360)

    # Formatting
    def set_font_family(self, font: QFont) -> None:
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        self._apply_format(fmt)

    def set_font_size(self, size: int) -> None:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self._apply_format(fmt)

    def toggle_bold(self) -> None:
        self._toggle_weight(QFont.Bold)

    def toggle_italic(self) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontItalic(not fmt.fontItalic())
        self.editor.setCurrentCharFormat(fmt)

    def toggle_underline(self) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontUnderline(not fmt.fontUnderline())
        self.editor.setCurrentCharFormat(fmt)

    def toggle_strikethrough(self) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontStrikeOut(not fmt.fontStrikeOut())
        self.editor.setCurrentCharFormat(fmt)

    def clear_formatting(self) -> None:
        c = self.editor.textCursor()
        if c.hasSelection():
            txt = c.selectedText()
            c.insertText(txt)  # inserts plain text, clearing formatting
        else:
            # apply default format going forward
            self.editor.setCurrentCharFormat(QTextCharFormat())

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.last_color, parent=self)
        if not color.isValid():
            return
        self.last_color = color
        self.settings.setValue(SETTINGS_KEY_COLOR, color)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._apply_format(fmt)

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

    def open_find_replace(self) -> None:
        FindReplaceDialog(self).exec()

    def get_edited_html(self) -> str:
        return self.editor.toHtml() if self.editor.acceptRichText() else self.editor.toPlainText()

    def update_word_count(self) -> None:
        t = self.editor.toPlainText()
        # Basic token split; this keeps it fast and robust
        w = len([s for s in t.split() if s.strip()])
        c = len(t)
        self.word_label.setText(f"Words: {w:,}  |  Chars: {c:,}")

    def apply_changes(self) -> None:
        content = self.get_edited_html()
        try:
            _ensure_dir(self.message_path.parent)
            _atomic_write(self.message_path, content, encoding="utf-8")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save:\n{type(e).__name__}: {e}")

    # Internals
    def _apply_format(self, fmt: QTextCharFormat) -> None:
        c = self.editor.textCursor()
        if c.hasSelection():
            c.mergeCharFormat(fmt)
        else:
            self.editor.setCurrentCharFormat(fmt)

    def _toggle_weight(self, wt: int) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontWeight(QFont.Normal if fmt.fontWeight() == wt else wt)
        self.editor.setCurrentCharFormat(fmt)

    def _sync_format(self, fmt: QTextCharFormat) -> None:
        self.font_combo.blockSignals(True)
        self.font_size_spin.blockSignals(True)
        self.font_combo.setCurrentFont(fmt.font())
        sz = int(fmt.fontPointSize()) if fmt.fontPointSize() > 0 else DEFAULT_FONT_SIZE
        self.font_size_spin.setValue(sz)
        self.font_combo.blockSignals(False)
        self.font_size_spin.blockSignals(False)