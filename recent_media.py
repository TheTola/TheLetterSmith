"""Shared artwork history and project-specific audio/media selections."""

from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from pathlib import Path
from threading import RLock
from uuid import UUID

from PIL import Image, ImageOps

from project_paths import application_paths
from transactional_io import atomic_copy_file, atomic_write_json


_LOCK = RLock()
_LIMIT = 3
_TYPE_IMAGE_LIMIT = 10
_TYPE_HISTORY_ID = "a406f198-533b-4d72-8777-a79b7dd8b061"


def recent_type_images(project_root, page_type):
    """The last ten available images for this canonical type, across projects."""
    _validate_page_type(page_type)
    return tuple((value, label) for value, label in
                 recent_media(project_root, _TYPE_HISTORY_ID, f"image:{page_type}")
                 if Path(value).is_file())


def remember_type_image(project_root, page_type, source, label):
    _validate_page_type(page_type)
    remember_image_copy(project_root, _TYPE_HISTORY_ID, page_type, source, label)


def _validate_page_type(page_type):
    from project_model import PAGE_TYPES
    if page_type not in PAGE_TYPES:
        raise ValueError("Choose a supported artwork type.")


def clear_type_image_history(project_root) -> None:
    """Forget shared image selections; retain every source and cached file."""
    path = _history_path(project_root)
    with _LOCK:
        payload = _load(path)
        if _TYPE_HISTORY_ID in payload:
            del payload[_TYPE_HISTORY_ID]
            atomic_write_json(path, payload)


def _entry_limit(project_id, slot):
    return _TYPE_IMAGE_LIMIT if project_id == _TYPE_HISTORY_ID and slot.startswith("image:") else _LIMIT


def _history_path(project_root: str | Path) -> Path:
    return application_paths(project_root).settings_root / "recent_media.json"


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=128)
def _image_content_key(path: str, size: int, modified: int, changed: int) -> str:
    """Compare decoded artwork, retaining animation frames, timing and loops.

    File stats invalidate the bounded cache after same-path replacement; PNG/GIF
    comments and compression do not make otherwise identical artwork distinct.
    """
    digest = hashlib.sha256()
    with Image.open(path) as image:
        frames = getattr(image, "n_frames", 1)
        digest.update(str(frames).encode("ascii") + b"\0")
        if frames > 1:
            digest.update(str(image.info.get("loop")).encode("ascii") + b"\0")
        for index in range(frames):
            image.seek(index)
            pixels = ImageOps.exif_transpose(image).convert("RGBA")
            digest.update(pixels.width.to_bytes(8, "big"))
            digest.update(pixels.height.to_bytes(8, "big"))
            digest.update(pixels.tobytes())
            if frames > 1:
                digest.update(str(image.info.get("duration", 0)).encode("ascii") + b"\0")
    return digest.hexdigest()


def _unique_entries(values: list, limit: int = _LIMIT, *, image_content: bool = False) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        label = item.get("label")
        if not isinstance(value, str) or not value or not isinstance(label, str):
            continue
        key = value
        if image_content:
            try:
                path = Path(value).resolve(strict=True)
                stat = path.stat()
                key = _image_content_key(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                continue
        if key in seen:
            continue
        seen.add(key)
        entries.append({"value": value, "label": label})
        if len(entries) == limit:
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
        for item in _unique_entries(values, _entry_limit(project_id, slot),
                                    image_content=project_id == _TYPE_HISTORY_ID and slot.startswith("image:"))
    )


def remember_media(
    project_root: str | Path,
    project_id: str,
    slot: str,
    value: str,
    label: str,
) -> None:
    """Keep the slot's bounded, distinct selections without changing other slots."""
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
            [{"value": value, "label": label}, *old], _entry_limit(project_id, slot),
            image_content=project_id == _TYPE_HISTORY_ID and slot.startswith("image:")
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
    if not isinstance(slot, str) or not slot:
        raise ValueError("An image page ID is required.")
    # Keep historical folders readable; canonical IDs may contain characters
    # that cannot safely be used as a filesystem component.
    folder = slot if slot in {"cover", "illustration", "letter", "end_page", "wall", "back"} else hashlib.sha256(slot.encode("utf-8")).hexdigest()
    identity = str(UUID(project_id))
    image = Path(source).resolve(strict=True)
    if not image.is_file() or image.suffix.lower() not in {".png", ".gif"}:
        raise ValueError("The imported image is unavailable.")
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    directory = _history_path(project_root).parent / "recent_images" / identity / folder
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
