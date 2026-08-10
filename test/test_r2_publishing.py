from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from Forge_Tab import ForgeTab
from publishing.cloudflare_oauth import (
    CloudflareAccount,
    CloudflareOAuthClientConfiguration,
    CloudflareOAuthClientConfigurationStore,
    CloudflareOAuthToken,
    CloudflareOAuthTokenStore,
    _CallbackState,
    _OAuthCallbackServer,
    _authorization_url,
    _callback_handler,
)
from publishing.credentials import R2CredentialStore, R2Credentials
from publishing.r2 import (
    AUTHENTICATION_MODE_OAUTH,
    CURRENT_PUBLIC_PATH_KEY,
    LIFECYCLE_RULE_ID,
    R2Configuration,
    R2OperationError,
    R2Publisher,
    R2StorageSnapshot,
)
from publishing.r2_ui import R2StorageDialog
from settings_store import SettingsStore


class _FakeClientError(RuntimeError):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {
                "HTTPStatusCode": status,
                "RequestId": "request-1",
            },
        }


class _FakeR2Client:
    def __init__(self) -> None:
        self.bucket_exists = True
        self.objects: dict[str, bytes] = {}
        self.lifecycle_rules = [
            {
                "ID": "existing-rule",
                "Status": "Enabled",
                "Filter": {"Prefix": "unrelated/"},
                "Expiration": {"Days": 90},
            }
        ]

    def head_bucket(self, *, Bucket: str) -> dict:
        del Bucket
        if not self.bucket_exists:
            raise _FakeClientError("NoSuchBucket", 404)
        return {}

    def create_bucket(self, *, Bucket: str) -> dict:
        del Bucket
        self.bucket_exists = True
        return {}

    def enable_public_domain(self, bucket: str) -> str:
        del bucket
        return "https://pub-test.r2.dev"

    def get_bucket_lifecycle_configuration(self, *, Bucket: str) -> dict:
        del Bucket
        return {"Rules": list(self.lifecycle_rules)}

    def put_bucket_lifecycle_configuration(
        self,
        *,
        Bucket: str,
        LifecycleConfiguration: dict,
    ) -> dict:
        del Bucket
        self.lifecycle_rules = list(LifecycleConfiguration["Rules"])
        return {}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **_kwargs) -> dict:
        del Bucket
        contents = [
            {"Key": key, "Size": len(value)}
            for key, value in sorted(self.objects.items())
            if key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict,
    ) -> None:
        del bucket
        self.objects[key] = Path(filename).read_bytes()
        assert ExtraArgs["StorageClass"] == "STANDARD"

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_kwargs) -> dict:
        del Bucket
        self.objects[Key] = bytes(Body)
        return {}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        del Bucket
        if Key not in self.objects:
            raise _FakeClientError("NoSuchKey", 404)
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        del Bucket
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_objects(self, *, Bucket: str, Delete: dict) -> dict:
        del Bucket
        for item in Delete["Objects"]:
            self.objects.pop(str(item["Key"]), None)
        return {}


class R2PublishingTests(unittest.TestCase):
    def _store(self, root: Path) -> R2CredentialStore:
        return R2CredentialStore(
            root / "credentials.bin",
            protect=lambda value: b"protected:" + value[::-1],
            unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
        )

    @staticmethod
    def _configuration(*, limit: int = 10_000_000_000) -> R2Configuration:
        return R2Configuration(
            account_id="a" * 32,
            bucket="letter-smith-publishing",
            public_base_url="https://letters.example.com",
            limit_bytes=limit,
        )

    @staticmethod
    def _build(root: Path, payload: bytes = b"letter") -> Path:
        build = root / "Play"
        build.mkdir()
        (build / "index.html").write_bytes(b"<html></html>")
        (build / "styles.css").write_bytes(b"body{}")
        (build / "script.js").write_bytes(payload)
        return build

    def test_credentials_are_not_written_as_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            credentials = R2Credentials("ACCESS-KEY", "SECRET-KEY")
            store.save(credentials)
            encrypted = store.path.read_bytes()
            self.assertNotIn(b"ACCESS-KEY", encrypted)
            self.assertNotIn(b"SECRET-KEY", encrypted)
            self.assertEqual(store.load(), credentials)

    def test_oauth_tokens_are_encrypted_and_pkce_url_has_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CloudflareOAuthTokenStore(
                root / "oauth.bin",
                protect=lambda value: b"protected:" + value[::-1],
                unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
            )
            token = CloudflareOAuthToken(
                access_token="ACCESS-TOKEN",
                refresh_token="REFRESH-TOKEN",
                expires_at=9999999999,
            )
            store.save(token)
            encrypted = store.path.read_bytes()
            self.assertNotIn(b"ACCESS-TOKEN", encrypted)
            self.assertNotIn(b"REFRESH-TOKEN", encrypted)
            self.assertEqual(store.load(), token)

            configuration = CloudflareOAuthClientConfiguration("client-id").validated()
            url = _authorization_url(
                configuration,
                state="state-value",
                challenge="pkce-challenge",
            )
            self.assertIn("code_challenge_method=S256", url)
            self.assertIn("code_challenge=pkce-challenge", url)
            self.assertNotIn("client_secret", url)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertEqual(
                query["scope"],
                ["account-settings.read workers-r2.write offline_access"],
            )

    def test_oauth_connection_uses_rest_client_and_automatic_public_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            oauth_store = CloudflareOAuthTokenStore(
                root / "oauth.bin",
                protect=lambda value: b"protected:" + value[::-1],
                unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
            )
            oauth_store.save(
                CloudflareOAuthToken(
                    access_token="access",
                    refresh_token="refresh",
                    expires_at=9999999999,
                )
            )
            configuration_store = CloudflareOAuthClientConfigurationStore(
                root / "oauth-client.json"
            )
            configuration_store.save(
                CloudflareOAuthClientConfiguration("client-id")
            )
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                oauth_token_store=oauth_store,
                oauth_configuration_store=configuration_store,
                oauth_client_factory=lambda _account_id, _session: client,
                public_checker=lambda _url, _expected: True,
            )
            snapshot = publisher.connect_oauth_account("a" * 32)
            self.assertEqual(snapshot.used_bytes, 0)
            self.assertEqual(publisher.authentication_mode(), AUTHENTICATION_MODE_OAUTH)
            self.assertEqual(
                publisher.configuration().public_base_url,
                "https://pub-test.r2.dev",
            )
            self.assertTrue(publisher.is_configured())
            self.assertIsNone(publisher.credential_store.load())

    def test_stale_oauth_callback_does_not_cancel_current_login(self) -> None:
        state = _CallbackState("current-state", "/oauth/callback")
        server = _OAuthCallbackServer(("127.0.0.1", 0), _callback_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        try:
            with urllib.request.urlopen(
                f"{base_url}?state=old-state&code=old-code",
                timeout=2.0,
            ) as response:
                response.read()
            self.assertFalse(state.completed)
            self.assertEqual(state.error, "")
            self.assertEqual(state.code, "")

            with urllib.request.urlopen(
                f"{base_url}?state=current-state&code=current-code",
                timeout=2.0,
            ) as response:
                response.read()
            self.assertTrue(state.completed)
            self.assertEqual(state.error, "")
            self.assertEqual(state.code, "current-code")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_saved_oauth_login_is_reused_without_opening_the_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oauth_store = CloudflareOAuthTokenStore(
                root / "oauth.bin",
                protect=lambda value: b"protected:" + value[::-1],
                unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
            )
            oauth_store.save(
                CloudflareOAuthToken(
                    access_token="access",
                    refresh_token="refresh",
                    expires_at=9999999999,
                )
            )
            configuration_store = CloudflareOAuthClientConfigurationStore(
                root / "oauth-client.json"
            )
            configuration_store.save(
                CloudflareOAuthClientConfiguration("client-id")
            )
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                oauth_token_store=oauth_store,
                oauth_configuration_store=configuration_store,
            )
            with mock.patch("publishing.r2.CloudflareOAuthSession") as session_type:
                session_type.return_value.accounts.return_value = (
                    CloudflareAccount("a" * 32, "Test Account"),
                )
                authorization = publisher.authorize_oauth(reuse_saved=True)
            self.assertEqual(len(authorization.accounts), 1)
            session_type.return_value.authorize.assert_not_called()

    def test_oauth_connection_reports_r2_activation_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class InactiveR2Client(_FakeR2Client):
                def head_bucket(self, *, Bucket: str) -> dict:
                    del Bucket
                    raise _FakeClientError("10042", 403)

            oauth_store = CloudflareOAuthTokenStore(
                root / "oauth.bin",
                protect=lambda value: b"protected:" + value[::-1],
                unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
            )
            oauth_store.save(
                CloudflareOAuthToken(access_token="access", expires_at=9999999999)
            )
            configuration_store = CloudflareOAuthClientConfigurationStore(
                root / "oauth-client.json"
            )
            configuration_store.save(
                CloudflareOAuthClientConfiguration("client-id")
            )
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                oauth_token_store=oauth_store,
                oauth_configuration_store=configuration_store,
                oauth_client_factory=lambda _account_id, _session: InactiveR2Client(),
            )
            with self.assertRaises(R2OperationError) as raised:
                publisher.connect_oauth_account("a" * 32)
            self.assertEqual(raised.exception.code, "r2_activation")
            self.assertIn("not enabled", raised.exception.user_message.casefold())

    def test_configure_preserves_other_lifecycle_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: client,
                public_checker=lambda _url, _expected: True,
            )
            snapshot = publisher.save_configuration(
                self._configuration(),
                R2Credentials("access", "secret"),
            )
            self.assertEqual(snapshot.used_bytes, 0)
            rule_ids = {rule["ID"] for rule in client.lifecycle_rules}
            self.assertEqual(rule_ids, {"existing-rule", LIFECYCLE_RULE_ID})
            expiration_rule = next(
                rule for rule in client.lifecycle_rules if rule["ID"] == LIFECYCLE_RULE_ID
            )
            self.assertEqual(expiration_rule["Expiration"]["Days"], 30)
            self.assertEqual(expiration_rule["Filter"]["Prefix"], "letters/")

    def test_publish_replaces_previous_letter_after_new_marker_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: client,
                public_checker=lambda _url, _expected: True,
            )
            publisher.save_configuration(
                self._configuration(),
                R2Credentials("access", "secret"),
            )
            build = self._build(root)
            first = publisher.publish(
                build,
                {"recipient_name": "Ada", "recipient_title": "Welcome"},
            )
            self.assertTrue(first.success)
            first_prefix = f"letters/{first.public_path}/"
            self.assertTrue(any(key.startswith(first_prefix) for key in client.objects))

            second = publisher.publish(
                build,
                {"recipient_name": "Ada", "recipient_title": "Welcome"},
            )
            self.assertTrue(second.success)
            self.assertNotEqual(first.public_path, second.public_path)
            self.assertFalse(any(key.startswith(first_prefix) for key in client.objects))
            marker_key = f"letters/{second.public_path}/lettersmith-publication.json"
            marker = json.loads(client.objects[marker_key])
            self.assertEqual(marker["expires_after_days"], 30)
            self.assertEqual(
                publisher.settings.get(CURRENT_PUBLIC_PATH_KEY),
                second.public_path,
            )

    def test_publish_stops_before_configured_free_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: client,
                public_checker=lambda _url, _expected: True,
            )
            publisher.save_configuration(
                self._configuration(limit=20),
                R2Credentials("access", "secret"),
            )
            result = publisher.publish(
                self._build(root, payload=b"x" * 50),
                {"recipient_name": "Ada", "recipient_title": "Large"},
            )
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "storage_limit")
            self.assertFalse(client.objects)

    def test_unverified_public_url_rolls_back_uploaded_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: client,
                public_checker=lambda _url, _expected: False,
            )
            publisher.save_configuration(
                self._configuration(),
                R2Credentials("access", "secret"),
            )
            result = publisher.publish(
                self._build(root),
                {"recipient_name": "Ada", "recipient_title": "Private"},
            )
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "public_access")
            self.assertIn("public URL", result.message)
            self.assertFalse(client.objects)

    def test_manual_delete_releases_objects_and_current_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = _FakeR2Client()
            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: client,
                public_checker=lambda _url, _expected: True,
            )
            publisher.save_configuration(
                self._configuration(),
                R2Credentials("access", "secret"),
            )
            result = publisher.publish(
                self._build(root),
                {"recipient_name": "Ada", "recipient_title": "Delete"},
            )
            snapshot = publisher.delete_publication(result.public_path)
            self.assertEqual(snapshot.used_bytes, 0)
            self.assertFalse(client.objects)
            self.assertEqual(publisher.settings.get(CURRENT_PUBLIC_PATH_KEY), "")

    def test_configuration_surfaces_authentication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class DeniedClient(_FakeR2Client):
                def head_bucket(self, *, Bucket: str) -> dict:
                    del Bucket
                    raise _FakeClientError("AccessDenied", 403)

            publisher = R2Publisher(
                root,
                credential_store=self._store(root),
                client_factory=lambda _configuration, _credentials: DeniedClient(),
                public_checker=lambda _url, _expected: True,
            )
            with self.assertRaises(R2OperationError) as raised:
                publisher.save_configuration(
                    self._configuration(),
                    R2Credentials("access", "secret"),
                )
            self.assertEqual(raised.exception.code, "authentication")
            self.assertIn("reconnect", raised.exception.user_message.casefold())


class R2StorageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_storage_dialog_displays_usage_and_hosted_letters(self) -> None:
        dialog = R2StorageDialog()
        self.assertFalse(hasattr(dialog, "refresh_btn"))
        snapshot = R2StorageSnapshot(
            used_bytes=8_500_000_000,
            limit_bytes=10_000_000_000,
            object_count=12,
            publications=(),
            measured_at="2026-08-10T12:00:00+00:00",
        )
        dialog.update_snapshot(snapshot)
        self.assertEqual(dialog.usage_bar.value(), 850)
        self.assertIn("8.5 GB", dialog.usage_label.text())
        self.assertIn("85%", dialog.usage_warning.text())
        dialog.close()

    def test_connection_buttons_reflect_connection_state(self) -> None:
        dialog = R2StorageDialog()
        dialog.set_configuration(None, has_credentials=False)
        self.assertTrue(dialog.oauth_connect_btn.isEnabled())
        self.assertFalse(dialog.disconnect_btn.isEnabled())
        self.assertIn("#26343a", dialog.disconnect_btn.styleSheet())

        dialog.set_configuration(
            R2Configuration(
                account_id="a" * 32,
                bucket="letter-smith-publishing",
                public_base_url="https://letters.example.com",
            ),
            has_credentials=True,
            authentication_mode=AUTHENTICATION_MODE_OAUTH,
        )
        self.assertFalse(dialog.oauth_connect_btn.isEnabled())
        self.assertIn("#26343a", dialog.oauth_connect_btn.styleSheet())
        self.assertTrue(dialog.disconnect_btn.isEnabled())
        self.assertIn("#8f2f3d", dialog.disconnect_btn.styleSheet())
        self.assertIn("color:white", dialog.disconnect_btn.styleSheet())
        dialog.close()

    def test_manual_setup_expands_dialog_and_keeps_fields_visible(self) -> None:
        dialog = R2StorageDialog()
        dialog.show()
        self.app.processEvents()
        collapsed_height = dialog.height()
        dialog.advanced_toggle.click()
        self.app.processEvents()
        self.assertTrue(dialog.manual_panel.isVisible())
        self.assertGreater(dialog.height(), collapsed_height)
        self.assertGreater(dialog.account_id.height(), 20)
        self.assertGreater(dialog.secret_key.height(), 20)
        dialog.close()

    def test_storage_dialog_refreshes_on_every_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forge = ForgeTab(Path(directory))
            publisher = mock.Mock()
            publisher.is_configured.return_value = True
            publisher.has_saved_oauth_authorization.return_value = True
            publisher.configuration.return_value = R2Configuration(
                account_id="a" * 32,
                bucket="letter-smith-publishing",
                public_base_url="https://letters.example.com",
            )
            publisher.authentication_mode.return_value = AUTHENTICATION_MODE_OAUTH
            with (
                mock.patch("Forge_Tab.R2Publisher", return_value=publisher),
                mock.patch.object(forge, "_refresh_r2_storage") as refresh,
            ):
                forge.show_r2_storage()
                forge.r2_storage_dialog.hide()
                forge.show_r2_storage()
            self.assertEqual(refresh.call_count, 2)
            forge.close()

    def test_storage_dialog_resumes_saved_oauth_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forge = ForgeTab(Path(directory))
            publisher = mock.Mock()
            publisher.is_configured.return_value = False
            publisher.has_saved_oauth_authorization.return_value = True
            publisher.configuration.return_value = None
            publisher.authentication_mode.return_value = AUTHENTICATION_MODE_OAUTH
            with (
                mock.patch("Forge_Tab.R2Publisher", return_value=publisher),
                mock.patch.object(forge, "_connect_r2_oauth") as connect,
            ):
                forge.show_r2_storage()
                self.app.processEvents()
            connect.assert_called_once_with()
            forge.close()

    def test_forge_defaults_new_install_to_r2_but_preserves_existing_github(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forge = ForgeTab(root)
            self.assertEqual(forge.publishing_provider.currentData(), "cloudflare_r2")
            forge.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            SettingsStore(root).update_fields(
                {"github_pages_repository": "owner/letter-smith-publishing"}
            )
            forge = ForgeTab(root)
            self.assertEqual(forge.publishing_provider.currentData(), "github_pages")
            forge.close()


if __name__ == "__main__":
    unittest.main()
