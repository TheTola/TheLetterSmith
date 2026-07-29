from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from transactional_io import atomic_write_text


SETTINGS_FILENAME = "settings.json"

REQUIRED_FEATURES_KEY = "required_features"
PUBLISHED_PAGE_URL_KEY = "published_page_url"


DEFAULT_SETTINGS = {
    "starting_volume": 31,
    "last_audio": "music.mp3",
    "curtain_style": "pure_white",
    REQUIRED_FEATURES_KEY: [],
    PUBLISHED_PAGE_URL_KEY: "",
}


VALID_CURTAIN_STYLES = {
    "pure_white",
    "average_color",
    "complementary_average_color",
    "light",
    "dark",
}


CURTAIN_STYLE_LABELS = {
    "pure_white": "White Curtain",
    "average_color": "Normal Curtain",
    "complementary_average_color": (
        "Complementary Curtain"
    ),
    "light": "Light Curtain",
    "dark": "Dark Curtain",
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

    "light curtain": "light",
    "light_curtain": "light",
    "lighter": "light",

    "dark curtain": "dark",
    "dark_curtain": "dark",
    "darker": "dark",
}


def normalize_published_page_url(
    value: object,
) -> str:
    """
    Return a normalized HTTP or HTTPS address.

    Blank or invalid values return an empty string.
    """
    candidate = str(value or "").strip()

    if not candidate:
        return ""

    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""

    scheme = parsed.scheme.lower()

    if scheme not in {
        "http",
        "https",
    }:
        return ""

    if not parsed.netloc:
        return ""

    if not parsed.hostname:
        return ""

    try:
        port = parsed.port
    except ValueError:
        return ""

    hostname = parsed.hostname.lower()

    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    username = parsed.username
    password = parsed.password

    user_information = ""

    if username:
        user_information = username

        if password:
            user_information += f":{password}"

        user_information += "@"

    network_location = (
        f"{user_information}{hostname}"
    )

    if port is not None:
        network_location += f":{port}"

    return urlunsplit(
        (
            scheme,
            network_location,
            parsed.path or "",
            parsed.query,
            parsed.fragment,
        )
    )


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

        self.path = (
            self.project_root
            / SETTINGS_FILENAME
        )

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
    "DEFAULT_SETTINGS",
    "PUBLISHED_PAGE_URL_KEY",
    "REQUIRED_FEATURES_KEY",
    "SETTINGS_FILENAME",
    "SettingsChanged",
    "SettingsStore",
    "VALID_CURTAIN_STYLES",
    "normalize_published_page_url",
]