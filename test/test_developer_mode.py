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
    _save_current_letter_if_ready,
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

    def test_dialog_uses_supplied_developer_artwork(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        dialog = DeveloperModeDialog(repository)

        self.assertEqual(DEVELOPER_MODE_SHORTCUT, "Ctrl+Alt+Shift+D")
        self.assertEqual(
            dialog.developer_button.accessibleName(),
            "Developer Mode",
        )
        self.assertEqual(dialog.developer_button.text(), "")
        self.assertFalse(dialog.developer_button.icon().isNull())
        self.assertEqual(
            dialog.developer_button.iconSize(),
            QtCore.QSize(840, 473),
        )
        dialog.deleteLater()

    def test_reset_clears_active_project_github_and_theme_choice(self) -> None:
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

            self.assertEqual(result.saved_letter, saved.parent)
            save_current.assert_called_once_with(root.resolve(), None)
            self.assertEqual(credentials.clear_count, 1)
            self.assertIsNone(credentials.stored)
            self.assertFalse(active_page.exists())
            self.assertTrue(saved.is_file())
            current = settings.snapshot()
            self.assertEqual(current.get("application_theme"), "")
            self.assertEqual(current.get("theme_family"), "")
            self.assertEqual(current.get("curtain_style"), "average_color")
            self.assertTrue(
                startup_theme_prompt_required(settings, force_prompt=False)
            )
            self.assertEqual(controller.state, ApplicationState.RECIPIENT_REQUIRED)

    def test_ready_letter_is_built_and_recorded_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Ready</p>", encoding="utf-8")
            play_dir = root / "output" / "Play" / "Recipient" / "Letter"
            play_dir.mkdir(parents=True)
            readiness = types.SimpleNamespace(
                can_preview=True,
                missing_items=(),
            )
            window = types.SimpleNamespace(
                flush_prompt_writer_state=mock.Mock(return_value=True),
                _release_forge_preview_files=mock.Mock(),
            )

            with (
                mock.patch(
                    "developermode.evaluate_readiness",
                    return_value=readiness,
                ),
                mock.patch(
                    "developermode.generate.ensure_play_bundle",
                    return_value=(play_dir, True),
                ) as build,
                mock.patch("developermode.update_saved_metadata") as metadata,
                mock.patch(
                    "developermode.record_saved_letter_activity"
                ) as activity,
            ):
                saved, reason = _save_current_letter_if_ready(root, window)

            self.assertEqual(saved, play_dir.resolve())
            self.assertEqual(reason, "")
            window.flush_prompt_writer_state.assert_called_once_with()
            window._release_forge_preview_files.assert_called_once_with()
            build.assert_called_once_with(
                root,
                message_html="<p>Ready</p>",
                force=True,
            )
            metadata.assert_called_once_with(play_dir, root, readiness)
            activity.assert_called_once_with(play_dir)

    def test_failed_reset_restores_github_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored = types.SimpleNamespace(account=object(), token=object())
            credentials = _CredentialStore(stored)

            with mock.patch(
                "command.start_developer_reset",
                side_effect=RuntimeError("reset failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "reset failed"):
                    perform_developer_reset(
                        root,
                        credential_store=credentials,
                        save_current=lambda _root, _window: (None, "not ready"),
                    )

            self.assertEqual(credentials.clear_count, 1)
            self.assertEqual(credentials.saved, [stored])
            self.assertIs(credentials.stored, stored)


if __name__ == "__main__":
    unittest.main()
