from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

from project_paths import application_paths
from publishing.github_auth import GitHubAccount, GitHubDeviceAuthorization
from ui_help import set_control_help


class GitHubAccountDialog(QtWidgets.QDialog):
    sign_in_requested = QtCore.Signal()
    sign_out_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint,
        )
        self.setObjectName("GitHubAccountDialog")
        self.setAccessibleName("GitHub Account")
        self.setModal(False)
        self.setMinimumSize(520, 300)
        self._watermark = QtGui.QPixmap(
            str(
                application_paths().app_resource_path(
                    "icons/GitHub-logo.png"
                )
            )
        )
        self.setStyleSheet(
            "QDialog#GitHubAccountDialog{background:#161b22;"
            "border:1px solid #30363d;color:#f0f6fc;}"
            "QLabel{background:transparent;color:#f0f6fc;}"
            "QLabel#GitHubConnectionStatus[connectionState='connected']{"
            "color:#39d98a;}"
            "QLabel#GitHubConnectionStatus[connectionState='disconnected']{"
            "color:#ff6b72;}"
            "QLabel#GitHubConnectionStatus[connectionState='checking']{"
            "color:#58a6ff;}"
            "QPushButton{min-height:38px;background:#21262d;color:#f0f6fc;"
            "border:1px solid #30363d;border-radius:6px;padding:0 14px;"
            "font:600 10pt 'Segoe UI';}"
            "QPushButton:hover{background:#30363d;border-color:#8b949e;}"
            "QPushButton#GitHubSignInButton{background:#238636;"
            "border-color:#2ea043;color:#ffffff;}"
            "QPushButton#GitHubSignInButton:hover{background:#2ea043;}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel("GitHub")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet("font:700 20pt 'Segoe UI';")
        layout.addWidget(heading)
        layout.addStretch(1)
        self.status_label = QtWidgets.QLabel("GitHub not connected")
        self.status_label.setObjectName("GitHubConnectionStatus")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font:700 12pt 'Segoe UI';")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.sign_in_button = QtWidgets.QPushButton("Sign in with GitHub")
        self.sign_in_button.setObjectName("GitHubSignInButton")
        set_control_help(
            self.sign_in_button,
            "Connect a GitHub account so Letter Smith can publish letters.",
        )
        self.sign_in_button.clicked.connect(self.sign_in_requested)
        actions.addWidget(self.sign_in_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.set_account(None)

    def set_account(
        self,
        account: GitHubAccount | None,
        *,
        checking: bool = False,
        message: str = "",
    ) -> None:
        if checking:
            self.status_label.setText("Checking GitHub connection…")
            connection_state = "checking"
        elif account is not None:
            self.status_label.setText(
                f"GitHub connected\nSigned in as {account.login}"
            )
            connection_state = "connected"
        else:
            self.status_label.setText(
                "GitHub not connected"
                + (f"\n{message}" if message else "")
            )
            connection_state = "disconnected"
        self.status_label.setProperty(
            "connectionState",
            connection_state,
        )
        self.status_label.style().unpolish(
            self.status_label
        )
        self.status_label.style().polish(
            self.status_label
        )
        self.sign_in_button.setVisible(True)
        self.sign_in_button.setEnabled(not checking)

    def paintEvent(
        self,
        event: QtGui.QPaintEvent,
    ) -> None:
        super().paintEvent(event)
        if self._watermark.isNull():
            return
        available = self.rect().adjusted(
            55,
            48,
            -55,
            -52,
        )
        if available.isEmpty():
            return
        scaled = self._watermark.scaled(
            available.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        target = QtCore.QRect(
            0,
            0,
            scaled.width(),
            scaled.height(),
        )
        target.moveCenter(self.rect().center())
        painter = QtGui.QPainter(self)
        painter.setOpacity(0.14)
        painter.drawPixmap(target, scaled)


class GitHubDeviceFlowDialog(QtWidgets.QDialog):
    cancel_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in with GitHub")
        self.setModal(False)
        self.setMinimumWidth(460)
        self._url = ""
        self._completed = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self.instruction = QtWidgets.QLabel(
            "Enter this code on GitHub to authorize Letter Smith:"
        )
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)

        code_row = QtWidgets.QHBoxLayout()
        self.code_label = QtWidgets.QLabel()
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_label.setStyleSheet(
            "font:700 22pt 'Consolas';padding:10px 16px;"
            "border:1px solid #617987;border-radius:7px;"
        )
        code_row.addWidget(self.code_label, 1)
        self.copy_button = QtWidgets.QPushButton("Copy Code")
        self.copy_button.clicked.connect(self._copy_code)
        set_control_help(
            self.copy_button,
            "Copy the temporary GitHub authorization code.",
        )
        code_row.addWidget(self.copy_button)
        layout.addLayout(code_row)

        self.status_label = QtWidgets.QLabel("Waiting for GitHub authorization…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Open GitHub and Sign In")
        self.open_button.clicked.connect(self.open_github)
        set_control_help(
            self.open_button,
            "Open GitHub's official authorization page in your browser.",
        )
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    def set_authorization(self, authorization: GitHubDeviceAuthorization) -> None:
        self._completed = False
        self._url = authorization.verification_uri
        self.code_label.setText(authorization.user_code)
        self.code_label.show()
        self.copy_button.show()
        self.instruction.setText(
            "Enter this code on GitHub to authorize Letter Smith:"
        )
        self.status_label.setText("Waiting for GitHub authorization…")
        self.open_button.setText("Open GitHub and Sign In")

    def set_installation_step(self, url: str) -> None:
        self._url = str(url)
        self.code_label.hide()
        self.copy_button.hide()
        self.instruction.setText(
            "GitHub sign-in is complete. Approve Letter Smith for your account to finish setup."
        )
        self.status_label.setText("Waiting for GitHub setup to finish…")
        self.open_button.setText("Open GitHub and Finish Setup")

    def finish(self) -> None:
        self._completed = True
        self.accept()

    @QtCore.Slot()
    def open_github(self) -> None:
        if self._url:
            QtGui.QDesktopServices.openUrl(QUrl(self._url))

    def _copy_code(self) -> None:
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.code_label.text())
            self.status_label.setText("Code copied. Waiting for GitHub authorization…")

    def reject(self) -> None:
        if not self._completed:
            self.cancel_requested.emit()
        super().reject()


__all__ = ["GitHubAccountDialog", "GitHubDeviceFlowDialog"]
