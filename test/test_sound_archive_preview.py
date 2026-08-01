from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtMultimedia import QMediaPlayer

from sound_model import TrackRecord
from sound_tab import ArchiveDialog


class _Library(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project_root = Path.cwd()
        self.record = TrackRecord(
            track_id="track-1",
            content_hash="hash",
            display_title="Song",
            original_name="song.mp3",
            original_file="song.mp3",
            processed_file="song.mp3",
            duration_seconds=1.0,
            added_at="2026-01-01T00:00:00+00:00",
        )

    def all_records(self, _sort: str) -> list[TrackRecord]:
        return [self.record]

    def path_for(self, _track_id: str) -> None:
        return None

    def get(self, _track_id: str) -> TrackRecord:
        return self.record


class SoundArchivePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_archive_preview_loops_until_explicitly_stopped(self) -> None:
        dialog = ArchiveDialog(
            _Library(),
            lambda: set(),
            lambda _track_id: True,
            multi_select=False,
        )
        try:
            self.assertEqual(
                dialog.preview_player.loops(),
                QMediaPlayer.Loops.Infinite,
            )
            dialog._stop_preview()
            self.assertTrue(dialog.preview_player.source().isEmpty())
        finally:
            dialog.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
