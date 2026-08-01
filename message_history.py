from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from transactional_io import atomic_write_text


REVISION_FOLDER_NAME = "revisions"
MAX_REVISIONS = 60


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
        current = path.read_text(encoding="utf-8")
    except OSError:
        return None

    if skip_if_content is not None and current == skip_if_content:
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
    previous = ""
    if path.is_file():
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError:
            previous = ""

    if previous == content:
        return False

    if previous:
        snapshot_current(path, reason=reason, skip_if_content=content)

    _atomic_write(path, content)
    return True


def restore_revision(message_path: str | Path, revision_path: str | Path) -> str:
    message = Path(message_path).resolve()
    revision = Path(revision_path).resolve()
    folder = revision_directory(message).resolve()

    if not revision.is_file() or revision.parent != folder:
        raise FileNotFoundError("The selected revision is not available.")

    content = revision.read_text(encoding="utf-8")
    snapshot_current(message, reason="before-restore", skip_if_content=content)
    _atomic_write(message, content)
    prune_revisions(message)
    return content


def delete_revision(revision_path: str | Path) -> None:
    Path(revision_path).unlink(missing_ok=True)
