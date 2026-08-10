from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from publishing.base import Publisher
from publishing.credentials import R2CredentialStore, R2Credentials
from publishing.expiration import (
    PUBLICATION_TTL_DAYS,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    publication_window,
)
from publishing.models import PublishConfiguration, PublishResult
from settings_store import PUBLISHED_PAGE_URL_KEY, SettingsStore


try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # The app remains launchable until publishing is configured.
    boto3 = None
    BotoConfig = None


PROVIDER_ID = "cloudflare_r2"
PROVIDER_LABEL = "Cloudflare R2"
PUBLISHING_PROVIDER_KEY = "publishing_provider"
ACCOUNT_ID_KEY = "r2_account_id"
BUCKET_KEY = "r2_bucket"
PUBLIC_BASE_URL_KEY = "r2_public_base_url"
FREE_TIER_LIMIT_KEY = "r2_free_tier_limit_bytes"
LAST_USED_BYTES_KEY = "r2_last_used_bytes"
LAST_OBJECT_COUNT_KEY = "r2_last_object_count"
LAST_USAGE_AT_KEY = "r2_last_usage_at"
CURRENT_PUBLIC_PATH_KEY = "r2_current_public_path"
PUBLIC_WARNING_KEY = "r2_public_warning_acknowledged"

DEFAULT_BUCKET = "letter-smith-publishing"
FREE_TIER_LIMIT_BYTES = 10_000_000_000
PUBLICATION_PREFIX = "letters/"
PUBLICATION_MARKER = "lettersmith-publication.json"
LIFECYCLE_RULE_ID = "letter-smith-30-day-expiration"

_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class R2Configuration:
    account_id: str
    bucket: str
    public_base_url: str
    limit_bytes: int = FREE_TIER_LIMIT_BYTES

    def validated(self) -> "R2Configuration":
        account_id = str(self.account_id).strip()
        bucket = str(self.bucket).strip().lower()
        public_base_url = _normalize_public_base_url(self.public_base_url)
        if not _ACCOUNT_ID_PATTERN.fullmatch(account_id):
            raise ValueError("The Cloudflare account ID must contain 32 hexadecimal characters.")
        if (
            not _BUCKET_PATTERN.fullmatch(bucket)
            or ".." in bucket
            or ".-" in bucket
            or "-." in bucket
            or _looks_like_ip_address(bucket)
        ):
            raise ValueError("The R2 bucket name is invalid.")
        if not public_base_url:
            raise ValueError("A valid HTTPS public bucket URL is required.")
        limit_bytes = int(self.limit_bytes)
        if limit_bytes <= 0 or limit_bytes > FREE_TIER_LIMIT_BYTES:
            raise ValueError("The free-storage limit must remain between 1 byte and 10 GB.")
        return R2Configuration(account_id, bucket, public_base_url, limit_bytes)


@dataclass(frozen=True)
class R2HostedPublication:
    public_path: str
    size_bytes: int
    object_count: int
    expires_at: str = ""
    recipient: str = ""
    title: str = ""


@dataclass(frozen=True)
class R2StorageSnapshot:
    used_bytes: int
    limit_bytes: int
    object_count: int
    publications: tuple[R2HostedPublication, ...] = ()
    measured_at: str = ""

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.limit_bytes - self.used_bytes)

    @property
    def percentage(self) -> float:
        if self.limit_bytes <= 0:
            return 0.0
        return min(100.0, max(0.0, self.used_bytes * 100.0 / self.limit_bytes))


class R2OperationError(RuntimeError):
    def __init__(self, code: str, user_message: str, technical_details: str = "") -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.technical_details = technical_details


def _looks_like_ip_address(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)


def _normalize_public_base_url(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _safe_public_path(recipient: str, title: str) -> str:
    raw = f"{recipient}-{title}".casefold()
    slug = "".join(character if character.isalnum() else "-" for character in raw)
    slug = "-".join(part for part in slug.split("-") if part)[:56] or "letter"
    return f"{slug}-{secrets.token_hex(4)}"


def _valid_public_path(value: object) -> str:
    candidate = str(value or "").strip()
    if (
        not candidate
        or candidate in {".", ".."}
        or Path(candidate).name != candidate
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,80}", candidate)
    ):
        raise ValueError("The hosted letter path is invalid.")
    return candidate


def _bundle_files(build: Path) -> tuple[tuple[Path, str, int], ...]:
    files: list[tuple[Path, str, int]] = []
    for path in sorted(build.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Published letter bundles cannot contain symbolic links.")
        relative = path.relative_to(build).as_posix()
        files.append((path, relative, path.stat().st_size))
    return tuple(files)


def _bundle_digest(files: Iterable[tuple[Path, str, int]]) -> str:
    digest = hashlib.sha256()
    for path, relative, _size in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _content_headers(path: Path) -> dict[str, str]:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    cache_control = (
        "no-cache, no-store, must-revalidate"
        if path.name in {"index.html", PUBLICATION_MARKER}
        else "public, max-age=86400"
    )
    return {
        "ContentType": content_type,
        "CacheControl": cache_control,
        "StorageClass": "STANDARD",
    }


def _boto3_client(configuration: R2Configuration, credentials: R2Credentials):
    if boto3 is None or BotoConfig is None:
        raise R2OperationError(
            "dependency_missing",
            "Cloudflare R2 support is not installed. Reinstall Letter Smith or install its requirements.",
            "boto3 and botocore are unavailable",
        )
    return boto3.client(
        "s3",
        endpoint_url=(
            f"https://{configuration.account_id}.r2.cloudflarestorage.com"
        ),
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        region_name="auto",
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=15,
            read_timeout=60,
            retries={"max_attempts": 4, "mode": "standard"},
        ),
    )


def _error_identity(error: BaseException) -> tuple[str, object, str]:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return type(error).__name__, "", ""
    details = response.get("Error", {})
    metadata = response.get("ResponseMetadata", {})
    code = str(details.get("Code", type(error).__name__)) if isinstance(details, Mapping) else type(error).__name__
    status = metadata.get("HTTPStatusCode", "") if isinstance(metadata, Mapping) else ""
    request_id = str(metadata.get("RequestId", "")) if isinstance(metadata, Mapping) else ""
    return code, status, request_id


def _classify_error(error: BaseException, operation: str) -> R2OperationError:
    if isinstance(error, R2OperationError):
        return error
    provider_code, status, request_id = _error_identity(error)
    normalized = provider_code.casefold()
    technical = (
        f"operation={operation}; type={type(error).__name__}; "
        f"provider_code={provider_code}; http_status={status}; request_id={request_id}"
    )
    if normalized in {
        "accessdenied",
        "invalidaccesskeyid",
        "signaturedoesnotmatch",
        "unauthorized",
        "403",
    } or status in {401, 403}:
        return R2OperationError(
            "authentication",
            "Cloudflare rejected the R2 credentials. Open R2 Storage and reconnect the account.",
            technical,
        )
    if normalized in {"nosuchbucket", "404"} or status == 404:
        return R2OperationError(
            "bucket_missing",
            "The configured R2 bucket could not be found. Open R2 Storage and check the bucket name.",
            technical,
        )
    if normalized in {"slowdown", "throttling", "toomanyrequests", "429"} or status == 429:
        return R2OperationError(
            "rate_limit",
            "Cloudflare is temporarily limiting requests. Wait briefly and try again.",
            technical,
        )
    if "timeout" in normalized or "connection" in normalized or "endpoint" in normalized:
        return R2OperationError(
            "network",
            "Letter Smith could not reach Cloudflare R2. Check the internet connection and try again.",
            technical,
        )
    return R2OperationError(
        "provider_error",
        "Cloudflare R2 could not complete the request. Try again or open Technical Details.",
        technical,
    )


class R2Publisher(Publisher):
    def __init__(
        self,
        project_root: str | Path,
        *,
        credential_store: R2CredentialStore | None = None,
        client_factory: Callable[[R2Configuration, R2Credentials], Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.credential_store = credential_store or R2CredentialStore()
        self.client_factory = client_factory or _boto3_client

    def configuration(self) -> R2Configuration | None:
        settings = self.settings.snapshot()
        try:
            return R2Configuration(
                account_id=str(settings.get(ACCOUNT_ID_KEY, "")),
                bucket=str(settings.get(BUCKET_KEY, "")),
                public_base_url=str(settings.get(PUBLIC_BASE_URL_KEY, "")),
                limit_bytes=int(settings.get(FREE_TIER_LIMIT_KEY, FREE_TIER_LIMIT_BYTES)),
            ).validated()
        except (TypeError, ValueError):
            return None

    def is_configured(self) -> bool:
        return self.configuration() is not None and self.credential_store.load() is not None

    def configure(self, parent=None) -> PublishConfiguration:
        del parent
        configuration = self.configuration()
        if configuration is None or self.credential_store.load() is None:
            return PublishConfiguration(
                False,
                message="Open R2 Storage and connect a Cloudflare account before publishing.",
            )
        return PublishConfiguration(
            True,
            repository=configuration.bucket,
            message="Cloudflare R2 is ready.",
        )

    def _client(
        self,
        configuration: R2Configuration | None = None,
        credentials: R2Credentials | None = None,
    ):
        active_configuration = configuration or self.configuration()
        active_credentials = credentials or self.credential_store.load()
        if active_configuration is None or active_credentials is None:
            raise R2OperationError(
                "not_configured",
                "Open R2 Storage and connect a Cloudflare account before publishing.",
            )
        return self.client_factory(active_configuration, active_credentials)

    def save_configuration(
        self,
        configuration: R2Configuration,
        credentials: R2Credentials | None = None,
    ) -> R2StorageSnapshot:
        valid_configuration = configuration.validated()
        valid_credentials = credentials.validated() if credentials is not None else self.credential_store.load()
        if valid_credentials is None:
            raise R2OperationError(
                "not_configured",
                "Both R2 access keys are required for the first connection.",
            )
        try:
            client = self._client(valid_configuration, valid_credentials)
            try:
                client.head_bucket(Bucket=valid_configuration.bucket)
            except Exception as error:
                provider_code, status, _request_id = _error_identity(error)
                if provider_code.casefold() in {"nosuchbucket", "404"} or status == 404:
                    client.create_bucket(Bucket=valid_configuration.bucket)
                else:
                    raise
            self._install_lifecycle(client, valid_configuration)
            snapshot = self._storage_snapshot(client, valid_configuration)
        except Exception as error:
            raise _classify_error(error, "configure") from error

        self.credential_store.save(valid_credentials)
        self.settings.update_fields(
            {
                PUBLISHING_PROVIDER_KEY: PROVIDER_ID,
                ACCOUNT_ID_KEY: valid_configuration.account_id,
                BUCKET_KEY: valid_configuration.bucket,
                PUBLIC_BASE_URL_KEY: valid_configuration.public_base_url,
                FREE_TIER_LIMIT_KEY: valid_configuration.limit_bytes,
            }
        )
        self._persist_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _install_lifecycle(client: Any, configuration: R2Configuration) -> None:
        try:
            response = client.get_bucket_lifecycle_configuration(
                Bucket=configuration.bucket
            )
            raw_rules = response.get("Rules", []) if isinstance(response, Mapping) else []
        except Exception as error:
            provider_code, _status, _request_id = _error_identity(error)
            if provider_code.casefold() not in {
                "nosuchlifecycleconfiguration",
                "nosuchlifecycle",
            }:
                raise
            raw_rules = []
        rules = [
            dict(rule)
            for rule in raw_rules
            if isinstance(rule, Mapping) and str(rule.get("ID", "")) != LIFECYCLE_RULE_ID
        ]
        rules.append(
            {
                "ID": LIFECYCLE_RULE_ID,
                "Status": "Enabled",
                "Filter": {"Prefix": PUBLICATION_PREFIX},
                "Expiration": {"Days": PUBLICATION_TTL_DAYS},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
            }
        )
        client.put_bucket_lifecycle_configuration(
            Bucket=configuration.bucket,
            LifecycleConfiguration={"Rules": rules},
        )

    def disconnect(self) -> None:
        self.credential_store.clear()
        self.settings.update_fields(
            {
                ACCOUNT_ID_KEY: "",
                BUCKET_KEY: "",
                PUBLIC_BASE_URL_KEY: "",
                LAST_USED_BYTES_KEY: 0,
                LAST_OBJECT_COUNT_KEY: 0,
                LAST_USAGE_AT_KEY: "",
                CURRENT_PUBLIC_PATH_KEY: "",
            }
        )

    def storage_snapshot(self) -> R2StorageSnapshot:
        configuration = self.configuration()
        if configuration is None:
            raise R2OperationError(
                "not_configured",
                "Open R2 Storage and connect a Cloudflare account first.",
            )
        try:
            snapshot = self._storage_snapshot(self._client(configuration), configuration)
        except Exception as error:
            raise _classify_error(error, "storage_usage") from error
        self._persist_snapshot(snapshot)
        return snapshot

    def _storage_snapshot(
        self,
        client: Any,
        configuration: R2Configuration,
    ) -> R2StorageSnapshot:
        grouped: dict[str, dict[str, Any]] = {}
        used_bytes = 0
        object_count = 0
        continuation: str | None = None
        while True:
            arguments: dict[str, Any] = {"Bucket": configuration.bucket}
            if continuation:
                arguments["ContinuationToken"] = continuation
            response = client.list_objects_v2(**arguments)
            contents = response.get("Contents", []) if isinstance(response, Mapping) else []
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = str(item.get("Key", ""))
                size = max(0, int(item.get("Size", 0) or 0))
                used_bytes += size
                object_count += 1
                if not key.startswith(PUBLICATION_PREFIX):
                    continue
                remainder = key[len(PUBLICATION_PREFIX):]
                public_path, separator, relative = remainder.partition("/")
                if not separator or not public_path or not relative:
                    continue
                group = grouped.setdefault(
                    public_path,
                    {"size": 0, "objects": 0, "marker": ""},
                )
                group["size"] += size
                group["objects"] += 1
                if relative == PUBLICATION_MARKER:
                    group["marker"] = key
            if not bool(response.get("IsTruncated", False)):
                break
            continuation = str(response.get("NextContinuationToken", ""))
            if not continuation:
                break

        publications: list[R2HostedPublication] = []
        for public_path, group in grouped.items():
            marker: dict[str, Any] = {}
            marker_key = str(group.get("marker", ""))
            if marker_key:
                try:
                    response = client.get_object(
                        Bucket=configuration.bucket,
                        Key=marker_key,
                    )
                    body = response.get("Body") if isinstance(response, Mapping) else None
                    raw = body.read() if body is not None else b""
                    loaded = json.loads(raw.decode("utf-8"))
                    if isinstance(loaded, dict):
                        marker = loaded
                except Exception:
                    _LOGGER.warning(
                        "R2 publication marker could not be read: bucket=%s path=%s",
                        configuration.bucket,
                        public_path,
                    )
            publications.append(
                R2HostedPublication(
                    public_path=public_path,
                    size_bytes=int(group["size"]),
                    object_count=int(group["objects"]),
                    expires_at=str(marker.get("expires_at", "")),
                    recipient=str(marker.get("recipient_name", "")),
                    title=str(marker.get("recipient_title", "")),
                )
            )
        publications.sort(
            key=lambda item: (item.expires_at, item.public_path),
            reverse=True,
        )
        return R2StorageSnapshot(
            used_bytes=used_bytes,
            limit_bytes=configuration.limit_bytes,
            object_count=object_count,
            publications=tuple(publications),
            measured_at=datetime.now(timezone.utc).isoformat(),
        )

    def _persist_snapshot(self, snapshot: R2StorageSnapshot) -> None:
        self.settings.update_fields(
            {
                LAST_USED_BYTES_KEY: snapshot.used_bytes,
                LAST_OBJECT_COUNT_KEY: snapshot.object_count,
                LAST_USAGE_AT_KEY: snapshot.measured_at,
            }
        )

    def publish(self, build_dir: Path, metadata: dict) -> PublishResult:
        configuration = self.configuration()
        if configuration is None or self.credential_store.load() is None:
            return PublishResult(
                False,
                message="Open R2 Storage and connect a Cloudflare account before publishing.",
                error_code="not_configured",
            )
        build = Path(build_dir).resolve()
        if not (build / "index.html").is_file():
            return PublishResult(
                False,
                message="The generated letter is incomplete.",
                error_code="invalid_build",
            )
        uploaded_keys: list[str] = []
        public_path = _safe_public_path(
            str(metadata.get("recipient_name", "")),
            str(metadata.get("recipient_title", "")),
        )
        previous_public_path = str(
            self.settings.get(CURRENT_PUBLIC_PATH_KEY, "")
        ).strip()
        published_at, expires_at = publication_window()
        try:
            files = _bundle_files(build)
            bundle_size = sum(size for _path, _relative, size in files)
            client = self._client(configuration)
            snapshot = self._storage_snapshot(client, configuration)
            self._persist_snapshot(snapshot)
            projected = snapshot.used_bytes + bundle_size
            if projected > configuration.limit_bytes:
                needed = projected - configuration.limit_bytes
                message = (
                    "Online publication stopped because this letter would exceed the "
                    f"10 GB free-storage limit. Delete at least {_format_bytes(needed)} "
                    "in R2 Storage, wait for expired letters to clear, or save locally."
                )
                return PublishResult(
                    False,
                    public_path=public_path,
                    message=message,
                    technical_details=(
                        f"used_bytes={snapshot.used_bytes}; bundle_bytes={bundle_size}; "
                        f"limit_bytes={configuration.limit_bytes}"
                    ),
                    error_code="storage_limit",
                )

            prefix = f"{PUBLICATION_PREFIX}{public_path}/"
            for path, relative, _size in files:
                key = f"{prefix}{relative}"
                client.upload_file(
                    str(path),
                    configuration.bucket,
                    key,
                    ExtraArgs=_content_headers(path),
                )
                uploaded_keys.append(key)

            marker = {
                "schema_version": 1,
                "provider": PROVIDER_ID,
                "build_sha256": _bundle_digest(files),
                "published_at": published_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "expires_after_days": PUBLICATION_TTL_DAYS,
                "public_path": public_path,
                "recipient_name": str(metadata.get("recipient_name", "")),
                "recipient_title": str(metadata.get("recipient_title", "")),
                "bundle_bytes": bundle_size,
            }
            marker_key = f"{prefix}{PUBLICATION_MARKER}"
            client.put_object(
                Bucket=configuration.bucket,
                Key=marker_key,
                Body=json.dumps(
                    marker,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                ContentType="application/json",
                CacheControl="no-cache, no-store, must-revalidate",
                StorageClass="STANDARD",
            )
            uploaded_keys.append(marker_key)
            client.head_object(Bucket=configuration.bucket, Key=marker_key)
            url = f"{configuration.public_base_url}/{quote(PUBLICATION_PREFIX)}{quote(public_path)}/"

            if previous_public_path and previous_public_path != public_path:
                try:
                    self._delete_prefix(client, configuration, previous_public_path)
                except Exception as cleanup_error:
                    classified = _classify_error(cleanup_error, "replace_previous_publication")
                    _LOGGER.warning(
                        "R2 previous-publication cleanup failed: %s",
                        classified.technical_details,
                    )

            refreshed = self._storage_snapshot(client, configuration)
            self._persist_snapshot(refreshed)
            self.settings.update_fields(
                {
                    PUBLISHED_AT_KEY: published_at.isoformat(),
                    PUBLISHED_EXPIRES_AT_KEY: expires_at.isoformat(),
                    CURRENT_PUBLIC_PATH_KEY: public_path,
                    PUBLISHED_PAGE_URL_KEY: url,
                }
            )
            return PublishResult(
                True,
                url=url,
                public_path=public_path,
                message="Published to Cloudflare R2.",
            )
        except Exception as error:
            classified = _classify_error(error, "publish")
            _LOGGER.error("R2 publication failed: %s", classified.technical_details)
            if uploaded_keys:
                try:
                    client = self._client(configuration)
                    self._delete_keys(client, configuration.bucket, uploaded_keys)
                except Exception as cleanup_error:
                    cleanup = _classify_error(cleanup_error, "rollback_upload")
                    _LOGGER.error("R2 rollback failed: %s", cleanup.technical_details)
            return PublishResult(
                False,
                public_path=public_path,
                message=classified.user_message,
                technical_details=classified.technical_details,
                error_code=classified.code,
            )

    def delete_publication(self, public_path: str) -> R2StorageSnapshot:
        configuration = self.configuration()
        if configuration is None:
            raise R2OperationError(
                "not_configured",
                "Open R2 Storage and connect a Cloudflare account first.",
            )
        valid_path = _valid_public_path(public_path)
        try:
            client = self._client(configuration)
            self._delete_prefix(client, configuration, valid_path)
            snapshot = self._storage_snapshot(client, configuration)
        except Exception as error:
            raise _classify_error(error, "delete_publication") from error
        self._persist_snapshot(snapshot)
        if str(self.settings.get(CURRENT_PUBLIC_PATH_KEY, "")) == valid_path:
            self.settings.update_fields(
                {
                    CURRENT_PUBLIC_PATH_KEY: "",
                    PUBLISHED_PAGE_URL_KEY: "",
                    PUBLISHED_AT_KEY: "",
                    PUBLISHED_EXPIRES_AT_KEY: "",
                }
            )
        return snapshot

    @classmethod
    def _delete_prefix(
        cls,
        client: Any,
        configuration: R2Configuration,
        public_path: str,
    ) -> None:
        prefix = f"{PUBLICATION_PREFIX}{_valid_public_path(public_path)}/"
        continuation: str | None = None
        keys: list[str] = []
        while True:
            arguments: dict[str, Any] = {
                "Bucket": configuration.bucket,
                "Prefix": prefix,
            }
            if continuation:
                arguments["ContinuationToken"] = continuation
            response = client.list_objects_v2(**arguments)
            for item in response.get("Contents", []):
                if isinstance(item, Mapping) and str(item.get("Key", "")).startswith(prefix):
                    keys.append(str(item["Key"]))
            if not bool(response.get("IsTruncated", False)):
                break
            continuation = str(response.get("NextContinuationToken", ""))
            if not continuation:
                break
        cls._delete_keys(client, configuration.bucket, keys)

    @staticmethod
    def _delete_keys(client: Any, bucket: str, keys: Iterable[str]) -> None:
        pending = [str(key) for key in keys if str(key)]
        for start in range(0, len(pending), 1000):
            batch = pending[start:start + 1000]
            if batch:
                client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )


def _format_bytes(value: int) -> str:
    amount = max(0, int(value))
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if amount < 1000 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "bytes" else f"{amount:.1f} {unit}"
        amount /= 1000
    return f"{amount:.1f} TB"


__all__ = [
    "ACCOUNT_ID_KEY",
    "BUCKET_KEY",
    "CURRENT_PUBLIC_PATH_KEY",
    "DEFAULT_BUCKET",
    "FREE_TIER_LIMIT_BYTES",
    "FREE_TIER_LIMIT_KEY",
    "LAST_OBJECT_COUNT_KEY",
    "LAST_USAGE_AT_KEY",
    "LAST_USED_BYTES_KEY",
    "PROVIDER_ID",
    "PROVIDER_LABEL",
    "PUBLIC_BASE_URL_KEY",
    "PUBLIC_WARNING_KEY",
    "PUBLISHING_PROVIDER_KEY",
    "R2Configuration",
    "R2HostedPublication",
    "R2OperationError",
    "R2Publisher",
    "R2StorageSnapshot",
    "_format_bytes",
]
