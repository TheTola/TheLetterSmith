from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from named_text_styles import (
    InvalidStyleSetError,
    NamedStyleSet,
    ParagraphStyleDefinition,
    SavedStyleSetStore,
    STYLE_KEYS,
    STYLE_PROPERTIES,
    STYLE_SCHEMA_VERSION,
    StyleSetOverwriteRequired,
    default_style_set,
    validate_document_style_state,
)
from settings_store import SettingsStore


class NamedTextStyleTests(unittest.TestCase):
    def test_normal_font_update_changes_only_other_font_families(self) -> None:
        original = default_style_set()
        title = replace(
            original.get("title"),
            font_family="Georgia",
            font_color="#aabbcc",
            underline=True,
            paragraph=ParagraphStyleDefinition(alignment=2, line_height=150, line_height_type=1),
        )
        original = original.update_style("title", title)
        updated_normal = replace(
            original.get("normal_text"),
            font_family="Arial",
            font_size=23,
            font_color="#123456",
            italic=True,
            paragraph=ParagraphStyleDefinition(top_margin=12, list_style=-1),
        )
        changed = original.update_style("normal_text", updated_normal)
        self.assertEqual(changed.get("normal_text"), updated_normal)
        for key in STYLE_KEYS[1:]:
            self.assertEqual(changed.get(key).font_family, "Arial")
            self.assertEqual(
                replace(changed.get(key), font_family=original.get(key).font_family),
                original.get(key),
            )
        self.assertEqual(original.get("title"), title)

    def test_no_propagation_for_nonfont_or_other_style_updates(self) -> None:
        original = default_style_set()
        recolored = original.update_style(
            "normal_text", replace(original.get("normal_text"), font_color="#123456")
        )
        self.assertEqual(
            [recolored.get(key) for key in STYLE_KEYS[1:]],
            [original.get(key) for key in STYLE_KEYS[1:]],
        )
        changed_title = recolored.update_style(
            "title", replace(recolored.get("title"), font_family="Georgia")
        )
        self.assertEqual(changed_title.get("normal_text"), recolored.get("normal_text"))
        self.assertEqual(changed_title.get("subtitle"), recolored.get("subtitle"))

    def test_serialization_is_versioned_and_rejects_partial_or_future_data(self) -> None:
        original = default_style_set()
        self.assertEqual(NamedStyleSet.from_dict(original.to_dict()), original)
        for change in (
            lambda data: data["styles"]["title"].pop("underline"),
            lambda data: data.update(version=STYLE_SCHEMA_VERSION + 1),
            lambda data: data["styles"]["title"].update(font_color="not a color"),
            lambda data: data["styles"]["title"].update(italic=0),
            lambda data: data["styles"].pop("heading_3"),
        ):
            with self.subTest(change=change):
                data = copy.deepcopy(original.to_dict())
                change(data)
                with self.assertRaises(InvalidStyleSetError):
                    NamedStyleSet.from_dict(data)

    def test_legacy_styles_load_without_changing_paragraph_layout(self) -> None:
        original = default_style_set()
        legacy = original.to_dict()
        legacy["version"] = 1
        for definition in legacy["styles"].values():
            definition.pop("paragraph")
        self.assertEqual(NamedStyleSet.from_dict(legacy), original)
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder))
            settings.update_fields({"editor_style_set_1": legacy})
            self.assertEqual(SavedStyleSetStore(settings).load(1), original)

    def test_explicit_normal_update_reunifies_an_independently_changed_family(self) -> None:
        original = default_style_set()
        original = original.update_style("title", replace(original.get("title"), font_family="Arial"))
        changed = original.update_style("normal_text", original.get("normal_text"))
        self.assertEqual(
            changed.get("title"),
            replace(original.get("title"), font_family=original.get("normal_text").font_family),
        )

    def test_paragraph_style_validation_and_roundtrip(self) -> None:
        paragraph = ParagraphStyleDefinition(
            alignment=2, direction=0, line_height=175, line_height_type=1,
            top_margin=12, bottom_margin=8, left_margin=24, right_margin=16,
            text_indent=-8, indent=2, list_style=-4, list_indent=3,
            list_prefix="(", list_suffix=")", list_start=7,
        )
        original = default_style_set()
        original = original.update_style("title", replace(original.get("title"), paragraph=paragraph))
        self.assertEqual(NamedStyleSet.from_dict(original.to_dict()), original)
        for changes in (
            {"alignment": True}, {"direction": 3}, {"list_style": 42},
            {"line_height": float("inf")}, {"text_indent": "12"},
            {"list_start": 2**31}, {"list_prefix": []},
        ):
            with self.subTest(changes=changes), self.assertRaises(InvalidStyleSetError):
                ParagraphStyleDefinition.from_dict({**paragraph.to_dict(), **changes})

    def test_saved_slots_are_independent_and_preserve_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = SavedStyleSetStore(SettingsStore(root))
            second = SavedStyleSetStore(SettingsStore(root))
            first.settings.update_fields({"recipient_name": "Ada"})
            original = default_style_set()
            different = original.update_style(
                "title", replace(
                    original.get("title"), font_family="Georgia",
                    paragraph=ParagraphStyleDefinition(alignment=2, list_style=-4),
                )
            )
            self.assertTrue(first.save(1, original))
            self.assertTrue(second.save(2, different))
            self.assertEqual(second.load(1), original)
            self.assertEqual(first.load(2), different)
            self.assertIsNone(first.load(3))
            self.assertEqual(first.settings.get("recipient_name"), "Ada")
            raw_copy = first.raw_slot(1)
            raw_copy["styles"]["title"]["font_family"] = "Other"
            self.assertEqual(first.load(1), original)
            edited = first.load(1).update_style(
                "heading_1", replace(original.get("heading_1"), font_color="#123456")
            )
            self.assertNotEqual(edited, first.load(1))
            with self.assertRaises(StyleSetOverwriteRequired):
                first.save(1, edited)
            self.assertEqual(second.load(1), original)
            self.assertTrue(first.save(1, edited, overwrite=True))
            self.assertFalse(first.save(1, edited))
            self.assertEqual(second.load(2), different)

    def test_invalid_slot_is_not_destroyed_by_load_or_save(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder))
            slots = SavedStyleSetStore(settings)
            settings.update_fields({"editor_style_set_1": {"version": 99}})
            with self.assertRaises(InvalidStyleSetError):
                slots.load(1)
            with self.assertRaises(InvalidStyleSetError):
                slots.save(1, default_style_set(), overwrite=True)
            self.assertEqual(slots.raw_slot(1), {"version": 99})

    def test_document_state_validates_all_blocks_before_application(self) -> None:
        source = {
            "schema_version": 1,
            "definitions": default_style_set().to_dict(),
            "blocks": [
                {
                    "style": "heading_1",
                    "overrides": [
                        {"start": 0, "end": 3, "mask": ["italic", "font_color"]}
                    ],
                },
                {"style": None, "overrides": []},
            ],
        }
        result = validate_document_style_state(source, block_lengths=[5, 0])
        self.assertEqual(
            result["blocks"][0]["overrides"][0]["mask"],
            [name for name in STYLE_PROPERTIES if name in {"italic", "font_color"}],
        )
        source["blocks"][0]["overrides"][0]["mask"].append("font_family")
        self.assertNotEqual(source, result)
        bad = copy.deepcopy(source)
        bad["blocks"][1]["overrides"] = [
            {"start": 0, "end": 1, "mask": ["italic"]}
        ]
        with self.assertRaises(InvalidStyleSetError):
            validate_document_style_state(bad, block_lengths=[5, 0])
        bad = copy.deepcopy(source)
        bad["blocks"][0]["overrides"][0]["mask"] = ["unknown"]
        with self.assertRaises(InvalidStyleSetError):
            validate_document_style_state(bad)


if __name__ == "__main__":
    unittest.main()
