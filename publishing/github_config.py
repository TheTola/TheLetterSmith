from __future__ import annotations

import os
import re
from dataclasses import dataclass


# Public GitHub App identifiers belong here. They are not secrets and may be
# embedded in Letter Smith releases after the app is registered.
GITHUB_APP_CLIENT_ID = "Iv23lifdQJYtcYZ2hOGs"
GITHUB_APP_SLUG = "letter-smith-publisher"

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# Retained for recognizing publications created by older Letter Smith builds.
GITHUB_REPOSITORY_NAME = "LetterSmith-Published"
GITHUB_CREDENTIAL_MODEL = "github_app_user"
GITHUB_OAUTH_REQUIRED_SCOPES = ("repo",)

_CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{10,128}")
_APP_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,99}")
_GITHUB_LOGIN_PATTERN = re.compile(r"(?!-)[A-Za-z0-9-]{1,39}(?<!-)")


def github_pages_repository_name(login: object) -> str:
    username = str(login or "").strip()
    if not _GITHUB_LOGIN_PATTERN.fullmatch(username):
        raise ValueError("GitHub returned an invalid account name.")
    return f"{username}.github.io"


@dataclass(frozen=True)
class GitHubApplicationConfiguration:
    client_id: str
    app_slug: str
    repository_name: str = GITHUB_REPOSITORY_NAME
    credential_model: str = GITHUB_CREDENTIAL_MODEL

    @property
    def configured(self) -> bool:
        if not _CLIENT_ID_PATTERN.fullmatch(self.client_id):
            return False
        if self.credential_model == "oauth_app":
            return True
        return bool(
            self.credential_model == "github_app_user"
            and _APP_SLUG_PATTERN.fullmatch(self.app_slug)
        )

    @property
    def installation_url(self) -> str:
        if not self.configured or self.credential_model != "github_app_user":
            return ""
        return f"https://github.com/apps/{self.app_slug}/installations/new"

    def require_configured(self) -> None:
        if not self.configured:
            raise RuntimeError(
                "GitHub publishing is not configured in this Letter Smith build."
            )


def github_application_configuration(
    environ: dict[str, str] | None = None,
) -> GitHubApplicationConfiguration:
    environment = os.environ if environ is None else environ
    return GitHubApplicationConfiguration(
        client_id=str(
            environment.get(
                "LETTERSMITH_GITHUB_CLIENT_ID",
                GITHUB_APP_CLIENT_ID,
            )
        ).strip(),
        app_slug=str(
            environment.get(
                "LETTERSMITH_GITHUB_APP_SLUG",
                GITHUB_APP_SLUG,
            )
        ).strip().casefold(),
    )


__all__ = [
    "GITHUB_API_URL",
    "GITHUB_API_VERSION",
    "GITHUB_APP_CLIENT_ID",
    "GITHUB_APP_SLUG",
    "GITHUB_CREDENTIAL_MODEL",
    "GITHUB_DEVICE_CODE_URL",
    "GITHUB_REPOSITORY_NAME",
    "GITHUB_OAUTH_REQUIRED_SCOPES",
    "GITHUB_TOKEN_URL",
    "GitHubApplicationConfiguration",
    "github_application_configuration",
    "github_pages_repository_name",
]
