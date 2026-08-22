from __future__ import annotations

"""Reusable artwork-backed QPushButton with a safe text-button fallback."""

from pathlib import Path
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from project_paths import application_paths


def resolve_application_root(explicit_root: str | Path | None = None) -> Path:
    return application_paths(explicit_root).resource_root


def button_art_path(project_root: str | Path, filename: str) -> Path:
    return application_paths(project_root).app_resource_path(
        Path("icons") / "buttons" / Path(filename).name,
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
    ) -> None:
        super().__init__(text, parent)
        self._project_root = Path(project_root).resolve()
        self._artwork_filename = Path(artwork_filename).name
        self._theme_service: Any = None
        self._artwork_path = button_art_path(
            self._project_root,
            self._artwork_filename,
        )
        self._artwork = QtGui.QPixmap()
        self._artwork_fill = False
        self._artwork_stretch = False
        self._text_word_wrap = False
        self._hovered = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        font = self.font()
        font.setBold(True)
        self.setFont(font)

        self.apply_theme_assets(self._discover_theme_service())

    def _discover_theme_service(self) -> Any:
        window = self.window()
        return getattr(window, "theme_service", None) if window is not None else None

    def apply_theme_assets(self, theme_service: Any = None) -> None:
        """Reload artwork from the active theme, with the legacy asset fallback."""
        if theme_service is not None:
            self._theme_service = theme_service
        service = self._theme_service
        fallback_path = button_art_path(
            self._project_root,
            self._artwork_filename,
        )
        artwork_path = fallback_path
        if service is not None and hasattr(service, "resolve_asset"):
            resource_root = application_paths(self._project_root).resource_root
            try:
                fallback = fallback_path.resolve().relative_to(resource_root).as_posix()
                artwork_path = service.resolve_asset(
                    f"buttons/{self._artwork_filename}",
                    fallback=fallback,
                )
            except (TypeError, ValueError):
                artwork_path = fallback_path

        self._artwork_path = Path(artwork_path).resolve()
        self._artwork = QtGui.QPixmap(str(self._artwork_path))

        if self.has_artwork:
            self.setAttribute(QtCore.Qt.WA_Hover, True)
            self.setStyleSheet("QPushButton{background:transparent;border:none;padding:0;}")
            self.setMinimumSize(118, 46)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Preferred,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            self.setAccessibleName(self.text())
        self.updateGeometry()
        self.update()

    @property
    def has_artwork(self) -> bool:
        return not self._artwork.isNull()

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
        scaled = self._artwork.scaled(
            content.size(),
            aspect_mode,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        pixmap_target = QtCore.QRect(
            content.center().x() - scaled.width() // 2,
            content.center().y() - scaled.height() // 2,
            scaled.width(),
            scaled.height(),
        )
        target = (
            content
            if self._artwork_fill or self._artwork_stretch
            else pixmap_target
        )

        painter.setOpacity(0.72 if not self.isEnabled() else (1.0 if self._hovered else 0.94))
        if self._artwork_fill:
            painter.save()
            painter.setClipRect(content)
            painter.drawPixmap(pixmap_target, scaled)
            painter.restore()
        else:
            painter.drawPixmap(pixmap_target, scaled)
        painter.setOpacity(1.0)

        # A restrained shadow keeps labels readable across light and dark art.
        text_rect = target.adjusted(8, 4, -8, -4)
        text_flags = QtCore.Qt.AlignCenter
        if self._text_word_wrap:
            text_flags |= QtCore.Qt.TextWordWrap
        painter.setFont(self.font())
        painter.setPen(QtGui.QColor(0, 0, 0, 190))
        painter.drawText(text_rect.translated(0, 1), text_flags, self.text())
        painter.setPen(QtGui.QColor(255, 255, 255, 240 if self.isEnabled() else 145))
        painter.drawText(text_rect, text_flags, self.text())

        if self.hasFocus():
            focus_pen = QtGui.QPen(QtGui.QColor(0, 229, 255, 230), 2)
            focus_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(focus_pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(target.adjusted(2, 2, -2, -2), 8, 8)


__all__ = ["ArtworkButton", "button_art_path", "resolve_application_root"]
