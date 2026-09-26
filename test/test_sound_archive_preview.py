from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
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
    processed_dir,
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

    def _wait_for(self, predicate, message: str) -> None:
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 4000:
            QtTest.QTest.qWait(20)
        self.assertTrue(predicate(), message)

    @contextmanager
    def _handoff_tab(self, *, state="playing", playlist=False):
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                tab.show()
                tab.activate_for_tab_change()
                # Exercise the real audio backend with the repository's music fixtures.
                sources = sorted((Path(__file__).resolve().parents[1] / "resources/stock/music").glob("*.mp3"))
                self.assertGreaterEqual(len(sources), 2)
                records = {}
                for index, source in enumerate(sources[:2]):
                    track_id = f"handoff-{index}"
                    target = processed_dir(directory) / f"{track_id}.mp3"
                    shutil.copyfile(source, target)
                    records[track_id] = TrackRecord(
                        track_id=track_id, content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                        display_title=f"Song {index}", original_name=source.name,
                        original_file=target.name, processed_file=target.name,
                        duration_seconds=140.0,
                    )
                tab.library.replace_records(records)
                tab.library.changed.emit()
                tab.player.set_muted(True)
                tab.volume.setValue(23)
                if state != "empty":
                    tab.project_sound.set_single("handoff-0")
                    if playlist:
                        tab.project_sound.add_to_playlist(["handoff-1"])
                    self._wait_for(lambda: tab.player.player.duration() > 5000, "Song A did not load")
                    if state != "stopped":
                        tab.player.play()
                        tab.player.seek(5000)
                        self._wait_for(lambda: tab.player.player.position() > 5000, "Song A did not advance")
                    if state == "paused":
                        tab.player.pause()
                yield tab
            finally:
                tab.shutdown()
                tab.close()
                tab.deleteLater()
                self.app.processEvents()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def _run_archive(self, tab, interact, *, recent=False) -> None:
        errors = []
        if recent:
            dialog = StockMusicDialog(
                tab.library, multi_select=False, parent=tab,
                recent_track_ids=("handoff-0", "handoff-1"), allow_browse=True,
            )
        else:
            dialog = ArchiveDialog(
                tab.library, lambda: set(tab.project_sound.ordered_ids()),
                tab._delete_archive_track, multi_select=False, parent=tab,
            )
        dialog.preview_output.setMuted(True)
        overlaps = []

        def check_overlap(_state):
            if dialog.preview_player.playbackState() == QMediaPlayer.PlayingState and tab.player.is_playing():
                overlaps.append(tab.player.player.position())

        media_players = [*tab.player.players, dialog.preview_player]
        for player in media_players:
            player.playbackStateChanged.connect(check_overlap)

        def run():
            try:
                interact(dialog)
            except Exception as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QtCore.QTimer.singleShot(0, run)
        try:
            tab._exec_music_popup(dialog)
        finally:
            for player in media_players:
                player.playbackStateChanged.disconnect(check_overlap)
        self.assertEqual(overlaps, [], "Main and archive music overlapped")
        self.assertEqual(dialog.preview_player.playbackState(), QMediaPlayer.StoppedState)
        self.assertTrue(dialog.preview_player.source().isEmpty())
        if errors:
            raise errors[0]

    def _select_preview(self, dialog, track_id):
        view = dialog.table if isinstance(dialog, ArchiveDialog) else dialog.recent_list
        count = view.rowCount() if isinstance(dialog, ArchiveDialog) else view.count()
        for row in range(count):
            item = view.item(row, 0) if isinstance(dialog, ArchiveDialog) else view.item(row)
            if item.data(QtCore.Qt.UserRole) == track_id:
                if isinstance(dialog, ArchiveDialog):
                    view.selectRow(row)
                else:
                    view.setCurrentRow(row)
                break
        else:
            self.fail(f"Missing archive song {track_id}")
        self._wait_for(
            lambda: dialog.preview_player.playbackState() == QMediaPlayer.PlayingState,
            "Archive song did not start",
        )

    def test_archive_handoff_preserves_position_across_songs_and_repeated_cycles(self) -> None:
        with self._handoff_tab() as tab:
            source = tab.player.player.source()
            source_changes = QtTest.QSignalSpy(tab.player.player.sourceChanged)
            for cycle in range(3):
                paused = []

                def interact(dialog):
                    self.assertTrue(tab.player.is_playing())
                    self._select_preview(dialog, "handoff-1")
                    self.assertEqual(tab.player.player.playbackState(), QMediaPlayer.PausedState)
                    paused.append(tab.player.player.position())
                    QtTest.QTest.qWait(180)
                    self._select_preview(dialog, "handoff-0")
                    self.assertEqual(tab.player.player.position(), paused[0])
                    tab.player.play()  # Other play requests cannot overlap a preview.
                    self.assertFalse(tab.player.is_playing())
                    self.assertEqual(tab.player.player.source(), source)
                    self.assertEqual(tab.project_sound.state.selected_track_id, "handoff-0")
                    if cycle == 1:
                        dialog.choose_btn.click()  # Choosing the same song remains a no-op.
                    elif cycle == 2:
                        dialog.close()

                self._run_archive(tab, interact)
                self.assertTrue(tab.player.is_playing())
                self.assertEqual(tab.player.player.position(), paused[0])
                self._wait_for(lambda: tab.player.player.position() > paused[0], "Song A did not resume")
                self.assertEqual(tab.player.current_track_id, "handoff-0")
                self.assertEqual(source_changes.count(), 0)
                self.assertEqual(tab.volume.value(), 23)
                self.assertTrue(tab.player.is_muted())
                self.assertAlmostEqual(tab.player.outputs[tab.player.active_slot].volume(), 0.23, places=2)

    def test_archive_handoff_does_not_start_paused_stopped_or_missing_music(self) -> None:
        for state in ("paused", "stopped", "empty"):
            with self.subTest(state=state), self._handoff_tab(state=state) as tab:
                before = (tab.player.player.playbackState(), tab.player.player.position())

                def interact(dialog):
                    self._select_preview(dialog, "handoff-1")
                    QtTest.QTest.qWait(150)
                    self.assertFalse(tab.player.is_playing())

                self._run_archive(tab, interact)
                self.assertEqual((tab.player.player.playbackState(), tab.player.player.position()), before)

    def test_archive_handoff_without_a_preview_leaves_playback_untouched(self) -> None:
        with self._handoff_tab() as tab:
            changes = QtTest.QSignalSpy(tab.player.player.playbackStateChanged)
            before = tab.player.player.position()
            self._run_archive(tab, lambda _dialog: QtTest.QTest.qWait(160))
            self.assertTrue(tab.player.is_playing())
            self.assertGreater(tab.player.player.position(), before)
            self.assertEqual(changes.count(), 0)

    def test_archive_handoff_also_applies_to_recent_music_previews(self) -> None:
        with self._handoff_tab() as tab:
            paused = []

            def interact(dialog):
                self._select_preview(dialog, "handoff-1")
                self.assertFalse(tab.player.is_playing())
                paused.append(tab.player.player.position())

            self._run_archive(tab, interact, recent=True)
            self.assertTrue(tab.player.is_playing())
            self.assertEqual(tab.player.player.position(), paused[0])

    def test_archive_handoff_shutdown_does_not_resume_music(self) -> None:
        with self._handoff_tab() as tab:
            def interact(dialog):
                self._select_preview(dialog, "handoff-1")
                self.assertTrue(tab.shutdown())

            self._run_archive(tab, interact)
            self.assertTrue(tab._shutdown)
            self.assertFalse(tab.player.is_playing())
            self.assertTrue(all(player.source().isEmpty() for player in tab.player.players))
            self.assertFalse(tab.player._loop_timer.isActive())
            self.assertFalse(tab.player._crossfade_timer.isActive())

    def test_archive_handoff_respects_explicit_pause_and_stop(self) -> None:
        for action in ("pause", "stop"):
            with self.subTest(action=action), self._handoff_tab() as tab:
                def interact(dialog):
                    self._select_preview(dialog, "handoff-1")
                    getattr(tab.player, action)()

                self._run_archive(tab, interact)
                self.assertFalse(tab.player.is_playing())

    def test_archive_handoff_keeps_explicit_use_song_behavior(self) -> None:
        for state in ("playing", "paused"):
            with self.subTest(state=state), self._handoff_tab(state=state) as tab:
                def interact(dialog):
                    self._select_preview(dialog, "handoff-1")
                    dialog.choose_btn.click()

                self._run_archive(tab, interact)
                self.assertEqual(tab.project_sound.state.selected_track_id, "handoff-1")
                self.assertEqual(tab.player.current_track_id, "handoff-1")
                if state == "playing":
                    self._wait_for(tab.player.is_playing, "The chosen replacement song did not start")
                else:
                    self._wait_for(lambda: tab.player.player.duration() > 0, "The replacement song did not load")
                self.assertEqual(tab.player.is_playing(), state == "playing")

    def test_archive_handoff_preserves_an_in_progress_crossfade(self) -> None:
        with self._handoff_tab(playlist=True) as tab:
            tab.player.seek(tab.player.player.duration() - 850)
            self._wait_for(
                lambda: tab.player._transitioning and all(
                    player.playbackState() == QMediaPlayer.PlayingState for player in tab.player.players
                ), "Crossfade did not start",
            )
            snapshot = []

            def interact(dialog):
                self._select_preview(dialog, "handoff-0")
                self.assertTrue(all(player.playbackState() == QMediaPlayer.PausedState for player in tab.player.players))
                snapshot.extend(player.position() for player in tab.player.players)
                elapsed = tab.player._crossfade_elapsed
                volumes = [output.volume() for output in tab.player.outputs]
                QtTest.QTest.qWait(1100)
                self.assertEqual([player.position() for player in tab.player.players], snapshot)
                self.assertEqual(tab.player._crossfade_elapsed, elapsed)
                self.assertEqual([output.volume() for output in tab.player.outputs], volumes)

            self._run_archive(tab, interact)
            self.assertTrue(tab.player._crossfade_timer.isActive())
            self.assertEqual([player.position() for player in tab.player.players], snapshot)
            self._wait_for(lambda: tab.player.current_track_id == "handoff-1", "Crossfade did not resume")

    def test_archive_handoff_suspends_pending_loop_until_close(self) -> None:
        with self._handoff_tab() as tab:
            tab.player.seek(tab.player.player.duration() - 60)
            self._wait_for(tab.player._loop_timer.isActive, "Natural end did not schedule the loop")
            interval = tab.player._loop_timer.interval()

            def interact(dialog):
                self._select_preview(dialog, "handoff-1")
                self.assertFalse(tab.player._loop_timer.isActive())
                QtTest.QTest.qWait(interval + 100)
                self.assertFalse(tab.player.is_playing())

            self._run_archive(tab, interact)
            self.assertTrue(tab.player._loop_timer.isActive())
            self.assertEqual(tab.player._loop_timer.interval(), interval)
            self._wait_for(tab.player.is_playing, "Natural loop did not resume")

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
