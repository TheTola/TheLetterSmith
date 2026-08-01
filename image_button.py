from __future__ import annotations

"""Reusable artwork-backed QPushButton with a safe text-button fallback."""

import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets


def resolve_application_root(explicit_root: str | Path | None = None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).resolve()
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def button_art_path(project_root: str | Path, filename: str) -> Path:
    return (
        resolve_application_root(project_root)
        / "gallery"
        / "app"
        / "icons"
        / "buttons"
        / Path(filename).name
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
        self._artwork_path = button_art_path(project_root, artwork_filename)
        self._artwork = QtGui.QPixmap(str(self._artwork_path))
        self._artwork_fill = False
        self._artwork_stretch = False
        self._hovered = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        font = self.font()
        font.setBold(True)
        self.setFont(font)

        if self.has_artwork:
            self.setAttribute(QtCore.Qt.WA_Hover, True)
            self.setStyleSheet("QPushButton{background:transparent;border:none;padding:0;}")
            self.setMinimumSize(118, 46)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Preferred,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            self.setAccessibleName(text)

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
        painter.setFont(self.font())
        painter.setPen(QtGui.QColor(0, 0, 0, 190))
        painter.drawText(text_rect.translated(0, 1), QtCore.Qt.AlignCenter, self.text())
        painter.setPen(QtGui.QColor(255, 255, 255, 240 if self.isEnabled() else 145))
        painter.drawText(text_rect, QtCore.Qt.AlignCenter, self.text())

        if self.hasFocus():
            focus_pen = QtGui.QPen(QtGui.QColor(0, 229, 255, 230), 2)
            focus_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(focus_pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(target.adjusted(2, 2, -2, -2), 8, 8)


__all__ = ["ArtworkButton", "button_art_path", "resolve_application_root"]
