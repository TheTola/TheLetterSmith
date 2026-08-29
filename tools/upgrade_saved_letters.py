#!/usr/bin/env python3
"""Upgrade existing pre-release saved letters, then leave runtime loading strict."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    MESSAGE_ASSETS_DIR,
    PLAY_METADATA_FILE,
    REQUIRED_SLIDES,
    canonical_play_root,
    canonical_recovery_root,
)
from image_animation import (  # noqa: E402
    IMAGE_MANIFEST_NAME,
    SLOT_DEFINITIONS,
    validate_runtime_image_manifest,
    write_image_manifest,
)
from project_paths import PROJECT_METADATA_SCHEMA_VERSION  # noqa: E402
from recipient_registry import RecipientRegistry  # noqa: E402
from save_schema import (  # noqa: E402
    CURRENT_SAVE_SCHEMA_VERSION,
    SAVED_LETTER_DOCUMENT_TYPE,
    stamp_current_save_schema,
    stored_save_schema_version,
    validate_saved_letter_metadata,
)
from saved_letters import (  # noqa: E402
    PROMPT_WRITER_STATE_FILE,
    SavedLetterCatalog,
    SavedLetterRestorer,
)
from settings_store import (  # noqa: E402
    DEFAULT_CURTAIN_STYLE,
    DEFAULT_SETTINGS,
    normalize_curtain_style,
)
from transactional_io import atomic_write_json  # noqa: E402


class PreReleaseSavedLetterMigrationError(RuntimeError):
    pass


_LOGGER = logging.getLogger(__name__)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PreReleaseSavedLetterMigrationError(f"{label} is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreReleaseSavedLetterMigrationError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise PreReleaseSavedLetterMigrationError(f"{label} must be an object")
    return value


def _valid_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return ""


def _normalized_required_features(value: object) -> list[str]:
    if isinstance(value, Mapping):
        candidates = [key for key, enabled in value.items() if bool(enabled)]
    elif isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = []
    return sorted(
        {
            str(candidate).strip().casefold()
            for candidate in candidates
            if str(candidate).strip()
        }
    )


def _current_settings(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("settings")
    settings = dict(raw) if isinstance(raw, Mapping) else {}
    starting_volume = settings.get(
        "starting_volume",
        metadata.get("starting_volume", DEFAULT_SETTINGS["starting_volume"]),
    )
    settings.setdefault("starting_volume", starting_volume)
    settings.setdefault("music_volume", starting_volume)
    settings["curtain_style"] = normalize_curtain_style(
        settings.get("curtain_style", DEFAULT_CURTAIN_STYLE)
    )
    settings.setdefault(
        "message_overlay_preset",
        DEFAULT_SETTINGS["message_overlay_preset"],
    )
    settings.setdefault(
        "message_overlay_opacity",
        DEFAULT_SETTINGS["message_overlay_opacity"],
    )
    settings["required_features"] = _normalized_required_features(
        settings.get("required_features", [])
    )
    settings.setdefault("forge_preview_mode", "window")
    return settings


def _static_image_manifest(pages: Path) -> dict[str, Any]:
    gifs = tuple(path for path in pages.glob("*.gif") if path.is_file())
    if gifs:
        raise PreReleaseSavedLetterMigrationError(
            "an image manifest cannot be inferred safely for animated pages"
        )
    slots: dict[str, dict[str, Any]] = {}
    for slot, definition in SLOT_DEFINITIONS.items():
        filename = f"{definition['basename']}.png"
        path = pages / filename
        if path.is_symlink() or not path.is_file():
            raise PreReleaseSavedLetterMigrationError(
                f"required page is missing or unsafe: {filename}"
            )
        slots[slot] = {
            "asset_type": "static",
            "is_animated_gif": False,
            "source_file": filename,
            "preview_file": filename,
        }
    return {"slots": slots}


def _current_metadata(
    project_root: Path,
    metadata: Mapping[str, Any],
    sound_payload: Mapping[str, Any],
) -> dict[str, Any]:
    migrated = dict(metadata)
    recipient_name = str(
        migrated.get("recipient_display_name")
        or migrated.get("recipient_name")
        or ""
    ).strip()
    recipient_title = str(migrated.get("recipient_title") or "").strip()
    if not recipient_name or not recipient_title:
        raise PreReleaseSavedLetterMigrationError(
            "recipient name and letter title are required"
        )
    record = RecipientRegistry(project_root).get_or_create(
        recipient_name,
        custom_capitalization=True,
        recipient_id=_valid_uuid(migrated.get("recipient_id")) or None,
    )
    project_id = _valid_uuid(migrated.get("project_id")) or str(uuid.uuid4())
    tracks = sound_payload.get("tracks", [])
    tracks = tracks if isinstance(tracks, list) else []
    mode = str(sound_payload.get("mode", "single")).strip()
    migrated.update(
        {
            "project_id": project_id,
            "project_schema_version": PROJECT_METADATA_SCHEMA_VERSION,
            "recipient_id": record.recipient_id,
            "recipient_display_name": record.display_name,
            "recipient_normalized_key": record.normalized_key,
            "recipient_name": record.display_name,
            "recipient_title": recipient_title,
            "build_timestamp": str(
                migrated.get("build_timestamp")
                or migrated.get("updated_at")
                or migrated.get("created_at")
                or datetime.now(timezone.utc).isoformat()
            ),
            "settings": _current_settings(migrated),
            "editable_assets": {
                "pages": {
                    name: f"gallery/pages/{name}" for name in REQUIRED_SLIDES
                },
                "message": "gallery/message/message.html",
                "message_assets": MESSAGE_ASSETS_DIR,
                "sound_manifest": "gallery/sounds/lettersmith-sound.json",
                "prompt_writer_state": PROMPT_WRITER_STATE_FILE,
                "image_manifest": f"gallery/pages/{IMAGE_MANIFEST_NAME}",
            },
            "sound": {
                "mode": mode,
                "playlist_order": [
                    str(track.get("display_title", "")).strip()
                    for track in tracks
                    if isinstance(track, Mapping)
                ],
                "track_count": len(tracks),
                "crossfade_ms": sound_payload.get("crossfade_ms", 0),
            },
            "readiness": (
                dict(migrated["readiness"])
                if isinstance(migrated.get("readiness"), Mapping)
                else {"percentage": 100, "status": "Ready"}
            ),
            "cover_thumbnail_path": "gallery/pages/cover.png",
        }
    )
    if not migrated.get("published_public_path") and migrated.get("public_path"):
        migrated["published_public_path"] = migrated["public_path"]
    migrated.pop("public_path", None)
    migrated = stamp_current_save_schema(
        migrated,
        document_type=SAVED_LETTER_DOCUMENT_TYPE,
    )
    return validate_saved_letter_metadata(migrated)


def upgrade_saved_letter_bundle(
    project_root: str | Path,
    bundle_path: str | Path,
) -> bool:
    root = Path(project_root).resolve()
    bundle = Path(bundle_path).resolve()
    restorer = SavedLetterRestorer(root)
    bundle = restorer._validated_play_directory(bundle)
    metadata_path = bundle / PLAY_METADATA_FILE
    metadata = _read_json_object(metadata_path, "saved-letter metadata")
    version = stored_save_schema_version(metadata)
    if version == CURRENT_SAVE_SCHEMA_VERSION:
        validate_saved_letter_metadata(metadata)
        return False
    if version and not version.isdigit():
        raise PreReleaseSavedLetterMigrationError(
            f"unsupported saved-letter schema: {version}"
        )

    pages = bundle / "gallery" / "pages"
    message = bundle / "gallery" / "message"
    sounds = bundle / "gallery" / "sounds"
    restorer._validate_pages(pages, require_manifest=False)
    restorer._validate_message(bundle, message)
    sound_payload, _tracks = restorer._validate_sound(sounds)

    prompt_path = bundle / PROMPT_WRITER_STATE_FILE
    if prompt_path.exists() or prompt_path.is_symlink():
        _read_json_object(prompt_path, "saved Prompt Writer state")
    else:
        from PromptWriterPanel import empty_prompt_writer_state

        atomic_write_json(prompt_path, empty_prompt_writer_state())

    image_manifest_path = pages / IMAGE_MANIFEST_NAME
    if image_manifest_path.exists() or image_manifest_path.is_symlink():
        validate_runtime_image_manifest(pages)
    else:
        write_image_manifest(pages, _static_image_manifest(pages))
        validate_runtime_image_manifest(pages)

    current = _current_metadata(root, metadata, sound_payload)
    try:
        atomic_write_json(metadata_path, current)
        restorer._validated_saved_letter_content(bundle)
    except Exception:
        atomic_write_json(metadata_path, metadata)
        raise
    return True


def upgrade_saved_letter_library(project_root: str | Path) -> tuple[int, int]:
    root = Path(project_root).resolve()
    migrated = 0
    skipped = 0
    for library_root in (canonical_play_root(root), canonical_recovery_root(root)):
        if not library_root.is_dir():
            continue
        for metadata_path in library_root.rglob(PLAY_METADATA_FILE):
            bundle = metadata_path.parent
            relative = bundle.relative_to(library_root)
            if any(
                marker in part
                for part in relative.parts
                for marker in (".build-staging", ".build-backup", ".letter-load-")
            ):
                continue
            if not SavedLetterCatalog._is_valid_candidate(bundle):
                skipped += 1
                continue
            try:
                if upgrade_saved_letter_bundle(root, bundle):
                    migrated += 1
            except Exception:
                skipped += 1
                _LOGGER.warning(
                    "Skipping saved letter that could not be upgraded: %s",
                    bundle,
                    exc_info=True,
                )
    return migrated, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", nargs="?", default=str(PROJECT_ROOT))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if not args.apply:
        pending = 0
        skipped = 0
        for library_root in (canonical_play_root(root), canonical_recovery_root(root)):
            if not library_root.is_dir():
                continue
            for metadata_path in library_root.rglob(PLAY_METADATA_FILE):
                bundle = metadata_path.parent
                relative = bundle.relative_to(library_root)
                if any(
                    marker in part
                    for part in relative.parts
                    for marker in (
                        ".build-staging",
                        ".build-backup",
                        ".letter-load-",
                    )
                ):
                    continue
                metadata = _read_json_object(metadata_path, "saved-letter metadata")
                if stored_save_schema_version(metadata) == CURRENT_SAVE_SCHEMA_VERSION:
                    continue
                if not SavedLetterCatalog._is_valid_candidate(bundle):
                    skipped += 1
                else:
                    pending += 1
        print(
            f"{pending} valid saved letter(s) require migration; "
            f"{skipped} incomplete item(s) are excluded."
        )
        return 0
    migrated, skipped = upgrade_saved_letter_library(root)
    print(f"Migrated {migrated} saved letter(s); skipped {skipped} incomplete item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
