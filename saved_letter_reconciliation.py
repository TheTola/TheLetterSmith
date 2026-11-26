"""Recoverable consolidation for duplicate library and autosave directories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
import logging
from pathlib import Path
import uuid

from project_model import ProjectModel
from project_repository import (
    ProjectRepository, ProjectRepositoryError, StoredProject, _json, _safe_file,
    _writer_lock, read_legacy_project, selected_sound_tracks, sound_track_reference,
)
from transactional_io import atomic_write_json, is_link_or_reparse_point, _replace_directory_path


_LOGGER = logging.getLogger(__name__)
CONSOLIDATED_LETTERS = "Consolidated Letters"
AUTOSAVE_ARCHIVE = ".consolidated-autosaves"


def project_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return ""


def _name(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def load_letter(directory: Path, *, autosave: bool = False) -> StoredProject:
    repository = ProjectRepository(directory / ".lettersmith-project")
    if repository.directory.exists():
        return repository.load(allow_missing=True)
    migrated = read_legacy_project(directory, saved=True, autosave=autosave)
    return StoredProject(migrated.project, "", directory, migrated.report.to_dict())


def _content_evidence(stored: StoredProject):
    """Require authored text and artwork and compare the ordered media by bytes."""
    from generate import _file_content_digest
    from message_format import message_plain_text
    from message_html import sanitize_message_html

    document = stored.project.to_dict()
    manifest = _json(stored.asset_root / "resources.json") if stored.revision else {}
    unavailable = set(document["recovery"].get("missing_resources", ()))

    def digest(reference):
        if not reference or reference in unavailable:
            raise FileNotFoundError(str(reference))
        if stored.revision:
            return manifest["files"][reference]
        return _file_content_digest(_safe_file(stored.asset_root, reference)).hex()

    pages = []
    has_text = has_art = False
    try:
        for page in document["pages"]:
            reference = page["artwork"].get("asset")
            artwork = digest(reference) if reference else ""
            message = page.get("message") or {}
            html = sanitize_message_html(message.get("html", "")).strip()
            text = message_plain_text(html).strip()
            has_text |= bool(text)
            has_art |= bool(artwork)
            resources = tuple(sorted(digest(resource) for resource in message.get("resources", ())))
            pages.append((page["type"], artwork, text, html, resources))
        tracks = tuple(digest(sound_track_reference(track)) for track in selected_sound_tracks(document["sound"]))
        if not tracks and not stored.revision:
            for relative in ("gallery/sounds/music.mp3", "sounds/music.mp3"):
                if (stored.asset_root / relative).is_file():
                    tracks = (digest(relative),)
                    break
    except (KeyError, OSError, TypeError, ValueError, ProjectRepositoryError):
        return None
    return (tuple(pages), tracks) if has_text and has_art else None


@dataclass(frozen=True)
class LetterEvidence:
    stored: StoredProject
    project_id: str
    recipient_id: str
    recipient: str
    title: str
    published_url: str
    created_at: str
    explicit_copy: bool

    @classmethod
    def read(cls, stored, *, project_id=None, recipient_id=None, recipient=None, title=None):
        document = stored.project.to_dict()
        metadata = document["metadata"]
        return cls(stored, project_uuid(document["project_id"] if project_id is None else project_id),
                   project_uuid(metadata.get("recipient_id") if recipient_id is None else recipient_id),
                   _name(metadata.get("recipient_name") if recipient is None else recipient),
                   _name((metadata.get("recipient_title") or metadata.get("title")) if title is None else title),
                   str(document["publication"].get("published_page_url") or "").strip(),
                   str(metadata.get("project_created_at") or ""), bool(metadata.get("copy_provenance")))

    def matches(self, other: LetterEvidence, *, repaired_identity: bool = False) -> bool:
        same_project = bool(self.project_id and self.project_id == other.project_id)
        if not same_project and self.recipient and other.recipient and self.recipient != other.recipient:
            return False
        if self.recipient_id and other.recipient_id:
            if self.recipient_id != other.recipient_id:
                return False
            same_recipient = True
        else:
            same_recipient = bool(self.recipient and self.recipient == other.recipient
                                  and self.recipient != "unknown recipient")
        if same_project:
            # A named and an earlier unnamed draft can belong to one established ID.
            return same_recipient or (not (self.recipient and other.recipient) and
                                      bool(self.stored.revision and other.stored.revision))
        if not same_recipient or self.explicit_copy or other.explicit_copy:
            return False
        if self.published_url and other.published_url and self.published_url != other.published_url:
            return False
        same_publication = bool(self.published_url and self.published_url == other.published_url)
        if not repaired_identity and not same_publication:
            if (self.stored.revision and other.stored.revision and self.created_at and other.created_at
                    and self.created_at != other.created_at):
                return False
            return bool(self.contents & other.contents)
        return bool({content for _, content in self.contents} & {content for _, content in other.contents})

    @cached_property
    def contents(self):
        """A renamed head can match an older copy of one of its authored revisions."""
        results = set()
        if (content := _content_evidence(self.stored)) is not None:
            results.add((self.title, content))
        if self.stored.revision:
            repository = ProjectRepository(self.stored.asset_root.parents[1])
            for revision in repository.revision_history():
                if revision == self.stored.revision:
                    continue
                try:
                    stored = repository.load_revision(revision, allow_missing=True)
                    if stored.project.project_id == self.stored.project.project_id and (content := _content_evidence(stored)) is not None:
                        metadata = stored.project.to_dict()["metadata"]
                        title = _name(metadata.get("recipient_title") or metadata.get("title"))
                        results.add((title, content))
                except (OSError, ValueError, ProjectRepositoryError):
                    continue
        return results


def with_project_id(stored: StoredProject, project_id: str) -> ProjectModel:
    document = stored.project.to_dict()
    document["project_id"] = project_id
    document["metadata"]["project_id"] = project_id
    return ProjectModel.from_dict(document)


def merge_duplicate(archive: Path, source: Path, target: Path, *, original: StoredProject,
                    canonical: StoredProject, project_id: str) -> None:
    """Record all revisions before moving the original to recoverable storage."""
    if source.is_relative_to(target) or target.is_relative_to(source) or any(
            is_link_or_reparse_point(path) for path in (source, target, archive)):
        raise ValueError("Duplicate letters must be independent local directories.")
    repository = ProjectRepository(target / ".lettersmith-project")
    source_repository = ProjectRepository(source / ".lettersmith-project")
    if not canonical.revision:
        canonical = repository.save(with_project_id(canonical, project_id), asset_root=canonical.asset_root,
                                    migration_report=canonical.migration_report, track_history=True)
    source_history = source_repository.revision_history() if original.revision else ()
    revisions = {}
    for revision in source_history or ("",):
        stored = (source_repository.load_revision(revision, allow_missing=True)
                  if revision and revision != original.revision else original)
        item = repository.save(with_project_id(stored, project_id), asset_root=stored.asset_root,
                               expected_revision=canonical.revision, migration_report=stored.migration_report,
                               track_history=True, make_current=False, ignore_page_ids=True)
        revisions[revision or "legacy"] = item.revision
    folder = archive / uuid.uuid4().hex
    folder.mkdir(parents=True)
    atomic_write_json(folder / "reconciliation.json", {
        "version": 1, "archived_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source), "source_project_id": original.project.project_id,
        "source_revision": original.revision, "canonical": str(target), "project_id": project_id,
        "canonical_revision": canonical.revision, "revisions": revisions,
    })
    _replace_directory_path(source, folder / "original")


def saved_archives(recovery_root: Path):
    for path in (recovery_root / CONSOLIDATED_LETTERS).glob("*/reconciliation.json"):
        if is_link_or_reparse_point(path.parent) or not (path.parent / "original").is_dir():
            continue
        try:
            record = _json(path)
            if record.get("version") == 1 and all(isinstance(record.get(key), str) and record[key]
                    for key in ("source", "source_project_id", "canonical", "project_id")):
                yield record
        except ProjectRepositoryError:
            _LOGGER.warning("Could not read saved letter reconciliation record %s", path, exc_info=True)


def reconcile_saved_letters(catalog, entries):
    """Reconcile only confirmed pairs among normal library entries."""
    from settings_store import SettingsStore

    archive = catalog.recovery_root / CONSOLIDATED_LETTERS
    editable = catalog.play_root / "Editable Projects"
    by_id, by_title = {}, {}
    for entry in entries:
        if not entry.valid or entry.recovery or entry.stock or entry.example:
            continue
        if entry.project_id:
            by_id.setdefault(entry.project_id, []).append(entry)
        if entry.recipient and entry.title:
            by_title.setdefault((_name(entry.recipient), _name(entry.title)), []).append(entry)
    candidates = {entry.path: entry for group in (*by_id.values(), *by_title.values()) if len(group) > 1 for entry in group}
    if not candidates:
        return entries

    workspace = catalog.project_root / ".lettersmith-project"
    active_id = SettingsStore(catalog.project_root).get("project_id", "")
    try:
        binding = _json(workspace / "editable-save.json", optional=True)
    except ProjectRepositoryError:
        binding = {}
    active_path = Path(binding["directory"]) if binding.get("directory") and binding.get("project_id") == active_id else None
    ranked = sorted(candidates.values(), key=lambda entry: (
        not entry.published, entry.path != active_path, not active_id or entry.project_id != active_id,
        entry.path.parent != editable, not entry.canonical, not entry.project_id,
        entry.modified_at.timestamp(), str(entry.path).casefold(),
    ))
    removed, refreshed, evidence = set(), {}, {}

    def read(entry):
        if entry.path not in evidence:
            try:
                stored = load_letter(entry.path)
                if stored.revision != entry.storage_revision:
                    raise ValueError("The letter changed before reconciliation.")
                evidence[entry.path] = LetterEvidence.read(stored, project_id=entry.project_id,
                    recipient_id=entry.recipient_id, recipient=entry.recipient, title=entry.title)
            except (OSError, ValueError, ProjectRepositoryError):
                evidence[entry.path] = None
                _LOGGER.info("Leaving unreadable duplicate candidate in place: %s", entry.path, exc_info=True)
        return evidence[entry.path]

    with _writer_lock(editable / ".library-lock"):
        for entry in ranked:
            if entry.path in removed:
                continue
            canonical = read(entry)
            if canonical is None:
                continue
            same_id = by_id.get(entry.project_id, ())
            peers = [*same_id]
            for known in (entry, *same_id):
                peers.extend(by_title.get((_name(known.recipient), _name(known.title)), ()))
            for duplicate in {peer.path: peer for peer in peers}.values():
                if duplicate.path == entry.path or duplicate.path in refreshed or duplicate.path in removed:
                    continue
                original = read(duplicate)
                if original is None or not canonical.matches(original):
                    continue
                if original.project_id != canonical.project_id and (duplicate.path == active_path or
                        (active_id and original.project_id == active_id)):
                    continue
                # Different-ID content matches must point to one project, even if
                # another plausible match would be ranked lower.
                if original.project_id != canonical.project_id and any(
                    other.path not in {entry.path, duplicate.path} and other.path not in removed
                    and (alternative := read(other)) is not None and alternative.project_id != canonical.project_id
                    and original.matches(alternative) and not canonical.matches(alternative)
                    for other in peers
                ):
                    continue
                project_id = canonical.project_id or project_uuid(canonical.stored.project.project_id) or str(uuid.uuid4())
                try:
                    current = load_letter(entry.path)
                    if current.revision and entry.path not in refreshed and current.revision != canonical.stored.revision:
                        continue
                    merge_duplicate(archive, duplicate.path, entry.path, original=original.stored,
                                    canonical=current, project_id=project_id)
                except (OSError, ValueError, ProjectRepositoryError):
                    _LOGGER.warning("Could not consolidate %s into %s", duplicate.path, entry.path, exc_info=True)
                    continue
                removed.add(duplicate.path)
                refreshed[entry.path] = catalog._entry(entry.path)
                latest = refreshed[entry.path]
                canonical = LetterEvidence.read(load_letter(entry.path), project_id=latest.project_id,
                    recipient_id=latest.recipient_id, recipient=latest.recipient, title=latest.title)
                evidence[entry.path] = canonical

    return [refreshed.get(entry.path, entry) for entry in entries if entry.path not in removed]
