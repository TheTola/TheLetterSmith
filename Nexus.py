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

import logging
import math
import os, sys, subprocess, json
from pathlib import Path
from typing import Optional

from app_icon import apply_qt_window_icon, canonical_icon_paths
from settings_store import (
    CURTAIN_STYLE_LABELS,
    CURTAIN_TEXT_STYLE_PAIRS,
    DEFAULT_SETTINGS,
    DEFAULT_VISIONARY_URL,
    SettingsStore,
    VALID_CURTAIN_STYLES,
    VISIONARY_URL_KEY,
    normalize_published_page_url,
)
from project_state import (
    ApplicationState,
    ProjectStateController,
)
from project_paths import ProjectPathResolver, application_paths
from project_save import ProjectNotReadyError, ProjectSaveService
from recipient_page import RecipientPage
from curtain_cache import (
    CurtainVariantCache,
    prepare_curtain_variant_cache,
)
from ui_help import set_action_help, set_control_help, set_tab_help
from ui_theme import ThemeService

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

_LOGGER = logging.getLogger(__name__)

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

REL_RETICLE_ICON = "icons/reticle.png"   # optional (title bar icon)
REL_SETTINGS_PNG = "icons/settings.png"  # title-bar idle image
REL_SETTINGS_GIF = "icons/Settings.gif"  # hover/open animation
REL_MINIMIZE_ICON = "icons/mini.png"
REL_MAXIMIZE_ICON = "icons/maxi.png"
REL_CLOSE_ICON = "icons/Exi.png"
REL_HELP_GIF     = "icons/Help.gif"      # idle (plays constantly)
REL_HELP_HOVER   = "icons/HHelp.gif"     # hover variant (plays on hover)
REL_HELP_PNG     = "icons/Help.png"      # final static fallback


def _app_asset(project_root: str | Path, relative_path: str | Path) -> Path:
    return application_paths(project_root).app_resource_path(relative_path)


def _theme_rgba(color: str, alpha: int) -> str:
    value = QColor(color)
    return f"rgba({value.red()},{value.green()},{value.blue()},{alpha})"

WIN_W, WIN_H = 1400, 900
_PREVIEW_AR = 169 / 253  # preview frame aspect (matches your 169├ù253 scaling)

# Help icon display size
HELP_ICON_PX = 125
HELP_ICON_HALF = HELP_ICON_PX // 2
TITLE_BAR_ICON_PX = 36
TITLE_BAR_CONTROL_PX = 40
SETTINGS_ICON_PX = TITLE_BAR_ICON_PX

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
        self._accent_color = self.palette().color(QtGui.QPalette.Highlight)
        self._ring_color = QColor(self._accent_color)
        self._ring_color.setAlpha(70)
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
        ring = QtGui.QPen(self._ring_color, pulse)
        painter.setPen(ring)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, 30, 30)

        painter.setPen(Qt.NoPen)
        for index in range(12):
            distance = (index - self._step) % 12
            color = QColor(self._accent_color)
            color.setAlpha(max(38, 255 - distance * 18))
            painter.setBrush(color)
            angle = math.radians(index * 30 - 90)
            point = QtCore.QPointF(
                center.x() + math.cos(angle) * 31,
                center.y() + math.sin(angle) * 31,
            )
            radius = 4.7 if distance == 0 else 3.4
            painter.drawEllipse(point, radius, radius)

    def apply_theme_assets(self, service: ThemeService) -> None:
        self._accent_color = QColor(service.tokens.accent)
        self._ring_color = QColor(service.tokens.secondary)
        self._ring_color.setAlpha(70)
        self.update()


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

    def apply_theme_assets(self, service: ThemeService) -> None:
        colors = service.tokens
        self.spinner.apply_theme_assets(service)
        self.setStyleSheet(
            "QFrame#ProjectLoadingOverlay{"
            f"background:{_theme_rgba(colors.background, 218)};border:none;}}"
            "QFrame#ProjectLoadingPanel{"
            f"background:{colors.panel_background};"
            f"border:1px solid {colors.primary};border-radius:12px;}}"
            "QLabel#ProjectLoadingTitle{"
            f"color:{colors.highlight};font:600 15pt 'Segoe UI';}}"
            "QLabel#ProjectLoadingDetail{"
            f"color:{colors.muted_text};font:10pt 'Segoe UI';}}"
        )

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


class _CurtainPreparationSignals(QtCore.QObject):
    completed = QtCore.Signal(int, object)
    failed = QtCore.Signal(int, str)


class _CurtainPreparationTask(QtCore.QRunnable):
    def __init__(self, project_root: str | Path, generation: int) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.generation = generation
        self.signals = _CurtainPreparationSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = prepare_curtain_variant_cache(self.project_root)
        except Exception as error:
            self.signals.failed.emit(self.generation, str(error))
            return
        self.signals.completed.emit(self.generation, result)

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
        self.theme_service = getattr(parent, "theme_service", None)
        self._owns_theme_service = self.theme_service is None
        if self.theme_service is None:
            self.theme_service = ThemeService(
                self.parent.project_root,
                parent=self,
            )

        self.setFixedHeight(TITLE_BAR_CONTROL_PX + 8)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # ---------------------------------------------------------------------
        # Window title
        # ---------------------------------------------------------------------

        app_icon = QtWidgets.QLabel(self)
        app_icon.setObjectName("AppIcon")
        app_icon.setFixedSize(TITLE_BAR_CONTROL_PX, TITLE_BAR_CONTROL_PX)
        app_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        png_path, _ = canonical_icon_paths(self.parent.project_root)
        pixmap = QPixmap(str(png_path))
        if not pixmap.isNull():
            app_icon.setPixmap(
                pixmap.scaled(
                    TITLE_BAR_ICON_PX,
                    TITLE_BAR_ICON_PX,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            layout.addWidget(app_icon)

        self.title_label = QtWidgets.QLabel(
            "The Silver-Tongued Lettersmith",
            self,
        )
        layout.addWidget(self.title_label)
        layout.addStretch()

        # ---------------------------------------------------------------------
        # Viewer settings
        # ---------------------------------------------------------------------

        self.settings_button = QtWidgets.QToolButton(self)
        set_control_help(
            self.settings_button,
            "Choose the theme, curtain style, and other application settings.",
            accessible_name="Application settings",
        )
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.setPopupMode(
            QtWidgets.QToolButton.InstantPopup
        )
        self.settings_button.setFixedSize(
            TITLE_BAR_CONTROL_PX,
            TITLE_BAR_CONTROL_PX,
        )
        self.settings_button.setIconSize(
            QSize(SETTINGS_ICON_PX, SETTINGS_ICON_PX)
        )
        self._settings_static_icon = QIcon()
        self._settings_movie_path = ""
        self._settings_movie = QMovie(parent=self)
        self._settings_icon_animated = False
        self._settings_menu_open = False
        self.settings_button.setAttribute(Qt.WA_Hover, True)
        self.settings_button.installEventFilter(self)
        self.settings_button.pressed.connect(
            self._show_animated_settings_icon
        )
        self.settings_menu = QtWidgets.QMenu(self.settings_button)
        self.themes_menu = self.settings_menu.addMenu("Themes")
        set_action_help(
            self.themes_menu.menuAction(),
            "Choose a complete visual theme for Letter Smith.",
        )
        self._theme_group = QtGui.QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QtGui.QAction] = {}
        for definition in self.theme_service.available_themes():
            action = self.themes_menu.addAction(definition.display_name)
            action.setCheckable(True)
            action.setData(definition.theme_id)
            set_action_help(
                action,
                f"Apply the {definition.display_name} theme across Letter Smith.",
            )
            action.triggered.connect(
                lambda _checked=False, theme_id=definition.theme_id: (
                    self._set_theme(theme_id)
                )
            )
            self._theme_group.addAction(action)
            self._theme_actions[definition.theme_id] = action

        self.curtain_menu = self.settings_menu.addMenu("Curtains")
        set_action_help(
            self.curtain_menu.menuAction(),
            "Choose the curtain colors used around the finished letter.",
        )
        self._curtain_actions: dict[str, QtWidgets.QPushButton] = {}
        self._curtain_parent_actions: dict[str, QtWidgets.QToolButton] = {}
        self._curtain_widget_actions: list[QtWidgets.QWidgetAction] = []
        self._curtain_family_styles = {
            "light": ("normal_light", "complementary_light"),
            "dark": ("normal_dark", "complementary_dark"),
        }
        self._current_curtain_style = str(
            DEFAULT_SETTINGS["curtain_style"]
        )
        self._curtain_preview_colors: dict[str, tuple[int, int, int]] = {
            "pure_white": (255, 255, 255),
        }
        self._curtain_group = QtWidgets.QButtonGroup(self)
        self._curtain_group.setExclusive(True)

        def add_style_row(
            menu: QtWidgets.QMenu,
            style: str,
        ) -> None:
            row = QtWidgets.QPushButton(CURTAIN_STYLE_LABELS[style], menu)
            row.setCheckable(True)
            row.setCursor(Qt.PointingHandCursor)
            row.setMinimumWidth(230)
            row.setFixedHeight(34)
            set_control_help(
                row,
                f"Use {CURTAIN_STYLE_LABELS[style]} around the finished letter.",
                accessible_name=CURTAIN_STYLE_LABELS[style],
            )
            row.clicked.connect(
                lambda _checked=False, value=style: self._set_curtain_style(
                    value
                )
            )
            widget_action = QtWidgets.QWidgetAction(menu)
            widget_action.setDefaultWidget(row)
            menu.addAction(widget_action)
            self._curtain_group.addButton(row)
            self._curtain_actions[style] = row
            self._curtain_widget_actions.append(widget_action)

        def add_family_row(
            family: str,
            submenu: QtWidgets.QMenu,
        ) -> None:
            row = QtWidgets.QToolButton(self.curtain_menu)
            row.setText(f"{family.title()} ▾")
            row.setCursor(Qt.PointingHandCursor)
            row.setMinimumWidth(230)
            row.setFixedHeight(34)
            row.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            row.setMenu(submenu)
            set_control_help(
                row,
                f"Open the {family} curtain color options.",
                accessible_name=f"{family.title()} curtain options",
            )
            widget_action = QtWidgets.QWidgetAction(self.curtain_menu)
            widget_action.setDefaultWidget(row)
            self.curtain_menu.addAction(widget_action)
            self._curtain_parent_actions[family] = row
            self._curtain_widget_actions.append(widget_action)

        for style in (
            "pure_white",
            "average_color",
            "complementary_average_color",
        ):
            add_style_row(self.curtain_menu, style)

        self.light_curtain_menu = QtWidgets.QMenu(
            "Light",
            self.curtain_menu,
        )
        self.light_curtain_menu.setStyleSheet(self.settings_menu.styleSheet())
        for style in self._curtain_family_styles["light"]:
            add_style_row(self.light_curtain_menu, style)
        add_family_row("light", self.light_curtain_menu)

        self.dark_curtain_menu = QtWidgets.QMenu(
            "Dark",
            self.curtain_menu,
        )
        self.dark_curtain_menu.setStyleSheet(self.settings_menu.styleSheet())
        for style in self._curtain_family_styles["dark"]:
            add_style_row(self.dark_curtain_menu, style)
        add_family_row("dark", self.dark_curtain_menu)
        self.visionary_location_action = self.settings_menu.addAction(
            "Visionary Location…"
        )
        set_action_help(
            self.visionary_location_action,
            "Set the web address used to open the Visionary assistant.",
        )
        self.visionary_location_action.triggered.connect(
            self._edit_visionary_location
        )
        self.github_account_action = self.settings_menu.addAction(
            "GitHub Account…"
        )
        set_action_help(
            self.github_account_action,
            "Sign in, review the connected GitHub account, or sign out.",
        )
        self.github_account_action.triggered.connect(
            self._show_github_account
        )
        self.settings_menu.addSeparator()
        self.repair_music_action = self.settings_menu.addAction(
            "Repair Music Archive"
        )
        set_action_help(
            self.repair_music_action,
            "Repair stored music records without changing the current playlist.",
        )
        self.repair_music_action.triggered.connect(
            self._repair_music_archive
        )
        self.settings_menu.addSeparator()
        self.save_settings_action = self.settings_menu.addAction(
            "Save Settings"
        )
        set_action_help(
            self.save_settings_action,
            "Save and confirm the settings currently applied to Letter Smith.",
        )
        self.save_settings_action.triggered.connect(self._save_settings)
        self.settings_menu.addSeparator()
        self.new_project_action = self.settings_menu.addAction(
            "New Project"
        )
        set_action_help(
            self.new_project_action,
            "Clear the active workspace and begin a letter for another recipient.",
        )
        self.new_project_action.triggered.connect(
            self.parent.start_new_project
        )
        self.exit_action = self.settings_menu.addAction("Exit")
        set_action_help(
            self.exit_action,
            "Close Letter Smith safely.",
        )
        self.exit_action.triggered.connect(self.parent.close)
        self.settings_menu.aboutToShow.connect(
            self._sync_curtain_menu
        )
        self.settings_menu.aboutToShow.connect(self._sync_theme_menu)
        self.settings_menu.aboutToShow.connect(
            self._settings_menu_shown
        )
        self.settings_menu.aboutToHide.connect(
            self._settings_menu_hidden
        )
        self.settings_button.setMenu(self.settings_menu)
        layout.addWidget(self.settings_button)

        # ---------------------------------------------------------------------
        # Target Browser
        # ---------------------------------------------------------------------

        self.btn_target = self._make_button(
            text=self.TARGET_SYMBOL,
            tooltip="Open the Target Browser to manage recipient projects.",
        )
        self._apply_button_icon(
            self.btn_target,
            "titlebar/target.png",
            REL_RETICLE_ICON,
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
            tooltip="Minimize Letter Smith to the taskbar.",
        )
        self._apply_button_icon(
            self.btn_minimize,
            "titlebar/minimize.png",
            REL_MINIMIZE_ICON,
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
            tooltip="Maximize the Letter Smith window.",
        )
        self._apply_button_icon(
            self.btn_max,
            "titlebar/maximize.png",
            REL_MAXIMIZE_ICON,
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
            tooltip="Close Letter Smith.",
            danger=True,
        )
        self._apply_button_icon(
            self.btn_close,
            "titlebar/close.png",
            REL_CLOSE_ICON,
        )

        self.btn_close.clicked.connect(
            self.parent.close
        )

        layout.addWidget(self.btn_close)

        # Keep the maximize/restore symbol synchronized when Windows changes
        # the window state outside this button.
        self.parent.installEventFilter(self)
        self._sync_max_restore_button()
        self.apply_theme(self.theme_service)

    def _sync_curtain_menu(self) -> None:
        current = str(
            SettingsStore(self.parent.project_root).get(
                "curtain_style",
                DEFAULT_SETTINGS["curtain_style"],
            )
        )
        self._current_curtain_style = current
        for style, action in self._curtain_actions.items():
            action.setChecked(style == current)
        self._refresh_curtain_preview_rows()

    def _sync_theme_menu(self) -> None:
        current = self.theme_service.theme_id
        for theme_id, action in self._theme_actions.items():
            action.setChecked(theme_id == current)

    def _set_theme(self, theme_id: str) -> None:
        try:
            previous_theme_id = self.theme_service.theme_id
            definition = self.theme_service.set_theme(theme_id)
            if (
                definition.theme_id == previous_theme_id
                or self._owns_theme_service
            ):
                apply_current = getattr(self.parent, "_apply_current_theme", None)
                if callable(apply_current):
                    apply_current()
                else:
                    self.apply_theme(self.theme_service)
        except (OSError, RuntimeError, ValueError) as error:
            _LOGGER.exception("Theme selection failed.")
            self.parent.status(f"Theme could not be applied: {error}")
            return
        self._sync_theme_menu()
        self.parent.status(f"{definition.display_name} theme applied.")
        toast = getattr(self.parent, "toast", None)
        if callable(toast):
            toast(f"{definition.display_name} theme applied")

    def _save_settings(self) -> None:
        try:
            self.theme_service.save()
            apply_current = getattr(self.parent, "_apply_current_theme", None)
            if callable(apply_current):
                apply_current()
            else:
                self.apply_theme(self.theme_service)
        except (OSError, RuntimeError, ValueError) as error:
            _LOGGER.exception("Settings could not be saved.")
            self.parent.status(f"Settings could not be saved: {error}")
            return
        self.parent.status("Settings saved and applied.")
        toast = getattr(self.parent, "toast", None)
        if callable(toast):
            toast("Settings saved")

    def apply_theme(self, service: ThemeService) -> None:
        """Refresh title-bar colors, menus, and decorative assets."""
        colors = service.tokens
        self.title_label.setStyleSheet(
            "QLabel{"
            f"color:{colors.accent};background:transparent;"
            "font-family:'Segoe UI Semibold';font-size:16px;"
            "letter-spacing:1px;}"
        )
        self.settings_button.setStyleSheet(
            "QToolButton{"
            f"color:{colors.text};background:transparent;"
            "border:1px solid transparent;border-radius:5px;padding:1px;}"
            "QToolButton:hover,QToolButton::menu-button:hover{"
            f"background:{_theme_rgba(colors.accent, 31)};"
            f"border-color:{colors.border};}}"
            "QToolButton::menu-indicator{image:none;}"
        )
        menu_qss = (
            "QMenu{"
            f"background:{colors.panel_background};color:{colors.text};"
            f"border:1px solid {colors.border};padding:5px;}}"
            "QMenu::item{padding:7px 26px 7px 10px;border-radius:4px;}"
            "QMenu::item:selected{"
            f"background:{colors.hover};color:{colors.highlight};}}"
            "QMenu::indicator:checked{"
            f"background:{colors.primary};border:1px solid {colors.highlight};}}"
        )
        for menu in (
            self.settings_menu,
            self.themes_menu,
            self.curtain_menu,
            self.light_curtain_menu,
            self.dark_curtain_menu,
        ):
            menu.setToolTipsVisible(True)
            menu.setStyleSheet(menu_qss)
        for button in (
            self.btn_target,
            self.btn_minimize,
            self.btn_max,
            self.btn_close,
        ):
            self._style_titlebar_button(button)
        self._reload_settings_assets()
        self._apply_button_icon(
            self.btn_target,
            "titlebar/target.png",
            REL_RETICLE_ICON,
        )
        self._apply_button_icon(
            self.btn_minimize,
            "titlebar/minimize.png",
            REL_MINIMIZE_ICON,
        )
        self._apply_button_icon(
            self.btn_max,
            "titlebar/maximize.png",
            REL_MAXIMIZE_ICON,
        )
        self._apply_button_icon(
            self.btn_close,
            "titlebar/close.png",
            REL_CLOSE_ICON,
        )
        self._sync_theme_menu()
        self._refresh_curtain_preview_rows()

    def _resolve_theme_asset(
        self,
        logical_name: str,
        legacy_relative: str,
    ) -> Path:
        resolver = getattr(self.parent, "resolve_theme_asset", None)
        if callable(resolver):
            return resolver(logical_name, legacy_relative)
        paths = application_paths(self.parent.project_root)
        fallback = paths.app_resource_path(legacy_relative).relative_to(
            paths.resource_root
        )
        return self.theme_service.resolve_asset(
            logical_name,
            fallback=fallback,
        )

    def _reload_settings_assets(self) -> None:
        animated = self._settings_icon_animated or self._settings_menu_open
        previous_movie = self._settings_movie
        previous_movie.stop()
        previous_movie.setFileName("")

        static_path = self._resolve_theme_asset(
            "settings/idle.png",
            REL_SETTINGS_PNG,
        )
        movie_path = self._resolve_theme_asset(
            "settings/hover.gif",
            REL_SETTINGS_GIF,
        )
        self._settings_static_icon = QIcon(str(static_path))
        self._settings_movie_path = str(movie_path)
        self._settings_movie = QMovie(self._settings_movie_path, parent=self)
        if self._settings_movie.isValid():
            self._settings_movie.setCacheMode(QMovie.CacheNone)
            self._settings_movie.setScaledSize(
                QSize(SETTINGS_ICON_PX, SETTINGS_ICON_PX)
            )
            self._settings_movie.frameChanged.connect(
                self._update_settings_movie_frame
            )
            self._settings_movie.setFileName("")
        previous_movie.deleteLater()

        if self._settings_static_icon.isNull():
            self.settings_button.setIcon(QIcon())
            self.settings_button.setText("Settings")
        else:
            self.settings_button.setText("")
            self.settings_button.setIcon(self._settings_static_icon)
        if animated:
            self._show_animated_settings_icon()

    def _update_settings_movie_frame(self, _frame: int) -> None:
        if not self._settings_icon_animated:
            return
        pixmap = self._settings_movie.currentPixmap()
        if not pixmap.isNull():
            self.settings_button.setIcon(QIcon(pixmap))

    def _show_animated_settings_icon(self) -> None:
        if not self._settings_movie.fileName():
            self._settings_movie.setFileName(self._settings_movie_path)
        if not self._settings_movie.isValid():
            return
        self._settings_icon_animated = True
        if self._settings_movie.state() == QMovie.NotRunning:
            self._settings_movie.jumpToFrame(0)
            self._settings_movie.start()

    def _show_static_settings_icon(self) -> None:
        self._settings_icon_animated = False
        self._settings_movie.stop()
        self._settings_movie.setFileName("")
        if not self._settings_static_icon.isNull():
            self.settings_button.setIcon(self._settings_static_icon)

    def _settings_menu_shown(self) -> None:
        self._settings_menu_open = True
        self._show_animated_settings_icon()

    def _settings_menu_hidden(self) -> None:
        self._settings_menu_open = False
        QtCore.QTimer.singleShot(0, self._sync_settings_icon_state)

    def _sync_settings_icon_state(self) -> None:
        if self._settings_menu_open or self.settings_button.underMouse():
            self._show_animated_settings_icon()
        else:
            self._show_static_settings_icon()

    def set_curtain_preview_colors(
        self,
        colors: dict[str, tuple[int, int, int]],
    ) -> None:
        self._curtain_preview_colors = {
            "pure_white": (255, 255, 255),
            **{
                style: tuple(max(0, min(255, int(channel))) for channel in rgb)
                for style, rgb in colors.items()
                if style in VALID_CURTAIN_STYLES and len(rgb) == 3
            },
        }
        self._refresh_curtain_preview_rows()

    def _refresh_curtain_preview_rows(self) -> None:
        for style, row in self._curtain_actions.items():
            self._style_curtain_row(
                row,
                style if row.isChecked() else None,
            )
        for family, row in self._curtain_parent_actions.items():
            selected_style = (
                self._current_curtain_style
                if self._current_curtain_style
                in self._curtain_family_styles[family]
                else None
            )
            self._style_curtain_row(row, selected_style)

    def _style_curtain_row(
        self,
        row: QtWidgets.QAbstractButton,
        selected_style: str | None,
    ) -> None:
        colors = self.theme_service.tokens
        rgb = (
            self._curtain_preview_colors.get(selected_style)
            if selected_style is not None
            else None
        )
        if selected_style is not None and rgb is not None:
            background = "#{:02x}{:02x}{:02x}".format(*rgb)
            if selected_style == "pure_white":
                foreground = "#000000"
            else:
                paired_style = CURTAIN_TEXT_STYLE_PAIRS.get(selected_style)
                paired_rgb = self._curtain_preview_colors.get(paired_style)
                foreground = (
                    "#{:02x}{:02x}{:02x}".format(*paired_rgb)
                    if paired_rgb is not None
                    else colors.text
                )
        else:
            background = colors.hover if selected_style else colors.panel_background
            foreground = colors.highlight if selected_style else colors.text
        border = colors.highlight if selected_style else "transparent"
        row.setStyleSheet(
            f"QPushButton,QToolButton{{background:{background};"
            f"color:{foreground};border:2px solid {border};"
            "border-radius:4px;font:600 10pt 'Segoe UI';"
            "text-align:left;padding:5px 10px;}"
            f"QPushButton:hover,QToolButton:hover{{border-color:{colors.accent};}}"
            "QToolButton::menu-indicator{image:none;}"
        )

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
        self.light_curtain_menu.close()
        self.dark_curtain_menu.close()
        self.curtain_menu.close()
        forge = getattr(self.parent, "forge_tab", None)
        if forge is None:
            return
        forge.schedule_refresh()
        if forge.isVisible():
            forge.ensure_preview_current()

    def _edit_visionary_location(self) -> None:
        settings = SettingsStore(self.parent.project_root)
        current = str(
            settings.get(
                VISIONARY_URL_KEY,
                DEFAULT_VISIONARY_URL,
            )
        )
        entered, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Visionary Location",
            "URL:",
            QtWidgets.QLineEdit.Normal,
            current,
        )
        if not accepted:
            return

        candidate = QUrl.fromUserInput(entered.strip()).toString()
        visionary_url = normalize_published_page_url(candidate)
        if not visionary_url:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Visionary Location",
                "Enter a valid http:// or https:// URL.",
            )
            return

        settings.update_fields(**{VISIONARY_URL_KEY: visionary_url})
        self.parent.status("Visionary location updated.")

    def _show_github_account(self) -> None:
        forge = getattr(self.parent, "forge_tab", None)
        if forge is None:
            self.parent.status("GitHub account settings are not available yet.")
            return
        forge.show_github_account()

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
        danger: bool = False,
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(
            text,
            self,
        )

        button.setFixedSize(
            TITLE_BAR_CONTROL_PX,
            TITLE_BAR_CONTROL_PX,
        )
        button.setIconSize(
            QSize(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX)
        )
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty("dangerControl", danger)
        button.setProperty("fallbackSymbol", text)

        symbol_font = QFont(
            "Segoe UI Symbol",
            15,
        )
        symbol_font.setBold(False)
        button.setFont(symbol_font)

        self._style_titlebar_button(button)

        return button

    def _style_titlebar_button(
        self,
        button: QtWidgets.QPushButton,
    ) -> None:
        colors = self.theme_service.tokens
        hover_color = colors.error if button.property("dangerControl") else colors.accent
        hover_alpha = 77 if button.property("dangerControl") else 38
        button.setStyleSheet(
            "QPushButton{"
            f"color:{colors.muted_text};background:transparent;"
            "border:none;padding:0;margin:0;}"
            "QPushButton:hover{"
            f"color:{colors.highlight};"
            f"background:{_theme_rgba(hover_color, hover_alpha)};}}"
            "QPushButton:pressed{"
            f"background:{_theme_rgba(colors.text, 31)};}}"
        )

    def _apply_button_icon(
        self,
        button: QtWidgets.QPushButton,
        logical_name: str,
        legacy_relative: str,
    ) -> None:
        path = str(self._resolve_theme_asset(logical_name, legacy_relative))
        icon = QIcon(path)
        button.setIcon(QIcon())
        button.setText(str(button.property("fallbackSymbol") or ""))
        if icon.isNull():
            return
        button.setText("")
        button.setIcon(icon)
        button.setIconSize(
            QSize(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX)
        )

    def _sync_max_restore_button(self) -> None:
        if self.parent.isMaximized():
            if self.btn_max.icon().isNull():
                self.btn_max.setText(
                    self.RESTORE_SYMBOL
                )
            self.btn_max.setToolTip(
                "Restore Letter Smith to its previous window size."
            )
            self.btn_max.setAccessibleName(
                "Restore window"
            )
        else:
            if self.btn_max.icon().isNull():
                self.btn_max.setText(
                    self.MAXIMIZE_SYMBOL
                )
            self.btn_max.setToolTip(
                "Maximize the Letter Smith window."
            )
            self.btn_max.setAccessibleName(
                "Maximize window"
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
        if watched is self.settings_button:
            if event.type() in (QEvent.Enter, QEvent.HoverEnter):
                self._show_animated_settings_icon()
            elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
                if not self._settings_menu_open:
                    self._show_static_settings_icon()
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
        self.header.setStyleSheet("font-weight:800;background:transparent;")

        self.body = QLabel("")
        bf = QFont("Segoe UI", 10)
        self.body.setFont(bf)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(self.header)
        lay.addWidget(self.body)

    def apply_theme(self, service: ThemeService) -> None:
        colors = service.tokens
        self.setStyleSheet(
            "QFrame#HelpPopover{"
            f"background:{colors.card_background};"
            f"border:2px solid {colors.primary};border-radius:8px;}}"
            "QFrame#HelpPopover QLabel{"
            f"color:{colors.highlight};}}"
        )

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
        service = getattr(owner.window(), "theme_service", None)
        if service is not None:
            self.apply_theme_assets(service)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._escape_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(Qt.Key_Escape),
            self,
        )
        self._escape_shortcut.setContext(Qt.WindowShortcut)
        self._escape_shortcut.activated.connect(self.exit_requested)

    def apply_theme_assets(self, service: ThemeService) -> None:
        self.setStyleSheet(
            "QWidget#ForgePreviewFullscreenWindow{"
            f"background:{service.tokens.background};}}"
        )

    def attach_preview(self, preview: QtWidgets.QWidget) -> None:
        self._layout.addWidget(preview)
        preview.show()
        self._layout.activate()

    def detach_preview(self, preview: QtWidgets.QWidget) -> None:
        self._layout.removeWidget(preview)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.exit_requested.emit()
        event.ignore()


class Nexus(QtWidgets.QMainWindow):
    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = str(project_root)
        self.theme_service = ThemeService(self.project_root, parent=self)
        self.theme_service.theme_changed.connect(self._on_theme_changed)
        self.setWindowTitle("Letter Smith")
        apply_qt_window_icon(self, self.project_root)
        self._tray_icon: Optional[QtWidgets.QSystemTrayIcon] = None
        self._tray_menu: Optional[QtWidgets.QMenu] = None
        self._command_bar: Optional[QtWidgets.QWidget] = None
        self._setup_system_tray()
        self.project_state = ProjectStateController(self.project_root)
        self.project_paths = ProjectPathResolver(self.project_root)
        self.project_save_service = ProjectSaveService(
            self.project_root,
            self.project_state,
            resolver=self.project_paths,
        )
        initial_project_state = self.project_state.initialize()
        self._project_tabs_initialized = False
        self._curtain_preparation_generation = 0
        self._curtain_preparation_tasks: dict[
            int,
            _CurtainPreparationTask,
        ] = {}
        self._curtain_preparation_timer = QtCore.QTimer(self)
        self._curtain_preparation_timer.setSingleShot(True)
        self._curtain_preparation_timer.setInterval(5000)
        self._curtain_preparation_timer.timeout.connect(
            self._start_curtain_preparation
        )
        self._curtain_preparation_pool = QtCore.QThreadPool(self)
        self._curtain_preparation_pool.setMaxThreadCount(1)
        self._forge_fullscreen_active = False
        self._forge_fullscreen_window: Optional[
            _ForgePreviewFullscreenWindow
        ] = None
        self._shutdown_complete = False
        self._shutdown_in_progress = False
        self.setObjectName("NexusWindow")

        # Frameless + QSS
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.theme_service.apply(
            self,
            additional_qss=self._build_nexus_theme_stylesheet(),
        )

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
        tab_help = (
            ("Images", "Select and prepare the four images for this letter."),
            ("Sound", "Choose one song or arrange a playlist for this letter."),
            ("Message", "Write, import, and format the letter's message."),
            ("Forge", "Review, preview, load, publish, and share the letter."),
            ("Command", "Open the deliberate reset workspace. Click to activate."),
        )
        for name, help_text in tab_help:
            index = self.tabbar.addTab(name)
            set_tab_help(self.tabbar, index, help_text)
        self.tabbar.setDrawBase(False)
        self.tabbar.setMouseTracking(True)
        self.tabbar.setAttribute(Qt.WA_Hover, True)
        self.tabbar.installEventFilter(self)
        self._readiness_hovered_tab = -1
        self._image_tab_readiness_hide_timer = QtCore.QTimer(self)
        self._image_tab_readiness_hide_timer.setSingleShot(True)
        self._image_tab_readiness_hide_timer.setInterval(1000)
        self._image_tab_readiness_hide_timer.timeout.connect(
            self._hide_readiness_after_image_tab_hover
        )
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
        preview_background = self.theme_service.tokens.background
        self.html_preview.setStyleSheet(
            f"background-color:{preview_background};"
        )
        self.html_preview.page().setBackgroundColor(QColor(preview_background))
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
            f"color:{self.theme_service.tokens.highlight};"
            "font:12px 'Segoe UI Semibold';padding:4px 6px;"
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
        self.help_icon.setToolTip(
            "Show instructions for the current Letter Smith workspace."
        )
        self.help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self.help_icon.setMouseTracking(True)
        self.help_icon.setAttribute(Qt.WA_Hover, True)

        # Movies: idle + hover
        self._help_movie_idle: Optional[QMovie] = None
        self._help_movie_hover: Optional[QMovie] = None

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
        self.recipient_page.load_requested.connect(
            self._show_saved_letters_from_recipient_page
        )
        self.recipient_page.stock_requested.connect(
            self._show_stock_letters_from_recipient_page
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

        self._apply_current_theme()
        self.project_state.add_listener(self._on_project_state_transition)
        self._apply_application_state(initial_project_state)

        # Diagnostics after event loop starts
        QtCore.QTimer.singleShot(0, self._post_init_diagnostics)

    def _build_nexus_theme_stylesheet(self) -> str:
        """Build the shell stylesheet exclusively from semantic theme tokens."""
        colors = self.theme_service.tokens
        overlay = _theme_rgba(colors.panel_background, 209)
        return f"""
            /* Background ownership remains deliberately scoped. */
            QMainWindow#NexusWindow,
            QWidget#NexusRoot,
            QWidget#NexusBody {{
                background:{colors.background};
                color:{colors.text};
                font-family:'Segoe UI';
                font-size:11px;
            }}

            QStackedWidget#FeatureStack,
            QStackedWidget#PreviewStack,
            QWidget#PreviewBlank {{
                background:transparent;
                border:none;
            }}

            QWidget#ImagePageSurface,
            QWidget#MessagePageSurface,
            QWidget#ForgePageSurface,
            QWidget#CommandPageSurface {{
                background:{colors.background};
                border:none;
            }}

            QWidget#SoundPageSurface {{
                background:{colors.panel_background};
                border:none;
            }}

            QTabBar#MainTabBar {{
                background:{colors.background};
                color:{colors.text};
                font-family:'Segoe UI';
                font-size:11px;
            }}

            QLabel {{
                color:{colors.highlight};
                font-weight:600;
            }}

            QPushButton {{
                background-color:transparent;
                color:{colors.text};
                border:1px solid {colors.border};
                border-radius:4px;
                padding:6px 12px;
                font:11px 'Segoe UI';
            }}
            QPushButton:hover {{
                background:{colors.hover};
                border-color:{colors.primary};
                color:{colors.highlight};
            }}
            QPushButton:pressed {{
                background:{colors.active};
                border-color:{colors.secondary};
                color:{colors.highlight};
            }}

            QTabBar#MainTabBar::tab {{
                background:transparent;
                border:none;
                padding:8px 14px;
                margin-right:2px;
                color:{colors.muted_text};
            }}
            QTabBar#MainTabBar::tab:selected {{
                color:{colors.highlight};
                border-bottom:2px solid {colors.primary};
            }}
            QTabBar#MainTabBar::tab:hover {{
                color:{colors.highlight};
            }}

            QTabBar#MainTabBar[commandOverlay="true"] {{
                background:transparent;
            }}
            QTabBar#MainTabBar[commandOverlay="true"]::tab {{
                background:transparent;
                border:none;
                color:transparent;
            }}
            QTabBar#MainTabBar[commandOverlay="true"]::tab:selected {{
                border:none;
                color:transparent;
            }}
            QTabBar#MainTabBar[commandOverlay="true"]::tab:hover {{
                background:{overlay};
                color:{colors.highlight};
                border-bottom:2px solid {colors.primary};
            }}

            QStatusBar {{
                background:{colors.background};
                color:{colors.text};
            }}

            QWidget#PreviewFrame {{
                background:{colors.card_background};
                border:2px solid {colors.border};
                border-radius:6px;
            }}

            QToolTip {{
                background:{colors.control_background};
                color:{colors.text};
                border:1px solid {colors.border};
                padding:4px 7px;
            }}
        """

    def resolve_theme_asset(
        self,
        logical_name: str,
        legacy_relative: str,
    ) -> Path:
        """Resolve a themed asset with the active app-resource path as fallback."""
        paths = application_paths(self.project_root)
        fallback_path = paths.app_resource_path(legacy_relative).resolve()
        fallback = fallback_path.relative_to(paths.resource_root)
        return self.theme_service.resolve_asset(
            logical_name,
            fallback=fallback,
        )

    @QtCore.Slot(str, object)
    def _on_theme_changed(self, _theme_id: str, _definition: object) -> None:
        self._apply_current_theme()

    def _apply_current_theme(self) -> None:
        """Reapply colors and decorative assets without rebuilding the UI."""
        self.theme_service.apply(
            self,
            additional_qss=self._build_nexus_theme_stylesheet(),
        )
        title_bar = getattr(self, "title_bar", None)
        if title_bar is not None:
            title_bar.apply_theme(self.theme_service)
        help_pop = getattr(self, "help_pop", None)
        if help_pop is not None:
            help_pop.apply_theme(self.theme_service)
        html_preview = getattr(self, "html_preview", None)
        if html_preview is not None:
            background = self.theme_service.tokens.background
            html_preview.setStyleSheet(f"background-color:{background};")
            html_preview.page().setBackgroundColor(QColor(background))
        preview_caption = getattr(self, "preview_caption", None)
        if preview_caption is not None:
            preview_caption.setStyleSheet(
                f"color:{self.theme_service.tokens.highlight};"
                "font:12px 'Segoe UI Semibold';padding:4px 6px;"
            )
        if hasattr(self, "help_icon"):
            self._reload_help_theme_assets()
        if hasattr(self, "_toast"):
            colors = self.theme_service.tokens
            self._toast.setStyleSheet(
                "QLabel{"
                f"background:{_theme_rgba(colors.panel_background, 230)};"
                f"color:{colors.highlight};border:1px solid {colors.border};"
                "border-radius:6px;padding:8px 12px;}"
            )
        self._apply_theme_assets_to_descendants()

    def _apply_theme_assets_to_descendants(self) -> None:
        for widget in self.findChildren(QtWidgets.QWidget):
            apply_assets = getattr(widget, "apply_theme_assets", None)
            if not callable(apply_assets):
                continue
            try:
                apply_assets(self.theme_service)
            except (OSError, RuntimeError, ValueError):
                _LOGGER.exception(
                    "Theme assets could not be applied to %s.",
                    type(widget).__name__,
                )

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
        self.image_tab.images_changed.connect(
            lambda _reason: self.forge_tab.schedule_refresh()
        )
        self.image_tab.cover_changed.connect(
            self._schedule_curtain_preparation
        )
        self.sound_tab.sound_state_changed.connect(
            lambda _reason: self.forge_tab.schedule_refresh()
        )
        self.sound_tab.volume_changed.connect(
            lambda _volume: self.forge_tab.schedule_refresh()
        )
        self.message_tab.project_changed.connect(
            self.forge_tab.schedule_refresh
        )
        self.image_tab.image_selected.connect(
            lambda pixmap: self._show_image_for_tab(0, pixmap)
        )
        self.image_tab.hover_preview_image.connect(
            lambda pixmap: self._show_image_for_tab(0, pixmap)
        )
        self.image_tab.clear_preview.connect(
            self._on_image_tab_clear_preview
        )
        self.message_tab.preview_image.connect(
            lambda pixmap: self._show_image_for_tab(2, pixmap)
        )
        self.message_tab.wall_preview.connect(
            lambda pixmap: self._show_image_for_tab(2, pixmap)
        )
        self.command_tab.wiped.connect(self._on_command_wiped)

        self._tabswitch = (
            TabSwitcher(
                self.page_stack,
                hover_excluded_indices={4},
            )
            if TabSwitcher is not None
            else None
        )
        self._project_tabs_initialized = True
        self._apply_theme_assets_to_descendants()
        self.tabbar.setCurrentIndex(0)
        self._tab_changed(0)
        self._schedule_curtain_preparation()

    def _schedule_curtain_preparation(self) -> None:
        self._curtain_preparation_generation += 1
        self._curtain_preparation_timer.stop()
        self.title_bar.set_curtain_preview_colors({})
        cover = (
            Path(self.project_root)
            / "gallery"
            / "user"
            / "pages"
            / "cover.png"
        )
        if cover.is_file():
            self._curtain_preparation_timer.start()

    def _stop_curtain_preparation(self, timeout_ms: int | None = None) -> bool:
        self._curtain_preparation_generation += 1
        self._curtain_preparation_timer.stop()
        self._curtain_preparation_pool.clear()
        return bool(
            self._curtain_preparation_pool.waitForDone()
            if timeout_ms is None
            else self._curtain_preparation_pool.waitForDone(
                max(0, int(timeout_ms))
            )
        )

    def _start_curtain_preparation(self) -> None:
        generation = self._curtain_preparation_generation
        task = _CurtainPreparationTask(self.project_root, generation)
        task.signals.completed.connect(self._curtain_preparation_completed)
        task.signals.failed.connect(self._curtain_preparation_failed)
        self._curtain_preparation_tasks[generation] = task
        self._curtain_preparation_pool.start(task)

    @QtCore.Slot(int, object)
    def _curtain_preparation_completed(
        self,
        generation: int,
        result: object,
    ) -> None:
        self._curtain_preparation_tasks.pop(generation, None)
        if generation != self._curtain_preparation_generation:
            return
        if not isinstance(result, CurtainVariantCache):
            return
        self.title_bar.set_curtain_preview_colors(dict(result.colors))

    @QtCore.Slot(int, str)
    def _curtain_preparation_failed(
        self,
        generation: int,
        message: str,
    ) -> None:
        self._curtain_preparation_tasks.pop(generation, None)
        if generation != self._curtain_preparation_generation:
            return
        _LOGGER.warning("Curtain preparation failed: %s", message)

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

    def _show_saved_letters_from_recipient_page(self) -> None:
        self._show_letter_menu_from_recipient_page(stock=False)

    def _show_stock_letters_from_recipient_page(self) -> None:
        self._show_letter_menu_from_recipient_page(stock=True)

    def _show_letter_menu_from_recipient_page(self, *, stock: bool) -> None:
        try:
            self._initialize_project_tabs()
            saved_panel = self.forge_tab.saved_panel
            if saved_panel.parentWidget() is not self:
                saved_panel.setParent(
                    self,
                    Qt.Popup | Qt.FramelessWindowHint,
                )
            saved_panel.setEnabled(True)
            if stock:
                self.forge_tab.show_stock_letters()
            else:
                self.forge_tab.show_saved_letters()
        except (ImportError, RuntimeError, OSError) as error:
            menu_name = "Stock letters" if stock else "Saved letters"
            self.recipient_page.show_error(
                f"{menu_name} could not be opened: {error}"
            )

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
        panel = getattr(self, "_prompt_writer_win", None)
        if isinstance(panel, QtWidgets.QWidget):
            try:
                if not panel.reload_project_state():
                    self.status("Prompt Writer could not refresh the loaded project.")
            except Exception as error:
                _LOGGER.exception("Prompt Writer could not refresh after restore: %s", error)
                self.status("Prompt Writer could not refresh the loaded project.")
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
                colors = self.theme_service.tokens
                view.setHtml(
                    "<html><body style='"
                    f"background:{colors.background};color:{colors.highlight};"
                    "font-family:Segoe UI;'>"
                    "<h3>No preview URL available</h3></body></html>"
                )

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
        autosave_note = ""
        if old_idx != idx:
            if old_idx == 0:
                try:
                    self.image_tab.deactivate_for_tab_change()
                except Exception as ex:
                    self.status(f"Images could not release on exit: {ex}")
            elif old_idx == 2:
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

        if old_idx != idx:
            autosave_note = self._autosave_project_on_tab_switch()

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
        status_message = f"Switched to: {tab_name}"
        if autosave_note:
            status_message = f"{status_message}. {autosave_note}"
        self.status(status_message)
        self._set_forge_preview_visible(idx == 3)
        if idx == 0:
            self.forge_tab.dismiss_readiness()
        else:
            self.forge_tab.set_readiness_context_visible(idx in {1, 2, 3})

        if idx == 0:
            try:
                self.image_tab.activate_for_tab_change()
            except Exception as ex:
                self.status(f"Images could not refresh: {ex}")
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

    def _autosave_project_on_tab_switch(self) -> str:
        eligibility = self.project_save_service.save_eligibility()
        if not eligibility.can_save:
            return f"Project not saved: {eligibility.blocked_reason}"
        try:
            self.project_save_service.save_workspace_snapshot(
                reason="tab-switch",
            )
        except ProjectNotReadyError as error:
            return f"Project not saved: {error}"
        except Exception as error:
            _LOGGER.exception("Project tab-switch autosave failed: %s", error)
            return f"Project autosave failed: {error}"
        return "Project autosaved."

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
    def _show_image_for_tab(self, tab_index: int, pixmap: QPixmap) -> None:
        """Ignore delayed preview work after its source tab has been left."""
        if self.tabbar.currentIndex() != tab_index:
            return
        self._show_image(pixmap)

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
        self._stop_curtain_preparation()
        panel = getattr(self, "_prompt_writer_win", None)
        if isinstance(panel, QtWidgets.QWidget):
            try:
                if not panel.reload_project_state():
                    raise RuntimeError("Prompt Writer rejected the empty state")
                panel.popdown()
            except Exception:
                _LOGGER.exception(
                    "Prompt Writer could not refresh after New Project."
                )
        if self._project_tabs_initialized:
            try:
                self.image_tab.reset_project_images()
            except Exception:
                _LOGGER.exception("Images could not refresh after New Project.")
            try:
                self.sound_tab.reset_project_sound()
            except Exception:
                _LOGGER.exception("Sound could not refresh after New Project.")
            try:
                self.message_tab.reset_project_message()
            except Exception:
                _LOGGER.exception("Message could not refresh after New Project.")
            try:
                self.forge_tab.reset_after_project_wipe()
            except Exception:
                _LOGGER.exception("Forge could not refresh after New Project.")
        try:
            self._release_forge_preview_files()
        except Exception:
            _LOGGER.exception("Forge preview could not be released after New Project.")
        if self._project_tabs_initialized:
            self._set_command_immersive(False)
            tab_blocker = QtCore.QSignalBlocker(self.tabbar)
            self.tabbar.setCurrentIndex(0)
            del tab_blocker
            self.page_stack.setCurrentIndex(0)
            self._detach_sound_preview()
        self._last_pixmap = None
        self._clear_preview()
        try:
            self.preview_stack.setCurrentIndex(0)
            self.preview_frame.setVisible(True)
            self.preview_caption.setVisible(False)
            if self._project_tabs_initialized:
                self.forge_tab.preview_format_panel.setVisible(False)
            self.help_icon.setVisible(True)
        except Exception:
            pass
        self.title_bar._sync_curtain_menu()
        self.title_bar.set_curtain_preview_colors({})

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
        if self.forge_tab.operation_in_progress:
            self._show_forge_preview_pending()
            return
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
        if self.forge_tab.operation_in_progress:
            self._show_forge_preview_pending()
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

    def _show_forge_preview_pending(self) -> None:
        self._last_pixmap = None
        self._clear_preview()
        self.preview_stack.setCurrentIndex(0)
        self.preview_caption.setText("Preparing interactive preview…")
        self.preview_caption.setVisible(True)

    def _load_forge_preview(self, index_path: str, mode: str) -> None:
        if self.tabbar.currentIndex() != 3:
            return
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
        if self.tabbar.currentIndex() == 3:
            self.preview_caption.setText("Preparing interactive preview…")
            self.preview_caption.setVisible(True)
        else:
            self.preview_caption.setVisible(False)
        previous_url = self.html_preview.url()
        self.html_preview.stop()
        blank_url = QUrl("about:blank")
        if not previous_url.isLocalFile():
            self.html_preview.setUrl(blank_url)
            return

        unload_loop = QtCore.QEventLoop()
        unload_timeout = QtCore.QTimer()
        unload_timeout.setSingleShot(True)
        unloaded = False

        def finish_unload(ok: bool) -> None:
            nonlocal unloaded
            if ok and self.html_preview.url() == blank_url:
                unloaded = True
                unload_loop.quit()

        self.html_preview.loadFinished.connect(finish_unload)
        unload_timeout.timeout.connect(unload_loop.quit)
        try:
            self.html_preview.setUrl(blank_url)
            unload_timeout.start(1500)
            unload_loop.exec(QtCore.QEventLoop.ExcludeUserInputEvents)
        finally:
            unload_timeout.stop()
            try:
                self.html_preview.loadFinished.disconnect(finish_unload)
            except (RuntimeError, TypeError):
                pass
        if not unloaded:
            _LOGGER.warning(
                "Timed out waiting for the embedded Forge preview to release %s",
                previous_url.toLocalFile(),
            )

    def _release_project_files_for_restore(self) -> None:
        """Release project-owned media handles before an atomic restore."""
        try:
            self.image_tab.prepare_for_project_restore()
        except Exception:
            _LOGGER.exception("Image files could not be released before restore.")
        try:
            self.sound_tab.prepare_for_project_restore()
        except Exception:
            _LOGGER.exception("Sound files could not be released before restore.")

    def prepare_for_project_reset(self) -> None:
        """Stop project-owned workers and media before New Project commits."""
        if not self._stop_curtain_preparation():
            raise RuntimeError(
                "Curtain preparation did not stop before New Project."
            )
        self._release_forge_preview_files()
        self._release_project_files_for_restore()

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
            request.accept()
            QtCore.QTimer.singleShot(0, self._enter_forge_fullscreen)
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
        self.html_preview.show()
        window.layout().activate()
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

        if current_tab == 2:
            message_tab = getattr(self, "message_tab", None)
            message_height = 340
            if message_tab is not None:
                message_height = max(
                    message_height,
                    message_tab.minimumSizeHint().height(),
                )
            help_height = self.help_icon.height() if self.help_icon.isVisible() else 0
            layout_gaps = max(0, self.body_layout.spacing()) * 2
            available_frame_height = max(
                120,
                body_height - message_height - help_height - layout_gaps,
            )
            h = max(
                108,
                min(
                    int(window_height * 0.35),
                    available_frame_height - 12,
                ),
            )
            w = int(h * _PREVIEW_AR)
            self.preview_frame.setFixedSize(
                max(160, w + 12),
                max(120, h + 12),
            )
            return

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
        _LOGGER.info("Application resource shutdown started.")
        try:
            try:
                if not self._stop_curtain_preparation():
                    _LOGGER.warning(
                        "Curtain preparation did not stop before shutdown."
                    )
            except Exception:
                _LOGGER.exception("Curtain preparation shutdown failed.")
            if self._project_tabs_initialized:
                try:
                    if not self.forge_tab.shutdown_operations():
                        _LOGGER.warning(
                            "Forge operation did not stop before shutdown."
                        )
                except Exception:
                    _LOGGER.exception("Forge operation shutdown failed.")
            self.project_state.remove_listener(
                self._on_project_state_transition
            )
            if not self.flush_prompt_writer_state():
                _LOGGER.warning(
                    "Prompt Writer state could not be flushed during shutdown."
                )
            prompt_writer = getattr(self, "_prompt_writer_win", None)
            prompt_writer_shutdown = getattr(prompt_writer, "shutdown", None)
            if callable(prompt_writer_shutdown):
                try:
                    prompt_writer_shutdown()
                except Exception:
                    _LOGGER.exception(
                        "Prompt Writer shutdown cleanup failed."
                    )
            if self._project_tabs_initialized:
                try:
                    self._release_forge_preview_files()
                except Exception:
                    _LOGGER.exception("Forge preview file release failed.")
                try:
                    self.forge_tab.deactivate_for_tab_change()
                    self.forge_tab.set_readiness_context_visible(False)
                    self.forge_tab.readiness_window.shutdown()
                except Exception:
                    _LOGGER.exception("Forge preview shutdown failed.")
                try:
                    if not self.sound_tab.shutdown():
                        _LOGGER.warning("Sound workers did not stop during shutdown.")
                except Exception:
                    _LOGGER.exception("Sound shutdown failed.")
                try:
                    self.message_tab.shutdown()
                except Exception:
                    _LOGGER.exception("Message shutdown failed.")
                try:
                    self.image_tab.shutdown()
                except Exception:
                    _LOGGER.exception("Image shutdown failed.")
        finally:
            try:
                self.project_state.shutdown()
            except Exception:
                _LOGGER.exception("Project state shutdown failed.")
            if self._tray_icon is not None:
                self._tray_icon.hide()
            self._shutdown_complete = True
            self._shutdown_in_progress = False
            _LOGGER.info("Application resource shutdown completed.")

    def start_new_project(self) -> None:
        """Confirm and clear only the currently active editable project."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is not None and forge_tab.operation_in_progress:
            self.status("Finish the current Forge operation before starting a new project.")
            return
        if self.project_state.is_project_ready:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Start New Project",
                "Discard the current active project and start a new one? "
                "Saved letters, backups, Prompt Writer libraries, palettes, "
                "and application preferences will be kept.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self.status("New project canceled.")
                return
        try:
            from command import start_new_project

            completed = start_new_project(
                self,
                project_root=self.project_root,
                project_state=self.project_state,
            )
        except Exception as error:
            _LOGGER.exception("New Project failed: %s", error)
            QtWidgets.QMessageBox.critical(
                self,
                "New Project",
                f"The active project could not be cleared: {error}",
            )
            return
        if completed:
            self._on_command_wiped()
            self.status("New project ready. Enter a recipient to begin.")

    def open_command_bar_and_close_editor(self, data: object) -> bool:
        """Transfer the completed-letter snapshot to the post-reset controller."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return False
        application = QtWidgets.QApplication.instance()
        previous_quit_policy: bool | None = None
        bar = None
        try:
            from command_bar import CommandBarData, launch_command_bar

            if not isinstance(data, CommandBarData):
                raise TypeError("invalid Command Bar data")
            if application is None:
                raise RuntimeError("QApplication is unavailable")
            screen = self.screen()
            bar = launch_command_bar(
                data,
                self.project_root,
                screen=screen,
                show=False,
            )
            if bar is None:
                self._command_bar = None
                self._release_forge_preview_files()
                if not self.close():
                    raise RuntimeError("Letter Smith could not close after the reset")
                application.quit()
                return True
            self._command_bar = bar
            self._release_forge_preview_files()
            previous_quit_policy = application.quitOnLastWindowClosed()
            application.setQuitOnLastWindowClosed(False)
            if not self.close():
                raise RuntimeError("Letter Smith could not close for the handoff")

            QtCore.QTimer.singleShot(
                0,
                lambda: self._show_command_bar_after_close(
                    bar,
                    application,
                    previous_quit_policy,
                ),
            )
            return True
        except Exception:
            _LOGGER.exception("Could not transition to the Command Bar")
            if bar is not None:
                try:
                    bar.abort_launch()
                except Exception:
                    _LOGGER.exception("Could not dispose the pending Command Bar")
            self._command_bar = None
            if application is not None and previous_quit_policy is not None:
                application.setQuitOnLastWindowClosed(previous_quit_policy)
            return False

    @staticmethod
    def _show_command_bar_after_close(
        bar: object,
        application: QtWidgets.QApplication,
        previous_quit_policy: bool,
    ) -> None:
        try:
            bar.present()
        except Exception:
            _LOGGER.exception("Could not show the Command Bar after closing Nexus")
            application.quit()
        finally:
            application.setQuitOnLastWindowClosed(previous_quit_policy)

    def _setup_system_tray(self) -> None:
        """Install one application tray icon owned by Nexus."""
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            _LOGGER.warning("System tray is unavailable; Letter Smith tray actions are disabled.")
            return

        icon = self.windowIcon()
        if icon.isNull():
            png_path, ico_path = canonical_icon_paths(self.project_root)
            _LOGGER.error(
                "System tray icon could not load the canonical assets: %s; %s",
                png_path,
                ico_path,
            )
            return

        tray = QtWidgets.QSystemTrayIcon(icon, self)
        tray.setToolTip("Letter Smith")
        menu = QtWidgets.QMenu(self)
        show_action = menu.addAction("Show Letter Smith")
        show_action.triggered.connect(self._restore_from_system_tray)
        prompt_action = menu.addAction("Open Prompt Writer")
        prompt_action.triggered.connect(self.open_prompt_writer)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Letter Smith")
        quit_action.triggered.connect(self._quit_from_system_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_system_tray_activated)
        tray.show()
        self._tray_menu = menu
        self._tray_icon = tray

    def _restore_from_system_tray(self) -> None:
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_system_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.Trigger,
            QtWidgets.QSystemTrayIcon.DoubleClick,
        ):
            self._restore_from_system_tray()

    def _quit_from_system_tray(self) -> None:
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        forge_tab = getattr(self, "forge_tab", None)
        if (
            self._project_tabs_initialized
            and forge_tab is not None
            and not forge_tab.shutdown_operations(timeout_ms=5000)
        ):
            self.status("Finish the current Forge operation before closing.")
            event.ignore()
            return
        sound_tab = getattr(self, "sound_tab", None)
        if (
            self._project_tabs_initialized
            and sound_tab is not None
            and not sound_tab.shutdown(timeout_ms=5000)
        ):
            self.status("Finish the current Sound operation before closing.")
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

    def flush_prompt_writer_state(self) -> bool:
        """Persist live Prompt Writer edits before saving the active letter."""
        panel = getattr(self, "_prompt_writer_win", None)
        if not isinstance(panel, QtWidgets.QWidget):
            return True
        try:
            return bool(panel.persist_project_state())
        except Exception as error:
            _LOGGER.exception("Prompt Writer state flush failed: %s", error)
            self.status("Prompt Writer state could not be saved.")
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
            w = PromptWriterPanel(
                self,
                project_root=self.project_root,
            )
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
    def _reload_help_theme_assets(self) -> None:
        self.help_icon.clear()
        for attribute in ("_help_movie_idle", "_help_movie_hover"):
            movie = getattr(self, attribute, None)
            if movie is None:
                continue
            movie.stop()
            movie.setFileName("")
            movie.deleteLater()
            setattr(self, attribute, None)

        def load_movie(path: Path) -> Optional[QMovie]:
            if not path.is_file():
                return None
            movie = QMovie(str(path), parent=self)
            if not movie.isValid():
                movie.deleteLater()
                return None
            movie.setCacheMode(QMovie.CacheAll)
            movie.setSpeed(100)
            movie.setScaledSize(QSize(HELP_ICON_PX, HELP_ICON_PX))
            movie.start()
            return movie

        self._help_movie_idle = load_movie(
            self.resolve_theme_asset("help/idle.gif", REL_HELP_GIF)
        )
        self._help_movie_hover = load_movie(
            self.resolve_theme_asset("help/hover.gif", REL_HELP_HOVER)
        )
        if self._help_movie_idle is not None:
            self.help_icon.setMovie(self._help_movie_idle)
        elif self._help_movie_hover is not None:
            self.help_icon.setMovie(self._help_movie_hover)
        else:
            fallback = self.resolve_theme_asset(
                "help/fallback.png",
                REL_HELP_PNG,
            )
            self._set_help_fallback_icon(str(fallback))

    def _set_help_fallback_icon(self, png_path: str):
        if os.path.exists(png_path):
            pm = QPixmap(png_path)
            if not pm.isNull():
                pm = pm.scaled(HELP_ICON_PX, HELP_ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.help_icon.setPixmap(pm)
                return
        # Final fallback: small text
        self.help_icon.setText("Help")
        self.help_icon.setStyleSheet(
            "QLabel#HelpIcon{background:transparent;border:none;"
            "padding:0;margin:0;"
            f"color:{self.theme_service.tokens.accent};font-weight:800;}}"
        )

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

    def _track_readiness_tab_hover(self, index: int) -> None:
        self._readiness_hovered_tab = index
        if index == 0:
            if not self._image_tab_readiness_hide_timer.isActive():
                self._image_tab_readiness_hide_timer.start()
            return
        self._image_tab_readiness_hide_timer.stop()

    def _hide_readiness_after_image_tab_hover(self) -> None:
        if self._readiness_hovered_tab != 0:
            return
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is not None:
            forge_tab.dismiss_readiness()

    # =============================================================================================
    # Global event filter for help hover (robust)
    # =============================================================================================
    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Safe object lookups (avoid AttributeError if Qt routes to a different QObject)
        icon = getattr(self, "help_icon", None)
        pop  = getattr(self, "help_pop", None)
        web_view = getattr(self, "html_preview", None)

        if watched is getattr(self, "tabbar", None):
            event_type = event.type()
            if event_type in (QEvent.MouseMove, QEvent.HoverMove):
                position = getattr(event, "position", lambda: QtCore.QPointF())()
                self._track_readiness_tab_hover(
                    self.tabbar.tabAt(position.toPoint())
                )
            elif event_type in (QEvent.Leave, QEvent.HoverLeave):
                self._track_readiness_tab_hover(-1)

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
