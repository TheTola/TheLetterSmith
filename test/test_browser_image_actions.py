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
from PySide6 import QtCore, QtWidgets, QtTest
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
        self.temp.cleanup()

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
