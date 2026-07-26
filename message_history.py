from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import MESSAGE_HTML_FILE, USER_MESSAGE_DIR
from message_format import message_plain_text
from transactional_io import atomic_write_text


MAX_MESSAGE_REVISIONS = 20
EXTENDED_EDIT_INTERVAL_SECONDS = 5 * 60
HISTORY_RELATIVE_PATH = Path(USER_MESSAGE_DIR) / "history"


@dataclass(frozen=True)
class MessageRevision:
    path: Path
    timestamp: datetime
    preview: str


class MessageHistory:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.message_path = self.project_root / MESSAGE_HTML_FILE
        self.history_dir = self.project_root / HISTORY_RELATIVE_PATH

    def list_revisions(self) -> tuple[MessageRevision, ...]:
        if not self.history_dir.is_dir():
            return ()
        revisions: list[MessageRevision] = []
        for path in self.history_dir.glob("*.html"):
            try:
                html = path.read_text(encoding="utf-8")
                timestamp = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
            plain = " ".join(message_plain_text(html).split())
            preview = plain[:120] + ("…" if len(plain) > 120 else "")
            revisions.append(MessageRevision(path.resolve(), timestamp, preview or "(Blank message)"))
        revisions.sort(key=lambda revision: (revision.timestamp, revision.path.name), reverse=True)
        return tuple(revisions)

    def create_revision(self, html: str, *, force: bool = False) -> Optional[Path]:
        value = html or ""
        revisions = self.list_revisions()
        if not force and revisions:
            try:
                if revisions[0].path.read_text(encoding="utf-8") == value:
                    return None
            except OSError:
                pass

        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.history_dir / f"{stamp}-{time.time_ns()}-{digest}.html"
        atomic_write_text(path, value)
        self._prune()
        return path.resolve()

    def snapshot_current_if_changed(self) -> Optional[Path]:
        if not self.message_path.is_file():
            return None
        try:
            html = self.message_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return self.create_revision(html)

    def maybe_create_timed_revision(
        self,
        html: str,
        *,
        now: Optional[float] = None,
    ) -> Optional[Path]:
        timestamp = time.time() if now is None else float(now)
        revisions = self.list_revisions()
        if revisions and timestamp - revisions[0].timestamp.timestamp() < EXTENDED_EDIT_INTERVAL_SECONDS:
            return None
        return self.create_revision(html)

    def restore(self, revision_path: str | Path) -> str:
        requested = Path(revision_path).resolve()
        allowed = {revision.path for revision in self.list_revisions()}
        if requested not in allowed:
            raise ValueError("Revision is outside the active message history.")

        replacement = requested.read_text(encoding="utf-8")
        if self.message_path.is_file():
            current = self.message_path.read_text(encoding="utf-8")
            self.create_revision(current, force=True)
        atomic_write_text(self.message_path, replacement)
        return replacement

    def copy_revision_to_message_directory(
        self,
        revision_path: str | Path,
        message_directory: str | Path,
    ) -> Path:
        source = Path(revision_path).resolve()
        allowed = {revision.path for revision in self.list_revisions()}
        if source not in allowed:
            raise ValueError("Revision is outside the active message history.")
        destination_dir = Path(message_directory) / "history"
        destination = destination_dir / source.name
        atomic_write_text(destination, source.read_text(encoding="utf-8"))
        self._prune_directory(destination_dir)
        return destination

    def _prune(self) -> None:
        self._prune_directory(self.history_dir)

    @staticmethod
    def _prune_directory(directory: Path) -> None:
        if not directory.is_dir():
            return
        paths = sorted(
            directory.glob("*.html"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        for stale in paths[MAX_MESSAGE_REVISIONS:]:
            stale.unlink(missing_ok=True)
