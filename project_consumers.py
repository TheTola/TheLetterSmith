"""Narrow adapters for the existing slot-based editors and single-message viewer.

The canonical model owns content. These disposable views never reinterpret
canonical type IDs and never select a message by a guessed page position.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from page_layout import LetterPage, PAGE_KEYS, MIN_PAGES, MAX_PAGES, save_pages
from project_model import PAGE_TYPES
from transactional_io import PathTransaction, atomic_copy_file, atomic_write_bytes, atomic_write_json, atomic_write_text


class ConsumerCompatibilityError(ValueError):
    pass


_AUDIO_SETTING_KEYS = ("music_volume", "starting_volume", "music_muted")


def _sound_projection_tokens(paths) -> tuple:
    from transactional_io import file_change_token
    result = []
    for path in paths:
        try:
            value = path.stat()
            token = (value.st_size, value.st_mtime_ns, file_change_token(path, stat_result=value))
        except OSError:
            token = None
        result.append((str(path), token))
    return tuple(result)


def project_sound_snapshot(session) -> dict:
    """Read the canonical assignment, including absent resources and audio levels."""
    from config import STARTING_VOLUME
    from sound_model import ProjectSoundState
    data = session.project.to_dict()
    sound = deepcopy(data["sound"])
    tracks = [track for track in sound.get("tracks", []) if isinstance(track, dict)]
    ids = [str(track.get("track_id") or track.get("sha256", "")[:24]
               or track.get("content_hash", "")[:24] or track.get("filename", "")) for track in tracks]
    if "playlist" not in sound and "single_track_id" not in sound:
        if sound.get("mode") == "playlist":
            sound["playlist"] = [track_id for track_id in ids if track_id]
        else:
            sound["single_track_id"] = next((track_id for track_id in ids if track_id), "")
        selected = sound.get("selected_track_index", 0)
        if isinstance(selected, int) and not isinstance(selected, bool) and 0 <= selected < len(ids):
            sound["selected_track_id"] = ids[selected]
    volume = sound.get("volume")
    if volume is None:
        settings = data["shared_settings"]
        volume = settings.get("music_volume", settings.get("starting_volume", STARTING_VOLUME))
    try:
        volume = max(0, min(100, int(volume)))
    except (TypeError, ValueError, OverflowError):
        volume = max(0, min(100, int(STARTING_VOLUME)))
    sound.update(ProjectSoundState.from_dict(sound).normalize().to_dict())
    sound["volume"] = volume
    sound["muted"] = data["sound"].get("muted", False) is True
    sound["tracks"] = [{**track, "track_id": track_id} for track, track_id in zip(tracks, ids)]
    return sound


def _project_sound_views(session, sound: dict, *, update_sound: bool = False) -> None:
    """Project only audio views; callers hold the session and activation locks."""
    from dataclasses import replace
    from saved_letters import _FileSnapshot, _SoundArchiveSnapshot
    from settings_store import SettingsStore
    from sound_model import (ProjectSoundState, current_manifest_path, current_music_path,
                             hash_file, import_runtime_track, library_path, load_library, project_sound_path,
                             resolve_track_path, save_library, save_project_state,
                             sync_current_compatibility)
    from project_repository import _active_audio_sources
    root = session.project_root
    if root is None:
        raise ConsumerCompatibilityError("The project has no editor workspace.")
    settings = SettingsStore(root)
    signature = (session.session_id, repr({key: value for key, value in sound.items() if key not in {"volume", "muted"}}))
    cached_paths = getattr(session, "_sound_projection_paths", ())
    levels_only = (signature == getattr(session, "_sound_projection_signature", None)
                   and _sound_projection_tokens(cached_paths) == getattr(session, "_sound_projection_tokens", None))
    paths = [project_sound_path(root), settings.path]
    if not levels_only:
        paths.extend((current_music_path(root), current_manifest_path(root)))
    snapshots = [_FileSnapshot.capture(path) for path in paths]
    archive_snapshot = None if levels_only else _SoundArchiveSnapshot.capture(root)
    records = {} if levels_only else load_library(root)
    original_records = {key: record.to_dict() for key, record in records.items()}
    state = ProjectSoundState.from_dict(sound).normalize()
    used = set(state.ordered_track_ids())
    try:
        for track in (() if levels_only else sound.get("tracks", [])):
            track_id = track.get("track_id")
            if track_id not in used:
                continue
            current = records.get(track_id)
            reference = track.get("asset") or ("gallery/sounds/" + track["filename"] if track.get("filename") else None)
            source = None
            try:
                source = _asset(session, reference) if reference else None
            except FileNotFoundError:
                pass
            expected_hash = hash_file(source) if source is not None else track.get("sha256", track.get("content_hash"))
            if current is not None and resolve_track_path(root, current).is_file():
                current_hash = hash_file(resolve_track_path(root, current))
                if current_hash == expected_hash or (not expected_hash and (not reference or track_id == current_hash[:24])):
                    continue
                # The same authored ID may belong to another loaded project's
                # portable resource. Keep its archive bytes under their own ID.
                original_id = current_hash[:24]
                records.setdefault(original_id, replace(current, track_id=original_id, content_hash=current_hash))
                records.pop(track_id, None)
            if source is None:
                continue
            reusable = dict(records)
            for record_id, candidate in records.items():
                if candidate.content_hash != expected_hash:
                    continue
                candidate_path = resolve_track_path(root, candidate)
                if candidate_path.is_file():
                    actual_hash = hash_file(candidate_path)
                    if actual_hash != expected_hash:
                        # Aliases may share one modified processed file. Keep
                        # its occupied ID and bytes, but never reuse its stale
                        # hash; the importer will allocate a fresh filename.
                        reusable[record_id] = replace(candidate, content_hash=actual_hash)
                elif candidate.source_kind == "stock":
                    # Portable recovery belongs in the writable archive, not
                    # in a missing bundled resource's installation directory.
                    reusable.pop(record_id)
            record = import_runtime_track(root, source, display_title=track.get("display_title", ""),
                                          original_name=track.get("original_name", source.name),
                                          duration_seconds=track.get("duration_seconds", 0),
                                          records=reusable, persist=False)
            records.update(reusable)
            if record.track_id != track_id:
                records[track_id] = replace(record, track_id=track_id)
        if original_records != {key: record.to_dict() for key, record in records.items()}:
            save_library(root, records)
        save_project_state(root, state)
        if not levels_only:
            sync_current_compatibility(root, state, records)
        mirrors = {"music_volume": sound["volume"], "starting_volume": sound["volume"], "music_muted": sound["muted"]}
        settings.update_fields(mirrors)
        # Record exactly the disposable fields just written. Other adapters'
        # snapshots and canonical shared settings must remain untouched.
        data = session.project.to_dict()
        extensions = deepcopy(data["extensions"])
        previous = extensions.setdefault("legacy_workspace_snapshot", {})
        previous["sound"] = {**state.to_dict(), "tracks": (
            previous.get("sound", {}).get("tracks", []) if levels_only else _active_audio_sources(root)[0])}
        previous.setdefault("settings", {}).update(mirrors)
        authored = extensions.setdefault("legacy_authored_snapshot", {})
        authored["sound"] = deepcopy(sound if update_sound else data["sound"])
        authored_settings = authored.setdefault("shared_settings", deepcopy(data["shared_settings"]))
        for key in _AUDIO_SETTING_KEYS:
            if key in data["shared_settings"]:
                authored_settings[key] = deepcopy(data["shared_settings"][key])
            else:
                authored_settings.pop(key, None)
        if update_sound:
            session.project.update_project_data("sound", sound)
        session.project.update_project_data("extensions", extensions)
    except Exception:
        for snapshot in snapshots:
            snapshot.restore()
        if archive_snapshot is not None:
            archive_snapshot.restore()
        raise
    if not levels_only:
        watched = [library_path(root), project_sound_path(root), current_music_path(root), current_manifest_path(root)]
        watched.extend(resolve_track_path(root, records[track_id]) for track_id in used if track_id in records)
        for track in sound.get("tracks", []):
            reference = track.get("asset") or ("gallery/sounds/" + track["filename"] if track.get("filename") else None)
            if reference and track.get("track_id") in used:
                for base in (session.asset_root, root):
                    if base is not None:
                        candidate = (Path(base) / reference).resolve()
                        if candidate.is_relative_to(Path(base).resolve()):
                            watched.append(candidate)
        session._sound_projection_paths = tuple(dict.fromkeys(watched))
    session._sound_projection_signature = signature
    session._sound_projection_tokens = _sound_projection_tokens(session._sound_projection_paths)


def project_sound_views(session) -> None:
    """Refresh legacy playback files without saving/replacing the project."""
    from project_repository import activation_lock
    if session.project_root is None:
        raise ConsumerCompatibilityError("The project has no editor workspace.")
    with session._lock, activation_lock(session.project_root), session.project.mutation_scope():
        if session._closed:
            raise RuntimeError("The project session is closed.")
        _project_sound_views(session, project_sound_snapshot(session))


def update_project_sound(session, patch: dict) -> dict:
    """Commit a narrow canonical sound edit and its rollback-safe playback views."""
    from project_repository import activation_lock, activation_pending, ProjectRepositoryError, _active_audio_sources
    from sound_model import ProjectSoundState
    if session.project_root is None:
        raise ConsumerCompatibilityError("The project has no editor workspace.")
    with session._lock, activation_lock(session.project_root), session.project.mutation_scope():
        if session._closed:
            raise RuntimeError("The project session is closed.")
        if session.is_restoring:
            raise RuntimeError("Cannot edit sound while a project restoration is in progress.")
        if activation_pending(session.project_root):
            raise ProjectRepositoryError("incomplete_activation", "Recover the interrupted project activation before editing sound.")
        before = project_sound_snapshot(session)
        sound = {**before, **deepcopy(patch)}
        sound.update(ProjectSoundState.from_dict(sound).normalize().to_dict())
        old_ids = ProjectSoundState.from_dict(before).ordered_track_ids()
        new_ids = ProjectSoundState.from_dict(sound).ordered_track_ids()
        if new_ids != old_ids or not sound.get("tracks"):
            previous = {track.get("track_id"): track for track in sound.get("tracks", [])}
            resolved, _sources = _active_audio_sources(session.project_root, state=sound)
            sound["tracks"] = [{**previous.get(track["track_id"], {}), **track} for track in resolved]
            for track, resource in zip(sound["tracks"], resolved):
                if not resource.get("missing"):
                    track.pop("missing", None)
        _project_sound_views(session, sound, update_sound=True)
        return deepcopy(sound)


def session_for(root):
    from project_session import peek_project_session
    return peek_project_session(root)


def consumer_issue(root, consumer: str = "output") -> str:
    session = session_for(root)
    if session is None:
        return ""
    pages = session.project.pages
    if not MIN_PAGES <= len(pages) <= MAX_PAGES:
        return "This draft is preserved. The current editors require 2–10 pages."
    if consumer in {"images", "prompts", "layout"}:
        return ""
    if consumer == "prompt_generation":
        if any(page["prompt"].get("overrides") or set(page["prompt"].get("options", {})) - {"approved_image"}
               or (page["message"] or {}).get("layout") for page in pages):
            return "This project has page-specific prompt options or text areas. Its saved prompts are preserved; generation requires the page-aware Prompt Writer update."
        return ""
    messages = [page["message"] for page in pages if PAGE_TYPES[page["type"]].supports_message]
    if consumer == "message" and len(messages) != 1:
        return ("The current Messages editor needs exactly one Letter page. "
                "Independent page messages are preserved; use the shared page APIs until the page selector is available.")
    if consumer in {"output", "message"}:
        data = session.project.to_dict()
        previous = data["extensions"].get("legacy_workspace_snapshot", {})
        authored = data["extensions"].get("legacy_authored_snapshot", {})
        previous_settings = authored.get("shared_settings", previous.get("settings", {}))
        if any(value != previous_settings.get(key)
               for key, value in data["shared_settings"].items()):
            return "Shared project settings changed through the page APIs. Reload the saved project before using the current viewer."
        if consumer == "output" and {"tracks": [], **data["sound"]} != {"tracks": [], **authored.get("sound", previous.get("sound", {}))}:
            return "The project sound assignment changed through the page APIs. Reload the saved project before preview or export."
        previous_presentation = authored.get("presentation", previous.get("presentation", previous.get("metadata", {}).get("presentation", {})))
        if data["presentation"] and data["presentation"] != previous_presentation:
            return "This project has presentation settings that the current viewer cannot represent. The project is preserved."
        if session.project.to_dict()["recovery"].get("unresolved_associations"):
            return "Resolve the preserved legacy content associations before preview or export."
        if consumer == "output" and messages and any(document != messages[0] for document in messages[1:]):
            return "This project has independent Letter messages. Preview and export require the page-aware Forge update."
        if any(page["design_overrides"] or page["artwork"].get("placement") for page in pages):
            return "This project has page-specific design settings that the current viewer cannot render. The project is preserved."
        if any(document.get("layout") or document.get("settings") for document in messages):
            return "This project has page-specific message layout settings that the current viewer cannot render."
    return ""


def require_consumer(root, consumer: str = "output") -> None:
    issue = consumer_issue(root, consumer)
    if issue:
        raise ConsumerCompatibilityError(issue)


def page_id_for_key(root, key: str) -> str | None:
    session = session_for(root)
    if session is None:
        return None
    return next((page["id"] for page in session.project.pages
                 if page["recovery"].get("legacy_key") == key), None)


def _asset(session, reference: str) -> Path:
    if reference in session.project.to_dict()["recovery"].get("missing_resources", []):
        raise FileNotFoundError(f"Project resource is missing: {reference}")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or ":" in reference:
        raise ConsumerCompatibilityError("A project resource has an unsafe path.")
    for base in (session.asset_root, session.project_root):
        if base is None:
            continue
        root = Path(base).resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ConsumerCompatibilityError("A project resource escapes its storage directory.")
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Project resource is missing: {reference}")


def _unchanged_prompt_state(data, pages):
    """Keep the exact legacy envelope when its authored prompt values agree."""
    previous = data["extensions"].get("legacy_workspace_snapshot")
    if not isinstance(previous, dict):
        return None
    from project_migration import migrate_project
    before = migrate_project(previous, source_format="legacy_active").project.to_dict()
    if before["prompt_settings"] != data["prompt_settings"]:
        return None
    previous_pages = {page["recovery"].get("legacy_key"): page for page in before["pages"]}
    if set(previous_pages) != {page["recovery"]["legacy_key"] for page in pages}:
        return None
    fields = ("text", "directions", "resolved_components", "stale")
    for page in pages:
        old_prompt = previous_pages[page["recovery"]["legacy_key"]]["prompt"]
        if any(page["prompt"].get(field) != old_prompt.get(field) for field in fields):
            return None
        if page["prompt"]["options"].get("approved_image", False) != old_prompt["options"].get("approved_image", False):
            return None
    return deepcopy(previous.get("prompt_writer_state", {}))


def project_legacy_views(session) -> None:
    """Materialize supported editor views; leave canonical content authoritative.

    Source artwork is copied byte-for-byte. PNG posters for GIFs are disposable
    previews. The original animation and its independent settings stay intact.
    Unsupported page counts are kept intact and reported before any write.
    """
    root = session.project_root
    if root is None:
        raise ConsumerCompatibilityError("The project has no editor workspace.")
    pages = session.project.pages
    if not MIN_PAGES <= len(pages) <= MAX_PAGES:
        raise ConsumerCompatibilityError("This draft is preserved. The current editors require 2–10 pages.")
    used = set()
    for page in pages:
        key = page["recovery"].get("legacy_key")
        if key not in PAGE_KEYS or key in used:
            key = next(candidate for candidate in PAGE_KEYS if candidate not in used
                       and all(other["recovery"].get("legacy_key") != candidate for other in pages))
            session.project.update_page_recovery(page["id"], {"legacy_key": key})
            page["recovery"]["legacy_key"] = key
        used.add(key)
    data = session.project.to_dict()
    initiating_session = session.session_id
    initiating_revision = session.project.revision
    def check_current():
        if session.session_id != initiating_session or session.project.revision != initiating_revision:
            raise ConsumerCompatibilityError("The project changed while its editor views were being prepared. Try again.")
    signature = repr((data["pages"], data["prompt_settings"], data["shared_settings"]))
    if getattr(session, "_legacy_projection_signature", None) == signature:
        return
    media_signature = repr([(page["id"], page["recovery"]["legacy_key"], page["type"], page["artwork"]) for page in pages])
    media_changed = getattr(session, "_legacy_projection_media_signature", None) != media_signature
    directory = root / "gallery/user/pages"
    view_paths = (root / "gallery/user/message/message.html", root / "prompt_writer_state.json")
    original_views = {path: path.read_bytes() if path.exists() else None for path in view_paths}
    transaction = None
    try:
        if media_changed:
            transaction = PathTransaction(directory, staging_suffix=".projection", backup_suffix=".projection-backup", unique_staging=True)
            staging = transaction.prepare()
            if directory.is_dir():
                shutil.copytree(directory, staging)
            else:
                staging.mkdir(parents=True)
            slots = {}
            layout = []
            for page in pages:
                key = page["recovery"]["legacy_key"]
                layout.append(LetterPage(key, PAGE_TYPES[page["type"]].artwork_template))
                artwork = page["artwork"]
                reference = artwork.get("asset")
                if not reference:
                    for suffix in (".png", ".gif", ".thumbnail.gif"):
                        (staging / (key + suffix)).unlink(missing_ok=True)
                    continue
                try:
                    source = _asset(session, reference)
                except FileNotFoundError:
                    for suffix in (".png", ".gif", ".thumbnail.gif"):
                        (staging / (key + suffix)).unlink(missing_ok=True)
                    continue
                record = deepcopy(page["recovery"].get("legacy_image_record", {}))
                animated = artwork.get("media_type") == "image/gif"
                destination = staging / (key + (".gif" if animated else ".png"))
                atomic_copy_file(source, destination)
                if animated:
                    from PIL import Image
                    with Image.open(source) as image:
                        image.seek(0)
                        image.convert("RGBA").save(staging / (key + ".png"))
                        frame_count = getattr(image, "n_frames", 1)
                    stat = destination.stat()
                    record.update({"asset_type": "animated_gif", "is_animated_gif": True,
                                   "source_file": destination.name, "preview_file": key + ".png",
                                   "settings": deepcopy(artwork.get("animation", {})),
                                   "gif": {**record.get("gif", {}), "frame_count": frame_count},
                                   "source_fingerprint": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}})
                else:
                    (staging / (key + ".gif")).unlink(missing_ok=True)
                    record.update({"asset_type": "static", "is_animated_gif": False,
                                   "source_file": destination.name, "preview_file": destination.name})
                slots[key] = record
            save_pages(staging, tuple(layout))
            atomic_write_json(staging / "lettersmith-images.json", {"schema_version": 1, "slots": slots})
            check_current()
            transaction.commit(keep_backup=True)
        messages = [page["message"] for page in pages if page["message"] is not None]
        if not messages or all(document == messages[0] for document in messages):
            html = messages[0]["html"] if messages else ""
            message_path = root / "gallery/user/message/message.html"
            if not message_path.exists() or message_path.read_bytes() != html.encode("utf-8"):
                atomic_write_text(message_path, html)
        prompt_state = deepcopy(data["prompt_settings"])
        prompt_state["version"] = 6
        prompt_state["generated_prompts"] = {}
        resolved = [page["prompt"]["resolved_components"] for page in pages]
        prompt_state["resolved_instructions"] = resolved[0] if resolved and all(item == resolved[0] for item in resolved) else {}
        prompt_state["generated_output_stale"] = any(page["prompt"].get("stale") for page in pages)
        for page in pages:
            key = page["recovery"]["legacy_key"]
            prompt_state[key] = page["prompt"]["directions"]
            if page["prompt"].get("text") is not None:
                prompt_state["generated_prompts"][key] = page["prompt"]["text"]
        unchanged_prompt_state = _unchanged_prompt_state(data, pages)
        if unchanged_prompt_state is not None:
            prompt_state = unchanged_prompt_state
        import json
        prompt_path = root / "prompt_writer_state.json"
        if not prompt_path.exists() or json.loads(prompt_path.read_text(encoding="utf-8")) != prompt_state:
            atomic_write_json(prompt_path, prompt_state)
        # Views are a cache of this exact model generation, not fresh imports.
        from project_repository import legacy_workspace_payload
        check_current()
        session.project.update_project_data("extensions", {"legacy_workspace_snapshot": legacy_workspace_payload(root)})
        if transaction is not None:
            transaction.finalize()
    except Exception:
        if transaction is not None:
            transaction.abort()
        for path, original in original_views.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, original)
        raise
    session._legacy_projection_media_signature = media_signature
    session._legacy_projection_signature = repr((session.project.pages, data["prompt_settings"], data["shared_settings"]))


def prepare_consumer(root, consumer: str = "output") -> None:
    session = session_for(root)
    if session is not None and not session.is_restoring:
        session.synchronize_workspace()
        project_sound_views(session)
    require_consumer(root, consumer)
    if session is not None:
        project_legacy_views(session)
