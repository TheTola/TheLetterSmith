from __future__ import annotations

"""Reusable artwork-backed QPushButton with a safe text-button fallback."""

from enum import Enum
from pathlib import Path
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from project_paths import application_paths
from ui_theme import (
    BUTTON_TIER_PROPERTY,
    ButtonTier,
    apply_button_tier,
)


_ARTWORK_STYLESHEET = "QPushButton{background:transparent;border:none;padding:0;}"
BUTTON_STATE_STABILIZATION_MS = 1500
BUTTON_STATE_FADE_MS = 400
INVISIBLE_BUTTON_OPACITY = 0.52


class ButtonSemanticState(str, Enum):
    NORMAL = "normal"
    BROKEN = "broken"


class _InvisibleControlFader(QtCore.QObject):
    """Apply the shared Invisible treatment to non-artwork controls."""

    def __init__(self, control: QtWidgets.QWidget) -> None:
        super().__init__(control)
        self._control = control
        existing_effect = control.graphicsEffect()
        self._effect = (
            existing_effect
            if isinstance(existing_effect, QtWidgets.QGraphicsOpacityEffect)
            else None
        )
        if existing_effect is None:
            self._effect = QtWidgets.QGraphicsOpacityEffect(control)
            self._effect.setOpacity(1.0)
            control.setGraphicsEffect(self._effect)
        self._animation = QtCore.QVariantAnimation(self)
        self._animation.setDuration(BUTTON_STATE_FADE_MS)
        self._animation.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._animation.valueChanged.connect(self._apply_opacity)

    def apply(self, *, available: bool, invisible: bool) -> None:
        self._control.setEnabled(bool(available) and not bool(invisible))
        if self._effect is None:
            return
        target = INVISIBLE_BUTTON_OPACITY if invisible else 1.0
        current = float(self._effect.opacity())
        self._animation.stop()
        if not self._control.isVisible() or abs(current - target) < 0.001:
            self._effect.setOpacity(target)
            return
        self._animation.setStartValue(current)
        self._animation.setEndValue(target)
        self._animation.start()

    @QtCore.Slot(object)
    def _apply_opacity(self, value: object) -> None:
        if self._effect is not None:
            self._effect.setOpacity(float(value))


def set_control_invisible(
    control: QtWidgets.QWidget,
    invisible: bool,
    *,
    available: bool = True,
) -> None:
    """Fade a non-artwork control without giving it Broken artwork."""
    fader = getattr(control, "_lettersmith_invisible_fader", None)
    if not isinstance(fader, _InvisibleControlFader):
        fader = _InvisibleControlFader(control)
        setattr(control, "_lettersmith_invisible_fader", fader)
    fader.apply(available=available, invisible=invisible)


def resolve_application_root(explicit_root: str | Path | None = None) -> Path:
    return application_paths(explicit_root).resource_root


def button_art_path(project_root: str | Path, filename: str) -> Path:
    return application_paths(project_root).app_resource_path(
        Path("themes")
        / "cyber_forge"
        / "buttons"
        / Path(filename).name,
    )


class ArtworkButton(QtWidgets.QPushButton):
    """Draw button artwork behind bold centered text.

    Missing or unreadable artwork leaves the normal QPushButton renderer and
    caller-provided stylesheet untouched.
    """

    def __init__(
        self,
        text: str,
        project_root: str | Path,
        artwork_filename: str,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        broken_artwork_filename: str | None = None,
        long_form: bool = False,
        tier: ButtonTier | None = ButtonTier.STANDARD,
    ) -> None:
        super().__init__(text, parent)
        self._project_root = Path(project_root).resolve()
        self._artwork_filename = Path(artwork_filename).name
        self._broken_artwork_filename = (
            Path(broken_artwork_filename).name
            if broken_artwork_filename
            else None
        )
        self._presentation_artwork_filename: str | None = None
        self._presentation_theme_artwork_relative_path: Path | None = None
        self._presentation_artwork_is_broken = False
        self._theme_artwork_relative_path: Path | None = None
        self._long_form = bool(long_form)
        self._theme_service: Any = None
        self._theme_tokens: Any = None
        self._artwork_path = button_art_path(
            self._project_root,
            self._artwork_filename,
        )
        self._artwork = QtGui.QPixmap()
        self._displaying_broken_artwork = False
        self._artwork_fill = False
        self._artwork_stretch = False
        self._use_artwork = True
        self._text_word_wrap = False
        self._hovered = False
        self._requested_action_state = ButtonSemanticState.NORMAL
        self._visual_action_state = ButtonSemanticState.NORMAL
        self._requested_invisible = False
        self._preserve_visual_when_disabled = False
        self._updating_action_state = False
        self._visual_opacity = 0.94
        self._artwork_transition_progress = 1.0
        self._artwork_transition_from = QtGui.QPixmap()
        self._artwork_transition_to = QtGui.QPixmap()
        self._artwork_transition_from_path = self._artwork_path
        self._artwork_transition_to_path = self._artwork_path
        self._artwork_transition_from_state = self._visual_action_state
        self._artwork_transition_to_state = self._visual_action_state

        self._state_delay_timer = QtCore.QTimer(self)
        self._state_delay_timer.setSingleShot(True)
        self._state_delay_timer.setInterval(BUTTON_STATE_STABILIZATION_MS)
        self._state_delay_timer.timeout.connect(self._commit_requested_action_state)

        self._artwork_transition = QtCore.QVariantAnimation(self)
        self._artwork_transition.setDuration(BUTTON_STATE_FADE_MS)
        self._artwork_transition.setStartValue(0.0)
        self._artwork_transition.setEndValue(1.0)
        self._artwork_transition.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._artwork_transition.valueChanged.connect(
            self._artwork_transition_changed
        )
        self._artwork_transition.finished.connect(
            self._artwork_transition_finished
        )

        self._opacity_transition = QtCore.QVariantAnimation(self)
        self._opacity_transition.setDuration(BUTTON_STATE_FADE_MS)
        self._opacity_transition.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._opacity_transition.valueChanged.connect(
            self._opacity_transition_changed
        )
        self.setCursor(QtCore.Qt.PointingHandCursor)
        if tier is not None:
            apply_button_tier(self, tier)

        self.apply_theme_assets(self._discover_theme_service())

    def _discover_theme_service(self) -> Any:
        window = self.window()
        return getattr(window, "theme_service", None) if window is not None else None

    def apply_theme_assets(self, theme_service: Any = None) -> None:
        """Reload assigned cloud artwork from the active theme."""
        if theme_service is not None:
            self._theme_service = theme_service
        service = self._theme_service
        self._theme_tokens = getattr(service, "tokens", None)
        fallback_path = button_art_path(
            self._project_root,
            self._artwork_filename,
        )
        self._use_artwork = bool(
            self._theme_artwork_relative_path
            or self._presentation_theme_artwork_relative_path
            or getattr(getattr(service, "current", None), "uses_image_buttons", True)
        )
        if not self._use_artwork:
            self._artwork_path = fallback_path.resolve()
            self._artwork = QtGui.QPixmap()
            self.setAttribute(QtCore.Qt.WA_Hover, False)
            if self.styleSheet() == _ARTWORK_STYLESHEET:
                self.setStyleSheet("")
            self.setAccessibleName(self.text())
            self.updateGeometry()
            self.update()
            return

        self._cancel_artwork_transition()
        artwork_path, artwork, displaying_broken = self._resolved_artwork(
            self._visual_action_state
        )
        self._artwork_path = artwork_path
        self._displaying_broken_artwork = displaying_broken
        self._artwork = artwork

        if self.has_artwork:
            self.setAttribute(QtCore.Qt.WA_Hover, True)
            self.setStyleSheet(_ARTWORK_STYLESHEET)
            if not self.property(BUTTON_TIER_PROPERTY):
                self.setMinimumSize(118, 46)
                self.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Preferred,
                    QtWidgets.QSizePolicy.Policy.Fixed,
                )
            self.setAccessibleName(self.text())
        else:
            self.setAttribute(QtCore.Qt.WA_Hover, False)
            if self.styleSheet() == _ARTWORK_STYLESHEET:
                self.setStyleSheet("")
            self.setAccessibleName(self.text())
        self.updateGeometry()
        self.update()

    def set_unavailable(
        self,
        unavailable: bool,
        *,
        broken_artwork_filename: str | None = None,
    ) -> None:
        if broken_artwork_filename is not None:
            self._broken_artwork_filename = Path(broken_artwork_filename).name
        self.set_action_state(
            broken=unavailable,
            invisible=False,
        )

    def set_broken_artwork_filename(self, filename: str | None) -> None:
        self._broken_artwork_filename = Path(filename).name if filename else None
        self.apply_theme_assets()

    def set_theme_artwork_path(self, relative_path: str | Path | None) -> None:
        """Use one theme-relative image instead of the standard buttons folder."""
        if relative_path is None:
            requested = None
        else:
            requested = Path(str(relative_path).strip())
            if (
                not str(requested)
                or requested.is_absolute()
                or ".." in requested.parts
            ):
                raise ValueError("Theme artwork paths must be relative and contained.")
        self._theme_artwork_relative_path = requested
        self.apply_theme_assets()

    def set_presentation_artwork(
        self,
        filename: str | None,
        *,
        broken: bool = False,
        animate: bool = False,
        theme_relative: bool = False,
    ) -> None:
        """Select presentation artwork without changing button behavior."""
        requested_filename = (
            None
            if theme_relative
            else Path(filename).name
            if filename
            else None
        )
        requested_theme_artwork = (
            Path(str(filename).strip())
            if filename and theme_relative
            else None
        )
        if requested_theme_artwork is not None and (
            requested_theme_artwork.is_absolute()
            or ".." in requested_theme_artwork.parts
        ):
            raise ValueError("Theme artwork paths must be relative and contained.")
        requested_broken = bool(filename and broken and not theme_relative)
        if (
            requested_filename == self._presentation_artwork_filename
            and requested_theme_artwork
            == self._presentation_theme_artwork_relative_path
            and requested_broken == self._presentation_artwork_is_broken
        ):
            return
        if not animate or not self.isVisible() or not self._use_artwork:
            self._presentation_artwork_filename = requested_filename
            self._presentation_theme_artwork_relative_path = (
                requested_theme_artwork
            )
            self._presentation_artwork_is_broken = requested_broken
            self.apply_theme_assets()
            return

        self._settle_interrupted_artwork_transition()
        from_state = self._visual_action_state
        from_path = self._artwork_path
        from_artwork = self._artwork
        self._presentation_artwork_filename = requested_filename
        self._presentation_theme_artwork_relative_path = requested_theme_artwork
        self._presentation_artwork_is_broken = requested_broken
        target_path, target_artwork, displaying_broken = self._resolved_artwork(
            self._visual_action_state
        )
        self._artwork_path = target_path
        self._artwork = target_artwork
        self._displaying_broken_artwork = displaying_broken
        if (
            not from_artwork.isNull()
            and not target_artwork.isNull()
            and from_path != target_path
        ):
            self._artwork_transition_from = from_artwork
            self._artwork_transition_to = target_artwork
            self._artwork_transition_from_path = from_path
            self._artwork_transition_to_path = target_path
            self._artwork_transition_from_state = from_state
            self._artwork_transition_to_state = self._visual_action_state
            self._artwork_transition_progress = 0.0
            self._artwork_transition.start()
        else:
            self._cancel_artwork_transition()
        self.updateGeometry()
        self.update()

    def set_preserve_visual_when_disabled(self, enabled: bool) -> None:
        """Keep the current artwork opaque when disabled for a special state."""
        self._preserve_visual_when_disabled = bool(enabled)

    def set_action_state(
        self,
        *,
        broken: bool,
        invisible: bool,
    ) -> None:
        """Apply a logical artwork state and an independent invisible layer."""
        target = (
            ButtonSemanticState.BROKEN
            if broken
            else ButtonSemanticState.NORMAL
        )
        was_invisible = self._requested_invisible
        self._requested_invisible = bool(invisible)

        self._updating_action_state = True
        try:
            self.setEnabled(
                target == ButtonSemanticState.NORMAL
                and not self._requested_invisible
            )
        finally:
            self._updating_action_state = False
        self._request_action_state(
            target,
            was_invisible=was_invisible,
        )
        self._animate_visual_opacity()

    @property
    def semantic_state(self) -> ButtonSemanticState:
        return self._requested_action_state

    @property
    def visual_semantic_state(self) -> ButtonSemanticState:
        return self._visual_action_state

    @property
    def is_invisible(self) -> bool:
        return self._requested_invisible

    def _request_action_state(
        self,
        target: ButtonSemanticState,
        *,
        was_invisible: bool | None = None,
    ) -> None:
        if was_invisible is None:
            was_invisible = self._requested_invisible
        if self._requested_invisible:
            if target != self._requested_action_state:
                self._settle_interrupted_artwork_transition()
            self._requested_action_state = target
            self._state_delay_timer.stop()
            return
        if target == self._requested_action_state:
            if (
                self._state_delay_timer.isActive()
                or self._artwork_transition.state()
                == QtCore.QAbstractAnimation.State.Running
            ):
                return
            if target == self._visual_action_state:
                return
        semantic_changed = target != self._requested_action_state
        if semantic_changed:
            self._settle_interrupted_artwork_transition()
        self._requested_action_state = target
        if semantic_changed or self._requested_invisible:
            self._state_delay_timer.stop()
        if self._requested_invisible:
            return
        if target == self._visual_action_state:
            return
        if was_invisible or not self.isVisible():
            self._commit_requested_action_state()
            return
        self._state_delay_timer.start()

    @QtCore.Slot()
    def _commit_requested_action_state(self) -> None:
        target = self._requested_action_state
        if target == self._visual_action_state:
            self._animate_visual_opacity()
            return

        from_state = self._visual_action_state
        from_path = self._artwork_path
        from_artwork = self._artwork
        target_path, target_artwork, displaying_broken = self._resolved_artwork(
            target
        )
        self._visual_action_state = target
        self._artwork_path = target_path
        self._artwork = target_artwork
        self._displaying_broken_artwork = displaying_broken

        if (
            self._use_artwork
            and not from_artwork.isNull()
            and not target_artwork.isNull()
            and from_path != target_path
        ):
            self._artwork_transition_from = from_artwork
            self._artwork_transition_to = target_artwork
            self._artwork_transition_from_path = from_path
            self._artwork_transition_to_path = target_path
            self._artwork_transition_from_state = from_state
            self._artwork_transition_to_state = target
            self._artwork_transition_progress = 0.0
            self._artwork_transition.stop()
            self._artwork_transition.start()
        else:
            self._cancel_artwork_transition()
        self._animate_visual_opacity()
        self.updateGeometry()
        self.update()

    def _resolved_artwork(
        self,
        state: ButtonSemanticState,
    ) -> tuple[Path, QtGui.QPixmap, bool]:
        presentation_filename = self._presentation_artwork_filename
        presentation_theme_artwork = (
            self._presentation_theme_artwork_relative_path
        )
        semantic_broken = bool(
            state == ButtonSemanticState.BROKEN
            and self._broken_artwork_filename
        )
        custom_theme_artwork = (
            presentation_theme_artwork
            if not semantic_broken and presentation_theme_artwork is not None
            else self._theme_artwork_relative_path
            if (
                not semantic_broken
                and not presentation_filename
                and presentation_theme_artwork is None
            )
            else None
        )
        fallback_path = (
            application_paths(self._project_root).app_resource_path(
                Path("themes") / "cyber_forge" / custom_theme_artwork
            )
            if custom_theme_artwork is not None
            else button_art_path(
                self._project_root,
                self._artwork_filename,
            )
        )
        if semantic_broken:
            requested_filename = self._broken_artwork_filename or self._artwork_filename
            wants_broken = True
        elif presentation_filename:
            requested_filename = presentation_filename
            wants_broken = self._presentation_artwork_is_broken
        else:
            requested_filename = self._artwork_filename
            wants_broken = False

        artwork_path = fallback_path
        service = self._theme_service
        if (
            custom_theme_artwork is not None
            and service is not None
            and hasattr(service, "resolve_asset")
        ):
            try:
                artwork_path = service.resolve_asset(custom_theme_artwork)
            except (TypeError, ValueError):
                artwork_path = fallback_path
        elif service is not None and hasattr(service, "resolve_button_asset"):
            try:
                artwork_path = service.resolve_button_asset(
                    requested_filename,
                    broken=wants_broken,
                    long_form=self._long_form,
                    allow_baseline=False,
                )
            except (TypeError, ValueError):
                artwork_path = fallback_path
        elif wants_broken:
            artwork_path = fallback_path.parent
            if self._long_form:
                artwork_path /= "Long"
            artwork_path = artwork_path / "broken" / requested_filename

        resolved_broken = bool(wants_broken and Path(artwork_path).is_file())
        if wants_broken and not resolved_broken:
            if service is not None and hasattr(service, "resolve_button_asset"):
                try:
                    artwork_path = service.resolve_button_asset(
                        self._artwork_filename,
                        long_form=self._long_form,
                        allow_baseline=False,
                    )
                except (TypeError, ValueError):
                    artwork_path = fallback_path
            else:
                artwork_path = fallback_path
        path = Path(artwork_path).resolve()
        return path, QtGui.QPixmap(str(path)), semantic_broken and resolved_broken

    def _target_visual_opacity(self) -> float:
        if self._requested_invisible:
            return INVISIBLE_BUTTON_OPACITY
        if self._visual_action_state == ButtonSemanticState.BROKEN:
            return 1.0
        return 0.94

    def _animate_visual_opacity(self) -> None:
        target = self._target_visual_opacity()
        if abs(self._visual_opacity - target) < 0.001:
            self._visual_opacity = target
            self.update()
            return
        self._opacity_transition.stop()
        self._opacity_transition.setStartValue(self._visual_opacity)
        self._opacity_transition.setEndValue(target)
        self._opacity_transition.start()

    @QtCore.Slot(object)
    def _opacity_transition_changed(self, value: object) -> None:
        self._visual_opacity = float(value)
        self.update()

    @QtCore.Slot(object)
    def _artwork_transition_changed(self, value: object) -> None:
        self._artwork_transition_progress = float(value)
        self.update()

    @QtCore.Slot()
    def _artwork_transition_finished(self) -> None:
        self._artwork_transition_progress = 1.0
        self._artwork_transition_from = QtGui.QPixmap()
        self._artwork_transition_to = QtGui.QPixmap()
        self.update()

    def _cancel_artwork_transition(self) -> None:
        self._artwork_transition.stop()
        self._artwork_transition_progress = 1.0
        self._artwork_transition_from = QtGui.QPixmap()
        self._artwork_transition_to = QtGui.QPixmap()

    def _settle_interrupted_artwork_transition(self) -> None:
        if (
            self._artwork_transition.state()
            != QtCore.QAbstractAnimation.State.Running
        ):
            return
        use_target = self._artwork_transition_progress >= 0.5
        if use_target:
            self._artwork = self._artwork_transition_to
            self._artwork_path = self._artwork_transition_to_path
            self._visual_action_state = self._artwork_transition_to_state
        else:
            self._artwork = self._artwork_transition_from
            self._artwork_path = self._artwork_transition_from_path
            self._visual_action_state = self._artwork_transition_from_state
        self._displaying_broken_artwork = (
            self._visual_action_state == ButtonSemanticState.BROKEN
            and "broken" in {part.casefold() for part in self._artwork_path.parts}
        )
        self._cancel_artwork_transition()
        self.update()

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() != QtCore.QEvent.Type.EnabledChange:
            return
        self.setCursor(
            QtCore.Qt.PointingHandCursor
            if self.isEnabled()
            else QtCore.Qt.ArrowCursor
        )
        if (
            hasattr(self, "_artwork_filename")
            and not self._updating_action_state
            and not self._preserve_visual_when_disabled
        ):
            inherited_disabled = (
                not self.isEnabled()
                and not self.testAttribute(QtCore.Qt.WA_ForceDisabled)
            )
            if inherited_disabled:
                return
            self.set_action_state(
                broken=(
                    self._requested_action_state
                    == ButtonSemanticState.BROKEN
                ),
                invisible=not self.isEnabled(),
            )

    @property
    def has_artwork(self) -> bool:
        return self._use_artwork and not self._artwork.isNull()

    @property
    def uses_artwork_presentation(self) -> bool:
        return self._use_artwork

    @property
    def artwork_path(self) -> Path:
        return self._artwork_path

    def set_artwork_fill(self, enabled: bool) -> None:
        """Opt into aspect-preserving cover scaling for padded artwork."""
        self._artwork_fill = bool(enabled)
        self.update()

    def set_artwork_stretch(self, enabled: bool) -> None:
        """Fit the complete artwork canvas without cropping its edges."""
        self._artwork_stretch = bool(enabled)
        self.update()

    def set_text_word_wrap(self, enabled: bool) -> None:
        self._text_word_wrap = bool(enabled)
        self.update()

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        if not self.has_artwork:
            return super().sizeHint()
        width = max(118, min(190, self._artwork.width()))
        height = max(46, min(72, self._artwork.height()))
        return QtCore.QSize(width, height)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def _artwork_label_color(self) -> QtGui.QColor:
        enabled_presentation = not self._requested_invisible
        return QtGui.QColor(
            str(
                getattr(
                    self._theme_tokens,
                    "artwork_button_text"
                    if enabled_presentation
                    else "artwork_button_disabled_text",
                    "#f0fcff" if enabled_presentation else "#aab4bc",
                )
            )
        )

    def _artwork_shadow_color(self) -> QtGui.QColor:
        color = QtGui.QColor(
            str(
                getattr(
                    self._theme_tokens,
                    "artwork_button_shadow",
                    "#000000",
                )
            )
        )
        color.setAlpha(190)
        return color

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        if not self.has_artwork:
            super().paintEvent(event)
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        content = self.rect().adjusted(2, 2, -2, -2)
        if self.isDown():
            content.translate(0, 2)

        aspect_mode = (
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio
            if self._artwork_stretch
            else (
                QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding
                if self._artwork_fill
                else QtCore.Qt.AspectRatioMode.KeepAspectRatio
            )
        )

        def scaled_geometry(
            pixmap: QtGui.QPixmap,
        ) -> tuple[QtGui.QPixmap, QtCore.QRect]:
            scaled_pixmap = pixmap.scaled(
                content.size(),
                aspect_mode,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            return scaled_pixmap, QtCore.QRect(
                content.center().x() - scaled_pixmap.width() // 2,
                content.center().y() - scaled_pixmap.height() // 2,
                scaled_pixmap.width(),
                scaled_pixmap.height(),
            )

        scaled, pixmap_target = scaled_geometry(self._artwork)
        target = (
            content
            if self._artwork_fill or self._artwork_stretch
            else pixmap_target
        )

        visual_opacity = self._visual_opacity
        if (
            self._visual_action_state == ButtonSemanticState.NORMAL
            and not self._requested_invisible
            and self._hovered
            and self._opacity_transition.state()
            != QtCore.QAbstractAnimation.State.Running
        ):
            visual_opacity = 1.0
        if self.isDown():
            visual_opacity *= 0.82

        def draw_artwork(pixmap: QtGui.QPixmap, opacity: float) -> None:
            if pixmap.isNull() or opacity <= 0.0:
                return
            layer, layer_target = scaled_geometry(pixmap)
            painter.setOpacity(max(0.0, min(1.0, opacity)))
            if self._artwork_fill:
                painter.save()
                painter.setClipRect(content)
                painter.drawPixmap(layer_target, layer)
                painter.restore()
            else:
                painter.drawPixmap(layer_target, layer)

        if (
            not self._artwork_transition_from.isNull()
            and not self._artwork_transition_to.isNull()
        ):
            progress = self._artwork_transition_progress
            draw_artwork(
                self._artwork_transition_from,
                visual_opacity * (1.0 - progress),
            )
            draw_artwork(
                self._artwork_transition_to,
                visual_opacity * progress,
            )
        else:
            draw_artwork(self._artwork, visual_opacity)
        painter.setOpacity(1.0)

        # A restrained shadow keeps labels readable across light and dark art.
        horizontal_text_inset = (
            3
            if self.property(BUTTON_TIER_PROPERTY) == ButtonTier.SMALL.value
            else 8
        )
        text_rect = target.adjusted(
            horizontal_text_inset,
            4,
            -horizontal_text_inset,
            -4,
        )
        text_flags = QtCore.Qt.AlignCenter
        if self._text_word_wrap:
            text_flags |= QtCore.Qt.TextWordWrap
        painter.setFont(self.font())
        tokens = self._theme_tokens
        painter.setPen(self._artwork_shadow_color())
        painter.drawText(text_rect.translated(0, 1), text_flags, self.text())
        painter.setPen(self._artwork_label_color())
        painter.drawText(text_rect, text_flags, self.text())

        if self.hasFocus():
            focus_color = QtGui.QColor(
                str(getattr(tokens, "accent", "#7f9099"))
            )
            focus_color.setAlpha(230)
            focus_pen = QtGui.QPen(focus_color, 2)
            focus_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(focus_pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(target.adjusted(2, 2, -2, -2), 8, 8)


__all__ = [
    "ArtworkButton",
    "BUTTON_STATE_FADE_MS",
    "BUTTON_STATE_STABILIZATION_MS",
    "ButtonSemanticState",
    "INVISIBLE_BUTTON_OPACITY",
    "button_art_path",
    "resolve_application_root",
    "set_control_invisible",
]
