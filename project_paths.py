from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from recipient_registry import RecipientRecord

from application_identity import APPLICATION_NAME, PUBLISHER_NAME
from save_schema import (
    AUTOSAVE_DOCUMENT_TYPE,
    stamp_current_save_schema,
)
from transactional_io import atomic_write_json, safe_write_json


PROJECT_METADATA_FILE = "lettersmith-metadata.json"
PROJECT_METADATA_SCHEMA_VERSION = 2
AUTOSAVE_RELATIVE_PATH = Path("output") / "projects"
_LOGGER = logging.getLogger(__name__)

STORAGE_LAYOUT_VERSION = 1


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class ApplicationPaths:
    """Authoritative read-only resource and writable user-data layout."""

    resource_root: Path
    workspace_root: Path
    app_data_root: Path
    settings_root: Path
    prompt_writer_content_root: Path
    custom_palette_root: Path
    music_archive_root: Path
    logs_root: Path
    cache_root: Path
    publication_root: Path
    migration_root: Path
    documents_root: Path
    saved_letters_root: Path
    recovery_root: Path
    export_root: Path
    generated_root: Path
    temporary_root: Path
    autosave_root: Path
    recipient_registry_file: Path
    project_sound_state_file: Path
    current_sound_manifest_file: Path
    settings_file: Path

    @classmethod
    def for_project(cls, project_root: str | Path) -> "ApplicationPaths":
        """Keep explicitly supplied development/test projects self-contained."""
        root = _resolved(project_root)
        output = root / "output"
        archive = root / "gallery" / "user" / "sounds" / "appssong"
        return cls(
            resource_root=root,
            workspace_root=root,
            app_data_root=root,
            settings_root=root,
            prompt_writer_content_root=root / "Prompter" / "modules",
            custom_palette_root=root / "Prompter" / "modules",
            music_archive_root=archive,
            logs_root=root / "logs",
            cache_root=root / "gallery" / "user" / "cache",
            publication_root=output / "publication",
            migration_root=root / "migrations",
            documents_root=output,
            saved_letters_root=output / "Play",
            recovery_root=output / "Recovery",
            export_root=output / "Exports",
            generated_root=output / "Generated Packages",
            temporary_root=output,
            autosave_root=output / "projects",
            recipient_registry_file=output / "recipients.json",
            project_sound_state_file=archive / "project_sound.json",
            current_sound_manifest_file=archive / "current.json",
            settings_file=root / "settings.json",
        )

    @classmethod
    def for_runtime(
        cls,
        resource_root: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        home: str | Path | None = None,
        temporary_base: str | Path | None = None,
    ) -> "ApplicationPaths":
        environment = os.environ if environ is None else environ
        if resource_root is None:
            frozen_root = getattr(sys, "_MEIPASS", None)
            resource_root = frozen_root or Path(__file__).resolve().parent
        resources = _resolved(resource_root)
        home_root = _resolved(
            home
            or environment.get("USERPROFILE")
            or Path.home()
        )
        local_app_data = _resolved(
            environment.get("LOCALAPPDATA")
            or (home_root / "AppData" / "Local")
        )
        documents_base = _resolved(
            environment.get("LETTER_SMITH_DOCUMENTS_ROOT")
            or (home_root / "Documents")
        )
        app_data = local_app_data / PUBLISHER_NAME / APPLICATION_NAME
        documents = documents_base / APPLICATION_NAME
        workspace = app_data / "Active Project"
        sound_workspace = workspace / "gallery" / "user" / "sounds" / "appssong"
        cache = app_data / "cache"
        temporary = _resolved(
            temporary_base
            or environment.get("LETTER_SMITH_TEMP_ROOT")
            or (cache / "temp")
        )
        return cls(
            resource_root=resources,
            workspace_root=workspace,
            app_data_root=app_data,
            settings_root=app_data / "settings",
            prompt_writer_content_root=app_data / "Prompt Writer" / "content",
            custom_palette_root=app_data / "Prompt Writer" / "palettes",
            music_archive_root=app_data / "Music Archive",
            logs_root=app_data / "logs",
            cache_root=cache,
            publication_root=app_data / "publication",
            migration_root=app_data / "migrations",
            documents_root=documents,
            saved_letters_root=documents / "Saved Letters",
            recovery_root=documents / "Recovery",
            export_root=documents / "Exports",
            generated_root=documents / "Generated Packages",
            temporary_root=temporary,
            autosave_root=app_data / "autosaves",
            recipient_registry_file=documents / "recipients.json",
            project_sound_state_file=sound_workspace / "project_sound.json",
            current_sound_manifest_file=sound_workspace / "current.json",
            settings_file=app_data / "settings" / "settings.json",
        )

    @property
    def stock_root(self) -> Path:
        return self.resource_root / "resources" / "stock"

    @property
    def stock_images_root(self) -> Path:
        return self.stock_root / "images"

    @property
    def stock_music_root(self) -> Path:
        return self.stock_root / "music"

    @property
    def stock_letters_root(self) -> Path:
        return self.stock_root / "letters"

    @property
    def examples_root(self) -> Path:
        return self.resource_root / "resources" / "examples"

    @property
    def bundled_prompt_writer_root(self) -> Path:
        primary = self.resource_root / "resources" / "prompt_writer"
        if primary.is_dir():
            return primary
        return self.resource_root / "Prompter" / "modules"

    def resource_path(self, relative: str | Path) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("Application resource paths must be relative.")
        return (self.resource_root / value).resolve()

    def app_resource_path(self, relative: str | Path) -> Path:
        """Resolve a 1.0 app resource with a source-tree compatibility read."""
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("Application resource paths must be relative.")
        primary = (self.resource_root / "resources" / "app" / value).resolve()
        if primary.exists():
            return primary
        return (self.resource_root / "gallery" / "app" / value).resolve()

    def tool_path(self, name: str) -> Path:
        filename = Path(name).name
        if filename != str(name):
            raise ValueError("Bundled tool names cannot contain directories.")
        return (self.resource_root / "tools" / filename).resolve()

    def ensure_writable_roots(self) -> None:
        directories = (
            self.workspace_root,
            self.workspace_root / "gallery" / "user" / "pages",
            self.workspace_root / "gallery" / "user" / "card" / "controls",
            self.workspace_root / "gallery" / "user" / "message",
            self.workspace_root / "gallery" / "user" / "sounds" / "appssong",
            self.workspace_root / "gallery" / "user" / "fonts",
            self.settings_root,
            self.prompt_writer_content_root,
            self.custom_palette_root,
            self.music_archive_root / "originals",
            self.music_archive_root / "processed",
            self.music_archive_root / "analysis",
            self.logs_root,
            self.cache_root,
            self.publication_root,
            self.migration_root,
            self.documents_root,
            self.saved_letters_root,
            self.recovery_root,
            self.export_root,
            self.generated_root,
            self.temporary_root,
            self.autosave_root,
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def initialize(self, legacy_root: str | Path | None = None) -> None:
        """Create writable roots and copy legacy data without overwriting it."""
        self.ensure_writable_roots()
        if legacy_root is None or self.workspace_root == self.resource_root:
            _LOGGER.debug("Writable application storage is ready: %s", self.app_data_root)
            return
        legacy = _resolved(legacy_root)
        marker = self.migration_root / f"storage-layout-{STORAGE_LAYOUT_VERSION}.json"
        if marker.is_file():
            _LOGGER.debug(
                "Storage layout migration %s is already complete.",
                STORAGE_LAYOUT_VERSION,
            )
            return

        _LOGGER.info(
            "Storage layout migration %s started.",
            STORAGE_LAYOUT_VERSION,
        )

        def copy_file(source: Path, destination: Path) -> None:
            if not source.is_file() or destination.exists():
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        def copy_tree(source: Path, destination: Path) -> None:
            if not source.is_dir():
                return
            destination.mkdir(parents=True, exist_ok=True)
            for item in source.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                target = destination / item.relative_to(source)
                copy_file(item, target)

        copy_file(legacy / "settings.json", self.settings_file)
        copy_file(
            legacy / "prompt_writer_state.json",
            self.workspace_root / "prompt_writer_state.json",
        )
        copy_tree(
            self.app_resource_path("controls"),
            self.workspace_root / "gallery/user/card/controls",
        )
        for relative in (
            Path("gallery/user/pages"),
            Path("gallery/user/card/controls"),
            Path("gallery/user/message"),
            Path("gallery/user/fonts"),
        ):
            copy_tree(legacy / relative, self.workspace_root / relative)

        legacy_sounds = legacy / "gallery" / "user" / "sounds"
        copy_file(legacy_sounds / "music.mp3", self.workspace_root / "gallery/user/sounds/music.mp3")
        legacy_archive = legacy_sounds / "appssong"
        for directory in ("originals", "processed", "analysis"):
            copy_tree(legacy_archive / directory, self.music_archive_root / directory)
        copy_file(legacy_archive / "library.json", self.music_archive_root / "library.json")
        copy_file(legacy_archive / "project_sound.json", self.project_sound_state_file)
        copy_file(legacy_archive / "current.json", self.current_sound_manifest_file)

        copy_tree(legacy / "output" / "Play", self.saved_letters_root)
        copy_tree(legacy / "output" / "Recovery", self.recovery_root)
        copy_tree(legacy / "output" / "projects", self.autosave_root)
        copy_file(legacy / "output" / "recipients.json", self.recipient_registry_file)

        legacy_modules = legacy / "Prompter" / "modules"
        for filename in ("type.txt", "topic.txt", "color.txt"):
            copy_file(legacy_modules / filename, self.prompt_writer_content_root / filename)
        copy_file(
            legacy_modules / "user_colors.json",
            self.custom_palette_root / "user_colors.json",
        )

        atomic_write_json(
            marker,
            {
                "storage_layout_version": STORAGE_LAYOUT_VERSION,
                "legacy_root": str(legacy),
            },
        )
        _LOGGER.info(
            "Storage layout migration %s completed.",
            STORAGE_LAYOUT_VERSION,
        )


_APPLICATION_PATHS: ApplicationPaths | None = None


def configure_application_paths(paths: ApplicationPaths) -> ApplicationPaths:
    global _APPLICATION_PATHS
    _APPLICATION_PATHS = paths
    return paths


def application_paths(project_root: str | Path | None = None) -> ApplicationPaths:
    configured = _APPLICATION_PATHS
    if configured is not None:
        if project_root is None:
            return configured
        root = _resolved(project_root)
        if root in {configured.workspace_root, configured.resource_root}:
            return configured
    if project_root is None:
        return ApplicationPaths.for_project(Path(__file__).resolve().parent)
    return ApplicationPaths.for_project(project_root)


def runtime_application_paths(
    resource_root: str | Path | None = None,
) -> ApplicationPaths:
    if _APPLICATION_PATHS is not None:
        return _APPLICATION_PATHS
    return ApplicationPaths.for_runtime(resource_root)


def resource_path(project_root: str | Path, relative: str | Path) -> Path:
    return application_paths(project_root).resource_path(relative)


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
    def identity(self) -> "ProjectIdentity":
        from project_state import ProjectIdentity

        return ProjectIdentity(
            recipient_id=self.recipient_id,
            recipient_display_name=self.recipient_display_name,
            recipient_normalized_key=self.recipient_normalized_key,
            project_id=self.project_id,
        )


class ProjectPathResolver:
    """Resolve temporary autosaves and canonical saved-letter paths separately.

    Autosaves are write-only working data. Saved-letter discovery and
    restoration use the configured saved-letter or recovery roots instead.
    """

    def __init__(self, project_root: str | Path) -> None:
        from recipient_registry import RecipientRegistry

        self.project_root = Path(project_root).resolve()
        paths = application_paths(self.project_root)
        self.output_root = paths.documents_root.resolve()
        self.autosave_root = paths.autosave_root.resolve()
        self.play_root = paths.saved_letters_root.resolve()
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
        from project_state import require_project_identity

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
            existing = stamp_current_save_schema(
                existing,
                document_type=AUTOSAVE_DOCUMENT_TYPE,
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
    "APPLICATION_NAME",
    "ApplicationPaths",
    "AUTOSAVE_RELATIVE_PATH",
    "PUBLISHER_NAME",
    "PROJECT_METADATA_FILE",
    "PROJECT_METADATA_SCHEMA_VERSION",
    "ProjectContext",
    "ProjectPathError",
    "ProjectPathResolver",
    "application_paths",
    "configure_application_paths",
    "resource_path",
    "runtime_application_paths",
]
