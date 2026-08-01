from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from command import CommandTab, _PressGoLabel, _ShockwaveWidget


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandHoldInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def _button(self) -> _PressGoLabel:
        button = _PressGoLabel()
        pixmap = QtGui.QPixmap(400, 200)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.fillRect(
            100,
            50,
            200,
            100,
            QtGui.QColor("red"),
        )
        painter.end()
        button.set_base(
            QtCore.QRect(0, 0, 400, 200),
            pixmap,
        )
        button.show()
        self.app.processEvents()
        return button

    def test_default_hold_duration_is_three_seconds(self) -> None:
        self.assertEqual(_PressGoLabel.HOLD_DURATION_MS, 3000)

    def test_early_release_cancels_and_keeps_full_hit_target(self) -> None:
        button = self._button()
        button.HOLD_DURATION_MS = 180
        activations = []
        button.clicked.connect(lambda: activations.append(True))

        QtTest.QTest.mousePress(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        QtTest.QTest.qWait(70)

        self.assertTrue(button._holding)
        self.assertTrue(button._use_gray)
        self.assertGreater(button._scale, 0.38)
        self.assertLess(button._scale, 1.0)
        gray_pixel = button.pixmap().toImage().pixelColor(
            button.pixmap().rect().center()
        )
        self.assertEqual(gray_pixel.red(), gray_pixel.green())
        self.assertEqual(gray_pixel.green(), gray_pixel.blue())
        self.assertEqual(
            button.geometry(),
            QtCore.QRect(0, 0, 400, 200),
        )

        QtTest.QTest.mouseRelease(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        QtTest.QTest.qWait(220)

        self.assertEqual(activations, [])
        self.assertFalse(button._holding)
        self.assertEqual(button._scale, 1.0)
        button.close()

    def test_completed_hold_shrinks_bursts_and_activates_once(self) -> None:
        button = self._button()
        button.HOLD_DURATION_MS = 120
        button.BURST_DURATION_MS = 70
        activations = []
        button.clicked.connect(lambda: activations.append(True))

        QtTest.QTest.mousePress(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        QtTest.QTest.qWait(80)
        shrinking_scale = button._scale
        QtTest.QTest.qWait(60)

        self.assertLess(shrinking_scale, 1.0)
        self.assertFalse(button._holding)
        self.assertTrue(button._hold_completed)
        self.assertEqual(
            button._burst_anim.state(),
            QtCore.QAbstractAnimation.Running,
        )

        QtTest.QTest.qWait(100)
        QtTest.QTest.mouseRelease(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )

        self.assertEqual(activations, [True])
        self.assertEqual(button._scale, 1.0)
        self.assertFalse(button._use_gray)
        button.close()

    def test_confirmation_colors_and_shockwave(self) -> None:
        tab = CommandTab(PROJECT_ROOT)
        tab.resize(900, 600)
        tab.show()
        self.app.processEvents()

        tab._do_reset()
        self.app.processEvents()

        dialog = tab._confirm_dialog
        question = dialog.findChild(QtWidgets.QLabel, "question")
        yes_button = dialog.findChild(QtWidgets.QPushButton, "danger")
        no_button = dialog.findChild(QtWidgets.QPushButton, "cancel")
        waves = tab.findChildren(_ShockwaveWidget)

        self.assertEqual(
            question.palette().color(QtGui.QPalette.WindowText).name(),
            "#ff4d4f",
        )
        self.assertEqual(
            yes_button.palette().color(QtGui.QPalette.ButtonText).name(),
            "#ff4d4f",
        )
        self.assertEqual(
            no_button.palette().color(QtGui.QPalette.ButtonText).name(),
            "#00e5ff",
        )
        self.assertEqual(len(waves), 1)
        self.assertTrue(waves[0].isVisible())
        self.assertTrue(tab.go_btn._busy)
        self.assertTrue(tab.go_btn._use_gray)

        dialog.reject()
        self.app.processEvents()
        tab.close()


if __name__ == "__main__":
    unittest.main()
