from __future__ import annotations

import colorsys
import math
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageOps

RGB = tuple[int, int, int]

FALLBACK_CURTAIN_RGB: RGB = (255, 255, 255)
_MAX_SAMPLE_PIXELS = 30_000
_HUE_BINS = 36
_MIN_ALPHA = 32
_MIN_SATURATION = 0.20
_MIN_VALUE = 0.16
_NEAR_WHITE_VALUE = 0.93
_NEAR_WHITE_SATURATION = 0.38
_NEAR_GRAY_DELTA = 18


def extract_deep_dominant_color(image_path: Path, *, hue_shift: float = 0.0) -> RGB:
    color = _extract_deep_dominant_color(Path(image_path), hue_shift=hue_shift)
    return color if color is not None else FALLBACK_CURTAIN_RGB


def extract_deep_dominant_color_from_images(
    image_paths: Iterable[Path],
    *,
    hue_shift: float = 0.0,
) -> RGB:
    for image_path in image_paths:
        color = _extract_deep_dominant_color(Path(image_path), hue_shift=hue_shift)
        if color is not None:
            return color
    return FALLBACK_CURTAIN_RGB


def extract_light_dominant_color_from_images(image_paths: Iterable[Path]) -> RGB:
    hue = _extract_hue_from_images(image_paths)
    return _light_rgb_for_hue(hue) if hue is not None else (250, 248, 249)


def extract_dark_dominant_color_from_images(image_paths: Iterable[Path]) -> RGB:
    hue = _extract_hue_from_images(image_paths)
    return _dark_rgb_for_hue(hue) if hue is not None else (24, 8, 12)


def curtain_rgb_for_style(image_paths: Iterable[Path], style: str) -> RGB:
    """Resolve a curtain tint while preserving the letter's dominant hue family."""
    paths = tuple(Path(path) for path in image_paths)
    normalized = (style or "").strip().casefold().replace(" ", "_")
    if normalized in {"light", "light_curtain", "light_average_color"}:
        return extract_light_dominant_color_from_images(paths)
    if normalized in {"dark", "dark_curtain", "dark_average_color"}:
        return extract_dark_dominant_color_from_images(paths)
    if normalized in {"complementary", "complementary_average_color"}:
        return extract_deep_dominant_color_from_images(paths, hue_shift=0.5)
    return extract_deep_dominant_color_from_images(paths)


def _extract_hue_from_images(image_paths: Iterable[Path]) -> Optional[float]:
    for image_path in image_paths:
        try:
            with Image.open(Path(image_path)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
        except Exception:
            continue
        hue = _dominant_meaningful_hue(_downsample_for_analysis(image))
        if hue is not None:
            return hue
    return None


def write_tinted_curtain_image(source_path: Path, target_path: Path, rgb: RGB) -> None:
    rgb = _clamp_rgb(rgb)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")

    alpha = image.getchannel("A")
    luminance = ImageOps.grayscale(image)

    channels = []
    for channel in rgb:
        lookup = [int(round(channel * (0.42 + 0.58 * (value / 255.0)))) for value in range(256)]
        channels.append(
            luminance.point(lookup)
        )

    tinted = Image.merge("RGBA", (channels[0], channels[1], channels[2], alpha))
    tinted.save(target, format="PNG", optimize=True)


def _extract_deep_dominant_color(image_path: Path, *, hue_shift: float = 0.0) -> Optional[RGB]:
    try:
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except Exception:
        return None

    image = _downsample_for_analysis(image)
    hue = _dominant_meaningful_hue(image)
    if hue is None:
        return None

    return _deep_rgb_for_hue((hue + hue_shift) % 1.0)


def _downsample_for_analysis(image: Image.Image) -> Image.Image:
    width, height = image.size
    total_pixels = max(1, width * height)
    if total_pixels <= _MAX_SAMPLE_PIXELS:
        return image

    scale = math.sqrt(_MAX_SAMPLE_PIXELS / total_pixels)
    target_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    resampling = getattr(getattr(Image, "Resampling", Image), "BOX")
    return image.resize(target_size, resampling)


def _dominant_meaningful_hue(image: Image.Image) -> Optional[float]:
    bins = [
        {"weight": 0.0, "sin": 0.0, "cos": 0.0}
        for _ in range(_HUE_BINS)
    ]

    for r, g, b, a in _iter_pixels(image):
        sampled = _meaningful_hsv(r, g, b, a)
        if sampled is None:
            continue

        hue, saturation, value, alpha_weight = sampled
        weight = (saturation ** 1.45) * (0.35 + min(value, 0.90)) * alpha_weight
        radians = hue * math.tau
        idx = int(hue * _HUE_BINS) % _HUE_BINS

        bins[idx]["weight"] += weight
        bins[idx]["sin"] += math.sin(radians) * weight
        bins[idx]["cos"] += math.cos(radians) * weight

    winner = max(range(_HUE_BINS), key=lambda idx: bins[idx]["weight"])
    if bins[winner]["weight"] <= 0:
        return None

    sin_sum = 0.0
    cos_sum = 0.0
    weight_sum = 0.0
    for idx in ((winner - 1) % _HUE_BINS, winner, (winner + 1) % _HUE_BINS):
        sin_sum += bins[idx]["sin"]
        cos_sum += bins[idx]["cos"]
        weight_sum += bins[idx]["weight"]

    if weight_sum <= 0:
        return None

    return (math.atan2(sin_sum, cos_sum) / math.tau) % 1.0


def _meaningful_hsv(r: int, g: int, b: int, a: int) -> Optional[tuple[float, float, float, float]]:
    if a < _MIN_ALPHA:
        return None

    channel_delta = max(r, g, b) - min(r, g, b)
    if channel_delta < _NEAR_GRAY_DELTA:
        return None

    hue, saturation, value = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if value < _MIN_VALUE:
        return None
    if saturation < _MIN_SATURATION:
        return None
    if value > _NEAR_WHITE_VALUE and saturation < _NEAR_WHITE_SATURATION:
        return None

    return hue, saturation, value, a / 255.0


def _deep_rgb_for_hue(hue: float) -> RGB:
    degrees = (hue % 1.0) * 360.0
    saturation = 0.86
    value = 0.54

    if degrees < 18 or degrees >= 342:
        hue = 350.0 / 360.0
        value = 0.50
    elif 28 <= degrees <= 68:
        hue = 42.0 / 360.0
        saturation = 0.88
        value = 0.64
    elif 85 <= degrees <= 165:
        hue = max(130.0, min(150.0, degrees)) / 360.0
        value = 0.50
    elif 190 <= degrees <= 250:
        hue = max(215.0, min(230.0, degrees)) / 360.0
        value = 0.55
    elif 260 <= degrees <= 315:
        hue = max(275.0, min(290.0, degrees)) / 360.0
        value = 0.56

    return _clamp_rgb(tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value)))


def _light_rgb_for_hue(hue: float) -> RGB:
    """Very pale, clearly tinted curtain based on the original hue."""
    h, s, l = hue % 1.0, 0.72, 0.92
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _clamp_rgb(tuple(round(channel * 255) for channel in (r, g, b)))


def _dark_rgb_for_hue(hue: float) -> RGB:
    """Near-black, richly saturated curtain based on the original hue."""
    degrees = (hue % 1.0) * 360.0
    # Pink/magenta moves slightly toward crimson so dark pink becomes wine or
    # blood red rather than muddy purple.
    if degrees >= 320.0 or degrees <= 18.0:
        hue = 352.0 / 360.0
    elif 285.0 <= degrees < 320.0:
        hue = 335.0 / 360.0
    h, s, l = hue % 1.0, 0.88, 0.14
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _clamp_rgb(tuple(round(channel * 255) for channel in (r, g, b)))


def _clamp_rgb(rgb: tuple[int, int, int]) -> RGB:
    return tuple(max(0, min(255, int(channel))) for channel in rgb)  # type: ignore[return-value]


def _iter_pixels(image: Image.Image):
    getter = getattr(image, "get_flattened_data", None)
    if callable(getter):
        return getter()
    return image.getdata()
