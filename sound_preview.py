# ===============================
# File: sound_preview.py
# ===============================
from __future__ import annotations

import logging
from typing import Optional, Any, Dict
from pathlib import Path
import base64
import zlib
import math

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

# Try to import QMediaPlayer constants for robust state comparisons.
try:
    from PySide6.QtMultimedia import QMediaPlayer  # type: ignore
except Exception:
    QMediaPlayer = None  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Try to import the full visualizer. If it fails, use the built-in minimal visualizer.
# ─────────────────────────────────────────────────────────────────────────────
_AudioVisualizerUltra = None  # type: ignore[var-annotated]

try:
    from sound_visualizer import AudioVisualizerUltra as _AudioVisualizerUltra  # type: ignore
except Exception:
    _AudioVisualizerUltra = None


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
class _MiniBarVisualizer(QtWidgets.QWidget):
    """
    Lightweight fallback: animated EQ bars while active.

    - `set_active(True)` starts motion.
    - `set_active(False)` collapses immediately.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._bars = [0.0] * 12
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self._active = False

        self._bg_color = QtGui.QColor("#0f1116")
        self._bar_color = QtGui.QColor(0, 210, 255, 180)
        self._grid_color = QtGui.QColor(255, 255, 255, 12)

    # Compatibility no-ops
    def set_media_player(self, _player) -> None:
        return

    def set_audio_file(self, _path: str) -> None:
        return

    def set_analysis_payload(self, _payload: Optional[Dict[str, Any]]) -> None:
        return

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._bars = [0.0] * len(self._bars)
            self._phase = 0.0
            self.update()

    def _tick(self) -> None:
        if not self._active:
            return
        self._phase += 0.055
        for i in range(len(self._bars)):
            w = 0.85 + 0.15 * math.sin(self._phase * (1.5 + 0.17 * i))
            self._bars[i] = 0.18 + 0.72 * abs(math.sin(self._phase * (0.8 + 0.13 * i))) * w
        self.update()

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
        self._silence_threshold = 0.003

        self._visible = False

        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._poll_tick)
        self._shutting_down = False

    def is_visible(self) -> bool:
        return self._visible

    def shutdown(self) -> None:
        self._shutting_down = True
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

        self._poll.start()
        self._poll_tick()

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
        if self._shutting_down:
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
            # Do not mark playback as paused just because position stalls briefly.
            # Some Qt backends update position in coarse bursts, especially on MP3s.
            # QMediaPlayer's playback state is the authority here.
            if self._playing:
                if pos == self._last_pos_ms:
                    self._stale_pos_ticks += 1
                else:
                    self._stale_pos_ticks = 0
            else:
                self._stale_pos_ticks = 0

            self._last_pos_ms = pos
            self._position_ms = pos

        self._recompute()

    # ── Gating logic ──────────────────────────────────────────────
    @staticmethod
    def _clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

    def _effective_volume(self) -> float:
        """
        Visualizer volume gate.

        Important: muted audio should not make the preview look dead.
        Muting only silences QAudioOutput; playback still continues, so the
        visualizer should keep responding while muted. A real 0% volume still
        hides the visualizer.
        """
        return self._clamp01(self._volume)

    def _analysis_level(self) -> Optional[float]:
        if not self._lvl or self._frames <= 0 or self._hop_ms <= 0:
            return None
        idx = int(self._position_ms / self._hop_ms)
        if idx < 0:
            idx = 0
        elif idx >= self._frames:
            idx = self._frames - 1

        # Use a tiny neighborhood so the preview does not blink off between
        # adjacent low-energy frames. This keeps silence detection useful while
        # avoiding the dead-preview look during quiet musical passages.
        try:
            lo = max(0, idx - 2)
            hi = min(self._frames - 1, idx + 2)
            vals = [float(self._lvl[i]) / 255.0 for i in range(lo, hi + 1)]
            return max(vals) if vals else None
        except Exception:
            return None

    def _recompute(self) -> None:
        # pause/stop or no track: hide
        if not self._playing or not self._audio_path:
            self._set_visible(False)
            return

        vol = self._effective_volume()
        if vol <= 0.0:
            self._set_visible(False)
            return

        # If we have analysis, hide on *true* near-silence.
        lvl = self._analysis_level()
        if lvl is not None:
            amp = lvl * vol
            self._set_visible(amp >= self._silence_threshold)
            return

        # No analysis yet: show while playing.
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
    - shutdown() -> None

    Behavior:
    - Uses the analyzer-driven visualizer when analysis payload exists.
    - Falls back to a lightweight live-motion visualizer when analysis is missing.
    - Keeps the preview alive while muted, because muted playback still advances.
    """

    def __init__(self, media_player, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._shutdown = False
        self._has_analysis_payload = False
        self._current_audio_path = ""

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

        self._blank = _BlankStage(self)
        self._mini_visualizer = _MiniBarVisualizer(self)

        self._premium_available = _AudioVisualizerUltra is not None
        if self._premium_available:
            self._visualizer = _AudioVisualizerUltra(self)
        else:
            self._visualizer = self._mini_visualizer

        self._blank.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._visualizer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._mini_visualizer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        stack = QtWidgets.QStackedLayout()
        stack.setContentsMargins(8, 8, 8, 8)
        stack.addWidget(self._blank)
        self._blank_index = 0

        if self._premium_available:
            stack.addWidget(self._visualizer)
            self._premium_index = 1
            stack.addWidget(self._mini_visualizer)
            self._mini_index = 2
        else:
            stack.addWidget(self._mini_visualizer)
            self._premium_index = -1
            self._mini_index = 1

        stack.setCurrentIndex(self._blank_index)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(stack, 1)
        self._stack = stack

        self._gate = _VisualizerGate(self)
        self._gate.visibleChanged.connect(self._on_gate_visible)

        if media_player is not None:
            self.set_media_player(media_player)

    def _all_visualizers(self):
        seen = set()
        for widget in (self._visualizer, self._mini_visualizer):
            if widget is None:
                continue
            key = id(widget)
            if key in seen:
                continue
            seen.add(key)
            yield widget

    def _selected_visualizer(self):
        # Prefer the analyzer-driven visualizer only after a valid cache payload
        # has actually been applied. If the cache is missing or still loading,
        # use the lightweight fallback so the preview remains alive instead of
        # displaying "Analysis cache unavailable" forever.
        if self._premium_available and self._has_analysis_payload:
            return self._visualizer, self._premium_index
        return self._mini_visualizer, self._mini_index

    def _on_gate_visible(self, show: bool) -> None:
        if self._shutdown:
            show = False

        if not show:
            self._stack.setCurrentIndex(self._blank_index)
            for widget in self._all_visualizers():
                try:
                    if hasattr(widget, "set_active"):
                        widget.set_active(False)  # type: ignore[attr-defined]
                except Exception:
                    pass
            return

        active_widget, active_index = self._selected_visualizer()
        self._stack.setCurrentIndex(active_index)

        for widget in self._all_visualizers():
            try:
                if hasattr(widget, "set_active"):
                    widget.set_active(widget is active_widget)  # type: ignore[attr-defined]
            except Exception:
                pass

    def set_media_player(self, player) -> None:
        if self._shutdown:
            return

        try:
            self._gate.set_media_player(player)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_media_player warning: {e!r}")

        for widget in self._all_visualizers():
            try:
                if hasattr(widget, "set_media_player"):
                    widget.set_media_player(player)  # type: ignore[call-arg]
            except Exception as e:
                logging.debug(f"[SoundPreview] visualizer set_media_player warning: {e!r}")

        self._on_gate_visible(self._gate.is_visible())

    def set_audio_file(self, path: str) -> None:
        if self._shutdown:
            return

        path = path or ""
        changed = path != self._current_audio_path
        self._current_audio_path = path

        if changed:
            # A new song must not inherit the previous song's cached analysis.
            # The correct cache will be applied immediately afterward if it exists.
            self._has_analysis_payload = False
            try:
                self._gate.set_analysis_payload(None)
            except Exception:
                pass
            for widget in self._all_visualizers():
                try:
                    if hasattr(widget, "set_analysis_payload"):
                        widget.set_analysis_payload(None)  # type: ignore[attr-defined]
                except Exception:
                    pass

        try:
            self._gate.set_audio_file(path)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_audio_file warning: {e!r}")

        for widget in self._all_visualizers():
            try:
                if hasattr(widget, "set_audio_file"):
                    widget.set_audio_file(path)  # type: ignore[call-arg]
            except Exception as e:
                logging.debug(f"[SoundPreview] visualizer set_audio_file warning: {e!r}")

        self._on_gate_visible(self._gate.is_visible())

    def set_analysis_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        if self._shutdown:
            return

        self._has_analysis_payload = False
        try:
            if payload:
                q = payload.get("q", {}) or {}
                frames = int(payload.get("frames", 0) or 0)
                self._has_analysis_payload = bool(frames > 0 and q.get("lvl"))
        except Exception:
            self._has_analysis_payload = False

        try:
            self._gate.set_analysis_payload(payload)
        except Exception as e:
            logging.debug(f"[SoundPreview] gate set_analysis_payload warning: {e!r}")

        for widget in self._all_visualizers():
            try:
                if hasattr(widget, "set_analysis_payload"):
                    widget.set_analysis_payload(payload)  # type: ignore[attr-defined]
            except Exception as e:
                logging.debug(f"[SoundPreview] visualizer set_analysis_payload warning: {e!r}")

        self._on_gate_visible(self._gate.is_visible())

    def visualizer(self) -> QtWidgets.QWidget:
        return self._visualizer

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True

        try:
            self._gate.shutdown()
        except Exception:
            pass
        try:
            self._on_gate_visible(False)
        except Exception:
            pass

        for widget in self._all_visualizers():
            try:
                if hasattr(widget, "set_media_player"):
                    widget.set_media_player(None)  # type: ignore[call-arg]
            except Exception:
                pass
            try:
                if hasattr(widget, "shutdown"):
                    widget.shutdown()  # type: ignore[attr-defined]
            except Exception:
                pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)


__all__ = ["SoundPreviewWidget"]
