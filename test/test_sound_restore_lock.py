from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sound_tab import SoundTab
from transactional_io import _remove_path, recover_stale_transactions


class SoundRestoreLockTests(unittest.TestCase):
    def test_tab_activation_reloads_only_once_while_active(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=False,
            _shutdown=False,
            _preview=mock.Mock(),
            reload_project_from_disk=mock.Mock(),
        )

        SoundTab.activate_for_tab_change(sound_tab)
        SoundTab.activate_for_tab_change(sound_tab)

        self.assertTrue(sound_tab._tab_active)
        sound_tab._preview.set_tab_active.assert_called_once_with(True)
        sound_tab.reload_project_from_disk.assert_called_once_with()

    def test_tab_deactivation_runs_only_once_while_inactive(self) -> None:
        sound_tab = SimpleNamespace(
            _tab_active=True,
            player=mock.Mock(),
            _preview=mock.Mock(),
            play_btn=mock.Mock(),
            _save_volume=mock.Mock(),
            sound_state_changed=mock.Mock(),
        )

        SoundTab.deactivate_for_tab_change(sound_tab)
        SoundTab.deactivate_for_tab_change(sound_tab)

        self.assertFalse(sound_tab._tab_active)
        sound_tab.player.stop.assert_called_once_with(reset_position=True)
        sound_tab._preview.set_tab_active.assert_called_once_with(False)
        sound_tab.play_btn.setText.assert_called_once_with("▶")
        sound_tab._save_volume.assert_called_once_with()
        sound_tab.sound_state_changed.emit.assert_called_once_with(
            "sound-tab-deactivated"
        )

    def test_restore_release_stops_sound_workers_and_media(self) -> None:
        sound_tab = SoundTab.__new__(SoundTab)
        analysis = mock.Mock()
        sound_tab._analysis = analysis
        sound_tab._analysis_key = "current-track"
        sound_tab.deactivate_for_tab_change = mock.Mock()
        sound_tab._stop_background_threads = mock.Mock()
        sound_tab.release_current_file_handle = mock.Mock()

        SoundTab.prepare_for_project_restore(sound_tab)

        sound_tab.deactivate_for_tab_change.assert_called_once_with()
        sound_tab._stop_background_threads.assert_called_once_with()
        analysis.shutdown.assert_called_once_with()
        self.assertIsNone(sound_tab._analysis)
        sound_tab.release_current_file_handle.assert_called_once_with()
        self.assertEqual(sound_tab._analysis_key, "")

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
