from __future__ import annotations

import ipaddress
import json
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from transactional_io import atomic_write_text


SETTINGS_FILENAME = "settings.json"
DEFAULT_SETTINGS = {
    "starting_volume": 31,
    "last_audio": "music.mp3",
    "curtain_style": "pure_white",
}
VALID_CURTAIN_STYLES = {
    "pure_white",
    "average_color",
    "complementary_average_color",
}
CURTAIN_STYLE_LABELS = {
    "pure_white": "Pure White",
    "average_color": "Average Color",
    "complementary_average_color": "Complementary Average Color",
}
CURTAIN_STYLE_ALIASES = {
    "white": "pure_white",
    "pure white": "pure_white",
    "blank": "pure_white",
    "original": "pure_white",
    "average": "average_color",
    "average color": "average_color",
    "common": "average_color",
    "common color": "average_color",
    "complementary": "complementary_average_color",
    "complementary average": "complementary_average_color",
    "complementary average color": "complementary_average_color",
}
PUBLISHED_PAGE_URL_KEY = "published_page_url"
REQUIRED_FEATURES_KEY = "required_features"
PROJECT_ID_KEY = "project_id"
PROJECT_SCHEMA_KEY = "project_schema_version"
PROJECT_SCHEMA_VERSION = 1
_LOGGER = logging.getLogger(__name__)


def normalize_published_page_url(value: object) -> str:
    """Return a trimmed HTTP(S) URL without inventing a missing scheme."""
    candidate = str(value or "").strip()
    if not candidate or any(character.isspace() for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        _port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.casefold() not in {"http", "https"}:
        return ""
    if not parsed.netloc or not parsed.hostname:
        return ""
    if "\\" in candidate:
        return ""
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        return ""
    hostname = parsed.hostname.rstrip(".")
    if not hostname:
        return ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if all(part.isdigit() for part in hostname.split(".")):
            return ""
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return ""
        labels = ascii_hostname.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            return ""
    return candidate


class SettingsChanged:
    def __init__(self) -> None:
        self._callbacks: list[Callable[[dict[str, Any], tuple[str, ...]], None]] = []

    def connect(self, callback: Callable[[dict[str, Any], tuple[str, ...]], None]) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def disconnect(self, callback: Callable[[dict[str, Any], tuple[str, ...]], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(self, settings: dict[str, Any], keys: tuple[str, ...]) -> None:
        for callback in tuple(self._callbacks):
            try:
                callback(dict(settings), keys)
            except Exception:
                _LOGGER.exception(
                    "Settings change callback failed for keys: %s",
                    ", ".join(keys),
                )


class SettingsStore:
    """Atomic, merge-based access to one project settings file."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}
    _signals_guard = threading.Lock()
    _signals: dict[str, SettingsChanged] = {}

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.path = self.project_root / SETTINGS_FILENAME
        self.changed = self._signal_for(self.path)
        self._settings: dict[str, Any] = {}
        self.validate_and_migrate()

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        key = str(path.resolve()).casefold()
        with cls._locks_guard:
            return cls._locks.setdefault(key, threading.RLock())

    @classmethod
    def _signal_for(cls, path: Path) -> SettingsChanged:
        key = str(path.resolve()).casefold()
        with cls._signals_guard:
            return cls._signals.setdefault(key, SettingsChanged())

    def get(self, key: str, default: Any = None) -> Any:
        return self.reload().get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """Return an independent snapshot of the latest settings on disk."""
        return self.reload()

    def reload(self) -> dict[str, Any]:
        lock = self._lock_for(self.path)
        with lock:
            raw, invalid = self._read_unlocked()
            normalized = self._normalize(raw)
            if invalid or normalized != raw or not self.path.exists():
                self._write_unlocked(normalized)
            self._settings = normalized
            return dict(self._settings)

    def validate_and_migrate(self) -> dict[str, Any]:
        return self.reload()

    def update_fields(
        self,
        fields: Optional[Mapping[str, Any]] = None,
        **updates: Any,
    ) -> dict[str, Any]:
        merged_updates = dict(fields or {})
        merged_updates.update(updates)
        if not merged_updates:
            return self.reload()

        lock = self._lock_for(self.path)
        with lock:
            current, _invalid = self._read_unlocked()
            current = self._normalize(current)
            before = dict(current)
            current.update(merged_updates)
            current = self._normalize(current)
            changed_keys = tuple(
                key for key in merged_updates if before.get(key) != current.get(key)
            )
            if current != before or not self.path.exists():
                self._write_unlocked(current)
            self._settings = current

        if changed_keys:
            self.changed.emit(self._settings, changed_keys)
        return dict(self._settings)

    def replace_snapshot(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically replace project settings and notify every live store."""
        lock = self._lock_for(self.path)
        with lock:
            before, _invalid = self._read_unlocked()
            before = self._normalize(before)
            replacement = self._normalize(settings)
            self._write_unlocked(replacement)
            self._settings = replacement
        changed_keys = tuple(
            sorted(
                key
                for key in set(before) | set(replacement)
                if before.get(key) != replacement.get(key)
            )
        )
        if changed_keys:
            self.changed.emit(self._settings, changed_keys)
        return dict(self._settings)

    def _read_unlocked(self) -> tuple[dict[str, Any], bool]:
        if not self.path.exists():
            return {}, False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data, False
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

        self._backup_invalid_unlocked()
        return {}, True

    def _backup_invalid_unlocked(self) -> None:
        if not self.path.is_file():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"settings.invalid.{stamp}.{time.time_ns()}.json")
        try:
            shutil.copy2(self.path, backup)
        except OSError:
            pass

    def _write_unlocked(self, settings: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(settings), indent=2, ensure_ascii=False) + "\n"
        atomic_write_text(self.path, payload)

    @staticmethod
    def _normalize(settings: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(settings)

        try:
            project_id = str(uuid.UUID(str(normalized.get(PROJECT_ID_KEY, ""))))
        except (ValueError, TypeError, AttributeError):
            project_id = str(uuid.uuid4())
        normalized[PROJECT_ID_KEY] = project_id
        normalized[PROJECT_SCHEMA_KEY] = PROJECT_SCHEMA_VERSION

        normalized[PUBLISHED_PAGE_URL_KEY] = normalize_published_page_url(
            normalized.get(PUBLISHED_PAGE_URL_KEY, "")
        )

        raw_required = normalized.get(REQUIRED_FEATURES_KEY, {})
        required = dict(raw_required) if isinstance(raw_required, Mapping) else {}
        required["music"] = bool(
            required.get("music", normalized.get("music_required", False))
        )
        normalized[REQUIRED_FEATURES_KEY] = required

        try:
            starting_volume = int(normalized.get("starting_volume", DEFAULT_SETTINGS["starting_volume"]))
        except (TypeError, ValueError):
            starting_volume = int(DEFAULT_SETTINGS["starting_volume"])
        normalized["starting_volume"] = max(0, min(100, starting_volume))

        if "music_volume" in normalized:
            try:
                music_volume = int(normalized["music_volume"])
            except (TypeError, ValueError):
                music_volume = normalized["starting_volume"]
            normalized["music_volume"] = max(0, min(100, music_volume))

        try:
            last_audio = Path(str(normalized.get("last_audio", DEFAULT_SETTINGS["last_audio"]))).name
        except (TypeError, ValueError):
            last_audio = str(DEFAULT_SETTINGS["last_audio"])
        normalized["last_audio"] = last_audio or str(DEFAULT_SETTINGS["last_audio"])

        style = str(normalized.get("curtain_style", DEFAULT_SETTINGS["curtain_style"])).strip().lower()
        style = CURTAIN_STYLE_ALIASES.get(style, style.replace(" ", "_"))
        if style not in VALID_CURTAIN_STYLES:
            style = str(DEFAULT_SETTINGS["curtain_style"])
        normalized["curtain_style"] = style
        return normalized


__all__ = [
    "CURTAIN_STYLE_LABELS",
    "DEFAULT_SETTINGS",
    "PUBLISHED_PAGE_URL_KEY",
    "REQUIRED_FEATURES_KEY",
    "SettingsChanged",
    "SettingsStore",
    "VALID_CURTAIN_STYLES",
    "normalize_published_page_url",
]
