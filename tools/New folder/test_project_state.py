from __future__ import annotations

import json
from pathlib import Path

from project_state import inspect_project_state
from settings_store import SettingsStore


def test_project_state_inspection_empty_workspace(tmp_path: Path) -> None:
    state = inspect_project_state(tmp_path)

    assert state.recipient == ""
    assert state.title == ""
    assert not any(image.exists for image in state.images.values())
    assert not state.message.has_content
    assert state.playlist.tracks == ()
    assert not state.current_music.exists
    assert state.saved_forge_build is None
    assert state.published_url == ""
    assert state.recovery_snapshot is None


def test_project_state_inspection_populated_workspace(tmp_path: Path) -> None:
    pages = tmp_path / "gallery/user/pages"
    message = tmp_path / "gallery/user/message"
    sounds = tmp_path / "gallery/user/sounds"
    build = tmp_path / "output/Play/ada/birthday"
    recovery = tmp_path / "output/Recovery/20260726-010203"
    for directory in (pages, message, sounds, build, recovery):
        directory.mkdir(parents=True)

    for name in ("cover.png", "letter.png", "wall.png", "back.png"):
        (pages / name).write_bytes(b"image")
    (message / "message.html").write_text("<p>Hello Ada</p>", encoding="utf-8")
    (sounds / "music.mp3").write_bytes(b"music")
    (sounds / "playlist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tracks": [{"archive_name": "first.mp3"}, {"archive_name": "second.mp3"}],
                "repeat": False,
                "crossfade_ms": 1000,
            }
        ),
        encoding="utf-8",
    )
    (build / "index.html").write_text("<html></html>", encoding="utf-8")
    SettingsStore(tmp_path).update_fields(
        recipient_name="Ada",
        recipient_title="Birthday",
        published_page_url="https://example.test/letters/ada",
    )

    state = inspect_project_state(tmp_path)

    assert state.recipient == "Ada"
    assert state.title == "Birthday"
    assert all(image.exists for image in state.images.values())
    assert state.message.has_content
    assert state.playlist.tracks == ("first.mp3", "second.mp3")
    assert not state.playlist.repeat
    assert state.playlist.crossfade_ms == 1000
    assert state.current_music.exists
    assert state.saved_forge_build == build.resolve()
    assert state.published_url == "https://example.test/letters/ada"
    assert state.recovery_snapshot == recovery.resolve()
