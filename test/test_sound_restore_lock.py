from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sound_tab import SoundTab
from transactional_io import _remove_path


class SoundRestoreLockTests(unittest.TestCase):
    def test_restore_release_stops_sound_workers_and_media(self) -> None:
        sound_tab = SoundTab.__new__(SoundTab)
        sound_tab._analysis = mock.Mock()
        sound_tab._analysis_key = "current-track"
        sound_tab.deactivate_for_tab_change = mock.Mock()
        sound_tab._stop_background_threads = mock.Mock()
        sound_tab.release_current_file_handle = mock.Mock()

        SoundTab.release_project_files_for_restore(sound_tab)

        sound_tab.deactivate_for_tab_change.assert_called_once_with()
        sound_tab._stop_background_threads.assert_called_once_with()
        sound_tab._analysis.shutdown.assert_called_once_with()
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


if __name__ == "__main__":
    unittest.main()
