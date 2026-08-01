from __future__ import annotations

import colorsys
import math
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageOps

RGB = tuple[int, int, int]
Lab = tuple[float, float, float]
WeightedLab = tuple[Lab, float]

FALLBACK_CURTAIN_RGB: RGB = (255, 255, 255)
_MAX_SAMPLE_PIXELS = 30_000
_HUE_BINS = 36
_MIN_ALPHA = 32
_MIN_SATURATION = 0.20
_MIN_VALUE = 0.16
_NEAR_WHITE_VALUE = 0.93
_NEAR_WHITE_SATURATION = 0.38
_NEAR_GRAY_DELTA = 18
_OKLAB_BIN_SIZE = 0.04
_MAX_COLOR_CLUSTERS = 6
_MIN_CLUSTER_SHARE = 0.04
_RELATED_HUE_DEGREES = 25.0
_NEAR_BLACK_OKLAB_LIGHTNESS = 0.18
_NEAR_WHITE_OKLAB_LIGHTNESS = 0.94
_NEUTRAL_OKLAB_CHROMA = 0.04
_EXTREME_COVERAGE_THRESHOLD = 0.30
_EXTREME_INTERIOR_THRESHOLD = 0.20
_CURTAIN_LOW_PERCENTILE = 0.02
_CURTAIN_MID_PERCENTILE = 0.50
_CURTAIN_HIGH_PERCENTILE = 0.98


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


def curtain_rgb_for_style(image_paths: Iterable[Path], style: str) -> RGB:
    """Resolve a persisted curtain style into the tint used for both panels.

    Normal is derived from the dominant color's hue, lightness, and saturation
    in the letter artwork. Light and Dark are relative variants of that Normal
    color. Complementary rotates the source hue by 180 degrees before balancing.
    """
    normalized = str(style or "pure_white").strip().lower().replace(" ", "_")
    cover_path = next((Path(path) for path in image_paths), None)

    if normalized in {"pure_white", "white", "white_curtain"}:
        return FALLBACK_CURTAIN_RGB
    source_color = (
        _representative_cover_color(cover_path)
        if cover_path is not None
        else FALLBACK_CURTAIN_RGB
    )
    normal = _normal_rgb_from_source(source_color)
    if normalized in {
        "complementary",
        "complementary_curtain",
        "complementary_average_color",
    }:
        return _rotate_rgb_hue(normal, 0.5)

    if normalized in {
        "normal",
        "normal_curtain",
        "inverse_complementary_color",
        "average_color",
    }:
        return normal
    if normalized in {"light", "light_curtain", "light_average_color"}:
        return _relative_hls_variant(
            normal,
            lightness_scale=0.72,
            saturation_scale=0.82,
        )
    if normalized in {"dark", "dark_curtain", "dark_average_color"}:
        return _relative_hls_variant(
            normal,
            lightness_scale=-0.55,
            saturation_scale=1.42,
        )
    return FALLBACK_CURTAIN_RGB


def _normal_rgb_from_source(rgb: RGB) -> RGB:
    red, green, blue = (channel / 255.0 for channel in _clamp_rgb(rgb))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    balanced_lightness = max(0.44, min(0.58, lightness * 0.78))
    balanced_saturation = (
        0.0
        if saturation < 0.08
        else max(0.40, min(0.75, saturation * 1.10))
    )
    converted = colorsys.hls_to_rgb(
        hue,
        balanced_lightness,
        balanced_saturation,
    )
    return _clamp_rgb(tuple(round(channel * 255) for channel in converted))


def _rotate_rgb_hue(rgb: RGB, shift: float) -> RGB:
    red, green, blue = (channel / 255.0 for channel in _clamp_rgb(rgb))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    converted = colorsys.hls_to_rgb(
        (hue + shift) % 1.0,
        lightness,
        saturation,
    )
    return _clamp_rgb(tuple(round(channel * 255) for channel in converted))


def _relative_hls_variant(
    rgb: RGB,
    *,
    lightness_scale: float,
    saturation_scale: float,
) -> RGB:
    red, green, blue = (channel / 255.0 for channel in _clamp_rgb(rgb))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    if lightness_scale >= 0:
        lightness += (1.0 - lightness) * lightness_scale
    else:
        lightness *= 1.0 + lightness_scale
    saturation *= saturation_scale
    converted = colorsys.hls_to_rgb(
        hue,
        max(0.0, min(1.0, lightness)),
        max(0.0, min(1.0, saturation)),
    )
    return _clamp_rgb(tuple(round(channel * 255) for channel in converted))


def write_tinted_curtain_image(source_path: Path, target_path: Path, rgb: RGB) -> None:
    rgb = _clamp_rgb(rgb)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")

    alpha = image.getchannel("A")
    luminance = ImageOps.grayscale(image)
    low_point, midpoint, high_point = _visible_luminance_percentiles(
        luminance,
        alpha,
    )
    lookups = _perceptual_curtain_lookups(
        rgb,
        low_point=low_point,
        midpoint=midpoint,
        high_point=high_point,
    )
    channels = [
        luminance.point(lookup)
        for lookup in lookups
    ]

    tinted = Image.merge("RGBA", (channels[0], channels[1], channels[2], alpha))
    tinted.save(target, format="PNG", optimize=True)


def _visible_luminance_percentiles(
    luminance: Image.Image,
    alpha: Image.Image,
) -> tuple[int, int, int]:
    visible_mask = alpha.point(
        lambda value: 255 if value >= _MIN_ALPHA else 0
    )
    histogram = luminance.histogram(mask=visible_mask)
    total = sum(histogram)
    if total <= 0:
        return (0, 128, 255)

    def percentile(fraction: float) -> int:
        threshold = total * fraction
        cumulative = 0
        for value, count in enumerate(histogram):
            cumulative += count
            if cumulative >= threshold:
                return value
        return 255

    return (
        percentile(_CURTAIN_LOW_PERCENTILE),
        percentile(_CURTAIN_MID_PERCENTILE),
        percentile(_CURTAIN_HIGH_PERCENTILE),
    )


def _perceptual_curtain_lookups(
    rgb: RGB,
    *,
    low_point: int,
    midpoint: int,
    high_point: int,
) -> tuple[list[int], list[int], list[int]]:
    target_lightness, target_a, target_b = _rgb_to_oklab(rgb)
    shadow_lightness = max(
        0.04,
        target_lightness - min(0.28, target_lightness * 0.55),
    )
    highlight_lightness = min(
        0.98,
        target_lightness
        + min(0.22, (1.0 - target_lightness) * 0.65),
    )
    red_lookup: list[int] = []
    green_lookup: list[int] = []
    blue_lookup: list[int] = []

    for value in range(256):
        if value < midpoint:
            span = max(1, midpoint - low_point)
            position = _smoothstep((value - low_point) / span)
            mapped_lightness = (
                shadow_lightness
                + (target_lightness - shadow_lightness) * position
            )
            distance_from_midpoint = 1.0 - position
        elif value > midpoint:
            span = max(1, high_point - midpoint)
            position = _smoothstep((value - midpoint) / span)
            mapped_lightness = (
                target_lightness
                + (highlight_lightness - target_lightness) * position
            )
            distance_from_midpoint = position
        else:
            mapped_lightness = target_lightness
            distance_from_midpoint = 0.0

        chroma_scale = 1.0 - 0.22 * distance_from_midpoint
        mapped = _oklab_to_rgb_gamut_mapped(
            (
                mapped_lightness,
                target_a * chroma_scale,
                target_b * chroma_scale,
            )
        )
        red_lookup.append(mapped[0])
        green_lookup.append(mapped[1])
        blue_lookup.append(mapped[2])

    return red_lookup, green_lookup, blue_lookup


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


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


def _representative_cover_color(image_path: Path) -> RGB:
    try:
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except Exception:
        return FALLBACK_CURTAIN_RGB

    image = _downsample_for_analysis(image)
    bins = _cover_color_bins(image)
    if not bins:
        return FALLBACK_CURTAIN_RGB

    clusters = _cluster_perceptual_colors(bins)
    if not clusters:
        return FALLBACK_CURTAIN_RGB

    selected = _select_dominant_cluster(clusters)
    return _oklab_to_rgb(selected[0])


def _cover_color_bins(image: Image.Image) -> list[WeightedLab]:
    width, height = image.size
    visible_samples: list[
        tuple[Lab, float, float, bool, bool, bool]
    ] = []
    visible_weight = 0.0
    interior_weight = 0.0
    black_weight = 0.0
    white_weight = 0.0
    black_interior_weight = 0.0
    white_interior_weight = 0.0

    for index, (red, green, blue, alpha) in enumerate(_iter_pixels(image)):
        if alpha < _MIN_ALPHA:
            continue

        alpha_weight = alpha / 255.0
        lab = _rgb_to_oklab((red, green, blue))
        lightness, a_axis, b_axis = lab
        chroma = math.hypot(a_axis, b_axis)
        is_black = (
            lightness <= _NEAR_BLACK_OKLAB_LIGHTNESS
            and chroma <= _NEUTRAL_OKLAB_CHROMA
        )
        is_white = (
            lightness >= _NEAR_WHITE_OKLAB_LIGHTNESS
            and chroma <= _NEUTRAL_OKLAB_CHROMA
        )

        x = index % width
        y = index // width
        is_interior = (
            width <= 2
            or height <= 2
            or (
                0.08 * width <= x < 0.92 * width
                and 0.08 * height <= y < 0.92 * height
            )
        )
        analysis_weight = alpha_weight * (1.0 if is_interior else 0.75)

        visible_samples.append(
            (
                lab,
                alpha_weight,
                analysis_weight,
                is_black,
                is_white,
                is_interior,
            )
        )
        visible_weight += alpha_weight
        if is_interior:
            interior_weight += alpha_weight
        if is_black:
            black_weight += alpha_weight
            if is_interior:
                black_interior_weight += alpha_weight
        elif is_white:
            white_weight += alpha_weight
            if is_interior:
                white_interior_weight += alpha_weight

    if visible_weight <= 0:
        return []

    include_black = _extreme_is_substantial(
        black_weight,
        black_interior_weight,
        visible_weight,
        interior_weight,
    )
    include_white = _extreme_is_substantial(
        white_weight,
        white_interior_weight,
        visible_weight,
        interior_weight,
    )

    eligible = [
        (lab, analysis_weight)
        for (
            lab,
            _coverage_weight,
            analysis_weight,
            is_black,
            is_white,
            _is_interior,
        ) in visible_samples
        if (not is_black and not is_white)
        or (is_black and include_black)
        or (is_white and include_white)
    ]
    if not eligible:
        eligible = [
            (lab, analysis_weight)
            for (
                lab,
                _coverage_weight,
                analysis_weight,
                _is_black,
                _is_white,
                _is_interior,
            ) in visible_samples
        ]

    accumulated: dict[tuple[int, int, int], list[float]] = {}
    for (lightness, a_axis, b_axis), weight in eligible:
        key = (
            round(lightness / _OKLAB_BIN_SIZE),
            round(a_axis / _OKLAB_BIN_SIZE),
            round(b_axis / _OKLAB_BIN_SIZE),
        )
        values = accumulated.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        values[0] += lightness * weight
        values[1] += a_axis * weight
        values[2] += b_axis * weight
        values[3] += weight

    return [
        (
            (
                lightness_sum / weight,
                a_sum / weight,
                b_sum / weight,
            ),
            weight,
        )
        for lightness_sum, a_sum, b_sum, weight in accumulated.values()
        if weight > 0
    ]


def _extreme_is_substantial(
    extreme_weight: float,
    extreme_interior_weight: float,
    visible_weight: float,
    interior_weight: float,
) -> bool:
    overall_share = extreme_weight / visible_weight
    interior_share = (
        extreme_interior_weight / interior_weight
        if interior_weight > 0
        else overall_share
    )
    return (
        overall_share >= _EXTREME_COVERAGE_THRESHOLD
        and interior_share >= _EXTREME_INTERIOR_THRESHOLD
    )


def _cluster_perceptual_colors(bins: list[WeightedLab]) -> list[WeightedLab]:
    if not bins:
        return []

    cluster_count = min(_MAX_COLOR_CLUSTERS, len(bins))
    centroids = _initial_centroids(bins, cluster_count)
    cluster_count = len(centroids)

    for _iteration in range(16):
        assignments: list[list[WeightedLab]] = [
            [] for _index in range(cluster_count)
        ]
        for lab, weight in bins:
            closest = min(
                range(cluster_count),
                key=lambda index: _lab_distance_squared(
                    lab,
                    centroids[index],
                ),
            )
            assignments[closest].append((lab, weight))

        updated: list[Lab] = []
        for index, assigned in enumerate(assignments):
            if assigned:
                updated.append(_robust_weighted_lab_mean(assigned)[0])
            else:
                updated.append(centroids[index])

        shift = max(
            _lab_distance_squared(old, new)
            for old, new in zip(centroids, updated)
        )
        centroids = updated
        if shift <= 1e-8:
            break

    final_assignments: list[list[WeightedLab]] = [
        [] for _index in range(cluster_count)
    ]
    for lab, weight in bins:
        closest = min(
            range(cluster_count),
            key=lambda index: _lab_distance_squared(
                lab,
                centroids[index],
            ),
        )
        final_assignments[closest].append((lab, weight))

    return [
        _robust_weighted_lab_mean(assigned)
        for assigned in final_assignments
        if assigned
    ]


def _initial_centroids(
    bins: list[WeightedLab],
    cluster_count: int,
) -> list[Lab]:
    first = max(bins, key=lambda item: item[1])[0]
    centroids = [first]

    while len(centroids) < cluster_count:
        candidate = max(
            bins,
            key=lambda item: item[1]
            * min(
                _lab_distance_squared(item[0], centroid)
                for centroid in centroids
            ),
        )[0]
        if candidate in centroids:
            break
        centroids.append(candidate)

    return centroids


def _select_dominant_cluster(clusters: list[WeightedLab]) -> WeightedLab:
    total_weight = sum(weight for _lab, weight in clusters)
    eligible = [
        cluster
        for cluster in clusters
        if cluster[1] / total_weight >= _MIN_CLUSTER_SHARE
    ]
    if not eligible:
        eligible = clusters

    ranked = sorted(
        eligible,
        key=_cluster_score,
        reverse=True,
    )
    primary = ranked[0]
    if len(ranked) > 1 and _clusters_are_related(primary, ranked[1]):
        return _weighted_lab_mean((primary, ranked[1]))
    return primary


def _cluster_score(cluster: WeightedLab) -> float:
    (lightness, a_axis, b_axis), weight = cluster
    del lightness
    chroma = math.hypot(a_axis, b_axis)
    chroma_bonus = 1.0 + 0.18 * min(chroma / 0.25, 1.0)
    return weight * chroma_bonus


def _clusters_are_related(
    first: WeightedLab,
    second: WeightedLab,
) -> bool:
    first_lab, _first_weight = first
    second_lab, _second_weight = second
    first_chroma = math.hypot(first_lab[1], first_lab[2])
    second_chroma = math.hypot(second_lab[1], second_lab[2])
    if (
        first_chroma <= _NEUTRAL_OKLAB_CHROMA
        and second_chroma <= _NEUTRAL_OKLAB_CHROMA
    ):
        return True
    if (
        first_chroma <= _NEUTRAL_OKLAB_CHROMA
        or second_chroma <= _NEUTRAL_OKLAB_CHROMA
    ):
        return False

    first_hue = math.degrees(math.atan2(first_lab[2], first_lab[1])) % 360.0
    second_hue = math.degrees(math.atan2(second_lab[2], second_lab[1])) % 360.0
    distance = abs(first_hue - second_hue)
    distance = min(distance, 360.0 - distance)
    return distance <= _RELATED_HUE_DEGREES


def _weighted_lab_mean(values: Iterable[WeightedLab]) -> WeightedLab:
    materialized = tuple(values)
    total_weight = sum(weight for _lab, weight in materialized)
    if total_weight <= 0:
        return ((0.0, 0.0, 0.0), 0.0)
    return (
        (
            sum(lab[0] * weight for lab, weight in materialized)
            / total_weight,
            sum(lab[1] * weight for lab, weight in materialized)
            / total_weight,
            sum(lab[2] * weight for lab, weight in materialized)
            / total_weight,
        ),
        total_weight,
    )


def _robust_weighted_lab_mean(values: Iterable[WeightedLab]) -> WeightedLab:
    materialized = tuple(values)
    preliminary, total_weight = _weighted_lab_mean(materialized)
    if total_weight <= 0 or len(materialized) <= 2:
        return preliminary, total_weight

    ordered = sorted(
        materialized,
        key=lambda item: _lab_distance_squared(item[0], preliminary),
    )
    retained_weight = total_weight * 0.90
    retained: list[WeightedLab] = []
    remaining = retained_weight
    for lab, weight in ordered:
        if remaining <= 0:
            break
        accepted = min(weight, remaining)
        if accepted > 0:
            retained.append((lab, accepted))
            remaining -= accepted

    robust_lab, _weight = _weighted_lab_mean(retained)
    return robust_lab, total_weight


def _lab_distance_squared(first: Lab, second: Lab) -> float:
    return sum(
        (first_channel - second_channel) ** 2
        for first_channel, second_channel in zip(first, second)
    )


def _rgb_to_oklab(rgb: RGB) -> Lab:
    red, green, blue = (
        _srgb_to_linear(channel / 255.0)
        for channel in _clamp_rgb(rgb)
    )
    l_value = (
        0.4122214708 * red
        + 0.5363325363 * green
        + 0.0514459929 * blue
    )
    m_value = (
        0.2119034982 * red
        + 0.6806995451 * green
        + 0.1073969566 * blue
    )
    s_value = (
        0.0883024619 * red
        + 0.2817188376 * green
        + 0.6299787005 * blue
    )
    l_root = l_value ** (1.0 / 3.0)
    m_root = m_value ** (1.0 / 3.0)
    s_root = s_value ** (1.0 / 3.0)
    return (
        0.2104542553 * l_root
        + 0.7936177850 * m_root
        - 0.0040720468 * s_root,
        1.9779984951 * l_root
        - 2.4285922050 * m_root
        + 0.4505937099 * s_root,
        0.0259040371 * l_root
        + 0.7827717662 * m_root
        - 0.8086757660 * s_root,
    )


def _oklab_to_rgb(lab: Lab) -> RGB:
    channels = _oklab_to_srgb_channels(lab)
    return _clamp_rgb(
        tuple(
            round(255.0 * max(0.0, min(1.0, channel)))
            for channel in channels
        )
    )


def _oklab_to_rgb_gamut_mapped(lab: Lab) -> RGB:
    lightness, a_axis, b_axis = lab
    channels = _oklab_to_srgb_channels(lab)
    if all(0.0 <= channel <= 1.0 for channel in channels):
        return _clamp_rgb(
            tuple(round(255.0 * channel) for channel in channels)
        )

    lower_scale = 0.0
    upper_scale = 1.0
    best_channels = _oklab_to_srgb_channels((lightness, 0.0, 0.0))
    for _iteration in range(14):
        scale = (lower_scale + upper_scale) / 2.0
        candidate = _oklab_to_srgb_channels(
            (lightness, a_axis * scale, b_axis * scale)
        )
        if all(0.0 <= channel <= 1.0 for channel in candidate):
            lower_scale = scale
            best_channels = candidate
        else:
            upper_scale = scale

    return _clamp_rgb(
        tuple(round(255.0 * channel) for channel in best_channels)
    )


def _oklab_to_srgb_channels(lab: Lab) -> tuple[float, float, float]:
    lightness, a_axis, b_axis = lab
    l_root = lightness + 0.3963377774 * a_axis + 0.2158037573 * b_axis
    m_root = lightness - 0.1055613458 * a_axis - 0.0638541728 * b_axis
    s_root = lightness - 0.0894841775 * a_axis - 1.2914855480 * b_axis
    l_value = l_root ** 3
    m_value = m_root ** 3
    s_value = s_root ** 3
    linear = (
        4.0767416621 * l_value
        - 3.3077115913 * m_value
        + 0.2309699292 * s_value,
        -1.2684380046 * l_value
        + 2.6097574011 * m_value
        - 0.3413193965 * s_value,
        -0.0041960863 * l_value
        - 0.7034186147 * m_value
        + 1.7076147010 * s_value,
    )
    return tuple(
        _linear_to_srgb_unclamped(channel)
        for channel in linear
    )


def _srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(channel: float) -> float:
    channel = max(0.0, min(1.0, channel))
    return _linear_to_srgb_unclamped(channel)


def _linear_to_srgb_unclamped(channel: float) -> float:
    if channel <= 0.0031308:
        return 12.92 * channel
    return 1.055 * (channel ** (1.0 / 2.4)) - 0.055


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


def _clamp_rgb(rgb: tuple[int, int, int]) -> RGB:
    return tuple(max(0, min(255, int(channel))) for channel in rgb)  # type: ignore[return-value]


def _iter_pixels(image: Image.Image):
    getter = getattr(image, "get_flattened_data", None)
    if callable(getter):
        return getter()
    return image.getdata()
