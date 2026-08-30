#!/usr/bin/env python3
# ===============================
# File: Generate.py
# Purpose:
#   Build the viewer bundle in the configured saved-letter root.
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
#     active-project sound workspace       (project sound manifest)
#   Viewer SFX:
#     gallery/app/sounds/ (canonical)
#
# Improvements applied:
#   1) Seed required SFX from canonical application resources
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
import secrets
import tempfile
import threading
import webbrowser
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from Template import TEMPLATE_HTML, TEMPLATE_CSS, TEMPLATE_JS
from curtain_cache import CurtainVariantCache, load_curtain_variant_cache
from curtain_color import (
    curtain_variant_rgbs,
    get_curtain_display_colors,
    write_recolored_banner_image,
    write_tinted_curtain_image,
)
from font_export import FontExportError, build_embedded_font_payload
from image_animation import (
    IMAGE_MANIFEST_NAME,
    build_runtime_image_assets,
    validate_runtime_image_manifest,
)
from letter_page import letter_page_style_from_settings
from message_format import message_plain_text
from message_html import sanitize_message_html
from performance_trace import performance_timed
from readiness import evaluate_readiness
from saved_letters import update_saved_metadata
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    build_sound_manifest,
    resolve_project_tracks,
    resolve_track_path,
)
from project_state import ensure_project_identity
from project_paths import application_paths
from protected_projects import demo_preview_directory, is_protected_project
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
    DEFAULT_SETTINGS,
    SettingsStore,
    normalize_curtain_style,
)
from transactional_io import (
    PathTransaction,
    atomic_copy_file as _transactional_atomic_copy_file,
    atomic_write_json as _transactional_atomic_write_json,
    cleanup_abandoned_staging,
    enforce_internal_tree_visibility,
    file_change_token,
)
from config import (
    APP_BANNER_PATH,
    DEFAULT_VOLUME,
    STARTING_VOLUME,
    ensure_output_dirs,
    plan_build,
    resolve_play_bundle_directory,
    validate_required_images,
    validate_controls,
    USER_PAGES_DIR,
    USER_CONTROLS_DIR,
    REQUIRED_SLIDES,
    CONTROL_FILES,
    MESSAGE_ASSETS_DIR,
    MESSAGE_HTML_FILE,
    MESSAGE_IMAGE_FILE,
    MUSIC_FILE,
    USER_MESSAGE_DIR,
    GLISS_FILE,
    FLIP_PREFIX,
    FLIP_COUNT,
)

# App-owned SFX live here (relative to the application resource root).
APP_SOUNDS_DIR = Path("sounds")
BANNER_FILE = "bannerman.png"
BUILD_STATE_FILE = "lettersmith-build.json"
BUILD_SCHEMA_VERSION = 14
CURTAIN_FILES = {"cleft.png", "cright.png"}
CURTAIN_ANALYSIS_PAGE_ORDER = (
    "cover.png",
)
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
_FILE_DIGEST_CACHE_LIMIT = 2048
_FILE_DIGEST_CACHE: OrderedDict[
    tuple[str, int, int, int, int, int], bytes
] = OrderedDict()
_FILE_DIGEST_CACHE_LOCK = threading.RLock()


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
    _transactional_atomic_copy_file(src, dst)


class _BuildAssetCopier:
    """Reuse unchanged generated copies from the current validated bundle."""

    def __init__(
        self,
        destination: Path,
        reuse_directory: Path | None,
    ) -> None:
        self.destination = destination.resolve()
        self.reuse_directory = (
            reuse_directory.resolve()
            if reuse_directory is not None and reuse_directory.is_dir()
            else None
        )
        self.previous: dict[str, dict[str, object]] = {}
        self.records: dict[str, dict[str, object]] = {}
        if self.reuse_directory is None:
            return
        try:
            state = json.loads(
                (self.reuse_directory / BUILD_STATE_FILE).read_text(
                    encoding="utf-8"
                )
            )
            assets = state.get("asset_sources")
            if isinstance(assets, dict):
                self.previous = {
                    str(key): dict(value)
                    for key, value in assets.items()
                    if isinstance(key, str) and isinstance(value, dict)
                }
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self.previous = {}

    def _reusable_file(self, relative: str) -> Path | None:
        if self.reuse_directory is None:
            return None
        candidate = self.reuse_directory / relative
        cursor = self.reuse_directory
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.reuse_directory)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None

    def copy(self, source: Path, destination: Path) -> None:
        source = source.resolve()
        destination = destination.resolve()
        try:
            relative = destination.relative_to(self.destination).as_posix()
        except ValueError:
            _atomic_copy_file(source, destination)
            return
        source_stat = source.stat()
        digest = _file_content_digest(source).hex()
        previous = self.previous.get(relative, {})
        existing = self._reusable_file(relative)
        reusable = False
        if existing is not None:
            existing_stat = existing.stat()
            recorded = (
                previous.get("source_digest") == digest
                and previous.get("source_size") == int(source_stat.st_size)
                and previous.get("output_digest") == digest
                and previous.get("output_size") == int(existing_stat.st_size)
                and previous.get("output_mtime_ns")
                == int(existing_stat.st_mtime_ns)
                and previous.get("output_change_token")
                == file_change_token(existing, stat_result=existing_stat)
                and previous.get("output_device") == int(existing_stat.st_dev)
                and previous.get("output_inode") == int(existing_stat.st_ino)
            )
            if recorded:
                reusable = True
            elif _file_content_digest(existing).hex() == digest:
                # Older build records did not include a strong output identity.
                # Verify their bytes once before promoting them to the new record.
                reusable = True
        if reusable and existing is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            try:
                os.link(existing, destination)
            except OSError:
                _atomic_copy_file(existing, destination)
        else:
            _atomic_copy_file(source, destination)
        output_stat = destination.stat()
        self.records[relative] = {
            "source_digest": digest,
            "source_size": int(source_stat.st_size),
            "output_digest": digest,
            "output_size": int(output_stat.st_size),
            "output_mtime_ns": int(output_stat.st_mtime_ns),
            "output_change_token": file_change_token(
                destination,
                stat_result=output_stat,
            ),
            "output_device": int(output_stat.st_dev),
            "output_inode": int(output_stat.st_ino),
        }


def _refresh_build_asset_identities(directory: Path) -> None:
    """Refresh output identities after transaction cleanup removes old links."""
    root = Path(directory).resolve()
    state_path = root / BUILD_STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assets = state.get("asset_sources") if isinstance(state, dict) else None
    if not isinstance(assets, dict):
        return
    changed = False
    for relative, raw_record in assets.items():
        if not isinstance(relative, str) or not isinstance(raw_record, dict):
            continue
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        candidate = root / relative_path
        cursor = root
        unsafe = False
        for part in relative_path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                unsafe = True
                break
        if unsafe:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            output_stat = resolved.stat()
        except (OSError, ValueError):
            continue
        if not resolved.is_file():
            continue
        identity = {
            "output_size": int(output_stat.st_size),
            "output_mtime_ns": int(output_stat.st_mtime_ns),
            "output_change_token": file_change_token(
                resolved,
                stat_result=output_stat,
            ),
            "output_device": int(output_stat.st_dev),
            "output_inode": int(output_stat.st_ino),
        }
        if any(raw_record.get(key) != value for key, value in identity.items()):
            raw_record.update(identity)
            changed = True
    if changed:
        _transactional_atomic_write_json(state_path, state)


def _copy_control_files(
    src_dir: Path,
    dst_dir: Path,
    names: list[str],
    *,
    curtain_rgb: tuple[int, int, int],
    curtain_cache_directory: Path | None = None,
    copy_file: Callable[[Path, Path], None] = _atomic_copy_file,
) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = src_dir / name
        destination = dst_dir / name
        if name in CURTAIN_FILES:
            cached = (
                curtain_cache_directory / name
                if curtain_cache_directory is not None
                else None
            )
            if cached is not None and cached.is_file():
                copy_file(cached, destination)
            else:
                write_tinted_curtain_image(source, destination, curtain_rgb)
        else:
            copy_file(source, destination)


def _copy_directory_files(
    source: Path,
    destination: Path,
    *,
    excluded_relative_paths: tuple[str, ...] = (),
    copy_file: Callable[[Path, Path], None] = _atomic_copy_file,
) -> None:
    """Copy regular files recursively without following directory symlinks."""
    if not source.is_dir():
        return
    excluded = {Path(value).as_posix() for value in excluded_relative_paths}
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.as_posix() in excluded:
            continue
        copy_file(path, destination / relative)


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


def _message_overlay_style_from_settings(settings: dict) -> str:
    return letter_page_style_from_settings(settings)


def _script_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
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
    required = (
        "{{TITLE}}",
        "{{CSP_NONCE}}",
        "{{TITLE_BANNER_TEXT_RGB}}",
        "{{MESSAGE_HTML}}",
        "{{HAS_MESSAGE_JSON}}",
        "{{INITIAL_VOLUME}}",
        "{{MESSAGE_OVERLAY_STYLE}}",
        "{{MUSIC_PLAYLIST_JSON}}",
        "{{MUSIC_CROSSFADE_MS}}",
        "{{MUSIC_PRELOAD_HTML}}",
        "{{IMAGE_ANIMATIONS_JSON}}",
    )
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
    paths = application_paths(project_root)
    directories = (paths.app_resource_path(APP_SOUNDS_DIR),)
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


def _seed_sfx_into_build(
    *,
    project_root: Path,
    sounds_dst: Path,
    seed_sfx: bool,
    copy_file: Callable[[Path, Path], None] = _atomic_copy_file,
) -> None:
    """Copy required sound effects from canonical application resources."""
    if not seed_sfx:
        return

    sources = _resolve_sfx_sources(project_root)
    missing = [name for name in _sfx_names() if name not in sources]
    if missing:
        searched = (
            application_paths(project_root).app_resource_path(APP_SOUNDS_DIR),
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
        copy_file(sources[name], sounds_dst / name)


def play_bundle_directory(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    settings_store = SettingsStore(root)
    settings = settings_store.snapshot()
    if is_protected_project(settings):
        return demo_preview_directory(
            root,
            ensure_project_identity(root),
        )
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    active_value = str(settings.get(ACTIVE_PLAY_DIR_KEY) or "").strip()
    build = resolve_play_bundle_directory(
        root,
        recipient=recipient,
        title=title,
        project_id=ensure_project_identity(root),
        active_play_dir=active_value,
    )
    if (
        str(build) != active_value
        and all(
            (build / name).is_file()
            for name in ("index.html", "styles.css", "script.js")
        )
    ):
        try:
            settings_store.update_fields({ACTIVE_PLAY_DIR_KEY: str(build)})
        except Exception:
            _LOGGER.exception(
                "The resolved active Play directory could not be persisted: %s",
                build,
            )
    return build


def _curtain_context_for_settings(
    project_root: Path,
    settings: dict,
) -> tuple[
    str,
    dict[str, tuple[int, int, int]],
    CurtainVariantCache | None,
]:
    style = normalize_curtain_style(settings.get("curtain_style"))
    cached = load_curtain_variant_cache(project_root)
    colors = (
        dict(cached.colors)
        if cached is not None
        else curtain_variant_rgbs(
            project_root / USER_PAGES_DIR / CURTAIN_ANALYSIS_PAGE_ORDER[0]
        )
    )
    return style, colors, cached


def _rgb_css_value(rgb: tuple[int, int, int]) -> str:
    return ",".join(str(channel) for channel in rgb)


def _file_signature(path: Path) -> tuple[str, int, int, int, int, int]:
    resolved = path.resolve()
    stat_result = resolved.stat()
    return (
        os.path.normcase(str(resolved)),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
        file_change_token(resolved, stat_result=stat_result),
        int(stat_result.st_dev),
        int(stat_result.st_ino),
    )


def _file_content_digest(path: Path) -> bytes:
    """Return a bounded, stat-invalidated digest for one build input."""
    resolved = path.resolve()
    for _attempt in range(2):
        signature = _file_signature(resolved)
        with _FILE_DIGEST_CACHE_LOCK:
            cached = _FILE_DIGEST_CACHE.get(signature)
            if cached is not None:
                _FILE_DIGEST_CACHE.move_to_end(signature)
                return cached

        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        content_digest = digest.digest()
        if _file_signature(resolved) != signature:
            continue

        with _FILE_DIGEST_CACHE_LOCK:
            _FILE_DIGEST_CACHE[signature] = content_digest
            _FILE_DIGEST_CACHE.move_to_end(signature)
            while len(_FILE_DIGEST_CACHE) > _FILE_DIGEST_CACHE_LIMIT:
                _FILE_DIGEST_CACHE.popitem(last=False)
        return content_digest
    raise OSError(f"Build input changed while it was being read: {resolved}")


def _hash_file(digest: "hashlib._Hash", root: Path, path: Path) -> None:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        relative = f"external/{resolved.name}"
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_file_content_digest(resolved))
    digest.update(b"\0")


@performance_timed("forge.source_fingerprint")
def build_source_fingerprint(project_root: str | Path) -> str:
    """Hash only inputs that materially change the generated viewer."""
    root = Path(project_root).resolve()
    paths = application_paths(root)
    digest = hashlib.sha256()
    digest.update(b"lettersmith-source-fingerprint-v2\0")
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
        root / MESSAGE_ASSETS_DIR,
        paths.app_resource_path(APP_SOUNDS_DIR),
        root / "gallery/user/fonts",
        paths.app_resource_path("fonts"),
    )
    files: set[Path] = set()
    for directory in file_roots:
        if directory.is_dir():
            files.update(
                path.resolve()
                for path in directory.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
    banner_source = paths.resource_path(APP_BANNER_PATH)
    if banner_source.is_file() and not banner_source.is_symlink():
        files.add(banner_source.resolve())
    sound_state, sound_tracks = resolve_project_tracks(root)
    digest.update(
        json.dumps(
            {
                "mode": sound_state.mode,
                "single_track_id": sound_state.single_track_id,
                "playlist": list(sound_state.playlist),
                "playlist_expanded": bool(sound_state.playlist_expanded),
                "selected_track_id": sound_state.selected_track_id,
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
        root / "gallery/pages" / IMAGE_MANIFEST_NAME,
        *(root / "gallery/controls" / name for name in CONTROL_FILES),
        root / "gallery/controls" / BANNER_FILE,
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
        (root / "gallery/message/message.html").read_text(encoding="utf-8")
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
    try:
        validate_runtime_image_manifest(root / "gallery/pages")
    except ValueError as error:
        raise RuntimeError(str(error)) from error
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
    forbidden_counter_patterns = (
        (root / "index.html", r"""\bid\s*=\s*["']progress["']"""),
        (root / "styles.css", r"""#progress\b"""),
        (root / "script.js", r"""\bupdateProgress\s*\("""),
    )
    for path, pattern in forbidden_counter_patterns:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError(
                f"The staged viewer cannot be read: {path.name}"
            ) from error
        if re.search(pattern, content, flags=re.IGNORECASE):
            raise RuntimeError(
                f"The staged viewer still contains a page counter: {path.name}"
            )
    if (
        not isinstance(build_state, dict)
        or build_state.get("schema_version") != BUILD_SCHEMA_VERSION
        or not str(build_state.get("source_fingerprint", "")).strip()
    ):
        raise RuntimeError("The staged build state is invalid.")

    font_report = build_state.get("font_export")
    if not isinstance(font_report, dict):
        raise RuntimeError("The staged font export report is missing.")
    report_fields: dict[str, tuple[str, ...]] = {}
    for key in ("embedded", "files", "fallback"):
        values = font_report.get(key)
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value.strip()
            for value in values
        ):
            raise RuntimeError("The staged font export report is invalid.")
        report_fields[key] = tuple(values)
    if report_fields["fallback"]:
        raise RuntimeError("The staged bundle contains unresolved fonts.")
    if report_fields["embedded"] and not report_fields["files"]:
        raise RuntimeError("The staged bundle is missing its embedded font files.")

    styles = (root / "styles.css").read_text(encoding="utf-8")
    if report_fields["embedded"] and "@font-face" not in styles:
        raise RuntimeError("The staged bundle is missing embedded font CSS.")
    for filename in report_fields["files"]:
        if (
            Path(filename).name != filename
            or not filename.startswith("ls-font-")
            or not (root / "gallery/fonts" / filename).is_file()
            or f"gallery/fonts/{filename}" not in styles
        ):
            raise RuntimeError(
                "The staged bundle contains an invalid embedded font reference."
            )
    return root


def is_play_bundle_current(
    project_root: str | Path,
    *,
    source_fingerprint: Optional[str] = None,
) -> bool:
    root = Path(project_root).resolve()
    build = play_bundle_directory(root)
    try:
        validate_play_bundle(build)
        state = json.loads((build / BUILD_STATE_FILE).read_text(encoding="utf-8"))
        fingerprint = (
            build_source_fingerprint(root)
            if source_fingerprint is None
            else source_fingerprint
        )
        return state.get("source_fingerprint") == fingerprint
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
    validate_output: bool = True,
    reuse_play_directory: str | Path | None = None,
) -> Path:
    """Generate and validate a complete Play bundle in ``destination``."""
    pr = Path(project_root).resolve()
    paths = application_paths(pr)
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
    message_html = sanitize_message_html(message_html or "")
    has_message = bool(message_plain_text(message_html).strip())
    embedded_message_html = _prepare_embedded_message_html(
        message_html,
        project_root=pr,
        message_root=pr / USER_MESSAGE_DIR,
    )

    sound_state, sound_tracks = resolve_project_tracks(pr)
    settings = SettingsStore(pr).snapshot()
    recipient = _recipient_from_settings(settings)
    title = _title_from_settings(settings, recipient)
    curtain_style, curtain_colors, curtain_cache = (
        _curtain_context_for_settings(pr, settings)
    )
    curtain_display = get_curtain_display_colors(
        curtain_style,
        curtain_colors,
    )
    curtain_rgb = curtain_display.background
    title_banner_text_rgb = curtain_display.foreground
    curtain_cache_directory = (
        curtain_cache.style_directory(curtain_style)
        if curtain_cache is not None
        else None
    )
    starting_vol = _starting_volume_from_settings(settings)
    project_id = ensure_project_identity(pr)
    bp = plan_build(
        pr,
        recipient=recipient,
        title=title,
        project_id=project_id,
        play_dir_override=target,
    )
    reuse_root = (
        Path(reuse_play_directory).resolve()
        if reuse_play_directory is not None
        else None
    )
    asset_copier = _BuildAssetCopier(bp.play_dir, reuse_root)

    pages_src = pr / USER_PAGES_DIR
    controls_src = pr / USER_CONTROLS_DIR
    message_src = pr / USER_MESSAGE_DIR
    runtime_images = build_runtime_image_assets(
        pages_src,
        bp.play_pages_dir,
        reconcile_source=False,
        reuse_pages_directory=(
            Path(reuse_play_directory) / "gallery" / "pages"
            if reuse_play_directory is not None
            else None
        ),
        copy_file=asset_copier.copy,
    )
    _copy_control_files(
        controls_src,
        bp.play_controls_dir,
        CONTROL_FILES,
        curtain_rgb=curtain_rgb,
        curtain_cache_directory=curtain_cache_directory,
        copy_file=asset_copier.copy,
    )
    banner_source = paths.resource_path(APP_BANNER_PATH)
    _require_file(
        banner_source,
        what="reusable banner asset",
        expected_rel_hint=APP_BANNER_PATH.as_posix(),
    )
    cached_banner = (
        curtain_cache_directory / BANNER_FILE
        if curtain_cache_directory is not None
        else None
    )
    if cached_banner is not None and cached_banner.is_file():
        asset_copier.copy(
            cached_banner,
            bp.play_controls_dir / BANNER_FILE,
        )
    else:
        write_recolored_banner_image(
            banner_source,
            bp.play_controls_dir / BANNER_FILE,
            curtain_rgb,
        )
    _copy_directory_files(
        message_src,
        bp.play_message_dir,
        excluded_relative_paths=("message.html",),
        copy_file=asset_copier.copy,
    )
    _copy_directory_files(
        pr / MESSAGE_ASSETS_DIR,
        bp.play_dir / MESSAGE_ASSETS_DIR,
        copy_file=asset_copier.copy,
    )
    _atomic_write_text(bp.play_message_dir / "message.html", message_html)

    for font_source in (
        paths.app_resource_path("fonts"),
        pr / "gallery/user/fonts",
    ):
        _copy_directory_files(
            font_source,
            bp.play_fonts_dir,
            copy_file=asset_copier.copy,
        )
    font_result = build_embedded_font_payload(
        pr,
        embedded_message_html,
        bp.play_fonts_dir,
        reuse_fonts_dir=(
            reuse_root / "gallery" / "fonts"
            if reuse_root is not None
            else None
        ),
    )
    embedded_message_html = font_result.html

    runtime_music_files: list[str] = []
    for index, record in enumerate(sound_tracks):
        runtime_name = MUSIC_FILE if index == 0 else f"music-{index + 1:03d}.mp3"
        source = resolve_track_path(pr, record)
        _require_file(source, what=f"processed music for {record.display_title}")
        asset_copier.copy(source, bp.play_sounds_dir / runtime_name)
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
        copy_file=asset_copier.copy,
    )

    styles_css = (
        TEMPLATE_CSS
        if not font_result.css
        else f"{font_result.css}\n\n{TEMPLATE_CSS}"
    )
    _atomic_write_text(bp.play_dir / "styles.css", styles_css)
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
    csp_nonce = secrets.token_urlsafe(24)
    html = (
        TEMPLATE_HTML
        .replace("{{CSP_NONCE}}", csp_nonce)
        .replace("{{TITLE}}", _html.escape(title, quote=True))
        .replace(
            "{{TITLE_BANNER_TEXT_RGB}}",
            _rgb_css_value(title_banner_text_rgb),
        )
        .replace("{{HAS_MESSAGE_JSON}}", _script_json(has_message))
        .replace("{{INITIAL_VOLUME}}", str(starting_vol))
        .replace(
            "{{MESSAGE_OVERLAY_STYLE}}",
            _message_overlay_style_from_settings(settings),
        )
        .replace("{{MUSIC_PLAYLIST_JSON}}", _script_json(playlist_sources))
        .replace(
            "{{MUSIC_CROSSFADE_MS}}",
            str(int(sound_manifest.get("crossfade_ms", 0))),
        )
        .replace("{{MUSIC_PRELOAD_HTML}}", preload_html)
        .replace(
            "{{IMAGE_ANIMATIONS_JSON}}",
            _script_json(runtime_images.animations),
        )
    )
    # Message content is the only untrusted template value and must be inserted
    # after every trusted placeholder, especially the CSP nonce.
    html = html.replace("{{MESSAGE_HTML}}", embedded_message_html)
    for slot, source in runtime_images.page_sources.items():
        html = html.replace(
            f"gallery/pages/{slot}.png",
            source,
        )
    _atomic_write_text(bp.play_dir / "index.html", html)
    fingerprint = (
        build_source_fingerprint(pr)
        if source_fingerprint is None
        else source_fingerprint
    )
    _atomic_write_text(
        bp.play_dir / BUILD_STATE_FILE,
        json.dumps(
            {
                "schema_version": BUILD_SCHEMA_VERSION,
                "project_id": project_id,
                "source_fingerprint": fingerprint,
                "font_export": {
                    key: list(values)
                    for key, values in font_result.report.items()
                },
                "asset_sources": asset_copier.records,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    enforce_internal_tree_visibility(bp.play_dir)
    if validate_output:
        return validate_play_bundle(bp.play_dir)
    return bp.play_dir


def _verify_committed_play_bundle(
    directory: str | Path,
    *,
    source_fingerprint: str,
) -> Path:
    """Verify the atomic commit without repeating full staged validation."""
    root = Path(directory).resolve()
    for relative in ("index.html", "styles.css", "script.js", BUILD_STATE_FILE):
        if not (root / relative).is_file():
            raise RuntimeError(
                f"The committed Play bundle is missing {relative}."
            )
    state = json.loads((root / BUILD_STATE_FILE).read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("The committed Play bundle state is invalid.")
    if state.get("schema_version") != BUILD_SCHEMA_VERSION:
        raise RuntimeError("The committed Play bundle schema is invalid.")
    if state.get("source_fingerprint") != source_fingerprint:
        raise RuntimeError("The committed Play bundle fingerprint is invalid.")
    return root


@performance_timed("forge.generate_play_bundle")
def generate_play_bundle(
    project_root: str,
    *,
    message_html: Optional[str] = None,
    open_in_browser: bool = False,
    seed_sfx: bool = True,
    source_fingerprint: Optional[str] = None,
) -> Path:
    """Transactionally replace the active recipient/title Play bundle."""
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
    fingerprint = (
        build_source_fingerprint(pr)
        if source_fingerprint is None
        else source_fingerprint
    )
    try:
        staging = transaction.prepare()
        build_play_bundle_to(
            pr,
            staging,
            message_html=message_html,
            seed_sfx=seed_sfx,
            source_fingerprint=fingerprint,
            validate_output=False,
            reuse_play_directory=(
                final_play_dir if final_play_dir.is_dir() else None
            ),
        )
        update_saved_metadata(
            staging,
            pr,
            evaluate_readiness(pr),
        )
        transaction.commit(
            keep_backup=True,
            validator=lambda directory: bool(validate_play_bundle(directory)),
        )
        _verify_committed_play_bundle(
            final_play_dir,
            source_fingerprint=fingerprint,
        )
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
    try:
        _refresh_build_asset_identities(final_play_dir)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        # The existing records remain safe: the next build will verify bytes
        # before reuse when its recorded identity no longer matches.
        _LOGGER.exception(
            "Play bundle asset identities could not be refreshed for %s",
            final_play_dir,
        )
    try:
        SettingsStore(pr).update_fields(
            {ACTIVE_PLAY_DIR_KEY: str(final_play_dir)}
        )
    except Exception:
        _LOGGER.exception(
            "The active Play directory could not be persisted: %s",
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
    source_fingerprint: Optional[str] = None,
) -> tuple[Path, bool]:
    """Return a validated build, rebuilding only when its inputs are stale."""
    root = Path(project_root).resolve()
    build = play_bundle_directory(root)
    fingerprint = (
        build_source_fingerprint(root)
        if source_fingerprint is None
        else source_fingerprint
    )
    if not force and is_play_bundle_current(
        root,
        source_fingerprint=fingerprint,
    ):
        update_saved_metadata(
            build,
            root,
            evaluate_readiness(root),
        )
        return build, False
    return (
        generate_play_bundle(
            str(root),
            message_html=message_html,
            open_in_browser=False,
            seed_sfx=seed_sfx,
            source_fingerprint=fingerprint,
        ),
        True,
    )
