from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

from publishing.credentials import R2Credentials
from publishing.r2 import (
    DEFAULT_BUCKET,
    FREE_TIER_LIMIT_BYTES,
    R2Configuration,
    R2StorageSnapshot,
    _format_bytes,
)
from publishing.expiration import publication_expiry_label


class R2StorageDialog(QtWidgets.QDialog):
    save_requested = QtCore.Signal(object, object)
    refresh_requested = QtCore.Signal()
    delete_requested = QtCore.Signal(str)
    disconnect_requested = QtCore.Signal()

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cloudflare R2 Storage")
        self.setModal(False)
        self.setMinimumSize(680, 620)
        self.resize(760, 700)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._has_credentials = False
        self._busy = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QDialog{background:#0d151c;color:#eaf9ff;}"
            "QGroupBox{color:#dff8ff;border:1px solid #31505e;"
            "border-radius:7px;margin-top:10px;padding-top:8px;font:600 10pt 'Segoe UI';}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;}"
            "QLabel{color:#c8e5ed;font:9.5pt 'Segoe UI';}"
            "QLineEdit{background:#111f28;color:#f2fcff;border:1px solid #385c6b;"
            "border-radius:5px;padding:7px;}"
            "QLineEdit:focus{border-color:#00d4f4;}"
            "QPushButton{background:#172934;color:#eafcff;border:1px solid #3d6575;"
            "border-radius:6px;padding:7px 12px;}"
            "QPushButton:hover{background:#1d3a47;border-color:#00d4f4;}"
            "QPushButton:disabled{color:#64757d;border-color:#293c45;}"
            "QTreeWidget{background:#091116;color:#eefaff;border:1px solid #294753;"
            "border-radius:5px;alternate-background-color:#0e1a21;}"
            "QHeaderView::section{background:#142731;color:#dff8ff;border:none;"
            "border-right:1px solid #294753;padding:6px;}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QtWidgets.QLabel("Cloudflare R2 Storage")
        heading.setStyleSheet("color:#00d4f4;font:700 16pt 'Segoe UI';")
        layout.addWidget(heading)
        explanation = QtWidgets.QLabel(
            "This connects Letter Smith to this user's own R2 account. "
            "Hosted letters expire automatically after 30 days."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        connection = QtWidgets.QGroupBox("Connection")
        form = QtWidgets.QFormLayout(connection)
        form.setContentsMargins(12, 15, 12, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.account_id = QtWidgets.QLineEdit()
        self.account_id.setPlaceholderText("32-character Cloudflare account ID")
        self.bucket = QtWidgets.QLineEdit(DEFAULT_BUCKET)
        self.public_url = QtWidgets.QLineEdit()
        self.public_url.setPlaceholderText("https://your-public-bucket-domain")
        self.access_key = QtWidgets.QLineEdit()
        self.access_key.setPlaceholderText("R2 Access Key ID")
        self.secret_key = QtWidgets.QLineEdit()
        self.secret_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.secret_key.setPlaceholderText("R2 Secret Access Key")
        form.addRow("Account ID", self.account_id)
        form.addRow("Bucket", self.bucket)
        form.addRow("Public URL", self.public_url)
        form.addRow("Access Key ID", self.access_key)
        form.addRow("Secret Access Key", self.secret_key)
        connection_actions = QtWidgets.QHBoxLayout()
        self.cloudflare_btn = QtWidgets.QPushButton("Open Cloudflare R2")
        self.cloudflare_btn.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(
                QUrl("https://dash.cloudflare.com/?to=/:account/r2")
            )
        )
        self.save_btn = QtWidgets.QPushButton("Save and Test Connection")
        self.save_btn.setStyleSheet(
            "QPushButton{background:#3153a8;color:white;border-color:#6f8ee1;"
            "font:600 9.5pt 'Segoe UI';}QPushButton:hover{background:#4268c6;}"
        )
        self.save_btn.clicked.connect(self._submit_configuration)
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._confirm_disconnect)
        connection_actions.addWidget(self.cloudflare_btn)
        connection_actions.addStretch(1)
        connection_actions.addWidget(self.disconnect_btn)
        connection_actions.addWidget(self.save_btn)
        form.addRow(connection_actions)
        layout.addWidget(connection)

        usage = QtWidgets.QGroupBox("Free-tier usage")
        usage_layout = QtWidgets.QVBoxLayout(usage)
        usage_layout.setContentsMargins(12, 15, 12, 12)
        self.usage_label = QtWidgets.QLabel("Storage usage has not been measured.")
        self.usage_label.setStyleSheet("font:600 10pt 'Segoe UI';")
        usage_layout.addWidget(self.usage_label)
        self.usage_bar = QtWidgets.QProgressBar()
        self.usage_bar.setRange(0, 1000)
        self.usage_bar.setValue(0)
        self.usage_bar.setTextVisible(True)
        self.usage_bar.setFormat("0.0%")
        self.usage_bar.setStyleSheet(
            "QProgressBar{background:#101c23;color:#f4fdff;border:1px solid #31505e;"
            "border-radius:6px;text-align:center;height:20px;}"
            "QProgressBar::chunk{background:#2f9e72;border-radius:5px;}"
        )
        usage_layout.addWidget(self.usage_bar)
        self.usage_warning = QtWidgets.QLabel(
            "Letter Smith stops a publication before it would exceed the configured 10 GB limit."
        )
        self.usage_warning.setWordWrap(True)
        usage_layout.addWidget(self.usage_warning)
        account_note = QtWidgets.QLabel(
            "This meter covers the dedicated Letter Smith bucket. Cloudflare's "
            "10 GB free allowance is shared across every R2 bucket in the account; "
            "use Open Cloudflare R2 to review the account total."
        )
        account_note.setWordWrap(True)
        account_note.setStyleSheet("color:#86aeb9;font:8.5pt 'Segoe UI';")
        usage_layout.addWidget(account_note)
        usage_actions = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh Usage")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.open_log_btn = QtWidgets.QPushButton("Open Error Log")
        self.open_log_btn.clicked.connect(self._open_error_log)
        usage_actions.addWidget(self.refresh_btn)
        usage_actions.addWidget(self.open_log_btn)
        usage_actions.addStretch(1)
        usage_layout.addLayout(usage_actions)
        layout.addWidget(usage)

        hosted = QtWidgets.QGroupBox("Hosted letters")
        hosted_layout = QtWidgets.QVBoxLayout(hosted)
        hosted_layout.setContentsMargins(10, 14, 10, 10)
        self.publications = QtWidgets.QTreeWidget()
        self.publications.setColumnCount(4)
        self.publications.setHeaderLabels(("Letter", "Size", "Expires", "Objects"))
        self.publications.setRootIsDecorated(False)
        self.publications.setAlternatingRowColors(True)
        self.publications.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.publications.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.publications.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.publications.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.publications.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        hosted_layout.addWidget(self.publications, 1)
        delete_row = QtWidgets.QHBoxLayout()
        delete_row.addStretch(1)
        self.delete_btn = QtWidgets.QPushButton("Delete Selected Hosted Letter")
        self.delete_btn.setStyleSheet(
            "QPushButton{background:#42252c;color:#ffc4c4;border-color:#87505c;}"
            "QPushButton:hover{background:#6a303b;}"
        )
        self.delete_btn.clicked.connect(self._confirm_delete)
        delete_row.addWidget(self.delete_btn)
        hosted_layout.addLayout(delete_row)
        layout.addWidget(hosted, 1)

        self.activity = QtWidgets.QLabel()
        self.activity.setWordWrap(True)
        self.activity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.activity)

        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.hide)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

    def set_configuration(
        self,
        configuration: R2Configuration | None,
        *,
        has_credentials: bool,
    ) -> None:
        self._has_credentials = bool(has_credentials)
        if configuration is not None:
            self.account_id.setText(configuration.account_id)
            self.bucket.setText(configuration.bucket)
            self.public_url.setText(configuration.public_base_url)
        elif not self.bucket.text().strip():
            self.bucket.setText(DEFAULT_BUCKET)
        self.access_key.clear()
        self.secret_key.clear()
        placeholder = (
            "Stored securely; leave blank to keep"
            if self._has_credentials
            else "Required"
        )
        self.access_key.setPlaceholderText(placeholder)
        self.secret_key.setPlaceholderText(placeholder)
        self.disconnect_btn.setEnabled(self._has_credentials and not self._busy)

    def _submit_configuration(self) -> None:
        try:
            configuration = R2Configuration(
                account_id=self.account_id.text(),
                bucket=self.bucket.text(),
                public_base_url=self.public_url.text(),
                limit_bytes=FREE_TIER_LIMIT_BYTES,
            ).validated()
            access_key = self.access_key.text().strip()
            secret_key = self.secret_key.text().strip()
            credentials = None
            if access_key or secret_key:
                credentials = R2Credentials(access_key, secret_key).validated()
            elif not self._has_credentials:
                raise ValueError("Both R2 access keys are required for the first connection.")
        except ValueError as error:
            self.show_error(str(error))
            return
        self.save_requested.emit(configuration, credentials)

    def update_snapshot(self, snapshot: R2StorageSnapshot) -> None:
        value = max(0, min(1000, round(snapshot.percentage * 10)))
        self.usage_bar.setValue(value)
        self.usage_bar.setFormat(f"{snapshot.percentage:.1f}%")
        self.usage_label.setText(
            f"{_format_bytes(snapshot.used_bytes)} of "
            f"{_format_bytes(snapshot.limit_bytes)} used · "
            f"{_format_bytes(snapshot.remaining_bytes)} available"
        )
        if snapshot.percentage >= 95:
            color = "#d9535f"
            warning = (
                "Storage is almost full. Delete hosted letters before publishing again."
            )
        elif snapshot.percentage >= 85:
            color = "#d98a37"
            warning = "Storage is above 85%. Review hosted letters soon."
        elif snapshot.percentage >= 70:
            color = "#d6b94b"
            warning = "Storage is above 70%. Automatic expiration remains active."
        else:
            color = "#2f9e72"
            warning = "Storage is within the configured free-tier limit."
        self.usage_bar.setStyleSheet(
            "QProgressBar{background:#101c23;color:#f4fdff;border:1px solid #31505e;"
            "border-radius:6px;text-align:center;height:20px;}"
            f"QProgressBar::chunk{{background:{color};border-radius:5px;}}"
        )
        self.usage_warning.setText(warning)
        self.publications.clear()
        for publication in snapshot.publications:
            title = publication.title.strip()
            recipient = publication.recipient.strip()
            label = " — ".join(part for part in (recipient, title) if part)
            item = QtWidgets.QTreeWidgetItem(
                (
                    label or publication.public_path,
                    _format_bytes(publication.size_bytes),
                    publication_expiry_label(publication.expires_at) or "Unknown",
                    str(publication.object_count),
                )
            )
            item.setData(0, Qt.UserRole, publication.public_path)
            item.setToolTip(0, publication.public_path)
            self.publications.addTopLevelItem(item)
        self.activity.setText(
            f"Usage refreshed {self._display_timestamp(snapshot.measured_at)}."
        )
        self.activity.setStyleSheet("color:#9fc4ce;")

    @staticmethod
    def _display_timestamp(value: str) -> str:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return moment.astimezone().strftime("%b %d, %Y at %I:%M %p")
        except ValueError:
            return "now"

    def set_busy(self, busy: bool, activity: str = "") -> None:
        self._busy = bool(busy)
        for widget in (
            self.account_id,
            self.bucket,
            self.public_url,
            self.access_key,
            self.secret_key,
            self.cloudflare_btn,
            self.save_btn,
            self.refresh_btn,
            self.delete_btn,
            self.publications,
        ):
            widget.setEnabled(not busy)
        self.disconnect_btn.setEnabled(not busy and self._has_credentials)
        if activity:
            self.activity.setText(activity)
            self.activity.setStyleSheet("color:#9fc4ce;")

    def show_error(self, message: str, technical_details: str = "") -> None:
        self.activity.setText(message)
        self.activity.setStyleSheet("color:#ff9a9a;")
        if technical_details:
            self.activity.setToolTip(technical_details)

    def _confirm_delete(self) -> None:
        item = self.publications.currentItem()
        if item is None:
            self.show_error("Select a hosted letter to delete.")
            return
        public_path = str(item.data(0, Qt.UserRole) or "")
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete Hosted Letter",
            "Delete this hosted letter now? Its public link will stop working.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.delete_requested.emit(public_path)

    def _confirm_disconnect(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Disconnect Cloudflare R2",
            "Remove the saved R2 connection from this computer? Hosted letters will not be deleted.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.disconnect_requested.emit()

    def _open_error_log(self) -> None:
        local_app_data = str(os.environ.get("LOCALAPPDATA", "")).strip()
        if not local_app_data:
            self.show_error("The Windows application-data folder is unavailable.")
            return
        log_path = Path(local_app_data) / "LetterSmith" / "logs" / "lettersmith.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        target = log_path if log_path.exists() else log_path.parent
        if not QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve()))):
            self.show_error("The error-log location could not be opened.")


__all__ = ["R2StorageDialog"]
