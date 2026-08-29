"""Shared publication-state validation and legacy-expiration helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlsplit

from settings_store import (
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    normalize_published_page_url,
)


PUBLICATION_TTL_DAYS = 30
PUBLICATION_CLEANUP_POLICY = "manual_or_scheduled_cleanup"
GITHUB_PAGES_PROVIDER_ID = "github_pages"
LEGACY_CLOUDFLARE_R2_PROVIDER_ID = "cloudflare_r2"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_publication_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def is_publication_expiration_malformed(value: object) -> bool:
    return bool(str(value).strip()) and parse_publication_timestamp(value) is None


def publication_expiry_label(value: object) -> str:
    expiry = parse_publication_timestamp(value)
    if expiry is None:
        return ""
    return f"Expires {expiry.astimezone().strftime('%b %d, %Y at %I:%M %p %Z')}"


def publication_window(
    published_at: datetime | None = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    start = _as_utc(published_at or now or datetime.now(timezone.utc))
    return start, start + timedelta(days=PUBLICATION_TTL_DAYS)


def is_publication_expired(
    expires_at: object,
    *,
    now: datetime | None = None,
) -> bool:
    expiry = parse_publication_timestamp(expires_at)
    if expiry is None:
        return False
    current = _as_utc(now or datetime.now(timezone.utc))
    return current >= expiry


def clear_publication_state() -> dict[str, object]:
    return {
        PUBLISHED_PAGE_URL_KEY: "",
        PUBLISHED_PUBLIC_PATH_KEY: "",
        PUBLISHED_AT_KEY: "",
        PUBLISHED_EXPIRES_AT_KEY: "",
        PUBLICATION_PROVIDER_KEY: "",
        PUBLICATION_VERIFIED_KEY: False,
        PUBLISHED_SOURCE_FINGERPRINT_KEY: "",
        PUBLISHED_GITHUB_OWNER_KEY: "",
        PUBLISHED_GITHUB_REPOSITORY_KEY: "",
    }


def publication_status(
    state: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> str:
    values = {
        PUBLISHED_PAGE_URL_KEY: str(state.get(PUBLISHED_PAGE_URL_KEY, "")).strip(),
        PUBLISHED_PUBLIC_PATH_KEY: str(
            state.get(PUBLISHED_PUBLIC_PATH_KEY, "")
        ).strip(),
        PUBLISHED_AT_KEY: str(state.get(PUBLISHED_AT_KEY, "")).strip(),
        PUBLISHED_EXPIRES_AT_KEY: str(
            state.get(PUBLISHED_EXPIRES_AT_KEY, "")
        ).strip(),
        PUBLICATION_PROVIDER_KEY: str(
            state.get(PUBLICATION_PROVIDER_KEY, "")
        ).strip(),
        PUBLISHED_SOURCE_FINGERPRINT_KEY: str(
            state.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
        ).strip(),
        PUBLISHED_GITHUB_OWNER_KEY: str(
            state.get(PUBLISHED_GITHUB_OWNER_KEY, "")
        ).strip(),
        PUBLISHED_GITHUB_REPOSITORY_KEY: str(
            state.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
        ).strip(),
    }
    verified = state.get(PUBLICATION_VERIFIED_KEY) is True
    if not any(values.values()) and not verified:
        return "local"
    publisher_metadata = (
        values[PUBLISHED_PUBLIC_PATH_KEY],
        values[PUBLISHED_AT_KEY],
        values[PUBLISHED_EXPIRES_AT_KEY],
        values[PUBLICATION_PROVIDER_KEY],
        values[PUBLISHED_SOURCE_FINGERPRINT_KEY],
        values[PUBLISHED_GITHUB_OWNER_KEY],
        values[PUBLISHED_GITHUB_REPOSITORY_KEY],
    )
    if not verified and not any(publisher_metadata):
        return (
            "published"
            if normalize_published_page_url(
                values[PUBLISHED_PAGE_URL_KEY]
            )
            else "invalid"
        )
    provider = values[PUBLICATION_PROVIDER_KEY]
    if not verified or provider not in {
        GITHUB_PAGES_PROVIDER_ID,
        LEGACY_CLOUDFLARE_R2_PROVIDER_ID,
    }:
        return "invalid"
    public_path = values[PUBLISHED_PUBLIC_PATH_KEY]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?", public_path):
        return "invalid"
    parsed_url = urlsplit(values[PUBLISHED_PAGE_URL_KEY])
    if (
        parsed_url.scheme.lower() != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        return "invalid"
    published_at = parse_publication_timestamp(values[PUBLISHED_AT_KEY])
    if published_at is None:
        return "invalid"
    if provider == LEGACY_CLOUDFLARE_R2_PROVIDER_ID:
        expires_at = parse_publication_timestamp(values[PUBLISHED_EXPIRES_AT_KEY])
        if expires_at is None or expires_at <= published_at:
            return "invalid"
        if _as_utc(now or datetime.now(timezone.utc)) >= expires_at:
            return "expired"
    return "published"


__all__ = [
    "PUBLICATION_CLEANUP_POLICY",
    "PUBLICATION_TTL_DAYS",
    "GITHUB_PAGES_PROVIDER_ID",
    "LEGACY_CLOUDFLARE_R2_PROVIDER_ID",
    "PUBLISHED_AT_KEY",
    "PUBLISHED_EXPIRES_AT_KEY",
    "clear_publication_state",
    "is_publication_expired",
    "is_publication_expiration_malformed",
    "parse_publication_timestamp",
    "publication_expiry_label",
    "publication_status",
    "publication_window",
]
