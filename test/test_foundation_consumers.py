from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtWidgets

from page_controls import page_controller
from page_layout import load_pages
from project_consumers import (ConsumerCompatibilityError, consumer_issue, project_legacy_views,
                               project_sound_snapshot, project_sound_views, require_consumer,
                               update_project_sound, prepare_consumer)
from project_model import create_project
from project_session import get_project_session, release_project_session


class FoundationConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.session = get_project_session(self.root)

    def tearDown(self):
        release_project_session(self.root)
        self.temp.cleanup()

    def test_five_page_projection_keeps_order_identity_and_blocks_distinct_messages(self):
        project = create_project(["cover", "letter", "illustration", "letter", "end_page"])
        letters = [page for page in project.pages if page["type"] == "letter"]
        for page, text in zip(letters, ("<b>Message A</b>", "<i>Message B</i>")):
            project.update_message(page["id"], {**page["message"], "html": text})
            project.update_prompt(page["id"], {"text": "  Prompt " + text + "\r\n", "stale": False})
        self.session.replace(project, asset_root=self.root, storage_revision=self.session.storage_revision)
        project_legacy_views(self.session)
        before = {page["id"]: page for page in self.session.project.pages}
        self.assertEqual([page.kind for page in load_pages(self.root / "gallery/user/pages")], ["cover", "wall", "letter", "wall", "back"])
        self.assertEqual(len({page["recovery"]["legacy_key"] for page in before.values()}), 5)
        controller = page_controller(self.root)
        controller.reload()
        key = before[letters[0]["id"]]["recovery"]["legacy_key"]
        controller.select(key)
        self.assertEqual(self.session.selected_page_id, letters[0]["id"])
        controller.update_pages(tuple(reversed(controller.pages)))
        self.assertEqual([page["id"] for page in self.session.project.pages], list(reversed(before)))
        for page in self.session.project.pages:
            self.assertEqual(page["message"], before[page["id"]]["message"])
            self.assertEqual(page["prompt"]["text"], before[page["id"]]["prompt"]["text"])
        with self.assertRaisesRegex(ConsumerCompatibilityError, "independent Letter messages"):
            require_consumer(self.root, "output")
        self.assertIn("exactly one Letter", consumer_issue(self.root, "message"))

    def test_raw_gif_bytes_and_playback_survive_projection(self):
        source = self.root / "art.gif"
        frames = [Image.new("RGB", (8, 12), color) for color in ("red", "blue")]
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=110, loop=2)
        original = source.read_bytes()
        project = create_project(["illustration", "end_page"])
        page_id = project.pages[0]["id"]
        project.update_artwork(page_id, {"asset": "art.gif", "media_type": "image/gif", "animation": {"start_delay_ms": 321}})
        self.session.replace(project, asset_root=self.root)
        project_legacy_views(self.session)
        key = self.session.project.get_page(page_id)["recovery"]["legacy_key"]
        self.assertEqual((self.root / f"gallery/user/pages/{key}.gif").read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.session.project.get_page(page_id)["artwork"]["animation"], {"start_delay_ms": 321})
        projected = self.root / f"gallery/user/pages/{key}.gif"
        media_revision = projected.stat().st_mtime_ns
        self.session.project.update_prompt(page_id, {"directions": "New directions"})
        project_legacy_views(self.session)
        self.assertEqual(projected.stat().st_mtime_ns, media_revision)

    def test_partial_projection_failure_restores_previous_views(self):
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        layout = self.root / "gallery/user/pages/lettersmith-pages.json"
        prompt = self.root / "prompt_writer_state.json"
        message = self.root / "gallery/user/message/message.html"
        before = {path: path.read_bytes() for path in (layout, prompt, message)}
        page = next(page for page in self.session.project.pages if page["message"] is not None)
        self.session.project.update_message(page["id"], {**page["message"], "html": "new message"})
        self.session.project.update_prompt(page["id"], {"text": "new prompt"})
        import project_consumers
        real_write = project_consumers.atomic_write_json
        def fail_prompt(path, value):
            if Path(path) == prompt:
                raise PermissionError("synthetic write failure")
            return real_write(path, value)
        with mock.patch.object(project_consumers, "atomic_write_json", side_effect=fail_prompt):
            with self.assertRaises(PermissionError):
                project_legacy_views(self.session)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.session.project.get_page(page["id"])["message"]["html"], "new message")

    def test_no_letter_and_repeated_types_are_supported_without_global_message(self):
        self.session.replace(create_project(["end_page", "cover", "cover"]), asset_root=self.root)
        project_legacy_views(self.session)
        require_consumer(self.root, "output")
        self.assertEqual([page.kind for page in load_pages(self.root / "gallery/user/pages")], ["back", "cover", "cover"])
        self.assertEqual((self.root / "gallery/user/message/message.html").read_text(), "")

    def test_changed_shared_settings_or_sound_cannot_render_old_cached_values(self):
        self.session.replace(create_project(["cover", "end_page"]), asset_root=self.root)
        project_legacy_views(self.session)
        self.session.project.update_shared_settings({"message_overlay_opacity": 12})
        self.assertIn("Shared project settings", consumer_issue(self.root, "output"))
        self.session.replace(create_project(["cover", "end_page"]), asset_root=self.root)
        project_legacy_views(self.session)
        self.session.project.update_project_data("sound", {"mode": "playlist", "playlist": ["new-track"]})
        self.assertIn("sound assignment changed", consumer_issue(self.root, "output"))

    def test_sound_projection_keeps_other_content_and_session_identity(self):
        from settings_store import SettingsStore
        from sound_model import current_music_path, import_runtime_track, load_project_state
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        page = self.session.project.pages[0]
        self.session.project.update_prompt(page["id"], {"text": "keep this", "stale": False})
        SettingsStore(self.root).update_fields({"ui_last_folder_music": "unchanged"})
        source = self.root / "selected.mp3"
        source.write_bytes(b"selected audio")
        track = import_runtime_track(self.root, source)
        before = self.session.project.to_dict()
        identity = self.session.session_id
        update_project_sound(self.session, {"single_track_id": track.track_id, "volume": 67, "muted": True})
        after = self.session.project.to_dict()
        self.assertEqual(after["pages"], before["pages"])
        self.assertEqual(after["shared_settings"], before["shared_settings"])
        self.assertEqual(after["prompt_settings"], before["prompt_settings"])
        self.assertEqual(self.session.session_id, identity)
        self.assertEqual(current_music_path(self.root).read_bytes(), source.read_bytes())
        self.assertEqual(load_project_state(self.root).single_track_id, track.track_id)
        self.assertEqual(SettingsStore(self.root).get("music_volume"), 67)
        self.assertIs(SettingsStore(self.root).get("music_muted"), True)
        self.assertEqual(SettingsStore(self.root).get("ui_last_folder_music"), "unchanged")
        self.assertEqual(consumer_issue(self.root, "output"), "")
        self.session.synchronize_workspace()
        self.assertEqual(self.session.project.to_dict()["sound"], after["sound"])
        self.assertEqual(self.session.project.to_dict()["shared_settings"], before["shared_settings"])

    def test_direct_canonical_sound_edit_is_projected_before_output(self):
        from sound_model import current_music_path, import_runtime_track
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        source = self.root / "selected.mp3"
        source.write_bytes(b"direct model edit")
        track = import_runtime_track(self.root, source)
        self.session.project.update_project_data("sound", {"single_track_id": track.track_id, "volume": 18})
        self.assertIn("sound assignment changed", consumer_issue(self.root, "output"))
        prepare_consumer(self.root, "output")
        self.assertEqual(current_music_path(self.root).read_bytes(), source.read_bytes())
        self.assertEqual(consumer_issue(self.root, "output"), "")

    def test_missing_sound_order_and_metadata_survive_projection_and_save(self):
        from sound_model import load_project_state
        self.session.replace(create_project(), asset_root=self.root)
        self.session.project.update_project_data("sound", {
            "mode": "playlist", "playlist": ["missing-a", "missing-b"], "selected_track_id": "missing-b",
            "tracks": [{"track_id": "missing-a", "asset": "audio/missing.mp3", "display_title": "Kept"},
                       {"track_id": "missing-b", "missing": True}], "volume": 21, "muted": True,
        })
        project_sound_views(self.session)
        update_project_sound(self.session, {"playlist": ["missing-b", "missing-a"], "volume": 22})
        self.assertEqual(load_project_state(self.root).playlist, ["missing-b", "missing-a"])
        stored = self.session.save()
        sound = stored.project.to_dict()["sound"]
        self.assertEqual(sound["playlist"], ["missing-b", "missing-a"])
        self.assertEqual(sound["tracks"][1]["display_title"], "Kept")
        self.assertEqual(sound["tracks"][1]["asset"], "audio/missing.mp3")
        self.assertEqual(sound["volume"], 22)
        self.assertTrue(sound["muted"])

    def test_sound_projection_failure_rolls_back_views_and_model(self):
        from sound_model import current_music_path, project_sound_path, import_runtime_track
        from settings_store import SettingsStore
        self.session.replace(create_project(), asset_root=self.root)
        source = self.root / "selected.mp3"
        source.write_bytes(b"current audio")
        track = import_runtime_track(self.root, source)
        update_project_sound(self.session, {"single_track_id": track.track_id, "volume": 29})
        before = self.session.project.to_dict()
        paths = (current_music_path(self.root), project_sound_path(self.root), SettingsStore(self.root).path)
        files = {path: path.read_bytes() for path in paths}
        with mock.patch.object(SettingsStore, "update_fields", side_effect=PermissionError("synthetic failure")):
            with self.assertRaises(PermissionError):
                update_project_sound(self.session, {"single_track_id": "missing", "volume": 87})
        self.assertEqual(self.session.project.to_dict(), before)
        self.assertEqual({path: path.read_bytes() for path in paths}, files)

    def test_volume_edit_preserves_track_metadata_and_blank_draft_clears_audio(self):
        from config import STARTING_VOLUME
        from sound_model import current_music_path, import_runtime_track, load_project_state
        self.session.replace(create_project(), asset_root=self.root)
        source = self.root / "selected.mp3"
        source.write_bytes(b"current audio")
        track = import_runtime_track(self.root, source)
        update_project_sound(self.session, {"single_track_id": track.track_id})
        canonical = self.session.project.to_dict()["sound"]
        canonical["tracks"][0]["custom_note"] = "preserved"
        self.session.project.update_project_data("sound", canonical)
        update_project_sound(self.session, {"volume": 0, "muted": True})
        self.assertEqual(self.session.project.to_dict()["sound"]["tracks"][0]["custom_note"], "preserved")
        with (mock.patch("sound_model.load_library", side_effect=AssertionError("volume must not scan the archive")),
              mock.patch("saved_letters._SoundArchiveSnapshot.capture", side_effect=AssertionError("volume must not snapshot the archive"))):
            update_project_sound(self.session, {"volume": 32})
            project_sound_views(self.session)
        self.session.replace(create_project([]), asset_root=self.root)
        project_sound_views(self.session)
        self.assertFalse(current_music_path(self.root).exists())
        self.assertEqual(load_project_state(self.root).ordered_track_ids(), [])
        self.assertEqual(project_sound_snapshot(self.session)["volume"], STARTING_VOLUME)
        self.assertFalse(project_sound_snapshot(self.session)["muted"])

    def test_portable_stock_alias_keeps_authored_identity_after_reload(self):
        from sound_model import load_library, load_project_state, current_music_path
        import json
        stock = self.root / "resources/stock"
        stock.mkdir(parents=True)
        (stock / "music.mp3").write_bytes(b"stock audio")
        (stock / "stock_manifest.json").write_text(json.dumps({"music": [{"filename": "music.mp3", "title": "Stock"}]}))
        audio = self.root / "audio"
        audio.mkdir()
        (audio / "portable.mp3").write_bytes(b"stock audio")
        self.session.replace(create_project([]), asset_root=self.root)
        self.session.project.update_project_data("sound", {
            "single_track_id": "authored-stock", "tracks": [{"track_id": "authored-stock", "asset": "audio/portable.mp3"}]})
        project_sound_views(self.session)
        self.assertIn("authored-stock", load_library(self.root))
        self.assertEqual(load_project_state(self.root).single_track_id, "authored-stock")
        update_project_sound(self.session, {"volume": 12})
        self.assertEqual(project_sound_snapshot(self.session)["single_track_id"], "authored-stock")
        self.assertEqual(current_music_path(self.root).read_bytes(), b"stock audio")

    def test_sound_edits_reject_restoration_and_closed_session(self):
        with self.session.restoring():
            with self.assertRaisesRegex(RuntimeError, "restoration"):
                update_project_sound(self.session, {"volume": 12})
        self.session.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            update_project_sound(self.session, {"volume": 12})

    def test_portable_asset_replaces_cached_audio_for_the_same_authored_id(self):
        import hashlib
        from sound_model import current_music_path, load_library
        self.session.replace(create_project([]), asset_root=self.root)
        audio = self.root / "audio"
        audio.mkdir()
        (audio / "first.mp3").write_bytes(b"first project audio")
        (audio / "second.mp3").write_bytes(b"second project audio")
        self.session.project.update_project_data("sound", {
            "single_track_id": "shared-authored-id",
            "tracks": [{"track_id": "shared-authored-id", "asset": "audio/first.mp3"}]})
        project_sound_views(self.session)
        self.session.project.update_project_data("sound", {
            "tracks": [{"track_id": "shared-authored-id", "asset": "audio/second.mp3"}]})
        project_sound_views(self.session)
        self.assertEqual(current_music_path(self.root).read_bytes(), b"second project audio")
        records = load_library(self.root)
        self.assertEqual(records["shared-authored-id"].content_hash, hashlib.sha256(b"second project audio").hexdigest())
        self.assertIn(hashlib.sha256(b"first project audio").hexdigest()[:24], records)
        self.session.project.update_project_data("sound", {
            "tracks": [{"track_id": "shared-authored-id", "asset": "audio/unavailable.mp3", "sha256": "0" * 64}]})
        project_sound_views(self.session)
        self.assertFalse(current_music_path(self.root).exists())
        self.assertEqual(project_sound_snapshot(self.session)["single_track_id"], "shared-authored-id")

    def test_direct_canonical_assignment_save_contains_portable_audio(self):
        from sound_model import import_runtime_track
        source = self.root / "selected.mp3"
        source.write_bytes(b"portable direct assignment")
        record = import_runtime_track(self.root, source)
        self.session.replace(create_project([]), asset_root=self.root)
        self.session.project.update_project_data("sound", {"single_track_id": record.track_id, "volume": 47, "muted": True})
        stored = self.session.save(sync_workspace=False)
        sound = stored.project.to_dict()["sound"]
        self.assertEqual(sound["single_track_id"], record.track_id)
        self.assertEqual((stored.asset_root / sound["tracks"][0]["asset"]).read_bytes(), source.read_bytes())
        self.assertEqual(sound["volume"], 47)
        self.assertTrue(sound["muted"])

    def test_portable_audio_repair_rejects_all_stale_hash_aliases(self):
        import hashlib
        from settings_store import SettingsStore
        from sound_model import current_music_path, load_library, processed_dir, resolve_track_path
        self.session.replace(create_project([]), asset_root=self.root)
        audio = self.root / "audio"
        audio.mkdir()
        expected = b"original portable audio"
        replaced = b"different cached audio"
        digest = hashlib.sha256(expected).hexdigest()
        (audio / "portable.mp3").write_bytes(expected)
        self.session.project.update_project_data("sound", {
            "single_track_id": "authored-alias",
            "tracks": [{"track_id": "authored-alias", "asset": "audio/portable.mp3", "sha256": digest}]})
        project_sound_views(self.session)
        records = load_library(self.root)
        modified = resolve_track_path(self.root, records["authored-alias"])
        self.assertEqual(modified, resolve_track_path(self.root, records[digest[:24]]))
        modified.write_bytes(replaced)
        existing_files = {path.name for path in processed_dir(self.root).iterdir()}
        with mock.patch.object(SettingsStore, "update_fields", side_effect=PermissionError("synthetic failure")):
            with self.assertRaises(PermissionError):
                project_sound_views(self.session)
        self.assertEqual(modified.read_bytes(), replaced)
        self.assertEqual({path.name for path in processed_dir(self.root).iterdir()}, existing_files)
        project_sound_views(self.session)
        records = load_library(self.root)
        recovered = resolve_track_path(self.root, records["authored-alias"])
        self.assertNotEqual(recovered, modified)
        self.assertEqual(recovered.read_bytes(), expected)
        self.assertEqual(modified.read_bytes(), replaced)
        self.assertEqual(current_music_path(self.root).read_bytes(), expected)
        self.assertEqual(records[digest[:24]].content_hash, hashlib.sha256(replaced).hexdigest())
        self.assertEqual(project_sound_snapshot(self.session)["single_track_id"], "authored-alias")

    def test_delayed_provider_image_rejects_a_to_b_to_a_project_switch(self):
        from PromptWriterPanel import PromptWriterPanel
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        original = self.session.project.to_dict()
        panel = PromptWriterPanel(project_root=str(self.root))
        try:
            panel._image_tab = mock.Mock()
            page = self.session.project.pages[0]
            key = page["recovery"]["legacy_key"]
            panel._on_browser_image_started(key)
            self.session.replace(create_project(), asset_root=self.root)
            from project_model import ProjectModel
            self.session.replace(ProjectModel.from_dict(original), asset_root=self.root)
            panel._on_browser_image_ready(key, "late-image.png")
            panel._image_tab.set_image_path.assert_not_called()
            self.assertIn("project was replaced", panel._site_status.text())
        finally:
            panel.shutdown()
            panel.deleteLater()

    def test_delayed_provider_image_keeps_original_target_after_reorder(self):
        from PromptWriterPanel import PromptWriterPanel
        from page_layout import PAGE_INDICES
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        panel = PromptWriterPanel(project_root=str(self.root))
        try:
            panel._image_tab = mock.Mock()
            panel._image_tab.set_image_path.return_value = True
            page = self.session.project.pages[0]
            key = page["recovery"]["legacy_key"]
            panel._on_browser_image_started(key)
            self.session.project.reorder_pages([item["id"] for item in reversed(self.session.project.pages)])
            panel._on_browser_image_ready(key, "selected.png")
            panel._image_tab.set_image_path.assert_called_once_with(PAGE_INDICES[key], "selected.png")
        finally:
            panel.shutdown()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
