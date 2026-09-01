from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from PySide6 import QtCore, QtWidgets

from application_identity import APPLICATION_VERSION
from project_paths import ApplicationPaths
from transactional_io import atomic_write_json


BETA_DIAGNOSTICS_SCHEMA_VERSION = 1
BETA_DIAGNOSTICS_CONSENT_FILE = "beta-diagnostics.json"
BETA_DIAGNOSTICS_DIRECTORY = "beta-diagnostics"
MAX_REPORT_FILES = 20
MAX_REPORT_BYTES = 25 * 1024 * 1024
ACTION_SETTLE_MS = 1500

_SAFE_OBJECT_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_PROTECTED_FIELD_NAME = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|device.?code)",
    flags=re.IGNORECASE,
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def beta_diagnostics_consent_path(paths: ApplicationPaths) -> Path:
    return paths.settings_root / BETA_DIAGNOSTICS_CONSENT_FILE


def beta_diagnostics_report_directory(paths: ApplicationPaths) -> Path:
    return paths.logs_root / BETA_DIAGNOSTICS_DIRECTORY


def _stored_consent(paths: ApplicationPaths) -> bool | None:
    path = beta_diagnostics_consent_path(paths)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != BETA_DIAGNOSTICS_SCHEMA_VERSION
        or not isinstance(value.get("enabled"), bool)
    ):
        return None
    return value["enabled"]


def store_beta_diagnostics_consent(
    paths: ApplicationPaths,
    enabled: bool,
) -> Path:
    return atomic_write_json(
        beta_diagnostics_consent_path(paths),
        {
            "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
            "enabled": bool(enabled),
            "acknowledged_at": _utc_timestamp(),
        },
    )


def request_beta_diagnostics_consent(
    paths: ApplicationPaths,
    *,
    parent: QtWidgets.QWidget | None = None,
) -> bool:
    override = os.environ.get("LETTER_SMITH_BETA_DIAGNOSTICS", "").strip().casefold()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False

    stored = _stored_consent(paths)
    if stored is not None:
        return stored

    dialog = QtWidgets.QMessageBox(parent)
    dialog.setWindowTitle("Letter Smith Beta Diagnostics")
    dialog.setIcon(QtWidgets.QMessageBox.Information)
    dialog.setText("Enable private beta diagnostics on this computer?")
    dialog.setInformativeText(
        "Letter Smith will record which controls are used, when fields change, "
        "field lengths and validation states, and application errors. It will "
        "never record the text you type, passwords, tokens, recipient content, "
        "or device identifiers. Reports remain on this computer and are never "
        "sent automatically."
    )
    enable = dialog.addButton(
        "Enable Local Diagnostics",
        QtWidgets.QMessageBox.AcceptRole,
    )
    dialog.addButton("Not Now", QtWidgets.QMessageBox.RejectRole)
    dialog.setDefaultButton(enable)
    dialog.exec()
    accepted = dialog.clickedButton() is enable
    store_beta_diagnostics_consent(paths, accepted)
    return accepted


def _safe_object_component(obj: QtCore.QObject) -> str:
    class_name = type(obj).__name__
    name = str(obj.objectName() or "").strip()
    if _SAFE_OBJECT_NAME.fullmatch(name):
        return f"{class_name}#{name}"
    parent = _qt_parent(obj)
    if parent is None:
        return class_name
    try:
        siblings = [
            child
            for child in parent.children()
            if type(child).__name__ == class_name
        ]
        index = siblings.index(obj)
    except (RuntimeError, ValueError):
        index = 0
    return f"{class_name}[{index}]"


def _qt_parent(obj: QtCore.QObject) -> QtCore.QObject | None:
    """Read Qt ownership without trusting a shadowable instance attribute."""
    try:
        return QtCore.QObject.parent(obj)
    except RuntimeError:
        return None


def widget_identifier(widget: QtWidgets.QWidget) -> str:
    components: list[str] = []
    current: QtCore.QObject | None = widget
    while current is not None and len(components) < 6:
        components.append(_safe_object_component(current))
        current = _qt_parent(current)
    return "/".join(reversed(components))


def _protected_input(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        if widget.echoMode() != QtWidgets.QLineEdit.Normal:
            return True
    return bool(_PROTECTED_FIELD_NAME.search(str(widget.objectName() or "")))


def _input_metadata(widget: QtWidgets.QWidget) -> dict[str, object]:
    metadata: dict[str, object] = {
        "field": widget_identifier(widget),
        "protected": _protected_input(widget),
    }
    if metadata["protected"]:
        return metadata

    text: str | None = None
    if isinstance(widget, QtWidgets.QLineEdit):
        text = widget.text()
    elif isinstance(widget, QtWidgets.QTextEdit):
        text = widget.toPlainText()
    elif isinstance(widget, QtWidgets.QPlainTextEdit):
        text = widget.toPlainText()
    elif isinstance(widget, QtWidgets.QComboBox):
        text = widget.currentText()
    elif isinstance(widget, QtWidgets.QAbstractSpinBox):
        text = widget.text()
    if text is not None:
        metadata["length"] = len(text)
        metadata["empty"] = not bool(text)

    acceptable = getattr(widget, "hasAcceptableInput", None)
    if callable(acceptable):
        try:
            metadata["acceptable"] = bool(acceptable())
        except RuntimeError:
            pass
    return metadata


def _traceback_location(
    traceback_object: TracebackType | None,
) -> tuple[str, str, int]:
    current = traceback_object
    if current is None:
        return "", "", 0
    while current.tb_next is not None:
        current = current.tb_next
    frame = current.tb_frame
    return (
        Path(frame.f_code.co_filename).name,
        frame.f_code.co_name,
        int(current.tb_lineno),
    )


class _BetaLogHandler(logging.Handler):
    def __init__(self, controller: "BetaDiagnosticsController") -> None:
        super().__init__(level=logging.ERROR)
        self._controller_ref = weakref.ref(controller)

    def emit(self, record: logging.LogRecord) -> None:
        controller = self._controller_ref()
        if controller is not None:
            controller.record_log_error(record)


class BetaDiagnosticsController(QtCore.QObject):
    """Record bounded, content-free beta interaction and failure events."""

    def __init__(
        self,
        application: QtWidgets.QApplication,
        paths: ApplicationPaths,
    ) -> None:
        super().__init__(application)
        self.application = application
        self.paths = paths
        self.report_directory = beta_diagnostics_report_directory(paths)
        self.report_directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._truncated = False
        self._error_generation = 0
        self._error_count = 0
        self._action_count = 0
        self._action_pass_count = 0
        self._action_fail_count = 0
        self._input_event_count = 0
        self._pending_actions: dict[str, int] = {}
        self._session_id = uuid.uuid4().hex[:16]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.report_path = self.report_directory / (
            f"beta-session-{timestamp}-{self._session_id}.jsonl"
        )
        self._recover_unfinished_reports()
        self._prune_reports()
        self._log_handler = _BetaLogHandler(self)
        logging.getLogger().addHandler(self._log_handler)
        application.installEventFilter(self)
        application.aboutToQuit.connect(self.close)
        self._write_event(
            "session_started",
            application_version=APPLICATION_VERSION,
            application_mode=(
                "frozen" if bool(getattr(sys, "frozen", False)) else "source"
            ),
            platform=sys.platform,
            privacy={
                "typed_content_recorded": False,
                "credentials_recorded": False,
                "device_identifiers_recorded": False,
                "automatic_transmission": False,
            },
        )

    def _write_event(self, event_type: str, **values: object) -> None:
        with self._lock:
            if self._closed or self._truncated:
                return
            try:
                if self.report_path.exists() and self.report_path.stat().st_size >= MAX_REPORT_BYTES:
                    self._truncated = True
                    return
                event = {
                    "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
                    "timestamp": _utc_timestamp(),
                    "session_id": self._session_id,
                    "event": str(event_type),
                    **values,
                }
                payload = json.dumps(
                    event,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                with self.report_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(payload + "\n")
                    stream.flush()
            except OSError:
                self._truncated = True

    def _recover_unfinished_reports(self) -> None:
        for path in sorted(self.report_directory.glob("beta-session-*.jsonl")):
            try:
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - 65536))
                    lines = stream.read().decode("utf-8", errors="ignore").splitlines()
                final: dict[str, Any] = {}
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    try:
                        candidate = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        final = candidate
                        break
                if isinstance(final, dict) and final.get("event") == "session_finished":
                    continue
                recovery = {
                    "schema_version": BETA_DIAGNOSTICS_SCHEMA_VERSION,
                    "timestamp": _utc_timestamp(),
                    "session_id": str(final.get("session_id", "unknown")),
                    "event": "session_finished",
                    "status": "fail",
                    "score": 0,
                    "reason": "unclean_exit_detected_on_next_start",
                }
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(recovery, separators=(",", ":")) + "\n")
            except (OSError, UnicodeError):
                continue

    def _prune_reports(self) -> None:
        try:
            reports = sorted(
                self.report_directory.glob("beta-session-*.jsonl"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return
        for path in reports[MAX_REPORT_FILES:]:
            try:
                path.unlink()
            except OSError:
                continue

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if (
            isinstance(watched, QtWidgets.QWidget)
            and event.type()
            in {
                QtCore.QEvent.Polish,
                QtCore.QEvent.Show,
                QtCore.QEvent.FocusIn,
                QtCore.QEvent.KeyPress,
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.Wheel,
            }
        ):
            self._instrument_widget(watched)
        return False

    def _instrument_widget(self, widget: QtWidgets.QWidget) -> None:
        supported = isinstance(
            widget,
            (
                QtWidgets.QAbstractButton,
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QComboBox,
                QtWidgets.QAbstractSpinBox,
                QtWidgets.QAbstractSlider,
            ),
        )
        if not supported:
            return
        try:
            if bool(widget.property("lettersmithBetaDiagnosticsInstrumented")):
                return
            widget.setProperty("lettersmithBetaDiagnosticsInstrumented", True)
        except RuntimeError:
            return

        reference = weakref.ref(widget)
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.clicked.connect(
                lambda *_args, ref=reference: self._record_button(ref())
            )
            return
        if isinstance(widget, QtWidgets.QLineEdit):
            widget.textEdited.connect(
                lambda *_args, ref=reference: self._record_input(ref(), "text_edited")
            )
            return
        if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            widget.textChanged.connect(
                lambda ref=reference: self._record_input(ref(), "text_changed")
            )
            return
        if isinstance(widget, QtWidgets.QComboBox):
            widget.activated.connect(
                lambda *_args, ref=reference: self._record_input(ref(), "selection_changed")
            )
            return
        if isinstance(widget, QtWidgets.QAbstractSpinBox):
            widget.editingFinished.connect(
                lambda ref=reference: self._record_input(ref(), "value_edited")
            )
            return
        if isinstance(widget, QtWidgets.QAbstractSlider):
            widget.sliderReleased.connect(
                lambda ref=reference: self._record_input(ref(), "value_changed")
            )

    @QtCore.Slot()
    def _record_button(self, widget: QtWidgets.QWidget | None) -> None:
        if widget is None:
            return
        with self._lock:
            self._action_count += 1
            action_id = f"action-{self._action_count}"
            self._pending_actions[action_id] = self._error_generation
        self._write_event(
            "button_clicked",
            action_id=action_id,
            control=widget_identifier(widget),
            result="pending",
        )
        QtCore.QTimer.singleShot(
            ACTION_SETTLE_MS,
            lambda value=action_id: self._finish_action(value),
        )

    def _finish_action(self, action_id: str) -> None:
        with self._lock:
            started_at_error = self._pending_actions.pop(action_id, None)
            if started_at_error is None:
                return
            passed = started_at_error == self._error_generation
            if passed:
                self._action_pass_count += 1
            else:
                self._action_fail_count += 1
        self._write_event(
            "action_result",
            action_id=action_id,
            result="pass" if passed else "fail",
            basis="no_error_observed_during_settle_window",
        )

    def _record_input(
        self,
        widget: QtWidgets.QWidget | None,
        interaction: str,
    ) -> None:
        if widget is None:
            return
        with self._lock:
            self._input_event_count += 1
            event_number = self._input_event_count
        self._write_event(
            "field_interaction",
            sequence=event_number,
            interaction=interaction,
            **_input_metadata(widget),
        )

    def record_log_error(self, record: logging.LogRecord) -> None:
        if record.module == "Main" and record.funcName == "_record_failure":
            return
        exception_type = ""
        if record.exc_info and record.exc_info[0] is not None:
            exception_type = record.exc_info[0].__name__
        fingerprint_source = "|".join(
            (
                str(record.name),
                str(record.module),
                str(record.funcName),
                str(record.lineno),
                exception_type,
            )
        )
        with self._lock:
            self._error_generation += 1
            self._error_count += 1
        self._write_event(
            "application_error",
            level=record.levelname,
            logger=record.name,
            module=record.module,
            function=record.funcName,
            line=int(record.lineno),
            exception_type=exception_type,
            fingerprint=hashlib.sha256(
                fingerprint_source.encode("utf-8", errors="replace")
            ).hexdigest()[:16],
        )

    def record_unhandled_exception(
        self,
        context: str,
        exception_type: type[BaseException],
        traceback_object: TracebackType | None,
    ) -> None:
        filename, function, line = _traceback_location(traceback_object)
        with self._lock:
            self._error_generation += 1
            self._error_count += 1
        self._write_event(
            "unhandled_exception",
            context=str(context),
            exception_type=getattr(exception_type, "__name__", "Exception"),
            module=filename,
            function=function,
            line=line,
        )

    @QtCore.Slot()
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            score = max(
                0,
                100 - (self._error_count * 25) - (self._action_fail_count * 5),
            )
            status = "pass" if self._error_count == 0 else "fail"
            pending_actions = len(self._pending_actions)
        self._write_event(
            "session_finished",
            status=status,
            score=score,
            errors=self._error_count,
            button_actions=self._action_count,
            action_passes=self._action_pass_count,
            action_failures=self._action_fail_count,
            pending_actions=pending_actions,
            field_interactions=self._input_event_count,
            truncated=self._truncated,
        )
        with self._lock:
            self._closed = True
        try:
            self.application.removeEventFilter(self)
        except RuntimeError:
            pass
        logging.getLogger().removeHandler(self._log_handler)


def install_beta_diagnostics(
    application: QtWidgets.QApplication,
    paths: ApplicationPaths,
    *,
    parent: QtWidgets.QWidget | None = None,
) -> BetaDiagnosticsController | None:
    if not request_beta_diagnostics_consent(paths, parent=parent):
        return None
    controller = BetaDiagnosticsController(application, paths)
    setattr(application, "_lettersmith_beta_diagnostics", controller)
    logging.info(
        "[Beta Diagnostics] Local report: %s",
        controller.report_path,
    )
    return controller


__all__ = [
    "ACTION_SETTLE_MS",
    "BETA_DIAGNOSTICS_CONSENT_FILE",
    "BETA_DIAGNOSTICS_DIRECTORY",
    "BETA_DIAGNOSTICS_SCHEMA_VERSION",
    "BetaDiagnosticsController",
    "beta_diagnostics_consent_path",
    "beta_diagnostics_report_directory",
    "install_beta_diagnostics",
    "request_beta_diagnostics_consent",
    "store_beta_diagnostics_consent",
    "widget_identifier",
]
