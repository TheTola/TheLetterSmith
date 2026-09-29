from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from image_button import ArtworkButton
from project_paths import application_paths
from protected_projects import reserved_identity_field_reason
from ui_dialogs import show_lettersmith_message
from ui_theme import ButtonTier, apply_button_tier


class RecipientPage(QtWidgets.QWidget):
    """Blocking project-entry page shown outside the normal tab interface."""

    recipient_submitted = QtCore.Signal(str, bool)
    load_requested = QtCore.Signal()
    stock_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipientPage")
        self.project_root = Path(
            getattr(self.window(), "project_root", application_paths().workspace_root)
        ).resolve()

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(1)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("RecipientPanel")
        panel.setStyleSheet(
            """
            QFrame#RecipientPanel {
                background: #171b20;
                border: 1px solid #3f555c;
                border-radius: 12px;
            }
            QLabel#RecipientQuestion {
                color: #e0ffff;
                font: 600 24px "Segoe UI";
            }
            QLabel#RecipientError {
                color: #ff9b9b;
                font: 11px "Segoe UI";
            }
            QLineEdit {
                background: #101317;
                color: #f4ffff;
                border: 1px solid #53666d;
                border-radius: 6px;
                padding: 9px 10px;
                font: 14px "Segoe UI";
            }
            QLineEdit:focus {
                border-color: #00b2b2;
            }
            QPushButton {
                background: #007f82;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 9px 22px;
                font: 600 12px "Segoe UI";
            }
            QPushButton:hover {
                background: #00979b;
            }
            """
        )
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(30, 28, 30, 28)
        panel_layout.setSpacing(10)

        question = QtWidgets.QLabel("Who is this letter for?", panel)
        question.setObjectName("RecipientQuestion")
        panel_layout.addWidget(question)

        self.recipient_input = QtWidgets.QLineEdit(panel)
        self.recipient_input.setObjectName("RecipientInput")
        self.recipient_input.setProperty("themeFontRole", "userEntry")
        self.recipient_input.setAccessibleName("Recipient")
        self.recipient_input.returnPressed.connect(self.submit)
        self.recipient_input.textChanged.connect(self._clear_error)
        panel_layout.addWidget(self.recipient_input)

        self.error_label = QtWidgets.QLabel("", panel)
        self.error_label.setObjectName("RecipientError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        panel_layout.addWidget(self.error_label)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addStretch(1)
        self.load_button = ArtworkButton(
            "Load",
            self.project_root,
            "AButton.png",
            panel,
        )
        self.load_button.setObjectName("RecipientLoad")
        apply_button_tier(self.load_button, ButtonTier.MEDIUM)
        self.load_button.clicked.connect(self.load_requested.emit)
        action_row.addWidget(self.load_button)
        self.stock_button = ArtworkButton(
            "Stock",
            self.project_root,
            "AButton.png",
            panel,
        )
        self.stock_button.setObjectName("RecipientStock")
        apply_button_tier(self.stock_button, ButtonTier.MEDIUM)
        self.stock_button.clicked.connect(self.stock_requested.emit)
        action_row.addWidget(self.stock_button)
        self.begin_button = ArtworkButton(
            "Begin",
            self.project_root,
            "DButton.png",
            panel,
        )
        self.begin_button.setObjectName("RecipientContinue")
        apply_button_tier(self.begin_button, ButtonTier.MEDIUM, bold=True)
        self.begin_button.clicked.connect(self.submit)
        self.continue_button = self.begin_button
        action_row.addWidget(self.begin_button)
        panel_layout.addLayout(action_row)

        outer.addWidget(panel, 0, QtCore.Qt.AlignHCenter)
        outer.addStretch(2)
        self._panel = panel
        self.apply_theme_assets(getattr(self.window(), "theme_service", None))

    def apply_theme_assets(self, theme_service: object | None = None) -> None:
        service = theme_service or getattr(self.window(), "theme_service", None)
        for button in (
            self.load_button,
            self.stock_button,
            self.begin_button,
        ):
            button.apply_theme_assets(service)
        tokens = getattr(service, "tokens", None)
        if tokens is None:
            return
        self._panel.setStyleSheet(
            "QFrame#RecipientPanel{"
            f"background:{tokens.panel_background};"
            f"border:1px solid {tokens.border};border-radius:12px;}}"
            "QLabel#RecipientQuestion{"
            f"color:{tokens.highlight};font:600 24px 'Segoe UI';}}"
            "QLabel#RecipientError{"
            f"color:{tokens.error};font:11px 'Segoe UI';}}"
            "QLineEdit{"
            f"background:{tokens.control_background};color:{tokens.text};"
            f"border:1px solid {tokens.border};border-radius:6px;"
            "padding:9px 10px;font:14px 'Segoe UI';}"
            f"QLineEdit:focus{{border-color:{tokens.accent};}}"
        )

    def submit(self) -> None:
        recipient = " ".join(self.recipient_input.text().split())
        if not recipient:
            self.show_error("Enter a recipient before beginning.")
            self.recipient_input.setFocus(QtCore.Qt.OtherFocusReason)
            return
        if reserved_identity_field_reason("recipient", recipient):
            message = f"{recipient} is an invalid recipient."
            show_lettersmith_message(
                self,
                "Invalid Recipient",
                message,
            )
            self.recipient_input.setFocus(QtCore.Qt.OtherFocusReason)
            self.recipient_input.selectAll()
            return
        self.recipient_input.setText(recipient)
        self.recipient_submitted.emit(
            recipient,
            True,
        )

    def show_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    def reset(self) -> None:
        self.recipient_input.clear()
        self.show_error("")
        self.focus_recipient()

    def focus_recipient(self) -> None:
        QtCore.QTimer.singleShot(
            0,
            lambda: self.recipient_input.setFocus(
                QtCore.Qt.OtherFocusReason
            ),
        )

    def _clear_error(self) -> None:
        if self.error_label.isVisible():
            self.show_error("")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.focus_recipient()


__all__ = ["RecipientPage"]
