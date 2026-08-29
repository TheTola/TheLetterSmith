from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Mapping

from PIL import Image, ImageOps

from transactional_io import (
    PathTransaction,
    atomic_copy_file,
    atomic_write_bytes,
    atomic_write_json,
    create_staging_directory,
    file_change_token,
    set_path_hidden,
)


IMAGE_MANIFEST_NAME = "lettersmith-images.json"
IMAGE_MANIFEST_VERSION = 1
GIF_FRAMES_DIRECTORY = "gif_frames"
PLAYBACK_MODES = ("original", "loop", "ping_pong")
FOREVER = "forever"
MAX_PLAY_COUNT = 9999
MAX_DELAY_MS = 86_400_000
GIF_VIEWER_MAX_DIMENSION = 2048
GIF_THUMBNAIL_MAX_DIMENSION = 384
GIF_PROCESSING_VERSION = 1
RUNTIME_FRAME_CACHE_VERSION = 2
_LOGGER = logging.getLogger(__name__)
_IMAGE_IMPORT_SIDECARS = (
    ".image-import-staging.",
    ".image-import-backup",
)
MAX_INVALID_MANIFEST_BACKUPS = 3


class UnsupportedImageManifestSchemaError(ValueError):
    pass


def _backup_invalid_image_manifest(path: Path) -> None:
    if not path.is_file():
        return
    backup = path.with_name(
        f"{path.stem}.invalid.{time.strftime('%Y%m%d-%H%M%S')}."
        f"{time.time_ns()}{path.suffix}"
    )
    try:
        shutil.copy2(path, backup)
        set_path_hidden(backup)
    except OSError:
        _LOGGER.exception("Invalid image manifest backup failed: %s", backup)
        return
    backups = sorted(
        path.parent.glob(f"{path.stem}.invalid.*{path.suffix}"),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in backups[MAX_INVALID_MANIFEST_BACKUPS:]:
        try:
            stale.unlink()
        except OSError:
            _LOGGER.exception(
                "Could not remove old image-manifest backup: %s",
                stale,
            )


SLOT_DEFINITIONS = {
    "cover": {
        "index": 1,
        "label": "Cover Page Image",
        "basename": "cover",
    },
    "letter": {
        "index": 2,
        "label": "Main Letter Image",
        "basename": "letter",
    },
    "wall": {
        "index": 3,
        "label": "Letter Background Image",
        "basename": "wall",
    },
    "back": {
        "index": 4,
        "label": "Final Backdrop Image",
        "basename": "back",
    },
}
INDEX_TO_SLOT = {
    int(definition["index"]): slot
    for slot, definition in SLOT_DEFINITIONS.items()
}


@dataclass(frozen=True)
class GifInfo:
    frame_count: int
    durations_ms: tuple[int, ...]
    embedded_play_count: int | str
    disposal_methods: tuple[int, ...]


@dataclass(frozen=True)
class RuntimeImageAssets:
    page_sources: dict[str, str]
    animations: dict[str, dict[str, Any]]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class _PreparedPathChange:
    transaction: PathTransaction
    replace: bool


class PreparedImageAssetImport:
    """A fully processed slot update that has not touched live files yet."""

    def __init__(
        self,
        record: Mapping[str, Any],
        preview_path: Path,
        changes: tuple[_PreparedPathChange, ...],
        workspace_change_count: int,
    ) -> None:
        self.record = dict(record)
        self.preview_path = Path(preview_path)
        self._changes = changes
        self._workspace_change_count = workspace_change_count
        self._committed_count = 0
        self._project_discarded = False

    def _commit_through(self, change_count: int) -> None:
        try:
            for change in self._changes[self._committed_count : change_count]:
                change.transaction.commit(
                    replace=change.replace,
                    keep_backup=True,
                )
                if change.transaction.final_path.name == IMAGE_MANIFEST_NAME:
                    set_path_hidden(change.transaction.final_path)
                self._committed_count += 1
        except Exception:
            self.rollback()
            raise

    def commit_workspace(self) -> None:
        if self._committed_count:
            raise RuntimeError("The prepared image workspace is already committed.")
        self._commit_through(self._workspace_change_count)

    def commit_project(self) -> None:
        if self._project_discarded:
            raise RuntimeError("The prepared project image update was discarded.")
        if self._committed_count != self._workspace_change_count:
            raise RuntimeError("Commit the image workspace before its project copy.")
        self._commit_through(len(self._changes))

    def discard_project(self) -> None:
        if self._committed_count > self._workspace_change_count:
            raise RuntimeError("The project image update is already committed.")
        for change in self._changes[self._workspace_change_count :]:
            change.transaction.abort()
        self._project_discarded = True

    def commit(self) -> None:
        self.commit_workspace()
        self.commit_project()

    def rollback(self) -> None:
        for change in reversed(self._changes[: self._committed_count]):
            change.transaction.rollback()
        for change in self._changes[self._committed_count :]:
            change.transaction.abort()
        self._committed_count = 0

    def finalize(self) -> None:
        for change in self._changes:
            change.transaction.finalize()
        self._committed_count = 0

    def abort(self) -> None:
        if self._committed_count:
            self.rollback()
            return
        for change in self._changes:
            change.transaction.abort()

    def __del__(self) -> None:
        try:
            self.abort()
        except Exception:
            pass


def default_gif_settings() -> dict[str, Any]:
    return {
        "animation_enabled": True,
        "speed_percent": 100,
        "playback_mode": "original",
        "play_count": FOREVER,
        "start_delay_ms": 0,
        "loop_pause_ms": 0,
    }


def normalize_gif_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    mode = str(raw.get("playback_mode", "original")).strip().lower()
    if mode not in PLAYBACK_MODES:
        mode = "original"

    enabled_value = raw.get("animation_enabled", True)
    if isinstance(enabled_value, bool):
        animation_enabled = enabled_value
    else:
        normalized_enabled = str(enabled_value).strip().casefold()
        if normalized_enabled in {"0", "false", "no", "off"}:
            animation_enabled = False
        elif normalized_enabled in {"1", "true", "yes", "on"}:
            animation_enabled = True
        else:
            animation_enabled = True

    try:
        speed_percent = int(round(float(raw.get("speed_percent", 100))))
    except (TypeError, ValueError):
        speed_percent = 100
    speed_percent = max(25, min(400, speed_percent))

    raw_count = raw.get("play_count", FOREVER)
    if str(raw_count).strip().lower() == FOREVER:
        play_count: int | str = FOREVER
    else:
        try:
            play_count = int(raw_count)
        except (TypeError, ValueError):
            play_count = 1
        play_count = max(1, min(MAX_PLAY_COUNT, play_count))

    return {
        "animation_enabled": animation_enabled,
        "speed_percent": speed_percent,
        "playback_mode": mode,
        "play_count": play_count,
        "start_delay_ms": _bounded_milliseconds(raw.get("start_delay_ms", 0)),
        "loop_pause_ms": _bounded_milliseconds(raw.get("loop_pause_ms", 0)),
    }


def inspect_gif(path: str | Path) -> GifInfo:
    source = Path(path)
    with Image.open(source) as image:
        if image.format != "GIF":
            raise ValueError("The selected file is not a GIF.")
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        raw_loop = image.info.get("loop")
        durations: list[int] = []
        disposal_methods: list[int] = []
        for frame_index in range(frame_count):
            image.seek(frame_index)
            duration = image.info.get("duration", 100)
            try:
                duration_ms = int(duration)
            except (TypeError, ValueError):
                duration_ms = 100
            durations.append(duration_ms if duration_ms > 0 else 100)
            disposal_methods.append(int(getattr(image, "disposal_method", 0) or 0))

        if raw_loop is None:
            embedded_play_count: int | str = 1
        else:
            try:
                loop_count = int(raw_loop)
            except (TypeError, ValueError):
                loop_count = 0
            embedded_play_count = FOREVER if loop_count == 0 else loop_count + 1

    return GifInfo(
        frame_count=frame_count,
        durations_ms=tuple(durations),
        embedded_play_count=embedded_play_count,
        disposal_methods=tuple(disposal_methods),
    )


def install_image_asset(
    pages_directory: str | Path,
    slot: str,
    source_path: str | Path,
) -> dict[str, Any]:
    pages = Path(pages_directory)
    source = Path(source_path)
    definition = _slot_definition(slot)
    basename = str(definition["basename"])
    png_path = pages / f"{basename}.png"
    gif_path = pages / f"{basename}.gif"
    thumbnail_path = pages / f"{basename}.thumbnail.gif"
    pages.mkdir(parents=True, exist_ok=True)
    manifest = load_image_manifest(pages)

    animated_gif = False
    gif_info: GifInfo | None = None
    import_source_sha256: str | None = None
    reusable: dict[str, Any] | None = None
    if source.suffix.casefold() == ".gif":
        import_source_sha256 = _file_sha256(source)
        reusable = _reuse_processed_animation(
            pages,
            manifest,
            slot,
            import_source_sha256,
            gif_path,
            png_path,
            thumbnail_path,
        )
        if reusable is None:
            gif_info = inspect_gif(source)
            animated_gif = gif_info.frame_count > 1
        else:
            animated_gif = True

    if animated_gif:
        if reusable is None:
            if gif_info is None:
                raise ValueError("Animated GIF metadata is missing.")
            gif_info, processed_size = _normalize_animated_gif(
                source,
                gif_path,
                png_path,
                thumbnail_path,
                gif_info,
            )
            gif_data = {
                "frame_count": gif_info.frame_count,
                "embedded_play_count": gif_info.embedded_play_count,
            }
            processing = _gif_processing_metadata(processed_size)
        else:
            gif_data = dict(reusable["gif"])
            processing = dict(reusable["processing"])
        record: dict[str, Any] = {
            "asset_type": "animated_gif",
            "is_animated_gif": True,
            "source_file": gif_path.name,
            "preview_file": png_path.name,
            "thumbnail_file": thumbnail_path.name,
            "settings": default_gif_settings(),
            "gif": gif_data,
            "source_fingerprint": _source_fingerprint(gif_path),
            "processing": processing,
            "import_source_sha256": import_source_sha256,
        }
    else:
        _write_static_png(source, png_path)
        gif_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        record = {
            "asset_type": "static",
            "is_animated_gif": False,
            "source_file": png_path.name,
            "preview_file": png_path.name,
        }

    _remove_slot_frames(pages, slot)
    manifest["slots"][slot] = record
    write_image_manifest(pages, manifest)
    if animated_gif:
        gif_data = record.get("gif", {})
        _LOGGER.info(
            "Animated image installed: slot=%s frames=%s reused=%s.",
            slot,
            gif_data.get("frame_count", "unknown"),
            reusable is not None,
        )
    else:
        _LOGGER.info("Static image installed: slot=%s.", slot)
    return record


def prepare_image_asset_import(
    pages_directory: str | Path,
    slot: str,
    source_path: str | Path,
    *,
    project_pages_directory: str | Path | None = None,
) -> PreparedImageAssetImport:
    """Process one image in private storage and prepare an atomic slot commit."""
    pages = Path(pages_directory).resolve()
    definition = _slot_definition(slot)
    basename = str(definition["basename"])
    work_root = create_staging_directory(
        pages.parent,
        prefix=".image-import-work-",
    )
    work_pages = work_root / "pages"
    changes: list[_PreparedPathChange] = []

    try:
        if pages.is_dir():
            shutil.copytree(
                pages,
                work_pages,
                copy_function=shutil.copyfile,
            )
        else:
            work_pages.mkdir(parents=True)

        record = install_image_asset(
            work_pages,
            slot,
            source_path,
        )
        preview_path: Path | None = None
        workspace_change_count = 0
        target_directories = [pages]
        if project_pages_directory is not None:
            project_pages = Path(project_pages_directory).resolve()
            if project_pages != pages:
                target_directories.append(project_pages)

        for target_pages in target_directories:
            for relative_path in (
                Path(f"{basename}.png"),
                Path(f"{basename}.gif"),
                Path(f"{basename}.thumbnail.gif"),
                Path(GIF_FRAMES_DIRECTORY) / slot,
                Path(IMAGE_MANIFEST_NAME),
            ):
                source = work_pages / relative_path
                target = target_pages / relative_path
                if (
                    relative_path.parts[0] == GIF_FRAMES_DIRECTORY
                    and not source.exists()
                    and not target.exists()
                ):
                    continue
                transaction = PathTransaction(
                    target,
                    staging_suffix=".image-import-staging",
                    backup_suffix=".image-import-backup",
                    unique_staging=True,
                )
                staging = transaction.prepare()
                replace = source.exists()
                if replace:
                    if source.is_dir():
                        shutil.copytree(
                            source,
                            staging,
                            copy_function=shutil.copyfile,
                        )
                    else:
                        atomic_copy_file(source, staging)
                change = _PreparedPathChange(
                    transaction=transaction,
                    replace=replace,
                )
                changes.append(change)
                if (
                    target_pages == pages
                    and relative_path == Path(f"{basename}.png")
                ):
                    preview_path = staging
            if target_pages == pages:
                workspace_change_count = len(changes)

        if preview_path is None or not preview_path.is_file():
            raise ValueError("The prepared image preview is missing.")
        return PreparedImageAssetImport(
            record,
            preview_path,
            tuple(changes),
            workspace_change_count,
        )
    except Exception:
        for change in reversed(changes):
            change.transaction.abort()
        raise
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def update_slot_gif_settings(
    pages_directory: str | Path,
    slot: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    pages = Path(pages_directory)
    manifest = load_image_manifest(pages)
    record = manifest["slots"].get(slot)
    if not isinstance(record, dict) or record.get("asset_type") != "animated_gif":
        raise ValueError("Animation settings require an animated GIF.")
    normalized = normalize_gif_settings(settings)
    record["settings"] = normalized
    manifest["slots"][slot] = record
    write_image_manifest(pages, manifest)
    _LOGGER.info(
        "GIF playback settings updated: slot=%s mode=%s play_count=%s.",
        slot,
        normalized["playback_mode"],
        normalized["play_count"],
    )
    return normalized


def clear_slot_asset(pages_directory: str | Path, slot: str) -> None:
    pages = Path(pages_directory)
    definition = _slot_definition(slot)
    basename = str(definition["basename"])
    manifest = load_image_manifest(pages)
    (pages / f"{basename}.png").unlink(missing_ok=True)
    (pages / f"{basename}.gif").unlink(missing_ok=True)
    (pages / f"{basename}.thumbnail.gif").unlink(missing_ok=True)
    _remove_slot_frames(pages, slot)
    manifest["slots"].pop(slot, None)
    write_image_manifest(pages, manifest)


def load_image_manifest(pages_directory: str | Path) -> dict[str, Any]:
    pages = Path(pages_directory)
    raw_slots = _read_manifest_slots(pages)

    slots: dict[str, dict[str, Any]] = {}
    for slot, definition in SLOT_DEFINITIONS.items():
        basename = str(definition["basename"])
        png_path = pages / f"{basename}.png"
        gif_path = pages / f"{basename}.gif"
        thumbnail_path = pages / f"{basename}.thumbnail.gif"
        raw_record = raw_slots.get(slot)

        if (
            isinstance(raw_record, dict)
            and raw_record.get("asset_type") == "animated_gif"
            and gif_path.is_file()
            and png_path.is_file()
        ):
            record = dict(raw_record)
            record.update(
                {
                    "asset_type": "animated_gif",
                    "is_animated_gif": True,
                    "source_file": gif_path.name,
                    "preview_file": png_path.name,
                    "thumbnail_file": (
                        thumbnail_path.name
                        if thumbnail_path.is_file()
                        else png_path.name
                    ),
                    "settings": normalize_gif_settings(raw_record.get("settings")),
                }
            )
            slots[slot] = record
            continue

        if not isinstance(raw_record, dict) and gif_path.is_file() and png_path.is_file():
            try:
                info = inspect_gif(gif_path)
            except (OSError, ValueError):
                info = None
            if info is not None and info.frame_count > 1:
                slots[slot] = {
                    "asset_type": "animated_gif",
                    "is_animated_gif": True,
                    "source_file": gif_path.name,
                    "preview_file": png_path.name,
                    "settings": default_gif_settings(),
                    "gif": {
                        "frame_count": info.frame_count,
                        "embedded_play_count": info.embedded_play_count,
                    },
                }
                continue

        if png_path.is_file():
            slots[slot] = {
                "asset_type": "static",
                "is_animated_gif": False,
                "source_file": png_path.name,
                "preview_file": png_path.name,
            }

    return {
        "schema_version": IMAGE_MANIFEST_VERSION,
        "slots": slots,
    }


def reconcile_external_image_assets(
    pages_directory: str | Path,
) -> bool:
    """Adopt GIFs copied directly into the canonical pages directory."""
    pages = Path(pages_directory)
    raw_slots = _read_manifest_slots(pages)
    manifest = load_image_manifest(pages)
    changed = False

    for slot, definition in SLOT_DEFINITIONS.items():
        basename = str(definition["basename"])
        gif_path = pages / f"{basename}.gif"
        if not gif_path.is_file() or gif_path.is_symlink():
            continue

        png_path = pages / f"{basename}.png"
        thumbnail_path = pages / f"{basename}.thumbnail.gif"
        raw_record = raw_slots.get(slot)
        raw_record = raw_record if isinstance(raw_record, dict) else {}
        fingerprint = _source_fingerprint(gif_path)
        already_current = (
            raw_record.get("asset_type") == "animated_gif"
            and raw_record.get("source_fingerprint") == fingerprint
            and png_path.is_file()
            and thumbnail_path.is_file()
            and raw_record.get("processing")
            == _gif_processing_metadata_from_record(raw_record)
        )
        if already_current:
            continue

        try:
            import_source_sha256 = _file_sha256(gif_path)
            reusable = _reuse_processed_animation(
                pages,
                manifest,
                slot,
                import_source_sha256,
                gif_path,
                png_path,
                thumbnail_path,
            )
            settings = (
                normalize_gif_settings(raw_record.get("settings"))
                if raw_record.get("asset_type") == "animated_gif"
                else default_gif_settings()
            )
            if reusable is not None:
                manifest["slots"][slot] = {
                    "asset_type": "animated_gif",
                    "is_animated_gif": True,
                    "source_file": gif_path.name,
                    "preview_file": png_path.name,
                    "thumbnail_file": thumbnail_path.name,
                    "settings": settings,
                    "gif": dict(reusable["gif"]),
                    "source_fingerprint": _source_fingerprint(gif_path),
                    "processing": dict(reusable["processing"]),
                    "import_source_sha256": import_source_sha256,
                }
            else:
                info = inspect_gif(gif_path)
                if info.frame_count > 1:
                    info, processed_size = _normalize_animated_gif(
                        gif_path,
                        gif_path,
                        png_path,
                        thumbnail_path,
                        info,
                    )
                    manifest["slots"][slot] = {
                        "asset_type": "animated_gif",
                        "is_animated_gif": True,
                        "source_file": gif_path.name,
                        "preview_file": png_path.name,
                        "thumbnail_file": thumbnail_path.name,
                        "settings": settings,
                        "gif": {
                            "frame_count": info.frame_count,
                            "embedded_play_count": info.embedded_play_count,
                        },
                        "source_fingerprint": _source_fingerprint(gif_path),
                        "processing": _gif_processing_metadata(processed_size),
                        "import_source_sha256": import_source_sha256,
                    }
                else:
                    _write_static_png(
                        gif_path,
                        png_path,
                        max_dimension=GIF_VIEWER_MAX_DIMENSION,
                    )
                    gif_path.unlink()
                    thumbnail_path.unlink(missing_ok=True)
                    manifest["slots"][slot] = {
                        "asset_type": "static",
                        "is_animated_gif": False,
                        "source_file": png_path.name,
                        "preview_file": png_path.name,
                    }
        except (OSError, ValueError):
            continue

        _remove_slot_frames(pages, slot)
        changed = True

    if changed:
        write_image_manifest(pages, manifest)
    return changed


def write_image_manifest(
    pages_directory: str | Path,
    manifest: Mapping[str, Any],
) -> Path:
    pages = Path(pages_directory)
    pages.mkdir(parents=True, exist_ok=True)
    slots = manifest.get("slots", {}) if isinstance(manifest, Mapping) else {}
    payload = {
        "schema_version": IMAGE_MANIFEST_VERSION,
        "slots": dict(slots) if isinstance(slots, Mapping) else {},
    }
    path = pages / IMAGE_MANIFEST_NAME
    set_path_hidden(path, False)
    try:
        return atomic_write_json(path, payload)
    finally:
        set_path_hidden(path)


def build_runtime_image_assets(
    source_pages_directory: str | Path,
    destination_pages_directory: str | Path,
    *,
    reconcile_source: bool = True,
    reuse_pages_directory: str | Path | None = None,
    fallback_page_sources: Mapping[str, str | Path] | None = None,
    copy_file: Callable[[Path, Path], object] = atomic_copy_file,
) -> RuntimeImageAssets:
    source_pages = Path(source_pages_directory)
    destination_pages = Path(destination_pages_directory)
    destination_pages.mkdir(parents=True, exist_ok=True)
    if reconcile_source:
        reconcile_external_image_assets(source_pages)
    source_manifest = load_image_manifest(source_pages)
    reuse_pages = (
        Path(reuse_pages_directory)
        if reuse_pages_directory is not None
        else None
    )
    reuse_manifest = (
        load_image_manifest(reuse_pages)
        if reuse_pages is not None and reuse_pages.is_dir()
        else None
    )
    runtime_slots: dict[str, dict[str, Any]] = {}
    page_sources: dict[str, str] = {}
    animations: dict[str, dict[str, Any]] = {}
    fallback_sources = dict(fallback_page_sources or {})

    for slot, definition in SLOT_DEFINITIONS.items():
        basename = str(definition["basename"])
        slide_index = int(definition["index"]) - 1
        source_png = source_pages / f"{basename}.png"
        using_fallback = False
        if not source_png.is_file():
            fallback = Path(fallback_sources.get(slot, ""))
            if not fallback.is_file():
                raise FileNotFoundError(f"Missing required page image: {source_png}")
            source_png = fallback
            using_fallback = True
        destination_png = destination_pages / f"{basename}.png"
        copy_file(source_png, destination_png)

        record = {} if using_fallback else source_manifest["slots"].get(slot, {})
        if isinstance(record, dict) and record.get("asset_type") == "animated_gif":
            source_gif = source_pages / f"{basename}.gif"
            if not source_gif.is_file():
                raise FileNotFoundError(
                    f"The {definition['label']} GIF source is missing: {source_gif}"
                )
            destination_gif = destination_pages / source_gif.name
            copy_file(source_gif, destination_gif)
            settings = normalize_gif_settings(record.get("settings"))
            if _uses_native_gif_playback(settings):
                gif_data = record.get("gif")
                gif_data = gif_data if isinstance(gif_data, Mapping) else {}
                try:
                    frame_count = int(gif_data.get("frame_count", 0))
                except (TypeError, ValueError):
                    frame_count = 0
                if frame_count <= 1:
                    raise ValueError(f"The {definition['label']} is not an animated GIF.")
                embedded_play_count = gif_data.get("embedded_play_count", FOREVER)
                runtime_record = {
                    "asset_type": "animated_gif",
                    "is_animated_gif": True,
                    "source_file": destination_gif.name,
                    "preview_file": destination_png.name,
                    "settings": settings,
                    "gif": {
                        "render_mode": "native_gif",
                        "frame_count": frame_count,
                        "embedded_play_count": embedded_play_count,
                    },
                }
                page_sources[slot] = f"gallery/pages/{destination_png.name}"
                animations[str(slide_index)] = {
                    "slot": slot,
                    "render_mode": "native_gif",
                    "source": f"gallery/pages/{destination_gif.name}",
                    "preview_source": f"gallery/pages/{destination_png.name}",
                    "embedded_play_count": embedded_play_count,
                    **settings,
                }
            else:
                frame_cache_key = _runtime_frame_cache_key(
                    record,
                    source_gif,
                )
                reused = _reuse_runtime_gif_frames(
                    reuse_pages,
                    reuse_manifest,
                    destination_pages,
                    slot,
                    frame_cache_key,
                )
                if reused is None:
                    info = inspect_gif(source_gif)
                    if info.frame_count <= 1:
                        raise ValueError(
                            f"The {definition['label']} is not an animated GIF."
                        )
                    frame_paths = _extract_gif_frames(
                        source_gif,
                        destination_pages / GIF_FRAMES_DIRECTORY / slot,
                        info.frame_count,
                    )
                    gif_runtime_data = {
                        "render_mode": "frames",
                        "frame_count": info.frame_count,
                        "durations_ms": list(info.durations_ms),
                        "embedded_play_count": info.embedded_play_count,
                    }
                else:
                    frame_paths, gif_runtime_data = reused
                relative_frames = [
                    path.relative_to(destination_pages).as_posix()
                    for path in frame_paths
                ]
                gif_runtime_data["frames"] = relative_frames
                runtime_record = {
                    "asset_type": "animated_gif",
                    "is_animated_gif": True,
                    "source_file": destination_gif.name,
                    "preview_file": destination_png.name,
                    "settings": settings,
                    "gif": gif_runtime_data,
                    "frame_cache_key": frame_cache_key,
                }
                frame_sources = [f"gallery/pages/{path}" for path in relative_frames]
                page_sources[slot] = frame_sources[0]
                animations[str(slide_index)] = {
                    "slot": slot,
                    "render_mode": "frames",
                    "frames": frame_sources,
                    "durations_ms": list(gif_runtime_data["durations_ms"]),
                    "embedded_play_count": gif_runtime_data[
                        "embedded_play_count"
                    ],
                    **settings,
                }
        else:
            runtime_record = {
                "asset_type": "static",
                "is_animated_gif": False,
                "source_file": destination_png.name,
                "preview_file": destination_png.name,
            }
            page_sources[slot] = f"gallery/pages/{destination_png.name}"

        runtime_slots[slot] = runtime_record

    runtime_manifest = {
        "schema_version": IMAGE_MANIFEST_VERSION,
        "slots": runtime_slots,
    }
    write_image_manifest(destination_pages, runtime_manifest)
    _LOGGER.info(
        "Runtime image assets built: animated_slots=%d.",
        len(animations),
    )
    return RuntimeImageAssets(
        page_sources=page_sources,
        animations=animations,
        manifest=runtime_manifest,
    )


def validate_runtime_image_manifest(pages_directory: str | Path) -> dict[str, Any]:
    pages = Path(pages_directory)
    path = pages / IMAGE_MANIFEST_NAME
    set_path_hidden(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("The generated image manifest is unreadable.") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != IMAGE_MANIFEST_VERSION:
        raise ValueError("The generated image manifest is invalid.")
    slots = manifest.get("slots")
    if not isinstance(slots, dict):
        raise ValueError("The generated image manifest has no image slots.")

    for slot, definition in SLOT_DEFINITIONS.items():
        record = slots.get(slot)
        if not isinstance(record, dict):
            raise ValueError(f"The generated {definition['label']} record is missing.")
        for field in ("source_file", "preview_file"):
            filename = str(record.get(field, ""))
            if Path(filename).name != filename or not (pages / filename).is_file():
                raise ValueError(
                    f"The generated {definition['label']} {field} is missing."
                )
        if record.get("asset_type") != "animated_gif":
            if record.get("asset_type") != "static":
                raise ValueError(f"The generated {definition['label']} type is invalid.")
            if Path(str(record["source_file"])).suffix.casefold() != ".png":
                raise ValueError(f"The generated {definition['label']} static source is invalid.")
            continue

        if Path(str(record["source_file"])).suffix.casefold() != ".gif":
            raise ValueError(f"The generated {definition['label']} GIF source is invalid.")
        raw_settings = record.get("settings")
        if not isinstance(raw_settings, Mapping):
            raise ValueError(f"The generated {definition['label']} settings are invalid.")
        compatible_settings = dict(raw_settings)
        compatible_settings.setdefault("animation_enabled", True)
        compatible_settings.setdefault("speed_percent", 100)
        settings = normalize_gif_settings(raw_settings)
        if settings != compatible_settings:
            raise ValueError(f"The generated {definition['label']} settings are invalid.")
        record["settings"] = settings
        gif = record.get("gif")
        if not isinstance(gif, dict):
            raise ValueError(f"The generated {definition['label']} GIF data is missing.")
        try:
            frame_count = int(gif.get("frame_count", 0))
        except (TypeError, ValueError):
            frame_count = 0
        if frame_count < 2:
            raise ValueError(f"The generated {definition['label']} frame count is invalid.")
        embedded_count = gif.get("embedded_play_count")
        if embedded_count != FOREVER:
            try:
                embedded_count = int(embedded_count)
            except (TypeError, ValueError):
                embedded_count = 0
            if embedded_count < 1:
                raise ValueError(
                    f"The generated {definition['label']} embedded play count is invalid."
                )
        render_mode = str(gif.get("render_mode", "frames"))
        if render_mode == "native_gif":
            continue
        if render_mode != "frames":
            raise ValueError(f"The generated {definition['label']} render mode is invalid.")
        frames = gif.get("frames")
        durations = gif.get("durations_ms")
        if (
            not isinstance(frames, list)
            or len(frames) < 2
            or not isinstance(durations, list)
            or len(durations) != len(frames)
            or any(
                not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration <= 0
                for duration in durations
            )
        ):
            raise ValueError(f"The generated {definition['label']} frames are invalid.")
        if frame_count != len(frames):
            raise ValueError(f"The generated {definition['label']} frame count is invalid.")
        for relative in frames:
            relative_path = Path(str(relative))
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or not (pages / relative_path).is_file()
            ):
                raise ValueError(f"A generated {definition['label']} frame is missing.")
    return manifest


def _slot_definition(slot: str) -> Mapping[str, Any]:
    try:
        return SLOT_DEFINITIONS[slot]
    except KeyError as error:
        raise ValueError(f"Unknown image slot: {slot}") from error


def _bounded_milliseconds(value: Any) -> int:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        milliseconds = 0
    return max(0, min(MAX_DELAY_MS, milliseconds))


def _uses_native_gif_playback(settings: Mapping[str, Any]) -> bool:
    return (
        settings.get("animation_enabled") is True
        and settings.get("speed_percent") == 100
        and settings.get("playback_mode") == "original"
        and settings.get("play_count") == FOREVER
        and settings.get("start_delay_ms") == 0
        and settings.get("loop_pause_ms") == 0
    )


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    mode = "RGBA" if image.mode == "RGBA" else "RGB"
    image.convert(mode).save(output, format="PNG", optimize=True)
    return output.getvalue()


def _write_static_png(
    source: Path,
    destination: Path,
    *,
    max_dimension: int | None = None,
) -> None:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        if image.mode not in ("RGB", "RGBA"):
            try:
                image = image.convert("RGBA")
            except Exception:
                image = image.convert("RGB")
        if max_dimension is not None:
            image.thumbnail(
                (max_dimension, max_dimension),
                Image.Resampling.LANCZOS,
            )
        atomic_write_bytes(destination, _png_bytes(image))


def _write_first_frame_png(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image.seek(0)
        atomic_write_bytes(destination, _png_bytes(image.convert("RGBA")))


def _normalize_animated_gif(
    source: Path,
    destination: Path,
    preview_path: Path,
    thumbnail_path: Path,
    info: GifInfo,
) -> tuple[GifInfo, tuple[int, int]]:
    with Image.open(source) as image:
        source_size = tuple(int(value) for value in image.size)

    if max(source_size) > GIF_VIEWER_MAX_DIMENSION:
        _write_resized_gif(source, destination, info)
    else:
        atomic_copy_file(source, destination)

    with Image.open(destination) as image:
        processed_size = tuple(int(value) for value in image.size)
    processed_info = inspect_gif(destination)
    _write_first_frame_png(destination, preview_path)
    if max(processed_size) > GIF_THUMBNAIL_MAX_DIMENSION:
        _write_resized_gif(
            destination,
            thumbnail_path,
            processed_info,
            max_dimension=GIF_THUMBNAIL_MAX_DIMENSION,
        )
    else:
        atomic_copy_file(destination, thumbnail_path)
    return processed_info, processed_size


def _write_resized_gif(
    source: Path,
    destination: Path,
    info: GifInfo,
    *,
    max_dimension: int = GIF_VIEWER_MAX_DIMENSION,
) -> None:
    output = BytesIO()
    with Image.open(source) as image:
        def resized_frame(frame_index: int) -> Image.Image:
            image.seek(frame_index)
            frame = image.convert("RGBA")
            frame.thumbnail(
                (max_dimension, max_dimension),
                Image.Resampling.LANCZOS,
            )
            return frame

        first = resized_frame(0)
        options: dict[str, Any] = {
            "format": "GIF",
            "save_all": True,
            "append_images": (
                resized_frame(index)
                for index in range(1, info.frame_count)
            ),
            "duration": list(info.durations_ms),
            "disposal": list(info.disposal_methods),
            "optimize": True,
        }
        if info.embedded_play_count == FOREVER:
            options["loop"] = 0
        elif int(info.embedded_play_count) > 1:
            options["loop"] = int(info.embedded_play_count) - 1
        first.save(output, **options)
    atomic_write_bytes(destination, output.getvalue())


def _gif_processing_metadata(size: tuple[int, int]) -> dict[str, int]:
    return {
        "version": GIF_PROCESSING_VERSION,
        "max_dimension": GIF_VIEWER_MAX_DIMENSION,
        "thumbnail_max_dimension": GIF_THUMBNAIL_MAX_DIMENSION,
        "width": int(size[0]),
        "height": int(size[1]),
    }


def _gif_processing_metadata_from_record(
    record: Mapping[str, Any],
) -> dict[str, int]:
    processing = record.get("processing")
    if not isinstance(processing, Mapping):
        return {}
    try:
        size = (int(processing.get("width", 0)), int(processing.get("height", 0)))
    except (TypeError, ValueError):
        return {}
    return _gif_processing_metadata(size)


def _reuse_processed_animation(
    pages: Path,
    manifest: Mapping[str, Any],
    slot: str,
    import_source_sha256: str,
    gif_path: Path,
    preview_path: Path,
    thumbnail_path: Path,
) -> dict[str, Any] | None:
    slots = manifest.get("slots") if isinstance(manifest, Mapping) else None
    if not isinstance(slots, Mapping):
        return None
    for candidate_slot, candidate in slots.items():
        if candidate_slot == slot or not isinstance(candidate, Mapping):
            continue
        if (
            candidate.get("asset_type") != "animated_gif"
            or candidate.get("import_source_sha256") != import_source_sha256
            or candidate.get("processing")
            != _gif_processing_metadata_from_record(candidate)
        ):
            continue
        gif = candidate.get("gif")
        processing = candidate.get("processing")
        if not isinstance(gif, Mapping) or not isinstance(processing, Mapping):
            continue
        try:
            if int(gif.get("frame_count", 0)) < 2:
                continue
        except (TypeError, ValueError):
            continue
        candidate_gif = pages / str(candidate.get("source_file", ""))
        candidate_preview = pages / str(candidate.get("preview_file", ""))
        candidate_thumbnail = pages / str(candidate.get("thumbnail_file", ""))
        if not all(
            path.is_file() and path.parent.resolve() == pages.resolve()
            for path in (candidate_gif, candidate_preview, candidate_thumbnail)
        ):
            continue
        atomic_copy_file(candidate_gif, gif_path)
        atomic_copy_file(candidate_preview, preview_path)
        atomic_copy_file(candidate_thumbnail, thumbnail_path)
        return {
            "gif": dict(gif),
            "processing": dict(processing),
        }
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest_slots(pages: Path) -> Mapping[str, Any]:
    path = pages / IMAGE_MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        return {}
    set_path_hidden(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _backup_invalid_image_manifest(path)
        return {}
    if not isinstance(payload, dict):
        _backup_invalid_image_manifest(path)
        return {}
    raw_version = payload.get("schema_version", 0)
    try:
        source_version = (
            -1
            if isinstance(raw_version, bool)
            else int(raw_version)
        )
    except (TypeError, ValueError):
        source_version = -1
    if source_version > IMAGE_MANIFEST_VERSION:
        raise UnsupportedImageManifestSchemaError(
            "Image settings were written by a newer Letter Smith version "
            f"(schema {source_version})."
        )
    if source_version < 0:
        _backup_invalid_image_manifest(path)
        return {}
    slots = payload.get("slots")
    if slots is not None and not isinstance(slots, dict):
        _backup_invalid_image_manifest(path)
        return {}
    return slots if isinstance(slots, dict) else {}


def _source_fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def image_asset_revision(pages_directory: str | Path) -> str:
    """Fingerprint live image assets while ignoring private import sidecars."""
    pages = Path(pages_directory)
    digest = hashlib.sha256()
    if not pages.is_dir():
        return digest.hexdigest()
    for path in sorted(pages.rglob("*"), key=lambda value: str(value).casefold()):
        relative_path = path.relative_to(pages)
        if not path.is_file() or any(
            marker in part
            for part in relative_path.parts
            for marker in _IMAGE_IMPORT_SIDECARS
        ):
            continue
        try:
            stat = path.stat()
            revision = file_change_token(path, stat_result=stat)
        except OSError:
            return "changed"
        digest.update(
            relative_path.as_posix().encode(
                "utf-8",
                errors="surrogatepass",
            )
        )
        digest.update(b"\0")
        digest.update(
            f"{stat.st_size}:{stat.st_mtime_ns}:{revision}".encode("ascii")
        )
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_frame_cache_key(
    source_record: Mapping[str, Any],
    source_gif: Path,
) -> dict[str, Any]:
    source_sha256 = str(
        source_record.get("import_source_sha256", "")
    ).strip()
    if not source_sha256:
        source_sha256 = _file_sha256(source_gif)
    processing = source_record.get("processing")
    return {
        "version": RUNTIME_FRAME_CACHE_VERSION,
        "source_sha256": source_sha256,
        "processed_source": _source_fingerprint(source_gif),
        "processing": dict(processing) if isinstance(processing, Mapping) else {},
    }


def _reuse_runtime_gif_frames(
    reuse_pages: Path | None,
    reuse_manifest: Mapping[str, Any] | None,
    destination_pages: Path,
    slot: str,
    cache_key: Mapping[str, Any],
) -> tuple[tuple[Path, ...], dict[str, Any]] | None:
    if reuse_pages is None or not isinstance(reuse_manifest, Mapping):
        return None
    slots = reuse_manifest.get("slots")
    previous = slots.get(slot) if isinstance(slots, Mapping) else None
    if (
        not isinstance(previous, Mapping)
        or previous.get("frame_cache_key") != cache_key
    ):
        return None
    gif_data = previous.get("gif")
    if not isinstance(gif_data, Mapping) or gif_data.get("render_mode") != "frames":
        return None
    frames = gif_data.get("frames")
    durations = gif_data.get("durations_ms")
    try:
        frame_count = int(gif_data.get("frame_count", 0))
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(frames, list)
        or not isinstance(durations, list)
        or frame_count < 2
        or len(frames) != frame_count
        or len(durations) != frame_count
    ):
        return None
    try:
        normalized_durations = [int(value) for value in durations]
    except (TypeError, ValueError):
        return None
    if any(value <= 0 for value in normalized_durations):
        return None

    reuse_root = reuse_pages.resolve()
    expected_parent = Path(GIF_FRAMES_DIRECTORY) / slot
    destination_directory = destination_pages / expected_parent
    destination_directory.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for raw_relative in frames:
            relative = Path(str(raw_relative))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.parent != expected_parent
                or relative.suffix.casefold() != ".png"
            ):
                raise ValueError("Cached GIF frame path is invalid.")
            source_candidate = reuse_root / relative
            if source_candidate.is_symlink():
                raise ValueError("Cached GIF frame cannot be a symlink.")
            source = source_candidate.resolve()
            source.relative_to(reuse_root)
            if not source.is_file():
                raise FileNotFoundError(source)
            with Image.open(source) as cached_frame:
                if cached_frame.format != "PNG":
                    raise ValueError("Cached GIF frame is not a PNG image.")
                cached_frame.verify()
            destination = destination_pages / relative
            atomic_copy_file(source, destination)
            copied.append(destination)
    except (OSError, ValueError):
        if destination_directory.is_dir():
            shutil.rmtree(destination_directory)
        return None

    return (
        tuple(copied),
        {
            "render_mode": "frames",
            "frame_count": frame_count,
            "durations_ms": normalized_durations,
            "embedded_play_count": gif_data.get(
                "embedded_play_count",
                FOREVER,
            ),
        },
    )


def _extract_gif_frames(
    source: Path,
    destination: Path,
    frame_count: int,
) -> tuple[Path, ...]:
    if destination.is_dir():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with Image.open(source) as image:
        for frame_index in range(frame_count):
            image.seek(frame_index)
            path = destination / f"frame-{frame_index:05d}.png"
            atomic_write_bytes(path, _png_bytes(image.convert("RGBA")))
            paths.append(path)
    return tuple(paths)


def _remove_slot_frames(pages: Path, slot: str) -> None:
    frames = pages / GIF_FRAMES_DIRECTORY / slot
    if frames.is_dir():
        shutil.rmtree(frames)
    parent = frames.parent
    if parent.is_dir():
        try:
            next(parent.iterdir())
        except StopIteration:
            parent.rmdir()


__all__ = [
    "FOREVER",
    "GIF_PROCESSING_VERSION",
    "GIF_THUMBNAIL_MAX_DIMENSION",
    "GIF_VIEWER_MAX_DIMENSION",
    "GIF_FRAMES_DIRECTORY",
    "PreparedImageAssetImport",
    "IMAGE_MANIFEST_NAME",
    "IMAGE_MANIFEST_VERSION",
    "INDEX_TO_SLOT",
    "MAX_DELAY_MS",
    "MAX_PLAY_COUNT",
    "PLAYBACK_MODES",
    "SLOT_DEFINITIONS",
    "GifInfo",
    "RuntimeImageAssets",
    "UnsupportedImageManifestSchemaError",
    "build_runtime_image_assets",
    "clear_slot_asset",
    "default_gif_settings",
    "image_asset_revision",
    "inspect_gif",
    "install_image_asset",
    "load_image_manifest",
    "normalize_gif_settings",
    "prepare_image_asset_import",
    "reconcile_external_image_assets",
    "update_slot_gif_settings",
    "validate_runtime_image_manifest",
    "write_image_manifest",
]
