from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from application_identity import (
    APPLICATION_DESCRIPTION,
    APPLICATION_LICENSE,
    APPLICATION_NAME,
    APPLICATION_VERSION,
    BUILD_ID,
    COPYRIGHT_NOTICE,
    CREATOR_NAME,
    LICENSE_FILE_OR_URL,
    OFFICIAL_WEBSITE,
    PLACEHOLDER_COLOR,
    PRIVACY_POLICY_URL,
    PUBLISHER_NAME,
    REPOSITORY_URL,
    SUPPORT_EMAIL,
    TERMS_OR_EULA_URL,
    THIRD_PARTY_ASSET_ATTRIBUTION,
    THIRD_PARTY_NOTICES,
    is_placeholder,
    unresolved_public_metadata,
)
from support_information import (
    SupportReportOptions,
    SupportRuntimeContext,
    build_support_report,
)
from ui_dialogs import LetterSmithDialog, LetterSmithMessageDialog
from ui_help import set_control_help


_LOGGER = logging.getLogger(__name__)


def _value_html(value: object) -> str:
    escaped = html.escape(str(value))
    if is_placeholder(value):
        return f'<span style="color:{PLACEHOLDER_COLOR};">{escaped}</span>'
    return escaped


def _metadata_html(label: str, value: object) -> str:
    return f"<b>{html.escape(label)}:</b> {_value_html(value)}"


def _github_state_label(value: object) -> str:
    normalized = str(getattr(value, "value", value) or "").strip().casefold()
    labels = {
        "connected": "Connected",
        "disconnected": "Disconnected",
        "connecting": "Checking connection",
        "authorizing": "Authentication in progress",
        "reconnecting": "Reconnecting",
        "install_required": "Installation required",
        "action_required": "Authentication action required",
        "github_unavailable": "Error / unavailable",
    }
    return labels.get(normalized, "Unavailable")


def capture_support_runtime_context(application: object) -> SupportRuntimeContext:
    theme_service = getattr(application, "theme_service", None)
    theme_definition = getattr(theme_service, "current", None)
    theme_name = str(
        getattr(theme_definition, "display_name", "")
        or getattr(theme_service, "theme_id", "")
        or "Unavailable"
    )

    project_state = getattr(application, "project_state", None)
    state = getattr(project_state, "state", None)
    application_state = str(getattr(state, "value", state) or "Unavailable")
    application_state = application_state.replace("_", " ").title()

    forge = getattr(application, "forge_tab", None)
    snapshot = getattr(forge, "_github_snapshot", None)
    if snapshot is None:
        try:
            from publishing.github_auth import github_connection_service

            snapshot = github_connection_service().snapshot
        except Exception:
            snapshot = None
    github_state = _github_state_label(getattr(snapshot, "state", ""))
    try:
        from publishing.github_config import github_application_configuration

        publishing_service = (
            "Available"
            if github_application_configuration().configured
            else "Developer setup required"
        )
    except Exception:
        publishing_service = "Unavailable"

    active_operation = str(
        getattr(forge, "_busy_operation", "")
        or getattr(forge, "_publication_operation", "")
    ).strip()
    last_operation = active_operation or "Unavailable"
    last_operation_result = "In progress" if active_operation else "Unavailable"

    screens = QtGui.QGuiApplication.screens()
    primary = QtGui.QGuiApplication.primaryScreen()
    if primary is None:
        resolution = dpi = scale = "Unavailable"
    else:
        geometry = primary.geometry()
        resolution = f"{geometry.width()} × {geometry.height()}"
        dpi = f"{primary.logicalDotsPerInch():.1f} logical DPI"
        scale = (
            f"{(primary.logicalDotsPerInch() / 96.0) * 100:.0f}% "
            f"(device pixel ratio {primary.devicePixelRatio():.2f})"
        )
    return SupportRuntimeContext(
        active_theme=theme_name,
        application_state=application_state,
        github_state=github_state,
        publishing_service=publishing_service,
        last_operation=last_operation,
        last_operation_result=last_operation_result,
        display_count=str(len(screens)) if screens else "Unavailable",
        primary_display_resolution=resolution,
        primary_display_dpi=dpi,
        primary_display_scale=scale,
    )


class _SupportReportSignals(QtCore.QObject):
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)


class _SupportReportTask(QtCore.QRunnable):
    def __init__(
        self,
        project_root: str | Path,
        options: SupportReportOptions,
        context: SupportRuntimeContext,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.options = options
        self.context = context
        self.signals = _SupportReportSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            report = build_support_report(
                self.project_root,
                self.options,
                self.context,
            )
        except Exception as error:
            _LOGGER.exception("Support information generation failed.")
            self.signals.failed.emit(str(error))
            return
        self.signals.completed.emit(report)


class SupportInformationOptionsDialog(LetterSmithDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(
            parent,
            title="Copy Support Information",
            modal=True,
            width=620,
        )
        self.setMinimumHeight(500)

        explanation = QtWidgets.QLabel(
            "Letter Smith can include additional technical information that may "
            "help identify the cause of a problem.",
            self.panel,
        )
        explanation.setObjectName("LetterSmithDialogMessage")
        explanation.setWordWrap(True)
        self.content_layout.addWidget(explanation)

        privacy = QtWidgets.QLabel(
            "No passwords, authentication tokens, letter contents, recipient "
            "information, or personal documents will be included.",
            self.panel,
        )
        privacy.setObjectName("LetterSmithDialogDetail")
        privacy.setWordWrap(True)
        self.content_layout.addWidget(privacy)

        self.computer_checkbox = QtWidgets.QCheckBox(
            "Include computer information",
            self.panel,
        )
        self.computer_checkbox.setChecked(True)
        self.computer_checkbox.setAccessibleName("Include computer information")
        set_control_help(
            self.computer_checkbox,
            "Adds basic operating-system and hardware information. No hardware "
            "serial numbers or unique device identifiers are included.",
        )
        self.content_layout.addWidget(self.computer_checkbox)
        computer_detail = QtWidgets.QLabel(
            "Includes operating system, CPU, GPU, memory, display, graphics-driver, "
            "architecture, and available storage information when obtainable.",
            self.panel,
        )
        computer_detail.setObjectName("LetterSmithDialogDetail")
        computer_detail.setWordWrap(True)
        computer_detail.setContentsMargins(28, 0, 0, 0)
        self.content_layout.addWidget(computer_detail)

        self.diagnostic_checkbox = QtWidgets.QCheckBox(
            "Include diagnostic information",
            self.panel,
        )
        self.diagnostic_checkbox.setChecked(True)
        self.diagnostic_checkbox.setAccessibleName("Include diagnostic information")
        set_control_help(
            self.diagnostic_checkbox,
            "Adds recent Letter Smith technical state and error information. "
            "Authentication secrets and letter content are excluded.",
        )
        self.content_layout.addWidget(self.diagnostic_checkbox)
        diagnostic_detail = QtWidgets.QLabel(
            "Includes recent Letter Smith errors, component availability, application "
            "state, publishing status, dependency information, and readiness state.",
            self.panel,
        )
        diagnostic_detail.setObjectName("LetterSmithDialogDetail")
        diagnostic_detail.setWordWrap(True)
        diagnostic_detail.setContentsMargins(28, 0, 0, 0)
        self.content_layout.addWidget(diagnostic_detail)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = self._action_button("Cancel", "secondary")
        self.copy_button = self._action_button("Copy Information", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.copy_button.clicked.connect(self.accept)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        self._uniform_button_width([self.cancel_button, self.copy_button])
        self.content_layout.addLayout(actions)
        self.copy_button.setDefault(True)

        colors = self.colors
        self.setStyleSheet(
            self.styleSheet()
            + "QCheckBox{"
            f"color:{colors['text']};background:transparent;"
            "font:600 10pt 'Segoe UI';spacing:9px;}"
            "QCheckBox::indicator{width:18px;height:18px;"
            f"border:1px solid {colors['border']};border-radius:4px;"
            f"background:{colors['background']};}}"
            "QCheckBox::indicator:checked{"
            f"background:{colors['accent']};border-color:{colors['accent']};}}"
        )

    def options(self) -> SupportReportOptions:
        return SupportReportOptions(
            include_computer_information=self.computer_checkbox.isChecked(),
            include_diagnostic_information=self.diagnostic_checkbox.isChecked(),
        )


class SupportInformationCopiedDialog(LetterSmithDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(
            parent,
            title="Support Information Copied",
            modal=True,
            width=570,
        )
        message = QtWidgets.QLabel(
            "Your Letter Smith support information has been copied to your clipboard.\n\n"
            "Next, open your email, create a new message, and paste the copied "
            "information into the email along with a description of the problem "
            "you experienced.",
            self.panel,
        )
        message.setObjectName("LetterSmithDialogMessage")
        message.setWordWrap(True)
        self.content_layout.addWidget(message)

        destination = QtWidgets.QLabel(SUPPORT_EMAIL, self.panel)
        destination.setObjectName("SupportDestination")
        destination.setAlignment(QtCore.Qt.AlignCenter)
        destination.setWordWrap(True)
        destination.setAccessibleName(f"Support destination: {SUPPORT_EMAIL}")
        if is_placeholder(SUPPORT_EMAIL):
            destination.setStyleSheet(
                f"color:{PLACEHOLDER_COLOR};background:transparent;border:none;"
                "font:700 11pt 'Segoe UI';"
            )
        else:
            destination.setStyleSheet(
                f"color:{self.colors['text']};background:transparent;border:none;"
                "font:700 11pt 'Segoe UI';"
            )
        self.support_destination_label = destination
        self.content_layout.addWidget(destination)

        if is_placeholder(SUPPORT_EMAIL):
            detail_text = (
                "The official Letter Smith support address has not yet been configured."
            )
        else:
            detail_text = "Send the message to the support address shown above."
        detail = QtWidgets.QLabel(detail_text, self.panel)
        detail.setObjectName("LetterSmithDialogDetail")
        detail.setAlignment(QtCore.Qt.AlignCenter)
        detail.setWordWrap(True)
        self.content_layout.addWidget(detail)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.done_button = self._action_button("Done", "primary")
        self.done_button.clicked.connect(self.accept)
        actions.addWidget(self.done_button)
        actions.addStretch(1)
        self.content_layout.addLayout(actions)
        self.done_button.setDefault(True)


class AboutLetterSmithDialog(LetterSmithDialog):
    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        application: object | None = None,
    ) -> None:
        super().__init__(
            parent,
            title="About Letter Smith",
            modal=True,
            width=760,
        )
        self.application = application or (
            parent.window() if parent is not None else None
        )
        self.project_root = Path(
            getattr(self.application, "project_root", Path.cwd())
        ).resolve()
        self._report_active = False
        self._report_task: _SupportReportTask | None = None
        self._pending_report = ""
        self.setMinimumSize(600, 560)
        self.resize(760, 760)

        scroll = QtWidgets.QScrollArea(self.panel)
        scroll.setObjectName("AboutLetterSmithScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget(scroll)
        content.setObjectName("AboutLetterSmithContent")
        about_layout = QtWidgets.QVBoxLayout(content)
        about_layout.setContentsMargins(8, 4, 8, 8)
        about_layout.setSpacing(12)

        product = QtWidgets.QLabel(APPLICATION_NAME.upper(), content)
        product.setObjectName("AboutProductName")
        product.setAlignment(QtCore.Qt.AlignCenter)
        about_layout.addWidget(product)

        for label, value in (
            ("Version", APPLICATION_VERSION),
            ("Build", BUILD_ID),
        ):
            self._add_metadata_label(about_layout, label, value, content)

        description = QtWidgets.QLabel(APPLICATION_DESCRIPTION, content)
        description.setObjectName("AboutBodyText")
        description.setWordWrap(True)
        description.setAlignment(QtCore.Qt.AlignCenter)
        about_layout.addWidget(description)

        creator = QtWidgets.QLabel(f"Created by {CREATOR_NAME}", content)
        creator.setObjectName("AboutCreator")
        creator.setAlignment(QtCore.Qt.AlignCenter)
        creator.setAccessibleName(f"Created by {CREATOR_NAME}")
        about_layout.addWidget(creator)

        divider = QtWidgets.QFrame(content)
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        divider.setObjectName("AboutDivider")
        about_layout.addWidget(divider)

        for label, value in (
            ("Publisher / Distributor", PUBLISHER_NAME),
            ("Copyright", COPYRIGHT_NOTICE),
            ("Official Website", OFFICIAL_WEBSITE),
            ("Support", SUPPORT_EMAIL),
            ("Public Repository", REPOSITORY_URL),
            ("Application License", APPLICATION_LICENSE),
            ("License File or URL", LICENSE_FILE_OR_URL),
            ("Third-Party Notices", THIRD_PARTY_NOTICES),
            ("Third-Party Asset Attribution", THIRD_PARTY_ASSET_ATTRIBUTION),
            ("Privacy Policy", PRIVACY_POLICY_URL),
            ("Terms / EULA", TERMS_OR_EULA_URL),
        ):
            self._add_metadata_label(about_layout, label, value, content)

        if unresolved_public_metadata():
            warning = QtWidgets.QLabel(
                "Public-release metadata is incomplete.",
                content,
            )
            warning.setObjectName("PublicMetadataWarning")
            warning.setAlignment(QtCore.Qt.AlignCenter)
            warning.setStyleSheet(
                f"color:{PLACEHOLDER_COLOR};background:transparent;border:none;"
                "font-weight:700;"
            )
            warning.setAccessibleName(
                "Public-release metadata is incomplete. Placeholder values remain."
            )
            about_layout.addWidget(warning)

        support_heading = QtWidgets.QLabel("Need Help?", content)
        support_heading.setObjectName("AboutSupportHeading")
        about_layout.addWidget(support_heading)
        support_copy = QtWidgets.QLabel(
            "If you are reporting a problem with Letter Smith, you may be asked "
            "to copy your application information. Click below to prepare the "
            "information needed for a support request.",
            content,
        )
        support_copy.setObjectName("AboutBodyText")
        support_copy.setWordWrap(True)
        about_layout.addWidget(support_copy)

        self.copy_support_button = self._action_button(
            "Copy Support Information",
            "primary",
        )
        self.copy_support_button.setAccessibleName("Copy Support Information")
        set_control_help(
            self.copy_support_button,
            "Prepare and copy technical information that can help diagnose a "
            "Letter Smith problem.",
        )
        self.copy_support_button.clicked.connect(self._choose_support_information)
        support_action = QtWidgets.QHBoxLayout()
        support_action.addStretch(1)
        support_action.addWidget(self.copy_support_button)
        support_action.addStretch(1)
        about_layout.addLayout(support_action)
        about_layout.addStretch(1)

        scroll.setWidget(content)
        self.content_layout.addWidget(scroll, 1)

        close_actions = QtWidgets.QHBoxLayout()
        close_actions.addStretch(1)
        self.close_button = self._action_button("Close", "secondary")
        self.close_button.clicked.connect(self.reject)
        close_actions.addWidget(self.close_button)
        close_actions.addStretch(1)
        self.content_layout.addLayout(close_actions)

        colors = self.colors
        self.setStyleSheet(
            self.styleSheet()
            + "QScrollArea#AboutLetterSmithScroll,QWidget#AboutLetterSmithContent{"
            "background:transparent;border:none;}"
            "QLabel#AboutProductName{"
            f"color:{colors['accent']};background:transparent;border:none;"
            "font:800 18pt 'Segoe UI';letter-spacing:2px;}"
            "QLabel#AboutCreator,QLabel#AboutSupportHeading{"
            f"color:{colors['text']};background:transparent;border:none;"
            "font:700 11pt 'Segoe UI';}"
            "QLabel#AboutBodyText,QLabel[aboutMetadata=\"true\"]{"
            f"color:{colors['text']};background:transparent;border:none;"
            "font:10pt 'Segoe UI';}"
            "QFrame#AboutDivider{"
            f"color:{colors['border']};background:{colors['border']};"
            "border:none;max-height:1px;}"
        )

    def _add_metadata_label(
        self,
        layout: QtWidgets.QVBoxLayout,
        label: str,
        value: object,
        parent: QtWidgets.QWidget,
    ) -> None:
        widget = QtWidgets.QLabel(_metadata_html(label, value), parent)
        widget.setProperty("aboutMetadata", True)
        widget.setTextFormat(QtCore.Qt.RichText)
        widget.setWordWrap(True)
        widget.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        widget.setAccessibleName(f"{label}: {value}")
        layout.addWidget(widget)

    def _choose_support_information(self) -> None:
        options_dialog = SupportInformationOptionsDialog(self)
        if options_dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        context = capture_support_runtime_context(self.application)
        self._start_report(options_dialog.options(), context)

    def _start_report(
        self,
        options: SupportReportOptions,
        context: SupportRuntimeContext,
    ) -> None:
        if self._report_active:
            return
        self._report_active = True
        self.copy_support_button.setEnabled(False)
        self.copy_support_button.setText("Preparing Support Information…")
        self.close_button.setEnabled(False)
        task = _SupportReportTask(self.project_root, options, context)
        task.signals.completed.connect(self._report_ready)
        task.signals.failed.connect(self._report_failed)
        self._report_task = task
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(str)
    def _report_ready(self, report: str) -> None:
        self._finish_report_task()
        self._pending_report = report
        self._copy_pending_report()

    @QtCore.Slot(str)
    def _report_failed(self, _message: str) -> None:
        self._finish_report_task()
        dialog = LetterSmithMessageDialog(
            self,
            title="Support Information Could Not Be Prepared",
            message=(
                "Letter Smith could not prepare the support information. "
                "No information was copied or transmitted."
            ),
            detail="Close this message and try again.",
        )
        dialog.exec()

    def _finish_report_task(self) -> None:
        self._report_active = False
        self._report_task = None
        self.copy_support_button.setEnabled(True)
        self.copy_support_button.setText("Copy Support Information")
        self.close_button.setEnabled(True)

    def _copy_pending_report(self) -> None:
        if not self._pending_report:
            return
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("The system clipboard is unavailable.")
            clipboard.setText(self._pending_report, QtGui.QClipboard.Clipboard)
            QtWidgets.QApplication.processEvents()
            if clipboard.text(QtGui.QClipboard.Clipboard) != self._pending_report:
                raise RuntimeError("The clipboard did not retain the support report.")
        except (OSError, RuntimeError, TypeError, ValueError):
            retry = LetterSmithMessageDialog(
                self,
                title="Support Information Was Not Copied",
                message=(
                    "Letter Smith prepared the support information, but the system "
                    "clipboard did not accept it."
                ),
                detail="You can retry without generating the report again.",
                action_text="Retry Copy",
                close_text="Cancel",
            )
            retry.exec()
            if retry.action_requested:
                self._copy_pending_report()
            return
        self._pending_report = ""
        SupportInformationCopiedDialog(self).exec()

    def reject(self) -> None:
        if self._report_active:
            return
        super().reject()


__all__ = [
    "AboutLetterSmithDialog",
    "SupportInformationCopiedDialog",
    "SupportInformationOptionsDialog",
    "capture_support_runtime_context",
]
