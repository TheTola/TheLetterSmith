"""Exercise the real isolated WebChannel with a local, synthetic conversation."""
import base64
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from PySide6.QtWebEngineCore import QWebEngineScript
import project_paths
from prompt_writer_browser import PromptWriterBrowser


class BrowserImageActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ls-image-action-')
        self.previous = project_paths._APPLICATION_PATHS
        paths = project_paths.ApplicationPaths.for_runtime(Path(__file__).resolve().parents[1],
                      environ={'LETTER_SMITH_PROFILE_ROOT':self.temp.name})
        project_paths.configure_application_paths(paths)
        self.owner = QtWidgets.QWidget()
        self.browser = PromptWriterBrowser(self.owner)
        self.browser.provider = 'ChatGPT'
        self.browser.view.resize(850, 750)
        self.browser.view.show()
        output = io.BytesIO()
        Image.new('RGB', (512, 512), 'teal').save(output, format='PNG')
        self.data = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        self.browser.set_approval_target('cover-token', 'Cover')
        loaded = []
        self.browser.view.loadFinished.connect(loaded.append)
        self.browser.view.setHtml('<html><body style="margin:0"><article data-message-author-role="assistant"><img width="400" height="400" src="'+self.data+'"></article></body></html>', QtCore.QUrl('https://chatgpt.com/lettersmith-test'))
        self.wait_for(lambda: bool(loaded))
        self.wait_for(lambda: self.js("document.querySelectorAll('[data-lettersmith-image-action]').length") == 1)

    def tearDown(self):
        self.browser.view.close()
        self.browser.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.browser._image_downloads.cleanup()
        self.owner.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        QtTest.QTest.qWait(250)
        project_paths.configure_application_paths(self.previous)
        for attempt in range(20):
            try:
                self.temp.cleanup()
                break
            except OSError as error:
                if attempt == 19 or getattr(error, 'winerror', None) not in (5, 32, 145):
                    raise
                QtTest.QTest.qWait(100)  # WebEngine releases its profile lock asynchronously.

    def wait_for(self, predicate):
        for _ in range(200):
            if predicate():
                return
            self.app.processEvents()
            time.sleep(.02)
        self.fail('Timed out waiting for image actions')

    def js(self, code):
        result = []
        self.browser.view.page().runJavaScript(code, result.append)
        self.wait_for(lambda: bool(result))
        return result[0]

    def test_trusted_click_keeps_original_target_after_new_destination(self):
        self.browser.set_approval_target('end-token', 'End Page')
        QtTest.QTest.qWait(100)
        self.assertEqual(self.js("document.querySelector('[data-lettersmith-image-action]').textContent"), 'Use image for Cover')
        rect = self.js("JSON.stringify((()=>{let r=document.querySelector('[data-lettersmith-image-action]').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())")
        import json
        x,y = json.loads(rect)
        with mock.patch.object(self.browser, '_approve_image_url') as approve:
            QtTest.QTest.mouseClick(self.browser.view.focusProxy(), QtCore.Qt.LeftButton, pos=QtCore.QPoint(round(x),round(y)))
            self.wait_for(lambda: approve.called)
            self.assertEqual(approve.call_args.kwargs['target'], 'cover-token')
        self.js("document.body.insertAdjacentHTML('beforeend','<article data-message-author-role=\"assistant\"><img width=400 height=400 src=\""+self.data+"\"></article>')")
        self.wait_for(lambda:self.js("document.querySelectorAll('[data-lettersmith-image-action]').length") == 2)
        self.assertEqual(self.js("Array.from(document.querySelectorAll('[data-lettersmith-image-action]')).map(x=>x.textContent).join('|')"), 'Use image for Cover|Use image for End Page')

    def test_scripted_click_cannot_trigger_import_and_duplicate_scan_is_safe(self):
        with mock.patch.object(self.browser, '_approve_image_url') as approve:
            self.js("document.querySelector('[data-lettersmith-image-action]').click()")
            self.browser.image_actions.install()
            QtTest.QTest.qWait(150)
            approve.assert_not_called()
            self.assertEqual(self.js("document.querySelectorAll('[data-lettersmith-image-action]').length"), 1)

    def test_untrusted_origin_and_unknown_token_are_rejected(self):
        with mock.patch.object(self.browser, '_approve_image_url') as approve:
            self.browser.image_actions.useImage('https://example.com/image.png', 'unknown')
            approve.assert_not_called()
            with mock.patch.object(self.browser.view, 'url', return_value=QtCore.QUrl('https://example.com/')):
                self.browser.image_actions.useImage('https://example.com/image.png', 'cover-token')
            approve.assert_not_called()

    def test_composer_replacement_preserves_conversation_and_never_sends(self):
        self.js("""document.body.insertAdjacentHTML('beforeend',
          '<textarea id="prompt-textarea">Old unsent draft</textarea><input id="attachment" type="file">' +
          '<button data-testid="send-button" onclick="window.sent=true">Send</button>')""")
        original = self.js("document.querySelector('article').outerHTML")
        for text in ('Cover prompt\nNew line 🌲', 'Illustration prompt\nReplace, do not append'):
            result = []
            self.browser.replace_composer(text, lambda *args: result.append(args))
            self.wait_for(lambda: bool(result))
            self.assertTrue(result[0][0])
            self.assertEqual(self.js("document.querySelector('textarea').value"), text)
            self.assertEqual(self.js("document.querySelector('article').outerHTML"), original)
            self.assertTrue(self.js("!!document.querySelector('#attachment')"))
            self.assertFalse(self.js("!!window.sent"))
        self.js("document.querySelector('textarea').remove(); document.body.insertAdjacentHTML('beforeend', '<div id=\"prompt-textarea\" contenteditable=\"true\"><p>Old rich draft</p></div>')")
        text = 'Complete current prompt\nSecond paragraph 🌲'
        result = []
        self.browser.replace_composer(text, lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertTrue(result[0][0])
        self.assertEqual(self.js("document.querySelector('#prompt-textarea').innerText"), text)
        self.assertFalse(self.js("!!window.sent"))

    def test_unavailable_composer_never_targets_an_arbitrary_input_or_attachment(self):
        self.js("document.body.insertAdjacentHTML('beforeend', '<input value=\"Keep me\"><div contenteditable=\"true\" role=\"textbox\">Unrelated editor</div>')")
        result = []
        self.browser.replace_composer('New prompt', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js("document.querySelector('input').value"), 'Keep me')
        self.assertEqual(self.js("document.querySelector('[contenteditable]').innerText"), 'Unrelated editor')
        self.js("document.querySelector('[contenteditable]').id='prompt-textarea'; document.querySelector('#prompt-textarea').insertAdjacentHTML('beforeend','<span contenteditable=\"false\">Attachment</span>')")
        before = self.js("document.querySelector('#prompt-textarea').innerHTML")
        result.clear()
        self.browser.replace_composer('New prompt', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js("document.querySelector('#prompt-textarea').innerHTML"), before)

    def test_rapid_replacements_keep_only_latest_unsent_prompt(self):
        self.js("document.body.insertAdjacentHTML('beforeend', '<textarea id=\"prompt-textarea\">Old</textarea>')")
        results = []
        for index in range(6):
            self.browser.replace_composer('Prompt ' + str(index), lambda *args: results.append(args))
        self.wait_for(lambda: bool(results))
        self.assertEqual(self.js("document.querySelector('textarea').value"), 'Prompt 5')
        self.assertTrue(results[-1][0])

    def test_gemini_prompt_replacement_and_busy_fallback(self):
        loaded = []
        self.browser.view.loadFinished.connect(loaded.append)
        self.browser.provider = 'Gemini'
        self.browser.view.setHtml('<textarea aria-label="Enter a prompt">Previous Gemini draft</textarea>',
                                  QtCore.QUrl('https://gemini.google.com/app'))
        self.wait_for(lambda: bool(loaded))
        result = []
        self.browser.replace_composer('Gemini page prompt\nComplete text', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertTrue(result[0][0])
        self.assertEqual(self.js("document.querySelector('textarea').value"), 'Gemini page prompt\nComplete text')
        self.js("document.body.insertAdjacentHTML('beforeend','<button aria-label=\"Stop generating\">Stop</button>')")
        result.clear()
        self.browser.replace_composer('Do not overwrite busy draft', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js("document.querySelector('textarea').value"), 'Gemini page prompt\nComplete text')

    def test_response_created_after_switch_keeps_the_submitted_draft_page(self):
        import json
        self.js("document.body.insertAdjacentHTML('beforeend', '<textarea id=\"prompt-textarea\"></textarea><button data-testid=\"send-button\">Send</button>')")
        result = []
        self.browser.replace_composer('Cover prompt', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertTrue(result[0][0])
        self.browser.set_approval_target('illustration-token', 'Illustration 2')
        self.js("document.querySelector('[data-testid=send-button]').addEventListener('click', event => window.sendActivated = event.isTrusted)")
        rect = self.js("JSON.stringify((()=>{let r=document.querySelector('[data-testid=send-button]').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())")
        x, y = json.loads(rect)
        QtTest.QTest.mouseClick(self.browser.view.focusProxy(), QtCore.Qt.LeftButton,
                               pos=QtCore.QPoint(round(x), round(y)))
        self.wait_for(lambda: self.js('!!window.sendActivated'))
        self.js("document.body.insertAdjacentHTML('beforeend','<article data-message-author-role=\"assistant\"><img width=400 height=400 src=\""+self.data+"\"></article>')")
        self.wait_for(lambda:self.js("document.querySelectorAll('[data-lettersmith-image-action]').length") == 2)
        self.assertEqual(self.js("Array.from(document.querySelectorAll('[data-lettersmith-image-action]')).map(x=>x.textContent).join('|')"),
                         'Use image for Cover|Use image for Cover')

    def install_sending_composer(self, *, rich=False):
        import json
        editor = ('<div id="prompt-textarea" contenteditable="true"><p>Previous draft</p></div>' if rich
                  else '<textarea id="prompt-textarea">Previous draft</textarea>')
        self.js("document.body.insertAdjacentHTML('beforeend', " + json.dumps(editor +
                '<button data-testid="send-button" aria-label="Send prompt">Send</button>') + ")")
        self.js("""window.sentPrompts = [];
          document.querySelector('[data-testid=send-button]').onclick = () => {
            const editor = document.querySelector('#prompt-textarea');
            const text = editor.isContentEditable ? editor.innerText : editor.value;
            window.sentPrompts.push(text);
            const user = document.createElement('article');
            user.dataset.messageAuthorRole = 'user'; user.style.whiteSpace = 'pre-wrap';
            user.textContent = text; document.body.appendChild(user);
            if (editor.isContentEditable) editor.innerHTML = '<p><br></p>'; else editor.value = '';
            const reply = document.createElement('article');
            reply.dataset.messageAuthorRole = 'assistant'; reply.textContent = 'Ready';
            document.body.appendChild(reply);
          };""")

    def test_create_image_and_page_buttons_send_complete_prompts_in_real_browser(self):
        from project_model import create_project
        from project_session import get_project_session, release_project_session
        from prompt_writer_workspace import PromptWriterPanel
        self.install_sending_composer(rich=True)
        root = project_paths.application_paths().workspace_root
        root.mkdir(parents=True, exist_ok=True)
        session = get_project_session(root)
        project = create_project(['cover', 'illustration', 'illustration'])
        session.replace(project, asset_root=root)
        first, second = [page['id'] for page in project.pages[1:]]
        session.select_page(second)
        panel = PromptWriterPanel(project_root=str(root), page_id=second)
        try:
            panel.attach_browser(self.browser)
            panel.resize(1100, 950); panel.show(); self.app.processEvents()
            QtTest.QTest.mouseClick(panel.service_button, QtCore.Qt.LeftButton)
            self.wait_for(lambda: not panel._site_request_in_progress)
            intro = self.js('window.sentPrompts[0]')
            self.assertTrue(intro.endswith('Are you ready?'))
            self.assertIn('3-page', intro)
            self.assertFalse(panel.service_prompt.isVisibleTo(panel))
            panel._show_writer(); panel._open_service()
            self.assertEqual(self.js('window.sentPrompts.length'), 1)
            with mock.patch.object(panel, '_write_prompt_clipboard', side_effect=AssertionError('Clipboard required')):
                for index, identity in enumerate((first, second, second)):
                    panel._page_fields[identity]['directions'].setPlainText('New scene 🌲\nLine two ' + str(index))
                    self.app.processEvents()
                    QtTest.QTest.mouseClick(panel._service_buttons[identity], QtCore.Qt.LeftButton)
                    prompt = panel.current_page_prompt(identity)
                    self.wait_for(lambda: not panel._site_request_in_progress)
                    self.assertEqual(self.js('window.sentPrompts.length'), index + 2)
                    self.assertEqual(self.js('window.sentPrompts.at(-1)'), prompt)
                    self.assertIn('Prompt sent', panel._site_status.text())
                    self.assertEqual(panel._image_destination, identity)
                    token = panel._image_target_token
                    panel.select_page(project.pages[0]['id'])
                    self.js("document.querySelectorAll('[data-message-author-role=assistant]')[" + str(index+2) +
                            "].insertAdjacentHTML('beforeend','<img width=400 height=400 src=\"" + self.data + "\">')")
                    self.wait_for(lambda: self.js("document.querySelectorAll('[data-lettersmith-image-action]').length") == index + 2)
                    self.assertEqual(self.js("document.querySelectorAll('[data-lettersmith-image-action]')[" + str(index+1) + "].textContent"),
                                     'Use image for ' + panel._service_page_label(identity))
                    self.assertEqual(panel._browser_targets[token].page_id, identity)
        finally:
            panel.shutdown(); panel.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            release_project_session(root)

    def test_page_card_drop_sends_latest_prompt_only_inside_composer(self):
        import json
        from prompt_writer_browser import PAGE_CARD_MIME
        from prompt_writer_workspace import PromptWriterPanel
        from project_model import create_project
        from project_session import get_project_session, release_project_session
        self.install_sending_composer(rich=True)
        self.js("document.querySelector('#prompt-textarea').style.cssText='min-height:70px;border:1px solid gray';")
        root = project_paths.application_paths().workspace_root
        root.mkdir(parents=True, exist_ok=True)
        session = get_project_session(root)
        project = create_project(['cover', 'illustration', 'illustration'])
        session.replace(project, asset_root=root)
        first, second = [page['id'] for page in project.pages[1:]]
        panel = PromptWriterPanel(project_root=str(root), page_id=first)
        try:
            panel.attach_browser(self.browser)
            panel.resize(1100, 950); panel.show(); panel._show_conversation()
            QtTest.QTest.qWait(100)
            card = panel._service_buttons[second]
            def drop(source, point):
                mime = QtCore.QMimeData()
                mime.setData(PAGE_CARD_MIME, second.encode())
                class Enter(QtGui.QDragEnterEvent):
                    def source(self): return source
                class Drop(QtGui.QDropEvent):
                    def source(self): return source
                target = self.browser.view.focusProxy()
                position = target.mapFrom(self.browser.view, point)
                enter = Enter(position, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                event = Drop(QtCore.QPointF(position), QtCore.Qt.CopyAction, mime,
                             QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                self.app.sendEvent(target, enter)
                self.app.sendEvent(target, event)
                return event
            def composer_center():
                xy = json.loads(self.js("JSON.stringify((()=>{const r=document.querySelector('#prompt-textarea').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())"))
                zoom = self.browser.view.zoomFactor()
                return QtCore.QPoint(round(xy[0]*zoom), round(xy[1]*zoom))
            drop(card, QtCore.QPoint(20, 20))
            self.wait_for(lambda: panel.handoff_status.isVisible())
            self.assertEqual(self.js('window.sentPrompts.length'), 0)
            self.assertEqual(self.js("document.querySelector('#prompt-textarea').innerText"), 'Previous draft')
            self.assertFalse(drop(None, composer_center()).isAccepted())
            for zoom in (1.0, 1.25):
                self.browser.view.setZoomFactor(zoom)
                QtTest.QTest.qWait(100)
                panel._page_fields[second]['directions'].setPlainText('Latest dropped scene ' + str(zoom))
                before = self.js('window.sentPrompts.length')
                event = drop(card, composer_center())
                self.assertTrue(event.isAccepted())
                self.wait_for(lambda: self.js('window.sentPrompts.length') == before + 1)
                self.wait_for(lambda: not panel._site_request_in_progress)
                self.assertEqual(self.js('window.sentPrompts.at(-1)'), panel.current_page_prompt(second))
                self.assertEqual(panel._image_destination, second)
                self.assertEqual(self.js("document.querySelector('#prompt-textarea').innerText").strip(), '')
            # A card from a replaced project cannot send, even if the ID is reused.
            stale = card
            session.replace(project, asset_root=root)
            with mock.patch.object(self.browser, 'is_composer_at') as hit_test:
                panel._on_page_card_dropped(stale, composer_center())
                hit_test.assert_not_called()
            current = panel._service_buttons[second]
            callbacks = []
            with mock.patch.object(self.browser, 'is_composer_at', side_effect=lambda pos, callback: callbacks.append(callback)):
                panel._on_page_card_dropped(current, composer_center())
                panel._show_writer()
                callbacks[-1](True)
            self.assertEqual(self.js('window.sentPrompts.length'), 2)
        finally:
            panel.shutdown(); panel.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            release_project_session(root)

    def test_submission_verifies_new_full_messages_and_blocks_duplicate_clicks(self):
        self.install_sending_composer()
        prompt = ('Complete prompt 🌲\n' + 'Long exact content. ' * 20).strip()
        for index in range(2):
            result, duplicate = [], []
            self.browser.submit_prompt(prompt, lambda *args: result.append(args))
            self.browser.submit_prompt('Must not be sent', lambda *args: duplicate.append(args))
            self.assertFalse(duplicate[0][0])
            self.wait_for(lambda: bool(result))
            self.assertTrue(result[0][0], result)
            self.assertEqual(self.js('window.sentPrompts.length'), index + 1)
            self.assertEqual(self.js('window.sentPrompts.at(-1)'), prompt)
        # An older identical message cannot prove that a new click was accepted.
        self.js("document.querySelector('[data-testid=send-button]').onclick = () => {}")
        result = []
        self.browser.submit_prompt(prompt, lambda *args: result.append(args))
        QtTest.QTest.qWait(1000)
        self.assertEqual(result, [])
        self.browser._submission_checks = 20
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js('window.sentPrompts.length'), 2)

    def test_submission_waits_for_composer_and_rejects_busy_or_changed_conversation(self):
        result = []
        self.browser.submit_prompt('Delayed composer prompt', lambda *args: result.append(args))
        QtTest.QTest.qWait(100)
        self.install_sending_composer()
        self.js("document.querySelector('[data-testid=send-button]').disabled = true; "
                "setTimeout(() => document.querySelector('[data-testid=send-button]').disabled = false, 900)")
        self.wait_for(lambda: bool(result))
        self.assertTrue(result[0][0], result)
        self.js("document.body.insertAdjacentHTML('beforeend','<button aria-label=\"Stop generating\">Stop</button>')")
        result.clear()
        self.browser.submit_prompt('Busy prompt', lambda *args: result.append(args))
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js('window.sentPrompts.length'), 1)
        self.js("document.querySelector('[aria-label=\"Stop generating\"]').remove()")
        result.clear()
        self.browser.submit_prompt('Wrong conversation', lambda *args: result.append(args))
        self.wait_for(lambda: self.js("document.querySelector('#prompt-textarea').value") == 'Wrong conversation')
        self.js("history.pushState({},'', '/c/different-conversation')")
        self.wait_for(lambda: bool(result))
        self.assertFalse(result[0][0])
        self.assertEqual(self.js('window.sentPrompts.length'), 1)

    def test_chatgpt_theme_updates_survive_navigation_and_preserve_content(self):
        from types import SimpleNamespace
        from PySide6.QtGui import QColor
        from ui_theme import THEMES

        original_image = self.js("document.querySelector('img').src")
        self.js("document.body.insertAdjacentHTML('beforeend', '<textarea id=\"prompt-textarea\">Unsent draft</textarea>')")
        for theme in THEMES.values():
            with self.subTest(theme=theme.theme_id):
                self.browser._theme_service = SimpleNamespace(tokens=theme.tokens)
                self.browser._apply_theme()
                color = QColor(theme.tokens.background)
                expected = f'rgb({color.red()}, {color.green()}, {color.blue()})'
                self.wait_for(lambda: self.js("getComputedStyle(document.body).backgroundColor") == expected)
                self.assertEqual(self.js("document.querySelectorAll('#lettersmith-provider-theme').length"), 1)
                self.assertEqual(self.js("document.querySelector('img').src"), original_image)
                self.assertEqual(self.js("document.querySelector('textarea').value"), 'Unsent draft')
                self.assertEqual(len(self.browser.profile.scripts().find('lettersmith-provider-theme')), 1)
                scheme = 'dark' if color.lightness() < 128 else 'light'
                self.js("document.documentElement.className = '" + ('light' if scheme == 'dark' else 'dark') + "'")
                self.wait_for(lambda: self.js("document.documentElement.classList.contains('" + scheme + "')"))

        loaded = []
        self.browser.view.loadFinished.connect(loaded.append)
        for host, expected_count in [('chatgpt.com', 1), ('gemini.google.com', 0), ('auth.openai.com', 0)]:
            loaded.clear()
            self.browser.view.setHtml('<html><body>Navigation</body></html>', QtCore.QUrl(f'https://{host}/'))
            self.wait_for(lambda: bool(loaded))
            self.assertEqual(self.js("document.querySelectorAll('#lettersmith-provider-theme').length"), expected_count)

    def test_download_flows_into_original_page_through_real_import_worker(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from Image_tab import ImageTab
        from project_model import create_project
        from project_session import get_project_session, release_project_session
        from prompt_writer_workspace import PromptWriterPanel
        output = io.BytesIO()
        Image.new('RGB', (512, 768), 'seagreen').save(output, format='PNG')
        png = output.getvalue()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(png)))
                self.end_headers()
                self.wfile.write(png)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = project_paths.application_paths().workspace_root
        root.mkdir(parents=True, exist_ok=True)
        session = get_project_session(root)
        project = create_project(('cover', 'letter', 'end_page'))
        session.replace(project, asset_root=root)
        tab = ImageTab(root)
        panel = PromptWriterPanel(project_root=str(root))
        try:
            panel.attach_browser(self.browser, tab)
            token = self.browser._approval_target
            cover, end = project.pages[0]['id'], project.pages[-1]['id']
            session.select_page(end)
            finished = []
            tab.artwork_import_completed.connect(lambda *args: finished.append(args))
            self.browser._approve_image_url(f'http://127.0.0.1:{server.server_port}/generated.png', self.browser.view.page(), target=token)
            self.wait_for(lambda: bool(finished))
            self.assertEqual(finished[0][0], cover)
            self.assertEqual(finished[0][2], '')
            self.assertIsNone(session.project.get_page(end)['artwork']['asset'])
            with Image.open(tab.artwork.resolve_asset(cover)) as saved:
                self.assertEqual(saved.size, (512, 768))
            self.assertEqual(session.selected_page_id, end)
        finally:
            panel.shutdown()
            panel.deleteLater()
            tab.shutdown()
            tab.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            release_project_session(root)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
