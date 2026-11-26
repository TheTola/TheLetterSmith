"""Revision summaries and last verified GitHub state for the Saved List."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from project_paths import application_paths, _valid_uuid
from project_repository import ProjectRepository, ProjectRepositoryError, _json
from transactional_io import atomic_write_json


def _state_path(root, project_id, kind):
    identifier = _valid_uuid(project_id)
    if identifier and kind in {"live", "backup"}:
        return application_paths(root).publication_root / "saved-letters" / f"{identifier}.{kind}.json"
    return None


def letter_state(root, project_id, kind):
    path = _state_path(root, project_id, kind)
    try:
        return _json(path, optional=True) if path is not None else {}
    except ProjectRepositoryError:
        return {}


def write_letter_state(root, project_id, kind, state):
    path = _state_path(root, project_id, kind)
    if path is not None:
        atomic_write_json(path, state)


def effective_publication(root, project_id, publication):
    record = letter_state(root, project_id, "live")
    verified = record.get("publication")
    if not isinstance(verified, dict):
        return publication
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    newer = saved_date(publication.get("published_at"), oldest) > saved_date(record.get("recorded_at"), oldest)
    return publication if newer and record.get("recorded_at") else verified


def saved_date(value, fallback):
    try:
        date = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback
    return date if date.tzinfo is not None else date.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RevisionSummary:
    revision: str
    date: datetime
    changes: str
    live: bool = False
    backed_up: bool = False


def _sections(document, resources):
    pages = document["pages"]
    meta = document["metadata"]
    result = {
        "Title or recipient": (meta.get("title") or meta.get("recipient_title"), meta.get("recipient_title"), meta.get("recipient_name")),
        "Page order or layout": [(page["id"], page["type"], page["name"], page["design_overrides"],
                                  (page["message"] or {}).get("layout")) for page in pages],
        "Page artwork": [({key: value for key, value in page["artwork"].items() if key != "asset"},
                         resources.get(page["artwork"].get("asset"))) for page in pages],
        "Messages": [(page["message"]["html"], page["message"].get("settings"),
                      [resources.get(path) for path in page["message"].get("resources", []) if isinstance(path, str)])
                     for page in pages if page["message"] is not None],
        "Prompts": ([page["prompt"] for page in pages], document["prompt_settings"]),
        "Soundtrack": (document["sound"], [resources.get(track.get("asset")) for track in document["sound"].get("tracks", [])
                                            if isinstance(track, dict)]),
        "Presentation settings": (document["presentation"], document["shared_settings"]),
    }
    return result


def summarize_revisions(root, entry, versions, remote_backup=None):
    from publishing.github_library import project_digest
    from settings_store import PUBLISHED_SOURCE_FINGERPRINT_KEY

    backup = letter_state(root, entry.project_id, "backup")
    if remote_backup is not None and (not backup or saved_date(remote_backup.timestamp, datetime.min.replace(tzinfo=timezone.utc)) >=
                                       saved_date(backup.get("timestamp"), datetime.min.replace(tzinfo=timezone.utc))):
        backup = {"timestamp": remote_backup.timestamp, "revision_key": remote_backup.revision_key, "digest": remote_backup.digest}
    state = letter_state(root, entry.project_id, "live") if entry.publication_verified and entry.published else {}
    repository = ProjectRepository(entry.path / ".lettersmith-project")
    rows = []
    for stored in versions:
        data = stored.project.to_dict()
        manifest = _json(stored.asset_root / "resources.json")
        date = saved_date(data["metadata"].get("saved_at"), datetime.fromtimestamp((stored.asset_root / "project.json").stat().st_mtime, timezone.utc))
        key = repository.version_key(stored.revision, ignore_page_ids=True)
        backed_up = bool(backup.get("revision_key") == key or (
            not backup.get("revision_key") and backup.get("digest") and project_digest(stored) == backup["digest"]))
        live = bool(state.get("revision_key") == key and
                    state.get("publication", {}).get(PUBLISHED_SOURCE_FINGERPRINT_KEY) == entry.published_source_fingerprint)
        if not state.get("revision_key") and entry.publication_verified and entry.published and entry.published_source_fingerprint:
            from forge_render import capture_render
            from project_session import ProjectSession
            session = ProjectSession(stored.project, project_root=stored.asset_root, asset_root=stored.asset_root)
            try:
                live = capture_render(session, "display", presentation_root=Path(root)).source_fingerprint == entry.published_source_fingerprint
            except (OSError, ValueError, RuntimeError):
                live = False
            finally:
                session.close()
        rows.append((date, stored.revision, _sections(data, manifest["files"]), live, backed_up))
    summaries, previous = [], None
    for date, revision, sections, live, backed_up in sorted(rows, key=lambda item: item[0]):
        changes = [label for label in sections if previous is not None and sections[label] != previous[label]]
        text = "Initial save" if previous is None else " · ".join(changes) or "Other project details"
        summaries.append(RevisionSummary(revision, date, text, live, backed_up))
        previous = sections
    return tuple(reversed(summaries)), backup
