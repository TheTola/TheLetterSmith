from __future__ import annotations

import ipaddress
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from transactional_io import atomic_write_text
from project_paths import application_paths


SETTINGS_FILENAME = "settings.json"

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
DEFAULT_VISIONARY_URL = (
    "https://chatgpt.com/g/g-68ce5925196c8191a222e24d29323813-the-visionary"
)


DEFAULT_SETTINGS = {
    "starting_volume": 31,
    "last_audio": "music.mp3",
    "curtain_style": "pure_white",
    REQUIRED_FEATURES_KEY: [],
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
}


VALID_CURTAIN_STYLES = {
    "pure_white",
    "average_color",
    "complementary_average_color",
    "normal_light",
    "complementary_light",
    "normal_dark",
    "complementary_dark",
}


CURTAIN_STYLE_LABELS = {
    "pure_white": "White Curtain",
    "average_color": "Normal Curtain",
    "complementary_average_color": (
        "Complementary Curtain"
    ),
    "normal_light": "Normal Light",
    "complementary_light": "Complementary Light",
    "normal_dark": "Normal Dark",
    "complementary_dark": "Complementary Dark",
}


CURTAIN_TEXT_STYLE_PAIRS = {
    "average_color": "complementary_average_color",
    "complementary_average_color": "average_color",
    "normal_light": "complementary_dark",
    "complementary_light": "normal_dark",
    "normal_dark": "complementary_light",
    "complementary_dark": "normal_light",
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
    "complementary average": (
        "complementary_average_color"
    ),
    "complementary average color": (
        "complementary_average_color"
    ),

    "light": "normal_light",
    "light curtain": "normal_light",
    "light_curtain": "normal_light",
    "lighter": "normal_light",
    "normal light": "normal_light",
    "normal light curtain": "normal_light",
    "complementary light": "complementary_light",
    "complementary light curtain": "complementary_light",

    "dark": "normal_dark",
    "dark curtain": "normal_dark",
    "dark_curtain": "normal_dark",
    "darker": "normal_dark",
    "normal dark": "normal_dark",
    "normal dark curtain": "normal_dark",
    "complementary dark": "complementary_dark",
    "complementary dark curtain": "complementary_dark",
}


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

        self.changed = SettingsChanged()

        self._settings: dict[
            str,
            Any,
        ] = {}

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
            raw, invalid = (
                self._read_unlocked()
            )

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
            current, _invalid = (
                self._read_unlocked()
            )

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
            before, _invalid = (
                self._read_unlocked()
            )

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

        # Curtain style
        style = str(
            normalized.get(
                "curtain_style",
                DEFAULT_SETTINGS[
                    "curtain_style"
                ],
            )
        ).strip().lower()

        style = CURTAIN_STYLE_ALIASES.get(
            style,
            style.replace(
                " ",
                "_",
            ),
        )

        if (
            style
            not in VALID_CURTAIN_STYLES
        ):
            style = str(
                DEFAULT_SETTINGS[
                    "curtain_style"
                ]
            )

        normalized[
            "curtain_style"
        ] = style

        return normalized


__all__ = [
    "CURTAIN_STYLE_ALIASES",
    "CURTAIN_STYLE_LABELS",
    "CURTAIN_TEXT_STYLE_PAIRS",
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
    "REQUIRED_FEATURES_KEY",
    "VISIONARY_URL_KEY",
    "SETTINGS_FILENAME",
    "SettingsChanged",
    "SettingsStore",
    "VALID_CURTAIN_STYLES",
    "normalize_published_page_url",
]
