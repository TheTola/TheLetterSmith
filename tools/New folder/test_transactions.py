from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtWidgets

import Forge_Tab
from config import REQUIRED_SLIDES
from saved_letters import SavedLetter
from settings_store import SettingsStore


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
    entry = SavedLetter(
        path=play_dir,
        recipient="Person",
        title="Title",
        modified_at=datetime.fromtimestamp(play_dir.stat().st_mtime),
        published_url="",
        cover_path=saved_pages / "cover.png",
    )

    with mock.patch.object(
        Forge_Tab.SettingsStore,
        "update_fields",
        side_effect=OSError("simulated settings failure"),
    ):
        forge._load_saved_letter(entry)

    assert (current_pages / "original.txt").read_text(encoding="utf-8") == "original pages"
    assert (current_message / "message.html").read_text(encoding="utf-8") == "<p>Original</p>"
    assert (current_sounds / "music.mp3").read_bytes() == b"original music"
    assert "rolled back" in forge.status.toPlainText()
    assert not tuple((tmp_path / "gallery/user").glob("*.load-staging"))
    assert not tuple((tmp_path / "gallery/user").glob("*.load-backup"))
    forge.close()
    assert app is not None


def test_saved_letter_load_commits_valid_staged_workspace(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    current_pages = tmp_path / "gallery/user/pages"
    current_message = tmp_path / "gallery/user/message"
    current_pages.mkdir(parents=True)
    current_message.mkdir(parents=True)
    (current_pages / "old.txt").write_text("old", encoding="utf-8")
    (current_message / "message.html").write_text("<p>Old</p>", encoding="utf-8")

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

    entry = SavedLetter(
        path=play_dir,
        recipient="Person",
        title="Title",
        modified_at=datetime.fromtimestamp(play_dir.stat().st_mtime),
        published_url="",
        cover_path=saved_pages / "cover.png",
    )
    forge = Forge_Tab.ForgeTab(tmp_path)
    forge._load_saved_letter(entry)

    assert not (current_pages / "old.txt").exists()
    assert (current_message / "message.html").read_text(encoding="utf-8") == "<p>Replacement</p>"
    assert (tmp_path / "gallery/user/sounds/music.mp3").read_bytes() == b"replacement music"
    assert SettingsStore(tmp_path).get("recipient_name") == "Person"
    assert "Loaded saved letter" in forge.status.toPlainText()
    forge.close()
    assert app is not None
