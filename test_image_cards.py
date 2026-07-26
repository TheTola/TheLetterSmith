from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from Image_tab import ImageState, ImageTab, assess_image


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_all_four_canonical_mappings() -> None:
    assert ImageTab.SLOT_MAP == {
        1: ("Cover Page Image", "cover.png"),
        2: ("Main Letter Image", "letter.png"),
        3: ("Letter Background Image", "wall.png"),
        4: ("Final Backdrop Image", "back.png"),
    }


def test_image_state_missing_unreadable_low_resolution_and_ready(tmp_path: Path) -> None:
    missing = assess_image(tmp_path / "missing.png")
    assert missing.state is ImageState.MISSING

    unreadable_path = tmp_path / "unreadable.png"
    unreadable_path.write_bytes(b"not an image")
    assert assess_image(unreadable_path).state is ImageState.MISSING

    low_path = _image(tmp_path / "low.png", (240, 360), (20, 40, 60))
    low = assess_image(low_path)
    assert low.state is ImageState.WARNING
    assert "resolution" in low.reason.lower()

    ready_path = _image(tmp_path / "ready.png", (1200, 1800), (20, 40, 60))
    assert assess_image(ready_path).state is ImageState.READY


def test_existing_images_are_restored_without_state_text(tmp_path: Path) -> None:
    _app()
    pages = tmp_path / "gallery/user/pages"
    _image(pages / "cover.png", (1200, 1800), (100, 20, 30))

    tab = ImageTab(tmp_path)
    card = tab.cards[1]

    assert card.image_area.image_state is ImageState.READY
    assert card.image_area.pixmap() is not None
    assert card.image_area.text() not in {"Ready", "Missing", "Warning"}
    tab.close()


def test_crop_metadata_persists_and_recrops_from_original(tmp_path: Path) -> None:
    _app()
    source = tmp_path / "source.png"
    image = Image.new("RGB", (1800, 1800), (255, 0, 0))
    image.paste((0, 0, 255), (900, 0, 1800, 1800))
    image.save(source)
    tab = ImageTab(tmp_path)
    tab.set_image_path(1, str(source))

    original = tmp_path / "gallery/user/pages/originals/cover.png"
    original_bytes = original.read_bytes()
    tab.apply_crop(1, zoom=1.5, center_x=0.25, center_y=0.5)
    first_crop = (tmp_path / "gallery/user/pages/cover.png").read_bytes()
    tab.apply_crop(1, zoom=1.5, center_x=0.75, center_y=0.5)
    second_crop = (tmp_path / "gallery/user/pages/cover.png").read_bytes()
    metadata = json.loads(
        (tmp_path / "gallery/user/pages/crops.json").read_text(encoding="utf-8")
    )

    assert original.read_bytes() == original_bytes
    assert first_crop != second_crop
    assert metadata["cover.png"]["source"] == "originals/cover.png"
    assert metadata["cover.png"]["center_x"] == 0.75
    tab.close()


def test_per_card_clear_removes_only_that_slot(tmp_path: Path) -> None:
    _app()
    source = _image(tmp_path / "source.png", (1200, 1800), (5, 10, 15))
    tab = ImageTab(tmp_path)
    tab.set_image_path(1, str(source))
    tab.set_image_path(2, str(source))
    tab.apply_crop(1, zoom=1.1, center_x=0.5, center_y=0.5)

    tab.clear_image(1)

    pages = tmp_path / "gallery/user/pages"
    assert not (pages / "cover.png").exists()
    assert not (pages / "originals/cover.png").exists()
    assert (pages / "letter.png").is_file()
    assert "cover.png" not in json.loads((pages / "crops.json").read_text(encoding="utf-8"))
    assert tab.cards[1].image_area.image_state is ImageState.MISSING
    tab.close()


def test_drag_drop_replaces_image_and_click_opens_selection(tmp_path: Path) -> None:
    app = _app()
    first = _image(tmp_path / "first.png", (1200, 1800), (10, 20, 30))
    second = _image(tmp_path / "second.png", (1200, 1800), (200, 210, 220))
    tab = ImageTab(tmp_path)
    area = tab.cards[1].image_area

    with mock.patch.object(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        return_value=(str(first), "Images"),
    ) as picker:
        QtTest.QTest.mouseClick(area, QtCore.Qt.MouseButton.LeftButton)
        app.processEvents()
    picker.assert_called_once()

    before = (tmp_path / "gallery/user/pages/cover.png").read_bytes()
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(second))])
    event = QtGui.QDropEvent(
        QtCore.QPointF(5, 5),
        QtCore.Qt.DropAction.CopyAction,
        mime,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    area.dropEvent(event)
    app.processEvents()

    assert event.isAccepted()
    assert (tmp_path / "gallery/user/pages/cover.png").read_bytes() != before
    tab.close()
