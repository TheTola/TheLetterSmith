"""Behavior checks for the restored interfaces, using canonical project data."""
import unittest
import json
import subprocess
import sys
from itertools import product
from unittest import mock
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from PIL import Image

from prompt_writer_workspace import PromptWriterPanel
from project_model import PAGE_TYPES, create_project
from project_repository import active_project_repository
from recent_media import remember_type_image, recent_type_images
import test_prompt_writer_pages as prompt_fixture
import test_messages_workspace as message_fixture


class RestoredWriterTests(unittest.TestCase):
    setUpClass = classmethod(prompt_fixture.PromptWriterPageTests.setUpClass.__func__)
    tearDown = prompt_fixture.PromptWriterPageTests.tearDown
    drain = prompt_fixture.PromptWriterPageTests.drain
    build = prompt_fixture.PromptWriterPageTests.build

    def setUp(self):
        with mock.patch('test_prompt_writer_pages.PromptWriterPanel', PromptWriterPanel):
            prompt_fixture.PromptWriterPageTests.setUp(self)

    def test_all_ten_pages_edit_together_without_selector_or_dimensions(self):
        self.session.replace(create_project(['cover'] + ['illustration']*8 + ['end_page']), asset_root=self.root)
        self.panel.resize(1100, 760)
        self.panel.show()
        self.app.processEvents()
        self.assertEqual(len(self.panel._page_fields), 10)
        self.assertTrue(self.panel.cmb_subject.isEditable())
        self.assertFalse(self.panel.output_width.isVisible())
        self.assertFalse(self.panel.output_height.isVisible())
        self.assertFalse(self.panel.btn_restore.isVisible())
        self.panel.cmb_subject.setCurrentText('The garden')
        exact = 'Details café 🌲\n' * 80
        ids = [page['id'] for page in self.session.project.pages]
        for index, identity in enumerate(ids):
            self.panel._page_fields[identity]['directions'].setPlainText(exact + str(index))
        self.session.project.reorder_pages(list(reversed(ids)))
        for index, identity in enumerate(ids):
            self.assertEqual(self.session.project.get_prompt(identity)['directions'], exact + str(index))
            self.assertEqual(self.panel._page_fields[identity]['directions'].toPlainText(), exact + str(index))
        self.assertEqual(self.session.project.to_dict()['prompt_settings']['subject'], 'The garden')
        self.assertTrue(self.panel.persist_project_state())
        for width in (560, 1100):
            self.panel.resize(width, 760)
            QtTest.QTest.qWait(60)
            self.assertEqual(self.panel._preview_scroll.horizontalScrollBar().maximum(), 0,
                             [(type(child).__name__, child.objectName(), child.minimumSizeHint().width()) for child in self.panel._preview_scroll.widget().findChildren(QtWidgets.QWidget) if child.isVisibleTo(self.panel) and child.minimumSizeHint().width() > width-70])
            self.assertFalse(self.panel.target_selector.isVisible())
            self.assertLess(self.panel.generated_section.y(), self.panel.style_section.y())
            cards = [fields['directions_card'] for fields in self.panel._page_fields.values()]
            self.assertTrue(all(card.isVisibleTo(self.panel) for card in cards))
            self.assertTrue(all(left.geometry().bottom() < right.y() for left, right in zip(cards, cards[1:])))
        self.session.project.delete_page(ids[4])
        added = self.session.project.add_page('illustration', index=2)
        self.assertNotIn(ids[4], self.panel._page_fields)
        self.assertEqual(self.panel._page_fields[added]['directions'].toPlainText(), '')
        for index, identity in enumerate(ids):
            if identity != ids[4]:
                self.assertEqual(self.panel._page_fields[identity]['directions'].toPlainText(), exact + str(index))

    def test_automatic_prompts_allow_all_eight_creative_combinations(self):
        from PromptWriterPanel import HIDDEN_STYLE_DEFAULT, HIDDEN_FRAMING_DEFAULT
        pages = create_project(['cover', 'illustration', 'letter', 'end_page'])
        for page in pages.pages:
            pages.update_prompt(page['id'], {'text': ''})  # Empty older saves are not manual overrides.
        browser = SimpleNamespace(provider='Test', submit_prompt=mock.Mock())
        self.panel._browser = browser
        requested = QtTest.QSignalSpy(self.panel.provider_requested)
        with mock.patch.object(self.panel, '_build_pages', side_effect=AssertionError('Explicit build required')):
            self.session.replace(pages, asset_root=self.root)
            artwork = [page['artwork'] for page in self.session.project.pages]
            self.assertTrue(all(page['prompt']['text'] for page in self.session.project.pages))
            for populated in product((False, True), repeat=3):
                with self.subTest(populated=populated):
                    values = dict(zip(('subject', 'type', 'color'),
                                      (value if enabled else '' for enabled, value in zip(populated, ('Forest', 'Ink', 'Ruby')))))
                    self.session.project.update_project_data('prompt_settings', {**values, 'checks': {}, 'global': ''})
                    for key, value in values.items():
                        self.assertEqual(self.panel._managed_combo_for_key(key).currentText(), value)
                    for page in self.session.project.pages:
                        text = self.panel.current_page_prompt(page['id'])
                        for key, fallback in (
                                ('subject', 'Choose an appropriate subject for this page'),
                                ('type', 'Choose an illustration style that suits the page'),
                                ('color', 'Choose a color scheme that suits the composition')):
                            self.assertEqual(fallback in text, not bool(values[key]))
                            if values[key]:
                                self.assertIn(values[key], text)
                        self.assertIn('Create exactly one image', text)
                        self.assertNotIn('four-page', text)
                        self.assertNotIn('None', text)
                        self.assertNotIn('undefined', text)
                        self.assertNotIn(HIDDEN_STYLE_DEFAULT, text)
                        self.assertNotIn(HIDDEN_FRAMING_DEFAULT, text)
                        self.assertNotIn('Guidance:', text)
                        self.assertIn('Maintain a cohesive artistic identity across all pages, '
                                      'while allowing each page its own creative direction.', text)
                        if page['type'] == 'letter':
                            self.assertIn('readable area', text)
                            self.assertIn('do not render the letter body', text)
                        elif page['type'] == 'illustration':
                            self.assertIn('artwork-only', text)
                        elif page['type'] == 'end_page':
                            self.assertIn('The End', text)
                            self.assertNotIn('echoes the cover', text)
            self.assertEqual([page['artwork'] for page in self.session.project.pages], artwork)
            browser.submit_prompt.assert_not_called()
            self.assertEqual(requested.count(), 0)
        self.panel._browser = None

    def test_shipped_prompt_seeds_do_not_request_multiple_images_or_portrait_framing(self):
        from pathlib import Path
        from prompt_writer_project import compose_page
        modules = Path(__file__).resolve().parents[1] / 'Prompter' / 'modules'
        roles = (modules / 'role.txt').read_text(encoding='utf-8').splitlines()
        orders = (modules / 'order.txt').read_text(encoding='utf-8').splitlines()
        page_id = self.ids[2]
        for role, order in product(roles, orders):
            if not role.strip() or not order.strip():
                continue
            text, _ = compose_page(self.session.project, page_id,
                                   {'role': role, 'order': order}, self.panel._guidance_phrases())
            self.assertIn('Create exactly one image', text)
            self.assertNotIn('four', text.casefold())
            self.assertNotIn('a portrait of', text.casefold())
            self.assertNotIn('a portrait image of', text.casefold())

    def test_all_page_previews_and_copy_are_current_without_selecting_or_building(self):
        for page_id in self.ids:
            self.session.project.update_prompt(page_id, {'text': None, 'resolved_components': {}})
        self.panel.resize(1100, 900)
        self.panel.show()
        self.app.processEvents()
        self.assertFalse(self.panel.target_selector.isVisible())
        self.assertFalse(self.panel.generated_section.expanded)
        self.panel.generated_section.toggle.click()
        self.assertTrue(self.panel.generated_section.expanded)
        self.assertFalse(any(button.isVisibleTo(self.panel) and button.text().startswith('Build')
                             for button in self.panel.findChildren(QtWidgets.QPushButton)))
        a, b = self.ids[1], self.ids[3]
        selected = self.panel._view_page_id
        with mock.patch.object(self.panel, '_build_pages', side_effect=AssertionError('Explicit build required')):
            self.session.project.update_prompt(a, {'overrides': {'subject': 'Copper forest', 'color': ''}})
            self.session.project.update_prompt(b, {'overrides': {'subject': 'Blue forest', 'color': 'Blue'}})
            for index, page_id in enumerate((a, b, a, b, a, b)):
                self.assertEqual(self.panel._view_page_id, selected)
                fields = self.panel._page_fields[page_id]
                fields['directions'].setPlainText(f'Only {page_id}, edit {index}')
                self.panel.txt_global.setPlainText(f'Shared sky {index}')
                self.panel.cb_paint.setChecked(bool(index % 2))
                current = self.panel.current_page_prompt(page_id)
                self.assertEqual(fields['output'].toPlainText(), current)
                self.assertIn(f'Only {page_id}, edit {index}', current)
                self.assertIn(f'Shared sky {index}', current)
                self.assertIn('Copper forest' if page_id == a else 'Blue forest', current)
                self.assertNotIn('Blue forest' if page_id == a else 'Copper forest', current)
                self.assertEqual('bias toward painterly style' in current, bool(index % 2))
                self.assertEqual([key for key, value in self.panel._page_fields.items()
                                  if value['output_card'].isVisibleTo(self.panel)], self.ids)
                self.assertEqual([key for key, value in self.panel._page_fields.items()
                                  if value['directions_card'].isVisibleTo(self.panel)], self.ids)
                self.app.processEvents()
                fields['copy'].click()
                self.app.processEvents()  # Deliver native clipboard ownership notifications.
                self.assertEqual(self.app.clipboard().text(), current,
                                 (index, fields['copy'].isEnabled(), self.panel.workspace_status.text()))
                self.assertEqual(self.panel._copy_all_prompts_text(), self.panel.current_page_prompt(selected))
                self.app.processEvents()

    def test_sections_preserve_live_state_and_header_access_across_navigation(self):
        project = create_project(['cover'] + ['illustration'] * 5 + ['end_page'])
        self.session.replace(project, asset_root=self.root)
        self.panel.resize(1280, 760)
        self.panel.show()
        self.app.processEvents()
        self.assertTrue(self.panel.service_button.isEnabled())
        self.assertEqual(self.panel.service_button.text(), 'Create Image')
        self.assertFalse(self.panel.generated_section.expanded)
        self.assertTrue(all(section.expanded for section in self.panel.editing_sections))
        self.assertTrue(all(section.summary.text() == 'No custom selections.'
                            for section in self.panel.editing_sections))
        for combo in (self.panel.cmb_subject, self.panel.cmb_type, self.panel.cmb_color):
            self.assertEqual(combo.currentText(), '')
            self.assertEqual(combo.lineEdit().placeholderText(), 'None')
        self.panel.service_button.click()
        self.assertTrue(self.panel.conversation_bar.isVisible())
        self.assertIn('Choose an appropriate subject for this page', self.panel.service_prompt.toPlainText())
        self.panel.prompts_button.click()
        ids = [page['id'] for page in project.pages]
        self.assertEqual(len(self.panel._page_fields), 7)
        self.assertTrue(all(fields['directions_card'].isVisibleTo(self.panel)
                            for fields in self.panel._page_fields.values()))
        initial_target = self.panel._view_page_id
        for index, page_id in enumerate(ids):
            self.panel._page_fields[page_id]['directions'].setPlainText(f'Unique scene {index}')
        self.assertEqual(self.panel._view_page_id, initial_target)
        self.panel.cmb_type.setCurrentText('Ink')
        self.panel.cb_paint.setChecked(True)
        self.panel.txt_global.setPlainText('Shared direction')
        before = self.session.project.to_dict()
        for index, section in enumerate(self.panel.editing_sections):
            section.toggle.click()
            self.assertFalse(section.expanded)
            self.assertFalse(section.body.isVisibleTo(self.panel))
            self.assertEqual(section.toggle.text(), 'Edit')
            self.assertTrue(all(other.expanded for other in self.panel.editing_sections[index + 1:]))
        self.assertEqual(self.session.project.to_dict(), before)
        self.panel._page_fields[ids[-1]]['directions'].setPlainText('Latest hidden scene')
        self.assertIn('Latest hidden scene', self.panel.current_page_prompt(ids[-1]))
        self.assertNotIn('Latest hidden scene', self.panel.current_page_prompt(ids[0]))
        self.panel.generated_section.toggle.click()
        self.assertTrue(all(fields['output_card'].isVisibleTo(self.panel)
                            for fields in self.panel._page_fields.values()))
        for section in self.panel.editing_sections:
            section.toggle.click()
            self.assertTrue(section.body.isVisibleTo(self.panel))
            self.assertEqual(section.toggle.text(), 'Finish Selecting')
        self.assertEqual(self.panel.cmb_type.currentText(), 'Ink')
        self.assertTrue(self.panel.cb_paint.isChecked())
        self.assertEqual(self.panel.txt_global.toPlainText(), 'Shared direction')
        self.app.processEvents()
        position = self.panel.service_button.mapTo(self.panel, QtCore.QPoint())
        scroll = self.panel._preview_scroll.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(scroll.maximum())
        self.app.processEvents()
        self.assertEqual(self.panel.service_button.mapTo(self.panel, QtCore.QPoint()), position)
        self.assertTrue(self.panel.service_button.isVisibleTo(self.panel))
        self.assertEqual([button for button in self.panel.findChildren(QtWidgets.QPushButton)
                          if button.isVisibleTo(self.panel) and button.text().casefold() == 'create image'],
                         [self.panel.service_button])
        self.panel.style_section.toggle.click()
        self.panel.service_button.click()
        self.assertTrue(self.panel.conversation_bar.isVisible())
        self.assertEqual(list(self.panel._service_buttons), ids)
        self.panel.select_page(ids[-1])
        self.assertEqual(self.panel._image_destination, ids[-1])
        self.assertIn('Latest hidden scene', self.panel.service_prompt.toPlainText())
        self.panel.prompts_button.click()
        self.assertFalse(self.panel.generated_section.expanded)
        self.assertFalse(self.panel.style_section.expanded)
        self.assertTrue(self.panel.directions_section.expanded)
        self.panel.generated_section.toggle.click()
        self.panel.popdown()
        self.panel.popup()
        self.assertFalse(self.panel.generated_section.expanded)
        self.assertFalse(self.panel.style_section.expanded)
        self.assertTrue(self.panel.persist_project_state())
        stored = active_project_repository(self.root).load()
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.assertTrue(all(section.expanded for section in self.panel.editing_sections))
        self.assertEqual(self.panel.cmb_type.currentText(), 'Ink')
        self.assertTrue(self.panel.cb_paint.isChecked())
        for index, page_id in enumerate(ids):
            self.assertEqual(self.panel._page_fields[page_id]['directions'].toPlainText(),
                             'Latest hidden scene' if index == 6 else f'Unique scene {index}')

    def test_automatic_prompts_and_manual_overrides_survive_fresh_process(self):
        for page_id in self.ids:
            self.session.project.update_prompt(page_id, {'text': None, 'resolved_components': {}})
        a, b = self.ids[1], self.ids[3]
        self.session.project.update_prompt(a, {'overrides': {'subject': '', 'type': '', 'color': 'Copper'}})
        self.session.project.update_prompt(b, {'overrides': {'subject': 'Blue forest', 'checks': {}}})
        self.panel._page_fields[a]['directions'].setPlainText('A only')
        self.panel._page_fields[b]['directions'].setPlainText('B only')
        exact = '  My complete manual prompt 🌲\nKeep every word.  '
        self.panel._page_fields[a]['output'].setPlainText(exact)
        self.panel.txt_global.setPlainText('Shared sky')
        self.session.project.rename_page(a, 'Repeated name')
        self.session.project.rename_page(b, 'Repeated name')
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.assertEqual(self.panel.current_page_prompt(a), exact)
        before = {key: self.session.project.get_prompt(key) for key in self.ids}
        self.assertTrue(self.panel.persist_project_state())
        script = '''
import json, sys
from PySide6 import QtWidgets
from prompt_writer_workspace import PromptWriterPanel
app = QtWidgets.QApplication([])
panel = PromptWriterPanel(project_root=sys.argv[1])
result = {}
for page in panel.session.project.pages:
    panel.select_page(page['id'])
    result[page['id']] = {'prompt': panel.session.project.get_prompt(page['id']),
                          'current': panel.current_page_prompt(),
                          'preview': panel._page_fields[page['id']]['output'].toPlainText()}
print(json.dumps(result))
panel.shutdown()
'''
        restarted = subprocess.run([sys.executable, '-c', script, str(self.root)],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
        result = json.loads(restarted.stdout.strip().splitlines()[-1])
        stored = active_project_repository(self.root).load()
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.assertEqual(list(result), list(reversed(self.ids)))
        for page_id in self.ids:
            self.assertEqual(result[page_id]['prompt'], before[page_id])
            self.assertEqual(result[page_id]['current'], before[page_id]['text'])
            self.assertEqual(result[page_id]['preview'], before[page_id]['text'])
        self.assertEqual(result[a]['current'], exact)
        self.assertIn('B only', result[b]['current'])
        self.assertNotIn('A only', result[b]['current'])

    def test_build_and_copy_keep_each_pages_exact_text(self):
        batch = self.build(self.ids)
        self.assertTrue(all(item.status == 'success' for item in batch.items))
        key = self.ids[3]
        fields = self.panel._page_fields[key]
        text = '  Exact prompt café 🌲\nKeep my wording.  '
        fields['output'].setPlainText(text)
        fields['copy'].click()
        self.assertEqual(self.app.clipboard().text(), text)
        self.assertEqual(self.session.project.get_prompt(key)['text'], text)
        self.assertEqual(self.panel._page_fields[self.ids[0]]['output'].toPlainText(), self.session.project.get_prompt(self.ids[0])['text'])

    def test_live_options_update_text_colors_and_remove_unselected_content(self):
        self.build(self.ids)
        self.panel.cmb_subject.setCurrentText('Encouragement')
        self.panel.cb_black.setChecked(True)
        self.panel.cb_paint.setChecked(True)
        self.panel.cb_forbid.setChecked(True)
        self.panel.cb_wide_scene.setChecked(True)
        self.panel.txt_global.setPlainText('Shared 🌲 sky')
        for page in self.session.project.pages:
            fields = self.panel._page_fields[page['id']]
            fields['directions'].setPlainText('Unique ' + page['id'])
            editor = fields['output']
            text = editor.toPlainText()
            self.assertIn('Encouragement', text)
            self.assertIn('Shared 🌲 sky', text)
            self.assertIn('Unique ' + page['id'], text)
            selections = {selection.cursor.selectedText(): selection.format.foreground().color().name()
                          for selection in editor.extraSelections()}
            self.assertEqual(selections['Encouragement'], self.panel._display_prompt_color('subject'))
            for checkbox, phrase in self.panel._guidance_checkbox_specs():
                if checkbox.isChecked() and not (page['type'] == 'end_page' and checkbox is self.panel.cb_forbid):
                    matching = [color for fragment, color in selections.items() if fragment.casefold().startswith(phrase.casefold())]
                    self.assertIn(self.panel._display_prompt_color(checkbox.property('promptCategory')), matching)
            self.assertIn(self.panel._page_prompt_color(page['id']),
                          selections.values())
        self.panel.cb_black.setChecked(False)
        self.panel.cb_paint.setChecked(False)
        self.panel.cb_wide_scene.setChecked(False)
        self.panel.cmb_subject.clearEditText()
        for page in self.session.project.pages:
            self.assertNotIn('thin black border', page['prompt']['text'])
            self.assertNotIn('bias toward painterly', page['prompt']['text'])
            self.assertNotIn('Encouragement', page['prompt']['text'])
            self.assertNotIn('wide scene', page['prompt']['text'].casefold())

    def test_live_updates_preserve_manual_edits_undo_and_plain_copy(self):
        self.build(self.ids)
        self.panel.cmb_subject.setCurrentText('Encouragement')
        fields = self.panel._page_fields[self.ids[1]]
        editor = fields['output']
        original = editor.toPlainText()
        editor.moveCursor(QtGui.QTextCursor.End)
        editor.insertPlainText('\n\nMy exact words 🌲 <keep>  ')
        self.panel.cmb_subject.setCurrentText('Celebration')
        self.assertIn('Celebration', editor.toPlainText())
        self.assertTrue(editor.toPlainText().endswith('\n\nMy exact words 🌲 <keep>  '))
        self.assertTrue(editor.document().isUndoAvailable())
        fields['copy'].click()
        self.assertEqual(self.app.clipboard().text(), editor.toPlainText())
        editor.undo()
        self.assertEqual(editor.toPlainText(), original + '\n\nMy exact words 🌲 <keep>  ')
        editor.setPlainText('My completely custom prompt')
        self.panel.cb_white.setChecked(True)
        self.assertEqual(editor.toPlainText(), 'My completely custom prompt')
        self.build([self.ids[1]])
        editor.setPlainText(editor.toPlainText().replace('Celebration', 'My hand-edited subject'))
        authored_line = editor.toPlainText().splitlines()[0]
        self.panel.cmb_subject.setCurrentText('Birthday')
        self.panel.cb_frame.setChecked(True)
        self.assertEqual(editor.toPlainText().splitlines()[0], authored_line)
        self.assertIn('add a decorative frame', editor.toPlainText())

    def test_live_same_type_pages_keep_overrides_and_survive_reopen(self):
        a, b = self.ids[1], self.ids[3]
        self.session.project.update_prompt(a, {'overrides': {'subject': 'Copper forest', 'color': 'Copper'}})
        self.session.project.update_prompt(b, {'overrides': {'subject': 'Blue forest', 'color': 'Blue'}})
        self.build(self.ids)
        self.panel.cb_paint.setChecked(True)
        self.panel._page_fields[a]['directions'].setPlainText('Only page A')
        self.assertNotIn('Only page A', self.session.project.get_prompt(b)['text'])
        for page_id, subject in ((a, 'Copper forest'), (b, 'Blue forest')):
            self.assertIn(subject, self.panel._page_fields[page_id]['output'].toPlainText())
        self.assertTrue(self.panel.persist_project_state())
        loaded = active_project_repository(self.root).load().project
        self.session.replace(loaded, asset_root=self.root)
        self.session.project.rename_page(a, 'Same name')
        self.session.project.rename_page(b, 'Same name')
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.panel.cb_white.setChecked(True)
        self.assertIn('Only page A', self.session.project.get_prompt(a)['text'])
        self.assertNotIn('Only page A', self.session.project.get_prompt(b)['text'])
        self.assertIn('thin white border', self.session.project.get_prompt(a)['text'])
        self.assertIn('thin white border', self.session.project.get_prompt(b)['text'])

    def test_ten_pages_update_copy_and_save_independently(self):
        project = create_project(['cover'] + ['letter'] * 4 + ['illustration'] * 4 + ['end_page'])
        self.session.replace(project, asset_root=self.root)
        ids = [page['id'] for page in self.session.project.pages]
        self.panel.cmb_subject.setCurrentText('Encouragement')
        for index, page_id in enumerate(ids):
            self.panel._page_fields[page_id]['directions'].setPlainText(f'Only scene {index}.')
        self.panel.cb_paint.setChecked(True)
        for index, page_id in enumerate(ids):
            fields = self.panel._page_fields[page_id]
            text = self.session.project.get_prompt(page_id)['text']
            self.assertIn(f'Only scene {index}.', text)
            self.assertIn('bias toward painterly style', text)
            self.assertTrue(all(f'Only scene {other}.' not in text for other in range(10) if other != index))
            fields['copy'].click()
            self.assertEqual(self.app.clipboard().text(), text)
        self.assertTrue(self.panel.persist_project_state())
        saved = active_project_repository(self.root).load().project
        self.assertEqual(len(saved.pages), 10)
        for page_id in ids:
            self.assertEqual(saved.get_prompt(page_id), self.session.project.get_prompt(page_id))

    def test_historical_text_is_preserved_until_explicit_build(self):
        exact = '  Historical 🌲 prompt\r\n  '
        self.session.project.update_prompt(self.ids[1], {'text': exact})
        self.panel.cb_white.setChecked(True)
        self.assertEqual(self.session.project.get_prompt(self.ids[1])['text'], exact)
        self.build([self.ids[1]])
        self.panel.cb_white.setChecked(False)
        self.assertNotIn('thin white border', self.session.project.get_prompt(self.ids[1])['text'])

    def test_subject_management_add_rename_remove_and_reload(self):
        self.build(self.ids)
        self.panel.manage_buttons['subject'].click()
        dialog = self.panel._list_manager_dialogs['subject']
        dialog.entry_edit.setText('New 🌲 Subject')
        dialog.btn_add.click()
        self.assertEqual(self.panel.cmb_subject.currentText(), 'New 🌲 Subject')
        dialog.entry_edit.setText('Renamed 🌲 Subject')
        dialog.btn_update.click()
        self.assertEqual(self.panel.cmb_subject.currentText(), 'Renamed 🌲 Subject')
        self.assertIn('Renamed 🌲 Subject', self.session.project.get_prompt(self.ids[1])['text'])
        self.panel._reload_managed_list('subject')
        self.assertGreaterEqual(self.panel.cmb_subject.findText('Renamed 🌲 Subject'), 0)
        saved_text = self.session.project.get_prompt(self.ids[1])['text']
        dialog.btn_removal_mode.click()
        dialog._remove_entry(dialog._editing_id)
        self.assertEqual(self.panel.cmb_subject.currentText(), 'Renamed 🌲 Subject')
        self.assertEqual(saved_text, self.session.project.get_prompt(self.ids[1])['text'])
        self.assertEqual([entry.text for entry in self.panel._read_managed_entries('subject')], ['Forest'])
        dialog._remove_entry(next(entry.identity for entry in dialog._entries if not entry.is_header))
        self.assertEqual([entry.text for entry in self.panel._read_managed_entries('subject')], ['Forest'])
        self.assertEqual(saved_text, self.session.project.get_prompt(self.ids[1])['text'])

    def test_legacy_subject_correction_preserves_other_custom_entries(self):
        path = self.panel._managed_list_write_path('subject')
        path.write_text('Discouragement\nCustom subject\nEncouragement\n', encoding='utf-8')
        self.panel._reload_managed_list('subject')
        self.assertEqual([entry.text for entry in self.panel._read_managed_entries('subject')],
                         ['Encouragement', 'Custom subject'])
        self.assertIn('Discouragement', path.read_text(encoding='utf-8'))  # Bundled definitions stay intact.

    def test_managed_controls_keep_builtins_and_custom_edit_remove_restart_behavior(self):
        from PromptWriterPanel import USER_ADDED_HEADER
        self.panel.resize(1100, 760)
        self.panel.show()
        self.app.processEvents()
        exact_prompt = '  Historical prompt café\nKeep this wording.  '
        self.session.project.update_prompt(self.ids[1], {'text': exact_prompt})
        identities = {}
        for key, title in (('subject', 'Subjects'), ('type', 'Illustration Styles'), ('color', 'Color Schemes')):
            with self.subTest(category=key):
                combo = self.panel._managed_combo_for_key(key)
                combo.lineEdit().selectAll()
                QtTest.QTest.keyClick(combo.lineEdit(), QtCore.Qt.Key_Backspace)
                self.assertEqual(combo.currentText(), '')
                self.panel._reload_managed_list(key)
                self.assertEqual(combo.currentText(), '')
                self.assertNotIn(USER_ADDED_HEADER, [combo.itemText(i) for i in range(combo.count())])
                QtTest.QTest.mouseClick(self.panel.manage_buttons[key], QtCore.Qt.LeftButton)
                dialog = self.panel._list_manager_dialogs[key]
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(dialog.windowTitle(), 'Manage ' + title)
                self.assertFalse(hasattr(dialog, 'btn_restore'))
                source = self.panel._resolve_managed_list_path(key)
                original_bytes = source.read_bytes()

                def row_for(text):
                    return next(i for i, entry in enumerate(dialog._entries) if entry.text == text)

                def click_row(text):
                    item = dialog.list_widget.item(row_for(text))
                    dialog.list_widget.scrollToItem(item)
                    QtTest.QTest.mouseClick(dialog.list_widget.viewport(), QtCore.Qt.LeftButton,
                                           pos=dialog.list_widget.visualItemRect(item).center())

                def hover(item):
                    point = dialog.list_widget.visualItemRect(item).center()
                    viewport = dialog.list_widget.viewport()
                    event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(point),
                        QtCore.QPointF(viewport.mapToGlobal(point)), QtCore.Qt.NoButton,
                        QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
                    self.app.sendEvent(viewport, event)

                def remove(text):
                    row = row_for(text)
                    item = dialog.list_widget.item(row)
                    button = dialog.list_widget.itemWidget(item).findChild(QtWidgets.QToolButton, 'removeOption')
                    self.assertFalse(button.isHidden())
                    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)

                builtin = next(entry.text for entry in dialog._entries if not entry.is_header)
                dialog.entry_edit.setText('Reby Chocolatier')
                click_row(builtin)
                self.assertEqual(dialog.entry_edit.text(), 'Reby Chocolatier')
                self.assertFalse(dialog.btn_update.isEnabled())
                QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
                identity = dialog._editing_id
                self.assertEqual(combo.currentText(), 'Reby Chocolatier')
                self.assertEqual(combo.itemText(0), USER_ADDED_HEADER)
                self.assertEqual(dialog._entries[0].text, USER_ADDED_HEADER)
                dialog.entry_edit.setText('Second Custom')
                QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
                self.assertNotEqual(identity, dialog._editing_id)

                click_row(builtin)
                dialog.entry_edit.clear()
                dialog.list_widget.setFocus()
                item = dialog.list_widget.item(row_for('Reby Chocolatier'))
                dialog.list_widget.scrollToItem(item)
                hover(item)
                self.assertEqual(dialog.entry_edit.text(), 'Reby Chocolatier')
                self.assertEqual(dialog._editing_id, identity)
                dialog.entry_edit.selectAll()
                QtTest.QTest.keyClicks(dialog.entry_edit, 'Ruby Chocolatier')
                item = dialog.list_widget.item(row_for('Second Custom'))
                hover(item)
                self.assertEqual(dialog.entry_edit.text(), 'Ruby Chocolatier')
                self.assertEqual(dialog._editing_id, identity)
                QtTest.QTest.mouseClick(dialog.btn_update, QtCore.Qt.LeftButton)
                self.assertEqual(dialog._editing_id, identity)
                self.assertNotIn('Reby Chocolatier', [entry.text for entry in dialog._entries])
                self.assertEqual(len([entry for entry in dialog._entries if entry.is_user and not entry.is_header]), 2)

                QtTest.QTest.mouseClick(dialog.btn_removal_mode, QtCore.Qt.LeftButton)
                self.assertTrue(dialog.btn_removal_mode.isChecked())
                remove('Second Custom')
                self.assertEqual(dialog._editing_id, identity)
                remove('Ruby Chocolatier')
                self.assertNotIn(USER_ADDED_HEADER, [entry.text for entry in dialog._entries])
                self.assertNotIn(USER_ADDED_HEADER, [combo.itemText(i) for i in range(combo.count())])
                self.assertEqual(combo.currentText(), 'Ruby Chocolatier')  # Historical selection survives.
                item = dialog.list_widget.item(row_for(builtin))
                self.assertIsNone(dialog.list_widget.itemWidget(item).findChild(QtWidgets.QToolButton, 'removeOption'))
                dialog._remove_entry(dialog._entries[row_for(builtin)].identity)
                self.assertIn(builtin, [entry.text for entry in dialog._entries])
                self.assertIn(builtin, [entry.text for entry in self.panel._read_managed_entries(key)])
                self.assertFalse(any(entry.is_user for entry in dialog._entries))
                QtTest.QTest.mouseClick(dialog.btn_removal_mode, QtCore.Qt.LeftButton)
                self.assertTrue(all(button.isHidden() for button in dialog.list_widget.findChildren(QtWidgets.QToolButton, 'removeOption')))

                dialog.entry_edit.setText('Saved Custom')
                QtTest.QTest.mouseClick(dialog.btn_add, QtCore.Qt.LeftButton)
                identities[key] = dialog._editing_id
                QtTest.QTest.mouseClick(dialog.btn_removal_mode, QtCore.Qt.LeftButton)
                dialog._remove_entry(dialog._entries[row_for(builtin)].identity)
                self.assertEqual(source.read_bytes(), original_bytes)
                dialog.close()
                self.app.processEvents()
                combo.clearEditText()
        self.assertEqual(self.session.project.get_prompt(self.ids[1])['text'], exact_prompt)
        self.assertTrue(self.panel.persist_project_state())
        script = '''
import json, sys
from PySide6 import QtWidgets
from prompt_writer_workspace import PromptWriterPanel
app = QtWidgets.QApplication([])
panel = PromptWriterPanel(project_root=sys.argv[1])
result = {key: {'text': panel._managed_combo_for_key(key).currentText(),
                'custom': [(entry.identity, entry.text) for entry in panel._read_managed_entries(key)
                           if entry.is_user and not entry.is_header],
                'builtins': [entry.text for entry in panel._read_managed_entries(key)
                             if not entry.is_user and not entry.is_header]}
          for key in ('subject', 'type', 'color')}
result['prompt'] = panel.session.project.get_prompt(sys.argv[2])['text']
print(json.dumps(result))
panel.shutdown()
'''
        restarted = subprocess.run([sys.executable, '-c', script, str(self.root), self.ids[1]],
                                   capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
        result = json.loads(restarted.stdout.strip().splitlines()[-1])
        for key in identities:
            self.assertEqual(result[key]['text'], '')
            self.assertEqual(result[key]['custom'], [[identities[key], 'Saved Custom']])
            self.assertEqual(result[key]['builtins'], [{'subject': 'Forest', 'type': 'Ink', 'color': 'Ruby'}[key]])
        self.assertEqual(result['prompt'], exact_prompt)
        stored = active_project_repository(self.root).load()
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.panel.reload_project_state()
        self.build(self.ids)
        self.assertTrue(all(item.status == 'success' for item in self.panel._last_batch.items))

    def test_legacy_libraries_migrate_without_changing_bundled_definitions(self):
        from dataclasses import replace
        bundled = self.root / 'bundled' / 'Prompter' / 'modules'
        user = self.root / 'user_options'
        bundled.mkdir(parents=True)
        user.mkdir()
        self.panel.application_paths = replace(self.panel.application_paths,
            resource_root=self.root / 'bundled', prompt_writer_content_root=user, custom_palette_root=user)
        for key, filename, builtin_text, legacy_text in (
                ('subject', 'topic.txt', 'Forest\nBirthday\n', 'Forest\nReby Chocolatier\n'),
                ('type', 'type.txt', '-Styles\nInk\nOil\n', '-Styles\nInk\n-User Added\nReby Chocolatier\n'),
                ('color', 'color.txt', '-Basic\nRuby\nBlue\n', '-Basic\nRuby\n-User Added\nReby Chocolatier\n')):
            with self.subTest(category=key):
                source = bundled / filename
                source.write_text(builtin_text, encoding='utf-8')
                (user / filename).write_text(legacy_text, encoding='utf-8')
                entries = self.panel._read_managed_entries(key)
                custom = next(entry for entry in entries if entry.is_user and not entry.is_header)
                self.assertEqual(custom.text, 'Reby Chocolatier')
                self.assertEqual(entries[0].text, 'User Added')
                entries = [replace(entry, text='Ruby Chocolatier') if entry.identity == custom.identity else entry
                           for entry in entries]
                self.assertTrue(self.panel._apply_managed_list_changes(key, entries))
                reloaded = self.panel._read_managed_entries(key)
                self.assertIn(replace(custom, text='Ruby Chocolatier'), reloaded)
                self.assertEqual(len([entry for entry in reloaded if not entry.is_header]), 3)
                options_path = self.panel._managed_options_path(key)
                payload = json.loads(options_path.read_text(encoding='utf-8'))
                self.assertNotIn('excluded', payload)
                payload['excluded'] = ['forest', 'birthday', 'ink', 'oil', 'ruby', 'blue']
                options_path.write_text(json.dumps(payload), encoding='utf-8')
                self.assertEqual(self.panel._read_managed_entries(key), reloaded)
                self.assertTrue(self.panel._apply_managed_list_changes(key, [entry for entry in reloaded if entry.is_user]))
                self.assertEqual(self.panel._read_managed_entries(key), reloaded)
                self.assertNotIn('excluded', json.loads(options_path.read_text(encoding='utf-8')))
                self.assertEqual(source.read_text(encoding='utf-8'), builtin_text)

    def test_guidance_groups_are_inline_compact_and_wrap_at_narrow_widths(self):
        self.panel.resize(1280, 760)
        self.panel.show()
        self.app.processEvents()
        groups = self.panel.guidance_groups
        self.assertFalse(hasattr(self.panel, 'guidance_toggle'))
        self.assertEqual([group.title().replace('&&', '&') for group in groups],
                         ['Edges', 'Style Bias', 'Image Output', 'Camera Angle & Framing'])
        self.assertEqual(groups[-1].title(), 'Camera Angle && Framing')
        self.assertEqual([group.objectName() for group in groups],
                         ['guidance_edges', 'guidance_style_bias', 'guidance_image_output', 'guidance_camera_angle_framing'])
        self.assertEqual([group.property('promptCategory') for group in groups],
                         ['helpful', 'style_bias', 'clean_output', 'framing'])
        self.assertEqual(len({group.y() for group in groups}), 1)
        for left, right in zip(groups, groups[1:]):
            self.assertEqual(right.x() - left.geometry().right() - 1, 10)
        for width in (560, 480):
            self.panel.resize(width, 760)
            QtTest.QTest.qWait(40)
            self.assertGreater(len({group.y() for group in groups}), 1)
            self.assertEqual(self.panel._preview_scroll.horizontalScrollBar().maximum(), 0)
            for group in groups:
                self.assertTrue(group.isVisible())
                self.assertGreaterEqual(group.width(), group.minimumSizeHint().width())
                self.assertLessEqual(group.geometry().right(), self.panel._preview_scroll.viewport().width())
            for checkbox, _ in self.panel._checkbox_state_specs():
                self.assertEqual(checkbox.width(), checkbox.sizeHint().width())
                self.assertLessEqual(checkbox.geometry().right(), checkbox.parentWidget().width())
        self.panel.popdown()
        self.panel.popup()
        self.assertTrue(all(group.isVisible() for group in groups))

    def test_inline_guidance_preserves_saved_keys_optionality_and_live_page_scope(self):
        project = create_project(['cover', 'illustration'])
        shared_page, override_page = [page['id'] for page in project.pages]
        project.update_prompt(override_page, {'overrides': {'checks': {}}})
        self.session.replace(project, asset_root=self.root)
        for checkbox, key in self.panel._checkbox_state_specs():
            with self.subTest(option=key):
                phrase = dict(self.panel._guidance_checkbox_specs())[checkbox]
                checkbox.setChecked(True)
                self.assertTrue(self.session.project.to_dict()['prompt_settings']['checks'][key])
                self.assertIn(phrase, self.panel._page_fields[shared_page]['output'].toPlainText())
                self.assertNotIn(phrase, self.panel._page_fields[override_page]['output'].toPlainText())
                self.assertTrue(self.panel.persist_project_state())
                stored = active_project_repository(self.root).load()
                self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
                self.assertTrue(checkbox.isChecked())
                checkbox.setChecked(False)
                self.assertNotIn('Guidance:', self.panel._page_fields[shared_page]['output'].toPlainText())
        self.assertTrue(self.panel.persist_project_state())
        stored = active_project_repository(self.root).load()
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.assertTrue(all(not checkbox.isChecked() for checkbox, _ in self.panel._checkbox_state_specs()))
        self.assertEqual(self.session.project.get_prompt(override_page)['overrides']['checks'], {})

    def test_theme_colors_match_controls_and_leave_authored_text_unchanged(self):
        from ui_theme import ThemeService, THEMES
        from prompt_writer_colors import MIN_CONTRAST, contrast, surface_colors, CATEGORY_KEYS
        service = ThemeService(self.root)
        self.build(self.ids)
        self.panel.cmb_subject.setCurrentText('Encouragement')
        before = self.session.project.to_dict()
        for theme in THEMES:
            service.set_theme(theme, persist=False)
            self.panel.apply_theme_assets(service)
            self.app.processEvents()
            palette = surface_colors(service.tokens)
            for surface in (self.panel._preview_scroll.widget(), self.panel.conversation_bar):
                self.assertEqual(surface.palette().color(QtGui.QPalette.Window).name(), palette['surface'])
            display_colors = [self.panel._display_prompt_color(key) for key in CATEGORY_KEYS]
            display_colors += [self.panel._page_prompt_color(page_id) for page_id in self.ids]
            self.assertEqual(len(display_colors), len(set(display_colors)))
            for color in display_colors:
                for key in ('surface', 'input', 'selected'):
                    self.assertGreaterEqual(contrast(color, palette[key]), MIN_CONTRAST, (theme, color, key))
            subject_color = self.panel._display_prompt_color('subject')
            self.assertEqual(self.panel.cmb_subject.property('promptColor'), subject_color)
            for fields in self.panel._page_fields.values():
                matches = [selection for selection in fields['output'].extraSelections()
                           if selection.cursor.selectedText() == 'Encouragement']
                self.assertEqual(matches[0].format.foreground().color().name(), subject_color)
                self.assertEqual(fields['output'].palette().color(QtGui.QPalette.Text).name(), palette['text'])
                self.assertEqual(fields['output'].palette().color(QtGui.QPalette.Base).name(), palette['input'])
                self.assertGreaterEqual(contrast(subject_color, palette['input']), MIN_CONTRAST)
            self.assertEqual(self.session.project.to_dict(), before)
            for key in ('subject', 'type', 'color'):
                self.panel._open_list_manager(key)
                manager = self.panel._list_manager_dialogs[key]
                self.app.processEvents()
                expected = self.panel._display_prompt_color('scheme' if key == 'color' else key)
                self.assertEqual(manager.entry_edit.palette().color(QtGui.QPalette.Text).name(), expected)
                self.assertEqual(manager.list_widget.palette().color(QtGui.QPalette.Base).name(), palette['input'])
                manager.close()

    def test_semantic_registry_handles_ten_pages_and_all_theme_accents(self):
        from itertools import combinations
        from math import dist
        from ui_theme import THEMES
        from prompt_writer_colors import (category_colors, ensure_page_colors, oklab, contrast,
            theme_colors, SURFACE, INPUT_SURFACE, SELECTED_SURFACE, TEXT,
            MIN_CONTRAST, MIN_IDENTITY_DISTANCE, MIN_THEME_DISTANCE, display_color, surface_colors)
        self.session.replace(create_project(['illustration'] * 9), asset_root=self.root)
        original = dict(self.panel._page_colors)
        added = self.session.project.duplicate_page(next(iter(original)))
        self.assertEqual({key: self.panel._page_colors[key] for key in original}, original)
        self.assertNotIn(self.panel._page_colors[added], original.values())
        expected = dict(self.panel._page_colors)
        colors = [*category_colors().values(), *expected.values()]
        self.assertEqual(len(set(colors)), 18)
        for theme in THEMES.values():
            displayed = [display_color(color, theme.tokens) for color in colors]
            self.assertEqual(len(set(displayed)), 18)
            palette = surface_colors(theme.tokens)
            for color in displayed:
                for key in ('surface', 'input', 'selected'):
                    self.assertGreaterEqual(contrast(color, palette[key]), MIN_CONTRAST)
        for a, b in combinations(colors, 2):
            self.assertGreaterEqual(dist(oklab(a), oklab(b)), MIN_IDENTITY_DISTANCE)
        for color in colors:
            for reserved in theme_colors():
                self.assertGreaterEqual(dist(oklab(color), oklab(reserved)), MIN_THEME_DISTANCE)
            for background in (SURFACE, INPUT_SURFACE, SELECTED_SURFACE):
                self.assertGreaterEqual(contrast(color, background), MIN_CONTRAST)
                self.assertGreaterEqual(contrast(TEXT, background), MIN_CONTRAST)
        self.session.project.rename_page(added, 'Another illustration')
        self.session.project.reorder_pages(list(reversed(expected)))
        self.assertEqual(self.panel._page_colors, expected)
        self.assertTrue(self.panel.persist_project_state())
        stored = active_project_repository(self.root).load()
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.assertEqual(self.panel._page_colors, expected)
        before = self.session.project.to_dict()
        self.assertEqual(ensure_page_colors(self.session.project), expected)
        self.assertEqual(self.session.project.to_dict(), before)
        for combo in (self.panel.target_selector, self.panel.destination_selector):
            for index in range(combo.count()):
                self.assertEqual(combo.itemData(index, QtCore.Qt.ForegroundRole).name(), expected[combo.itemData(index)])

    def test_image_route_survives_selection_reorder_and_rejects_replaced_project(self):
        browser = SimpleNamespace(set_approval_target=mock.Mock(), provider='ChatGPT', submit_prompt=mock.Mock())
        self.panel._browser = browser
        self.panel._image_tab = SimpleNamespace(import_image=mock.Mock(return_value=True))
        self.panel._bind_image_target(self.ids[0])
        token = browser.set_approval_target.call_args.args[0]
        self.session.select_page(self.ids[-1])
        self.session.project.reorder_pages(list(reversed(self.ids)))
        self.panel._on_browser_image_ready(token, 'generated.png')
        self.assertEqual(self.panel._image_tab.import_image.call_args.args, (self.ids[0], 'generated.png'))
        self.panel._pending_approval_key = ''
        self.panel._image_tab.import_image.reset_mock()
        self.session.replace(create_project(), asset_root=self.root)
        self.panel._on_browser_image_ready(token, 'stale.png')
        self.panel._image_tab.import_image.assert_not_called()
        self.panel._browser = None

    def test_service_page_actions_send_latest_prompt_once_without_clipboard(self):
        project = create_project(['cover', 'illustration', 'illustration'])
        self.session.replace(project, asset_root=self.root)
        cover, first, second = [page['id'] for page in project.pages]
        callbacks = []
        browser = SimpleNamespace(provider='ChatGPT', view=QtWidgets.QWidget(),
            set_approval_target=mock.Mock(),
            submit_prompt=mock.Mock(side_effect=lambda text, callback: callbacks.append(callback)))
        self.addCleanup(browser.view.deleteLater)
        self.panel.attach_browser(browser)
        self.panel._show_conversation()
        browser.submit_prompt.assert_not_called()
        with mock.patch.object(self.panel, '_write_prompt_clipboard') as copy:
            for index, identity in enumerate((first, second, second)):
                self.panel._page_fields[identity]['directions'].setPlainText('Latest scene ' + str(index))
                self.panel._service_buttons[identity].click()
                current = self.panel.current_page_prompt(identity)
                self.assertEqual(browser.submit_prompt.call_args.args[0], current)
                self.assertEqual(self.panel._image_destination, identity)
                self.assertEqual(self.panel.service_prompt.toPlainText(), current)
                self.assertTrue(all(not button.isEnabled() for button in self.panel._service_buttons.values()))
                self.panel._service_buttons[cover].click()
                self.assertEqual(browser.submit_prompt.call_count, index + 1)
                callbacks[-1](True, 'Prompt sent.')
                self.assertFalse(self.panel._site_request_in_progress)
                self.assertTrue(all(button.isEnabled() for button in self.panel._service_buttons.values()))
            copy.assert_not_called()
        self.panel._service_buttons[first].click()
        callbacks[-1](False, 'Provider is busy.')
        self.assertIn('Provider is busy', self.panel._site_status.text())
        self.panel._service_buttons[second].click()
        callbacks[0](False, 'Old result')
        self.assertTrue(self.panel._site_request_in_progress)
        callbacks[-1](True, 'Prompt sent.')

    def test_page_cards_wrap_and_drag_without_accidental_clicks(self):
        from prompt_writer_browser import PAGE_CARD_MIME
        self.session.replace(create_project(['cover'] + ['illustration'] * 8 + ['end_page']), asset_root=self.root)
        self.panel._show_conversation()
        self.panel.resize(1280, 850)
        self.panel.show()
        self.app.processEvents()
        cards = list(self.panel._service_buttons.values())
        self.assertEqual(len(cards), 10)
        for index, card in enumerate(cards, 1):
            self.assertIn(f'Page {index}\n', card.text())
            self.assertIn('Drag into the message box', card.toolTip())
        for width in (1280, 480):
            self.panel.resize(width, 850)
            QtTest.QTest.qWait(40)
            for card in cards:
                self.assertGreaterEqual(card.x(), 0)
                self.assertLessEqual(card.geometry().right(), self.panel.conversation_bar.width())
            self.assertGreater(self.panel._output_stack.height(), 150)
        card = cards[2]
        clicked = QtTest.QSignalSpy(card.clicked)
        with mock.patch('prompt_writer_workspace.QtGui.QDrag') as drag_type:
            QtTest.QTest.mousePress(card, QtCore.Qt.LeftButton, pos=QtCore.QPoint(8, 8))
            move = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(50, 35),
                                    QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            self.app.sendEvent(card, move)
            QtTest.QTest.mouseRelease(card, QtCore.Qt.LeftButton, pos=QtCore.QPoint(50, 35))
            drag_type.assert_called_once_with(card)
            mime = drag_type.return_value.setMimeData.call_args.args[0]
            self.assertEqual(bytes(mime.data(PAGE_CARD_MIME)).decode(), card.page_id)
            self.assertFalse(mime.hasText())
            drag_type.return_value.exec.assert_called_once_with(QtCore.Qt.CopyAction)
        self.assertEqual(clicked.count(), 0)

    def test_create_image_sends_project_introduction_once_per_conversation(self):
        project = create_project(['cover'] + ['illustration'] * 5 + ['end_page'])
        self.session.replace(project, asset_root=self.root)
        callbacks = []
        browser = SimpleNamespace(provider='ChatGPT', view=QtWidgets.QWidget(),
            set_approval_target=mock.Mock(),
            submit_prompt=mock.Mock(side_effect=lambda text, callback: callbacks.append(callback)))
        self.addCleanup(browser.view.deleteLater)
        self.panel.attach_browser(browser)
        self.panel.cmb_subject.setCurrentText('Encouragement')
        self.panel.txt_global.setPlainText('A journey through a forest')
        self.panel._open_service()
        introduction = browser.submit_prompt.call_args.args[0]
        self.assertIn('7-page', introduction)
        self.assertIn('Encouragement', introduction)
        self.assertIn('A journey through a forest', introduction)
        self.assertIn('Illustration 5', introduction)
        self.assertIn('do not generate any images', introduction)
        self.assertTrue(introduction.endswith('Are you ready?'))
        self.assertFalse(self.panel.service_prompt.isVisibleTo(self.panel))
        self.panel._open_service()
        self.assertEqual(browser.submit_prompt.call_count, 1)
        callbacks[-1](False, 'Sign in first.')
        self.panel._open_service()
        self.assertEqual(browser.submit_prompt.call_count, 2)
        callbacks[-1](True, 'Sent')
        self.panel._show_writer()
        self.panel._open_service()
        self.assertEqual(browser.submit_prompt.call_count, 2)
        self.session.replace(create_project(['cover', 'letter']), asset_root=self.root)
        self.panel._open_service()
        self.assertEqual(browser.submit_prompt.call_count, 3)
        callbacks[0](True, 'Old result')
        self.assertTrue(self.panel._site_request_in_progress)
        callbacks[-1](True, 'Sent')
        self.assertIn('Project introduction sent', self.panel._site_status.text())

    def test_service_ordinary_edits_refresh_preview_without_copy_or_composer_writes(self):
        self.session.replace(create_project(['cover', 'illustration']), asset_root=self.root)
        browser = SimpleNamespace(provider='ChatGPT', view=QtWidgets.QWidget(),
            set_approval_target=mock.Mock(), submit_prompt=mock.Mock(), replace_composer=mock.Mock())
        self.addCleanup(browser.view.deleteLater)
        self.panel.attach_browser(browser)
        self.panel._show_conversation()
        with mock.patch.object(self.panel, '_write_prompt_clipboard') as copy:
            self.panel.txt_global.setPlainText('New shared wording')
            self.panel._page_fields[self.panel._view_page_id]['directions'].setPlainText('Latest page wording')
            self.assertIn('Latest page wording', self.panel.service_prompt.toPlainText())
            self.assertIn('New shared wording', self.panel.service_prompt.toPlainText())
            copy.assert_not_called()
        browser.replace_composer.assert_not_called()
        browser.submit_prompt.assert_not_called()

    def test_service_without_browser_requests_sign_in_without_copying(self):
        self.session.replace(create_project(['cover', 'illustration']), asset_root=self.root)
        requested = QtTest.QSignalSpy(self.panel.provider_requested)
        self.panel._show_conversation()
        self.assertIs(self.panel._output_stack.currentWidget(), self.panel._service_empty)
        with mock.patch.object(self.panel, '_write_prompt_clipboard') as copy:
            self.panel._activate_service_page(self.session.project.pages[1]['id'])
            copy.assert_not_called()
        self.assertEqual(requested.count(), 1)
        self.assertIn('sign in', self.panel.handoff_status.text())

    def test_failed_save_keeps_writer_open(self):
        self.panel.show()
        with mock.patch.object(self.panel, 'persist_project_state', return_value=False):
            self.assertFalse(self.panel.close_with_anim())
            self.assertTrue(self.panel.isVisible())

    def test_type_history_is_limited_and_independent_of_page_identity(self):
        for i in range(12):
            path = self.root / f'history-{i}.png'
            Image.new('RGB', (12, 18), (i*20, 10, 20)).save(path)
            remember_type_image(self.root, 'cover', path, str(i))
        remember_type_image(self.root, 'end_page', path, 'Ending')
        self.session.replace(create_project(), asset_root=self.root)
        self.assertEqual([label for _, label in recent_type_images(self.root, 'cover')],
                         [str(i) for i in reversed(range(2, 12))])
        self.assertEqual([label for _, label in recent_type_images(self.root, 'end_page')], ['Ending'])


class FloatingEditorTests(unittest.TestCase):
    setUpClass = classmethod(message_fixture.MessagesWorkspaceTests.setUpClass.__func__)
    setUp = message_fixture.MessagesWorkspaceTests.setUp
    tearDown = message_fixture.MessagesWorkspaceTests.tearDown
    _open_tab = message_fixture.MessagesWorkspaceTests._open_tab
    _background = message_fixture.MessagesWorkspaceTests._background
    _wait_for = message_fixture.MessagesWorkspaceTests._wait_for

    def test_message_preview_shows_text_without_presentation_controls(self):
        self._background(self.letter_a, 'navy')
        self.workspace.open_page(self.letter_a)
        self.workspace.render_preview()
        self._wait_for(lambda: self.workspace.shared_preview.viewed_page_id == self.letter_a)
        result = []
        self.workspace.shared_preview.view.page().runJavaScript("JSON.stringify({navigation:getComputedStyle(document.getElementById('page-navigation')).display, text:getComputedStyle(document.querySelector('.ls-editor-message')).opacity})", result.append)
        self._wait_for(lambda:bool(result))
        import json
        self.assertEqual(json.loads(result[0]), {'navigation':'none','text':'1'})

    def test_only_explicit_open_shows_editor_and_close_keeps_formatting(self):
        workspace = self.tab.messages_workspace
        self.assertFalse(workspace.editor_window.isVisible())
        workspace.open_page(self.letter_a)
        self.assertFalse(workspace.editor_window.isVisible())
        workspace.open_editor()
        self.assertTrue(workspace.editor_window.isVisible())
        workspace.editor.editor.setHtml('<p><b>Keep this letter</b></p>')
        workspace.editor_window.close()
        self.assertFalse(workspace.editor_window.isVisible())
        self.assertIn('Keep this letter', self.session.project.get_message(self.letter_a)['html'])
        workspace.open_editor()
        self.assertIn('font-weight', workspace.editor.editor.toHtml())
        with mock.patch.object(workspace, 'save', return_value=False):
            workspace.editor_window.close()
            self.assertTrue(workspace.editor_window.isVisible())

    def test_outside_click_closes_and_all_artwork_has_no_editor(self):
        workspace = self.tab.messages_workspace
        workspace.open_editor()
        outside = QtWidgets.QWidget()
        outside.setGeometry(0, 0, 20, 20)
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(0, 0),
                                 QtCore.QPointF(-100, -100), QtCore.Qt.LeftButton,
                                 QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        workspace.editor_window.eventFilter(outside, event)
        self.assertFalse(workspace.editor_window.isVisible())
        for page in self.session.project.pages:
            if page['type'] == 'letter':
                self.session.project.change_page_type(page['id'], 'illustration')
        self.assertFalse(workspace.open_editor_button.isEnabled())
        self.assertFalse(workspace.open_editor())


class FullPageIntegrationTests(unittest.TestCase):
    def test_writer_occupies_main_workspace_and_single_service_button_opens_conversation(self):
        import test_window_sizing as sizing
        sizing.WindowSizingTests.setUpClass()
        helper = sizing.WindowSizingTests()
        with helper.window() as window:
            project = create_project(('cover', 'letter', 'end_page'))
            window.project_session.replace(project)
            window.open_prompt_writer(page_id=project.pages[0]['id'])
            panel = window._prompt_writer_win
            self.assertIsNotNone(panel)
            self.assertIs(window.application_stack.currentWidget(), panel)
            self.assertFalse(panel.isWindow())
            panel.service_button.click()
            self.assertIs(panel._output_stack.currentWidget(), panel._browser.view)
            self.assertTrue(panel.conversation_bar.isVisible())
            panel.destination_selector.setCurrentIndex(2)
            panel.destination_selector.activated.emit(2)
            self.assertEqual(panel._image_destination, project.pages[-1]['id'])
            window.tabbar.setCurrentIndex(2)
            self.assertIs(window.application_stack.currentWidget(), window.body)
