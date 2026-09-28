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
from window_chrome import (
    bounded_window_geometry, fit_window_to_screen, install_window_placement_guard,
    place_window_on_launcher, restored_window_geometry, screen_for_launcher,
)


ROOT = Path(__file__).resolve().parents[1]


class WindowSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        install_button_text_guard(cls.app)
        install_window_placement_guard(cls.app)

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
        if window is None or window.tabbar.currentIndex() not in {1, 2, 3}:
            return
        # Qt can deliver a scroll-area LayoutRequest after both resize timers
        # have stopped. Wait for the visible result as well as their completion.
        settled = QtCore.QElapsedTimer()
        settled.start()
        scroll = (window.sound_controls_scroll, window.message_controls_scroll,
                  window.forge_controls_scroll)[window.tabbar.currentIndex() - 1]
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
                        (900, 480), (960, 516), (1024, 540),
                        (1920, 600), (1920, 1080), (1180, 820),
                    ):
                        with self.subTest(theme=theme, tab=index, size=(width, height)):
                            window.resize(width, height)
                            self.settle(window)
                            self.assertEqual(window.size(), QtCore.QSize(width, height))
                            self.assertEqual(window.minimumSize(), initial_minimum)
                            page = window.page_stack.currentWidget()
                            self.assertEqual(page.geometry(), window.page_stack.contentsRect())
                            if index != 4:
                                preview = QtCore.QRect(
                                    window.preview_frame.mapTo(window.body, QtCore.QPoint()),
                                    window.preview_frame.size(),
                                )
                                self.assertTrue(window.body.rect().contains(preview))
                                self.assertGreaterEqual(preview.height(), 40)
                                self.assertLessEqual(preview.bottom(), window.page_stack.y())
                                self.assertTrue(window.body.rect().contains(window.page_stack.geometry()))
                            if index == 0:
                                tab = window.image_tab
                                cards = list(tab.cards.values())
                                for i, card in enumerate(cards):
                                    self.assertTrue(tab.rect().contains(card.geometry()))
                                    self.assertLess(card.geometry().bottom(), tab.status.y())
                                    for other in cards[i + 1:]:
                                        self.assertFalse(card.geometry().intersects(other.geometry()))
                                    button = card.clear_btn
                                    rect = QtCore.QRect(button.mapTo(card, QtCore.QPoint()), button.size())
                                    self.assertTrue(card.rect().contains(rect), (card.size(), rect))
                                scroll = window.image_controls_scroll
                                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                                self.assertGreaterEqual(tab.pwrite_fab.y(), window.tabbar.geometry().bottom())
                                for button in (tab.reset_btn, tab.open_btn,
                                               *(card.clear_btn for card in cards)):
                                    rect = QtCore.QRect(
                                        button.mapTo(scroll.viewport(), QtCore.QPoint()),
                                        button.size(),
                                    )
                                    self.assertTrue(scroll.viewport().rect().contains(rect),
                                                    (theme, (width, height), button.text(), rect))
                                self.assertGreaterEqual(
                                    tab.open_btn.y() - tab.reset_btn.geometry().bottom() - 1,
                                    6,
                                )
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

    def test_help_hover_recovers_after_a_modal_interrupts_enter(self) -> None:
        with self.window() as window:
            window.tabbar.setCurrentIndex(2)
            self.settle(window)
            icon = window.help_icon
            modal = QtWidgets.QDialog(window)
            modal.setModal(True)
            modal.show()
            QtWidgets.QApplication.sendEvent(icon, QtCore.QEvent(QtCore.QEvent.Enter))
            QtTest.QTest.qWait(180)
            self.assertFalse(window.help_pop.isVisible())
            modal.close()
            self.app.processEvents()
            # No new Enter is required after the interrupted hover.
            QtWidgets.QApplication.sendEvent(icon, QtCore.QEvent(QtCore.QEvent.HoverMove))
            QtTest.QTest.qWait(180)
            self.assertTrue(window.help_pop.isVisible())
            window._hide_help_popover()
            modal.deleteLater()

    def test_sound_and_message_fit_without_panel_scrolling(self) -> None:
        with self.window() as window:
            window.setMinimumSize(900, 480)
            sound = window.sound_tab
            sound.single_title.setText("A long track title with spaces " * 3)
            sound.single_detail.setText("4:30 • " + "a-long-imported-audio-filename-" * 3 + ".mp3")
            sound.now_playing.setText("Now Playing: " + sound.single_title.text())
            sound.create_playlist_btn.show()
            for theme in THEMES:
                window.theme_service.set_theme(theme)
                for index, mode in ((1, 0), (1, 1), (2, None)):
                    window.tabbar.setCurrentIndex(index)
                    if mode is not None:
                        sound.mode_stack.setCurrentIndex(mode)
                    QtTest.QTest.qWait(300)
                    for size in ((1400, 900), (900, 480), (960, 600),
                                 (960, 516), (1024, 540), (1920, 600),
                                 (1180, 820), (1400, 900)):
                        with self.subTest(theme=theme, tab=index, mode=mode, size=size):
                            window.resize(*size)
                            self.settle(window)
                            scroll = (window.sound_controls_scroll if index == 1
                                      else window.message_controls_scroll)
                            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                            self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                            self.assertEqual(window.size(), QtCore.QSize(*size))
                            self.assertEqual(window.body_layout.direction(), QtWidgets.QBoxLayout.TopToBottom)
                            preview_bottom = window.preview_frame.mapTo(
                                window.body, QtCore.QPoint(0, window.preview_frame.height()),
                            ).y()
                            self.assertLessEqual(preview_bottom, window.page_stack.y())
                            self.assertGreaterEqual(window.preview_frame.height(), 40)
                            controls = ((sound.stock_btn, sound.archive_btn, sound.clear_btn,
                                         sound.play_btn, sound.volume, sound.status,
                                         sound.single_action_btn if mode == 0 else sound.add_track_btn)
                                        if index == 1 else
                                        (window.message_tab.title_input, window.message_tab.url_input,
                                         window.message_tab.overlay_opacity_slider, window.message_tab.btn,
                                         window.message_tab.edit_btn, window.message_tab.revisions_btn))
                            for control in controls:
                                rect = QtCore.QRect(control.mapTo(scroll.viewport(), QtCore.QPoint()), control.size())
                                self.assertTrue(scroll.viewport().rect().contains(rect), (control, rect))
                            if index == 1:
                                panel = sound.mode_stack.currentWidget()
                                self.assertGreaterEqual(panel.height(), panel.minimumSizeHint().height())
                            self.assertFalse(window._preview_geometry_timer.isActive())

    def test_closing_editor_does_not_create_native_main_content(self) -> None:
        if self.app.platformName() != "windows":
            self.skipTest("Requires native Windows window destruction")
        from Editor import Editor

        with self.window() as window:
            content = [window.findChild(QtWidgets.QWidget, name)
                       for name in ("NexusRoot", "NexusBody")]
            self.assertTrue(all(widget.internalWinId() == 0 for widget in content))
            for _ in range(2):
                editor = Editor("<p>Window lifecycle check</p>", parent=window.message_tab)
                editor.settings = QtCore.QSettings(
                    str(Path(window.message_tab.project_root) / "editor-window.ini"),
                    QtCore.QSettings.IniFormat,
                )
                QtCore.QTimer.singleShot(100, editor._finish_close)
                editor.exec()
                self.assertTrue(all(widget.internalWinId() == 0 for widget in content))
                self.assertEqual(editor.internalWinId(), 0)
                editor.deleteLater()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_forge_fits_small_windows_without_scaling_text_or_repeating_layout(self) -> None:
        with self.window(settings={
            "recipient_title": "A long letter title " * 12,
            "recipient_name": "A long recipient name " * 12,
        }) as window:
            window.setMinimumSize(900, 480)
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
                    for size in ((1400, 900), (900, 480), (960, 516),
                                 (1024, 540), (960, 840), (960, 900),
                                 (1920, 600), (1400, 900)):
                        with self.subTest(theme=theme, mode=mode, size=size):
                            window.resize(*size)
                            window._update_preview_geometry()
                            self.settle(window)
                            scroll = window.forge_controls_scroll
                            self.assertEqual(window.size(), QtCore.QSize(*size))
                            self.assertEqual(window.body_layout.direction(), QtWidgets.QBoxLayout.TopToBottom)
                            preview_bottom = window.preview_frame.mapTo(
                                window.body, QtCore.QPoint(0, window.preview_frame.height()),
                            ).y()
                            self.assertLessEqual(preview_bottom, tab.preview_format_panel.mapTo(
                                window.body, QtCore.QPoint(),
                            ).y())
                            self.assertLessEqual(preview_bottom, window.page_stack.y())
                            self.assertGreaterEqual(window.preview_frame.height(), 40)
                            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                            self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                            self.assertEqual([button.font().toString() for button in buttons], fonts)
                            for widget in (*buttons, tab.github_account_btn, tab.unpublish_btn):
                                rect = QtCore.QRect(widget.mapTo(scroll.viewport(), QtCore.QPoint()), widget.size())
                                self.assertTrue(scroll.viewport().rect().contains(rect), (widget.text(), rect))
                            for label in (tab.identity_title, tab.identity_recipient):
                                self.assertLessEqual(len(label.text().splitlines()), 2)
                                for line in label.text().splitlines():
                                    self.assertLessEqual(
                                        label.fontMetrics().horizontalAdvance(line),
                                        label.contentsRect().width(),
                                    )
                            for first, second in ((tab.preview_btn, tab.publish_btn),
                                                  (tab.publish_btn, tab.open_published_btn)):
                                first_rect = QtCore.QRect(first.mapTo(tab, QtCore.QPoint()), first.size())
                                second_rect = QtCore.QRect(second.mapTo(tab, QtCore.QPoint()), second.size())
                                self.assertGreaterEqual(second_rect.left() - first_rect.right() - 1, 6)
                            help_rect = QtCore.QRect(window.help_icon.pos(), window.help_icon.size())
                            self.assertTrue(window.body.rect().contains(help_rect))
                            self.assertFalse(window._preview_geometry_timer.isActive())
                            self.assertFalse(tab._action_geometry_timer.isActive())
                self.assertTrue(tab.identity_title.toolTip())
                self.assertTrue(tab.github_account_summary.toolTip())
            window.resize(900, 480)
            self.settle()
            window.tabbar.setCurrentIndex(0)
            QtTest.QTest.qWait(300)
            self.assertFalse(window._forge_compact_layout)
            self.assertEqual(window.body_layout.direction(), QtWidgets.QBoxLayout.TopToBottom)
            self.assertLessEqual(window.help_icon.width(), 64)

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
                window.resize(900, 480)
                QtTest.QTest.qWait(500)
                self.assertEqual(window.size(), QtCore.QSize(900, 480))
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

    def test_tool_placement_preserves_size_and_valid_positions_on_launcher_screen(self) -> None:
        owner = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
        tool = QtWidgets.QDialog(owner, QtCore.Qt.FramelessWindowHint)
        self.addCleanup(owner.deleteLater)
        available = QtCore.QRect(-1600, -200, 1600, 900)
        owner.setGeometry(-1500, -100, 1000, 700)
        screen = mock.Mock()
        screen.availableGeometry.return_value = available
        valid = QtCore.QRect(-1400, -80, 640, 420)
        with mock.patch("window_chrome.screen_for_launcher", return_value=screen):
            for saved in (
                valid, QtCore.QRect(120, 80, 640, 420),
                QtCore.QRect(8000, -4000, 640, 420),
                QtCore.QRect(-300, 500, 640, 420),
            ):
                with self.subTest(saved=saved):
                    placed = place_window_on_launcher(tool, geometry=saved)
                    self.assertEqual(placed.size(), saved.size())
                    self.assertTrue(available.contains(placed))
                    if saved == valid:
                        self.assertEqual(placed, saved)
            tool.setMinimumSize(1800, 1200)
            placed = place_window_on_launcher(tool, geometry=QtCore.QRect(8000, 0, 2400, 1600))
            self.assertEqual(placed, available)
            self.assertEqual(tool.minimumSize(), available.size())

    def test_tool_placement_uses_the_controls_global_screen(self) -> None:
        owner = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
        self.addCleanup(owner.deleteLater)
        button = QtWidgets.QPushButton("Launch", owner)
        owner.setGeometry(500, 100, 700, 500)
        button.setGeometry(600, 20, 80, 30)
        owner.show()
        screen = mock.Mock()
        with mock.patch("window_chrome.QtGui.QGuiApplication.screenAt", return_value=screen) as lookup:
            self.assertIs(screen_for_launcher(button), screen)
            lookup.assert_called_once_with(button.mapToGlobal(button.rect().center()))
        owner.close()

    def test_tool_placement_rechecks_changed_monitor_arrangement_on_reopen(self) -> None:
        owner = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
        self.addCleanup(owner.deleteLater)
        tool = QtWidgets.QDialog(owner, QtCore.Qt.FramelessWindowHint)
        screen = mock.Mock()
        saved = QtCore.QRect(-1500, 80, 600, 400)
        with mock.patch("window_chrome.screen_for_launcher", return_value=screen):
            for available in (
                QtCore.QRect(-1920, 0, 1920, 1032),
                QtCore.QRect(0, -900, 1440, 900),
                QtCore.QRect(1920, 200, 1280, 720),
            ):
                screen.availableGeometry.return_value = available
                owner.setGeometry(QtCore.QRect(available.topLeft() + QtCore.QPoint(40, 40), QtCore.QSize(700, 500)))
                placed = place_window_on_launcher(tool, geometry=saved)
                self.assertTrue(available.contains(placed), (available, placed))
                self.assertEqual(placed.size(), saved.size())
                saved = placed

    def test_tool_placement_guard_corrects_custom_show_events_and_native_frames(self) -> None:
        class SavedPositionDialog(QtWidgets.QDialog):
            def showEvent(self, event):
                super().showEvent(event)
                self.move(9000, -4000)

        owner = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
        self.addCleanup(owner.deleteLater)
        available = self.app.primaryScreen().availableGeometry()
        owner.setGeometry(QtCore.QRect(available.topLeft() + QtCore.QPoint(20, 20), QtCore.QSize(500, 400)))
        owner.show()
        dialog = SavedPositionDialog(owner)
        dialog.resize(320, 200)
        dialog.show()
        self.settle()
        self.assertTrue(available.contains(dialog.frameGeometry()), dialog.frameGeometry())
        self.assertEqual(dialog.size(), QtCore.QSize(320, 200))
        # The guard applies only at opening, not while the user moves the tool.
        dialog.move(dialog.pos() + QtCore.QPoint(12, 12))
        moved = dialog.pos()
        self.settle()
        self.assertEqual(dialog.pos(), moved)
        dialog.close()
        owner.close()

    def test_tool_placement_guard_follows_clicked_control_when_owner_spans_screens(self) -> None:
        owner = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
        self.addCleanup(owner.deleteLater)
        owner.setGeometry(100, 100, 700, 400)
        button = QtWidgets.QPushButton("Launch", owner)
        button.setGeometry(20, 20, 100, 30)
        dialog = QtWidgets.QDialog(owner, QtCore.Qt.FramelessWindowHint)
        dialog.setGeometry(500, 150, 200, 160)
        button.clicked.connect(dialog.show)
        owner.show()
        left, right = mock.Mock(), mock.Mock()
        left.availableGeometry.return_value = QtCore.QRect(0, 0, 400, 800)
        right.availableGeometry.return_value = QtCore.QRect(400, 0, 400, 800)
        with mock.patch(
            "window_chrome.QtGui.QGuiApplication.screenAt",
            side_effect=lambda point: left if point.x() < 400 else right,
        ):
            self.assertIs(screen_for_launcher(owner), right)
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            self.settle()
            self.assertTrue(left.availableGeometry().contains(dialog.frameGeometry()))
        dialog.close()
        owner.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows multi-monitor placement")
    def test_native_tool_placement_across_connected_screens(self) -> None:
        if self.app.platformName() != "windows" or len(self.app.screens()) < 2:
            self.skipTest("Requires the native Windows platform and multiple screens")
        from Editor import Editor, SETTINGS_KEY_GEOMETRY
        from Image_tab import StockImageDialog
        from PromptWriterPanel import ListManagerDialog
        from command_bar import CommandBarData, CommandBarWindow, COMMAND_COMPACT_POSITION_SETTINGS_KEY
        from sound_tab import ArchiveDialog, StockMusicDialog
        from startup_theme import ThemeFamilySelector, THEME_SELECTOR_POSITION_SETTINGS_KEY
        from ui_dialogs import LetterSmithDialog

        with self.window() as window:
            settings = QtCore.QSettings(
                str(Path(window.project_root) / "editor-test.ini"), QtCore.QSettings.IniFormat,
            )
            for index, screen in enumerate(self.app.screens()):
                available = screen.availableGeometry()
                other = self.app.screens()[(index + 1) % len(self.app.screens())]
                stale = QtCore.QRect(other.availableGeometry().topLeft() + QtCore.QPoint(100, 80), QtCore.QSize(1100, 720))
                window.setGeometry(QtCore.QRect(available.topLeft() + QtCore.QPoint(80, 80), QtCore.QSize(1200, 750)))
                self.settle()
                writer = getattr(window, "_prompt_writer_win", None)
                if writer is not None:
                    writer._normal_geometry = QtCore.QRect(stale)
                window.open_prompt_writer()
                writer = window._prompt_writer_win
                # Both animation endpoints and intermediate frames stay reachable.
                for _ in range(7):
                    QtTest.QTest.qWait(45)
                    self.assertTrue(available.contains(writer.frameGeometry()), (screen.name(), writer.frameGeometry()))
                size = writer.size()
                writer.popdown()
                QtTest.QTest.qWait(260)
                window.open_prompt_writer()
                QtTest.QTest.qWait(300)
                self.assertEqual(writer.size(), size)
                writer.popdown()
                QtTest.QTest.qWait(260)

                donor = QtWidgets.QWidget(None, QtCore.Qt.FramelessWindowHint)
                donor.setGeometry(stale)
                settings.setValue(SETTINGS_KEY_GEOMETRY, donor.saveGeometry())
                donor.deleteLater()
                with mock.patch("Editor.QSettings", return_value=settings):
                    editor = Editor("", parent=window.message_tab)
                editor.show()
                self.settle()
                self.assertTrue(available.contains(editor.frameGeometry()), (screen.name(), editor.frameGeometry()))
                self.assertEqual(editor.size(), stale.size())
                editor.open_find_replace()
                self.settle()
                self.assertTrue(available.contains(editor._find_dialog.frameGeometry()))
                editor._find_dialog.move(stale.topLeft())
                editor.open_find_replace()
                self.settle()
                self.assertTrue(available.contains(editor._find_dialog.frameGeometry()))
                editor._discard_changes = True
                editor._finish_close()
                editor.deleteLater()

                SettingsStore(window.project_root).update_fields({
                    THEME_SELECTOR_POSITION_SETTINGS_KEY: {"x": stale.x(), "y": stale.y()},
                })
                for factory in (
                    lambda: LetterSmithDialog(window),
                    lambda: StockImageDialog("Stock Images", (), window.image_tab),
                    lambda: StockMusicDialog(window.sound_tab.library, multi_select=True, parent=window.sound_tab),
                    lambda: ArchiveDialog(window.sound_tab.library, lambda: set(), lambda _track: False,
                                          multi_select=True, parent=window.sound_tab),
                    lambda: ListManagerDialog(title="Topics", entries=[], parent=window),
                    lambda: ThemeFamilySelector(window.project_root, window),
                ):
                    dialog = factory()
                    dialog.show()
                    self.settle()
                    self.assertTrue(dialog.isVisible(), type(dialog).__name__)
                    self.assertTrue(available.contains(dialog.frameGeometry()), (type(dialog).__name__, screen.name(), dialog.frameGeometry()))
                    dialog.close()
                    dialog.deleteLater()
                    self.app.processEvents()

                SettingsStore(window.project_root).update_fields({
                    COMMAND_COMPACT_POSITION_SETTINGS_KEY: {"x": stale.x(), "y": stale.y()},
                })
                bar = CommandBarWindow(CommandBarData("Recipient", "Title", None, ""),
                                       window.project_root, screen=screen)
                try:
                    bar.show()
                    bar._enter_compact_mode()
                    self.settle()
                    self.assertTrue(available.contains(bar.frameGeometry()))
                    bar._restore_expanded_mode()
                    self.settle()
                    self.assertTrue(available.contains(bar.frameGeometry()))
                finally:
                    bar.abort_launch()
                    bar.deleteLater()

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

    def test_restore_replaces_nearly_maximized_bounds_on_the_current_monitor(self) -> None:
        available = QtCore.QRect(1920, 0, 1920, 1032)
        minimum = QtCore.QSize(960, 600)
        default = QtCore.QSize(1400, 900)
        for target in (available, QtCore.QRect(1921, 32, 1918, 999)):
            restored = restored_window_geometry(target, available, minimum, default)
            self.assertTrue(available.contains(restored))
            self.assertLess(restored.width(), available.width() - 40)
            self.assertLess(restored.height(), available.height() - 40)
        normal = QtCore.QRect(2000, 100, 1200, 750)
        self.assertEqual(restored_window_geometry(normal, available, minimum, default), normal)
        other_monitor = QtCore.QRect(-1800, 100, 1200, 750)
        restored = restored_window_geometry(other_monitor, available, minimum, default)
        self.assertTrue(available.contains(restored))
        self.assertEqual(restored.size(), other_monitor.size())

    def test_legacy_maximized_settings_restore_to_a_smaller_resizable_window(self) -> None:
        with self.window() as window:
            available = window.screen().availableGeometry()
            window.setGeometry(available.adjusted(1, 32, -1, -1))
            window.showMaximized()
            self.settle()
            saved = {
                "ui_window_geometry": bytes(window.saveGeometry().toBase64()).decode("ascii"),
                "ui_window_maximized": True,
            }
        with self.window(settings=saved) as reopened:
            self.assertTrue(reopened.isMaximized())
            minimum = reopened.minimumSize()
            QtTest.QTest.mouseClick(reopened.title_bar.btn_max, QtCore.Qt.LeftButton)
            self.settle()
            self.assertFalse(reopened.isMaximized())
            self.assertLess(reopened.height(), available.height() - 40)
            normal = QtCore.QRect(reopened.geometry())
            reopened.resize(reopened.width(), reopened.height() - 30)
            self.settle()
            self.assertLess(reopened.height(), normal.height())
            self.assertEqual(reopened.minimumSize(), minimum)

    def test_restore_uses_latest_native_normal_bounds_instead_of_stale_button_cache(self) -> None:
        with self.window() as window:
            window.setGeometry(0, 0, 960, 650)
            self.settle()
            window.title_bar._toggle_max_restore()
            self.settle()
            window.title_bar._toggle_max_restore()
            self.settle()
            window.resize(window.width(), 610)
            self.settle()
            expected = QtCore.QRect(window.geometry())
            window.showMaximized()
            self.settle()
            window.title_bar._toggle_max_restore()
            self.settle()
            self.assertEqual(window.geometry(), expected)

    @unittest.skipUnless(sys.platform == "win32", "Windows native window state")
    def test_maximized_launch_button_restores_windows_state_and_bounds(self) -> None:
        if self.app.platformName() != "windows":
            self.skipTest("Requires QT_QPA_PLATFORM=windows; offscreen cannot reproduce this bug")
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.IsZoomed.argtypes = (wintypes.HWND,)
        user32.IsZoomed.restype = wintypes.BOOL
        normal = QtCore.QRect(100, 80, 1200, 750)
        with self.window(settings={
            "ui_window_normal_geometry": list(normal.getRect()),
            "ui_window_maximized": True,
        }) as window:
            hwnd = int(window.winId())
            for _ in range(3):
                self.assertTrue(window.isMaximized())
                self.assertTrue(user32.IsZoomed(hwnd))
                self.assertEqual(window.geometry(), window.screen().availableGeometry())
                QtTest.QTest.mouseClick(window.title_bar.btn_max, QtCore.Qt.LeftButton)
                self.settle()
                self.assertFalse(window.isMaximized())
                self.assertFalse(user32.IsZoomed(hwnd))
                self.assertEqual(window.geometry(), normal)
                normal.translate(10, 10)
                normal.setSize(normal.size() - QtCore.QSize(20, 20))
                window.setGeometry(normal)
                self.settle()
                self.assertEqual(window.geometry(), normal)
                QtTest.QTest.mouseClick(window.title_bar.btn_max, QtCore.Qt.LeftButton)
                self.settle()
            window._save_window_preferences()
            self.assertEqual(window.settings_store.get("ui_window_normal_geometry"), list(normal.getRect()))

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
            self.assertEqual(menu.frameGeometry().right(), available.right())
            self.assertTrue(available.contains(menu.frameGeometry()))
        finally:
            field.hidePopup()
            host.close()
            host.deleteLater()

    def test_prompt_writer_button_toggles_one_instance_and_show_requests_focus(self) -> None:
        from PromptWriterPanel import PromptWriterPanel

        with self.window() as window:
            button = window.image_tab.pwrite_fab
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            panel = window._prompt_writer_win
            self.assertIsInstance(panel, PromptWriterPanel)
            self.assertTrue(panel.is_open)

            # A second click closes; a third during the animation reopens it.
            for expected in (False, True, False, True):
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                self.assertEqual(panel.is_open, expected)
                self.assertIs(window._prompt_writer_win, panel)
                self.assertEqual(len(window.findChildren(PromptWriterPanel)), 1)
            QtTest.QTest.qWait(300)
            self.assertTrue(panel.is_open)

            panel.txt_global.setPlainText("Keep this draft")
            with mock.patch.object(panel, "_proofread_visible_prompt_fields") as proofread:
                window.open_prompt_writer()
                self.assertTrue(panel.is_open)
                self.assertIs(window._prompt_writer_win, panel)
                proofread.assert_not_called()
            QtTest.QTest.mouseClick(window.tabbar, QtCore.Qt.LeftButton,
                                    pos=window.tabbar.tabRect(1).center())
            self.settle()
            self.assertEqual(window.tabbar.currentIndex(), 1)
            self.assertTrue(panel.is_open)
            self.assertEqual(panel.txt_global.toPlainText(), "Keep this draft")

            panel.close()
            self.assertFalse(panel.is_open)
            self.assertFalse(panel._shutdown)
            window.open_prompt_writer()
            self.assertIs(window._prompt_writer_win, panel)
            self.assertTrue(panel.is_open)

            # External destruction must clear the reference before a new request.
            panel.shutdown()
            panel.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.assertIsNone(window._prompt_writer_win)
            window.open_prompt_writer()
            replacement = window._prompt_writer_win
            self.assertIsNot(replacement, panel)
            self.assertTrue(replacement.is_open)
            window._on_prompt_writer_destroyed(panel)
            self.assertIs(window._prompt_writer_win, replacement)
            self.assertEqual(len(window.findChildren(PromptWriterPanel)), 1)

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
