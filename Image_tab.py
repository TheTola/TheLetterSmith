from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon

from transactional_io import atomic_write_bytes, atomic_write_json
from ui_status import StatusBanner, StatusController, StatusLevel


class ImageState(Enum):
    READY = "ready"
    MISSING = "missing"
    WARNING = "warning"


@dataclass(frozen=True)
class ImageAssessment:
    state: ImageState
    reason: str
    width: int = 0
    height: int = 0


RECOMMENDED_WIDTH = 1200
RECOMMENDED_HEIGHT = 1800
TARGET_ASPECT_RATIO = 2 / 3
EXTREME_ASPECT_MIN = 0.35
EXTREME_ASPECT_MAX = 1.8
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
RING_COLORS = {
    ImageState.READY: "#75b88a",
    ImageState.MISSING: "#c75858",
    ImageState.WARNING: "#d5ad48",
}


def _load_image_exif(path: str | Path) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    return image


def _png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    mode = "RGBA" if image.mode == "RGBA" else "RGB"
    image.convert(mode).save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def assess_image(
    canonical_path: str | Path,
    crop_metadata: Optional[dict] = None,
) -> ImageAssessment:
    path = Path(canonical_path)
    if not path.is_file():
        return ImageAssessment(ImageState.MISSING, "The image has not been selected.")
    try:
        if path.stat().st_size <= 0:
            return ImageAssessment(ImageState.MISSING, "The image file is empty.")
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError, SyntaxError) as exc:
        return ImageAssessment(ImageState.MISSING, f"The image cannot be read: {exc}")

    if width <= 0 or height <= 0:
        return ImageAssessment(ImageState.MISSING, "The image dimensions are invalid.")

    metadata = crop_metadata if isinstance(crop_metadata, dict) else {}
    source_value = str(metadata.get("source", "")).strip()
    if source_value:
        source = Path(source_value)
        if not source.is_absolute():
            source = path.parent / source
        if not source.is_file():
            return ImageAssessment(
                ImageState.WARNING,
                "Crop information references an original image that is missing.",
                width,
                height,
            )

    if width < RECOMMENDED_WIDTH or height < RECOMMENDED_HEIGHT:
        return ImageAssessment(
            ImageState.WARNING,
            (
                f"Resolution is {width} × {height}; "
                f"{RECOMMENDED_WIDTH} × {RECOMMENDED_HEIGHT} or larger is recommended."
            ),
            width,
            height,
        )

    aspect = width / height
    if not metadata and (aspect < EXTREME_ASPECT_MIN or aspect > EXTREME_ASPECT_MAX):
        return ImageAssessment(
            ImageState.WARNING,
            "The image has an unusually extreme aspect ratio and has not been cropped.",
            width,
            height,
        )
    return ImageAssessment(ImageState.READY, "", width, height)


def calculate_crop_box(
    width: int,
    height: int,
    *,
    zoom: float = 1.0,
    center_x: float = 0.5,
    center_y: float = 0.5,
) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Crop source dimensions must be positive.")
    zoom = max(1.0, min(3.0, float(zoom)))
    source_aspect = width / height
    if source_aspect > TARGET_ASPECT_RATIO:
        base_height = float(height)
        base_width = base_height * TARGET_ASPECT_RATIO
    else:
        base_width = float(width)
        base_height = base_width / TARGET_ASPECT_RATIO

    crop_width = max(1.0, base_width / zoom)
    crop_height = max(1.0, base_height / zoom)
    half_width = crop_width / 2
    half_height = crop_height / 2
    center_px_x = max(half_width, min(width - half_width, float(center_x) * width))
    center_px_y = max(half_height, min(height - half_height, float(center_y) * height))

    left = int(round(center_px_x - half_width))
    top = int(round(center_px_y - half_height))
    right = int(round(center_px_x + half_width))
    bottom = int(round(center_px_y + half_height))
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return left, top, right, bottom


class ImageArea(QtWidgets.QLabel):
    clicked = Signal()
    file_dropped = Signal(str)
    hovered = Signal()

    def __init__(self, title: str) -> None:
        super().__init__()
        self._source_pixmap = QtGui.QPixmap()
        self.image_state = ImageState.MISSING
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(150, 225)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.setAcceptDrops(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Select {title}")
        self.setText("＋")
        self.setToolTip("The image has not been selected. Click or drop an image here.")
        self._apply_ring()

    def set_image(self, path: Path, assessment: ImageAssessment) -> None:
        pixmap = QtGui.QPixmap(str(path)) if path.is_file() else QtGui.QPixmap()
        self._source_pixmap = pixmap
        self.image_state = assessment.state
        self.setToolTip(
            assessment.reason
            or "Click or drop an image here to replace the current image."
        )
        self._render_pixmap()
        self._apply_ring()

    def _render_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            self.setPixmap(QtGui.QPixmap())
            self.setText("＋")
            return
        self.setText("")
        available = self.size() - QSize(16, 16)
        self.setPixmap(
            self._source_pixmap.scaled(
                available,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _apply_ring(self, *, drop_active: bool = False) -> None:
        color = "#8ce7ee" if drop_active else RING_COLORS[self.image_state]
        self.setStyleSheet(
            "QLabel {"
            f"border: 4px solid {color};"
            "border-radius: 18px;"
            "background: rgba(20, 24, 29, 0.78);"
            "color: #77828d;"
            "font: 42px 'Segoe UI Light';"
            "padding: 4px;"
            "}"
            f"QLabel:focus {{ border-color: #b9f4f6; outline: none; }}"
        )

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
            QtCore.Qt.Key.Key_Space,
        ):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.hovered.emit()
        super().enterEvent(event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if self._first_supported_path(event.mimeData()) is not None:
            self._apply_ring(drop_active=True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._apply_ring()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._apply_ring()
        path = self._first_supported_path(event.mimeData())
        if path is None:
            event.ignore()
            return
        self.file_dropped.emit(str(path))
        event.acceptProposedAction()

    @staticmethod
    def _first_supported_path(mime_data: QtCore.QMimeData) -> Optional[Path]:
        if not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                return path
        return None

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_pixmap()


class ImageCard(QtWidgets.QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("imageCard")
        self.setStyleSheet(
            "QFrame#imageCard {"
            "background: rgba(31, 35, 40, 0.72);"
            "border: 1px solid rgba(100, 155, 165, 0.25);"
            "border-radius: 12px;"
            "}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title_label = QtWidgets.QLabel(title)
        title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("color:#cce7ea; font:600 11px 'Segoe UI'; border:none;")
        layout.addWidget(title_label)

        self.image_area = ImageArea(title)
        layout.addWidget(self.image_area, 1)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.crop_button = QtWidgets.QPushButton("Crop")
        self.clear_button = QtWidgets.QPushButton("Clear")
        for button in (self.crop_button, self.clear_button):
            button.setMinimumHeight(28)
            button.setStyleSheet(
                "QPushButton {"
                "background:#22282d; color:#dce9ea; border:1px solid #52646a;"
                "border-radius:5px; padding:4px 12px;"
                "}"
                "QPushButton:hover { border-color:#7fc7cd; color:white; }"
                "QPushButton:disabled { color:#687378; border-color:#343c40; }"
            )
            controls.addWidget(button)
        layout.addLayout(controls)

    def emphasize(self) -> None:
        effect = QtWidgets.QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(28)
        effect.setOffset(0, 0)
        effect.setColor(QtGui.QColor("#8ce7ee"))
        self.setGraphicsEffect(effect)
        QtCore.QTimer.singleShot(900, lambda: self.setGraphicsEffect(None))
        self.image_area.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)


class CropCanvas(QtWidgets.QWidget):
    position_changed = Signal(float, float)

    def __init__(
        self,
        source: Path,
        *,
        zoom: float,
        center_x: float,
        center_y: float,
    ) -> None:
        super().__init__()
        self._pixmap = QtGui.QPixmap(str(source))
        if self._pixmap.isNull():
            raise ValueError("The original image cannot be displayed.")
        self.zoom = zoom
        self.center_x = center_x
        self.center_y = center_y
        self._last_position: Optional[QtCore.QPointF] = None
        self.setFixedSize(360, 540)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(360, 540)

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(1.0, min(3.0, zoom))
        self._constrain_center()
        self.update()

    def reset_crop(self) -> None:
        self.zoom = 1.0
        self.center_x = 0.5
        self.center_y = 0.5
        self.position_changed.emit(self.center_x, self.center_y)
        self.update()

    def crop_box(self) -> tuple[int, int, int, int]:
        return calculate_crop_box(
            self._pixmap.width(),
            self._pixmap.height(),
            zoom=self.zoom,
            center_x=self.center_x,
            center_y=self.center_y,
        )

    def _constrain_center(self) -> None:
        left, top, right, bottom = self.crop_box()
        crop_width = right - left
        crop_height = bottom - top
        half_x = crop_width / (2 * self._pixmap.width())
        half_y = crop_height / (2 * self._pixmap.height())
        self.center_x = max(half_x, min(1.0 - half_x, self.center_x))
        self.center_y = max(half_y, min(1.0 - half_y, self.center_y))
        self.position_changed.emit(self.center_x, self.center_y)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QtGui.QColor("#101316"))
        left, top, right, bottom = self.crop_box()
        source = QtCore.QRectF(left, top, right - left, bottom - top)
        painter.drawPixmap(QtCore.QRectF(self.rect()), self._pixmap, source)
        painter.setPen(QtGui.QPen(QtGui.QColor("#8ce7ee"), 2))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 10, 10)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._last_position = event.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._last_position is None:
            return
        delta = event.position() - self._last_position
        self._last_position = event.position()
        left, top, right, bottom = self.crop_box()
        crop_fraction_x = (right - left) / self._pixmap.width()
        crop_fraction_y = (bottom - top) / self._pixmap.height()
        self.center_x -= (delta.x() / max(1, self.width())) * crop_fraction_x
        self.center_y -= (delta.y() / max(1, self.height())) * crop_fraction_y
        self._constrain_center()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._last_position = None
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class CropDialog(QtWidgets.QDialog):
    def __init__(
        self,
        source: Path,
        *,
        zoom: float = 1.0,
        center_x: float = 0.5,
        center_y: float = 0.5,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crop image")
        self.setModal(True)
        layout = QtWidgets.QVBoxLayout(self)
        self.canvas = CropCanvas(
            source,
            zoom=zoom,
            center_x=center_x,
            center_y=center_y,
        )
        layout.addWidget(self.canvas, 1)

        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addWidget(QtWidgets.QLabel("Zoom"))
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 300)
        self.zoom_slider.setValue(round(zoom * 100))
        self.zoom_slider.valueChanged.connect(
            lambda value: self.canvas.set_zoom(value / 100)
        )
        zoom_row.addWidget(self.zoom_slider, 1)
        layout.addLayout(zoom_row)

        button_row = QtWidgets.QHBoxLayout()
        reset_button = QtWidgets.QPushButton("Reset crop")
        reset_button.clicked.connect(self._reset)
        button_row.addWidget(reset_button)
        button_row.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

    def _reset(self) -> None:
        self.canvas.reset_crop()
        self.zoom_slider.setValue(100)

    def values(self) -> tuple[float, float, float]:
        return self.canvas.zoom, self.canvas.center_x, self.canvas.center_y


class StaticFab(QtWidgets.QToolButton):
    def __init__(self, parent_widget: QtWidgets.QWidget, surface: QtWidgets.QWidget):
        super().__init__(parent_widget)
        self._surface = surface
        self.setObjectName("PWriteFab")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setFixedSize(200, 200)
        self.setStyleSheet("#PWriteFab{background:transparent; border:none; padding:0;}")

    def set_surface(self, surface: QtWidgets.QWidget) -> None:
        self._surface = surface

    def clamp_to_surface(self) -> None:
        if self._surface is None:
            return
        self.move(
            max(0, min(self.x(), self._surface.width() - self.width())),
            max(0, min(self.y(), self._surface.height() - self.height())),
        )


class ImageTab(QtWidgets.QWidget):
    image_selected = Signal(QtGui.QPixmap)
    hover_preview_image = Signal(QtGui.QPixmap)
    clear_preview = Signal()

    SLOT_MAP = {
        1: ("Cover Page Image", "cover.png"),
        2: ("Main Letter Image", "letter.png"),
        3: ("Letter Background Image", "wall.png"),
        4: ("Final Backdrop Image", "back.png"),
    }
    FAB_FIXED_X = 55
    FAB_FIXED_Y_PAD = 28

    def __init__(self, project_root: str | Path | None = None) -> None:
        super().__init__()
        self.project_root = Path(project_root or Path(__file__).resolve().parent).resolve()
        self.labels = dict(self.SLOT_MAP)
        self.image_paths: dict[int, Optional[str]] = {index: None for index in self.labels}
        self.cards: dict[int, ImageCard] = {}
        self.buttons: dict[int, ImageArea] = {}
        self.status_controller = StatusController()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QtWidgets.QLabel("Select images for your letter")
        header.setFont(QtGui.QFont("Segoe UI Semibold", 16))
        header.setStyleSheet("color:#00d0ff;")
        header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, (title, _filename) in self.labels.items():
            card = ImageCard(title)
            card.image_area.clicked.connect(
                lambda index=index: self._pick_image_dialog(index)
            )
            card.image_area.file_dropped.connect(
                lambda path, index=index: self._set_image_from_drop(path, index)
            )
            card.image_area.hovered.connect(
                lambda index=index: self.preview_from_gallery(index)
            )
            card.crop_button.clicked.connect(
                lambda _checked=False, index=index: self.open_crop_dialog(index)
            )
            card.clear_button.clicked.connect(
                lambda _checked=False, index=index: self.clear_image(index)
            )
            self.cards[index] = card
            self.buttons[index] = card.image_area
            grid.addWidget(card, (index - 1) // 2, (index - 1) % 2)
        layout.addLayout(grid, 1)

        utility_row = QtWidgets.QHBoxLayout()
        self.reset_btn = QtWidgets.QPushButton("Reset Images")
        self.reset_btn.clicked.connect(self.reset_images)
        utility_row.addWidget(self.reset_btn)
        self.open_btn = QtWidgets.QPushButton("Gallery")
        self.open_btn.clicked.connect(self.open_gallery_folder)
        utility_row.addWidget(self.open_btn)
        utility_row.addStretch(1)
        layout.addLayout(utility_row)

        self.status_banner = StatusBanner(controller=self.status_controller)
        self.status = self.status_banner._label
        layout.addWidget(self.status_banner)

        self._fab_surface: QtWidgets.QWidget = self
        self.pwrite_fab = StaticFab(self, self)
        icon_path = self.project_root / "gallery/app/icons/Pwrite.png"
        if icon_path.is_file():
            self.pwrite_fab.setIcon(QIcon(str(icon_path)))
            self.pwrite_fab.setIconSize(QSize(200, 200))
        else:
            self.pwrite_fab.setText("PROMPT\nWRITER")
        self.pwrite_fab.clicked.connect(self._open_prompt_writer_bridge)
        self.pwrite_fab.show()
        self.pwrite_fab.raise_()

        self.refresh_from_workspace()

    def _project_dir(self) -> str:
        return str(self.project_root)

    def _user_pages_dir(self) -> str:
        return str(self.project_root / "gallery/user/pages")

    @property
    def pages_dir(self) -> Path:
        return self.project_root / "gallery/user/pages"

    @property
    def originals_dir(self) -> Path:
        return self.pages_dir / "originals"

    @property
    def crops_path(self) -> Path:
        return self.pages_dir / "crops.json"

    def canonical_path(self, index: int) -> Path:
        return self.pages_dir / self.labels[index][1]

    def original_path(self, index: int) -> Path:
        return self.originals_dir / self.labels[index][1]

    def _load_crop_metadata(self) -> dict:
        try:
            data = __import__("json").loads(self.crops_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeError, ValueError):
            return {}

    def _write_crop_metadata(self, metadata: dict) -> None:
        atomic_write_json(self.crops_path, metadata)

    def _assessment(self, index: int) -> ImageAssessment:
        metadata = self._load_crop_metadata().get(self.labels[index][1])
        return assess_image(
            self.canonical_path(index),
            metadata if isinstance(metadata, dict) else None,
        )

    def _refresh_card(self, index: int) -> ImageAssessment:
        assessment = self._assessment(index)
        path = self.canonical_path(index)
        self.image_paths[index] = str(path) if path.is_file() else None
        card = self.cards[index]
        card.image_area.set_image(path, assessment)
        card.crop_button.setEnabled(
            path.is_file() and assessment.state is not ImageState.MISSING
        )
        metadata = self._load_crop_metadata()
        card.clear_button.setEnabled(
            path.exists()
            or self.original_path(index).exists()
            or self.labels[index][1] in metadata
        )
        return assessment

    def _pick_image_dialog(self, index: int) -> None:
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Select {self.labels[index][0]}",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self.set_image_path(index, path)

    def _set_image_from_drop(self, path: str, index: int) -> None:
        if Path(path).is_file():
            self.set_image_path(index, path)

    def set_image_path(self, index: int, source_path: str) -> None:
        if index not in self.labels:
            raise KeyError(f"Unknown image slot: {index}")
        filename = self.labels[index][1]
        try:
            image = _load_image_exif(source_path)
            payload = _png_bytes(image)
            atomic_write_bytes(self.original_path(index), payload)
            atomic_write_bytes(self.canonical_path(index), payload)
            metadata = self._load_crop_metadata()
            if filename in metadata:
                metadata.pop(filename, None)
                self._write_crop_metadata(metadata)
        except Exception as exc:
            self.status_controller.publish(
                f"Could not import {filename}: {exc}",
                StatusLevel.ERROR,
                persistent=True,
                key=f"image-{index}",
            )
            self._refresh_card(index)
            return

        assessment = self._refresh_card(index)
        pixmap = QtGui.QPixmap(str(self.canonical_path(index)))
        if pixmap.isNull():
            self.status_controller.publish(
                f"Could not read the saved {filename}.",
                StatusLevel.ERROR,
                persistent=True,
                key=f"image-{index}",
            )
            return
        self.status_controller.clear(f"image-{index}")
        self.status_controller.publish(f"{filename} saved.", StatusLevel.SUCCESS)
        self.image_selected.emit(
            pixmap.scaledToWidth(
                200,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        if assessment.state is ImageState.WARNING:
            self.cards[index].image_area.setToolTip(assessment.reason)

    def _ensure_original(self, index: int) -> Path:
        original = self.original_path(index)
        if original.is_file():
            return original
        canonical = self.canonical_path(index)
        image = _load_image_exif(canonical)
        atomic_write_bytes(original, _png_bytes(image))
        return original

    def open_crop_dialog(self, index: int) -> None:
        if self._assessment(index).state is ImageState.MISSING:
            self.status_controller.publish(
                "Select a readable image before cropping.",
                StatusLevel.WARNING,
            )
            return
        try:
            original = self._ensure_original(index)
            saved = self._load_crop_metadata().get(self.labels[index][1], {})
            dialog = CropDialog(
                original,
                zoom=float(saved.get("zoom", 1.0)) if isinstance(saved, dict) else 1.0,
                center_x=float(saved.get("center_x", 0.5)) if isinstance(saved, dict) else 0.5,
                center_y=float(saved.get("center_y", 0.5)) if isinstance(saved, dict) else 0.5,
                parent=self,
            )
        except Exception as exc:
            self.status_controller.publish(
                f"Could not open crop: {exc}",
                StatusLevel.ERROR,
                persistent=True,
                key=f"image-{index}",
            )
            return
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            zoom, center_x, center_y = dialog.values()
            self.apply_crop(
                index,
                zoom=zoom,
                center_x=center_x,
                center_y=center_y,
            )

    def apply_crop(
        self,
        index: int,
        *,
        zoom: float,
        center_x: float,
        center_y: float,
    ) -> None:
        filename = self.labels[index][1]
        try:
            original = self._ensure_original(index)
            image = _load_image_exif(original)
            box = calculate_crop_box(
                image.width,
                image.height,
                zoom=zoom,
                center_x=center_x,
                center_y=center_y,
            )
            cropped = image.crop(box)
            atomic_write_bytes(self.canonical_path(index), _png_bytes(cropped))
            metadata = self._load_crop_metadata()
            metadata[filename] = {
                "source": f"originals/{filename}",
                "zoom": round(max(1.0, min(3.0, float(zoom))), 3),
                "center_x": round(max(0.0, min(1.0, float(center_x))), 4),
                "center_y": round(max(0.0, min(1.0, float(center_y))), 4),
                "crop_box": list(box),
                "target_ratio": "2:3",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            self._write_crop_metadata(metadata)
        except Exception as exc:
            self.status_controller.publish(
                f"Could not save crop for {filename}: {exc}",
                StatusLevel.ERROR,
                persistent=True,
                key=f"image-{index}",
            )
            self._refresh_card(index)
            return

        self.status_controller.clear(f"image-{index}")
        self.status_controller.publish(f"{filename} crop saved.", StatusLevel.SUCCESS)
        self._refresh_card(index)
        self.preview_from_gallery(index, selected=True)

    def clear_image(self, index: int, *, announce: bool = True) -> None:
        filename = self.labels[index][1]
        errors: list[str] = []
        for path in (self.canonical_path(index), self.original_path(index)):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(str(exc))
        try:
            metadata = self._load_crop_metadata()
            metadata.pop(filename, None)
            self._write_crop_metadata(metadata)
        except OSError as exc:
            errors.append(str(exc))

        assessment = self._refresh_card(index)
        self.image_selected.emit(QtGui.QPixmap())
        self.hover_preview_image.emit(QtGui.QPixmap())
        self.clear_preview.emit()
        if errors:
            self.status_controller.publish(
                f"Could not fully clear {filename}: {'; '.join(errors)}",
                StatusLevel.ERROR,
                persistent=True,
                key=f"image-{index}",
            )
        else:
            self.status_controller.clear(f"image-{index}")
            if announce:
                self.status_controller.publish(f"{filename} cleared.", StatusLevel.INFO)
        self.cards[index].image_area.setToolTip(assessment.reason)

    def reset_images(self) -> None:
        for index in self.labels:
            self.clear_image(index, announce=False)
        self.status_controller.publish("All images cleared.", StatusLevel.INFO)

    def refresh_from_workspace(self) -> None:
        first_pixmap: Optional[QtGui.QPixmap] = None
        for index in self.labels:
            assessment = self._refresh_card(index)
            if first_pixmap is None and assessment.state is not ImageState.MISSING:
                candidate = QtGui.QPixmap(str(self.canonical_path(index)))
                if not candidate.isNull():
                    first_pixmap = candidate
        if first_pixmap is None:
            self.clear_preview.emit()
            return
        self.image_selected.emit(
            first_pixmap.scaledToWidth(
                200,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def preview_from_gallery(self, index: int, *, selected: bool = False) -> None:
        pixmap = QtGui.QPixmap(str(self.canonical_path(index)))
        if pixmap.isNull():
            return
        preview = pixmap.scaledToWidth(
            200,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        if selected:
            self.image_selected.emit(preview)
        else:
            self.hover_preview_image.emit(preview)

    def focus_card(self, index: int) -> None:
        card = self.cards.get(index)
        if card is not None:
            card.emphasize()

    def open_gallery_folder(self) -> None:
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.pages_dir)))

    def _place_fab_locked(self) -> None:
        window = self.window()
        surface = window if isinstance(window, QtWidgets.QWidget) else self
        if self.pwrite_fab.parent() is not surface:
            self.pwrite_fab.setParent(surface)
        self._fab_surface = surface
        self.pwrite_fab.set_surface(surface)
        tabbar = surface.findChild(QtWidgets.QTabBar)
        y = (
            tabbar.geometry().bottom() + self.FAB_FIXED_Y_PAD
            if tabbar is not None
            else self.FAB_FIXED_Y_PAD
        )
        self.pwrite_fab.move(QPoint(self.FAB_FIXED_X, y))
        self.pwrite_fab.clamp_to_surface()
        self.pwrite_fab.show()
        self.pwrite_fab.raise_()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._place_fab_locked)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "pwrite_fab"):
            self._place_fab_locked()

    def _open_prompt_writer_bridge(self) -> None:
        opener = getattr(self.window(), "open_prompt_writer", None)
        if callable(opener):
            opener()
            self.status_controller.publish("Prompt Writer opened.", StatusLevel.INFO)
            return
        self.status_controller.publish(
            "Prompt Writer is unavailable.",
            StatusLevel.WARNING,
        )


__all__ = [
    "CropDialog",
    "ImageAssessment",
    "ImageCard",
    "ImageState",
    "ImageTab",
    "assess_image",
    "calculate_crop_box",
]
