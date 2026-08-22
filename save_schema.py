from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from transactional_io import safe_write_json


CURRENT_SAVE_SCHEMA_VERSION = "1.0"
AUTOSAVE_DOCUMENT_TYPE = "project_autosave"
SAVED_LETTER_DOCUMENT_TYPE = "saved_letter"
LEGACY_COMPLETE_METADATA_VERSION = 4
REQUIRED_PAGE_FILES = (
    "cover.png",
    "letter.png",
    "wall.png",
    "back.png",
)
_LOGGER = logging.getLogger(__name__)


class SaveSchemaError(ValueError):
    pass


class SaveSchemaMigrationError(SaveSchemaError):
    pass


@dataclass(frozen=True)
class SaveSchemaRepair:
    repair_id: str
    message: str
    field: str = ""
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        repair_id = str(self.repair_id).strip()
        message = str(self.message).strip()
        field = str(self.field).strip()
        options = tuple(str(option).strip() for option in self.options)
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", repair_id):
            raise SaveSchemaMigrationError("repair_id is invalid")
        if not message:
            raise SaveSchemaMigrationError("repair message is required")
        if any(not option for option in options):
            raise SaveSchemaMigrationError("repair options cannot be empty")
        object.__setattr__(self, "repair_id", repair_id)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "options", options)


class SaveSchemaRepairRequired(SaveSchemaMigrationError):
    def __init__(self, repairs: Sequence[SaveSchemaRepair]) -> None:
        self.repairs = tuple(repairs)
        detail = "; ".join(repair.message for repair in self.repairs)
        super().__init__(
            "saved-letter migration requires repair"
            + (f": {detail}" if detail else "")
        )


@dataclass(frozen=True)
class SaveSchemaMigrationStepResult:
    metadata: Mapping[str, Any]
    required_repairs: tuple[SaveSchemaRepair, ...] = ()


@dataclass(frozen=True)
class SaveSchemaMigrationReport:
    metadata: dict[str, Any]
    source_version: str
    target_version: str
    applied_steps: tuple[tuple[str, str], ...] = ()
    required_repairs: tuple[SaveSchemaRepair, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            not self.required_repairs
            and stored_save_schema_version(self.metadata)
            == self.target_version
        )

    @property
    def changed(self) -> bool:
        return self.complete and bool(self.applied_steps)


MigrationTransform = Callable[
    [dict[str, Any], Mapping[str, Any]],
    SaveSchemaMigrationStepResult,
]


@dataclass(frozen=True)
class _SaveSchemaMigrationStep:
    source_version: str
    target_version: str
    transform: MigrationTransform


_SCHEMA_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _schema_version_key(version: object) -> tuple[int, int]:
    text = str(version or "").strip()
    match = _SCHEMA_VERSION_PATTERN.fullmatch(text)
    if match is None:
        raise SaveSchemaMigrationError(
            f"invalid save schema version: {text or '<missing>'}"
        )
    return int(match.group(1)), int(match.group(2))


def _copy_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise SaveSchemaMigrationError("saved-letter metadata must be an object")
    return copy.deepcopy(dict(metadata))


class SaveSchemaMigrationRegistry:
    """Ordered, side-effect-free forward migrations for saved-letter metadata."""

    def __init__(self, target_version: str) -> None:
        _schema_version_key(target_version)
        self.target_version = target_version
        self._steps: dict[str, _SaveSchemaMigrationStep] = {}

    def register(
        self,
        source_version: str,
        target_version: str,
        transform: MigrationTransform,
    ) -> None:
        source_key = _schema_version_key(source_version)
        target_key = _schema_version_key(target_version)
        if target_key <= source_key:
            raise SaveSchemaMigrationError(
                "migration target must be newer than its source"
            )
        if target_key > _schema_version_key(self.target_version):
            raise SaveSchemaMigrationError(
                "migration target exceeds the registry target"
            )
        if source_version in self._steps:
            raise SaveSchemaMigrationError(
                f"migration already registered from {source_version}"
            )
        if not callable(transform):
            raise SaveSchemaMigrationError("migration transform must be callable")
        self._steps[source_version] = _SaveSchemaMigrationStep(
            source_version,
            target_version,
            transform,
        )

    def migration(
        self,
        source_version: str,
        target_version: str,
    ) -> Callable[[MigrationTransform], MigrationTransform]:
        def decorator(transform: MigrationTransform) -> MigrationTransform:
            self.register(source_version, target_version, transform)
            return transform

        return decorator

    def migrate(
        self,
        metadata: Mapping[str, Any],
        *,
        repair_values: Mapping[str, Any] | None = None,
    ) -> SaveSchemaMigrationReport:
        working = _copy_metadata(metadata)
        source_version = stored_save_schema_version(working)
        source_key = _schema_version_key(source_version)
        target_key = _schema_version_key(self.target_version)
        if source_key > target_key:
            raise SaveSchemaMigrationError(
                "saved-letter schema "
                f"{source_version} is newer than supported schema "
                f"{self.target_version}"
            )
        if source_key == target_key:
            working["schema_version"] = self.target_version
            return SaveSchemaMigrationReport(
                metadata=working,
                source_version=source_version,
                target_version=self.target_version,
            )

        repairs = dict(repair_values or {})
        current_version = source_version
        applied_steps: list[tuple[str, str]] = []
        visited: set[str] = set()
        while current_version != self.target_version:
            if current_version in visited:
                raise SaveSchemaMigrationError(
                    f"migration cycle detected at {current_version}"
                )
            visited.add(current_version)
            step = self._steps.get(current_version)
            if step is None:
                raise SaveSchemaMigrationError(
                    "no migration is registered from schema "
                    f"{current_version} to {self.target_version}"
                )
            if (
                _schema_version_key(step.target_version)
                > target_key
            ):
                raise SaveSchemaMigrationError(
                    f"migration from {current_version} overshoots "
                    f"target {self.target_version}"
                )
            result = step.transform(_copy_metadata(working), repairs)
            if not isinstance(result, SaveSchemaMigrationStepResult):
                raise SaveSchemaMigrationError(
                    f"migration from {current_version} returned an invalid result"
                )
            next_metadata = _copy_metadata(result.metadata)
            pending_repairs = tuple(result.required_repairs)
            if any(
                not isinstance(repair, SaveSchemaRepair)
                for repair in pending_repairs
            ):
                raise SaveSchemaMigrationError(
                    f"migration from {current_version} returned an invalid repair"
                )
            repair_ids = [repair.repair_id for repair in pending_repairs]
            if len(repair_ids) != len(set(repair_ids)):
                raise SaveSchemaMigrationError(
                    f"migration from {current_version} returned duplicate repairs"
                )
            if pending_repairs:
                next_metadata["schema_version"] = current_version
                _LOGGER.warning(
                    "Save schema migration paused at %s with %d required repair(s).",
                    current_version,
                    len(pending_repairs),
                )
                return SaveSchemaMigrationReport(
                    metadata=next_metadata,
                    source_version=source_version,
                    target_version=self.target_version,
                    applied_steps=tuple(applied_steps),
                    required_repairs=pending_repairs,
                )
            next_metadata["schema_version"] = step.target_version
            working = next_metadata
            applied_steps.append(
                (step.source_version, step.target_version)
            )
            current_version = step.target_version

        report = SaveSchemaMigrationReport(
            metadata=working,
            source_version=source_version,
            target_version=self.target_version,
            applied_steps=tuple(applied_steps),
        )
        if report.applied_steps:
            _LOGGER.info(
                "Save schema migration prepared: %s -> %s (%d step(s)).",
                report.source_version,
                report.target_version,
                len(report.applied_steps),
            )
        return report


SAVE_SCHEMA_MIGRATIONS = SaveSchemaMigrationRegistry(
    CURRENT_SAVE_SCHEMA_VERSION
)


def stored_save_schema_version(metadata: Mapping[str, Any]) -> str:
    raw = metadata.get("schema_version", "")
    return str(raw).strip()


def is_current_save_schema(metadata: Mapping[str, Any]) -> bool:
    return stored_save_schema_version(metadata) == CURRENT_SAVE_SCHEMA_VERSION


def legacy_metadata_version(metadata: Mapping[str, Any]) -> int:
    raw = metadata.get(
        "source_version",
        metadata.get("schema_version", 0),
    )
    if isinstance(raw, bool):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def has_complete_saved_state(metadata: Mapping[str, Any]) -> bool:
    return is_current_save_schema(metadata) or (
        legacy_metadata_version(metadata)
        >= LEGACY_COMPLETE_METADATA_VERSION
    )


def stamp_current_save_schema(
    metadata: Mapping[str, Any],
    *,
    document_type: str,
) -> dict[str, Any]:
    if document_type not in {
        AUTOSAVE_DOCUMENT_TYPE,
        SAVED_LETTER_DOCUMENT_TYPE,
    }:
        raise SaveSchemaError("unsupported save document type")
    stamped = dict(metadata)
    stamped["schema_version"] = CURRENT_SAVE_SCHEMA_VERSION
    stamped["document_type"] = document_type
    stamped.pop("source_version", None)
    stamped.pop("release_schema_version", None)
    return stamped


def migrate_saved_letter_metadata(
    metadata: Mapping[str, Any],
    *,
    repair_values: Mapping[str, Any] | None = None,
    registry: SaveSchemaMigrationRegistry | None = None,
) -> SaveSchemaMigrationReport:
    version = stored_save_schema_version(metadata)
    if not version or version.isdigit():
        legacy = _copy_metadata(metadata)
        return SaveSchemaMigrationReport(
            metadata=legacy,
            source_version=version,
            target_version=version,
        )
    return (registry or SAVE_SCHEMA_MIGRATIONS).migrate(
        metadata,
        repair_values=repair_values,
    )


def persist_completed_save_schema_migration(
    path: str | Path,
    report: SaveSchemaMigrationReport,
    *,
    validator: Callable[[Mapping[str, Any]], Any] | None = None,
) -> Path:
    if report.required_repairs:
        raise SaveSchemaRepairRequired(report.required_repairs)
    if not report.complete:
        raise SaveSchemaMigrationError("save schema migration is incomplete")

    target = Path(path)
    if target.is_symlink():
        raise SaveSchemaMigrationError(
            "saved-letter metadata cannot be a link"
        )
    if not report.changed:
        return target

    if validator is not None:
        validator(report.metadata)
    try:
        safe_write_json(
            target,
            report.metadata,
            validator=validator,
        )
        reopened = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SaveSchemaMigrationError(
            "migrated saved-letter metadata could not be saved"
        ) from error
    if reopened != report.metadata:
        raise SaveSchemaMigrationError(
            "migrated saved-letter metadata did not reopen exactly"
        )
    _LOGGER.info(
        "Save schema migration committed: %s -> %s.",
        report.source_version,
        report.target_version,
    )
    return target


def migrate_saved_letter_metadata_file(
    path: str | Path,
    *,
    repair_values: Mapping[str, Any] | None = None,
    registry: SaveSchemaMigrationRegistry | None = None,
    validator: Callable[[Mapping[str, Any]], Any] | None = None,
) -> SaveSchemaMigrationReport:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise SaveSchemaMigrationError(
            "saved-letter metadata file is missing or unsafe"
        )
    try:
        metadata = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SaveSchemaMigrationError(
            "saved-letter metadata is unreadable"
        ) from error
    if not isinstance(metadata, dict):
        raise SaveSchemaMigrationError(
            "saved-letter metadata must contain an object"
        )

    report = migrate_saved_letter_metadata(
        metadata,
        repair_values=repair_values,
        registry=registry,
    )
    if report.complete:
        persist_completed_save_schema_migration(
            target,
            report,
            validator=validator,
        )
    return report


def _require_object(
    metadata: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    value = metadata.get(key)
    if not isinstance(value, Mapping):
        raise SaveSchemaError(f"{key} must be an object")
    return value


def _require_text(
    metadata: Mapping[str, Any],
    key: str,
) -> str:
    value = str(metadata.get(key, "")).strip()
    if not value:
        raise SaveSchemaError(f"{key} is required")
    return value


def _require_relative_file(value: object, key: str) -> str:
    text = str(value or "").strip()
    relative = Path(text)
    if (
        not text
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.name in {"", ".", ".."}
    ):
        raise SaveSchemaError(f"{key} must be a safe relative file path")
    return text


def _require_uuid(metadata: Mapping[str, Any], key: str) -> None:
    try:
        uuid.UUID(str(metadata.get(key, "")))
    except (AttributeError, TypeError, ValueError) as error:
        raise SaveSchemaError(f"{key} must be a UUID") from error


def validate_saved_letter_metadata(
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate 1.0 metadata while allowing numeric pre-1.0 bundles."""
    migration = migrate_saved_letter_metadata(metadata)
    if migration.required_repairs:
        raise SaveSchemaRepairRequired(migration.required_repairs)
    validated = migration.metadata
    version = stored_save_schema_version(validated)
    if not version or version.isdigit():
        return validated
    if version != CURRENT_SAVE_SCHEMA_VERSION:
        raise SaveSchemaError(
            f"unsupported saved-letter schema version: {version}"
        )
    if validated.get("document_type") != SAVED_LETTER_DOCUMENT_TYPE:
        raise SaveSchemaError("document_type must be saved_letter")

    _require_text(validated, "recipient_name")
    _require_text(validated, "recipient_title")
    if not bool(validated.get("example_master", False)):
        _require_uuid(validated, "project_id")
        _require_uuid(validated, "recipient_id")

    _require_object(validated, "settings")
    _require_object(validated, "sound")
    _require_object(validated, "readiness")
    editable_assets = _require_object(validated, "editable_assets")
    pages = _require_object(editable_assets, "pages")
    for filename in REQUIRED_PAGE_FILES:
        _require_relative_file(
            pages.get(filename),
            f"editable_assets.pages.{filename}",
        )
    for key in (
        "message",
        "sound_manifest",
        "prompt_writer_state",
        "image_manifest",
    ):
        _require_relative_file(
            editable_assets.get(key),
            f"editable_assets.{key}",
        )
    _require_relative_file(
        validated.get("cover_thumbnail_path"),
        "cover_thumbnail_path",
    )
    return validated


__all__ = [
    "AUTOSAVE_DOCUMENT_TYPE",
    "CURRENT_SAVE_SCHEMA_VERSION",
    "LEGACY_COMPLETE_METADATA_VERSION",
    "SAVED_LETTER_DOCUMENT_TYPE",
    "SAVE_SCHEMA_MIGRATIONS",
    "SaveSchemaError",
    "SaveSchemaMigrationError",
    "SaveSchemaMigrationRegistry",
    "SaveSchemaMigrationReport",
    "SaveSchemaMigrationStepResult",
    "SaveSchemaRepair",
    "SaveSchemaRepairRequired",
    "has_complete_saved_state",
    "is_current_save_schema",
    "legacy_metadata_version",
    "migrate_saved_letter_metadata",
    "migrate_saved_letter_metadata_file",
    "persist_completed_save_schema_migration",
    "stamp_current_save_schema",
    "stored_save_schema_version",
    "validate_saved_letter_metadata",
]
