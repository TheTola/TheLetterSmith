# File: Editor.py
# -*- coding: utf-8 -*-
"""
Letter Smith â€” Rich Text Editor (dark mode, professional polish)

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
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSize, QSettings, QMimeData
from PySide6.QtGui import (
    QFont,
    QColor,
    QAction,
    QBrush,
    QKeySequence,
    QSyntaxHighlighter,
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
    QGridLayout,
    QTextEdit,
    QToolBar,
    QFontComboBox,
    QSpinBox,
    QLabel,
    QColorDialog,
    QPushButton,
    QMessageBox,
    QLineEdit,
    QInputDialog,
    QToolButton,
    QMenu,
    QSplitter,
    QCheckBox,
    QAbstractSpinBox,
)

from config import (
    SETTINGS_FILE,
    GALLERY_DIR,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
)
from window_chrome import StandardTitleBar

try:
    from spellchecker import SpellChecker
except Exception:
    SpellChecker = None

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Constants & Helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

TOOLBAR_ICON_SIZE = QSize(16, 16)
DEFAULT_FONT_SIZE = 16
FONT_SIZE_MIN, FONT_SIZE_MAX = 1, 100
UNDO_SYMBOL = "\u21b6"
REDO_SYMBOL = "\u21b7"

SETTINGS_ORG = "LetterSmith"
SETTINGS_APP = "Editor"
SETTINGS_KEY_COLOR = "textColor"
SETTINGS_KEY_GEOMETRY = "windowGeometry"

ASSET_SUBDIR = "message_assets"  # under gallery/
AUTOSAVE_DELAY_MS = 1500
SPELLCHECK_SUGGESTION_LIMIT = 6
WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


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


class EditorSpellChecker:
    def __init__(self, personal_words_path: Path) -> None:
        self.personal_words_path = Path(personal_words_path)
        self.available = SpellChecker is not None
        self._personal_words: set[str] = set()
        self._known_cache: dict[str, bool] = {}

        if self.available:
            self._checker = SpellChecker(distance=1)
            self._load_personal_words()

    @staticmethod
    def normalize(word: str) -> str:
        return re.sub(r"[^A-Za-z']", "", word or "").strip("'").lower()

    def _load_personal_words(self) -> None:
        if not self.available:
            return

        try:
            raw = json.loads(self.personal_words_path.read_text(encoding="utf-8")) if self.personal_words_path.exists() else []
        except Exception:
            raw = []

        self._personal_words = {self.normalize(word) for word in raw if self.normalize(word)}
        if self._personal_words:
            self._checker.word_frequency.load_words(self._personal_words)

    def save_personal_words(self) -> None:
        if not self.available:
            return

        _atomic_write(
            self.personal_words_path,
            json.dumps(sorted(self._personal_words), indent=2),
            encoding="utf-8",
        )

    def add_word(self, word: str) -> None:
        normalized = self.normalize(word)
        if not self.available or not normalized:
            return

        if normalized not in self._personal_words:
            self._personal_words.add(normalized)
            self._checker.word_frequency.load_words([normalized])
            self.save_personal_words()
            self._known_cache.clear()

    def is_candidate_word(self, word: str) -> bool:
        normalized = self.normalize(word)
        return len(normalized) >= 2 and any(ch.isalpha() for ch in normalized)

    def is_correct(self, word: str) -> bool:
        if not self.available:
            return True

        normalized = self.normalize(word)
        if not self.is_candidate_word(normalized):
            return True

        if normalized in self._known_cache:
            return self._known_cache[normalized]

        result = bool(self._checker.known([normalized])) or normalized in self._personal_words
        self._known_cache[normalized] = result
        return result

    def suggestions(self, word: str) -> list[str]:
        if not self.available:
            return []

        normalized = self.normalize(word)
        if not normalized:
            return []

        candidates = list(self._checker.candidates(normalized) or [])
        ordered: list[str] = []

        correction = self._checker.correction(normalized)
        if correction and correction not in ordered and correction != normalized:
            ordered.append(correction)

        for cand in sorted(candidates):
            if cand != normalized and cand not in ordered:
                ordered.append(cand)

        def match_case(candidate: str) -> str:
            if word.isupper():
                return candidate.upper()
            if word.istitle():
                return candidate.title()
            return candidate

        return [match_case(candidate) for candidate in ordered[:SPELLCHECK_SUGGESTION_LIMIT]]


class SpellCheckHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, engine: EditorSpellChecker) -> None:
        super().__init__(document)
        self._engine = engine
        self._error_format = QTextCharFormat()
        self._error_format.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        self._error_format.setUnderlineColor(QColor("#ff6b6b"))

    def highlightBlock(self, text: str) -> None:
        if not self._engine.available:
            return

        for match in WORD_PATTERN.finditer(text):
            word = match.group(0)
            if not self._engine.is_correct(word):
                self.setFormat(match.start(), len(word), self._error_format)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Find / Replace Dialog
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class FindReplaceDialog(QDialog):
    """
    Robust find/replace:
      - Wrap-around search
      - Enter in Find triggers find-next
      - Optional match-case
    """
    def __init__(self, parent: "Editor") -> None:
        super().__init__(parent)
        self.setWindowTitle("Find / Replace")
        self.setModal(True)
        self.setMinimumWidth(460)

        self._editor: QTextEdit = parent.editor

        self.find_input = QLineEdit(placeholderText="Findâ€¦")
        self.replace_input = QLineEdit(placeholderText="Replace withâ€¦")
        self.match_case = QCheckBox("Match case")
        self.match_case.setChecked(False)
        self.find_input.setPlaceholderText("Find")
        self.replace_input.setPlaceholderText("Replace with")
        self.status_label = QLabel("Find the next match or replace the selected one.")
        self.status_label.setObjectName("findStatus")

        selected = self._editor.textCursor().selectedText().replace("\u2029", " ").strip()
        if selected:
            self.find_input.setText(selected)
            self.find_input.selectAll()

        self.find_input.returnPressed.connect(self.find_next)
        self.replace_input.returnPressed.connect(self.replace_one)

        row = QHBoxLayout()
        row.addWidget(self.find_input)
        row.addWidget(self.replace_input)

        opts = QHBoxLayout()
        opts.addWidget(self.match_case)
        opts.addStretch(1)

        button_row = QHBoxLayout()
        self.btn_find = QPushButton("Find Next")
        self.btn_replace = QPushButton("Replace")
        self.btn_replace_all = QPushButton("Replace All")
        self.btn_close = QPushButton("Close")
        self.btn_find.clicked.connect(self.find_next)
        self.btn_replace.clicked.connect(self.replace_one)
        self.btn_replace_all.clicked.connect(self.replace_all)
        self.btn_close.clicked.connect(self.reject)
        button_row.addWidget(self.btn_find)
        button_row.addWidget(self.btn_replace)
        button_row.addWidget(self.btn_replace_all)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.addLayout(row)
        root.addLayout(opts)
        root.addWidget(self.status_label)
        root.addLayout(button_row)

        self.setStyleSheet(
            "QDialog{background:#141414;border:1px solid #00d0ff;border-radius:8px;}"
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #2a2a2a;padding:6px;border-radius:4px;}"
            "QLabel{color:#d7d7d7;}"
            "QLabel#findStatus{color:#8fdcff;padding:4px 2px;}"
            "QCheckBox{color:#ddd; padding-left:2px;}"
            "QPushButton{background:#232323;color:#fff;border:1px solid #00d0ff;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#00d0ff;color:#111}"
        )
        self.find_input.setFocus()

    def _find_options(self) -> QTextDocument.FindFlags:
        flags = QTextDocument.FindFlags()
        if self.match_case.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        return flags

    def _set_status(self, message: str, *, error: bool = False) -> None:
        color = "#ff9696" if error else "#8fdcff"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color:{color}; padding:4px 2px;")

    def _selection_matches_find(self, term: str) -> bool:
        selected = self._editor.textCursor().selectedText()
        if not selected:
            return False
        if self.match_case.isChecked():
            return selected == term
        return selected.casefold() == term.casefold()

    def _find_next_match(self, *, announce: bool = True) -> bool:
        term = self.find_input.text().strip()
        if not term:
            self._set_status("Enter text to find.", error=True)
            return False

        doc = self._editor.document()
        flags = self._find_options()

        cur = self._editor.textCursor()
        start_cursor = QTextCursor(cur)
        if cur.hasSelection():
            start_cursor.setPosition(cur.selectionEnd())
        else:
            start_cursor.setPosition(cur.position())

        found = doc.find(term, start_cursor, flags)
        wrapped = False

        if found.isNull():
            top = QTextCursor(doc)
            top.movePosition(QTextCursor.Start)
            found = doc.find(term, top, flags)
            wrapped = not found.isNull()

        if found.isNull():
            QtWidgets.QApplication.beep()
            self._set_status(f'No matches for "{term}".', error=True)
            return False

        self._editor.setTextCursor(found)
        self._editor.ensureCursorVisible()
        if announce:
            self._set_status("Wrapped to the top." if wrapped else "Match found.")
        return True

    def find_next(self) -> None:
        self._find_next_match()

    def replace_one(self) -> None:
        term = self.find_input.text().strip()
        if not term:
            self._set_status("Enter text to replace.", error=True)
            return

        cur = self._editor.textCursor()
        if not self._selection_matches_find(term) and not self._find_next_match(announce=False):
            return

        cur = self._editor.textCursor()
        if cur.hasSelection() and self._selection_matches_find(term):
            replace_fmt = cur.charFormat()
            cur.insertText(self.replace_input.text(), replace_fmt)
            self._editor.setTextCursor(cur)
            if self._find_next_match(announce=False):
                self._set_status("Replaced 1 match.")
            else:
                self._set_status("Replaced 1 match. No more matches.")

    def replace_all(self) -> None:
        term = self.find_input.text().strip()
        if not term:
            self._set_status("Enter text to replace.", error=True)
            return

        doc = self._editor.document()
        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        count = 0
        try:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            found = doc.find(term, cursor, self._find_options())
            while not found.isNull():
                replace_fmt = found.charFormat()
                found.insertText(self.replace_input.text(), replace_fmt)
                count += 1
                cursor = QTextCursor(found)
                found = doc.find(term, cursor, self._find_options())
        finally:
            edit_cursor.endEditBlock()

        if count:
            self._set_status(f"Replaced {count} matches.")
        else:
            QtWidgets.QApplication.beep()
            self._set_status(f'No matches for "{term}".', error=True)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# RichTextEdit: paste/drop images into <project_root>/gallery/message_assets/
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
                if not img.save(str(out), "PNG"):
                    QMessageBox.warning(self, "Image Paste Error", "Could not save the pasted image.")
                    return
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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Preview Widget
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PreviewWidget(QtWidgets.QWidget):
    def __init__(
        self,
        background_path: Optional[Path],
        editor: QTextEdit,
        preview_pixmap: Optional[QPixmap] = None,
        parent=None
    ) -> None:
        super().__init__(parent)
        self._bg_path = Path(background_path) if background_path else None
        self._bg_pm: Optional[QPixmap] = preview_pixmap if (preview_pixmap and not preview_pixmap.isNull()) else None
        self._editor = editor
        self.setMinimumWidth(320)

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()

        pm = self._bg_pm
        if pm is None and self._bg_path and self._bg_path.exists():
            pm = QPixmap(str(self._bg_path))

        if pm and not pm.isNull():
            bg = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(0, 0, bg)
        else:
            painter.fillRect(self.rect(), Qt.black)

        doc = QtGui.QTextDocument()
        doc.setHtml(self._editor.toHtml())

        margin = 16
        doc.setTextWidth(max(1, w - margin * 2))

        painter.save()
        painter.translate(margin, margin)
        doc.drawContents(painter, QtCore.QRectF(0, 0, w - margin * 2, h - margin * 2))
        painter.restore()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Editor Dialog
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

        saved = self.settings.value(SETTINGS_KEY_COLOR, QColor("#eeeeee"))
        self.last_color = saved if isinstance(saved, QColor) else QColor("#eeeeee")

        self.message_html = message_html or ""
        self._last_saved_html = self.message_html
        self._dirty = False

        # Tracks the most recent spacing selection (used to force identical browser output)
        self._export_line_spacing: Optional[float] = None
        self._font_size_mixed_display = False
        self.autosave_path = self.message_path.with_name(f"{self.message_path.stem}.autosave.html")
        self.personal_dictionary_path = self.message_path.with_name("editor_dictionary.json")
        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(AUTOSAVE_DELAY_MS)
        self.autosave_timer.timeout.connect(self._write_autosave_draft)
        self.spell_checker = EditorSpellChecker(self.personal_dictionary_path)
        self.spell_highlighter: Optional[SpellCheckHighlighter] = None
        self._normal_geometry: Optional[QtCore.QRect] = None

        self.setObjectName("EditorDialog")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.base_title = "Letter Smith Editor"
        self.setWindowTitle(self.base_title)
        self.setModal(True)
        self.resize(1100, 720)

        self._apply_styles()
        self._build_ui(preview_pixmap)
        self._restore_geometry()
        self._install_editor_services()
        self._last_saved_html = self._current_editor_html()
        self._dirty = False
        self._sync_title_bar()
        self._maybe_restore_autosave_draft()
        self._sync_window_title()
        self._sync_current_format_state()

    def _build_ui(self, preview_pixmap: Optional[QPixmap]) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.title_bar = StandardTitleBar(
            self,
            "Letter Smith Editor",
            show_minimize=True,
            on_close=self.close,
            on_minimize=self.showMinimized,
            on_toggle_maximize=self._toggle_max_restore,
            is_maximized=self.isMaximized,
        )
        self.title_bar.insert_control_button("?", "Editor Help", self.show_shortcuts_help, object_name="windowHelpButton")
        main_layout.addWidget(self.title_bar)

        toolbar_host = QtWidgets.QWidget(self)
        toolbar_host.setObjectName("editorToolbarHost")
        toolbar_layout = QVBoxLayout(toolbar_host)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)

        self.command_toolbar = self._create_toolbar_row("editorCommandToolbar")
        self.format_toolbar = self._create_toolbar_row("editorFormatToolbar")
        toolbar_layout.addWidget(self.command_toolbar)
        toolbar_layout.addWidget(self.format_toolbar)
        main_layout.addWidget(toolbar_host)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.editor = RichTextEdit(self.project_root)
        self.editor.setPlaceholderText("Write your message here...")
        self.editor.document().setDocumentMargin(18)
        self.editor.setHtml(self.message_html)
        splitter.addWidget(self.editor)

        # Preview background should be the letter background image (wall.png)
        wall_path = (self.project_root / USER_PAGES_DIR / "wall.png").resolve()
        self.preview = PreviewWidget(wall_path, editor=self.editor, preview_pixmap=preview_pixmap, parent=self)
        splitter.addWidget(self.preview)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([740, 360])
        main_layout.addWidget(splitter)

        self._build_toolbar_actions()

        self.editor.textChanged.connect(self.preview.update)
        self.editor.textChanged.connect(self.update_word_count)
        self.editor.currentCharFormatChanged.connect(self.preview.update)
        self.editor.currentCharFormatChanged.connect(self._sync_format)
        self.editor.cursorPositionChanged.connect(self._sync_current_format_state)
        self.editor.textChanged.connect(self._on_editor_text_changed)

        hb = QHBoxLayout()
        self.word_label = QLabel()
        hb.addWidget(self.word_label)
        hb.addStretch()

        btn_save = QPushButton("Save")
        btn_save.setShortcut(QKeySequence.Save)
        btn_save.clicked.connect(self.apply_changes)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.close)

        hb.addWidget(btn_save)
        hb.addWidget(btn_cancel)
        main_layout.addLayout(hb)

        self.update_word_count()

    def _create_toolbar_row(self, object_name: str) -> QToolBar:
        toolbar = QToolBar(self)
        toolbar.setObjectName(object_name)
        toolbar.setIconSize(TOOLBAR_ICON_SIZE)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        return toolbar

    def _build_toolbar_actions(self) -> None:
        act_sal = QAction("Salutation", self)
        act_sal.triggered.connect(self.insert_salutation)
        self.command_toolbar.addAction(act_sal)
        self.command_toolbar.addSeparator()

        self.act_undo = QAction(UNDO_SYMBOL, self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.setToolTip("Undo")
        self.act_undo.triggered.connect(self.editor.undo)
        self.command_toolbar.addAction(self.act_undo)

        self.act_redo = QAction(REDO_SYMBOL, self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.setToolTip("Redo")
        self.act_redo.triggered.connect(self.editor.redo)
        self.command_toolbar.addAction(self.act_redo)
        self.command_toolbar.addSeparator()

        self.act_find = QAction("Find / Replace", self)
        self.act_find.setShortcut(QKeySequence.Find)
        self.act_find.triggered.connect(self.open_find_replace)
        self.command_toolbar.addAction(self.act_find)

        self.editor.undoAvailable.connect(self.act_undo.setEnabled)
        self.editor.redoAvailable.connect(self.act_redo.setEnabled)
        self.act_undo.setEnabled(self.editor.document().isUndoAvailable())
        self.act_redo.setEnabled(self.editor.document().isRedoAvailable())

        self.act_paste_plain = QAction("Paste Without Formatting", self)
        self.act_paste_plain.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.act_paste_plain.triggered.connect(self.paste_as_plain_text)
        self.addAction(self.act_paste_plain)

        self.act_help = QAction("Help", self)
        self.act_help.setShortcut(QKeySequence("F1"))
        self.act_help.triggered.connect(self.show_shortcuts_help)
        self.addAction(self.act_help)

        self.font_combo = QFontComboBox()
        self.font_combo.setMinimumContentsLength(12)
        self.font_combo.setMinimumWidth(190)
        self.font_combo.currentFontChanged.connect(self.set_font_family)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
        self.font_size_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.font_size_spin.setKeyboardTracking(False)
        self.font_size_spin.setAlignment(Qt.AlignCenter)
        self.font_size_spin.setFixedWidth(58)
        self.font_size_spin.setValue(DEFAULT_FONT_SIZE)
        self.font_size_spin.valueChanged[int].connect(self.set_font_size)
        self.font_size_spin.editingFinished.connect(self._commit_font_size_input)

        self.font_size_down = QPushButton("\u2212")
        self.font_size_down.setObjectName("toolbarMiniButton")
        self.font_size_down.setToolTip("Decrease font size")
        self.font_size_down.clicked.connect(lambda: self._step_font_size(-1))

        self.font_size_up = QPushButton("+")
        self.font_size_up.setObjectName("toolbarMiniButton")
        self.font_size_up.setToolTip("Increase font size")
        self.font_size_up.clicked.connect(lambda: self._step_font_size(1))

        size_box = QtWidgets.QWidget(self)
        size_layout = QHBoxLayout(size_box)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(4)
        size_layout.addWidget(self.font_size_down)
        size_layout.addWidget(self.font_size_spin)
        size_layout.addWidget(self.font_size_up)

        self.format_toolbar.addWidget(self.font_combo)
        self.format_toolbar.addWidget(size_box)
        self.format_toolbar.addSeparator()

        self.act_bold = QAction("Bold", self)
        self.act_bold.setShortcut(QKeySequence.Bold)
        self.act_bold.setCheckable(True)
        self.act_bold.triggered.connect(self.toggle_bold)
        self.format_toolbar.addAction(self.act_bold)

        self.act_italic = QAction("Italic", self)
        self.act_italic.setShortcut(QKeySequence.Italic)
        self.act_italic.setCheckable(True)
        self.act_italic.triggered.connect(self.toggle_italic)
        self.format_toolbar.addAction(self.act_italic)

        self.act_underline = QAction("Underline", self)
        self.act_underline.setShortcut(QKeySequence.Underline)
        self.act_underline.setCheckable(True)
        self.act_underline.triggered.connect(self.toggle_underline)
        self.format_toolbar.addAction(self.act_underline)

        self.act_strike = QAction("Strike", self)
        self.act_strike.setCheckable(True)
        self.act_strike.triggered.connect(self.toggle_strikethrough)
        self.format_toolbar.addAction(self.act_strike)
        self.format_toolbar.addSeparator()

        self.color_swatch = QToolButton(self)
        self.color_swatch.setObjectName("colorSwatch")
        self.color_swatch.setFixedSize(22, 22)
        self.color_swatch.setCursor(Qt.PointingHandCursor)
        self.color_swatch.clicked.connect(self.choose_color)

        self.btn_color = QToolButton(self)
        self.btn_color.setText("Color")
        self.btn_color.setToolTip("Change text color")
        self.btn_color.clicked.connect(self.choose_color)

        color_box = QtWidgets.QWidget(self)
        color_layout = QHBoxLayout(color_box)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(6)
        color_layout.addWidget(self.color_swatch)
        color_layout.addWidget(self.btn_color)

        self.btn_align = QToolButton(self)
        self.btn_align.setText("Align")
        self.btn_align.setPopupMode(QToolButton.InstantPopup)
        self.btn_align.setAutoRaise(True)
        self.btn_align.setToolTip("Paragraph alignment")

        menu_align = QMenu(self)
        menu_align.addAction("Left", lambda: self.set_alignment(Qt.AlignLeft))
        menu_align.addAction("Center", lambda: self.set_alignment(Qt.AlignCenter))
        menu_align.addAction("Right", lambda: self.set_alignment(Qt.AlignRight))
        menu_align.addAction("Justify", lambda: self.set_alignment(Qt.AlignJustify))
        self.btn_align.setMenu(menu_align)

        self.btn_spacing = QToolButton(self)
        self.btn_spacing.setText("Line Spacing")
        self.btn_spacing.setPopupMode(QToolButton.InstantPopup)
        self.btn_spacing.setAutoRaise(True)
        self.btn_spacing.setToolTip("Paragraph line spacing")

        sp_menu = QMenu(self)
        sp_menu.addAction("Single (1.0)", lambda: self.set_line_spacing(1.0))
        sp_menu.addAction("1.15", lambda: self.set_line_spacing(1.15))
        sp_menu.addAction("1.5", lambda: self.set_line_spacing(1.5))
        sp_menu.addAction("Double (2.0)", lambda: self.set_line_spacing(2.0))
        sp_menu.addSeparator()
        sp_menu.addAction("2.5", lambda: self.set_line_spacing(2.5))
        sp_menu.addAction("3.0", lambda: self.set_line_spacing(3.0))
        self.btn_spacing.setMenu(sp_menu)

        self.btn_lists = QToolButton(self)
        self.btn_lists.setText("Lists")
        self.btn_lists.setPopupMode(QToolButton.InstantPopup)
        self.btn_lists.setAutoRaise(True)
        self.btn_lists.setToolTip("Lists and indentation")

        list_menu = QMenu(self)
        list_menu.addAction("Bullet list", self.insert_bullet_list)
        list_menu.addAction("Numbered list", self.insert_number_list)
        list_menu.addSeparator()
        list_menu.addAction("Remove list", self.clear_list_format)
        list_menu.addAction("Indent", self.indent_selection)
        list_menu.addAction("Outdent", self.outdent_selection)
        self.btn_lists.setMenu(list_menu)

        self.btn_links = QToolButton(self)
        self.btn_links.setText("Links")
        self.btn_links.setPopupMode(QToolButton.InstantPopup)
        self.btn_links.setAutoRaise(True)

        self.link_menu = QMenu(self)
        self.act_add_edit_link = self.link_menu.addAction("Add / Edit Link", self.add_or_edit_link)
        self.act_add_edit_link.setShortcut(QKeySequence("Ctrl+K"))
        self.act_remove_link = self.link_menu.addAction("Remove Link", self.remove_link)
        self.act_remove_link.setShortcut(QKeySequence("Ctrl+Shift+K"))
        self.btn_links.setMenu(self.link_menu)
        self.addAction(self.act_add_edit_link)
        self.addAction(self.act_remove_link)

        self.format_toolbar.addWidget(color_box)
        self.format_toolbar.addSeparator()
        self.format_toolbar.addWidget(self.btn_align)
        self.format_toolbar.addWidget(self.btn_spacing)
        self.format_toolbar.addWidget(self.btn_lists)
        self.format_toolbar.addSeparator()
        self.format_toolbar.addWidget(self.btn_links)
        self.format_toolbar.addSeparator()

        self.act_clear = QAction("Clear Formatting", self)
        self.act_clear.triggered.connect(self.clear_formatting)
        self.format_toolbar.addAction(self.act_clear)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            "QDialog#EditorDialog{background:#111318;border:1px solid #2b3038;border-radius:14px;}"
            "QWidget#editorToolbarHost{background:transparent;}"
            "QToolBar{background:#181b20;border:1px solid #2b3038;border-radius:8px;margin:0px;padding:4px 6px;spacing:4px;}"
            "QToolBar::separator{background:#2b3038;width:1px;margin:6px 4px;}"
            "QTextEdit{background:#0f1115;color:#edf1f7;border:1px solid #2b3038;border-radius:8px;padding:10px;selection-background-color:#2f6fed;}"
            "QLabel{color:#c5ccd6;}"
            "QSplitter::handle{background:#1b1f25;}"
            "QPushButton{background:#1c2128;color:#f2f4f8;border:1px solid #303744;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#263244;border-color:#4d6b95;}"
            "QPushButton#toolbarMiniButton{padding:0px;font-size:14px;min-width:28px;max-width:28px;min-height:28px;max-height:28px;}"
            "QSpinBox{background:#181b20;color:#edf1f7;border:1px solid #313845;border-radius:6px;padding:2px 8px;min-height:28px;}"
            "QFontComboBox{background:#181b20;color:#edf1f7;border:1px solid #313845;border-radius:6px;min-height:28px;}"
            "QMenu{background:#14171c;color:#edf1f7;border:1px solid #2b3038;}"
            "QToolButton{color:#e6ebf2;padding:5px 10px;border:1px solid transparent;border-radius:6px;background:transparent;}"
            "QToolButton:hover{background:#222831;border-color:#374355;}"
            "QToolButton:checked{background:#273142;border-color:#4f78aa;}"
            "QToolButton#colorSwatch{min-width:22px;max-width:22px;min-height:22px;max-height:22px;padding:0;}"
            "QCheckBox{color:#ddd;}"
        )

    def _restore_geometry(self) -> None:
        geom = self.settings.value(SETTINGS_KEY_GEOMETRY)
        if isinstance(geom, QtCore.QByteArray):
            self.restoreGeometry(geom)
        self._normal_geometry = None if self.isMaximized() else self.geometry()

    def _install_editor_services(self) -> None:
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self.spell_highlighter = SpellCheckHighlighter(self.editor.document(), self.spell_checker)

    def _current_editor_html(self) -> str:
        return self.get_edited_html()

    def _set_dirty_state(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        self._sync_window_title()

    def _sync_window_title(self) -> None:
        prefix = "* " if self._dirty else ""
        title = f"{prefix}{self.base_title}"
        self.setWindowTitle(title)
        if hasattr(self, "title_bar"):
            self.title_bar.title_label.setText(title)

    def _on_editor_text_changed(self) -> None:
        self._set_dirty_state(self._current_editor_html() != self._last_saved_html)
        if self._dirty:
            self.autosave_timer.start()
        else:
            self.autosave_timer.stop()

    def _remove_autosave_draft(self) -> None:
        try:
            if self.autosave_path.exists():
                self.autosave_path.unlink()
        except Exception:
            pass

    def _write_autosave_draft(self) -> None:
        if not self._dirty:
            self._remove_autosave_draft()
            return

        try:
            _atomic_write(self.autosave_path, self._current_editor_html(), encoding="utf-8")
        except Exception:
            pass

    def _maybe_restore_autosave_draft(self) -> None:
        if not self.autosave_path.exists():
            return

        try:
            draft_html = self.autosave_path.read_text(encoding="utf-8")
        except Exception:
            return

        if not draft_html or draft_html == self._last_saved_html:
            self._remove_autosave_draft()
            return

        answer = QMessageBox.question(
            self,
            "Restore Draft",
            "An autosaved draft was found. Restore it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.editor.setHtml(draft_html)
            self._set_dirty_state(True)
        else:
            self._remove_autosave_draft()

    def _save_current_document(self) -> bool:
        content = self._current_editor_html()
        export_content = self._inject_export_line_spacing_wrapper(content)
        try:
            _atomic_write(self.message_path, export_content, encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save:\n{type(exc).__name__}: {exc}")
            return False

        self._last_saved_html = content
        self.message_html = content
        self._set_dirty_state(False)
        self.autosave_timer.stop()
        self._remove_autosave_draft()
        return True

    def _confirm_close(self) -> bool:
        if not self._dirty:
            self._remove_autosave_draft()
            return True

        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            "Save your changes before closing the editor?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            saved = self._save_current_document()
            if saved:
                self.setResult(QDialog.Accepted)
            return saved
        if answer == QMessageBox.Discard:
            self.autosave_timer.stop()
            self._remove_autosave_draft()
            self.setResult(QDialog.Rejected)
            return True
        return False

    def _toggle_max_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
        else:
            self._normal_geometry = self.geometry()
            self.showMaximized()
        self._sync_title_bar()

    def _sync_title_bar(self) -> None:
        if hasattr(self, "title_bar"):
            self.title_bar.sync_window_state()

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.WindowStateChange:
            self._sync_title_bar()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.settings.setValue(SETTINGS_KEY_GEOMETRY, self.saveGeometry())
        except Exception:
            pass

        if not self._confirm_close():
            event.ignore()
            return

        self._sync_title_bar()
        super().closeEvent(event)

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Public API
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get_edited_html(self) -> str:
        return self.editor.toHtml()

    def apply_changes(self) -> None:
        """
        Save to message.html, but force browser to match spacing chosen in editor by
        injecting a wrapper with inline line-height.
        """
        if self._save_current_document():
            self.accept()

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
        existing_wrapper = re.compile(
            r'<div\b[^>]*class=(["\'])(?=[^"\']*\bls-linewrap\b)[^"\']*\1[^>]*>',
            re.IGNORECASE,
        )
        if existing_wrapper.search(inner):
            new_inner = existing_wrapper.sub(wrapper_open, inner, count=1)
            return html[:body_tag_end + 1] + new_inner + html[body_close:]

        new_inner = wrapper_open + inner + wrapper_close
        return html[:body_tag_end + 1] + new_inner + html[body_close:]

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Commands
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def insert_salutation(self) -> None:
        if self.editor.document().firstBlock().text().strip() == f"Dear {self.recipient_name},":
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.editor.setTextCursor(cursor)
            return

        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)

        fmt = QTextCharFormat()
        fmt.setFontFamily("Parchment")
        fmt.setFontPointSize(48.0)

        cursor.insertText(f"Dear {self.recipient_name},", fmt)
        cursor.insertBlock()
        self.editor.setTextCursor(cursor)

    def open_find_replace(self) -> None:
        FindReplaceDialog(self).exec()

    def _context_word_cursor(self, pos: QtCore.QPoint) -> Optional[tuple[QTextCursor, str]]:
        cursor = self.editor.cursorForPosition(pos)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText().strip()
        if not self.spell_checker.is_candidate_word(word):
            return None
        return cursor, word

    def _replace_word_cursor(self, cursor: QTextCursor, replacement: str) -> None:
        self.editor.setTextCursor(cursor)
        fmt = cursor.charFormat()
        cursor.insertText(replacement, fmt)
        self.editor.setTextCursor(cursor)

    def _show_editor_context_menu(self, pos: QtCore.QPoint) -> None:
        menu = self.editor.createStandardContextMenu()
        first_action = menu.actions()[0] if menu.actions() else None
        word_info = self._context_word_cursor(pos)

        if self.spell_checker.available and word_info is not None:
            word_cursor, word = word_info
            if not self.spell_checker.is_correct(word):
                for suggestion in self.spell_checker.suggestions(word):
                    action = QAction(suggestion, self)
                    action.triggered.connect(
                        lambda _checked=False, cur=QTextCursor(word_cursor), repl=suggestion: self._replace_word_cursor(cur, repl)
                    )
                    if first_action is not None:
                        menu.insertAction(first_action, action)
                    else:
                        menu.addAction(action)

                add_word_action = QAction("Add to Dictionary", self)
                add_word_action.triggered.connect(
                    lambda _checked=False, misspelled=word: self.add_word_to_dictionary(misspelled)
                )
                if first_action is not None:
                    menu.insertAction(first_action, add_word_action)
                    menu.insertSeparator(first_action)
                else:
                    menu.addAction(add_word_action)
                    menu.addSeparator()

        self._update_link_actions()
        menu.addSeparator()
        menu.addAction(self.act_paste_plain)
        menu.addAction(self.act_add_edit_link)
        menu.addAction(self.act_remove_link)
        menu.addAction(self.act_help)
        menu.exec(self.editor.mapToGlobal(pos))

    def paste_as_plain_text(self) -> None:
        text = QtWidgets.QApplication.clipboard().text()
        if text:
            self.editor.textCursor().insertText(text)

    def add_word_to_dictionary(self, word: str) -> None:
        self.spell_checker.add_word(word)
        if self.spell_highlighter is not None:
            self.spell_highlighter.rehighlight()

    def show_shortcuts_help(self) -> None:
        QMessageBox.information(
            self,
            "Editor Help",
            "\n".join(
                [
                    "Ctrl+B: Bold",
                    "Ctrl+I: Italic",
                    "Ctrl+U: Underline",
                    "Ctrl+K: Add or edit link",
                    "Ctrl+Shift+K: Remove link",
                    "Ctrl+F: Find / Replace",
                    "Ctrl+Shift+V: Paste without formatting",
                    "Ctrl+S: Save",
                    "F1: Show this help",
                ]
            ),
        )

    def set_alignment(self, alignment: Qt.AlignmentFlag) -> None:
        self.editor.setAlignment(alignment)

    def _apply_to_selected_blocks(self, updater: Callable[[QTextBlockFormat], None]) -> None:
        cursor = self.editor.textCursor()
        doc = self.editor.document()
        start = cursor.selectionStart() if cursor.hasSelection() else cursor.position()
        end = cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
        end_pos = max(start, end - 1) if cursor.hasSelection() else start

        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        try:
            block = doc.findBlock(start)
            end_block = doc.findBlock(end_pos)
            while block.isValid():
                block_cursor = QTextCursor(block)
                block_format = block_cursor.blockFormat()
                updater(block_format)
                block_cursor.setBlockFormat(block_format)
                if block == end_block:
                    break
                block = block.next()
        finally:
            edit_cursor.endEditBlock()

        self.preview.update()
        self._sync_current_format_state()

    def indent_selection(self) -> None:
        self._apply_to_selected_blocks(lambda block_format: block_format.setIndent(block_format.indent() + 1))

    def outdent_selection(self) -> None:
        self._apply_to_selected_blocks(lambda block_format: block_format.setIndent(max(0, block_format.indent() - 1)))

    def clear_list_format(self) -> None:
        def updater(block_format: QTextBlockFormat) -> None:
            block_format.setObjectIndex(-1)
            block_format.setIndent(max(0, block_format.indent() - 1))

        self._apply_to_selected_blocks(updater)

    def _link_target_cursor(self) -> Optional[QTextCursor]:
        cursor = QTextCursor(self.editor.textCursor())
        if cursor.hasSelection():
            return cursor

        cursor.select(QTextCursor.WordUnderCursor)
        return cursor if cursor.hasSelection() else None

    def _current_link_href(self) -> str:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            formats = self._selected_formats()
            hrefs = {fmt.anchorHref() for fmt in formats if fmt.isAnchor() and fmt.anchorHref()}
            return next(iter(hrefs)) if len(hrefs) == 1 else ""

        fmt = self.editor.currentCharFormat()
        return fmt.anchorHref() if fmt.isAnchor() else ""

    def _update_link_actions(self) -> None:
        has_link = bool(self._current_link_href()) or any(fmt.isAnchor() for fmt in self._selected_formats())
        self.act_add_edit_link.setEnabled(self._link_target_cursor() is not None)
        self.act_remove_link.setEnabled(has_link)

    def add_or_edit_link(self) -> None:
        cursor = self._link_target_cursor()
        if cursor is None:
            QMessageBox.information(self, "Link", "Select text or place the cursor on a word first.")
            return

        current_href = self._current_link_href() or "https://"
        href, ok = QInputDialog.getText(self, "Add or Edit Link", "URL", text=current_href)
        if not ok:
            return

        href = href.strip()
        if not href:
            return
        if "://" not in href and not href.startswith("mailto:"):
            href = f"https://{href}"

        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(href)
        fmt.setFontUnderline(True)
        self.editor.setTextCursor(cursor)
        self._apply_char_format(fmt)
        self._update_link_actions()

    def remove_link(self) -> None:
        cursor = self._link_target_cursor()
        if cursor is None:
            return

        fmt = QTextCharFormat()
        fmt.setAnchor(False)
        fmt.setAnchorHref("")
        self.editor.setTextCursor(cursor)
        self._apply_char_format(fmt)
        self._update_link_actions()

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Formatting
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _clamp_font_size(self, size: float) -> int:
        try:
            size_f = float(size)
        except (TypeError, ValueError):
            size_f = float(DEFAULT_FONT_SIZE)
        return int(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, round(size_f))))

    def _default_point_size(self) -> int:
        doc_font = self.editor.document().defaultFont()
        doc_size = doc_font.pointSizeF()
        if doc_size and doc_size > 0:
            return self._clamp_font_size(doc_size)

        editor_size = self.editor.font().pointSizeF()
        if editor_size and editor_size > 0:
            return self._clamp_font_size(editor_size)

        return DEFAULT_FONT_SIZE

    def _effective_font_point_size(self, fmt: QTextCharFormat) -> int:
        point_size = fmt.fontPointSize()
        if point_size and point_size > 0:
            return self._clamp_font_size(point_size)

        font_size = fmt.font().pointSizeF()
        if font_size and font_size > 0:
            return self._clamp_font_size(font_size)

        return self._default_point_size()

    def _effective_font_family(self, fmt: QTextCharFormat) -> str:
        family = fmt.font().family()
        if family:
            return family

        doc_family = self.editor.document().defaultFont().family()
        if doc_family:
            return doc_family

        return self.editor.font().family()

    def _effective_foreground_color(self, fmt: QTextCharFormat) -> QColor:
        brush = fmt.foreground()
        if brush.style() != Qt.NoBrush:
            color = brush.color()
            if color.isValid():
                return color

        current_color = self.editor.textColor()
        return current_color if current_color.isValid() else QColor("#eeeeee")

    @staticmethod
    def _is_object_replacement_text(text: str) -> bool:
        return bool(text) and all(ch == "\uFFFC" for ch in text)

    def _selected_text_fragments(self) -> list[tuple[int, int, QTextCharFormat]]:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return []

        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.editor.document()
        block = doc.findBlock(start)
        fragments: list[tuple[int, int, QTextCharFormat]] = []

        while block.isValid() and block.position() < end:
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    if self._is_object_replacement_text(fragment.text()):
                        it += 1
                        continue
                    frag_start = fragment.position()
                    frag_end = frag_start + fragment.length()
                    sel_start = max(start, frag_start)
                    sel_end = min(end, frag_end)
                    if sel_start < sel_end:
                        fragments.append((sel_start, sel_end, fragment.charFormat()))
                it += 1
            block = block.next()

        return fragments

    def _selected_formats(self) -> list[QTextCharFormat]:
        return [fmt for _, _, fmt in self._selected_text_fragments()]

    def _selection_has_mixed_font_sizes(self) -> bool:
        sizes = {self._effective_font_point_size(fmt) for fmt in self._selected_formats()}
        return len(sizes) > 1

    def _selection_has_mixed_font_families(self) -> bool:
        families = {self._effective_font_family(fmt) for fmt in self._selected_formats()}
        return len(families) > 1

    def _selection_has_mixed_colors(self) -> bool:
        colors = {self._effective_foreground_color(fmt).name() for fmt in self._selected_formats()}
        return len(colors) > 1

    def _selection_bool_state(self, getter: Callable[[QTextCharFormat], bool]) -> Optional[bool]:
        formats = self._selected_formats()
        if not formats:
            return None

        values = {bool(getter(fmt)) for fmt in formats}
        if len(values) == 1:
            return values.pop()
        return None

    def _set_action_mixed_state(
        self,
        action: QAction,
        state: Optional[bool],
        mixed_tooltip: str,
    ) -> None:
        action.setChecked(bool(state))
        action.setToolTip("" if state is not None else mixed_tooltip)

    def _set_color_swatch(self, color: Optional[QColor], *, mixed: bool = False) -> None:
        if not hasattr(self, "color_swatch"):
            return

        if mixed:
            self.color_swatch.setStyleSheet(
                "border:1px solid #4a4a4a;border-radius:4px;"
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #00d0ff,stop:1 #ff6b6b);"
            )
            self.btn_color.setToolTip("Mixed text colors selected")
            self.color_swatch.setToolTip("Mixed text colors selected")
            return

        swatch = color if color and color.isValid() else QColor("#eeeeee")
        self.color_swatch.setStyleSheet(
            f"border:1px solid #4a4a4a;border-radius:4px;background:{swatch.name()};"
        )
        self.btn_color.setToolTip(f"Current text color: {swatch.name()}")
        self.color_swatch.setToolTip(f"Current text color: {swatch.name()}")

    def _clear_char_format_patch(self, current_fmt: Optional[QTextCharFormat] = None) -> Optional[QTextCharFormat]:
        default_font = self.editor.document().defaultFont()
        default_size = default_font.pointSizeF()
        if not default_size or default_size <= 0:
            default_size = float(self._default_point_size())

        fmt = QTextCharFormat()
        fmt.setFontFamily(default_font.family())
        fmt.setFontPointSize(float(default_size))
        fmt.setFontWeight(default_font.weight())
        fmt.setFontItalic(default_font.italic())
        fmt.setFontUnderline(default_font.underline())
        fmt.setFontStrikeOut(default_font.strikeOut())
        fmt.setForeground(QBrush())
        fmt.setBackground(QBrush())
        fmt.setAnchor(False)
        fmt.setAnchorHref("")
        return fmt

    def _apply_selection_format(
        self,
        patch_builder: Callable[[QTextCharFormat], Optional[QTextCharFormat]],
    ) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return

        fragments = self._selected_text_fragments()
        if not fragments:
            return

        original_cursor = QTextCursor(cursor)
        doc = self.editor.document()
        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        try:
            for start, end, current_fmt in fragments:
                patch = patch_builder(current_fmt)
                if patch is None:
                    continue
                frag_cursor = QTextCursor(doc)
                frag_cursor.setPosition(start)
                frag_cursor.setPosition(end, QTextCursor.KeepAnchor)
                frag_cursor.mergeCharFormat(patch)
        finally:
            edit_cursor.endEditBlock()

        self.editor.setTextCursor(original_cursor)
        self.preview.update()
        self._sync_format(self.editor.currentCharFormat())

    def _sync_current_format_state(self) -> None:
        self._sync_format(self.editor.currentCharFormat())

    def _apply_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            self._apply_selection_format(lambda _current: fmt)
            return

        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setCurrentCharFormat(self.editor.currentCharFormat())

    def _toggle_weight(self, wt: int) -> None:
        if self.editor.textCursor().hasSelection():
            all_bold = self._selection_bool_state(lambda fmt: fmt.fontWeight() >= wt)
            target_weight = QFont.Normal if all_bold is True else wt
        else:
            current_weight = self.editor.currentCharFormat().fontWeight()
            target_weight = QFont.Normal if current_weight == wt else wt
        fmt = QTextCharFormat()
        fmt.setFontWeight(target_weight)
        self._apply_char_format(fmt)

    def set_font_family(self, font: QFont) -> None:
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        self._apply_char_format(fmt)

    def _commit_font_size_input(self) -> None:
        current_text = self.font_size_spin.lineEdit().text().strip()
        if self._font_size_mixed_display and current_text.lower() == "mixed":
            self.editor.setFocus()
            return

        self._font_size_mixed_display = False
        self.font_size_spin.interpretText()
        self.set_font_size(self.font_size_spin.value())
        self.editor.setFocus()

    def _step_font_size(self, delta: int) -> None:
        if self.editor.textCursor().hasSelection():
            delta_i = int(delta)
            self._apply_selection_format(
                lambda current_fmt: self._font_size_patch(
                    self._effective_font_point_size(current_fmt) + delta_i
                )
            )
            self.editor.setFocus()
            return

        current = self.font_size_spin.value()
        target = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, current + int(delta)))
        if target == current:
            self.set_font_size(target)
        else:
            self.font_size_spin.setValue(target)
        self.editor.setFocus()

    def _font_size_patch(self, size: float) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(self._clamp_font_size(size)))
        return fmt

    def set_font_size(self, size: int) -> None:
        size_i = self._clamp_font_size(size)
        if self.font_size_spin.value() != size_i:
            self.font_size_spin.blockSignals(True)
            try:
                self.font_size_spin.setValue(size_i)
            finally:
                self.font_size_spin.blockSignals(False)

        self._apply_char_format(self._font_size_patch(size_i))

    def toggle_bold(self) -> None:
        self._toggle_weight(QFont.Bold)

    def toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        if self.editor.textCursor().hasSelection():
            target = self._selection_bool_state(lambda current_fmt: current_fmt.fontItalic()) is not True
        else:
            target = not self.editor.currentCharFormat().fontItalic()
        fmt.setFontItalic(target)
        self._apply_char_format(fmt)

    def toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        if self.editor.textCursor().hasSelection():
            target = self._selection_bool_state(lambda current_fmt: current_fmt.fontUnderline()) is not True
        else:
            target = not self.editor.currentCharFormat().fontUnderline()
        fmt.setFontUnderline(target)
        self._apply_char_format(fmt)

    def toggle_strikethrough(self) -> None:
        fmt = QTextCharFormat()
        if self.editor.textCursor().hasSelection():
            target = self._selection_bool_state(lambda current_fmt: current_fmt.fontStrikeOut()) is not True
        else:
            target = not self.editor.currentCharFormat().fontStrikeOut()
        fmt.setFontStrikeOut(target)
        self._apply_char_format(fmt)

    def clear_formatting(self) -> None:
        c = self.editor.textCursor()
        if c.hasSelection():
            self._apply_selection_format(lambda current_fmt: self._clear_char_format_patch(current_fmt))
        else:
            fmt = self._clear_char_format_patch()
            if fmt is not None:
                self.editor.setCurrentCharFormat(fmt)
                self.editor.mergeCurrentCharFormat(fmt)
                self._sync_current_format_state()

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

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Misc
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def update_word_count(self) -> None:
        t = self.editor.toPlainText()
        w = len([s for s in t.split() if s.strip()])
        c = len(t)
        lines = max(1, self.editor.document().blockCount())
        self.word_label.setText(f"Words: {w:,}  |  Chars: {c:,}  |  Lines: {lines:,}")

    def _sync_format(self, fmt: QTextCharFormat) -> None:
        selection_active = self.editor.textCursor().hasSelection()
        selected_formats = self._selected_formats() if selection_active else []
        mixed_family = selection_active and self._selection_has_mixed_font_families()
        mixed_size = selection_active and self._selection_has_mixed_font_sizes()
        mixed_color = selection_active and self._selection_has_mixed_colors()

        try:
            self.font_combo.blockSignals(True)
            self.font_size_spin.blockSignals(True)
            self.act_bold.blockSignals(True)
            self.act_italic.blockSignals(True)
            self.act_underline.blockSignals(True)
            self.act_strike.blockSignals(True)

            if mixed_family:
                self.font_combo.setCurrentIndex(-1)
                self.font_combo.setToolTip("Mixed font families selected")
            else:
                family = self._effective_font_family(selected_formats[0]) if selected_formats else self._effective_font_family(fmt)
                self.font_combo.setCurrentFont(QFont(family))
                self.font_combo.setToolTip("")

            if mixed_size:
                self._font_size_mixed_display = True
                self.font_size_spin.setSpecialValueText("Mixed")
                self.font_size_spin.setValue(FONT_SIZE_MIN)
                self.font_size_spin.setToolTip("Mixed font sizes selected")
            else:
                self._font_size_mixed_display = False
                self.font_size_spin.setSpecialValueText("")
                if selected_formats:
                    sz = self._effective_font_point_size(selected_formats[0])
                else:
                    sz = self._effective_font_point_size(fmt)
                self.font_size_spin.setValue(max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, sz)))
                self.font_size_spin.setToolTip("")

            if mixed_color:
                self._set_color_swatch(None, mixed=True)
            else:
                active_color = self._effective_foreground_color(selected_formats[0]) if selected_formats else self._effective_foreground_color(fmt)
                self._set_color_swatch(active_color)

            if selection_active:
                bold_state = self._selection_bool_state(lambda current_fmt: current_fmt.fontWeight() >= QFont.Bold)
                italic_state = self._selection_bool_state(lambda current_fmt: current_fmt.fontItalic())
                underline_state = self._selection_bool_state(lambda current_fmt: current_fmt.fontUnderline())
                strike_state = self._selection_bool_state(lambda current_fmt: current_fmt.fontStrikeOut())
            else:
                bold_state = fmt.fontWeight() >= QFont.Bold
                italic_state = fmt.fontItalic()
                underline_state = fmt.fontUnderline()
                strike_state = fmt.fontStrikeOut()

            self._set_action_mixed_state(self.act_bold, bold_state, "Mixed bold selection")
            self._set_action_mixed_state(self.act_italic, italic_state, "Mixed italic selection")
            self._set_action_mixed_state(self.act_underline, underline_state, "Mixed underline selection")
            self._set_action_mixed_state(self.act_strike, strike_state, "Mixed strike selection")
            self._update_link_actions()
        finally:
            self.font_combo.blockSignals(False)
            self.font_size_spin.blockSignals(False)
            self.act_bold.blockSignals(False)
            self.act_italic.blockSignals(False)
            self.act_underline.blockSignals(False)
            self.act_strike.blockSignals(False)

