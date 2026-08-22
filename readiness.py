from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from config import MESSAGE_HTML_FILE, REQUIRED_SLIDES, USER_PAGES_DIR
from message_format import message_plain_text
from message_html import read_text_normalized
from project_paths import ProjectPathError, ProjectPathResolver
from publishing.expiration import publication_status
from settings_store import (
    REQUIRED_FEATURES_KEY,
    SettingsStore,
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


@dataclass(frozen=True)
class ProjectSaveEligibility:
    completed_tabs: tuple[str, ...]
    recipient_ready: bool
    title_ready: bool
    title_detail: str

    @property
    def can_save(self) -> bool:
        return (
            self.recipient_ready
            and self.title_ready
            and len(self.completed_tabs) >= 2
        )

    @property
    def blocked_reason(self) -> str:
        if not self.recipient_ready:
            return "Add the recipient before saving the project."
        if not self.title_ready:
            return self.title_detail
        return (
            "Complete at least two tabs before saving the project: "
            "Images, Sound, Message, or Prompt Writer."
        )


def _message_is_complete(root: Path) -> bool:
    message_path = root / MESSAGE_HTML_FILE
    try:
        return message_path.is_file() and bool(
            message_plain_text(read_text_normalized(message_path)).strip()
        )
    except (OSError, UnicodeError, ValueError):
        return False


def _sound_is_complete(root: Path) -> bool:
    try:
        return bool(resolve_project_tracks(root)[1])
    except (OSError, ValueError):
        return False


def _prompt_writer_is_complete(root: Path) -> bool:
    try:
        state = json.loads(
            (root / "prompt_writer_state.json").read_text(
                encoding="utf-8-sig"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    generated = (
        state.get("generated_prompts", {})
        if isinstance(state, dict)
        else {}
    )
    return isinstance(generated, dict) and any(
        str(prompt).strip() for prompt in generated.values()
    )


def _title_readiness(
    root: Path,
    settings: dict,
) -> tuple[bool, str]:
    title = str(settings.get("recipient_title", "")).strip()
    if not title:
        return False, "Add the letter title in Message."
    try:
        conflict = ProjectPathResolver(root).find_title_conflict(
            settings.get("recipient_id"),
            title,
            project_id=settings.get("project_id"),
        )
    except ProjectPathError:
        conflict = None
    if conflict is not None:
        return (
            False,
            "This recipient already has a letter with that title. "
            "Enter a different letter title.",
        )
    return True, "Letter title is ready."


def evaluate_project_save_eligibility(
    project_root: str | Path,
) -> ProjectSaveEligibility:
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    pages = root / USER_PAGES_DIR
    recipient_ready = bool(str(settings.get("recipient_name", "")).strip())
    title_ready, title_detail = _title_readiness(root, settings)
    completed_tabs: list[str] = []
    if all((pages / name).is_file() for name in REQUIRED_SLIDES):
        completed_tabs.append("images")
    if _sound_is_complete(root):
        completed_tabs.append("sound")
    if recipient_ready and title_ready:
        completed_tabs.append("message")
    if _prompt_writer_is_complete(root):
        completed_tabs.append("prompt_writer")
    return ProjectSaveEligibility(
        completed_tabs=tuple(completed_tabs),
        recipient_ready=recipient_ready,
        title_ready=title_ready,
        title_detail=title_detail,
    )


def evaluate_readiness(project_root: str | Path) -> ReadinessResult:
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    pages = root / USER_PAGES_DIR
    required_features = settings.get(REQUIRED_FEATURES_KEY, {})
    if isinstance(required_features, dict):
        music_required = bool(
            required_features.get(
                "music",
                settings.get("music_required", False),
            )
        )
    elif isinstance(required_features, (list, tuple, set)):
        music_required = "music" in {
            str(feature).strip().lower()
            for feature in required_features
        }
    else:
        music_required = bool(settings.get("music_required", False))
    has_music = _sound_is_complete(root)
    has_message = _message_is_complete(root)
    title_ready, title_detail = _title_readiness(root, settings)

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
            title_ready,
            True,
            title_detail,
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
            False,
            "Add a message only if this letter needs one.",
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
            "Published Letter Link",
            publication_status(settings) == "published",
            False,
            "Enter a valid Published Page URL in Message or publish the letter in Forge.",
            "forge",
            "publish",
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
