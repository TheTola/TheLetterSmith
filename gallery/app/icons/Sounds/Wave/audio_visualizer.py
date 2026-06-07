# Legacy compatibility wrapper for Letter Smith.
#
# This file intentionally does NOT import pydub, numpy, or decode audio.
# Older builds may still import this legacy module as a fallback, so it must stay
# lightweight and warning-free. The active implementation lives in:
#
#   sound_visualizer.py
#
# Keep this file as a thin re-export only.

from __future__ import annotations

try:
    from sound_visualizer import AudioVisualizerUltra
except Exception:
    # Minimal emergency fallback so legacy imports do not crash the app.
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt

    class AudioVisualizerUltra(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._active = False
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        def set_media_player(self, _player) -> None:
            return

        def set_audio_file(self, _path: str) -> None:
            return

        def set_analysis_payload(self, _payload) -> None:
            return

        def set_active(self, active: bool) -> None:
            self._active = bool(active)
            self.update()

        def shutdown(self) -> None:
            self._active = False
            self.update()

        def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#0f1116"))
            painter.setPen(QtGui.QColor("#8f98a8"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Visualizer unavailable")
            painter.end()


__all__ = ["AudioVisualizerUltra"]
