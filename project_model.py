"""Authoritative, UI-independent authored project state.

All public reads are detached snapshots. Mutations go through the operations on
``ProjectModel``; disk transactions and transient selection live in their own
existing application layers. Template identifiers refer to PromptWriterPanel's
four existing specialized artwork models, not to display labels.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
import json
import logging
import math
from types import MappingProxyType
from threading import RLock
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4


FORMAT = "lettersmith.project"
SCHEMA_VERSION = 3
MIN_PAGES = 2
MAX_PAGES = 10
_PROJECT_DATA_FIELDS = ("metadata", "shared_settings", "prompt_settings", "sound",
                        "presentation", "publication", "recovery", "extensions")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageType:
    id: str
    label: str
    supports_message: bool
    artwork_template: str


PAGE_TYPES = MappingProxyType({
    "cover": PageType("cover", "Cover", False, "cover"),
    "illustration": PageType("illustration", "Illustration", False, "letter"),
    "letter": PageType("letter", "Letter", True, "wall"),
    "end_page": PageType("end_page", "End Page", False, "back"),
})
CLASSIC_PAGE_TYPES = ("cover", "illustration", "letter", "end_page")


class ProjectModelError(ValueError):
    pass


class UnsupportedProjectError(ProjectModelError):
    pass


class UnknownPageError(KeyError):
    pass


class PageLimitError(ProjectModelError):
    pass


class ProjectLayoutError(ProjectModelError):
    """A preserved draft layout cannot be used for display output."""

    def __init__(self, message: str, *, field: str = "message_layout") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class ModelChange:
    kind: str
    page_ids: tuple[str, ...]
    revision: int
    changed_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_model_id: int | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    page_id: str | None = None
    severity: str = "readiness"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message,
                "page_id": self.page_id, "severity": self.severity}


@dataclass(frozen=True)
class DeletionRecord:
    project_id: str
    page: dict[str, Any]
    index: int


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectModelError(f"{field} must be an object")
    result = deepcopy(dict(value))
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ProjectModelError(f"{field} must contain JSON data") from error
    return result


def page_type(identifier: str) -> PageType:
    try:
        return PAGE_TYPES[identifier]
    except (KeyError, TypeError) as error:
        raise UnsupportedProjectError(f"Unsupported page type: {identifier!r}") from error


def empty_message() -> dict[str, Any]:
    """A fresh independent rich-text document, with no imposed greeting fields."""
    return {"format": "html", "html": "", "resources": [], "layout": {}, "settings": {}}


def new_page(kind: str, *, name: str = "", page_id: str | None = None) -> dict[str, Any]:
    definition = page_type(kind)
    if not isinstance(name, str):
        raise ProjectModelError("Page name must be text")
    return {
        "id": page_id or str(uuid4()), "type": kind, "name": name,
        "artwork": {"asset": None, "media_type": None, "placement": {}, "animation": {}},
        "prompt": {"directions": "", "overrides": {}, "template_id": definition.artwork_template,
                   "resolved_components": {}, "text": None, "options": {}, "stale": False},
        "message": empty_message() if definition.supports_message else None,
        "design_overrides": {}, "recovery": {}, "revision": 0, "preview_stale": True,
    }


def _validate_message(value: object) -> dict[str, Any]:
    document = _object(value, "message")
    if document.get("format") != "html" or not isinstance(document.get("html"), str):
        raise ProjectModelError("Message must preserve an HTML document")
    if not isinstance(document.get("resources", []), list):
        raise ProjectModelError("Message resources must be a list")
    for field in ("layout", "settings"):
        _object(document.get(field, {}), f"message.{field}")
    return document


def _validate_page(value: object) -> dict[str, Any]:
    page = _object(value, "page")
    if not isinstance(page.get("id"), str) or not page["id"].strip():
        raise ProjectModelError("Page ID must be nonempty text")
    definition = page_type(page.get("type"))
    if not isinstance(page.get("name", ""), str):
        raise ProjectModelError("Page name must be text")
    for field in ("artwork", "prompt", "design_overrides", "recovery"):
        _object(page.get(field), f"page.{field}")
    if "inactive_message" in page["recovery"]:
        _validate_message(page["recovery"]["inactive_message"])
    artwork = page["artwork"]
    if artwork.get("asset") is not None and not isinstance(artwork["asset"], str):
        raise ProjectModelError("Artwork asset must be text or null")
    for field in ("placement", "animation"):
        _object(artwork.get(field, {}), f"artwork.{field}")
    prompt = page["prompt"]
    if prompt.get("text") is not None and not isinstance(prompt["text"], str):
        raise ProjectModelError("Prompt text must be text or null")
    if not isinstance(prompt.get("directions", ""), str):
        raise ProjectModelError("Prompt directions must be text")
    if prompt.get("template_id") != definition.artwork_template:
        raise ProjectModelError("Prompt template does not match the canonical page type")
    for field in ("overrides", "resolved_components", "options"):
        _object(prompt.get(field, {}), f"prompt.{field}")
    if definition.supports_message:
        _validate_message(page.get("message"))
    elif page.get("message") is not None:
        raise ProjectModelError("Only Letter pages may have an active message")
    revision = page.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ProjectModelError("Page revision must be a nonnegative integer")
    return page


def _validate_project_recovery(value: Mapping[str, Any]) -> None:
    for field in ("deleted_pages", "missing_resources", "unresolved_associations"):
        if field in value and not isinstance(value[field], list):
            raise ProjectModelError(f"Project recovery.{field} must be a list")
    for record in value.get("deleted_pages", []):
        if not isinstance(record, dict) or not isinstance(record.get("index"), int) or record["index"] < 0:
            raise ProjectModelError("Deleted page recovery record is malformed")
        _validate_page(record.get("page"))


def resolve_page_layout(page: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve version-1 logical CSS-pixel geometry without editing authored data.

    Shared settings < page design overrides < explicit document/artwork fields.
    The caller passes already resolved settings; nested geometry fields merge.
    Empty objects inherit, while a null settings override clears its default.
    Unknown historical objects remain savable but are never assigned guessed units.
    """
    field = "message_layout"

    def fail(message):
        raise ProjectLayoutError(message, field=field)

    def obj(value, label, allowed):
        if not isinstance(value, Mapping) or set(value) - set(allowed):
            fail(f"{label} has unsupported fields or is not an object.")
        return dict(value)

    def number(value, label, low=0, high=100000):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not low <= value <= high or not math.isfinite(value):
            fail(f"{label} must be a finite number between {low} and {high}.")
        return value

    def merge(base, explicit):
        if base is None:
            base = {}
        if not isinstance(base, Mapping) or not isinstance(explicit, Mapping):
            fail(f"{field} must be an object or a null inherited default.")
        result = deepcopy(dict(base))
        for key, value in explicit.items():
            if key in ("canvas", "box", "padding", "position") and isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = {**result[key], **deepcopy(dict(value))}
            else:
                result[key] = deepcopy(value)
        return result

    def canvas(value):
        size = obj(value, "Canvas", ("width", "height"))
        return {axis: number(size.get(axis), f"Canvas {axis}", 1, 16384) for axis in ("width", "height")}

    message = None
    document = page.get("message")
    if document is not None:
        raw = merge(settings.get("message_layout"), document.get("layout", {}))
        if raw:
            obj(raw, "Message layout", ("version", "units", "canvas", "box", "padding", "text_align", "overflow"))
            if type(raw.get("version")) is not int or raw["version"] != 1 or raw.get("units") != "css_px":
                fail("Message layout requires version 1 and explicit css_px units; older layout data is preserved.")
            size = canvas(raw.get("canvas"))
            box = obj(raw.get("box"), "Text box", ("x", "y", "width", "height"))
            box = {key: number(box.get(key), f"Text box {key}", 1 if key in ("width", "height") else 0) for key in ("x", "y", "width", "height")}
            if box["x"] + box["width"] > size["width"] or box["y"] + box["height"] > size["height"]:
                fail("The text box must fit inside its logical canvas.")
            padding = obj(raw.get("padding", {}), "Text padding", ("top", "right", "bottom", "left"))
            padding = {side: number(padding.get(side, 0), f"Padding {side}") for side in ("top", "right", "bottom", "left")}
            if padding["left"] + padding["right"] >= box["width"] or padding["top"] + padding["bottom"] >= box["height"]:
                fail("Text padding must leave positive space for the message.")
            alignment = raw.get("text_align", "inherit")
            if alignment not in ("inherit", "left", "center", "right", "justify", "start", "end"):
                fail("Text alignment is unsupported.")
            if raw.get("overflow", "scroll") != "scroll":
                fail("Message overflow must use scroll; clipping or automatic text shrinking is unsupported.")
            message = {"version": 1, "units": "css_px", "canvas": size, "box": box,
                       "padding": padding, "text_align": alignment, "overflow": "scroll"}

    field = "artwork_placement"
    raw = merge(settings.get("artwork_placement"), page.get("artwork", {}).get("placement", {}))
    artwork = None
    if raw or message is not None:
        obj(raw, "Artwork placement", ("version", "canvas", "fit", "position"))
        if raw and (type(raw.get("version")) is not int or raw["version"] != 1):
            fail("Artwork placement requires version 1; older placement data is preserved.")
        size = canvas(raw.get("canvas", message["canvas"] if message else None))
        if message is not None and size != message["canvas"]:
            fail("Artwork and message must use the same logical canvas.")
        fit = raw.get("fit", "contain")
        if fit not in ("contain", "cover"):
            fail("Artwork fit must be contain or cover.")
        position = obj(raw.get("position", {}), "Artwork position", ("x", "y"))
        position = {axis: number(position.get(axis, .5), f"Artwork position {axis}", 0, 1) for axis in ("x", "y")}
        artwork = {"version": 1, "canvas": size, "fit": fit, "position": position}
    return {"message": message, "artwork": artwork}


def _reading(method):
    @wraps(method)
    def synchronized(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return synchronized


def _mutating(method):
    """Publish after the model lock is released, avoiding session/model inversion."""
    @wraps(method)
    def synchronized(self, *args, **kwargs):
        with self.mutation_scope():
            return method(self, *args, **kwargs)
    return synchronized


class ProjectModel:
    """One ordered page collection, including safely savable unfinished drafts."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        data = _object(payload, "project")
        if data.get("format") != FORMAT or data.get("schema_version") != SCHEMA_VERSION:
            raise UnsupportedProjectError("Unsupported project format or schema version")
        if not isinstance(data.get("project_id"), str) or not data["project_id"].strip():
            raise ProjectModelError("Project ID must be nonempty text")
        if not isinstance(data.get("pages"), list):
            raise ProjectModelError("Project pages must be an ordered list")
        pages = [_validate_page(page) for page in data["pages"]]
        if len({page["id"] for page in pages}) != len(pages):
            raise ProjectModelError("Page IDs must be unique")
        for field in _PROJECT_DATA_FIELDS:
            _object(data.get(field), field)
        _validate_project_recovery(data["recovery"])
        revision = data.get("revision", 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ProjectModelError("Project revision must be a nonnegative integer")
        data["pages"] = pages
        self._data = data
        self._lock = RLock()
        self._mutation_depth = 0
        self._pending_changes: list[ModelChange] = []
        self._input_versions: dict[tuple[str, str], int] = {}
        self._project_input_versions: dict[str, int] = {}
        self._listeners: list[Callable[[ModelChange], None]] = []

    @contextmanager
    def mutation_scope(self):
        """Make a guard and its mutations atomic; dispatch after releasing lock.

        This is synchronization, not rollback: validate operations before edits.
        Nested public operations publish only after the outer scope completes.
        """
        notifications = []
        try:
            with self._lock:
                self._mutation_depth += 1
                try:
                    yield self
                finally:
                    self._mutation_depth -= 1
                    if not self._mutation_depth:
                        notifications = [(event, tuple(self._listeners)) for event in self._pending_changes]
                        self._pending_changes.clear()
        finally:
            for change, listeners in notifications:
                for listener in listeners:
                    try:
                        listener(change)
                    except Exception:
                        _LOGGER.exception("Project listener failed after %s revision %s", change.kind, change.revision)

    @_reading
    def input_versions(self, page_id: str, fields: Iterable[str], *, include_shared: bool = True) -> tuple[int, ...]:
        """Read mutation-time counters; notifications are not the guard authority."""
        self._page(page_id)
        fields = tuple(fields)
        keys = [(page_id, "*"), *((page_id, field) for field in fields)]
        if '$order' in fields:
            keys.append(('*', 'page_order'))
        if include_shared:
            keys.append(("*", "shared_settings"))
        return tuple(self._input_versions.get(key, 0) for key in keys)

    @_reading
    def project_input_versions(self, fields: Iterable[str]) -> tuple[int, ...]:
        """Read independent mutation counters for authored project objects."""
        fields = tuple(fields)
        if any(field not in _PROJECT_DATA_FIELDS for field in fields):
            raise ProjectModelError("Unsupported project data field")
        return tuple(self._project_input_versions.get(field, 0) for field in fields)

    def _project_data_changed(self, fields: Iterable[str]) -> None:
        for field in fields:
            self._project_input_versions[field] = self._project_input_versions.get(field, 0) + 1

    def _record_change(self, change: ModelChange) -> None:
        if change.kind == 'structure':
            key = ('*', 'page_order')
            self._input_versions[key] = self._input_versions.get(key, 0) + 1
        for page_id, fields in change.changed_fields:
            for field in fields:
                key = (page_id, field)
                self._input_versions[key] = self._input_versions.get(key, 0) + 1
        self._pending_changes.append(replace(change, source_model_id=id(self)))

    @_mutating
    def adopt_snapshot(self, payload: Mapping[str, Any], *, kind: str = "workspace") -> None:
        """Foundation adapter: adopt same-project data without detaching writers."""
        candidate = ProjectModel.from_dict(payload).to_dict()
        if candidate["project_id"] != self.project_id:
            raise ProjectModelError("Adopting another project requires session replacement")
        if candidate == self._data:
            return
        before = {page["id"]: page for page in self._data["pages"]}
        after = {page["id"]: page for page in candidate["pages"]}
        changes = []
        for page_id in before.keys() | after.keys():
            if page_id not in before or page_id not in after:
                changes.append((page_id, ("*",)))
            else:
                fields = tuple(field for field in before[page_id].keys() | after[page_id].keys()
                               if before[page_id].get(field) != after[page_id].get(field))
                changes.append((page_id, fields))
        if any(self._data[key] != candidate[key] for key in ("shared_settings", "prompt_settings", "presentation")):
            changes.append(("*", ("shared_settings",)))
        if tuple(before) != tuple(after):
            changes.append(('*', ('page_order',)))
        self._project_data_changed(field for field in _PROJECT_DATA_FIELDS
                                   if self._data[field] != candidate[field])
        self._data = candidate
        self._record_change(ModelChange(kind, tuple(after), self.revision, tuple(changes)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectModel":
        return cls(payload)

    @_reading
    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    @_reading
    def independent_copy(self, *, title: str | None = None) -> "ProjectModel":
        """Copy authored state; page IDs are scoped to the new project identity."""
        data = deepcopy(self._data)
        data["project_id"] = str(uuid4())
        data["revision"] = 0
        data["publication"] = {}
        for field in ("metadata", "shared_settings", "presentation"):
            for key in list(data[field]):
                if key.startswith(("published_", "publication_")) or key in (
                        "project_published_at", "active_play_dir", "protected_project_kind", "protected_project_master_path"):
                    data[field].pop(key)
        data["metadata"]["project_id"] = data["project_id"]
        if title is not None:
            data["metadata"]["recipient_title"] = title
            data["metadata"]["title"] = title
        for key in ("legacy_workspace_snapshot", "legacy_authored_snapshot"):
            data["extensions"].pop(key, None)
        data["metadata"]["copy_provenance"] = {"source_project_id": self.project_id}
        return ProjectModel.from_dict(data)

    @_reading
    def recoverable_items(self) -> tuple[dict, ...]:
        recovery = self._data["recovery"]
        items = []
        if isinstance(recovery.get("unassigned_message"), dict):
            items.append({"kind": "message", "key": "", "value": deepcopy(recovery["unassigned_message"])})
        for field, kind in (("unassigned_messages", "message"), ("unassigned_prompts", "prompt")):
            for key, value in recovery.get(field, {}).items():
                items.append({"kind": kind, "key": key, "value": deepcopy(value)})
        return tuple(items)

    @_mutating
    def assign_recovered(self, kind: str, key: str, page_id: str, *, replace: bool = False) -> None:
        """Explicit assignment; originals and displaced content remain recoverable."""
        item = next((item for item in self.recoverable_items() if item["kind"] == kind and item["key"] == key), None)
        if item is None:
            raise ProjectModelError("This recovery item no longer exists")
        page = self._page(page_id)
        if kind == "message" and page["type"] != "letter":
            raise ProjectModelError("Recovered messages require a Letter page")
        previous = deepcopy(page["message"] if kind == "message" else page["prompt"])
        occupied = previous.get("html") if kind == "message" else previous.get("text")
        if occupied and not replace:
            raise ProjectModelError("The destination is occupied; choose Replace, Copy to a new page, or Cancel")
        record = {**item, "page_id": page_id, "previous": previous}
        if kind == "message":
            self.update_message(page_id, item["value"])
        else:
            value = item["value"]
            self.replace_prompt(page_id, value if isinstance(value, dict) else {"text": value, "stale": False})
        recovery = deepcopy(self._data["recovery"])
        recovery.setdefault("assigned_content", []).append(record)
        assigned = {(record["kind"], record["key"]) for record in recovery["assigned_content"]}
        pending = [item for item in self.recoverable_items() if (item["kind"], item["key"]) not in assigned]
        unresolved = []
        for issue in recovery.get("unresolved_associations", []):
            content = issue.get("content")
            remaining = [item for item in pending if item["kind"] == ("prompt" if content == "prompts" else "message")]
            if content not in ("prompts", "message", "messages") or remaining:
                unresolved.append(issue)
        recovery["unresolved_associations"] = unresolved
        self.update_project_data("recovery", recovery)

    @property
    @_reading
    def project_id(self) -> str:
        return self._data["project_id"]

    @property
    @_reading
    def revision(self) -> int:
        return self._data.get("revision", 0)

    @property
    @_reading
    def pages(self) -> list[dict[str, Any]]:
        return deepcopy(self._data["pages"])

    def _page(self, page_id: str) -> dict[str, Any]:
        for page in self._data["pages"]:
            if page["id"] == page_id:
                return page
        raise UnknownPageError(page_id)

    @_reading
    def get_page(self, page_id: str) -> dict[str, Any]:
        return deepcopy(self._page(page_id))

    @_reading
    def add_listener(self, listener: Callable[[ModelChange], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    @_reading
    def remove_listener(self, listener: Callable[[ModelChange], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _changed(self, kind: str, ids: Iterable[str], *, stale: bool = True,
                 fields: tuple[str, ...] | None = None) -> None:
        page_ids = tuple(ids)
        self._data["revision"] = self.revision + 1
        for page in self._data["pages"]:
            if page["id"] in page_ids:
                page["revision"] = page.get("revision", 0) + 1
                if stale:
                    page["preview_stale"] = True
        if fields is None:
            fields = {"structure": ("*",), "name": ("name",), "artwork": ("artwork",),
                      "message": ("message",), "prompt": ("prompt",),
                      "page_design": ("design_overrides",), "shared_design": (),
                      "project_data": ()}[kind]
        changed_fields = tuple((page_id, fields) for page_id in page_ids)
        if kind == "shared_design":
            changed_fields += (("*", ("shared_settings",)),)
        if not changed_fields:
            changed_fields = (("*", ()),)
        self._record_change(ModelChange(kind, page_ids, self.revision, changed_fields))

    def _insert(self, page: dict[str, Any], index: int | None) -> str:
        if len(self._data["pages"]) >= MAX_PAGES:
            raise PageLimitError(f"Interactive projects support at most {MAX_PAGES} pages")
        position = len(self._data["pages"]) if index is None else index
        if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= len(self._data["pages"]):
            raise ProjectModelError("Page insertion index is out of range")
        self._data["pages"].insert(position, page)
        self._invalidate_order_prompts()
        self._changed("structure", [page["id"]])
        return page["id"]

    def _invalidate_order_prompts(self) -> None:
        for page in self._data["pages"]:
            if page["prompt"].get("text") is not None:
                page["prompt"]["stale"] = True

    @_mutating
    def add_page(self, kind: str, *, name: str = "", index: int | None = None) -> str:
        return self._insert(new_page(kind, name=name), index)

    @_mutating
    def duplicate_page(self, page_id: str, *, index: int | None = None) -> str:
        page = self.get_page(page_id)
        page["id"] = str(uuid4())
        page["revision"] = 0
        page["recovery"].pop("legacy_key", None)
        if index is None:
            index = next(i for i, existing in enumerate(self._data["pages"]) if existing["id"] == page_id) + 1
        return self._insert(page, index)

    @_mutating
    def rename_page(self, page_id: str, name: str) -> None:
        if not isinstance(name, str):
            raise ProjectModelError("Page name must be text")
        page = self._page(page_id)
        if page["name"] != name:
            page["name"] = name
            self._changed("name", [page_id], stale=False)

    @_mutating
    def reorder_pages(self, page_ids: Iterable[str]) -> None:
        order = list(page_ids)
        existing = {page["id"]: page for page in self._data["pages"]}
        if len(order) != len(existing) or set(order) != set(existing):
            raise ProjectModelError("Order must include every page ID exactly once")
        if order != list(existing):
            self._data["pages"] = [existing[page_id] for page_id in order]
            self._invalidate_order_prompts()
            self._changed("structure", order, stale=False, fields=())

    @_mutating
    def delete_page(self, page_id: str) -> DeletionRecord:
        page = self._page(page_id)
        if len(self._data["pages"]) <= MIN_PAGES:
            raise PageLimitError(f"Interactive deletion must retain at least {MIN_PAGES} pages; reset can create an empty session")
        index = self._data["pages"].index(page)
        record = DeletionRecord(self.project_id, deepcopy(page), index)
        self._data["pages"].pop(index)
        self._data["recovery"].setdefault("deleted_pages", []).append({"page": deepcopy(page), "index": index})
        self._project_data_changed(("recovery",))
        self._invalidate_order_prompts()
        self._changed("structure", [page_id])
        return record

    @_reading
    def deletion_scope(self, page_id: str) -> dict[str, Any]:
        page = self.get_page(page_id)
        return {"page_id": page_id, "name": page["name"], "type": page["type"],
                "artwork": page["artwork"], "message": page["message"], "prompt": page["prompt"],
                "retains_assets": True, "undo_available": True}

    @_mutating
    def restore_page(self, record: DeletionRecord) -> str:
        if record.project_id != self.project_id:
            raise ProjectModelError("Deleted page belongs to another project")
        page = _validate_page(record.page)
        if any(existing["id"] == page["id"] for existing in self._data["pages"]):
            raise ProjectModelError("Deleted page identity is already active")
        if len(self._data["pages"]) >= MAX_PAGES:
            raise PageLimitError(f"Interactive projects support at most {MAX_PAGES} pages")
        if isinstance(record.index, bool) or not isinstance(record.index, int) or record.index < 0:
            raise ProjectModelError("Deleted page index must be a nonnegative integer")
        deleted = self._data["recovery"].get("deleted_pages", [])
        retained = [item for item in deleted if item["page"]["id"] != page["id"]]
        if "deleted_pages" not in self._data["recovery"] or retained != deleted:
            self._project_data_changed(("recovery",))
        self._data["recovery"]["deleted_pages"] = retained
        result = self._insert(page, min(record.index, len(self._data["pages"])))
        return result

    @_mutating
    def change_page_type(self, page_id: str, kind: str) -> None:
        definition = page_type(kind)
        page = self._page(page_id)
        if page["type"] == kind:
            return
        if page["message"] is not None:
            page["recovery"]["inactive_message"] = deepcopy(page["message"])
        page["message"] = (page["recovery"].pop("inactive_message", None) or empty_message()) if definition.supports_message else None
        page["type"] = kind
        page["prompt"]["template_id"] = definition.artwork_template
        page["prompt"]["stale"] = True
        self._changed("structure", [page_id], fields=("type", "message", "prompt"))

    def _update_field(self, page_id: str, field: str, patch: Mapping[str, Any], kind: str) -> None:
        page = self.get_page(page_id)
        page[field].update(_object(patch, field))
        _validate_page(page)
        if page[field] != self._page(page_id)[field]:
            self._page(page_id)[field] = page[field]
            self._changed(kind, [page_id])

    @_mutating
    def update_artwork(self, page_id: str, patch: Mapping[str, Any]) -> None:
        self._update_field(page_id, "artwork", patch, "artwork")

    @_reading
    def get_message(self, page_id: str) -> dict[str, Any] | None:
        return self.get_page(page_id)["message"]

    @_mutating
    def update_message(self, page_id: str, document: Mapping[str, Any]) -> None:
        page = self._page(page_id)
        if not page_type(page["type"]).supports_message:
            raise ProjectModelError("Only Letter pages may edit active messages")
        value = _validate_message(document)
        if page["message"] != value:
            layout_changed = page["message"].get("layout") != value.get("layout")
            page["message"] = value
            if layout_changed:
                page["prompt"]["stale"] = True
            self._changed("message", [page_id])

    @_reading
    def get_prompt(self, page_id: str) -> dict[str, Any]:
        return self.get_page(page_id)["prompt"]

    @_mutating
    def update_prompt(self, page_id: str, patch: Mapping[str, Any]) -> None:
        value = _object(patch, "prompt")
        if any(key in value for key in ("directions", "overrides", "options")) and "stale" not in value:
            value["stale"] = True
        self._update_field(page_id, "prompt", value, "prompt")

    @_mutating
    def replace_prompt(self, page_id: str, patch: Mapping[str, Any]) -> None:
        """Explicit build: retain one exact previous snapshot in page recovery."""
        page = self.get_page(page_id)
        previous = deepcopy(page["prompt"])
        page["prompt"].update(_object(patch, "prompt"))
        _validate_page(page)
        if previous.get("text") is not None:
            self.update_page_recovery(page_id, {"previous_prompt": previous})
        self.update_prompt(page_id, patch)

    @_mutating
    def restore_previous_prompt(self, page_id: str) -> bool:
        """Swap output snapshots only; current directions/overrides stay authored."""
        page = self.get_page(page_id)
        previous = page["recovery"].get("previous_prompt")
        if not isinstance(previous, dict) or not isinstance(previous.get("text"), str):
            return False
        self.update_prompt(page_id, {"text": previous["text"],
                                    "resolved_components": previous.get("resolved_components", {}),
                                    "stale": True})
        self.update_page_recovery(page_id, {"previous_prompt": page["prompt"]})
        return True

    @_mutating
    def resolve_prompt_components(self, factory: Callable[[], Mapping], *, reroll: bool = False) -> dict:
        """One project resolution, changed only on first build or explicit reroll."""
        resolved = self._data["prompt_settings"].get("resolved_components")
        if not reroll and isinstance(resolved, dict) and resolved:
            return deepcopy(resolved)
        legacy = [page["prompt"]["resolved_components"] for page in self._data["pages"]
                  if page["prompt"]["resolved_components"]]
        value = legacy[0] if not reroll and legacy and all(item == legacy[0] for item in legacy) else factory()
        value = _object(value, "resolved_components")
        self.update_project_data("prompt_settings", {"resolved_components": value})
        return deepcopy(value)

    @_mutating
    def update_page_settings(self, page_id: str, patch: Mapping[str, Any]) -> None:
        value = _object(patch, "design_overrides")
        page = self._page(page_id)
        merged = {**page["design_overrides"], **value}
        if merged != page["design_overrides"]:
            inputs = self._prompt_dependencies(page_id)
            page["design_overrides"] = merged
            if self._prompt_dependencies(page_id) != inputs:
                page["prompt"]["stale"] = True
            self._changed("page_design", [page_id])

    @_mutating
    def update_page_recovery(self, page_id: str, patch: Mapping[str, Any]) -> None:
        """Retain adapter provenance without replacing the active project session."""
        self._update_field(page_id, "recovery", patch, "project_data")

    @_mutating
    def clear_page_override(self, page_id: str, key: str) -> None:
        page = self._page(page_id)
        if key in page["design_overrides"]:
            inputs = self._prompt_dependencies(page_id)
            del page["design_overrides"][key]
            if self._prompt_dependencies(page_id) != inputs:
                page["prompt"]["stale"] = True
            self._changed("page_design", [page_id])

    @_mutating
    def update_shared_settings(self, patch: Mapping[str, Any]) -> None:
        self.update_project_data("shared_settings", patch)

    @_mutating
    def update_project_data(self, field: str, patch: Mapping[str, Any]) -> None:
        if field not in _PROJECT_DATA_FIELDS:
            raise ProjectModelError("Unsupported project data field")
        value = _object(patch, field)
        merged = {**self._data[field], **value}
        if field == "recovery":
            _validate_project_recovery(merged)
        if merged == self._data[field]:
            return
        dependencies = {page["id"]: self._prompt_dependencies(page["id"])
                        for page in self._data["pages"]} if field in {"shared_settings", "prompt_settings", "presentation"} else {}
        self._data[field] = merged
        self._project_data_changed((field,))
        ids = [] if field in {"sound", "extensions"} else [page["id"] for page in self._data["pages"]]
        if field in {"shared_settings", "prompt_settings", "presentation"}:
            for page in self._data["pages"]:
                if dependencies[page["id"]] != self._prompt_dependencies(page["id"]):
                    page["prompt"]["stale"] = True
        self._changed("shared_design" if field in {"shared_settings", "prompt_settings", "presentation"} else "project_data", ids)

    @_reading
    def resolve_settings(self, page_id: str) -> dict[str, Any]:
        return {**deepcopy(self._data["shared_settings"]), **deepcopy(self._page(page_id)["design_overrides"])}

    def _prompt_dependencies(self, page_id: str):
        """Inputs consumed by the artwork composer; rendered readability is separate."""
        page = self._page(page_id)
        choices = {**deepcopy(self._data["prompt_settings"]), **deepcopy(page["prompt"]["overrides"])}
        try:
            geometry = self.prompt_geometry(page_id)
        except ProjectModelError:
            geometry = (deepcopy(self._data["presentation"].get("artwork_output")),
                        deepcopy(page["message"].get("layout")) if page["message"] else None,
                        {key: self.resolve_settings(page_id).get(key) for key in ("message_layout", "artwork_placement")})
        return choices, geometry

    @_reading
    def resolve_layout(self, page_id: str) -> dict[str, Any]:
        """Return resolved geometry, or raise ProjectLayoutError for an invalid draft."""
        return resolve_page_layout(self._page(page_id), self.resolve_settings(page_id))

    @_reading
    def prompt_context(self, page_id: str) -> dict[str, Any]:
        page = self.get_page(page_id)
        return {"page_id": page_id, "type": page["type"], "template_id": page["prompt"]["template_id"],
                "page_count": len(self._data["pages"]),
                "page_order": [item["id"] for item in self._data["pages"]],
                "position": next(i + 1 for i, item in enumerate(self._data["pages"]) if item["id"] == page_id),
                "text_area": deepcopy(page["message"].get("layout", {})) if page["message"] is not None else None,
                "shared": deepcopy(self._data["prompt_settings"]), "prompt": page["prompt"]}

    @_reading
    def prompt_geometry(self, page_id: str) -> dict[str, Any]:
        """Artwork pixels plus message geometry; never includes message text.

        Empty legacy layouts use the existing responsive, centered viewer panel.
        Explicit rectangles use normalized image coordinates or artwork pixels.
        Unknown historical layout conventions remain stored, but cannot be built.
        """
        output = self._data["presentation"].get("artwork_output", {})
        width, height = output.get("width", 2048), output.get("height", 3072)
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (width, height)):
            raise ProjectModelError("Artwork dimensions must be positive whole pixels")
        divisor = math.gcd(width, height)
        result = {"width": width, "height": height, "aspect_ratio": f"{width // divisor}:{height // divisor}",
                  "orientation": "landscape" if width > height else "portrait" if height > width else "square",
                  "text_area": None}
        message = self._page(page_id)["message"]
        if message is not None:
            area = deepcopy(message.get("layout", {}))
            settings = self.resolve_settings(page_id)
            if area.get("units") == "css_px" or settings.get("message_layout"):
                layout = self.resolve_layout(page_id)["message"]
                if layout is None:
                    area = {"mode": "responsive_center"}
                else:
                    area = {"units": "normalized", **{key: layout["box"][key] / layout["canvas"][
                        "width" if key in ("x", "width") else "height"] for key in ("x", "y", "width", "height")}}
            elif not area or area == {"mode": "responsive_center"}:
                area = {"mode": "responsive_center"}
            else:
                units = area.get("units")
                if units not in {"normalized", "pixels"}:
                    raise ProjectModelError("The Letter text area needs explicit normalized or pixels units")
                values = [area.get(key) for key in ("x", "y", "width", "height")]
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise ProjectModelError("The Letter text area needs finite x, y, width and height")
                x, y, w, h = values
                limit_x, limit_y = (1, 1) if units == "normalized" else (width, height)
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > limit_x or y + h > limit_y:
                    raise ProjectModelError("The Letter text area must fit inside the artwork")
                area = {key: area[key] for key in ("units", "x", "y", "width", "height")}
            result["text_area"] = area
        return result

    @_reading
    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not MIN_PAGES <= len(self._data["pages"]) <= MAX_PAGES:
            issues.append(ValidationIssue("page_count", f"Final output supports {MIN_PAGES}–{MAX_PAGES} pages; all imported pages are retained"))
        if self._data["recovery"].get("unresolved_associations"):
            issues.append(ValidationIssue("unresolved_associations", "Legacy content ownership needs review before final output"))
        for resource in self._data["recovery"].get("missing_resources", []):
            issues.append(ValidationIssue("missing_resource", f"Project resource is missing: {resource}"))
        for page in self._data["pages"]:
            try:
                self.resolve_layout(page["id"])
            except ProjectLayoutError as error:
                issues.append(ValidationIssue("unsupported_layout" if error.field == "message_layout" else "unsupported_placement", str(error), page["id"]))
            if not page["artwork"].get("asset"):
                issues.append(ValidationIssue("missing_artwork", "Page has no artwork", page["id"]))
            if page["message"] is not None and not page["message"]["html"].strip():
                issues.append(ValidationIssue("empty_message", "Letter message is empty", page["id"]))
        return tuple(issues)

    @_reading
    def render_snapshot(self) -> dict[str, Any]:
        data = self.to_dict()
        for page in data["pages"]:
            page["resolved_settings"] = self.resolve_settings(page["id"])
            try:
                page["resolved_layout"] = self.resolve_layout(page["id"])
            except ProjectLayoutError:
                page["resolved_layout"] = None  # Preserved draft; validation explains the problem.
        data["validation"] = [issue.to_dict() for issue in self.validate()]
        data["ready"] = not data["validation"]
        return data


def create_project(page_types: Iterable[str] = CLASSIC_PAGE_TYPES, *, metadata: Mapping[str, Any] | None = None,
                   project_id: str | None = None) -> ProjectModel:
    types = list(page_types)
    if len(types) > MAX_PAGES:
        raise PageLimitError(f"New projects support at most {MAX_PAGES} pages")
    return ProjectModel({"format": FORMAT, "schema_version": SCHEMA_VERSION,
                         "project_id": project_id or str(uuid4()), "revision": 0,
                         "pages": [new_page(kind) for kind in types],
                         "metadata": _object(metadata or {}, "metadata"), "shared_settings": {},
                         "prompt_settings": {}, "sound": {}, "presentation": {},
                         "publication": {}, "recovery": {}, "extensions": {}})
