from __future__ import annotations

import unittest

from PySide6 import QtGui

from Message_tab import (
    _effective_message_overlay_opacity,
    _soft_blur_message_background,
)
from Template import TEMPLATE_CSS, TEMPLATE_HTML
from generate import _message_overlay_style_from_settings


class MessageOverlayTests(unittest.TestCase):
    def test_colored_presets_keep_their_surface(self) -> None:
        for preset, rgb in (
            ("paper", "245,235,210"),
            ("black", "0,0,0"),
            ("white", "255,255,255"),
        ):
            with self.subTest(preset=preset):
                style = _message_overlay_style_from_settings(
                    {
                        "message_overlay_preset": preset,
                        "message_overlay_opacity": 68,
                    }
                )
                self.assertIn(f"--message-overlay-rgb:{rgb}", style)
                self.assertIn("--message-overlay-surface-opacity:0.680", style)
                self.assertIn("--message-overlay-blur:0px", style)

    def test_transparent_preset_is_readable_blurred_glass(self) -> None:
        style = _message_overlay_style_from_settings(
            {
                "message_overlay_preset": "clear",
                "message_overlay_opacity": 0,
            }
        )
        self.assertIn("--message-overlay-opacity:0.000", style)
        self.assertIn("--message-overlay-surface-opacity:0.180", style)
        self.assertIn("--message-overlay-blur:14px", style)
        self.assertEqual(_effective_message_overlay_opacity("clear", 0), 18)

        source = QtGui.QImage(72, 72, QtGui.QImage.Format_RGB32)
        for y in range(source.height()):
            for x in range(source.width()):
                source.setPixelColor(
                    x,
                    y,
                    QtGui.QColor("#000000" if (x + y) % 2 else "#ffffff"),
                )
        blurred = _soft_blur_message_background(source)
        center = blurred.pixelColor(36, 36)
        self.assertEqual(blurred.size(), source.size())
        self.assertNotIn(center.red(), (0, 255))

    def test_texture_does_not_replace_the_surface_color(self) -> None:
        self.assertIn(
            "background-color:rgba(var(--message-overlay-rgb),"
            "var(--message-overlay-surface-opacity))",
            TEMPLATE_CSS,
        )
        self.assertIn(
            "backdrop-filter:blur(var(--message-overlay-blur))",
            TEMPLATE_CSS,
        )

    def test_close_button_is_inside_the_message_area(self) -> None:
        wall_index = TEMPLATE_HTML.index('class="text-wall"')
        close_index = TEMPLATE_HTML.index('id="close-text"')
        content_index = TEMPLATE_HTML.index('id="textWallContent"')
        self.assertLess(wall_index, close_index)
        self.assertLess(close_index, content_index)
        self.assertIn("#close-text{position:sticky", TEMPLATE_CSS)


if __name__ == "__main__":
    unittest.main()
