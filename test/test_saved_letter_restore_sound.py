from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from config import CONTROL_FILES, REQUIRED_SLIDES
from recipient_registry import RecipientRegistry
from saved_letters import SavedLetterCatalog, SavedLetterRestoreError, SavedLetterRestorer
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from sound_model import (
    ProjectSoundState,
    current_music_path,
    import_runtime_track,
    load_library,
    load_project_state,
    project_sound_path,
    save_project_state,
    sync_current_compatibility,
)


class SavedLetterSoundRestoreTests(unittest.TestCase):
    def _bundle(self, root: Path, *, recipient: str = "Shariana Parker", title: str = "A Joyful Noise") -> Path:
        bundle = root / "output" / "Play" / recipient / title
        pages = bundle / "gallery" / "pages"
        message = bundle / "gallery" / "message"
        controls = bundle / "gallery" / "controls"
        pages.mkdir(parents=True)
        message.mkdir(parents=True)
        controls.mkdir(parents=True)
        for name in REQUIRED_SLIDES:
            (pages / name).write_bytes(name.encode("ascii"))
        for name in CONTROL_FILES:
            (controls / name).write_bytes(b"control")
        (message / "message.html").write_text("<p>Saved letter</p>", encoding="utf-8")
        (bundle / "index.html").write_text(f"<title>{title}</title>", encoding="utf-8")
        (bundle / "styles.css").write_text("", encoding="utf-8")
        (bundle / "script.js").write_text("", encoding="utf-8")
        registry = RecipientRegistry(root)
        record = registry.get_or_create(recipient)
        (bundle / "lettersmith-metadata.json").write_text(
            json.dumps(
                {
                    "project_id": str(uuid.uuid4()),
                    "recipient_id": record.recipient_id,
                    "recipient_name": recipient,
                    "recipient_title": title,
                }
            ),
            encoding="utf-8",
        )
        return bundle

    def _restore(self, root: Path, bundle: Path):
        entry = SavedLetterCatalog(root).list_entries()[0]
        return SavedLetterRestorer(root).restore(entry)

    def test_playlist_restore_deduplicates_and_preserves_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "unrelated.mp3"
            unrelated.write_bytes(b"unrelated")
            unrelated_record = import_runtime_track(root, unrelated, display_title="Unrelated")
            bundle = self._bundle(root)
            sounds = bundle / "gallery" / "sounds"
            sounds.mkdir()
            tracks = []
            for index, content in enumerate((b"one", b"two", b"three"), start=1):
                filename = f"music{'-' + str(index).zfill(3) if index > 1 else ''}.mp3"
                (sounds / filename).write_bytes(content)
                tracks.append({"filename": filename, "display_title": f"Track {index}"})
            (sounds / "lettersmith-sound.json").write_text(
                json.dumps({"version": 2, "mode": "playlist", "crossfade_ms": 1000, "tracks": tracks}),
                encoding="utf-8",
            )

            self._restore(root, bundle)

            state = load_project_state(root)
            library = load_library(root)
            self.assertEqual(state.mode, "playlist")
            self.assertEqual(len(state.playlist), 3)
            self.assertIn(unrelated_record.track_id, library)
            self.assertEqual(len(library), 4)
            self.assertEqual(current_music_path(root).read_bytes(), b"one")
            self.assertFalse((root / "gallery" / "user" / "sounds.load-backup").exists())

            self._restore(root, bundle)
            self.assertEqual(len(load_library(root)), 4)

    def test_manifestless_sound_bundle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["settings"] = {"required_features": {"music": True}}
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            sounds = bundle / "gallery" / "sounds"
            sounds.mkdir()
            (sounds / "music.mp3").write_bytes(b"retired-format")

            with self.assertRaises(SavedLetterRestoreError):
                self._restore(root, bundle)

    def test_silent_letter_clears_project_sound_without_deleting_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.mp3"
            existing.write_bytes(b"existing")
            record = import_runtime_track(root, existing, display_title="Existing")
            state = ProjectSoundState(mode="single", single_track_id=record.track_id, selected_track_id=record.track_id)
            save_project_state(root, state)
            sync_current_compatibility(root, state, load_library(root))
            bundle = self._bundle(root)

            self._restore(root, bundle)

            self.assertFalse(load_project_state(root).ordered_track_ids())
            self.assertFalse(current_music_path(root).exists())
            self.assertIn(record.track_id, load_library(root))

    def test_restore_commits_active_play_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)

            restored = self._restore(root, bundle)

            self.assertEqual(restored.play_dir, bundle.resolve())
            self.assertEqual(
                SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY),
                str(bundle.resolve()),
            )

    def test_failure_restores_active_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_pages = root / "gallery" / "user" / "pages"
            active_message = root / "gallery" / "user" / "message"
            active_pages.mkdir(parents=True)
            active_message.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (active_pages / name).write_bytes(b"current")
            (active_message / "message.html").write_text("<p>current</p>", encoding="utf-8")
            settings = SettingsStore(root)
            existing = root / "existing.mp3"
            existing.write_bytes(b"current")
            record = import_runtime_track(root, existing, display_title="Current")
            active_state = ProjectSoundState(mode="single", single_track_id=record.track_id, selected_track_id=record.track_id)
            save_project_state(root, active_state)
            sync_current_compatibility(root, active_state, load_library(root))
            settings.update_fields({"recipient_name": "Amanda", "recipient_title": "Current"})
            old_settings = settings.snapshot()
            bundle = self._bundle(root)
            sounds = bundle / "gallery" / "sounds"
            sounds.mkdir()
            (sounds / "music.mp3").write_bytes(b"new")

            restorer = SavedLetterRestorer(root)
            restorer._verify_committed_state = lambda: (_ for _ in ()).throw(RuntimeError("injected"))
            entry = SavedLetterCatalog(root).list_entries()[0]
            with self.assertRaises(SavedLetterRestoreError):
                restorer.restore(entry)

            self.assertEqual((active_pages / "letter.png").read_bytes(), b"current")
            self.assertEqual((active_message / "message.html").read_text(encoding="utf-8"), "<p>current</p>")
            self.assertEqual(settings.snapshot().get("recipient_name"), old_settings.get("recipient_name"))
            self.assertEqual(current_music_path(root).read_bytes(), b"current")
            self.assertEqual(load_project_state(root).single_track_id, record.track_id)
            self.assertTrue(project_sound_path(root).exists())


if __name__ == "__main__":
    unittest.main()
