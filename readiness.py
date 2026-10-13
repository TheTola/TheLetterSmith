from __future__ import annotations

from page_layout import load_pages, page_files, page_labels

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from config import (
    MESSAGE_HTML_FILE,
    USER_PAGES_DIR,
)
from message_format import message_plain_text
from message_html import read_text_normalized
from project_paths import ProjectPathError, ProjectPathResolver
from protected_projects import (
    is_protected_project,
    reserved_save_reason,
)
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
    preview_required: bool | None = None


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
        return all(
            item.ready
            for item in self.items
            if (item.required if item.preview_required is None else item.preview_required)
        )

    @property
    def can_publish(self) -> bool:
        return all(item.ready for item in self.items if item.required)


def music_is_required(settings: Mapping[str, object]) -> bool:
    """Return the canonical music-readiness requirement for a settings snapshot."""
    required_features = settings.get(REQUIRED_FEATURES_KEY, {})
    if isinstance(required_features, Mapping):
        return bool(
            required_features.get(
                "music",
                settings.get("music_required", False),
            )
        )
    if isinstance(required_features, (list, tuple, set)):
        return "music" in {
            str(feature).strip().lower()
            for feature in required_features
        }
    return bool(settings.get("music_required", False))


@dataclass(frozen=True)
class ProjectSaveEligibility:
    completed_tabs: tuple[str, ...]
    recipient_ready: bool
    title_ready: bool
    title_detail: str
    persistent_block_reason: str = ""

    @property
    def can_save(self) -> bool:
        return (
            not self.persistent_block_reason
            and self.recipient_ready
            and self.title_ready
            and len(self.completed_tabs) >= 2
        )

    @property
    def blocked_reason(self) -> str:
        if self.persistent_block_reason:
            return self.persistent_block_reason
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
    persistent_block_reason = reserved_save_reason(
        settings.get("recipient_title", ""),
        settings.get("recipient_name", ""),
    )
    if is_protected_project(settings):
        persistent_block_reason = (
            "Stock and Example Letters are temporary demonstrations and "
            "cannot replace their bundled originals."
        )
    completed_tabs: list[str] = []
    if all(
        (pages / name).is_file()
        for name in page_files(pages)
    ):
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
        persistent_block_reason=persistent_block_reason,
    )


def _readiness_result(items: tuple[ReadinessItem, ...]) -> ReadinessResult:
    completed = sum(item.ready for item in items)
    percentage = round((completed / len(items)) * 100) if items else 100
    result = ReadinessResult(items, percentage, "")
    optional_missing = {
        item.key for item in items if not item.ready and not item.required
    }
    if not result.can_publish:
        status = "Preview Only — Review Required" if result.can_preview else "Not Ready"
    elif not optional_missing:
        status = "Ready"
    elif optional_missing == {"music"}:
        status = "Ready — Missing Music"
    else:
        status = "Ready — Missing Optional Features"
    return ReadinessResult(items, percentage, status)


def _canonical_readiness(root: Path, session, settings: dict) -> ReadinessResult:
    from forge_render import capture_render, render_has_music, validate_render
    from project_model import PAGE_TYPES

    if session.is_restoring:
        return _readiness_result((ReadinessItem(
            "project_restoring", "Project", False, True,
            "Wait for the project to finish opening.", "forge", "pages",
        ),))
    render = capture_render(session, output_mode="preview")
    snapshot = render.snapshot
    # Canonical authored values override compatibility settings. The application
    # store still supplies preferences which are deliberately not project data.
    settings = {
        **settings,
        **snapshot["shared_settings"],
        **snapshot["metadata"],
        **snapshot["publication"],
        "project_id": snapshot["project_id"],
    }
    title_ready, title_detail = _title_readiness(root, settings)
    items = [
        ReadinessItem(
            "recipient", "Recipient",
            bool(str(settings.get("recipient_name", "")).strip()), True,
            "Add the recipient in Message.", "message", "recipient",
        ),
        ReadinessItem(
            "title", "Letter Title", title_ready, True, title_detail,
            "message", "title",
        ),
    ]
    issues = list(validate_render(render))
    remaining = list(issues)
    for position, page in enumerate(snapshot["pages"], 1):
        page_id = page["id"]
        name = page["name"].strip() or PAGE_TYPES[page["type"]].label
        label = f"Page {position} — {name}"
        features = [("images", "artwork", "Artwork", "images")]
        if PAGE_TYPES[page["type"]].supports_message:
            features.append(("messages", "message", "Message", "message"))
        for feature, key, field_label, tab in features:
            problems = [issue for issue in remaining
                        if issue.page_id == page_id and issue.feature == feature]
            remaining = [issue for issue in remaining if issue not in problems]
            items.append(ReadinessItem(
                f"page:{page_id}:{key}", f"{label}: {field_label}",
                not problems,
                any(issue.blocks_output for issue in problems) if problems else True,
                " ".join(issue.message for issue in problems)
                if problems else f"{field_label} is ready.",
                tab, page_id,
                any(issue.blocks_preview for issue in problems) if problems else True,
            ))
    sound_issues = [issue for issue in remaining if issue.feature == "sound"]
    remaining = [issue for issue in remaining if issue not in sound_issues]
    music_required = music_is_required(settings)
    items.append(ReadinessItem(
        "music", "Music", render_has_music(render) and not sound_issues,
        music_required or any(issue.blocks_output for issue in sound_issues),
        " ".join(issue.message for issue in sound_issues) or "Choose music in Sound.",
        "sound", "music",
        music_required or any(issue.blocks_preview for issue in sound_issues),
    ))
    pages = {page["id"]: (position, page)
             for position, page in enumerate(snapshot["pages"], 1)}
    for index, issue in enumerate(remaining):
        label = "Presentation"
        if issue.page_id in pages:
            position, page = pages[issue.page_id]
            name = page["name"].strip() or PAGE_TYPES[page["type"]].label
            label = f"Page {position} — {name}"
        items.append(ReadinessItem(
            f"render:{issue.page_id or 'project'}:{issue.code}:{index}",
            label, False, issue.blocks_output, issue.message,
            "message" if issue.feature == "messages" else issue.feature,
            issue.page_id or "pages", issue.blocks_preview,
        ))
    items.append(ReadinessItem(
        "published_url", "Published Letter Link",
        publication_status(settings) == "published", False,
        "Enter a valid Published Page URL in Message or publish the letter in Forge.",
        "forge", "publish",
    ))
    return _readiness_result(tuple(items))


def evaluate_readiness(project_root: str | Path) -> ReadinessResult:
    root = Path(project_root).resolve()
    from project_consumers import session_for
    session = session_for(root)
    if session is not None and not session.is_restoring:
        session.synchronize_workspace()
    settings = SettingsStore(root).snapshot()
    if is_protected_project(settings):
        return ReadinessResult((), 0, "")
    if session is not None:
        return _canonical_readiness(root, session, settings)
    pages = root / USER_PAGES_DIR
    music_required = music_is_required(settings)
    has_music = _sound_is_complete(root)
    has_message = _message_is_complete(root)
    title_ready, title_detail = _title_readiness(root, settings)
    layout = load_pages(pages)
    labels = page_labels(layout)

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
        *(
            (
                page.key, labels[page.key] + " Image",
                (pages / page.filename).is_file(), True,
                f"Choose the {labels[page.key]} image.",
                "images", page.key,
            )
            for page in layout
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
    return _readiness_result(items)
