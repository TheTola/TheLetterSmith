# File: Image_tab.py
"""
Image-tab UI and logic for Letter Smith.

Canonical preview files:
    gallery/user/pages/cover.png
    gallery/user/pages/letter.png
    gallery/user/pages/wall.png
    gallery/user/pages/back.png

Animated selections also retain their original ``<slot>.gif`` source and
per-image playback settings in ``lettersmith-images.json``.

The Reset Images and Gallery artwork buttons sit in a fixed-size column beside
the image cards. Their native aspect ratios are preserved so they stay readable
without consuming the compact vertical space below the cards.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Callable, MutableMapping, Optional

from PIL import Image, ImageChops

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon

from image_button import ArtworkButton, set_control_invisible
from image_animation import (
    FOREVER,
    IMAGE_MANIFEST_NAME,
    INDEX_TO_SLOT,
    MAX_PLAY_COUNT,
    PreparedImageAssetImport,
    clear_slot_asset,
    image_asset_revision,
    inspect_gif,
    load_image_manifest,
    normalize_gif_settings,
    prepare_image_asset_import,
    reconcile_external_image_assets,
    update_slot_gif_settings,
)
from project_paths import ProjectPathResolver, application_paths
from project_save import ProjectSaveService
from project_state import ProjectStateController
from project_sync import file_fingerprint, image_fingerprint
from recent_media import recent_media, remember_image_copy
from ui_dialogs import LetterSmithConfirmationDialog, LetterSmithDialog
from ui_help import set_control_help
from ui_sounds import UiSound, play_ui_sound
from window_chrome import place_window_on_launcher
from ui_theme import (
    PRIMARY_PAGE_LAYOUT,
    ButtonTier,
    BUTTON_GEOMETRY_SCALE_PROPERTY,
    apply_button_tier,
    apply_tab_heading_style,
)


STOCK_IMAGE_FILES = {
    1: ("stock cover 1.png", "stock cover 2.png", "stock cover 3.png"),
    2: ("stock letter 1.png", "stock letter 2.png", "stock letter 3.png"),
    3: ("stock wall 1.png", "stock wall 2.png", "stock wall 3.png"),
    4: ("stock back 1.png", "stock back 2.png", "stock back 3.png"),
}


def _downloads_directory() -> str:
    location = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.DownloadLocation
    )
    if location:
        return location

    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


@dataclass(frozen=True)
class _PreparedImageImportResult:
    generation: int
    index: int
    project_identity: tuple[str, str]
    baseline_fingerprint: str
    project_pages_directory: Path | None
    prepared: PreparedImageAssetImport
    preview_image: QtGui.QImage
    source_path: Path | None = None


class _ImageImportWorker(QtCore.QObject):
    prepared = Signal(object)
    failed = Signal(int, int, str)
    finished = Signal()

    def __init__(
        self,
        pages_directory: Path,
        project_pages_directory: Path | None,
        index: int,
        slot: str,
        source_path: Path,
        project_identity: tuple[str, str],
        baseline_fingerprint: str,
        generation: int,
        result_holder: MutableMapping[str, object],
    ) -> None:
        super().__init__()
        self._pages_directory = pages_directory
        self._project_pages_directory = project_pages_directory
        self._index = index
        self._slot = slot
        self._source_path = source_path
        self._project_identity = project_identity
        self._baseline_fingerprint = baseline_fingerprint
        self._generation = generation
        self._result_holder = result_holder
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        thread = QtCore.QThread.currentThread()
        return self._cancelled or thread.isInterruptionRequested()

    @QtCore.Slot()
    def run(self) -> None:
        prepared: PreparedImageAssetImport | None = None
        try:
            if self._is_cancelled():
                raise RuntimeError("Image import canceled.")
            if (
                image_asset_revision(self._pages_directory)
                != self._baseline_fingerprint
            ):
                raise RuntimeError(
                    "Images changed before the selection could be processed."
                )

            prepared = prepare_image_asset_import(
                self._pages_directory,
                self._slot,
                self._source_path,
                project_pages_directory=self._project_pages_directory,
            )
            if self._is_cancelled():
                raise RuntimeError("Image import canceled.")
            if (
                image_asset_revision(self._pages_directory)
                != self._baseline_fingerprint
            ):
                raise RuntimeError(
                    "Images changed while the selection was being processed."
                )

            preview_image = QtGui.QImage(str(prepared.preview_path))
            if preview_image.isNull():
                raise ValueError("The selected image could not be decoded.")

            result = _PreparedImageImportResult(
                generation=self._generation,
                index=self._index,
                project_identity=self._project_identity,
                baseline_fingerprint=self._baseline_fingerprint,
                project_pages_directory=self._project_pages_directory,
                prepared=prepared,
                preview_image=preview_image,
                source_path=self._source_path,
            )
            self._result_holder["result"] = result
            self.prepared.emit(result)
            prepared = None
        except Exception as error:
            if prepared is not None:
                prepared.abort()
            self.failed.emit(self._generation, self._index, str(error))
        finally:
            self.finished.emit()


class StockImageDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        image_paths: tuple[Path, ...],
        parent: QtWidgets.QWidget | None = None,
        *,
        recent_paths: tuple[Path, ...] = (),
        recent_labels: tuple[str, ...] = (),
        allow_browse: bool = False,
    ) -> None:
        super().__init__(
            parent,
            QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint,
        )
        self.setObjectName("StockImageTray")
        self.setAccessibleName(
            f"{'Recently Used' if recent_paths else 'Stock Images'} - {title}"
        )
        self.setFixedSize(420, 205 if allow_browse else 146)
        self._selected_path: Path | None = None
        self.browse_requested = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._images: QtWidgets.QListWidget | None = None
        self._recent_images: QtWidgets.QListWidget | None = None
        if recent_paths:
            if allow_browse:
                layout.addWidget(QtWidgets.QLabel("Recently Used"))
            self._recent_images = self._make_image_list(
                recent_paths, recent_labels
            )
            layout.addWidget(self._recent_images)
        else:
            if allow_browse:
                layout.addWidget(QtWidgets.QLabel("Stock Images"))
            self._images = self._make_image_list(image_paths)
            layout.addWidget(self._images)
        if allow_browse:
            browse = QtWidgets.QPushButton("Choose Image…")
            browse.clicked.connect(self._browse)
            layout.addWidget(browse)
        self.setStyleSheet(
            "QDialog#StockImageTray{background:#101317;border:1px solid #394654;"
            "border-radius:8px;}"
            "QLabel{color:#e8edf5;font-weight:600;}"
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{border:1px solid #2d3540;border-radius:6px;}"
            "QListWidget::item:hover,QListWidget::item:selected{"
            "border:2px solid #00d0ff;background:#19232d;}"
        )

    def _make_image_list(
        self,
        paths: tuple[Path, ...],
        labels: tuple[str, ...] = (),
    ) -> QtWidgets.QListWidget:
        images = QtWidgets.QListWidget()
        images.setViewMode(QtWidgets.QListView.IconMode)
        images.setIconSize(QtCore.QSize(112, 112))
        images.setGridSize(QtCore.QSize(130, 126))
        images.setSpacing(3)
        images.setMovement(QtWidgets.QListView.Static)
        images.setResizeMode(QtWidgets.QListView.Adjust)
        images.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        images.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        for index, path in enumerate(paths):
            item = QtWidgets.QListWidgetItem(QIcon(str(path)), "")
            item.setData(QtCore.Qt.UserRole, str(path))
            item.setToolTip(labels[index] if index < len(labels) else path.stem)
            images.addItem(item)
        images.itemClicked.connect(self._accept_item)
        return images

    def _browse(self) -> None:
        self.browse_requested = True
        self.accept()

    def selected_path(self) -> Path | None:
        return self._selected_path

    def _accept_item(self, item: QtWidgets.QListWidgetItem) -> None:
        self._selected_path = Path(str(item.data(QtCore.Qt.UserRole)))
        self.accept()

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:
        self.reject()
        super().focusOutEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        cursor = QtGui.QCursor.pos()
        target = QtCore.QRect(cursor + QtCore.QPoint(8, 8), self.size())
        place_window_on_launcher(self, geometry=target, near=True)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Writer floating button
# ─────────────────────────────────────────────────────────────────────────────


class StaticFab(QtWidgets.QToolButton):
    def __init__(
        self,
        parent_widget: QtWidgets.QWidget,
        surface: QtWidgets.QWidget,
    ) -> None:
        super().__init__(parent_widget)
        self._surface = surface

        self.setObjectName("PWriteFab")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.setFixedSize(200, 200)
        self.setStyleSheet(
            "#PWriteFab {"
            "background: transparent;"
            "border: none;"
            "padding: 0;"
            "}"
        )

    def set_surface(self, surface: QtWidgets.QWidget) -> None:
        self._surface = surface

    def clamp_to_surface(self) -> None:
        if self._surface is None:
            return

        maximum_x = max(
            0,
            self._surface.width() - self.width(),
        )
        maximum_y = max(
            0,
            self._surface.height() - self.height(),
        )

        self.move(
            QPoint(
                max(
                    0,
                    min(
                        self.x(),
                        maximum_x,
                    ),
                ),
                max(
                    0,
                    min(
                        self.y(),
                        maximum_y,
                    ),
                ),
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Image helpers
# ─────────────────────────────────────────────────────────────────────────────


def _trim_artwork_canvas(
    button: ArtworkButton,
) -> None:
    """
    Crop the large invisible canvas around an artwork-button PNG.

    These button images contain large transparent or near-background regions.
    Merely increasing the QPushButton size therefore leaves the visible artwork
    tiny. This routine identifies meaningful pixels from both alpha and corner
    color difference, crops the canvas, and replaces the button's pixmap with
    the cropped version.
    """
    if not button.has_artwork:
        return

    try:
        with Image.open(
            button.artwork_path
        ) as source:
            rgba = source.convert("RGBA")
            width, height = rgba.size

            if width <= 0 or height <= 0:
                return

            alpha_mask = rgba.getchannel(
                "A"
            ).point(
                lambda value: (
                    255
                    if value >= 28
                    else 0
                )
            )

            corners = (
                rgba.getpixel((0, 0)),
                rgba.getpixel(
                    (
                        width - 1,
                        0,
                    )
                ),
                rgba.getpixel(
                    (
                        0,
                        height - 1,
                    )
                ),
                rgba.getpixel(
                    (
                        width - 1,
                        height - 1,
                    )
                ),
            )

            background_color = tuple(
                sum(
                    pixel[channel]
                    for pixel in corners
                )
                // len(corners)
                for channel in range(4)
            )

            background = Image.new(
                "RGBA",
                rgba.size,
                background_color,
            )

            difference = ImageChops.difference(
                rgba,
                background,
            ).convert("L")

            difference_mask = difference.point(
                lambda value: (
                    255
                    if value >= 18
                    else 0
                )
            )

            alpha_bounds = alpha_mask.getbbox()
            if alpha_bounds not in {
                None,
                (0, 0, width, height),
            }:
                # Transparent pixels may retain arbitrary RGB data. Including
                # their color difference restores the invisible canvas that
                # the alpha channel already identified correctly.
                combined_mask = alpha_mask
            else:
                combined_mask = difference_mask

            bounds = combined_mask.getbbox()

            if bounds is None:
                return

            left, top, right, bottom = bounds

            horizontal_padding = max(
                4,
                int(
                    (right - left)
                    * 0.035
                ),
            )

            vertical_padding = max(
                4,
                int(
                    (bottom - top)
                    * 0.06
                ),
            )

            cropped = rgba.crop(
                (
                    max(
                        0,
                        left - horizontal_padding,
                    ),
                    max(
                        0,
                        top - vertical_padding,
                    ),
                    min(
                        width,
                        right + horizontal_padding,
                    ),
                    min(
                        height,
                        bottom + vertical_padding,
                    ),
                )
            )

            crop_width, crop_height = (
                cropped.size
            )

            if (
                crop_width <= 0
                or crop_height <= 0
            ):
                return

            raw = cropped.tobytes(
                "raw",
                "RGBA",
            )

            image = QtGui.QImage(
                raw,
                crop_width,
                crop_height,
                crop_width * 4,
                QtGui.QImage.Format_RGBA8888,
            ).copy()

            button._artwork = (
                QtGui.QPixmap.fromImage(
                    image
                )
            )

            button.updateGeometry()
            button.update()

    except Exception:
        return


def _mask_button_to_artwork(
    button: ArtworkButton,
) -> None:
    """Keep transparent artwork margins from blocking nearby buttons."""
    if not button.has_artwork:
        button.clearMask()
        return

    content = button.rect().adjusted(
        2,
        2,
        -2,
        -2,
    )
    scaled = button._artwork.scaled(
        content.size(),
        QtCore.Qt.KeepAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    target = QtCore.QRect(
        content.center().x()
        - scaled.width() // 2,
        content.center().y()
        - scaled.height() // 2,
        scaled.width(),
        scaled.height(),
    )
    button.setMask(QtGui.QRegion(target))


class _ImageUtilityButton(ArtworkButton):
    def __init__(
        self,
        text: str,
        project_root: str | Path,
        artwork_filename: str,
        *,
        parent: QtWidgets.QWidget | None = None,
        broken_artwork_filename: str | None = None,
    ) -> None:
        super().__init__(
            text,
            project_root,
            artwork_filename,
            parent,
            broken_artwork_filename=broken_artwork_filename,
        )

    def apply_theme_assets(
        self,
        theme_service: object | None = None,
    ) -> None:
        super().apply_theme_assets(
            theme_service
        )
        _trim_artwork_canvas(self)
        self.set_artwork_stretch(False)
        self.set_text_word_wrap(True)
        apply_button_tier(self, ButtonTier.LARGE)
        _mask_button_to_artwork(self)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        _mask_button_to_artwork(self)
        panel = self.parentWidget()
        if isinstance(panel, _ImageUtilityPanel):
            panel.position_buttons()


class _ImageUtilityPanel(QtWidgets.QWidget):
    def __init__(
        self,
        reset_btn: QtWidgets.QAbstractButton,
        open_btn: QtWidgets.QAbstractButton,
        gap: int,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(parent)
        self._buttons = (reset_btn, open_btn)
        self._gap = gap
        for button in self._buttons:
            button.setParent(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

    def set_gap(self, gap: int) -> None:
        self._gap = gap
        self.position_buttons()

    def position_buttons(self) -> None:
        reset_btn, open_btn = self._buttons
        total_height = reset_btn.height() + open_btn.height() + self._gap
        self.setMinimumHeight(total_height)
        top = max(0, (self.height() - total_height) // 2)
        reset_btn.move(max(0, (self.width() - reset_btn.width()) // 2), top)
        open_btn.move(
            max(0, (self.width() - open_btn.width()) // 2),
            top + reset_btn.height() + self._gap,
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.position_buttons()


class _ImageSettingsButton(ArtworkButton):
    """Square GIF control; use setbutton.png when the active theme supplies it."""

    def __init__(self, project_root: str | Path, parent: QtWidgets.QWidget) -> None:
        super().__init__("⚙", project_root, "setbutton.png", parent, tier=None)
        self.setFixedSize(44, 44)
        font = QtGui.QFont("Segoe UI Symbol")
        font.setPointSize(18)
        self.setFont(font)
        self.setAccessibleName("Settings")

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        # The shared text-fit guard must not size this control from its pixmap.
        return QtCore.QSize(44, 44)

    def apply_theme_assets(self, theme_service: object | None = None) -> None:
        service = theme_service if theme_service is not None else self._theme_service
        theme_id = getattr(service, "theme_id", "cyber_forge")
        dedicated = application_paths(self._project_root).app_resource_path(
            Path("themes") / theme_id / "buttons" / "setbutton.png"
        )
        self._artwork_filename = "setbutton.png" if dedicated.is_file() else "AButton.png"
        super().apply_theme_assets(theme_service)
        # ArtworkButton raises its minimum size when artwork is available.
        self.setFixedSize(44, 44)
        self.setAccessibleName("Settings")

    def _resolved_artwork(self, state):
        path, artwork, broken = super()._resolved_artwork(state)
        if self._artwork_filename != "AButton.png" or artwork.isNull():
            return path, artwork, broken

        # Reuse the themed frame's end caps and a centered panel at native
        # proportions, instead of squeezing its wide canvas into a square.
        source_width, source_height = artwork.width(), artwork.height()
        canvas_size = 176
        cap_source = min(round(source_height * 0.38), source_width // 3)
        cap_target = round(cap_source * canvas_size / source_height)
        center_target = canvas_size - 2 * cap_target
        center_source = round(center_target * source_height / canvas_size)
        square = QtGui.QPixmap(canvas_size, canvas_size)
        square.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(square)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(
            QtCore.QRect(0, 0, cap_target, canvas_size),
            artwork,
            QtCore.QRect(0, 0, cap_source, source_height),
        )
        painter.drawPixmap(
            QtCore.QRect(cap_target, 0, center_target, canvas_size),
            artwork,
            QtCore.QRect(
                (source_width - center_source) // 2, 0, center_source, source_height
            ),
        )
        painter.drawPixmap(
            QtCore.QRect(canvas_size - cap_target, 0, cap_target, canvas_size),
            artwork,
            QtCore.QRect(source_width - cap_source, 0, cap_source, source_height),
        )
        painter.end()
        return path, square, broken


class _ResetImagesConfirmationDialog(
    LetterSmithConfirmationDialog
):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            question="Are you sure you want to reset?",
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
            width=390,
        )
        self.setObjectName(
            "ResetImagesConfirmationDialog"
        )
        self.setAccessibleName(
            "Confirm reset images"
        )
        self.primary_button.setObjectName(
            "ResetImagesYes"
        )
        self.secondary_button.setObjectName(
            "ResetImagesNo"
        )
        self.yes_button = self.primary_button
        self.no_button = self.secondary_button


# ─────────────────────────────────────────────────────────────────────────────
# Clickable image thumbnail
# ─────────────────────────────────────────────────────────────────────────────


class _ImageThumbnail(
    QtWidgets.QLabel
):
    clicked = Signal()
    file_dropped = Signal(str)
    hovered = Signal()

    SUPPORTED_EXTENSIONS = (
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
    )

    def __init__(
        self,
        parent: Optional[
            QtWidgets.QWidget
        ] = None,
    ) -> None:
        super().__init__(parent)
        self._theme_frame_source = QtGui.QPixmap()
        self._theme_frame = QtGui.QPixmap()
        self._theme_frame_path = Path()
        self._theme_frame_color = QtGui.QColor("#7f9099")

        self.setAlignment(
            QtCore.Qt.AlignCenter
        )

        self.setFixedSize(150, 165)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Expanding,
        )

        self.setAcceptDrops(True)

        self.setCursor(
            QtCore.Qt.PointingHandCursor
        )

        self.setFocusPolicy(
            QtCore.Qt.StrongFocus
        )

        set_control_help(
            self,
            "Click to select an image, "
            "or drag an image here.",
        )

        self.setStyleSheet(
            "background: #101317;"
            "border: 1px solid #2d3540;"
            "border-radius: 6px;"
        )

    def apply_theme_assets(
        self,
        theme_service: object | None,
        project_root: str | Path | None = None,
    ) -> None:
        if project_root is None:
            owner = self.parentWidget()
            while owner is not None and not hasattr(owner, "project_root"):
                owner = owner.parentWidget()
            project_root = getattr(
                owner,
                "project_root",
                application_paths().workspace_root,
            )
        baseline = application_paths(project_root).app_resource_path(
            "themes/cyber_forge/image_frame.png"
        )
        frame_path = baseline
        resolver = getattr(theme_service, "resolve_asset", None)
        if callable(resolver):
            try:
                frame_path = Path(resolver("image_frame.png"))
            except (OSError, RuntimeError, TypeError, ValueError):
                frame_path = baseline
        self._theme_frame_path = Path(frame_path).resolve()
        self._theme_frame_source = QtGui.QPixmap(str(self._theme_frame_path))
        tokens = getattr(theme_service, "tokens", None)
        self._theme_frame_color = QtGui.QColor(
            getattr(tokens, "secondary", "#7f9099")
        )
        self._theme_frame = QtGui.QPixmap(self._theme_frame_source.size())
        self._theme_frame.fill(QtCore.Qt.transparent)
        if not self._theme_frame_source.isNull():
            painter = QtGui.QPainter(self._theme_frame)
            painter.drawPixmap(0, 0, self._theme_frame_source)
            painter.setCompositionMode(
                QtGui.QPainter.CompositionMode_SourceIn
            )
            painter.fillRect(
                self._theme_frame.rect(),
                self._theme_frame_color,
            )
            painter.end()
        self.update()

    @property
    def theme_frame_path(self) -> Path:
        return self._theme_frame_path

    @property
    def theme_frame_color(self) -> QtGui.QColor:
        return QtGui.QColor(self._theme_frame_color)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if self._theme_frame.isNull():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._theme_frame)

    def mousePressEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if (
            event.button()
            == QtCore.Qt.LeftButton
        ):
            self.clicked.emit()
            event.accept()
            return

        super().mousePressEvent(event)

    def keyPressEvent(
        self,
        event: QtGui.QKeyEvent,
    ) -> None:
        if event.key() in (
            QtCore.Qt.Key_Return,
            QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Space,
        ):
            self.clicked.emit()
            event.accept()
            return

        super().keyPressEvent(event)

    def enterEvent(
        self,
        event: QtCore.QEvent,
    ) -> None:
        self.hovered.emit()
        super().enterEvent(event)

    def dragEnterEvent(
        self,
        event: QtGui.QDragEnterEvent,
    ) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        for url in event.mimeData().urls():
            if (
                url.isLocalFile()
                and url.toLocalFile()
                .lower()
                .endswith(
                    self.SUPPORTED_EXTENSIONS
                )
            ):
                event.acceptProposedAction()
                return

        event.ignore()

    def dragMoveEvent(
        self,
        event: QtGui.QDragMoveEvent,
    ) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(
        self,
        event: QtGui.QDropEvent,
    ) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue

            path = url.toLocalFile()

            if path.lower().endswith(
                self.SUPPORTED_EXTENSIONS
            ):
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return

        event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
# Image asset card
# ─────────────────────────────────────────────────────────────────────────────


class ImageAssetCard(
    QtWidgets.QFrame
):
    select_requested = Signal(int)
    clear_requested = Signal(int)
    settings_requested = Signal(int)
    preview_requested = Signal(int)
    file_dropped = Signal(
        int,
        str,
    )

    def __init__(
        self,
        index: int,
        title: str,
        project_root: str | Path,
        parent: Optional[
            QtWidgets.QWidget
        ] = None,
    ) -> None:
        super().__init__(parent)

        self.index = index

        self._source_pixmap = (
            QtGui.QPixmap()
        )
        self._movie: QtGui.QMovie | None = None
        self._movie_source_size = QtCore.QSize()
        self._playback_active = True
        self._resume_movie_on_activation = False
        self._animation_enabled = True
        self._speed_percent = 100
        self._gif_settings = normalize_gif_settings(None)
        self._managed_preview = False
        self._preview_running = False
        self._preview_phase = ""
        self._completed_cycles = 0
        self._target_cycles: int | str = FOREVER
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._advance_preview_cycle)

        self.setObjectName(
            "ImageAssetCard"
        )

        self.setProperty(
            "assetState",
            "missing",
        )

        self.setFixedSize(300, 270)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Preferred,
        )

        root_layout = (
            QtWidgets.QVBoxLayout(self)
        )

        root_layout.setContentsMargins(
            10,
            10,
            10,
            8,
        )

        root_layout.setSpacing(7)

        self.title_label = (
            QtWidgets.QLabel(title)
        )

        self.title_label.setAlignment(
            QtCore.Qt.AlignCenter
        )

        self.title_label.setStyleSheet(
            "QLabel {"
            "background-color: transparent;"
            "border: none;"
            "color: #e8edf5;"
            "font: 600 12px 'Segoe UI';"
            "padding: 0;"
            "}"
        )

        root_layout.addWidget(
            self.title_label
        )

        self.thumbnail = (
            _ImageThumbnail(self)
        )

        self.thumbnail.clicked.connect(
            lambda: (
                self.select_requested.emit(
                    self.index
                )
            )
        )

        self.thumbnail.hovered.connect(
            lambda: (
                self.preview_requested.emit(
                    self.index
                )
            )
        )

        self.thumbnail.file_dropped.connect(
            lambda path: (
                self.file_dropped.emit(
                    self.index,
                    path,
                )
            )
        )

        root_layout.addWidget(
            self.thumbnail,
            1,
            QtCore.Qt.AlignCenter,
        )

        button_row = (
            QtWidgets.QHBoxLayout()
        )

        button_row.setSpacing(8)

        self.clear_btn = (
            ArtworkButton(
                "Clear",
                project_root,
                "AButton.png",
                self,
                broken_artwork_filename="AButton.png",
                tier=ButtonTier.MEDIUM,
            )
        )

        self.clear_btn.setCursor(
            QtCore.Qt.PointingHandCursor
        )
        self.clear_btn.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, 1.0)
        self.clear_btn.apply_theme_assets()
        self.clear_btn.setProperty("themeRole", "clearAction")
        set_control_help(
            self.clear_btn,
            "Remove this image from the letter and return this slot to its empty state.",
            accessible_name="Clear image",
        )

        self.clear_btn.clicked.connect(
            lambda: (
                self.clear_requested.emit(
                    self.index
                )
            )
        )

        self.settings_btn = _ImageSettingsButton(project_root, self)
        self.settings_btn.setCursor(
            QtCore.Qt.PointingHandCursor
        )
        self.settings_btn.setProperty("themeRole", "button")
        set_control_help(
            self.settings_btn,
            "Settings — adjust playback for this animated image.",
            accessible_name="Settings",
        )
        self.settings_btn.setEnabled(False)
        self.settings_btn.setVisible(False)
        self.settings_btn.clicked.connect(
            lambda: self.settings_requested.emit(
                self.index
            )
        )

        button_row.addStretch(1)
        button_row.addWidget(
            self.clear_btn
        )
        button_row.addWidget(
            self.settings_btn
        )
        button_row.addStretch(1)

        root_layout.addLayout(
            button_row
        )

        self.setStyleSheet(
            "QFrame#ImageAssetCard {"
            "background: #101317;"
            "border: 1px solid #8c2f36;"
            "border-radius: 8px;"
            "}"
            "QFrame#ImageAssetCard"
            "[assetState='ready'] {"
            "border: 1px solid #00d0ff;"
            "}"
            "QFrame#ImageAssetCard"
            "[assetState='warning'] {"
            "border-color: #c9a227;"
            "}"
            "QFrame#ImageAssetCard"
            "[assetState='missing'] {"
            "border-color: #8c2f36;"
            "}"
        )

    def apply_theme_assets(
        self,
        theme_service: object | None,
        project_root: str | Path | None = None,
    ) -> None:
        if project_root is None:
            owner = self.parentWidget()
            while owner is not None and not hasattr(owner, "project_root"):
                owner = owner.parentWidget()
            project_root = getattr(
                owner,
                "project_root",
                application_paths().workspace_root,
            )
        self.thumbnail.apply_theme_assets(theme_service, project_root)
        self.clear_btn.apply_theme_assets(theme_service)
        self.settings_btn.apply_theme_assets(theme_service)

    def set_asset_state(
        self,
        state: str,
    ) -> None:
        self.setProperty(
            "assetState",
            state,
        )

        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_pixmap(
        self,
        pixmap: QtGui.QPixmap,
    ) -> None:
        self._stop_movie()
        self._source_pixmap = (
            QtGui.QPixmap(pixmap)
        )

        self._rescale()

        self.set_asset_state(
            "missing"
            if pixmap.isNull()
            else "ready"
        )

        self.settings_btn.setEnabled(False)
        self.settings_btn.setVisible(False)

    def set_asset_path(
        self,
        path: str,
        *,
        animated_gif: bool,
        settings: dict[str, object] | None = None,
        preview_path: str | None = None,
        animate_gif: bool = True,
        preview_pixmap: QtGui.QPixmap | None = None,
        embedded_play_count: int | str | None = None,
    ) -> None:
        if not animated_gif:
            self.set_pixmap(
                preview_pixmap
                if preview_pixmap is not None
                else QtGui.QPixmap(path)
            )
            self.settings_btn.setToolTip(
                "Settings — static image"
            )
            return

        normalized = normalize_gif_settings(settings)
        self._gif_settings = normalized
        self._animation_enabled = bool(normalized["animation_enabled"])
        self._speed_percent = int(normalized["speed_percent"])
        self._managed_preview = (
            normalized["playback_mode"] != "original"
            or normalized["play_count"] != FOREVER
            or normalized["start_delay_ms"] > 0
            or normalized["loop_pause_ms"] > 0
        )
        if self._managed_preview:
            if embedded_play_count is None:
                try:
                    embedded_play_count = inspect_gif(path).embedded_play_count
                except (OSError, ValueError):
                    embedded_play_count = FOREVER
            if embedded_play_count != FOREVER:
                try:
                    embedded_play_count = max(1, int(embedded_play_count))
                except (TypeError, ValueError):
                    embedded_play_count = FOREVER
            configured = normalized["play_count"]
            if normalized["playback_mode"] == "original":
                self._target_cycles = (
                    embedded_play_count if configured == FOREVER
                    else configured if embedded_play_count == FOREVER
                    else min(int(configured), int(embedded_play_count))
                )
            else:
                self._target_cycles = configured
        animation_path = preview_path or path
        if not self._animation_enabled:
            preview = (
                preview_pixmap
                if preview_pixmap is not None
                else QtGui.QPixmap(animation_path)
            )
            self.set_pixmap(preview)
            if preview.isNull():
                return
            self.settings_btn.setVisible(True)
            self.settings_btn.setEnabled(True)
            self.settings_btn.setToolTip(
                "Settings — animation for this GIF"
            )
            return

        self._stop_movie()
        self._source_pixmap = QtGui.QPixmap()
        movie = QtGui.QMovie(animation_path)
        movie.setCacheMode(QtGui.QMovie.CacheNone)
        if not movie.isValid():
            self.set_pixmap(QtGui.QPixmap(animation_path))
            return
        movie.setSpeed(self._speed_percent)
        self._movie = movie
        if self._managed_preview:
            movie.frameChanged.connect(self._preview_frame_changed)
        self.thumbnail.setText("")
        self.thumbnail.setStyleSheet(
            "background: #101317;"
            "border: 1px solid #2d3540;"
            "border-radius: 6px;"
        )
        self.thumbnail.setMovie(movie)
        movie.jumpToFrame(0)
        self._movie_source_size = movie.currentPixmap().size()
        self.settings_btn.setVisible(True)
        self.settings_btn.setEnabled(True)
        self.settings_btn.setToolTip(
            "Settings — animation for this GIF"
        )
        self.set_asset_state("ready")
        self._rescale()
        if self._playback_active and animate_gif:
            if self._managed_preview:
                self._restart_managed_preview()
            else:
                movie.start()
        else:
            self._resume_movie_on_activation = True

    def clear_pixmap(self) -> None:
        self._stop_movie()
        self._source_pixmap = (
            QtGui.QPixmap()
        )

        self.thumbnail.clear()
        self.thumbnail.setFixedSize(self._thumbnail_bounds())

        self.thumbnail.setText(
            "Click to select image"
        )

        self.thumbnail.setStyleSheet(
            "background: #101317;"
            "border: 1px dashed #3c4652;"
            "border-radius: 6px;"
            "color: #788594;"
        )

        self.set_asset_state(
            "missing"
        )
        self.settings_btn.setEnabled(False)
        self.settings_btn.setVisible(False)
        self.settings_btn.setToolTip(
            "Settings — select an image before opening"
        )

    def set_card_size(self, size: QtCore.QSize) -> None:
        scale = min(1.0, max(0.55, (size.width() - 76) / 155))
        self.clear_btn.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, scale)
        apply_button_tier(self.clear_btn, ButtonTier.MEDIUM)
        self.setFixedSize(size)
        self._rescale()

    def _thumbnail_bounds(self) -> QtCore.QSize:
        margins = self.layout().contentsMargins()
        controls = max(self.clear_btn.height(), self.settings_btn.height())
        return QtCore.QSize(
            max(40, min(168, self.width() - margins.left() - margins.right() - 2)),
            max(40, min(155, self.height() - margins.top() - margins.bottom()
                        - self.title_label.sizeHint().height() - controls
                        - 2 * self.layout().spacing() - 2)),
        )

    def _rescale(self) -> None:
        maximum_content = self._thumbnail_bounds()
        if self._movie is not None:
            if not self._movie_source_size.isEmpty():
                content_size = self._movie_source_size.scaled(
                    maximum_content, QtCore.Qt.KeepAspectRatio,
                )
                self.thumbnail.setFixedSize(
                    content_size + QtCore.QSize(2, 2)
                )
                self._movie.setScaledSize(content_size)
            return

        if self._source_pixmap.isNull():
            self.clear_pixmap()
            return

        self.thumbnail.setText("")

        self.thumbnail.setStyleSheet(
            "background: #101317;"
            "border: 1px solid #2d3540;"
            "border-radius: 6px;"
        )

        scaled_pixmap = (
            self._source_pixmap.scaled(
                maximum_content,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )
        self.thumbnail.setFixedSize(
            scaled_pixmap.size() + QtCore.QSize(2, 2)
        )

        self.thumbnail.setPixmap(
            scaled_pixmap
        )

    def set_playback_active(
        self,
        active: bool,
    ) -> None:
        active = bool(active)
        if active == self._playback_active:
            return

        self._playback_active = active
        if self._movie is None:
            return

        if getattr(self, "_managed_preview", False):
            if active:
                if self._preview_phase != "end":
                    self._restart_managed_preview()
            else:
                self._preview_running = False
                self._preview_timer.stop()
                if self._movie.state() == QtGui.QMovie.Running:
                    self._movie.setPaused(True)
            return

        if not active:
            if (
                self._movie.state()
                == QtGui.QMovie.Running
            ):
                self._movie.setPaused(True)
                self._resume_movie_on_activation = True
            else:
                self._resume_movie_on_activation = False
            return

        if not self._resume_movie_on_activation:
            return

        if (
            self._movie.state()
            == QtGui.QMovie.Paused
        ):
            self._movie.setPaused(False)
        elif (
            self._movie.state()
            == QtGui.QMovie.NotRunning
        ):
            self._movie.start()
        self._resume_movie_on_activation = False

    def _stop_movie(self) -> None:
        self._preview_timer.stop()
        self._preview_running = False
        self._preview_phase = ""
        self._resume_movie_on_activation = False
        self._movie_source_size = QtCore.QSize()
        if self._movie is None:
            return
        self._movie.stop()
        self.thumbnail.setMovie(None)
        self._movie.setFileName("")
        self._movie.deleteLater()
        self._movie = None

    def _restart_managed_preview(self) -> None:
        movie = self._movie
        if movie is None:
            return
        self._preview_timer.stop()
        self._preview_running = False
        movie.stop()
        movie.jumpToFrame(0)
        self._completed_cycles = 0
        self._preview_phase = "start"
        delay = int(self._gif_settings["start_delay_ms"])
        if delay:
            self._preview_timer.start(delay)
        else:
            self._advance_preview_cycle()

    def _preview_frame_changed(self, frame: int) -> None:
        movie = self._movie
        if (
            not self._preview_running or movie is None
            or frame != movie.frameCount() - 1
        ):
            return
        remaining = max(1, movie.nextFrameDelay())
        self._preview_running = False
        movie.setPaused(True)
        self._preview_phase = "frame"
        self._preview_timer.start(remaining)

    def _advance_preview_cycle(self) -> None:
        movie = self._movie
        if movie is None or not self._playback_active:
            return
        if self._preview_phase == "frame":
            self._completed_cycles += 1
            movie.stop()
            movie.jumpToFrame(max(0, movie.frameCount() - 1))
            if (
                self._target_cycles != FOREVER
                and self._completed_cycles >= self._target_cycles
            ):
                # Published playback also retains the final displayed frame.
                self._preview_phase = "end"
                return
            self._preview_phase = "pause"
            pause = int(self._gif_settings["loop_pause_ms"])
            if pause:
                self._preview_timer.start(pause)
                return
        self._preview_phase = "playing"
        self._preview_running = True
        movie.start()
        if movie.currentFrameNumber() != 0:
            movie.jumpToFrame(0)

    def release_asset_handle(self) -> None:
        self._stop_movie()

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(event)
        self._rescale()


# ─────────────────────────────────────────────────────────────────────────────
# Image tab
# ─────────────────────────────────────────────────────────────────────────────


class ImageSettingsDialog(LetterSmithDialog):
    def __init__(
        self,
        title: str,
        *,
        animated_gif: bool,
        settings: dict[str, object] | None = None,
        embedded_play_count: int | str = FOREVER,
        preview_callback: Callable[[dict[str, object]], None] | None = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        if not animated_gif:
            raise ValueError("GIF controls are only available for animated GIFs")
        heading = {
            "Cover Page Image": "COVER CONTROLS",
            "Main Letter Image": "LETTER CONTROLS",
            "Letter Background Image": "CLARIFIER CONTROLS",
            "Final Backdrop Image": "BACKDROP CONTROLS",
        }.get(title, f"{title.upper()} CONTROLS")
        super().__init__(
            parent, title=heading, click_outside_dismiss=True, width=400,
        )
        self._saved_settings = normalize_gif_settings(settings)
        self._preview_callback = preview_callback
        self._embedded_play_count = embedded_play_count
        self._committed = False
        self._preserve_timing_precision = True
        self.setObjectName("GifControlsPopup")

        controls_style = (
            "QComboBox,QSpinBox,QDoubleSpinBox{"
            f"background:{self.colors['control_background']};"
            f"color:{self.colors['text']};"
            f"border:1px solid {self.colors['border']};"
            "border-radius:6px;padding:6px 9px;"
            "min-height:24px;}"
            "QComboBox:hover,QSpinBox:hover,QDoubleSpinBox:hover{"
            f"border-color:{self.colors['accent']};}}"
            f"QLabel{{color:{self.colors['text']};background:transparent;border:none;}}"
            "QPushButton[dialogRole='primary']{"
            f"background:{self.colors['accent']};color:{self.colors['background']};"
            f"border-color:{self.colors['accent']};}}"
        )
        self.panel.setStyleSheet(controls_style)

        self._section("Playback")
        playback = QtWidgets.QFormLayout()
        playback.setSpacing(10)
        playback.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.speed = QtWidgets.QComboBox(self.panel)
        self.speed.setAccessibleName("Speed")
        for label, value in (
            ("Original", 100), ("50%", 50), ("75%", 75),
            ("125%", 125), ("150%", 150), ("Custom…", "custom"),
        ):
            self.speed.addItem(label, value)
        self.custom_speed = QtWidgets.QSpinBox(self.panel)
        self.custom_speed.setRange(25, 400)
        self.custom_speed.setSuffix("%")
        self.custom_speed.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.custom_speed.setAccessibleName("Custom speed")
        speed_row = self._selection_row(self.speed, self.custom_speed)
        playback.addRow("Speed", speed_row)
        self.play_count = QtWidgets.QComboBox(self.panel)
        self.play_count.setAccessibleName("Play Count")
        for label, value in (
            ("Original", "original"), ("Forever", FOREVER),
            ("1", 1), ("2", 2), ("3", 3), ("5", 5),
            ("Custom…", "custom"),
        ):
            self.play_count.addItem(label, value)
        self.custom_count = QtWidgets.QSpinBox(self.panel)
        self.custom_count.setRange(1, MAX_PLAY_COUNT)
        self.custom_count.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.custom_count.setAccessibleName("Custom play count")
        count_row = self._selection_row(self.play_count, self.custom_count)
        playback.addRow("Play Count", count_row)
        self.content_layout.addLayout(playback)

        self._section("Timing")
        timing = QtWidgets.QFormLayout()
        timing.setSpacing(10)
        timing.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.start_delay = self._seconds_control(
            int(self._saved_settings["start_delay_ms"])
        )
        self.start_delay.setAccessibleName("Start Delay")
        timing.addRow("Start Delay", self.start_delay)
        self.loop_pause = self._seconds_control(
            int(self._saved_settings["loop_pause_ms"])
        )
        self.timing_label = QtWidgets.QLabel(self.panel)
        self.loop_pause.setAccessibleName("Loop Pause")
        timing.addRow(self.timing_label, self.loop_pause)
        self.content_layout.addLayout(timing)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()
        self.reset_button = self._action_button("Reset", "secondary")
        self.save_button = self._action_button("Save", "primary")
        self.reset_button.setAutoDefault(False)
        self.save_button.setAutoDefault(False)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.save_button)
        self.content_layout.addLayout(actions)
        self.reset_button.clicked.connect(self.reset_defaults)
        self.save_button.clicked.connect(self.accept)

        stored_speed = int(self._saved_settings["speed_percent"])
        speed_index = self.speed.findData(stored_speed)
        if speed_index < 0:
            speed_index = self.speed.findData("custom")
        self.speed.setCurrentIndex(speed_index)
        self.custom_speed.setValue(stored_speed)
        stored_count = self._saved_settings["play_count"]
        mode = self._saved_settings["playback_mode"]
        selected_count = (
            "original" if mode == "original" and stored_count == FOREVER
            else stored_count
        )
        count_index = self.play_count.findData(selected_count)
        if count_index < 0:
            count_index = self.play_count.findData("custom")
        self.play_count.setCurrentIndex(count_index)
        self.custom_count.setValue(
            int(stored_count) if stored_count != FOREVER else 4
        )
        self._sync_controls()
        self.speed.currentIndexChanged.connect(self._changed)
        self.play_count.currentIndexChanged.connect(self._changed)
        self.custom_speed.valueChanged.connect(self._changed)
        self.custom_count.valueChanged.connect(self._changed)
        self.start_delay.valueChanged.connect(self._changed)
        self.loop_pause.valueChanged.connect(self._changed)

    def _section(self, title: str) -> None:
        label = QtWidgets.QLabel(title, self.panel)
        label.setStyleSheet(
            f"color:{self.colors['accent']};font:600 10pt 'Segoe UI';"
        )
        self.content_layout.addWidget(label)

    @staticmethod
    def _selection_row(
        selector: QtWidgets.QComboBox, custom: QtWidgets.QSpinBox,
    ) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(selector, 1)
        layout.addWidget(custom)
        return row

    @staticmethod
    def _seconds_control(milliseconds: int) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(0.0, 86_400.0)
        control.setDecimals(2)
        control.setSingleStep(0.25)
        control.setSuffix(" s")
        control.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        control.setValue(milliseconds / 1000.0)
        return control

    def _sync_controls(self) -> None:
        self.custom_speed.setVisible(self.speed.currentData() == "custom")
        self.custom_count.setVisible(
            self.play_count.currentData() == "custom"
        )
        selected = self.play_count.currentData()
        looping = selected == FOREVER or (
            selected == "original" and self._embedded_play_count == FOREVER
        )
        label = "Loop Pause" if looping else "End Hold"
        self.timing_label.setText(label)
        self.loop_pause.setAccessibleName(label)

    def _changed(self, *_args: object) -> None:
        self._sync_controls()
        if self._preview_callback is not None:
            self._preview_callback(self.gif_settings())

    def reset_defaults(self) -> None:
        self._preserve_timing_precision = False
        for control, value in (
            (self.speed, "Original"), (self.play_count, "Original"),
        ):
            with QtCore.QSignalBlocker(control):
                control.setCurrentText(value)
        for control in (self.start_delay, self.loop_pause):
            with QtCore.QSignalBlocker(control):
                control.setValue(0)
        self._changed()

    def reject(self) -> None:
        if self._preview_callback is not None:
            self._preview_callback(self._saved_settings.copy())
        super().reject()

    def accept(self) -> None:
        self._committed = True
        super().accept()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        if not self._committed and self._preview_callback is not None:
            self._preview_callback(self._saved_settings.copy())
        super().hideEvent(event)

    def gif_settings(self) -> dict[str, object]:
        selected_count = self.play_count.currentData()
        play_count = (
            self.custom_count.value() if selected_count == "custom"
            else FOREVER if selected_count == "original" else selected_count
        )
        speed = self.speed.currentData()
        return normalize_gif_settings(
            {
                "animation_enabled": True,
                "speed_percent": (
                    self.custom_speed.value() if speed == "custom" else speed
                ),
                "playback_mode": (
                    "loop" if selected_count == FOREVER else "original"
                ),
                "play_count": play_count,
                "start_delay_ms": self._timing_milliseconds(
                    self.start_delay, "start_delay_ms"
                ),
                "loop_pause_ms": self._timing_milliseconds(
                    self.loop_pause, "loop_pause_ms"
                ),
            }
        )

    def _timing_milliseconds(
        self, control: QtWidgets.QDoubleSpinBox, key: str,
    ) -> int:
        original = int(self._saved_settings[key])
        if (
            self._preserve_timing_precision
            and control.value() == round(original / 1000, 2)
        ):
            return original
        return round(control.value() * 1000)


class ImageTab(
    QtWidgets.QWidget
):
    image_selected = Signal(
        QtGui.QPixmap
    )

    hover_preview_image = Signal(
        QtGui.QPixmap
    )

    clear_preview = Signal()

    images_changed = Signal(str)
    cover_changed = Signal()
    animation_settings_changed = Signal(int)

    FAB_FIXED_X = 55
    FAB_CARD_GAP = 12

    # Space between Reset Images and Gallery.
    UTILITY_BUTTON_GAP = 10

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        project_state: ProjectStateController | None = None,
        project_paths: ProjectPathResolver | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(
            project_root or application_paths().workspace_root
        ).resolve()
        self.project_state = project_state
        if self.project_state is None:
            self.project_state = ProjectStateController(
                self.project_root
            )
            self.project_state.initialize()
        self.project_save_service = ProjectSaveService(
            self.project_root,
            self.project_state,
            resolver=project_paths,
        )

        self.labels = {
            1: (
                "Cover Page Image",
                "cover.png",
            ),
            2: (
                "Main Letter Image",
                "letter.png",
            ),
            3: (
                "Letter Background Image",
                "wall.png",
            ),
            4: (
                "Final Backdrop Image",
                "back.png",
            ),
        }

        self.image_paths: dict[
            int,
            Optional[str],
        ] = {
            index: None
            for index in self.labels
        }

        self._disk_fingerprint: str | None = None
        self._cover_fingerprint = self._cover_file_fingerprint()

        self._tab_active = False
        self._shutdown = False
        self._image_import_generation = 0
        self._image_import_thread: QtCore.QThread | None = None
        self._image_import_worker: _ImageImportWorker | None = None
        self._image_import_result_holder: dict[str, object] | None = None
        self._image_import_index: int | None = None

        self._compact_layout = False
        root = QtWidgets.QVBoxLayout(self)

        PRIMARY_PAGE_LAYOUT.apply(root)

        self.heading = QtWidgets.QLabel(
            "Select images for your letter"
        )
        apply_tab_heading_style(self.heading)
        self.heading.setAlignment(
            QtCore.Qt.AlignCenter
        )
        root.addWidget(self.heading)

        self.cards: dict[
            int,
            ImageAssetCard,
        ] = {}

        cards_layout = QtWidgets.QGridLayout()

        cards_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        cards_layout.setSpacing(10)
        self._cards_layout = cards_layout
        self._cards_columns = 2

        for index in (
            1,
            2,
            3,
            4,
        ):
            title, _filename = (
                self.labels[index]
            )

            card = ImageAssetCard(
                index,
                title,
                self.project_root,
                self,
            )
            card.set_playback_active(
                self._tab_active
            )

            card.select_requested.connect(
                self._pick_image_dialog
            )

            card.clear_requested.connect(
                self.clear_image
            )

            card.settings_requested.connect(
                self.open_image_settings
            )

            card.preview_requested.connect(
                self.preview_from_gallery
            )

            card.file_dropped.connect(
                self._set_image_from_drop
            )

            self.cards[index] = card

            cards_layout.addWidget(
                card,
                (index - 1) // 2,
                (index - 1) % 2,
                QtCore.Qt.AlignTop,
            )

        centered_cards = (
            QtWidgets.QHBoxLayout()
        )

        centered_cards.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        centered_cards.setSpacing(
            12
        )

        centered_cards.addStretch(1)

        centered_cards.addLayout(
            cards_layout
        )

        centered_cards.addStretch(1)

        root.addLayout(
            centered_cards
        )

        self.reset_btn = _ImageUtilityButton(
            "Reset Images",
            self._project_dir(),
            "CButton.png",
            parent=self,
            broken_artwork_filename="CButton.png",
        )

        self.open_btn = _ImageUtilityButton(
            "Gallery",
            self._project_dir(),
            "CButton.png",
            parent=self,
            broken_artwork_filename="CButton.png",
        )

        for button in (
            self.reset_btn,
            self.open_btn,
        ):
            button.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, 1.0)
            button.apply_theme_assets()
            if button.uses_artwork_presentation and not button.has_artwork:
                button.setStyleSheet(
                    "QPushButton {"
                    "background: #171a1f;"
                    "color: #ffffff;"
                    "border: 2px solid #00b8cf;"
                    "border-radius: 12px;"
                    "padding: 12px 20px;"
                    "font-weight: 800;"
                    "}"
                    "QPushButton:hover {"
                    "background: #1c252d;"
                    "border-color: #00e5ff;"
                    "}"
                    "QPushButton:pressed {"
                    "background: #101318;"
                    "}"
                )

        set_control_help(
            self.reset_btn,
            "Clear the current image selections and return the Images workspace to its default state.",
            accessible_name="Reset Images",
        )

        self.reset_btn.clicked.connect(
            self.reset_images
        )

        set_control_help(
            self.open_btn,
            "Open the working image gallery folder to review or manage letter artwork.",
            accessible_name="Gallery",
        )

        self.open_btn.clicked.connect(
            self.open_gallery_folder
        )

        # Keep the utility controls beside the cards. The tab's vertical space
        # is intentionally compact, so placing large controls below the cards
        # clips their artwork and lets their widget rectangles overlap a card.
        utility_panel = _ImageUtilityPanel(
            self.reset_btn,
            self.open_btn,
            self.UTILITY_BUTTON_GAP,
            self,
        )
        self._utility_panel = utility_panel

        centered_cards.insertWidget(
            1,
            utility_panel,
        )
        self._centered_cards = centered_cards
        self._minimum_cards_width = 380
        self.setMinimumWidth(self._minimum_cards_width)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self.status = QtWidgets.QLabel()

        self.status.setFont(
            QtGui.QFont(
                "Segoe UI",
                10,
            )
        )

        self.status.setStyleSheet(
            "color: #8995a3;"
        )

        root.addWidget(
            self.status
        )

        self._status_clear_timer = (
            QtCore.QTimer(self)
        )

        self._status_clear_timer.setSingleShot(
            True
        )

        self._status_clear_timer.timeout.connect(
            self.status.clear
        )

        root.addStretch(1)

        self._fab_surface: QtWidgets.QWidget = (
            self
        )

        self.pwrite_fab = StaticFab(
            self,
            self,
        )

        set_control_help(
            self.pwrite_fab,
            "Open or close Prompt Writer to create and manage image-generation prompts for your letter.",
            accessible_name="Prompt Writer",
        )

        self.apply_theme_assets(
            getattr(self.window(), "theme_service", None)
        )

        self.pwrite_fab.clicked.connect(
            self._open_prompt_writer_bridge
        )

        self.pwrite_fab.hide()

        self.refresh_cards()
        self._disk_fingerprint = image_fingerprint(
            self._project_dir()
        )

    # ─────────────────────────────────────────────────────────────────────
    # Paths and synchronization
    # ─────────────────────────────────────────────────────────────────────

    def _project_dir(self) -> str:
        return str(self.project_root)

    def apply_theme_assets(self, theme_service: object | None = None) -> None:
        """Refresh cloud buttons, image frames, and Prompt Writer artwork."""
        for button_name in ("reset_btn", "open_btn"):
            button = getattr(self, button_name, None)
            refresher = getattr(button, "apply_theme_assets", None)
            if callable(refresher):
                refresher(theme_service)

        for card in self.cards.values():
            card.apply_theme_assets(theme_service, self.project_root)

        prompt_button = getattr(self, "pwrite_fab", None)
        if prompt_button is None:
            return

        fallback_path = application_paths(self._project_dir()).app_resource_path(
            "themes/cyber_forge/prompt_writer/Pwrite.png"
        )
        icon_path = fallback_path
        resolver = getattr(theme_service, "resolve_asset", None)
        if callable(resolver):
            try:
                icon_path = resolver("prompt_writer/Pwrite.png")
            except (TypeError, ValueError):
                icon_path = fallback_path

        prompt_writer_icon = QIcon(str(icon_path))
        if not prompt_writer_icon.isNull():
            prompt_button.setText("")
            prompt_button.setIcon(prompt_writer_icon)
            prompt_button.setIconSize(QSize(200, 200))
            return

        prompt_button.setIcon(QIcon())
        prompt_button.setText("PROMPT\nWRITER")
        if not bool(prompt_button.property("promptWriterTextFallback")):
            prompt_button.setProperty("promptWriterTextFallback", True)
            tokens = getattr(theme_service, "tokens", None)
            fallback_color = getattr(tokens, "accent_bright", "#00e5e5")
            prompt_button.setStyleSheet(
                prompt_button.styleSheet()
                + (
                    "#PWriteFab {"
                    f"color: {fallback_color};"
                    "font: 700 18px 'Segoe UI';"
                    "}"
                )
            )

    def _user_pages_dir(self) -> str:
        return os.path.join(
            self._project_dir(),
            "gallery",
            "user",
            "pages",
        )

    def _cover_file_fingerprint(self) -> str:
        return file_fingerprint(
            Path(self._user_pages_dir()) / "cover.png"
        )

    def _emit_cover_change_if_needed(self) -> bool:
        current = self._cover_file_fingerprint()
        if current == self._cover_fingerprint:
            return False
        self._cover_fingerprint = current
        self.cover_changed.emit()
        return True

    def refresh_cards(self) -> bool:
        pages_directory = self._user_pages_dir()
        reconciled = reconcile_external_image_assets(
            pages_directory
        )
        manifest = load_image_manifest(
            pages_directory
        )
        for index, (
            _title,
            filename,
        ) in self.labels.items():
            preview_path = os.path.join(
                pages_directory,
                filename,
            )
            slot = INDEX_TO_SLOT[index]
            record = manifest["slots"].get(slot, {})
            animated_gif = (
                isinstance(record, dict)
                and record.get("asset_type")
                == "animated_gif"
            )
            asset_path = (
                os.path.join(
                    pages_directory,
                    f"{slot}.gif",
                )
                if animated_gif
                else preview_path
            )
            thumbnail_path = os.path.join(
                self._user_pages_dir(),
                str(record.get("thumbnail_file", filename)),
            )

            if (
                os.path.isfile(preview_path)
                and os.path.isfile(asset_path)
            ):
                self.image_paths[index] = asset_path
                self.cards[index].set_asset_path(
                    asset_path,
                    animated_gif=animated_gif,
                    settings=record.get("settings"),
                    preview_path=thumbnail_path,
                    animate_gif=self._tab_active,
                    embedded_play_count=(
                        record.get("gif", {}).get("embedded_play_count")
                        if animated_gif and isinstance(record.get("gif"), dict)
                        else None
                    ),
                )
                continue

            self.image_paths[
                index
            ] = None

            self.cards[
                index
            ].clear_pixmap()

        self._sync_image_action_state()
        return reconciled

    def has_meaningful_images(self) -> bool:
        """Return whether any user-selected image asset is currently present."""
        return any(
            bool(path) and Path(path).is_file()
            for path in self.image_paths.values()
        )

    def _sync_image_action_state(self) -> None:
        available = self.has_meaningful_images()
        importing = self._image_import_thread is not None
        for index, card in self.cards.items():
            selected = bool(
                self.image_paths[index]
                and Path(str(self.image_paths[index])).is_file()
            )
            card.clear_btn.set_action_state(
                broken=not selected,
                invisible=importing,
            )
            set_control_invisible(
                card.thumbnail,
                importing,
                available=True,
            )

        self.reset_btn.set_action_state(
            broken=not available,
            invisible=importing,
        )
        self.open_btn.set_action_state(
            broken=not available,
            invisible=importing,
        )

    def sync_from_disk(
        self,
        *,
        force: bool = False,
    ) -> bool:
        current = image_fingerprint(
            self._project_dir()
        )

        changed = (
            force
            or self._disk_fingerprint
            != current
        )

        if not changed:
            return False

        reconciled = self.refresh_cards()
        self._disk_fingerprint = (
            image_fingerprint(
                self._project_dir()
            )
            if reconciled
            else current
        )
        self._emit_cover_change_if_needed()

        self.images_changed.emit(
            "disk"
        )

        return changed

    def sync_to_disk(self) -> bool:
        current = image_fingerprint(
            self._project_dir()
        )

        if current == self._disk_fingerprint:
            return False

        reconciled = self.refresh_cards()
        self._disk_fingerprint = (
            image_fingerprint(
                self._project_dir()
            )
            if reconciled
            else current
        )

        self.images_changed.emit(
            "saved"
        )
        self._emit_cover_change_if_needed()
        return True

    def refresh_from_disk(self) -> None:
        self.sync_from_disk(
            force=True
        )

    def activate_for_tab_change(
        self,
    ) -> None:
        if self._tab_active:
            return

        self._tab_active = True
        self.sync_from_disk()
        for card in self.cards.values():
            card.set_playback_active(True)

    def deactivate_for_tab_change(
        self,
    ) -> None:
        if not self._tab_active:
            return

        self._tab_active = False
        for card in self.cards.values():
            card.set_playback_active(False)
        self.sync_to_disk()

    def focus_asset_slot(
        self,
        target: str,
    ) -> None:
        normalized = (
            str(target or "")
            .strip()
            .lower()
            .replace(
                "-",
                "_",
            )
        )

        mapping = {
            "cover": 1,
            "cover_image": 1,
            "cover_page": 1,
            "cover_page_image": 1,
            "letter": 2,
            "main": 2,
            "main_image": 2,
            "main_letter": 2,
            "main_letter_image": 2,
            "wall": 3,
            "background": 3,
            "letter_background": 3,
            "letter_background_image": 3,
            "back": 4,
            "backdrop": 4,
            "final_backdrop": 4,
            "final_backdrop_image": 4,
        }

        index = mapping.get(
            normalized
        )

        if index is None:
            return

        card = self.cards[index]

        card.thumbnail.setFocus(
            QtCore.Qt.OtherFocusReason
        )

        card.ensurePolished()

    # ─────────────────────────────────────────────────────────────────────
    # Prompt Writer positioning
    # ─────────────────────────────────────────────────────────────────────

    def _find_preview_surface(
        self,
    ) -> Optional[
        QtWidgets.QWidget
    ]:
        window = self.window()

        if not isinstance(
            window,
            QtWidgets.QWidget,
        ):
            return None

        return window.findChild(
            QtWidgets.QWidget,
            "PreviewFrame",
        )

    def _cards_top_in_window(
        self,
        window: QtWidgets.QWidget,
    ) -> Optional[int]:
        card_tops: list[int] = []

        for card in self.cards.values():
            if not card.isVisible():
                continue

            position = card.mapTo(
                window,
                QPoint(
                    0,
                    0,
                ),
            )

            card_tops.append(
                position.y()
            )

        if not card_tops:
            return None

        return min(card_tops)

    def _position_prompt_writer_button(
        self,
    ) -> None:
        window = self.window()

        if not isinstance(
            window,
            QtWidgets.QWidget,
        ):
            self.pwrite_fab.hide()
            return

        if not self.isVisibleTo(window):
            self.pwrite_fab.hide()
            return

        preview_frame = (
            self._find_preview_surface()
        )

        if preview_frame is None:
            self.pwrite_fab.hide()
            return

        if (
            self.pwrite_fab.parent()
            is not window
        ):
            self.pwrite_fab.setParent(
                window
            )

        self._fab_surface = window

        self.pwrite_fab.set_surface(
            window
        )

        preview_position = (
            preview_frame.mapTo(
                window,
                QPoint(
                    0,
                    0,
                ),
            )
        )

        # Scale the launcher with the shorter preview, keeping it below the tabs.
        side = min(200, max(64, preview_frame.height()))
        self.pwrite_fab.setIconSize(QSize(side, side))
        self.pwrite_fab.setFixedSize(side, side)

        x_position = max(
            0,
            self.FAB_FIXED_X,
        )

        y_position = (
            preview_position.y()
            + (
                preview_frame.height()
                - self.pwrite_fab.height()
            )
            // 2
        )

        cards_top = (
            self._cards_top_in_window(
                window
            )
        )

        if cards_top is not None:
            protected_button = getattr(
                window,
                "protected_new_project_btn",
                None,
            )
            protected_height = (
                protected_button.height() + self.FAB_CARD_GAP
                if protected_button is not None
                and protected_button.isVisible()
                else 0
            )
            maximum_y = (
                cards_top
                - self.pwrite_fab.height()
                - self.FAB_CARD_GAP
                - protected_height
            )

            y_position = min(
                y_position,
                maximum_y,
            )

        self.pwrite_fab.move(
            QPoint(
                x_position,
                max(
                    0,
                    max(preview_position.y(), y_position),
                ),
            )
        )

        self.pwrite_fab.clamp_to_surface()
        self.pwrite_fab.show()
        self.pwrite_fab.raise_()
        position_new_project = getattr(
            window,
            "_position_protected_new_project_button",
            None,
        )
        if callable(position_new_project):
            position_new_project()

    def _schedule_prompt_writer_position(
        self,
    ) -> None:
        QtCore.QTimer.singleShot(
            0,
            self._position_prompt_writer_button,
        )

    def showEvent(
            self,
            event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(event)

        self.activate_for_tab_change()
        self._schedule_prompt_writer_position()

    def hideEvent(
            self,
            event: QtGui.QHideEvent,
    ) -> None:
        self.deactivate_for_tab_change()

        if hasattr(
                self,
                "pwrite_fab",
        ):
            self.pwrite_fab.hide()

        super().hideEvent(event)

    def set_compact_layout(self, compact: bool) -> None:
        if self._compact_layout == compact:
            return
        self._compact_layout = compact
        if compact:
            self.layout().setContentsMargins(12, 4, 12, 4)
            self.layout().setSpacing(4)
        else:
            PRIMARY_PAGE_LAYOUT.apply(self.layout())
        self._cards_layout.setSpacing(6 if compact else 10)
        self._utility_panel.set_gap(6 if compact else self.UTILITY_BUTTON_GAP)
        self._fit_cards()
        self.updateGeometry()

    def _columns_for_width(self, width: int) -> int:
        margins = self.layout().contentsMargins()
        gap = self._cards_layout.horizontalSpacing()
        utility_width = max(self.reset_btn.width(), self.open_btn.width())
        cards_width = (
            width - margins.left() - margins.right()
            - utility_width - self._centered_cards.spacing()
        )
        return min(len(self.cards), max(1, (cards_width + gap) // (164 + gap)))

    def minimumSizeHint(self) -> QtCore.QSize:
        if hasattr(self, "_minimum_cards_width"):
            return QtCore.QSize(self._minimum_cards_width, self.heightForWidth(self.width()))
        return super().minimumSizeHint()

    def _card_area_overhead(self) -> int:
        if not hasattr(self, "status"):
            return 88
        layout = self.layout()
        margins = layout.contentsMargins()
        return (margins.top() + margins.bottom()
            + self.heading.sizeHint().height() + self.status.sizeHint().height()
            + 2 * layout.spacing())

    def heightForWidth(self, width: int) -> int:
        if not hasattr(self, "_minimum_cards_width"):
            return super().heightForWidth(width)
        columns = self._columns_for_width(width)
        rows = (len(self.cards) + columns - 1) // columns
        return self._card_area_overhead() + rows * 160 + (rows - 1) * self._cards_layout.verticalSpacing()

    def _fit_cards(self) -> None:
        scale = min(1.0, max(0.45, (self.width() - 40) / 1600))
        if self._compact_layout:
            scale = min(scale, 0.45)
        for button in (self.reset_btn, self.open_btn):
            button.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, scale)
            apply_button_tier(button, ButtonTier.LARGE)
        utility_width = max(self.reset_btn.width(), self.open_btn.width())
        self._utility_panel.setFixedWidth(utility_width)
        self._utility_panel.position_buttons()
        columns = self._columns_for_width(self.width())
        rows = (len(self.cards) + columns - 1) // columns
        margins = self.layout().contentsMargins()
        width = min(300, max(164, (
            self.width() - margins.left() - margins.right() - utility_width
            - self._centered_cards.spacing() - (columns - 1) * self._cards_layout.horizontalSpacing()
        ) // columns))
        height = min(270, max(160, (self.height() - self._card_area_overhead()
            - (rows - 1) * self._cards_layout.verticalSpacing()) // rows))
        for card in self.cards.values():
            card.set_card_size(QtCore.QSize(width, height))
        if columns != self._cards_columns:
            self._cards_columns = columns
            for card in self.cards.values():
                self._cards_layout.removeWidget(card)
            for index, card in self.cards.items():
                self._cards_layout.addWidget(
                    card, (index - 1) // columns, (index - 1) % columns,
                    QtCore.Qt.AlignTop,
                )
            self.updateGeometry()
        self._centered_cards.invalidate()
        self.layout().activate()
        self._utility_panel.position_buttons()

    def resizeEvent(
            self,
            event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(event)

        if hasattr(self, "_minimum_cards_width"):
            self._fit_cards()

        if not self.isVisible():
            return

        if hasattr(
                self,
                "pwrite_fab",
        ):
            self._schedule_prompt_writer_position()

    # ─────────────────────────────────────────────────────────────────────
    # Image selection and storage
    # ─────────────────────────────────────────────────────────────────────

    def _pick_image_dialog(
        self,
        index: int,
    ) -> None:
        if index not in self.labels:
            return
        if not self.image_paths.get(index):
            self.open_stock_gallery(index)
            return

        self._browse_image_file(index)

    def _browse_image_file(self, index: int) -> None:
        path, _selected_filter = (
            QtWidgets.QFileDialog.getOpenFileName(
                self,
                (
                    f"Select "
                    f"{self.labels[index][0]}"
                ),
                _downloads_directory(),
                (
                    "Images "
                    "(*.png *.jpg *.jpeg *.bmp *.gif)"
                ),
            )
        )

        if path:
            self.set_image_path(
                index,
                path,
            )

    def _stock_image_paths(self, index: int) -> tuple[Path, ...]:
        slot = INDEX_TO_SLOT.get(index)
        filenames = STOCK_IMAGE_FILES.get(index, ())
        if not slot:
            return ()
        stock_directory = (
            application_paths(self.project_root).stock_images_root / slot
        )
        return tuple(
            path
            for filename in filenames
            if (path := stock_directory / filename).is_file()
        )

    def open_stock_gallery(self, index: int) -> None:
        if index not in self.labels:
            return
        recent_entries = tuple(
            (path, label) for value, label in recent_media(
                self.project_root,
                self.project_state.identity.project_id,
                f"image:{INDEX_TO_SLOT[index]}",
            )
            if (path := Path(value)).is_file()
        )
        image_paths = () if recent_entries else self._stock_image_paths(index)
        if not recent_entries and len(image_paths) != 3:
            self._show_temporary_status(
                f"Stock images are unavailable for {self.labels[index][0]}.",
                5000,
            )
            return
        dialog = StockImageDialog(
            self.labels[index][0],
            image_paths,
            self,
            recent_paths=tuple(path for path, _label in recent_entries),
            recent_labels=tuple(label for _path, label in recent_entries),
            allow_browse=True,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        if dialog.browse_requested:
            self._browse_image_file(index)
            return
        selected_path = dialog.selected_path()
        if selected_path is not None:
            self.set_image_path(index, str(selected_path))

    def _set_image_from_drop(
        self,
        index: int,
        path: str,
    ) -> None:
        if os.path.isfile(path):
            self.set_image_path(
                index,
                path,
            )

    def _commit_image_change(
        self,
        reason: str,
    ) -> None:
        self._disk_fingerprint = (
            image_fingerprint(
                self._project_dir()
            )
        )
        self._emit_cover_change_if_needed()
        self._sync_image_action_state()

        self.images_changed.emit(
            reason
        )

    def set_image_path(
        self,
        index: int,
        source_path: str,
    ) -> None:
        if self._shutdown or index not in self.labels:
            return
        if not self.project_state.is_project_ready:
            self._show_temporary_status(
                "A recipient is required before saving images.",
                5000,
            )
            return
        if self._image_import_thread is not None:
            self._show_temporary_status(
                "Another image is already being processed.",
                3000,
            )
            return

        source = Path(source_path).resolve()
        if not source.is_file():
            self._show_temporary_status(
                f"Image file does not exist: {source.name}",
                5000,
            )
            return

        project_pages_directory: Path | None = None
        try:
            eligibility = self.project_save_service.save_eligibility()
            if eligibility.can_save:
                context = self.project_save_service.current_context()
                if context.autosave_directory.is_dir():
                    project_pages_directory = (
                        context.autosave_directory / "pages"
                    ).resolve()
        except Exception as error:
            self._show_temporary_status(
                f"The image project is not ready: {error}",
                5000,
            )
            return

        baseline = image_asset_revision(self._user_pages_dir())
        identity = self.project_state.identity
        project_identity = (
            identity.recipient_id,
            identity.project_id,
        )
        self._image_import_generation += 1
        generation = self._image_import_generation
        holder: dict[str, object] = {}
        thread = QtCore.QThread(self)
        worker = _ImageImportWorker(
            Path(self._user_pages_dir()),
            project_pages_directory,
            index,
            INDEX_TO_SLOT[index],
            source,
            project_identity,
            baseline,
            generation,
            holder,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.prepared.connect(
            self._image_import_prepared,
            QtCore.Qt.QueuedConnection,
        )
        worker.failed.connect(
            self._image_import_failed,
            QtCore.Qt.QueuedConnection,
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(
            thread.quit,
            QtCore.Qt.DirectConnection,
        )
        thread.finished.connect(
            self._image_import_thread_finished,
            QtCore.Qt.QueuedConnection,
        )
        thread.finished.connect(thread.deleteLater)
        self._image_import_thread = thread
        self._image_import_worker = worker
        self._image_import_result_holder = holder
        self._image_import_index = index
        self._sync_image_action_state()
        self._show_temporary_status(
            f"Processing {source.name}…",
            0,
        )
        thread.start()

    @QtCore.Slot(object)
    def _image_import_prepared(self, result: object) -> None:
        if not isinstance(result, _PreparedImageImportResult):
            return
        holder = self._image_import_result_holder
        if holder is None or holder.get("result") is not result:
            return
        holder.pop("result", None)

        if self._shutdown or result.generation != self._image_import_generation:
            result.prepared.abort()
            return
        if not self.project_state.is_project_ready:
            result.prepared.abort()
            self._show_temporary_status(
                "The project changed before the image could be saved.",
                5000,
            )
            return
        current_identity = self.project_state.identity
        if result.project_identity != (
            current_identity.recipient_id,
            current_identity.project_id,
        ):
            result.prepared.abort()
            self._show_temporary_status(
                "The active letter changed before the image could be saved.",
                5000,
            )
            return

        try:
            if result.project_pages_directory is not None:
                context = self.project_save_service.current_context()
                current_project_pages = (
                    context.autosave_directory / "pages"
                ).resolve()
                if current_project_pages != result.project_pages_directory:
                    raise RuntimeError(
                        "The active letter changed while the image was processing."
                    )
            if (
                image_asset_revision(self._user_pages_dir())
                != result.baseline_fingerprint
            ):
                raise RuntimeError(
                    "Images changed while the selection was processing."
                )

            self.cards[result.index].release_asset_handle()
            result.prepared.commit_workspace()
            if self.project_save_service.can_save():
                if result.project_pages_directory is not None:
                    result.prepared.commit_project()
                    self.project_save_service.copy_workspace_file(
                        Path(self._user_pages_dir()) / IMAGE_MANIFEST_NAME,
                        Path("pages") / IMAGE_MANIFEST_NAME,
                    )
                else:
                    self._persist_image_record_to_project(
                        result.index,
                        result.prepared.record,
                    )
            else:
                result.prepared.discard_project()

            record = result.prepared.record
            _label, filename = self.labels[result.index]
            pages_directory = Path(self._user_pages_dir())
            destination_path = pages_directory / filename
            pixmap = QtGui.QPixmap.fromImage(result.preview_image)
            if pixmap.isNull():
                raise ValueError("The selected image preview is invalid.")

            animated_gif = record["asset_type"] == "animated_gif"
            self.image_paths[result.index] = str(
                pages_directory / str(record["source_file"])
            )
            self.cards[result.index].set_asset_path(
                self.image_paths[result.index] or str(destination_path),
                animated_gif=animated_gif,
                settings=record.get("settings"),
                preview_path=str(
                    pages_directory
                    / str(record.get("thumbnail_file", filename))
                ),
                animate_gif=True,
                preview_pixmap=pixmap,
            )
            self.image_selected.emit(pixmap)
            self._commit_image_change("selected")
            result.prepared.finalize()
            self._show_temporary_status(
                f"{record['source_file']} saved."
            )
            play_ui_sound(UiSound.ADDED)
        except Exception as error:
            self.cards[result.index].release_asset_handle()
            result.prepared.rollback()
            self.cards[result.index].set_asset_state("warning")
            self._show_temporary_status(
                f"Failed to process {self.labels[result.index][1]}: {error}",
                5000,
            )
        else:
            if result.source_path is not None:
                try:
                    remember_image_copy(
                        self.project_root,
                        result.project_identity[1],
                        INDEX_TO_SLOT[result.index],
                        pages_directory / str(record["source_file"]),
                        result.source_path.name,
                    )
                except (OSError, ValueError):
                    logging.getLogger(__name__).exception(
                        "Could not save recent image history."
                    )

    def _persist_image_record_to_project(
        self,
        index: int,
        record: MutableMapping[str, object],
    ) -> None:
        _label, filename = self.labels[index]
        slot = INDEX_TO_SLOT[index]
        pages_directory = Path(self._user_pages_dir())
        self.project_save_service.copy_workspace_file(
            pages_directory / filename,
            Path("pages") / filename,
        )
        source_filename = str(record["source_file"])
        if record["asset_type"] == "animated_gif":
            self.project_save_service.copy_workspace_file(
                pages_directory / source_filename,
                Path("pages") / source_filename,
            )
            thumbnail_filename = str(record["thumbnail_file"])
            self.project_save_service.copy_workspace_file(
                pages_directory / thumbnail_filename,
                Path("pages") / thumbnail_filename,
            )
        else:
            for obsolete_filename in (
                f"{slot}.gif",
                f"{slot}.thumbnail.gif",
            ):
                self.project_save_service.delete_project_file(
                    Path("pages") / obsolete_filename
                )
        self.project_save_service.copy_workspace_file(
            pages_directory / IMAGE_MANIFEST_NAME,
            Path("pages") / IMAGE_MANIFEST_NAME,
        )

    @QtCore.Slot(int, int, str)
    def _image_import_failed(
        self,
        generation: int,
        index: int,
        message: str,
    ) -> None:
        if self._shutdown or generation != self._image_import_generation:
            return
        if index in self.cards:
            self.cards[index].set_asset_state("warning")
        self._show_temporary_status(
            f"Failed to process image: {message}",
            5000,
        )

    @QtCore.Slot()
    def _image_import_thread_finished(self) -> None:
        thread = self._image_import_thread
        if thread is None:
            return
        holder = self._image_import_result_holder
        self._image_import_thread = None
        self._image_import_worker = None
        self._image_import_result_holder = None
        if holder is not None:
            result = holder.get("result")
            if isinstance(result, _PreparedImageImportResult):
                self._image_import_result_holder = holder
                self._image_import_prepared(result)
                self._image_import_result_holder = None
        self._image_import_index = None
        self._sync_image_action_state()

    def open_image_settings(
        self,
        index: int,
    ) -> None:
        if index not in self.labels:
            return
        slot = INDEX_TO_SLOT[index]
        manifest = load_image_manifest(
            self._user_pages_dir()
        )
        record = manifest["slots"].get(slot)
        if not isinstance(record, dict):
            return
        animated_gif = (
            record.get("asset_type")
            == "animated_gif"
        )
        if not animated_gif:
            return
        gif_record = record.get("gif")
        embedded_count = (
            gif_record.get("embedded_play_count", FOREVER)
            if isinstance(gif_record, dict) else FOREVER
        )

        def preview_settings(values: dict[str, object]) -> None:
            card = getattr(self, "cards", {}).get(index)
            if card is None:
                return
            asset_path = os.path.join(self._user_pages_dir(), f"{slot}.gif")
            preview_path = os.path.join(
                self._user_pages_dir(),
                str(record.get("thumbnail_file", f"{slot}.gif")),
            )
            card.set_asset_path(
                asset_path,
                animated_gif=True,
                settings=values,
                preview_path=preview_path,
                embedded_play_count=embedded_count,
            )

        dialog = ImageSettingsDialog(
            self.labels[index][0],
            animated_gif=True,
            settings=record.get("settings"),
            embedded_play_count=embedded_count,
            preview_callback=preview_settings,
            parent=self,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            update_slot_gif_settings(
                self._user_pages_dir(),
                slot,
                dialog.gif_settings(),
            )
            self.project_save_service.copy_workspace_file(
                Path(self._user_pages_dir())
                / IMAGE_MANIFEST_NAME,
                Path("pages") / IMAGE_MANIFEST_NAME,
            )
            self.refresh_cards()
        except Exception as error:
            preview_settings(normalize_gif_settings(record.get("settings")))
            self._show_temporary_status(
                f"Could not save {slot} GIF settings: {error}",
                5000,
            )
            return
        self._commit_image_change("settings")
        self.animation_settings_changed.emit(index)
        self._show_temporary_status(
            f"{self.labels[index][0]} settings saved."
        )

    def clear_image(
        self,
        index: int,
    ) -> None:
        if index not in self.labels:
            return
        selected_path = self.image_paths.get(index)
        if not selected_path or not Path(str(selected_path)).is_file():
            self._sync_image_action_state()
            return

        _label, filename = (
            self.labels[index]
        )

        slot = INDEX_TO_SLOT[index]

        try:
            self.cards[index].release_asset_handle()
            clear_slot_asset(
                self._user_pages_dir(),
                slot,
            )
            if self.project_state.is_project_ready:
                for project_filename in (
                    filename,
                    f"{slot}.gif",
                    f"{slot}.thumbnail.gif",
                ):
                    self.project_save_service.delete_project_file(
                        Path("pages") / project_filename
                    )
                self.project_save_service.copy_workspace_file(
                    Path(self._user_pages_dir())
                    / IMAGE_MANIFEST_NAME,
                    Path("pages") / IMAGE_MANIFEST_NAME,
                )

        except OSError as error:
            self._show_temporary_status(
                (
                    f"Could not clear "
                    f"{filename}: {error}"
                ),
                5000,
            )

            return

        self.image_paths[index] = None

        self.cards[
            index
        ].clear_pixmap()

        self.clear_preview.emit()

        self._commit_image_change(
            "cleared"
        )

        self._show_temporary_status(
            f"{filename} cleared."
        )
        play_ui_sound(UiSound.REMOVED)

    def preview_from_gallery(
        self,
        index: int,
    ) -> None:
        if index not in self.labels:
            return

        path = os.path.join(
            self._user_pages_dir(),
            self.labels[index][1],
        )

        pixmap = QtGui.QPixmap(
            path
        )

        if not pixmap.isNull():
            self.hover_preview_image.emit(
                pixmap
            )

    def reset_images(self) -> None:
        if not self.has_meaningful_images():
            self._sync_image_action_state()
            return
        confirmation = _ResetImagesConfirmationDialog(
            self
        )
        if (
            confirmation.exec()
            != QtWidgets.QDialog.DialogCode.Accepted
        ):
            return

        self._reset_images_confirmed()

    def _reset_images_confirmed(self) -> None:
        for index in self.labels:
            _label, filename = (
                self.labels[index]
            )

            slot = INDEX_TO_SLOT[index]

            try:
                self.cards[index].release_asset_handle()
                clear_slot_asset(
                    self._user_pages_dir(),
                    slot,
                )
                if self.project_state.is_project_ready:
                    for project_filename in (
                        filename,
                        f"{slot}.gif",
                        f"{slot}.thumbnail.gif",
                    ):
                        self.project_save_service.delete_project_file(
                            Path("pages") / project_filename
                        )

            except OSError:
                pass

            self.image_paths[
                index
            ] = None

            self.cards[
                index
            ].clear_pixmap()

        if self.project_state.is_project_ready:
            try:
                self.project_save_service.copy_workspace_file(
                    Path(self._user_pages_dir())
                    / IMAGE_MANIFEST_NAME,
                    Path("pages") / IMAGE_MANIFEST_NAME,
                )
            except OSError:
                pass

        self.clear_preview.emit()

        self._commit_image_change(
            "reset"
        )

        self._show_temporary_status(
            "All images cleared."
        )
        play_ui_sound(UiSound.REMOVED)

    def reset_project_images(self) -> None:
        """Refresh the empty image workspace after New Project commits."""
        self.deactivate_for_tab_change()
        self.refresh_from_disk()
        self.clear_preview.emit()
        self._show_temporary_status(
            "No images selected."
        )

    def open_gallery_folder(
        self,
    ) -> None:
        pages_directory = (
            self._user_pages_dir()
        )

        os.makedirs(
            pages_directory,
            exist_ok=True,
        )

        opened = (
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    pages_directory
                )
            )
        )

        if opened:
            self._show_temporary_status(
                "Image gallery opened."
            )

        else:
            self._show_temporary_status(
                (
                    "Could not open the "
                    "image gallery folder."
                ),
                5000,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Temporary status and Prompt Writer bridge
    # ─────────────────────────────────────────────────────────────────────

    def _show_temporary_status(
        self,
        message: str,
        duration_ms: int = 3000,
    ) -> None:
        self._status_clear_timer.stop()

        self.status.setText(
            message
        )

        if duration_ms > 0:
            self._status_clear_timer.start(
                duration_ms
            )

    def _open_prompt_writer_bridge(
        self,
    ) -> None:
        window = self.window()

        toggle = getattr(
            window,
            "toggle_prompt_writer",
            None,
        )

        if not callable(toggle):
            self._show_temporary_status(
                (
                    "Prompt Writer toggle "
                    "not found on the main window."
                ),
                5000,
            )

            return

        try:
            is_open = toggle()

        except Exception as error:
            self._show_temporary_status(
                (
                    f"Could not toggle "
                    f"Prompt Writer: {error}"
                ),
                5000,
            )

            return

        self._show_temporary_status(
            (
                "Prompt Writer opened."
                if is_open
                else "Prompt Writer closed."
            )
        )

    def _stop_image_import(self, timeout_ms: int | None = 5000) -> bool:
        thread = self._image_import_thread
        if thread is None:
            return True

        self._image_import_generation += 1
        worker = self._image_import_worker
        if worker is not None:
            worker.cancel()
        thread.requestInterruption()
        if thread.isRunning():
            stopped = (
                thread.wait()
                if timeout_ms is None
                else thread.wait(max(0, int(timeout_ms)))
            )
            if not stopped:
                thread.setParent(None)
                self._image_import_thread = None
                self._image_import_worker = None
                self._image_import_result_holder = None
                self._image_import_index = None
                return False

        holder = self._image_import_result_holder
        if holder is not None:
            result = holder.pop("result", None)
            if isinstance(result, _PreparedImageImportResult):
                result.prepared.abort()
        self._image_import_thread = None
        self._image_import_worker = None
        self._image_import_result_holder = None
        self._image_import_index = None
        return True

    def shutdown(self, timeout_ms: int | None = 5000) -> bool:
        if self._shutdown:
            return self._image_import_thread is None
        self._shutdown = True
        self._status_clear_timer.stop()
        self.pwrite_fab.hide()

        stopped = self._stop_image_import(timeout_ms)

        try:
            self.sync_to_disk()
        except Exception:
            logging.getLogger(__name__).debug(
                "Best-effort operation failed.",
                exc_info=True,
            )
        finally:
            for card in self.cards.values():
                card.release_asset_handle()
        return stopped

    def prepare_for_project_restore(self, timeout_ms: int = 5000) -> None:
        if not self._stop_image_import(timeout_ms):
            raise RuntimeError(
                "Image processing did not stop before the project changed."
            )
        for card in self.cards.values():
            card.release_asset_handle()


__all__ = [
    "ImageAssetCard",
    "ImageTab",
    "StaticFab",
]
