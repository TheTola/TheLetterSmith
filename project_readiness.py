from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import (
    CONTROL_FILES,
    FLIP_COUNT,
    GLISS_FILE,
    MESSAGE_HTML_FILE,
    MUSIC_FILE,
    USER_CONTROLS_DIR,
    USER_PAGES_DIR,
    USER_SOUNDS_DIR,
)
from message_html import message_html_has_content, read_text_normalized
from settings_store import SettingsStore


APP_SOUNDS_DIR = Path("gallery") / "app" / "sounds"


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    required: bool
    detail: str


def _sfx_names() -> tuple[str, ...]:
    return (GLISS_FILE, *(f"flip{i}.mp3" for i in range(1, FLIP_COUNT + 1)))


def assess_project_readiness(project_root: str | Path) -> tuple[ReadinessItem, ...]:
    root = Path(project_root)
    pages = root / USER_PAGES_DIR
    sounds = root / USER_SOUNDS_DIR
    settings = SettingsStore(root).as_dict()

    image_items = (
        ("cover", "Cover image", "cover.png"),
        ("letter", "Main letter image", "letter.png"),
        ("wall", "Message background", "wall.png"),
        ("back", "Final backdrop", "back.png"),
    )
    items = [
        ReadinessItem(key, label, (pages / name).is_file(), True, str(pages / name))
        for key, label, name in image_items
    ]

    message_path = root / MESSAGE_HTML_FILE
    try:
        has_message = message_path.is_file() and message_html_has_content(
            read_text_normalized(message_path)
        )
    except Exception:
        has_message = False
    items.append(ReadinessItem("message", "Message", has_message, True, str(message_path)))

    recipient = str(settings.get("recipient_name", "")).strip()
    title = str(settings.get("recipient_title", "")).strip()
    items.extend(
        (
            ReadinessItem("recipient", "Recipient", bool(recipient), True, recipient or "Not set"),
            ReadinessItem("title", "Title", bool(title), True, title or "Not set"),
            ReadinessItem(
                "music",
                "Music",
                (sounds / MUSIC_FILE).is_file(),
                False,
                str(sounds / MUSIC_FILE),
            ),
        )
    )

    controls_dir = root / USER_CONTROLS_DIR
    missing_controls = tuple(name for name in CONTROL_FILES if not (controls_dir / name).is_file())
    items.append(
        ReadinessItem(
            "controls",
            "Viewer controls",
            not missing_controls,
            True,
            "Complete" if not missing_controls else "Missing: " + ", ".join(missing_controls),
        )
    )

    app_sounds = root / APP_SOUNDS_DIR
    missing_sfx = tuple(
        name
        for name in _sfx_names()
        if not (app_sounds / name).is_file() and not (sounds / name).is_file()
    )
    items.append(
        ReadinessItem(
            "sfx",
            "Required sound effects",
            not missing_sfx,
            True,
            "Complete" if not missing_sfx else "Missing: " + ", ".join(missing_sfx),
        )
    )
    return tuple(items)


def project_is_ready(items: tuple[ReadinessItem, ...]) -> bool:
    return all(item.ready for item in items if item.required)
