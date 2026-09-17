from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import saved_letters
import generate
import sound_model

from config import (
    CONTROL_FILES,
    MESSAGE_ASSETS_DIR,
    REQUIRED_SLIDES,
    USER_MESSAGE_DIR,
)
from recipient_registry import RecipientRegistry
from publishing.expiration import publication_status
from readiness import ReadinessResult
from save_schema import (
    PROMPT_WRITER_STATE_VERSION,
    validate_prompt_writer_state_payload,
)
from saved_letters import (
    PROMPT_WRITER_STATE_FILE,
    SavedLetterCatalog,
    SavedLetterRestoreError,
    SavedLetterRestorer,
    save_published_snapshot,
    update_saved_metadata,
)
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from sound_model import (
    ProjectSoundState,
    archive_root,
    current_manifest_path,
    current_music_path,
    import_runtime_track,
    load_library,
    load_project_state,
    project_sound_path,
    save_project_state,
    sync_current_compatibility,
)
from transactional_io import atomic_write_json


class SavedLetterSoundRestoreTests(unittest.TestCase):
    def _bundle(self, root: Path, *, recipient: str = "Shariana Parker", title: str = "A Joyful Noise") -> Path:
        bundle = root / "output" / "Play" / recipient / title
        pages = bundle / "gallery" / "pages"
        message = bundle / "gallery" / "message"
        controls = bundle / "gallery" / "controls"
        sounds = bundle / "gallery" / "sounds"
        pages.mkdir(parents=True)
        message.mkdir(parents=True)
        controls.mkdir(parents=True)
        sounds.mkdir(parents=True)
        for name in REQUIRED_SLIDES:
            (pages / name).write_bytes(name.encode("ascii"))
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
        (message / "message.html").write_text("<p>Saved letter</p>", encoding="utf-8")
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
        (bundle / PROMPT_WRITER_STATE_FILE).write_text("{}", encoding="utf-8")
        (bundle / "index.html").write_text(f"<title>{title}</title>", encoding="utf-8")
        (bundle / "styles.css").write_text("", encoding="utf-8")
        (bundle / "script.js").write_text("", encoding="utf-8")
        registry = RecipientRegistry(root)
        record = registry.get_or_create(recipient)
        (bundle / "lettersmith-metadata.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "document_type": "saved_letter",
                    "project_id": str(uuid.uuid4()),
                    "recipient_id": record.recipient_id,
                    "recipient_name": recipient,
                    "recipient_title": title,
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
            tracks = []
            for index, content in enumerate((b"one", b"two", b"three"), start=1):
                filename = f"music{'-' + str(index).zfill(3) if index > 1 else ''}.mp3"
                (sounds / filename).write_bytes(content)
                tracks.append({"filename": filename, "display_title": f"Track {index}"})
            (sounds / "lettersmith-sound.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "playlist",
                        "playlist_expanded": False,
                        "selected_track_index": 1,
                        "crossfade_ms": 1000,
                        "tracks": tracks,
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch(
                    "saved_letters.load_library",
                    wraps=saved_letters.load_library,
                ) as load_once,
                mock.patch(
                    "saved_letters.save_library",
                    wraps=saved_letters.save_library,
                ) as save_once,
                mock.patch(
                    "saved_letters.copy_directory_tree_no_links",
                    wraps=saved_letters.copy_directory_tree_no_links,
                ) as copytree,
            ):
                self._restore(root, bundle)

            self.assertEqual(load_once.call_count, 1)
            self.assertEqual(save_once.call_count, 1)
            self.assertEqual(copytree.call_count, 2)

            state = load_project_state(root)
            library = load_library(root)
            self.assertEqual(state.mode, "playlist")
            self.assertEqual(len(state.playlist), 3)
            self.assertFalse(state.playlist_expanded)
            self.assertEqual(state.selected_track_id, state.playlist[1])
            self.assertIn(unrelated_record.track_id, library)
            self.assertEqual(len(library), 4)
            self.assertEqual(current_music_path(root).read_bytes(), b"two")
            self.assertFalse((root / "gallery" / "user" / "sounds.load-backup").exists())

            with (
                mock.patch(
                    "saved_letters.load_library",
                    wraps=saved_letters.load_library,
                ) as reload_once,
                mock.patch(
                    "saved_letters.save_library",
                    wraps=saved_letters.save_library,
                ) as no_save,
            ):
                self._restore(root, bundle)
            self.assertEqual(reload_once.call_count, 1)
            self.assertEqual(no_save.call_count, 0)
            self.assertEqual(len(load_library(root)), 4)

    def test_message_assets_replace_the_active_projects_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            message = bundle / "gallery" / "message" / "message.html"
            message.write_text(
                '<p onclick="run()">Saved letter<img src="gallery/message_assets/saved.png">'
                "<script>run()</script></p>",
                encoding="utf-8",
            )
            saved_assets = bundle / MESSAGE_ASSETS_DIR
            saved_assets.mkdir(parents=True)
            (saved_assets / "saved.png").write_bytes(b"saved asset")

            active_assets = root / MESSAGE_ASSETS_DIR
            active_assets.mkdir(parents=True)
            (active_assets / "old.png").write_bytes(b"old asset")

            self._restore(root, bundle)

            self.assertEqual(
                (active_assets / "saved.png").read_bytes(),
                b"saved asset",
            )
            self.assertFalse((active_assets / "old.png").exists())
            restored_message = (root / USER_MESSAGE_DIR / "message.html").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("onclick", restored_message)
            self.assertNotIn("<script", restored_message)

    def test_restore_without_message_assets_removes_the_active_asset_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / MESSAGE_ASSETS_DIR).mkdir(parents=True)
            active_assets = root / MESSAGE_ASSETS_DIR
            active_assets.mkdir(parents=True)
            (active_assets / "old.png").write_bytes(b"old asset")

            self._restore(root, bundle)

            self.assertFalse(active_assets.exists())

    def test_failed_restore_without_message_assets_restores_active_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            active_asset = root / MESSAGE_ASSETS_DIR / "current.png"
            active_asset.parent.mkdir(parents=True)
            active_asset.write_bytes(b"current asset")
            restorer = SavedLetterRestorer(root)
            restorer._verify_committed_state = lambda: (_ for _ in ()).throw(
                RuntimeError("injected")
            )
            entry = SavedLetterCatalog(root).list_entries()[0]

            with self.assertRaises(SavedLetterRestoreError):
                restorer.restore(entry)

            self.assertEqual(active_asset.read_bytes(), b"current asset")

    def test_restore_rejects_nested_directory_links_and_preserves_active_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            outside = root / "outside-content"
            outside.mkdir()
            (outside / "private.txt").write_text("private", encoding="utf-8")
            linked = bundle / "gallery" / "message" / "linked"
            try:
                linked.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("Directory links are unavailable on this host.")

            active = root / USER_MESSAGE_DIR / "message.html"
            active.parent.mkdir(parents=True)
            active.write_text("<p>current project</p>", encoding="utf-8")
            entry = SavedLetterCatalog(root).list_entries()[0]

            with self.assertRaises(SavedLetterRestoreError):
                SavedLetterRestorer(root).restore(entry)

            self.assertEqual(
                active.read_text(encoding="utf-8"),
                "<p>current project</p>",
            )
            self.assertFalse((root / USER_MESSAGE_DIR / "linked").exists())

    def test_compatibility_copy_is_skipped_until_destination_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected.mp3"
            source.write_bytes(b"selected audio")
            record = import_runtime_track(root, source, display_title="Selected")
            records = load_library(root)
            state = ProjectSoundState(
                mode="single",
                single_track_id=record.track_id,
                selected_track_id=record.track_id,
            )

            save_project_state(root, state)
            self.assertTrue(sync_current_compatibility(root, state, records))
            if os.name == "nt":
                hidden = stat.FILE_ATTRIBUTE_HIDDEN
                self.assertTrue(
                    project_sound_path(root).stat().st_file_attributes & hidden
                )
                self.assertTrue(
                    current_manifest_path(root).stat().st_file_attributes & hidden
                )
            with mock.patch.object(
                sound_model,
                "atomic_copy_file",
                wraps=sound_model.atomic_copy_file,
            ) as copy_file:
                self.assertFalse(
                    sync_current_compatibility(root, state, records)
                )
                copy_file.assert_not_called()

                current_music_path(root).unlink()
                self.assertTrue(
                    sync_current_compatibility(root, state, records)
                )
                copy_file.assert_called_once()

    def test_nonpersisted_runtime_import_requires_shared_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected.mp3"
            source.write_bytes(b"selected audio")

            with self.assertRaisesRegex(ValueError, "shared library mapping"):
                import_runtime_track(root, source, persist=False)

            self.assertFalse(sound_model.processed_dir(root).exists())

    def test_manifestless_required_sound_bundle_is_not_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["settings"] = {"required_features": {"music": True}}
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            sounds = bundle / "gallery" / "sounds"
            (sounds / "lettersmith-sound.json").unlink()
            (sounds / "music.mp3").write_bytes(b"retired-format")

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

    def test_canonical_required_music_rejects_a_silent_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["settings"] = {"required_features": ["music"]}
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

    def test_manifestless_optional_music_bundle_is_not_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            sounds = bundle / "gallery" / "sounds"
            (sounds / "lettersmith-sound.json").unlink()
            (sounds / "flip1.mp3").write_bytes(b"page turn")
            (sounds / "music.mp3").write_bytes(b"manifestless music")
            manifest = sounds / "lettersmith-sound.json"

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())
            self.assertFalse(manifest.exists())

    def test_saved_bundle_user_workspace_layout_is_not_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            gallery = bundle / "gallery"
            user = gallery / "user"
            (user / "card").mkdir(parents=True)
            (gallery / "pages").replace(user / "pages")
            (gallery / "message").replace(user / "message")
            (gallery / "controls").replace(user / "card" / "controls")

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

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
            SettingsStore(root).update_fields({"forge_preview_mode": "window"})
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["settings"]["forge_preview_mode"] = "portrait"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            restored = self._restore(root, bundle)

            self.assertEqual(SettingsStore(root).get("forge_preview_mode"), "window")
            self.assertEqual(restored.play_dir, bundle.resolve())
            self.assertEqual(
                SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY),
                str(bundle.resolve()),
            )

    def test_verified_publication_state_round_trips_without_cross_letter_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            expected = {
                "published_page_url": "https://letters.example.com/letters/saved/",
                "published_public_path": "saved",
                "published_at": "2026-08-13T12:00:00+00:00",
                "published_expires_at": "",
                "publication_provider": "github_pages",
                "publication_verified": True,
                "published_source_fingerprint": "saved-fingerprint",
                "published_github_owner": "ada",
                "published_github_repository": "LetterSmith-Published",
            }
            identity = json.loads(
                (bundle / "lettersmith-metadata.json").read_text(encoding="utf-8")
            )
            SettingsStore(root).update_fields(
                {
                    **expected,
                    "project_id": identity["project_id"],
                    "recipient_id": identity["recipient_id"],
                    "recipient_name": identity["recipient_name"],
                    "recipient_display_name": identity["recipient_name"],
                    "recipient_title": identity["recipient_title"],
                }
            )
            update_saved_metadata(
                bundle,
                root,
                ReadinessResult((), 100, "Ready"),
            )
            SettingsStore(root).update_fields(
                {
                    **expected,
                    "published_page_url": "https://letters.example.com/letters/other/",
                    "published_public_path": "other",
                    "published_source_fingerprint": "other-fingerprint",
                }
            )

            restored = self._restore(root, bundle)
            current = SettingsStore(root).snapshot()
            saved = json.loads(
                (bundle / "lettersmith-metadata.json").read_text(encoding="utf-8")
            )

            for key, value in expected.items():
                self.assertEqual(saved[key], value)
                self.assertEqual(current[key], value)
            self.assertEqual(publication_status(current), "published")
            self.assertEqual(restored.published_public_path, "saved")
            self.assertTrue(restored.publication_verified)
            self.assertEqual(restored.published_github_owner, "ada")
            self.assertEqual(
                restored.as_payload()["published_github_repository"],
                "LetterSmith-Published",
            )

    def test_published_snapshot_survives_working_edits_and_restores_owned_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            publication = {
                "published_page_url": "https://ada.github.io/letter/",
                "published_public_path": "letter",
                "published_at": "2026-09-16T12:00:00+00:00",
                "published_expires_at": "",
                "publication_provider": "github_pages",
                "publication_verified": True,
                "published_source_fingerprint": "version-one",
                "published_github_owner": "ada",
                "published_github_repository": "letter",
            }
            metadata_path = bundle / "lettersmith-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(publication)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            (bundle / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"source_fingerprint": "version-one"}),
                encoding="utf-8",
            )
            snapshot = save_published_snapshot(bundle, root, publication)
            self.assertEqual(
                (snapshot / "gallery/pages/cover.png").read_bytes(),
                b"cover.png",
            )

            (bundle / "gallery/pages/cover.png").write_bytes(b"version two")
            (bundle / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"source_fingerprint": "version-two"}),
                encoding="utf-8",
            )
            entries = SavedLetterCatalog(root).list_entries()
            self.assertIn(bundle.resolve(), [item.path for item in entries])
            self.assertTrue(next(item for item in entries if item.path == snapshot).published)
            self.assertFalse(
                next(item for item in entries if item.path == bundle.resolve()).published
            )
            working = root / "gallery/user/pages"
            working.mkdir(parents=True)
            (working / "cover.png").write_bytes(b"version two")
            (working / "cover.png").unlink()

            entry = next(item for item in entries if item.path == snapshot)
            frozen_metadata = (snapshot / "lettersmith-metadata.json").read_bytes()
            SavedLetterRestorer(root).restore(entry)
            self.assertEqual((working / "cover.png").read_bytes(), b"cover.png")
            self.assertEqual(
                (snapshot / "gallery/pages/cover.png").read_bytes(),
                b"cover.png",
            )
            self.assertEqual(
                (snapshot / "lettersmith-metadata.json").read_bytes(),
                frozen_metadata,
            )
            self.assertEqual(
                SettingsStore(root).get("published_source_fingerprint"),
                "version-one",
            )
            self.assertEqual(SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY), "")

    def test_existing_published_play_is_preserved_before_working_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"source_fingerprint": "published-version"}),
                encoding="utf-8",
            )
            SettingsStore(root).update_fields(
                {
                    "published_page_url": "https://ada.github.io/letter/",
                    "published_public_path": "letter",
                    "published_at": "2026-09-16T12:00:00+00:00",
                    "published_expires_at": "",
                    "publication_provider": "github_pages",
                    "publication_verified": True,
                    "published_source_fingerprint": "published-version",
                    "published_github_owner": "ada",
                    "published_github_repository": "letter",
                }
            )
            generate._preserve_published_play_bundle(root, bundle)
            snapshots = tuple((root / "output/Recovery/Published").iterdir())
            self.assertEqual(len(snapshots), 1)
            snapshot_cover = snapshots[0] / "gallery/pages/cover.png"
            self.assertEqual(snapshot_cover.read_bytes(), b"cover.png")

            (bundle / "gallery/pages/cover.png").write_bytes(b"working version")
            (bundle / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"source_fingerprint": "working-version"}),
                encoding="utf-8",
            )
            generate._preserve_published_play_bundle(root, bundle)
            self.assertEqual(snapshot_cover.read_bytes(), b"cover.png")

    def test_local_letter_restore_clears_another_letters_publication_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            SettingsStore(root).update_fields(
                {
                    "published_page_url": "https://letters.example.com/letters/other/",
                    "published_public_path": "other",
                    "published_at": "2026-08-13T12:00:00+00:00",
                    "published_expires_at": "2099-09-12T12:00:00+00:00",
                    "publication_provider": "cloudflare_r2",
                    "publication_verified": True,
                    "published_source_fingerprint": "other-fingerprint",
                }
            )

            self._restore(root, bundle)
            current = SettingsStore(root).snapshot()

            self.assertEqual(publication_status(current), "local")
            self.assertEqual(current["published_public_path"], "")
            self.assertFalse(current["publication_verified"])

    def test_restore_uses_listed_bundle_when_project_snapshot_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            metadata = json.loads(
                (bundle / "lettersmith-metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            project = (
                root
                / "output"
                / "projects"
                / metadata["recipient_name"]
                / metadata["recipient_title"]
            )
            project.mkdir(parents=True)
            (project / "lettersmith-metadata.json").write_text(
                json.dumps(metadata),
                encoding="utf-8",
            )

            catalog = SavedLetterCatalog(root)
            entry = catalog.list_entries()[0]
            restorer = SavedLetterRestorer(root)
            with mock.patch.object(
                restorer.resolver,
                "resolve_autosave_directory",
                side_effect=AssertionError(
                    "Load Letters must not consult autosave storage."
                ),
            ):
                restored = restorer.restore(entry)

            self.assertEqual(restored.play_dir, bundle.resolve())
            self.assertEqual(
                {managed_root.name for managed_root in catalog.managed_roots},
                {"Play", "Recovery"},
            )

    def test_catalog_omits_bundle_that_restorer_cannot_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / "gallery" / "pages" / "cover.png").unlink()

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

    def test_catalog_omits_bundle_with_corrupt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / "lettersmith-metadata.json").write_text(
                "{not-json",
                encoding="utf-8",
            )

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

    def test_catalog_accepts_letter_with_empty_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / "gallery" / "message" / "message.html").write_text(
                "",
                encoding="utf-8",
            )

            entries = SavedLetterCatalog(root).list_entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path, bundle.resolve())

            restored = SavedLetterRestorer(root).restore(entries[0])

            self.assertEqual(restored.play_dir, bundle.resolve())
            self.assertEqual(
                (root / "gallery" / "user" / "message" / "message.html").read_text(
                    encoding="utf-8"
                ),
                "",
            )

    def test_prompt_writer_workspace_round_trips_without_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            metadata_path = bundle / "lettersmith-metadata.json"
            original_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            record = RecipientRegistry(root).find_by_id(original_metadata["recipient_id"])
            self.assertIsNotNone(record)
            SettingsStore(root).update_fields(
                {
                    "project_id": original_metadata["project_id"],
                    "recipient_id": record.recipient_id,
                    "recipient_display_name": record.display_name,
                    "recipient_normalized_key": record.normalized_key,
                    "recipient_name": record.display_name,
                    "recipient_title": original_metadata["recipient_title"],
                }
            )
            prompt_state = {
                "version": 6,
                "type": "Historical Illustration",
                "subject": "GO and Genesis Prime",
                "color": "Historical Palette",
                "global": "Shared direction",
                "cover": "Cover direction",
                "letter": "Letter direction",
                "wall": "Wall direction",
                "back": "Back direction",
                "checks": {"black": True, "wide_scene": True},
                "resolved_instructions": {
                    "role": "Exact role",
                    "subject_lead_in": ["Exact", "subject lead-in"],
                    "effort": "Exact effort",
                    "format": "Exact format",
                },
                "generated_prompts": {
                    "cover": "Cover exact\r\nsecond line",
                    "letter": "Letter exact",
                    "wall": "Wall exact",
                    "back": "Back exact",
                },
                "generated_input_signature": "historical-signature",
            }
            (root / PROMPT_WRITER_STATE_FILE).write_text(
                json.dumps(prompt_state, ensure_ascii=False),
                encoding="utf-8",
            )

            update_saved_metadata(
                bundle,
                root,
                ReadinessResult((), 100, "Ready"),
            )

            saved_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_metadata["schema_version"], "1.0")
            self.assertEqual(saved_metadata["document_type"], "saved_letter")
            self.assertNotIn("source_version", saved_metadata)
            self.assertNotIn("release_schema_version", saved_metadata)
            self.assertEqual(
                saved_metadata["editable_assets"]["prompt_writer_state"],
                PROMPT_WRITER_STATE_FILE,
            )
            self.assertEqual(
                saved_metadata["prompt_writer"],
                {
                    "snapshot_schema_version": 1,
                    "state_file": PROMPT_WRITER_STATE_FILE,
                    "state": prompt_state,
                },
            )
            self.assertEqual(
                json.loads((bundle / PROMPT_WRITER_STATE_FILE).read_text(encoding="utf-8")),
                prompt_state,
            )

            (root / PROMPT_WRITER_STATE_FILE).write_text(
                json.dumps({"subject": "Different active project"}),
                encoding="utf-8",
            )
            self._restore(root, bundle)
            self.assertEqual(
                json.loads((root / PROMPT_WRITER_STATE_FILE).read_text(encoding="utf-8")),
                prompt_state,
            )
            (bundle / PROMPT_WRITER_STATE_FILE).unlink()
            atomic_write_json(
                root / PROMPT_WRITER_STATE_FILE,
                {"subject": "Another active project"},
            )
            self._restore(root, bundle)
            self.assertEqual(
                json.loads((root / PROMPT_WRITER_STATE_FILE).read_text(encoding="utf-8")),
                prompt_state,
            )

    def test_prompt_writer_state_accepts_backward_compatible_versions(self) -> None:
        for payload in (
            {},
            {"version": 1},
            {"version": str(PROMPT_WRITER_STATE_VERSION)},
            {"version": PROMPT_WRITER_STATE_VERSION},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    validate_prompt_writer_state_payload(payload),
                    payload,
                )

    def test_invalid_prompt_writer_state_is_rejected_before_restore(self) -> None:
        invalid_payloads = (
            {"version": True},
            {"version": "invalid"},
            {"version": PROMPT_WRITER_STATE_VERSION + 1},
        )
        for payload in invalid_payloads:
            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                bundle = self._bundle(root)
                entry = SavedLetterCatalog(root).list_entries()[0]
                active_state = {"version": 1, "subject": "Current project"}
                atomic_write_json(root / PROMPT_WRITER_STATE_FILE, active_state)

                metadata_path = bundle / "lettersmith-metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["prompt_writer"] = {
                    "snapshot_schema_version": 1,
                    "state_file": PROMPT_WRITER_STATE_FILE,
                    "state": payload,
                }
                atomic_write_json(metadata_path, metadata)

                self.assertEqual(
                    SavedLetterCatalog(root).list_entries(force_refresh=True),
                    (),
                )
                with self.assertRaisesRegex(
                    SavedLetterRestoreError,
                    "Prompt Writer state is invalid",
                ):
                    SavedLetterRestorer(root).restore(entry)

                self.assertEqual(
                    json.loads(
                        (root / PROMPT_WRITER_STATE_FILE).read_text(encoding="utf-8")
                    ),
                    active_state,
                )
                self.assertFalse((root / "gallery" / "user" / "pages").exists())

    def test_letter_without_prompt_writer_state_is_not_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            (bundle / PROMPT_WRITER_STATE_FILE).unlink()
            (root / PROMPT_WRITER_STATE_FILE).write_text(
                json.dumps(
                    {
                        "subject": "Current project",
                        "generated_prompts": {"cover": "must not leak"},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())
            self.assertFalse((bundle / PROMPT_WRITER_STATE_FILE).exists())
            self.assertEqual(
                json.loads(
                    (root / PROMPT_WRITER_STATE_FILE).read_text(encoding="utf-8")
                )["subject"],
                "Current project",
            )

    def test_recovery_letter_is_listed_and_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            recovery = (
                root
                / "output"
                / "Recovery"
                / bundle.parent.name
                / bundle.name
            )
            recovery.parent.mkdir(parents=True)
            bundle.replace(recovery)

            entries = SavedLetterCatalog(root).list_entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path, recovery.resolve())
            self.assertTrue(entries[0].recovery)
            restored = SavedLetterRestorer(root).restore(entries[0])
            self.assertEqual(restored.play_dir, recovery.resolve())
            self.assertTrue(recovery.is_dir())

    def test_failure_restores_active_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_pages = root / "gallery" / "user" / "pages"
            active_message = root / "gallery" / "user" / "message"
            active_pages.mkdir(parents=True)
            active_message.mkdir(parents=True)
            active_assets = root / MESSAGE_ASSETS_DIR
            active_assets.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (active_pages / name).write_bytes(b"current")
            (active_message / "message.html").write_text("<p>current</p>", encoding="utf-8")
            (active_assets / "current.png").write_bytes(b"current asset")
            settings = SettingsStore(root)
            existing = root / "existing.mp3"
            existing.write_bytes(b"current")
            record = import_runtime_track(root, existing, display_title="Current")
            active_state = ProjectSoundState(mode="single", single_track_id=record.track_id, selected_track_id=record.track_id)
            save_project_state(root, active_state)
            sync_current_compatibility(root, active_state, load_library(root))
            settings.update_fields({"recipient_name": "Amanda", "recipient_title": "Current"})
            old_settings = settings.snapshot()
            prompt_state_path = root / PROMPT_WRITER_STATE_FILE
            prompt_state_path.write_bytes(b'{"subject": "Current prompt"}\n')
            old_prompt_state = prompt_state_path.read_bytes()
            archive_files_before = {
                path.relative_to(archive_root(root))
                for path in archive_root(root).rglob("*")
                if path.is_file()
            }
            bundle = self._bundle(root)
            bundle_assets = bundle / MESSAGE_ASSETS_DIR
            bundle_assets.mkdir(parents=True)
            (bundle_assets / "new.png").write_bytes(b"new asset")
            sounds = bundle / "gallery" / "sounds"
            (sounds / "music.mp3").write_bytes(b"new")
            (sounds / "lettersmith-sound.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "single",
                        "tracks": [{"filename": "music.mp3"}],
                    }
                ),
                encoding="utf-8",
            )

            restorer = SavedLetterRestorer(root)
            restorer._verify_committed_state = lambda: (_ for _ in ()).throw(RuntimeError("injected"))
            entry = SavedLetterCatalog(root).list_entries()[0]
            with self.assertRaises(SavedLetterRestoreError):
                restorer.restore(entry)

            self.assertEqual((active_pages / "letter.png").read_bytes(), b"current")
            self.assertEqual((active_message / "message.html").read_text(encoding="utf-8"), "<p>current</p>")
            self.assertEqual(
                (active_assets / "current.png").read_bytes(),
                b"current asset",
            )
            self.assertFalse((active_assets / "new.png").exists())
            self.assertEqual(settings.snapshot().get("recipient_name"), old_settings.get("recipient_name"))
            self.assertEqual(current_music_path(root).read_bytes(), b"current")
            self.assertEqual(load_project_state(root).single_track_id, record.track_id)
            self.assertTrue(project_sound_path(root).exists())
            self.assertEqual(prompt_state_path.read_bytes(), old_prompt_state)
            self.assertEqual(set(load_library(root)), {record.track_id})
            self.assertEqual(
                {
                    path.relative_to(archive_root(root))
                    for path in archive_root(root).rglob("*")
                    if path.is_file()
                },
                archive_files_before,
            )


if __name__ == "__main__":
    unittest.main()
