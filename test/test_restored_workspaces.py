"""Behavior checks for the restored interfaces, using canonical project data."""
import unittest
from unittest import mock
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from PIL import Image

from prompt_writer_workspace import PromptWriterPanel
from project_model import create_project
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

    def test_all_ten_pages_edit_together_with_dropdown_and_no_dimensions(self):
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
            directions = self.panel.directions_container.geometry()
            self.assertGreater(self.panel.outputs_container.y(), directions.bottom())

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
