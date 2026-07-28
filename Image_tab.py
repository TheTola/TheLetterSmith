# File: Image_tab.py
"""
Image tab UI and logic.

SOURCE OF TRUTH (pages):
  gallery/user/pages/
    cover.png
    letter.png
    wall.png
    back.png

Notes:
- Slot mapping:
    1 → cover.png
    2 → letter.png
    3 → wall.png   (Letter Background)
    4 → back.png
- Clicking an image thumbnail selects or replaces that image.
- Each card has a Clear button.
- Hover preview works for all four images.
- Reset clears the preview immediately.

This file writes only into gallery/user/pages/*.png.

Prompt Writer FAB:
- Not draggable.
- Appears only while the Images tab is visible.
- Positioned in the shared preview region.
- Prevented from overlapping the image cards.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Signal, QUrl, QSize, QPoint
from PySide6.QtGui import QDesktopServices, QIcon


# ─────────────────────────────────────────────────────────────────────────────
# Static Floating Action Button
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

    def set_surface(
            self,
            surface: QtWidgets.QWidget,
    ) -> None:
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

        x_position = max(
            0,
            min(self.x(), maximum_x),
        )

        y_position = max(
            0,
            min(self.y(), maximum_y),
        )

        self.move(
            QPoint(
                x_position,
                y_position,
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Image utilities
# ─────────────────────────────────────────────────────────────────────────────

def _load_image_exif(path: str) -> Image.Image:
    """
    Load an image and apply its EXIF orientation.
    """
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
    """
    Save an image as PNG in the working pages directory.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Image thumbnail
# ─────────────────────────────────────────────────────────────────────────────

class _ImageThumbnail(QtWidgets.QLabel):
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
            parent: Optional[QtWidgets.QWidget] = None,
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

        self.setToolTip(
            "Click to select an image, or drag an image here."
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
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return

        super().mousePressEvent(event)

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
            if not url.isLocalFile():
                continue

            path = url.toLocalFile()

            if path.lower().endswith(
                    self.SUPPORTED_EXTENSIONS
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

class ImageAssetCard(QtWidgets.QFrame):
    select_requested = Signal(int)
    clear_requested = Signal(int)
    preview_requested = Signal(int)
    file_dropped = Signal(int, str)

    def __init__(
            self,
            index: int,
            title: str,
            parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.index = index
        self._source_pixmap = QtGui.QPixmap()

        self.setObjectName("ImageAssetCard")
        self.setProperty("assetState", "missing")

        self.setFixedWidth(190)
        self.setMinimumHeight(255)
        self.setMaximumHeight(280)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Preferred,
        )

        root_layout = QtWidgets.QVBoxLayout(self)

        root_layout.setContentsMargins(
            10,
            10,
            10,
            8,
        )

        root_layout.setSpacing(7)

        self.title_label = QtWidgets.QLabel(title)

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

        self.thumbnail = _ImageThumbnail(self)

        self.thumbnail.clicked.connect(
            lambda: self.select_requested.emit(
                self.index
            )
        )

        self.thumbnail.hovered.connect(
            lambda: self.preview_requested.emit(
                self.index
            )
        )

        self.thumbnail.file_dropped.connect(
            lambda path: self.file_dropped.emit(
                self.index,
                path,
            )
        )

        root_layout.addWidget(
            self.thumbnail,
            1,
        )

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)

        self.clear_btn = QtWidgets.QPushButton(
            "♲  Clear"
        )

        self.clear_btn.setMinimumHeight(34)

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
            lambda: self.clear_requested.emit(
                self.index
            )
        )

        button_row.addStretch(1)
        button_row.addWidget(self.clear_btn)
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
            "QFrame#ImageAssetCard[assetState='ready'] "
            "{" "border: 1px solid #00d0ff;""}"

            "QFrame#ImageAssetCard[assetState='warning'] {"
            "border-color: #c9a227;"
            "}"
            "QFrame#ImageAssetCard[assetState='missing'] {"
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
        self._source_pixmap = QtGui.QPixmap(
            pixmap
        )

        self._rescale()

        if pixmap.isNull():
            self.set_asset_state("missing")
        else:
            self.set_asset_state("ready")

    def clear_pixmap(self) -> None:
        self._source_pixmap = QtGui.QPixmap()

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

        self.set_asset_state("missing")

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
                - QtCore.QSize(8, 8)
        )

        if (
                target_size.width() <= 0
                or target_size.height() <= 0
        ):
            return

        scaled_pixmap = self._source_pixmap.scaled(
            target_size,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
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

class ImageTab(QtWidgets.QWidget):
    image_selected = Signal(QtGui.QPixmap)
    hover_preview_image = Signal(QtGui.QPixmap)
    clear_preview = Signal()

    FAB_FIXED_X = 55
    FAB_CARD_GAP = 12

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

        layout = QtWidgets.QVBoxLayout(self)

        layout.setContentsMargins(
            20,
            6,
            20,
            12,
        )

        layout.setSpacing(8)

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

        layout.addWidget(header)

        self.cards: dict[
            int,
            ImageAssetCard,
        ] = {}

        card_row = QtWidgets.QHBoxLayout()
        card_row.setSpacing(14)

        card_row.addStretch(1)

        for index in (
                1,
                2,
                3,
                4,
        ):
            title, _filename = self.labels[index]

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

            card_row.addWidget(
                card,
                0,
                QtCore.Qt.AlignTop,
            )

        card_row.addStretch(1)

        layout.addLayout(card_row)

        utility_row = QtWidgets.QHBoxLayout()
        utility_row.setSpacing(8)

        self.reset_btn = QtWidgets.QPushButton(
            "Reset Images"
        )

        self.open_btn = QtWidgets.QPushButton(
            "Gallery"
        )

        for button in (
                self.reset_btn,
                self.open_btn,
        ):
            button.setFont(
                QtGui.QFont(
                    "Segoe UI Semibold",
                    10,
                )
            )

            button.setStyleSheet(
                "QPushButton {"
                "background: #171a1f;"
                "color: #dfe6ee;"
                "border: 1px solid #3b434e;"
                "border-radius: 5px;"
                "padding: 6px 12px;"
                "}"
                "QPushButton:hover {"
                "border-color: #00b8cf;"
                "color: #ffffff;"
                "}"
                "QPushButton:pressed {"
                "background: #101318;"
                "}"
            )

        self.reset_btn.clicked.connect(
            self.reset_images
        )

        self.open_btn.clicked.connect(
            self.open_gallery_folder
        )

        utility_row.addWidget(
            self.reset_btn
        )

        utility_row.addWidget(
            self.open_btn
        )

        utility_row.addStretch(1)

        layout.addLayout(
            utility_row
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

        # Status messages describe recent activity, not permanent state.
        # Restarting this timer replaces the previous message and resets
        # how long the newest action remains visible.
        self._status_clear_timer = QtCore.QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.timeout.connect(
            self.status.clear
        )

        self._prompt_writer_panel: Optional[
            QtWidgets.QWidget
        ] = None

        layout.addWidget(
            self.status
        )

        layout.addStretch(1)

        self._fab_surface: QtWidgets.QWidget = self

        self.pwrite_fab = StaticFab(
            self,
            self,
        )

        project_directory = self._project_dir()

        prompt_writer_icon: Optional[QIcon] = None

        for icon_path in (
                os.path.join(
                    project_directory,
                    "gallery",
                    "app",
                    "icons",
                    "Pwrite.png",
                ),
                os.path.join(
                    project_directory,
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

        # Start hidden so it cannot flash on another tab during startup.
        self.pwrite_fab.hide()

        self.refresh_cards()

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

    def _load_fresh_pixmap(
            self,
            path: str,
    ) -> QtGui.QPixmap:
        """
        Load an image directly from disk without relying on QPixmap's
        filename cache. This matters when another part of the app replaces
        cover.png, letter.png, wall.png, or back.png while the Images tab is
        already open.
        """
        reader = QtGui.QImageReader(path)
        reader.setAutoTransform(True)

        image = reader.read()

        if image.isNull():
            return QtGui.QPixmap()

        return QtGui.QPixmap.fromImage(image)

    def _refresh_card_from_disk(
            self,
            index: int,
            *,
            show_in_preview: bool = False,
    ) -> bool:
        """
        Refresh one image card from its gallery file.

        Hovering a card calls this method, so only the hovered card is
        rescanned. The thumbnail and shared preview receive the exact same
        freshly loaded pixmap.
        """
        if index not in self.labels:
            return False

        path = os.path.join(
            self._user_pages_dir(),
            self.labels[index][1],
        )

        if not os.path.isfile(path):
            self.image_paths[index] = None
            self.cards[index].clear_pixmap()

            if show_in_preview:
                self.clear_preview.emit()

            return False

        pixmap = self._load_fresh_pixmap(path)

        if pixmap.isNull():
            self.cards[index].set_asset_state(
                "warning"
            )
            return False

        self.image_paths[index] = path
        self.cards[index].set_pixmap(pixmap)

        if show_in_preview:
            self.hover_preview_image.emit(
                pixmap
            )

        return True

    def refresh_cards(self) -> None:
        for index in self.labels:
            self._refresh_card_from_disk(index)

    def refresh_from_disk(self) -> None:
        """Refresh Image-owned card state after a project restoration."""
        self.refresh_cards()

    def focus_asset_slot(
            self,
            target: str,
            *,
            open_picker: bool = True,
    ) -> None:
        """Focus the named image slot and optionally open its picker."""
        index = {
            "cover": 1,
            "letter": 2,
            "wall": 3,
            "back": 4,
        }.get(str(target))
        if index is None:
            return
        card = self.cards.get(index)
        if card is not None:
            card.setFocus(Qt.OtherFocusReason)
        if open_picker:
            self._pick_image_dialog(index)

    # ─────────────────────────────────────────────────────────────────────
    # Prompt Writer positioning
    # ─────────────────────────────────────────────────────────────────────

    def _find_preview_surface(
            self,
    ) -> Optional[QtWidgets.QWidget]:
        """
        Return the shared preview frame owned by the main window.
        """
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
        """
        Return the top edge of the image cards in main-window coordinates.
        """
        card_tops: list[int] = []

        for card in self.cards.values():
            if not card.isVisible():
                continue

            card_position = card.mapTo(
                window,
                QPoint(0, 0),
            )

            card_tops.append(
                card_position.y()
            )

        if not card_tops:
            return None

        return min(card_tops)

    def _position_prompt_writer_button(self) -> None:
        """
        Position the Prompt Writer button beside the shared preview.

        The button is parented to the main window so it can occupy the preview
        region above the Image tab content. Its visibility is controlled by
        ImageTab.showEvent() and ImageTab.hideEvent().
        """
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

        preview_frame = self._find_preview_surface()

        if preview_frame is None:
            self.pwrite_fab.hide()
            return

        if self.pwrite_fab.parent() is not window:
            self.pwrite_fab.setParent(window)

        self._fab_surface = window

        self.pwrite_fab.set_surface(
            window
        )

        preview_position = preview_frame.mapTo(
            window,
            QPoint(0, 0),
        )

        x_position = max(
            0,
            self.FAB_FIXED_X,
        )

        # Vertically center the button against the shared preview area.
        y_position = (
                preview_position.y()
                + (
                        preview_frame.height()
                        - self.pwrite_fab.height()
                )
                // 2
        )

        # The button must never overlap the image cards.
        cards_top = self._cards_top_in_window(
            window
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

        y_position = max(
            0,
            y_position,
        )

        self.pwrite_fab.move(
            QPoint(
                x_position,
                y_position,
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

        self.refresh_cards()
        self._schedule_prompt_writer_position()

    def hideEvent(
            self,
            event: QtGui.QHideEvent,
    ) -> None:
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

        if (
                hasattr(self, "pwrite_fab")
                and self.isVisible()
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
                f"Select {self.labels[index][0]}",
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

    def set_image_path(
            self,
            index: int,
            source_path: str,
    ) -> None:
        if index not in self.labels:
            return

        _label, filename = self.labels[index]

        pages_directory = (
            self._user_pages_dir()
        )

        os.makedirs(
            pages_directory,
            exist_ok=True,
        )

        destination_path = os.path.join(
            pages_directory,
            filename,
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
            self.status.setText(
                f"Failed to process "
                f"{filename}: {error}"
            )

            self.cards[index].set_asset_state(
                "warning"
            )

            return

        pixmap = QtGui.QPixmap(
            destination_path
        )

        if pixmap.isNull():
            self.status.setText(
                f"Invalid image: {filename}"
            )

            self.cards[index].set_asset_state(
                "warning"
            )

            return

        self.image_paths[index] = (
            destination_path
        )

        self.cards[index].set_pixmap(
            pixmap
        )

        self.image_selected.emit(
            pixmap
        )

        self.status.setText(
            f"{filename} saved."
        )

    def clear_image(
            self,
            index: int,
    ) -> None:
        if index not in self.labels:
            return

        _label, filename = self.labels[index]

        path = os.path.join(
            self._user_pages_dir(),
            filename,
        )

        try:
            if os.path.isfile(path):
                os.remove(path)

        except OSError as error:
            self.status.setText(
                f"Could not clear "
                f"{filename}: {error}"
            )

            return

        self.image_paths[index] = None

        self.cards[index].clear_pixmap()

        self.clear_preview.emit()

        self.status.setText(
            f"{filename} cleared."
        )

    def preview_from_gallery(
            self,
            index: int,
    ) -> None:
        # Hovering scans only this slot. If another part of the app replaced
        # its gallery image, both the card thumbnail and preview update now.
        self._refresh_card_from_disk(
            index,
            show_in_preview=True,
        )

    def reset_images(self) -> None:
        for index in self.labels:
            _label, filename = self.labels[index]

            path = os.path.join(
                self._user_pages_dir(),
                filename,
            )

            try:
                if os.path.isfile(path):
                    os.remove(path)

            except OSError:
                pass

            self.image_paths[index] = None

            self.cards[index].clear_pixmap()

        self.clear_preview.emit()

        self.status.setText(
            "All images cleared."
        )

    def open_gallery_folder(self) -> None:
        pages_directory = (
            self._user_pages_dir()
        )

        os.makedirs(
            pages_directory,
            exist_ok=True,
        )

        QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                pages_directory
            )
        )

    def _show_temporary_status(
            self,
            message: str,
            duration_ms: int = 3000,
    ) -> None:
        """Show the latest action briefly, then clear it."""
        self._status_clear_timer.stop()
        self.status.setText(message)

        if duration_ms > 0:
            self._status_clear_timer.start(
                duration_ms
            )

    def _track_prompt_writer_panel(
            self,
            panel: object,
    ) -> None:
        """Listen for the current Prompt Writer window closing."""
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
            self._prompt_writer_panel = panel
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
                "Prompt Writer opener not found "
                "on the main window.",
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
                f"Could not open Prompt Writer: {error}",
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
            "Prompt Writer focused."
            if was_visible
            else "Prompt Writer opened."
        )
