"""Exercise variable page layouts in the actual exported browser viewer."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
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
from Template import TEMPLATE_CSS, TEMPLATE_HTML, TEMPLATE_JS, template_for_pages, template_for_project_pages


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

    @contextmanager
    def canonical_view(self, pages, *, initial_muted=None, initial_volume=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gallery = root / "gallery/pages"
            gallery.mkdir(parents=True)
            for page in pages:
                Image.new("RGB", (160, 240), page.get("color", "navy")).save(root / page["source"])
            controls = root / "gallery/controls"
            controls.mkdir()
            for name in ("cleft", "cright", "bannerman", "ppage", "npage", "volon", "voloff", "showmessageicon"):
                Image.new("RGBA", (24, 24), "#404050").save(controls / f"{name}.png")
            shell = TEMPLATE_HTML
            for key, value in {
                "CSP_NONCE": "canonical-page-test", "TITLE": "Independent pages",
                "MUSIC_PRELOAD_HTML": "", "TITLE_BANNER_TEXT_RGB": "255,255,255",
                "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "",
                "INITIAL_VOLUME": str(initial_volume), "MUSIC_CROSSFADE_MS": "0",
                "MUSIC_PLAYLIST_JSON": "[]", "IMAGE_ANIMATIONS_JSON": "{}", "HAS_MESSAGE_JSON": "false",
            }.items():
                shell = shell.replace("{{" + key + "}}", value)
            if initial_muted is not None:
                shell = shell.replace('<body>', f'<body data-initial-muted="{str(initial_muted).lower()}" data-playback-id="{root.name}">')
            (root / "index.html").write_text(template_for_project_pages(pages, template_html=shell), encoding="utf-8")
            (root / "styles.css").write_text(TEMPLATE_CSS, encoding="utf-8")
            (root / "script.js").write_text(TEMPLATE_JS, encoding="utf-8")
            self.view = QWebEngineView()
            self.view.resize(1000, 700)
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
                self.wait_for("return !!window.LetterSmithViewer;")
                self.js("document.getElementById('curtain-overlay').click(); return true;")
                self.wait_for("return !document.getElementById('next').disabled;")
                self.wait_for("return Number(getComputedStyle(document.getElementById('slideshow')).opacity) > .99;")
                yield root
                self.assertEqual(self.js("return window.viewerErrors;"), [])
            finally:
                self.view.close()
                self.view.deleteLater()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_canonical_messages_keep_identity_formatting_and_navigation(self):
        pages = [
            {"id": "cover", "type": "cover", "name": "Opening", "source": "gallery/pages/cover.png"},
            {"id": "letter-a", "type": "letter", "name": "Same name", "source": "gallery/pages/a.png",
             "color": "#804040", "message_html": '<p style="font-size:24px;color:#101010"><b>Message A</b> {{CSP_NONCE}}</p>',
             "overlay_style": "--message-overlay-surface-opacity:.9"},
            {"id": "ending", "type": "end_page", "name": "Middle ending", "source": "gallery/pages/end.png"},
            {"id": "art", "type": "illustration", "name": "Artwork", "source": "gallery/pages/art.png",
             "message_html": "INACTIVE MESSAGE MUST NOT RENDER"},
            {"id": "letter-b", "type": "letter", "name": "Same name", "source": "gallery/pages/b.png",
             "color": "#204060", "message_html": '<p style="font-size:31px;color:#101010"><i>Message B</i></p>',
             "overlay_style": "--message-overlay-surface-opacity:.7"},
        ]
        for reordered in (False, True):
            order = list(pages)
            if reordered:
                order[1], order[4] = order[4], order[1]
            with self.subTest(reordered=reordered), self.canonical_view(order):
                self.assertEqual(self.js("return document.querySelectorAll('.text-wall').length;"), 2)
                self.assertTrue(self.js("const ids=Array.from(document.querySelectorAll('[id]'), e=>e.id); return ids.length === new Set(ids).size;"))
                self.assertNotIn("INACTIVE MESSAGE", self.js("return document.body.textContent;"))
                for page_id, text, font_size, source in (
                    ("letter-a", "Message A", "24px", "a.png"),
                    ("letter-b", "Message B", "31px", "b.png"),
                ):
                    self.assertTrue(self.js(f"return LetterSmithViewer.goToPage('{page_id}');"))
                    self.wait_for("return Array.from(document.querySelectorAll('.text-wall')).some(item => item.dataset.pageId === LetterSmithViewer.currentPageId() && item.classList.contains('is-open'));")
                    self.assertEqual(self.js("return LetterSmithViewer.currentPageId();"), page_id)
                    self.assertIn(text, self.js("return document.querySelector('.text-wall.is-open .text-wall-content').textContent;"))
                    if page_id == "letter-a":
                        self.assertIn("{{CSP_NONCE}}", self.js("return document.querySelector('.text-wall.is-open .text-wall-content').textContent;"))
                    self.assertEqual(self.js("return getComputedStyle(document.querySelector('.text-wall.is-open .text-wall-content p')).fontSize;"), font_size)
                    self.assertTrue(self.js(f"return document.querySelector('.slide.active > img').src.endsWith('/{source}');"))
                    self.assertEqual(self.js("return document.querySelectorAll('.text-wall.is-open').length;"), 1)
                    self.assertTrue(self.js("const r=document.querySelector('.text-wall.is-open').getBoundingClientRect(); return r.width > 0 && r.height > 0;"))
                    self.wait_for("return document.getElementById('title-banner').getAttribute('aria-hidden') === 'true';")
                    self.wait_for("return Number(getComputedStyle(document.querySelector('.text-wall.is-open')).opacity) > .99;")
                    for width, height, mode in ((1000, 700, "landscape"), (390, 844, "portrait")):
                        self.view.resize(width, height)
                        self.wait_for(f"return window.innerWidth === {width} && window.innerHeight === {height};")
                        self.wait_for("const r=document.querySelector('.text-wall.is-open').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth;")
                        QtTest.QTest.qWait(400)
                        self.assertEqual(self.js("return getComputedStyle(document.querySelector('.text-wall.is-open .text-wall-content p')).fontSize;"), font_size)
                        profile = os.environ.get("LETTER_SMITH_PROFILE_ROOT")
                        if profile and not reordered:
                            screenshots = Path(profile) / "viewer-screenshots"
                            screenshots.mkdir(parents=True, exist_ok=True)
                            self.assertTrue(self.view.grab().save(str(screenshots / f"{page_id}-{mode}.png")))
                self.js("LetterSmithViewer.goToPage('ending'); return true;")
                self.wait_for("return !document.getElementById('next').disabled;")
                self.js("document.getElementById('next').click(); return true;")
                self.wait_for("return LetterSmithViewer.currentPageId() === 'art';")
                self.assertEqual(self.js("return document.getElementById('page-indicator').textContent;"), "Page 4 of 5 — Artwork")
                self.assertEqual(self.js("return document.querySelectorAll('.text-wall.is-open').length;"), 0)
                self.wait_for("return !document.getElementById('page-selector').disabled;")
                self.js("const selector=document.getElementById('page-selector'); selector.value='0'; selector.dispatchEvent(new Event('change')); return true;")
                self.wait_for("return LetterSmithViewer.currentPageId() === 'cover';")
                self.assertFalse(self.js("return LetterSmithViewer.goToPage('deleted-page');"))

    def test_canonical_artwork_only_has_no_message_layer(self):
        pages = [
            {"id": "first", "type": "end_page", "name": "First", "source": "gallery/pages/first.png"},
            {"id": "last", "type": "illustration", "name": "Last", "source": "gallery/pages/last.png"},
        ]
        with self.canonical_view(pages):
            self.assertEqual(self.js("return document.querySelectorAll('.text-wall').length;"), 0)
            self.js("document.getElementById('next').click(); return true;")
            self.wait_for("return LetterSmithViewer.currentPageId() === 'last';")
            self.assertTrue(self.js("return document.getElementById('next').disabled;"))
            self.assertEqual(self.js("return document.getElementById('page-indicator').textContent;"), "Page 2 of 2 — Last")

    def test_canonical_muted_state_preserves_volume_and_user_toggle_on_restart(self):
        pages = [
            {"id": "first", "type": "cover", "source": "gallery/pages/first.png"},
            {"id": "last", "type": "end_page", "source": "gallery/pages/last.png"},
        ]
        with self.canonical_view(pages, initial_muted=True, initial_volume=37):
            self.assertEqual(self.js("return document.getElementById('mute-button').textContent;"), "Unmute")
            self.assertTrue(self.js("return Array.from(document.querySelectorAll('audio')).every(audio=>audio.muted && audio.volume === .37);"))
            self.js("document.getElementById('mute-button').click(); return true;")
            self.assertEqual(self.js("return document.getElementById('mute-button').textContent;"), "Mute")
            self.assertTrue(self.js("return Array.from(document.querySelectorAll('audio')).every(audio=>!audio.muted && audio.volume === .37);"))
            loaded=[]
            loop=QtCore.QEventLoop()
            self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
            self.js("document.getElementById('restart-button').click(); return true;")
            QtCore.QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertEqual(loaded, [True])
            self.wait_for("return !!window.LetterSmithViewer;")
            self.assertEqual(self.js("return document.getElementById('mute-button').textContent;"), "Mute")
            self.assertTrue(self.js("return Array.from(document.querySelectorAll('audio')).every(audio=>!audio.muted && audio.volume === .37);"))

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
