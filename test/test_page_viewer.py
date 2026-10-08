"""Exercise variable page layouts in the actual exported browser viewer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PIL import Image
from PySide6 import QtCore, QtTest, QtWidgets
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from page_layout import DEFAULT_PAGES, LetterPage
from Template import TEMPLATE_CSS, TEMPLATE_JS, template_for_pages


class PageViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def js(self, source):
        result = []
        loop = QtCore.QEventLoop()
        self.view.page().runJavaScript(
            f"JSON.stringify((() => {{ {source} }})())",
            lambda value: (result.append(value), loop.quit()),
        )
        QtCore.QTimer.singleShot(4000, loop.quit)
        loop.exec()
        self.assertTrue(result, "Browser script did not complete")
        return json.loads(result[0])

    def wait_for(self, source):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < 6000:
            if self.js(source):
                return
            QtTest.QTest.qWait(50)
        self.fail(f"Viewer did not reach expected state: {source}")

    def test_navigation_shows_message_on_each_wall_and_handles_no_walls(self):
        layouts = (
            (DEFAULT_PAGES[0], LetterPage("letter", "wall"), LetterPage("wall", "letter"),
             LetterPage("page_1", "wall"), LetterPage("page_2", "wall"), DEFAULT_PAGES[-1]),
            (DEFAULT_PAGES[0], DEFAULT_PAGES[-1]),
        )
        for pages in layouts:
            with self.subTest(count=len(pages)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                gallery = root / "gallery/pages"
                gallery.mkdir(parents=True)
                for page in pages:
                    Image.new("RGB", (80, 120), "navy").save(gallery / page.filename)
                html = template_for_pages(pages)
                for key, value in {
                    "CSP_NONCE": "page-viewer-test", "TITLE": "Variable pages",
                    "MUSIC_PRELOAD_HTML": "", "TITLE_BANNER_TEXT_RGB": "255,255,255",
                    "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "<p>Shared message</p>",
                    "INITIAL_VOLUME": "0", "MUSIC_CROSSFADE_MS": "0",
                    "MUSIC_PLAYLIST_JSON": "[]", "IMAGE_ANIMATIONS_JSON": "{}", "HAS_MESSAGE_JSON": "true",
                }.items():
                    html = html.replace("{{" + key + "}}", value)
                (root / "index.html").write_text(html, encoding="utf-8")
                (root / "styles.css").write_text(TEMPLATE_CSS, encoding="utf-8")
                (root / "script.js").write_text(TEMPLATE_JS, encoding="utf-8")
                self.view = QWebEngineView()
                self.view.resize(800, 600)
                script = QWebEngineScript()
                script.setInjectionPoint(QWebEngineScript.DocumentCreation)
                script.setWorldId(QWebEngineScript.MainWorld)
                script.setSourceCode('''
                    window.viewerErrors = [];
                    window.addEventListener('error', event => viewerErrors.push(event.message));
                    const nativeMatch = window.matchMedia.bind(window);
                    window.matchMedia = query => query.includes('prefers-reduced-motion')
                      ? {matches:true, addEventListener(){}, removeEventListener(){}}
                      : nativeMatch(query);
                ''')
                self.view.page().scripts().insert(script)
                self.view.show()
                try:
                    loaded = []
                    loop = QtCore.QEventLoop()
                    self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
                    self.view.setUrl(QtCore.QUrl.fromLocalFile(str(root / "index.html")))
                    QtCore.QTimer.singleShot(5000, loop.quit)
                    loop.exec()
                    self.assertEqual(loaded, [True])
                    self.js("document.getElementById('curtain-overlay').click(); return true;")
                    self.wait_for("return !document.getElementById('next').disabled;")
                    for index, page in enumerate(pages):
                        if index:
                            self.js("document.getElementById('next').click(); return true;")
                        self.wait_for(f"return document.querySelector('.slide.active').dataset.index === '{index}';")
                        if page.kind == "wall":
                            self.wait_for("return document.getElementById('textWall').classList.contains('is-open');")
                            self.assertGreater(self.js("return document.getElementById('textWall').getBoundingClientRect().width;"), 0)
                            self.js("document.getElementById('close-text').click(); return true;")
                            self.assertEqual(self.js("return document.getElementById('textWall').getAttribute('aria-hidden');"), "true")
                            self.js("document.getElementById('open-text').click(); return true;")
                            self.wait_for("return document.getElementById('textWall').classList.contains('is-open');")
                        else:
                            self.assertEqual(self.js("return document.getElementById('textWall').getAttribute('aria-hidden');"), "true")
                        if index < len(pages) - 1:
                            self.wait_for("return !document.getElementById('next').disabled;")
                    self.assertTrue(self.js("return document.getElementById('next').disabled;"))
                    self.wait_for("return !document.getElementById('prev').disabled;")
                    self.js("document.getElementById('prev').click(); return true;")
                    self.wait_for(f"return document.querySelector('.slide.active').dataset.index === '{len(pages) - 2}';")
                    if pages[-2].kind == "wall":
                        self.wait_for("return document.getElementById('textWall').classList.contains('is-open');")
                    self.assertEqual(self.js("return window.viewerErrors;"), [])
                finally:
                    self.view.close()
                    self.view.deleteLater()
                    self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
