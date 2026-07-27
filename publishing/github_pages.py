from __future__ import annotations

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
        result = self._run(("gh", "auth", "status"), check=False)
        return result.returncode == 0

    def is_configured(self) -> bool:
        settings = self.settings.snapshot()
        repository = str(settings.get(REPOSITORY_KEY, "")).strip()
        workspace = Path(str(settings.get(WORKSPACE_KEY, ""))).expanduser()
        return (
            self.git_available()
            and self.gh_available()
            and self.authenticated()
            and "/" in repository
            and workspace.is_dir()
            and (workspace / ".git").exists()
        )

    def configure(self, parent=None) -> PublishConfiguration:
        if not self.git_available():
            return PublishConfiguration(False, message="Git is not installed.")
        if not self.gh_available():
            return PublishConfiguration(False, message="GitHub CLI is not installed.")
        if not self.authenticated():
            login = self._run(("gh", "auth", "login", "--web"), check=False)
            if login.returncode != 0 or not self.authenticated():
                return PublishConfiguration(False, message="GitHub authentication was not completed.")

        settings = self.settings.snapshot()
        repository = str(settings.get(REPOSITORY_KEY, "")).strip()
        if not repository:
            owner_result = self._run(("gh", "api", "user", "--jq", ".login"))
            owner = owner_result.stdout.strip()
            repository = f"{owner}/{DEFAULT_REPOSITORY}"

        view = self._run(("gh", "repo", "view", repository), check=False)
        if view.returncode != 0:
            create = self._run(
                ("gh", "repo", "create", repository, "--public", "--confirm"),
                check=False,
            )
            if create.returncode != 0:
                return PublishConfiguration(
                    False,
                    repository=repository,
                    message=create.stderr.strip() or "Could not create the publishing repository.",
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
                    message=clone.stderr.strip() or "Could not prepare the publishing workspace.",
                )

        self._install_pages_files(workspace)
        self._run(("git", "add", ".github/workflows/pages.yml", "robots.txt"), cwd=workspace)
        status = self._run(("git", "status", "--porcelain"), cwd=workspace)
        if status.stdout.strip():
            self._run(("git", "commit", "-m", "Configure Letter Smith publishing"), cwd=workspace)
            self._run(("git", "push", "origin", DEFAULT_BRANCH), cwd=workspace)

        self._run(
            ("gh", "api", "--method", "POST", f"repos/{repository}/pages", "-f", "build_type=workflow"),
            check=False,
        )
        verify = self._run(("gh", "api", f"repos/{repository}/pages"), check=False)
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
        return PublishConfiguration(True, repository, workspace, "GitHub Pages is ready.")

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
        start_head = self._run(("git", "rev-parse", "HEAD"), cwd=workspace).stdout.strip()

        try:
            staging = transaction.prepare()
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
            url = github_pages_url(repository, public_path)
            if not self.poller(url, 120.0, 2.0):
                raise TimeoutError("The published URL did not become available in time.")
            transaction.finalize()
            return PublishResult(True, url, public_path, "Published.")
        except Exception as error:
            try:
                self._run(("git", "reset", "--mixed", start_head), cwd=workspace, check=False)
                transaction.rollback()
            except Exception:
                transaction.abort()
            details = f"{type(error).__name__}: {error}"
            return PublishResult(
                False,
                public_path=public_path,
                message="Publishing failed. The previous live version was preserved.",
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
