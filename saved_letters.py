from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import PLAY_METADATA_FILE
from project_state import ensure_project_identity
from readiness import ReadinessResult
from settings_store import SettingsStore
from sound_model import resolve_project_tracks
from transactional_io import atomic_write_json


@dataclass(frozen=True)
class SavedLetter:
    path: Path
    recipient: str
    title: str
    modified_at: datetime
    published_url: str
    cover_path: Optional[Path]
    recovery: bool = False

    @property
    def published(self) -> bool:
        return bool(self.published_url)


class SavedLetterCatalog:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.play_root = self.project_root / "output" / "Play"
        self.recovery_root = self.project_root / "output" / "Recovery"

    def list_entries(self) -> tuple[SavedLetter, ...]:
        saved: list[SavedLetter] = []
        recovery: list[SavedLetter] = []
        if self.play_root.is_dir():
            for index in self.play_root.rglob("index.html"):
                saved.append(self._entry(index.parent, recovery=False))
        if self.recovery_root.is_dir():
            for path in self.recovery_root.iterdir():
                if path.is_dir():
                    recovery.append(self._entry(path, recovery=True))
        key = lambda entry: (entry.modified_at, entry.title.casefold())
        saved.sort(key=key, reverse=True)
        recovery.sort(key=key, reverse=True)
        return tuple((*saved, *recovery))

    def search(self, query: str) -> tuple[SavedLetter, ...]:
        needle = (query or "").strip().casefold()
        if not needle:
            return self.list_entries()
        return tuple(
            entry
            for entry in self.list_entries()
            if needle in f"{entry.recipient} {entry.title}".casefold()
        )

    def _entry(self, path: Path, *, recovery: bool) -> SavedLetter:
        metadata = self._metadata(path)
        recipient = str(metadata.get("recipient_name") or "").strip()
        title = str(metadata.get("recipient_title") or "").strip()
        if not title:
            title = self._html_title(path / "index.html") or path.name
        if not recipient:
            recipient = "Recovery" if recovery else self._humanize(path.parent.name)
        cover = next(
            (
                candidate
                for candidate in (
                    path / "gallery/pages/cover.png",
                    path / "gallery/user/pages/cover.png",
                    path / "cover.png",
                )
                if candidate.is_file()
            ),
            None,
        )
        return SavedLetter(
            path=path.resolve(),
            recipient=recipient,
            title=title,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            published_url=str(metadata.get("published_page_url") or "").strip(),
            cover_path=cover.resolve() if cover else None,
            recovery=recovery,
        )

    @staticmethod
    def _metadata(path: Path) -> dict:
        for name in (
            PLAY_METADATA_FILE,
            "play_metadata.json",
            "recovery_metadata.json",
            "metadata.json",
        ):
            candidate = path / name
            if not candidate.is_file():
                continue
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _html_title(path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        match = re.search(r"<title>\s*(.*?)\s*</title>", value, re.I | re.S)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    @staticmethod
    def _humanize(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip().title()


def update_saved_metadata(
    play_dir: str | Path,
    project_root: str | Path,
    readiness: ReadinessResult,
) -> dict:
    root = Path(project_root).resolve()
    destination = Path(play_dir).resolve()
    metadata_path = destination / PLAY_METADATA_FILE
    metadata = SavedLetterCatalog._metadata(destination)
    settings = SettingsStore(root).snapshot()
    sound_state, sound_tracks = resolve_project_tracks(root)
    restorable_settings = {
        key: settings[key]
        for key in (
            "starting_volume",
            "music_volume",
            "curtain_style",
            "message_overlay_preset",
            "message_overlay_opacity",
        )
        if key in settings
    }
    metadata.update(
        {
            "project_id": ensure_project_identity(root),
            "recipient_name": str(settings.get("recipient_name", "")).strip(),
            "recipient_title": str(settings.get("recipient_title", "")).strip(),
            "build_location": str(destination),
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            "published_page_url": str(settings.get("published_page_url", "")).strip(),
            "settings": restorable_settings,
            "sound": {
                "mode": sound_state.mode,
                "track_count": len(sound_tracks),
                "tracks": [
                    {
                        "display_title": track.display_title,
                        "duration_seconds": track.duration_seconds,
                    }
                    for track in sound_tracks
                ],
            },
            "readiness": {
                "percentage": readiness.completion_percentage,
                "status": readiness.status,
            },
            "cover_thumbnail_path": "gallery/pages/cover.png",
            "source_version": 2,
        }
    )
    atomic_write_json(metadata_path, metadata)
    return metadata
