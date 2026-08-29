import json
import tempfile
import unittest
import uuid
from pathlib import Path

from config import CONTROL_FILES, REQUIRED_SLIDES
from saved_letters import (
    PROMPT_WRITER_STATE_FILE,
    SavedLetterCatalog,
    SavedLetterRestorer,
)


class SavedLetterRecipientRestoreTests(unittest.TestCase):
    def test_saved_recipient_rebinds_when_registry_id_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "output" / "Play" / "Old Recipient" / "Old Letter"
            pages = bundle / "gallery" / "pages"
            message = bundle / "gallery" / "message"
            controls = bundle / "gallery" / "controls"
            sounds = bundle / "gallery" / "sounds"
            pages.mkdir(parents=True)
            message.mkdir(parents=True)
            controls.mkdir(parents=True)
            sounds.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            (pages / "lettersmith-images.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "slots": {
                            name.removesuffix(".png"): {
                                "asset_type": "static",
                                "is_animated_gif": False,
                                "source_file": name,
                                "preview_file": name,
                            }
                            for name in REQUIRED_SLIDES
                        },
                    }
                ),
                encoding="utf-8",
            )
            for name in CONTROL_FILES:
                (controls / name).write_bytes(b"control")
            (message / "message.html").write_text(
                "<p>Saved letter</p>",
                encoding="utf-8",
            )
            (sounds / "lettersmith-sound.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "single",
                        "playlist_expanded": True,
                        "selected_track_index": -1,
                        "tracks": [],
                    }
                ),
                encoding="utf-8",
            )
            (bundle / PROMPT_WRITER_STATE_FILE).write_text(
                "{}",
                encoding="utf-8",
            )
            (bundle / "index.html").write_text("<title>Old Letter</title>", encoding="utf-8")
            (bundle / "styles.css").write_text("", encoding="utf-8")
            (bundle / "script.js").write_text("", encoding="utf-8")
            project_id = str(uuid.uuid4())
            (bundle / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "document_type": "saved_letter",
                        "project_id": project_id,
                        "recipient_id": str(uuid.uuid4()),
                        "recipient_name": "Old Recipient",
                        "recipient_title": "Old Letter",
                        "settings": {},
                        "sound": {},
                        "readiness": {},
                        "editable_assets": {
                            "pages": {
                                name: f"gallery/pages/{name}"
                                for name in REQUIRED_SLIDES
                            },
                            "message": "gallery/message/message.html",
                            "sound_manifest": "gallery/sounds/lettersmith-sound.json",
                            "prompt_writer_state": PROMPT_WRITER_STATE_FILE,
                            "image_manifest": "gallery/pages/lettersmith-images.json",
                        },
                        "cover_thumbnail_path": "gallery/pages/cover.png",
                    }
                ),
                encoding="utf-8",
            )

            entry = SavedLetterCatalog(root).list_entries()[0]
            restorer = SavedLetterRestorer(root)
            identified = restorer.ensure_entry_identity(entry)
            restored = restorer.restore(identified)

            self.assertEqual(identified.path, bundle.resolve())
            self.assertEqual(restored.play_dir, bundle.resolve())
            self.assertEqual(restored.recipient, "Old Recipient")
            self.assertEqual(restored.project_id, project_id)
            self.assertTrue(restored.recipient_id)
            self.assertEqual(restored.recipient_id, identified.recipient_id)
            record = restorer.registry.find_by_id(restored.recipient_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.display_name, "Old Recipient")
            metadata = json.loads(
                (bundle / "lettersmith-metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["recipient_id"], restored.recipient_id)


if __name__ == "__main__":
    unittest.main()
