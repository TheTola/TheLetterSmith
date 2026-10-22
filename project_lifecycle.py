"""Shared shell decisions; canonical state, copying and storage stay in Foundation."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from project_model import CLASSIC_PAGE_TYPES, MIN_PAGES, MAX_PAGES, PAGE_TYPES


class NewProjectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.resize(520, 490)
        layout = QtWidgets.QVBoxLayout(self)
        self.starter = QtWidgets.QComboBox()
        self.starter.setAccessibleName("Project starter")
        classic_valid = MIN_PAGES <= len(CLASSIC_PAGE_TYPES) <= MAX_PAGES
        if classic_valid:
            self.starter.addItem("Classic Four-Page Letter", "classic")
        self.starter.addItem("Custom Project", "custom")
        layout.addWidget(self.starter)
        self.description = QtWidgets.QLabel(
            "Cover → Illustration → Letter → End Page" if classic_valid else
            f"Four pages are outside the configured limits. Choose {MIN_PAGES}–{MAX_PAGES} pages.")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        row = QtWidgets.QFormLayout()
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(MIN_PAGES, MAX_PAGES)
        self.count.setValue(max(MIN_PAGES, min(len(CLASSIC_PAGE_TYPES), MAX_PAGES)))
        row.addRow("Initial page count", self.count)
        layout.addLayout(row)
        self.pages = QtWidgets.QTableWidget(0, 1)
        self.pages.setHorizontalHeaderLabels(["Page type, in reading order"])
        self.pages.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.pages.setAccessibleName("Ordered starter pages")
        layout.addWidget(self.pages, 1)
        help_text = QtWidgets.QLabel(
            "Cover: opening illustration. Illustration: artwork only.\n"
            "Letter: illustration with independent message text.\n"
            "End Page: closing illustration; its prompt requests “The End.”\n"
            "Types can repeat in any order. Change pages later in Images.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Create Project")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.count.valueChanged.connect(self._resize_pages)
        self.starter.currentIndexChanged.connect(self._starter_changed)
        self._resize_pages(self.count.value())
        self._starter_changed()

    def _resize_pages(self, count):
        previous = self.pages.rowCount()
        self.pages.setRowCount(count)
        for index in range(previous, count):
            choice = QtWidgets.QComboBox()
            choice.setAccessibleName(f"Page {index + 1} type")
            for kind, definition in PAGE_TYPES.items():
                choice.addItem(definition.label, kind)
            default = CLASSIC_PAGE_TYPES[index] if index < len(CLASSIC_PAGE_TYPES) else "illustration"
            choice.setCurrentIndex(choice.findData(default))
            self.pages.setCellWidget(index, 0, choice)

    def _starter_changed(self):
        custom = self.starter.currentData() == "custom"
        self.count.setEnabled(custom)
        self.pages.setEnabled(custom)
        if custom:
            self.description.setText("Choose the initial order. An artwork-only project is valid.")
        else:
            self.count.setValue(len(CLASSIC_PAGE_TYPES))
            for index, kind in enumerate(CLASSIC_PAGE_TYPES):
                choice = self.pages.cellWidget(index, 0)
                choice.setCurrentIndex(choice.findData(kind))
            self.description.setText("Cover → Illustration → Letter → End Page")

    def page_types(self):
        if self.starter.currentData() == "classic":
            return tuple(CLASSIC_PAGE_TYPES)
        return tuple(self.pages.cellWidget(index, 0).currentData() for index in range(self.pages.rowCount()))


class ProjectLifecycle(QtCore.QObject):
    """One in-flight transition and one Save / Discard / Cancel decision."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self._exit_token = None
        self._discard = False

    def token(self):
        session = self.window.project_session
        return session.session_id, session.project.revision

    def finish(self, *_):
        self._set_busy(False)

    def _set_busy(self, busy):
        self.busy = busy
        title_bar = getattr(self.window, "title_bar", None)
        for name in ("new_project_action", "save_project_action", "open_project_action", "exit_action"):
            action = getattr(title_bar, name, None)
            if action is not None:
                action.setEnabled(not busy)
        commands = getattr(getattr(self.window, "command_tab", None), "go_btn", None)
        if commands is not None:
            commands.setEnabled(not busy)
            commands.set_busy(busy)

    def _begin(self):
        window = self.window
        if self.busy or window._shutdown_complete or window._shutdown_in_progress:
            return False
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal is not None:
            window.status(f"Apply or cancel {modal.windowTitle() or 'the open dialog'} before changing projects or exiting.")
            modal.raise_()
            modal.activateWindow()
            return False
        forge = getattr(window, "forge_tab", None)
        if (forge is not None and forge.operation_in_progress) or window._autosave_active_revision is not None:
            window.status("Finish the current project operation, then retry.")
            return False
        self._set_busy(True)
        return True

    def _resolve(self, label, proceed):
        window = self.window
        if not window.flush_project_edits():
            self.finish()
            return False
        token = self.token()
        self._discard = False

        def guarded():
            # Recheck pending fields as well as committed revision after worker/event waits.
            if not window.flush_project_edits() or token != self.token():
                window.status("New edits arrived. They remain open; retry the operation.")
                self.finish()
                return
            try:
                proceed()
            except Exception as error:
                retained = ("Your project is retained; retry when ready." if token[0] == self.token()[0] else
                            "The new canonical project is recoverable. Reopen the application to restore its views.")
                window.status(f"{label} stopped: {error}. {retained}")
                self.finish()

        if window.project_save_service.has_unsaved_editable_changes():
            metadata = window.project_session.project.to_dict()["metadata"]
            title = metadata.get("recipient_title") or metadata.get("title") or "Untitled Project"
            choice = QtWidgets.QMessageBox.question(window, label,
                f'Save changes to “{title}” before continuing?\nDiscard Changes keeps saved projects, recovery and libraries.',
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel)
            if choice == QtWidgets.QMessageBox.Cancel:
                self.finish()
                return False
            if choice == QtWidgets.QMessageBox.Save:
                # Even the recipient screen uses Saved Letters' actual save controller.
                def saved(current):
                    if not current:
                        self.finish()
                    else:
                        window.forge_tab._defer_until_idle(guarded)
                try:
                    window._initialize_project_tabs()
                    started = window.forge_tab.saved_projects.save(finished=saved)
                except Exception as error:
                    window.status(f"Save could not start: {error}. Your edits remain open.")
                    self.finish()
                    return False
                if not started:
                    self.finish()
                    return False
                return True
            self._discard = True
        guarded()
        return True

    def command_save(self, *, reset=False):
        """Save, then optionally clear the workspace and hand off the finished letter."""
        window = self.window

        def explain(message=None):
            message = message or window.statusBar().currentMessage() or "Save did not finish. Your project remains open."
            window.status(message)
            # Command hides the status bar; failures must still be visible.
            window.toast(message, ms=5000)

        if not self._begin():
            explain()
            return False
        try:
            if not window.flush_project_edits():
                explain()
                self.finish()
                return False
            token = self.token()

            def complete():
                try:
                    if reset:
                        if not window.flush_project_edits() or token != self.token():
                            explain("New edits arrived. Saved version retained; reset cancelled.")
                            return
                        from command_bar import build_command_bar_data
                        completed_letter = build_command_bar_data(window.project_root)
                        candidate = window.project_save_service.prepare_new_project(CLASSIC_PAGE_TYPES)
                        window.prepare_for_project_reset()
                        if not window.flush_project_edits() or token != self.token():
                            explain("New edits arrived. Saved version retained; reset cancelled.")
                            return
                        window.project_save_service.activate_new_project(candidate)
                        from recent_media import clear_type_image_history
                        clear_type_image_history(window.project_root)
                        window._show_new_project()
                        if not window.flush_project_edits():
                            raise RuntimeError("The fresh workspace could not be prepared for closing")
                        self._exit_token = self.token()
                        if not window.open_command_bar_and_close_editor(completed_letter):
                            raise RuntimeError("The completed letter's Command Bar could not open")
                except Exception as error:
                    self._exit_token = None
                    retained = ("Your saved project is retained." if token[0] == self.token()[0] else
                                "The fresh project is recoverable. Reopen to restore its views.")
                    explain(f"Reset stopped: {error}. {retained}")
                finally:
                    self.finish()

            def saved(current):
                if current:
                    window.forge_tab._defer_until_idle(complete)
                else:
                    explain()
                    self.finish()

            window._initialize_project_tabs()
            if not window.forge_tab.saved_projects.save(finished=saved):
                explain()
                self.finish()
                return False
            return True
        except Exception as error:
            explain(f"Save stopped: {error}. Your project remains open.")
            self.finish()
            return False

    def new_project(self):
        if not self._begin():
            return False
        dialog = NewProjectDialog(self.window)
        try:
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                self.finish()
                return False
            candidate = self.window.project_save_service.prepare_new_project(dialog.page_types())
        except Exception as error:
            self.window.status(f"New Project could not be prepared: {error}. The current project is unchanged.")
            self.finish()
            return False
        finally:
            dialog.deleteLater()

        def activate():
            origin = self.token()
            if self.window._project_tabs_initialized:
                self.window.prepare_for_project_reset()
            if origin != self.token():
                raise RuntimeError("New edits arrived while releasing media")
            self.window.project_save_service.activate_new_project(candidate)
            self.window._show_new_project()
            self.finish()
        return self._resolve("New Project", activate)

    def open_saved(self, entry):
        if not self._begin():
            return False
        def open_candidate():
            if not self.window.forge_tab.saved_projects._read_and_open(entry, finished=self.finish):
                self.finish()
        return self._resolve("Open Project", open_candidate)

    def request_exit(self):
        if not self._begin():
            return False
        def close():
            if self._discard:
                prepared = self.window.project_save_service.prepare_discard_project()
                origin = self.token()
                if self.window._project_tabs_initialized:
                    self.window.prepare_for_project_reset()
                if origin != self.token():
                    raise RuntimeError("New edits arrived while releasing media")
                self.window.project_save_service.discard_active_changes(prepared)
                if self.window._project_tabs_initialized:
                    with self.window.project_session.restoring():
                        self.window._refresh_restored_project()
                    self.window._refresh_prompt_writer_after_restore()
                    if self.window._project_view_restore_errors:
                        raise RuntimeError("Views could not refresh after discarding; reopen the saved project")
            self._exit_token = self.token()
            # Qt suppresses recursive close() calls from inside closeEvent.
            QtCore.QTimer.singleShot(0, self.window.close)
        return self._resolve("Exit Application", close)

    def exit_is_authorized(self):
        return self._exit_token is not None and self._exit_token == self.token()

    def exit_failed(self):
        self._exit_token = None
        self.finish()
