#!/usr/bin/env python3
"""
Central configuration for the eLetter project.

Permanent design:
- Canonical SOURCE assets live in: gallery/user/*
- Generated viewer bundles use normalized RUNTIME layout under: <build>/gallery/*
  (pages/, controls/, sounds/, message/)

Important:
Some tabs import folder-name constants such as SOUNDS_DIR.
Those constants are retained as runtime folder names.
They do not point to old source locations.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional

from transactional_io import atomic_write_json


# ─────────────────────────────────────────────────────────────────────────────
# Project and settings
# ─────────────────────────────────────────────────────────────────────────────

SETTINGS_FILE = "settings.json"
PUBLISHED_PAGE_URL_KEY = "published_page_url"
PLAY_METADATA_FILE = "lettersmith-metadata.json"

DEFAULT_VOLUME = 31
DEFAULT_AUDIO = "music.mp3"


def _project_root() -> Path:
    return Path(__file__).resolve().parent


_PROJECT_ROOT = _project_root()


# ─────────────────────────────────────────────────────────────────────────────
# Normalized runtime folder names
# ─────────────────────────────────────────────────────────────────────────────

GALLERY_DIR = "gallery"

PAGES_DIR = "pages"
CONTROLS_DIR = "controls"
SOUNDS_DIR = "sounds"
MESSAGE_DIR = "message"
FONTS_DIR = "fonts"

# Some older modules may still import ICONS_DIR.
ICONS_DIR = CONTROLS_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Canonical source asset tree
# ─────────────────────────────────────────────────────────────────────────────

GALLERY_USER_DIR = "gallery/user"

USER_PAGES_DIR = f"{GALLERY_USER_DIR}/pages"
USER_CONTROLS_DIR = f"{GALLERY_USER_DIR}/card/controls"
USER_MESSAGE_DIR = f"{GALLERY_USER_DIR}/message"
USER_SOUNDS_DIR = f"{GALLERY_USER_DIR}/sounds"


# ─────────────────────────────────────────────────────────────────────────────
# Message assets
# ─────────────────────────────────────────────────────────────────────────────

MESSAGE_HTML_FILE = f"{USER_MESSAGE_DIR}/message.html"
MESSAGE_IMAGE_FILE = f"{USER_MESSAGE_DIR}/message.png"


# ─────────────────────────────────────────────────────────────────────────────
# Page assets
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_SLIDES = [
    "cover.png",
    "letter.png",
    "wall.png",
    "back.png",
]


# ─────────────────────────────────────────────────────────────────────────────
# Control assets
# ─────────────────────────────────────────────────────────────────────────────

CONTROL_FILES = [
    "npage.png",
    "ppage.png",
    "cleft.png",
    "cright.png",
    "volon.png",
    "voloff.png",
    "showmessageicon.png",
]


# ─────────────────────────────────────────────────────────────────────────────
# Audio assets
# ─────────────────────────────────────────────────────────────────────────────

GLISS_FILE = "glissando.mp3"
MUSIC_FILE = "music.mp3"

FLIP_PREFIX = "flip"
FLIP_COUNT = 10

MAX_AUDIO_MB = 15


# ─────────────────────────────────────────────────────────────────────────────
# User audio archive
# ─────────────────────────────────────────────────────────────────────────────

USER_AUDIO_ARCHIVE_DIR = f"{USER_SOUNDS_DIR}/appssong"

# Compatibility aliases used by different Sound tab versions.
USER_AUDIO_LIBRARY_DIR = USER_AUDIO_ARCHIVE_DIR

USER_AUDIO_ORIGINALS_DIR = f"{USER_AUDIO_ARCHIVE_DIR}/originals"
USER_AUDIO_PROCESSED_DIR = f"{USER_AUDIO_ARCHIVE_DIR}/processed"
USER_AUDIO_ANALYSIS_DIR = f"{USER_AUDIO_ARCHIVE_DIR}/analysis"

USER_AUDIO_CURRENT_MANIFEST = (
    f"{USER_AUDIO_ARCHIVE_DIR}/current.json"
)

USER_AUDIO_CURRENT_MANIFEST_FILE = USER_AUDIO_CURRENT_MANIFEST
USER_AUDIO_MANIFEST_FILE = USER_AUDIO_CURRENT_MANIFEST

USER_AUDIO_PLAYLIST_FILE = (
    f"{USER_AUDIO_ARCHIVE_DIR}/playlist.json"
)


# ─────────────────────────────────────────────────────────────────────────────
# Curtain styles
# ─────────────────────────────────────────────────────────────────────────────

CURTAIN_STYLE_WHITE = "white"
CURTAIN_STYLE_BLACK = "black"
CURTAIN_STYLE_DARK = "dark"
CURTAIN_STYLE_LIGHT = "light"
CURTAIN_STYLE_RED = "red"
CURTAIN_STYLE_BLUE = "blue"
CURTAIN_STYLE_GOLD = "gold"
CURTAIN_STYLE_SILVER = "silver"

CURTAIN_STYLE_DEFAULT = CURTAIN_STYLE_WHITE
DEFAULT_CURTAIN_STYLE = CURTAIN_STYLE_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# Output layout
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_DIR = "output"

OUTPUT_PLAY_DIR = os.path.join(
    OUTPUT_DIR,
    "Play",
)

OUTPUT_FILE_DIR = os.path.join(
    OUTPUT_DIR,
    "File",
)

WINDOWS_RESERVED_FOLDER_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_PLAY_METADATA_CANDIDATES = (
    PLAY_METADATA_FILE,
    "play_metadata.json",
    "recovery_metadata.json",
    "metadata.json",
)
_BUILD_STATE_FILE = "lettersmith-build.json"


# ─────────────────────────────────────────────────────────────────────────────
# Settings loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_settings(project_root: str | Path) -> Dict:
    pr = Path(project_root)
    path = pr / SETTINGS_FILE

    try:
        if path.exists():
            data = json.loads(
                path.read_text(encoding="utf-8")
            )
        else:
            data = {}
    except Exception:
        data = {}

    updated = False

    # Starting volume must remain between 0 and 100.
    try:
        volume = int(
            data.get(
                "starting_volume",
                DEFAULT_VOLUME,
            )
        )
        volume = max(0, min(100, volume))
    except Exception:
        volume = DEFAULT_VOLUME

    if data.get("starting_volume") != volume:
        data["starting_volume"] = volume
        updated = True

    # Store only the audio filename rather than a full path.
    last_audio = data.get(
        "last_audio",
        DEFAULT_AUDIO,
    )

    try:
        last_audio = Path(
            str(last_audio)
        ).name
    except Exception:
        last_audio = DEFAULT_AUDIO

    if data.get("last_audio") != last_audio:
        data["last_audio"] = last_audio
        updated = True

    if updated:
        try:
            path.write_text(
                json.dumps(
                    data,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    return data


_SETTINGS = _load_settings(_PROJECT_ROOT)

STARTING_VOLUME = int(
    _SETTINGS.get(
        "starting_volume",
        DEFAULT_VOLUME,
    )
)

LAST_AUDIO = str(
    _SETTINGS.get(
        "last_audio",
        DEFAULT_AUDIO,
    )
)

CONFIG_DICT = dict(_SETTINGS)


# ─────────────────────────────────────────────────────────────────────────────
# Directory helpers
# ─────────────────────────────────────────────────────────────────────────────

def canonical_output_root(project_root: str | Path) -> Path:
    """Return the one output root, even when a frozen app passes it directly."""
    root = Path(project_root).resolve()
    if root.name.casefold() == OUTPUT_DIR.casefold():
        return root
    return (root / OUTPUT_DIR).resolve()


def canonical_play_root(project_root: str | Path) -> Path:
    return (canonical_output_root(project_root) / "Play").resolve()


def canonical_file_root(project_root: str | Path) -> Path:
    return (canonical_output_root(project_root) / "File").resolve()


def canonical_recovery_root(project_root: str | Path) -> Path:
    return (canonical_output_root(project_root) / "Recovery").resolve()


def legacy_play_roots(project_root: str | Path) -> tuple[Path, ...]:
    """Return recognized duplicated Play roots without creating them."""
    canonical = canonical_play_root(project_root)
    nested = (canonical_output_root(project_root) / OUTPUT_DIR / "Play").resolve()
    return () if nested == canonical else (nested,)


def legacy_recovery_roots(project_root: str | Path) -> tuple[Path, ...]:
    canonical = canonical_recovery_root(project_root)
    nested = (
        canonical_output_root(project_root) / OUTPUT_DIR / "Recovery"
    ).resolve()
    return () if nested == canonical else (nested,)


def safe_folder_name(
    text: object,
    fallback: str,
    *,
    max_length: int = 120,
) -> str:
    """Return one Windows-safe, user-facing folder component."""
    value = unescape(str(text or ""))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if value in {"", ".", ".."}:
        value = str(fallback or "Untitled")
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
        value = re.sub(r"\s+", " ", value).strip(" .") or "Untitled"
    value = value[:max_length].rstrip(" .") or "Untitled"
    reserved_stem = value.split(".", 1)[0].rstrip(" .").upper()
    if reserved_stem in WINDOWS_RESERVED_FOLDER_NAMES:
        value = f"_{value}"
    return value[:max_length].rstrip(" .") or "Untitled"


def _valid_project_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _read_play_metadata(play_dir: Path) -> dict:
    for name in _PLAY_METADATA_CANDIDATES:
        path = play_dir / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _bundle_project_id(play_dir: Path) -> str:
    metadata = _read_play_metadata(play_dir)
    project_id = _valid_project_uuid(metadata.get("project_id"))
    if project_id:
        return project_id
    state_path = play_dir / _BUILD_STATE_FILE
    if state_path.is_file() and not state_path.is_symlink():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            state = {}
        if isinstance(state, dict):
            project_id = _valid_project_uuid(state.get("project_id"))
            if project_id:
                return project_id
    return _valid_project_uuid(play_dir.name)


def _is_play_bundle(play_dir: Path) -> bool:
    if not play_dir.is_dir() or play_dir.is_symlink():
        return False
    required = tuple(
        play_dir / name
        for name in ("index.html", "styles.css", "script.js")
    )
    return all(path.is_file() and not path.is_symlink() for path in required)


def _iter_play_bundles(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        return ()
    bundles: set[Path] = set()
    for index in root.rglob("index.html"):
        candidate = index.parent.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if (
            not relative.parts
            or any(
                ".build-staging" in part
                or ".build-backup" in part
                for part in relative.parts
            )
            or not _is_play_bundle(candidate)
        ):
            continue
        bundles.add(candidate)
    return tuple(
        sorted(
            bundles,
            key=lambda path: str(path).casefold(),
        )
    )


def _html_bundle_title(play_dir: Path) -> str:
    try:
        html = (play_dir / "index.html").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""
    match = re.search(r"<title>\s*(.*?)\s*</title>", html, re.I | re.S)
    return (
        unescape(re.sub(r"\s+", " ", match.group(1))).strip()
        if match
        else ""
    )


def _humanize_folder_name(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[-_]+", " ", value),
    ).strip().title()


def _bundle_display_names(
    play_dir: Path,
    source_root: Path,
    metadata: dict,
) -> tuple[str, str]:
    relative = play_dir.relative_to(source_root)
    recipient = str(metadata.get("recipient_name") or "").strip()
    title = str(metadata.get("recipient_title") or "").strip()
    if not recipient and len(relative.parts) >= 2:
        recipient = _humanize_folder_name(relative.parts[-2])
    if not title:
        if not _valid_project_uuid(relative.parts[-1]):
            title = _humanize_folder_name(relative.parts[-1])
        title = title or _html_bundle_title(play_dir)
    return (
        recipient or "Unknown Recipient",
        title or "Untitled Letter",
    )


def _casefold_child(parent: Path, name: str) -> Optional[Path]:
    if not parent.is_dir():
        return None
    folded = name.casefold()
    try:
        children = tuple(parent.iterdir())
    except OSError:
        return None
    for child in children:
        if child.name.casefold() == folded:
            return child
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _rename_component_case(path: Path, requested_name: str) -> Path:
    """Apply a case-only directory rename without replacing another entry."""
    if (
        path.name == requested_name
        or path.name.casefold() != requested_name.casefold()
    ):
        return path
    requested = path.with_name(requested_name)
    if requested.exists():
        try:
            same_entry = path.samefile(requested)
        except OSError:
            same_entry = False
        if not same_entry:
            raise FileExistsError(
                f"Case-normalized saved-letter path exists: {requested}"
            )
    temporary = path.with_name(
        f".lettersmith-case-{uuid.uuid4().hex}"
    )
    while temporary.exists():
        temporary = path.with_name(
            f".lettersmith-case-{uuid.uuid4().hex}"
        )
    os.replace(path, temporary)
    try:
        os.replace(temporary, requested)
    except OSError:
        if temporary.exists() and not path.exists():
            os.replace(temporary, path)
        raise
    return requested.resolve(strict=True)


def _case_normalized_bundle_path(
    play_dir: Path,
    play_root: Path,
    *,
    recipient: object,
    title: object,
) -> Path:
    try:
        relative = play_dir.relative_to(play_root)
    except ValueError:
        return play_dir
    if len(relative.parts) != 2:
        return play_dir

    recipient_name = safe_folder_name(
        recipient,
        "Unknown Recipient",
    )
    title_name = safe_folder_name(
        title,
        "Untitled Letter",
    )
    actual_title = relative.parts[1]
    if actual_title.casefold() == title_name.casefold():
        requested_title = title_name
    else:
        suffix = re.fullmatch(
            rf"{re.escape(title_name)} (\([2-9][0-9]*\))",
            actual_title,
            flags=re.I,
        )
        requested_title = (
            f"{title_name} {suffix.group(1)}"
            if suffix
            else actual_title
        )

    normalized = _rename_component_case(
        play_dir,
        requested_title,
    )
    normalized_parent = _rename_component_case(
        normalized.parent,
        recipient_name,
    )
    return (normalized_parent / normalized.name).resolve(strict=True)


def _bundle_case_matches(
    play_dir: Path,
    play_root: Path,
    *,
    recipient: object,
    title: object,
) -> bool:
    try:
        relative = play_dir.relative_to(play_root)
    except ValueError:
        return False
    if len(relative.parts) != 2:
        return False
    recipient_name = safe_folder_name(
        recipient,
        "Unknown Recipient",
    )
    title_name = safe_folder_name(
        title,
        "Untitled Letter",
    )
    return bool(
        relative.parts[0] == recipient_name
        and (
            relative.parts[1] == title_name
            or re.fullmatch(
                rf"{re.escape(title_name)} \([2-9][0-9]*\)",
                relative.parts[1],
            )
            is not None
        )
    )


def _available_named_play_directory(
    play_root: Path,
    recipient: object,
    title: object,
    *,
    project_id: str,
    source: Optional[Path] = None,
    allow_existing_project: bool,
) -> Path:
    recipient_name = safe_folder_name(recipient, "Unknown Recipient")
    title_name = safe_folder_name(title, "Untitled Letter")
    existing_recipient = _casefold_child(play_root, recipient_name)
    recipient_dir = (
        existing_recipient
        if existing_recipient is not None and existing_recipient.is_dir()
        else play_root / recipient_name
    )
    candidate_number = 1
    while True:
        candidate_name = (
            title_name
            if candidate_number == 1
            else safe_folder_name(
                f"{title_name} ({candidate_number})",
                "Untitled Letter",
            )
        )
        existing = _casefold_child(recipient_dir, candidate_name)
        candidate = (
            existing
            if existing is not None
            else recipient_dir / candidate_name
        ).resolve()
        if not _is_relative_to(candidate, play_root):
            raise ValueError("The saved-letter path escapes the Play root.")
        if source is not None and candidate == source:
            return candidate
        if existing is None:
            return candidate
        if (
            allow_existing_project
            and project_id
            and _bundle_project_id(candidate) == project_id
        ):
            return candidate
        candidate_number += 1


def _write_migrated_metadata(
    play_dir: Path,
    metadata: dict,
    *,
    project_id: str,
    recipient: str,
    title: str,
) -> None:
    updated = dict(metadata)
    updated["project_id"] = project_id
    updated["recipient_name"] = recipient
    updated["recipient_title"] = title
    if "build_location" in updated:
        updated["build_location"] = str(play_dir)
    atomic_write_json(play_dir / PLAY_METADATA_FILE, updated)


@dataclass(frozen=True)
class PlayBundleMigration:
    source: Path
    destination: Path
    project_id: str


def migrate_play_bundle(
    project_root: str | Path,
    source: str | Path,
    *,
    recipient: object,
    title: object,
    project_id: object = "",
    allow_existing_project: bool = False,
) -> PlayBundleMigration:
    """Move one valid bundle into the canonical recipient/title layout."""
    original = Path(source)
    if ".." in original.parts or original.is_symlink():
        raise ValueError("Saved-letter path traversal and links are not allowed.")
    source_path = original.resolve(strict=True)
    allowed_roots = (
        canonical_play_root(project_root),
        *legacy_play_roots(project_root),
    )
    source_root = next(
        (
            root
            for root in allowed_roots
            if source_path != root
            and _is_relative_to(source_path, root)
        ),
        None,
    )
    if source_root is None or not _is_play_bundle(source_path):
        raise ValueError("The source is not a managed Play bundle.")
    source_stat = source_path.stat()
    metadata = _read_play_metadata(source_path)
    stable_id = (
        _valid_project_uuid(project_id)
        or _bundle_project_id(source_path)
        or str(uuid.uuid4())
    )
    recipient_name = str(recipient or "").strip() or "Unknown Recipient"
    title_name = str(title or "").strip() or "Untitled Letter"
    play_root = canonical_play_root(project_root)
    play_root.mkdir(parents=True, exist_ok=True)
    destination = _available_named_play_directory(
        play_root,
        recipient_name,
        title_name,
        project_id=stable_id,
        source=source_path,
        allow_existing_project=allow_existing_project,
    )
    if destination != source_path:
        if destination.exists():
            if (
                allow_existing_project
                and _bundle_project_id(destination) == stable_id
            ):
                return PlayBundleMigration(source_path, destination, stable_id)
            raise FileExistsError(f"Saved-letter destination exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_path, destination)
    destination = _case_normalized_bundle_path(
        destination,
        play_root,
        recipient=recipient_name,
        title=title_name,
    )
    _write_migrated_metadata(
        destination,
        metadata,
        project_id=stable_id,
        recipient=recipient_name,
        title=title_name,
    )
    os.utime(
        destination,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )
    return PlayBundleMigration(source_path, destination, stable_id)


def migrate_legacy_play_bundles(
    project_root: str | Path,
    *,
    strict: bool = False,
) -> tuple[PlayBundleMigration, ...]:
    """Normalize every saved bundle into the canonical visible layout."""
    canonical = canonical_play_root(project_root)
    sources: list[tuple[Path, Path]] = []
    for bundle in _iter_play_bundles(canonical):
        metadata = _read_play_metadata(bundle)
        recipient, title = _bundle_display_names(
            bundle,
            canonical,
            metadata,
        )
        relative = bundle.relative_to(canonical)
        expected_recipient = safe_folder_name(
            recipient,
            "Unknown Recipient",
        )
        expected_title = safe_folder_name(
            title,
            "Untitled Letter",
        )
        title_matches = (
            len(relative.parts) == 2
            and (
                relative.parts[1] == expected_title
                or re.fullmatch(
                    rf"{re.escape(expected_title)} \([2-9][0-9]*\)",
                    relative.parts[1],
                )
                is not None
            )
        )
        metadata_is_complete = bool(
            _valid_project_uuid(metadata.get("project_id"))
            and str(metadata.get("recipient_name") or "").strip()
            and str(metadata.get("recipient_title") or "").strip()
        )
        if (
            len(relative.parts) != 2
            or relative.parts[0] != expected_recipient
            or not title_matches
            or not metadata_is_complete
        ):
            sources.append((bundle, canonical))
    for legacy_root in legacy_play_roots(project_root):
        sources.extend(
            (bundle, legacy_root)
            for bundle in _iter_play_bundles(legacy_root)
        )

    migrations: list[PlayBundleMigration] = []
    seen: set[Path] = set()
    for source, source_root in sources:
        if source in seen or not source.exists():
            continue
        seen.add(source)
        metadata = _read_play_metadata(source)
        recipient, title = _bundle_display_names(
            source,
            source_root,
            metadata,
        )
        try:
            migrations.append(
                migrate_play_bundle(
                    project_root,
                    source,
                    recipient=recipient,
                    title=title,
                    project_id=_bundle_project_id(source),
                    allow_existing_project=False,
                )
            )
        except (OSError, ValueError):
            if strict:
                raise
    return tuple(migrations)


def resolve_play_bundle_directory(
    project_root: str | Path,
    *,
    recipient: object,
    title: object,
    project_id: object,
    migrate_existing: bool = True,
) -> Path:
    """Resolve the active project's canonical visible folder."""
    stable_id = _valid_project_uuid(project_id)
    if not stable_id:
        raise ValueError("project_id must be a UUID string")
    play_root = canonical_play_root(project_root)
    play_root.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    for root in (play_root, *legacy_play_roots(project_root)):
        for bundle in _iter_play_bundles(root):
            if _bundle_project_id(bundle) == stable_id:
                candidates.append(bundle)
    candidates.sort(
        key=lambda path: (
            _is_relative_to(path, play_root),
            path.stat().st_mtime,
        ),
        reverse=True,
    )
    source = candidates[0] if candidates else None
    destination = _available_named_play_directory(
        play_root,
        recipient,
        title,
        project_id=stable_id,
        source=source,
        allow_existing_project=True,
    )
    case_mismatch = bool(
        source is not None
        and not _bundle_case_matches(
            source,
            play_root,
            recipient=recipient,
            title=title,
        )
    )
    if (
        source is not None
        and migrate_existing
        and (source != destination or case_mismatch)
    ):
        same_location = False
        if destination.exists():
            try:
                same_location = source.samefile(destination)
            except OSError:
                same_location = False
        if (
            not same_location
            and destination.exists()
            and _bundle_project_id(destination) == stable_id
        ):
            return destination
        return migrate_play_bundle(
            project_root,
            source,
            recipient=recipient,
            title=title,
            project_id=stable_id,
            allow_existing_project=True,
        ).destination
    return destination


def ensure_output_dirs(
    project_root: str | Path,
) -> None:
    for directory in (
        canonical_play_root(project_root),
        canonical_file_root(project_root),
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def ensure_gallery_dirs(
    project_root: str | Path,
) -> None:
    """
    Ensure all canonical source directories exist.

    Runtime Play bundle directories are created by plan_build().
    """

    pr = Path(project_root)

    source_directories = (
        USER_PAGES_DIR,
        USER_CONTROLS_DIR,
        USER_MESSAGE_DIR,
        USER_SOUNDS_DIR,
        USER_AUDIO_ARCHIVE_DIR,
        USER_AUDIO_ORIGINALS_DIR,
        USER_AUDIO_PROCESSED_DIR,
        USER_AUDIO_ANALYSIS_DIR,
    )

    for directory in source_directories:
        (
            pr / directory
        ).mkdir(
            parents=True,
            exist_ok=True,
        )


def safe_slug(
    text: Optional[str],
    fallback: str = "eletter",
) -> str:
    if not text:
        return fallback

    slug = re.sub(
        r"\s+",
        " ",
        text,
    ).strip().lower()

    slug = re.sub(
        r"[^a-z0-9\-_. ]+",
        "",
        slug,
    )

    slug = slug.replace(
        " ",
        "-",
    )

    return slug or fallback


def display_name(
    text: Optional[str],
    fallback: str = "eLetter",
) -> str:
    if not text:
        return fallback

    name = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return name or fallback


def _safe_clear_dir_contents(
    directory_path: Path,
) -> None:
    """
    Remove everything inside a directory while retaining the directory itself.

    This ensures that generating a project overwrites the existing build
    rather than producing timestamped copies.
    """

    if not directory_path.exists():
        return

    if not directory_path.is_dir():
        return

    for entry in directory_path.iterdir():
        if entry.is_file() or entry.is_symlink():
            entry.unlink(
                missing_ok=True
            )
        elif entry.is_dir():
            shutil.rmtree(entry)


# ─────────────────────────────────────────────────────────────────────────────
# Build planning
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BuildPaths:
    play_dir: Path
    play_gallery_dir: Path
    play_pages_dir: Path
    play_controls_dir: Path
    play_message_dir: Path
    play_sounds_dir: Path
    play_fonts_dir: Path


def plan_build(
    project_root: str | Path,
    *,
    recipient: str,
    title: str,
    project_id: str,
    play_dir_override: Optional[str | Path] = None,
) -> BuildPaths:
    """
    Create a deterministic project build location.

    Output:
        output/Play/<recipient>/<title>/

    ``project_id`` remains stable metadata while recipient/title provide the
    visible folder names. Renaming either field safely relocates the bundle.

    Runtime layout:

        <play_dir>/
            index.html
            styles.css
            script.js
            gallery/
                pages/
                controls/
                message/
                sounds/
                fonts/
    """

    pr = Path(project_root)

    ensure_output_dirs(pr)

    final_play_dir = resolve_play_bundle_directory(
        pr,
        recipient=recipient,
        title=title,
        project_id=project_id,
    )
    play_dir = (
        Path(play_dir_override).resolve()
        if play_dir_override is not None
        else final_play_dir
    )
    if play_dir_override is not None and (
        play_dir == final_play_dir
        or play_dir.parent != final_play_dir.parent
    ):
        raise ValueError("The build staging directory must be a sibling of the final build.")

    play_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    _safe_clear_dir_contents(play_dir)

    play_gallery = (
        play_dir
        / GALLERY_DIR
    )

    play_pages = (
        play_gallery
        / PAGES_DIR
    )

    play_controls = (
        play_gallery
        / CONTROLS_DIR
    )

    play_message = (
        play_gallery
        / MESSAGE_DIR
    )

    play_sounds = (
        play_gallery
        / SOUNDS_DIR
    )

    play_fonts = (
        play_gallery
        / FONTS_DIR
    )

    runtime_directories = (
        play_pages,
        play_controls,
        play_message,
        play_sounds,
        play_fonts,
    )

    for directory in runtime_directories:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    return BuildPaths(
        play_dir=play_dir,
        play_gallery_dir=play_gallery,
        play_pages_dir=play_pages,
        play_controls_dir=play_controls,
        play_message_dir=play_message,
        play_sounds_dir=play_sounds,
        play_fonts_dir=play_fonts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_required_images(
    project_root: str | Path,
) -> List[str]:
    base = (
        Path(project_root)
        / USER_PAGES_DIR
    )

    return [
        filename
        for filename in REQUIRED_SLIDES
        if not (
            base / filename
        ).is_file()
    ]


def validate_controls(
    project_root: str | Path,
) -> List[str]:
    base = (
        Path(project_root)
        / USER_CONTROLS_DIR
    )

    return [
        filename
        for filename in CONTROL_FILES
        if not (
            base / filename
        ).is_file()
    ]


def validate_audio_assets(
    project_root: str | Path,
) -> List[str]:
    sounds_directory = (
        Path(project_root)
        / USER_SOUNDS_DIR
    )

    missing: List[str] = []

    if not (
        sounds_directory
        / GLISS_FILE
    ).is_file():
        missing.append(GLISS_FILE)

    if not (
        sounds_directory
        / MUSIC_FILE
    ).is_file():
        missing.append(MUSIC_FILE)

    for index in range(
        1,
        FLIP_COUNT + 1,
    ):
        filename = (
            f"{FLIP_PREFIX}{index}.mp3"
        )

        if not (
            sounds_directory
            / filename
        ).is_file():
            missing.append(filename)

    return missing


# ─────────────────────────────────────────────────────────────────────────────
# Config wrapper
# ─────────────────────────────────────────────────────────────────────────────

class Config:
    def __init__(
        self,
        project_root: str | Path = _PROJECT_ROOT,
        **_ignored,
    ):
        self.project_root = Path(project_root)

        self._settings = _load_settings(
            self.project_root
        )

    def __getattr__(
        self,
        key: str,
    ):
        if key in self._settings:
            return self._settings[key]

        raise AttributeError(
            f"No config key named '{key}'"
        )

    def __getitem__(
        self,
        key: str,
    ):
        return self._settings.get(key)

    def as_dict(self) -> Dict:
        return dict(self._settings)


# ─────────────────────────────────────────────────────────────────────────────
# Public exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Settings
    "SETTINGS_FILE",
    "PUBLISHED_PAGE_URL_KEY",
    "PLAY_METADATA_FILE",
    "DEFAULT_VOLUME",
    "DEFAULT_AUDIO",
    "STARTING_VOLUME",
    "LAST_AUDIO",
    "CONFIG_DICT",

    # Runtime folder names
    "GALLERY_DIR",
    "PAGES_DIR",
    "CONTROLS_DIR",
    "SOUNDS_DIR",
    "MESSAGE_DIR",
    "FONTS_DIR",
    "ICONS_DIR",

    # Canonical source tree
    "GALLERY_USER_DIR",
    "USER_PAGES_DIR",
    "USER_CONTROLS_DIR",
    "USER_MESSAGE_DIR",
    "USER_SOUNDS_DIR",

    # Message assets
    "MESSAGE_HTML_FILE",
    "MESSAGE_IMAGE_FILE",

    # Page and control assets
    "REQUIRED_SLIDES",
    "CONTROL_FILES",

    # Audio
    "GLISS_FILE",
    "MUSIC_FILE",
    "FLIP_PREFIX",
    "FLIP_COUNT",
    "MAX_AUDIO_MB",

    # User audio archive
    "USER_AUDIO_ARCHIVE_DIR",
    "USER_AUDIO_LIBRARY_DIR",
    "USER_AUDIO_ORIGINALS_DIR",
    "USER_AUDIO_PROCESSED_DIR",
    "USER_AUDIO_ANALYSIS_DIR",
    "USER_AUDIO_CURRENT_MANIFEST",
    "USER_AUDIO_CURRENT_MANIFEST_FILE",
    "USER_AUDIO_MANIFEST_FILE",
    "USER_AUDIO_PLAYLIST_FILE",

    # Curtain styles
    "CURTAIN_STYLE_WHITE",
    "CURTAIN_STYLE_BLACK",
    "CURTAIN_STYLE_DARK",
    "CURTAIN_STYLE_LIGHT",
    "CURTAIN_STYLE_RED",
    "CURTAIN_STYLE_BLUE",
    "CURTAIN_STYLE_GOLD",
    "CURTAIN_STYLE_SILVER",
    "CURTAIN_STYLE_DEFAULT",
    "DEFAULT_CURTAIN_STYLE",

    # Output
    "OUTPUT_DIR",
    "OUTPUT_PLAY_DIR",
    "OUTPUT_FILE_DIR",

    # Helpers
    "canonical_output_root",
    "canonical_play_root",
    "canonical_file_root",
    "canonical_recovery_root",
    "legacy_play_roots",
    "legacy_recovery_roots",
    "safe_folder_name",
    "migrate_play_bundle",
    "migrate_legacy_play_bundles",
    "resolve_play_bundle_directory",
    "PlayBundleMigration",
    "ensure_output_dirs",
    "ensure_gallery_dirs",
    "safe_slug",
    "display_name",
    "plan_build",
    "BuildPaths",

    # Validation
    "validate_required_images",
    "validate_controls",
    "validate_audio_assets",

    # Wrapper
    "Config",
]
