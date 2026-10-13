"""Shared editor selection and target guards for a canonical project.

Sessions are runtime-only. Saving never persists selection, jobs or listeners.
Use get_project_session with a workspace root to share one context across tabs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Callable, Iterator, Mapping
import uuid

from project_model import ProjectModel, create_project

_LOGGER = logging.getLogger(__name__)


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _input_value(field: str, value: object) -> object:
    if field == 'prompt' and isinstance(value, dict):
        return {key: item for key, item in value.items() if key != 'stale'}
    return value


@dataclass(frozen=True)
class SessionChange:
    kind: str
    page_ids: tuple[str, ...]
    revision: int
    session_id: str


@dataclass(frozen=True)
class WorkTarget:
    session_id: str
    project_id: str
    page_id: str
    fields: tuple[str, ...]
    include_shared: bool
    signature: str
    input_versions: tuple[int, ...]


class ProjectSession:
    """A model plus non-authored selection and a replacement-generation ID."""

    def __init__(self, project: ProjectModel, *, project_root: str | Path | None = None,
                 storage_revision: str | None = None, asset_root: str | Path | None = None):
        self._lock = RLock()
        self._closed = False
        self.project_root = Path(project_root).resolve() if project_root is not None else None
        self.storage_revision = storage_revision
        self.asset_root = Path(asset_root).resolve() if asset_root is not None else self.project_root
        self._listeners: list[Callable[[SessionChange], None]] = []
        self._suspend_depth = 0
        self._pending = False
        self.session_id = str(uuid.uuid4())
        self.project = project
        self.selected_page_id: str | None = None
        self._last_pages: dict[str, dict] = {}
        self._remember_inputs()
        self.project.add_listener(self._on_model_change)

    def _remember_inputs(self) -> None:
        payload = self.project.to_dict()
        self._last_pages = {page['id']: page for page in payload['pages']}

    def add_listener(self, listener: Callable[[SessionChange], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[SessionChange], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _publish(self, kind: str, page_ids: tuple[str, ...] = ()) -> None:
        if self._suspend_depth:
            self._pending = True
            return
        event = SessionChange(kind, page_ids, self.project.revision, self.session_id)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception('Project session listener failed after %s', kind)

    def _on_model_change(self, event: object) -> None:
        with self._lock:
            if self._closed or getattr(event, 'source_model_id', None) not in (None, id(self.project)):
                return
            self._remember_inputs()
            selection_changed = self.selected_page_id is not None and self.selected_page_id not in self._last_pages
            if selection_changed:
                self.selected_page_id = next(iter(self._last_pages), None)
            self._publish(event.kind, tuple(event.page_ids))
            if selection_changed:
                self._publish('selection', (self.selected_page_id,) if self.selected_page_id else ())

    @contextmanager
    def restoring(self) -> Iterator[None]:
        """Serialize restoration with saves/work and publish completed state once."""
        with self._lock:
            self._suspend_depth += 1
            try:
                yield
            finally:
                self._suspend_depth -= 1
                if not self._suspend_depth and self._pending:
                    self._pending = False
                    self._publish('project_replaced', tuple(self._last_pages))

    @property
    def is_restoring(self) -> bool:
        return bool(self._suspend_depth)

    def select_page(self, page_id: str | None) -> None:
        with self._lock:
            if page_id is not None:
                self.project.get_page(page_id)
            if self.selected_page_id != page_id:
                self.selected_page_id = page_id
                self._publish('selection', (page_id,) if page_id else ())

    def replace(self, project: ProjectModel, *, storage_revision: str | None = None,
                asset_root: str | Path | None = None) -> None:
        # Validate and copy before touching the active session. Even A -> B -> A
        # gets a new session ID, so old jobs can never attach to the returned A.
        replacement = ProjectModel.from_dict(project.to_dict())
        with self._lock:
            self.project.remove_listener(self._on_model_change)
            self.project = replacement
            if storage_revision is not None:
                self.storage_revision = storage_revision
            if asset_root is not None:
                self.asset_root = Path(asset_root).resolve()
            self.session_id = str(uuid.uuid4())
            self._remember_inputs()
            self.selected_page_id = next(iter(self._last_pages), None)
            self.project.add_listener(self._on_model_change)
            self._publish('project_replaced', tuple(self._last_pages))

    def snapshot(self) -> Mapping:
        """Return recursively read-only authored state."""
        return _freeze(self.project.to_dict())

    def _target_signature(self, page_id: str, fields: tuple[str, ...], include_shared: bool) -> str:
        page = self.project.get_page(page_id)
        values = {key: _input_value(key, page.get(key)) for key in fields}
        if include_shared:
            payload = self.project.to_dict()
            values['$shared'] = {key: payload.get(key, {}) for key in ('shared_settings', 'prompt_settings', 'presentation')}
        return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode('utf-8')).hexdigest()

    def _versions(self, page_id: str, fields: tuple[str, ...], include_shared: bool) -> tuple[int, ...]:
        return self.project.input_versions(page_id, fields, include_shared=include_shared)

    def capture_target(self, page_id: str, *,
                       fields: tuple[str, ...] = ('type', 'artwork', 'prompt', 'message', 'design_overrides'),
                       include_shared: bool = True) -> WorkTarget:
        with self._lock, self.project.mutation_scope():
            if self._closed:
                raise RuntimeError('The project session is closed.')
            if self.is_restoring:
                raise RuntimeError('Cannot start page work while a project restoration is in progress.')
            return WorkTarget(self.session_id, self.project.project_id, page_id, tuple(fields),
                              include_shared, self._target_signature(page_id, tuple(fields), include_shared),
                              self._versions(page_id, tuple(fields), include_shared))

    def target_is_current(self, target: WorkTarget) -> bool:
        with self._lock, self.project.mutation_scope():
            if self._closed or self.is_restoring or target.session_id != self.session_id or target.project_id != self.project.project_id:
                return False
            try:
                versions = self._versions(target.page_id, target.fields, target.include_shared)
            except KeyError:
                return False
            return (target.input_versions == versions
                    and target.signature == self._target_signature(target.page_id, target.fields, target.include_shared))

    def apply_result(self, target: WorkTarget, apply: Callable[[ProjectModel, str], None]) -> bool:
        """Apply to the captured page, or return False without invoking apply."""
        with self._lock, self.project.mutation_scope():
            if not self.target_is_current(target):
                return False
            apply(self.project, target.page_id)
            return True

    def save(self, *, sync_workspace: bool = True):
        from project_repository import active_project_repository, activation_lock, activation_pending, ProjectRepositoryError, sync_legacy_workspace
        if self.project_root is None:
            raise ValueError('This session has no workspace; save through ProjectRepository.')
        with self._lock, activation_lock(self.project_root):
            if self._closed:
                raise RuntimeError('The project session is closed.')
            if self.is_restoring:
                raise RuntimeError('Cannot save while a project restoration is in progress.')
            if activation_pending(self.project_root):
                raise ProjectRepositoryError('incomplete_activation', 'Recover the interrupted project activation before saving.')
            if sync_workspace:
                with self.project.mutation_scope():
                    self.adopt(sync_legacy_workspace(self.project, self.project_root))
            saved_revision = self.project.revision
            stored = active_project_repository(self.project_root).save(
                self.project, asset_root=self.project_root, fallback_asset_root=self.asset_root,
                expected_revision=self.storage_revision)
            self.storage_revision = stored.revision
            self.asset_root = stored.asset_root
            with self.project.mutation_scope():
                if self.project.revision == saved_revision:
                    self.adopt(stored.project, kind='persisted')
            return stored

    def close(self) -> None:
        """Invalidate work retained by a closed window without changing disk."""
        with self._lock:
            self._closed = True
            self.session_id = str(uuid.uuid4())
            self.project.remove_listener(self._on_model_change)
            self._listeners.clear()

    def adopt(self, project: ProjectModel, *, kind: str = 'workspace') -> None:
        """Adopt a synchronized representation without replacing the session."""
        with self._lock:
            if project is self.project or project.to_dict() == self.project.to_dict():
                return
            if project.project_id != self.project.project_id:
                self.replace(project, storage_revision=self.storage_revision)
                return
            self.project.adopt_snapshot(project.to_dict(), kind=kind)

    def synchronize_workspace(self) -> None:
        """Apply changed legacy controls through their field-aware adapter."""
        from project_repository import sync_legacy_workspace, activation_lock, activation_pending, ProjectRepositoryError
        if self.project_root is None or self.is_restoring:
            return
        with self._lock, activation_lock(self.project_root), self.project.mutation_scope():
            if activation_pending(self.project_root):
                raise ProjectRepositoryError('incomplete_activation', 'Recover the interrupted project activation before reading editor views.')
            merged = sync_legacy_workspace(self.project, self.project_root)
            self.adopt(merged)


_sessions: dict[Path, ProjectSession] = {}
_registry_lock = RLock()


def peek_project_session(project_root: str | Path) -> ProjectSession | None:
    """Read an existing context without loading or migrating a workspace."""
    with _registry_lock:
        return _sessions.get(Path(project_root).resolve())


def get_project_session(project_root: str | Path) -> ProjectSession:
    """Get the shared context; loading/migration failure leaves registry untouched."""
    from project_repository import active_project_repository, read_legacy_project, activation_pending, ProjectRepositoryError
    root = Path(project_root).resolve()
    with _registry_lock:
        if root not in _sessions:
            repository = active_project_repository(root)
            if repository.exists:
                stored = repository.load()
                session = ProjectSession(stored.project, project_root=root,
                                         storage_revision=stored.revision, asset_root=stored.asset_root)
            else:
                if activation_pending(root):
                    raise ProjectRepositoryError('incomplete_activation', 'Interrupted activation has no committed project to recover.')
                migrated = read_legacy_project(root)
                report = migrated.report.to_dict()
                project = migrated.project
                source = project.to_dict()['extensions'].get('legacy_workspace_snapshot', {})
                if (not project.pages and not project.to_dict()['recovery'].get('unresolved_associations')
                        and source.get('page_layout') is None and source.get('message_html') is None
                        and not source.get('images') and not source.get('prompt_writer_state')
                        and not source.get('settings', {}).get('recipient_name')
                        and not source.get('settings', {}).get('recipient_title')):
                    # A genuinely new editor starts from the classic template.
                    # Explicit empty projects and incomplete imports stay empty.
                    project = create_project(project_id=source.get('settings', {}).get('project_id') or None)
                    project.update_project_data('extensions', {'legacy_workspace_snapshot': source})
                report['locations']['destination'] = str(repository.directory)
                stored = repository.save(project, asset_root=root, expected_revision=None,
                                         migration_report=report)
                session = ProjectSession(stored.project, project_root=root, asset_root=stored.asset_root,
                                         storage_revision=stored.revision)
            _sessions[root] = session
        session = _sessions[root]
        if not session.is_restoring and activation_pending(root):
            from saved_letters import SavedLetterRestorer
            try:
                # Register first so nested restore lookups reuse this context;
                # suspend recursion/notifications until committed state is back.
                with session.restoring():
                    SavedLetterRestorer(root).restore_canonical(active_project_repository(root).load(), _recover_committed=True)
            except Exception:
                _sessions.pop(root, None)
                session.close()
                raise
        return _sessions[root]


def reset_project_session(project_root: str | Path) -> ProjectSession:
    """Commit reset content before replacing the established active session.

    The caller establishes the session and activation journal before resetting
    compatibility files. A failed generation/pointer write leaves that session
    and its committed generation intact for the caller's rollback/recovery.
    """
    from project_repository import active_project_repository, legacy_workspace_payload
    root = Path(project_root).resolve()
    with _registry_lock:
        session = _sessions.get(root)
    if session is None:
        raise RuntimeError('Establish the active project session before resetting workspace files.')
    with session.restoring():
        source = legacy_workspace_payload(root)
        project = create_project()
        project.update_shared_settings(source['settings'])
        authored = project.to_dict()
        project.update_project_data('extensions', {
            'legacy_workspace_snapshot': source,
            'legacy_authored_snapshot': {key: authored[key] for key in ('sound', 'presentation', 'shared_settings')},
        })
        stored = active_project_repository(root).save(
            project, asset_root=root, expected_revision=session.storage_revision,
            migration_report={},
        )
        session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
    return session


def release_project_session(project_root: str | Path) -> None:
    """Drop a closed profile's cache; do not modify any saved files."""
    with _registry_lock:
        session = _sessions.pop(Path(project_root).resolve(), None)
    if session is not None:
        session.close()
