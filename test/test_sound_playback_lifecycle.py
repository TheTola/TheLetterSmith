from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if __name__ == "__main__" and sys.argv[1:2] == ["--native-transport"]:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    from project_paths import ApplicationPaths, configure_application_paths
    configure_application_paths(ApplicationPaths.for_runtime(repository))

from PySide6 import QtCore, QtTest, QtWidgets
from PySide6.QtMultimedia import QMediaPlayer

from sound_model import (
    ProjectSoundState, TrackRecord, current_manifest_path, current_music_path,
    load_project_state, processed_dir, project_sound_path, save_library, save_project_state,
)
from sound_tab import PlaylistPlayer, ProjectSound, SoundLibrary


class SoundPlaybackLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def wait_for(self, predicate, message: str) -> None:
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 4000:
            QtTest.QTest.qWait(10)
        self.assertTrue(predicate(), message)

    @contextmanager
    def player_fixture(self):
        with tempfile.TemporaryDirectory(prefix="ls-sound-") as directory:
            paths = {}
            for name in ("first", "second"):
                path = Path(directory) / f"{name}.wav"
                with wave.open(str(path), "wb") as audio:
                    audio.setnchannels(2)
                    audio.setsampwidth(2)
                    audio.setframerate(44100)
                    audio.writeframes(b"\0\0\0\0" * 88200)
                paths[name] = path
            player = PlaylistPlayer(paths.get)
            player.set_muted(True)
            try:
                player.set_queue(["first", "second"])
                self.wait_for(lambda: player.player.mediaStatus() == QMediaPlayer.LoadedMedia,
                              "Synthetic audio did not load")
                yield player
            finally:
                player.shutdown()
                player.deleteLater()
                self.app.processEvents()

    def test_transport_follows_actual_backend_play_pause_stop(self) -> None:
        result = self._native_transport_result("controls")
        self.assertEqual(result["transport"][-4:], ["playing", "paused", "stopped", "unavailable"])
        self.assertEqual(result["state"], "stopped")
        self.assertTrue(result["shutdown_quiet"])

    def test_play_request_does_not_report_playing_before_backend(self) -> None:
        with self.player_fixture() as player:
            observed = []
            player.playbackChanged.connect(observed.append)
            with mock.patch.object(player.player, "play"):
                player.play()
            self.assertFalse(player.is_playing())
            self.assertNotIn(True, observed)
            self.assertEqual(player.transport_state, "stopped")

    def test_stop_rejects_delayed_end_error_loop_and_crossfade_callbacks(self) -> None:
        with self.player_fixture() as player:
            errors = []
            player.error.connect(errors.append)
            player.play()
            self.wait_for(player.is_playing, "Playback did not start")
            callbacks = [callback for _signal, callback in player._player_connections[0]]
            player.stop()
            callbacks[2](QMediaPlayer.EndOfMedia)
            callbacks[4](QMediaPlayer.ResourceError, "Old track failed")
            player._on_status(0, QMediaPlayer.EndOfMedia)
            player._on_error(0, "Old track failed")
            player._advance_after_loop_delay()
            player._crossfade_step()
            self.assertFalse(player._loop_timer.isActive())
            self.assertFalse(player._crossfade_timer.isActive())
            self.assertFalse(player.is_playing())
            self.assertEqual(errors, [])
            self.assertEqual(player.current_track_id, "first")

    def test_replaced_track_rejects_previous_source_callbacks(self) -> None:
        with self.player_fixture() as player:
            errors = []
            player.error.connect(errors.append)
            previous = [callback for _signal, callback in player._player_connections[0]]
            player.select_track("second", autoplay=True)
            self.wait_for(player.is_playing, "Second track did not start")
            previous[2](QMediaPlayer.EndOfMedia)
            previous[4](QMediaPlayer.ResourceError, "Previous source failed")
            self.assertFalse(player._loop_timer.isActive())
            self.assertEqual(errors, [])
            self.assertEqual(player.current_track_id, "second")

    def test_missing_selection_is_unavailable_without_substituting_next_track(self) -> None:
        with self.player_fixture() as player:
            errors = []
            player.error.connect(errors.append)
            player.set_queue(["missing", "second"], "missing")
            player.play()
            self.assertEqual(player.current_track_id, "missing")
            self.assertEqual(player.transport_state, "unavailable")
            self.assertFalse(player.is_playing())
            self.assertEqual(len(errors), 1)

    def test_repeat_delay_remains_active_only_until_explicit_stop(self) -> None:
        with self.player_fixture() as player:
            player.play()
            self.wait_for(player.is_playing, "Playback did not start")
            player._on_status(0, QMediaPlayer.EndOfMedia)
            self.assertTrue(player._loop_timer.isActive())
            player.stop()
            self.assertFalse(player._loop_timer.isActive())
            player._advance_after_loop_delay()
            self.assertEqual(player.current_track_id, "first")

    def test_current_error_stops_playback_and_preserves_error_state(self) -> None:
        with self.player_fixture() as player:
            errors = []
            player.error.connect(errors.append)
            player.play()
            self.wait_for(player.is_playing, "Playback did not start")
            player._on_error(player.active_slot, "Synthetic playback error")
            self.assertFalse(player.is_playing())
            self.assertEqual(player.transport_state, "error")
            self.assertEqual(errors, ["Synthetic playback error"])
            player._on_status(player.active_slot, QMediaPlayer.EndOfMedia)
            self.assertFalse(player._loop_timer.isActive())

    def test_shutdown_rejects_further_play_requests(self) -> None:
        with self.player_fixture() as player:
            player.shutdown()
            player.play()
            self.assertFalse(player.is_playing())
            self.assertTrue(player.player.source().isEmpty())
            self.assertEqual(player.transport_state, "unavailable")

    def _native_transport_result(self, scenario: str) -> dict:
        # QTest.qWait hangs at native EndOfMedia on the local Windows backend,
        # including a single track. Exercise the real application event loop in
        # an isolated process with both an internal watchdog and a parent bound.
        with tempfile.TemporaryDirectory(prefix="ls-native-") as directory:
            environment = dict(os.environ, LETTER_SMITH_PROFILE_ROOT=directory,
                               QT_QPA_PLATFORM="offscreen")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--native-transport", scenario],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, timeout=16,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_native_crossfade_repeats_with_cached_source_duration(self) -> None:
        result = self._native_transport_result("crossfade")
        self.assertEqual(result["tracks"], ["first", "second", "first", "second"])
        self.assertEqual(result["slots"], [0, 1, 0, 1])
        self.assertEqual(result["durations"], [2000, 2000])
        self.assertEqual(result["state"], "playing")
        self.assertTrue(result["shutdown_quiet"])

    def test_native_single_track_repeats_after_end_delay(self) -> None:
        result = self._native_transport_result("single")
        self.assertEqual(result["tracks"], ["first", "first"])
        self.assertGreaterEqual(result["end_count"], 1)
        self.assertEqual(result["durations"][0], 2000)
        self.assertEqual(result["state"], "playing")
        self.assertTrue(result["shutdown_quiet"])


class MissingSoundAssignmentTests(unittest.TestCase):
    def test_missing_assignment_survives_reload_reorder_and_remove(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ls-missing-") as directory:
            root = Path(directory)
            source = processed_dir(root) / "present.mp3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"synthetic model-only media")
            record = TrackRecord("present", "hash", "Present track", "present.mp3", "", "present.mp3")
            save_library(root, {record.track_id: record})
            save_project_state(root, ProjectSoundState(
                mode="playlist", playlist=["missing", "present"], selected_track_id="missing",
            ))
            library = SoundLibrary(root)
            project = ProjectSound(root, library)
            self.assertEqual(project.ordered_ids(), ["missing", "present"])
            self.assertEqual(project.missing_track_ids(), ["missing"])
            self.assertFalse(project.reconcile_missing_files())
            project.reload_from_disk()
            project.reorder_playlist(["present", "missing"])
            self.assertEqual(load_project_state(root).playlist, ["present", "missing"])
            project.remove_from_playlist("missing")
            self.assertEqual(load_project_state(root).playlist, ["present"])
            self.assertTrue(source.is_file())

    def test_external_removal_and_repair_never_change_assignment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ls-repair-") as directory:
            root = Path(directory)
            source = processed_dir(root) / "track.mp3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"synthetic model-only media")
            record = TrackRecord("track", "hash", "Track", "track.mp3", "", "track.mp3")
            save_library(root, {record.track_id: record})
            save_project_state(root, ProjectSoundState(single_track_id="track"))
            project = ProjectSound(root, SoundLibrary(root))
            saved_assignment = project_sound_path(root).read_bytes()
            self.assertEqual(current_music_path(root).read_bytes(), source.read_bytes())
            source.unlink()
            self.assertTrue(project.reconcile_missing_files())
            self.assertEqual(project.missing_track_ids(), ["track"])
            self.assertEqual(load_project_state(root).single_track_id, "track")
            self.assertFalse(current_music_path(root).exists())
            self.assertFalse(current_manifest_path(root).exists())
            self.assertEqual(project_sound_path(root).read_bytes(), saved_assignment)
            source.write_bytes(b"repaired synthetic media")
            self.assertTrue(project.reconcile_missing_files())
            self.assertEqual(project.missing_track_ids(), [])
            self.assertEqual(load_project_state(root).single_track_id, "track")
            self.assertEqual(current_music_path(root).read_bytes(), source.read_bytes())
            self.assertTrue(current_manifest_path(root).exists())
            self.assertEqual(project_sound_path(root).read_bytes(), saved_assignment)

    def test_reload_changed_source_refreshes_compatibility_without_editing_assignment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ls-replaced-") as directory:
            root = Path(directory)
            source = processed_dir(root) / "track.mp3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"original synthetic media")
            record = TrackRecord("track", "hash", "Track", "track.mp3", "", "track.mp3")
            save_library(root, {record.track_id: record})
            save_project_state(root, ProjectSoundState(single_track_id="track"))
            project = ProjectSound(root, SoundLibrary(root))
            saved_assignment = project_sound_path(root).read_bytes()
            source.write_bytes(b"replacement synthetic media with a new length")
            self.assertFalse(project.reload_from_disk())
            self.assertEqual(current_music_path(root).read_bytes(), source.read_bytes())
            self.assertEqual(project_sound_path(root).read_bytes(), saved_assignment)

    def test_stored_playlist_occurrences_are_not_collapsed(self) -> None:
        state = ProjectSoundState(mode="playlist", playlist=["a", "b", "a"])
        self.assertEqual(state.normalize().playlist, ["a", "b", "a"])


def _run_native_transport(scenario: str) -> None:
    import faulthandler
    faulthandler.dump_traceback_later(12, exit=True)
    app = QtWidgets.QApplication([])
    profile = Path(os.environ["LETTER_SMITH_PROFILE_ROOT"])
    paths = {}
    for name in ("first", "second"):
        path = profile / f"{name}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"\0\0\0\0" * 88200)
        paths[name] = path
    player = PlaylistPlayer(paths.get)
    player.set_muted(True)
    result = {"tracks": [], "slots": [], "end_count": 0}
    errors = []

    def changed(track: str) -> None:
        result["tracks"].append(track)
        result["slots"].append(player.active_slot)

    def media_status(status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            result["end_count"] += 1

    player.trackChanged.connect(changed)
    player.error.connect(errors.append)
    for native in player.players:
        native.mediaStatusChanged.connect(media_status)
    expected_count = 2 if scenario == "single" else 4
    monitor = QtCore.QTimer()
    monitor.setInterval(20)
    if scenario == "controls":
        result["transport"] = []
        player.transportStateChanged.connect(result["transport"].append)
        def advance_controls():
            if errors:
                app.quit()
            elif player.transport_state == "playing":
                player.pause()
            elif player.transport_state == "paused":
                player.stop()
            elif "playing" in result["transport"] and player.transport_state == "stopped":
                app.quit()
        monitor.timeout.connect(advance_controls)
    else:
        monitor.timeout.connect(
            lambda: app.quit() if errors or len(result["tracks"]) >= expected_count else None
        )
    deadline = QtCore.QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(app.quit)
    try:
        player.set_queue(["first"] if scenario == "single" else ["first", "second"])
        player.play()
        monitor.start()
        deadline.start(8000)
        app.exec()
        result["durations"] = list(player._durations)
        result["state"] = player.transport_state
        assert not errors, errors
        if scenario == "controls":
            assert result["transport"][-3:] == ["playing", "paused", "stopped"], result
            assert not player.is_playing(), result
        else:
            assert len(result["tracks"]) == expected_count, result
            assert player.is_playing(), result
    finally:
        monitor.stop()
        deadline.stop()
        player.shutdown()
    count = len(result["tracks"])
    drained = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(100, drained.quit)
    drained.exec()
    result["shutdown_quiet"] = not player.is_playing() and len(result["tracks"]) == count
    player.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(player, QtCore.QEvent.DeferredDelete)
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps(result))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--native-transport"]:
        _run_native_transport(sys.argv[2])
    else:
        unittest.main()
