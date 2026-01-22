# ===============================
# File: Generate.py  (back-compat + fresh settings)
# Purpose:
#   - Keep legacy APIs used by Forge_Tab: prepare_gallery_dir, generate_gallery
#   - Fresh-read settings.json at build time (no stale STARTING_VOLUME)
#   - Inject {{STARTING_VOLUME}} (0–100) and {{INITIAL_VOLUME}} (0–1)
#   - Prefer Sound Archive's current.json audio if available (fallback to last_audio/default)
# ===============================

from __future__ import annotations

import os
import json
import shutil
import webbrowser
from pathlib import Path
from typing import Optional

from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from config import (
    SETTINGS_FILE,
    GALLERY_DIR, SOUNDS_DIR,
    MESSAGE_HTML_FILE,
    DEFAULT_VOLUME, DEFAULT_AUDIO,
    OUTPUT_PLAY_DIR,
    ensure_output_dirs,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers (I/O, settings, paths)
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Windows-safe atomic text write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)

def _load_settings(project_root: Path) -> dict:
    """Load settings.json (fresh, tolerant)."""
    fp = project_root / SETTINGS_FILE
    try:
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    except Exception:
        return {}

def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a folder tree, creating subdirs as needed; ignore if src missing."""
    if not src.exists():
        return
    for root, _dirs, files in os.walk(src):
        root_p = Path(root)
        rel = root_p.relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(root_p / f, (dst / rel / f))

def _posix(*parts: str | Path) -> str:
    """Join path parts and return with forward slashes for web use."""
    return Path(*parts).as_posix()

def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# Audio resolution (Sound Archive aware)
# ─────────────────────────────────────────────────────────────────────────────

def _read_archive_current_rel(project_root: Path) -> Optional[str]:
    """
    If gallery/sounds/current.json exists and points to a valid file,
    return that relative web path (posix). Supports either:
      - "gallery/sounds/archive/NAME.mp3"
      - "gallery/sounds/archive/processed/NAME.mp3"
    """
    manifest = project_root / GALLERY_DIR / SOUNDS_DIR / "current.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rel = data.get("current_rel")
        if not rel:
            return None
        rel_path = project_root / rel
        if rel_path.exists() and rel_path.is_file():
            # Ensure it resolves under gallery/… for the web bundle
            # If someone wrote an absolute by mistake, fall back.
            if str(rel).replace("\\", "/").startswith(f"{GALLERY_DIR}/"):
                return rel.replace("\\", "/")
    except Exception:
        pass
    return None

def _guess_audio_web_path(project_root: Path, settings: dict) -> str:
    """
    Decide the <source src="..."> path for the audio tag, relative to index.html:
      1) Use Sound Archive current.json's 'current_rel' if valid.
      2) Else use settings['last_audio'] if present (file name OR subpath under sounds/).
      3) Else use DEFAULT_AUDIO.
    Returns a posix-style relative path like:
       "gallery/sounds/music.mp3"  or  "gallery/sounds/archive/processed/foo.mp3"
    """
    # 1) Archive manifest
    rel = _read_archive_current_rel(project_root)
    if rel:
        return rel

    # 2) last_audio from settings (can be "music.mp3" or "archive/foo.mp3")
    last_audio = (settings.get("last_audio") or "").strip().replace("\\", "/")
    if last_audio:
        # If already includes "gallery/", trust it directly.
        if last_audio.startswith(f"{GALLERY_DIR}/"):
            candidate = project_root / last_audio
            if candidate.exists():
                return last_audio
        # If looks like a subpath under sounds/ (e.g., "archive/foo.mp3")
        if "/" in last_audio and not last_audio.startswith("/"):
            candidate = project_root / GALLERY_DIR / SOUNDS_DIR / last_audio
            if candidate.exists():
                return _posix(GALLERY_DIR, SOUNDS_DIR, last_audio)
        # Treat as a filename in sounds/
        candidate = project_root / GALLERY_DIR / SOUNDS_DIR / last_audio
        if candidate.exists():
            return _posix(GALLERY_DIR, SOUNDS_DIR, last_audio)

    # 3) DEFAULT_AUDIO fallback
    return _posix(GALLERY_DIR, SOUNDS_DIR, DEFAULT_AUDIO)

def _mime_from_ext(path_like: str) -> str:
    ext = Path(path_like).suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
        ".m4a": "audio/aac",
        ".flac": "audio/flac",
    }.get(ext, "audio/mpeg")

def _audio_html(project_root: Path, settings: dict) -> str:
    """
    Build the <audio> tag using resolved source path and appropriate MIME.
    """
    src = _guess_audio_web_path(project_root, settings)
    mime = _mime_from_ext(src)
    return (
        f'<audio id="bg-music" autoplay loop>'
        f'<source src="{src}" type="{mime}">'
        f'Your browser does not support the audio tag.'
        f'</audio>'
    )

# ─────────────────────────────────────────────────────────────────────────────
# Output root helpers
# ─────────────────────────────────────────────────────────────────────────────

def _play_root(project_root: Path, recipient: str) -> Path:
    return project_root / OUTPUT_PLAY_DIR / f"Letter for {recipient}"

# ─────────────────────────────────────────────────────────────────────────────
# Back-compat API expected by Forge_Tab.py
# ─────────────────────────────────────────────────────────────────────────────

def prepare_gallery_dir(project_root: str) -> Path:
    """
    Idempotently copies project /gallery into:
      output/Play/Letter for <recipient>/gallery
    Returns the destination gallery path.
    """
    pr = Path(project_root)
    settings = _load_settings(pr)
    recipient = (settings.get("recipient_name") or "Friend").strip() or "Friend"
    dest = _play_root(pr, recipient) / GALLERY_DIR
    dest.mkdir(parents=True, exist_ok=True)
    _copy_tree(pr / GALLERY_DIR, dest)
    return dest

def generate_gallery(
    project_root: str,
    message_html: Optional[str] = None,
    open_in_browser: bool = False
) -> Path:
    """
    Builds the browsable bundle at:
      output/Play/Letter for <recipient>/{index.html, styles.css, script.js, gallery/...}
    Returns the play folder path.
    """
    pr = Path(project_root)
    ensure_output_dirs(str(pr))  # make sure output dirs exist now

    # Fresh settings (no stale volume)
    settings   = _load_settings(pr)
    recipient  = (settings.get("recipient_name") or "Friend").strip() or "Friend"
    title      = (settings.get("recipient_title") or f"Letter for {recipient}").strip() or f"Letter for {recipient}"

    # Volume: 0..100 for slider; INITIAL is 0..1 float
    try:
        starting_vol = int(settings.get("starting_volume", DEFAULT_VOLUME))
    except Exception:
        starting_vol = DEFAULT_VOLUME
    starting_vol = max(0, min(100, starting_vol))
    initial_vol  = max(0.0, min(1.0, starting_vol / 100.0))

    play_dir = _play_root(pr, recipient)
    play_dir.mkdir(parents=True, exist_ok=True)

    # 1) Copy gallery into the Play bundle
    dst_gallery = play_dir / GALLERY_DIR
    dst_gallery.mkdir(parents=True, exist_ok=True)
    _copy_tree(pr / GALLERY_DIR, dst_gallery)

    # 2) Write CSS/JS
    _atomic_write_text(play_dir / "styles.css", TEMPLATE_CSS)
    _atomic_write_text(play_dir / "script.js",  TEMPLATE_JS)

    # 3) Load message HTML if not provided
    if message_html is None:
        msg_fp = pr / MESSAGE_HTML_FILE
        message_html = _read_text_safe(msg_fp)

    # 4) Compose index.html with fresh STARTING/INITIAL volume & resolved audio
    html = (
        TEMPLATE_HTML
        .replace("{{TITLE}}", title)
        .replace("{{MESSAGE_HTML}}", message_html or "")
        .replace("{{AUDIO_HTML}}", _audio_html(pr, settings))
        .replace("{{STARTING_VOLUME}}", str(starting_vol))          # keep as 0–100
        .replace("{{INITIAL_VOLUME}}", f"{initial_vol:.3f}")        # precise 0–1
    )

    _atomic_write_text(play_dir / "index.html", html)

    if open_in_browser:
        try:
            webbrowser.open((play_dir / "index.html").as_uri())
        except Exception:
            pass

    return play_dir