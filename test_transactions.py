from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtWidgets

import Forge_Tab
from config import REQUIRED_SLIDES


class _Action:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


def test_saved_letter_load_rolls_back_assets_when_settings_commit_fails(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    current_pages = tmp_path / "gallery/user/pages"
    current_message = tmp_path / "gallery/user/message"
    current_sounds = tmp_path / "gallery/user/sounds"
    current_pages.mkdir(parents=True)
    current_message.mkdir(parents=True)
    current_sounds.mkdir(parents=True)
    (current_pages / "original.txt").write_text("original pages", encoding="utf-8")
    (current_message / "message.html").write_text("<p>Original</p>", encoding="utf-8")
    (current_sounds / "music.mp3").write_bytes(b"original music")

    play_dir = tmp_path / "output/Play/person/title"
    saved_pages = play_dir / "gallery/pages"
    saved_message = play_dir / "gallery/message"
    saved_sounds = play_dir / "gallery/sounds"
    saved_pages.mkdir(parents=True)
    saved_message.mkdir(parents=True)
    saved_sounds.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(saved_pages / name)
    (saved_message / "message.html").write_text("<p>Replacement</p>", encoding="utf-8")
    (saved_sounds / "music.mp3").write_bytes(b"replacement music")
    (play_dir / "index.html").write_text("<html><title>Title</title></html>", encoding="utf-8")

    forge = Forge_Tab.ForgeTab(tmp_path)
    action = _Action(
        {
            "play_dir": str(play_dir),
            "recipient_display": "Person",
            "title_display": "Title",
            "recipient_slug": "person",
        }
    )

    with mock.patch.object(
        Forge_Tab.SettingsStore,
        "update_fields",
        side_effect=OSError("simulated settings failure"),
    ):
        forge._load_from_action(action)

    assert (current_pages / "original.txt").read_text(encoding="utf-8") == "original pages"
    assert (current_message / "message.html").read_text(encoding="utf-8") == "<p>Original</p>"
    assert (current_sounds / "music.mp3").read_bytes() == b"original music"
    assert "rolled back" in forge.status.toPlainText()
    assert not tuple((tmp_path / "gallery/user").glob("*.load-staging"))
    assert not tuple((tmp_path / "gallery/user").glob("*.load-backup"))
    forge.close()
    assert app is not None
