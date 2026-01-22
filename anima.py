# anima.py — High-quality animation toolkit for LetterSmith
# ---------------------------------------------------------------------------
# Exposes:
#   - ParticleBurst          : spark + shockwave overlay for click celebrations
#   - AnimatedPrompterButton : large icon button (pulse + glow + fireworks)
#   - TabSwitcher            : slide+fade transitions for QStackedWidget pages
#   - install_click_fx       : global hover glow + press pulse for all buttons
#   - set_excitement_level   : set global excitement (e.g., 55555)
#   - get_excitement_level   : read current global excitement
#
# Design goals:
#   • Zero circular imports
#   • Python 3.7+ compatible (no PEP 604 unions)
#   • Layout-stable animations (no geometry jank)
#   • Guarded against GC of animations/effects
#   • DPI-aware rendering and sensible defaults
#   • Small, clean public surface; configurable via FX constants
# ---------------------------------------------------------------------------

from __future__ import annotations

import math
import random
from typing import Optional, List, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import (
    Qt, QObject, QEvent, QEasingCurve, QPropertyAnimation, QPoint, QSize, QRect, QTimer
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QGraphicsDropShadowEffect
)

__all__ = [
    "ParticleBurst",
    "AnimatedPrompterButton",
    "TabSwitcher",
    "install_click_fx",
    "set_excitement_level",
    "get_excitement_level",
    "FX",
    "VERSION",
]

VERSION = "1.3.0-pro"


# ---------------------------------------------------------------------------
# Centralized FX configuration (tweak without touching logic)
# ---------------------------------------------------------------------------
class FX:
    class DURATIONS:
        PRESS_PULSE_MS = 140
        PWRITE_PULSE_UP_MS = 210
        PWRITE_PULSE_DOWN_MS = 160
        PWRITE_GLOW_SHIFT_MS = 150
        TAB_SWITCH_MS = 220
        SPARK_TICK_MS = 16  # ~60 FPS
        RING_TICK_MS = 16
        FLASH_FADE_MS = 220
        BURST_WAVE_INTERVAL_MS = 28

    class EASING:
        PRESS = QEasingCurve.InOutSine
        PULSE_UP = QEasingCurve.OutBack
        PULSE_DOWN = QEasingCurve.InOutQuad
        GLOW = QEasingCurve.OutCubic
        TAB_FADE = QEasingCurve.InOutSine
        TAB_SLIDE = QEasingCurve.OutCubic

    class GLOW:
        BLUR_RADIUS = 28
        REST_ALPHA = 110
        HOVER_ALPHA = 170
        COLOR = QtGui.QColor(0, 190, 255)  # alpha applied dynamically

    class HOVER_GLOW:
        # global hover glow for all buttons via install_click_fx
        BLUR_RADIUS = 20
        REST_ALPHA = 90
        HOVER_ALPHA = 160
        COLOR = QtGui.QColor(0, 200, 255)  # alpha applied dynamically

    class SPARKS:
        COUNT_BASE = 22           # nominal particle count per burst (scale up by excitement)
        SPEED_MIN = 2.5
        SPEED_MAX = 4.0
        LIFE_MIN_MS = 220
        LIFE_MAX_MS = 320
        RADIUS_MIN = 1.4
        RADIUS_MAX = 2.6
        MAX_PARTICLES_PER_BURST = 320  # hard cap for perf
        MAX_WAVES = 4
        # Cyan-to-white sparkle palette; alpha fades via age
        BASE_COLORS = [
            QtGui.QColor(150, 240, 255),  # pale cyan
            QtGui.QColor(175, 245, 255),
            QtGui.QColor(200, 250, 255),
            QtGui.QColor(220, 255, 255),  # near white
        ]

    class RING:
        ENABLED = True
        LIFE_MS = 420
        WIDTH = 2.0
        COLOR = QtGui.QColor(120, 235, 255)
        # radii will scale with excitement level; these are minimums
        RADIUS_START = 8
        RADIUS_END_BASE = 58

    class FLASH:
        ENABLED = True
        ALPHA_MAX = 210
        ALPHA_BASE = 80  # base flash even at low excitement

    class TAB:
        OFFSET_PX = 28  # slide-in horizontal offset

    class PWRITE:
        # Peak icon scale during pulse (icon only; widget size fixed)
        PEAK_SCALE = 1.14

    class EXCITEMENT:
        DEFAULT = 10
        # Safety caps—excitement maps logarithmically but we still cap outputs
        MAX_PARTICLES = 320
        MAX_RINGS = 3


# ---------------------------------------------------------------------------
# Global excitement control
# ---------------------------------------------------------------------------
_GLOBAL_EXCITEMENT = FX.EXCITEMENT.DEFAULT

def set_excitement_level(level: int) -> None:
    """Set global excitement (any positive int; extreme values are tamed internally)."""
    global _GLOBAL_EXCITEMENT
    _GLOBAL_EXCITEMENT = max(0, int(level or 0))

def get_excitement_level() -> int:
    return _GLOBAL_EXCITEMENT


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _safe_stop(anim: Optional[QPropertyAnimation]) -> None:
    if anim and anim.state() == QPropertyAnimation.Running:
        anim.stop()

def _dpi_round(w: QtWidgets.QWidget, value: float) -> int:
    """Round a pixel value considering device pixel ratio (for crispness)."""
    try:
        ratio = w.devicePixelRatioF()
    except Exception:
        ratio = 1.0
    return int(round(value * ratio) / max(ratio, 1.0))

def _excitement_scale(level: int) -> float:
    set_excitement_level(55555)

    """
    
    Map arbitrary level (0..∞) to a gentle multiplier:
      level=0  -> 0.0
      level=10 -> ~1.28
      level=100 -> ~2.04
      level=1000 -> ~3.0
      level=55555 -> ~4.74
    """
    if level <= 0:
        return 0.0
    return max(0.0, math.log10(level + 9.0))

def _particle_count_for_level(level: int) -> int:
    scale = _excitement_scale(level)
    count = int(FX.SPARKS.COUNT_BASE * scale) if scale > 0 else 0
    return max(0, min(count, FX.EXCITEMENT.MAX_PARTICLES))

def _ring_count_for_level(level: int) -> int:
    if not FX.RING.ENABLED:
        return 0
    scale = _excitement_scale(level)
    # 0 at 0, 1 near 10, up to 3 near high levels
    rings = int(min(FX.EXCITEMENT.MAX_RINGS, max(0, round(scale))))
    return rings

def _ring_end_radius_for_level(level: int) -> int:
    scale = _excitement_scale(level)
    return int(FX.RING.RADIUS_END_BASE + 22 * scale)

def _flash_alpha_for_level(level: int) -> int:
    if not FX.FLASH.ENABLED:
        return 0
    scale = _excitement_scale(level)
    a = int(FX.FLASH.ALPHA_BASE + 30 * scale)
    return max(0, min(a, FX.FLASH.ALPHA_MAX))


# ---------------------------------------------------------------------------
# ParticleBurst — lightweight, DPI-aware sparkle + ring + flash overlay
# ---------------------------------------------------------------------------
class ParticleBurst(QtWidgets.QWidget):




    """
    Transparent overlay drawing short-lived spark particles + optional shockwave rings + flash.

    Usage:
        overlay = ParticleBurst(parent_container)
        overlay.burst_at(widget.mapTo(overlay, widget.rect().center()), excitement=55555)
    """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._particles: List[Dict[str, Any]] = []
        self._rings: List[Dict[str, Any]] = []
        self._flash_alpha: int = 0

        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(min(FX.DURATIONS.SPARK_TICK_MS, FX.DURATIONS.RING_TICK_MS))
        self._timer.timeout.connect(self._tick)
        self.hide()

        # For large excitement, we can emit in waves to avoid a single huge frame
        self._wave_queue: List[Dict[str, Any]] = []
        self._wave_emitter: Optional[QTimer] = None

    # Ensure overlay always covers its parent
    def resizeEvent(self, _: QtGui.QResizeEvent) -> None:
        if self.parent():
            self.setGeometry(self.parent().rect())

    # Public API
    def burst_at(self,
                 center: QPoint,
                 count: Optional[int] = None,
                 excitement: Optional[int] = None) -> None:
        """
        Spawn effects centered at `center` in overlay coords.
        If `excitement` is None, uses global excitement.
        """
        level = _GLOBAL_EXCITEMENT if excitement is None else max(0, int(excitement))
        # PARTICLES
        want = _particle_count_for_level(level) if count is None else int(max(0, count))
        # guard: if user passes enormous `count`, cap anyway
        want = min(want, FX.SPARKS.MAX_PARTICLES_PER_BURST)

        self._emit_particles(center, want, waves=True)

        # RINGS
        rings = _ring_count_for_level(level)
        if rings > 0:
            for i in range(rings):
                life = FX.RING.LIFE_MS
                radius_end = _ring_end_radius_for_level(level) + i * 10
                self._rings.append({
                    "cx": float(center.x()),
                    "cy": float(center.y()),
                    "age": 0.0,
                    "life": float(life),
                    "r0": float(FX.RING.RADIUS_START + i * 2),
                    "r1": float(radius_end),
                    "width": float(FX.RING.WIDTH),
                    "color": QtGui.QColor(FX.RING.COLOR),
                })

        # FLASH
        self._flash_alpha = max(self._flash_alpha, _flash_alpha_for_level(level))

        # Start engines
        self._timer.start()
        self.update()
        self.show()
        self.raise_()

    # Internal: emit in waves to smooth frame time
    def _emit_particles(self, center: QPoint, total: int, waves: bool) -> None:
        if total <= 0:
            return
        max_per_wave = max(1, int(FX.SPARKS.MAX_PARTICLES_PER_BURST / max(1, FX.SPARKS.MAX_WAVES)))
        if waves and total > max_per_wave:
            remaining = total
            self._wave_queue.clear()
            while remaining > 0:
                n = min(max_per_wave, remaining)
                self._wave_queue.append({"center": QPoint(center), "count": n})
                remaining -= n
            if not self._wave_emitter:
                self._wave_emitter = QTimer(self)
                self._wave_emitter.setInterval(FX.DURATIONS.BURST_WAVE_INTERVAL_MS)
                self._wave_emitter.timeout.connect(self._emit_next_wave)
            self._wave_emitter.start()
        else:
            self._spawn_particles(center, total)

    def _emit_next_wave(self) -> None:
        if not self._wave_queue:
            if self._wave_emitter:
                self._wave_emitter.stop()
            return
        wave = self._wave_queue.pop(0)
        self._spawn_particles(wave["center"], wave["count"])
        self.update()

    def _spawn_particles(self, center: QPoint, n: int) -> None:
        for _ in range(max(1, int(n))):
            ang = random.uniform(0.0, 360.0)
            spd = random.uniform(FX.SPARKS.SPEED_MIN, FX.SPARKS.SPEED_MAX)
            life = random.uniform(FX.SPARKS.LIFE_MIN_MS, FX.SPARKS.LIFE_MAX_MS)
            base_color = random.choice(FX.SPARKS.BASE_COLORS)
            color = QtGui.QColor(base_color)
            color.setAlpha(random.randint(160, 220))
            self._particles.append({
                "x": float(center.x()),
                "y": float(center.y()),
                "angle": ang,
                "speed": spd,
                "age": 0.0,
                "life": life,
                "color": color,
                "radius": random.uniform(FX.SPARKS.RADIUS_MIN, FX.SPARKS.RADIUS_MAX),
            })

    def _tick(self) -> None:
        any_alive = False
        dt = float(self._timer.interval())

        # Update particles
        if self._particles:
            alive: List[Dict[str, Any]] = []
            for p in self._particles:
                p["age"] += dt
                t = p["age"] / p["life"]
                if t >= 1.0:
                    continue
                vx = p["speed"] * math.cos(math.radians(p["angle"]))
                vy = p["speed"] * math.sin(math.radians(p["angle"]))
                p["x"] += vx
                p["y"] += vy
                fade = (1.0 - t)
                a = max(0, int(255 * (fade * fade)))
                c = p["color"]
                c.setAlpha(a)
                p["color"] = c
                alive.append(p)
            self._particles = alive
            any_alive = any_alive or bool(self._particles)

        # Update rings
        if self._rings:
            alive_rings: List[Dict[str, Any]] = []
            for r in self._rings:
                r["age"] += dt
                t = r["age"] / r["life"]
                if t >= 1.0:
                    continue
                # ease for radius and alpha
                radius = r["r0"] + (r["r1"] - r["r0"]) * QEasingCurve.OutCubic.valueForProgress(t)
                a = max(0, int(255 * (1.0 - t) ** 1.6))
                color = QtGui.QColor(r["color"])
                color.setAlpha(a)
                r["draw_radius"] = radius
                r["draw_color"] = color
                alive_rings.append(r)
            self._rings = alive_rings
            any_alive = any_alive or bool(self._rings)

        # Update flash
        if self._flash_alpha > 0:
            # consistent fade regardless of timer interval
            drop = int(255 * (dt / max(1.0, float(FX.DURATIONS.FLASH_FADE_MS))))
            self._flash_alpha = max(0, self._flash_alpha - max(1, drop))
            any_alive = True

        if not any_alive:
            self._timer.stop()
            self.hide()
            return

        self.update()

    def paintEvent(self, _: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # FLASH (fullscreen, subtle)
        if self._flash_alpha > 0 and FX.FLASH.ENABLED:
            painter.setPen(Qt.NoPen)
            flash = QtGui.QColor(255, 255, 255, self._flash_alpha)
            painter.setBrush(flash)
            painter.drawRect(self.rect())

        # PARTICLES
        for p in self._particles:
            painter.setPen(Qt.NoPen)
            painter.setBrush(p["color"])
            r = p["radius"]
            painter.drawEllipse(QtCore.QPointF(p["x"], p["y"]), r, r)

        # RINGS
        if self._rings and FX.RING.ENABLED:
            for r in self._rings:
                pen = QtGui.QPen(r.get("draw_color", FX.RING.COLOR))
                pen.setWidthF(r.get("width", FX.RING.WIDTH))
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(r["cx"], r["cy"]),
                                    r.get("draw_radius", r["r0"]),
                                    r.get("draw_radius", r["r0"]))

        painter.end()


# ---------------------------------------------------------------------------
# ButtonPressPulse — universal press feedback via opacity
# ---------------------------------------------------------------------------
class ButtonPressPulse(QObject):
    """
    Brief opacity dip on press (1.0 → 0.75 → 1.0).
    Purely visual; keeps geometry/layout unchanged.
    """
    def eventFilter(self, obj: QObject, event: QtCore.QEvent) -> bool:
        if isinstance(obj, QtWidgets.QPushButton) and event.type() == QEvent.MouseButtonPress:
            eff = obj.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                eff = QGraphicsOpacityEffect(obj)
                eff.setOpacity(1.0)
                obj.setGraphicsEffect(eff)

            anim = QPropertyAnimation(eff, b"opacity", obj)
            anim.setDuration(FX.DURATIONS.PRESS_PULSE_MS)
            anim.setStartValue(1.0)
            anim.setKeyValueAt(0.5, 0.75)
            anim.setEndValue(1.0)
            anim.setEasingCurve(FX.EASING.PRESS)
            # Keep a reference to avoid GC mid-flight
            if not hasattr(obj, "_anima_refs"):
                obj._anima_refs = []  # type: ignore[attr-defined]
            obj._anima_refs.append(anim)  # type: ignore[attr-defined]
            anim.finished.connect(lambda: obj._anima_refs.remove(anim))  # type: ignore[attr-defined]
            anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
        return False


# ---------------------------------------------------------------------------
# HoverGlow — gentle glow on hover (buttons only by default)
# ---------------------------------------------------------------------------
class HoverGlow(QObject):
    """Adds/animates a cyan glow on hover."""
    def __init__(self,
                 alpha_hover: int = FX.HOVER_GLOW.HOVER_ALPHA,
                 alpha_rest: int = FX.HOVER_GLOW.REST_ALPHA,
                 blur: int = FX.HOVER_GLOW.BLUR_RADIUS):
        super().__init__()
        self.alpha_hover = alpha_hover
        self.alpha_rest = alpha_rest
        self.blur = blur

    def _ensure_glow(self, w: QtWidgets.QWidget) -> QGraphicsDropShadowEffect:
        eff = w.graphicsEffect()
        if not isinstance(eff, QGraphicsDropShadowEffect):
            eff = QGraphicsDropShadowEffect(w)
            eff.setOffset(0, 0)
            eff.setBlurRadius(self.blur)
            c = QtGui.QColor(FX.HOVER_GLOW.COLOR)
            c.setAlpha(self.alpha_rest)
            eff.setColor(c)
            w.setGraphicsEffect(eff)
        return eff

    def eventFilter(self, obj: QObject, event: QtCore.QEvent) -> bool:
        # Respect opt-out flag (e.g., AnimatedPrompterButton)
        try:
            if bool(obj.property('anima.NoHoverGlow')):
                return False
        except Exception:
            pass
        if not isinstance(obj, QtWidgets.QAbstractButton):
            return False
        if event.type() == QEvent.Enter:
            eff = self._ensure_glow(obj)
            c = QtGui.QColor(eff.color())
            c.setAlpha(self.alpha_hover)
            eff.setColor(c)
        elif event.type() == QEvent.Leave:
            eff = self._ensure_glow(obj)
            c = QtGui.QColor(eff.color())
            c.setAlpha(self.alpha_rest)
            eff.setColor(c)
        return False


# ---------------------------------------------------------------------------
# AnimatedPrompterButton — large icon button (pulse + glow + fireworks)
# ---------------------------------------------------------------------------
class AnimatedPrompterButton(QtWidgets.QPushButton):
    """
    Big, icon-only button with click celebration:
      • Icon pulse scale 1.0 → PEAK → 1.0
      • Soft cyan glow (stronger on hover)
      • Optional ParticleBurst overlay for sparks/ring/flash on click

    `excitement_level`: per-button override; falls back to global when None.
    Geometry remains fixed; only icon size changes (no layout jank).
    """
    def __init__(self,
                 icon: QtGui.QIcon,
                 size: int = 160,
                 overlay: Optional[ParticleBurst] = None,
                 excitement_level: Optional[int] = None,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        # Opt-out flag for global HoverGlow
        self.setProperty('anima.NoHoverGlow', True)

        self._base = max(48, int(size))
        self._pulse = 1.0
        self._overlay = overlay
        self._excite = excitement_level  # None -> use global

        # Visuals
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton{border:none; background:transparent;}")
        self.setFixedSize(self._base, self._base)
        self.setIcon(icon)
        self.setIconSize(QSize(self._base, self._base))

        # Persistent glow
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(FX.GLOW.BLUR_RADIUS)
        c = QtGui.QColor(FX.GLOW.COLOR)
        c.setAlpha(FX.GLOW.REST_ALPHA)
        self._glow.setColor(c)
        self.setGraphicsEffect(self._glow)

        # Animations (kept as attributes to avoid GC)
        self._pulse_anim = QPropertyAnimation(self, b"pulseScale", self)
        self._glow_anim = QPropertyAnimation(self, b"glowAlpha", self)
        self._glow_anim.setDuration(FX.DURATIONS.PWRITE_GLOW_SHIFT_MS)
        self._glow_anim.setEasingCurve(FX.EASING.GLOW)

    # Property: pulseScale (drives icon size & slight blur lift)
    @QtCore.Property(float)
    def pulseScale(self) -> float:
        return self._pulse

    @pulseScale.setter
    def pulseScale(self, v: float) -> None:
        self._pulse = float(v)
        sz = _dpi_round(self, self._base * v)
        self.setIconSize(QSize(sz, sz))
        # Tiny blur lift at peak for extra “pop”
        try:
            self._glow.setBlurRadius(FX.GLOW.BLUR_RADIUS + (v - 1.0) * 18.0)
        except RuntimeError:
            pass
        self.update()

    # Property: glowAlpha (animate alpha only; color hue constant)
    def _get_glow_alpha(self) -> int:
        try:
            return int(self._glow.color().alpha())
        except Exception:
            return FX.GLOW.REST_ALPHA

    def _set_glow_alpha(self, a: int) -> None:
        try:
            c = QtGui.QColor(self._glow.color())
            c.setAlpha(int(max(0, min(255, a))))
            self._glow.setColor(c)
        except Exception:
            pass

    glowAlpha = QtCore.Property(int, _get_glow_alpha, _set_glow_alpha)

    # Hover glow lift
    def enterEvent(self, e: QtGui.QEnterEvent) -> None:  # type: ignore[override]
        super().enterEvent(e)
        _safe_stop(self._glow_anim)
        self._glow_anim.setStartValue(self._get_glow_alpha())
        self._glow_anim.setEndValue(FX.GLOW.HOVER_ALPHA)
        self._glow_anim.start()

    def leaveEvent(self, e: QtCore.QEvent) -> None:  # type: ignore[override]
        super().leaveEvent(e)
        _safe_stop(self._glow_anim)
        self._glow_anim.setStartValue(self._get_glow_alpha())
        self._glow_anim.setEndValue(FX.GLOW.REST_ALPHA)
        self._glow_anim.start()

    # Click feedback: pulse up → pulse down; fireworks via overlay
    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(e)

        _safe_stop(self._pulse_anim)
        self._pulse_anim.setDuration(FX.DURATIONS.PWRITE_PULSE_UP_MS)
        self._pulse_anim.setEasingCurve(FX.EASING.PULSE_UP)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(FX.PWRITE.PEAK_SCALE)
        try:
            self._pulse_anim.finished.disconnect(self._return_to_normal)
        except Exception:
            pass
        self._pulse_anim.finished.connect(self._return_to_normal)
        self._pulse_anim.start()

        if self._overlay and self.isVisible():
            center_local = self.rect().center()
            center_in_overlay = self.mapTo(self._overlay, center_local)
            # Use per-button excitement if provided; otherwise global
            level = self._excite if self._excite is not None else get_excitement_level()
            self._overlay.burst_at(center_in_overlay, excitement=level)

    def _return_to_normal(self) -> None:
        _safe_stop(self._pulse_anim)
        self._pulse_anim.setStartValue(self._pulse)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(FX.DURATIONS.PWRITE_PULSE_DOWN_MS)
        self._pulse_anim.setEasingCurve(FX.EASING.PULSE_DOWN)
        self._pulse_anim.start()


# ---------------------------------------------------------------------------
# TabSwitcher — smooth slide+fade transitions for QStackedWidget
# ---------------------------------------------------------------------------
class TabSwitcher(QtCore.QObject):
    """
    Animate transitions on a QStackedWidget:
        current: fade out
        target : slide in from +X with fade in

    Notes:
      • Re-entrant safe: interrupts any running transition and snaps to new one.
      • Restores widget effects/positions on completion.
    """
    def __init__(self,
                 stack: QtWidgets.QStackedWidget,
                 parent: Optional[QtWidgets.QObject] = None):
        super().__init__(parent or stack)
        self.stack = stack
        self._active_group: Optional[QtCore.QParallelAnimationGroup] = None
        self._duration = FX.DURATIONS.TAB_SWITCH_MS

    def go_to(self, index: int) -> None:
        if index == self.stack.currentIndex():
            return

        # If an animation is running, stop and snap stack to last intended state.
        if self._active_group and self._active_group.state() == QtCore.QAbstractAnimation.Running:
            self._active_group.stop()
            self._active_group = None

        old_w = self.stack.currentWidget()
        new_w = self.stack.widget(index)
        if not old_w or not new_w:
            self.stack.setCurrentIndex(index)
            return

        # Prepare effects
        old_eff = QGraphicsOpacityEffect(old_w)
        new_eff = QGraphicsOpacityEffect(new_w)
        old_w.setGraphicsEffect(old_eff)
        new_w.setGraphicsEffect(new_eff)

        # Ensure new is visible and offset a bit to the right
        new_w.setVisible(True)
        start_geo: QRect = new_w.geometry()
        new_w.move(start_geo.x() + FX.TAB.OFFSET_PX, start_geo.y())

        # Build animations
        fade_out = QPropertyAnimation(old_eff, b"opacity", self)
        fade_out.setDuration(self._duration)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(FX.EASING.TAB_FADE)

        fade_in = QPropertyAnimation(new_eff, b"opacity", self)
        fade_in.setDuration(self._duration)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(FX.EASING.TAB_FADE)

        slide_in = QPropertyAnimation(new_w, b"pos", self)
        slide_in.setDuration(self._duration)
        slide_in.setStartValue(new_w.pos())
        slide_in.setEndValue(start_geo.topLeft())
        slide_in.setEasingCurve(FX.EASING.TAB_SLIDE)

        grp = QtCore.QParallelAnimationGroup(self)
        grp.addAnimation(fade_out)
        grp.addAnimation(fade_in)
        grp.addAnimation(slide_in)

        def _finish() -> None:
            try:
                self.stack.setCurrentIndex(index)
                # Clean up graphics effects and restore final positions
                old_w.setGraphicsEffect(None)
                new_w.setGraphicsEffect(None)
                new_w.move(start_geo.topLeft())
            except Exception:
                pass
            self._active_group = None

        grp.finished.connect(_finish)
        self._active_group = grp
        grp.start(QtCore.QAbstractAnimation.DeleteWhenStopped)


# ---------------------------------------------------------------------------
# install_click_fx — one-liner to add hover glow & press pulse everywhere
# ---------------------------------------------------------------------------
def install_click_fx(root: QtWidgets.QWidget,
                     include_toolbuttons: bool = True) -> None:
    """
    Attach HoverGlow + ButtonPressPulse to every existing and future button
    inside `root`. Uses a lightweight tree eventFilter to wire newcomers.

    This function stores persistent references on `root` so filters aren’t GC’d.
    """
    press = ButtonPressPulse()
    glow = HoverGlow(
        alpha_hover=FX.HOVER_GLOW.HOVER_ALPHA,
        alpha_rest=FX.HOVER_GLOW.REST_ALPHA,
        blur=FX.HOVER_GLOW.BLUR_RADIUS,
    )

    class _TreeFilter(QObject):
        def eventFilter(self, obj: QObject, event: QtCore.QEvent) -> bool:
            # On Show or ChildAdded, sweep for buttons and install filters
            if event.type() in (QEvent.Show, QEvent.ChildAdded):
                for btn in obj.findChildren(QtWidgets.QAbstractButton):
                    if not include_toolbuttons and isinstance(btn, QtWidgets.QToolButton):
                        continue
                    if not bool(btn.property("anima.fxInstalled")):
                        btn.installEventFilter(press)
                        btn.installEventFilter(glow)
                        btn.setProperty("anima.fxInstalled", True)
            return False

    f = _TreeFilter(root)  # keep bound to root via attributes below

    # Persist strong refs on root to avoid GC
    setattr(root, "_anima_press_filter", press)
    setattr(root, "_anima_glow_filter", glow)
    setattr(root, "_anima_tree_filter", f)

    root.installEventFilter(f)

    # Also wire currently existing buttons immediately
    for btn in root.findChildren(QtWidgets.QAbstractButton):
        if not include_toolbuttons and isinstance(btn, QtWidgets.QToolButton):
            continue
        if not bool(btn.property("anima.fxInstalled")):
            btn.installEventFilter(press)
            btn.installEventFilter(glow)
            btn.setProperty("anima.fxInstalled", True)