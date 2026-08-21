from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
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
    GitHubAuthenticator,
    GitHubOperationError,
    GitHubSession,
)
from publishing.github_config import (
    GitHubApplicationConfiguration,
    github_application_configuration,
)
from publishing.models import PublishConfiguration, PublishResult
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
)


PUBLICATION_PREFIX = "letters"
PUBLICATION_MARKER = "lettersmith-publication.json"
REPOSITORY_MARKER = ".lettersmith-publishing.json"
PUBLIC_WARNING_KEY = "github_public_warning_acknowledged"
MAX_BLOB_BYTES = 90 * 1024 * 1024
MAX_BUNDLE_BYTES = 900 * 1024 * 1024
_PUBLIC_PATH_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,80}")
_EXCLUDED_ROOT_FILES = {
    "lettersmith-build.json",
    "lettersmith-metadata.json",
    "prompt_writer_state.json",
}
_ROOT_INDEX = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Letter Smith</title></head>
<body><main><h1>Letter Smith</h1><p>Letters published with Letter Smith are available through their direct links.</p></main></body></html>
"""


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


def _valid_public_path(value: object) -> str:
    candidate = str(value or "").strip().casefold()
    if not _PUBLIC_PATH_PATTERN.fullmatch(candidate):
        raise GitHubOperationError(
            "invalid_publication_id",
            "The letter's publication identity is invalid.",
        )
    return candidate


def _publication_path(metadata: dict) -> str:
    if str(metadata.get(PUBLICATION_PROVIDER_KEY, "")).strip() == GITHUB_PAGES_PROVIDER_ID:
        existing = str(metadata.get(PUBLISHED_PUBLIC_PATH_KEY, "")).strip()
        if existing:
            return _valid_public_path(existing)
    project_id = str(metadata.get("project_id", "")).strip()
    try:
        return str(uuid.UUID(project_id))
    except (ValueError, AttributeError) as error:
        raise GitHubOperationError(
            "invalid_project_identity",
            "This letter needs a valid project identity before it can be published.",
        ) from error


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
        self.public_fetcher = public_fetcher
        self.sleeper = sleeper
        self.clock = clock
        self.cancelled = cancelled
        self.verification_timeout = max(1, int(verification_timeout))

    def is_configured(self) -> bool:
        return self.configuration.configured

    def configure(self, parent=None) -> PublishConfiguration:
        del parent
        return PublishConfiguration(
            configured=self.is_configured(),
            repository=(
                f"{self.session.account.login}/{self.configuration.repository_name}"
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
        if not (build / "index.html").is_file():
            return PublishResult(
                False,
                message="The generated letter is incomplete.",
                error_code="invalid_build",
            )
        try:
            self.configuration.require_configured()
            public_path = _publication_path(metadata)
            files = _published_files(build)
            if not any(relative == "index.html" for _path, relative, _size in files):
                raise GitHubOperationError(
                    "invalid_build",
                    "The generated letter is missing its main page.",
                )
            source_fingerprint = str(metadata.get("source_fingerprint", "")).strip()
            bundle_sha256 = _bundle_digest(files)
            nonce = secrets.token_urlsafe(18)
            published_at = datetime.now(timezone.utc).isoformat()
            marker = json.dumps(
                {
                    "schema_version": 1,
                    "provider": GITHUB_PAGES_PROVIDER_ID,
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
            repository, repository_created = self._ensure_repository()
            owner = self.session.account.login
            repository_name = str(repository["name"])
            branch = str(repository.get("default_branch", "main") or "main")
            commit_sha, tree_sha, tree, initialized = self._current_tree(
                owner,
                repository_name,
                branch,
            )
            existing_paths = {
                str(entry.get("path", "")): str(entry.get("type", ""))
                for entry in tree
                if isinstance(entry, dict)
            }
            if (
                not repository_created
                and not initialized
                and REPOSITORY_MARKER not in existing_paths
            ):
                raise GitHubOperationError(
                    "repository_conflict",
                    "A GitHub project named LetterSmith-Published already exists but is not managed by Letter Smith.",
                )
            updates = self._tree_updates(
                owner,
                repository_name,
                files,
                public_path,
                marker,
                existing_paths,
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
            pages_url = self._ensure_pages(owner, repository_name, branch)
            url = (
                f"{pages_url}/{quote(PUBLICATION_PREFIX)}/"
                f"{quote(public_path)}/"
            )
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
                PUBLISHED_EXPIRES_AT_KEY: "",
                PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                PUBLICATION_VERIFIED_KEY: True,
                PUBLISHED_SOURCE_FINGERPRINT_KEY: source_fingerprint,
                PUBLISHED_GITHUB_OWNER_KEY: owner,
                PUBLISHED_GITHUB_REPOSITORY_KEY: repository_name,
            }
            self.settings.update_fields(publication)
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
            if error.code == "authentication":
                try:
                    GitHubAuthenticator().sign_out()
                except GitHubOperationError:
                    pass
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

    def _ensure_repository(self) -> tuple[dict, bool]:
        owner = self.session.account.login
        name = self.configuration.repository_name
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
                "LetterSmith-Published exists but is private. GitHub Pages publishing requires Letter Smith's managed public project.",
            )
        if repository.get("archived") is True or repository.get("disabled") is True:
            raise GitHubOperationError(
                "repository_unavailable",
                "LetterSmith-Published is unavailable for updates on GitHub.",
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
        public_path: str,
        marker: bytes,
        existing_paths: dict[str, str],
    ) -> list[dict[str, object]]:
        prefix = f"{PUBLICATION_PREFIX}/{public_path}/"
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
        payloads["index.html"] = _ROOT_INDEX

        updates: list[dict[str, object]] = []
        target_paths = set(payloads)
        for path, object_type in existing_paths.items():
            if path.startswith(prefix) and object_type == "blob" and path not in target_paths:
                updates.append(
                    {"path": path, "mode": "100644", "type": "blob", "sha": None}
                )
        for path, content in sorted(payloads.items()):
            blob = self.api.request(
                "POST",
                f"/repos/{quote(owner)}/{quote(repository)}/git/blobs",
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
                expected=(201,),
            )
            sha = str(blob.get("sha", "")).strip()
            if not sha:
                raise GitHubOperationError(
                    "upload_failure",
                    "GitHub did not confirm an uploaded letter file.",
                    technical_details=f"path={path}",
                )
            updates.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": sha}
            )
        return updates

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
            hosted_index = self.public_fetcher(index_url, 6.0)
            if hosted_marker == marker and hosted_index == index:
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
    "PUBLICATION_MARKER",
    "PUBLICATION_PREFIX",
    "PUBLIC_WARNING_KEY",
    "REPOSITORY_MARKER",
]
