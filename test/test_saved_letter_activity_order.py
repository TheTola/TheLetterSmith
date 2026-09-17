from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

import Forge_Tab as forge_module
from Forge_Tab import ForgeTab, SavedLetterCard
from config import CONTROL_FILES, PLAY_METADATA_FILE, REQUIRED_SLIDES
from readiness import ReadinessResult
from recipient_registry import RecipientRegistry
from project_state import ProjectStateController
from project_timestamps import (
    LEGACY_PROJECT_DATE,
    PROJECT_CREATED_AT_KEY,
    PROJECT_PUBLISHED_AT_KEY,
    parse_project_timestamp,
)
from settings_store import SettingsStore, empty_publication_settings
from transactional_io import set_path_hidden
from saved_letters import (
    LAST_ACTIVITY_AT_KEY,
    PROMPT_WRITER_STATE_FILE,
    SavedLetter,
    SavedLetterCatalog,
    SavedLetterDeleteError,
    SavedLetterRestorer,
    record_saved_letter_activity,
    update_saved_metadata,
    update_saved_publication_metadata,
)
from protected_projects import (
    EXAMPLE_PROJECT_KIND,
    PROTECTED_PROJECT_KIND_KEY,
    PROTECTED_PROJECT_MASTER_PATH_KEY,
    STOCK_PROJECT_KIND,
)


def _saved_bundle(
    root: Path,
    name: str,
    *,
    recipient: str = "Recipient",
    created_at: str = "",
    project_published_at: str = "",
) -> Path:
    bundle = root / "output" / "Play" / "Recipient" / name
    pages = bundle / "gallery" / "pages"
    message = bundle / "gallery" / "message"
    controls = bundle / "gallery" / "controls"
    sounds = bundle / "gallery" / "sounds"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    controls.mkdir(parents=True)
    sounds.mkdir(parents=True)
    for filename in REQUIRED_SLIDES:
        (pages / filename).write_bytes(b"image")
    (pages / "lettersmith-images.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slots": {
                    filename.removesuffix(".png"): {
                        "asset_type": "static",
                        "is_animated_gif": False,
                        "source_file": filename,
                        "preview_file": filename,
                    }
                    for filename in REQUIRED_SLIDES
                },
            }
        ),
        encoding="utf-8",
    )
    for filename in CONTROL_FILES:
        (controls / filename).write_bytes(b"control")
    (message / "message.html").write_text("<p>Letter</p>", encoding="utf-8")
    (bundle / "index.html").write_text(
        f"<html><title>{name}</title></html>",
        encoding="utf-8",
    )
    (bundle / "styles.css").write_text("", encoding="utf-8")
    (bundle / "script.js").write_text("", encoding="utf-8")
    (sounds / "lettersmith-sound.json").write_text(
        json.dumps(
            {
                "version": 2,
                "mode": "single",
                "playlist_expanded": True,
                "selected_track_index": -1,
                "tracks": [],
            }
        ),
        encoding="utf-8",
    )
    (bundle / PROMPT_WRITER_STATE_FILE).write_text("{}", encoding="utf-8")
    recipient_record = RecipientRegistry(root).get_or_create(recipient)
    metadata = {
        "schema_version": "1.0",
        "document_type": "saved_letter",
        "project_id": str(uuid.uuid4()),
        "recipient_id": recipient_record.recipient_id,
        "recipient_name": recipient,
        "recipient_title": name,
        "settings": {},
        "sound": {},
        "readiness": {},
        "editable_assets": {
            "pages": {
                filename: f"gallery/pages/{filename}"
                for filename in REQUIRED_SLIDES
            },
            "message": "gallery/message/message.html",
            "sound_manifest": "gallery/sounds/lettersmith-sound.json",
            "prompt_writer_state": PROMPT_WRITER_STATE_FILE,
            "image_manifest": "gallery/pages/lettersmith-images.json",
        },
        "cover_thumbnail_path": "gallery/pages/cover.png",
    }
    if created_at:
        metadata[PROJECT_CREATED_AT_KEY] = created_at
    if project_published_at:
        metadata[PROJECT_PUBLISHED_AT_KEY] = project_published_at
    (bundle / PLAY_METADATA_FILE).write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    return bundle


class SavedLetterActivityOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _wait_for_catalog_refresh(
        self,
        tab: ForgeTab,
        *,
        timeout: float = 5.0,
    ) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.app.processEvents()
            if (
                not tab._catalog_reconcile_active
                and not tab._catalog_refresh_queued
            ):
                return
            QtCore.QThread.msleep(5)
        self.fail("saved-letter catalog reconciliation did not finish")

    def test_stock_letter_card_uses_lowercase_purple_published_badge(self) -> None:
        entry = SavedLetter(
            path=Path("Stock Letter 1"),
            recipient="Example Recipient",
            title="Stock Letter 1",
            modified_at=datetime.now(),
            published_url="",
            cover_path=None,
            stock=True,
        )
        card = SavedLetterCard(entry)
        self.addCleanup(card.deleteLater)

        self.assertEqual(card.status_label.text(), "published")
        self.assertEqual(card.status_label.objectName(), "StockStatus")
        self.assertIn(
            "QLabel#StockStatus{color:#e6d4ff;background:#271b38;",
            card.styleSheet(),
        )

    def test_created_and_published_dates_control_order_not_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(timezone.utc)
            first = _saved_bundle(
                root,
                "First Letter",
                created_at=(now - timedelta(days=2)).isoformat(),
            )
            second = _saved_bundle(
                root,
                "Second Letter",
                created_at=(now - timedelta(days=1)).isoformat(),
            )
            record_saved_letter_activity(first, when=now - timedelta(days=2))
            record_saved_letter_activity(second, when=now - timedelta(days=1))

            # Directory mtimes deliberately disagree with explicit activity.
            future = (now + timedelta(days=3)).timestamp()
            os.utime(first, (future, future))
            catalog = SavedLetterCatalog(root)
            entries = catalog.list_entries()
            self.assertEqual(entries[0].title, "Second Letter")

            activity = record_saved_letter_activity(
                first,
                when=now + timedelta(days=3),
            )
            entries = catalog.refresh_entry(first)
            self.assertIsNotNone(entries)
            self.assertEqual(entries[0].title, "Second Letter")

            metadata_path = first / PLAY_METADATA_FILE
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata[PROJECT_PUBLISHED_AT_KEY] = now.isoformat()
            set_path_hidden(metadata_path, False)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            entries = catalog.refresh_entry(first)
            self.assertIsNotNone(entries)
            self.assertEqual(entries[0].title, "First Letter")
            metadata = json.loads(
                (first / PLAY_METADATA_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(metadata[LAST_ACTIVITY_AT_KEY], activity)

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
            with mock.patch.object(
                forge_module.generate, "play_bundle_directory", return_value=bundle
            ), mock.patch.object(tab, "_update_metadata_silently") as update:
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
                    created_at=(now + timedelta(days=index)).isoformat(),
                )
                record_saved_letter_activity(
                    bundle,
                    when=now + timedelta(minutes=index),
                )

            tab = ForgeTab(root)
            tab.refresh_saved_letters()
            self._wait_for_catalog_refresh(tab)
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

    def test_new_project_gets_a_full_created_timestamp_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = ProjectStateController(root)

            controller.begin_new_project()

            timestamp = SettingsStore(root).get(PROJECT_CREATED_AT_KEY, "")
            parsed = parse_project_timestamp(timestamp)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed.microsecond, 0)
            self.assertEqual(
                SettingsStore(root).get(PROJECT_PUBLISHED_AT_KEY, ""),
                "",
            )

    def test_legacy_project_uses_2025_until_first_successful_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _saved_bundle(root, "Legacy Letter")
            catalog = SavedLetterCatalog(root)
            entry = catalog.list_entries(force_refresh=True)[0]
            self.assertEqual(entry.created_sort_date, LEGACY_PROJECT_DATE)
            self.assertEqual(entry.project_created_at, "")

            SavedLetterRestorer(root).restore(entry)

            metadata = json.loads(
                (bundle / PLAY_METADATA_FILE).read_text(encoding="utf-8")
            )
            migrated = metadata.get(PROJECT_CREATED_AT_KEY, "")
            self.assertIsNotNone(parse_project_timestamp(migrated))
            self.assertEqual(
                SettingsStore(root).get(PROJECT_CREATED_AT_KEY, ""),
                migrated,
            )
            refreshed = catalog.refresh_entry(bundle)
            self.assertIsNotNone(refreshed)
            self.assertNotEqual(refreshed[0].created_sort_date, LEGACY_PROJECT_DATE)

    def test_saved_sort_uses_calendar_date_then_alphabetical_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(
                root,
                "Zeta",
                created_at="2026-08-28T01:02:03+00:00",
            )
            _saved_bundle(
                root,
                "Alpha",
                created_at="2026-08-28T23:59:58+00:00",
            )
            _saved_bundle(
                root,
                "Published Later",
                created_at="2020-01-01T10:00:00+00:00",
                project_published_at="2026-08-29T00:00:01+00:00",
            )

            entries = SavedLetterCatalog(root).list_entries(force_refresh=True)

            self.assertEqual(
                [entry.title for entry in entries],
                ["Published Later", "Alpha", "Zeta"],
            )

    def test_last_published_timestamp_survives_unpublish_metadata_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _saved_bundle(
                root,
                "Published Letter",
                created_at="2026-08-20T12:00:00+00:00",
            )
            published = "2026-08-28T16:42:17+00:00"
            settings = SettingsStore(root)
            settings.update_fields(
                {
                    PROJECT_PUBLISHED_AT_KEY: published,
                    "published_at": published,
                }
            )
            update_saved_publication_metadata(bundle, root)
            settings.update_fields(empty_publication_settings())

            metadata = update_saved_publication_metadata(bundle, root)

            self.assertEqual(metadata[PROJECT_PUBLISHED_AT_KEY], published)
            self.assertEqual(metadata["published_at"], "")

    def test_archive_recipients_use_earliest_created_date_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(
                root,
                "Johanna Current",
                recipient="Johanna",
                created_at="2032-01-01T12:00:00+00:00",
            )
            for index in range(14):
                _saved_bundle(
                    root,
                    f"Recent {index:02d}",
                    recipient=f"Recent {index:02d}",
                    created_at=f"2031-12-{index + 1:02d}T12:00:00+00:00",
                )
            _saved_bundle(
                root,
                "Sarah First",
                recipient="Sarah",
                created_at="2026-01-01T12:00:00+00:00",
            )
            _saved_bundle(
                root,
                "Michael First",
                recipient="Michael",
                created_at="2024-01-01T12:00:00+00:00",
            )
            _saved_bundle(
                root,
                "Johanna First",
                recipient="Johanna",
                created_at="2020-01-01T12:00:00+00:00",
            )

            tab = ForgeTab(root)
            tab.refresh_saved_letters(force_reconcile=True)
            self._wait_for_catalog_refresh(tab)
            recipients = [
                tab.saved_archive_recipient.itemText(index)
                for index in range(1, tab.saved_archive_recipient.count())
            ]
            self.assertEqual(recipients, ["Sarah", "Michael", "Johanna"])
            tab.close()

    def test_saved_letter_refresh_retains_unchanged_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _saved_bundle(root, "First Letter")
            second = _saved_bundle(root, "Second Letter")
            tab = ForgeTab(root)
            tab.refresh_saved_letters(force_reconcile=True)
            self._wait_for_catalog_refresh(tab)
            original_cards = {
                card.entry.path: card
                for card in tab._saved_cards
            }

            record_saved_letter_activity(first)
            tab.refresh_saved_letters(force_reconcile=True)
            self._wait_for_catalog_refresh(tab)

            refreshed_cards = {
                card.entry.path: card
                for card in tab._saved_cards
            }
            self.assertIs(refreshed_cards[first.resolve()], original_cards[first.resolve()])
            self.assertIs(refreshed_cards[second.resolve()], original_cards[second.resolve()])
            tab.close()

    def test_saved_card_cover_decode_does_not_block_gui_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _saved_bundle(root, "Large Cover Letter")
            cover_path = bundle / "gallery" / "pages" / "cover.png"
            cover = QtGui.QImage(2400, 1600, QtGui.QImage.Format_RGB32)
            cover.fill(QtGui.QColor("#147a91"))
            self.assertTrue(cover.save(str(cover_path), "PNG"))
            forge_module._COVER_PIXMAP_CACHE.clear()

            decode_started = threading.Event()
            decode_release = threading.Event()
            decoded_on_gui: list[bool] = []
            original_decode = forge_module._decode_scaled_cover_image

            def blocked_decode(*args, **kwargs):
                decoded_on_gui.append(
                    QtCore.QThread.currentThread() is self.app.thread()
                )
                decode_started.set()
                decode_release.wait(1.0)
                return original_decode(*args, **kwargs)

            with mock.patch.object(
                ForgeTab,
                "_validate_github_account_async",
            ):
                tab = ForgeTab(root)
            try:
                tab.catalog.list_entries(force_refresh=True)
                with mock.patch.object(
                    forge_module,
                    "_decode_scaled_cover_image",
                    side_effect=blocked_decode,
                ):
                    started_at = time.perf_counter()
                    tab.refresh_saved_letters()
                    refresh_elapsed = time.perf_counter() - started_at
                    self.assertTrue(decode_started.wait(0.5))
                    self.assertLess(refresh_elapsed, 0.25)
                    self.assertEqual(decoded_on_gui, [False])
                    decode_release.set()

                    deadline = time.perf_counter() + 2.0
                    while time.perf_counter() < deadline:
                        self.app.processEvents()
                        pixmap = tab._saved_cards[0].cover.pixmap()
                        if pixmap is not None and not pixmap.isNull():
                            break
                        QtCore.QThread.msleep(5)
                    else:
                        self.fail("decoded cover was not applied to its card")
                self.assertLessEqual(pixmap.width(), 168)
                self.assertLessEqual(pixmap.height(), 92)
            finally:
                decode_release.set()
                tab.close()

    def test_missing_catalog_index_reconciliation_does_not_block_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root, "Indexed Letter")
            catalog = SavedLetterCatalog(root)
            entries = catalog.list_entries(force_refresh=True)
            catalog.index_path.unlink()
            reconcile_started = threading.Event()
            reconcile_release = threading.Event()
            reconciled_on_gui: list[bool] = []

            with mock.patch.object(
                ForgeTab,
                "_validate_github_account_async",
            ):
                tab = ForgeTab(root)

            def blocked_reconcile(_catalog, *, force_refresh=False):
                self.assertTrue(force_refresh)
                reconciled_on_gui.append(
                    QtCore.QThread.currentThread() is self.app.thread()
                )
                reconcile_started.set()
                reconcile_release.wait(1.0)
                return entries

            try:
                with mock.patch.object(
                    SavedLetterCatalog,
                    "list_entries",
                    autospec=True,
                    side_effect=blocked_reconcile,
                ):
                    started_at = time.perf_counter()
                    tab.refresh_saved_letters()
                    refresh_elapsed = time.perf_counter() - started_at
                    self.assertTrue(reconcile_started.wait(0.5))
                    self.assertLess(refresh_elapsed, 0.25)
                    self.assertEqual(reconciled_on_gui, [False])
                    reconcile_release.set()
                    self._wait_for_catalog_refresh(tab)
                self.assertEqual(len(tab._saved_cards), 1)
            finally:
                reconcile_release.set()
                tab.close()

    def test_forge_loads_valid_persistent_catalog_without_worker_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root, "Indexed Letter")
            SavedLetterCatalog(root).list_entries(force_refresh=True)
            with mock.patch.object(
                ForgeTab,
                "_validate_github_account_async",
            ):
                tab = ForgeTab(root)
            try:
                tab.refresh_saved_letters()
                self.assertFalse(tab._catalog_reconcile_active)
                self.assertEqual(len(tab._saved_cards), 1)
            finally:
                tab.close()

    def test_opening_saved_letters_reconciles_stale_catalog_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(4):
                _saved_bundle(root, f"Letter {index:02d}")
            SavedLetterCatalog(root).list_entries(force_refresh=True)
            for index in range(4, 18):
                _saved_bundle(root, f"Letter {index:02d}")

            with mock.patch.object(
                ForgeTab,
                "_validate_github_account_async",
            ):
                tab = ForgeTab(root)
            try:
                tab.show_saved_letters()
                self._wait_for_catalog_refresh(tab)
                self.assertEqual(len(tab._saved_cards), 15)
                self.assertFalse(tab.saved_archive.isHidden())
                self.assertEqual(tab.saved_archive_label.text(), "Archive (3)")
            finally:
                tab.close()

    def test_catalog_reopens_from_persistent_index_without_rescanning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _saved_bundle(root, "First Letter")
            recovery_source = _saved_bundle(root, "Recovery Letter")
            recovery = (
                root
                / "output"
                / "Recovery"
                / "Recipient"
                / "Recovery Letter"
            )
            recovery.parent.mkdir(parents=True)
            shutil.move(str(recovery_source), str(recovery))
            example_source = _saved_bundle(root, "Example Letter")
            example = root / "resources" / "examples" / "example_letter"
            example.parent.mkdir(parents=True)
            shutil.move(str(example_source), str(example))
            catalog = SavedLetterCatalog(root)
            expected = catalog.list_entries(force_refresh=True)
            self.assertTrue(catalog.index_path.is_file())

            with (
                mock.patch.object(
                    Path,
                    "rglob",
                    side_effect=AssertionError("catalog was rescanned"),
                ),
                mock.patch.object(
                    SavedLetterRestorer,
                    "_validated_saved_letter_content",
                    side_effect=AssertionError("build was revalidated"),
                ),
            ):
                reopened = SavedLetterCatalog(root).list_entries()

            self.assertEqual(reopened, expected)
            by_path = {entry.path: entry for entry in reopened}
            self.assertIn(first.resolve(), by_path)
            self.assertTrue(by_path[recovery.resolve()].recovery)
            self.assertTrue(by_path[example.resolve()].example)

    def test_catalog_incrementally_persists_add_edit_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _saved_bundle(root, "First Letter")
            catalog = SavedLetterCatalog(root)
            catalog.list_entries(force_refresh=True)

            second = _saved_bundle(root, "Second Letter")
            refreshed = catalog.refresh_entry(second)
            self.assertIsNotNone(refreshed)
            self.assertEqual(len(refreshed or ()), 2)

            metadata_path = first / PLAY_METADATA_FILE
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["recipient_title"] = "Edited Letter"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            catalog.refresh_entry(first)

            with mock.patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("catalog was rescanned"),
            ):
                reopened_catalog = SavedLetterCatalog(root)
                reopened = reopened_catalog.list_entries()
            self.assertEqual(
                {entry.title for entry in reopened},
                {"Edited Letter", "Second Letter"},
            )

            second_entry = next(
                entry for entry in reopened if entry.path == second.resolve()
            )
            reopened_catalog.delete(second_entry)
            with mock.patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("catalog was rescanned"),
            ):
                after_delete = SavedLetterCatalog(root).list_entries()
            self.assertEqual(
                [entry.title for entry in after_delete],
                ["Edited Letter"],
            )

    def test_force_refresh_reconciles_external_catalog_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root, "First Letter")
            SavedLetterCatalog(root).list_entries(force_refresh=True)
            _saved_bundle(root, "External Letter")

            catalog = SavedLetterCatalog(root)
            self.assertEqual(len(catalog.list_entries()), 1)
            self.assertEqual(len(catalog.list_entries(force_refresh=True)), 2)
            self.assertEqual(len(SavedLetterCatalog(root).list_entries()), 2)

            catalog.index_path.write_text("{", encoding="utf-8")
            with mock.patch("saved_letters._LOGGER.warning"):
                rebuilt = SavedLetterCatalog(root).list_entries()
            self.assertEqual(len(rebuilt), 2)

    def test_old_catalog_schema_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _saved_bundle(root, "First Letter")
            catalog = SavedLetterCatalog(root)
            catalog.list_entries(force_refresh=True)
            payload = json.loads(catalog.index_path.read_text(encoding="utf-8"))
            payload["schema_version"] = 1
            catalog.index_path.write_text(json.dumps(payload), encoding="utf-8")
            _saved_bundle(root, "Second Letter")

            reopened = SavedLetterCatalog(root)
            self.assertIsNone(reopened.load_persisted_entries())
            self.assertEqual(len(reopened.list_entries()), 2)

    def test_targeted_refresh_does_not_hide_pending_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _saved_bundle(root, "First Letter")
            catalog = SavedLetterCatalog(root)
            catalog.list_entries(force_refresh=True)
            _saved_bundle(root, "External Letter")
            catalog.invalidate()

            self.assertIsNone(catalog.refresh_entry(first))
            self.assertEqual(len(catalog.list_entries()), 2)

    def test_stock_menu_lists_only_stock_letters_and_restores_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = _saved_bundle(root, "Saved Letter")
            stock_source = _saved_bundle(root, "Stock Letter")
            stock = root / "resources" / "stock" / "letters" / "Stock Letter"
            stock.parent.mkdir(parents=True)
            shutil.move(str(stock_source), str(stock))
            metadata_path = stock / PLAY_METADATA_FILE
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["recipient_title"] = "Stock: Letter"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            saved_entries = SavedLetterCatalog(root).list_entries()
            self.assertEqual(
                [entry.path for entry in saved_entries],
                [saved.resolve()],
            )

            stock_catalog = SavedLetterCatalog(root, stock_only=True)
            stock_entries = stock_catalog.list_entries()
            self.assertEqual(len(stock_entries), 1)
            self.assertEqual(stock_entries[0].path, stock.resolve())
            with self.assertRaisesRegex(
                SavedLetterDeleteError,
                "Stock letters cannot be deleted",
            ):
                stock_catalog.delete(stock_entries[0])

            restored = SavedLetterRestorer(root).restore(stock_entries[0])
            self.assertEqual(restored.play_dir, stock.resolve())
            snapshot = SettingsStore(root).snapshot()
            self.assertEqual(
                snapshot[PROTECTED_PROJECT_KIND_KEY],
                STOCK_PROJECT_KIND,
            )
            self.assertEqual(
                snapshot[PROTECTED_PROJECT_MASTER_PATH_KEY],
                str(stock.resolve()),
            )
            self.assertEqual(snapshot["active_play_dir"], "")

            tab = ForgeTab(root)
            tab.show_stock_letters()
            self._wait_for_catalog_refresh(tab)
            self.assertEqual(tab.saved_heading.text(), "Stock Letters")
            self.assertEqual(len(tab._saved_cards), 1)
            self.assertEqual(tab._saved_cards[0].entry.path, stock.resolve())
            self.assertTrue(tab.saved_delete_toggle.isHidden())
            tab.saved_panel.hide()
            tab.close()

    def test_stock_letters_are_sorted_by_number_not_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock_root = root / "resources" / "stock" / "letters"
            now = datetime.now(timezone.utc)
            activity_offsets = {1: 3, 2: 1, 3: 2}
            for number in (1, 2, 3):
                source = _saved_bundle(root, f"Stock Letter {number}")
                stock = stock_root / f"Stock Letter {number}"
                stock.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(stock))
                metadata_path = stock / PLAY_METADATA_FILE
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                metadata["recipient_title"] = f"Stock: Letter {number}"
                metadata_path.write_text(
                    json.dumps(metadata),
                    encoding="utf-8",
                )
                record_saved_letter_activity(
                    stock,
                    when=now
                    + timedelta(minutes=activity_offsets[number]),
                )

            entries = SavedLetterCatalog(root, stock_only=True).list_entries()
            self.assertEqual(
                [entry.title for entry in entries],
                ["Stock: Letter 1", "Stock: Letter 2", "Stock: Letter 3"],
            )

    def test_cancelled_github_sign_in_is_not_logged_as_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = ForgeTab(Path(directory))
            tab._github_sign_in_cancelled = True
            tab._operation_failure = None
            tab._operation_error_message = "GitHub sign-in failed."
            with (
                mock.patch("Forge_Tab._LOGGER.error") as log_error,
                mock.patch.object(tab, "_set_status") as set_status,
                mock.patch.object(tab, "request_preview") as request_preview,
            ):
                tab._operation_failed_on_ui(
                    "GitHub sign-in was canceled.",
                    "expected cancellation traceback",
                    True,
                )

            log_error.assert_not_called()
            set_status.assert_called_once_with("GitHub sign-in canceled.")
            request_preview.assert_called_once_with()
            self.assertFalse(tab._github_sign_in_cancelled)
            tab.close()

    def test_bundled_example_is_first_read_only_and_restores_from_a_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _saved_bundle(root, "Example Letter", recipient="A Friend")
            regular = _saved_bundle(root, "Newest Letter", recipient="Davis")
            example = root / "resources" / "examples" / "example_letter"
            example.parent.mkdir(parents=True)
            shutil.move(str(source), str(example))
            metadata_path = example / PLAY_METADATA_FILE
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "example_master": True,
                }
            )
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            (example / "prompt_writer_state.json").write_text(
                json.dumps({"version": 6}),
                encoding="utf-8",
            )
            original_metadata = metadata_path.read_bytes()
            future = datetime.now(timezone.utc) + timedelta(days=10)
            record_saved_letter_activity(regular, when=future)

            catalog = SavedLetterCatalog(root)
            entries = catalog.list_entries()
            self.assertEqual(len(entries), 2)
            self.assertTrue(entries[0].example)
            self.assertEqual(entries[0].title, "Example Letter")
            with self.assertRaises(SavedLetterDeleteError):
                catalog.delete(entries[0])

            restored = SavedLetterRestorer(root).restore(entries[0])
            self.assertEqual(restored.play_dir, example.resolve())
            self.assertTrue(restored.project_id)
            self.assertEqual(metadata_path.read_bytes(), original_metadata)
            snapshot = SettingsStore(root).snapshot()
            self.assertEqual(
                snapshot[PROTECTED_PROJECT_KIND_KEY],
                EXAMPLE_PROJECT_KIND,
            )

            readiness = ReadinessResult((), 0, "")
            self.assertEqual(
                update_saved_metadata(example, root, readiness),
                metadata,
            )
            self.assertEqual(
                update_saved_publication_metadata(example, root),
                metadata,
            )
            self.assertEqual(record_saved_letter_activity(example), "")
            self.assertEqual(metadata_path.read_bytes(), original_metadata)


if __name__ == "__main__":
    unittest.main()
