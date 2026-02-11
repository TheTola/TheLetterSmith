# File: Over_Nexus.py
# Purpose: Nexus overlay tools (Wall + Helper) + optional standalone PromptWriterPanel launcher
# Standalone-first: removes embedded PromptMakerUIEmbedded/PrompterPanel redundancy.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import QFileDialog

# Optional: DropButton from Image_tab (drag-and-drop). Falls back to QPushButton if absent.
try:
    from Image_tab import DropButton  # type: ignore
except Exception:
    DropButton = None  # type: ignore

# Optional: Standalone PromptWriterPanel (preferred)
try:
    from PromptWriterPanel import PromptWriterPanel  # type: ignore
except Exception:
    PromptWriterPanel = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Theme & Configuration
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Theme:
    bg: str = "#1b1b1b"
    fg: str = "#e6e6e6"
    accent: str = "#8feaff"


@dataclass(frozen=True)
class Config:
    # Icons (relative to project_root)
    ICON_WALL: str = os.path.join("gallery", "icons", "wallB.png")
    ICON_HELPER: str = os.path.join("gallery", "icons", "cperp.png")
    ICON_PROMPTER_CANDIDATES: tuple[str, ...] = (
        os.path.join("gallery", "icons", "pwrite.png"),
        os.path.join("gallery", "icons", "Pwrite.png"),
    )

    # Sizes
    ICON_SIZE: QSize = QSize(96, 96)
    BTN_SMALL: QSize = QSize(30, 30)

    # Features
    show_prompter_launcher: bool = False

    # Message overlay placement (the two hidden buttons)
    MESSAGE_OVERLAY_OFFSET_PX: int = 150  # center ± offset
    MESSAGE_OVERLAY_Y_PX: int = 129       # y inside overlay geometry

    # Overlay geometry padding around preview_frame (lets buttons sit outside preview)
    OVERLAY_PAD: int = 140


def _set_png_icon(
    btn: QtWidgets.QPushButton,
    abs_path: Optional[str],
    size: QSize,
    fallback_text: Optional[str] = None,
) -> None:
    try:
        if abs_path and os.path.exists(abs_path):
            btn.setIcon(QIcon(abs_path))
            btn.setIconSize(size)
            btn.setText("")
            btn.setFlat(True)
            btn.setFixedSize(size)
            return
    except Exception:
        pass

    if fallback_text is not None:
        btn.setText(fallback_text)
        btn.setFlat(True)
        btn.setFixedSize(max(size.width(), 24), max(size.height(), 24))


def _make_drop_button(label: str, slot_index: int, parent: QtWidgets.QWidget) -> QtWidgets.QPushButton:
    if DropButton is None:
        btn = QtWidgets.QPushButton(parent)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet("QPushButton{border:none; background:transparent;}")
        return btn
    try:
        return DropButton(label, slot_index, parent=parent)  # type: ignore[misc]
    except TypeError:
        btn = QtWidgets.QPushButton(parent)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet("QPushButton{border:none; background:transparent;}")
        return btn


# ──────────────────────────────────────────────────────────────────────────────
# Floating overlay bar (NOT clipped by preview_frame)
# ──────────────────────────────────────────────────────────────────────────────

class FloatingOverlayBar(QtWidgets.QWidget):
    """
    Transparent container parented to a higher widget (container),
    tracks preview_frame geometry with padding, so children can exist outside preview.
    """
    def __init__(self, host: QtWidgets.QWidget, cfg: Config):
        super().__init__(host)
        self.cfg = cfg

        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setVisible(False)

        self._target_widget: Optional[QtWidgets.QWidget] = None

        self._wall_btn: Optional[QtWidgets.QPushButton] = None
        self._helper_btn: Optional[QtWidgets.QPushButton] = None
        self._offset_px: int = int(cfg.MESSAGE_OVERLAY_OFFSET_PX)
        self._y_px: int = int(cfg.MESSAGE_OVERLAY_Y_PX)

    def bind_target(self, target_widget: QtWidgets.QWidget) -> None:
        self._target_widget = target_widget
        self.sync_geometry()

    def bind_buttons(
        self,
        wall_btn: QtWidgets.QPushButton,
        helper_btn: QtWidgets.QPushButton,
        offset_px: int,
        y_px: int,
    ) -> None:
        self._wall_btn = wall_btn
        self._helper_btn = helper_btn
        self._offset_px = int(offset_px)
        self._y_px = int(y_px)
        self._position_buttons()

    def sync_geometry(self) -> None:
        host = self.parentWidget()
        target = self._target_widget
        if not host or not target:
            return

        # preview_frame rect in host coordinates
        top_left = target.mapTo(host, QtCore.QPoint(0, 0))
        r = QRect(top_left, target.size())

        pad = int(self.cfg.OVERLAY_PAD)
        r.adjust(-pad, -pad, pad, pad)

        self.setGeometry(r)
        self._position_buttons()

    def _position_buttons(self) -> None:
        if not (self._wall_btn and self._helper_btn):
            return

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        self._wall_btn.adjustSize()
        self._helper_btn.adjustSize()

        wall_w = self._wall_btn.width()
        help_w = self._helper_btn.width()

        center_x = w // 2

        # center ± offset (horizontal)
        wall_center_x = center_x - self._offset_px
        help_center_x = center_x + self._offset_px

        wall_x = int(wall_center_x - (wall_w // 2))
        help_x = int(help_center_x - (help_w // 2))

        # explicit Y (your tuning knob)
        y = int(self._y_px)

        self._wall_btn.move(wall_x, y)
        self._helper_btn.move(help_x, y)

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(e)
        self._position_buttons()


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class OverNexusController(QtCore.QObject):
    """
    Wires overlay tools into existing Nexus.
    Tabs assumed: Images=0, Sound=1, Message=2, Forge=3, Command=4.
    """
    def __init__(self, nexus: object, project_root: str, theme: Theme, cfg: Config):
        super().__init__(nexus)
        self.nexus = nexus
        self.project_root = project_root
        self.theme = theme
        self.cfg = cfg

        # Host for overlays (must be above preview_frame to avoid clipping)
        host = getattr(self.nexus, "container", None)
        if not isinstance(host, QtWidgets.QWidget):
            host = self.nexus if isinstance(self.nexus, QtWidgets.QWidget) else None
        if host is None:
            raise RuntimeError("Over_Nexus: could not resolve a QWidget host for overlays.")

        # Optional PromptWriter launcher (standalone window)
        self.prompter_btn: Optional[QtWidgets.QPushButton] = None
        self.prompt_panel: Optional[QtWidgets.QWidget] = None

        if self.cfg.show_prompter_launcher and PromptWriterPanel is not None:
            self.prompter_btn = QtWidgets.QPushButton(host)
            self.prompter_btn.setCursor(Qt.PointingHandCursor)
            self.prompter_btn.setFlat(True)
            self.prompter_btn.setStyleSheet("QPushButton{border:none; background:transparent;}")

            icon_path = None
            for rel in self.cfg.ICON_PROMPTER_CANDIDATES:
                cand = os.path.join(project_root, rel)
                if os.path.exists(cand):
                    icon_path = cand
                    break

            if icon_path:
                self.prompter_btn.setIcon(QIcon(icon_path))
                self.prompter_btn.setIconSize(self.cfg.ICON_SIZE)
                self.prompter_btn.setToolTip("Prompt Writer")
                self.prompter_btn.setFixedSize(self.cfg.ICON_SIZE)
            else:
                self.prompter_btn.setText("Prompt\nWriter")
                self.prompter_btn.setFixedSize(120, 96)

            self.prompter_btn.move(24, 24)
            self.prompter_btn.clicked.connect(self._toggle_promptwriter)

        # Floating overlay bar (Wall + Helper) for Message tab
        self.float_bar = FloatingOverlayBar(host, cfg)
        self.float_bar.bind_target(self.nexus.preview_frame)
        self.float_bar.raise_()

        # Wall button
        self.wall_btn = _make_drop_button("", 4, self.float_bar)
        self.wall_btn.setObjectName("wallButton")
        self.wall_btn.setToolTip("Set clarifier (text wall) image")
        _set_png_icon(
            self.wall_btn,
            abs_path=os.path.join(self.project_root, self.cfg.ICON_WALL),
            size=self.cfg.BTN_SMALL,
            fallback_text="🧱",
        )
        self.wall_btn.clicked.connect(self._select_wall_image)
        if hasattr(self.wall_btn, "file_dropped"):
            self.wall_btn.file_dropped.connect(lambda p: self.nexus.image_tab.set_image_path(4, p))  # type: ignore[attr-defined]

        # Helper button (Title Change toggle)
        self.helper_btn = QtWidgets.QPushButton("", self.float_bar)
        self.helper_btn.setToolTip("Toggle message helper")
        _set_png_icon(
            self.helper_btn,
            abs_path=os.path.join(self.project_root, self.cfg.ICON_HELPER),
            size=self.cfg.BTN_SMALL,
            fallback_text="🞪",
        )
        if hasattr(self.nexus.message_tab, "toggle_title_sister_area"):
            self.helper_btn.clicked.connect(self.nexus.message_tab.toggle_title_sister_area)
        else:
            self.helper_btn.setEnabled(False)

        self.float_bar.bind_buttons(
            self.wall_btn,
            self.helper_btn,
            offset_px=self.cfg.MESSAGE_OVERLAY_OFFSET_PX,
            y_px=self.cfg.MESSAGE_OVERLAY_Y_PX,
        )

        # Tab + geometry updates
        self.nexus.tabbar.currentChanged.connect(self._on_tab_changed)
        if hasattr(self.nexus.tabbar, "tabBarDoubleClicked"):
            self.nexus.tabbar.tabBarDoubleClicked.connect(self._on_tab_doubled)

        self.nexus.preview_frame.installEventFilter(self)

        self.float_bar.sync_geometry()
        self._on_tab_changed(self.nexus.tabbar.currentIndex())

    def _toggle_promptwriter(self) -> None:
        if PromptWriterPanel is None:
            return

        if self.prompt_panel is None:
            try:
                # Standalone window, no parent -> independent
                try:
                    self.prompt_panel = PromptWriterPanel(parent=None, project_root=self.project_root)  # type: ignore[call-arg]
                except TypeError:
                    self.prompt_panel = PromptWriterPanel(parent=None)  # type: ignore[call-arg]
            except Exception:
                self.prompt_panel = None
                return

        try:
            if hasattr(self.prompt_panel, "popup"):
                self.prompt_panel.popup()  # type: ignore[attr-defined]
            else:
                self.prompt_panel.show()
                self.prompt_panel.raise_()
        except Exception:
            pass

    def _on_tab_changed(self, idx: int) -> None:
        is_images = (idx == 0)
        is_message = (idx == 2)

        if self.prompter_btn:
            self.prompter_btn.setVisible(is_images)

        self.float_bar.setVisible(is_message)
        if is_message:
            self.float_bar.sync_geometry()

        if not is_message and hasattr(self.nexus.message_tab, "title_sister_container"):
            self.nexus.message_tab.title_sister_container.setVisible(False)

    def _on_tab_doubled(self, index: int) -> None:
        if index == 2:
            self.float_bar.setVisible(not self.float_bar.isVisible())
            if self.float_bar.isVisible():
                self.float_bar.sync_geometry()

    def _select_wall_image(self) -> None:
        parent_widget = getattr(self.nexus, "container", None)
        if not isinstance(parent_widget, QtWidgets.QWidget):
            parent_widget = None

        path, _ = QFileDialog.getOpenFileName(
            parent_widget, "Select Clarifier (Text Wall) Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return

        try:
            self.nexus.image_tab.set_image_path(4, path)
        except Exception:
            pass

        html = getattr(self.nexus.message_tab, "current_html", None)
        if html and str(html).strip():
            try:
                if hasattr(self.nexus.message_tab, "_generate_image"):
                    self.nexus.message_tab._generate_image(str(html))  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        pix = QPixmap(path)
        if not pix.isNull():
            thumb = pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if hasattr(self.nexus.message_tab, "wall_preview"):
                try:
                    self.nexus.message_tab.wall_preview.emit(thumb)
                except Exception:
                    pass

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self.nexus.preview_frame and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show, QtCore.QEvent.Move):
            self.float_bar.sync_geometry()
        return super().eventFilter(obj, event)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def install_over_nexus(
    nexus: object,
    project_root: str,
    theme: Optional[Theme] = None,
    config: Optional[Config] = None,
) -> OverNexusController:
    theme = theme or Theme()
    cfg = config or Config()
    return OverNexusController(nexus, project_root, theme, cfg)
