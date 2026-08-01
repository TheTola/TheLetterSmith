import json
import tempfile
import unittest
import uuid
from pathlib import Path

from config import CONTROL_FILES, REQUIRED_SLIDES
from saved_letters import SavedLetterCatalog, SavedLetterRestorer


class SavedLetterRecipientRestoreTests(unittest.TestCase):
    def test_saved_recipient_rebinds_when_registry_id_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "output" / "Play" / "Old Recipient" / "Old Letter"
            pages = bundle / "gallery" / "pages"
            message = bundle / "gallery" / "message"
            controls = bundle / "gallery" / "controls"
            pages.mkdir(parents=True)
            message.mkdir(parents=True)
            controls.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            for name in CONTROL_FILES:
                (controls / name).write_bytes(b"control")
            (message / "message.html").write_text(
                "<p>Saved letter</p>",
                encoding="utf-8",
            )
            (bundle / "index.html").write_text("<title>Old Letter</title>", encoding="utf-8")
            (bundle / "styles.css").write_text("", encoding="utf-8")
            (bundle / "script.js").write_text("", encoding="utf-8")
            project_id = str(uuid.uuid4())
            (bundle / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "project_id": project_id,
                        "recipient_id": str(uuid.uuid4()),
                        "recipient_name": "Old Recipient",
                        "recipient_title": "Old Letter",
                    }
                ),
                encoding="utf-8",
            )

            entry = SavedLetterCatalog(root).list_entries()[0]
            restorer = SavedLetterRestorer(root)
            restored = restorer.ensure_entry_identity(entry)

            self.assertEqual(restored.recipient, "Old Recipient")
            self.assertEqual(restored.project_id, project_id)
            self.assertTrue(restored.recipient_id)
            self.assertEqual(restored.recipient_id, entry.recipient_id)
            record = restorer.registry.find_by_id(restored.recipient_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.display_name, "Old Recipient")
            metadata = json.loads(
                (bundle / "lettersmith-metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["recipient_id"], restored.recipient_id)


if __name__ == "__main__":
    unittest.main()
