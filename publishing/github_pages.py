from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote

from publishing.base import Publisher
from publishing.models import PublishConfiguration, PublishResult
from settings_store import SettingsStore
from transactional_io import PathTransaction, atomic_write_text


REPOSITORY_KEY = "github_pages_repository"
WORKSPACE_KEY = "github_pages_workspace"
BRANCH_KEY = "github_pages_branch"
PUBLIC_WARNING_KEY = "github_pages_public_warning_acknowledged"
DEFAULT_REPOSITORY = "letter-smith-publishing"
DEFAULT_BRANCH = "main"
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


def email_uri(url: str, *, recipient: str = "") -> str:
    subject = quote("A letter for you")
    body = quote(f"I made this for you:\n\n{url}")
    return f"mailto:{quote(recipient)}?subject={subject}&body={body}"


def sms_uri(url: str, *, phone: str = "") -> str:
    return f"sms:{quote(phone)}?body={quote(url)}"


def sms_handler_available() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "sms"):
                return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return True
    if shutil.which("xdg-mime") is None:
        return False
    result = subprocess.run(
        ("xdg-mime", "query", "default", "x-scheme-handler/sms"),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


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
            index = staging / "index.html"
            if not index.is_file():
                raise RuntimeError("The staged letter has no index.html.")
            transaction.commit(keep_backup=True)
            relative = destination.relative_to(workspace).as_posix()
            self._run(("git", "add", "--", relative), cwd=workspace)
            self._run(
                (
                    "git",
                    "commit",
                    "-m",
                    f"Publish letter: {metadata.get('recipient_name', '')} — "
                    f"{metadata.get('recipient_title', '')}",
                ),
                cwd=workspace,
            )
            self._run(("git", "push", "origin", branch), cwd=workspace)
            pushed = True
            url = github_pages_url(repository, public_path)
            if not self.poller(url, 120.0, 2.0):
                raise TimeoutError("The published URL did not become available in time.")
            try:
                transaction.finalize()
            except OSError:
                _LOGGER.exception(
                    "Published letter cleanup failed for %s",
                    destination,
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
