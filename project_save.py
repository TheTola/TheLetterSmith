from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from config import USER_MESSAGE_DIR, USER_PAGES_DIR
from message_history import write_message_with_revision
from project_paths import ProjectContext, ProjectPathResolver
from project_state import ProjectStateController
from readiness import (
    ProjectSaveEligibility,
    evaluate_project_save_eligibility,
)
from settings_store import SettingsStore
from sound_model import project_sound_path
from transactional_io import atomic_write_bytes


class ProjectSaveError(RuntimeError):
    pass


class ProjectNotReadyError(ProjectSaveError):
    pass


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

    def save_eligibility(self) -> ProjectSaveEligibility:
        return evaluate_project_save_eligibility(self.project_root)

    def can_save(self) -> bool:
        return self.save_eligibility().can_save

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
        target = (context.project_directory / relative).resolve()
        try:
            target.relative_to(context.project_directory.resolve())
        except ValueError as error:
            raise ProjectSaveError(
                "Project save path escaped the project directory."
            ) from error
        if target == context.project_directory.resolve():
            raise ProjectSaveError("Project save path must name a file.")
        return target

    def save_message(
        self,
        content: str,
        *,
        workspace_path: str | Path,
        reason: str,
    ) -> Path:
        workspace = Path(workspace_path).resolve()
        write_message_with_revision(
            workspace,
            content,
            reason=reason,
        )
        if not self.can_save():
            return workspace
        context = self.current_context()
        self.resolver.ensure_project_storage(context)
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
        if not self.can_save():
            return source
        context = self.current_context()
        self.resolver.ensure_project_storage(context)
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
        if not self.can_save():
            return ()
        context = self.current_context()
        self.resolver.ensure_project_storage(context)
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

    def save_workspace_snapshot(
        self,
        *,
        reason: str,
    ) -> Path:
        eligibility = self.save_eligibility()
        if not eligibility.can_save:
            raise ProjectNotReadyError(eligibility.blocked_reason)
        context = self.current_context()
        self.resolver.ensure_project_storage(context)
        self._copy_tree_to_context(
            context,
            self.project_root / USER_PAGES_DIR,
            Path("pages"),
        )
        self._copy_tree_to_context(
            context,
            self.project_root / USER_MESSAGE_DIR,
            Path("message"),
        )
        sound_state = project_sound_path(self.project_root)
        if sound_state.is_file():
            destination = self.project_file(
                context,
                Path("sounds") / "project_sound.json",
            )
            atomic_write_bytes(destination, sound_state.read_bytes())
        prompt_state = self.project_root / "prompt_writer_state.json"
        if prompt_state.is_file():
            destination = self.project_file(
                context,
                prompt_state.name,
            )
            atomic_write_bytes(destination, prompt_state.read_bytes())
        self._finish_save(
            context,
            updates={
                "autosave_reason": str(reason),
                "completed_tabs": list(eligibility.completed_tabs),
            },
        )
        return context.project_directory

    def _copy_tree_to_context(
        self,
        context: ProjectContext,
        source_root: Path,
        destination_root: Path,
    ) -> None:
        if not source_root.is_dir():
            return
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            destination = self.project_file(
                context,
                destination_root / source.relative_to(source_root),
            )
            atomic_write_bytes(destination, source.read_bytes())

    def _finish_save(
        self,
        context: ProjectContext,
        *,
        updates: dict | None = None,
    ) -> None:
        if self.project_state.identity != context.identity:
            raise ProjectSaveError(
                "The active project changed while saving."
            )
        metadata_updates = dict(updates or {})
        metadata_updates["last_saved_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        self.resolver.write_project_metadata(context, metadata_updates)


__all__ = [
    "ProjectNotReadyError",
    "ProjectSaveError",
    "ProjectSaveService",
]
