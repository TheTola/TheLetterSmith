"""Persistent, user-operated ChatGPT and Gemini browser for Prompt Writer."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import (
    QWebEngineContextMenuRequest,
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from project_paths import application_paths, runtime_application_paths


PROVIDER_URLS = {
    "ChatGPT": "https://chatgpt.com/",
    "Gemini": "https://gemini.google.com/app",
}
_ICONS = application_paths().app_resource_path("icons")


class _ProviderView(QWebEngineView):
    imageApprovalRequested = QtCore.Signal(str, object)

    def __init__(self, profile: QWebEngineProfile, popups: list[QtWidgets.QDialog]):
        super().__init__()
        self._profile = profile
        self._popups = popups
        self.approval_label = ""
        self.setPage(QWebEnginePage(profile, self))

    def createWindow(self, _kind: QWebEnginePage.WebWindowType) -> QWebEngineView:
        popup = QtWidgets.QDialog(self.window())
        popup.setWindowTitle("Prompt Writer sign-in")
        popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.resize(920, 700)
        layout = QtWidgets.QVBoxLayout(popup)
        view = _ProviderView(self._profile, self._popups)
        view.approval_label = self.approval_label
        view.imageApprovalRequested.connect(self.imageApprovalRequested)
        layout.addWidget(view)
        view.page().windowCloseRequested.connect(popup.close)
        popup.destroyed.connect(lambda: self._popups.remove(popup) if popup in self._popups else None)
        self._popups.append(popup)
        popup.show()
        return view

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        request = self.lastContextMenuRequest()
        menu = self.createStandardContextMenu()
        if (
            menu is not None
            and self.approval_label
            and request is not None
            and request.mediaType() == QWebEngineContextMenuRequest.MediaType.MediaTypeImage
            and request.mediaUrl().isValid()
        ):
            image_url = request.mediaUrl().toString()
            menu.addSeparator()
            menu.addAction(
                f"Use image for {self.approval_label}",
                lambda: self.imageApprovalRequested.emit(image_url, self.page()),
            )
        if menu is None:
            super().contextMenuEvent(event)
            return
        menu.exec(event.globalPos())
        menu.deleteLater()
        event.accept()


class PromptWriterBrowser(QtCore.QObject):
    """One live provider page, shared by sign-in and the Prompt Writer panel."""

    imageReady = QtCore.Signal(str, str)
    imageFailed = QtCore.Signal(str)
    imageApprovalStarted = QtCore.Signal(str)

    def __init__(self, parent: QtCore.QObject):
        super().__init__(parent)
        root = runtime_application_paths().app_data_root / "Prompt Writer" / "Browser"
        root.mkdir(parents=True, exist_ok=True)
        self.profile = QWebEngineProfile("lettersmith-prompt-writer", self)
        self.profile.setPersistentStoragePath(str(root / "Storage"))
        self.profile.setCachePath(str(root / "Cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self._popups: list[QtWidgets.QDialog] = []
        self.view = _ProviderView(self.profile, self._popups)
        self.view.imageApprovalRequested.connect(self._approve_image_url)
        self.profile.downloadRequested.connect(self._on_download_requested)
        self._image_downloads = tempfile.TemporaryDirectory(prefix="lettersmith-prompt-images-")
        self._approval_target = ""
        self._pending_image: tuple[str, str, QWebEnginePage] | None = None
        self._active_image_download: QWebEngineDownloadRequest | None = None
        self.provider = ""
        self._pending_prompt = ""
        self._pending_callback: Callable[[bool, str], None] | None = None
        self._submission_checks = 0

    def select(self, provider: str) -> None:
        if provider not in PROVIDER_URLS:
            raise ValueError(f"Unknown image provider: {provider}")
        if provider == self.provider:
            return
        self.provider = provider
        self.view.setUrl(QUrl(PROVIDER_URLS[provider]))

    def park_view(self) -> None:
        owner = self.parent()
        self.view.setParent(owner if isinstance(owner, QtWidgets.QWidget) else None)
        self.view.hide()

    def set_approval_target(self, page_key: str, label: str) -> None:
        self._approval_target = page_key
        self.view.approval_label = label
        for popup in self._popups:
            for view in popup.findChildren(_ProviderView):
                view.approval_label = label

    def _approve_image_url(self, url: str, page: QWebEnginePage) -> None:
        image_url = QUrl(url)
        if not self._approval_target or image_url.scheme().lower() not in {"blob", "http", "https"}:
            self.imageFailed.emit("Choose a generated image in the conversation first.")
            return
        if self._pending_image is not None or self._active_image_download is not None:
            self.imageFailed.emit("Wait for the current image to finish saving.")
            return
        pending = (self._approval_target, image_url.toString(), page)
        self._pending_image = pending
        self.imageApprovalStarted.emit(self._approval_target)
        try:
            page.download(image_url)
        except Exception as error:
            self._pending_image = None
            self.imageFailed.emit(f"Could not download the selected image: {error}")
            return
        QtCore.QTimer.singleShot(30000, lambda: self._expire_image_request(pending))

    def _expire_image_request(self, pending: tuple[str, str, QWebEnginePage]) -> None:
        if self._pending_image == pending:
            self._pending_image = None
            self.imageFailed.emit("The selected image download did not start.")

    def _on_download_requested(self, item: QWebEngineDownloadRequest) -> None:
        pending = self._pending_image
        if pending is None or item.url().toString() != pending[1]:
            return
        if item.page() is not None and item.page() is not pending[2]:
            return
        self._pending_image = None
        if item.isSavePageDownload():
            item.cancel()
            self.imageFailed.emit("The selected item is a page, not an image.")
            return
        target = Path(self._image_downloads.name) / f"{uuid.uuid4().hex}.download"
        item.setDownloadDirectory(str(target.parent))
        item.setDownloadFileName(target.name)
        self._active_image_download = item
        item.stateChanged.connect(
            lambda state, request=item, page_key=pending[0], path=target:
            self._on_image_download_state(request, page_key, path, state)
        )
        item.accept()

    def _on_image_download_state(
        self,
        item: QWebEngineDownloadRequest,
        page_key: str,
        path: Path,
        state: QWebEngineDownloadRequest.DownloadState,
    ) -> None:
        if item is not self._active_image_download:
            return
        if state not in {
            QWebEngineDownloadRequest.DownloadState.DownloadCompleted,
            QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
        }:
            return
        self._active_image_download = None
        if state != QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.imageFailed.emit("The selected image download did not finish.")
            return
        reader = QtGui.QImageReader(str(path))
        dimensions = reader.size()
        if not dimensions.isValid() or min(dimensions.width(), dimensions.height()) < 256:
            self.imageFailed.emit("Choose a full-sized generated image.")
            return
        image = reader.read()
        output = path.with_suffix(".png")
        if image.isNull() or not image.save(str(output), "PNG"):
            self.imageFailed.emit("The selected image could not be decoded.")
            return
        self.imageReady.emit(page_key, str(output))

    def check_signed_in(self, callback: Callable[[str], None]) -> None:
        script = """(() => {
            const provider = %s;
            const host = provider === 'ChatGPT' ? 'chatgpt.com' : 'gemini.google.com';
            if (location.hostname !== host || document.readyState === 'loading') return 'loading';
            const visible = element => element && element.getClientRects().length &&
                getComputedStyle(element).visibility !== 'hidden';
            const login = Array.from(document.querySelectorAll('a, button')).find(element =>
                visible(element) && (/^(log in|sign in)$/i.test((element.textContent || '').trim()) ||
                /login|signin/i.test(element.getAttribute('href') || '')));
            const selector = provider === 'ChatGPT'
                ? '#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]'
                : '[contenteditable="true"][role="textbox"][aria-label*="prompt" i], [contenteditable="true"][aria-label*="prompt" i], textarea[aria-label*="prompt" i]';
            const composer = Array.from(document.querySelectorAll(selector)).find(visible);
            return composer && !login ? 'ready' : login ? 'sign_in' : 'loading';
        })()""" % json.dumps(self.provider)
        self.view.page().runJavaScript(script, callback)

    def submit_prompt(self, prompt: str, callback: Callable[[bool, str], None]) -> None:
        if self._pending_callback is not None:
            callback(False, "Wait for the current prompt to be sent.")
            return
        if not self.provider or not prompt.strip():
            callback(False, "Create a prompt and choose ChatGPT or Gemini first.")
            return
        self._pending_prompt = prompt.strip()
        self._pending_callback = callback
        self.check_signed_in(self._on_submit_sign_in_checked)

    def _on_submit_sign_in_checked(self, state: str) -> None:
        if self._pending_callback is None:
            return
        if state != "ready":
            self._finish(False, f"Sign in to {self.provider} in the conversation first.")
            return
        self._type_pending_prompt()

    def _type_pending_prompt(self) -> None:
        script = """(() => {
            const provider = %s, prompt = %s;
            const host = provider === 'ChatGPT' ? 'chatgpt.com' : 'gemini.google.com';
            if (location.hostname !== host) return 'sign_in';
            const selector = provider === 'ChatGPT'
                ? '#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]'
                : '[contenteditable="true"][role="textbox"][aria-label*="prompt" i], [contenteditable="true"][aria-label*="prompt" i], textarea[aria-label*="prompt" i]';
            const editor = Array.from(document.querySelectorAll(selector)).find(element => element.getClientRects().length);
            if (!editor) return 'no_composer';
            const busy = Array.from(document.querySelectorAll('button')).some(button =>
                button.getClientRects().length && /stop (generating|response)|cancel response/i.test(
                    button.getAttribute('aria-label') || ''));
            if (busy) return 'busy';
            if ((editor.innerText || editor.value || '').trim()) return 'occupied';
            editor.focus();
            if (editor.isContentEditable) {
                if (!document.execCommand('insertText', false, prompt)) return 'failed';
            } else {
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
                setter.call(editor, prompt);
                editor.dispatchEvent(new Event('input', {bubbles: true}));
            }
            return (editor.innerText || editor.value || '').trim() === prompt ? 'typed' : 'failed';
        })()""" % (json.dumps(self.provider), json.dumps(self._pending_prompt, ensure_ascii=False))
        self.view.page().runJavaScript(script, self._on_prompt_typed)

    def _on_prompt_typed(self, result: object) -> None:
        if result != "typed":
            messages = {
                "occupied": "The conversation has an unsent draft. Send or clear it first.",
                "no_composer": "The provider's prompt box is unavailable. Sign in or reload the page.",
                "sign_in": "Sign in to the selected provider first.",
                "busy": "Wait for the current image to finish before sending another prompt.",
            }
            self._finish(False, messages.get(str(result), "Could not place the prompt in the provider page."))
            return
        QtCore.QTimer.singleShot(300, self._click_send)

    def _click_send(self) -> None:
        if self._pending_callback is None:
            return
        script = """(() => {
            const prompt = %s;
            const editor = document.querySelector(
                '#prompt-textarea, [data-testid="prompt-textarea"], ' +
                '[contenteditable="true"][role="textbox"][aria-label*="prompt" i], ' +
                '[contenteditable="true"][role="textbox"], textarea[aria-label*="prompt" i]');
            if (!editor || (editor.innerText || editor.value || '').trim() !== prompt) return 'changed';
            const buttons = Array.from(document.querySelectorAll('button')).filter(button =>
                button.getClientRects().length && !button.disabled &&
                button.getAttribute('aria-disabled') !== 'true');
            const send = buttons.find(button => /send-button/i.test(button.getAttribute('data-testid') || '')) ||
                buttons.find(button => /^(send|send message|send prompt|submit)$/i.test(
                    (button.getAttribute('aria-label') || button.title || '').trim()));
            if (!send) return 'no_send';
            send.click();
            return 'clicked';
        })()""" % json.dumps(self._pending_prompt, ensure_ascii=False)
        self.view.page().runJavaScript(script, self._on_send_clicked)

    def _on_send_clicked(self, result: object) -> None:
        if result != "clicked":
            self._finish(False, "The prompt is in the provider's input. Use its Send button to continue.")
            return
        self._submission_checks = 0
        QtCore.QTimer.singleShot(500, self._verify_submission)

    def _verify_submission(self) -> None:
        if self._pending_callback is None:
            return
        script = """(() => {
            const provider = %s, lead = %s;
            const selector = provider === 'ChatGPT'
                ? '[data-message-author-role="user"], [data-turnrole="user"]'
                : 'user-query, .user-query, [data-message-author-role="user"]';
            return Array.from(document.querySelectorAll(selector)).some(message =>
                (message.innerText || '').includes(lead));
        })()""" % (json.dumps(self.provider), json.dumps(self._pending_prompt[:100], ensure_ascii=False))
        self.view.page().runJavaScript(script, self._on_submission_checked)

    def _on_submission_checked(self, confirmed: object) -> None:
        if self._pending_callback is None:
            return
        if confirmed:
            self._finish(True, f"Prompt sent to {self.provider}.")
        elif self._submission_checks < 20:
            self._submission_checks += 1
            QtCore.QTimer.singleShot(500, self._verify_submission)
        else:
            self._finish(False, "Check the conversation; prompt submission could not be confirmed.")

    def _finish(self, ok: bool, message: str) -> None:
        callback = self._pending_callback
        self._pending_callback = None
        self._pending_prompt = ""
        if callback is not None:
            callback(ok, message)


class ProviderSignInDialog(QtWidgets.QDialog):
    """Choose a provider, then wait for its own signed-in composer."""

    def __init__(
        self, browser: PromptWriterBrowser, parent: QtWidgets.QWidget, initial_provider: str = ""
    ):
        super().__init__(parent)
        self.browser = browser
        self.setWindowTitle("Prompt Writer · Choose a service")
        self.resize(520, 205)
        self._stack = QtWidgets.QStackedWidget(self)
        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(self._stack)

        chooser = QtWidgets.QWidget()
        choices = QtWidgets.QVBoxLayout(chooser)
        title = QtWidgets.QLabel("Choose where to create your images")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        choices.addWidget(title)
        row = QtWidgets.QHBoxLayout()
        for provider, filename in (("ChatGPT", "provider-chatgpt.png"), ("Gemini", "provider-gemini.png")):
            button = QtWidgets.QToolButton()
            button.setText(provider)
            icon = QtGui.QPixmap(str(_ICONS / filename))
            if provider == "ChatGPT" and not icon.isNull():
                tinted = QtGui.QPixmap(icon.size())
                tinted.fill(QtCore.Qt.GlobalColor.transparent)
                painter = QtGui.QPainter(tinted)
                painter.drawPixmap(0, 0, icon)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
                surface = self.palette().color(QtGui.QPalette.ColorRole.Window)
                painter.fillRect(tinted.rect(), QtGui.QColor(
                    "#f1f5f7" if surface.lightness() < 128 else "#20242a"
                ))
                painter.end()
                icon = tinted
            button.setIcon(QtGui.QIcon(icon))
            button.setIconSize(QtCore.QSize(56, 56))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setMinimumSize(180, 110)
            button.clicked.connect(lambda _checked=False, name=provider: self._choose(name))
            row.addWidget(button)
        choices.addLayout(row)
        self._stack.addWidget(chooser)

        login = QtWidgets.QWidget()
        login_layout = QtWidgets.QVBoxLayout(login)
        self._status = QtWidgets.QLabel("Sign in on the provider page.")
        login_layout.addWidget(self._status)
        login_layout.addWidget(browser.view, 1)
        self._stack.addWidget(login)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check_login)
        self.finished.connect(self._detach_view)
        if initial_provider in PROVIDER_URLS:
            QtCore.QTimer.singleShot(0, lambda: self._choose(initial_provider))

    def _choose(self, provider: str) -> None:
        self.browser.select(provider)
        self._status.setText(f"Sign in to {provider} on this page.")
        self._stack.setCurrentIndex(1)
        self.resize(1040, 760)
        self._timer.start()
        self._check_login()

    def _check_login(self) -> None:
        self.browser.check_signed_in(self._on_login_checked)

    def _on_login_checked(self, state: str) -> None:
        if state == "ready":
            self.accept()
        elif state == "sign_in":
            self._status.setText(f"Sign in to {self.browser.provider} on this page.")

    def _detach_view(self, _result: int) -> None:
        self._timer.stop()
        self.browser.park_view()
