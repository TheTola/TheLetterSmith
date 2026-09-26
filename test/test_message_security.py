from __future__ import annotations

import base64
import os
import re
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtTest, QtWidgets

import generate
import message_import
import message_html
import transactional_io
from Message_tab import MessageTab
from config import CONTROL_FILES, REQUIRED_SLIDES
from message_history import (
    restore_revision, revision_directory, write_message_with_revision,
    list_revisions, message_change_count, snapshot_current,
)
from message_html import sanitize_message_html
from settings_store import SettingsStore


class MessageHTMLSecurityTests(unittest.TestCase):
    def test_manual_saves_accumulate_significant_revisions_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.html"
            original = '<p>' + ' '.join(f'word{i}' for i in range(30)) + '</p>'
            self.assertTrue(write_message_with_revision(path, original, reason='manual-save'))
            for _ in range(2):
                self.assertFalse(write_message_with_revision(path, original, reason='manual-save'))
            minor = original.replace('word0 ', 'WORD0 ')
            self.assertTrue(write_message_with_revision(path, minor, reason='manual-save'))
            self.assertIn('WORD0', path.read_text())
            write_message_with_revision(path, original, reason='manual-save')
            self.assertEqual(list_revisions(path), [])
            changed = original
            for index in range(16):
                changed = changed.replace(f'word{index} ', f'changed{index} ')
                write_message_with_revision(path, changed, reason='manual-save')
                self.assertEqual(len(list_revisions(path)), int(index == 15))
            revisions = list_revisions(path)
            self.assertEqual(len(revisions), 1)
            self.assertIn('word0 ', revisions[0].path.read_text())
            # Restoring and then returning to the same checkpoint cannot create
            # another copy of the original content.
            restore_revision(path, revisions[0].path)
            write_message_with_revision(path, changed, reason='autosave')
            self.assertEqual(len(list_revisions(path)), 2)
            self.assertIsNone(snapshot_current(path))

    def test_revision_comparison_counts_net_words_and_formatting(self) -> None:
        self.assertEqual(message_change_count('<p>Kassi</p>', '<p>KASSI</p>'), 1)
        self.assertEqual(message_change_count('<p>Kassi</p>', '<p>Kassi</p>'), 0)
        self.assertEqual(message_change_count('<b>word</b>', '<span style="font-weight:700">word</span>'), 0)
        text = ' '.join(f'word{i}' for i in range(16))
        self.assertEqual(message_change_count(f'<p>{text}</p>', f'<p style="font-family:Arial">{text}</p>'), 16)

    def test_initial_style_metadata_and_hidden_css_do_not_create_revisions(self) -> None:
        from named_text_styles import DOCUMENT_STYLE_SCHEMA_VERSION, default_style_set

        before = '<p>Kassi</p>'
        after = message_html.embed_lettersmith_style_state(
            '<html><head><style>p, li { white-space: pre-wrap; }</style></head>'
            '<body><p>KASSI</p></body></html>',
            {"schema_version": DOCUMENT_STYLE_SCHEMA_VERSION,
             "definitions": default_style_set().to_dict(), "blocks": []},
        )
        self.assertEqual(message_change_count(before, after), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.html"
            write_message_with_revision(path, before, reason="manual-save")
            self.assertTrue(write_message_with_revision(path, after, reason="manual-save"))
            self.assertEqual(list_revisions(path), [])

    def test_style_state_comment_survives_sanitization_and_revisions(self) -> None:
        state = {
            "schema_version": 1,
            "definitions": {"normal_text": {"font_family": "Arial"}},
            "blocks": [{"style": "normal_text", "overrides": []}],
        }
        html = message_html.embed_lettersmith_style_state(
            "<!-- lettersmith-message:v2 --><p>Hello</p>", state
        )
        sanitized = sanitize_message_html(html)
        self.assertEqual(message_html.extract_lettersmith_style_state(sanitized), state)
        self.assertEqual(sanitize_message_html(sanitized), sanitized)
        self.assertEqual(sanitized.count("lettersmith-style-state:v1:"), 1)
        self.assertEqual(
            message_html.embed_lettersmith_style_state(sanitized, state).count(
                "lettersmith-style-state:v1:"
            ),
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.html"
            write_message_with_revision(path, sanitized, reason="first")
            write_message_with_revision(path, "<p>Second</p>", reason="second")
            revision = next(iter(revision_directory(path).glob("*.html")))
            restored = restore_revision(path, revision)
            self.assertEqual(message_html.extract_lettersmith_style_state(restored), state)

    def test_style_state_comment_rejects_invalid_and_duplicate_payloads(self) -> None:
        state = {"schema_version": 1, "definitions": {}, "blocks": []}
        valid = message_html.embed_lettersmith_style_state("<p>Hello</p>", state)
        duplicated = valid + valid
        sanitized = sanitize_message_html(duplicated)
        self.assertEqual(sanitized.count("lettersmith-style-state:v1:"), 1)
        self.assertEqual(message_html.extract_lettersmith_style_state(sanitized), state)
        malformed = (
            "<!-- lettersmith-style-state:v1:invalid! -->"
            "<!-- lettersmith-style-state:v2:e30= -->"
            "<script><!-- lettersmith-style-state:v1:e30= --></script>"
            "<p>Hello</p>"
        )
        self.assertIsNone(message_html.extract_lettersmith_style_state(malformed))
        self.assertNotIn("lettersmith-style-state", sanitize_message_html(malformed))
        with self.assertRaises(ValueError):
            message_html.embed_lettersmith_style_state("<p>Hello</p>", {"schema_version": 3})

    def test_future_style_state_is_preserved_but_not_loaded(self) -> None:
        encoded = base64.b64encode(
            b'{"schema_version":3,"future_field":"keep"}'
        ).decode("ascii")
        raw = f"<!-- lettersmith-style-state:v3:{encoded} --><p>Hello</p>"
        safe = sanitize_message_html(raw)
        self.assertIn(f"lettersmith-style-state:v3:{encoded}", safe)
        self.assertEqual(sanitize_message_html(safe), safe)
        self.assertTrue(message_html.has_unsupported_lettersmith_style_state(safe))
        self.assertIsNone(message_html.extract_lettersmith_style_state(safe))

    def test_sanitizer_preserves_passive_formatting_and_drops_active_content(self) -> None:
        raw = """
<!doctype html><html><!-- lettersmith-message:v2 --><head>
<meta http-equiv="refresh" content="0;url=file:///private"><style>@import 'https://evil.test/a.css';</style>
</head><body><div class="ls-linewrap forged" id="textWallContent" onclick="run()">
<p style="color:#123456; font-family:'Papyrus'; line-height:2; position:fixed; background-image:url(https://evil.test/x)">
<a href="https://example.com">Web</a><a href="ultralink:Hello%20there">Note</a>
<a href="javascript:alert(1)">Bad</a><img src="gallery/message_assets/photo.png" onerror="run()">
<img src="data:image/png;base64,iVBORw0KGgo="><img src="../private.png">
<script>alert(1)</script><svg><script>alert(2)</script></svg></p></div></body></html>
"""

        sanitized = sanitize_message_html(raw)

        self.assertIn("lettersmith-message:v2", sanitized)
        self.assertIn('class="ls-linewrap"', sanitized)
        self.assertIn("color:#123456", sanitized)
        self.assertIn("font-family:'Papyrus'", sanitized)
        self.assertIn('href="https://example.com"', sanitized)
        self.assertIn('href="ultralink:Hello%20there"', sanitized)
        self.assertIn('src="gallery/message_assets/photo.png"', sanitized)
        self.assertIn('src="data:image/png;base64,iVBORw0KGgo="', sanitized)
        self.assertNotIn("javascript:", sanitized.casefold())
        self.assertNotIn("onclick", sanitized.casefold())
        self.assertNotIn("onerror", sanitized.casefold())
        self.assertNotIn("position:", sanitized.casefold())
        self.assertNotIn("background-image", sanitized.casefold())
        self.assertNotIn("evil.test/a.css", sanitized)
        self.assertNotIn("<script", sanitized.casefold())
        self.assertNotIn("<svg", sanitized.casefold())
        self.assertNotIn("../private.png", sanitized)
        self.assertNotIn('id="textWallContent"', sanitized)
        self.assertEqual(sanitize_message_html(sanitized), sanitized)

    def test_sanitizer_preserves_url_spaces_and_bounds_link_geometry(self) -> None:
        raw = (
            '<a href="https://example.com/?subject=Hello World" '
            'style="padding:10000px; margin:-10000px; color:transparent; font-size:20pt">'
            '<span style="padding:10000px; font-size:20pt">Hello</span>'
            '<img src="gallery/message_assets/my photo.png"><br></a>'
            '<img src="gallery/message_assets/my photo.png">'
        )

        sanitized = sanitize_message_html(raw)

        self.assertIn("subject=Hello World", sanitized)
        self.assertIn("font-size:20pt", sanitized)
        self.assertNotIn("padding", sanitized)
        self.assertNotIn("margin", sanitized)
        self.assertNotIn("transparent", sanitized)
        self.assertIn("white-space:normal", sanitized)
        self.assertEqual(
            sanitized.count('src="gallery/message_assets/my photo.png"'),
            1,
        )

    def test_sanitizer_falls_back_to_bounded_plain_text_on_tag_flood(self) -> None:
        with mock.patch.object(message_html, "MAX_MESSAGE_HTML_TAGS", 4):
            sanitized = sanitize_message_html("<b>" * 10 + "text" + "</b>" * 10)

        self.assertNotIn("<b>", sanitized)
        self.assertIn("&lt;b&gt;", sanitized)

        with mock.patch.object(message_html, "MAX_MESSAGE_HTML_OUTPUT_CHARACTERS", 5):
            sanitized = sanitize_message_html("<b>x")
        self.assertLessEqual(len(sanitized), 5)

    def test_message_and_revision_writes_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            message = Path(directory) / "message.html"
            malicious = '<p onclick="run()">Hello<script>run()</script></p>'

            write_message_with_revision(message, malicious, reason="test")
            self.assertEqual(message.read_text(encoding="utf-8"), "<p>Hello</p>")

            revisions = revision_directory(message)
            revisions.mkdir(parents=True, exist_ok=True)
            revision = revisions / "20260101-000000-000000__test.html"
            revision.write_text(malicious, encoding="utf-8")
            restored = restore_revision(message, revision)
            self.assertEqual(restored, "<p>Hello</p>")
            self.assertEqual(message.read_text(encoding="utf-8"), "<p>Hello</p>")


class MessageImportSecurityTests(unittest.TestCase):
    def test_normal_text_import_runs_through_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "letter.txt"
            source.write_text("Hello <friend>", encoding="utf-8")

            imported = message_import.import_message_sync(source)

            self.assertEqual(imported, "Hello &lt;friend&gt;")

    def test_input_size_limit_is_enforced_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "letter.html"
            source.write_bytes(b"x" * 17)

            with mock.patch.object(message_import, "MAX_INPUT_BYTES", 16):
                with self.assertRaisesRegex(
                    message_import.MessageImportError,
                    "too large",
                ):
                    message_import.extract_message_html(source)

    def test_document_archive_expansion_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "letter.docx"
            with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", "x" * 128)

            with mock.patch.object(
                message_import,
                "MAX_ARCHIVE_EXPANDED_BYTES",
                64,
            ):
                with self.assertRaisesRegex(
                    message_import.MessageImportError,
                    "too large",
                ):
                    message_import.extract_message_html(source)

    def test_pdf_stream_decompression_is_bounded(self) -> None:
        compressed = message_import.zlib.compress(b"x" * 128)
        with mock.patch.object(message_import, "MAX_PDF_STREAM_BYTES", 64):
            with self.assertRaisesRegex(
                message_import.MessageImportError,
                "too complex",
            ):
                message_import._bounded_zlib_decompress(compressed)

    def test_worker_command_does_not_resolve_source_on_calling_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            with mock.patch.object(
                Path,
                "resolve",
                side_effect=OSError("offline share"),
            ):
                _program, arguments = message_import.worker_command(
                    "relative.txt",
                    result_path,
                )

        self.assertTrue(os.path.isabs(arguments[-2]))

    def test_worker_result_reuses_private_precreated_file(self) -> None:
        result_path = message_import.create_result_path()
        try:
            message_import._write_worker_result(
                result_path,
                {"ok": True, "html": "private"},
            )
            self.assertEqual(message_import.read_worker_result(result_path), "private")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
            self.assertEqual(
                list(result_path.parent.glob(f".{result_path.name}.*.tmp")),
                [],
            )
        finally:
            message_import.remove_result_path(result_path, attempts=3)

    def test_worker_result_verifies_inode_before_truncating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_path = root / "result.json"
            victim = root / "victim.txt"
            result_path.write_bytes(b"")
            victim.write_text("preserve me", encoding="utf-8")
            real_open = os.open

            def redirect_open(path: object, flags: int, *args: object) -> int:
                return real_open(victim, flags, *args)

            with mock.patch.object(message_import.os, "open", side_effect=redirect_open):
                with self.assertRaises(OSError):
                    message_import._write_worker_result(
                        result_path,
                        {"ok": True, "html": "private"},
                    )

            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve me")

    def test_non_redirecting_cloud_reparse_tag_is_not_treated_as_link(self) -> None:
        path_stat = mock.Mock(
            st_file_attributes=0x00000400,
            st_reparse_tag=0x9000001A,
        )
        with (
            mock.patch.object(Path, "is_symlink", return_value=False),
            mock.patch.object(Path, "is_junction", return_value=False),
            mock.patch.object(Path, "lstat", return_value=path_stat),
        ):
            self.assertFalse(transactional_io.is_link_or_reparse_point("cloud"))
            path_stat.st_reparse_tag = 0xA000000C
            self.assertTrue(transactional_io.is_link_or_reparse_point("link"))


class MessageImportProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv)
        )

    def test_message_tab_import_is_asynchronous_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incoming.html"
            source.write_text(
                "<!-- lettersmith-message:v2 -->"
                '<p onclick="run()">Hello<script>run()</script></p>',
                encoding="utf-8",
            )
            message = MessageTab(str(root))

            message._process_file(str(source))

            self.assertIsNotNone(message._message_import_process)
            self.assertFalse(message.btn.isEnabled())
            result_path = message._message_import_result_path
            deadline = 5_000
            while message._message_import_process is not None and deadline > 0:
                self.app.processEvents()
                QtTest.QTest.qWait(20)
                deadline -= 20
            self.assertIsNone(message._message_import_process)
            self.assertTrue(message.btn.isEnabled())
            self.assertIsNotNone(result_path)
            self.assertFalse(result_path.exists())
            stored = message._html_path().read_text(encoding="utf-8")
            self.assertIn("Hello", stored)
            self.assertNotIn("onclick", stored)
            self.assertNotIn("<script", stored)
            self.assertTrue(message.shutdown())
            message.deleteLater()
            self.app.processEvents()

class GeneratedViewerSecurityTests(unittest.TestCase):
    @staticmethod
    def _prepare_project(root: Path) -> None:
        pages = root / "gallery/user/pages"
        controls = root / "gallery/user/card/controls"
        pages.mkdir(parents=True)
        controls.mkdir(parents=True)
        image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGBA8888)
        image.fill(QtGui.QColor("white"))
        for name in REQUIRED_SLIDES:
            if not image.save(str(pages / name)):
                raise AssertionError(f"Could not create {name}")
        for name in CONTROL_FILES:
            if not image.save(str(controls / name)):
                raise AssertionError(f"Could not create {name}")
        banner = root / generate.APP_BANNER_PATH
        banner.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(banner)):
            raise AssertionError("Could not create banner")
        SettingsStore(root).update_fields(
            {
                "recipient_name": "Security Reader",
                "recipient_title": "Safe Letter",
            }
        )

    def test_generated_viewer_sanitizes_message_and_uses_nonce_csp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._prepare_project(root)
            message = (
                "<!-- lettersmith-message:v2 -->"
                "<p>{{CSP_NONCE}}</p>"
                '<img src="data:image/png;base64,iVBORw0KGgo=" onerror=run()>'
                "<script nonce={{CSP_NONCE}}>run()</script>"
            )

            play = generate.generate_play_bundle(
                root,
                message_html=message,
                seed_sfx=False,
            )
            index = (play / "index.html").read_text(encoding="utf-8")
            saved_message = (play / "gallery/message/message.html").read_text(
                encoding="utf-8"
            )

            nonce_match = re.search(r"script-src 'nonce-([^']+)'", index)
            self.assertIsNotNone(nonce_match)
            nonce = nonce_match.group(1)
            self.assertEqual(re.findall(r'<script nonce="([^"]+)"', index), [nonce, nonce])
            self.assertEqual(index.count(nonce), 3)
            self.assertIn("{{CSP_NONCE}}", index)
            self.assertNotIn("<script nonce={{CSP_NONCE}}>", index)
            self.assertNotIn("onerror", index.casefold())
            self.assertNotIn("run()</script>", index)
            self.assertNotIn("onerror", saved_message.casefold())
            self.assertNotIn("<script", saved_message.casefold())
            generate.validate_play_bundle(play)


if __name__ == "__main__":
    unittest.main()
