from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from Forge_Tab import ForgeTab
from config import CONTROL_FILES, PLAY_METADATA_FILE, REQUIRED_SLIDES
from readiness import ReadinessResult
from saved_letters import (
    LAST_ACTIVITY_AT_KEY,
    SavedLetterCatalog,
    record_saved_letter_activity,
)


def _saved_bundle(
    root: Path,
    name: str,
    *,
    recipient: str = "Recipient",
) -> Path:
    bundle = root / "output" / "Play" / "Recipient" / name
    pages = bundle / "gallery" / "pages"
    message = bundle / "gallery" / "message"
    controls = bundle / "gallery" / "controls"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    controls.mkdir(parents=True)
    for filename in REQUIRED_SLIDES:
        (pages / filename).write_bytes(b"image")
    for filename in CONTROL_FILES:
        (controls / filename).write_bytes(b"control")
    (message / "message.html").write_text("<p>Letter</p>", encoding="utf-8")
    (bundle / "index.html").write_text(
        f"<html><title>{name}</title></html>",
        encoding="utf-8",
    )
    (bundle / "styles.css").write_text("", encoding="utf-8")
    (bundle / "script.js").write_text("", encoding="utf-8")
    (bundle / PLAY_METADATA_FILE).write_text(
        json.dumps(
            {
                "recipient_name": recipient,
                "recipient_title": name,
            }
        ),
        encoding="utf-8",
    )
    return bundle


class SavedLetterActivityOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_explicit_activity_controls_order_and_updates_on_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _saved_bundle(root, "First Letter")
            second = _saved_bundle(root, "Second Letter")
            now = datetime.now(timezone.utc)
            record_saved_letter_activity(first, when=now - timedelta(days=2))
            record_saved_letter_activity(second, when=now - timedelta(days=1))

            # Directory mtimes deliberately disagree with explicit activity.
            future = (now + timedelta(days=3)).timestamp()
            os.utime(first, (future, future))
            entries = SavedLetterCatalog(root).list_entries()
            self.assertEqual(entries[0].title, "Second Letter")

            timestamp = record_saved_letter_activity(
                first,
                when=now,
            )
            entries = SavedLetterCatalog(root).list_entries()
            self.assertEqual(entries[0].title, "First Letter")
            metadata = json.loads(
                (first / PLAY_METADATA_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(metadata[LAST_ACTIVITY_AT_KEY], timestamp)

    def test_explicit_preview_and_new_url_request_activity_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _saved_bundle(root, "Current Letter")
            tab = ForgeTab(root)
            readiness = ReadinessResult((), 100, "Ready")

            tab._finish_preview(
                (bundle, False, readiness),
                record_activity=True,
            )
            self.assertEqual(tab._pending_metadata_update[2], True)
            tab._metadata_timer.stop()
            tab._pending_metadata_update = None

            tab._last_play_dir = bundle
            tab.saved_page_url = "https://example.com/old"
            with mock.patch.object(tab, "_update_metadata_silently") as update:
                tab.set_saved_page_url("https://example.com/new")
                self.assertTrue(update.call_args.kwargs["record_activity"])
                update.reset_mock()
                tab.set_saved_page_url("https://example.com/new")
                update.assert_not_called()
            tab.close()

    def test_saved_letter_panel_archives_entries_after_latest_fifteen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(timezone.utc)
            for index in range(18):
                recipient = "Ada" if index % 2 == 0 else "Grace"
                bundle = _saved_bundle(
                    root,
                    f"Letter {index:02d}",
                    recipient=recipient,
                )
                record_saved_letter_activity(
                    bundle,
                    when=now + timedelta(minutes=index),
                )

            tab = ForgeTab(root)
            self.assertEqual(len(tab._saved_cards), 15)
            self.assertFalse(tab.saved_archive.isHidden())
            self.assertEqual(tab.saved_archive_label.text(), "Archive (3)")
            self.assertEqual(tab.saved_archive_recipient.count(), 3)

            ada_index = tab.saved_archive_recipient.findText("Ada")
            tab.saved_archive_recipient.setCurrentIndex(ada_index)
            archived_titles = [
                tab.saved_archive_list.item(row).text()
                for row in range(tab.saved_archive_list.count())
            ]
            self.assertEqual(archived_titles, ["Letter 02", "Letter 00"])
            tab.close()


if __name__ == "__main__":
    unittest.main()
