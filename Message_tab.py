# ===============================
# File: Message_tab.py
# Purpose: Message authoring + render to message.png (no visible wall/cperp toolbar)
# Notes:
#   • The two visible toolbar buttons were removed on purpose.
#   • Hidden buttons managed by Over_Nexus.py still work:
#       - Clarifier/wall (slot 4) is handled elsewhere.
#       - cperp/Title helper toggles this tab's title_sister_container via toggle_title_sister_area().
#   • Micro improvements:
#       - Safer reads/writes (atomic write for message.html and message.png).
#       - Clearer status updates, early guards, and type hints.
#       - Robust defaults: current_html exists even when nothing is loaded yet.
#       - Tiny UX polish on drag/drop and file selection.
#       - Optional library fallbacks (DOCX/PDF/ODT) so the tab never hard-crashes.
#   • Update:
#       - Removed the "Open message.html" link (not needed; previews already exist).
#       - Kept auto-save when the editor closes (Accepted or Cancelled).
# ===============================

from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path
from typing import Optional

# Optional converters (prefer mammoth for DOCX → HTML)
try:
    import mammoth  # type: ignore
except Exception:  # pragma: no cover
    mammoth = None

try:
    from docx import Document  # type: ignore
except Exception:  # pragma: no cover
    Document = None  # type: ignore

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:  # pragma: no cover
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

from Editor import Editor  # our rich-text editor dialog
from config import (
    GALLERY_DIR,
    SETTINGS_FILE,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write_text(path: str | os.PathLike, data: str) -> None:
    """Write text atomically (best effort): path.tmp → replace(path)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        os.replace(tmp, p)
    except Exception:
        # Fallback if replace fails on some FS
        p.write_text(data, encoding="utf-8")


def _atomic_save_image(img: QImage, path: str) -> bool:
    """Atomic save for images: path.tmp → replace(path)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    if not img.save(tmp):
        return False
    try:
        os.replace(tmp, str(p))
        return True
    except Exception:
        return img.save(str(p))


def _strip_html_for_word_count(html: str) -> str:
    """Very lightweight tag stripper (for counting words)."""
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
# Main Message Tab (NO visible wall/cperp toolbar here)
# ─────────────────────────────────────────────────────────────────────────────

class MessageTab(QtWidgets.QWidget):
    text_selected = Signal(str)
    preview_image = Signal(QPixmap)
    wall_preview = Signal(QPixmap)  # reserved for Nexus overlay previews

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self.project_root = project_root
        self.settings_path = os.path.join(project_root, SETTINGS_FILE)
        self.current_html: str = ""  # always defined

        self._load_settings()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header
        header = QtWidgets.QLabel("Your Letter’s Message")
        header.setFont(QFont("Lucida Handwriting", 14))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Title/Recipient container — toggled by the hidden "cperp" button from Over_Nexus.py
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

        # Select / drop message file
        self.btn = DropMessageButton()
        self.btn.clicked.connect(self.select_file)
        self.btn.file_dropped.connect(self.handle_drop)
        layout.addWidget(self.btn)

        # Status + action buttons
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

        self.open_root_btn = QtWidgets.QPushButton("📂 Open Project Folder")
        self.open_root_btn.setFont(QFont("Lucida Handwriting", 11))
        self.open_root_btn.setStyleSheet(
            "QPushButton { background-color:#1c1e26; border:1px solid #00d0ff;"
            " border-radius:8px; padding:8px; color:#e6e6e6;}"
            "QPushButton:hover { background-color:#00d0ff; color:#0e0f12;}"
        )
        self.open_root_btn.clicked.connect(self.open_project_folder)
        layout.addWidget(self.open_root_btn)

        self._check_existing()

    # ──────────────────────────────────────────────────────────────────
    # Hidden-button integration (Over_Nexus.py calls this)
    # ──────────────────────────────────────────────────────────────────

    def toggle_title_sister_area(self) -> None:
        self.title_sister_container.setVisible(not self.title_sister_container.isVisible())

    # Nexus double-click compatibility wrapper (no UI change)
    def open_message_editor(self) -> None:
        self.open_editor()

    # ──────────────────────────────────────────────────────────────────
    # Core actions
    # ──────────────────────────────────────────────────────────────────

    def open_project_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.project_root))

    def _load_settings(self) -> None:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                self.settings = QtCore.QJsonDocument.fromJson(f.read().encode("utf-8")).object()  # type: ignore
        except Exception:
            # Fallback: plain json
            import json
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
            import json
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
            self.status.setText("💾 Recipient info saved.")
        except Exception as e:
            self.status.setText(f"❌ Error saving settings: {e}")

    def _check_existing(self) -> None:
        html_path = os.path.join(self.project_root, MESSAGE_HTML_FILE)
        png_path = os.path.join(self.project_root, MESSAGE_IMAGE_FILE)
        loaded = False

        if os.path.isfile(html_path):
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    self.current_html = f.read()
                self.edit_btn.setEnabled(True)
                self.view_btn.setEnabled(os.path.exists(png_path))
                self.status.setText("✅ Loaded existing message.html")
                loaded = True
            except Exception as e:
                self.status.setText(f"⚠️ Failed to read message.html: {e}")

        if os.path.isfile(png_path):
            full_pix = QPixmap(png_path)
            if not full_pix.isNull():
                small_pix = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(small_pix)
                self.view_btn.setEnabled(True)

        if loaded:
            self.text_selected.emit(self.current_html)

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

    def open_editor(self) -> None:
        html_for_editor = self.current_html or ""
        full_pix: Optional[QPixmap] = None

        png_path = os.path.join(self.project_root, MESSAGE_IMAGE_FILE)
        if os.path.isfile(png_path):
            candidate = QPixmap(png_path)
            if not candidate.isNull():
                full_pix = candidate

        dlg = Editor(html_for_editor, full_pix, parent=self)

        # Auto-save on ANY close (Accepted or Rejected)
        dlg.finished.connect(lambda _result: self._handle_editor_finished(dlg))
        dlg.exec()

    def _handle_editor_finished(self, dlg: QtWidgets.QDialog) -> None:
        """Autosave editor contents on close (OK or Cancel)."""
        # Try preferred API first
        new_html = None
        try:
            new_html = dlg.get_edited_html()  # type: ignore[attr-defined]
        except Exception:
            try:
                new_html = dlg.editor.toHtml()  # type: ignore[attr-defined]
            except Exception:
                pass

        if new_html is None:
            # nothing retrievable; keep current
            self.status.setText("ℹ️ Editor closed with no changes detected.")
            return

        html_path = os.path.join(self.project_root, MESSAGE_HTML_FILE)
        try:
            _atomic_write_text(html_path, new_html)
            self.current_html = new_html
            self.status.setText("💾 message.html auto-saved.")
            self.edit_btn.setEnabled(True)
        except Exception as e:
            self.status.setText(f"❌ Error saving edited HTML: {e}")
            return

        # Update Nexus HTML preview immediately
        self.text_selected.emit(new_html)

        # Re-generate image snapshot
        self._generate_image(new_html)

    def _emit_preview(self) -> None:
        png_path = os.path.join(self.project_root, MESSAGE_IMAGE_FILE)
        if os.path.exists(png_path):
            pix = QPixmap(png_path)
            if not pix.isNull():
                thumb = pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.emit(thumb)
                self.status.setText("🔍 Preview updated.")
                return
        QtWidgets.QMessageBox.warning(self, "No Image", "No message.png found to preview.")
        self.status.setText("❌ No message.png to preview.")

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
                # Fallback to python-docx → naive paragraph HTML
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
                # last resort
                return ""

            if low.endswith(".pdf"):
                # Prefer PyPDF2
                if PdfReader is not None:
                    try:
                        reader = PdfReader(path)
                        pages = [(page.extract_text() or "").replace("\n", "<br>") for page in reader.pages]
                        return "<br><br>".join(pages)
                    except Exception:
                        pass
                # Fallback to pdfplumber if available
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

        # Guard: 1000-word cap (best-effort). Counting is done on tag-stripped content.
        wc = _word_count(_strip_html_for_word_count(html))
        if wc > 1000:
            dlg = ConfirmTruncateDialog(wc)
            if dlg.exec() == QtWidgets.QDialog.Rejected:
                self.status.setText("✋ Import canceled.")
                return
            # Simple truncation by words — prioritizes safety over rich formatting
            words = re.findall(r"\b\w+\b", _strip_html_for_word_count(html))[:1000]
            html = _html.escape(" ".join(words)).replace("\n", "<br>")
            self.status.setText("✂️ Truncated to 1000 words and saved.")

        html_path = os.path.join(self.project_root, MESSAGE_HTML_FILE)
        try:
            _atomic_write_text(html_path, html)
            self.current_html = html
            self.edit_btn.setEnabled(True)
            self.status.setText(f"💾 message.html saved ({Path(path).name}).")
            self.text_selected.emit(html)
        except Exception as e:
            self.status.setText(f"❌ Error saving message.html: {e}")
            return

        self._generate_image(html)

    # ──────────────────────────────────────────────────────────────────
    # Render message.png (text over optional gallery/wall.png)
    # ──────────────────────────────────────────────────────────────────

    def _generate_image(self, html: str) -> None:
        try:
            FULL_W, FULL_H = 2048, 3072
            MARGIN_LR = 100
            MARGIN_TOP = 100
            MARGIN_BOTTOM = 100
            TEXT_WIDTH = FULL_W - 2 * MARGIN_LR
            TEXT_HEIGHT = FULL_H - MARGIN_TOP - MARGIN_BOTTOM

            canvas = QImage(FULL_W, FULL_H, QImage.Format_ARGB32)
            canvas.setDevicePixelRatio(self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0)
            canvas.fill(Qt.transparent)
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            # Optional background (gallery/wall.png)
            gallery_wall = os.path.join(self.project_root, GALLERY_DIR, "wall.png")
            if os.path.exists(gallery_wall):
                wall_img = QImage(gallery_wall).scaled(
                    FULL_W, FULL_H,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation
                )
                painter.drawImage(0, 0, wall_img)
            else:
                # Subtle dark parchment if no wall; keeps legibility consistent.
                painter.fillRect(0, 0, FULL_W, FULL_H, QtGui.QColor(14, 14, 18, 235))

            # Draw HTML text
            doc = QTextDocument()
            doc.setDefaultFont(QFont("Lucida Handwriting", 12))
            # White text, transparent background
            doc.setDefaultStyleSheet("body { color: white; background: transparent; }")
            doc.setHtml(html)
            doc.setTextWidth(TEXT_WIDTH)
            doc.setPageSize(QSizeF(TEXT_WIDTH, TEXT_HEIGHT))

            painter.save()
            painter.translate(MARGIN_LR, MARGIN_TOP)
            painter.setClipRect(0, 0, TEXT_WIDTH, TEXT_HEIGHT)
            doc.drawContents(painter, QtCore.QRectF(0, 0, TEXT_WIDTH, TEXT_HEIGHT))
            painter.restore()
            painter.end()

            png_path = os.path.join(self.project_root, MESSAGE_IMAGE_FILE)
            if not _atomic_save_image(canvas, png_path):
                raise RuntimeError("Failed to save message.png")

            self.view_btn.setEnabled(True)

            full_pix = QPixmap.fromImage(canvas)
            small_pix = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview_image.emit(small_pix)
            self.status.setText("🔍 message.png generated.")
        except Exception as e:
            self.status.setText(f"❌ Error generating message.png: {e}")
