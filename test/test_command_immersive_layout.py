from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest import mock

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from command import CommandTab
from Nexus import (
    FORGE_WINDOW_VIEWPORT, Nexus,
    PREVIEW_BORDER_PX, PREVIEW_FRAME_EXTRA,
    SHELL_FONT_PX, SOUND_PREVIEW_MAX_HEIGHT,
)
from project_state import ProjectStateController
import project_paths
from project_paths import ApplicationPaths, configure_application_paths
from settings_store import SettingsStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandImmersiveLayoutTests(unittest.TestCase):
    def _use_project_resources(self, root: str | Path) -> None:
        # Use the same bundled fonts/artwork as the real launcher, with isolated data.
        previous = project_paths._APPLICATION_PATHS
        configure_application_paths(replace(
            ApplicationPaths.for_project(root), resource_root=PROJECT_ROOT,
        ))
        self.addCleanup(configure_application_paths, previous)

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
            SOUND_PREVIEW_MAX_HEIGHT + PREVIEW_FRAME_EXTRA,
        )
        preview_frame.deleteLater()
        body.deleteLater()

    def test_image_preview_border_follows_the_complete_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project("Preview Geometry", custom_capitalization=True)
            window = Nexus(temp_dir)
            try:
                window.resize(1400, 900)
                window.application_stack.setCurrentWidget(window.body)
                window.show()
                self.app.processEvents()
                region_heights = set()
                for width, height in ((600, 900), (900, 600), (700, 700)):
                    source = QtGui.QPixmap(width, height)
                    source.fill(QtGui.QColor("red"))
                    window._show_image_for_tab(0, source)
                    self.app.processEvents()
                    frame = window.preview_frame
                    content = window.preview_stack.geometry()
                    region_heights.add(window.preview_region.height())
                    self.assertEqual((content.x(), content.y()), (2, 2))
                    self.assertEqual(content.width(), frame.width() - PREVIEW_FRAME_EXTRA)
                    self.assertEqual(content.height(), frame.height() - PREVIEW_FRAME_EXTRA)
                    self.assertAlmostEqual(
                        content.width() / content.height(), width / height,
                        delta=0.01,
                    )
                    displayed = window.image_preview.pixmap()
                    ratio = displayed.devicePixelRatio()
                    self.assertLessEqual(
                        abs(displayed.width() / ratio - content.width()), 1,
                    )
                    self.assertLessEqual(
                        abs(displayed.height() / ratio - content.height()), 1,
                    )
                    grabbed = frame.grab()
                    grab_ratio = grabbed.devicePixelRatio()
                    middle = grabbed.toImage().pixelColor(
                        round((PREVIEW_BORDER_PX + 5) * grab_ratio),
                        round(frame.height() * grab_ratio / 2),
                    )
                    self.assertEqual(middle.name(), "#ff0000")
                self.assertEqual(len(region_heights), 1)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()
                state.shutdown()

    def test_sound_card_keeps_buttons_visible_and_help_near_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project(
                "Amanda Miller", custom_capitalization=True
            )
            window = Nexus(temp_dir)
            try:
                window.application_stack.setCurrentWidget(window.body)
                window.show()
                for width, height in ((1400, 900), (1180, 820)):
                    window.resize(width, height)
                    self.app.processEvents()
                    window.tabbar.setCurrentIndex(1)
                    QtTest.QTest.qWait(350)
                    self.app.processEvents()

                    card = window.sound_tab.single_panel
                    button = window.sound_tab.single_action_btn
                    self.assertGreaterEqual(card.height(), 230)
                    self.assertLessEqual(
                        button.y() + button.height(), card.height()
                    )
                    self.assertFalse(window.help_anchor.isVisible())
                    self.assertLessEqual(
                        abs(window.help_icon.geometry().center().y()
                            - (window.preview_frame.mapTo(window.body, QtCore.QPoint()).y()
                               + window.preview_frame.height() // 2)),
                        1,
                    )

                    window.tabbar.setCurrentIndex(0)
                    QtTest.QTest.qWait(350)
                    self.app.processEvents()
                    self.assertEqual(
                        window.help_icon.pos(),
                        window.help_anchor.mapTo(
                            window.body, QtCore.QPoint(0, 0)
                        ),
                    )
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

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
                self.assertIsNotNone(window.html_preview)
                self.assertEqual(
                    window.html_preview.url().toString(), "about:blank"
                )
                window._initialize_project_tabs()
                self.assertEqual(window.tabbar.currentIndex(), 0)
                self.assertEqual(window.tabbar.tabText(0), "Images")
                self.assertEqual(window.page_stack.currentIndex(), 0)
                self.assertIsNot(
                    window.preview_stack.currentWidget(), window.html_preview
                )
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_images_preview_is_visible_on_first_project_ready_show(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project("Startup Preview", custom_capitalization=True)
            window = Nexus(temp_dir)
            try:
                self.assertEqual(window.tabbar.currentIndex(), 0)
                self.assertFalse(window.preview_region.isHidden())
                window.resize(1400, 900)
                window.show()
                self.app.processEvents()
                self.assertIs(window.application_stack.currentWidget(), window.body)
                self.assertTrue(window.preview_region.isVisible())
                self.assertTrue(window.preview_frame.isVisible())
                self.assertGreater(window.preview_region.height(), 0)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()
                state.shutdown()

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
                window.resize(window.width(), 650)
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
                self.assertEqual(window.help_icon.toolTip(), "")
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
        class PreviewView(QtWidgets.QWidget):
            loadFinished = QtCore.Signal(bool)

            def __init__(self) -> None:
                super().__init__()
                self._url = QtCore.QUrl()
                self._page = mock.Mock()

            def page(self):
                return self._page

            def setUrl(self, url: QtCore.QUrl) -> None:
                self._url = QtCore.QUrl(url)
                QtCore.QTimer.singleShot(
                    0,
                    lambda: self.loadFinished.emit(True),
                )

            def stop(self) -> None:
                pass

            def url(self) -> QtCore.QUrl:
                return QtCore.QUrl(self._url)

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
            preview_view = PreviewView()
            window.preview_stack.addWidget(preview_view)
            window.html_preview = preview_view
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
                window._image_tab_readiness_hide_timer.stop()
                window._hide_readiness_after_image_tab_hover()
                self.app.processEvents()

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
            preview_view = QtWidgets.QWidget()
            window.preview_stack.addWidget(preview_view)
            window.html_preview = preview_view
            try:
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
                window._restore_forge_preview_from_fullscreen()
                window.preview_stack.removeWidget(preview_view)
                preview_view.setParent(None)
                preview_view.deleteLater()
                window.html_preview = None
                window.shutdown()
                window.close()
                self.app.processEvents()

    def test_forge_window_preview_scales_above_controls_without_reloading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index = root / "preview.html"
            index.write_text(
                '<!doctype html><html><head><meta name="viewport" '
                'content="width=device-width, initial-scale=1"></head>'
                '<body><button onclick="window.previewClicks='
                '(window.previewClicks||0)+1">Preview control</button></body></html>',
                encoding="utf-8",
            )
            state = ProjectStateController(root)
            state.initialize()
            state.establish_project("Forge Viewport", custom_capitalization=True)
            self._use_project_resources(root)
            window = Nexus(root)
            try:
                window.resize(1400, 900)
                window.application_stack.setCurrentWidget(window.body)
                window._initialize_project_tabs()
                window.show()
                with QtCore.QSignalBlocker(window.tabbar):
                    window.tabbar.setCurrentIndex(3)
                window.page_stack.setCurrentIndex(3)
                window.preview_frame.show()
                window.preview_region.show()
                window.forge_tab.preview_format_panel.show()
                window.help_anchor.hide()
                window.preview_caption.hide()
                window._forge_preview_mode = "window"
                view = window._ensure_forge_preview()
                window.preview_stack.setCurrentWidget(view)
                window._update_preview_geometry()
                self.app.processEvents()

                loads = []
                load_loop = QtCore.QEventLoop()
                view.loadFinished.connect(
                    lambda ok: (loads.append(ok), load_loop.quit())
                )
                view.setUrl(QtCore.QUrl.fromLocalFile(str(index)))
                QtCore.QTimer.singleShot(3000, load_loop.quit)
                load_loop.exec()
                self.assertEqual(loads, [True])
                revision = window.project_dirty.revision

                def browser_viewport() -> dict[str, int]:
                    result = []
                    loop = QtCore.QEventLoop()
                    view.page().runJavaScript(
                        "JSON.stringify({width:innerWidth,height:innerHeight})",
                        lambda value: (result.append(value), loop.quit()),
                    )
                    QtCore.QTimer.singleShot(3000, loop.quit)
                    loop.exec()
                    self.assertTrue(result)
                    return json.loads(str(result[0]))

                window.setMinimumHeight(516)
                for size in ((1400, 900), (1180, 820), (960, 516), (960, 780), (960, 840), (1920, 600), (1400, 900)):
                    window.resize(*size)
                    QtTest.QTest.qWait(250)
                    self.app.processEvents()
                    content = window.preview_stack.size()
                    self.assertEqual(
                        window.preview_stack.pos(),
                        QtCore.QPoint(PREVIEW_BORDER_PX, PREVIEW_BORDER_PX),
                    )
                    self.assertEqual(content, view.size())
                    self.assertEqual(
                        content.width(),
                        window.preview_frame.width() - PREVIEW_FRAME_EXTRA,
                    )
                    self.assertEqual(
                        content.height(),
                        window.preview_frame.height() - PREVIEW_FRAME_EXTRA,
                    )
                    self.assertAlmostEqual(
                        content.width() / content.height(),
                        FORGE_WINDOW_VIEWPORT.width()
                        / FORGE_WINDOW_VIEWPORT.height(),
                        delta=0.02,
                    )
                    measured = browser_viewport()
                    # WebEngine's 25% zoom floor must not force a side column
                    # or enlarge the main window on a small available screen.
                    expected_width = min(FORGE_WINDOW_VIEWPORT.width(), content.width() * 4)
                    expected_height = min(FORGE_WINDOW_VIEWPORT.height(), content.height() * 4)
                    self.assertLessEqual(
                        abs(measured["width"] - expected_width), 2,
                        f"{size=} {content=} {view.size()=} "
                        f"{view.zoomFactor()=} {view.isVisible()=} {measured=} "
                        f"mode={window._forge_preview_mode} "
                        f"current_view={window.html_preview is view} "
                        f"timer={window._preview_geometry_timer.isActive()}",
                    )
                    self.assertLessEqual(
                        abs(measured["height"] - expected_height), 2
                    )
                    self.assertEqual(len(loads), 1)
                    self.assertEqual(window.project_dirty.revision, revision)
                    self.assertTrue(window.forge_controls_scroll.isVisible())
                    self.assertLessEqual(
                        window.forge_tab.width(),
                        window.forge_controls_scroll.viewport().width(),
                        f"Forge controls exceed their viewport at {size}",
                    )
                    frame_bottom = window.preview_frame.mapTo(
                        window.body,
                        QtCore.QPoint(0, window.preview_frame.height()),
                    ).y()
                    panel_top = window.forge_tab.preview_format_panel.mapTo(
                        window.body, QtCore.QPoint(0, 0),
                    ).y()
                    self.assertGreaterEqual(panel_top, frame_bottom)

                self.assertEqual(
                    window.forge_controls_scroll.verticalScrollBar().maximum(), 0,
                )
                click_position = QtCore.QPoint(10, 8)
                click_target = view.childAt(click_position)
                self.assertIsNotNone(click_target)
                QtTest.QTest.mouseClick(
                    click_target, QtCore.Qt.LeftButton,
                    pos=click_target.mapFrom(view, click_position),
                )
                QtTest.QTest.qWait(100)
                clicks = []
                click_loop = QtCore.QEventLoop()
                view.page().runJavaScript(
                    "window.previewClicks||0",
                    lambda value: (clicks.append(value), click_loop.quit()),
                )
                QtCore.QTimer.singleShot(3000, click_loop.quit)
                click_loop.exec()
                self.assertEqual(clicks, [1])

                window._enter_forge_fullscreen()
                self.app.processEvents()
                self.assertEqual(view.zoomFactor(), 1.0)
                window._restore_forge_preview_from_fullscreen()
                QtTest.QTest.qWait(250)
                self.app.processEvents()
                measured = browser_viewport()
                self.assertLessEqual(
                    abs(measured["width"] - FORGE_WINDOW_VIEWPORT.width()), 2
                )
                self.assertLessEqual(
                    abs(measured["height"] - FORGE_WINDOW_VIEWPORT.height()), 2
                )
                self.assertEqual(len(loads), 1)
                self.assertEqual(window.project_dirty.revision, revision)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()
                state.shutdown()

    def test_forge_tab_switch_keeps_controls_below_preview_and_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project("Forge layout", custom_capitalization=True)
            self._use_project_resources(temp_dir)
            window = Nexus(temp_dir)
            try:
                window.resize(1920, 1032)
                window.application_stack.setCurrentWidget(window.body)
                window._initialize_project_tabs()
                window.show()
                self.app.processEvents()
                window.tabbar.setCurrentIndex(3)
                window._forge_preview_mode = "window"
                QtTest.QTest.qWait(250)
                window.setMinimumHeight(516)
                for size in ((1920, 1032), (1400, 900), (1180, 820), (960, 516), (1920, 600), (1400, 900)):
                    window.resize(*size)
                    window._update_preview_geometry()
                    QtTest.QTest.qWait(100)
                    self.app.processEvents()
                    frame_bottom = window.preview_frame.mapTo(
                        window.body,
                        QtCore.QPoint(0, window.preview_frame.height()),
                    ).y()
                    panel_top = window.forge_tab.preview_format_panel.mapTo(
                        window.body, QtCore.QPoint(0, 0),
                    ).y()
                    self.assertGreaterEqual(panel_top, frame_bottom)
                    scroll = window.forge_controls_scroll
                    self.assertEqual(window.size(), QtCore.QSize(*size))
                    self.assertGreaterEqual(
                        window.forge_tab.height(),
                        window.forge_tab.minimumSizeHint().height(),
                    )
                    self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                    self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                    self.assertGreater(scroll.viewport().height(), 100)
                self.assertEqual(
                    window.page_stack.sizePolicy().verticalPolicy(),
                    QtWidgets.QSizePolicy.Ignored,
                )
                window.tabbar.setCurrentIndex(0)
                self.assertEqual(
                    window.page_stack.sizePolicy().verticalPolicy(),
                    window._page_stack_vertical_policy,
                )
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()
                state.shutdown()

    def test_returning_to_forge_keeps_the_loaded_preview_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            index = Path(temp_dir) / "index.html"
            index.write_text("<html></html>", encoding="utf-8")
            preview_stack = QtWidgets.QStackedWidget()
            preview_stack.addWidget(QtWidgets.QWidget())
            view = QtWidgets.QWidget()
            view.url = lambda: QtCore.QUrl.fromLocalFile(str(index))
            preview_stack.addWidget(view)

            window = mock.Mock()
            window.page_stack.currentIndex.return_value = 0
            window._tabswitch = None
            window._autosave_project_on_tab_switch.return_value = ""
            window.tabbar.tabText.return_value = "Forge"
            window.help_pop.isVisible.return_value = False
            window.html_preview = view
            window.preview_stack = preview_stack
            window.forge_tab.current_play_index.return_value = index

            with mock.patch("Nexus.QtCore.QTimer.singleShot"):
                Nexus._apply_tab_state(window, 3, animate_page=False)

            self.assertIs(preview_stack.currentWidget(), view)
            window.forge_tab.activate_for_tab_change.assert_called_once_with()

            window.forge_tab.current_play_index.return_value = None
            with mock.patch("Nexus.QtCore.QTimer.singleShot"):
                Nexus._apply_tab_state(window, 3, animate_page=False)
            self.assertIs(preview_stack.currentWidget(), preview_stack.widget(0))

    def test_first_forge_switch_shows_new_page_without_window_blink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project("Incomplete Forge", custom_capitalization=True)
            window = Nexus(temp_dir)
            try:
                window.application_stack.setCurrentWidget(window.body)
                window.show()
                self.app.processEvents()

                marker = QtWidgets.QLabel(window.image_page)
                marker.setStyleSheet("background:#ff00ff;")
                marker.setGeometry(16, 16, 48, 48)
                marker.show()
                marker.raise_()
                self.app.processEvents()
                point = marker.mapTo(window.body, QtCore.QPoint(24, 24))
                old_color = window.body.grab().toImage().pixelColor(point)
                self.assertEqual(old_color, QtGui.QColor("#ff00ff"))

                class WindowEvents(QtCore.QObject):
                    def __init__(self) -> None:
                        super().__init__()
                        self.types: list[QtCore.QEvent.Type] = []

                    def eventFilter(self, _watched, event) -> bool:
                        if event.type() in {
                            QtCore.QEvent.Hide,
                            QtCore.QEvent.Show,
                            QtCore.QEvent.WinIdChange,
                            QtCore.QEvent.WindowStateChange,
                        }:
                            self.types.append(event.type())
                        return False

                events = WindowEvents()
                window.installEventFilter(events)
                window.tabbar.setCurrentIndex(3)
                self.app.processEvents()
                self.assertTrue(window.isVisible())
                self.assertEqual(window.page_stack.currentIndex(), 3)
                self.assertIsNone(window._tabswitch._active)
                self.assertEqual(events.types, [])
                self.assertNotEqual(
                    window.body.grab().toImage().pixelColor(point),
                    old_color,
                )
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
