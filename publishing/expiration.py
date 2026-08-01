"""Shared publication-expiration policy for local and hosted metadata.

GitHub Pages has no per-page server-side TTL. The marker and local settings
record the deadline; removal still requires a manual or scheduled cleanup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


PUBLICATION_TTL_DAYS = 30
PUBLISHED_AT_KEY = "published_at"
PUBLISHED_EXPIRES_AT_KEY = "published_expires_at"
PUBLICATION_CLEANUP_POLICY = "manual_or_scheduled_cleanup"


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


__all__ = [
    "PUBLICATION_CLEANUP_POLICY",
    "PUBLICATION_TTL_DAYS",
    "PUBLISHED_AT_KEY",
    "PUBLISHED_EXPIRES_AT_KEY",
    "is_publication_expired",
    "parse_publication_timestamp",
    "publication_window",
]
