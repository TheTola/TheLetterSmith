from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sound_model
import sound_tab


class _TextTarget:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = value


class _PreviewTarget:
    def __init__(self) -> None:
        self.path = ""

    def set_audio_file(self, path: str) -> None:
        self.path = path


class SoundTabStateTests(unittest.TestCase):
    def test_atomic_json_write_retries_brief_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "project_sound.json"
            destination.write_text('{"old": true}', encoding="utf-8")
            real_replace = os.replace
            attempts = 0

            def briefly_locked(source, target):
                nonlocal attempts
                if attempts == 0:
                    attempts += 1
                    raise PermissionError("simulated Windows file lock")
                return real_replace(source, target)

            with mock.patch(
                "sound_model.os.replace",
                side_effect=briefly_locked,
            ):
                sound_model.atomic_write_json(destination, {"new": True})

            self.assertEqual(attempts, 1)
            self.assertIn('"new": true', destination.read_text(encoding="utf-8"))
            self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_refreshing_current_track_does_not_rewrite_project_state(self) -> None:
        track_id = "track-1"
        record = SimpleNamespace(display_title="Track One")
        project_sound = SimpleNamespace(
            state=SimpleNamespace(
                selected_track_id=track_id,
                mode="single",
            ),
            ordered_ids=lambda: [track_id],
        )
        target = SimpleNamespace(
            project_root=Path("unused"),
            project_sound=project_sound,
            library=SimpleNamespace(
                path_for=lambda value: Path("track.mp3") if value == track_id else None,
                get=lambda value: record if value == track_id else None,
            ),
            now_playing=_TextTarget(),
            _preview=_PreviewTarget(),
            _prime_analysis_for_current=lambda: None,
        )

        with mock.patch("sound_tab.save_project_state") as save:
            sound_tab.SoundTab._on_track_changed(target, track_id)

        save.assert_not_called()
        self.assertEqual(target.now_playing.value, "Now Playing: Track One")

    def test_persistent_save_failure_is_contained(self) -> None:
        old_track_id = "track-1"
        new_track_id = "track-2"
        record = SimpleNamespace(display_title="Track Two")
        messages: list[tuple[str, bool]] = []
        project_sound = SimpleNamespace(
            state=SimpleNamespace(
                selected_track_id=old_track_id,
                mode="single",
            ),
            ordered_ids=lambda: [old_track_id, new_track_id],
        )
        target = SimpleNamespace(
            project_root=Path("unused"),
            project_sound=project_sound,
            library=SimpleNamespace(
                path_for=lambda value: Path("track.mp3"),
                get=lambda value: record,
            ),
            now_playing=_TextTarget(),
            _preview=_PreviewTarget(),
            _prime_analysis_for_current=lambda: None,
            _show_status=lambda message, persistent=False: messages.append(
                (message, persistent)
            ),
        )

        with mock.patch(
            "sound_tab.save_project_state",
            side_effect=PermissionError("still locked"),
        ):
            sound_tab.SoundTab._on_track_changed(target, new_track_id)

        self.assertEqual(project_sound.state.selected_track_id, old_track_id)
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0][1])


if __name__ == "__main__":
    unittest.main()
