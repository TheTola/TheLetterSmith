from __future__ import annotations

import re
from dataclasses import dataclass

from config import safe_folder_name


class RecipientNameError(ValueError):
    pass


def _collapse_spaces(value: object) -> str:
    return " ".join(str(value or "").split())


def collapse_recipient_spacing(value: object) -> str:
    return _collapse_spaces(value)


def _capitalize_name_part(value: str) -> str:
    lowered = value.casefold()
    if lowered.startswith("mc") and len(lowered) > 2:
        return f"Mc{lowered[2].upper()}{lowered[3:]}"
    return lowered[:1].upper() + lowered[1:]


def _capitalize_name_component(component: str) -> str:
    return "".join(
        separator
        if separator in {"'", "\u2019", "-"}
        else _capitalize_name_part(separator)
        for separator in re.split(r"(['\u2019-])", component)
        if separator
    )


def validate_recipient_name(display_name: str) -> str:
    normalized = _collapse_spaces(display_name)
    if not normalized:
        raise RecipientNameError("Recipient is required.")
    if any(character in normalized for character in "\r\n\t"):
        raise RecipientNameError("Recipient must be a single line.")
    if not any(character.isalnum() for character in normalized):
        raise RecipientNameError(
            "Recipient must contain at least one letter or number."
        )
    return normalized


def normalize_recipient_display_name(
    raw_name: str,
    *,
    custom_capitalization: bool = False,
) -> str:
    collapsed = validate_recipient_name(raw_name)
    if custom_capitalization:
        return collapsed
    return " ".join(
        _capitalize_name_component(component)
        for component in collapsed.split(" ")
    )


def build_recipient_match_key(display_name: str) -> str:
    return validate_recipient_name(display_name).casefold()


def sanitize_recipient_folder_name(display_name: str) -> str:
    normalized = validate_recipient_name(display_name)
    return safe_folder_name(normalized, "Recipient")


@dataclass(frozen=True)
class RecipientName:
    display_name: str
    normalized_key: str
    folder_name: str

    @classmethod
    def from_raw(
        cls,
        raw_name: str,
        *,
        custom_capitalization: bool = False,
    ) -> "RecipientName":
        display_name = normalize_recipient_display_name(
            raw_name,
            custom_capitalization=custom_capitalization,
        )
        return cls(
            display_name=display_name,
            normalized_key=build_recipient_match_key(display_name),
            folder_name=sanitize_recipient_folder_name(display_name),
        )


__all__ = [
    "RecipientName",
    "RecipientNameError",
    "build_recipient_match_key",
    "collapse_recipient_spacing",
    "normalize_recipient_display_name",
    "sanitize_recipient_folder_name",
    "validate_recipient_name",
]
