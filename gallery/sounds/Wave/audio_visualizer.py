# audio_visualizer.py — Ultra visualizer (defensive, precomputed bins, pro polish)
from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass
from typing import Optional, List, Tuple

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from pydub import AudioSegment

try:
    from PySide6.QtMultimedia import QMediaPlayer  # type: ignore
except Exception:  # pragma: no cover
    QMediaPlayer = object  # type: ignore[misc,assignment]


@dataclass
class BandGroup:
    name: str
    f_lo: float
    f_hi: float
    color: QtGui.QColor


# ── Palette ───────────────────────────────────────────────────────────────────
DEFAULT_GROUPS: List[BandGroup] = [
    BandGroup("Bass",      20.0,   120.0,  QtGui.QColor(170, 170, 170)),  # gray
    BandGroup("Low-Mid",   120.0,  500.0,  QtGui.QColor(160,  60, 255)),  # purple
    BandGroup("Mid",       500.0,  2000.0, QtGui.QColor(255, 204,  64)),  # gold
    BandGroup("High-Mid",  2000.0, 6000.0, QtGui.QColor( 60, 220, 160)),  # cyan-green
    BandGroup("Treble",    6000.0, 18000.0,QtGui.QColor( 90, 140, 255)),  # blue
]


class AudioVisualizerUltra(QtWidgets.QWidget):
    """
    Ultra visualizer with grouped FFT bars, peaks, trails, spectral-flux flash,
    sub glow, sparkles, scanline, and optional waveform.

    Public API:
      set_media_player(QMediaPlayer)
      set_audio_file(str, sample_rate=44100)
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMinimumHeight(180)

        # Threading — define FIRST to avoid init-order issues
        self._lock = threading.Lock()
        self._decoding = False

        # Visual base
        self._bg = QtGui.QColor(8, 9, 12)
        self._grid = QtGui.QColor(30, 32, 40, 220)

        # Config & toggles
        self._groups: List[BandGroup] = list(DEFAULT_GROUPS)
        self._bars_per_group: int = 14
        self._flashiness: float = 1.0
        self._show_waveform = False
        self._enable_flux_flash = True
        self._enable_sub_glow = True
        self._enable_sparkles = True
        self._enable_scanline = True

        # Audio state
        self._player: Optional[QMediaPlayer] = None
        self._sr: int = 44100
        self._pcm: Optional[np.ndarray] = None  # mono float32 [-1..1]
        self._duration: float = 0.0

        # Analysis settings
        self._fft_size: int = 4096
        self._window: np.ndarray = np.hanning(self._fft_size).astype(np.float32)
        self._min_db, self._max_db = -90.0, -10.0
        self._gamma: float = 1.0  # 1.0=linear; >1 compresses peaks
        self._prev_mag: Optional[np.ndarray] = None

        # Precomputed FFT bins (indices for each bar)
        self._freqs: Optional[np.ndarray] = None
        self._bins: List[np.ndarray] = []        # index arrays per bar
        self._bar_colors: List[QtGui.QColor] = []

        # Bar state arrays — create BEFORE any rebuilds
        total = self._bar_count()
        self._bar_vals = np.zeros(total, np.float32)
        self._bar_peaks = np.zeros(total, np.float32)
        self._bar_trails = np.zeros(total, np.float32)

        # Dynamics
        self._bar_fall = 120.0
        self._peak_fall = 40.0
        self._trail_fall = 1.8
        self._lp = 0.18  # low-pass factor on bar input

        # Flux / flash
        self._flux_energy = 0.0
        self._flux_decay = 2.6
        self._flux_gain = 12.0

        # Sub glow envelope
        self._sub_env = 0.0

        # Sparkles
        self._sparkles: List[dict] = []
        self._max_sparkles = 140

        # Scanline
        self._scan_x = 0.0
        self._scan_v = 120.0

        # Timing (start timers AFTER state is ready)
        self._clock = QtCore.QElapsedTimer(); self._clock.start()
        self._frame_clock = QtCore.QElapsedTimer(); self._frame_clock.start()

        # Build bins now that everything exists
        self._rebuild_bins()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self.update)
        self._timer.start()

    # ── Public API ───────────────────────────────────────────────────────────
    def set_media_player(self, player: QMediaPlayer) -> None:  # noqa: N802
        self._player = player

    def set_audio_file(self, path: str, sample_rate: int = 44100) -> None:  # noqa: N802
        if self._decoding:
            return
        self._decoding = True

        def _work():
            try:
                seg = AudioSegment.from_file(path).set_channels(1).set_frame_rate(sample_rate)
                pcm_i16 = np.array(seg.get_array_of_samples(), dtype=np.int16)
                pcm = pcm_i16.astype(np.float32) / 32768.0
                with self._lock:
                    self._sr = int(sample_rate)
                    self._pcm = pcm
                    self._duration = pcm.size / float(self._sr)
                    self._prev_mag = None
                # Rebuild bins after SR change (no UI objects touched here)
                self._rebuild_bins()
            except Exception as e:
                print(f"[AudioVisualizerUltra] Decode failed: {e}")
                with self._lock:
                    self._pcm = None
                    self._duration = 0.0
            finally:
                self._decoding = False

        threading.Thread(target=_work, daemon=True).start()

    # Tunables
    def set_bars_per_group(self, n: int) -> None:  # noqa: N802
        self._bars_per_group = max(4, int(n))
        self._rebuild_bins()
        self._resize_bar_buffers()

    def set_groups(self, groups: List[BandGroup]) -> None:  # noqa: N802
        self._groups = list(groups) if groups else list(DEFAULT_GROUPS)
        self._rebuild_bins()
        self._resize_bar_buffers()

    def set_show_waveform(self, yes: bool) -> None:  # noqa: N802
        self._show_waveform = bool(yes)

    def set_flashiness(self, level: float) -> None:  # noqa: N802
        self._flashiness = float(max(0.0, min(2.0, level)))

    def set_enable_flux_flash(self, yes: bool) -> None:  # noqa: N802
        self._enable_flux_flash = bool(yes)

    def set_enable_sub_glow(self, yes: bool) -> None:  # noqa: N802
        self._enable_sub_glow = bool(yes)

    def set_enable_sparkles(self, yes: bool) -> None:  # noqa: N802
        self._enable_sparkles = bool(yes)

    def set_enable_scanline(self, yes: bool) -> None:  # noqa: N802
        self._enable_scanline = bool(yes)

    # Expert knobs
    def set_fft_size(self, n: int) -> None:
        n = int(max(1024, min(32768, 1 << (int(n - 1).bit_length()))))
        if n != self._fft_size:
            self._fft_size = n
            self._window = np.hanning(self._fft_size).astype(np.float32)
            self._prev_mag = None
            self._rebuild_bins()

    def set_db_range(self, min_db: float, max_db: float) -> None:
        if min_db < max_db:
            self._min_db, self._max_db = float(min_db), float(max_db)

    def set_gamma(self, gamma: float) -> None:
        self._gamma = float(max(0.1, min(3.0, gamma)))

    # ── Internals: timing & analysis ─────────────────────────────────────────
    def _bar_count(self) -> int:
        return max(1, len(self._groups) * self._bars_per_group)

    def _resize_bar_buffers(self) -> None:
        total = self._bar_count()
        # Defensive: handle first run where arrays may not exist yet
        if not hasattr(self, "_bar_vals") or self._bar_vals.shape[0] != total:
            self._bar_vals = np.zeros(total, np.float32)
            self._bar_peaks = np.zeros(total, np.float32)
            self._bar_trails = np.zeros(total, np.float32)

    def _time_sec(self) -> float:
        if self._player is not None:
            try:
                return max(0.0, float(self._player.position()) / 1000.0)
            except Exception:
                pass
        return self._clock.elapsed() / 1000.0

    def _slice(self) -> Optional[np.ndarray]:
        with self._lock:
            pcm = self._pcm
            sr = self._sr
        if pcm is None:
            return None
        idx = int(self._time_sec() * sr)
        half = self._fft_size // 2
        lo = max(0, idx - half)
        hi = min(pcm.size, lo + self._fft_size)
        chunk = pcm[lo:hi]
        if chunk.size < self._fft_size:
            chunk = np.pad(chunk, (0, self._fft_size - chunk.size))
        return (chunk * self._window).astype(np.float32, copy=False)

    def _spectrum(self) -> Tuple[np.ndarray, np.ndarray]:
        chunk = self._slice()
        if chunk is None:
            n = self._fft_size // 2 + 1
            z = np.zeros(n, np.float32)
            return z, z
        spec = np.fft.rfft(chunk, n=self._fft_size)
        mag = np.abs(spec).astype(np.float32) + 1e-12
        # Cache/update freqs if SR/FFT changed
        if self._freqs is None or self._freqs.shape[0] != mag.shape[0]:
            sr = self._sr  # simple read; protected writes happen under lock
            self._freqs = np.fft.rfftfreq(self._fft_size, d=1.0 / float(sr)).astype(np.float32)
        return mag, self._freqs

    def _spectral_flux(self, mag: np.ndarray) -> float:
        prev = self._prev_mag
        if prev is None or prev.shape != mag.shape:
            self._prev_mag = mag.copy()
            return 0.0
        diff = mag - prev
        self._prev_mag = mag
        return float(np.sum(np.clip(diff, 0, None)) / (mag.size + 1e-9))

    def _rebuild_bins(self) -> None:
        """Recompute FFT bin indices & colors when SR/FFT/groups/bars change."""
        sr = getattr(self, "_sr", 44100)
        self._freqs = np.fft.rfftfreq(self._fft_size, d=1.0 / float(sr)).astype(np.float32)

        bins: List[np.ndarray] = []
        colors: List[QtGui.QColor] = []

        for g in self._groups:
            # geometric spacing gives better low-end resolution
            edges = np.geomspace(max(1.0, g.f_lo),
                                 max(g.f_lo + 1.0, g.f_hi),
                                 self._bars_per_group + 1)
            for k in range(self._bars_per_group):
                lo, hi = edges[k], edges[k + 1]
                idx = np.where((self._freqs >= lo) & (self._freqs < hi))[0]
                if idx.size == 0:
                    nearest = int(np.argmin(np.abs(self._freqs - (0.5 * (lo + hi)))))
                    idx = np.array([nearest], dtype=int)
                bins.append(idx)
                colors.append(g.color)

        self._bins = bins
        self._bar_colors = colors
        self._resize_bar_buffers()

    # ── Painting ─────────────────────────────────────────────────────────────
    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(640, 220)

    def paintEvent(self, _: QtGui.QPaintEvent) -> None:
        # Defensive sizes
        self._resize_bar_buffers()

        p = QtGui.QPainter(self)
        r = QtCore.QRectF(self.rect())
        p.fillRect(r, self._bg)
        self._draw_grid(p, r.adjusted(0, 6, 0, -6))

        # Timing
        dt_ms = max(1.0, float(self._frame_clock.restart()))
        dt = dt_ms / 1000.0

        mag, freqs = self._spectrum()
        if mag.size > 1:
            # Global dynamics
            flux = self._spectral_flux(mag) if self._enable_flux_flash else 0.0
            self._flux_energy = max(0.0, self._flux_energy - self._flux_decay * dt) + self._flux_gain * flux

            # Group energies for overlays
            energies = []
            for g in self._groups:
                idx = np.where((freqs >= g.f_lo) & (freqs < g.f_hi))[0]
                if idx.size:
                    m = float(np.mean(mag[idx]))
                    db = 20.0 * math.log10(m + 1e-12)
                    v = (db - self._min_db) / (self._max_db - self._min_db)
                    energies.append(max(0.0, min(1.0, v)))
                else:
                    energies.append(0.0)

            bass_v = energies[0] if energies else 0.0
            treble_v = energies[-1] if energies else 0.0
            self._sub_env = (1.0 - self._lp) * self._sub_env + self._lp * bass_v

            overall = float(np.mean(energies)) if energies else 0.0
            self._scan_v = 100.0 + 600.0 * overall * self._flashiness
            self._scan_x = (self._scan_x + self._scan_v * dt) % max(1.0, r.width())

            # Bars
            self._draw_bars(p, r, mag, dt)

            # Overlays
            if self._enable_sub_glow and bass_v > 0.0:
                self._draw_sub_glow(p, r, bass_v)
            if self._enable_sparkles:
                self._update_and_draw_sparkles(p, r, treble_v, dt)
            if self._enable_scanline:
                self._draw_scanline(p, r)

            if self._enable_flux_flash and self._flux_energy > 0.0:
                a = int(max(0, min(110, 110 * self._flux_energy * 0.6 * self._flashiness)))
                if a > 0:
                    p.fillRect(r, QtGui.QColor(255, 255, 255, a))

            if self._show_waveform:
                self._draw_waveform(p, r.adjusted(8, 8, -8, -8))

        p.end()

    # ── Drawing helpers ──────────────────────────────────────────────────────
    def _draw_grid(self, p: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        p.setPen(QtGui.QPen(self._grid, 1))
        for i in range(1, 4):
            y = rect.top() + i * rect.height() / 4.0
            p.drawLine(rect.left(), y, rect.right(), y)

    def _draw_bars(self, p: QtGui.QPainter, r: QtCore.QRectF, mag: np.ndarray, dt: float) -> None:
        total = self._bar_count()
        if total <= 0 or not self._bins:
            return

        mag_db = 20.0 * np.log10(mag + 1e-12, dtype=np.float32)

        max_h = r.height() - 16.0
        x = r.left() + 8.0
        avail_w = r.width() - 16.0
        bw = float(avail_w) / (total * 1.25)
        gap = bw * 0.25
        y_bot = r.bottom() - 8.0

        fall = (self._bar_fall * dt) / max(1e-6, (self._max_db - self._min_db))
        peak_fall = self._peak_fall * dt / max(1.0, (self._max_db - self._min_db))
        trail_keep = max(0.0, min(1.0, 1.0 - self._trail_fall * dt * 0.5))

        for i, idx in enumerate(self._bins):
            db = float(np.mean(mag_db[idx])) if idx.size else self._min_db
            v = (db - self._min_db) / (self._max_db - self._min_db)
            v = max(0.0, min(1.0, v))
            v = (1.0 - self._lp) * float(self._bar_vals[i]) + self._lp * v
            if self._gamma != 1.0:
                v = pow(v, 1.0 / self._gamma)

            self._bar_vals[i] = max(v, self._bar_vals[i] - fall)

            if self._bar_vals[i] > self._bar_peaks[i]:
                self._bar_peaks[i] = self._bar_vals[i]
            else:
                self._bar_peaks[i] = max(0.0, self._bar_peaks[i] - peak_fall)

            self._bar_trails[i] = max(self._bar_trails[i] * trail_keep, self._bar_vals[i])

            h = float(self._bar_vals[i]) * max_h
            trail_h = float(self._bar_trails[i]) * max_h
            peak_h = float(self._bar_peaks[i]) * max_h

            base_col = self._bar_colors[i]

            # Trail
            trail_rect = QtCore.QRectF(x, y_bot - trail_h, bw, trail_h)
            p.fillRect(trail_rect, QtGui.QColor(base_col.red(), base_col.green(), base_col.blue(), 60))

            # Main bar gradient
            grad = QtGui.QLinearGradient(QtCore.QPointF(x, y_bot - h), QtCore.QPointF(x, y_bot))
            grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 120))
            grad.setColorAt(0.15, base_col)
            c_mid = QtGui.QColor(base_col.red(), base_col.green(), base_col.blue(), 220)
            grad.setColorAt(0.85, c_mid)
            grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            bar_rect = QtCore.QRectF(x, y_bot - h, bw, h)
            p.fillRect(bar_rect, grad)

            # Peak cap
            cap_h = min(4.0 + 2.0 * self._flashiness, h)
            cap_y = y_bot - peak_h
            cap_rect = QtCore.QRectF(x, cap_y - cap_h * 0.5, bw, cap_h)
            p.fillRect(cap_rect, QtGui.QColor(255, 255, 255, 210))

            # Side glow
            glow_alpha = int(80 * self._flashiness)
            if glow_alpha > 0:
                for s in range(1, 4):
                    gx = x - s * 1.2
                    gw = bw + s * 2.4
                    ga = max(0, glow_alpha - s * 18)
                    p.fillRect(QtCore.QRectF(gx, y_bot - h - s * 1.0, gw, h + s * 2.0),
                               QtGui.QColor(base_col.red(), base_col.green(), base_col.blue(), ga))

            x += bw + gap

    def _draw_sub_glow(self, p: QtGui.QPainter, r: QtCore.QRectF, bass_v: float) -> None:
        strength = (0.35 + 0.65 * self._flashiness) * (0.2 + 0.8 * self._sub_env)
        alpha = int(160 * strength)
        if alpha <= 0:
            return
        h = max(16.0, r.height() * 0.25 * strength)
        grad = QtGui.QLinearGradient(QtCore.QPointF(r.center().x(), r.bottom()),
                                     QtCore.QPointF(r.center().x(), r.bottom() - h))
        c1 = QtGui.QColor(200, 200, 200, alpha)
        c2 = QtGui.QColor(200, 200, 200, int(alpha * 0.1))
        grad.setColorAt(0.0, c1); grad.setColorAt(1.0, c2)
        p.fillRect(QtCore.QRectF(r.left(), r.bottom() - h, r.width(), h), grad)

    def _update_and_draw_sparkles(self, p: QtGui.QPainter, r: QtCore.QRectF, treble_v: float, dt: float) -> None:
        rate = int((6 + 80 * self._flashiness) * max(0.0, min(1.0, treble_v)))
        for _ in range(rate):
            if len(self._sparkles) < self._max_sparkles:
                self._sparkles.append({
                    "x": random.uniform(r.left() + 8.0, r.right() - 8.0),
                    "y": random.uniform(r.top() + 8.0, r.center().y() - 10.0),
                    "vy": random.uniform(20.0, 120.0) * (0.5 + treble_v),
                    "size": random.uniform(1.0, 2.6) * (0.7 + self._flashiness * 0.6),
                    "life": random.uniform(0.4, 1.0),
                    "age": 0.0
                })
        keep: List[dict] = []
        for s in self._sparkles:
            s["age"] += dt
            t = s["age"] / s["life"]
            if t >= 1.0:
                continue
            s["y"] -= s["vy"] * dt
            alpha = int(255 * (1.0 - t) * (0.6 + 0.4 * treble_v))
            col = QtGui.QColor(220, 240, 255, max(0, min(255, alpha)))
            sz = float(s["size"])
            p.fillRect(QtCore.QRectF(float(s["x"]) - sz * 0.5, float(s["y"]) - sz * 0.5, sz, sz), col)
            keep.append(s)
        self._sparkles = keep

    def _draw_scanline(self, p: QtGui.QPainter, r: QtCore.QRectF) -> None:
        x = r.left() + (self._scan_x % max(1.0, r.width()))
        w = 8.0 + 10.0 * self._flashiness
        grad = QtGui.QLinearGradient(QtCore.QPointF(x, r.top()), QtCore.QPointF(x + w, r.top()))
        grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, QtGui.QColor(255, 255, 255, 60))
        grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        p.fillRect(QtCore.QRectF(x, r.top(), w, r.height()), grad)

    def _draw_waveform(self, p: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        with self._lock:
            pcm = self._pcm
            sr = self._sr
        if pcm is None:
            return
        span = int(0.04 * sr)  # ±40 ms
        idx = int(self._time_sec() * sr)
        lo = max(0, idx - span)
        hi = min(pcm.size, idx + span)
        w = pcm[lo:hi]
        if w.size < 2:
            return

        width = int(rect.width())
        step = max(1, w.size // max(1, width))
        reduced = w[::step][:max(1, width)]
        mid = rect.center().y()
        amp = 0.40 * rect.height()

        path = QtGui.QPainterPath(QtCore.QPointF(rect.left(), mid - float(reduced[0]) * amp))
        for i, v in enumerate(reduced):
            x = rect.left() + i
            y = mid - float(v) * amp
            path.lineTo(x, y)

        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor(230, 255, 255, 190), 2))
        p.drawPath(path)
