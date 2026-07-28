#!/usr/bin/env python3
# ===============================
# File: Generate.py
# Purpose:
#   Build the Play viewer bundle at:
#     output/Play/<project_id>/
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
#   Viewer SFX:
#     gallery/app/sounds/ (canonical)
#     recognized legacy locations remain readable
#
# Improvements applied:
#   1) Seed required SFX from canonical or recognized legacy sources
#   2) Atomic copy on Windows (copy -> tmp -> os.replace) for robustness
#   3) Strict template placeholder validation (fail fast if Template drift occurs)
#
# NOTE:
# - Back-compat API removed (no prepare_gallery_dir / generate_gallery legacy signature).
# - Forge_Tab.py will be updated separately to call generate_play_bundle().
# ===============================

from __future__ import annotations

import html as _html
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    build_sound_manifest,
    resolve_project_tracks,
    resolve_track_path,
)
from project_state import ensure_project_identity
from settings_store import SettingsStore
from transactional_io import PathTransaction, cleanup_abandoned_staging
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
    USER_MESSAGE_DIR,
    GLISS_FILE,
    FLIP_PREFIX,
    FLIP_COUNT,
)

# App-owned SFX live here (relative to project root)
APP_SOUNDS_DIR = Path("gallery") / "app" / "sounds"
LEGACY_SFX_DIRS = (
    Path("gallery") / "user" / "sounds",
    Path("gallery") / "app" / "icons" / "Sounds",
)
BUILD_STATE_FILE = "lettersmith-build.json"
BUILD_SCHEMA_VERSION = 2
_FINGERPRINT_SETTING_KEYS = (
    "recipient_name",
    "recipient_title",
    "starting_volume",
    "music_volume",
    "curtain_style",
    "message_overlay_preset",
    "message_overlay_opacity",
)
_LOGGER = logging.getLogger(__name__)


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


def _copy_directory_files(source: Path, destination: Path) -> None:
    """Copy regular files recursively without following directory symlinks."""
    if not source.is_dir():
        return
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(source)
        _atomic_copy_file(path, destination / relative)


_MESSAGE_ATTRIBUTE_ASSET = re.compile(
    r"""(?P<prefix>\b(?:src|poster)\s*=\s*(?P<quote>["']))"""
    r"""(?P<value>[^"']+)(?P<suffix>(?P=quote))""",
    re.IGNORECASE,
)
_CSS_ASSET = re.compile(
    r"""(?P<prefix>\burl\(\s*(?P<quote>["']?))"""
    r"""(?P<value>[^)"']+)(?P<suffix>(?P=quote)\s*\))""",
    re.IGNORECASE,
)


def _message_asset_reference(
    reference: str,
    *,
    project_root: Path,
    message_root: Path,
) -> str:
    value = reference.strip()
    if not value or value.startswith(("#", "data:")):
        return reference
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError(
            f"Message media must be stored with the project: {value}"
        )
    relative = Path(unquote(parsed.path))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
    ):
        raise ValueError(f"Unsafe message asset path: {value}")

    if relative.parts[:1] == ("gallery",):
        source = project_root / relative
        embedded_path = relative.as_posix()
        containment_root = project_root
    else:
        source = message_root / relative
        embedded_path = f"gallery/message/{relative.as_posix()}"
        containment_root = message_root
    resolved = source.resolve()
    try:
        resolved.relative_to(containment_root.resolve())
    except ValueError as error:
        raise ValueError(f"Message asset escapes its project: {value}") from error
    source_relative = source.relative_to(containment_root)
    cursor = containment_root
    unsafe_link = False
    for part in source_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            unsafe_link = True
            break
    if unsafe_link or not resolved.is_file():
        raise FileNotFoundError(f"Message asset is missing: {value}")

    suffix = ""
    if parsed.query:
        suffix += f"?{parsed.query}"
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"
    return embedded_path + suffix


def _prepare_embedded_message_html(
    message_html: str,
    *,
    project_root: Path,
    message_root: Path,
) -> str:
    def replace(match: re.Match[str]) -> str:
        rewritten = _message_asset_reference(
            match.group("value"),
            project_root=project_root,
            message_root=message_root,
        )
        return (
            match.group("prefix")
            + rewritten
            + match.group("suffix")
        )

    prepared = _MESSAGE_ATTRIBUTE_ASSET.sub(replace, message_html)
    return _CSS_ASSET.sub(replace, prepared)


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
    except (TypeError, ValueError):
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
    except (TypeError, ValueError):
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


def _resolve_sfx_sources(project_root: Path) -> dict[str, Path]:
    directories = (
        project_root / APP_SOUNDS_DIR,
        *(project_root / relative for relative in LEGACY_SFX_DIRS),
    )
    resolved: dict[str, Path] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        available = {
            path.name.casefold(): path
            for path in directory.iterdir()
            if path.is_file() and not path.is_symlink()
        }
        for name in _sfx_names():
            if name in resolved:
                continue
            source = available.get(name.casefold())
            if source is not None:
                resolved[name] = source
    return resolved


def _seed_sfx_into_build(*, project_root: Path, sounds_dst: Path, seed_sfx: bool) -> None:
    """Copy required sound effects from canonical or recognized legacy sources."""
    if not seed_sfx:
        return

    sources = _resolve_sfx_sources(project_root)
    missing = [name for name in _sfx_names() if name not in sources]
    if missing:
        searched = (
            project_root / APP_SOUNDS_DIR,
            *(project_root / relative for relative in LEGACY_SFX_DIRS),
        )
        lines = [
            "Missing required app sound effects:",
            *[f"  - {name}" for name in missing],
            "",
            "Searched:",
            *[f"  - {directory}" for directory in searched],
        ]
        raise FileNotFoundError("\n".join(lines))

    for name in _sfx_names():
        _atomic_copy_file(sources[name], sounds_dst / name)


def play_bundle_directory(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    return (root / OUTPUT_PLAY_DIR / ensure_project_identity(root)).resolve()


def _hash_file(digest: "hashlib._Hash", root: Path, path: Path) -> None:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        relative = f"external/{resolved.name}"
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    digest.update(b"\0")


def build_source_fingerprint(project_root: str | Path) -> str:
    """Hash only inputs that materially change the generated viewer."""
    root = Path(project_root).resolve()
    digest = hashlib.sha256()
    settings = SettingsStore(root).snapshot()
    relevant_settings = {
        key: settings.get(key)
        for key in _FINGERPRINT_SETTING_KEYS
        if key in settings
    }
    digest.update(
        json.dumps(
            relevant_settings,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(TEMPLATE_HTML.encode("utf-8"))
    digest.update(TEMPLATE_CSS.encode("utf-8"))
    digest.update(TEMPLATE_JS.encode("utf-8"))

    file_roots = (
        root / USER_PAGES_DIR,
        root / USER_CONTROLS_DIR,
        root / USER_MESSAGE_DIR,
        root / APP_SOUNDS_DIR,
        root / "gallery/user/fonts",
        root / "gallery/app/fonts",
    )
    files: set[Path] = set()
    for directory in file_roots:
        if directory.is_dir():
            files.update(
                path.resolve()
                for path in directory.rglob("*")
                if path.is_file() and not path.is_symlink()
            )

    sound_state, sound_tracks = resolve_project_tracks(root)
    digest.update(
        json.dumps(
            {
                "mode": sound_state.mode,
                "single_track_id": sound_state.single_track_id,
                "playlist": list(sound_state.playlist),
                "crossfade_ms": (
                    1000
                    if sound_state.mode == "playlist"
                    and len(sound_tracks) > 1
                    else 0
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for track in sound_tracks:
        source = resolve_track_path(root, track)
        if source.is_file():
            files.add(source.resolve())
    files.update(
        source.resolve()
        for source in _resolve_sfx_sources(root).values()
    )

    for path in sorted(files, key=lambda item: item.as_posix().casefold()):
        _hash_file(digest, root, path)
    return digest.hexdigest()


def _runtime_asset_references(value: str) -> tuple[str, ...]:
    return tuple(
        match.group("value").strip()
        for pattern in (_MESSAGE_ATTRIBUTE_ASSET, _CSS_ASSET)
        for match in pattern.finditer(value)
    )


def _validate_runtime_asset_reference(
    bundle_root: Path,
    base_directory: Path,
    reference: str,
) -> None:
    if not reference or reference.startswith(("#", "data:")):
        return
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        raise RuntimeError(
            f"The staged bundle contains an external media reference: {reference}"
        )
    relative = Path(unquote(parsed.path))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
    ):
        raise RuntimeError(
            f"The staged bundle contains an unsafe media path: {reference}"
        )
    candidate = (
        bundle_root / relative
        if relative.parts[:1] == ("gallery",)
        else base_directory / relative
    )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(bundle_root)
    except ValueError as error:
        raise RuntimeError(
            f"The staged bundle media path escapes the build: {reference}"
        ) from error
    if candidate.is_symlink() or not resolved.is_file():
        raise RuntimeError(
            f"The staged bundle media asset is missing: {reference}"
        )


def validate_play_bundle(directory: str | Path) -> Path:
    """Validate the complete static viewer contract before it becomes live."""
    root = Path(directory).resolve()
    required = (
        root / "index.html",
        root / "styles.css",
        root / "script.js",
        *(root / "gallery/pages" / name for name in REQUIRED_SLIDES),
        *(root / "gallery/controls" / name for name in CONTROL_FILES),
        root / "gallery/message/message.html",
        root / "gallery/sounds" / BUILD_SOUND_MANIFEST_NAME,
        root / BUILD_STATE_FILE,
    )
    missing = [
        path.relative_to(root).as_posix()
        for path in required
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "The staged Play bundle is incomplete: " + ", ".join(missing)
        )

    try:
        if not (root / "gallery/message/message.html").read_text(
            encoding="utf-8"
        ).strip():
            raise ValueError("message.html is empty")
        sound_manifest = json.loads(
            (root / "gallery/sounds" / BUILD_SOUND_MANIFEST_NAME).read_text(
                encoding="utf-8"
            )
        )
        build_state = json.loads(
            (root / BUILD_STATE_FILE).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            f"The staged Play bundle contains unreadable data: {error}"
        ) from error
    if not isinstance(sound_manifest, dict) or not isinstance(
        sound_manifest.get("tracks", []), list
    ):
        raise RuntimeError("The staged sound manifest is invalid.")
    for raw_track in sound_manifest.get("tracks", []):
        if not isinstance(raw_track, dict):
            raise RuntimeError("The staged sound manifest is invalid.")
        filename = str(raw_track.get("filename", "")).strip()
        if (
            not filename
            or Path(filename).name != filename
            or not (root / "gallery/sounds" / filename).is_file()
        ):
            raise RuntimeError(
                "The staged sound manifest references a missing track."
            )
    for path, base in (
        (root / "index.html", root),
        (root / "styles.css", root),
        (
            root / "gallery/message/message.html",
            root / "gallery/message",
        ),
    ):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError(
                f"The staged bundle asset references cannot be read: {path.name}"
            ) from error
        for reference in _runtime_asset_references(content):
            _validate_runtime_asset_reference(root, base, reference)
    if (
        not isinstance(build_state, dict)
        or build_state.get("schema_version") != BUILD_SCHEMA_VERSION
        or not str(build_state.get("source_fingerprint", "")).strip()
    ):
        raise RuntimeError("The staged build state is invalid.")
    return root


def is_play_bundle_current(project_root: str | Path) -> bool:
    root = Path(project_root).resolve()
    build = play_bundle_directory(root)
    try:
        validate_play_bundle(build)
        state = json.loads((build / BUILD_STATE_FILE).read_text(encoding="utf-8"))
        return state.get("source_fingerprint") == build_source_fingerprint(root)
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def build_play_bundle_to(
    project_root: str | Path,
    destination: str | Path,
    *,
    message_html: Optional[str] = None,
    seed_sfx: bool = True,
    source_fingerprint: Optional[str] = None,
) -> Path:
    """Generate and validate a complete Play bundle in ``destination``."""
    pr = Path(project_root).resolve()
    target = Path(destination).resolve()
    _validate_template_placeholders()

    missing_pages = validate_required_images(pr)
    if missing_pages:
        raise FileNotFoundError(
            "Required page images are missing: " + ", ".join(missing_pages)
        )
    missing_controls = validate_controls(pr)
    if missing_controls:
        raise FileNotFoundError(
            "Required viewer controls are missing: " + ", ".join(missing_controls)
        )

    msg_html_src = pr / MESSAGE_HTML_FILE
    if message_html is None:
        message_html = _read_text_safe(msg_html_src)
    if not (message_html or "").strip():
        raise FileNotFoundError("Message content is required.")
    embedded_message_html = _prepare_embedded_message_html(
        message_html,
        project_root=pr,
        message_root=pr / USER_MESSAGE_DIR,
    )

    sound_state, sound_tracks = resolve_project_tracks(pr)
    settings = SettingsStore(pr).snapshot()
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    starting_vol = _starting_volume_from_settings(settings)
    project_id = ensure_project_identity(pr)
    bp = plan_build(
        pr,
        recipient=recipient,
        title=title,
        project_id=project_id,
        play_dir_override=target,
    )

    pages_src = pr / USER_PAGES_DIR
    controls_src = pr / USER_CONTROLS_DIR
    message_src = pr / USER_MESSAGE_DIR
    _copy_required_files(pages_src, bp.play_pages_dir, REQUIRED_SLIDES)
    _copy_required_files(controls_src, bp.play_controls_dir, CONTROL_FILES)
    _copy_directory_files(message_src, bp.play_message_dir)
    _atomic_write_text(bp.play_message_dir / "message.html", message_html)

    for font_source in (
        pr / "gallery/app/fonts",
        pr / "gallery/user/fonts",
    ):
        _copy_directory_files(font_source, bp.play_fonts_dir)

    runtime_music_files: list[str] = []
    for index, record in enumerate(sound_tracks):
        runtime_name = MUSIC_FILE if index == 0 else f"music-{index + 1:03d}.mp3"
        source = resolve_track_path(pr, record)
        _require_file(source, what=f"processed music for {record.display_title}")
        _atomic_copy_file(source, bp.play_sounds_dir / runtime_name)
        runtime_music_files.append(runtime_name)

    sound_manifest = build_sound_manifest(
        sound_state,
        sound_tracks,
        runtime_music_files,
    )
    _atomic_write_text(
        bp.play_sounds_dir / BUILD_SOUND_MANIFEST_NAME,
        json.dumps(sound_manifest, indent=2, ensure_ascii=False),
    )
    _seed_sfx_into_build(
        project_root=pr,
        sounds_dst=bp.play_sounds_dir,
        seed_sfx=seed_sfx,
    )

    _atomic_write_text(bp.play_dir / "styles.css", TEMPLATE_CSS)
    _atomic_write_text(bp.play_dir / "script.js", TEMPLATE_JS)
    playlist_sources = [
        f"gallery/sounds/{name}" for name in runtime_music_files
    ]
    preload_html = (
        f'<link rel="preload" as="audio" href="{playlist_sources[0]}" '
        'type="audio/mpeg">'
        if playlist_sources
        else ""
    )
    html = (
        TEMPLATE_HTML
        .replace("{{TITLE}}", _html.escape(title, quote=True))
        .replace("{{MESSAGE_HTML}}", embedded_message_html)
        .replace("{{INITIAL_VOLUME}}", str(starting_vol))
        .replace(
            "{{MESSAGE_OVERLAY_STYLE}}",
            _message_overlay_style_from_settings(settings),
        )
        .replace("{{MUSIC_PLAYLIST_JSON}}", json.dumps(playlist_sources))
        .replace(
            "{{MUSIC_CROSSFADE_MS}}",
            str(int(sound_manifest.get("crossfade_ms", 0))),
        )
        .replace("{{MUSIC_PRELOAD_HTML}}", preload_html)
    )
    _atomic_write_text(bp.play_dir / "index.html", html)
    fingerprint = source_fingerprint or build_source_fingerprint(pr)
    _atomic_write_text(
        bp.play_dir / BUILD_STATE_FILE,
        json.dumps(
            {
                "schema_version": BUILD_SCHEMA_VERSION,
                "project_id": project_id,
                "source_fingerprint": fingerprint,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    return validate_play_bundle(bp.play_dir)


def generate_play_bundle(
    project_root: str,
    *,
    message_html: Optional[str] = None,
    open_in_browser: bool = False,
    seed_sfx: bool = True,
) -> Path:
    """Transactionally replace the stable project-ID Play bundle."""
    pr = Path(project_root).resolve()
    ensure_output_dirs(pr)
    final_play_dir = play_bundle_directory(pr)
    staging_prefix = final_play_dir.name + ".build-staging."
    cleanup_abandoned_staging(
        final_play_dir.parent,
        prefix=staging_prefix,
    )
    transaction = PathTransaction(
        final_play_dir,
        staging_suffix=".build-staging",
        backup_suffix=".build-backup",
        unique_staging=True,
    )
    source_fingerprint = build_source_fingerprint(pr)
    try:
        staging = transaction.prepare()
        build_play_bundle_to(
            pr,
            staging,
            message_html=message_html,
            seed_sfx=seed_sfx,
            source_fingerprint=source_fingerprint,
        )
        transaction.commit(
            keep_backup=True,
            validator=lambda directory: bool(validate_play_bundle(directory)),
        )
        validate_play_bundle(final_play_dir)
    except Exception:
        _LOGGER.exception("Play bundle generation failed for %s", pr)
        transaction.abort()
        raise
    try:
        transaction.finalize()
    except OSError:
        _LOGGER.exception(
            "Play bundle backup cleanup failed for %s",
            final_play_dir,
        )

    if open_in_browser:
        webbrowser.open((final_play_dir / "index.html").as_uri())
    return final_play_dir


def ensure_play_bundle(
    project_root: str | Path,
    *,
    message_html: Optional[str] = None,
    seed_sfx: bool = True,
    force: bool = False,
) -> tuple[Path, bool]:
    """Return a validated build, rebuilding only when its inputs are stale."""
    root = Path(project_root).resolve()
    build = play_bundle_directory(root)
    if not force and is_play_bundle_current(root):
        return build, False
    return (
        generate_play_bundle(
            str(root),
            message_html=message_html,
            open_in_browser=False,
            seed_sfx=seed_sfx,
        ),
        True,
    )
