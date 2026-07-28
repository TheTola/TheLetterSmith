from __future__ import annotations

import ast
import colorsys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from curtain_color import curtain_rgb_for_style
from project_sync import file_fingerprint, image_fingerprint, settings_fingerprint

ROOT = Path(__file__).resolve().parent


class ContractTests(unittest.TestCase):
    def test_forge_matches_nexus_contract(self) -> None:
        tree = ast.parse((ROOT / "Forge_Tab.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ForgeTab")
        methods = {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}
        signals: set[str] = set()
        for node in cls.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        signals.add(target.id)

        self.assertFalse(
            {
                "attach_readiness_window",
                "schedule_refresh",
                "ensure_preview_current",
                "current_play_index",
                "refresh_project_state",
                "refresh_saved_letters",
                "set_saved_page_url",
                "shutdown_operations",
                "activate_for_tab_change",
                "deactivate_for_tab_change",
                "sync_all_from_disk",
            }
            - methods
        )
        self.assertFalse(
            {
                "project_restored",
                "correction_requested",
                "preview_requested",
                "preview_files_release_requested",
                "preview_visibility_changed",
                "published_url_changed",
            }
            - signals
        )

    def test_tab_compatibility_methods(self) -> None:
        expectations = {
            "Image_tab.py": {"refresh_from_disk", "focus_asset_slot", "activate_for_tab_change", "deactivate_for_tab_change"},
            "sound_tab.py": {"refresh_from_disk", "focus_music_editor", "activate_for_tab_change", "deactivate_for_tab_change"},
            "Message_tab.py": {"refresh_from_disk", "focus_field", "activate_for_tab_change", "deactivate_for_tab_change", "shutdown"},
        }
        for filename, required in expectations.items():
            tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
            methods = {node.name for cls in classes for node in cls.body if isinstance(node, ast.FunctionDef)}
            self.assertFalse(required - methods, f"{filename}: {sorted(required - methods)}")

    def test_curtain_menu_labels_exist(self) -> None:
        settings = (ROOT / "settings_store.py").read_text(encoding="utf-8")
        nexus = (ROOT / "Nexus.py").read_text(encoding="utf-8")
        self.assertIn("CURTAIN_STYLE_LABELS", settings)
        self.assertIn('"light": "Light Curtain"', settings)
        self.assertIn('"dark": "Dark Curtain"', settings)
        self.assertIn('addMenu("Curtains")', nexus)

    def test_button_mappings(self) -> None:
        message = (ROOT / "Message_tab.py").read_text(encoding="utf-8")
        forge = (ROOT / "Forge_Tab.py").read_text(encoding="utf-8")
        for token in ("LButton.png", "PButton.png", "RButton.png", '"Import"', '"Edit"', '"Revisions"'):
            self.assertIn(token, message)
        for token in (
            "MButton.png",
            "EButton.png",
            "ROButton.png",
            "IButton.png",
            '"Preview Letter"',
            '"Publish Letter"',
            '"Open Letter"',
            '"Load Letter"',
        ):
            self.assertIn(token, forge)

    def test_fullscreen_css_is_visible_before_start(self) -> None:
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("#fullscreen-toggle", css)
        self.assertIn("body:not(.stage-ready) #fullscreen-toggle", css)
        self.assertIn("fullscreenAttention", css)


class FingerprintTests(unittest.TestCase):
    def test_direct_replacement_changes_image_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            before = image_fingerprint(root)
            cover = pages / "cover.png"
            cover.write_bytes(b"first")
            first = image_fingerprint(root)
            cover.write_bytes(b"second")
            second = image_fingerprint(root)
            self.assertNotEqual(before, first)
            self.assertNotEqual(first, second)

    def test_json_formatting_does_not_invalidate_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = root / "settings.json"
            settings.write_text('{"starting_volume":31}', encoding="utf-8")
            first = settings_fingerprint(root)
            settings.write_text('{\n  "starting_volume": 31\n}', encoding="utf-8")
            self.assertEqual(first, settings_fingerprint(root))

    def test_single_file_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "message.html"
            before = file_fingerprint(path)
            path.write_text("<p>Hello</p>", encoding="utf-8")
            self.assertNotEqual(before, file_fingerprint(path))


class CurtainTests(unittest.TestCase):
    def test_light_and_dark_preserve_a_pink_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "pink.png"
            Image.new("RGB", (64, 64), (85, 10, 45)).save(image_path)
            light = curtain_rgb_for_style([image_path], "light")
            dark = curtain_rgb_for_style([image_path], "dark")
            _lh, lightness, light_saturation = colorsys.rgb_to_hls(*(value / 255 for value in light))
            _dh, darkness, dark_saturation = colorsys.rgb_to_hls(*(value / 255 for value in dark))
            self.assertGreater(lightness, 0.88)
            self.assertGreater(light_saturation, 0.18)
            self.assertLess(darkness, 0.20)
            self.assertGreater(dark_saturation, 0.45)


if __name__ == "__main__":
    unittest.main()
