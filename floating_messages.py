"""A quiet preview tab with an explicitly opened, draft-preserving editor."""
import json
from PySide6 import QtCore, QtGui, QtWidgets

from messages_workspace import MessagesWorkspace as _MessagesWorkspace
from prompt_writer_project import page_label
from window_chrome import place_window_on_launcher


class _EditorWindow(QtWidgets.QDialog):
    def __init__(self, workspace):
        super().__init__(workspace, QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowCloseButtonHint)
        self.workspace = workspace
        self.setModal(False)
        self.resize(900, 660)
        self.setMinimumSize(460, 380)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.content = QtWidgets.QVBoxLayout(self)

    def closeEvent(self, event):
        if not self.workspace.save():
            event.ignore()
            return
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
        if not self.isVisible():
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


class MessagesWorkspace(_MessagesWorkspace):
    def __init__(self, owner, session):
        self.editor_window = None
        super().__init__(owner, session)
        self.editor_window = _EditorWindow(self)
        for widget in (self.notice, self.actions, self.style_panel, self.editor_host):
            self.editor_window.content.addWidget(widget, 1 if widget is self.editor_host else 0)
        self.heading.hide()
        self.preview_toggle.hide()
        self.preview_panel.show()
        self.open_editor_button = QtWidgets.QPushButton('Open Editor')
        self.open_editor_button.clicked.connect(self.open_editor)
        self.layout().itemAt(0).layout().addWidget(self.open_editor_button)
        self.save_button.hide()
        self.preview_note.setWordWrap(True)
        if self.shared_preview is not None:
            self.shared_preview.view.loadFinished.connect(self._quiet_preview)
        self.refresh()

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
                #open-text,.hud-button-close {display:none!important}
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
            self.editor_window.setWindowTitle('Message · '+(self.binding.label if self.binding else 'Letter'))
            if self.page_id is None:
                self.editor_window.hide()
            self.save_button.setVisible(self._failed)
        return result

    def open_editor(self):
        if not self.open_page(None) or self.editor is None:
            return False
        self.editor_window.setWindowTitle('Message · '+self.binding.label)
        place_window_on_launcher(self.editor_window, self.open_editor_button)
        self.editor_window.show()
        self.editor_window.raise_()
        self.editor_window.activateWindow()
        self.editor.editor.setFocus()
        return True

    def _error(self, message):
        super()._error(message)
        if self.editor_window is not None:
            self.save_button.show()
            self.editor_window.show()

    def prepare_for_restore(self):
        super().prepare_for_restore()
        self.editor_window.hide()

    def shutdown(self):
        result = super().shutdown()
        if result and self.editor_window is not None:
            self.editor_window.hide()
        return result
