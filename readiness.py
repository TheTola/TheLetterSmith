from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import MESSAGE_HTML_FILE, USER_PAGES_DIR
from message_format import message_plain_text
from message_html import read_text_normalized
from settings_store import (
    REQUIRED_FEATURES_KEY,
    SettingsStore,
    normalize_published_page_url,
)
from sound_model import resolve_project_tracks


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    required: bool
    detail: str
    correction_tab: str
    correction_target: str


@dataclass(frozen=True)
class ReadinessResult:
    items: tuple[ReadinessItem, ...]
    completion_percentage: int
    status: str

    @property
    def missing_items(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if not item.ready)

    @property
    def can_preview(self) -> bool:
        return all(item.ready for item in self.items if item.required)

    @property
    def can_publish(self) -> bool:
        return self.can_preview


def evaluate_readiness(project_root: str | Path) -> ReadinessResult:
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    pages = root / USER_PAGES_DIR
    message_path = root / MESSAGE_HTML_FILE
    required_features = settings.get(REQUIRED_FEATURES_KEY, {})
    required_mapping = (
        required_features if isinstance(required_features, dict) else {}
    )
    music_required = bool(
        required_mapping.get("music", settings.get("music_required", False))
    )
    try:
        has_music = bool(resolve_project_tracks(root)[1])
    except (OSError, ValueError):
        has_music = False

    try:
        has_message = message_path.is_file() and bool(
            message_plain_text(read_text_normalized(message_path)).strip()
        )
    except (OSError, UnicodeError, ValueError):
        has_message = False

    definitions = (
        (
            "recipient",
            "Recipient",
            bool(str(settings.get("recipient_name", "")).strip()),
            True,
            "Add the recipient in Message.",
            "message",
            "recipient",
        ),
        (
            "title",
            "Letter Title",
            bool(str(settings.get("recipient_title", "")).strip()),
            True,
            "Add the letter title in Message.",
            "message",
            "title",
        ),
        (
            "cover",
            "Cover Image",
            (pages / "cover.png").is_file(),
            True,
            "Choose the cover image.",
            "images",
            "cover",
        ),
        (
            "letter",
            "Main Letter Image",
            (pages / "letter.png").is_file(),
            True,
            "Choose the main letter image.",
            "images",
            "letter",
        ),
        (
            "wall",
            "Letter Background",
            (pages / "wall.png").is_file(),
            True,
            "Choose the letter background.",
            "images",
            "wall",
        ),
        (
            "back",
            "Final Backdrop",
            (pages / "back.png").is_file(),
            True,
            "Choose the final backdrop.",
            "images",
            "back",
        ),
        (
            "message",
            "Message",
            has_message,
            True,
            "Add or edit the message.",
            "message",
            "message",
        ),
        (
            "music",
            "Music",
            has_music,
            music_required,
            "Choose music in Sound.",
            "sound",
            "music",
        ),
        (
            "published_url",
            "Published Page URL",
            bool(
                normalize_published_page_url(
                    settings.get("published_page_url", "")
                )
            ),
            False,
            "Publish the letter or save its public URL.",
            "message",
            "published_url",
        ),
    )
    items = tuple(ReadinessItem(*definition) for definition in definitions)
    completed = sum(item.ready for item in items)
    percentage = round((completed / len(items)) * 100)
    required_missing = any(not item.ready and item.required for item in items)
    optional_missing = {
        item.key for item in items if not item.ready and not item.required
    }
    if required_missing:
        status = "Not Ready"
    elif not optional_missing:
        status = "Ready"
    elif optional_missing == {"music"}:
        status = "Ready \u2014 Missing Music"
    else:
        status = "Ready \u2014 Missing Optional Features"
    return ReadinessResult(items, percentage, status)
