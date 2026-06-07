# ===============================
# File: sound_visualizer.py
# ===============================
from __future__ import annotations

import base64
import zlib
import math
from dataclasses import dataclass
from typing import Optional, Any, Dict, List

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _b64z_unpack_u8(s: str) -> bytes:
    comp = base64.b64decode(s.encode("ascii"))
    return zlib.decompress(comp)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * _clamp01(t)


def _hsv(h: float, s: float, v: float, a: int = 255) -> QtGui.QColor:
    """h: 0..360, s/v: 0..1"""
    h = float(h) % 360.0
    s = _clamp01(float(s))
    v = _clamp01(float(v))

    c = v * s
    x = c * (1.0 - abs(((h / 60.0) % 2.0) - 1.0))
    m = v - c

    rp = gp = bp = 0.0
    if 0.0 <= h < 60.0:
        rp, gp, bp = c, x, 0.0
    elif 60.0 <= h < 120.0:
        rp, gp, bp = x, c, 0.0
    elif 120.0 <= h < 180.0:
        rp, gp, bp = 0.0, c, x
    elif 180.0 <= h < 240.0:
        rp, gp, bp = 0.0, x, c
    elif 240.0 <= h < 300.0:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x

    r = int(round((rp + m) * 255.0))
    g = int(round((gp + m) * 255.0))
    b = int(round((bp + m) * 255.0))
    col = QtGui.QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    col.setAlpha(max(0, min(255, int(a))))
    return col


# ─────────────────────────────────────────────────────────────────────────────
# Analysis view
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _AnalysisView:
    hop_ms: int = 20
    frames: int = 0
    nbands: int = 0

    lvl: bytes = b""
    bass: bytes = b""
    mid: bytes = b""
    high: bytes = b""
    beat: bytes = b""
    spec: bytes = b""

    profile_bass_ratio: float = 0.0
    profile_brightness: float = 0.0

    def ok(self) -> bool:
        return self.frames > 0 and self.hop_ms > 0 and len(self.lvl) >= self.frames

    def has_spectrum(self) -> bool:
        return self.ok() and self.nbands > 0 and len(self.spec) >= self.frames * self.nbands


# ─────────────────────────────────────────────────────────────────────────────
# Realistic spectrum visualizer
# ─────────────────────────────────────────────────────────────────────────────
class AudioVisualizerUltra(QtWidgets.QWidget):
    """
    Realistic analyzer-driven preview.

    This intentionally avoids fake motion effects:
    - no screen shake
    - no random particles
    - no white strobe
    - no decorative beat explosions
    - no motion when analysis is missing

    Motion comes from cached analysis arrays only:
    - q.spec  -> bar heights by frequency band
    - q.lvl   -> global loudness/envelope
    - q.bass  -> low-frequency body pulse
    - q.mid   -> vocal/instrument density
    - q.high  -> brightness/cymbal presence
    """

    # Visual honesty / smoothing knobs
    BARS = 48
    ATTACK = 0.30              # higher = faster rise
    RELEASE = 0.105            # higher = faster fall
    PEAK_RELEASE = 0.982       # peak marker decay
    NOISE_FLOOR = 0.020        # hide tiny quantization noise
    MIN_VISIBLE = 0.012
    MAX_BAR_FILL = 0.76        # prevents bars from slamming into top constantly
    GRID_ALPHA = 12
    GLOW_ALPHA = 34
    BODY_ALPHA = 210
    PEAK_ALPHA = 120

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._player = None
        self._audio_out = None
        self._active = False

        self._analysis = _AnalysisView()
        self._bars_n = int(self.BARS)
        self._bars: List[float] = [0.0] * self._bars_n
        self._peaks: List[float] = [0.0] * self._bars_n

        self._lvl_s = 0.0
        self._bass_s = 0.0
        self._mid_s = 0.0
        self._high_s = 0.0
        self._beat_s = 0.0

        self._hue = 204.0
        self._vol = 1.0
        self._muted = False
        self._last_frame = -1

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

        self._bg = QtGui.QColor("#0b0d12")
        self._grid = QtGui.QColor(255, 255, 255, self.GRID_ALPHA)
        self._text = QtGui.QColor(185, 195, 210, 130)

    # ───────────────────────── public API ─────────────────────────
    def set_media_player(self, player) -> None:
        self._disconnect_audio_out()
        self._player = player
        self._audio_out = None

        if player is None:
            self._reset_motion()
            self.update()
            return

        try:
            ao_fn = getattr(player, "audioOutput", None)
            ao = ao_fn() if callable(ao_fn) else None
            if ao is not None:
                self._audio_out = ao
                try:
                    self._vol = float(ao.volume())
                except Exception:
                    self._vol = 1.0
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
        except Exception:
            self._audio_out = None

    def set_audio_file(self, _path: str) -> None:
        # The visualizer does not decode audio. The cached payload drives motion.
        self._last_frame = -1
        self._reset_motion()
        self.update()

    def set_analysis_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        if not payload:
            self._analysis = _AnalysisView()
            self._last_frame = -1
            self._reset_motion()
            self.update()
            return

        a = _AnalysisView()
        try:
            a.hop_ms = int(payload.get("hop_ms", 20))
            a.frames = int(payload.get("frames", 0))
            a.nbands = int(payload.get("nbands", 0))

            q = payload.get("q", {}) or {}
            a.lvl = _b64z_unpack_u8(q.get("lvl", "")) if q.get("lvl") else b""
            a.bass = _b64z_unpack_u8(q.get("bass", "")) if q.get("bass") else b""
            a.mid = _b64z_unpack_u8(q.get("mid", "")) if q.get("mid") else b""
            a.high = _b64z_unpack_u8(q.get("high", "")) if q.get("high") else b""
            a.beat = _b64z_unpack_u8(q.get("beat", "")) if q.get("beat") else b""
            a.spec = _b64z_unpack_u8(q.get("spec", "")) if q.get("spec") else b""

            prof = payload.get("profile", {}) or {}
            a.profile_bass_ratio = float(prof.get("bass_ratio", 0.0))
            a.profile_brightness = float(prof.get("bright_mean", 0.0))
        except Exception:
            a = _AnalysisView()

        self._analysis = a
        self._last_frame = -1
        self._reset_motion()

        # Stable track color. Bass-heavy songs lean warmer; bright songs lean cooler.
        bassy = _clamp01(a.profile_bass_ratio)
        bright = _clamp01(a.profile_brightness)
        self._hue = (205.0 - 38.0 * bassy + 22.0 * bright) % 360.0
        self.update()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._reset_motion()
            self.update()

    def shutdown(self) -> None:
        self._timer.stop()
        self._disconnect_audio_out()
        self._player = None
        self._reset_motion()

    # ───────────────────────── audio output wiring ─────────────────────────
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

    def _on_volume_changed(self, v) -> None:
        try:
            self._vol = float(v)
        except Exception:
            self._vol = 1.0

    def _on_muted_changed(self, m) -> None:
        try:
            self._muted = bool(m)
        except Exception:
            self._muted = False

    def _vol_eff(self) -> float:
        # Mute does not pause playback and should not kill the visualizer.
        # Volume is still respected so a true 0% setting can quiet the preview.
        return _clamp01(float(self._vol))

    # ───────────────────────── analysis sampling ─────────────────────────
    @staticmethod
    def _u8_at(buf: bytes, i: int) -> float:
        if not buf:
            return 0.0
        if i < 0:
            i = 0
        elif i >= len(buf):
            i = len(buf) - 1
        return float(buf[i]) / 255.0

    def _frame_index(self) -> int:
        if self._player is None or not self._analysis.ok():
            return -1
        try:
            pos = int(self._player.position())
        except Exception:
            return -1
        idx = int(pos // max(1, self._analysis.hop_ms))
        if idx < 0:
            return 0
        if idx >= self._analysis.frames:
            return self._analysis.frames - 1
        return idx

    def _spec_band(self, frame: int, band: int) -> float:
        a = self._analysis
        if not a.has_spectrum():
            return 0.0
        band = max(0, min(int(band), a.nbands - 1))
        off = frame * a.nbands + band
        return self._u8_at(a.spec, off)

    def _sample_spectrum(self, frame: int, bar: int) -> float:
        a = self._analysis
        if a.has_spectrum():
            # Analysis bands are log-spaced already. Linear interpolation across
            # those log bands gives stable musical movement without inventing motion.
            t = bar * (a.nbands - 1) / max(1, (self._bars_n - 1))
            lo = int(t)
            hi = min(a.nbands - 1, lo + 1)
            frac = t - lo
            s0 = self._spec_band(frame, lo)
            s1 = self._spec_band(frame, hi)
            return (1.0 - frac) * s0 + frac * s1

        # Envelope-only cache fallback. This is less accurate than spectrum data,
        # but it still follows the real loudness envelope instead of fake motion.
        lvl = self._u8_at(a.lvl, frame)
        return lvl

    def _reset_motion(self) -> None:
        self._bars = [0.0] * self._bars_n
        self._peaks = [0.0] * self._bars_n
        self._lvl_s = 0.0
        self._bass_s = 0.0
        self._mid_s = 0.0
        self._high_s = 0.0
        self._beat_s = 0.0

    # ───────────────────────── core tick ─────────────────────────
    def _tick(self) -> None:
        if not self._active:
            return

        idx = self._frame_index()
        if idx < 0:
            # Analysis missing. Do not fake an audio response.
            self._reset_motion()
            self.update()
            return

        vol = self._vol_eff()
        if vol <= 0.0:
            self._reset_motion()
            self.update()
            return

        a = self._analysis
        lvl = self._u8_at(a.lvl, idx)
        bass = self._u8_at(a.bass, idx) if a.bass else lvl
        mid = self._u8_at(a.mid, idx) if a.mid else lvl
        high = self._u8_at(a.high, idx) if a.high else lvl
        beat = self._u8_at(a.beat, idx) if a.beat else 0.0

        # Compress extremes. This keeps soft songs soft and loud songs large
        # without turning every beat into a full-height spike.
        lvl_c = _clamp01(math.pow(max(0.0, lvl), 1.18))
        bass_c = _clamp01(math.pow(max(0.0, bass), 1.12))
        mid_c = _clamp01(math.pow(max(0.0, mid), 1.14))
        high_c = _clamp01(math.pow(max(0.0, high), 1.10))
        beat_c = _clamp01(beat * 0.45)

        # Smooth global meters.
        self._lvl_s = _lerp(self._lvl_s, lvl_c, 0.22 if lvl_c > self._lvl_s else 0.08)
        self._bass_s = _lerp(self._bass_s, bass_c, 0.24 if bass_c > self._bass_s else 0.09)
        self._mid_s = _lerp(self._mid_s, mid_c, 0.22 if mid_c > self._mid_s else 0.08)
        self._high_s = _lerp(self._high_s, high_c, 0.26 if high_c > self._high_s else 0.12)
        self._beat_s = _lerp(self._beat_s, beat_c, 0.20 if beat_c > self._beat_s else 0.07)

        envelope = 0.18 + 0.82 * self._lvl_s

        for bi in range(self._bars_n):
            x = bi / max(1, self._bars_n - 1)
            raw = self._sample_spectrum(idx, bi)

            if raw < self.NOISE_FLOOR:
                target = 0.0
            else:
                # Keep values proportional. The tiny band curve compensates for
                # perceptual display balance without overriding the actual spectrum.
                band_balance = 0.94 + 0.08 * math.sin(math.pi * x)
                target = math.pow(raw, 1.22) * envelope * band_balance

                # Very small low-end body, not an explosion.
                if x < 0.18:
                    target *= 1.0 + 0.08 * self._bass_s
                elif x > 0.78:
                    target *= 0.96 + 0.08 * self._high_s

            target *= vol
            target = _clamp01(target * self.MAX_BAR_FILL)

            cur = self._bars[bi]
            t = self.ATTACK if target > cur else self.RELEASE
            cur = _lerp(cur, target, t)
            if cur < self.MIN_VISIBLE:
                cur = 0.0
            self._bars[bi] = cur

            pk = self._peaks[bi]
            if cur > pk:
                pk = cur
            else:
                pk *= self.PEAK_RELEASE
                if pk < self.MIN_VISIBLE:
                    pk = 0.0
            self._peaks[bi] = pk

        # Slow, subtle hue drift tied to actual brightness. No beat-jump colors.
        self._hue = (self._hue + 0.025 + 0.035 * self._high_s) % 360.0

        if idx != self._last_frame:
            self._last_frame = idx
        self.update()

    # ───────────────────────── drawing ─────────────────────────
    def paintEvent(self, _ev: QtGui.QPaintEvent) -> None:
        r = self.rect()
        if not r.isValid():
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

        p.fillRect(r, self._bg)

        cx = r.center().x()
        cy = r.center().y()
        rad = max(r.width(), r.height()) * 0.62

        # Subtle audio presence glow. Small enough to avoid lying about motion.
        glow_power = _clamp01(0.10 + 0.18 * self._lvl_s + 0.09 * self._bass_s + 0.06 * self._high_s)
        aura = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), float(rad))
        aura.setColorAt(0.0, _hsv(self._hue, 0.72, 0.22 + glow_power, 40 + int(48 * glow_power)))
        aura.setColorAt(0.55, _hsv(self._hue + 36.0, 0.58, 0.13 + glow_power * 0.5, 26 + int(28 * glow_power)))
        aura.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QBrush(aura))
        p.drawRect(r)

        # Grid. It is decorative but static.
        p.setPen(QtGui.QPen(self._grid, 1))
        step = max(24, int(min(r.width(), r.height()) * 0.06))
        for x in range(r.left() + step, r.right(), step):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top() + step, r.bottom(), step):
            p.drawLine(r.left(), y, r.right(), y)

        pad = max(10, int(min(r.width(), r.height()) * 0.06))
        area = QtCore.QRectF(
            float(r.left() + pad),
            float(r.top() + pad),
            float(max(1, r.width() - pad * 2)),
            float(max(1, r.height() - pad * 2)),
        )

        # If active but cache is missing, tell the truth visually instead of faking.
        if not self._analysis.ok():
            if self._active:
                p.setPen(self._text)
                font = QtGui.QFont("Segoe UI", 9)
                font.setWeight(QtGui.QFont.Medium)
                p.setFont(font)
                p.drawText(r, Qt.AlignCenter, "Analysis cache unavailable")
            p.end()
            return

        # Baseline.
        base_y = int(area.bottom() - max(8, area.height() * 0.10))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 24), 1))
        p.drawLine(QtCore.QPointF(area.left(), base_y), QtCore.QPointF(area.right(), base_y))

        n = self._bars_n
        gap = max(3, int(area.width() * 0.0045))
        total_gap = gap * (n + 1)
        bar_w = max(4, (int(area.width()) - total_gap) // n)
        max_h = int(area.height() * 0.70)

        # Glow pass: very restrained.
        p.save()
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
        x = area.left() + gap
        for i, v in enumerate(self._bars):
            if v <= 0.0:
                x += bar_w + gap
                continue
            h = int(max(2, max_h * v))
            hue_i = (self._hue + (i / max(1, n - 1)) * 82.0) % 360.0
            col = _hsv(hue_i, 0.70, 0.80, int(self.GLOW_ALPHA * (0.35 + 0.65 * v)))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QtCore.QRectF(x - 1.5, base_y - h - 1.5, bar_w + 3.0, h + 3.0), 3.0, 3.0)
            x += bar_w + gap
        p.restore()

        # Body pass: the actual spectrum bars.
        x = area.left() + gap
        for i, v in enumerate(self._bars):
            if v <= 0.0:
                x += bar_w + gap
                continue

            h = int(max(2, max_h * v))
            rect = QtCore.QRectF(x, base_y - h, bar_w, h)
            hue_i = (self._hue + (i / max(1, n - 1)) * 82.0) % 360.0

            grad = QtGui.QLinearGradient(0, base_y - max_h, 0, base_y)
            grad.setColorAt(0.0, _hsv(hue_i, 0.62, 0.92, self.BODY_ALPHA))
            grad.setColorAt(1.0, _hsv(hue_i + 18.0, 0.54, 0.60, 178))

            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 18), 1))
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(rect, 2.8, 2.8)

            pk = self._peaks[i]
            if pk > 0.0:
                ph = int(max(2, max_h * pk))
                ypk = base_y - ph
                peak_col = _hsv(hue_i + 10.0, 0.28, 0.94, self.PEAK_ALPHA)
                p.setPen(QtGui.QPen(peak_col, 1.4))
                p.drawLine(QtCore.QPointF(x + 0.8, ypk), QtCore.QPointF(x + bar_w - 0.8, ypk))

            x += bar_w + gap

        # Small honest meter labels: visual debugging without clutter.
        meter_text = f"L {self._lvl_s:0.2f}   B {self._bass_s:0.2f}   M {self._mid_s:0.2f}   H {self._high_s:0.2f}"
        font = QtGui.QFont("Consolas", 8)
        p.setFont(font)
        p.setPen(QtGui.QColor(200, 210, 220, 72))
        p.drawText(QtCore.QRectF(area.left(), area.top(), area.width(), 18), Qt.AlignRight | Qt.AlignVCenter, meter_text)

        p.end()
