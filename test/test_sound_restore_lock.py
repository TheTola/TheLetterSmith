from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sound_tab
from sound_model import TrackRecord, processed_dir
from sound_tab import SoundLibrary, SoundTab
from transactional_io import _remove_path, recover_stale_transactions


class SoundRestoreLockTests(unittest.TestCase):
    def test_library_availability_is_cached_until_records_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track_path = processed_dir(root) / "track.mp3"
            track_path.parent.mkdir(parents=True, exist_ok=True)
            track_path.write_bytes(b"audio")
            record = TrackRecord(
                track_id="track",
                content_hash="hash",
                display_title="Track",
                original_name="track.mp3",
                original_file="",
                processed_file=track_path.name,
                duration_seconds=1.0,
                added_at="2026-01-01T00:00:00+00:00",
            )
            library = SoundLibrary(root)
            library.replace_records({record.track_id: record})

            with mock.patch(
                "sound_tab.resolve_track_path",
                wraps=sound_tab.resolve_track_path,
            ) as resolve:
                self.assertEqual(library.available_track_ids(), {"track"})
                self.assertEqual(library.available_track_ids(), {"track"})
            self.assertEqual(resolve.call_count, 1)

            track_path.unlink()
            library.invalidate_availability()
            self.assertEqual(library.available_track_ids(), set())

    def test_tab_activation_skips_reload_when_sound_files_are_unchanged(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=False,
            _shutdown=False,
            _preview=mock.Mock(),
            _sound_disk_revision=((1, 1, 1),),
            _active_sound_revision=("same",),
            _current_sound_disk_revision=mock.Mock(
                return_value=((1, 1, 1),)
            ),
            _current_active_sound_revision=mock.Mock(return_value=("same",)),
            reload_project_from_disk=mock.Mock(),
        )

        SoundTab.activate_for_tab_change(sound_tab)
        SoundTab.activate_for_tab_change(sound_tab)

        self.assertTrue(sound_tab._tab_active)
        sound_tab._preview.set_tab_active.assert_called_once_with(True)
        sound_tab.reload_project_from_disk.assert_not_called()

    def test_tab_activation_reloads_once_after_sound_files_change(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=False,
            _shutdown=False,
            _preview=mock.Mock(),
            _sound_disk_revision=((1, 1, 1),),
            _active_sound_revision=("same",),
            _current_sound_disk_revision=mock.Mock(
                return_value=((2, 2, 2),)
            ),
            _current_active_sound_revision=mock.Mock(return_value=("same",)),
            reload_project_from_disk=mock.Mock(),
        )

        SoundTab.activate_for_tab_change(sound_tab)
        SoundTab.activate_for_tab_change(sound_tab)

        sound_tab.reload_project_from_disk.assert_called_once_with()

    def test_tab_activation_reloads_once_after_active_audio_changes(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=False,
            _shutdown=False,
            _preview=mock.Mock(),
            _sound_disk_revision=((1, 1, 1),),
            _active_sound_revision=("before",),
            _current_sound_disk_revision=mock.Mock(
                return_value=((1, 1, 1),)
            ),
            _current_active_sound_revision=mock.Mock(return_value=("after",)),
            reload_project_from_disk=mock.Mock(),
        )

        SoundTab.activate_for_tab_change(sound_tab)
        SoundTab.activate_for_tab_change(sound_tab)

        sound_tab.reload_project_from_disk.assert_called_once_with()

    def test_tab_deactivation_runs_only_once_while_inactive(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=True,
            player=mock.Mock(),
            _preview=mock.Mock(),
            play_btn=mock.Mock(),
            _save_volume=mock.Mock(),
            _status_timer=mock.Mock(),
            sound_state_changed=mock.Mock(),
        )

        SoundTab.deactivate_for_tab_change(sound_tab)
        SoundTab.deactivate_for_tab_change(sound_tab)

        self.assertFalse(sound_tab._tab_active)
        sound_tab.player.stop.assert_called_once_with(reset_position=True)
        sound_tab._preview.set_tab_active.assert_called_once_with(False)
        sound_tab.play_btn.setText.assert_called_once_with("▶")
        sound_tab._save_volume.assert_called_once_with()
        sound_tab._status_timer.stop.assert_called_once_with()
        sound_tab.sound_state_changed.emit.assert_not_called()

    def test_restore_release_stops_sound_workers_and_media(self) -> None:
        sound_tab = SoundTab.__new__(SoundTab)
        analysis = mock.Mock()
        analysis.shutdown.return_value = True
        sound_tab._analysis = analysis
        sound_tab._analysis_key = "current-track"
        sound_tab._background_generation = 4
        sound_tab.deactivate_for_tab_change = mock.Mock()
        sound_tab._stop_background_threads = mock.Mock(return_value=True)
        sound_tab.release_current_file_handle = mock.Mock()

        SoundTab.prepare_for_project_restore(sound_tab, timeout_ms=500)

        sound_tab.deactivate_for_tab_change.assert_called_once_with()
        sound_tab._stop_background_threads.assert_called_once()
        stop_timeout = sound_tab._stop_background_threads.call_args.args[0]
        self.assertGreaterEqual(stop_timeout, 0)
        self.assertLessEqual(stop_timeout, 500)
        analysis.shutdown.assert_called_once()
        analysis_timeout = analysis.shutdown.call_args.kwargs["timeout_ms"]
        self.assertGreaterEqual(analysis_timeout, 0)
        self.assertLessEqual(analysis_timeout, 500)
        analysis.deleteLater.assert_called_once_with()
        self.assertIsNone(sound_tab._analysis)
        sound_tab.release_current_file_handle.assert_called_once_with()
        self.assertEqual(sound_tab._analysis_key, "")
        self.assertEqual(sound_tab._background_generation, 5)

    def test_restore_release_fails_closed_when_sound_worker_does_not_stop(self) -> None:
        sound_tab = SoundTab.__new__(SoundTab)
        sound_tab._analysis = None
        sound_tab._background_generation = 0
        sound_tab.deactivate_for_tab_change = mock.Mock()
        sound_tab._stop_background_threads = mock.Mock(return_value=False)
        sound_tab.release_current_file_handle = mock.Mock()

        with self.assertRaisesRegex(RuntimeError, "background work"):
            SoundTab.prepare_for_project_restore(sound_tab, timeout_ms=1)

        sound_tab.release_current_file_handle.assert_not_called()

    def test_persist_updates_sound_revisions_after_self_write(self) -> None:
        sound_tab = SimpleNamespace(
            project_sound=mock.Mock(),
            _sync_project_sound_autosave=mock.Mock(),
            _current_sound_disk_revision=mock.Mock(return_value=((2, 2, 2),)),
            _current_active_sound_revision=mock.Mock(return_value=("active",)),
            _sound_disk_revision=((1, 1, 1),),
            _active_sound_revision=("before",),
        )

        SoundTab._persist_project_sound_state(sound_tab)

        sound_tab.project_sound.persist_state.assert_called_once_with()
        sound_tab._sync_project_sound_autosave.assert_called_once_with()
        self.assertEqual(sound_tab._sound_disk_revision, ((2, 2, 2),))
        self.assertEqual(sound_tab._active_sound_revision, ("active",))

    def test_path_cleanup_retries_transient_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "staging"
            target.mkdir()
            (target / "payload.txt").write_text("payload", encoding="utf-8")
            real_rmtree = shutil.rmtree
            calls = 0

            def flaky_rmtree(path: str | os.PathLike[str], **kwargs: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("transient lock")
                real_rmtree(path, **kwargs)

            with mock.patch("transactional_io.shutil.rmtree", side_effect=flaky_rmtree):
                _remove_path(target)

            self.assertGreaterEqual(calls, 2)
            self.assertFalse(target.exists())

    def test_stale_recovery_restores_missing_project_destination_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "pages"
            backup = root / "pages.load-backup"
            backup.mkdir()
            (backup / "letter.png").write_bytes(b"old")
            staging = root / "pages.load-staging.old"
            staging.mkdir()
            os.utime(staging, (0, 0))

            recovered = recover_stale_transactions((destination,), now=100, older_than_seconds=1)

            self.assertEqual(recovered, (destination.resolve(),))
            self.assertEqual((destination / "letter.png").read_bytes(), b"old")
            self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
