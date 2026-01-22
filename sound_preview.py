# ===============================
# File: sound_preview.py
# ===============================
from __future__ import annotations

"""
Sound Preview / Visualizer Wrapper (polished)

Purpose
-------
Provide a single, durable widget the app can mount in the global Preview area.
It prefers the high-end `AudioVisualizerUltra` implementation, but degrades
gracefully to a lightweight, dependency-free bar visualizer when the advanced
module cannot be imported.

Design Notes
------------
• Zero hard failures — always renders *something*.
• API stable with prior versions:
    - class SoundPreviewWidget(QtWidgets.QFrame)
    - __init__(media_player, parent=None)
    - set_audio_file(path: str) -> None
    - set_media_player(player)  -> None (new public pass-through)
• Dark UI by default; rounded border and subtle frame.
• No external deps beyond PySide6.
• DPI-aware painting; layout-stable.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


# ─────────────────────────────────────────────────────────────────────────────
# Try to import the premium visualizer. Fall back to project-local Wave path.
# If both fail, provide a minimal placeholder visualizer.
# ─────────────────────────────────────────────────────────────────────────────
_AudioVisualizerUltra = None  # type: ignore[var-annotated]

try:
    from audio_visualizer import AudioVisualizerUltra as _AudioVisualizerUltra  # type: ignore
except Exception:
    # Fallback: try project-local Wave module (gallery/sounds/Wave)
    try:
        import sys
        from pathlib import Path

        _alt = Path(__file__).resolve().parent / "gallery" / "sounds" / "Wave"
        sys.path.insert(0, str(_alt))
        from audio_visualizer import AudioVisualizerUltra as _AudioVisualizerUltra  # type: ignore
    except Exception:
        _AudioVisualizerUltra = None


# ─────────────────────────────────────────────────────────────────────────────
# Minimal, dependency-free visualizer (placeholder)
# ─────────────────────────────────────────────────────────────────────────────
class _MiniBarVisualizer(QtWidgets.QWidget):
    """
    Lightweight fallback: animated EQ bars while audio is "playing".
    We don't inspect real audio; we mirror the player's playback state
    and generate deterministic pseudo-motion for a calm, premium feel.
    """
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        self._bars = [0.2, 0.35, 0.5, 0.75, 0.55, 0.4, 0.3, 0.45, 0.6, 0.32]
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS
        self._timer.timeout.connect(self._tick)

        self._player = None  # QMediaPlayer-like (duck typed)
        self._active = False
        self._hint_text = "No track selected"

        self._bg_color = QtGui.QColor("#0f1116")
        self._bar_color = QtGui.QColor(0, 210, 255, 180)
        self._bar_bg = QtGui.QColor(255, 255, 255, 18)
        self._grid_color = QtGui.QColor(255, 255, 255, 12)

    # Public (parity with premium)
    def set_media_player(self, player) -> None:
        # Disconnect old player if present
        try:
            if self._player:
                self._player.playbackStateChanged.disconnect(self._on_state_changed)  # type: ignore[attr-defined]
        except Exception:
            pass

        self._player = player
        try:
            # Connect new player; start anim if already playing
            self._player.playbackStateChanged.connect(self._on_state_changed)  # type: ignore[attr-defined]
            self._on_state_changed(getattr(self._player, "playbackState", lambda: 0)())
        except Exception:
            # If player doesn't expose Qt signal, keep simple idle animation
            self._active = False
            self._timer.stop()
            self.update()

    def set_audio_file(self, path: str) -> None:
        # Only used for UX hints in fallback
        self._hint_text = path or "No track selected"
        self.update()

    # Internals
    def _on_state_changed(self, state) -> None:
        # QtMultimedia playback states (Qt 6): 0=Stopped, 1=Playing, 2=Paused
        playing = int(state) == 1
        self._active = playing
        if playing:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase += 0.055
        # Soft, smooth motion
        import math
        for i, _ in enumerate(self._bars):
            w = 0.85 + 0.15 * math.sin(self._phase * (1.5 + 0.17 * i))
            self._bars[i] = 0.18 + 0.72 * abs(math.sin(self._phase * (0.8 + 0.13 * i))) * w
        self.update()

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        r = self.rect()
        if not r.isValid():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # Background
        p.fillRect(r, self._bg_color)

        # Subtle grid
        p.setPen(QtGui.QPen(self._grid_color, 1))
        step = max(24, int(min(r.width(), r.height()) * 0.06))
        for x in range(r.left() + step, r.right(), step):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top() + step, r.bottom(), step):
            p.drawLine(r.left(), y, r.right(), y)

        # Bars
        n = len(self._bars)
        gap = max(4, int(r.width() * 0.006))
        total_gap = gap * (n + 1)
        bar_w = max(6, (r.width() - total_gap) // n)

        base_y = int(r.bottom() - max(8, r.height() * 0.08))
        max_h = int(r.height() * 0.65)
        x = r.left() + gap

        # Bar background track
        p.setPen(Qt.NoPen)
        p.setBrush(self._bar_bg)
        for _ in range(n):
            p.drawRoundedRect(QtCore.QRectF(x, base_y - max_h, bar_w, max_h), 3, 3)
            x += bar_w + gap

        # Foreground bars
        x = r.left() + gap
        p.setBrush(self._bar_color)
        for h_ratio in self._bars:
            h = int(max(3, max_h * h_ratio))
            p.drawRoundedRect(QtCore.QRectF(x, base_y - h, bar_w, h), 3, 3)
            x += bar_w + gap

        # Hint (only when not active)
        if not self._active:
            p.setPen(QtGui.QPen(QtGui.QColor(220, 230, 235, 160)))
            f = QtGui.QFont("Segoe UI", 10)
            f.setLetterSpacing(QtGui.QFont.PercentageSpacing, 102)
            p.setFont(f)
            text = self._hint_text if isinstance(self._hint_text, str) else "No track selected"
            p.drawText(r.adjusted(8, 8, -8, -8), Qt.AlignBottom | Qt.AlignLeft, text)

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Public Widget
# ─────────────────────────────────────────────────────────────────────────────
class SoundPreviewWidget(QtWidgets.QFrame):
    """
    A self-contained preview frame that hosts the audio visualizer.

    Parameters
    ----------
    media_player : QMediaPlayer-like object
        The player's output drives the visualizer. This is the same object
        your Sound tab uses for playback.
    parent : QWidget | None
        Parent widget (optional).

    Public Methods
    --------------
    set_media_player(player)  -> None
    set_audio_file(path:str) -> None
    """

    def __init__(self, media_player, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sound_preview_frame")
        # Subtle, dark, rounded frame. (Color tokens kept local to avoid globals.)
        self.setStyleSheet(
            "#sound_preview_frame {"
            "  background: #101014;"
            "  border: 1px solid #2b2b31;"
            "  border-radius: 12px;"
            "}"
        )
        self.setAccessibleName("Sound Preview Frame")
        self.setToolTip("Music visualizer")

        # Choose best available visualizer implementation
        if _AudioVisualizerUltra is not None:
            self._visualizer = _AudioVisualizerUltra(self)
        else:
            self._visualizer = _MiniBarVisualizer(self)

        # Ensure it expands nicely within layouts
        self._visualizer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # Mount
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self._visualizer, 1)

        # Wire player if provided
        if media_player is not None:
            self.set_media_player(media_player)

    # ── Public API ──────────────────────────────────────────────────────────
    def set_media_player(self, player) -> None:
        """
        Re/wire the media player after construction.
        Delegates to the inner visualizer if supported, else to the fallback.
        """
        try:
            # Premium visualizer API
            if hasattr(self._visualizer, "set_media_player"):
                self._visualizer.set_media_player(player)  # type: ignore[call-arg]
        except Exception as e:
            # In worst case, ignore but keep widget alive
            print(f"[SoundPreview] set_media_player warning: {e!r}")

    def set_audio_file(self, path: str) -> None:
        """
        Convenience pass-through; callers don't need to touch the inner widget.
        """
        try:
            if hasattr(self._visualizer, "set_audio_file"):
                self._visualizer.set_audio_file(path)  # type: ignore[call-arg]
        except Exception as e:
            print(f"[SoundPreview] set_audio_file warning: {e!r}")

    # ── Optional helpers ───────────────────────────────────────────────────
    def visualizer(self) -> QtWidgets.QWidget:
        """Return the inner visualizer widget (advanced or fallback)."""
        return self._visualizer

    def shutdown(self) -> None:
        """Best-effort cleanup hook (safe to call multiple times)."""
        try:
            if hasattr(self._visualizer, "shutdown"):
                self._visualizer.shutdown()  # type: ignore[attr-defined]
        except Exception:
            pass


__all__ = ["SoundPreviewWidget"]