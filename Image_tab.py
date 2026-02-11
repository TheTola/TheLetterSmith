# File: Image_tab.py
"""
Image tab UI and logic.

SOURCE OF TRUTH (pages):
  gallery/user/pages/
    cover.png
    letter.png
    wall.png
    back.png

Notes:
- Slot mapping:
    1 → cover.png
    2 → letter.png
    3 → wall.png   (Letter Background)
    4 → back.png
- We emit scaled previews (200 px width, aspect-preserved) for all 4 as they are set.
- Hover preview works for all 4.
- Reset clears preview instantly (signals).

This file writes ONLY into gallery/user/pages/*.png for the viewer pipeline.

Prompt Writer FAB:
- NOT draggable.
- Hard-locked placement mode (FAB_LOCKED) to pin it where you want.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List

from PIL import Image, ImageOps

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Signal, QUrl, QSize, QPoint
from PySide6.QtGui import QDesktopServices, QIcon


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
# Static Floating Action Button (Prompt Writer)
# ─────────────────────────────────────────────────────────────────────────────
class StaticFab(QtWidgets.QToolButton):
    SETTINGS_ORG = "LetterSmith"
    SETTINGS_APP = "LettersmithApp"
    KEY_GLOBAL_POS = "pwrite_fab_global_pos"  # stored as "x,y"

    def __init__(self, parent_widget: QtWidgets.QWidget, surface: QtWidgets.QWidget):
        super().__init__(parent_widget)
        self._surface = surface

        self.setObjectName("PWriteFab")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.setFixedSize(200, 200)
        self.setStyleSheet("#PWriteFab{background:transparent; border:none; padding:0;}")

    def set_surface(self, surf: QtWidgets.QWidget) -> None:
        self._surface = surf

    def _settings(self) -> QtCore.QSettings:
        return QtCore.QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)

    def save_global_position(self) -> None:
        gp = self.mapToGlobal(QPoint(0, 0))
        self._settings().setValue(self.KEY_GLOBAL_POS, f"{gp.x()},{gp.y()}")

    def restore_global_position(self, surface: Optional[QtWidgets.QWidget] = None) -> bool:
        surf = surface or self._surface
        raw = self._settings().value(self.KEY_GLOBAL_POS, "")
        if not isinstance(raw, str) or "," not in raw:
            return False
        try:
            xs, ys = raw.split(",", 1)
            gp = QPoint(int(xs), int(ys))
        except Exception:
            return False
        if not surf:
            return False
        self.move(surf.mapFromGlobal(gp))
        return True

    def clamp_to_surface(self) -> None:
        if not self._surface:
            return
        pad = 0
        max_x = max(pad, self._surface.width() - self.width() - pad)
        max_y = max(pad, self._surface.height() - self.height() - pad)
        x = max(pad, min(self.x(), max_x))
        y = max(pad, min(self.y(), max_y))
        self.move(QPoint(x, y))


# ─────────────────────────────────────────────────────────────────────────────
# Image utilities
# ─────────────────────────────────────────────────────────────────────────────
def _load_image_exif(path: str) -> Image.Image:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        try:
            im = im.convert("RGBA")
        except Exception:
            im = im.convert("RGB")
    return im


def _save_png(img: Image.Image, dest_png_path: str) -> None:
    Path(dest_png_path).parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if img.mode == "RGBA" else "RGB"
    img.convert(mode).save(dest_png_path, format="PNG", optimize=True)


# ─────────────────────────────────────────────────────────────────────────────
# Image tab proper
# ─────────────────────────────────────────────────────────────────────────────
class ImageTab(QtWidgets.QWidget):
    image_selected = Signal(QtGui.QPixmap)
    hover_preview_image = Signal(QtGui.QPixmap)

    # NEW: explicit clear signal for Nexus
    clear_preview = Signal()

    FAB_LOCKED = True
    FAB_FIXED_X = 55
    FAB_FIXED_Y_PAD = 28

    FAB_NUDGE_X = 0
    FAB_NUDGE_Y = 0

    FAB_TOP_PAD = 16
    FAB_RIGHT_PAD = 16

    def __init__(self) -> None:
        super().__init__()

        # Correct order: cover, letter, wall, back
        self.labels = {
            1: ("Cover Page Image", "cover.png"),
            2: ("Main Letter Image", "letter.png"),
            3: ("Letter Background Image", "wall.png"),
            4: ("Final Backdrop Image", "back.png"),
        }
        self.image_paths: dict[int, Optional[str]] = {i: None for i in self.labels.keys()}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header = QtWidgets.QLabel("Select images for your letter")
        header.setFont(QtGui.QFont("Segoe UI Semibold", 16))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(QtCore.Qt.AlignCenter)
        glow = QtWidgets.QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(8)
        glow.setOffset(0, 1)
        glow.setColor(QtGui.QColor(0, 255, 255, 90))
        header.setGraphicsEffect(glow)
        layout.addWidget(header)

        self.buttons: dict[int, QtWidgets.QWidget] = {}
        for idx in (1, 2, 3, 4):
            label_text, _ = self.labels[idx]
            btn = DropButton(f"  {label_text}", idx)
            btn.clicked.connect(lambda _=False, i=idx: self._pick_image_dialog(i))
            btn.file_dropped.connect(lambda p, i=idx: self._set_image_from_drop(p, i))
            btn.hovered.connect(self.preview_from_gallery)
            self.buttons[idx] = btn
            layout.addWidget(btn)

        btn_row = QtWidgets.QHBoxLayout()

        self.reset_btn = QtWidgets.QPushButton("🔄 Reset Images")
        self.reset_btn.setFont(QtGui.QFont("Segoe UI Semibold", 11))
        self.reset_btn.setStyleSheet(
            "QPushButton { background-color:#222; color:#eee; border:1px solid #00d0ff; }"
            "QPushButton:hover { background-color:#00d0ff; color:#111; }"
        )
        self.reset_btn.clicked.connect(self.reset_images)
        btn_row.addWidget(self.reset_btn)

        self.open_btn = QtWidgets.QPushButton("Gallery")
        self.open_btn.setFont(QtGui.QFont("Segoe UI Semibold", 11))
        self.open_btn.setStyleSheet(
            "QPushButton { background-color:#222; color:#eee; border:1px solid #00d0ff; }"
            "QPushButton:hover { background-color:#00d0ff; color:#111; }"
        )
        self.open_btn.clicked.connect(self.open_gallery_folder)
        btn_row.addWidget(self.open_btn)

        layout.addLayout(btn_row)

        self.status = QtWidgets.QLabel()
        self.status.setFont(QtGui.QFont("Segoe UI", 10))
        self.status.setStyleSheet("color:#bbb;")
        layout.addWidget(self.status)

        # Create FAB with safe temporary parent; attach on showEvent.
        self._fab_surface: QtWidgets.QWidget = self
        self.pwrite_fab = StaticFab(self, self)

        # Icon
        project_dir = os.path.dirname(os.path.abspath(__file__))
        pwrite_paths = [
            os.path.join(project_dir, "gallery", "app", "icons", "Pwrite.png"),
            os.path.join(project_dir, "gallery", "app", "icons", "pwrite.png"),
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

        self.pwrite_fab.clicked.connect(self._open_prompt_writer_bridge)
        self.pwrite_fab.show()
        self.pwrite_fab.raise_()

    # ────────────────────────────────────────────────────────────
    # Canonical paths
    # ────────────────────────────────────────────────────────────
    def _project_dir(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    def _user_pages_dir(self) -> str:
        return os.path.join(self._project_dir(), "gallery", "user", "pages")

    # ────────────────────────────────────────────────────────────
    # Preview surface discovery (legacy)
    # ────────────────────────────────────────────────────────────
    def _find_preview_surface(self) -> Optional[QtWidgets.QWidget]:
        win = self.window()
        if not isinstance(win, QtWidgets.QWidget):
            return None

        candidates: List[QtWidgets.QWidget] = []

        for w in win.findChildren(QtWidgets.QWidget):
            name = (w.objectName() or "").lower()
            if "preview" in name:
                candidates.append(w)

        for lab in win.findChildren(QtWidgets.QLabel):
            try:
                pm = lab.pixmap()
            except Exception:
                pm = None
            if pm is not None and not pm.isNull():
                candidates.append(lab)

        biggest = None
        biggest_area = 0
        for w in win.findChildren(QtWidgets.QWidget):
            if w is self or self.isAncestorOf(w):
                continue
            if not w.isVisible():
                continue
            r = w.rect()
            area = max(0, r.width()) * max(0, r.height())
            if area > biggest_area:
                biggest_area = area
                biggest = w

        if biggest is not None:
            candidates.append(biggest)

        best = None
        best_area = 0
        for w in candidates:
            if not w.isVisible():
                continue
            r = w.rect()
            area = max(0, r.width()) * max(0, r.height())
            if area >= best_area and area >= 200 * 200:
                best = w
                best_area = area

        return best

    # ────────────────────────────────────────────────────────────
    # FAB positioning
    # ────────────────────────────────────────────────────────────
    def _place_fab_default_top_right(self) -> None:
        surf = self._fab_surface
        x = surf.width() - self.pwrite_fab.width() - self.FAB_RIGHT_PAD
        y = self.FAB_TOP_PAD
        self.pwrite_fab.move(QPoint(max(0, x), max(0, y)))
        self.pwrite_fab.clamp_to_surface()

    def _apply_nudge(self) -> None:
        if self.FAB_NUDGE_X != 0 or self.FAB_NUDGE_Y != 0:
            self.pwrite_fab.move(self.pwrite_fab.pos() + QPoint(self.FAB_NUDGE_X, self.FAB_NUDGE_Y))
            self.pwrite_fab.clamp_to_surface()

    def _place_fab_locked(self, win: QtWidgets.QWidget) -> None:
        tabbar = win.findChild(QtWidgets.QTabBar)
        if tabbar is not None:
            y = tabbar.geometry().bottom() + int(self.FAB_FIXED_Y_PAD)
        else:
            y = int(self.FAB_FIXED_Y_PAD)

        x = int(self.FAB_FIXED_X)

        self.pwrite_fab.move(QPoint(max(0, x), max(0, y)))
        self.pwrite_fab.clamp_to_surface()
        self.pwrite_fab.raise_()

    def _ensure_fab_on_preview(self) -> None:
        if bool(self.FAB_LOCKED):
            win = self.window()
            if not isinstance(win, QtWidgets.QWidget):
                win = self

            if self.pwrite_fab.parent() is not win:
                self._fab_surface = win
                self.pwrite_fab.setParent(win)
                self.pwrite_fab.set_surface(win)
                self.pwrite_fab.show()
                self.pwrite_fab.raise_()

            self._place_fab_locked(win)
            return

        surf = self._find_preview_surface() or self
        global_pos = self.pwrite_fab.mapToGlobal(QPoint(0, 0))

        if self.pwrite_fab.parent() is not surf:
            self._fab_surface = surf
            self.pwrite_fab.setParent(surf)
            self.pwrite_fab.set_surface(surf)
            self.pwrite_fab.show()
            self.pwrite_fab.raise_()
            self.pwrite_fab.move(surf.mapFromGlobal(global_pos))

        restored = self.pwrite_fab.restore_global_position(surface=surf)

        if not restored:
            self._place_fab_default_top_right()
            self.pwrite_fab.save_global_position()
        else:
            self.pwrite_fab.clamp_to_surface()

        self._apply_nudge()
        self.pwrite_fab.save_global_position()

        self.pwrite_fab.show()
        self.pwrite_fab.raise_()

    # ────────────────────────────────────────────────────────────
    # Qt events
    # ────────────────────────────────────────────────────────────
    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._ensure_fab_on_preview)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "pwrite_fab") and self.pwrite_fab and self.pwrite_fab.isVisible():
            if bool(self.FAB_LOCKED):
                win = self.window()
                if not isinstance(win, QtWidgets.QWidget):
                    win = self
                self._fab_surface = win
                self.pwrite_fab.set_surface(win)
                self._place_fab_locked(win)
            else:
                self.pwrite_fab.clamp_to_surface()
                self.pwrite_fab.save_global_position()

    # ────────────────────────────────────────────────────────────
    # Selection
    # ────────────────────────────────────────────────────────────
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

    def set_image_path(self, idx: int, source_path: str) -> None:
        label_text, filename = self.labels[idx]
        self.image_paths[idx] = source_path

        pages_dir = self._user_pages_dir()
        os.makedirs(pages_dir, exist_ok=True)

        dest_png = os.path.join(pages_dir, filename)

        try:
            img = _load_image_exif(source_path)
            _save_png(img, dest_png)
        except Exception as e:
            self.status.setText(f"❌ Failed to process {filename}: {e}")
            return

        btn = self.buttons.get(idx)
        if isinstance(btn, QtWidgets.QPushButton):
            btn.setText(f"  ✔️  {label_text}")

        pix = QtGui.QPixmap(dest_png)
        if pix.isNull():
            self.status.setText("❌ Invalid image file after save")
            return

        preview = pix.scaledToWidth(200, QtCore.Qt.SmoothTransformation)
        self.image_selected.emit(preview)
        self.status.setText(f"✅ {filename} saved; preview ready.")

    # ────────────────────────────────────────────────────────────
    # Hover preview
    # ────────────────────────────────────────────────────────────
    def preview_from_gallery(self, idx: int) -> None:
        _, filename = self.labels[idx]
        full_path = os.path.join(self._user_pages_dir(), filename)
        if os.path.exists(full_path):
            pix = QtGui.QPixmap(full_path)
            if not pix.isNull():
                prev = pix.scaledToWidth(200, QtCore.Qt.SmoothTransformation)
                self.hover_preview_image.emit(prev)

    # ────────────────────────────────────────────────────────────
    # Utilities
    # ────────────────────────────────────────────────────────────
    def reset_images(self) -> None:
        pages_dir = self._user_pages_dir()

        for i in (1, 2, 3, 4):
            self.image_paths[i] = None
            label_text, filename = self.labels[i]
            btn = self.buttons.get(i)
            if isinstance(btn, QtWidgets.QPushButton):
                btn.setText(f"  {label_text}")

            file_path = os.path.join(pages_dir, filename)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

        # Clear preview instantly (works even if Nexus isn't wired yet)
        self.image_selected.emit(QtGui.QPixmap())
        self.hover_preview_image.emit(QtGui.QPixmap())
        self.clear_preview.emit()

        self.status.setText("🧹 Cleared All Images.")

    def open_gallery_folder(self) -> None:
        user_root = os.path.join(self._project_dir(), "gallery", "user", "pages")
        os.makedirs(user_root, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(user_root))

    # ────────────────────────────────────────────────────────────
    # Prompt Writer bridge
    # ────────────────────────────────────────────────────────────
    def _open_prompt_writer_bridge(self):
        try:
            win = self.window()
            opener = getattr(win, "open_prompt_writer", None)
            if callable(opener):
                opener()
                self.status.setText("Prompt Writer opened.")
                return
        except Exception:
            pass
        self.status.setText("⚠️ Prompt Writer opener not found on main window.")
