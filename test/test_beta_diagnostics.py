from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from beta_diagnostics import (
    BetaDiagnosticsController,
    beta_diagnostics_report_directory,
    request_beta_diagnostics_consent,
    store_beta_diagnostics_consent,
    widget_identifier,
)
from project_paths import ApplicationPaths


class BetaDiagnosticsParentTraversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def test_widget_identifier_ignores_shadowed_parent_attribute(self) -> None:
        window = QtWidgets.QWidget()
        window.setObjectName("NexusWindow")
        title_bar = QtWidgets.QWidget(window)
        title_bar.setObjectName("NexusTitleBar")
        title_bar.parent = window
        maximize = QtWidgets.QPushButton(title_bar)
        maximize.setObjectName("maximizeButton")

        identifier = widget_identifier(maximize)

        self.assertIn("NexusWindow", identifier)
        self.assertIn("NexusTitleBar", identifier)
        self.assertIn("maximizeButton", identifier)


class BetaDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix=".beta-diagnostics-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = ApplicationPaths.for_runtime(
            self.root / "resources",
            home=self.root / "home",
            temporary_base=self.root / "temporary",
            platform_name="win32",
            environ={"LOCALAPPDATA": str(self.root / "local-app-data")},
        )
        self.paths.ensure_writable_roots()

    @staticmethod
    def _events(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_consent_is_persisted_without_reprompting(self) -> None:
        store_beta_diagnostics_consent(self.paths, True)
        with mock.patch.object(
            QtWidgets.QMessageBox,
            "exec",
            side_effect=AssertionError("consent dialog reopened"),
        ):
            self.assertTrue(request_beta_diagnostics_consent(self.paths))

    def test_interactions_never_store_typed_content(self) -> None:
        controller = BetaDiagnosticsController(self.application, self.paths)
        self.addCleanup(controller.close)

        editor = QtWidgets.QLineEdit()
        editor.setObjectName("messageEditor")
        controller._instrument_widget(editor)
        private_text = "Private recipient text and ghp_exampleSecretToken123456"
        editor.setText(private_text)
        editor.textEdited.emit(private_text)

        button = QtWidgets.QPushButton("Save")
        button.setObjectName("saveButton")
        controller._instrument_widget(button)
        button.click()
        controller._finish_action("action-1")
        controller.close()

        report = controller.report_path.read_text(encoding="utf-8")
        self.assertNotIn(private_text, report)
        self.assertNotIn("ghp_exampleSecretToken123456", report)
        events = self._events(controller.report_path)
        interaction = next(
            event for event in events if event["event"] == "field_interaction"
        )
        self.assertEqual(interaction["length"], len(private_text))
        self.assertNotIn("text", interaction)
        action = next(event for event in events if event["event"] == "button_clicked")
        self.assertIn("saveButton", action["control"])
        result = next(event for event in events if event["event"] == "action_result")
        self.assertEqual(result["result"], "pass")

    def test_protected_fields_do_not_store_length(self) -> None:
        controller = BetaDiagnosticsController(self.application, self.paths)
        self.addCleanup(controller.close)
        editor = QtWidgets.QLineEdit()
        editor.setObjectName("githubToken")
        editor.setEchoMode(QtWidgets.QLineEdit.Password)
        controller._instrument_widget(editor)
        editor.setText("secret-value")
        editor.textEdited.emit("secret-value")
        controller.close()

        interaction = next(
            event
            for event in self._events(controller.report_path)
            if event["event"] == "field_interaction"
        )
        self.assertTrue(interaction["protected"])
        self.assertNotIn("length", interaction)
        self.assertNotIn("secret-value", controller.report_path.read_text(encoding="utf-8"))

    def test_errors_fail_the_session_without_storing_error_message(self) -> None:
        controller = BetaDiagnosticsController(self.application, self.paths)
        self.addCleanup(controller.close)
        logging_secret = "recipient-private-error-value"
        logger = __import__("logging").getLogger("lettersmith.beta.test")
        logger.error(logging_secret)
        controller.close()

        report = controller.report_path.read_text(encoding="utf-8")
        self.assertNotIn(logging_secret, report)
        events = self._events(controller.report_path)
        error = next(event for event in events if event["event"] == "application_error")
        self.assertEqual(error["level"], "ERROR")
        self.assertIn("fingerprint", error)
        finished = events[-1]
        self.assertEqual(finished["event"], "session_finished")
        self.assertEqual(finished["status"], "fail")
        self.assertLess(finished["score"], 100)

    def test_previous_unfinished_session_is_marked_failed(self) -> None:
        directory = beta_diagnostics_report_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        previous = directory / "beta-session-20260831T000000Z-previous.jsonl"
        previous.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "timestamp": "2026-08-31T00:00:00.000Z",
                    "session_id": "previous",
                    "event": "session_started",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        controller = BetaDiagnosticsController(self.application, self.paths)
        self.addCleanup(controller.close)
        controller.close()

        recovered = self._events(previous)[-1]
        self.assertEqual(recovered["event"], "session_finished")
        self.assertEqual(recovered["status"], "fail")
        self.assertEqual(recovered["reason"], "unclean_exit_detected_on_next_start")


if __name__ == "__main__":
    unittest.main()
