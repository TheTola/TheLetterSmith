from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from Template import TEMPLATE_HTML, TEMPLATE_JS
from publishing.github_pages import (
    GitHubPagesPublisher,
    email_uri,
    github_pages_url,
    safe_public_path,
    sms_uri,
)
from settings_store import SettingsStore


def _completed(command, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class RecordingRunner:
    def __init__(self, head: str = "abc123") -> None:
        self.commands: list[tuple[str, ...]] = []
        self.head = head

    def __call__(self, command, **_kwargs):
        normalized = tuple(command)
        self.commands.append(normalized)
        if normalized[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(command, stdout=self.head + "\n")
        return _completed(command)


def _configured_publisher(
    root: Path,
    *,
    poll_result: bool = True,
) -> tuple[GitHubPagesPublisher, RecordingRunner, Path]:
    workspace = root / "publish-workspace"
    (workspace / ".git").mkdir(parents=True)
    SettingsStore(root).update_fields(
        github_pages_repository="writer/letters",
        github_pages_workspace=str(workspace),
        github_pages_branch="main",
    )
    runner = RecordingRunner()
    publisher = GitHubPagesPublisher(
        root,
        runner=runner,
        poller=lambda _url, _timeout, _interval: poll_result,
    )
    publisher.git_available = lambda: True
    publisher.gh_available = lambda: True
    publisher.authenticated = lambda: True
    return publisher, runner, workspace


def test_configuration_detection_and_missing_cli(tmp_path: Path) -> None:
    publisher = GitHubPagesPublisher(tmp_path)
    with mock.patch.object(publisher, "git_available", return_value=True), mock.patch.object(
        publisher, "gh_available", return_value=False
    ):
        assert not publisher.is_configured()
        result = publisher.configure()
    assert not result.configured
    assert "GitHub CLI" in result.message


def test_authentication_failure_is_reported(tmp_path: Path) -> None:
    runner = RecordingRunner()
    publisher = GitHubPagesPublisher(tmp_path, runner=runner)
    publisher.git_available = lambda: True
    publisher.gh_available = lambda: True
    publisher.authenticated = lambda: False
    result = publisher.configure()
    assert not result.configured
    assert "authentication" in result.message.lower()
    assert ("gh", "auth", "login", "--web") in runner.commands


def test_safe_path_urls_and_sharing_uris() -> None:
    public_path = safe_public_path("Ada Lovelace", "A Birthday!", token="A71F3C")
    assert public_path == "ada-lovelace-a-birthday-a71f3c"
    assert github_pages_url("writer/letters", public_path).endswith(
        "/letters/ada-lovelace-a-birthday-a71f3c/"
    )
    assert email_uri("https://example.test/letter", recipient="a@example.test").startswith(
        "mailto:a%40example.test?"
    )
    assert "body=https%3A//example.test/letter" in sms_uri(
        "https://example.test/letter", phone="+15551212"
    )


def test_publish_stages_commits_pushes_and_polls(tmp_path: Path) -> None:
    publisher, runner, workspace = _configured_publisher(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<html></html>", encoding="utf-8")
    (build / "styles.css").write_text("body{}", encoding="utf-8")
    metadata = {
        "recipient_name": "Ada",
        "recipient_title": "Birthday",
        "public_path": "ada-birthday-a71f3c",
    }
    result = publisher.publish(build, metadata)

    assert result.success
    assert result.url == "https://writer.github.io/letters/letters/ada-birthday-a71f3c/"
    assert (workspace / "letters/ada-birthday-a71f3c/index.html").is_file()
    assert ("git", "add", "--", "letters/ada-birthday-a71f3c") in runner.commands
    assert ("git", "push", "origin", "main") in runner.commands
    assert not (workspace / "index.html").exists()


def test_failed_publish_restores_previous_workspace_letter(tmp_path: Path) -> None:
    publisher, runner, workspace = _configured_publisher(tmp_path, poll_result=False)
    destination = workspace / "letters/ada-birthday-a71f3c"
    destination.mkdir(parents=True)
    (destination / "index.html").write_text("previous", encoding="utf-8")
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("replacement", encoding="utf-8")

    result = publisher.publish(
        build,
        {
            "recipient_name": "Ada",
            "recipient_title": "Birthday",
            "public_path": "ada-birthday-a71f3c",
        },
    )

    assert not result.success
    assert (destination / "index.html").read_text(encoding="utf-8") == "previous"
    assert ("git", "reset", "--mixed", "abc123") in runner.commands


def test_fullscreen_and_noindex_viewer_markup() -> None:
    assert 'name="robots" content="noindex, nofollow, noarchive"' in TEMPLATE_HTML
    assert 'id="fullscreen-button"' in TEMPLATE_HTML
    assert "document.documentElement.requestFullscreen()" in TEMPLATE_JS
    assert "document.exitFullscreen()" in TEMPLATE_JS
    assert "fullscreenBtn.setAttribute('aria-label'" in TEMPLATE_JS
