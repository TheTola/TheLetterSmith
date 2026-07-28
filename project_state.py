#!/usr/bin/env python3
"""Stable project identity and atomic autosave for Letter Smith.

The editable title and recipient are project metadata. They never determine the
project's identity or storage location. ``project_id`` is generated once and
persists for the lifetime of the project.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from config import SETTINGS_FILE

PROJECT_ID_KEY = "project_id"
PROJECT_SCHEMA_KEY = "project_schema_version"
PROJECT_SCHEMA_VERSION = 1

_lock = RLock()


def _settings_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / SETTINGS_FILE


def _valid_project_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _new_project_id() -> str:
    return str(uuid.uuid4())


def atomic_write_settings(project_root: str | Path, data: Mapping[str, Any]) -> None:
    """Write settings without exposing readers to a partially written file."""
    path = _settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_project_settings(project_root: str | Path, *, ensure_identity: bool = True) -> dict[str, Any]:
    """Load settings and, by default, migrate the active project to a stable ID."""
    path = _settings_path(project_root)
    with _lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        changed = False
        if ensure_identity:
            project_id = _valid_project_id(data.get(PROJECT_ID_KEY))
            if not project_id:
                project_id = _new_project_id()
                changed = True
            if data.get(PROJECT_ID_KEY) != project_id:
                data[PROJECT_ID_KEY] = project_id
                changed = True
            if data.get(PROJECT_SCHEMA_KEY) != PROJECT_SCHEMA_VERSION:
                data[PROJECT_SCHEMA_KEY] = PROJECT_SCHEMA_VERSION
                changed = True

        if changed:
            atomic_write_settings(project_root, data)
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
        data = load_project_settings(project_root, ensure_identity=True)
        stable_id = data[PROJECT_ID_KEY]
        for key, value in updates.items():
            if preserve_project_id and key == PROJECT_ID_KEY:
                continue
            data[key] = value
        data[PROJECT_ID_KEY] = stable_id
        data[PROJECT_SCHEMA_KEY] = PROJECT_SCHEMA_VERSION
        atomic_write_settings(project_root, data)
        return data


def adopt_loaded_project(project_root: str | Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Load a saved project's identity and editable metadata into the workspace."""
    loaded_id = _valid_project_id(metadata.get(PROJECT_ID_KEY)) or _new_project_id()
    with _lock:
        current = load_project_settings(project_root, ensure_identity=False)
        current.update({
            PROJECT_ID_KEY: loaded_id,
            PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
            "recipient_name": str(metadata.get("recipient_name", "")).strip(),
            "recipient_title": str(metadata.get("recipient_title", "")).strip(),
        })
        if "published_page_url" in metadata:
            current["published_page_url"] = str(metadata.get("published_page_url", "")).strip()
        atomic_write_settings(project_root, current)
        return current
