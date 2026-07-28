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
#         sounds/     (single track or playlist, glissando, flip1..flip10)
#
# Source-of-truth on disk:
#   User content:
#     gallery/user/pages
#     gallery/user/card/controls
#     gallery/user/message
#     gallery/user/sounds/appssong/        (archive + project sound manifest)
#   App-owned SFX (immutable):
#     gallery/app/sounds/glissando.mp3
#     gallery/app/sounds/flip1..flip10.mp3
#
# Improvements applied:
#   1) Seed required SFX exclusively from the app-owned sound directory
#   2) Atomic copy on Windows (copy -> tmp -> os.replace) for robustness
#   3) Strict template placeholder validation (fail fast if Template drift occurs)
#
# NOTE:
# - Back-compat API removed (no prepare_gallery_dir / generate_gallery legacy signature).
# - Forge_Tab.py will be updated separately to call generate_play_bundle().
# ===============================

from __future__ import annotations

import html as _html
import json
import os
import shutil
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional

from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    build_sound_manifest,
    resolve_project_tracks,
    resolve_track_path,
)
from project_state import ensure_project_identity
from transactional_io import PathTransaction
from config import (
    SETTINGS_FILE,
    DEFAULT_VOLUME,
    STARTING_VOLUME,
    OUTPUT_PLAY_DIR,
    ensure_output_dirs,
    plan_build,
    validate_required_images,
    validate_controls,
    USER_PAGES_DIR,
    USER_CONTROLS_DIR,
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


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────
class TemplateDriftError(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _unique_temp_path(destination: Path, suffix: str = ".tmp") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=suffix, dir=str(destination.parent)
    )
    os.close(fd)
    return Path(name)


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    destination = Path(path).resolve()
    tmp = _unique_temp_path(destination)
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_copy_file(src: Path, dst: Path) -> None:
    source = Path(src).resolve()
    destination = Path(dst).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Missing required asset: {source}")

    tmp = _unique_temp_path(destination)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def _copy_required_files(src_dir: Path, dst_dir: Path, names: list[str]) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        s = src_dir / name
        d = dst_dir / name
        _atomic_copy_file(s, d)


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_settings(project_root: Path) -> dict:
    path = project_root / SETTINGS_FILE
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _recipient_from_settings(settings: dict) -> str:
    value = str(settings.get("recipient_name") or "Friend").strip()
    return value or "Friend"


def _title_from_settings(settings: dict, recipient: str) -> str:
    value = str(settings.get("recipient_title") or f"Letter for {recipient}").strip()
    return value or f"Letter for {recipient}"


def _starting_volume_from_settings(settings: dict) -> int:
    try:
        v = int(settings.get("starting_volume", STARTING_VOLUME if isinstance(STARTING_VOLUME, int) else DEFAULT_VOLUME))
    except Exception:
        v = DEFAULT_VOLUME
    return max(0, min(100, v))


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


def _message_overlay_style_from_settings(settings: dict) -> str:
    preset = str(settings.get(MESSAGE_OVERLAY_PRESET_KEY, DEFAULT_MESSAGE_OVERLAY_PRESET)).strip().lower()
    if preset not in MESSAGE_OVERLAY_PRESETS:
        preset = DEFAULT_MESSAGE_OVERLAY_PRESET

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


def _require_file(path: Path, *, what: str, expected_rel_hint: Optional[str] = None) -> None:
    if path.is_file():
        return
    hint = f"\nExpected: {expected_rel_hint}" if expected_rel_hint else ""
    raise FileNotFoundError(f"Missing {what}: {path}{hint}")


def _validate_template_placeholders() -> None:
    """
    Fail fast if Template.py changes and placeholders drift.
    """
    required = ("{{TITLE}}", "{{MESSAGE_HTML}}", "{{INITIAL_VOLUME}}", "{{MESSAGE_OVERLAY_STYLE}}", "{{MUSIC_PLAYLIST_JSON}}", "{{MUSIC_CROSSFADE_MS}}", "{{MUSIC_PRELOAD_HTML}}")
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


def _seed_sfx_into_build(*, project_root: Path, sounds_dst: Path, seed_sfx: bool) -> None:
    """Copy required sound effects from the app-owned source directory."""
    if not seed_sfx:
        return

    app_sounds = project_root / APP_SOUNDS_DIR
    missing = [name for name in _sfx_names() if not (app_sounds / name).is_file()]
    if missing:
        lines = [
            "Missing required app sound effects:",
            *[f"  - {name}" for name in missing],
            "",
            f"Expected directory: {app_sounds}",
        ]
        raise FileNotFoundError("\n".join(lines))

    for name in _sfx_names():
        _atomic_copy_file(app_sounds / name, sounds_dst / name)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def generate_play_bundle(
    project_root: str,
    *,
    message_html: Optional[str] = None,
    open_in_browser: bool = False,
    seed_sfx: bool = True,
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

    # Resolve the current project's explicit sound mode. Music is optional;
    # a project with no selected track exports silently instead of failing.
    sound_state, sound_tracks = resolve_project_tracks(pr)

    # Settings drive deterministic output path
    settings = _load_settings(pr)
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    starting_vol = _starting_volume_from_settings(settings)
    project_id = ensure_project_identity(pr)

    # Build beside the live Play bundle and replace it only after validation.
    final_play_dir = (pr / OUTPUT_PLAY_DIR / project_id).resolve()
    transaction = PathTransaction(
        final_play_dir,
        staging_suffix=".build-staging",
        backup_suffix=".build-backup",
    )
    staging = transaction.prepare()
    bp = plan_build(
        pr,
        recipient=recipient,
        title=title,
        project_id=project_id,
        play_dir_override=staging,
    )

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
    _copy_required_files(controls_src, controls_dst, CONTROL_FILES)

    # Copy message files (optional)
    if msg_html_src.is_file():
        _atomic_copy_file(msg_html_src, message_dst / "message.html")
    if msg_png_src.is_file():
        _atomic_copy_file(msg_png_src, message_dst / "message.png")

    # Copy only the tracks assigned to this letter. The first runtime name
    # remains music.mp3 for compatibility; additional playlist entries receive
    # stable numbered names.
    runtime_music_files: list[str] = []
    for index, record in enumerate(sound_tracks):
        runtime_name = MUSIC_FILE if index == 0 else f"music-{index + 1:03d}.mp3"
        source = resolve_track_path(pr, record)
        _require_file(source, what=f"processed music for {record.display_title}")
        _atomic_copy_file(source, sounds_dst / runtime_name)
        runtime_music_files.append(runtime_name)

    sound_manifest = build_sound_manifest(sound_state, sound_tracks, runtime_music_files)
    _atomic_write_text(
        sounds_dst / BUILD_SOUND_MANIFEST_NAME,
        json.dumps(sound_manifest, indent=2, ensure_ascii=False),
    )

    # Copy required app-owned SFX into the build.
    _seed_sfx_into_build(project_root=pr, sounds_dst=sounds_dst, seed_sfx=seed_sfx)

    # Write CSS/JS
    _atomic_write_text(bp.play_dir / "styles.css", TEMPLATE_CSS)
    _atomic_write_text(bp.play_dir / "script.js", TEMPLATE_JS)

    # Message HTML injection (prefer passed string, else disk)
    if message_html is None:
        message_html = _read_text_safe(msg_html_src)

    playlist_sources = [f"gallery/sounds/{name}" for name in runtime_music_files]
    preload_html = (
        f'<link rel="preload" as="audio" href="{playlist_sources[0]}" type="audio/mpeg">'
        if playlist_sources else ""
    )
    html = (
        TEMPLATE_HTML
        .replace("{{TITLE}}", _html.escape(title, quote=True))
        .replace("{{MESSAGE_HTML}}", message_html or "")
        .replace("{{INITIAL_VOLUME}}", str(starting_vol))
        .replace("{{MESSAGE_OVERLAY_STYLE}}", _message_overlay_style_from_settings(settings))
        .replace("{{MUSIC_PLAYLIST_JSON}}", json.dumps(playlist_sources))
        .replace("{{MUSIC_CROSSFADE_MS}}", str(int(sound_manifest.get("crossfade_ms", 0))))
        .replace("{{MUSIC_PRELOAD_HTML}}", preload_html)
    )
    _atomic_write_text(bp.play_dir / "index.html", html)

    required_output = (
        bp.play_dir / "index.html",
        bp.play_dir / "styles.css",
        bp.play_dir / "script.js",
        *(bp.play_pages_dir / name for name in REQUIRED_SLIDES),
        *(bp.play_controls_dir / name for name in CONTROL_FILES),
    )
    missing_output = [str(path.relative_to(bp.play_dir)) for path in required_output if not path.is_file()]
    if missing_output:
        transaction.abort()
        raise RuntimeError(
            "The staged Play bundle is incomplete: " + ", ".join(missing_output)
        )
    try:
        transaction.commit(
            keep_backup=True,
            validator=lambda directory: (directory / "index.html").is_file(),
        )
    except Exception:
        transaction.abort()
        raise
    transaction.finalize()

    if open_in_browser:
        try:
            webbrowser.open((final_play_dir / "index.html").as_uri())
        except Exception:
            pass

    return final_play_dir
