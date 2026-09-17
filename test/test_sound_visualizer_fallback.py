from __future__ import annotations

import base64
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat, QMediaPlayer

from sound_preview import SoundPreviewWidget, _LiveBarVisualizer
from sound_tab import CROSSFADE_MS, PlaylistPlayer, SoundTab
from sound_visualizer import AudioVisualizerUltra


class _Signal:
    def __init__(self) -> None:
        self._slots: list[Callable[..., object]] = []

    def connect(self, slot: Callable[..., object]) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: Callable[..., object]) -> None:
        self._slots = [connected for connected in self._slots if connected != slot]

    def emit(self, *args: object) -> None:
        for slot in tuple(self._slots):
            slot(*args)

    def slot_keys(self) -> list[tuple[object, object]]:
        return [
            (
                getattr(slot, "__self__", None),
                getattr(slot, "__func__", slot),
            )
            for slot in self._slots
        ]


class _AudioOutput:
    def __init__(self) -> None:
        self.volumeChanged = _Signal()
        self.mutedChanged = _Signal()
        self._volume = 1.0
        self._muted = False

    def volume(self) -> float:
        return self._volume

    def isMuted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.mutedChanged.emit(self._muted)


class _Player:
    def __init__(self) -> None:
        self.playbackStateChanged = _Signal()
        self.positionChanged = _Signal()
        self._state = QMediaPlayer.StoppedState
        self._position = 0
        self._audio_output = _AudioOutput()

    def playbackState(self) -> QMediaPlayer.PlaybackState:
        return self._state

    def position(self) -> int:
        return self._position

    def audioOutput(self) -> _AudioOutput:
        return self._audio_output

    def set_state(self, state: QMediaPlayer.PlaybackState) -> None:
        self._state = state
        self.playbackStateChanged.emit(state)

    def set_position(self, position: int) -> None:
        self._position = int(position)
        self.positionChanged.emit(self._position)


class _OrderingPlayer:
    def __init__(self, name: str, events: list[tuple[str, str]]) -> None:
        self.name = name
        self.events = events

    def stop(self) -> None:
        self.events.append(("stop", self.name))

    def setPosition(self, _position: int) -> None:
        return


class _OrderingOutput:
    def setVolume(self, _volume: float) -> None:
        return


def _packed(values: bytes) -> str:
    return base64.b64encode(zlib.compress(values)).decode("ascii")


def _analysis_payload(path: Path) -> dict:
    frames = 4
    bands = 2
    envelope = bytes((32, 96, 160, 224))
    return {
        "version": 1,
        "codec": "b64z_u8",
        "src": {
            "path": str(path),
            "mtime": 1.0,
            "size": 100,
        },
        "hop_ms": 20,
        "frames": frames,
        "nbands": bands,
        "q": {
            "lvl": _packed(envelope),
            "bass": _packed(envelope),
            "mid": _packed(envelope),
            "high": _packed(envelope),
            "beat": _packed(bytes((0, 255, 0, 0))),
            "spec": _packed(bytes((20, 40, 60, 80, 100, 120, 140, 160))),
        },
        "profile": {
            "bass_ratio": 0.4,
            "bright_mean": 0.5,
        },
    }


class SoundVisualizerFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self) -> None:
        self.player = _Player()
        self.preview = SoundPreviewWidget(self.player)
        self.track_a = Path("C:/music/song-a.mp3")
        self.track_b = Path("C:/music/song-b.mp3")
        self.preview.set_audio_file(str(self.track_a))
        self.preview.set_tab_active(True)

    def tearDown(self) -> None:
        self.preview.shutdown()
        self.preview.close()
        self.app.processEvents()

    def _start_playback(self) -> None:
        self.player.set_state(QMediaPlayer.PlayingState)
        self.app.processEvents()

    def test_live_fallback_runs_without_analysis(self) -> None:
        self._start_playback()

        self.assertEqual(self.preview.renderer_mode(), "live")
        self.assertTrue(self.preview.is_animation_running())

    def test_valid_matching_analysis_selects_analyzed_renderer(self) -> None:
        self._start_playback()
        self.preview.set_analysis_payload(_analysis_payload(self.track_a))
        self.app.processEvents()

        self.assertEqual(self.preview.renderer_mode(), "analyzed")
        self.assertTrue(self.preview.visualizer().has_analysis())

    def test_malformed_analysis_uses_live_fallback(self) -> None:
        self._start_playback()
        payload = _analysis_payload(self.track_a)
        payload["q"]["spec"] = "not-valid-base64"

        self.preview.set_analysis_payload(payload)
        self.app.processEvents()

        self.assertEqual(self.preview.renderer_mode(), "live")
        self.assertTrue(self.preview.is_animation_running())

    def test_track_change_clears_analysis_and_rejects_stale_payload(self) -> None:
        self._start_playback()
        old_payload = _analysis_payload(self.track_a)
        self.preview.set_analysis_payload(old_payload)
        self.assertEqual(self.preview.renderer_mode(), "analyzed")

        self.preview.set_audio_file(str(self.track_b))
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "live")
        self.assertFalse(self.preview.visualizer().has_analysis())

        self.preview.set_analysis_payload(old_payload)
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "live")
        self.assertFalse(self.preview.visualizer().has_analysis())

    def test_rebinding_disconnects_old_player_and_does_not_duplicate_slots(self) -> None:
        replacement = _Player()

        self.preview.set_media_player(replacement)
        self.preview.set_media_player(replacement)
        self.app.processEvents()

        self.assertEqual(self.player.playbackStateChanged.slot_keys(), [])
        self.assertEqual(self.player.positionChanged.slot_keys(), [])
        for signal in (
            replacement.playbackStateChanged,
            replacement.positionChanged,
            replacement.audioOutput().volumeChanged,
            replacement.audioOutput().mutedChanged,
        ):
            keys = signal.slot_keys()
            self.assertEqual(len(keys), len(set(keys)))

        self.player.set_state(QMediaPlayer.PlayingState)
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "inactive")

    def test_pause_stop_and_inactive_tab_collapse_and_stop_animation(self) -> None:
        self._start_playback()
        self.assertEqual(self.preview.renderer_mode(), "live")

        self.player.set_state(QMediaPlayer.PausedState)
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "inactive")
        self.assertFalse(self.preview.is_animation_running())

        self.player.set_state(QMediaPlayer.PlayingState)
        self.preview.set_tab_active(False)
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "inactive")
        self.assertFalse(self.preview.is_animation_running())
        self.assertFalse(
            any(timer.isActive() for timer in self.preview.findChildren(QtCore.QTimer))
        )

        self.preview.set_tab_active(True)
        self.player.set_state(QMediaPlayer.StoppedState)
        self.app.processEvents()
        self.assertEqual(self.preview.renderer_mode(), "inactive")
        self.assertFalse(self.preview.is_animation_running())

    def test_muting_does_not_hide_source_visualization(self) -> None:
        self._start_playback()

        self.player.audioOutput().set_muted(True)
        self.app.processEvents()

        self.assertEqual(self.preview.renderer_mode(), "live")
        self.assertTrue(self.preview.is_animation_running())


class AnalyzedVisualizerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_has_analysis_reports_only_valid_payloads(self) -> None:
        visualizer = AudioVisualizerUltra()
        try:
            self.assertFalse(visualizer.has_analysis())

            visualizer.set_analysis_payload({"frames": 2, "hop_ms": 20, "q": {}})
            self.assertFalse(visualizer.has_analysis())

            visualizer.set_analysis_payload(_analysis_payload(Path("C:/music/song.mp3")))
            self.assertTrue(visualizer.has_analysis())
        finally:
            visualizer.shutdown()
            visualizer.close()
            self.app.processEvents()

    def test_compact_preview_has_no_inset_and_retains_last_analyzed_bar(self) -> None:
        preview = SoundPreviewWidget(None)
        try:
            preview.resize(180, 230)
            preview.show()
            self.app.processEvents()

            self.assertEqual(preview._blank.geometry(), preview.rect())
            self.assertEqual(preview._blank._bg_color.name(), "#0f1116")
            self.assertEqual(preview._live_visualizer._bg_color.name(), "#0f1116")
            visualizer = preview.visualizer()
            self.assertEqual(visualizer._bg.name(), "#0f1116")

            blank = QtGui.QImage(180, 230, QtGui.QImage.Format_ARGB32)
            preview._blank.render(blank)
            self.assertEqual(blank.pixelColor(0, 0).name(), "#0f1116")

            visualizer.set_analysis_payload(_analysis_payload(Path("C:/music/song.mp3")))
            visualizer.resize(180, 230)
            visualizer._bars = [0.0] * visualizer.BARS
            visualizer._peaks = [0.0] * visualizer.BARS
            before = QtGui.QImage(180, 230, QtGui.QImage.Format_ARGB32)
            visualizer.render(before)
            visualizer._bars[-1] = 0.8
            after = QtGui.QImage(180, 230, QtGui.QImage.Format_ARGB32)
            visualizer.render(after)

            changed_columns = [
                x for x in range(180)
                if any(before.pixel(x, y) != after.pixel(x, y) for y in range(70, 190))
            ]
            self.assertEqual(len(visualizer._bars), 48)
            self.assertTrue(changed_columns)
            self.assertGreaterEqual(min(changed_columns), 160)
            self.assertGreaterEqual(max(changed_columns), 160)
            self.assertLess(max(changed_columns), 180)
        finally:
            preview.shutdown()
            preview.close()
            self.app.processEvents()


class LivePcmVisualizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_int16_pcm_buffer_produces_nonzero_live_targets_without_analysis(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(44_100)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        samples = [
            value
            for _ in range(32)
            for value in (0, 8_192, 16_384, -8_192, -16_384, 0)
        ]
        raw_pcm = struct.pack(f"={len(samples)}h", *samples)
        buffer = QAudioBuffer(raw_pcm, audio_format)
        visualizer = _LiveBarVisualizer()
        try:
            self.assertTrue(buffer.isValid())
            self.assertFalse(any(visualizer._targets))

            visualizer.set_active(True)
            visualizer._on_audio_buffer(buffer)

            self.assertTrue(any(target > 0.0 for target in visualizer._targets))
        finally:
            visualizer.shutdown()
            visualizer.close()
            self.app.processEvents()


class PlaylistCrossfadeOrderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_crossfade_rebinds_active_player_before_stopping_outgoing_player(self) -> None:
        player = PlaylistPlayer(lambda _track_id: None)
        events: list[tuple[str, str]] = []
        outgoing = _OrderingPlayer("outgoing", events)
        incoming = _OrderingPlayer("incoming", events)
        player.players = [outgoing, incoming]
        player.outputs = [_OrderingOutput(), _OrderingOutput()]
        player.queue = ["track-a", "track-b"]
        player.active_slot = 0
        player.current_index = 0
        player._transitioning = True
        player._crossfade_from = 0
        player._crossfade_to = 1
        player._crossfade_elapsed = CROSSFADE_MS - player._crossfade_timer.interval()
        player.activePlayerChanged.connect(
            lambda active: events.append(("active", active.name))
        )
        try:
            player._crossfade_step()

            self.assertIn(("active", "incoming"), events)
            self.assertIn(("stop", "outgoing"), events)
            self.assertLess(
                events.index(("active", "incoming")),
                events.index(("stop", "outgoing")),
            )
        finally:
            player._crossfade_timer.stop()
            player.deleteLater()
            self.app.processEvents()


class SoundTabLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_repeated_tab_activation_is_idempotent_and_shuts_down_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = SoundTab(Path(directory))
            try:
                for _ in range(20):
                    tab.activate_for_tab_change()
                    tab.activate_for_tab_change()
                    self.app.processEvents()
                    tab.deactivate_for_tab_change()
                    tab.deactivate_for_tab_change()
                    self.app.processEvents()

                self.assertFalse(tab._tab_active)
                self.assertFalse(tab.shared_preview_widget().is_animation_running())
            finally:
                tab.shutdown()
                tab.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
