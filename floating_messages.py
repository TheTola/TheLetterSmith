"""A quiet preview tab with an explicitly opened, draft-preserving editor."""
import json
from PySide6 import QtCore, QtGui, QtWidgets

from messages_workspace import MessagesWorkspace as _MessagesWorkspace, ReadabilityControls
from prompt_writer_project import page_label
from window_chrome import place_window_on_launcher


class _EditorWindow(QtWidgets.QDialog):
    def __init__(self, workspace):
        super().__init__(workspace, QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowCloseButtonHint)
        self.workspace = workspace
        self.setModal(False)
        self.resize(1080, 720)
        self.setMinimumSize(720, 480)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.content = QtWidgets.QVBoxLayout(self)
        self.content.setContentsMargins(8, 8, 8, 8)
        self.content.setSpacing(4)

    def closeEvent(self, event):
        if not self.workspace.save():
            event.ignore()
            return
        self.workspace._return_preview()
        event.accept()

    def reject(self):
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        QtWidgets.QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        QtWidgets.QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if not self.isVisible() or self.workspace.expanded_preview is not None:
            return False
        app = QtWidgets.QApplication.instance()
        if app.activePopupWidget() is not None or (app.activeModalWidget() is not None and app.activeModalWidget() is not self):
            return False
        if event.type() == QtCore.QEvent.MouseButtonPress:
            ancestor = watched if isinstance(watched, QtWidgets.QWidget) else None
            while ancestor is not None:
                if ancestor is self:
                    return False
                ancestor = ancestor.parentWidget()
            if isinstance(event, QtGui.QMouseEvent) and not self.frameGeometry().contains(event.globalPosition().toPoint()):
                self.close()
        return False


class _ExpandedPreview(QtWidgets.QDialog):
    """Temporarily hosts the same browser, preserving its page and scroll state."""

    def __init__(self, workspace):
        super().__init__(workspace, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.workspace = workspace
        self.preview = workspace.shared_preview
        self.setWindowTitle("LetterSmith Preview")
        self.setStyleSheet("QDialog{background:#101014;}")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self.preview)
        self.preview.show()
        self.setGeometry(workspace.editor_window.screen().availableGeometry())
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        self.shortcut.activated.connect(self.close)

    def showEvent(self, event):
        super().showEvent(event)
        QtWidgets.QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if self.isVisible() and event.type() == QtCore.QEvent.MouseButtonPress:
            view = self.preview.view
            bounds = QtCore.QRect(view.mapToGlobal(QtCore.QPoint()), view.size())
            if not bounds.contains(event.globalPosition().toPoint()):
                self.close()
                return True
        return False

    def closeEvent(self, event):
        QtWidgets.QApplication.instance().removeEventFilter(self)
        self.workspace.preview_panel.layout().addWidget(self.preview, 1)
        self.preview.show()
        self.workspace.expanded_preview = None
        event.accept()
        self.deleteLater()

    def reject(self):
        self.close()


class MessagesWorkspace(_MessagesWorkspace):
    def __init__(self, owner, session):
        self.editor_window = None
        self.expanded_preview = None
        super().__init__(owner, session)
        self.editor_window = _EditorWindow(self)
        self.editor_window.content.addWidget(owner.create_identity_panel(self.editor_window))
        owner.title_input.textChanged.connect(self._refresh_editor_title)
        for widget in (self.notice, self.actions):
            self.editor_window.content.addWidget(widget)
        self.editor_window.content.addWidget(self.editor_host, 1)
        self.editor_window.content.addWidget(self.style_panel)
        self.main_style_panel = ReadabilityControls(self, self)
        self.layout().addWidget(self.main_style_panel)
        self.heading.hide()
        self.preview_toggle.hide()
        self.preview_panel.show()
        self.open_editor_button = QtWidgets.QPushButton('Open Editor')
        self.open_editor_button.setProperty("themeRole", "accentButton")
        self.open_editor_button.setStyleSheet(
            "QPushButton{padding:6px 18px;border-radius:6px;font-weight:600;}"
        )
        self.open_editor_button.clicked.connect(self.open_editor)
        header = self.layout().itemAt(0).layout()
        header.setSpacing(8)
        page_caption = QtWidgets.QLabel("Letter page")
        page_caption.setProperty("themeRole", "mutedText")
        page_caption.setBuddy(self.selector)
        header.insertWidget(0, page_caption)
        header.addWidget(self.open_editor_button)
        self.open_editor_button.setMinimumHeight(36)
        self.open_editor_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.selector.setMinimumHeight(36)
        self.layout().setSpacing(12)
        self.save_button.hide()
        self.preview_panel.setObjectName("MessagePreviewPanel")
        self.preview_panel.setProperty("themeRole", "card")
        self.preview_panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.preview_panel.setStyleSheet(
            "QWidget#MessagePreviewPanel{border-radius:10px;}"
        )
        self.preview_panel.layout().setContentsMargins(16, 12, 16, 16)
        self.preview_panel.layout().setSpacing(10)
        self.preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.preview_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.preview_scroll.viewport().setAutoFillBackground(False)
        self.preview_label.setProperty("themeRole", "mutedText")
        self.preview_note.setProperty("themeRole", "mutedText")
        self.preview_note.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_note.setWordWrap(True)
        if self.shared_preview is not None:
            self.shared_preview.view.loadFinished.connect(self._quiet_preview)
            self.shared_preview.expand_requested.connect(self.expand_preview)
        self.refresh()

    def expand_preview(self):
        if self.shared_preview is None or self.shared_preview._current is None:
            return
        if self.expanded_preview is None:
            self.expanded_preview = _ExpandedPreview(self)
            self.expanded_preview.show()
            self.expanded_preview.raise_()
            self.expanded_preview.activateWindow()
            self.shared_preview.view.setFocus()

    def _return_preview(self):
        if self.expanded_preview is not None:
            self.expanded_preview.close()
        self.splitter.addWidget(self.preview_panel)
        self.preview_panel.setMaximumWidth(16777215)
        self.preview_panel.show()

    def _quiet_preview(self, ok):
        if not ok or not self.page_id or self.shared_preview is None:
            return
        # Keep Forge's actual artwork/text geometry, but omit presentation
        # controls in this editing surface. The exported presentation is intact.
        script = r'''(() => {
            if (!window.LetterSmithViewer) return;
            const id = PAGE_ID;
            let style = document.getElementById('lettersmith-editor-preview');
            if (!style) {
                style = document.createElement('style');
                style.id = 'lettersmith-editor-preview';
                document.head.appendChild(style);
            }
            style.textContent = `
                #curtain-overlay,#title-banner,#begin-button,#viewer-actions,
                #fullscreen-button,#prev,#next,#page-navigation,#volume-control,
                #open-text,.hud-button-close,.close-text {display:none!important}
                .text-wall.ls-editor-message {opacity:1!important;visibility:visible!important;
                    pointer-events:auto!important;transition:none!important}
                .text-wall.ls-editor-message:not(.canvas-message) {transform:translateX(-50%) scale(1)!important}
            `;
            const wall = Array.from(document.querySelectorAll('.text-wall')).find(x =>
                x.dataset.pageId === id && x.dataset.hasMessage === 'true');
            if (wall) {
                wall.classList.add('ls-editor-message');
                const expose = () => { if (wall.getAttribute('aria-hidden') !== 'false') wall.setAttribute('aria-hidden','false'); };
                expose();
                new MutationObserver(expose).observe(wall,{attributes:true,attributeFilter:['aria-hidden']});
            }
        })()'''.replace('PAGE_ID', json.dumps(self.page_id))
        self.shared_preview.view.page().runJavaScript(script)

    def _label(self, index, page):
        return page_label(self.session.project, page['id'])

    def refresh(self, requested=None):
        result = super().refresh(requested)
        if self.editor_window is not None:
            self.open_editor_button.setEnabled(self.page_id is not None)
            self.editor_window.setWindowTitle(self._editor_title())
            if self.page_id is None:
                self._return_preview()
                self.editor_window.hide()
            self.save_button.setVisible(self._failed)
        return result

    def _editor_title(self):
        name = self.binding.label if self.binding else "Letter"
        if self.page_id is not None and self.binding.target.session_id == self.session.session_id:
            try:
                name = (self.session.project.get_page(self.page_id).get("name")
                        or self.owner.title_input.text().strip() or name)
            except KeyError:
                pass  # A retained draft can outlive its deleted page.
        return "LetterSmith Editor · " + name

    def _refresh_editor_title(self, *_):
        self.editor_window.setWindowTitle(self._editor_title())

    def open_editor(self):
        if not self.open_page(None) or self.editor is None:
            return False
        self.editor_window.setWindowTitle(self._editor_title())
        place_window_on_launcher(self.editor_window, self.open_editor_button)
        self.editor_window.show()
        self.editor_window.raise_()
        self.editor_window.activateWindow()
        self.editor.editor.setFocus()
        self._preview_timer.start()
        return True

    def _error(self, message):
        super()._error(message)
        if self.editor_window is not None:
            self.save_button.show()
            self.editor_window.show()

    def prepare_for_restore(self):
        if self.expanded_preview is not None:
            self.expanded_preview.close()
        super().prepare_for_restore()
        self._return_preview()
        self.editor_window.hide()

    def shutdown(self):
        if self.expanded_preview is not None:
            self.expanded_preview.close()
        result = super().shutdown()
        if result and self.editor_window is not None:
            self.editor_window.hide()
        return result
