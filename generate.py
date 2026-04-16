#!/usr/bin/env python3
# ===============================
# File: Generate.py
# Purpose:
#   Build the Play viewer bundle at:
#     output/Play/<recipient>/<title>/
#       index.html, styles.css, script.js
#       gallery/
#         pages/      (cover/letter/wall/back)
#         controls/   (npage/ppage/cleft/cright/volon/voloff/showmessageicon)
#         message/    (message.html, message.png optional)
#         sounds/     (music, glissando, flip1..flip10)
#
# Source-of-truth on disk:
#   User content:
#     gallery/user/pages
#     gallery/user/card/controls
#     gallery/user/message
#     gallery/user/sounds/music.mp3        (user-selected music)
#   App-owned SFX (immutable):
#     gallery/app/sounds/glissando.mp3
#     gallery/app/sounds/flip1..flip10.mp3
#
# Improvements applied:
#   1) Auto-seed SFX into the build (app path preferred; fallback to user sounds if enabled)
#   2) Atomic copy on Windows (copy -> tmp -> os.replace) for robustness
#   3) Strict template placeholder validation (fail fast if Template drift occurs)
#
# NOTE:
# - Back-compat API removed (no prepare_gallery_dir / generate_gallery legacy signature).
# - Forge_Tab.py will be updated separately to call generate_play_bundle().
# ===============================

from __future__ import annotations

import json
import os
import shutil
import webbrowser
import html as html_lib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Iterable

from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from message_html import (
    extract_font_families,
    normalize_message_fragment,
    read_text_normalized,
    rewrite_font_families,
)
from config import (
    SETTINGS_FILE,
    DEFAULT_VOLUME,
    STARTING_VOLUME,
    ensure_output_dirs,
    plan_build,
    validate_required_images,
    validate_controls,
    USER_PAGES_DIR,
    USER_CONTROLS_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
    FONTS_DIR,
    REQUIRED_SLIDES,
    CONTROL_FILES,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
    MUSIC_FILE,
    GLISS_FILE,
    FLIP_PREFIX,
    FLIP_COUNT,
)

# App-owned SFX live here (relative to project root)
APP_SOUNDS_DIR = Path("gallery") / "app" / "sounds"
FONT_REGISTRY_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")
FONT_EXPORT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2", ".ttc"}
FONT_DISPLAY_NAME_SUFFIXES = (
    "",
    " Regular",
    " Roman",
    " Italic",
    " Oblique",
    " Bold",
    " Bold Italic",
    " Bold Oblique",
)
FONT_STYLE_TOKENS = {
    "thin",
    "extralight",
    "ultralight",
    "light",
    "semilight",
    "demilight",
    "book",
    "normal",
    "regular",
    "roman",
    "medium",
    "demibold",
    "semibold",
    "bold",
    "extrabold",
    "ultrabold",
    "black",
    "heavy",
    "italic",
    "oblique",
}


@dataclass(frozen=True)
class ResolvedFontFace:
    display_name: str
    source_path: Path
    weight: int
    style: str


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────
class TemplateDriftError(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """
    Windows-safe atomic copy:
      - copy to dst.tmp
      - os.replace(tmp, dst)
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"Missing required asset: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")

    # copy2 preserves mtime/metadata; good for deterministic debugging
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _copy_required_files(src_dir: Path, dst_dir: Path, names: list[str]) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        s = src_dir / name
        d = dst_dir / name
        _atomic_copy_file(s, d)


def _read_text_safe(path: Path) -> str:
    try:
        return read_text_normalized(path)
    except Exception:
        return ""


def _normalize_font_display_name(value: str) -> str:
    return re.sub(r"\s+", " ", FONT_REGISTRY_SUFFIX_RE.sub("", (value or "").strip())).strip()


def _font_registry_keys():
    try:
        import winreg
    except Exception:
        return ()

    return (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )


def _font_search_dirs() -> list[Path]:
    dirs: list[Path] = []

    windir = os.environ.get("WINDIR")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    else:
        dirs.append(Path(r"C:\Windows") / "Fonts")

    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")

    return dirs


def _resolve_font_file_path(value: str, search_dirs: list[Path]) -> Optional[Path]:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_file():
        return candidate

    for base in search_dirs:
        probe = base / raw
        if probe.is_file():
            return probe
        probe = base / candidate.name
        if probe.is_file():
            return probe

    return None


@lru_cache(maxsize=1)
def _load_font_registry() -> tuple[tuple[str, Path], ...]:
    entries: list[tuple[str, Path]] = []
    keys = _font_registry_keys()
    if not keys:
        return tuple(entries)

    try:
        import winreg
    except Exception:
        return tuple(entries)

    search_dirs = _font_search_dirs()
    seen: set[tuple[str, str]] = set()

    for root, key_path in keys:
        try:
            key = winreg.OpenKey(root, key_path)
        except OSError:
            continue

        idx = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, idx)
            except OSError:
                break

            idx += 1
            display_name = _normalize_font_display_name(name)
            source_path = _resolve_font_file_path(str(value), search_dirs)
            if not display_name or source_path is None:
                continue

            source_key = (display_name.casefold(), str(source_path).casefold())
            if source_key in seen:
                continue
            seen.add(source_key)
            entries.append((display_name, source_path))

    return tuple(entries)


def _classify_font_face(display_name: str) -> tuple[int, str]:
    name = _normalize_font_display_name(display_name).casefold()
    tokens = set(part for part in re.split(r"[\s-]+", name) if part)

    if "black" in tokens or "heavy" in tokens:
        weight = 900
    elif "extrabold" in tokens or "ultrabold" in tokens:
        weight = 800
    elif "bold" in tokens:
        weight = 700
    elif "demibold" in tokens or "semibold" in tokens:
        weight = 600
    elif "medium" in tokens:
        weight = 500
    elif "light" in tokens or "book" in tokens:
        weight = 300
    elif "thin" in tokens or "extralight" in tokens or "ultralight" in tokens:
        weight = 200
    else:
        weight = 400

    if "italic" in tokens:
        style = "italic"
    elif "oblique" in tokens:
        style = "oblique"
    else:
        style = "normal"

    return weight, style


def _is_style_suffix_only(display_name: str, family: str) -> bool:
    normalized_name = _normalize_font_display_name(display_name)
    normalized_family = _normalize_font_display_name(family)
    if normalized_name.casefold() == normalized_family.casefold():
        return True
    if not normalized_name.lower().startswith(normalized_family.lower() + " "):
        return False

    suffix = normalized_name[len(normalized_family):].strip()
    if not suffix:
        return True

    tokens = [part for part in re.split(r"[\s-]+", suffix.casefold()) if part]
    return bool(tokens) and all(token in FONT_STYLE_TOKENS for token in tokens)


def _resolve_font_faces_for_family(family: str) -> list[ResolvedFontFace]:
    family_name = _normalize_font_display_name(family)
    if not family_name:
        return []

    resolved: dict[tuple[int, str], ResolvedFontFace] = {}
    registry_entries = _load_font_registry()
    registry_map = {name.casefold(): path for name, path in registry_entries}

    def register_face(display_name: str, source_path: Path) -> None:
        ext = source_path.suffix.lower()
        if ext not in FONT_EXPORT_EXTENSIONS:
            return
        weight, style = _classify_font_face(display_name)
        key = (weight, style)
        if key not in resolved:
            resolved[key] = ResolvedFontFace(
                display_name=display_name,
                source_path=source_path,
                weight=weight,
                style=style,
            )

    for suffix in FONT_DISPLAY_NAME_SUFFIXES:
        display_name = f"{family_name}{suffix}"
        source_path = registry_map.get(display_name.casefold())
        if source_path is not None:
            register_face(display_name, source_path)

    for display_name, source_path in registry_entries:
        if _is_style_suffix_only(display_name, family_name):
            register_face(display_name, source_path)

    return sorted(resolved.values(), key=lambda face: (face.weight, face.style, face.display_name.casefold()))


def _font_face_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".ttf":
        return " format('truetype')"
    if ext == ".otf":
        return " format('opentype')"
    if ext == ".woff":
        return " format('woff')"
    if ext == ".woff2":
        return " format('woff2')"
    return ""


def _build_embedded_font_payload(message_html: str, fonts_dst: Path) -> tuple[str, str]:
    families = extract_font_families(message_html)
    if not families:
        return message_html, ""

    family_aliases: dict[str, str] = {}
    css_rules: list[str] = []
    unresolved: list[str] = []

    for family_index, family in enumerate(families, start=1):
        faces = _resolve_font_faces_for_family(family)
        if not faces:
            unresolved.append(family)
            continue

        alias = f"LetterSmithEmbeddedFont{family_index}"
        family_aliases[family] = alias

        for face_index, face in enumerate(faces, start=1):
            out_name = f"font-{family_index}-{face_index}{face.source_path.suffix.lower()}"
            _atomic_copy_file(face.source_path, fonts_dst / out_name)
            css_rules.append(
                "@font-face{"
                f"font-family:'{alias}';"
                f"src:url('gallery/{FONTS_DIR}/{out_name}'){_font_face_format(face.source_path)};"
                f"font-style:{face.style};"
                f"font-weight:{face.weight};"
                "font-display:block;"
                "}"
            )

    if unresolved:
        raise FileNotFoundError(
            "Could not bundle the selected font files for export:\n"
            + "\n".join(f"  - {name}" for name in unresolved)
            + "\nChoose a different font or install the missing font locally before generating."
        )

    return rewrite_font_families(message_html, family_aliases), "\n".join(css_rules)


def _load_settings(project_root: Path) -> dict:
    fp = project_root / SETTINGS_FILE
    try:
        return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    except Exception:
        return {}


def _recipient_from_settings(settings: dict) -> str:
    v = (settings.get("recipient_name") or "Friend").strip()
    return v or "Friend"


def _title_from_settings(settings: dict, recipient: str) -> str:
    v = (settings.get("recipient_title") or f"Letter for {recipient}").strip()
    return v or f"Letter for {recipient}"


def _starting_volume_from_settings(settings: dict) -> int:
    try:
        v = int(settings.get("starting_volume", STARTING_VOLUME if isinstance(STARTING_VOLUME, int) else DEFAULT_VOLUME))
    except Exception:
        v = DEFAULT_VOLUME
    return max(0, min(100, v))


def _require_file(path: Path, *, what: str, expected_rel_hint: Optional[str] = None) -> None:
    if path.is_file():
        return
    hint = f"\nExpected: {expected_rel_hint}" if expected_rel_hint else ""
    raise FileNotFoundError(f"Missing {what}: {path}{hint}")


def _validate_template_placeholders() -> None:
    """
    Fail fast if Template.py changes and placeholders drift.
    """
    required = ("{{TITLE}}", "{{MESSAGE_HTML}}", "{{INITIAL_VOLUME}}")
    missing = [k for k in required if k not in TEMPLATE_HTML]
    if missing:
        raise TemplateDriftError(
            "Template drift detected: TEMPLATE_HTML is missing placeholder(s): "
            + ", ".join(missing)
        )

    # Optional sanity checks (non-fatal, but good to catch major layout change)
    # If you want these hard-fatal too, add them to required checks.
    # e.g., ensure runtime paths exist as expected:
    # "gallery/pages/cover.png" etc.


def _sfx_names() -> list[str]:
    names = [GLISS_FILE]
    for i in range(1, FLIP_COUNT + 1):
        names.append(f"{FLIP_PREFIX}{i}.mp3")
    return names


def _pick_first_existing(candidates: Iterable[Path]) -> Optional[Path]:
    for p in candidates:
        if p.is_file():
            return p
    return None


def _seed_sfx_into_build(
    *,
    project_root: Path,
    sounds_dst: Path,
    seed_sfx: bool,
    allow_user_sfx_fallback: bool,
) -> None:
    """
    Always writes SFX into the Play bundle's gallery/sounds/.

    Priority:
      1) gallery/app/sounds/<sfx>
      2) (optional fallback) gallery/user/sounds/<sfx>   [legacy location]

    This NEVER writes back into gallery/app/sounds.
    """
    if not seed_sfx:
        return

    app_sounds = project_root / APP_SOUNDS_DIR
    user_sounds = project_root / USER_SOUNDS_DIR

    missing: list[str] = []

    for name in _sfx_names():
        src = _pick_first_existing(
            [
                app_sounds / name,
                (user_sounds / name) if allow_user_sfx_fallback else Path("__disabled__"),
            ]
        )
        if src is None:
            missing.append(name)
            continue

        _atomic_copy_file(src, sounds_dst / name)

    if missing:
        # Produce a concrete, actionable error message
        lines = [
            "Missing required SFX for Play build:",
            *[f"  - {m}" for m in missing],
            "",
            "Provide them here (preferred):",
            f"  - {APP_SOUNDS_DIR.as_posix()}/",
        ]
        if allow_user_sfx_fallback:
            lines += [
                "",
                "Fallback (legacy) path was also checked:",
                f"  - {Path(USER_SOUNDS_DIR).as_posix()}/",
            ]
        raise FileNotFoundError("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def generate_play_bundle(
    project_root: str,
    *,
    message_html: Optional[str] = None,
    open_in_browser: bool = False,
    seed_sfx: bool = True,
    allow_user_sfx_fallback: bool = True,
) -> Path:
    """
    Build the Play bundle at:
      output/Play/<recipient>/<title>/
        index.html
        styles.css
        script.js
        gallery/pages/*
        gallery/controls/*
        gallery/message/*
        gallery/sounds/*

    Returns the play folder path.
    """
    pr = Path(project_root)
    ensure_output_dirs(pr)

    # Fail fast if Template drift occurs
    _validate_template_placeholders()

    # Validate SOURCE assets (user content)
    missing_pages = validate_required_images(pr)
    if missing_pages:
        raise FileNotFoundError(
            f"Missing pages in {pr / USER_PAGES_DIR}: {missing_pages}\n"
            f"Expected files: {REQUIRED_SLIDES}"
        )

    missing_controls = validate_controls(pr)
    if missing_controls:
        raise FileNotFoundError(
            f"Missing controls in {pr / USER_CONTROLS_DIR}: {missing_controls}\n"
            f"Expected files: {CONTROL_FILES}"
        )

    # Validate user music exists
    user_sounds = pr / USER_SOUNDS_DIR
    _require_file(
        user_sounds / MUSIC_FILE,
        what="user music (music.mp3)",
        expected_rel_hint=f"{Path(USER_SOUNDS_DIR) / MUSIC_FILE}",
    )

    # Settings drive deterministic output path
    settings = _load_settings(pr)
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    starting_vol = _starting_volume_from_settings(settings)

    # Deterministic Play folder (NO timestamp; overwrites same location)
    bp = plan_build(pr, recipient=recipient, title=title)

    # Runtime destinations
    pages_dst = bp.play_pages_dir
    controls_dst = bp.play_controls_dir
    message_dst = bp.play_message_dir
    sounds_dst = bp.play_sounds_dir
    fonts_dst = bp.play_fonts_dir

    # Source dirs (user)
    pages_src = pr / USER_PAGES_DIR
    controls_src = pr / USER_CONTROLS_DIR
    msg_html_src = pr / MESSAGE_HTML_FILE
    msg_png_src = pr / MESSAGE_IMAGE_FILE

    # Copy pages/controls (required)
    _copy_required_files(pages_src, pages_dst, REQUIRED_SLIDES)
    _copy_required_files(controls_src, controls_dst, CONTROL_FILES)

    # Copy message files (optional)
    if msg_html_src.is_file():
        _atomic_copy_file(msg_html_src, message_dst / "message.html")
    if msg_png_src.is_file():
        _atomic_copy_file(msg_png_src, message_dst / "message.png")

    # Copy sounds:
    # - music from user
    _atomic_copy_file(user_sounds / MUSIC_FILE, sounds_dst / MUSIC_FILE)

    # - seed/copy SFX into the build (app preferred, optional user fallback)
    _seed_sfx_into_build(
        project_root=pr,
        sounds_dst=sounds_dst,
        seed_sfx=seed_sfx,
        allow_user_sfx_fallback=allow_user_sfx_fallback,
    )

    # Message HTML injection (prefer passed string, else disk)
    if message_html is None:
        message_html = _read_text_safe(msg_html_src)
    message_html = normalize_message_fragment(message_html)
    message_html, embedded_font_css = _build_embedded_font_payload(message_html, fonts_dst)
    safe_title = html_lib.escape(title, quote=False)

    # Write CSS/JS
    styles_css = TEMPLATE_CSS if not embedded_font_css else f"{embedded_font_css}\n\n{TEMPLATE_CSS}"
    _atomic_write_text(bp.play_dir / "styles.css", styles_css)
    _atomic_write_text(bp.play_dir / "script.js", TEMPLATE_JS)

    built_html = (
        TEMPLATE_HTML
        .replace("{{TITLE}}", safe_title)
        .replace("{{MESSAGE_HTML}}", message_html)
        .replace("{{INITIAL_VOLUME}}", str(starting_vol))
    )
    _atomic_write_text(bp.play_dir / "index.html", built_html)

    if open_in_browser:
        try:
            webbrowser.open((bp.play_dir / "index.html").as_uri())
        except Exception:
            pass

    return bp.play_dir
