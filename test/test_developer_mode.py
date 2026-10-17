from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtCore, QtWidgets

if "keyring" not in sys.modules:
    keyring = types.ModuleType("keyring")
    keyring.get_password = lambda *_args, **_kwargs: None
    keyring.set_password = lambda *_args, **_kwargs: None
    keyring.delete_password = lambda *_args, **_kwargs: None
    keyring_errors = types.ModuleType("keyring.errors")
    keyring_errors.KeyringError = type("KeyringError", (Exception,), {})
    keyring_errors.PasswordDeleteError = type(
        "PasswordDeleteError",
        (keyring_errors.KeyringError,),
        {},
    )
    keyring.errors = keyring_errors
    sys.modules["keyring"] = keyring
    sys.modules["keyring.errors"] = keyring_errors

from developermode import (
    DEVELOPER_MODE_SHORTCUT,
    DeveloperModeDialog,
    run_developer_mode,
    perform_developer_reset,
)
from project_state import ApplicationState, ProjectStateController
from settings_store import SettingsStore
from startup_theme import startup_theme_prompt_required


class _CredentialStore:
    def __init__(self, stored: object | None) -> None:
        self.stored = stored
        self.clear_count = 0
        self.saved: list[object] = []

    def load(self) -> object | None:
        return self.stored

    def clear(self) -> None:
        self.clear_count += 1
        self.stored = None

    def save(self, stored: object) -> None:
        self.saved.append(stored)
        self.stored = stored


class DeveloperModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            sys.argv
        )

    def test_dialog_names_the_exact_preference_scope(self) -> None:
        dialog = DeveloperModeDialog(Path(self.temp_dir.name) if hasattr(self, "temp_dir") else Path.cwd())
        self.assertEqual(DEVELOPER_MODE_SHORTCUT, "Ctrl+Alt+Shift+D")
        self.assertEqual(dialog.windowTitle(), "Reset Appearance Preferences")
        self.assertEqual(dialog.developer_button.text(), "Reset Appearance Preferences")
        dialog.deleteLater()

    def test_reset_only_changes_appearance_and_keeps_projects_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_page = root / "gallery" / "user" / "pages" / "cover.png"
            active_page.parent.mkdir(parents=True)
            active_page.write_bytes(b"active")
            saved = root / "output" / "Play" / "Saved" / "index.html"
            saved.parent.mkdir(parents=True)
            saved.write_text("saved", encoding="utf-8")

            settings = SettingsStore(root)
            settings.update_fields(
                {
                    "application_theme": "celestial_rose",
                    "theme_family": "rose",
                    "curtain_style": "pure_white",
                }
            )
            controller = ProjectStateController(root)
            controller.initialize()
            controller.establish_project("Amanda Miller")
            settings.update_fields({"recipient_title": "Ready Letter"})

            stored = types.SimpleNamespace(account=object(), token=object())
            credentials = _CredentialStore(stored)
            save_current = mock.Mock(return_value=(saved.parent, ""))

            result = perform_developer_reset(
                root,
                project_state=controller,
                credential_store=credentials,
                save_current=save_current,
            )

            self.assertIsNone(result.saved_letter)
            save_current.assert_not_called()
            self.assertEqual(credentials.clear_count, 0)
            self.assertIs(credentials.stored, stored)
            self.assertTrue(active_page.exists())
            self.assertTrue(saved.is_file())
            current = settings.snapshot()
            self.assertEqual(current.get("application_theme"), "")
            self.assertEqual(current.get("theme_family"), "")
            self.assertEqual(current.get("curtain_style"), "pure_white")
            self.assertTrue(
                startup_theme_prompt_required(settings, force_prompt=False)
            )
            self.assertEqual(controller.state, ApplicationState.PROJECT_READY)

    def test_cancel_and_write_failure_change_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SettingsStore(root)
            before = store.snapshot()
            credentials = _CredentialStore(object())
            with mock.patch.object(DeveloperModeDialog, "exec", return_value=QtWidgets.QDialog.Rejected):
                self.assertFalse(run_developer_mode(project_root=root, credential_store=credentials))
            self.assertEqual(store.snapshot(), before)
            with mock.patch.object(SettingsStore, "update_fields", side_effect=OSError("read only")):
                with self.assertRaises(OSError):
                    perform_developer_reset(root, credential_store=credentials)
            self.assertEqual(credentials.clear_count, 0)
            self.assertEqual(credentials.saved, [])
            self.assertEqual(store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
