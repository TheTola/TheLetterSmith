from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

import project_paths
from project_model import create_project
from project_session import get_project_session, release_project_session


@unittest.skipUnless(importlib.util.find_spec("forge_render"), "Requires the committed Forge adapter integration")
class SharedMessagesPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lsm-browser-")
        self.previous_paths = project_paths._APPLICATION_PATHS
        self.paths = project_paths.ApplicationPaths.for_runtime(
            Path(__file__).resolve().parents[1],
            environ={"LETTER_SMITH_PROFILE_ROOT": self.temporary.name},
        )
        project_paths.configure_application_paths(self.paths)
        self.paths.initialize()
        self.root = self.paths.workspace_root
        self.session = get_project_session(self.root)
        project = create_project(["cover", "letter", "illustration", "letter", "end_page"])
        self.a, self.b = project.pages[1]["id"], project.pages[3]["id"]
        self.original_gif = None
        for index, page in enumerate(project.pages):
            source = self.root / f"art-{index}.png"
            Image.new("RGB", (100, 150), ("gray", "red", "gray", "blue", "gray")[index]).save(source)
            if page["id"] == self.b:
                source = source.with_suffix(".gif")
                Image.new("RGB", (100, 150), "blue").save(
                    source, save_all=True, append_images=[Image.new("RGB", (100, 150), "navy")], duration=120, loop=0,
                )
                self.original_gif = hashlib.sha256(source.read_bytes()).hexdigest()
            project.update_artwork(page["id"], {"asset": source.name, "media_type": "image/gif" if index == 3 else "image/png"})
            if page["type"] == "letter":
                name = "Letter A" if page["id"] == self.a else "Letter B"
                project.rename_page(page["id"], name)
                document = project.get_message(page["id"])
                document["html"] = f'<p style="font-size:26pt"><b>{name} — café 雪</b></p>'
                if page["id"] == self.b:
                    document["html"] += "<p>Long independent letter B.</p>" * 60
                project.update_message(page["id"], document)
                project.update_page_settings(page["id"], {"message_overlay_preset": "clear", "message_overlay_opacity": 0})
        self.session.replace(project, asset_root=self.root)
        self.session.save(sync_workspace=False)
        from Message_tab import MessageTab
        self.tab = MessageTab(str(self.root))
        self.workspace = self.tab.messages_workspace
        self.preview = self.workspace.shared_preview
        self.assertIsNotNone(self.preview)
        self.tab.resize(1200, 800)
        self.tab.show()
        self.app.processEvents()
        self.workspace._preview_timer.stop()

    def tearDown(self):
        try:
            self.assertTrue(self.tab.shutdown())
            self.tab.hide()
            self.tab.deleteLater()
            self.app.processEvents()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            release_project_session(self.root)
        finally:
            project_paths.configure_application_paths(self.previous_paths)
            self.temporary.cleanup()

    def wait_for(self, condition, message="Preview did not settle", timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.02)
        self.fail(message + ": " + self.workspace.preview_note.text())

    def js(self, source):
        results = []
        self.preview.view.page().runJavaScript(
            f"JSON.stringify((() => {{ {source} }})())", results.append,
        )
        self.wait_for(lambda: bool(results), "Browser script did not complete")
        return json.loads(results[0])

    def open_preview(self, page_id):
        self.workspace.open_page(page_id)
        self.workspace.render_preview()
        self.wait_for(lambda: self.preview.viewed_page_id == page_id)
        self.wait_for(lambda: self.js("return !!document.querySelector('.text-wall.is-open');"))

    def test_real_shared_viewer_binds_letters_animates_gif_and_scrolls_without_changing_documents(self):
        self.open_preview(self.a)
        self.assertIn("Letter A", self.js("return document.querySelector('.text-wall.is-open .text-wall-content').textContent;"))
        self.assertTrue(self.preview.view.page().isAudioMuted())
        self.open_preview(self.b)
        self.assertIn("Letter B", self.js("return document.querySelector('.text-wall.is-open .text-wall-content').textContent;"))
        original = self.session.project.get_message(self.b)
        self.wait_for(lambda: self.js("return document.querySelector('.text-wall.is-open').scrollHeight > document.querySelector('.text-wall.is-open').clientHeight;"))
        self.assertGreater(self.js("const w=document.querySelector('.text-wall.is-open'); w.scrollTop=120; return w.scrollTop;"), 0)
        first_frame = self.js("return document.querySelector('.slide.active img').src;")
        self.wait_for(lambda: self.js("return document.querySelector('.slide.active img').src;") != first_frame)
        self.workspace.zoom.setValue(150)
        self.assertEqual(self.preview.view.zoomFactor(), 1.5)
        self.tab.resize(900, 600)
        self.app.processEvents()
        self.assertEqual(self.session.project.get_message(self.b), original)
        artwork = self.session.asset_root / self.session.project.get_page(self.b)["artwork"]["asset"]
        self.assertEqual(hashlib.sha256(artwork.read_bytes()).hexdigest(), self.original_gif)

    def test_delayed_build_does_not_redirect_after_switch_or_project_replacement(self):
        import forge_render
        entered, resume = threading.Event(), threading.Event()
        build = forge_render.build_render_to

        def delayed(*args, **kwargs):
            entered.set()
            resume.wait(5)
            return build(*args, **kwargs)

        with mock.patch.object(forge_render, "build_render_to", side_effect=delayed):
            self.workspace.render_preview()
            self.wait_for(entered.is_set)
            self.workspace.open_page(self.b)
            self.workspace.render_preview()
            resume.set()
            self.wait_for(lambda: self.preview.viewed_page_id == self.b)
        self.assertEqual(self.workspace.page_id, self.b)
        self.session.replace(create_project(["cover", "illustration", "end_page"]))
        self.wait_for(lambda: self.preview.view.url().toString() == "about:blank")
        self.assertIsNone(self.preview.viewed_page_id)
        self.assertIsNone(self.preview._current)
        self.assertIsNone(self.workspace.page_id)

    def test_hide_and_restore_release_generated_files_and_reopen_selected_page(self):
        self.open_preview(self.b)
        previous = Path(self.preview._current[0].name)
        self.tab.deactivate_for_tab_change()
        self.wait_for(lambda: not self.preview._retired)
        self.assertFalse(previous.exists())
        self.assertEqual(self.preview.view.url().toString(), "about:blank")
        self.tab.activate_for_tab_change()
        self.open_preview(self.b)
        self.tab.prepare_for_project_restore()
        self.assertIsNone(self.preview._current)
        self.assertFalse(self.preview._retired)

    def test_missing_background_keeps_draft_editable_and_validation_does_not_show_fake_output(self):
        self.open_preview(self.a)
        self.session.project.update_artwork(self.a, {"asset": "missing.png"})
        self.workspace.render_preview()
        self.assertIn("No background artwork", self.workspace.preview_label.text())
        self.assertIsNone(self.preview._current)
        self.workspace.editor.editor.moveCursor(QtGui.QTextCursor.End)
        self.workspace.editor.editor.insertPlainText(" Draft still editable.")
        self.assertTrue(self.workspace.save())
        self.assertIn("Draft still editable.", self.session.project.get_message(self.a)["html"])
        self.workspace.open_page(self.b)
        document = self.session.project.get_message(self.b)
        document["layout"] = {"unsupported-units": 10}
        self.session.project.update_message(self.b, document)
        self.workspace.render_preview()
        self.wait_for(lambda: "Preview unavailable" in self.workspace.preview_note.text())
        self.assertIsNone(self.preview._current)
        self.assertEqual(self.session.project.get_message(self.b), document)

    def test_repeated_release_unloads_and_keeps_preview_usable(self):
        self.open_preview(self.b)
        previous = Path(self.preview._current[0].name)
        self.assertTrue(self.preview.release())
        self.assertTrue(self.preview.release())
        self.assertEqual(self.preview.view.url().toString(), "about:blank")
        self.assertFalse(previous.exists())
        self.assertFalse(self.preview._closed)
        self.open_preview(self.a)
        self.assertEqual(self.preview.viewed_page_id, self.a)


if __name__ == "__main__":
    unittest.main()
