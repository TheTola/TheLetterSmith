from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from Nexus import _ProjectLoadingOverlay
from ui_theme import THEMES


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
        self.assertEqual(overlay.activity_mode, "restore")
        key = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_F,
            QtCore.Qt.ControlModifier,
        )
        self.assertTrue(overlay.eventFilter(owner, key))
        mouse = QtCore.QEvent(QtCore.QEvent.MouseButtonPress)
        self.assertFalse(overlay.eventFilter(owner, mouse))

        overlay.stop()
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.spinner._timer.isActive())
        owner.close()

    def test_publication_overlay_blocks_the_application_and_fades_cleanly(self) -> None:
        owner = QtWidgets.QWidget()
        owner.resize(960, 640)
        unrelated_window = QtWidgets.QWidget()
        overlay = _ProjectLoadingOverlay(owner)
        owner.show()
        unrelated_window.show()

        overlay.start(
            "Publishing Letter…",
            mode="publish",
            detail="Uploading the verified letter.",
        )
        self.app.processEvents()

        self.assertEqual(overlay.geometry(), owner.rect())
        self.assertEqual(overlay.activity_mode, "publish")
        self.assertTrue(overlay.spinner._timer.isActive())
        self.assertLess(
            overlay.spinner.geometry().bottom(),
            overlay.title.geometry().top(),
        )
        self.assertLess(
            overlay.title.geometry().bottom(),
            overlay.detail.geometry().top(),
        )
        overlay.spinner._step = 7
        overlay.start(
            "Publishing Letter…",
            mode="publish",
            detail="Verifying the public page.",
        )
        self.assertEqual(overlay.spinner._step, 7)
        mouse = QtCore.QEvent(QtCore.QEvent.MouseButtonPress)
        self.assertTrue(overlay.eventFilter(unrelated_window, mouse))

        modal = QtWidgets.QDialog(owner)
        modal.setWindowModality(QtCore.Qt.ApplicationModal)
        modal.setModal(True)
        modal.show()
        self.app.processEvents()
        modal_key = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_Escape,
            QtCore.Qt.NoModifier,
        )
        self.assertFalse(overlay.eventFilter(modal, modal_key))
        modal.hide()

        dismissed = QtTest.QSignalSpy(overlay.dismissed)
        overlay.stop(animated=True)
        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.spinner._timer.isActive())
        self.assertEqual(
            overlay._fade_animation.state(),
            QtCore.QAbstractAnimation.Running,
        )
        overlay._fade_animation.setCurrentTime(
            overlay._fade_animation.duration()
        )
        self.assertEqual(dismissed.count(), 1)
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.spinner._timer.isActive())
        self.assertFalse(overlay._filter_installed)
        unrelated_window.close()
        owner.close()

    def test_publish_and_unpublish_use_distinct_theme_derived_treatments(self) -> None:
        owner = QtWidgets.QWidget()
        owner.resize(900, 600)
        overlay = _ProjectLoadingOverlay(owner)
        owner.show()

        try:
            for definition in THEMES.values():
                with self.subTest(theme=definition.theme_id):
                    service = SimpleNamespace(
                        tokens=definition.tokens,
                        app_font_family="Segoe UI",
                    )
                    overlay.start("Publishing Letter…", mode="publish")
                    overlay.apply_theme_assets(service)
                    publish_color = QtGui.QColor(overlay._overlay_color)
                    publish_style = overlay.styleSheet()
                    publish_accent = QtGui.QColor(
                        overlay.spinner._accent_color
                    )

                    overlay.start("Unpublishing Letter…", mode="unpublish")
                    overlay.apply_theme_assets(service)
                    unpublish_color = QtGui.QColor(overlay._overlay_color)
                    unpublish_style = overlay.styleSheet()
                    unpublish_accent = QtGui.QColor(
                        overlay.spinner._accent_color
                    )

                    self.assertGreater(
                        publish_color.lightness(),
                        unpublish_color.lightness(),
                    )
                    self.assertNotEqual(publish_style, unpublish_style)
                    self.assertNotEqual(publish_accent, unpublish_accent)
        finally:
            overlay.stop()
            owner.close()


if __name__ == "__main__":
    unittest.main()
