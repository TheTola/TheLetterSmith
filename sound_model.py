from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from config import MUSIC_FILE, USER_SOUNDS_DIR

SOUND_MODEL_VERSION = 2
ARCHIVE_DIR_NAME = "appssong"
ORIGINALS_DIR_NAME = "originals"
PROCESSED_DIR_NAME = "processed"
ANALYSIS_DIR_NAME = "analysis"
LIBRARY_FILE_NAME = "library.json"
PROJECT_SOUND_FILE_NAME = "project_sound.json"
CURRENT_MANIFEST_NAME = "current.json"
BUILD_SOUND_MANIFEST_NAME = "lettersmith-sound.json"
ATOMIC_REPLACE_TIMEOUT_SECONDS = 2.0


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Retry brief Windows sharing violations during atomic replacement."""
    deadline = time.monotonic() + ATOMIC_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def user_sounds_dir(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / USER_SOUNDS_DIR


def archive_root(project_root: str | Path) -> Path:
    return user_sounds_dir(project_root) / ARCHIVE_DIR_NAME


def originals_dir(project_root: str | Path) -> Path:
    return archive_root(project_root) / ORIGINALS_DIR_NAME


def processed_dir(project_root: str | Path) -> Path:
    return archive_root(project_root) / PROCESSED_DIR_NAME


def analysis_dir(project_root: str | Path) -> Path:
    return archive_root(project_root) / ANALYSIS_DIR_NAME


def library_path(project_root: str | Path) -> Path:
    return archive_root(project_root) / LIBRARY_FILE_NAME


def project_sound_path(project_root: str | Path) -> Path:
    return archive_root(project_root) / PROJECT_SOUND_FILE_NAME


def current_manifest_path(project_root: str | Path) -> Path:
    return archive_root(project_root) / CURRENT_MANIFEST_NAME


def current_music_path(project_root: str | Path) -> Path:
    return user_sounds_dir(project_root) / MUSIC_FILE


def ensure_sound_dirs(project_root: str | Path) -> None:
    user_sounds_dir(project_root).mkdir(parents=True, exist_ok=True)
    originals_dir(project_root).mkdir(parents=True, exist_ok=True)
    processed_dir(project_root).mkdir(parents=True, exist_ok=True)
    analysis_dir(project_root).mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: str | Path, payload: dict) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _replace_with_retry(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: str | Path, default: dict) -> dict:
    source = Path(path)
    if not source.is_file():
        return dict(default)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(name: str) -> str:
    candidate = Path(str(name)).name.strip().rstrip(". ")
    if not candidate or candidate in {".", ".."}:
        raise ValueError("The audio filename is invalid.")
    if any(ch in candidate for ch in '<>:"/\\|?*'):
        raise ValueError("The audio filename contains characters Windows does not allow.")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update({f"COM{i}" for i in range(1, 10)})
    reserved.update({f"LPT{i}" for i in range(1, 10)})
    if Path(candidate).stem.upper() in reserved:
        raise ValueError("The audio filename is reserved by Windows.")
    return candidate


def display_title_from_name(name: str) -> str:
    title = Path(name).stem.replace("_", " ").replace("-", " ")
    title = " ".join(title.split()).strip()
    return title or "Untitled Track"


@dataclass
class TrackRecord:
    track_id: str
    content_hash: str
    display_title: str
    original_name: str
    original_file: str
    processed_file: str
    duration_seconds: float = 0.0
    added_at: str = field(default_factory=utc_now_text)

    @classmethod
    def from_dict(cls, payload: dict) -> "TrackRecord":
        return cls(
            track_id=str(payload.get("track_id", "")).strip(),
            content_hash=str(payload.get("content_hash", "")).strip(),
            display_title=str(payload.get("display_title", "")).strip() or "Untitled Track",
            original_name=str(payload.get("original_name", "")).strip(),
            original_file=str(payload.get("original_file", "")).strip(),
            processed_file=str(payload.get("processed_file", "")).strip(),
            duration_seconds=max(0.0, float(payload.get("duration_seconds", 0.0) or 0.0)),
            added_at=str(payload.get("added_at", "")).strip() or utc_now_text(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectSoundState:
    mode: str = "single"
    single_track_id: str = ""
    playlist: list[str] = field(default_factory=list)
    playlist_expanded: bool = True
    selected_track_id: str = ""

    def normalize(self, valid_ids: Optional[set[str]] = None) -> "ProjectSoundState":
        self.mode = "playlist" if self.mode == "playlist" else "single"
        self.single_track_id = str(self.single_track_id or "").strip()
        clean: list[str] = []
        seen: set[str] = set()
        for value in self.playlist:
            track_id = str(value or "").strip()
            if not track_id or track_id in seen:
                continue
            if valid_ids is not None and track_id not in valid_ids:
                continue
            seen.add(track_id)
            clean.append(track_id)
        self.playlist = clean
        if valid_ids is not None and self.single_track_id not in valid_ids:
            self.single_track_id = ""
        self.selected_track_id = str(self.selected_track_id or "").strip()
        if valid_ids is not None and self.selected_track_id not in valid_ids:
            self.selected_track_id = ""
        if self.mode == "single":
            self.playlist = []
            self.selected_track_id = self.single_track_id
        elif self.selected_track_id not in self.playlist:
            self.selected_track_id = self.playlist[0] if self.playlist else ""
        return self

    @classmethod
    def from_dict(cls, payload: dict) -> "ProjectSoundState":
        playlist = payload.get("playlist", [])
        return cls(
            mode=str(payload.get("mode", "single")),
            single_track_id=str(payload.get("single_track_id", "")),
            playlist=[str(item) for item in playlist] if isinstance(playlist, list) else [],
            playlist_expanded=bool(payload.get("playlist_expanded", True)),
            selected_track_id=str(payload.get("selected_track_id", "")),
        )

    def to_dict(self) -> dict:
        return {
            "version": SOUND_MODEL_VERSION,
            "mode": self.mode,
            "single_track_id": self.single_track_id,
            "playlist": list(self.playlist),
            "playlist_expanded": bool(self.playlist_expanded),
            "selected_track_id": self.selected_track_id,
        }

    def ordered_track_ids(self) -> list[str]:
        if self.mode == "playlist":
            return list(self.playlist)
        return [self.single_track_id] if self.single_track_id else []

    def is_using(self, track_id: str) -> bool:
        return track_id == self.single_track_id or track_id in self.playlist

    def remove_usage(self, track_id: str) -> None:
        if self.single_track_id == track_id:
            self.single_track_id = ""
        self.playlist = [item for item in self.playlist if item != track_id]
        if self.selected_track_id == track_id:
            self.selected_track_id = self.playlist[0] if self.playlist else self.single_track_id


def load_library(project_root: str | Path) -> dict[str, TrackRecord]:
    ensure_sound_dirs(project_root)
    payload = read_json(library_path(project_root), {"version": SOUND_MODEL_VERSION, "tracks": {}})
    raw_tracks = payload.get("tracks", {})
    if not isinstance(raw_tracks, dict):
        return {}
    records: dict[str, TrackRecord] = {}
    for key, value in raw_tracks.items():
        if not isinstance(value, dict):
            continue
        try:
            record = TrackRecord.from_dict(value)
        except (TypeError, ValueError):
            continue
        if not record.track_id:
            record.track_id = str(key)
        if record.track_id and record.processed_file:
            records[record.track_id] = record
    return records


def save_library(project_root: str | Path, records: dict[str, TrackRecord]) -> None:
    atomic_write_json(
        library_path(project_root),
        {
            "version": SOUND_MODEL_VERSION,
            "tracks": {key: record.to_dict() for key, record in sorted(records.items())},
        },
    )


def load_project_state(
    project_root: str | Path,
    *,
    valid_ids: Optional[set[str]] = None,
) -> ProjectSoundState:
    payload = read_json(project_sound_path(project_root), {"version": SOUND_MODEL_VERSION})
    return ProjectSoundState.from_dict(payload).normalize(valid_ids)


def save_project_state(project_root: str | Path, state: ProjectSoundState) -> None:
    atomic_write_json(project_sound_path(project_root), state.normalize().to_dict())


def resolve_track_path(project_root: str | Path, record: TrackRecord) -> Path:
    return processed_dir(project_root) / Path(record.processed_file).name


def resolve_project_tracks(project_root: str | Path) -> tuple[ProjectSoundState, list[TrackRecord]]:
    records = load_library(project_root)
    state = load_project_state(project_root, valid_ids=set(records))
    tracks = [records[track_id] for track_id in state.ordered_track_ids() if track_id in records]
    return state, tracks


def build_sound_manifest(state: ProjectSoundState, tracks: Iterable[TrackRecord], filenames: list[str]) -> dict:
    track_list = list(tracks)
    return {
        "version": SOUND_MODEL_VERSION,
        "mode": state.mode,
        "crossfade_ms": 1000 if state.mode == "playlist" and len(track_list) > 1 else 0,
        "tracks": [
            {
                "filename": filename,
                "display_title": record.display_title,
                "duration_seconds": record.duration_seconds,
                "content_hash": record.content_hash,
                "original_name": record.original_name,
            }
            for record, filename in zip(track_list, filenames)
        ],
    }


def atomic_copy_file(source: str | Path, destination: str | Path) -> None:
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source file does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with src.open("rb") as read_handle, tmp.open("wb") as write_handle:
            while True:
                chunk = read_handle.read(1024 * 1024)
                if not chunk:
                    break
                write_handle.write(chunk)
            write_handle.flush()
            os.fsync(write_handle.fileno())
        _replace_with_retry(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def import_runtime_track(
    project_root: str | Path,
    source: str | Path,
    *,
    display_title: str = "",
    original_name: str = "",
    content_hash: str = "",
    duration_seconds: float = 0.0,
) -> TrackRecord:
    ensure_sound_dirs(project_root)
    src = Path(source).resolve()
    digest = content_hash or hash_file(src)
    records = load_library(project_root)
    for record in records.values():
        if record.content_hash != digest:
            continue
        processed = resolve_track_path(project_root, record)
        if not processed.is_file():
            atomic_copy_file(src, processed)
        return record
    track_id = digest[:24]
    while track_id in records:
        track_id = digest[: min(64, len(track_id) + 4)]
    processed_name = f"{track_id}.mp3"
    source_name = safe_filename(original_name or src.name)
    stored_original_name = safe_filename(f"{Path(source_name).stem}.mp3")
    original_file = f"{track_id}__{stored_original_name}"
    atomic_copy_file(src, processed_dir(project_root) / processed_name)
    atomic_copy_file(src, originals_dir(project_root) / original_file)
    record = TrackRecord(
        track_id=track_id,
        content_hash=digest,
        display_title=" ".join(display_title.split()).strip() or display_title_from_name(source_name),
        original_name=source_name,
        original_file=original_file,
        processed_file=processed_name,
        duration_seconds=max(0.0, float(duration_seconds or 0.0)),
        added_at=utc_now_text(),
    )
    records[track_id] = record
    save_library(project_root, records)
    return record


def sync_current_compatibility(
    project_root: str | Path,
    state: ProjectSoundState,
    records: dict[str, TrackRecord],
) -> None:
    state.normalize(set(records))
    selected = state.selected_track_id
    ordered = state.ordered_track_ids()
    if selected not in ordered:
        selected = ordered[0] if ordered else ""
        state.selected_track_id = selected
    music = current_music_path(project_root)
    manifest = current_manifest_path(project_root)
    record = records.get(selected)
    if record is None:
        music.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        return
    source = resolve_track_path(project_root, record)
    if not source.is_file():
        music.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        return
    atomic_copy_file(source, music)
    atomic_write_json(
        manifest,
        {
            "current_rel": f"{USER_SOUNDS_DIR}/{ARCHIVE_DIR_NAME}/{PROCESSED_DIR_NAME}/{record.processed_file}",
            "track_id": record.track_id,
            "link_mode": "copy",
        },
    )
