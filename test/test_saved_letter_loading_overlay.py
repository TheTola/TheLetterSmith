from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from Nexus import _ProjectLoadingOverlay


class SavedLetterLoadingOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_overlay_animates_blocks_input_and_stops_cleanly(self) -> None:
        owner = QtWidgets.QWidget()
        owner.resize(900, 600)
        overlay = _ProjectLoadingOverlay(owner)
        overlay.setGeometry(owner.rect())
        owner.show()

        overlay.start("Loading Saved Recipient…")
        self.app.processEvents()

        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.spinner._timer.isActive())
        self.assertEqual(overlay.title.text(), "Loading Saved Recipient…")
        key = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_F,
            QtCore.Qt.ControlModifier,
        )
        self.assertTrue(overlay.eventFilter(owner, key))

        overlay.stop()
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.spinner._timer.isActive())
        owner.close()


if __name__ == "__main__":
    unittest.main()
