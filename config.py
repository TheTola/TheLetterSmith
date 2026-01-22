#!/usr/bin/env python3
"""
Central configuration for the eLetter project.

- Backward compatible constants used across the app (Image_tab, Message_tab,
  sound_tab, Forge_Tab, Transmuter, etc.)
- Loads settings.json (with defaults), writes back missing keys.
- Standardized /output layout with helpers to create per-build work folders.
- Safe filename/slug utilities and asset validation helpers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# File and Folder Names (BC: used by existing modules)
# ─────────────────────────────────────────────────────────────────────────────
SETTINGS_FILE        = "settings.json"
GALLERY_DIR          = "gallery"
ICONS_DIR            = "icons"
SOUNDS_DIR           = "sounds"
MESSAGE_HTML_FILE    = "message.html"
MESSAGE_IMAGE_FILE   = "message.png"

# Standardized output locations
OUTPUT_DIR       = "output"
OUTPUT_PLAY_DIR  = os.path.join(OUTPUT_DIR, "Play")   # browsable bundle
OUTPUT_FILE_DIR  = os.path.join(OUTPUT_DIR, "File")   # single-file HTML
OUTPUT_ZIP_DIR   = os.path.join(OUTPUT_DIR, "Zip")    # packaged zips

# ─────────────────────────────────────────────────────────────────────────────
# Required Images & Audio (BC constants)
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_SLIDES = ["cover.png", "letter.png", "wall.png", "back.png"]

GLISS_FILE     = "glissando.mp3"
FLIP_PREFIX    = "flip"
FLIP_COUNT     = 10
MUSIC_FILE     = "music.mp3"
SOUND_GIF      = "sound.gif"
MAX_AUDIO_MB   = 15

# Template defaults
DEFAULT_VOLUME = 31    # percent (0–100), used if not in settings.json
DEFAULT_AUDIO  = MUSIC_FILE

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers for settings
# ─────────────────────────────────────────────────────────────────────────────
def _project_root() -> Path:
    return Path(__file__).resolve().parent

def _load_settings(project_root: str | Path) -> Dict:
    """
    Load settings.json (if present), inject defaults for missing keys,
    and write back to disk if we added anything.
    """
    pr = Path(project_root)
    path = pr / SETTINGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        data = {}

    updated = False
    if "starting_volume" not in data:
        data["starting_volume"] = DEFAULT_VOLUME
        updated = True
    if "last_audio" not in data:
        data["last_audio"] = DEFAULT_AUDIO
        updated = True

    if updated:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            # Non-fatal: continue with in-memory defaults
            pass

    return data

# Module init
_PROJECT_ROOT = _project_root()
_SETTINGS     = _load_settings(_PROJECT_ROOT)

# Exposed configuration values (BC names)
STARTING_VOLUME = _SETTINGS.get("starting_volume", DEFAULT_VOLUME)
LAST_AUDIO      = _SETTINGS.get("last_audio", DEFAULT_AUDIO)

# Optional: access the full config dict
CONFIG_DICT = dict(_SETTINGS)

# ─────────────────────────────────────────────────────────────────────────────
# Output & build planning
# ─────────────────────────────────────────────────────────────────────────────
def ensure_output_dirs(project_root: str | Path) -> None:
    """
    Make sure /output/Play, /output/File, /output/Zip exist.
    Safe to call repeatedly.
    """
    pr = Path(project_root)
    for sub in (OUTPUT_PLAY_DIR, OUTPUT_FILE_DIR, OUTPUT_ZIP_DIR):
        (pr / sub).mkdir(parents=True, exist_ok=True)

def ensure_gallery_dirs(root: str | Path) -> None:
    """
    Ensure the working gallery structure exists under `root`.
    (Useful both at the project root and inside a per-build Play folder.)
    """
    r = Path(root)
    (r / GALLERY_DIR / ICONS_DIR ).mkdir(parents=True, exist_ok=True)
    (r / GALLERY_DIR / SOUNDS_DIR).mkdir(parents=True, exist_ok=True)

def safe_slug(text: str | None, fallback: str = "eLetter") -> str:
    """
    Convert arbitrary text to a filesystem-safe slug.
    """
    if not text:
        return fallback
    # Normalize whitespace, strip, lower
    t = re.sub(r"\s+", " ", text).strip().lower()
    # Replace disallowed chars with hyphens
    t = re.sub(r"[^a-z0-9\-_. ]+", "", t)
    t = t.replace(" ", "-")
    return t or fallback

def display_name(text: str | None, fallback: str = "eLetter") -> str:
    """
    Human-readable name for files like 'A Letter for {Name}.html'
    """
    if not text:
        return fallback
    # Collapse whitespace and strip control characters
    name = re.sub(r"\s+", " ", text).strip()
    return name or fallback

def _timestamp() -> str:
    # yyyy-mm-dd_hh-mm-ss for readability/sorting
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

@dataclass(frozen=True)
class BuildPaths:
    """
    Describes where to build/run a single output.
    """
    # Workspace for the Play build (everything self-contained inside here)
    play_dir: Path
    play_gallery_dir: Path
    play_icons_dir: Path
    play_sounds_dir: Path

    # Final single-file HTML output
    single_file_html: Path

    # ZIP output (when packaging)
    zip_path: Path

def plan_build(project_root: str | Path, recipient: str | None = None, label: str | None = None) -> BuildPaths:
    """
    Create a brand-new Play workspace folder under /output/Play and
    return paths for this build. You should write *all* generated files
    (index.html, styles.css, script.js, gallery/...) inside `play_dir`.

    Example layout:
      output/
        Play/
          angel-hill-2025-08-11_14-03-22/
            index.html
            styles.css
            script.js
            gallery/...
        File/
          A Letter for Angel Hill.html
        Zip/
          Letter for Angel Hill.zip
    """
    pr = Path(project_root)
    ensure_output_dirs(pr)

    nice_name = display_name(recipient or label, fallback="eLetter")
    slug      = safe_slug(recipient or label, fallback="eletter")
    stamp     = _timestamp()

    # Fresh workspace for this run
    play_dir = pr / OUTPUT_PLAY_DIR / f"{slug}-{stamp}"
    play_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the internal gallery structure lives *inside* the new play_dir
    play_gallery = play_dir / GALLERY_DIR
    play_icons   = play_gallery / ICONS_DIR
    play_sounds  = play_gallery / SOUNDS_DIR
    play_icons.mkdir(parents=True, exist_ok=True)
    play_sounds.mkdir(parents=True, exist_ok=True)

    # Pre-resolve final outputs (outside the workspace, but stable targets)
    single_file_html = pr / OUTPUT_FILE_DIR / f"A Letter for {nice_name}.html"
    zip_path         = pr / OUTPUT_ZIP_DIR  / f"Letter for {nice_name}.zip"

    return BuildPaths(
        play_dir=play_dir,
        play_gallery_dir=play_gallery,
        play_icons_dir=play_icons,
        play_sounds_dir=play_sounds,
        single_file_html=single_file_html,
        zip_path=zip_path,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers (optional but handy in Forge_Tab before building)
# ─────────────────────────────────────────────────────────────────────────────
def validate_required_images(root: str | Path) -> List[str]:
    """
    Return a list of missing required slide image filenames
    under `<root>/gallery/`.
    """
    base = Path(root) / GALLERY_DIR
    missing = []
    for fname in REQUIRED_SLIDES:
        if not (base / fname).is_file():
            missing.append(fname)
    return missing

def validate_audio_assets(root: str | Path) -> List[str]:
    """
    Check for glissando and flip sounds under `<root>/gallery/sounds/`.
    Returns a list of missing filenames (empty if all good).
    """
    snd = Path(root) / GALLERY_DIR / SOUNDS_DIR
    missing = []
    if not (snd / GLISS_FILE).is_file():
        missing.append(GLISS_FILE)
    for i in range(1, FLIP_COUNT + 1):
        name = f"{FLIP_PREFIX}{i}.mp3"
        if not (snd / name).is_file():
            missing.append(name)
    return missing

# ─────────────────────────────────────────────────────────────────────────────
# Optional object-style access
# ─────────────────────────────────────────────────────────────────────────────
class Config:
    """
    Object-based dynamic config that reads/writes settings.json
    at the given project_root.
    """
    def __init__(self, project_root: str | Path = _PROJECT_ROOT):
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

# ─────────────────────────────────────────────────────────────────────────────
# What this module exports
# ─────────────────────────────────────────────────────────────────────────────
__all__ = [
    # constants (BC)
    "SETTINGS_FILE", "GALLERY_DIR", "ICONS_DIR", "SOUNDS_DIR",
    "MESSAGE_HTML_FILE", "MESSAGE_IMAGE_FILE",
    "OUTPUT_DIR", "OUTPUT_PLAY_DIR", "OUTPUT_FILE_DIR", "OUTPUT_ZIP_DIR",
    "REQUIRED_SLIDES",
    "GLISS_FILE", "FLIP_PREFIX", "FLIP_COUNT", "MUSIC_FILE", "SOUND_GIF", "MAX_AUDIO_MB",
    "DEFAULT_VOLUME", "DEFAULT_AUDIO",
    "STARTING_VOLUME", "LAST_AUDIO", "CONFIG_DICT",
    # helpers
    "ensure_output_dirs", "ensure_gallery_dirs",
    "safe_slug", "display_name",
    "plan_build", "BuildPaths",
    "validate_required_images", "validate_audio_assets",
    # object access
    "Config",
]
