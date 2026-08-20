from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from config import APP_BANNER_PATH, USER_CONTROLS_DIR, USER_PAGES_DIR
from curtain_color import (
    FALLBACK_CURTAIN_RGB,
    RGB,
    curtain_variant_rgbs,
    write_recolored_banner_image,
    write_tinted_curtain_image,
)
from project_paths import application_paths
from transactional_io import PathTransaction, atomic_write_json


CURTAIN_CACHE_SCHEMA_VERSION = 2
CURTAIN_DYNAMIC_STYLES = (
    "average_color",
    "complementary_average_color",
    "normal_light",
    "complementary_light",
    "normal_dark",
    "complementary_dark",
)
CURTAIN_CACHE_STYLES = ("pure_white", *CURTAIN_DYNAMIC_STYLES)
CURTAIN_CACHE_FILES = (
    "cleft.png",
    "cright.png",
    "bannerman.png",
)
CURTAIN_CACHE_ROOT = Path("gallery") / "user" / "cache" / "curtains"
CURTAIN_CACHE_DIRECTORY_NAME = "curtains"


@dataclass(frozen=True)
class CurtainVariantCache:
    fingerprint: str
    directory: Path
    colors: Mapping[str, RGB]

    def style_directory(self, style: str) -> Path | None:
        normalized = str(style or "").strip()
        if normalized not in CURTAIN_CACHE_STYLES:
            return None
        directory = self.directory / normalized
        return directory if directory.is_dir() else None


def curtain_source_fingerprint(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    paths = application_paths(root)
    sources = (
        ("cover", root / USER_PAGES_DIR / "cover.png"),
        ("curtain-left", root / USER_CONTROLS_DIR / "cleft.png"),
        ("curtain-right", root / USER_CONTROLS_DIR / "cright.png"),
        ("banner", paths.resource_path(APP_BANNER_PATH)),
    )
    if any(
        not path.is_file() or path.is_symlink()
        for _label, path in sources
    ):
        return ""

    digest = hashlib.sha256()
    digest.update(f"curtain-cache:{CURTAIN_CACHE_SCHEMA_VERSION}".encode("ascii"))
    for label, path in sources:
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def load_curtain_variant_cache(
    project_root: str | Path,
    *,
    fingerprint: str | None = None,
) -> CurtainVariantCache | None:
    root = Path(project_root).resolve()
    paths = application_paths(root)
    source_fingerprint = fingerprint or curtain_source_fingerprint(root)
    if not source_fingerprint:
        return None
    directory = paths.cache_root / CURTAIN_CACHE_DIRECTORY_NAME / source_fingerprint
    return _load_cache_directory(directory, source_fingerprint)


def prepare_curtain_variant_cache(
    project_root: str | Path,
) -> CurtainVariantCache | None:
    root = Path(project_root).resolve()
    paths = application_paths(root)
    fingerprint = curtain_source_fingerprint(root)
    if not fingerprint:
        return None

    existing = load_curtain_variant_cache(root, fingerprint=fingerprint)
    if existing is not None:
        return existing

    final_directory = paths.cache_root / CURTAIN_CACHE_DIRECTORY_NAME / fingerprint
    transaction = PathTransaction(
        final_directory,
        staging_suffix=".building",
        backup_suffix=".backup",
        unique_staging=True,
    )
    staging = transaction.prepare()
    try:
        cover = root / USER_PAGES_DIR / "cover.png"
        controls = root / USER_CONTROLS_DIR
        banner = paths.resource_path(APP_BANNER_PATH)
        colors = curtain_variant_rgbs(cover)

        for style in CURTAIN_CACHE_STYLES:
            style_directory = staging / style
            style_directory.mkdir(parents=True, exist_ok=True)
            rgb = colors.get(style, FALLBACK_CURTAIN_RGB)
            for filename in ("cleft.png", "cright.png"):
                write_tinted_curtain_image(
                    controls / filename,
                    style_directory / filename,
                    rgb,
                )
            write_recolored_banner_image(
                banner,
                style_directory / "bannerman.png",
                rgb,
            )

        atomic_write_json(
            staging / "manifest.json",
            {
                "schema_version": CURTAIN_CACHE_SCHEMA_VERSION,
                "source_fingerprint": fingerprint,
                "colors": {
                    style: list(colors.get(style, FALLBACK_CURTAIN_RGB))
                    for style in CURTAIN_CACHE_STYLES
                },
            },
        )
        if curtain_source_fingerprint(root) != fingerprint:
            transaction.abort()
            return None
        transaction.commit(
            validator=lambda directory: _load_cache_directory(
                directory,
                fingerprint,
            ) is not None,
        )
    except Exception:
        transaction.abort()
        raise

    return load_curtain_variant_cache(root, fingerprint=fingerprint)


def _load_cache_directory(
    directory: Path,
    fingerprint: str,
) -> CurtainVariantCache | None:
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != CURTAIN_CACHE_SCHEMA_VERSION
        or manifest.get("source_fingerprint") != fingerprint
    ):
        return None

    raw_colors = manifest.get("colors")
    if not isinstance(raw_colors, dict):
        return None
    colors: dict[str, RGB] = {}
    for style in CURTAIN_CACHE_STYLES:
        raw_rgb = raw_colors.get(style)
        if (
            not isinstance(raw_rgb, list)
            or len(raw_rgb) != 3
            or not all(
                isinstance(channel, int) and 0 <= channel <= 255
                for channel in raw_rgb
            )
        ):
            return None
        colors[style] = tuple(raw_rgb)  # type: ignore[assignment]
        style_directory = directory / style
        if any(
            not (style_directory / filename).is_file()
            or (style_directory / filename).is_symlink()
            for filename in CURTAIN_CACHE_FILES
        ):
            return None

    return CurtainVariantCache(
        fingerprint=fingerprint,
        directory=directory.resolve(),
        colors=colors,
    )


__all__ = [
    "CURTAIN_CACHE_FILES",
    "CURTAIN_CACHE_ROOT",
    "CURTAIN_CACHE_SCHEMA_VERSION",
    "CURTAIN_CACHE_STYLES",
    "CURTAIN_DYNAMIC_STYLES",
    "CurtainVariantCache",
    "curtain_source_fingerprint",
    "load_curtain_variant_cache",
    "prepare_curtain_variant_cache",
]
