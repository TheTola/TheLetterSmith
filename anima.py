#!/usr/bin/env python3
# File: anima.py
# -*- coding: utf-8 -*-

"""
anima.py — UI micro-animations + click FX toolkit for Letter Smith (PySide6)

Design goals
- Zero project imports (no circular coupling).
- No side effects on import (no prints, no filesystem, no timers until used).
- Safe defaults: do not destroy existing graphics effects unless unavoidable.

Public API (stable)
- ParticleBurst            : transparent overlay for sparks + rings + flash
- TabSwitcher              : normal slide+fade plus Command-only cross-fade
- install_click_fx         : global hover glow + press pulse installer
- set_excitement_level     : global intensity scalar for bursts
- get_excitement_level

Important Qt constraint
- A QWidget can have ONLY ONE graphicsEffect at a time. This module:
  - avoids overwriting existing effects when installing click FX
  - keeps normal TabSwitcher non-destructive when pages already own effects
  - uses snapshot layers for the Command override, preserving page effects
"""

from __future__ import annotations

import math
import random
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize, QTimer, QEasingCurve


# ─────────────────────────────────────────────────────────────────────────────
# Debug toggles (off by default)
# ─────────────────────────────────────────────────────────────────────────────

DEBUG_FX = False  # set True temporarily if you need to trace click-fx wiring


def _dbg(msg: str) -> None:
    if DEBUG_FX:
        logging.getLogger(__name__).debug(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Version + exports
# ─────────────────────────────────────────────────────────────────────────────

VERSION = "2.1.0-command-fade"

__all__ = [
    "VERSION",
    "FX",
    "set_excitement_level",
    "get_excitement_level",
    "ParticleBurst",
    "TabSwitcher",
    "install_hover_tab_switch",
    "install_click_fx",
    "ButtonPulseFilter",
]


# ─────────────────────────────────────────────────────────────────────────────
# Tunables (centralized)
# ─────────────────────────────────────────────────────────────────────────────

class FX:
    """Central constants. Change these without touching logic."""

    # Timings
    PRESS_MS = 140
    HOVER_GLOW_MS = 120
    TAB_MS = 220
    COMMAND_TAB_MULTIPLIER = 2.0
    TAB_HOVER_DELAY_MS = 333

    TICK_MS = 16  # ~60fps for overlay (sparks/rings/flash)
    WAVE_INTERVAL_MS = 28  # emit in small waves under heavy load

    # Hover glow (global install)
    HOVER_GLOW_COLOR = QtGui.QColor(0, 200, 255)
    HOVER_GLOW_ALPHA_REST = 85
    HOVER_GLOW_ALPHA_HOVER = 150
    HOVER_GLOW_BLUR = 20

    # Prompter glow (stronger)
    PROMPTER_GLOW_COLOR = QtGui.QColor(0, 190, 255)
    PROMPTER_GLOW_ALPHA_REST = 110
    PROMPTER_GLOW_ALPHA_HOVER = 175
    PROMPTER_GLOW_BLUR = 28

    # Particle burst
    SPARK_COUNT_BASE = 22
    SPARK_SPEED_MIN = 2.4
    SPARK_SPEED_MAX = 3.9
    SPARK_LIFE_MIN_MS = 220
    SPARK_LIFE_MAX_MS = 330
    SPARK_RADIUS_MIN = 1.3
    SPARK_RADIUS_MAX = 2.6
    SPARK_MAX_PER_BURST = 320
    SPARK_MAX_WAVES = 4

    SPARK_COLORS = [
        QtGui.QColor(150, 240, 255),
        QtGui.QColor(175, 245, 255),
        QtGui.QColor(200, 250, 255),
        QtGui.QColor(220, 255, 255),
    ]

    # Rings
    RING_ENABLED = True
    RING_LIFE_MS = 420
    RING_WIDTH = 2.0
    RING_COLOR = QtGui.QColor(120, 235, 255)
    RING_RADIUS_START = 8
    RING_RADIUS_END_BASE = 58
    RING_MAX = 3

    # Flash
    FLASH_ENABLED = True
    FLASH_ALPHA_BASE = 80
    FLASH_ALPHA_MAX = 210
    FLASH_FADE_MS = 220

    # Tab slide offset
    TAB_OFFSET_PX = 28

    # Intensity caps
    MAX_PARTICLES = 320


# ─────────────────────────────────────────────────────────────────────────────
# Global intensity
# ─────────────────────────────────────────────────────────────────────────────

_GLOBAL_EXCITEMENT = 10


def set_excitement_level(level: int) -> None:
    """Set global burst intensity (>=0). Very large values are internally tamed."""
    global _GLOBAL_EXCITEMENT
    try:
        _GLOBAL_EXCITEMENT = max(0, int(level))
    except Exception:
        _GLOBAL_EXCITEMENT = 0


def get_excitement_level() -> int:
    return _GLOBAL_EXCITEMENT


def _log_scale(level: int) -> float:
    """
    Maps level 0..∞ to a gentle multiplier using log10.
    - 0 -> 0
    - 10 -> ~1
    - 100 -> ~2
    - 1000 -> ~3
    """
    if level <= 0:
        return 0.0
    return max(0.0, math.log10(level + 9.0))


def _particle_count(level: int) -> int:
    scale = _log_scale(level)
    count = int(FX.SPARK_COUNT_BASE * scale) if scale > 0 else 0
    return max(0, min(count, FX.MAX_PARTICLES))


def _ring_count(level: int) -> int:
    if not FX.RING_ENABLED:
        return 0
    scale = _log_scale(level)
    return int(min(FX.RING_MAX, max(0, round(scale))))


def _ring_end_radius(level: int) -> float:
    return float(FX.RING_RADIUS_END_BASE + 22.0 * _log_scale(level))


def _flash_alpha(level: int) -> int:
    if not FX.FLASH_ENABLED:
        return 0
    a = int(FX.FLASH_ALPHA_BASE + 30.0 * _log_scale(level))
    return max(0, min(a, FX.FLASH_ALPHA_MAX))


# ─────────────────────────────────────────────────────────────────────────────
# Safe animation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _keep_anim(owner: QtCore.QObject, anim: QtCore.QAbstractAnimation) -> None:
    """
    Prevent premature GC of an animation.
    Stores a list on the owner; auto-removes on finish.
    """
    key = "_anima_anims"
    lst = getattr(owner, key, None)
    if lst is None:
        lst = []
        setattr(owner, key, lst)
    lst.append(anim)

    def _drop() -> None:
        try:
            lst.remove(anim)
        except Exception:
            pass

    try:
        anim.finished.connect(_drop)  # type: ignore[attr-defined]
    except Exception:
        pass


def _has_icon(btn: QtWidgets.QAbstractButton) -> bool:
    try:
        ic = btn.icon()
        return ic is not None and not ic.isNull()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Particle overlay
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Spark:
    x: float
    y: float
    angle_deg: float
    speed: float
    age: float
    life: float
    radius: float
    color: QtGui.QColor


@dataclass
class _Ring:
    cx: float
    cy: float
    age: float
    life: float
    r0: float
    r1: float
    width: float
    base_color: QtGui.QColor


class ParticleBurst(QtWidgets.QWidget):
    """
    Transparent overlay that can draw:
    - sparks (tiny circles moving outward while fading)
    - optional rings (shockwaves)
    - optional flash (full-rect fade)

    This widget follows its parent geometry automatically (non-destructive).
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._sparks: List[_Spark] = []
        self._rings: List[_Ring] = []
        self._flash_alpha: int = 0

        self._timer = QTimer(self)
        self._timer.setInterval(FX.TICK_MS)
        self._timer.timeout.connect(self._tick)

        # Wave queue to avoid a single giant frame under high counts
        self._waves: List[Dict[str, Any]] = []
        self._wave_timer: Optional[QTimer] = None

        # Snap geometry immediately if parent exists (avoids a 1-frame mismatch)
        if isinstance(parent, QtWidgets.QWidget):
            try:
                self.setGeometry(parent.rect())
                parent.installEventFilter(self)
                self.raise_()
            except Exception:
                pass

        self.hide()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Follow parent changes reliably
        if obj is self.parent() and isinstance(obj, QtWidgets.QWidget):
            et = event.type()
            if et in (QEvent.Resize, QEvent.Move, QEvent.Show):
                try:
                    self.setGeometry(obj.rect())
                except Exception:
                    pass
        return False

    def burst_at(self, center: QPoint, *, count: Optional[int] = None, excitement: Optional[int] = None) -> None:
        """
        Spawn effects centered at `center` (overlay coordinates).
        - If `excitement` is None, uses global intensity.
        - If `count` is provided, it overrides spark count (still capped).
        """
        level = get_excitement_level() if excitement is None else max(0, int(excitement))
        want = _particle_count(level) if count is None else max(0, int(count))
        want = min(want, FX.SPARK_MAX_PER_BURST)

        if want > 0:
            self._emit_sparks(center, want, waves=True)

        rings = _ring_count(level)
        if rings > 0:
            for i in range(rings):
                self._rings.append(
                    _Ring(
                        cx=float(center.x()),
                        cy=float(center.y()),
                        age=0.0,
                        life=float(FX.RING_LIFE_MS),
                        r0=float(FX.RING_RADIUS_START + i * 2),
                        r1=float(_ring_end_radius(level) + i * 10),
                        width=float(FX.RING_WIDTH),
                        base_color=QtGui.QColor(FX.RING_COLOR),
                    )
                )

        self._flash_alpha = max(self._flash_alpha, _flash_alpha(level))

        self._timer.start()
        self.show()
        self.raise_()
        self.update()

    def _emit_sparks(self, center: QPoint, total: int, *, waves: bool) -> None:
        if total <= 0:
            return

        max_per_wave = max(1, int(FX.SPARK_MAX_PER_BURST / max(1, FX.SPARK_MAX_WAVES)))
        if waves and total > max_per_wave:
            self._waves.clear()
            remaining = total
            while remaining > 0:
                n = min(max_per_wave, remaining)
                self._waves.append({"center": QPoint(center), "count": n})
                remaining -= n

            if self._wave_timer is None:
                self._wave_timer = QTimer(self)
                self._wave_timer.setInterval(FX.WAVE_INTERVAL_MS)
                self._wave_timer.timeout.connect(self._emit_next_wave)

            self._wave_timer.start()
        else:
            self._spawn_sparks(center, total)

    def _emit_next_wave(self) -> None:
        if not self._waves:
            if self._wave_timer:
                self._wave_timer.stop()
            return
        wave = self._waves.pop(0)
        self._spawn_sparks(wave["center"], wave["count"])
        self.update()

    def _spawn_sparks(self, center: QPoint, n: int) -> None:
        cx = float(center.x())
        cy = float(center.y())
        for _ in range(int(n)):
            ang = random.uniform(0.0, 360.0)
            spd = random.uniform(FX.SPARK_SPEED_MIN, FX.SPARK_SPEED_MAX)
            life = random.uniform(FX.SPARK_LIFE_MIN_MS, FX.SPARK_LIFE_MAX_MS)
            rad = random.uniform(FX.SPARK_RADIUS_MIN, FX.SPARK_RADIUS_MAX)

            base = random.choice(FX.SPARK_COLORS)
            c = QtGui.QColor(base)
            c.setAlpha(random.randint(160, 220))

            self._sparks.append(_Spark(cx, cy, ang, spd, 0.0, float(life), float(rad), c))

    def _tick(self) -> None:
        dt = float(self._timer.interval())
        any_alive = False

        # Sparks
        if self._sparks:
            alive: List[_Spark] = []
            for s in self._sparks:
                s.age += dt
                t = s.age / s.life
                if t >= 1.0:
                    continue

                vx = s.speed * math.cos(math.radians(s.angle_deg))
                vy = s.speed * math.sin(math.radians(s.angle_deg))
                s.x += vx
                s.y += vy

                fade = (1.0 - t)
                a = max(0, int(255.0 * (fade * fade)))
                s.color.setAlpha(a)
                alive.append(s)

            self._sparks = alive
            any_alive = any_alive or bool(self._sparks)

        # Rings
        if self._rings:
            alive_r: List[_Ring] = []
            for r in self._rings:
                r.age += dt
                t = r.age / r.life
                if t >= 1.0:
                    continue
                alive_r.append(r)
            self._rings = alive_r
            any_alive = any_alive or bool(self._rings)

        # Flash
        if self._flash_alpha > 0:
            drop = int(255.0 * (dt / max(1.0, float(FX.FLASH_FADE_MS))))
            self._flash_alpha = max(0, self._flash_alpha - max(1, drop))
            any_alive = True

        if not any_alive:
            self._timer.stop()
            self.hide()
            return

        self.update()

    def paintEvent(self, _: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # Flash
        if FX.FLASH_ENABLED and self._flash_alpha > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, self._flash_alpha))
            p.drawRect(self.rect())

        # Sparks
        if self._sparks:
            p.setPen(Qt.NoPen)
            for s in self._sparks:
                p.setBrush(s.color)
                p.drawEllipse(QtCore.QPointF(s.x, s.y), s.radius, s.radius)

        # Rings
        if FX.RING_ENABLED and self._rings:
            for r in self._rings:
                t = r.age / r.life
                eased = QEasingCurve.OutCubic.valueForProgress(t)
                radius = r.r0 + (r.r1 - r.r0) * eased
                alpha = max(0, int(255.0 * (1.0 - t) ** 1.6))
                c = QtGui.QColor(r.base_color)
                c.setAlpha(alpha)

                pen = QtGui.QPen(c)
                pen.setWidthF(r.width)
                pen.setCosmetic(True)

                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QtCore.QPointF(r.cx, r.cy), radius, radius)

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Press feedback filter
# ─────────────────────────────────────────────────────────────────────────────

class ButtonPulseFilter(QtCore.QObject):
    """
    Compatibility filter.
    Gives a small press feedback on QAbstractButton.

    Strategy:
    - If the widget has NO graphicsEffect: use QGraphicsOpacityEffect pulse.
    - If it already has an effect: do NOT overwrite; pulse iconSize (if icon exists).
    """

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() != QEvent.MouseButtonPress:
            return False
        if not isinstance(obj, QtWidgets.QAbstractButton):
            return False

        # Respect opt-out
        try:
            if bool(obj.property("anima.NoPressPulse")):
                return False
        except Exception:
            pass

        # A) opacity pulse only when safe (no existing effect)
        if obj.graphicsEffect() is None:
            eff = QtWidgets.QGraphicsOpacityEffect(obj)
            eff.setOpacity(1.0)
            obj.setGraphicsEffect(eff)

            anim = QtCore.QPropertyAnimation(eff, b"opacity", obj)
            anim.setDuration(FX.PRESS_MS)
            anim.setStartValue(1.0)
            anim.setKeyValueAt(0.5, 0.78)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.InOutSine)

            _keep_anim(obj, anim)
            anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
            return False

        # B) icon-size pulse (no effect overwrite)
        if not _has_icon(obj):
            return False

        base = obj.iconSize()

        # Cache base size once (and clamp to current if zero)
        bw_prop = int(obj.property("anima._icon_base_w") or 0)
        bh_prop = int(obj.property("anima._icon_base_h") or 0)

        bw = bw_prop if bw_prop > 0 else max(1, base.width())
        bh = bh_prop if bh_prop > 0 else max(1, base.height())

        if bw_prop <= 0:
            obj.setProperty("anima._icon_base_w", bw)
        if bh_prop <= 0:
            obj.setProperty("anima._icon_base_h", bh)

        anim = QtCore.QVariantAnimation(obj)
        anim.setDuration(FX.PRESS_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.InOutSine)

        def _apply(v: Any) -> None:
            t = float(v)
            # Triangle dip (0->1->0)
            tri = 1.0 - abs(2.0 * t - 1.0)
            k = 1.0 - 0.08 * tri
            obj.setIconSize(QSize(max(1, int(bw * k)), max(1, int(bh * k))))

        anim.valueChanged.connect(_apply)  # type: ignore[attr-defined]
        anim.finished.connect(lambda: obj.setIconSize(QSize(bw, bh)))  # type: ignore[attr-defined]

        _keep_anim(obj, anim)
        anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Hover glow filter (installed by install_click_fx)
# ─────────────────────────────────────────────────────────────────────────────

class _HoverGlowFilter(QtCore.QObject):
    """
    Adds a QGraphicsDropShadowEffect glow on hover.

    Safety:
    - If a widget already has ANY graphicsEffect, we do not replace it.
    - For buttons that want to opt out: setProperty('anima.NoHoverGlow', True).
    """

    def __init__(self, *, color: QtGui.QColor, alpha_rest: int, alpha_hover: int, blur: int) -> None:
        super().__init__()
        self._color = QtGui.QColor(color)
        self._alpha_rest = int(alpha_rest)
        self._alpha_hover = int(alpha_hover)
        self._blur = int(blur)

    def _ensure_effect(self, w: QtWidgets.QWidget) -> Optional[QtWidgets.QGraphicsDropShadowEffect]:
        if w.graphicsEffect() is not None:
            return None

        eff = QtWidgets.QGraphicsDropShadowEffect(w)
        eff.setOffset(0, 0)
        eff.setBlurRadius(self._blur)
        c = QtGui.QColor(self._color)
        c.setAlpha(self._alpha_rest)
        eff.setColor(c)
        w.setGraphicsEffect(eff)
        w.setProperty("anima._hover_glow_installed", True)
        return eff

    def _set_alpha(self, eff: QtWidgets.QGraphicsDropShadowEffect, alpha: int) -> None:
        c = QtGui.QColor(eff.color())
        c.setAlpha(int(alpha))
        eff.setColor(c)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not isinstance(obj, QtWidgets.QAbstractButton):
            return False

        try:
            if bool(obj.property("anima.NoHoverGlow")):
                return False
        except Exception:
            pass

        if event.type() == QEvent.Enter:
            eff = obj.graphicsEffect()
            if not isinstance(eff, QtWidgets.QGraphicsDropShadowEffect):
                eff = self._ensure_effect(obj)
            if isinstance(eff, QtWidgets.QGraphicsDropShadowEffect):
                self._set_alpha(eff, self._alpha_hover)

        elif event.type() == QEvent.Leave:
            eff = obj.graphicsEffect()
            if isinstance(eff, QtWidgets.QGraphicsDropShadowEffect) and bool(obj.property("anima._hover_glow_installed")):
                self._set_alpha(eff, self._alpha_rest)

        return False


# ─────────────────────────────────────────────────────────────────────────────
# Removed unused titlebar button helpers.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Removed unused prompter button helpers.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# TabSwitcher (QStackedWidget transitions)
# ─────────────────────────────────────────────────────────────────────────────

class _HoverTabSwitchFilter(QtCore.QObject):
    """Switch a QTabBar after the cursor rests on a tab for a short delay."""

    def __init__(self, tabbar: QtWidgets.QTabBar, delay_ms: int = FX.TAB_HOVER_DELAY_MS) -> None:
        super().__init__(tabbar)
        self.tabbar = tabbar
        self._pending_index = -1
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(1, int(delay_ms)))
        self._timer.timeout.connect(self._activate_pending_tab)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is not self.tabbar:
            return False

        event_type = event.type()
        if event_type in (QEvent.MouseMove, QEvent.HoverMove, QEvent.Enter, QEvent.HoverEnter):
            if QtWidgets.QApplication.mouseButtons() != Qt.NoButton:
                self._cancel()
                return False

            position = self._event_position(event)
            if position is not None:
                self._schedule(self.tabbar.tabAt(position))

        elif event_type in (QEvent.Leave, QEvent.HoverLeave, QEvent.MouseButtonPress):
            self._cancel()

        return False

    def _event_position(self, event: QtCore.QEvent) -> Optional[QtCore.QPoint]:
        try:
            if hasattr(event, "position"):
                return event.position().toPoint()  # type: ignore[attr-defined]
            if hasattr(event, "pos"):
                return event.pos()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            return self.tabbar.mapFromGlobal(QtGui.QCursor.pos())
        except RuntimeError:
            return None

    def _schedule(self, index: int) -> None:
        if index < 0 or index == self.tabbar.currentIndex():
            self._cancel()
            return
        if index != self._pending_index:
            self._pending_index = index
            self._timer.start()

    def _cancel(self) -> None:
        self._pending_index = -1
        self._timer.stop()

    def _activate_pending_tab(self) -> None:
        index = self._pending_index
        self._cancel()
        if index < 0:
            return

        try:
            cursor_pos = self.tabbar.mapFromGlobal(QtGui.QCursor.pos())
            cursor_index = self.tabbar.tabAt(cursor_pos)
            if index == cursor_index and index != self.tabbar.currentIndex():
                self.tabbar.setCurrentIndex(index)
        except RuntimeError:
            pass


def install_hover_tab_switch(
    tabbar: QtWidgets.QTabBar,
    *,
    delay_ms: int = FX.TAB_HOVER_DELAY_MS,
) -> _HoverTabSwitchFilter:
    """
    Restore switch-on-hover behavior for a QTabBar.

    The filter is retained on the tab bar, duplicate installation is avoided,
    and a click/drag immediately cancels any pending hover switch.
    """
    existing = getattr(tabbar, "_anima_hover_tab_switch", None)
    if isinstance(existing, _HoverTabSwitchFilter):
        return existing

    tabbar.setMouseTracking(True)
    tabbar.setAttribute(Qt.WA_Hover, True)

    hover_filter = _HoverTabSwitchFilter(tabbar, delay_ms=delay_ms)
    tabbar.installEventFilter(hover_filter)
    setattr(tabbar, "_anima_hover_tab_switch", hover_filter)
    tabbar.setProperty("anima.HoverTabSwitchInstalled", True)
    return hover_filter


class TabSwitcher(QtCore.QObject):
    """
    Animated transitions for QStackedWidget.

    Normal tabs:
    - slide in from the right
    - fade when neither page already owns a graphics effect

    Command tab override:
    - pure cross-fade, no slide
    - double the normal transition duration
    - applies both entering and leaving Command
    - cancels any transition already in progress
    - uses snapshots, so it does not replace page graphics effects

    Hover switching:
    - preserves an existing Nexus hover filter
    - otherwise restores hover switching automatically when a matching QTabBar
      can be found on the stack's window
    """

    def __init__(self, stack: QtWidgets.QStackedWidget, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent or stack)
        self.stack = stack
        self._active: Optional[QtCore.QParallelAnimationGroup] = None
        self._active_target: Optional[QtWidgets.QWidget] = None
        self._active_cleanup = None
        self._hover_tab_switch: Optional[_HoverTabSwitchFilter] = None
        self._restore_hover_switching()

    def _restore_hover_switching(self) -> None:
        """Install hover switching only when Nexus has not already installed it."""
        try:
            window = self.stack.window()
            existing = getattr(window, "_hover_tab_switch", None)
            existing_tabbar = getattr(existing, "tabbar", None)
            if isinstance(existing_tabbar, QtWidgets.QTabBar):
                return

            candidate = getattr(window, "tabbar", None)
            if not isinstance(candidate, QtWidgets.QTabBar):
                candidates = [
                    tabbar
                    for tabbar in window.findChildren(QtWidgets.QTabBar)
                    if tabbar.count() == self.stack.count()
                ]
                candidate = candidates[0] if candidates else None

            if isinstance(candidate, QtWidgets.QTabBar):
                self._hover_tab_switch = install_hover_tab_switch(candidate)
        except Exception:
            self._hover_tab_switch = None

    @staticmethod
    def _is_command_page(widget: Optional[QtWidgets.QWidget]) -> bool:
        if widget is None:
            return False

        try:
            mode = str(widget.property("anima.Transition") or "").strip().lower()
            if mode in {"fade", "command-fade", "command_fade"}:
                return True
        except Exception:
            pass

        try:
            if widget.objectName().strip().lower() == "commandtab":
                return True
        except Exception:
            pass

        return widget.__class__.__name__.strip().lower() == "commandtab"

    @staticmethod
    def _command_duration(old_w: QtWidgets.QWidget, new_w: QtWidgets.QWidget) -> int:
        multiplier = float(FX.COMMAND_TAB_MULTIPLIER)
        for widget in (old_w, new_w):
            try:
                value = widget.property("anima.TransitionDurationMultiplier")
                if value is not None:
                    multiplier = max(multiplier, float(value))
            except (TypeError, ValueError, RuntimeError):
                pass
        return max(1, int(round(FX.TAB_MS * multiplier)))

    def _stop_active(self) -> None:
        grp = self._active
        cleanup = self._active_cleanup
        self._active = None
        self._active_target = None
        self._active_cleanup = None

        if grp is not None:
            try:
                grp.stop()
            except RuntimeError:
                pass

        if cleanup is not None:
            cleanup()

        if grp is not None:
            try:
                grp.deleteLater()
            except RuntimeError:
                pass

    @staticmethod
    def _grab_page(widget: QtWidgets.QWidget, size: QtCore.QSize) -> QtGui.QPixmap:
        """Capture a page at the live stack size without changing its effects."""
        try:
            widget.ensurePolished()
            widget.repaint()
            pixmap = widget.grab()
        except RuntimeError:
            return QtGui.QPixmap()

        if pixmap.isNull() or not size.isValid():
            return pixmap
        if pixmap.size() != size:
            pixmap = pixmap.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return pixmap

    def _go_to_command_fade(
        self,
        index: int,
        old_w: QtWidgets.QWidget,
        new_w: QtWidgets.QWidget,
        new_geo: QRect,
    ) -> None:
        """Run the Command-only snapshot cross-fade override."""
        viewport = self.stack.contentsRect()
        if viewport.isEmpty():
            self.stack.setCurrentIndex(index)
            return

        old_pixmap = self._grab_page(old_w, viewport.size())

        # Commit the requested page before capturing it. The snapshots cover the
        # viewport during the fade, and this makes interruption deterministic:
        # a new hover target starts from whichever page was most recently chosen.
        self.stack.setCurrentWidget(new_w)
        new_w.setGeometry(new_geo)
        new_w.show()
        new_w.raise_()
        new_pixmap = self._grab_page(new_w, viewport.size())

        if old_pixmap.isNull() or new_pixmap.isNull():
            new_w.show()
            new_w.raise_()
            return

        old_layer = QtWidgets.QLabel(self.stack)
        new_layer = QtWidgets.QLabel(self.stack)
        for layer, pixmap in ((old_layer, old_pixmap), (new_layer, new_pixmap)):
            layer.setGeometry(viewport)
            layer.setPixmap(pixmap)
            layer.setScaledContents(True)
            layer.setStyleSheet("background:transparent; border:none; padding:0; margin:0;")
            layer.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layer.show()

        new_layer.raise_()
        old_layer.raise_()

        old_eff = QtWidgets.QGraphicsOpacityEffect(old_layer)
        new_eff = QtWidgets.QGraphicsOpacityEffect(new_layer)
        old_eff.setOpacity(1.0)
        new_eff.setOpacity(0.0)
        old_layer.setGraphicsEffect(old_eff)
        new_layer.setGraphicsEffect(new_eff)

        # Hide the live page until the new snapshot has fully faded in.
        new_w.hide()

        duration = self._command_duration(old_w, new_w)
        grp = QtCore.QParallelAnimationGroup(self)

        fade_out = QtCore.QPropertyAnimation(old_eff, b"opacity", grp)
        fade_out.setDuration(duration)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InOutSine)

        fade_in = QtCore.QPropertyAnimation(new_eff, b"opacity", grp)
        fade_in.setDuration(duration)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.InOutSine)

        grp.addAnimation(fade_out)
        grp.addAnimation(fade_in)

        cleaned = False

        def _cleanup() -> None:
            nonlocal cleaned
            if cleaned:
                return
            cleaned = True

            for layer in (old_layer, new_layer):
                try:
                    layer.hide()
                    layer.setGraphicsEffect(None)
                    layer.deleteLater()
                except RuntimeError:
                    pass

            try:
                current_w = self.stack.currentWidget()
                if current_w is not None:
                    current_w.setGeometry(new_geo)
                    current_w.show()
                    current_w.raise_()
            except RuntimeError:
                pass

        def _finish() -> None:
            if self._active is not grp:
                return
            self._active = None
            self._active_target = None
            self._active_cleanup = None
            _cleanup()
            grp.deleteLater()

        grp.finished.connect(_finish)
        self._active = grp
        self._active_target = new_w
        self._active_cleanup = _cleanup
        grp.start()

    def go_to(self, index: int) -> None:
        if index < 0 or index >= self.stack.count():
            return

        requested_w = self.stack.widget(index)
        if self._active is not None:
            if requested_w is self._active_target:
                return
            self._stop_active()

        if index == self.stack.currentIndex():
            return

        old_w = self.stack.currentWidget()
        new_w = requested_w
        if not old_w or not new_w:
            self.stack.setCurrentIndex(index)
            return

        # Hidden stack pages can retain Qt's default 640x480 geometry. Size the
        # incoming page to the live viewport before its first visible paint.
        new_geo: QRect = old_w.geometry()
        if new_geo.isEmpty():
            new_geo = self.stack.contentsRect()
        new_w.setGeometry(new_geo)

        # Command overrides every ordinary transition both entering and leaving.
        if self._is_command_page(old_w) or self._is_command_page(new_w):
            self._go_to_command_fade(index, old_w, new_w, new_geo)
            return

        # Always ensure new widget is visible and on top during normal animation.
        new_w.setVisible(True)
        new_w.raise_()

        can_fade = (old_w.graphicsEffect() is None) and (new_w.graphicsEffect() is None)

        start_pos = QtCore.QPoint(new_geo.x() + FX.TAB_OFFSET_PX, new_geo.y())
        end_pos = new_geo.topLeft()
        new_w.move(start_pos)

        grp = QtCore.QParallelAnimationGroup(self)

        old_eff = None
        new_eff = None
        if can_fade:
            old_eff = QtWidgets.QGraphicsOpacityEffect(old_w)
            new_eff = QtWidgets.QGraphicsOpacityEffect(new_w)
            old_eff.setOpacity(1.0)
            new_eff.setOpacity(0.0)

            old_w.setGraphicsEffect(old_eff)
            new_w.setGraphicsEffect(new_eff)

            fade_out = QtCore.QPropertyAnimation(old_eff, b"opacity", grp)
            fade_out.setDuration(FX.TAB_MS)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.InOutSine)

            fade_in = QtCore.QPropertyAnimation(new_eff, b"opacity", grp)
            fade_in.setDuration(FX.TAB_MS)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.InOutSine)

            grp.addAnimation(fade_out)
            grp.addAnimation(fade_in)

        slide_in = QtCore.QPropertyAnimation(new_w, b"pos", grp)
        slide_in.setDuration(FX.TAB_MS)
        slide_in.setStartValue(start_pos)
        slide_in.setEndValue(end_pos)
        slide_in.setEasingCurve(QEasingCurve.OutCubic)
        grp.addAnimation(slide_in)

        cleaned = False

        def _cleanup() -> None:
            nonlocal cleaned
            if cleaned:
                return
            cleaned = True

            for widget, effect in ((old_w, old_eff), (new_w, new_eff)):
                if effect is None:
                    continue
                try:
                    if widget.graphicsEffect() is effect:
                        widget.setGraphicsEffect(None)
                except RuntimeError:
                    pass

            try:
                new_w.move(end_pos)
                current_w = self.stack.currentWidget()
                if current_w is not new_w:
                    new_w.setVisible(False)
                if current_w is not None:
                    current_w.setVisible(True)
                    current_w.raise_()
            except RuntimeError:
                pass

        def _finish() -> None:
            if self._active is not grp:
                return

            self._active = None
            self._active_target = None
            self._active_cleanup = None
            try:
                if self.stack.indexOf(new_w) >= 0:
                    self.stack.setCurrentWidget(new_w)
            except RuntimeError:
                pass
            _cleanup()
            grp.deleteLater()

        grp.finished.connect(_finish)
        self._active = grp
        self._active_target = new_w
        self._active_cleanup = _cleanup
        grp.start()


# ─────────────────────────────────────────────────────────────────────────────
# install_click_fx (robust: catches deep runtime additions)
# ─────────────────────────────────────────────────────────────────────────────

def install_click_fx(root: QtWidgets.QWidget, *, include_toolbuttons: bool = True) -> None:
    """
    Attach:
    - hover glow (safe: only if no existing graphicsEffect)
    - press pulse (safe: does not overwrite existing effects)

    Robust dynamic wiring:
    - Installs a single "tree watcher" eventFilter on root AND every QWidget container under root.
    - When any container receives ChildAdded, it wires:
        - the child if it's a button
        - or the child's subtree if it's a container
    This ensures deep/nested runtime additions are caught.

    Persistence:
    - Stores strong references on `root` so filters won't get GC'd.
    """

    press = ButtonPulseFilter()
    glow = _HoverGlowFilter(
        color=FX.HOVER_GLOW_COLOR,
        alpha_rest=FX.HOVER_GLOW_ALPHA_REST,
        alpha_hover=FX.HOVER_GLOW_ALPHA_HOVER,
        blur=FX.HOVER_GLOW_BLUR,
    )

    def _should_wire(btn: QtWidgets.QAbstractButton) -> bool:
        if not include_toolbuttons and isinstance(btn, QtWidgets.QToolButton):
            return False
        try:
            if bool(btn.property("anima.fxInstalled")):
                return False
        except Exception:
            pass
        return True

    def _wire_button(btn: QtWidgets.QAbstractButton) -> None:
        if not _should_wire(btn):
            return
        btn.installEventFilter(press)
        btn.installEventFilter(glow)
        btn.setProperty("anima.fxInstalled", True)
        _dbg(f"[anima] wired button: {btn.__class__.__name__} {btn.objectName()!r}")

    def _wire_subtree(widget: QtWidgets.QWidget) -> None:
        # Wire all buttons under this subtree
        for btn in widget.findChildren(QtWidgets.QAbstractButton):
            _wire_button(btn)
        # Ensure watcher installed on all containers in this subtree
        for w in widget.findChildren(QtWidgets.QWidget):
            _ensure_watch(w)

    def _ensure_watch(w: QtWidgets.QWidget) -> None:
        try:
            if bool(w.property("anima.treeWatchInstalled")):
                return
        except Exception:
            pass
        w.installEventFilter(tree)
        w.setProperty("anima.treeWatchInstalled", True)

    class _TreeFilter(QtCore.QObject):
        def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
            if event.type() != QEvent.ChildAdded:
                return False

            # QChildEvent.child() gives the added QObject
            try:
                child = event.child()  # type: ignore[attr-defined]
            except Exception:
                child = None

            if isinstance(child, QtWidgets.QAbstractButton):
                _wire_button(child)
                return False

            if isinstance(child, QtWidgets.QWidget):
                # A container was added: watch it, then wire its subtree
                _ensure_watch(child)
                _wire_subtree(child)
                return False

            return False

    tree = _TreeFilter(root)

    # Persist strong refs on root
    setattr(root, "_anima_press_filter", press)
    setattr(root, "_anima_glow_filter", glow)
    setattr(root, "_anima_tree_filter", tree)

    # Install watcher broadly (root + all widget containers)
    _ensure_watch(root)
    for w in root.findChildren(QtWidgets.QWidget):
        _ensure_watch(w)

    # Wire everything currently present
    _wire_subtree(root)
