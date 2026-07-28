from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable
from weakref import WeakKeyDictionary


def mix_hex(color_a: str, color_b: str, amount: float) -> str:
    """Blend two six-digit hexadecimal colors."""
    amount = max(0.0, min(1.0, amount))
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")

    if len(a) != 6 or len(b) != 6:
        raise ValueError("Colors must use six-digit hexadecimal notation.")

    rgb_a = tuple(int(a[i:i + 2], 16) for i in (0, 2, 4))
    rgb_b = tuple(int(b[i:i + 2], 16) for i in (0, 2, 4))

    mixed = tuple(
        round(value_a + ((value_b - value_a) * amount))
        for value_a, value_b in zip(rgb_a, rgb_b)
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


@dataclass(frozen=True)
class ColorPool:
    # Surfaces
    app_background: str = "#1d1d1d"
    panel_background: str = "#101821"
    card_background: str = "#111820"
    control_background: str = "#14202c"
    preview_background: str = "#0b1016"

    # Borders, tracks, and decorative lines
    border_default: str = "#34485c"
    border_muted: str = "#273644"
    track_background: str = "#20313f"
    grid_line: str = "#17222d"

    # Text
    text_primary: str = "#f4f8fb"
    text_secondary: str = "#91a7ba"
    text_disabled: str = "#64798b"

    # Shared accent family
    accent: str = "#00d9f5"
    accent_hover: str = "#42e8ff"
    accent_soft: str = "#123b45"

    # Optional destructive role
    danger: str = "#d85c6a"
    danger_hover: str = "#ef7380"


class ThemeManager:
    """
    Centralized theme manager.

    Every widget registers against a semantic role. Updating one value in the
    shared ColorPool restyles all registered widgets across all tabs.
    """

    def __init__(self, colors: ColorPool | None = None) -> None:
        self.colors = colors or ColorPool()
        self._bindings: WeakKeyDictionary[Any, str] = WeakKeyDictionary()
        self._observers: list[Callable[[ColorPool], None]] = []

    def bind(self, widget: Any, role: str) -> Any:
        self._bindings[widget] = role
        self.apply(widget, role)
        return widget

    def subscribe(self, callback: Callable[[ColorPool], None]) -> None:
        if callback not in self._observers:
            self._observers.append(callback)
        callback(self.colors)

    def unsubscribe(self, callback: Callable[[ColorPool], None]) -> None:
        if callback in self._observers:
            self._observers.remove(callback)

    def apply(self, widget: Any, role: str | None = None) -> None:
        role = role or self._bindings.get(widget)
        if role is None:
            return

        options = self._role_options(role)
        try:
            widget.configure(**options)
        except Exception as exc:
            raise RuntimeError(
                f"Could not apply role {role!r} to {type(widget).__name__}."
            ) from exc

    def refresh(self) -> None:
        for widget, role in list(self._bindings.items()):
            try:
                if widget.winfo_exists():
                    self.apply(widget, role)
            except Exception:
                continue

        for observer in list(self._observers):
            try:
                observer(self.colors)
            except Exception:
                continue

    def update(self, **changes: str) -> None:
        try:
            self.colors = replace(self.colors, **changes)
        except TypeError as exc:
            raise ValueError(
                "One or more supplied names do not exist in ColorPool."
            ) from exc
        self.refresh()

    def set_accent(self, accent: str) -> None:
        """
        Change the global accent and derive matching hover and muted variants.
        """
        hover = mix_hex(accent, "#ffffff", 0.22)
        soft = mix_hex(self.colors.control_background, accent, 0.18)

        self.colors = replace(
            self.colors,
            accent=accent,
            accent_hover=hover,
            accent_soft=soft,
        )
        self.refresh()

    def _role_options(self, role: str) -> dict[str, Any]:
        c = self.colors

        roles: dict[str, dict[str, Any]] = {
            "window": {
                "fg_color": c.app_background,
            },
            "transparent": {
                "fg_color": "transparent",
            },
            "panel": {
                "fg_color": c.panel_background,
                "border_color": c.border_muted,
                "border_width": 1,
                "corner_radius": 8,
            },
            # Used by both image cards and the main sound card.
            "media_card": {
                "fg_color": c.card_background,
                "border_color": c.accent,
                "border_width": 1,
                "corner_radius": 8,
            },
            "media_slot": {
                "fg_color": c.panel_background,
                "border_color": c.border_default,
                "border_width": 1,
                "corner_radius": 6,
            },
            "button": {
                "fg_color": c.control_background,
                "hover_color": c.accent_soft,
                "border_color": c.border_default,
                "border_width": 1,
                "text_color": c.text_primary,
                "corner_radius": 6,
            },
            "accent_button": {
                "fg_color": c.control_background,
                "hover_color": c.accent_soft,
                "border_color": c.accent,
                "border_width": 1,
                "text_color": c.text_primary,
                "corner_radius": 6,
            },
            "playback_button": {
                "fg_color": c.control_background,
                "hover_color": c.accent_soft,
                "border_color": c.border_default,
                "border_width": 1,
                "text_color": c.text_primary,
                "corner_radius": 6,
            },
            "danger_button": {
                "fg_color": c.control_background,
                "hover_color": c.danger_hover,
                "border_color": c.danger,
                "border_width": 1,
                "text_color": c.text_primary,
                "corner_radius": 6,
            },
            "heading_text": {
                "text_color": c.text_primary,
            },
            "primary_text": {
                "text_color": c.text_primary,
            },
            "secondary_text": {
                "text_color": c.text_secondary,
            },
            "disabled_text": {
                "text_color": c.text_disabled,
            },
            "accent_text": {
                "text_color": c.accent,
            },
            "active_tab": {
                "fg_color": "transparent",
                "hover_color": c.accent_soft,
                "text_color": c.text_primary,
                "border_width": 0,
            },
            "inactive_tab": {
                "fg_color": "transparent",
                "hover_color": c.accent_soft,
                "text_color": c.text_secondary,
                "border_width": 0,
            },
            "accent_line": {
                "fg_color": c.accent,
                "corner_radius": 0,
            },
            "progress_bar": {
                "fg_color": c.track_background,
                "progress_color": c.accent,
                "border_color": c.border_muted,
            },
            "slider": {
                "fg_color": c.track_background,
                "progress_color": c.accent,
                "button_color": c.accent,
                "button_hover_color": c.accent_hover,
            },
            "entry": {
                "fg_color": c.panel_background,
                "border_color": c.border_default,
                "text_color": c.text_primary,
                "placeholder_text_color": c.text_secondary,
            },
        }

        try:
            return roles[role]
        except KeyError as exc:
            raise ValueError(f"Unknown theme role: {role!r}") from exc


# The entire application imports this same object.
THEME = ThemeManager()
