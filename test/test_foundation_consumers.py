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
from project_consumers import ConsumerCompatibilityError, consumer_issue, project_legacy_views, require_consumer
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

    def test_delayed_provider_image_rejects_a_to_b_to_a_project_switch(self):
        from PromptWriterPanel import PromptWriterPanel
        self.session.replace(create_project(), asset_root=self.root)
        project_legacy_views(self.session)
        original = self.session.project.to_dict()
        panel = PromptWriterPanel(project_root=str(self.root))
        try:
            panel._image_tab = mock.Mock(spec=["set_image_path"])
            page = self.session.project.pages[0]
            key = page["id"]
            panel._browser_targets[key] = self.session.capture_target(key)
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
            panel._image_tab = mock.Mock(spec=["set_image_path"])
            panel._image_tab.set_image_path.return_value = True
            page = self.session.project.pages[0]
            key = page["recovery"]["legacy_key"]
            page_id = page["id"]
            panel._browser_targets[page_id] = self.session.capture_target(page_id)
            panel._on_browser_image_started(page_id)
            self.session.project.reorder_pages([item["id"] for item in reversed(self.session.project.pages)])
            panel._on_browser_image_ready(page_id, "selected.png")
            panel._image_tab.set_image_path.assert_called_once_with(PAGE_INDICES[key], "selected.png")
        finally:
            panel.shutdown()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
