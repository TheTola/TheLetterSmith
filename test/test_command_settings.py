from __future__ import annotations

import os
from pathlib import Path
import threading
import unittest
from unittest import mock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from project_lifecycle import NewProjectDialog
from project_model import CLASSIC_PAGE_TYPES, MIN_PAGES, MAX_PAGES
from project_repository import ProjectRepository, active_project_repository
from project_session import get_project_session, release_project_session
from settings_store import SettingsStore
import test_saved_projects as saved_fixtures


class CommandSettingsServiceTests(unittest.TestCase):
    setUpClass = saved_fixtures.SavedProjectTests.__dict__["setUpClass"]
    setUp = saved_fixtures.SavedProjectTests.setUp
    tearDown = saved_fixtures.SavedProjectTests.tearDown
    save_entry = saved_fixtures.SavedProjectTests.save_entry

    def test_starters_use_factory_bounds_and_independent_documents(self):
        original = self.session.project.to_dict()
        token = self.session.session_id
        for kinds in (CLASSIC_PAGE_TYPES, ("end_page", "letter", "letter", "cover"),
                      ("illustration",) * MIN_PAGES, ("letter",) * MAX_PAGES):
            candidate = self.service.prepare_new_project(kinds)
            self.assertEqual([p["type"] for p in candidate.project.pages], list(kinds))
            self.assertTrue(all(p["artwork"]["asset"] is None and p["prompt"]["text"] is None for p in candidate.project.pages))
            letters = [p for p in candidate.project.pages if p["type"] == "letter"]
            if len(letters) > 1:
                message = letters[0]["message"]
                message["html"] = "one only"
                candidate.project.update_message(letters[0]["id"], message)
                self.assertNotEqual(candidate.project.get_message(letters[1]["id"])["html"], "one only")
            self.assertEqual(self.session.project.to_dict(), original)
            self.assertEqual(self.session.session_id, token)
        for kinds in (("cover",) * (MIN_PAGES - 1), ("letter",) * (MAX_PAGES + 1), ("wall", "letter")):
            with self.assertRaises(ValueError if kinds[0] == "wall" else RuntimeError):
                self.service.prepare_new_project(kinds)

    def test_new_activation_retains_saved_versions_preferences_and_libraries(self):
        entry, saved = self.save_entry()
        original = (entry.path / ".lettersmith-project/current.json").read_bytes()
        settings = SettingsStore(self.root)
        settings.update_fields(ui_window_width=1170, visionary_url="https://example.invalid/assistant")
        from project_paths import application_paths
        palette = application_paths(self.root).custom_palette_root / "keep.json"
        palette.parent.mkdir(parents=True, exist_ok=True)
        palette.write_text('{"name":"Keep"}', encoding="utf-8")
        self.session.project.update_project_data("publication", {"published_page_url": "https://example.invalid/published"})
        self.session.project.update_project_data("recovery", {"unassigned_message": {"html": "Retain in old generation"}})
        self.session.save()
        old_token = self.session.capture_target(self.ids[1], fields=("message",), include_shared=False)
        candidate = self.service.prepare_new_project(CLASSIC_PAGE_TYPES)
        self.service.activate_new_project(candidate)
        self.assertNotEqual(self.session.project.project_id, saved.project.project_id)
        self.assertEqual(self.session.project.to_dict()["publication"], {})
        self.assertFalse(self.session.project.to_dict()["sound"].get("playlist"))
        self.assertNotIn("unassigned_message", self.session.project.to_dict()["recovery"])
        self.assertFalse(self.session.target_is_current(old_token))
        self.assertEqual(settings.snapshot()["ui_window_width"], 1170)
        self.assertTrue(palette.is_file())
        self.assertEqual((entry.path / ".lettersmith-project/current.json").read_bytes(), original)
        self.service.open_saved_project(entry)
        self.assertFalse(self.session.target_is_current(old_token))
        self.assertEqual(self.session.project.get_prompt(self.ids[1])["text"], saved.project.get_prompt(self.ids[1])["text"])

    def test_failed_activation_keeps_current_state_and_commit(self):
        self.session.save()
        before = self.session.project.to_dict()
        token = self.session.session_id
        head = active_project_repository(self.root)._head()
        candidate = self.service.prepare_new_project(CLASSIC_PAGE_TYPES)
        with mock.patch.object(ProjectRepository, "save", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.service.activate_new_project(candidate)
        self.assertEqual(self.session.project.to_dict(), before)
        self.assertEqual(self.session.session_id, token)
        self.assertEqual(active_project_repository(self.root)._head(), head)

    def test_exit_discard_restores_explicit_save_and_retains_recovery_generation(self):
        entry, saved = self.save_entry()
        self.session.project.update_prompt(self.ids[1], {"text": "unsaved draft"})
        recovery = self.session.save()
        self.service.discard_active_changes(self.service.prepare_discard_project())
        self.assertEqual(self.session.project.get_prompt(self.ids[1])["text"], saved.project.get_prompt(self.ids[1])["text"])
        self.assertTrue((recovery.asset_root / "project.json").is_file())
        self.assertEqual(ProjectRepository(entry.path / ".lettersmith-project").load().revision, saved.revision)
        release_project_session(self.root)
        self.session = get_project_session(self.root)
        self.assertEqual(self.session.project.get_prompt(self.ids[1])["text"], saved.project.get_prompt(self.ids[1])["text"])

    def test_isolated_profile_never_calls_system_keyring(self):
        from publishing import github_auth
        with mock.patch.dict(os.environ, {"LETTER_SMITH_PROFILE_ROOT": str(self.root)}), \
             mock.patch.object(github_auth.keyring, "get_password") as read, \
             mock.patch.object(github_auth.keyring, "set_password") as write, \
             mock.patch.object(github_auth.keyring, "delete_password") as delete:
            store = github_auth.GitHubCredentialStore()
            self.assertIsNone(store.load())
            store.clear()
            with self.assertRaises(github_auth.GitHubOperationError):
                store.save(None)
            read.assert_not_called()
            write.assert_not_called()
            delete.assert_not_called()

    def test_project_curtains_preserve_overrides_prompts_and_preferences(self):
        from curtain_controls import CurtainStyleController
        settings = SettingsStore(self.root)
        settings.update_fields(ui_window_width=1190, recipient_title_locked=True,
                               last_sealed_path="old-output", music_file="old-audio", last_audio="old-audio")
        controller = CurtainStyleController(settings)
        original = self.session.project.get_prompt(self.ids[1])["text"]
        self.session.project.update_page_settings(self.ids[1], {"curtain_style": "pure_white"})
        controller.set_style("normal_dark")
        self.assertEqual(self.session.project.resolve_settings(self.ids[1])["curtain_style"], "pure_white")
        self.assertEqual(self.session.project.resolve_settings(self.ids[3])["curtain_style"], "normal_dark")
        self.assertEqual(self.session.project.get_prompt(self.ids[1])["text"], original)
        self.service.activate_new_project(self.service.prepare_new_project(CLASSIC_PAGE_TYPES))
        restored = settings.snapshot()
        self.assertEqual(restored["ui_window_width"], 1190)
        self.assertFalse(restored["recipient_title_locked"])
        self.assertEqual(restored["last_sealed_path"], "")
        self.assertEqual(restored["music_file"], "")
        self.assertEqual(restored["last_audio"], "none")
        controller.close()

    def test_real_starter_dialog_order_keyboard_and_limits(self):
        dialog = NewProjectDialog()
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.page_types(), CLASSIC_PAGE_TYPES)
        dialog.starter.setCurrentIndex(dialog.starter.findData("custom"))
        dialog.count.setValue(MAX_PAGES)
        for index in range(MAX_PAGES):
            choice = dialog.pages.cellWidget(index, 0)
            choice.setCurrentIndex(choice.findData("illustration"))
        self.assertEqual(dialog.page_types(), ("illustration",) * MAX_PAGES)
        dialog.count.setValue(MIN_PAGES)
        self.assertEqual(len(dialog.page_types()), MIN_PAGES)
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
        dialog.deleteLater()


class CommandSettingsUiTests(unittest.TestCase):
    setUpClass = saved_fixtures.SavedProjectTests.__dict__["setUpClass"]
    setUp = saved_fixtures.SavedProjectUiTests.setUp
    tearDown = saved_fixtures.SavedProjectUiTests.tearDown
    wait = saved_fixtures.SavedProjectUiTests.wait

    def edits(self):
        for page_id, text in ((self.ids[1], " Final A"), (self.ids[3], " Final B")):
            self.assertTrue(self.window.open_message_page(page_id))
            editor = self.window.message_tab.messages_workspace.editor.editor
            editor.moveCursor(QtGui.QTextCursor.End)
            QtTest.QTest.keyClicks(editor, text)
        self.window.open_prompt_writer(page_id=self.ids[3])
        panel = self.window._prompt_writer_win
        self.assertTrue(panel.isWindow())
        editor = panel._view_spec.preview_widget
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, " Last prompt")
        return self.session.project.get_prompt(self.ids[3])["text"]

    def new_project(self, choice, kinds=CLASSIC_PAGE_TYPES):
        with mock.patch.object(NewProjectDialog, "exec", return_value=QtWidgets.QDialog.Accepted), \
             mock.patch.object(NewProjectDialog, "page_types", return_value=kinds), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=choice):
            return self.window.project_lifecycle.new_project()

    def test_cancel_dialog_and_exit_leave_editors_and_session_usable(self):
        exact = self.edits()
        token = self.session.session_id
        with mock.patch.object(NewProjectDialog, "exec", return_value=QtWidgets.QDialog.Rejected):
            self.assertFalse(self.window.project_lifecycle.new_project())
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._shutdown_complete)
        self.assertFalse(self.window.image_tab._shutdown)
        self.assertFalse(self.window.sound_tab._shutdown)
        self.assertEqual(self.session.session_id, token)
        self.assertEqual(self.session.project.get_prompt(self.ids[3])["text"], exact)
        self.assertTrue(self.window.flush_project_edits())
        self.session.project.rename_page(self.ids[1], "Still editable")
        self.assertEqual(self.session.project.get_page(self.ids[1])["name"], "Still editable")
        from Main import connect_shutdown_handler
        connect_shutdown_handler(self.app, self.window)
        try:
            with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Cancel) as decision:
                self.app.sendEvent(self.app, QtCore.QEvent(QtCore.QEvent.Quit))
                self.assertEqual(decision.call_count, 1)
            self.assertFalse(self.window._shutdown_complete)
        finally:
            self.app.removeEventFilter(self.window._application_quit_guard)

    def test_save_then_new_includes_last_edits_and_clears_active_content(self):
        self.assertIs(self.service, self.window.project_save_service)
        exact = self.edits()
        original_id = self.session.project.project_id
        self.assertTrue(self.new_project(QtWidgets.QMessageBox.Save, ("letter", "letter", "illustration")))
        self.wait(lambda: not self.window.project_lifecycle.busy and not self.forge._busy, seconds=180)
        self.assertNotEqual(self.session.project.project_id, original_id)
        saved = ProjectRepository(self.service.editable_root / original_id / ".lettersmith-project").load()
        self.assertIn("Final A", saved.project.get_message(self.ids[1])["html"])
        self.assertIn("Final B", saved.project.get_message(self.ids[3])["html"])
        self.assertEqual(saved.project.get_prompt(self.ids[3])["text"], exact)
        self.assertEqual([p["type"] for p in self.session.project.pages], ["letter", "letter", "illustration"])
        self.assertTrue(all(p["artwork"]["asset"] is None and not p["prompt"]["text"] for p in self.session.project.pages))
        self.assertFalse(self.session.project.to_dict()["sound"].get("playlist"))
        self.assertIsNone(self.window._last_pixmap)
        self.assertEqual(self.window.title_bar._project_title, "")
        self.assertTrue(self.service.has_unsaved_editable_changes())

    def test_save_failure_and_edit_during_save_cancel_transition(self):
        token = self.session.session_id
        with mock.patch.object(self.service, "commit_editable_save", side_effect=OSError("synthetic write failure")):
            self.assertTrue(self.new_project(QtWidgets.QMessageBox.Save))
            self.wait(lambda: not self.forge._busy)
        self.assertFalse(self.window.project_lifecycle.busy)
        self.assertEqual(self.session.session_id, token)
        started, release = threading.Event(), threading.Event()
        commit = self.service.commit_editable_save
        def delayed(capture):
            started.set()
            if not release.wait(10):
                raise RuntimeError("test save was not released")
            return commit(capture)
        with mock.patch.object(self.service, "commit_editable_save", side_effect=delayed):
            try:
                self.assertTrue(self.new_project(QtWidgets.QMessageBox.Save))
                self.wait(started.is_set)
                self.assertFalse(self.window.title_bar.new_project_action.isEnabled())
                self.assertFalse(self.window.command_tab.project_actions.isEnabled())
                self.assertFalse(self.window.project_lifecycle.new_project())
                self.session.project.rename_page(self.ids[0], "Newer change")
            finally:
                release.set()
            self.wait(lambda: not self.forge._busy)
        self.assertFalse(self.window.project_lifecycle.busy)
        self.assertTrue(self.window.title_bar.new_project_action.isEnabled())
        self.assertEqual(self.session.session_id, token)
        self.assertTrue(self.service.has_unsaved_editable_changes())

    def test_discard_new_and_failed_candidate_keep_saved_project(self):
        self.assertTrue(self.window.flush_project_edits())
        destination, saved = self.service.save_editable()
        self.session.project.rename_page(self.ids[0], "Draft change")
        token = self.session.session_id
        with mock.patch.object(self.service, "prepare_new_project", side_effect=OSError("not writable")):
            self.assertFalse(self.new_project(QtWidgets.QMessageBox.Discard))
        self.assertEqual(self.session.session_id, token)
        self.assertTrue(self.new_project(QtWidgets.QMessageBox.Discard, ("illustration", "end_page")))
        self.assertNotEqual(self.session.session_id, token)
        self.assertFalse(any(p["message"] for p in self.session.project.pages))
        self.assertEqual(ProjectRepository(destination / ".lettersmith-project").load().revision, saved.revision)

    def test_conflict_and_invalid_pending_field_prevent_exit(self):
        self.assertTrue(self.window.flush_project_edits())
        destination, saved = self.service.save_editable()
        repository = ProjectRepository(destination / ".lettersmith-project")
        external = repository.load()
        external.project.rename_page(self.ids[0], "Other writer")
        external = repository.save(external.project, asset_root=external.asset_root, expected_revision=external.revision)
        self.session.project.rename_page(self.ids[0], "Local writer")
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.window.close()
            self.wait(lambda: not self.forge._busy)
        self.assertFalse(self.window._shutdown_complete)
        self.assertFalse(self.window.project_lifecycle.busy)
        self.assertEqual(repository.load().revision, external.revision)
        self.assertEqual(self.session.project.get_page(self.ids[0])["name"], "Local writer")
        field = QtWidgets.QLineEdit(self.window)
        field.setValidator(QtGui.QIntValidator(1, 9, field))
        field.setText("invalid")
        field.show()
        field.setFocus()
        self.app.processEvents()
        with mock.patch.object(QtWidgets.QMessageBox, "question") as decision:
            self.assertFalse(self.window.project_lifecycle.request_exit())
            decision.assert_not_called()
        self.assertEqual(field.text(), "invalid")
        field.hide()
        field.deleteLater()

    def test_save_shortcut_works_outside_forge(self):
        self.window.tabbar.setCurrentIndex(0)
        self.session.project.rename_page(self.ids[0], "Saved from Images")
        QtTest.QTest.keyClick(self.window, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier)
        self.wait(lambda: not self.forge._busy and self.forge._selected_saved_letter is not None)
        saved = self.service.read_saved_project(self.forge._selected_saved_letter)
        self.assertEqual(saved.project.get_page(self.ids[0])["name"], "Saved from Images")

    def test_view_restore_failure_reports_recoverable_committed_project(self):
        original_id = self.session.project.project_id
        with mock.patch.object(self.window.image_tab, "refresh_from_disk", side_effect=OSError("synthetic view failure")):
            self.new_project(QtWidgets.QMessageBox.Discard)
        self.assertNotEqual(self.session.project.project_id, original_id)
        self.assertEqual(active_project_repository(self.root).load().project.project_id, self.session.project.project_id)
        self.assertFalse(self.window.project_lifecycle.busy)
        self.assertIn("recoverable", self.window._status.currentMessage())
        self.assertNotIn("ready", self.window._status.currentMessage())

    def test_exit_save_and_restart_preserve_last_edits(self):
        self.assertIs(self.service, self.window.project_save_service)
        exact = self.edits()
        original_id = self.session.project.project_id
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Save):
            self.window.close()
            self.wait(lambda: self.window._shutdown_complete or not self.window.project_lifecycle.busy, seconds=180)
        self.assertTrue(self.window._shutdown_complete, self.window._status.currentMessage())
        saved = ProjectRepository(self.service.editable_root / original_id / ".lettersmith-project").load()
        self.assertIn("Final B", saved.project.get_message(self.ids[3])["html"])
        self.assertEqual(saved.project.get_prompt(self.ids[3])["text"], exact)
        restarted = get_project_session(self.root)
        self.assertEqual(restarted.project.project_id, original_id)
        self.assertEqual(restarted.project.get_prompt(self.ids[3])["text"], exact)

    def test_exit_discard_restores_last_save_without_destroying_libraries(self):
        self.assertTrue(self.window.flush_project_edits())
        destination, saved = self.service.save_editable()
        self.edits()
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Discard):
            self.window.close()
            self.wait(lambda: self.window._shutdown_complete or not self.window.project_lifecycle.busy, seconds=180)
        self.assertTrue(self.window._shutdown_complete, self.window._status.currentMessage())
        restarted = get_project_session(self.root)
        self.assertEqual(restarted.project.get_prompt(self.ids[3])["text"], saved.project.get_prompt(self.ids[3])["text"])
        self.assertEqual(ProjectRepository(destination / ".lettersmith-project").load().revision, saved.revision)


if __name__ == "__main__":
    unittest.main()
