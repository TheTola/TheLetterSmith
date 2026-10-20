from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import wave
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from audio_tools import AudioToolError
from project_model import MAX_PAGES, MIN_PAGES, create_project
from project_paths import application_paths
from project_repository import ProjectRepository
from project_session import get_project_session, release_project_session
from settings_store import SettingsStore
from sound_model import (
    ProjectSoundState, TrackRecord, library_path, load_library, load_project_state,
    originals_dir, processed_dir, project_sound_path, save_library, save_project_state,
)
from sound_tab import ProjectSound, SoundLibrary, SoundTab, StockMusicDialog, _ImportWorker
from test_project_model import five_page_project


class SoundProjectPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lss-")
        self.root = Path(self.temporary.name)
        self.library = SoundLibrary(self.root)
        self.original_bytes = {}
        for index in range(2):
            track_id = f"synthetic-{index}"
            path = processed_dir(self.root) / f"{track_id}.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(bytes(8000 + index * 2))
            content = path.read_bytes()
            self.original_bytes[path] = content
            self.library.records[track_id] = TrackRecord(
                track_id=track_id,
                content_hash=hashlib.sha256(content).hexdigest(),
                display_title=f"Synthetic track {index + 1}",
                original_name=path.name,
                original_file=path.name,
                processed_file=path.name,
                duration_seconds=0.5,
                added_at="2026-01-01T00:00:00+00:00",
            )
        self.library.invalidate_availability()
        save_library(self.root, self.library.records)
        self.tabs = []

    def tearDown(self) -> None:
        for tab in self.tabs:
            tab.shutdown()
            tab.close()
            tab.deleteLater()
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        release_project_session(self.root)
        self.temporary.cleanup()

    def _playlist(self) -> ProjectSound:
        sound = ProjectSound(self.root, self.library)
        sound.add_to_playlist(["synthetic-0", "synthetic-1"])
        return sound

    def _tab(self, *, playlist: bool = True) -> SoundTab:
        if playlist:
            self._playlist()
        tab = SoundTab(self.root)
        self.tabs.append(tab)
        if not tab.player.is_muted():
            tab.mute_btn.click()
        return tab

    def _assert_sources_unchanged(self) -> None:
        for path, content in self.original_bytes.items():
            self.assertEqual(path.read_bytes(), content)

    def test_page_operations_preserve_project_wide_sound_at_supported_counts(self) -> None:
        tab = self._tab()
        tab.volume.setValue(37)
        tab._save_volume()
        session = get_project_session(self.root)
        state = tab.project_sound.state.to_dict()
        authored_sound = session.project.to_dict()["sound"]
        for count in (MIN_PAGES, 5, MAX_PAGES):
            with self.subTest(count=count):
                project = five_page_project() if count == 5 else create_project(["letter"] * count)
                project.update_project_data("sound", authored_sound)
                project.update_shared_settings({"music_volume": 37, "starting_volume": 37})
                session.replace(project, asset_root=self.root)
                project = session.project
                before = project.to_dict()["sound"]
                settings = project.to_dict()["shared_settings"]
                with mock.patch.object(tab.player, "set_queue", wraps=tab.player.set_queue) as queue:
                    ids = [page["id"] for page in project.pages]
                    project.reorder_pages(list(reversed(ids)))
                    project.rename_page(ids[0], "Repeated name")
                    project.change_page_type(ids[0], "illustration")
                    project.change_page_type(ids[0], "letter")
                    if count < MAX_PAGES:
                        extra = project.add_page("letter", name="Repeated name")
                        project.delete_page(extra)
                    else:
                        removed = project.delete_page(ids[-1])
                        project.restore_page(removed)
                    self.assertEqual(project.to_dict()["sound"], before)
                    self.assertEqual(project.to_dict()["shared_settings"], settings)
                    self.assertEqual(tab.project_sound.state.to_dict(), state)
                    self.assertEqual(tab.volume.value(), 37)
                    queue.assert_not_called()
                repository = ProjectRepository(self.root / f"saved-{count}")
                repository.save(project, asset_root=self.root)
                loaded = repository.load().project
                self.assertEqual(loaded.to_dict()["sound"], before)
                self.assertEqual(loaded.to_dict()["shared_settings"], settings)
                self.assertEqual([page["id"] for page in loaded.pages], [page["id"] for page in project.pages])
        self._assert_sources_unchanged()

    def test_duplicate_rule_reorder_remove_and_reload_preserve_library(self) -> None:
        sound = self._playlist()
        sound.add_to_playlist(["synthetic-0", "synthetic-1", "synthetic-0"])
        self.assertEqual(sound.ordered_ids(), ["synthetic-0", "synthetic-1"])
        sound.reorder_playlist(["synthetic-1", "synthetic-0"])
        self.assertEqual(load_project_state(self.root).playlist, ["synthetic-1", "synthetic-0"])
        sound.remove_from_playlist("synthetic-1")
        reopened = ProjectSound(self.root, self.library)
        self.assertEqual(reopened.ordered_ids(), ["synthetic-0"])
        self.assertIn("synthetic-1", self.library.records)
        self._assert_sources_unchanged()

    def test_stock_and_imported_sources_keep_canonical_library_ids(self) -> None:
        bundled = Path(__file__).resolve().parents[1] / "resources" / "stock"
        manifest = json.loads((bundled / "stock_manifest.json").read_text(encoding="utf-8"))
        entry = manifest["music"][0]
        fixture_root = self.root / "resources" / "stock"
        fixture_source = fixture_root / entry["filename"]
        fixture_source.parent.mkdir(parents=True)
        shutil.copyfile(bundled / entry["filename"], fixture_source)
        (fixture_root / "stock_manifest.json").write_text(json.dumps({"music": [entry]}), encoding="utf-8")
        self.library.replace_records(load_library(self.root))
        stock = next(record for record in self.library.records.values() if record.source_kind == "stock")
        source = self.library.path_for(stock.track_id)
        self.assertIsNotNone(source)
        before = source.stat()
        sound = self._playlist()
        sound.add_to_playlist([stock.track_id])
        sound.reorder_playlist([stock.track_id, "synthetic-1", "synthetic-0"])
        reopened = ProjectSound(self.root, self.library)
        self.assertEqual(reopened.ordered_ids(), [stock.track_id, "synthetic-1", "synthetic-0"])
        sound.remove_from_playlist(stock.track_id)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertIn(stock.track_id, self.library.records)

    def test_missing_source_keeps_assignment_through_reorder_reload_and_relink(self) -> None:
        sound = self._playlist()
        path = self.library.path_for("synthetic-1")
        self.assertIsNotNone(path)
        content = path.read_bytes()
        path.unlink()
        self.assertTrue(sound.reconcile_missing_files())
        self.assertEqual(sound.missing_track_ids(), ["synthetic-1"])
        self.assertEqual(sound.ordered_ids(), ["synthetic-0", "synthetic-1"])
        sound.reorder_playlist(["synthetic-1", "synthetic-0"])
        reopened = ProjectSound(self.root, self.library)
        self.assertEqual(reopened.ordered_ids(), ["synthetic-1", "synthetic-0"])
        path.write_bytes(content)
        self.assertTrue(reopened.reconcile_missing_files())
        self.assertEqual(reopened.missing_track_ids(), [])
        self.assertEqual(reopened.ordered_ids(), ["synthetic-1", "synthetic-0"])

    def test_missing_library_record_is_not_removed_when_playlist_is_saved(self) -> None:
        sound = self._playlist()
        records = dict(self.library.records)
        del records["synthetic-1"]
        self.library.replace_records(records)
        sound.reconcile_missing_files()
        sound.save()
        self.assertEqual(load_project_state(self.root).playlist, ["synthetic-0", "synthetic-1"])
        sound.reorder_playlist(["synthetic-1", "synthetic-0"])
        self.assertEqual(load_project_state(self.root).playlist, ["synthetic-1", "synthetic-0"])
        self._assert_sources_unchanged()

    def test_clear_and_silent_reload_preserve_permanent_library(self) -> None:
        sound = self._playlist()
        records = set(self.library.records)
        sound.clear()
        self.assertEqual(sound.ordered_ids(), [])
        self.assertEqual(ProjectSound(self.root, self.library).ordered_ids(), [])
        self.assertEqual(set(self.library.records), records)
        self._assert_sources_unchanged()

    def test_silent_tab_does_not_autoplay_or_assign_music(self) -> None:
        tab = self._tab(playlist=False)
        tab.activate_for_tab_change()
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self.assertFalse(tab.player.is_playing())
        self.assertFalse(tab.play_btn.isEnabled())

    def test_conversion_failure_preserves_assignment_library_and_source(self) -> None:
        self._playlist()
        state = project_sound_path(self.root).read_bytes()
        catalog = library_path(self.root).read_bytes()
        files = set(processed_dir(self.root).iterdir())
        worker = _ImportWorker(self.root, [str(next(iter(self.original_bytes)))], {})
        failures = QtTest.QSignalSpy(worker.failed)
        completed = QtTest.QSignalSpy(worker.finished)
        with (
            mock.patch("sound_tab.toolchain_available", return_value=True),
            mock.patch("sound_tab.convert_to_mp3", side_effect=AudioToolError("Synthetic conversion failure")),
        ):
            worker.run()
        self.assertEqual(failures.count(), 1)
        self.assertEqual(completed.count(), 0)
        self.assertEqual(project_sound_path(self.root).read_bytes(), state)
        self.assertEqual(library_path(self.root).read_bytes(), catalog)
        self.assertEqual(set(processed_dir(self.root).iterdir()), files)
        self.assertEqual(list(originals_dir(self.root).iterdir()), [])
        self._assert_sources_unchanged()

    def test_cancelled_import_does_not_commit_or_assign(self) -> None:
        self._playlist()
        state = project_sound_path(self.root).read_bytes()
        catalog = library_path(self.root).read_bytes()
        worker = _ImportWorker(self.root, [str(next(iter(self.original_bytes)))], {})
        failures = QtTest.QSignalSpy(worker.failed)
        completed = QtTest.QSignalSpy(worker.finished)
        worker.cancel()
        worker.run()
        self.assertEqual(failures.count(), 1)
        self.assertIn("canceled", failures.at(0)[0].lower())
        self.assertEqual(completed.count(), 0)
        self.assertEqual(project_sound_path(self.root).read_bytes(), state)
        self.assertEqual(library_path(self.root).read_bytes(), catalog)
        self._assert_sources_unchanged()

    def test_malformed_mp3_is_not_committed_to_library(self) -> None:
        self._playlist()
        source = self.root / "invalid.mp3"
        source.write_bytes(b"Synthetic invalid audio input")
        state = project_sound_path(self.root).read_bytes()
        catalog = library_path(self.root).read_bytes()
        files = set(processed_dir(self.root).iterdir())
        worker = _ImportWorker(self.root, [str(source)], {})
        failures = QtTest.QSignalSpy(worker.failed)
        completed = QtTest.QSignalSpy(worker.finished)
        worker.run()
        self.assertEqual(failures.count(), 1)
        self.assertEqual(completed.count(), 0)
        self.assertEqual(project_sound_path(self.root).read_bytes(), state)
        self.assertEqual(library_path(self.root).read_bytes(), catalog)
        self.assertEqual(set(processed_dir(self.root).iterdir()), files)
        self.assertEqual(source.read_bytes(), b"Synthetic invalid audio input")
        self._assert_sources_unchanged()

    def test_valid_mp3_import_without_conversion_tools_validates_before_registration(self) -> None:
        sound = self._playlist()
        bundled = Path(__file__).resolve().parents[1] / "resources" / "stock"
        manifest = json.loads((bundled / "stock_manifest.json").read_text(encoding="utf-8"))
        source = bundled / manifest["music"][0]["filename"]
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        state = project_sound_path(self.root).read_bytes()
        worker = _ImportWorker(self.root, [str(source)], {})
        failures = QtTest.QSignalSpy(worker.failed)
        completed = QtTest.QSignalSpy(worker.finished)
        validation_paths = mock.Mock(wraps=application_paths())
        validation_paths.tool_path.return_value = self.root / "unavailable-ffprobe"
        with mock.patch("sound_tab.application_paths", side_effect=lambda *args: application_paths(*args) if args else validation_paths):
            worker.run()
        self.assertEqual(failures.count(), 0, [failures.at(index) for index in range(failures.count())])
        self.assertEqual(completed.count(), 1)
        payloads = completed.at(0)[0]
        self.assertGreater(payloads[0]["record"]["duration_seconds"], 0)
        imported = self.library.register_imports(payloads)
        self.assertEqual(len(imported), 1)
        self.assertIsNotNone(self.library.path_for(imported[0]))
        self.assertEqual(project_sound_path(self.root).read_bytes(), state)
        self.assertEqual(sound.ordered_ids(), ["synthetic-0", "synthetic-1"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)

    def test_import_completion_after_project_replacement_does_not_assign(self) -> None:
        tab = self._tab()
        session = get_project_session(self.root)
        tab._import_context = tab._project_context()
        generation = tab._background_generation
        session.replace(create_project(["letter", "letter"]), asset_root=self.root)
        tab.project_sound.clear()
        tab._accept_import_finished(generation, [{"existing_track_id": "synthetic-1"}])
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self.assertIn("synthetic-1", tab.library.records)
        self.assertFalse(tab.player.is_playing())
        self._assert_sources_unchanged()

    def test_popup_choice_after_project_replacement_does_not_assign_or_resume(self) -> None:
        tab = self._tab()
        self._show_tab(tab)
        session = get_project_session(self.root)
        dialog = StockMusicDialog(
            tab.library, multi_select=False, parent=tab,
            recent_track_ids=("synthetic-0", "synthetic-1"), allow_browse=True,
        )

        def replace_project() -> None:
            dialog.tracksChosen.emit(["synthetic-1"])
            session.replace(create_project(["letter", "letter"]), asset_root=self.root)
            tab.project_sound.clear()
            dialog.accept()

        QtCore.QTimer.singleShot(0, replace_project)
        tab._exec_music_popup(dialog)
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self.assertFalse(tab.player.is_playing())
        self._assert_sources_unchanged()

    def _show_tab(self, tab: SoundTab) -> None:
        tab.resize(1000, 850)
        tab.show()
        tab.activateWindow()
        tab.activate_for_tab_change()
        self.app.processEvents()

    def test_playlist_selection_highlights_without_changing_preview(self) -> None:
        tab = self._tab()
        self._show_tab(tab)
        before = tab.project_sound.state.to_dict()
        active = tab.player.current_track_id
        with mock.patch.object(tab.player, "select_track", wraps=tab.player.select_track) as select:
            tab.playlist_list.setCurrentRow(1)
            self.app.processEvents()
            self.assertEqual(tab.project_sound.state.to_dict(), before)
            self.assertEqual(tab.player.current_track_id, active)
            select.assert_not_called()
            tab.playlist_preview_btn.click()
            select.assert_called_with("synthetic-1", autoplay=True)
        self.assertEqual(tab.project_sound.state.selected_track_id, "synthetic-1")
        self.assertEqual(tab.project_sound.ordered_ids(), before["playlist"])
        tab.deactivate_for_tab_change()
        self.assertFalse(tab.player.is_playing())

    def test_keyboard_reorders_previews_and_removes_only_playlist_assignment(self) -> None:
        tab = self._tab()
        self._show_tab(tab)
        tab.playlist_list.setCurrentRow(1)
        tab.playlist_list.setFocus()
        self.app.processEvents()
        QtTest.QTest.keyClick(tab.playlist_list, QtCore.Qt.Key_Up, QtCore.Qt.ControlModifier)
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1", "synthetic-0"])
        QtTest.QTest.keyClick(tab.playlist_list, QtCore.Qt.Key_Down, QtCore.Qt.ControlModifier)
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0", "synthetic-1"])
        with mock.patch.object(tab.player, "select_track", wraps=tab.player.select_track) as select:
            QtTest.QTest.keyClick(tab.playlist_list, QtCore.Qt.Key_Return)
            select.assert_called_with("synthetic-1", autoplay=True)
        QtTest.QTest.keyClick(tab.playlist_list, QtCore.Qt.Key_Delete)
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])
        self.assertEqual(tab.playlist_list.count(), 1)
        self.assertIn("synthetic-1", tab.library.records)
        self._assert_sources_unchanged()

    def test_space_activates_focused_buttons_and_only_transports_focused_playlist(self) -> None:
        tab = self._tab()
        self._show_tab(tab)
        tab.player.set_muted(False)
        with mock.patch.object(tab.player, "play") as play:
            tab.mute_btn.setFocus()
            self.app.processEvents()
            QtTest.QTest.keyClick(tab.mute_btn, QtCore.Qt.Key_Space)
            self.assertTrue(tab.player.is_muted())
            play.assert_not_called()

            with mock.patch.object(tab, "_open_music_selector") as selector:
                tab.add_track_btn.setFocus()
                self.app.processEvents()
                QtTest.QTest.keyClick(tab.add_track_btn, QtCore.Qt.Key_Space)
                selector.assert_called_once_with(user_music=True)
                play.assert_not_called()

            tab.playlist_list.setFocus()
            self.app.processEvents()
            QtTest.QTest.keyClick(tab.playlist_list, QtCore.Qt.Key_Space)
            play.assert_called_once_with()

    def test_keyboard_volume_and_project_mute_persist_without_changing_saved_level(self) -> None:
        tab = self._tab()
        self._show_tab(tab)
        tab.volume.setValue(25)
        tab.volume.setFocus()
        self.app.processEvents()
        changes = QtTest.QSignalSpy(tab.volume_changed)
        QtTest.QTest.keyClick(tab.volume, QtCore.Qt.Key_Right)
        self.assertGreater(tab.volume.value(), 25)
        settings = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["music_volume"], tab.volume.value())
        self.assertEqual(settings["starting_volume"], tab.volume.value())
        self.assertEqual(changes.count(), 1)
        tab.player.set_muted(False)
        tab.mute_btn.click()
        self.assertTrue(tab.player.is_muted())
        self.assertEqual(json.loads((self.root / "settings.json").read_text(encoding="utf-8")),
                         {**settings, "music_muted": True})
        self.assertEqual(tab.session.project.to_dict()["sound"]["volume"], settings["music_volume"])
        self.assertTrue(tab.session.project.to_dict()["sound"]["muted"])
        tab.shutdown()
        reopened = SoundTab(self.root)
        self.tabs.append(reopened)
        self.assertEqual(reopened.volume.value(), settings["music_volume"])
        self.assertTrue(reopened.player.is_muted())
        self.assertFalse(reopened.player.is_playing())

    def test_reload_restores_volume_and_silence_without_edit_callbacks(self) -> None:
        tab = self._tab()
        tab.volume.setValue(23)
        settings_path = self.root / "settings.json"
        replacement = create_project(["letter", "letter"])
        replacement.update_project_data("sound", {"volume": 61, "muted": False})
        tab.session.replace(replacement, asset_root=self.root)
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        save_project_state(self.root, ProjectSoundState())
        changes = QtTest.QSignalSpy(tab.volume_changed)
        with mock.patch.object(tab, "_save_volume", wraps=tab._save_volume) as save:
            tab.reload_project_from_disk()
            save.assert_not_called()
        self.assertEqual(tab.volume.value(), 61)
        self.assertEqual(changes.count(), 0)
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self.assertFalse(tab.player.is_playing())
        self.assertFalse(tab.player.is_muted())
        self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), settings)

    def test_audio_settings_preserve_pages_and_running_queue(self) -> None:
        tab = self._tab()
        before = tab.session.project.pages
        with mock.patch.object(tab.player, "set_queue", wraps=tab.player.set_queue) as queue:
            tab.volume.setValue(41)
            tab.player.set_muted(False)
            tab.mute_btn.click()
            queue.assert_not_called()
        self.assertEqual(tab.session.project.pages, before)
        self.assertEqual(tab.session.project.to_dict()["sound"]["volume"], 41)
        self.assertTrue(tab.session.project.to_dict()["sound"]["muted"])

    def test_mute_adopts_queued_canonical_volume_before_event_delivery(self) -> None:
        tab = self._tab()
        tab.volume.setValue(20)
        expected_muted = not tab.player.is_muted()
        worker = threading.Thread(target=lambda: tab.session.project.update_project_data("sound", {"volume": 73}))
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive(), "Canonical audio update deadlocked")
        # The worker's Qt event is still queued when the user changes mute.
        tab.mute_btn.click()
        self.app.processEvents()
        self.assertEqual(tab.session.project.to_dict()["sound"]["volume"], 73)
        self.assertEqual(tab.volume.value(), 73)
        self.assertAlmostEqual(tab.player._volume, 0.73)
        self.assertEqual(tab.player.is_muted(), expected_muted)
        self.assertEqual(tab.session.project.to_dict()["sound"]["muted"], expected_muted)

    def test_late_import_after_sound_edit_then_revert_stays_in_archive(self) -> None:
        tab = self._tab()
        tab._import_context = tab._project_context()
        tab._import_target = tab.session.capture_project_target(fields=("sound",))
        tab._pending_import_mode = "playlist"
        tab.project_sound.remove_from_playlist("synthetic-1")
        tab.project_sound.add_to_playlist(["synthetic-1"])
        before = tab.session.project.to_dict()["sound"]
        with mock.patch.object(tab.project_sound, "add_to_playlist", wraps=tab.project_sound.add_to_playlist) as assign:
            tab._accept_import_finished(tab._background_generation, [{"existing_track_id": "synthetic-0"}])
            assign.assert_not_called()
        self.assertEqual(tab.session.project.to_dict()["sound"], before)
        self.assertIn("synthetic-0", tab.library.records)

    def test_import_to_empty_canonical_draft_uses_project_target(self) -> None:
        tab = self._tab(playlist=False)
        tab.session.replace(create_project([]), asset_root=self.root)
        tab._import_context = tab._project_context()
        tab._import_target = tab.session.capture_project_target(fields=("sound",))
        tab._pending_import_mode = "single"
        tab._accept_import_finished(tab._background_generation, [{"existing_track_id": "synthetic-0"}])
        self.assertEqual(tab.session.project.pages, [])
        self.assertEqual(tab.session.project.to_dict()["sound"]["single_track_id"], "synthetic-0")
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])

    def test_canonical_save_load_and_silent_project_reset_restore_audio(self) -> None:
        from saved_letters import SavedLetterRestorer
        tab = self._tab()
        tab.volume.setValue(34)
        tab.player.set_muted(False)
        tab.mute_btn.click()
        tab.project_sound.reorder_playlist(["synthetic-1", "synthetic-0"])
        saved = tab.session.save(sync_workspace=False)
        tab.session.replace(create_project(["letter", "letter"]), asset_root=self.root)
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self.assertFalse(tab.player.is_playing())
        self.assertFalse(tab.player.is_muted())
        SavedLetterRestorer(self.root).restore_canonical(saved)
        tab.reload_project_from_disk()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1", "synthetic-0"])
        self.assertEqual(tab.volume.value(), 34)
        self.assertTrue(tab.player.is_muted())
        self.assertFalse(tab.player.is_playing())
        self._assert_sources_unchanged()

    def test_unavailable_playlist_row_remains_visible_and_removable(self) -> None:
        tab = self._tab()
        records = dict(tab.library.records)
        del records["synthetic-1"]
        tab.library.replace_records(records)
        tab.project_sound.reconcile_missing_files()
        tab._refresh_ui()
        tab.playlist_list.setCurrentRow(1)
        self.assertEqual(tab.playlist_list.count(), 2)
        self.assertIn("unavailable", tab.playlist_list.item(1).text())
        self.assertFalse(tab.playlist_preview_btn.isEnabled())
        remove = tab.playlist_list.itemWidget(tab.playlist_list.item(1)).findChild(
            QtWidgets.QToolButton, "playlistRemove",
        )
        self.assertTrue(remove.isEnabled())
        self.assertIn("1 unavailable", tab.playlist_summary.text())
        remove.click()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])
        self._assert_sources_unchanged()

    def test_music_workflow_visibility_preserves_song_and_returns_to_empty(self) -> None:
        tab = self._tab(playlist=False)
        self._show_tab(tab)
        self.assertTrue(tab.archive_btn.isVisible())
        self.assertTrue(tab.import_btn.isVisible())
        for button in (tab.single_action_btn, tab.create_playlist_btn,
                       tab.add_track_btn, tab.convert_single_btn, tab.clear_btn):
            self.assertFalse(button.isVisible())

        tab._archive_tracks_chosen(["synthetic-1"])
        self.assertTrue(tab.single_action_btn.isVisible())
        self.assertEqual(tab.single_action_btn.text(), "Replace Music")
        self.assertTrue(tab.create_playlist_btn.isVisible())
        self.assertFalse(tab.convert_single_btn.isVisible())
        self.assertFalse(tab.import_btn.isVisible())
        with mock.patch.object(tab, "_open_user_music_for_current_mode") as selector:
            tab.create_playlist_btn.click()
            selector.assert_called_once_with()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1"])
        self.assertTrue(tab.add_track_btn.isVisible())
        self.assertTrue(tab.convert_single_btn.isVisible())
        self.assertFalse(tab.single_action_btn.isVisible())
        tab._archive_tracks_chosen(["synthetic-0"])
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1", "synthetic-0"])

        for expected in (["synthetic-0"], []):
            row = tab.playlist_list.itemWidget(tab.playlist_list.item(0))
            row.findChild(QtWidgets.QToolButton, "playlistRemove").click()
            self.assertEqual(tab.project_sound.ordered_ids(), expected)
            self.assertEqual(load_project_state(self.root).ordered_track_ids(), expected)
        self.assertTrue(tab.import_btn.isVisible())
        self.assertFalse(tab.convert_single_btn.isVisible())
        self.assertFalse(tab.add_track_btn.isVisible())
        tab._archive_tracks_chosen(["synthetic-0"])
        self.assertEqual(tab.project_sound.state.mode, "single")
        self._assert_sources_unchanged()

    def test_conversion_requires_choice_and_cancel_preserves_playlist(self) -> None:
        tab = self._tab()
        before = tab.project_sound.state.to_dict()
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec",
                        return_value=QtWidgets.QDialog.Rejected):
            tab._convert_to_single()
        self.assertEqual(tab.project_sound.state.to_dict(), before)

        # Missing sources must not cause a different song to be retained.
        tab.library.records.pop("synthetic-1")
        def choose(dialog):
            combo = dialog.findChild(QtWidgets.QComboBox)
            self.assertEqual(combo.count(), 2)
            combo.setCurrentIndex(1)
            return QtWidgets.QDialog.Accepted
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec", choose):
            tab._convert_to_single()
        self.assertEqual(tab.project_sound.state.mode, "single")
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1"])
        self.assertEqual(load_project_state(self.root).single_track_id, "synthetic-1")
        with mock.patch.object(tab, "_open_user_music_for_current_mode"):
            tab._create_playlist()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1"])
        self._assert_sources_unchanged()

    def test_conversion_distinguishes_songs_with_the_same_title(self) -> None:
        tab = self._tab()
        tab.library.records["synthetic-1"].display_title = "Synthetic track 1"
        def choose(dialog):
            combo = dialog.findChild(QtWidgets.QComboBox)
            self.assertEqual(combo.itemText(0), "1. Synthetic track 1")
            self.assertEqual(combo.itemText(1), "2. Synthetic track 1")
            combo.setCurrentIndex(1)
            return QtWidgets.QDialog.Accepted
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec", choose):
            tab._convert_to_single()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-1"])
        self.assertEqual(load_project_state(self.root).single_track_id, "synthetic-1")
        self._assert_sources_unchanged()

    def test_conversion_and_clear_ignore_a_changed_soundtrack(self) -> None:
        tab = self._tab()
        def change_sound(dialog):
            tab.project_sound.set_single("synthetic-0")
            return QtWidgets.QDialog.Accepted
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec", change_sound):
            tab._convert_to_single()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])

        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec",
                        return_value=QtWidgets.QDialog.Rejected):
            tab.clear_btn.click()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])
        tab.project_sound.set_single("synthetic-1")
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec", change_sound):
            tab.clear_btn.click()
        self.assertEqual(tab.project_sound.ordered_ids(), ["synthetic-0"])
        with mock.patch("sound_tab.LetterSmithConfirmationDialog.exec",
                        return_value=QtWidgets.QDialog.Accepted):
            tab.clear_btn.click()
        self.assertEqual(tab.project_sound.ordered_ids(), [])
        self._assert_sources_unchanged()


if __name__ == "__main__":
    unittest.main()
