"""GitHub entries and actions within the existing Saved Letters library."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from library_status import badge_style
from project_paths import application_paths
from project_repository import ProjectRepository
from publishing.github_library import GitHubLibrary, LibraryProject, downloaded_presentations, project_digest
from saved_letters import SavedLetterCatalog


class GitHubLetterCard(QtWidgets.QFrame):
    """A remote entry in the normal grid, without a fabricated local path."""
    selected = QtCore.Signal(str)
    activated = QtCore.Signal(str)

    def __init__(self, project, location, kind, version, parent):
        super().__init__(parent)
        self.project = project
        self.key = project.project_id
        self.setFixedSize(184, 260)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setObjectName("GitHubLetterCard")
        self.setStyleSheet(
            "QFrame#GitHubLetterCard{background:#111b23;border:1px solid #345160;border-radius:7px;}"
            "QFrame#GitHubLetterCard:hover{border-color:#528ed0;}"
            "QFrame#GitHubLetterCard[selected=\"true\"]{border:2px solid #528ed0;}"
            "QLabel{color:#aac0ca;border:none;background:transparent;font:8pt 'Segoe UI';}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(3)
        cover = QtWidgets.QLabel("GitHub")
        cover.setAlignment(QtCore.Qt.AlignCenter)
        cover.setFixedHeight(96)
        cover.setStyleSheet("background:#142d50;color:#b7d9ff;border-radius:4px;font:600 14pt 'Segoe UI';")
        cover.setProperty("themeIndependent", True)
        layout.addWidget(cover)
        title = QtWidgets.QLabel(project.title)
        title.setWordWrap(True)
        title.setMaximumHeight(34)
        title.setStyleSheet("color:#f0fbff;font:600 9pt 'Segoe UI';")
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel("Editable backup" if project.backup else "Presentation only"))
        layout.addWidget(QtWidgets.QLabel(project.recipient))
        self.location_label = QtWidgets.QLabel(location)
        self.publication_label = QtWidgets.QLabel("Published" if project.presentation else "Publication not found")
        for label, color in ((self.location_label, kind),
                             (self.publication_label, "published" if project.presentation else "draft")):
            label.setFixedHeight(20)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setProperty("themeIndependent", True)
            label.setStyleSheet(badge_style(color))
            layout.addWidget(label)
        layout.addWidget(QtWidgets.QLabel(version if version != "Not compared" else ""))
        for label in self.findChildren(QtWidgets.QLabel):
            label.setTextFormat(QtCore.Qt.PlainText)
            label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setToolTip(project.title + "\n" + location + "\nDouble-click to download.")

    def set_selected(self, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.selected.emit(self.key)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.activated.emit(self.key)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.activated.emit(self.key)
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_Space:
            self.selected.emit(self.key)
            event.accept()
            return
        super().keyPressEvent(event)


class GitHubLibraryController(QtCore.QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.forge = controller.forge
        self.rows = {}
        self.downloads = {}
        self.cards = []
        self.selected_id = ""
        self.account = None
        self.working = False
        self.download_root = application_paths(self.forge.project_root).saved_letters_root / "GitHub Downloads"
        self.refresh_button = QtWidgets.QPushButton("Refresh GitHub")
        self.refresh_button.clicked.connect(self.refresh)
        controller.row.insertWidget(2, self.refresh_button)
        self.status = QtWidgets.QLabel()
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(45)
        self.forge.saved_panel.layout().insertWidget(
            self.forge.saved_panel.layout().indexOf(controller.details), self.status)
        controller.menu.addSeparator()
        self.download_project_action = controller.menu.addAction("Download Separate Editable Copy", self.download_project)
        self.download_presentation_action = controller.menu.addAction("Download Published Presentation", self.download_presentation)
        self.open_action = controller.menu.addAction("Open Published Letter", self.open_online)
        self.backup_action = controller.menu.addAction("Back Up Open Project to GitHub", self.backup_current)
        controller.menu.addAction("GitHub Account", self.forge.show_github_account)
        self.forge._github_state_changed.connect(self.account_changed)
        self.update_selection()

    def _session(self):
        return self.forge._github_service.snapshot.session

    def account_changed(self, snapshot):
        if snapshot.account is None or snapshot.account.account_id != self.account:
            self.rows.clear()
            self.selected_id = ""
            self.account = None
            self.sync_cards()
            self.status.setText("Connect GitHub to see online letters here." if snapshot.account is None
                                else "Select Refresh GitHub to load this account's letters.")
            if snapshot.account is not None and self.forge.saved_panel.isVisible() and not self.working:
                self.forge._defer_until_idle(self.refresh)

    def selected(self):
        session = self._session()
        if session is None or session.account.account_id != self.account:
            return None
        local = self.forge._selected_saved_letter
        return self.rows.get(local.project_id if local else self.selected_id)

    def select(self, project_id):
        self.selected_id = project_id
        self.forge._selected_saved_letter = None
        for card in self.forge._saved_cards:
            card.set_selected(False)
        self.controller.selected_changed()

    def activate(self, project_id):
        self.select(project_id)
        self.download_selected()

    def download_selected(self):
        downloaded = self.downloads.get(self.selected_id)
        row = self.selected()
        if not downloaded and row and not row[0].backup:
            downloaded = self._download_for(row[0])
        if downloaded:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(downloaded[1] / "index.html")))
            return
        if row:
            return self.download_project() if row[0].backup else self.download_presentation()

    def _download_for(self, project):
        session = self._session()
        return next((value for value in self.downloads.values()
                     if session and project.presentation and value[0].project_id == project.project_id
                     and value[0].marker_sha == project.presentation.marker_sha
                     and value[2].casefold() == session.account.login.casefold()), None)

    def _run(self, activity, operation, success):
        session = self._session()
        if session is None:
            self.status.setText("Connect GitHub through Project Actions, then select Refresh GitHub.")
            return False
        if self.working or self.forge._busy:
            return False
        account = session.account.account_id
        generation = self.forge._github_service.snapshot.generation
        self.account, self.working = account, True
        self.status.setStyleSheet("")
        self.status.setText(activity)
        self.update_selection()

        def cancelled():
            current = self.forge._github_service.snapshot
            return (QtCore.QThread.currentThread().isInterruptionRequested()
                    or current.session is None or current.session.account.account_id != account
                    or current.generation != generation)

        def work():
            library = GitHubLibrary(session, self.forge._github_service.authorized_api(session), cancelled=cancelled)
            return operation(library)

        def finished(value):
            self.working = False
            if cancelled():
                self.account_changed(self.forge._github_service.snapshot)
            else:
                success(value)
            self.update_selection()

        def failed(_value):
            self.working = False
            if cancelled():
                self.account_changed(self.forge._github_service.snapshot)
            self.status.setText(self.controller.details.text())
            self.status.setStyleSheet(badge_style("error"))
            self.update_selection()

        return self.controller._run(activity, work, finished, failed=failed)

    def refresh(self):
        if self.working or self.forge._saved_panel_mode == "stock":
            return
        if self.forge._busy:
            self.forge._defer_until_idle(self.refresh)
            return
        session = self._session()
        if session is None:
            self.account_changed(self.forge._github_service.snapshot)
            return

        def load(library):
            projects, warnings = library.discover()
            local = SavedLetterCatalog(self.forge.project_root).list_entries(force_refresh=True)
            downloads = downloaded_presentations(self.download_root)
            by_id = {}
            for entry in local:
                by_id.setdefault(entry.project_id, []).append(entry)
            rows = []
            for project in projects:
                copies = by_id.get(project.project_id, [])
                location, kind = ("Local + GitHub", "local") if copies else ("GitHub only", "github")
                version, version_kind = "Not compared", "draft"
                if any(not entry.valid for entry in copies):
                    version, version_kind = "Files damaged", "error"
                elif project.backup and any(entry.canonical for entry in copies):
                    try:
                        digests = {project_digest(ProjectRepository(entry.path / ".lettersmith-project").load())
                                   for entry in copies if entry.canonical}
                        version, version_kind = (("Matching backup", "local") if digests == {project.backup.digest}
                                                 else ("Compare versions", "different"))
                    except Exception:
                        version, version_kind = "Files damaged", "error"
                elif project.presentation and copies:
                    fingerprints = {entry.published_source_fingerprint for entry in copies}
                    if project.presentation.fingerprint and (project.presentation.fingerprint not in fingerprints
                            or any("not published" in entry.saved_publication_state for entry in copies)):
                        version, version_kind = "Compare versions", "different"
                if project.presentation:
                    path = library.presentation_path(project.presentation, self.download_root)
                    previous = [item for item in downloads.values()
                                if item[0].project_id == project.project_id
                                and item[2].casefold() == library.owner.casefold()]
                    if previous and not path.exists():
                        location, kind = "Local + GitHub", "local"
                        version, version_kind = "Compare versions", "different"
                    if path.exists():
                        try:
                            library.download_presentation(project.presentation, self.download_root)
                            location, kind = "Local + GitHub", "local"
                        except (OSError, ValueError):
                            location, kind = "Download damaged", "error"
                rows.append((project, location, kind, version, version_kind, copies))
            return rows, warnings

        def loaded(result):
            rows, warnings = result
            self.rows = {row[0].project_id: row for row in rows}
            count = f"{len(rows)} letter" + ("" if len(rows) == 1 else "s")
            self.status.setText(f"GitHub · {session.account.login} · {count}" +
                                (" · Some letters could not be checked. See details." if warnings else ""))
            self.status.setToolTip("\n".join(warnings))
            if warnings:
                self.controller.details.setText("\n".join(warnings[:3]))
            self.sync_cards()

        self._run("Checking GitHub letters…", load, loaded)

    def sync_cards(self):
        for card in self.cards:
            self.forge.saved_cards_layout.removeWidget(card)
            card.deleteLater()
        self.cards = []
        self.downloads = downloaded_presentations(self.download_root)
        if self.forge._saved_panel_mode != "stock":
            local_ids = {entry.project_id for entry in self.forge._rendered_catalog_entries}
            for project_id, row in self.rows.items():
                if project_id in local_ids:
                    continue
                card = GitHubLetterCard(*row[:4], self.forge.saved_cards_widget)
                card.selected.connect(self.select)
                card.activated.connect(self.activate)
                self.cards.append(card)
                card.show()
            session = self._session()
            for key, (copy, path, owner) in self.downloads.items():
                online = self.rows.get(copy.project_id)
                if (online and session and owner.casefold() == session.account.login.casefold()
                        and online[0].presentation):
                    continue
                available = (path / "index.html").is_file()
                card = GitHubLetterCard(LibraryProject(copy.project_id, presentation=copy),
                    "Local" if available else "Files damaged", "local" if available else "error",
                    "Downloaded presentation", self.forge.saved_cards_widget)
                card.key = key
                card.selected.connect(self.select)
                card.activated.connect(self.activate)
                self.cards.append(card)
                card.show()
            service = getattr(self.forge.window(), "theme_service", None)
            if service is not None:
                for card in self.cards:
                    service.apply_semantic_styles(card)
        self.forge._layout_saved_cards()
        self.update_selection()

    def update_selection(self):
        local = self.forge._selected_saved_letter
        if local is not None:
            self.selected_id = ""
        row = self.selected()
        ready = row is not None and not self.working and not self.forge._busy
        self.refresh_button.setEnabled(not self.working and not self.forge._busy)
        self.download_project_action.setEnabled(bool(ready and row[0].backup))
        self.download_presentation_action.setEnabled(bool(ready and row[0].presentation))
        self.open_action.setEnabled(bool(ready and row[0].presentation))
        self.backup_action.setEnabled(bool(self._session() and not self.working and not self.forge.is_protected_project()))
        self.controller.open_button.setText("Open Project")
        if local is None:
            self.controller.open_button.setEnabled(False)
        if local is None and row:
            self.controller.open_button.setText("Download Project" if row[0].backup else "Download Presentation")
            self.controller.open_button.setEnabled(ready)
            project = row[0]
            self.controller.details.setText(
                f"{project.title}\n{row[1]} · {row[3]}\n" +
                (f"Editable backup: {project.backup.timestamp}" if project.backup else
                 "Presentation only. Back up the original editable project on its source computer to edit it here."))
        if row and row[3] == "Compare versions":
            project = row[0]
            dates = [entry.modified_at.astimezone().strftime("%Y-%m-%d %H:%M") for entry in row[5]]
            dates.extend(copy.timestamp for copy, _path, _owner in self.downloads.values()
                         if copy.project_id == project.project_id)
            remote_date = (project.backup or project.presentation).timestamp
            self.controller.details.setText(self.controller.details.text().split("\nLocal: ", 1)[0] +
                f"\nLocal: {', '.join(dates)} · GitHub: {remote_date}. Downloads keep both versions.")
        downloaded = self.downloads.get(self.selected_id)
        if not downloaded and row and not row[0].backup:
            downloaded = self._download_for(row[0])
        if local is None and downloaded:
            self.controller.open_button.setText("Open Downloaded Letter")
            self.controller.open_button.setEnabled(not self.working and not self.forge._busy
                                                    and (downloaded[1] / "index.html").is_file())
            self.controller.details.setText(f"{downloaded[0].title}\nDownloaded presentation · Available offline\n{downloaded[1]}")
        for card in self.cards:
            card.set_selected(card.key == self.selected_id)
            card.setEnabled(not self.working)
        for card in self.forge._saved_cards:
            remote = self.rows.get(card.entry.project_id)
            card.update_entry(card.entry)
            if remote:
                card.status_label.setText("Saved draft · GitHub" if card.entry.canonical else "Local + GitHub")
                if remote[0].presentation:
                    card.publication_label.setText("Published")
                    card.publication_label.setStyleSheet(badge_style("published"))
                if remote[3] != "Not compared":
                    card.state_label.setText(remote[3])
                    card.state_label.setStyleSheet(badge_style(remote[4]))
            else:
                card.state_label.setStyleSheet("")
                card.state_label.setText("Currently open" if card.property("currentProject")
                                         else "Selected" if card.entry == local else "")

    def download_project(self):
        row = self.selected()
        if not row or not row[0].backup or self.working:
            return
        project = row[0]
        def completed(path):
            self.forge._refresh_catalog_entry(path)
            self.controller._status("Editable project downloaded. Select its card to open it.")
            self.status.setText("Editable project downloaded; your open project is unchanged.")
            self.selected_id = ""
            self.forge._selected_saved_letter = self.forge.catalog._entry(path)
            self.controller.selected_changed()
            self.forge._defer_until_idle(self.refresh)
        self._run("Downloading editable project…",
            lambda library: library.download_project(project.backup, self.controller.service.editable_root, as_copy=bool(row[5])), completed)

    def download_presentation(self):
        row = self.selected()
        if not row or not row[0].presentation or self.working:
            return
        def completed(path):
            self.status.setText(f"Presentation downloaded to {path}")
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
            self.forge._defer_until_idle(self.refresh)
        self._run("Downloading presentation…",
            lambda library: library.download_presentation(row[0].presentation, self.download_root), completed)

    def open_online(self):
        row = self.selected()
        if row and row[0].presentation and not self.working:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(row[0].presentation.url))

    def backup_current(self):
        if self.working or self.forge._busy or self.forge.is_protected_project() or not self.controller._flush():
            return
        try:
            capture = self.controller.service.capture_editable_save()
        except Exception as error:
            self.status.setText(str(error))
            return
        row = self.rows.get(capture.project.project_id)
        remote = row[0].backup if row else None
        if remote:
            choice = QtWidgets.QMessageBox.question(self.forge.saved_panel, "Replace GitHub Backup",
                f"Replace the editable GitHub backup from {remote.timestamp} with the open project? "
                "Other computers may have newer edits. Local projects and the published letter stay unchanged.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if choice != QtWidgets.QMessageBox.Yes:
                return
        def completed(_project_id):
            self.status.setText("Editable project backed up privately to GitHub.")
            self.forge._defer_until_idle(self.refresh)
        self._run("Backing up editable project…",
            lambda library: library.backup_project(capture, expected_marker=remote.marker_sha if remote else ""), completed)
