from __future__ import annotations

import sys
from typing import Callable, Optional
from weakref import ref

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from shiboken6 import isValid
from ui_theme import MAXIMIZE_THEME_ASSET, RESTORE_THEME_ASSET_CANDIDATES


MINIMIZE_SYMBOL = "\u2212"
MAXIMIZE_SYMBOL = "\u25a1"
RESTORE_SYMBOL = "\u2750"
CLOSE_SYMBOL = "\u00d7"
TITLE_BAR_CONTROL_PX = 40
TITLE_BAR_ICON_PX = 36
FRAME_RESIZE_MARGIN_PX = 8


def bounded_window_geometry(
    geometry: QtCore.QRect,
    available: QtCore.QRect,
    minimum: QtCore.QSize = QtCore.QSize(1, 1),
) -> QtCore.QRect:
    """Fit logical window coordinates inside one screen, including negative origins."""
    width = min(available.width(), max(minimum.width(), geometry.width(), 1))
    height = min(available.height(), max(minimum.height(), geometry.height(), 1))
    return QtCore.QRect(
        min(max(geometry.x(), available.left()), available.right() - width + 1),
        min(max(geometry.y(), available.top()), available.bottom() - height + 1),
        width,
        height,
    )


def restored_window_geometry(
    geometry: QtCore.QRect,
    available: QtCore.QRect,
    minimum: QtCore.QSize,
    default_size: QtCore.QSize,
) -> QtCore.QRect:
    """Keep a screen-sized saved rectangle from masquerading as a restore."""
    fitted = bounded_window_geometry(geometry, available, minimum)
    # Legacy Qt geometry can retain an almost-maximized normal rectangle,
    # inset by a native title bar even though our window is frameless.
    if (
        fitted.width() >= available.width() - TITLE_BAR_CONTROL_PX
        and fitted.height() >= available.height() - TITLE_BAR_CONTROL_PX
    ):
        size = default_size.boundedTo(available.size() * 0.85).expandedTo(minimum)
        fitted.setSize(size.boundedTo(available.size()))
        fitted.moveCenter(available.center())
    return fitted


def fit_window_to_screen(
    window: QtWidgets.QWidget,
    minimum: QtCore.QSize,
    geometry: QtCore.QRect | None = None,
    *,
    screen: QtGui.QScreen | None = None,
) -> QtCore.QRect:
    """Recover a frameless window without taking ownership of ordinary resizing."""
    target = QtCore.QRect(geometry if geometry is not None else window.geometry())
    screens = QtGui.QGuiApplication.screens()
    if not screens:
        return target

    def screen_rank(screen: QtGui.QScreen) -> tuple[int, int]:
        bounds = screen.availableGeometry()
        overlap = bounds.intersected(target)
        center = target.center()
        dx = max(bounds.left() - center.x(), 0, center.x() - bounds.right())
        dy = max(bounds.top() - center.y(), 0, center.y() - bounds.bottom())
        return overlap.width() * overlap.height(), -(dx * dx + dy * dy)

    available = (screen or max(screens, key=screen_rank)).availableGeometry()
    window.setMinimumSize(minimum.boundedTo(available.size()))
    fitted = bounded_window_geometry(target, available, window.minimumSize())
    if not window.isMaximized() and not window.isFullScreen():
        if fitted != window.geometry():
            window.setGeometry(fitted)
    return fitted


def screen_for_launcher(launcher: QtWidgets.QWidget | None) -> QtGui.QScreen | None:
    """Resolve the control's current physical screen, never a tool's saved screen."""
    if launcher is not None and isValid(launcher):
        if not launcher.isVisible():
            launcher = launcher.window()
        center = launcher.mapToGlobal(launcher.rect().center())
        screen = QtGui.QGuiApplication.screenAt(center)
        if screen is not None:
            return screen
        return launcher.window().screen()
    return QtGui.QGuiApplication.primaryScreen()


def place_window_on_launcher(
    window: QtWidgets.QWidget,
    launcher: QtWidgets.QWidget | None = None,
    *,
    geometry: QtCore.QRect | None = None,
    minimum: QtCore.QSize | None = None,
    near: bool = False,
    screen: QtGui.QScreen | None = None,
) -> QtCore.QRect:
    """Keep a tool's saved size/valid position within its launcher's work area."""
    if launcher is not None and isValid(launcher):
        window._lettersmith_launch_widget = ref(launcher)
    if launcher is None:
        launch_ref = getattr(window, "_lettersmith_launch_widget", None)
        launcher = launch_ref() if launch_ref is not None else None
    if launcher is None or not isValid(launcher):
        launcher = window.parentWidget()
    if launcher is None:
        active = QtWidgets.QApplication.activeWindow()
        launcher = active if active is not window else None
    if screen is None or not isValid(screen):
        screen = screen_for_launcher(launcher)
    target = QtCore.QRect(geometry if geometry is not None else window.geometry())
    if screen is None:
        return target
    available = screen.availableGeometry()
    # QWidget geometry excludes native decorations; reserve them inside the work area.
    client, frame = window.geometry(), window.frameGeometry()
    margins = QtCore.QMargins(
        max(0, client.left() - frame.left()), max(0, client.top() - frame.top()),
        max(0, frame.right() - client.right()), max(0, frame.bottom() - client.bottom()),
    )
    client_area = available.marginsRemoved(margins)
    size_floor = minimum if minimum is not None else window.minimumSize()
    window.setMinimumSize(size_floor.boundedTo(client_area.size()))
    if not client_area.contains(target.center()):
        if launcher is not None and isValid(launcher):
            anchor = QtCore.QRect(launcher.mapToGlobal(QtCore.QPoint()), launcher.size())
            if near:
                y = anchor.bottom() + 9
                if y + target.height() > client_area.bottom() + 1:
                    y = anchor.top() - target.height() - 8
                target.moveTopLeft(QtCore.QPoint(anchor.left(), y))
            else:
                target.moveCenter(launcher.window().frameGeometry().center())
        else:
            target.moveCenter(client_area.center())
    target = bounded_window_geometry(target, client_area, window.minimumSize())
    if window.isMaximized() or window.isFullScreen():
        if window.screen() is not screen:
            window.setScreen(screen)
            window.setGeometry(screen.geometry() if window.isFullScreen() else available)
    elif target != window.geometry():
        window.setGeometry(target)
    return target


class _WindowPlacementGuard(QtCore.QObject):
    """Cover parented dialogs, menus and custom tools without policing user moves."""

    def __init__(self, application: QtWidgets.QApplication) -> None:
        super().__init__(application)
        self._launcher = None

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not isinstance(watched, QtWidgets.QWidget):
            return False
        if event.type() in (
            QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
            QtCore.QEvent.KeyPress, QtCore.QEvent.ContextMenu, QtCore.QEvent.ToolTip,
        ):
            launch_ref = ref(watched)
            self._launcher = launch_ref

            def clear_launcher() -> None:
                if self._launcher is launch_ref:
                    self._launcher = None

            QtCore.QTimer.singleShot(0, self, clear_launcher)
        if (
            event.type() == QtCore.QEvent.Show
            and not event.spontaneous()
            and watched.isWindow()
            and watched.parentWidget() is not None
        ):
            launcher = watched.parentWidget()
            initiator = self._launcher() if self._launcher is not None else None
            if (
                initiator is not None and isValid(initiator)
                and initiator.window() is launcher.window()
            ):
                launcher = initiator
            watched._lettersmith_launch_widget = ref(launcher)
            place_window_on_launcher(watched, launcher)

            def finish_placement() -> None:
                if watched.isVisible():
                    place_window_on_launcher(watched)

            # Final layout/native frame margins and custom showEvent positions are
            # known after Show. The QObject context cancels this if the tool dies.
            QtCore.QTimer.singleShot(0, watched, finish_placement)
        return False


def install_window_placement_guard(
    application: QtWidgets.QApplication,
) -> _WindowPlacementGuard:
    """Install once at application startup; leave unparented main windows alone."""
    attribute = "_lettersmith_window_placement_guard"
    guard = getattr(application, attribute, None)
    if not isinstance(guard, _WindowPlacementGuard):
        guard = _WindowPlacementGuard(application)
        application.installEventFilter(guard)
        setattr(application, attribute, guard)
    return guard


_WM_NCHITTEST = 0x0084
_WM_ENTERSIZEMOVE = 0x0231
_WM_EXITSIZEMOVE = 0x0232
_HTCAPTION = 2
_HTMAXBUTTON = 9
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17


def resize_edges_at_point(
    size: QtCore.QSize,
    point: QtCore.QPoint,
    margin: int = FRAME_RESIZE_MARGIN_PX,
) -> Qt.Edge:
    """Return the adjacent window edges represented by a local point."""
    width = max(0, int(size.width()))
    height = max(0, int(size.height()))
    x = int(point.x())
    y = int(point.y())
    margin = max(1, int(margin))
    if not (0 <= x < width and 0 <= y < height):
        return Qt.Edge(0)

    edges = Qt.Edge(0)
    if x < margin:
        edges |= Qt.LeftEdge
    elif x >= width - margin:
        edges |= Qt.RightEdge
    if y < margin:
        edges |= Qt.TopEdge
    elif y >= height - margin:
        edges |= Qt.BottomEdge
    return edges


def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
    if edges in (
        Qt.TopEdge | Qt.LeftEdge,
        Qt.BottomEdge | Qt.RightEdge,
    ):
        return Qt.SizeFDiagCursor
    if edges in (
        Qt.TopEdge | Qt.RightEdge,
        Qt.BottomEdge | Qt.LeftEdge,
    ):
        return Qt.SizeBDiagCursor
    if edges & (Qt.LeftEdge | Qt.RightEdge):
        return Qt.SizeHorCursor
    return Qt.SizeVerCursor


def native_resize_hit_test(edges: Qt.Edge) -> int:
    """Translate Qt resize edges into the corresponding Windows hit-test."""
    return {
        Qt.LeftEdge: _HTLEFT,
        Qt.RightEdge: _HTRIGHT,
        Qt.TopEdge: _HTTOP,
        Qt.TopEdge | Qt.LeftEdge: _HTTOPLEFT,
        Qt.TopEdge | Qt.RightEdge: _HTTOPRIGHT,
        Qt.BottomEdge: _HTBOTTOM,
        Qt.BottomEdge | Qt.LeftEdge: _HTBOTTOMLEFT,
        Qt.BottomEdge | Qt.RightEdge: _HTBOTTOMRIGHT,
    }.get(edges, 0)


class FramelessWindowController(QtCore.QObject):
    """Give a custom-framed QWidget native move, resize, and Snap behavior."""

    system_interaction_finished = QtCore.Signal()

    def __init__(
        self,
        window: QtWidgets.QWidget,
        maximize_button: Optional[QtWidgets.QWidget] = None,
        *,
        resize_margin: int = FRAME_RESIZE_MARGIN_PX,
        title_bar: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._maximize_button = maximize_button
        self._title_bar = title_bar
        self._system_interaction_active = False
        self._resize_margin = max(1, int(resize_margin))
        self._cursor_target: Optional[QtWidgets.QWidget] = None
        self._cursor_was_explicit = False
        self._cursor_before_resize = QtGui.QCursor()

        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self._enable_mouse_tracking(window)

    def _enable_mouse_tracking(self, widget: QtWidgets.QWidget) -> None:
        widget.setMouseTracking(True)
        for child in widget.findChildren(QtWidgets.QWidget):
            child.setMouseTracking(True)

    def _enable_native_resize_style(self) -> None:
        if (
            sys.platform != "win32"
            or QtGui.QGuiApplication.platformName() != "windows"
            or not self._window.windowFlags() & Qt.FramelessWindowHint
            or self._window.windowHandle() is None
        ):
            return
        # WinIdChange also arrives during destruction. winId() would recreate
        # the closing dialog and native ancestor surfaces in the main window.
        hwnd = int(self._window.internalWinId())
        if not hwnd:
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongW.restype = wintypes.LONG
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
        user32.SetWindowLongW.restype = wintypes.LONG
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        if not style & 0x00040000:  # WS_THICKFRAME
            # Qt removes this flag for frameless windows. Native resize hits
            # require it; Qt still removes the visible frame in WM_NCCALCSIZE.
            user32.SetWindowLongW(hwnd, -16, style | 0x00040000)
            # Refresh non-client metrics without moving, resizing or activating.
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)

    def _is_caption_point(self, point: QtCore.QPoint) -> bool:
        title = self._title_bar
        if title is None or not title.isVisible():
            return False
        local = title.mapFrom(self._window, point)
        if not title.rect().contains(local):
            return False
        child = title.childAt(local)
        return child is None or isinstance(child, QtWidgets.QLabel)

    @property
    def system_interaction_active(self) -> bool:
        return self._system_interaction_active

    def start_system_move(self) -> bool:
        handle = self._window.windowHandle()
        return bool(handle is not None and handle.startSystemMove())

    def set_native_maximized(self, maximized: bool) -> bool:
        """Keep Windows and Qt in the same state for a visible frameless window."""
        if (
            sys.platform != "win32"
            or QtGui.QGuiApplication.platformName() != "windows"
            or not self._window.isVisible()
            or self._window.windowHandle() is None
        ):
            return False
        import ctypes
        from ctypes import wintypes

        self._enable_native_resize_style()
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = wintypes.BOOL
        # Qt's frameless showNormal/showMaximized path only moves the window.
        # It cannot clear WS_MAXIMIZE after a native maximize or a maximized
        # launch, so Windows rejects subsequent restores and border resizing.
        user32.ShowWindow(int(self._window.winId()), 3 if maximized else 9)
        return True

    def start_system_resize(self, edges: Qt.Edge) -> bool:
        if not edges or self._window.isMaximized() or self._window.isFullScreen():
            return False
        handle = self._window.windowHandle()
        return bool(handle is not None and handle.startSystemResize(edges))

    def _restore_cursor(self) -> None:
        target = self._cursor_target
        self._cursor_target = None
        if target is None:
            return
        try:
            if self._cursor_was_explicit:
                target.setCursor(self._cursor_before_resize)
            else:
                target.unsetCursor()
        except RuntimeError:
            pass

    def _show_resize_cursor(
        self,
        target: QtWidgets.QWidget,
        edges: Qt.Edge,
    ) -> None:
        if self._cursor_target is not target:
            self._restore_cursor()
            self._cursor_target = target
            self._cursor_was_explicit = target.testAttribute(Qt.WA_SetCursor)
            self._cursor_before_resize = QtGui.QCursor(target.cursor())
        target.setCursor(_cursor_for_edges(edges))

    def _event_edges(self, event: QtGui.QMouseEvent) -> Qt.Edge:
        global_point = event.globalPosition().toPoint()
        local_point = self._window.mapFromGlobal(global_point)
        return resize_edges_at_point(
            self._window.size(),
            local_point,
            self._resize_margin,
        )

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._window and event.type() in (
            QtCore.QEvent.Show, QtCore.QEvent.WinIdChange,
        ):
            self._enable_native_resize_style()
        if isinstance(watched, QtWidgets.QWidget):
            if event.type() == QtCore.QEvent.ChildAdded:
                child = event.child()
                if (
                    isinstance(child, QtWidgets.QWidget)
                    and child.window() is self._window
                ):
                    self._enable_mouse_tracking(child)

            if watched.window() is self._window:
                if event.type() == QtCore.QEvent.MouseMove:
                    edges = self._event_edges(event)
                    if (
                        edges
                        and not self._window.isMaximized()
                        and not self._window.isFullScreen()
                    ):
                        self._show_resize_cursor(watched, edges)
                    else:
                        self._restore_cursor()
                elif event.type() == QtCore.QEvent.MouseButtonPress:
                    if event.button() == Qt.LeftButton:
                        edges = self._event_edges(event)
                        if self.start_system_resize(edges):
                            self._restore_cursor()
                            event.accept()
                            return True
                elif event.type() in (
                    QtCore.QEvent.Hide,
                    QtCore.QEvent.WindowStateChange,
                ):
                    self._restore_cursor()
        return super().eventFilter(watched, event)

    def native_event(self, event_type: object, message: object) -> tuple[bool, int]:
        """Expose native resize borders and an optional Windows Snap target."""
        if sys.platform != "win32" or bytes(event_type) != b"windows_generic_MSG":
            return False, 0

        try:
            import ctypes
            from ctypes import wintypes

            native_message = wintypes.MSG.from_address(int(message))
            message_id = int(native_message.message)
            if message_id not in (_WM_NCHITTEST, _WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE):
                return False, 0
            if int(native_message.hWnd or 0) != int(self._window.internalWinId()):
                return False, 0
            if message_id == _WM_ENTERSIZEMOVE:
                self._system_interaction_active = True
                return False, 0
            if message_id == _WM_EXITSIZEMOVE:
                self._system_interaction_active = False
                self.system_interaction_finished.emit()
                return False, 0
            if message_id != _WM_NCHITTEST:
                return False, 0

            packed_position = int(native_message.lParam)
            screen_point = wintypes.POINT(
                ctypes.c_short(packed_position & 0xFFFF).value,
                ctypes.c_short((packed_position >> 16) & 0xFFFF).value,
            )
            ctypes.windll.user32.MapWindowPoints(
                None,
                native_message.hWnd,
                ctypes.byref(screen_point),
                1,
            )
            scale = max(1.0, float(self._window.devicePixelRatioF()))
            local_point = QtCore.QPoint(
                round(screen_point.x / scale),
                round(screen_point.y / scale),
            )
            if not self._window.isMaximized() and not self._window.isFullScreen():
                resize_hit = native_resize_hit_test(
                    resize_edges_at_point(
                        self._window.size(),
                        local_point,
                        self._resize_margin,
                    )
                )
                if resize_hit:
                    return True, resize_hit

            if self._is_caption_point(local_point):
                return True, _HTCAPTION

            button = self._maximize_button
            if button is not None and button.isVisible() and button.isEnabled():
                button_top_left = button.mapTo(self._window, QtCore.QPoint())
                button_rect = QtCore.QRect(button_top_left, button.size())
                if button_rect.contains(local_point):
                    return True, _HTMAXBUTTON
        except (AttributeError, OSError, TypeError, ValueError):
            return False, 0
        return False, 0


def _theme_rgba(color: object, alpha: int) -> str:
    value = QtGui.QColor(str(color or ""))
    if not value.isValid():
        value = QtGui.QColor("#7f9099")
    return f"rgba({value.red()},{value.green()},{value.blue()},{alpha})"


def _service_font_family(service: object) -> str:
    family = getattr(service, "app_font_family", "Segoe UI")
    if callable(family):
        family = family()
    return str(family or "Segoe UI").strip() or "Segoe UI"


def _qss_font_family(family: object) -> str:
    return str(family or "Segoe UI").replace("\\", "\\\\").replace("'", "\\'")


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
        close_icon_asset: str = "titlebar/close.png",
        close_fallback_symbol: str = CLOSE_SYMBOL,
        close_danger: bool = True,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._on_close = on_close or window.close
        self._on_minimize = on_minimize or window.showMinimized
        self._on_toggle_maximize = on_toggle_maximize
        self._is_maximized = is_maximized or (lambda: False)
        self._close_icon_asset = str(close_icon_asset)
        self._theme_service: object | None = None

        self.setObjectName("standardTitleBar")
        self.setFixedHeight(TITLE_BAR_CONTROL_PX + 8)

        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(8, 0, 8, 0)
        self._layout.setSpacing(6)

        self.icon_label = QtWidgets.QLabel(self)
        self.icon_label.setObjectName("windowIconLabel")
        self.icon_label.setFixedSize(TITLE_BAR_CONTROL_PX, TITLE_BAR_CONTROL_PX)
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon = window.windowIcon()
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX))
            self._layout.addWidget(self.icon_label)

        self.title_label = QtWidgets.QLabel(title, self)
        self.title_label.setObjectName("windowTitleLabel")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._layout.addWidget(self.title_label)

        self._content_layout = QtWidgets.QHBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        self._layout.addLayout(self._content_layout)

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
            close_fallback_symbol,
            "Close",
            "windowCloseButton" if close_danger else "windowControlButton",
            self._handle_close,
        )
        self._controls_layout.addWidget(self.btn_close)

        self._apply_style(
            title_color="#7f9099",
            muted_color="#68747a",
            highlight_color="#f1f3f4",
            accent_color="#7f9099",
            error_color="#d85c6a",
            pressed_color="#f2fbff",
            font_family="Segoe UI",
        )
        window.installEventFilter(self)
        self.window_controller = FramelessWindowController(
            window,
            self.btn_maximize,
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
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(TITLE_BAR_CONTROL_PX, TITLE_BAR_CONTROL_PX)
        button.setIconSize(QtCore.QSize(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX))
        button.setProperty("fallbackSymbol", text)
        symbol_font = QtGui.QFont("Segoe UI Symbol", 15)
        symbol_font.setBold(False)
        button.setFont(symbol_font)
        button.clicked.connect(slot)
        return button

    def _apply_style(
        self,
        *,
        title_color: str,
        muted_color: str,
        highlight_color: str,
        accent_color: str,
        error_color: str,
        pressed_color: str,
        font_family: str,
    ) -> None:
        family = _qss_font_family(font_family)
        self.setStyleSheet(
            "QFrame#standardTitleBar{background:transparent;}"
            "QLabel#windowTitleLabel{"
            f"color:{title_color};background:transparent;"
            f"font-family:'{family}';font-size:16px;font-weight:700;"
            "letter-spacing:1px;}"
            "QPushButton#windowControlButton,"
            "QPushButton#windowCloseButton,"
            "QPushButton#windowHelpButton{"
            f"color:{muted_color};background:transparent;"
            "border:none;padding:0;margin:0;}"
            "QPushButton#windowControlButton:hover,"
            "QPushButton#windowHelpButton:hover{"
            f"color:{highlight_color};"
            f"background:{_theme_rgba(accent_color, 38)};}}"
            "QPushButton#windowCloseButton:hover{"
            f"color:{highlight_color};"
            f"background:{_theme_rgba(error_color, 77)};}}"
            "QPushButton#windowControlButton:pressed,"
            "QPushButton#windowCloseButton:pressed,"
            "QPushButton#windowHelpButton:pressed{"
            f"background:{_theme_rgba(pressed_color, 31)};}}"
        )

    def apply_theme_assets(self, service: object) -> None:
        """Apply the same title-bar artwork and semantic styling as Nexus."""
        self._theme_service = service
        tokens = getattr(service, "tokens", None)
        self._apply_style(
            title_color=str(getattr(tokens, "accent", "#7f9099")),
            muted_color=str(getattr(tokens, "muted_text", "#68747a")),
            highlight_color=str(getattr(tokens, "highlight", "#f1f3f4")),
            accent_color=str(getattr(tokens, "accent", "#7f9099")),
            error_color=str(getattr(tokens, "error", "#d85c6a")),
            pressed_color=str(getattr(tokens, "text", "#f2fbff")),
            font_family=_service_font_family(service),
        )
        for button, logical_name in (
            (self.btn_minimize, "titlebar/minimize.png"),
            (self.btn_maximize, "titlebar/maximize.png"),
            (self.btn_close, self._close_icon_asset),
        ):
            self._apply_button_icon(button, logical_name)
        self.sync_window_state()

    def _apply_button_icon(
        self,
        button: QtWidgets.QPushButton,
        logical_name: str,
    ) -> None:
        resolver = getattr(self._theme_service, "resolve_asset", None)
        icon = QtGui.QIcon()
        if callable(resolver):
            try:
                icon = QtGui.QIcon(str(resolver(logical_name)))
            except (OSError, RuntimeError, TypeError, ValueError):
                icon = QtGui.QIcon()
        button.setIcon(QtGui.QIcon())
        button.setText(str(button.property("fallbackSymbol") or ""))
        if icon.isNull():
            return
        button.setText("")
        button.setIcon(icon)
        button.setIconSize(QtCore.QSize(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX))

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

    def add_content_widget(
        self,
        widget: QtWidgets.QWidget,
        *,
        stretch: int = 0,
        alignment: Qt.AlignmentFlag = Qt.AlignmentFlag(0),
    ) -> QtWidgets.QWidget:
        self._content_layout.addWidget(widget, stretch, alignment)
        return widget

    def add_content_spacing(self, spacing: int) -> None:
        self._content_layout.addSpacing(max(0, int(spacing)))

    def sync_window_state(self) -> None:
        if not self.btn_maximize.isVisible():
            return
        maximized = bool(self._is_maximized())
        if self._theme_service is not None and not self.btn_maximize.icon().isNull():
            resolver = getattr(self._theme_service, "resolve_first_asset", None)
            if callable(resolver):
                candidates = (
                    RESTORE_THEME_ASSET_CANDIDATES
                    if maximized
                    else (MAXIMIZE_THEME_ASSET,)
                )
                icon = QtGui.QIcon(str(resolver(candidates)))
                if not icon.isNull():
                    self.btn_maximize.setIcon(icon)
                    self.btn_maximize.setText("")
        if self.btn_maximize.icon().isNull():
            self.btn_maximize.setText(RESTORE_SYMBOL if maximized else MAXIMIZE_SYMBOL)
        self.btn_maximize.setToolTip("Restore" if maximized else "Maximize")
        self.btn_maximize.setAccessibleName(
            "Restore window" if maximized else "Maximize window"
        )

    def _handle_close(self) -> None:
        self._on_close()

    def _handle_minimize(self) -> None:
        self._on_minimize()

    def _handle_toggle_maximize(self) -> None:
        if self._on_toggle_maximize is None:
            return
        self._on_toggle_maximize()
        self.sync_window_state()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._window and event.type() == QtCore.QEvent.WindowStateChange:
            QtCore.QTimer.singleShot(0, self.sync_window_state)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if self.window_controller.start_system_move():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._on_toggle_maximize is not None:
            self._handle_toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
