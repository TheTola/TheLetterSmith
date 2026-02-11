# ===============================
# File: Message_tab.py
# Purpose: Message authoring + render to message.png
# ===============================

from __future__ import annotations

import html as _html
import os
import re
import json
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
from config import (
    SETTINGS_FILE,
    USER_PAGES_DIR,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write_text(path: str | os.PathLike, data: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        os.replace(tmp, p)
    except Exception:
        p.write_text(data, encoding="utf-8")


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


def _strip_html_for_word_count(html: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", html)
    return _html.unescape(no_tags)


def _word_count(text_like: str) -> int:
    return len(re.findall(r"\b\w+\b", text_like))


# ─────────────────────────────────────────────────────────────────────────────
# Confirm truncate dialog
# ─────────────────────────────────────────────────────────────────────────────

class ConfirmTruncateDialog(QtWidgets.QDialog):
    def __init__(self, word_count: int) -> None:
        super().__init__()
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setStyleSheet(
            "QDialog {background-color:#121318; border:2px solid #00d0ff; border-radius:12px;}"
            "QLabel {color:#e6e6e6; font-size:14px;}"
            "QPushButton {background-color:#222; color:#fff; border:1px solid #00d0ff;"
            " border-radius:6px; padding:8px 14px; min-width:72px;}"
            "QPushButton:hover {background-color:#00d0ff; color:#0c0c0c;}"
        )
        self.setFixedSize(460, 200)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        label = QtWidgets.QLabel(
            f"The selected file contains {word_count} words.\n"
            "Only the first 1000 will be kept.\n\nProceed with truncation?"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        btns = QtWidgets.QHBoxLayout()
        self.yes_btn = QtWidgets.QPushButton("Yes")
        self.no_btn = QtWidgets.QPushButton("No")
        btns.addStretch()
        btns.addWidget(self.yes_btn)
        btns.addWidget(self.no_btn)
        layout.addLayout(btns)

        self.yes_btn.clicked.connect(lambda: self.done(QtWidgets.QDialog.Accepted))
        self.no_btn.clicked.connect(lambda: self.done(QtWidgets.QDialog.Rejected))

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)


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


# ─────────────────────────────────────────────────────────────────────────────
# Main Message Tab
# ─────────────────────────────────────────────────────────────────────────────

class MessageTab(QtWidgets.QWidget):
    # Public contract used by Nexus/Over_Nexus — DO NOT REMOVE
    text_selected = Signal(str)
    preview_image = Signal(QPixmap)
    wall_preview = Signal(QPixmap)

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self.project_root = project_root
        self.settings_path = os.path.join(project_root, SETTINGS_FILE)

        # Compatibility cache (disk is authoritative)
        self.current_html: str = ""

        self._load_settings()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QtWidgets.QLabel("Your Letter’s Message")
        header.setFont(QFont("Lucida Handwriting", 14))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        self.title_sister_container = QtWidgets.QWidget(self)
        title_sister_layout = QtWidgets.QFormLayout(self.title_sister_container)
        title_sister_layout.setContentsMargins(0, 0, 0, 0)
        title_sister_layout.setSpacing(8)

        self.title_input = QtWidgets.QLineEdit(self.settings.get("recipient_title", ""))
        self.title_input.setPlaceholderText("e.g. Letter Title")
        self.name_input = QtWidgets.QLineEdit(self.settings.get("recipient_name", ""))
        self.name_input.setPlaceholderText("Name")

        title_sister_layout.addRow("Letter Title:", self.title_input)
        title_sister_layout.addRow("Sister:", self.name_input)

        self.title_input.editingFinished.connect(self._save_settings)
        self.name_input.editingFinished.connect(self._save_settings)

        layout.addWidget(self.title_sister_container)

        self.btn = DropMessageButton()
        self.btn.clicked.connect(self.select_file)
        self.btn.file_dropped.connect(self.handle_drop)
        layout.addWidget(self.btn)

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
        layout.addLayout(btns)

        # Ensure fallback assets + show something immediately
        self._check_existing()

    # ──────────────────────────────────────────────────────────────────
    # Show hook: every time user clicks into Message tab
    # ──────────────────────────────────────────────────────────────────
    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        # When user lands on the tab: force wall fallback, then show message.png if present else wall.
        self._ensure_wall_exists()
        self._ensure_message_exists()
        self._emit_best_preview()

    # ──────────────────────────────────────────────────────────────────
    # Nexus / Over_Nexus hooks
    # ──────────────────────────────────────────────────────────────────
    def toggle_title_sister_area(self) -> None:
        self.title_sister_container.setVisible(not self.title_sister_container.isVisible())

    def open_message_editor(self) -> None:
        self.open_editor()

    # ──────────────────────────────────────────────────────────────────
    # Settings
    # ──────────────────────────────────────────────────────────────────
    def _load_settings(self) -> None:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                self.settings = json.load(f)
        except Exception:
            self.settings = {}
        if not isinstance(self.settings, dict):
            self.settings = {}

    def _save_settings(self) -> None:
        self.settings["recipient_title"] = self.title_input.text().strip()
        self.settings["recipient_name"] = self.name_input.text().strip()
        try:
            Path(self.settings_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
            self.status.setText("💾 Recipient info saved.")
        except Exception as e:
            self.status.setText(f"❌ Error saving settings: {e}")

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
        - If missing, render using current_html if available,
          else render a blank message (wall-only).
        - If render fails, force message.png = wall.png (scaled).
        """
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

        if loaded_html:
            self.text_selected.emit(self.current_html)

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
        if html_path.is_file():
            try:
                html_for_editor = html_path.read_text(encoding="utf-8")
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

        dlg = Editor(html_for_editor, full_pix, parent=self)
        dlg.accepted.connect(lambda: self._handle_editor_accepted(dlg))
        dlg.exec()

    def _handle_editor_accepted(self, dlg: QtWidgets.QDialog) -> None:
        new_html: Optional[str] = None
        try:
            new_html = dlg.get_edited_html()  # type: ignore[attr-defined]
        except Exception:
            try:
                new_html = dlg.editor.toHtml()  # type: ignore[attr-defined]
            except Exception:
                new_html = None

        if not new_html:
            self.status.setText("ℹ️ Editor accepted, but HTML not Found.")
            return

        html_path = self._html_path()
        try:
            _atomic_write_text(html_path, new_html)
            self.current_html = new_html
            self.status.setText("💾 message.html saved.")
            self.edit_btn.setEnabled(True)
        except Exception as e:
            self.status.setText(f"❌ Error saving HTML: {e}")
            return

        # Always guarantee wall before rendering
        self._ensure_wall_exists()

        self.text_selected.emit(new_html)
        self._generate_image(new_html)
        self._emit_best_preview()

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

        wc = _word_count(_strip_html_for_word_count(html))
        if wc > 1000:
            dlg = ConfirmTruncateDialog(wc)
            if dlg.exec() == QtWidgets.QDialog.Rejected:
                self.status.setText("✋ Import canceled.")
                return
            words = re.findall(r"\b\w+\b", _strip_html_for_word_count(html))[:1000]
            html = _html.escape(" ".join(words)).replace("\n", "<br>")
            self.status.setText("✂️ Truncated to 1000 words and saved.")

        html_path = self._html_path()
        try:
            _atomic_write_text(html_path, html)
            self.current_html = html
            self.edit_btn.setEnabled(True)
            self.status.setText(f"💾 message.html saved ({Path(path).name}).")
            self.text_selected.emit(html)
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

            # HTML text
            doc = QTextDocument()
            doc.setDefaultFont(QFont("Lucida Handwriting", 12))

            doc.setDefaultStyleSheet(
                "body { color: #ffffff; background: transparent; }"
                "p { margin: 0 0 12px 0; }"
                "br { line-height: 1.4; }"
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
