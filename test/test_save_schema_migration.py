from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from config import CONTROL_FILES, REQUIRED_SLIDES
from image_animation import validate_runtime_image_manifest
from save_schema import (
    SaveSchemaMigrationError,
    SaveSchemaMigrationRegistry,
    SaveSchemaMigrationStepResult,
    SaveSchemaRepair,
    migrate_saved_letter_metadata_file,
    validate_saved_letter_metadata,
)
from tools.upgrade_saved_letters import (
    PreReleaseSavedLetterMigrationError,
    upgrade_saved_letter_bundle,
    upgrade_saved_letter_library,
)


class SaveSchemaMigrationTests(unittest.TestCase):
    def test_numeric_pre_schema_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "lettersmith-metadata.json"
            metadata_path.write_text(
                json.dumps({"schema_version": 4}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                SaveSchemaMigrationError,
                "invalid save schema version",
            ):
                migrate_saved_letter_metadata_file(metadata_path)

    def test_missing_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "lettersmith-metadata.json"
            metadata_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                SaveSchemaMigrationError,
                "invalid save schema version",
            ):
                migrate_saved_letter_metadata_file(metadata_path)

    def test_numeric_and_missing_pre_release_metadata_are_not_loadable(self) -> None:
        numeric = {"schema_version": 4, "recipient_name": "Ada"}
        missing = {"recipient_name": "Grace"}

        for metadata in (numeric, missing):
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(
                    SaveSchemaMigrationError,
                    "invalid save schema version",
                ):
                    validate_saved_letter_metadata(metadata)

    def test_pre_release_bundle_upgrade_is_complete_and_idempotent(self) -> None:
        for schema_version in (3, None):
            with self.subTest(schema_version=schema_version):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    bundle = root / "output" / "Play" / "Ada" / "Letter"
                    pages = bundle / "gallery" / "pages"
                    message = bundle / "gallery" / "message"
                    sounds = bundle / "gallery" / "sounds"
                    controls = bundle / "gallery" / "controls"
                    for path in (pages, message, sounds, controls):
                        path.mkdir(parents=True, exist_ok=True)
                    for filename in ("index.html", "styles.css", "script.js"):
                        (bundle / filename).write_text(filename, encoding="utf-8")
                    for filename in REQUIRED_SLIDES:
                        (pages / filename).write_bytes(filename.encode("ascii"))
                    for filename in CONTROL_FILES:
                        (controls / filename).write_bytes(filename.encode("ascii"))
                    (message / "message.html").write_text(
                        "<p>Hello</p>",
                        encoding="utf-8",
                    )
                    (sounds / "lettersmith-sound.json").write_text(
                        json.dumps(
                            {
                                "version": 2,
                                "mode": "single",
                                "crossfade_ms": 0,
                                "tracks": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                    metadata = {
                        "project_id": str(uuid.uuid4()),
                        "recipient_name": "Ada Lovelace",
                        "recipient_title": "A New Machine",
                        "starting_volume": 42,
                    }
                    if schema_version is not None:
                        metadata["schema_version"] = schema_version
                    metadata_path = bundle / "lettersmith-metadata.json"
                    metadata_path.write_text(
                        json.dumps(metadata),
                        encoding="utf-8",
                    )

                    self.assertTrue(upgrade_saved_letter_bundle(root, bundle))
                    upgraded = json.loads(metadata_path.read_text(encoding="utf-8"))
                    validated = validate_saved_letter_metadata(upgraded)

                    self.assertEqual(validated["schema_version"], "1.0")
                    self.assertEqual(validated["document_type"], "saved_letter")
                    self.assertEqual(validated["settings"]["starting_volume"], 42)
                    self.assertTrue((bundle / "prompt_writer_state.json").is_file())
                    validate_runtime_image_manifest(pages)
                    self.assertFalse(upgrade_saved_letter_bundle(root, bundle))

    def test_library_upgrade_continues_after_one_malformed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "output" / "Play" / "Ada" / "Broken"
            valid = root / "output" / "Play" / "Grace" / "Valid"
            for bundle in (broken, valid):
                bundle.mkdir(parents=True)
                (bundle / "lettersmith-metadata.json").write_text(
                    "{}",
                    encoding="utf-8",
                )

            def upgrade(_root: Path, bundle: Path) -> bool:
                if bundle.name == "Broken":
                    raise PreReleaseSavedLetterMigrationError("malformed")
                return True

            with (
                mock.patch(
                    "tools.upgrade_saved_letters.SavedLetterCatalog._is_valid_candidate",
                    return_value=True,
                ),
                mock.patch(
                    "tools.upgrade_saved_letters.upgrade_saved_letter_bundle",
                    side_effect=upgrade,
                ),
                mock.patch("tools.upgrade_saved_letters._LOGGER.warning"),
            ):
                self.assertEqual(upgrade_saved_letter_library(root), (1, 1))

    def test_sequential_migration_repairs_saves_and_reopens(self) -> None:
        registry = SaveSchemaMigrationRegistry("1.2")

        @registry.migration("1.0", "1.1")
        def add_required_theme(
            metadata: dict[str, Any],
            repair_values: Mapping[str, Any],
        ) -> SaveSchemaMigrationStepResult:
            theme = str(repair_values.get("replacement_theme", "")).strip()
            if not theme:
                return SaveSchemaMigrationStepResult(
                    metadata,
                    (
                        SaveSchemaRepair(
                            "replacement_theme",
                            "Choose a replacement theme.",
                            field="settings.theme",
                            options=("classic", "modern"),
                        ),
                    ),
                )
            migrated = dict(metadata)
            settings = dict(migrated.get("settings", {}))
            settings["theme"] = theme
            migrated["settings"] = settings
            return SaveSchemaMigrationStepResult(migrated)

        @registry.migration("1.1", "1.2")
        def add_prompt_writer_revision(
            metadata: dict[str, Any],
            _repair_values: Mapping[str, Any],
        ) -> SaveSchemaMigrationStepResult:
            migrated = dict(metadata)
            prompt_writer = dict(migrated.get("prompt_writer", {}))
            prompt_writer["revision"] = 2
            migrated["prompt_writer"] = prompt_writer
            return SaveSchemaMigrationStepResult(migrated)

        def validate_current(metadata: Mapping[str, Any]) -> None:
            if metadata.get("schema_version") != "1.2":
                raise ValueError("schema_version must be 1.2")
            if metadata.get("settings", {}).get("theme") not in {
                "classic",
                "modern",
            }:
                raise ValueError("settings.theme is invalid")

        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "lettersmith-metadata.json"
            original = {
                "schema_version": "1.0",
                "settings": {},
                "prompt_writer": {
                    "generated_prompts": [
                        "Cover prompt.\nPreserve this exact line break.",
                        "Letter prompt!",
                        "Wall prompt?",
                        "Back prompt.",
                    ]
                },
            }
            metadata_path.write_text(
                json.dumps(original, indent=2),
                encoding="utf-8",
            )
            original_bytes = metadata_path.read_bytes()

            repair_report = migrate_saved_letter_metadata_file(
                metadata_path,
                registry=registry,
                validator=validate_current,
            )

            self.assertFalse(repair_report.complete)
            self.assertEqual(
                tuple(
                    repair.repair_id
                    for repair in repair_report.required_repairs
                ),
                ("replacement_theme",),
            )
            self.assertEqual(metadata_path.read_bytes(), original_bytes)

            migrated_report = migrate_saved_letter_metadata_file(
                metadata_path,
                registry=registry,
                repair_values={"replacement_theme": "classic"},
                validator=validate_current,
            )

            self.assertTrue(migrated_report.complete)
            self.assertEqual(
                migrated_report.applied_steps,
                (("1.0", "1.1"), ("1.1", "1.2")),
            )
            migrated = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], "1.2")
            self.assertEqual(migrated["settings"]["theme"], "classic")
            self.assertEqual(migrated["prompt_writer"]["revision"], 2)
            self.assertEqual(
                migrated["prompt_writer"]["generated_prompts"],
                original["prompt_writer"]["generated_prompts"],
            )

            migrated_bytes = metadata_path.read_bytes()
            reopened_report = migrate_saved_letter_metadata_file(
                metadata_path,
                registry=registry,
                validator=validate_current,
            )
            self.assertTrue(reopened_report.complete)
            self.assertFalse(reopened_report.changed)
            self.assertEqual(metadata_path.read_bytes(), migrated_bytes)


if __name__ == "__main__":
    unittest.main()
