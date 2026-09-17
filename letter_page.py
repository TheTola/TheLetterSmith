from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Mapping, Sequence


MESSAGE_OVERLAY_PRESET_KEY = "message_overlay_preset"
MESSAGE_OVERLAY_OPACITY_KEY = "message_overlay_opacity"
DEFAULT_MESSAGE_OVERLAY_PRESET = "paper"
DEFAULT_MESSAGE_OVERLAY_OPACITY = 68

# Shared message-render geometry.  The editor and the committed message.png
# renderer both use these values so the editing surface keeps the same usable
# text frame as the final raster output.
MESSAGE_RENDER_WIDTH = 2048
MESSAGE_RENDER_HEIGHT = 3072
MESSAGE_RENDER_MARGIN_LR = 100
MESSAGE_RENDER_MARGIN_TOP = 100
MESSAGE_RENDER_MARGIN_BOTTOM = 100
MESSAGE_RENDER_FONT_FAMILY = "Papyrus"
MESSAGE_RENDER_FONT_SIZE = 16
MESSAGE_RENDER_LINE_HEIGHT = 2.0


@dataclass(frozen=True)
class LetterPagePreset:
    label: str
    base_rgb: tuple[int, int, int]
    center_rgb: tuple[int, int, int]
    edge_rgb: tuple[int, int, int]
    default_ink: str
    max_lightness_shift: float
    border: str
    inner_border: str
    shadow: str


LETTER_PAGE_PRESETS: dict[str, LetterPagePreset] = {
    "paper": LetterPagePreset(
        "Warm Paper",
        (245, 235, 210),
        (248, 240, 220),
        (241, 228, 198),
        "#2b1c12",
        0.03,
        "rgba(104,76,43,.18)",
        "rgba(104,76,43,.10)",
        "0 18px 52px rgba(0,0,0,.20),inset 0 1px 0 rgba(255,255,255,.32)",
    ),
    "black": LetterPagePreset(
        "Dark Panel",
        (15, 15, 15),
        (19, 19, 19),
        (15, 15, 15),
        "#f1eee8",
        0.03,
        "rgba(255,255,255,.07)",
        "rgba(255,255,255,.035)",
        "0 20px 56px rgba(0,0,0,.30),inset 0 1px 0 rgba(255,255,255,.035)",
    ),
    "white": LetterPagePreset(
        "Light Panel",
        (247, 248, 250),
        (250, 251, 252),
        (244, 246, 248),
        "#20252a",
        0.03,
        "rgba(35,46,58,.10)",
        "rgba(35,46,58,.055)",
        "0 18px 50px rgba(0,0,0,.16),inset 0 1px 0 rgba(255,255,255,.55)",
    ),
    "clear": LetterPagePreset(
        "Transparent",
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        "#eeeae2",
        0.05,
        "transparent",
        "transparent",
        "none",
    ),
}

LETTER_PAGE_PRESET_LABELS = {
    key: preset.label for key, preset in LETTER_PAGE_PRESETS.items()
}

_PRESET_ALIASES = {
    "paper": "paper",
    "warm": "paper",
    "warm paper": "paper",
    "cream": "paper",
    "beige": "paper",
    "black": "black",
    "dark": "black",
    "dark panel": "black",
    "white": "white",
    "light": "white",
    "light panel": "white",
    "clear": "clear",
    "transparent": "clear",
    "none": "clear",
}


def normalize_letter_page_preset(value: object) -> str:
    key = " ".join(
        str(value or "").strip().casefold().replace("_", " ").replace("-", " ").split()
    )
    return _PRESET_ALIASES.get(key, DEFAULT_MESSAGE_OVERLAY_PRESET)


def normalized_letter_page_settings(
    data: Mapping[str, object],
) -> tuple[str, int, tuple[int, int, int], str]:
    preset = normalize_letter_page_preset(
        data.get(MESSAGE_OVERLAY_PRESET_KEY, DEFAULT_MESSAGE_OVERLAY_PRESET)
    )
    try:
        opacity = int(
            data.get(MESSAGE_OVERLAY_OPACITY_KEY, DEFAULT_MESSAGE_OVERLAY_OPACITY)
        )
    except (TypeError, ValueError):
        opacity = DEFAULT_MESSAGE_OVERLAY_OPACITY
    opacity = max(0, min(100, opacity))
    if preset == "clear":
        opacity = 0
    definition = LETTER_PAGE_PRESETS[preset]
    return preset, opacity, definition.base_rgb, definition.default_ink


def effective_letter_page_opacity(preset: str, opacity: int) -> int:
    if normalize_letter_page_preset(preset) == "clear":
        return 0
    return max(0, min(100, int(opacity)))


def composite_rgb(
    background: Sequence[int],
    foreground: Sequence[int],
    opacity: float,
) -> tuple[int, int, int]:
    alpha = max(0.0, min(1.0, float(opacity)))
    return tuple(
        max(0, min(255, round(bg + ((fg - bg) * alpha))))
        for bg, fg in zip(background[:3], foreground[:3])
    )  # type: ignore[return-value]


def surface_rgb_at(preset: str, x_fraction: float, y_fraction: float) -> tuple[int, int, int]:
    """Return the preset's deliberately slight center-to-edge tonal treatment."""
    normalized = normalize_letter_page_preset(preset)
    definition = LETTER_PAGE_PRESETS[normalized]
    if normalized == "clear":
        return definition.base_rgb
    x = max(0.0, min(1.0, float(x_fraction)))
    y = max(0.0, min(1.0, float(y_fraction)))
    distance = min(1.0, math.hypot(x - 0.5, y - 0.43) / 0.76)
    return tuple(
        round(center + ((edge - center) * distance))
        for center, edge in zip(definition.center_rgb, definition.edge_rgb)
    )  # type: ignore[return-value]


def relative_luminance(rgb: Sequence[int]) -> float:
    channels = []
    for raw in rgb[:3]:
        value = max(0.0, min(255.0, float(raw))) / 255.0
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2])


def contrast_ratio(foreground: Sequence[int], background: Sequence[int]) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def adaptive_text_rgb(
    intended_rgb: Sequence[int],
    background_rgb: Sequence[int],
    *,
    max_lightness_shift: float,
) -> tuple[int, int, int]:
    """Apply only a bounded HLS-lightness nudge, never full recoloring."""
    foreground = tuple(max(0, min(255, int(round(value)))) for value in intended_rgb[:3])
    background = tuple(max(0, min(255, int(round(value)))) for value in background_rgb[:3])
    ratio = contrast_ratio(foreground, background)
    if ratio >= 7.0 or max_lightness_shift <= 0:
        return foreground  # type: ignore[return-value]

    assistance = max(0.0, min(1.0, (7.0 - ratio) / 6.0))
    shift = min(max(0.0, max_lightness_shift), 0.05) * assistance
    # Quarter-percent steps keep neighboring glyphs visually coherent and cap
    # the number of browser highlight colors without forcing a minimum change.
    shift = math.floor((shift + 1e-12) / 0.0025) * 0.0025
    if shift <= 0:
        return foreground  # type: ignore[return-value]
    red, green, blue = (channel / 255.0 for channel in foreground)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    if relative_luminance(foreground) <= relative_luminance(background):
        adjusted_lightness = max(0.0, lightness - shift)
    else:
        adjusted_lightness = min(1.0, lightness + shift)
    candidate = tuple(
        round(channel * 255)
        for channel in colorsys.hls_to_rgb(hue, adjusted_lightness, saturation)
    )
    if contrast_ratio(candidate, background) + 1e-9 < ratio:
        return foreground  # type: ignore[return-value]
    return candidate  # type: ignore[return-value]


def letter_page_style_from_settings(data: Mapping[str, object]) -> str:
    preset, opacity, _rgb, _ink = normalized_letter_page_settings(data)
    definition = LETTER_PAGE_PRESETS[preset]
    alpha = effective_letter_page_opacity(preset, opacity) / 100.0
    values = {
        "--message-overlay-rgb": ",".join(map(str, definition.base_rgb)),
        "--message-overlay-center-rgb": ",".join(map(str, definition.center_rgb)),
        "--message-overlay-edge-rgb": ",".join(map(str, definition.edge_rgb)),
        "--message-overlay-opacity": f"{alpha:.3f}",
        "--message-overlay-surface-opacity": f"{alpha:.3f}",
        "--message-overlay-border": definition.border,
        "--message-overlay-inner-border": definition.inner_border,
        "--message-overlay-shadow": definition.shadow,
        "--message-ink": definition.default_ink,
        "--adaptive-max-lightness": f"{definition.max_lightness_shift:.3f}",
        "--wall-fade-ms": "900ms",
    }
    return ";".join(f"{key}:{value}" for key, value in values.items()) + ";"
