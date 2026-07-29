# File: Image_tab.py
"""
Image-tab UI and logic for Letter Smith.

Canonical image files:
    gallery/user/pages/cover.png
    gallery/user/pages/letter.png
    gallery/user/pages/wall.png
    gallery/user/pages/back.png

The Reset Images and Gallery artwork buttons are deliberately large. They live
in their own left-aligned horizontal strip below the image cards. On narrower
windows that strip scrolls horizontally instead of shrinking the buttons or
allowing them to overlap the cards.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageOps

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon

from image_button import ArtworkButton
from project_sync import image_fingerprint


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


def _load_image_exif(path: str) -> Image.Image:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)

    if image.mode not in ("RGB", "RGBA"):
        try:
            image = image.convert("RGBA")
        except Exception:
            image = image.convert("RGB")

    return image


def _save_png(
    image: Image.Image,
    destination_path: str,
) -> None:
    destination = Path(destination_path)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_mode = (
        "RGBA"
        if image.mode == "RGBA"
        else "RGB"
    )

    image.convert(output_mode).save(
        destination,
        format="PNG",
        optimize=True,
    )


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

            combined_mask = ImageChops.lighter(
                alpha_mask,
                difference_mask,
            )

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

        self.setToolTip(
            "Click to select an image, "
            "or drag an image here."
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

        button_row.addStretch(1)
        button_row.addWidget(
            self.clear_btn
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
        self._source_pixmap = (
            QtGui.QPixmap(pixmap)
        )

        self._rescale()

        self.set_asset_state(
            "missing"
            if pixmap.isNull()
            else "ready"
        )

    def clear_pixmap(self) -> None:
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

    def _rescale(self) -> None:
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

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(event)
        self._rescale()


# ─────────────────────────────────────────────────────────────────────────────
# Image tab
# ─────────────────────────────────────────────────────────────────────────────


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

    FAB_FIXED_X = 55
    FAB_CARD_GAP = 12

    UTILITY_BUTTON_WIDTH = 400
    UTILITY_BUTTON_HEIGHT = 150

    # Exact position measured from the application window's left edge.
    # Change this to 10, 15, 20, etc. whenever needed.
    RESET_BUTTON_WINDOW_X = -130

    # Space between Reset Images and Gallery.
    UTILITY_BUTTON_GAP = -240

    # Vertical adjustment relative to the bottom of the image cards.
    # Negative values move the buttons upward.
    # Positive values move them downward.
    UTILITY_BUTTON_Y_OFFSET = -80

    def __init__(self) -> None:
        super().__init__()

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

        self._tab_active = False

        self._prompt_writer_panel: Optional[
            QtWidgets.QWidget
        ] = None

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

        centered_cards.addStretch(1)

        centered_cards.addLayout(
            cards_layout
        )

        centered_cards.addStretch(1)

        root.addLayout(
            centered_cards
        )

        self.reset_btn = ArtworkButton(
            "Reset Images",
            self._project_dir(),
            "BButton.png",
            self,
        )

        self.open_btn = ArtworkButton(
            "Gallery",
            self._project_dir(),
            "PButton.png",
            self,
        )

        for button in (
            self.reset_btn,
            self.open_btn,
        ):
            _trim_artwork_canvas(
                button
            )

            button.setFixedSize(
                self.UTILITY_BUTTON_WIDTH,
                self.UTILITY_BUTTON_HEIGHT,
            )

            button.setSizePolicy(
                QtWidgets.QSizePolicy.Fixed,
                QtWidgets.QSizePolicy.Fixed,
            )

            button.setFont(
                QtGui.QFont(
                    "Segoe UI Semibold",
                    18,
                    QtGui.QFont.Weight.Bold,
                )
            )

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

        self.reset_btn.setAccessibleName(
            "Reset Images"
        )

        self.reset_btn.setToolTip(
            "Clear all four selected "
            "letter images."
        )

        self.reset_btn.clicked.connect(
            self.reset_images
        )

        self.open_btn.setAccessibleName(
            "Gallery"
        )

        self.open_btn.setToolTip(
            "Open the working image "
            "gallery folder."
        )

        self.open_btn.clicked.connect(
            self.open_gallery_folder
        )

        # Reserve vertical room for the floating buttons.
        # The buttons themselves are not inside a layout or frame.
        # Reserve enough room for the buttons without affecting their position.
        root.addSpacing(
            self.UTILITY_BUTTON_HEIGHT
        )

        self.reset_btn.hide()
        self.open_btn.hide()

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

        prompt_writer_icon: Optional[
            QIcon
        ] = None

        for icon_path in (
            os.path.join(
                self._project_dir(),
                "gallery",
                "app",
                "icons",
                "Pwrite.png",
            ),
            os.path.join(
                self._project_dir(),
                "gallery",
                "app",
                "icons",
                "pwrite.png",
            ),
        ):
            if os.path.exists(icon_path):
                prompt_writer_icon = QIcon(
                    icon_path
                )
                break

        if (
            prompt_writer_icon is not None
            and not prompt_writer_icon.isNull()
        ):
            self.pwrite_fab.setIcon(
                prompt_writer_icon
            )

            self.pwrite_fab.setIconSize(
                QSize(
                    200,
                    200,
                )
            )

        else:
            self.pwrite_fab.setText(
                "PROMPT\nWRITER"
            )

            self.pwrite_fab.setStyleSheet(
                self.pwrite_fab.styleSheet()
                + (
                    "#PWriteFab {"
                    "color: #00e5e5;"
                    "font: 700 18px 'Segoe UI';"
                    "}"
                )
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
        return os.path.dirname(
            os.path.abspath(__file__)
        )

    def _user_pages_dir(self) -> str:
        return os.path.join(
            self._project_dir(),
            "gallery",
            "user",
            "pages",
        )

    def refresh_cards(self) -> None:
        for index, (
            _title,
            filename,
        ) in self.labels.items():
            path = os.path.join(
                self._user_pages_dir(),
                filename,
            )

            if os.path.isfile(path):
                pixmap = QtGui.QPixmap(
                    path
                )

                if not pixmap.isNull():
                    self.image_paths[
                        index
                    ] = path

                    self.cards[
                        index
                    ].set_pixmap(
                        pixmap
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
    # Utility button positioning
    # ─────────────────────────────────────────────────────────────────────

    def _position_utility_buttons(
            self,
    ) -> None:
        """
        Position Reset Images and Gallery directly inside the main window.

        RESET_BUTTON_WINDOW_X is an exact application-window coordinate.
        Image-tab margins, layouts, frames, and window resizing do not alter it.
        """
        window = self.window()

        if not isinstance(
                window,
                QtWidgets.QWidget,
        ):
            self.reset_btn.hide()
            self.open_btn.hide()
            return

        if not self.isVisibleTo(window):
            self.reset_btn.hide()
            self.open_btn.hide()
            return

        visible_cards = [
            card
            for card in self.cards.values()
            if card.isVisible()
        ]

        if not visible_cards:
            self.reset_btn.hide()
            self.open_btn.hide()
            return

        # Find the lowest edge of the image cards in window coordinates.
        cards_bottom = max(
            card.mapTo(
                window,
                QPoint(
                    0,
                    card.height(),
                ),
            ).y()
            for card in visible_cards
        )

        button_y = (
                cards_bottom
                + self.UTILITY_BUTTON_Y_OFFSET
        )

        # Move both buttons out of ImageTab and directly onto the window.
        for button in (
                self.reset_btn,
                self.open_btn,
        ):
            if button.parent() is not window:
                button.setParent(window)

            button.setFixedSize(
                self.UTILITY_BUTTON_WIDTH,
                self.UTILITY_BUTTON_HEIGHT,
            )

        # Exact horizontal position.
        self.reset_btn.move(
            self.RESET_BUTTON_WINDOW_X,
            button_y,
        )

        self.open_btn.move(
            self.RESET_BUTTON_WINDOW_X
            + self.UTILITY_BUTTON_WIDTH
            + self.UTILITY_BUTTON_GAP,
            button_y,
        )

        self.reset_btn.show()
        self.open_btn.show()

        self.reset_btn.raise_()
        self.open_btn.raise_()

    def _schedule_utility_button_position(
            self,
    ) -> None:
        QtCore.QTimer.singleShot(
            0,
            self._position_utility_buttons,
        )

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
        self._schedule_utility_button_position()
        self._schedule_prompt_writer_position()

    def hideEvent(
            self,
            event: QtGui.QHideEvent,
    ) -> None:
        self.deactivate_for_tab_change()

        if hasattr(
                self,
                "reset_btn",
        ):
            self.reset_btn.hide()

        if hasattr(
                self,
                "open_btn",
        ):
            self.open_btn.hide()

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
                "reset_btn",
        ):
            self._schedule_utility_button_position()

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
        path, _selected_filter = (
            QtWidgets.QFileDialog.getOpenFileName(
                self,
                (
                    f"Select "
                    f"{self.labels[index][0]}"
                ),
                "",
                (
                    "Images "
                    "(*.png *.jpg *.jpeg *.bmp)"
                ),
            )
        )

        if path:
            self.set_image_path(
                index,
                path,
            )

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

        _label, filename = (
            self.labels[index]
        )

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
            image = _load_image_exif(
                source_path
            )

            _save_png(
                image,
                destination_path,
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
        ] = destination_path

        self.cards[
            index
        ].set_pixmap(
            pixmap
        )

        self.image_selected.emit(
            pixmap
        )

        self._commit_image_change(
            "selected"
        )

        self._show_temporary_status(
            f"{filename} saved."
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

        path = os.path.join(
            self._user_pages_dir(),
            filename,
        )

        try:
            if os.path.isfile(path):
                os.remove(path)

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
        for index in self.labels:
            _label, filename = (
                self.labels[index]
            )

            path = os.path.join(
                self._user_pages_dir(),
                filename,
            )

            try:
                if os.path.isfile(path):
                    os.remove(path)

            except OSError:
                pass

            self.image_paths[
                index
            ] = None

            self.cards[
                index
            ].clear_pixmap()

        self.clear_preview.emit()

        self._commit_image_change(
            "reset"
        )

        self._show_temporary_status(
            "All images cleared."
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

    def _track_prompt_writer_panel(
        self,
        panel: object,
    ) -> None:
        if not isinstance(
            panel,
            QtWidgets.QWidget,
        ):
            return

        if panel is self._prompt_writer_panel:
            return

        closed_signal = getattr(
            panel,
            "closed",
            None,
        )

        if closed_signal is None:
            return

        try:
            closed_signal.connect(
                self._on_prompt_writer_closed
            )

            self._prompt_writer_panel = (
                panel
            )

        except Exception:
            pass

    @QtCore.Slot()
    def _on_prompt_writer_closed(
        self,
    ) -> None:
        self._prompt_writer_panel = None

        self._show_temporary_status(
            "Prompt Writer closed."
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

        panel = getattr(
            window,
            "_prompt_writer_win",
            None,
        )

        self._track_prompt_writer_panel(
            panel
        )

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

        try:
            self.sync_to_disk()
        except Exception:
            pass


__all__ = [
    "ImageAssetCard",
    "ImageTab",
    "StaticFab",
]