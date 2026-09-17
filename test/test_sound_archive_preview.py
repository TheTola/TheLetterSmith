from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6 import QtCore, QtTest, QtWidgets
from PySide6.QtMultimedia import QMediaPlayer

from sound_model import (
    ProjectSoundState,
    TrackRecord,
    library_path,
    load_library,
    load_project_state,
    save_library,
    save_project_state,
)
from sound_tab import ArchiveDialog, PlaylistItemWidget, SoundTab, StockMusicDialog
from recent_media import recent_media
from ui_sounds import UiSound


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


class _StockLibrary(_Library):
    def __init__(self) -> None:
        super().__init__()
        self.record.source_kind = "stock"
        self.record.resource_file = "music/stock song Song.mp3"


class _MixedLibrary(_Library):
    def __init__(self) -> None:
        super().__init__()
        self.stock_record = TrackRecord(
            track_id="stock-track",
            content_hash="stock-hash",
            display_title="Stock Song",
            original_name="stock-song.mp3",
            original_file="",
            processed_file="stock-song.mp3",
            duration_seconds=1.0,
            added_at="2000-01-01T00:00:00+00:00",
            source_kind="stock",
            resource_file="music/stock-song.mp3",
        )

    def all_records(self, _sort: str) -> list[TrackRecord]:
        return [self.record, self.stock_record]

    def get(self, track_id: str) -> TrackRecord:
        if track_id == self.stock_record.track_id:
            return self.stock_record
        return self.record


class SoundArchivePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_music_mode_changes_use_blip_only_when_mode_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                with (
                    mock.patch.object(tab, "_choose_new_files"),
                    mock.patch("sound_tab.play_ui_sound") as play,
                ):
                    tab._create_playlist()
                    tab._create_playlist()
                    tab._convert_to_single()
                self.assertEqual(
                    play.call_args_list,
                    [mock.call(UiSound.BLIP), mock.call(UiSound.BLIP)],
                )
            finally:
                tab.close()

    def test_failed_import_uses_error_but_cancellation_stays_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                with (
                    mock.patch("sound_tab.show_lettersmith_message"),
                    mock.patch("sound_tab.play_ui_sound") as play,
                ):
                    tab._import_failed("Invalid audio file")
                    tab._import_failed("Import canceled")
                play.assert_called_once_with(UiSound.ERROR)
            finally:
                tab.close()

    def test_playlist_remove_is_compact_and_tracks_stay_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                tab.resize(960, 720)
                tab.project_sound.state.mode = "playlist"
                tab.project_sound.state.playlist_expanded = False
                tab.show()
                tab.mode_stack.setCurrentWidget(tab.playlist_panel)
                tab._refresh_playlist()
                self.app.processEvents()
                self.assertFalse(hasattr(tab, "expand_btn"))
                self.assertTrue(tab.playlist_list.isVisible())

                row = PlaylistItemWidget(_Library().record, False, tab)
                row.show()
                self.app.processEvents()
                remove = row.findChild(QtWidgets.QToolButton, "playlistRemove")
                self.assertEqual(remove.size(), QtCore.QSize(24, 24))
                self.assertLessEqual(remove.geometry().bottom(), row.height())
                self.assertGreater(remove.geometry().x(), row.width() - 40)
                title = row._title_label
                self.assertLessEqual(
                    title.fontMetrics().horizontalAdvance(title.text()),
                    title.width(),
                )
                removed: list[str] = []
                row.removeRequested.connect(removed.append)
                remove.click()
                self.assertEqual(removed, ["track-1"])
                row.close()
            finally:
                tab.close()

    def test_playlist_tracks_wrap_before_vertical_scrolling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                tab.resize(960, 720)
                tab.mode_stack.setCurrentWidget(tab.playlist_panel)
                tab.playlist_list.setVisible(True)
                tab.show()
                for index in range(30):
                    item = QtWidgets.QListWidgetItem(str(index))
                    item.setSizeHint(QtCore.QSize(274, 44))
                    tab.playlist_list.addItem(item)
                self.app.processEvents()

                view = tab.playlist_list
                rects = [view.visualItemRect(view.item(index)) for index in range(10)]
                self.assertTrue(
                    any(rect.y() == rects[0].y() and rect.x() > rects[0].x()
                        for rect in rects[1:])
                )
                self.assertTrue(any(rect.y() > rects[0].y() for rect in rects[1:]))
                self.assertGreaterEqual(view.viewport().height(), 2 * view.gridSize().height())
                self.assertGreater(view.verticalScrollBar().maximum(), 0)
                self.assertEqual(view.horizontalScrollBarPolicy(), QtCore.Qt.ScrollBarAlwaysOff)
            finally:
                tab.close()

    def test_sound_buttons_open_separate_user_and_stock_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                tab.resize(960, 720)
                tab.show()
                self.app.processEvents()
                with mock.patch.object(tab, "_open_music_selector") as selector:
                    tab.single_action_btn.click()
                    tab.project_sound.state.mode = "playlist"
                    tab._refresh_ui()
                    tab.add_track_btn.click()
                    tab.stock_btn.click()
                self.assertEqual(tab.stock_btn.text(), "Stock Music")
                self.assertEqual(
                    selector.call_args_list,
                    [
                        mock.call(user_music=True),
                        mock.call(user_music=True),
                        mock.call(user_music=False),
                    ],
                )
            finally:
                tab.close()

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

    def test_stock_track_is_selectable_but_read_only(self) -> None:
        dialog = StockMusicDialog(
            _StockLibrary(),
            multi_select=False,
        )
        try:
            dialog.track_list.setCurrentRow(0)
            self.app.processEvents()
            self.assertEqual(dialog.heading.text(), "Stock Music")
            self.assertEqual(dialog.track_list.item(0).text(), "Song")
            self.assertTrue(dialog.choose_btn.isEnabled())
            self.assertFalse(hasattr(dialog, "rename_btn"))
            self.assertFalse(hasattr(dialog, "delete_btn"))
            self.assertIsNone(dialog.findChild(QtWidgets.QTableWidget))
        finally:
            dialog.close()
            self.app.processEvents()

    def test_add_track_shows_only_choose_music_and_recent_songs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library = _MixedLibrary()
            library.path_for = mock.Mock(return_value=Path(temp_dir) / "song.mp3")
            tab = mock.Mock()
            tab.project_root = Path(temp_dir)
            tab.project_state.is_project_ready = True
            tab.project_state.identity.project_id = "project-a"
            tab.library = library
            SoundTab._remember_tracks(tab, ["track-1"])
            self.assertEqual(
                recent_media(tab.project_root, "project-a", "music")[0][0],
                "track-1",
            )

            dialog = StockMusicDialog(
                library,
                multi_select=False,
                recent_track_ids=("track-1",),
                allow_browse=True,
            )
            try:
                self.assertIsNone(dialog.track_list)
                self.assertEqual(dialog.recent_list.count(), 1)
                self.assertEqual(dialog.layout().itemAt(1).widget().text(), "Choose Music…")
                self.assertFalse(any(
                    button.text() == "Stock Music"
                    for button in dialog.findChildren(QtWidgets.QPushButton)
                ))
                with mock.patch.object(dialog, "_preview"):
                    dialog.recent_list.setCurrentRow(0)
                self.assertEqual(dialog.selected_ids(), ["track-1"])
                dialog._browse()
                self.assertTrue(dialog.browse_requested)
            finally:
                dialog.close()
                self.app.processEvents()

    def test_add_track_without_history_only_shows_choose_music(self) -> None:
        dialog = StockMusicDialog(
            _StockLibrary(), multi_select=False, allow_browse=True
        )
        try:
            self.assertIsNone(dialog.recent_list)
            self.assertIsNone(dialog.track_list)
            self.assertEqual(dialog.layout().itemAt(1).widget().text(), "Choose Music…")
        finally:
            dialog.close()
            self.app.processEvents()

    def test_stock_dialog_filters_out_user_music(self) -> None:
        dialog = StockMusicDialog(
            _MixedLibrary(),
            multi_select=False,
        )
        try:
            self.assertEqual(dialog.windowTitle(), "Stock Music")
            self.assertEqual(dialog.track_list.count(), 1)
            self.assertEqual(dialog.track_list.item(0).text(), "Stock Song")
            self.assertIsNone(dialog.recent_list)
            self.assertFalse(any(
                button.text() == "Choose Music…"
                for button in dialog.findChildren(QtWidgets.QPushButton)
            ))
        finally:
            dialog.close()
            self.app.processEvents()

    def test_popups_are_frameless_and_archive_has_detailed_user_rows(self) -> None:
        archive = ArchiveDialog(
            _MixedLibrary(),
            lambda: {"track-1"},
            lambda _track_id: True,
            multi_select=False,
        )
        stock = StockMusicDialog(
            _MixedLibrary(),
            multi_select=False,
        )
        try:
            for dialog in (archive, stock):
                self.assertTrue(dialog.windowFlags() & QtCore.Qt.Popup)
                self.assertTrue(
                    dialog.windowFlags() & QtCore.Qt.FramelessWindowHint
                )
            self.assertEqual(archive.windowTitle(), "Archive")
            self.assertEqual(archive.heading.text(), "Archive")
            self.assertEqual(archive.table.rowCount(), 1)
            self.assertEqual(archive.table.columnCount(), 3)
            self.assertTrue(archive.table.verticalHeader().isHidden())
            self.assertEqual(
                [
                    archive.table.horizontalHeaderItem(column).text()
                    for column in range(3)
                ],
                ["Title", "Duration", "Added"],
            )
            self.assertEqual(archive.table.item(0, 0).text(), "Song")
            self.assertEqual(archive.table.item(0, 1).text(), "0:01")
            self.assertEqual(archive.table.item(0, 2).text(), "2026-01-01")
            self.assertEqual(
                [
                    index.row()
                    for index in archive.table.selectionModel().selectedRows()
                ],
                [0],
            )
            self.assertIn("QTableWidget::item:selected", archive.styleSheet())
            self.assertIn("background: #1c5275", archive.styleSheet())
            self.assertIn("alternate-background-color", archive.styleSheet())
        finally:
            archive.close()
            stock.close()
            self.app.processEvents()

    def test_stock_selection_emits_track_without_archive_mutation_controls(self) -> None:
        dialog = StockMusicDialog(
            _StockLibrary(),
            multi_select=False,
        )
        chosen = QtTest.QSignalSpy(dialog.tracksChosen)
        try:
            dialog.track_list.setCurrentRow(0)
            dialog._choose()
            self.assertEqual(chosen.count(), 1)
            self.assertEqual(chosen.at(0)[0], ["track-1"])
        finally:
            dialog.close()
            self.app.processEvents()

    def test_clicking_outside_dismisses_stock_popup(self) -> None:
        host = QtWidgets.QWidget()
        host.resize(700, 500)
        host.show()
        dialog = StockMusicDialog(
            _StockLibrary(),
            multi_select=False,
            parent=host,
        )
        try:
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            QtTest.QTest.mouseClick(
                host,
                QtCore.Qt.LeftButton,
                pos=QtCore.QPoint(4, 4),
            )
            self.app.processEvents()
            self.assertFalse(dialog.isVisible())
            self.assertTrue(dialog.preview_player.source().isEmpty())
        finally:
            dialog.close()
            host.close()
            self.app.processEvents()

    def test_legacy_stock_copy_is_separated_and_project_ids_are_preserved(self) -> None:
        root = Path.cwd() / f".sound-popup-test-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        try:
            stock_dir = root / "resources" / "stock"
            music_dir = stock_dir / "music"
            music_dir.mkdir(parents=True)
            stock_file = music_dir / "stock song Test.mp3"
            stock_file.write_bytes(b"bundled stock audio")
            (stock_dir / "stock_manifest.json").write_text(
                json.dumps(
                    {
                        "music": [
                            {
                                "filename": "music/stock song Test.mp3",
                                "title": "Test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(stock_file.read_bytes()).hexdigest()
            canonical_id = digest[:24]
            legacy_id = digest[:16]
            legacy_stock = TrackRecord(
                track_id=legacy_id,
                content_hash=digest,
                display_title="stock song Test",
                original_name="stock song Test.mp3",
                original_file="legacy-stock.mp3",
                processed_file="legacy-stock.mp3",
                duration_seconds=12.0,
            )
            user_record = TrackRecord(
                track_id="user-track",
                content_hash="user-hash",
                display_title="User Song",
                original_name="user.mp3",
                original_file="user.mp3",
                processed_file="user.mp3",
                duration_seconds=42.0,
            )
            save_library(
                root,
                {
                    legacy_id: legacy_stock,
                    user_record.track_id: user_record,
                },
            )
            save_project_state(
                root,
                ProjectSoundState(
                    mode="playlist",
                    playlist=[
                        legacy_id,
                        user_record.track_id,
                        f"stock:{canonical_id}",
                    ],
                    selected_track_id=legacy_id,
                ),
            )

            records = load_library(root)
            state = load_project_state(
                root,
                valid_ids=set(records),
            )

            self.assertEqual(
                set(records),
                {canonical_id, user_record.track_id},
            )
            self.assertEqual(records[canonical_id].source_kind, "stock")
            self.assertEqual(
                state.playlist,
                [canonical_id, user_record.track_id],
            )
            self.assertEqual(state.selected_track_id, canonical_id)

            save_library(root, records)
            persisted = json.loads(
                library_path(root).read_text(encoding="utf-8")
            )["tracks"]
            self.assertEqual(set(persisted), {user_record.track_id})
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_add_music_opens_in_downloads(self) -> None:
        tab = SimpleNamespace(
            project_sound=SimpleNamespace(
                state=SimpleNamespace(mode="single")
            ),
            _remember_music_folder=mock.Mock(),
            _start_import=mock.Mock(),
        )
        with (
            mock.patch(
                "sound_tab.QtCore.QStandardPaths.writableLocation",
                return_value="C:/Users/Test/Downloads",
            ),
            mock.patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=("", ""),
            ) as choose,
        ):
            SoundTab._choose_new_files(tab)

        self.assertEqual(choose.call_args.args[2], "C:/Users/Test/Downloads")


if __name__ == "__main__":
    unittest.main()
