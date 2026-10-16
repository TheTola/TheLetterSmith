"""Exercise variable page layouts in the actual exported browser viewer."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave

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

    @contextmanager
    def playback_viewer(self, *, muted=False, crossfade_ms=0):
        """Run the real viewer JS; record media requests without audible output."""
        with tempfile.TemporaryDirectory(prefix="ls-viewer-audio-") as directory:
            root = Path(directory)
            pages = (DEFAULT_PAGES[0], DEFAULT_PAGES[-1])
            gallery = root / "gallery/pages"
            gallery.mkdir(parents=True)
            for page in pages:
                Image.new("RGB", (80, 120), "navy").save(gallery / page.filename)
            sounds = root / "gallery/sounds"
            sounds.mkdir()
            for name, frames in (("first.wav", 24000), ("second.wav", 24000), ("glissando.mp3", 800)):
                with wave.open(str(sounds / name), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(8000)
                    audio.writeframes(bytes(frames * 2))
            html = template_for_pages(pages)
            values = {
                "CSP_NONCE": "playback-test", "TITLE": "Playback lifecycle",
                "MUSIC_PRELOAD_HTML": "", "TITLE_BANNER_TEXT_RGB": "255,255,255",
                "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "",
                "INITIAL_VOLUME": "37", "INITIAL_MUTED": str(muted).lower(),
                "MUSIC_CROSSFADE_MS": str(crossfade_ms),
                "MUSIC_PLAYLIST_JSON": '["gallery/sounds/first.wav","gallery/sounds/second.wav"]',
                "IMAGE_ANIMATIONS_JSON": "{}", "HAS_MESSAGE_JSON": "false",
            }
            for key, value in values.items():
                html = html.replace("{{" + key + "}}", value)
            index = root / "index.html"
            index.write_text(html, encoding="utf-8")
            (root / "styles.css").write_text(TEMPLATE_CSS, encoding="utf-8")
            (root / "script.js").write_text(TEMPLATE_JS, encoding="utf-8")
            self.view = QWebEngineView()
            self.view.resize(800, 600)
            self.view.page().setAudioMuted(True)
            script = QWebEngineScript()
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setSourceCode('''
                window.audit = {plays:[], active:new Set(), media:new Set(), errors:[]};
                window.addEventListener('error', event => audit.errors.push(event.message));
                sessionStorage.setItem('lettersmith.viewerMuted', 'STALE_MUTE');
                const nativeMatch = window.matchMedia.bind(window);
                window.matchMedia = query => query.includes('prefers-reduced-motion')
                    ? {matches:true, addEventListener(){}, removeEventListener(){}}
                    : nativeMatch(query);
                HTMLMediaElement.prototype.play = function() {
                    audit.plays.push({id:this.id, src:this.src, muted:this.muted});
                    audit.media.add(this); audit.active.add(this);
                    return Promise.resolve();
                };
                const nativePause = HTMLMediaElement.prototype.pause;
                HTMLMediaElement.prototype.pause = function() {
                    audit.active.delete(this);
                    return nativePause.call(this);
                };
                const nativeLoad = HTMLMediaElement.prototype.load;
                HTMLMediaElement.prototype.load = function() {
                    audit.media.add(this);
                    return nativeLoad.call(this);
                };
            '''.replace("STALE_MUTE", str(not muted).lower()))
            self.view.page().scripts().insert(script)
            self.view.show()
            try:
                loaded = []
                loop = QtCore.QEventLoop()
                callback = lambda ok: (loaded.append(ok), loop.quit())
                self.view.loadFinished.connect(callback)
                self.view.setUrl(QtCore.QUrl.fromLocalFile(str(index)))
                QtCore.QTimer.singleShot(5000, loop.quit)
                loop.exec()
                self.view.loadFinished.disconnect(callback)
                self.assertEqual(loaded, [True])
                self.wait_for("return !!window.letterSmithPlayback;")
                yield index
                self.assertEqual(self.js("return audit.errors;"), [])
            finally:
                self.view.close()
                self.view.deleteLater()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def start_playback(self):
        self.js("document.getElementById('curtain-overlay').click(); return true;")
        self.wait_for("return audit.plays.some(item => item.id === 'bg-music') && !document.getElementById('next').disabled;")

    def test_authored_mute_and_volume_restore_without_restarting_on_page_navigation(self):
        for muted in (False, True):
            with self.subTest(muted=muted), self.playback_viewer(muted=muted) as index:
                self.assertEqual(self.js("return document.getElementById('bg-music').muted;"), muted)
                self.assertAlmostEqual(self.js("return document.getElementById('bg-music').volume;"), 0.37)
                self.start_playback()
                self.wait_for("return document.getElementById('bg-music').readyState > 0;")
                self.js("document.getElementById('mute-button').click(); document.getElementById('bg-music').currentTime = 0.2; return true;")
                before = self.js("const music=document.getElementById('bg-music'); return {src:music.src, time:music.currentTime, plays:audit.plays.filter(item => item.id.startsWith('bg-music')).length};")
                self.js("document.getElementById('next').click(); return true;")
                self.wait_for("return document.querySelector('.slide.active').dataset.index === '1';")
                self.js("document.getElementById('prev').click(); return true;")
                self.wait_for("return document.querySelector('.slide.active').dataset.index === '0';")
                after = self.js("const music=document.getElementById('bg-music'); return {src:music.src, time:music.currentTime, plays:audit.plays.filter(item => item.id.startsWith('bg-music')).length};")
                self.assertEqual(after, before)
                self.assertEqual(self.js("return document.getElementById('bg-music').muted;"), not muted)
                self.js("document.getElementById('restart-button').click(); return true;")
                self.wait_for("return !!document.getElementById('curtain-overlay') && !!window.letterSmithPlayback;")
                self.assertEqual(self.js("return document.getElementById('bg-music').muted;"), muted)

    def test_hidden_shell_cancels_delayed_playlist_restart_and_late_load(self):
        from Nexus import Nexus
        with self.playback_viewer():
            self.start_playback()
            self.js("document.getElementById('bg-music').dispatchEvent(new Event('ended')); return true;")
            shell = SimpleNamespace(html_preview=self.view)
            Nexus._set_forge_preview_visible(shell, False)
            self.wait_for("return audit.active.size === 0;")
            count = self.js("return audit.plays.length;")
            QtTest.QTest.qWait(1350)
            self.assertEqual(self.js("return audit.plays.length;"), count)
            shell.tabbar = SimpleNamespace(currentIndex=lambda: 2)
            shell._shutdown_in_progress = False
            shell._shutdown_complete = False
            shell._update_forge_preview_zoom = lambda: None
            shell._set_forge_preview_visible = lambda visible: Nexus._set_forge_preview_visible(shell, visible)
            Nexus._forge_preview_loaded(shell, True)
            self.js("document.getElementById('bg-music').dispatchEvent(new Event('ended')); return true;")
            QtTest.QTest.qWait(1350)
            self.assertEqual(self.js("return audit.plays.length;"), count)
            Nexus._set_forge_preview_visible(shell, True)
            self.js("document.getElementById('bg-music').dispatchEvent(new Event('ended')); return true;")
            QtTest.QTest.qWait(1350)
            self.assertEqual(self.js("return audit.plays.length;"), count)

    def test_hide_cancels_crossfade_and_shutdown_rejects_future_playback(self):
        with self.playback_viewer(crossfade_ms=500):
            self.start_playback()
            self.js("document.getElementById('bg-music').dispatchEvent(new Event('ended')); return true;")
            self.wait_for("return audit.plays.some(item => item.id === 'bg-music-standby');")
            self.js("window.letterSmithPlayback.setVisible(false); return true;")
            self.assertEqual(self.js("return audit.active.size;"), 0)
            volumes = self.js("return Array.from(document.querySelectorAll('audio')).map(audio => audio.volume);")
            count = self.js("return audit.plays.length;")
            QtTest.QTest.qWait(650)
            self.assertEqual(self.js("return audit.plays.length;"), count)
            self.assertEqual(self.js("return Array.from(document.querySelectorAll('audio')).map(audio => audio.volume);"), volumes)
            self.js("window.letterSmithPlayback.setVisible(true); document.getElementById('bg-music').dispatchEvent(new Event('ended')); window.letterSmithPlayback.shutdown(); return true;")
            stopped = self.js("return audit.plays.length;")
            self.js("window.letterSmithPlayback.setVisible(true); document.getElementById('bg-music').dispatchEvent(new Event('ended')); return true;")
            QtTest.QTest.qWait(650)
            self.assertEqual(self.js("return audit.plays.length;"), stopped)
            self.assertEqual(self.js("return audit.active.size;"), 0)

    def test_hide_during_intro_rejects_old_gliss_callbacks_after_show(self):
        with self.playback_viewer():
            self.js("document.getElementById('curtain-overlay').click(); window.letterSmithPlayback.setVisible(false); return true;")
            count = self.js("return audit.plays.length;")
            QtTest.QTest.qWait(500)
            self.js("window.letterSmithPlayback.setVisible(true); audit.media.forEach(audio => { for (const type of ['loadedmetadata','ended','error']) audio.dispatchEvent(new Event(type)); }); return true;")
            QtTest.QTest.qWait(500)
            self.assertEqual(self.js("return audit.plays.length;"), count)
            self.assertEqual(self.js("return audit.active.size;"), 0)

    def test_crossfade_keeps_current_viewer_volume_and_mute_changes(self):
        with self.playback_viewer(muted=True, crossfade_ms=500):
            self.start_playback()
            self.js("document.getElementById('bg-music').dispatchEvent(new Event('ended')); document.getElementById('mute-button').click(); const slider=document.getElementById('volume-slider'); slider.value='19'; slider.dispatchEvent(new Event('input')); return true;")
            QtTest.QTest.qWait(650)
            state = self.js("return Array.from(document.querySelectorAll('audio')).map(audio => ({volume:audio.volume, muted:audio.muted}));")
            self.assertTrue(state)
            for audio in state:
                self.assertAlmostEqual(audio["volume"], 0.19)
                self.assertFalse(audio["muted"])

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
                    "INITIAL_VOLUME": "0", "INITIAL_MUTED": "false", "MUSIC_CROSSFADE_MS": "0",
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
