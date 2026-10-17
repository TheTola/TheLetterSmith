import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from PromptWriterPanel import PromptWriterPanel
from project_model import PAGE_TYPES, create_project
from project_repository import active_project_repository
from project_session import get_project_session, release_project_session
from prompt_writer_project import PromptBatch, compose_page
from test_project_model import five_page_project


class PromptWriterPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        modules = self.root / 'Prompter/modules'
        modules.mkdir(parents=True)
        for name, text in {'topic': 'Forest', 'type': 'Ink', 'color': '-Basic\nRuby',
                           'role': 'a director of four-page sets', 'order': 'Create a scene of',
                           'format': 'Portrait image at 2048×3072 pixels. Keep safe margins.',
                           'effort': 'use coherent geometry and clear materials'}.items():
            (modules / (name + '.txt')).write_text(text + '\n', encoding='utf-8')
        project = five_page_project()
        for index, page in enumerate(project.pages):
            path = self.root / page['artwork']['asset']
            path.parent.mkdir(exist_ok=True)
            Image.new('RGB', (8, 12), 'navy').save(path)
            if page['type'] == 'letter':
                message = page['message']
                project.rename_page(page['id'], 'Letter A' if index == 1 else 'Letter B')
                message['layout'] = {'units': 'normalized', 'x': .1 if index == 1 else .55,
                                     'y': .2, 'width': .35, 'height': .6}
                project.update_message(page['id'], message)
        project.update_project_data('prompt_settings', {'subject': 'Forest', 'type': 'Ink', 'color': 'Ruby'})
        project.update_project_data('presentation', {'artwork_output': {'width': 1600, 'height': 900}})
        self.session = get_project_session(self.root)
        self.session.replace(project, asset_root=self.root)
        self.ids = [p['id'] for p in project.pages]
        self.panel = PromptWriterPanel(project_root=str(self.root), page_id=self.ids[1])

    def tearDown(self):
        self.drain()
        self.panel.shutdown()
        self.panel.deleteLater()
        self.app.processEvents()
        release_project_session(self.root)
        self.temp.cleanup()

    def drain(self):
        for _ in range(200):
            if not self.panel._generation_in_progress:
                return
            QtTest.QTest.qWait(5)
        self.fail('Build did not finish')

    def build(self, ids):
        batch = self.panel._build_pages(ids, confirm=False)
        self.drain()
        return batch

    def test_each_launcher_target_uses_canonical_model_and_actual_dimensions(self):
        for page_id in self.ids:
            self.panel.select_page(page_id)
            page = self.session.project.get_page(page_id)
            self.assertEqual(self.panel.target_selector.currentData(), page_id)
            self.assertEqual(self.panel._view_spec.color_key, PAGE_TYPES[page['type']].artwork_template)
            label = self.panel.target_selector.currentText()
            self.assertIn(label, self.panel._view_spec.copy_button.toolTip())
            self.assertIn(label, self.panel._view_spec.generate_button.toolTip())
            self.assertIn(label, self.panel._view_spec.preview_widget.placeholderText())
            self.assertNotIn('cover.png', self.panel._view_spec.detail_label.toolTip())
            batch = self.build([page_id])
            self.assertEqual(batch.items[0].status, 'success')
            text = self.session.project.get_prompt(page_id)['text']
            self.assertIn('1600×900', text)
            self.assertIn('16:9', text)
            self.assertNotIn('four-page', text)
            self.assertIn('5-page', text)
            self.assertNotIn('Message A', text)
            self.assertNotIn('Message B', text)
            if page['type'] == 'letter':
                self.assertIn('x=10' if page['name'] == 'Letter A' else 'x=55', text)
                self.assertIn('width=35', text)
            elif page['type'] == 'illustration':
                self.assertIn('artwork-only', text)
                self.assertNotIn('readable area', text)
            elif page['type'] == 'end_page':
                self.assertIn('The End', text)
                self.assertIsNone(self.session.project.get_message(page_id))

    def test_refresh_preserves_cursor_undo_and_failed_target_switch(self):
        editor = self.panel._view_spec.preview_widget
        editor.moveCursor(QtGui.QTextCursor.End)
        editor.insertPlainText(" LAST 雪\n  ")
        exact = self.session.project.get_prompt(self.ids[1])["text"]
        cursor = editor.textCursor().position()
        self.session.project.rename_page(self.ids[1], "Renamed")
        self.session.project.update_project_data("sound", {"volume": 33})
        self.assertEqual(editor.textCursor().position(), cursor)
        self.assertTrue(editor.document().isUndoAvailable())
        self.assertEqual(self.session.project.get_prompt(self.ids[1])["text"], exact)
        with mock.patch.object(self.session, "save", side_effect=OSError("synthetic conflict")):
            self.assertFalse(self.panel.select_page(self.ids[3]))
            self.assertFalse(self.panel.select_page(self.ids[3]))
        self.assertEqual(self.panel._view_page_id, self.ids[1])
        self.assertEqual(self.panel.target_selector.currentData(), self.ids[1])
        self.assertTrue(self.panel.select_page(self.ids[3]))
        self.assertFalse(editor.document().isUndoAvailable())

    def test_independent_controls_exact_text_reorder_rename_save_reload(self):
        a, b = self.ids[1], self.ids[3]
        self.panel.select_page(a)
        self.panel._view_spec.detail_widget.setPlainText('  A direction 雪\n')
        self.panel.use_project_palette.setChecked(False)
        self.panel.page_palette.setCurrentText('Copper A')
        self.panel.select_page(b)
        self.panel._view_spec.detail_widget.setPlainText('  B direction café\n\n')
        self.panel.use_project_palette.setChecked(False)
        self.panel.page_palette.setCurrentText('Blue B')
        self.build([a, b])
        self.assertIn('Copper A', self.session.project.get_prompt(a)['text'])
        self.assertIn('Blue B', self.session.project.get_prompt(b)['text'])
        exact = '  Manual café 雪\r\n\n  '
        self.session.project.update_prompt(a, {'text': exact})
        self.panel.select_page(b)
        self.panel._view_spec.preview_widget.setPlainText('  B edited 雪\n\n ')
        exact_b = self.session.project.get_prompt(b)['text']
        self.panel.select_page(a)
        self.session.project.rename_page(a, 'Repeated name')
        self.session.project.rename_page(b, 'Repeated name')
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.assertEqual(self.panel.target_selector.currentData(), a)
        self.assertIn('Page 4', self.panel.target_selector.currentText())
        self.assertTrue(self.panel.persist_project_state())
        self.panel._on_close()
        self.panel.reload_project_state()
        loaded = active_project_repository(self.root).load().project
        self.assertEqual(loaded.get_prompt(a)['text'], exact)
        self.assertEqual(loaded.get_prompt(b)['text'], exact_b)
        self.assertEqual(loaded.get_prompt(a)['overrides']['color'], 'Copper A')
        self.assertEqual(loaded.get_prompt(b)['directions'], '  B direction café\n\n')

    def test_settings_stale_inheritance_and_manual_end_text_preserved(self):
        a = self.ids[1]
        self.build([a, self.ids[-1]])
        before = self.session.project.get_prompt(a)['text']
        self.panel.select_page(a)
        self.panel.cmb_color.setCurrentText('Ruby')
        self.panel.txt_global.setPlainText('Shared change')
        self.assertEqual(self.session.project.get_prompt(a)['text'], before)
        self.assertIn('Settings changed', self.panel.prompt_status.text())
        self.panel.use_project_palette.setChecked(False)
        self.panel.page_palette.setCurrentText('Custom')
        self.panel.use_project_palette.setChecked(True)
        self.assertNotIn('color', self.session.project.get_prompt(a)['overrides'])
        self.assertEqual(self.session.project.to_dict()['prompt_settings']['color'], 'Ruby')
        self.panel.select_page(self.ids[-1])
        self.panel._view_spec.preview_widget.setPlainText('  My own ending without required words\n ')
        self.panel.select_page(a)
        self.panel.select_page(self.ids[-1])
        self.assertEqual(self.panel._view_spec.preview_widget.toPlainText(), '  My own ending without required words\n ')

    def test_build_recovery_and_shared_resolution_are_stable(self):
        a, b = self.ids[1], self.ids[3]
        original = self.session.project.get_prompt(a)['text']
        with mock.patch.object(self.panel, '_roll_shared_prompt_data', wraps=self.panel._roll_shared_prompt_data) as roll:
            self.build([a])
            self.build([b])
            self.build([a])
            self.assertEqual(roll.call_count, 1)
        previous = self.session.project.get_page(a)['recovery']['previous_prompt']['text']
        self.panel.select_page(a)
        self.panel._restore_previous()
        self.assertEqual(self.session.project.get_prompt(a)['text'], previous)
        self.assertNotEqual(previous, original)
        self.assertTrue(self.panel.persist_project_state())
        loaded = active_project_repository(self.root).load().project
        self.assertIn('previous_prompt', loaded.get_page(a)['recovery'])

    def test_batch_edit_delete_retype_cancel_and_project_replacement(self):
        batch = PromptBatch(self.session, self.ids, self.panel._roll_shared_prompt_data, self.panel._guidance_phrases())
        batch.run_item(batch.items[0])
        self.panel.select_page(self.ids[-1])
        self.session.project.update_prompt(self.ids[1], {'text': 'NEW AUTHOR TEXT'})
        self.session.project.delete_page(self.ids[2])
        self.session.project.change_page_type(self.ids[3], 'illustration')
        for item in batch.items[1:]:
            batch.run_item(item)
        self.assertEqual(batch.items[0].status, 'success')
        self.assertTrue(all(item.status == 'conflict' for item in batch.items[1:]))
        self.assertEqual(self.session.project.get_prompt(self.ids[1])['text'], 'NEW AUTHOR TEXT')
        ids = [p['id'] for p in self.session.project.pages]
        batch = PromptBatch(self.session, ids, self.panel._roll_shared_prompt_data, self.panel._guidance_phrases())
        batch.cancel()
        for item in batch.items: batch.run_item(item)
        self.assertTrue(all(item.status == 'skipped' for item in batch.items))
        batch = PromptBatch(self.session, ids, self.panel._roll_shared_prompt_data, self.panel._guidance_phrases())
        self.session.replace(create_project(['cover', 'end_page']), asset_root=self.root)
        for item in batch.items: batch.run_item(item)
        self.assertTrue(all(item.status == 'conflict' for item in batch.items))
        self.assertTrue(all(page['prompt']['text'] is None for page in self.session.project.pages))

    def test_batch_failures_retry_only_failed_items_and_confirmation_cancel(self):
        fail_id = self.ids[1]
        original = self.session.project.get_prompt(fail_id)['text']
        def fail_one(project, page_id, *args):
            if page_id == fail_id: raise ValueError('synthetic composer failure')
            return compose_page(project, page_id, *args)
        with mock.patch('prompt_writer_project.compose_page', side_effect=fail_one):
            batch = self.build(self.ids)
        self.assertEqual([i.status for i in batch.items], ['success', 'failed', 'success', 'success', 'success'])
        successful = {i.page_id: self.session.project.get_prompt(i.page_id) for i in batch.items if i.status == 'success'}
        self.assertEqual(self.session.project.get_prompt(fail_id)['text'], original)
        with mock.patch.object(self.panel, '_confirm_replacement', return_value=True):
            self.panel._retry_batch()
            self.drain()
        self.assertEqual(len(self.panel._last_batch.items), 1)
        for page_id, prompt in successful.items():
            self.assertEqual(self.session.project.get_prompt(page_id), prompt)
        before = self.session.project.get_prompt(fail_id)
        with mock.patch.object(self.panel, '_confirm_replacement', return_value=False):
            cancelled = self.panel._build_pages([fail_id])
        self.assertEqual(cancelled.items[0].status, 'skipped')
        self.assertEqual(self.session.project.get_prompt(fail_id), before)

    def test_page_palette_add_and_new_project_keep_library(self):
        a = self.ids[1]
        self.panel.select_page(a)
        self.panel._open_list_manager('color', for_page=True)
        dialog = self.panel._list_manager_dialogs['color']
        self.app.processEvents()
        dialog.entry_edit.selectAll()
        QtTest.QTest.keyClicks(dialog.entry_edit, 'Copper Page')
        QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
        self.assertEqual(self.panel.page_palette.currentText(), 'Copper Page')
        self.assertEqual(self.session.project.get_prompt(a)['overrides']['color'], 'Copper Page')
        self.assertEqual(self.session.project.to_dict()['prompt_settings']['color'], 'Ruby')
        saved_library = self.panel._user_colors_path().read_bytes()
        self.session.replace(create_project(['illustration', 'illustration', 'end_page']), asset_root=self.root)
        self.assertEqual(self.panel.cmb_subject.currentText(), '')
        self.assertEqual(self.panel._view_spec.preview_widget.toPlainText(), '')
        self.assertEqual(self.panel._user_colors_path().read_bytes(), saved_library)

    def test_removed_library_value_and_image_message_edits_preserve_prompt(self):
        a = self.ids[1]
        self.session.project.update_project_data('prompt_settings', {'color': 'Removed palette'})
        self.panel.reload_project_state()
        self.assertEqual(self.panel.cmb_color.currentText(), 'Removed palette')
        exact = self.session.project.get_prompt(a)['text']
        self.session.project.update_artwork(a, {'placement': {'fit': 'contain'}})
        message = self.session.project.get_message(a)
        message['html'] = '<p>PRIVATE CHANGED MESSAGE</p>'
        self.session.project.update_message(a, message)
        self.assertEqual(self.session.project.get_prompt(a)['text'], exact)
        self.build([a])
        self.assertNotIn('PRIVATE CHANGED MESSAGE', self.session.project.get_prompt(a)['text'])

    def test_repeated_end_pages_and_last_illustration_follow_type(self):
        self.session.replace(create_project(['end_page', 'end_page', 'illustration']), asset_root=self.root)
        self.panel.cmb_subject.setCurrentText('Forest')
        ids = [page['id'] for page in self.session.project.pages]
        batch = self.build(ids)
        self.assertTrue(all(item.status == 'success' for item in batch.items))
        for page_id in ids[:2]:
            self.assertIn('The End', self.session.project.get_prompt(page_id)['text'])
            self.assertIsNone(self.session.project.get_message(page_id))
        last = self.session.project.get_prompt(ids[-1])['text']
        self.assertNotIn('The End', last)
        self.assertIn('artwork-only', last)
        self.assertIn('3-page', last)

    def test_empty_draft_and_explicit_copy_preserve_exact_authored_text(self):
        exact = '  Emoji 🌲 café 雪\r\n\n '
        self.session.project.update_prompt(self.ids[1], {'text': exact})
        self.panel.select_page(self.ids[1])
        self.panel.btn_copy.click()
        self.assertEqual(self.app.clipboard().text(), exact)
        self.session.replace(create_project([]), asset_root=self.root)
        self.assertEqual(self.panel.target_selector.count(), 0)
        self.assertFalse(self.panel.btn_generate.isEnabled())
        self.assertTrue(self.panel.persist_project_state())

    def test_semantic_highlights_do_not_change_exact_text_or_undo(self):
        self.panel.select_page(self.ids[1])
        editor = self.panel._view_spec.preview_widget
        editor.setPlainText('Author 🌲 text')
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, ' more')
        before = self.session.project.get_prompt(self.ids[1])['text']
        self.panel.refresh_semantic_palette()
        self.assertEqual(self.session.project.get_prompt(self.ids[1])['text'], before)
        editor.undo()
        self.assertEqual(self.session.project.get_prompt(self.ids[1])['text'], 'Author 🌲 text')


if __name__ == '__main__':
    unittest.main()
