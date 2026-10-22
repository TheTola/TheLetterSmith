from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
from dataclasses import dataclass, field
from uuid import uuid4

from config import (
    MESSAGE_ASSETS_DIR,
    PLAY_METADATA_FILE,
    USER_MESSAGE_DIR,
    USER_PAGES_DIR,
    resolve_play_bundle_directory,
)
from message_html import sanitize_message_html
from message_history import write_message_with_revision
from performance_trace import performance_timed
from project_paths import (
    PROJECT_METADATA_SCHEMA_VERSION,
    ProjectContext,
    ProjectPathResolver,
)
from project_state import ProjectStateController
from project_timestamps import (
    PROJECT_CREATED_AT_KEY,
    PROJECT_PUBLISHED_AT_KEY,
)
from readiness import (
    ProjectSaveEligibility,
    evaluate_project_save_eligibility,
)
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore
from sound_model import project_sound_path
from transactional_io import (
    PathTransaction,
    atomic_copy_file,
    atomic_write_bytes,
    atomic_write_json,
    enforce_internal_tree_visibility,
    file_change_token,
)


_LOGGER = logging.getLogger(__name__)
_SNAPSHOT_MANIFEST_FILE = ".lettersmith-snapshot-manifest.json"
_SNAPSHOT_MANIFEST_VERSION = 2
_SNAPSHOT_COPY_ATTEMPTS = 3


class ProjectSaveError(RuntimeError):
    pass


class ProjectNotReadyError(ProjectSaveError):
    pass


@dataclass(frozen=True)
class EditableSave:
    project: object
    session_id: str
    content_revision: int
    directory: Path
    expected_revision: str | None
    asset_root: Path
    fallback_asset_root: Path | None
    copy: bool = False
    migration_report: dict = field(default_factory=dict)


class ProjectSaveService:
    """State-gated persistence for recipient-owned project files."""

    def __init__(
        self,
        project_root: str | Path,
        project_state: ProjectStateController,
        *,
        resolver: ProjectPathResolver | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.project_state = project_state
        self.resolver = resolver or ProjectPathResolver(self.project_root)
        self.settings = SettingsStore(self.project_root)
        self._editable_binding = None

    @property
    def editable_root(self) -> Path:
        from project_paths import application_paths
        return application_paths(self.project_root).saved_letters_root / "Editable Projects"

    @property
    def _binding_path(self) -> Path:
        return self.project_root / ".lettersmith-project" / "editable-save.json"

    def _binding(self, session):
        if self._editable_binding is None:
            try:
                value = json.loads(self._binding_path.read_text(encoding="utf-8"))
                if value["project_id"] == session.project.project_id:
                    value["session_id"] = session.session_id
                    self._editable_binding = value
            except (OSError, ValueError, KeyError, TypeError):
                pass
        value = self._editable_binding
        return value if value and value["session_id"] == session.session_id else None

    def _bind(self, session, directory, revision, content_revision):
        self._editable_binding = {"session_id": session.session_id, "project_id": session.project.project_id,
                                  "directory": str(directory), "revision": revision, "content_revision": content_revision}
        try:
            atomic_write_json(self._binding_path, self._editable_binding)
        except OSError:
            _LOGGER.warning("Saved project committed; its session binding could not be cached", exc_info=True)

    def has_unsaved_editable_changes(self) -> bool:
        from project_session import get_project_session
        session = get_project_session(self.project_root)
        binding = self._binding(session)
        return binding is None or binding["content_revision"] != session.project.revision

    def capture_editable_save(self, *, copy: bool = False, title: str | None = None) -> EditableSave:
        """Call on the GUI thread after all editors have committed pending fields."""
        from project_model import ProjectModel
        from project_repository import _json
        from project_session import get_project_session
        session = get_project_session(self.project_root)
        session.synchronize_workspace()
        with session._lock, session.project.mutation_scope():
            if session.is_restoring or session._closed:
                raise ProjectSaveError("Wait for the project transition before saving")
            project = ProjectModel.from_dict(session.project.to_dict())
            revision = project.revision
            binding = self._binding(session)
            if copy:
                project = project.independent_copy(title=title)
            elif title is not None:
                project.update_project_data("metadata", {"title": title, "recipient_title": title})
            directory = Path(binding["directory"]) if binding and not copy else self.editable_root / project.project_id
            self._validate_editable_directory(directory)
            return EditableSave(project, session.session_id, revision, directory,
                                binding["revision"] if binding and not copy else None,
                                self.project_root, session.asset_root, copy,
                                _json((session.asset_root or self.project_root) / "migration.json", optional=True))

    def commit_editable_save(self, capture: EditableSave):
        """Worker-safe canonical commit. No editor or library index mutation."""
        from project_model import ProjectModel
        from project_repository import ProjectRepository, _writer_lock
        self._validate_editable_directory(capture.directory)
        payload = capture.project.to_dict()
        payload["metadata"]["saved_at"] = datetime.now(timezone.utc).isoformat()
        with _writer_lock(self.editable_root / ".library-lock"):
            return ProjectRepository(capture.directory / ".lettersmith-project").save(
                ProjectModel.from_dict(payload), asset_root=capture.asset_root,
                fallback_asset_root=capture.fallback_asset_root, expected_revision=capture.expected_revision,
                migration_report=capture.migration_report)

    def complete_editable_save(self, capture: EditableSave, stored) -> bool:
        """Bind only the originating live session; newer authored edits stay dirty."""
        from project_session import get_project_session
        session = get_project_session(self.project_root)
        if capture.copy or session.session_id != capture.session_id or session.project.project_id != capture.project.project_id:
            return False
        self._bind(session, capture.directory, stored.revision, capture.content_revision)
        return session.project.revision == capture.content_revision

    def save_editable(self, *, copy: bool = False, title: str | None = None):
        capture = self.capture_editable_save(copy=copy, title=title)
        stored = self.commit_editable_save(capture)
        self.complete_editable_save(capture, stored)
        return capture.directory, stored

    def _validate_editable_directory(self, directory: Path):
        directory = Path(directory)
        from transactional_io import is_link_or_reparse_point
        from project_repository import ProjectRepository
        managed = self.editable_root.parent.resolve()
        resolved = directory.resolve()
        if resolved.is_relative_to(managed) and resolved != managed:
            ancestors = (directory, *directory.parents)
            if any(is_link_or_reparse_point(path) for path in ancestors if path.resolve().is_relative_to(managed)):
                raise ProjectSaveError("Editable saves must not use linked folders")
            if ProjectRepository(directory / ".lettersmith-project").exists:
                return
        root = self.editable_root.resolve()
        if directory.is_symlink() or directory.resolve().parent != root or directory.parent.is_symlink():
            raise ProjectSaveError("Editable saves must remain in the managed project library")
        try:
            from uuid import UUID
            UUID(directory.name)
        except ValueError as error:
            raise ProjectSaveError("Editable save destinations use project identities, not titles") from error

    def read_saved_project(self, entry):
        """Validate/migrate a candidate without activating it or changing its source."""
        from project_repository import ProjectRepository, StoredProject, read_legacy_project
        from saved_letters import SavedLetterRestorer
        restorer = SavedLetterRestorer(self.project_root)
        source = Path(entry.path)
        if not source.is_dir():
            raise ProjectSaveError("The selected saved location is missing; restore it before opening")
        if source.is_symlink() or not any(source.resolve().is_relative_to(root.resolve()) and source.resolve() != root.resolve()
                                         for root in restorer.allowed_roots):
            raise ProjectSaveError("The selected project is outside the managed library")
        repository = ProjectRepository(source / ".lettersmith-project")
        if repository.directory.exists() or getattr(entry, "canonical", False):
            loaded = repository.load(allow_missing=True)
            if entry.storage_revision and loaded.revision != entry.storage_revision:
                raise ProjectSaveError("The saved version changed; refresh the library before continuing")
            return loaded
        # The migration reader owns historical type interpretation and exact strings.
        migrated = read_legacy_project(source, saved=True)
        return StoredProject(migrated.project, "", source, migrated.report.to_dict())

    def open_saved_project(self, entry, *, loaded=None):
        from dataclasses import replace
        from project_session import get_project_session
        from saved_letters import SavedLetterRestorer
        loaded = loaded or self.read_saved_project(entry)
        independent = entry.example or entry.stock or entry.recovery
        if independent:
            loaded = replace(loaded, project=loaded.project.independent_copy())
        elif not loaded.revision:
            loaded = self._convert_saved_project(entry, loaded)
        stored = SavedLetterRestorer(self.project_root).restore_canonical(loaded)
        session = get_project_session(self.project_root)
        if not independent:
            self._validate_editable_directory(entry.path)
            self._bind(session, entry.path, loaded.revision, session.project.revision)
        else:
            self._bind(session, self.editable_root / session.project.project_id, None, -1)
        self.project_state.initialize()
        return stored

    def _convert_saved_project(self, entry, loaded):
        """Publish a complete canonical repository beside the untouched old files."""
        import tempfile
        from uuid import UUID
        from project_model import ProjectModel
        from project_repository import ProjectRepository, _writer_lock
        source = Path(entry.path)
        destination = source / ".lettersmith-project"
        with _writer_lock(self.editable_root / ".library-lock"):
            if destination.exists():
                raise ProjectSaveError("The saved project changed while opening; open it again")
            with tempfile.TemporaryDirectory(prefix=".s-convert-", dir=source.parent) as temporary:
                staging = Path(temporary) / ".lettersmith-project"
                repository = ProjectRepository(staging)
                document = loaded.project.to_dict()
                try:
                    UUID(document["project_id"])
                except ValueError:
                    # Old names are retained in the migration snapshot. The
                    # settings bridge requires a UUID to keep the save binding.
                    document["project_id"] = str(uuid4())
                repository.save(ProjectModel.from_dict(document), asset_root=loaded.asset_root,
                                migration_report=loaded.migration_report)
                repository.load(allow_missing=True)
                os.rename(staging, destination)
        return ProjectRepository(destination).load(allow_missing=True)

    def duplicate_saved_project(self, entry, *, title: str | None = None):
        loaded = self.read_saved_project(entry)
        project = loaded.project.independent_copy(title=title or entry.title + " — Copy")
        capture = EditableSave(project, "", project.revision, self.editable_root / project.project_id,
                               None, loaded.asset_root, None, True, loaded.migration_report)
        return capture.directory, self.commit_editable_save(capture)

    def rename_saved_project(self, entry, title: str):
        from project_repository import ProjectRepository, _writer_lock
        self._validate_editable_directory(entry.path)
        if not title.strip():
            raise ProjectSaveError("Enter a project title")
        loaded = self.read_saved_project(entry)
        if loaded.revision != entry.storage_revision:
            raise ProjectSaveError("The saved version changed; refresh the library before renaming")
        loaded.project.update_project_data("metadata", {"title": title, "recipient_title": title,
                                                        "saved_at": datetime.now(timezone.utc).isoformat()})
        with _writer_lock(self.editable_root / ".library-lock"):
            return ProjectRepository(entry.path / ".lettersmith-project").save(
                loaded.project, asset_root=loaded.asset_root, expected_revision=entry.storage_revision)

    def trash_saved_project(self, entry) -> Path:
        """Move only the selected owned save, retaining every generation for recovery."""
        from project_paths import application_paths
        from project_repository import ProjectRepository, _writer_lock
        self._validate_editable_directory(entry.path)
        repository = ProjectRepository(entry.path / ".lettersmith-project")
        # The lock lives outside the moved tree, while the repository lock verifies
        # the last observed generation. No shared source asset is removed.
        with _writer_lock(self.editable_root / ".library-lock"):
            with _writer_lock(repository.directory):
                if repository._head().get("revision") != entry.storage_revision:
                    raise ProjectSaveError("The saved version changed; refresh before deleting")
            destination = application_paths(self.project_root).recovery_root / ("Deleted Project " + uuid4().hex)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(entry.path, destination)
        return destination

    def save_eligibility(self) -> ProjectSaveEligibility:
        return evaluate_project_save_eligibility(self.project_root)

    def prepare_new_project(self, page_types):
        """Validate an empty starter without changing the active session."""
        from project_model import MIN_PAGES, MAX_PAGES, create_project
        from project_repository import StoredProject
        kinds = tuple(page_types)
        if not MIN_PAGES <= len(kinds) <= MAX_PAGES:
            raise ProjectSaveError(f"Choose between {MIN_PAGES} and {MAX_PAGES} pages")
        project = create_project(kinds, metadata={
            PROJECT_CREATED_AT_KEY: datetime.now(timezone.utc).isoformat(),
        })
        # There are no source references in a fresh factory document.
        return StoredProject(project, "", self.project_root, {})

    def activate_new_project(self, candidate):
        """Use the same transactional replacement as Open; retain prior generations."""
        from project_session import get_project_session
        from saved_letters import SavedLetterRestorer
        stored = SavedLetterRestorer(self.project_root).restore_canonical(candidate)
        session = get_project_session(self.project_root)
        self._bind(session, self.editable_root / session.project.project_id, None, -1)
        self.project_state.initialize()
        return stored

    def prepare_discard_project(self):
        """Exit restores the explicit save, or a fresh draft, without deleting recovery."""
        from project_model import CLASSIC_PAGE_TYPES
        from project_repository import ProjectRepository
        from project_session import get_project_session
        binding = self._binding(get_project_session(self.project_root))
        if binding and binding.get("revision"):
            directory = Path(binding["directory"])
            self._validate_editable_directory(directory)
            return ProjectRepository(directory / ".lettersmith-project").load(allow_missing=True), directory
        return self.prepare_new_project(CLASSIC_PAGE_TYPES), None

    def discard_active_changes(self, prepared):
        from project_session import get_project_session
        from saved_letters import SavedLetterRestorer
        candidate, directory = prepared
        stored = SavedLetterRestorer(self.project_root).restore_canonical(candidate)
        session = get_project_session(self.project_root)
        self._bind(session, directory or self.editable_root / session.project.project_id,
                   candidate.revision if directory else None, session.project.revision if directory else -1)
        self.project_state.initialize()
        return stored

    def can_save(self) -> bool:
        return self.save_eligibility().can_save

    def save_project(self, directory: str | Path | None = None, *, expected_revision: str | None = None):
        """Save the canonical authored draft, independently of output readiness."""
        from project_session import get_project_session
        stored = get_project_session(self.project_root).save()
        if directory is None:
            return stored
        from project_repository import ProjectRepository
        return ProjectRepository(directory).save(stored.project, asset_root=stored.asset_root,
                                                  expected_revision=expected_revision)

    def load_project(self, directory: str | Path):
        """Activate a portable canonical draft after complete source validation.

        ``directory`` is a ProjectRepository directory, or a viewer package
        containing .lettersmith-project. The source is never modified. Existing
        UI consumers project this model through the shared compatibility bridge.
        """
        from project_repository import ProjectRepository
        from saved_letters import SavedLetterRestorer
        source = ProjectRepository(directory)
        if not source.exists:
            source = ProjectRepository(Path(directory) / ".lettersmith-project")
        loaded = source.load()
        stored = SavedLetterRestorer(self.project_root).restore_canonical(loaded)
        self.project_state.initialize()
        return stored

    def current_context(self) -> ProjectContext:
        try:
            identity = self.project_state.require_ready()
        except RuntimeError as error:
            raise ProjectNotReadyError(str(error)) from error
        context = self.resolver.context_from_settings(
            self.settings.snapshot()
        )
        if context.identity != identity:
            raise ProjectNotReadyError(
                "Active settings do not match the ready project."
            )
        return context

    def project_file(
        self,
        context: ProjectContext,
        relative_path: str | Path,
    ) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ProjectSaveError(
                "Project save path must remain inside the project."
            )
        target = (context.autosave_directory / relative).resolve()
        try:
            target.relative_to(context.autosave_directory.resolve())
        except ValueError as error:
            raise ProjectSaveError(
                "Project save path escaped the project directory."
            ) from error
        if target == context.autosave_directory.resolve():
            raise ProjectSaveError("Project save path must name a file.")
        return target

    def save_message(
        self,
        content: str,
        *,
        workspace_path: str | Path,
        reason: str,
    ) -> Path:
        content = sanitize_message_html(content)
        workspace = Path(workspace_path).resolve()
        write_message_with_revision(
            workspace,
            content,
            reason=reason,
        )
        self.save_project()
        if not self.can_save():
            return workspace
        context = self.current_context()
        self.resolver.ensure_autosave_storage(context)
        destination = self.project_file(
            context,
            Path("message") / "message.html",
        )
        if workspace != destination:
            write_message_with_revision(
                destination,
                content,
                reason=reason,
            )
        self._finish_save(context)
        return destination

    def copy_workspace_file(
        self,
        workspace_path: str | Path,
        project_relative_path: str | Path,
    ) -> Path:
        source = Path(workspace_path).resolve()
        if not source.is_file():
            raise ProjectSaveError(
                f"Workspace file does not exist: {source}"
            )
        self.save_project()
        if not self.can_save():
            return source
        context = self.current_context()
        self.resolver.ensure_autosave_storage(context)
        destination = self.project_file(
            context,
            project_relative_path,
        )
        atomic_write_bytes(destination, source.read_bytes())
        self._finish_save(context)
        return destination

    def copy_workspace_tree(
        self,
        workspace_directory: str | Path,
        project_relative_directory: str | Path,
    ) -> tuple[Path, ...]:
        source_root = Path(workspace_directory).resolve()
        if not source_root.is_dir():
            return ()
        self.save_project()
        if not self.can_save():
            return ()
        context = self.current_context()
        self.resolver.ensure_autosave_storage(context)
        copied: list[Path] = []
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(source_root)
            destination = self.project_file(
                context,
                Path(project_relative_directory) / relative,
            )
            atomic_write_bytes(destination, source.read_bytes())
            copied.append(destination)
        self._finish_save(context)
        return tuple(copied)

    def delete_project_file(
        self,
        project_relative_path: str | Path,
    ) -> bool:
        if not self.can_save():
            return False
        context = self.current_context()
        destination = self.project_file(
            context,
            project_relative_path,
        )
        if not destination.is_file():
            return False
        destination.unlink()
        self._finish_save(context)
        return True

    @performance_timed("project.save_workspace_snapshot")
    def save_workspace_snapshot(
        self,
        *,
        reason: str,
        defer_play_metadata: bool = False,
    ) -> Path:
        eligibility = self.save_eligibility()
        if not eligibility.can_save:
            raise ProjectNotReadyError(eligibility.blocked_reason)
        stored_project = self.save_project()
        context = self.current_context()
        transaction = PathTransaction(
            context.autosave_directory,
            staging_suffix=".snapshot-staging",
            backup_suffix=".snapshot-backup",
            unique_staging=True,
        )
        staging = transaction.prepare()
        try:
            previous_manifest = self._read_snapshot_manifest(
                context.autosave_directory
            )
            snapshot_manifest: dict[str, dict[str, list[int]]] = {}
            if context.autosave_directory.is_dir():
                self._copy_preserved_snapshot_entries(
                    context.autosave_directory,
                    staging,
                )
            else:
                staging.mkdir(parents=True)
            from project_repository import ProjectRepository
            canonical = ProjectRepository(staging / ".lettersmith-project")
            previous_revision = canonical.load().revision if canonical.exists else None
            canonical.save(stored_project.project, asset_root=stored_project.asset_root,
                           expected_revision=previous_revision)
            staged_context = ProjectContext(
                recipient_id=context.recipient_id,
                recipient_display_name=context.recipient_display_name,
                recipient_normalized_key=context.recipient_normalized_key,
                project_id=context.project_id,
                letter_title=context.letter_title,
                recipient_directory=context.recipient_directory,
                autosave_directory=staging,
            )
            self._replace_staged_tree(
                staging,
                context.autosave_directory,
                self.project_root / USER_PAGES_DIR,
                Path("pages"),
                previous_manifest,
                snapshot_manifest,
            )
            self._replace_staged_tree(
                staging,
                context.autosave_directory,
                self.project_root / USER_MESSAGE_DIR,
                Path("message"),
                previous_manifest,
                snapshot_manifest,
            )
            self._replace_staged_tree(
                staging,
                context.autosave_directory,
                self.project_root / MESSAGE_ASSETS_DIR,
                Path(MESSAGE_ASSETS_DIR),
                previous_manifest,
                snapshot_manifest,
            )
            self._replace_staged_file(
                staging,
                context.autosave_directory,
                project_sound_path(self.project_root),
                Path("sounds") / "project_sound.json",
                previous_manifest,
                snapshot_manifest,
            )
            prompt_state = self.project_root / "prompt_writer_state.json"
            self._replace_staged_file(
                staging,
                context.autosave_directory,
                prompt_state,
                Path(prompt_state.name),
                previous_manifest,
                snapshot_manifest,
            )
            atomic_write_json(
                staging / _SNAPSHOT_MANIFEST_FILE,
                {
                    "schema_version": _SNAPSHOT_MANIFEST_VERSION,
                    "files": snapshot_manifest,
                },
            )
            self._finish_save(
                staged_context,
                updates={
                    "autosave_reason": str(reason),
                    "completed_tabs": list(eligibility.completed_tabs),
                },
                update_play_metadata=not defer_play_metadata,
            )
            transaction.commit(
                keep_backup=True,
                validator=lambda directory: (
                    self.resolver._metadata_project_id(directory)
                    == context.project_id
                ),
            )
        except Exception:
            transaction.abort()
            raise
        transaction.finalize()
        try:
            self._refresh_committed_snapshot_manifest(
                context.autosave_directory,
                snapshot_manifest,
            )
        except OSError:
            # The manifest is only a reuse hint. A stale stored signature makes
            # the next snapshot copy the source again instead of risking data.
            _LOGGER.warning(
                "Could not refresh project snapshot reuse metadata: %s",
                context.autosave_directory,
                exc_info=True,
            )
        enforce_internal_tree_visibility(context.autosave_directory)
        return context.autosave_directory

    def finish_deferred_workspace_snapshot(self) -> None:
        """Apply GUI-observed settings updates after a worker snapshot."""
        self._finish_play_metadata(self.current_context())

    @staticmethod
    def _link_or_copy_file(source: str | Path, destination: str | Path) -> bool:
        """Reuse a writable file by link, otherwise make an independent copy."""
        source_path = Path(source)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_is_writable = bool(source_path.stat().st_mode & stat.S_IWRITE)
        except OSError:
            source_is_writable = False
        if source_is_writable:
            try:
                os.link(source_path, destination_path)
                return True
            except OSError:
                pass
        atomic_copy_file(source_path, destination_path)
        return False

    @classmethod
    def _copy_preserved_snapshot_entries(
        cls,
        existing_root: Path,
        staging: Path,
    ) -> None:
        """Preserve unowned autosave entries without cloning managed trees."""
        staging.mkdir(parents=True)
        replaced_paths = (
            Path(".lettersmith-project"),
            Path("pages"),
            Path("message"),
            Path(MESSAGE_ASSETS_DIR),
            Path("sounds") / "project_sound.json",
            Path("prompt_writer_state.json"),
            Path(_SNAPSHOT_MANIFEST_FILE),
        )

        def is_replaced(relative: Path) -> bool:
            return any(
                relative == replaced or replaced in relative.parents
                for replaced in replaced_paths
            )

        for source in sorted(existing_root.rglob("*")):
            relative = source.relative_to(existing_root)
            if is_replaced(relative):
                continue
            destination = staging / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                cls._link_or_copy_file(source, destination)

    @staticmethod
    def _source_signature(path: Path) -> list[int]:
        stat_result = path.stat()
        return [
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            file_change_token(path, stat_result=stat_result),
        ]

    @staticmethod
    def _stored_signature(path: Path) -> list[int]:
        stat_result = path.stat()
        return [
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            file_change_token(path, stat_result=stat_result),
        ]

    @classmethod
    def _read_snapshot_manifest(
        cls,
        autosave_directory: Path,
    ) -> dict[str, dict[str, list[int]]]:
        path = autosave_directory / _SNAPSHOT_MANIFEST_FILE
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if int(payload.get("schema_version", 0)) != _SNAPSHOT_MANIFEST_VERSION:
                return {}
            files = payload.get("files")
            if not isinstance(files, dict):
                return {}
            validated: dict[str, dict[str, list[int]]] = {}
            for raw_relative, raw_entry in files.items():
                relative = Path(str(raw_relative))
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not isinstance(raw_entry, dict)
                ):
                    return {}
                source = raw_entry.get("source")
                stored = raw_entry.get("stored")
                if (
                    not isinstance(source, list)
                    or len(source) != 3
                    or not all(isinstance(value, int) for value in source)
                    or not isinstance(stored, list)
                    or len(stored) != 3
                    or not all(isinstance(value, int) for value in stored)
                ):
                    return {}
                validated[relative.as_posix()] = {
                    "source": list(source),
                    "stored": list(stored),
                }
            return validated
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            _LOGGER.warning(
                "Ignoring an invalid project snapshot manifest: %s",
                path,
                exc_info=True,
            )
            return {}

    @classmethod
    def _reusable_stored_signature(
        cls,
        source_signature: list[int],
        stored: Path,
        relative_key: str,
        previous_manifest: dict[str, dict[str, list[int]]],
    ) -> list[int] | None:
        entry = previous_manifest.get(relative_key)
        if entry is None or not stored.is_file():
            return None
        try:
            stored_signature = cls._stored_signature(stored)
        except OSError:
            return None
        if (
            entry.get("source") != source_signature
            or entry.get("stored") != stored_signature
        ):
            return None
        return stored_signature

    @classmethod
    def _copy_stable_snapshot_file(
        cls,
        source: Path,
        existing: Path,
        destination: Path,
        relative_key: str,
        previous_manifest: dict[str, dict[str, list[int]]],
    ) -> dict[str, list[int]]:
        """Copy bytes that correspond to one stable source signature."""
        for _attempt in range(_SNAPSHOT_COPY_ATTEMPTS):
            destination.unlink(missing_ok=True)
            source_signature = cls._source_signature(source)
            stored_signature = cls._reusable_stored_signature(
                source_signature,
                existing,
                relative_key,
                previous_manifest,
            )
            if stored_signature is not None:
                stored_link_count = int(existing.stat().st_nlink)
                linked = cls._link_or_copy_file(existing, destination)
                try:
                    source_after = cls._source_signature(source)
                    existing_after = cls._stored_signature(existing)
                    destination_after = cls._stored_signature(destination)
                    existing_link_count_after = int(existing.stat().st_nlink)
                except OSError:
                    continue
                if linked:
                    # Creating the hardlink itself advances ChangeTime on NTFS
                    # and ctime on POSIX. Attribute that expected token change
                    # to the observed one-link increase while still requiring
                    # the content-bearing size/mtime fields to remain stable.
                    stored_stable = (
                        existing_after[:2] == stored_signature[:2]
                        and existing_link_count_after
                        == stored_link_count + 1
                    )
                    destination_matches = (
                        destination_after[:2] == existing_after[:2]
                    )
                else:
                    stored_stable = existing_after == stored_signature
                    destination_matches = (
                        destination_after[:2] == existing_after[:2]
                    )
                if (
                    source_after == source_signature
                    and stored_stable
                    and destination_matches
                ):
                    return {
                        "source": source_signature,
                        "stored": destination_after,
                    }
                continue

            atomic_copy_file(source, destination)
            if cls._source_signature(source) == source_signature:
                return {
                    "source": source_signature,
                    "stored": cls._stored_signature(destination),
                }

        destination.unlink(missing_ok=True)
        raise ProjectSaveError(
            f"Project file kept changing while it was being saved: {source}"
        )

    @classmethod
    def _refresh_committed_snapshot_manifest(
        cls,
        autosave_directory: Path,
        snapshot_manifest: dict[str, dict[str, list[int]]],
    ) -> None:
        refreshed: dict[str, dict[str, list[int]]] = {}
        for relative_key, entry in snapshot_manifest.items():
            stored = autosave_directory / Path(relative_key)
            refreshed[relative_key] = {
                "source": list(entry["source"]),
                "stored": cls._stored_signature(stored),
            }
        atomic_write_json(
            autosave_directory / _SNAPSHOT_MANIFEST_FILE,
            {
                "schema_version": _SNAPSHOT_MANIFEST_VERSION,
                "files": refreshed,
            },
        )

    @classmethod
    def _replace_staged_tree(
        cls,
        staging: Path,
        existing_root: Path,
        source_root: Path,
        destination_root: Path,
        previous_manifest: dict[str, dict[str, list[int]]],
        snapshot_manifest: dict[str, dict[str, list[int]]],
    ) -> None:
        destination = staging / destination_root
        if destination.exists():
            shutil.rmtree(destination)
        if not source_root.is_dir():
            return
        destination.mkdir(parents=True)
        directories = sorted(
            (path for path in source_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
        )
        for directory in directories:
            (destination / directory.relative_to(source_root)).mkdir(
                parents=True,
                exist_ok=True,
            )
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = destination_root / source.relative_to(source_root)
            relative_key = relative.as_posix()
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = existing_root / relative
            snapshot_manifest[relative_key] = cls._copy_stable_snapshot_file(
                source,
                existing,
                target,
                relative_key,
                previous_manifest,
            )

    @classmethod
    def _replace_staged_file(
        cls,
        staging: Path,
        existing_root: Path,
        source: Path,
        destination_relative: Path,
        previous_manifest: dict[str, dict[str, list[int]]],
        snapshot_manifest: dict[str, dict[str, list[int]]],
    ) -> None:
        destination = staging / destination_relative
        destination.unlink(missing_ok=True)
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            relative_key = destination_relative.as_posix()
            existing = existing_root / destination_relative
            snapshot_manifest[relative_key] = cls._copy_stable_snapshot_file(
                source,
                existing,
                destination,
                relative_key,
                previous_manifest,
            )

    def _finish_save(
        self,
        context: ProjectContext,
        *,
        updates: dict | None = None,
        update_play_metadata: bool = True,
    ) -> None:
        if self.project_state.identity != context.identity:
            raise ProjectSaveError(
                "The active project changed while saving."
            )
        metadata_updates = dict(updates or {})
        settings = self.settings.snapshot()
        metadata_updates.update(
            {
                PROJECT_CREATED_AT_KEY: str(
                    settings.get(PROJECT_CREATED_AT_KEY, "")
                ).strip(),
                PROJECT_PUBLISHED_AT_KEY: str(
                    settings.get(PROJECT_PUBLISHED_AT_KEY, "")
                ).strip(),
            }
        )
        metadata_updates["last_saved_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        self.resolver.write_autosave_metadata(context, metadata_updates)
        _LOGGER.debug(
            "Project autosave metadata updated for project_id=%s.",
            context.project_id,
        )
        if update_play_metadata:
            self._finish_play_metadata(context)

    def _finish_play_metadata(self, context: ProjectContext) -> None:
        if self.project_state.identity != context.identity:
            raise ProjectSaveError(
                "The active project changed while saving."
            )
        try:
            play_directory = resolve_play_bundle_directory(
                self.project_root,
                recipient=context.recipient_display_name,
                title=context.letter_title,
                project_id=context.project_id,
            )
        except (OSError, ValueError) as error:
            raise ProjectSaveError(
                "The generated letter could not follow the updated title: "
                f"{error}"
            ) from error
        if (play_directory / "lettersmith-display.json").is_file():
            return
        if (play_directory / "index.html").is_file():
            settings = self.settings.snapshot()
            metadata_path = play_directory / PLAY_METADATA_FILE
            existing_metadata: dict = {}
            if metadata_path.is_file():
                try:
                    value = self.resolver._read_metadata(metadata_path)
                    existing_metadata = dict(value)
                except Exception as error:
                    raise ProjectSaveError(
                        "The generated letter metadata could not be read: "
                        f"{error}"
                    ) from error
            existing_metadata.update(
                {
                    "project_id": context.project_id,
                    "project_schema_version": (
                        PROJECT_METADATA_SCHEMA_VERSION
                    ),
                    "recipient_id": context.recipient_id,
                    "recipient_display_name": (
                        context.recipient_display_name
                    ),
                    "recipient_normalized_key": (
                        context.recipient_normalized_key
                    ),
                    "recipient_name": context.recipient_display_name,
                    "recipient_title": context.letter_title,
                    PROJECT_CREATED_AT_KEY: str(
                        settings.get(
                            PROJECT_CREATED_AT_KEY,
                            "",
                        )
                    ).strip(),
                    PROJECT_PUBLISHED_AT_KEY: str(
                        settings.get(
                            PROJECT_PUBLISHED_AT_KEY,
                            "",
                        )
                    ).strip(),
                }
            )
            atomic_write_json(metadata_path, existing_metadata)
            self.settings.update_fields(
                {ACTIVE_PLAY_DIR_KEY: str(play_directory)}
            )


__all__ = [
    "ProjectNotReadyError",
    "ProjectSaveError",
    "ProjectSaveService",
]
