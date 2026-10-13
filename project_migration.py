"""Explicit, side-effect-free conversion of inspected Letter Smith formats.

The repository reader supplies the legacy envelope below. This module never
opens files, generates artwork/prompts, edits libraries, or commits migrations.
The legacy keys are historical storage roles; only this boundary translates
old ``letter`` to canonical ``illustration``.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from project_model import (
    FORMAT, SCHEMA_VERSION, ProjectModel, ProjectModelError,
    UnsupportedProjectError, create_project, empty_message, new_page,
)


LEGACY_TYPE_MAPPING = {"cover": "cover", "letter": "illustration", "wall": "letter", "back": "end_page"}
LEGACY_PROMPT_LABELS = {"cover": "Cover Prompt", "letter": "Letter Prompt", "wall": "Wall Prompt", "back": "End Prompt"}


@dataclass
class MigrationReport:
    source_format: str
    source_schema: Any
    target_schema: int = SCHEMA_VERSION
    converted: bool = True
    role_mappings: list[dict[str, Any]] = field(default_factory=list)
    identities: list[dict[str, Any]] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved_associations: list[dict[str, Any]] = field(default_factory=list)
    validation: list[dict[str, Any]] = field(default_factory=list)
    locations: dict[str, str | None] = field(default_factory=lambda: {"source": None, "backup": None, "destination": None})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MigrationResult:
    project: ProjectModel
    report: MigrationReport


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectModelError(f"Legacy {field_name} must be an object")
    return deepcopy(dict(value))


def _legacy_version(envelope: Mapping[str, Any], metadata: Mapping[str, Any], source_format: str) -> None:
    version = metadata.get("schema_version")
    # Numeric 1–4 are the pre-release metadata generations present in historical
    # upgrade fixtures. They are unrelated to the marked canonical schema.
    if isinstance(version, (bool, float)) or version not in (None, "", "1.0", 1, 2, 3, 4, "1", "2", "3", "4"):
        raise UnsupportedProjectError(f"Unsupported legacy save schema: {version!r}")
    identity_version = metadata.get("project_schema_version", 2)
    if isinstance(identity_version, bool) or identity_version not in (None, 0, 1, 2):
        raise UnsupportedProjectError(f"Unsupported legacy project identity schema: {identity_version!r}")
    layout_version = envelope.get("page_layout_version", 1)
    if isinstance(layout_version, bool) or layout_version != 1:
        raise UnsupportedProjectError(f"Unsupported legacy page layout version: {layout_version!r}")
    if metadata.get("document_type") not in (None, "", "saved_letter", "project_autosave"):
        raise UnsupportedProjectError("Unsupported legacy document type")


def _asset_path(prefix: str, filename: str) -> str:
    """Preserve references; the repository validates containment before copying."""
    normalized = filename.replace("\\", "/")
    if "/" in normalized:
        return normalized
    return str(PurePosixPath(prefix) / normalized) if prefix else normalized


def _identity(value: object, seen: set[str], report: MigrationReport, *, role: str) -> str:
    if isinstance(value, str) and value.strip() and value not in seen:
        identifier, action = value, "preserved"
    else:
        identifier = str(uuid4())
        action = "generated" if not value else "duplicate_repaired"
        if value:
            report.warnings.append(f"Duplicate or invalid {role} identity retained as provenance and replaced: {value!r}")
    seen.add(identifier)
    report.identities.append({"role": role, "source_id": value, "id": identifier, "action": action})
    return identifier


def migrate_project(payload: Mapping[str, Any], *, source_format: str | None = None) -> MigrationResult:
    """Convert a canonical document or an explicitly inspected legacy envelope.

    Legacy envelope keys: metadata/settings/sound/prompt_writer_state objects,
    page_layout list (or None), page_layout_version=1, images slot->record,
    asset_prefix string, message_html exact string (or None), message_resources
    list, resources list. Optional messages maps stable IDs/legacy keys to HTML
    documents; message_page_id explicitly assigns the old global document.
    Unknown envelope and metadata fields remain inert in extensions.
    """
    source = _mapping(payload, "project")
    if "format" in source:
        if source.get("format") != FORMAT:
            raise UnsupportedProjectError(f"Unknown project format: {source.get('format')!r}")
        project = ProjectModel.from_dict(source)
        report = MigrationReport(FORMAT, source["schema_version"], converted=False)
        report.identities = [{"role": "project", "id": project.project_id, "action": "preserved"}] + [
            {"role": "page", "id": page["id"], "action": "preserved"} for page in project.pages]
        report.validation = [issue.to_dict() for issue in project.validate()]
        return MigrationResult(project, report)
    if source_format not in {"legacy_active", "legacy_saved"}:
        raise UnsupportedProjectError("Unmarked data needs an inspected legacy_active or legacy_saved source format")
    metadata = _mapping(source.get("metadata", {}), "metadata")
    if "format" in metadata:
        raise UnsupportedProjectError("A marked project must be loaded through its canonical document")
    _legacy_version(source, metadata, source_format)
    report = MigrationReport(source_format, {"save": metadata.get("schema_version"),
                                            "identity": metadata.get("project_schema_version"),
                                            "layout": source.get("page_layout_version", 1)})
    settings = _mapping(source.get("settings", metadata.get("settings", {})), "settings")
    prompt_state = _mapping(source.get("prompt_writer_state", {}), "prompt_writer_state")
    prompt_version = prompt_state.get("version", 0)
    if isinstance(prompt_version, bool) or str(prompt_version) not in {str(i) for i in range(7)}:
        raise UnsupportedProjectError(f"Unsupported legacy Prompt Writer version: {prompt_version!r}")
    if not isinstance(prompt_state.get("approved_images", []), list):
        raise ProjectModelError("Legacy approved images must be a list")
    images = _mapping(source.get("images", {}), "images")
    if "slots" in images:
        if images.get("version", 1) != 1:
            raise UnsupportedProjectError("Unsupported legacy image manifest version")
        images = _mapping(images["slots"], "image slots")
    raw_layout = source.get("page_layout", metadata.get("page_layout"))
    if raw_layout is None:
        # Absent optional walls stay absent. Readers must enumerate actual source
        # assets, including classic files when no image manifest exists.
        raw_layout = [{"key": key, "kind": key} for key in LEGACY_TYPE_MAPPING if key in images]
        report.warnings.append("Classic slot structure inferred from actual artwork references; absent slots were not inserted")
    if not isinstance(raw_layout, list):
        raise ProjectModelError("Legacy page layout must be a list")
    project_id = _identity(metadata.get("project_id", settings.get("project_id")), set(), report, role="project")
    data = create_project([], metadata=metadata, project_id=project_id).to_dict()
    data["shared_settings"] = deepcopy(settings)
    data["sound"] = _mapping(source.get("sound", metadata.get("sound", {})), "sound")
    data["presentation"] = _mapping(source.get("presentation", metadata.get("presentation", {})), "presentation")
    data["publication"] = _mapping(source.get("publication", metadata.get("publication", {})), "publication")
    # Existing publication metadata is flat in SettingsStore. Preserve those
    # values without making them active configuration during conversion.
    for key, value in {**metadata, **settings}.items():
        if key.startswith(("published_", "publication_")):
            data["publication"].setdefault(key, deepcopy(value))
    data["extensions"]["legacy_source"] = deepcopy(source)
    data["extensions"]["legacy_source_format"] = source_format
    resources = source.get("resources", [])
    if not isinstance(resources, list):
        raise ProjectModelError("Legacy resources must be a list")
    data["extensions"]["resource_files"] = deepcopy(resources)
    prefix = source.get("asset_prefix", "")
    if not isinstance(prefix, str):
        raise ProjectModelError("Legacy asset prefix must be text")
    seen: set[str] = set()
    keys: dict[str, list[dict[str, Any]]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_layout):
        raw = _mapping(item, "page")
        kind = raw.get("kind", raw.get("type"))
        if not isinstance(kind, str) or kind not in LEGACY_TYPE_MAPPING:
            raise UnsupportedProjectError(f"Unrecognized legacy page meaning at position {index + 1}: {kind!r}")
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise ProjectModelError("Legacy page key is missing")
        page_id = _identity(raw.get("id", raw.get("page_id", key)), seen, report, role="page")
        page = new_page(LEGACY_TYPE_MAPPING[kind], name=raw.get("name", ""), page_id=page_id)
        page["recovery"] = {"legacy_key": key, "legacy_type": kind, "legacy_page": deepcopy(raw)}
        keys.setdefault(key, []).append(page)
        raw_by_id[page_id] = raw
        report.role_mappings.append({"page_id": page_id, "source_key": key, "source_type": kind, "type": page["type"]})
        record = raw.get("artwork", images.get(key, {}))
        record = _mapping(record, "artwork")
        filename = record.get("asset", record.get("source_file"))
        if filename is not None:
            if not isinstance(filename, str):
                raise ProjectModelError("Legacy image source must be text")
            asset = _asset_path(prefix, filename)
            gif = record.get("is_animated_gif") or record.get("asset_type") == "animated_gif" or filename.lower().endswith(".gif")
            media_type = record.get("media_type") or ("image/gif" if gif else "image/png")
            page["artwork"].update({"asset": asset, "media_type": media_type,
                                    "placement": deepcopy(record.get("placement", {})),
                                    "animation": deepcopy(record.get("animation", record.get("settings", {})))})
            report.assets.append({"page_id": page_id, "source": asset, "media_type": media_type, "action": "referenced_without_transformation"})
        page["recovery"]["legacy_image_record"] = record
        data["pages"].append(page)

    reference_counts: Counter[str] = Counter()
    for page in data["pages"]:
        raw = raw_by_id[page["id"]]
        reference_counts.update({reference for reference in (raw.get("id", raw.get("page_id")), page["recovery"]["legacy_key"])
                                 if isinstance(reference, str)})

    # Shared Prompt Writer selections stay shared, while resolved random choices
    # are copied exactly into each page snapshot. No prompt is generated here.
    page_keys = set(keys)
    data["prompt_settings"] = {key: deepcopy(value) for key, value in prompt_state.items()
                               if key not in page_keys | {"generated_prompts", "resolved_instructions"}}
    generated = _mapping(prompt_state.get("generated_prompts", {}), "generated_prompts")
    resolved = _mapping(prompt_state.get("resolved_instructions", {}), "resolved_instructions")
    consumed_prompts: set[str] = set()
    for page in data["pages"]:
        key = page["recovery"]["legacy_key"]
        raw = raw_by_id[page["id"]]
        page_prompt = raw.get("prompt")
        if page_prompt is not None:
            values = _mapping(page_prompt, "page prompt")
            page["prompt"].update(values)
            page["prompt"]["template_id"] = new_page(page["type"])["prompt"]["template_id"]
        else:
            details = prompt_state.get(key, "")
            if not isinstance(details, str):
                raise ProjectModelError("Legacy page directions must be text")
            page["prompt"]["directions"] = details
            candidate = next((name for name in (page["id"], key, LEGACY_PROMPT_LABELS.get(key), "Back Prompt" if key == "back" else None)
                              if name is not None and name in generated and reference_counts.get(name, 1) == 1), None)
            if candidate is not None and len(keys[key]) == 1:
                if not isinstance(generated[candidate], str):
                    raise ProjectModelError("Legacy prompt snapshots must be text")
                page["prompt"]["text"] = generated[candidate]
                consumed_prompts.add(candidate)
            page["prompt"]["resolved_components"] = deepcopy(resolved)
            page["prompt"]["options"]["approved_image"] = key in prompt_state.get("approved_images", [])
            page["prompt"]["stale"] = bool(prompt_state.get("generated_output_stale", False))
    orphan_prompts = {key: deepcopy(value) for key, value in generated.items() if key not in consumed_prompts}
    if orphan_prompts:
        data["recovery"]["unassigned_prompts"] = orphan_prompts
        report.unresolved_associations.append({"content": "prompts", "keys": list(orphan_prompts), "reason": "No unique stored page association"})

    documents = _mapping(source.get("messages", {}), "messages")
    consumed_documents: set[str] = set()
    for page in data["pages"]:
        raw = raw_by_id[page["id"]]
        key = page["recovery"]["legacy_key"]
        document = raw.get("message")
        if document is None:
            candidates = (raw.get("id", raw.get("page_id")), key)
            reference = next((item for item in candidates if item in documents and reference_counts[item] == 1), None)
            if reference is not None:
                document = documents[reference]
                consumed_documents.add(reference)
        if document is not None:
            message = _message(document, source)
            if page["type"] == "letter":
                page["message"] = message
                page["recovery"]["message_association"] = "explicit_page_reference"
            else:
                page["recovery"]["inactive_message"] = message
    global_html = source.get("message_html")
    if global_html is not None:
        if not isinstance(global_html, str):
            raise ProjectModelError("Legacy message HTML must be text")
        message = _message(global_html, source)
        reference = source.get("message_page_id", metadata.get("message_page_id"))
        if reference is not None:
            targets = [page for page in data["pages"] if reference in (
                raw_by_id[page["id"]].get("id", raw_by_id[page["id"]].get("page_id")),
                page["recovery"]["legacy_key"])]
            if len(targets) != 1:
                targets = []
        else:
            # Template.template_for_pages intentionally places one shared overlay
            # outside the slides. Template.isWallPage selects every legacy wall.
            targets = [page for page in data["pages"] if page["type"] == "letter"]
        targets = [page for page in targets if "message_association" not in page["recovery"]]
        if not targets:
            data["recovery"]["unassigned_message"] = message
            report.unresolved_associations.append({"content": "message", "source_reference": reference, "reason": "No provable eligible target"})
        else:
            for page in targets:
                if page["type"] == "letter":
                    page["message"] = deepcopy(message)
                else:
                    page["recovery"]["inactive_message"] = deepcopy(message)
                page["recovery"]["message_association"] = "explicit_page_reference" if reference is not None else "legacy_shared_wall_overlay"
            if len(targets) > 1:
                report.warnings.append(f"Legacy shared message rendered on {len(targets)} Wall pages was preserved as independent documents")
    unassigned = {key: value for key, value in documents.items() if key not in consumed_documents}
    if unassigned:
        data["recovery"]["unassigned_messages"] = deepcopy(unassigned)
        report.unresolved_associations.append({"content": "messages", "keys": list(unassigned), "reason": "Unknown page references"})
    if report.unresolved_associations:
        data["recovery"]["unresolved_associations"] = deepcopy(report.unresolved_associations)
    data["recovery"]["migration"] = {"source_format": source_format, "source_schema": report.source_schema,
                                    "warnings": deepcopy(report.warnings), "identities": deepcopy(report.identities)}
    project = ProjectModel.from_dict(data)
    report.validation = [issue.to_dict() for issue in project.validate()]
    return MigrationResult(project, report)


def _message(value: object, source: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        document = empty_message()
        document["html"] = value
        document["resources"] = deepcopy(source.get("message_resources", []))
        document["layout"] = deepcopy(source.get("message_layout", {}))
        document["settings"] = deepcopy(source.get("message_settings", {}))
        return document
    document = empty_message()
    document.update(_mapping(value, "message"))
    return document
