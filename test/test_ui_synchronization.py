"""Combined production routes; persistence and rendering are real and isolated."""
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from project_model import create_project
from project_session import SessionChange
import test_saved_projects as saved_fixtures


class SynchronizationTests(unittest.TestCase):
    setUpClass = saved_fixtures.SavedProjectTests.__dict__["setUpClass"]
    setUp = saved_fixtures.SavedProjectUiTests.setUp
    tearDown = saved_fixtures.SavedProjectUiTests.tearDown
    wait = saved_fixtures.SavedProjectUiTests.wait

    def js(self, preview, code):
        result = []
        preview.view.page().runJavaScript("JSON.stringify((() => {" + code + "})())", result.append)
        self.wait(lambda: bool(result))
        return json.loads(result[0])

    def test_five_page_routes_layout_exact_prompts_save_and_reopen(self):
        window, images = self.window, self.window.image_tab
        workspace = window.message_tab.messages_workspace
        a, b = self.ids[1], self.ids[3]
        original_a = self.session.project.get_message(a)
        self.session.select_page(b)
        images.message_button.click()
        self.assertEqual(workspace.page_id, b)
        workspace.editor.editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(workspace.editor.editor, " Final distinguishing B")
        images.request_prompt()
        panel = window._prompt_writer_win
        self.assertEqual(panel._view_page_id, b)
        self.assertIn("Final distinguishing B", self.session.project.get_message(b)["html"])
        panel._view_spec.preview_widget.setPlainText("  Manual B café 雪\n\n  ")
        exact = self.session.project.get_prompt(b)["text"]
        images.request_prompt()
        self.assertIs(window._prompt_writer_win, panel)
        panel.btn_page_settings.click()
        self.assertEqual(window.tabbar.currentIndex(), 0)
        self.assertEqual(self.session.selected_page_id, b)
        images.move_page(b, -2)
        images.rename_page(b, "Second Letter with a long duplicate-friendly name")
        self.assertEqual(self.session.project.pages[1]["id"], b)
        self.assertIn("Page 2 of 5", images.header.text())
        self.assertIn("Page 2 of 5", workspace.heading.text())
        self.assertIn("Page 2 of 5", panel.target_selector.currentText())
        self.assertEqual(self.session.project.get_prompt(b)["text"], exact)
        images.message_button.click()
        target = self.session.capture_target(b, fields=("type", "message", "artwork", "design_overrides"))
        layout = self.session.project.get_message(b)["layout"]
        layout["box"]["y"] += 30
        workspace.apply_layout(target, layout)
        self.assertEqual(self.session.project.get_message(b)["layout"], layout)
        self.assertTrue(self.session.project.get_prompt(b)["stale"])
        self.assertEqual(self.session.project.get_prompt(b)["text"], exact)
        preview = workspace.shared_preview
        workspace.render_preview()
        self.wait(lambda: preview.viewed_page_id == b, seconds=90)
        before = self.session.project.to_dict()
        self.assertTrue(self.js(preview, f"return window.LetterSmithViewer.goToPage({json.dumps(a)});"))
        self.wait(lambda: preview.viewed_page_id == a)
        self.assertEqual(self.session.selected_page_id, b)
        self.assertEqual(panel._view_page_id, b)
        self.assertEqual(workspace.page_id, b)
        self.assertEqual(self.session.project.to_dict(), before)
        self.assertTrue(preview.view.page().isAudioMuted())
        rendered = preview._current[1].snapshot["pages"]
        rendered_letters = {page["id"]: page for page in rendered if page["type"] == "letter"}
        self.assertIn("Final distinguishing B", rendered_letters[b]["message"]["html"])
        self.assertNotIn("Final distinguishing B", rendered_letters[a]["message"]["html"])
        self.assertNotEqual(rendered_letters[a]["resolved_layout"], rendered_letters[b]["resolved_layout"])
        panel.select_page(b)
        panel._view_spec.preview_widget.moveCursor(QtGui.QTextCursor.End)
        panel._view_spec.preview_widget.insertPlainText("last 雪\n  ")
        exact = self.session.project.get_prompt(b)["text"]
        self.assertTrue(self.ui.save())
        self.wait(lambda: not self.forge._busy)
        entry = self.forge._selected_saved_letter
        loaded = self.service.read_saved_project(entry)
        self.assertEqual(loaded.project.get_prompt(b)["text"], exact)
        self.assertEqual(loaded.project.get_message(a), original_a)
        self.assertEqual(loaded.project.to_dict()["sound"]["playlist"], ["music", "music"])
        self.assertTrue(self.ui.open_selected())
        self.wait(lambda: not self.forge._busy, seconds=180)
        self.assertEqual(self.session.project.get_prompt(b)["text"], exact)
        self.assertEqual(panel._view_session_id, self.session.session_id)
        self.assertEqual(self.session.project.get_message(a), original_a)

    def test_fallback_warning_routes_and_bounded_subscriptions(self):
        window, session = self.window, self.session
        workspace = window.message_tab.messages_workspace
        b = self.ids[3]
        self.assertTrue(window.open_message_page(b))
        window.tabbar.setCurrentIndex(0)
        session.select_page(self.ids[2])
        window.tabbar.setCurrentIndex(2)
        self.assertEqual(workspace.page_id, b)
        self.assertEqual(session.selected_page_id, b)
        window.open_prompt_writer(b)
        panel = window._prompt_writer_win
        listeners = len(session._listeners)
        events = []
        session.add_listener(events.append)
        revision = session.project.revision
        for _ in range(5):
            session.select_page(self.ids[1])
            session.select_page(b)
            window.open_prompt_writer(b)
        self.assertEqual(session.project.revision, revision)
        self.assertEqual(len(session._listeners), listeners + 1)
        self.assertEqual(len(events), 10)
        self.assertTrue(all(event.kind == "selection" for event in events))
        self.assertIs(window._prompt_writer_win, panel)
        session.remove_listener(events.append)
        window.image_tab.move_page(b, -2)
        self.forge._handle_readiness_correction("message", b)
        self.assertEqual(workspace.page_id, b)
        self.forge._handle_readiness_correction("images", b)
        self.app.processEvents()
        self.assertEqual(session.selected_page_id, b)
        self.assertEqual(window.tabbar.currentIndex(), 0)
        self.assertIn("Page 2 of 5", window.image_tab.header.text())
        from project_lifecycle import NewProjectDialog
        self.wait(lambda: not self.forge._busy and window._autosave_active_revision is None)
        with mock.patch.object(NewProjectDialog, "exec", return_value=QtWidgets.QDialog.Accepted), \
             mock.patch.object(NewProjectDialog, "page_types", return_value=("illustration", "end_page")), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.assertTrue(window.project_lifecycle.new_project())
        self.wait(lambda: not window.project_lifecycle.busy)
        window.recipient_page.recipient_submitted.emit("Artwork only", False)
        window.tabbar.setCurrentIndex(2)
        self.assertIsNone(workspace.page_id)
        self.wait(workspace.empty.isVisible)
        self.assertEqual(len(session.project.pages), 2)

    def test_old_session_notifications_and_save_completion_cannot_change_reopened_a(self):
        self.assertTrue(self.window.flush_project_edits())
        original = self.session.project.to_dict()
        old_session = self.session.session_id
        from project_model import ProjectModel
        self.session.replace(create_project(), asset_root=self.root)
        self.session.replace(ProjectModel.from_dict(original), asset_root=self.root)
        self.assertNotEqual(self.session.session_id, old_session)
        revision = self.window.project_dirty.revision
        self.window._on_foundation_changed(SessionChange("message", (self.ids[1],), 100, old_session))
        self.assertEqual(self.window.project_dirty.revision, revision)
        self.window._autosave_tasks[100] = SimpleNamespace(session_id=old_session)
        self.window._autosave_active_revision = 100
        with mock.patch.object(self.service, "finish_deferred_workspace_snapshot") as completion:
            self.window._project_autosave_completed(100, "old destination")
        completion.assert_not_called()
        self.assertEqual(self.window.project_dirty.revision, revision)
        self.assertEqual(self.session.project.to_dict(), original)

    def test_failed_name_commit_blocks_tabs_and_private_edits_do_not_refresh_forge(self):
        window = self.window
        field = window.image_tab.name_edit
        field.setFocus()
        field.selectAll()
        QtTest.QTest.keyClicks(field, "Unsaved long page name")
        self.assertIn("Unsaved changes", window.title_bar.title_label.text())
        with mock.patch.object(self.session, "save", side_effect=OSError("synthetic save failure")):
            window.tabbar.setCurrentIndex(2)
        self.assertEqual(window.tabbar.currentIndex(), 0)
        self.assertEqual(window.image_tab.name_edit.text(), "Unsaved long page name")
        with mock.patch.object(self.forge, "schedule_refresh") as refresh:
            self.session.project.update_prompt(self.ids[1], {"text": "private exact 雪\n  "})
            refresh.assert_not_called()
            self.forge._on_project_session_changed(SessionChange("message", (), 10, "old-session"))
            refresh.assert_not_called()
        self.assertTrue(window.flush_project_edits())

    def test_sound_forge_messages_transfer_playback_without_changing_editor_target(self):
        window = self.window
        b = self.ids[3]
        self.session.select_page(b)
        window.tabbar.setCurrentIndex(1)
        sound = window.sound_tab
        self.wait(lambda: sound._tab_active)
        sound.player.set_muted(True)
        sound.player.play()
        self.wait(sound.player.is_playing)
        with mock.patch.object(sound.player, "stop", wraps=sound.player.stop) as stop:
            window.tabbar.setCurrentIndex(3)
            self.wait(lambda: not sound._tab_active)
            self.assertEqual(stop.call_count, 1)
        self.assertFalse(sound.player.is_playing())
        self.wait(lambda: not self.forge._busy and window.html_preview is not None, seconds=120)
        self.assertEqual(self.session.selected_page_id, b)
        window.tabbar.setCurrentIndex(2)
        self.wait(lambda: window.message_tab.messages_workspace.isVisible())
        self.assertEqual(window.message_tab.messages_workspace.page_id, b)
        result = []
        window.html_preview.page().runJavaScript(
            "Array.from(document.querySelectorAll('audio,video')).every(media => media.paused)", result.append)
        self.wait(lambda: bool(result))
        self.assertTrue(result[0])
        self.assertFalse(sound.player.is_playing())

    def test_failed_preview_release_keeps_session_and_shutdown_subscriptions(self):
        window = self.window
        session_id = self.session.session_id
        listeners = len(self.session._listeners)
        view = window._ensure_forge_preview()
        with mock.patch.object(view, "url", return_value=QtCore.QUrl.fromLocalFile(str(self.root / "index.html"))), \
             mock.patch.object(view, "setUrl"):
            self.assertFalse(window._release_forge_preview_files(timeout_ms=0))
        self.assertIn("still releasing", self.forge._project_release_error)
        with mock.patch.object(window, "_release_forge_preview_files", return_value=False):
            self.assertFalse(window.shutdown())
        self.assertFalse(window._shutdown_complete)
        self.assertFalse(window._window_controller._disposed)
        self.assertFalse(window.image_tab._shutdown)
        self.assertEqual(len(self.session._listeners), listeners)
        self.assertEqual(self.session.session_id, session_id)
        self.assertTrue(window.open_message_page(self.ids[3]))
        self.assertTrue(window.flush_project_edits())
        # Finish the deliberately suppressed unload, then allow a normal retry.
        view.setUrl(QtCore.QUrl("about:blank"))
        self.wait(lambda: not window._forge_preview_unload_pending)
        self.assertTrue(window._release_forge_preview_files())

    def test_modal_layout_keeps_unapplied_input_when_save_new_or_exit_is_requested(self):
        from message_layout_dialog import MessageLayoutDialog
        window = self.window
        b = self.ids[3]
        self.assertTrue(window.open_message_page(b))
        before, session_id = self.session.project.to_dict(), self.session.session_id
        dialog = MessageLayoutDialog(window, self.session.project.get_page(b),
                                     self.session.project.resolve_settings(b),
                                     before["shared_settings"], "Letter B", mock.Mock())
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        dialog.show()
        self.app.processEvents()
        try:
            field = next(iter(dialog.values.values())).lineEdit()
            field.setText("unfinished")
            self.assertFalse(self.ui.save())
            self.assertFalse(window.project_lifecycle.new_project())
            self.assertFalse(window.project_lifecycle.request_exit())
            self.assertEqual(field.text(), "unfinished")
            self.assertEqual(self.session.project.to_dict(), before)
            self.assertEqual(self.session.session_id, session_id)
            self.assertFalse(window._shutdown_complete)
        finally:
            dialog.reject()
            dialog.deleteLater()
            self.app.processEvents()
        self.assertTrue(window.flush_project_edits())


if __name__ == "__main__":
    unittest.main()
