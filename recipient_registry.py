from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from recipient_identity import (
    RecipientName,
    build_recipient_match_key,
)
from transactional_io import atomic_write_json


REGISTRY_SCHEMA_VERSION = 1
RECIPIENT_REGISTRY_FILE = "recipients.json"


class RecipientRegistryError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return ""


@dataclass(frozen=True)
class RecipientRecord:
    recipient_id: str
    display_name: str
    normalized_key: str
    folder_name: str
    created_at: str
    updated_at: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "RecipientRecord":
        record = cls(
            recipient_id=_valid_uuid(value.get("recipient_id")),
            display_name=str(value.get("display_name") or "").strip(),
            normalized_key=str(value.get("normalized_key") or "").strip().casefold(),
            folder_name=str(value.get("folder_name") or "").strip(),
            created_at=str(value.get("created_at") or "").strip(),
            updated_at=str(value.get("updated_at") or "").strip(),
        )
        if (
            not record.recipient_id
            or not record.display_name
            or not record.normalized_key
            or not record.folder_name
        ):
            raise RecipientRegistryError(
                "Recipient registry contains an incomplete record."
            )
        if (
            build_recipient_match_key(record.display_name)
            != record.normalized_key
        ):
            raise RecipientRegistryError(
                f"Recipient registry key does not match "
                f"{record.display_name!r}."
            )
        return record


class RecipientRegistry:
    """Canonical, atomic recipient ownership registry."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.path = (
            self.project_root
            / "output"
            / RECIPIENT_REGISTRY_FILE
        ).resolve()
        self._lock = RLock()

    def list(self) -> tuple[RecipientRecord, ...]:
        with self._lock:
            records = self._read_unlocked()
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    record.display_name.casefold(),
                    record.recipient_id,
                ),
            )
        )

    def find_by_id(self, recipient_id: object) -> RecipientRecord | None:
        stable_id = _valid_uuid(recipient_id)
        if not stable_id:
            return None
        return next(
            (
                record
                for record in self.list()
                if record.recipient_id == stable_id
            ),
            None,
        )

    def find_matching_recipient(
        self,
        match_key: str,
    ) -> RecipientRecord | None:
        normalized_key = build_recipient_match_key(match_key)
        return next(
            (
                record
                for record in self.list()
                if record.normalized_key == normalized_key
            ),
            None,
        )

    def get_or_create(
        self,
        raw_name: str,
        *,
        custom_capitalization: bool = False,
        recipient_id: str | None = None,
    ) -> RecipientRecord:
        name = RecipientName.from_raw(
            raw_name,
            custom_capitalization=custom_capitalization,
        )
        with self._lock:
            records = self._read_unlocked()
            by_key = next(
                (
                    record
                    for record in records
                    if record.normalized_key == name.normalized_key
                ),
                None,
            )
            if by_key is not None:
                return by_key

            stable_id = _valid_uuid(recipient_id) or str(uuid.uuid4())
            by_id = next(
                (
                    record
                    for record in records
                    if record.recipient_id == stable_id
                ),
                None,
            )
            if by_id is not None:
                raise RecipientRegistryError(
                    "Recipient ID already belongs to another recipient."
                )
            now = _timestamp()
            record = RecipientRecord(
                recipient_id=stable_id,
                display_name=name.display_name,
                normalized_key=name.normalized_key,
                folder_name=name.folder_name,
                created_at=now,
                updated_at=now,
            )
            records.append(record)
            self._write_unlocked(records)
            return record

    def replace_record(
        self,
        record: RecipientRecord,
    ) -> RecipientRecord:
        validated = RecipientRecord.from_mapping(asdict(record))
        with self._lock:
            records = self._read_unlocked()
            duplicate = next(
                (
                    candidate
                    for candidate in records
                    if (
                        candidate.normalized_key
                        == validated.normalized_key
                        and candidate.recipient_id
                        != validated.recipient_id
                    )
                ),
                None,
            )
            if duplicate is not None:
                raise RecipientRegistryError(
                    "Another recipient already uses that name."
                )
            replaced = False
            updated: list[RecipientRecord] = []
            for candidate in records:
                if candidate.recipient_id == validated.recipient_id:
                    updated.append(validated)
                    replaced = True
                else:
                    updated.append(candidate)
            if not replaced:
                raise RecipientRegistryError("Recipient record was not found.")
            self._write_unlocked(updated)
            return validated

    def _read_unlocked(self) -> list[RecipientRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RecipientRegistryError(
                f"Could not read recipient registry: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise RecipientRegistryError(
                "Recipient registry root must be an object."
            )
        if raw.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise RecipientRegistryError(
                "Recipient registry schema is unsupported."
            )
        values = raw.get("recipients", [])
        if not isinstance(values, list):
            raise RecipientRegistryError(
                "Recipient registry records must be a list."
            )
        records = [
            RecipientRecord.from_mapping(value)
            for value in values
            if isinstance(value, Mapping)
        ]
        if len(records) != len(values):
            raise RecipientRegistryError(
                "Recipient registry contains an invalid record."
            )
        ids = {record.recipient_id for record in records}
        keys = {record.normalized_key for record in records}
        if len(ids) != len(records) or len(keys) != len(records):
            raise RecipientRegistryError(
                "Recipient registry contains duplicate identities."
            )
        return records

    def _write_unlocked(
        self,
        records: list[RecipientRecord],
    ) -> None:
        atomic_write_json(
            self.path,
            {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "recipients": [
                    asdict(record)
                    for record in sorted(
                        records,
                        key=lambda value: (
                            value.normalized_key,
                            value.recipient_id,
                        ),
                    )
                ],
            },
        )


__all__ = [
    "RECIPIENT_REGISTRY_FILE",
    "REGISTRY_SCHEMA_VERSION",
    "RecipientRecord",
    "RecipientRegistry",
    "RecipientRegistryError",
]
