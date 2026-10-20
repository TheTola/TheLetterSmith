from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import wave

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from project_consumers import project_legacy_views
from project_model import create_project, ProjectModelError
from project_repository import ProjectRepository, ProjectConflictError, ProjectRepositoryError
from project_save import ProjectSaveService, ProjectSaveError
from project_session import get_project_session, release_project_session
from project_state import ProjectStateController
from saved_letters import SavedLetterCatalog
from settings_store import SettingsStore
from test_project_model import canvas_layout, legacy_fixture


class SavedProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saved-")
        self.root = Path(self.temp.name)
        self.session = get_project_session(self.root)
        self.state = ProjectStateController(self.root)
        self.service = ProjectSaveService(self.root, self.state)
        self.catalog = SavedLetterCatalog(self.root)
        project = create_project(["cover", "letter", "illustration", "letter", "end_page"])
        project.update_project_data("metadata", {"recipient_title": "Same title", "title": "Same title", "recipient_name": "Test Recipient"})
        frames = [Image.new("RGB", (20, 30), color) for color in ("red", "blue")]
        frames[0].save(self.root / "animated.gif", save_all=True, append_images=frames[1:], duration=[90, 180], loop=2)
        Image.new("RGB", (20, 30), "green").save(self.root / "still.png")
        (self.root / "message_assets").mkdir()
        Image.new("RGB", (8, 8), "navy").save(self.root / "message_assets" / "embedded.png")
        with wave.open(str(self.root / "music.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(bytes(1600))
        for index, page in enumerate(project.pages):
            project.update_artwork(page["id"], {"asset": "animated.gif" if index == 1 else "still.png",
                                   "animation": {"start_delay_ms": 37, "play_count": 2} if index == 1 else {}})
            if page["type"] == "letter":
                label = "A" if index == 1 else "B"
                message = page["message"]
                message.update(html=f'<p><b>Letter {label} 雪</b><img src="message_assets/embedded.png"></p>',
                               resources=["message_assets/embedded.png"], layout=canvas_layout(), settings={"message_overlay_opacity": 20 * index})
                message["layout"]["box"]["y"] += index
                project.update_message(page["id"], message)
                project.update_prompt(page["id"], {"text": f"  Final {label} — four-page Wall\r\n雪\n  ", "stale": False})
        project.update_project_data("sound", {"mode": "playlist", "playlist": ["music", "music"], "volume": 12, "muted": True,
                                              "tracks": [{"track_id": "music", "asset": "music.wav"}]})
        project.update_project_data("prompt_settings", {"palette": "Unavailable historical palette"})
        project.update_project_data("extensions", {"custom_supported": {"value": "preserved"}})
        self.session.replace(project, asset_root=self.root)
        project_legacy_views(self.session)
        self.ids = [page["id"] for page in self.session.project.pages]

    def tearDown(self):
        release_project_session(self.root)
        self.temp.cleanup()

    def save_entry(self):
        destination, stored = self.service.save_editable()
        return self.catalog._entry(destination), stored

    def test_complete_five_page_snapshot_copy_and_reopen(self):
        entry, stored = self.save_entry()
        originals = stored.project.to_dict()
        self.assertEqual((entry.page_count, entry.letter_count), (5, 2))
        self.assertEqual(stored.project.to_dict()["sound"]["playlist"], ["music", "music"])
        gif = stored.asset_root / originals["pages"][1]["artwork"]["asset"]
        self.assertEqual(gif.read_bytes(), (self.root / "animated.gif").read_bytes())
        self.session.project.update_prompt(self.ids[1], {"text": "unsaved original"})
        copy_path, copied = self.service.duplicate_saved_project(entry, title="Same title")
        self.assertNotEqual(copied.project.project_id, stored.project.project_id)
        self.assertEqual([page["id"] for page in copied.project.pages], self.ids)
        self.assertEqual(copied.project.get_prompt(self.ids[1])["text"], originals["pages"][1]["prompt"]["text"])
        copied_entry = self.catalog._entry(copy_path)
        self.service.open_saved_project(copied_entry)
        self.session.project.update_prompt(self.ids[1], {"text": "copy edit"})
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.service.save_editable()
        self.service.open_saved_project(entry)
        restored = self.session.project.to_dict()
        self.assertEqual([page["id"] for page in restored["pages"]], self.ids)
        for index in (1, 3):
            self.assertEqual(restored["pages"][index]["message"], originals["pages"][index]["message"])
            self.assertEqual(restored["pages"][index]["prompt"]["text"], originals["pages"][index]["prompt"]["text"])
        self.assertEqual(restored["sound"]["playlist"], ["music", "music"])
        self.assertEqual(restored["prompt_settings"], originals["prompt_settings"])
        self.assertEqual(restored["extensions"]["custom_supported"], {"value": "preserved"})

    def test_save_failure_conflict_and_edit_during_save(self):
        entry, first = self.save_entry()
        capture = self.service.capture_editable_save()
        self.session.project.update_prompt(self.ids[3], {"text": "newer edit"})
        stored = self.service.commit_editable_save(capture)
        self.assertFalse(self.service.complete_editable_save(capture, stored))
        self.assertTrue(self.service.has_unsaved_editable_changes())
        self.assertNotEqual(stored.project.get_prompt(self.ids[3])["text"], "newer edit")
        stale = self.service.capture_editable_save()
        repository = ProjectRepository(entry.path / ".lettersmith-project")
        external = repository.save(stored.project, asset_root=stored.asset_root, expected_revision=stored.revision)
        with self.assertRaises(ProjectConflictError):
            self.service.commit_editable_save(stale)
        self.assertEqual(repository.load().revision, external.revision)
        self.assertEqual(self.session.project.get_prompt(self.ids[3])["text"], "newer edit")
        capture = replace(stale, expected_revision=external.revision)
        with mock.patch("project_repository.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(ProjectRepositoryError):
                self.service.commit_editable_save(capture)
        self.assertEqual(repository.load().revision, external.revision)

    def test_old_completion_cannot_follow_a_b_a(self):
        entry, _ = self.save_entry()
        capture = self.service.capture_editable_save()
        copy_path, _ = self.service.duplicate_saved_project(entry)
        self.service.open_saved_project(self.catalog._entry(copy_path))
        self.service.open_saved_project(entry)
        stored = self.service.commit_editable_save(capture)
        self.assertFalse(self.service.complete_editable_save(capture, stored))
        self.assertNotEqual(capture.session_id, self.session.session_id)

    def test_copy_detaches_publication_and_keeps_current_active(self):
        self.session.project.update_project_data("publication", {"published_page_url": "https://example.test/a", "published_public_path": "original", "publication_verified": True})
        entry, _ = self.save_entry()
        identity = self.session.session_id
        destination, copied = self.service.save_editable(copy=True)
        self.assertEqual(self.session.session_id, identity)
        self.assertEqual(copied.project.to_dict()["publication"], {})
        self.assertNotEqual(destination, entry.path)
        self.assertEqual(self.session.project.to_dict()["publication"]["published_public_path"], "original")

    def test_invalid_candidate_and_missing_media_leave_draft_safe(self):
        entry, stored = self.save_entry()
        before = self.session.project.to_dict()
        (stored.asset_root / stored.project.pages[1]["artwork"]["asset"]).unlink()
        candidate = self.service.read_saved_project(entry)
        self.assertTrue(candidate.project.to_dict()["recovery"]["missing_resources"])
        self.service.open_saved_project(entry, loaded=candidate)
        self.assertEqual(self.session.project.pages[1]["artwork"]["asset"], stored.project.pages[1]["artwork"]["asset"])
        active = self.session.project.to_dict()
        payload = json.loads((stored.asset_root / "project.json").read_text(encoding="utf-8"))
        payload["schema_version"] = 900
        (stored.asset_root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(Exception):
            self.service.open_saved_project(entry)
        self.assertEqual(self.session.project.to_dict(), active)
        invalid = self.catalog.list_entries(force_refresh=True)[0]
        self.assertFalse(invalid.valid)
        self.assertTrue(invalid.warning)

    def test_missing_saved_resources_do_not_use_previous_workspace_names(self):
        from page_artwork import PageArtworkController
        from forge_render import capture_render, validate_render
        from sound_model import resolve_project_tracks, load_library, resolve_track_path
        original, _ = self.save_entry()
        self.service.open_saved_project(original)
        library = load_library(self.root)
        original_track = library["music"]
        source_audio = resolve_track_path(self.root, original_track)
        original_audio = source_audio.read_bytes()
        source = self.root / "missing-source"
        source.mkdir()
        missing = self.session.project.independent_copy(title="Missing resource draft")
        destination = self.service.editable_root / missing.project_id
        stored = ProjectRepository(destination / ".lettersmith-project").save(missing, asset_root=source)
        self.service.open_saved_project(self.catalog._entry(destination))
        self.assertIsNone(PageArtworkController(self.session).resolve_asset(self.ids[0]))
        from Editor import Editor
        editor = Editor(self.session.project.get_message(self.ids[1])["html"], save_callback=lambda _: True,
                        resource_roots=(self.root, self.session.asset_root),
                        missing_resources=self.session.project.to_dict()["recovery"]["missing_resources"])
        try:
            self.assertTrue(editor.editor.loadResource(QtGui.QTextDocument.ImageResource,
                            QtCore.QUrl("message_assets/embedded.png")).isNull())
        finally:
            editor.close()
        self.assertTrue(any(issue.code == "missing_artwork" for issue in validate_render(capture_render(self.session, "preview"))))
        self.assertFalse(resolve_project_tracks(self.root)[1])
        self.assertEqual(source_audio.read_bytes(), original_audio)
        self.assertIn(original_track.content_hash[:24], load_library(self.root))
        _, saved = self.service.save_editable()
        self.assertEqual(set(saved.project.to_dict()["recovery"]["missing_resources"]),
                         set(stored.project.to_dict()["recovery"]["missing_resources"]))

    def test_rename_same_titles_and_recoverable_delete_do_not_touch_sources(self):
        entry, original = self.save_entry()
        other_path, other = self.service.duplicate_saved_project(entry, title=entry.title)
        self.service.rename_saved_project(entry, "../Same title — 雪")
        renamed = self.catalog._entry(entry.path)
        self.assertEqual(renamed.project_id, entry.project_id)
        self.assertEqual(renamed.title, "../Same title — 雪")
        self.catalog.list_entries(force_refresh=True)
        self.catalog.delete(renamed)
        self.assertFalse(entry.path.exists())
        entries = self.catalog.list_entries(force_refresh=True)
        recovered = next(item for item in entries if item.recovery)
        self.assertEqual(self.service.read_saved_project(recovered).project.project_id, entry.project_id)
        self.assertEqual(ProjectRepository(other_path / ".lettersmith-project").load().project.project_id, other.project.project_id)
        self.assertTrue((self.root / "animated.gif").is_file())

    def test_repair_keeps_original_and_replaced_embedded_resources(self):
        message = self.session.project.get_message(self.ids[1])
        message["html"] = "Recovered <b>message</b>"
        self.session.project.update_project_data("recovery", {"unassigned_message": message,
            "unassigned_prompts": {"Wall": " exact old Wall\r\n雪 "},
            "unresolved_associations": [{"content": "message"}, {"content": "prompts", "keys": ["Wall"]}]})
        with self.assertRaises(ProjectModelError):
            self.session.project.assign_recovered("message", "", self.ids[2])
        with self.assertRaises(ProjectModelError):
            self.session.project.assign_recovered("message", "", self.ids[1])
        self.session.project.assign_recovered("message", "", self.ids[1], replace=True)
        self.session.project.assign_recovered("prompt", "Wall", self.ids[2])
        entry, stored = self.save_entry()
        recovery = stored.project.to_dict()["recovery"]
        self.assertEqual(recovery["unresolved_associations"], [])
        self.assertEqual(recovery["unassigned_message"], message)
        self.assertIn("Letter A", recovery["assigned_content"][0]["previous"]["html"])
        self.assertTrue((stored.asset_root / "message_assets/embedded.png").exists())

    def test_empty_and_no_letter_drafts_are_listed_and_reopen(self):
        for kinds in ([], ["illustration", "end_page"]):
            self.session.replace(create_project(kinds), asset_root=self.root)
            project_legacy_views(self.session) if kinds else None
            # Replacing through the canonical restoration boundary clears old views.
            from project_repository import StoredProject
            from saved_letters import SavedLetterRestorer
            SavedLetterRestorer(self.root).restore_canonical(StoredProject(self.session.project, "", self.root, {}))
            entry, stored = self.save_entry()
            self.assertEqual((entry.page_count, entry.letter_count), (len(kinds), 0))
            self.service.open_saved_project(entry)
            self.assertEqual(len(self.session.project.pages), len(kinds))

    def test_example_open_creates_editable_independent_session(self):
        entry, stored = self.save_entry()
        pointer = (entry.path / ".lettersmith-project/current.json").read_bytes()
        self.service.open_saved_project(replace(entry, example=True))
        self.assertNotEqual(self.session.project.project_id, entry.project_id)
        destination, copied = self.service.save_editable()
        self.assertNotEqual(destination, entry.path)
        self.assertEqual((entry.path / ".lettersmith-project/current.json").read_bytes(), pointer)
        self.assertFalse(SettingsStore(self.root).get("protected_project_kind"))

    def test_stale_missing_and_failed_delete_preserve_the_saved_version(self):
        entry, stored = self.save_entry()
        self.service.rename_saved_project(entry, "Latest title")
        with self.assertRaisesRegex(ProjectSaveError, "saved version changed"):
            self.service.duplicate_saved_project(entry)
        latest = self.catalog._entry(entry.path)
        pointer = (entry.path / ".lettersmith-project/current.json").read_bytes()
        with mock.patch("project_save.os.replace", side_effect=PermissionError("held by another process")):
            with self.assertRaises(PermissionError):
                self.service.trash_saved_project(latest)
        self.assertEqual((entry.path / ".lettersmith-project/current.json").read_bytes(), pointer)
        self.assertEqual(self.service.read_saved_project(latest).project.project_id, stored.project.project_id)
        self.catalog.list_entries(force_refresh=True)
        moved = entry.path.with_name(entry.path.name + "-external")
        from transactional_io import _with_transient_retry
        _with_transient_retry(lambda: entry.path.rename(moved))
        with self.assertRaisesRegex(ProjectSaveError, "location is missing"):
            self.service.read_saved_project(latest)
        missing = next(item for item in self.catalog.list_entries(force_refresh=True) if item.path == entry.path)
        self.assertFalse(missing.valid)
        self.assertIn("missing", missing.warning)

    def test_publication_status_uses_saved_content_without_network_or_activation(self):
        from forge_render import capture_render
        from project_session import ProjectSession
        entry, stored = self.save_entry()
        isolated = ProjectSession(stored.project, project_root=self.root, asset_root=stored.asset_root)
        try:
            fingerprint = capture_render(isolated).source_fingerprint
        finally:
            isolated.close()
        self.session.project.update_project_data("publication", {
            "published_page_url": "https://example.test/confirmed", "publication_verified": True,
            "published_source_fingerprint": fingerprint})
        destination, saved = self.service.save_editable()
        self.assertIn("matches saved content", self.catalog._entry(destination).saved_publication_state)
        self.session.project.update_prompt(self.ids[1], {"text": "private prompt edit"})
        self.service.save_editable()
        self.assertIn("matches saved content", self.catalog._entry(destination).saved_publication_state)
        self.service.rename_saved_project(self.catalog._entry(destination), "Changed visible title")
        self.assertIn("not published", self.catalog._entry(destination).saved_publication_state)

    def test_thumbnail_failure_and_stale_result_do_not_invalidate_saved_project(self):
        from Forge_Tab import SavedLetterCard
        entry, _ = self.save_entry()
        requests = []
        card = SavedLetterCard(entry, cover_requester=lambda *args: requests.append(args))
        self.addCleanup(card.deleteLater)
        _, _, old_generation, old_path = requests[-1]
        self.session.project.update_artwork(self.ids[0], {"asset": "animated.gif"})
        destination, _ = self.service.save_editable()
        latest = self.catalog._entry(destination)
        card.update_entry(latest)
        self.assertFalse(card.apply_cover_result(old_generation, old_path, None, QtGui.QPixmap(8, 8)))
        _, _, generation, path = requests[-1]
        self.assertTrue(card.apply_cover_result(generation, path, None, QtGui.QPixmap()))
        self.assertEqual(card.cover.text(), "Thumbnail unavailable")
        self.assertTrue(self.catalog._entry(destination).valid)
        card.cancel_cover_request()
        self.assertFalse(card.apply_cover_result(generation, path, None, QtGui.QPixmap(8, 8)))

    def test_derived_forge_output_is_not_an_editable_library_entry(self):
        from saved_letters import update_saved_metadata, update_saved_publication_metadata, record_saved_letter_activity
        entry, _ = self.save_entry()
        output = self.catalog.play_root / "derived-preview"
        output.mkdir()
        (output / "index.html").write_text("<title>Preview</title>", encoding="utf-8")
        (output / "lettersmith-display.json").write_text('{"version":1,"output_mode":"preview"}', encoding="utf-8")
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        active = self.session.project.to_dict()
        update_saved_metadata(output, self.root, object())
        update_saved_publication_metadata(output, self.root)
        record_saved_letter_activity(output)
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
        self.assertEqual(self.session.project.to_dict(), active)
        # Older compatibility callbacks may already have created an editable
        # repository in a derived folder. Retain it, but do not list it as a save.
        ProjectRepository(output / ".lettersmith-project").save(self.session.project, asset_root=self.root)
        self.assertEqual([item.path for item in self.catalog.list_entries(force_refresh=True)], [entry.path])
        self.assertEqual([item.path for item in self.catalog.refresh_entry(output)], [entry.path])

    def test_legacy_classic_variable_and_over_limit_save_copy_keep_originals(self):
        from config import PLAY_METADATA_FILE
        from page_layout import LAYOUT_FILE
        from saved_letters import PROMPT_WRITER_STATE_FILE
        for count in (4, 6, 12):
            with self.subTest(count=count):
                fixture = legacy_fixture(count=count)
                source = self.catalog.play_root / f"legacy-{count}"
                pages = source / "gallery/pages"
                pages.mkdir(parents=True)
                (pages / LAYOUT_FILE).write_text(json.dumps({"version": 1, "pages": fixture["page_layout"]}), encoding="utf-8")
                (source / PLAY_METADATA_FILE).write_text(json.dumps(fixture["metadata"]), encoding="utf-8")
                (source / PROMPT_WRITER_STATE_FILE).write_text(json.dumps(fixture["prompt_writer_state"]), encoding="utf-8")
                message = source / "gallery/message/message.html"
                message.parent.mkdir()
                message.write_text(fixture["message_html"], encoding="utf-8")
                for page in fixture["page_layout"]:
                    Image.new("RGB", (6, 9), "purple").save(pages / (page["key"] + ".png"))
                (pages / "lettersmith-images.json").write_text(json.dumps({"schema_version": 1, "slots": fixture["images"]}), encoding="utf-8")
                originals = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
                entry = self.catalog._entry(source)
                candidate = self.service.read_saved_project(entry)
                self.assertEqual(len(candidate.project.pages), count)
                self.assertEqual([page["type"] for page in candidate.project.pages[:3]], ["cover", "illustration", "letter"])
                self.service.open_saved_project(entry)
                destination, saved = self.service.save_editable()
                self.assertTrue(saved.migration_report)
                self.assertEqual(len(saved.project.pages), count)
                self.assertEqual(saved.project.pages[0]["prompt"]["text"], fixture["prompt_writer_state"]["generated_prompts"]["cover"])
                self.assertEqual({path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}, originals)
                self.service.open_saved_project(self.catalog._entry(destination))
                self.assertEqual(len(self.session.project.pages), count)
                self.assertEqual(len({page["id"] for page in self.session.project.pages}), count)

    def test_bundled_example_and_permanent_resources_survive_edit_save_and_reset(self):
        import hashlib
        import project_paths
        from project_session import reset_project_session
        previous = project_paths._APPLICATION_PATHS
        paths = replace(project_paths.ApplicationPaths.for_project(self.root), resource_root=Path(__file__).resolve().parents[1])
        project_paths.configure_application_paths(paths)
        try:
            catalog = SavedLetterCatalog(self.root)
            entries = catalog.list_entries(force_refresh=True)
            example = next(entry for entry in entries if entry.example and entry.valid)
            def fingerprints():
                return {str(path.relative_to(example.path)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in example.path.rglob("*") if path.is_file()}
            original = fingerprints()
            self.service.open_saved_project(example)
            self.session.project.rename_page(self.session.project.pages[0]["id"], "User example edit")
            destination, saved = self.service.save_editable()
            self.assertFalse(destination.is_relative_to(paths.examples_root))
            palette = paths.custom_palette_root / "preserved-test-palette.json"
            palette.parent.mkdir(parents=True, exist_ok=True)
            palette.write_text('{"name": "Keep"}', encoding="utf-8")
            reset_project_session(self.root)
            self.assertEqual(fingerprints(), original)
            self.assertTrue(palette.is_file())
            self.assertEqual(ProjectRepository(destination / ".lettersmith-project").load().project.project_id, saved.project.project_id)
        finally:
            project_paths.configure_application_paths(previous)


class SavedProjectUiTests(unittest.TestCase):
    setUpClass = SavedProjectTests.__dict__["setUpClass"]

    def setUp(self):
        SavedProjectTests.setUp(self)
        import project_paths
        from Nexus import Nexus
        self.previous_paths = project_paths._APPLICATION_PATHS
        paths = replace(project_paths.ApplicationPaths.for_project(self.root),
                        resource_root=Path(__file__).resolve().parents[1], profile_root=self.root)
        project_paths.configure_application_paths(paths)
        paths.initialize()
        self.state.initialize()
        self.state.establish_project("Test Recipient", project_id=self.session.project.project_id)
        self.session.save()
        self.patches = [mock.patch("Forge_Tab.ForgeTab._validate_github_account_async"),
                        mock.patch("Nexus.Nexus._schedule_curtain_preparation"),
                        mock.patch("ui_sounds.UiSoundPlayer.play", return_value=False)]
        for patch in self.patches:
            patch.start()
        self.window = Nexus(self.root)
        from config import CONTROL_FILES, USER_CONTROLS_DIR
        for name in CONTROL_FILES:
            path = self.root / USER_CONTROLS_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 48), "gray").save(path)
        self.window.resize(1200, 850)
        self.window.show()
        self.app.processEvents()
        self.forge = self.window.forge_tab
        self.ui = self.forge.saved_projects
        self.service = self.ui.service

    def tearDown(self):
        import project_paths
        try:
            self.wait(lambda: not self.forge._busy and self.window._autosave_active_revision is None, seconds=180)
            self.assertTrue(self.window.shutdown())
            self.window.hide()
            self.window.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            self.app.processEvents()
        finally:
            for patch in reversed(self.patches):
                patch.stop()
            project_paths.configure_application_paths(self.previous_paths)
            SavedProjectTests.tearDown(self)

    def wait(self, condition, seconds=60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return
            QtTest.QTest.qWait(20)
        import faulthandler
        faulthandler.dump_traceback()
        notes = [label.text() for dialog, *_ in self.ui.previews for label in dialog.findChildren(QtWidgets.QLabel)]
        self.fail(self.ui.details.text() + "\n" + repr(notes))

    def test_last_keystrokes_detached_prompt_save_open_and_saved_preview(self):
        self.assertTrue(self.window.open_message_page(self.ids[1]))
        editor = self.window.message_tab.messages_workspace.editor.editor
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, " Final A")
        self.assertTrue(self.window.open_message_page(self.ids[3]))
        editor = self.window.message_tab.messages_workspace.editor.editor
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, " Final B")
        self.window.open_prompt_writer(page_id=self.ids[3])
        panel = self.window._prompt_writer_win
        # The restored Prompt Writer occupies the application workspace. Its
        # pending edits must still join Save even though Messages is not visible.
        self.assertIs(self.window.application_stack.currentWidget(), panel)
        self.assertTrue(panel.isVisibleTo(self.window))
        self.assertEqual(panel._view_page_id, self.ids[3])
        prompt = panel._view_spec.preview_widget
        prompt.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(prompt, " Last prompt key")
        exact = prompt.toPlainText()
        self.assertTrue(self.ui.save())
        self.wait(lambda: not self.forge._busy)
        entry = self.forge._selected_saved_letter
        self.assertIsNotNone(entry)
        loaded = self.service.read_saved_project(entry)
        self.assertIn("Final A", loaded.project.get_message(self.ids[1])["html"])
        self.assertIn("Final B", loaded.project.get_message(self.ids[3])["html"])
        self.assertEqual(loaded.project.get_prompt(self.ids[3])["text"], exact)
        self.assertFalse(self.window._prompt_writer_browser)
        self.ui.duplicate_selected()
        self.wait(lambda: not self.forge._busy)
        copied = self.forge._selected_saved_letter
        self.assertNotEqual(copied.project_id, entry.project_id)
        preview = self.window.message_tab.messages_workspace.shared_preview
        release = preview.release
        def release_during_open(*args, **kwargs):
            result = release(*args, **kwargs)
            self.assertTrue(self.forge._busy, "Worker completion must not unlock an unfinished activation")
            return result
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard), \
             mock.patch.object(preview, "release", side_effect=release_during_open):
            self.assertTrue(self.ui.open_selected())
            self.wait(lambda: not self.forge._busy, seconds=180)
        self.assertEqual(self.session.project.project_id, copied.project_id, self.window._status.currentMessage())
        self.forge._select_saved_letter(entry)
        before = self.session.project.to_dict()
        self.ui.preview_selected()
        self.wait(lambda: bool(self.ui.previews))
        dialog, preview, _, _ = self.ui.previews[0]
        self.wait(lambda: preview.viewed_page_id == self.ids[0], seconds=90)
        self.assertEqual(self.session.project.to_dict(), before)
        self.assertTrue(preview.view.page().isAudioMuted())
        dialog.close()
        self.wait(lambda: not self.ui.previews)

    def test_cancel_delete_failure_and_keyboard_card_selection(self):
        self.assertTrue(self.ui.save())
        self.wait(lambda: not self.forge._busy)
        entry = self.forge._selected_saved_letter
        self.forge.refresh_saved_letters(force_reconcile=True)
        self.wait(lambda: not self.forge._catalog_reconcile_active)
        self.forge.show_saved_letters()
        self.wait(lambda: not self.forge._catalog_reconcile_active)
        card = next(card for card in self.forge._saved_cards if card.entry.path == entry.path)
        card.setFocus()
        with mock.patch.object(self.ui, "open_selected", return_value=False) as activated:
            QtTest.QTest.keyClick(card, QtCore.Qt.Key_Space)
            activated.assert_called_once()
        self.assertEqual(self.forge._selected_saved_letter.path, entry.path)
        self.assertIn("letter pages", card.title_label.text())
        before = (entry.path / ".lettersmith-project/current.json").read_bytes()
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.assertFalse(self.ui.delete_selected())
        self.assertEqual((entry.path / ".lettersmith-project/current.json").read_bytes(), before)
        with mock.patch.object(self.service, "commit_editable_save", side_effect=OSError("synthetic write failure")):
            self.assertTrue(self.ui.save())
            self.wait(lambda: not self.forge._busy)
        self.assertIn("synthetic write failure", self.ui.details.text())
        self.assertEqual((entry.path / ".lettersmith-project/current.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
