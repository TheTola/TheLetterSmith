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
GITHUB_REPOSITORY_NAME = "LetterSmith-Published"

_CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{10,128}")
_APP_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,99}")


@dataclass(frozen=True)
class GitHubApplicationConfiguration:
    client_id: str
    app_slug: str
    repository_name: str = GITHUB_REPOSITORY_NAME

    @property
    def configured(self) -> bool:
        return bool(
            _CLIENT_ID_PATTERN.fullmatch(self.client_id)
            and _APP_SLUG_PATTERN.fullmatch(self.app_slug)
        )

    @property
    def installation_url(self) -> str:
        if not self.configured:
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
    "GITHUB_DEVICE_CODE_URL",
    "GITHUB_REPOSITORY_NAME",
    "GITHUB_TOKEN_URL",
    "GitHubApplicationConfiguration",
    "github_application_configuration",
]
