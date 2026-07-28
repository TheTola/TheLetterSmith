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
from pathlib import Path
from typing import Dict, List, Optional


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

def ensure_output_dirs(
    project_root: str | Path,
) -> None:
    pr = Path(project_root)

    for subdirectory in (
        OUTPUT_PLAY_DIR,
        OUTPUT_FILE_DIR,
    ):
        (
            pr / subdirectory
        ).mkdir(
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
        output/Play/<project_id>/

    Recipient and title are display metadata only.
    Renaming either field does not change the project folder.

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

    try:
        stable_id = str(uuid.UUID(str(project_id).strip()))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "project_id must be a UUID string"
        ) from None

    final_play_dir = (
        pr
        / OUTPUT_PLAY_DIR
        / stable_id
    ).resolve()
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
