from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

import generate
import Editor as editor_module
from Editor import Editor, FindReplaceDialog, UltralinkDialog
from message_html import ultralink_message_from_href
from Forge_Tab import ForgeTab, ReadinessWindow
from config import (
    CONTROL_FILES,
    MESSAGE_ASSETS_DIR,
    REQUIRED_SLIDES,
    resolve_play_bundle_directory,
)
from protected_projects import (
    EXAMPLE_PROJECT_KIND,
    PROTECTED_PROJECT_MASTER_PATH_KEY,
    PROTECTED_PROJECT_KIND_KEY,
    STOCK_PROJECT_KIND,
)
from project_paths import ProjectPathError, ProjectPathResolver, application_paths
from project_save import ProjectNotReadyError, ProjectSaveService
from project_state import ProjectStateController
from readiness import (
    ReadinessResult,
    evaluate_project_save_eligibility,
    evaluate_readiness,
)
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from sound_model import (
    ProjectSoundState,
    TrackRecord,
    import_runtime_track,
    save_project_state,
)
from sound_tab import ArchiveDialog
from ui_theme import THEMES, ThemeService
from ui_sounds import UiSound


class FakeLanguageService:
    def __init__(
        self,
        issues=(),
        *,
        error: BaseException | None = None,
        issue_provider=None,
    ) -> None:
        self.issues = tuple(issues)
        self.error = error
        self.issue_provider = issue_provider
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.added_words: list[str] = []
        self.ignored_words: list[str] = []

    def get_issues(
        self,
        text: str,
        *,
        context: str,
        protected_terms=(),
    ):
        self.calls.append((text, context, tuple(protected_terms)))
        if self.error is not None:
            raise self.error
        if self.issue_provider is not None:
            issues = tuple(self.issue_provider(text))
        else:
            issues = self.issues
        ignored = {word.casefold() for word in self.ignored_words}
        return tuple(
            issue
            for issue in issues
            if str(getattr(issue, "text", "")).casefold() not in ignored
        )

    def add_to_dictionary(self, word: str) -> bool:
        self.added_words.append(word)
        return True

    def ignore_word(self, word: str) -> bool:
        self.ignored_words.append(word)
        return True


class ReadinessEditorAndProjectPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_failed_find_uses_letter_smith_cue_and_keeps_visual_status(self) -> None:
        host = QtWidgets.QWidget()
        host.editor = QtWidgets.QTextEdit(host)
        host.editor.setPlainText("hello world")
        dialog = FindReplaceDialog(host)
        try:
            dialog.find_input.setText("missing")
            with mock.patch.object(editor_module, "play_ui_sound") as play:
                self.assertFalse(dialog.find_next())
            play.assert_called_once_with(UiSound.ERROR)
            self.assertEqual(dialog.status_label.text(), "No matches found.")
        finally:
            dialog.close()
            host.close()

    def test_readiness_panel_is_frameless_tool_closed_only_by_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = ReadinessWindow(Path(directory))
            self.assertEqual(panel.windowTitle(), "")
            self.assertTrue(panel.isWindow())
            self.assertTrue(panel.windowFlags() & QtCore.Qt.Tool)
            self.assertTrue(panel.windowFlags() & QtCore.Qt.FramelessWindowHint)
            self.assertGreater(panel.maximumWidth(), 520)
            panel.refresh(evaluate_readiness(directory))
            self.assertIn(
                "text-align:center",
                panel._missing_buttons["recipient"].styleSheet(),
            )
            self.assertGreater(panel.height(), panel.minimumHeight())
            self.assertFalse(panel.isVisible())
            panel.show()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())
            correction_spy = QtTest.QSignalSpy(panel.correction_requested)
            panel._missing_buttons["recipient"].click()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())
            self.assertEqual(correction_spy.count(), 1)
            panel.show()
            self.app.processEvents()
            panel.close()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())
            panel.hide()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())
            panel.shutdown()

    def test_complete_readiness_closes_panel_and_preview_selector_is_bright(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = ForgeTab(Path(directory))
            self.assertEqual(tab.readiness_btn.text(), "Review")
            correction_spy = QtTest.QSignalSpy(tab.correction_requested)
            tab._readiness_requested = True
            tab.readiness_window.show()
            self.app.processEvents()
            tab.readiness_window._missing_buttons["cover"].click()
            self.app.processEvents()
            self.assertFalse(tab.readiness_window.isVisible())
            self.assertFalse(tab._readiness_requested)
            self.assertEqual(correction_spy.count(), 1)
            ready = ReadinessResult(
                items=(),
                completion_percentage=100,
                status="Ready",
            )
            tab.readiness_window.show()
            self.app.processEvents()
            with mock.patch("Forge_Tab.evaluate_readiness", return_value=ready):
                tab.refresh_readiness()
            self.assertFalse(tab.readiness_window.isVisible())

            with mock.patch.object(tab, "refresh_readiness", return_value=ready):
                tab.show_readiness_window()
            self.assertFalse(tab.readiness_window.isVisible())
            self.assertIn(
                THEMES["cyber_forge"].tokens.highlight,
                tab.preview_format_label.styleSheet(),
            )
            self.assertGreaterEqual(
                tab.preview_mode.itemDelegate().sizeHint(
                    QtWidgets.QStyleOptionViewItem(),
                    tab.preview_mode.model().index(0, 0),
                ).height(),
                48,
            )
            tab.close()

    def test_forge_builds_a_missing_preview_and_reuses_a_current_one(self) -> None:
        missing = SimpleNamespace(
            _busy=False,
            _preview_refresh_pending=True,
            _preview_refresh_requested=False,
            _refresh_source_fingerprint=mock.Mock(),
            refresh_readiness=mock.Mock(return_value=SimpleNamespace(can_preview=True)),
            _current_play_index=mock.Mock(return_value=None),
            _prepare_preview=mock.Mock(),
            request_preview=mock.Mock(),
        )

        ForgeTab.ensure_preview_current(missing)

        missing._prepare_preview.assert_called_once_with(open_in_browser=False)
        missing.request_preview.assert_not_called()

        current = SimpleNamespace(
            _busy=False,
            _preview_refresh_pending=False,
            _preview_refresh_requested=False,
            _refresh_source_fingerprint=mock.Mock(),
            refresh_readiness=mock.Mock(return_value=SimpleNamespace(can_preview=True)),
            _current_play_index=mock.Mock(return_value=Path("index.html")),
            _prepare_preview=mock.Mock(),
            request_preview=mock.Mock(),
        )

        ForgeTab.ensure_preview_current(current)

        current.request_preview.assert_called_once_with()
        current._prepare_preview.assert_not_called()

        incomplete = SimpleNamespace(
            _busy=False,
            _preview_refresh_pending=False,
            _refresh_source_fingerprint=mock.Mock(),
            refresh_readiness=mock.Mock(return_value=SimpleNamespace(can_preview=False)),
            _prepare_preview=mock.Mock(),
            request_preview=mock.Mock(),
        )
        ForgeTab.ensure_preview_current(incomplete)
        self.assertTrue(incomplete._preview_refresh_pending)
        incomplete._prepare_preview.assert_not_called()
        incomplete.request_preview.assert_not_called()

    def test_forge_preview_index_uses_working_bundle_after_saved_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "output/Play/letter"
            recovery = root / "output/Recovery/letter"
            working.mkdir(parents=True)
            recovery.mkdir(parents=True)
            (working / "index.html").write_text("working", encoding="utf-8")
            (recovery / "index.html").write_text("published", encoding="utf-8")
            forge = SimpleNamespace(project_root=root, _last_play_dir=recovery)
            with mock.patch.object(generate, "play_bundle_directory", return_value=working):
                self.assertEqual(
                    ForgeTab._current_play_index(forge),
                    working / "index.html",
                )

    def test_forge_does_not_retry_a_preview_after_leaving_the_tab(self) -> None:
        forge = SimpleNamespace(
            _shutdown=False,
            _worker_thread=None,
            _worker=object(),
            _operation_error_message="error",
            _operation_failure=mock.Mock(),
            _busy=True,
            _set_busy=mock.Mock(),
            _finish_restore_activity=mock.Mock(),
            _preview_refresh_requested=True,
            _tab_active=False,
            ensure_preview_current=mock.Mock(),
        )

        with mock.patch("Forge_Tab.QtCore.QTimer.singleShot") as single_shot:
            ForgeTab._operation_finished(forge)

        single_shot.assert_not_called()
        self.assertFalse(forge._preview_refresh_requested)

    def test_forge_deactivation_pauses_preview_without_releasing_files(self) -> None:
        forge = SimpleNamespace(
            _tab_active=True,
            _preview_refresh_requested=True,
            _refresh_timer=mock.Mock(),
            _catalog_refresh_timer=mock.Mock(),
            _card_layout_timer=mock.Mock(),
            _scroll_restore_timer=mock.Mock(),
            saved_panel=mock.Mock(),
            preview_visibility_changed=mock.Mock(),
            preview_files_release_requested=mock.Mock(),
        )

        ForgeTab.deactivate_for_tab_change(forge)

        self.assertFalse(forge._tab_active)
        self.assertFalse(forge._preview_refresh_requested)
        forge._refresh_timer.stop.assert_called_once_with()
        forge._catalog_refresh_timer.stop.assert_called_once_with()
        forge._card_layout_timer.stop.assert_called_once_with()
        forge._scroll_restore_timer.stop.assert_called_once_with()
        forge.saved_panel.hide.assert_called_once_with()
        forge.preview_visibility_changed.emit.assert_called_once_with(False)
        forge.preview_files_release_requested.emit.assert_not_called()

    def test_forge_restore_fails_closed_when_media_release_reports_error(self) -> None:
        forge = SimpleNamespace(
            _project_release_error="",
            preview_files_release_requested=mock.Mock(),
            project_files_release_requested=mock.Mock(),
            _set_status=mock.Mock(),
        )
        forge.project_files_release_requested.emit.side_effect = lambda: setattr(
            forge,
            "_project_release_error",
            "sound worker is still active",
        )

        released = ForgeTab._release_project_files_for_restore(forge)

        self.assertFalse(released)
        forge.preview_files_release_requested.emit.assert_called_once_with()
        forge.project_files_release_requested.emit.assert_called_once_with()
        forge._set_status.assert_called_once_with(
            "Background media work must finish before this letter can load.",
            error=True,
            timeout_ms=0,
        )

    def test_hidden_forge_defers_scheduled_refresh_until_activation(self) -> None:
        refresh_timer = mock.Mock()

        ForgeTab.schedule_refresh(
            SimpleNamespace(
                _shutdown=False,
                _tab_active=False,
                _refresh_timer=refresh_timer,
            )
        )

        refresh_timer.start.assert_not_called()

    def test_forge_shutdown_stops_owned_resources_once(self) -> None:
        timers = [mock.Mock() for _index in range(6)]
        watcher = mock.Mock()
        watcher.directories.return_value = ["watched-directory"]
        watcher.files.return_value = ["watched-file"]
        forge = SimpleNamespace(
            _shutdown=False,
            shutdown_operations=mock.Mock(return_value=True),
            deactivate_for_tab_change=mock.Mock(),
            _card_layout_timer=timers[0],
            _refresh_timer=timers[1],
            _status_timer=timers[2],
            _catalog_refresh_timer=timers[3],
            _scroll_restore_timer=timers[4],
            _metadata_timer=timers[5],
            _pending_metadata_update=None,
            _run_pending_metadata_update=mock.Mock(),
            _preview_refresh_requested=True,
            _pending_publish_context=(object(),),
            _operation_success=mock.Mock(),
            _operation_failure=mock.Mock(),
            _operation_error_message="error",
            _finish_restore_activity=mock.Mock(),
            _catalog_watcher=watcher,
            settings=SimpleNamespace(changed=mock.Mock()),
            _on_settings_changed=mock.Mock(),
            _settings_refresh_requested=mock.Mock(),
            schedule_refresh=mock.Mock(),
            saved_panel=mock.Mock(),
            github_account_dialog=mock.Mock(),
            github_device_dialog=mock.Mock(),
            readiness_window=mock.Mock(),
        )

        self.assertTrue(ForgeTab.shutdown(forge, timeout_ms=25))
        self.assertTrue(ForgeTab.shutdown(forge, timeout_ms=25))

        forge.shutdown_operations.assert_called_once_with(timeout_ms=25)
        forge.deactivate_for_tab_change.assert_called_once_with()
        for timer in timers:
            timer.stop.assert_called_once_with()
        watcher.removePaths.assert_called_once_with(
            ["watched-directory", "watched-file"]
        )
        watcher.blockSignals.assert_called_once_with(True)
        forge.saved_panel.close.assert_called_once_with()
        forge.github_account_dialog.close.assert_called_once_with()
        forge.github_device_dialog.finish.assert_called_once_with()
        forge.readiness_window.shutdown.assert_called_once_with()

    def test_forge_shutdown_timeout_preserves_live_resources(self) -> None:
        timer = mock.Mock()
        forge = SimpleNamespace(
            _shutdown=False,
            shutdown_operations=mock.Mock(return_value=False),
            _card_layout_timer=timer,
        )

        self.assertFalse(ForgeTab.shutdown(forge, timeout_ms=1))
        self.assertFalse(forge._shutdown)
        timer.stop.assert_not_called()

    def test_duplicate_project_ids_are_reported_and_repaired_without_merging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = ProjectPathResolver(root)
            recipient = resolver.registry.get_or_create("José Núñez")
            recipient_dir = resolver.resolve_autosave_recipient_directory(recipient.recipient_id)
            project_id = str(uuid.uuid4())
            first = recipient_dir / "Letter One"
            second = recipient_dir / "Letter Two"
            for path, title in ((first, "Letter One"), (second, "Letter Two")):
                path.mkdir(parents=True)
                (path / "lettersmith-metadata.json").write_text(
                    json.dumps(
                        {
                            "project_id": project_id,
                            "recipient_id": recipient.recipient_id,
                            "recipient_name": recipient.display_name,
                            "recipient_title": title,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(ProjectPathError, "Letter One") as raised:
                resolver.resolve_autosave_directory(project_id, recipient_id=recipient.recipient_id)
            self.assertIn("Letter Two", str(raised.exception))

            context = resolver.context_from_settings(
                {
                    "project_id": project_id,
                    "recipient_id": recipient.recipient_id,
                    "recipient_name": recipient.display_name,
                    "recipient_title": "Letter One",
                }
            )
            self.assertEqual(context.autosave_directory, first.resolve())
            self.assertEqual(
                resolver._metadata_project_id(first),
                project_id,
            )
            self.assertNotEqual(
                resolver._metadata_project_id(second),
                project_id,
            )
            self.assertTrue(
                any(second.glob("lettersmith-metadata.json.backup-*"))
            )

    def test_failed_project_id_rewrite_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = ProjectPathResolver(root)
            recipient = resolver.registry.get_or_create("Amina O'Connor")
            recipient_dir = resolver.resolve_autosave_recipient_directory(recipient.recipient_id)
            project_id = str(uuid.uuid4())
            active = recipient_dir / "Current"
            copy = recipient_dir / "Copied"
            for path in (active, copy):
                path.mkdir(parents=True)
                (path / "lettersmith-metadata.json").write_text(
                    json.dumps({"project_id": project_id, "recipient_title": path.name}),
                    encoding="utf-8",
                )
            before = (copy / "lettersmith-metadata.json").read_text(encoding="utf-8")
            with mock.patch("project_paths.safe_write_json", side_effect=OSError("read-only")):
                with self.assertRaises(ProjectPathError):
                    resolver.repair_duplicate_autosave_ids(active_autosave_directory=active)
            self.assertEqual(
                (copy / "lettersmith-metadata.json").read_text(encoding="utf-8"),
                before,
            )

    def test_same_recipient_requires_a_unique_letter_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            resolver = ProjectPathResolver(root)
            recipient_dir = resolver.resolve_autosave_recipient_directory(
                identity.recipient_id
            )
            existing = recipient_dir / "Morning Joy"
            existing.mkdir(parents=True)
            (existing / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "project_id": str(uuid.uuid4()),
                        "recipient_id": identity.recipient_id,
                        "recipient_name": "Amanda Miller",
                        "recipient_title": "Morning Joy",
                    }
                ),
                encoding="utf-8",
            )
            settings = {
                **identity.as_settings(),
                "recipient_title": "Morning Joy",
            }

            self.assertEqual(
                resolver.find_title_conflict(
                    identity.recipient_id,
                    "Morning Joy",
                    project_id=identity.project_id,
                ),
                existing.resolve(),
            )
            with self.assertRaisesRegex(
                ProjectPathError,
                "different letter title",
            ):
                resolver.context_from_settings(settings)
            SettingsStore(root).update_fields(
                {"recipient_title": "Morning Joy"}
            )
            title_item = next(
                item
                for item in evaluate_readiness(root).items
                if item.key == "title"
            )
            self.assertFalse(title_item.ready)
            self.assertIn("different letter title", title_item.detail)

    def test_snapshot_staging_directory_does_not_conflict_with_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {"recipient_title": "Morning Joy"}
            )
            resolver = ProjectPathResolver(root)
            recipient_dir = resolver.resolve_autosave_recipient_directory(
                identity.recipient_id
            )
            current = recipient_dir / "Morning Joy"
            staging = recipient_dir / (
                "Morning Joy.snapshot-staging."
                f"{uuid.uuid4().hex}"
            )
            for path, project_id in (
                (current, identity.project_id),
                (staging, str(uuid.uuid4())),
            ):
                path.mkdir(parents=True)
                (path / "lettersmith-metadata.json").write_text(
                    json.dumps(
                        {
                            "project_id": project_id,
                            "recipient_id": identity.recipient_id,
                            "recipient_name": "Amanda Miller",
                            "recipient_title": "Morning Joy",
                        }
                    ),
                    encoding="utf-8",
                )

            self.assertIsNone(
                resolver.find_title_conflict(
                    identity.recipient_id,
                    "Morning Joy",
                    project_id=identity.project_id,
                )
            )
            title_item = next(
                item
                for item in evaluate_readiness(root).items
                if item.key == "title"
            )
            self.assertTrue(title_item.ready)

    def test_stock_prefixed_title_routes_to_disposable_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = str(uuid.uuid4())
            source = root / "output" / "Play" / "Amani Hill" / "Stock Letter"
            source.mkdir(parents=True)
            for filename in ("index.html", "styles.css", "script.js"):
                (source / filename).write_text("", encoding="utf-8")
            (source / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "project_id": project_id,
                        "recipient_name": "Amani Hill",
                        "recipient_title": "Stock: Letter",
                    }
                ),
                encoding="utf-8",
            )

            stock = resolve_play_bundle_directory(
                root,
                recipient="Amani Hill",
                title="Stock: Letter",
                project_id=project_id,
            )

            self.assertEqual(
                stock,
                (
                    application_paths(root).temporary_root
                    / "Demo Preview"
                    / project_id
                ).resolve(),
            )
            self.assertFalse(stock.exists())
            self.assertTrue(source.is_dir())

            play = resolve_play_bundle_directory(
                root,
                recipient="Amani Hill",
                title="Regular Letter",
                project_id=project_id,
            )

            self.assertEqual(
                play,
                (
                    root
                    / "output"
                    / "Play"
                    / "Amani Hill"
                    / "Regular Letter"
                ).resolve(),
            )
            self.assertNotEqual(play, source.resolve())
            self.assertTrue(source.is_dir())

    def test_reserved_stock_and_example_identities_cannot_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root)
            cases = (
                ("Stock 1.0", "Davis"),
                ("Stock Real", "Davis"),
                ("A Stock Letter 2", "Davis"),
                ("NONE", "Davis"),
                ("Ordinary Letter", "Stock"),
                ("Ordinary Letter", "Davis Stock 3"),
                ("Example Letter", "Davis"),
                ("Ordinary Letter", "A Friend"),
                ("Ordinary Letter", "None"),
            )
            for title, recipient in cases:
                settings.update_fields(
                    {
                        "recipient_title": title,
                        "recipient_name": recipient,
                    }
                )
                eligibility = evaluate_project_save_eligibility(root)
                self.assertFalse(eligibility.can_save)
                self.assertTrue(eligibility.persistent_block_reason)

            for recipient in (
                "Stock Davis",
                "Friend",
                "My Friend",
                "None Davis",
            ):
                settings.update_fields(
                    {
                        "recipient_title": "Ordinary Letter",
                        "recipient_name": recipient,
                    }
                )
                eligibility = evaluate_project_save_eligibility(root)
                self.assertFalse(eligibility.persistent_block_reason)

    def test_bundled_letters_are_already_published_and_openable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind, relative in (
                (STOCK_PROJECT_KIND, "resources/stock/letters/Stock Letter 1"),
                (STOCK_PROJECT_KIND, "resources/stock/letters/Stock Letter 2"),
                (STOCK_PROJECT_KIND, "resources/stock/letters/Stock Letter 3"),
                (EXAMPLE_PROJECT_KIND, "resources/examples/example_letter"),
            ):
                with self.subTest(letter=relative):
                    master = root / relative
                    master.mkdir(parents=True, exist_ok=True)
                    index = master / "index.html"
                    index.write_text("bundled viewer", encoding="utf-8")
                    SettingsStore(root).update_fields({
                        PROTECTED_PROJECT_KIND_KEY: kind,
                        PROTECTED_PROJECT_MASTER_PATH_KEY: str(master),
                    })
                    tab = ForgeTab(root)
                    try:
                        result = tab.refresh_readiness()
                        self.assertEqual(result.items, ())
                        self.assertEqual(result.status, "")
                        self.assertEqual(tab.readiness_summary.text(), "")
                        self.assertTrue(tab._readiness_controls.isHidden())
                        self.assertEqual(tab.publish_btn.text(), "Published")
                        self.assertFalse(tab.publish_btn.isEnabled())
                        self.assertFalse(tab.unpublish_btn.isHidden())
                        self.assertFalse(tab.unpublish_btn.isEnabled())
                        self.assertTrue(tab.preview_btn.isEnabled())
                        self.assertTrue(tab.open_published_btn.isEnabled())
                        with mock.patch.object(
                            QtGui.QDesktopServices, "openUrl", return_value=True
                        ) as open_url:
                            tab.open_published_letter()
                        self.assertEqual(
                            Path(open_url.call_args.args[0].toLocalFile()),
                            index.resolve(),
                        )
                        with mock.patch.object(
                            tab, "_flush_prompt_writer_state"
                        ) as flush:
                            with mock.patch.object(tab, "_start_operation") as start:
                                tab._prepare_preview(open_in_browser=False)
                            tab._update_metadata_silently(master, result)
                        flush.assert_not_called()
                        start.assert_called_once()
                        tab._record_active_play_dir(master)
                        self.assertFalse(
                            SettingsStore(root).get(ACTIVE_PLAY_DIR_KEY)
                        )
                    finally:
                        tab.close()

    def test_preview_options_follow_preview_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = ForgeTab(Path(directory))
            try:
                self.assertEqual(tab.preview_mode_value, "landscape")
                blocked = tab._readiness_result
                self.assertFalse(blocked.can_preview)
                self.assertEqual(tab.preview_mode.count(), 0)
                self.assertEqual(tab.preview_mode.currentIndex(), -1)
                self.assertFalse(tab.preview_mode.isEnabled())
                self.assertEqual(tab.curtain_style_selector.count(), 0)
                self.assertEqual(tab.curtain_style_selector.currentText(), "")
                self.assertFalse(tab.curtain_style_selector.isEnabled())

                ready = ReadinessResult((), 100, "Ready")
                with mock.patch("Forge_Tab.evaluate_readiness", return_value=ready):
                    tab.refresh_readiness()
                self.assertTrue(tab.preview_mode.isEnabled())
                self.assertEqual(tab.preview_mode.count(), 3)
                self.assertEqual(tab.preview_mode.currentData(), "landscape")
                self.assertEqual(
                    [tab.preview_mode.itemText(index) for index in range(3)],
                    ["Portrait", "Landscape", "Browser"],
                )
                self.assertTrue(tab.curtain_style_selector.isEnabled())
                self.assertEqual(tab.curtain_style_selector.count(), 7)

                with mock.patch.object(tab, "request_preview") as request_preview:
                    tab.preview_mode.setCurrentIndex(tab.preview_mode.findData("window"))
                request_preview.assert_called_once_with()
                self.assertEqual(SettingsStore(directory).get("forge_preview_mode"), "window")
                tab.curtain_style_selector.set_current_style("normal_dark")
                with mock.patch("Forge_Tab.evaluate_readiness", return_value=blocked):
                    tab.refresh_readiness()
                self.assertEqual(tab.preview_mode.count(), 0)
                self.assertEqual(tab.preview_mode.currentText(), "")
                self.assertFalse(tab.preview_mode.isEnabled())
                self.assertEqual(tab.curtain_style_selector.count(), 0)
                self.assertEqual(tab.curtain_style_selector.currentText(), "")
                self.assertFalse(tab.curtain_style_selector.isEnabled())
                with mock.patch("Forge_Tab.evaluate_readiness", return_value=ready):
                    tab.refresh_readiness()
                self.assertEqual(tab.preview_mode.currentData(), "window")
            finally:
                tab.shutdown()
                tab.close()

            with mock.patch("Forge_Tab.evaluate_readiness", return_value=ready):
                reopened = ForgeTab(Path(directory))
            try:
                self.assertEqual(reopened.preview_mode_value, "window")
                self.assertEqual(reopened.preview_mode.currentText(), "Browser")
            finally:
                reopened.shutdown()
                reopened.close()

    def test_forge_actions_and_preview_format_follow_each_theme(self) -> None:
        def contrast_ratio(foreground: str, background: str) -> float:
            def luminance(value: str) -> float:
                color = QtGui.QColor(value)

                def linear(channel: int) -> float:
                    normalized = channel / 255.0
                    return (
                        normalized / 12.92
                        if normalized <= 0.04045
                        else ((normalized + 0.055) / 1.055) ** 2.4
                    )

                return (
                    0.2126 * linear(color.red())
                    + 0.7152 * linear(color.green())
                    + 0.0722 * linear(color.blue())
                )

            foreground_luminance = luminance(foreground)
            background_luminance = luminance(background)
            lighter = max(foreground_luminance, background_luminance)
            darker = min(foreground_luminance, background_luminance)
            return (lighter + 0.05) / (darker + 0.05)

        with tempfile.TemporaryDirectory() as directory:
            tab = ForgeTab(Path(directory))
            service = ThemeService(directory, parent=tab)
            backgrounds_by_action = {
                "preview": set(),
                "publish": set(),
                "open": set(),
            }
            buttons = {
                "preview": tab.preview_btn,
                "publish": tab.publish_btn,
                "open": tab.open_published_btn,
            }

            for theme_id, definition in THEMES.items():
                service.set_theme(theme_id, persist=False)
                tab.apply_theme_assets(service)

                colors = definition.tokens
                self.assertIn(colors.border, tab.preview_format_panel.styleSheet())
                self.assertIn(colors.highlight, tab.preview_format_label.styleSheet())
                self.assertIn(colors.text, tab.preview_mode.styleSheet())
                self.assertIn(colors.accent, tab.preview_mode.styleSheet())
                delegate = tab.preview_mode.itemDelegate()
                self.assertEqual(
                    delegate._normal_background,
                    QtGui.QColor(colors.panel_background),
                )
                self.assertEqual(
                    delegate._selected_background,
                    QtGui.QColor(colors.active),
                )

                service.apply_semantic_styles(tab)
                self.assertEqual(
                    tab.unpublish_btn.size(),
                    tab.github_account_btn.size(),
                )
                theme_backgrounds = set()
                for action, button in buttons.items():
                    background = str(button.property("forgeActionBackground"))
                    hover = str(button.property("forgeActionHover"))
                    foreground = str(button.property("forgeActionText"))
                    backgrounds_by_action[action].add(background)
                    theme_backgrounds.add(background)
                    self.assertIn(background, button.styleSheet())
                    self.assertGreaterEqual(
                        contrast_ratio(foreground, background),
                        4.5,
                    )
                    self.assertGreaterEqual(
                        contrast_ratio(foreground, hover),
                        4.5,
                    )
                self.assertEqual(len(theme_backgrounds), 3)

            for backgrounds in backgrounds_by_action.values():
                self.assertEqual(len(backgrounds), len(THEMES))
            tab.close()

    def test_project_save_requires_two_completed_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {"recipient_title": "Morning Joy"}
            )
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            (pages / "cover.png").write_bytes(b"cover")

            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(
                root,
                state,
                resolver=resolver,
            )
            eligibility = evaluate_project_save_eligibility(root)
            self.assertEqual(eligibility.completed_tabs, ("message",))
            self.assertFalse(eligibility.can_save)
            context = resolver.context_from_settings(
                SettingsStore(root).snapshot()
            )
            service.copy_workspace_file(
                pages / "cover.png",
                Path("pages") / "cover.png",
            )
            self.assertFalse(context.autosave_directory.exists())

            for name in REQUIRED_SLIDES:
                if not (pages / name).exists():
                    (pages / name).write_bytes(name.encode("ascii"))
            message_asset = root / MESSAGE_ASSETS_DIR / "inline.png"
            message_asset.parent.mkdir(parents=True)
            message_asset.write_bytes(b"inline asset")
            eligibility = evaluate_project_save_eligibility(root)
            self.assertEqual(
                eligibility.completed_tabs,
                ("images", "message"),
            )
            self.assertTrue(eligibility.can_save)
            readiness = evaluate_readiness(root)
            message_item = next(
                item for item in readiness.items if item.key == "message"
            )
            self.assertFalse(message_item.ready)
            self.assertFalse(message_item.required)
            self.assertTrue(readiness.can_preview)

            saved = service.save_workspace_snapshot(reason="tab-switch")

            self.assertEqual(saved, context.autosave_directory)
            self.assertTrue((saved / "pages" / "cover.png").is_file())
            self.assertEqual(
                (saved / MESSAGE_ASSETS_DIR / "inline.png").read_bytes(),
                b"inline asset",
            )
            self.assertFalse((saved / "message" / "message.html").exists())
            metadata = json.loads(
                (saved / "lettersmith-metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                metadata["completed_tabs"],
                ["images", "message"],
            )
            self.assertEqual(metadata["autosave_reason"], "tab-switch")
            self.assertEqual(metadata["schema_version"], "1.0")
            self.assertEqual(
                metadata["document_type"],
                "project_autosave",
            )

    def test_valid_manual_url_satisfies_published_link_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            SettingsStore(root).update_fields(
                {
                    "published_page_url": "http://example.com/letter?preview=1",
                }
            )

            published_link = next(
                item
                for item in evaluate_readiness(root).items
                if item.key == "published_url"
            )

            self.assertTrue(published_link.ready)

    def test_title_change_renames_the_same_project_and_play_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            identity = state.establish_project(
                "Amani Hill",
                custom_capitalization=True,
            )
            settings = SettingsStore(root)
            settings.update_fields(
                {"recipient_title": "Perfection's Path"}
            )
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Letter</p>", encoding="utf-8")
            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(root, state, resolver=resolver)

            original_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )
            play_recipient = resolver.resolve_play_recipient_directory(
                identity.recipient_id
            )
            original_play = play_recipient / "Perfection's Path"
            original_play.mkdir(parents=True)
            (original_play / "index.html").write_text(
                "<title>Perfection's Path</title>",
                encoding="utf-8",
            )
            (original_play / "styles.css").write_text("", encoding="utf-8")
            (original_play / "script.js").write_text("", encoding="utf-8")
            (original_play / "lettersmith-build.json").write_text(
                json.dumps({"project_id": identity.project_id}),
                encoding="utf-8",
            )

            settings.update_fields(
                {"recipient_title": "The Path of Perfection"}
            )
            renamed_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )
            renamed_play = play_recipient / "The Path of Perfection"

            self.assertFalse(original_project.exists())
            self.assertEqual(
                renamed_project.name,
                "The Path of Perfection",
            )
            self.assertEqual(
                json.loads(
                    (
                        renamed_project / "lettersmith-metadata.json"
                    ).read_text(encoding="utf-8")
                )["project_id"],
                identity.project_id,
            )
            self.assertFalse(original_play.exists())
            self.assertTrue(renamed_play.is_dir())
            play_metadata = json.loads(
                (renamed_play / "lettersmith-metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                play_metadata["project_id"],
                identity.project_id,
            )
            self.assertEqual(
                play_metadata["recipient_id"],
                identity.recipient_id,
            )
            self.assertEqual(
                play_metadata["recipient_title"],
                "The Path of Perfection",
            )
            self.assertEqual(
                settings.snapshot()["active_play_dir"],
                str(renamed_play.resolve()),
            )
            with self.assertRaises(FileExistsError):
                resolve_play_bundle_directory(
                    root,
                    recipient="Amani Hill",
                    title="The Path of Perfection",
                    project_id=str(uuid.uuid4()),
                )
            self.assertFalse(
                (play_recipient / "The Path of Perfection (2)").exists()
            )

    def test_same_recipient_and_title_cannot_save_a_second_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            first_identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            settings = SettingsStore(root)
            settings.update_fields({"recipient_title": "Morning Joy"})
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Letter</p>", encoding="utf-8")
            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(root, state, resolver=resolver)
            first_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )

            state.begin_new_project()
            second_identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            settings.update_fields({"recipient_title": "Morning Joy"})

            self.assertNotEqual(
                first_identity.project_id,
                second_identity.project_id,
            )
            with self.assertRaisesRegex(
                ProjectNotReadyError,
                "different letter title",
            ):
                service.save_workspace_snapshot(reason="tab-switch")
            self.assertTrue(first_project.is_dir())
            self.assertEqual(
                [path.name for path in first_project.parent.iterdir()],
                ["Morning Joy"],
            )

    def test_each_completed_workflow_counts_toward_two_tab_gate(self) -> None:
        expected_by_workflow = {
            "images": ("images", "message"),
            "sound": ("sound", "message"),
            "prompt_writer": ("message", "prompt_writer"),
        }
        for workflow, expected in expected_by_workflow.items():
            with self.subTest(workflow=workflow):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    state = ProjectStateController(root)
                    state.initialize()
                    state.establish_project(
                        "Amanda Miller",
                        custom_capitalization=True,
                    )
                    SettingsStore(root).update_fields(
                        {"recipient_title": "A Unique Title"}
                    )
                    baseline = evaluate_project_save_eligibility(root)
                    self.assertEqual(baseline.completed_tabs, ("message",))
                    self.assertFalse(baseline.can_save)

                    if workflow == "images":
                        pages = root / "gallery" / "user" / "pages"
                        pages.mkdir(parents=True)
                        for name in REQUIRED_SLIDES:
                            (pages / name).write_bytes(b"image")
                    if workflow == "sound":
                        source = root / "song.mp3"
                        source.write_bytes(b"sound")
                        record = import_runtime_track(
                            root,
                            source,
                            display_title="Song",
                        )
                        save_project_state(
                            root,
                            ProjectSoundState(
                                mode="single",
                                single_track_id=record.track_id,
                                selected_track_id=record.track_id,
                            ),
                        )
                    if workflow == "prompt_writer":
                        (root / "prompt_writer_state.json").write_text(
                            json.dumps(
                                {
                                    "generated_prompts": {
                                        "cover": "A generated cover prompt"
                                    }
                                }
                            ),
                            encoding="utf-8",
                        )

                    eligibility = evaluate_project_save_eligibility(root)

                    self.assertEqual(
                        eligibility.completed_tabs,
                        expected,
                    )
                    self.assertTrue(eligibility.can_save)

    def _make_editor(
        self,
        preset: str,
        *,
        language_service=None,
        theme_service=None,
    ) -> tuple[Editor, tempfile.TemporaryDirectory[str]]:
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        (root / "settings.json").write_text(
            json.dumps({"message_overlay_preset": preset}),
            encoding="utf-8",
        )
        host = QtWidgets.QWidget()
        host.project_root = root
        if theme_service is not None:
            host.theme_service = theme_service
        editor = Editor(
            "<p>Letter</p>",
            parent=host,
            language_service=language_service,
        )
        editor._host_for_test = host
        return editor, holder

    def test_editor_uses_themed_close_only_title_bar(self) -> None:
        theme_service = SimpleNamespace(
            tokens=SimpleNamespace(
                accent="#123456",
                muted_text="#789abc",
                highlight="#ffffff",
                error="#ff0000",
                text="#eeeeee",
            ),
            app_font_family="Segoe UI",
            resolve_asset=lambda _name: Path("missing-theme-asset.png"),
        )
        editor, holder = self._make_editor(
            "black",
            theme_service=theme_service,
        )
        try:
            editor.show()
            self.app.processEvents()

            self.assertEqual(editor.windowTitle(), "Letter Editor")
            self.assertTrue(
                editor.windowFlags() & QtCore.Qt.FramelessWindowHint
            )
            self.assertIs(editor.title_bar._theme_service, theme_service)
            self.assertFalse(editor.title_bar.btn_minimize.isVisible())
            self.assertFalse(editor.title_bar.btn_maximize.isVisible())
            self.assertTrue(editor.title_bar.btn_close.isVisible())
        finally:
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_background_and_font_controls_stay_open(self) -> None:
        editor, holder = self._make_editor("black")
        try:
            editor.show()
            self.app.processEvents()
            for control in (
                editor.font_controls,
                editor.font_combo,
                editor.font_size_spin,
            ):
                self.assertTrue(control.isVisible())
                self.assertGreater(control.width(), 0)
            for control in (
                editor.btn_format,
                editor.btn_color,
                editor.btn_align,
                editor.btn_lists,
                editor.btn_spacing,
            ):
                self.assertTrue(editor.font_controls.isAncestorOf(control))
                self.assertFalse(editor.toolbar.isAncestorOf(control))
            toolbar_actions = editor.toolbar.actions()

            def widget_action(widget: QtWidgets.QWidget) -> QtGui.QAction:
                return next(
                    action
                    for action in toolbar_actions
                    if isinstance(action, QtWidgets.QWidgetAction)
                    and action.defaultWidget() is widget
                )

            def has_separator_between(
                left: QtGui.QAction,
                right: QtGui.QAction,
            ) -> bool:
                left_index = toolbar_actions.index(left)
                right_index = toolbar_actions.index(right)
                return any(
                    action.isSeparator()
                    for action in toolbar_actions[left_index + 1 : right_index]
                )

            link_action = widget_action(editor.btn_links)
            ultralink_action = widget_action(editor.btn_ultralink)
            ultralink_color_action = widget_action(editor.btn_ultralink_color)
            self.assertTrue(
                has_separator_between(editor.act_salutation, link_action)
            )
            self.assertTrue(has_separator_between(link_action, ultralink_action))
            self.assertTrue(
                has_separator_between(ultralink_color_action, editor.act_undo)
            )
            self.assertTrue(
                has_separator_between(editor.act_redo, editor.act_find)
            )
            self.assertFalse(hasattr(editor, "font_size_down"))
            self.assertFalse(hasattr(editor, "font_size_up"))
            self.assertIn(
                "background:transparent",
                editor.editor.styleSheet(),
            )
            self.assertEqual(editor.editor_surface._preset, "black")
            self.assertEqual(editor.readability_indicator.objectName(), "readabilityIndicator")
            self.assertIn("color:#f1eee8", editor.editor.styleSheet())
            editor.editor.selectAll()
            editor.set_font_size(24)
            self.assertEqual(round(editor.editor.textCursor().charFormat().fontPointSize()), 24)
            editor.font_size_spin.setFocus()
            event = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(editor.font_size_spin, event)
            self.assertFalse(editor._closing)
        finally:
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_save_does_not_close_and_reentrant_save_is_ignored(self) -> None:
        editor, holder = self._make_editor("clear")
        try:
            editor._last_persisted_html = "different"
            def save_and_reenter(*_args, **_kwargs):
                editor._save_only()

            with mock.patch.object(
                editor.project_save_service,
                "save_message",
                side_effect=save_and_reenter,
            ) as save:
                editor._save_only()
            self.assertFalse(editor._closing)
            self.assertEqual(save.call_count, 1)
            self.assertIn("background:transparent", editor.editor.styleSheet())
            self.assertEqual(editor.editor_surface._opacity, 0)
        finally:
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_surface_uses_wall_art_and_reports_low_contrast(self) -> None:
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        (root / "settings.json").write_text(
            json.dumps({"message_overlay_preset": "clear"}),
            encoding="utf-8",
        )
        host = QtWidgets.QWidget()
        host.project_root = root
        wall = QtGui.QPixmap(64, 64)
        wall.fill(QtGui.QColor("white"))
        editor = Editor("<p>Letter</p>", wall, parent=host)
        try:
            editor.show()
            self.app.processEvents()
            self.assertIsNotNone(editor.editor_surface.wall_pixmap)
            self.assertFalse(hasattr(editor_module, "PreviewWidget"))

            cursor = editor.editor.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            format_ = QtGui.QTextCharFormat()
            format_.setForeground(QtGui.QColor("white"))
            cursor.mergeCharFormat(format_)
            editor.editor.setTextCursor(cursor)
            editor._update_readability_indicator()
            self.assertEqual(
                editor.readability_indicator.property("readabilityState"),
                "low",
            )
        finally:
            editor.deleteLater()
            host.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_debounces_language_analysis_and_underlines_issues(self) -> None:
        issue = SimpleNamespace(
            start=2,
            end=5,
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=True,
        )
        service = FakeLanguageService((issue,))
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor.recipient_name = "Amani Hill"
            editor._language_check_timer.stop()
            editor._language_check_timer.setInterval(0)

            editor.editor.setPlainText("A teh note for Amani Hill.")

            self.assertTrue(editor._language_check_timer.isActive())
            self.app.processEvents()
            self.assertEqual(
                service.calls[-1],
                (
                    "A teh note for Amani Hill.",
                    "prose",
                    ("Amani Hill",),
                ),
            )
            selections = editor.editor.extraSelections()
            self.assertEqual(len(selections), 1)
            self.assertEqual(selections[0].cursor.selectedText(), "teh")
            self.assertEqual(
                selections[0].format.underlineStyle(),
                QtGui.QTextCharFormat.UnderlineStyle.SpellCheckUnderline,
            )
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_proofreads_before_save_preserving_format_and_undo(self) -> None:
        issue = SimpleNamespace(
            start=0,
            end=3,
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=False,
        )
        service = FakeLanguageService((issue,))
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor._language_check_timer.stop()
            editor.editor.setHtml(
                '<p><span style="color:#ff0000;font-weight:700">teh</span> '
                '<span style="color:#0000ff;font-style:italic">world</span></p>'
            )

            with mock.patch.object(
                editor.project_save_service,
                "save_message",
            ) as save:
                self.assertTrue(editor._save_document())

            saved_document = QtGui.QTextDocument()
            saved_document.setHtml(save.call_args.args[0])
            self.assertEqual(saved_document.toPlainText(), "the world")
            self.assertEqual(editor.editor.toPlainText(), "the world")

            corrected = QtGui.QTextCursor(editor.editor.document())
            corrected.setPosition(0)
            corrected.setPosition(3, QtGui.QTextCursor.KeepAnchor)
            self.assertEqual(
                corrected.charFormat().foreground().color().name(),
                "#ff0000",
            )
            self.assertTrue(corrected.charFormat().font().bold())

            untouched = QtGui.QTextCursor(editor.editor.document())
            untouched.setPosition(4)
            untouched.movePosition(
                QtGui.QTextCursor.NextCharacter,
                QtGui.QTextCursor.KeepAnchor,
            )
            self.assertEqual(
                untouched.charFormat().foreground().color().name(),
                "#0000ff",
            )
            self.assertTrue(untouched.charFormat().fontItalic())

            self.assertTrue(editor.editor.document().isUndoAvailable())
            editor.editor.undo()
            self.assertEqual(editor.editor.toPlainText(), "teh world")
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_maps_language_offsets_after_astral_emoji(self) -> None:
        text = "😀 teh note"
        issue = SimpleNamespace(
            start=text.index("teh"),
            end=text.index("teh") + len("teh"),
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=True,
        )
        service = FakeLanguageService(
            issue_provider=lambda current: (issue,) if "teh" in current else ()
        )
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor._language_check_timer.stop()
            editor.editor.setPlainText(text)
            editor._language_check_timer.stop()

            editor._refresh_language_issues()

            selections = editor.editor.extraSelections()
            self.assertEqual(len(selections), 1)
            self.assertEqual(selections[0].cursor.selectedText(), "teh")

            with mock.patch.object(
                editor.project_save_service,
                "save_message",
            ) as save:
                self.assertTrue(editor._save_document())

            saved_document = QtGui.QTextDocument()
            saved_document.setHtml(save.call_args.args[0])
            self.assertEqual(saved_document.toPlainText(), "😀 the note")
            self.assertEqual(editor.editor.toPlainText(), "😀 the note")
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_rechecks_safe_corrections_before_saving(self) -> None:
        first_issue = SimpleNamespace(
            start=0,
            end=3,
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=True,
        )
        second_issue = SimpleNamespace(
            start=4,
            end=7,
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=True,
        )

        def staged_issues(text: str):
            if text == "teh teh":
                return (first_issue,)
            if text == "the teh":
                return (second_issue,)
            return ()

        service = FakeLanguageService(issue_provider=staged_issues)
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor._language_check_timer.stop()
            editor.editor.setPlainText("teh teh")
            editor._language_check_timer.stop()

            with mock.patch.object(
                editor.project_save_service,
                "save_message",
            ) as save:
                self.assertTrue(editor._save_document())

            saved_document = QtGui.QTextDocument()
            saved_document.setHtml(save.call_args.args[0])
            self.assertEqual(saved_document.toPlainText(), "the the")
            self.assertEqual(editor.editor.toPlainText(), "the the")
            self.assertEqual(
                [call[0] for call in service.calls[:2]],
                ["teh teh", "the teh"],
            )
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_adds_spelling_issue_to_shared_dictionary(self) -> None:
        issue = SimpleNamespace(
            start=0,
            end=7,
            text="Quorvax",
            category="spelling",
        )
        service = FakeLanguageService((issue,))
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            refresh_spy = QtTest.QSignalSpy(
                editor.editor.language_refresh_requested
            )

            self.assertTrue(
                editor.editor.add_language_issue_to_dictionary(issue)
            )

            self.assertEqual(service.added_words, ["Quorvax"])
            self.assertEqual(refresh_spy.count(), 1)
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_ignored_spelling_issue_is_not_corrected_on_save(self) -> None:
        issue = SimpleNamespace(
            start=0,
            end=3,
            text="teh",
            category="spelling",
            replacement="the",
            suggestions=("the",),
            confidence="high",
            auto_fix=True,
        )
        service = FakeLanguageService((issue,))
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor._language_check_timer.stop()
            editor.editor.setPlainText("teh remains")
            editor._language_check_timer.stop()
            editor.editor.set_language_issues((issue,))

            editor.editor.ignore_language_issue(issue)

            self.assertEqual(service.ignored_words, ["teh"])
            self.assertEqual(editor.editor.language_issues, ())
            with mock.patch.object(
                editor.project_save_service,
                "save_message",
            ) as save:
                self.assertTrue(editor._save_document())

            saved_document = QtGui.QTextDocument()
            saved_document.setHtml(save.call_args.args[0])
            self.assertEqual(saved_document.toPlainText(), "teh remains")
            self.assertEqual(editor.editor.toPlainText(), "teh remains")
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_language_failure_does_not_block_manual_save(self) -> None:
        error = RuntimeError("checker unavailable")
        service = FakeLanguageService(error=error)
        editor, holder = self._make_editor(
            "paper",
            language_service=service,
        )
        try:
            editor._language_check_timer.stop()
            editor.editor.setPlainText("Keep the original text.")
            with (
                mock.patch.object(editor, "_record_failure") as record_failure,
                mock.patch.object(
                    editor.project_save_service,
                    "save_message",
                ) as save,
            ):
                self.assertTrue(editor._save_document())

            saved_document = QtGui.QTextDocument()
            saved_document.setHtml(save.call_args.args[0])
            self.assertEqual(
                saved_document.toPlainText(),
                "Keep the original text.",
            )
            record_failure.assert_any_call(
                "proofread letter before save",
                error,
            )
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_font_size_picker_preserves_selection_and_boundaries(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.show()
            self.app.processEvents()
            editor.editor.setHtml("<p>Alpha Beta</p>")
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor.set_font_size(16)

            option = QtWidgets.QStyleOptionSpinBox()
            editor.font_size_spin.initStyleOption(option)
            up_button = editor.font_size_spin.style().subControlRect(
                QtWidgets.QStyle.CC_SpinBox,
                option,
                QtWidgets.QStyle.SC_SpinBoxUp,
                editor.font_size_spin,
            )
            down_button = editor.font_size_spin.style().subControlRect(
                QtWidgets.QStyle.CC_SpinBox,
                option,
                QtWidgets.QStyle.SC_SpinBoxDown,
                editor.font_size_spin,
            )
            self.assertGreaterEqual(up_button.width(), 22)
            self.assertGreaterEqual(up_button.height(), 14)
            self.assertGreaterEqual(down_button.width(), 22)
            self.assertGreaterEqual(down_button.height(), 14)

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            selected = editor.editor.textCursor()
            self.assertEqual((selected.selectionStart(), selected.selectionEnd()), (0, 5))
            self.assertEqual(round(selected.charFormat().fontPointSize()), 17)

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                18,
            )

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=down_button.center(),
            )
            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=down_button.center(),
            )
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                16,
            )

            editor.font_size_spin.lineEdit().setText("24")
            enter = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(editor.font_size_spin.lineEdit(), enter)
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                24,
            )
            self.assertFalse(editor._closing)

            target_font = QtGui.QFont("LetterSmith Test Font")
            editor.font_combo.currentFontChanged.emit(target_font)
            self.app.processEvents()
            self.assertEqual(
                editor.editor.textCursor().charFormat().fontFamilies()[0],
                target_font.family(),
            )

            typing_cursor = editor.editor.textCursor()
            typing_cursor.clearSelection()
            typing_cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(typing_cursor)
            editor.set_font_size(16)
            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            typing_cursor = editor.editor.textCursor()
            typing_cursor.insertText("X")
            self.assertEqual(round(typing_cursor.charFormat().fontPointSize()), 17)

            editor.font_size_spin.setValue(100)
            editor.font_size_spin.stepUp()
            self.assertEqual(editor.font_size_spin.value(), 100)
            editor.font_size_spin.setValue(1)
            editor.font_size_spin.stepDown()
            self.assertEqual(editor.font_size_spin.value(), 1)
            self.assertFalse(editor.font_size_spin.keyboardTracking())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_salutation_uses_title_then_normal_text(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.recipient_name = "Amani Hill"
            editor.editor.setPlainText("Existing content")
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)

            block_format = cursor.blockFormat()
            block_format.setAlignment(QtCore.Qt.AlignRight)
            block_format.setLeftMargin(13.0)
            cursor.setBlockFormat(block_format)
            editor.editor.setTextCursor(cursor)

            char_format = QtGui.QTextCharFormat()
            char_format.setFontFamilies(["Onyx"])
            char_format.setFontPointSize(16.0)
            char_format.setFontWeight(QtGui.QFont.Bold)
            char_format.setFontItalic(True)
            char_format.setFontUnderline(True)
            char_format.setFontStrikeOut(True)
            char_format.setForeground(QtGui.QColor("#123456"))
            editor.editor.setCurrentCharFormat(char_format)

            editor.insert_salutation()

            self.assertEqual(
                editor.editor.toPlainText(),
                "Dear Amani Hill,\nExisting content",
            )
            salutation = QtGui.QTextCursor(editor.editor.document())
            salutation.setPosition(0)
            salutation.setPosition(len("Dear Amani Hill,"), QtGui.QTextCursor.KeepAnchor)
            salutation_format = salutation.charFormat()
            title = editor.named_styles.active_set.get("title")
            normal = editor.named_styles.active_set.get("normal_text")
            self.assertEqual(salutation_format.fontFamilies()[0], title.font_family)
            self.assertEqual(round(salutation_format.fontPointSize()), title.font_size)
            self.assertEqual(editor.named_styles.selected_style(), "normal_text")

            restored_cursor = editor.editor.textCursor()
            self.assertEqual(restored_cursor.positionInBlock(), 0)
            self.assertEqual(restored_cursor.block().text(), "Existing content")
            self.assertEqual(
                restored_cursor.blockFormat().alignment(),
                QtCore.Qt.AlignRight,
            )
            self.assertEqual(restored_cursor.blockFormat().leftMargin(), 13.0)

            restored_format = editor.editor.currentCharFormat()
            self.assertEqual(restored_format.fontFamilies()[0], normal.font_family)
            self.assertEqual(round(restored_format.fontPointSize()), normal.font_size)
            self.assertEqual(restored_format.fontWeight(), normal.font_weight)
            self.assertEqual(restored_format.fontItalic(), normal.italic)
            self.assertEqual(restored_format.fontUnderline(), normal.underline)
            self.assertEqual(restored_format.fontStrikeOut(), normal.strikethrough)
            self.assertEqual(restored_format.foreground().color().name(), normal.font_color)

            restored_cursor.insertText("X")
            typed = QtGui.QTextCursor(editor.editor.document())
            typed.setPosition(len("Dear Amani Hill,") + 1)
            typed.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            typed_format = typed.charFormat()
            self.assertEqual(typed.selectedText(), "X")
            self.assertEqual(typed_format.fontFamilies()[0], normal.font_family)
            self.assertEqual(round(typed_format.fontPointSize()), normal.font_size)
            self.assertEqual(typed_format.foreground().color().name(), normal.font_color)
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_format_actions_preserve_text_structure_and_other_styles(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setHtml(
                '<p><span style="font-size:20pt;color:#ff0000">Alpha</span></p>'
                '<p><span style="font-size:12pt;color:#0000ff">Beta</span></p>'
            )
            before_text = editor.editor.toPlainText()
            before_blocks = editor.editor.document().blockCount()
            editor.editor.selectAll()
            editor.act_bold.trigger()
            self.assertTrue(editor.act_bold.isChecked())

            document = editor.editor.document()
            alpha = QtGui.QTextCursor(document)
            alpha.setPosition(0)
            alpha.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            beta = QtGui.QTextCursor(document)
            beta.setPosition(6)
            beta.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            self.assertEqual(round(alpha.charFormat().fontPointSize()), 20)
            self.assertEqual(round(beta.charFormat().fontPointSize()), 12)
            self.assertEqual(alpha.charFormat().foreground().color().name(), "#ff0000")
            self.assertEqual(beta.charFormat().foreground().color().name(), "#0000ff")
            self.assertTrue(alpha.charFormat().font().bold())
            self.assertTrue(beta.charFormat().font().bold())

            editor.editor.selectAll()
            editor.act_clear_formatting.trigger()
            self.assertEqual(editor.editor.toPlainText(), before_text)
            self.assertEqual(editor.editor.document().blockCount(), before_blocks)
            self.assertTrue(editor.editor.textCursor().hasSelection())
            self.assertFalse(editor.act_bold.isChecked())

            editor.spacing_actions[1.5].trigger()
            self.assertTrue(editor.spacing_actions[1.5].isChecked())
            editor.alignment_actions["center"].trigger()
            self.assertTrue(editor.alignment_actions["center"].isChecked())

            cursor = editor.editor.textCursor()
            cursor.clearSelection()
            cursor.movePosition(QtGui.QTextCursor.End)
            cursor.insertText("!")
            self.assertTrue(editor.act_undo.isEnabled())
            editor.act_undo.trigger()
            self.assertTrue(editor.act_redo.isEnabled())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_format_and_shortcut_labels_show_their_effects(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            self.assertTrue(editor.act_bold.font().bold())
            self.assertTrue(editor.act_italic.font().italic())
            self.assertTrue(editor.act_underline.font().underline())
            self.assertTrue(editor.act_strike.font().strikeOut())
            salutation_button = editor.toolbar.widgetForAction(editor.act_salutation)
            self.assertTrue(salutation_button.font().bold())
            self.assertTrue(salutation_button.font().italic())

            for action, symbol, label in (
                (editor.act_undo, "↶", "Undo"),
                (editor.act_redo, "↷", "Redo"),
                (editor.act_find, "🔍", "Find and replace"),
            ):
                self.assertEqual(action.text(), symbol)
                self.assertFalse(action.shortcut().isEmpty())
                button = editor.toolbar.widgetForAction(action)
                self.assertEqual(button.accessibleName(), label)
                self.assertIn(action.shortcut().toString(), button.toolTip())

            self.assertEqual(editor.btn_links.text(), "🔗")
            self.assertEqual(editor.btn_links.accessibleName(), "Add/edit link")
            self.assertEqual(editor.btn_links.menu().actions()[0], editor.act_add_edit_link)
            self.assertEqual(editor.act_add_edit_link.text(), "Add / Edit Link")
            self.assertEqual(editor.act_add_edit_link.shortcut().toString(), "Ctrl+K")
            self.assertIn("Ctrl+K", editor.btn_links.toolTip())
            self.assertGreater(
                editor.btn_links.menu().minimumWidth(),
                editor.btn_links.menu().fontMetrics().horizontalAdvance(
                    editor.act_add_edit_link.text()
                ),
            )
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_toolbar_failures_are_contained(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            with mock.patch("Editor.show_lettersmith_message") as warning:
                editor._run_editor_action(
                    "Injected failure",
                    lambda: (_ for _ in ()).throw(RuntimeError("injected")),
                )
            warning.assert_called_once()
            error_log = (
                application_paths(editor.project_root).logs_root
                / "editor_error.log"
            )
            self.assertTrue(error_log.is_file())
            log_text = error_log.read_text(encoding="utf-8")
            self.assertIn("editor action: Injected failure", log_text)
            self.assertIn("RuntimeError: injected", log_text)
            self.assertFalse(editor._closing)
            self.assertTrue(editor.btn_save.isEnabled())
            self.assertTrue(editor.btn_close.isEnabled())
            self.assertTrue(editor.btn_save_close.isEnabled())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_generated_viewer_embeds_the_selected_font(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            fonts = root / "gallery/user/fonts"
            for folder in (pages, controls, fonts):
                folder.mkdir(parents=True, exist_ok=True)

            image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGBA8888)
            image.fill(QtGui.QColor("white"))
            for name in REQUIRED_SLIDES:
                self.assertTrue(image.save(str(pages / name)))
            for name in CONTROL_FILES:
                self.assertTrue(image.save(str(controls / name)))
            banner = root / generate.APP_BANNER_PATH
            banner.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(image.save(str(banner)))

            font_path = fonts / "Arcane_Font.ttf"
            header = struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0)
            table_record = struct.pack(">4sIII", b"OS/2", 0, 28, 10)
            font_path.write_bytes(header + table_record + (b"\0" * 10))

            play_dir = generate.generate_play_bundle(
                str(root),
                message_html=(
                    '<span style="font-family:\'Arcane\';font-size:24pt">'
                    "Letter</span>"
                ),
                seed_sfx=False,
            )
            styles = (play_dir / "styles.css").read_text(encoding="utf-8")
            index = (play_dir / "index.html").read_text(encoding="utf-8")
            state = json.loads(
                (play_dir / generate.BUILD_STATE_FILE).read_text(encoding="utf-8")
            )
            exported_files = state["font_export"]["files"]

            self.assertIn("@font-face", styles)
            self.assertIn("LetterSmithFont1", styles)
            self.assertIn("font-family:'LetterSmithFont1', 'Arcane'", index)
            self.assertEqual(state["font_export"]["embedded"], ["Arcane"])
            self.assertEqual(len(exported_files), 1)
            exported_font = play_dir / "gallery/fonts" / exported_files[0]
            self.assertTrue(exported_font.is_file())
            exported_font.write_bytes(b"X" * exported_font.stat().st_size)

            regenerated = generate.generate_play_bundle(
                str(root),
                message_html=(
                    '<span style="font-family:\'Arcane\';font-size:24pt">'
                    "Letter</span>"
                ),
                seed_sfx=False,
            )
            self.assertEqual(
                (regenerated / "gallery/fonts" / exported_files[0]).read_bytes(),
                font_path.read_bytes(),
            )

    def test_generated_viewer_allows_an_empty_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            pages.mkdir(parents=True)
            controls.mkdir(parents=True)
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGBA8888)
            image.fill(QtGui.QColor("white"))
            for name in REQUIRED_SLIDES:
                self.assertTrue(image.save(str(pages / name)))
            for name in CONTROL_FILES:
                self.assertTrue(image.save(str(controls / name)))
            banner = root / generate.APP_BANNER_PATH
            banner.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(image.save(str(banner)))
            SettingsStore(root).update_fields(
                {
                    "recipient_name": "Amanda Miller",
                    "recipient_title": "Picture Letter",
                }
            )

            play_dir = generate.generate_play_bundle(
                root,
                message_html="",
                seed_sfx=False,
            )

            generate.validate_play_bundle(play_dir)
            index = (play_dir / "index.html").read_text(encoding="utf-8")
            script = (play_dir / "script.js").read_text(encoding="utf-8")
            self.assertIn("const HAS_MESSAGE = false;", index)
            self.assertIn("if (!HAS_MESSAGE){", script)
            self.assertEqual(
                (play_dir / "gallery/message/message.html").read_text(
                    encoding="utf-8"
                ),
                "",
            )

    def test_generated_viewer_copies_and_fingerprints_message_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            pages.mkdir(parents=True)
            controls.mkdir(parents=True)
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGBA8888)
            image.fill(QtGui.QColor("white"))
            for name in REQUIRED_SLIDES:
                self.assertTrue(image.save(str(pages / name)))
            for name in CONTROL_FILES:
                self.assertTrue(image.save(str(controls / name)))
            banner = root / generate.APP_BANNER_PATH
            banner.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(image.save(str(banner)))
            SettingsStore(root).update_fields(
                {
                    "recipient_name": "Amanda Miller",
                    "recipient_title": "Picture Letter",
                }
            )
            asset = root / MESSAGE_ASSETS_DIR / "inline.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"first inline asset")
            message_html = (
                '<p><img src="gallery/message_assets/inline.png"></p>'
            )

            first_fingerprint = generate.build_source_fingerprint(root)
            play_dir = generate.generate_play_bundle(
                root,
                message_html=message_html,
                seed_sfx=False,
            )

            generate.validate_play_bundle(play_dir)
            self.assertEqual(
                (play_dir / MESSAGE_ASSETS_DIR / "inline.png").read_bytes(),
                b"first inline asset",
            )
            asset.write_bytes(b"changed inline asset")
            self.assertNotEqual(
                generate.build_source_fingerprint(root),
                first_fingerprint,
            )

    def test_ultralink_button_requires_safe_selected_text(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setHtml("<p>Alpha Beta</p>")
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()
            self.assertFalse(editor.btn_ultralink.isEnabled())

            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()
            self.assertTrue(editor.btn_ultralink.isEnabled())

            web_link = QtGui.QTextCharFormat()
            web_link.setAnchor(True)
            web_link.setAnchorHref("https://example.com")
            editor._apply_char_format(web_link)
            editor._update_ultralink_action()
            self.assertFalse(editor.btn_ultralink.isEnabled())
            self.assertIn("Remove the web link", editor.btn_ultralink.toolTip())

            with mock.patch("Editor.show_lettersmith_message") as info:
                editor.open_ultralink_dialog()
            info.assert_called_once()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_text_color_button_tracks_uniform_and_mixed_selection_colors(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.show()
            self.app.processEvents()
            editor.editor.setHtml(
                '<p><span style="color:#ff0000">Alpha</span> '
                '<span style="color:#0000ff">Beta</span></p>'
            )
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor._sync_current_format()

            self.assertFalse(editor._text_color_indicator_mixed)
            self.assertEqual(editor._active_text_color.name(), "#ff0000")
            self.assertEqual(editor.btn_color.toolTip(), "Text color: #ff0000")
            self.assertGreaterEqual(editor.btn_color.iconSize().height(), 28)
            style_option = QtWidgets.QStyleOptionToolButton()
            editor.btn_color.initStyleOption(style_option)
            self.assertGreaterEqual(style_option.iconSize.height(), 28)

            editor.settings = mock.Mock()
            with mock.patch(
                "Editor.QColorDialog.getColor",
                return_value=QtGui.QColor("#00ff00"),
            ) as picker:
                editor.btn_color.click()
            self.assertEqual(picker.call_args.args[0].name(), "#ff0000")

            cursor = editor.editor.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            editor.editor.setTextCursor(cursor)
            editor._sync_current_format()

            self.assertTrue(editor._text_color_indicator_mixed)
            self.assertEqual(
                editor._text_color_indicator_colors,
                (
                    "#ff0000",
                    "#ff7f00",
                    "#ffd700",
                    "#00a651",
                    "#0066ff",
                    "#8f00ff",
                ),
            )
            self.assertIn("Mixed text colors", editor.btn_color.toolTip())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_default_ultralink_color_button_is_direct_and_always_available(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()

            self.assertFalse(editor.btn_ultralink.isEnabled())
            self.assertTrue(editor.btn_ultralink_color.isEnabled())
            self.assertEqual(editor.btn_ultralink_color.text(), "")
            self.assertIsNone(editor.btn_ultralink.menu())
            self.assertIsNone(editor.btn_ultralink_color.menu())

            editor.settings = mock.Mock()
            with mock.patch(
                "Editor.QColorDialog.getColor",
                return_value=QtGui.QColor("#00ffff"),
            ) as picker:
                editor.btn_ultralink_color.click()

            self.assertEqual(picker.call_args.args[2], "Default Ultra Link Color Picker")
            self.assertEqual(editor.ultralink_color.name(), "#00ffff")
            self.assertIn("color:#00ffff", editor.btn_ultralink.styleSheet())
            self.assertIn("background:#00ffff", editor.btn_ultralink_color.styleSheet())
            editor.settings.setValue.assert_called_once()
            editor.settings.sync.assert_called_once()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_popup_is_minimal_and_selection_aware(self) -> None:
        single = UltralinkDialog(
            selection_text="  Amani  ",
        )
        multiple = UltralinkDialog(selection_text="Amani Hill")
        try:
            self.assertTrue(single.windowFlags() & QtCore.Qt.Popup)
            self.assertTrue(single.windowFlags() & QtCore.Qt.FramelessWindowHint)
            self.assertEqual(
                single.instruction_label.text(),
                "Ultra Link: Whatever you type here is shown when the user "
                "hovers over this word.",
            )
            self.assertEqual(
                multiple.instruction_label.text(),
                "Ultra Link: Whatever you type here is shown when the user "
                "hovers over these words.",
            )
            self.assertEqual(
                [button.text() for button in single.findChildren(QtWidgets.QPushButton)],
                ["Save"],
            )
            self.assertFalse(single.findChildren(QtWidgets.QDialogButtonBox))
            self.assertFalse(single.findChildren(QtWidgets.QCheckBox))
            self.assertFalse(single.findChildren(QtWidgets.QComboBox))
        finally:
            single.deleteLater()
            multiple.deleteLater()
            self.app.processEvents()

    def test_ultralink_popup_click_outside_rejects(self) -> None:
        editor, holder = self._make_editor("paper")
        dialog = UltralinkDialog(
            "Original hover text",
            selection_text="Amani",
            parent=editor,
        )
        try:
            editor.show()
            self.app.processEvents()
            dialog.move(editor.mapToGlobal(QtCore.QPoint(editor.width() + 20, 0)))
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            dialog.message_edit.setPlainText("Unsaved change")
            QtTest.QTest.mouseClick(
                dialog.message_edit.viewport(),
                QtCore.Qt.LeftButton,
                pos=dialog.message_edit.viewport().rect().center(),
            )
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())

            QtTest.QTest.mouseClick(
                editor,
                QtCore.Qt.LeftButton,
                pos=editor.rect().center(),
            )
            self.app.processEvents()

            self.assertFalse(dialog.isVisible())
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
        finally:
            dialog.reject()
            dialog.deleteLater()
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_save_applies_exact_phrase_to_every_occurrence(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            content = "Amani Hill met Amani Hill. Amani waited."
            editor.editor.setPlainText(content)
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(len("Amani Hill"), QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor.settings = mock.Mock()
            editor.ultralink_color = QtGui.QColor("#00ffff")

            popup = mock.Mock()
            popup.exec.return_value = QtWidgets.QDialog.Accepted
            popup.message_text.return_value = "Amani's profile"
            with mock.patch("Editor.UltralinkDialog", return_value=popup), mock.patch.object(
                QtWidgets.QToolTip,
                "showText",
            ):
                editor.open_ultralink_dialog()

            first = content.find("Amani Hill")
            second = content.find("Amani Hill", first + 1)
            for position in (first, second):
                probe = QtGui.QTextCursor(editor.editor.document())
                probe.setPosition(position)
                probe.movePosition(
                    QtGui.QTextCursor.NextCharacter,
                    QtGui.QTextCursor.KeepAnchor,
                )
                char_format = probe.charFormat()
                self.assertTrue(char_format.isAnchor())
                self.assertEqual(
                    ultralink_message_from_href(char_format.anchorHref()),
                    "Amani's profile",
                )
                self.assertEqual(char_format.foreground().color().name(), "#00ffff")

            lone_name = content.rfind("Amani")
            probe = QtGui.QTextCursor(editor.editor.document())
            probe.setPosition(lone_name)
            probe.movePosition(
                QtGui.QTextCursor.NextCharacter,
                QtGui.QTextCursor.KeepAnchor,
            )
            self.assertFalse(probe.charFormat().isAnchor())
            self.assertEqual(editor.ultralink_color.name(), "#00ffff")
            self.assertIn("#00ffff", editor.editor.toHtml().lower())
            editor.settings.setValue.assert_not_called()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_cancel_discards_document_and_color_changes(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setPlainText("Amani")
            cursor = editor.editor.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            editor.editor.setTextCursor(cursor)
            editor.settings = mock.Mock()
            editor.ultralink_color = QtGui.QColor("#ffd84d")
            original_html = editor.editor.toHtml()

            popup = mock.Mock()
            popup.exec.return_value = QtWidgets.QDialog.Rejected
            popup.message_text.return_value = "Unsaved hover text"
            with mock.patch("Editor.UltralinkDialog", return_value=popup):
                editor.open_ultralink_dialog()

            self.assertEqual(editor.editor.toHtml(), original_html)
            self.assertEqual(editor.ultralink_color.name(), "#ffd84d")
            editor.settings.setValue.assert_not_called()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_default_color_persists_across_editors(self) -> None:
        class MemorySettings:
            values: dict[str, object] = {}

            def __init__(self, *_args, **_kwargs) -> None:
                self.synced = False

            def value(self, key: str, default=None):
                return self.values.get(key, default)

            def setValue(self, key: str, value: object) -> None:
                self.values[key] = value

            def sync(self) -> None:
                self.synced = True

        first = second = None
        first_holder = second_holder = None
        with mock.patch("Editor.QSettings", MemorySettings):
            try:
                first, first_holder = self._make_editor("paper")
                first._persist_ultralink_color(QtGui.QColor("#00ffff"))
                self.assertTrue(first.settings.synced)

                second, second_holder = self._make_editor("paper")
                self.assertEqual(second.ultralink_color.name(), "#00ffff")
            finally:
                for editor, holder in (
                    (first, first_holder),
                    (second, second_holder),
                ):
                    if editor is not None:
                        editor._closing = True
                        editor.close()
                        editor.deleteLater()
                        editor._host_for_test.deleteLater()
                    if holder is not None:
                        holder.cleanup()
                self.app.processEvents()

    def test_music_archive_exposes_only_remaining_actions(self) -> None:
        class Library(QtCore.QObject):
            changed = QtCore.Signal()

            def __init__(self) -> None:
                super().__init__()
                self.project_root = Path(tempfile.gettempdir())
                self.record = TrackRecord(
                    track_id="track-1",
                    content_hash="hash",
                    display_title="Song",
                    original_name="song.mp3",
                    original_file="song.mp3",
                    processed_file="song.mp3",
                    duration_seconds=1.0,
                    added_at="2026-01-01T00:00:00+00:00",
                )

            def all_records(self, _sort: str) -> list[TrackRecord]:
                return [self.record]

            def get(self, _track_id: str) -> TrackRecord:
                return self.record

            def path_for(self, _track_id: str) -> None:
                return None

            def rename_display_title(self, _track_id: str, title: str) -> None:
                self.record.display_title = title

        library = Library()
        dialog = ArchiveDialog(
            library,
            lambda: set(),
            lambda _track_id: True,
            multi_select=False,
        )
        try:
            labels = {button.text() for button in dialog.findChildren(QtWidgets.QPushButton)}
            self.assertEqual(labels & {"Preview", "Show Original", "Repair Archive", "Close"}, set())
            self.assertIn("Rename Title", labels)
            self.assertIn("Delete", labels)
            self.assertIn("Use Selected", labels)
            self.assertFalse(dialog.rename_btn.isEnabled())
            dialog.table.selectRow(0)
            self.app.processEvents()
            self.assertTrue(dialog.rename_btn.isEnabled())
            self.assertTrue(dialog.delete_btn.isEnabled())
            self.assertTrue(dialog.choose_btn.isEnabled())
        finally:
            dialog.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
