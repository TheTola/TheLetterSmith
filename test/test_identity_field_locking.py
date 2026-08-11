from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtCore, QtGui, QtWidgets

from Message_tab import IDENTITY_LOCK_KEYS, IdentityLineEdit, MessageTab
from Nexus import Nexus
from project_paths import ProjectPathResolver
from project_state import (
    ApplicationState,
    ProjectStateController,
    load_project_settings,
)
from readiness import evaluate_project_save_eligibility
from saved_letters import SavedLetterCatalog
from settings_store import SettingsStore


class IdentityFieldLockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_lock_keys_cover_title_recipient_and_url(self) -> None:
        self.assertEqual(
            set(IDENTITY_LOCK_KEYS),
            {"title", "recipient", "published_url"},
        )
        self.assertFalse(MessageTab._setting_bool("false"))
        self.assertTrue(MessageTab._setting_bool("true"))

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
        self.assertIn("#10263b", field.styleSheet())
        self.assertIn("#78b9dd", field.styleSheet())
        field.deleteLater()

    def test_styled_identity_field_preserves_its_text_height(self) -> None:
        field = IdentityLineEdit("The Silver Lettersmith")

        MessageTab._style_identity_field(field)

        self.assertGreaterEqual(field.minimumHeight(), field.sizeHint().height())
        self.assertGreaterEqual(
            field.minimumHeight(),
            field.fontMetrics().height() + 10,
        )
        field.deleteLater()

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

    def test_tab_switch_autosave_uses_the_central_project_snapshot(self) -> None:
        service = mock.Mock()
        service.save_eligibility.return_value = types.SimpleNamespace(
            can_save=True,
            blocked_reason="",
        )
        harness = types.SimpleNamespace(project_save_service=service)

        note = Nexus._autosave_project_on_tab_switch(harness)

        service.save_workspace_snapshot.assert_called_once_with(
            reason="tab-switch"
        )
        self.assertEqual(note, "Project autosaved.")

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
            def deactivate_for_tab_change(self) -> None:
                events.append("tab")

            def set_readiness_context_visible(self, _visible: bool) -> None:
                return None

            readiness_window = types.SimpleNamespace(shutdown=lambda: None)

        harness = types.SimpleNamespace(
            _shutdown_complete=False,
            _shutdown_in_progress=False,
            _project_tabs_initialized=True,
            _on_project_state_transition=object(),
            _prompt_writer_win=None,
            _tray_icon=None,
            project_state=ProjectState(),
            flush_prompt_writer_state=lambda: events.append("prompt") or True,
            _release_forge_preview_files=lambda: None,
            forge_tab=Tab(),
            sound_tab=types.SimpleNamespace(
                deactivate_for_tab_change=lambda: events.append("sound"),
                release_current_file_handle=lambda: None,
            ),
            message_tab=types.SimpleNamespace(
                shutdown=lambda: events.append("message")
            ),
            image_tab=types.SimpleNamespace(
                shutdown=lambda: events.append("image")
            ),
        )

        Nexus.shutdown(harness)

        self.assertLess(events.index("prompt"), events.index("project"))
        self.assertLess(events.index("message"), events.index("project"))
        self.assertLess(events.index("image"), events.index("project"))

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
            message.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
