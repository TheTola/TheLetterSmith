from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from project_paths import application_paths
from settings_store import SettingsStore
from ui_fonts import resolve_registered_family
from window_chrome import place_window_on_launcher
from ui_theme import (
    CELESTIAL_ROSE_THEME,
    DEFAULT_FORGE_THEME_ID,
    DEFAULT_ROSE_THEME_ID,
    OBSIDIAN_FORGE_THEME,
    THEMES,
    THEME_FAMILY_SETTINGS_KEY,
    THEME_SETTINGS_KEY,
    normalize_theme_id,
)


THEME_FAMILY_DEFAULTS = {
    "forge": DEFAULT_FORGE_THEME_ID,
    "rose": DEFAULT_ROSE_THEME_ID,
}
THEME_SELECTOR_POSITION_SETTINGS_KEY = "ui_theme_family_selector_position"

_THEME_FAMILY_DARK_DEFAULTS = {
    "forge": OBSIDIAN_FORGE_THEME.theme_id,
    "rose": CELESTIAL_ROSE_THEME.theme_id,
}


class _GlowToolButton(QtWidgets.QToolButton):
    _ICON_SIZE = QtCore.QSize(500, 500)
    _DEPRESSED_ICON_SIZE = QtCore.QSize(450, 450)

    def __init__(
        self,
        glow_color: str,
        parent: QtWidgets.QWidget | None = None,
        *,
        icon_size: QtCore.QSize | None = None,
        depressed_icon_size: QtCore.QSize | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_size = QtCore.QSize(icon_size or self._ICON_SIZE)
        self._depressed_icon_size = QtCore.QSize(
            depressed_icon_size or self._DEPRESSED_ICON_SIZE
        )
        self._glow = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(38.0)
        self._glow.setColor(QtGui.QColor(glow_color))
        self._glow.setOffset(0, 0)
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)
        self._pending_click: Callable[[], None] | None = None

        self._press_animation = QtCore.QPropertyAnimation(
            self,
            b"iconSize",
            self,
        )
        self._press_animation.setDuration(100)
        self._press_animation.setEndValue(self._depressed_icon_size)
        self._press_animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._restore_animation = QtCore.QPropertyAnimation(
            self,
            b"iconSize",
            self,
        )
        self._restore_animation.setDuration(140)
        self._restore_animation.setEndValue(self._icon_size)
        self._restore_animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._bounce_animation = QtCore.QPropertyAnimation(
            self,
            b"iconSize",
            self,
        )
        self._bounce_animation.setDuration(280)
        self._bounce_animation.setKeyValueAt(
            0.45,
            QtCore.QSize(
                round(self._icon_size.width() * 1.024),
                round(self._icon_size.height() * 1.024),
            ),
        )
        self._bounce_animation.setKeyValueAt(
            0.72,
            QtCore.QSize(
                round(self._icon_size.width() * 0.968),
                round(self._icon_size.height() * 0.968),
            ),
        )
        self._bounce_animation.setEndValue(self._icon_size)
        self._bounce_animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._bounce_animation.finished.connect(self._finish_click)

        self.pressed.connect(self._animate_pressed)
        self.released.connect(self._animate_released)

    def _stop_icon_animations(self) -> None:
        self._press_animation.stop()
        self._restore_animation.stop()
        self._bounce_animation.stop()

    def _animate_pressed(self) -> None:
        self._stop_icon_animations()
        self._press_animation.setStartValue(self.iconSize())
        self._press_animation.start()

    def _animate_released(self) -> None:
        self._stop_icon_animations()
        self._restore_animation.setStartValue(self.iconSize())
        self._restore_animation.start()

    def animate_click(self, callback: Callable[[], None]) -> None:
        self._stop_icon_animations()
        self._pending_click = callback
        self.setEnabled(False)
        self._bounce_animation.setStartValue(self.iconSize())
        self._bounce_animation.start()

    def _finish_click(self) -> None:
        callback = self._pending_click
        self._pending_click = None
        if callback is not None:
            callback()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._glow.setEnabled(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._glow.setEnabled(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self._pending_click is None:
            self._animate_released()


class ThemeFamilySelector(QtWidgets.QDialog):
    """Neutral startup-only Male/Female theme-family selector."""

    def __init__(
        self,
        project_root: str | Path,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.selected_family = ""
        self._selection_in_progress = False
        self._drag_offset: QtCore.QPoint | None = None
        self._drag_moved = False
        self._position_restored = False
        self.setWindowTitle("")
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Popup, True)
        self.setModal(True)
        self.setMinimumSize(680, 400)
        stored_theme = self.settings.get(
            THEME_SETTINGS_KEY,
            DEFAULT_FORGE_THEME_ID,
        )
        app_font = resolve_registered_family(
            THEMES[normalize_theme_id(stored_theme)].app_font_family
        )
        self.setStyleSheet(
            "QDialog{background:#181b21;color:#f4f7fb;}"
            f"QLabel#ThemeFamilyQuestion{{font:600 24px '{app_font}';}}"
            "QToolButton{background:transparent;border:none;padding:0;}"
            "QToolButton:hover{background:transparent;border:none;}"
            "QToolButton:pressed{background:transparent;border:none;}"
            "QFrame#ThemeFamilyDivider{background:#566273;border:none;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 34)
        layout.setSpacing(24)
        self.question_label = QtWidgets.QLabel(
            "Are you male or female?",
            self,
        )
        self.question_label.setObjectName("ThemeFamilyQuestion")
        self.question_label.setAlignment(QtCore.Qt.AlignCenter)
        self.question_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.question_label)

        choices = QtWidgets.QHBoxLayout()
        choices.setSpacing(16)
        choices.addStretch(1)
        self.male_button = self._choice_button(
            "Male",
            "mal.png",
            "forge",
            "#2b8cff",
        )
        self.female_button = self._choice_button(
            "Female",
            "fem.png",
            "rose",
            "#ff4fa3",
        )
        choices.addWidget(self.male_button)
        self.choice_divider = QtWidgets.QFrame(self)
        self.choice_divider.setObjectName("ThemeFamilyDivider")
        self.choice_divider.setFrameShape(QtWidgets.QFrame.VLine)
        self.choice_divider.setFixedWidth(1)
        self.choice_divider.setMinimumHeight(500)
        self.choice_divider.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        choices.addWidget(self.choice_divider, 0, QtCore.Qt.AlignVCenter)
        choices.addWidget(self.female_button)
        choices.addStretch(1)
        layout.addLayout(choices)

    def _choice_button(
        self,
        label: str,
        icon_filename: str,
        family: str,
        glow_color: str,
    ) -> QtWidgets.QToolButton:
        button = _GlowToolButton(glow_color, self)
        button.setText("")
        button.setAccessibleName(label)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        button.setFixedSize(512, 512)
        button.setIconSize(QtCore.QSize(500, 500))
        icon_path = application_paths(self.project_root).app_resource_path(
            Path("icons") / icon_filename
        )
        icon = QtGui.QIcon(str(icon_path))
        if not icon.isNull():
            button.setIcon(icon)
        button.pressed.connect(self._begin_inside_selection)
        button.released.connect(
            lambda: QtCore.QTimer.singleShot(0, self._clear_selection_guard)
        )
        button.clicked.connect(lambda: self._select(family))
        return button

    def _begin_inside_selection(self) -> None:
        self._selection_in_progress = True

    def _clear_selection_guard(self) -> None:
        self._selection_in_progress = False

    def _select(self, family: str) -> None:
        self.selected_family = family
        self.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self.frameGeometry().contains(
            event.globalPosition().toPoint()
        ):
            self.reject()
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_moved = False
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & QtCore.Qt.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._drag_moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_offset is not None and self._drag_moved:
            self._persist_current_position()
        self._drag_offset = None
        self._drag_moved = False
        super().mouseReleaseEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        if not self._position_restored:
            self._position_restored = True
            self._restore_saved_position()
        place_window_on_launcher(self)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._persist_current_position()
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().hideEvent(event)

    def _persist_current_position(self) -> None:
        position = {"x": self.x(), "y": self.y()}
        if self.settings.get(THEME_SELECTOR_POSITION_SETTINGS_KEY, {}) == position:
            return
        self.settings.update_fields(
            {THEME_SELECTOR_POSITION_SETTINGS_KEY: position}
        )

    def _restore_saved_position(self) -> None:
        stored = self.settings.get(THEME_SELECTOR_POSITION_SETTINGS_KEY, {})
        if not isinstance(stored, dict):
            return
        x = stored.get("x")
        y = stored.get("y")
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            return
        place_window_on_launcher(
            self, geometry=QtCore.QRect(QtCore.QPoint(x, y), self.size()),
        )

    def eventFilter(
        self,
        watched: QtCore.QObject,
        event: QtCore.QEvent,
    ) -> bool:
        if self.isVisible() and not self._selection_in_progress:
            if event.type() == QtCore.QEvent.ApplicationDeactivate:
                self.reject()
            elif (
                event.type() == QtCore.QEvent.MouseButtonPress
                and isinstance(event, QtGui.QMouseEvent)
                and not self.frameGeometry().contains(
                    event.globalPosition().toPoint()
                )
            ):
                self.reject()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def event(self, event: QtCore.QEvent) -> bool:
        if (
            event.type() == QtCore.QEvent.WindowDeactivate
            and self.isVisible()
            and not self._selection_in_progress
        ):
            self.reject()
        return super().event(event)


def startup_theme_prompt_required(
    settings: SettingsStore,
    *,
    force_prompt: bool,
) -> bool:
    if force_prompt:
        return True
    family = str(settings.get(THEME_FAMILY_SETTINGS_KEY, "")).strip().casefold()
    return family not in THEME_FAMILY_DEFAULTS


def save_theme_family_preference(
    settings: SettingsStore,
    family: object,
) -> str:
    normalized = str(family or "").strip().casefold()
    try:
        light_theme_id = THEME_FAMILY_DEFAULTS[normalized]
        dark_theme_id = _THEME_FAMILY_DARK_DEFAULTS[normalized]
    except KeyError as error:
        raise ValueError("Theme family must be 'forge' or 'rose'.") from error
    theme_id = dark_theme_id if _system_uses_dark_mode() else light_theme_id
    settings.update_fields(
        {
            THEME_FAMILY_SETTINGS_KEY: normalized,
            THEME_SETTINGS_KEY: theme_id,
        }
    )
    return theme_id


def _system_uses_dark_mode() -> bool:
    color_scheme = QtGui.QGuiApplication.styleHints().colorScheme()
    return color_scheme == QtCore.Qt.ColorScheme.Dark


def ensure_startup_theme_preference(
    project_root: str | Path,
    *,
    force_prompt: bool,
    dialog_factory: Callable[[str | Path], ThemeFamilySelector] = ThemeFamilySelector,
) -> str:
    """Return the startup theme ID, prompting only when configured to do so."""
    settings = SettingsStore(project_root)
    if not startup_theme_prompt_required(settings, force_prompt=force_prompt):
        return str(settings.get(THEME_SETTINGS_KEY, DEFAULT_FORGE_THEME_ID))

    dialog = dialog_factory(project_root)
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return ""
    return save_theme_family_preference(settings, dialog.selected_family)


__all__ = [
    "THEME_FAMILY_DEFAULTS",
    "THEME_SELECTOR_POSITION_SETTINGS_KEY",
    "ThemeFamilySelector",
    "ensure_startup_theme_preference",
    "save_theme_family_preference",
    "startup_theme_prompt_required",
]
