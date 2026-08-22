from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PIL import Image

import config
import generate
from config import CONTROL_FILES, REQUIRED_SLIDES, USER_CONTROLS_DIR, USER_MESSAGE_DIR, USER_PAGES_DIR
from project_paths import ProjectPathResolver
from project_save import ProjectSaveService
from project_state import ApplicationState, ProjectDirtyController, ProjectStateController
from readiness import evaluate_readiness
from saved_letters import SavedLetterCatalog, SavedLetterRestorer, update_saved_metadata
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from startup_check import run_startup_self_check
from transactional_io import cleanup_abandoned_temp_files


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
            update_saved_metadata(play_dir, root, evaluate_readiness(root))
            entry = SavedLetterCatalog(root).list_entries()[0]

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
