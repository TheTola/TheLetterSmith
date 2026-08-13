# ===============================
# File: sound_preview.py
# ===============================
from __future__ import annotations

import logging
from typing import Optional, Any, Dict
import base64
import zlib
import math
import struct
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

# Try to import QMediaPlayer constants for robust state comparisons.
try:
    from PySide6.QtMultimedia import (  # type: ignore
        QAudioBufferOutput,
        QAudioFormat,
        QMediaPlayer,
    )
except Exception:
    QMediaPlayer = None  # type: ignore
    QAudioBufferOutput = None  # type: ignore[assignment]
    QAudioFormat = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Load the current visualizer implementation. The built-in minimal visualizer
# remains as a startup-safe substitute if the optional visualizer cannot load.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from sound_visualizer import AudioVisualizerUltra as _AudioVisualizerUltra  # type: ignore
except ImportError:
    _AudioVisualizerUltra = None  # type: ignore[assignment]


def _b64z_unpack_u8(s: str) -> bytes:
    """Decode base64(zlib(bytes)) => raw bytes."""
    raw = base64.b64decode(s.encode("ascii"))
    return zlib.decompress(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Blank stage: draws background/grid only (the "vanished" state)
# ─────────────────────────────────────────────────────────────────────────────
class _BlankStage(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._bg_color = QtGui.QColor("#0f1116")
        self._grid_color = QtGui.QColor(255, 255, 255, 12)

    def paintEvent(self, _ev: QtGui.QPaintEvent) -> None:
        r = self.rect()
        if not r.isValid():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(r, self._bg_color)

        p.setPen(QtGui.QPen(self._grid_color, 1))
        step = max(24, int(min(r.width(), r.height()) * 0.06))
        for x in range(r.left() + step, r.right(), step):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top() + step, r.bottom(), step):
            p.drawLine(r.left(), y, r.right(), y)

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Minimal dependency-free visualizer fallback
# ─────────────────────────────────────────────────────────────────────────────
class _LiveBarVisualizer(QtWidgets.QWidget):
    """
    Lightweight live visualizer driven by Qt's decoded PCM buffers.

    - `set_active(True)` starts motion.
    - `set_active(False)` collapses immediately.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._bars = [0.0] * 24
        self._targets = [0.0] * len(self._bars)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self._active = False
        self._player = None
        self._buffer_output = None

        self._bg_color = QtGui.QColor("#0f1116")
        self._bar_color = QtGui.QColor(0, 210, 255, 180)
        self._grid_color = QtGui.QColor(255, 255, 255, 12)

    def set_media_player(self, player) -> None:
        if player is self._player:
            return
        self._detach_buffer_output()
        self._player = player
        if self._active:
            self._attach_buffer_output()

    def set_audio_file(self, _path: str) -> None:
        self._reset_motion()

    def set_analysis_payload(self, _payload: Optional[Dict[str, Any]]) -> None:
        return

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self._attach_buffer_output()
            self._timer.start()
        else:
            self._timer.stop()
            self._detach_buffer_output()
            self._reset_motion()
            self.update()

    def _tick(self) -> None:
        if not self._active:
            return
        for index, target in enumerate(self._targets):
            current = self._bars[index]
            speed = 0.48 if target > current else 0.16
            current += (target - current) * speed
            self._bars[index] = 0.0 if current < 0.008 else current
            self._targets[index] *= 0.91
        self.update()

    def _attach_buffer_output(self) -> None:
        if (
            self._buffer_output is not None
            or self._player is None
            or QAudioBufferOutput is None
            or not hasattr(self._player, "setAudioBufferOutput")
        ):
            return
        try:
            output = QAudioBufferOutput(self)
            output.audioBufferReceived.connect(self._on_audio_buffer)
            self._player.setAudioBufferOutput(output)
            self._buffer_output = output
        except Exception:
            self._buffer_output = None

    def _detach_buffer_output(self) -> None:
        output = self._buffer_output
        if output is None:
            return
        try:
            output.audioBufferReceived.disconnect(self._on_audio_buffer)
        except Exception:
            pass
        try:
            if self._player is not None and hasattr(self._player, "setAudioBufferOutput"):
                current = getattr(self._player, "audioBufferOutput", lambda: None)()
                if current is output:
                    self._player.setAudioBufferOutput(None)
        except Exception:
            pass
        output.deleteLater()
        self._buffer_output = None

    @staticmethod
    def _samples_from_buffer(buffer) -> list[float]:
        try:
            if buffer is None or not buffer.isValid() or buffer.byteCount() <= 0:
                return []
            raw = bytes(buffer.constData())
            audio_format = buffer.format()
            sample_format = audio_format.sampleFormat()
        except Exception:
            return []

        if QAudioFormat is None:
            return []
        try:
            if sample_format == QAudioFormat.UInt8:
                values = [(value - 128.0) / 128.0 for value in raw]
            elif sample_format == QAudioFormat.Int16:
                usable = len(raw) - (len(raw) % 2)
                values = [value[0] / 32768.0 for value in struct.iter_unpack("=h", raw[:usable])]
            elif sample_format == QAudioFormat.Int32:
                usable = len(raw) - (len(raw) % 4)
                values = [value[0] / 2147483648.0 for value in struct.iter_unpack("=i", raw[:usable])]
            elif sample_format == QAudioFormat.Float:
                usable = len(raw) - (len(raw) % 4)
                values = [float(value[0]) for value in struct.iter_unpack("=f", raw[:usable])]
            else:
                return []
        except (ValueError, struct.error):
            return []

        if len(values) > 8192:
            step = max(1, len(values) // 8192)
            values = values[::step]
        return values

    @QtCore.Slot(object)
    def _on_audio_buffer(self, buffer) -> None:
        if not self._active:
            return
        samples = self._samples_from_buffer(buffer)
        if not samples:
            return
        count = len(self._targets)
        chunk = max(1, len(samples) // count)
        targets: list[float] = []
        for index in range(count):
            segment = samples[index * chunk:(index + 1) * chunk]
            if not segment:
                targets.append(0.0)
                continue
            rms = math.sqrt(sum(value * value for value in segment) / len(segment))
            peak = max(abs(value) for value in segment)
            targets.append(min(1.0, math.sqrt(max(rms, peak * 0.35)) * 1.15))
        self._targets = targets

    def _reset_motion(self) -> None:
        self._bars = [0.0] * len(self._bars)
        self._targets = [0.0] * len(self._targets)

    def is_animation_running(self) -> bool:
        return self._timer.isActive()

    def shutdown(self) -> None:
        self._timer.stop()
        self._detach_buffer_output()
        self._player = None
        self._active = False
        self._reset_motion()

    def paintEvent(self, _ev: QtGui.QPaintEvent) -> None:
        r = self.rect()
        if not r.isValid():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(r, self._bg_color)

        p.setPen(QtGui.QPen(self._grid_color, 1))
        step = max(24, int(min(r.width(), r.height()) * 0.06))
        for x in range(r.left() + step, r.right(), step):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top() + step, r.bottom(), step):
            p.drawLine(r.left(), y, r.right(), y)

        if not self._active:
            p.end()
            return

        n = len(self._bars)
        gap = max(4, int(r.width() * 0.006))
        total_gap = gap * (n + 1)
        bar_w = max(6, (r.width() - total_gap) // n)

        base_y = int(r.bottom() - max(8, r.height() * 0.10))
        max_h = int(r.height() * 0.64)
        x = r.left() + gap

        p.setPen(Qt.NoPen)
        p.setBrush(self._bar_color)
        for h_ratio in self._bars:
            h = int(max(3, max_h * float(h_ratio)))
            p.drawRoundedRect(QtCore.QRectF(x, base_y - h, bar_w, h), 3, 3)
            x += bar_w + gap

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Gate controller: decides whether the visualizer should be shown or "vanished".
# Uses offline analysis payload for silence detection.
# ─────────────────────────────────────────────────────────────────────────────
class _VisualizerGate(QtCore.QObject):
    visibleChanged = QtCore.Signal(bool)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)

        self._player = None
        self._audio_out = None

        self._audio_path = ""

        # Offline analysis (per-track)
        self._hop_ms: int = 20
        self._frames: int = 0
        self._lvl: Optional[bytes] = None  # u8 levels (0..255)

        self._position_ms = 0
        self._last_pos_ms = 0
        self._stale_pos_ticks = 0

        self._playing = False
        self._volume = 1.0  # 0..1
        self._muted = False

        # Gate thresholds
        self._silence_threshold = 0.01

        self._visible = False

        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._poll_tick)
        self._enabled = False
        self._shutting_down = False

    def is_visible(self) -> bool:
        return self._visible

    def set_enabled(self, enabled: bool) -> None:
        """Suspend gate polling while Sound does not own the preview."""
        if self._shutting_down:
            return
        self._enabled = bool(enabled)
        if not self._enabled or self._player is None:
            self._poll.stop()
            self._stale_pos_ticks = 0
            self._set_visible(False)
            return
        self._poll.start()
        self._poll_tick()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._enabled = False
        try:
            self._poll.stop()
        except Exception:
            pass
        self._disconnect_player()
        self._disconnect_audio_out()
        self._player = None
        self._playing = False
        self._audio_path = ""
        self._stale_pos_ticks = 0
        self._set_visible(False)

    # ───────────────────────── robust playing-state detection ─────────────────
    @staticmethod
    def _state_is_playing(state_obj: object) -> bool:
        # Best: direct enum compare
        if QMediaPlayer is not None:
            try:
                return state_obj == QMediaPlayer.PlayingState  # Qt6 PlaybackState enum
            except Exception:
                pass

        # Fallback: int coercion
        try:
            return int(state_obj) == 1
        except Exception:
            pass

        # Last resort: string contains "playing"
        try:
            s = str(state_obj).lower()
            return "playing" in s
        except Exception:
            return False

    # ── Public wiring ─────────────────────────────────────────────
    def set_media_player(self, player) -> None:
        self._disconnect_player()
        self._disconnect_audio_out()

        self._player = player
        self._audio_out = None

        self._playing = False
        self._position_ms = 0
        self._last_pos_ms = 0
        self._stale_pos_ticks = 0

        if self._player is None:
            self._poll.stop()
            self._set_visible(False)
            return

        self._try_connect("playbackStateChanged", self._on_state_changed)  # Qt6
        self._try_connect("stateChanged", self._on_state_changed)          # Qt5
        self._try_connect("positionChanged", self._on_pos_changed)

        self._wire_audio_out()

        if self._enabled:
            self._poll.start()
            self._poll_tick()
        else:
            self._poll.stop()
            self._set_visible(False)

    def set_audio_file(self, path: str) -> None:
        self._audio_path = path or ""
        self._position_ms = 0
        self._last_pos_ms = 0
        self._stale_pos_ticks = 0
        self._recompute()

    def set_analysis_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        """Provide offline analysis for silence gating."""
        if not payload:
            self._lvl = None
            self._frames = 0
            self._hop_ms = 20
            self._recompute()
            return

        try:
            hop_ms = int(payload.get("hop_ms", 20))
            frames = int(payload.get("frames", 0))
            q = payload.get("q", {}) or {}
            lvl_b64z = q.get("lvl", "")
            lvl = _b64z_unpack_u8(str(lvl_b64z)) if lvl_b64z else b""

            if frames and len(lvl) >= frames:
                self._hop_ms = max(10, hop_ms)
                self._frames = frames
                self._lvl = lvl[:frames]
            else:
                self._lvl = None
                self._frames = 0
                self._hop_ms = max(10, hop_ms)
        except Exception:
            self._lvl = None
            self._frames = 0

        self._recompute()

    # ── Connection helpers ─────────────────────────────────────────
    def _try_connect(self, signal_name: str, slot) -> None:
        try:
            sig = getattr(self._player, signal_name, None)
            if sig is not None:
                sig.connect(slot)
        except Exception:
            pass

    def _try_disconnect(self, signal_name: str, slot) -> None:
        try:
            sig = getattr(self._player, signal_name, None)
            if sig is not None:
                sig.disconnect(slot)
        except Exception:
            pass

    def _disconnect_player(self) -> None:
        if not self._player:
            return
        self._try_disconnect("playbackStateChanged", self._on_state_changed)
        self._try_disconnect("stateChanged", self._on_state_changed)
        self._try_disconnect("positionChanged", self._on_pos_changed)

    def _disconnect_audio_out(self) -> None:
        ao = self._audio_out
        if ao is None:
            return
        try:
            if hasattr(ao, "volumeChanged"):
                ao.volumeChanged.disconnect(self._on_volume_changed)
        except Exception:
            pass
        try:
            if hasattr(ao, "mutedChanged"):
                ao.mutedChanged.disconnect(self._on_muted_changed)
        except Exception:
            pass
        self._audio_out = None

    def _wire_audio_out(self) -> None:
        self._audio_out = None
        self._volume = 1.0
        self._muted = False

        p = self._player
        if p is None:
            return

        # Qt6: QMediaPlayer.audioOutput() -> QAudioOutput
        try:
            fn = getattr(p, "audioOutput", None)
            ao = fn() if callable(fn) else None
            if ao is not None:
                self._audio_out = ao
                try:
                    self._volume = float(ao.volume())
                except Exception:
                    self._volume = 1.0
                try:
                    self._muted = bool(ao.isMuted())
                except Exception:
                    self._muted = False

                try:
                    if hasattr(ao, "volumeChanged"):
                        ao.volumeChanged.connect(self._on_volume_changed)
                except Exception:
                    pass
                try:
                    if hasattr(ao, "mutedChanged"):
                        ao.mutedChanged.connect(self._on_muted_changed)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # Fallback: player.volume() 0..100, player.isMuted()
        try:
            v = getattr(p, "volume", None)
            if callable(v):
                self._volume = float(v()) / 100.0
            elif v is not None:
                self._volume = float(v) / 100.0
        except Exception:
            self._volume = 1.0

        try:
            m = getattr(p, "isMuted", None)
            if callable(m):
                self._muted = bool(m())
            elif m is not None:
                self._muted = bool(m)
        except Exception:
            self._muted = False

    # ── Signals ───────────────────────────────────────────────────
    def _on_state_changed(self, state) -> None:
        self._playing = self._state_is_playing(state)
        if not self._playing:
            self._stale_pos_ticks = 0
        self._recompute()

    def _on_pos_changed(self, pos) -> None:
        try:
            self._position_ms = int(pos)
        except Exception:
            return
        self._recompute()

    def _on_volume_changed(self, v) -> None:
        try:
            self._volume = float(v)
        except Exception:
            return
        self._recompute()

    def _on_muted_changed(self, m) -> None:
        try:
            self._muted = bool(m)
        except Exception:
            return
        self._recompute()

    # ── Poll fallback ─────────────────────────────────────────────
    def _read_state(self) -> Optional[object]:
        p = self._player
        if p is None:
            return None
        for name in ("playbackState", "state"):
            try:
                attr = getattr(p, name, None)
                if callable(attr):
                    return attr()
                if attr is not None:
                    return attr
            except Exception:
                continue
        return None

    def _read_position(self) -> Optional[int]:
        p = self._player
        if p is None:
            return None
        try:
            attr = getattr(p, "position", None)
            if callable(attr):
                return int(attr())
            if attr is not None:
                return int(attr)
        except Exception:
            pass
        return None

    def _poll_tick(self) -> None:
        if self._shutting_down or not self._enabled:
            self._set_visible(False)
            return
        if self._player is None:
            self._set_visible(False)
            return

        # refresh audio output state defensively
        if self._audio_out is not None:
            try:
                self._volume = float(self._audio_out.volume())
            except Exception:
                pass
            try:
                self._muted = bool(self._audio_out.isMuted())
            except Exception:
                pass

        st = self._read_state()
        if st is not None:
            self._playing = self._state_is_playing(st)

        pos = self._read_position()
        if pos is not None:
            self._last_pos_ms = pos
            self._position_ms = pos

        self._recompute()

    # ── Gating logic ──────────────────────────────────────────────
    @staticmethod
    def _clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

    def _effective_volume(self) -> float:
        if self._muted:
            return 0.0
        return self._clamp01(self._volume)

    def _analysis_level(self) -> Optional[float]:
        if not self._lvl or self._frames <= 0 or self._hop_ms <= 0:
            return None
        idx = int(self._position_ms / self._hop_ms)
        if idx < 0:
            idx = 0
        elif idx >= self._frames:
            idx = self._frames - 1
        try:
            return float(self._lvl[idx]) / 255.0
        except Exception:
            return None

    def _recompute(self) -> None:
        if not self._enabled:
            self._set_visible(False)
            return

        # pause/stop or no track: hide
        if not self._playing or not self._audio_path:
            self._set_visible(False)
            return

        # Quiet passages, mute, and zero volume keep the visualizer present at
        # its baseline. Playback state owns visibility; audio level does not.
        self._set_visible(True)

    def _set_visible(self, v: bool) -> None:
        v = bool(v)
        if v == self._visible:
            return
        self._visible = v
        self.visibleChanged.emit(v)


# ─────────────────────────────────────────────────────────────────────────────
# Public Widget
# ─────────────────────────────────────────────────────────────────────────────
class SoundPreviewWidget(QtWidgets.QFrame):
    """A self-contained preview frame that hosts the audio visualizer.

    Public API:
    - set_media_player(player) -> None
    - set_audio_file(path) -> None
    - set_analysis_payload(payload) -> None
    - set_tab_active(active) -> None
    - shutdown() -> None
    """

    def __init__(self, media_player, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._shutdown = False
        self._tab_active = False
        self._audio_path = ""
        self._renderer_mode = "inactive"
        self.setObjectName("sound_preview_frame")
        self.setStyleSheet(
            "#sound_preview_frame {"
            "  background: #101014;"
            "  border: 1px solid #2b2b31;"
            "  border-radius: 12px;"
            "}"
        )
        self.setAccessibleName("Sound Preview Frame")
        self.setToolTip("Music visualizer")

        # Build both stages: blank (vanished) and visualizer (active)
        self._blank = _BlankStage(self)

        self._live_visualizer = _LiveBarVisualizer(self)
        self._analyzed_visualizer = (
            _AudioVisualizerUltra(self)
            if _AudioVisualizerUltra is not None
            else None
        )

        self._blank.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._live_visualizer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        if self._analyzed_visualizer is not None:
            self._analyzed_visualizer.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )

        # Stacked layout to keep size stable while "vanishing"
        stack = QtWidgets.QStackedLayout()
        stack.setContentsMargins(8, 8, 8, 8)
        stack.addWidget(self._blank)             # index 0: inactive
        stack.addWidget(self._live_visualizer)   # index 1: live PCM
        if self._analyzed_visualizer is not None:
            stack.addWidget(self._analyzed_visualizer)  # index 2: analyzed
        stack.setCurrentIndex(0)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(stack, 1)
        self._stack = stack

        # Gate decides whether to show visualizer or blank
        self._gate = _VisualizerGate(self)
        self._gate.visibleChanged.connect(self._on_gate_visible)

        if media_player is not None:
            self.set_media_player(media_player)

    def _on_gate_visible(self, show: bool) -> None:
        show = bool(show and self._tab_active and not self._shutdown)
        analyzed = bool(
            show
            and self._analyzed_visualizer is not None
            and self._analyzed_visualizer.has_analysis()
        )
        self._renderer_mode = "analyzed" if analyzed else "live" if show else "inactive"
        self._stack.setCurrentIndex(2 if analyzed else 1 if show else 0)

        self._live_visualizer.set_active(bool(show and not analyzed))
        if self._analyzed_visualizer is not None:
            self._analyzed_visualizer.set_active(analyzed)

    # ── Public API ──────────────────────────────────────────────────────────
    def set_media_player(self, player) -> None:
        if self._shutdown:
            return

        # Wire gate FIRST (controls visibility)
        try:
            self._gate.set_media_player(player)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_media_player warning: {e!r}")

        # Rebind both renderers. Only the selected renderer consumes buffers.
        try:
            self._live_visualizer.set_media_player(player)
            if self._analyzed_visualizer is not None:
                self._analyzed_visualizer.set_media_player(player)
        except Exception as e:
            logging.debug(f"[SoundPreview] visualizer set_media_player warning: {e!r}")

        # Sync current visible state into visualizer active flag
        self._on_gate_visible(self._gate.is_visible())

    def set_audio_file(self, path: str) -> None:
        if self._shutdown:
            return

        new_path = str(path or "")
        changed = self._normalized_path(new_path) != self._normalized_path(self._audio_path)
        self._audio_path = new_path
        if changed:
            self._clear_analysis()

        # Update gate so it can silence-detect
        try:
            self._gate.set_audio_file(new_path)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_audio_file warning: {e!r}")

        # Pass through to visualizer
        try:
            self._live_visualizer.set_audio_file(new_path)
            if self._analyzed_visualizer is not None:
                self._analyzed_visualizer.set_audio_file(new_path)
        except Exception as e:
            logging.debug(f"[SoundPreview] visualizer set_audio_file warning: {e!r}")

        self._on_gate_visible(self._gate.is_visible())

    def set_analysis_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        if self._shutdown:
            return

        accepted = payload if self._analysis_matches_current_track(payload) else None

        # The gate retains the payload contract for compatibility, while
        # playback state alone determines whether the preview is visible.
        try:
            self._gate.set_analysis_payload(accepted)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_analysis_payload warning: {e!r}")

        # Visualizer uses this for drawing
        try:
            if self._analyzed_visualizer is not None:
                self._analyzed_visualizer.set_analysis_payload(accepted)
        except Exception as e:
            logging.debug(f"[SoundPreview] visualizer set_analysis_payload warning: {e!r}")

        self._on_gate_visible(self._gate.is_visible())

    def set_tab_active(self, active: bool) -> None:
        """Enable gate polling and drawing only while Sound owns the preview."""
        if self._shutdown:
            return
        self._tab_active = bool(active)
        self._gate.set_enabled(self._tab_active)
        self._on_gate_visible(self._gate.is_visible() if self._tab_active else False)

    def visualizer(self) -> QtWidgets.QWidget:
        return self._analyzed_visualizer or self._live_visualizer

    def renderer_mode(self) -> str:
        return self._renderer_mode

    def is_animation_running(self) -> bool:
        if self._renderer_mode == "live":
            return self._live_visualizer.is_animation_running()
        if self._renderer_mode == "analyzed" and self._analyzed_visualizer is not None:
            return self._analyzed_visualizer.is_animation_running()
        return False

    @staticmethod
    def _normalized_path(path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve()).casefold()
        except Exception:
            return str(path).casefold()

    def _analysis_matches_current_track(self, payload: Optional[Dict[str, Any]]) -> bool:
        if not payload or not self._audio_path:
            return False
        source = payload.get("src")
        if not isinstance(source, dict):
            return False
        if self._normalized_path(str(source.get("path", ""))) != self._normalized_path(
            self._audio_path
        ):
            return False
        try:
            current = Path(self._audio_path).stat()
        except OSError:
            return True
        try:
            return (
                int(source.get("size", -1)) == current.st_size
                and abs(float(source.get("mtime", -1.0)) - current.st_mtime) <= 1e-6
            )
        except (TypeError, ValueError):
            return False

    def _clear_analysis(self) -> None:
        self._gate.set_analysis_payload(None)
        if self._analyzed_visualizer is not None:
            self._analyzed_visualizer.set_analysis_payload(None)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._tab_active = False

        try:
            self._gate.shutdown()
        except Exception:
            pass
        try:
            self._on_gate_visible(False)
        except Exception:
            pass
        try:
            self._live_visualizer.shutdown()
        except Exception:
            pass
        try:
            if self._analyzed_visualizer is not None:
                self._analyzed_visualizer.set_media_player(None)
                self._analyzed_visualizer.shutdown()
        except Exception:
            pass
        self._renderer_mode = "inactive"

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)


__all__ = ["SoundPreviewWidget"]
