from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote

from publishing.base import Publisher
from publishing.expiration import (
    PUBLICATION_CLEANUP_POLICY,
    PUBLICATION_TTL_DAYS,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    is_publication_expired,
    publication_window,
)
from publishing.models import PublishConfiguration, PublishResult
from settings_store import PUBLISHED_PAGE_URL_KEY, SettingsStore
from transactional_io import PathTransaction, atomic_write_text


REPOSITORY_KEY = "github_pages_repository"
WORKSPACE_KEY = "github_pages_workspace"
BRANCH_KEY = "github_pages_branch"
PUBLIC_WARNING_KEY = "github_pages_public_warning_acknowledged"
DEFAULT_REPOSITORY = "letter-smith-publishing"
DEFAULT_BRANCH = "main"
PUBLICATION_MARKER = "lettersmith-publication.json"
_LOGGER = logging.getLogger(__name__)

PAGES_WORKFLOW = """name: Deploy Letter Smith pages
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: .
      - id: deployment
        uses: actions/deploy-pages@v4
"""

ROBOTS_TEXT = "User-agent: *\nDisallow: /\n"


def safe_public_path(recipient: str, title: str, *, token: str | None = None) -> str:
    raw = f"{recipient}-{title}".casefold()
    slug = "".join(char if char.isalnum() else "-" for char in raw)
    slug = "-".join(part for part in slug.split("-") if part)[:56] or "letter"
    suffix = (token or secrets.token_hex(3)).casefold()
    suffix = "".join(char for char in suffix if char.isalnum())[:12]
    if not suffix:
        raise ValueError("A public-path token is required.")
    return f"{slug}-{suffix}"


def github_pages_url(repository: str, public_path: str) -> str:
    owner, name = repository.split("/", 1)
    return f"https://{owner}.github.io/{name}/letters/{quote(public_path)}/"


def _directory_digest(directory: Path) -> str:
    root = Path(directory).resolve()
    digest = hashlib.sha256()
    files = sorted(
        (
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file()
        ),
        key=lambda candidate: (
            candidate.relative_to(root).as_posix()
        ),
    )
    for path in files:
        if path.is_symlink():
            raise ValueError(
                "Published letter bundles cannot contain symbolic links."
            )
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(
                1024 * 1024
            ):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _publication_marker(
    build: Path,
    public_path: str,
    *,
    published_at: datetime,
    expires_at: datetime,
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "build_sha256": (
                _directory_digest(build)
            ),
            "published_at": published_at.isoformat(),
            "public_path": public_path,
            "expires_at": expires_at.isoformat(),
            "expires_after_days": PUBLICATION_TTL_DAYS,
            "cleanup_policy": PUBLICATION_CLEANUP_POLICY,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


class GitHubPagesPublisher(Publisher):
    def __init__(
        self,
        project_root: str | Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        poller: Callable[[str, float, float], bool] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.runner = runner
        self.poller = poller or self.poll_url

    @staticmethod
    def git_available() -> bool:
        return shutil.which("git") is not None

    @staticmethod
    def gh_available() -> bool:
        return shutil.which("gh") is not None

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            check=check,
            capture_output=True,
            text=True,
        )

    def authenticated(self) -> bool:
        if not self.gh_available():
            return False
        try:
            result = self._run(("gh", "auth", "status"), check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    @staticmethod
    def _repository_is_valid(repository: str) -> bool:
        parts = repository.strip().split("/")
        return (
            len(parts) == 2
            and all(
                part
                and part not in {".", ".."}
                and not any(character.isspace() for character in part)
                for part in parts
            )
        )

    @classmethod
    def _origin_matches_repository(cls, origin: str, repository: str) -> bool:
        if not cls._repository_is_valid(repository):
            return False
        normalized = origin.strip().rstrip("/")
        if normalized.casefold().endswith(".git"):
            normalized = normalized[:-4]
        expected = repository.strip("/").casefold()
        folded = normalized.casefold()
        return folded.endswith(f"github.com/{expected}") or folded.endswith(
            f"github.com:{expected}"
        )

    def _workspace_has_expected_origin(
        self,
        workspace: Path,
        repository: str,
    ) -> bool:
        try:
            result = self._run(
                ("git", "remote", "get-url", "origin"),
                cwd=workspace,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and self._origin_matches_repository(
            result.stdout,
            repository,
        )

    def is_configured(self) -> bool:
        try:
            settings = self.settings.snapshot()
            repository = str(settings.get(REPOSITORY_KEY, "")).strip()
            workspace_value = str(settings.get(WORKSPACE_KEY, "")).strip()
        except OSError:
            return False
        if not workspace_value:
            return False
        workspace = Path(workspace_value).expanduser()
        return (
            self.git_available()
            and self._repository_is_valid(repository)
            and workspace.is_dir()
            and (workspace / ".git").exists()
            and self._workspace_has_expected_origin(workspace, repository)
        )

    def configure(self, parent=None) -> PublishConfiguration:
        repository = ""
        workspace: Path | None = None
        if self.is_configured():
            settings = self.settings.snapshot()
            repository = str(settings.get(REPOSITORY_KEY, "")).strip()
            workspace = Path(str(settings.get(WORKSPACE_KEY, ""))).expanduser()
            return PublishConfiguration(
                True,
                repository,
                workspace,
                "GitHub Pages is ready.",
            )
        if not self.git_available():
            return PublishConfiguration(False, message="Git is not installed.")
        if not self.gh_available():
            return PublishConfiguration(
                False,
                message=(
                    "GitHub CLI is required to configure publishing for the "
                    "first time."
                ),
            )

        try:
            if not self.authenticated():
                login = self._run(
                    ("gh", "auth", "login", "--web"),
                    check=False,
                )
                if login.returncode != 0 or not self.authenticated():
                    return PublishConfiguration(
                        False,
                        message="GitHub authentication was not completed.",
                    )

            settings = self.settings.snapshot()
            repository = str(settings.get(REPOSITORY_KEY, "")).strip()
            if not repository:
                owner_result = self._run(
                    ("gh", "api", "user", "--jq", ".login")
                )
                owner = owner_result.stdout.strip()
                if not owner:
                    return PublishConfiguration(
                        False,
                        message="GitHub account information could not be read.",
                    )
                repository = f"{owner}/{DEFAULT_REPOSITORY}"
            if not self._repository_is_valid(repository):
                return PublishConfiguration(
                    False,
                    repository=repository,
                    message=(
                        "The GitHub repository must use the owner/name format."
                    ),
                )

            view = self._run(
                ("gh", "repo", "view", repository),
                check=False,
            )
            if view.returncode != 0:
                create = self._run(
                    (
                        "gh",
                        "repo",
                        "create",
                        repository,
                        "--public",
                        "--confirm",
                    ),
                    check=False,
                )
                if create.returncode != 0:
                    return PublishConfiguration(
                        False,
                        repository=repository,
                        message="Could not create the publishing repository.",
                    )

            workspace_value = str(settings.get(WORKSPACE_KEY, "")).strip()
            workspace = (
                Path(workspace_value).expanduser().resolve()
                if workspace_value
                else (self.project_root / ".lettersmith-publishing").resolve()
            )
            if not (workspace / ".git").is_dir():
                if workspace.exists() and any(workspace.iterdir()):
                    return PublishConfiguration(
                        False,
                        repository=repository,
                        workspace=workspace,
                        message="The publishing workspace is not empty.",
                    )
                workspace.parent.mkdir(parents=True, exist_ok=True)
                clone = self._run(
                    ("gh", "repo", "clone", repository, str(workspace)),
                    check=False,
                )
                if clone.returncode != 0:
                    return PublishConfiguration(
                        False,
                        repository=repository,
                        workspace=workspace,
                        message="Could not prepare the publishing workspace.",
                    )
            if not self._workspace_has_expected_origin(
                workspace,
                repository,
            ):
                return PublishConfiguration(
                    False,
                    repository=repository,
                    workspace=workspace,
                    message=(
                        "The publishing workspace does not have the expected "
                        "GitHub origin."
                    ),
                )

            self._install_pages_files(workspace)
            self._run(
                (
                    "git",
                    "add",
                    ".github/workflows/pages.yml",
                    "robots.txt",
                ),
                cwd=workspace,
            )
            status = self._run(
                ("git", "status", "--porcelain"),
                cwd=workspace,
            )
            if status.stdout.strip():
                self._run(
                    (
                        "git",
                        "commit",
                        "-m",
                        "Configure Letter Smith publishing",
                    ),
                    cwd=workspace,
                )
                self._run(
                    ("git", "push", "origin", DEFAULT_BRANCH),
                    cwd=workspace,
                )

            self._run(
                (
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/pages",
                    "-f",
                    "build_type=workflow",
                ),
                check=False,
            )
            verify = self._run(
                ("gh", "api", f"repos/{repository}/pages"),
                check=False,
            )
            if verify.returncode != 0:
                return PublishConfiguration(
                    False,
                    repository=repository,
                    workspace=workspace,
                    message="GitHub Pages could not be enabled.",
                )

            self.settings.update_fields(
                **{
                    REPOSITORY_KEY: repository,
                    WORKSPACE_KEY: str(workspace),
                    BRANCH_KEY: DEFAULT_BRANCH,
                }
            )
        except (OSError, subprocess.SubprocessError):
            _LOGGER.exception("GitHub Pages configuration failed.")
            return PublishConfiguration(
                False,
                repository=repository,
                workspace=workspace,
                message="GitHub publishing setup could not be completed.",
            )
        return PublishConfiguration(
            True,
            repository,
            workspace,
            "GitHub Pages is ready.",
        )

    @staticmethod
    def _install_pages_files(workspace: Path) -> None:
        atomic_write_text(workspace / ".github/workflows/pages.yml", PAGES_WORKFLOW)
        atomic_write_text(workspace / "robots.txt", ROBOTS_TEXT)

    def publish(self, build_dir: Path, metadata: dict) -> PublishResult:
        if not self.is_configured():
            return PublishResult(False, message="GitHub Pages publishing is not configured.")
        build = Path(build_dir).resolve()
        if not (build / "index.html").is_file():
            return PublishResult(False, message="The generated letter is incomplete.")

        settings = self.settings.snapshot()
        repository = str(settings[REPOSITORY_KEY])
        workspace = Path(str(settings[WORKSPACE_KEY])).resolve()
        branch = str(settings.get(BRANCH_KEY, DEFAULT_BRANCH))
        public_path = str(metadata.get("public_path", "")).strip() or safe_public_path(
            str(metadata.get("recipient_name", "")),
            str(metadata.get("recipient_title", "")),
        )
        published_at, expires_at = publication_window()
        destination = workspace / "letters" / public_path
        transaction = PathTransaction(destination, staging_suffix=".publish-staging")
        start_head = ""
        transaction_prepared = False
        pushed = False
        url = ""

        try:
            start_head = self._run(
                ("git", "rev-parse", "HEAD"),
                cwd=workspace,
            ).stdout.strip()
            if not start_head:
                raise RuntimeError(
                    "The publishing workspace has no current Git revision."
                )
            staging = transaction.prepare()
            transaction_prepared = True
            shutil.copytree(build, staging)
            marker = _publication_marker(
                build,
                public_path,
                published_at=published_at,
                expires_at=expires_at,
            )
            atomic_write_text(
                staging / PUBLICATION_MARKER,
                marker,
            )
            index = staging / "index.html"
            if not index.is_file():
                raise RuntimeError("The staged letter has no index.html.")
            transaction.commit(keep_backup=True)
            relative = destination.relative_to(workspace).as_posix()
            self._run(("git", "add", "--", relative), cwd=workspace)
            staged = self._run(
                (
                    "git",
                    "diff",
                    "--cached",
                    "--quiet",
                    "--",
                    relative,
                ),
                cwd=workspace,
                check=False,
            )
            url = github_pages_url(repository, public_path)
            if staged.returncode not in {0, 1}:
                raise RuntimeError(
                    "Git could not inspect the staged publication."
                )
            if staged.returncode == 1:
                self._run(
                    (
                        "git",
                        "commit",
                        "--only",
                        "-m",
                        f"Publish letter: {metadata.get('recipient_name', '')} — "
                        f"{metadata.get('recipient_title', '')}",
                        "--",
                        relative,
                    ),
                    cwd=workspace,
                )
                local_head = self._run(
                    ("git", "rev-parse", "HEAD"),
                    cwd=workspace,
                ).stdout.strip()
                if not local_head:
                    raise RuntimeError(
                        "The publication commit could not be identified."
                    )
                self._run(
                    (
                        "git",
                        "push",
                        "origin",
                        branch,
                    ),
                    cwd=workspace,
                )
                pushed = True
                remote = self._run(
                    (
                        "git",
                        "ls-remote",
                        "--heads",
                        "origin",
                        f"refs/heads/{branch}",
                    ),
                    cwd=workspace,
                ).stdout.strip()
                remote_head = (
                    remote.split(None, 1)[0]
                    if remote
                    else ""
                )
                if remote_head != local_head:
                    raise RuntimeError(
                        "The pushed Git revision could not be confirmed."
                    )
            marker_url = (
                f"{url}{quote(PUBLICATION_MARKER)}"
            )
            if self.poller == self.poll_url:
                confirmed = self.poll_url_content(
                    marker_url,
                    marker.encode("ascii"),
                    120.0,
                    2.0,
                )
            else:
                confirmed = self.poller(
                    url,
                    120.0,
                    2.0,
                )
            if not confirmed:
                raise TimeoutError("The published URL did not become available in time.")
            try:
                transaction.finalize()
            except OSError:
                _LOGGER.exception(
                    "Published letter cleanup failed for %s",
                    destination,
                )
            self.settings.update_fields(
                **{
                    PUBLISHED_AT_KEY: published_at.isoformat(),
                    PUBLISHED_EXPIRES_AT_KEY: expires_at.isoformat(),
                }
            )
            return PublishResult(True, url, public_path, "Published.")
        except Exception as error:
            cleanup_errors: list[str] = []
            if pushed:
                try:
                    transaction.finalize()
                except OSError as cleanup_error:
                    _LOGGER.exception(
                        "Post-push publishing cleanup failed for %s",
                        destination,
                    )
                    cleanup_errors.append(
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                details = f"{type(error).__name__}: {error}"
                if cleanup_errors:
                    details += "; cleanup: " + "; ".join(cleanup_errors)
                return PublishResult(
                    False,
                    url=url,
                    public_path=public_path,
                    message=(
                        "The letter was pushed, but the published page could "
                        "not be confirmed."
                    ),
                    technical_details=details,
                )

            rollback_ok = True
            if start_head:
                try:
                    reset = self._run(
                        ("git", "reset", "--mixed", start_head),
                        cwd=workspace,
                        check=False,
                    )
                    if reset.returncode != 0:
                        rollback_ok = False
                        cleanup_errors.append(
                            "Git could not restore the previous revision."
                        )
                except (OSError, subprocess.SubprocessError) as cleanup_error:
                    rollback_ok = False
                    cleanup_errors.append(
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            if transaction_prepared:
                try:
                    transaction.rollback()
                except Exception as cleanup_error:
                    rollback_ok = False
                    cleanup_errors.append(
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            details = f"{type(error).__name__}: {error}"
            if cleanup_errors:
                details += "; rollback: " + "; ".join(cleanup_errors)
            return PublishResult(
                False,
                public_path=public_path,
                message=(
                    "Publishing failed. The previous live version was preserved."
                    if rollback_ok
                    else (
                        "Publishing failed, and the publishing workspace "
                        "needs attention."
                    )
                ),
                technical_details=details,
            )

    def cleanup_expired(
        self,
        public_path: str,
        expires_at: object,
        *,
        now: datetime | None = None,
    ) -> PublishResult:
        """Remove one verified expired publication from its owning workspace."""
        public_path = str(public_path).strip()
        if (
            not public_path
            or Path(public_path).name != public_path
            or public_path in {".", ".."}
            or not is_publication_expired(expires_at, now=now)
        ):
            return PublishResult(
                False,
                public_path=public_path,
                message="The publication is not eligible for cleanup.",
            )
        if not self.is_configured():
            return PublishResult(
                False,
                public_path=public_path,
                message="GitHub Pages publishing is not configured.",
            )

        settings = self.settings.snapshot()
        workspace = Path(str(settings[WORKSPACE_KEY])).resolve()
        branch = str(settings.get(BRANCH_KEY, DEFAULT_BRANCH))
        letters_root = (workspace / "letters").resolve()
        destination = letters_root / public_path
        try:
            if destination.resolve().parent != letters_root:
                raise ValueError("The publication path escaped the letters directory.")
            if destination.is_symlink():
                raise ValueError("Symbolic-link publications cannot be removed safely.")
            marker_path = destination / PUBLICATION_MARKER
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                not isinstance(marker, dict)
                or marker.get("public_path") != public_path
                or not is_publication_expired(marker.get("expires_at"), now=now)
            ):
                raise ValueError("The hosted publication marker did not verify expiration.")
            if not destination.is_dir():
                raise FileNotFoundError(destination)

            transaction = PathTransaction(
                destination,
                staging_suffix=".cleanup-staging",
                backup_suffix=".cleanup-backup",
            )
            transaction.prepare()
            transaction.commit(replace=False, keep_backup=True)
            relative = destination.relative_to(workspace).as_posix()
            self._run(("git", "add", "--all", "--", relative), cwd=workspace)
            self._run(
                (
                    "git",
                    "commit",
                    "--only",
                    "-m",
                    f"Expire letter publication: {public_path}",
                    "--",
                    relative,
                ),
                cwd=workspace,
            )
            self._run(("git", "push", "origin", branch), cwd=workspace)
            transaction.finalize()
            expected_url = github_pages_url(str(settings[REPOSITORY_KEY]), public_path)
            if settings.get(PUBLISHED_PAGE_URL_KEY, "") == expected_url:
                self.settings.update_fields(
                    **{
                        PUBLISHED_PAGE_URL_KEY: "",
                        PUBLISHED_AT_KEY: "",
                        PUBLISHED_EXPIRES_AT_KEY: "",
                    }
                )
            return PublishResult(True, public_path=public_path, message="Expired publication removed.")
        except Exception as error:
            _LOGGER.exception("Expired publication cleanup failed for %s", public_path)
            try:
                transaction.rollback()
            except (NameError, UnboundLocalError, OSError):
                pass
            return PublishResult(
                False,
                public_path=public_path,
                message="The expired publication could not be removed.",
                technical_details=f"{type(error).__name__}: {error}",
            )

    @staticmethod
    def poll_url(url: str, timeout: float, interval: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(request, timeout=min(10.0, interval + 5.0)) as response:
                    if 200 <= int(response.status) < 400:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(interval)
        return False

    @staticmethod
    def poll_url_content(
        url: str,
        expected: bytes,
        timeout: float,
        interval: float,
    ) -> bool:
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            separator = "&" if "?" in url else "?"
            request_url = (
                f"{url}{separator}"
                f"lettersmith_verify={attempt}"
            )
            try:
                request = urllib.request.Request(
                    request_url,
                    method="GET",
                )
                with urllib.request.urlopen(
                    request,
                    timeout=min(
                        10.0,
                        interval + 5.0,
                    ),
                ) as response:
                    if (
                        200
                        <= int(response.status)
                        < 400
                        and response.read()
                        == expected
                    ):
                        return True
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
            ):
                pass
            time.sleep(interval)
        return False
