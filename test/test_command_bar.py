from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from command_bar import (
    CommandBarData,
    CommandBarWindow,
    build_command_bar_data,
    launch_command_bar,
)
from config import CONTROL_FILES, PLAY_METADATA_FILE, REQUIRED_SLIDES
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore


class CommandBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    @staticmethod
    def _write_saved_letter(
        root: Path,
        folder: str,
        *,
        recipient: str,
        title: str,
        published_url: str,
        activity: str,
        metadata_name: str = PLAY_METADATA_FILE,
    ) -> Path:
        play_dir = root / "output" / "Play" / folder
        pages = play_dir / "gallery" / "pages"
        message = play_dir / "gallery" / "message"
        controls = play_dir / "gallery" / "controls"
        for directory in (pages, message, controls):
            directory.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_SLIDES:
            (pages / name).write_bytes(b"image")
        for name in CONTROL_FILES:
            (controls / name).write_bytes(b"control")
        (message / "message.html").write_text("message", encoding="utf-8")
        (play_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        (play_dir / "styles.css").write_text("body{}", encoding="utf-8")
        (play_dir / "script.js").write_text("", encoding="utf-8")
        (play_dir / metadata_name).write_text(
            json.dumps(
                {
                    "recipient_name": recipient,
                    "recipient_title": title,
                    "published_page_url": published_url,
                    "last_activity_at": activity,
                }
            ),
            encoding="utf-8",
        )
        return play_dir

    def test_active_build_metadata_wins_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            play_dir = root / "output" / "Play" / "Amanda Miller" / "A Letter"
            play_dir.mkdir(parents=True)
            (play_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            (play_dir / PLAY_METADATA_FILE).write_text(
                json.dumps(
                    {
                        "recipient_name": "Amanda Miller",
                        "recipient_title": "A Letter",
                        "published_page_url": "https://example.com/letter",
                    }
                ),
                encoding="utf-8",
            )
            SettingsStore(root).update_fields(
                {
                    ACTIVE_PLAY_DIR_KEY: str(play_dir),
                    "recipient_name": "Cleared later",
                    "recipient_title": "Cleared later",
                }
            )

            data = build_command_bar_data(root)

        self.assertEqual(data.recipient_name, "Amanda Miller")
        self.assertEqual(data.recipient_title, "A Letter")
        self.assertEqual(data.published_url, "https://example.com/letter")
        self.assertTrue(data.local_preview_path is not None)

    def test_missing_decorative_assets_do_not_block_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = CommandBarData(
                recipient_name="Recipient",
                recipient_title="Title",
                local_preview_path=None,
                published_url="",
            )
            window = CommandBarWindow(data, temporary)
            self.assertIsNone(window.movie)
            self.assertFalse(window.preview_button.isEnabled())
            self.assertFalse(window.open_button.isEnabled())
            self.assertFalse(window.copy_button.isEnabled())
            self.assertGreaterEqual(window.width(), window.MIN_WIDTH)
            self.assertEqual(window.preview_button.graphicsEffect().opacity(), 0.38)
            window.show()
            self.app.processEvents()
            fallback_pixel = window.grab().toImage().pixelColor(
                8,
                window.height() // 2,
            )
            self.assertEqual(fallback_pixel.alpha(), 255)
            self.assertEqual(
                (
                    fallback_pixel.red(),
                    fallback_pixel.green(),
                    fallback_pixel.blue(),
                ),
                (0, 0, 0),
            )
            window.abort_launch()

    def test_fallback_selects_newest_exact_match_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_saved_letter(
                root,
                "unrelated",
                recipient="Someone Else",
                title="Different Letter",
                published_url="https://example.com/unrelated",
                activity="2030-01-01T00:00:00+00:00",
            )
            self._write_saved_letter(
                root,
                "matching-old",
                recipient="Amanda Miller",
                title="Words of Encouragement",
                published_url="https://example.com/old",
                activity="2028-01-01T00:00:00+00:00",
            )
            newest_match = self._write_saved_letter(
                root,
                "matching-new",
                recipient="Amanda Miller",
                title="Words of Encouragement",
                published_url="https://example.com/new",
                activity="2029-01-01T00:00:00+00:00",
                metadata_name="metadata.json",
            )
            outside = root / "outside"
            outside.mkdir()
            (outside / "index.html").write_text("unrelated", encoding="utf-8")
            SettingsStore(root).update_fields(
                {
                    ACTIVE_PLAY_DIR_KEY: str(outside),
                    "recipient_name": "Amanda Miller",
                    "recipient_title": "Words of Encouragement",
                }
            )

            data = build_command_bar_data(root)

        self.assertEqual(
            data.local_preview_path,
            newest_match.resolve() / "index.html",
        )
        self.assertEqual(data.published_url, "https://example.com/new")

    def test_valid_gif_uses_native_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            icons = root / "gallery" / "app" / "icons"
            icons.mkdir(parents=True)
            icons.joinpath("bloodsnow.gif").write_bytes(
                bytes.fromhex(
                    "47494638396101000100800000000000ffffff"
                    "21ff0b4e45545343415045322e300301000000"
                    "21f904000a0000002c0000000001000100000202440100"
                    "21f904000a0000002c00000000010001000002024c01003b"
                )
            )
            window = CommandBarWindow(
                CommandBarData("Recipient", "Title", None, ""),
                root,
            )
            self.assertIsNotNone(window.movie)
            self.assertEqual(window.width(), window.MIN_WIDTH)
            movie = window.movie
            try:
                window.show()
                self.app.processEvents()
                self.assertEqual(movie.state(), QtGui.QMovie.MovieState.Running)
            finally:
                window.abort_launch()
            self.assertEqual(movie.state(), QtGui.QMovie.MovieState.NotRunning)
            self.assertEqual(movie.fileName(), "")

    def test_long_text_is_elided_and_keeps_full_tooltip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recipient = ("Amanda Miller " * 20).strip()
            window = CommandBarWindow(
                CommandBarData(recipient, "Words of Encouragement", None, ""),
                temporary,
            )
            window.show()
            self.app.processEvents()
            self.assertNotEqual(window.recipient_label.text(), recipient)
            self.assertEqual(window.recipient_label.toolTip(), recipient)
            window.abort_launch()

    def test_copy_uses_validated_url_and_restores_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window = CommandBarWindow(
                CommandBarData(
                    "Recipient",
                    "Title",
                    None,
                    "https://example.com/letter",
                ),
                temporary,
            )
            window._copy_published()
            self.assertEqual(
                QtWidgets.QApplication.clipboard().text(),
                "https://example.com/letter",
            )
            self.assertEqual(window.copy_button.text(), "✓")
            window._restore_copy_button()
            self.assertEqual(window.copy_button.text(), "⧉")
            window.abort_launch()

    def test_preview_and_open_use_preserved_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "output" / "Play" / "letter" / "index.html"
            index.parent.mkdir(parents=True)
            index.write_text("<html></html>", encoding="utf-8")
            window = CommandBarWindow(
                CommandBarData(
                    "Recipient",
                    "Title",
                    index,
                    "https://example.com/letter",
                ),
                root,
            )
            with mock.patch.object(
                QtGui.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as open_url:
                window._preview_letter()
                preview_url = open_url.call_args.args[0]
                self.assertTrue(preview_url.isLocalFile())
                self.assertEqual(
                    Path(preview_url.toLocalFile()).resolve(),
                    index.resolve(),
                )
                window._open_published()
                self.assertEqual(
                    open_url.call_args.args[0].toString(),
                    "https://example.com/letter",
                )
            window.abort_launch()

    def test_malformed_url_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window = CommandBarWindow(
                CommandBarData("Recipient", "Title", None, "javascript:alert(1)"),
                temporary,
            )
            self.assertEqual(window.data.published_url, "")
            self.assertFalse(window.open_button.isEnabled())
            self.assertFalse(window.copy_button.isEnabled())
            window.abort_launch()

    def test_active_path_setting_normalizes_non_string_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = SettingsStore(temporary)
            settings.update_fields({ACTIVE_PLAY_DIR_KEY: 42})
            self.assertEqual(settings.get(ACTIVE_PLAY_DIR_KEY), "")

    def test_deferred_launch_reuses_one_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = CommandBarData("Recipient", "Title", None, "")
            first = launch_command_bar(data, temporary, show=False)
            second = launch_command_bar(data, temporary, show=False)
            self.assertIs(first, second)
            self.assertFalse(first.isVisible())
            first.abort_launch()
            self.app.processEvents()

    def test_nexus_closes_before_presenting_command_bar(self) -> None:
        from Nexus import Nexus

        events = []
        callbacks = []

        class PendingBar:
            presented = False
            aborted = False

            def present(self) -> None:
                self.presented = True
                events.append("present")

            def abort_launch(self) -> None:
                self.aborted = True

        bar = PendingBar()

        class FakeNexus:
            _shutdown_complete = False
            _shutdown_in_progress = False
            _command_bar = None
            project_root = Path(".").resolve()
            _show_command_bar_after_close = staticmethod(
                Nexus._show_command_bar_after_close
            )

            @staticmethod
            def screen():
                return None

            @staticmethod
            def _release_forge_preview_files() -> None:
                events.append("release")

            @staticmethod
            def close() -> bool:
                events.append("close")
                return True

        original_policy = self.app.quitOnLastWindowClosed()
        data = CommandBarData("Recipient", "Title", None, "")
        with (
            mock.patch(
                "command_bar.launch_command_bar",
                side_effect=lambda *_args, **_kwargs: events.append("create") or bar,
            ),
            mock.patch.object(
                QtCore.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callbacks.append(callback),
            ),
        ):
            self.assertTrue(
                Nexus.open_command_bar_and_close_editor(FakeNexus(), data)
            )
            self.assertEqual(events, ["create", "release", "close"])
            self.assertFalse(bar.presented)
            self.assertFalse(self.app.quitOnLastWindowClosed())
            callbacks[0]()

        self.assertTrue(bar.presented)
        self.assertEqual(events[-1], "present")
        self.assertEqual(self.app.quitOnLastWindowClosed(), original_policy)

    def test_refused_nexus_close_aborts_without_quitting(self) -> None:
        from Nexus import Nexus

        class PendingBar:
            aborted = False

            def abort_launch(self) -> None:
                self.aborted = True

        bar = PendingBar()

        class FakeNexus:
            _shutdown_complete = False
            _shutdown_in_progress = False
            _command_bar = None
            project_root = Path(".").resolve()
            _show_command_bar_after_close = staticmethod(
                Nexus._show_command_bar_after_close
            )

            @staticmethod
            def screen():
                return None

            @staticmethod
            def _release_forge_preview_files() -> None:
                return None

            @staticmethod
            def close() -> bool:
                return False

        original_policy = self.app.quitOnLastWindowClosed()
        with (
            mock.patch("command_bar.launch_command_bar", return_value=bar),
            mock.patch.object(QtCore.QTimer, "singleShot") as single_shot,
            mock.patch("Nexus._LOGGER.exception"),
        ):
            self.assertFalse(
                Nexus.open_command_bar_and_close_editor(
                    FakeNexus(),
                    CommandBarData("Recipient", "Title", None, ""),
                )
            )

        self.assertTrue(bar.aborted)
        single_shot.assert_not_called()
        self.assertEqual(self.app.quitOnLastWindowClosed(), original_policy)

    def test_aborted_handoff_does_not_quit_but_normal_close_does(self) -> None:
        data = CommandBarData("Recipient", "Title", None, "")
        with tempfile.TemporaryDirectory() as temporary:
            aborted = CommandBarWindow(data, temporary)
            with mock.patch.object(QtCore.QTimer, "singleShot") as single_shot:
                aborted.abort_launch()
                single_shot.assert_not_called()

            closing = CommandBarWindow(data, temporary)
            with mock.patch.object(QtCore.QTimer, "singleShot") as single_shot:
                closing.close()
                single_shot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
