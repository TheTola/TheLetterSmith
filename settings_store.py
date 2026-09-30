from __future__ import annotations

import ipaddress
import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from transactional_io import atomic_write_text, set_path_hidden
from letter_page import (
    DEFAULT_MESSAGE_OVERLAY_OPACITY,
    DEFAULT_MESSAGE_OVERLAY_PRESET,
    MESSAGE_OVERLAY_OPACITY_KEY,
    MESSAGE_OVERLAY_PRESET_KEY,
)
from project_paths import application_paths
from project_timestamps import (
    PROJECT_CREATED_AT_KEY,
    PROJECT_PUBLISHED_AT_KEY,
)


SETTINGS_FILENAME = "settings.json"
SETTINGS_SCHEMA_KEY = "settings_schema_version"
SETTINGS_SCHEMA_VERSION = 1
_LOGGER = logging.getLogger(__name__)

REQUIRED_FEATURES_KEY = "required_features"
PUBLISHED_PAGE_URL_KEY = "published_page_url"
PUBLISHED_PUBLIC_PATH_KEY = "published_public_path"
PUBLISHED_AT_KEY = "published_at"
PUBLISHED_EXPIRES_AT_KEY = "published_expires_at"
PUBLICATION_PROVIDER_KEY = "publication_provider"
PUBLICATION_VERIFIED_KEY = "publication_verified"
PUBLISHED_SOURCE_FINGERPRINT_KEY = "published_source_fingerprint"
PUBLISHED_GITHUB_OWNER_KEY = "published_github_owner"
PUBLISHED_GITHUB_REPOSITORY_KEY = "published_github_repository"
ACTIVE_PLAY_DIR_KEY = "active_play_dir"
VISIONARY_URL_KEY = "visionary_url"
PROMPT_WRITER_PROVIDER_KEY = "prompt_writer_provider"
DEFAULT_VISIONARY_URL = (
    "https://chatgpt.com/g/g-68ce5925196c8191a222e24d29323813-the-visionary"
)


CURTAIN_STYLE_WHITE = "pure_white"
CURTAIN_STYLE_NORMAL = "average_color"
CURTAIN_STYLE_COMPLEMENTARY = "complementary_average_color"
CURTAIN_STYLE_NORMAL_LIGHT = "normal_light"
CURTAIN_STYLE_COMPLEMENTARY_LIGHT = "complementary_light"
CURTAIN_STYLE_NORMAL_DARK = "normal_dark"
CURTAIN_STYLE_COMPLEMENTARY_DARK = "complementary_dark"

# Compatibility names for callers that treated Light and Dark as single leaves.
CURTAIN_STYLE_LIGHT = CURTAIN_STYLE_NORMAL_LIGHT
CURTAIN_STYLE_DARK = CURTAIN_STYLE_NORMAL_DARK

DEFAULT_CURTAIN_STYLE = CURTAIN_STYLE_NORMAL

CURTAIN_STYLE_OPTIONS = (
    (CURTAIN_STYLE_WHITE, "White Curtains"),
    (CURTAIN_STYLE_NORMAL, "Normal Curtains"),
    (CURTAIN_STYLE_COMPLEMENTARY, "Complementary Curtains"),
    (CURTAIN_STYLE_NORMAL_LIGHT, "Normal Light Curtains"),
    (CURTAIN_STYLE_COMPLEMENTARY_LIGHT, "Complementary Light Curtains"),
    (CURTAIN_STYLE_NORMAL_DARK, "Normal Dark Curtains"),
    (CURTAIN_STYLE_COMPLEMENTARY_DARK, "Complementary Dark Curtains"),
)

CURTAIN_STYLE_LABELS = dict(CURTAIN_STYLE_OPTIONS)
VALID_CURTAIN_STYLES = frozenset(CURTAIN_STYLE_LABELS)
CURTAIN_TEXT_STYLE_PAIRS = {
    CURTAIN_STYLE_NORMAL: CURTAIN_STYLE_COMPLEMENTARY,
    CURTAIN_STYLE_COMPLEMENTARY: CURTAIN_STYLE_NORMAL,
    CURTAIN_STYLE_NORMAL_LIGHT: CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
    CURTAIN_STYLE_COMPLEMENTARY_LIGHT: CURTAIN_STYLE_NORMAL_LIGHT,
    CURTAIN_STYLE_NORMAL_DARK: CURTAIN_STYLE_COMPLEMENTARY_DARK,
    CURTAIN_STYLE_COMPLEMENTARY_DARK: CURTAIN_STYLE_NORMAL_DARK,
}


DEFAULT_SETTINGS = {
    SETTINGS_SCHEMA_KEY: SETTINGS_SCHEMA_VERSION,
    "starting_volume": 31,
    "last_audio": "music.mp3",
    "curtain_style": DEFAULT_CURTAIN_STYLE,
    MESSAGE_OVERLAY_PRESET_KEY: DEFAULT_MESSAGE_OVERLAY_PRESET,
    MESSAGE_OVERLAY_OPACITY_KEY: DEFAULT_MESSAGE_OVERLAY_OPACITY,
    REQUIRED_FEATURES_KEY: [],
    PROJECT_CREATED_AT_KEY: "",
    PROJECT_PUBLISHED_AT_KEY: "",
    PUBLISHED_PAGE_URL_KEY: "",
    PUBLISHED_PUBLIC_PATH_KEY: "",
    PUBLISHED_AT_KEY: "",
    PUBLISHED_EXPIRES_AT_KEY: "",
    PUBLICATION_PROVIDER_KEY: "",
    PUBLICATION_VERIFIED_KEY: False,
    PUBLISHED_SOURCE_FINGERPRINT_KEY: "",
    PUBLISHED_GITHUB_OWNER_KEY: "",
    PUBLISHED_GITHUB_REPOSITORY_KEY: "",
    ACTIVE_PLAY_DIR_KEY: "",
    VISIONARY_URL_KEY: DEFAULT_VISIONARY_URL,
    PROMPT_WRITER_PROVIDER_KEY: "",
}

PUBLICATION_SETTING_KEYS = (
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
)


class UnsupportedSettingsSchemaError(RuntimeError):
    pass


def empty_publication_settings() -> dict[str, object]:
    return {
        key: DEFAULT_SETTINGS[key]
        for key in PUBLICATION_SETTING_KEYS
    }


CURTAIN_STYLE_ALIASES = {
    "white": CURTAIN_STYLE_WHITE,
    "pure white": CURTAIN_STYLE_WHITE,
    "white curtain": CURTAIN_STYLE_WHITE,
    "white curtains": CURTAIN_STYLE_WHITE,
    "blank": CURTAIN_STYLE_WHITE,
    "original": CURTAIN_STYLE_WHITE,

    "average": CURTAIN_STYLE_NORMAL,
    "average color": CURTAIN_STYLE_NORMAL,
    "common": CURTAIN_STYLE_NORMAL,
    "common color": CURTAIN_STYLE_NORMAL,
    "normal": CURTAIN_STYLE_NORMAL,
    "normal curtain": CURTAIN_STYLE_NORMAL,
    "normal curtains": CURTAIN_STYLE_NORMAL,

    "complementary": CURTAIN_STYLE_COMPLEMENTARY,
    "complementary average": CURTAIN_STYLE_COMPLEMENTARY,
    "complementary average color": CURTAIN_STYLE_COMPLEMENTARY,
    "complementary curtain": CURTAIN_STYLE_COMPLEMENTARY,
    "complementary curtains": CURTAIN_STYLE_COMPLEMENTARY,

    "light": CURTAIN_STYLE_NORMAL_LIGHT,
    "light curtain": CURTAIN_STYLE_NORMAL_LIGHT,
    "light curtains": CURTAIN_STYLE_NORMAL_LIGHT,
    "light_curtain": CURTAIN_STYLE_NORMAL_LIGHT,
    "lighter": CURTAIN_STYLE_NORMAL_LIGHT,
    "normal light": CURTAIN_STYLE_NORMAL_LIGHT,
    "normal light curtain": CURTAIN_STYLE_NORMAL_LIGHT,
    "normal light curtains": CURTAIN_STYLE_NORMAL_LIGHT,
    "complementary light": CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
    "complementary light curtain": CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
    "complementary light curtains": CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
    "complementary_light": CURTAIN_STYLE_COMPLEMENTARY_LIGHT,

    "dark": CURTAIN_STYLE_NORMAL_DARK,
    "dark curtain": CURTAIN_STYLE_NORMAL_DARK,
    "dark curtains": CURTAIN_STYLE_NORMAL_DARK,
    "dark_curtain": CURTAIN_STYLE_NORMAL_DARK,
    "darker": CURTAIN_STYLE_NORMAL_DARK,
    "normal dark": CURTAIN_STYLE_NORMAL_DARK,
    "normal dark curtain": CURTAIN_STYLE_NORMAL_DARK,
    "normal dark curtains": CURTAIN_STYLE_NORMAL_DARK,
    "complementary dark": CURTAIN_STYLE_COMPLEMENTARY_DARK,
    "complementary dark curtain": CURTAIN_STYLE_COMPLEMENTARY_DARK,
    "complementary dark curtains": CURTAIN_STYLE_COMPLEMENTARY_DARK,
    "complementary_dark": CURTAIN_STYLE_COMPLEMENTARY_DARK,
}


def normalize_curtain_style(value: object) -> str:
    """Return one canonical persisted curtain style."""
    key = " ".join(
        str(value or "")
        .strip()
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )
    style = CURTAIN_STYLE_ALIASES.get(
        key,
        key.replace(" ", "_"),
    )
    return (
        style
        if style in VALID_CURTAIN_STYLES
        else DEFAULT_CURTAIN_STYLE
    )


def normalize_published_page_url(
    value: object,
) -> str:
    """Return a trimmed HTTP(S) URL without inventing a missing scheme."""
    candidate = str(value or "").strip()
    if (
        not candidate
        or any(
            character.isspace()
            for character in candidate
        )
    ):
        return ""
    try:
        parsed = urlsplit(candidate)
        _port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.casefold() not in {
        "http",
        "https",
    }:
        return ""
    if not parsed.netloc or not parsed.hostname:
        return ""
    if "\\" in candidate:
        return ""
    if re.search(
        r"%(?![0-9A-Fa-f]{2})",
        candidate,
    ):
        return ""
    hostname = parsed.hostname.rstrip(".")
    if not hostname:
        return ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if all(
            part.isdigit()
            for part in hostname.split(".")
        ):
            return ""
        try:
            ascii_hostname = (
                hostname.encode("idna")
                .decode("ascii")
            )
        except UnicodeError:
            return ""
        labels = ascii_hostname.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(
                character.isalnum()
                or character == "-"
                for character in label
            )
            for label in labels
        ):
            return ""
    return candidate


class SettingsChanged:
    def __init__(self) -> None:
        self._callbacks: list[
            Callable[
                [
                    dict[str, Any],
                    tuple[str, ...],
                ],
                None,
            ]
        ] = []

    def connect(
        self,
        callback: Callable[
            [
                dict[str, Any],
                tuple[str, ...],
            ],
            None,
        ],
    ) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def disconnect(
        self,
        callback: Callable[
            [
                dict[str, Any],
                tuple[str, ...],
            ],
            None,
        ],
    ) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(
        self,
        settings: dict[str, Any],
        keys: tuple[str, ...],
    ) -> None:
        for callback in tuple(
            self._callbacks
        ):
            try:
                callback(
                    dict(settings),
                    keys,
                )
            except Exception:
                # One failed observer must not prevent
                # the remaining observers from updating.
                continue


class SettingsStore:
    """
    Atomic, merge-based access to the project settings file.
    """

    _locks_guard = threading.Lock()

    _locks: dict[
        str,
        threading.RLock,
    ] = {}

    _signals: dict[str, SettingsChanged] = {}

    def __init__(
        self,
        project_root: str | Path,
    ) -> None:
        self.project_root = Path(
            project_root
        ).resolve()

        self.path = application_paths(
            self.project_root
        ).settings_file
        set_path_hidden(self.path)

        signal_key = str(self.path).casefold()
        with self._locks_guard:
            self.changed = self._signals.setdefault(
                signal_key,
                SettingsChanged(),
            )

        self._settings: dict[
            str,
            Any,
        ] = {}
        self._file_signature: tuple[int, int, int, int, int] | None = None

        self.validate_and_migrate()

    @classmethod
    def _lock_for(
        cls,
        path: Path,
    ) -> threading.RLock:
        key = str(
            path.resolve()
        ).casefold()

        with cls._locks_guard:
            return cls._locks.setdefault(
                key,
                threading.RLock(),
            )

    def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self.reload().get(
            key,
            default,
        )

    def as_dict(
        self,
    ) -> dict[str, Any]:
        return self.snapshot()

    def last_folder(self, picker: str) -> str:
        key = f"ui_last_folder_{str(picker).strip().casefold()}"
        value = self.get(key, "")
        path = Path(str(value)).expanduser() if value else None
        return str(path) if path is not None and path.is_dir() else ""

    def remember_folder(self, picker: str, selected_path: str | Path) -> str:
        candidate = Path(selected_path).expanduser()
        folder = candidate if candidate.is_dir() else candidate.parent
        try:
            folder = folder.resolve()
        except OSError:
            folder = folder.absolute()
        key = f"ui_last_folder_{str(picker).strip().casefold()}"
        self.update_fields({key: str(folder)})
        return str(folder)

    def snapshot(
        self,
    ) -> dict[str, Any]:
        return self.reload()

    def reload(
        self,
    ) -> dict[str, Any]:
        lock = self._lock_for(
            self.path
        )

        with lock:
            signature = self._stat_signature_unlocked()
            if (
                self._file_signature is not None
                and signature == self._file_signature
            ):
                return dict(self._settings)

            raw, invalid = self._read_unlocked()

            normalized = self._normalize(
                raw
            )

            if (
                invalid
                or normalized != raw
                or not self.path.exists()
            ):
                self._write_unlocked(
                    normalized
                )
            else:
                self._file_signature = self._stat_signature_unlocked()

            self._settings = normalized

            return dict(
                self._settings
            )

    def validate_and_migrate(
        self,
    ) -> dict[str, Any]:
        return self.reload()

    def update_fields(
        self,
        fields: Optional[
            Mapping[str, Any]
        ] = None,
        **updates: Any,
    ) -> dict[str, Any]:
        merged_updates = dict(
            fields or {}
        )

        merged_updates.update(
            updates
        )

        if not merged_updates:
            return self.reload()

        lock = self._lock_for(
            self.path
        )

        with lock:
            signature = self._stat_signature_unlocked()
            if (
                self._file_signature is not None
                and signature == self._file_signature
            ):
                current = dict(self._settings)
            else:
                current, _invalid = self._read_unlocked()

            current = self._normalize(
                current
            )

            before = dict(current)

            current.update(
                merged_updates
            )

            current = self._normalize(
                current
            )

            changed_keys = tuple(
                key
                for key in merged_updates
                if (
                    before.get(key)
                    != current.get(key)
                )
            )

            if (
                current != before
                or not self.path.exists()
            ):
                self._write_unlocked(
                    current
                )
            else:
                self._file_signature = self._stat_signature_unlocked()

            self._settings = current

        if changed_keys:
            self.changed.emit(
                self._settings,
                changed_keys,
            )

        return dict(
            self._settings
        )

    def replace_snapshot(
        self,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically replace the complete project settings snapshot."""
        lock = self._lock_for(
            self.path
        )

        with lock:
            signature = self._stat_signature_unlocked()
            if (
                self._file_signature is not None
                and signature == self._file_signature
            ):
                before = dict(self._settings)
            else:
                before, _invalid = self._read_unlocked()

            before = self._normalize(
                before
            )

            replacement = self._normalize(
                settings
            )

            self._write_unlocked(
                replacement
            )

            self._settings = replacement

        changed_keys = tuple(
            sorted(
                key
                for key in (
                    set(before)
                    | set(replacement)
                )
                if (
                    before.get(key)
                    != replacement.get(key)
                )
            )
        )

        if changed_keys:
            self.changed.emit(
                self._settings,
                changed_keys,
            )

        return dict(
            self._settings
        )

    def _read_unlocked(
        self,
    ) -> tuple[
        dict[str, Any],
        bool,
    ]:
        if not self.path.exists():
            return {}, False

        try:
            raw_text = self.path.read_text(
                encoding="utf-8",
            )

            data = json.loads(
                raw_text
            )

            if isinstance(
                data,
                dict,
            ):
                return data, False

        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            pass

        self._backup_invalid_unlocked()

        return {}, True

    def _backup_invalid_unlocked(
        self,
    ) -> None:
        if not self.path.is_file():
            return

        stamp = time.strftime(
            "%Y%m%d-%H%M%S"
        )

        backup = self.path.with_name(
            "settings.invalid."
            f"{stamp}."
            f"{time.time_ns()}.json"
        )

        try:
            shutil.copy2(
                self.path,
                backup,
            )
            set_path_hidden(backup)
        except OSError:
            pass

    def _write_unlocked(
        self,
        settings: Mapping[
            str,
            Any,
        ],
    ) -> None:
        payload = (
            json.dumps(
                dict(settings),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

        atomic_write_text(
            self.path,
            payload,
        )
        set_path_hidden(self.path)
        self._file_signature = self._stat_signature_unlocked()

    def _stat_signature_unlocked(
        self,
    ) -> tuple[int, int, int, int, int] | None:
        try:
            stat_result = self.path.stat()
        except OSError:
            return None
        return (
            int(stat_result.st_dev),
            int(stat_result.st_ino),
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            int(stat_result.st_ctime_ns),
        )

    @staticmethod
    def _normalize(
        settings: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:
        normalized = dict(
            settings
        )

        try:
            source_schema = int(normalized.get(SETTINGS_SCHEMA_KEY, 0))
        except (TypeError, ValueError):
            source_schema = 0
        if source_schema > SETTINGS_SCHEMA_VERSION:
            raise UnsupportedSettingsSchemaError(
                "Settings were written by a newer Letter Smith version "
                f"(schema {source_schema})."
            )
        normalized[SETTINGS_SCHEMA_KEY] = SETTINGS_SCHEMA_VERSION
        if source_schema and source_schema < SETTINGS_SCHEMA_VERSION:
            _LOGGER.info(
                "Settings upgraded from schema %d -> %d",
                source_schema,
                SETTINGS_SCHEMA_VERSION,
            )

        # Starting volume
        try:
            starting_volume = int(
                normalized.get(
                    "starting_volume",
                    DEFAULT_SETTINGS[
                        "starting_volume"
                    ],
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            starting_volume = int(
                DEFAULT_SETTINGS[
                    "starting_volume"
                ]
            )

        normalized[
            "starting_volume"
        ] = max(
            0,
            min(
                100,
                starting_volume,
            ),
        )

        # Current music volume
        if "music_volume" in normalized:
            try:
                music_volume = int(
                    normalized[
                        "music_volume"
                    ]
                )
            except (
                TypeError,
                ValueError,
            ):
                music_volume = normalized[
                    "starting_volume"
                ]

            normalized[
                "music_volume"
            ] = max(
                0,
                min(
                    100,
                    music_volume,
                ),
            )

        # Last selected audio filename
        try:
            last_audio = Path(
                str(
                    normalized.get(
                        "last_audio",
                        DEFAULT_SETTINGS[
                            "last_audio"
                        ],
                    )
                )
            ).name
        except (
            TypeError,
            ValueError,
        ):
            last_audio = str(
                DEFAULT_SETTINGS[
                    "last_audio"
                ]
            )

        normalized[
            "last_audio"
        ] = (
            last_audio
            or str(
                DEFAULT_SETTINGS[
                    "last_audio"
                ]
            )
        )

        # Required optional features
        raw_required_features = (
            normalized.get(
                REQUIRED_FEATURES_KEY,
                DEFAULT_SETTINGS[
                    REQUIRED_FEATURES_KEY
                ],
            )
        )

        if isinstance(
            raw_required_features,
            str,
        ):
            raw_required_features = [
                raw_required_features
            ]

        elif not isinstance(
            raw_required_features,
            (
                list,
                tuple,
                set,
            ),
        ):
            raw_required_features = []

        normalized[
            REQUIRED_FEATURES_KEY
        ] = sorted(
            {
                str(feature).strip()
                for feature
                in raw_required_features
                if str(feature).strip()
            }
        )

        # Published page URL
        normalized[
            PUBLISHED_PAGE_URL_KEY
        ] = normalize_published_page_url(
            normalized.get(
                PUBLISHED_PAGE_URL_KEY,
                "",
            )
        )
        for key in (
            PROJECT_CREATED_AT_KEY,
            PROJECT_PUBLISHED_AT_KEY,
            PUBLISHED_PUBLIC_PATH_KEY,
            PUBLISHED_AT_KEY,
            PUBLISHED_EXPIRES_AT_KEY,
            PUBLICATION_PROVIDER_KEY,
            PUBLISHED_SOURCE_FINGERPRINT_KEY,
            PUBLISHED_GITHUB_OWNER_KEY,
            PUBLISHED_GITHUB_REPOSITORY_KEY,
        ):
            value = normalized.get(key, DEFAULT_SETTINGS[key])
            normalized[key] = value.strip() if isinstance(value, str) else ""
        normalized[PUBLICATION_VERIFIED_KEY] = (
            normalized.get(PUBLICATION_VERIFIED_KEY) is True
        )
        for obsolete_key in (
            "r2_account_id",
            "r2_authentication_mode",
            "r2_bucket",
            "r2_current_public_path",
            "r2_free_tier_limit_bytes",
            "r2_last_object_count",
            "r2_last_usage_at",
            "r2_last_used_bytes",
            "r2_public_base_url",
            "r2_public_warning_acknowledged",
        ):
            normalized.pop(obsolete_key, None)

        # Active generated-letter directory. Path validation remains with the
        # owning workflow because the folder may be moved between sessions.
        active_play_dir = normalized.get(
            ACTIVE_PLAY_DIR_KEY,
            DEFAULT_SETTINGS[ACTIVE_PLAY_DIR_KEY],
        )
        normalized[ACTIVE_PLAY_DIR_KEY] = (
            active_play_dir.strip()
            if isinstance(active_play_dir, str)
            else ""
        )

        # Prompt Writer companion URL.
        visionary_url = normalize_published_page_url(
            normalized.get(
                VISIONARY_URL_KEY,
                DEFAULT_VISIONARY_URL,
            )
        )
        normalized[VISIONARY_URL_KEY] = (
            visionary_url
            or DEFAULT_VISIONARY_URL
        )

        provider = normalized.get(PROMPT_WRITER_PROVIDER_KEY, "")
        normalized[PROMPT_WRITER_PROVIDER_KEY] = (
            provider if provider in {"ChatGPT", "Gemini"} else ""
        )

        # Curtain style
        normalized[
            "curtain_style"
        ] = normalize_curtain_style(
            normalized.get(
                "curtain_style",
                DEFAULT_CURTAIN_STYLE,
            )
        )

        return normalized


__all__ = [
    "CURTAIN_STYLE_ALIASES",
    "CURTAIN_STYLE_COMPLEMENTARY",
    "CURTAIN_STYLE_COMPLEMENTARY_DARK",
    "CURTAIN_STYLE_COMPLEMENTARY_LIGHT",
    "CURTAIN_STYLE_DARK",
    "CURTAIN_STYLE_LABELS",
    "CURTAIN_STYLE_LIGHT",
    "CURTAIN_STYLE_NORMAL",
    "CURTAIN_STYLE_NORMAL_DARK",
    "CURTAIN_STYLE_NORMAL_LIGHT",
    "CURTAIN_STYLE_OPTIONS",
    "CURTAIN_STYLE_WHITE",
    "CURTAIN_TEXT_STYLE_PAIRS",
    "DEFAULT_CURTAIN_STYLE",
    "DEFAULT_SETTINGS",
    "DEFAULT_VISIONARY_URL",
    "ACTIVE_PLAY_DIR_KEY",
    "PUBLICATION_PROVIDER_KEY",
    "PUBLICATION_VERIFIED_KEY",
    "PUBLISHED_AT_KEY",
    "PUBLISHED_EXPIRES_AT_KEY",
    "PUBLISHED_GITHUB_OWNER_KEY",
    "PUBLISHED_GITHUB_REPOSITORY_KEY",
    "PUBLISHED_PAGE_URL_KEY",
    "PUBLISHED_PUBLIC_PATH_KEY",
    "PUBLISHED_SOURCE_FINGERPRINT_KEY",
    "PUBLICATION_SETTING_KEYS",
    "PROJECT_CREATED_AT_KEY",
    "PROJECT_PUBLISHED_AT_KEY",
    "REQUIRED_FEATURES_KEY",
    "VISIONARY_URL_KEY",
    "PROMPT_WRITER_PROVIDER_KEY",
    "SETTINGS_FILENAME",
    "SETTINGS_SCHEMA_KEY",
    "SETTINGS_SCHEMA_VERSION",
    "SettingsChanged",
    "SettingsStore",
    "UnsupportedSettingsSchemaError",
    "empty_publication_settings",
    "VALID_CURTAIN_STYLES",
    "normalize_curtain_style",
    "normalize_published_page_url",
]
