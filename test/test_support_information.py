from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

import application_identity
from about_dialog import (
    AboutLetterSmithDialog,
    SupportInformationCopiedDialog,
    SupportInformationOptionsDialog,
)
from project_paths import application_paths
from support_information import (
    MAX_LOG_BYTES,
    SupportReportOptions,
    SupportRuntimeContext,
    UNAVAILABLE,
    build_support_report,
    collect_diagnostic_information,
    recent_diagnostic_excerpt,
    sanitize_support_text,
)
from ui_theme import BASIC_DARK_THEME, THEMES


class SupportInformationLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix=".support-information-",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = SupportRuntimeContext(active_theme="Cyber Forge")

    def test_metadata_is_centralized_and_placeholders_are_explicit(self) -> None:
        self.assertEqual(application_identity.CREATOR_NAME, "Oluwatola Ayedun")
        self.assertEqual(application_identity.PLACEHOLDER_COLOR, "#FF4F00")
        self.assertTrue(application_identity.PUBLIC_METADATA)
        self.assertTrue(
            all(
                "PLACEHOLDER" in value
                for value in application_identity.PUBLIC_METADATA.values()
            )
        )
        about_source = Path(
            __import__("about_dialog").__file__,
        ).read_text(encoding="utf-8")
        self.assertNotIn("SUPPORT EMAIL NOT CONFIGURED", about_source)
        self.assertFalse(application_identity.is_placeholder("support@example.com"))

    def test_creator_is_public_without_allowing_private_home_paths(self) -> None:
        from release import build_release

        patterns = build_release._private_text_patterns(
            include_secret_literals=False,
        )
        self.assertFalse(
            any(
                pattern.search(application_identity.CREATOR_NAME)
                for pattern in patterns
            )
        )
        self.assertTrue(
            any(pattern.search(str(Path.home())) for pattern in patterns)
        )

    def test_basic_report_omits_both_optional_sections(self) -> None:
        report = build_support_report(
            self.root,
            SupportReportOptions(False, False),
            self.context,
            computer_collector=mock.Mock(
                side_effect=AssertionError("computer collector was called")
            ),
            diagnostic_collector=mock.Mock(
                side_effect=AssertionError("diagnostic collector was called")
            ),
        )

        self.assertIn("Application: Letter Smith", report)
        self.assertIn("Created by: Oluwatola Ayedun", report)
        self.assertIn("Active Theme: Cyber Forge", report)
        self.assertNotIn("COMPUTER INFORMATION", report)
        self.assertNotIn("DIAGNOSTIC INFORMATION", report)

    def test_optional_sections_follow_the_selected_options(self) -> None:
        computer = lambda *_args: {"GPU Model": "Example GPU"}
        diagnostics = lambda *_args: ({"FFmpeg": "Available"}, ())

        computer_only = build_support_report(
            self.root,
            SupportReportOptions(True, False),
            self.context,
            computer_collector=computer,
            diagnostic_collector=diagnostics,
        )
        diagnostic_only = build_support_report(
            self.root,
            SupportReportOptions(False, True),
            self.context,
            computer_collector=computer,
            diagnostic_collector=diagnostics,
        )
        both = build_support_report(
            self.root,
            SupportReportOptions(True, True),
            self.context,
            computer_collector=computer,
            diagnostic_collector=diagnostics,
        )

        self.assertIn("COMPUTER INFORMATION", computer_only)
        self.assertNotIn("DIAGNOSTIC INFORMATION", computer_only)
        self.assertNotIn("COMPUTER INFORMATION", diagnostic_only)
        self.assertIn("DIAGNOSTIC INFORMATION", diagnostic_only)
        self.assertIn("COMPUTER INFORMATION", both)
        self.assertIn("DIAGNOSTIC INFORMATION", both)

    def test_unavailable_collector_does_not_abort_the_report(self) -> None:
        report = build_support_report(
            self.root,
            SupportReportOptions(True, True),
            self.context,
            computer_collector=mock.Mock(side_effect=OSError("no hardware API")),
            diagnostic_collector=mock.Mock(side_effect=ValueError("bad log")),
        )

        self.assertEqual(report.count("Collection Status: Unavailable"), 2)
        self.assertIn("PRIVACY", report)

    def test_sanitizer_redacts_secrets_paths_and_identifiers(self) -> None:
        unsafe = "\n".join(
            (
                "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
                "api_key=top-secret-value",
                "oauth_token=oauth-secret-value",
                "Authorization: Bearer bearer-secret-value",
                "Cookie: sid=cookie-secret-value; mode=private",
                r"C:\Users\Alice\Documents\Letter Smith\draft.txt",
                "/Users/alice/Documents/draft.txt",
                "/home/bob/private/draft.txt",
                "MAC: 00:11:22:33:44:55",
                "IP: 192.168.1.25",
                "device uuid: 123e4567-e89b-42d3-a456-426614174000",
            )
        )

        safe = sanitize_support_text(unsafe, project_root=self.root)

        for forbidden in (
            "ghp_",
            "top-secret-value",
            "oauth-secret-value",
            "bearer-secret-value",
            "cookie-secret-value",
            r"C:\Users\Alice",
            "/Users/alice",
            "/home/bob",
            "00:11:22:33:44:55",
            "192.168.1.25",
            "123e4567-e89b-42d3-a456-426614174000",
        ):
            self.assertNotIn(forbidden, safe)
        self.assertIn("<USER_HOME>", safe)
        self.assertIn("[REDACTED]", safe)

    def test_report_excludes_injected_personal_and_authentication_values(self) -> None:
        message = "Private letter words that must never be copied"
        recipient = "Alex Private Recipient"
        diagnostics = {
            "Recent Diagnostic Excerpt": (
                f"message_text: {message}\n"
                f"recipient_name: {recipient}\n"
                "Authorization: Bearer never-copy-this"
            )
        }
        report = build_support_report(
            self.root,
            SupportReportOptions(False, True),
            self.context,
            diagnostic_collector=lambda *_args: (
                diagnostics,
                (message, recipient),
            ),
        )

        self.assertNotIn(message, report)
        self.assertNotIn(recipient, report)
        self.assertNotIn("never-copy-this", report)
        self.assertIn("[REDACTED PERSONAL CONTENT]", report)

    def test_missing_malformed_and_oversized_logs_are_safe_and_bounded(self) -> None:
        self.assertEqual(recent_diagnostic_excerpt(self.root), "")
        logs = application_paths(self.root).logs_root
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "lettersmith.log").write_bytes(b"\xff\xfeERROR malformed\n")
        malformed = recent_diagnostic_excerpt(self.root)
        self.assertIn("ERROR malformed", malformed)

        oversized_line = b"ERROR bounded diagnostic event\n"
        (logs / "lettersmith.log").write_bytes(oversized_line * 2000)
        bounded = recent_diagnostic_excerpt(self.root)
        self.assertLessEqual(len(bounded.encode("utf-8")), MAX_LOG_BYTES)
        self.assertLessEqual(len(bounded.splitlines()), 41)

    def test_unavailable_dependencies_are_reported_without_crashing(self) -> None:
        with (
            mock.patch(
                "support_information.importlib.util.find_spec",
                return_value=None,
            ),
            mock.patch("support_information.shutil.which", return_value=None),
            mock.patch("support_information.evaluate_readiness", return_value=None),
        ):
            diagnostics, _private = collect_diagnostic_information(
                self.root,
                self.context,
            )

        self.assertEqual(diagnostics["Audio Backend"], UNAVAILABLE)
        self.assertEqual(diagnostics["DOCX Import"], UNAVAILABLE)
        self.assertEqual(diagnostics["PDF Import"], UNAVAILABLE)
        self.assertEqual(diagnostics["Git"], UNAVAILABLE)


class SupportInformationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix=".support-ui-",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _host(self, definition=BASIC_DARK_THEME) -> QtWidgets.QWidget:
        host = QtWidgets.QWidget()
        host.project_root = str(self.root)
        host.theme_service = types.SimpleNamespace(
            current=definition,
            tokens=definition.tokens,
        )
        self.addCleanup(host.deleteLater)
        return host

    def test_options_dialog_is_accessible_and_cancel_copies_nothing(self) -> None:
        clipboard = self.app.clipboard()
        clipboard.setText("unchanged", QtGui.QClipboard.Clipboard)
        dialog = SupportInformationOptionsDialog(self._host())
        self.addCleanup(dialog.deleteLater)

        self.assertTrue(dialog.computer_checkbox.isChecked())
        self.assertTrue(dialog.diagnostic_checkbox.isChecked())
        self.assertTrue(dialog.copy_button.isDefault())
        self.assertTrue(dialog.computer_checkbox.toolTip())
        self.assertTrue(dialog.diagnostic_checkbox.toolTip())
        dialog.cancel_button.click()

        self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
        self.assertEqual(
            clipboard.text(QtGui.QClipboard.Clipboard),
            "unchanged",
        )

    def test_about_opens_options_and_cancel_stops_generation(self) -> None:
        dialog = AboutLetterSmithDialog(
            self._host(),
            application=self._host(),
        )
        self.addCleanup(dialog.deleteLater)
        with (
            mock.patch("about_dialog.SupportInformationOptionsDialog") as options,
            mock.patch.object(dialog, "_start_report") as start_report,
        ):
            options.return_value.exec.return_value = QtWidgets.QDialog.Rejected
            dialog._choose_support_information()

        options.assert_called_once_with(dialog)
        start_report.assert_not_called()

    def test_copy_places_one_report_on_clipboard_and_shows_instructions(self) -> None:
        host = self._host()
        dialog = AboutLetterSmithDialog(host, application=host)
        self.addCleanup(dialog.deleteLater)
        report = "LETTER SMITH SUPPORT INFORMATION\nApplication: Letter Smith\n"
        dialog._pending_report = report

        with mock.patch.object(
            SupportInformationCopiedDialog,
            "exec",
            return_value=QtWidgets.QDialog.Accepted,
        ) as success:
            dialog._copy_pending_report()

        self.assertEqual(self.app.clipboard().text(), report)
        self.assertEqual(dialog._pending_report, "")
        success.assert_called_once()

        success_dialog = SupportInformationCopiedDialog(host)
        self.addCleanup(success_dialog.deleteLater)
        message_text = " ".join(
            label.text()
            for label in success_dialog.findChildren(QtWidgets.QLabel)
        )
        self.assertIn("paste the copied information into the email", message_text)
        self.assertIn("PLACEHOLDER", success_dialog.support_destination_label.text())
        self.assertIn(
            application_identity.PLACEHOLDER_COLOR,
            success_dialog.support_destination_label.styleSheet(),
        )

    def test_placeholder_color_is_fixed_across_every_theme(self) -> None:
        for definition in THEMES.values():
            host = self._host(definition)
            dialog = AboutLetterSmithDialog(host, application=host)
            self.addCleanup(dialog.deleteLater)
            metadata = [
                label
                for label in dialog.findChildren(QtWidgets.QLabel)
                if label.property("aboutMetadata")
            ]
            support = next(
                label for label in metadata if label.accessibleName().startswith("Support:")
            )
            creator = next(
                label
                for label in dialog.findChildren(QtWidgets.QLabel)
                if label.objectName() == "AboutCreator"
            )

            self.assertIn(application_identity.PLACEHOLDER_COLOR, support.text())
            self.assertIn("Created by Oluwatola Ayedun", creator.text())
            self.assertNotIn(application_identity.PLACEHOLDER_COLOR, creator.text())


if __name__ == "__main__":
    unittest.main()
