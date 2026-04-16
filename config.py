#!/usr/bin/env python3
"""
Central configuration for the eLetter project.

Permanent design:
- Canonical SOURCE assets live in: gallery/user/*
- Generated viewer bundles use normalized RUNTIME layout under: <build>/gallery/*
  (pages/, controls/, sounds/, message/)

Important:
Some tabs (ex: sound_tab) import folder-name constants like SOUNDS_DIR.
Those constants are kept as RUNTIME folder names (not “legacy” behavior).
They do not point to old source locations.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Project + settings
# ─────────────────────────────────────────────────────────────────────────────
SETTINGS_FILE = "settings.json"

DEFAULT_VOLUME = 31        # 0–100 (0 is valid)
DEFAULT_AUDIO = "music.mp3"


def _project_root() -> Path:
    return Path(__file__).resolve().parent


_PROJECT_ROOT = _project_root()

# ─────────────────────────────────────────────────────────────────────────────
# Normalized RUNTIME folder names (used inside Play/File builds)
# These are simple directory names under <build>/gallery/
# ─────────────────────────────────────────────────────────────────────────────
GALLERY_DIR = "gallery"

# These names are required by existing tabs (e.g., sound_tab imports SOUNDS_DIR).
# They represent RUNTIME layout, not source layout.
PAGES_DIR = "pages"
CONTROLS_DIR = "controls"
SOUNDS_DIR = "sounds"
MESSAGE_DIR = "message"
FONTS_DIR = "fonts"

# Keep these names as well because other files may import them.
# They are also RUNTIME folder names (not the old gallery/icons concept).
ICONS_DIR = CONTROLS_DIR  # runtime uses "controls" (we do not use "icons" anymore)

# ─────────────────────────────────────────────────────────────────────────────
# Canonical SOURCE asset tree (single source of truth on disk)
# ─────────────────────────────────────────────────────────────────────────────
GALLERY_USER_DIR = "gallery/user"

USER_PAGES_DIR = f"{GALLERY_USER_DIR}/pages"
USER_CONTROLS_DIR = f"{GALLERY_USER_DIR}/card/controls"
USER_MESSAGE_DIR = f"{GALLERY_USER_DIR}/message"
USER_SOUNDS_DIR = f"{GALLERY_USER_DIR}/sounds"

# Message (SOURCE)
MESSAGE_HTML_FILE = f"{USER_MESSAGE_DIR}/message.html"
MESSAGE_IMAGE_FILE = f"{USER_MESSAGE_DIR}/message.png"

# Pages (SOURCE)
REQUIRED_SLIDES = ["cover.png", "letter.png", "wall.png", "back.png"]

# Controls (SOURCE)
CONTROL_FILES = [
    "npage.png",
    "ppage.png",
    "cleft.png",
    "cright.png",
    "volon.png",
    "voloff.png",
    "showmessageicon.png",
]

# Audio (SOURCE)
GLISS_FILE = "glissando.mp3"
MUSIC_FILE = "music.mp3"
FLIP_PREFIX = "flip"
FLIP_COUNT = 10

MAX_AUDIO_MB = 15

# ─────────────────────────────────────────────────────────────────────────────
# Output layout (Zip removed)
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
OUTPUT_PLAY_DIR = os.path.join(OUTPUT_DIR, "Play")  # browsable bundle
OUTPUT_FILE_DIR = os.path.join(OUTPUT_DIR, "File")  # single-file HTML (inlined)

# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────
def _load_settings(project_root: str | Path) -> Dict:
    pr = Path(project_root)
    path = pr / SETTINGS_FILE

    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}

    updated = False

    # starting_volume (0–100)
    try:
        v = int(data.get("starting_volume", DEFAULT_VOLUME))
        v = max(0, min(100, v))
    except Exception:
        v = DEFAULT_VOLUME

    if data.get("starting_volume") != v:
        data["starting_volume"] = v
        updated = True

    # last_audio (store filename only)
    last_audio = data.get("last_audio", DEFAULT_AUDIO)
    try:
        last_audio = Path(str(last_audio)).name
    except Exception:
        last_audio = DEFAULT_AUDIO

    if data.get("last_audio") != last_audio:
        data["last_audio"] = last_audio
        updated = True

    if updated:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    return data


_SETTINGS = _load_settings(_PROJECT_ROOT)

STARTING_VOLUME = int(_SETTINGS.get("starting_volume", DEFAULT_VOLUME))
LAST_AUDIO = str(_SETTINGS.get("last_audio", DEFAULT_AUDIO))
CONFIG_DICT = dict(_SETTINGS)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def ensure_output_dirs(project_root: str | Path) -> None:
    pr = Path(project_root)
    for sub in (OUTPUT_PLAY_DIR, OUTPUT_FILE_DIR):
        (pr / sub).mkdir(parents=True, exist_ok=True)


def ensure_gallery_dirs(project_root: str | Path) -> None:
    """
    Ensures the canonical SOURCE directories exist (gallery/user/*).
    Does not create runtime Play bundle directories; plan_build() does.
    """
    pr = Path(project_root)
    (pr / USER_PAGES_DIR).mkdir(parents=True, exist_ok=True)
    (pr / USER_CONTROLS_DIR).mkdir(parents=True, exist_ok=True)
    (pr / USER_MESSAGE_DIR).mkdir(parents=True, exist_ok=True)
    (pr / USER_SOUNDS_DIR).mkdir(parents=True, exist_ok=True)


def safe_slug(text: Optional[str], fallback: str = "eletter") -> str:
    if not text:
        return fallback
    t = re.sub(r"\s+", " ", text).strip().lower()
    t = re.sub(r"[^a-z0-9\-_. ]+", "", t)
    t = t.replace(" ", "-")
    return t or fallback


def display_name(text: Optional[str], fallback: str = "eLetter") -> str:
    if not text:
        return fallback
    name = re.sub(r"\s+", " ", text).strip()
    return name or fallback


def _safe_clear_dir_contents(dir_path: Path) -> None:
    """
    Remove all files/folders inside dir_path, keeping dir_path itself.
    This enforces: Generate overwrites the same save location (no dated folders).
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return
    for entry in dir_path.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Build planning (Play bundle normalized layout)
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


def plan_build(project_root: str | Path, *, recipient: str, title: str) -> BuildPaths:
    """
    Deterministic output path (NO timestamp):
      output/Play/<recipient_slug>/<title_slug>/

    Every Generate overwrites that folder (clears it first).

    Normalized runtime layout:
      <play_dir>/
        index.html
        styles.css
        script.js
        gallery/
          pages/
          controls/
          message/
          sounds/
    """
    pr = Path(project_root)
    ensure_output_dirs(pr)

    rec_slug = safe_slug(recipient, fallback="friend")
    ttl_slug = safe_slug(title, fallback="letter")

    play_dir = pr / OUTPUT_PLAY_DIR / rec_slug / ttl_slug
    play_dir.mkdir(parents=True, exist_ok=True)

    # Overwrite behavior: clear previous build contents
    _safe_clear_dir_contents(play_dir)

    play_gallery = play_dir / GALLERY_DIR
    play_pages = play_gallery / PAGES_DIR
    play_controls = play_gallery / CONTROLS_DIR
    play_message = play_gallery / MESSAGE_DIR
    play_sounds = play_gallery / SOUNDS_DIR
    play_fonts = play_gallery / FONTS_DIR

    play_pages.mkdir(parents=True, exist_ok=True)
    play_controls.mkdir(parents=True, exist_ok=True)
    play_message.mkdir(parents=True, exist_ok=True)
    play_sounds.mkdir(parents=True, exist_ok=True)
    play_fonts.mkdir(parents=True, exist_ok=True)

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
# Validation (canonical SOURCE tree)
# ─────────────────────────────────────────────────────────────────────────────
def validate_required_images(project_root: str | Path) -> List[str]:
    base = Path(project_root) / USER_PAGES_DIR
    return [f for f in REQUIRED_SLIDES if not (base / f).is_file()]


def validate_controls(project_root: str | Path) -> List[str]:
    base = Path(project_root) / USER_CONTROLS_DIR
    return [f for f in CONTROL_FILES if not (base / f).is_file()]


def validate_audio_assets(project_root: str | Path) -> List[str]:
    snd = Path(project_root) / USER_SOUNDS_DIR
    missing: List[str] = []

    if not (snd / GLISS_FILE).is_file():
        missing.append(GLISS_FILE)

    if not (snd / MUSIC_FILE).is_file():
        missing.append(MUSIC_FILE)

    for i in range(1, FLIP_COUNT + 1):
        name = f"{FLIP_PREFIX}{i}.mp3"
        if not (snd / name).is_file():
            missing.append(name)

    return missing


class Config:
    def __init__(self, project_root: str | Path = _PROJECT_ROOT, **_ignored):
        self.project_root = Path(project_root)
        self._settings = _load_settings(self.project_root)

    def __getattr__(self, key):
        if key in self._settings:
            return self._settings[key]
        raise AttributeError(f"No config key named '{key}'")

    def __getitem__(self, key):
        return self._settings.get(key)

    def as_dict(self) -> Dict:
        return dict(self._settings)


__all__ = [
    # settings
    "SETTINGS_FILE",
    "DEFAULT_VOLUME",
    "DEFAULT_AUDIO",
    "STARTING_VOLUME",
    "LAST_AUDIO",
    "CONFIG_DICT",
    # runtime folder names
    "GALLERY_DIR",
    "PAGES_DIR",
    "CONTROLS_DIR",
    "SOUNDS_DIR",
    "MESSAGE_DIR",
    "FONTS_DIR",
    "ICONS_DIR",
    # canonical source tree
    "GALLERY_USER_DIR",
    "USER_PAGES_DIR",
    "USER_CONTROLS_DIR",
    "USER_MESSAGE_DIR",
    "USER_SOUNDS_DIR",
    "REQUIRED_SLIDES",
    "CONTROL_FILES",
    "MESSAGE_HTML_FILE",
    "MESSAGE_IMAGE_FILE",
    # audio
    "GLISS_FILE",
    "MUSIC_FILE",
    "FLIP_PREFIX",
    "FLIP_COUNT",
    "MAX_AUDIO_MB",
    # output
    "OUTPUT_DIR",
    "OUTPUT_PLAY_DIR",
    "OUTPUT_FILE_DIR",
    # helpers
    "ensure_output_dirs",
    "ensure_gallery_dirs",
    "safe_slug",
    "display_name",
    "plan_build",
    "BuildPaths",
    # validation
    "validate_required_images",
    "validate_controls",
    "validate_audio_assets",
    # wrapper
    "Config",
]
