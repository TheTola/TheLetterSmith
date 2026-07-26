from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from transactional_io import atomic_write_bytes, atomic_write_json


PLAYLIST_VERSION = 1
CROSSFADE_MS = 1000
PLAYLIST_PATH = Path("gallery/user/sounds/playlist.json")
CURRENT_MUSIC_PATH = Path("gallery/user/sounds/music.mp3")
CURRENT_MANIFEST_PATH = Path("gallery/user/sounds/appssong/current.json")
PROCESSED_ARCHIVE_PATH = Path("gallery/user/sounds/appssong/processed")
ORIGINAL_ARCHIVE_PATH = Path("gallery/user/sounds/appssong/originals")


@dataclass(frozen=True)
class PlaylistTrack:
    archive_name: str


@dataclass(frozen=True)
class Playlist:
    tracks: tuple[PlaylistTrack, ...] = ()
    repeat: bool = True
    crossfade_ms: int = CROSSFADE_MS
    version: int = PLAYLIST_VERSION


def playlist_payload(playlist: Playlist) -> dict:
    return {
        "version": PLAYLIST_VERSION,
        "tracks": [
            {"archive_name": track.archive_name}
            for track in playlist.tracks
        ],
        "repeat": bool(playlist.repeat),
        "crossfade_ms": CROSSFADE_MS,
    }


class PlaylistStore:
    """Transactional persistence for the active Letter Smith playlist."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.path = self.project_root / PLAYLIST_PATH
        self.current_music = self.project_root / CURRENT_MUSIC_PATH

    def load(self, *, migrate_legacy: bool = True) -> Playlist:
        if not self.path.is_file():
            if migrate_legacy and self.current_music.is_file():
                return self.migrate_legacy_music()
            return Playlist()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            playlist = self._normalize(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            self._backup_invalid()
            playlist = Playlist()
            self.save(playlist)
            return playlist
        if raw != playlist_payload(playlist):
            self.save(playlist)
        return playlist

    def save(self, playlist: Playlist) -> Playlist:
        normalized = Playlist(
            tracks=tuple(
                PlaylistTrack(Path(track.archive_name).name)
                for track in playlist.tracks
                if Path(track.archive_name).name
            ),
            repeat=bool(playlist.repeat),
        )
        atomic_write_json(self.path, playlist_payload(normalized))
        return normalized

    def add(self, archive_name: str) -> Playlist:
        name = Path(str(archive_name)).name
        if not name:
            raise ValueError("A playlist track name is required.")
        current = self.load(migrate_legacy=False)
        return self.save(
            Playlist(
                tracks=(*current.tracks, PlaylistTrack(name)),
                repeat=current.repeat,
            )
        )

    def replace_tracks(self, archive_names: Iterable[str]) -> Playlist:
        current = self.load(migrate_legacy=False)
        tracks = tuple(
            PlaylistTrack(Path(str(name)).name)
            for name in archive_names
            if Path(str(name)).name
        )
        return self.save(Playlist(tracks=tracks, repeat=current.repeat))

    def reorder(self, source_index: int, destination_index: int) -> Playlist:
        current = self.load(migrate_legacy=False)
        tracks = list(current.tracks)
        if not 0 <= source_index < len(tracks):
            raise IndexError("Playlist source index is out of range.")
        if not 0 <= destination_index < len(tracks):
            raise IndexError("Playlist destination index is out of range.")
        track = tracks.pop(source_index)
        tracks.insert(destination_index, track)
        return self.save(Playlist(tuple(tracks), current.repeat))

    def remove(self, index: int) -> Playlist:
        current = self.load(migrate_legacy=False)
        tracks = list(current.tracks)
        if not 0 <= index < len(tracks):
            raise IndexError("Playlist index is out of range.")
        tracks.pop(index)
        return self.save(Playlist(tuple(tracks), current.repeat))

    def set_repeat(self, repeat: bool) -> Playlist:
        current = self.load(migrate_legacy=False)
        return self.save(Playlist(current.tracks, bool(repeat)))

    def resolve_track(self, track: PlaylistTrack) -> Path | None:
        name = Path(track.archive_name).name
        candidates = [
            self.project_root / PROCESSED_ARCHIVE_PATH / name,
            self.project_root / ORIGINAL_ARCHIVE_PATH / name,
        ]
        if name.casefold() == "music.mp3":
            candidates.append(self.current_music)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None

    def resolve_all(self, playlist: Playlist | None = None) -> tuple[Path, ...]:
        selected = playlist or self.load()
        resolved: list[Path] = []
        missing: list[str] = []
        for track in selected.tracks:
            path = self.resolve_track(track)
            if path is None:
                missing.append(track.archive_name)
            else:
                resolved.append(path)
        if missing:
            raise FileNotFoundError("Playlist tracks are missing: " + ", ".join(missing))
        return tuple(resolved)

    def migrate_legacy_music(
        self,
        source_music: str | Path | None = None,
    ) -> Playlist:
        if self.path.is_file():
            current = self.load(migrate_legacy=False)
            if current.tracks:
                return current

        source = Path(source_music).resolve() if source_music is not None else self.current_music
        if not source.is_file():
            return Playlist()
        if source.resolve() != self.current_music.resolve():
            atomic_write_bytes(self.current_music, source.read_bytes())

        archive_name = self._legacy_archive_name()
        playlist = Playlist((PlaylistTrack(archive_name),), True)
        return self.save(playlist)

    def _legacy_archive_name(self) -> str:
        manifest = self.project_root / CURRENT_MANIFEST_PATH
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            raw = str(
                data.get("current_processed_rel")
                or data.get("current_rel")
                or ""
            )
            name = Path(raw).name
            if name and (self.project_root / PROCESSED_ARCHIVE_PATH / name).is_file():
                return name
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            pass
        return "music.mp3"

    @staticmethod
    def _normalize(raw: object) -> Playlist:
        if not isinstance(raw, dict):
            raise ValueError("Playlist root must be an object.")
        raw_tracks = raw.get("tracks", [])
        if not isinstance(raw_tracks, list):
            raise ValueError("Playlist tracks must be a list.")
        tracks: list[PlaylistTrack] = []
        for entry in raw_tracks:
            if not isinstance(entry, dict):
                continue
            name = Path(str(entry.get("archive_name", ""))).name
            if name:
                tracks.append(PlaylistTrack(name))
        return Playlist(tuple(tracks), bool(raw.get("repeat", True)))

    def _backup_invalid(self) -> None:
        if not self.path.is_file():
            return
        backup = self.path.with_name(
            f"playlist.invalid.{time.strftime('%Y%m%d-%H%M%S')}.{time.time_ns()}.json"
        )
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, backup)
        except OSError:
            pass


__all__ = [
    "CROSSFADE_MS",
    "PLAYLIST_PATH",
    "PLAYLIST_VERSION",
    "Playlist",
    "PlaylistStore",
    "PlaylistTrack",
    "playlist_payload",
]
