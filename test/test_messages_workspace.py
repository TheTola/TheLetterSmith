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
from test_project_model import canvas_layout, five_page_project


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
        self.paths.initialize()
        self.root = self.paths.workspace_root
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

    def _wait_for(self, condition, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.02)
        self.fail(self.workspace.preview_note.text())

    def _layout_dialog(self):
        from message_layout_dialog import MessageLayoutDialog
        self.assertTrue(self.workspace.save())
        identity = self.workspace.page_id
        target = self.session.capture_target(identity, fields=("type", "message", "artwork", "design_overrides"))
        dialog = MessageLayoutDialog(
            self.workspace, self.session.project.get_page(identity), self.session.project.resolve_settings(identity),
            self.session.project.to_dict()["shared_settings"], self.workspace.binding.label,
            lambda geometry, **options: self.workspace.apply_layout(target, geometry, **options),
        )
        return dialog

    def _plain(self, page_id: str) -> str:
        document = QtGui.QTextDocument()
        document.setHtml(self.session.project.get_message(page_id)["html"])
        return document.toPlainText().strip()

    def _character_format(self) -> QtGui.QTextCharFormat:
        cursor = QtGui.QTextCursor(self.workspace.editor.editor.document())
        cursor.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def _set_html(self, page_id, html):
        document = self.session.project.get_message(page_id)
        self.session.project.update_message(page_id, {**document, "html": html})

    def _interact_with_dialog(self, open_dialog, interact):
        failures = []

        def visit():
            dialog = self.app.activeModalWidget() or self.app.activePopupWidget()
            try:
                self.assertIsNotNone(dialog)
                interact(dialog)
            except BaseException as error:
                failures.append(error)
                if isinstance(dialog, QtWidgets.QDialog):
                    dialog.reject()
                elif dialog is not None:
                    dialog.close()

        QtCore.QTimer.singleShot(0, visit)
        open_dialog()
        if failures:
            raise failures[0]

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
                         ["Letter 1", "Letter 2"])
        self.session.project.rename_page(self.letter_a, "Same title")
        self.session.project.rename_page(self.letter_b, "Same title")
        order = list(self.ids)
        order[1], order[3] = order[3], order[1]
        self.session.project.reorder_pages(order)
        self.assertEqual([self.workspace.selector.itemText(i) for i in range(2)],
                         ["Same title", "Same title"])
        self.workspace.selector.setCurrentIndex(0)
        self.assertEqual(self.workspace.page_id, self.letter_b)
        self.assertEqual(self.workspace.heading.text(), "Same title")
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
            self.workspace.clear_text()
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
        original_text = self._plain(self.letter_a)
        self.session.project.update_shared_settings({"message_overlay_preset": "white", "message_overlay_opacity": 75})
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.workspace.preset.setCurrentIndex(self.workspace.preset.findData("black"))
        self.workspace.opacity.setValue(42)
        self.assertEqual(self.workspace.main_style_panel.preset.currentData(), "black")
        self.workspace.open_page(self.letter_b)
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.assertFalse(hasattr(self.workspace, "default_button"))
        self.workspace.open_page(self.letter_a)
        self.assertEqual(self.workspace.opacity.value(), 42)
        self.workspace.use_project_style()
        self.assertEqual(self.workspace.preset.currentData(), "white")
        self.assertEqual(self.workspace.opacity.value(), 75)
        self.assertEqual(self._plain(self.letter_a), original_text)

    def test_paper_colors_preserve_direct_formatting_and_survive_reopen(self):
        from letter_page import LETTER_PAGE_PRESETS
        workspace = self.workspace
        workspace.open_page(self.letter_a)
        editor = workspace.editor
        editor.editor.selectAll()
        editor._apply_named_style("normal_text")
        editor.set_paper_text_color("title", QtGui.QColor("#996644"))
        cursor = editor.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(2, QtGui.QTextCursor.KeepAnchor)
        editor.editor.setTextCursor(cursor)
        custom = QtGui.QTextCharFormat()
        custom.setForeground(QtGui.QColor("#c03040"))
        custom.setFontItalic(True)
        editor.named_styles.apply_direct_format(custom)
        editor.flush_document()
        workspace.preset.setCurrentIndex(workspace.preset.findData("black"))
        self.assertEqual(editor.named_styles.active_set.get("normal_text").font_color,
                         LETTER_PAGE_PRESETS["black"].default_ink)
        self.assertEqual(editor.named_styles.active_set.get("title").font_color, "#996644")
        self.assertEqual(editor.editor.textCursor().charFormat().foreground().color().name(), "#c03040")
        self.assertTrue(editor.editor.textCursor().charFormat().fontItalic())
        editor.set_paper_text_color("title", QtGui.QColor("#aabbcc"))
        workspace.preset.setCurrentIndex(workspace.preset.findData("paper"))
        self.assertEqual(editor.named_styles.active_set.get("title").font_color, "#996644")
        self.assertTrue(workspace.save())
        workspace.open_page(self.letter_b)
        workspace.open_page(self.letter_a)
        workspace.preset.setCurrentIndex(workspace.preset.findData("black"))
        self.assertEqual(workspace.editor.named_styles.active_set.get("title").font_color, "#aabbcc")
        self.assertTrue(workspace.save())
        self.assertTrue(self.tab.shutdown())
        self.tab.hide()
        self.tab.deleteLater()
        self.app.processEvents()
        release_project_session(self.root)
        self.session = get_project_session(self.root)
        self._open_tab()
        self.workspace.open_page(self.letter_a)
        self.assertEqual(self.workspace.preset.currentData(), "black")
        self.assertEqual(self.workspace.editor.named_styles.active_set.get("title").font_color, "#aabbcc")

    def test_editor_identity_is_compact_visible_and_title_tracks_page_name(self):
        workspace = self.workspace
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        self.app.processEvents()
        self.session.project.rename_page(self.letter_a, "A Letter to Anavi Grace")
        self.assertEqual(workspace.editor_window.windowTitle(),
                         "LetterSmith Editor · A Letter to Anavi Grace")
        self.assertTrue(self.tab.title_recipient_container.isVisible())
        self.assertEqual(self.tab.title_input.y(), self.tab.name_input.y())
        self.assertGreater(self.tab.url_input.y(), self.tab.title_input.y())
        self.assertLess(self.tab.title_input.height(), 45)
        self.assertTrue(workspace.style_panel.isVisible())
        self.assertTrue(workspace.main_style_panel.isVisible())
        self.assertTrue(workspace.editor_window.isAncestorOf(workspace.editor_host))
        self.assertFalse(workspace.editor_window.isAncestorOf(workspace.preview_panel))
        self.assertTrue(workspace.splitter.isAncestorOf(workspace.preview_panel))
        self.assertTrue(workspace.preview_panel.isVisible())
        self.assertFalse(any(button.text() in ("Import Text", "Clear Text", "Text Layout",
                                              "Readability", "Use project default")
                             for button in workspace.editor_window.findChildren(QtWidgets.QPushButton)))
        workspace.main_style_panel.opacity.setValue(83)
        self.assertEqual(workspace.opacity.value(), 83)
        workspace.preset.setCurrentIndex(workspace.preset.findData("clear"))
        self.assertEqual(workspace.opacity.value(), 0)
        self.assertFalse(workspace.opacity.isEnabled())
        workspace.main_style_panel.preset.setCurrentIndex(
            workspace.main_style_panel.preset.findData("paper"))
        self.assertEqual(workspace.opacity.value(), 83)
        self.assertTrue(workspace.opacity.isEnabled())
        self.assertLessEqual(workspace.selector.width(), 320)
        workspace.style_panel.zoom.setValue(125)
        self.assertEqual(workspace.main_style_panel.zoom.value(), 125)
        self.assertEqual(workspace.main_style_panel.preset.currentText(), "Warm Paper")
        self.assertFalse(workspace.main_style_panel.color.isVisible())
        self.assertFalse(workspace.style_panel.opacity.isVisible())
        self.assertFalse(workspace.style_panel.contrast.isVisible())
        self.assertEqual(workspace.style_panel.color.text(), "Normal Text Color")
        self.assertFalse(hasattr(workspace, "background_button"))

    def test_new_blank_letter_remembers_paper_without_restyling_other_letters(self):
        self.workspace.open_page(self.letter_a)
        original_b = self.session.project.get_page(self.letter_b)
        self.workspace.preset.setCurrentIndex(self.workspace.preset.findData("black"))
        self.workspace.opacity.setValue(86)
        self.workspace.add_letter()
        self.assertNotIn(self.workspace.page_id, self.ids)
        self.assertEqual(self.workspace.preset.currentData(), "black")
        self.assertEqual(self.workspace.opacity.value(), 86)
        self.assertEqual(self.session.project.get_page(self.letter_b), original_b)

    def test_preview_uses_selected_letters_text_and_artwork_and_handles_missing_background(self) -> None:
        reference_a = self._background(self.letter_a, "red")
        reference_b = self._background(self.letter_b, "blue")
        self.workspace.open_page(self.letter_b)
        self.workspace.render_preview()
        self._wait_for(lambda: self.workspace.shared_preview.viewed_page_id == self.letter_b)
        self.assertTrue(self.workspace.shared_preview.view.isVisible())
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

    def test_long_unicode_letter_reports_scrollable_overflow_without_truncation(self) -> None:
        from project_repository import active_project_repository

        self._background(self.letter_a, "white")
        self.workspace.open_page(self.letter_a)
        self.workspace.apply_layout(self.session.capture_target(self.letter_a), canvas_layout())
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
        self._wait_for(lambda: "scrollable text area" in self.workspace.preview_note.text())
        self.assertTrue(self.workspace.shared_preview.view.isVisible())
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
        self._wait_for(lambda: "Message resource is missing" in self.workspace.preview_note.text())
        self.assertIn(missing, self.workspace.preview_note.text())
        self.assertEqual(self.session.project.get_message(self.letter_a), document)
        self.assertIn("Keep the missing image reference.", self.workspace.editor.editor.toPlainText())

    def test_layout_controls_preserve_partial_inheritance_and_other_pages(self):
        self.session.project.update_shared_settings({"message_layout": canvas_layout()})
        self.workspace.open_page(self.letter_a)
        original = self.session.project.get_page(self.letter_a)
        other = self.session.project.get_page(self.letter_b)
        dialog = self._layout_dialog()
        self.assertEqual(dialog.scope.text(), "Using project text layout")
        dialog.custom.setChecked(True)
        dialog.values[("box", "x")].setValue(60)
        dialog.values[("box", "width")].setValue(280)
        dialog.values[("padding", "left")].setValue(18)
        dialog.alignment.setCurrentIndex(dialog.alignment.findData("right"))
        dialog.apply_button.click()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
        authored = self.session.project.get_message(self.letter_a)
        self.assertEqual(authored["layout"], {"box": {"x": 60, "width": 280}, "padding": {"left": 18}, "text_align": "right"})
        self.assertEqual(authored["html"], original["message"]["html"])
        self.assertEqual(self.session.project.get_page(self.letter_b), other)
        self.assertEqual(self.session.project.get_page(self.letter_a)["artwork"], original["artwork"])
        self.assertEqual(self.session.project.get_page(self.letter_a)["prompt"]["text"], original["prompt"]["text"])
        defaults = canvas_layout()
        defaults["box"]["y"] = 200
        self.session.project.update_shared_settings({"message_layout": defaults})
        resolved = self.session.project.resolve_layout(self.letter_a)["message"]
        self.assertEqual((resolved["box"]["x"], resolved["box"]["y"]), (60, 200))
        self.assertEqual(self.session.project.get_message(self.letter_a), authored)

    def test_layout_default_reset_clears_only_text_layout_overrides(self):
        self.session.project.update_shared_settings({"message_layout": canvas_layout()})
        self.workspace.open_page(self.letter_a)
        self.session.project.update_page_settings(self.letter_a, {"message_layout": None, "message_overlay_opacity": 31})
        self.workspace.apply_layout(self.session.capture_target(self.letter_a), canvas_layout())
        dialog = self._layout_dialog()
        dialog.default_button.click()
        dialog.apply_button.click()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
        page = self.session.project.get_page(self.letter_a)
        self.assertEqual(page["message"]["layout"], {})
        self.assertNotIn("message_layout", page["design_overrides"])
        self.assertEqual(page["design_overrides"]["message_overlay_opacity"], 31)
        self.assertEqual(self.session.project.resolve_layout(self.letter_a)["message"], canvas_layout())

    def test_layout_dialog_rejects_bounds_and_padding_without_replacing_document(self):
        self.workspace.open_page(self.letter_a)
        original = self.session.project.to_dict()
        dialog = self._layout_dialog()
        dialog.custom.setChecked(True)
        dialog.values[("box", "x")].setValue(100)
        dialog.apply_button.click()
        self.assertIn("fit inside", dialog.error.text())
        self.assertEqual(self.session.project.to_dict(), original)
        dialog.values[("box", "x")].setValue(0)
        dialog.values[("padding", "left")].setValue(400)
        dialog.apply_button.click()
        self.assertIn("positive space", dialog.error.text())
        self.assertEqual(self.session.project.to_dict(), original)

    def test_layout_dialog_rejects_changed_inputs_and_replaced_session(self):
        self.workspace.open_page(self.letter_a)
        dialog = self._layout_dialog()
        dialog.custom.setChecked(True)
        self._type("Newer document")
        changed = self.session.project.to_dict()
        dialog.apply_button.click()
        self.assertIn("changed while Text Layout", dialog.error.text())
        self.assertEqual(self.session.project.to_dict(), changed)
        self.assertTrue(self.workspace.save())
        dialog = self._layout_dialog()
        dialog.custom.setChecked(True)
        replacement = type(self.session.project).from_dict(self.session.project.to_dict())
        self.session.replace(replacement)
        changed = self.session.project.to_dict()
        dialog.apply_button.click()
        self.assertIn("changed while Text Layout", dialog.error.text())
        self.assertEqual(self.session.project.to_dict(), changed)

    def test_layout_survives_retype_duplicate_reorder_and_repository_reload(self):
        from project_repository import active_project_repository
        self.workspace.open_page(self.letter_a)
        self.workspace.apply_layout(self.session.capture_target(self.letter_a), canvas_layout())
        original = self.session.project.get_message(self.letter_a)
        duplicate = self.session.project.duplicate_page(self.letter_a)
        self.workspace.open_page(duplicate)
        dialog = self._layout_dialog()
        dialog.values[("box", "x")].setValue(20)
        dialog.apply_button.click()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted, dialog.error.text())
        self.assertEqual(self.session.project.get_message(self.letter_a), original)
        self.session.project.change_page_type(self.letter_a, "illustration")
        self.session.project.change_page_type(self.letter_a, "letter")
        self.session.project.reorder_pages(list(reversed([p["id"] for p in self.session.project.pages])))
        self.workspace.open_page(self.letter_a)
        self._type("Text after geometry", replace=False)
        self.assertTrue(self.workspace.save())
        stored = active_project_repository(self.root).load()
        self.assertEqual(stored.project.get_message(self.letter_a)["layout"], original["layout"])
        self.assertIn("Text after geometry", stored.project.get_message(self.letter_a)["html"])
        self.assertEqual(stored.project.resolve_layout(duplicate)["message"]["box"]["x"], 20)

    def test_layout_open_cancel_preserves_unknown_data_and_readiness_targets_letter(self):
        from message_layout_dialog import MessageLayoutDialog
        self.tab.focus_field(self.letter_b)
        self.assertEqual(self.workspace.page_id, self.letter_b)
        document = self.session.project.get_message(self.letter_b)
        document["layout"] = {"historical": 17}
        self.session.project.update_message(self.letter_b, document)
        original = self.session.project.to_dict()
        inspected = []
        def cancel(dialog):
            inspected.append(dialog.error.text())
            return QtWidgets.QDialog.Rejected
        with mock.patch.object(MessageLayoutDialog, "exec", cancel):
            self.workspace.edit_layout()
        self.assertIn("Saved layout cannot be rendered", inspected[0])
        self.assertEqual(self.session.project.to_dict(), original)

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
            for mirror in field._identity_mirrors:
                self.assertEqual(mirror.text(), expected)
                self.assertTrue(mirror.isReadOnly())
        self.assertEqual(self.session.project.get_message(self.letter_a), original)
        self.assertEqual(self.workspace.editor.get_edited_html(), original["html"])

    def test_footer_zoom_accepts_percentage_and_both_arrow_buttons(self):
        workspace = self.workspace
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        for controls in (workspace.style_panel, workspace.main_style_panel):
            with self.subTest(editor=controls is workspace.style_panel):
                zoom = controls.zoom
                zoom.setFocus()
                zoom.selectAll()
                QtTest.QTest.keyClicks(zoom, "125%")
                workspace.readability_changed.emit()
                self.assertEqual(zoom.cleanText(), "125")
                QtTest.QTest.keyClick(zoom, QtCore.Qt.Key_Return)
                self.assertEqual(zoom.value(), 125)
                self.assertEqual(workspace.zoom.value(), 125)
                for subcontrol, expected in (
                    (QtWidgets.QStyle.SC_SpinBoxUp, 126),
                    (QtWidgets.QStyle.SC_SpinBoxDown, 125),
                ):
                    option = QtWidgets.QStyleOptionSpinBox()
                    zoom.initStyleOption(option)
                    rect = zoom.style().subControlRect(
                        QtWidgets.QStyle.CC_SpinBox, option, subcontrol, zoom)
                    QtTest.QTest.mouseClick(zoom, QtCore.Qt.LeftButton, pos=rect.center())
                    self.assertEqual(zoom.value(), expected)
                    self.assertEqual(workspace.zoom.value(), expected)
                    self.assertAlmostEqual(workspace.shared_preview.view.zoomFactor(), expected / 100)
                self.assertEqual(workspace.style_panel.zoom.value(), workspace.main_style_panel.zoom.value())

    def test_identity_views_edit_restore_and_reopen_without_stock_values(self):
        from Message_tab import PUBLISHED_PAGE_URL_KEY
        from project_repository import active_project_repository

        workspace = self.workspace
        workspace.open_page(self.letter_a)
        self.session.project.rename_page(self.letter_a, "")
        self.tab.project_state.establish_project("Synthetic recipient", custom_capitalization=True)
        self.session.synchronize_workspace()
        self.tab.settings_store.update_fields({
            "recipient_title": "Synthetic older letter",
            "recipient_name": "Synthetic recipient",
            PUBLISHED_PAGE_URL_KEY: "https://example.com/older-letter",
        })
        self.tab.sync_from_disk(lock_loaded_identity=True)
        workspace.open_editor()
        self.app.processEvents()
        self.assertEqual(workspace.editor_window.windowTitle(),
                         "LetterSmith Editor · Synthetic older letter")
        expected = {}
        for source, key, text in (
            (self.tab.title_input, "recipient_title", "Updated letter"),
            (self.tab.name_input, "recipient_name", "Updated recipient"),
            (self.tab.url_input, PUBLISHED_PAGE_URL_KEY, "https://example.com/updated"),
        ):
            mirror = source._identity_mirrors[0]
            QtTest.QTest.mouseDClick(mirror, QtCore.Qt.LeftButton)
            self.assertFalse(mirror.isReadOnly())
            self.assertTrue(mirror.hasFocus())
            mirror.selectAll()
            QtTest.QTest.keyClicks(mirror, text)
            QtTest.QTest.keyClick(mirror, QtCore.Qt.Key_Return)
            self.assertEqual(source.text(), text)
            self.assertEqual(self.tab.settings_store.snapshot()[key], text)
            expected[key] = text
        self.assertEqual(workspace.editor_window.windowTitle(), "LetterSmith Editor · Updated letter")
        self.assertTrue(workspace.save())
        self.session.synchronize_workspace()
        self.session.save(sync_workspace=False)
        saved = active_project_repository(self.root).load().project.to_dict()
        self.assertEqual(saved["metadata"]["recipient_title"], expected["recipient_title"])
        self.assertEqual(saved["metadata"]["recipient_name"], expected["recipient_name"])
        self.assertEqual(saved["publication"][PUBLISHED_PAGE_URL_KEY], expected[PUBLISHED_PAGE_URL_KEY])
        self.assertTrue(self.tab.shutdown())
        self.tab.hide()
        self.tab.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        release_project_session(self.root)
        self.session = get_project_session(self.root)
        self._open_tab()
        for source, key in ((self.tab.title_input, "recipient_title"),
                            (self.tab.name_input, "recipient_name"),
                            (self.tab.url_input, PUBLISHED_PAGE_URL_KEY)):
            self.assertEqual(source.text(), expected[key])
            self.assertEqual(source._identity_mirrors[0].text(), expected[key])
            source.setReadOnly(False)
            source.setText("Changed in Messages")
            self.assertEqual(source._identity_mirrors[0].text(), "Changed in Messages")
        self.tab.settings_store.update_fields({key: "" for key in expected})
        self.tab.sync_from_disk()
        for field in (self.tab.title_input, self.tab.name_input, self.tab.url_input):
            self.assertEqual(field.text(), "")
            self.assertEqual(field._identity_mirrors[0].text(), "")

    def test_explicit_name_update_rewrites_all_pages_and_preserves_rich_text(self):
        workspace = self.workspace
        self.tab.project_state.establish_project("Maya Michaels", custom_capitalization=True)
        self.tab.sync_from_disk()
        self._set_html(self.letter_a, '<p>🙂 <b>Maya</b> <i>Michaels</i> and Maya’s. Mayan.</p>')
        self._set_html(self.letter_b, '<p>Michaels’s note for MAYA. <a href="https://example.com/Maya">Link</a></p>')
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        self.tab.name_input.setText("Mia Michaels")
        self.assertTrue(self.tab._save_settings())
        self.assertIn("Maya Michaels", self._plain(self.letter_a))

        self.assertIn("Maya Michaels to Mia Michaels", workspace.update_name_button.toolTip())
        workspace.update_name_button.click()
        self.assertEqual(self._plain(self.letter_a), "🙂 Mia Michaels and Mia’s. Mayan.")

        def change_surname(dialog):
            old_name, new_name = dialog.findChildren(QtWidgets.QLineEdit)
            old_name.setText("Mia Michaels")
            new_name.setText("Mia Thomas")
            next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                 if button.text() == "Update Name").click()

        self._interact_with_dialog(workspace.update_name_button.click, change_surname)
        self.assertEqual(self._plain(self.letter_a), "🙂 Mia Thomas and Mia’s. Mayan.")
        self.assertEqual(self._plain(self.letter_b), "Thomas’s note for MIA. Link")
        editor = workspace.editor.editor
        document = editor.document()
        self.assertGreater(document.find("Mia").charFormat().fontWeight(), QtGui.QFont.Weight.Normal)
        self.assertTrue(document.find("Thomas").charFormat().fontItalic())
        self.assertIn('href="https://example.com/Maya"', self.session.project.get_message(self.letter_b)["html"])

    def test_letter_search_previews_matches_and_rejects_stale_results(self):
        workspace = self.workspace
        self._set_html(self.letter_a, "<p>🙂 <b>anchor</b></p>")
        self._set_html(self.letter_b, "<p>Anchor already here</p>")
        workspace.open_page(self.letter_a)
        workspace.open_editor()

        def replace(dialog):
            dialog.query.setText("anchor")
            dialog.replacement.setText("<changed>")
            self.assertFalse(dialog.replace_all.isEnabled())
            QtTest.QTest.keyClick(dialog.query, QtCore.Qt.Key_Return)
            self.assertEqual(dialog.results.topLevelItemCount(), 2)
            self._set_html(self.letter_b, "<p>Anchor edited elsewhere</p>")
            dialog.replace_all.click()
            self.assertIn("changed", dialog.status.text())
            self.assertEqual(self._plain(self.letter_a), "🙂 anchor")
            dialog.search()
            dialog.replace_all.click()
            dialog.reject()

        self._interact_with_dialog(workspace.editor.act_find.trigger, replace)
        self.assertEqual(self._plain(self.letter_a), "🙂 <changed>")
        self.assertEqual(self._plain(self.letter_b), "<changed> edited elsewhere")

    def test_copy_style_and_selection_actions_use_the_rich_text_editor(self):
        workspace = self.workspace
        self._set_html(self.letter_a, '<p style="line-height:185%;font-family:Arial;'
                                     'font-size:24pt;color:#933822">Source style</p>')
        self._set_html(self.letter_b, '<ul><li>Keep this emphasis and <b>list</b>.</li></ul>')
        workspace.open_page(self.letter_b)
        workspace.open_editor()
        workspace.copy_style_menu.aboutToShow.emit()
        workspace.copy_style_menu.actions()[0].trigger()
        editor = workspace.editor.editor
        block = editor.document().begin()
        self.assertEqual(block.blockFormat().lineHeight(), 185)
        self.assertIsNotNone(block.textList())
        style = editor.document().find("emphasis").charFormat()
        self.assertIn("Arial", style.fontFamilies())
        self.assertEqual(style.fontPointSize(), 24)
        self.assertEqual(style.foreground().color().name(), "#933822")
        self.assertGreater(editor.document().find("list").charFormat().fontWeight(), QtGui.QFont.Weight.Normal)
        editor.setTextCursor(editor.document().find("emphasis"))
        tools = workspace.editor.selection_actions
        self.assertTrue(tools.isVisible())
        buttons = {button.text(): button for button in tools.findChildren(QtWidgets.QToolButton)}
        self.assertEqual(set(buttons), {"List", "Link", "Pop-up Note"})
        self.assertEqual([action.text() for action in buttons["List"].menu().actions()],
                         ["Bullet list", "Numbered list", "", "Move List Item Up", "Move List Item Down"])
        with mock.patch("Editor.LetterSmithInputDialog.get_text", return_value=("https://example.com", True)):
            buttons["Link"].click()
        self.assertEqual(editor.document().find("emphasis").charFormat().anchorHref(), "https://example.com")
        editor.setTextCursor(editor.document().find("Keep"))

        def add_note(dialog):
            dialog.message_edit.setPlainText("A note for this phrase.")
            dialog.save_button.click()

        self._interact_with_dialog(buttons["Pop-up Note"].click, add_note)
        from message_html import ultralink_message_from_href
        href = editor.document().find("Keep").charFormat().anchorHref()
        self.assertEqual(ultralink_message_from_href(href), "A note for this phrase.")

    def test_whole_letter_proofreading_groups_dictionary_and_local_grammar(self):
        workspace = self.workspace
        self._set_html(self.letter_a, "<p>I saw teh moon.</p>")
        self._set_html(self.letter_b, "<p>We was lucky.</p>")
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        grammar = mock.Mock()
        grammar.ready.return_value = True

        def submit(_owner, text, *, context, protected_terms, callback):
            corrected = text.replace("was", "were")
            QtCore.QTimer.singleShot(0, lambda: callback(1, corrected, ""))

        grammar.submit.side_effect = submit

        def review(dialog):
            self._wait_for(lambda: "Checking" not in dialog.status.text())
            first, second = (dialog.results.topLevelItem(index) for index in range(2))
            self.assertIn("Page 2", first.text(0))
            self.assertIn("Page 4", second.text(0))
            self.assertIn("the", [first.child(index).text(1) for index in range(first.childCount())])
            suggestion = next(second.child(index) for index in range(second.childCount())
                              if second.child(index).text(1) == "were")
            dialog.results.setCurrentItem(suggestion)
            dialog.apply_button.click()
            dialog.reject()

        with mock.patch("language_ai.get_grammar_service", return_value=grammar):
            self._interact_with_dialog(workspace.proofread_letter_button.click, review)
        self.assertEqual(self._plain(self.letter_a), "I saw teh moon.")
        self.assertEqual(self._plain(self.letter_b), "We were lucky.")

    def test_single_microphone_stops_then_restarts_from_the_same_button(self):
        from test_dictation import _Speech

        workspace = self.workspace
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor
        editor.editor.moveCursor(QtGui.QTextCursor.End)
        manager = mock.Mock()
        manager.active = None
        manager.speech = _Speech()
        manager.ensure_model.return_value = True
        with mock.patch("dictation._manager", return_value=manager):
            editor.btn_dictate.click()
            first = manager.speech.callbacks[-1]
            first({"event": "ready"})
            self.assertEqual(editor.dictation_status.text(), "Listening…")
            editor.btn_dictate.click()
            self.assertEqual(manager.speech.stopped, ["session-1"])
            editor.btn_dictate.click()
            self.assertEqual(editor.dictation_status.text(), "Restart queued…")
            first({"event": "final", "text": "first phrase"})
            first({"event": "done"})
            second = manager.speech.callbacks[-1]
            self.assertIsNot(second, first)
            first({"event": "final", "text": "stale phrase"})
            second({"event": "ready"})
            self.assertEqual(editor.dictation_status.text(), "Listening…")
            editor.btn_dictate.click()
            second({"event": "done"})
            self.assertEqual(editor.dictation_status.text(), "")
            self.assertIn("first phrase", editor.editor.toPlainText())
            self.assertNotIn("stale phrase", editor.editor.toPlainText())

    def test_empty_indicator_and_autosaved_history_survive_reopening(self):
        from message_history import list_revisions

        workspace = self.workspace
        self._set_html(self.letter_a, "<p><br></p>")
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        self.assertTrue(workspace.selector.currentText().endswith(" · Empty"))
        path = workspace._draft_path(self.letter_a)
        self._type("First draft")
        self._wait_for(lambda: len(list_revisions(path)) >= 2)
        self.assertFalse(workspace.selector.currentText().endswith(" · Empty"))
        self._type("Second draft")
        self._wait_for(lambda: len(list_revisions(path)) >= 3)
        self.assertTrue(self.tab.shutdown())
        self.tab.hide()
        self.tab.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        release_project_session(self.root)
        self.session = get_project_session(self.root)
        self._open_tab()
        self.workspace.open_page(self.letter_a)
        self.workspace.open_editor()

        def restore(dialog):
            drafts = dialog.findChild(QtWidgets.QListWidget)
            drafts.setCurrentRow(1)
            self.assertEqual(dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText(), "First draft")
            next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                 if button.text() == "Restore Draft").click()

        self._interact_with_dialog(self.workspace.history_button.click, restore)
        self.assertEqual(self._plain(self.letter_a), "First draft")
        self.assertFalse(list_revisions(self.workspace._draft_path(self.letter_b)))

    def test_contrast_suggestion_and_device_preview_use_selected_letter(self):
        workspace = self.workspace
        self._background(self.letter_a, "#141414")
        self._set_html(self.letter_a, '<p style="color:#000000">Contrast test</p>')
        self.session.project.update_page_settings(self.letter_a, {"message_overlay_preset": "clear"})
        workspace.open_page(self.letter_a)
        workspace.render_preview()
        suggestion = workspace.main_style_panel.suggest_button
        self.assertTrue(suggestion.isVisible())
        suggestion.click()
        settings = self.session.project.resolve_settings(self.letter_a)
        self.assertIn(settings["message_overlay_preset"], ("paper", "white"))
        self.assertGreater(settings["message_overlay_opacity"], 0)
        preview = workspace.shared_preview
        self._wait_for(lambda: preview.viewed_page_id == self.letter_a)
        original = self.session.project.get_message(self.letter_a)
        for index, zoom, phone in ((1, 100, True), (2, 100, False), (1, 25, True)):
            workspace.preview_device.setCurrentIndex(index)
            workspace.zoom.setValue(zoom)
            results = []

            def device_matches():
                preview.view.page().runJavaScript("window.matchMedia('(max-width: 640px)').matches", results.append)
                self.app.processEvents()
                return any(result is phone for result in results)

            self._wait_for(device_matches)
            self.assertEqual(preview.viewed_page_id, self.letter_a)
            self.assertEqual(self.session.project.get_message(self.letter_a), original)

    def test_editor_settings_greetings_default_single_and_saved_choices(self):
        workspace = self.workspace
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor
        editor.recipient_name = "Mia Michaels"
        editor.act_salutation.trigger()
        self.assertTrue(editor.editor.toPlainText().startswith("Dear Mia Michaels,\n"))
        editor.act_undo.trigger()

        def save_greetings(text):
            def edit_settings(dialog):
                dialog.findChild(QtWidgets.QPlainTextEdit).setPlainText(text)
                dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Save).click()
            self._interact_with_dialog(editor.editor_settings_button.click, edit_settings)

        save_greetings("Hello <Mia>,")
        editor.act_salutation.trigger()
        self.assertTrue(editor.editor.toPlainText().startswith("Hello <Mia>,\n"))
        self.assertIn("Undo Insert salutation", editor.act_undo.toolTip())
        editor.act_undo.trigger()
        save_greetings("Hello again,\nWelcome back,")
        workspace.open_page(self.letter_b)
        reopened = workspace.editor

        def choose(dialog):
            choices = dialog.findChild(QtWidgets.QListWidget)
            self.assertEqual(choices.count(), 2)
            choices.setCurrentRow(1)
            next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                 if button.text() == "Insert").click()

        self._interact_with_dialog(reopened.act_salutation.trigger, choose)
        self.assertTrue(reopened.editor.toPlainText().startswith("Welcome back,\n"))
        reopened.editor.setTextCursor(reopened.editor.document().find("back"))
        QtTest.QTest.keyClicks(reopened.editor, "home")
        self.assertTrue(reopened.editor.toPlainText().startswith("Welcome home,\n"))

    def test_editor_rich_clipboard_plain_copy_and_undo_descriptions(self):
        workspace = self.workspace
        self._set_html(self.letter_a, '<p><b style="color:#914010">Ink</b></p><p>Next </p>')
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor
        edit = editor.editor
        clipboard = self.app.clipboard()
        self.addCleanup(clipboard.clear)
        edit.setTextCursor(edit.document().find("Ink"))
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
        self.assertTrue(clipboard.mimeData().hasHtml())
        self.assertEqual(clipboard.text(), "Ink")
        edit.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_V, QtCore.Qt.ControlModifier)
        pasted = edit.document().find("Ink", 3)
        self.assertEqual(pasted.charFormat().foreground().color().name(), "#914010")
        self.assertGreater(pasted.charFormat().fontWeight(), QtGui.QFont.Weight.Normal)
        self.assertIn("Undo Paste", editor.act_undo.toolTip())
        editor.act_undo.trigger()
        self.assertIn("Redo Paste", editor.act_redo.toolTip())
        editor.act_redo.trigger()

        edit.setTextCursor(edit.document().find("Ink"))
        point = edit.cursorRect().center()
        event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point,
                                        edit.viewport().mapToGlobal(point))

        def plain_copy(menu):
            next(action for action in menu.actions() if action.text() == "Copy Without Formatting").trigger()
            menu.close()

        self._interact_with_dialog(lambda: self.app.sendEvent(edit.viewport(), event), plain_copy)
        self.assertFalse(clipboard.mimeData().hasHtml())
        self.assertEqual(clipboard.text(), "Ink")
        rich = QtCore.QMimeData()
        rich.setHtml("<p><i>Mixed clipboard</i></p>")
        rich.setText("Mixed clipboard")
        rich.setImageData(QtGui.QImage(5, 5, QtGui.QImage.Format_RGB32))
        clipboard.setMimeData(rich)
        edit.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_V, QtCore.Qt.ControlModifier)
        self.assertTrue(edit.document().find("Mixed clipboard").charFormat().fontItalic())

    def test_editor_paragraph_previews_and_numbered_item_moves(self):
        workspace = self.workspace
        self._set_html(self.letter_a, '<p>First paragraph with words</p><p>Second paragraph</p>'
                       '<ol start="4"><li><b>Red</b><ul><li>Red child</li></ul></li>'
                       '<li><i>Blue</i><ol start="8"><li>Blue child</li></ol></li><li>Green</li></ol>')
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor
        edit = editor.editor
        edit.setTextCursor(edit.document().find("First paragraph"))
        original = edit.toHtml()
        steps = edit.document().availableUndoSteps()
        spacing = editor.spacing_actions[1.5]
        editor.btn_spacing.menu().aboutToShow.emit()
        editor.btn_spacing.menu().hovered.emit(spacing)
        self.assertEqual(editor.spacing_preview.toPlainText(), "First paragraph with words")
        self.assertEqual(editor.spacing_preview.document().begin().blockFormat().lineHeight(), 150)
        alignment = editor.alignment_actions["right"]
        editor.btn_align.menu().aboutToShow.emit()
        editor.btn_align.menu().hovered.emit(alignment)
        self.assertEqual(editor.alignment_preview.document().begin().blockFormat().alignment(),
                         QtCore.Qt.AlignRight)
        self.assertEqual(edit.toHtml(), original)
        self.assertEqual(edit.document().availableUndoSteps(), steps)
        spacing.trigger()
        alignment.trigger()
        self.assertEqual(edit.document().begin().blockFormat().lineHeight(), 150)
        self.assertEqual(edit.document().begin().next().blockFormat().lineHeight(), 0)
        self.assertEqual(edit.document().begin().blockFormat().alignment(), QtCore.Qt.AlignRight)
        self.assertIn("Undo Align right", editor.act_undo.toolTip())

        edit.setTextCursor(edit.document().find("Blue"))
        self.assertTrue(editor.list_move_actions[-1].isEnabled())
        editor.list_move_actions[-1].trigger()
        self.assertEqual(edit.toPlainText().splitlines()[-5:], ["Blue", "Blue child", "Red", "Red child", "Green"])
        blue = edit.textCursor().block()
        self.assertEqual(blue.textList().format().start(), 4)
        self.assertEqual(blue.textList().itemNumber(blue), 0)
        self.assertEqual(blue.next().textList().format().start(), 8)
        self.assertTrue(edit.textCursor().charFormat().fontItalic())
        editor.act_undo.trigger()
        self.assertIn("Redo Move List Item Up", editor.act_redo.toolTip())
        self.assertEqual(edit.toPlainText().splitlines()[-5:], ["Red", "Red child", "Blue", "Blue child", "Green"])
        editor.act_redo.trigger()
        edit.setTextCursor(edit.document().find("Blue"))
        editor.list_move_actions[1].trigger()
        self.assertEqual(edit.toPlainText().splitlines()[-5:], ["Red", "Red child", "Blue", "Blue child", "Green"])

    def test_editor_names_saved_styles_and_jumps_to_page_links(self):
        from message_html import make_ultralink_href

        workspace = self.workspace
        note_href = make_ultralink_href("A note for Mia")
        self._set_html(self.letter_a, '<p><a href="https://example.com"><b>Web</b> site</a>'
                       f' and <a href="{note_href}">Note</a></p>')
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor

        def enter_name(name):
            def interact(dialog):
                dialog.input.setText(name)
                dialog.accept_button.click()
            return interact

        self._interact_with_dialog(editor._style_save_actions[1].trigger, enter_name("Personal Letters"))
        original_styles = editor._style_slots.load(1)
        self._interact_with_dialog(editor._style_rename_actions[1].trigger, enter_name("Warm Letters"))
        self.assertEqual(editor._style_slots.load(1), original_styles)

        def jump_to_note(dialog):
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            groups = {tree.topLevelItem(index).text(0): tree.topLevelItem(index)
                      for index in range(tree.topLevelItemCount())}
            self.assertEqual(groups["Website links"].child(0).text(0), "Web site")
            note = groups["Ultra Links"].child(0)
            self.assertEqual(note.text(1), "A note for Mia")
            tree.setCurrentItem(note)
            next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                 if button.text() == "Jump to Link").click()

        self._interact_with_dialog(editor.act_page_links.trigger, jump_to_note)
        self.assertEqual(editor.editor.textCursor().selectedText(), "Note")
        workspace.open_page(self.letter_b)
        workspace.editor._refresh_named_style_menu()
        self.assertIn("Warm Letters", workspace.editor._style_slot_buttons[1].text())

    def test_editor_selection_only_replace_stays_in_scope_and_can_undo(self):
        workspace = self.workspace
        self._set_html(self.letter_a, "<p>aa 🙂 <b>aa aa</b> aa</p>")
        self._set_html(self.letter_b, "<p>aa on another page</p>")
        workspace.open_page(self.letter_a)
        workspace.open_editor()
        editor = workspace.editor
        editor.editor.setTextCursor(editor.editor.document().find("aa aa"))

        def replace_selection(dialog):
            self.assertTrue(dialog.selection_only.isEnabled())
            dialog.selection_only.setChecked(True)
            dialog.query.setText("aa")
            dialog.replacement.setText("long")
            dialog.search()
            self.assertEqual(dialog.results.topLevelItem(0).childCount(), 2)
            dialog.replace_all.click()
            dialog.query.setText("long")
            dialog.replacement.setText("x")
            dialog.search()
            self.assertEqual(dialog.results.topLevelItem(0).childCount(), 2)
            dialog.replace_all.click()
            dialog.reject()

        self._interact_with_dialog(editor.act_find.trigger, replace_selection)
        self.assertEqual(self._plain(self.letter_a), "aa 🙂 x x aa")
        self.assertEqual(self._plain(self.letter_b), "aa on another page")
        self.assertIn("Undo Replace text", editor.act_undo.toolTip())
        editor.act_undo.trigger()
        self.assertEqual(self._plain(self.letter_a), "aa 🙂 long long aa")
        editor.act_redo.trigger()
        self.assertEqual(self._plain(self.letter_a), "aa 🙂 x x aa")

    def test_editor_word_selection_contrast_and_offline_writing_shortcuts(self):
        from Editor import READABILITY_SELECTION_PROPERTY
        from language_service import LanguageIssue
        from proofreading import PROOFREADING_SELECTION_PROPERTY
        from test_dictation import _Speech

        workspace = self.workspace
        self._background(self.letter_a, "#171717")
        self._set_html(self.letter_a, '<p style="color:#191919">Dark words</p>'
                       '<p style="color:#ffffff">Bright words</p>')
        self.session.project.update_page_settings(self.letter_a, {"message_overlay_preset": "clear"})
        self.assertTrue(workspace.open_page(self.letter_a))
        self.assertTrue(workspace.open_editor())
        workspace.render_preview()
        editor = workspace.editor
        edit = editor.editor
        editor._proofreading.set_issues((LanguageIssue(0, 4, "Dark", "spelling", "First"),
                                       LanguageIssue(11, 17, "Bright", "spelling", "Second")))
        edit.setTextCursor(edit.document().find("Dark words"))
        self.assertIn("Selected: 2 words", editor.word_label.text())
        self._wait_for(lambda: any(item.format.property(READABILITY_SELECTION_PROPERTY)
                                   for item in edit.extraSelections()))
        low = [item.cursor.selectedText() for item in edit.extraSelections()
               if item.format.property(READABILITY_SELECTION_PROPERTY)]
        self.assertEqual(low, ["Dark words"])
        self.assertTrue(any(item.format.property(PROOFREADING_SELECTION_PROPERTY)
                            for item in edit.extraSelections()))
        edit.moveCursor(QtGui.QTextCursor.Start)
        edit.setFocus()
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_F7)
        self.assertEqual(edit.textCursor().selectedText(), "Dark")
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_F7)
        self.assertEqual(edit.textCursor().selectedText(), "Bright")
        QtTest.QTest.keyClick(edit, QtCore.Qt.Key_F7, QtCore.Qt.ShiftModifier)
        self.assertEqual(edit.textCursor().selectedText(), "Dark")

        manager = mock.Mock(active=None)
        manager.speech = _Speech()
        manager.ensure_model.return_value = True
        modifiers = QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier
        with mock.patch("dictation._manager", return_value=manager):
            QtTest.QTest.keyClick(edit, QtCore.Qt.Key_M, modifiers)
            self._wait_for(lambda: bool(manager.speech.callbacks))
            manager.speech.callbacks[0]({"event": "ready"})
            self.assertEqual(editor.dictation_status.text(), "Listening…")
            QtTest.QTest.keyClick(edit, QtCore.Qt.Key_M, modifiers)
            self._wait_for(lambda: manager.speech.stopped == ["session-1"])
            manager.speech.callbacks[0]({"event": "done"})
        self.assertEqual(editor.dictation_status.text(), "")


if __name__ == "__main__":
    unittest.main()
