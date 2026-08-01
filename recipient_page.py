from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class RecipientPage(QtWidgets.QWidget):
    """Blocking project-entry page shown outside the normal tab interface."""

    recipient_submitted = QtCore.Signal(str, bool)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipientPage")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(1)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("RecipientPanel")
        panel.setMaximumWidth(520)
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
            QLabel#RecipientFieldLabel {
                color: #b0e0e6;
                font: 600 12px "Segoe UI";
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

        field_label = QtWidgets.QLabel("Recipient", panel)
        field_label.setObjectName("RecipientFieldLabel")
        panel_layout.addWidget(field_label)

        self.recipient_input = QtWidgets.QLineEdit(panel)
        self.recipient_input.setObjectName("RecipientInput")
        self.recipient_input.setAccessibleName("Recipient")
        self.recipient_input.returnPressed.connect(self.submit)
        self.recipient_input.textChanged.connect(self._clear_error)
        panel_layout.addWidget(self.recipient_input)

        self.error_label = QtWidgets.QLabel("", panel)
        self.error_label.setObjectName("RecipientError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        panel_layout.addWidget(self.error_label)

        self.custom_capitalization = QtWidgets.QCheckBox(
            "Keep capitalization exactly as typed",
            panel,
        )
        self.custom_capitalization.setObjectName(
            "RecipientCustomCapitalization"
        )
        self.custom_capitalization.setStyleSheet(
            "color: #9fb9bd; font: 11px 'Segoe UI';"
        )
        panel_layout.addWidget(self.custom_capitalization)

        action_row = QtWidgets.QHBoxLayout()
        action_row.addStretch(1)
        self.continue_button = QtWidgets.QPushButton("Continue", panel)
        self.continue_button.setObjectName("RecipientContinue")
        self.continue_button.clicked.connect(self.submit)
        action_row.addWidget(self.continue_button)
        panel_layout.addLayout(action_row)

        outer.addWidget(panel, 0, QtCore.Qt.AlignHCenter)
        outer.addStretch(2)

    def submit(self) -> None:
        recipient = " ".join(self.recipient_input.text().split())
        if not recipient:
            self.show_error("Enter a recipient before continuing.")
            self.recipient_input.setFocus(QtCore.Qt.OtherFocusReason)
            return
        self.recipient_input.setText(recipient)
        self.recipient_submitted.emit(
            recipient,
            self.custom_capitalization.isChecked(),
        )

    def show_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    def reset(self) -> None:
        self.recipient_input.clear()
        self.custom_capitalization.setChecked(False)
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
