from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtWidgets

from command import CommandTab
from Nexus import Nexus


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


if __name__ == "__main__":
    unittest.main()
