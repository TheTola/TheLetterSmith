from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
import generate
from project_save import ProjectSaveService
from project_state import ProjectStateController
from image_animation import (
    IMAGE_MANIFEST_NAME,
    UnsupportedImageManifestSchemaError,
    clear_slot_asset,
    load_image_manifest,
)
from recipient_registry import RecipientRegistry, RecipientRegistryError
from saved_letters import (
    SavedLetterRestoreError,
    record_saved_letter_activity,
    update_saved_metadata,
)
from settings_store import (
    SETTINGS_SCHEMA_KEY,
    SettingsStore,
    UnsupportedSettingsSchemaError,
)
from sound_model import (
    ProjectSoundState,
    import_runtime_track,
    load_project_state,
    project_sound_path,
    save_project_state,
)
from transactional_io import (
    atomic_copy_file,
    atomic_write_json,
    enforce_internal_tree_visibility,
    file_change_token,
    set_path_hidden,
)


class PersistenceHardeningTests(unittest.TestCase):
    def test_internal_visibility_hides_state_but_not_user_content(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows hidden attributes are platform-specific.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            internal = (
                root / "Active Project/prompt_writer_state.json",
                root / "Active Project/settings.json",
                root / "Music Archive/library.json",
                root / "Music Archive/analysis/song.mp3.analysis.json",
                root / "Saved Letters/A Letter/lettersmith-metadata.json",
                root / "Saved Letters/A Letter/lettersmith-build.json",
                root / "Saved Letters/A Letter/gallery/pages/lettersmith-images.json",
                root / "Saved Letters/A Letter/gallery/sounds/lettersmith-sound.json",
                root / "autosaves/A Letter/.lettersmith-snapshot-manifest.json",
            )
            content = (
                root / "Active Project/gallery/user/pages/cover.png",
                root / "Active Project/gallery/user/message/message.html",
                root / "Active Project/gallery/user/message/message.png",
                root / "Active Project/gallery/user/sounds/music.mp3",
                root / "autosaves/A Letter/message/revisions/revision.html",
            )
            controls = (
                root / "Active Project/gallery/user/card/controls",
                root / "Saved Letters/A Letter/gallery/controls",
            )
            for path in (*internal, *content):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"data")
            for path in controls:
                path.mkdir(parents=True, exist_ok=True)

            enforce_internal_tree_visibility(root)
            hidden_flag = stat.FILE_ATTRIBUTE_HIDDEN
            for path in (*internal, *controls):
                self.assertTrue(path.stat().st_file_attributes & hidden_flag)
            for path in content:
                self.assertFalse(path.stat().st_file_attributes & hidden_flag)

            prompt_state = internal[0]
            atomic_write_json(prompt_state, {"version": 1})
            atomic_write_json(prompt_state, {"version": 2})
            self.assertTrue(prompt_state.stat().st_file_attributes & hidden_flag)
            self.assertEqual(
                json.loads(prompt_state.read_text(encoding="utf-8"))["version"],
                2,
            )

    def test_atomic_copy_preserves_and_replaces_hidden_files(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows hidden attributes are platform-specific.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            destination = root / "destination.json"
            source.write_text("first", encoding="utf-8")
            set_path_hidden(source)

            atomic_copy_file(source, destination)
            self.assertTrue(
                destination.stat().st_file_attributes
                & stat.FILE_ATTRIBUTE_HIDDEN
            )

            set_path_hidden(source, False)
            source.write_text("second", encoding="utf-8")
            set_path_hidden(source)
            atomic_copy_file(source, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "second")
            self.assertTrue(
                destination.stat().st_file_attributes
                & stat.FILE_ATTRIBUTE_HIDDEN
            )

    @staticmethod
    def _ready_save_service(
        root: Path,
    ) -> tuple[ProjectSaveService, Path]:
        state = ProjectStateController(root)
        state.initialize()
        state.establish_project(
            "Amanda Miller",
            custom_capitalization=True,
        )
        SettingsStore(root).update_fields(
            {"recipient_title": "Persistence Test"}
        )
        pages = root / config.USER_PAGES_DIR
        pages.mkdir(parents=True)
        for name in config.REQUIRED_SLIDES:
            (pages / name).write_bytes(
                f"{name}-original".encode("utf-8")
            )
        return ProjectSaveService(root, state), pages

    def test_incremental_snapshot_retries_a_concurrent_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, pages = self._ready_save_service(root)
            saved = service.save_workspace_snapshot(reason="first")
            source = pages / config.REQUIRED_SLIDES[0]
            existing = saved / "pages" / source.name
            replacement = source.read_bytes()[::-1]
            original_reuse = ProjectSaveService._link_or_copy_file
            changed = False

            def link_then_change(
                stored: str | Path,
                destination: str | Path,
            ) -> bool:
                nonlocal changed
                linked = original_reuse(stored, destination)
                if not changed and Path(stored) == existing:
                    changed = True
                    source.write_bytes(replacement)
                return linked

            with mock.patch.object(
                ProjectSaveService,
                "_link_or_copy_file",
                side_effect=link_then_change,
            ):
                service.save_workspace_snapshot(reason="concurrent-change")

            self.assertTrue(changed)
            self.assertEqual(existing.read_bytes(), replacement)

            with mock.patch(
                "project_save.atomic_copy_file",
                side_effect=AssertionError("stable files should be reused"),
            ) as copied:
                service.save_workspace_snapshot(reason="stable-reuse")
            self.assertEqual(copied.call_count, 0)

    def test_incremental_snapshot_rejects_changed_stored_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, pages = self._ready_save_service(root)
            saved = service.save_workspace_snapshot(reason="first")
            source = pages / config.REQUIRED_SLIDES[0]
            stored = saved / "pages" / source.name
            original_stat = stored.stat()
            original_token = file_change_token(
                stored,
                stat_result=original_stat,
            )
            stored.write_bytes(stored.read_bytes()[::-1])
            os.utime(
                stored,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            self.assertNotEqual(file_change_token(stored), original_token)

            service.save_workspace_snapshot(reason="repair-stored")

            self.assertEqual(stored.read_bytes(), source.read_bytes())

    def test_read_only_preserved_file_is_not_hardlinked_or_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _pages = self._ready_save_service(root)
            saved = service.save_workspace_snapshot(reason="first")
            preserved = saved / "custom-preserved.bin"
            preserved.write_bytes(b"preserve me")
            preserved.chmod(stat.S_IREAD)
            self.addCleanup(
                lambda: preserved.exists() and preserved.chmod(stat.S_IWRITE)
            )

            with mock.patch(
                "project_save.PathTransaction.commit",
                side_effect=RuntimeError("stop before commit"),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop before commit"):
                    service.save_workspace_snapshot(reason="injected-failure")

            self.assertEqual(preserved.read_bytes(), b"preserve me")
            self.assertFalse(bool(preserved.stat().st_mode & stat.S_IWRITE))

    def test_canonical_settings_loader_backs_up_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            malformed = b"{not-json"
            settings.write_bytes(malformed)

            restored = config._load_settings(root)

            self.assertEqual(restored[SETTINGS_SCHEMA_KEY], 1)
            backups = list(root.glob("settings.invalid.*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), malformed)
            self.assertIsInstance(
                json.loads(settings.read_text(encoding="utf-8")),
                dict,
            )

    def test_canonical_settings_loader_preserves_future_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        SETTINGS_SCHEMA_KEY: 99,
                        "starting_volume": 500,
                    }
                ),
                encoding="utf-8",
            )
            original = settings.read_bytes()

            with self.assertRaises(UnsupportedSettingsSchemaError):
                config._load_settings(root)

            self.assertEqual(settings.read_bytes(), original)
            self.assertEqual(list(root.glob("settings.invalid.*.json")), [])

    def test_recipient_schema_zero_migrates_and_future_schema_is_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = RecipientRegistry(root)
            record = registry.get_or_create("Amanda Miller")
            payload = json.loads(registry.path.read_text(encoding="utf-8"))
            payload["schema_version"] = 0
            registry.path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(registry.list()[0].recipient_id, record.recipient_id)
            migrated = json.loads(registry.path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 1)

            migrated["schema_version"] = 99
            registry.path.write_text(json.dumps(migrated), encoding="utf-8")
            original = registry.path.read_bytes()
            with self.assertRaises(RecipientRegistryError):
                registry.list()
            self.assertEqual(registry.path.read_bytes(), original)
            backups = list(registry.path.parent.glob("recipients.invalid.*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)

    def test_malformed_sound_state_is_backed_up_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = project_sound_path(root)
            state_path.parent.mkdir(parents=True)
            malformed = b"[not-an-object]"
            state_path.write_bytes(malformed)

            state = load_project_state(root)

            self.assertEqual(state.ordered_track_ids(), [])
            backups = list(
                state_path.parent.glob("project_sound.invalid.*.json")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), malformed)

    def test_sound_editor_state_participates_in_saved_bundle_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = root / "first.mp3"
            second_source = root / "second.mp3"
            first_source.write_bytes(b"first")
            second_source.write_bytes(b"second")
            first = import_runtime_track(root, first_source)
            second = import_runtime_track(root, second_source)
            save_project_state(
                root,
                ProjectSoundState(
                    mode="playlist",
                    playlist=[first.track_id, second.track_id],
                    playlist_expanded=True,
                    selected_track_id=first.track_id,
                ),
            )
            before = generate.build_source_fingerprint(root)

            save_project_state(
                root,
                ProjectSoundState(
                    mode="playlist",
                    playlist=[first.track_id, second.track_id],
                    playlist_expanded=False,
                    selected_track_id=second.track_id,
                ),
            )

            self.assertNotEqual(generate.build_source_fingerprint(root), before)

    def test_saved_metadata_mutations_refuse_to_overwrite_malformed_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "output" / "Play" / "Recipient" / "Letter"
            bundle.mkdir(parents=True)
            metadata = bundle / config.PLAY_METADATA_FILE
            malformed = b"{not-json"
            metadata.write_bytes(malformed)

            with self.assertRaises(SavedLetterRestoreError):
                update_saved_metadata(bundle, root, object())
            self.assertEqual(metadata.read_bytes(), malformed)

            with self.assertRaises(SavedLetterRestoreError):
                record_saved_letter_activity(bundle)
            self.assertEqual(metadata.read_bytes(), malformed)

    def test_image_manifest_backs_up_malformed_and_blocks_future_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory)
            manifest = pages / IMAGE_MANIFEST_NAME
            malformed = b"{not-json"
            manifest.write_bytes(malformed)

            self.assertEqual(load_image_manifest(pages)["slots"], {})
            backups = list(
                pages.glob("lettersmith-images.invalid.*.json")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), malformed)

            set_path_hidden(manifest, False)
            manifest.write_text(
                json.dumps({"schema_version": 99, "slots": {}}),
                encoding="utf-8",
            )
            original = manifest.read_bytes()
            with self.assertRaises(UnsupportedImageManifestSchemaError):
                load_image_manifest(pages)
            self.assertEqual(manifest.read_bytes(), original)

            cover = pages / "cover.png"
            cover.write_bytes(b"cover")
            with self.assertRaises(UnsupportedImageManifestSchemaError):
                clear_slot_asset(pages, "cover")
            self.assertEqual(manifest.read_bytes(), original)
            self.assertEqual(cover.read_bytes(), b"cover")


if __name__ == "__main__":
    unittest.main()
