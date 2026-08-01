#!/usr/bin/env python3
"""Stable project identity and atomic autosave for Letter Smith.

Recipient and title provide the visible saved-letter folder names. ``project_id``
is generated once, remains internal metadata, and persists across folder renames.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from recipient_identity import (
    RecipientName,
    build_recipient_match_key,
    collapse_recipient_spacing,
    normalize_recipient_display_name,
)
from recipient_registry import RecipientRegistry
from settings_store import SettingsStore

PROJECT_ID_KEY = "project_id"
RECIPIENT_ID_KEY = "recipient_id"
RECIPIENT_DISPLAY_NAME_KEY = "recipient_display_name"
RECIPIENT_NORMALIZED_KEY = "recipient_normalized_key"
LEGACY_RECIPIENT_NAME_KEY = "recipient_name"
PROJECT_SCHEMA_KEY = "project_schema_version"
PROJECT_SCHEMA_VERSION = 2

_lock = RLock()


class ApplicationState(str, Enum):
    BOOTING = "BOOTING"
    RECIPIENT_REQUIRED = "RECIPIENT_REQUIRED"
    PROJECT_LOADING = "PROJECT_LOADING"
    PROJECT_READY = "PROJECT_READY"
    PROJECT_CLEARING = "PROJECT_CLEARING"
    PROJECT_MIGRATING = "PROJECT_MIGRATING"
    SHUTTING_DOWN = "SHUTTING_DOWN"


_ALLOWED_TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    ApplicationState.BOOTING: frozenset(
        {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_LOADING,
            ApplicationState.PROJECT_READY,
            ApplicationState.PROJECT_CLEARING,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.RECIPIENT_REQUIRED: frozenset(
        {
            ApplicationState.PROJECT_LOADING,
            ApplicationState.PROJECT_READY,
            ApplicationState.PROJECT_CLEARING,
            ApplicationState.PROJECT_MIGRATING,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.PROJECT_LOADING: frozenset(
        {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_READY,
            ApplicationState.PROJECT_MIGRATING,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.PROJECT_READY: frozenset(
        {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_LOADING,
            ApplicationState.PROJECT_CLEARING,
            ApplicationState.PROJECT_MIGRATING,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.PROJECT_CLEARING: frozenset(
        {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_READY,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.PROJECT_MIGRATING: frozenset(
        {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_LOADING,
            ApplicationState.PROJECT_READY,
            ApplicationState.SHUTTING_DOWN,
        }
    ),
    ApplicationState.SHUTTING_DOWN: frozenset(),
}


def _valid_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _recipient_display_name(data: Mapping[str, Any]) -> str:
    raw_name = collapse_recipient_spacing(
        data.get(RECIPIENT_DISPLAY_NAME_KEY)
        or data.get(LEGACY_RECIPIENT_NAME_KEY)
    )
    if not raw_name:
        return ""
    return normalize_recipient_display_name(
        raw_name,
        custom_capitalization=True,
    )


def _recipient_normalized_key(
    data: Mapping[str, Any],
    display_name: str,
) -> str:
    configured = collapse_recipient_spacing(
        data.get(RECIPIENT_NORMALIZED_KEY)
    )
    if configured:
        return configured.casefold()
    return (
        build_recipient_match_key(display_name)
        if display_name
        else ""
    )


@dataclass(frozen=True)
class ProjectIdentity:
    """Path-independent ownership for one editable letter."""

    recipient_id: str
    recipient_display_name: str
    recipient_normalized_key: str
    project_id: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ProjectIdentity":
        display_name = _recipient_display_name(data)
        return cls(
            recipient_id=_valid_uuid(data.get(RECIPIENT_ID_KEY)),
            recipient_display_name=display_name,
            recipient_normalized_key=_recipient_normalized_key(
                data,
                display_name,
            ),
            project_id=_valid_uuid(data.get(PROJECT_ID_KEY)),
        )

    @property
    def is_valid(self) -> bool:
        return not self.missing_fields

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.recipient_id:
            missing.append(RECIPIENT_ID_KEY)
        if not self.recipient_display_name:
            missing.append(RECIPIENT_DISPLAY_NAME_KEY)
        if not self.recipient_normalized_key:
            missing.append(RECIPIENT_NORMALIZED_KEY)
        if not self.project_id:
            missing.append(PROJECT_ID_KEY)
        return tuple(missing)

    def as_settings(self) -> dict[str, str | int]:
        return {
            RECIPIENT_ID_KEY: self.recipient_id,
            RECIPIENT_DISPLAY_NAME_KEY: self.recipient_display_name,
            RECIPIENT_NORMALIZED_KEY: self.recipient_normalized_key,
            LEGACY_RECIPIENT_NAME_KEY: self.recipient_display_name,
            PROJECT_ID_KEY: self.project_id,
            PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
        }


class ProjectStateController:
    """Single authority for project availability and lifecycle transitions."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.recipient_registry = RecipientRegistry(self.project_root)
        self._lock = RLock()
        self._state = ApplicationState.BOOTING
        self._identity = ProjectIdentity("", "", "", "")
        self._listeners: list[
            Callable[[ApplicationState, ApplicationState], None]
        ] = []

    @property
    def state(self) -> ApplicationState:
        with self._lock:
            return self._state

    @property
    def identity(self) -> ProjectIdentity:
        with self._lock:
            return self._identity

    @property
    def is_project_ready(self) -> bool:
        with self._lock:
            return (
                self._state is ApplicationState.PROJECT_READY
                and self._identity.is_valid
            )

    def initialize(self) -> ApplicationState:
        settings = load_project_settings(self.project_root)
        identity = ProjectIdentity.from_mapping(settings)
        if identity.recipient_display_name:
            record = self.recipient_registry.get_or_create(
                identity.recipient_display_name,
                custom_capitalization=True,
                recipient_id=identity.recipient_id or None,
            )
            if (
                identity.recipient_id != record.recipient_id
                or identity.recipient_display_name != record.display_name
                or identity.recipient_normalized_key
                != record.normalized_key
            ):
                settings = SettingsStore(self.project_root).update_fields(
                    {
                        RECIPIENT_ID_KEY: record.recipient_id,
                        RECIPIENT_DISPLAY_NAME_KEY: record.display_name,
                        RECIPIENT_NORMALIZED_KEY: record.normalized_key,
                        LEGACY_RECIPIENT_NAME_KEY: record.display_name,
                    }
                )
                identity = ProjectIdentity.from_mapping(settings)
        target = (
            ApplicationState.PROJECT_READY
            if identity.is_valid
            else ApplicationState.RECIPIENT_REQUIRED
        )
        self.transition(target, identity=identity)
        return target

    def add_listener(
        self,
        listener: Callable[[ApplicationState, ApplicationState], None],
    ) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(
        self,
        listener: Callable[[ApplicationState, ApplicationState], None],
    ) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def transition(
        self,
        target: ApplicationState,
        *,
        identity: ProjectIdentity | None = None,
    ) -> ApplicationState:
        target = ApplicationState(target)
        with self._lock:
            previous = self._state
            if target is previous:
                if identity is not None:
                    self._identity = identity
                return target
            if target not in _ALLOWED_TRANSITIONS[previous]:
                raise RuntimeError(
                    f"Invalid project-state transition: "
                    f"{previous.value} -> {target.value}"
                )
            candidate = identity if identity is not None else self._identity
            if (
                target is ApplicationState.PROJECT_READY
                and not candidate.is_valid
            ):
                missing = ", ".join(candidate.missing_fields)
                raise ValueError(
                    f"PROJECT_READY requires complete identity: {missing}"
                )
            self._identity = candidate
            self._state = target
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(previous, target)
        return target

    def refresh_identity(self) -> ProjectIdentity:
        identity = ProjectIdentity.from_mapping(
            load_project_settings(self.project_root)
        )
        with self._lock:
            self._identity = identity
        return identity

    def require_ready(self) -> ProjectIdentity:
        if not self.is_project_ready:
            raise RuntimeError(
                f"Project is not ready: {self.state.value}"
            )
        return self.identity

    def require_recipient(self) -> None:
        self.transition(ApplicationState.RECIPIENT_REQUIRED)

    def establish_project(
        self,
        recipient_display_name: str,
        *,
        recipient_normalized_key: str | None = None,
        recipient_id: str | None = None,
        project_id: str | None = None,
        custom_capitalization: bool = False,
    ) -> ProjectIdentity:
        """Persist an accepted recipient and enter PROJECT_READY."""
        if self.state not in {
            ApplicationState.RECIPIENT_REQUIRED,
            ApplicationState.PROJECT_MIGRATING,
            ApplicationState.PROJECT_LOADING,
        }:
            raise RuntimeError(
                "A recipient can only be accepted while a project "
                "is being initialized."
            )
        recipient_name = RecipientName.from_raw(
            recipient_display_name,
            custom_capitalization=custom_capitalization,
        )
        record = self.recipient_registry.get_or_create(
            recipient_name.display_name,
            custom_capitalization=True,
            recipient_id=recipient_id,
        )
        if (
            recipient_normalized_key
            and build_recipient_match_key(recipient_normalized_key)
            != record.normalized_key
        ):
            raise ValueError("Recipient matching key does not match the name.")
        display_name = record.display_name
        normalized_key = record.normalized_key

        current = SettingsStore(self.project_root).snapshot()
        stable_recipient_id = record.recipient_id
        stable_project_id = (
            _valid_uuid(project_id)
            or _valid_uuid(current.get(PROJECT_ID_KEY))
            or _new_uuid()
        )
        updated = SettingsStore(self.project_root).update_fields(
            {
                RECIPIENT_ID_KEY: stable_recipient_id,
                RECIPIENT_DISPLAY_NAME_KEY: display_name,
                RECIPIENT_NORMALIZED_KEY: normalized_key,
                LEGACY_RECIPIENT_NAME_KEY: display_name,
                PROJECT_ID_KEY: stable_project_id,
                PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
            }
        )
        identity = require_project_identity(updated)
        self.transition(ApplicationState.PROJECT_READY, identity=identity)
        return identity

    def begin_new_project(
        self,
        additional_settings: Mapping[str, Any] | None = None,
    ) -> None:
        """Clear active ownership without creating permanent storage."""
        if self.state is ApplicationState.SHUTTING_DOWN:
            raise RuntimeError("The application is shutting down.")
        if self.state is not ApplicationState.PROJECT_CLEARING:
            self.transition(ApplicationState.PROJECT_CLEARING)
        updates = dict(additional_settings or {})
        updates.update(
            {
                RECIPIENT_ID_KEY: "",
                RECIPIENT_DISPLAY_NAME_KEY: "",
                RECIPIENT_NORMALIZED_KEY: "",
                LEGACY_RECIPIENT_NAME_KEY: "",
                PROJECT_ID_KEY: "",
                PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
                "recipient_title": "",
                "published_page_url": "",
                "published_public_path": "",
            }
        )
        SettingsStore(self.project_root).update_fields(updates)
        empty_identity = ProjectIdentity("", "", "", "")
        self.transition(
            ApplicationState.RECIPIENT_REQUIRED,
            identity=empty_identity,
        )

    def shutdown(self) -> None:
        if self.state is not ApplicationState.SHUTTING_DOWN:
            self.transition(ApplicationState.SHUTTING_DOWN)


def project_identity_from_settings(
    data: Mapping[str, Any],
) -> ProjectIdentity:
    return ProjectIdentity.from_mapping(data)


def require_project_identity(
    data: Mapping[str, Any],
) -> ProjectIdentity:
    identity = project_identity_from_settings(data)
    if not identity.is_valid:
        missing = ", ".join(identity.missing_fields)
        raise ValueError(f"Project identity is incomplete: {missing}")
    return identity


def _identity_updates(data: Mapping[str, Any]) -> dict[str, str | int]:
    display_name = _recipient_display_name(data)
    project_id = _valid_uuid(data.get(PROJECT_ID_KEY)) or _new_uuid()
    updates: dict[str, str | int] = {
        PROJECT_ID_KEY: project_id,
        PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
    }
    if not display_name:
        return updates

    recipient_id = _valid_uuid(data.get(RECIPIENT_ID_KEY)) or _new_uuid()
    updates.update(
        {
            RECIPIENT_ID_KEY: recipient_id,
            RECIPIENT_DISPLAY_NAME_KEY: display_name,
            RECIPIENT_NORMALIZED_KEY: _recipient_normalized_key(
                data,
                display_name,
            ),
            LEGACY_RECIPIENT_NAME_KEY: display_name,
        }
    )
    return updates


def atomic_write_settings(project_root: str | Path, data: Mapping[str, Any]) -> None:
    """Write settings without exposing readers to a partially written file."""
    SettingsStore(project_root).replace_snapshot(data)


def load_project_settings(project_root: str | Path, *, ensure_identity: bool = True) -> dict[str, Any]:
    """Load settings and migrate legacy active identity fields when possible."""
    with _lock:
        store = SettingsStore(project_root)
        data = store.snapshot()
        if not ensure_identity:
            return data
        updates = _identity_updates(data)
        if any(data.get(key) != value for key, value in updates.items()):
            data = store.update_fields(updates)
        return data


def ensure_project_identity(project_root: str | Path) -> str:
    """Return the stable letter UUID retained for compatibility."""
    return str(load_project_settings(project_root, ensure_identity=True)[PROJECT_ID_KEY])


def autosave_project_settings(
    project_root: str | Path,
    updates: Mapping[str, Any],
    *,
    preserve_project_id: bool = True,
    preserve_recipient_id: bool = True,
) -> dict[str, Any]:
    """Merge an autosave update into the active project atomically.

    Normal edits cannot replace ``project_id``. Creating or duplicating a project
    must use an explicit project-management operation instead.
    """
    with _lock:
        store = SettingsStore(project_root)
        data = store.snapshot()
        identity_updates = _identity_updates(data)
        accepted: dict[str, Any] = {}
        for key, value in updates.items():
            if preserve_project_id and key == PROJECT_ID_KEY:
                continue
            if preserve_recipient_id and key == RECIPIENT_ID_KEY:
                continue
            accepted[key] = value
        accepted.update(identity_updates)
        return store.update_fields(accepted)


def adopt_loaded_project(project_root: str | Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Load a saved project's identity and editable metadata into the workspace."""
    loaded_id = _valid_uuid(metadata.get(PROJECT_ID_KEY)) or _new_uuid()
    with _lock:
        store = SettingsStore(project_root)
        current = store.snapshot()
        display_name = _recipient_display_name(metadata)
        normalized_key = _recipient_normalized_key(metadata, display_name)
        current_identity = ProjectIdentity.from_mapping(current)
        recipient_id = _valid_uuid(metadata.get(RECIPIENT_ID_KEY))
        if (
            not recipient_id
            and display_name
            and current_identity.recipient_normalized_key == normalized_key
        ):
            recipient_id = current_identity.recipient_id
        if not recipient_id and display_name:
            recipient_id = _new_uuid()
        current.update({
            PROJECT_ID_KEY: loaded_id,
            PROJECT_SCHEMA_KEY: PROJECT_SCHEMA_VERSION,
            RECIPIENT_ID_KEY: recipient_id,
            RECIPIENT_DISPLAY_NAME_KEY: display_name,
            RECIPIENT_NORMALIZED_KEY: normalized_key,
            LEGACY_RECIPIENT_NAME_KEY: display_name,
            "recipient_title": str(metadata.get("recipient_title", "")).strip(),
        })
        if "published_page_url" in metadata:
            current["published_page_url"] = str(metadata.get("published_page_url", "")).strip()
        return store.replace_snapshot(current)
