from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from save_schema import (
    SaveSchemaMigrationRegistry,
    SaveSchemaMigrationStepResult,
    SaveSchemaRepair,
    migrate_saved_letter_metadata_file,
)


class SaveSchemaMigrationTests(unittest.TestCase):
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
