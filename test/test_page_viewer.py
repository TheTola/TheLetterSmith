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

    def wait_for(self, source, timeout_ms=6000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            if self.js(source):
                return
            QtTest.QTest.qWait(50)
        self.fail(f"Viewer did not reach expected state: {source}")

    @contextmanager
    def canonical_view(self, pages, *, transition=None, initial_muted=None, initial_volume=0, reduced_motion=True, seed_sfx=False):
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
            if seed_sfx:
                from generate import _seed_sfx_into_build
                _seed_sfx_into_build(project_root=Path(__file__).resolve().parents[1],
                                     sounds_dst=root / "gallery/sounds", seed_sfx=True)
            shell = TEMPLATE_HTML
            for key, value in {
                "CSP_NONCE": "canonical-page-test", "TITLE": "Independent pages",
                "MUSIC_PRELOAD_HTML": "", "TITLE_BANNER_TEXT_RGB": "255,255,255",
                "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "",
                "INITIAL_VOLUME": str(initial_volume), "INITIAL_MUTED": str(bool(initial_muted)).lower(), "MUSIC_CROSSFADE_MS": "0",
                "MUSIC_PLAYLIST_JSON": "[]", "IMAGE_ANIMATIONS_JSON": "{}", "HAS_MESSAGE_JSON": "false",
            }.items():
                shell = shell.replace("{{" + key + "}}", value)
            if initial_muted is not None:
                shell = shell.replace('<body>', f'<body data-initial-muted="{str(initial_muted).lower()}" data-playback-id="{root.name}">')
            (root / "index.html").write_text(template_for_project_pages(pages, template_html=shell, transition=transition), encoding="utf-8")
            (root / "styles.css").write_text(TEMPLATE_CSS, encoding="utf-8")
            (root / "script.js").write_text(TEMPLATE_JS, encoding="utf-8")
            with self.output_view(root / "index.html", reduced_motion=reduced_motion):
                yield root

    @contextmanager
    def output_view(self, entry, *, native_start=False, reduced_motion=True):
        self.view = QWebEngineView()
        self.view.resize(1000, 700)
        script = QWebEngineScript()
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setSourceCode('''
            window.viewerErrors = [];
            window.cspErrors = [];
            window.viewerAudio = [];
            window.Audio = new Proxy(window.Audio, {construct(target,args){
                const audio=Reflect.construct(target,args); viewerAudio.push(audio); return audio;
            }});
            window.addEventListener('error', event => viewerErrors.push(event.message));
            window.addEventListener('securitypolicyviolation', event => cspErrors.push(event.violatedDirective));
            const nativeMatch = window.matchMedia.bind(window);
            window.matchMedia = query => query.includes('prefers-reduced-motion')
              ? {matches:REDUCED_MOTION, addEventListener(){}, removeEventListener(){}}
              : nativeMatch(query);
        '''.replace("REDUCED_MOTION", str(reduced_motion).lower()))
        self.view.page().scripts().insert(script)
        self.view.show()
        try:
            loaded = []
            loop = QtCore.QEventLoop()
            self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
            self.view.setUrl(QtCore.QUrl.fromLocalFile(str(entry)))
            QtCore.QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertEqual(loaded, [True])
            self.wait_for("return !!window.LetterSmithViewer;")
            if native_start:
                self.wait_for("return Number(getComputedStyle(document.getElementById('curtain-overlay')).opacity) > .99;")
                QtTest.QTest.mouseClick(self.view.focusProxy() or self.view, QtCore.Qt.LeftButton, pos=QtCore.QPoint(500, 350))
            else:
                self.js("document.getElementById('curtain-overlay').click(); return true;")
            # Trusted input plays the bundled intro sound before unlocking navigation.
            self.wait_for("return !document.getElementById('next').disabled;", 20000 if native_start else 6000)
            self.wait_for("return Number(getComputedStyle(document.getElementById('slideshow')).opacity) > .99;")
            yield
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
                self.assertTrue(self.js("return !document.getElementById('page-selector');"))
                self.assertTrue(self.js("return LetterSmithViewer.goToPage('cover');"))
                self.wait_for("return LetterSmithViewer.currentPageId() === 'cover';")
                self.assertFalse(self.js("return LetterSmithViewer.goToPage('deleted-page');"))

    def test_canonical_artwork_only_has_no_message_layer(self):
        pages = [
            {"id": "first", "type": "end_page", "name": "First", "source": "gallery/pages/first.png"},
            {"id": "last", "type": "illustration", "name": "Last", "source": "gallery/pages/last.png"},
        ]
        with self.canonical_view(pages):
            self.assertEqual(self.js("return document.querySelectorAll('.text-wall').length;"), 0)
            self.view.resize(390, 844)
            self.wait_for("""
                const artwork = document.querySelector('.slide.active > img').getBoundingClientRect();
                const prev = document.getElementById('prev').getBoundingClientRect();
                const next = document.getElementById('next').getBoundingClientRect();
                return innerWidth === 390 && Math.abs(artwork.left + artwork.width / 2 - 195) < 1
                    && Math.abs(artwork.top + artwork.height / 2 - 422) < 1
                    && artwork.left - prev.right > 0 && artwork.left - prev.right < 14
                    && next.left - artwork.right > 0 && next.left - artwork.right < 14;
            """)
            self.assertTrue(self.js("""
                const artwork = document.querySelector('.slide.active > img');
                const fittedWidth = artwork.getBoundingClientRect().width;
                const zoom = document.querySelector('[aria-label="Zoom in"]');
                zoom.click();
                const magnified = artwork.getBoundingClientRect().width > fittedWidth;
                document.querySelector('[aria-label="Reset zoom"]').click();
                const reset = artwork.getBoundingClientRect().width === fittedWidth;
                zoom.click();
                return magnified && reset && artwork.classList.contains('artwork-zoomed');
            """))
            self.js("document.getElementById('next').click(); return true;")
            self.wait_for("return LetterSmithViewer.currentPageId() === 'last';")
            self.assertTrue(self.js("return !document.querySelector('.artwork-zoomed');"))
            self.assertTrue(self.js("return document.getElementById('next').disabled;"))
            self.assertEqual(self.js("return document.getElementById('page-indicator').textContent;"), "Page 2 of 2 — Last")

    def test_classic_page_turn_opens_a_spread_and_can_reverse(self):
        pages = [
            {"id": "cover", "type": "cover", "name": "Cover", "source": "gallery/pages/cover.png", "color": "#804040"},
            {"id": "inside", "type": "illustration", "name": "Inside", "source": "gallery/pages/inside.png", "color": "#204060"},
        ]
        with self.canonical_view(pages, reduced_motion=False, seed_sfx=True):
            self.assertTrue(self.js("""
                document.getElementById('next').click();
                return !document.getElementById('turn').hidden
                  && document.getElementById('slideshow').getAttribute('aria-busy') === 'true'
                  && document.getElementById('next').disabled
                  && !document.getElementById('page-selector')
                  && LetterSmithViewer.currentPageId() === 'cover';
            """))
            self.assertRegex(self.js("return viewerAudio.at(-1).src;"), r"/transitions/classic/flip(?:[1-9]|10)\.mp3$")
            self.wait_for("""
                const canvas = document.getElementById('turn');
                if (canvas.hidden) return false;
                const stage = canvas.getBoundingClientRect();
                const cover = document.querySelector('.slide.active > img').getBoundingClientRect();
                const x = (cover.left - stage.left - cover.width * .45) * canvas.width / stage.width;
                const y = (cover.top - stage.top + cover.height / 2) * canvas.height / stage.height;
                const pixel = canvas.getContext('2d').getImageData(x, y, 1, 1).data;
                return pixel[0] > 20 && pixel[3] > 230;
            """)
            self.view.resize(390, 844)
            self.wait_for("return LetterSmithViewer.currentPageId() === 'inside' && !document.getElementById('prev').disabled;")
            self.assertTrue(self.js("""
                const canvas = document.getElementById('turn');
                return canvas.hidden && canvas.width === 0
                  && !document.querySelector('.slide.ghost, .slide.peek')
                  && document.getElementById('slideshow').getAttribute('aria-busy') === 'false';
            """))
            self.assertTrue(self.js("""
                window.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowLeft'}));
                return !document.getElementById('turn').hidden && LetterSmithViewer.currentPageId() === 'inside';
            """))
            self.wait_for("return LetterSmithViewer.currentPageId() === 'cover' && !document.getElementById('next').disabled;")
            self.assertTrue(self.js("return document.getElementById('turn').hidden;"))

    def test_letter_transition_plays_on_every_page_boundary(self):
        pages = [
            {"id": name, "type": "illustration", "source": f"gallery/pages/{name}.png", "color": color}
            for name, color in (("first", "navy"), ("second", "orange"), ("third", "white"))
        ]
        def assert_cue(cue):
            self.assertTrue(self.js(f"""
                const audio = viewerAudio.at(-1);
                return audio.src.endsWith('/gallery/sounds/transitions/{cue}')
                    && audio.muted && Math.abs(audio.volume - .37) < 1e-6;
            """))
            self.wait_for("return viewerAudio.at(-1).readyState >= 2 && !viewerAudio.at(-1).error;")

        for mode, forward, reverse in (
            ("shuffle_deal", "shuffle_deal/deal.mp3", "shuffle_deal/return.mp3"),
            ("pixelate", "pixelate/dissolve-reveal.mp3", "pixelate/dissolve-reveal.mp3"),
            ("envelope", "envelope/open-letter.mp3", "envelope/return-letter.mp3"),
            ("curtain", "curtain/close-open.mp3", "curtain/close-open.mp3"),
        ):
            with self.subTest(mode=mode), self.canonical_view(pages, transition=mode, initial_muted=True,
                                                             initial_volume=37, reduced_motion=False, seed_sfx=True):
                self.assertTrue(self.js("return !document.querySelector('.slide[data-transition]');"))
                for destination in ("second", "third"):
                    self.assertEqual(self.js("""
                        document.getElementById('next').click();
                        return document.getElementById('letter-preview').dataset.turnEffect;
                    """), mode)
                    if mode == "pixelate":
                        self.wait_for("""
                            const canvas = document.getElementById('turn');
                            if (canvas.hidden) return false;
                            const paint = canvas.getContext('2d');
                            const hiddenControls = ['restart-button', 'fullscreen-button', 'page-navigation']
                                .every(id => getComputedStyle(document.getElementById(id)).visibility === 'hidden');
                            return hiddenControls && [paint.getImageData(10, 10, 1, 1),
                                paint.getImageData(canvas.width / 2, canvas.height / 2, 1, 1)]
                                .every(({data}) => data[0] === 0 && data[1] === 0 && data[2] === 0 && data[3] === 255);
                        """)
                    assert_cue(forward)
                    self.wait_for(f"return LetterSmithViewer.currentPageId() === '{destination}' && !document.getElementById('prev').disabled;")
                    self.assertTrue(self.js("return document.getElementById('turn').hidden;"))
                self.assertEqual(self.js("""
                    document.getElementById('prev').click();
                    return document.getElementById('letter-preview').dataset.turnEffect;
                """), mode)
                assert_cue(reverse)
                self.wait_for("return LetterSmithViewer.currentPageId() === 'second' && !document.getElementById('next').disabled;")

    def test_real_layouts_zip_and_single_html_work_without_source_project(self):
        import base64
        import wave
        import zipfile
        from test_forge_render import ForgeRenderTests
        from test_project_model import canvas_layout
        from forge_render import build_render_to, capture_render, copy_display_output, export_render

        fixture = ForgeRenderTests('test_real_five_pages_order_messages_reorder_and_repository_reload')
        fixture.setUp()
        with tempfile.TemporaryDirectory(prefix="detached-display-") as directory:
            detached = Path(directory)
            try:
                fixture.seed_app_sounds()
                pages = fixture.project.pages
                a, b = pages[1]["id"], pages[3]["id"]
                for page, color in zip(pages, ('#204050', '#804040', '#405040', '#204060', '#302040')):
                    Image.new('RGB', (400, 600), color).save(fixture.root / page['artwork']['asset'])
                wide_artwork = Image.new('RGB', (1200, 600), '#204060')
                wide_artwork.paste('#905010', (0, 0, 600, 600))
                wide_artwork.save(fixture.root / pages[3]['artwork']['asset'])
                frames = [Image.new('RGB', (400, 600), color) for color in ('#306080', '#803060')]
                frames[0].save(fixture.root / 'cover.gif', save_all=True, append_images=frames[1:], duration=[100, 160], loop=0)
                fixture.project.update_artwork(pages[0]['id'], {'asset': 'cover.gif'})
                fixture.project.set_letter_transition('pixelate')
                fixture.project.update_message(a, {**fixture.project.get_message(a), 'layout': canvas_layout(),
                    'html': '<p style="font-size:24px"><b>Message A — red artwork</b></p>' + '<p>Readable overflowing message paragraph.</p>' * 18})
                fixture.project.update_message(b, {**fixture.project.get_message(b),
                    'layout': {**canvas_layout(), 'box': {'x': 80, 'y': 330, 'width': 280, 'height': 190}, 'text_align': 'right'},
                    'html': '<p style="font-size:30px"><i>Message B — blue artwork</i></p>'})
                fixture.project.update_artwork(b, {'placement': {'version': 1, 'fit': 'cover', 'position': {'x': .8, 'y': .3}}})
                tracks = []
                for index in range(2):
                    audio_path = fixture.root / f'pcm-{index}.wav'
                    with wave.open(str(audio_path), 'wb') as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(22050)
                        audio.writeframes(b'\x00\x00' * 22050 * 6)
                    tracks.append({'track_id': str(index), 'asset': audio_path.name})
                fixture.project.update_project_data('sound', {'tracks': tracks, 'mode': 'playlist', 'playlist': ['0', '1'], 'volume': 23, 'muted': True, 'crossfade_ms': 300})
                built = build_render_to(capture_render(fixture.session), fixture.root / 'display')
                display = copy_display_output(built, detached / 'display')
                archive_path = export_render(fixture.session, detached / 'letter.zip')
                standalone = export_render(fixture.session, detached / 'letter.html', output_format='html')
                cue_path = 'gallery/sounds/transitions/pixelate/dissolve-reveal.mp3'
                cue_uri = 'data:audio/mpeg;base64,' + base64.b64encode((built / cue_path).read_bytes()).decode('ascii')
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(detached / 'unpacked')
            finally:
                fixture.tearDown()

            self.assertFalse(fixture.root.exists())
            observations = []
            for mode, entry in (('display', display / 'index.html'), ('zip', detached / 'unpacked/letter/index.html'), ('html', standalone)):
                with self.subTest(mode=mode), self.output_view(entry, native_start=True):
                    sources = set()
                    for _ in range(8):
                        sources.add(self.js("return document.querySelector('.slide.active > img').src;"))
                        QtTest.QTest.qWait(70)
                    self.assertGreater(len(sources), 1)
                    self.wait_for("return Array.from(document.querySelectorAll('audio')).some(audio=>!audio.paused && audio.currentTime > .1);")
                    before = self.js("const a=Array.from(document.querySelectorAll('audio')).find(a=>!a.paused); return {src:a.currentSrc,time:a.currentTime};")
                    self.js('LetterSmithViewer.goToPage(' + json.dumps(a) + '); return true;')
                    after = self.js("const a=Array.from(document.querySelectorAll('audio')).find(a=>!a.paused); return {src:a.currentSrc,time:a.currentTime};")
                    self.assertEqual(before['src'], after['src'])
                    self.assertGreaterEqual(after['time'], before['time'])
                    if mode == 'html':
                        self.assertTrue(after['src'].startswith('data:audio/'))
                    self.wait_for("return !document.getElementById('next').disabled;")
                    self.js("document.getElementById('next').click(); return true;")
                    self.assertEqual(self.js("return document.getElementById('letter-preview').dataset.transition;"), 'pixelate')
                    self.assertTrue(self.js("return document.getElementById('turn').hidden;"))
                    self.wait_for("return viewerAudio.length > 1 && viewerAudio[viewerAudio.length-1].readyState >= 2;")
                    self.assertGreaterEqual(self.js("return viewerAudio.at(-1).currentTime;"), .85)
                    self.assertTrue(self.js("return viewerAudio.every(audio=>!audio.error);"))
                    if mode == 'html':
                        self.assertTrue(self.js("return viewerAudio.every(audio=>audio.src.startsWith('data:audio/'));"))
                        self.assertEqual(self.js("return viewerAudio.at(-1).src;"), cue_uri)
                    else:
                        self.assertTrue(self.js(f"return viewerAudio.at(-1).src.endsWith('/{cue_path}');"))
                    for page_id, expected, x, y, font in ((a, 'Message A', 40, 130, '24px'), (b, 'Message B', 80, 330, '30px')):
                        self.wait_for("return !document.getElementById('prev').disabled;")
                        self.js('LetterSmithViewer.goToPage(' + json.dumps(page_id) + '); return true;')
                        self.wait_for("return !!document.querySelector('.text-wall.is-open');")
                        self.wait_for("return document.getElementById('title-banner').getAttribute('aria-hidden') === 'true';")
                        self.wait_for("return Number(getComputedStyle(document.querySelector('.text-wall.is-open')).opacity) > .99;")
                        for width, height, orientation in ((1000, 700, 'landscape'), (390, 844, 'portrait')):
                            self.view.resize(width, height)
                            self.wait_for(f"return innerWidth === {width} && innerHeight === {height};")
                            QtTest.QTest.qWait(500)
                            measure = self.js('''
                                const wall=document.querySelector('.text-wall.is-open');
                                const r=wall.getBoundingClientRect(), image=document.querySelector('.slide.active > img'), a=image.getBoundingClientRect();
                                return {x:(r.left-a.left)/a.width,y:(r.top-a.top)/a.height,text:wall.querySelector('.text-wall-content').textContent,
                                  font:getComputedStyle(wall.querySelector('.text-wall-content p')).fontSize,fit:getComputedStyle(image).objectFit,
                                  position:getComputedStyle(image).objectPosition,naturalWidth:image.naturalWidth,
                                  issues:LetterSmithViewer.layoutIssues()};
                            ''')
                            self.assertIn(expected, measure['text'])
                            self.assertEqual(measure['font'], font)
                            self.assertAlmostEqual(measure['x'], x / 400, delta=.003)
                            self.assertAlmostEqual(measure['y'], y / 600, delta=.003)
                            self.assertEqual(measure['fit'], 'cover' if page_id == b else 'contain')
                            if page_id == b:
                                self.assertEqual(measure['position'], '80% 30%')
                                self.assertEqual(measure['naturalWidth'], 1200)
                            self.assertTrue(any(issue['page_id'] == a and issue['code'] == 'message_overflow' for issue in measure['issues']))
                            profile = os.environ.get('LETTER_SMITH_PROFILE_ROOT')
                            if profile and mode != 'zip':
                                screenshot = Path(profile) / f'layout-{mode}-{expected[-1].lower()}-{orientation}.png'
                                self.assertTrue(self.view.grab().save(str(screenshot)))
                            observations.append({'mode': mode, 'page_id': page_id, 'orientation': orientation, 'x': measure['x'], 'y': measure['y'], 'font': measure['font']})
                        if page_id == a:
                            self.assertTrue(self.js("const viewport=document.querySelector('.text-wall.is-open .text-wall-viewport'); viewport.scrollTop=viewport.scrollHeight; return viewport.scrollTop > 0;"))
                    self.assertEqual(self.js('return cspErrors;'), [])
            profile = os.environ.get('LETTER_SMITH_PROFILE_ROOT')
            if profile:
                (Path(profile) / 'layout-export-observations.json').write_text(json.dumps(observations, indent=2), encoding='utf-8')

    def test_canonical_muted_state_preserves_volume_and_user_toggle_on_restart(self):
        pages = [
            {"id": "first", "type": "cover", "source": "gallery/pages/first.png"},
            {"id": "last", "type": "end_page", "source": "gallery/pages/last.png"},
        ]
        with self.canonical_view(pages, initial_muted=True, initial_volume=37):
            self.assertTrue(self.js("""
                const sound = document.getElementById('volume-icon');
                const settings = document.getElementById(sound.getAttribute('aria-controls'));
                const actions = document.getElementById('viewer-actions');
                return actions.contains(sound) && actions.contains(document.getElementById('fullscreen-button'))
                    && settings.hidden && settings.contains(document.getElementById('mute-button'))
                    && settings.contains(document.getElementById('volume-slider'));
            """))
            self.assertEqual(self.js("return document.getElementById('mute-button').textContent;"), "Unmute")
            self.assertTrue(self.js("return Array.from(document.querySelectorAll('audio')).every(audio=>audio.muted && audio.volume === .37);"))
            self.js("document.getElementById('volume-icon').click(); return true;")
            self.assertTrue(self.js("return !document.getElementById('desktop-sound-settings').hidden;"))
            self.js("document.getElementById('mute-button').click(); return true;")
            self.assertEqual(self.js("return document.getElementById('mute-button').textContent;"), "Mute")
            self.assertTrue(self.js("return Array.from(document.querySelectorAll('audio')).every(audio=>!audio.muted && audio.volume === .37);"))
            self.assertTrue(self.js("""
                const volume = document.getElementById('volume-slider');
                volume.value = '62';
                volume.dispatchEvent(new Event('input'));
                return Array.from(document.querySelectorAll('audio')).every(audio=>audio.volume === .62);
            """))
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
            # Chromium's animation frames need not finish within a fixed wall
            # time. Observe the real handoff before checking the settled volume.
            self.wait_for("return audit.active.has(document.getElementById('bg-music-standby')) && !audit.active.has(document.getElementById('bg-music'));")
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
