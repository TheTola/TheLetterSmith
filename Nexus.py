# ===============================
# File: Nexus.py
# ===============================
"""
Nexus ΓÇö main shell for Letter Smith
Clean placement ΓÇó Robust overlay ΓÇó Sound visualizer ΓÇó Prompt Writer FAB owned by Image_tab
+ Help.gif (idle, plays constantly) swaps to HHelp.gif on hover
+ Per-tab Help popover header: The Image tab / The sound tab / The message tab / The forge tab

Notes
- If Qt WebEngine is missing, we exit with a clear tip: pip install PySide6-Addons
- Animation helpers come from anima.py; we fall back safely if not found
"""

from __future__ import annotations

import math
import os, sys, subprocess, json
from pathlib import Path
from typing import Optional

from settings_store import (
    CURTAIN_STYLE_LABELS,
    DEFAULT_SETTINGS,
    SettingsStore,
    VALID_CURTAIN_STYLES,
)
from project_state import (
    ApplicationState,
    ProjectStateController,
)
from project_paths import ProjectPathResolver
from recipient_page import RecipientPage

# ===================================================================================================================================================================================
# Overlay integration
# ===================================================================================================================================================================================
from Over_Nexus import install_over_nexus

# ===================================================================================================================================================================================
# Qt
# ===================================================================================================================================================================================
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl, QEvent, QSize, QPoint
from PySide6.QtGui import QColor, QPixmap, QIcon, QMouseEvent, QMovie, QFont
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QStatusBar, QLabel, QVBoxLayout, QHBoxLayout, QDialog,
    QFrame
)

# WebEngine (used for HTML preview)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
except Exception as e:
    raise SystemExit(
        "Qt WebEngine is required for the HTML preview.\n"
        "Install it with:  pip install PySide6-Addons\n\n"
        f"Original error:\n{e}"
    )

# Animations / FX (from anima.py)
try:
    from anima import ParticleBurst, TabSwitcher, install_click_fx
except Exception:
    ParticleBurst = None
    TabSwitcher = None
    def install_click_fx(_):  # type: ignore
        pass

# ===================================================================================================================================================================================
# Relative asset hints & sizing
# ===================================================================================================================================================================================

REL_RETICLE_ICON = "gallery/app/icons/reticle.png"   # optional (title bar icon)
REL_HELP_GIF     = "gallery/app/icons/Help.gif"      # idle (plays constantly)
REL_HELP_HOVER   = "gallery/app/icons/HHelp.gif"     # hover variant (plays on hover)
REL_HELP_PNG     = "gallery/app/icons/Help.png"      # final static fallback

WIN_W, WIN_H = 1400, 900
_PREVIEW_AR = 169 / 253  # preview frame aspect (matches your 169├ù253 scaling)

# Help icon display size
HELP_ICON_PX = 125
HELP_ICON_HALF = HELP_ICON_PX // 2

# Command deliberately bypasses the normal TabSwitcher slide. Its body-level
# fade covers the preview, help, and page layout as one stable snapshot.
COMMAND_FADE_MS = 440


# =============================================================================================
# Event filter for message double-click on QWebEngineView
# ===================================================================================================================================================================================
class _DoubleClickFilter(QtCore.QObject):
    def __init__(self, nexus: "Nexus"):
        super().__init__(nexus)
        self.nexus = nexus

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonDblClick:
            if isinstance(ev, QMouseEvent) and ev.button() == Qt.LeftButton:
                self.nexus._on_message_double_click()
                return True
        return False


class _LoadingSpinner(QtWidgets.QWidget):
    """Small indeterminate spinner rendered without external assets."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._step = 0
        self.setFixedSize(92, 92)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._step = 0
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)

        pulse = 2.0 + 1.5 * (1.0 + math.sin(self._step * math.pi / 6.0))
        ring = QtGui.QPen(QtGui.QColor(0, 205, 236, 70), pulse)
        painter.setPen(ring)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, 30, 30)

        painter.setPen(Qt.NoPen)
        for index in range(12):
            distance = (index - self._step) % 12
            color = QtGui.QColor(0, 235, 255)
            color.setAlpha(max(38, 255 - distance * 18))
            painter.setBrush(color)
            angle = math.radians(index * 30 - 90)
            point = QtCore.QPointF(
                center.x() + math.cos(angle) * 31,
                center.y() + math.sin(angle) * 31,
            )
            radius = 4.7 if distance == 0 else 3.4
            painter.drawEllipse(point, radius, radius)


class _ProjectLoadingOverlay(QtWidgets.QFrame):
    """Animated input shield shown while a saved project is restored."""

    _BLOCKED_KEYS = {
        QEvent.KeyPress,
        QEvent.KeyRelease,
        QEvent.Shortcut,
        QEvent.ShortcutOverride,
    }

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectLoadingOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            "QFrame#ProjectLoadingOverlay{background:rgba(4,8,12,218);border:none;}"
            "QFrame#ProjectLoadingPanel{background:#101820;border:1px solid #287080;"
            "border-radius:12px;}"
            "QLabel#ProjectLoadingTitle{color:#e9fdff;font:600 15pt 'Segoe UI';}"
            "QLabel#ProjectLoadingDetail{color:#9fcbd3;font:10pt 'Segoe UI';}"
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("ProjectLoadingPanel")
        panel.setMaximumWidth(460)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(34, 26, 34, 28)
        panel_layout.setSpacing(10)

        self.spinner = _LoadingSpinner(panel)
        panel_layout.addWidget(self.spinner, 0, Qt.AlignHCenter)
        self.title = QtWidgets.QLabel("Loading saved letter…", panel)
        self.title.setObjectName("ProjectLoadingTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        panel_layout.addWidget(self.title)
        self.detail = QtWidgets.QLabel(
            "Restoring the recipient, images, message, and sound safely.",
            panel,
        )
        self.detail.setObjectName("ProjectLoadingDetail")
        self.detail.setAlignment(Qt.AlignCenter)
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        panel_layout.addWidget(self.detail)

        for child in (panel, self.title, self.detail):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        root.addWidget(panel, 0, Qt.AlignHCenter)
        root.addStretch(1)
        self.hide()

    def start(self, message: str) -> None:
        activity = str(message or "Loading saved letter…").strip()
        self.title.setText(activity)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self.show()
        self.raise_()
        self.setFocus(Qt.OtherFocusReason)
        self.spinner.start()

    def stop(self) -> None:
        self.spinner.stop()
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self.hide()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self.isVisible() and event.type() in self._BLOCKED_KEYS:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        event.accept()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.accept()


# =============================================================================
# Custom Title Bar
# =============================================================================

class TitleBar(QtWidgets.QWidget):
    """
    Custom frameless title bar.

    Unicode symbols are written as escape codes rather than literal characters.
    This prevents UTF-8/CP437 encoding corruption.
    """

    TARGET_SYMBOL = "\uFF0B"      # ＋
    MINIMIZE_SYMBOL = "\u2013"    # –
    MAXIMIZE_SYMBOL = "\u25A1"    # □
    RESTORE_SYMBOL = "\u2750"     # ❐
    CLOSE_SYMBOL = "\u2715"       # ✕

    def __init__(self, parent=None):
        super().__init__(parent)

        self.parent = parent
        self._drag_start = QtCore.QPoint()

        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # ---------------------------------------------------------------------
        # Window title
        # ---------------------------------------------------------------------

        title = QtWidgets.QLabel(
            "The Silver-Tongued Lettersmith",
            self,
        )

        title.setStyleSheet(
            """
            QLabel {
                color: #00ffff;
                background: transparent;
                font-family: "Segoe UI Semibold";
                font-size: 16px;
                letter-spacing: 1px;
            }
            """
        )

        layout.addWidget(title)
        layout.addStretch()

        # ---------------------------------------------------------------------
        # Viewer settings
        # ---------------------------------------------------------------------

        self.settings_button = QtWidgets.QToolButton(self)
        self.settings_button.setText("Settings")
        self.settings_button.setToolTip("Application settings")
        self.settings_button.setAccessibleName("Application settings")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.setPopupMode(
            QtWidgets.QToolButton.InstantPopup
        )
        self.settings_button.setFixedHeight(30)
        self.settings_button.setStyleSheet(
            "QToolButton{color:#d7f8ff;background:transparent;"
            "border:1px solid transparent;border-radius:5px;padding:4px 9px;}"
            "QToolButton:hover,QToolButton::menu-button:hover{"
            "background:rgba(0,255,255,0.12);border-color:#2f6672;}"
            "QToolButton::menu-indicator{image:none;}"
        )
        self.settings_menu = QtWidgets.QMenu(self.settings_button)
        self.settings_menu.setStyleSheet(
            "QMenu{background:#101820;color:#dff9ff;"
            "border:1px solid #31515f;padding:5px;}"
            "QMenu::item{padding:7px 26px 7px 10px;border-radius:4px;}"
            "QMenu::item:selected{background:#18323d;color:#fff;}"
            "QMenu::indicator:checked{background:#00b8d4;"
            "border:1px solid #8cf3ff;}"
        )
        self.curtain_menu = self.settings_menu.addMenu("Curtains")
        self._curtain_actions: dict[str, QtGui.QAction] = {}
        self._curtain_group = QtGui.QActionGroup(self)
        self._curtain_group.setExclusive(True)
        for style, label in CURTAIN_STYLE_LABELS.items():
            action = self.curtain_menu.addAction(label)
            action.setCheckable(True)
            action.setData(style)
            self._curtain_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, value=style: self._set_curtain_style(
                    value
                )
            )
            self._curtain_actions[style] = action
        self.settings_menu.addSeparator()
        self.repair_music_action = self.settings_menu.addAction(
            "Repair Music Archive"
        )
        self.repair_music_action.triggered.connect(
            self._repair_music_archive
        )
        self.settings_menu.aboutToShow.connect(
            self._sync_curtain_menu
        )
        self.settings_button.setMenu(self.settings_menu)
        layout.addWidget(self.settings_button)

        # ---------------------------------------------------------------------
        # Target Browser
        # ---------------------------------------------------------------------

        self.btn_target = self._make_button(
            text=self.TARGET_SYMBOL,
            tooltip="Open Target Browser",
            hover_background="rgba(0, 255, 255, 0.15)",
        )

        target_icon_path = os.path.join(
            self.parent.project_root,
            REL_RETICLE_ICON,
        )

        if os.path.exists(target_icon_path):
            self.btn_target.setText("")
            self.btn_target.setIcon(
                QIcon(target_icon_path)
            )
            self.btn_target.setIconSize(
                QSize(24, 24)
            )

        self.btn_target.clicked.connect(
            self.parent.open_target_browser
        )

        layout.addWidget(self.btn_target)

        # ---------------------------------------------------------------------
        # Minimize
        # ---------------------------------------------------------------------

        self.btn_minimize = self._make_button(
            text=self.MINIMIZE_SYMBOL,
            tooltip="Minimize",
            hover_background="rgba(0, 255, 255, 0.15)",
        )

        self.btn_minimize.clicked.connect(
            self.parent.showMinimized
        )

        layout.addWidget(self.btn_minimize)

        # ---------------------------------------------------------------------
        # Maximize / Restore
        # ---------------------------------------------------------------------

        self.btn_max = self._make_button(
            text=self.MAXIMIZE_SYMBOL,
            tooltip="Maximize",
            hover_background="rgba(0, 255, 255, 0.15)",
        )

        self.btn_max.clicked.connect(
            self._toggle_max_restore
        )

        layout.addWidget(self.btn_max)

        # ---------------------------------------------------------------------
        # Close
        # ---------------------------------------------------------------------

        self.btn_close = self._make_button(
            text=self.CLOSE_SYMBOL,
            tooltip="Close",
            hover_background="rgba(255, 0, 0, 0.30)",
        )

        self.btn_close.clicked.connect(
            self.parent.close
        )

        layout.addWidget(self.btn_close)

        # Keep the maximize/restore symbol synchronized when Windows changes
        # the window state outside this button.
        self.parent.installEventFilter(self)
        self._sync_max_restore_button()

    def _sync_curtain_menu(self) -> None:
        current = str(
            SettingsStore(self.parent.project_root).get(
                "curtain_style",
                DEFAULT_SETTINGS["curtain_style"],
            )
        )
        for style, action in self._curtain_actions.items():
            action.setChecked(style == current)

    def _set_curtain_style(self, style: str) -> None:
        if style not in VALID_CURTAIN_STYLES:
            style = str(DEFAULT_SETTINGS["curtain_style"])
        SettingsStore(self.parent.project_root).update_fields(
            curtain_style=style
        )
        self._sync_curtain_menu()
        self.parent.status(
            f"Curtain style set to {CURTAIN_STYLE_LABELS[style]}."
        )
        forge = getattr(self.parent, "forge_tab", None)
        if forge is None:
            return
        forge.schedule_refresh()
        if forge.isVisible():
            forge.ensure_preview_current()

    def _repair_music_archive(self) -> None:
        sound_tab = getattr(self.parent, "sound_tab", None)
        if sound_tab is None:
            self.parent.status("Music Archive is not ready.")
            return
        sound_tab.repair_music_archive()

    def _make_button(
        self,
        text: str,
        tooltip: str,
        hover_background: str,
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(
            text,
            self,
        )

        button.setFixedSize(32, 32)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)

        symbol_font = QFont(
            "Segoe UI Symbol",
            15,
        )
        symbol_font.setBold(False)
        button.setFont(symbol_font)

        button.setStyleSheet(
            f"""
            QPushButton {{
                color: #d0d0d0;
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}

            QPushButton:hover {{
                color: #ffffff;
                background: {hover_background};
            }}

            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.12);
            }}
            """
        )

        return button

    def _sync_max_restore_button(self) -> None:
        if self.parent.isMaximized():
            self.btn_max.setText(
                self.RESTORE_SYMBOL
            )
            self.btn_max.setToolTip(
                "Restore"
            )
            self.btn_max.setAccessibleName(
                "Restore"
            )
        else:
            self.btn_max.setText(
                self.MAXIMIZE_SYMBOL
            )
            self.btn_max.setToolTip(
                "Maximize"
            )
            self.btn_max.setAccessibleName(
                "Maximize"
            )

    def _toggle_max_restore(self) -> None:
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

        QtCore.QTimer.singleShot(
            0,
            self._sync_max_restore_button,
        )

    def eventFilter(self, watched, event):
        if (
            watched is self.parent
            and event.type() == QEvent.WindowStateChange
        ):
            QtCore.QTimer.singleShot(
                0,
                self._sync_max_restore_button,
            )

        return super().eventFilter(
            watched,
            event,
        )

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._toggle_max_restore()
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_start = (
                event.globalPosition().toPoint()
            )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            event.buttons() & Qt.LeftButton
            and not self.parent.isMaximized()
        ):
            current_position = (
                event.globalPosition().toPoint()
            )

            delta = (
                current_position
                - self._drag_start
            )

            self.parent.move(
                self.parent.pos() + delta
            )

            self._drag_start = current_position
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = QtCore.QPoint()
        super().mouseReleaseEvent(event)

# ===================================================================================================================================================================================
# Small hover-help popover (top-level tooltip window)
# ===================================================================================================================================================================================
class HelpPopover(QFrame):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("HelpPopover")
        self.setVisible(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.ToolTip, True)  # native tooltip stacking behavior

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)

        self.header = QLabel("")  # dynamic per-tab header
        self.header.setTextFormat(Qt.RichText)
        hf = QFont("Segoe UI Semibold", 13)
        hf.setBold(True)
        self.header.setFont(hf)
        self.header.setWordWrap(True)
        self.header.setStyleSheet("font-weight:800; color:#e0ffff; background:transparent;")

        self.body = QLabel("")
        bf = QFont("Segoe UI", 10)
        self.body.setFont(bf)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(self.header)
        lay.addWidget(self.body)

        self.setStyleSheet("""
            QFrame#HelpPopover {
                background: #101317;
                border: 2px solid #2f7474;
                border-radius: 8px;
            }
            QFrame#HelpPopover QLabel {
                color: #e0ffff;
            }
        """)

    def set_header_text(self, text: str) -> None:
        self.header.setText(text or "")
        self.header.updateGeometry()

    def set_help_text(self, body_text: str) -> None:
        self.body.setText(body_text or "")
        self.body.updateGeometry()

    def _resize_for_width(self, width: int) -> None:
        layout = self.layout()
        margins = layout.contentsMargins()
        frame = self.frameWidth() * 2
        content_width = max(
            1,
            int(width) - margins.left() - margins.right() - frame,
        )

        heights = []
        for label in (self.header, self.body):
            height = label.heightForWidth(content_width)
            if height < 0:
                height = label.sizeHint().height()
            label.setFixedSize(content_width, max(1, height))
            heights.append(max(1, height))

        total_height = (
            margins.top()
            + margins.bottom()
            + frame
            + sum(heights)
            + layout.spacing()
        )
        self.setFixedSize(int(width), total_height)

    def popup_at(self, anchor_global: QPoint, prefer_left: bool, parent: QtWidgets.QWidget, icon_px: int = HELP_ICON_PX):
        """
        Place the popover adjacent to the help icon.

        anchor_global: icon center in GLOBAL coords.
        If this widget is a top-level (Qt.ToolTip), we must move in GLOBAL coords.
        If it's a child widget, we move in PARENT-LOCAL coords.
        """
        ideal_width = self.sizeHint().width()
        width = min(420, max(320, ideal_width))
        self._resize_for_width(width)

        margin = 8
        is_tooltip = bool(self.windowFlags() & Qt.ToolTip)

        if is_tooltip:
            # Top-level tooltip: use GLOBAL coords; clamp to the window's screen
            try:
                win = parent.window().windowHandle()
                screen = win.screen() if win else QtGui.QGuiApplication.primaryScreen()
            except Exception:
                screen = QtGui.QGuiApplication.primaryScreen()
            sgeo = screen.availableGeometry() if screen else QtGui.QGuiApplication.primaryScreen().availableGeometry()

            x_right_g = anchor_global.x() + (icon_px // 2) + margin
            x_left_g  = anchor_global.x() - (icon_px // 2) - margin - self.width()

            xg = x_right_g
            if prefer_left or (x_right_g + self.width() > sgeo.right() - margin):
                xg = max(sgeo.left() + margin, x_left_g)

            yg = max(sgeo.top() + margin, anchor_global.y() - (self.height() // 2))
            if yg + self.height() > sgeo.bottom() - margin:
                yg = sgeo.bottom() - margin - self.height()

            self.move(xg, yg)
        else:
            # Child widget: position in PARENT-LOCAL coords
            anchor_local = parent.mapFromGlobal(anchor_global)

            x_right = anchor_local.x() + (icon_px // 2) + margin
            x_left  = anchor_local.x() - (icon_px // 2) - margin - self.width()
            parent_rect = parent.rect()

            x = x_right
            if prefer_left or (x_right + self.width() > parent_rect.right() - margin):
                x = max(margin, x_left)

            y = max(margin, anchor_local.y() - (self.height() // 2))
            if y + self.height() > parent_rect.bottom() - margin:
                y = max(margin, parent_rect.bottom() - margin - self.height())

            self.move(x, y)

        self.setVisible(True)
        self.raise_()

    def popdown(self):
        self.setVisible(False)


# ===================================================================================================================================================================================
# Nexus Main Window
# ===================================================================================================================================================================================
class _ForgePreviewFullscreenWindow(QtWidgets.QWidget):
    """Top-level surface that fullscreens only the interactive letter preview."""

    exit_requested = QtCore.Signal()

    def __init__(self, owner: QtWidgets.QWidget):
        super().__init__(
            owner,
            QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint,
        )
        self.setObjectName("ForgePreviewFullscreenWindow")
        self.setWindowTitle("Letter Preview")
        self.setStyleSheet(
            "QWidget#ForgePreviewFullscreenWindow{background:#0b0c12;}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._escape_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(Qt.Key_Escape),
            self,
        )
        self._escape_shortcut.setContext(Qt.WindowShortcut)
        self._escape_shortcut.activated.connect(self.exit_requested)

    def attach_preview(self, preview: QtWidgets.QWidget) -> None:
        self._layout.addWidget(preview)

    def detach_preview(self, preview: QtWidgets.QWidget) -> None:
        self._layout.removeWidget(preview)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.exit_requested.emit()
        event.ignore()


class Nexus(QtWidgets.QMainWindow):
    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = str(project_root)
        self.project_state = ProjectStateController(self.project_root)
        self.project_paths = ProjectPathResolver(self.project_root)
        initial_project_state = self.project_state.initialize()
        self._project_tabs_initialized = False
        self._forge_fullscreen_active = False
        self._forge_fullscreen_window: Optional[
            _ForgePreviewFullscreenWindow
        ] = None
        self._shutdown_complete = False
        self._shutdown_in_progress = False
        self.setObjectName("NexusWindow")

        # Frameless + QSS
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet("""
            /*
             * Background ownership is deliberately scoped.
             *
             * Do not assign a background to every QWidget or QStackedWidget.
             * Doing so paints Nexus gray through transparent child margins and
             * creates the rectangular "holes" that were visible in Sound.
             */
            QMainWindow#NexusWindow,
            QWidget#NexusRoot,
            QWidget#NexusBody {
                background:#1e1e1e;
                color:#d0d0d0;
                font-family:'Segoe UI';
                font-size:11px;
            }

            QStackedWidget#FeatureStack,
            QStackedWidget#PreviewStack,
            QWidget#PreviewBlank {
                background:transparent;
                border:none;
            }

            /*
             * Each feature page owns its own surface. Sound uses the dark-blue
             * surface expected by its interface, so transparent areas reveal
             * blue rather than Nexus gray.
             */
            QWidget#ImagePageSurface,
            QWidget#MessagePageSurface,
            QWidget#ForgePageSurface,
            QWidget#CommandPageSurface {
                background:#1e1e1e;
                border:none;
            }

            QWidget#SoundPageSurface {
                background:#101820;
                border:none;
            }

            QTabBar#MainTabBar {
                background:#1e1e1e;
                color:#d0d0d0;
                font-family:'Segoe UI';
                font-size:11px;
            }

            QLabel {
                color:#b0e0e6;
                font-weight:600;
            }

            QPushButton {
                background-color:transparent;
                color:#d0d0d0;
                border:1px solid #444;
                border-radius:4px;
                padding:6px 12px;
                font:11px 'Segoe UI';
            }
            QPushButton:hover {
                background:#2a2a2a;
                border-color:#00b2b2;
                color:#e0f7f7;
            }
            QPushButton:pressed {
                background:#353535;
                border-color:#00a0a0;
                color:#c0f0f0;
            }

            QTabBar#MainTabBar::tab {
                background:transparent;
                border:none;
                padding:8px 14px;
                margin-right:2px;
                color:#a0a0a0;
            }
            QTabBar#MainTabBar::tab:selected {
                color:#e0fdfd;
                border-bottom:2px solid #00b2b2;
            }
            QTabBar#MainTabBar::tab:hover {
                color:#e0f7f7;
            }

            QTabBar#MainTabBar[commandOverlay="true"] {
                background:transparent;
            }
            QTabBar#MainTabBar[commandOverlay="true"]::tab {
                background:transparent;
                border:none;
                color:transparent;
            }
            QTabBar#MainTabBar[commandOverlay="true"]::tab:selected {
                border:none;
                color:transparent;
            }
            QTabBar#MainTabBar[commandOverlay="true"]::tab:hover {
                background:rgba(11,12,16,0.82);
                color:#e0fdfd;
                border-bottom:2px solid #00b2b2;
            }

            QStatusBar {
                background:#1e1e1e;
                color:#d0d0d0;
            }

            QWidget#PreviewFrame {
                background:#101317;
                border:2px solid #444;
                border-radius:6px;
            }
        """)

        # Central layout
        self.main_widget = QtWidgets.QWidget(self)
        self.main_widget.setObjectName("NexusRoot")
        self.main_widget.setAttribute(Qt.WA_StyledBackground, True)
        main_layout = QtWidgets.QVBoxLayout(self.main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.main_layout = main_layout

        # Title bar
        self.title_bar = TitleBar(self)
        main_layout.addWidget(self.title_bar)

        # Tab bar
        self.tabbar = QtWidgets.QTabBar()
        self.tabbar.setObjectName("MainTabBar")
        for name in ("Images", "Sound", "Message", "Forge", "Command"):
            self.tabbar.addTab(name)
        self.tabbar.setDrawBase(False)
        self.tabbar.setMouseTracking(True)
        self.tabbar.setAttribute(Qt.WA_Hover, True)
        self.tabbar.currentChanged.connect(self._tab_changed)
        main_layout.addWidget(self.tabbar)
        self._command_immersive = False
        self._command_status_was_visible = True

        # Body
        self.body = QtWidgets.QWidget()
        self.body.setObjectName("NexusBody")
        self.body.setAttribute(Qt.WA_StyledBackground, True)
        body_layout = QtWidgets.QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)
        self.body_layout = body_layout

        # Preview frame (centered)
        self.preview_frame = QtWidgets.QWidget()
        self.preview_frame.setObjectName("PreviewFrame")
        pf_layout = QVBoxLayout(self.preview_frame)
        pf_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_stack = QtWidgets.QStackedWidget(self.preview_frame)
        self.preview_stack.setObjectName("PreviewStack")

        self.preview_blank = QtWidgets.QWidget()
        self.preview_blank.setObjectName("PreviewBlank")
        self.preview_blank.setAttribute(Qt.WA_StyledBackground, True)
        self.preview_stack.addWidget(self.preview_blank)

        self.image_preview = QtWidgets.QLabel(alignment=Qt.AlignCenter)
        self.image_preview.setStyleSheet("background:transparent;")
        self.preview_stack.addWidget(self.image_preview)

        self.html_preview = QWebEngineView()
        self.html_preview.setStyleSheet("background-color:#1e1e1e;")
        self.html_preview.page().setBackgroundColor(QColor("#1e1e1e"))
        self.html_preview.settings().setAttribute(
            QWebEngineSettings.FullScreenSupportEnabled,
            True,
        )
        self.html_preview.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessFileUrls,
            True,
        )
        self.html_preview.page().fullScreenRequested.connect(
            self._on_web_fullscreen_requested
        )
        self.html_preview.installEventFilter(self)
        self.preview_stack.addWidget(self.html_preview)

        pf_layout.addWidget(self.preview_stack)
        body_layout.addWidget(self.preview_frame, alignment=Qt.AlignHCenter)

        # Caption under preview (Forge tab: project title)
        self.preview_caption = QLabel("", alignment=Qt.AlignCenter)
        self.preview_caption.setVisible(False)
        self.preview_caption.setStyleSheet(
            "color:#e0ffff; font:12px 'Segoe UI Semibold'; padding:4px 6px;"
        )
        body_layout.addWidget(self.preview_caption, alignment=Qt.AlignHCenter)

        # =============================================================================================
        # Help (top-right above the feature panel) ΓÇö dual-GIF swap
        # Idle = Help.gif (plays constantly), Hover = HHelp.gif
        # =============================================================================================
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.setSpacing(0)
        help_row.addStretch(1)
        self.preview_tools_layout = help_row

        self.help_icon = QLabel()
        self.help_icon.setObjectName("HelpIcon")
        self.help_icon.setAutoFillBackground(False)
        self.help_icon.setAttribute(Qt.WA_NoSystemBackground, True)
        self.help_icon.setAttribute(Qt.WA_TranslucentBackground, True)
        self.help_icon.setStyleSheet("""
            QLabel#HelpIcon {
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }
        """)
        self.help_icon.setCursor(Qt.WhatsThisCursor)
        self.help_icon.setAccessibleName("Help ΓÇö instructions for this tab")
        self.help_icon.setToolTip("Help")
        self.help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self.help_icon.setMouseTracking(True)
        self.help_icon.setAttribute(Qt.WA_Hover, True)

        # Movies: idle + hover
        self._help_movie_idle: Optional[QMovie] = None
        self._help_movie_hover: Optional[QMovie] = None

        def _abs(rel: str) -> str:
            return os.path.join(self.project_root, rel)

        def _load_movie(path: str) -> Optional[QMovie]:
            if os.path.exists(path):
                mv = QMovie(path)
                if mv.isValid():
                    mv.setCacheMode(QMovie.CacheAll)
                    mv.setSpeed(100)
                    mv.setScaledSize(QSize(HELP_ICON_PX, HELP_ICON_PX))
                    mv.start()  # play constantly
                    return mv
            return None

        self._help_movie_idle  = _load_movie(_abs(REL_HELP_GIF))
        self._help_movie_hover = _load_movie(_abs(REL_HELP_HOVER))

        # Initial visual: prefer idle movie ΓåÆ hover movie ΓåÆ PNG fallback ΓåÆ text
        if self._help_movie_idle:
            self.help_icon.setMovie(self._help_movie_idle)
        elif self._help_movie_hover:
            self.help_icon.setMovie(self._help_movie_hover)
        else:
            self._set_help_fallback_icon(_abs(REL_HELP_PNG))

        # Legacy-compat shim (prevents AttributeError in any old slots)
        self._help_movie = None

        help_row.addWidget(self.help_icon, 0, Qt.AlignRight)
        body_layout.addLayout(help_row)

        # Help popover (top-level tooltip window)
        self.help_pop = HelpPopover(self.body)

        # Show/hide timers for hover UX
        self._help_show_timer = QtCore.QTimer(self)
        self._help_show_timer.setSingleShot(True)
        self._help_show_timer.setInterval(150)  # 120ΓÇô160 ms
        self._help_show_timer.timeout.connect(self._show_help_from_icon)

        self._help_hide_timer = QtCore.QTimer(self)
        self._help_hide_timer.setSingleShot(True)
        self._help_hide_timer.setInterval(360)  # 300ΓÇô400 ms
        self._help_hide_timer.timeout.connect(self._hide_help_popover)

        # Install event filters to manage hover persistence and GIF swap
        self.help_icon.installEventFilter(self)
        self.help_pop.installEventFilter(self)

        # Feature tabs
        self.page_stack = QtWidgets.QStackedWidget()
        self.page_stack.setObjectName("FeatureStack")
        body_layout.addWidget(self.page_stack)

        self.recipient_page = RecipientPage(self.main_widget)
        self.recipient_page.recipient_submitted.connect(
            self._accept_recipient
        )
        self.application_stack = QtWidgets.QStackedWidget(self.main_widget)
        self.application_stack.setObjectName("ApplicationStack")
        self.application_stack.addWidget(self.body)
        self.application_stack.addWidget(self.recipient_page)
        main_layout.addWidget(self.application_stack)
        self.setCentralWidget(self.main_widget)
        self._project_loading_overlay = _ProjectLoadingOverlay(
            self.main_widget
        )

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # Toast overlay
        self._toast = QLabel(self)
        self._toast.setStyleSheet(
            "QLabel{background:rgba(0,0,0,0.7); color:#e0ffff; border-radius:6px; padding:8px 12px;}"
        )
        self._toast.setVisible(False)
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast.setVisible(False))

        # Preview fade state
        self._fade_timer: Optional[QtCore.QTimer] = None
        self._fade_anim: Optional[QtCore.QPropertyAnimation] = None

        # Message-tab detail visibility is owned by Over_Nexus.
        self._over = install_over_nexus(self)


        # Optional spark overlay
        self._spark = ParticleBurst(self.body) if ParticleBurst else None
        if self._spark:
            self._spark.setGeometry(self.body.rect())
            self._spark.hide()

        # Optional tab switcher animations. This remains responsible only for
        # ordinary tab-to-tab movement. Command transitions bypass it entirely.
        self._tabswitch = None

        # Command transition state. Fixed body snapshots prevent Command from
        # inheriting movement from page-stack resizing or preview visibility.
        self._command_fade_animation: Optional[QtCore.QPropertyAnimation] = None
        self._command_fade_effect: Optional[QGraphicsOpacityEffect] = None
        self._command_fade_overlays: list[QLabel] = []
        self._command_fade_generation = 0

        # === Mounted Sound visualizer state ===
        self._sound_preview_widget: Optional[QtWidgets.QWidget] = None
        self._sound_preview_index: Optional[int] = None
        self._forge_preview_mode = "portrait"
        self._forge_preview_generation = 0

        # Remember last image pixmap for proper re-scaling on resize
        self._last_pixmap: Optional[QPixmap] = None

        # Keep a reference to Prompt Writer window if opened via shortcut
        self._prompt_writer_win: Optional[QtWidgets.QWidget] = None

        # Initial sizing & tab
        self.setMinimumSize(1180, 820)
        self.resize(WIN_W, WIN_H)

        # Shortcuts + click effects
        self._install_shortcuts()
        try:
            install_click_fx(self)
        except Exception:
            pass

        # Double-click filter for full message view
        self._dbl_filter = _DoubleClickFilter(self)
        self.html_preview.installEventFilter(self._dbl_filter)

        self.project_state.add_listener(self._on_project_state_transition)
        self._apply_application_state(initial_project_state)

        # Diagnostics after event loop starts
        QtCore.QTimer.singleShot(0, self._post_init_diagnostics)

    def _initialize_project_tabs(self) -> None:
        if self._project_tabs_initialized:
            return
        try:
            from Image_tab import ImageTab
            from sound_tab import SoundTab
            from Message_tab import MessageTab
            from Forge_Tab import ForgeTab
            from command import CommandTab
        except Exception as ex:
            raise ImportError(f"Failed to import feature tabs: {ex}") from ex

        self.image_tab = ImageTab(
            self.project_root,
            project_state=self.project_state,
            project_paths=self.project_paths,
        )
        self.sound_tab = SoundTab(
            self.project_root,
            project_state=self.project_state,
            project_paths=self.project_paths,
        )
        self.message_tab = MessageTab(
            self.project_root,
            project_state=self.project_state,
            project_paths=self.project_paths,
        )
        self.forge_tab = ForgeTab(
            self.project_root,
            project_state=self.project_state,
            project_paths=self.project_paths,
        )
        self.command_tab = CommandTab(
            self.project_root,
            project_state=self.project_state,
        )

        self.preview_tools_layout.insertWidget(
            0,
            self.forge_tab.preview_format_panel,
            0,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        self.forge_tab.preview_format_panel.setVisible(False)

        self.image_page = self._make_page_surface(
            "ImagePageSurface",
            self.image_tab,
        )
        self.sound_page = self._make_page_surface(
            "SoundPageSurface",
            self.sound_tab,
        )
        self.message_page = self._make_page_surface(
            "MessagePageSurface",
            self.message_tab,
        )
        self.forge_page = self._make_page_surface(
            "ForgePageSurface",
            self.forge_tab,
        )
        self.command_page = self._make_page_surface(
            "CommandPageSurface",
            self.command_tab,
        )
        for page in (
            self.image_page,
            self.sound_page,
            self.message_page,
            self.forge_page,
            self.command_page,
        ):
            self.page_stack.addWidget(page)

        self.forge_tab.attach_readiness_window(self)
        self.forge_tab.project_restored.connect(self._on_project_restored)
        self.forge_tab.correction_requested.connect(
            self._route_forge_correction
        )
        self.forge_tab.preview_requested.connect(self._load_forge_preview)
        self.forge_tab.preview_visibility_changed.connect(
            self._set_forge_preview_visible
        )
        self.forge_tab.preview_files_release_requested.connect(
            self._release_forge_preview_files
        )
        self.forge_tab.project_files_release_requested.connect(
            self._release_project_files_for_restore
        )
        self.forge_tab.restore_activity_changed.connect(
            self._set_restore_activity
        )
        self.forge_tab.published_url_changed.connect(
            lambda url: self.message_tab.set_published_page_url(
                url,
                persist=False,
                announce=False,
            )
        )
        self.message_tab.published_page_url_changed.connect(
            self.forge_tab.set_saved_page_url
        )
        self.image_tab.image_selected.connect(
            lambda _pixmap: self.forge_tab.schedule_refresh()
        )
        self.image_tab.clear_preview.connect(self.forge_tab.schedule_refresh)
        self.sound_tab.project_sound.changed.connect(
            self.forge_tab.schedule_refresh
        )
        self.message_tab.project_changed.connect(
            self.forge_tab.schedule_refresh
        )
        self.image_tab.image_selected.connect(self._show_image)
        self.image_tab.hover_preview_image.connect(self._show_image)
        self.image_tab.clear_preview.connect(
            self._on_image_tab_clear_preview
        )
        self.message_tab.preview_image.connect(self._show_image)
        self.message_tab.wall_preview.connect(self._show_image)
        self.message_tab.text_selected.connect(self._show_html)
        self.command_tab.wiped.connect(self._on_command_wiped)

        self._tabswitch = (
            TabSwitcher(self.page_stack)
            if TabSwitcher is not None
            else None
        )
        self._project_tabs_initialized = True
        self.tabbar.setCurrentIndex(0)
        self._tab_changed(0)

    def _on_project_state_transition(
        self,
        previous: ApplicationState,
        current: ApplicationState,
    ) -> None:
        def apply_transition() -> None:
            if (
                current is ApplicationState.RECIPIENT_REQUIRED
                and previous is not ApplicationState.BOOTING
            ):
                self.recipient_page.reset()
            self._apply_application_state(current)

        QtCore.QTimer.singleShot(0, apply_transition)

    def _apply_application_state(
        self,
        state: ApplicationState,
    ) -> None:
        ready = (
            state is ApplicationState.PROJECT_READY
            and self.project_state.is_project_ready
        )
        if not ready and self._command_immersive:
            self._set_command_immersive(False)
        self.tabbar.setVisible(ready)
        if ready:
            self._initialize_project_tabs()
            self.body.setEnabled(True)
            self.application_stack.setCurrentWidget(self.body)
            self.status("Ready.")
            self.toast("Welcome to Letter Smith")
            return

        if (
            state in {
                ApplicationState.PROJECT_LOADING,
                ApplicationState.PROJECT_MIGRATING,
            }
            and self.application_stack.currentWidget() is self.body
        ):
            self.body.setEnabled(False)
            self.status("Loading saved letter…")
            return

        self.body.setEnabled(False)
        self.application_stack.setCurrentWidget(self.recipient_page)
        self.recipient_page.focus_recipient()
        self.status("A recipient is required before editing.")

    def _accept_recipient(
        self,
        recipient: str,
        custom_capitalization: bool,
    ) -> None:
        try:
            forge_tab = getattr(self, "forge_tab", None)
            if (
                forge_tab is not None
                and forge_tab.has_pending_recipient_assignment()
            ):
                if forge_tab.assign_pending_recipient(
                    recipient,
                    custom_capitalization=custom_capitalization,
                ):
                    return
            self.project_state.establish_project(
                recipient,
                custom_capitalization=custom_capitalization,
            )
        except (RuntimeError, ValueError, OSError) as error:
            self.recipient_page.show_error(str(error))

    def _make_page_surface(
        self,
        object_name: str,
        content: QtWidgets.QWidget,
    ) -> QtWidgets.QWidget:
        """Wrap a feature tab in a page-owned background surface."""
        surface = QtWidgets.QWidget(self.page_stack)
        surface.setObjectName(object_name)
        surface.setAttribute(Qt.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content)

        # The feature widget itself remains transparent unless it deliberately
        # paints one of its own panels. Its unused regions therefore reveal the
        # correct page surface instead of the Nexus shell.
        content.setAutoFillBackground(False)
        return surface

    # =============================================================================================
    # Diagnostics: show key state once live
    # =============================================================================================
    def _post_init_diagnostics(self) -> None:
        over = getattr(self, "_over", None)
        panel = getattr(over, "panel", None) if over is not None else None
        print(f"[Boot] Nexus visible={self.isVisible()} minimized={self.isMinimized()} "
              f"over_panel={'yes' if panel else 'no'}")

    # =============================================================================================
    # Shortcuts
    # =============================================================================================
    def _install_shortcuts(self) -> None:
        # Ctrl+Alt+P opens prompt writer (no button)
        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+P"), self)
        sc.activated.connect(self.open_prompt_writer)

        # Ctrl+H toggles help popover
        sc2 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+H"), self)
        sc2.activated.connect(lambda: (self._show_help_from_icon() if not self.help_pop.isVisible() else self._hide_help_popover()))

    # =============================================================================================
    # Status / Toast utilities
    # =============================================================================================
    def status(self, msg: str) -> None:
        try:
            self._status.showMessage(msg, 5000)
        except Exception:
            pass

    def toast(self, msg: str, ms: int = 1200) -> None:
        if not msg:
            return
        try:
            self._toast.setText(msg)
            self._toast.adjustSize()
            # place top-center, below title bar
            x = max(10, (self.width() - self._toast.width()) // 2)
            y = 48
            self._toast.move(x, y)
            self._toast.setVisible(True)
            self._toast.raise_()
            self._toast_timer.start(ms)
        except Exception:
            pass

    # =============================================================================================
    # Sound preview mounting (widget-based visualizer)
    # =============================================================================================
    def _mount_sound_preview(self, widget: QtWidgets.QWidget) -> None:
        if not isinstance(widget, QtWidgets.QWidget):
            return

        if self._sound_preview_widget is not None and self._sound_preview_widget is not widget:
            self._detach_sound_preview()

        self._sound_preview_widget = widget
        index = self.preview_stack.indexOf(widget)
        if index < 0:
            index = self.preview_stack.addWidget(widget)
        self._sound_preview_index = index

        if self.tabbar.currentIndex() == 1:
            widget.show()
            self.preview_stack.setCurrentIndex(index)

    def _detach_sound_preview(self) -> None:
        """Remove the Sound-owned widget from the shared preview surface."""
        widget = self._sound_preview_widget
        if widget is None:
            self._sound_preview_index = None
            return

        if self.preview_stack.currentWidget() is widget:
            self.preview_stack.setCurrentIndex(0)
        if self.preview_stack.indexOf(widget) >= 0:
            self.preview_stack.removeWidget(widget)
        widget.hide()
        widget.setParent(self.sound_tab)
        self._sound_preview_index = None

    def _on_project_restored(self, _payload: Optional[dict] = None) -> None:
        """Coordinate public refresh contracts after an atomic restore."""
        refreshers = (
            ("Images", self.image_tab.refresh_from_disk),
            ("Message", self.message_tab.refresh_from_disk),
            ("Sound", self.sound_tab.refresh_from_disk),
        )
        for owner, refresh in refreshers:
            try:
                refresh()
            except Exception as error:
                self.status(f"{owner} could not refresh: {error}")
        self.forge_tab.refresh_project_state()
        self.forge_tab.refresh_saved_letters()
        self._show_forge_preview()

    def _route_forge_correction(self, tab: str, target: str) -> None:
        destinations = {
            "images": (0, self.image_tab.focus_asset_slot),
            "sound": (1, self.sound_tab.focus_music_editor),
            "message": (2, self.message_tab.focus_field),
        }
        destination = destinations.get(str(tab))
        if destination is None:
            return
        index, focus = destination
        self.tabbar.setCurrentIndex(index)
        if tab == "sound":
            QtCore.QTimer.singleShot(0, focus)
        else:
            QtCore.QTimer.singleShot(0, lambda: focus(target))

    # =============================================================================================
    # Message double-click ΓåÆ full dialog preview
    # =============================================================================================
    def _on_message_double_click(self):
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Message Preview")
            dlg.resize(900, 700)

            lay = QVBoxLayout(dlg)
            view = QWebEngineView(dlg)
            lay.addWidget(view)

            # Clone current html from main view
            url = getattr(self.message_tab, "last_preview_url", None)
            if isinstance(url, QUrl):
                view.setUrl(url)
            else:
                view.setHtml("<html><body style='background:#1e1e1e; color:#e0ffff; font-family:Segoe UI;'>"
                             "<h3>No preview URL available</h3></body></html>")

            self.status("Message preview opened.")
            dlg.exec()
        except Exception as ex:
            self.status(f"Γ¥î Message preview failed: {ex}")

    # =============================================================================================
    # Tabs ΓÇö show correct preview per tab + update help visibility/content
    # =============================================================================================
    def _tab_changed(self, idx: int) -> None:
        """
        Route the requested tab transition.

        Ordinary tabs keep the shared slide animation. Any transition entering
        or leaving Command bypasses TabSwitcher and uses a fixed body-snapshot
        fade so no page, preview, or help movement leaks through.
        """
        if (
            not self._project_tabs_initialized
            or not self.project_state.is_project_ready
        ):
            return
        if idx < 0 or idx >= self.page_stack.count():
            return

        # A new request supersedes an in-progress Command fade. Its target page
        # was already committed underneath the snapshots.
        self._cancel_command_fade()
        old_idx = self.page_stack.currentIndex()

        if old_idx != idx and (old_idx == 4 or idx == 4):
            self._run_command_transition(idx)
            return

        self._apply_tab_state(idx, animate_page=(old_idx != idx))

    def _set_command_immersive(self, active: bool) -> None:
        """Let Command cover all app content while retaining hover navigation."""
        active = bool(active)
        if self._command_immersive == active:
            if active:
                self._position_command_tabbar()
            return

        self._command_immersive = active
        if active:
            self._command_status_was_visible = self.statusBar().isVisible()
            self.statusBar().hide()
            self.main_layout.removeWidget(self.tabbar)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(0)
            self.tabbar.setProperty("commandOverlay", True)
            self._refresh_tabbar_style()
            self.main_layout.invalidate()
            self.main_layout.activate()
            self._position_command_tabbar()
            return

        self.tabbar.setProperty("commandOverlay", False)
        self._refresh_tabbar_style()
        self.main_layout.insertWidget(1, self.tabbar)
        self.body_layout.setContentsMargins(12, 12, 12, 12)
        self.body_layout.setSpacing(10)
        if self._command_status_was_visible:
            self.statusBar().show()
        self.main_layout.invalidate()
        self.main_layout.activate()

    def _refresh_tabbar_style(self) -> None:
        style = self.tabbar.style()
        style.unpolish(self.tabbar)
        style.polish(self.tabbar)
        self.tabbar.update()

    def _position_command_tabbar(self) -> None:
        if not self._command_immersive:
            return
        height = max(1, self.tabbar.sizeHint().height())
        content_top = self.application_stack.geometry().top()
        self.tabbar.setGeometry(
            0,
            content_top,
            self.main_widget.width(),
            height,
        )
        self.tabbar.show()
        self.tabbar.raise_()

    def _apply_tab_state(self, idx: int, *, animate_page: bool) -> None:
        """Apply the complete settled UI state for one tab."""
        old_idx = self.page_stack.currentIndex()
        if old_idx != idx:
            if old_idx == 2:
                try:
                    self.message_tab.deactivate_for_tab_change()
                except Exception as ex:
                    self.status(f"Message could not save on exit: {ex}")
            elif old_idx == 3:
                try:
                    self.forge_tab.deactivate_for_tab_change()
                except Exception as ex:
                    self.status(f"Forge could not reset on exit: {ex}")

        if idx != 1:
            try:
                self.sound_tab.deactivate_for_tab_change()
            except Exception:
                pass
            self._detach_sound_preview()

        self._last_pixmap = None
        self._clear_preview()
        self.preview_stack.setCurrentIndex(0)
        self.preview_caption.setVisible(False)
        self.preview_frame.setVisible(idx != 4)
        self.forge_tab.preview_format_panel.setVisible(idx == 3)
        self._set_command_immersive(idx == 4)
        self._update_preview_tools_geometry()

        if animate_page and self._tabswitch is not None:
            self._tabswitch.go_to(idx)
        else:
            self._stop_shared_tab_animation()
            self.page_stack.setCurrentIndex(idx)

        tab_name = self.tabbar.tabText(idx)
        self.status(f"Switched to: {tab_name}")
        self._set_forge_preview_visible(idx == 3)
        self.forge_tab.set_readiness_context_visible(
            idx in {0, 1, 2, 3}
        )

        if idx == 0:
            self.preview_stack.setCurrentIndex(0)
        elif idx == 1:
            try:
                self.sound_tab.activate_for_tab_change()
                self._mount_sound_preview(
                    self.sound_tab.shared_preview_widget()
                )
            except Exception as ex:
                self.status(f"ΓÜá∩╕Å Sound preview unavailable: {ex}")
            if self._sound_preview_index is not None:
                self.preview_stack.setCurrentIndex(self._sound_preview_index)
            else:
                self.preview_stack.setCurrentIndex(0)
        elif idx == 2:
            try:
                self.message_tab.activate_for_tab_change()
            except Exception as ex:
                self.status(f"Message could not refresh: {ex}")
            QtCore.QTimer.singleShot(0, self._request_message_preview)
        elif idx == 3:
            try:
                self.forge_tab.activate_for_tab_change()
            except Exception as ex:
                self.status(f"Forge could not refresh: {ex}")
            QtCore.QTimer.singleShot(0, self._show_forge_preview)
        else:
            self.preview_stack.setCurrentIndex(0)

        self.help_icon.setVisible(idx != 4)
        if self.help_pop.isVisible():
            if idx == 4:
                self._hide_help_popover()
            else:
                self._refresh_help_text(idx)
                self._reposition_help_popover()

        QtCore.QTimer.singleShot(0, self._update_preview_geometry)

    def _update_preview_tools_geometry(self) -> None:
        forge_visible = self.tabbar.currentIndex() == 3
        left_margin = (
            max(0, int(self.width() * 0.085))
            if forge_visible
            else 0
        )
        self.preview_tools_layout.setContentsMargins(left_margin, 0, 0, 0)

    def _stop_shared_tab_animation(self) -> None:
        """Stop and clean the ordinary TabSwitcher without starting another."""
        switcher = self._tabswitch
        if switcher is None:
            return

        stop = getattr(switcher, "_stop_active", None)
        if callable(stop):
            try:
                stop()
                return
            except Exception:
                pass

        active = getattr(switcher, "_active", None)
        if active is not None:
            try:
                active.stop()
            except Exception:
                pass

    def _grab_body_snapshot(self) -> QPixmap:
        """Capture the body without including temporary transition overlays."""
        visibility: list[tuple[QLabel, bool]] = []
        for overlay in self._command_fade_overlays:
            try:
                visibility.append((overlay, overlay.isVisible()))
                overlay.hide()
            except RuntimeError:
                pass

        try:
            snapshot = self.body.grab()
        finally:
            for overlay, was_visible in visibility:
                try:
                    overlay.setVisible(was_visible)
                    if was_visible:
                        overlay.raise_()
                except RuntimeError:
                    pass

        return snapshot

    def _make_body_snapshot_overlay(self, pixmap: QPixmap) -> QLabel:
        """Create a mouse-transparent snapshot over the complete body."""
        overlay = QLabel(self.body)
        overlay.setObjectName("CommandTransitionSnapshot")
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.setAttribute(Qt.WA_NoSystemBackground, True)
        overlay.setStyleSheet(
            "QLabel#CommandTransitionSnapshot {"
            "background: transparent; border: none; padding: 0; margin: 0;"
            "}"
        )
        overlay.setGeometry(self.body.rect())
        overlay.setPixmap(pixmap)
        overlay.setScaledContents(False)
        overlay.show()
        overlay.raise_()
        self._command_fade_overlays.append(overlay)
        return overlay

    def _settle_body_layout_for_snapshot(self) -> None:
        """Commit pending destination layout while the old snapshot masks it."""
        try:
            body_layout = self.body.layout()
            if body_layout is not None:
                body_layout.invalidate()
                body_layout.activate()

            stack_layout = self.page_stack.layout()
            if stack_layout is not None:
                stack_layout.invalidate()
                stack_layout.activate()

            self.body.updateGeometry()
            self.page_stack.updateGeometry()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents
                | QtCore.QEventLoop.ExcludeSocketNotifiers
            )
        except Exception:
            pass

    def _run_command_transition(self, new_idx: int) -> None:
        """
        Fade only Command while the complete source or destination stays fixed.

        Entering Command fades a settled Command snapshot over the old body.
        Leaving Command fades the old Command snapshot over the settled target.
        """
        self._stop_shared_tab_animation()
        self._cancel_command_fade()

        old_snapshot = self._grab_body_snapshot()
        old_overlay = self._make_body_snapshot_overlay(old_snapshot)

        self._apply_tab_state(new_idx, animate_page=False)
        self._settle_body_layout_for_snapshot()

        entering_command = new_idx == 4
        if entering_command:
            command_snapshot = self._grab_body_snapshot()
            fading_overlay = self._make_body_snapshot_overlay(command_snapshot)
            start_opacity = 0.0
            end_opacity = 1.0
        else:
            fading_overlay = old_overlay
            start_opacity = 1.0
            end_opacity = 0.0

        effect = QGraphicsOpacityEffect(fading_overlay)
        effect.setOpacity(start_opacity)
        fading_overlay.setGraphicsEffect(effect)

        animation = QtCore.QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(COMMAND_FADE_MS)
        animation.setStartValue(start_opacity)
        animation.setEndValue(end_opacity)
        animation.setEasingCurve(QtCore.QEasingCurve.InOutSine)

        self._command_fade_generation += 1
        generation = self._command_fade_generation
        self._command_fade_effect = effect
        self._command_fade_animation = animation

        def finish() -> None:
            if generation != self._command_fade_generation:
                return
            self._clear_command_fade_objects()

        animation.finished.connect(finish)
        animation.start()

    def _clear_command_fade_objects(self) -> None:
        """Remove transition effects and snapshots without changing pages."""
        animation = self._command_fade_animation
        self._command_fade_animation = None
        self._command_fade_effect = None

        overlays = self._command_fade_overlays
        self._command_fade_overlays = []
        for overlay in overlays:
            try:
                overlay.setGraphicsEffect(None)
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                pass

        if animation is not None:
            try:
                animation.deleteLater()
            except RuntimeError:
                pass

    def _cancel_command_fade(self) -> None:
        """Cancel a running fade and leave its committed target visible."""
        self._command_fade_generation += 1
        animation = self._command_fade_animation
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
        self._clear_command_fade_objects()

    # =============================================================================================
    # Preview rendering (image/html) + fade behavior
    # =============================================================================================
    def _show_image(self, pixmap: QPixmap):
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return
        self._last_pixmap = pixmap
        self._clear_preview()
        scaled = pixmap.scaled(
            max(1, self.preview_frame.width() - 12),
            max(1, self.preview_frame.height() - 12),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_preview.setPixmap(scaled)
        self.preview_stack.setCurrentWidget(self.image_preview)
        # gentle fade after 30s idle
        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_preview)
        self._fade_timer.start(30000)

    def _show_html(self, html: str):
        self._clear_preview()
        self.html_preview.setHtml(html or "<p></p>")
        self.preview_stack.setCurrentWidget(self.html_preview)
        self.status("HTML preview updated.")

    def _fade_preview(self):
        effect = QGraphicsOpacityEffect(self.image_preview)
        self.image_preview.setGraphicsEffect(effect)
        self._fade_anim = QtCore.QPropertyAnimation(effect, b"opacity", self)
        self._fade_anim.setDuration(1000)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._clear_preview)
        self._fade_anim.start()

    def _on_image_tab_clear_preview(self) -> None:
        # Hard-clear: also drop last pixmap so resize can't resurrect it
        self._last_pixmap = None
        self._clear_preview()

    def _clear_preview(self):
        if hasattr(self, "_fade_timer") and self._fade_timer and self._fade_timer.isActive():
            self._fade_timer.stop()
        self._fade_timer = None
        if hasattr(self, "_fade_anim") and self._fade_anim and self._fade_anim.state() == QtCore.QPropertyAnimation.Running:
            self._fade_anim.stop()
        self._fade_anim = None
        eff = self.image_preview.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            self.image_preview.setGraphicsEffect(None)
        self.image_preview.clear()

    def _on_command_wiped(self) -> None:
        """Clear every live project surface after one confirmed reset."""
        try:
            self.message_tab.sync_from_disk(force=True)
        except Exception:
            pass
        try:
            self.forge_tab.reset_after_project_wipe()
        except Exception:
            pass
        try:
            self._release_forge_preview_files()
        except Exception:
            pass
        self._last_pixmap = None
        self._clear_preview()
        try:
            self.preview_stack.setCurrentIndex(0)
        except Exception:
            pass
        try:
            self.preview_caption.setVisible(False)
        except Exception:
            pass

    def _request_message_preview(self) -> None:
        """Ask MessageTab to emit whichever preview it thinks is correct."""
        try:
            if hasattr(self.message_tab, "refresh_preview"):
                self.message_tab.refresh_preview()  # type: ignore[attr-defined]
                return
            if hasattr(self.message_tab, "_emit_best_preview"):
                self.message_tab._emit_best_preview()  # type: ignore[attr-defined]
                return
            if hasattr(self.message_tab, "_emit_preview"):
                self.message_tab._emit_preview()  # type: ignore[attr-defined]
                return
        except Exception:
            pass

    def _show_forge_preview(self) -> None:
        """Show the actual generated viewer, never a static Forge stand-in."""
        try:
            self.forge_tab.ensure_preview_current()
        except Exception as ex:
            self._release_forge_preview_files()
            self.preview_caption.setText(
                f"Forge preview unavailable: {ex}"
            )
            self.preview_caption.setVisible(True)
            self.status(f"Forge preview could not be rebuilt: {ex}")
            return

        index = self.forge_tab.current_play_index()
        if index is not None:
            self._load_forge_preview(
                str(index),
                self.forge_tab.preview_mode_value,
            )
            return
        self._last_pixmap = None
        self._clear_preview()
        self.preview_stack.setCurrentIndex(0)
        self.preview_caption.setText(
            "Select Preview Letter to build the interactive viewer."
        )
        self.preview_caption.setVisible(True)

    def _load_forge_preview(self, index_path: str, mode: str) -> None:
        index = Path(index_path)
        if not index.is_file():
            self.status("The playable letter is missing. Preview it again.")
            return
        self._forge_preview_mode = (
            mode if mode in {"portrait", "landscape", "window"} else "portrait"
        )
        self._update_preview_geometry()
        self._last_pixmap = None
        try:
            modified = index.stat().st_mtime_ns
        except OSError:
            modified = None

        current_url = self.html_preview.url()
        current_path = Path(current_url.toLocalFile()) if current_url.isLocalFile() else None
        same_build = False
        if current_path is not None:
            try:
                same_build = current_path.resolve() == index.resolve()
            except OSError:
                same_build = False
        if same_build and modified is not None:
            same_build = current_url.query().startswith(
                f"lettersmith={modified}-"
            )

        self._clear_preview()
        self.preview_caption.setVisible(False)
        self.preview_stack.setCurrentWidget(self.html_preview)
        if same_build:
            self.status(
                f"Interactive letter preview: "
                f"{self._forge_preview_mode.replace('-', ' ')}"
            )
            return

        self._forge_preview_generation += 1
        viewer_url = QUrl.fromLocalFile(str(index.resolve()))
        cache_token = (
            modified
            if modified is not None
            else self._forge_preview_generation
        )
        viewer_url.setQuery(
            f"lettersmith={cache_token}-{self._forge_preview_generation}"
        )
        self.html_preview.setUrl(viewer_url)
        self.status(
            f"Interactive letter preview: "
            f"{self._forge_preview_mode.replace('-', ' ')}"
        )

    def _set_forge_preview_visible(self, visible: bool) -> None:
        if visible:
            return
        self.html_preview.page().runJavaScript(
            "document.querySelectorAll('audio,video').forEach("
            "media => { try { media.pause(); } catch (_) {} });"
        )

    def _release_forge_preview_files(self) -> None:
        """Stop playback and release the generated viewer's file handles."""
        if self._forge_fullscreen_active:
            self._restore_forge_preview_from_fullscreen()
        try:
            self.html_preview.page().runJavaScript(
                "document.querySelectorAll('audio,video').forEach("
                "media => { try { media.pause(); media.currentTime = 0; } catch (_) {} });"
            )
        except Exception:
            pass
        if self.preview_stack.currentWidget() is self.html_preview:
            self.preview_stack.setCurrentIndex(0)
        self.preview_caption.setVisible(False)
        self.html_preview.stop()
        self.html_preview.setUrl(QUrl("about:blank"))

    def _release_project_files_for_restore(self) -> None:
        """Release project-owned media handles before an atomic restore."""
        try:
            self.sound_tab.release_project_files_for_restore()
        except Exception:
            _LOGGER.exception("Sound files could not be released before restore.")

    @QtCore.Slot(bool, str)
    def _set_restore_activity(self, active: bool, message: str) -> None:
        if active:
            self._position_project_loading_overlay()
            self._project_loading_overlay.start(message)
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents
                | QtCore.QEventLoop.ExcludeSocketNotifiers
            )
            return
        self._project_loading_overlay.stop()

    def _position_project_loading_overlay(self) -> None:
        overlay = getattr(self, "_project_loading_overlay", None)
        if overlay is None:
            return
        top = self.title_bar.geometry().bottom() + 1
        overlay.setGeometry(
            0,
            top,
            self.main_widget.width(),
            max(0, self.main_widget.height() - top),
        )

    def restart_forge_preview(self, _reason: str = "") -> None:
        """Reset playback so every Forge entry starts at the curtain."""
        self._release_forge_preview_files()

    reset_forge_preview = restart_forge_preview

    def _on_web_fullscreen_requested(self, request) -> None:
        toggle_on = bool(request.toggleOn())
        if toggle_on:
            if self._forge_fullscreen_active:
                request.accept()
                return
            self._enter_forge_fullscreen()
            request.accept()
            return
        request.accept()
        self._restore_forge_preview_from_fullscreen()

    def _enter_forge_fullscreen(self) -> None:
        if self._forge_fullscreen_active:
            return
        window = self._forge_fullscreen_window
        if window is None:
            window = _ForgePreviewFullscreenWindow(self)
            window.exit_requested.connect(
                self._request_forge_fullscreen_exit
            )
            self._forge_fullscreen_window = window

        self._forge_fullscreen_active = True
        self.preview_stack.removeWidget(self.html_preview)
        window.attach_preview(self.html_preview)
        screen = self.screen()
        if screen is not None:
            window.setGeometry(screen.geometry())
        window.showFullScreen()
        window.raise_()
        window.activateWindow()
        self.html_preview.setFocus()

    def _request_forge_fullscreen_exit(self) -> None:
        if not self._forge_fullscreen_active:
            return
        try:
            self.html_preview.page().runJavaScript(
                "if (document.fullscreenElement) document.exitFullscreen();"
            )
        except RuntimeError:
            self._restore_forge_preview_from_fullscreen()
            return
        QtCore.QTimer.singleShot(
            250,
            lambda: (
                self._restore_forge_preview_from_fullscreen()
                if self._forge_fullscreen_active
                else None
            ),
        )

    def _restore_forge_preview_from_fullscreen(self, *_args) -> None:
        if not self._forge_fullscreen_active:
            return
        self._forge_fullscreen_active = False
        window = self._forge_fullscreen_window
        if window is not None:
            window.hide()
            window.detach_preview(self.html_preview)
        if self.preview_stack.indexOf(self.html_preview) < 0:
            self.preview_stack.addWidget(self.html_preview)
        self.preview_stack.setCurrentWidget(self.html_preview)
        QtCore.QTimer.singleShot(0, self._update_preview_geometry)
        self.html_preview.setFocus()

    def _read_project_title(self) -> str:
        """Read recipient_title from settings.json (best available 'project title' signal)."""
        try:
            settings_path = os.path.join(self.project_root, "settings.json")
            if os.path.exists(settings_path):
                data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    t = str(data.get("recipient_title", "")).strip()
                    return t
        except Exception:
            pass
        return ""

    # =============================================================================================
    # Resize/Move: keep preview aspect; keep popover aligned
    # =============================================================================================
    def _update_preview_geometry(self) -> None:
        """Keep Sound usable in normal windows without changing full-screen layout."""
        if self._forge_fullscreen_active:
            return
        window_height = max(1, self.height())
        window_width = max(1, self.width())
        body_width = max(1, self.body.width() - 24)
        body_height = max(1, self.body.height() - 24)
        try:
            current_tab = self.tabbar.currentIndex()
        except Exception:
            current_tab = -1

        sound_in_normal_window = (
            current_tab == 1
            and not self.isMaximized()
            and not self.isFullScreen()
        )

        if current_tab == 3:
            mode = getattr(self, "_forge_preview_mode", "portrait")
            max_h = max(
                1,
                min(
                    int(window_height * 0.34),
                    int(body_height * 0.45),
                ),
            )
            max_w = max(
                1,
                min(
                    int(window_width * 0.74),
                    body_width,
                ),
            )
            if mode == "portrait":
                h = max_h
                w = int(h * 0.8)
                if w > max_w:
                    w = max_w
                    h = int(w / 0.8)
            elif mode == "landscape":
                w = min(max_w, int(max_h * (16 / 9)))
                h = int(w * (9 / 16))
            else:
                w = max_w
                h = max_h
            self.preview_frame.setFixedSize(
                min(body_width, max(1, w + 12)),
                min(body_height, max(1, h + 12)),
            )
            return
        if sound_in_normal_window:
            # The preview content is naturally 169 x 253. Capping it near that
            # native height returns roughly 60-75 px to the Sound controls.
            h = max(220, min(253, int(window_height * 0.30)))
        else:
            # Preserve the existing appearance in maximized/full-screen mode
            # and on every other tab.
            h = int(window_height * 0.35)

        w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

    def resizeEvent(self, event):
        self._update_preview_geometry()
        self._update_preview_tools_geometry()
        self._position_command_tabbar()
        self._position_project_loading_overlay()
        if (
            getattr(self, "_project_tabs_initialized", False)
            and self.forge_tab.readiness_window.isVisible()
        ):
            self.forge_tab.readiness_window.position_near_image_area()

        # Reposition any visible toast
        if self._toast.isVisible():
            self.toast(self._toast.text(), ms=(self._toast_timer.remainingTime() or 800))

        # Cover the body with spark overlay if present
        if self._spark:
            self._spark.setGeometry(self.body.rect())

        # Rescale current image preview cleanly
        try:
            if self.preview_stack.currentWidget() is self.image_preview and self._last_pixmap and not self._last_pixmap.isNull():
                scaled = self._last_pixmap.scaled(
                    max(1, self.preview_frame.width() - 12),
                    max(1, self.preview_frame.height() - 12),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.image_preview.setPixmap(scaled)
        except Exception:
            pass

        # Keep help popover adjacent to the icon if visible
        if self.help_pop.isVisible():
            self._reposition_help_popover()

        super().resizeEvent(event)

    def moveEvent(self, event):
        # Keep help popover glued to the Help icon while the window moves
        if self.help_pop.isVisible():
            self._reposition_help_popover()
        if (
            getattr(self, "_project_tabs_initialized", False)
            and self.forge_tab.readiness_window.isVisible()
        ):
            self.forge_tab.readiness_window.position_near_image_area()
        super().moveEvent(event)

    def shutdown(self) -> None:
        """Release live tab and WebEngine resources exactly once."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True
        try:
            self.project_state.remove_listener(
                self._on_project_state_transition
            )
            self.project_state.shutdown()
            if self._project_tabs_initialized:
                try:
                    self._release_forge_preview_files()
                except Exception:
                    pass
                try:
                    self.forge_tab.deactivate_for_tab_change()
                    self.forge_tab.set_readiness_context_visible(False)
                    self.forge_tab.readiness_window.shutdown()
                except Exception:
                    pass
                try:
                    self.sound_tab.deactivate_for_tab_change()
                    self.sound_tab.release_current_file_handle()
                except Exception:
                    pass
                try:
                    self.message_tab.shutdown()
                except Exception:
                    pass
                try:
                    self.image_tab.shutdown()
                except Exception:
                    pass
        finally:
            self._shutdown_complete = True
            self._shutdown_in_progress = False

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        forge_tab = getattr(self, "forge_tab", None)
        if (
            self._project_tabs_initialized
            and forge_tab is not None
            and not forge_tab.shutdown_operations()
        ):
            self.status("Finish the current Forge operation before closing.")
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)

    # =============================================================================================
    # Target Browser (title-bar button)
    # =============================================================================================
    def open_target_browser(self):
        try:
            script = os.path.join(self.project_root, "target.py")
            if not os.path.exists(script):
                self.status("Γ¥î target.py not found")
                self.toast("target.py missing")
                return
            pos = QtGui.QCursor.pos()
            subprocess.Popen(
                [sys.executable, script, "--x", str(pos.x()), "--y", str(pos.y())],
                close_fds=True
            )
            self.status("Target Browser opened.")
            self.toast("Target Browser launched")
        except Exception as ex:
            self.status(f"Γ¥î Could not open Target Browser: {ex}")
            self.toast("Target launch failed")

    # =============================================================================================
    # Prompt Writer opener (used by Image_tab FAB and Ctrl+Alt+P shortcut)
    # =============================================================================================
    def reset_prompt_writer_state(self) -> bool:
        """Reset the owned Prompt Writer without constructing it just to clear state."""
        try:
            panel = getattr(self, "_prompt_writer_win", None)
            if isinstance(panel, QtWidgets.QWidget):
                result = panel.reset_prompt_writer_state()
            else:
                from PromptWriterPanel import reset_prompt_writer_state_file

                result = reset_prompt_writer_state_file(self.project_root)
            if not result:
                self.status("Prompt Writer reset failed; command was not completed.")
            return bool(result)
        except Exception as error:
            print(f"[PromptWriter] reset failed: {error}")
            self.status("Prompt Writer reset failed; command was not completed.")
            return False

    def open_prompt_writer(self):
        """
        Open Prompt Writer inline as an overlay panel (if available).
        The visible launcher button is owned by Image_tab.py; Ctrl+Alt+P also opens it.
        """
        if not self.project_state.is_project_ready:
            self.status("Enter a recipient before opening Prompt Writer.")
            self.recipient_page.focus_recipient()
            return
        w = getattr(self, "_prompt_writer_win", None)
        if isinstance(w, QtWidgets.QWidget):
            try:
                w.open_with_anim()
                w.raise_()
                w.activateWindow()
                self.status("Prompt Writer focused.")
                self.toast("Prompt Writer")
                return
            except RuntimeError:
                self._prompt_writer_win = None

        # In-process overlay panel
        try:
            from PromptWriterPanel import PromptWriterPanel  # local module in project root
            w = PromptWriterPanel(self)                      # parent=self so it overlays inside the main window
            self._prompt_writer_win = w
            w.destroyed.connect(self._on_prompt_writer_destroyed)
            w.dismissed.connect(self._on_prompt_writer_dismissed)
            w.open_with_anim()

            self.status("Prompt Writer opened (inline).")
            self.toast("Prompt Writer")
            return

        except Exception as ex:
            print(f"[PromptWriter] In-process load failed: {ex}")
        self.status("Prompt Writer could not be opened.")
        self.toast("Prompt Writer unavailable")

    def _on_prompt_writer_destroyed(self, *_args: object) -> None:
        self._prompt_writer_win = None

    def _on_prompt_writer_dismissed(self) -> None:
        self.status("Prompt Writer closed.")

    # =============================================================================================
    # Help icon support
    # =============================================================================================
    def _set_help_fallback_icon(self, png_path: str):
        if os.path.exists(png_path):
            pm = QPixmap(png_path)
            if not pm.isNull():
                pm = pm.scaled(HELP_ICON_PX, HELP_ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.help_icon.setPixmap(pm)
                return
        # Final fallback: small text
        self.help_icon.setText("Help")
        self.help_icon.setStyleSheet("""
            QLabel#HelpIcon {
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
                color: #8feaff;
                font-weight: 800;
            }
        """)

    def _refresh_help_text(self, idx: int):
        # Header (per tab)
        if idx == 0:
            header = "<b>✨The Images Tab✨</b>"
            body = (
                "Here you choose the four images that make up your letter: "
                "the Cover Page, Main Letter, Letter Background, and Final "
                "Backdrop. Click an image card to select or replace it, or "
                "hover over it to view it in the main preview. You can clear "
                "individual images, reset all four, open the Gallery, or use "
                "Prompt Writer to create image prompts."
            )
        elif idx == 1:
            header = "<b>✨The Sound Tab✨</b>"
            body = body = (
    "Here you can add background music to your letter. Choose a "
    "song from your computer or Music Archive, or create an optional "
    "playlist with multiple songs. Use the playback, volume, mute, "
    "ordering, and removal controls to review your music. Sound is "
    "optional, so leaving this tab empty will create a silent letter."
)
        elif idx == 2:
            header = "<b>✨The Message Tab✨</b>"
            body = (
                "Here you import, write, and refine the message inside your "
                "letter. Use Import to load an existing message, Edit to change "
                "its text and formatting, and Revisions to review saved versions. "
                "The message is limited to 1,000 words and appears in the main "
                "preview so you can inspect it before completing the letter."
            )
        elif idx == 3:
            header = "<b>✨The Forge Tab✨</b>"
            body = (
                "Here you assemble and manage the finished letter. Check readiness "
                "to find anything that is missing, load an existing saved letter, "
                "and use Preview Letter to test the complete interactive experience "
                "locally. Publish Letter creates the online version, Open Letter "
                "opens the available local or published copy, and Go to Gallery "
                "opens your collection of published letters."
            )
        else:
            header, body = "", ""

        self.help_pop.set_header_text(header)
        self.help_pop.set_help_text(body)

    def _reposition_help_popover(self):
        # Place adjacent to the help icon; flip to left if near right edge
        global_center = self.help_icon.mapToGlobal(self.help_icon.rect().center())
        prefer_left = True  # prefer left so it doesnΓÇÖt collide with right edge
        self.help_pop.popup_at(global_center, prefer_left, self.body, icon_px=HELP_ICON_PX)

    def _show_help_from_icon(self):
        idx = self.tabbar.currentIndex()
        if idx == 4:  # Command ΓÇö hide help
            return
        self._refresh_help_text(idx)
        self._reposition_help_popover()

    def _hide_help_popover(self):
        self.help_pop.popdown()

    # =============================================================================================
    # Global event filter for help hover (robust)
    # =============================================================================================
    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Safe object lookups (avoid AttributeError if Qt routes to a different QObject)
        icon = getattr(self, "help_icon", None)
        pop  = getattr(self, "help_pop", None)
        web_view = getattr(self, "html_preview", None)

        if (
            web_view is not None
            and watched is web_view
            and self._forge_fullscreen_active
            and event.type() == QEvent.KeyPress
            and isinstance(event, QtGui.QKeyEvent)
            and event.key() == Qt.Key_Escape
        ):
            self._request_forge_fullscreen_exit()
            return True

        if icon is not None and watched is icon:
            t = event.type()
            if t in (QEvent.Enter, QEvent.HoverEnter):
                self._help_hide_timer.stop()
                self._help_show_timer.start()
                # Swap to hover movie while over the icon
                if self._help_movie_hover:
                    self.help_icon.setMovie(self._help_movie_hover)
            elif t in (QEvent.Leave, QEvent.HoverLeave):
                self._help_show_timer.stop()
                # If we immediately entered the popover, it will cancel this timer
                self._help_hide_timer.start()
                # Swap back to idle movie when leaving icon
                if self._help_movie_idle:
                    self.help_icon.setMovie(self._help_movie_idle)

        elif pop is not None and watched is pop:
            t = event.type()
            if t == QEvent.Enter:
                self._help_hide_timer.stop()
            elif t == QEvent.Leave:
                self._help_hide_timer.start()

        return super().eventFilter(watched, event)
