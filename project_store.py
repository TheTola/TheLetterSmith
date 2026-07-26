from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import MESSAGE_HTML_FILE, MUSIC_FILE, USER_MESSAGE_DIR, USER_PAGES_DIR, USER_SOUNDS_DIR
from settings_store import SettingsStore
from transactional_io import PathTransaction


PROJECTS_DIR = "projects"
PROJECT_MANIFEST = "project.json"
ACTIVE_PROJECT_KEY = "active_project"
RECENT_PROJECTS_KEY = "recent_projects"
MAX_RECENT_PROJECTS = 8
MAX_MESSAGE_REVISIONS = 20


def _safe_project_id(name: str) -> str:
    value = "".join(char.lower() if char.isalnum() else "-" for char in (name or "").strip())
    value = "-".join(part for part in value.split("-") if part)
    return value or "untitled-project"


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_directory(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    name: str
    path: Path
    updated_at: str


class ProjectStore:
    """Durable projects backed by the existing canonical workspace."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.projects_dir = self.project_root / PROJECTS_DIR
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.settings = SettingsStore(self.project_root)

    @property
    def active_project_id(self) -> str:
        value = str(self.settings.get(ACTIVE_PROJECT_KEY, "")).strip()
        path = self.projects_dir / value
        return value if value and (path / PROJECT_MANIFEST).is_file() else ""

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        projects: list[ProjectSummary] = []
        for path in self.projects_dir.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest = self._read_manifest(path)
            if not manifest:
                continue
            projects.append(
                ProjectSummary(
                    project_id=path.name,
                    name=str(manifest.get("name") or path.name),
                    path=path,
                    updated_at=str(manifest.get("updated_at") or ""),
                )
            )
        projects.sort(key=lambda project: (project.updated_at, project.name.casefold()), reverse=True)
        return tuple(projects)

    def recent_projects(self) -> tuple[ProjectSummary, ...]:
        projects = {project.project_id: project for project in self.list_projects()}
        configured = self.settings.get(RECENT_PROJECTS_KEY, [])
        order = configured if isinstance(configured, list) else []
        recent = [projects[project_id] for project_id in order if project_id in projects]
        recent.extend(project for project in projects.values() if project not in recent)
        return tuple(recent[:MAX_RECENT_PROJECTS])

    def create(self, name: str, *, from_workspace: bool = False) -> ProjectSummary:
        clean_name = (name or "").strip() or "Untitled Project"
        project_id = self._available_project_id(_safe_project_id(clean_name))
        if from_workspace:
            return self.save_as(clean_name, project_id=project_id, activate=True)

        project_dir = self.projects_dir / project_id
        tx = PathTransaction(project_dir)
        staging = tx.prepare()
        try:
            for child in ("pages", "message", "sounds", "revisions/message", "autosave"):
                (staging / child).mkdir(parents=True, exist_ok=True)
            self._write_manifest(
                staging,
                {
                    "id": project_id,
                    "name": clean_name,
                    "created_at": self._timestamp(),
                    "updated_at": self._timestamp(),
                    "settings": {
                        "recipient_name": "",
                        "recipient_title": "",
                        "published_page_url": "",
                    },
                },
            )
            tx.commit()
        except Exception:
            tx.abort()
            raise
        self.open(project_id)
        return self._summary(project_id)

    def save_as(
        self,
        name: str,
        *,
        project_id: Optional[str] = None,
        activate: bool = True,
    ) -> ProjectSummary:
        clean_name = (name or "").strip() or "Untitled Project"
        target_id = project_id or self._available_project_id(_safe_project_id(clean_name))
        target = self.projects_dir / target_id
        if target.exists():
            raise FileExistsError(f"Project already exists: {target_id}")
        self._write_workspace_snapshot(target, clean_name, target_id, existing=None)
        if activate:
            self._set_active(target_id)
        return self._summary(target_id)

    def save_active(self) -> Optional[ProjectSummary]:
        project_id = self.active_project_id
        if not project_id:
            return None
        current = self.projects_dir / project_id
        manifest = self._read_manifest(current)
        name = str(manifest.get("name") or project_id)
        self._write_workspace_snapshot(current, name, project_id, existing=current)
        self._touch_recent(project_id)
        return self._summary(project_id)

    def duplicate_active(self, name: str) -> ProjectSummary:
        if not self.active_project_id:
            return self.save_as(name, activate=True)
        self.save_active()
        return self.save_as(name, activate=True)

    def open(self, project_id: str) -> ProjectSummary:
        source = (self.projects_dir / project_id).resolve()
        if source.parent != self.projects_dir.resolve() or not (source / PROJECT_MANIFEST).is_file():
            raise FileNotFoundError(f"Project does not exist: {project_id}")

        manifest = self._read_manifest(source)
        pages_tx = PathTransaction(self.project_root / USER_PAGES_DIR, staging_suffix=".project-staging")
        message_tx = PathTransaction(self.project_root / USER_MESSAGE_DIR, staging_suffix=".project-staging")
        music_tx = PathTransaction(
            self.project_root / USER_SOUNDS_DIR / MUSIC_FILE,
            staging_suffix=".project-staging",
        )
        transactions = (pages_tx, message_tx, music_tx)
        committed: list[PathTransaction] = []
        try:
            _copy_directory(source / "pages", pages_tx.prepare())
            _copy_directory(source / "message", message_tx.prepare())
            music_staging = music_tx.prepare()
            source_music = source / "sounds" / MUSIC_FILE
            if source_music.is_file():
                music_staging.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_music, music_staging)

            pages_tx.commit(keep_backup=True)
            committed.append(pages_tx)
            message_tx.commit(keep_backup=True)
            committed.append(message_tx)
            music_tx.commit(replace=source_music.is_file(), keep_backup=True)
            committed.append(music_tx)

            project_settings = manifest.get("settings", {})
            updates = dict(project_settings) if isinstance(project_settings, dict) else {}
            updates[ACTIVE_PROJECT_KEY] = project_id
            updates[RECENT_PROJECTS_KEY] = self._recent_ids(project_id)
            self.settings.update_fields(updates)
        except Exception:
            for tx in reversed(committed):
                tx.rollback()
            for tx in transactions:
                tx.abort()
            raise
        for tx in transactions:
            tx.finalize()
        return self._summary(project_id)

    def autosave_message(self, html: str) -> Path:
        project_id = self.active_project_id
        if project_id:
            path = self.projects_dir / project_id / "autosave" / "message.html"
        else:
            path = self.projects_dir / ".recovery" / "message.html"
        _atomic_write_text(path, html)
        return path

    def clear_message_autosave(self) -> None:
        project_id = self.active_project_id
        path = (
            self.projects_dir / project_id / "autosave" / "message.html"
            if project_id
            else self.projects_dir / ".recovery" / "message.html"
        )
        path.unlink(missing_ok=True)

    def recoverable_message_autosave(self) -> Optional[Path]:
        project_id = self.active_project_id
        path = (
            self.projects_dir / project_id / "autosave" / "message.html"
            if project_id
            else self.projects_dir / ".recovery" / "message.html"
        )
        message_path = self.project_root / MESSAGE_HTML_FILE
        if not path.is_file():
            return None
        if message_path.is_file() and path.stat().st_mtime <= message_path.stat().st_mtime:
            return None
        return path

    def list_message_revisions(self, project_id: Optional[str] = None) -> tuple[Path, ...]:
        selected = project_id or self.active_project_id
        if not selected:
            return ()
        revisions = self.projects_dir / selected / "revisions" / "message"
        if not revisions.is_dir():
            return ()
        return tuple(sorted(revisions.glob("*.html"), key=lambda path: path.name, reverse=True))

    def restore_message_revision(self, revision: Path) -> str:
        revisions = self.list_message_revisions()
        resolved = revision.resolve()
        if resolved not in {path.resolve() for path in revisions}:
            raise ValueError("Revision is outside the active project.")
        html = resolved.read_text(encoding="utf-8")
        _atomic_write_text(self.project_root / MESSAGE_HTML_FILE, html)
        self.save_active()
        return html

    def _write_workspace_snapshot(
        self,
        target: Path,
        name: str,
        project_id: str,
        *,
        existing: Optional[Path],
    ) -> None:
        tx = PathTransaction(target)
        staging = tx.prepare()
        try:
            if existing is not None and existing.is_dir():
                shutil.copytree(existing, staging)
            else:
                staging.mkdir(parents=True, exist_ok=True)
            old_message = staging / "message" / "message.html"
            active_message = self.project_root / MESSAGE_HTML_FILE
            if old_message.is_file() and active_message.is_file():
                old_html = old_message.read_text(encoding="utf-8")
                new_html = active_message.read_text(encoding="utf-8")
                if old_html != new_html:
                    self._add_message_revision(staging, old_html)

            for child, source in (
                ("pages", self.project_root / USER_PAGES_DIR),
                ("message", self.project_root / USER_MESSAGE_DIR),
            ):
                destination = staging / child
                if destination.exists():
                    shutil.rmtree(destination)
                _copy_directory(source, destination)

            sounds = staging / "sounds"
            sounds.mkdir(parents=True, exist_ok=True)
            project_music = sounds / MUSIC_FILE
            active_music = self.project_root / USER_SOUNDS_DIR / MUSIC_FILE
            if active_music.is_file():
                shutil.copy2(active_music, project_music)
            else:
                project_music.unlink(missing_ok=True)

            previous = self._read_manifest(staging)
            settings = self.settings.as_dict()
            settings.pop(ACTIVE_PROJECT_KEY, None)
            settings.pop(RECENT_PROJECTS_KEY, None)
            self._write_manifest(
                staging,
                {
                    "id": project_id,
                    "name": name,
                    "created_at": previous.get("created_at") or self._timestamp(),
                    "updated_at": self._timestamp(),
                    "settings": settings,
                },
            )
            tx.commit()
        except Exception:
            tx.abort()
            raise

    def _add_message_revision(self, project_dir: Path, html: str) -> None:
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()[:10]
        revisions = project_dir / "revisions" / "message"
        revisions.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        revision = revisions / f"{stamp}-{time.time_ns()}-{digest}.html"
        _atomic_write_text(revision, html)
        for stale in sorted(revisions.glob("*.html"), reverse=True)[MAX_MESSAGE_REVISIONS:]:
            stale.unlink(missing_ok=True)

    def _set_active(self, project_id: str) -> None:
        self.settings.update_fields(
            {
                ACTIVE_PROJECT_KEY: project_id,
                RECENT_PROJECTS_KEY: self._recent_ids(project_id),
            }
        )

    def _touch_recent(self, project_id: str) -> None:
        self.settings.update_fields({RECENT_PROJECTS_KEY: self._recent_ids(project_id)})

    def _recent_ids(self, first: str) -> list[str]:
        existing = self.settings.get(RECENT_PROJECTS_KEY, [])
        values = existing if isinstance(existing, list) else []
        return [first, *(value for value in values if value != first)][:MAX_RECENT_PROJECTS]

    def _available_project_id(self, base: str) -> str:
        candidate = base
        index = 2
        while (self.projects_dir / candidate).exists():
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def _summary(self, project_id: str) -> ProjectSummary:
        path = self.projects_dir / project_id
        manifest = self._read_manifest(path)
        return ProjectSummary(
            project_id=project_id,
            name=str(manifest.get("name") or project_id),
            path=path,
            updated_at=str(manifest.get("updated_at") or ""),
        )

    @staticmethod
    def _timestamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    @staticmethod
    def _read_manifest(project_dir: Path) -> dict[str, Any]:
        path = project_dir / PROJECT_MANIFEST
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_manifest(project_dir: Path, manifest: dict[str, Any]) -> None:
        _atomic_write_text(
            project_dir / PROJECT_MANIFEST,
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
