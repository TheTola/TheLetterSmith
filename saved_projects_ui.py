"""Saved Letters controls; authored state and persistence stay in shared services."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets

from project_model import ProjectModel
from project_save import ProjectSaveService
from project_session import ProjectSession, get_project_session
from saved_letters import SavedLetter


class SavedVersionsDialog(QtWidgets.QDialog):
    def __init__(self, parent, entry, versions):
        super().__init__(parent)
        self.setWindowTitle("Compare Versions — " + entry.title)
        self.resize(860, 620)
        self.versions = versions
        self.revision = None
        layout = QtWidgets.QVBoxLayout(self)
        columns = QtWidgets.QHBoxLayout()
        self.choices, self.panes = [], []
        for side, selected in (("left", 1), ("right", 0)):
            column = QtWidgets.QVBoxLayout()
            choice = QtWidgets.QComboBox()
            choice.setAccessibleName(f"Choose {side} version")
            for index, version in enumerate(versions):
                metadata = version.project.to_dict()["metadata"]
                try:
                    when = datetime.fromisoformat(metadata["saved_at"]).astimezone()
                except (KeyError, TypeError, ValueError):
                    when = datetime.fromtimestamp((version.asset_root / "project.json").stat().st_mtime).astimezone()
                label = when.strftime("%b %d, %Y · %H:%M:%S")
                choice.addItem(f"{label} · {'Current save' if index == 0 else version.revision[:7]}")
            choice.setCurrentIndex(selected)
            choice.currentIndexChanged.connect(self._update_comparison)
            column.addWidget(choice)
            pane = QtWidgets.QScrollArea()
            pane.setWidgetResizable(True)
            pane.setAccessibleName(f"{side.title()} version contents")
            column.addWidget(pane, 1)
            open_button = QtWidgets.QPushButton(f"Open {side} version")
            open_button.clicked.connect(lambda _checked=False, combo=choice: self._open(combo.currentIndex()))
            column.addWidget(open_button)
            self.choices.append(choice)
            self.panes.append(pane)
            columns.addLayout(column, 1)
        layout.addLayout(columns, 1)
        self.differences = QtWidgets.QLabel()
        self.differences.setWordWrap(True)
        layout.addWidget(self.differences)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_comparison()

    def _open(self, index):
        self.revision = self.versions[index].revision
        self.accept()

    @staticmethod
    def _label(text, *, heading=False):
        label = QtWidgets.QLabel(text)
        label.setTextFormat(QtCore.Qt.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        if heading:
            font = label.font()
            font.setBold(True)
            label.setFont(font)
        return label

    def _update_comparison(self):
        from message_format import message_plain_text
        from project_model import PAGE_TYPES
        from project_repository import _safe_file, selected_sound_tracks, sound_track_reference

        left, right = [self.versions[choice.currentIndex()] for choice in self.choices]
        left_data, right_data = (version.project.to_dict() for version in (left, right))
        labels = {"sound": "Soundtrack",
                  "shared_settings": "Shared settings", "presentation": "Presentation", "prompt_settings": "Prompt settings"}
        changed = [label for key, label in labels.items() if left_data[key] != right_data[key]]
        details = [(data["metadata"].get("title"), data["metadata"].get("recipient_title"),
                    data["metadata"].get("recipient_name")) for data in (left_data, right_data)]
        pages = [[{key: value for key, value in page.items() if key not in {"revision", "preview_stale", "id", "recovery"}}
                  for page in data["pages"]] for data in (left_data, right_data)]
        if details[0] != details[1]:
            changed.insert(0, "Letter details")
        if pages[0] != pages[1]:
            changed.insert(0, "Pages or page content")
        self.differences.setText("Differences · " + ", ".join(changed) if changed else
                                 "Same version" if left.revision == right.revision else "Other project details changed")
        for pane, stored, data in zip(self.panes, (left, right), (left_data, right_data)):
            content = QtWidgets.QWidget()
            rows = QtWidgets.QVBoxLayout(content)
            metadata = data["metadata"]
            rows.addWidget(self._label(str(metadata.get("title") or metadata.get("recipient_title") or "Untitled letter"), heading=True))
            if metadata.get("recipient_name"):
                rows.addWidget(self._label(f"For {metadata['recipient_name']}"))
            tracks = [str(track.get("display_title") or track.get("original_name") or Path(sound_track_reference(track)).name
                          or track.get("track_id", "Unavailable track")) for track in selected_sound_tracks(data["sound"])]
            rows.addWidget(self._label("Soundtrack · " + (", ".join(tracks) or "None")))
            for position, page in enumerate(data["pages"], 1):
                rows.addWidget(self._label(f"{position}. {page['name'] or PAGE_TYPES[page['type']].label}", heading=True))
                if asset := page["artwork"].get("asset"):
                    reader = QtGui.QImageReader(str(_safe_file(stored.asset_root, asset)))
                    size = reader.size()
                    if size.isValid():
                        size.scale(152, 120, QtCore.Qt.KeepAspectRatio)
                        reader.setScaledSize(size)
                    image = reader.read()
                    if not image.isNull():
                        artwork = QtWidgets.QLabel()
                        artwork.setAccessibleName(f"Page {position} artwork")
                        artwork.setPixmap(QtGui.QPixmap.fromImage(image))
                        rows.addWidget(artwork)
                if page["message"] is not None:
                    rows.addWidget(self._label(message_plain_text(page["message"]["html"]).strip() or "Empty message"))
                if prompt := page["prompt"].get("text"):
                    rows.addWidget(self._label("Artwork prompt\n" + prompt))
            rows.addStretch(1)
            pane.setWidget(content)


class SavedProjectsController(QtCore.QObject):
    changed = QtCore.Signal()
    revisions_loaded = QtCore.Signal(object, object, object)

    def __init__(self, forge):
        super().__init__(forge)
        self.forge = forge
        self.service = getattr(forge, "project_save_service", None) or getattr(forge.window(), "project_save_service", None) or ProjectSaveService(forge.project_root, forge.project_state)
        self.previews = []
        self.github_library = None
        layout = forge.saved_panel.layout()
        self.history_panel = QtWidgets.QWidget()
        history_layout = QtWidgets.QVBoxLayout(self.history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(4)
        self.history_title = QtWidgets.QLabel()
        self.history_title.setTextFormat(QtCore.Qt.PlainText)
        history_layout.addWidget(self.history_title)
        self.history_list = QtWidgets.QTreeWidget()
        self.history_list.setHeaderLabels(("Date", "Changes", "Status"))
        self.history_list.setAccessibleName("Saved letter revisions")
        self.history_list.setRootIsDecorated(False)
        self.history_list.setMaximumHeight(126)
        self.history_list.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.history_list.resizeColumnToContents(0)
        history_layout.addWidget(self.history_list)
        self.history_backup = QtWidgets.QLabel()
        self.history_backup.setTextFormat(QtCore.Qt.PlainText)
        self.history_backup.setWordWrap(True)
        history_layout.addWidget(self.history_backup)
        self.history_live = QtWidgets.QLineEdit()
        self.history_live.setReadOnly(True)
        self.history_live.setAccessibleName("Verified live letter link")
        history_layout.addWidget(self.history_live)
        layout.addWidget(self.history_panel)
        self.history_panel.hide()
        self._history_key = None
        self._history_pool = QtCore.QThreadPool(self)
        self._history_pool.setMaxThreadCount(1)
        self.revisions_loaded.connect(self._show_revision_history, QtCore.Qt.QueuedConnection)
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
        self.restore_button = QtWidgets.QPushButton("Restore")
        self.restore_button.clicked.connect(self.restore_selected)
        row.addWidget(self.restore_button)
        self.compare_button = QtWidgets.QPushButton("Compare Versions")
        self.compare_button.clicked.connect(self.compare_selected)
        row.addWidget(self.compare_button)
        self.template_button = QtWidgets.QPushButton("Use as Template")
        self.template_button.clicked.connect(self.use_stock_template)
        row.addWidget(self.template_button)
        self.menu = QtWidgets.QMenu(forge.saved_panel)
        self.preview_action = self.menu.addAction("Preview Saved Version", self.preview_selected)
        self.compare_action = self.menu.addAction("Compare Versions", self.compare_selected)
        self.template_action = self.menu.addAction("Use as Template", self.use_stock_template)
        self.duplicate_action = self.menu.addAction("Duplicate Saved Version", self.duplicate_selected)
        self.rename_action = self.menu.addAction("Rename Saved Project", self.rename_selected)
        self.restore_action = self.menu.addAction("Restore Deleted Letter", self.restore_selected)
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
        deleted = valid and entry.deleted
        self.open_button.setVisible(not deleted)
        self.open_button.setEnabled(valid)
        self.restore_button.setVisible(deleted)
        self.restore_button.setEnabled(deleted and not self.forge._busy)
        self.restore_action.setVisible(deleted)
        self.restore_action.setEnabled(self.restore_button.isEnabled())
        stock = self.forge._saved_panel_mode == "stock"
        self.template_button.setVisible(stock)
        self.template_action.setVisible(stock)
        self.template_button.setEnabled(valid and entry.stock and not self.forge._busy)
        self.template_action.setEnabled(self.template_button.isEnabled())
        can_compare = valid and entry.canonical and not (entry.stock or entry.example or entry.recovery)
        self.compare_button.setVisible(not stock)
        self.compare_action.setVisible(not stock)
        self.compare_button.setEnabled(can_compare and not self.forge._busy)
        self.compare_action.setEnabled(self.compare_button.isEnabled())
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
        self.refresh_revision_history()

    def refresh_revision_history(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter) or not entry.valid or entry.stock or entry.example or not self.forge.saved_panel.isVisible():
            self._history_key = None
            self.history_panel.hide()
            self._history_pool.clear()
            return
        from saved_letter_versions import letter_state
        row = self.github_library.rows.get(entry.project_id)
        backup = row[0].backup if row else None
        receipt = letter_state(self.forge.project_root, entry.project_id, "backup")
        try:
            history_stamp = (entry.path / ".lettersmith-project/current.json").stat().st_mtime_ns
        except OSError:
            history_stamp = None
        key = (entry, history_stamp, getattr(backup, "marker_sha", ""), receipt.get("timestamp"))
        if key == self._history_key:
            return
        self._history_key = key
        self._history_pool.clear()
        self.history_title.setText(entry.title)
        self.history_list.clear()
        self.history_list.setVisible(entry.canonical)
        self.history_backup.hide()
        url = entry.published_url if entry.publication_verified and entry.published else ""
        self.history_live.setText(url)
        self.history_live.setToolTip(url)
        self.history_live.setVisible(bool(url))
        self.history_panel.setVisible(bool(entry.canonical or url or backup or receipt))
        if entry.canonical:
            self.history_list.addTopLevelItem(QtWidgets.QTreeWidgetItem(("Reading revisions…",)))
        root, service, completed = self.forge.project_root, self.service, self.revisions_loaded

        def load():
            from saved_letter_versions import summarize_revisions
            try:
                versions = service.saved_revisions(entry) if entry.canonical else ()
                summaries, recorded_backup = summarize_revisions(root, entry, versions, backup)
                completed.emit(key, summaries, recorded_backup)
            except (OSError, ValueError, RuntimeError, KeyError):
                completed.emit(key, None, {})

        self._history_pool.start(load)

    def _show_revision_history(self, key, rows, backup):
        if key != self._history_key or not self.forge.saved_panel.isVisible():
            return
        from saved_letter_versions import saved_date
        entry = key[0]
        self.history_list.clear()
        live_count = sum(item.live for item in rows or ())
        for item in rows or ():
            labels = []
            if item.revision == entry.storage_revision:
                labels.append("Current save")
            if item.live:
                labels.append("Live" if live_count == 1 else "Matches live presentation")
            if item.backed_up:
                labels.append("GitHub backup")
            when = item.date.astimezone().strftime("%b %d, %Y %H:%M:%S")
            row = QtWidgets.QTreeWidgetItem((when, item.changes, " · ".join(labels)))
            row.setToolTip(0, f"Revision {item.revision[:8]}")
            row.setToolTip(1, item.changes)
            self.history_list.addTopLevelItem(row)
        if rows is None:
            self.history_list.addTopLevelItem(QtWidgets.QTreeWidgetItem(("Revisions unavailable",)))
        self.history_list.setVisible(bool(rows) or rows is None)
        self.history_list.resizeColumnToContents(0)
        backup_date = saved_date(backup.get("timestamp"), None)
        message = ""
        if backup_date:
            match = next((item for item in rows or () if item.backed_up), None)
            key = match.revision if match else backup.get("revision_key") or backup.get("digest", "")
            message = "GitHub backup · " + backup_date.astimezone().strftime("%b %d, %Y %H:%M:%S")
            if key:
                message += f" · Revision {key[:8]}"
        if self.history_live.text() and not live_count and entry.published_source_fingerprint:
            message = "\n".join(filter(None, (message, f"Live revision · {entry.published_source_fingerprint[:8]}")))
        self.history_backup.setText(message)
        self.history_backup.setVisible(bool(message))

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
        if isinstance(entry, SavedLetter) and entry.deleted:
            return self.restore_selected()
        return self._open_entry(entry)

    def use_stock_template(self):
        entry = self.forge._selected_saved_letter
        if isinstance(entry, SavedLetter) and entry.stock:
            return self._open_entry(entry, as_template=True)
        return False

    def compare_selected(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter) or not entry.valid or not entry.canonical:
            return False

        def show(versions):
            if len(versions) < 2:
                self._status("This letter has one distinct saved version.")
                return
            dialog = SavedVersionsDialog(self.forge.window(), entry, versions)
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                self._open_entry(entry, revision=dialog.revision)
            dialog.deleteLater()

        return self._run("Reading saved versions…", lambda: self.service.saved_revisions(entry),
                         lambda versions: self.forge._defer_until_idle(lambda: show(versions)))

    def _open_entry(self, entry, *, revision=None, as_template=False):
        if not isinstance(entry, SavedLetter) or not entry.valid or self.forge._busy:
            return False
        lifecycle = getattr(self.forge.window(), "project_lifecycle", None)
        options = {"revision": revision, "as_template": as_template} if revision or as_template else {}
        if lifecycle is not None:
            return lifecycle.open_saved(entry, **options)
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
                return self.save(after=lambda: self._read_and_open(entry, **options))
        return self._read_and_open(entry, **options)

    def _read_and_open(self, entry, *, finished=None, revision=None, as_template=False):
        origin, origin_revision = self.session.session_id, self.session.project.revision

        def loaded(candidate):
            if not self._flush() or (origin, origin_revision) != (self.session.session_id, self.session.project.revision):
                self._status("The open project changed while loading. Its edits were kept; open the saved project again.", error=True)
                if finished is not None:
                    finished(False)
                return
            self.forge._begin_restore_activity("Opening project…")
            try:
                if not self.forge._release_project_files_for_restore():
                    return
                if not self._flush() or (origin, origin_revision) != (self.session.session_id, self.session.project.revision):
                    raise RuntimeError("New edits arrived during the transition; retry opening")
                stored = self.service.open_saved_project(entry, loaded=candidate, revision=revision, as_template=as_template)
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
                self._status("Stock template opened." if as_template else "Project opened.")
            except Exception as error:
                retained = ("The previous project is retained." if origin == self.session.session_id else
                            "The activated project is recoverable; reopen the application to restore its views.")
                self._status(f"Open failed: {error}. {retained}", error=True)
            finally:
                self.forge._finish_restore_activity()
                if finished is not None:
                    finished()

        return self._run("Reading saved project…", lambda: self.service.read_saved_project(entry, revision=revision), loaded, failed=finished)

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
        from project_paths import ProjectPathError
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter):
            return
        dialog = QtWidgets.QInputDialog(self.forge)
        dialog.setWindowTitle("Rename Saved Project")
        dialog.setLabelText("Display title")
        dialog.setTextValue(entry.title)

        def warn(value):
            try:
                conflict = self.service.resolver.find_title_conflict_for_name(entry.recipient, value, project_id=entry.project_id)
            except (OSError, ValueError, ProjectPathError):
                conflict = None
            dialog.setLabelText("Display title\nThis recipient already has a letter with that title." if conflict else "Display title")

        dialog.textValueChanged.connect(warn)
        if dialog.exec() != QtWidgets.QDialog.Accepted or (title := dialog.textValue()) == entry.title:
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
            self._status("Saved project moved to Recovery. Select Restore to return it to the Saved List.")
        return self._run("Deleting saved project…", lambda: self.forge.catalog.delete(entry), deleted)

    def restore_selected(self):
        entry = self.forge._selected_saved_letter
        if not isinstance(entry, SavedLetter) or not entry.valid or not entry.deleted:
            return False

        def restored(destination):
            catalog = self.forge.catalog
            catalog.refresh_entry(entry.path)
            catalog.refresh_entry(destination)
            self.forge._selected_saved_letter = catalog._entry(destination)
            self.forge.refresh_saved_letters(force_reconcile=True)
            self._status("Letter restored to the Saved List.")

        return self._run("Restoring deleted letter…", lambda: self.service.restore_deleted_project(entry), restored)

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
        dialog.resize(760, 440)
        layout = QtWidgets.QVBoxLayout(dialog)
        source = QtWidgets.QComboBox()
        destination = QtWidgets.QComboBox()
        source.setAccessibleName("Recovered content")
        destination.setAccessibleName("Destination page")
        for item in items:
            source.addItem(f'{item["kind"].title()}: {item["key"] or "Unassigned legacy message"}')
        layout.addWidget(source)
        layout.addWidget(destination)
        comparison = QtWidgets.QHBoxLayout()
        panes = []
        for label in ("Recovered text", "Current destination text"):
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(label))
            preview = QtWidgets.QPlainTextEdit()
            preview.setAccessibleName(label)
            preview.setReadOnly(True)
            column.addWidget(preview)
            panes.append(preview)
            comparison.addLayout(column, 1)
        layout.addLayout(comparison, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        apply = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        apply.setText("Apply Recovery")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        pages = {page["id"]: page for page in self.session.project.pages}

        def content(kind, value):
            if kind == "message":
                from message_format import message_plain_text
                return message_plain_text(value.get("html", "") if isinstance(value, dict) else str(value or ""))
            return str((value.get("text") if isinstance(value, dict) else value) or "")

        def show_destination():
            page = pages.get(destination.currentData())
            kind = items[source.currentIndex()]["kind"]
            panes[1].setPlainText(content(kind, page[kind]) or "Empty" if page else "")
            apply.setEnabled(page is not None)

        def selected(index):
            item = items[index]
            panes[0].setPlainText(content(item["kind"], item["value"]))
            with QtCore.QSignalBlocker(destination):
                destination.clear()
                destination.addItem("Choose a destination page…", None)
                for number, page in enumerate(pages.values(), 1):
                    if item["kind"] == "prompt" or page["type"] == "letter":
                        destination.addItem(f'{number}. {page["name"] or page["type"]}', page["id"])
            show_destination()
        source.currentIndexChanged.connect(selected)
        destination.currentIndexChanged.connect(show_destination)
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
        self._history_pool.clear()
        return all(preview.release() for _dialog, preview, _session, _temporary in tuple(self.previews))

    def shutdown(self):
        self._history_key = None
        self._history_pool.clear()
        if not self._history_pool.waitForDone(5000):
            return False
        for dialog, preview, session, temporary in tuple(self.previews):
            if not preview.shutdown():
                return False
            session.close()
            dialog.close()
            temporary.cleanup()
        self.previews.clear()
        self._observed_session.remove_listener(self._session_listener)
        return True
