from __future__ import annotations

import unittest
import colorsys
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from Message_tab import (
    MessageTab,
    RevisionHistoryDialog,
    _apply_adaptive_micro_contrast,
    _effective_message_overlay_opacity,
)
from Template import TEMPLATE_CSS, TEMPLATE_HTML
from generate import _message_overlay_style_from_settings
from letter_page import (
    LETTER_PAGE_PRESET_LABELS,
    adaptive_text_rgb,
    composite_rgb,
    contrast_ratio,
    normalize_letter_page_preset,
    surface_rgb_at,
)


class MessageOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_preview_emits_full_resolution_message_and_wall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            message_path = Path(directory) / "message.png"
            wall_path = Path(directory) / "wall.png"
            for path, size in (
                (message_path, (2048, 3072)),
                (wall_path, (1024, 1536)),
            ):
                image = QtGui.QImage(*size, QtGui.QImage.Format_RGB32)
                image.fill(QtGui.QColor("white"))
                self.assertTrue(image.save(str(path)))

            emitted = []
            tab = SimpleNamespace(
                _png_path=lambda: message_path,
                _render_wall_path=lambda: wall_path,
                preview_image=SimpleNamespace(emit=emitted.append),
                status=SimpleNamespace(setText=lambda _text: None),
            )
            MessageTab._emit_best_preview(tab)
            self.assertEqual(emitted[-1].size(), QtCore.QSize(2048, 3072))

            message_path.unlink()
            MessageTab._emit_best_preview(tab)
            self.assertEqual(emitted[-1].size(), QtCore.QSize(1024, 1536))

    def test_revision_history_is_a_frameless_lettersmith_panel(self) -> None:
        message_tab = QtWidgets.QWidget()
        message_tab._html_path = lambda: Path("missing.html")
        dialog = RevisionHistoryDialog(message_tab)
        self.addCleanup(message_tab.deleteLater)
        self.addCleanup(dialog.deleteLater)

        self.assertTrue(dialog.windowFlags() & QtCore.Qt.FramelessWindowHint)
        self.assertEqual(dialog.heading.text(), "Revision History")
        self.assertIn(
            "Close",
            {button.text() for button in dialog.findChildren(QtWidgets.QPushButton)},
        )

    def test_colored_presets_keep_their_surface(self) -> None:
        for preset, rgb in (
            ("paper", "245,235,210"),
            ("black", "15,15,15"),
            ("white", "247,248,250"),
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

    def test_transparent_preset_keeps_the_artwork_uncovered(self) -> None:
        style = _message_overlay_style_from_settings(
            {
                "message_overlay_preset": "clear",
                "message_overlay_opacity": 0,
            }
        )
        self.assertIn("--message-overlay-opacity:0.000", style)
        self.assertIn("--message-overlay-surface-opacity:0.000", style)
        self.assertIn("--message-overlay-border:transparent", style)
        self.assertIn("--message-overlay-shadow:none", style)
        self.assertIn("--adaptive-max-lightness:0.050", style)
        self.assertEqual(_effective_message_overlay_opacity("clear", 0), 0)

    def test_surfaces_use_subtle_tonal_depth_without_texture_or_blur(self) -> None:
        self.assertIn(
            "background:radial-gradient(ellipse at 50% 43%",
            TEMPLATE_CSS,
        )
        self.assertNotIn("message-overlay-texture", TEMPLATE_CSS)
        self.assertNotIn("message-overlay-blur", TEMPLATE_CSS)
        self.assertNotIn("filter: contrast", TEMPLATE_CSS.casefold())

    def test_exact_four_labels_and_legacy_aliases(self) -> None:
        self.assertEqual(
            list(LETTER_PAGE_PRESET_LABELS.values()),
            ["Warm Paper", "Dark Panel", "Light Panel", "Transparent"],
        )
        for legacy, expected in (
            ("warm paper", "paper"),
            ("dark", "black"),
            ("light", "white"),
            ("transparent", "clear"),
        ):
            with self.subTest(legacy=legacy):
                self.assertEqual(normalize_letter_page_preset(legacy), expected)

    def test_micro_contrast_preserves_hue_and_respects_caps(self) -> None:
        intended = (216, 184, 90)
        for maximum in (0.03, 0.05):
            adjusted = adaptive_text_rgb(
                intended,
                (205, 175, 82),
                max_lightness_shift=maximum,
            )
            original_hls = colorsys.rgb_to_hls(*(value / 255 for value in intended))
            adjusted_hls = colorsys.rgb_to_hls(*(value / 255 for value in adjusted))
            self.assertAlmostEqual(original_hls[0], adjusted_hls[0], places=2)
            self.assertAlmostEqual(original_hls[2], adjusted_hls[2], places=2)
            self.assertLessEqual(
                abs(original_hls[1] - adjusted_hls[1]),
                maximum + 0.001,
            )
        self.assertEqual(
            adaptive_text_rgb((0, 0, 0), (255, 255, 255), max_lightness_shift=0.03),
            (0, 0, 0),
        )

    def test_requested_color_matrix_never_loses_contrast_or_exceeds_cap(self) -> None:
        colors = (
            (0, 0, 0),
            (255, 255, 255),
            (43, 28, 18),
            (238, 234, 226),
            (220, 38, 38),
            (39, 104, 199),
            (216, 184, 90),
            (255, 0, 180),
            (126, 119, 103),
        )
        backgrounds = ((4, 4, 6), (250, 250, 250), (126, 119, 103))
        for maximum in (0.03, 0.05):
            for intended in colors:
                for background in backgrounds:
                    with self.subTest(
                        maximum=maximum,
                        intended=intended,
                        background=background,
                    ):
                        adjusted = adaptive_text_rgb(
                            intended,
                            background,
                            max_lightness_shift=maximum,
                        )
                        self.assertGreaterEqual(
                            contrast_ratio(adjusted, background) + 1e-9,
                            contrast_ratio(intended, background),
                        )
                        before = colorsys.rgb_to_hls(
                            *(value / 255 for value in intended)
                        )
                        after = colorsys.rgb_to_hls(
                            *(value / 255 for value in adjusted)
                        )
                        self.assertLessEqual(
                            abs(before[1] - after[1]),
                            maximum + 0.001,
                        )

    def test_composited_panel_color_uses_artwork_and_opacity(self) -> None:
        center = surface_rgb_at("paper", 0.5, 0.43)
        edge = surface_rgb_at("paper", 0.0, 0.0)
        self.assertNotEqual(center, edge)
        self.assertEqual(composite_rgb((0, 0, 0), center, 0.0), (0, 0, 0))
        self.assertEqual(composite_rgb((0, 0, 0), center, 1.0), center)

    def test_qt_render_adjusts_characters_against_their_local_background(self) -> None:
        document = QtGui.QTextDocument()
        document.setHtml('<span style="font-size:80px;color:#808080">AB</span>')
        document.setTextWidth(300)
        document.documentLayout().documentSize()
        block = document.firstBlock()
        line = block.layout().lineForTextPosition(0)
        cursor_x = line.cursorToX(1)
        split = round(cursor_x[0] if isinstance(cursor_x, tuple) else cursor_x)
        background = QtGui.QImage(300, 160, QtGui.QImage.Format_RGB32)
        background.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(background)
        painter.fillRect(split, 0, 300 - split, 160, QtGui.QColor("black"))
        painter.end()

        changed = _apply_adaptive_micro_contrast(
            document,
            background,
            preset="clear",
            origin=QtCore.QPointF(0, 0),
            fallback_ink="#808080",
        )
        colors = []
        for position in (0, 1):
            cursor = QtGui.QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + 1, QtGui.QTextCursor.KeepAnchor)
            colors.append(cursor.charFormat().foreground().color().name())
        self.assertEqual(changed, 2)
        self.assertNotEqual(colors[0], colors[1])

    def test_qt_render_includes_rich_text_background_in_local_sample(self) -> None:
        document = QtGui.QTextDocument()
        document.setHtml(
            '<span style="font-size:40px;color:#808080;'
            'background-color:#808080">A</span>'
        )
        document.setTextWidth(120)
        background = QtGui.QImage(120, 80, QtGui.QImage.Format_RGB32)
        background.fill(QtGui.QColor("black"))
        _apply_adaptive_micro_contrast(
            document,
            background,
            preset="clear",
            origin=QtCore.QPointF(0, 0),
            fallback_ink="#808080",
        )
        cursor = QtGui.QTextCursor(document)
        cursor.setPosition(0)
        cursor.setPosition(1, QtGui.QTextCursor.KeepAnchor)
        self.assertLess(cursor.charFormat().foreground().color().red(), 128)

    def test_browser_uses_per_character_highlights(self) -> None:
        from Template import TEMPLATE_JS

        self.assertIn("installAdaptiveMicroContrast", TEMPLATE_JS)
        self.assertIn("document.createRange()", TEMPLATE_JS)
        self.assertIn("CSS.highlights.set", TEMPLATE_JS)
        self.assertIn("glyph-spans", TEMPLATE_JS)
        self.assertIn("ls-amc-glyph", TEMPLATE_JS)
        self.assertNotIn("filter: contrast", TEMPLATE_JS.casefold())

    def test_close_button_is_inside_the_message_area(self) -> None:
        wall_index = TEMPLATE_HTML.index('class="text-wall"')
        close_index = TEMPLATE_HTML.index('id="close-text"')
        content_index = TEMPLATE_HTML.index('id="textWallContent"')
        self.assertLess(wall_index, close_index)
        self.assertLess(close_index, content_index)
        self.assertIn("#close-text{position:sticky", TEMPLATE_CSS)


if __name__ == "__main__":
    unittest.main()
