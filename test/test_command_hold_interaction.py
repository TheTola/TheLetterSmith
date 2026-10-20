from __future__ import annotations

import os
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from command import CommandTab, _PressGoLabel, _ShockwaveWidget, _toast
from ui_fonts import COMMAND_FONT_FAMILY


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
        button._scale_anim.setCurrentTime(70)
        self.app.processEvents()

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
        button._scale_anim.setCurrentTime(button._scale_anim.duration())
        self.app.processEvents()

        self.assertEqual(activations, [True])
        self.assertFalse(button._holding)
        self.assertEqual(button._scale, 1.0)
        button.close()

    def test_completed_hold_activates_reset_once_without_click(self) -> None:
        button = self._button()
        clicks, holds = [], []
        button.clicked.connect(lambda: clicks.append(True))
        button.held.connect(lambda: holds.append(True))
        QtTest.QTest.mousePress(button, QtCore.Qt.LeftButton, pos=button.rect().center())
        button._hold_timer.stop()
        button._complete_hold()
        button._complete_hold()
        QtTest.QTest.mouseRelease(button, QtCore.Qt.LeftButton, pos=button.rect().center())
        self.assertEqual(holds, [True])
        self.assertEqual(clicks, [])
        self.assertEqual(button._scale, 1.0)
        button.close()

    def test_leaving_or_hiding_cancels_hold(self) -> None:
        button = self._button()
        holds = []
        button.held.connect(lambda: holds.append(True))
        for event in (QtCore.QEvent.Leave, QtCore.QEvent.Hide):
            button._start_hold()
            self.app.sendEvent(button, QtCore.QEvent(event))
            button._complete_hold()
        self.assertEqual(holds, [])
        button.close()

    def test_hold_plays_blip_on_each_completed_second(self) -> None:
        button = self._button()
        button.HOLD_DURATION_MS = 300
        blips = []
        button._play_countdown_sound = mock.Mock(
            side_effect=lambda: blips.append(True)
        )

        QtTest.QTest.mousePress(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        button._countdown_timer.stop()
        button._advance_countdown()
        button._advance_countdown()

        self.assertEqual(blips, [True, True])
        self.assertEqual(button._countdown_label.text(), "1")
        self.assertTrue(button._countdown_label.isVisible())

        button._hold_timer.stop()
        button._complete_hold()
        self.assertEqual(blips, [True, True, True])
        self.assertFalse(button._countdown_label.isVisible())

        QtTest.QTest.mouseRelease(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        self.assertFalse(button._countdown_label.isVisible())
        button.close()

    def test_command_uses_relative_blip_app_resource(self) -> None:
        tab = CommandTab(PROJECT_ROOT)
        player = tab.go_btn._countdown_player

        self.assertIsNotNone(player)
        self.assertIsNotNone(tab.go_btn._countdown_output)
        self.assertEqual(tab.go_btn._countdown_output.volume(), 1.0)
        self.assertEqual(
            Path(player.source().toLocalFile()).resolve(),
            (
                PROJECT_ROOT
                / "gallery"
                / "app"
                / "sounds"
                / "App sounds"
                / "Blip.mp3"
            ).resolve(),
        )
        tab.close()

    def test_command_has_only_go_without_entry_notice(self) -> None:
        tab = CommandTab(PROJECT_ROOT)
        tab.resize(900, 600)
        tab.show()
        self.app.processEvents()
        self.assertEqual(tab.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(tab.findChildren(QtWidgets.QPushButton), [])
        self.assertFalse(hasattr(tab, "_entry_notice"))
        self.assertIn("Click GO to save", tab.go_btn.toolTip())
        tab.close()


if __name__ == "__main__":
    unittest.main()
