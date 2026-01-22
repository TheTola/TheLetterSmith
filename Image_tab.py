# File: Image_tab.py
"""
Image tab UI and logic.

Intent (for maintainers)
------------------------
• Each "slot" maps to a canonical PNG filename in /gallery:
    1 → cover.png
    2 → letter.png
    3 → back.png
    4 → wall.png  (Clarifier/Text Wall; the Nexus hosts the special select button)
• We emit scaled previews (200 px width, aspect-preserved) for slots 1–3 as they are set.
• We accept drag-and-drop for convenience and give visual feedback.

Why write PNGs into /gallery?
-----------------------------
Normalize the downstream export pipeline. Other components assume canonical PNG names
exist in /gallery. This module always writes/rewrites *true PNG* files to those names —
no mismatched extensions.

Cover side effects
------------------
When cover.png is updated, we automatically generate:
  • gallery/icon/cover.ico      : 256×256 ICO built from cover
  • gallery/pic.png             : cover composited *under* gallery/frame.png (if present)
  • gallery/icon/pic.ico        : 256×256 ICO built from pic.png (if created)
  • gallery/icon/covered.ico    : pic.png overlaid with gallery/zip.png (if present), 256×256 ICO

Notes
-----
• PNG writing is EXIF-aware (rotated images are auto-corrected).
• Non-PNG inputs (JPG/BMP, etc.) are converted to PNG; alpha preserved when present.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Tuple, Optional

import shutil
from PIL import Image, ImageOps

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Signal, QUrl, QSize, QPoint, QEvent
from PySide6.QtGui import QDesktopServices, QIcon


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper: make top-level windows reasonably sized & persistent
# ─────────────────────────────────────────────────────────────────────────────
def _apply_window_defaults(w: QtWidgets.QWidget, key: str, fallback_size=(900, 700)):
    s = QtCore.QSettings("LetterSmith", "LettersmithApp")
    w.setWindowFlag(QtCore.Qt.Window, True)
    w.setMinimumSize(*fallback_size)
    try:
        geom = s.value(f"{key}_geom", b"")
        if not w.restoreGeometry(geom):
            w.resize(*fallback_size)
    except Exception:
        w.resize(*fallback_size)

    def _save_geom():
        try:
            s.setValue(f"{key}_geom", w.saveGeometry())
        except Exception:
            pass

    w.destroyed.connect(lambda _=None: _save_geom())


# ─────────────────────────────────────────────────────────────────────────────
# Drag-and-drop aware selection button
# ─────────────────────────────────────────────────────────────────────────────
class DropButton(QtWidgets.QPushButton):
    file_dropped = Signal(str)
    hovered = Signal(int)

    def __init__(self, label: str, index: int) -> None:
        super().__init__(label)
        self.index = index
        self.setFont(QtGui.QFont("Segoe UI", 11))
        self.setFixedHeight(40)
        self.setAcceptDrops(True)
        self.setToolTip("Click or drag-and-drop an image (PNG, JPG, BMP)")
        self.setStyleSheet(self._style_default())

    def enterEvent(self, event) -> None:
        self.hovered.emit(self.index)
        super().enterEvent(event)

    def _style_default(self) -> str:
        return (
            "QPushButton { background-color: #222; border: 1px solid #00d0ff; "
            "border-radius: 6px; padding: 8px; color: #eee; text-align: left; } "
            "QPushButton:hover { background-color: #00d0ff; color: #111; }"
        )

    def _style_glow(self) -> str:
        return (
            "QPushButton { background-color: #222; border: 2px solid #00ffff; "
            "border-radius: 6px; padding: 8px; color: #fff; }"
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._style_glow())

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._style_default())

    def dropEvent(self, event) -> None:
        self.setStyleSheet(self._style_default())
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                self.file_dropped.emit(path)
                break


# ─────────────────────────────────────────────────────────────────────────────
# Draggable, lockable Floating Action Button (Prompt Writer)
# Reparented to the MainWindow (Option B). **Visibility is tied to this tab**.
# Position persists via QSettings. Lock state does NOT persist (always locked).
# No "hide" command, no "reset to top-left" anywhere.
# ─────────────────────────────────────────────────────────────────────────────
class DraggableFab(QtWidgets.QToolButton):
    posChanged = Signal(QPoint)

    def __init__(self, owner_tab: QtWidgets.QWidget):
        super().__init__(owner_tab)
        self._owner_tab = owner_tab
        self._dragging = False
        self._drag_offset = QPoint()
        self._locked = True  # start locked every launch
        self._press_global = QPoint()
        self._moved = False
        self._drag_threshold = 6  # px to treat as drag

        self.setObjectName("PWriteFab")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.setFixedSize(200, 200)
        self.setStyleSheet("#PWriteFab{background:transparent; border:none; padding:0;}")

        # Small lock badge
        self._lock_badge = QtWidgets.QLabel("🔒", self)
        self._lock_badge.setStyleSheet(
            "QLabel{background:rgba(0,0,0,0.55); color:#cfe; border-radius:9px; padding:1px 4px; font:12px 'Segoe UI';}"
        )
        self._lock_badge.adjustSize()
        self._position_lock_badge()

        # Context menu
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    # — Dragging with click support —
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton and not self._locked:
            self._press_global = e.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.mapToGlobal(QPoint(0, 0))
            self._dragging = True
            self._moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._dragging and not self._locked:
            delta = e.globalPosition().toPoint() - self._press_global
            if not self._moved and (abs(delta.x()) > self._drag_threshold or abs(delta.y()) > self._drag_threshold):
                self._moved = True
            if self._moved:
                win = self.window()
                if isinstance(win, QtWidgets.QWidget):
                    new_top_left = e.globalPosition().toPoint() - self._drag_offset
                    wr = win.rect()
                    wr = QtCore.QRect(wr.x(), wr.y(), wr.width() - self.width(), wr.height() - self.height())
                    clamped = QPoint(
                        max(0, min(new_top_left.x(), wr.width())),
                        max(0, min(new_top_left.y(), wr.height()))
                    )
                    self.move(clamped)
                    self.raise_()
                    self.posChanged.emit(clamped)
                e.accept()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._moved:
                self._save_position()
                e.accept()
                return
        super().mouseReleaseEvent(e)

    # — Context Menu —
    def _show_menu(self, pt: QPoint):
        m = QtWidgets.QMenu(self)
        act_lock = m.addAction("Unlock Position" if self._locked else "Lock Position")
        chosen = m.exec(self.mapToGlobal(pt))
        if chosen == act_lock:
            self._locked = not self._locked
            self._lock_badge.setText("🔒" if self._locked else "🔓")
            self._lock_badge.adjustSize()
            self._position_lock_badge()
            self._save_position()

    # — Persistence (only position; lock starts True every run) —
    def _settings(self) -> QtCore.QSettings:
        return QtCore.QSettings("LetterSmith", "LettersmithApp")

    def _save_position(self):
        self._settings().setValue("pwrite_fab_pos", self.pos())

    def restore_position(self):
        pos = self._settings().value("pwrite_fab_pos", None)
        if isinstance(pos, QPoint):
            self.move(pos)

    def resizeEvent(self, e):
        self._position_lock_badge()
        super().resizeEvent(e)

    def _position_lock_badge(self):
        self._lock_badge.move(6, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Image utilities (EXIF-aware load, safe PNG write, compositing)
# ─────────────────────────────────────────────────────────────────────────────
def _load_image_exif(path: str) -> Image.Image:
    """Open and EXIF-transpose an image; prefer RGBA when alpha exists."""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        # Prefer RGBA for broad compatibility (preserves alpha if it appears later)
        try:
            im = im.convert("RGBA")
        except Exception:
            im = im.convert("RGB")
    return im


def _save_png(img: Image.Image, dest_png_path: str) -> None:
    Path(dest_png_path).parent.mkdir(parents=True, exist_ok=True)
    # If the image has alpha, keep RGBA, otherwise RGB
    mode = "RGBA" if img.mode == "RGBA" else "RGB"
    img.convert(mode).save(dest_png_path, format="PNG", optimize=True)


def _ico_from_image(img: Image.Image, dest_ico_path: str) -> None:
    Path(dest_ico_path).parent.mkdir(parents=True, exist_ok=True)
    # Pillow can build an ICO directly from PNG/RGBA with size hint
    img.save(dest_ico_path, format="ICO", sizes=[(256, 256)])


def _compose_under_frame(cover: Image.Image, frame: Image.Image) -> Image.Image:
    """
    Place `cover` under `frame`, centered; scale cover to *contain* within frame canvas.
    Returns the merged RGBA image (same size as frame).
    """
    fw, fh = frame.size
    merged = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
    cover_fit = ImageOps.contain(cover, (fw, fh), method=Image.LANCZOS)
    x = (fw - cover_fit.width) // 2
    y = (fh - cover_fit.height) // 2
    merged.paste(cover_fit.convert("RGBA"), (x, y), cover_fit.convert("RGBA"))
    merged.paste(frame.convert("RGBA"), (0, 0), frame.convert("RGBA"))
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Image tab proper
# ─────────────────────────────────────────────────────────────────────────────
class ImageTab(QtWidgets.QWidget):
    image_selected = Signal(QtGui.QPixmap)
    hover_preview_image = Signal(QtGui.QPixmap)

    def __init__(self) -> None:
        super().__init__()

        # Index → (human label, canonical filename)
        self.labels = {
            1: ("Cover Page Image",     "cover.png"),
            2: ("Main Letter Image",    "letter.png"),
            3: ("Final Backdrop Image", "back.png"),
            4: ("Clarifier (Text Wall)", "wall.png"),
        }
        self.image_paths: dict[int, Optional[str]] = {i: None for i in self.labels.keys()}

        # Base layout
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header = QtWidgets.QLabel("Select images for your letter")
        header.setFont(QtGui.QFont("Segoe UI Semibold", 16))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(QtCore.Qt.AlignCenter)
        glow = QtWidgets.QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(8); glow.setOffset(0, 1); glow.setColor(QtGui.QColor(0, 255, 255, 90))
        header.setGraphicsEffect(glow)
        layout.addWidget(header)

        # Primary pickers (slot 4 is owned by Nexus; placeholder keeps layout consistent)
        self.buttons: dict[int, QtWidgets.QWidget] = {}
        for idx in (1, 2, 3, 4):
            label_text, _ = self.labels[idx]
            if idx == 4:
                placeholder = QtWidgets.QWidget(self)
                placeholder.setFixedSize(0, 0)
                self.buttons[idx] = placeholder
                layout.addWidget(placeholder)
                continue

            btn = DropButton(f"  {label_text}", idx)
            btn.clicked.connect(lambda _=False, i=idx: self._pick_image_dialog(i))
            btn.file_dropped.connect(lambda p, i=idx: self._set_image_from_drop(p, i))
            btn.hovered.connect(self.preview_from_gallery)
            self.buttons[idx] = btn
            layout.addWidget(btn)

        # Utilities row
        btn_row = QtWidgets.QHBoxLayout()
        self.reset_btn = QtWidgets.QPushButton("🔄 Reset Image Selection")
        self.reset_btn.setFont(QtGui.QFont("Segoe UI Semibold", 11))
        self.reset_btn.setStyleSheet(
            "QPushButton { background-color:#222; color:#eee; border:1px solid #00d0ff; }"
            "QPushButton:hover { background-color:#00d0ff; color:#111; }"
        )
        self.reset_btn.clicked.connect(self.reset_images)
        btn_row.addWidget(self.reset_btn)

        self.open_btn = QtWidgets.QPushButton("📂 Open Gallery Folder")
        self.open_btn.setFont(QtGui.QFont("Segoe UI Semibold", 11))
        self.open_btn.setStyleSheet(
            "QPushButton { background-color:#222; color:#eee; border:1px solid #00d0ff; }"
            "QPushButton:hover { background-color:#00d0ff; color:#111; }"
        )
        self.open_btn.clicked.connect(self.open_gallery_folder)
        btn_row.addWidget(self.open_btn)

        layout.addLayout(btn_row)

        # Status label
        self.status = QtWidgets.QLabel()
        self.status.setFont(QtGui.QFont("Segoe UI", 10))
        self.status.setStyleSheet("color:#bbb;")
        layout.addWidget(self.status)

        # ── FLOATING PROMPT WRITER BUTTON (draggable, persistent) ────────────
        self.pwrite_fab = DraggableFab(self)

        # Load icon for Prompt Writer (multiple fallbacks)
        project_dir = os.path.dirname(os.path.abspath(__file__))
        pwrite_paths = [
            os.path.join(project_dir, "gallery", "icons", "pwrite.png"),
            os.path.join(project_dir, "gallery", "icon",  "pwrite.png"),
            os.path.join(project_dir, "gallery", "icons", "PWRITE.png"),
        ]
        icon = None
        for p in pwrite_paths:
            if os.path.exists(p):
                icon = QIcon(p)
                break
        if icon:
            self.pwrite_fab.setIcon(icon)
            self.pwrite_fab.setIconSize(QSize(200, 200))
        else:
            self.pwrite_fab.setText("PROMPT\nWRITER")
            self.pwrite_fab.setStyleSheet(
                self.pwrite_fab.styleSheet() + " #PWriteFab{color:#00e5e5; font:700 18px 'Segoe UI';}"
            )

        # Prefer MainWindow opener; else fallback local
        self.pwrite_fab.clicked.connect(self._open_prompt_writer_bridge)
        self.pwrite_fab.hide()  # start hidden until this tab is actually shown

        # Keep FAB visibility exclusive to this page via eventFilter
        self.installEventFilter(self)

    # ── Selection paths ──────────────────────────────────────────────────────
    def _pick_image_dialog(self, idx: int) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Select {self.labels[idx][0]}",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if path:
            self.set_image_path(idx, path)

    def _set_image_from_drop(self, path: str, idx: int) -> None:
        if os.path.exists(path):
            self.set_image_path(idx, path)

    # ── Core setter (always writes a *real* PNG into /gallery) ───────────────
    def set_image_path(self, idx: int, source_path: str) -> None:
        label_text, filename = self.labels[idx]
        self.image_paths[idx] = source_path

        project_dir = os.path.dirname(os.path.abspath(__file__))
        gallery_dir = os.path.join(project_dir, "gallery")
        os.makedirs(gallery_dir, exist_ok=True)

        dest_png = os.path.join(gallery_dir, filename)

        # Load/convert → PNG
        try:
            img = _load_image_exif(source_path)
            _save_png(img, dest_png)
            if filename == "cover.png":
                self._generate_cover_derivatives(dest_png, project_dir)
        except Exception as e:
            self.status.setText(f"❌ Failed to process {filename}: {e}")
            return

        # Update UI + emit preview (slots 1–3 only)
        if idx in (1, 2, 3):
            btn = self.buttons.get(idx)
            if isinstance(btn, QtWidgets.QPushButton):
                btn.setText(f"  ✔️  {label_text}")
            pix = QtGui.QPixmap(dest_png)
            if pix.isNull():
                self.status.setText("❌ Invalid image file")
                return
            # 200 px width preview (aspect preserved)
            preview = pix.scaledToWidth(200, QtCore.Qt.SmoothTransformation)
            self.image_selected.emit(preview)
            self.status.setText(f"✅ {filename} saved; preview ready.")
        else:
            self.status.setText(f"✅ {filename} saved for Wall (no ImageTab label).")

    # ── Derived outputs from cover.png ───────────────────────────────────────
    def _generate_cover_derivatives(self, cover_png_path: str, project_dir: str) -> None:
        """
        Produce:
          • icon/cover.ico
          • pic.png  (cover under frame.png)
          • icon/pic.ico
          • icon/covered.ico  (pic + zip.png overlay)
        """
        try:
            gallery_dir = os.path.join(project_dir, "gallery")
            icon_dir = os.path.join(gallery_dir, "icon")
            os.makedirs(icon_dir, exist_ok=True)

            # Load cover
            cover_img = _load_image_exif(cover_png_path)

            # cover.ico
            _ico_from_image(cover_img, os.path.join(icon_dir, "cover.ico"))

            # Try to build pic.png if frame exists
            frame_path = os.path.join(gallery_dir, "frame.png")
            pic_png_path = os.path.join(gallery_dir, "pic.png")
            pic_img: Optional[Image.Image] = None

            if os.path.isfile(frame_path):
                try:
                    frame_img = _load_image_exif(frame_path)
                    pic_img = _compose_under_frame(cover_img, frame_img)
                    _save_png(pic_img, pic_png_path)
                except Exception as ex:
                    self.status.setText(f"⚠️ cover.ico saved; failed pic.png: {ex}")
                    pic_img = None

            # pic.ico (if we have pic.png; else we can still make it from cover)
            try:
                ico_source = pic_img if pic_img is not None else cover_img
                _ico_from_image(ico_source, os.path.join(icon_dir, "pic.ico"))
            except Exception as ex:
                # Non-fatal: icon is a convenience
                self.status.setText(f"⚠️ pic.ico failed: {ex}")

            # covered.ico (overlay zip.png on pic/base if available)
            try:
                zip_path = os.path.join(gallery_dir, "zip.png")
                if os.path.isfile(zip_path):
                    base = pic_img if pic_img is not None else cover_img.copy()
                    zip_img = _load_image_exif(zip_path).convert("RGBA")
                    # center overlay
                    bw, bh = base.size
                    zw, zh = zip_img.size
                    over = base.convert("RGBA")
                    over.paste(zip_img, ((bw - zw)//2, (bh - zh)//2), zip_img)
                    _ico_from_image(over, os.path.join(icon_dir, "covered.ico"))
                    self.status.setText("✅ cover.ico, pic.png, pic.ico, and covered.ico saved.")
                    return
            except Exception as ex:
                self.status.setText(f"⚠️ covered.ico failed: {ex}")

            # If we got here with no errors, summarize:
            if os.path.isfile(pic_png_path):
                self.status.setText("✅ cover.ico, pic.png, and pic.ico saved.")
            else:
                self.status.setText("✅ cover.ico saved. (No frame.png for pic.png)")
        except Exception as e:  # pragma: no cover
            self.status.setText(f"❌ Error generating cover derivatives: {e}")

    # ── Hover preview (non-destructive) ──────────────────────────────────────
    def preview_from_gallery(self, idx: int) -> None:
        if idx == 4:
            return
        _, filename = self.labels[idx]
        project_dir = os.path.dirname(os.path.abspath(__file__))
        gallery_dir = os.path.join(project_dir, "gallery")
        full_path = os.path.join(gallery_dir, filename)
        if os.path.exists(full_path):
            pix = QtGui.QPixmap(full_path)
            if not pix.isNull():
                prev = pix.scaledToWidth(200, QtCore.Qt.SmoothTransformation)
                self.hover_preview_image.emit(prev)

    # ── Utilities ────────────────────────────────────────────────────────────
    def reset_images(self) -> None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        gallery_dir = os.path.join(project_dir, "gallery")
        for i in (1, 2, 3):
            self.image_paths[i] = None
            label_text, filename = self.labels[i]
            btn = self.buttons.get(i)
            if isinstance(btn, QtWidgets.QPushButton):
                btn.setText(f"  {label_text}")
            file_path = os.path.join(gallery_dir, filename)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        self.status.setText("🧹 Cleared Cover, Letter, and Back PNGs.")

    def open_gallery_folder(self) -> None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        gallery_dir = os.path.join(project_dir, "gallery")
        os.makedirs(gallery_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(gallery_dir))

    # ── Prompt Writer bridge (prefer MainWindow/Nexus handler if present) ────
    def _open_prompt_writer_bridge(self):
        try:
            win = self.window()
            opener = getattr(win, "open_prompt_writer", None)
            if callable(opener):
                opener()
                self.status.setText("Prompt Writer opened (via Main Window).")
                return
        except Exception:
            pass
        self.open_prompt_writer()  # fallback

    # ── Prompt Writer (local fallback) ───────────────────────────────────────
    def open_prompt_writer(self):
        win = getattr(self, "_prompt_writer_win", None)
        if win is not None:
            try:
                win.showNormal()
                win.raise_()
                win.activateWindow()
                self.status.setText("Prompt Writer focused.")
                return
            except Exception:
                self._prompt_writer_win = None

        try:
            import inspect
            import PromptWriterPanel as PWM
            cls = getattr(PWM, "PromptWriterPanel", None)
            if cls is None:
                for name, obj in vars(PWM).items():
                    if inspect.isclass(obj) and issubclass(obj, QtWidgets.QWidget):
                        cls = obj
                        break
            if cls is not None:
                try:
                    parent = self.window() if isinstance(self.window(), QtWidgets.QWidget) else None
                    self._prompt_writer_win = cls(parent if parent else self)
                    self._prompt_writer_win.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
                    self._prompt_writer_win.setWindowTitle("Prompt Writer")
                    _apply_window_defaults(self._prompt_writer_win, "pwriter")

                    # Position near the FAB if possible
                    try:
                        fab = getattr(self, "pwrite_fab", None)
                        if fab and fab.isVisible():
                            global_pt = fab.mapToGlobal(fab.rect().bottomRight())
                            self._prompt_writer_win.move(global_pt + QtCore.QPoint(24, 24))
                    except Exception:
                        pass

                    # Ultra effort seed if panel supports it
                    try:
                        ultra_effort_text = "highest level effort Ultra think and be maximum verbosity"
                        if hasattr(self._prompt_writer_win, "_data") and isinstance(self._prompt_writer_win._data, dict):
                            self._prompt_writer_win._data["effort"] = ultra_effort_text
                    except Exception:
                        pass

                    # Auto-generate if Subject already exists
                    try:
                        subj_widget = getattr(self._prompt_writer_win, "cmb_subject", None)
                        if subj_widget and subj_widget.currentText().strip():
                            self._prompt_writer_win._on_generate()
                    except Exception:
                        pass

                    self._prompt_writer_win.show()
                    self.status.setText("Prompt Writer opened.")
                    return
                except Exception as ex:
                    print(f"[PromptWriter] Failed to instantiate panel: {ex}")
        except Exception as ex:
            print(f"[PromptWriter] Import failed: {ex}")

        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PromptWriterPanel.py")
            if os.path.exists(script):
                QtCore.QProcess.startDetached(sys.executable, [script])
                self.status.setText("Prompt Writer launched (external).")
                return
        except Exception as ex:
            print(f"[PromptWriter] External launch failed: {ex}")

        self.status.setText("❌ Prompt Writer not found")

    # ── Visibility + positioning (FAB belongs to MainWindow, but only shows on this tab)
    def showEvent(self, event):
        super().showEvent(event)
        self._position_pwrite_fab()
        self._update_fab_visibility()

    def eventFilter(self, obj, ev):
        if obj is self:
            t = ev.type()
            if t in (QEvent.Show, QEvent.ShowToParent):
                self._position_pwrite_fab()
                self._update_fab_visibility()
            elif t in (QEvent.Hide, QEvent.HideToParent):
                try:
                    self.pwrite_fab.hide()
                except Exception:
                    pass
        return super().eventFilter(obj, ev)

    def resizeEvent(self, event):
        self._position_pwrite_fab()
        super().resizeEvent(event)

    def _position_pwrite_fab(self):
        """
        Option B: FAB belongs to the MainWindow; place it relative to this tab's
        visual top-left (under the TitleBar + QTabBar). Restore saved pos if any.
        """
        try:
            win = self.window()
            if not isinstance(win, QtWidgets.QWidget):
                return

            # Reparent once to the window for true overlay behavior
            if self.pwrite_fab.parent() is not win:
                self.pwrite_fab.setParent(win)
                self.pwrite_fab.restore_position()  # restore only POS (lock always starts True)

            # If position was never saved (e.g., first run), snap near this tab's top-left
            if self.pwrite_fab.pos() == QPoint(0, 0):
                top_left_in_win = self.mapTo(win, QPoint(0, 0))
                inset_x, inset_y = 8, 8
                self.pwrite_fab.move(top_left_in_win.x() + inset_x, top_left_in_win.y() + inset_y)

            self.pwrite_fab.raise_()

            # Clamp within window if needed (e.g., after resize)
            wr = win.rect()
            wr = QtCore.QRect(wr.x(), wr.y(), wr.width() - self.pwrite_fab.width(), wr.height() - self.pwrite_fab.height())
            cur = self.pwrite_fab.pos()
            clamped = QPoint(
                max(0, min(cur.x(), wr.width())),
                max(0, min(cur.y(), wr.height()))
            )
            if clamped != cur:
                self.pwrite_fab.move(clamped)
        except Exception:
            pass

    def _update_fab_visibility(self):
        """Only show the FAB while THIS tab is visible."""
        try:
            self.pwrite_fab.setVisible(self.isVisible())
        except Exception:
            pass
