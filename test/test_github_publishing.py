from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets
from keyring.errors import PasswordDeleteError
from Main import redact_sensitive_diagnostics

from publishing.expiration import GITHUB_PAGES_PROVIDER_ID, publication_status
from publishing.github_auth import (
    GitHubAPI,
    GitHubAccount,
    GitHubAuthenticator,
    GitHubCredentialStore,
    GitHubDeviceAuthorization,
    GitHubOperationError,
    GitHubSession,
    GitHubToken,
)
from publishing.github_config import GitHubApplicationConfiguration
from publishing.github_pages import (
    REPOSITORY_MARKER,
    GitHubPagesPublisher,
    _publication_path,
)
from publishing.github_ui import GitHubAccountDialog, GitHubDeviceFlowDialog
from settings_store import (
    PUBLICATION_PROVIDER_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    SettingsStore,
)


class _MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        try:
            del self.values[(service, account)]
        except KeyError as error:
            raise PasswordDeleteError("missing") from error


class _SequenceAPI:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, payload=None, **_kwargs) -> dict:
        self.calls.append((method, path, payload))
        if not self.responses:
            raise AssertionError(f"Unexpected GitHub request: {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _PublishingAPI:
    def __init__(
        self,
        publication_id: str,
        *,
        repository_exists: bool = False,
        pages_exists: bool = False,
        existing_tree: list[dict] | None = None,
    ) -> None:
        self.publication_id = publication_id
        self.repository_exists = repository_exists
        self.pages_exists = pages_exists
        self.existing_tree = list(existing_tree or [])
        self.calls: list[tuple[str, str, dict | None]] = []
        self.blobs: dict[str, bytes] = {}
        self.remote_files: dict[str, bytes] = {}
        self.last_tree_updates: list[dict] = []
        self._blob_number = 0

    def request(self, method: str, path: str, payload=None, **_kwargs) -> dict:
        self.calls.append((method, path, payload))
        if method == "GET" and path.startswith("/repos/ada/LetterSmith-Published"):
            if path == "/repos/ada/LetterSmith-Published":
                if not self.repository_exists:
                    raise GitHubOperationError("not_found", "missing", status=404)
                return self._repository()
            if "/git/ref/heads/" in path:
                return {"object": {"sha": "old-commit"}}
            if "/git/commits/old-commit" in path:
                return {"tree": {"sha": "old-tree"}}
            if "/git/trees/old-tree" in path:
                return {"tree": self.existing_tree, "truncated": False}
            if path.endswith("/pages"):
                if not self.pages_exists:
                    raise GitHubOperationError("not_found", "missing", status=404)
                return {
                    "html_url": "https://ada.github.io/LetterSmith-Published",
                    "source": {"branch": "main", "path": "/"},
                }
        if method == "POST" and path == "/user/repos":
            self.repository_exists = True
            return self._repository()
        if method == "POST" and path.endswith("/git/blobs"):
            self._blob_number += 1
            sha = f"blob-{self._blob_number}"
            self.blobs[sha] = base64.b64decode(payload["content"])
            return {"sha": sha}
        if method == "POST" and path.endswith("/git/trees"):
            self.last_tree_updates = list(payload["tree"])
            for entry in self.last_tree_updates:
                name = entry["path"]
                sha = entry.get("sha")
                if sha is None:
                    self.remote_files.pop(name, None)
                else:
                    self.remote_files[name] = self.blobs[sha]
            return {"sha": "new-tree"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "new-commit"}
        if method == "PATCH" and "/git/refs/heads/" in path:
            return {"object": {"sha": payload["sha"]}}
        if method == "POST" and path.endswith("/pages"):
            self.pages_exists = True
            return {"html_url": "https://ada.github.io/LetterSmith-Published"}
        raise AssertionError(f"Unexpected GitHub request: {method} {path}")

    @staticmethod
    def _repository() -> dict:
        return {
            "name": "LetterSmith-Published",
            "default_branch": "main",
            "private": False,
            "archived": False,
            "disabled": False,
        }

    def public_fetch(self, url: str, _timeout: float) -> bytes | None:
        relative = url.split("/LetterSmith-Published/", 1)[-1].split("?", 1)[0]
        return self.remote_files.get(relative)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class GitHubAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = GitHubApplicationConfiguration(
            client_id="Iv1.1234567890abcdef",
            app_slug="letter-smith",
        )
        self.keyring = _MemoryKeyring()
        self.store = GitHubCredentialStore(keyring_module=self.keyring)

    def test_credentials_round_trip_only_through_keyring(self) -> None:
        session = GitHubSession(
            GitHubAccount("ada", 7, "https://avatars.example/ada"),
            GitHubToken("ghu_access", "ghr_refresh", 1200, 2400),
        )
        self.store.save(session)

        restored = self.store.load()

        self.assertEqual(restored.account.login, "ada")
        self.assertEqual(restored.token.refresh_token, "ghr_refresh")
        self.store.clear()
        self.assertIsNone(self.store.load())

    def test_restart_validation_and_sign_out_use_secure_store(self) -> None:
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_restart_token"),
        )
        self.store.save(session)
        account_api = _SequenceAPI([{"login": "ada", "id": 7}])
        restarted = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: account_api,
        )

        restored = restarted.validate_stored()

        self.assertEqual(restored.account.login, "ada")
        restarted.sign_out()
        self.assertIsNone(self.store.load())

    def test_diagnostics_redact_github_tokens_and_device_codes(self) -> None:
        text = redact_sensitive_diagnostics(
            "access=ghu_1234567890abcdefghijkl device_code=secret-device-code "
            "user_code=ABCD-EFGH"
        )

        self.assertNotIn("ghu_1234567890abcdefghijkl", text)
        self.assertNotIn("secret-device-code", text)
        self.assertNotIn("ABCD-EFGH", text)

    def test_device_flow_respects_slow_down_and_validates_identity(self) -> None:
        oauth_api = _SequenceAPI(
            [
                {"error": "slow_down", "interval": 7},
                {"error": "authorization_pending"},
                {
                    "access_token": "ghu_access",
                    "refresh_token": "ghr_refresh",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 7200,
                },
            ]
        )
        account_api = _SequenceAPI([{"login": "ada", "id": 7}])
        elapsed: list[float] = []
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            api=oauth_api,
            authorized_api_factory=lambda _token: account_api,
            sleeper=elapsed.append,
            now=lambda: 100.0,
        )
        authorization = GitHubDeviceAuthorization(
            "device-code",
            "ABCD-EFGH",
            "https://github.com/login/device",
            60,
            2,
        )

        session = authenticator.poll_device_authorization(authorization)

        self.assertEqual(session.account.login, "ada")
        self.assertAlmostEqual(sum(elapsed), 16.0)
        self.assertEqual(self.store.load().token.access_token, "ghu_access")

    def test_cancelled_and_expired_device_codes_are_recoverable(self) -> None:
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            api=_SequenceAPI([]),
            sleeper=lambda _seconds: None,
        )
        authorization = GitHubDeviceAuthorization(
            "device-code", "ABCD-EFGH", "https://github.com/login/device", 1, 1
        )
        with self.assertRaisesRegex(GitHubOperationError, "canceled"):
            authenticator.poll_device_authorization(
                authorization,
                cancelled=lambda: True,
            )

        pending = _SequenceAPI([{"error": "authorization_pending"}])
        authenticator.api = pending
        with self.assertRaisesRegex(GitHubOperationError, "expired"):
            authenticator.poll_device_authorization(authorization)

    def test_revoked_stored_authorization_is_cleared(self) -> None:
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_revoked"),
        )
        self.store.save(session)
        rejected = _SequenceAPI(
            [GitHubOperationError("authentication", "expired", status=401)]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: rejected,
        )

        self.assertIsNone(authenticator.validate_stored())
        self.assertIsNone(self.store.load())

    def test_network_failure_has_a_local_work_safe_message(self) -> None:
        def unavailable(_request, **_kwargs):
            raise urllib.error.URLError("offline")

        with self.assertRaisesRegex(GitHubOperationError, "saved locally"):
            GitHubAPI("ghu_example", opener=unavailable).request("GET", "/user")


class GitHubPublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configuration = GitHubApplicationConfiguration(
            client_id="Iv1.1234567890abcdef",
            app_slug="letter-smith",
        )
        self.session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )

    @staticmethod
    def _build(root: Path) -> Path:
        build = root / "Play"
        (build / "gallery/message/revisions").mkdir(parents=True)
        (build / "index.html").write_text("<html>letter</html>", encoding="utf-8")
        (build / "script.js").write_text("console.log('letter')", encoding="utf-8")
        (build / "lettersmith-build.json").write_text("{}", encoding="utf-8")
        (build / "lettersmith-metadata.json").write_text("{}", encoding="utf-8")
        (build / "prompt_writer_state.json").write_text("{}", encoding="utf-8")
        (build / "gallery/message/message.html").write_text("hello", encoding="utf-8")
        (build / "gallery/message/revisions/private.html").write_text(
            "old draft", encoding="utf-8"
        )
        return build

    def test_first_publish_uses_one_managed_repository_and_filters_editable_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            project_id = str(uuid.uuid4())
            api = _PublishingAPI(project_id)
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            )

            result = publisher.publish(
                build,
                {"project_id": project_id, "source_fingerprint": "source-1"},
            )

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.public_path, project_id)
            self.assertEqual(
                result.url,
                f"https://ada.github.io/LetterSmith-Published/letters/{project_id}/",
            )
            prefix = f"letters/{project_id}/"
            self.assertIn(f"{prefix}index.html", api.remote_files)
            self.assertIn(f"{prefix}gallery/message/message.html", api.remote_files)
            self.assertNotIn(f"{prefix}lettersmith-metadata.json", api.remote_files)
            self.assertNotIn(f"{prefix}prompt_writer_state.json", api.remote_files)
            self.assertNotIn(
                f"{prefix}gallery/message/revisions/private.html", api.remote_files
            )
            self.assertIn(REPOSITORY_MARKER, api.remote_files)
            settings = SettingsStore(root).snapshot()
            self.assertEqual(settings[PUBLICATION_PROVIDER_KEY], GITHUB_PAGES_PROVIDER_ID)
            self.assertEqual(settings[PUBLISHED_GITHUB_OWNER_KEY], "ada")
            self.assertEqual(
                settings[PUBLISHED_GITHUB_REPOSITORY_KEY], "LetterSmith-Published"
            )
            self.assertEqual(publication_status(settings), "published")

    def test_republish_keeps_path_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            public_path = str(uuid.uuid4())
            prefix = f"letters/{public_path}/"
            tree = [
                {"path": REPOSITORY_MARKER, "type": "blob", "sha": "owner"},
                {"path": f"{prefix}index.html", "type": "blob", "sha": "old"},
                {"path": f"{prefix}stale.txt", "type": "blob", "sha": "stale"},
            ]
            api = _PublishingAPI(
                public_path,
                repository_exists=True,
                pages_exists=True,
                existing_tree=tree,
            )
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            )

            result = publisher.publish(
                build,
                {
                    "project_id": str(uuid.uuid4()),
                    PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                    "published_public_path": public_path,
                    "source_fingerprint": "source-2",
                },
            )

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.public_path, public_path)
            deletion = next(
                item
                for item in api.last_tree_updates
                if item["path"] == f"{prefix}stale.txt"
            )
            self.assertIsNone(deletion["sha"])

    def test_publication_timeout_never_marks_settings_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            project_id = str(uuid.uuid4())
            api = _PublishingAPI(project_id)
            clock = _Clock()
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=lambda _url, _timeout: None,
                sleeper=clock.sleep,
                clock=clock.now,
                verification_timeout=1,
            )

            result = publisher.publish(
                build,
                {"project_id": project_id, "source_fingerprint": "source-3"},
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "publication_timeout")
            self.assertEqual(SettingsStore(root).get(PUBLISHED_PAGE_URL_KEY), "")

    def test_project_identity_produces_stable_distinct_paths(self) -> None:
        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        self.assertEqual(_publication_path({"project_id": first}), first)
        self.assertEqual(_publication_path({"project_id": first}), first)
        self.assertNotEqual(
            _publication_path({"project_id": first}),
            _publication_path({"project_id": second}),
        )

    def test_two_letters_publish_to_distinct_paths_in_the_same_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            first = str(uuid.uuid4())
            second = str(uuid.uuid4())
            first_api = _PublishingAPI(first)
            first_result = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=first_api,
                public_fetcher=first_api.public_fetch,
            ).publish(
                build,
                {"project_id": first, "source_fingerprint": "source-first"},
            )
            second_api = _PublishingAPI(
                second,
                repository_exists=True,
                pages_exists=True,
                existing_tree=[
                    {"path": REPOSITORY_MARKER, "type": "blob", "sha": "owner"}
                ],
            )
            second_result = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=second_api,
                public_fetcher=second_api.public_fetch,
            ).publish(
                build,
                {"project_id": second, "source_fingerprint": "source-second"},
            )

            self.assertTrue(first_result.success, first_result.message)
            self.assertTrue(second_result.success, second_result.message)
            self.assertNotEqual(first_result.url, second_result.url)
            self.assertIn(f"letters/{first}/index.html", first_api.remote_files)
            self.assertIn(f"letters/{second}/index.html", second_api.remote_files)

    def test_old_hosting_settings_are_removed_without_touching_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text(
                json.dumps(
                    {
                        "starting_volume": 42,
                        "r2_account_id": "old-account",
                        "r2_bucket": "old-bucket",
                        "r2_public_base_url": "https://old.example",
                    }
                ),
                encoding="utf-8",
            )

            settings = SettingsStore(root).snapshot()

            self.assertEqual(settings["starting_volume"], 42)
            self.assertNotIn("r2_account_id", settings)
            self.assertNotIn("r2_bucket", settings)
            self.assertNotIn("r2_public_base_url", settings)


class GitHubAccountUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_account_dialog_never_exposes_credentials(self) -> None:
        dialog = GitHubAccountDialog()
        dialog.set_account(GitHubAccount("ada", 7))

        self.assertEqual(
            dialog.status_label.text(),
            "GitHub connected\nSigned in as ada",
        )
        self.assertEqual(
            dialog.status_label.property("connectionState"),
            "connected",
        )
        self.assertNotIn("token", dialog.status_label.text().casefold())
        self.assertFalse(dialog._watermark.isNull())
        self.assertEqual(
            dialog.sign_in_button.objectName(),
            "GitHubSignInButton",
        )
        self.assertFalse(dialog.sign_in_button.isHidden())
        self.assertEqual(
            dialog.sign_in_button.text(),
            "Disconnect from GitHub",
        )
        self.assertEqual(
            dialog.sign_in_button.property("accountAction"),
            "disconnect",
        )
        self.assertIn("#39d98a", dialog.styleSheet())
        self.assertIn("#ff6b72", dialog.styleSheet())
        dialog.close()

    def test_account_dialog_is_a_single_action_frameless_popup(self) -> None:
        dialog = GitHubAccountDialog()
        try:
            self.assertTrue(dialog.windowFlags() & QtCore.Qt.Popup)
            self.assertTrue(
                dialog.windowFlags() & QtCore.Qt.FramelessWindowHint
            )
            buttons = dialog.findChildren(QtWidgets.QPushButton)
            self.assertEqual(buttons, [dialog.sign_in_button])
            self.assertEqual(
                dialog.sign_in_button.text(),
                "Sign in with GitHub",
            )
        finally:
            dialog.close()

    def test_disconnected_github_status_is_explicit(self) -> None:
        dialog = GitHubAccountDialog()
        try:
            self.assertEqual(
                dialog.status_label.text(),
                "GitHub not connected",
            )
            self.assertEqual(
                dialog.status_label.property("connectionState"),
                "disconnected",
            )
            self.assertEqual(
                dialog.sign_in_button.text(),
                "Sign in with GitHub",
            )
        finally:
            dialog.close()

    def test_account_dialog_routes_its_single_button_by_connection(self) -> None:
        dialog = GitHubAccountDialog()
        requests: list[str] = []
        dialog.sign_in_requested.connect(lambda: requests.append("sign-in"))
        dialog.sign_out_requested.connect(lambda: requests.append("disconnect"))
        try:
            dialog.sign_in_button.click()
            dialog.set_account(GitHubAccount("ada", 7))
            dialog.sign_in_button.click()
            self.assertEqual(requests, ["sign-in", "disconnect"])
        finally:
            dialog.close()

    def test_device_flow_dialog_has_no_title_bar(self) -> None:
        dialog = GitHubDeviceFlowDialog()
        try:
            self.assertTrue(
                dialog.windowFlags() & QtCore.Qt.FramelessWindowHint
            )
            self.assertEqual(dialog.windowTitle(), "")
            self.assertEqual(
                dialog.accessibleName(),
                "Sign in with GitHub",
            )
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
