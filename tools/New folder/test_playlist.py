from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtWidgets

import audio_export
import generate
import sound_tab
from config import CONTROL_FILES, REQUIRED_SLIDES
from playlist import CROSSFADE_MS, PlaylistStore
from playlist_player import PlaylistPlayer


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _minimal_project(root: Path) -> None:
    pages = root / "gallery/user/pages"
    controls = root / "gallery/user/card/controls"
    message = root / "gallery/user/message"
    sounds = root / "gallery/user/sounds"
    app_sounds = root / "gallery/app/sounds"
    for directory in (pages, controls, message, sounds, app_sounds):
        directory.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_SLIDES:
        Image.new("RGBA", (8, 8), (240, 240, 240, 255)).save(pages / name)
    for name in CONTROL_FILES:
        Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / name)
    for name in ("glissando.mp3", *[f"flip{i}.mp3" for i in range(1, 11)]):
        (app_sounds / name).write_bytes(b"sfx")
    (root / "settings.json").write_text(
        json.dumps({"recipient_name": "Ada", "recipient_title": "Playlist"}),
        encoding="utf-8",
    )


def test_playlist_migrates_legacy_music_and_uses_fixed_crossfade(tmp_path: Path) -> None:
    music = tmp_path / "gallery/user/sounds/music.mp3"
    music.parent.mkdir(parents=True)
    music.write_bytes(b"legacy")

    playlist = PlaylistStore(tmp_path).load()

    assert [track.archive_name for track in playlist.tracks] == ["music.mp3"]
    assert playlist.crossfade_ms == CROSSFADE_MS == 1000
    data = json.loads(
        (tmp_path / "gallery/user/sounds/playlist.json").read_text(encoding="utf-8")
    )
    assert data["crossfade_ms"] == 1000


def test_reorder_and_remove_do_not_delete_archive(tmp_path: Path) -> None:
    archive = tmp_path / "gallery/user/sounds/appssong/processed"
    archive.mkdir(parents=True)
    for name in ("one.mp3", "two.mp3", "three.mp3"):
        (archive / name).write_bytes(name.encode())
    store = PlaylistStore(tmp_path)
    store.add("one.mp3")
    store.add("two.mp3")
    store.add("three.mp3")

    reordered = store.reorder(2, 0)
    removed = store.remove(1)

    assert [track.archive_name for track in reordered.tracks] == [
        "three.mp3",
        "one.mp3",
        "two.mp3",
    ]
    assert [track.archive_name for track in removed.tracks] == ["three.mp3", "two.mp3"]
    assert (archive / "one.mp3").is_file()


def test_playlist_player_uses_two_players_and_completion_modes(tmp_path: Path) -> None:
    _app()
    paths = []
    for name in ("one.mp3", "two.mp3", "three.mp3"):
        path = tmp_path / name
        path.write_bytes(b"audio")
        paths.append(path)
    player = PlaylistPlayer()
    player.set_tracks(paths, repeat=False)

    assert len(player.players) == 2
    assert len(player.audio_outputs) == 2
    assert player.crossfade_ms == 1000
    assert player.next_index(0) == 1
    assert player.next_index(2) is None

    player.set_repeat(True)
    assert player.next_index(2) == 0
    player.set_tracks(paths[:1], repeat=True)
    assert player.next_index(0) == 0
    player.shutdown()


def test_playlist_player_keeps_a_valid_track_when_current_is_removed(tmp_path: Path) -> None:
    _app()
    paths = []
    for name in ("one.mp3", "two.mp3", "three.mp3"):
        path = tmp_path / name
        path.write_bytes(b"audio")
        paths.append(path)
    player = PlaylistPlayer()
    player.set_tracks(paths, repeat=False)
    player.current_index = 1
    player._load_active()

    player.set_tracks((paths[0], paths[2]), repeat=False)

    assert player.current_path == paths[2].resolve()
    player.shutdown()


def test_legacy_build_music_can_migrate_into_active_playlist(tmp_path: Path) -> None:
    legacy = tmp_path / "saved/gallery/sounds/music.mp3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy build")
    store = PlaylistStore(tmp_path / "active")

    playlist = store.migrate_legacy_music(legacy)

    assert [track.archive_name for track in playlist.tracks] == ["music.mp3"]
    assert (tmp_path / "active/gallery/user/sounds/music.mp3").read_bytes() == b"legacy build"


def test_generation_exports_playlist_assets_and_viewer_json(tmp_path: Path) -> None:
    _minimal_project(tmp_path)
    archive = tmp_path / "gallery/user/sounds/appssong/processed"
    archive.mkdir(parents=True)
    (archive / "one.mp3").write_bytes(b"one")
    (archive / "two.mp3").write_bytes(b"two")
    store = PlaylistStore(tmp_path)
    store.add("one.mp3")
    store.add("two.mp3")
    store.set_repeat(False)

    def fake_export(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    with mock.patch.object(generate, "_export_apple_safe_mp3", fake_export):
        play_dir = generate.generate_play_bundle(str(tmp_path), message_html="")

    playlist_dir = play_dir / "gallery/sounds/playlist"
    assert (playlist_dir / "track-001.mp3").read_bytes() == b"one"
    assert (playlist_dir / "track-002.mp3").read_bytes() == b"two"
    script = (play_dir / "script.js").read_text(encoding="utf-8")
    assert '"repeat": false' in script
    assert "gallery/sounds/playlist/track-001.mp3" in script
    assert "gallery/sounds/playlist/track-002.mp3" in script
    assert "crossfade_ms" in script
    assert "const playlistCrossfadeMs = 1000" in script
    assert "Array.from({length: 2}" in script
    assert "{{PLAYLIST_JSON}}" not in script


def test_direct_mp3_fallback_requires_no_analysis_or_conversion_dependency(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp3"
    destination = tmp_path / "destination.mp3"
    source.write_bytes(b"direct mp3")

    with (
        mock.patch.object(
            audio_export,
            "_export_with_pydub",
            side_effect=RuntimeError("pydub unavailable"),
        ),
        mock.patch.object(
            audio_export,
            "_export_with_ffmpeg",
            side_effect=RuntimeError("ffmpeg unavailable"),
        ),
    ):
        audio_export._export_apple_safe_mp3(source, destination)

    assert destination.read_bytes() == b"direct mp3"


def test_sound_tab_disables_analysis_by_default(tmp_path: Path) -> None:
    _app()
    tab = sound_tab.SoundTab(tmp_path)

    assert tab._analysis is None
    tab._on_analysis_failed("unused", "decoder missing")
    assert "analysis failed" not in tab.status.text().casefold()
    tab.shutdown()
