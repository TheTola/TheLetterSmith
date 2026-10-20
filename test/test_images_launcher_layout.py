"""Actual shell checks for the themed Prompt Writer artwork and its layout."""
import unittest

from PySide6 import QtCore, QtWidgets

import test_window_sizing as sizing
from ui_theme import THEMES


class ImagesLauncherLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sizing.WindowSizingTests.setUpClass()

    def test_launcher_fits_all_themes_and_short_windows(self):
        helper = sizing.WindowSizingTests()
        with helper.window() as window:
            tab = window.image_tab
            for theme in THEMES:
                window.theme_service.set_theme(theme, persist=False)
                for width, height in ((1200, 820), (960, 600), (900, 480), (1200, 820)):
                    with self.subTest(theme=theme, size=(width, height)):
                        window.resize(width, height)
                        helper.settle(window)
                        self.assertEqual(window.size(), QtCore.QSize(width, height))
                        self.assertFalse(tab.pwrite_fab.icon().isNull())
                        self.assertEqual(tab.pwrite_fab.toolButtonStyle(), QtCore.Qt.ToolButtonIconOnly)
                        scroll = window.image_controls_scroll
                        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                        for control in (tab.pwrite_fab, tab.name_edit, tab.type_combo):
                            rect = QtCore.QRect(control.mapTo(scroll.viewport(), QtCore.QPoint()), control.size())
                            self.assertTrue(scroll.viewport().rect().contains(rect), (control.accessibleName(), rect))
                        self.assertEqual(tab.page_list.viewMode(), QtWidgets.QListView.IconMode)
                        self.assertFalse(tab.creation_panel.isVisible())
                        preview_rect = QtCore.QRect(tab.page_list.mapTo(tab, QtCore.QPoint()), tab.page_list.size())
                        launcher_rect = QtCore.QRect(tab.pwrite_fab.mapTo(tab, QtCore.QPoint()), tab.pwrite_fab.size())
                        self.assertFalse(preview_rect.intersects(launcher_rect))
