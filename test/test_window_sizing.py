from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

import project_paths
from Nexus import Nexus
from curtain_controls import CurtainStyleComboBox, CurtainStyleModel
from project_paths import ApplicationPaths, configure_application_paths
from project_state import ProjectStateController
from settings_store import SettingsStore
from ui_theme import THEMES, install_button_text_guard
from window_chrome import bounded_window_geometry, fit_window_to_screen


ROOT = Path(__file__).resolve().parents[1]


class WindowSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        install_button_text_guard(cls.app)

    @contextmanager
    def window(self, *, settings=None, populated=True):
        previous = project_paths._APPLICATION_PATHS
        with tempfile.TemporaryDirectory(prefix="lettersmith-sizing-") as directory:
            configure_application_paths(replace(
                ApplicationPaths.for_project(directory), resource_root=ROOT,
            ))
            state = ProjectStateController(directory)
            state.initialize()
            if populated:
                state.establish_project("Resize validation", custom_capitalization=True)
            if settings:
                SettingsStore(directory).update_fields(settings)
            sound_patch = mock.patch("ui_sounds.UiSoundPlayer.play", return_value=False)
            sound_patch.start()
            # Layout checks use bundled artwork/fonts but do not scan the stock catalogue.
            stock_patch = mock.patch.object(
                ApplicationPaths, "stock_letters_root", new_callable=mock.PropertyMock,
                return_value=Path(directory) / "fixture-stock-letters",
            )
            stock_patch.start()
            account_patch = mock.patch("Forge_Tab.ForgeTab._validate_github_account_async")
            account_patch.start()
            window = Nexus(directory)
            try:
                window.show()
                QtTest.QTest.qWait(80)
                yield window
            finally:
                window.shutdown()
                window.close()
                window.deleteLater()
                self.app.processEvents()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                state.shutdown()
                configure_application_paths(previous)
                sound_patch.stop()
                stock_patch.stop()
                account_patch.stop()

    def settle(self, window: Nexus | None = None) -> None:
        QtTest.QTest.qWait(50)
        self.app.processEvents()
        if window is None or window.tabbar.currentIndex() != 3:
            return
        # Qt can deliver a scroll-area LayoutRequest after both resize timers
        # have stopped. Wait for the visible result as well as their completion.
        settled = QtCore.QElapsedTimer()
        settled.start()
        scroll = window.forge_controls_scroll
        while settled.elapsed() < 1000 and (
            window._preview_geometry_timer.isActive()
            or window.forge_tab._action_geometry_timer.isActive()
            or scroll.horizontalScrollBar().maximum()
            or scroll.verticalScrollBar().maximum()
        ):
            QtTest.QTest.qWait(10)

    def test_shell_layout_all_themes_and_independent_dimensions(self) -> None:
        with self.window() as window:
            for theme in THEMES:
                window.theme_service.set_theme(theme)
                self.settle()
                initial_minimum = window.minimumSize()
                for index in range(5):
                    window.tabbar.setCurrentIndex(index)
                    QtTest.QTest.qWait(300)
                    for width, height in (
                        (1400, 900), (960, 900), (960, 600),
                        (1920, 600), (1920, 1080), (1180, 820),
                    ):
                        with self.subTest(theme=theme, tab=index, size=(width, height)):
                            window.resize(width, height)
                            self.settle(window)
                            self.assertEqual(window.size(), QtCore.QSize(width, height))
                            self.assertEqual(window.minimumSize(), initial_minimum)
                            page = window.page_stack.currentWidget()
                            self.assertEqual(page.geometry(), window.page_stack.contentsRect())
                            if index == 0:
                                tab = window.image_tab
                                cards = list(tab.cards.values())
                                for i, card in enumerate(cards):
                                    self.assertTrue(tab.rect().contains(card.geometry()))
                                    for other in cards[i + 1:]:
                                        self.assertFalse(card.geometry().intersects(other.geometry()))
                                    button = card.clear_btn
                                    rect = QtCore.QRect(button.mapTo(card, QtCore.QPoint()), button.size())
                                    self.assertTrue(card.rect().contains(rect), (card.size(), rect))
                                scroll = window.image_controls_scroll
                                scroll.ensureWidgetVisible(cards[-1].clear_btn, 0, 0)
                                self.app.processEvents()
                                button = cards[-1].clear_btn
                                rect = QtCore.QRect(button.mapTo(scroll.viewport(), QtCore.QPoint()), button.size())
                                self.assertTrue(scroll.viewport().rect().contains(rect))
                            elif index == 1:
                                tab = window.sound_tab
                                self.assertLess(tab.mode_stack.geometry().bottom(), tab.now_playing.y())
                                play_y = tab.play_btn.mapTo(tab, QtCore.QPoint()).y()
                                self.assertLess(tab.now_playing.geometry().bottom(), play_y)
                                self.assertGreaterEqual(tab.height(), tab.minimumSizeHint().height())
                            elif index == 2:
                                self.assertGreaterEqual(
                                    window.message_tab.height(), window.message_tab.minimumSizeHint().height(),
                                )
                            elif index == 3:
                                self.assertGreaterEqual(
                                    window.forge_tab.height(), window.forge_tab.minimumSizeHint().height(),
                                )
                                self.assertEqual(window.forge_controls_scroll.horizontalScrollBar().maximum(), 0)
                                self.assertEqual(window.forge_controls_scroll.verticalScrollBar().maximum(), 0)
                                for label in (window.forge_tab.identity_title,
                                              window.forge_tab.identity_recipient,
                                              window.forge_tab.github_account_summary):
                                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))

    def test_forge_fits_small_windows_without_scaling_text_or_repeating_layout(self) -> None:
        with self.window(settings={
            "recipient_title": "A long letter title " * 12,
            "recipient_name": "A long recipient name " * 12,
        }) as window:
            window.setMinimumSize(960, 516)
            window.tabbar.setCurrentIndex(3)
            tab = window.forge_tab
            buttons = (tab.readiness_btn, tab.load_saved_btn, tab.load_stock_btn,
                       tab.preview_btn, tab.publish_btn, tab.open_published_btn)
            for theme in THEMES:
                window.theme_service.set_theme(theme)
                QtTest.QTest.qWait(150)
                tab._set_github_account_summary("Connected as " + "longaccountname" * 4,
                                                sign_in_warning=False)
                tab.unpublish_btn.show()
                fonts = [button.font().toString() for button in buttons]
                for mode in ("landscape", "portrait", "window"):
                    window._forge_preview_mode = mode
                    for size in ((1400, 900), (960, 516), (960, 840), (960, 900), (1920, 600), (1400, 900)):
                        with self.subTest(theme=theme, mode=mode, size=size):
                            window.resize(*size)
                            window._update_preview_geometry()
                            self.settle(window)
                            scroll = window.forge_controls_scroll
                            self.assertEqual(window.size(), QtCore.QSize(*size))
                            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                            self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                            self.assertEqual([button.font().toString() for button in buttons], fonts)
                            for widget in (*buttons, tab.github_account_btn, tab.unpublish_btn):
                                rect = QtCore.QRect(widget.mapTo(scroll.viewport(), QtCore.QPoint()), widget.size())
                                self.assertTrue(scroll.viewport().rect().contains(rect), (widget.text(), rect))
                            help_rect = QtCore.QRect(window.help_icon.pos(), window.help_icon.size())
                            self.assertTrue(window.body.rect().contains(help_rect))
                            self.assertFalse(window._preview_geometry_timer.isActive())
                            self.assertFalse(tab._action_geometry_timer.isActive())
                self.assertTrue(tab.identity_title.toolTip())
                self.assertTrue(tab.github_account_summary.toolTip())
            window.resize(960, 516)
            self.settle()
            window.tabbar.setCurrentIndex(0)
            QtTest.QTest.qWait(300)
            self.assertFalse(window._forge_compact_layout)
            self.assertEqual(window.body_layout.direction(), QtWidgets.QBoxLayout.TopToBottom)
            self.assertEqual(window.help_icon.size(), QtCore.QSize(125, 125))

    def test_shell_layout_hidden_pages_and_content_do_not_grow_window(self) -> None:
        with self.window() as window:
            window.resize(1180, 820)
            self.settle()
            expected = window.geometry()
            for index in (0, 1, 2, 3, 4, 0, 3, 1, 2, 0):
                window.tabbar.setCurrentIndex(index)
                QtTest.QTest.qWait(300)
                self.assertEqual(window.geometry(), expected)
            for width, height in ((1600, 150), (150, 1600)):
                pixmap = QtGui.QPixmap(width, height)
                pixmap.fill(QtGui.QColor("#2987ad"))
                window._last_pixmap = pixmap
                window._last_pixmap_tab = 0
                window._refresh_preview_after_layout()
                self.settle()
                self.assertEqual(window.geometry(), expected)
            window._clear_preview()
            self.settle()
            self.assertEqual(window.geometry(), expected)

    def test_shell_layout_resize_during_transitions_settles_in_viewport(self) -> None:
        with self.window() as window:
            for index in (1, 2, 0, 4, 1, 3, 0):
                window.tabbar.setCurrentIndex(index)
                window.resize(1180, 820)
                QtTest.QTest.qWait(30)
                window.resize(960, 600)
                QtTest.QTest.qWait(500)
                self.assertEqual(window.size(), QtCore.QSize(960, 600))
                self.assertEqual(window.page_stack.currentIndex(), index)
                self.assertEqual(
                    window.page_stack.currentWidget().geometry(), window.page_stack.contentsRect(),
                )

    def test_window_geometry_handles_negative_and_disconnected_displays(self) -> None:
        minimum = QtCore.QSize(960, 600)
        for available in (QtCore.QRect(-1920, -200, 1920, 1032), QtCore.QRect(0, 0, 960, 516)):
            for rect in (
                QtCore.QRect(8000, 4000, 1400, 900),
                QtCore.QRect(-8000, -4000, 1400, 900),
                QtCore.QRect(0, 0, 3000, 2000),
                QtCore.QRect(available.x() + 10, available.y() + 10, 10, 10),
            ):
                with self.subTest(available=available, rect=rect):
                    fitted = bounded_window_geometry(rect, available, minimum)
                    self.assertTrue(available.contains(fitted))
                    self.assertGreaterEqual(fitted.width(), min(minimum.width(), available.width()))
                    self.assertGreaterEqual(fitted.height(), min(minimum.height(), available.height()))

    def test_native_caption_region_excludes_window_controls_and_content(self) -> None:
        with self.window() as window:
            controller = window._window_controller
            title = window.title_bar
            self.assertTrue(controller._is_caption_point(
                title.mapTo(window, QtCore.QPoint(100, title.height() // 2)),
            ))
            for button in (title.settings_button, title.btn_minimize, title.btn_max, title.btn_close):
                self.assertFalse(controller._is_caption_point(
                    button.mapTo(window, button.rect().center()),
                ))
            self.assertFalse(controller._is_caption_point(window.body.geometry().center()))

    def test_screen_transition_uses_destination_bounds(self) -> None:
        window = QtWidgets.QWidget()
        window.setGeometry(180, 50, 1400, 900)
        destination = mock.Mock()
        available = QtCore.QRect(-1920, 0, 1536, 826)
        destination.availableGeometry.return_value = available
        fitted = fit_window_to_screen(window, QtCore.QSize(960, 600), screen=destination)
        self.assertEqual(fitted, QtCore.QRect(-1784, 0, 1400, 826))
        self.assertTrue(available.contains(window.geometry()))
        window.deleteLater()

    @unittest.skipUnless(sys.platform == "win32", "Windows native messages")
    def test_screen_recovery_waits_for_native_drag_to_finish(self) -> None:
        import ctypes
        from ctypes import wintypes

        with self.window() as window:
            controller = window._window_controller
            message = wintypes.MSG()
            message.message = 1  # WM_CREATE must not recursively request a winId.
            with mock.patch.object(window, "winId", side_effect=AssertionError("early winId")):
                self.assertEqual(controller.native_event(
                    b"windows_generic_MSG", ctypes.addressof(message),
                ), (False, 0))
            message.hWnd = int(window.winId())
            message.message = 0x0231  # WM_ENTERSIZEMOVE
            controller.native_event(b"windows_generic_MSG", ctypes.addressof(message))
            with mock.patch("Nexus.fit_window_to_screen", wraps=fit_window_to_screen) as fit:
                window._queue_window_geometry_recovery()
                self.settle()
                fit.assert_not_called()
                message.message = 0x0232  # WM_EXITSIZEMOVE
                controller.native_event(b"windows_generic_MSG", ctypes.addressof(message))
                self.settle()
                self.assertEqual(fit.call_count, 1)
                self.assertFalse(controller.system_interaction_active)
                self.assertTrue(window.screen().availableGeometry().contains(window.geometry()))

    def test_audio_import_callbacks_run_on_the_interface_thread(self) -> None:
        with self.window() as window:
            tab = window.sound_tab
            callbacks = []
            with mock.patch.object(tab, "_import_finished", side_effect=lambda payload: callbacks.append(
                ("success", QtCore.QThread.currentThread()),
            )), mock.patch.object(tab, "_import_failed", side_effect=lambda message: callbacks.append(
                ("failure", QtCore.QThread.currentThread()),
            )):
                for path in (
                    ROOT / "resources/stock/music/stock song Background Music.mp3",
                    Path(window.project_root) / "missing.mp3",
                ):
                    tab._start_import([str(path)])
                    elapsed = QtCore.QElapsedTimer()
                    elapsed.start()
                    while tab._import_thread is not None and elapsed.elapsed() < 5000:
                        QtTest.QTest.qWait(10)
                    self.assertIsNone(tab._import_thread)
                self.assertEqual([kind for kind, thread in callbacks], ["success", "failure"])
                self.assertTrue(all(thread == self.app.thread() for kind, thread in callbacks))
                previous_generation = tab._background_generation
                tab._background_generation += 1
                tab._import_result_ready.emit(previous_generation, [])
                tab._import_error_ready.emit(previous_generation, "stale result")
                self.app.processEvents()
                self.assertEqual(len(callbacks), 2)

    def test_window_geometry_invalid_saved_value_keeps_other_settings(self) -> None:
        with self.window(settings={
            "ui_window_geometry": "invalid base64",
            "ui_window_normal_geometry": ["invalid", 0, 960, 600],
            "ui_window_maximized": "false",
            "recipient_title": "Resize validation",
        }) as window:
            self.assertTrue(window.screen().availableGeometry().contains(window.frameGeometry()))
            self.assertFalse(window.isMaximized())
            self.assertEqual(window.settings_store.get("recipient_title"), "Resize validation")

    def test_window_geometry_reopens_without_native_title_bar_shrinkage(self) -> None:
        with self.window() as window:
            available = window.screen().availableGeometry()
            expected = QtCore.QRect(available)
            window.setGeometry(expected)
            self.settle()
            window._save_window_preferences()
            saved = {key: window.settings_store.get(key) for key in (
                "ui_window_geometry", "ui_window_normal_geometry", "ui_window_maximized",
            )}
            self.assertEqual(saved["ui_window_normal_geometry"], list(expected.getRect()))
        with self.window(settings=saved) as reopened:
            self.assertEqual(reopened.geometry(), expected)

    def test_window_geometry_restores_normal_bounds_and_maximized_state(self) -> None:
        with self.window() as window:
            window.setGeometry(20, 30, 960, 650)
            self.settle()
            expected = window.geometry()
            window.title_bar._toggle_max_restore()
            self.settle()
            self.assertTrue(window.isMaximized())
            window._save_window_preferences()
            self.assertTrue(window.settings_store.get("ui_window_maximized"))
            window.title_bar._toggle_max_restore()
            self.settle()
            # A restore is bounded to the available screen; offscreen uses 800x800.
            self.assertEqual(window.geometry(), bounded_window_geometry(
                expected, window.screen().availableGeometry(), window.minimumSize(),
            ))
            self.assertFalse(window.isMaximized())

    def test_curtain_popup_tracks_field_and_dismisses_when_anchor_changes(self) -> None:
        host = QtWidgets.QWidget()
        field = CurtainStyleComboBox(host)
        field.setModel(CurtainStyleModel(field))
        host.resize(600, 400)
        host.move(10, 10)
        field.setGeometry(40, 50, 250, 38)
        host.show()
        self.settle()
        try:
            menu = field.popup_menu()
            for theme in THEMES.values():
                field.apply_theme_tokens(theme.tokens)
                for width in (250, 340, 250):
                    field.resize(width, 38)
                    self.settle()
                    menu.popup(field.mapToGlobal(QtCore.QPoint(0, field.height())))
                    self.settle()
                    rect = QtCore.QRect(field.mapToGlobal(QtCore.QPoint()), field.size())
                    self.assertEqual(menu.width(), rect.width())
                    self.assertEqual(menu.x(), rect.x())
                    self.assertEqual(menu.geometry().right(), rect.right())
                    host.move(host.pos() + QtCore.QPoint(1, 0))
                    self.app.processEvents()
                    self.assertFalse(menu.isVisible())
            for change in (
                lambda: field.resize(field.width() + 10, field.height()),
                lambda: host.resize(host.width() + 10, host.height()),
                lambda: field.apply_theme_tokens(THEMES["light"].tokens),
                lambda: self.app.sendEvent(host, QtCore.QEvent(QtCore.QEvent.DevicePixelRatioChange)),
                lambda: field.hide(),
            ):
                field.show()
                menu.popup(field.mapToGlobal(QtCore.QPoint(0, field.height())))
                menu.light_menu.popup(menu.mapToGlobal(QtCore.QPoint(menu.width(), 0)))
                self.settle()
                self.assertTrue(menu.isVisible())
                self.assertTrue(menu.light_menu.isVisible())
                change()
                self.app.processEvents()
                self.assertFalse(menu.isVisible())
                self.assertFalse(menu.light_menu.isVisible())
                self.assertFalse(field._popup_anchors)
            field.show()
            available = host.screen().availableGeometry()
            rect = QtCore.QRect(field.mapToGlobal(QtCore.QPoint()), field.size())
            host.move(host.pos() + QtCore.QPoint(
                available.right() - rect.right(), available.bottom() - rect.bottom(),
            ))
            self.settle()
            menu.popup(field.mapToGlobal(QtCore.QPoint(0, field.height())))
            self.settle()
            self.assertEqual(menu.x(), field.mapToGlobal(QtCore.QPoint()).x())
            self.assertEqual(menu.geometry().right(), available.right())
        finally:
            field.hidePopup()
            host.close()
            host.deleteLater()

    def test_editor_formatting_remains_reachable_in_a_narrow_window(self) -> None:
        from Editor import Editor

        with self.window() as host:
            settings = QtCore.QSettings(
                str(Path(host.project_root) / "editor-test.ini"), QtCore.QSettings.IniFormat,
            )
            with mock.patch("Editor.QSettings", return_value=settings):
                editor = Editor("<p>Resize validation</p>", parent=host)
            try:
                editor.show()
                self.settle()
                self.assertTrue(editor.screen().availableGeometry().contains(editor.frameGeometry()))
                for width in (1100, 640, 960):
                    editor.resize(width, 480)
                    self.settle()
                    self.assertEqual(editor.width(), width)
                    scroll = editor.font_controls_scroll
                    scroll.ensureWidgetVisible(editor.btn_spacing, 0, 0)
                    self.app.processEvents()
                    button = editor.btn_spacing
                    rect = QtCore.QRect(button.mapTo(scroll.viewport(), QtCore.QPoint()), button.size())
                    self.assertTrue(scroll.viewport().rect().contains(rect))
                    self.assertGreater(editor.editor.height(), 100)
            finally:
                editor._autosave_timer.stop()
                editor._language_check_timer.stop()
                editor.hide()
                editor.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
