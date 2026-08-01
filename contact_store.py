from __future__ import annotations

import csv
import json
import quopri
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import QStandardPaths

from transactional_io import atomic_write_json


CONTACTS_SCHEMA_VERSION = 1


class ContactImportError(ValueError):
    """A contact file could not be read safely."""


@dataclass(frozen=True)
class Contact:
    contact_id: str
    name: str
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()

    @property
    def primary_email(self) -> str:
        return self.emails[0] if self.emails else ""

    @property
    def primary_phone(self) -> str:
        return self.phones[0] if self.phones else ""

    @classmethod
    def create(
        cls,
        name: object,
        emails: Iterable[object] = (),
        phones: Iterable[object] = (),
    ) -> Contact:
        clean_emails = _unique(
            _normalize_email(value)
            for value in emails
            if _normalize_email(value)
        )
        clean_phones = _unique(
            _normalize_phone(value)
            for value in phones
            if _normalize_phone(value)
        )
        clean_name = " ".join(str(name or "").split()) or "Unnamed Contact"
        identity = "\x1f".join(
            (
                clean_name.casefold(),
                *(value.casefold() for value in clean_emails),
                *(_phone_identity(value) for value in clean_phones),
            )
        )
        return cls(
            contact_id=uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"lettersmith-contact:{identity}",
            ).hex,
            name=clean_name,
            emails=clean_emails,
            phones=clean_phones,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Contact:
        contact = cls.create(
            value.get("name", ""),
            value.get("emails", ()) if isinstance(value.get("emails"), list) else (),
            value.get("phones", ()) if isinstance(value.get("phones"), list) else (),
        )
        stored_id = str(value.get("contact_id", "")).strip()
        return cls(
            contact_id=stored_id or contact.contact_id,
            name=contact.name,
            emails=contact.emails,
            phones=contact.phones,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "contact_id": self.contact_id,
            "name": self.name,
            "emails": list(self.emails),
            "phones": list(self.phones),
        }


@dataclass(frozen=True)
class ContactImportResult:
    parsed: int
    added: int
    updated: int
    total: int


class ContactStore:
    """Private application contact book imported from standard contact files."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path).resolve()
            if path is not None
            else self.default_path()
        )

    @staticmethod
    def default_path() -> Path:
        base = QStandardPaths.writableLocation(
            QStandardPaths.AppDataLocation
        )
        if not base:
            base = str(Path.home() / ".lettersmith")
        return (Path(base) / "contacts.json").resolve()

    def list_contacts(self) -> tuple[Contact, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ContactImportError(
                "The saved contact book could not be read."
            ) from error
        if not isinstance(payload, dict):
            raise ContactImportError("The saved contact book is invalid.")
        raw_contacts = payload.get("contacts", [])
        if not isinstance(raw_contacts, list):
            raise ContactImportError("The saved contact book is invalid.")
        contacts = [
            Contact.from_mapping(value)
            for value in raw_contacts
            if isinstance(value, dict)
        ]
        return tuple(
            sorted(
                contacts,
                key=lambda contact: (
                    contact.name.casefold(),
                    contact.primary_email.casefold(),
                    _phone_identity(contact.primary_phone),
                ),
            )
        )

    def import_files(
        self,
        paths: Iterable[str | Path],
    ) -> ContactImportResult:
        parsed: list[Contact] = []
        for raw_path in paths:
            path = Path(raw_path).resolve()
            suffix = path.suffix.casefold()
            if suffix in {".vcf", ".vcard"}:
                parsed.extend(_parse_vcard(path))
            elif suffix == ".csv":
                parsed.extend(_parse_csv(path))
            else:
                raise ContactImportError(
                    f"{path.name} is not a supported contact file."
                )
        if not parsed:
            raise ContactImportError(
                "No contacts with an email address or phone number were found."
            )

        contacts = list(self.list_contacts())
        added = 0
        updated = 0
        for incoming in parsed:
            index = _matching_contact_index(contacts, incoming)
            if index is None:
                contacts.append(incoming)
                added += 1
                continue
            merged = _merge_contacts(contacts[index], incoming)
            if merged != contacts[index]:
                contacts[index] = merged
                updated += 1

        contacts.sort(key=lambda contact: contact.name.casefold())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.path,
            {
                "schema_version": CONTACTS_SCHEMA_VERSION,
                "contacts": [
                    contact.to_mapping()
                    for contact in contacts
                ],
            },
        )
        return ContactImportResult(
            parsed=len(parsed),
            added=added,
            updated=updated,
            total=len(contacts),
        )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _normalize_email(value: object) -> str:
    candidate = str(value or "").strip()
    if candidate.casefold().startswith("mailto:"):
        candidate = candidate[7:]
    if (
        not candidate
        or any(character.isspace() for character in candidate)
        or candidate.count("@") != 1
    ):
        return ""
    local, domain = candidate.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return candidate


def _normalize_phone(value: object) -> str:
    candidate = str(value or "").strip()
    if candidate.casefold().startswith("tel:"):
        candidate = candidate[4:]
    candidate = re.sub(r"\s+", " ", candidate)
    return candidate if len(re.findall(r"\d", candidate)) >= 5 else ""


def _phone_identity(value: str) -> str:
    prefix = "+" if str(value).strip().startswith("+") else ""
    return prefix + "".join(re.findall(r"\d", str(value)))


def _matching_contact_index(
    contacts: list[Contact],
    incoming: Contact,
) -> int | None:
    incoming_emails = {value.casefold() for value in incoming.emails}
    incoming_phones = {_phone_identity(value) for value in incoming.phones}
    for index, contact in enumerate(contacts):
        if contact.contact_id == incoming.contact_id:
            return index
        if incoming_emails.intersection(
            value.casefold() for value in contact.emails
        ):
            return index
        if incoming_phones.intersection(
            _phone_identity(value) for value in contact.phones
        ):
            return index
    return None


def _merge_contacts(existing: Contact, incoming: Contact) -> Contact:
    name = (
        incoming.name
        if existing.name == "Unnamed Contact"
        else existing.name
    )
    return Contact(
        contact_id=existing.contact_id,
        name=name,
        emails=_unique((*existing.emails, *incoming.emails)),
        phones=_unique((*existing.phones, *incoming.phones)),
    )


def _read_contact_text(path: Path) -> str:
    if not path.is_file():
        raise ContactImportError(f"{path.name} could not be found.")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ContactImportError(f"{path.name} could not be read.") from error
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ContactImportError(f"{path.name} uses an unsupported text encoding.")


def _unescape_vcard(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\:", ":")
        .replace("\\\\", "\\")
        .strip()
    )


def _vcard_value(header: str, value: str) -> str:
    if "ENCODING=QUOTED-PRINTABLE" in header.upper():
        try:
            value = quopri.decodestring(value).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            value = quopri.decodestring(value).decode("cp1252", errors="replace")
    return _unescape_vcard(value)


def _parse_vcard(path: Path) -> list[Contact]:
    text = _read_contact_text(path)
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        elif lines and lines[-1].endswith("="):
            lines[-1] = lines[-1][:-1] + line
        else:
            lines.append(line)

    contacts: list[Contact] = []
    block: list[str] = []
    inside = False
    for line in lines:
        if line.strip().upper() == "BEGIN:VCARD":
            block = []
            inside = True
            continue
        if line.strip().upper() == "END:VCARD":
            if inside:
                contact = _contact_from_vcard_block(block)
                if contact is not None:
                    contacts.append(contact)
            block = []
            inside = False
            continue
        if inside:
            block.append(line)
    return contacts


def _contact_from_vcard_block(lines: Iterable[str]) -> Contact | None:
    full_name = ""
    structured_name = ""
    emails: list[str] = []
    phones: list[str] = []
    for line in lines:
        if ":" not in line:
            continue
        header, raw_value = line.split(":", 1)
        key = header.split(";", 1)[0].rsplit(".", 1)[-1].upper()
        value = _vcard_value(header, raw_value)
        if key == "FN":
            full_name = value
        elif key == "N":
            parts = value.split(";")
            parts.extend([""] * (5 - len(parts)))
            family, given, additional, prefix, suffix = parts[:5]
            structured_name = " ".join(
                part for part in (prefix, given, additional, family, suffix) if part
            )
        elif key == "EMAIL":
            emails.append(value)
        elif key == "TEL":
            phones.append(value)
    contact = Contact.create(full_name or structured_name, emails, phones)
    return contact if contact.emails or contact.phones else None


def _parse_csv(path: Path) -> list[Contact]:
    text = _read_contact_text(path)
    try:
        rows = csv.DictReader(text.splitlines())
        if not rows.fieldnames:
            raise ContactImportError(f"{path.name} has no contact columns.")
        contacts: list[Contact] = []
        for row in rows:
            normalized = {
                re.sub(r"[^a-z0-9]+", " ", str(key).casefold()).strip(): str(
                    value or ""
                ).strip()
                for key, value in row.items()
                if key is not None
            }
            name = next(
                (
                    normalized[key]
                    for key in ("name", "full name", "display name")
                    if normalized.get(key)
                ),
                "",
            )
            if not name:
                name = " ".join(
                    value
                    for key in (
                        "prefix",
                        "given name",
                        "first name",
                        "middle name",
                        "additional name",
                        "family name",
                        "last name",
                        "suffix",
                    )
                    if (value := normalized.get(key, ""))
                )
            emails = [
                value
                for key, value in normalized.items()
                if ("email" in key or "e mail" in key)
                and _normalize_email(value)
            ]
            phones = [
                value
                for key, value in normalized.items()
                if any(word in key for word in ("phone", "mobile", "cell", "telephone"))
                and _normalize_phone(value)
            ]
            contact = Contact.create(name, emails, phones)
            if contact.emails or contact.phones:
                contacts.append(contact)
        return contacts
    except csv.Error as error:
        raise ContactImportError(f"{path.name} is not valid CSV.") from error


__all__ = [
    "Contact",
    "ContactImportError",
    "ContactImportResult",
    "ContactStore",
]
