from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from Forge_Tab import ForgeTab
from publishing.credentials import R2CredentialStore, R2Credentials
from publishing.r2 import (
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
