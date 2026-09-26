from __future__ import annotations

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QWidgetAction

from named_text_style_preview import (
    add_style_preview_submenu,
    preview_font,
    set_action_style_preview,
    set_slot_style_preview,
)
from named_text_styles import default_style_set


class NamedTextStylePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_menu_labels_use_individual_style_and_actual_color(self) -> None:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#141414;color:#eeeeee;} QLabel{font:8pt 'Arial';}"
        )
        normal = add_style_preview_submenu(menu, "Normal text")
        subtitle = add_style_preview_submenu(menu, "Subtitle")
        normal.addAction("Apply Normal text")
        subtitle.addAction("Apply Subtitle")
        first, second = menu.actions()
        self.assertTrue(all(isinstance(action, QWidgetAction) for action in (first, second)))
        self.assertEqual([action.text() for action in menu.actions()], ["Normal text", "Subtitle"])
        self.assertIs(first.menu(), normal)
        self.assertIs(second.menu(), subtitle)

        base = default_style_set()
        title_style = replace(
            base.get("normal_text"),
            font_family="Font That Is Not Installed",
            font_size=21,
            font_weight=700,
            italic=True,
            underline=True,
            strikethrough=True,
            font_color="#ff0000",
        )
        subtitle_style = replace(
            base.get("subtitle"), font_family="Another Missing Font", font_color="#00ff00"
        )
        ui_size = first._named_text_preview_row.label.font().pointSizeF()
        set_action_style_preview(first, title_style)
        set_action_style_preview(second, subtitle_style)
        first._named_text_preview_row.label.ensurePolished()
        shown_font = first._named_text_preview_row.label.font()
        self.assertEqual(shown_font.pointSizeF(), ui_size)
        self.assertEqual(title_style.font_size, 21)
        self.assertEqual(shown_font.weight(), 700)
        self.assertTrue(shown_font.italic())
        self.assertTrue(shown_font.underline())
        self.assertTrue(shown_font.strikeOut())
        self.assertTrue(shown_font.family())
        self.assertEqual(title_style.font_family, "Font That Is Not Installed")

        menu.show()
        self.app.processEvents()
        image = menu.grab().toImage()
        red = green = 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                if pixel.red() > 190 and pixel.green() < 90 and pixel.blue() < 90:
                    red += 1
                if pixel.green() > 190 and pixel.red() < 90 and pixel.blue() < 90:
                    green += 1
        self.assertGreater(red, 0)
        self.assertGreater(green, 0)

        QTest.mouseMove(menu, menu.actionGeometry(first).center())
        QTest.qWait(350)
        self.assertIs(menu.activeAction(), first)
        self.assertTrue(normal.isVisible())
        normal.hide()
        menu.setActiveAction(second)
        QTest.keyClick(menu, Qt.Key_Right)
        self.assertTrue(subtitle.isVisible())
        menu.hide()

    def test_preview_preserves_point_and_pixel_ui_sizes_without_changing_definition(self) -> None:
        for pixels in (False, True):
            base_font = QFont("Arial", 10)
            if pixels:
                base_font.setPixelSize(15)
            for document_size in (1, 40, 100):
                with self.subTest(pixels=pixels, document_size=document_size):
                    definition = replace(default_style_set().get("title"), font_size=document_size)
                    font = preview_font(definition, base_font)
                    self.assertEqual(font.pointSizeF(), base_font.pointSizeF())
                    self.assertEqual(font.pixelSize(), base_font.pixelSize())
                    self.assertEqual(definition.font_size, document_size)
                    self.assertTrue(font.bold())
                    self.assertFalse(base_font.bold())

    def test_saved_button_uses_heading_one_family_and_color(self) -> None:
        button = QPushButton("Style 1")
        button.setStyleSheet("QPushButton{border:1px solid #555555;font:8pt 'Arial';}")
        button.ensurePolished()
        original_font = button.font()
        original_sheet = button.styleSheet()
        saved = default_style_set().update_style(
            "heading_1",
            replace(
                default_style_set().get("heading_1"),
                font_family="Another Missing Font",
                font_color="#ff0000",
            ),
        )
        set_slot_style_preview(button, saved)
        self.assertEqual(button.text(), "Style 1")
        self.assertTrue(button.font().family())
        self.assertEqual(button.font().pointSizeF(), original_font.pointSizeF())
        self.assertIn("#ffff0000", button.styleSheet())
        self.assertIn("border:1px solid #555555", button.styleSheet())
        button.show()
        self.app.processEvents()
        image = button.grab().toImage()
        self.assertTrue(
            any(
                (pixel := image.pixelColor(x, y)).red() > 190
                and pixel.green() < 90
                and pixel.blue() < 90
                for y in range(image.height())
                for x in range(image.width())
            )
        )
        set_slot_style_preview(button, None)
        self.assertEqual(button.styleSheet(), original_sheet)
        self.assertEqual(button.font(), original_font)
        button.hide()


if __name__ == "__main__":
    unittest.main()
