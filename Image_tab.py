# File: Image_tab.py
"""
Shared image preview, stock tray and GIF settings widgets for Letter Smith.

The public ImageTab entry point uses images_grid.py and images_workspace.py over the
canonical project session. Slot preview files are disposable compatibility
views; page_artwork.py stages immutable source artwork by page ID.
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
from page_controls import PageControls, page_controller
from page_layout import LAYOUT_FILE, PAGE_INDICES, page_labels
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
        self.setWindowTitle(f"Choose {title} artwork")
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or self.screen()
        available = screen.availableGeometry().size()
        count = max(len(recent_paths), len(image_paths), 1)
        self._columns = min(5, max(2, (available.width() - 64) // 144), max(3, count))
        self.resize(self._columns * 144 + 52,
                    min(available.height() - 40, 156 + ((count + self._columns - 1) // self._columns) * 164
                        + (192 if recent_paths and image_paths else 0)))
        self._selected_path: Path | None = None
        self.browse_requested = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QLabel(f"Choose {title} artwork")
        heading.setObjectName("ImageChoiceHeading")
        header.addWidget(heading, 1)
        close = QtWidgets.QToolButton()
        close.setText("×")
        close.setAccessibleName("Close image choices")
        close.setToolTip("Close (Esc)")
        close.setFixedSize(28, 28)
        close.setAutoRaise(True)
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)

        self._images: QtWidgets.QListWidget | None = None
        self._recent_images: QtWidgets.QListWidget | None = None
        if recent_paths:
            layout.addWidget(QtWidgets.QLabel(f"Recently used · {len(recent_paths)} images"))
            self._recent_images = self._make_image_list(
                recent_paths, recent_labels
            )
            self._recent_images.setAccessibleName(f"Recent {title} images")
            layout.addWidget(self._recent_images, 1)
        if image_paths or not recent_paths:
            layout.addWidget(QtWidgets.QLabel("Stock images"))
            self._images = self._make_image_list(
                image_paths, tuple(f"{title} {index+1}" for index in range(len(image_paths)))
            )
            self._images.setAccessibleName(f"Stock {title} images")
            layout.addWidget(self._images, 1)
        if allow_browse:
            browse = QtWidgets.QPushButton("Import image…")
            browse.clicked.connect(self._browse)
            layout.addWidget(browse)
        choices = self._recent_images or self._images
        if choices is not None and choices.count():
            choices.setCurrentRow(0)
            self.setFocusProxy(choices)
        self.setStyleSheet(
            "QDialog#StockImageTray{background:palette(window);border:1px solid palette(mid);"
            "border-radius:12px;}"
            "QLabel{color:palette(window-text);}"
            "QLabel#ImageChoiceHeading{font-size:16px;font-weight:600;}"
            "QPushButton{min-height:30px;padding:2px 12px;border-radius:7px;}"
            "QListWidget{background:transparent;border:none;}"
            "QListWidget::item{background:palette(base);border:1px solid palette(mid);"
            "border-radius:8px;padding:5px;margin:3px;color:palette(text);}"
            "QListWidget::item:hover{border:1px solid palette(highlight);}"
            "QListWidget::item:selected{border:2px solid palette(highlight);"
            "background:palette(alternate-base);color:palette(text);}"
        )

    def _make_image_list(
        self,
        paths: tuple[Path, ...],
        labels: tuple[str, ...] = (),
    ) -> QtWidgets.QListWidget:
        images = QtWidgets.QListWidget()
        font = QtGui.QFont(self.font())
        font.setPointSizeF(max(9, font.pointSizeF()))
        images.setFont(font)
        images.setViewMode(QtWidgets.QListView.IconMode)
        images.setIconSize(QtCore.QSize(116, 120))
        images.setGridSize(QtCore.QSize(144, 164))
        images.setWordWrap(False)
        images.setTextElideMode(QtCore.Qt.ElideMiddle)
        images.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        images.setSpacing(0)
        images.setMovement(QtWidgets.QListView.Static)
        images.setResizeMode(QtWidgets.QListView.Adjust)
        images.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        images.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        images.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        for index, path in enumerate(paths):
            label = labels[index] if index < len(labels) else path.stem
            item = QtWidgets.QListWidgetItem(QIcon(str(path)), label)
            item.setData(QtCore.Qt.UserRole, str(path))
            item.setToolTip(label)
            item.setData(QtCore.Qt.AccessibleTextRole, label)
            images.addItem(item)
        images.itemClicked.connect(self._accept_item)
        images.itemActivated.connect(self._accept_item)
        return images

    def _browse(self) -> None:
        self.browse_requested = True
        self.accept()

    def selected_path(self) -> Path | None:
        return self._selected_path

    def _accept_item(self, item: QtWidgets.QListWidgetItem) -> None:
        self._selected_path = Path(str(item.data(QtCore.Qt.UserRole)))
        self.accept()

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
        self.setText("Prompt Writer")
        self.setAccessibleName("Prompt Writer")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.setFixedSize(200, 200)
        self.setStyleSheet('#PWriteFab {background:transparent; border:none; padding:0;}')
        self._press_scale = 1.0
        self._press_animation = QtCore.QVariantAnimation(self)
        self._press_animation.setDuration(110)
        self._press_animation.valueChanged.connect(self._set_press_scale)
        self.pressed.connect(lambda: self._animate_press(0.94))
        self.released.connect(lambda: self._animate_press(1.0))

    def _animate_press(self, value):
        self._press_animation.stop()
        self._press_animation.setStartValue(self._press_scale)
        self._press_animation.setEndValue(value)
        self._press_animation.start()

    def _set_press_scale(self, value):
        self._press_scale = float(value)
        self.update()

    def paintEvent(self, event):
        if self.icon().isNull():
            return super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        side = int(min(self.width(), self.height()) * self._press_scale)
        rect = QtCore.QRect(0, 0, side, side)
        rect.moveCenter(self.rect().center() + QtCore.QPoint(0, 2 if self.isDown() else 0))
        self.icon().paint(painter, rect, QtCore.Qt.AlignCenter,
                          QIcon.Normal if self.isEnabled() else QIcon.Disabled)
        if self.hasFocus() and self.testAttribute(QtCore.Qt.WA_KeyboardFocusChange):
            painter.setPen(QtGui.QPen(self.palette().highlight().color(), 2))
            painter.drawLine(self.width()//3, self.height()-3, self.width()*2//3, self.height()-3)

    def set_artwork_size(self, size: int) -> None:
        self._artwork_size = size
        self.setFixedSize(size, size)
        self.setIconSize(QSize(size - 8, size - 8))

    def apply_theme_assets(self, theme_service=None, project_root=None) -> None:
        baseline = application_paths(project_root).app_resource_path(
            "themes/cyber_forge/prompt_writer/Pwrite.png"
        )
        path = baseline
        resolver = getattr(theme_service, "resolve_asset", None)
        if callable(resolver):
            try:
                path = Path(resolver("prompt_writer/Pwrite.png"))
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull() and path != baseline:
            pixmap = QtGui.QPixmap(str(baseline))
        # The shared text-fit guard also measures hidden icon-only text. Keep
        # the accessible name, but supply a visible label only for the fallback.
        self.setText("Prompt\nWriter" if pixmap.isNull() else "")
        self.setIcon(QIcon(pixmap))
        self.setToolButtonStyle(
            QtCore.Qt.ToolButtonTextOnly if pixmap.isNull()
            else QtCore.Qt.ToolButtonIconOnly
        )
        self.set_artwork_size(getattr(self, "_artwork_size", 152))

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
        movie.setCacheMode(
            QtGui.QMovie.CacheAll
            if normalized["playback_mode"] == "ping_pong"
            else QtGui.QMovie.CacheNone
        )
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
        if self.property("workspacePreview"):
            return QtCore.QSize(
                max(40, self.width() - margins.left() - margins.right() - 4),
                max(40, self.height() - margins.top() - margins.bottom() - 4),
            )
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
        if self._gif_settings["playback_mode"] == "ping_pong":
            self._advance_ping_pong_preview()
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

    def _advance_ping_pong_preview(self) -> None:
        # Use cached seeks in both directions. Starting QMovie between reverse
        # seeks can display an extra forward frame before it returns to zero.
        movie = self._movie
        last = max(0, movie.frameCount() - 1)
        phase = self._preview_phase
        if phase == "frame":
            self._completed_cycles += 1
            if self._target_cycles != FOREVER and self._completed_cycles >= self._target_cycles:
                self._preview_phase = "end"
                return
            self._preview_phase = phase = "pause"
            pause = int(self._gif_settings["loop_pause_ms"])
            if pause:
                self._preview_timer.start(pause)
                return
        if phase == "reverse" and movie.currentFrameNumber() == 0:
            self._preview_phase = phase = "reverse_pause"
            pause = int(self._gif_settings["loop_pause_ms"])
            if pause:
                self._preview_timer.start(pause)
                return
        if phase == "pause":
            self._preview_phase = "reverse"
            frame = max(0, last - 1)
        elif phase == "reverse":
            frame = movie.currentFrameNumber() - 1
        elif phase == "playing":
            frame = movie.currentFrameNumber() + 1
        else:
            self._preview_phase = "playing"
            frame = 0
        movie.jumpToFrame(frame)
        if self._preview_phase == "playing" and frame == last:
            self._preview_phase = "frame"
        self._preview_timer.start(max(1, movie.nextFrameDelay()))

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
                    "ping_pong" if self._saved_settings["playback_mode"] == "ping_pong" and selected_count != "original"
                    else "loop" if selected_count == FOREVER else "original"
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


# Keep the established public entry point; media widgets above are shared.
from images_grid import ImageTab
