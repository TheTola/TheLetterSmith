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

import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon

from image_button import ArtworkButton
from image_animation import (
    FOREVER,
    IMAGE_MANIFEST_NAME,
    INDEX_TO_SLOT,
    MAX_PLAY_COUNT,
    clear_slot_asset,
    install_image_asset,
    load_image_manifest,
    normalize_gif_settings,
    reconcile_external_image_assets,
    update_slot_gif_settings,
)
from project_paths import ProjectPathResolver, application_paths
from project_save import ProjectSaveService
from project_state import ProjectStateController
from project_sync import file_fingerprint, image_fingerprint
from ui_help import set_control_help


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


class StockImageDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        image_paths: tuple[Path, ...],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint,
        )
        self.setObjectName("StockImageTray")
        self.setAccessibleName(f"Stock Images - {title}")
        self.setFixedSize(420, 146)
        self._selected_path: Path | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._images = QtWidgets.QListWidget()
        self._images.setViewMode(QtWidgets.QListView.IconMode)
        self._images.setIconSize(QtCore.QSize(112, 112))
        self._images.setGridSize(QtCore.QSize(130, 126))
        self._images.setSpacing(3)
        self._images.setMovement(QtWidgets.QListView.Static)
        self._images.setResizeMode(QtWidgets.QListView.Adjust)
        self._images.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._images.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        for path in image_paths:
            item = QtWidgets.QListWidgetItem(QIcon(str(path)), "")
            item.setData(QtCore.Qt.UserRole, str(path))
            item.setToolTip(path.stem)
            self._images.addItem(item)
        self._images.itemClicked.connect(self._accept_item)
        layout.addWidget(self._images)
        self.setStyleSheet(
            "QDialog#StockImageTray{background:#101317;border:1px solid #394654;"
            "border-radius:8px;}"
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{border:1px solid #2d3540;border-radius:6px;}"
            "QListWidget::item:hover,QListWidget::item:selected{"
            "border:2px solid #00d0ff;background:#19232d;}"
        )

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
        screen = QtGui.QGuiApplication.screenAt(cursor)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = min(max(cursor.x() + 8, available.left()), available.right() - self.width())
        y = min(max(cursor.y() + 8, available.top()), available.bottom() - self.height())
        self.move(x, y)


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
        width: int,
        height: int,
        font_size: int,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._utility_width = int(width)
        self._utility_height = int(height)
        self._utility_font_size = int(font_size)
        super().__init__(
            text,
            project_root,
            artwork_filename,
            parent,
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
        self.setFixedSize(
            self._utility_width,
            self._utility_height,
        )
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.setFont(
            QtGui.QFont(
                "Segoe UI Semibold",
                self._utility_font_size,
                QtGui.QFont.Weight.Bold,
            )
        )
        _mask_button_to_artwork(self)


class _ResetImagesConfirmationDialog(
    QtWidgets.QDialog
):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            QtCore.Qt.Dialog
            | QtCore.Qt.FramelessWindowHint,
        )
        self.setObjectName(
            "ResetImagesConfirmationDialog"
        )
        self.setAccessibleName(
            "Confirm reset images"
        )
        self.setModal(True)
        self.setWindowModality(
            QtCore.Qt.ApplicationModal
        )
        self.setFixedWidth(390)
        self.setStyleSheet(
            "QDialog#ResetImagesConfirmationDialog{"
            "background:#101317;border:1px solid #43505d;"
            "border-radius:10px;}"
            "QLabel{color:#f2f5f7;font:600 13pt 'Segoe UI';}"
            "QPushButton{background:#171c22;border:1px solid #465260;"
            "border-radius:7px;padding:9px 26px;font:700 12pt 'Segoe UI';}"
            "QPushButton#ResetImagesYes{color:#ff626c;border-color:#9b3740;}"
            "QPushButton#ResetImagesYes:hover{background:#32191d;}"
            "QPushButton#ResetImagesNo{color:#00d0ff;border-color:#287f92;}"
            "QPushButton#ResetImagesNo:hover{background:#132a31;}"
        )

        layout = QtWidgets.QVBoxLayout(
            self
        )
        layout.setContentsMargins(
            28,
            24,
            28,
            22,
        )
        layout.setSpacing(20)

        question = QtWidgets.QLabel(
            "Are you sure you want to reset?"
        )
        question.setAlignment(
            QtCore.Qt.AlignCenter
        )
        question.setWordWrap(True)
        layout.addWidget(question)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)

        yes_button = QtWidgets.QPushButton(
            "Yes"
        )
        yes_button.setObjectName(
            "ResetImagesYes"
        )
        yes_button.clicked.connect(
            self.accept
        )
        actions.addWidget(yes_button)

        no_button = QtWidgets.QPushButton(
            "No"
        )
        no_button.setObjectName(
            "ResetImagesNo"
        )
        no_button.setDefault(True)
        no_button.clicked.connect(
            self.reject
        )
        actions.addWidget(no_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.yes_button = yes_button
        self.no_button = no_button


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

        self.setAlignment(
            QtCore.Qt.AlignCenter
        )

        self.setMinimumSize(
            150,
            165,
        )

        self.setMaximumSize(
            170,
            185,
        )

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

        self.setObjectName(
            "ImageAssetCard"
        )

        self.setProperty(
            "assetState",
            "missing",
        )

        self.setFixedWidth(190)
        self.setMinimumHeight(255)
        self.setMaximumHeight(280)

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
        )

        button_row = (
            QtWidgets.QHBoxLayout()
        )

        button_row.setSpacing(8)

        self.clear_btn = (
            QtWidgets.QPushButton(
                "♲  Clear"
            )
        )

        self.clear_btn.setMinimumHeight(
            34
        )

        self.clear_btn.setCursor(
            QtCore.Qt.PointingHandCursor
        )

        set_control_help(
            self.clear_btn,
            "Remove this image from the letter and return this slot to its empty state.",
            accessible_name="Clear image",
        )

        self.clear_btn.setStyleSheet(
            "QPushButton {"
            "background: #151a20;"
            "color: #d8e0ea;"
            "border: 1px solid #35404d;"
            "border-radius: 6px;"
            "padding: 6px 14px;"
            "}"
            "QPushButton:hover {"
            "background: #202832;"
            "border-color: #00a9c7;"
            "color: #ffffff;"
            "}"
            "QPushButton:pressed {"
            "background: #11161c;"
            "}"
        )

        self.clear_btn.clicked.connect(
            lambda: (
                self.clear_requested.emit(
                    self.index
                )
            )
        )

        self.settings_btn = QtWidgets.QPushButton(
            "Settings"
        )
        self.settings_btn.setMinimumHeight(34)
        self.settings_btn.setCursor(
            QtCore.Qt.PointingHandCursor
        )
        set_control_help(
            self.settings_btn,
            "Adjust playback settings for this animated image.",
            accessible_name="Image animation settings",
        )
        self.settings_btn.setEnabled(False)
        self.settings_btn.setVisible(False)
        self.settings_btn.setStyleSheet(
            self.clear_btn.styleSheet()
        )
        self.settings_btn.clicked.connect(
            lambda: self.settings_requested.emit(
                self.index
            )
        )

        button_row.addWidget(
            self.settings_btn
        )
        button_row.addWidget(
            self.clear_btn
        )

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
        preview_path: str | None = None,
        animate_gif: bool = True,
    ) -> None:
        if not animated_gif:
            self.set_pixmap(
                QtGui.QPixmap(path)
            )
            self.settings_btn.setToolTip(
                "Static image settings"
            )
            return

        animation_path = preview_path or path
        if not animate_gif:
            preview = QtGui.QPixmap(animation_path)
            self.set_pixmap(preview)
            if preview.isNull():
                return
            self.settings_btn.setVisible(True)
            self.settings_btn.setEnabled(True)
            self.settings_btn.setToolTip(
                "Animation settings for this GIF"
            )
            return

        self._stop_movie()
        self._source_pixmap = QtGui.QPixmap()
        movie = QtGui.QMovie(animation_path)
        movie.setCacheMode(QtGui.QMovie.CacheNone)
        if not movie.isValid():
            self.set_pixmap(QtGui.QPixmap(animation_path))
            return
        self._movie = movie
        self.thumbnail.setText("")
        self.thumbnail.setStyleSheet(
            "background: #101317;"
            "border: 1px solid #2d3540;"
            "border-radius: 6px;"
        )
        self.thumbnail.setMovie(movie)
        movie.jumpToFrame(0)
        self.settings_btn.setVisible(True)
        self.settings_btn.setEnabled(True)
        self.settings_btn.setToolTip(
            "Animation settings for this GIF"
        )
        self.set_asset_state("ready")
        self._rescale()
        movie.start()

    def clear_pixmap(self) -> None:
        self._stop_movie()
        self._source_pixmap = (
            QtGui.QPixmap()
        )

        self.thumbnail.clear()

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
            "Select an image before opening settings"
        )

    def _rescale(self) -> None:
        if self._movie is not None:
            target_size = (
                self.thumbnail.size()
                - QtCore.QSize(8, 8)
            )
            if (
                target_size.width() > 0
                and target_size.height() > 0
            ):
                current = self._movie.currentPixmap()
                source_size = current.size()
                if source_size.isEmpty():
                    source_size = self._movie.currentImage().size()
                if not source_size.isEmpty():
                    self._movie.setScaledSize(
                        source_size.scaled(
                            target_size,
                            QtCore.Qt.KeepAspectRatio,
                        )
                    )
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

        target_size = (
            self.thumbnail.size()
            - QtCore.QSize(
                8,
                8,
            )
        )

        if (
            target_size.width() <= 0
            or target_size.height() <= 0
        ):
            return

        scaled_pixmap = (
            self._source_pixmap.scaled(
                target_size,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

        self.thumbnail.setPixmap(
            scaled_pixmap
        )

    def _stop_movie(self) -> None:
        if self._movie is None:
            return
        self._movie.stop()
        self.thumbnail.setMovie(None)
        self._movie.setFileName("")
        self._movie.deleteLater()
        self._movie = None

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


class ImageSettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        *,
        animated_gif: bool,
        settings: dict[str, object] | None = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} Settings")
        self.setModal(True)
        self.setMinimumWidth(410)

        root = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel(
            "Animated GIF" if animated_gif else "Static image"
        )
        heading.setStyleSheet(
            "color:#00d0ff;font:700 14px 'Segoe UI';"
        )
        root.addWidget(heading)

        if not animated_gif:
            message = QtWidgets.QLabel(
                "This image has no animation settings."
            )
            message.setWordWrap(True)
            root.addWidget(message)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Close
            )
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)
            return

        normalized = normalize_gif_settings(settings)
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )

        self.playback_mode = QtWidgets.QComboBox()
        self.playback_mode.addItem("Original", "original")
        self.playback_mode.addItem("Loop", "loop")
        self.playback_mode.addItem("Ping-Pong", "ping_pong")
        self.playback_mode.setCurrentIndex(
            max(
                0,
                self.playback_mode.findData(
                    normalized["playback_mode"]
                ),
            )
        )
        set_control_help(
            self.playback_mode,
            "Choose whether this animated image plays normally, loops, or reverses between cycles.",
        )
        form.addRow("Playback Mode", self.playback_mode)

        count_row = QtWidgets.QWidget()
        count_layout = QtWidgets.QHBoxLayout(count_row)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.setSpacing(8)
        self.play_count = QtWidgets.QComboBox()
        self.play_count.addItem("Once", 1)
        self.play_count.addItem("2 times", 2)
        self.play_count.addItem("3 times", 3)
        self.play_count.addItem("Custom number", "custom")
        self.play_count.addItem("Forever", FOREVER)
        self.custom_count = QtWidgets.QSpinBox()
        self.custom_count.setRange(1, MAX_PLAY_COUNT)
        self.custom_count.setValue(4)
        set_control_help(
            self.play_count,
            "Choose how many times the animation plays before stopping.",
        )
        set_control_help(
            self.custom_count,
            "Set the exact number of animation cycles when Custom number is selected.",
        )
        stored_count = normalized["play_count"]
        if stored_count in (1, 2, 3, FOREVER):
            count_index = self.play_count.findData(stored_count)
        else:
            count_index = self.play_count.findData("custom")
            self.custom_count.setValue(int(stored_count))
        self.play_count.setCurrentIndex(max(0, count_index))
        count_layout.addWidget(self.play_count, 1)
        count_layout.addWidget(self.custom_count)
        form.addRow("Play Count", count_row)

        self.start_delay = self._seconds_control(
            int(normalized["start_delay_ms"])
        )
        form.addRow("Start Delay", self.start_delay)
        self.loop_pause = self._seconds_control(
            int(normalized["loop_pause_ms"])
        )
        form.addRow("Loop Pause / End Hold", self.loop_pause)
        root.addLayout(form)

        self.mode_help = QtWidgets.QLabel()
        self.mode_help.setWordWrap(True)
        self.mode_help.setStyleSheet("color:#aebbc8;")
        root.addWidget(self.mode_help)
        self.playback_mode.currentIndexChanged.connect(
            self._update_mode_help
        )
        self.play_count.currentIndexChanged.connect(
            self._sync_custom_count
        )
        self._sync_custom_count()
        self._update_mode_help()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save
            | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _seconds_control(milliseconds: int) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(0.0, 86_400.0)
        control.setDecimals(3)
        control.setSingleStep(0.25)
        control.setSuffix(" seconds")
        control.setValue(milliseconds / 1000.0)
        return control

    def _sync_custom_count(self) -> None:
        self.custom_count.setVisible(
            self.play_count.currentData() == "custom"
        )

    def _update_mode_help(self) -> None:
        descriptions = {
            "original": (
                "Uses the GIF's embedded loop behavior. Forever leaves that "
                "behavior authoritative; a finite Play Count caps it."
            ),
            "loop": (
                "Plays forward from beginning to end for the selected count."
            ),
            "ping_pong": (
                "Plays forward, then displays the frames in reverse before "
                "the next forward play."
            ),
        }
        self.mode_help.setText(
            descriptions[str(self.playback_mode.currentData())]
        )

    def gif_settings(self) -> dict[str, object]:
        play_count = self.play_count.currentData()
        if play_count == "custom":
            play_count = self.custom_count.value()
        return normalize_gif_settings(
            {
                "playback_mode": self.playback_mode.currentData(),
                "play_count": play_count,
                "start_delay_ms": round(
                    self.start_delay.value() * 1000
                ),
                "loop_pause_ms": round(
                    self.loop_pause.value() * 1000
                ),
            }
        )


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

    UTILITY_BUTTON_WIDTH = 210
    UTILITY_BUTTON_HEIGHT = 120
    UTILITY_BUTTON_FONT_SIZE = 12

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

        self._disk_fingerprint = (
            image_fingerprint(
                self._project_dir()
            )
        )
        self._cover_fingerprint = self._cover_file_fingerprint()

        self._tab_active = False

        root = QtWidgets.QVBoxLayout(self)

        root.setContentsMargins(0,0,0,0)

        root.setSpacing(8)

        header = QtWidgets.QLabel(
            "Select images for your letter"
        )

        header.setFont(
            QtGui.QFont(
                "Segoe UI Semibold",
                13,
            )
        )

        header.setStyleSheet(
            "color: #00d0ff;"
        )

        header.setAlignment(
            QtCore.Qt.AlignCenter
        )

        root.addWidget(header)

        self.cards: dict[
            int,
            ImageAssetCard,
        ] = {}

        cards_layout = (
            QtWidgets.QHBoxLayout()
        )

        cards_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        cards_layout.setSpacing(6)

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
                self,
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
                0,
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
            "BButton.png",
            width=self.UTILITY_BUTTON_WIDTH,
            height=self.UTILITY_BUTTON_HEIGHT,
            font_size=self.UTILITY_BUTTON_FONT_SIZE,
            parent=self,
        )

        self.open_btn = _ImageUtilityButton(
            "Gallery",
            self._project_dir(),
            "PButton.png",
            width=self.UTILITY_BUTTON_WIDTH,
            height=self.UTILITY_BUTTON_HEIGHT,
            font_size=self.UTILITY_BUTTON_FONT_SIZE,
            parent=self,
        )

        for button in (
            self.reset_btn,
            self.open_btn,
        ):
            if not button.has_artwork:
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
        utility_position_column = (
            QtWidgets.QVBoxLayout()
        )

        utility_position_column.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        utility_position_column.setSpacing(
            self.UTILITY_BUTTON_GAP
        )

        utility_position_column.addStretch(1)

        utility_position_column.addWidget(
            self.reset_btn,
            0,
            QtCore.Qt.AlignHCenter,
        )

        utility_position_column.addWidget(
            self.open_btn,
            0,
            QtCore.Qt.AlignHCenter,
        )

        utility_position_column.addStretch(1)

        centered_cards.insertLayout(
            1,
            utility_position_column,
        )

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
            "Open Prompt Writer to create and manage image-generation prompts for your letter.",
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

    # ─────────────────────────────────────────────────────────────────────
    # Paths and synchronization
    # ─────────────────────────────────────────────────────────────────────

    def _project_dir(self) -> str:
        return str(self.project_root)

    def apply_theme_assets(self, theme_service: object | None = None) -> None:
        """Refresh cloud buttons and Prompt Writer art for the active theme."""
        for button_name in ("reset_btn", "open_btn"):
            button = getattr(self, button_name, None)
            refresher = getattr(button, "apply_theme_assets", None)
            if callable(refresher):
                refresher(theme_service)

        prompt_button = getattr(self, "pwrite_fab", None)
        if prompt_button is None:
            return

        paths = application_paths(self._project_dir())
        fallback_candidates = (
            paths.app_resource_path("icons/Pwrite.png"),
            paths.app_resource_path("icons/pwrite.png"),
        )
        fallback_path = next(
            (path for path in fallback_candidates if path.is_file()),
            fallback_candidates[0],
        )
        icon_path = fallback_path
        resolver = getattr(theme_service, "resolve_asset", None)
        if callable(resolver):
            try:
                fallback = fallback_path.resolve().relative_to(
                    paths.resource_root
                ).as_posix()
                icon_path = resolver(
                    "prompt_writer/Pwrite.png",
                    fallback=fallback,
                )
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
            prompt_button.setStyleSheet(
                prompt_button.styleSheet()
                + (
                    "#PWriteFab {"
                    "color: #00e5e5;"
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

    def refresh_cards(self) -> None:
        reconcile_external_image_assets(
            self._user_pages_dir()
        )
        manifest = load_image_manifest(
            self._user_pages_dir()
        )
        for index, (
            _title,
            filename,
        ) in self.labels.items():
            preview_path = os.path.join(
                self._user_pages_dir(),
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
                    self._user_pages_dir(),
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
                    preview_path=thumbnail_path,
                    animate_gif=self._tab_active,
                )
                continue

            self.image_paths[
                index
            ] = None

            self.cards[
                index
            ].clear_pixmap()

    def sync_from_disk(
        self,
        *,
        force: bool = False,
    ) -> bool:
        before = self._disk_fingerprint

        after = image_fingerprint(
            self._project_dir()
        )

        changed = (
            force
            or before != after
        )

        self.refresh_cards()

        self._disk_fingerprint = (
            image_fingerprint(
                self._project_dir()
            )
        )
        self._emit_cover_change_if_needed()

        if changed:
            self.images_changed.emit(
                "disk"
            )

        return changed

    def sync_to_disk(self) -> bool:
        current = image_fingerprint(
            self._project_dir()
        )

        changed = (
            current
            != self._disk_fingerprint
        )

        if changed:
            self._disk_fingerprint = (
                current
            )

            self.images_changed.emit(
                "saved"
            )

        self._emit_cover_change_if_needed()

        self.refresh_cards()

        return changed

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

    def deactivate_for_tab_change(
        self,
    ) -> None:
        if not self._tab_active:
            return

        self.sync_to_disk()
        for card in self.cards.values():
            card.release_asset_handle()
        self._tab_active = False

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
            maximum_y = (
                cards_top
                - self.pwrite_fab.height()
                - self.FAB_CARD_GAP
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
                    y_position,
                ),
            )
        )

        self.pwrite_fab.clamp_to_surface()
        self.pwrite_fab.show()
        self.pwrite_fab.raise_()

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

    def resizeEvent(
            self,
            event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(event)

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
        image_paths = self._stock_image_paths(index)
        if len(image_paths) != 3:
            self._show_temporary_status(
                f"Stock images are unavailable for {self.labels[index][0]}.",
                5000,
            )
            return
        dialog = StockImageDialog(
            self.labels[index][0],
            image_paths,
            self,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
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

        self.images_changed.emit(
            reason
        )

    def set_image_path(
        self,
        index: int,
        source_path: str,
    ) -> None:
        if index not in self.labels:
            return
        if not self.project_state.is_project_ready:
            self._show_temporary_status(
                "A recipient is required before saving images.",
                5000,
            )
            return

        _label, filename = (
            self.labels[index]
        )
        slot = INDEX_TO_SLOT[index]

        pages_directory = (
            self._user_pages_dir()
        )

        os.makedirs(
            pages_directory,
            exist_ok=True,
        )

        destination_path = (
            os.path.join(
                pages_directory,
                filename,
            )
        )

        try:
            self.cards[index].release_asset_handle()
            record = install_image_asset(
                pages_directory,
                slot,
                source_path,
            )
            self.project_save_service.copy_workspace_file(
                destination_path,
                Path("pages") / filename,
            )
            source_filename = str(
                record["source_file"]
            )
            if record["asset_type"] == "animated_gif":
                self.project_save_service.copy_workspace_file(
                    Path(pages_directory) / source_filename,
                    Path("pages") / source_filename,
                )
                thumbnail_filename = str(record["thumbnail_file"])
                self.project_save_service.copy_workspace_file(
                    Path(pages_directory) / thumbnail_filename,
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
                Path(pages_directory) / IMAGE_MANIFEST_NAME,
                Path("pages") / IMAGE_MANIFEST_NAME,
            )

        except Exception as error:
            self._show_temporary_status(
                (
                    f"Failed to process "
                    f"{filename}: {error}"
                ),
                5000,
            )

            self.cards[
                index
            ].set_asset_state(
                "warning"
            )

            return

        pixmap = QtGui.QPixmap(
            destination_path
        )

        if pixmap.isNull():
            self._show_temporary_status(
                f"Invalid image: {filename}",
                5000,
            )

            self.cards[
                index
            ].set_asset_state(
                "warning"
            )

            return

        self.image_paths[
            index
        ] = str(
            Path(pages_directory)
            / str(record["source_file"])
        )

        self.cards[
            index
        ].set_asset_path(
            self.image_paths[index] or destination_path,
            animated_gif=(
                record["asset_type"]
                == "animated_gif"
            ),
            preview_path=str(
                Path(pages_directory)
                / str(record.get("thumbnail_file", filename))
            ),
        )

        self.image_selected.emit(
            pixmap
        )

        self._commit_image_change(
            "selected"
        )

        self._show_temporary_status(
            f"{record['source_file']} saved."
        )

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
        dialog = ImageSettingsDialog(
            self.labels[index][0],
            animated_gif=animated_gif,
            settings=record.get("settings"),
            parent=self,
        )
        if not animated_gif:
            dialog.exec()
            return
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
        except Exception as error:
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

        opener = getattr(
            window,
            "open_prompt_writer",
            None,
        )

        if not callable(opener):
            self._show_temporary_status(
                (
                    "Prompt Writer opener "
                    "not found on the main window."
                ),
                5000,
            )

            return

        existing_panel = getattr(
            window,
            "_prompt_writer_win",
            None,
        )

        was_visible = (
            isinstance(
                existing_panel,
                QtWidgets.QWidget,
            )
            and existing_panel.isVisible()
        )

        try:
            opener()

        except Exception as error:
            self._show_temporary_status(
                (
                    f"Could not open "
                    f"Prompt Writer: {error}"
                ),
                5000,
            )

            return

        self._show_temporary_status(
            (
                "Prompt Writer focused."
                if was_visible
                else "Prompt Writer opened."
            )
        )

    def shutdown(self) -> None:
        self._status_clear_timer.stop()
        self.pwrite_fab.hide()
        self.prepare_for_project_restore()

        try:
            self.sync_to_disk()
        except Exception:
            pass

    def prepare_for_project_restore(self) -> None:
        for card in self.cards.values():
            card.release_asset_handle()


__all__ = [
    "ImageAssetCard",
    "ImageTab",
    "StaticFab",
]
