from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

import Template as viewer_template


ROOT = Path(__file__).resolve().parents[1]


class ViewerStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def js(self, source: str):
        result = []
        loop = QtCore.QEventLoop()
        self.view.page().runJavaScript(
            f"JSON.stringify((() => {{ {source} }})())",
            lambda value: (result.append(value), loop.quit()),
        )
        QtCore.QTimer.singleShot(4000, loop.quit)
        loop.exec()
        self.assertTrue(result, "Browser script did not return")
        return json.loads(result[0])

    def load(self, index: Path) -> None:
        loaded = []
        loop = QtCore.QEventLoop()
        callback = lambda ok: (loaded.append(ok), loop.quit())
        self.view.loadFinished.connect(callback)
        self.view.setUrl(QtCore.QUrl.fromLocalFile(str(index)))
        QtCore.QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.view.loadFinished.disconnect(callback)
        self.assertEqual(loaded, [True])
        self.view.setFocus()
        QtTest.QTest.qWait(750)
        self.js("""
            const probe = document.createElement('button');
            probe.tabIndex = -1;
            probe.style.cssText = 'position:absolute;inset:0;z-index:5000;background:transparent';
            probe.addEventListener('click', () => window.audit.underneath++);
            document.getElementById('letter-preview').append(probe);
            return true;
        """)

    def test_opening_overlay_mouse_touch_keyboard_and_media(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lettersmith-start-") as directory:
            root = Path(directory)
            # Use real bundled curtains, controls and audio, not replacement assets.
            shutil.copytree(ROOT / "resources/stock/letters/Stock Letter 1/gallery", root / "gallery")
            html = viewer_template.TEMPLATE_HTML
            values = {
                "CSP_NONCE": "viewer-start-test", "TITLE": "Start interaction",
                "MUSIC_PRELOAD_HTML": "", "TITLE_BANNER_TEXT_RGB": "255,255,255",
                "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "<p>Test letter</p>",
                "INITIAL_VOLUME": "40", "MUSIC_CROSSFADE_MS": "0",
                "MUSIC_PLAYLIST_JSON": '["gallery/sounds/music.mp3"]',
                "IMAGE_ANIMATIONS_JSON": "{}", "HAS_MESSAGE_JSON": "true",
            }
            for key, value in values.items():
                html = html.replace("{{" + key + "}}", value)
            index = root / "index.html"
            index.write_text(html, encoding="utf-8")
            (root / "styles.css").write_text(viewer_template.TEMPLATE_CSS, encoding="utf-8")
            (root / "script.js").write_text(viewer_template.TEMPLATE_JS, encoding="utf-8")
            self.view = QWebEngineView()
            self.view.resize(800, 600)
            self.view.page().setAudioMuted(True)
            audit = QWebEngineScript()
            audit.setInjectionPoint(QWebEngineScript.DocumentCreation)
            audit.setWorldId(QWebEngineScript.MainWorld)
            audit.setSourceCode("""
                window.audit = {plays:[], playing:[], rejected:[], clicks:[], pointers:[], bubbled:0, underneath:0};
                const nativePlay = HTMLMediaElement.prototype.play;
                HTMLMediaElement.prototype.play = function() {
                    const source = this.src;
                    audit.plays.push(source);
                    this.addEventListener('playing', () => audit.playing.push(source), {once:true});
                    const result = nativePlay.call(this);
                    result.catch(error => audit.rejected.push(error.name));
                    return result;
                };
                document.addEventListener('click', event => audit.clicks.push({
                    target:event.target.id, trusted:event.isTrusted,
                    active:navigator.userActivation.isActive
                }), true);
                document.addEventListener('click', () => audit.bubbled++);
                for (const type of ['pointerdown', 'pointerup', 'touchstart', 'touchend']) {
                    document.addEventListener(type, event => audit.pointers.push(type), true);
                }
            """)
            self.view.page().scripts().insert(audit)
            self.view.show()
            touch = QtTest.QTest.createTouchDevice(QtGui.QInputDevice.DeviceType.TouchScreen)
            try:
                for action in ("empty", "text", "between", "fullscreen", "drag", "touch", "keyboard"):
                    with self.subTest(action=action):
                        self.load(index)
                        properties = self.js("""
                            const text = document.getElementById('begin-button');
                            const box = text.getBoundingClientRect();
                            const style = getComputedStyle(text);
                            const range = document.createRange();
                            range.setStart(text.firstChild, 3); range.setEnd(text.firstChild, 4);
                            const gap = range.getBoundingClientRect();
                            text.focus();
                            return {tag:text.tagName, editable:text.isContentEditable,
                                tabIndex:text.tabIndex, focused:document.activeElement === text,
                                cursor:style.cursor, pointer:style.pointerEvents,
                                selection:style.userSelect, caret:style.caretColor,
                                x:box.x + box.width/2, y:box.y + box.height/2,
                                gapX:gap.x + gap.width/2};
                        """)
                        point = QtCore.QPoint(round(properties["x"]), round(properties["y"]))
                        if action == "empty":
                            point = QtCore.QPoint(30, 30)
                        elif action == "fullscreen":
                            point = QtCore.QPoint(770, 30)
                        elif action == "between":
                            point.setX(round(properties["gapX"]))
                        target = self.view.focusProxy() or self.view
                        if action == "keyboard":
                            for _ in range(4):
                                QtTest.QTest.keyClick(target, QtCore.Qt.Key_Tab)
                                focused = self.js("return document.activeElement.id;")
                                self.assertNotEqual(focused, "begin-button")
                                if focused == "curtain-overlay":
                                    break
                            self.assertEqual(focused, "curtain-overlay")
                            QtTest.QTest.keyClick(target, QtCore.Qt.Key_Return)
                        elif action == "touch":
                            touch_window = self.view.windowHandle()
                            QtTest.QTest.touchEvent(touch_window, touch).press(0, point, touch_window).commit()
                            QtTest.QTest.qWait(50)
                            QtTest.QTest.touchEvent(touch_window, touch).release(0, point, touch_window).commit()
                        else:
                            QtTest.QTest.mouseMove(target, point)
                            if action == "drag":
                                QtTest.QTest.mousePress(target, QtCore.Qt.LeftButton, pos=point)
                                point += QtCore.QPoint(90, 0)
                                QtTest.QTest.mouseMove(target, point, delay=50)
                                self.assertEqual(self.js("return String(getSelection());"), "")
                                self.assertEqual(self.js("return audit.plays;"), [])
                                QtTest.QTest.mouseRelease(target, QtCore.Qt.LeftButton, pos=point)
                            else:
                                QtTest.QTest.mouseClick(target, QtCore.Qt.LeftButton, pos=point)
                        QtTest.QTest.qWait(350)
                        self.assertTrue(self.js("return document.getElementById('curtain-left').style.animation.includes('curtainLeftOut');"), self.js("return audit;"))
                        self.assertEqual(properties["tag"], "SPAN")
                        self.assertFalse(properties["editable"])
                        self.assertEqual(properties["tabIndex"], -1)
                        self.assertFalse(properties["focused"])
                        self.assertEqual(properties["pointer"], "none")
                        self.assertEqual(properties["cursor"], "default")
                        self.assertEqual(properties["selection"], "none")
                        self.assertEqual(properties["caret"], "rgba(0, 0, 0, 0)")
                        for _ in range(3):
                            QtTest.QTest.mouseClick(target, QtCore.Qt.LeftButton, pos=QtCore.QPoint(
                                round(properties["x"]), round(properties["y"]),
                            ))
                        deadline = QtCore.QElapsedTimer()
                        deadline.start()
                        while deadline.elapsed() < 8000:
                            state = self.js("return {removed:!document.getElementById('curtain-overlay'), ...audit};")
                            if state["removed"] and any(url.endswith("/music.mp3") for url in state["playing"]):
                                break
                            QtTest.QTest.qWait(100)
                        self.assertTrue(state["removed"])
                        for audio in ("glissando.mp3", "music.mp3"):
                            self.assertEqual(sum(url.endswith('/' + audio) for url in state["plays"]), 1)
                            self.assertTrue(any(url.endswith('/' + audio) for url in state["playing"]), state)
                        self.assertEqual(state["rejected"], [])
                        self.assertEqual(state["underneath"], 0, state)
                        self.assertEqual(state["bubbled"], 0, state)
                        self.assertEqual(self.js("return document.querySelector('.slide.active').dataset.index;"), "0")
                        self.assertFalse(self.js("return document.getElementById('fullscreen-button').inert;"))
                        self.assertFalse(self.js("return document.getElementById('next').disabled;"))
                        if action != "keyboard":
                            self.assertTrue(state["clicks"][0]["trusted"])
                            self.assertTrue(state["clicks"][0]["active"])
                            self.assertEqual(state["clicks"][0]["target"], "curtain-overlay")
                        self.assertEqual(self.js("return String(getSelection());"), "")
            finally:
                self.view.close()
                self.view.deleteLater()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
