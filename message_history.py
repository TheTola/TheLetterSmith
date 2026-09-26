from __future__ import annotations

import re
from difflib import SequenceMatcher
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from message_html import extract_lettersmith_style_state, sanitize_message_html
from named_text_styles import NamedStyleSet, InvalidStyleSetError
from transactional_io import atomic_write_text


REVISION_FOLDER_NAME = "revisions"
MAX_REVISIONS = 60
SIGNIFICANT_MESSAGE_CHANGES = 15


class _MessageContent(HTMLParser):
    """Compare visible words and their formatting, not Qt's HTML serialization."""

    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, tuple]] = []
        self.stack: list[tuple[str, dict]] = [("", {})]
        self.feed(sanitize_message_html(content))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = dict(self.stack[-1][1])
        for declaration in attrs.get("style", "").split(";"):
            name, sep, value = declaration.partition(":")
            name, value = name.strip().lower(), value.strip()
            if sep and not name.startswith("-qt-"):
                style[name] = re.sub(r"(?<=\d)\.0+(?=\D|$)", "", value)
        implied = {
            "b": ("font-weight", "700"), "strong": ("font-weight", "700"),
            "i": ("font-style", "italic"), "em": ("font-style", "italic"),
            "u": ("text-decoration", "underline"), "s": ("text-decoration", "line-through"),
        }.get(tag)
        if implied:
            style[implied[0]] = implied[1]
        if tag == "a" and attrs.get("href"):
            style["href"] = attrs["href"]
        for name in ("align", "dir", "start", "type", "rowspan", "colspan", "width", "height"):
            if attrs.get(name) is not None:
                style[name] = attrs[name]
        if tag in ("ol", "ul"):
            style["list"] = tag
        if tag in ("p", "div", "li", "br"):
            self.tokens.append(("\n", ()))
        if tag == "img":
            self.tokens.append(("image", tuple(sorted(attrs.items()))))
        if tag not in ("br", "img", "hr", "meta", "link", "input"):
            self.stack.append((tag, style))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(tag in ("head", "style", "title", "script") for tag, _style in self.stack):
            return
        style = tuple(sorted(self.stack[-1][1].items()))
        self.tokens.extend((word, style) for word in re.findall(r"\w+|[^\w\s]", data))


def _style_state(content: str):
    state = extract_lettersmith_style_state(content)
    if state is None:
        return None
    definitions = state.get("definitions")
    try:
        definitions = NamedStyleSet.from_dict(definitions).to_dict()
    except (InvalidStyleSetError, TypeError, ValueError):
        pass
    return definitions, state.get("blocks")


def message_change_count(before: str, after: str) -> int:
    """Count net word/format changes; reverted edits do not count as activity."""
    left, right = _MessageContent(before).tokens, _MessageContent(after).tokens
    count = sum(
        max(i2 - i1, j2 - j1)
        for kind, i1, i2, j1, j2 in SequenceMatcher(None, left, right).get_opcodes()
        if kind != "equal"
    )
    old_state, new_state = _style_state(before), _style_state(after)
    if old_state is not None and (new_state is None or old_state[0] != new_state[0]):
        # Updating a reusable style is an intentional formatting change even
        # when no paragraph currently uses it. Adding the first metadata to a
        # legacy letter is serialization, not a style-definition edit.
        count = max(count, SIGNIFICANT_MESSAGE_CHANGES + 1)
    return count


def messages_equal(before: str, after: str) -> bool:
    return before == after or (
        _style_state(before) == _style_state(after)
        and message_change_count(before, after) == 0
    )


@dataclass(frozen=True)
class MessageRevision:
    path: Path
    created_at: datetime
    reason: str
    size_bytes: int

    @property
    def display_name(self) -> str:
        stamp = self.created_at.strftime("%b %d, %Y  %I:%M:%S %p")
        reason = self.reason.replace("_", " ").strip().title()
        return f"{stamp}  —  {reason}" if reason else stamp


def revision_directory(message_path: str | Path) -> Path:
    return Path(message_path).resolve().parent / REVISION_FOLDER_NAME


def _atomic_write(path: Path, content: str) -> None:
    """Write a revision atomically with flush, fsync, and temp cleanup."""
    atomic_write_text(path, content)


def _revision_filename(reason: str) -> str:
    safe_reason = re.sub(r"[^a-z0-9_-]+", "-", (reason or "revision").strip().lower()).strip("-")
    safe_reason = safe_reason or "revision"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{stamp}__{safe_reason}.html"


def _parse_revision(path: Path) -> MessageRevision:
    stem = path.stem
    stamp_text, _, reason = stem.partition("__")
    try:
        created = datetime.strptime(stamp_text, "%Y%m%d-%H%M%S-%f")
    except ValueError:
        created = datetime.fromtimestamp(path.stat().st_mtime)
    try:
        size = int(path.stat().st_size)
    except OSError:
        size = 0
    return MessageRevision(path=path, created_at=created, reason=reason or "revision", size_bytes=size)


def list_revisions(message_path: str | Path) -> list[MessageRevision]:
    folder = revision_directory(message_path)
    if not folder.is_dir():
        return []
    revisions = [_parse_revision(path) for path in folder.glob("*.html") if path.is_file()]
    revisions.sort(key=lambda item: item.created_at, reverse=True)
    return revisions


def prune_revisions(message_path: str | Path, *, keep: int = MAX_REVISIONS) -> None:
    keep = max(1, int(keep))
    for revision in list_revisions(message_path)[keep:]:
        try:
            revision.path.unlink(missing_ok=True)
        except OSError:
            pass


def snapshot_current(
    message_path: str | Path,
    *,
    reason: str = "revision",
    skip_if_content: Optional[str] = None,
) -> Optional[Path]:
    path = Path(message_path).resolve()
    if not path.is_file():
        return None

    try:
        current = sanitize_message_html(path.read_text(encoding="utf-8"))
    except OSError:
        return None

    if skip_if_content is not None and messages_equal(current, skip_if_content):
        return None

    return _snapshot_content(path, current, reason)


def _snapshot_content(path: Path, current: str, reason: str) -> Optional[Path]:
    for revision in list_revisions(path):
        if messages_equal(current, revision.path.read_text(encoding="utf-8")):
            return None

    folder = revision_directory(path)
    folder.mkdir(parents=True, exist_ok=True)
    revision_path = folder / _revision_filename(reason)
    _atomic_write(revision_path, current)
    prune_revisions(path)
    return revision_path


def write_message_with_revision(
    message_path: str | Path,
    content: str,
    *,
    reason: str = "autosave",
) -> bool:
    path = Path(message_path).resolve()
    content = sanitize_message_html(content)
    previous = ""
    if path.is_file():
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError:
            previous = ""

    if previous == content:
        return False

    # Keep a checkpoint apart from the live file so small manual saves can
    # accumulate into one meaningful revision, without losing the saved edits.
    baseline_path = revision_directory(path) / ".baseline"
    baseline = baseline_path.read_text(encoding="utf-8") if baseline_path.is_file() else previous
    significant = bool(baseline) and message_change_count(baseline, content) > SIGNIFICANT_MESSAGE_CHANGES
    if significant:
        _snapshot_content(path, baseline, reason)

    _atomic_write(path, content)
    if significant or not baseline_path.is_file():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(baseline_path, content if significant or not baseline else baseline)
    return True


def restore_revision(message_path: str | Path, revision_path: str | Path) -> str:
    message = Path(message_path).resolve()
    revision = Path(revision_path).resolve()
    folder = revision_directory(message).resolve()

    if not revision.is_file() or revision.parent != folder:
        raise FileNotFoundError("The selected revision is not available.")

    content = sanitize_message_html(revision.read_text(encoding="utf-8"))
    snapshot_current(message, reason="before-restore", skip_if_content=content)
    _atomic_write(message, content)
    _atomic_write(folder / ".baseline", content)
    prune_revisions(message)
    return content


def delete_revision(revision_path: str | Path) -> None:
    Path(revision_path).unlink(missing_ok=True)
