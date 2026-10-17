from __future__ import annotations

import base64
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

import project_paths
from project_model import create_project
from project_session import get_project_session, release_project_session, reset_project_session
from test_project_model import five_page_project


class MessagesWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ls-messages-test-")
        self.profile = Path(self.temporary.name)
        self.previous_paths = project_paths._APPLICATION_PATHS
        self.paths = project_paths.ApplicationPaths.for_runtime(
            Path(__file__).resolve().parents[1],
            environ={"LETTER_SMITH_PROFILE_ROOT": str(self.profile)},
        )
        project_paths.configure_application_paths(self.paths)
        self.root = self.paths.workspace_root
        self.root.mkdir(parents=True)
        self.previous_settings_format = QtCore.QSettings.defaultFormat()
        probe = QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                                 "LetterSmithPathProbe", "Probe")
        self.previous_ini_root = Path(probe.fileName()).parent.parent
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                                 str(self.paths.settings_root))
        self.session = get_project_session(self.root)
        fixture = five_page_project()
        for page in fixture.pages:
            if page["type"] == "letter":
                document = fixture.get_message(page["id"])
                document["layout"] = {}
                fixture.update_message(page["id"], document)
        self.ids = [page["id"] for page in fixture.pages]
        self.letter_a, self.illustration, self.letter_b = self.ids[1:4]
        self.session.replace(fixture)
        self.session.save(sync_workspace=False)
        self.tab = None
        self._open_tab()

    def tearDown(self) -> None:
        try:
            if self.tab is not None:
                self.assertTrue(self.tab.shutdown())
                self.tab.hide()
                self.tab.deleteLater()
            self.app.processEvents()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            release_project_session(self.root)
        finally:
            project_paths.configure_application_paths(self.previous_paths)
            QtCore.QSettings.setDefaultFormat(self.previous_settings_format)
            QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                                     str(self.previous_ini_root))
            self.temporary.cleanup()

    def _open_tab(self) -> None:
        # Production imports run after profile paths and Qt settings are isolated.
        from Message_tab import MessageTab

        self.tab = MessageTab(str(self.root))
        self.workspace = self.tab.messages_workspace
        self.tab.resize(1200, 850)
        self.tab.show()
        self.app.processEvents()
        self.assertIsNotNone(self.workspace)

    def _type(self, text: str, *, replace: bool = True) -> None:
        editor = self.workspace.editor.editor
        editor.setFocus()
        if replace:
            editor.selectAll()
        else:
            editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, text)

    def _plain(self, page_id: str) -> str:
        document = QtGui.QTextDocument()
        document.setHtml(self.session.project.get_message(page_id)["html"])
        return document.toPlainText().strip()

    def _character_format(self) -> QtGui.QTextCharFormat:
        cursor = QtGui.QTextCursor(self.workspace.editor.editor.document())
        cursor.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def _background(self, page_id: str, color: str) -> str:
        reference = f"assets/{page_id}.png"
        target = self.root / reference
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QtGui.QImage(80, 120, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor(color))
        self.assertTrue(image.save(str(target)))
        self.session.project.update_artwork(page_id, {"asset": reference})
        return reference

    def test_letters_keep_independent_typed_text_and_formatting_during_rapid_switches(self) -> None:
        self.assertTrue(self.workspace.open_page(self.letter_a))
        self._type("Alpha independent letter")
        self.workspace.editor.editor.selectAll()
        self.workspace.editor.act_underline.trigger()
        self.assertTrue(self.workspace.open_page(self.letter_b))
        self._type("Beta independent letter")
        self.workspace.editor.editor.selectAll()
        self.workspace.editor.act_italic.trigger()
        for _ in range(3):
            self.assertTrue(self.workspace.open_page(self.letter_a))
            self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Alpha independent letter")
            self.assertTrue(self._character_format().fontUnderline())
            self.assertTrue(self.workspace.open_page(self.letter_b))
            self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Beta independent letter")
            self.assertTrue(self._character_format().fontItalic())
            self.assertFalse(self._character_format().fontUnderline())
        self.assertEqual(self._plain(self.letter_a), "Alpha independent letter")
        self.assertEqual(self._plain(self.letter_b), "Beta independent letter")

    def test_selector_uses_actual_positions_and_ids_after_reorder_and_duplicate_names(self) -> None:
        self.assertEqual([self.workspace.selector.itemText(i) for i in range(2)],
                         ["Page 2 — Letter", "Page 4 — Letter"])
        self.session.project.rename_page(self.letter_a, "Same title")
        self.session.project.rename_page(self.letter_b, "Same title")
        order = list(self.ids)
        order[1], order[3] = order[3], order[1]
        self.session.project.reorder_pages(order)
        self.assertEqual([self.workspace.selector.itemText(i) for i in range(2)],
                         ["Page 2 — Same title", "Page 4 — Same title"])
        self.workspace.selector.setCurrentIndex(0)
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.assertEqual(self.workspace.heading.text(), "Page 2 — Same title")
        self.assertIn("Message B", self.workspace.editor.editor.toPlainText())
        self.assertEqual(self.workspace.selector.itemData(1), self.letter_a)

    def test_bound_named_style_and_paragraph_controls_survive_page_switch(self) -> None:
        from message_html import extract_lettersmith_style_state

        self.workspace.open_page(self.letter_a)
        self._type("Heading belongs to A")
        editor = self.workspace.editor
        editor.editor.selectAll()
        heading = next(action for action in editor.named_style_menu.actions()
                       if action.text() == "Heading 1")
        heading.trigger()
        editor.font_size_spin.setValue(27)
        editor.alignment_actions["center"].trigger()
        editor.spacing_actions[1.5].trigger()
        editor.act_number_list.trigger()
        self.assertTrue(self.workspace.save())
        authored = self.session.project.get_message(self.letter_a)["html"]
        style_state = extract_lettersmith_style_state(authored)
        self.assertIsNotNone(style_state)
        self.assertTrue(any(block.get("style") == "heading_1" for block in style_state["blocks"]))
        self.assertIn("<ol", authored)
        self.workspace.open_page(self.letter_b)
        self.assertNotEqual(self.workspace.editor.btn_named_style.text(), "Heading 1")
        self.assertIsNone(self.workspace.editor.editor.textCursor().currentList())
        self.workspace.open_page(self.letter_a)
        self.assertEqual(self.workspace.editor.btn_named_style.text(), "Heading 1")
        self.assertEqual(self._character_format().fontPointSize(), 27)
        self.assertTrue(self.workspace.editor.editor.textCursor().blockFormat().alignment() & QtCore.Qt.AlignHCenter)
        self.assertIsNotNone(self.workspace.editor.editor.textCursor().currentList())

    def test_images_selection_prefers_selected_letter_then_last_letter_then_first(self) -> None:
        self.session.select_page(self.letter_b)
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.session.select_page(self.illustration)
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.assertEqual(self.session.selected_page_id, self.illustration)
        self.session.project.delete_page(self.letter_b)
        self.assertEqual(self.workspace.page_id, self.letter_a)
        self.assertIn("Message A", self.workspace.editor.editor.toPlainText())

    def test_duplicate_edit_and_undo_do_not_modify_original_and_deleted_page_restores(self) -> None:
        self.workspace.open_page(self.letter_a)
        self._type("Original message")
        self.assertTrue(self.workspace.save())
        duplicate = self.session.project.duplicate_page(self.letter_a)
        self.assertTrue(self.workspace.open_page(duplicate))
        self.assertFalse(self.workspace.editor.editor.document().isUndoAvailable())
        self._type(" with duplicate edit", replace=False)
        self.assertEqual(self._plain(self.letter_a), "Original message")
        self.workspace.editor.editor.undo()
        self.assertTrue(self.workspace.save())
        self.assertEqual(self._plain(duplicate), "Original message")
        self._type("Independent duplicate")
        self.assertTrue(self.workspace.save())
        record = self.session.project.delete_page(duplicate)
        self.session.project.restore_page(record)
        self.workspace.open_page(duplicate)
        self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Independent duplicate")
        self.assertEqual(self._plain(self.letter_a), "Original message")

    def test_type_change_retains_message_and_no_letter_state_can_add_or_reset(self) -> None:
        self.workspace.open_page(self.letter_a)
        self._type("Retained when converted")
        self.assertTrue(self.workspace.save())
        self.session.project.change_page_type(self.letter_a, "illustration")
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.session.project.change_page_type(self.letter_a, "letter")
        self.workspace.open_page(self.letter_a)
        self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Retained when converted")
        self.session.replace(create_project(["cover", "illustration", "end_page"]))
        self.assertIsNone(self.workspace.page_id)
        self.assertIsNone(self.workspace.editor)
        self.assertTrue(self.workspace.empty.isVisible())
        self.assertFalse(self.workspace.selector.isEnabled())
        self.workspace.add_button.click()
        self.assertIsNotNone(self.workspace.page_id)
        self.assertEqual(self.workspace.editor.editor.toPlainText(), "")
        self.assertTrue(self.workspace.save())
        reset_project_session(self.root)
        self.assertEqual(self.workspace.selector.count(), 1)
        self.assertEqual(self.workspace.editor.editor.toPlainText(), "")
        self.assertNotIn(self.workspace.page_id, self.ids)

    def test_clear_only_removes_selected_text_and_is_undoable(self) -> None:
        self.workspace.open_page(self.letter_a)
        before = self.session.project.get_page(self.letter_a)
        other = self.session.project.get_message(self.letter_b)
        with mock.patch("messages_workspace.LetterSmithConfirmationDialog") as confirmation:
            confirmation.return_value.choice = "primary"
            self.workspace.clear_button.click()
        self.assertEqual(self._plain(self.letter_a), "")
        page = self.session.project.get_page(self.letter_a)
        self.assertEqual(page["artwork"], before["artwork"])
        self.assertEqual(page["prompt"], before["prompt"])
        self.assertEqual(self.session.project.get_message(self.letter_b), other)
        self.workspace.editor.editor.undo()
        self.assertTrue(self.workspace.save())
        self.assertIn("Message A", self._plain(self.letter_a))

    def test_import_replace_append_cancel_and_delayed_selection_keep_original_target(self) -> None:
        source = self.profile / "import.html"
        self.workspace.open_page(self.letter_a)
        before = self.session.project.get_message(self.letter_a)
        self.assertIsNone(self.workspace.capture_import(source, mode="cancel"))
        self.assertEqual(self.session.project.get_message(self.letter_a), before)
        target = self.workspace.capture_import(source, mode="replace")
        self.workspace.open_page(self.letter_b)
        other = self.session.project.get_message(self.letter_b)
        self.assertTrue(self.workspace.finish_import(source, "<p><i>Imported first</i></p>", target))
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.assertEqual(self._plain(self.letter_a), "Imported first")
        self.assertEqual(self.session.project.get_message(self.letter_b), other)
        self.workspace.open_page(self.letter_a)
        self.assertTrue(self._character_format().fontItalic())
        target = self.workspace.capture_import(source, mode="append")
        self.assertTrue(self.workspace.finish_import(source, "<p><u>Imported second</u></p>", target))
        self.assertIn("Imported first", self._plain(self.letter_a))
        self.assertIn("Imported second", self._plain(self.letter_a))
        self.assertTrue(self._character_format().fontItalic())

    def test_stale_import_requires_review_and_never_crosses_project_sessions(self) -> None:
        source = self.profile / "import.html"
        self.workspace.open_page(self.letter_a)
        target = self.workspace.capture_import(source, mode="replace")
        self._type("Newer edit")
        self.assertFalse(self.workspace.finish_import(source, "<p>Older import</p>", target))
        self.assertFalse(self.workspace.apply_reviewed_import())
        self.assertEqual(self._plain(self.letter_a), "Newer edit")
        self.assertTrue(self.workspace.review_button.isVisible())
        self.assertTrue(self.workspace.apply_reviewed_import(resolve_conflict=True))
        self.assertEqual(self._plain(self.letter_a), "Older import")
        target = self.workspace.capture_import(source, mode="replace")
        replacement = five_page_project()
        self.session.replace(replacement)
        original = self.session.project.to_dict()
        self.assertFalse(self.workspace.finish_import(source, "<p>Wrong project</p>", target))
        self.assertFalse(self.workspace.apply_reviewed_import(resolve_conflict=True))
        self.assertEqual(self.session.project.to_dict(), original)

    def test_imported_embedded_resource_survives_canonical_save_and_reopen(self) -> None:
        import io
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (3, 3), "red").save(buffer, "PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        self.workspace.open_page(self.letter_b)
        source = self.profile / "images.html"
        target = self.workspace.capture_import(source, mode="replace")
        self.assertTrue(self.workspace.finish_import(
            source, f'<p>Picture on B</p><img src="data:image/png;base64,{encoded}">', target
        ))
        self.assertTrue(self.workspace.save())
        stored = self.session.project.get_message(self.letter_b)
        self.assertEqual(len(stored["resources"]), 1)
        reference = stored["resources"][0]
        self.assertEqual((self.session.asset_root / reference).read_bytes(), buffer.getvalue())
        self.assertTrue(self.tab.shutdown())
        self.tab.hide()
        self.tab.deleteLater()
        self.tab = None
        self.app.processEvents()
        release_project_session(self.root)
        self.session = get_project_session(self.root)
        self.session.select_page(self.letter_b)
        self._open_tab()
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.assertEqual(self.session.project.get_message(self.letter_b), stored)
        self.assertIn("Picture on B", self.workspace.editor.editor.toPlainText())
        self.assertEqual((self.session.asset_root / reference).read_bytes(), buffer.getvalue())

    def test_readability_is_page_local_with_explicit_project_inheritance(self) -> None:
        self.workspace.open_page(self.letter_a)
        original_a = self.session.project.get_message(self.letter_a)
        self.session.project.update_shared_settings({"message_overlay_preset": "white", "message_overlay_opacity": 75})
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.workspace.preset.setCurrentIndex(self.workspace.preset.findData("black"))
        self.workspace.opacity.setValue(42)
        self.assertEqual(self.workspace.style_scope.text(), "Customized for this page")
        self.workspace.open_page(self.letter_b)
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.assertFalse(self.workspace.default_button.isEnabled())
        self.workspace.open_page(self.letter_a)
        self.assertEqual(self.workspace.opacity.value(), 42)
        self.workspace.default_button.click()
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.assertEqual(self.workspace.style_scope.text(), "Using project text style")
        self.assertEqual(self.session.project.get_message(self.letter_a), original_a)

    def test_preview_uses_selected_letters_text_and_artwork_and_handles_missing_background(self) -> None:
        reference_a = self._background(self.letter_a, "red")
        reference_b = self._background(self.letter_b, "blue")
        self.workspace.open_page(self.letter_b)
        self.workspace.render_preview()
        timer = QtCore.QElapsedTimer()
        timer.start()
        while self.workspace.preview_label.pixmap().isNull() and timer.elapsed() < 10_000:
            self.app.processEvents()
            QtTest.QTest.qWait(20)
        self.assertFalse(self.workspace.preview_label.pixmap().isNull(), self.workspace.preview_note.text())
        self.assertEqual(self.workspace.preview_page_id, self.letter_b)
        self.assertEqual(self.workspace.preview_background, reference_b)
        self.assertNotEqual(self.workspace.preview_background, reference_a)
        self.assertIn("Message B", self.workspace.preview_html)
        self.assertNotIn("Message A", self.workspace.preview_html)
        self.session.project.update_artwork(self.letter_b, {"asset": "missing-background.png"})
        self.workspace.render_preview()
        self.assertIn("No background artwork", self.workspace.preview_label.text())
        self._type("Editable without background")
        self.assertTrue(self.workspace.save())
        self.assertEqual(self._plain(self.letter_b), "Editable without background")

    def test_save_failure_keeps_draft_and_blocks_page_switch_until_retry(self) -> None:
        self.workspace.open_page(self.letter_a)
        self._type("Keep this unsaved draft")
        with mock.patch.object(self.session, "save", side_effect=OSError("Synthetic write failure")):
            self.assertFalse(self.workspace.open_page(self.letter_b))
        self.assertEqual(self.workspace.page_id, self.letter_a)
        self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Keep this unsaved draft")
        self.assertIn("Could not save", self.workspace.notice.text())
        self.assertTrue(self.workspace.save())
        self.assertTrue(self.workspace.open_page(self.letter_b))
        self.assertEqual(self._plain(self.letter_a), "Keep this unsaved draft")

    def test_long_unicode_letter_reports_fixed_preview_overflow_without_truncation(self) -> None:
        from project_repository import active_project_repository

        self._background(self.letter_a, "white")
        self.workspace.open_page(self.letter_a)
        text = "\n".join(
            f"{index}: Unicode 雪 café e\u0301 😀 — preserved ending."
            for index in range(50)
        )
        editor = self.workspace.editor.editor
        editor.selectAll()
        editor.insertPlainText(text)
        editor.selectAll()
        self.workspace.editor.font_size_spin.setValue(36)
        self.assertTrue(self.workspace.save())
        original = self.session.project.get_message(self.letter_a)
        self.workspace.render_preview()
        timer = QtCore.QElapsedTimer()
        timer.start()
        while ("Text exceeds this fixed preview" not in self.workspace.preview_note.text()
               and timer.elapsed() < 15_000):
            self.app.processEvents()
            # The Python renderer needs the GIL while the GUI remains idle.
            time.sleep(0.02)
        self.assertIn("Text exceeds this fixed preview", self.workspace.preview_note.text())
        self.assertFalse(self.workspace.preview_label.pixmap().isNull())
        self.assertEqual(self._plain(self.letter_a), text)
        self.workspace.zoom.setValue(175)
        self.assertEqual(self.session.project.get_message(self.letter_a), original)
        stored = active_project_repository(self.root).load()
        self.assertEqual(stored.project.get_message(self.letter_a), original)

    def test_preview_reports_missing_embedded_resource_without_removing_message(self) -> None:
        self._background(self.letter_a, "white")
        self.workspace.open_page(self.letter_a)
        missing = "gallery/message_assets/missing-inline.png"
        document = self.session.project.get_message(self.letter_a)
        document["html"] = f'<p>Keep the missing image reference.</p><img src="{missing}">'
        document["resources"] = [missing]
        self.session.project.update_message(self.letter_a, document)
        self.workspace.render_preview()
        self.assertIn("Missing embedded resource", self.workspace.preview_note.text())
        self.assertIn(missing, self.workspace.preview_note.text())
        self.assertEqual(self.session.project.get_message(self.letter_a), document)
        self.assertIn("Keep the missing image reference.", self.workspace.editor.editor.toPlainText())

    def test_dirty_replaced_session_retains_draft_and_blocks_style_changes(self) -> None:
        self.workspace.open_page(self.letter_a)
        original_text = self._plain(self.letter_a)
        replacement = type(self.session.project).from_dict(self.session.project.to_dict())
        self._type("Draft retained after external replacement")
        editor = self.workspace.editor
        self.assertTrue(self.workspace._dirty)
        self.session.replace(replacement)
        original = self.session.project.to_dict()
        self.assertIs(self.workspace.editor, editor)
        self.assertFalse(self.workspace._save_timer.isActive())
        self.assertIn("Retained draft from previous project", self.workspace.heading.text())
        self.workspace.preset.setCurrentIndex(self.workspace.preset.findData("black"))
        self.workspace.opacity.setValue(31)
        self.workspace.use_project_style()
        self.assertFalse(self.workspace.save())
        self.assertEqual(self.session.project.to_dict(), original)
        self.assertEqual(editor.editor.toPlainText().strip(), "Draft retained after external replacement")
        with mock.patch("messages_workspace.LetterSmithConfirmationDialog") as confirmation:
            confirmation.return_value.choice = "primary"
            self.workspace.discard_draft()
        self.assertEqual(self._plain(self.letter_a), original_text)

    def test_deferred_import_review_remains_accessible_without_letter_pages(self) -> None:
        source = self.profile / "deferred.html"
        target = self.workspace.capture_import(source, mode="replace")
        self.session.replace(create_project(["cover", "illustration", "end_page"]))
        original = self.session.project.to_dict()
        self.assertFalse(self.workspace.finish_import(source, "<p>Retained import to copy</p>", target))
        self.assertIsNone(self.workspace.page_id)
        self.assertTrue(self.workspace.empty.isVisible())
        self.assertFalse(self.workspace.actions.isVisible())
        self.assertTrue(self.workspace.review_button.isVisible())
        inspected = []

        def inspect_review(dialog):
            view = dialog.findChild(QtWidgets.QTextEdit)
            inspected.append(view.toPlainText())
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
            apply_button = next(button for button in buttons.buttons()
                                if buttons.buttonRole(button) == QtWidgets.QDialogButtonBox.AcceptRole)
            self.assertFalse(apply_button.isEnabled())
            return QtWidgets.QDialog.Rejected

        with mock.patch.object(QtWidgets.QDialog, "exec", inspect_review):
            self.workspace.review_button.click()
        self.assertEqual(inspected, ["Retained import to copy"])
        self.assertEqual(self.session.project.to_dict(), original)

    def test_external_message_update_retains_pending_draft_without_overwriting_newer_content(self) -> None:
        self.workspace.open_page(self.letter_a)
        self._type("Pending local text to recover")
        editor = self.workspace.editor
        updated = self.session.project.get_message(self.letter_a)
        updated["html"] = "<p>Newer external text</p>"
        self.session.project.update_message(self.letter_a, updated)
        self.assertIs(self.workspace.editor, editor)
        self.assertFalse(self.workspace._save_timer.isActive())
        self.assertIn("Retained draft", self.workspace.heading.text())
        self.assertEqual(editor.editor.toPlainText().strip(), "Pending local text to recover")
        self.assertFalse(self.workspace.save())
        self.assertFalse(self.workspace.open_page(self.letter_b))
        self.assertEqual(self.session.project.get_message(self.letter_a), updated)
        with mock.patch("messages_workspace.LetterSmithConfirmationDialog") as confirmation:
            confirmation.return_value.choice = "primary"
            self.workspace.discard_draft()
        self.assertEqual(self.workspace.editor.editor.toPlainText().strip(), "Newer external text")

    def test_canonical_disk_sync_restores_identity_locks_without_rewriting_message(self) -> None:
        from Message_tab import PUBLISHED_PAGE_URL_KEY

        self.workspace.open_page(self.letter_a)
        original = self.session.project.get_message(self.letter_a)
        self.tab.settings_store.update_fields({
            "recipient_title": "Saved letter title",
            "recipient_name": "Saved recipient",
            PUBLISHED_PAGE_URL_KEY: "https://example.com/saved-letter",
        })
        self.tab.sync_from_disk(lock_loaded_identity=True)
        for field, expected in (
            (self.tab.title_input, "Saved letter title"),
            (self.tab.name_input, "Saved recipient"),
            (self.tab.url_input, "https://example.com/saved-letter"),
        ):
            self.assertEqual(field.text(), expected)
            self.assertTrue(field.isReadOnly())
        self.assertEqual(self.session.project.get_message(self.letter_a), original)
        self.assertEqual(self.workspace.editor.get_edited_html(), original["html"])


if __name__ == "__main__":
    unittest.main()
