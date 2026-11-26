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
from transactional_io import (
    atomic_copy_file,
    atomic_write_json,
    enforce_internal_tree_visibility,
    is_link_or_reparse_point,
    safe_write_json,
    set_path_hidden,
)


PROJECT_METADATA_FILE = "lettersmith-metadata.json"
PROJECT_METADATA_SCHEMA_VERSION = 2
LEGACY_OUTPUT_DIR = "output"
LEGACY_PLAY_DIR = "Play"
AUTOSAVE_RELATIVE_PATH = Path(LEGACY_OUTPUT_DIR) / "projects"

WORKSPACE_USER_DIR = Path("gallery") / "user"
WORKSPACE_PAGES_DIR = WORKSPACE_USER_DIR / "pages"
WORKSPACE_CONTROLS_DIR = WORKSPACE_USER_DIR / "card" / "controls"
WORKSPACE_MESSAGE_DIR = WORKSPACE_USER_DIR / "message"
WORKSPACE_SOUNDS_DIR = WORKSPACE_USER_DIR / "sounds"
WORKSPACE_SOUND_STATE_DIR = WORKSPACE_SOUNDS_DIR / "appssong"
_WORKSPACE_MEDIA_DIRS = (
    WORKSPACE_PAGES_DIR,
    WORKSPACE_CONTROLS_DIR,
    WORKSPACE_MESSAGE_DIR,
    WORKSPACE_USER_DIR / "fonts",
)
MUSIC_FILE_NAME = "music.mp3"
AUDIO_ORIGINALS_DIR_NAME = "originals"
AUDIO_PROCESSED_DIR_NAME = "processed"
AUDIO_ANALYSIS_DIR_NAME = "analysis"
AUDIO_LIBRARY_FILE_NAME = "library.json"
AUDIO_PROJECT_STATE_NAME = "project_sound.json"
AUDIO_CURRENT_MANIFEST_NAME = "current.json"
_MUSIC_ARCHIVE_DIRS = (
    AUDIO_ORIGINALS_DIR_NAME,
    AUDIO_PROCESSED_DIR_NAME,
    AUDIO_ANALYSIS_DIR_NAME,
)

_BUNDLED_PROMPT_WRITER_DIR = Path("resources") / "prompt_writer"
_LEGACY_PROMPT_WRITER_DIR = Path("Prompter") / "modules"
_LOGGER = logging.getLogger(__name__)

STORAGE_LAYOUT_VERSION = 1
_TRANSACTION_DIRECTORY_PATTERN = re.compile(
    r"\.(?:snapshot|identity|build|new-project)-"
    r"(?:staging\.[0-9a-f]{32}|backup)$",
    flags=re.IGNORECASE,
)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_transaction_artifact_directory(path: Path) -> bool:
    return bool(_TRANSACTION_DIRECTORY_PATTERN.search(path.name))


def _copy_missing_file(source: Path, destination: Path) -> None:
    if source.is_file() and not destination.exists():
        atomic_copy_file(source, destination)


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if item.is_file() and not item.is_symlink():
            _copy_missing_file(item, destination / item.relative_to(source))


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
    profile_root: Path | None = None

    @classmethod
    def for_project(
        cls, project_root: str | Path, *, output_root: str | Path | None = None,
    ) -> "ApplicationPaths":
        """Keep explicitly supplied development/test projects self-contained."""
        root = _resolved(project_root)
        output = root / LEGACY_OUTPUT_DIR if output_root is None else _resolved(output_root)
        archive = root / WORKSPACE_SOUND_STATE_DIR
        prompt_writer = root / _LEGACY_PROMPT_WRITER_DIR
        return cls(
            resource_root=root,
            workspace_root=root,
            app_data_root=root,
            settings_root=root,
            prompt_writer_content_root=prompt_writer,
            custom_palette_root=prompt_writer,
            music_archive_root=archive,
            logs_root=root / "logs",
            cache_root=root / WORKSPACE_USER_DIR / "cache",
            publication_root=output / "publication",
            migration_root=root / "migrations",
            documents_root=output,
            saved_letters_root=output / LEGACY_PLAY_DIR,
            recovery_root=output / "Recovery",
            export_root=output / "Exports",
            generated_root=output / "Generated Packages",
            temporary_root=output,
            autosave_root=output / AUTOSAVE_RELATIVE_PATH.name,
            recipient_registry_file=output / "recipients.json",
            project_sound_state_file=archive / AUDIO_PROJECT_STATE_NAME,
            current_sound_manifest_file=archive / AUDIO_CURRENT_MANIFEST_NAME,
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
        platform_name: str | None = None,
    ) -> "ApplicationPaths":
        environment = os.environ if environ is None else environ
        runtime_platform = str(platform_name or sys.platform).casefold()
        if resource_root is None:
            frozen_root = getattr(sys, "_MEIPASS", None)
            resource_root = frozen_root or Path(__file__).resolve().parent
        resources = _resolved(resource_root)
        profile_value = str(environment.get("LETTER_SMITH_PROFILE_ROOT", "")).strip()
        profile = _resolved(profile_value) if profile_value else None
        home_root = _resolved(
            home
            or environment.get(
                "USERPROFILE" if runtime_platform == "win32" else "HOME"
            )
            or Path.home()
        )
        documents_base = _resolved(
            environment.get("LETTER_SMITH_DOCUMENTS_ROOT")
            or (home_root / "Documents")
        )
        if runtime_platform == "darwin":
            app_data = (
                home_root
                / "Library"
                / "Application Support"
                / PUBLISHER_NAME
                / APPLICATION_NAME
            )
            cache = (
                home_root
                / "Library"
                / "Caches"
                / PUBLISHER_NAME
                / APPLICATION_NAME
            )
        else:
            local_app_data = _resolved(
                environment.get("LOCALAPPDATA")
                or (home_root / "AppData" / "Local")
            )
            app_data = local_app_data / PUBLISHER_NAME / APPLICATION_NAME
            cache = app_data / "cache"
        documents = documents_base / APPLICATION_NAME
        temporary = _resolved(
            temporary_base
            or environment.get("LETTER_SMITH_TEMP_ROOT")
            or (cache / "temp")
        )
        if profile is not None:
            # One override isolates every writable runtime path, including saved
            # letters and reusable libraries. Resources remain read-only inputs.
            app_data = profile / "app"
            cache = profile / "cache"
            documents = profile / "documents"
            temporary = cache / "temp"
        workspace = app_data / "Active Project"
        sound_workspace = workspace / WORKSPACE_SOUND_STATE_DIR
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
            project_sound_state_file=sound_workspace / AUDIO_PROJECT_STATE_NAME,
            current_sound_manifest_file=sound_workspace / AUDIO_CURRENT_MANIFEST_NAME,
            settings_file=app_data / "settings" / "settings.json",
            profile_root=profile,
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
    def workspace_sounds_root(self) -> Path:
        return self.workspace_root / WORKSPACE_SOUNDS_DIR

    @property
    def music_library_file(self) -> Path:
        return self.music_archive_root / AUDIO_LIBRARY_FILE_NAME

    @property
    def bundled_prompt_writer_root(self) -> Path:
        primary = self.resource_root / _BUNDLED_PROMPT_WRITER_DIR
        if primary.is_dir():
            return primary
        return self.resource_root / _LEGACY_PROMPT_WRITER_DIR

    def bundled_prompt_writer_path(self, filename: str) -> Path:
        if Path(filename).name != filename or filename in {"", ".", ".."}:
            raise ValueError("Prompt Writer module names must be filenames.")
        path = self.bundled_prompt_writer_root / filename
        if not path.is_file():
            legacy = self.resource_root / _LEGACY_PROMPT_WRITER_DIR / filename
            if legacy.is_file():
                return legacy
        return path

    def resource_path(self, relative: str | Path) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("Application resource paths must be relative.")
        return (self.resource_root / value).resolve()

    def app_resource_path(self, relative: str | Path) -> Path:
        """Resolve an app resource from its canonical source-tree owner."""
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("Application resource paths must be relative.")
        resources_owned = bool(value.parts) and value.parts[0].casefold() in {
            "fonts",
            "themes",
        }
        primary_root = (
            self.resource_root / "resources" / "app"
            if resources_owned
            else self.resource_root / "gallery" / "app"
        )
        compatibility_root = (
            self.resource_root / "gallery" / "app"
            if resources_owned
            else self.resource_root / "resources" / "app"
        )
        primary = (primary_root / value).resolve()
        if primary.exists():
            return primary
        return (compatibility_root / value).resolve()

    def tool_path(self, name: str) -> Path:
        filename = Path(name).name
        if filename != str(name):
            raise ValueError("Bundled tool names cannot contain directories.")
        return (self.resource_root / "tools" / filename).resolve()

    def ensure_sound_dirs(self) -> None:
        directories = (
            self.workspace_sounds_root,
            self.project_sound_state_file.parent,
            self.current_sound_manifest_file.parent,
            *(self.music_archive_root / name for name in _MUSIC_ARCHIVE_DIRS),
        )
        for directory in dict.fromkeys(directories):
            directory.mkdir(parents=True, exist_ok=True)

    def ensure_workspace_media_dirs(self) -> None:
        for relative in _WORKSPACE_MEDIA_DIRS:
            (self.workspace_root / relative).mkdir(parents=True, exist_ok=True)
        self.ensure_sound_dirs()

    def ensure_writable_roots(self) -> None:
        self.ensure_workspace_media_dirs()
        directories = (
            self.settings_root,
            self.prompt_writer_content_root,
            self.custom_palette_root,
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

    def enforce_internal_visibility(self) -> tuple[Path, ...]:
        """Hide implementation state while retaining user-authored assets."""
        hidden: list[Path] = []
        roots = {
            self.workspace_root,
            self.settings_root,
            self.music_archive_root,
            self.autosave_root,
            self.saved_letters_root,
            self.recovery_root,
            self.export_root,
            self.generated_root,
            self.temporary_root,
        }
        for root in roots:
            hidden.extend(enforce_internal_tree_visibility(root))
        for path in (
            self.settings_file,
            self.workspace_root / "prompt_writer_state.json",
            self.project_sound_state_file,
            self.current_sound_manifest_file,
            self.music_library_file,
            self.workspace_root / WORKSPACE_CONTROLS_DIR,
        ):
            if set_path_hidden(path):
                hidden.append(path)
        return tuple(dict.fromkeys(hidden))

    def initialize(self, legacy_root: str | Path | None = None) -> None:
        """Create writable roots and copy legacy data without overwriting it."""
        self.ensure_writable_roots()
        self.enforce_internal_visibility()
        if self.profile_root is not None:
            # Development profiles must never import private source-tree state
            # or the normal user's active project on first launch.
            _copy_missing_tree(
                self.app_resource_path("controls"),
                self.workspace_root / WORKSPACE_CONTROLS_DIR,
            )
            return
        if legacy_root is None or self.workspace_root == self.resource_root:
            _LOGGER.debug("Writable application storage is ready: %s", self.app_data_root)
            return
        legacy = self.for_project(legacy_root)
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

        for relative in _WORKSPACE_MEDIA_DIRS:
            _copy_missing_tree(legacy.workspace_root / relative, self.workspace_root / relative)
        _copy_missing_tree(
            self.app_resource_path("controls"),
            self.workspace_root / WORKSPACE_CONTROLS_DIR,
        )

        for name in _MUSIC_ARCHIVE_DIRS:
            _copy_missing_tree(legacy.music_archive_root / name, self.music_archive_root / name)
        _copy_missing_tree(legacy.saved_letters_root, self.saved_letters_root)
        _copy_missing_tree(legacy.recovery_root, self.recovery_root)
        _copy_missing_tree(legacy.autosave_root, self.autosave_root)

        for source, destination in (
            (legacy.settings_file, self.settings_file),
            (legacy.workspace_root / "prompt_writer_state.json",
             self.workspace_root / "prompt_writer_state.json"),
            (legacy.workspace_sounds_root / MUSIC_FILE_NAME,
             self.workspace_sounds_root / MUSIC_FILE_NAME),
            (legacy.music_library_file, self.music_library_file),
            (legacy.project_sound_state_file, self.project_sound_state_file),
            (legacy.current_sound_manifest_file, self.current_sound_manifest_file),
            (legacy.recipient_registry_file, self.recipient_registry_file),
        ):
            _copy_missing_file(source, destination)

        for filename in (
            "type.txt", "topic.txt", "color.txt", "user_type.json", "user_subject.json", "user_color.json",
        ):
            _copy_missing_file(
                legacy.prompt_writer_content_root / filename,
                self.prompt_writer_content_root / filename,
            )
        _copy_missing_file(
            legacy.custom_palette_root / "user_colors.json",
            self.custom_palette_root / "user_colors.json",
        )

        atomic_write_json(
            marker,
            {
                "storage_layout_version": STORAGE_LAYOUT_VERSION,
                "legacy_root": str(legacy.workspace_root),
            },
        )
        self.enforce_internal_visibility()
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
    elif os.environ.get("LETTER_SMITH_PROFILE_ROOT", "").strip():
        # Imports such as config can read settings before Main configures paths.
        # Honor the isolation override at that boundary as well as after startup.
        runtime = ApplicationPaths.for_runtime()
        if project_root is None or _resolved(project_root) in {
            runtime.workspace_root, runtime.resource_root,
        }:
            return runtime
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
                if _is_transaction_artifact_directory(child):
                    continue
                if child.is_dir() and not is_link_or_reparse_point(child) and self._metadata_project_id(child) == stable_project_id:
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
        with self.autosave_lock():
            return self._context_from_settings(settings)

    def autosave_lock(self):
        from project_repository import _writer_lock
        return _writer_lock(self.autosave_root / ".snapshot-lock")

    def _context_from_settings(self, settings: Mapping[str, Any]) -> ProjectContext:
        from project_state import require_project_identity

        identity = require_project_identity(settings)
        record = self.registry.find_by_id(identity.recipient_id)
        if record is None:
            raise ProjectPathError("Active recipient is not registered.")
        title = _safe_title(settings.get("recipient_title"))
        owned = self.find_autosave_directories(identity.project_id, recipient_id=record.recipient_id)
        existing_matches = owned or self.find_autosave_directories(identity.project_id)
        matching_title = _safe_title(title).casefold()
        named_matches = tuple(
            path for path in existing_matches if path.name.casefold() == matching_title
        )
        if len(existing_matches) > 1 and len(named_matches) == 1:
            existing = named_matches[0]
        elif len(existing_matches) > 1:
            existing = max(existing_matches, key=lambda path: (path.stat().st_mtime_ns, path.name))
            if not all(path == existing or self._confirmed_autosave_duplicate(existing, path, repaired_identity=False)
                       for path in existing_matches):
                raise ProjectPathError(
                    "Project ID appears in more than one autosave folder: "
                    + "; ".join(str(path) for path in existing_matches)
                )
        else:
            existing = existing_matches[0] if existing_matches else None
        if existing is not None:
            self.repair_duplicate_autosave_ids(active_autosave_directory=existing)
        conflict = self.find_title_conflict(identity.recipient_id, title, project_id=identity.project_id)
        if conflict is not None:
            raise ProjectPathError(
                f"{record.display_name} already has a letter titled {title!r}. Enter a different letter title."
            )
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
        recipient = self.registry.find_by_id(recipient_id)
        roots = (
            self.resolve_autosave_recipient_directory(recipient_id),
            self.resolve_play_recipient_directory(recipient_id),
            self.play_root / "Editable Projects",
        )
        for recipient_directory in roots:
            if not recipient_directory.is_dir():
                continue
            for directory in sorted(recipient_directory.iterdir()):
                if (
                    not directory.is_dir()
                    or is_link_or_reparse_point(directory)
                    or _is_transaction_artifact_directory(directory)
                ):
                    continue
                canonical = self._canonical_letter_metadata(directory) if not directory.is_relative_to(self.autosave_root) else None
                if canonical is not None:
                    candidate_project_id, metadata = canonical
                    stored_recipient = _valid_uuid(metadata.get("recipient_id"))
                    stored_name = " ".join(str(metadata.get("recipient_name") or "").split()).casefold()
                    same_recipient = (stored_recipient == recipient.recipient_id if stored_recipient
                                      else stored_name == recipient.normalized_key)
                    if not same_recipient:
                        continue
                    candidate_title = _safe_title(metadata.get("recipient_title") or metadata.get("title")).casefold()
                elif recipient_directory == roots[-1]:
                    continue
                else:
                    candidate_title = self._directory_title(directory).casefold()
                    candidate_project_id = self._directory_project_id(directory)
                if candidate_title != requested_title:
                    continue
                if candidate_project_id == stable_project_id:
                    continue
                if directory.is_relative_to(self.autosave_root):
                    active = self.find_autosave_directories(stable_project_id, recipient_id=recipient_id)
                    if len(active) == 1 and self._confirmed_autosave_duplicate(
                            active[0], directory, repaired_identity=stable_project_id in self._autosave_original_ids(directory)):
                        continue
                return directory.resolve()
        return None

    def find_title_conflict_for_name(self, name: str, title: str, *, project_id: str) -> Path | None:
        key = " ".join(name.split()).casefold()
        if not key or not title.strip() or not project_id:
            return None
        recipient = next((record for record in self.registry.list() if record.normalized_key == key), None)
        return self.find_title_conflict(recipient.recipient_id, title, project_id=project_id) if recipient else None

    @staticmethod
    def _canonical_letter_metadata(directory: Path):
        from project_repository import ProjectRepository, ProjectRepositoryError, _safe_file, _json
        repository = ProjectRepository(directory / ".lettersmith-project")
        try:
            head = repository._head()
            if head:
                document = _json(_safe_file(repository.directory, f"g/{head['revision']}/project.json"))
                metadata = document.get("metadata")
                if isinstance(metadata, dict):
                    return _valid_uuid(document.get("project_id")), metadata
        except (OSError, ProjectRepositoryError):
            return None
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
        """Consolidate actual copies before splitting legacy identity collisions."""
        with self.autosave_lock():
            return self._repair_duplicate_autosave_ids(Path(active_autosave_directory).resolve())

    def _repair_duplicate_autosave_ids(self, active: Path) -> tuple[tuple[Path, str], ...]:
        from saved_letter_reconciliation import AUTOSAVE_ARCHIVE, merge_duplicate

        if not active.is_dir():
            raise ProjectPathError("The active autosave folder does not exist.")
        active_id = self._metadata_project_id(active)
        if not active_id:
            raise ProjectPathError("The active project metadata has no valid project ID.")
        same_id = self.find_autosave_directories(active_id)
        if active not in same_id:
            raise ProjectPathError("The active autosave folder does not match the duplicate ID.")
        former_ids = {active_id, *self._autosave_original_ids(active)}
        title = self._directory_title(active).casefold()
        related = {}
        for path in active.parent.iterdir():
            if (path == active or not path.is_dir() or is_link_or_reparse_point(path)
                    or _is_transaction_artifact_directory(path)):
                continue
            related_id = bool(former_ids.intersection({self._metadata_project_id(path), *self._autosave_original_ids(path)}))
            if related_id or self._directory_title(path).casefold() == title:
                related[path] = related_id
        matches = tuple(dict.fromkeys((*same_id, *related)))
        if len(matches) < 2:
            return ()
        used_ids = {self._metadata_project_id(path) for path in self._all_autosave_directories()}
        repaired: list[tuple[Path, str]] = []
        for path in matches:
            if path == active:
                continue
            duplicate = self._confirmed_autosave_duplicate(active, path, repaired_identity=path not in same_id and related.get(path, False))
            if duplicate:
                canonical, original = duplicate
                merge_duplicate(self.autosave_root / AUTOSAVE_ARCHIVE, path, active,
                                original=original, canonical=canonical, project_id=active_id)
                repaired.append((path, active_id))
                continue
            if path not in same_id or (path / ".lettersmith-project").exists():
                continue
            # Old metadata-only collisions can be split safely. A content-bearing
            # autosave with unclear ownership stays intact for later inspection.
            active_metadata = self._read_metadata(active / PROJECT_METADATA_FILE)
            metadata_path = path / PROJECT_METADATA_FILE
            metadata = self._read_metadata(metadata_path)
            same_recipient = _valid_uuid(active_metadata.get("recipient_id")) == _valid_uuid(metadata.get("recipient_id"))
            if same_recipient and any((path / name).exists() for name in ("pages", "message", "sounds")):
                continue
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

    @classmethod
    def _autosave_original_ids(cls, path: Path) -> set[str]:
        result = set()
        for backup in path.glob(f"{PROJECT_METADATA_FILE}.backup-*"):
            try:
                identifier = _valid_uuid(cls._read_metadata(backup).get("project_id"))
                if identifier:
                    result.add(identifier)
            except ProjectPathError:
                continue
        return result

    @classmethod
    def _confirmed_autosave_duplicate(cls, active: Path, other: Path, *, repaired_identity: bool):
        from project_repository import ProjectRepositoryError
        from saved_letter_reconciliation import LetterEvidence, load_letter

        if any(is_link_or_reparse_point(path) for path in (active, other)):
            return None
        try:
            letters = []
            for path in (active, other):
                metadata = cls._read_metadata(path / PROJECT_METADATA_FILE)
                stored = load_letter(path, autosave=True)
                if not stored.project.pages:
                    return None
                letters.append(LetterEvidence.read(stored, project_id=metadata.get("project_id", ""),
                    recipient_id=metadata.get("recipient_id", ""), recipient=metadata.get("recipient_name", ""),
                    title=metadata.get("recipient_title", "")))
        except (OSError, ValueError, ProjectPathError, ProjectRepositoryError):
            return None
        first, second = letters
        if first.matches(second, repaired_identity=repaired_identity):
            return first.stored, second.stored
        return None

    def _all_autosave_directories(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for record in self.registry.list():
            root = self.resolve_autosave_recipient_directory(record.recipient_id)
            if root.is_dir():
                paths.extend(
                    child.resolve()
                    for child in root.iterdir()
                    if child.is_dir()
                    and not is_link_or_reparse_point(child)
                    and not _is_transaction_artifact_directory(child)
                )
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
