from __future__ import annotations

import logging
import threading

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

from external_urls import open_external_url
from project_paths import application_paths
from publishing.github_auth import (
    GitHubAccount,
    GitHubAuthenticator,
    GitHubConnectionService,
    GitHubConnectionSnapshot,
    GitHubConnectionState,
    GitHubCredentialStore,
    GitHubDeviceAuthorization,
    GitHubOperationError,
    GitHubPublishingAccess,
    GitHubSession,
    GitHubToken,
    github_connection_service,
)
from ui_help import set_control_help
from settings_store import SettingsStore
from ui_theme import (
    BASIC_LIGHT_THEME,
    CYBER_FORGE_THEME,
    DEFAULT_THEME_ID,
    THEMES,
    THEME_SETTINGS_KEY,
    ThemeService,
    ThemeTokens,
    normalize_theme_id,
)


_LOGGER = logging.getLogger(__name__)


def _copy_to_clipboard(text: str) -> None:
    value = str(text).strip()
    if not value:
        return
    clipboard = QtWidgets.QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(value)


class _ClickableCodeLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        clicked = (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        )
        if clicked:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _github_logo_pixmap(*, inverted: bool = False) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(
        str(
            application_paths().app_resource_path(
                "icons/GitHub-logo.png"
            )
        )
    )
    if inverted and not pixmap.isNull():
        image = pixmap.toImage()
        image.invertPixels(QtGui.QImage.InvertRgb)
        return QtGui.QPixmap.fromImage(image)
    return pixmap


def _github_logo_is_inverted(colors: ThemeTokens) -> bool:
    return colors == BASIC_LIGHT_THEME.tokens


def _active_theme_tokens(parent: QtWidgets.QWidget | None) -> ThemeTokens:
    service = getattr(parent, "theme_service", None)
    tokens = getattr(service, "tokens", None)
    if isinstance(tokens, ThemeTokens):
        return tokens
    paths = application_paths()
    try:
        stored = SettingsStore(paths.workspace_root).get(
            THEME_SETTINGS_KEY,
            DEFAULT_THEME_ID,
        )
        return THEMES[normalize_theme_id(stored, THEMES)].tokens
    except Exception:
        _LOGGER.exception("The stored theme could not be loaded for GitHub sign-in.")
        return CYBER_FORGE_THEME.tokens


def _paint_relative_watermark(
    widget: QtWidgets.QWidget,
    pixmap: QtGui.QPixmap,
    *,
    opacity: float,
) -> None:
    if pixmap.isNull() or widget.rect().isEmpty():
        return
    horizontal_margin = round(widget.width() * 0.08)
    vertical_margin = round(widget.height() * 0.08)
    available = widget.rect().adjusted(
        horizontal_margin,
        vertical_margin,
        -horizontal_margin,
        -vertical_margin,
    )
    if available.isEmpty():
        return
    scaled = pixmap.scaled(
        available.size(),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    target = QtCore.QRect(QtCore.QPoint(), scaled.size())
    target.moveCenter(widget.rect().center())
    painter = QtGui.QPainter(widget)
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    painter.drawPixmap(target, scaled)


class _GitHubStartupSignals(QtCore.QObject):
    authorization_ready = QtCore.Signal(object)
    installation_required = QtCore.Signal(str, str)
    connected = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()


class GitHubAuthenticationDialog(QtWidgets.QDialog):
    connected = QtCore.Signal(object)
    authentication_failed = QtCore.Signal(str)
    cancel_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.Dialog | Qt.FramelessWindowHint,
        )
        self.setObjectName("GitHubAuthenticationDialog")
        self.setAccessibleName("Connect to GitHub")
        self.setWindowModality(Qt.ApplicationModal)
        self.setModal(True)
        self.selected_action = "skip"
        self._theme_tokens = _active_theme_tokens(parent)
        self._watermark_inverted = _github_logo_is_inverted(self._theme_tokens)
        self._watermark = _github_logo_pixmap(inverted=self._watermark_inverted)
        self._browser_url = ""
        self._authorization_code = ""
        self._connection_service: GitHubConnectionService | None = None
        self._cancel_authentication = threading.Event()
        self._authentication_thread: threading.Thread | None = None
        self._authentication_signals: _GitHubStartupSignals | None = None
        self._completed = False
        self._restart_access_on_open = False
        self._start_auth_on_open = False
        self._dimmer: QtWidgets.QWidget | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 26)
        layout.setSpacing(16)
        layout.addStretch(1)

        self.heading = QtWidgets.QLabel(
            "Connect Letter Smith to GitHub",
            self,
        )
        self.heading.setAlignment(Qt.AlignCenter)
        self.heading.setStyleSheet("font:700 19pt 'Segoe UI';")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.message = QtWidgets.QLabel("Preparing secure GitHub sign-in…", self)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("font:10.5pt 'Segoe UI';")
        layout.addWidget(self.message)

        self.connection_status = QtWidgets.QLabel(
            "Waiting for GitHub…",
            self,
        )
        self.connection_status.setAlignment(Qt.AlignCenter)
        self.connection_status.setObjectName("GitHubConnectionStatus")
        self.connection_status.setWordWrap(True)
        self.connection_status.setStyleSheet("font:10pt 'Segoe UI';")
        layout.addWidget(self.connection_status)

        self.authorization_code = _ClickableCodeLabel("", self)
        self.authorization_code.setAlignment(Qt.AlignCenter)
        self.authorization_code.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        self.authorization_code.setCursor(Qt.PointingHandCursor)
        self.authorization_code.setStyleSheet(
            "font:700 18pt 'Consolas';color:#58a6ff;"
        )
        self.authorization_code.hide()
        self.authorization_code.clicked.connect(self._copy_authorization_code)
        layout.addWidget(self.authorization_code)
        self.instruction = self.message
        self.status_label = self.connection_status
        self.code_label = self.authorization_code
        layout.addStretch(1)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.open_button = QtWidgets.QPushButton("Open GitHub", self)
        self.open_button.setObjectName("GitHubAuthOpen")
        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        self.cancel_button.setObjectName("GitHubAuthCancel")
        for button in (
            self.open_button,
            self.cancel_button,
        ):
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Preferred,
                QtWidgets.QSizePolicy.Fixed,
            )
            button.setMinimumWidth(112)
            button.setMaximumWidth(180)
            button.setMinimumHeight(38)
            button.setMaximumHeight(46)
        set_control_help(
            self.open_button,
            "Open GitHub's official device authorization page.",
        )
        set_control_help(
            self.cancel_button,
            "Cancel this GitHub authentication attempt.",
        )
        actions.addStretch(1)
        actions.addWidget(self.open_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.setMinimumSize(520, 300)
        natural_size = self.sizeHint().expandedTo(QtCore.QSize(560, 320))
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        target_size = natural_size
        if screen is not None:
            available = screen.availableGeometry().size()
            target_size = target_size.boundedTo(
                QtCore.QSize(
                    round(available.width() * 0.86),
                    round(available.height() * 0.82),
                )
            )
        self.resize(target_size)

        self.open_button.clicked.connect(self.open_github)
        self.cancel_button.clicked.connect(self.reject)
        self.open_button.setEnabled(False)
        self.apply_theme_tokens(self._theme_tokens)

    def prepare(self, status: str = "") -> None:
        self._completed = False
        self._restart_access_on_open = False
        self._start_auth_on_open = False
        self.heading.setText("Connecting Letter Smith to GitHub")
        self.message.setText("Secure device authorization")
        self.connection_status.setText(
            status or "Preparing secure GitHub sign-in…"
        )
        self.authorization_code.hide()
        self.open_button.setText("Open GitHub")
        self.open_button.setEnabled(False)

    def prepare_startup(self, service: GitHubConnectionService) -> None:
        self._connection_service = service
        self._completed = False
        self._restart_access_on_open = False
        self._start_auth_on_open = True
        self.heading.setText("Connect Letter Smith to GitHub")
        self.message.setText("Sign in to enable automatic publishing.")
        self.connection_status.setText(
            "Continue with GitHub, or cancel to open Letter Smith without connecting."
        )
        self.authorization_code.hide()
        self.open_button.setText("Sign in with GitHub")
        self.open_button.setEnabled(True)

    def begin_authentication(self, service: GitHubConnectionService) -> None:
        thread = self._authentication_thread
        if thread is not None and thread.is_alive():
            return
        self._connection_service = service
        self._start_auth_on_open = False
        self._cancel_authentication = threading.Event()
        self.prepare()
        self._browser_url = ""
        self._authorization_code = ""
        self.show()
        self.raise_()
        self.activateWindow()
        _LOGGER.info("GitHub device authentication started.")

        signals = _GitHubStartupSignals()
        signals.authorization_ready.connect(self._authorization_ready)
        signals.installation_required.connect(self._installation_required)
        signals.connected.connect(self._authentication_completed)
        signals.failed.connect(self._authentication_failed)
        signals.finished.connect(self._authentication_finished)
        self._authentication_signals = signals
        self._authentication_thread = threading.Thread(
            target=self._run_authentication,
            args=(service, signals, self._cancel_authentication),
            name="LetterSmithGitHubStartup",
            daemon=True,
        )
        self._authentication_thread.start()

    def begin_publishing_access(
        self,
        service: GitHubConnectionService,
        snapshot: GitHubConnectionSnapshot,
    ) -> None:
        thread = self._authentication_thread
        if thread is not None and thread.is_alive():
            return
        access = snapshot.access
        if access is None:
            raise ValueError("GitHub publishing access details are required.")
        self._connection_service = service
        self._cancel_authentication = threading.Event()
        self.prepare(access.message)
        self.message.setText("Approve Letter Smith publishing access")
        self.show()
        self.raise_()
        self.activateWindow()
        _LOGGER.info(
            "GitHub publishing access update started: reason=%s",
            access.code,
        )
        signals = _GitHubStartupSignals()
        signals.installation_required.connect(self._installation_required)
        signals.connected.connect(self._authentication_completed)
        signals.failed.connect(self._authentication_failed)
        signals.finished.connect(self._authentication_finished)
        self._authentication_signals = signals
        self._authentication_thread = threading.Thread(
            target=self._run_publishing_access,
            args=(
                service,
                snapshot,
                signals,
                self._cancel_authentication,
            ),
            name="LetterSmithGitHubAccess",
            daemon=True,
        )
        self._authentication_thread.start()

    @staticmethod
    def _run_authentication(
        service: GitHubConnectionService,
        signals: _GitHubStartupSignals,
        cancelled: threading.Event,
    ) -> None:
        try:
            snapshot = service.authorize(
                authorization_ready=signals.authorization_ready.emit,
                publishing_access_required=lambda access: (
                    signals.installation_required.emit(
                        access.setup_url,
                        access.message,
                    )
                ),
                cancelled=cancelled.is_set,
            )
            if (
                not cancelled.is_set()
                and snapshot.state == GitHubConnectionState.CONNECTED
                and snapshot.session is not None
            ):
                signals.connected.emit(snapshot.session)
        except GitHubOperationError as error:
            if not cancelled.is_set():
                _LOGGER.warning(
                    "GitHub authentication transport failed: code=%s "
                    "status=%s details=%s",
                    error.code,
                    error.status,
                    error.technical_details or "unavailable",
                )
                signals.failed.emit(error.user_message)
        except Exception:
            if not cancelled.is_set():
                signals.failed.emit(
                    "GitHub sign-in could not be completed. Please try again."
                )
        finally:
            signals.finished.emit()

    @staticmethod
    def _run_publishing_access(
        service: GitHubConnectionService,
        _snapshot: GitHubConnectionSnapshot,
        signals: _GitHubStartupSignals,
        cancelled: threading.Event,
    ) -> None:
        try:
            snapshot = service.complete_publishing_access(
                publishing_access_required=lambda access: (
                    signals.installation_required.emit(
                        access.setup_url,
                        access.message,
                    )
                ),
                cancelled=cancelled.is_set,
            )
            if (
                not cancelled.is_set()
                and snapshot.state == GitHubConnectionState.CONNECTED
                and snapshot.session is not None
            ):
                signals.connected.emit(snapshot.session)
        except GitHubOperationError as error:
            if not cancelled.is_set():
                signals.failed.emit(error.user_message)
        except Exception:
            if not cancelled.is_set():
                signals.failed.emit(
                    "GitHub publishing access could not be completed. "
                    "Please try again."
                )
        finally:
            signals.finished.emit()

    @QtCore.Slot(object)
    def _authorization_ready(
        self,
        authorization: GitHubDeviceAuthorization,
    ) -> None:
        self._browser_url = authorization.verification_uri
        self._authorization_code = authorization.user_code
        self.authorization_code.setText(
            f"GitHub code: {authorization.user_code}"
        )
        self._copy_authorization_code()
        self.authorization_code.show()
        self.connection_status.setText(
            "The code was copied. Continue in the browser, paste it, and "
            "authorize Letter Smith. This page will wait for confirmation."
        )
        self.open_button.setEnabled(True)
        _LOGGER.info(
            "GitHub device code created: expires_in=%s interval=%s",
            authorization.expires_in,
            authorization.interval,
        )
        self.open_github()

    @QtCore.Slot(str)
    def _installation_required(self, url: str, message: str = "") -> None:
        self._browser_url = url
        self._authorization_code = ""
        self.authorization_code.hide()
        self.connection_status.setText(
            message
            or "GitHub sign-in succeeded. Continue in the browser and approve "
            "Letter Smith for your account."
        )
        self.open_button.setEnabled(True)

    @QtCore.Slot(object)
    def _authentication_completed(self, session: GitHubSession) -> None:
        self._completed = True
        self.selected_action = "connected"
        self.connection_status.setText(
            f"Connected to GitHub as {session.account.login}."
        )
        _LOGGER.info(
            "GitHub authentication completed: account=%s account_id=%s",
            session.account.login,
            session.account.account_id,
        )
        self.connected.emit(session)
        self.accept()

    @QtCore.Slot(str)
    def _authentication_failed(self, message: str) -> None:
        self._authorization_code = ""
        self.authorization_code.hide()
        self.connection_status.setText(message)
        self._restart_access_on_open = bool(self._browser_url)
        self._start_auth_on_open = bool(
            not self._browser_url and self._connection_service is not None
        )
        self.open_button.setText(
            "Open GitHub / Retry"
            if self._restart_access_on_open
            else "Retry GitHub Sign-In"
        )
        self.open_button.setEnabled(
            bool(self._browser_url) or self._start_auth_on_open
        )
        self.authentication_failed.emit(message)
        _LOGGER.warning("GitHub authentication failed: %s", message)

    @QtCore.Slot()
    def _authentication_finished(self) -> None:
        self._authentication_thread = None

    @QtCore.Slot()
    def open_github(self) -> None:
        if self._start_auth_on_open and self._connection_service is not None:
            service = self._connection_service
            self._start_auth_on_open = False
            self.begin_authentication(service)
            return
        if not self._browser_url:
            return
        if self._authorization_code:
            self._copy_authorization_code()
        opened = open_external_url(QUrl(self._browser_url))
        _LOGGER.info("GitHub authorization browser launch: opened=%s", opened)
        if not opened:
            self.connection_status.setText(
                "GitHub could not be opened. Select Open GitHub to "
                "try again."
            )
            return
        if self._restart_access_on_open and self._connection_service is not None:
            snapshot = self._connection_service.snapshot
            if snapshot.session is not None and snapshot.access is not None:
                self._restart_access_on_open = False
                self.begin_publishing_access(self._connection_service, snapshot)

    def set_authorization(self, authorization: GitHubDeviceAuthorization) -> None:
        self._authorization_ready(authorization)

    def set_installation_step(self, url: str, message: str = "") -> None:
        self._installation_required(url, message)

    def finish(self) -> None:
        self._completed = True
        self._cancel_authentication.set()
        self.accept()

    @QtCore.Slot()
    def _copy_authorization_code(self) -> None:
        _copy_to_clipboard(self._authorization_code)
        if self._authorization_code:
            self.connection_status.setText(
                "Code copied. Waiting for GitHub authorization…"
            )

    def reject(self) -> None:
        self.selected_action = "skip"
        self._cancel_authentication.set()
        if not self._completed:
            self.cancel_requested.emit()
            _LOGGER.info("GitHub authentication cancelled by the user.")
        super().reject()

    def apply_theme_assets(self, service: ThemeService) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._theme_tokens = colors
        watermark_inverted = _github_logo_is_inverted(colors)
        if watermark_inverted != self._watermark_inverted:
            self._watermark = _github_logo_pixmap(inverted=watermark_inverted)
            self._watermark_inverted = watermark_inverted
        self.update()
        self.setStyleSheet(
            "QDialog#GitHubAuthenticationDialog{"
            f"background:{colors.panel_background};"
            f"border:2px solid {colors.accent};color:{colors.text};}}"
            f"QLabel{{background:transparent;color:{colors.text};}}"
            f"QLabel#GitHubConnectionStatus{{color:{colors.text};}}"
            f"QPushButton{{background:{colors.control_background};"
            f"color:{colors.text};border:1px solid {colors.border};"
            "border-radius:7px;padding:0 12px;font-weight:700;}"
            f"QPushButton:hover{{background:{colors.hover};"
            f"border-color:{colors.accent};}}"
            f"QPushButton:focus{{border:2px solid {colors.accent};}}"
            f"QPushButton:disabled{{background:{colors.card_background};"
            f"color:{colors.disabled_text};border-color:{colors.border};}}"
            f"QPushButton#GitHubAuthOpen{{border-color:{colors.accent};}}"
        )
        self.authorization_code.setStyleSheet(
            "font:700 18pt 'Consolas';padding:10px 16px;"
            f"color:{colors.accent};border:1px solid {colors.accent};"
            "border-radius:7px;"
        )

    def _owner_window(self) -> QtWidgets.QWidget | None:
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    def _show_dimmer(self) -> None:
        owner = self._owner_window()
        if owner is None:
            return
        if self._dimmer is None:
            self._dimmer = QtWidgets.QWidget(owner)
            self._dimmer.setObjectName("GitHubAuthenticationDimmer")
            self._dimmer.setStyleSheet(
                "QWidget#GitHubAuthenticationDimmer{"
                "background:rgba(0,0,0,155);border:none;}"
            )
            self._dimmer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._dimmer.setGeometry(owner.rect())
        self._dimmer.show()
        self._dimmer.raise_()
        owner.installEventFilter(self)

    def _hide_dimmer(self) -> None:
        owner = self._owner_window()
        if owner is not None:
            owner.removeEventFilter(self)
        if self._dimmer is not None:
            self._dimmer.hide()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._owner_window() and event.type() in {
            QtCore.QEvent.Resize,
            QtCore.QEvent.Move,
        }:
            if self._dimmer is not None:
                self._dimmer.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._show_dimmer()
        owner = self._owner_window()
        if owner is not None:
            center = owner.mapToGlobal(owner.rect().center())
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._hide_dimmer()
        super().hideEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        _paint_relative_watermark(self, self._watermark, opacity=0.11)


def prompt_for_github_startup(
    parent: QtWidgets.QWidget | None = None,
    *,
    credential_store: GitHubCredentialStore | None = None,
) -> str:
    store = credential_store or GitHubCredentialStore()
    try:
        stored_credential = store.load()
    except GitHubOperationError:
        stored_credential = None
    if stored_credential is not None:
        return "connected"

    dialog = GitHubAuthenticationDialog(parent)
    service = GitHubConnectionService(
        GitHubAuthenticator(credential_store=store)
    )
    dialog.prepare_startup(service)
    dialog.exec()
    return dialog.selected_action


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
        self._theme_tokens = _active_theme_tokens(parent)
        self._watermark_inverted = _github_logo_is_inverted(self._theme_tokens)
        self._watermark = _github_logo_pixmap(inverted=self._watermark_inverted)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)

        self.heading = QtWidgets.QLabel("GitHub")
        self.heading.setAlignment(Qt.AlignCenter)
        self.heading.setStyleSheet("font:700 20pt 'Segoe UI';")
        layout.addWidget(self.heading)
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
        self.sign_in_button.setMinimumHeight(38)
        self.sign_in_button.setMaximumHeight(46)
        self.sign_in_button.setMaximumWidth(240)
        set_control_help(
            self.sign_in_button,
            "Connect a GitHub account so Letter Smith can publish letters.",
        )
        self._connected = False
        self._connection_state = GitHubConnectionState.DISCONNECTED
        self.sign_in_button.clicked.connect(self._request_account_action)
        actions.addWidget(self.sign_in_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.set_account(None)
        self.apply_theme_tokens(self._theme_tokens)

    def apply_theme_assets(self, service: ThemeService) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._theme_tokens = colors
        watermark_inverted = _github_logo_is_inverted(colors)
        if watermark_inverted != self._watermark_inverted:
            self._watermark = _github_logo_pixmap(inverted=watermark_inverted)
            self._watermark_inverted = watermark_inverted
        self.update()
        self.setStyleSheet(
            "QDialog#GitHubAccountDialog{"
            f"background:{colors.panel_background};"
            f"border:1px solid {colors.accent};color:{colors.text};}}"
            f"QLabel{{background:transparent;color:{colors.text};}}"
            "QLabel#GitHubConnectionStatus[connectionState='connected']{"
            f"color:{colors.success};}}"
            "QLabel#GitHubConnectionStatus[connectionState='disconnected']{"
            f"color:{colors.error};}}"
            "QLabel#GitHubConnectionStatus[connectionState='checking']{"
            f"color:{colors.accent};}}"
            "QLabel#GitHubConnectionStatus[connectionState='insufficient']{"
            f"color:{colors.error};}}"
            "QLabel#GitHubConnectionStatus[connectionState='unavailable']{"
            f"color:{colors.muted_text};}}"
            f"QPushButton{{background:{colors.control_background};"
            f"color:{colors.text};border:1px solid {colors.border};"
            "border-radius:6px;padding:0 14px;font-weight:700;}"
            f"QPushButton:hover{{background:{colors.hover};"
            f"border-color:{colors.accent};}}"
            "QPushButton#GitHubSignInButton[accountAction='disconnect']{"
            f"border-color:{colors.error};color:{colors.error};}}"
        )

    def set_account(
        self,
        account: GitHubAccount | None,
        *,
        checking: bool = False,
        message: str = "",
    ) -> None:
        state = (
            GitHubConnectionState.CONNECTING
            if checking
            else GitHubConnectionState.CONNECTED
            if account is not None
            else GitHubConnectionState.DISCONNECTED
        )
        self.set_connection(
            GitHubConnectionSnapshot(
                state,
                session=(
                    GitHubSession(account, GitHubToken("display-only"))
                    if account is not None
                    else None
                ),
                message=message,
            )
        )

    def set_connection(self, snapshot: GitHubConnectionSnapshot) -> None:
        account = snapshot.account
        state = snapshot.state
        if state in {GitHubConnectionState.CONNECTING, GitHubConnectionState.AUTHORIZING}:
            self.status_label.setText("Checking GitHub connection…")
            connection_state = "checking"
        elif state == GitHubConnectionState.CONNECTED and account is not None:
            self.status_label.setText(
                f"GitHub connected\nSigned in as {account.login}"
            )
            connection_state = "connected"
        elif (
            state in {
                GitHubConnectionState.INSTALL_REQUIRED,
                GitHubConnectionState.ACTION_REQUIRED,
            }
            and account is not None
        ):
            self.status_label.setText(
                f"GitHub — {account.login}\n"
                + (
                    "Install Letter Smith to finish connecting"
                    if state == GitHubConnectionState.INSTALL_REQUIRED
                    else snapshot.message or "GitHub access needs attention"
                )
            )
            connection_state = "insufficient"
        elif state == GitHubConnectionState.RECONNECTING and account is not None:
            self.status_label.setText(
                f"GitHub connected as {account.login}\nReconnecting automatically…"
            )
            connection_state = "unavailable"
        elif state == GitHubConnectionState.GITHUB_UNAVAILABLE:
            self.status_label.setText(
                "GitHub connection unavailable"
                + (f"\n{snapshot.message}" if snapshot.message else "")
            )
            connection_state = "unavailable"
        else:
            self.status_label.setText(
                "GitHub not connected"
                + (f"\n{snapshot.message}" if snapshot.message else "")
            )
            connection_state = "disconnected"
        self._connection_state = state
        self._connected = bool(
            account is not None
            and state in {
                GitHubConnectionState.CONNECTED,
                GitHubConnectionState.RECONNECTING,
            }
        )
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
        self.sign_in_button.setEnabled(
            state not in {
                GitHubConnectionState.CONNECTING,
                GitHubConnectionState.AUTHORIZING,
            }
        )
        self.sign_in_button.setText(
            "Disconnect from GitHub"
            if self._connected
            else "Install Letter Smith"
            if state == GitHubConnectionState.INSTALL_REQUIRED
            else "Update GitHub Access"
            if state == GitHubConnectionState.ACTION_REQUIRED
            else "Sign in with GitHub"
        )
        self.sign_in_button.setProperty(
            "accountAction",
            "disconnect"
            if self._connected
            else "installation"
            if state == GitHubConnectionState.INSTALL_REQUIRED
            else "repair-access"
            if state == GitHubConnectionState.ACTION_REQUIRED
            else "sign-in",
        )
        self.sign_in_button.style().unpolish(self.sign_in_button)
        self.sign_in_button.style().polish(self.sign_in_button)
        set_control_help(
            self.sign_in_button,
            (
                "Disconnect this GitHub account from Letter Smith."
                if self._connected
                else "Install Letter Smith's GitHub App for this account."
                if state == GitHubConnectionState.INSTALL_REQUIRED
                else "Restore Letter Smith's GitHub access."
                if state == GitHubConnectionState.ACTION_REQUIRED
                else (
                    "Connect a GitHub account so Letter Smith can publish "
                    "letters."
                )
            ),
        )

    @QtCore.Slot()
    def _request_account_action(self) -> None:
        if self._connected:
            self.sign_out_requested.emit()
        else:
            self.sign_in_requested.emit()

    def paintEvent(
        self,
        event: QtGui.QPaintEvent,
    ) -> None:
        super().paintEvent(event)
        _paint_relative_watermark(self, self._watermark, opacity=0.14)


GitHubStartupDialog = GitHubAuthenticationDialog
GitHubDeviceFlowDialog = GitHubAuthenticationDialog


__all__ = [
    "GitHubAccountDialog",
    "GitHubAuthenticationDialog",
    "GitHubDeviceFlowDialog",
    "GitHubStartupDialog",
    "prompt_for_github_startup",
]
