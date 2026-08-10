from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from project_state import ProjectIdentity, require_project_identity
from recipient_registry import RecipientRecord, RecipientRegistry
from transactional_io import atomic_write_json, safe_write_json


PROJECT_METADATA_FILE = "lettersmith-metadata.json"
PROJECT_METADATA_SCHEMA_VERSION = 2
AUTOSAVE_RELATIVE_PATH = Path("output") / "projects"
_LOGGER = logging.getLogger(__name__)


class ProjectPathError(RuntimeError):
    pass


def _valid_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return ""


def _safe_title(value: object) -> str:
    text = " ".join(str(value or "").split()) or "Untitled Letter"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text)
    text = " ".join(text.split()).rstrip(" .")
    if not text:
        text = "Untitled Letter"
    if text.casefold() in {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }:
        text = f"{text} Letter"
    return text


@dataclass(frozen=True)
class ProjectContext:
    recipient_id: str
    recipient_display_name: str
    recipient_normalized_key: str
    project_id: str
    letter_title: str
    recipient_directory: Path
    autosave_directory: Path

    @property
    def identity(self) -> ProjectIdentity:
        return ProjectIdentity(
            recipient_id=self.recipient_id,
            recipient_display_name=self.recipient_display_name,
            recipient_normalized_key=self.recipient_normalized_key,
            project_id=self.project_id,
        )


class ProjectPathResolver:
    """Resolve temporary autosaves and canonical Play paths separately.

    ``output/projects`` is write-only autosave storage. Saved-letter discovery
    and restoration must use ``output/Play`` or ``output/Recovery`` instead.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.output_root = (self.project_root / "output").resolve()
        self.autosave_root = (self.output_root / "projects").resolve()
        self.play_root = (self.output_root / "Play").resolve()
        self.registry = RecipientRegistry(self.project_root)
        self._lock = RLock()
        _LOGGER.debug("Letter Smith autosave path: %s", self.autosave_root)

    def find_autosave_directories(
        self,
        project_id: object,
        *,
        recipient_id: object | None = None,
    ) -> tuple[Path, ...]:
        stable_project_id = _valid_uuid(project_id)
        if not stable_project_id:
            raise ProjectPathError("Project ID must be a UUID.")
        if recipient_id is not None:
            roots = (self.resolve_autosave_recipient_directory(recipient_id),)
        else:
            roots = tuple(
                self.resolve_autosave_recipient_directory(record.recipient_id)
                for record in self.registry.list()
            )
        matches: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_dir() and self._metadata_project_id(child) == stable_project_id:
                    matches.append(child.resolve())
        return tuple(dict.fromkeys(matches))

    def resolve_autosave_recipient_directory(
        self,
        recipient_id: object,
    ) -> Path:
        record = self.registry.find_by_id(recipient_id)
        if record is None:
            raise ProjectPathError("Recipient ID is not registered.")
        path = (self.autosave_root / record.folder_name).resolve()
        self._assert_child(self.autosave_root, path)
        return path

    def resolve_play_recipient_directory(
        self,
        recipient_id: object,
    ) -> Path:
        record = self.registry.find_by_id(recipient_id)
        if record is None:
            raise ProjectPathError("Recipient ID is not registered.")
        path = (self.play_root / record.folder_name).resolve()
        self._assert_child(self.play_root, path)
        return path

    def resolve_autosave_directory(
        self,
        project_id: object,
        *,
        recipient_id: object | None = None,
    ) -> Path | None:
        stable_project_id = _valid_uuid(project_id)
        if not stable_project_id:
            raise ProjectPathError("Project ID must be a UUID.")
        unique = self.find_autosave_directories(
            stable_project_id,
            recipient_id=recipient_id,
        )
        if len(unique) > 1:
            raise ProjectPathError(
                "Project ID appears in more than one autosave folder: "
                + "; ".join(str(path) for path in unique)
            )
        return unique[0] if unique else None

    def context_from_settings(
        self,
        settings: Mapping[str, Any],
    ) -> ProjectContext:
        identity = require_project_identity(settings)
        record = self.registry.find_by_id(identity.recipient_id)
        if record is None:
            raise ProjectPathError("Active recipient is not registered.")
        title = _safe_title(settings.get("recipient_title"))
        conflict = self.find_title_conflict(
            identity.recipient_id,
            title,
            project_id=identity.project_id,
        )
        if conflict is not None:
            raise ProjectPathError(
                f"{record.display_name} already has a letter titled "
                f"{title!r}. Enter a different letter title."
            )
        existing_matches = self.find_autosave_directories(
            identity.project_id,
        )
        matching_title = _safe_title(title).casefold()
        named_matches = tuple(
            path for path in existing_matches if path.name.casefold() == matching_title
        )
        if len(existing_matches) > 1 and len(named_matches) == 1:
            self.repair_duplicate_autosave_ids(
                active_autosave_directory=named_matches[0],
            )
            existing = named_matches[0]
        elif len(existing_matches) > 1:
            raise ProjectPathError(
                "Project ID appears in more than one autosave folder: "
                + "; ".join(str(path) for path in existing_matches)
            )
        else:
            existing = existing_matches[0] if existing_matches else None
        autosave_directory = (
            self._relocate_autosave_directory(
                existing,
                record,
                title,
                identity.project_id,
            )
            if existing is not None
            else self._available_autosave_directory(
                record,
                title,
                identity.project_id,
            )
        )
        return ProjectContext(
            recipient_id=record.recipient_id,
            recipient_display_name=record.display_name,
            recipient_normalized_key=record.normalized_key,
            project_id=identity.project_id,
            letter_title=title,
            recipient_directory=self.resolve_autosave_recipient_directory(
                record.recipient_id
            ),
            autosave_directory=autosave_directory,
        )

    def find_title_conflict(
        self,
        recipient_id: object,
        title: object,
        *,
        project_id: object,
    ) -> Path | None:
        """Find another project using this recipient/title combination."""
        stable_project_id = _valid_uuid(project_id)
        if not stable_project_id:
            raise ProjectPathError("Project ID must be a UUID.")
        requested_title = _safe_title(title).casefold()
        roots = (
            self.resolve_autosave_recipient_directory(recipient_id),
            self.resolve_play_recipient_directory(recipient_id),
        )
        for recipient_directory in roots:
            if not recipient_directory.is_dir():
                continue
            for directory in sorted(recipient_directory.iterdir()):
                if not directory.is_dir():
                    continue
                candidate_title = self._directory_title(
                    directory
                ).casefold()
                if candidate_title != requested_title:
                    continue
                candidate_project_id = self._directory_project_id(
                    directory
                )
                if candidate_project_id == stable_project_id:
                    continue
                return directory.resolve()
        return None

    def ensure_autosave_storage(
        self,
        context: ProjectContext,
    ) -> Path:
        with self._lock:
            self._validate_context(context)
            context.autosave_directory.mkdir(parents=True, exist_ok=True)
            self.write_autosave_metadata(context)
            return context.autosave_directory

    def write_autosave_metadata(
        self,
        context: ProjectContext,
        updates: Mapping[str, Any] | None = None,
    ) -> Path:
        with self._lock:
            self._validate_context(context)
            path = context.autosave_directory / PROJECT_METADATA_FILE
            existing: dict[str, Any] = {}
            if path.is_file():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        existing = raw
                except (OSError, UnicodeError, json.JSONDecodeError):
                    raise ProjectPathError(
                        f"Project metadata could not be read: {path}"
                    )
            existing.update(dict(updates or {}))
            existing.update(
                {
                    "project_schema_version": (
                        PROJECT_METADATA_SCHEMA_VERSION
                    ),
                    "project_id": context.project_id,
                    "recipient_id": context.recipient_id,
                    "recipient_display_name": (
                        context.recipient_display_name
                    ),
                    "recipient_normalized_key": (
                        context.recipient_normalized_key
                    ),
                    "recipient_name": context.recipient_display_name,
                    "recipient_title": context.letter_title,
                }
            )
            atomic_write_json(path, existing)
            return path

    def repair_duplicate_autosave_ids(
        self,
        *,
        active_autosave_directory: str | Path,
    ) -> tuple[tuple[Path, str], ...]:
        """Give independent duplicate autosaves new IDs without merging them."""
        active = Path(active_autosave_directory).resolve()
        if not active.is_dir():
            raise ProjectPathError("The active autosave folder does not exist.")
        active_id = self._metadata_project_id(active)
        if not active_id:
            raise ProjectPathError("The active project metadata has no valid project ID.")
        matches = self.find_autosave_directories(active_id)
        if len(matches) < 2:
            return ()
        if active not in matches:
            raise ProjectPathError("The active autosave folder does not match the duplicate ID.")

        used_ids = {
            self._metadata_project_id(path)
            for path in self._all_autosave_directories()
        }
        repaired: list[tuple[Path, str]] = []
        for path in matches:
            if path == active:
                continue
            metadata_path = path / PROJECT_METADATA_FILE
            metadata = self._read_metadata(metadata_path)
            backup = metadata_path.with_name(
                f"{metadata_path.name}.backup-{uuid.uuid4().hex}"
            )
            shutil.copy2(metadata_path, backup)
            new_id = str(uuid.uuid4())
            while new_id in used_ids:
                new_id = str(uuid.uuid4())
            updated = dict(metadata)
            updated["project_id"] = new_id

            def validate(value: Mapping[str, Any]) -> None:
                if _valid_uuid(value.get("project_id")) != new_id:
                    raise ValueError("rewritten project metadata has an invalid project ID")
                if any(
                    value.get(key) != metadata.get(key)
                    for key in ("recipient_id", "recipient_name", "recipient_title")
                ):
                    raise ValueError("project metadata changed outside project_id")

            try:
                safe_write_json(metadata_path, updated, validator=validate)
            except Exception:
                _LOGGER.exception("Could not repair duplicate project metadata: %s", metadata_path)
                raise ProjectPathError(
                    f"Could not safely rewrite project metadata: {metadata_path}"
                ) from None
            used_ids.add(new_id)
            repaired.append((path, new_id))
        return tuple(repaired)

    def _all_autosave_directories(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for record in self.registry.list():
            root = self.resolve_autosave_recipient_directory(record.recipient_id)
            if root.is_dir():
                paths.extend(child.resolve() for child in root.iterdir() if child.is_dir())
        return tuple(paths)

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProjectPathError(f"Project metadata could not be read: {path}") from error
        if not isinstance(raw, dict):
            raise ProjectPathError(f"Project metadata must be an object: {path}")
        return raw

    def _available_autosave_directory(
        self,
        recipient: RecipientRecord,
        title: str,
        project_id: str,
    ) -> Path:
        recipient_directory = self.resolve_autosave_recipient_directory(
            recipient.recipient_id
        )
        base = _safe_title(title)
        candidate = (recipient_directory / base).resolve()
        self._assert_child(recipient_directory, candidate)
        if candidate.exists():
            if self._metadata_project_id(candidate) == project_id:
                return candidate
            raise ProjectPathError(
                f"{recipient.display_name} already has a letter titled "
                f"{title!r}. Enter a different letter title."
            )
        return candidate

    def _relocate_autosave_directory(
        self,
        source: Path,
        recipient: RecipientRecord,
        title: str,
        project_id: str,
    ) -> Path:
        source = source.resolve()
        recipient_directory = self.resolve_autosave_recipient_directory(
            recipient.recipient_id
        )
        destination = (recipient_directory / _safe_title(title)).resolve()
        self._assert_child(recipient_directory, destination)
        if source == destination and source.name == destination.name:
            return source
        if destination.exists():
            try:
                if source.samefile(destination):
                    return self._rename_case_only(source, destination)
            except OSError:
                pass
            if self._metadata_project_id(destination) == project_id:
                raise ProjectPathError(
                    "Project ID appears in more than one project folder: "
                    f"{source}; {destination}"
                )
            raise ProjectPathError(
                f"{recipient.display_name} already has a letter titled "
                f"{title!r}. Enter a different letter title."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.replace(destination)
        except OSError as error:
            raise ProjectPathError(
                f"The project folder could not be renamed: {error}"
            ) from error
        return destination

    @staticmethod
    def _rename_case_only(source: Path, destination: Path) -> Path:
        if source.name == destination.name:
            return source
        temporary = source.with_name(
            f".{source.name}.rename-{uuid.uuid4().hex}"
        )
        try:
            source.replace(temporary)
            temporary.replace(destination)
        except OSError as error:
            if temporary.exists() and not source.exists():
                try:
                    temporary.replace(source)
                except OSError:
                    pass
            raise ProjectPathError(
                f"The project folder capitalization could not be changed: {error}"
            ) from error
        return destination.resolve()

    @classmethod
    def _directory_project_id(cls, directory: Path) -> str:
        metadata_id = cls._metadata_project_id(directory)
        if metadata_id:
            return metadata_id
        path = directory / "lettersmith-build.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""
        if not isinstance(value, dict):
            return ""
        return _valid_uuid(value.get("project_id"))

    @classmethod
    def _directory_title(cls, directory: Path) -> str:
        metadata_path = directory / PROJECT_METADATA_FILE
        if metadata_path.is_file():
            try:
                metadata = cls._read_metadata(metadata_path)
            except ProjectPathError:
                metadata = {}
            title = str(metadata.get("recipient_title", "")).strip()
            if title:
                return _safe_title(title)
        index_path = directory / "index.html"
        try:
            index = index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            index = ""
        match = re.search(
            r"<title\b[^>]*>(.*?)</title\s*>",
            index,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            title = unescape(re.sub(r"<[^>]+>", "", match.group(1)))
            if title.strip():
                return _safe_title(title)
        return _safe_title(directory.name)

    def _validate_context(self, context: ProjectContext) -> None:
        if not context.identity.is_valid:
            raise ProjectPathError("Project context identity is incomplete.")
        record = self.registry.find_by_id(context.recipient_id)
        if record is None:
            raise ProjectPathError("Project recipient is not registered.")
        if (
            record.display_name != context.recipient_display_name
            or record.normalized_key
            != context.recipient_normalized_key
        ):
            raise ProjectPathError(
                "Project context does not match the recipient registry."
            )
        recipient_directory = self.resolve_autosave_recipient_directory(
            context.recipient_id
        )
        self._assert_child(
            recipient_directory,
            context.autosave_directory.resolve(),
        )
        existing_id = self._metadata_project_id(
            context.autosave_directory
        )
        if existing_id and existing_id != context.project_id:
            raise ProjectPathError(
                "Autosave directory belongs to another project."
            )

    @staticmethod
    def _metadata_project_id(autosave_directory: Path) -> str:
        path = autosave_directory / PROJECT_METADATA_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""
        if not isinstance(value, dict):
            return ""
        return _valid_uuid(value.get("project_id"))

    @staticmethod
    def _assert_child(parent: Path, child: Path) -> None:
        try:
            child.relative_to(parent)
        except ValueError as error:
            raise ProjectPathError(
                f"Project path escapes its canonical root: {child}"
            ) from error
        if child == parent:
            raise ProjectPathError("Project path cannot equal its root.")


__all__ = [
    "AUTOSAVE_RELATIVE_PATH",
    "PROJECT_METADATA_FILE",
    "PROJECT_METADATA_SCHEMA_VERSION",
    "ProjectContext",
    "ProjectPathError",
    "ProjectPathResolver",
]
