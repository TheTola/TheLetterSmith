#!/usr/bin/env python3
# ===============================
# File: Generate.py
# Purpose:
#   Build the Play viewer bundle at:
#     output/Play/<recipient>/<title>/
#       index.html, styles.css, script.js
#       gallery/
#         pages/      (cover/letter/wall/back)
#         controls/   (npage/ppage/cleft/cright/R_cleft/R_cright/volon/voloff/showmessageicon)
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
import time
import webbrowser
import html as html_lib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable

from audio_export import _export_apple_safe_mp3
from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from curtain_color import (
    FALLBACK_CURTAIN_RGB,
    extract_deep_dominant_color_from_images,
    write_tinted_curtain_image,
)
from message_html import (
    message_html_has_content,
    normalize_message_fragment,
    read_text_normalized,
)
from config import (
    SETTINGS_FILE,
    PUBLISHED_PAGE_URL_KEY,
    PLAY_METADATA_FILE,
    DEFAULT_VOLUME,
    STARTING_VOLUME,
    CURTAIN_STYLE_KEY,
    CURTAIN_STYLE_WHITE,
    CURTAIN_STYLE_AVERAGE,
    CURTAIN_STYLE_COMPLEMENTARY,
    DEFAULT_CURTAIN_STYLE,
    VALID_CURTAIN_STYLES,
    ensure_output_dirs,
    plan_build,
    validate_required_images,
    validate_controls,
    USER_PAGES_DIR,
    USER_CONTROLS_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
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
CURTAIN_FILES = {"cleft.png", "cright.png"}
CURTAIN_ANALYSIS_PAGE_ORDER = ("wall.png", "cover.png", "letter.png", "back.png")


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


def _curtain_style_from_settings(settings: dict) -> str:
    style = str(settings.get(CURTAIN_STYLE_KEY, DEFAULT_CURTAIN_STYLE)).strip().lower()
    aliases = {
        "white": CURTAIN_STYLE_WHITE,
        "pure white": CURTAIN_STYLE_WHITE,
        "pure_white": CURTAIN_STYLE_WHITE,
        "blank": CURTAIN_STYLE_WHITE,
        "original": CURTAIN_STYLE_WHITE,
        "average": CURTAIN_STYLE_AVERAGE,
        "average color": CURTAIN_STYLE_AVERAGE,
        "average_color": CURTAIN_STYLE_AVERAGE,
        "common": CURTAIN_STYLE_AVERAGE,
        "common color": CURTAIN_STYLE_AVERAGE,
        "complementary": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary average": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary average color": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary_average_color": CURTAIN_STYLE_COMPLEMENTARY,
    }
    style = aliases.get(style, style)
    return style if style in VALID_CURTAIN_STYLES else DEFAULT_CURTAIN_STYLE


def _curtain_analysis_paths(project_root: Path) -> list[Path]:
    pages_dir = project_root / USER_PAGES_DIR
    return [pages_dir / name for name in CURTAIN_ANALYSIS_PAGE_ORDER]


def _curtain_rgb_for_style(project_root: Path, settings: dict) -> tuple[str, tuple[int, int, int]]:
    style = _curtain_style_from_settings(settings)
    if style == CURTAIN_STYLE_WHITE:
        return style, FALLBACK_CURTAIN_RGB

    hue_shift = 0.5 if style == CURTAIN_STYLE_COMPLEMENTARY else 0.0
    rgb = extract_deep_dominant_color_from_images(_curtain_analysis_paths(project_root), hue_shift=hue_shift)
    return style, rgb


def _copy_control_files(
    src_dir: Path,
    dst_dir: Path,
    names: list[str],
    *,
    curtain_rgb: tuple[int, int, int],
) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = src_dir / name
        dst = dst_dir / name
        if name in CURTAIN_FILES:
            write_tinted_curtain_image(src, dst, curtain_rgb)
        else:
            _atomic_copy_file(src, dst)


def _write_play_metadata(
    play_dir: Path,
    *,
    settings: dict,
    recipient: str,
    title: str,
    curtain_style: str,
    curtain_rgb: tuple[int, int, int],
) -> None:
    metadata = {
        "recipient_name": recipient,
        "recipient_title": title,
        PUBLISHED_PAGE_URL_KEY: str(settings.get(PUBLISHED_PAGE_URL_KEY, "")).strip(),
        CURTAIN_STYLE_KEY: curtain_style,
        "curtain_rgb": list(curtain_rgb),
    }
    _atomic_write_text(play_dir / PLAY_METADATA_FILE, json.dumps(metadata, indent=2))


MESSAGE_OVERLAY_PRESET_KEY = "message_overlay_preset"
MESSAGE_OVERLAY_OPACITY_KEY = "message_overlay_opacity"
DEFAULT_MESSAGE_OVERLAY_PRESET = "paper"
DEFAULT_MESSAGE_OVERLAY_OPACITY = 68
MESSAGE_OVERLAY_PRESETS: dict[str, tuple[tuple[int, int, int], str]] = {
    "black": ((0, 0, 0), "#ffffff"),
    "white": ((255, 255, 255), "#221710"),
    "paper": ((245, 235, 210), "#221710"),
    "clear": ((255, 255, 255), "#221710"),
}


def _message_overlay_preset_from_settings(settings: dict) -> str:
    preset = str(settings.get(MESSAGE_OVERLAY_PRESET_KEY, DEFAULT_MESSAGE_OVERLAY_PRESET)).strip().lower()
    return preset if preset in MESSAGE_OVERLAY_PRESETS else DEFAULT_MESSAGE_OVERLAY_PRESET


def _message_overlay_style_from_settings(settings: dict) -> str:
    preset = _message_overlay_preset_from_settings(settings)

    try:
        opacity = int(settings.get(MESSAGE_OVERLAY_OPACITY_KEY, DEFAULT_MESSAGE_OVERLAY_OPACITY))
    except Exception:
        opacity = DEFAULT_MESSAGE_OVERLAY_OPACITY
    opacity = max(0, min(100, opacity))
    if preset == "clear":
        opacity = 0

    (r, g, b), ink = MESSAGE_OVERLAY_PRESETS[preset]
    alpha = max(0.0, min(1.0, opacity / 100.0))
    return (
        f"--message-overlay-rgb:{r},{g},{b};"
        f"--message-overlay-opacity:{alpha:.3f};"
        f"--message-ink:{ink};"
        f"--wall-fade-ms:900ms;"
    )


def _message_overlay_html(message_html: str, overlay_style: str) -> str:
    return (
        '<button id="close-text" class="hud-button hud-button-close" type="button" '
        'title="Close Text" aria-label="Close message" aria-controls="textWall">&times;</button>\n'
        f'      <div class="text-wall" id="textWall" role="dialog" aria-modal="false" '
        f'aria-label="Message text" aria-hidden="true" tabindex="-1" style="{overlay_style}">\n'
        f'        <div class="text-wall-content" id="textWallContent">{message_html}</div>\n'
        f'      </div>'
    )


def _message_button_html() -> str:
    return (
        '<button id="open-text" class="hud-button hud-button-icon" type="button" '
        'title="Show Message" aria-label="Show message" aria-controls="textWall" '
        'aria-expanded="false" aria-hidden="true">\n'
        '      <img src="gallery/controls/showmessageicon.png" alt="" aria-hidden="true" decoding="async">\n'
        '    </button>'
    )


def _validate_template_placeholders() -> None:
    """
    Fail fast if Template.py changes and placeholders drift.
    """
    required = (
        "{{TITLE}}",
        "{{BUILD_ID}}",
        "{{INITIAL_VOLUME}}",
        "{{HAS_MESSAGE}}",
        "{{MESSAGE_OVERLAY_HTML}}",
        "{{MESSAGE_BUTTON_HTML}}",
        "{{MESSAGE_OVERLAY_PRESET}}",
    )
    missing = [k for k in required if k not in TEMPLATE_HTML]
    if missing:
        raise TemplateDriftError(
            "Template drift detected: TEMPLATE_HTML is missing placeholder(s): "
            + ", ".join(missing)
        )

    required_js = ("{{BUILD_ID}}",)
    missing_js = [k for k in required_js if k not in TEMPLATE_JS]
    if missing_js:
        raise TemplateDriftError(
            "Template drift detected: TEMPLATE_JS is missing placeholder(s): "
            + ", ".join(missing_js)
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

        try:
            _export_apple_safe_mp3(src, sounds_dst / name)
        except Exception as exc:
            raise RuntimeError(f"Failed to prepare app SFX for Play build ({name}): {exc}") from exc

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

    # User music is optional. Image-only letters should still build.
    user_sounds = pr / USER_SOUNDS_DIR
    user_music = user_sounds / MUSIC_FILE
    has_user_music = user_music.is_file()
    build_id = str(int(time.time()))

    # Settings drive deterministic output path
    settings = _load_settings(pr)
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    starting_vol = _starting_volume_from_settings(settings)
    curtain_style, curtain_rgb = _curtain_rgb_for_style(pr, settings)

    # Deterministic Play folder (NO timestamp; overwrites same location)
    bp = plan_build(pr, recipient=recipient, title=title)

    # Runtime destinations
    pages_dst = bp.play_pages_dir
    controls_dst = bp.play_controls_dir
    message_dst = bp.play_message_dir
    sounds_dst = bp.play_sounds_dir

    # Source dirs (user)
    pages_src = pr / USER_PAGES_DIR
    controls_src = pr / USER_CONTROLS_DIR
    msg_html_src = pr / MESSAGE_HTML_FILE
    msg_png_src = pr / MESSAGE_IMAGE_FILE

    # Copy pages/controls (required)
    _copy_required_files(pages_src, pages_dst, REQUIRED_SLIDES)
    _copy_control_files(controls_src, controls_dst, CONTROL_FILES, curtain_rgb=curtain_rgb)

    # Copy message files (optional)
    if msg_html_src.is_file():
        _atomic_copy_file(msg_html_src, message_dst / "message.html")
    if msg_png_src.is_file():
        _atomic_copy_file(msg_png_src, message_dst / "message.png")

    # Export sounds:
    # - optional user music
    if has_user_music:
        try:
            _export_apple_safe_mp3(user_music, sounds_dst / MUSIC_FILE)
        except Exception as exc:
            raise RuntimeError(f"Failed to prepare user music for Play build: {exc}") from exc

    # - seed/export SFX into the build (app preferred, optional user fallback)
    _seed_sfx_into_build(
        project_root=pr,
        sounds_dst=sounds_dst,
        seed_sfx=seed_sfx,
        allow_user_sfx_fallback=allow_user_sfx_fallback,
    )

    # Write CSS/JS
    _atomic_write_text(bp.play_dir / "styles.css", TEMPLATE_CSS)
    _atomic_write_text(bp.play_dir / "script.js", TEMPLATE_JS.replace("{{BUILD_ID}}", build_id))

    # Message HTML injection (prefer passed string, else disk)
    if message_html is None:
        message_html = _read_text_safe(msg_html_src)

    has_message_html = message_html_has_content(message_html or "")
    message_html = normalize_message_fragment(message_html or "") if has_message_html else ""
    overlay_style = _message_overlay_style_from_settings(settings)
    has_message_js = "true" if has_message_html else "false"

    html = (
        TEMPLATE_HTML
        .replace("{{TITLE}}", html_lib.escape(title, quote=False))
        .replace("{{BUILD_ID}}", build_id)
        .replace("{{HAS_MESSAGE}}", has_message_js)
        .replace("{{MESSAGE_OVERLAY_HTML}}", _message_overlay_html(message_html, overlay_style) if has_message_html else "")
        .replace("{{MESSAGE_BUTTON_HTML}}", _message_button_html() if has_message_html else "")
        .replace("{{INITIAL_VOLUME}}", str(starting_vol))
        .replace("{{MESSAGE_OVERLAY_PRESET}}", _message_overlay_preset_from_settings(settings))
    )

    if not has_user_music:
        html = html.replace('data-has-user-music="true"', 'data-has-user-music="false"')

    _atomic_write_text(bp.play_dir / "index.html", html)
    _write_play_metadata(
        bp.play_dir,
        settings=settings,
        recipient=recipient,
        title=title,
        curtain_style=curtain_style,
        curtain_rgb=curtain_rgb,
    )

    if open_in_browser:
        try:
            webbrowser.open((bp.play_dir / "index.html").as_uri())
        except Exception:
            pass

    return bp.play_dir
