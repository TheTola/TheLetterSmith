"""Foundation-owned generation storage and read-only legacy workspace import.

A generation contains the document and every available referenced resource. The
only commit point is current.json; interrupted staging never changes the reader's
generation. Old generations remain available for recovery and page deletion undo.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import threading
import uuid

from transactional_io import atomic_write_bytes, atomic_write_json, is_link_or_reparse_point


class ProjectRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str, *, path: Path | None = None):
        self.code = code
        self.path = path
        super().__init__(message)


class ProjectConflictError(ProjectRepositoryError):
    pass


@dataclass(frozen=True)
class StoredProject:
    project: object
    revision: str
    asset_root: Path
    migration_report: dict


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD_LOCKS = threading.local()
_LOGGER = logging.getLogger(__name__)


def _relative(value: str) -> Path:
    text = str(value).replace("\\", "/")
    relative = PurePosixPath(text)
    if not text or relative.is_absolute() or ":" in text or ".." in relative.parts:
        raise ProjectRepositoryError("unsafe_path", "Project resources require safe relative paths.")
    return Path(*relative.parts)


def _safe_file(root: Path, relative: str) -> Path:
    path = root / _relative(relative)
    cursor = root
    if is_link_or_reparse_point(root):
        raise ProjectRepositoryError("unsafe_path", "Linked project roots are unsupported.", path=root)
    for part in path.relative_to(root).parts:
        cursor = cursor / part
        if is_link_or_reparse_point(cursor):
            raise ProjectRepositoryError("unsafe_path", "Linked project resources are unsupported.", path=cursor)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ProjectRepositoryError("unsafe_path", "A project resource escaped its root.", path=path)
    return path


def _json(path: Path, *, optional: bool = False) -> dict:
    if optional and not path.exists():
        return {}
    if is_link_or_reparse_point(path):
        raise ProjectRepositoryError("unsafe_path", "Project documents cannot be links.", path=path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, UnicodeError, ValueError) as error:
        raise ProjectRepositoryError("invalid_document", f"Cannot read project document: {path.name}", path=path) from error


@contextmanager
def _writer_lock(directory: Path):
    key = os.path.normcase(str(directory.resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_HELD_LOCKS, "directories", None)
        if held is None:
            held = _HELD_LOCKS.directories = set()
        if key in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".writer.lock"
        if is_link_or_reparse_point(lock_path):
            raise ProjectRepositoryError("unsafe_path", "Project lock cannot be a link.", path=lock_path)
        with lock_path.open("a+b") as stream:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ProjectConflictError("writer_busy", "Another writer is saving this project.", path=directory) from error
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _owned_pages(payload: dict) -> list[dict]:
    pages = list(payload["pages"])
    recovery = payload.get("recovery", {})
    pages.extend(item["page"] for item in recovery.get("deleted_pages", []) if isinstance(item, dict) and isinstance(item.get("page"), dict))
    pages.extend(item for item in recovery.get("deleted_legacy_pages", []) if isinstance(item, dict))
    return pages


def _resource_references(payload: dict) -> set[str]:
    references: set[str] = set()
    def resource(value):
        if isinstance(value, str) and value:
            references.add(value)
        elif isinstance(value, dict):
            for key in ("asset", "path", "source", "src"):
                if isinstance(value.get(key), str) and value[key]:
                    references.add(value[key])
        elif isinstance(value, list):
            for item in value:
                resource(item)
    for page in _owned_pages(payload):
        resource(page.get("artwork", {}).get("asset"))
        for message in (page.get("message"), page.get("recovery", {}).get("inactive_message")):
            if isinstance(message, dict):
                resource(message.get("resources", []))
    resource(payload.get("extensions", {}).get("resource_files", []))
    resource(payload.get("sound", {}).get("tracks", []))
    recovery = payload.get("recovery", {})
    messages = [recovery.get("unassigned_message")]
    messages.extend(recovery.get("unassigned_messages", {}).values())
    for message in messages:
        if isinstance(message, dict):
            resource(message.get("resources", []))
    return references


def _active_audio_sources(root: Path, *, state: dict | None = None) -> tuple[list[dict], dict[str, Path]]:
    """Resolve only this project's selected archive/stock tracks, read-only."""
    from project_paths import application_paths
    from sound_model import hash_file
    paths = application_paths(root)
    state = state if state is not None else _json(paths.project_sound_state_file, optional=True)
    previous = {item.get("track_id"): item for item in state.get("tracks", []) if isinstance(item, dict)}
    ids = list(state.get("playlist", [])) if state.get("mode") == "playlist" else [state.get("single_track_id")]
    ids = [item for item in ids if isinstance(item, str) and item]
    if not ids:
        return [], {}
    def optional_catalog(path: Path) -> dict:
        try:
            return _json(path, optional=True)
        except ProjectRepositoryError as error:
            if error.code != "invalid_document":
                raise
            _LOGGER.warning("Reusable sound catalog is unavailable; retaining selected track IDs as missing: %s", path)
            return {}

    library = optional_catalog(paths.music_archive_root / "library.json").get("tracks", {})
    stock = optional_catalog(paths.stock_root / "stock_manifest.json").get("music", [])
    records, sources = [], {}
    for track_id in ids:
        record = library.get(track_id) if isinstance(library, dict) else None
        source = None
        if isinstance(record, dict):
            if record.get("source_kind") == "stock" and record.get("resource_file"):
                source = _safe_file(paths.stock_root, record["resource_file"])
            elif record.get("processed_file"):
                source = _safe_file(paths.music_archive_root / "processed", record["processed_file"])
        if source is None:
            for candidate in stock if isinstance(stock, list) else []:
                if not isinstance(candidate, dict) or not candidate.get("filename"):
                    continue
                path = _safe_file(paths.stock_root, candidate["filename"])
                if path.is_file() and hash_file(path)[:24] == track_id.removeprefix("stock:"):
                    source, record = path, candidate
                    break
        if source is None or not source.is_file():
            records.append({**deepcopy(previous.get(track_id, {})), "track_id": track_id, "missing": True})
            continue
        digest = hash_file(source)
        relative = f"audio/{digest[:32]}{source.suffix.lower()}"
        sources[relative] = source
        records.append({"track_id": track_id, "asset": relative, "sha256": digest,
                        "display_title": record.get("display_title", record.get("title", source.stem)),
                        "original_name": record.get("original_name", source.name),
                        "duration_seconds": record.get("duration_seconds", 0)})
    return records, sources


class ProjectRepository:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).absolute()
        # Recipient/title names plus outer build/snapshot staging can exceed the
        # Win32 legacy limit even when every individual component is valid.
        if os.name == "nt" and not str(self.directory).startswith("\\\\?\\"):
            text = str(self.directory)
            self.directory = Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)
        self.pointer = self.directory / "current.json"

    @property
    def exists(self) -> bool:
        return self.pointer.exists()

    def _head(self) -> dict:
        if not self.pointer.exists():
            return {}
        head = _json(self.pointer)
        revision = head.get("revision", "")
        if head.get("format") != "lettersmith.project-head" or head.get("version") != 1:
            raise ProjectRepositoryError("unsupported_schema", "Unsupported project generation pointer.", path=self.pointer)
        if not isinstance(revision, str) or len(revision) != 16 or any(c not in "0123456789abcdef" for c in revision):
            raise ProjectRepositoryError("invalid_document", "Invalid project generation identity.", path=self.pointer)
        return head

    def load(self) -> StoredProject:
        from project_model import ProjectModel
        head = self._head()
        if not head:
            raise ProjectRepositoryError("not_found", "No committed project exists.", path=self.directory)
        generation = _safe_file(self.directory, f"g/{head['revision']}")
        payload = _json(generation / "project.json")
        try:
            project = ProjectModel.from_dict(payload)
        except (ValueError, TypeError, KeyError) as error:
            raise ProjectRepositoryError("invalid_project", str(error), path=generation / "project.json") from error
        manifest = _json(generation / "resources.json")
        if manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("missing", []), list):
            raise ProjectRepositoryError("invalid_document", "Unsupported project resource manifest.", path=generation)
        declared = set(manifest["files"]) | set(manifest.get("missing", []))
        if not _resource_references(payload).issubset(declared):
            raise ProjectRepositoryError("unlisted_resource", "The project references resources outside its committed manifest.", path=generation)
        if set(manifest.get("missing", [])) != set(payload.get("recovery", {}).get("missing_resources", [])):
            raise ProjectRepositoryError("invalid_document", "Missing resource records disagree with the project document.", path=generation)
        for relative, digest in manifest.get("files", {}).items():
            candidate = _safe_file(generation, relative)
            try:
                actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError as error:
                raise ProjectRepositoryError("missing_resource", f"Committed resource is missing: {relative}", path=candidate) from error
            if actual != digest:
                raise ProjectRepositoryError("resource_changed", f"Committed resource changed: {relative}", path=candidate)
        return StoredProject(project, head["revision"], generation, _json(generation / "migration.json", optional=True))

    def save(self, project, *, asset_root: str | Path,
             expected_revision: str | None = None,
             fallback_asset_root: str | Path | None = None,
             migration_report: dict | None = None) -> StoredProject:
        from project_model import ProjectModel
        payload = project.to_dict()
        ProjectModel.from_dict(payload)
        source_roots = [Path(asset_root).resolve()]
        if fallback_asset_root is not None:
            source_roots.append(Path(fallback_asset_root).resolve())
        sound = payload.get("sound", {})
        assigned = sound.get("playlist", []) if sound.get("mode") == "playlist" else [sound.get("single_track_id")]
        external_audio = {}
        if any(isinstance(track_id, str) and track_id for track_id in assigned):
            resolved, external_audio = _active_audio_sources(source_roots[0], state=sound)
            previous = {track.get("track_id"): track for track in sound.get("tracks", []) if isinstance(track, dict)}
            # Direct canonical assignment APIs may not have visited Sound yet.
            # Add portable metadata, retaining authored references even when
            # their assets are missing or differ from a cached archive alias.
            portable = []
            for track in resolved:
                before = previous.get(track["track_id"], {})
                if before.get("asset") or before.get("filename"):
                    portable.append(deepcopy(before))
                else:
                    item = {**deepcopy(before), **track}
                    if not track.get("missing"):
                        item.pop("missing", None)
                    portable.append(item)
            resolved_ids = {track["track_id"] for track in resolved}
            portable.extend(deepcopy(track) for track in sound.get("tracks", [])
                            if isinstance(track, dict) and track.get("track_id") not in resolved_ids)
            sound["tracks"] = portable
        with _writer_lock(self.directory):
            head = self._head()
            if (head.get("revision") or None) != expected_revision:
                raise ProjectConflictError("revision_conflict", "Project changed on disk; reload before saving.", path=self.pointer)
            revision = uuid.uuid4().hex[:16]
            generation = self.directory / "g" / revision
            staging = self.directory / "g" / (".s-" + revision)
            staging.mkdir(parents=True)
            report = deepcopy(migration_report)
            if report is None:
                report = _json(self.directory / "g" / head["revision"] / "migration.json", optional=True) if head else {}
            if report:
                report.setdefault("locations", {})["destination"] = str(self.directory)
            files = {}
            missing = []
            owned_pages = _owned_pages(payload)
            artwork_references = {page.get("artwork", {}).get("asset") for page in owned_pages}
            for relative in sorted(_resource_references(payload)):
                candidates = [_safe_file(root, relative) for root in source_roots]
                if relative in external_audio:
                    candidates.append(external_audio[relative])
                source = next((item for item in candidates if item.is_file()), None)
                if source is None:
                    missing.append(relative)
                    continue
                try:
                    before = source.stat()
                    value = source.read_bytes()
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ProjectConflictError("resource_changed", "A project resource changed while saving.", path=source)
                    digest = hashlib.sha256(value).hexdigest()
                    stored_relative = relative
                    if relative in artwork_references:
                        stored_relative = f"a/{digest[:32]}{source.suffix.lower()}"
                        for page in owned_pages:
                            if page.get("artwork", {}).get("asset") == relative:
                                page["artwork"]["asset"] = stored_relative
                    destinations = {stored_relative}
                    # Rich HTML and other resource owners retain their exact
                    # paths even when the same bytes are also page artwork.
                    if relative in _resource_references(payload):
                        destinations.add(relative)
                    for destination_relative in destinations:
                        if destination_relative in files and files[destination_relative] != digest:
                            raise ProjectConflictError("asset_collision", "Resource names collided; no project was committed.")
                        destination = _safe_file(staging, destination_relative)
                        atomic_write_bytes(destination, value)
                        files[destination_relative] = digest
                except OSError as error:
                    raise ProjectRepositoryError("resource_write_failed", "Cannot stage project resource.", path=source) from error
            if missing:
                payload["recovery"]["missing_resources"] = missing
            else:
                payload["recovery"].pop("missing_resources", None)
            atomic_write_json(staging / "project.json", payload)
            atomic_write_json(staging / "resources.json", {"version": 1, "files": files, "missing": missing})
            atomic_write_json(staging / "migration.json", report)
            # Reparse serialized bytes before the one visible commit operation.
            ProjectModel.from_dict(_json(staging / "project.json"))
            os.replace(staging, generation)
            atomic_write_json(self.pointer, {"format": "lettersmith.project-head", "version": 1, "revision": revision})
            return StoredProject(ProjectModel.from_dict(payload), revision, generation, report)


def active_project_repository(project_root: str | Path) -> ProjectRepository:
    return ProjectRepository(Path(project_root) / ".lettersmith-project")


def activation_lock(project_root: str | Path):
    """Serialize multi-file compatibility activation independently of commits."""
    return _writer_lock(active_project_repository(project_root).directory / ".activation")


def begin_activation(project_root: str | Path, *, expected_revision: str | None = None) -> Path:
    repository = active_project_repository(project_root)
    head = repository._head()
    if not head:
        raise ProjectRepositoryError("incomplete_activation", "Commit an initial project before changing its editor views.")
    if expected_revision is not None and expected_revision != head["revision"]:
        raise ProjectConflictError("revision_conflict", "Project changed before activation; reload before continuing.")
    marker = repository.directory / "activation.json"
    atomic_write_json(marker, {"version": 1, "previous_revision": head["revision"]})
    return marker


def activation_pending(project_root: str | Path) -> bool:
    marker = active_project_repository(project_root).directory / "activation.json"
    if not marker.exists():
        return False
    if _json(marker).get("version") != 1:
        raise ProjectRepositoryError("unsupported_schema", "Unsupported interrupted activation journal.", path=marker)
    return True


def finish_activation(project_root: str | Path) -> None:
    marker = active_project_repository(project_root).directory / "activation.json"
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        # Replaying this completed generation on next open is safe. Cleanup is
        # never allowed to roll back a successfully committed pointer.
        _LOGGER.warning("Completed activation journal could not be removed: %s", marker, exc_info=True)


def legacy_workspace_payload(project_root: str | Path, *, saved: bool = False) -> dict:
    from config import MESSAGE_ASSETS_DIR, PLAY_METADATA_FILE, USER_MESSAGE_DIR, USER_PAGES_DIR
    from page_layout import LAYOUT_FILE
    from settings_store import SettingsStore
    from sound_model import project_sound_path
    root = Path(project_root).resolve()
    pages = root / ("gallery/pages" if saved else USER_PAGES_DIR)
    message = root / ("gallery/message" if saved else USER_MESSAGE_DIR) / "message.html"
    metadata = _json(root / PLAY_METADATA_FILE, optional=True) if saved else {}
    layout = _json(pages / LAYOUT_FILE, optional=True)
    if layout and layout.get("version") != 1:
        raise ProjectRepositoryError("unsupported_schema", "Unsupported legacy page layout.", path=pages / LAYOUT_FILE)
    images = _json(pages / "lettersmith-images.json", optional=True)
    if images and images.get("schema_version") != 1:
        raise ProjectRepositoryError("unsupported_schema", "Unsupported legacy image manifest.", path=pages)
    slots = images.setdefault("slots", {})
    if not isinstance(slots, dict):
        raise ProjectRepositoryError("invalid_document", "Legacy image slots must be an object.", path=pages)
    for key in ("cover", "letter", "wall", "back"):
        if key not in slots:
            source = next((pages / (key + suffix) for suffix in (".gif", ".png") if (pages / (key + suffix)).is_file()), None)
            if source is not None:
                slots[key] = {"source_file": source.name, "asset_type": "animated_gif" if source.suffix == ".gif" else "static"}
    settings = metadata.get("settings", {}) if saved else SettingsStore(root).snapshot()
    if not saved:
        from project_state import PROJECT_SCHEMA_KEY
        if settings.get(PROJECT_SCHEMA_KEY, 2) not in (0, 1, 2, None):
            raise ProjectRepositoryError("unsupported_schema", "Unsupported active project identity schema.")
    # Application preferences/credentials are never part of a project package.
    from saved_letters import RESTORABLE_SETTING_KEYS, PUBLICATION_METADATA_KEYS
    settings = {key: deepcopy(value) for key, value in settings.items() if key in (*RESTORABLE_SETTING_KEYS, *PUBLICATION_METADATA_KEYS) or key in {"project_id", "recipient_id", "recipient_name", "recipient_title", "project_created_at"}}
    if not saved:
        metadata = {key: deepcopy(settings[key]) for key in ("project_id", "recipient_id", "recipient_name", "recipient_title", "project_created_at") if key in settings}
    if "settings" in metadata:
        metadata["settings"] = deepcopy(settings)
    prompt = _json(root / "prompt_writer_state.json", optional=True)
    if saved and isinstance(metadata.get("prompt_writer"), dict):
        prompt = metadata["prompt_writer"].get("state", prompt)
    sound_path = root / "gallery/sounds/lettersmith-sound.json" if saved else project_sound_path(root)
    sound = _json(sound_path, optional=True)
    if not saved:
        sound["tracks"] = _active_audio_sources(root)[0]
    resources = []
    assets = root / MESSAGE_ASSETS_DIR
    if assets.is_dir():
        for item in assets.rglob("*"):
            if item.is_file():
                _safe_file(root, item.relative_to(root).as_posix())
                resources.append(item.relative_to(root).as_posix())
    if message.parent.is_dir():
        for item in message.parent.rglob("*"):
            if item.is_file() and item != message and not any(part.startswith(".") for part in item.relative_to(message.parent).parts):
                _safe_file(root, item.relative_to(root).as_posix())
                resources.append(item.relative_to(root).as_posix())
    for track in sound.get("tracks", []):
        if isinstance(track, dict) and track.get("filename"):
            resources.append("gallery/sounds/" + str(track["filename"]))
        elif isinstance(track, dict) and track.get("asset"):
            resources.append(track["asset"])
    signatures = {}
    for key, record in images.get("slots", {}).items():
        if isinstance(record, dict) and record.get("source_file"):
            source = _safe_file(pages, record["source_file"])
            if source.is_file():
                signatures[key] = hashlib.sha256(source.read_bytes()).hexdigest()
    return {"metadata": metadata, "settings": settings,
            "page_layout": layout.get("pages", metadata.get("page_layout")),
            "page_layout_version": layout.get("version", 1),
            "images": images.get("slots", {}), "asset_prefix": pages.relative_to(root).as_posix(),
            "message_html": message.read_text(encoding="utf-8") if message.is_file() else None,
            "message_resources": [item for item in resources if item.startswith((str(MESSAGE_ASSETS_DIR).replace("\\", "/") + "/", message.parent.relative_to(root).as_posix() + "/"))],
            "resources": resources, "asset_signatures": signatures,
            "prompt_writer_state": prompt, "sound": sound}


def read_legacy_project(project_root: str | Path, *, saved: bool = False):
    from project_migration import migrate_project
    payload = legacy_workspace_payload(project_root, saved=saved)
    result = migrate_project(payload, source_format="legacy_saved" if saved else "legacy_active")
    result.report.locations["source"] = str(Path(project_root).resolve())
    document = result.project.to_dict()
    document.setdefault("extensions", {})["legacy_workspace_snapshot"] = deepcopy(payload)
    document["extensions"]["resource_files"] = list(payload["resources"])
    from project_model import ProjectModel
    from dataclasses import replace
    return replace(result, project=ProjectModel.from_dict(document))


def sync_legacy_workspace(project, project_root: str | Path):
    """Merge changed compatibility fields; untouched views never erase API edits."""
    from project_migration import migrate_project
    from project_model import ProjectModel, empty_message
    current = legacy_workspace_payload(project_root)
    document = project.to_dict()
    extensions = document.setdefault("extensions", {})
    previous = extensions.get("legacy_workspace_snapshot")
    if not isinstance(previous, dict):
        extensions["legacy_workspace_snapshot"] = current
        return ProjectModel.from_dict(document)
    if current == previous:
        return project
    imported = migrate_project(current, source_format="legacy_active").project.to_dict()
    imported_pages = {page.get("recovery", {}).get("legacy_key"): page for page in imported["pages"]}
    old_pages = {page.get("recovery", {}).get("legacy_key"): page for page in document["pages"]}
    structure_changed = current.get("page_layout") != previous.get("page_layout")
    if current.get("page_layout") is None and previous.get("page_layout") is None:
        structure_changed = set(current.get("images", {})) != set(previous.get("images", {}))
    previous_types = {item["key"]: item.get("kind") for item in previous.get("page_layout") or [] if isinstance(item, dict) and item.get("key")}
    if structure_changed:
        # A legacy layout edit owns only pages represented by that layout.
        ordered = []
        for incoming in imported["pages"]:
            key = incoming.get("recovery", {}).get("legacy_key")
            page = old_pages.get(key)
            if page is None:
                page = incoming
                if page.get("message") is not None:
                    page["message"] = empty_message()
            elif (page["type"] != incoming["type"]
                  and previous_types.get(key, page.get("recovery", {}).get("legacy_type")) != incoming["recovery"].get("legacy_type")):
                if page.get("message") is not None and incoming.get("message") is None:
                    page.setdefault("recovery", {})["inactive_message"] = page["message"]
                    page["message"] = None
                elif incoming.get("message") is not None and page.get("message") is None:
                    page["message"] = deepcopy(page.get("recovery", {}).pop("inactive_message", empty_message()))
                page["type"] = incoming["type"]
                page["prompt"]["template_id"] = incoming["prompt"]["template_id"]
                page["prompt"]["stale"] = True
                page["preview_stale"] = True
            ordered.append(page)
        removed = [page for key, page in old_pages.items() if key and key not in imported_pages]
        if removed:
            document.setdefault("recovery", {}).setdefault("deleted_legacy_pages", []).extend(deepcopy(removed))
        ordered.extend(page for page in document["pages"] if not page.get("recovery", {}).get("legacy_key"))
        document["pages"] = ordered
    prompt_changed = current.get("prompt_writer_state") != previous.get("prompt_writer_state")
    message_changed = current.get("message_html") != previous.get("message_html")
    before_imported = migrate_project(previous, source_format="legacy_active").project.to_dict()
    for page in document["pages"]:
        key = page.get("recovery", {}).get("legacy_key")
        incoming = imported_pages.get(key)
        if incoming is None:
            continue
        before_page = next((item for item in before_imported["pages"] if item.get("recovery", {}).get("legacy_key") == key), None)
        if (current.get("images", {}).get(key) != previous.get("images", {}).get(key)
                or current.get("asset_signatures", {}).get(key) != previous.get("asset_signatures", {}).get(key)):
            for field, value in incoming["artwork"].items():
                if (before_page is None or before_page["artwork"].get(field) != value
                        or (field == "asset" and current.get("asset_signatures", {}).get(key) != previous.get("asset_signatures", {}).get(key))):
                    page["artwork"][field] = deepcopy(value)
            page["preview_stale"] = True
        if prompt_changed:
            # The legacy writer persists a whole envelope. Resolve page changes
            # through the converter to avoid overwriting other canonical pages.
            for field, value in incoming["prompt"].items():
                if before_page is None or before_page["prompt"].get(field) != value:
                    page["prompt"][field] = deepcopy(value)
        if message_changed and key in old_pages and page.get("message") is not None and incoming.get("message") is not None:
            for field, value in incoming["message"].items():
                if before_page is None or (before_page.get("message") or {}).get(field) != value:
                    page["message"][field] = deepcopy(value)
            page["preview_stale"] = True
    if prompt_changed:
        for field in before_imported["prompt_settings"].keys() | imported["prompt_settings"].keys():
            if before_imported["prompt_settings"].get(field) != imported["prompt_settings"].get(field):
                if field in imported["prompt_settings"]:
                    document["prompt_settings"][field] = deepcopy(imported["prompt_settings"][field])
                else:
                    document["prompt_settings"].pop(field, None)
    for legacy_key, model_key in (("settings", "shared_settings"), ("sound", "sound")):
        if current.get(legacy_key) != previous.get(legacy_key):
            if model_key == "shared_settings":
                for key, value in imported.get(model_key, {}).items():
                    if current.get(legacy_key, {}).get(key) != previous.get(legacy_key, {}).get(key):
                        document.setdefault(model_key, {})[key] = deepcopy(value)
            else:
                document[model_key] = deepcopy(imported[model_key])
            if "legacy_authored_snapshot" in extensions:
                extensions["legacy_authored_snapshot"][model_key] = deepcopy(document[model_key])
    configured_id = current.get("settings", {}).get("project_id")
    if configured_id and configured_id != previous.get("settings", {}).get("project_id"):
        document["project_id"] = configured_id
    for field in ("metadata", "publication"):
        for key, value in imported[field].items():
            if current.get("settings", {}).get(key) != previous.get("settings", {}).get(key):
                document[field][key] = deepcopy(value)
    extensions["legacy_workspace_snapshot"] = deepcopy(current)
    extensions["resource_files"] = list(dict.fromkeys(extensions.get("resource_files", []) + current["resources"]))
    document["revision"] = document.get("revision", 0) + 1
    return ProjectModel.from_dict(document)
