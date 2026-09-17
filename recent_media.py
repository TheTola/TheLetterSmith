"""Small, project- and slot-specific history for media selectors."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from threading import RLock
from uuid import UUID

from project_paths import application_paths
from transactional_io import atomic_copy_file, atomic_write_json


_LOCK = RLock()
_LIMIT = 3


def _history_path(project_root: str | Path) -> Path:
    return application_paths(project_root).settings_root / "recent_media.json"


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _unique_entries(values: list) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        label = item.get("label")
        if not isinstance(value, str) or not value or not isinstance(label, str):
            continue
        if value in seen:
            continue
        seen.add(value)
        entries.append({"value": value, "label": label})
        if len(entries) == _LIMIT:
            break
    return entries


def recent_media(
    project_root: str | Path,
    project_id: str,
    slot: str,
) -> tuple[tuple[str, str], ...]:
    """Return newest-first distinct entries for one project slot."""
    if not project_id or not slot:
        return ()
    with _LOCK:
        entries = _load(_history_path(project_root)).get(project_id, {})
        values = entries.get(slot, []) if isinstance(entries, dict) else []
    if not isinstance(values, list):
        return ()
    return tuple(
        (item["value"], item["label"])
        for item in _unique_entries(values)
    )


def remember_media(
    project_root: str | Path,
    project_id: str,
    slot: str,
    value: str,
    label: str,
) -> None:
    """Keep at most three distinct selections without changing other slots."""
    if not all((project_id, slot, value)):
        return
    path = _history_path(project_root)
    with _LOCK:
        payload = _load(path)
        project = payload.get(project_id)
        if not isinstance(project, dict):
            project = {}
        old = project.get(slot, [])
        if not isinstance(old, list):
            old = []
        project[slot] = _unique_entries(
            [{"value": value, "label": label}, *old]
        )
        payload[project_id] = project
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)


def remember_image_copy(
    project_root: str | Path,
    project_id: str,
    slot: str,
    source: str | Path,
    label: str,
) -> None:
    """Retain one imported image independently of the working slot and original."""
    if slot not in {"cover", "letter", "wall", "back"}:
        raise ValueError("Unknown image slot.")
    identity = str(UUID(project_id))
    image = Path(source).resolve(strict=True)
    if not image.is_file() or image.suffix.lower() not in {".png", ".gif"}:
        raise ValueError("The imported image is unavailable.")
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    directory = _history_path(project_root).parent / "recent_images" / identity / slot
    destination = directory / f"{digest}{image.suffix.lower()}"
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            atomic_copy_file(image, destination)
        remember_media(
            project_root,
            identity,
            f"image:{slot}",
            str(destination),
            label,
        )
        retained = {
            value for value, _label in recent_media(
                project_root, identity, f"image:{slot}"
            )
        }
        for cached in directory.iterdir():
            if cached.is_file() and str(cached) not in retained:
                cached.unlink()
