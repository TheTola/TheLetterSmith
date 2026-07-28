from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PIL import Image

import Forge_Tab
import generate
from Forge_Tab import ForgeTab
from config import CONTROL_FILES, FLIP_COUNT, FLIP_PREFIX, GLISS_FILE, REQUIRED_SLIDES
from publishing.github_pages import (
    REPOSITORY_KEY,
    WORKSPACE_KEY,
    GitHubPagesPublisher,
)
from publishing.models import PublishConfiguration, PublishResult
from readiness import evaluate_readiness
from saved_letters import (
    SavedLetter,
    SavedLetterCatalog,
    SavedLetterRestoreError,
    SavedLetterRestorer,
)
from settings_store import (
    SettingsStore,
    normalize_published_page_url,
)
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    import_runtime_track,
    resolve_project_tracks,
    save_project_state,
)
from transactional_io import PathTransaction


def _populate_required(root: Path) -> None:
    pages = root / "gallery/user/pages"
    message = root / "gallery/user/message"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        (pages / name).write_bytes(b"image")
    (message / "message.html").write_text("<p>Message</p>", encoding="utf-8")
    SettingsStore(root).update_fields(
        recipient_name="Ada",
        recipient_title="Birthday",
    )


def _saved_letter(root: Path) -> SavedLetter:
    play_dir = root / "output/Play/saved"
    pages = play_dir / "gallery/pages"
    message = play_dir / "gallery/message"
    controls = play_dir / "gallery/controls"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    controls.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        (pages / name).write_bytes(f"saved-{name}".encode())
    for name in CONTROL_FILES:
        (controls / name).write_bytes(b"control")
    (message / "message.html").write_text("<p>Saved</p>", encoding="utf-8")
    (play_dir / "index.html").write_text(
        "<html><title>Saved Title</title></html>",
        encoding="utf-8",
    )
    (play_dir / "styles.css").write_text("", encoding="utf-8")
    (play_dir / "script.js").write_text("", encoding="utf-8")
    (play_dir / "lettersmith-metadata.json").write_text(
        json.dumps(
            {
                "project_id": str(uuid.uuid4()),
                "recipient_name": "Saved Recipient",
                "recipient_title": "Saved Title",
                "published_page_url": "https://example.test/saved/",
                "settings": {
                    "message_overlay_preset": "paper",
                    "message_overlay_opacity": 72,
                },
            }
        ),
        encoding="utf-8",
    )
    return SavedLetter(
        path=play_dir,
        recipient="Saved Recipient",
        title="Saved Title",
        modified_at=datetime.fromtimestamp(play_dir.stat().st_mtime),
        published_url="https://example.test/saved/",
        cover_path=pages / "cover.png",
    )


def _populate_runtime_assets(root: Path) -> None:
    controls = root / "gallery/user/card/controls"
    controls.mkdir(parents=True)
    for name in CONTROL_FILES:
        path = controls / name
        if name in {"cleft.png", "cright.png"}:
            Image.new("RGBA", (6, 6), (128, 128, 128, 255)).save(path)
        else:
            path.write_bytes(b"control")
    app_sounds = root / "gallery/app/sounds"
    app_sounds.mkdir(parents=True)
    (app_sounds / GLISS_FILE).write_bytes(b"sound")
    for index in range(1, FLIP_COUNT + 1):
        (app_sounds / f"{FLIP_PREFIX}{index}.mp3").write_bytes(b"sound")


def _add_saved_tracks(
    entry: SavedLetter,
    names: tuple[str, ...],
    *,
    mode: str = "playlist",
) -> None:
    sounds = entry.path / "gallery/sounds"
    sounds.mkdir(parents=True, exist_ok=True)
    tracks = []
    for index, name in enumerate(names):
        filename = f"music-{index + 1}.mp3"
        (sounds / filename).write_bytes(name.encode("utf-8"))
        tracks.append(
            {
                "filename": filename,
                "display_title": name,
                "original_name": f"{name}.mp3",
                "duration_seconds": index + 1,
            }
        )
    (sounds / BUILD_SOUND_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 2,
                "mode": mode,
                "crossfade_ms": 1000 if len(tracks) > 1 else 0,
                "tracks": tracks,
            }
        ),
        encoding="utf-8",
    )


def _active_snapshot(root: Path) -> tuple[bytes, str, dict]:
    return (
        (root / "gallery/user/pages/cover.png").read_bytes(),
        (root / "gallery/user/message/message.html").read_text(
            encoding="utf-8"
        ),
        SettingsStore(root).snapshot(),
    )


def _catalog_build(
    root: Path,
    relative: str,
    *,
    html_title: str,
    metadata: dict | None = None,
) -> Path:
    play_dir = root / relative
    pages = play_dir / "gallery/pages"
    message = play_dir / "gallery/message"
    controls = play_dir / "gallery/controls"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    controls.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        (pages / name).write_bytes(name.encode("utf-8"))
    for name in CONTROL_FILES:
        (controls / name).write_bytes(b"control")
    (message / "message.html").write_text(
        "<p>Catalog message</p>",
        encoding="utf-8",
    )
    (play_dir / "index.html").write_text(
        f"<html><title>{html_title}</title></html>",
        encoding="utf-8",
    )
    (play_dir / "styles.css").write_text("", encoding="utf-8")
    (play_dir / "script.js").write_text("", encoding="utf-8")
    if metadata is not None:
        (play_dir / "lettersmith-metadata.json").write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
    return play_dir


class ForgeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.app.processEvents()
        self.temporary.cleanup()

    def test_readiness_uses_supported_optional_statuses(self) -> None:
        _populate_required(self.root)
        result = evaluate_readiness(self.root)
        self.assertTrue(result.can_preview)
        self.assertEqual(result.status, "Ready — Missing Optional Features")
        self.assertEqual(
            {item.key for item in result.missing_items},
            {"music", "published_url"},
        )

        SettingsStore(self.root).update_fields(
            published_page_url="https://example.test/letter/"
        )
        self.assertEqual(
            evaluate_readiness(self.root).status,
            "Ready — Missing Music",
        )

        music = self.root / "track.mp3"
        music.write_bytes(b"audio")
        record = import_runtime_track(self.root, music, display_title="Track")
        save_project_state(
            self.root,
            ProjectSoundState(
                single_track_id=record.track_id,
                selected_track_id=record.track_id,
            ),
        )
        self.assertEqual(evaluate_readiness(self.root).status, "Ready")

    def test_forge_uses_plain_actions_and_missing_only_readiness(self) -> None:
        _populate_required(self.root)
        tab = ForgeTab(self.root)
        tab.refresh_readiness()

        self.assertEqual(tab.preview_btn.text(), "Preview Letter")
        self.assertEqual(tab.publish_btn.text(), "Publish Letter")
        self.assertEqual(
            tab.open_published_btn.text(),
            "Open Published Letter",
        )
        self.assertFalse(hasattr(tab, "generate_btn"))
        self.assertFalse(hasattr(tab, "seal_btn"))
        self.assertEqual(tab.load_saved_btn.text(), "Load Letters")
        self.assertTrue(tab.saved_panel.isWindow())
        self.assertTrue(bool(tab.saved_panel.windowFlags() & Qt.Popup))
        self.assertGreaterEqual(tab.saved_panel.minimumHeight(), 480)
        self.assertGreaterEqual(tab.saved_panel.maximumHeight(), 720)
        self.assertIsNotNone(tab.saved_scroll)
        self.assertFalse(hasattr(tab, "refresh_saved_btn"))
        self.assertFalse(hasattr(tab, "saved_selector"))
        self.assertFalse(hasattr(tab, "published_url"))
        self.assertFalse(hasattr(tab, "copy_link_btn"))
        self.assertIsNotNone(tab.saved_delete_toggle)
        self.assertEqual(tab.heading_title.alignment(), Qt.AlignCenter)
        self.assertIsNotNone(tab.preview_format_panel)
        visible = {
            key
            for key, button in tab.readiness_window._missing_buttons.items()
            if not button.isHidden()
        }
        self.assertEqual(visible, {"music", "published_url"})
        tab.resize(1200, 800)
        tab.show()
        self.app.processEvents()
        tab.show_saved_letters()
        self.app.processEvents()
        self.assertTrue(tab.saved_panel.isVisible())
        self.assertGreaterEqual(tab.saved_panel.height(), 480)
        tab.saved_panel.hide()
        tab.close()

    def test_saved_letters_use_cover_cards_and_missing_cover_placeholder(
        self,
    ) -> None:
        published = _saved_letter(self.root)
        cover = QtGui.QPixmap(30, 45)
        cover.fill(QtGui.QColor("#cc66aa"))
        self.assertTrue(cover.save(str(published.cover_path), "PNG"))
        local = _catalog_build(
            self.root,
            "output/Play/local-letter",
            html_title="Local Letter",
            metadata={
                "recipient_title": "Local Letter",
                "recipient_name": "Local Recipient",
                "published_page_url": "",
            },
        )
        (local / "gallery/pages/cover.png").unlink()
        root_cover = _catalog_build(
            self.root,
            "output/Play/root-cover",
            html_title="Root Cover",
            metadata={
                "recipient_title": "Root Cover",
                "recipient_name": "Root Recipient",
            },
        )
        shutil.move(
            str(root_cover / "gallery/pages/cover.png"),
            str(root_cover / "cover.png"),
        )
        self.assertTrue(cover.save(str(root_cover / "cover.png"), "PNG"))

        tab = ForgeTab(self.root)
        tab.resize(1200, 600)
        tab.show()
        self.app.processEvents()

        by_title = {card.entry.title: card for card in tab._saved_cards}
        self.assertEqual(
            by_title["Saved Title"].status_label.text(),
            "Published",
        )
        self.assertFalse(by_title["Saved Title"].cover.pixmap().isNull())
        self.assertEqual(
            by_title["Local Letter"].status_label.text(),
            "Local",
        )
        self.assertEqual(by_title["Local Letter"].cover.text(), "No cover")
        self.assertEqual(
            by_title["Root Cover"].entry.cover_path,
            (root_cover / "cover.png").resolve(),
        )
        self.assertFalse(by_title["Root Cover"].cover.pixmap().isNull())
        self.assertIn("Title:", by_title["Local Letter"].title_label.text())
        self.assertIn(
            "Recipient:",
            by_title["Local Letter"].recipient_label.text(),
        )
        self.assertEqual(by_title["Local Letter"].delete_button.text(), "−")
        self.assertTrue(by_title["Local Letter"].delete_button.isHidden())

        tab.saved_delete_toggle.click()
        self.app.processEvents()
        self.assertTrue(tab.saved_delete_toggle.isChecked())
        self.assertFalse(by_title["Local Letter"].delete_button.isHidden())

        tab._select_saved_letter(by_title["Saved Title"].entry)
        selected_path = tab._selected_saved_letter.path
        tab.refresh_saved_letters()
        self.assertEqual(tab._selected_saved_letter.path, selected_path)

        local_entry = next(
            card.entry
            for card in tab._saved_cards
            if card.entry.title == "Local Letter"
        )
        with mock.patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ):
            tab._delete_saved_letter(local_entry)
        self.assertFalse(local.exists())
        self.assertFalse(tab.saved_delete_toggle.isChecked())
        self.assertNotIn(
            "Local Letter",
            {card.entry.title for card in tab._saved_cards},
        )
        tab.close()

    def test_preview_opens_local_index_in_default_browser_without_publisher(
        self,
    ) -> None:
        _populate_required(self.root)
        play_dir = self.root / "output/Play/browser-preview"
        play_dir.mkdir(parents=True)
        index = play_dir / "index.html"
        index.write_text("<html></html>", encoding="utf-8")
        tab = ForgeTab(self.root)
        releases: list[bool] = []
        tab.preview_files_release_requested.connect(
            lambda: releases.append(True)
        )

        def run_now(_activity, task, on_success, _error_message):
            on_success(task())

        with (
            mock.patch.object(
                generate,
                "ensure_play_bundle",
                return_value=(play_dir, True),
            ),
            mock.patch.object(
                Forge_Tab,
                "GitHubPagesPublisher",
                side_effect=AssertionError("Preview must not publish"),
            ),
            mock.patch.object(
                Forge_Tab.QtGui.QDesktopServices,
                "openUrl",
                return_value=True,
            ) as opener,
            mock.patch.object(tab, "_start_operation", side_effect=run_now),
        ):
            tab.preview_letter()

        opened_url = opener.call_args.args[0]
        self.assertTrue(opened_url.isLocalFile())
        self.assertEqual(Path(opened_url.toLocalFile()).resolve(), index.resolve())
        self.assertEqual(
            tab.status.toPlainText(),
            "Preview opened in your browser.",
        )
        self.assertEqual(releases, [True])
        tab._metadata_timer.stop()
        tab.close()

    def test_missing_github_cli_keeps_local_publish_build_usable(self) -> None:
        _populate_required(self.root)
        entry = _saved_letter(self.root)
        SettingsStore(self.root).update_fields(
            github_pages_public_warning_acknowledged=True
        )
        tab = ForgeTab(self.root)
        publisher = mock.Mock()
        publisher.is_configured.return_value = False
        publisher.configure.return_value = PublishConfiguration(
            False,
            message=(
                "GitHub CLI is required to configure publishing for the "
                "first time."
            ),
        )

        def run_now(_activity, task, on_success, _error_message):
            on_success(task())

        with (
            mock.patch.object(
                generate,
                "ensure_play_bundle",
                return_value=(entry.path, False),
            ),
            mock.patch.object(
                Forge_Tab,
                "update_saved_metadata",
                return_value={},
            ),
            mock.patch.object(
                Forge_Tab,
                "GitHubPagesPublisher",
                return_value=publisher,
            ),
            mock.patch.object(tab, "_start_operation", side_effect=run_now),
        ):
            tab.publish_letter()

        self.assertIn("GitHub CLI", tab.status.toPlainText())
        self.assertIn(
            "local letter was generated successfully",
            tab.status.toPlainText(),
        )
        self.assertTrue(entry.path.joinpath("index.html").is_file())
        self.assertTrue(tab.preview_btn.isEnabled())
        tab.close()

    def test_worker_completion_runs_on_application_thread(self) -> None:
        _populate_required(self.root)
        tab = ForgeTab(self.root)
        loop = QtCore.QEventLoop()
        observed: dict[str, bool] = {}

        def task():
            observed["task_on_gui"] = (
                QtCore.QThread.currentThread() is self.app.thread()
            )
            return "done"

        def completed(result):
            observed["result"] = result == "done"
            observed["callback_on_gui"] = (
                QtCore.QThread.currentThread() is self.app.thread()
            )

        tab._start_operation("Working…", task, completed, "Failed.")
        self.assertIsNotNone(tab._worker_thread)
        tab._worker_thread.finished.connect(loop.quit)
        QtCore.QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.app.processEvents()

        self.assertFalse(observed.get("task_on_gui", True))
        self.assertTrue(observed.get("callback_on_gui", False))
        self.assertTrue(observed.get("result", False))
        self.assertFalse(tab._busy)
        tab.close()

    def test_configured_git_workspace_does_not_require_github_cli(
        self,
    ) -> None:
        workspace = self.root / "publishing"
        (workspace / ".git").mkdir(parents=True)
        SettingsStore(self.root).update_fields(
            **{
                REPOSITORY_KEY: "owner/letters",
                WORKSPACE_KEY: str(workspace),
            }
        )

        def runner(command, **_kwargs):
            self.assertEqual(command, ["git", "remote", "get-url", "origin"])
            return mock.Mock(
                returncode=0,
                stdout="https://github.com/owner/letters.git\n",
                stderr="",
            )

        publisher = GitHubPagesPublisher(self.root, runner=runner)
        with (
            mock.patch.object(publisher, "git_available", return_value=True),
            mock.patch.object(publisher, "gh_available", return_value=False),
        ):
            self.assertTrue(publisher.is_configured())

    def test_saved_letter_load_is_transactional(self) -> None:
        _populate_required(self.root)
        original_pages = self.root / "gallery/user/pages"
        original_message = self.root / "gallery/user/message/message.html"
        original_cover = (original_pages / "cover.png").read_bytes()
        original_html = original_message.read_text(encoding="utf-8")
        entry = _saved_letter(self.root)
        tab = ForgeTab(self.root)
        real_replace_snapshot = tab.restorer.settings.replace_snapshot
        replace_attempts = 0

        def fail_first_settings_commit(settings):
            nonlocal replace_attempts
            replace_attempts += 1
            if replace_attempts == 1:
                raise OSError("simulated settings failure")
            return real_replace_snapshot(settings)

        with mock.patch.object(
            tab.restorer.settings,
            "replace_snapshot",
            side_effect=fail_first_settings_commit,
        ):
            tab._load_saved_letter(entry)

        self.assertEqual(
            (original_pages / "cover.png").read_bytes(),
            original_cover,
        )
        self.assertEqual(
            original_message.read_text(encoding="utf-8"),
            original_html,
        )
        self.assertIn(
            "could not be restored",
            tab.status.toPlainText(),
        )

        tab._load_saved_letter(entry)
        self.assertEqual(
            (original_pages / "cover.png").read_bytes(),
            b"saved-cover.png",
        )
        self.assertEqual(
            original_message.read_text(encoding="utf-8"),
            "<p>Saved</p>",
        )
        settings = SettingsStore(self.root).snapshot()
        self.assertEqual(settings["recipient_name"], "Saved Recipient")
        self.assertEqual(
            settings["published_page_url"],
            "https://example.test/saved/",
        )
        tab.close()

    def test_failed_generation_preserves_last_working_bundle(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)

        project_id = str(uuid.uuid4())
        SettingsStore(self.root).update_fields(project_id=project_id)
        previous = self.root / "output/Play" / project_id
        previous.mkdir(parents=True)
        (previous / "index.html").write_text(
            "last working",
            encoding="utf-8",
        )

        with mock.patch.object(
            generate,
            "_atomic_write_text",
            side_effect=OSError("simulated build failure"),
        ):
            with self.assertRaises(OSError):
                generate.generate_play_bundle(
                    str(self.root),
                    message_html="<p>New</p>",
                )

        self.assertEqual(
            (previous / "index.html").read_text(encoding="utf-8"),
            "last working",
        )

    def test_successful_generation_commits_complete_bundle(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        project_id = str(uuid.uuid4())
        SettingsStore(self.root).update_fields(project_id=project_id)
        previous = self.root / "output/Play" / project_id
        previous.mkdir(parents=True)
        (previous / "obsolete.txt").write_text("old", encoding="utf-8")

        result = generate.generate_play_bundle(
            str(self.root),
            message_html="<p>Playable</p>",
        )

        expected = (self.root / "output/Play" / project_id).resolve()
        self.assertEqual(result, expected)
        for name in ("index.html", "styles.css", "script.js"):
            self.assertTrue((result / name).is_file())
        for name in REQUIRED_SLIDES:
            self.assertTrue((result / "gallery/pages" / name).is_file())
        self.assertFalse((result / "obsolete.txt").exists())
        generated_html = (result / "index.html").read_text(encoding="utf-8")
        generated_styles = (result / "styles.css").read_text(encoding="utf-8")
        generated_script = (result / "script.js").read_text(encoding="utf-8")
        self.assertIn('id="fullscreen-button"', generated_html)
        self.assertIn('aria-label="Enter fullscreen"', generated_html)
        self.assertIn('id="viewer-actions-left"', generated_html)
        self.assertIn('id="restart-button"', generated_html)
        self.assertIn('aria-label="Restart letter"', generated_html)
        self.assertIn('id="mute-button"', generated_html)
        self.assertIn('aria-label="Mute letter audio"', generated_html)
        self.assertNotIn('id="music-playback"', generated_html)
        self.assertIn('id="turn"', generated_html)
        self.assertIn('id="turnShadow"', generated_html)
        self.assertIn("requestFullscreen", generated_script)
        self.assertIn("window.location.reload", generated_script)
        self.assertIn("setViewerMuted", generated_script)
        self.assertIn("window.sessionStorage", generated_script)
        self.assertIn("function flipTo", generated_script)
        self.assertIn("page-turning", generated_script)
        self.assertIn("transform-style:preserve-3d", generated_styles)
        self.assertIn("--bottom-control-rail:60px", generated_styles)
        self.assertIn("--page-side-rail:50px", generated_styles)
        self.assertNotIn("music-playback", generated_script)
        self.assertNotIn("Pause music", generated_script)
        self.assertFalse(result.with_name(result.name + ".build-staging").exists())
        self.assertFalse(result.with_name(result.name + ".build-backup").exists())

    def test_readiness_required_items_and_music_requirement(self) -> None:
        _populate_required(self.root)
        pages = self.root / "gallery/user/pages"
        (pages / "cover.png").unlink()
        missing_cover = evaluate_readiness(self.root)
        self.assertEqual(missing_cover.status, "Not Ready")
        self.assertFalse(missing_cover.can_preview)
        self.assertFalse(missing_cover.can_publish)
        self.assertIn("cover", {item.key for item in missing_cover.missing_items})
        self.assertNotIn(
            "recipient",
            {item.key for item in missing_cover.missing_items},
        )

        (pages / "cover.png").write_bytes(b"image")
        message = self.root / "gallery/user/message/message.html"
        message.write_text("", encoding="utf-8")
        missing_message = evaluate_readiness(self.root)
        self.assertEqual(missing_message.status, "Not Ready")
        self.assertIn(
            "message",
            {item.key for item in missing_message.missing_items},
        )

        message.write_text("<p>Message</p>", encoding="utf-8")
        SettingsStore(self.root).update_fields(
            required_features={"music": True}
        )
        required_music = evaluate_readiness(self.root)
        self.assertEqual(required_music.status, "Not Ready")
        self.assertFalse(required_music.can_preview)
        self.assertTrue(
            next(
                item
                for item in required_music.items
                if item.key == "music"
            ).required
        )

        SettingsStore(self.root).update_fields(
            required_features={"music": False}
        )
        optional_music = evaluate_readiness(self.root)
        self.assertTrue(optional_music.can_preview)
        self.assertTrue(optional_music.can_publish)

    def test_published_url_normalization_matrix(self) -> None:
        valid = {
            "https://example.test/letter": "https://example.test/letter",
            "http://example.test": "http://example.test",
            "  https://example.test/a?q=1#page  ":
                "https://example.test/a?q=1#page",
        }
        for raw, expected in valid.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    normalize_published_page_url(raw),
                    expected,
                )

        invalid = (
            "example.test/no-scheme",
            "file:///tmp/letter.html",
            "ftp://example.test/letter",
            "https://",
            "https://bad host.test",
            "https://-bad.example",
            "https://example..test",
            r"C:\letters\index.html",
            "https://999.999.999.999/",
            "https://example.test/%zz",
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_published_page_url(raw), "")

    def test_generation_validation_failure_preserves_previous_build(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        project_id = str(uuid.uuid4())
        SettingsStore(self.root).update_fields(project_id=project_id)
        previous = self.root / "output/Play" / project_id
        previous.mkdir(parents=True)
        (previous / "index.html").write_text("previous", encoding="utf-8")

        with mock.patch.object(
            generate,
            "validate_play_bundle",
            side_effect=RuntimeError("invalid staging"),
        ):
            with self.assertRaises(RuntimeError):
                generate.generate_play_bundle(str(self.root))

        self.assertEqual(
            (previous / "index.html").read_text(encoding="utf-8"),
            "previous",
        )

    def test_generation_commit_failure_rolls_back_previous_build(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        project_id = str(uuid.uuid4())
        SettingsStore(self.root).update_fields(project_id=project_id)
        previous = self.root / "output/Play" / project_id
        previous.mkdir(parents=True)
        (previous / "index.html").write_text("previous", encoding="utf-8")
        real_replace = os.replace
        failed = False

        def fail_staging_replace(source, destination):
            nonlocal failed
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                not failed
                and source_path.is_dir()
                and destination_path.resolve() == previous.resolve()
                and source_path.resolve() != previous.resolve()
            ):
                failed = True
                raise OSError("simulated commit failure")
            return real_replace(source, destination)

        with mock.patch(
            "transactional_io.os.replace",
            side_effect=fail_staging_replace,
        ):
            with self.assertRaises(OSError):
                generate.generate_play_bundle(str(self.root))

        self.assertEqual(
            (previous / "index.html").read_text(encoding="utf-8"),
            "previous",
        )
        self.assertTrue(failed)

    def test_path_transaction_retries_brief_windows_sharing_violation(
        self,
    ) -> None:
        final = self.root / "build"
        final.mkdir()
        (final / "state.txt").write_text("old", encoding="utf-8")
        transaction = PathTransaction(final)
        staging = transaction.prepare()
        staging.mkdir()
        (staging / "state.txt").write_text("new", encoding="utf-8")
        real_replace = os.replace
        attempts = 0

        def briefly_locked(source, destination):
            nonlocal attempts
            if Path(source).resolve() == final.resolve() and attempts == 0:
                attempts += 1
                raise PermissionError("simulated viewer lock")
            return real_replace(source, destination)

        with mock.patch(
            "transactional_io.os.replace",
            side_effect=briefly_locked,
        ):
            transaction.commit()

        self.assertEqual(attempts, 1)
        self.assertEqual(
            (final / "state.txt").read_text(encoding="utf-8"),
            "new",
        )

    def test_abandoned_build_staging_is_cleaned(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        project_id = str(uuid.uuid4())
        SettingsStore(self.root).update_fields(project_id=project_id)
        play_root = self.root / "output/Play"
        abandoned = play_root / f"{project_id}.build-staging.abandoned"
        abandoned.mkdir(parents=True)
        old = time.time() - (48 * 60 * 60)
        os.utime(abandoned, (old, old))

        generate.generate_play_bundle(str(self.root))

        self.assertFalse(abandoned.exists())

    def test_silent_build_and_required_output_validation(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        result = generate.generate_play_bundle(str(self.root))
        manifest = json.loads(
            (
                result
                / "gallery/sounds"
                / BUILD_SOUND_MANIFEST_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["tracks"], [])
        index_html = (result / "index.html").read_text(encoding="utf-8")
        styles = (result / "styles.css").read_text(encoding="utf-8")
        script = (result / "script.js").read_text(encoding="utf-8")
        self.assertNotIn('id="progress"', index_html)
        self.assertNotIn("#progress", styles)
        self.assertNotIn("updateProgress", script)

        (result / "script.js").write_text(
            script + "\nfunction updateProgress() {}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "page counter"):
            generate.validate_play_bundle(result)
        (result / "script.js").write_text(script, encoding="utf-8")

        (result / "gallery/pages/back.png").unlink()
        with self.assertRaises(RuntimeError):
            generate.validate_play_bundle(result)

    def test_curtain_style_changes_generated_curtain_colors(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        pages = self.root / "gallery/user/pages"
        Image.new("RGB", (10, 10), (220, 25, 25)).save(pages / "wall.png")

        store = SettingsStore(self.root)
        pixels: dict[str, tuple[int, int, int]] = {}
        for style in (
            "pure_white",
            "average_color",
            "complementary_average_color",
        ):
            store.update_fields(curtain_style=style)
            result = generate.generate_play_bundle(str(self.root))
            with Image.open(result / "gallery/controls/cleft.png") as curtain:
                pixels[style] = curtain.convert("RGB").getpixel((2, 2))

        self.assertEqual(
            len(set(pixels.values())),
            3,
            pixels,
        )

    def test_preview_mode_does_not_invalidate_playable_build(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        first, rebuilt = generate.ensure_play_bundle(self.root)
        self.assertTrue(rebuilt)

        SettingsStore(self.root).update_fields(
            forge_preview_mode="landscape"
        )
        second, rebuilt = generate.ensure_play_bundle(self.root)

        self.assertEqual(first, second)
        self.assertFalse(rebuilt)

    def test_legacy_viewer_sound_sources_remain_buildable(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        legacy_sounds = self.root / "gallery/user/sounds"
        legacy_sounds.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(
            str(self.root / "gallery/app/sounds"),
            str(legacy_sounds),
        )

        result = generate.generate_play_bundle(str(self.root))

        for name in (GLISS_FILE, f"{FLIP_PREFIX}1.mp3"):
            self.assertTrue((result / "gallery/sounds" / name).is_file())

    def test_message_assets_and_fonts_are_rewritten_and_validated(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        message = self.root / "gallery/user/message"
        (message / "portrait.png").write_bytes(b"portrait")
        (message / "fonts").mkdir()
        (message / "fonts/letter.woff2").write_bytes(b"font")
        (message / "message.html").write_text(
            "<style>@font-face{src:url('fonts/letter.woff2')}</style>"
            "<p>Message</p><img src=\"portrait.png\">",
            encoding="utf-8",
        )

        result = generate.generate_play_bundle(str(self.root))
        index = (result / "index.html").read_text(encoding="utf-8")

        self.assertIn("gallery/message/portrait.png", index)
        self.assertIn("gallery/message/fonts/letter.woff2", index)
        self.assertTrue(
            (result / "gallery/message/portrait.png").is_file()
        )
        (result / "gallery/message/portrait.png").unlink()
        with self.assertRaises(RuntimeError):
            generate.validate_play_bundle(result)

    def test_sealed_status_requires_successful_publishing(self) -> None:
        _populate_required(self.root)
        _populate_runtime_assets(self.root)
        play_dir = generate.generate_play_bundle(str(self.root))
        readiness = evaluate_readiness(self.root)
        tab = ForgeTab(self.root)

        tab._publish_completed(
            (
                play_dir,
                readiness,
                {},
                PublishResult(False, message="Publishing failed."),
            )
        )
        self.assertNotEqual(
            tab.status.toPlainText(),
            "The letter has been sealed.",
        )

        tab._publish_completed(
            (
                play_dir,
                readiness,
                {},
                PublishResult(
                    True,
                    url="https://example.test/letter/",
                    public_path="letter-123",
                ),
            )
        )
        self.assertEqual(
            tab.status.toPlainText(),
            "The letter has been sealed.",
        )
        self.assertEqual(
            SettingsStore(self.root).get("published_page_url"),
            "https://example.test/letter/",
        )
        metadata = json.loads(
            (
                play_dir / "lettersmith-metadata.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["public_path"], "letter-123")
        tab.close()

    def test_restore_rejects_missing_required_page_without_mutation(self) -> None:
        _populate_required(self.root)
        before = _active_snapshot(self.root)
        entry = _saved_letter(self.root)
        (entry.path / "gallery/pages/back.png").unlink()

        with self.assertRaises(SavedLetterRestoreError):
            SavedLetterRestorer(self.root).restore(entry)

        self.assertEqual(_active_snapshot(self.root), before)

    def test_optional_invalid_sound_is_absent_but_required_sound_rejects(self) -> None:
        _populate_required(self.root)
        entry = _saved_letter(self.root)
        sounds = entry.path / "gallery/sounds"
        sounds.mkdir(parents=True)
        manifest = sounds / BUILD_SOUND_MANIFEST_NAME
        manifest.write_text("{invalid", encoding="utf-8")

        SavedLetterRestorer(self.root).restore(entry)
        self.assertEqual(resolve_project_tracks(self.root)[1], [])

        metadata_path = entry.path / "lettersmith-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["settings"]["required_features"] = {"music": True}
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        before = _active_snapshot(self.root)
        with self.assertRaises(SavedLetterRestoreError):
            SavedLetterRestorer(self.root).restore(entry)
        self.assertEqual(_active_snapshot(self.root), before)

    def test_restore_commit_failure_rolls_back_all_project_state(self) -> None:
        _populate_required(self.root)
        before = _active_snapshot(self.root)
        unrelated = self.root / "application-owned.txt"
        unrelated.write_text("unchanged", encoding="utf-8")
        entry = _saved_letter(self.root)
        real_commit = PathTransaction.commit
        calls = 0

        def fail_second_commit(transaction, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated restore commit failure")
            return real_commit(transaction, *args, **kwargs)

        with mock.patch.object(
            PathTransaction,
            "commit",
            new=fail_second_commit,
        ):
            with self.assertRaises(SavedLetterRestoreError):
                SavedLetterRestorer(self.root).restore(entry)

        self.assertEqual(_active_snapshot(self.root), before)
        self.assertEqual(
            unrelated.read_text(encoding="utf-8"),
            "unchanged",
        )

    def test_restore_preserves_url_playlist_mode_and_order(self) -> None:
        _populate_required(self.root)
        entry = _saved_letter(self.root)
        _add_saved_tracks(entry, ("First", "Second"))
        saved_message = entry.path / "gallery/message"
        (saved_message / "portrait.png").write_bytes(b"embedded")
        (saved_message / "fonts").mkdir()
        (saved_message / "fonts/letter.woff2").write_bytes(b"font")
        (saved_message / "message.html").write_text(
            '<p>Saved</p><img src="portrait.png">',
            encoding="utf-8",
        )

        restored = SavedLetterRestorer(self.root).restore(entry)
        state, tracks = resolve_project_tracks(self.root)

        self.assertEqual(restored.published_url, "https://example.test/saved/")
        self.assertEqual(state.mode, "playlist")
        self.assertEqual(
            [track.display_title for track in tracks],
            ["First", "Second"],
        )
        self.assertEqual(
            (
                self.root
                / "gallery/user/message/portrait.png"
            ).read_bytes(),
            b"embedded",
        )
        self.assertTrue(
            (
                self.root
                / "gallery/user/message/fonts/letter.woff2"
            ).is_file()
        )

    def test_legacy_single_track_restore(self) -> None:
        _populate_required(self.root)
        entry = _saved_letter(self.root)
        sounds = entry.path / "gallery/sounds"
        sounds.mkdir(parents=True)
        (sounds / "music.mp3").write_bytes(b"legacy music")

        SavedLetterRestorer(self.root).restore(entry)
        state, tracks = resolve_project_tracks(self.root)

        self.assertEqual(state.mode, "single")
        self.assertEqual(len(tracks), 1)

    def test_restore_rejects_paths_outside_saved_roots(self) -> None:
        _populate_required(self.root)
        outside = _catalog_build(
            self.root,
            "outside/letter",
            html_title="Outside",
        )
        entry = SavedLetter(
            path=outside,
            recipient="Outside",
            title="Outside",
            modified_at=datetime.fromtimestamp(outside.stat().st_mtime),
            published_url="",
            cover_path=outside / "gallery/pages/cover.png",
        )
        before = _active_snapshot(self.root)

        with self.assertRaises(SavedLetterRestoreError):
            SavedLetterRestorer(self.root).restore(entry)

        self.assertEqual(_active_snapshot(self.root), before)

    def test_restore_rejects_explicit_path_traversal(self) -> None:
        _populate_required(self.root)
        entry = _saved_letter(self.root)
        traversing_path = entry.path.parent / "unused" / ".." / entry.path.name
        traversing = SavedLetter(
            path=traversing_path,
            recipient=entry.recipient,
            title=entry.title,
            modified_at=entry.modified_at,
            published_url=entry.published_url,
            cover_path=entry.cover_path,
        )
        before = _active_snapshot(self.root)

        with self.assertRaises(SavedLetterRestoreError):
            SavedLetterRestorer(self.root).restore(traversing)

        self.assertEqual(_active_snapshot(self.root), before)

    def test_catalog_metadata_legacy_recovery_sorting_and_filtering(self) -> None:
        metadata_build = _catalog_build(
            self.root,
            f"output/Play/{uuid.uuid4()}",
            html_title="HTML Ignored",
            metadata={
                "recipient_name": "Metadata Recipient",
                "recipient_title": "Metadata Title",
                "published_page_url": "https://example.test/published",
            },
        )
        legacy_build = _catalog_build(
            self.root,
            "output/Play/Legacy Recipient/Legacy Folder",
            html_title="HTML Fallback Title",
        )
        legacy_user = legacy_build / "gallery/user"
        legacy_user.mkdir()
        shutil.move(
            str(legacy_build / "gallery/pages"),
            str(legacy_user / "pages"),
        )
        shutil.move(
            str(legacy_build / "gallery/message"),
            str(legacy_user / "message"),
        )
        (legacy_user / "card").mkdir()
        shutil.move(
            str(legacy_build / "gallery/controls"),
            str(legacy_user / "card/controls"),
        )
        recovery_build = _catalog_build(
            self.root,
            "output/Recovery/recovered",
            html_title="Recovered Title",
        )
        invalid = self.root / "output/Play/not-a-letter"
        invalid.mkdir(parents=True)
        (invalid / "index.html").write_text("<title>Invalid</title>")
        now = time.time()
        os.utime(metadata_build, (now - 30, now - 30))
        os.utime(legacy_build, (now - 20, now - 20))
        os.utime(recovery_build, (now - 10, now - 10))

        entries = SavedLetterCatalog(self.root).list_entries()
        paths = [entry.path for entry in entries]
        by_path = {entry.path: entry for entry in entries}

        self.assertEqual(paths[0], recovery_build.resolve())
        self.assertIn(metadata_build.resolve(), paths)
        self.assertIn(legacy_build.resolve(), paths)
        self.assertNotIn(invalid.resolve(), paths)
        metadata_entry = by_path[metadata_build.resolve()]
        self.assertEqual(metadata_entry.title, "Metadata Title")
        self.assertEqual(metadata_entry.recipient, "Metadata Recipient")
        self.assertTrue(metadata_entry.published)
        self.assertEqual(
            by_path[legacy_build.resolve()].title,
            "HTML Fallback Title",
        )
        self.assertTrue(by_path[recovery_build.resolve()].recovery)


if __name__ == "__main__":
    unittest.main()
