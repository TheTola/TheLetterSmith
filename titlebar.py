# File: titlebar.py  (replace the whole file with this version)
from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QLinearGradient, QAction
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QMenu

from anima import ParticleBurst, AnimatedIconButton
from target import on_select_folder  # action for the optional reticle

RETICLE_ICON_REL_DEFAULT = os.path.join("gallery", "app", "icons", "reticle.png")


def _resolve_path(candidate: Optional[str], parent: Optional[QtWidgets.QWidget]) -> Optional[str]:
    """
    Resolve a file path with robust fallbacks:
      1) Absolute existing → use it
      2) parent.project_root/<candidate>
      3) <this_file_dir>/<candidate>
    """
    if candidate:
        if os.path.isabs(candidate) and os.path.isfile(candidate):
            return candidate
        bases = []
        if parent is not None:
            proj_root = getattr(parent, "project_root", None)
            if proj_root:
                bases.append(proj_root)
        bases.append(os.path.dirname(os.path.abspath(__file__)))
        for base in bases:
            p = os.path.join(base, candidate)
            if os.path.isfile(p):
                return p
    return None


def _icon_pixmap_strict(path: str, dpr: float) -> Optional[QtGui.QPixmap]:
    """
    Load an icon image with strong guarantees:
      - Prefer direct QPixmap(path) (best for PNG with alpha)
      - Fallback to QIcon(path).pixmap()
      - Scale to ~20*dpr device pixels, set devicePixelRatio for crisp rendering
    """
    target_px = max(16, int(20 * (dpr or 1.0)))

    pm = QtGui.QPixmap(path)  # direct (keeps alpha best)
    if not pm.isNull():
        if pm.width() != target_px or pm.height() != target_px:
            pm = pm.scaled(target_px, target_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr or 1.0)
        return pm

    ic = QIcon(path)
    pm = ic.pixmap(target_px, target_px)
    if not pm.isNull():
        if pm.width() != target_px or pm.height() != target_px:
            pm = pm.scaled(target_px, target_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr or 1.0)
        return pm

    return None


class TitleBar(QtWidgets.QWidget):
    minimizeRequested = QtCore.Signal()
    maximizeRestoreRequested = QtCore.Signal()
    closeRequested = QtCore.Signal()
    alwaysOnTopToggled = QtCore.Signal(bool)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        title_text: str = "Letter Smith",
        title_icon_path: Optional[str] = None,  # relative or absolute
        show_reticle: bool = True,
        reticle_icon_path: Optional[str] = None,  # relative or absolute
        reticle_tooltip: str = "List Folder Contents",
        use_gradient_bg: bool = True,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self._title_text = title_text
        self._title_icon_path = _resolve_path(title_icon_path, parent)
        self._use_gradient = use_gradient_bg

        self.setFixedHeight(40)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        # Layout
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 0)
        root.setSpacing(6)

        # ── Title icon: transparent, no fixed 22×22 slot ────────────────────
        self._title_icon_label = QtWidgets.QLabel(self)
        self._title_icon_label.setObjectName("titleIcon")
        self._title_icon_label.setVisible(False)
        self._title_icon_label.setAutoFillBackground(False)
        self._title_icon_label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._title_icon_label.setStyleSheet("background: transparent; border: none; margin: 0; padding: 0;")
        # No fixed size here — let the pixmap size dictate the natural box
        root.addWidget(self._title_icon_label, 0, Qt.AlignVCenter)

        # ── Title text: transparent, no panel behind ────────────────────────
        self._title_label = QtWidgets.QLabel(self._title_text, self)
        self._title_label.setObjectName("titleText")
        self._title_label.setAutoFillBackground(False)
        self._title_label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._title_label.setStyleSheet(
            "background: transparent; "
            "color: #00ffff; "
            "font: 16px 'Segoe UI Semibold'; "
            "letter-spacing: 1px; "
            "margin: 0; padding: 0;"
        )
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(12)
        glow.setOffset(0, 0)
        glow.setColor(QColor(0, 255, 255, 80))
        self._title_label.setGraphicsEffect(glow)
        root.addWidget(self._title_label, 0, Qt.AlignVCenter)

        # Spark overlay
        self._burst = ParticleBurst(self)
        self._burst.lower()
        self._burst.show()

        # Optional tiny reticle (already transparent in its own class)
        self.btn_target: Optional[AnimatedIconButton] = None
        if show_reticle:
            resolved_reticle = _resolve_path(reticle_icon_path or RETICLE_ICON_REL_DEFAULT, parent)
            self.btn_target = AnimatedIconButton(icon_path=resolved_reticle or "", tooltip=reticle_tooltip, parent=self)
            self.btn_target.setParticleOverlay(self._burst)
            self.btn_target.clicked.connect(on_select_folder)
            root.addWidget(self.btn_target, 0, Qt.AlignVCenter)

        root.addStretch(1)

        # Window control buttons (minimal style; hover tint only)
        self._btn_min = self._make_sys_button("—", "Minimize")
        self._btn_max = self._make_sys_button("▢", "Maximize")
        self._btn_close = self._make_sys_button("✕", "Close")

        self._btn_min.clicked.connect(self._on_minimize)
        self._btn_max.clicked.connect(self._on_maximize_restore)
        self._btn_close.clicked.connect(self._on_close)

        root.addWidget(self._btn_min)
        root.addWidget(self._btn_max)
        root.addWidget(self._btn_close)

        self._drag_start = QtCore.QPoint()

        if self.parent:
            self.parent.installEventFilter(self)

        self._refresh_title_icon()

    # Public API
    def setTitleText(self, text: str) -> None:
        self._title_text = text
        self._title_label.setText(text)

    def setTitleIcon(self, path: Optional[str]) -> None:
        self._title_icon_path = _resolve_path(path, self.parent)
        self._refresh_title_icon()

    # Helpers
    def _make_sys_button(self, glyph: str, tip: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(glyph, self)
        btn.setFixedSize(30, 30)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setStyleSheet(
            "QPushButton { color:#ccc; background:transparent; font-size:16px; border-radius:4px; }"
            "QPushButton:hover { background:rgba(255,255,255,0.10); }"
        )
        return btn

    def _refresh_title_icon(self) -> None:
        """Load/scale PNG/ICO for the title bar icon; show/hide label accordingly, with no box."""
        path = self._title_icon_path
        if not path or not os.path.isfile(path):
            self._title_icon_label.clear()
            self._title_icon_label.setVisible(False)
            return

        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = _icon_pixmap_strict(path, dpr)
        if pm is None or pm.isNull():
            self._title_icon_label.clear()
            self._title_icon_label.setVisible(False)
            return

        # Use natural pixmap size (no fixed 22×22 frame). This avoids a visible square slot.
        self._title_icon_label.setPixmap(pm)
        # Ensure label itself paints nothing behind
        self._title_icon_label.setAutoFillBackground(False)
        self._title_icon_label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._title_icon_label.setStyleSheet("background: transparent; border: none; margin: 0; padding: 0;")
        self._title_icon_label.setVisible(True)

    def _update_maximize_glyph(self) -> None:
        if not self.parent:
            return
        is_max = bool(self.parent.windowState() & Qt.WindowMaximized)
        self._btn_max.setText("❐" if is_max else "▢")
        self._btn_max.setToolTip("Restore" if is_max else "Maximize")

    # Window actions
    def _on_minimize(self):
        if self.parent:
            self.minimizeRequested.emit()
            self.parent.showMinimized()

    def _on_maximize_restore(self):
        if not self.parent:
            return
        self.maximizeRestoreRequested.emit()
        if self.parent.windowState() & Qt.WindowMaximized:
            self.parent.showNormal()
        else:
            self.parent.showMaximized()
        self._update_maximize_glyph()

    def _on_close(self):
        if self.parent:
            self.closeRequested.emit()
            self.parent.close()

    # Context menu (right-click)
    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        if not self.parent:
            return
        menu = QMenu(self)

        act_min = QAction("Minimize", self, triggered=self._on_minimize)
        is_max = bool(self.parent.windowState() & Qt.WindowMaximized)
        act_max = QAction("Restore" if is_max else "Maximize", self, triggered=self._on_maximize_restore)

        act_top = QAction("Always on top", self, checkable=True)
        stay_on_top = bool(self.parent.windowFlags() & Qt.WindowStaysOnTopHint)
        act_top.setChecked(stay_on_top)

        def _toggle_top():
            flags = self.parent.windowFlags()
            if act_top.isChecked():
                flags |= Qt.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowStaysOnTopHint
            self.parent.setWindowFlags(flags)
            self.parent.show()
            self.alwaysOnTopToggled.emit(act_top.isChecked())

        act_top.triggered.connect(_toggle_top)

        act_close = QAction("Close", self, triggered=self._on_close)

        menu.addAction(act_min)
        menu.addAction(act_max)
        menu.addSeparator()
        menu.addAction(act_top)
        menu.addSeparator()
        menu.addAction(act_close)

        menu.exec(e.globalPos())

    # Drag/move & double-click maximize
    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if event.buttons() & Qt.LeftButton and self.parent:
            delta = event.globalPosition().toPoint() - self._drag_start
            self.parent.move(self.parent.pos() + delta)
            self._drag_start = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._on_maximize_restore()
        super().mouseDoubleClickEvent(event)

    # Paint: optional gradient + bottom hairline
    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)

        if self._use_gradient:
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0.0, QColor(28, 28, 28))
            grad.setColorAt(1.0, QColor(22, 22, 22))
            p.fillRect(self.rect(), grad)

        p.setPen(QColor(0, 0, 0, 110))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self._burst.setGeometry(self.rect())
        self._refresh_title_icon()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.parent and event.type() == QEvent.WindowStateChange:
            self._update_maximize_glyph()
        return super().eventFilter(watched, event)
