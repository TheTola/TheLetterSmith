import tempfile
import unittest
from pathlib import Path

from project_store import ProjectStore
from settings_store import SettingsStore


class ProjectStoreTests(unittest.TestCase):
    def test_save_open_duplicate_and_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pages = root / "gallery/user/pages"
            message = root / "gallery/user/message"
            sounds = root / "gallery/user/sounds"
            for folder in (pages, message, sounds):
                folder.mkdir(parents=True, exist_ok=True)
            (pages / "cover.png").write_bytes(b"first")
            (message / "message.html").write_text("<p>First</p>", encoding="utf-8")
            (sounds / "music.mp3").write_bytes(b"music")
            SettingsStore(root).update_fields(
                {"recipient_name": "Ada", "recipient_title": "Hello"}
            )

            store = ProjectStore(root)
            original = store.save_as("First Project")
            self.assertEqual(store.active_project_id, original.project_id)

            (message / "message.html").write_text("<p>Second</p>", encoding="utf-8")
            store.save_active()
            self.assertEqual(len(store.list_message_revisions()), 1)

            duplicate = store.duplicate_active("Second Project")
            self.assertNotEqual(duplicate.project_id, original.project_id)
            (pages / "cover.png").write_bytes(b"changed")
            store.open(original.project_id)
            self.assertEqual((pages / "cover.png").read_bytes(), b"first")
            self.assertEqual(
                (message / "message.html").read_text(encoding="utf-8"),
                "<p>Second</p>",
            )

    def test_empty_project_clears_project_assets_but_keeps_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pages = root / "gallery/user/pages"
            message = root / "gallery/user/message"
            archive = root / "gallery/user/sounds/appssong/originals"
            for folder in (pages, message, archive):
                folder.mkdir(parents=True, exist_ok=True)
            (pages / "cover.png").write_bytes(b"cover")
            (message / "message.html").write_text("<p>Letter</p>", encoding="utf-8")
            (archive / "kept.mp3").write_bytes(b"archive")

            ProjectStore(root).create("Blank")

            self.assertFalse((pages / "cover.png").exists())
            self.assertFalse((message / "message.html").exists())
            self.assertTrue((archive / "kept.mp3").exists())


if __name__ == "__main__":
    unittest.main()
