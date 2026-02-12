# ===============================
# File: sound_visualizer.py
# ===============================
from __future__ import annotations

import base64
import zlib
import math
import random
from dataclasses import dataclass
from typing import Optional, Any, Dict, List, Tuple

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
    return x


def _lerp(a: float, b: float, t: float) -> float:
    t = _clamp01(t)
    return a + (b - a) * t


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
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))

    col = QtGui.QColor(r, g, b)
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


# ─────────────────────────────────────────────────────────────────────────────
# METAL VISUALIZER
# ─────────────────────────────────────────────────────────────────────────────
class AudioVisualizerUltra(QtWidgets.QWidget):
    """
    METAL MODE:
    - Strobe hits on beat (white flash + additive bloom)
    - Kick shockwaves (rings) + "slam" bar overdrive
    - Sparks/embers on hits (neon shards that decay)
    - Razor scanlines + vignette (stage lighting vibe)
    - Hue drift + per-bar arc (controlled)
    """

    # ---- tuning knobs (SAFE to tweak) ----
    STROBE_MAX_ALPHA = 165       # how hard the beat flash hits
    STROBE_DECAY = 0.82          # how fast strobe fades per tick
    ZAP_DECAY = 0.86             # streak decay
    SPARK_DECAY = 0.88           # particle decay per tick
    SPARK_SPAWN_BASE = 10        # particles on beat (base)
    SPARK_SPAWN_BASS = 18        # extra particles from bass
    SPARK_MAX = 120              # cap particles
    SHAKE_MAX_PX = 10            # screen shake on heavy hit
    SHAKE_DECAY = 0.84           # shake decay
    GLOW_MULT = 1.25             # bloom intensity multiplier
    BAR_ROUND = 3.2              # bar roundness
    BAR_OVERDRIVE = 0.28         # extra bar height on beat
    GRID_ALPHA = 14              # grid visibility

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._player = None
        self._audio_out = None

        self._active = False
        self._analysis = _AnalysisView()

        self._bars_n = 48
        self._bars = [0.0] * self._bars_n
        self._bars_peak = [0.0] * self._bars_n

        self._bass_pulse = 0.0
        self._beat_hold = 0.0

        # Stage state
        self._hue = 205.0
        self._hue_vel = 0.65
        self._hue_kick = 0.0

        self._strobe = 0.0
        self._zap = 0.0
        self._shake = 0.0
        self._spark_phase = 0.0

        # Sparks: (x, y, vx, vy, life, size, hue_off, hot)
        self._sparks: List[Tuple[float, float, float, float, float, float, float, float]] = []

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

        # Palette base
        self._bg = QtGui.QColor("#0b0d12")
        self._grid = QtGui.QColor(255, 255, 255, int(self.GRID_ALPHA))

        self._last_frame = -1
        self._vol = 1.0
        self._muted = False

    # ───────────────────────── public API ─────────────────────────
    def set_media_player(self, player) -> None:
        self._disconnect_audio_out()
        self._player = player

        # try QAudioOutput
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
        # visualizer does not decode audio itself; analysis payload drives it
        return

    def set_analysis_payload(self, payload: Dict[str, Any]) -> None:
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

        # Track identity -> different “stage hue”
        bassy = _clamp01(a.profile_bass_ratio)
        bright = _clamp01(a.profile_brightness)

        # bass-heavy -> hotter start, bright -> cooler start
        self._hue = (210.0 - 85.0 * bassy + 35.0 * bright) % 360.0
        self.update()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if not active:
            self._timer.stop()
            self._reset_motion()
            self.update()
        else:
            self._timer.start()

    def shutdown(self) -> None:
        self._timer.stop()
        self._disconnect_audio_out()

    # ───────────────────────── internal ─────────────────────────
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

    @staticmethod
    def _u8_at(buf: bytes, i: int) -> float:
        if not buf:
            return 0.0
        if i < 0:
            return 0.0
        if i >= len(buf):
            i = len(buf) - 1
        return float(buf[i]) / 255.0

    def _vol_eff(self) -> float:
        if self._muted:
            return 0.0
        v = self._vol
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return float(v)

    def _frame_index(self) -> int:
        if self._player is None:
            return -1
        if not self._analysis.ok():
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

    def _reset_motion(self) -> None:
        self._bars = [0.0] * self._bars_n
        self._bars_peak = [0.0] * self._bars_n
        self._bass_pulse = 0.0
        self._beat_hold = 0.0
        self._strobe = 0.0
        self._zap = 0.0
        self._shake = 0.0
        self._hue_kick = 0.0
        self._sparks.clear()

    def _spec_band(self, frame: int, band: int) -> float:
        a = self._analysis
        if a.nbands <= 0 or not a.spec:
            return 0.0
        band = max(0, min(band, a.nbands - 1))
        off = frame * a.nbands + band
        return self._u8_at(a.spec, off)

    # ───────────────────────── sparks ─────────────────────────
    def _spawn_sparks(self, rect: QtCore.QRectF, intensity: float, hue: float) -> None:
        """Spawn neon shards around the center/bottom (kick impact zone). intensity: 0..1"""
        if intensity <= 0.0:
            return

        cx = float(rect.center().x())
        bottom = float(rect.bottom())

        n = int(self.SPARK_SPAWN_BASE + self.SPARK_SPAWN_BASS * intensity)
        n = max(0, min(42, n))

        for _ in range(n):
            x = cx + random.uniform(-rect.width() * 0.22, rect.width() * 0.22)
            y = bottom - random.uniform(rect.height() * 0.10, rect.height() * 0.42)

            ang = random.uniform(-math.pi * 0.95, -math.pi * 0.05)  # mostly upward
            spd = random.uniform(2.2, 8.0) * (0.55 + 0.85 * intensity)
            vx = math.cos(ang) * spd + random.uniform(-0.6, 0.6)
            vy = math.sin(ang) * spd - random.uniform(0.2, 1.4)

            life = random.uniform(0.55, 1.0) * (0.55 + 0.60 * intensity)
            size = random.uniform(1.2, 3.6) * (0.85 + 0.85 * intensity)
            hue_off = random.uniform(-35.0, 55.0)
            hot = random.uniform(0.35, 1.0)

            self._sparks.append((x, y, vx, vy, life, size, hue_off, hot))

        if len(self._sparks) > self.SPARK_MAX:
            self._sparks = self._sparks[-self.SPARK_MAX :]

    def _tick_sparks(self) -> None:
        if not self._sparks:
            return
        out: List[Tuple[float, float, float, float, float, float, float, float]] = []
        for (x, y, vx, vy, life, size, hue_off, hot) in self._sparks:
            vx *= 0.985
            vy = vy * 0.985 + 0.18
            x += vx
            y += vy

            life *= self.SPARK_DECAY
            size *= 0.992

            if life > 0.045 and size > 0.35:
                out.append((x, y, vx, vy, life, size, hue_off, hot))
        self._sparks = out

    # ───────────────────────── core tick ─────────────────────────
    def _tick(self) -> None:
        if not self._active:
            return

        a = self._analysis
        idx = self._frame_index()
        if idx < 0:
            self._reset_motion()
            self.update()
            return

        vol = self._vol_eff()
        if vol <= 0.0:
            self._reset_motion()
            self.update()
            return

        lvl = self._u8_at(a.lvl, idx) * vol
        bass = self._u8_at(a.bass, idx) * vol
        mid = self._u8_at(a.mid, idx) * vol
        high = self._u8_at(a.high, idx) * vol
        beat = self._u8_at(a.beat, idx) * vol

        # Beat hold
        if beat > 0.5:
            self._beat_hold = 1.0
            self._strobe = 1.0
            self._zap = 1.0
            self._hue_kick = min(1.0, self._hue_kick + 0.75)

            hit = _clamp01((bass * 1.10 + lvl * 0.65) * 0.85)
            self._shake = max(self._shake, hit)
        else:
            self._beat_hold *= 0.88
            self._strobe *= self.STROBE_DECAY
            self._zap *= self.ZAP_DECAY
            self._hue_kick *= 0.90
            self._shake *= self.SHAKE_DECAY

        # Bass pulse
        pulse_target = min(1.0, bass * (0.55 + 0.75 * self._beat_hold))
        self._bass_pulse = (0.55 * self._bass_pulse + 0.45 * pulse_target)

        # Hue motion
        bright_bias = _clamp01(a.profile_brightness)
        bassy_bias = _clamp01(a.profile_bass_ratio)

        drift = self._hue_vel * (0.55 + 0.95 * bright_bias)
        kick = (18.0 + 48.0 * bassy_bias) * self._hue_kick
        self._hue = (self._hue + drift + kick * 0.06) % 360.0

        self._spark_phase = (self._spark_phase + 0.017 + 0.05 * self._beat_hold) % 1000.0

        # Bar build
        bass_bias = 0.55 + 0.70 * bassy_bias
        bright_w = 0.60 + 0.40 * bright_bias

        for bi in range(self._bars_n):
            if a.nbands > 0 and a.spec:
                t = bi * (a.nbands - 1) / max(1, (self._bars_n - 1))
                lo = int(t)
                hi_i = min(a.nbands - 1, lo + 1)
                frac = t - lo
                s0 = self._spec_band(idx, lo)
                s1 = self._spec_band(idx, hi_i)
                s = (1.0 - frac) * s0 + frac * s1
            else:
                x = bi / max(1, self._bars_n - 1)
                if x < 0.33:
                    s = bass
                elif x < 0.72:
                    s = mid
                else:
                    s = high

            x = bi / max(1, self._bars_n - 1)
            low_w = (1.15 - 0.65 * x) * bass_bias
            high_w = (0.55 + 0.85 * x) * bright_w

            slam = self.BAR_OVERDRIVE * self._beat_hold * (1.0 - 0.62 * x)

            shaped = (s ** 0.85) * (0.58 * low_w + 0.42 * high_w)
            shaped *= (0.40 + 0.95 * lvl)
            shaped *= (1.0 + 0.22 * self._beat_hold * (1.0 - 0.55 * x))
            shaped = _clamp01(shaped + slam)

            cur = self._bars[bi]
            if shaped > cur:
                cur = 0.52 * cur + 0.48 * shaped
            else:
                cur = 0.82 * cur + 0.18 * shaped
            self._bars[bi] = cur

            pk = self._bars_peak[bi]
            if cur > pk:
                pk = cur
            else:
                pk *= 0.965
            self._bars_peak[bi] = pk

        # Spawn sparks on strong beat hits (tied to bass + level)
        if beat > 0.5:
            intensity = _clamp01(bass * 1.2 + lvl * 0.55)
            # rect injected during paint; fallback spawn uses a guessed rect on widget
            rect = QtCore.QRectF(self.rect())
            self._spawn_sparks(rect, intensity, self._hue)

        self._tick_sparks()

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

        pulse = _clamp01(self._bass_pulse)
        beat = _clamp01(self._beat_hold)
        strobe = _clamp01(self._strobe)
        zap = _clamp01(self._zap)
        shake = _clamp01(self._shake)

        # Screen shake
        if shake > 0.01:
            mag = self.SHAKE_MAX_PX * shake
            ox = random.uniform(-mag, mag)
            oy = random.uniform(-mag, mag)
            p.translate(ox, oy)

        # Background base
        p.fillRect(r, self._bg)

        cx = r.center().x()
        cy = r.center().y()
        rad = max(r.width(), r.height()) * (0.55 + 0.30 * pulse)

        # Reactive aura
        aura = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), float(rad))
        aura.setColorAt(0.0, _hsv(self._hue + 12.0, 0.95, 0.28 + 0.32 * beat, 110 + int(90 * beat)))
        aura.setColorAt(0.52, _hsv(self._hue + 90.0, 0.85, 0.20 + 0.12 * pulse, 50 + int(70 * pulse)))
        aura.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QBrush(aura))
        p.drawRect(r)

        # Vignette
        vig = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), float(rad * 0.85))
        vig.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
        vig.setColorAt(0.75, QtGui.QColor(0, 0, 0, 55))
        vig.setColorAt(1.0, QtGui.QColor(0, 0, 0, 140))
        p.setBrush(QtGui.QBrush(vig))
        p.drawRect(r)

        # Grid
        self._grid.setAlpha(int(self.GRID_ALPHA))
        p.setPen(QtGui.QPen(self._grid, 1))
        step = max(24, int(min(r.width(), r.height()) * 0.06))
        for x in range(r.left() + step, r.right(), step):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top() + step, r.bottom(), step):
            p.drawLine(r.left(), y, r.right(), y)

        # Razor scanlines
        scan_step = max(18, step - 6)
        scan_alpha = 8 + int(22 * (0.35 + 0.65 * beat))
        scan_col = _hsv(self._hue + 180.0, 0.55, 0.55, scan_alpha)
        p.setPen(QtGui.QPen(scan_col, 1))
        y = r.top() + int((self._spark_phase * 150.0) % max(1, scan_step))
        while y < r.bottom():
            p.drawLine(r.left(), y, r.right(), y)
            y += scan_step

        # If inactive or no analysis: keep it as stage-only
        if not self._active or not self._analysis.ok():
            p.end()
            return

        # Bars region
        pad = max(10, int(min(r.width(), r.height()) * 0.06))
        area = QtCore.QRectF(
            float(r.left() + pad),
            float(r.top() + pad),
            float(max(1, r.width() - pad * 2)),
            float(max(1, r.height() - pad * 2)),
        )

        # Kick shockwaves (rings)
        if pulse > 0.04:
            ring_n = 2 + (1 if beat > 0.60 else 0)
            for k in range(ring_n):
                t = (k / max(1, ring_n - 1)) if ring_n > 1 else 0.0
                rr = (0.22 + 0.62 * t) * min(area.width(), area.height()) * (0.55 + 0.60 * pulse)
                ring_alpha = int(85 * (1.0 - t) * (0.30 + 0.70 * beat))
                ring_col = _hsv(self._hue + 40.0 + 120.0 * t, 0.95, 0.95, ring_alpha)
                p.setPen(QtGui.QPen(ring_col, max(1.0, 2.0 + 3.0 * pulse)))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)

        # Beat “zap” streaks
        if zap > 0.02:
            streaks = 2 + (1 if beat > 0.80 else 0)
            for i in range(streaks):
                ang = (self._hue * 0.6 + i * 120.0 + self._spark_phase * 30.0) % 360.0
                aa = math.radians(ang)
                length = 0.78 * min(area.width(), area.height())
                x1 = cx - math.cos(aa) * length * 0.5
                y1 = cy - math.sin(aa) * length * 0.5
                x2 = cx + math.cos(aa) * length * 0.5
                y2 = cy + math.sin(aa) * length * 0.5
                col = _hsv(self._hue + i * 90.0, 0.30 + 0.70 * beat, 1.0, int(80 + 160 * zap))
                p.setPen(QtGui.QPen(col, 2.2 + 3.8 * zap, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

        # Bar layout
        n = self._bars_n
        gap = max(3, int(area.width() * 0.004))
        total_gap = gap * (n + 1)
        bar_w = max(4, (int(area.width()) - total_gap) // n)

        base_y = int(area.bottom() - max(8, area.height() * 0.10))
        max_h = int(area.height() * 0.72)

        # ── BLOOM PASS (additive) ───────────────────────────────────
        p.save()
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
        x = area.left() + gap
        for i in range(n):
            v = self._bars[i]
            if v <= 0.002:
                x += bar_w + gap
                continue

            h = int(max(2, max_h * v))
            rect = QtCore.QRectF(x - 2.0, base_y - h - 2.0, bar_w + 4.0, h + 4.0)

            hue_i = (self._hue + (i / max(1, n - 1)) * 150.0 + 20.0 * beat) % 360.0
            glow = QtGui.QLinearGradient(0, base_y - max_h, 0, base_y)
            glow.setColorAt(0.0, _hsv(hue_i, 0.98, 1.0, int((70 + 140 * beat) * self.GLOW_MULT)))
            glow.setColorAt(1.0, _hsv(hue_i + 45.0, 0.75, 0.75, int((24 + 90 * pulse) * self.GLOW_MULT)))

            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QBrush(glow))
            p.drawRoundedRect(rect, self.BAR_ROUND + 1.6, self.BAR_ROUND + 1.6)

            x += bar_w + gap
        p.restore()

        # ── BODY PASS (clean neon bars) ─────────────────────────────
        x = area.left() + gap
        for i in range(n):
            v = self._bars[i]
            if v <= 0.002:
                x += bar_w + gap
                continue

            h = int(max(2, max_h * v))
            rect = QtCore.QRectF(x, base_y - h, bar_w, h)

            hue_i = (self._hue + (i / max(1, n - 1)) * 150.0 + 10.0 * beat) % 360.0
            grad = QtGui.QLinearGradient(0, base_y - max_h, 0, base_y)
            grad.setColorAt(0.0, _hsv(hue_i, 0.85, 0.98, 230))
            grad.setColorAt(0.55, _hsv(hue_i + 18.0, 0.80, 0.86, 210))
            grad.setColorAt(1.0, _hsv(hue_i + 42.0, 0.70, 0.62, 190))

            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 22), 1))
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(rect, self.BAR_ROUND, self.BAR_ROUND)

            # Peak line (razor cap)
            pk = self._bars_peak[i]
            if pk > 0.02:
                ph = int(max(2, max_h * pk))
                ypk = base_y - ph
                peak_col = _hsv(hue_i + 22.0, 0.35, 1.0, 160)
                p.setPen(QtGui.QPen(peak_col, 2))
                p.drawLine(QtCore.QPointF(x + 1.0, ypk), QtCore.QPointF(x + bar_w - 1.0, ypk))

            x += bar_w + gap

        # Sparks (embers)
        if self._sparks:
            for (sx, sy, _vx, _vy, life, size, hue_off, hot) in self._sparks:
                a = int(255.0 * _clamp01(life))
                if a <= 2:
                    continue

                hue_s = (self._hue + hue_off) % 360.0
                core = _hsv(hue_s, 0.22 + 0.48 * hot, 1.0, min(255, int(a * (0.65 + 0.35 * hot))))
                rim = _hsv(hue_s + 18.0, 0.95, 0.95, min(255, int(a * 0.65)))

                # soft glow
                p.save()
                p.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                p.setPen(Qt.NoPen)
                p.setBrush(core)
                p.drawEllipse(QtCore.QPointF(sx, sy), size * 2.3, size * 2.3)
                p.restore()

                # hot shard
                p.setPen(QtGui.QPen(rim, max(1.0, 1.2 + 1.6 * hot), Qt.SolidLine, Qt.RoundCap))
                p.drawLine(
                    QtCore.QPointF(sx - size * 0.9, sy - size * 0.2),
                    QtCore.QPointF(sx + size * 0.9, sy + size * 0.2),
                )

        # Strobe flash (white)
        if strobe > 0.01:
            alpha = int(self.STROBE_MAX_ALPHA * strobe)
            p.fillRect(r, QtGui.QColor(255, 255, 255, alpha))

        p.end()
