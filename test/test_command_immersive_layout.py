from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtTest, QtWidgets

from command import CommandTab
from Nexus import Nexus
from project_state import ProjectStateController
from settings_store import SettingsStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandImmersiveLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def test_command_and_go_share_exact_display_canvas(self) -> None:
        tab = CommandTab(PROJECT_ROOT)
        try:
            for size in (
                QtCore.QSize(900, 600),
                QtCore.QSize(1600, 900),
                QtCore.QSize(1915, 1025),
            ):
                tab.resize(size)
                tab.show()
                self.app.processEvents()

                self.assertEqual(tab.bg_label.pixmap().size(), size)
                self.assertEqual(tab.go_btn._pix_base.size(), size)
                self.assertEqual(
                    tab.go_btn.geometry(),
                    QtCore.QRect(QtCore.QPoint(), size),
                )
        finally:
            tab.close()

    def test_immersive_shell_covers_everything_below_title_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = Nexus(temp_dir)
            window.resize(1200, 820)
            window.application_stack.setCurrentWidget(window.body)
            window.preview_frame.hide()
            window.preview_caption.hide()
            window.help_icon.hide()
            window.show()
            self.app.processEvents()

            window._set_command_immersive(True)
            self.app.processEvents()

            margins = window.body_layout.contentsMargins()
            content_top = window.application_stack.geometry().top()
            self.assertEqual(
                content_top,
                window.title_bar.geometry().bottom() + 1,
            )
            self.assertEqual(
                window.page_stack.geometry(),
                window.body.rect(),
            )
            self.assertEqual(
                (margins.left(), margins.top(), margins.right(), margins.bottom()),
                (0, 0, 0, 0),
            )
            self.assertEqual(window.main_layout.indexOf(window.tabbar), -1)
            self.assertTrue(window.tabbar.property("commandOverlay"))
            self.assertEqual(window.tabbar.geometry().top(), content_top)
            self.assertEqual(
                window.tabbar.geometry().width(),
                window.main_widget.width(),
            )
            self.assertEqual(
                window.tabbar.tabAt(window.tabbar.tabRect(0).center()),
                0,
            )
            self.assertFalse(window.statusBar().isVisible())

            window._set_command_immersive(False)
            self.app.processEvents()

            margins = window.body_layout.contentsMargins()
            self.assertEqual(window.main_layout.indexOf(window.tabbar), 1)
            self.assertFalse(window.tabbar.property("commandOverlay"))
            self.assertEqual(
                (margins.left(), margins.top(), margins.right(), margins.bottom()),
                (12, 12, 12, 12),
            )
            window.close()

    def test_forge_preview_is_unloaded_before_directory_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview = root / "preview"
            preview.mkdir()
            index = preview / "index.html"
            index.write_text(
                "<!doctype html><html><body>preview</body></html>",
                encoding="utf-8",
            )
            window = Nexus(root)
            window._initialize_project_tabs()
            try:
                loaded = False
                load_loop = QtCore.QEventLoop()
                load_timeout = QtCore.QTimer()
                load_timeout.setSingleShot(True)
                load_timeout.timeout.connect(load_loop.quit)

                def finish_load(ok: bool) -> None:
                    nonlocal loaded
                    loaded = bool(ok)
                    load_loop.quit()

                window.html_preview.loadFinished.connect(finish_load)
                window.html_preview.setUrl(
                    QtCore.QUrl.fromLocalFile(str(index))
                )
                load_timeout.start(3000)
                load_loop.exec()
                window.html_preview.loadFinished.disconnect(finish_load)
                self.assertTrue(loaded)

                window.forge_tab._busy = True
                window._show_forge_preview()
                self.assertEqual(
                    window.preview_caption.text(),
                    "Preparing interactive preview…",
                )
                self.assertEqual(window.preview_stack.currentIndex(), 0)
                window.forge_tab._busy = False

                window._release_forge_preview_files()
                self.assertEqual(
                    window.html_preview.url(),
                    QtCore.QUrl("about:blank"),
                )
                backup = root / "preview.build-backup"
                os.replace(preview, backup)
                self.assertTrue(backup.is_dir())
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_image_tab_hover_hides_review_without_switching_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            SettingsStore(temp_dir).update_fields(
                {"recipient_title": "Picture Letter"}
            )
            window = Nexus(temp_dir)
            try:
                window.resize(1200, 820)
                window.application_stack.setCurrentWidget(window.body)
                window.show()
                window.tabbar.show()
                window.tabbar.setCurrentIndex(3)
                window.forge_tab._readiness_requested = True
                window.forge_tab.readiness_window.show()
                self.app.processEvents()
                self.assertTrue(window.forge_tab.readiness_window.isVisible())

                QtTest.QTest.mouseMove(
                    window.tabbar,
                    window.tabbar.tabRect(0).center(),
                )
                self.assertTrue(
                    window._image_tab_readiness_hide_timer.isActive()
                )
                hover_switch = getattr(
                    window.tabbar,
                    "_anima_hover_tab_switch",
                )
                hover_switch._cancel()
                QtTest.QTest.qWait(1050)

                self.assertEqual(window.tabbar.currentIndex(), 3)
                self.assertFalse(window.forge_tab.readiness_window.isVisible())
                self.assertFalse(window.forge_tab._readiness_requested)

                window.forge_tab._readiness_requested = True
                window.forge_tab.readiness_window.show()
                self.app.processEvents()
                window.tabbar.setCurrentIndex(0)
                self.app.processEvents()
                self.assertFalse(window.forge_tab.readiness_window.isVisible())
                self.assertFalse(window.forge_tab._readiness_requested)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_forge_fullscreen_keeps_web_preview_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = Nexus(temp_dir)
            window._initialize_project_tabs()
            try:
                window.resize(1200, 820)
                window.application_stack.setCurrentWidget(window.body)
                window.preview_stack.setCurrentWidget(window.html_preview)
                window.show()
                self.app.processEvents()
                self.assertTrue(window.html_preview.isVisible())

                window._enter_forge_fullscreen()
                self.app.processEvents()

                fullscreen = window._forge_fullscreen_window
                self.assertIsNotNone(fullscreen)
                self.assertTrue(fullscreen.isFullScreen())
                self.assertIs(window.html_preview.parentWidget(), fullscreen)
                self.assertTrue(window.html_preview.isVisible())
                self.assertFalse(window.html_preview.size().isEmpty())

                window._restore_forge_preview_from_fullscreen()
                self.app.processEvents()
                self.assertIs(window.html_preview.parentWidget(), window.preview_stack)
                self.assertTrue(window.html_preview.isVisible())
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
