from __future__ import annotations

import json
import os
import stat
import struct
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PIL import Image

import config
import font_export
import generate
from config import CONTROL_FILES, REQUIRED_SLIDES, USER_CONTROLS_DIR, USER_MESSAGE_DIR, USER_PAGES_DIR
from project_paths import ProjectPathResolver
from project_save import ProjectSaveService
from project_state import ApplicationState, ProjectDirtyController, ProjectStateController
from readiness import evaluate_readiness
from saved_letters import SavedLetterCatalog, SavedLetterRestorer, update_saved_metadata
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from startup_check import run_startup_self_check
from transactional_io import (
    atomic_copy_file,
    atomic_write_json,
    cleanup_abandoned_temp_files,
)


class SmallImprovementSmokeTests(unittest.TestCase):
    @staticmethod
    def _write_play_bundle(path: Path, project_id: str) -> Path:
        path.mkdir(parents=True)
        for name in ("index.html", "styles.css", "script.js"):
            (path / name).write_text("", encoding="utf-8")
        (path / config.PLAY_METADATA_FILE).write_text(
            json.dumps({"project_id": project_id}),
            encoding="utf-8",
        )
        return path.resolve()

    @staticmethod
    def _synthetic_name_table(family: str, subfamily: str) -> bytes:
        full_name = family if subfamily == "Regular" else f"{family} {subfamily}"
        names = (
            (1, family),
            (2, subfamily),
            (4, full_name),
            (16, family),
            (17, subfamily),
        )
        string_data = bytearray()
        records = bytearray()
        string_offset = 6 + len(names) * 12
        for name_id, value in names:
            encoded = value.encode("utf-16-be")
            records.extend(
                struct.pack(
                    ">HHHHHH",
                    3,
                    1,
                    0x0409,
                    name_id,
                    len(encoded),
                    len(string_data),
                )
            )
            string_data.extend(encoded)
        return (
            struct.pack(">HHH", 0, len(names), string_offset)
            + records
            + string_data
        )

    @classmethod
    def _write_synthetic_ttc(cls, path: Path, family: str) -> None:
        faces: list[dict[bytes, bytes]] = []
        for subfamily, weight, italic in (
            ("Regular", 400, False),
            ("Bold Italic", 700, True),
        ):
            head = bytearray(54)
            struct.pack_into(">I", head, 0, 0x00010000)
            struct.pack_into(">I", head, 12, 0x5F0F3CF5)
            os2 = bytearray(64)
            struct.pack_into(">H", os2, 0, 4)
            struct.pack_into(">H", os2, 4, weight)
            struct.pack_into(">H", os2, 62, 1 if italic else 0)
            faces.append(
                {
                    b"OS/2": bytes(os2),
                    b"head": bytes(head),
                    b"name": cls._synthetic_name_table(family, subfamily),
                }
            )

        header_size = 12 + len(faces) * 4
        face_offsets: list[int] = []
        directory_size = sum(12 + len(tables) * 16 for tables in faces)
        directory_cursor = header_size
        table_cursor = header_size + directory_size
        directories: list[bytes] = []
        table_data = bytearray()
        for tables in faces:
            face_offsets.append(directory_cursor)
            table_count = len(tables)
            entry_selector = table_count.bit_length() - 1
            search_range = (1 << entry_selector) * 16
            range_shift = table_count * 16 - search_range
            directory = bytearray(
                struct.pack(
                    ">4sHHHH",
                    b"\x00\x01\x00\x00",
                    table_count,
                    search_range,
                    entry_selector,
                    range_shift,
                )
            )
            for tag, value in sorted(tables.items()):
                directory.extend(
                    struct.pack(
                        ">4sIII",
                        tag,
                        0,
                        table_cursor + len(table_data),
                        len(value),
                    )
                )
                table_data.extend(value)
                table_data.extend(b"\0" * ((-len(value)) % 4))
            directories.append(bytes(directory))
            directory_cursor += len(directory)

        path.write_bytes(
            struct.pack(">4sII", b"ttcf", 0x00010000, len(faces))
            + struct.pack(f">{len(face_offsets)}I", *face_offsets)
            + b"".join(directories)
            + table_data
        )

    def test_settings_notifications_and_picker_folders_are_shared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "assets" / "cover.png"
            selected.parent.mkdir()
            selected.write_bytes(b"image")
            first = SettingsStore(root)
            second = SettingsStore(root)
            observed: list[tuple[str, ...]] = []
            first.changed.connect(lambda _settings, keys: observed.append(keys))

            second.update_fields({"recipient_title": "Shared Signal"})
            second.remember_folder("image", selected)

            self.assertIn(("recipient_title",), observed)
            self.assertEqual(first.last_folder("image"), str(selected.parent.resolve()))

    def test_dirty_controller_has_one_saved_boundary(self) -> None:
        dirty = ProjectDirtyController()
        states: list[bool] = []
        dirty.add_listener(states.append)
        dirty.mark_changed("images")
        dirty.mark_changed("message")
        dirty.mark_saved()

        self.assertEqual(states, [False, True, False])
        self.assertFalse(dirty.is_dirty)

    def test_dirty_controller_does_not_clear_a_newer_revision(self) -> None:
        dirty = ProjectDirtyController()
        dirty.mark_changed("images")
        snapshot_revision = dirty.revision
        dirty.mark_changed("message")

        self.assertFalse(dirty.mark_saved(expected_revision=snapshot_revision))
        self.assertTrue(dirty.is_dirty)
        self.assertTrue(dirty.mark_saved(expected_revision=dirty.revision))

    def test_stale_temporary_cleanup_is_scoped_and_age_gated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / ".settings.json.tmp.abandoned"
            recent = root / "recent.tmp"
            unrelated = root / "keep.txt"
            stale.write_bytes(b"stale")
            recent.write_bytes(b"recent")
            unrelated.write_bytes(b"keep")
            old = time.time() - 48 * 60 * 60
            os.utime(stale, (old, old))

            removed = cleanup_abandoned_temp_files((root,), recursive=False)

            self.assertEqual(removed, (stale.resolve(),))
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_startup_self_check_reports_no_issue_for_complete_writable_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Template.py").write_text("", encoding="utf-8")
            (root / "styles.css").write_text("", encoding="utf-8")
            (root / "gallery" / "app" / "icons").mkdir(parents=True)

            issues = run_startup_self_check(root)

            self.assertEqual(issues, ())
            self.assertTrue((root / "gallery" / "user").is_dir())
            self.assertTrue((root / "output").is_dir())

    def test_new_assets_message_save_load_generate_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            self.assertEqual(state.initialize(), ApplicationState.RECIPIENT_REQUIRED)
            state.establish_project("Discarded Recipient", custom_capitalization=True)
            state.transition(ApplicationState.PROJECT_CLEARING)
            state.begin_new_project()
            state.establish_project("Amanda Miller", custom_capitalization=True)
            SettingsStore(root).update_fields({"recipient_title": "Smoke Letter"})

            pages = root / USER_PAGES_DIR
            controls = root / USER_CONTROLS_DIR
            message = root / USER_MESSAGE_DIR / "message.html"
            pages.mkdir(parents=True)
            controls.mkdir(parents=True)
            message.parent.mkdir(parents=True)
            for index, name in enumerate(REQUIRED_SLIDES):
                Image.new("RGB", (8, 8), (40 + index, 80, 120)).save(pages / name)
            for index, name in enumerate(CONTROL_FILES):
                Image.new("RGBA", (8, 8), (80, 120 + index, 160, 255)).save(
                    controls / name
                )
            banner = root / config.APP_BANNER_PATH
            banner.parent.mkdir(parents=True)
            Image.new("RGBA", (8, 8), (120, 160, 200, 255)).save(banner)
            message.write_text("<p>Round-trip smoke message.</p>", encoding="utf-8")

            service = ProjectSaveService(
                root,
                state,
                resolver=ProjectPathResolver(root),
            )
            saved = service.save_workspace_snapshot(reason="smoke-test")
            self.assertTrue((saved / "message" / "message.html").is_file())

            play_dir, rebuilt = generate.ensure_play_bundle(
                root,
                seed_sfx=False,
                force=True,
            )
            self.assertTrue(rebuilt)
            self.assertTrue(
                (play_dir / config.PLAY_METADATA_FILE).is_file()
            )
            self.assertTrue((play_dir / "prompt_writer_state.json").is_file())
            prompt_state = {
                "version": 6,
                "subject": "Cached Prompt Writer state",
                "generated_prompts": {
                    "cover": "Exact cover prompt",
                    "letter": "Exact letter prompt",
                    "wall": "Exact wall prompt",
                    "back": "Exact back prompt",
                },
            }
            atomic_write_json(
                root / "prompt_writer_state.json",
                prompt_state,
            )
            cached_play_dir, cached_rebuilt = generate.ensure_play_bundle(
                root,
                seed_sfx=False,
            )
            self.assertEqual(cached_play_dir, play_dir)
            self.assertFalse(cached_rebuilt)
            cached_metadata = json.loads(
                (play_dir / config.PLAY_METADATA_FILE).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                cached_metadata["prompt_writer"]["state"],
                prompt_state,
            )
            entry = SavedLetterCatalog(root).list_entries()[0]
            self.assertIsNotNone(entry.cover_path)
            self.assertEqual(entry.cover_path.name, "cover.thumbnail.png")
            with Image.open(entry.cover_path) as thumbnail:
                self.assertLessEqual(thumbnail.width, 336)
                self.assertLessEqual(thumbnail.height, 184)
            metadata = json.loads(
                (play_dir / config.PLAY_METADATA_FILE).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                metadata["cover_thumbnail_source_signature"]["version"],
                1,
            )
            entry.cover_path.write_bytes(b"broken thumbnail")
            update_saved_metadata(play_dir, root, evaluate_readiness(root))
            with Image.open(entry.cover_path) as repaired_thumbnail:
                repaired_thumbnail.verify()
            thumbnail_stat = entry.cover_path.stat()
            with Image.open(entry.cover_path) as repaired_thumbnail:
                thumbnail_size = repaired_thumbnail.size
            Image.new("RGB", thumbnail_size, (255, 0, 0)).save(
                entry.cover_path,
                format="PNG",
            )
            os.utime(
                entry.cover_path,
                ns=(thumbnail_stat.st_atime_ns, thumbnail_stat.st_mtime_ns),
            )
            update_saved_metadata(play_dir, root, evaluate_readiness(root))
            with Image.open(entry.cover_path) as repaired_thumbnail:
                self.assertNotEqual(
                    repaired_thumbnail.getpixel((0, 0)),
                    (255, 0, 0),
                )

            message.write_text("<p>Unsaved replacement.</p>", encoding="utf-8")
            restored = SavedLetterRestorer(root).restore(entry)
            self.assertEqual(restored.title, "Smoke Letter")
            self.assertEqual(
                message.read_text(encoding="utf-8"),
                "<p>Round-trip smoke message.</p>",
            )

            regenerated, was_rebuilt = generate.ensure_play_bundle(
                root,
                seed_sfx=False,
                force=True,
            )
            self.assertTrue(was_rebuilt)
            self.assertTrue((regenerated / "index.html").is_file())

    def test_active_play_path_avoids_historical_bundle_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            collision = self._write_play_bundle(
                root / "output" / "Play" / "Ada Lovelace" / "Current Letter",
                str(uuid.uuid4()),
            )
            active = self._write_play_bundle(
                root
                / "output"
                / "Play"
                / "Ada Lovelace"
                / "Current Letter (2)",
                project_id,
            )
            duplicate = self._write_play_bundle(
                root / "output" / "Play" / "Grace Hopper" / "Copied Letter",
                project_id,
            )
            SettingsStore(root).update_fields(
                {
                    "project_id": project_id,
                    "recipient_name": "Ada Lovelace",
                    "recipient_title": "Current Letter",
                    ACTIVE_PLAY_DIR_KEY: str(active),
                }
            )

            with mock.patch(
                "config._iter_play_bundles",
                side_effect=AssertionError("Play history should not be scanned"),
            ):
                resolved = generate.play_bundle_directory(root)

            self.assertEqual(resolved, active)
            self.assertTrue(collision.is_dir())
            self.assertTrue(duplicate.is_dir())

    def test_incremental_snapshot_reuses_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            state.establish_project("Amanda Miller", custom_capitalization=True)
            SettingsStore(root).update_fields({"recipient_title": "Incremental"})
            pages = root / USER_PAGES_DIR
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes((name + "-original").encode("utf-8"))
            service = ProjectSaveService(
                root,
                state,
                resolver=ProjectPathResolver(root),
            )
            saved = service.save_workspace_snapshot(reason="first")

            with mock.patch(
                "project_save.atomic_copy_file",
                wraps=atomic_copy_file,
            ) as copy_file:
                service.save_workspace_snapshot(reason="unchanged")
            self.assertEqual(copy_file.call_count, 0)

            changed = pages / REQUIRED_SLIDES[0]
            original_stat = changed.stat()
            replacement = changed.read_bytes()[::-1]
            changed.write_bytes(replacement)
            os.utime(
                changed,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            with mock.patch(
                "project_save.atomic_copy_file",
                wraps=atomic_copy_file,
            ) as copy_file:
                service.save_workspace_snapshot(reason="one-change")

            self.assertEqual(copy_file.call_count, 1)
            self.assertEqual(
                (saved / "pages" / REQUIRED_SLIDES[0]).read_bytes(),
                replacement,
            )

    def test_canonical_play_path_avoids_scan_and_becomes_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            canonical = self._write_play_bundle(
                root / "output" / "Play" / "Ada Lovelace" / "Current Letter",
                project_id,
            )
            SettingsStore(root).update_fields(
                {
                    "project_id": project_id,
                    "recipient_name": "Ada Lovelace",
                    "recipient_title": "Current Letter",
                }
            )

            with mock.patch(
                "config._iter_play_bundles",
                side_effect=AssertionError("Play history should not be scanned"),
            ):
                resolved = generate.play_bundle_directory(root)

            self.assertEqual(resolved, canonical)
            self.assertEqual(
                SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY),
                str(canonical),
            )

    def test_ensure_play_bundle_computes_one_fingerprint_per_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            build = root / "output" / "Play" / "Current"
            with (
                mock.patch.object(
                    generate,
                    "build_source_fingerprint",
                    return_value="source-one",
                ) as fingerprint,
                mock.patch.object(
                    generate,
                    "play_bundle_directory",
                    return_value=build,
                ),
                mock.patch.object(
                    generate,
                    "is_play_bundle_current",
                    return_value=False,
                ) as current,
                mock.patch.object(
                    generate,
                    "generate_play_bundle",
                    return_value=build,
                ) as rebuild,
            ):
                result, rebuilt = generate.ensure_play_bundle(root)

            self.assertEqual(result, build)
            self.assertTrue(rebuilt)
            fingerprint.assert_called_once_with(root)
            current.assert_called_once_with(
                root,
                source_fingerprint="source-one",
            )
            self.assertEqual(
                rebuild.call_args.kwargs["source_fingerprint"],
                "source-one",
            )

    def test_generate_validates_staging_once_and_checks_committed_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            final = root / "output" / "Play" / "Current"

            def build_staging(_root: Path, staging: Path, **kwargs) -> Path:
                staging.mkdir(parents=True)
                for name in ("index.html", "styles.css", "script.js"):
                    (staging / name).write_text(name, encoding="utf-8")
                (staging / generate.BUILD_STATE_FILE).write_text(
                    json.dumps(
                        {
                            "schema_version": generate.BUILD_SCHEMA_VERSION,
                            "source_fingerprint": "source-one",
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertFalse(kwargs["validate_output"])
                return staging

            with (
                mock.patch.object(
                    generate,
                    "play_bundle_directory",
                    return_value=final,
                ),
                mock.patch.object(
                    generate,
                    "build_play_bundle_to",
                    side_effect=build_staging,
                ),
                mock.patch.object(
                    generate,
                    "validate_play_bundle",
                    side_effect=lambda path: Path(path).resolve(),
                ) as validate,
            ):
                result = generate.generate_play_bundle(
                    str(root),
                    seed_sfx=False,
                    source_fingerprint="source-one",
                )

            self.assertEqual(result, final)
            self.assertEqual(validate.call_count, 1)

    def test_postcommit_verification_failure_restores_previous_play_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            final = root / "output" / "Play" / "Current"
            final.mkdir(parents=True)
            (final / "old.txt").write_text("preserved", encoding="utf-8")

            def build_staging(_root: Path, staging: Path, **_kwargs) -> Path:
                staging.mkdir(parents=True)
                for name in ("index.html", "styles.css", "script.js"):
                    (staging / name).write_text("new", encoding="utf-8")
                (staging / generate.BUILD_STATE_FILE).write_text(
                    json.dumps(
                        {
                            "schema_version": generate.BUILD_SCHEMA_VERSION,
                            "source_fingerprint": "source-one",
                        }
                    ),
                    encoding="utf-8",
                )
                return staging

            with (
                mock.patch.object(generate, "ensure_output_dirs"),
                mock.patch.object(
                    generate,
                    "play_bundle_directory",
                    return_value=final,
                ),
                mock.patch.object(
                    generate,
                    "build_play_bundle_to",
                    side_effect=build_staging,
                ),
                mock.patch.object(
                    generate,
                    "validate_play_bundle",
                    side_effect=lambda path: Path(path).resolve(),
                ),
                mock.patch.object(
                    generate,
                    "_verify_committed_play_bundle",
                    side_effect=RuntimeError("postcommit failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "postcommit failure"):
                    generate.generate_play_bundle(
                        str(root),
                        seed_sfx=False,
                        source_fingerprint="source-one",
                    )

            self.assertEqual(
                (final / "old.txt").read_text(encoding="utf-8"),
                "preserved",
            )
            self.assertFalse((final / "index.html").exists())

    def test_file_digest_cache_invalidates_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            source.write_bytes(b"first")
            with generate._FILE_DIGEST_CACHE_LOCK:
                generate._FILE_DIGEST_CACHE.clear()

            first = generate._file_content_digest(source)
            again = generate._file_content_digest(source)
            source.write_bytes(b"other")
            changed = generate._file_content_digest(source)

            self.assertEqual(first, again)
            self.assertNotEqual(first, changed)

    def test_file_digest_cache_detects_restored_mtime_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            source.write_bytes(b"first")
            original = source.stat()
            with generate._FILE_DIGEST_CACHE_LOCK:
                generate._FILE_DIGEST_CACHE.clear()
            first = generate._file_content_digest(source)

            source.write_bytes(b"other")
            os.utime(
                source,
                ns=(original.st_atime_ns, original.st_mtime_ns),
            )
            changed = generate._file_content_digest(source)

            self.assertNotEqual(first, changed)

    def test_generate_asset_copier_hardlinks_unchanged_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"unchanged-asset")
            first_build = root / "first-build"
            first_output = first_build / "gallery" / "sounds" / "music.mp3"
            first = generate._BuildAssetCopier(first_build, None)
            first.copy(source, first_output)
            (first_build / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"asset_sources": first.records}),
                encoding="utf-8",
            )

            second_build = root / "second-build"
            second_output = second_build / "gallery" / "sounds" / "music.mp3"
            second = generate._BuildAssetCopier(second_build, first_build)
            with mock.patch(
                "generate._atomic_copy_file",
                side_effect=AssertionError("unchanged asset should be reused"),
            ):
                second.copy(source, second_output)

            self.assertEqual(second_output.read_bytes(), source.read_bytes())
            self.assertTrue(os.path.samefile(first_output, second_output))

    def test_generate_asset_copier_rejects_corrupt_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"unchanged-asset")
            first_build = root / "first-build"
            first_output = first_build / "gallery" / "sounds" / "music.mp3"
            first = generate._BuildAssetCopier(first_build, None)
            first.copy(source, first_output)
            (first_build / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"asset_sources": first.records}),
                encoding="utf-8",
            )
            original_stat = first_output.stat()
            first_output.write_bytes(b"X" * len(source.read_bytes()))
            os.utime(
                first_output,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            second_build = root / "second-build"
            second_output = second_build / "gallery" / "sounds" / "music.mp3"
            second = generate._BuildAssetCopier(second_build, first_build)
            second.copy(source, second_output)

            self.assertEqual(second_output.read_bytes(), source.read_bytes())
            self.assertFalse(os.path.samefile(first_output, second_output))

    def test_generate_asset_copier_rejects_symlinked_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"unchanged-asset")
            first_build = root / "first-build"
            first_output = first_build / "asset.bin"
            first = generate._BuildAssetCopier(first_build, None)
            first.copy(source, first_output)
            (first_build / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"asset_sources": first.records}),
                encoding="utf-8",
            )
            first_output.unlink()
            try:
                first_output.symlink_to(source)
            except OSError as error:
                self.skipTest(f"File symlinks are unavailable: {error}")

            second_build = root / "second-build"
            second_output = second_build / "asset.bin"
            second = generate._BuildAssetCopier(second_build, first_build)
            second.copy(source, second_output)

            self.assertFalse(second_output.is_symlink())
            self.assertFalse(os.path.samefile(source, second_output))
            self.assertEqual(second_output.read_bytes(), source.read_bytes())

    def test_generate_refreshes_identity_after_old_hardlink_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"unchanged-asset")
            first_build = root / "first-build"
            first_output = first_build / "asset.bin"
            first = generate._BuildAssetCopier(first_build, None)
            first.copy(source, first_output)
            (first_build / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"asset_sources": first.records}),
                encoding="utf-8",
            )

            second_build = root / "second-build"
            second_output = second_build / "asset.bin"
            second = generate._BuildAssetCopier(second_build, first_build)
            second.copy(source, second_output)
            state_path = second_build / generate.BUILD_STATE_FILE
            state_path.write_text(
                json.dumps({"asset_sources": second.records}),
                encoding="utf-8",
            )
            first_output.unlink()

            generate._refresh_build_asset_identities(second_build)

            refreshed = json.loads(state_path.read_text(encoding="utf-8"))
            output_stat = second_output.stat()
            self.assertEqual(
                refreshed["asset_sources"]["asset.bin"][
                    "output_change_token"
                ],
                generate.file_change_token(
                    second_output,
                    stat_result=output_stat,
                ),
            )

    def test_runtime_page_copies_use_integrity_safe_asset_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_pages = root / "source-pages"
            source_pages.mkdir()
            for index, name in enumerate(REQUIRED_SLIDES):
                Image.new("RGB", (8, 8), (40 + index, 80, 120)).save(
                    source_pages / name
                )

            first_build = root / "first-build"
            first_pages = first_build / "gallery" / "pages"
            first = generate._BuildAssetCopier(first_build, None)
            generate.build_runtime_image_assets(
                source_pages,
                first_pages,
                reconcile_source=False,
                copy_file=first.copy,
            )
            (first_build / generate.BUILD_STATE_FILE).write_text(
                json.dumps({"asset_sources": first.records}),
                encoding="utf-8",
            )

            second_build = root / "second-build"
            second_pages = second_build / "gallery" / "pages"
            second = generate._BuildAssetCopier(second_build, first_build)
            with mock.patch(
                "generate._atomic_copy_file",
                side_effect=AssertionError("unchanged pages should be reused"),
            ):
                generate.build_runtime_image_assets(
                    source_pages,
                    second_pages,
                    reconcile_source=False,
                    reuse_pages_directory=first_pages,
                    copy_file=second.copy,
                )

            for name in REQUIRED_SLIDES:
                self.assertTrue(
                    os.path.samefile(first_pages / name, second_pages / name)
                )

    def test_atomic_copy_does_not_propagate_read_only_source_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.bin"
            second = root / "second.bin"
            destination = root / "destination.bin"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            first.chmod(stat.S_IREAD)
            second.chmod(stat.S_IREAD)
            try:
                for copier in (
                    atomic_copy_file,
                    generate._atomic_copy_file,
                    font_export._atomic_copy_file,
                ):
                    destination.unlink(missing_ok=True)
                    copier(first, destination)
                    copier(second, destination)
                    self.assertEqual(destination.read_bytes(), b"second")
                    self.assertTrue(destination.stat().st_mode & stat.S_IWUSR)
            finally:
                first.chmod(stat.S_IREAD | stat.S_IWRITE)
                second.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_bundled_variable_fonts_resolve_by_family_name(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        italic_families = {
            "Cormorant Garamond",
            "Exo 2",
            "IBM Plex Sans",
            "Lora",
            "Montserrat",
            "Source Sans 3",
            "Source Serif 4",
        }
        for family in (
            "Cinzel",
            "Cormorant Garamond",
            "Exo 2",
            "IBM Plex Sans",
            "Lora",
            "Manrope",
            "Montserrat",
            "Source Sans 3",
            "Source Serif 4",
        ):
            with self.subTest(family=family):
                faces = font_export.resolve_font_faces_for_family(
                    project_root,
                    family,
                )
                self.assertTrue(faces)
                if family in italic_families:
                    self.assertIn("normal", {face.style for face in faces})
                    self.assertIn("italic", {face.style for face in faces})

    def test_ttc_faces_resolve_and_export_as_standalone_fonts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fonts = root / "gallery" / "user" / "fonts"
            fonts.mkdir(parents=True)
            collection = fonts / "UnrelatedCollection.ttc"
            self._write_synthetic_ttc(collection, "Arcane Collection")

            faces = font_export.resolve_font_faces_for_family(
                root,
                "Arcane Collection",
            )
            self.assertEqual(
                [(face.weight, face.style) for face in faces],
                [(400, "normal"), (700, "italic")],
            )
            self.assertEqual(
                [face.collection_index for face in faces],
                [0, 1],
            )

            destination = root / "export" / "gallery" / "fonts"
            result = font_export.build_embedded_font_payload(
                root,
                (
                    '<span style="font-family:\'Arcane Collection\';">'
                    "Letter</span>"
                ),
                destination,
            )

            self.assertIn("LetterSmithFont1", result.html)
            self.assertIn("font-style:normal;font-weight:400", result.css)
            self.assertIn("font-style:italic;font-weight:700", result.css)
            self.assertEqual(len(result.report["files"]), 2)
            for name in result.report["files"]:
                self.assertTrue(name.endswith(".ttf"))
                exported = (destination / name).read_bytes()
                self.assertEqual(exported[:4], b"\x00\x01\x00\x00")
                self.assertEqual(
                    font_export._sfnt_checksum(exported),
                    font_export.SFNT_CHECKSUM_MAGIC,
                )

    def test_inconsistent_active_path_falls_back_and_repairs_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            canonical = self._write_play_bundle(
                root / "output" / "Play" / "Ada Lovelace" / "Current Letter",
                project_id,
            )
            stale = self._write_play_bundle(
                root / "output" / "Play" / "Grace Hopper" / "Other Letter",
                str(uuid.uuid4()),
            )
            SettingsStore(root).update_fields(
                {
                    "project_id": project_id,
                    "recipient_name": "Ada Lovelace",
                    "recipient_title": "Current Letter",
                    ACTIVE_PLAY_DIR_KEY: str(stale),
                }
            )

            with mock.patch(
                "config._iter_play_bundles",
                wraps=config._iter_play_bundles,
            ) as scan:
                resolved = generate.play_bundle_directory(root)

            self.assertTrue(scan.called)
            self.assertEqual(resolved, canonical)
            self.assertEqual(
                SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY),
                str(canonical),
            )

    def test_unneeded_numbered_path_falls_back_to_canonical_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            numbered = self._write_play_bundle(
                root
                / "output"
                / "Play"
                / "Ada Lovelace"
                / "Current Letter (2)",
                project_id,
            )
            SettingsStore(root).update_fields(
                {
                    "project_id": project_id,
                    "recipient_name": "Ada Lovelace",
                    "recipient_title": "Current Letter",
                    ACTIVE_PLAY_DIR_KEY: str(numbered),
                }
            )

            with mock.patch(
                "config._iter_play_bundles",
                wraps=config._iter_play_bundles,
            ) as scan:
                resolved = generate.play_bundle_directory(root)

            canonical = (
                root
                / "output"
                / "Play"
                / "Ada Lovelace"
                / "Current Letter"
            ).resolve()
            self.assertTrue(scan.called)
            self.assertEqual(resolved, canonical)
            self.assertFalse(numbered.exists())

    def test_renamed_legacy_bundle_uses_scanning_migration_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            legacy = self._write_play_bundle(
                root / "output" / "Play" / "legacy-letter",
                project_id,
            )
            SettingsStore(root).update_fields(
                {
                    "project_id": project_id,
                    "recipient_name": "CON",
                    "recipient_title": "A/B?",
                    ACTIVE_PLAY_DIR_KEY: str(legacy),
                }
            )

            with mock.patch(
                "config._iter_play_bundles",
                wraps=config._iter_play_bundles,
            ) as scan:
                resolved = generate.play_bundle_directory(root)

            expected = (
                root / "output" / "Play" / "_CON" / "A B"
            ).resolve()
            self.assertTrue(scan.called)
            self.assertEqual(resolved, expected)
            self.assertFalse(legacy.exists())
            self.assertEqual(
                SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY),
                str(expected),
            )
            metadata = json.loads(
                (expected / config.PLAY_METADATA_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["project_id"], project_id)
            self.assertEqual(metadata["recipient_name"], "CON")
            self.assertEqual(metadata["recipient_title"], "A/B?")


if __name__ == "__main__":
    unittest.main()
