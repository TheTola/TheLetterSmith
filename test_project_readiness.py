import tempfile
import unittest
from pathlib import Path

from config import CONTROL_FILES, FLIP_COUNT
from project_readiness import assess_project_readiness, project_is_ready
from settings_store import SettingsStore


class ProjectReadinessTests(unittest.TestCase):
    def test_required_items_and_optional_music(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            message = root / "gallery/user/message"
            app_sounds = root / "gallery/app/sounds"
            for folder in (pages, controls, message, app_sounds):
                folder.mkdir(parents=True, exist_ok=True)

            for name in ("cover.png", "letter.png", "wall.png", "back.png"):
                (pages / name).write_bytes(b"image")
            for name in CONTROL_FILES:
                (controls / name).write_bytes(b"control")
            (message / "message.html").write_text("<p>Hello there</p>", encoding="utf-8")
            (app_sounds / "glissando.mp3").write_bytes(b"sound")
            for index in range(1, FLIP_COUNT + 1):
                (app_sounds / f"flip{index}.mp3").write_bytes(b"sound")
            SettingsStore(root).update_fields(
                {"recipient_name": "Cassi", "recipient_title": "Birthday"}
            )

            items = assess_project_readiness(root)
            by_key = {item.key: item for item in items}

            self.assertTrue(project_is_ready(items))
            self.assertFalse(by_key["music"].ready)
            self.assertFalse(by_key["music"].required)

            (pages / "back.png").unlink()
            items = assess_project_readiness(root)
            self.assertFalse(project_is_ready(items))
            self.assertFalse({item.key: item for item in items}["back"].ready)


if __name__ == "__main__":
    unittest.main()
