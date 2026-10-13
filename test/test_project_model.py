from __future__ import annotations

from copy import deepcopy
import json
import threading
import unittest

from project_model import (
    CLASSIC_PAGE_TYPES, MAX_PAGES, PAGE_TYPES, PageLimitError, ProjectModel,
    ProjectModelError, UnsupportedProjectError, create_project, empty_message,
)
from project_migration import migrate_project


def five_page_project():
    """Reusable synthetic fixture for dependent feature work; no user data."""
    project = create_project(["cover", "letter", "illustration", "letter", "end_page"])
    for index, page in enumerate(project.pages):
        project.update_artwork(page["id"], {"asset": f"assets/background-{index}.png", "media_type": "image/png"})
        if page["type"] == "letter":
            label = "A" if index == 1 else "B"
            message = empty_message()
            message.update({"html": f'<p><b>Message {label}</b> — café</p>', "layout": {"width": index * 100}})
            project.update_message(page["id"], message)
            project.update_prompt(page["id"], {"text": f"  Prompt {label}\n雪\n ", "resolved_components": {"palette": [label]}, "stale": False})
    return project


def legacy_fixture(*, count=4):
    layout = [{"key": key, "kind": key} for key in ("cover", "letter", "wall", "back")]
    for index in range(max(0, count - 4)):
        layout.insert(-1, {"key": f"page_{index + 1}", "kind": "wall"})
    return {
        "metadata": {"schema_version": "1.0", "project_schema_version": 2, "project_id": "legacy-project", "publication_custom": "kept"},
        "page_layout_version": 1, "page_layout": layout,
        "images": {page["key"]: {"source_file": page["key"] + ".png"} for page in layout},
        "asset_prefix": "gallery/pages", "settings": {"style": "blue"},
        "message_html": '<p style="color:red"><b>Legacy — 雪</b></p>\n',
        "message_resources": ["message_assets/attachment.png"],
        "prompt_writer_state": {"version": 6, "color": "Missing library palette", "cover": "exact directions\n",
                                "generated_prompts": {"cover": " \nHistorical four-page Wall Letter — 雪\n "},
                                "resolved_instructions": {"role": "Chosen artist"}},
        "sound": {"playlist": ["sounds/a.mp3", "sounds/b.mp3"], "volume": 40},
    }


class ProjectModelTests(unittest.TestCase):
    def test_types_and_templates(self):
        self.assertEqual(tuple(PAGE_TYPES), CLASSIC_PAGE_TYPES)
        self.assertEqual([item.artwork_template for item in PAGE_TYPES.values()], ["cover", "letter", "wall", "back"])
        self.assertEqual([item.id for item in PAGE_TYPES.values() if item.supports_message], ["letter"])

    def test_five_page_identity_duplicate_and_roundtrip(self):
        project = five_page_project()
        original_ids = [page["id"] for page in project.pages]
        letter_a, letter_b = original_ids[1], original_ids[3]
        original_a = project.get_page(letter_a)
        project.rename_page(letter_a, "Same name")
        project.rename_page(letter_b, "Same name")
        order = original_ids[:]
        order[1], order[3] = order[3], order[1]
        project.reorder_pages(order)
        duplicate = project.duplicate_page(letter_a)
        duplicate_message = project.get_message(duplicate)
        duplicate_message["html"] = "<p>Duplicate</p>"
        duplicate_message["layout"]["width"] = 9
        project.update_message(duplicate, duplicate_message)
        project.update_prompt(duplicate, {"text": "Duplicate prompt", "resolved_components": {"palette": ["Z"]}})
        project.update_artwork(duplicate, {"placement": {"x": 0.4}, "animation": {"speed_percent": 22}})
        self.assertEqual(project.get_message(letter_a), original_a["message"])
        self.assertEqual(project.get_prompt(letter_a)["text"], original_a["prompt"]["text"])
        self.assertEqual(project.get_page(letter_a)["artwork"], original_a["artwork"])
        restored = ProjectModel.from_dict(json.loads(json.dumps(project.to_dict(), ensure_ascii=False)))
        self.assertEqual(restored.to_dict(), project.to_dict())
        self.assertEqual(restored.pages[1]["id"], letter_b)
        self.assertEqual(restored.get_prompt(letter_b)["text"], "  Prompt B\n雪\n ")

    def test_type_changes_retain_independent_message_art_prompt(self):
        project = five_page_project()
        page_id = project.pages[1]["id"]
        original = project.get_page(page_id)
        project.change_page_type(page_id, "cover")
        changed = project.get_page(page_id)
        self.assertIsNone(changed["message"])
        self.assertEqual(changed["recovery"]["inactive_message"], original["message"])
        self.assertEqual(changed["artwork"], original["artwork"])
        self.assertEqual(changed["prompt"]["text"], original["prompt"]["text"])
        project.change_page_type(page_id, "letter")
        self.assertEqual(project.get_message(page_id), original["message"])

    def test_empty_draft_and_limits(self):
        project = create_project([])
        self.assertEqual(project.pages, [])
        for _ in range(MAX_PAGES):
            project.add_page("cover")
        with self.assertRaises(PageLimitError):
            project.add_page("cover")
        for _ in range(MAX_PAGES - 2):
            project.delete_page(project.pages[0]["id"])
        with self.assertRaises(PageLimitError):
            project.delete_page(project.pages[0]["id"])
        self.assertEqual(ProjectModel.from_dict(project.to_dict()).pages, project.pages)

    def test_delete_restore_and_defensive_snapshots(self):
        project = five_page_project()
        page_id = project.pages[1]["id"]
        snapshot = project.get_page(page_id)
        record = project.delete_page(page_id)
        self.assertEqual(project.to_dict()["recovery"]["deleted_pages"][0]["page"], snapshot)
        project.restore_page(record)
        self.assertEqual(project.get_message(page_id), snapshot["message"])
        copied = project.get_page(page_id)
        copied["message"]["html"] = "uncommitted mutation"
        self.assertEqual(project.get_message(page_id), snapshot["message"])

    def test_shared_defaults_preserve_explicit_empty_and_exact_authorship(self):
        project = five_page_project()
        page_id = project.pages[1]["id"]
        before = project.get_page(page_id)
        project.update_shared_settings({"color": "red", "font": "Papyrus"})
        project.update_page_settings(page_id, {"color": "", "font": None})
        self.assertEqual(project.resolve_settings(page_id), {"color": "", "font": None})
        project.clear_page_override(page_id, "color")
        self.assertEqual(project.resolve_settings(page_id)["color"], "red")
        after = project.get_page(page_id)
        self.assertEqual(after["message"], before["message"])
        self.assertEqual(after["artwork"], before["artwork"])
        self.assertEqual(after["prompt"]["text"], before["prompt"]["text"])
        self.assertTrue(after["prompt"]["stale"])

    def test_events_reflect_complete_state(self):
        project = five_page_project()
        observed = []
        project.add_listener(lambda event: observed.append((event, project.to_dict())))
        project.reorder_pages(reversed([page["id"] for page in project.pages]))
        self.assertEqual(observed[-1][0].kind, "structure")
        self.assertEqual(observed[-1][1], project.to_dict())
        page_id = project.pages[0]["id"]
        project.update_page_settings(page_id, {"color": "red"})
        self.assertEqual(observed[-1][1], project.to_dict())
        revision = project.revision
        project.update_page_settings(page_id, {"color": "red"})
        self.assertEqual(project.revision, revision)

    def test_listener_failure_does_not_abort_mutation_or_other_listeners(self):
        project = create_project()
        received = []
        def fail(event):
            raise RuntimeError("Synthetic listener failure")
        project.add_listener(fail)
        project.add_listener(received.append)
        with self.assertLogs("project_model", level="ERROR"):
            project.rename_page(project.pages[0]["id"], "Changed")
        self.assertEqual(project.pages[0]["name"], "Changed")
        self.assertEqual(received[-1].kind, "name")

    def test_callbacks_release_model_lock_before_other_thread_snapshot(self):
        project = create_project()
        completed = threading.Event()
        snapshots = []
        threads = []
        available_during_callback = []
        def listener(event):
            def snapshot():
                snapshots.append(project.to_dict())
                completed.set()
            worker = threading.Thread(target=snapshot)
            threads.append(worker)
            worker.start()
            available_during_callback.append(completed.wait(2))
        project.add_listener(listener)
        project.rename_page(project.pages[0]["id"], "Renamed")
        for worker in threads:
            worker.join(2)
        self.assertEqual(available_during_callback, [True], "Model lock was held during callback")
        self.assertEqual(snapshots[0]["pages"][0]["name"], "Renamed")

    def test_concurrent_type_edits_and_snapshots_remain_structurally_valid(self):
        project = five_page_project()
        page_id = project.pages[1]["id"]
        failures = []
        start = threading.Barrier(2)
        def edit():
            start.wait()
            try:
                for _ in range(150):
                    project.change_page_type(page_id, "cover")
                    project.change_page_type(page_id, "letter")
            except Exception as error:
                failures.append(error)
        worker = threading.Thread(target=edit)
        worker.start()
        start.wait()
        for _ in range(150):
            ProjectModel.from_dict(project.to_dict())
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])

    def test_events_retain_fields_across_reverted_edits(self):
        project = create_project()
        page_id = project.pages[0]["id"]
        events = []
        project.add_listener(events.append)
        project.update_artwork(page_id, {"asset": "a.png"})
        project.update_artwork(page_id, {"asset": None})
        self.assertEqual([event.changed_fields for event in events], [((page_id, ("artwork",)),)] * 2)

    def test_invalid_mutation_does_not_modify_document(self):
        project = five_page_project()
        before = project.to_dict()
        with self.assertRaises(ProjectModelError):
            project.update_message(project.pages[1]["id"], {"format": "plain", "html": "x"})
        with self.assertRaises(ProjectModelError):
            project.update_prompt(project.pages[1]["id"], {"text": 3})
        self.assertEqual(project.to_dict(), before)


class ProjectMigrationTests(unittest.TestCase):
    def test_classic_mapping_and_resources(self):
        source = legacy_fixture()
        before = deepcopy(source)
        migrated = migrate_project(source, source_format="legacy_saved")
        project = migrated.project
        self.assertEqual(source, before)
        self.assertEqual([page["type"] for page in project.pages], list(CLASSIC_PAGE_TYPES))
        self.assertEqual(project.get_message("wall")["html"], source["message_html"])
        self.assertEqual(project.get_message("wall")["resources"], source["message_resources"])
        self.assertEqual(project.get_prompt("cover")["text"], source["prompt_writer_state"]["generated_prompts"]["cover"])
        self.assertEqual(project.to_dict()["sound"], source["sound"])
        self.assertEqual(project.to_dict()["publication"]["publication_custom"], "kept")

    def test_configurable_shared_wall_message_is_copied_independently(self):
        source = legacy_fixture(count=7)
        result = migrate_project(source, source_format="legacy_active")
        project = result.project
        self.assertEqual(len(project.pages), 7)
        walls = [page for page in project.pages if page["type"] == "letter"]
        self.assertEqual(len(walls), 4)
        message = project.get_message(walls[0]["id"])
        message["html"] = "changed"
        project.update_message(walls[0]["id"], message)
        self.assertEqual(project.get_message(walls[-1]["id"])["html"], source["message_html"])
        self.assertTrue(any("independent documents" in warning for warning in result.report.warnings))

    def test_current_is_idempotent_and_letter_remains_letter(self):
        original = five_page_project().to_dict()
        first = migrate_project(original)
        second = migrate_project(json.loads(json.dumps(first.project.to_dict())))
        self.assertFalse(first.report.converted)
        self.assertEqual(second.project.to_dict(), original)
        self.assertEqual(second.project.pages[1]["type"], "letter")

    def test_import_never_truncates_or_discards_unknown_metadata(self):
        source = legacy_fixture(count=13)
        source["future_metadata"] = {"inert": ["keep"]}
        result = migrate_project(source, source_format="legacy_saved")
        self.assertEqual(len(result.project.pages), 13)
        self.assertIn("page_count", [issue.code for issue in result.project.validate()])
        self.assertEqual(result.project.to_dict()["extensions"]["legacy_source"]["future_metadata"], {"inert": ["keep"]})

    def test_optional_wall_absent_and_orphan_message(self):
        source = legacy_fixture()
        source.pop("page_layout")
        source["images"].pop("wall")
        result = migrate_project(source, source_format="legacy_saved")
        self.assertEqual([page["type"] for page in result.project.pages], ["cover", "illustration", "end_page"])
        self.assertEqual(result.project.to_dict()["recovery"]["unassigned_message"]["html"], source["message_html"])
        self.assertFalse(result.project.render_snapshot()["ready"])

    def test_unknown_prompt_owner_is_not_guessed(self):
        source = legacy_fixture(count=5)
        source["page_layout"] = [page for page in source["page_layout"] if page["key"] != "wall"]
        source["prompt_writer_state"]["generated_prompts"] = {"Wall Prompt": "Do not guess"}
        result = migrate_project(source, source_format="legacy_saved")
        self.assertIsNone(result.project.get_prompt("page_1")["text"])
        self.assertEqual(result.project.to_dict()["recovery"]["unassigned_prompts"], {"Wall Prompt": "Do not guess"})

    def test_gif_reference_and_settings_are_retained(self):
        source = legacy_fixture()
        source["images"]["wall"] = {"source_file": "wall.gif", "asset_type": "animated_gif", "settings": {"speed_percent": 81, "loop_pause_ms": 999}}
        project = migrate_project(source, source_format="legacy_saved").project
        self.assertEqual(project.get_page("wall")["artwork"]["asset"], "gallery/pages/wall.gif")
        self.assertEqual(project.get_page("wall")["artwork"]["media_type"], "image/gif")
        self.assertEqual(project.get_page("wall")["artwork"]["animation"], source["images"]["wall"]["settings"])

    def test_explicit_message_target_and_duplicate_ids(self):
        source = legacy_fixture(count=5)
        source["message_page_id"] = "page_1"
        source["page_layout"][0]["id"] = "same"
        source["page_layout"][1]["id"] = "same"
        result = migrate_project(source, source_format="legacy_active")
        ids = [page["id"] for page in result.project.pages]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "same")
        self.assertEqual(result.project.get_message("wall")["html"], "")
        self.assertEqual(result.project.get_message("page_1")["html"], source["message_html"])
        again = migrate_project(result.project.to_dict())
        self.assertEqual(again.project.to_dict(), result.project.to_dict())

    def test_duplicate_original_message_reference_is_retained_without_guessing(self):
        source = legacy_fixture(count=5)
        for page in source["page_layout"]:
            if page["kind"] == "wall":
                page["id"] = "duplicate-wall-id"
        source["message_page_id"] = "duplicate-wall-id"
        result = migrate_project(source, source_format="legacy_saved")
        for page in result.project.pages:
            if page["type"] == "letter":
                self.assertEqual(page["message"]["html"], "")
        self.assertEqual(result.project.to_dict()["recovery"]["unassigned_message"]["html"], source["message_html"])

    def test_unsupported_versions_and_unknown_meanings_fail_without_mutating(self):
        for field, value in (("schema_version", "99.0"), ("schema_version", True), ("project_schema_version", 99)):
            source = legacy_fixture()
            source["metadata"][field] = value
            before = deepcopy(source)
            with self.assertRaises(UnsupportedProjectError):
                migrate_project(source, source_format="legacy_saved")
            self.assertEqual(source, before)
        current = five_page_project().to_dict()
        current["schema_version"] = 99
        with self.assertRaises(UnsupportedProjectError):
            migrate_project(current)
        source = legacy_fixture()
        source["page_layout"][0]["kind"] = "mystery"
        with self.assertRaises(UnsupportedProjectError):
            migrate_project(source, source_format="legacy_saved")
        with self.assertRaises(UnsupportedProjectError):
            migrate_project(legacy_fixture())


if __name__ == "__main__":
    unittest.main()
