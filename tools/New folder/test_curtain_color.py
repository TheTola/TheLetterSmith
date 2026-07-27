from __future__ import annotations

import colorsys
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import generate
from curtain_color import extract_deep_dominant_color, write_tinted_curtain_image
from Template import TEMPLATE_CSS, TEMPLATE_HTML, TEMPLATE_JS


class CurtainColorTests(unittest.TestCase):
    def _write_image(self, pixels: list[tuple[int, int, int, int]], size: tuple[int, int]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sample.png"
        image = Image.new("RGBA", size)
        image.putdata(pixels)
        image.save(path)
        return path

    def test_ignores_noise_and_deepens_dominant_red(self) -> None:
        pixels: list[tuple[int, int, int, int]] = []
        pixels.extend([(255, 255, 255, 255)] * 120)
        pixels.extend([(245, 246, 244, 255)] * 80)
        pixels.extend([(0, 0, 0, 255)] * 70)
        pixels.extend([(24, 24, 24, 255)] * 60)
        pixels.extend([(150, 150, 150, 255)] * 50)
        pixels.extend([(0, 0, 0, 0)] * 40)
        pixels.extend([(205, 116, 125, 255)] * 80)

        rgb = extract_deep_dominant_color(self._write_image(pixels, (50, 10)))
        hue, sat, val = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))

        self.assertGreater(rgb[0], rgb[1])
        self.assertGreater(rgb[0], rgb[2])
        self.assertGreaterEqual(sat, 0.70)
        self.assertLessEqual(val, 0.68)
        self.assertTrue(hue <= 0.04 or hue >= 0.94)

    def test_returns_white_when_image_has_no_meaningful_color(self) -> None:
        pixels = (
            [(255, 255, 255, 255)] * 60
            + [(244, 244, 244, 255)] * 60
            + [(12, 12, 12, 255)] * 60
            + [(128, 128, 128, 255)] * 60
            + [(170, 168, 166, 255)] * 60
            + [(30, 80, 120, 0)] * 60
        )

        rgb = extract_deep_dominant_color(self._write_image(pixels, (30, 12)))

        self.assertEqual(rgb, (255, 255, 255))

    def test_tinted_curtain_preserves_alpha_and_shading(self) -> None:
        src = self._write_image(
            [
                (255, 255, 255, 255),
                (120, 120, 120, 180),
                (0, 0, 0, 0),
                (220, 220, 220, 90),
            ],
            (2, 2),
        )
        dst = src.with_name("tinted.png")

        write_tinted_curtain_image(src, dst, (120, 20, 30))

        with Image.open(dst).convert("RGBA") as image:
            getter = getattr(image, "get_flattened_data", None)
            pixels = list(getter() if callable(getter) else image.getdata())

        self.assertEqual(pixels[0][3], 255)
        self.assertEqual(pixels[1][3], 180)
        self.assertEqual(pixels[2][3], 0)
        self.assertGreater(pixels[0][0], pixels[1][0])
        self.assertGreater(pixels[0][1], pixels[1][1])

    def test_generate_copies_tassel_overlays_without_tinting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = root / "gallery" / "user" / "pages"
            controls = root / "gallery" / "user" / "card" / "controls"
            message = root / "gallery" / "user" / "message"
            pages.mkdir(parents=True)
            controls.mkdir(parents=True)
            message.mkdir(parents=True)

            for name in ("cover.png", "letter.png", "back.png"):
                Image.new("RGBA", (8, 8), (240, 240, 240, 255)).save(pages / name)
            Image.new("RGBA", (8, 8), (150, 115, 205, 255)).save(pages / "wall.png")

            for name in ("npage.png", "ppage.png", "volon.png", "voloff.png", "showmessageicon.png"):
                Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / name)
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / "cleft.png")
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(controls / "cright.png")
            Image.new("RGBA", (4, 4), (181, 134, 22, 125)).save(controls / "R_cleft.png")
            Image.new("RGBA", (4, 4), (181, 134, 22, 125)).save(controls / "R_cright.png")

            (message / "message.html").write_text("<p>Hello</p>", encoding="utf-8")
            (root / "settings.json").write_text(
                json.dumps(
                    {
                        "recipient_name": "Test",
                        "recipient_title": "Curtain",
                        "curtain_style": "average_color",
                    }
                ),
                encoding="utf-8",
            )

            play_dir = generate.generate_play_bundle(str(root), message_html="<p>Hello</p>", seed_sfx=False)
            with Image.open(play_dir / "gallery" / "controls" / "cleft.png").convert("RGBA") as image:
                curtain_pixel = image.getpixel((1, 1))
            with Image.open(play_dir / "gallery" / "controls" / "R_cleft.png").convert("RGBA") as image:
                overlay_pixel = image.getpixel((1, 1))

            self.assertNotEqual(curtain_pixel[:3], (255, 255, 255))
            self.assertEqual(overlay_pixel, (181, 134, 22, 125))

    def test_template_layers_tassel_overlays_with_matching_curtain_motion(self) -> None:
        self.assertIn('href="gallery/controls/R_cleft.png"', TEMPLATE_HTML)
        self.assertIn('href="gallery/controls/R_cright.png"', TEMPLATE_HTML)
        self.assertIn('id="curtain-left-detail"', TEMPLATE_HTML)
        self.assertIn('id="curtain-right-detail"', TEMPLATE_HTML)
        self.assertIn("#curtain-left-detail", TEMPLATE_CSS)
        self.assertIn("#curtain-right-detail", TEMPLATE_CSS)
        self.assertIn("leftCurtainLayers", TEMPLATE_JS)
        self.assertIn("rightCurtainLayers", TEMPLATE_JS)
        self.assertIn("curtainLeftOut ${openMs}ms", TEMPLATE_JS)
        self.assertIn("curtainRightOut ${openMs}ms", TEMPLATE_JS)


if __name__ == "__main__":
    unittest.main()
