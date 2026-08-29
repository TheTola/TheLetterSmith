from __future__ import annotations

from datetime import date, datetime, timezone


PROJECT_CREATED_AT_KEY = "project_created_at"
PROJECT_PUBLISHED_AT_KEY = "project_published_at"
LEGACY_PROJECT_TIMESTAMP = "2025-01-01T00:00:00+00:00"
LEGACY_PROJECT_DATE = date(2025, 1, 1)


def current_project_timestamp() -> str:
    """Return a timezone-aware UTC timestamp with second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_project_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def valid_project_timestamp(value: object) -> str:
    text = str(value or "").strip()
    return text if parse_project_timestamp(text) is not None else ""


def project_timestamp_date(
    value: object,
    *,
    legacy_default: bool = True,
) -> date | None:
    parsed = parse_project_timestamp(value)
    if parsed is not None:
        return parsed.date()
    return LEGACY_PROJECT_DATE if legacy_default else None


__all__ = [
    "LEGACY_PROJECT_DATE",
    "LEGACY_PROJECT_TIMESTAMP",
    "PROJECT_CREATED_AT_KEY",
    "PROJECT_PUBLISHED_AT_KEY",
    "current_project_timestamp",
    "parse_project_timestamp",
    "project_timestamp_date",
    "valid_project_timestamp",
]
