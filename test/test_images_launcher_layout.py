"""Actual shell checks for the themed Prompt Writer artwork and its layout."""
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PySide6 import QtCore, QtTest, QtWidgets

import test_window_sizing as sizing
from project_model import PAGE_TYPES, create_project
from recent_media import recent_type_images, remember_type_image, recent_media, remember_media
from ui_theme import THEMES


class ImagesLauncherLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sizing.WindowSizingTests.setUpClass()

    def test_command_click_clears_only_shared_image_history_including_repeated_clicks(self):
        helper = sizing.WindowSizingTests()
        with helper.window() as window:
            root = Path(window.project_root)
            source = root / "synthetic-artwork.png"
            Image.new("RGB", (24, 36), "navy").save(source)
            tab = window.image_tab
            tab.session.replace(create_project(), asset_root=root)
            identity = tab.session.project.pages[0]["id"]
            tab.session.select_page(identity)
            self.assertTrue(tab.import_image(identity, str(source)))
            elapsed = QtCore.QElapsedTimer()
            elapsed.start()
            while tab._image_import_thread is not None and elapsed.elapsed() < 8000:
                QtTest.QTest.qWait(10)
            self.assertIsNone(tab._image_import_thread)
            artwork = tab.artwork.resolve_asset(identity)
            self.assertIsNotNone(artwork)
            self.assertTrue(recent_type_images(root, "cover"))
            project = tab.session.project.to_dict()
            identity = tab.session.project.project_id
            remember_media(root, identity, "music", "synthetic-track", "Track")
            for kind in PAGE_TYPES:
                remember_type_image(root, kind, source, kind)
            originals = {source: source.read_bytes(), artwork: artwork.read_bytes()}
            # Restoring a tab is not a user request to clear history.
            window.tabbar.setCurrentIndex(4)
            helper.settle(window)
            self.assertTrue(recent_type_images(root, "cover"))
            for _ in range(2):
                retained = {Path(p): Path(p).read_bytes() for kind in PAGE_TYPES
                            for p, _ in recent_type_images(root, kind)}
                retained.update(originals)
                QtTest.QTest.mouseClick(window.tabbar, QtCore.Qt.LeftButton,
                                       pos=window.tabbar.tabRect(4).center())
                helper.settle(window)
                for kind in PAGE_TYPES:
                    self.assertEqual(recent_type_images(root, kind), ())
                self.assertEqual(tab.session.project.to_dict(), project)
                self.assertEqual({p: p.read_bytes() for p in retained}, retained)
                self.assertEqual(recent_media(root, identity, "music"), (("synthetic-track", "Track"),))
                remember_type_image(root, "cover", source, "Cover")
            with mock.patch("recent_media.atomic_write_json", side_effect=OSError("Synthetic write failure")), \
                    mock.patch.object(window, "status") as status:
                QtTest.QTest.mouseClick(window.tabbar, QtCore.Qt.LeftButton,
                                       pos=window.tabbar.tabRect(4).center())
                self.assertTrue(recent_type_images(root, "cover"))
                status.assert_called_with("Image history could not be cleared. Try Command again.")

    def test_launcher_fits_all_themes_and_short_windows(self):
        helper = sizing.WindowSizingTests()
        with helper.window() as window:
            tab = window.image_tab
            for theme in THEMES:
                window.theme_service.set_theme(theme, persist=False)
                for width, height in ((1920, 1080), (1200, 820), (960, 600), (900, 480), (1200, 820)):
                    with self.subTest(theme=theme, size=(width, height)):
                        window.resize(width, height)
                        helper.settle(window)
                        self.assertEqual(window.size(), QtCore.QSize(width, height))
                        self.assertFalse(tab.pwrite_fab.icon().isNull())
                        self.assertEqual(tab.pwrite_fab.toolButtonStyle(), QtCore.Qt.ToolButtonIconOnly)
                        scroll = window.image_controls_scroll
                        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                        for control in (tab.pwrite_fab, tab.name_edit, tab.type_combo, tab.add_button,
                                        tab.page_actions_button):
                            rect = QtCore.QRect(control.mapTo(scroll.viewport(), QtCore.QPoint()), control.size())
                            self.assertTrue(scroll.viewport().rect().contains(rect), (control.accessibleName(), rect))
                        self.assertEqual(tab.page_list.viewMode(), QtWidgets.QListView.IconMode)
                        self.assertFalse(tab.creation_panel.isVisible())
                        preview_rect = QtCore.QRect(tab.page_list.mapTo(tab, QtCore.QPoint()), tab.page_list.size())
                        launcher_rect = QtCore.QRect(tab.pwrite_fab.mapTo(tab, QtCore.QPoint()), tab.pwrite_fab.size())
                        self.assertFalse(preview_rect.intersects(launcher_rect))
                        self.assertTrue(tab.add_button.isVisibleTo(window))
                        self.assertLessEqual(abs(tab._identity_panel.mapTo(window, tab._identity_panel.rect().center()).x()
                                                 - window.rect().center().x()), 1)
                        launcher_top = tab.pwrite_fab.mapTo(window, QtCore.QPoint()).y()
                        tabs_bottom = window.tabbar.mapTo(window, window.tabbar.rect().bottomLeft()).y()
                        self.assertLessEqual(launcher_top - tabs_bottom, 32)
                        help_rect = QtCore.QRect(tab.mapFromGlobal(window.help_icon.mapToGlobal(QtCore.QPoint())),
                                                window.help_icon.size())
                        for control in (tab.pwrite_fab, tab._identity_panel, tab.page_list):
                            rect = QtCore.QRect(control.mapTo(tab, QtCore.QPoint()), control.size())
                            self.assertFalse(help_rect.intersects(rect),
                                             (type(control).__name__, help_rect, rect))
