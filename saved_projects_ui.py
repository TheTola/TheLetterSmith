"""Saved Letters controls; authored state and persistence stay in shared services."""
from __future__ import annotations

from pathlib import Path
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets

from project_model import ProjectModel
from project_save import ProjectSaveService
from project_session import ProjectSession, get_project_session
from saved_letters import SavedLetter


class SavedProjectsController(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, forge):
        super().__init__(forge)
        self.forge = forge
        self.service = getattr(forge, "project_save_service", None) or getattr(forge.window(), "project_save_service", None) or ProjectSaveService(forge.project_root, forge.project_state)
        self.previews = []
        self.github_library = None
        layout = forge.saved_panel.layout()
        self.details = QtWidgets.QLabel()
        self.details.setTextFormat(QtCore.Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setMaximumHeight(115)
        self.details.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.details.hide()
        row = QtWidgets.QHBoxLayout()
        self.row = row
        self.open_button = QtWidgets.QPushButton("Open Project")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self.open_selected)
        row.addWidget(self.open_button)
        self.menu = QtWidgets.QMenu(forge.saved_panel)
        self.preview_action = self.menu.addAction("Preview Saved Version", self.preview_selected)
        self.duplicate_action = self.menu.addAction("Duplicate Saved Version", self.duplicate_selected)
        self.rename_action = self.menu.addAction("Rename Saved Project", self.rename_selected)
        self.delete_action = self.menu.addAction("Delete Saved Project (keep recovery copy)", self.delete_selected)
        self.menu.addSeparator()
        self.copy_action = self.menu.addAction("Save a Copy of Open Project…", lambda: self.save(copy=True))
        self.recovery_action = self.menu.addAction("Recover Content in Open Project…", self.recover_content)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(forge.saved_panel.hide)
        row.addWidget(close)
        layout.addLayout(row)
        from github_library_ui import GitHubLibraryController
        self.github_library = GitHubLibraryController(self)
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence.Save, forge.window())
        self.shortcut.activated.connect(self.save)
        # A hidden Forge widget cannot own the application's global shortcut.
        # Nexus supplies its shared QAction; standalone Forge keeps this shortcut.
        self.shortcut.setEnabled(getattr(forge, "project_save_service", None) is None)
        self.changed.connect(self.selected_changed)
        self._observed_session = self.session
        self._session_listener = lambda _event: self.changed.emit()
        self._observed_session.add_listener(self._session_listener)
        self.selected_changed()

    @property
    def session(self):
        return get_project_session(self.forge.project_root)

    def _status(self, text, *, error=False):
        self.forge._set_status(text, error=error, timeout_ms=0)
        if error:
            self.details.setText(text)
            self.details.show()
        hook = getattr(self.forge.window(), "status", None)
        if callable(hook):
            hook(text)

    def _flush(self):
        hook = getattr(self.forge.window(), "flush_project_edits", None)
        if callable(hook):
            return bool(hook())
        return self.forge._flush_prompt_writer_state()

    def _run(self, activity, task, success, *, failed=None, quiet=False):
        if self.forge._busy:
            self._status("Wait for the current operation to finish.")
            return False

        def work():
            try:
                return task(), ""
            except Exception as error:
                return None, str(error)

        def finished(result):
            value, error = result
            if error:
                self._status(f"{activity.rstrip('…')} failed: {error}. Your open edits are retained. Retry, reload the saved version, or Save a Copy.", error=True)
                if failed is not None:
                    failed(False)
                return
            try:
                success(value)
            except Exception as error:
                self._status(f"{activity.rstrip('…')} could not finish: {error}. Your edits remain open.", error=True)
                if failed is not None:
                    failed(False)

        self.forge._start_operation(activity, work, finished, "The project operation failed; your edits remain open.", quiet=quiet)
        return True

    def save(self, _checked=False, *, copy=False, after=None, finished=None):
        if self.forge._busy or not self._flush():
            return False
        try:
            title = None
            if copy:
                title, accepted = QtWidgets.QInputDialog.getText(self.forge, "Save a Copy", "Copy title (the current project stays open):",
                    text=str(self.session.project.to_dict()["metadata"].get("recipient_title", "Untitled Project")) + " — Copy")
                if not accepted:
                    return False
            capture = self.service.capture_editable_save(copy=copy, title=title)
        except Exception as error:
            self._status(f"Save stopped: {error}. Repair the field or cancel; your edits remain open.", error=True)
            return False
        dirty = getattr(self.forge.window(), "project_dirty", None)
        dirty_revision = dirty.revision if dirty is not None else None
        feedback = getattr(self.forge.window(), "set_save_feedback", lambda _state: None)
        feedback("Saving…")

        def saved(stored):
            current = self.service.complete_editable_save(capture, stored)
            if current and dirty is not None:
                dirty.mark_saved(expected_revision=dirty_revision)
            entry = self.forge.catalog._entry(capture.directory)
            self.forge._selected_saved_letter = entry
            self.forge._refresh_catalog_entry(capture.directory)
            self._status("Copy saved; the current project remains open." if copy else
                         "Project saved." if current else "Saved the captured version; newer edits remain unsaved.")
            self.selected_changed()
            feedback("Saved" if current else "Unsaved changes")
            if after is not None and current:
                self.forge._defer_until_idle(after)
            if finished is not None:
                finished(current)

        def failed(current):
            feedback("Save failed")
            if finished is not None:
                finished(current)
        return self._run("Saving project…", lambda: self.service.commit_editable_save(capture), saved, failed=failed)

    def selected_changed(self):
        if self.session.is_restoring:
            return
        entry = self.forge._selected_saved_letter
        valid = isinstance(entry, SavedLetter) and entry.valid
        self.open_button.setEnabled(valid)
        self.preview_action.setEnabled(valid)
        self.duplicate_action.setEnabled(valid)
        editable = valid and entry.canonical and not (entry.example or entry.stock or entry.recovery)
        self.rename_action.setEnabled(editable)
        self.delete_action.setEnabled(valid and not (entry.example or entry.stock or entry.recovery))
        self.recovery_action.setEnabled(bool(self.session.project.recoverable_items()))
        binding = self.service._binding(self.session)
        active_path = Path(binding["directory"]).resolve() if binding else None
        for card in self.forge._saved_cards:
            current = card.entry.project_id == self.session.project.project_id and card.entry.path.resolve() == active_path
            card.setProperty("currentProject", current)
        self.github_library.update_selection()

    def show_context_menu(self, card, point):
        if self.forge._busy:
            return
        if hasattr(card, "entry"):
            self.forge._select_saved_letter(card.entry)
        else:
            self.github_library.select(card.key)
        self.selected_changed()
        self.menu.exec(card.mapToGlobal(point))

    def open_selected(self):
        entry = self.forge._selected_saved_letter
        if entry is None and (self.github_library.selected() is not None
                              or self.github_library.selected_id in self.github_library.downloads):
            return self.github_library.download_selected()
        if not isinstance(entry, SavedLetter) or not entry.valid or self.forge._busy:
            return False
        lifecycle = getattr(self.forge.window(), "project_lifecycle", None)
        if lifecycle is not None:
            return lifecycle.open_saved(entry)
        if not self._flush():
            return False
        if self.service.has_unsaved_editable_changes():
            choice = QtWidgets.QMessageBox.question(self.forge.saved_panel, "Open Project",
                "Save the current editable project before opening the selected saved version?",
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel)
            if choice == QtWidgets.QMessageBox.Cancel:
                return False
            if choice == QtWidgets.QMessageBox.Save:
                return self.save(after=lambda: self._read_and_open(entry))
        return self._read_and_open(entry)

    def _read_and_open(self, entry, *, finished=None):
        origin, revision = self.session.session_id, self.session.project.revision

        def loaded(candidate):
            if not self._flush() or (origin, revision) != (self.session.session_id, self.session.project.revision):
                self._status("The open project changed while loading. Its edits were kept; open the saved project again.", error=True)
                if finished is not None:
                    finished(False)
                return
            self.forge._begin_restore_activity("Opening project…")
            try:
                if not self.forge._release_project_files_for_restore():
                    return
                if not self._flush() or (origin, revision) != (self.session.session_id, self.session.project.revision):
                    raise RuntimeError("New edits arrived during the transition; retry opening")
                stored = self.service.open_saved_project(entry, loaded=candidate)
                self.forge._last_play_dir = None
                payload = {"project_id": stored.project.project_id, "title": entry.title, "play_dir": str(entry.path)}
                self.forge.project_restored.emit(payload)
                self.forge.letter_loaded.emit(payload)
                self.forge.saved_panel.hide()
                self.forge._refresh_catalog_entry(entry.path)
                self.selected_changed()
                view_errors = getattr(self.forge.window(), "_project_view_restore_errors", ())
                if view_errors:
                    self._status("Project activated and retained, but these views need reopening: " + ", ".join(view_errors), error=True)
                    return
                self._status("Project opened.")
            except Exception as error:
                retained = ("The previous project is retained." if origin == self.session.session_id else
                            "The activated project is recoverable; reopen the application to restore its views.")
                self._status(f"Open failed: {error}. {retained}", error=True)
            finally:
                self.forge._finish_restore_activity()
                if finished is not None:
                    finished()

        return self._run("Reading saved project…", lambda: self.service.read_saved_project(entry), loaded, failed=finished)

    def duplicate_selected(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter):
            return
        def copied(result):
            destination, _ = result
            self.forge._selected_saved_letter = self.forge.catalog._entry(destination)
            self.forge._refresh_catalog_entry(destination)
            self._status("Duplicated the saved version. The open project and its unsaved edits are unchanged.")
            self.selected_changed()
        self._run("Duplicating saved project…", lambda: self.service.duplicate_saved_project(entry), copied)

    def rename_selected(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter):
            return
        title, accepted = QtWidgets.QInputDialog.getText(self.forge, "Rename Saved Project", "Display title:", text=entry.title)
        if not accepted or title == entry.title:
            return
        origin, revision = self.session.session_id, self.session.project.revision
        clean_active = entry.project_id == self.session.project.project_id and not self.service.has_unsaved_editable_changes()
        def renamed(stored):
            if clean_active and (origin, revision) == (self.session.session_id, self.session.project.revision):
                self.session.project.update_project_data("metadata", {"title": title, "recipient_title": title})
                self.forge.settings.update_fields({"recipient_title": title})
                self.service._bind(self.session, entry.path, stored.revision, self.session.project.revision)
            self.forge._selected_saved_letter = self.forge.catalog._entry(entry.path)
            self.forge._refresh_catalog_entry(entry.path)
            self.selected_changed()
            self._status("Saved project renamed. Its publication has not changed.")
        self._run("Renaming saved project…", lambda: self.service.rename_saved_project(entry, title), renamed)

    def delete_selected(self, _checked=False, *, entry=None):
        entry = entry or self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter) or entry.example or entry.stock or entry.recovery or self.forge._busy:
            return False
        choice = QtWidgets.QMessageBox.question(self.forge.saved_panel, "Delete Saved Project",
            f'Delete saved project “{entry.title}”?\nID: {entry.project_id or entry.path.name}\n\n'
            "The selected save moves to Recovery. The open project, source media, Music Archive, and published website remain available.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
        if choice != QtWidgets.QMessageBox.Yes:
            return False
        if entry.project_id == self.session.project.project_id:
            if not self._flush():
                return False
            try:
                self.session.save()
            except Exception as error:
                self._status(f"Deletion stopped: {error}. Save or repair the current edits first.", error=True)
                return False
        def deleted(_result):
            if entry.project_id == self.session.project.project_id:
                self.service._bind(self.session, self.service.editable_root / entry.project_id, None, -1)
            self.forge._selected_saved_letter = None
            self.forge.refresh_saved_letters(force_reconcile=True)
            self.selected_changed()
            self._status("Saved project moved to Recovery. Open or duplicate its recovery copy to keep editing.")
        return self._run("Deleting saved project…", lambda: self.forge.catalog.delete(entry), deleted)

    def preview_selected(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter) or not entry.valid:
            return
        def show(loaded):
            from message_shared_preview import SharedMessagePreview
            from project_paths import application_paths
            import forge_render
            temporary = tempfile.TemporaryDirectory(prefix="saved-preview-", dir=application_paths(self.forge.project_root).cache_root)
            session = ProjectSession(loaded.project, project_root=loaded.asset_root, asset_root=loaded.asset_root)
            dialog = QtWidgets.QDialog(self.forge.window())
            dialog.setWindowTitle("Preview Saved Version — " + entry.title)
            dialog.resize(900, 700)
            layout = QtWidgets.QVBoxLayout(dialog)
            note = QtWidgets.QLabel("Read-only saved version. Sound muted.")
            note.setWordWrap(True)
            layout.addWidget(note)
            preview = SharedMessagePreview(dialog, session, temporary.name, forge_render.SavedProjectPreviewAdapter(self.forge.project_root))
            preview.view.setAccessibleName("Read-only preview of the saved project")
            preview.status_changed.connect(note.setText)
            layout.addWidget(preview, 1)
            record = (dialog, preview, session, temporary)
            self.previews.append(record)
            def closed(_result):
                if preview.shutdown():
                    session.close()
                    temporary.cleanup()
                    if record in self.previews:
                        self.previews.remove(record)
                    dialog.deleteLater()
            dialog.finished.connect(closed)
            dialog.show()
            if loaded.project.pages:
                preview.request(loaded.project.pages[0]["id"])
            else:
                note.setText("This saved draft has no pages yet.")
        self._run("Reading saved preview…", lambda: self.service.read_saved_project(entry), show)

    def recover_content(self):
        if not self._flush():
            return
        items = self.session.project.recoverable_items()
        if not items:
            return
        dialog = QtWidgets.QDialog(self.forge.window())
        dialog.setWindowTitle("Recover Content — Open Project")
        dialog.resize(540, 440)
        layout = QtWidgets.QVBoxLayout(dialog)
        source = QtWidgets.QComboBox()
        destination = QtWidgets.QComboBox()
        preview = QtWidgets.QPlainTextEdit()
        preview.setReadOnly(True)
        for item in items:
            source.addItem(f'{item["kind"].title()}: {item["key"] or "Unassigned legacy message"}')
        layout.addWidget(source)
        layout.addWidget(preview, 1)
        layout.addWidget(QtWidgets.QLabel("Destination page:"))
        layout.addWidget(destination)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def selected(index):
            item = items[index]
            value = item["value"]
            preview.setPlainText(str(value.get("html", value.get("text", value)) if isinstance(value, dict) else value))
            destination.clear()
            destination.addItem("Choose a destination…", None)
            for number, page in enumerate(self.session.project.pages, 1):
                if item["kind"] == "prompt" or page["type"] == "letter":
                    destination.addItem(f'{number}. {page["name"] or page["type"]} · {page["id"][:8]}', page["id"])
        source.currentIndexChanged.connect(selected)
        selected(0)
        origin, revision = self.session.session_id, self.session.project.revision
        if dialog.exec() != QtWidgets.QDialog.Accepted or destination.currentData() is None:
            return
        if (origin, revision) != (self.session.session_id, self.session.project.revision):
            self._status("The open project changed; choose the recovery destination again.", error=True)
            return
        item, page_id = items[source.currentIndex()], destination.currentData()
        candidate = ProjectModel.from_dict(self.session.project.to_dict())
        page = candidate.get_page(page_id)
        occupied = page["message"].get("html") if item["kind"] == "message" else page["prompt"].get("text")
        if occupied:
            box = QtWidgets.QMessageBox(self.forge.window())
            box.setWindowTitle("Occupied Destination")
            box.setText("Replace this content and retain its previous version, or assign to a new copy of the page?")
            overwrite = box.addButton("Replace (keep previous)", QtWidgets.QMessageBox.AcceptRole)
            copy = box.addButton("Copy to a new page", QtWidgets.QMessageBox.ActionRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() not in (overwrite, copy):
                return
            if box.clickedButton() is copy:
                try:
                    page_id = candidate.duplicate_page(page_id)
                except Exception as error:
                    self._status(str(error), error=True)
                    return
        try:
            candidate.assign_recovered(item["kind"], item["key"], page_id, replace=True)
            if (origin, revision) != (self.session.session_id, self.session.project.revision):
                raise RuntimeError("The project changed; repeat the assignment")
            self.session.adopt(candidate, kind="recovery")
            self.selected_changed()
            self._status("Recovery content assigned. Save Project to retain the repair; original and previous versions remain recoverable.")
        except Exception as error:
            self._status(str(error), error=True)

    def prepare_for_shutdown(self):
        return all(preview.release() for _dialog, preview, _session, _temporary in tuple(self.previews))

    def shutdown(self):
        for dialog, preview, session, temporary in tuple(self.previews):
            if not preview.shutdown():
                return False
            session.close()
            dialog.close()
            temporary.cleanup()
        self.previews.clear()
        self._observed_session.remove_listener(self._session_listener)
        return True
