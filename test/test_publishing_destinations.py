from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from publishing.destinations import (
    DestinationStore, PublishingDestination, destination_for, public_path, repository_display_name,
)
from settings_store import SettingsStore, empty_publication_settings
from publishing.github_collections import GitHubCollections
from publishing.github_auth import GitHubOperationError


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = DestinationStore(self.root)
        self.session = SimpleNamespace(account=SimpleNamespace(login="michael", account_id=7))
        self.destination = PublishingDestination(7, "michael", "Letters-to-Keep", 21,
            "Letters to Keep", base_url="https://michael.github.io/Letters-to-Keep", confirmed=True)

    def test_repository_label_is_display_only(self):
        for raw, expected in (("my_letter_repository", "My Letter Repository"),
                              ("  my/letter\\repository  ", "My Letter Repository"),
                              ("Letters-of-Encouragement", "Letters Of Encouragement")):
            self.assertEqual(repository_display_name(raw), expected)
        self.store.save(self.destination, make_default=True)
        repository_display_name(self.destination.repository)
        self.assertEqual(self.store.default(7).repository, "Letters-to-Keep")

    def test_configured_repository_skips_network_and_initial_setup(self):
        from unittest import mock
        from Forge_Tab import ForgeTab
        from publishing.github_auth import GitHubConnectionState
        self.store.save(self.destination, make_default=True)
        request = mock.Mock(side_effect=AssertionError("Unexpected network request"))
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        self.assertEqual(service.restore_default(), self.destination)
        forge = SimpleNamespace(_shutdown=False, _busy=False, _publication_operation="",
            project_root=self.root, _sync_repository_summary=mock.Mock(),
            _github_service=SimpleNamespace(snapshot=SimpleNamespace(
                state=GitHubConnectionState.CONNECTED, session=self.session)))
        ForgeTab._ensure_publishing_repository(forge)
        forge._sync_repository_summary.assert_called_once()
        request.assert_not_called()

    def test_existing_connected_site_is_remembered_without_remote_changes(self):
        self.session.repository_name = self.destination.repository
        self.session.repository_id = self.destination.repository_id
        calls = []
        def request(method, path, payload=None, **kwargs):
            calls.append(method)
            if path.endswith("/pages"):
                return {"html_url": self.destination.base_url,
                        "source": {"branch": "main", "path": "/"}}
            return {"id": 21, "name": self.destination.repository,
                    "owner": {"login": "michael", "id": 7}, "permissions": {"push": True}}
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        self.assertEqual(service.restore_default().repository, self.destination.repository)
        self.assertEqual(self.store.default(7).repository_id, 21)
        self.assertEqual(calls, ["GET", "GET"])
        service.restore_default()
        self.assertEqual(calls, ["GET", "GET"])

    def test_first_setup_creates_exact_chosen_name_and_remembers_it(self):
        name = "my_letter_repository"
        state = {"exists": False, "pages": False}
        writes = []
        def request(method, path, payload=None, **kwargs):
            if method == "POST":
                writes.append((path, payload))
                state["exists" if path == "/user/repos" else "pages"] = True
                return {}
            if not state["exists"] or (path.endswith("/pages") and not state["pages"]):
                raise GitHubOperationError("not_found", "Missing", status=404)
            if path.endswith("/pages"):
                return {"html_url": f"https://michael.github.io/{name}",
                        "source": {"branch": "main", "path": "/"}}
            return {"id": 40, "name": name, "owner": {"login": "michael", "id": 7},
                    "permissions": {"push": True, "admin": True}}
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        self.assertIsNone(service.restore_default())
        service.configure(service.inspect(name), confirmed=True)
        self.assertEqual(writes[0][1]["name"], name)
        restarted = DestinationStore(self.root)
        self.assertEqual(restarted.default(7).repository, name)
        self.assertIsNone(restarted.default(8))

    def test_default_is_account_scoped_and_does_not_rebind_old_cards(self):
        self.store.save(self.destination, make_default=True)
        self.assertIsNone(self.store.default(8))
        self.assertEqual(destination_for(self.session, {}, self.store), self.destination)
        old = {"published_github_owner": "michael", "published_github_repository": "michael.github.io"}
        self.assertEqual(destination_for(self.session, old, self.store).repository, "michael.github.io")
        with self.assertRaises(ValueError):
            destination_for(self.session, {"published_destination": replace(self.destination, account_id=8).to_dict()}, self.store)

    def test_stable_identity_survives_friendly_and_repository_names(self):
        self.store.save(self.destination, make_default=True)
        self.store.save(replace(self.destination, display_name="Family", repository="Family-Letters"))
        self.assertEqual(len(self.store.all(7)), 1)
        self.assertEqual(self.store.default(7).display_name, "Family")

    def test_future_registry_is_preserved(self):
        self.store.path.write_text('{"version": 99}')
        with self.assertRaises(ValueError):
            self.store.save(self.destination)
        self.assertEqual(json.loads(self.store.path.read_text()), {"version": 99})

    def test_destination_roundtrips_settings_and_clears_with_publication(self):
        settings = SettingsStore(self.root)
        settings.update_fields({"published_destination": self.destination.to_dict()})
        self.assertEqual(settings.snapshot()["published_destination"], self.destination.to_dict())
        settings.update_fields(empty_publication_settings())
        self.assertEqual(settings.snapshot()["published_destination"], {})

    def test_raw_title_path_is_encoded_once_and_docs_is_not_url_prefix(self):
        destination = replace(self.destination, source="/docs")
        self.assertEqual(destination.prefix, "docs/")
        self.assertEqual(destination.url("Valley & Shepard"),
                         "https://michael.github.io/Letters-to-Keep/Valley%20%26%20Shepard/")
        self.assertEqual(public_path("ありがとう"), "ありがとう")
        for invalid in ("../a", "a/b", "a\\b", "CON.txt", "a%2Fb", "end.", " trailing", "a\u202eb"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                public_path(invalid)

    def test_collision_checks_include_root_files_and_unicode_equivalents(self):
        from publishing.github_pages import _allocate_public_path
        self.assertEqual(_allocate_public_path("hello", ["hello"]), "hello-2")
        self.assertEqual(_allocate_public_path("Café", ["CAFE\u0301/index.html"]), "Café-2")

    def test_setup_requires_confirmation_and_preserves_existing_source(self):
        calls = []
        def request(method, path, payload=None, **kwargs):
            calls.append((method, path, payload))
            if path.endswith("/pages"):
                return {"html_url": "https://letters.example", "build_type": "legacy",
                        "source": {"branch": "published", "path": "/docs"}}
            return {"id": 21, "name": "Letters-to-Keep", "owner": {"login": "michael", "id": 7},
                    "permissions": {"push": True, "admin": True}, "default_branch": "main"}
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        plan = service.inspect("Letters-to-Keep")
        with self.assertRaises(ValueError):
            service.configure(plan)
        configured = service.configure(plan, confirmed=True)
        self.assertEqual((configured.branch, configured.source), ("published", "/docs"))
        self.assertEqual(configured.url("Hello"), "https://letters.example/Hello/")
        self.assertTrue(all(c[0] == "GET" for c in calls))

    def test_no_creation_during_preview_and_creation_conflict_is_not_reused(self):
        calls = []
        def request(method, path, payload=None, **kwargs):
            calls.append(method)
            if method == "GET":
                raise GitHubOperationError("not_found", "Missing", status=404)
            raise GitHubOperationError("validation", "Name already exists", status=422)
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        plan = service.inspect("Letters-to-Keep")
        self.assertEqual(calls, ["GET"])
        with self.assertRaises(GitHubOperationError):
            service.configure(plan, confirmed=True)
        self.assertIsNone(self.store.default(7))

    def test_repository_name_reuse_is_rejected_before_upload(self):
        def request(method, path, payload=None, **kwargs):
            if path.endswith("/pages"):
                return {"html_url": self.destination.base_url, "source": {"branch": "main", "path": "/"}}
            return {"id": 22, "name": "Letters-to-Keep", "owner": {"login": "michael", "id": 7},
                    "permissions": {"push": True}}
        service = GitHubCollections(self.session, SimpleNamespace(request=request), self.store)
        with self.assertRaisesRegex(ValueError, "different repository"):
            service.validate(self.destination)


class RepositorySetupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_startup_initializes_lazy_tabs_before_repository_setup(self):
        from unittest import mock
        from Nexus import Nexus
        setup = mock.Mock()
        window = SimpleNamespace()
        def initialize():
            window.forge_tab = SimpleNamespace(start_repository_setup=setup)
        window._initialize_project_tabs = initialize
        Nexus.start_repository_setup(window)
        setup.assert_called_once_with()

    def test_setup_starts_blank_and_name_changes_require_new_confirmation(self):
        from PySide6 import QtWidgets
        from publishing.collections_ui import CollectionDialog
        from publishing.github_collections import CollectionPlan
        with tempfile.TemporaryDirectory() as folder:
            session = SimpleNamespace(account=SimpleNamespace(login="michael", account_id=7))
            dialog = CollectionDialog(session, None, Path(folder))
            try:
                self.assertEqual(dialog.repository.text(), "")
                self.assertFalse(dialog.confirm.isEnabled())
                self.assertFalse(dialog.findChildren(QtWidgets.QComboBox))
                dialog.run_task = lambda task, done: done(task())
                destination = PublishingDestination(7, "michael", "my_letter_repository")
                from unittest import mock
                dialog.service.inspect = mock.Mock(return_value=CollectionPlan(destination, False, False))
                dialog.repository.setText("my_letter_repository")
                dialog._review()
                dialog.service.inspect.assert_called_once_with("my_letter_repository", "My Letter Repository")
                self.assertTrue(dialog.confirm.isEnabled())
                dialog.repository.setText("another_name")
                self.assertFalse(dialog.confirm.isEnabled())
                self.assertIsNone(dialog.plan)
                self.assertEqual(dialog.preview.text(), "")
            finally:
                dialog.close()

    def test_initial_connection_offers_setup_once_per_session(self):
        from unittest import mock
        from PySide6 import QtCore, QtTest, QtWidgets
        from Forge_Tab import ForgeTab
        from publishing.github_auth import GitHubAccount, GitHubToken, GitHubSession, GitHubConnectionSnapshot, GitHubConnectionState
        from publishing.collections_ui import CollectionDialog
        with tempfile.TemporaryDirectory() as folder:
            session = GitHubSession(GitHubAccount("michael", 7), GitHubToken("test-only"))
            snapshot = GitHubConnectionSnapshot(GitHubConnectionState.CONNECTED, session=session)
            connection = SimpleNamespace(snapshot=snapshot, subscribe=mock.Mock(), unsubscribe=mock.Mock(),
                                         restore=lambda: snapshot, authorized_api=lambda _session: None)
            forge = ForgeTab(Path(folder), github_service=connection)
            try:
                with mock.patch.object(CollectionDialog, "exec", return_value=QtWidgets.QDialog.Rejected) as prompt:
                    forge.start_repository_setup()
                    deadline = QtCore.QDeadlineTimer(5000)
                    while not deadline.hasExpired() and not prompt.called:
                        self.app.processEvents()
                        QtTest.QTest.qWait(10)
                    self.assertEqual(prompt.call_count, 1)
                    forge._apply_github_state(snapshot)
                    QtTest.QTest.qWait(100)
                    self.assertEqual(prompt.call_count, 1)
                    self.assertFalse(hasattr(forge, "publishing_collection_btn"))
            finally:
                forge.shutdown(timeout_ms=5000)
                forge.close()
                forge.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
