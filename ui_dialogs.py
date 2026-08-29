from __future__ import annotations

"""Shared frameless dialogs for lightweight Letter Smith interactions."""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets


_FALLBACK_COLORS = {
    "background": "#0b0f15",
    "panel_background": "#101317",
    "control_background": "#171c22",
    "border": "#43505d",
    "text": "#f2f5f7",
    "muted_text": "#aeb8c6",
    "accent": "#00d0ff",
    "hover": "#132a31",
    "error": "#ff626c",
}


def _dialog_colors(parent: QtWidgets.QWidget | None) -> dict[str, str]:
    service = None
    ancestor = parent
    while ancestor is not None and service is None:
        service = getattr(ancestor, "theme_service", None)
        ancestor = ancestor.parentWidget()
    if service is None and parent is not None:
        service = getattr(parent.window(), "theme_service", None)
    tokens = getattr(service, "tokens", None)
    colors: dict[str, str] = {}
    for name, fallback in _FALLBACK_COLORS.items():
        value = str(getattr(tokens, name, fallback) or fallback)
        colors[name] = value if QtGui.QColor(value).isValid() else fallback
    return colors


class LetterSmithDialog(QtWidgets.QDialog):
    """Frameless themed panel used by small modal and popup windows."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        title: str = "",
        modal: bool = True,
        click_outside_dismiss: bool = False,
        width: int = 420,
    ) -> None:
        flags = (
            QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint
            if click_outside_dismiss
            else QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint
        )
        super().__init__(parent, flags)
        self._click_outside_dismiss = bool(click_outside_dismiss)
        self._outside_filter_installed = False
        self.colors = _dialog_colors(parent)
        self.setModal(bool(modal))
        if modal:
            self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(max(320, int(width)))
        self.setAccessibleName(title or "Letter Smith dialog")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QtWidgets.QFrame(self)
        self.panel.setObjectName("LetterSmithDialogPanel")
        outer.addWidget(self.panel)

        self.content_layout = QtWidgets.QVBoxLayout(self.panel)
        self.content_layout.setContentsMargins(28, 24, 28, 22)
        self.content_layout.setSpacing(18)

        self.heading: QtWidgets.QLabel | None = None
        if title:
            self.heading = QtWidgets.QLabel(title, self.panel)
            self.heading.setObjectName("LetterSmithDialogHeading")
            self.heading.setAlignment(QtCore.Qt.AlignCenter)
            self.heading.setWordWrap(True)
            self.content_layout.addWidget(self.heading)

        self._apply_style()

    def _apply_style(self) -> None:
        colors = self.colors
        self.setStyleSheet(
            "QFrame#LetterSmithDialogPanel{"
            f"background:{colors['panel_background']};"
            f"border:1px solid {colors['border']};border-radius:10px;}}"
            "QLabel#LetterSmithDialogHeading{"
            f"color:{colors['text']};background:transparent;border:none;"
            "font:700 14pt 'Segoe UI';}"
            "QLabel#LetterSmithDialogMessage{"
            f"color:{colors['text']};background:transparent;border:none;"
            "font:600 12pt 'Segoe UI';}"
            "QLabel#LetterSmithDialogDetail{"
            f"color:{colors['muted_text']};background:transparent;border:none;"
            "font:10pt 'Segoe UI';}"
            "QPushButton[dialogAction=\"true\"]{"
            f"background:{colors['control_background']};color:{colors['text']};"
            f"border:1px solid {colors['border']};border-radius:7px;"
            "font:700 11pt 'Segoe UI';padding:0 14px;}"
            "QPushButton[dialogAction=\"true\"]:hover{"
            f"background:{colors['hover']};border-color:{colors['accent']};}}"
            "QPushButton[dialogRole=\"secondary\"]{"
            f"color:{colors['accent']};border-color:{colors['accent']};}}"
            "QPushButton[dialogRole=\"destructive\"]{"
            f"color:{colors['error']};border-color:{colors['error']};}}"
            "QPushButton[dialogRole=\"destructive\"]:hover{"
            f"background:{colors['error']};color:{colors['background']};}}"
            "QPlainTextEdit#LetterSmithDialogDetails{"
            f"background:{colors['background']};color:{colors['muted_text']};"
            f"border:1px solid {colors['border']};border-radius:7px;"
            "font:9pt 'Consolas';padding:8px;}"
        )

    @staticmethod
    def _action_button(text: str, role: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setProperty("dialogAction", True)
        button.setProperty("dialogRole", role)
        button.setFixedHeight(42)
        return button

    @staticmethod
    def _uniform_button_width(buttons: list[QtWidgets.QPushButton]) -> None:
        if not buttons:
            return
        width = max(
            118,
            max(
                button.fontMetrics().horizontalAdvance(button.text()) + 34
                for button in buttons
            ),
        )
        width = min(width, 220)
        for button in buttons:
            button.setFixedWidth(width)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        application = QtWidgets.QApplication.instance()
        if (
            self._click_outside_dismiss
            and application is not None
            and not self._outside_filter_installed
        ):
            application.installEventFilter(self)
            self._outside_filter_installed = True
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
            self.move(center - self.rect().center())

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        application = QtWidgets.QApplication.instance()
        if application is not None and self._outside_filter_installed:
            application.removeEventFilter(self)
        self._outside_filter_installed = False
        super().hideEvent(event)

    def eventFilter(
        self,
        watched: QtCore.QObject,
        event: QtCore.QEvent,
    ) -> bool:
        if (
            self._click_outside_dismiss
            and self.isVisible()
            and event.type() == QtCore.QEvent.MouseButtonPress
        ):
            widget = watched if isinstance(watched, QtWidgets.QWidget) else None
            ancestor = widget
            while ancestor is not None:
                if ancestor is self:
                    return super().eventFilter(watched, event)
                ancestor = ancestor.parentWidget()
            if isinstance(event, QtGui.QMouseEvent):
                if self.frameGeometry().contains(event.globalPosition().toPoint()):
                    return super().eventFilter(watched, event)
            self.reject()
        return super().eventFilter(watched, event)


class LetterSmithConfirmationDialog(LetterSmithDialog):
    """Uniform question dialog with explicit, role-aware actions."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        question: str,
        primary_text: str = "Yes",
        secondary_text: str = "No",
        destructive_primary: bool = False,
        modal: bool = True,
        click_outside_dismiss: bool = False,
        title: str = "",
        secondary_accepts: bool = False,
        destructive_secondary: bool = False,
        cancel_text: str = "",
        width: int = 390,
    ) -> None:
        super().__init__(
            parent,
            title=title,
            modal=modal,
            click_outside_dismiss=click_outside_dismiss,
            width=width,
        )
        self.choice = ""
        self.message_label = QtWidgets.QLabel(question, self.panel)
        self.message_label.setObjectName("LetterSmithDialogMessage")
        self.message_label.setAlignment(QtCore.Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        self.content_layout.addWidget(self.message_label)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)
        self.cancel_button: QtWidgets.QPushButton | None = None
        buttons: list[QtWidgets.QPushButton] = []
        if cancel_text:
            self.cancel_button = self._action_button(cancel_text, "secondary")
            self.cancel_button.clicked.connect(self._cancel)
            actions.addWidget(self.cancel_button)
            buttons.append(self.cancel_button)

        secondary_role = "destructive" if destructive_secondary else "secondary"
        self.secondary_button = self._action_button(secondary_text, secondary_role)
        self.secondary_button.clicked.connect(
            self._accept_secondary if secondary_accepts else self._reject_secondary
        )
        actions.addWidget(self.secondary_button)
        buttons.append(self.secondary_button)

        role = "destructive" if destructive_primary else "primary"
        self.primary_button = self._action_button(primary_text, role)
        self.primary_button.clicked.connect(self._accept_primary)
        actions.addWidget(self.primary_button)
        buttons.append(self.primary_button)
        actions.addStretch(1)
        self._uniform_button_width(buttons)
        self.content_layout.addLayout(actions)
        self.secondary_button.setDefault(True)

    def _accept_primary(self) -> None:
        self.choice = "primary"
        self.accept()

    def _accept_secondary(self) -> None:
        self.choice = "secondary"
        self.accept()

    def _reject_secondary(self) -> None:
        self.choice = "secondary"
        self.reject()

    def _cancel(self) -> None:
        self.choice = "cancel"
        self.reject()


class LetterSmithMessageDialog(LetterSmithDialog):
    """Frameless information, warning, or error panel."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        title: str,
        message: str,
        detail: str = "",
        technical_details: str = "",
        action_text: str = "",
        destructive_action: bool = False,
        close_text: str = "Close",
        width: int = 470,
    ) -> None:
        super().__init__(parent, title=title, modal=True, width=width)
        self.action_requested = False
        self.message_label = QtWidgets.QLabel(message, self.panel)
        self.message_label.setObjectName("LetterSmithDialogMessage")
        self.message_label.setAlignment(QtCore.Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        self.content_layout.addWidget(self.message_label)
        if detail:
            detail_label = QtWidgets.QLabel(detail, self.panel)
            detail_label.setObjectName("LetterSmithDialogDetail")
            detail_label.setAlignment(QtCore.Qt.AlignCenter)
            detail_label.setWordWrap(True)
            self.content_layout.addWidget(detail_label)
        if technical_details:
            details = QtWidgets.QPlainTextEdit(self.panel)
            details.setObjectName("LetterSmithDialogDetails")
            details.setReadOnly(True)
            details.setPlainText(technical_details)
            details.setMaximumHeight(130)
            self.content_layout.addWidget(details)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)
        buttons: list[QtWidgets.QPushButton] = []
        if action_text:
            role = "destructive" if destructive_action else "primary"
            self.action_button = self._action_button(action_text, role)
            self.action_button.clicked.connect(self._accept_action)
            actions.addWidget(self.action_button)
            buttons.append(self.action_button)
        else:
            self.action_button = None
        self.close_button = self._action_button(close_text, "secondary")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        buttons.append(self.close_button)
        actions.addStretch(1)
        self._uniform_button_width(buttons)
        self.content_layout.addLayout(actions)
        self.close_button.setDefault(True)

    def _accept_action(self) -> None:
        self.action_requested = True
        self.accept()


class LetterSmithInputDialog(LetterSmithDialog):
    """Small frameless single-line input panel."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        title: str,
        prompt: str,
        text: str = "",
        accept_text: str = "Save",
        cancel_text: str = "Cancel",
        width: int = 440,
    ) -> None:
        super().__init__(parent, title=title, modal=True, width=width)
        self.prompt_label = QtWidgets.QLabel(prompt, self.panel)
        self.prompt_label.setObjectName("LetterSmithDialogMessage")
        self.prompt_label.setWordWrap(True)
        self.content_layout.addWidget(self.prompt_label)
        self.input = QtWidgets.QLineEdit(text, self.panel)
        self.input.setMinimumHeight(40)
        self.input.setStyleSheet(
            f"background:{self.colors['background']};color:{self.colors['text']};"
            f"border:1px solid {self.colors['border']};border-radius:7px;"
            "padding:0 10px;font:10pt 'Segoe UI';"
        )
        self.input.selectAll()
        self.content_layout.addWidget(self.input)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)
        self.cancel_button = self._action_button(cancel_text, "secondary")
        self.accept_button = self._action_button(accept_text, "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.accept_button.clicked.connect(self.accept)
        self.input.returnPressed.connect(self.accept)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.accept_button)
        actions.addStretch(1)
        self._uniform_button_width([self.cancel_button, self.accept_button])
        self.content_layout.addLayout(actions)

    @classmethod
    def get_text(
        cls,
        parent: Optional[QtWidgets.QWidget],
        title: str,
        prompt: str,
        *,
        text: str = "",
        accept_text: str = "Save",
    ) -> tuple[str, bool]:
        dialog = cls(
            parent,
            title=title,
            prompt=prompt,
            text=text,
            accept_text=accept_text,
        )
        accepted = dialog.exec() == QtWidgets.QDialog.Accepted
        return dialog.input.text(), accepted


def show_lettersmith_message(
    parent: QtWidgets.QWidget | None,
    title: str,
    message: str,
    *,
    detail: str = "",
    technical_details: str = "",
) -> None:
    dialog = LetterSmithMessageDialog(
        parent,
        title=title,
        message=message,
        detail=detail,
        technical_details=technical_details,
    )
    dialog.exec()


__all__ = [
    "LetterSmithConfirmationDialog",
    "LetterSmithDialog",
    "LetterSmithInputDialog",
    "LetterSmithMessageDialog",
    "show_lettersmith_message",
]
