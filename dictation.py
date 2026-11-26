"""Editor-bound offline dictation shared by Prompt Writer and the letter editor."""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from project_paths import runtime_application_paths


class DictationSettings:
    """Device-local preferences; never put model paths in letter/project data."""

    def __init__(self):
        path = runtime_application_paths().settings_root / "dictation.ini"
        self.settings = QtCore.QSettings(str(path), QtCore.QSettings.IniFormat)

    def load(self):
        return {key: str(self.settings.value(key, "") or "") for key in
                ("dictation_model_pack", "dictation_microphone")}

    def update(self, **values):
        for key, value in values.items():
            self.settings.setValue(key, value)
        self.settings.sync()
        if self.settings.status() != QtCore.QSettings.NoError:
            raise OSError("Could not save dictation settings.")


class _PackVerifier(QtCore.QThread):
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def cancel(self):
        self.requestInterruption()

    def run(self):
        from dictation_models import verify_speech_pack
        try:
            verify_speech_pack(self.path, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.completed.emit(self.path)
        except InterruptedError:
            pass
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))


def _configure(manager, parent):
    from dictation_models import ModelDownloadWorker
    from PySide6.QtMultimedia import QMediaDevices
    from ui_dialogs import LetterSmithDialog

    class SetupDialog(LetterSmithDialog):
        def __init__(self):
            super().__init__(parent, title="Dictation", width=470)
            self.worker = None
            self.closing = False
            self.status = QtWidgets.QLabel(self.panel)
            self.status.setWordWrap(True)
            self.status.setTextFormat(QtCore.Qt.PlainText)
            self.content_layout.addWidget(self.status)
            self.microphones = QtWidgets.QComboBox(self.panel)
            self.microphones.setAccessibleName("Dictation microphone")
            self.content_layout.addWidget(QtWidgets.QLabel("Microphone", self.panel))
            self.content_layout.addWidget(self.microphones)
            self.devices = QMediaDevices(self)
            self.devices.audioInputsChanged.connect(self.refresh_devices)
            self.refresh_devices()
            self.microphones.currentIndexChanged.connect(self.select_microphone)
            self.progress = QtWidgets.QProgressBar(self.panel)
            self.progress.hide()
            self.content_layout.addWidget(self.progress)
            row = QtWidgets.QHBoxLayout()
            self.download = QtWidgets.QPushButton("Download English model", self.panel)
            self.download.clicked.connect(self.start_download)
            self.existing = QtWidgets.QPushButton("Use existing model…", self.panel)
            self.existing.clicked.connect(self.choose_pack)
            row.addWidget(self.download)
            row.addWidget(self.existing)
            self.content_layout.addLayout(row)
            self.done = QtWidgets.QPushButton("Done", self.panel)
            self.done.clicked.connect(self.accept)
            self.content_layout.addWidget(self.done)
            QtWidgets.QApplication.instance().aboutToQuit.connect(self.shutdown)
            self.refresh_status()

        def refresh_status(self):
            ready = bool(manager.store.load()["dictation_model_pack"])
            self.status.setText("English dictation is ready. Speech is processed on this computer."
                                if ready else "Download the compact English model (191 MB) once. "
                                "Speech is then processed on this computer.")

        def refresh_devices(self):
            selected = manager.store.load()["dictation_microphone"]
            with QtCore.QSignalBlocker(self.microphones):
                self.microphones.clear()
                self.microphones.addItem("Windows default input", "")
                for device in QMediaDevices.audioInputs():
                    self.microphones.addItem(device.description(), bytes(device.id()).hex())
                index = self.microphones.findData(selected)
                if index < 0:
                    self.microphones.addItem("Selected microphone (disconnected)", selected)
                    index = self.microphones.count() - 1
                self.microphones.setCurrentIndex(index)

        def select_microphone(self):
            try:
                manager.store.update(dictation_microphone=self.microphones.currentData() or "")
            except OSError as error:
                self.status.setText(str(error))

        def begin(self, worker):
            self.worker = worker
            self.download.setEnabled(False)
            self.existing.setEnabled(False)
            self.done.setText("Cancel")
            self.done.clicked.disconnect()
            self.done.clicked.connect(self.reject)
            self.progress.setRange(0, 0)
            self.progress.show()
            worker.completed.connect(self.installed)
            worker.failed.connect(self.failed)
            worker.finished.connect(self.finished_work)
            if hasattr(worker, "status"):
                worker.status.connect(self.status.setText)
            worker.start()

        def start_download(self):
            self.status.setText("Downloading English dictation…")
            worker = ModelDownloadWorker(self)
            worker.progress.connect(self.download_progress)
            self.begin(worker)

        def download_progress(self, received, total):
            if total > 0:
                self.progress.setRange(0, 100)
                self.progress.setValue(min(100, int(received * 100 / total)))

        def choose_pack(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "English speech model folder")
            if path:
                self.status.setText("Checking English model…")
                self.begin(_PackVerifier(path, self))

        def installed(self, path):
            if not self.closing:
                try:
                    manager.store.update(dictation_model_pack=path)
                    self.refresh_status()
                except OSError as error:
                    self.status.setText(str(error))

        def failed(self, message):
            self.status.setText(message)

        def finished_work(self):
            worker, self.worker = self.worker, None
            worker.deleteLater()
            self.progress.hide()
            self.download.setEnabled(True)
            self.existing.setEnabled(True)
            self.done.setText("Done")
            self.done.clicked.disconnect()
            self.done.clicked.connect(self.accept)
            if self.closing:
                super().reject()

        def reject(self):
            if self.worker is not None:
                self.closing = True
                self.worker.cancel()
                self.status.setText("Canceling…")
                self.done.setEnabled(False)
            else:
                super().reject()

        def closeEvent(self, event):
            if self.worker is not None:
                event.ignore()
                self.reject()
            else:
                super().closeEvent(event)

        def shutdown(self):
            if self.worker is not None:
                self.closing = True
                self.worker.cancel()
                self.worker.wait()

    dialog = SetupDialog()
    dialog.exec()
    dialog.deleteLater()


class DictationManager(QtCore.QObject):
    def __init__(self, app):
        super().__init__(app)
        from dictation_speech import SpeechService
        from dictation_session import DictationSessionMonitor
        self.store = DictationSettings()
        self.speech = SpeechService(self.store, self)
        self.active = None
        self.monitor = DictationSessionMonitor(self.cancel)
        self.speech.capture_allowed = lambda: self.monitor.available and not self.monitor.locked
        app.aboutToQuit.connect(self.dispose)

    def ensure_model(self, parent):
        if sys.platform != "win32":
            _show_error(parent, "Offline dictation is currently available on Windows.")
            return False
        if not self.store.load()["dictation_model_pack"]:
            from dictation_models import find_installed_pack
            try:
                existing = find_installed_pack()
                if existing:
                    self.store.update(dictation_model_pack=str(existing))
            except (OSError, ValueError) as error:
                _show_error(parent, str(error))
                return False
        if not self.store.load()["dictation_model_pack"]:
            _configure(self, parent)
        return bool(self.store.load()["dictation_model_pack"])

    def cancel(self):
        if self.active is not None:
            self.active.cancel()
        else:
            self.speech.cancel()

    def dispose(self):
        self.cancel()
        self.speech.dispose()
        self.monitor.close()


def _manager():
    app = QtWidgets.QApplication.instance()
    manager = getattr(app, "_lettersmith_dictation", None)
    if manager is None:
        manager = app._lettersmith_dictation = DictationManager(app)
    return manager


def _show_error(parent, message):
    from ui_dialogs import show_lettersmith_message
    show_lettersmith_message(parent, "Dictation", message)


def _icon(widget, active=False):
    pixmap = QtGui.QPixmap(24, 24)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    color = QtGui.QColor("#ed6464") if active else widget.palette().color(QtGui.QPalette.ButtonText)
    painter.setPen(QtGui.QPen(color, 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    if active:
        painter.setBrush(color)
        painter.drawRoundedRect(QtCore.QRectF(6, 6, 12, 12), 2, 2)
    else:
        painter.drawRoundedRect(QtCore.QRectF(9, 3, 6, 12), 3, 3)
        painter.drawArc(QtCore.QRectF(6, 7, 12, 12), 180 * 16, 180 * 16)
        painter.drawLine(12, 19, 12, 22)
        painter.drawLine(8, 22, 16, 22)
    painter.end()
    return QtGui.QIcon(pixmap)


class DictationBinding(QtCore.QObject):
    finished = QtCore.Signal()
    status_changed = QtCore.Signal(str)

    def __init__(self, editor, button=None, label="Dictate", *, allow_queued_restart=False):
        super().__init__(editor)
        self.editor = editor
        self.label = label
        self.manager = None
        self.active = False
        self.finishing = False
        self.listening = False
        self.allow_queued_restart = allow_queued_restart
        self.restart_requested = False
        self.inserting = False
        self.generation = 0
        self.session = None
        self.window = None
        self.expected_target = None
        self._toggle_shortcut = None
        self.embedded = button is None
        if button is None:
            button = QtWidgets.QToolButton(editor)
            button.setFixedSize(28, 28)
            button.setAutoRaise(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            button.setIconSize(QtCore.QSize(18, 18))
            button.setStyleSheet("QToolButton { padding: 0px; }")
            margins = editor.viewportMargins()
            editor.setViewportMargins(margins.left(), margins.top(), margins.right() + 36, margins.bottom())
        self.button = button
        if not button.objectName():
            button.setObjectName("dictationButton")
        if isinstance(button, QtWidgets.QAbstractButton):
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.clicked.connect(self.toggle)
            button.installEventFilter(self)
        else:
            button.triggered.connect(self.toggle)
        editor.installEventFilter(self)
        editor.viewport().installEventFilter(self)
        editor.verticalScrollBar().installEventFilter(self)
        editor.document().contentsChanged.connect(self.content_changed)
        editor.cursorPositionChanged.connect(self.cursor_changed)
        editor.textChanged.connect(self.content_changed)
        editor.destroyed.connect(self.editor_destroyed)
        self.refresh()

    def enable_toggle_shortcut(self, key):
        if self._toggle_shortcut is None:
            self._toggle_shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.editor)
            self._toggle_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            self._toggle_shortcut.setAutoRepeat(False)
            self._toggle_shortcut.activated.connect(self.toggle)
            self.refresh()

    def refresh(self):
        if not isValid(self.editor) or not isValid(self.button):
            return
        icon_owner = self.button if isinstance(self.button, QtWidgets.QWidget) else self.editor
        self.button.setIcon(_icon(icon_owner, self.active and not self.finishing))
        status, action = "", "Right-click for settings"
        if self.finishing:
            status = "Restart queued…" if self.restart_requested else "Finishing dictation…"
            action = "Click to cancel restart" if self.restart_requested else "Click to restart" if self.allow_queued_restart else ""
        elif self.active:
            status = "Listening…" if self.listening else "Starting dictation…"
            action = "Click to stop dictation"
        text = status or self.label
        self.button.setText(text)
        details = f"{text} · {action}" if action else text
        if self._toggle_shortcut is not None:
            key = self._toggle_shortcut.key().toString(QtGui.QKeySequence.NativeText)
            details = f"{details} ({key})"
        self.button.setToolTip(details)
        if isinstance(self.button, QtWidgets.QAbstractButton):
            self.button.setAccessibleName(text)
        self.button.setEnabled(self.editor.isEnabled() and not self.editor.isReadOnly()
                               and (not self.finishing or self.allow_queued_restart))
        self.status_changed.emit(status)
        self.position_button()

    def position_button(self):
        if not self.embedded or not isValid(self.editor) or not isValid(self.button):
            return
        rect = self.editor.contentsRect()
        right = rect.right() - 3
        scrollbar = self.editor.verticalScrollBar()
        if scrollbar.isVisible():
            scrollbar_x = scrollbar.mapTo(self.editor, QtCore.QPoint()).x()
            if scrollbar_x > rect.center().x():
                right = min(right, scrollbar_x - 4)
        self.button.move(right - self.button.width(), rect.top() + 3)
        self.button.raise_()

    def valid_target(self):
        return (isValid(self.editor) and self.editor.isVisible() and
                self.editor.isEnabled() and not self.editor.isReadOnly())

    def target_snapshot(self):
        cursor = self.editor.textCursor()
        document = self.editor.document()
        return document, document.revision(), cursor.position(), cursor.anchor()

    def toggle(self):
        if self.active:
            if not self.finishing:
                self.finishing = True
                self.refresh()
                self.manager.speech.stop(self.session)
            elif self.allow_queued_restart:
                self.restart_requested = not self.restart_requested
                self.refresh()
            return
        if not self.valid_target():
            return
        manager = _manager()
        if manager.active is not None:
            manager.active.cancel()
        if not manager.ensure_model(self.editor.window()) or not self.valid_target():
            return
        self.manager = manager
        cursor = self.editor.textCursor()
        cursor.setPosition(cursor.selectionEnd())
        self.editor.setTextCursor(cursor)
        self.editor.setFocus(QtCore.Qt.OtherFocusReason)
        self.window = self.editor.window()
        self.window.installEventFilter(self)
        self.active = True
        self.finishing = False
        self.generation += 1
        generation = self.generation
        self.expected_target = self.target_snapshot()
        manager.active = self
        self.refresh()
        self.session = manager.speech.start(lambda event: self.speech_event(event, generation))

    def speech_event(self, event, generation=None):
        if not self.active or generation is not None and generation != self.generation:
            return
        if not self.valid_target() or self.target_snapshot() != self.expected_target:
            self.cancel()
            return
        kind = event["event"]
        if kind in {"partial", "final"}:
            self.inserting = True
            try:
                text = event["text"]
                previous_cursor = QtGui.QTextCursor(self.editor.textCursor())
                previous_cursor.movePosition(QtGui.QTextCursor.PreviousCharacter, QtGui.QTextCursor.KeepAnchor)
                previous = previous_cursor.selectedText()
                if text and previous and not previous[-1].isspace():
                    text = " " + text
                ime = QtGui.QInputMethodEvent(text if kind == "partial" else "", [])
                if kind == "final":
                    ime.setCommitString(text)
                QtWidgets.QApplication.sendEvent(self.editor, ime)
                if self.active:
                    self.expected_target = self.target_snapshot()
            finally:
                self.inserting = False
        elif kind == "ready":
            self.listening = True
            self.refresh()
        elif kind == "error":
            message = event["message"]
            self.cancel()
            _show_error(self.editor.window(), message)
        elif kind == "done":
            restart = self.restart_requested
            self.clear()
            if restart and self.manager.active is None:
                self.toggle()

    def cancel(self):
        if self.active:
            self.manager.speech.cancel(self.session)
            self.clear()

    def clear(self):
        was_active = self.active
        self.generation += 1
        self.inserting = True
        try:
            if isValid(self.editor):
                QtWidgets.QApplication.sendEvent(self.editor, QtGui.QInputMethodEvent("", []))
        finally:
            self.inserting = False
        self.active = self.finishing = self.listening = self.restart_requested = False
        self.session = None
        self.expected_target = None
        if self.manager and self.manager.active is self:
            self.manager.active = None
        if self.window is not None and isValid(self.window):
            self.window.removeEventFilter(self)
        self.window = None
        self.refresh()
        if was_active:
            self.finished.emit()

    def editor_destroyed(self):
        if self.active:
            self.manager.speech.cancel(self.session)
            if self.manager.active is self:
                self.manager.active = None
        self.active = False
        self.generation += 1

    def content_changed(self, *_):
        if self.active and not self.inserting:
            self.cancel()
        self.position_button()

    def cursor_changed(self, *_):
        if self.active and not self.inserting:
            self.cancel()

    def eventFilter(self, watched, event):
        if not isValid(self.editor):
            return False
        kind = event.type()
        if self.active and not self.inserting and watched in {
            self.editor, self.editor.viewport(), self.window
        }:
            # Let modifier presses reach the stop shortcut without cancelling
            # the pending transcript before the full key combination arrives.
            typing = kind == QtCore.QEvent.KeyPress and event.key() not in {
                QtCore.Qt.Key_Control, QtCore.Qt.Key_Shift, QtCore.Qt.Key_Alt,
                QtCore.Qt.Key_Meta, QtCore.Qt.Key_AltGr,
            }
            if typing or kind in {QtCore.QEvent.Hide, QtCore.QEvent.Close, QtCore.QEvent.WindowDeactivate,
                                  QtCore.QEvent.FocusOut, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.Drop,
                                  QtCore.QEvent.EnabledChange, QtCore.QEvent.ReadOnlyChange}:
                self.cancel()
        if watched is self.editor:
            if kind in {QtCore.QEvent.Resize, QtCore.QEvent.Show, QtCore.QEvent.PaletteChange,
                        QtCore.QEvent.EnabledChange, QtCore.QEvent.ReadOnlyChange}:
                self.refresh()
        elif watched is self.button and kind in {QtCore.QEvent.PaletteChange, QtCore.QEvent.StyleChange}:
            self.refresh()
        elif watched is self.editor.verticalScrollBar() and kind in {
            QtCore.QEvent.Show, QtCore.QEvent.Hide, QtCore.QEvent.Move, QtCore.QEvent.Resize
        }:
            self.position_button()
        if kind == QtCore.QEvent.ContextMenu and watched is self.button:
            menu = QtWidgets.QMenu(self.button)
            action = menu.addAction("Dictation settings…")
            action.triggered.connect(self.configure)
            menu.exec(event.globalPos())
            menu.deleteLater()
            return True
        return False

    def configure(self):
        manager = _manager()
        manager.cancel()
        _configure(manager, self.editor.window())


def attach_dictation(editor, *, button=None, label="Dictate", allow_queued_restart=False):
    binding = getattr(editor, "_dictation_binding", None)
    if binding is None:
        binding = DictationBinding(editor, button, label, allow_queued_restart=allow_queued_restart)
        editor._dictation_binding = binding
    return binding
