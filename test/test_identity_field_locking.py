from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

import Message_tab as message_module
from config import USER_PAGES_DIR
from image_button import ArtworkButton
from Message_tab import IDENTITY_LOCK_KEYS, IdentityLineEdit, MessageTab
from Nexus import Nexus
from project_paths import ProjectPathResolver
from project_state import (
    ApplicationState,
    ProjectDirtyController,
    ProjectStateController,
    load_project_settings,
)
from recipient_page import RecipientPage
from readiness import evaluate_project_save_eligibility
from saved_letters import SavedLetterCatalog
from settings_store import SettingsStore
from ui_theme import BUTTON_TIER_STYLES, ButtonTier, ThemeService


class IdentityFieldLockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def _write_wall(self, root: Path) -> Path:
        wall = root / USER_PAGES_DIR / "wall.png"
        wall.parent.mkdir(parents=True, exist_ok=True)
        image = QtGui.QImage(16, 24, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("#20242a"))
        self.assertTrue(image.save(str(wall)))
        return wall

    def test_lock_keys_cover_title_recipient_and_url(self) -> None:
        self.assertEqual(
            set(IDENTITY_LOCK_KEYS),
            {"title", "recipient", "published_url"},
        )
        self.assertFalse(MessageTab._setting_bool("false"))
        self.assertTrue(MessageTab._setting_bool("true"))

    def test_recipient_page_has_load_stock_and_begin_actions(self) -> None:
        page = RecipientPage()
        load_spy = QtTest.QSignalSpy(page.load_requested)
        stock_spy = QtTest.QSignalSpy(page.stock_requested)
        recipient_spy = QtTest.QSignalSpy(page.recipient_submitted)

        labels = {
            label.text()
            for label in page.findChildren(QtWidgets.QLabel)
            if label.text()
        }
        self.assertIn("Who is this letter for?", labels)
        self.assertNotIn("Recipient", labels)
        self.assertFalse(hasattr(page, "custom_capitalization"))
        self.assertEqual(page.load_button.text(), "Load")
        self.assertEqual(page.stock_button.text(), "Stock")
        self.assertEqual(page.begin_button.text(), "Begin")
        self.assertIsInstance(page.begin_button, ArtworkButton)
        self.assertEqual(page.begin_button.artwork_path.name, "DButton.png")
        self.assertEqual(
            page.load_button.minimumSize(),
            page.begin_button.minimumSize(),
        )
        self.assertEqual(
            page.stock_button.minimumSize(),
            page.begin_button.minimumSize(),
        )

        page.stock_button.click()
        self.assertEqual(stock_spy.count(), 1)
        self.assertEqual(load_spy.count(), 0)
        self.assertEqual(recipient_spy.count(), 0)

        page.load_button.click()
        self.assertEqual(load_spy.count(), 1)
        page.recipient_input.setText("aMANda mCCall")
        page.begin_button.click()
        self.assertEqual(recipient_spy.count(), 1)
        self.assertEqual(
            list(recipient_spy.at(0)),
            ["aMANda mCCall", True],
        )
        page.deleteLater()

    def test_recipient_page_rejects_reserved_recipient_with_popup(self) -> None:
        page = RecipientPage()
        recipient_spy = QtTest.QSignalSpy(page.recipient_submitted)
        page.recipient_input.setText("Stock Davis 2")

        with mock.patch("recipient_page.show_lettersmith_message") as warning:
            page.submit()

        self.assertEqual(recipient_spy.count(), 0)
        warning.assert_called_once_with(
            page,
            "Invalid Recipient",
            "Stock Davis 2 is an invalid recipient.",
        )
        self.assertTrue(page.recipient_input.hasSelectedText())
        page.deleteLater()

    def test_double_click_unlocks_a_committed_field(self) -> None:
        field = IdentityLineEdit("A Letter")
        field.setReadOnly(True)
        field.show()
        self.app.processEvents()

        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonDblClick,
            field.rect().center(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        field.mouseDoubleClickEvent(event)
        self.assertFalse(field.isReadOnly())
        field.deleteLater()

    def test_enter_commit_locks_a_non_empty_field(self) -> None:
        message = MessageTab.__new__(MessageTab)
        message.settings = {}
        message.status = QtWidgets.QLabel()
        message._save_settings = lambda: True
        message._persist_settings = lambda *, announce: True
        field = IdentityLineEdit("Recipient")

        message._commit_identity_field(field, "recipient")

        self.assertTrue(field.isReadOnly())
        self.assertTrue(message.settings["recipient_name_locked"])
        self.assertEqual(field.property("themeRole"), "selectedState")
        self.assertEqual(field.styleSheet(), "")
        field.deleteLater()

    def test_styled_identity_field_preserves_its_text_height(self) -> None:
        field = IdentityLineEdit("The Silver Lettersmith")

        MessageTab._style_identity_field(field)

        self.assertEqual(field.property("themeRole"), "input")
        self.assertGreaterEqual(field.minimumHeight(), field.sizeHint().height())
        self.assertGreaterEqual(
            field.minimumHeight(),
            field.fontMetrics().height() + 10,
        )
        field.deleteLater()

    def test_locked_identity_field_uses_all_six_semantic_palettes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(__file__).resolve().parents[1]
            service = ThemeService(
                repository,
                settings=SettingsStore(directory),
            )
            root = QtWidgets.QWidget()
            field = IdentityLineEdit("Committed", root)
            field.setReadOnly(True)
            MessageTab._style_identity_field(field)
            palettes = set()

            for theme_id in (
                "cyber_forge",
                "obsidian_forge",
                "velvet_rose",
                "celestial_rose",
                "dark",
                "light",
            ):
                service.set_theme(theme_id, persist=False)
                service.apply_semantic_styles(root)
                stylesheet = root.styleSheet().casefold()
                palette = (
                    service.tokens.selection_background.casefold(),
                    service.tokens.selection_text.casefold(),
                    service.tokens.secondary_accent.casefold(),
                    service.tokens.input_focus.casefold(),
                )
                palettes.add(palette)
                with self.subTest(theme_id=theme_id):
                    self.assertEqual(field.property("themeRole"), "selectedState")
                    for color in palette:
                        self.assertIn(color, stylesheet)

            self.assertEqual(len(palettes), 6)
            root.deleteLater()

    def test_message_preview_reserves_the_message_tabs_minimum_height(self) -> None:
        preview_frame = QtWidgets.QWidget()
        body = QtWidgets.QWidget()
        body.resize(1400, 728)
        harness = types.SimpleNamespace(
            _forge_fullscreen_active=False,
            height=lambda: 820,
            width=lambda: 1400,
            body=body,
            tabbar=types.SimpleNamespace(currentIndex=lambda: 2),
            message_tab=types.SimpleNamespace(
                minimumSizeHint=lambda: QtCore.QSize(534, 340)
            ),
            help_icon=types.SimpleNamespace(
                height=lambda: 125,
                isVisible=lambda: True,
            ),
            body_layout=types.SimpleNamespace(spacing=lambda: 10),
            preview_frame=preview_frame,
        )

        Nexus._update_preview_geometry(harness)

        available_height = body.height() - 24
        reserved_height = preview_frame.height() + 340 + 125 + 20
        self.assertLessEqual(reserved_height, available_height)
        preview_frame.deleteLater()
        body.deleteLater()

    def test_double_click_clears_the_persisted_lock_for_reediting(self) -> None:
        message = MessageTab.__new__(MessageTab)
        message.settings = {"recipient_title_locked": True}
        field = IdentityLineEdit("A Letter")
        field.setReadOnly(True)

        message._unlock_identity_field(field, "title")

        self.assertFalse(message.settings["recipient_title_locked"])
        self.assertFalse(field.isReadOnly())
        field.deleteLater()

    def test_manual_url_change_clears_cloudflare_verification(self) -> None:
        message = types.SimpleNamespace(
            settings={
                "published_page_url": "https://letters.example.com/letters/old/",
                "published_public_path": "old",
                "published_at": "2026-08-13T12:00:00+00:00",
                "published_expires_at": "2099-09-12T12:00:00+00:00",
                "publication_provider": "cloudflare_r2",
                "publication_verified": True,
                "published_source_fingerprint": "old-source",
            },
            url_input=IdentityLineEdit(),
            status=QtWidgets.QLabel(),
            _persist_settings=lambda *, announce: True,
            published_page_url_changed=types.SimpleNamespace(emit=mock.Mock()),
        )

        saved = MessageTab.set_published_page_url(
            message,
            "https://example.com/manual",
        )

        self.assertTrue(saved)
        self.assertEqual(
            message.settings["published_page_url"],
            "https://example.com/manual",
        )
        self.assertFalse(message.settings["publication_verified"])
        self.assertEqual(message.settings["published_public_path"], "")
        message.url_input.deleteLater()
        message.status.deleteLater()

    def test_enter_commits_manual_url_to_the_canonical_settings_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            message = MessageTab(
                str(root),
                project_state=project_state,
            )
            message.title_input.setText("A Letter")
            message.name_input.setText("Recipient")
            message.url_input.setText("https://example.com/manual")

            message.url_input.returnPressed.emit()

            saved = SettingsStore(root).snapshot()
            self.assertTrue(message.url_input.isReadOnly())
            self.assertTrue(saved[IDENTITY_LOCK_KEYS["published_url"]])
            self.assertEqual(
                saved["published_page_url"],
                "https://example.com/manual",
            )
            message.deleteLater()
            self.app.processEvents()

    def test_loaded_letter_relocks_every_populated_identity_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {
                    "recipient_title": "A Loaded Letter",
                    "published_page_url": "https://example.com/loaded",
                    **{
                        lock_key: False
                        for lock_key in IDENTITY_LOCK_KEYS.values()
                    },
                }
            )

            message = MessageTab(
                str(root),
                project_state=project_state,
            )
            fields = (
                message.title_input,
                message.name_input,
                message.url_input,
            )
            self.assertTrue(all(field.isReadOnly() for field in fields))
            saved = SettingsStore(root).snapshot()
            self.assertTrue(
                all(
                    saved[lock_key]
                    for lock_key in IDENTITY_LOCK_KEYS.values()
                )
            )

            for field in fields:
                field.setReadOnly(False)
            SettingsStore(root).update_fields(
                {
                    lock_key: False
                    for lock_key in IDENTITY_LOCK_KEYS.values()
                }
            )

            message.refresh_from_disk()

            self.assertTrue(all(field.isReadOnly() for field in fields))
            reloaded = SettingsStore(root).snapshot()
            self.assertTrue(
                all(
                    reloaded[lock_key]
                    for lock_key in IDENTITY_LOCK_KEYS.values()
                )
            )
            message.deleteLater()
            self.app.processEvents()

    def test_recipient_edit_updates_canonical_project_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            existing = project_state.recipient_registry.get_or_create(
                "Amanda Miller",
                custom_capitalization=True,
            )
            original = project_state.establish_project(
                "Original Recipient",
                custom_capitalization=True,
            )
            message = types.SimpleNamespace(
                project_state=project_state,
                settings={},
                title_input=IdentityLineEdit("Letter Title"),
                name_input=IdentityLineEdit("Changed RECIPIENT"),
                url_input=IdentityLineEdit(""),
                status=QtWidgets.QLabel(),
                project_paths=ProjectPathResolver(root),
                _persist_settings=lambda *, announce: False,
            )

            MessageTab._save_settings(message)

            identity = project_state.identity
            reloaded = load_project_settings(root)
            self.assertNotEqual(identity.recipient_id, original.recipient_id)
            self.assertEqual(identity.project_id, original.project_id)
            self.assertEqual(
                identity.recipient_display_name,
                "Changed RECIPIENT",
            )
            self.assertEqual(
                reloaded["recipient_name"],
                "Changed RECIPIENT",
            )
            self.assertEqual(
                reloaded["recipient_display_name"],
                "Changed RECIPIENT",
            )
            self.assertEqual(
                project_state.recipient_registry.find_by_id(
                    original.recipient_id
                ).display_name,
                "Original Recipient",
            )

            message.name_input.setText("Amanda Miller")
            MessageTab._save_settings(message)

            identity = project_state.identity
            reloaded = load_project_settings(root)
            self.assertEqual(identity.recipient_id, existing.recipient_id)
            self.assertEqual(identity.project_id, original.project_id)
            self.assertEqual(
                reloaded["recipient_name"],
                "Amanda Miller",
            )
            self.assertEqual(
                reloaded["recipient_display_name"],
                "Amanda Miller",
            )
            message.title_input.deleteLater()
            message.name_input.deleteLater()
            message.url_input.deleteLater()
            message.status.deleteLater()

    def test_message_identity_fields_reject_reserved_values_with_popup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Original Recipient",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {"recipient_title": "Ordinary Letter"}
            )
            message = MessageTab(
                str(root),
                project_state=project_state,
            )

            message.title_input.setReadOnly(False)
            message.title_input.setText("NONE")
            with mock.patch("Message_tab.show_lettersmith_message") as warning:
                self.assertFalse(message._save_settings())
            warning.assert_called_once_with(
                message,
                "Invalid Title",
                "NONE is an invalid title.",
            )
            self.assertEqual(message.title_input.text(), "Ordinary Letter")

            message.name_input.setReadOnly(False)
            message.name_input.setText("Stock Davis 3")
            with mock.patch("Message_tab.show_lettersmith_message") as warning:
                self.assertFalse(message._save_settings())
            warning.assert_called_once_with(
                message,
                "Invalid Recipient",
                "Stock Davis 3 is an invalid recipient.",
            )
            self.assertEqual(
                message.name_input.text(),
                "Original Recipient",
            )
            saved = SettingsStore(root).snapshot()
            self.assertEqual(saved["recipient_title"], "Ordinary Letter")
            self.assertEqual(saved["recipient_name"], "Original Recipient")
            self.assertIsNone(
                project_state.recipient_registry.find_matching_recipient(
                    "Stock Davis 3"
                )
            )
            message.deleteLater()
            self.app.processEvents()

    def test_protected_message_render_does_not_resolve_or_copy_autosave(self) -> None:
        service = mock.Mock()
        service.can_save.return_value = False
        harness = types.SimpleNamespace(
            project_state=types.SimpleNamespace(is_project_ready=True),
            project_save_service=service,
        )

        MessageTab._sync_project_render_assets(harness)

        service.can_save.assert_called_once_with()
        service.current_context.assert_not_called()
        service.copy_workspace_file.assert_not_called()

    def test_protected_message_exit_skips_project_message_comparison(self) -> None:
        service = mock.Mock()
        service.can_save.return_value = False
        harness = types.SimpleNamespace(
            project_state=types.SimpleNamespace(is_project_ready=True),
            project_save_service=service,
        )

        self.assertTrue(
            MessageTab._project_message_matches_workspace(harness, "workspace")
        )
        service.current_context.assert_not_called()

    def test_tab_switch_autosave_uses_the_central_project_snapshot(self) -> None:
        service = mock.Mock()
        service.save_eligibility.return_value = types.SimpleNamespace(
            can_save=True,
            blocked_reason="",
        )
        flush_prompt_writer_state = mock.Mock(return_value=True)
        harness = types.SimpleNamespace(
            project_save_service=service,
            flush_prompt_writer_state=flush_prompt_writer_state,
        )

        note = Nexus._autosave_project_on_tab_switch(harness)

        service.save_workspace_snapshot.assert_called_once_with(
            reason="tab-switch"
        )
        flush_prompt_writer_state.assert_called_once_with()
        self.assertEqual(note, "Project autosaved.")

    def test_protected_tab_switch_skips_every_persistence_attempt(self) -> None:
        dirty = ProjectDirtyController()
        dirty.mark_changed("message")
        service = mock.Mock()
        flush_prompt_writer_state = mock.Mock(return_value=True)
        harness = types.SimpleNamespace(
            project_dirty=dirty,
            forge_tab=types.SimpleNamespace(is_protected_project=lambda: True),
            project_save_service=service,
            flush_prompt_writer_state=flush_prompt_writer_state,
        )

        note = Nexus._autosave_project_on_tab_switch(harness)

        flush_prompt_writer_state.assert_not_called()
        service.save_eligibility.assert_not_called()
        service.save_workspace_snapshot.assert_not_called()
        self.assertFalse(dirty.is_dirty)
        self.assertEqual(note, "Demonstration changes saved for this session.")

    def test_tab_switch_does_not_snapshot_stale_prompt_writer_state(self) -> None:
        service = mock.Mock()
        harness = types.SimpleNamespace(
            project_save_service=service,
            flush_prompt_writer_state=mock.Mock(return_value=False),
        )

        note = Nexus._autosave_project_on_tab_switch(harness)

        service.save_eligibility.assert_not_called()
        service.save_workspace_snapshot.assert_not_called()
        self.assertIn("Prompt Writer state could not be saved", note)

    def test_tab_switch_queues_snapshot_off_the_gui_thread(self) -> None:
        service = mock.Mock()
        service.save_eligibility.return_value = types.SimpleNamespace(
            can_save=True,
            blocked_reason="",
        )
        dirty = ProjectDirtyController()
        dirty.mark_changed("images")
        harness = types.SimpleNamespace(
            project_dirty=dirty,
            project_save_service=service,
            flush_prompt_writer_state=mock.Mock(return_value=True),
            _autosave_pool=object(),
            _autosave_active_revision=None,
            _autosave_pending_revision=None,
            _start_project_autosave=mock.Mock(),
        )

        note = Nexus._autosave_project_on_tab_switch(harness)

        harness._start_project_autosave.assert_called_once_with(dirty.revision)
        service.save_workspace_snapshot.assert_not_called()
        self.assertEqual(note, "Project autosave started.")

    def test_completed_autosave_coalesces_a_newer_dirty_revision(self) -> None:
        dirty = ProjectDirtyController()
        dirty.mark_changed("first")
        first_revision = dirty.revision
        dirty.mark_changed("newer")
        latest_revision = dirty.revision
        task = types.SimpleNamespace(error="", destination="saved")
        harness = types.SimpleNamespace(
            _autosave_active_revision=first_revision,
            _autosave_pending_revision=None,
            _autosave_tasks={first_revision: task},
            _shutdown_in_progress=False,
            _shutdown_complete=False,
            project_dirty=dirty,
            project_save_service=mock.Mock(),
            _start_project_autosave=mock.Mock(),
            status=mock.Mock(),
        )

        Nexus._project_autosave_completed(
            harness,
            first_revision,
            "saved",
        )

        harness._start_project_autosave.assert_called_once_with(latest_revision)
        self.assertTrue(dirty.is_dirty)
        harness.status.assert_not_called()

    def test_tab_switch_explicitly_releases_and_refreshes_images(self) -> None:
        def harness(current_index: int) -> mock.Mock:
            nexus = mock.Mock()
            nexus.page_stack.currentIndex.return_value = current_index
            nexus._tabswitch = None
            nexus._sound_preview_index = None
            nexus._autosave_project_on_tab_switch.return_value = ""
            nexus.tabbar.tabText.return_value = "Images"
            nexus.help_pop.isVisible.return_value = False
            return nexus

        with mock.patch("Nexus.QtCore.QTimer.singleShot"):
            leaving = harness(0)
            Nexus._apply_tab_state(leaving, 1, animate_page=False)
            leaving.image_tab.deactivate_for_tab_change.assert_called_once_with()

            entering = harness(1)
            Nexus._apply_tab_state(entering, 0, animate_page=False)
            entering.image_tab.activate_for_tab_change.assert_called_once_with()

    def test_incomplete_active_project_returns_without_becoming_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            identity = project_state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {"recipient_title": "Halfway Letter"}
            )

            self.assertFalse(
                evaluate_project_save_eligibility(root).can_save
            )
            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

            project_state.shutdown()
            restored = ProjectStateController(root)
            self.assertEqual(
                restored.initialize(),
                ApplicationState.PROJECT_READY,
            )
            self.assertEqual(restored.identity, identity)
            self.assertEqual(
                load_project_settings(root)["recipient_title"],
                "Halfway Letter",
            )
            self.assertEqual(SavedLetterCatalog(root).list_entries(), ())

    def test_shutdown_flushes_draft_before_project_state_closes(self) -> None:
        events: list[str] = []

        class ProjectState:
            def remove_listener(self, _listener: object) -> None:
                events.append("listener")

            def shutdown(self) -> None:
                events.append("project")

        class Tab:
            def shutdown(self, timeout_ms: int | None = None) -> bool:
                self.timeout_ms = timeout_ms
                events.append("forge-operation")
                return True

            def deactivate_for_tab_change(self) -> None:
                events.append("tab")

            def set_readiness_context_visible(self, _visible: bool) -> None:
                return None

            readiness_window = types.SimpleNamespace(shutdown=lambda: None)

        harness = types.SimpleNamespace(
            _shutdown_complete=False,
            _shutdown_in_progress=False,
            _project_tabs_initialized=True,
            _stop_curtain_preparation=lambda timeout_ms=None: (
                events.append("curtain") or True
            ),
            _on_project_state_transition=object(),
            _prompt_writer_win=None,
            _tray_icon=None,
            project_state=ProjectState(),
            flush_prompt_writer_state=lambda: events.append("prompt") or True,
            _release_forge_preview_files=lambda: None,
            _dispose_forge_preview=lambda: None,
            _finish_project_autosave_for_shutdown=lambda _timeout: (
                events.append("autosave") or True
            ),
            forge_tab=Tab(),
            sound_tab=types.SimpleNamespace(
                shutdown=lambda timeout_ms=None: events.append("sound") or True,
            ),
            message_tab=types.SimpleNamespace(
                shutdown=lambda: events.append("message")
            ),
            image_tab=types.SimpleNamespace(
                shutdown=lambda: events.append("image")
            ),
        )

        Nexus.shutdown(harness)

        self.assertLess(events.index("curtain"), events.index("project"))
        self.assertLess(events.index("prompt"), events.index("autosave"))
        self.assertLess(events.index("forge-operation"), events.index("project"))
        self.assertLess(events.index("prompt"), events.index("project"))
        self.assertLess(events.index("sound"), events.index("project"))
        self.assertLess(events.index("message"), events.index("project"))
        self.assertLess(events.index("image"), events.index("project"))

    def test_shutdown_autosave_drains_the_latest_dirty_revision(self) -> None:
        dirty = ProjectDirtyController()
        dirty.mark_changed("first")
        first_revision = dirty.revision
        dirty.mark_changed("newer")
        latest_revision = dirty.revision
        service = mock.Mock()
        first_task = types.SimpleNamespace(error="", destination="first")
        latest_task = types.SimpleNamespace(error="", destination="latest")
        harness = types.SimpleNamespace(
            _autosave_pool=types.SimpleNamespace(
                waitForDone=mock.Mock(return_value=True)
            ),
            _autosave_active_revision=first_revision,
            _autosave_pending_revision=latest_revision,
            _autosave_tasks={first_revision: first_task},
            project_save_service=service,
            project_dirty=dirty,
            forge_tab=types.SimpleNamespace(
                is_protected_project=lambda: False
            ),
        )

        def start_latest(revision: int) -> None:
            harness._autosave_active_revision = revision
            harness._autosave_tasks[revision] = latest_task

        harness._start_project_autosave = mock.Mock(side_effect=start_latest)

        self.assertTrue(
            Nexus._finish_project_autosave_for_shutdown(harness, 500)
        )

        harness._start_project_autosave.assert_called_once_with(latest_revision)
        self.assertEqual(
            service.finish_deferred_workspace_snapshot.call_count,
            2,
        )
        self.assertFalse(dirty.is_dirty)
        self.assertIsNone(harness._autosave_active_revision)
        self.assertIsNone(harness._autosave_pending_revision)

    def test_restore_release_propagates_sound_shutdown_failure_to_forge(self) -> None:
        report_failure = mock.Mock()
        harness = types.SimpleNamespace(
            _autosave_active_revision=None,
            message_tab=types.SimpleNamespace(
                prepare_for_project_restore=mock.Mock()
            ),
            image_tab=types.SimpleNamespace(
                prepare_for_project_restore=mock.Mock()
            ),
            sound_tab=types.SimpleNamespace(
                prepare_for_project_restore=mock.Mock(
                    side_effect=RuntimeError("analysis did not stop")
                )
            ),
            forge_tab=types.SimpleNamespace(
                report_project_file_release_failure=report_failure
            ),
        )

        released = Nexus._release_project_files_for_restore(harness)

        self.assertFalse(released)
        report_failure.assert_called_once_with("analysis did not stop")

    def test_message_tab_keeps_its_project_path_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            project_paths = ProjectPathResolver(root)

            message = MessageTab(
                str(root),
                project_state=project_state,
                project_paths=project_paths,
            )

            self.assertIs(message.project_paths, project_paths)
            standard_size = BUTTON_TIER_STYLES[ButtonTier.STANDARD].size
            self.assertEqual(message.btn.size(), standard_size)
            self.assertEqual(message.edit_btn.size(), standard_size)
            self.assertEqual(message.revisions_btn.size(), standard_size)
            message.deleteLater()
            self.app.processEvents()

    def test_message_overlay_changes_coalesce_persistence_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            self._write_wall(root)
            message = MessageTab(str(root), project_state=project_state)
            self.assertTrue(message._wait_for_message_render())
            self.app.processEvents()
            rendered = QtGui.QImage(str(message._png_path()))
            self.assertEqual((rendered.width(), rendered.height()), (2048, 3072))
            changed = QtTest.QSignalSpy(message.project_changed)

            with mock.patch.object(message, "_generate_image") as render:
                message._set_overlay_opacity(31)
                message._set_overlay_opacity(42)
                message._set_overlay_opacity(53)

                self.assertTrue(message._overlay_render_timer.isActive())
                self.assertNotEqual(
                    SettingsStore(root).get("message_overlay_opacity"),
                    53,
                )
                message._flush_overlay_update()

            self.assertEqual(
                SettingsStore(root).get("message_overlay_opacity"),
                53,
            )
            render.assert_called_once_with("<p><br></p>")
            self.assertEqual(changed.count(), 1)
            self.assertTrue(message.shutdown())
            message.deleteLater()
            self.app.processEvents()

    def test_message_render_worker_commits_only_latest_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            self._write_wall(root)
            message = MessageTab(str(root), project_state=project_state)
            self.assertTrue(message._wait_for_message_render())
            self.app.processEvents()
            first_started = threading.Event()
            release_first = threading.Event()

            def controlled_render(
                html: str,
                _wall_path: Path,
                staged_path: Path,
                _preset: str,
                _overlay_opacity: int,
            ) -> bool:
                if html == "first":
                    first_started.set()
                    release_first.wait(2.0)
                staged_path.write_bytes(html.encode("utf-8"))
                return True

            with mock.patch.object(
                message_module,
                "_render_message_png",
                side_effect=controlled_render,
            ):
                first_revision = message._generate_image("first")
                self.assertIsNotNone(first_revision)
                self.assertTrue(first_started.wait(1.0))
                latest_revision = message._generate_image("latest")
                self.assertGreater(latest_revision, first_revision)
                release_first.set()
                self.assertTrue(message._wait_for_message_render())

            self.assertEqual(message._png_path().read_bytes(), b"latest")
            self.app.processEvents()
            self.assertTrue(message.shutdown())
            message.deleteLater()
            self.app.processEvents()

    def test_message_restore_fails_closed_when_render_worker_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_state = ProjectStateController(root)
            project_state.initialize()
            project_state.establish_project(
                "Recipient",
                custom_capitalization=True,
            )
            self._write_wall(root)
            message = MessageTab(str(root), project_state=project_state)
            self.assertTrue(message._wait_for_message_render())
            original = message._png_path().read_bytes()
            worker_started = threading.Event()
            release_worker = threading.Event()

            def blocked_render(
                _html: str,
                _wall_path: Path,
                staged_path: Path,
                _preset: str,
                _overlay_opacity: int,
            ) -> bool:
                worker_started.set()
                release_worker.wait(2.0)
                staged_path.write_bytes(b"stale")
                return True

            with mock.patch.object(
                message_module,
                "_render_message_png",
                side_effect=blocked_render,
            ):
                message._generate_image("stale")
                self.assertTrue(worker_started.wait(1.0))
                with self.assertRaisesRegex(
                    RuntimeError,
                    "did not stop before restore",
                ):
                    message.prepare_for_project_restore(timeout_ms=1)
                release_worker.set()
                self.assertTrue(message._wait_for_message_render())

            self.assertEqual(message._png_path().read_bytes(), original)
            self.assertTrue(message.shutdown())
            message.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
