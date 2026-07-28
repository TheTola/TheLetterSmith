#!/usr/bin/env python3
"""Stable project identity and atomic autosave for Letter Smith.

The editable title and recipient are project metadata. They never determine the
project's identity or storage location. ``project_id`` is generated once and
persists for the lifetime of the project.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from settings_store import SettingsStore

PROJECT_ID_KEY = "project_id"
PROJECT_SCHEMA_KEY = "project_schema_version"
PROJECT_SCHEMA_VERSION = 1

_lock = RLock()


def _valid_project_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _new_project_id() -> str:
    return str(uuid.uuid4())


def atomic_write_settings(project_root: str | Path, data: Mapping[str, Any]) -> None:
    """Write settings without exposing readers to a partially written file."""
    SettingsStore(project_root).replace_snapshot(data)


def load_project_settings(project_root: str | Path, *, ensure_identity: bool = True) -> dict[str, Any]:
    """Load settings and, by default, migrate the active project to a stable ID."""
    with _lock:
        data = SettingsStore(project_root).snapshot()
        if not ensure_identity:
            return data
        return data


def ensure_project_identity(project_root: str | Path) -> str:
    return str(load_project_settings(project_root, ensure_identity=True)[PROJECT_ID_KEY])


def autosave_project_settings(
    project_root: str | Path,
    updates: Mapping[str, Any],
    *,
    preserve_project_id: bool = True,
) -> dict[str, Any]:
    """Merge an autosave update into the active project atomically.

    Normal edits cannot replace ``project_id``. Creating or duplicating a project
    must use an explicit project-management operation instead.
    """
    with _lock:
        store = SettingsStore(project_root)
        data = store.snapshot()
        stable_id = data[PROJECT_ID_KEY]
        accepted: dict[str, Any] = {}
        for key, value in updates.items():
            if preserve_project_id and key == PROJECT_ID_KEY:
                continue
            accepted[key] = value
        accepted[PROJECT_ID_KEY] = stable_id
        accepted[PROJECT_SCHEMA_KEY] = PROJECT_SCHEMA_VERSION
        return store.update_fields(accepted)


def adopt_loaded_project(project_root: str | Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Load a saved project's identity and editable metadata into the workspace."""
    loaded_id = _valid_project_id(metadata.get(PROJECT_ID_KEY)) or _new_project_id()
    with _lock:
        store = SettingsStore(project_root)
        current = store.snapshot()
        current.update({
            PROJECT_ID_KEY: loaded_id,
            PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
            "recipient_name": str(metadata.get("recipient_name", "")).strip(),
            "recipient_title": str(metadata.get("recipient_title", "")).strip(),
        })
        if "published_page_url" in metadata:
            current["published_page_url"] = str(metadata.get("published_page_url", "")).strip()
        return store.replace_snapshot(current)
