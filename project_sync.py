from __future__ import annotations

"""Project-state fingerprints used by Letter Smith tab synchronization.

The fingerprints are deliberately independent of UI state. They detect files
changed through Letter Smith as well as files replaced directly on disk.
"""

import hashlib
import json
from pathlib import Path
from typing import Iterable

_SAMPLE_SIZE = 64 * 1024
_IMAGE_NAMES = ("cover.png", "letter.png", "wall.png", "back.png")


def _sampled_file_digest(path: Path) -> str:
    """Return a stable, inexpensive digest for one file.

    Small files are hashed completely. Large files use their size plus samples
    from the beginning and end, which is sufficient for stale-preview checks
    without repeatedly hashing an entire music library.
    """
    try:
        stat = path.stat()
    except OSError:
        return "missing"

    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode("ascii", errors="ignore"))
    try:
        with path.open("rb") as handle:
            if stat.st_size <= _SAMPLE_SIZE * 2:
                while chunk := handle.read(_SAMPLE_SIZE):
                    digest.update(chunk)
            else:
                digest.update(handle.read(_SAMPLE_SIZE))
                handle.seek(max(0, stat.st_size - _SAMPLE_SIZE))
                digest.update(handle.read(_SAMPLE_SIZE))
    except OSError:
        return f"unreadable:{stat.st_size}:{stat.st_mtime_ns}"
    return digest.hexdigest()


def _paths_digest(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item).casefold()):
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            relative = str(path.resolve())
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(_sampled_file_digest(path).encode("ascii", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _files_under(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    try:
        return [path for path in directory.rglob("*") if path.is_file()]
    except OSError:
        return []



def file_fingerprint(path: str | Path) -> str:
    """Return the sampled content fingerprint for one path."""
    return _sampled_file_digest(Path(path))

def image_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    pages = root / "gallery" / "user" / "pages"
    return _paths_digest(root, (pages / name for name in _IMAGE_NAMES))


def message_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    message_dir = root / "gallery" / "user" / "message"
    return _paths_digest(root, _files_under(message_dir))


def sound_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    sound_dir = root / "gallery" / "user" / "sounds"

    # Only current project sound files and manifests affect Forge. The reusable
    # archive can be large and does not need to invalidate the current letter.
    candidates: list[Path] = []
    for name in ("music.mp3", "current.json", "playlist.json", "manifest.json"):
        candidate = sound_dir / name
        if candidate.is_file():
            candidates.append(candidate)

    archive = sound_dir / "appssong"
    for name in ("current.json", "playlist.json"):
        candidate = archive / name
        if candidate.is_file():
            candidates.append(candidate)

    return _paths_digest(root, candidates)


def settings_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    path = root / "settings.json"
    if not path.is_file():
        return _paths_digest(root, (path,))

    # Normalize JSON so harmless formatting changes do not force a rebuild.
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _paths_digest(root, (path,))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def project_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    digest = hashlib.sha256()
    for label, value in (
        ("images", image_fingerprint(root)),
        ("message", message_fingerprint(root)),
        ("sound", sound_fingerprint(root)),
        ("settings", settings_fingerprint(root)),
    ):
        digest.update(label.encode("ascii"))
        digest.update(b"=")
        digest.update(value.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "file_fingerprint",
    "image_fingerprint",
    "message_fingerprint",
    "project_fingerprint",
    "settings_fingerprint",
    "sound_fingerprint",
]
