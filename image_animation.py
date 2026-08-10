from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from PIL import Image, ImageOps

from transactional_io import atomic_write_bytes, atomic_write_json


IMAGE_MANIFEST_NAME = "lettersmith-images.json"
IMAGE_MANIFEST_VERSION = 1
GIF_FRAMES_DIRECTORY = "gif_frames"
PLAYBACK_MODES = ("original", "loop", "ping_pong")
FOREVER = "forever"
MAX_PLAY_COUNT = 9999
MAX_DELAY_MS = 86_400_000

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


@dataclass(frozen=True)
class RuntimeImageAssets:
    page_sources: dict[str, str]
    animations: dict[str, dict[str, Any]]
    manifest: dict[str, Any]


def default_gif_settings() -> dict[str, Any]:
    return {
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
        for frame_index in range(frame_count):
            image.seek(frame_index)
            duration = image.info.get("duration", 100)
            try:
                duration_ms = int(duration)
            except (TypeError, ValueError):
                duration_ms = 100
            durations.append(duration_ms if duration_ms > 0 else 100)

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
    pages.mkdir(parents=True, exist_ok=True)

    animated_gif = False
    gif_info: GifInfo | None = None
    if source.suffix.casefold() == ".gif":
        gif_info = inspect_gif(source)
        animated_gif = gif_info.frame_count > 1

    if animated_gif and gif_info is not None:
        original_bytes = source.read_bytes()
        atomic_write_bytes(gif_path, original_bytes)
        _write_first_frame_png(source, png_path)
        record: dict[str, Any] = {
            "asset_type": "animated_gif",
            "is_animated_gif": True,
            "source_file": gif_path.name,
            "preview_file": png_path.name,
            "settings": default_gif_settings(),
            "gif": {
                "frame_count": gif_info.frame_count,
                "embedded_play_count": gif_info.embedded_play_count,
            },
        }
    else:
        _write_static_png(source, png_path)
        gif_path.unlink(missing_ok=True)
        record = {
            "asset_type": "static",
            "is_animated_gif": False,
            "source_file": png_path.name,
            "preview_file": png_path.name,
        }

    _remove_slot_frames(pages, slot)
    manifest = load_image_manifest(pages)
    manifest["slots"][slot] = record
    write_image_manifest(pages, manifest)
    return record


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
    return normalized


def clear_slot_asset(pages_directory: str | Path, slot: str) -> None:
    pages = Path(pages_directory)
    definition = _slot_definition(slot)
    basename = str(definition["basename"])
    (pages / f"{basename}.png").unlink(missing_ok=True)
    (pages / f"{basename}.gif").unlink(missing_ok=True)
    _remove_slot_frames(pages, slot)
    manifest = load_image_manifest(pages)
    manifest["slots"].pop(slot, None)
    write_image_manifest(pages, manifest)


def load_image_manifest(pages_directory: str | Path) -> dict[str, Any]:
    pages = Path(pages_directory)
    raw_slots: Mapping[str, Any] = {}
    path = pages / IMAGE_MANIFEST_NAME
    if path.is_file() and not path.is_symlink():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("slots"), dict):
            raw_slots = payload["slots"]

    slots: dict[str, dict[str, Any]] = {}
    for slot, definition in SLOT_DEFINITIONS.items():
        basename = str(definition["basename"])
        png_path = pages / f"{basename}.png"
        gif_path = pages / f"{basename}.gif"
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
    return atomic_write_json(pages / IMAGE_MANIFEST_NAME, payload)


def build_runtime_image_assets(
    source_pages_directory: str | Path,
    destination_pages_directory: str | Path,
) -> RuntimeImageAssets:
    source_pages = Path(source_pages_directory)
    destination_pages = Path(destination_pages_directory)
    destination_pages.mkdir(parents=True, exist_ok=True)
    source_manifest = load_image_manifest(source_pages)
    runtime_slots: dict[str, dict[str, Any]] = {}
    page_sources: dict[str, str] = {}
    animations: dict[str, dict[str, Any]] = {}

    for slot, definition in SLOT_DEFINITIONS.items():
        basename = str(definition["basename"])
        slide_index = int(definition["index"]) - 1
        source_png = source_pages / f"{basename}.png"
        if not source_png.is_file():
            raise FileNotFoundError(f"Missing required page image: {source_png}")
        destination_png = destination_pages / source_png.name
        atomic_write_bytes(destination_png, source_png.read_bytes())

        record = source_manifest["slots"].get(slot, {})
        if isinstance(record, dict) and record.get("asset_type") == "animated_gif":
            source_gif = source_pages / f"{basename}.gif"
            if not source_gif.is_file():
                raise FileNotFoundError(
                    f"The {definition['label']} GIF source is missing: {source_gif}"
                )
            info = inspect_gif(source_gif)
            if info.frame_count <= 1:
                raise ValueError(f"The {definition['label']} is not an animated GIF.")
            destination_gif = destination_pages / source_gif.name
            atomic_write_bytes(destination_gif, source_gif.read_bytes())
            frame_paths = _extract_gif_frames(
                source_gif,
                destination_pages / GIF_FRAMES_DIRECTORY / slot,
                info.frame_count,
            )
            relative_frames = [
                path.relative_to(destination_pages).as_posix()
                for path in frame_paths
            ]
            settings = normalize_gif_settings(record.get("settings"))
            runtime_record = {
                "asset_type": "animated_gif",
                "is_animated_gif": True,
                "source_file": destination_gif.name,
                "preview_file": destination_png.name,
                "settings": settings,
                "gif": {
                    "frame_count": info.frame_count,
                    "durations_ms": list(info.durations_ms),
                    "embedded_play_count": info.embedded_play_count,
                    "frames": relative_frames,
                },
            }
            frame_sources = [f"gallery/pages/{path}" for path in relative_frames]
            page_sources[slot] = frame_sources[0]
            animations[str(slide_index)] = {
                "slot": slot,
                "frames": frame_sources,
                "durations_ms": list(info.durations_ms),
                "embedded_play_count": info.embedded_play_count,
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
    return RuntimeImageAssets(
        page_sources=page_sources,
        animations=animations,
        manifest=runtime_manifest,
    )


def validate_runtime_image_manifest(pages_directory: str | Path) -> dict[str, Any]:
    pages = Path(pages_directory)
    path = pages / IMAGE_MANIFEST_NAME
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
        settings = normalize_gif_settings(record.get("settings"))
        if settings != record.get("settings"):
            raise ValueError(f"The generated {definition['label']} settings are invalid.")
        gif = record.get("gif")
        if not isinstance(gif, dict):
            raise ValueError(f"The generated {definition['label']} GIF data is missing.")
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
        try:
            frame_count = int(gif.get("frame_count", 0))
        except (TypeError, ValueError):
            frame_count = 0
        if frame_count != len(frames):
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


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    mode = "RGBA" if image.mode == "RGBA" else "RGB"
    image.convert(mode).save(output, format="PNG", optimize=True)
    return output.getvalue()


def _write_static_png(source: Path, destination: Path) -> None:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        if image.mode not in ("RGB", "RGBA"):
            try:
                image = image.convert("RGBA")
            except Exception:
                image = image.convert("RGB")
        atomic_write_bytes(destination, _png_bytes(image))


def _write_first_frame_png(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image.seek(0)
        atomic_write_bytes(destination, _png_bytes(image.convert("RGBA")))


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
    "GIF_FRAMES_DIRECTORY",
    "IMAGE_MANIFEST_NAME",
    "IMAGE_MANIFEST_VERSION",
    "INDEX_TO_SLOT",
    "MAX_DELAY_MS",
    "MAX_PLAY_COUNT",
    "PLAYBACK_MODES",
    "SLOT_DEFINITIONS",
    "GifInfo",
    "RuntimeImageAssets",
    "build_runtime_image_assets",
    "clear_slot_asset",
    "default_gif_settings",
    "inspect_gif",
    "install_image_asset",
    "load_image_manifest",
    "normalize_gif_settings",
    "update_slot_gif_settings",
    "validate_runtime_image_manifest",
    "write_image_manifest",
]
