from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from message_html import message_html_has_content, read_text_normalized
from settings_store import SettingsStore


IMAGE_FILES = {
    "cover": "cover.png",
    "letter": "letter.png",
    "wall": "wall.png",
    "back": "back.png",
}
PLAYLIST_FILE = Path("gallery/user/sounds/playlist.json")
CURRENT_MUSIC_FILE = Path("gallery/user/sounds/music.mp3")
MESSAGE_FILE = Path("gallery/user/message/message.html")
PLAY_OUTPUT_DIR = Path("output/Play")
RECOVERY_OUTPUT_DIR = Path("output/Recovery")
PLAY_METADATA_FILE = "lettersmith-metadata.json"


@dataclass(frozen=True)
class FileState:
    path: Path
    exists: bool
    readable: bool
    size_bytes: int


@dataclass(frozen=True)
class MessageState:
    path: Path
    exists: bool
    readable: bool
    has_content: bool


@dataclass(frozen=True)
class PlaylistState:
    path: Path
    exists: bool
    valid: bool
    tracks: tuple[str, ...]
    repeat: bool
    crossfade_ms: int
    error: str = ""


@dataclass(frozen=True)
class ProjectState:
    root: Path
    recipient: str
    title: str
    images: dict[str, FileState]
    message: MessageState
    playlist: PlaylistState
    current_music: FileState
    saved_forge_build: Path | None
    published_url: str
    recovery_snapshot: Path | None


def _inspect_file(path: Path) -> FileState:
    if not path.is_file():
        return FileState(path.resolve(), False, False, 0)
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.read(1)
    except OSError:
        return FileState(path.resolve(), True, False, 0)
    return FileState(path.resolve(), True, size > 0, size)


def _inspect_message(path: Path) -> MessageState:
    if not path.is_file():
        return MessageState(path.resolve(), False, False, False)
    try:
        html = read_text_normalized(path)
        has_content = message_html_has_content(html)
    except Exception:
        return MessageState(path.resolve(), True, False, False)
    return MessageState(path.resolve(), True, True, has_content)


def _inspect_playlist(path: Path) -> PlaylistState:
    if not path.is_file():
        return PlaylistState(path.resolve(), False, True, (), True, 1000)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Playlist root must be an object.")
        raw_tracks = data.get("tracks", [])
        if not isinstance(raw_tracks, list):
            raise ValueError("Playlist tracks must be a list.")
        tracks = tuple(
            str(track.get("archive_name", "")).strip()
            for track in raw_tracks
            if isinstance(track, dict) and str(track.get("archive_name", "")).strip()
        )
        crossfade_ms = int(data.get("crossfade_ms", 1000))
        if crossfade_ms < 0:
            raise ValueError("Playlist crossfade must not be negative.")
        return PlaylistState(
            path.resolve(),
            True,
            True,
            tracks,
            bool(data.get("repeat", True)),
            crossfade_ms,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return PlaylistState(path.resolve(), True, False, (), True, 1000, str(exc))


def _safe_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"\s+", " ", value).strip().lower()
    slug = re.sub(r"[^a-z0-9\-_. ]+", "", slug).replace(" ", "-")
    return slug or fallback


def _metadata_matches(path: Path, recipient: str, title: str) -> bool:
    metadata_path = path / PLAY_METADATA_FILE
    if not metadata_path.is_file():
        return False
    try:
        metadata: Any = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(metadata, dict)
        and str(metadata.get("recipient_name", "")).strip() == recipient
        and str(metadata.get("recipient_title", "")).strip() == title
    )


def _saved_build(root: Path, settings: dict[str, Any], recipient: str, title: str) -> Path | None:
    configured = str(
        settings.get("build_location")
        or settings.get("last_play_dir")
        or ""
    ).strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = root / candidate
        if (candidate / "index.html").is_file():
            return candidate.resolve()

    if not recipient or not title:
        return None
    play_root = root / PLAY_OUTPUT_DIR
    expected = play_root / _safe_slug(recipient, "friend") / _safe_slug(title, "letter")
    if (expected / "index.html").is_file():
        return expected.resolve()
    if not play_root.is_dir():
        return None
    for candidate in play_root.glob("*/*"):
        if (candidate / "index.html").is_file() and _metadata_matches(candidate, recipient, title):
            return candidate.resolve()
    return None


def _latest_recovery(root: Path) -> Path | None:
    recovery_root = root / RECOVERY_OUTPUT_DIR
    if not recovery_root.is_dir():
        return None
    candidates = tuple(path for path in recovery_root.iterdir() if path.is_dir())
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: (path.stat().st_mtime, path.name.casefold()),
    ).resolve()


def inspect_project_state(project_root: str | Path) -> ProjectState:
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    recipient = str(settings.get("recipient_name", "")).strip()
    title = str(settings.get("recipient_title", "")).strip()
    pages = root / "gallery/user/pages"
    images = {
        key: _inspect_file(pages / filename)
        for key, filename in IMAGE_FILES.items()
    }
    return ProjectState(
        root=root,
        recipient=recipient,
        title=title,
        images=images,
        message=_inspect_message(root / MESSAGE_FILE),
        playlist=_inspect_playlist(root / PLAYLIST_FILE),
        current_music=_inspect_file(root / CURRENT_MUSIC_FILE),
        saved_forge_build=_saved_build(root, settings, recipient, title),
        published_url=str(settings.get("published_page_url", "")).strip(),
        recovery_snapshot=_latest_recovery(root),
    )


__all__ = [
    "FileState",
    "IMAGE_FILES",
    "MessageState",
    "PlaylistState",
    "ProjectState",
    "inspect_project_state",
]
