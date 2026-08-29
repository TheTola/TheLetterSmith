from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import logging
import re
import secrets
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urlsplit

from publishing.base import Publisher
from publishing.expiration import GITHUB_PAGES_PROVIDER_ID
from publishing.github_auth import (
    GitHubAPI,
    GitHubConnectionService,
    GitHubFailureKind,
    GitHubOperationError,
    GitHubSession,
    classify_github_error,
)
from publishing.github_config import (
    GITHUB_REPOSITORY_NAME,
    GitHubApplicationConfiguration,
    github_application_configuration,
    github_pages_repository_name,
)
from publishing.models import PublishConfiguration, PublishResult
from project_timestamps import PROJECT_PUBLISHED_AT_KEY
from settings_store import (
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    SettingsStore,
    empty_publication_settings,
)


PUBLICATION_PREFIX = ""
LEGACY_PUBLICATION_PREFIX = "letters"
PUBLICATION_MARKER = "lettersmith-publication.json"
REPOSITORY_MARKER = ".lettersmith-publishing.json"
PUBLIC_WARNING_KEY = "github_public_warning_acknowledged"
MAX_BLOB_BYTES = 90 * 1024 * 1024
MAX_BUNDLE_BYTES = 900 * 1024 * 1024
MAX_INLINE_TEXT_BYTES = 1024 * 1024
MAX_INLINE_TREE_BYTES = 4 * 1024 * 1024
MAX_PARALLEL_BLOB_UPLOADS = 4
MAX_PARALLEL_BLOB_BYTES = 128 * 1024 * 1024
_PUBLIC_PATH_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?")
_UNSUPPORTED_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")
_EXCLUDED_ROOT_FILES = {
    "lettersmith-build.json",
    "lettersmith-metadata.json",
    "prompt_writer_state.json",
}
_ROOT_INDEX = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Letter Smith</title></head>
<body><main><h1>Letter Smith</h1><p>Letters published with Letter Smith are available through their direct links.</p></main></body></html>
"""
_LOGGER = logging.getLogger(__name__)


def _published_files(build: Path) -> tuple[tuple[Path, str, int], ...]:
    files: list[tuple[Path, str, int]] = []
    total = 0
    for path in sorted(build.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise GitHubOperationError(
                "invalid_build",
                "The generated letter contains an unsupported linked file.",
            )
        relative = path.relative_to(build).as_posix()
        if relative in _EXCLUDED_ROOT_FILES:
            continue
        if relative.startswith("gallery/message/revisions/"):
            continue
        size = path.stat().st_size
        if size > MAX_BLOB_BYTES:
            raise GitHubOperationError(
                "file_too_large",
                f"The generated letter contains a file larger than {MAX_BLOB_BYTES // (1024 * 1024)} MB.",
                technical_details=f"path={relative}; bytes={size}",
            )
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise GitHubOperationError(
                "bundle_too_large",
                "The generated letter is too large to publish through GitHub.",
                technical_details=f"bundle_bytes={total}",
            )
        files.append((path, relative, size))
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


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _valid_public_path(value: object) -> str:
    candidate = str(value or "").strip().casefold()
    if not _PUBLIC_PATH_PATTERN.fullmatch(candidate):
        raise GitHubOperationError(
            "invalid_publication_id",
            "The letter's publication identity is invalid.",
        )
    return candidate


def _project_publication_id(metadata: dict) -> str:
    project_id = str(metadata.get("project_id", "")).strip()
    try:
        return str(uuid.UUID(project_id))
    except (ValueError, AttributeError) as error:
        raise GitHubOperationError(
            "invalid_project_identity",
            "This letter needs a valid project identity before it can be published.",
        ) from error


def _slugify_title(value: object) -> str:
    title = unicodedata.normalize("NFKD", str(value or "").strip())
    ascii_title = title.encode("ascii", "ignore").decode("ascii").casefold()
    slug = _UNSUPPORTED_SLUG_CHARACTERS.sub("-", ascii_title).strip("-")[:80]
    slug = slug.rstrip("-")
    if not slug:
        raise GitHubOperationError(
            "invalid_public_title",
            "Add a letter title before publishing.",
        )
    return _valid_public_path(slug)


def _publication_path(metadata: dict) -> str:
    if str(metadata.get(PUBLICATION_PROVIDER_KEY, "")).strip() == GITHUB_PAGES_PROVIDER_ID:
        existing = str(metadata.get(PUBLISHED_PUBLIC_PATH_KEY, "")).strip()
        if existing:
            return _valid_public_path(existing)
    _project_publication_id(metadata)
    return _slugify_title(metadata.get("recipient_title", ""))


def _publication_prefix(repository: str, owner: str, public_path: str) -> str:
    user_site = github_pages_repository_name(owner)
    if repository.casefold() == user_site.casefold():
        return f"{public_path}/"
    if repository.casefold() == GITHUB_REPOSITORY_NAME.casefold():
        return f"{LEGACY_PUBLICATION_PREFIX}/{public_path}/"
    raise GitHubOperationError(
        "repository_mismatch",
        "This letter's saved GitHub publishing project is not recognized.",
    )


def _allocate_public_path(base: str, existing_paths: Iterable[str]) -> str:
    occupied = {
        str(path).split("/", 1)[0].casefold()
        for path in existing_paths
        if "/" in str(path) and str(path).split("/", 1)[0]
    }
    if base.casefold() not in occupied:
        return base
    suffix = 2
    while True:
        suffix_text = f"-{suffix}"
        candidate = f"{base[:80 - len(suffix_text)].rstrip('-')}{suffix_text}"
        if candidate.casefold() not in occupied:
            return _valid_public_path(candidate)
        suffix += 1


def _user_site_url(owner: str) -> str:
    repository = github_pages_repository_name(owner)
    return f"https://{repository.casefold()}"


def _publication_url(owner: str, repository: str, public_path: str) -> str:
    user_site = github_pages_repository_name(owner)
    if repository.casefold() == user_site.casefold():
        return f"{_user_site_url(owner)}/{quote(public_path)}/"
    if repository.casefold() == GITHUB_REPOSITORY_NAME.casefold():
        return (
            f"{_user_site_url(owner)}/{quote(repository)}/"
            f"{quote(LEGACY_PUBLICATION_PREFIX)}/{quote(public_path)}/"
        )
    raise GitHubOperationError(
        "repository_mismatch",
        "This letter's saved GitHub publishing project is not recognized.",
    )


def _safe_pages_url(value: object) -> str:
    candidate = str(value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubOperationError(
            "invalid_url",
            "GitHub did not return a valid public page address.",
        )
    return candidate


def _fetch_public_bytes(url: str, timeout: float) -> bytes | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "LetterSmith/GitHub-Publisher", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if int(response.status) != 200:
                return None
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


class GitHubPagesPublisher(Publisher):
    def __init__(
        self,
        project_root: str | Path,
        session: GitHubSession,
        *,
        configuration: GitHubApplicationConfiguration | None = None,
        api: GitHubAPI | None = None,
        auth_service: GitHubConnectionService | None = None,
        connection_generation: int = 0,
        public_fetcher: Callable[[str, float], bytes | None] = _fetch_public_bytes,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        cancelled: Callable[[], bool] = lambda: False,
        verification_timeout: int = 180,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.session = session
        self.configuration = configuration or github_application_configuration()
        self.api = api or GitHubAPI(session.token.access_token)
        self.auth_service = auth_service
        self.connection_generation = max(0, int(connection_generation))
        self.public_fetcher = public_fetcher
        self.sleeper = sleeper
        self.clock = clock
        self.cancelled = cancelled
        self.verification_timeout = max(1, int(verification_timeout))

    def is_configured(self) -> bool:
        return self.configuration.configured

    def configure(self, parent=None) -> PublishConfiguration:
        del parent
        repository = github_pages_repository_name(self.session.account.login)
        return PublishConfiguration(
            configured=self.is_configured(),
            repository=(
                f"{self.session.account.login}/{repository}"
                if self.is_configured()
                else ""
            ),
            message=(
                ""
                if self.is_configured()
                else "GitHub publishing is not configured in this Letter Smith build."
            ),
        )

    def publish(self, build_dir: Path, metadata: dict) -> PublishResult:
        build = Path(build_dir).resolve()
        owner = self.session.account.login
        target_repository = github_pages_repository_name(owner)
        _LOGGER.info(
            "GitHub publishing started: account=%s repository=%s build=%s",
            owner,
            target_repository,
            build,
        )
        if not (build / "index.html").is_file():
            return PublishResult(
                False,
                message="The generated letter is incomplete.",
                error_code="invalid_build",
            )
        try:
            self.configuration.require_configured()
            previous_owner = str(
                metadata.get(PUBLISHED_GITHUB_OWNER_KEY, "")
            ).strip()
            previous_repository = str(
                metadata.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
            ).strip()
            if previous_owner and (
                previous_owner.casefold() != owner.casefold()
            ):
                raise GitHubOperationError(
                    "repository_owner_mismatch",
                    "This letter was published from a different GitHub account. Reconnect that account before publishing it again.",
                    technical_details=(
                        f"expected_owner={previous_owner}; "
                        f"authenticated_owner={owner}"
                    ),
                )
            if previous_repository and (
                previous_repository.casefold()
                != target_repository.casefold()
            ):
                raise GitHubOperationError(
                    "repository_mismatch",
                    "This letter uses an older GitHub publishing location. Unpublish it first, then publish again to create a clean link.",
                    technical_details=(
                        f"expected_repository={previous_repository}; "
                        f"configured_repository={target_repository}"
                    ),
                )
            project_id = _project_publication_id(metadata)
            requested_public_path = _publication_path(metadata)
            has_saved_publication = bool(
                str(metadata.get(PUBLICATION_PROVIDER_KEY, "")).strip()
                == GITHUB_PAGES_PROVIDER_ID
                and str(metadata.get(PUBLISHED_PUBLIC_PATH_KEY, "")).strip()
            )
            files = _published_files(build)
            if not any(relative == "index.html" for _path, relative, _size in files):
                raise GitHubOperationError(
                    "invalid_build",
                    "The generated letter is missing its main page.",
                )
            source_fingerprint = str(metadata.get("source_fingerprint", "")).strip()
            bundle_sha256 = _bundle_digest(files)
            repository, repository_created = self._ensure_repository(
                target_repository
            )
            repository_name = str(repository["name"])
            branch = str(repository.get("default_branch", "main") or "main")
            commit_sha, tree_sha, tree, initialized = self._current_tree(
                owner,
                repository_name,
                branch,
            )
            existing_entries = {
                str(entry.get("path", "")): entry
                for entry in tree
                if isinstance(entry, dict)
            }
            public_path = (
                requested_public_path
                if has_saved_publication
                else _allocate_public_path(requested_public_path, existing_entries)
            )
            prefix = _publication_prefix(repository_name, owner, public_path)
            if has_saved_publication:
                target_entries = tuple(
                    entry
                    for entry in tree
                    if isinstance(entry, dict)
                    and str(entry.get("path", "")).startswith(prefix)
                    and str(entry.get("type", "")) == "blob"
                )
                self._validated_remote_marker(
                    owner,
                    repository_name,
                    prefix,
                    project_id,
                    public_path,
                    target_entries,
                )
                if not target_entries:
                    _LOGGER.warning(
                        "Saved GitHub publication path is missing remotely; recreating it: %s",
                        prefix,
                    )
            nonce = secrets.token_urlsafe(18)
            published_at = datetime.now(timezone.utc).isoformat()
            marker = json.dumps(
                {
                    "schema_version": 2,
                    "provider": GITHUB_PAGES_PROVIDER_ID,
                    "project_id": project_id,
                    "public_path": public_path,
                    "published_at": published_at,
                    "source_fingerprint": source_fingerprint,
                    "bundle_sha256": bundle_sha256,
                    "deployment_nonce": nonce,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            updates = self._tree_updates(
                owner,
                repository_name,
                files,
                prefix,
                marker,
                existing_entries,
                include_root_index=(
                    repository_created or "index.html" not in existing_entries
                ),
            )
            new_tree = self.api.request(
                "POST",
                f"/repos/{quote(owner)}/{quote(repository_name)}/git/trees",
                {"base_tree": tree_sha, "tree": updates},
                expected=(201,),
            )
            new_tree_sha = str(new_tree.get("sha", "")).strip()
            if not new_tree_sha:
                raise GitHubOperationError(
                    "invalid_response",
                    "GitHub did not confirm the uploaded letter files.",
                )
            commit = self.api.request(
                "POST",
                f"/repos/{quote(owner)}/{quote(repository_name)}/git/commits",
                {
                    "message": "Update published Letter Smith letter",
                    "tree": new_tree_sha,
                    "parents": [commit_sha],
                },
                expected=(201,),
            )
            new_commit_sha = str(commit.get("sha", "")).strip()
            if not new_commit_sha:
                raise GitHubOperationError(
                    "invalid_response",
                    "GitHub did not confirm the published letter update.",
                )
            self.api.request(
                "PATCH",
                f"/repos/{quote(owner)}/{quote(repository_name)}/git/refs/heads/{quote(branch, safe='')}",
                {"sha": new_commit_sha, "force": False},
            )
            self._ensure_pages(owner, repository_name, branch)
            url = _publication_url(owner, repository_name, public_path)
            index_bytes = (build / "index.html").read_bytes()
            if not self._verify_publication(url, marker, index_bytes, nonce):
                raise GitHubOperationError(
                    "publication_timeout",
                    "GitHub received the letter, but the public page was not ready in time. Your local letter is unchanged.",
                )
            publication = {
                PUBLISHED_PAGE_URL_KEY: url,
                PUBLISHED_PUBLIC_PATH_KEY: public_path,
                PUBLISHED_AT_KEY: published_at,
                PROJECT_PUBLISHED_AT_KEY: published_at,
                PUBLISHED_EXPIRES_AT_KEY: "",
                PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                PUBLICATION_VERIFIED_KEY: True,
                PUBLISHED_SOURCE_FINGERPRINT_KEY: source_fingerprint,
                PUBLISHED_GITHUB_OWNER_KEY: owner,
                PUBLISHED_GITHUB_REPOSITORY_KEY: repository_name,
            }
            self.settings.update_fields(publication)
            if self.auth_service is not None:
                self.auth_service.record_operation_success(
                    self.session,
                    expected_generation=self.connection_generation or None,
                )
            _LOGGER.info(
                "GitHub publishing succeeded: account=%s repository=%s "
                "public_path=%s",
                owner,
                repository_name,
                public_path,
            )
            return PublishResult(
                True,
                url=url,
                public_path=public_path,
                provider=GITHUB_PAGES_PROVIDER_ID,
                published_at=published_at,
                expires_at="",
                source_fingerprint=source_fingerprint,
                owner=owner,
                repository=repository_name,
                verified=True,
                message="Published with GitHub.",
            )
        except GitHubOperationError as error:
            _LOGGER.error(
                "GitHub publishing failed: code=%s status=%s details=%s",
                error.code,
                error.status,
                error.technical_details,
            )
            if (
                self.auth_service is not None
                and classify_github_error(error) != GitHubFailureKind.FATAL
            ):
                self.auth_service.record_operation_error(
                    error,
                    self.session,
                    expected_generation=self.connection_generation or None,
                )
            return PublishResult(
                False,
                message=error.user_message,
                technical_details=error.technical_details,
                error_code=error.code,
            )
        except (OSError, ValueError) as error:
            return PublishResult(
                False,
                message="The generated letter could not be prepared for GitHub. Your local letter is unchanged.",
                technical_details=f"{type(error).__name__}: {error}",
                error_code="invalid_build",
            )

    def unpublish(self, metadata: dict) -> PublishResult:
        owner = self.session.account.login
        try:
            self.configuration.require_configured()
            project_id = _project_publication_id(metadata)
            previous_owner = str(
                metadata.get(PUBLISHED_GITHUB_OWNER_KEY, "")
            ).strip()
            repository_name = str(
                metadata.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
            ).strip()
            public_path = _valid_public_path(
                metadata.get(PUBLISHED_PUBLIC_PATH_KEY, "")
            )
            if not previous_owner or not repository_name:
                raise GitHubOperationError(
                    "missing_publication_identity",
                    "This letter does not have complete GitHub publication details.",
                )
            if previous_owner.casefold() != owner.casefold():
                raise GitHubOperationError(
                    "repository_owner_mismatch",
                    "This letter was published from a different GitHub account. Reconnect that account before unpublishing it.",
                    technical_details=(
                        f"expected_owner={previous_owner}; authenticated_owner={owner}"
                    ),
                )
            prefix = _publication_prefix(repository_name, owner, public_path)
            url = _publication_url(owner, repository_name, public_path)
            saved_url = f"{_safe_pages_url(metadata.get(PUBLISHED_PAGE_URL_KEY, ''))}/"
            if saved_url.casefold() != url.casefold():
                raise GitHubOperationError(
                    "publication_conflict",
                    "The saved public link does not match this letter's GitHub publication identity.",
                )

            repository = self.api.request(
                "GET",
                f"/repos/{quote(owner)}/{quote(repository_name)}",
            )
            branch = str(repository.get("default_branch", "main") or "main")
            commit_sha, tree_sha, tree, _initialized = self._current_tree(
                owner,
                repository_name,
                branch,
            )
            target_entries = [
                entry
                for entry in tree
                if isinstance(entry, dict)
                and str(entry.get("path", "")).startswith(prefix)
                and str(entry.get("type", "")) == "blob"
            ]
            marker_bytes = self._validated_remote_marker(
                owner,
                repository_name,
                prefix,
                project_id,
                public_path,
                target_entries,
            )

            index_entry = next(
                (
                    entry
                    for entry in target_entries
                    if str(entry.get("path", "")) == f"{prefix}index.html"
                ),
                None,
            )
            index_bytes = (
                self._remote_blob_bytes(
                    owner,
                    repository_name,
                    str(index_entry.get("sha", "")),
                )
                if index_entry is not None
                else None
            )
            if target_entries:
                updates = [
                    {
                        "path": str(entry["path"]),
                        "mode": "100644",
                        "type": "blob",
                        "sha": None,
                    }
                    for entry in target_entries
                ]
                new_tree = self.api.request(
                    "POST",
                    f"/repos/{quote(owner)}/{quote(repository_name)}/git/trees",
                    {"base_tree": tree_sha, "tree": updates},
                    expected=(201,),
                )
                new_tree_sha = str(new_tree.get("sha", "")).strip()
                if not new_tree_sha:
                    raise GitHubOperationError(
                        "invalid_response",
                        "GitHub did not confirm the publication removal.",
                    )
                commit = self.api.request(
                    "POST",
                    f"/repos/{quote(owner)}/{quote(repository_name)}/git/commits",
                    {
                        "message": "Unpublish Letter Smith letter",
                        "tree": new_tree_sha,
                        "parents": [commit_sha],
                    },
                    expected=(201,),
                )
                new_commit_sha = str(commit.get("sha", "")).strip()
                if not new_commit_sha:
                    raise GitHubOperationError(
                        "invalid_response",
                        "GitHub did not confirm the publication removal commit.",
                    )
                self.api.request(
                    "PATCH",
                    f"/repos/{quote(owner)}/{quote(repository_name)}/git/refs/heads/{quote(branch, safe='')}",
                    {"sha": new_commit_sha, "force": False},
                )

            nonce = secrets.token_urlsafe(18)
            if not self._verify_unpublication(
                url,
                marker_bytes,
                index_bytes,
                nonce,
            ):
                raise GitHubOperationError(
                    "unpublication_timeout",
                    "GitHub removed the publication files, but the public link still served the letter. Local publication details were kept so you can retry.",
                )
            self.settings.update_fields(empty_publication_settings())
            if self.auth_service is not None:
                self.auth_service.record_operation_success(
                    self.session,
                    expected_generation=self.connection_generation or None,
                )
            _LOGGER.info(
                "GitHub unpublish succeeded: account=%s repository=%s public_path=%s",
                owner,
                repository_name,
                public_path,
            )
            return PublishResult(
                True,
                owner=owner,
                repository=repository_name,
                message="The online copy was removed. The local letter was preserved.",
            )
        except GitHubOperationError as error:
            _LOGGER.error(
                "GitHub unpublish failed: code=%s status=%s details=%s",
                error.code,
                error.status,
                error.technical_details,
            )
            if (
                self.auth_service is not None
                and classify_github_error(error) != GitHubFailureKind.FATAL
            ):
                self.auth_service.record_operation_error(
                    error,
                    self.session,
                    expected_generation=self.connection_generation or None,
                )
            return PublishResult(
                False,
                message=error.user_message,
                technical_details=error.technical_details,
                error_code=error.code,
            )
        except (OSError, ValueError) as error:
            return PublishResult(
                False,
                message="The GitHub publication details are invalid. The local letter was unchanged.",
                technical_details=f"{type(error).__name__}: {error}",
                error_code="invalid_publication",
            )

    def _remote_blob_bytes(
        self,
        owner: str,
        repository: str,
        sha: str,
    ) -> bytes:
        if not sha:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a remote publication file identity.",
            )
        response = self.api.request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repository)}/git/blobs/{quote(sha)}",
        )
        if str(response.get("encoding", "")).casefold() != "base64":
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned an unsupported publication file encoding.",
            )
        try:
            encoded = "".join(str(response.get("content", "")).split())
            return base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned invalid publication file content.",
            ) from error

    def _validated_remote_marker(
        self,
        owner: str,
        repository: str,
        prefix: str,
        project_id: str,
        public_path: str,
        target_entries: Iterable[dict],
    ) -> bytes | None:
        entries = tuple(target_entries)
        marker_path = f"{prefix}{PUBLICATION_MARKER}"
        marker_entry = next(
            (
                entry
                for entry in entries
                if str(entry.get("path", "")) == marker_path
            ),
            None,
        )
        if entries and marker_entry is None:
            raise GitHubOperationError(
                "publication_conflict",
                "The saved public link points to files that are not owned by this letter.",
            )
        if marker_entry is None:
            return None
        marker_bytes = self._remote_blob_bytes(
            owner,
            repository,
            str(marker_entry.get("sha", "")),
        )
        try:
            marker_payload = json.loads(marker_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GitHubOperationError(
                "publication_conflict",
                "The remote publication identity could not be verified.",
            ) from error
        remote_project_id = str(marker_payload.get("project_id", "")).strip()
        remote_public_path = str(marker_payload.get("public_path", "")).strip()
        legacy_match = not remote_project_id and public_path == project_id
        if (
            remote_public_path.casefold() != public_path.casefold()
            or (
                remote_project_id
                and remote_project_id.casefold() != project_id.casefold()
            )
            or (not remote_project_id and not legacy_match)
        ):
            raise GitHubOperationError(
                "publication_conflict",
                "The remote publication belongs to a different letter.",
            )
        return marker_bytes

    def _ensure_repository(self, name: str) -> tuple[dict, bool]:
        owner = self.session.account.login
        path = f"/repos/{quote(owner)}/{quote(name)}"
        try:
            repository = self.api.request("GET", path)
            created = False
        except GitHubOperationError as error:
            if error.code != "not_found":
                raise
            repository = self.api.request(
                "POST",
                "/user/repos",
                {
                    "name": name,
                    "description": "Letters published with Letter Smith",
                    "private": False,
                    "auto_init": True,
                    "has_issues": False,
                    "has_projects": False,
                    "has_wiki": False,
                },
                expected=(201,),
            )
            created = True
        if repository.get("private") is True:
            raise GitHubOperationError(
                "repository_private",
                f"{name} exists but is private. GitHub Pages requires a public user-site project.",
            )
        if repository.get("archived") is True or repository.get("disabled") is True:
            raise GitHubOperationError(
                "repository_unavailable",
                f"{name} is unavailable for updates on GitHub.",
            )
        if not str(repository.get("name", "")).strip():
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return usable publishing-project information.",
            )
        return repository, created

    def _current_tree(
        self,
        owner: str,
        repository: str,
        branch: str,
    ) -> tuple[str, str, list, bool]:
        ref_path = (
            f"/repos/{quote(owner)}/{quote(repository)}/git/ref/heads/"
            f"{quote(branch, safe='')}"
        )
        initialized = False
        try:
            reference = self.api.request("GET", ref_path)
        except GitHubOperationError as error:
            if error.code != "not_found":
                raise
            self.api.request(
                "PUT",
                f"/repos/{quote(owner)}/{quote(repository)}/contents/.nojekyll",
                {
                    "message": "Initialize Letter Smith publishing",
                    "content": base64.b64encode(b"\n").decode("ascii"),
                    "branch": branch,
                },
                expected=(201,),
            )
            initialized = True
            reference = self.api.request("GET", ref_path)
        commit_sha = str(reference.get("object", {}).get("sha", "")).strip()
        if not commit_sha:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return the publishing branch state.",
            )
        commit = self.api.request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repository)}/git/commits/{quote(commit_sha)}",
        )
        tree_sha = str(commit.get("tree", {}).get("sha", "")).strip()
        if not tree_sha:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return the publishing file state.",
            )
        tree_response = self.api.request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repository)}/git/trees/{quote(tree_sha)}?recursive=1",
        )
        if tree_response.get("truncated") is True:
            raise GitHubOperationError(
                "repository_too_large",
                "The Letter Smith publishing project is too large to update safely.",
            )
        tree = tree_response.get("tree", [])
        if not isinstance(tree, list):
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned an invalid publishing file list.",
            )
        return commit_sha, tree_sha, tree, initialized

    def _tree_updates(
        self,
        owner: str,
        repository: str,
        files: tuple[tuple[Path, str, int], ...],
        prefix: str,
        marker: bytes,
        existing_entries: dict[str, dict],
        *,
        include_root_index: bool,
    ) -> list[dict[str, object]]:
        payloads = {
            f"{prefix}{relative}": path.read_bytes()
            for path, relative, _size in files
        }
        payloads[f"{prefix}{PUBLICATION_MARKER}"] = marker
        payloads[REPOSITORY_MARKER] = json.dumps(
            {"schema_version": 1, "application": "Letter Smith"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payloads[".nojekyll"] = b"\n"
        if include_root_index:
            payloads["index.html"] = _ROOT_INDEX

        updates: list[dict[str, object]] = []
        target_paths = set(payloads)
        for path, entry in existing_entries.items():
            object_type = str(entry.get("type", ""))
            if path.startswith(prefix) and object_type == "blob" and path not in target_paths:
                updates.append(
                    {"path": path, "mode": "100644", "type": "blob", "sha": None}
                )

        existing_blob_shas = {
            str(entry.get("sha", "")).strip()
            for entry in existing_entries.values()
            if str(entry.get("type", "")) == "blob"
            and str(entry.get("sha", "")).strip()
        }
        binary_uploads: list[tuple[str, bytes]] = []
        inline_bytes = 0
        for path, content in sorted(payloads.items()):
            content_sha = _git_blob_sha(content)
            existing = existing_entries.get(path, {})
            if (
                str(existing.get("type", "")) == "blob"
                and str(existing.get("sha", "")).strip() == content_sha
            ):
                continue
            if content_sha in existing_blob_shas:
                updates.append(
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": content_sha,
                    }
                )
                continue
            text: str | None = None
            if (
                len(content) <= MAX_INLINE_TEXT_BYTES
                and inline_bytes + len(content) <= MAX_INLINE_TREE_BYTES
            ):
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            if text is not None:
                inline_bytes += len(content)
                updates.append(
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "content": text,
                    }
                )
                continue
            binary_uploads.append((path, content))

        uploaded_shas: dict[str, str] = {}
        if binary_uploads:
            self._raise_if_cancelled()
            largest_upload = max(len(content) for _path, content in binary_uploads)
            memory_worker_limit = max(1, MAX_PARALLEL_BLOB_BYTES // largest_upload)
            worker_count = min(
                MAX_PARALLEL_BLOB_UPLOADS,
                memory_worker_limit,
                len(binary_uploads),
            )
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="lettersmith-github-blob",
            ) as executor:
                futures = {}
                try:
                    for path, content in binary_uploads:
                        self._raise_if_cancelled()
                        future = executor.submit(
                            self._upload_blob,
                            owner,
                            repository,
                            path,
                            content,
                        )
                        futures[future] = path
                    for future in as_completed(futures):
                        path = futures[future]
                        uploaded_shas[path] = future.result()
                        self._raise_if_cancelled()
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
        for path, _content in binary_uploads:
            updates.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": uploaded_shas[path],
                }
            )
        return updates

    def _upload_blob(
        self,
        owner: str,
        repository: str,
        path: str,
        content: bytes,
    ) -> str:
        self._raise_if_cancelled()
        blob = self.api.request(
            "POST",
            f"/repos/{quote(owner)}/{quote(repository)}/git/blobs",
            {
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
            expected=(201,),
        )
        self._raise_if_cancelled()
        sha = str(blob.get("sha", "")).strip()
        if not sha:
            raise GitHubOperationError(
                "upload_failure",
                "GitHub did not confirm an uploaded letter file.",
                technical_details=f"path={path}",
            )
        return sha

    def _raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise GitHubOperationError(
                "publication_cancelled",
                "Publishing was canceled. Your local letter is unchanged.",
            )

    def _ensure_pages(self, owner: str, repository: str, branch: str) -> str:
        path = f"/repos/{quote(owner)}/{quote(repository)}/pages"
        try:
            pages = self.api.request("GET", path)
            source = pages.get("source", {})
            if not isinstance(source, dict):
                source = {}
            if source.get("branch") != branch or source.get("path") != "/":
                self.api.request(
                    "PUT",
                    path,
                    {
                        "build_type": "legacy",
                        "source": {"branch": branch, "path": "/"},
                    },
                    expected=(204,),
                )
                pages = self.api.request("GET", path)
        except GitHubOperationError as error:
            if error.code != "not_found":
                raise
            pages = self.api.request(
                "POST",
                path,
                {
                    "build_type": "legacy",
                    "source": {"branch": branch, "path": "/"},
                },
                expected=(201,),
            )
        if not str(pages.get("html_url", "")).strip():
            pages = self.api.request("GET", path)
        return _safe_pages_url(pages.get("html_url", ""))

    def _verify_publication(
        self,
        url: str,
        marker: bytes,
        index: bytes,
        nonce: str,
    ) -> bool:
        deadline = self.clock() + self.verification_timeout
        marker_url = f"{url}{quote(PUBLICATION_MARKER)}?lettersmith={quote(nonce)}"
        index_url = f"{url}index.html?lettersmith={quote(nonce)}"
        while self.clock() < deadline:
            if self.cancelled():
                raise GitHubOperationError(
                    "publication_cancelled",
                    "Publishing was canceled. Your local letter is unchanged.",
                )
            hosted_marker = self.public_fetcher(marker_url, 6.0)
            if hosted_marker == marker:
                hosted_index = self.public_fetcher(index_url, 6.0)
                if hosted_index == index:
                    return True
            self._wait(3.0)
        return False

    def _verify_unpublication(
        self,
        url: str,
        marker: bytes | None,
        index: bytes | None,
        nonce: str,
    ) -> bool:
        deadline = self.clock() + self.verification_timeout
        marker_url = f"{url}{quote(PUBLICATION_MARKER)}?lettersmith={quote(nonce)}"
        index_url = f"{url}index.html?lettersmith={quote(nonce)}"
        while self.clock() < deadline:
            if self.cancelled():
                raise GitHubOperationError(
                    "publication_cancelled",
                    "Unpublishing was canceled. Your local letter is unchanged.",
                )
            hosted_marker = self.public_fetcher(marker_url, 6.0)
            marker_removed = (
                hosted_marker is None if marker is None else hosted_marker != marker
            )
            if marker_removed:
                hosted_index = self.public_fetcher(index_url, 6.0)
                index_removed = (
                    hosted_index is None if index is None else hosted_index != index
                )
                if index_removed:
                    return True
            self._wait(3.0)
        return False

    def _wait(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            if self.cancelled():
                raise GitHubOperationError(
                    "publication_cancelled",
                    "Publishing was canceled. Your local letter is unchanged.",
                )
            duration = min(0.25, remaining)
            self.sleeper(duration)
            remaining -= duration


__all__ = [
    "GITHUB_PAGES_PROVIDER_ID",
    "GitHubPagesPublisher",
    "LEGACY_PUBLICATION_PREFIX",
    "PUBLICATION_MARKER",
    "PUBLICATION_PREFIX",
    "PUBLIC_WARNING_KEY",
    "REPOSITORY_MARKER",
]
