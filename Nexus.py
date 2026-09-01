# ===============================
# File: Nexus.py
# ===============================
"""
Nexus ΓÇö main shell for Letter Smith
Clean placement ΓÇó Robust overlay ΓÇó Sound visualizer ΓÇó Prompt Writer FAB owned by Image_tab
+ Help animates when available; HHelp prefers a static PNG
+ Per-tab Help popover header: The Image tab / The sound tab / The message tab / The forge tab

Notes
- If Qt WebEngine is missing, we exit with a clear tip: pip install PySide6-Addons
- Animation helpers come from anima.py; we fall back safely if not found
"""

from __future__ import annotations

import logging
import math
import os, json
from pathlib import Path
from time import monotonic
from typing import Optional

from app_icon import apply_qt_window_icon, canonical_icon_paths
from about_dialog import AboutLetterSmithDialog
from settings_store import (
    DEFAULT_SETTINGS,
    DEFAULT_VISIONARY_URL,
    SettingsStore,
    VISIONARY_URL_KEY,
    normalize_published_page_url,
)
from project_state import (
    ApplicationState,
    ProjectDirtyController,
    ProjectStateController,
)
from curtain_color import curtain_variant_rgbs
from curtain_controls import CurtainStyleController, CurtainStyleMenuSelector
from project_paths import ProjectPathResolver, application_paths
from protected_projects import is_protected_project
from project_save import ProjectNotReadyError, ProjectSaveService
from performance_trace import performance_timed
from recipient_page import RecipientPage
from curtain_cache import (
    CurtainVariantCache,
    prepare_curtain_variant_cache,
)
from ui_help import set_action_help, set_control_help, set_tab_help
from ui_dialogs import (
    LetterSmithConfirmationDialog,
    LetterSmithInputDialog,
    show_lettersmith_message,
)
from ui_constants import (
    HELP_HIDE_DELAY_MS,
    HELP_HOVER_DELAY_MS,
    TOAST_DURATION_MS,
    TRANSIENT_STATUS_MS,
)
from ui_theme import (
    HELP_THEME_ASSET_CANDIDATES,
    MAXIMIZE_THEME_ASSET,
    RESTORE_THEME_ASSET_CANDIDATES,
    ThemeService,
)
from ui_fonts import COMMAND_FONT_FAMILY
from window_chrome import FramelessWindowController

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

def _webengine_components():
    """Import WebEngine only when an HTML viewer is actually requested."""
    try:
        from PySide6.QtWebEngineCore import QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as error:
        raise RuntimeError(
            "Qt WebEngine is required for the HTML preview. "
            "Install it with: pip install PySide6-Addons"
        ) from error
    return QWebEngineView, QWebEngineSettings

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

REL_HELP_PNG     = "icons/Help.png"      # final static fallback


def _app_asset(project_root: str | Path, relative_path: str | Path) -> Path:
    return application_paths(project_root).app_resource_path(relative_path)


def _theme_rgba(color: str, alpha: int) -> str:
    value = QColor(color)
    return f"rgba({value.red()},{value.green()},{value.blue()},{alpha})"


def _blend_color(
    first: QColor | str,
    second: QColor | str,
    second_weight: float,
) -> QColor:
    """Blend valid theme colors without introducing a fixed palette."""
    left = QColor(first)
    right = QColor(second)
    weight = max(0.0, min(1.0, float(second_weight)))
    return QColor(
        round(left.red() * (1.0 - weight) + right.red() * weight),
        round(left.green() * (1.0 - weight) + right.green() * weight),
        round(left.blue() * (1.0 - weight) + right.blue() * weight),
    )


def _neutral_color(color: QColor | str) -> QColor:
    value = QColor(color)
    gray = round(
        value.red() * 0.299
        + value.green() * 0.587
        + value.blue() * 0.114
    )
    return QColor(gray, gray, gray)


def _color_rgba(color: QColor | str, alpha: int) -> str:
    value = QColor(color)
    return (
        f"rgba({value.red()},{value.green()},{value.blue()},"
        f"{max(0, min(255, int(alpha)))})"
    )

WIN_W, WIN_H = 1400, 900
_PREVIEW_AR = 169 / 253  # preview frame aspect (matches your 169├ù253 scaling)
SHELL_FONT_PX = 13
SOUND_PREVIEW_MIN_HEIGHT = 220
SOUND_PREVIEW_MAX_HEIGHT = 253

# Help icon display size
HELP_ICON_PX = 125
HELP_ICON_HALF = HELP_ICON_PX // 2
TITLE_BAR_ICON_PX = 36
TITLE_BAR_CONTROL_PX = 40
SETTINGS_ICON_PX = TITLE_BAR_ICON_PX
NEW_PROJECT_ARTWORK_PX = 200
NEW_PROJECT_ARTWORK_GAP = 12
NEW_PROJECT_ARTWORK_RELATIVE = "New/New.png"

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
        self._activity_mode = "restore"
        self._accent_color = self.palette().color(QtGui.QPalette.Highlight)
        self._ring_color = QColor(self._accent_color)
        self._ring_color.setAlpha(70)
        self._glow_color = QColor(self._accent_color)
        self._glow_color.setAlpha(0)
        self.setFixedSize(92, 92)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self._timer.isActive():
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
        if self._glow_color.alpha() > 0:
            glow = QtGui.QPen(self._glow_color, pulse + 5.0)
            painter.setPen(glow)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 33, 33)
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

    def set_activity_mode(self, mode: str) -> None:
        self._activity_mode = str(mode or "restore").strip().casefold()

    def apply_theme_assets(self, service: ThemeService) -> None:
        colors = service.tokens
        if self._activity_mode == "publish":
            self._accent_color = QColor(colors.highlight)
            self._ring_color = QColor(colors.accent)
            self._ring_color.setAlpha(112)
            self._glow_color = QColor(colors.accent)
            self._glow_color.setAlpha(42)
        elif self._activity_mode == "unpublish":
            self._accent_color = _blend_color(
                colors.muted_text,
                colors.warning,
                0.34,
            )
            self._ring_color = _blend_color(
                colors.secondary,
                colors.warning,
                0.22,
            )
            self._ring_color.setAlpha(76)
            self._glow_color = QColor(self._ring_color)
            self._glow_color.setAlpha(14)
        else:
            self._accent_color = QColor(colors.accent)
            self._ring_color = QColor(colors.secondary)
            self._ring_color.setAlpha(70)
            self._glow_color = QColor(self._accent_color)
            self._glow_color.setAlpha(0)
        self.update()


class _ProjectLoadingOverlay(QtWidgets.QFrame):
    """Theme-aware activity shield for restore and publication workflows."""

    dismissed = QtCore.Signal()

    _BLOCKED_KEYS = {
        QEvent.KeyPress,
        QEvent.KeyRelease,
        QEvent.Shortcut,
        QEvent.ShortcutOverride,
    }
    _BLOCKED_APPLICATION_INPUT = _BLOCKED_KEYS | {
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.MouseMove,
        QEvent.Wheel,
        QEvent.ContextMenu,
        QEvent.DragEnter,
        QEvent.DragMove,
        QEvent.Drop,
        QEvent.TouchBegin,
        QEvent.TouchUpdate,
        QEvent.TouchEnd,
        QEvent.TabletPress,
        QEvent.TabletMove,
        QEvent.TabletRelease,
        QEvent.NativeGesture,
    }
    _PUBLICATION_MODES = {"publish", "unpublish"}

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectLoadingOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._activity_mode = "restore"
        self._theme_service: ThemeService | None = None
        self._filter_installed = False
        self._stopping = False
        self._overlay_color = QColor()

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_animation = QtCore.QPropertyAnimation(
            self._opacity_effect,
            b"opacity",
            self,
        )
        self._fade_animation.setDuration(180)
        self._fade_animation.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._fade_animation.finished.connect(self._finish_stop)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        self.panel = QtWidgets.QFrame(self)
        self.panel.setObjectName("ProjectLoadingPanel")
        self.panel.setMinimumWidth(420)
        self.panel.setMaximumWidth(460)
        self.panel.setMinimumHeight(250)
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(34, 26, 34, 28)
        panel_layout.setSpacing(10)
        self._panel_layout = panel_layout

        self.spinner = _LoadingSpinner(self.panel)
        panel_layout.addWidget(self.spinner, 0, Qt.AlignHCenter)
        self.title = QtWidgets.QLabel("Loading saved letter…", self.panel)
        self.title.setObjectName("ProjectLoadingTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        panel_layout.addWidget(self.title)
        self.detail = QtWidgets.QLabel(
            "Restoring the recipient, images, message, and sound safely.",
            self.panel,
        )
        self.detail.setObjectName("ProjectLoadingDetail")
        self.detail.setAlignment(Qt.AlignCenter)
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        panel_layout.addWidget(self.detail)

        for child in (self.panel, self.title, self.detail):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        root.addWidget(self.panel, 0, Qt.AlignHCenter)
        root.addStretch(1)
        self.hide()

    def apply_theme_assets(self, service: ThemeService) -> None:
        self._theme_service = service
        colors = service.tokens
        self.spinner.set_activity_mode(self._activity_mode)
        self.spinner.apply_theme_assets(service)
        app_font = service.app_font_family
        if self._activity_mode == "publish":
            panel_neutral = _neutral_color(colors.panel_background)
            bright_level = max(
                panel_neutral.red(),
                _neutral_color(colors.highlight).red(),
                _neutral_color(colors.text).red(),
            )
            bright_neutral = QColor(
                bright_level,
                bright_level,
                bright_level,
            )
            overlay_color = _blend_color(
                panel_neutral,
                bright_neutral,
                0.72,
            ).lighter(112)
            panel_color = _blend_color(
                colors.panel_background,
                colors.highlight,
                0.08,
            )
            border_color = colors.accent
            title_color = colors.highlight
            detail_color = colors.text
            overlay_alpha = 205
        elif self._activity_mode == "unpublish":
            dark_level = min(
                _neutral_color(colors.background).red(),
                _neutral_color(colors.panel_background).red(),
                _neutral_color(colors.muted_text).red(),
            )
            dark_neutral = QColor(
                dark_level,
                dark_level,
                dark_level,
            ).darker(138)
            sickly_tint = _blend_color(
                _neutral_color(colors.warning),
                colors.warning,
                0.18,
            )
            overlay_color = _blend_color(
                dark_neutral,
                sickly_tint,
                0.16,
            ).darker(108)
            panel_neutral = _neutral_color(colors.panel_background)
            bright_level = max(
                panel_neutral.red(),
                _neutral_color(colors.highlight).red(),
                _neutral_color(colors.text).red(),
            )
            publish_reference = _blend_color(
                panel_neutral,
                QColor(bright_level, bright_level, bright_level),
                0.72,
            ).lighter(112)
            overlay_color.setHsl(
                overlay_color.hslHue(),
                overlay_color.hslSaturation(),
                min(
                    overlay_color.lightness(),
                    max(0, publish_reference.lightness() - 28),
                ),
            )
            panel_color = _blend_color(
                colors.panel_background,
                overlay_color,
                0.28,
            )
            border_color = _blend_color(
                colors.muted_text,
                colors.warning,
                0.32,
            ).name()
            title_color = _blend_color(
                colors.text,
                colors.warning,
                0.22,
            ).name()
            detail_color = colors.muted_text
            overlay_alpha = 225
        else:
            overlay_color = QColor(colors.background)
            panel_color = QColor(colors.panel_background)
            border_color = colors.primary
            title_color = colors.highlight
            detail_color = colors.muted_text
            overlay_alpha = 218
        self._overlay_color = QColor(overlay_color)
        self.setStyleSheet(
            "QFrame#ProjectLoadingOverlay{"
            f"background:{_color_rgba(overlay_color, overlay_alpha)};"
            "border:none;}"
            "QFrame#ProjectLoadingPanel{"
            f"background:{QColor(panel_color).name()};"
            f"border:1px solid {border_color};border-radius:12px;}}"
            "QLabel#ProjectLoadingTitle{"
            f"color:{title_color};font:600 15pt '{app_font}';}}"
            "QLabel#ProjectLoadingDetail{"
            f"color:{detail_color};font:10pt '{app_font}';}}"
        )
        self._update_panel_minimum_height()

    @property
    def activity_mode(self) -> str:
        return self._activity_mode

    def start(
        self,
        message: str,
        *,
        mode: str = "restore",
        detail: str = "",
    ) -> None:
        requested_mode = str(mode or "restore").strip().casefold()
        if requested_mode not in {"restore", *self._PUBLICATION_MODES}:
            requested_mode = "restore"
        self._activity_mode = requested_mode
        self.setProperty("activityMode", requested_mode)
        self.spinner.set_activity_mode(requested_mode)
        if self._theme_service is not None:
            self.apply_theme_assets(self._theme_service)
        activity = str(message or "Loading saved letter…").strip()
        self.title.setText(activity)
        descriptions = {
            "restore": "Restoring the recipient, images, message, and sound safely.",
            "publish": "Preparing and sending your letter safely. Please keep Letter Smith open.",
            "unpublish": "Removing the online copy while preserving your local letter.",
        }
        self.detail.setText(
            str(detail or descriptions[requested_mode]).strip()
        )
        self._update_panel_minimum_height()
        self._stopping = False
        if self._fade_animation.state() == QtCore.QAbstractAnimation.Running:
            self._fade_animation.stop()
        self._opacity_effect.setOpacity(1.0)
        application = QtWidgets.QApplication.instance()
        if application is not None and not self._filter_installed:
            application.installEventFilter(self)
            self._filter_installed = True
        if requested_mode in self._PUBLICATION_MODES:
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.OtherFocusReason)
        self.spinner.start()

    def _update_panel_minimum_height(self) -> None:
        self.panel.ensurePolished()
        margins = self._panel_layout.contentsMargins()
        content_width = max(
            1,
            self.panel.minimumWidth() - margins.left() - margins.right(),
        )
        title_height = max(
            self.title.sizeHint().height(),
            self.title.heightForWidth(content_width),
        )
        detail_height = max(
            self.detail.sizeHint().height(),
            self.detail.heightForWidth(content_width),
        )
        required_height = (
            margins.top()
            + margins.bottom()
            + self.spinner.height()
            + title_height
            + detail_height
            + (self._panel_layout.spacing() * 2)
        )
        self.panel.setMinimumHeight(max(250, required_height))
        self._panel_layout.invalidate()
        self.panel.updateGeometry()

    def stop(self, *, animated: bool = False) -> None:
        if not self.isVisible() and not self._filter_installed:
            self.spinner.stop()
            return
        if animated:
            if self._stopping:
                return
            self._stopping = True
            self._fade_animation.stop()
            self._fade_animation.setStartValue(self._opacity_effect.opacity())
            self._fade_animation.setEndValue(0.0)
            self._fade_animation.start()
            return
        self._finish_stop()

    @QtCore.Slot()
    def _finish_stop(self) -> None:
        if self._fade_animation.state() == QtCore.QAbstractAnimation.Running:
            self._fade_animation.stop()
        self._stopping = False
        self.spinner.stop()
        application = QtWidgets.QApplication.instance()
        if application is not None and self._filter_installed:
            application.removeEventFilter(self)
        self._filter_installed = False
        self.hide()
        self._opacity_effect.setOpacity(1.0)
        self.dismissed.emit()

    @staticmethod
    def _is_modal_input_target(watched: QtCore.QObject) -> bool:
        application = QtWidgets.QApplication.instance()
        modal = application.activeModalWidget() if application is not None else None
        if modal is None or not isinstance(watched, QtWidgets.QWidget):
            return False
        widget: QtWidgets.QWidget | None = watched
        while widget is not None:
            if widget is modal:
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not self.isVisible():
            return super().eventFilter(watched, event)
        blocked = (
            self._BLOCKED_APPLICATION_INPUT
            if self._activity_mode in self._PUBLICATION_MODES
            else self._BLOCKED_KEYS
        )
        if event.type() in blocked:
            if self._is_modal_input_target(watched):
                return super().eventFilter(watched, event)
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


class _ProjectAutosaveSignals(QtCore.QObject):
    completed = QtCore.Signal(int, str)
    failed = QtCore.Signal(int, str)


class _ProjectAutosaveTask(QtCore.QRunnable):
    def __init__(
        self,
        service: ProjectSaveService,
        revision: int,
        reason: str,
    ) -> None:
        super().__init__()
        self.service = service
        self.revision = int(revision)
        self.reason = str(reason)
        self.signals = _ProjectAutosaveSignals()
        self.destination = ""
        self.error = ""

    @QtCore.Slot()
    def run(self) -> None:
        try:
            destination = self.service.save_workspace_snapshot(
                reason=self.reason,
                defer_play_metadata=True,
            )
        except Exception as error:
            self.error = str(error)
            self.signals.failed.emit(self.revision, self.error)
            return
        self.destination = str(destination)
        self.signals.completed.emit(self.revision, self.destination)

class TitleBar(QtWidgets.QWidget):
    """
    Custom frameless title bar.

    Unicode symbols are written as escape codes rather than literal characters.
    This prevents UTF-8/CP437 encoding corruption.
    """

    MINIMIZE_SYMBOL = "\u2013"    # –
    MAXIMIZE_SYMBOL = "\u25A1"    # □
    RESTORE_SYMBOL = "\u2750"     # ❐
    CLOSE_SYMBOL = "\u2715"       # ✕

    def __init__(self, parent=None):
        super().__init__(parent)

        self.parent = parent
        self.setObjectName("NexusTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self._normal_window_geometry: Optional[QtCore.QRect] = None
        self._window_state_generation = 0
        self._command_mode = False
        self._command_hovered = False
        self._title_label_theme_qss = ""
        self.theme_service = getattr(parent, "theme_service", None)
        self._owns_theme_service = self.theme_service is None
        if self.theme_service is None:
            self.theme_service = ThemeService(
                self.parent.project_root,
                parent=self,
            )
        self.curtain_styles = getattr(parent, "curtain_styles", None)
        self._owns_curtain_styles = self.curtain_styles is None
        if self.curtain_styles is None:
            self.curtain_styles = CurtainStyleController(
                SettingsStore(self.parent.project_root),
                self,
            )
            self.destroyed.connect(self.curtain_styles.close)

        self.setFixedHeight(TITLE_BAR_CONTROL_PX + 8)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # ---------------------------------------------------------------------
        # Window title
        # ---------------------------------------------------------------------

        self.app_icon = QtWidgets.QLabel(self)
        self.app_icon.setObjectName("AppIcon")
        self.app_icon.setFixedSize(TITLE_BAR_CONTROL_PX, TITLE_BAR_CONTROL_PX)
        self.app_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        png_path, _ = canonical_icon_paths(self.parent.project_root)
        pixmap = QPixmap(str(png_path))
        self._app_icon_available = not pixmap.isNull()
        if not pixmap.isNull():
            self.app_icon.setPixmap(
                pixmap.scaled(
                    TITLE_BAR_ICON_PX,
                    TITLE_BAR_ICON_PX,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            layout.addWidget(self.app_icon)

        self.title_label = QtWidgets.QLabel(
            "The Silver-Tongued Lettersmith",
            self,
        )
        self._project_title = ""
        self._project_dirty = False
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
        self.themes_menu = self.settings_menu.addMenu("App Theme")
        set_action_help(
            self.themes_menu.menuAction(),
            "Choose a complete visual theme for Letter Smith.",
        )
        self._theme_group = QtGui.QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QtGui.QAction] = {}
        basic_section_started = False
        for definition in self.theme_service.available_themes():
            if not definition.uses_image_buttons and not basic_section_started:
                self.themes_menu.addSeparator()
                basic_section_started = True
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

        self.curtain_style_selector = CurtainStyleMenuSelector(
            "Curtains",
            self.settings_menu,
            object_name="SettingsCurtainStyleSelector",
        )
        self.curtain_menu = self.curtain_style_selector.menu()
        self.settings_menu.addMenu(self.curtain_menu)
        set_action_help(
            self.curtain_menu.menuAction(),
            "Choose the curtain colors used around the finished letter.",
        )
        self.curtain_styles.bind(self.curtain_style_selector)
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
        self.delete_project_action = self.settings_menu.addAction(
            "Delete Project"
        )
        set_action_help(
            self.delete_project_action,
            "Permanently remove the active draft, saved letter, and recovery copies.",
        )
        self.delete_project_action.triggered.connect(
            self.parent.delete_project
        )
        self.settings_menu.addSeparator()
        self.about_action = self.settings_menu.addAction(
            "About Letter Smith…"
        )
        set_action_help(
            self.about_action,
            "View Letter Smith application information and prepare support details.",
        )
        self.about_action.triggered.connect(self._show_about)
        self.settings_menu.addSeparator()
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
        self.settings_menu.aboutToShow.connect(self._sync_project_actions)
        self.settings_menu.aboutToShow.connect(
            self._settings_menu_shown
        )
        self.settings_menu.aboutToHide.connect(
            self._settings_menu_hidden
        )
        self.settings_button.setMenu(self.settings_menu)
        layout.addWidget(self.settings_button)

        half_button_gap = TITLE_BAR_CONTROL_PX // 2
        divider_inner_gap = max(0, half_button_gap - layout.spacing())
        self.settings_window_divider_container = QtWidgets.QWidget(self)
        self.settings_window_divider_container.setFixedSize(
            (divider_inner_gap * 2) + 1,
            TITLE_BAR_CONTROL_PX,
        )
        divider_layout = QHBoxLayout(self.settings_window_divider_container)
        divider_layout.setContentsMargins(
            divider_inner_gap,
            0,
            divider_inner_gap,
            0,
        )
        divider_layout.setSpacing(0)
        self.settings_window_divider = QFrame(
            self.settings_window_divider_container
        )
        self.settings_window_divider.setObjectName("SettingsWindowDivider")
        self.settings_window_divider.setFixedSize(
            1,
            (TITLE_BAR_CONTROL_PX * 3) // 5,
        )
        divider_layout.addWidget(
            self.settings_window_divider,
            0,
            Qt.AlignVCenter,
        )
        layout.addWidget(self.settings_window_divider_container)

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

    def set_project_dirty(self, dirty: bool) -> None:
        self._project_dirty = bool(dirty)
        self._render_title()

    def set_project_title(self, title: str) -> None:
        self._project_title = str(title or "").strip()
        self._render_title()

    def _render_title(self) -> None:
        title = "The Silver-Tongued Lettersmith"
        if self._project_title:
            title = f"{title} — {self._project_title}"
        if self._project_dirty:
            title = f"{title} •"
        self.title_label.setText(title)
        self.title_label.setToolTip(
            "Unsaved project changes" if self._project_dirty else "Letter Smith"
        )

    def _sync_curtain_menu(self) -> None:
        self.curtain_styles.sync_from_settings()

    def _sync_theme_menu(self) -> None:
        current = self.theme_service.theme_id
        for theme_id, action in self._theme_actions.items():
            action.setChecked(theme_id == current)

    def _sync_project_actions(self) -> None:
        settings = SettingsStore(self.parent.project_root).snapshot()
        self.delete_project_action.setEnabled(
            bool(str(settings.get("project_id", "")).strip())
            and not is_protected_project(settings)
        )

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
        app_font = service.app_font_family
        self._title_label_theme_qss = (
            "QLabel{"
            f"color:{colors.accent};background:transparent;"
            f"font-family:'{app_font}';font-size:16px;"
            "letter-spacing:1px;}"
        )
        self.title_label.setStyleSheet(self._title_label_theme_qss)
        self.settings_window_divider.setStyleSheet(
            f"background:{colors.border};border:none;"
        )
        if service.current.uses_image_buttons:
            settings_qss = (
                "QToolButton{"
                f"color:{colors.text};background:transparent;"
                "border:1px solid transparent;border-radius:5px;padding:1px;}"
                "QToolButton:hover,QToolButton::menu-button:hover{"
                f"background:{_theme_rgba(colors.accent, 31)};"
                f"border-color:{colors.border};}}"
                "QToolButton::menu-indicator{image:none;}"
            )
        else:
            settings_qss = (
                "QToolButton{"
                f"color:{colors.text};background:{colors.control_background};"
                f"border:1px solid {colors.border};border-radius:5px;padding:1px;}}"
                "QToolButton:hover,QToolButton::menu-button:hover{"
                f"color:{colors.highlight};background:{colors.hover};"
                f"border-color:{colors.primary};}}"
                "QToolButton:pressed{"
                f"background:{colors.active};border-color:{colors.secondary};}}"
                "QToolButton:disabled{"
                f"color:{colors.muted_text};background:{colors.card_background};}}"
                "QToolButton::menu-indicator{image:none;}"
            )
        self.settings_button.setStyleSheet(settings_qss)
        menu_qss = (
            "QMenu{"
            f"background:{colors.panel_background};color:{colors.text};"
            f"border:1px solid {colors.border};padding:5px;"
            f"font-family:'{app_font}';}}"
            "QMenu::item{padding:7px 26px 7px 10px;border-radius:4px;}"
            "QMenu::item:selected{"
            f"background:{colors.hover};color:{colors.highlight};}}"
            "QMenu::indicator:checked{"
            f"background:{colors.primary};border:1px solid {colors.highlight};}}"
            "QMenu::separator{height:1px;"
            f"background:{colors.border};margin:5px 8px;}}"
        )
        for menu in (
            self.settings_menu,
            self.themes_menu,
            self.curtain_menu,
        ):
            menu.setToolTipsVisible(True)
            menu.setStyleSheet(menu_qss)
        self.curtain_style_selector.apply_theme_tokens(colors)
        for button in (
            self.btn_minimize,
            self.btn_max,
            self.btn_close,
        ):
            self._style_titlebar_button(button)
        self._reload_settings_assets()
        if service.current.uses_image_buttons:
            self._apply_button_icon(
                self.btn_minimize,
                "titlebar/minimize.png",
            )
            self._apply_button_icon(
                self.btn_max,
                "titlebar/maximize.png",
            )
            self._apply_button_icon(
                self.btn_close,
                "titlebar/close.png",
            )
        else:
            for button in (
                self.btn_minimize,
                self.btn_max,
                self.btn_close,
            ):
                button.setIcon(QIcon())
                button.setText(str(button.property("fallbackSymbol") or ""))
        self._sync_max_restore_button()
        self._sync_theme_menu()
        self._apply_command_presentation()

    def _resolve_theme_asset(
        self,
        logical_name: str,
    ) -> Path:
        resolver = getattr(self.parent, "resolve_theme_asset", None)
        if callable(resolver):
            return resolver(logical_name)
        return self.theme_service.resolve_asset(logical_name)

    def _reload_settings_assets(self) -> None:
        animated = self._settings_icon_animated or self._settings_menu_open
        previous_movie = self._settings_movie
        previous_movie.stop()
        previous_movie.setFileName("")

        if not self.theme_service.current.uses_image_buttons:
            self._settings_static_icon = QIcon()
            self._settings_movie_path = ""
            self._settings_movie = QMovie(parent=self)
            self._settings_icon_animated = False
            self.settings_button.setIcon(QIcon())
            self.settings_button.setText("\u2699")
            gear_font = QFont("Segoe UI Symbol", 17)
            gear_font.setBold(False)
            self.settings_button.setFont(gear_font)
            previous_movie.deleteLater()
            return

        static_path = self._resolve_theme_asset(
            "settings/idle.png",
        )
        movie_path = self._resolve_theme_asset(
            "settings/hover.gif",
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
        if not self.theme_service.current.uses_image_buttons:
            return
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
        if self._command_mode and not self.underMouse():
            self._command_hovered = False
            self._apply_command_presentation()

    def set_command_mode(self, active: bool) -> None:
        """Make the title bar black and reveal its contents only on hover."""
        self._command_mode = bool(active)
        self._command_hovered = self._command_mode and self.underMouse()
        self._apply_command_presentation()

    def _apply_command_presentation(self) -> None:
        reveal = (
            not self._command_mode
            or self._command_hovered
            or self._settings_menu_open
        )
        self.setStyleSheet(
            "QWidget#NexusTitleBar{background:#000000;color:#000000;}"
            if self._command_mode
            else ""
        )
        if self._command_mode and not reveal:
            self.title_label.setStyleSheet(
                f"{self._title_label_theme_qss}QLabel{{color:#000000;}}"
            )
        else:
            self.title_label.setStyleSheet(self._title_label_theme_qss)

        self.app_icon.setVisible(self._app_icon_available and reveal)
        for widget in (
            self.settings_button,
            self.settings_window_divider_container,
            self.btn_minimize,
            self.btn_max,
            self.btn_close,
        ):
            widget.setVisible(reveal)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        if self._command_mode:
            self._command_hovered = True
            self._apply_command_presentation()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._command_mode and not self._settings_menu_open:
            self._command_hovered = False
            self._apply_command_presentation()
        super().leaveEvent(event)

    def set_curtain_preview_colors(
        self,
        colors: dict[str, tuple[int, int, int]],
    ) -> None:
        self.curtain_styles.set_preview_colors(colors)

    def _set_curtain_style(self, style: str) -> None:
        self.curtain_styles.set_style(style)

    def _edit_visionary_location(self) -> None:
        settings = SettingsStore(self.parent.project_root)
        current = str(
            settings.get(
                VISIONARY_URL_KEY,
                DEFAULT_VISIONARY_URL,
            )
        )
        entered, accepted = LetterSmithInputDialog.get_text(
            self,
            "Visionary Location",
            "URL:",
            text=current,
            accept_text="Save",
        )
        if not accepted:
            return

        candidate = QUrl.fromUserInput(entered.strip()).toString()
        visionary_url = normalize_published_page_url(candidate)
        if not visionary_url:
            show_lettersmith_message(
                self,
                "Invalid Visionary Location",
                "Enter a valid http:// or https:// URL.",
            )
            return

        settings.update_fields(**{VISIONARY_URL_KEY: visionary_url})
        self.parent.status("Visionary location updated.")

    def _show_about(self) -> None:
        AboutLetterSmithDialog(
            self.parent,
            application=self.parent,
        ).exec()

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
        if not self.theme_service.current.uses_image_buttons:
            button.setStyleSheet(
                "QPushButton{"
                f"color:{colors.text};background:{colors.control_background};"
                f"border:1px solid {colors.border};border-radius:5px;padding:0;}}"
                "QPushButton:hover{"
                f"color:{colors.highlight};background:{colors.hover};"
                f"border-color:{hover_color};}}"
                "QPushButton:pressed{"
                f"background:{colors.active};border-color:{colors.secondary};}}"
                "QPushButton:disabled{"
                f"color:{colors.muted_text};background:{colors.card_background};}}"
            )
            return
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
    ) -> None:
        self._apply_first_button_icon(button, (logical_name,))

    def _apply_first_button_icon(
        self,
        button: QtWidgets.QPushButton,
        logical_names: tuple[str, ...],
    ) -> None:
        resolver = getattr(self.theme_service, "resolve_first_asset")
        path = str(resolver(logical_names))
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
        maximized = self.parent.isMaximized()
        if self.theme_service.current.uses_image_buttons:
            candidates = (
                RESTORE_THEME_ASSET_CANDIDATES
                if maximized
                else (MAXIMIZE_THEME_ASSET,)
            )
            self._apply_first_button_icon(self.btn_max, candidates)

        if maximized:
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
        self._window_state_generation += 1
        generation = self._window_state_generation
        if self.parent.isMaximized():
            target = self._normal_window_geometry
            if target is None or not target.isValid():
                normal = self.parent.normalGeometry()
                target = QtCore.QRect(normal) if normal.isValid() else None
            self.parent.setWindowState(
                self.parent.windowState() & ~Qt.WindowMaximized
            )
            self.parent.showNormal()
            if target is not None:
                QtCore.QTimer.singleShot(
                    0,
                    lambda: self._finish_restore_geometry(
                        generation,
                        QtCore.QRect(target),
                    ),
                )
        else:
            current = self.parent.geometry()
            if current.isValid() and not self.parent.isFullScreen():
                self._normal_window_geometry = QtCore.QRect(current)
            self.parent.showMaximized()

        QtCore.QTimer.singleShot(
            0,
            self._sync_max_restore_button,
        )

    def _finish_restore_geometry(
        self,
        generation: int,
        target: QtCore.QRect,
    ) -> None:
        if generation != self._window_state_generation or not target.isValid():
            return
        self.parent.setWindowState(
            self.parent.windowState() & ~Qt.WindowMaximized
        )
        self.parent.showNormal()
        self.parent.setGeometry(target)
        self._normal_window_geometry = QtCore.QRect(target)
        self._sync_max_restore_button()

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
            controller = getattr(self.parent, "_window_controller", None)
            if controller is not None and controller.start_system_move():
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
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
        header_font = QFont(service.app_font_family, 13)
        header_font.setBold(True)
        self.header.setFont(header_font)
        self.body.setFont(QFont(service.app_font_family, 10))
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
        self._allow_close = False
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
        if self._allow_close:
            event.accept()
            return
        self.exit_requested.emit()
        event.ignore()

    def shutdown(self) -> None:
        self._allow_close = True
        self._escape_shortcut.setEnabled(False)
        self.hide()
        self.close()
        self.deleteLater()


class _ThemedNewProjectButton(QtWidgets.QToolButton):
    """Stock/example exit control backed by per-theme New Project artwork."""

    def __init__(self, project_root: str | Path, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root).resolve()
        self._artwork_path = Path()
        self.setObjectName("ProtectedNewProjectButton")
        self.setProperty("themeRole", "button")
        self.setAccessibleName("New Project")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(NEW_PROJECT_ARTWORK_PX, NEW_PROJECT_ARTWORK_PX)
        self.setIconSize(self.size())

    @property
    def artwork_path(self) -> Path:
        return self._artwork_path

    def apply_theme_assets(self, service: ThemeService) -> None:
        fallback = _app_asset(
            self._project_root,
            f"themes/cyber_forge/{NEW_PROJECT_ARTWORK_RELATIVE}",
        )
        try:
            path = service.resolve_asset(
                NEW_PROJECT_ARTWORK_RELATIVE,
            )
        except (TypeError, ValueError):
            path = fallback

        self._artwork_path = Path(path).resolve()
        icon = QIcon(str(self._artwork_path))
        if not icon.isNull():
            self.setText("")
            self.setIcon(icon)
            self.setToolButtonStyle(Qt.ToolButtonIconOnly)
            self.setStyleSheet(
                "QToolButton#ProtectedNewProjectButton{"
                "background:transparent;border:none;padding:0;}"
            )
            return

        colors = service.tokens
        self.setIcon(QIcon())
        self.setText("New Project")
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setStyleSheet(
            "QToolButton#ProtectedNewProjectButton{"
            f"background:{colors.control_background};color:{colors.text};"
            f"border:1px solid {colors.border};border-radius:12px;"
            f"font:700 18px '{service.app_font_family}';}}"
            "QToolButton#ProtectedNewProjectButton:hover{"
            f"background:{colors.hover};border-color:{colors.accent};}}"
            "QToolButton#ProtectedNewProjectButton:pressed{"
            f"background:{colors.active};}}"
        )


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
        self.settings_store = SettingsStore(self.project_root)
        self.curtain_styles = CurtainStyleController(
            self.settings_store,
            self,
        )
        self.curtain_styles.previewColorsRequested.connect(
            self._refresh_curtain_preview_colors
        )
        self.project_dirty = ProjectDirtyController()
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
        self._autosave_pool = QtCore.QThreadPool(self)
        self._autosave_pool.setMaxThreadCount(1)
        self._autosave_active_revision: int | None = None
        self._autosave_pending_revision: int | None = None
        self._autosave_tasks: dict[int, _ProjectAutosaveTask] = {}
        self._forge_fullscreen_active = False
        self._forge_fullscreen_window: Optional[
            _ForgePreviewFullscreenWindow
        ] = None
        self._shutdown_complete = False
        self._shutdown_in_progress = False
        self._save_in_progress = False
        self._protected_change_count = 0
        self._protected_warning_shown = False
        self._protected_change_tracking_suspended = False
        self.setObjectName("NexusWindow")

        # Frameless + QSS
        self.setWindowFlags(
            self.windowFlags()
            | QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowSystemMenuHint
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
            | QtCore.Qt.WindowCloseButtonHint
        )
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
        # Keep maximize/restore on the ordinary Qt click path. Advertising the
        # custom button as HTMAXBUTTON makes Windows consume the click for its
        # Snap Layout flyout before QPushButton can toggle the window state.
        self._window_controller = FramelessWindowController(self)
        self.title_bar.set_project_title(
            str(self.settings_store.get("recipient_title", ""))
        )
        self.project_dirty.add_listener(self.title_bar.set_project_dirty)
        self.settings_store.changed.connect(self._on_project_settings_changed)
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

        # Chromium is only needed after Forge has a playable viewer. Keeping
        # the view absent until then avoids starting WebEngine for Image,
        # Sound, Message, and recipient-selection sessions.
        self.html_preview: Optional[QtWidgets.QWidget] = None

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
        # Help (top-right above the feature panel) — dual-state media swap
        # Idle = Help and prefers GIF; Hover = HHelp and prefers PNG.
        # =============================================================================================
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.setSpacing(0)
        self.protected_new_project_btn = _ThemedNewProjectButton(
            self.project_root,
            self,
        )
        self.protected_new_project_btn.setVisible(False)
        self.protected_new_project_btn.clicked.connect(self.start_new_project)
        set_control_help(
            self.protected_new_project_btn,
            "Leave this demonstration and begin your own letter.",
        )
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

        # Each state may be animated or static; candidate order selects the default.
        self._help_movie_idle: Optional[QMovie] = None
        self._help_movie_hover: Optional[QMovie] = None
        self._help_movie_idle_path = ""
        self._help_movie_hover_path = ""
        self._help_static_idle_path = ""
        self._help_static_hover_path = ""
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
            self
        )
        self._publication_activity_active = False
        self._publication_interaction_locked = False
        self._publication_tabbar_was_enabled = True
        self._project_loading_overlay.dismissed.connect(
            self._restore_publication_interaction
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
        self._restore_window_preferences()

        # Shortcuts + click effects
        self._install_shortcuts()
        try:
            install_click_fx(self)
        except Exception:
            pass

        # Double-click filter for full message view
        self._dbl_filter = _DoubleClickFilter(self)

        self._apply_current_theme()
        self.project_state.add_listener(self._on_project_state_transition)
        self._apply_application_state(initial_project_state)
        self.project_dirty.mark_saved()

        # Diagnostics after event loop starts
        QtCore.QTimer.singleShot(0, self._post_init_diagnostics)

    def _build_nexus_theme_stylesheet(self) -> str:
        """Build the shell stylesheet exclusively from semantic theme tokens."""
        colors = self.theme_service.tokens
        app_font = self.theme_service.app_font_family
        overlay = _theme_rgba(colors.panel_background, 209)
        return f"""
            /* Background ownership remains deliberately scoped. */
            QMainWindow#NexusWindow,
            QWidget#NexusRoot,
            QWidget#NexusBody {{
                background:{colors.background};
                color:{colors.text};
                font-family:'{app_font}';
                font-size:{SHELL_FONT_PX}px;
            }}

            QStackedWidget#FeatureStack,
            QStackedWidget#PreviewStack,
            QWidget#PreviewBlank {{
                background:transparent;
                border:none;
            }}

            QWidget#ImagePageSurface,
            QWidget#MessagePageSurface,
            QWidget#ForgePageSurface {{
                background:{colors.background};
                border:none;
            }}

            QWidget#CommandPageSurface {{
                background:transparent;
                border:none;
            }}

            QWidget#SoundPageSurface {{
                background:{colors.panel_background};
                border:none;
            }}

            QTabBar#MainTabBar {{
                background:{colors.background};
                color:{colors.text};
                font-family:'{app_font}';
                font-size:{SHELL_FONT_PX}px;
            }}

            QTabBar#MainTabBar::tab {{
                background:transparent;
                border:none;
                padding:8px 14px;
                margin-right:2px;
                color:{colors.muted_text};
                font-weight:700;
            }}
            QTabBar#MainTabBar::tab:selected {{
                color:{colors.highlight};
                border-bottom:2px solid {colors.primary};
            }}
            QTabBar#MainTabBar::tab:hover {{
                color:{colors.highlight};
            }}

            QTabBar#MainTabBar::tab:last {{
                font-family:'{COMMAND_FONT_FAMILY}';
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
                border:2px solid {colors.preview_frame_border};
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
        fallback_relative: str | None = None,
    ) -> Path:
        """Resolve active-theme artwork through the central theme service."""
        return self.theme_service.resolve_asset(
            logical_name,
            fallback=fallback_relative,
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
                f"font:12px '{self.theme_service.app_font_family}';"
                "padding:4px 6px;"
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
        self._apply_theme_styles_to_ordinary_ui()

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

    def _apply_theme_styles_to_ordinary_ui(self) -> None:
        for name in (
            "recipient_page",
            "image_tab",
            "sound_tab",
            "message_tab",
            "forge_tab",
            "_prompt_writer_win",
        ):
            widget = getattr(self, name, None)
            if not isinstance(widget, QtWidgets.QWidget):
                continue
            try:
                self.theme_service.apply_semantic_styles(widget)
            except (RuntimeError, TypeError, ValueError):
                _LOGGER.exception(
                    "Theme styles could not be applied to %s.",
                    type(widget).__name__,
                )

    @performance_timed("startup.project_tabs")
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
            curtain_styles=self.curtain_styles,
        )
        self.command_tab = CommandTab(
            self.project_root,
            project_state=self.project_state,
        )
        self.command_tab.setProperty("themeIndependent", True)

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
        self.command_page.setProperty("themeIndependent", True)
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
        publication_activity = getattr(
            self.forge_tab,
            "publication_activity_changed",
            None,
        )
        if publication_activity is not None:
            publication_activity.connect(self._set_publication_activity)
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
        self.image_tab.images_changed.connect(
            lambda _reason: self.project_dirty.mark_changed("images")
        )
        self.image_tab.images_changed.connect(
            lambda _reason: self._record_protected_edit()
        )
        self.image_tab.animation_settings_changed.connect(
            lambda _index: self.project_dirty.mark_changed("image-animation")
        )
        self.image_tab.animation_settings_changed.connect(
            lambda _index: self._record_protected_edit()
        )
        self.sound_tab.project_sound.changed.connect(
            lambda: self.project_dirty.mark_changed("sound")
        )
        self.sound_tab.project_sound.changed.connect(
            self._record_protected_edit
        )
        self.message_tab.project_changed.connect(
            lambda: self.project_dirty.mark_changed("message")
        )
        self.message_tab.project_changed.connect(
            self._record_protected_edit
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
        self._apply_theme_styles_to_ordinary_ui()
        self.tabbar.setCurrentIndex(0)
        self._tab_changed(0)
        self._sync_protected_project_ui()
        self._schedule_curtain_preparation(immediate=True)

    def _schedule_curtain_preparation(
        self,
        *,
        immediate: bool = False,
    ) -> None:
        self._curtain_preparation_generation += 1
        self._curtain_preparation_timer.stop()
        self._refresh_curtain_preview_colors()
        cover = (
            Path(self.project_root)
            / "gallery"
            / "user"
            / "pages"
            / "cover.png"
        )
        if not cover.is_file():
            return
        if immediate:
            self._start_curtain_preparation()
        else:
            self._curtain_preparation_timer.start()

    @QtCore.Slot()
    def _refresh_curtain_preview_colors(self) -> None:
        root = Path(self.project_root)
        cover = root / "gallery" / "user" / "pages" / "cover.png"
        self.curtain_styles.set_preview_colors(
            curtain_variant_rgbs(cover if cover.is_file() else None)
        )

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
            self._refresh_curtain_preview_colors()
            return
        self.curtain_styles.set_preview_colors(dict(result.colors))

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
        self._refresh_curtain_preview_colors()

    def _on_project_settings_changed(
        self,
        _settings: dict,
        keys: tuple[str, ...],
    ) -> None:
        self._sync_protected_project_ui(_settings)
        if "recipient_title" in keys:
            self.title_bar.set_project_title(
                str(_settings.get("recipient_title", ""))
            )
        ignored = {
            "active_play_dir",
            "app_icon",
            "debug",
            "last_music_folder",
            "settings_schema_version",
            "visionary_url",
        }
        if any(not key.startswith("ui_") and key not in ignored for key in keys):
            self.project_dirty.mark_changed("settings")

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
            self._show_help_asset("idle")
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
        self._stop_help_movies()
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

        self._developer_mode_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+Alt+Shift+D"),
            self,
        )
        self._developer_mode_shortcut.activated.connect(
            self.open_developer_mode
        )

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
        self._protected_change_tracking_suspended = True
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
        self._schedule_curtain_preparation(immediate=True)
        self.forge_tab.refresh_project_state()
        self.forge_tab.refresh_saved_letters()
        self._show_forge_preview()
        self.project_dirty.mark_saved()
        self._protected_change_count = 0
        self._protected_warning_shown = False
        self._protected_change_tracking_suspended = False
        self._sync_protected_project_ui()

    def _sync_protected_project_ui(
        self,
        snapshot: dict | None = None,
    ) -> None:
        button = getattr(self, "protected_new_project_btn", None)
        if button is None:
            return
        settings = (
            snapshot
            if snapshot is not None
            else self.settings_store.snapshot()
        )
        visible = (
            is_protected_project(settings)
            and not bool(getattr(self, "_command_immersive", False))
        )
        button.setVisible(visible)
        if visible:
            QtCore.QTimer.singleShot(
                0,
                self._position_protected_new_project_button,
            )

    def _position_protected_new_project_button(self) -> None:
        button = getattr(self, "protected_new_project_btn", None)
        if button is None or not button.isVisible():
            return
        image_tab = getattr(self, "image_tab", None)
        prompt_button = getattr(image_tab, "pwrite_fab", None)
        if prompt_button is None:
            return

        x_position = prompt_button.x()
        y_position = (
            prompt_button.y()
            + prompt_button.height()
            + NEW_PROJECT_ARTWORK_GAP
        )
        button.move(
            max(0, min(x_position, self.width() - button.width())),
            max(0, min(y_position, self.height() - button.height())),
        )
        button.raise_()

    def _record_protected_edit(self) -> None:
        if self._protected_change_tracking_suspended:
            return
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is None or not forge_tab.is_protected_project():
            return
        self._protected_change_count += 1
        if self._protected_warning_shown or self._protected_change_count < 5:
            return
        self._protected_warning_shown = True
        self._show_protected_letter_warning()

    def _show_protected_letter_warning(self) -> None:
        dialog = QDialog(self, Qt.Popup | Qt.FramelessWindowHint)
        dialog.setObjectName("ProtectedLetterWarning")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        message = QLabel(
            "This is a Stock or Example Letter. Changes are temporary and "
            "cannot replace the original. Start a New Project when you are "
            "ready to create your own letter.",
            dialog,
        )
        message.setWordWrap(True)
        message.setMaximumWidth(430)
        layout.addWidget(message)
        action = QtWidgets.QPushButton("New Project", dialog)
        action.clicked.connect(dialog.accept)
        action.clicked.connect(self.start_new_project)
        layout.addWidget(action, 0, Qt.AlignRight)
        dialog.adjustSize()
        center = self.mapToGlobal(self.rect().center())
        frame = dialog.frameGeometry()
        frame.moveCenter(center)
        dialog.move(frame.topLeft())
        dialog.exec()

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
            web_view_type, _settings_type = _webengine_components()
            dlg = QDialog(self)
            dlg.setWindowTitle("Message Preview")
            dlg.resize(900, 700)

            lay = QVBoxLayout(dlg)
            view = web_view_type(dlg)
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
        self.title_bar.set_command_mode(active)
        if self._command_immersive == active:
            self._sync_protected_project_ui()
            if active:
                self._position_command_tabbar()
            return

        self._command_immersive = active
        self._sync_protected_project_ui()
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
        self.settings_store.update_fields({"ui_last_tab": tab_name})
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
        if idx == 4:
            self._stop_help_movies()
        else:
            self._show_help_asset("idle")
        if self.help_pop.isVisible():
            if idx == 4:
                self._hide_help_popover()
            else:
                self._refresh_help_text(idx)
                self._reposition_help_popover()

        QtCore.QTimer.singleShot(0, self._update_preview_geometry)

    def _autosave_project_on_tab_switch(self) -> str:
        dirty = getattr(self, "project_dirty", None)
        if dirty is not None and not dirty.is_dirty:
            return ""
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is not None and forge_tab.is_protected_project():
            if dirty is not None:
                dirty.mark_saved()
            return "Demonstration changes saved for this session."
        flush_prompt_writer = getattr(
            self,
            "flush_prompt_writer_state",
            None,
        )
        if callable(flush_prompt_writer) and not flush_prompt_writer():
            return "Project not saved: Prompt Writer state could not be saved."
        eligibility = self.project_save_service.save_eligibility()
        if not eligibility.can_save:
            return f"Project not saved: {eligibility.blocked_reason}"
        autosave_pool = getattr(self, "_autosave_pool", None)
        if autosave_pool is not None and dirty is not None:
            revision = dirty.revision
            if self._autosave_active_revision is not None:
                self._autosave_pending_revision = revision
                return "Project autosave queued."
            self._start_project_autosave(revision)
            return "Project autosave started."
        try:
            self.project_save_service.save_workspace_snapshot(
                reason="tab-switch",
            )
        except ProjectNotReadyError as error:
            return f"Project not saved: {error}"
        except Exception as error:
            _LOGGER.exception("Project tab-switch autosave failed: %s", error)
            return f"Project autosave failed: {error}"
        if dirty is not None:
            dirty.mark_saved()
        return "Project autosaved."

    def _start_project_autosave(self, revision: int) -> None:
        task = _ProjectAutosaveTask(
            self.project_save_service,
            revision,
            "tab-switch",
        )
        task.signals.completed.connect(self._project_autosave_completed)
        task.signals.failed.connect(self._project_autosave_failed)
        self._autosave_active_revision = int(revision)
        self._autosave_tasks[int(revision)] = task
        self._autosave_pool.start(task)

    @QtCore.Slot(int, str)
    def _project_autosave_completed(
        self,
        revision: int,
        _destination: str,
    ) -> None:
        if self._autosave_active_revision != int(revision):
            self._autosave_tasks.pop(int(revision), None)
            return
        try:
            self.project_save_service.finish_deferred_workspace_snapshot()
        except Exception as error:
            self._project_autosave_failed(int(revision), str(error))
            return

        self._autosave_active_revision = None
        self._autosave_tasks.pop(int(revision), None)
        saved = self.project_dirty.mark_saved(
            expected_revision=int(revision)
        )
        self._autosave_pending_revision = None
        if (
            self.project_dirty.is_dirty
            and self.project_dirty.revision != int(revision)
            and not self._shutdown_in_progress
            and not self._shutdown_complete
        ):
            self._start_project_autosave(self.project_dirty.revision)
            return
        if (
            saved
            and not self._shutdown_in_progress
            and not self._shutdown_complete
        ):
            self.status("Project autosaved.")

    @QtCore.Slot(int, str)
    def _project_autosave_failed(self, revision: int, message: str) -> None:
        if self._autosave_active_revision == int(revision):
            self._autosave_active_revision = None
        self._autosave_tasks.pop(int(revision), None)
        self._autosave_pending_revision = None
        if (
            self.project_dirty.is_dirty
            and self.project_dirty.revision != int(revision)
            and not self._shutdown_in_progress
            and not self._shutdown_complete
        ):
            self._start_project_autosave(self.project_dirty.revision)
            return
        _LOGGER.error("Project tab-switch autosave failed: %s", message)
        if not self._shutdown_in_progress and not self._shutdown_complete:
            self.status(f"Project autosave failed: {message}")

    def _finish_project_autosave_for_shutdown(
        self,
        timeout_ms: int = 5000,
    ) -> bool:
        pool = getattr(self, "_autosave_pool", None)
        if pool is None:
            return True
        deadline = monotonic() + (max(0, int(timeout_ms)) / 1000.0)

        while True:
            revision = self._autosave_active_revision
            if revision is None:
                if not self.project_dirty.is_dirty:
                    self._autosave_pending_revision = None
                    return True
                forge_tab = getattr(self, "forge_tab", None)
                is_protected = getattr(forge_tab, "is_protected_project", None)
                if callable(is_protected) and is_protected():
                    self._autosave_pending_revision = None
                    return True
                eligibility = self.project_save_service.save_eligibility()
                if not eligibility.can_save:
                    return True
                if monotonic() >= deadline:
                    return False
                revision = self.project_dirty.revision
                self._autosave_pending_revision = revision
                self._start_project_autosave(revision)

            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                return False
            remaining_ms = max(1, int(remaining_seconds * 1000.0))
            if not pool.waitForDone(remaining_ms):
                return False

            revision = self._autosave_active_revision
            if revision is None:
                continue
            task = self._autosave_tasks.get(int(revision))
            if task is None or task.error or not task.destination:
                self._autosave_active_revision = None
                self._autosave_tasks.pop(int(revision), None)
                if (
                    self.project_dirty.is_dirty
                    and self.project_dirty.revision != int(revision)
                ):
                    self._autosave_pending_revision = (
                        self.project_dirty.revision
                    )
                    continue
                return False
            try:
                self.project_save_service.finish_deferred_workspace_snapshot()
            except Exception:
                _LOGGER.exception(
                    "Deferred autosave finalization failed during shutdown."
                )
                self._autosave_active_revision = None
                self._autosave_tasks.pop(int(revision), None)
                return False

            self._autosave_active_revision = None
            self._autosave_tasks.pop(int(revision), None)
            saved = self.project_dirty.mark_saved(
                expected_revision=int(revision)
            )
            self._autosave_pending_revision = None
            if saved:
                return True
            self._autosave_pending_revision = self.project_dirty.revision

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
    def _ensure_forge_preview(self) -> QtWidgets.QWidget:
        """Construct the shared WebEngine view on first actual viewer use."""
        view = self.html_preview
        if view is not None:
            return view

        web_view_type, settings_type = _webengine_components()
        view = web_view_type()
        preview_background = self.theme_service.tokens.background
        view.setStyleSheet(f"background-color:{preview_background};")
        view.page().setBackgroundColor(QColor(preview_background))
        view.settings().setAttribute(
            settings_type.FullScreenSupportEnabled,
            True,
        )
        view.settings().setAttribute(
            settings_type.LocalContentCanAccessFileUrls,
            True,
        )
        view.page().fullScreenRequested.connect(
            self._on_web_fullscreen_requested
        )
        view.installEventFilter(self)
        view.installEventFilter(self._dbl_filter)
        self.preview_stack.addWidget(view)
        self.html_preview = view
        return view

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
        view = self._ensure_forge_preview()
        self._clear_preview()
        view.setHtml(html or "<p></p>")
        self.preview_stack.setCurrentWidget(view)
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
        self.curtain_styles.sync_from_settings()
        self.curtain_styles.set_preview_colors({})

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
        view = self._ensure_forge_preview()
        self._forge_preview_mode = (
            mode if mode in {"portrait", "landscape", "window"} else "portrait"
        )
        self._update_preview_geometry()
        self._last_pixmap = None
        try:
            modified = index.stat().st_mtime_ns
        except OSError:
            modified = None

        current_url = view.url()
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
        self.preview_stack.setCurrentWidget(view)
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
        view.setUrl(viewer_url)
        self.status(
            f"Interactive letter preview: "
            f"{self._forge_preview_mode.replace('-', ' ')}"
        )

    def _set_forge_preview_visible(self, visible: bool) -> None:
        if visible:
            return
        view = self.html_preview
        if view is None:
            return
        view.page().runJavaScript(
            "document.querySelectorAll('audio,video').forEach("
            "media => { try { media.pause(); } catch (_) {} });"
        )

    def _release_forge_preview_files(self) -> None:
        """Stop playback and release the generated viewer's file handles."""
        view = self.html_preview
        if view is None:
            return
        if self._forge_fullscreen_active:
            self._restore_forge_preview_from_fullscreen()
        try:
            view.page().runJavaScript(
                "document.querySelectorAll('audio,video').forEach("
                "media => { try { media.pause(); media.currentTime = 0; } catch (_) {} });"
            )
        except Exception:
            pass
        if self.preview_stack.currentWidget() is view:
            self.preview_stack.setCurrentIndex(0)
        if self.tabbar.currentIndex() == 3:
            self.preview_caption.setText("Preparing interactive preview…")
            self.preview_caption.setVisible(True)
        else:
            self.preview_caption.setVisible(False)
        previous_url = view.url()
        view.stop()
        blank_url = QUrl("about:blank")
        if not previous_url.isLocalFile():
            view.setUrl(blank_url)
            return

        unload_loop = QtCore.QEventLoop()
        unload_timeout = QtCore.QTimer()
        unload_timeout.setSingleShot(True)
        unloaded = False

        def finish_unload(ok: bool) -> None:
            nonlocal unloaded
            if ok and view.url() == blank_url:
                unloaded = True
                unload_loop.quit()

        view.loadFinished.connect(finish_unload)
        unload_timeout.timeout.connect(unload_loop.quit)
        try:
            view.setUrl(blank_url)
            unload_timeout.start(1500)
            unload_loop.exec(QtCore.QEventLoop.ExcludeUserInputEvents)
        finally:
            unload_timeout.stop()
            try:
                view.loadFinished.disconnect(finish_unload)
            except (RuntimeError, TypeError):
                pass
        if not unloaded:
            _LOGGER.warning(
                "Timed out waiting for the embedded Forge preview to release %s",
                previous_url.toLocalFile(),
            )

    def _release_project_files_for_restore(self) -> bool:
        """Release project-owned media handles before an atomic restore."""
        errors: list[str] = []
        if getattr(self, "_autosave_active_revision", None) is not None:
            errors.append("project autosave is still running")
        try:
            self.message_tab.prepare_for_project_restore()
        except Exception as error:
            _LOGGER.exception("Message files could not be released before restore.")
            errors.append(str(error) or "message rendering is still busy")
        try:
            self.image_tab.prepare_for_project_restore()
        except Exception as error:
            _LOGGER.exception("Image files could not be released before restore.")
            errors.append(str(error) or "image resources are still busy")
        try:
            self.sound_tab.prepare_for_project_restore()
        except Exception as error:
            _LOGGER.exception("Sound files could not be released before restore.")
            errors.append(str(error) or "sound resources are still busy")
        if errors:
            forge_tab = getattr(self, "forge_tab", None)
            report_failure = getattr(
                forge_tab,
                "report_project_file_release_failure",
                None,
            )
            if callable(report_failure):
                report_failure("; ".join(errors))
            return False
        return True

    def prepare_for_project_reset(self) -> None:
        """Stop project-owned workers and media before New Project commits."""
        if not self._stop_curtain_preparation():
            raise RuntimeError(
                "Curtain preparation did not stop before New Project."
        )
        self._release_forge_preview_files()
        if not self._release_project_files_for_restore():
            raise RuntimeError(
                "Project media could not be stopped before New Project."
            )

    @QtCore.Slot(bool, str)
    def _set_restore_activity(self, active: bool, message: str) -> None:
        if active:
            self._project_loading_overlay.start(message, mode="restore")
            self._position_project_loading_overlay()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents
                | QtCore.QEventLoop.ExcludeSocketNotifiers
            )
            return
        self._project_loading_overlay.stop()

    @QtCore.Slot(bool, str, str)
    def _set_publication_activity(
        self,
        active: bool,
        operation: str,
        message: str,
    ) -> None:
        operation_name = str(operation or "publish").strip().casefold()
        mode = "unpublish" if operation_name.startswith("un") else "publish"
        overlay = self._project_loading_overlay
        if active:
            self._publication_activity_active = True
            if not self._publication_interaction_locked:
                self._publication_tabbar_was_enabled = self.tabbar.isEnabled()
                self._publication_interaction_locked = True
            self.tabbar.setEnabled(False)
            forge_tab = getattr(self, "forge_tab", None)
            dismiss_readiness = getattr(forge_tab, "dismiss_readiness", None)
            if callable(dismiss_readiness):
                dismiss_readiness()
            self._hide_help_popover()
            title = (
                "Unpublishing Letter…"
                if mode == "unpublish"
                else "Publishing Letter…"
            )
            detail = str(message or "").strip()
            if detail.casefold() == title.casefold():
                detail = ""
            overlay.start(title, mode=mode, detail=detail)
            self._position_project_loading_overlay()
            return
        self._publication_activity_active = False
        overlay.stop(animated=True)

    @QtCore.Slot()
    def _restore_publication_interaction(self) -> None:
        if (
            self._publication_activity_active
            or not self._publication_interaction_locked
        ):
            return
        self.tabbar.setEnabled(self._publication_tabbar_was_enabled)
        self._publication_interaction_locked = False

    def _position_project_loading_overlay(self) -> None:
        overlay = getattr(self, "_project_loading_overlay", None)
        if overlay is None:
            return
        if overlay.activity_mode in overlay._PUBLICATION_MODES:
            overlay.setGeometry(self.rect())
            if overlay.isVisible():
                overlay.raise_()
            return
        origin = self.main_widget.mapTo(self, QtCore.QPoint(0, 0))
        top = origin.y() + self.title_bar.geometry().bottom() + 1
        overlay.setGeometry(
            origin.x(),
            top,
            self.main_widget.width(),
            max(
                0,
                self.main_widget.height()
                - (top - origin.y()),
            ),
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
        view = self._ensure_forge_preview()
        window = self._forge_fullscreen_window
        if window is None:
            window = _ForgePreviewFullscreenWindow(self)
            window.exit_requested.connect(
                self._request_forge_fullscreen_exit
            )
            self._forge_fullscreen_window = window

        self._forge_fullscreen_active = True
        self.preview_stack.removeWidget(view)
        window.attach_preview(view)
        screen = self.screen()
        if screen is not None:
            window.setGeometry(screen.geometry())
        window.showFullScreen()
        view.show()
        window.layout().activate()
        window.raise_()
        window.activateWindow()
        view.setFocus()

    def _request_forge_fullscreen_exit(self) -> None:
        if not self._forge_fullscreen_active:
            return
        view = self.html_preview
        if view is None:
            self._restore_forge_preview_from_fullscreen()
            return
        try:
            view.page().runJavaScript(
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
        view = self.html_preview
        if view is None:
            return
        window = self._forge_fullscreen_window
        if window is not None:
            window.hide()
            window.detach_preview(view)
        if self.preview_stack.indexOf(view) < 0:
            self.preview_stack.addWidget(view)
        self.preview_stack.setCurrentWidget(view)
        QtCore.QTimer.singleShot(0, self._update_preview_geometry)
        view.setFocus()

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

    def _restore_window_preferences(self) -> None:
        encoded = str(self.settings_store.get("ui_window_geometry", ""))
        if encoded:
            try:
                geometry = QtCore.QByteArray.fromBase64(encoded.encode("ascii"))
                self.restoreGeometry(geometry)
            except Exception:
                _LOGGER.exception("Window geometry could not be restored.")
        if bool(self.settings_store.get("ui_window_maximized", False)):
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    def _save_window_preferences(self) -> None:
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.settings_store.update_fields(
            {
                "ui_window_geometry": geometry,
                "ui_window_maximized": self.isMaximized(),
            }
        )

    # =============================================================================================
    # Resize/Move: keep preview aspect; keep popover aligned
    # =============================================================================================
    def _update_preview_geometry(self) -> None:
        """Keep Sound controls usable across window states without shrinking other tabs."""
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
        if current_tab == 1:
            # The visualizer is naturally 169 x 253. Enlarging it in maximized
            # windows steals the height needed by the Sound controls below.
            h = max(
                SOUND_PREVIEW_MIN_HEIGHT,
                min(SOUND_PREVIEW_MAX_HEIGHT, int(window_height * 0.30)),
            )
        else:
            # Preserve the existing appearance on every other tab.
            h = int(window_height * 0.35)

        w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

    def nativeEvent(self, event_type, message):
        controller = getattr(self, "_window_controller", None)
        if controller is not None:
            handled, result = controller.native_event(event_type, message)
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event):
        self._update_preview_geometry()
        self._update_preview_tools_geometry()
        self._position_protected_new_project_button()
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
                stop_tab_animation = getattr(
                    self,
                    "_stop_shared_tab_animation",
                    None,
                )
                if callable(stop_tab_animation):
                    stop_tab_animation()
            except Exception:
                _LOGGER.exception("Tab transition shutdown failed.")
            try:
                if not self._stop_curtain_preparation(timeout_ms=5000):
                    _LOGGER.warning(
                        "Curtain preparation did not stop before shutdown."
                    )
            except Exception:
                _LOGGER.exception("Curtain preparation shutdown failed.")
            autosave_deadline = monotonic() + 5.0
            try:
                if not self.flush_prompt_writer_state():
                    _LOGGER.warning(
                        "Prompt Writer state could not be flushed during shutdown."
                    )
            except Exception:
                _LOGGER.exception("Prompt Writer shutdown flush failed.")
            try:
                autosave_remaining_ms = max(
                    0,
                    int((autosave_deadline - monotonic()) * 1000.0),
                )
                if not self._finish_project_autosave_for_shutdown(
                    autosave_remaining_ms
                ):
                    _LOGGER.warning(
                        "Project autosave did not finish cleanly before shutdown."
                    )
            except Exception:
                _LOGGER.exception("Project autosave shutdown failed.")
            if self._project_tabs_initialized:
                try:
                    if not self.forge_tab.shutdown(timeout_ms=5000):
                        _LOGGER.warning(
                            "Forge resources did not stop before shutdown."
                        )
                except Exception:
                    _LOGGER.exception("Forge resource shutdown failed.")
            save_preferences = getattr(self, "_save_window_preferences", None)
            if callable(save_preferences):
                save_preferences()
            for timer_name in (
                "_image_tab_readiness_hide_timer",
                "_help_show_timer",
                "_help_hide_timer",
                "_toast_timer",
            ):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            clear_preview = getattr(self, "_clear_preview", None)
            if callable(clear_preview):
                clear_preview()
            cancel_command_fade = getattr(self, "_cancel_command_fade", None)
            if callable(cancel_command_fade):
                cancel_command_fade()
            loading_overlay = getattr(self, "_project_loading_overlay", None)
            if loading_overlay is not None:
                loading_overlay.stop()
            help_icon = getattr(self, "help_icon", None)
            if help_icon is not None:
                help_icon.setMovie(None)
            for movie in (
                getattr(self, "_help_movie_idle", None),
                getattr(self, "_help_movie_hover", None),
            ):
                if movie is not None:
                    movie.stop()
            title_bar = getattr(self, "title_bar", None)
            stop_settings_movie = getattr(
                title_bar,
                "_show_static_settings_icon",
                None,
            )
            if callable(stop_settings_movie):
                stop_settings_movie()
            settings_store = getattr(self, "settings_store", None)
            settings_changed = getattr(settings_store, "changed", None)
            settings_callback = getattr(self, "_on_project_settings_changed", None)
            if settings_changed is not None and callable(settings_callback):
                settings_changed.disconnect(settings_callback)
            curtain_styles = getattr(self, "curtain_styles", None)
            if curtain_styles is not None:
                curtain_styles.close()
            project_dirty = getattr(self, "project_dirty", None)
            dirty_callback = getattr(title_bar, "set_project_dirty", None)
            if project_dirty is not None and callable(dirty_callback):
                project_dirty.remove_listener(dirty_callback)
            self.project_state.remove_listener(
                self._on_project_state_transition
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
                    if not self.sound_tab.shutdown(timeout_ms=5000):
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
            try:
                self._dispose_forge_preview()
            except Exception:
                _LOGGER.exception("Forge WebEngine disposal failed.")
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

    def _dispose_forge_preview(self) -> None:
        """Close the fullscreen host and the view-owned WebEngine page."""
        fullscreen = getattr(self, "_forge_fullscreen_window", None)
        if fullscreen is not None:
            shutdown = getattr(fullscreen, "shutdown", None)
            if callable(shutdown):
                shutdown()
            self._forge_fullscreen_window = None

        view = getattr(self, "html_preview", None)
        if view is None:
            return
        try:
            view.page().fullScreenRequested.disconnect(
                self._on_web_fullscreen_requested
            )
        except (RuntimeError, TypeError):
            pass
        for event_filter in (
            self,
            getattr(self, "_dbl_filter", None),
        ):
            if event_filter is None:
                continue
            try:
                view.removeEventFilter(event_filter)
            except RuntimeError:
                pass
        preview_stack = getattr(self, "preview_stack", None)
        if preview_stack is not None and preview_stack.indexOf(view) >= 0:
            preview_stack.removeWidget(view)
        view.stop()
        view.hide()
        view.close()
        view.setParent(None)
        view.deleteLater()
        self.html_preview = None

    def start_new_project(self) -> None:
        """Confirm and clear only the currently active editable project."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is not None and forge_tab.operation_in_progress:
            self.status("Finish the current Forge operation before starting a new project.")
            return
        protected = bool(
            forge_tab is not None and forge_tab.is_protected_project()
        )
        if self.project_state.is_project_ready and not protected:
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Start New Project",
                question=(
                    "Discard the current active project and start a new one? "
                    "Saved letters, backups, Prompt Writer libraries, palettes, "
                    "and application preferences will be kept."
                ),
                primary_text="Yes",
                secondary_text="No",
                destructive_primary=True,
                click_outside_dismiss=False,
                width=560,
            )
            if confirmation.exec() != QtWidgets.QDialog.Accepted:
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
            show_lettersmith_message(
                self,
                "New Project",
                f"The active project could not be cleared: {error}",
            )
            return
        if completed:
            self._on_command_wiped()
            self._protected_change_count = 0
            self._protected_warning_shown = False
            self._sync_protected_project_ui()
            self.status("New project ready. Enter a recipient to begin.")

    def delete_project(self) -> None:
        """Confirm and permanently remove the active editable project."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        forge_tab = getattr(self, "forge_tab", None)
        if forge_tab is not None and forge_tab.operation_in_progress:
            self.status("Finish the current Forge operation before deleting the project.")
            return
        if not self.project_state.is_project_ready:
            self.status("There is no active project to delete.")
            return
        if forge_tab is not None and forge_tab.is_protected_project():
            self.status("Stock and Example Letters cannot be deleted.")
            return

        settings = SettingsStore(self.project_root).snapshot()
        recipient = str(settings.get("recipient_name", "")).strip()
        title = str(settings.get("recipient_title", "")).strip()
        project_name = title or "Untitled Letter"
        owner = f" for {recipient}" if recipient else ""
        confirmation = LetterSmithConfirmationDialog(
            self,
            title="Delete Project",
            question=(
                f'Permanently delete "{project_name}"{owner}?\n\n'
                "The active draft, saved letter, and recovery copies will be "
                "removed from this device. Shared Prompt Writer libraries, "
                "palettes, music, application preferences, and published "
                "copies will be kept.\n\nThis cannot be undone."
            ),
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
            width=620,
        )
        if confirmation.exec() != QtWidgets.QDialog.Accepted:
            self.status("Project deletion canceled.")
            return

        try:
            from command import delete_project

            completed = delete_project(
                self,
                project_root=self.project_root,
                project_state=self.project_state,
            )
        except Exception as error:
            _LOGGER.exception("Delete Project failed: %s", error)
            show_lettersmith_message(
                self,
                "Delete Project",
                f"The active project could not be deleted: {error}",
            )
            return
        if completed:
            self._on_command_wiped()
            self._protected_change_count = 0
            self._protected_warning_shown = False
            self._sync_protected_project_ui()
            self.status("Project deleted. Enter a recipient to begin.")

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
    # Prompt Writer opener (used by Image_tab FAB and Ctrl+Alt+P shortcut)
    # =============================================================================================
    def open_developer_mode(self) -> None:
        """Open the guarded developer reset dialog."""
        if getattr(self, "_developer_mode_active", False):
            return
        self._developer_mode_active = True
        try:
            from developermode import run_developer_mode

            run_developer_mode(
                project_root=self.project_root,
                parent=self,
            )
        finally:
            self._developer_mode_active = False

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
            self.theme_service.apply_semantic_styles(w)
            w.project_changed.connect(
                lambda: self.project_dirty.mark_changed("prompt-writer")
            )
            w.project_changed.connect(self._record_protected_edit)
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

        resolver = getattr(self.theme_service, "resolve_first_asset")
        for kind, candidates in HELP_THEME_ASSET_CANDIDATES.items():
            selected = resolver(candidates, fallback=REL_HELP_PNG)
            selected_path = str(selected) if selected.is_file() else ""
            movie_path = selected_path if selected.suffix.casefold() == ".gif" else ""
            static_candidate = next(
                candidate for candidate in candidates if candidate.endswith(".png")
            )
            static_path = self.resolve_theme_asset(
                static_candidate,
                REL_HELP_PNG,
            )
            setattr(self, f"_help_movie_{kind}_path", movie_path)
            setattr(
                self,
                f"_help_static_{kind}_path",
                str(static_path) if static_path.is_file() else "",
            )

        project_ready = bool(
            getattr(getattr(self, "project_state", None), "is_project_ready", False)
        )
        command_active = (
            getattr(self, "tabbar", None) is not None
            and self.tabbar.currentIndex() == 4
        )
        if project_ready and not command_active:
            if self._show_help_asset("idle"):
                return
        fallback = self.resolve_theme_asset(
            "help/fallback.png",
            REL_HELP_PNG,
        )
        self._set_help_fallback_icon(str(fallback))

    def _ensure_help_movie(self, kind: str) -> Optional[QMovie]:
        """Create one help animation only when that state becomes visible."""
        if kind not in {"idle", "hover"}:
            raise ValueError(f"Unknown help movie kind: {kind}")
        attribute = f"_help_movie_{kind}"
        movie = getattr(self, attribute)
        if movie is not None:
            return movie
        path = getattr(self, f"{attribute}_path", "")
        if not path:
            return None
        movie = QMovie(path, parent=self)
        if not movie.isValid():
            movie.deleteLater()
            setattr(self, f"{attribute}_path", "")
            return None
        movie.setCacheMode(QMovie.CacheAll)
        movie.setSpeed(100)
        movie.setScaledSize(QSize(HELP_ICON_PX, HELP_ICON_PX))
        setattr(self, attribute, movie)
        return movie

    def _show_help_movie(self, kind: str) -> bool:
        movie = self._ensure_help_movie(kind)
        if movie is None:
            return False
        for candidate in (
            self._help_movie_idle,
            self._help_movie_hover,
        ):
            if candidate is not None and candidate is not movie:
                candidate.stop()
        self.help_icon.setMovie(movie)
        if movie.state() == QMovie.NotRunning:
            movie.start()
        return True

    def _show_help_asset(self, kind: str) -> bool:
        if kind not in {"idle", "hover"}:
            raise ValueError(f"Unknown help asset kind: {kind}")
        if self._show_help_movie(kind):
            return True
        path = getattr(self, f"_help_static_{kind}_path", "")
        if not path:
            return False
        self._stop_help_movies()
        self.help_icon.setMovie(None)
        return self._set_help_fallback_icon(path)

    def _stop_help_movies(self) -> None:
        for movie in (
            self._help_movie_idle,
            self._help_movie_hover,
        ):
            if movie is not None:
                movie.stop()

    def _set_help_fallback_icon(self, png_path: str) -> bool:
        if os.path.exists(png_path):
            pm = QPixmap(png_path)
            if not pm.isNull():
                pm = pm.scaled(HELP_ICON_PX, HELP_ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.help_icon.setPixmap(pm)
                return True
        # Final fallback: small text
        self.help_icon.setText("Help")
        self.help_icon.setStyleSheet(
            "QLabel#HelpIcon{background:transparent;border:none;"
            "padding:0;margin:0;"
            f"color:{self.theme_service.tokens.accent};font-weight:800;}}"
        )
        return False

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
                self._show_help_asset("hover")
            elif t in (QEvent.Leave, QEvent.HoverLeave):
                self._help_show_timer.stop()
                # If we immediately entered the popover, it will cancel this timer
                self._help_hide_timer.start()
                # Swap back to idle movie when leaving icon
                self._show_help_asset("idle")

        elif pop is not None and watched is pop:
            t = event.type()
            if t == QEvent.Enter:
                self._help_hide_timer.stop()
            elif t == QEvent.Leave:
                self._help_hide_timer.start()

        return super().eventFilter(watched, event)
