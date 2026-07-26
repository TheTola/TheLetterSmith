from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets


class StatusLevel(Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_LEVEL_PRIORITY = {
    StatusLevel.INFO: 0,
    StatusLevel.SUCCESS: 1,
    StatusLevel.WARNING: 2,
    StatusLevel.ERROR: 3,
}

_LEVEL_COLORS = {
    StatusLevel.INFO: ("#d8e6f2", "rgba(80, 120, 150, 0.18)"),
    StatusLevel.SUCCESS: ("#a9d6b2", "rgba(65, 125, 80, 0.18)"),
    StatusLevel.WARNING: ("#e8c66a", "rgba(160, 115, 35, 0.20)"),
    StatusLevel.ERROR: ("#ef8d86", "rgba(155, 55, 50, 0.20)"),
}


@dataclass(frozen=True)
class StatusMessage:
    text: str
    level: StatusLevel
    persistent: bool = False
    key: str = ""
    sequence: int = 0


class StatusController:
    """Select the most important active status without losing persistent warnings."""

    def __init__(self) -> None:
        self._persistent: dict[str, StatusMessage] = {}
        self._transient: Optional[StatusMessage] = None
        self._listeners: list[Callable[[Optional[StatusMessage]], None]] = []
        self._sequence = 0

    @property
    def current(self) -> Optional[StatusMessage]:
        candidates = [*self._persistent.values()]
        if self._transient is not None:
            candidates.append(self._transient)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda message: (_LEVEL_PRIORITY[message.level], message.sequence),
        )

    def connect(self, listener: Callable[[Optional[StatusMessage]], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)
        listener(self.current)

    def disconnect(self, listener: Callable[[Optional[StatusMessage]], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def publish(
        self,
        text: str,
        level: StatusLevel = StatusLevel.INFO,
        *,
        persistent: bool = False,
        key: str = "",
    ) -> StatusMessage:
        self._sequence += 1
        message = StatusMessage(
            text=str(text),
            level=level,
            persistent=persistent,
            key=key,
            sequence=self._sequence,
        )
        if persistent:
            stable_key = key or "persistent"
            self._persistent[stable_key] = message
        else:
            self._transient = message
        self._notify()
        return self.current or message

    def clear(self, key: Optional[str] = None) -> Optional[StatusMessage]:
        if key is None:
            self._transient = None
        else:
            self._persistent.pop(key, None)
        self._notify()
        return self.current

    def clear_all(self) -> None:
        self._persistent.clear()
        self._transient = None
        self._notify()

    def _notify(self) -> None:
        visible = self.current
        for listener in tuple(self._listeners):
            listener(visible)


class StatusBanner(QtWidgets.QFrame):
    """Compact reusable banner for Images, Sound, Message, and Forge."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        controller: Optional[StatusController] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statusBanner")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._label = QtWidgets.QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self._label)
        self.controller = controller or StatusController()
        self.controller.connect(self._show_message)
        self.hide()

    def _show_message(self, message: Optional[StatusMessage]) -> None:
        if message is None or not message.text:
            self._label.clear()
            self.hide()
            return
        foreground, background = _LEVEL_COLORS[message.level]
        self.setStyleSheet(
            "#statusBanner {"
            f"color: {foreground}; background: {background};"
            "border-radius: 7px;"
            "}"
        )
        self._label.setText(message.text)
        self.show()


__all__ = [
    "StatusBanner",
    "StatusController",
    "StatusLevel",
    "StatusMessage",
]
