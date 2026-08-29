from __future__ import annotations

import re
from pathlib import Path


PROTECTED_PROJECT_KIND_KEY = "protected_project_kind"
PROTECTED_PROJECT_MASTER_PATH_KEY = "protected_project_master_path"
STOCK_PROJECT_KIND = "stock"
EXAMPLE_PROJECT_KIND = "example"

_STOCK_WORD_PATTERN = re.compile(r"\bstock\b")
_STOCK_NUMBER_PATTERN = re.compile(r"\b[123]\b")


def normalized_protected_kind(value: object) -> str:
    kind = str(value or "").strip().casefold()
    return kind if kind in {STOCK_PROJECT_KIND, EXAMPLE_PROJECT_KIND} else ""


def protected_project_kind(settings: dict) -> str:
    return normalized_protected_kind(
        settings.get(PROTECTED_PROJECT_KIND_KEY, "")
    )


def is_protected_project(settings: dict) -> bool:
    return bool(protected_project_kind(settings))


def _contains_numbered_stock_identity(value: str) -> bool:
    return bool(
        _STOCK_WORD_PATTERN.search(value)
        and _STOCK_NUMBER_PATTERN.search(value)
    )


def reserved_identity_field_reason(field_name: str, value: object) -> str:
    field = str(field_name or "").strip().casefold()
    normalized = str(value or "").strip().casefold()
    if field == "title":
        if normalized == "none":
            return "NONE is not a valid letter title."
        if normalized.startswith("stock"):
            return "Titles beginning with Stock are reserved for Stock Letters."
        if _contains_numbered_stock_identity(normalized):
            return "Numbered Stock titles are reserved for Stock Letters."
        if normalized == "example letter":
            return "Example Letter is reserved for the bundled example."
        return ""
    if field == "recipient":
        if normalized == "none":
            return "NONE is not a valid recipient for a saved letter."
        if normalized == "stock":
            return "Stock is reserved for Stock Letter recipients."
        if _contains_numbered_stock_identity(normalized):
            return "Numbered Stock recipients are reserved for Stock Letters."
        if normalized == "a friend":
            return "A Friend is reserved for the bundled Example Letter."
        return ""
    raise ValueError(f"Unsupported identity field: {field_name!r}")


def reserved_save_reason(title: object, recipient: object) -> str:
    title_reason = reserved_identity_field_reason("title", title)
    if title_reason:
        return title_reason
    recipient_reason = reserved_identity_field_reason("recipient", recipient)
    if recipient_reason:
        return recipient_reason
    return ""


def is_reserved_save_identity(title: object, recipient: object) -> bool:
    return bool(reserved_save_reason(title, recipient))


def demo_preview_directory(project_root: str | Path, project_id: str) -> Path:
    from project_paths import application_paths

    return (
        application_paths(project_root).temporary_root
        / "Demo Preview"
        / project_id
    ).resolve()


__all__ = [
    "EXAMPLE_PROJECT_KIND",
    "PROTECTED_PROJECT_KIND_KEY",
    "PROTECTED_PROJECT_MASTER_PATH_KEY",
    "STOCK_PROJECT_KIND",
    "demo_preview_directory",
    "is_protected_project",
    "is_reserved_save_identity",
    "normalized_protected_kind",
    "protected_project_kind",
    "reserved_identity_field_reason",
    "reserved_save_reason",
]
