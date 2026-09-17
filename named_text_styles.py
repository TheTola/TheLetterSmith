"""Validated text-style definitions and reusable editor style sets.

The active set belongs to one letter. Saved slots belong to application
settings and are never changed by editing or loading an active set.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from letter_page import MESSAGE_RENDER_FONT_FAMILY, MESSAGE_RENDER_FONT_SIZE
from settings_store import SettingsStore


STYLE_SCHEMA_VERSION = 1
STYLE_KEYS = (
    "normal_text",
    "subtitle",
    "title",
    "heading_1",
    "heading_2",
    "heading_3",
)
STYLE_LABELS = {
    "normal_text": "Normal text",
    "subtitle": "Subtitle",
    "title": "Title",
    "heading_1": "Heading 1",
    "heading_2": "Heading 2",
    "heading_3": "Heading 3",
}
SAVED_STYLE_SLOT_KEYS = {
    1: "editor_style_set_1",
    2: "editor_style_set_2",
    3: "editor_style_set_3",
}
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z")
_STYLE_FIELDS = frozenset(
    {
        "font_family",
        "font_size",
        "font_color",
        "font_weight",
        "italic",
        "underline",
        "strikethrough",
    }
)
STYLE_PROPERTIES = tuple(
    name
    for name in (
        "font_family",
        "font_size",
        "font_color",
        "font_weight",
        "italic",
        "underline",
        "strikethrough",
    )
)
DOCUMENT_STYLE_SCHEMA_VERSION = 1
MAX_STYLE_BLOCKS = 50_000
MAX_STYLE_OVERRIDE_SPANS = 250_000
MAX_STYLE_BLOCK_LENGTH = 1_000_000


class InvalidStyleSetError(ValueError):
    """A stored style set cannot be loaded without losing information."""


class StyleSetOverwriteRequired(ValueError):
    """The chosen slot contains a different style set."""


def _validate_style_key(key: str) -> None:
    if key not in STYLE_KEYS:
        raise ValueError(f"Unknown named text style: {key!r}")


@dataclass(frozen=True)
class StyleDefinition:
    font_family: str
    font_size: float
    font_color: str
    font_weight: int
    italic: bool
    underline: bool
    strikethrough: bool

    def __post_init__(self) -> None:
        family = self.font_family
        if (
            not isinstance(family, str)
            or not family.strip()
            or len(family) > 255
            or any(ord(character) < 32 for character in family)
        ):
            raise InvalidStyleSetError("Invalid font family")
        if (
            isinstance(self.font_size, bool)
            or not isinstance(self.font_size, (int, float))
            or not math.isfinite(self.font_size)
            or not 1 <= self.font_size <= 100
        ):
            raise InvalidStyleSetError("Invalid font size")
        if not isinstance(self.font_color, str) or not _COLOR_RE.fullmatch(
            self.font_color
        ):
            raise InvalidStyleSetError("Invalid font color")
        if (
            isinstance(self.font_weight, bool)
            or not isinstance(self.font_weight, int)
            or not 1 <= self.font_weight <= 1000
        ):
            raise InvalidStyleSetError("Invalid font weight")
        for name in ("italic", "underline", "strikethrough"):
            if not isinstance(getattr(self, name), bool):
                raise InvalidStyleSetError(f"Invalid {name} value")

    def to_dict(self) -> dict[str, object]:
        return {
            "font_family": self.font_family,
            "font_size": float(self.font_size),
            "font_color": self.font_color,
            "font_weight": self.font_weight,
            "italic": self.italic,
            "underline": self.underline,
            "strikethrough": self.strikethrough,
        }

    @classmethod
    def from_dict(cls, data: object) -> StyleDefinition:
        if not isinstance(data, Mapping) or set(data) != _STYLE_FIELDS:
            raise InvalidStyleSetError("Incomplete named text style")
        try:
            return cls(**data)
        except TypeError as error:
            raise InvalidStyleSetError("Malformed named text style") from error


@dataclass(frozen=True)
class NamedStyleSet:
    definitions: Mapping[str, StyleDefinition]

    def __post_init__(self) -> None:
        if not isinstance(self.definitions, Mapping) or set(self.definitions) != set(
            STYLE_KEYS
        ):
            raise InvalidStyleSetError("A style set must contain all six styles")
        copied = dict(self.definitions)
        if any(not isinstance(value, StyleDefinition) for value in copied.values()):
            raise InvalidStyleSetError("Invalid named text style definition")
        object.__setattr__(self, "definitions", MappingProxyType(copied))

    def get(self, key: str) -> StyleDefinition:
        _validate_style_key(key)
        return self.definitions[key]

    def update_style(self, key: str, definition: StyleDefinition) -> NamedStyleSet:
        """Explicit Update; propagate only a changed Normal font family."""
        _validate_style_key(key)
        if not isinstance(definition, StyleDefinition):
            raise InvalidStyleSetError("Invalid named text style definition")
        updated = dict(self.definitions)
        old_family = updated["normal_text"].font_family
        updated[key] = definition
        if key == "normal_text" and definition.font_family != old_family:
            for other_key in STYLE_KEYS[1:]:
                updated[other_key] = replace(
                    updated[other_key], font_family=definition.font_family
                )
        return NamedStyleSet(updated)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": STYLE_SCHEMA_VERSION,
            "styles": {
                key: self.definitions[key].to_dict() for key in STYLE_KEYS
            },
        }

    @classmethod
    def from_dict(cls, data: object) -> NamedStyleSet:
        if not isinstance(data, Mapping) or set(data) != {"version", "styles"}:
            raise InvalidStyleSetError("Incomplete style set")
        version = data["version"]
        if type(version) is not int or version != STYLE_SCHEMA_VERSION:
            raise InvalidStyleSetError(f"Unsupported style set version: {version!r}")
        styles = data["styles"]
        if not isinstance(styles, Mapping) or set(styles) != set(STYLE_KEYS):
            raise InvalidStyleSetError("A style set must contain all six styles")
        return cls(
            {key: StyleDefinition.from_dict(styles[key]) for key in STYLE_KEYS}
        )


def default_style_set(*, color: str = "#eeeeee") -> NamedStyleSet:
    """Use the editor's existing Papyrus/16 baseline with distinct text roles."""
    family = MESSAGE_RENDER_FONT_FAMILY

    def definition(size: float, weight: int = 400, italic: bool = False) -> StyleDefinition:
        return StyleDefinition(family, size, color, weight, italic, False, False)

    return NamedStyleSet(
        {
            "normal_text": definition(MESSAGE_RENDER_FONT_SIZE),
            "subtitle": definition(20, italic=True),
            "title": definition(28, weight=700),
            "heading_1": definition(24, weight=700),
            "heading_2": definition(20, weight=700),
            "heading_3": definition(18, weight=700),
        }
    )


class SavedStyleSetStore:
    """Three independent settings keys, each holding one complete snapshot."""

    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings

    @staticmethod
    def _slot_key(slot: int) -> str:
        if type(slot) is not int or slot not in SAVED_STYLE_SLOT_KEYS:
            raise ValueError(f"Invalid style slot: {slot!r}")
        return SAVED_STYLE_SLOT_KEYS[slot]

    def raw_slot(self, slot: int) -> Any:
        return deepcopy(self.settings.get(self._slot_key(slot)))

    def load(self, slot: int) -> NamedStyleSet | None:
        raw = self.raw_slot(slot)
        return None if raw is None else NamedStyleSet.from_dict(raw)

    def save(
        self,
        slot: int,
        style_set: NamedStyleSet,
        *,
        overwrite: bool = False,
    ) -> bool:
        """Write only one slot; confirm changed contents before overwrite."""
        key = self._slot_key(slot)
        if not isinstance(style_set, NamedStyleSet):
            raise InvalidStyleSetError("Invalid active style set")
        payload = style_set.to_dict()
        current = self.settings.get(key)
        if current is not None:
            existing = NamedStyleSet.from_dict(current)
            if existing == style_set:
                return False
            if not overwrite:
                raise StyleSetOverwriteRequired(f"Style {slot} already has a saved set")
        self.settings.update_fields({key: payload})
        return True


def validate_document_style_state(
    data: object,
    *,
    block_lengths: list[int] | None = None,
) -> dict[str, object]:
    """Validate and detach per-letter state before any document mutation.

    Each override mask names properties with intentional direct formatting;
    the matching HTML fragments hold their resolved values.
    """
    if not isinstance(data, Mapping) or set(data) != {
        "schema_version",
        "definitions",
        "blocks",
    }:
        raise InvalidStyleSetError("Incomplete document style state")
    version = data["schema_version"]
    if type(version) is not int or version != DOCUMENT_STYLE_SCHEMA_VERSION:
        raise InvalidStyleSetError(f"Unsupported document style version: {version!r}")
    definitions = NamedStyleSet.from_dict(data["definitions"])
    blocks = data["blocks"]
    if not isinstance(blocks, list) or len(blocks) > MAX_STYLE_BLOCKS:
        raise InvalidStyleSetError("Invalid document style block list")
    if block_lengths is not None and len(block_lengths) != len(blocks):
        raise InvalidStyleSetError("Document style block count differs from HTML")
    canonical_blocks: list[dict[str, object]] = []
    total_spans = 0
    for block_index, block in enumerate(blocks):
        if not isinstance(block, Mapping) or set(block) != {"style", "overrides"}:
            raise InvalidStyleSetError("Malformed document style block")
        style = block["style"]
        if style is not None and style not in STYLE_KEYS:
            raise InvalidStyleSetError("Unknown paragraph style")
        spans = block["overrides"]
        if not isinstance(spans, list):
            raise InvalidStyleSetError("Malformed direct-formatting spans")
        total_spans += len(spans)
        if total_spans > MAX_STYLE_OVERRIDE_SPANS:
            raise InvalidStyleSetError("Too many direct-formatting spans")
        block_length = MAX_STYLE_BLOCK_LENGTH
        if block_lengths is not None:
            block_length = block_lengths[block_index]
            if (
                type(block_length) is not int
                or not 0 <= block_length <= MAX_STYLE_BLOCK_LENGTH
            ):
                raise InvalidStyleSetError("Invalid document block length")
        canonical_spans: list[dict[str, object]] = []
        for span in spans:
            if not isinstance(span, Mapping) or set(span) != {
                "start",
                "end",
                "mask",
            }:
                raise InvalidStyleSetError("Malformed direct-formatting span")
            start, end, mask = span["start"], span["end"], span["mask"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= block_length
            ):
                raise InvalidStyleSetError("Direct-formatting span exceeds its block")
            if (
                not isinstance(mask, list)
                or not mask
                or len(mask) > len(STYLE_PROPERTIES)
                or any(type(name) is not str for name in mask)
                or len(set(mask)) != len(mask)
                or not set(mask) <= _STYLE_FIELDS
            ):
                raise InvalidStyleSetError("Invalid direct-formatting property mask")
            canonical_spans.append(
                {
                    "start": start,
                    "end": end,
                    "mask": [name for name in STYLE_PROPERTIES if name in mask],
                }
            )
        canonical_blocks.append({"style": style, "overrides": canonical_spans})
    return {
        "schema_version": DOCUMENT_STYLE_SCHEMA_VERSION,
        "definitions": definitions.to_dict(),
        "blocks": canonical_blocks,
    }
