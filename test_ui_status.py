from __future__ import annotations

from ui_status import StatusController, StatusLevel


def test_routine_success_does_not_erase_persistent_warning() -> None:
    controller = StatusController()
    controller.publish(
        "Image resolution is low.",
        StatusLevel.WARNING,
        persistent=True,
        key="image-quality",
    )

    visible = controller.publish("Image saved.", StatusLevel.SUCCESS)

    assert visible.text == "Image resolution is low."
    assert visible.level is StatusLevel.WARNING


def test_clearing_persistent_warning_reveals_latest_status() -> None:
    controller = StatusController()
    controller.publish("Music is optional.", StatusLevel.WARNING, persistent=True, key="music")
    controller.publish("Playlist saved.", StatusLevel.SUCCESS)

    visible = controller.clear("music")

    assert visible.text == "Playlist saved."
    assert visible.level is StatusLevel.SUCCESS
