from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import Forge_Tab
import generate
from Forge_Tab import ForgeTab
from config import CONTROL_FILES, FLIP_COUNT, FLIP_PREFIX, GLISS_FILE, REQUIRED_SLIDES
from readiness import evaluate_readiness
from saved_letters import SavedLetter
from settings_store import SettingsStore
from sound_model import (
    ProjectSoundState,
    import_runtime_track,
    save_project_state,
)


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
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    for name in REQUIRED_SLIDES:
        (pages / name).write_bytes(f"saved-{name}".encode())
    (message / "message.html").write_text("<p>Saved</p>", encoding="utf-8")
    (play_dir / "index.html").write_text(
        "<html><title>Saved Title</title></html>",
        encoding="utf-8",
    )
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
        (controls / name).write_bytes(b"control")
    app_sounds = root / "gallery/app/sounds"
    app_sounds.mkdir(parents=True)
    (app_sounds / GLISS_FILE).write_bytes(b"sound")
    for index in range(1, FLIP_COUNT + 1):
        (app_sounds / f"{FLIP_PREFIX}{index}.mp3").write_bytes(b"sound")


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
        visible = {
            key
            for key, button in tab.readiness_window._missing_buttons.items()
            if not button.isHidden()
        }
        self.assertEqual(visible, {"music", "published_url"})
        tab.close()

    def test_saved_letter_load_is_transactional(self) -> None:
        _populate_required(self.root)
        original_pages = self.root / "gallery/user/pages"
        original_message = self.root / "gallery/user/message/message.html"
        original_cover = (original_pages / "cover.png").read_bytes()
        original_html = original_message.read_text(encoding="utf-8")
        entry = _saved_letter(self.root)
        tab = ForgeTab(self.root)

        with mock.patch.object(
            Forge_Tab,
            "atomic_write_settings",
            side_effect=OSError("simulated settings failure"),
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
            "current project was preserved",
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
        self.assertFalse(result.with_name(result.name + ".build-staging").exists())
        self.assertFalse(result.with_name(result.name + ".build-backup").exists())


if __name__ == "__main__":
    unittest.main()
