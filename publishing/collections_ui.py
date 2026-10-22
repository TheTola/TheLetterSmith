"""Collection setup and publication review using the existing GitHub connection."""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from publishing.destinations import DestinationStore, public_path, repository_display_name
from publishing.github_collections import GitHubCollections
from publishing.github_ui import _active_theme_tokens


class _Work(QtCore.QThread):
    def __init__(self, task, parent):
        super().__init__(parent)
        self.task, self.value, self.error = task, None, ""

    def run(self):
        try:
            self.value = self.task()
        except Exception as error:
            self.error = str(getattr(error, "user_message", error))


class _PublishingDialog(QtWidgets.QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(580)
        self.work = None
        self.cancel_requested = False
        self.layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        colors = _active_theme_tokens(parent)
        self.setStyleSheet(f"QDialog{{background:{colors.panel_background};color:{colors.text};}}"
            f"QLabel{{color:{colors.text};}} QLineEdit,QComboBox,QTextEdit{{background:{colors.control_background};color:{colors.text};}}"
            f"QPushButton{{background:{colors.control_background};color:{colors.text};padding:8px;border:1px solid {colors.border};}}"
            f"QPushButton:disabled{{color:{colors.muted_text};}}")

    def run_task(self, task, completed):
        if self.work:
            return
        self.cancel_requested = False
        self.status.setText("Checking GitHub…")
        controls = [c for c in self.findChildren(QtWidgets.QWidget)
                    if isinstance(c, (QtWidgets.QPushButton, QtWidgets.QComboBox, QtWidgets.QLineEdit, QtWidgets.QListWidget))]
        enabled = [(c, c.isEnabled()) for c in controls]
        for control in controls:
            control.setEnabled(False)
        self.work = _Work(task, self)
        def finished():
            work, self.work = self.work, None
            for control, was_enabled in enabled:
                control.setEnabled(was_enabled)
            work.deleteLater()
            if self.cancel_requested:
                super(_PublishingDialog, self).reject()
            elif work.error:
                self.status.setText(work.error)
            else:
                self.status.clear()
                completed(work.value)
        self.work.finished.connect(finished)
        self.work.start()

    def reject(self):
        if self.work:
            self.cancel_requested = True
            self.status.setText("Cancelling after the current GitHub request…")
            return
        super().reject()


class CollectionDialog(_PublishingDialog):
    """One-time naming and setup; no repository management controls."""
    def __init__(self, session, api, project_root, parent=None, *, cancelled=lambda: False):
        super().__init__(parent, "Set up your letter repository")
        self.store = DestinationStore(project_root)
        self.service = GitHubCollections(session, api, self.store,
            cancelled=lambda: self.cancel_requested or cancelled())
        self.session, self.plan, self.destination = session, None, None
        self.layout.addWidget(QtWidgets.QLabel(f"GitHub account: {session.account.login}"))
        self.repository = QtWidgets.QLineEdit()
        self.repository.setPlaceholderText("Choose a repository name")
        form = QtWidgets.QFormLayout()
        form.addRow("Repository name", self.repository)
        self.layout.addLayout(form)
        explanation = QtWidgets.QLabel(
            "Choose where your published letters will be stored. LetterSmith will remember this repository. "
            "The repository and published letters will be public.")
        explanation.setWordWrap(True)
        self.layout.addWidget(explanation)
        self.preview = QtWidgets.QLabel()
        self.preview.setTextFormat(QtCore.Qt.PlainText)
        self.preview.setWordWrap(True)
        self.layout.addWidget(self.preview)
        self.layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.review = QtWidgets.QPushButton("Check name")
        self.confirm = QtWidgets.QPushButton("Create repository")
        self.close_button = QtWidgets.QPushButton("Not now")
        for button in (self.review, self.confirm, self.close_button):
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.review.clicked.connect(self._review)
        self.confirm.clicked.connect(self._confirm)
        self.close_button.clicked.connect(self.reject)
        self.repository.textChanged.connect(self._invalidate)
        self._invalidate()

    def _invalidate(self, *_args):
        self.plan = None
        self.confirm.setEnabled(False)
        self.preview.clear()
        self.review.setEnabled(bool(self.repository.text().strip()))

    def _review(self):
        name = self.repository.text().strip()
        self.plan = None
        self.confirm.setEnabled(False)
        def done(plan):
            if name != self.repository.text().strip():
                self._invalidate()
                return
            self.plan = plan
            d = plan.destination
            self.preview.setText(
                f"Repository: {d.owner}/{d.repository}\n{d.base_url}/\n"
                + ("Use this existing repository. Existing letters will be kept."
                   if plan.exists else "Create this public repository for your letters."))
            self.confirm.setText("Use repository" if plan.exists else "Create repository")
            self.confirm.setEnabled(True)
        self.run_task(lambda: self.service.inspect(name, repository_display_name(name)), done)

    def _confirm(self):
        if not self.plan:
            return
        plan = self.plan
        def done(destination):
            self.destination = destination
            self.accept()
        self.run_task(lambda: self.service.configure(plan, confirmed=True), done)


class PublicationDialog(_PublishingDialog):
    def __init__(self, publisher, metadata, parent=None):
        super().__init__(parent, "Review publishing")
        self.publisher, self.metadata, self.result = publisher, dict(metadata), None
        title = QtWidgets.QLabel(str(metadata.get("recipient_title", "")))
        title.setTextFormat(QtCore.Qt.PlainText)
        title.setWordWrap(True)
        self.layout.addWidget(title)
        self.path_mode = QtWidgets.QComboBox()
        self.path_mode.addItems(["Readable slug", "Use title", "Custom folder"])
        self.folder = QtWidgets.QLineEdit()
        form = QtWidgets.QFormLayout()
        form.addRow("Address style", self.path_mode)
        form.addRow("Folder name", self.folder)
        self.layout.addLayout(form)
        self.saved_path = str(metadata.get("published_public_path") or "")
        if self.saved_path:
            self.folder.setText(self.saved_path)
            self.path_mode.setEnabled(False)
        self.folder.setEnabled(False)
        self.preview = QtWidgets.QTextEdit()
        self.preview.setReadOnly(True)
        self.layout.addWidget(self.preview)
        self.layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.review = QtWidgets.QPushButton("Preview address")
        self.confirm = QtWidgets.QPushButton("Publish letter")
        cancel = QtWidgets.QPushButton("Cancel")
        for button in (self.review, self.confirm, cancel):
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.review.clicked.connect(self._review)
        self.confirm.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        self.confirm.setEnabled(False)
        self.path_mode.currentIndexChanged.connect(self._path_changed)
        self.folder.textChanged.connect(self._invalidate_path)
        QtCore.QTimer.singleShot(0, self._review)

    def _invalidate_path(self, *_args):
        self.result = None
        self.confirm.setEnabled(False)

    def _path_changed(self, *_args):
        self.folder.setEnabled(self.path_mode.currentIndex() == 2 and not self.saved_path)
        if self.path_mode.currentIndex() == 1:
            self.folder.setText(str(self.metadata.get("recipient_title") or ""))
        elif self.path_mode.currentIndex() == 0 and not self.saved_path:
            self.folder.clear()
        self._invalidate_path()

    def _review(self):
        self.result = None
        try:
            if not self.saved_path:
                self.metadata.pop("_requested_public_path", None)
                if self.path_mode.currentIndex() != 0:
                    self.metadata["_requested_public_path"] = public_path(self.folder.text(), normalize=True)
        except ValueError as error:
            self.status.setText(str(error))
            self.confirm.setEnabled(False)
            return
        reviewed = dict(self.metadata)
        mode, folder = self.path_mode.currentIndex(), self.folder.text()
        def done(result):
            if (mode, folder) != (self.path_mode.currentIndex(), self.folder.text()):
                self._invalidate_path()
                return
            self.result = result
            d = result["destination"]
            self.preview.setPlainText(f"Account: {d['owner']}\nCollection: {d['display_name']}\n"
                f"Repository: {d['repository']}\nFolder: {result['path']}\n{result['url']}\n\n"
                "The finished letter and repository contents will be public. Updating a published letter keeps its address.")
            self.confirm.setEnabled(True)
        self.run_task(lambda: self.publisher.preview(reviewed), done)

    def _confirm(self):
        if self.result:
            self.accept()
