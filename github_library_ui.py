"""GitHub entries and actions within the existing Saved Letters library."""
from __future__ import annotations

from datetime import datetime
import logging
from threading import Event
from time import monotonic

from PySide6 import QtCore, QtGui, QtWidgets

from library_status import badge_style, set_card_status, paint_card_status
from project_paths import application_paths
from publishing.github_auth import GitHubConnectionState, GitHubOperationError
from publishing.destinations import DestinationStore
from publishing.github_library import GitHubLibrary, LibraryProject, downloaded_presentations

_LOGGER = logging.getLogger(__name__)


class GitHubLetterCard(QtWidgets.QFrame):
    """A remote entry in the normal grid, without a fabricated local path."""
    selected = QtCore.Signal(str)
    activated = QtCore.Signal(str)

    def __init__(self, project, location, kind, version, parent):
        super().__init__(parent)
        self.project = project
        self.key = project.project_id
        self.setFixedSize(208, 276)
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
        self.cover = QtWidgets.QLabel("No cover")
        self.cover.setAlignment(QtCore.Qt.AlignCenter)
        self.cover.setFixedHeight(196)
        self.cover.setStyleSheet("background:#091116;color:#78909a;border-radius:4px;")
        layout.addWidget(self.cover)
        title = QtWidgets.QLabel()
        title.setText(title.fontMetrics().elidedText(project.title, QtCore.Qt.ElideRight, 192))
        title.setFixedHeight(20)
        title.setStyleSheet("color:#f0fbff;font:600 9pt 'Segoe UI';")
        layout.addWidget(title)
        recipient = QtWidgets.QLabel()
        recipient.setText(recipient.fontMetrics().elidedText(project.recipient, QtCore.Qt.ElideRight, 192))
        recipient.setFixedHeight(18)
        layout.addWidget(recipient)
        timestamp = (project.backup or project.presentation).timestamp
        try:
            timestamp = datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %Y")
        except ValueError:
            timestamp = timestamp[:10]
        date = QtWidgets.QLabel(timestamp)
        date.setFixedHeight(14)
        layout.addWidget(date)
        for label in self.findChildren(QtWidgets.QLabel):
            label.setTextFormat(QtCore.Qt.PlainText)
            label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        set_card_status(self, (location, "both" if location == "Local + GitHub" else kind),
                        ("Published", "published") if project.presentation else ("Unpublished", "draft"))
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

    def paintEvent(self, event):
        super().paintEvent(event)
        paint_card_status(self)

    def set_cover(self, path):
        from pathlib import Path
        if path and Path(path).is_file():
            image = QtGui.QImageReader(str(path))
            size = image.size()
            if size.isValid():
                image.setScaledSize(size.scaled(192, 192, QtCore.Qt.KeepAspectRatio))
            pixmap = QtGui.QPixmap.fromImage(image.read())
            if not pixmap.isNull():
                self.cover.setPixmap(pixmap)

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
    _listing_ready = QtCore.Signal(int, object)
    _cover_ready = QtCore.Signal(int, str, object)
    REFRESH_TIMEOUT_MS = 60000

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
        self.covers = {}
        self._refresh_thread = None
        self._refresh_worker = None
        self._refresh_cancel = Event()
        self._refresh_id = 0
        self._retry_pending = False
        self._refresh_deferred = False
        self._awaiting_auth = False
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_timed_out)
        self._listing_ready.connect(self._show_listing, QtCore.Qt.QueuedConnection)
        self._cover_ready.connect(self._show_cover, QtCore.Qt.QueuedConnection)
        self.download_root = application_paths(self.forge.project_root).saved_letters_root / "GitHub Downloads"
        self.status = controller.details
        self.retry_button = QtWidgets.QPushButton("Retry GitHub", self.forge.saved_panel)
        self.retry_button.clicked.connect(self.retry)
        controller.row.insertWidget(1, self.retry_button)
        self.retry_button.hide()
        controller.menu.addSeparator()
        self.open_action = controller.menu.addAction("Open Published Letter", self.open_online)
        self.backup_action = controller.menu.addAction("Back Up Open Project to GitHub", self.backup_current)
        controller.menu.addAction("GitHub Account", self.forge.show_github_account)
        self.forge._github_state_changed.connect(self.account_changed)
        self.update_selection()

    def _session(self):
        snapshot = self.forge._github_service.snapshot
        return snapshot.session if snapshot.state == GitHubConnectionState.CONNECTED else None

    def account_changed(self, snapshot):
        if snapshot.account is None or snapshot.account.account_id != self.account:
            self._refresh_cancel.set()
            self.rows.clear()
            self.selected_id = ""
            self.account = None
            self.sync_cards()
            if snapshot.state not in {GitHubConnectionState.CONNECTED, GitHubConnectionState.CONNECTING, GitHubConnectionState.RECONNECTING}:
                self._refresh_timer.stop()
                self._refresh_error(snapshot.message or "Sign in using GitHub Account to load your online letters.")
        if self._awaiting_auth and snapshot.state == GitHubConnectionState.CONNECTED:
            self._awaiting_auth = False
            self.refresh()
        elif self._awaiting_auth and snapshot.state not in {
            GitHubConnectionState.CONNECTING, GitHubConnectionState.RECONNECTING,
        }:
            self._refresh_timer.stop()
            self._awaiting_auth = False
            self._refresh_error(snapshot.message or "Sign in using GitHub Account, then retry.")
        elif snapshot.account is not None and snapshot.account.account_id != self.account and self.forge.saved_panel.isVisible():
            self.refresh()

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

    def _run(self, activity, operation, success, *, quiet=False):
        session = self._session()
        if session is None:
            self.controller._status("Connect GitHub to open this letter.", error=True)
            return False
        if self.working or self.forge._busy:
            return False
        self._refresh_cancel.set()
        account = session.account.account_id
        generation = self.forge._github_service.snapshot.generation
        self.account, self.working = account, True
        self.status.setStyleSheet("")
        self.status.hide()
        self.retry_button.hide()
        if not quiet:
            self.status.setText(activity)
            self.status.show()
        self.update_selection()

        def cancelled():
            current = self.forge._github_service.snapshot
            return (QtCore.QThread.currentThread().isInterruptionRequested()
                    or current.session is None or current.session.account.account_id != account
                    or current.generation != generation)

        def work():
            library = GitHubLibrary(session, self.forge._github_service.authorized_api(session), cancelled=cancelled,
                                    store=DestinationStore(self.forge.project_root))
            return operation(library)

        def finished(value):
            self.working = False
            self.status.hide()
            if cancelled():
                self.account_changed(self.forge._github_service.snapshot)
            else:
                success(value)
            self.update_selection()

        def failed(_value):
            self.working = False
            if cancelled():
                self.account_changed(self.forge._github_service.snapshot)
            self.status.show()
            self.status.setStyleSheet(badge_style("error"))
            self.update_selection()

        return self.controller._run(activity, work, finished, failed=failed, quiet=quiet)

    def refresh(self):
        if self.working or self.forge._shutdown or self.forge._saved_panel_mode == "stock":
            return
        if self._refresh_thread is not None:
            if self._refresh_cancel.is_set():
                self._retry_pending = True
            return
        if self.forge._busy:
            if not self._refresh_deferred:
                self._refresh_deferred = True
                def later():
                    self._refresh_deferred = False
                    self.refresh()
                self.forge._defer_until_idle(later)
            return
        session = self._session()
        if session is None:
            snapshot = self.forge._github_service.snapshot
            if snapshot.state in {GitHubConnectionState.CONNECTING, GitHubConnectionState.RECONNECTING}:
                self._awaiting_auth = True
                self.status.setText("Connecting to GitHub… Local letters remain available.")
                self.status.show()
                if not self._refresh_timer.isActive():
                    self._refresh_timer.start(self.REFRESH_TIMEOUT_MS)
            else:
                self._refresh_error(snapshot.message or "Sign in using GitHub Account to load your online letters.")
            return

        from Forge_Tab import _TaskWorker
        self._awaiting_auth = False
        self.account = session.account.account_id
        generation = self.forge._github_service.snapshot.generation
        self._refresh_id += 1
        refresh_id = self._refresh_id
        cancel = self._refresh_cancel = Event()
        deadline = monotonic() + self.REFRESH_TIMEOUT_MS / 1000
        # The local catalog is already loaded. Listing GitHub must not rehash
        # downloaded letters or hold Forge's global project-operation lock.
        local = tuple(self.forge._rendered_catalog_entries)
        self.status.setStyleSheet("")
        self.status.setText("Loading GitHub letters…")
        self.status.show()
        self.retry_button.hide()
        self._refresh_timer.start(self.REFRESH_TIMEOUT_MS)
        _LOGGER.info("[Saved Letters] GitHub listing started; local controls remain available.")

        def cancelled():
            current = self.forge._github_service.snapshot
            return (cancel.is_set() or current.session is None
                    or current.session.account.account_id != session.account.account_id
                    or current.generation != generation)

        def load():
            api = self.forge._github_service.authorized_api(session)
            if hasattr(api, "timeout"):
                api.timeout = min(api.timeout, 10.0)
            library = GitHubLibrary(session, api, cancelled=cancelled,
                store=DestinationStore(self.forge.project_root), deadline=deadline)
            projects, warnings = library.discover()
            _LOGGER.info("[Saved Letters] GitHub metadata loaded: projects=%s warnings=%s elapsed=%.2fs",
                         len(projects), len(warnings), monotonic() - (deadline - self.REFRESH_TIMEOUT_MS / 1000))
            downloads = downloaded_presentations(self.download_root)
            by_id = {}
            for entry in local:
                by_id.setdefault(entry.project_id, []).append(entry)
            rows = []
            for project in projects:
                copies = by_id.get(project.project_id, [])
                location, kind = ("Local + GitHub", "local") if copies else ("GitHub only", "github")
                version, version_kind = "", "draft"
                if project.presentation:
                    path = library.presentation_path(project.presentation, self.download_root)
                    previous = [item for item in downloads.values()
                                if item[0].project_id == project.project_id
                                and item[2].casefold() == library.owner.casefold()]
                    if previous and not path.exists():
                        location, kind = "Local + GitHub", "local"

                    if path.exists():
                        location, kind = (("Local + GitHub", "local") if (path / "index.html").is_file()
                                          else ("Download damaged", "error"))
                rows.append((project, location, kind, version, version_kind, copies))
            library._check()
            self._listing_ready.emit(refresh_id, (rows, warnings))
            # Covers arrive after the usable listing, never as a prerequisite.
            for row in rows:
                try:
                    library._check()
                    cover = library.cover_preview(row[0],
                        application_paths(self.forge.project_root).cache_root / "github-covers")
                    library._check()
                    self._cover_ready.emit(refresh_id, row[0].project_id, cover)
                except GitHubOperationError:
                    break
                except (OSError, ValueError, TypeError, KeyError):
                    pass
            return refresh_id

        thread = self._refresh_thread = QtCore.QThread(self)
        worker = self._refresh_worker = _TaskWorker(load)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.failed.connect(self._refresh_failed, QtCore.Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit, QtCore.Qt.DirectConnection)
        thread.finished.connect(self._refresh_finished, QtCore.Qt.QueuedConnection)
        thread.start()

    @QtCore.Slot(int, object)
    def _show_listing(self, refresh_id, result):
        if refresh_id != self._refresh_id or self._refresh_cancel.is_set() or self.forge._shutdown:
            return
        self._refresh_timer.stop()
        rows, warnings = result
        self.rows = {row[0].project_id: row for row in rows}
        self.status.hide()
        if warnings:
            self._refresh_error("Some GitHub letters could not be loaded: " + warnings[0])
            self.status.setToolTip("\n".join(warnings))
        self.sync_cards()

    @QtCore.Slot(int, str, object)
    def _show_cover(self, refresh_id, project_id, path):
        if refresh_id != self._refresh_id or self._refresh_cancel.is_set() or self.forge._shutdown:
            return
        self.covers[project_id] = path
        for card in self.cards:
            if card.key == project_id:
                card.set_cover(path)

    def _refresh_error(self, message):
        self.status.setText(message + " Your local letters remain available.")
        self.status.setStyleSheet(badge_style("error"))
        self.status.show()
        self.retry_button.show()

    def _refresh_timed_out(self):
        self._awaiting_auth = False
        self._retry_pending = False
        self._refresh_cancel.set()
        _LOGGER.warning("[Saved Letters] GitHub listing timed out; late results will be ignored.")
        self._refresh_error("GitHub took too long to load your letters. Check your connection and choose Retry GitHub.")

    @QtCore.Slot(str, str, bool)
    def _refresh_failed(self, message, _technical, _safe):
        self._refresh_timer.stop()
        if not self._refresh_cancel.is_set() and not self.forge._shutdown:
            _LOGGER.warning("[Saved Letters] GitHub listing failed: %s", message)
            self._refresh_error(message or "GitHub could not load your letters. Please retry.")

    @QtCore.Slot()
    def _refresh_finished(self):
        thread = self._refresh_thread
        self._refresh_thread = self._refresh_worker = None
        self._refresh_timer.stop()
        if thread is not None:
            thread.deleteLater()
        if self._retry_pending and not self.forge._shutdown:
            self._retry_pending = False
            self.refresh()

    def retry(self):
        if self._refresh_thread is not None:
            self._refresh_cancel.set()
            self._retry_pending = True
            self.status.setText("Stopping the previous request before retrying GitHub…")
            self._refresh_timer.start(self.REFRESH_TIMEOUT_MS)
        elif self._session() is None:
            self._awaiting_auth = True
            self.status.setText("Connecting to GitHub… Local letters remain available.")
            self.status.show()
            self._refresh_timer.start(self.REFRESH_TIMEOUT_MS)
            self.forge._validate_github_account_async()
        else:
            self.refresh()

    def shutdown(self, timeout_ms=None):
        self._refresh_timer.stop()
        self._retry_pending = self._awaiting_auth = False
        self._refresh_cancel.set()
        thread = self._refresh_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            thread.quit()
            return thread.wait() if timeout_ms is None else thread.wait(max(0, timeout_ms))
        return True

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
                card.customContextMenuRequested.connect(
                    lambda point, card=card: self.controller.show_context_menu(card, point))
                card.set_cover(self.covers.get(project_id))
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
                card.customContextMenuRequested.connect(
                    lambda point, card=card: self.controller.show_context_menu(card, point))
                from publishing.github_library import presentation_cover
                card.set_cover(presentation_cover(copy, path))
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
        self.open_action.setEnabled(bool(ready and row[0].presentation))
        self.backup_action.setEnabled(bool(self._session() and not self.working and not self.forge.is_protected_project()))
        self.controller.open_button.setText("Open Project")
        if local is None:
            downloaded = self.downloads.get(self.selected_id)
            self.controller.open_button.setEnabled(bool(ready or (downloaded and
                (downloaded[1] / "index.html").is_file() and not self.working and not self.forge._busy)))
        for card in self.cards:
            card.set_selected(card.key == self.selected_id)
            card.setEnabled(not self.working)
        for card in self.forge._saved_cards:
            from library_status import local_badges
            location, publication = local_badges(card.entry)
            remote = self.rows.get(card.entry.project_id)
            if remote and not (card.entry.example or card.entry.stock):
                location = ("Local + GitHub", "both")
                if remote[0].presentation and publication[1] != "different":
                    publication = ("Published", "published")
            set_card_status(card, location, publication)

    def download_project(self):
        row = self.selected()
        if not row or not row[0].backup or self.working:
            return
        project = row[0]
        def completed(path):
            self.forge._refresh_catalog_entry(path)
            self.selected_id = ""
            self.forge._selected_saved_letter = self.forge.catalog._entry(path)
            self.controller.selected_changed()
            def open_downloaded():
                selected = self.forge._selected_saved_letter
                if selected is not None and selected.path == path:
                    self.controller.open_selected()
            self.forge._defer_until_idle(open_downloaded)
        def retrieve(library):
            current = self._latest(library, project.project_id)
            if not current.backup:
                raise ValueError("This editable project is no longer available on GitHub.")
            destination = self.controller.service.editable_root / current.project_id
            if destination.exists():
                return destination
            return library.download_project(current.backup, self.controller.service.editable_root)
        return self._run("Opening project…", retrieve, completed)

    def download_presentation(self):
        row = self.selected()
        if not row or not row[0].presentation or self.working:
            return
        def completed(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path / "index.html")))
            self.forge._defer_until_idle(self.refresh)
        def retrieve(library):
            current = self._latest(library, row[0].project_id)
            if not current.presentation:
                raise ValueError("This published letter is no longer available on GitHub.")
            return library.download_presentation(current.presentation, self.download_root)
        return self._run("Opening letter…", retrieve, completed)

    @staticmethod
    def _latest(library, project_id):
        projects, warnings = library.discover()
        project = next((item for item in projects if item.project_id == project_id), None)
        if project is None:
            raise ValueError(warnings[0] if warnings else "This letter is no longer available on GitHub.")
        return project

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
            self.controller._status(str(error), error=True)
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
            self.controller._status("Project backed up to GitHub.")
            self.forge._defer_until_idle(self.refresh)
        self._run("Backing up editable project…",
            lambda library: library.backup_project(capture, expected_marker=remote.marker_sha if remote else ""), completed)
