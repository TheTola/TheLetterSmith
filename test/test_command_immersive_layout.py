from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from command import CommandTab
from Nexus import Nexus, SHELL_FONT_PX, SOUND_PREVIEW_MAX_HEIGHT
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

    @classmethod
    def _release_help_movies(cls, window: Nexus) -> None:
        window.help_icon.clear()
        for attribute in ("_help_movie_idle", "_help_movie_hover"):
            movie = getattr(window, attribute)
            if movie is not None:
                movie.stop()
                movie.setFileName("")
                movie.deleteLater()
                setattr(window, attribute, None)
        cls.app.processEvents()

    def test_maximized_sound_preview_keeps_native_height_cap(self) -> None:
        preview_frame = QtWidgets.QWidget()
        body = QtWidgets.QWidget()
        body.resize(1896, 936)
        harness = mock.Mock()
        harness._forge_fullscreen_active = False
        harness.height.return_value = 1080
        harness.width.return_value = 1920
        harness.body = body
        harness.tabbar.currentIndex.return_value = 1
        harness.preview_frame = preview_frame

        Nexus._update_preview_geometry(harness)

        self.assertEqual(
            preview_frame.height(),
            SOUND_PREVIEW_MAX_HEIGHT + 12,
        )
        preview_frame.deleteLater()
        body.deleteLater()

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

    def test_curtain_preparation_is_delayed_and_uses_a_worker_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "gallery" / "user" / "pages" / "cover.png"
            cover.parent.mkdir(parents=True)
            cover.write_bytes(b"cover")
            window = Nexus(root)
            forge_previews = mock.Mock()
            window.forge_tab = mock.Mock(
                set_curtain_preview_colors=forge_previews,
            )
            try:
                with (
                    mock.patch.object(
                        window.title_bar,
                        "set_curtain_preview_colors",
                    ) as title_bar_previews,
                ):
                    window._schedule_curtain_preparation()
                title_bar_previews.assert_not_called()
                forge_previews.assert_not_called()
                self.assertEqual(
                    window._curtain_preparation_timer.interval(),
                    5000,
                )
                self.assertTrue(window._curtain_preparation_timer.isActive())
                self.assertEqual(
                    window._curtain_preparation_pool.maxThreadCount(),
                    1,
                )
                window._curtain_preparation_timer.stop()
                with mock.patch.object(
                    window,
                    "_start_curtain_preparation",
                ) as start_preparation:
                    window._schedule_curtain_preparation(immediate=True)
                start_preparation.assert_called_once_with()
                self.assertFalse(window._curtain_preparation_timer.isActive())
            finally:
                window.close()

    def test_startup_always_selects_images_tab(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            SettingsStore(temp_dir).update_fields({"ui_last_tab": "Forge"})
            window = Nexus(temp_dir)
            try:
                self.assertIsNone(window.html_preview)
                window._initialize_project_tabs()
                self.assertEqual(window.tabbar.currentIndex(), 0)
                self.assertEqual(window.tabbar.tabText(0), "Images")
                self.assertEqual(window.page_stack.currentIndex(), 0)
                self.assertIsNone(window.html_preview)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_main_tab_labels_are_bold_in_every_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = Nexus(temp_dir)
            try:
                for theme in window.theme_service.available_themes():
                    window.theme_service.set_theme(theme.theme_id, persist=False)
                    stylesheet = window._build_nexus_theme_stylesheet()
                    with self.subTest(theme_id=theme.theme_id):
                        self.assertIn("font-weight:700;", stylesheet)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_maximize_restore_button_uses_direct_qt_clicks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = Nexus(temp_dir)
            try:
                window.show()
                self.app.processEvents()
                self.assertTrue(callable(window.title_bar.parent))
                self.assertIs(window.title_bar.parent(), window.main_widget)
                self.assertIsNone(window._window_controller._maximize_button)
                self.assertFalse(window.isMaximized())
                original_geometry = QtCore.QRect(window.geometry())

                QtTest.QTest.mouseClick(
                    window.title_bar.btn_max,
                    QtCore.Qt.LeftButton,
                )
                self.app.processEvents()
                self.assertTrue(window.isMaximized())
                self.assertEqual(
                    window.title_bar.btn_max.accessibleName(),
                    "Restore window",
                )

                QtTest.QTest.mouseClick(
                    window.title_bar.btn_max,
                    QtCore.Qt.LeftButton,
                )
                self.app.processEvents()
                self.assertFalse(window.isMaximized())
                self.assertEqual(window.geometry(), original_geometry)
                self.assertEqual(
                    window.title_bar.btn_max.accessibleName(),
                    "Maximize window",
                )

                QtTest.QTest.mouseClick(
                    window.title_bar.btn_max,
                    QtCore.Qt.LeftButton,
                )
                self.app.processEvents()
                window.title_bar._normal_window_geometry = None
                QtTest.QTest.mouseClick(
                    window.title_bar.btn_max,
                    QtCore.Qt.LeftButton,
                )
                self.app.processEvents()
                self.assertFalse(window.isMaximized())
                self.assertEqual(window.geometry(), original_geometry)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_cover_preview_frame_uses_each_theme_rectangle_color(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = Nexus(temp_dir)
            try:
                for theme in window.theme_service.available_themes():
                    window.theme_service.set_theme(theme.theme_id, persist=False)
                    stylesheet = window._build_nexus_theme_stylesheet()
                    with self.subTest(theme_id=theme.theme_id):
                        self.assertGreaterEqual(
                            stylesheet.count(f"font-size:{SHELL_FONT_PX}px;"),
                            2,
                        )
                        self.assertIn(
                            "border:2px solid "
                            f"{theme.tokens.preview_frame_border};",
                            stylesheet,
                        )
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_help_hover_movie_is_created_only_when_first_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            idle_gif = root / "idle.gif"
            hover_gif = root / "hover.gif"
            for path, color in (
                (idle_gif, "#4a90e2"),
                (hover_gif, "#d85c6a"),
            ):
                frames = [
                    Image.new("RGB", (4, 4), color),
                    Image.new("RGB", (4, 4), "#ffffff"),
                ]
                frames[0].save(
                    path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=100,
                    loop=0,
                )
            window = Nexus(root)
            try:
                window._help_movie_idle_path = str(idle_gif)
                window._help_movie_hover_path = str(hover_gif)

                self.assertTrue(window._show_help_movie("idle"))
                self.assertIsNotNone(window._help_movie_idle)
                self.assertIsNone(window._help_movie_hover)
                self.assertEqual(
                    window._help_movie_idle.state(),
                    QtGui.QMovie.Running,
                )

                self.assertTrue(window._show_help_movie("hover"))
                self.assertEqual(
                    window._help_movie_idle.state(),
                    QtGui.QMovie.NotRunning,
                )
                self.assertEqual(
                    window._help_movie_hover.state(),
                    QtGui.QMovie.Running,
                )
            finally:
                window.shutdown()
                self._release_help_movies(window)
                window.close()
                self.app.processEvents()

    def test_static_help_state_replaces_and_stops_an_active_gif(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            idle_gif = root / "Help.gif"
            hover_png = root / "HHelp.png"
            frames = [
                Image.new("RGB", (4, 4), "#4a90e2"),
                Image.new("RGB", (4, 4), "#ffffff"),
            ]
            frames[0].save(
                idle_gif,
                save_all=True,
                append_images=frames[1:],
                duration=100,
                loop=0,
            )
            image = QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#4a90e2"))
            self.assertTrue(image.save(str(hover_png)))
            window = Nexus(root)
            try:
                window._help_movie_idle_path = str(idle_gif)
                window._help_movie_hover_path = ""
                window._help_static_hover_path = str(hover_png)

                self.assertTrue(window._show_help_asset("idle"))
                idle_movie = window._help_movie_idle
                self.assertIsNotNone(idle_movie)
                self.assertEqual(idle_movie.state(), QtGui.QMovie.Running)

                self.assertTrue(window._show_help_asset("hover"))
                self.assertEqual(idle_movie.state(), QtGui.QMovie.NotRunning)
                self.assertIsNone(window.help_icon.movie())
                self.assertFalse(window.help_icon.pixmap().isNull())
            finally:
                window.shutdown()
                self._release_help_movies(window)
                window.close()
                self.app.processEvents()

    def test_immersive_shell_covers_everything_below_title_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            SettingsStore(temp_dir).update_fields(
                {"protected_project_kind": "stock"}
            )
            window = Nexus(temp_dir)
            window.resize(1200, 820)
            window.application_stack.setCurrentWidget(window.body)
            window.preview_frame.hide()
            window.preview_caption.hide()
            window.help_icon.hide()
            window.show()
            self.app.processEvents()
            window._sync_protected_project_ui()
            self.assertTrue(window.protected_new_project_btn.isVisible())

            window._set_command_immersive(True)
            self.app.processEvents()
            QtWidgets.QApplication.sendEvent(
                window.title_bar,
                QtCore.QEvent(QtCore.QEvent.Leave),
            )
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
            self.assertFalse(window.protected_new_project_btn.isVisible())
            for theme in window.theme_service.available_themes():
                window.theme_service.set_theme(theme.theme_id, persist=False)
                window.title_bar.apply_theme(window.theme_service)
                with self.subTest(theme_id=theme.theme_id):
                    self.assertIn(
                        "background:#000000",
                        window.title_bar.styleSheet(),
                    )
                    self.assertIn(
                        "color:#000000",
                        window.title_bar.title_label.styleSheet(),
                    )
                    for widget in (
                        window.title_bar.app_icon,
                        window.title_bar.settings_button,
                        window.title_bar.settings_window_divider_container,
                        window.title_bar.btn_minimize,
                        window.title_bar.btn_max,
                        window.title_bar.btn_close,
                    ):
                        self.assertFalse(widget.isVisible())

            QtWidgets.QApplication.sendEvent(
                window.title_bar,
                QtCore.QEvent(QtCore.QEvent.Enter),
            )
            self.app.processEvents()
            self.assertNotIn(
                "color:#000000",
                window.title_bar.title_label.styleSheet(),
            )
            for widget in (
                window.title_bar.settings_button,
                window.title_bar.settings_window_divider_container,
                window.title_bar.btn_minimize,
                window.title_bar.btn_max,
                window.title_bar.btn_close,
            ):
                self.assertTrue(widget.isVisible())

            window._set_command_immersive(False)
            self.app.processEvents()

            margins = window.body_layout.contentsMargins()
            self.assertEqual(window.main_layout.indexOf(window.tabbar), 1)
            self.assertFalse(window.tabbar.property("commandOverlay"))
            self.assertEqual(
                (margins.left(), margins.top(), margins.right(), margins.bottom()),
                (12, 12, 12, 12),
            )
            self.assertTrue(window.protected_new_project_btn.isVisible())
            self.assertTrue(window.title_bar.settings_button.isVisible())
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
                preview_view = window._ensure_forge_preview()
                loaded = False
                load_loop = QtCore.QEventLoop()
                load_timeout = QtCore.QTimer()
                load_timeout.setSingleShot(True)
                load_timeout.timeout.connect(load_loop.quit)

                def finish_load(ok: bool) -> None:
                    nonlocal loaded
                    loaded = bool(ok)
                    load_loop.quit()

                preview_view.loadFinished.connect(finish_load)
                preview_view.setUrl(
                    QtCore.QUrl.fromLocalFile(str(index))
                )
                load_timeout.start(3000)
                load_loop.exec()
                preview_view.loadFinished.disconnect(finish_load)
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
                    preview_view.url(),
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
                preview_view = window._ensure_forge_preview()
                window.resize(1200, 820)
                window.application_stack.setCurrentWidget(window.body)
                window.preview_stack.setCurrentWidget(preview_view)
                window.show()
                self.app.processEvents()
                self.assertTrue(preview_view.isVisible())

                window._enter_forge_fullscreen()
                self.app.processEvents()

                fullscreen = window._forge_fullscreen_window
                self.assertIsNotNone(fullscreen)
                self.assertTrue(fullscreen.isFullScreen())
                self.assertIs(preview_view.parentWidget(), fullscreen)
                self.assertTrue(preview_view.isVisible())
                self.assertFalse(preview_view.size().isEmpty())

                window._restore_forge_preview_from_fullscreen()
                self.app.processEvents()
                self.assertIs(preview_view.parentWidget(), window.preview_stack)
                self.assertTrue(preview_view.isVisible())
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
