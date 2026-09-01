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
        QtTest.QTest.qWait(230)

        self.assertEqual(blips, [True, True])
        self.assertEqual(button._countdown_label.text(), "1")
        self.assertTrue(button._countdown_label.isVisible())

        QtTest.QTest.qWait(100)
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

    def test_command_notice_reappears_and_click_dismisses_it(self) -> None:
        tab = CommandTab(PROJECT_ROOT)
        tab.resize(900, 600)
        tab.show()
        self.app.processEvents()

        notice = tab._entry_notice
        self.assertEqual(tab.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(
            tab.go_btn._countdown_label.font().family(),
            COMMAND_FONT_FAMILY,
        )
        self.assertEqual(tab.go_btn._countdown_label.font().pixelSize(), 92)
        self.assertEqual(notice.message.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(notice.message.font().pixelSize(), 15)
        self.assertEqual(
            notice.completion_message.font().family(),
            COMMAND_FONT_FAMILY,
        )
        self.assertEqual(notice.completion_message.font().pixelSize(), 30)
        _toast(tab, "Command ready", msecs=10_000)
        self.app.processEvents()
        toast = tab.findChildren(QtWidgets.QDialog)[0]
        toast_label = toast.findChild(QtWidgets.QLabel)
        self.assertEqual(toast.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(toast_label.font().family(), COMMAND_FONT_FAMILY)
        toast.close()
        self.assertEqual(notice._dismiss_timer.interval(), 10_000)
        self.assertEqual(notice._fade_timer.interval(), 6_000)
        self.assertEqual(notice._fade_animation.duration(), 4_000)
        self.assertNotIn("10 seconds", notice.message.text())
        self.assertTrue(notice.isVisible())
        self.assertEqual(
            notice.panel.palette().color(QtGui.QPalette.Window).name(),
            "#4a090c",
        )
        self.assertEqual(
            notice.message.palette().color(QtGui.QPalette.WindowText).name(),
            "#ffb0bb",
        )
        self.assertTrue(notice.message.wordWrap())
        self.assertEqual(notice.panel.width(), 836)
        self.assertLessEqual(notice.panel.height(), notice.height() - 64)
        self.assertEqual(
            notice.message_scroll.horizontalScrollBarPolicy(),
            QtCore.Qt.ScrollBarAlwaysOff,
        )

        tab.resize(480, 320)
        self.app.processEvents()
        self.assertEqual(notice.panel.width(), 416)
        self.assertLessEqual(notice.panel.height(), 256)

        QtTest.QTest.mouseClick(
            notice,
            QtCore.Qt.LeftButton,
            pos=QtCore.QPoint(10, 10),
        )
        self.assertFalse(notice.isVisible())

        tab.hide()
        tab.show()
        self.app.processEvents()
        self.assertTrue(notice.isVisible())
        notice._fade_animation.setDuration(80)
        notice._fade_timer.setInterval(10)
        notice._dismiss_timer.setInterval(200)
        notice._fade_timer.start()
        notice._dismiss_timer.start()
        QtTest.QTest.qWait(50)
        self.assertTrue(notice.isVisible())
        self.assertLess(notice._opacity_effect.opacity(), 1.0)
        self.assertGreater(notice._opacity_effect.opacity(), 0.0)
        QtTest.QTest.mouseClick(
            notice,
            QtCore.Qt.LeftButton,
            pos=QtCore.QPoint(10, 10),
        )
        self.assertFalse(notice.isVisible())
        self.assertEqual(notice._opacity_effect.opacity(), 1.0)

        tab.hide()
        tab.show()
        self.app.processEvents()
        QtTest.QTest.qWait(110)
        self.assertFalse(notice.isVisible())
        tab.close()

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

        self.assertEqual(question.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(yes_button.font().family(), COMMAND_FONT_FAMILY)
        self.assertEqual(no_button.font().family(), COMMAND_FONT_FAMILY)
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
