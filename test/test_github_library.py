from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import unquote
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets
from library_status import BADGE_COLORS, local_badges
from project_model import create_project
from project_repository import ProjectRepository
from publishing.github_auth import GitHubAccount, GitHubSession, GitHubToken, GitHubOperationError
from publishing.github_library import (
    BACKUP_MARKER, BACKUP_REPOSITORY, GitHubLibrary, LibraryProject, RemoteCopy, project_digest,
)
from publishing.github_pages import PUBLICATION_MARKER, _git_blob_sha
from saved_letters import SavedLetter


class MemoryGit:
    """Immutable Git blobs/trees/commits with non-forced branch updates."""
    def __init__(self):
        self.repos, self.blobs, self.trees, self.commits = {}, {}, {}, {}
        self.calls = []
        self.ref_conflict = False
        self.corrupt_blob = False

    def blob(self, data):
        sha = _git_blob_sha(data)
        self.blobs[sha] = data
        return sha

    def tree(self, files):
        sha = _git_blob_sha(json.dumps(files, sort_keys=True).encode())
        self.trees[sha] = dict(files)
        return sha

    def commit(self, tree, parents):
        sha = _git_blob_sha(json.dumps([tree, parents, len(self.commits)]).encode())
        self.commits[sha] = (tree, parents)
        return sha

    def add_repo(self, name, files, *, private=False):
        tree = self.tree({path: self.blob(data) for path, data in files.items()})
        self.repos[name] = {"private": private, "head": self.commit(tree, [])}

    def request(self, method, path, payload=None, **_kwargs):
        self.calls.append((method, path, payload))
        if path == "/user/repos":
            assert method == "POST" and payload["private"] is True
            self.add_repo(payload["name"], {"README.md": b"private"}, private=True)
            return {}
        parts = unquote(path).split("/")
        assert parts[:3] == ["", "repos", "ada"], path
        name = parts[3]
        if name not in self.repos:
            raise GitHubOperationError("not_found", "Missing repository", status=404)
        repo = self.repos[name]
        tail = "/".join(parts[4:])
        if not tail:
            return {"owner": {"login": "ada"}, "name": name, "private": repo["private"], "default_branch": "main"}
        if tail == "git/ref/heads/main":
            return {"object": {"sha": repo["head"]}}
        if tail == "git/refs/heads/main":
            assert payload["force"] is False
            if self.ref_conflict or self.commits[payload["sha"]][1] != [repo["head"]]:
                raise GitHubOperationError("conflict", "Concurrent GitHub edit", status=422)
            repo["head"] = payload["sha"]
            return {"object": {"sha": repo["head"]}}
        if tail.startswith("git/commits/"):
            return {"tree": {"sha": self.commits[parts[-1]][0]}}
        if tail.startswith("git/trees/"):
            files = self.trees[parts[-1].split("?")[0]]
            return {"truncated": False, "tree": [
                {"path": path, "sha": sha, "size": len(self.blobs[sha]), "type": "blob", "mode": "100644"}
                for path, sha in files.items()]}
        if tail.startswith("git/blobs/"):
            data = b"damaged" if self.corrupt_blob else self.blobs[parts[-1]]
            return {"encoding": "base64", "content": base64.b64encode(data).decode()}
        if tail == "git/blobs":
            return {"sha": self.blob(base64.b64decode(payload["content"]))}
        if tail == "git/trees":
            files = dict(self.trees[payload["base_tree"]])
            for item in payload["tree"]:
                if item["sha"] is None:
                    del files[item["path"]]
                else:
                    files[item["path"]] = item["sha"]
            return {"sha": self.tree(files)}
        if tail == "git/commits":
            return {"sha": self.commit(payload["tree"], payload["parents"])}
        raise AssertionError((method, path, payload))


class GitHubLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = MemoryGit()
        self.session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("test-token"))
        self.library = GitHubLibrary(self.session, self.api)
        self.project = create_project(["cover", "letter", "end_page"])
        self.project.update_project_data("metadata", {"recipient_title": "A letter", "title": "A letter", "recipient_name": "Recipient"})
        # Real canonical authoring data, including Unicode and recoverable content.
        letter = self.project.pages[1]["id"]
        self.project.update_prompt(letter, {"text": "  Preserve 雪\nPrivate prompt  ", "stale": False})
        self.capture = SimpleNamespace(project=self.project, asset_root=self.root, fallback_asset_root=None, migration_report={})

    def publication(self, *, legacy=False):
        slug = "a-letter"
        marker = {"schema_version": 2, "provider": "github_pages",
                  "project_id": self.project.project_id, "public_path": slug,
                  "published_at": "2026-10-20T12:00:00+00:00",
                  "source_fingerprint": "content", "title": "A letter", "recipient": "Recipient"}
        repo = "LetterSmith-Published" if legacy else "ada.github.io"
        prefix = "letters/" + slug if legacy else slug
        self.api.add_repo(repo, {prefix + "/" + PUBLICATION_MARKER: json.dumps(marker).encode(),
                               prefix + "/index.html": b"<html>Letter</html>",
                               prefix + "/media/image.png": b"image bytes"})

    def test_cover_previews_use_verified_artwork_from_backup_and_publication(self):
        artwork = (Path(__file__).resolve().parents[1] / "resources/stock/images/cover/stock cover 1.png").read_bytes()
        (self.root / "cover.png").write_bytes(artwork)
        self.project.update_artwork(self.project.pages[0]["id"], {"asset": "cover.png"})
        self.library.backup_project(self.capture)
        project = self.library.discover()[0][0]
        self.api.calls.clear()
        preview = self.library.cover_preview(project, self.root / "covers")
        self.assertEqual(preview.read_bytes(), artwork)
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))
        self.publication()
        copy = self.library.discover()[0][0].presentation
        files = {copy.prefix + "/" + name: self.api.blobs[sha] for name, sha, size in copy.files}
        source = "gallery/pages/page-123/artwork.png"
        files[copy.prefix + "/" + source] = artwork
        files[copy.prefix + "/lettersmith-display.json"] = json.dumps({
            "pages": [{"type": "cover", "source": source}]}).encode()
        self.api.add_repo("ada.github.io", files)
        presentation = self.library.discover()[0][0].presentation
        preview = self.library.cover_preview(LibraryProject(presentation.project_id, presentation=presentation), self.root / "public-covers")
        self.assertEqual(preview.read_bytes(), artwork)
        downloaded = self.library.download_presentation(presentation, self.root / "downloads")
        from publishing.github_library import presentation_cover
        self.assertEqual(presentation_cover(presentation, downloaded).read_bytes(), artwork)

    def test_public_discovery_download_is_pinned_and_does_not_upload(self):
        self.publication()
        projects, warnings = self.library.discover()
        self.assertFalse(warnings)
        copy = projects[0].presentation
        self.assertEqual(copy.url, "https://ada.github.io/a-letter/")
        # A new branch head must not change what was selected for download.
        self.api.add_repo("ada.github.io", {"other/index.html": b"other"})
        destination = self.library.download_presentation(copy, self.root / "downloads")
        self.assertEqual((destination / "index.html").read_bytes(), b"<html>Letter</html>")
        self.assertEqual(self.library.download_presentation(copy, self.root / "downloads"), destination)
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))
        (destination / "index.html").write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "damaged"):
            self.library.download_presentation(copy, self.root / "downloads")

    def test_legacy_repository_is_discovered(self):
        self.publication(legacy=True)
        projects, warnings = self.library.discover()
        self.assertFalse(warnings)
        self.assertEqual(projects[0].presentation.url, "https://ada.github.io/LetterSmith-Published/letters/a-letter/")

    def test_private_backup_roundtrip_and_conflict_preserve_local_projects(self):
        (self.root / "art.png").write_bytes(b"art")
        self.project.update_artwork(self.project.pages[0]["id"], {"asset": "art.png"})
        self.publication()
        self.library.backup_project(self.capture)
        self.assertTrue(self.api.repos[BACKUP_REPOSITORY]["private"])
        projects, warnings = self.library.discover()
        self.assertFalse(warnings)
        self.assertEqual(len(projects), 1)
        self.assertIsNotNone(projects[0].presentation)
        copy = projects[0].backup
        destination = self.library.download_project(copy, self.root / "library")
        imported = ProjectRepository(destination / ".lettersmith-project").load()
        self.assertEqual(imported.project.project_id, self.project.project_id)
        self.assertEqual(imported.project.get_prompt(self.project.pages[1]["id"])["text"], "  Preserve 雪\nPrivate prompt  ")
        self.assertEqual(project_digest(imported), copy.digest)
        original = imported.project.to_dict()
        with self.assertRaisesRegex(ValueError, "already has this identity"):
            self.library.download_project(copy, self.root / "library")
        second = self.library.download_project(copy, self.root / "library", as_copy=True)
        self.assertNotEqual(destination, second)
        self.assertEqual(ProjectRepository(destination / ".lettersmith-project").load().project.to_dict(), original)
        self.assertFalse(any((self.root / "library").glob(".github-*")))

    def test_backup_refuses_public_repo_before_upload(self):
        self.api.add_repo(BACKUP_REPOSITORY, {"README.md": b"public"}, private=False)
        with self.assertRaisesRegex(ValueError, "must be private"):
            self.library.backup_project(self.capture)
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_remote_change_requires_review_and_nonforce_update(self):
        self.library.backup_project(self.capture)
        copy = self.library.discover()[0][0].backup
        before = self.api.repos[BACKUP_REPOSITORY]["head"]
        with self.assertRaisesRegex(ValueError, "changed"):
            self.library.backup_project(self.capture)
        self.assertEqual(self.api.repos[BACKUP_REPOSITORY]["head"], before)
        self.api.ref_conflict = True
        with self.assertRaises(GitHubOperationError):
            self.library.backup_project(self.capture, expected_marker=copy.marker_sha)
        self.assertEqual(self.api.repos[BACKUP_REPOSITORY]["head"], before)

    def test_backup_replacement_preserves_other_projects(self):
        self.library.backup_project(self.capture)
        first = self.library.discover()[0][0].backup
        other = self.project.independent_copy(title="Other")
        self.library.backup_project(SimpleNamespace(**{**vars(self.capture), "project": other}))
        self.project.update_prompt(self.project.pages[1]["id"], {"text": "new"})
        self.library.backup_project(self.capture, expected_marker=first.marker_sha)
        projects, warnings = self.library.discover()
        self.assertFalse(warnings)
        self.assertEqual({p.project_id for p in projects}, {self.project.project_id, other.project_id})

    def test_invalid_bytes_and_unsafe_paths_never_commit_downloads(self):
        self.publication()
        copy = self.library.discover()[0][0].presentation
        self.api.corrupt_blob = True
        with self.assertRaisesRegex(ValueError, "verification"):
            self.library.download_presentation(copy, self.root / "bad")
        self.assertFalse(self.library.presentation_path(copy, self.root / "bad").exists())
        self.api.corrupt_blob = False
        for path in ("../escape", "C:/escape", "CON.txt", "media/a.", "media/a\\b"):
            unsafe = replace(copy, files=((path, _git_blob_sha(b""), 0),))
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unsafe"):
                self.library.download_presentation(unsafe, self.root / "unsafe")
        self.assertFalse((self.root / "escape").exists())

    def test_bad_backup_identity_and_digest_never_import(self):
        self.library.backup_project(self.capture)
        backup = self.library.discover()[0][0].backup
        for copy in (replace(backup, digest="wrong"), replace(backup, project_id=str(uuid.uuid4()))):
            with self.assertRaisesRegex(ValueError, "does not match"):
                self.library.download_project(copy, self.root / "library")
        self.assertEqual(list((self.root / "library").iterdir()), [])

    def test_cancelled_operation_does_not_request_or_commit(self):
        self.library.cancelled = lambda: True
        with self.assertRaises(GitHubOperationError):
            self.library.discover()
        self.assertFalse(self.api.calls)

    def test_discovery_deadline_stops_before_request(self):
        library = GitHubLibrary(self.session, self.api, deadline=monotonic() - 1)
        with self.assertRaises(GitHubOperationError) as caught:
            library.discover()
        self.assertEqual(caught.exception.code, "library_timeout")
        self.assertFalse(self.api.calls)

    def test_shared_verified_markers_are_requested_once(self):
        self.publication()
        original = self.library.discover()[0][0].presentation
        self.library._metadata.clear()
        self.publication(legacy=True)
        self.api.calls.clear()
        projects, warnings = self.library.discover()
        self.assertFalse(warnings)
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(projects[0].retained_presentations), 1)
        self.assertEqual(sum(path.endswith('/git/blobs/' + original.marker_sha)
                             for _, path, _ in self.api.calls), 1)

    def test_truncated_listing_reports_failure_without_empty_success(self):
        self.publication()
        original = self.api.request
        def request(method, path, payload=None, **kwargs):
            result = original(method, path, payload, **kwargs)
            if "/git/trees/" in path:
                result["truncated"] = True
            return result
        self.api.request = request
        projects, warnings = self.library.discover()
        self.assertFalse(projects)
        self.assertIn("incomplete", warnings[0])


class LibraryBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def entry(self, **kwargs):
        return SavedLetter(Path("local"), "", "Letter", datetime.now(timezone.utc), "", None, **kwargs)

    def test_location_and_publication_are_separate_and_colors_distinct(self):
        from Forge_Tab import SavedLetterCard
        entry = self.entry(canonical=True)
        card = SavedLetterCard(entry)
        self.addCleanup(card.deleteLater)
        self.assertEqual(card.storage_status, ("Local", "local"))
        self.assertEqual(card.publication_status, ("Unpublished", "draft"))
        self.assertEqual(local_badges(entry)[0], local_badges(self.entry())[0])
        self.assertEqual(len(set(BADGE_COLORS.values())), 8)
        self.assertEqual(local_badges(self.entry(example=True))[1], ("", ""))
        self.assertGreater(card.cover.height(), card.height() * .65)
        self.assertFalse(hasattr(card, "status_label"))
        card.update_entry(replace(entry, saved_publication_state="Saved changes not published (last known publication)"))
        self.assertEqual(card.storage_status, ("Local", "local"))
        self.assertIn("Local changes", card.publication_status[0])

    def test_badge_colors_survive_all_themes(self):
        from Forge_Tab import SavedLetterCard
        from ui_theme import ThemeService
        from settings_store import SettingsStore
        with tempfile.TemporaryDirectory() as root:
            service = ThemeService(Path(__file__).resolve().parents[1], settings=SettingsStore(root))
            card = SavedLetterCard(self.entry(canonical=True))
            self.addCleanup(card.deleteLater)
            expected = card.storage_status
            for theme in ("cyber_forge", "obsidian_forge", "velvet_rose", "celestial_rose", "dark", "light"):
                service.set_theme(theme, persist=False)
                service.apply_semantic_styles(card)
                self.assertEqual(card.storage_status, expected)


class GitHubLibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from Forge_Tab import ForgeTab
        from project_session import release_project_session
        from publishing.github_auth import GitHubConnectionSnapshot, GitHubConnectionState
        self.fixture = GitHubLibraryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(release_project_session, self.fixture.root)
        self.fixture.publication()
        self.connection = SimpleNamespace(
            snapshot=GitHubConnectionSnapshot(GitHubConnectionState.CONNECTED, session=self.fixture.session),
            subscribe=mock.Mock(), unsubscribe=mock.Mock(), authorized_api=lambda _session: self.fixture.api)
        self.connection.restore = lambda: self.connection.snapshot
        self.forge = ForgeTab(self.fixture.root, github_service=self.connection)
        self.library = self.forge.saved_projects.github_library
        self.addCleanup(self.cleanup)
        self.forge.saved_panel.resize(900, 600)
        self.forge.saved_panel.show()
        question = mock.patch("saved_projects_ui.QtWidgets.QMessageBox.question", return_value=QtWidgets.QMessageBox.Discard)
        question.start()
        self.addCleanup(question.stop)

    def cleanup(self):
        self.forge.saved_panel.close()
        self.forge.shutdown(timeout_ms=5000)
        self.forge.close()
        self.forge.deleteLater()
        self.app.processEvents()

    def settle(self):
        deadline = QtCore.QDeadlineTimer(30000)
        while not deadline.hasExpired():
            self.app.processEvents()
            if not self.forge._busy and not self.library.working and self.library._refresh_thread is None:
                sleep(0.1)
                self.app.processEvents()
                if not self.forge._busy and not self.library.working and self.library._refresh_thread is None:
                    return
            sleep(0.01)
        self.fail(f"Library did not settle: busy={self.forge._busy}, working={self.library.working}, "
                  f"activity={self.forge._busy_operation}, details={self.library.status.text()}, "
                  f"status={self.forge.status.text()}")

    def test_real_worker_listing_and_download_keep_open_project(self):
        from project_session import get_project_session
        session = get_project_session(self.fixture.root)
        before = session.project.to_dict()
        self.library.refresh()
        self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.assertFalse(any(button.text() in ("Project Actions", "Refresh GitHub", "Download")
            for button in self.forge.saved_panel.findChildren(QtWidgets.QAbstractButton)))
        card = self.library.cards[0]
        QtTest.QTest.mouseClick(card, QtCore.Qt.LeftButton)
        self.assertEqual(card.storage_status[0], "GitHub only")
        self.assertEqual(card.publication_status[0], "Published")
        self.assertEqual(self.forge.saved_projects.open_button.text(), "Open Project")
        with mock.patch("github_library_ui.QtGui.QDesktopServices.openUrl") as opened:
            QtTest.QTest.mouseClick(self.forge.saved_projects.open_button, QtCore.Qt.LeftButton)
            self.settle()
            # Completion queues a refresh on Forge's existing idle path.
            QtTest.QTest.qWait(120)
            self.settle()
            opened.assert_called_once()
        self.assertEqual(self.library.cards[0].storage_status[0], "Local + GitHub")
        self.assertEqual(session.project.to_dict(), before)

    def test_remote_open_activates_project_and_refresh_preserves_local_edits(self):
        self.fixture.library.backup_project(self.fixture.capture)
        self.library.refresh()
        self.settle()
        QtTest.QTest.mouseClick(self.library.cards[0], QtCore.Qt.LeftButton)
        QtTest.QTest.mouseClick(self.forge.saved_projects.open_button, QtCore.Qt.LeftButton)
        self.settle()
        QtTest.QTest.qWait(120)
        self.settle()
        # Opening now hides the panel; render the local catalog when returning.
        self.forge.saved_panel.show()
        self.forge.refresh_saved_letters(force_reconcile=True)
        deadline = QtCore.QDeadlineTimer(5000)
        while self.forge._catalog_reconcile_active and not deadline.hasExpired():
            self.app.processEvents()
            sleep(0.01)
        self.assertEqual(len(self.library.cards), 0)
        self.assertEqual(len(self.forge._saved_cards), 1)
        self.assertEqual(self.forge.saved_projects.session.project.project_id, self.fixture.project.project_id)
        self.assertEqual(self.forge._saved_cards[0].storage_status, ("Local + GitHub", "both"))
        self.assertEqual(self.forge.saved_projects.open_button.text(), "Open Project")
        directory = self.forge.saved_projects.service.editable_root / self.fixture.project.project_id
        stored = ProjectRepository(directory / ".lettersmith-project").load()
        stored.project.update_prompt(stored.project.pages[1]["id"], {"text": "local edits"})
        ProjectRepository(directory / ".lettersmith-project").save(
            stored.project, asset_root=stored.asset_root, expected_revision=stored.revision)
        self.library.refresh()
        self.settle()
        self.assertEqual(ProjectRepository(directory / ".lettersmith-project").load().project.get_prompt(
            stored.project.pages[1]["id"])["text"], "local edits")
        self.assertTrue(self.library.status.isHidden())

    def test_account_change_clears_rows_and_disables_actions(self):
        from publishing.github_auth import GitHubConnectionSnapshot, GitHubConnectionState
        self.library.refresh()
        self.settle()
        QtTest.QTest.mouseClick(self.library.cards[0], QtCore.Qt.LeftButton)
        self.connection.snapshot = GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED)
        self.forge._github_state_changed.emit(self.connection.snapshot)
        self.app.processEvents()
        self.assertEqual(len(self.library.cards), 0)
        self.assertFalse(self.library.open_action.isEnabled())

    def test_listing_is_usable_before_slow_covers_and_refresh_does_not_overlap(self):
        release, entered = Event(), Event()
        def slow_cover(*args):
            entered.set()
            release.wait(5)
            return None
        with mock.patch('github_library_ui.GitHubLibrary.cover_preview', side_effect=slow_cover):
            try:
                self.library.refresh()
                deadline = QtCore.QDeadlineTimer(5000)
                while (not entered.is_set() or not self.library.cards) and not deadline.hasExpired():
                    QtTest.QTest.qWait(10)
                self.assertTrue(entered.is_set())
                self.assertEqual(len(self.library.cards), 1)
                self.assertTrue(self.library.cards[0].isEnabled())
                self.assertTrue(self.forge.saved_projects.menu.isEnabled())
                self.assertFalse(self.forge._busy)
                self.assertTrue(self.library.status.isHidden())
                calls = len(self.fixture.api.calls)
                self.library.refresh()
                QtTest.QTest.qWait(50)
                self.assertEqual(len(self.fixture.api.calls), calls)
            finally:
                release.set()
                self.settle()

    def test_timeout_restores_controls_ignores_late_response_and_retry_recovers(self):
        release, entered = Event(), Event()
        request = self.fixture.api.request
        def stalled_request(*args, **kwargs):
            entered.set()
            release.wait(5)
            return request(*args, **kwargs)
        self.library.REFRESH_TIMEOUT_MS = 50
        with mock.patch.object(self.fixture.api, 'request', side_effect=stalled_request):
            try:
                self.library.refresh()
                QtTest.QTest.qWait(150)
                self.assertTrue(entered.is_set())
                self.assertIn('too long', self.library.status.text())
                self.assertTrue(self.library.retry_button.isVisible())
                self.assertTrue(self.forge.saved_projects.menu.isEnabled())
                self.assertFalse(self.forge._busy)
            finally:
                release.set()
                self.settle()
        self.assertFalse(self.library.rows)
        self.library.REFRESH_TIMEOUT_MS = 5000
        QtTest.QTest.mouseClick(self.library.retry_button, QtCore.Qt.LeftButton)
        self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.assertTrue(self.library.retry_button.isHidden())

    def test_authentication_failure_is_visible_and_retry_loads_existing_letters(self):
        with mock.patch.object(self.fixture.api, 'request', side_effect=GitHubOperationError(
                'authentication', 'GitHub sign-in has expired. Please sign in again.')) as request:
            self.library.refresh()
            self.settle()
            self.assertEqual(request.call_count, 1)
        self.assertIn('sign-in has expired', self.library.status.text())
        self.assertTrue(self.library.retry_button.isVisible())
        QtTest.QTest.mouseClick(self.library.retry_button, QtCore.Qt.LeftButton)
        self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.assertTrue(self.library.status.isHidden())

    def test_connecting_account_times_out_and_retry_resumes_listing(self):
        from publishing.github_auth import GitHubConnectionSnapshot, GitHubConnectionState
        self.connection.snapshot = GitHubConnectionSnapshot(GitHubConnectionState.CONNECTING)
        self.library.REFRESH_TIMEOUT_MS = 50
        self.library.refresh()
        self.assertFalse(self.forge._busy)
        QtTest.QTest.qWait(100)
        self.assertIn('too long', self.library.status.text())
        self.assertTrue(self.library.retry_button.isVisible())
        def restored():
            self.connection.snapshot = GitHubConnectionSnapshot(
                GitHubConnectionState.CONNECTED, session=self.fixture.session)
            self.forge._github_state_changed.emit(self.connection.snapshot)
            return self.connection.snapshot
        self.connection.restore = restored
        self.library.REFRESH_TIMEOUT_MS = 5000
        QtTest.QTest.mouseClick(self.library.retry_button, QtCore.Qt.LeftButton)
        deadline = QtCore.QDeadlineTimer(5000)
        while not self.library.rows and not deadline.hasExpired():
            self.app.processEvents()
            sleep(0.01)
        self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.assertTrue(self.library.status.isHidden())

    def test_retry_waits_for_cancelled_request_without_starting_overlapping_workers(self):
        release, entered = Event(), Event()
        request = self.fixture.api.request
        def stalled(*args, **kwargs):
            entered.set()
            release.wait(5)
            return request(*args, **kwargs)
        with mock.patch.object(self.fixture.api, 'request', side_effect=stalled) as call:
            try:
                self.library.refresh()
                deadline = QtCore.QDeadlineTimer(5000)
                while not entered.is_set() and not deadline.hasExpired():
                    self.app.processEvents()
                    sleep(0.01)
                self.library._refresh_timed_out()
                self.library.retry()
                self.library.retry()
                self.assertEqual(call.call_count, 1)
                self.assertFalse(self.forge._busy)
            finally:
                release.set()
                self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.assertTrue(self.library.retry_button.isHidden())

    def test_downloaded_presentation_stays_in_normal_library_offline(self):
        from publishing.github_auth import GitHubConnectionSnapshot, GitHubConnectionState
        copy = self.fixture.library.discover()[0][0].presentation
        path = self.fixture.library.download_presentation(copy, self.library.download_root)
        self.connection.snapshot = GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED)
        self.forge._github_state_changed.emit(self.connection.snapshot)
        self.app.processEvents()
        self.assertEqual(len(self.library.cards), 1)
        card = self.library.cards[0]
        self.assertEqual(card.storage_status[0], "Local")
        QtTest.QTest.mouseClick(card, QtCore.Qt.LeftButton)
        self.assertEqual(self.forge.saved_projects.open_button.text(), "Open Project")
        with mock.patch("github_library_ui.QtGui.QDesktopServices.openUrl") as opened:
            QtTest.QTest.mouseClick(self.forge.saved_projects.open_button, QtCore.Qt.LeftButton)
            self.assertEqual(opened.call_args.args[0].toLocalFile(), str(path / "index.html").replace("\\", "/"))

    def test_stock_view_does_not_show_or_destroy_online_cards(self):
        self.library.refresh()
        self.settle()
        self.assertEqual(len(self.library.cards), 1)
        self.forge._saved_panel_mode = "stock"
        self.forge._layout_saved_cards()
        self.assertTrue(self.library.cards[0].isHidden())
        self.forge._saved_panel_mode = "saved"
        self.library.sync_cards()
        self.assertEqual(len(self.library.cards), 1)
        self.assertFalse(self.library.cards[0].isHidden())


if __name__ == "__main__":
    unittest.main()
