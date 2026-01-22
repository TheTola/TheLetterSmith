# Transmuter.py — Finalization for standalone single-file letter
# - Inlines CSS, JS, images, and audio (everything under gallery/)
# - Rewrites any 'gallery/...' references found in HTML, <style>, and <script>
# - Gliss fallback: tries /gallery/sounds/glissando.mp3 then /gallery/icons/Sounds/Glissando.mp3
# - Outputs: /output/File/A Letter for {NAME}.html

from __future__ import annotations

import base64
import mimetypes
import os
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from config import (
    OUTPUT_FILE_DIR,
    GALLERY_DIR,
)

# ---------- MIME helpers ----------
DEFAULT_MIME: Dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".ogg":  "audio/ogg",
    ".aac":  "audio/aac",
    ".m4a":  "audio/mp4",
}

def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in DEFAULT_MIME:
        return DEFAULT_MIME[ext]
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"

def _b64_uri(fp: Path) -> Optional[str]:
    if not fp.is_file():
        return None
    try:
        data = fp.read_bytes()
        mime = _mime_for(fp)
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None

# ---------- Case-insensitive resolver (Windows-safe, GH Pages-proof) ----------
def _resolve_case_insensitive(base: Path, rel: str) -> Optional[Path]:
    """
    Resolve 'rel' under 'base' by walking each segment case-insensitively.
    Returns a Path if a match exists, else None.
    """
    p = base
    for seg in Path(rel).parts:
        if seg in (".", ""):
            continue
        if not p.exists():
            return None
        if not p.is_dir():
            return None
        # find matching entry ignoring case
        lower = seg.lower()
        match = None
        try:
            for entry in p.iterdir():
                if entry.name.lower() == lower:
                    match = entry
                    break
        except FileNotFoundError:
            return None
        if not match:
            return None
        p = match
    return p

# Known alternates you’re actually using
GLISS_CANONICAL = f"{GALLERY_DIR}/sounds/glissando.mp3"
GLISS_ALT       = f"{GALLERY_DIR}/icons/Sounds/Glissando.mp3"

def _alternate_candidates(rel: str) -> Iterable[str]:
    # Specific hardening for your project
    if rel.replace("\\", "/").lower() == GLISS_CANONICAL.lower():
        yield GLISS_ALT
    # Add more alternates here if you ever move assets around.

# ---------- Inlining core ----------
GALLERY_REF_RE = re.compile(
    r"""(?P<q>['"])               # opening quote
        (?P<path>gallery/[^'")\s]+) # gallery-relative path
        (?P=q)                     # closing quote = same as opening
    """,
    re.IGNORECASE | re.VERBOSE,
)

CSS_URL_RE = re.compile(
    r"""url\(\s*(['"]?)(?P<path>gallery/[^'")\s]+)\1\s*\)""",
    re.IGNORECASE | re.VERBOSE,
)

def _inline_one_path(play_root: Path, rel_path: str) -> Optional[str]:
    """
    Given a gallery-relative path like 'gallery/icons/cleft.png',
    return a data URI if the file is found; else None.
    Tries case-insensitive resolution and known alternates.
    """
    # First: exact/case-insensitive under play_root
    found = _resolve_case_insensitive(play_root, rel_path)
    if found and found.is_file():
        uri = _b64_uri(found)
        if uri:
            return uri

    # Second: try alternates (e.g., gliss under icons/Sounds)
    for alt in _alternate_candidates(rel_path):
        alt_found = _resolve_case_insensitive(play_root, alt)
        if alt_found and alt_found.is_file():
            uri = _b64_uri(alt_found)
            if uri:
                return uri

    # Not found; give up
    return None

def _inline_css(css_text: str, play_root: Path) -> str:
    def repl(m: re.Match) -> str:
        rel = m.group("path")
        uri = _inline_one_path(play_root, rel)
        if uri:
            return f"url('{uri}')"
        return m.group(0)
    return CSS_URL_RE.sub(repl, css_text)

def _inline_gallery_strings(text: str, play_root: Path) -> str:
    """
    Rewrites "gallery/..." and 'gallery/...' string literals to data URIs.
    Works for both HTML attributes and JS strings in <script>.
    """
    def repl(m: re.Match) -> str:
        quote = m.group("q")
        rel   = m.group("path")
        uri = _inline_one_path(play_root, rel)
        if uri:
            return f"{quote}{uri}{quote}"
        return m.group(0)
    return GALLERY_REF_RE.sub(repl, text)

# ---------- Build loader ----------
def _read_text(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return ""

def _play_root(project_root: Path, recipient: str) -> Path:
    """
    The Play build root created by Generate.py is:
      <root>/output/Play/Letter for {recipient}
    We detect the actual folder name by scanning.
    """
    play_base = project_root / "output" / "Play"
    if not play_base.exists():
        return play_base
    # Find the newest "Letter for ..." folder if there are multiple
    candidates = sorted(
        [p for p in play_base.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        # Prefer ones that contain index.html and gallery/
        if (c / "index.html").is_file() and (c / GALLERY_DIR).is_dir():
            return c
    # Fallback: first folder or base
    return candidates[0] if candidates else play_base

def _inject_inline(html: str, css_text: str, js_text: str) -> str:
    # Replace the external <link rel="stylesheet" href="styles.css">
    html = re.sub(
        r'<link[^>]+href=["\']styles\.css["\'][^>]*>',
        f"<style>\n{css_text}\n</style>",
        html,
        flags=re.IGNORECASE,
    )
    # Replace the external <script src="script.js">
    html = re.sub(
        r'<script[^>]+src=["\']script\.js["\'][^>]*>\s*</script>',
        f"<script>\n{js_text}\n</script>",
        html,
        flags=re.IGNORECASE,
    )
    return html

# ---------- Public API ----------
def transmute(project_root: str, recipient_name: str = "Dear Friend") -> str:
    """
    Read the Play bundle, inline everything, and write the single-file HTML
    into /output/File.
    """
    pr = Path(project_root).resolve()

    play_dir = _play_root(pr, recipient_name)
    index_fp = play_dir / "index.html"
    css_fp   = play_dir / "styles.css"
    js_fp    = play_dir / "script.js"

    html = _read_text(index_fp)
    css  = _read_text(css_fp)
    js   = _read_text(js_fp)

    # 1) Inline gallery URLs inside CSS and JS first
    css_in  = _inline_css(css, play_dir)
    css_in  = _inline_gallery_strings(css_in, play_dir)

    js_in   = _inline_gallery_strings(js, play_dir)

    # 2) Inject CSS/JS inline into HTML
    html = _inject_inline(html, css_in, js_in)

    # 3) Inline any remaining gallery references in the HTML itself
    html = _inline_gallery_strings(html, play_dir)

    # 4) Make the title use the chosen name (non-destructive fallback)
    html = html.replace("Letter to Dear Friend", f"Letter to {recipient_name}")

    # 5) Write to /output/File
    out_dir = pr / OUTPUT_FILE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\-. ]+", "_", recipient_name).strip() or "Friend"
    output_path = out_dir / f"A Letter for {safe_name}.html"
    output_path.write_text(html, encoding="utf-8")

    print(f"✅ Final HTML saved to: {output_path}")
    return str(output_path)

# CLI convenience (optional)
if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    name = sys.argv[2] if len(sys.argv) > 2 else "Dear Friend"
    transmute(root, name)
