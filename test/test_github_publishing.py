from __future__ import annotations

import base64
import io
import json
import os
import ssl
import tempfile
import threading
import unittest
import urllib.error
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from keyring.errors import PasswordDeleteError
from Main import _resolve_startup_choices, redact_sensitive_diagnostics

from publishing.expiration import GITHUB_PAGES_PROVIDER_ID, publication_status
from publishing.github_auth import (
    GitHubAPI,
    GitHubAccount,
    GitHubAuthenticator,
    GitHubConnectionService,
    GitHubConnectionSnapshot,
    GitHubConnectionState,
    GitHubCredentialStore,
    GitHubCredentialType,
    GitHubDeviceAuthorization,
    GitHubFailureKind,
    GitHubOperationError,
    GitHubPublishingAccess,
    GitHubSession,
    GitHubToken,
    classify_github_error,
)
from publishing.github_config import GitHubApplicationConfiguration
from publishing.github_pages import (
    REPOSITORY_MARKER,
    GitHubPagesPublisher,
    _git_blob_sha,
    _publication_path,
    _published_files,
)
from publishing.github_ui import (
    GitHubAccountDialog,
    GitHubAuthenticationDialog,
    GitHubDeviceFlowDialog,
    GitHubStartupDialog,
    prompt_for_github_startup,
)
from project_timestamps import PROJECT_PUBLISHED_AT_KEY
from ui_theme import THEMES
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
        owner: str = "ada",
        repository_exists: bool = False,
        pages_exists: bool = False,
        existing_tree: list[dict] | None = None,
        existing_remote_files: dict[str, bytes] | None = None,
    ) -> None:
        self.publication_id = publication_id
        self.owner = owner
        self.repository_name = f"{owner}.github.io"
        self.repository_exists = repository_exists
        self.pages_exists = pages_exists
        self.existing_tree = list(existing_tree or [])
        self.calls: list[tuple[str, str, dict | None]] = []
        self._lock = threading.Lock()
        self.blobs: dict[str, bytes] = {}
        self.remote_files: dict[str, bytes] = dict(existing_remote_files or {})
        self.last_tree_updates: list[dict] = []
        for entry in self.existing_tree:
            path = str(entry.get("path", ""))
            sha = str(entry.get("sha", ""))
            if path in self.remote_files and sha:
                self.blobs[sha] = self.remote_files[path]

    def request(self, method: str, path: str, payload=None, **_kwargs) -> dict:
        with self._lock:
            self.calls.append((method, path, payload))
        repository_path = f"/repos/{self.owner}/{self.repository_name}"
        if method == "GET" and path.startswith(repository_path):
            if path == repository_path:
                if not self.repository_exists:
                    raise GitHubOperationError("not_found", "missing", status=404)
                return self._repository()
            if "/git/ref/heads/" in path:
                return {"object": {"sha": "old-commit"}}
            if "/git/commits/old-commit" in path:
                return {"tree": {"sha": "old-tree"}}
            if "/git/trees/old-tree" in path:
                return {"tree": self.existing_tree, "truncated": False}
            if "/git/blobs/" in path:
                sha = path.rsplit("/", 1)[-1]
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(self.blobs[sha]).decode("ascii"),
                }
            if path.endswith("/pages"):
                if not self.pages_exists:
                    raise GitHubOperationError("not_found", "missing", status=404)
                return {
                    "html_url": f"https://{self.owner.casefold()}.github.io",
                    "source": {"branch": "main", "path": "/"},
                }
        if method == "POST" and path == "/user/repos":
            self.repository_exists = True
            return self._repository()
        if method == "POST" and path.endswith("/git/blobs"):
            content = base64.b64decode(payload["content"])
            sha = _git_blob_sha(content)
            with self._lock:
                self.blobs[sha] = content
            return {"sha": sha}
        if method == "POST" and path.endswith("/git/trees"):
            self.last_tree_updates = list(payload["tree"])
            entries = {
                str(entry.get("path", "")): dict(entry)
                for entry in self.existing_tree
                if isinstance(entry, dict)
            }
            for entry in self.last_tree_updates:
                name = entry["path"]
                sha = entry.get("sha")
                if "content" in entry:
                    content = str(entry["content"]).encode("utf-8")
                    sha = _git_blob_sha(content)
                    self.blobs[sha] = content
                    self.remote_files[name] = content
                    entries[name] = {
                        "path": name,
                        "type": "blob",
                        "sha": sha,
                    }
                elif sha is None:
                    self.remote_files.pop(name, None)
                    entries.pop(name, None)
                else:
                    self.remote_files[name] = self.blobs[sha]
                    entries[name] = {
                        "path": name,
                        "type": "blob",
                        "sha": sha,
                    }
            self.existing_tree = list(entries.values())
            return {"sha": "new-tree"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "new-commit"}
        if method == "PATCH" and "/git/refs/heads/" in path:
            return {"object": {"sha": payload["sha"]}}
        if method == "POST" and path.endswith("/pages"):
            self.pages_exists = True
            return {"html_url": f"https://{self.owner.casefold()}.github.io"}
        raise AssertionError(f"Unexpected GitHub request: {method} {path}")

    def _repository(self) -> dict:
        return {
            "name": self.repository_name,
            "default_branch": "main",
            "private": False,
            "archived": False,
            "disabled": False,
        }

    def public_fetch(self, url: str, _timeout: float) -> bytes | None:
        relative = url.split(f"{self.owner.casefold()}.github.io/", 1)[-1].split("?", 1)[0]
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
            installation_id=41,
            repository_owner="ada",
            repository_name="ada.github.io",
            repository_id=99,
            repository_selection="selected",
        )
        self.store.save(session)

        restored = self.store.load()

        self.assertEqual(restored.account.login, "ada")
        self.assertEqual(restored.token.refresh_token, "ghr_refresh")
        self.assertEqual(restored.installation_id, 41)
        self.assertEqual(restored.repository_id, 99)
        self.assertEqual(restored.connection_version, 2)
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

        with self.assertRaisesRegex(GitHubOperationError, "authorization was removed"):
            authenticator.validate_stored()
        self.assertIsNone(self.store.load())

    def test_network_failure_has_a_local_work_safe_message(self) -> None:
        def unavailable(_request, **_kwargs):
            raise urllib.error.URLError("offline")

        with self.assertRaisesRegex(GitHubOperationError, "saved locally"):
            GitHubAPI("ghu_example", opener=unavailable).request("GET", "/user")

    def test_expired_user_token_refreshes_without_user_interaction(self) -> None:
        self.store.save(
            GitHubSession(
                GitHubAccount("ada", 7),
                GitHubToken("ghu_old", "ghr_old", 50, 500),
                installation_id=41,
                repository_owner="ada",
                repository_name="ada.github.io",
            )
        )
        token_api = _SequenceAPI(
            [
                {
                    "access_token": "ghu_new",
                    "refresh_token": "ghr_new",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 7200,
                }
            ]
        )
        account_api = _SequenceAPI([{"login": "ada", "id": 7}])
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            api=token_api,
            authorized_api_factory=lambda _token: account_api,
            now=lambda: 100.0,
        )

        restored = authenticator.validate_stored()

        self.assertEqual(restored.token.access_token, "ghu_new")
        self.assertEqual(restored.installation_id, 41)
        self.assertEqual(self.store.load().token.refresh_token, "ghr_new")

    def test_temporary_github_503_reconnects_with_backoff(self) -> None:
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_token"))

        class Authenticator:
            calls = 0

            @staticmethod
            def stored_session() -> GitHubSession:
                return session

            def validate_stored(self) -> GitHubSession:
                self.calls += 1
                if self.calls == 1:
                    raise GitHubOperationError(
                        "github_unavailable",
                        "GitHub is unavailable.",
                        status=503,
                    )
                return session

            @staticmethod
            def publishing_access(_session) -> GitHubPublishingAccess:
                return GitHubPublishingAccess(True, "ready")

        service = GitHubConnectionService(Authenticator())

        unavailable = service.restore()
        connected = service.restore()

        self.assertEqual(unavailable.state, GitHubConnectionState.RECONNECTING)
        self.assertEqual(unavailable.session, session)
        self.assertEqual(unavailable.retry_attempt, 1)
        self.assertEqual(unavailable.retry_after_seconds, 2.0)
        self.assertEqual(connected.state, GitHubConnectionState.CONNECTED)

    def test_network_failure_preserves_durable_connection(self) -> None:
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_token"),
            installation_id=41,
            repository_owner="ada",
            repository_name="ada.github.io",
        )
        self.store.save(session)

        class Authenticator:
            credential_store = self.store

            def stored_session(self) -> GitHubSession:
                return self.credential_store.load().session()

            @staticmethod
            def validate_stored() -> GitHubSession:
                raise GitHubOperationError(
                    "network",
                    "GitHub could not be reached.",
                )

        service = GitHubConnectionService(Authenticator())

        snapshot = service.restore()

        self.assertEqual(snapshot.state, GitHubConnectionState.RECONNECTING)
        self.assertEqual(snapshot.session.installation_id, 41)
        self.assertIsNotNone(self.store.load())

    def test_rate_limit_retry_after_is_preserved(self) -> None:
        def limited(request, **_kwargs):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "47"},
                io.BytesIO(b'{"message":"rate limit exceeded"}'),
            )

        with self.assertRaises(GitHubOperationError) as raised:
            GitHubAPI("ghu_example", opener=limited).request("GET", "/user")

        self.assertEqual(raised.exception.code, "rate_limit")
        self.assertEqual(raised.exception.retry_after_seconds, 47.0)
        self.assertEqual(
            classify_github_error(raised.exception),
            GitHubFailureKind.TEMPORARY,
        )

    def test_tls_failure_has_actionable_certificate_message(self) -> None:
        def unavailable(_request, **_kwargs):
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
            )

        with self.assertRaises(GitHubOperationError) as raised:
            GitHubAPI(opener=unavailable).request(
                "POST",
                "https://github.com/login/device/code",
                {"client_id": "client"},
                authenticate=False,
                form=True,
            )

        self.assertEqual(raised.exception.code, "network")
        self.assertIn("certificate", raised.exception.user_message.casefold())
        self.assertIn("CERTIFICATE_VERIFY_FAILED", raised.exception.technical_details)

    def test_secondary_rate_limit_without_headers_waits_one_minute(self) -> None:
        def limited(request, **_kwargs):
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "rate limited",
                {},
                io.BytesIO(b'{"message":"secondary rate limit exceeded"}'),
            )

        with self.assertRaises(GitHubOperationError) as raised:
            GitHubAPI("ghu_example", opener=limited).request("GET", "/user")

        self.assertEqual(raised.exception.code, "rate_limit")
        self.assertEqual(raised.exception.retry_after_seconds, 60.0)

    def test_brand_new_connection_finishes_one_installation_flow(self) -> None:
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_token"))
        install = GitHubPublishingAccess(
            False,
            "installation_required",
            setup_url="https://github.com/apps/letter-smith/installations/new",
        )

        class Authenticator:
            checks = 0

            @staticmethod
            def begin_device_authorization() -> GitHubDeviceAuthorization:
                return GitHubDeviceAuthorization(
                    "device",
                    "ABCD-EFGH",
                    "https://github.com/login/device",
                    60,
                    2,
                )

            @staticmethod
            def poll_device_authorization(_authorization, *, cancelled):
                del cancelled
                return session

            def publishing_access(self, _session) -> GitHubPublishingAccess:
                self.checks += 1
                return install if self.checks == 1 else GitHubPublishingAccess(True, "ready")

            @staticmethod
            def wait_for_publishing_access(_session, *, cancelled):
                del cancelled
                return session

        service = GitHubConnectionService(Authenticator())
        installation_requests: list[GitHubPublishingAccess] = []

        snapshot = service.authorize(
            authorization_ready=lambda _authorization: None,
            publishing_access_required=installation_requests.append,
        )

        self.assertEqual(snapshot.state, GitHubConnectionState.CONNECTED)
        self.assertEqual(installation_requests, [install])

    def test_missing_installation_and_repository_access_are_action_states(self) -> None:
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_token"))

        for access, expected_state in (
            (
                GitHubPublishingAccess(
                    False,
                    "installation_required",
                    setup_url="https://github.com/apps/letter-smith/installations/new",
                ),
                GitHubConnectionState.INSTALL_REQUIRED,
            ),
            (
                GitHubPublishingAccess(
                    False,
                    "repository_access",
                    setup_url="https://github.com/settings/installations/41",
                    installation_id=41,
                ),
                GitHubConnectionState.ACTION_REQUIRED,
            ),
        ):
            with self.subTest(code=access.code):
                authenticator = SimpleNamespace(
                    stored_session=lambda: session,
                    validate_stored=lambda: session,
                    publishing_access=lambda _session: access,
                )
                snapshot = GitHubConnectionService(authenticator).restore()
                self.assertEqual(snapshot.state, expected_state)
                self.assertEqual(snapshot.retry_attempt, 0)

    def test_installation_confirmation_polling_is_bounded_and_cancelable(self) -> None:
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            sleeper=lambda _seconds: None,
        )
        access = GitHubPublishingAccess(
            False,
            "installation_permissions",
            setup_url="https://github.com/settings/installations/41",
        )
        authenticator.publishing_access = mock.Mock(return_value=access)
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_token"))

        with self.assertRaisesRegex(GitHubOperationError, "not completed in time"):
            authenticator.wait_for_publishing_access(
                session,
                timeout_seconds=4,
            )
        self.assertEqual(authenticator.publishing_access.call_count, 3)

        with self.assertRaisesRegex(GitHubOperationError, "canceled"):
            authenticator.wait_for_publishing_access(
                session,
                cancelled=lambda: True,
            )

    def test_stale_connection_result_cannot_overwrite_newer_state(self) -> None:
        service = GitHubConnectionService(SimpleNamespace())
        old_generation = service._next_generation()
        new_generation = service._next_generation()
        connected = GitHubConnectionSnapshot(GitHubConnectionState.CONNECTED)
        service._set_snapshot(connected, generation=new_generation)

        result = service._set_snapshot(
            GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED),
            generation=old_generation,
        )

        self.assertEqual(result.state, GitHubConnectionState.CONNECTED)
        self.assertEqual(service.snapshot.state, GitHubConnectionState.CONNECTED)

        stale_failure = service.record_operation_error(
            GitHubOperationError("network", "offline"),
            GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_token")),
            expected_generation=old_generation,
        )
        self.assertEqual(stale_failure.state, GitHubConnectionState.CONNECTED)

    def test_publishing_access_requires_actual_app_permissions(self) -> None:
        registration_api = _SequenceAPI([{"permissions": {}}])
        installation_api = _SequenceAPI(
            [
                {
                    "installations": [
                        {
                            "id": 41,
                            "app_slug": "letter-smith",
                            "account": {"id": 7},
                            "repository_selection": "selected",
                            "permissions": {},
                            "html_url": "https://github.com/settings/installations/41",
                        }
                    ]
                }
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            api=registration_api,
            authorized_api_factory=lambda _token: installation_api,
        )
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("token"))

        access = authenticator.publishing_access(session)

        self.assertIsInstance(access, GitHubPublishingAccess)
        self.assertFalse(access.ready)
        self.assertEqual(access.code, "app_permissions_not_configured")
        self.assertEqual(access.setup_url, "")
        self.assertIn("developer", access.message)
        self.assertNotIn("Administration", access.message)
        self.assertNotIn("Pages", access.message)

    def test_installation_can_approve_registered_permission_update(self) -> None:
        registration_api = _SequenceAPI(
            [
                {
                    "permissions": {
                        "administration": "write",
                        "contents": "write",
                        "pages": "write",
                    }
                }
            ]
        )
        installation_api = _SequenceAPI(
            [
                {
                    "installations": [
                        {
                            "id": 41,
                            "app_slug": "letter-smith",
                            "account": {"id": 7},
                            "repository_selection": "selected",
                            "permissions": {},
                            "html_url": "https://github.com/settings/installations/41",
                        }
                    ]
                }
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            api=registration_api,
            authorized_api_factory=lambda _token: installation_api,
        )
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("token"))

        access = authenticator.publishing_access(session)

        self.assertFalse(access.ready)
        self.assertEqual(access.code, "installation_permissions")
        self.assertEqual(
            access.setup_url,
            "https://github.com/settings/installations/41",
        )

    def test_unconfigured_app_permissions_are_not_polled(self) -> None:
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("token"))
        access = GitHubPublishingAccess(
            False,
            "app_permissions_not_configured",
            message="Developer action is required.",
        )
        authenticator = SimpleNamespace(
            wait_for_publishing_access=mock.Mock(),
        )
        service = GitHubConnectionService(authenticator)
        service._set_snapshot(
            GitHubConnectionSnapshot(
                GitHubConnectionState.AUTHENTICATED_INSUFFICIENT_PERMISSION,
                session=session,
                access=access,
            )
        )

        with self.assertRaisesRegex(GitHubOperationError, "Developer action"):
            service.complete_publishing_access(
                publishing_access_required=mock.Mock(),
            )

        authenticator.wait_for_publishing_access.assert_not_called()
        self.assertEqual(
            service.snapshot.state,
            GitHubConnectionState.AUTHENTICATED_INSUFFICIENT_PERMISSION,
        )

    def test_oauth_scopes_and_github_app_permissions_are_not_mixed(self) -> None:
        oauth_configuration = GitHubApplicationConfiguration(
            client_id="Ov1.1234567890abcdef",
            app_slug="letter-smith",
            credential_model="oauth_app",
        )
        api = _SequenceAPI([])
        authenticator = GitHubAuthenticator(
            oauth_configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: api,
        )
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken(
                "gho_access",
                credential_type=GitHubCredentialType.OAUTH_APP,
            ),
        )

        access = authenticator.publishing_access(session)

        self.assertFalse(access.ready)
        self.assertEqual(access.code, "oauth_scope_missing")
        self.assertEqual(api.calls, [])

    def test_oauth_repo_scope_can_create_missing_target_repository(self) -> None:
        oauth_configuration = GitHubApplicationConfiguration(
            client_id="Ov1.1234567890abcdef",
            app_slug="letter-smith",
            credential_model="oauth_app",
        )
        api = _SequenceAPI(
            [GitHubOperationError("not_found", "missing", status=404)]
        )
        authenticator = GitHubAuthenticator(
            oauth_configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: api,
        )
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken(
                "gho_access",
                credential_type=GitHubCredentialType.OAUTH_APP,
                granted_scopes=("repo",),
            ),
        )

        access = authenticator.publishing_access(session)

        self.assertTrue(access.ready)
        self.assertEqual(
            api.calls,
            [("GET", "/repos/ada/ada.github.io", None)],
        )

    def test_connection_service_reuses_one_active_device_flow(self) -> None:
        authorization = GitHubDeviceAuthorization(
            "device-code",
            "ABCD-EFGH",
            "https://github.com/login/device",
            60,
            1,
        )
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        polling = threading.Event()
        release = threading.Event()

        class Authenticator:
            begin_count = 0

            def begin_device_authorization(self):
                self.begin_count += 1
                return authorization

            @staticmethod
            def poll_device_authorization(_authorization, *, cancelled):
                polling.set()
                while not release.wait(0.01):
                    if cancelled():
                        raise GitHubOperationError(
                            "authorization_cancelled",
                            "GitHub sign-in was canceled.",
                        )
                return session

            @staticmethod
            def publishing_access(_session):
                return GitHubPublishingAccess(True, "ready")

        authenticator = Authenticator()
        service = GitHubConnectionService(authenticator)
        results: list[GitHubConnectionSnapshot] = []

        def connect() -> None:
            results.append(
                service.authorize(
                    authorization_ready=lambda _authorization: None,
                    publishing_access_required=lambda _access: None,
                )
            )

        first = threading.Thread(target=connect)
        second = threading.Thread(target=connect)
        first.start()
        self.assertTrue(polling.wait(1.0))
        second.start()
        release.set()
        first.join(1.0)
        second.join(1.0)

        self.assertEqual(authenticator.begin_count, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(
            all(
                result.state == GitHubConnectionState.CONNECTED_READY
                for result in results
            )
        )

    def test_publishing_access_checks_selected_repository(self) -> None:
        installation = {
            "id": 41,
            "app_slug": "letter-smith",
            "account": {"id": 7},
            "repository_selection": "selected",
            "permissions": {
                "administration": "write",
                "contents": "write",
                "pages": "write",
            },
            "html_url": "https://github.com/settings/installations/41",
        }
        installation_api = _SequenceAPI(
            [
                {"installations": [installation]},
                {"repositories": [{"name": "ada.github.io"}]},
                {
                    "name": "ada.github.io",
                    "owner": {"login": "ada"},
                },
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: installation_api,
        )
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("token"))

        access = authenticator.publishing_access(session)

        self.assertTrue(access.ready)
        self.assertEqual(access.code, "ready")
        self.assertEqual(
            installation_api.calls[-2][1],
            "/user/installations/41/repositories?per_page=100",
        )

    def test_ready_installation_metadata_is_persisted_for_restart(self) -> None:
        self.store.save(
            GitHubSession(
                GitHubAccount("ada", 7),
                GitHubToken("ghu_restart"),
            )
        )
        api = _SequenceAPI(
            [
                {"login": "ada", "id": 7},
                {
                    "installations": [
                        {
                            "id": 41,
                            "app_slug": "letter-smith",
                            "account": {"id": 7},
                            "repository_selection": "selected",
                            "permissions": {
                                "administration": "write",
                                "contents": "write",
                                "pages": "write",
                            },
                            "html_url": "https://github.com/settings/installations/41",
                        }
                    ]
                },
                {"repositories": [{"name": "ada.github.io"}]},
                {
                    "id": 99,
                    "name": "ada.github.io",
                    "owner": {"login": "ada"},
                },
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: api,
        )

        snapshot = GitHubConnectionService(authenticator).restore()
        stored = self.store.load()

        self.assertEqual(snapshot.state, GitHubConnectionState.CONNECTED)
        self.assertEqual(stored.installation_id, 41)
        self.assertEqual(stored.repository_owner, "ada")
        self.assertEqual(stored.repository_name, "ada.github.io")
        self.assertEqual(stored.repository_id, 99)

    def test_selected_installation_can_create_its_missing_managed_repository(self) -> None:
        installation = {
            "id": 41,
            "app_slug": "letter-smith",
            "account": {"id": 7},
            "repository_selection": "selected",
            "permissions": {
                "administration": "write",
                "contents": "write",
                "pages": "write",
            },
            "html_url": "https://github.com/settings/installations/41",
        }
        installation_api = _SequenceAPI(
            [
                {"installations": [installation]},
                {"repositories": []},
                GitHubOperationError("not_found", "missing", status=404),
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=self.store,
            authorized_api_factory=lambda _token: installation_api,
        )
        session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("token"))

        access = authenticator.publishing_access(session)

        self.assertTrue(access.ready)
        self.assertEqual(access.code, "ready")


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
        (build / "gallery/pages").mkdir(parents=True)
        (build / "gallery/sounds").mkdir(parents=True)
        (build / "index.html").write_text("<html>letter</html>", encoding="utf-8")
        (build / "script.js").write_text("console.log('letter')", encoding="utf-8")
        (build / "lettersmith-build.json").write_text("{}", encoding="utf-8")
        (build / "lettersmith-metadata.json").write_text("{}", encoding="utf-8")
        (build / "prompt_writer_state.json").write_text("{}", encoding="utf-8")
        (build / "gallery/message/message.html").write_text("hello", encoding="utf-8")
        (build / "gallery/message/revisions/private.html").write_text(
            "old draft", encoding="utf-8"
        )
        (build / "gallery/pages/lettersmith-images.json").write_text(
            '{"source_sha256":"private-image-hash"}',
            encoding="utf-8",
        )
        (build / "gallery/sounds/lettersmith-sound.json").write_text(
            '{"original_name":"private-song.mp3","content_hash":"private-sound-hash"}',
            encoding="utf-8",
        )
        return build

    def test_public_file_inventory_excludes_private_runtime_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = self._build(Path(directory))

            public_paths = {
                relative
                for _path, relative, _size in _published_files(build)
            }

            self.assertNotIn(
                "gallery/pages/lettersmith-images.json",
                public_paths,
            )
            self.assertNotIn(
                "gallery/sounds/lettersmith-sound.json",
                public_paths,
            )

    def test_first_publish_uses_title_slug_and_filters_editable_data(self) -> None:
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
                {
                    "project_id": project_id,
                    "recipient_title": "Bad Micah Noooo",
                    "source_fingerprint": "source-1",
                },
            )

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.public_path, "bad-micah-noooo")
            self.assertEqual(
                result.url,
                "https://ada.github.io/bad-micah-noooo/",
            )
            prefix = "bad-micah-noooo/"
            self.assertIn(f"{prefix}index.html", api.remote_files)
            self.assertIn(f"{prefix}gallery/message/message.html", api.remote_files)
            self.assertNotIn(f"{prefix}lettersmith-metadata.json", api.remote_files)
            self.assertNotIn(f"{prefix}prompt_writer_state.json", api.remote_files)
            self.assertNotIn(
                f"{prefix}gallery/message/revisions/private.html", api.remote_files
            )
            self.assertNotIn(
                f"{prefix}gallery/pages/lettersmith-images.json",
                api.remote_files,
            )
            self.assertNotIn(
                f"{prefix}gallery/sounds/lettersmith-sound.json",
                api.remote_files,
            )
            self.assertIn(REPOSITORY_MARKER, api.remote_files)
            settings = SettingsStore(root).snapshot()
            self.assertEqual(settings[PUBLICATION_PROVIDER_KEY], GITHUB_PAGES_PROVIDER_ID)
            self.assertEqual(settings[PUBLISHED_GITHUB_OWNER_KEY], "ada")
            self.assertEqual(
                settings[PUBLISHED_GITHUB_REPOSITORY_KEY], "ada.github.io"
            )
            self.assertEqual(publication_status(settings), "published")
            self.assertEqual(
                settings[PROJECT_PUBLISHED_AT_KEY],
                result.published_at,
            )
            marker = json.loads(
                api.remote_files[f"{prefix}lettersmith-publication.json"]
            )
            self.assertEqual(marker["project_id"], project_id)
            blob_calls = [
                call
                for call in api.calls
                if call[0] == "POST" and call[1].endswith("/git/blobs")
            ]
            self.assertEqual(blob_calls, [])
            self.assertTrue(
                any("content" in entry for entry in api.last_tree_updates)
            )

    def test_republish_keeps_path_replaces_files_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            project_id = str(uuid.uuid4())
            public_path = "same-public-link"
            prefix = f"{public_path}/"
            tree = [
                {"path": REPOSITORY_MARKER, "type": "blob", "sha": "owner"},
                {"path": f"{prefix}index.html", "type": "blob", "sha": "old"},
                {"path": f"{prefix}stale.txt", "type": "blob", "sha": "stale"},
                {
                    "path": f"{prefix}lettersmith-publication.json",
                    "type": "blob",
                    "sha": "marker",
                },
                {
                    "path": f"{prefix}gallery/pages/lettersmith-images.json",
                    "type": "blob",
                    "sha": "old-images",
                },
                {
                    "path": f"{prefix}gallery/sounds/lettersmith-sound.json",
                    "type": "blob",
                    "sha": "old-sounds",
                },
            ]
            marker = json.dumps(
                {
                    "schema_version": 2,
                    "provider": GITHUB_PAGES_PROVIDER_ID,
                    "project_id": project_id,
                    "public_path": public_path,
                }
            ).encode("utf-8")
            api = _PublishingAPI(
                public_path,
                repository_exists=True,
                pages_exists=True,
                existing_tree=tree,
                existing_remote_files={
                    f"{prefix}index.html": b"old",
                    f"{prefix}stale.txt": b"stale",
                    f"{prefix}lettersmith-publication.json": marker,
                    f"{prefix}gallery/pages/lettersmith-images.json": b"private images",
                    f"{prefix}gallery/sounds/lettersmith-sound.json": b"private sounds",
                },
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
                    "project_id": project_id,
                    "recipient_title": "A Renamed Letter",
                    PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                    "published_public_path": public_path,
                    PUBLISHED_GITHUB_OWNER_KEY: "ada",
                    PUBLISHED_GITHUB_REPOSITORY_KEY: "ada.github.io",
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
            deleted_paths = {
                item["path"]
                for item in api.last_tree_updates
                if item.get("sha") is None
            }
            self.assertIn(
                f"{prefix}gallery/pages/lettersmith-images.json",
                deleted_paths,
            )
            self.assertIn(
                f"{prefix}gallery/sounds/lettersmith-sound.json",
                deleted_paths,
            )
            self.assertEqual(
                api.remote_files[f"{prefix}index.html"],
                b"<html>letter</html>",
            )
            self.assertEqual(result.url, "https://ada.github.io/same-public-link/")

    def test_republish_reuses_unchanged_blobs_and_uploads_only_changed_binary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            unchanged = b"\x89PNG\r\n\x1a\nunchanged"
            changed_before = b"\x89PNG\r\n\x1a\nbefore"
            changed_after = b"\x89PNG\r\n\x1a\nafter"
            (build / "unchanged.png").write_bytes(unchanged)
            (build / "changed.png").write_bytes(changed_before)
            project_id = str(uuid.uuid4())
            api = _PublishingAPI(project_id)
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            )

            first = publisher.publish(
                build,
                {
                    "project_id": project_id,
                    "recipient_title": "Incremental Letter",
                    "source_fingerprint": "source-1",
                },
            )
            self.assertTrue(first.success, first.message)
            api.calls.clear()
            (build / "changed.png").write_bytes(changed_after)

            second = publisher.publish(
                build,
                {
                    **SettingsStore(root).snapshot(),
                    "project_id": project_id,
                    "recipient_title": "Incremental Letter",
                    "source_fingerprint": "source-2",
                },
            )

            self.assertTrue(second.success, second.message)
            blob_calls = [
                call
                for call in api.calls
                if call[0] == "POST" and call[1].endswith("/git/blobs")
            ]
            self.assertEqual(len(blob_calls), 1)
            self.assertEqual(
                base64.b64decode(blob_calls[0][2]["content"]),
                changed_after,
            )
            update_paths = {
                str(entry.get("path", "")) for entry in api.last_tree_updates
            }
            self.assertIn("incremental-letter/changed.png", update_paths)
            self.assertNotIn("incremental-letter/unchanged.png", update_paths)
            self.assertNotIn("incremental-letter/index.html", update_paths)

    def test_cancelled_blob_uploads_never_issue_github_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "asset.png"
            content = b"\x89PNG\r\n\x1a\ncancelled"
            binary.write_bytes(content)
            api = _PublishingAPI(str(uuid.uuid4()))
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                cancelled=lambda: True,
            )

            with self.assertRaises(GitHubOperationError) as tree_error:
                publisher._tree_updates(
                    "ada",
                    "ada.github.io",
                    ((binary, "asset.png", len(content)),),
                    "cancelled/",
                    b"{}",
                    {},
                    include_root_index=False,
                )
            self.assertEqual(tree_error.exception.code, "publication_cancelled")

            with self.assertRaises(GitHubOperationError) as upload_error:
                publisher._upload_blob(
                    "ada",
                    "ada.github.io",
                    "cancelled/asset.png",
                    content,
                )
            self.assertEqual(upload_error.exception.code, "publication_cancelled")
            self.assertFalse(
                any(
                    method == "POST" and path.endswith("/git/blobs")
                    for method, path, _payload in api.calls
                )
            )

    def test_publication_timeout_never_marks_settings_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            project_id = str(uuid.uuid4())
            api = _PublishingAPI(project_id)
            clock = _Clock()
            fetches: list[str] = []

            def missing_publication(url: str, _timeout: float) -> None:
                fetches.append(url)
                return None

            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=missing_publication,
                sleeper=clock.sleep,
                clock=clock.now,
                verification_timeout=1,
            )

            result = publisher.publish(
                build,
                {
                    "project_id": project_id,
                    "recipient_title": "Timeout Letter",
                    "source_fingerprint": "source-3",
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "publication_timeout")
            self.assertEqual(SettingsStore(root).get(PUBLISHED_PAGE_URL_KEY), "")
            self.assertTrue(fetches)
            self.assertTrue(
                all("lettersmith-publication.json" in url for url in fetches)
            )

    def test_unpublication_verification_skips_index_while_marker_remains(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _Clock()
            marker = b"existing marker"
            fetches: list[str] = []

            def stale_publication(url: str, _timeout: float) -> bytes:
                fetches.append(url)
                return marker

            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                public_fetcher=stale_publication,
                sleeper=clock.sleep,
                clock=clock.now,
                verification_timeout=1,
            )

            verified = publisher._verify_unpublication(
                "https://ada.github.io/example/",
                marker,
                b"existing index",
                "nonce",
            )

            self.assertFalse(verified)
            self.assertTrue(fetches)
            self.assertTrue(
                all("lettersmith-publication.json" in url for url in fetches)
            )

    def test_republish_rejects_a_different_authenticated_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            api = _PublishingAPI(str(uuid.uuid4()))
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
                    "recipient_title": "Owner Test",
                    PUBLISHED_GITHUB_OWNER_KEY: "grace",
                    PUBLISHED_GITHUB_REPOSITORY_KEY: "ada.github.io",
                },
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "repository_owner_mismatch")
            self.assertEqual(api.calls, [])

    def test_saved_public_path_survives_a_title_change(self) -> None:
        project_id = str(uuid.uuid4())
        self.assertEqual(
            _publication_path(
                {
                    "project_id": project_id,
                    "recipient_title": "Original Title",
                    PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                    "published_public_path": "stable-link",
                }
            ),
            "stable-link",
        )

    def test_duplicate_titles_receive_readable_collision_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            second = str(uuid.uuid4())
            api = _PublishingAPI(
                second,
                repository_exists=True,
                pages_exists=True,
                existing_tree=[
                    {
                        "path": "shared-title/index.html",
                        "type": "blob",
                        "sha": "existing",
                    }
                ],
            )
            result = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            ).publish(
                build,
                {
                    "project_id": second,
                    "recipient_title": "Shared Title",
                    "source_fingerprint": "source-second",
                },
            )

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.public_path, "shared-title-2")
            self.assertEqual(result.url, "https://ada.github.io/shared-title-2/")

    def test_different_accounts_use_their_own_user_site_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            session = GitHubSession(
                GitHubAccount("manmike", 8),
                GitHubToken("ghu_access"),
            )
            api = _PublishingAPI(str(uuid.uuid4()), owner="manmike")
            result = GitHubPagesPublisher(
                root,
                session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            ).publish(
                build,
                {
                    "project_id": str(uuid.uuid4()),
                    "recipient_title": "Bad Micah Noooo",
                    "source_fingerprint": "source-manmike",
                },
            )

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.url, "https://manmike.github.io/bad-micah-noooo/")
            self.assertEqual(result.repository, "manmike.github.io")

    def test_unpublish_removes_remote_tree_and_preserves_local_letter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._build(root)
            local_message = build / "gallery/message/message.html"
            local_before = local_message.read_bytes()
            project_id = str(uuid.uuid4())
            api = _PublishingAPI(project_id)
            publisher = GitHubPagesPublisher(
                root,
                self.session,
                configuration=self.configuration,
                api=api,
                public_fetcher=api.public_fetch,
            )
            published = publisher.publish(
                build,
                {
                    "project_id": project_id,
                    "recipient_title": "Remove Me",
                    "source_fingerprint": "source-remove",
                },
            )
            metadata = {
                **SettingsStore(root).snapshot(),
                "project_id": project_id,
            }

            removed = publisher.unpublish(metadata)

            self.assertTrue(published.success, published.message)
            self.assertTrue(removed.success, removed.message)
            self.assertFalse(
                any(path.startswith("remove-me/") for path in api.remote_files)
            )
            self.assertNotEqual(publication_status(SettingsStore(root).snapshot()), "published")
            self.assertTrue(local_message.is_file())
            self.assertEqual(local_message.read_bytes(), local_before)

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
        self.assertIn(dialog._theme_tokens.success, dialog.styleSheet())
        self.assertIn(dialog._theme_tokens.error, dialog.styleSheet())
        dialog.close()

    def test_github_authentication_uses_one_modal_component(self) -> None:
        self.assertIs(GitHubStartupDialog, GitHubAuthenticationDialog)
        self.assertIs(GitHubDeviceFlowDialog, GitHubAuthenticationDialog)
        owner = QtWidgets.QWidget()
        owner.resize(900, 600)
        owner.show()
        dialog = GitHubAuthenticationDialog(owner)
        cancellations: list[bool] = []
        dialog.cancel_requested.connect(lambda: cancellations.append(True))
        try:
            self.assertTrue(
                dialog.windowFlags() & QtCore.Qt.FramelessWindowHint
            )
            self.assertEqual(
                dialog.windowModality(),
                QtCore.Qt.ApplicationModal,
            )
            self.assertTrue(dialog.isModal())
            self.assertFalse(dialog._watermark.isNull())
            self.assertEqual(
                [
                    dialog.open_button.text(),
                    dialog.cancel_button.text(),
                ],
                ["Open GitHub", "Cancel"],
            )

            dialog.show()
            self.app.processEvents()
            self.assertIsNotNone(dialog._dimmer)
            self.assertTrue(dialog._dimmer.isVisible())
            for button in (
                dialog.open_button,
                dialog.cancel_button,
            ):
                self.assertLessEqual(button.width(), 180)
                self.assertTrue(dialog.contentsRect().contains(button.geometry()))

            dialog.cancel_button.click()
            self.assertEqual(cancellations, [True])
            self.assertFalse(dialog.isVisible())
            self.assertFalse(dialog._dimmer.isVisible())
        finally:
            dialog.close()
            owner.close()

    def test_startup_hook_never_blocks_on_stored_github_connection(self) -> None:
        import publishing.github_ui as github_ui

        store = mock.Mock()
        store.load.return_value = object()
        with mock.patch.object(github_ui, "GitHubAuthenticationDialog") as dialog:
            self.assertEqual(
                prompt_for_github_startup(credential_store=store),
                "connected",
            )
            store.load.assert_called_once_with()
            dialog.assert_not_called()

    def test_disconnected_startup_blocks_until_dialog_finishes(self) -> None:
        import publishing.github_ui as github_ui

        store = mock.Mock()
        store.load.return_value = None
        dialog_instance = mock.Mock(selected_action="skip")
        with (
            mock.patch.object(
                github_ui,
                "GitHubAuthenticationDialog",
                return_value=dialog_instance,
            ),
            mock.patch.object(github_ui, "GitHubAuthenticator") as authenticator,
            mock.patch.object(github_ui, "GitHubConnectionService") as service,
        ):
            self.assertEqual(
                prompt_for_github_startup(credential_store=store),
                "skip",
            )
            authenticator.assert_called_once_with(credential_store=store)
            service.assert_called_once_with(authenticator.return_value)
            dialog_instance.prepare_startup.assert_called_once_with(
                service.return_value
            )
            dialog_instance.exec.assert_called_once_with()

    def test_startup_browser_waits_for_explicit_sign_in_click(self) -> None:
        dialog = GitHubAuthenticationDialog()
        service = mock.Mock()
        try:
            with (
                mock.patch.object(dialog, "begin_authentication") as begin,
                mock.patch(
                    "publishing.github_ui.QtGui.QDesktopServices.openUrl",
                    return_value=True,
                ) as open_url,
            ):
                dialog.prepare_startup(service)
                open_url.assert_not_called()
                dialog.open_button.click()
                begin.assert_called_once_with(service)
                open_url.assert_not_called()
        finally:
            dialog.close()

    def test_initial_network_failure_allows_device_flow_retry(self) -> None:
        dialog = GitHubAuthenticationDialog()
        service = mock.Mock()
        try:
            dialog._connection_service = service
            dialog._browser_url = ""
            with mock.patch.object(dialog, "begin_authentication") as begin:
                dialog._authentication_failed("GitHub could not be reached.")
                self.assertTrue(dialog.open_button.isEnabled())
                self.assertEqual(dialog.open_button.text(), "Retry GitHub Sign-In")
                dialog.open_button.click()
                begin.assert_called_once_with(service)
        finally:
            dialog.close()

    def test_installation_page_opens_only_after_explicit_click(self) -> None:
        dialog = GitHubAuthenticationDialog()
        try:
            with mock.patch(
                "publishing.github_ui.QtGui.QDesktopServices.openUrl",
                return_value=True,
            ) as open_url:
                dialog._installation_required(
                    "https://github.com/apps/letter-smith/installations/new",
                    "Install Letter Smith.",
                )
                open_url.assert_not_called()

                dialog.open_button.click()
                self.assertEqual(open_url.call_count, 1)
        finally:
            dialog.close()

    def test_reconnecting_account_remains_connected_visual_state(self) -> None:
        dialog = GitHubAccountDialog()
        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_token"),
        )
        try:
            dialog.set_connection(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.RECONNECTING,
                    session=session,
                    message="Reconnecting automatically…",
                    retry_attempt=2,
                    retry_after_seconds=5,
                )
            )

            self.assertIn("Reconnecting automatically", dialog.status_label.text())
            self.assertEqual(dialog.sign_in_button.text(), "Disconnect from GitHub")
            self.assertEqual(
                dialog.sign_in_button.property("accountAction"),
                "disconnect",
            )
        finally:
            dialog.close()

    def test_startup_dialog_waits_for_confirmed_connection(self) -> None:
        authorization = GitHubDeviceAuthorization(
            device_code="device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=5,
        )
        session = GitHubSession(
            account=GitHubAccount("ada", 7),
            token=GitHubToken("token-value"),
        )
        polling = threading.Event()
        release_connection = threading.Event()

        class Service:
            @staticmethod
            def authorize(
                *,
                authorization_ready,
                publishing_access_required,
                cancelled,
            ) -> GitHubConnectionSnapshot:
                del publishing_access_required
                authorization_ready(authorization)
                polling.set()
                while not release_connection.wait(0.01):
                    if cancelled():
                        raise GitHubOperationError(
                            "authorization_cancelled",
                            "GitHub sign-in was canceled.",
                        )
                return GitHubConnectionSnapshot(
                    GitHubConnectionState.CONNECTED_READY,
                    session=session,
                    access=GitHubPublishingAccess(True, "ready"),
                )

        dialog = GitHubStartupDialog()
        service = Service()
        try:
            with mock.patch(
                "publishing.github_ui.QtGui.QDesktopServices.openUrl",
                return_value=True,
            ):
                dialog.begin_authentication(service)
                self.assertTrue(polling.wait(1.0))
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(dialog.selected_action, "skip")

                release_connection.set()
                for _attempt in range(100):
                    self.app.processEvents()
                    if not dialog.isVisible():
                        break
                    QtCore.QThread.msleep(5)
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.selected_action, "connected")
        finally:
            release_connection.set()
            dialog.close()

    def test_confirmed_connection_queues_resume_through_state_application(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.CONNECTED_READY,
            session=session,
            access=GitHubPublishingAccess(True, "ready"),
        )
        resume = mock.Mock()
        forge = SimpleNamespace(
            _github_service=SimpleNamespace(snapshot=snapshot),
            _apply_github_state=mock.Mock(),
            _github_account_checked=False,
            _github_account_error="error",
            _sync_publishing_controls=mock.Mock(),
            _set_status=mock.Mock(),
            _resume_pending_publish=resume,
        )

        ForgeTab._finish_github_sign_in(forge, session)

        forge._apply_github_state.assert_called_once_with(snapshot)
        resume.assert_not_called()
        forge._set_status.assert_called_once_with("Connected as ada.")

    def test_transient_operation_retries_are_silent_bounded_and_persistent(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.RECONNECTING,
            session=session,
            access=GitHubPublishingAccess(True, "ready"),
            retry_after_seconds=2,
        )
        for operation in ("publishing", "unpublishing"):
            with self.subTest(operation=operation):
                forge = SimpleNamespace(
                    _github_service=SimpleNamespace(snapshot=snapshot),
                    _pending_publish_context=("publish",),
                    _pending_unpublish_context=("unpublish",),
                    _pending_publish_retry_attempt=0,
                    _pending_unpublish_retry_attempt=0,
                    _set_status=mock.Mock(),
                    _update_publication_activity=mock.Mock(),
                    _schedule_github_reconnect=mock.Mock(),
                )

                for _attempt in range(3):
                    self.assertTrue(
                        ForgeTab._queue_github_operation_retry(forge, operation)
                    )
                self.assertFalse(
                    ForgeTab._queue_github_operation_retry(forge, operation)
                )
                self.assertEqual(forge._schedule_github_reconnect.call_count, 3)
                self.assertEqual(forge._pending_publish_context, ("publish",))
                self.assertEqual(forge._pending_unpublish_context, ("unpublish",))

    def test_publication_reuses_a_confirmed_ready_connection(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.CONNECTED,
            session=session,
            access=GitHubPublishingAccess(True, "ready"),
        )
        restore = mock.Mock()
        forge = SimpleNamespace(
            _github_service=SimpleNamespace(
                snapshot=snapshot,
                restore=restore,
            )
        )

        result = ForgeTab._publication_connection_snapshot(forge)

        self.assertIs(result, snapshot)
        restore.assert_not_called()

    def test_publication_activity_stays_active_across_stage_updates(self) -> None:
        from Forge_Tab import ForgeTab

        emitted: list[tuple[bool, str, str]] = []
        forge = SimpleNamespace(
            _publication_operation="",
            _pending_publish_context=None,
            _pending_publish_retry_attempt=0,
            _pending_unpublish_context=None,
            _pending_unpublish_retry_attempt=0,
            publication_activity_changed=SimpleNamespace(
                emit=lambda *values: emitted.append(tuple(values))
            ),
        )

        ForgeTab._begin_publication_activity(forge, "publish", "Preparing…")
        ForgeTab._update_publication_activity(forge, "Uploading…")
        ForgeTab._finish_publication_activity(forge, "publish")
        ForgeTab._finish_publication_activity(forge, "publish")

        self.assertEqual(
            emitted,
            [
                (True, "publish", "Preparing…"),
                (True, "publish", "Uploading…"),
                (False, "publish", ""),
            ],
        )

    def test_publish_refreshes_flushed_source_before_readiness_gate(self) -> None:
        from Forge_Tab import ForgeTab

        flush = mock.Mock(return_value=True)
        gate = mock.Mock(return_value=None)
        forge = SimpleNamespace(
            _busy=False,
            _publication_operation="",
            _project_fingerprint="published-source",
            is_protected_project=lambda: False,
            _flush_prompt_writer_state=flush,
            _refresh_source_fingerprint=lambda: setattr(
                forge,
                "_project_fingerprint",
                "new-source",
            ),
            settings=SimpleNamespace(
                snapshot=lambda: {
                    PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                    PUBLICATION_VERIFIED_KEY: True,
                    PUBLISHED_PAGE_URL_KEY: "https://ada.github.io/example/",
                    PUBLISHED_PUBLIC_PATH_KEY: "example",
                    PUBLISHED_AT_KEY: "2026-08-29T12:00:00+00:00",
                    PUBLISHED_SOURCE_FINGERPRINT_KEY: "published-source",
                }
            ),
            _required_gate=gate,
            _set_status=mock.Mock(),
        )

        ForgeTab.publish_letter(forge)

        flush.assert_called_once_with()
        gate.assert_called_once_with(for_publish=True)
        forge._set_status.assert_not_called()

    def test_remote_operations_keep_context_until_a_result_arrives(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.CONNECTED,
            session=session,
            access=GitHubPublishingAccess(True, "ready"),
        )
        publish_context = (Path.cwd(), object(), {}, 1, "source", "source")
        publish_forge = SimpleNamespace(
            _busy=False,
            _pending_publish_context=publish_context,
            _github_service=SimpleNamespace(snapshot=snapshot),
            project_root=Path.cwd(),
            _ready_publication_session=lambda _operation: session,
            _update_publication_activity=mock.Mock(),
            _start_operation=mock.Mock(),
            _publish_completed=mock.Mock(),
        )
        ForgeTab._resume_pending_publish(publish_forge, session)
        self.assertIs(publish_forge._pending_publish_context, publish_context)

        unpublish_context = ({}, None)
        unpublish_forge = SimpleNamespace(
            _busy=False,
            _pending_unpublish_context=unpublish_context,
            _github_service=SimpleNamespace(snapshot=snapshot),
            project_root=Path.cwd(),
            _ready_publication_session=lambda _operation: session,
            _update_publication_activity=mock.Mock(),
            _start_operation=mock.Mock(),
            _unpublish_completed=mock.Mock(),
        )
        ForgeTab._resume_pending_unpublish(unpublish_forge, session)
        self.assertIs(unpublish_forge._pending_unpublish_context, unpublish_context)

    def test_pending_publish_does_not_bypass_reconnect_backoff(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.RECONNECTING,
            session=session,
            access=GitHubPublishingAccess(True, "ready"),
            retry_after_seconds=2,
        )
        start_operation = mock.Mock()
        schedule = mock.Mock()
        forge = SimpleNamespace(
            _busy=False,
            _pending_publish_context=(Path.cwd(), object(), {}, 1, "source", "source"),
            _github_service=SimpleNamespace(snapshot=snapshot),
            _update_publication_activity=mock.Mock(),
            _schedule_github_reconnect=schedule,
            _abort_publication_activity=mock.Mock(),
            _start_operation=start_operation,
        )
        forge._ready_publication_session = lambda operation: (
            ForgeTab._ready_publication_session(forge, operation)
        )

        ForgeTab._resume_pending_publish(forge, session)

        start_operation.assert_not_called()
        schedule.assert_called_once_with(snapshot)

    def test_unconfigured_sign_in_ends_pending_publication(self) -> None:
        from Forge_Tab import ForgeTab

        abort = mock.Mock()
        forge = SimpleNamespace(
            _busy=False,
            _publication_operation="publish",
            github_auth_dialog=SimpleNamespace(isVisible=lambda: False),
            github_account_dialog=SimpleNamespace(set_account=mock.Mock()),
            _set_status=mock.Mock(),
            _abort_publication_activity=abort,
            show_github_account=mock.Mock(),
        )

        with mock.patch(
            "Forge_Tab.github_application_configuration",
            return_value=SimpleNamespace(configured=False),
        ):
            ForgeTab.sign_in_github(forge)

        abort.assert_called_once_with("publish")
        forge.show_github_account.assert_called_once_with()

    def test_publish_does_not_open_setup_for_unconfigured_app_permissions(self) -> None:
        from Forge_Tab import ForgeTab

        session = GitHubSession(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_access"),
        )
        access = GitHubPublishingAccess(
            False,
            "app_permissions_not_configured",
            message="Developer action is required.",
        )
        snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.AUTHENTICATED_INSUFFICIENT_PERMISSION,
            session=session,
            access=access,
        )
        forge = SimpleNamespace(
            _github_service=SimpleNamespace(snapshot=snapshot),
            github_account_dialog=SimpleNamespace(set_connection=mock.Mock()),
            github_auth_dialog=SimpleNamespace(
                begin_publishing_access=mock.Mock(),
            ),
            _set_status=mock.Mock(),
        )

        ForgeTab._begin_github_access_setup(forge, session, access)

        forge.github_auth_dialog.begin_publishing_access.assert_not_called()
        forge._set_status.assert_called_once_with(
            "Developer action is required.",
            error=True,
            timeout_ms=0,
        )

    def test_github_choice_precedes_theme_choice(self) -> None:
        calls: list[str] = []
        import publishing.github_ui as github_ui
        import startup_theme

        original_github_prompt = github_ui.prompt_for_github_startup
        original_theme_prompt = startup_theme.ensure_startup_theme_preference
        try:
            github_ui.prompt_for_github_startup = lambda: (
                calls.append("github") or "skip"
            )
            startup_theme.ensure_startup_theme_preference = (
                lambda _root, *, force_prompt: (
                    calls.append("theme") or "female"
                )
            )
            choices = _resolve_startup_choices(
                Path.cwd(),
                force_theme_prompt=True,
            )
        finally:
            github_ui.prompt_for_github_startup = original_github_prompt
            startup_theme.ensure_startup_theme_preference = original_theme_prompt

        self.assertEqual(calls, ["github", "theme"])
        self.assertEqual(choices, ("skip", "female"))

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
                "Connect to GitHub",
            )
        finally:
            dialog.close()

    def test_device_code_is_copied_automatically_and_when_clicked(self) -> None:
        dialog = GitHubDeviceFlowDialog()
        clipboard = self.app.clipboard()
        authorization = GitHubDeviceAuthorization(
            device_code="device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=5,
        )
        try:
            clipboard.setText("before")
            with mock.patch(
                "publishing.github_ui.QtGui.QDesktopServices.openUrl",
                return_value=True,
            ) as open_url:
                dialog.set_authorization(authorization)
            self.assertEqual(clipboard.text(), "ABCD-1234")
            self.assertEqual(open_url.call_count, 1)
            self.assertNotIn(
                "Copy Code",
                [
                    button.text()
                    for button in dialog.findChildren(QtWidgets.QPushButton)
                ],
            )

            clipboard.setText("changed")
            QtTest.QTest.mouseClick(dialog.code_label, QtCore.Qt.LeftButton)
            self.assertEqual(clipboard.text(), "ABCD-1234")

        finally:
            dialog.close()

    def test_startup_code_is_copied_automatically_and_when_clicked(self) -> None:
        dialog = GitHubStartupDialog()
        clipboard = self.app.clipboard()
        authorization = GitHubDeviceAuthorization(
            device_code="device",
            user_code="WXYZ-9876",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=5,
        )
        try:
            clipboard.setText("before")
            with mock.patch(
                "publishing.github_ui.QtGui.QDesktopServices.openUrl",
                return_value=True,
            ):
                dialog._authorization_ready(authorization)
            self.assertEqual(clipboard.text(), "WXYZ-9876")

            clipboard.setText("changed")
            QtTest.QTest.mouseClick(
                dialog.authorization_code,
                QtCore.Qt.LeftButton,
            )
            self.assertEqual(clipboard.text(), "WXYZ-9876")
        finally:
            dialog.close()

    def test_authentication_buttons_fit_every_theme(self) -> None:
        dialog = GitHubAuthenticationDialog()
        try:
            dialog.resize(dialog.minimumSize())
            for definition in THEMES.values():
                dialog.apply_theme_tokens(definition.tokens)
                dialog.show()
                self.app.processEvents()
                self.assertEqual(
                    dialog.connection_status.palette().color(
                        QtGui.QPalette.WindowText
                    ),
                    QtGui.QColor(definition.tokens.text),
                    definition.theme_id,
                )
                for button in (
                    dialog.open_button,
                    dialog.cancel_button,
                ):
                    self.assertLessEqual(button.width(), 180)
                    self.assertTrue(
                        dialog.contentsRect().contains(button.geometry()),
                        definition.theme_id,
                    )
                dialog.hide()
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
