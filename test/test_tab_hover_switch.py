from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from anima import install_hover_tab_switch
from ui_help import set_action_help, set_control_help, set_tab_help


class HoverTabSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.tabbar = QtWidgets.QTabBar()
        for name in ("Images", "Sound", "Message", "Forge", "Command"):
            self.tabbar.addTab(name)
        self.tabbar.resize(600, 44)
        self.tabbar.show()
        self.app.processEvents()
        self.hover_filter = install_hover_tab_switch(
            self.tabbar,
            delay_ms=30,
            excluded_indices={4},
        )

    def tearDown(self) -> None:
        self.tabbar.close()
        self.app.processEvents()

    def _hover(self, index: int) -> None:
        QtTest.QTest.mouseMove(self.tabbar, self.tabbar.tabRect(index).center())
        QtTest.QTest.qWait(60)

    def test_command_never_activates_from_hover(self) -> None:
        self.tabbar.setCurrentIndex(0)
        self._hover(4)
        self.assertEqual(self.tabbar.currentIndex(), 0)
        self.assertFalse(self.hover_filter._timer.isActive())

    def test_command_still_activates_from_click(self) -> None:
        QtTest.QTest.mouseClick(
            self.tabbar,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
            self.tabbar.tabRect(4).center(),
        )
        self.assertEqual(self.tabbar.currentIndex(), 4)

    def test_normal_tab_still_activates_from_hover(self) -> None:
        self.tabbar.setCurrentIndex(0)
        self._hover(2)
        self.assertEqual(self.tabbar.currentIndex(), 2)


class UiHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_shared_helpers_apply_all_supported_help_surfaces(self) -> None:
        button = QtWidgets.QToolButton()
        set_control_help(
            button,
            "  Open   application settings. ",
            accessible_name="Settings",
        )
        self.assertEqual(button.toolTip(), "Open application settings.")
        self.assertEqual(button.statusTip(), button.toolTip())
        self.assertEqual(button.whatsThis(), button.toolTip())
        self.assertEqual(button.accessibleName(), "Settings")

        action = QtGui.QAction("Save")
        set_action_help(action, "Apply the current settings.")
        self.assertEqual(action.toolTip(), "Apply the current settings.")
        self.assertEqual(action.statusTip(), action.toolTip())
        self.assertEqual(action.whatsThis(), action.toolTip())

        tabbar = QtWidgets.QTabBar()
        index = tabbar.addTab("Command")
        set_tab_help(tabbar, index, "Open Command only when clicked.")
        self.assertEqual(tabbar.tabToolTip(index), "Open Command only when clicked.")
        self.assertEqual(tabbar.tabWhatsThis(index), tabbar.tabToolTip(index))


class NexusHoverIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_nexus_excludes_command_and_sets_main_tab_help(self) -> None:
        from Nexus import Nexus
        from project_state import ProjectStateController
        from settings_store import SettingsStore
        from ui_theme import THEME_SETTINGS_KEY

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            state = ProjectStateController(temp_dir)
            state.initialize()
            state.establish_project("Test Recipient", custom_capitalization=True)
            SettingsStore(temp_dir).update_fields(recipient_title="Test Letter")
            window = Nexus(Path(temp_dir))
            try:
                window.resize(1200, 820)
                window.application_stack.setCurrentWidget(window.body)
                window.show()
                self.app.processEvents()

                hover_filter = getattr(window.tabbar, "_anima_hover_tab_switch")
                self.assertEqual(hover_filter._excluded_indices, frozenset({4}))
                self.assertTrue(
                    all(
                        window.tabbar.tabToolTip(index).strip()
                        for index in range(window.tabbar.count())
                    )
                )
                self.assertTrue(window.title_bar.save_settings_action.toolTip())
                self.assertEqual(
                    set(window.title_bar._theme_actions),
                    {"futuristic", "soft_elegant"},
                )
                window.title_bar._theme_actions["soft_elegant"].trigger()
                self.app.processEvents()
                self.assertEqual(window.theme_service.theme_id, "soft_elegant")
                self.assertEqual(
                    SettingsStore(temp_dir).get(THEME_SETTINGS_KEY),
                    "soft_elegant",
                )
                window.title_bar.save_settings_action.trigger()
                self.app.processEvents()
                self.assertEqual(
                    window.property("letterSmithTheme"),
                    "soft_elegant",
                )

                window.tabbar.setCurrentIndex(0)
                QtTest.QTest.mouseMove(
                    window.tabbar,
                    window.tabbar.tabRect(4).center(),
                )
                QtTest.QTest.qWait(400)
                self.assertEqual(window.tabbar.currentIndex(), 0)

                QtTest.QTest.mouseClick(
                    window.tabbar,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.NoModifier,
                    window.tabbar.tabRect(4).center(),
                )
                self.assertEqual(window.tabbar.currentIndex(), 4)
            finally:
                window.shutdown()
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
