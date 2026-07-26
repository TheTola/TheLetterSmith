from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import MESSAGE_HTML_FILE, USER_PAGES_DIR
from message_html import message_html_has_content, read_text_normalized
from playlist import PlaylistStore
from settings_store import SettingsStore


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    required: bool
    detail: str


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


def evaluate_readiness(project_root: str | Path) -> ReadinessResult:
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    pages = root / USER_PAGES_DIR
    message_path = root / MESSAGE_HTML_FILE

    try:
        has_message = message_path.is_file() and message_html_has_content(
            read_text_normalized(message_path)
        )
    except OSError:
        has_message = False

    definitions = (
        ("recipient", "Recipient", bool(str(settings.get("recipient_name", "")).strip()), True, "Message"),
        ("title", "Letter Title", bool(str(settings.get("recipient_title", "")).strip()), True, "Message"),
        ("cover", "Cover Image", (pages / "cover.png").is_file(), True, str(pages / "cover.png")),
        ("letter", "Main Letter Image", (pages / "letter.png").is_file(), True, str(pages / "letter.png")),
        ("wall", "Letter Background", (pages / "wall.png").is_file(), True, str(pages / "wall.png")),
        ("back", "Final Backdrop", (pages / "back.png").is_file(), True, str(pages / "back.png")),
        ("message", "Message", has_message, True, str(message_path)),
        ("music", "Music", bool(PlaylistStore(root).load().tracks), False, "Sound"),
    )
    items = tuple(ReadinessItem(*definition) for definition in definitions)
    completed = sum(item.ready for item in items)
    percentage = round((completed / len(items)) * 100)
    required_missing = any(not item.ready and item.required for item in items)
    optional_missing = any(not item.ready and not item.required for item in items)
    status = (
        "Not Ready"
        if required_missing
        else "Ready — Missing Music"
        if optional_missing
        else "Ready"
    )
    return ReadinessResult(items, percentage, status)
