from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


MINIMIZE_SYMBOL = "\u2212"
MAXIMIZE_SYMBOL = "\u25a1"
RESTORE_SYMBOL = "\u2750"
CLOSE_SYMBOL = "\u00d7"


class StandardTitleBar(QtWidgets.QFrame):
    def __init__(
        self,
        window: QtWidgets.QWidget,
        title: str,
        *,
        show_minimize: bool = True,
        on_close: Optional[Callable[[], None]] = None,
        on_minimize: Optional[Callable[[], None]] = None,
        on_toggle_maximize: Optional[Callable[[], None]] = None,
        is_maximized: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._on_close = on_close or window.close
        self._on_minimize = on_minimize or window.showMinimized
        self._on_toggle_maximize = on_toggle_maximize
        self._is_maximized = is_maximized or (lambda: False)
        self._drag_offset: Optional[QtCore.QPoint] = None

        self.setObjectName("standardTitleBar")
        self.setFixedHeight(40)

        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(6, 0, 6, 0)
        self._layout.setSpacing(8)

        self.title_label = QtWidgets.QLabel(title, self)
        self.title_label.setObjectName("windowTitleLabel")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._layout.addWidget(self.title_label)
        self._layout.addStretch(1)

        self._controls_layout = QtWidgets.QHBoxLayout()
        self._controls_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_layout.setSpacing(6)
        self._layout.addLayout(self._controls_layout)

        self.btn_minimize = self._make_button(
            MINIMIZE_SYMBOL,
            "Minimize",
            "windowControlButton",
            self._handle_minimize,
        )
        self.btn_minimize.setVisible(show_minimize)
        self._controls_layout.addWidget(self.btn_minimize)

        self.btn_maximize = self._make_button(
            MAXIMIZE_SYMBOL,
            "Maximize",
            "windowControlButton",
            self._handle_toggle_maximize,
        )
        self.btn_maximize.setVisible(self._on_toggle_maximize is not None)
        self._controls_layout.addWidget(self.btn_maximize)

        self.btn_close = self._make_button(
            CLOSE_SYMBOL,
            "Close",
            "windowCloseButton",
            self._handle_close,
        )
        self._controls_layout.addWidget(self.btn_close)

        self.setStyleSheet(
            """
            QFrame#standardTitleBar {
                background: transparent;
            }
            QLabel#windowTitleLabel {
                color: #eef4ff;
                font-family: 'Segoe UI Semibold';
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 0.2px;
            }
            QPushButton#windowControlButton,
            QPushButton#windowCloseButton,
            QPushButton#windowHelpButton {
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                border-radius: 7px;
                border: 1px solid transparent;
                background: transparent;
                color: #d7dee8;
                font-family: 'Segoe UI Symbol';
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#windowControlButton:hover {
                background: rgba(60, 85, 120, 0.24);
                border-color: rgba(109, 131, 163, 0.34);
                color: #ffffff;
            }
            QPushButton#windowHelpButton {
                border-radius: 15px;
                border-color: rgba(109, 131, 163, 0.28);
                background: rgba(24, 28, 35, 0.92);
                font-family: 'Segoe UI Semibold';
                font-size: 15px;
            }
            QPushButton#windowHelpButton:hover {
                background: rgba(60, 85, 120, 0.24);
                border-color: rgba(109, 131, 163, 0.42);
                color: #ffffff;
            }
            QPushButton#windowCloseButton:hover {
                background: rgba(255, 59, 48, 0.18);
                border-color: rgba(255, 59, 48, 0.34);
                color: #ffffff;
            }
            """
        )
        self.sync_window_state()

    def _make_button(
        self,
        text: str,
        tooltip: str,
        object_name: str,
        slot: Callable[[], None],
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text, self)
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def insert_control_button(
        self,
        text: str,
        tooltip: str,
        slot: Callable[[], None],
        *,
        object_name: str = "windowControlButton",
    ) -> QtWidgets.QPushButton:
        button = self._make_button(text, tooltip, object_name, slot)
        self._controls_layout.insertWidget(0, button)
        return button

    def sync_window_state(self) -> None:
        if not self.btn_maximize.isVisible():
            return
        maximized = bool(self._is_maximized())
        self.btn_maximize.setText(RESTORE_SYMBOL if maximized else MAXIMIZE_SYMBOL)
        self.btn_maximize.setToolTip("Restore" if maximized else "Maximize")

    def _handle_close(self) -> None:
        self._on_close()

    def _handle_minimize(self) -> None:
        self._on_minimize()

    def _handle_toggle_maximize(self) -> None:
        if self._on_toggle_maximize is None:
            return
        self._on_toggle_maximize()
        self.sync_window_state()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.LeftButton
            and not bool(self._is_maximized())
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._on_toggle_maximize is not None:
            self._handle_toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
