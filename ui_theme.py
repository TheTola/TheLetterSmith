from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import logging
import math
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping
from weakref import WeakKeyDictionary

from PySide6 import QtCore, QtGui, QtWidgets

from settings_store import SettingsStore
from project_paths import application_paths
from ui_fonts import load_application_fonts, resolve_registered_family


_LOGGER = logging.getLogger(__name__)


THEME_SETTINGS_KEY = "application_theme"
THEME_FAMILY_SETTINGS_KEY = "theme_family"
DEFAULT_THEME_ID = "cyber_forge"
DEFAULT_FORGE_THEME_ID = "cyber_forge"
DEFAULT_ROSE_THEME_ID = "velvet_rose"
MAXIMIZE_THEME_ASSET = "titlebar/maximize.png"
RESTORE_THEME_ASSET_CANDIDATES = (
    "titlebar/restore.png",
    MAXIMIZE_THEME_ASSET,
)

THEME_FAMILIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "forge": ("cyber_forge", "obsidian_forge"),
        "rose": ("velvet_rose", "celestial_rose"),
    }
)
REQUIRED_THEME_ASSETS = (
    "buttons/BButton.png",
    "buttons/CButton.png",
    "buttons/DButton.png",
    "buttons/EButton.png",
    "buttons/GButton.png",
    "buttons/IButton.png",
    "buttons/LButton.png",
    "buttons/MButton.png",
    "buttons/PButton.png",
    "buttons/RButton.png",
    "buttons/ROButton.png",
    "image_frame.png",
    "prompt_writer/Pwrite.png",
    "settings/Settings.gif",
    "settings/settingz.gif",
    "titlebar/Exi.png",
    "titlebar/maxi.png",
    "titlebar/mini.png",
    "titlebar/reticle.png",
)
REQUIRED_CONTENT_THEME_ASSETS = (
    "image_frame.png",
    "prompt_writer/Pwrite.png",
)
HELP_THEME_ASSET_CANDIDATES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "idle": ("help/idle.gif", "help/idle.png"),
        "hover": ("help/hover.png", "help/hover.gif"),
    }
)
_LEGACY_THEME_ALIASES = {
    "futuristic": DEFAULT_FORGE_THEME_ID,
    "soft_elegant": DEFAULT_ROSE_THEME_ID,
    "basic_dark": "dark",
    "basic_light": "light",
}
_STANDARD_ASSET_OVERRIDES = MappingProxyType(
    {
        "titlebar/target.png": "titlebar/reticle.png",
        "titlebar/minimize.png": "titlebar/mini.png",
        MAXIMIZE_THEME_ASSET: "titlebar/maxi.png",
        "titlebar/restore.png": "titlebar/rest.png",
        "titlebar/close.png": "titlebar/Exi.png",
        "settings/idle.png": "settings/settings.png",
        "settings/hover.gif": "settings/Settings.gif",
        "help/idle.gif": "help/Help.gif",
        "help/idle.png": "help/Help.png",
        "help/hover.gif": "help/HHelp.gif",
        "help/hover.png": "help/HHelp.png",
    }
)

# Existing widget styles were authored against the original Cyber Forge
# palette.  This table classifies those UI colors by meaning so legacy widget
# styles can be translated without changing semantic status colors or user
# content colors.  New styles should use ThemeTokens directly.
_CYBER_UI_COLOR_ROLES: Mapping[str, str] = MappingProxyType(
    {
        "#00b2b2": "accent_primary",
        "#00d0ff": "accent_secondary",
        "#00e5ff": "accent_bright",
        "#00ffff": "accent_bright",
        "#00c8ff": "accent_secondary",
        "#00d4f4": "accent_secondary",
        "#00d2ef": "accent_secondary",
        "#00cce8": "accent_secondary",
        "#00a9c7": "accent_primary",
        "#00a9c5": "accent_primary",
        "#00b8cf": "accent_primary",
        "#00e5e5": "accent_bright",
        "#007f82": "button_background",
        "#00979b": "button_hover",
        "#13585d": "button_background",
        "#176c70": "button_hover",
        "#74e1e1": "accent_bright",
        "#72c8c8": "accent_secondary",
        "#8ce3e3": "accent_bright",
        "#9bfffb": "accent_bright",
        "#4fe5ff": "accent_bright",
        "#ffffff": "text_primary",
        "#fffefc": "text_primary",
        "#f4ffff": "text_primary",
        "#f0f6fc": "text_primary",
        "#f2f5f7": "text_primary",
        "#f0fbff": "text_primary",
        "#eefaff": "text_primary",
        "#eef4fb": "text_primary",
        "#ecf2ff": "text_primary",
        "#eaf8fc": "text_primary",
        "#eaf7ff": "text_primary",
        "#e8f7ff": "text_primary",
        "#e8edf5": "text_primary",
        "#e8eff8": "text_primary",
        "#e7eef8": "text_primary",
        "#e6e6e6": "text_primary",
        "#e5e7eb": "text_primary",
        "#eeeeee": "text_primary",
        "#dddddd": "text_primary",
        "#bbbbbb": "text_secondary",
        "#e4ebf4": "text_primary",
        "#dffcff": "text_primary",
        "#dff7ff": "text_primary",
        "#dce9ef": "text_primary",
        "#d9e4ef": "text_primary",
        "#d8e0ea": "text_primary",
        "#cfd8e5": "text_secondary",
        "#c9d1d9": "text_secondary",
        "#b9ccd5": "text_secondary",
        "#b8c9d0": "text_secondary",
        "#b7c9dc": "text_secondary",
        "#8b949e": "text_muted",
        "#8097a1": "text_muted",
        "#78909a": "text_muted",
        "#667985": "text_muted",
        "#93a8bd": "text_muted",
        "#68717c": "text_disabled",
        "#dffbff": "text_primary",
        "#dff9ff": "text_primary",
        "#dff8ff": "text_primary",
        "#eafcff": "text_primary",
        "#e8f9ff": "text_primary",
        "#f1fdff": "text_primary",
        "#edfaff": "text_primary",
        "#f2fbff": "text_primary",
        "#e0ffff": "text_primary",
        "#edf7fb": "text_primary",
        "#d9e6ec": "text_primary",
        "#d9e7ed": "text_primary",
        "#d7e7ef": "text_primary",
        "#bfeaf3": "text_secondary",
        "#a9cbd6": "text_secondary",
        "#a9c4cf": "text_secondary",
        "#9fcbd5": "text_secondary",
        "#aac0ca": "text_secondary",
        "#aebbc8": "text_secondary",
        "#aeb8c6": "text_secondary",
        "#93a7b3": "text_muted",
        "#91a7ba": "text_muted",
        "#8995a3": "text_muted",
        "#8794a5": "text_muted",
        "#839da8": "text_muted",
        "#788594": "text_muted",
        "#61727a": "text_disabled",
        "#69727e": "text_disabled",
        "#111111": "text_primary",
        "#0e0f12": "text_primary",
        "#101820": "panel_background",
        "#101317": "panel_background",
        "#111820": "panel_secondary",
        "#111921": "panel_secondary",
        "#0d151c": "panel_secondary",
        "#0d1a22": "panel_secondary",
        "#101b23": "panel_secondary",
        "#121b23": "panel_secondary",
        "#121820": "panel_secondary",
        "#111b23": "panel_secondary",
        "#15191f": "panel_background",
        "#151a21": "input_background",
        "#151a20": "button_background",
        "#151a22": "button_background",
        "#15181c": "button_disabled",
        "#171a1f": "button_background",
        "#171c22": "button_background",
        "#1b2430": "button_background",
        "#1c1f21": "button_background",
        "#1d1f21": "button_background",
        "#1d232b": "input_background",
        "#233447": "button_hover",
        "#202832": "button_hover",
        "#1c252d": "button_hover",
        "#11161c": "button_pressed",
        "#101318": "button_pressed",
        "#0b0f15": "window_background",
        "#0c141a": "panel_background",
        "#0c151b": "panel_background",
        "#0d0f14": "input_background",
        "#10141d": "panel_background",
        "#11151c": "panel_background",
        "#121a21": "panel_secondary",
        "#131e26": "button_background",
        "#13222c": "input_background",
        "#132a31": "panel_secondary",
        "#14232d": "button_hover",
        "#17232c": "button_hover",
        "#19232d": "button_background",
        "#21262d": "button_background",
        "#30363d": "button_hover",
        "#161b22": "panel_background",
        "#121318": "window_background",
        "#121212": "window_background",
        "#1e1e1e": "window_background",
        "#141414": "window_background",
        "#161616": "panel_background",
        "#181818": "input_background",
        "#1a1b1d": "panel_background",
        "#1e2023": "panel_secondary",
        "#202225": "surface_raised",
        "#25282b": "button_background",
        "#293537": "button_hover",
        "#191c1f": "button_pressed",
        "#151719": "input_background",
        "#171b20": "button_background",
        "#1c1e26": "button_background",
        "#1c252e": "button_hover",
        "#1c2430": "button_hover",
        "#15212b": "button_background",
        "#192a35": "button_hover",
        "#14202c": "input_background",
        "#17485a": "selection_background",
        "#215052": "selection_background",
        "#254252": "selection_background",
        "#10263b": "selection_background",
        "#173f4d": "selection_background",
        "#1c5275": "selection_background",
        "#244b69": "selection_background",
        "#142630": "selection_background",
        "#091116": "input_background",
        "#272e38": "scrollbar_track",
        "#447b8a": "accent_primary",
        "#315a68": "scrollbar_handle",
        "#3b7181": "scrollbar_hover",
        "#31505e": "scrollbar_handle",
        "#31505f": "tooltip_border",
        "#34485c": "border_normal",
        "#35404d": "border_normal",
        "#38424f": "border_normal",
        "#435064": "border_normal",
        "#3f555c": "border_normal",
        "#394654": "border_normal",
        "#33475f": "border_normal",
        "#303945": "border_normal",
        "#484f58": "border_normal",
        "#465260": "border_normal",
        "#43505d": "border_normal",
        "#3c4652": "border_normal",
        "#344956": "border_normal",
        "#2e596a": "border_normal",
        "#2c3440": "border_normal",
        "#2b3344": "border_subtle",
        "#2b3139": "border_subtle",
        "#2b3a4d": "border_subtle",
        "#263e4a": "border_subtle",
        "#375463": "input_border",
        "#373d42": "input_border",
        "#3a4045": "border_normal",
        "#3a4145": "border_normal",
        "#2d3540": "border_subtle",
        "#303438": "border_subtle",
        "#2b3034": "border_subtle",
        "#2c3034": "border_subtle",
        "#2c3033": "border_subtle",
        "#343a3e": "border_subtle",
        "#34393d": "border_subtle",
        "#253d49": "border_subtle",
        "#294653": "border_subtle",
        "#315c69": "border_normal",
        "#356072": "border_normal",
        "#365365": "border_normal",
        "#223e4a": "border_subtle",
        "#53666d": "input_border",
        "#287f92": "border_hover",
        "#78b9dd": "text_secondary",
        "#3d8fba": "input_focus",
        "#38506b": "border_normal",
        "#294858": "button_hover",
        "#d8ffff": "text_primary",
        "#91a8bd": "text_muted",
        "#345160": "border_normal",
        "#1b2932": "panel_secondary",
        "#3b515d": "border_normal",
        "#3b6678": "border_hover",
        "#2a2a2a": "border_subtle",
        "#232323": "button_background",
        "#0f0f0f": "input_background",
        "#242424": "border_subtle",
        "#454545": "separator",
        "#1d1d1d": "button_background",
        "#101010": "button_background",
        "#80eaff": "accent_bright",
        "#48616f": "border_hover",
        "#123d49": "selection_background",
        "#2d6bff": "glow_secondary",
        "#1b1d20": "panel_secondary",
        "#35c8e6": "border_focus",
        "#008f8f": "accent_primary",
        "#00b9d8": "accent_secondary",
        "#00cdec": "accent_secondary",
        "#00d5f5": "accent_bright",
        "#8defff": "accent_bright",
    }
)
_QSS_HEX_COLOR_PATTERN = re.compile(
    r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b"
)
_QSS_INTERFACE_FONT_PATTERN = re.compile(
    r"(?:"
    r"['\"]Segoe[ \t]+UI[ \t]+Semibold['\"]"
    r"|['\"]Segoe[ \t]+UI['\"]"
    r"|\bSegoe[ \t]+UI[ \t]+Semibold\b(?=\s*(?:[,;}]|$))"
    r"|\bSegoe[ \t]+UI\b(?=\s*(?:[,;}]|$))"
    r")",
    re.IGNORECASE,
)
_QSS_FONT_SIZE_PATTERN = re.compile(
    r"(?P<prefix>(?:font-size\s*:\s*|font\s*:[^;{}]*?))"
    r"(?P<size>\d+(?:\.\d+)?)(?P<unit>pt|px)\b",
    re.IGNORECASE,
)

_THEME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

THEME_FONT_ROLE_PROPERTY = "themeFontRole"
APPLICATION_FONT_ROLE = "application"
USER_ENTRY_FONT_ROLE = "userEntry"
LETTER_CONTENT_FONT_ROLE = "letterContent"
BUTTON_TIER_PROPERTY = "buttonTier"
BUTTON_WEIGHT_PROPERTY = "buttonWeight"
BUTTON_FULL_TIER_GEOMETRY_PROPERTY = "fullTierGeometry"
_VELVET_FONT_ADJUSTED_PROPERTY = "_velvetRoseFontAdjusted"
TAB_HEADING_FONT_POINT_SIZE = 18.0


class ButtonTier(str, Enum):
    LARGE = "large"
    STANDARD = "standard"
    MEDIUM = "medium"
    SMALL = "small"


@dataclass(frozen=True)
class ButtonTierStyle:
    width: int
    height: int
    font_point_size: float
    horizontal_padding: int
    vertical_padding: int
    radius: int
    bold: bool

    @property
    def size(self) -> QtCore.QSize:
        return QtCore.QSize(self.width, self.height)


@dataclass(frozen=True)
class LayoutMetrics:
    left: int
    top: int
    right: int
    bottom: int
    spacing: int

    def apply(self, layout: QtWidgets.QLayout) -> None:
        layout.setContentsMargins(self.left, self.top, self.right, self.bottom)
        layout.setSpacing(self.spacing)


BUTTON_TIER_STYLES: Mapping[ButtonTier, ButtonTierStyle] = MappingProxyType(
    {
        ButtonTier.LARGE: ButtonTierStyle(308, 103, 13.7, 22, 12, 12, True),
        ButtonTier.STANDARD: ButtonTierStyle(205, 68, 14.3, 21, 10, 10, True),
        ButtonTier.MEDIUM: ButtonTierStyle(155, 51, 11.7, 16, 8, 9, True),
        ButtonTier.SMALL: ButtonTierStyle(131, 44, 10.4, 12, 7, 8, True),
    }
)


def _scaled_button_tier_style(style: ButtonTierStyle) -> ButtonTierStyle:
    return ButtonTierStyle(
        width=round(style.width * 0.90),
        height=round(style.height * 0.80),
        font_point_size=style.font_point_size,
        horizontal_padding=max(6, round(style.horizontal_padding * 0.90)),
        vertical_padding=max(3, round(style.vertical_padding * 0.60)),
        radius=style.radius,
        bold=style.bold,
    )


BASIC_BUTTON_TIER_STYLES: Mapping[ButtonTier, ButtonTierStyle] = (
    MappingProxyType(
        {
            tier: _scaled_button_tier_style(style)
            for tier, style in BUTTON_TIER_STYLES.items()
        }
    )
)

PRIMARY_PAGE_LAYOUT = LayoutMetrics(20, 16, 20, 18, 12)
SOUND_PAGE_LAYOUT = LayoutMetrics(28, 22, 28, 24, 18)
SECTION_LAYOUT_SPACING = 14
ROW_LAYOUT_SPACING = 10


def apply_tab_heading_style(label: QtWidgets.QLabel) -> QtWidgets.QLabel:
    """Apply the shared themed typography used by primary project-tab headings."""
    if not isinstance(label, QtWidgets.QLabel):
        raise TypeError("Tab heading styling can only be applied to QLabel widgets.")
    label.setProperty(THEME_FONT_ROLE_PROPERTY, APPLICATION_FONT_ROLE)
    label.setProperty("themeRole", "headingText")
    font = QtGui.QFont(label.font())
    font.setPointSizeF(TAB_HEADING_FONT_POINT_SIZE)
    font.setWeight(QtGui.QFont.Weight.Bold)
    label.setFont(font)
    return label


def apply_button_tier(
    button: QtWidgets.QAbstractButton,
    tier: ButtonTier | str,
    *,
    bold: bool | None = None,
) -> QtWidgets.QAbstractButton:
    """Apply one centralized geometry and typography tier to a button role."""
    if not isinstance(button, QtWidgets.QAbstractButton):
        raise TypeError("Button tiers can only be applied to button widgets.")
    resolved = tier if isinstance(tier, ButtonTier) else ButtonTier(str(tier))
    style = BUTTON_TIER_STYLES[resolved]
    use_bold = style.bold if bold is None else bool(bold)
    button.setProperty(BUTTON_TIER_PROPERTY, resolved.value)
    button.setProperty(BUTTON_WEIGHT_PROPERTY, "bold" if use_bold else "regular")
    button.setFixedSize(style.size)
    button.setSizePolicy(
        QtWidgets.QSizePolicy.Fixed,
        QtWidgets.QSizePolicy.Fixed,
    )
    font = QtGui.QFont(button.font())
    font.setPointSizeF(style.font_point_size)
    font.setWeight(
        QtGui.QFont.Weight.Bold
        if use_bold
        else QtGui.QFont.Weight.Normal
    )
    button.setFont(font)
    widget_style = button.style()
    if widget_style is not None:
        widget_style.unpolish(button)
        widget_style.polish(button)
    button.updateGeometry()
    button.update()
    return button


def _button_tier_stylesheet(
    scope: str,
    *,
    font_size_adjustment: float = 0.0,
    small_buttons_bold: bool = True,
    tier_styles: Mapping[ButtonTier, ButtonTierStyle] = BUTTON_TIER_STYLES,
    include_geometry_constraints: bool = True,
) -> str:
    rules: list[str] = []
    for tier, style in tier_styles.items():
        selector = (
            f'{scope}QPushButton[{BUTTON_TIER_PROPERTY}="{tier.value}"], '
            f'{scope}QToolButton[{BUTTON_TIER_PROPERTY}="{tier.value}"]'
        )
        geometry = (
            f"min-width:{style.width}px;max-width:{style.width}px;"
            f"min-height:{style.height}px;max-height:{style.height}px;"
            if include_geometry_constraints
            else ""
        )
        rules.append(
            f"{selector}{{{geometry}padding:0;border-radius:{style.radius}px;"
            f"font-size:{max(1.0, style.font_point_size + font_size_adjustment):g}pt;}}"
        )
    rules.extend(
        (
            f'{scope}QPushButton[{BUTTON_WEIGHT_PROPERTY}="bold"], '
            f'{scope}QToolButton[{BUTTON_WEIGHT_PROPERTY}="bold"]'
            "{font-weight:700;}",
            f'{scope}QPushButton[{BUTTON_WEIGHT_PROPERTY}="regular"], '
            f'{scope}QToolButton[{BUTTON_WEIGHT_PROPERTY}="regular"]'
            "{font-weight:400;}",
        )
    )
    if not small_buttons_bold:
        rules.append(
            f'{scope}QPushButton[{BUTTON_TIER_PROPERTY}="small"], '
            f'{scope}QToolButton[{BUTTON_TIER_PROPERTY}="small"]'
            "{font-weight:400;}"
        )
    return "\n".join(rules)

_TEXT_WIDGET_TYPES = (
    QtWidgets.QAbstractButton,
    QtWidgets.QAbstractItemView,
    QtWidgets.QAbstractSpinBox,
    QtWidgets.QComboBox,
    QtWidgets.QGroupBox,
    QtWidgets.QLabel,
    QtWidgets.QLineEdit,
    QtWidgets.QMenu,
    QtWidgets.QMenuBar,
    QtWidgets.QPlainTextEdit,
    QtWidgets.QProgressBar,
    QtWidgets.QTabBar,
    QtWidgets.QTextEdit,
)


def _qss_font_family(value: object) -> str:
    family = " ".join(str(value or "").split())
    return "'" + family.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _adjust_qss_font_sizes(stylesheet: str, adjustment: float) -> str:
    if not adjustment:
        return str(stylesheet or "")

    def replace(match: re.Match[str]) -> str:
        size = max(1.0, float(match.group("size")) + adjustment)
        return f'{match.group("prefix")}{size:g}{match.group("unit")}'

    return _QSS_FONT_SIZE_PATTERN.sub(replace, str(stylesheet or ""))


@dataclass(frozen=True)
class ThemeTokens:
    """Semantic application colors shared by themed widgets and painters."""

    primary: str
    secondary: str
    accent: str
    background: str
    panel_background: str
    card_background: str
    control_background: str
    border: str
    text: str
    muted_text: str
    highlight: str
    hover: str
    active: str
    success: str
    warning: str
    error: str
    secondary_surface: str = ""
    control_border: str = ""
    secondary_accent: str = ""
    selected_background: str = ""
    selected_text: str = ""
    clear_action_background: str = ""
    clear_action_hover: str = ""
    clear_action_border: str = ""
    heading_text: str = ""
    disabled_text: str = ""
    artwork_button_text: str = ""
    artwork_button_disabled_text: str = ""
    artwork_button_shadow: str = ""
    preview_frame_border: str = ""

    def __post_init__(self) -> None:
        fallbacks = {
            "secondary_surface": "card_background",
            "control_border": "border",
            "secondary_accent": "secondary",
            "selected_background": "active",
            "selected_text": "highlight",
            "clear_action_background": "control_background",
            "clear_action_hover": "hover",
            "clear_action_border": "border",
            "heading_text": "highlight",
            "disabled_text": "muted_text",
            "artwork_button_text": "highlight",
            "artwork_button_disabled_text": "muted_text",
            "artwork_button_shadow": "background",
            "preview_frame_border": "border",
        }
        for token in fields(self):
            value = str(getattr(self, token.name) or "").strip()
            if not value and token.name in fallbacks:
                value = str(getattr(self, fallbacks[token.name])).strip()
            if not QtGui.QColor(value).isValid():
                raise ValueError(
                    f"Theme token {token.name!r} is not a valid Qt color: {value!r}"
                )
            object.__setattr__(self, token.name, value)

    # Complete semantic vocabulary.  The compact stored palette remains
    # backwards compatible while ordinary widgets consume meaning-based roles.
    @property
    def window_background(self) -> str:
        return self.background

    @property
    def panel_secondary(self) -> str:
        return self.secondary_surface

    @property
    def surface_raised(self) -> str:
        return self.control_background

    @property
    def text_primary(self) -> str:
        return self.text

    @property
    def text_secondary(self) -> str:
        return self.heading_text

    @property
    def text_muted(self) -> str:
        return self.muted_text

    @property
    def text_disabled(self) -> str:
        return self.disabled_text

    @property
    def text_heading(self) -> str:
        return self.heading_text

    @property
    def text_on_artwork(self) -> str:
        return self.artwork_button_text

    @property
    def text_on_artwork_disabled(self) -> str:
        return self.artwork_button_disabled_text

    @property
    def accent_primary(self) -> str:
        return self.primary

    @property
    def accent_secondary(self) -> str:
        return self.secondary_accent

    @property
    def accent_bright(self) -> str:
        return self.accent

    @property
    def accent_muted(self) -> str:
        return self.border

    @property
    def border_normal(self) -> str:
        return self.control_border

    @property
    def border_subtle(self) -> str:
        return self.card_background

    @property
    def border_hover(self) -> str:
        return self.primary

    @property
    def border_active(self) -> str:
        return self.secondary

    @property
    def border_focus(self) -> str:
        return self.accent

    @property
    def button_background(self) -> str:
        return self.control_background

    @property
    def button_hover(self) -> str:
        return self.hover

    @property
    def button_pressed(self) -> str:
        return self.active

    @property
    def button_disabled(self) -> str:
        return self.card_background

    @property
    def input_background(self) -> str:
        return self.control_background

    @property
    def input_border(self) -> str:
        return self.control_border

    @property
    def input_hover(self) -> str:
        return self.primary

    @property
    def input_focus(self) -> str:
        return self.accent

    @property
    def tab_background(self) -> str:
        return self.panel_background

    @property
    def tab_hover(self) -> str:
        return self.hover

    @property
    def tab_selected(self) -> str:
        return self.active

    @property
    def selection_background(self) -> str:
        return self.selected_background

    @property
    def selection_text(self) -> str:
        return self.selected_text

    @property
    def scrollbar_track(self) -> str:
        return self.panel_background

    @property
    def scrollbar_handle(self) -> str:
        return self.border

    @property
    def scrollbar_hover(self) -> str:
        return self.primary

    @property
    def separator(self) -> str:
        return self.border

    @property
    def glow_primary(self) -> str:
        return self.accent

    @property
    def glow_secondary(self) -> str:
        return self.secondary

    @property
    def tooltip_background(self) -> str:
        return self.control_background

    @property
    def tooltip_border(self) -> str:
        return self.border

    @property
    def tooltip_text(self) -> str:
        return self.text


def _safe_relative_path(value: str | Path, *, label: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or any(":" in part for part in candidate.parts)
        or any(ord(character) < 32 for character in raw)
    ):
        raise ValueError(f"{label} must be a safe relative path.")
    return candidate.as_posix()


@dataclass(frozen=True)
class ThemeDefinition:
    theme_id: str
    display_name: str
    tokens: ThemeTokens
    app_font_family: str = "Segoe UI"
    user_entry_font_family: str = "Segoe UI"
    uses_image_buttons: bool = True
    font_size_adjustment: float = 0.0
    small_buttons_bold: bool = True
    asset_overrides: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        theme_id = str(self.theme_id or "").strip().casefold()
        if not _THEME_ID_PATTERN.fullmatch(theme_id):
            raise ValueError(f"Invalid theme identifier: {self.theme_id!r}")

        display_name = str(self.display_name or "").strip()
        if not display_name:
            raise ValueError("A theme display name is required.")

        app_font_family = " ".join(str(self.app_font_family or "").split())
        user_entry_font_family = " ".join(
            str(self.user_entry_font_family or "").split()
        )
        if not app_font_family or not user_entry_font_family:
            raise ValueError("Theme font families cannot be empty.")
        if not isinstance(self.uses_image_buttons, bool):
            raise TypeError("Theme button presentation must be a boolean.")
        if not isinstance(self.small_buttons_bold, bool):
            raise TypeError("Theme small-button weight must be a boolean.")
        if isinstance(self.font_size_adjustment, bool):
            raise TypeError("Theme font-size adjustment must be numeric.")
        try:
            font_size_adjustment = float(self.font_size_adjustment)
        except (TypeError, ValueError) as error:
            raise TypeError("Theme font-size adjustment must be numeric.") from error
        if not math.isfinite(font_size_adjustment):
            raise ValueError("Theme font-size adjustment must be finite.")

        overrides: dict[str, str] = {}
        for logical_name, relative_path in dict(self.asset_overrides).items():
            logical = _safe_relative_path(logical_name, label="Asset name")
            override = _safe_relative_path(relative_path, label="Asset override")
            overrides[logical] = override

        object.__setattr__(self, "theme_id", theme_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "app_font_family", app_font_family)
        object.__setattr__(self, "font_size_adjustment", font_size_adjustment)
        object.__setattr__(
            self,
            "user_entry_font_family",
            user_entry_font_family,
        )
        object.__setattr__(
            self,
            "asset_overrides",
            MappingProxyType(overrides),
        )


CYBER_FORGE_THEME = ThemeDefinition(
    theme_id="cyber_forge",
    display_name="Cyber Forge",
    app_font_family="IBM Plex Sans",
    user_entry_font_family="IBM Plex Sans",
    tokens=ThemeTokens(
        primary="#009eb8",
        secondary="#7f9099",
        accent="#00bdd6",
        background="#d4d4d4",
        panel_background="#c8d0d3",
        card_background="#bdc7cb",
        control_background="#d0d5d7",
        border="#9aaab2",
        text="#005563",
        muted_text="#31565e",
        highlight="#006070",
        hover="#d0d4d4",
        active="#b8e8ee",
        success="#2f7955",
        warning="#8a6418",
        error="#a63f4c",
        secondary_surface="#b8c4c8",
        control_border="#92a4ac",
        secondary_accent="#778892",
        selected_background="#b8e8ee",
        selected_text="#003e49",
        clear_action_background="#c1cdd1",
        clear_action_hover="#ccd2d4",
        clear_action_border="#5f98a2",
        heading_text="#006070",
        disabled_text="#657c82",
        artwork_button_text="#80efff",
        artwork_button_disabled_text="#6f9ba2",
        artwork_button_shadow="#003e49",
        preview_frame_border="#009eb8",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


OBSIDIAN_FORGE_THEME = ThemeDefinition(
    theme_id="obsidian_forge",
    display_name="Obsidian Forge",
    app_font_family="Rajdhani",
    user_entry_font_family="Cinzel",
    tokens=ThemeTokens(
        primary="#c99a3d",
        secondary="#416dcc",
        accent="#f0c96c",
        background="#0d0e10",
        panel_background="#15171a",
        card_background="#1c1f23",
        control_background="#252a30",
        border="#555a62",
        text="#f0c96c",
        muted_text="#b5a47b",
        highlight="#ffe3a0",
        hover="#32343a",
        active="#3a4f7d",
        success="#4fb77d",
        warning="#d9a441",
        error="#d2525f",
        secondary_surface="#1f2226",
        control_border="#5e5b54",
        secondary_accent="#416dcc",
        selected_background="#3a4f7d",
        selected_text="#fff7e6",
        clear_action_background="#2b2924",
        clear_action_hover="#383226",
        clear_action_border="#c99a3d",
        heading_text="#ffe3a0",
        disabled_text="#7f7562",
        artwork_button_text="#080500",
        artwork_button_disabled_text="#5d4d2f",
        artwork_button_shadow="#ffe6a3",
        preview_frame_border="#c99a3d",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


VELVET_ROSE_THEME = ThemeDefinition(
    theme_id="velvet_rose",
    display_name="Velvet Rose",
    app_font_family="Source Sans 3",
    user_entry_font_family="Source Sans 3",
    tokens=ThemeTokens(
        primary="#e05b91",
        secondary="#c79a3b",
        accent="#c43865",
        background="#d4bdc8",
        panel_background="#caa6b8",
        card_background="#bf94aa",
        control_background="#d2b9c6",
        border="#d8a7b8",
        text="#5b1734",
        muted_text="#523640",
        highlight="#8e2853",
        hover="#d4cdd1",
        active="#f1b7cf",
        success="#367a55",
        warning="#8c6417",
        error="#a52f50",
        secondary_surface="#b98da3",
        control_border="#d8a7b8",
        secondary_accent="#c79a3b",
        selected_background="#f1b7cf",
        selected_text="#3a1221",
        clear_action_background="#c49caf",
        clear_action_hover="#d1c0c8",
        clear_action_border="#c43865",
        heading_text="#8e2853",
        disabled_text="#8d727d",
        artwork_button_text="#ff9bc5",
        artwork_button_disabled_text="#aa748d",
        artwork_button_shadow="#210a2b",
        preview_frame_border="#c43865",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


CELESTIAL_ROSE_THEME = ThemeDefinition(
    theme_id="celestial_rose",
    display_name="Celestial Rose",
    app_font_family="Manrope",
    user_entry_font_family="Lora",
    tokens=ThemeTokens(
        primary="#b89be8",
        secondary="#e56fae",
        accent="#dcc8ff",
        background="#100b18",
        panel_background="#191025",
        card_background="#241631",
        control_background="#30203f",
        border="#725d89",
        text="#e4d2ff",
        muted_text="#ad9bbe",
        highlight="#eadcff",
        hover="#463056",
        active="#58335f",
        success="#7caf91",
        warning="#d5aa68",
        error="#cb7486",
        secondary_surface="#281839",
        control_border="#725d89",
        secondary_accent="#e56fae",
        selected_background="#58335f",
        selected_text="#fff8ff",
        clear_action_background="#382344",
        clear_action_hover="#4b315a",
        clear_action_border="#b89be8",
        heading_text="#dcc8ff",
        disabled_text="#89799a",
        artwork_button_text="#f6ddff",
        artwork_button_disabled_text="#9b89ac",
        artwork_button_shadow="#100b18",
        preview_frame_border="#725d89",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


BASIC_DARK_THEME = ThemeDefinition(
    theme_id="dark",
    display_name="Dark",
    app_font_family="IBM Plex Sans",
    user_entry_font_family="Spectral",
    uses_image_buttons=False,
    tokens=ThemeTokens(
        primary="#6f9fd1",
        secondary="#8ab4df",
        accent="#9bc4ec",
        background="#17191b",
        panel_background="#202326",
        card_background="#292d31",
        control_background="#33383d",
        border="#515860",
        text="#f3f5f7",
        muted_text="#a9b0b7",
        highlight="#ffffff",
        hover="#41474d",
        active="#4b5f73",
        success="#65ad83",
        warning="#d2a85c",
        error="#d8747d",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


BASIC_LIGHT_THEME = ThemeDefinition(
    theme_id="light",
    display_name="Light",
    app_font_family="Source Sans 3",
    user_entry_font_family="Source Serif 4",
    uses_image_buttons=False,
    tokens=ThemeTokens(
        primary="#b8873e",
        secondary="#c9a66b",
        accent="#9a6b27",
        background="#d4d4d4",
        panel_background="#ccc8c0",
        card_background="#c4beb3",
        control_background="#d0ccc4",
        border="#cdb88f",
        text="#241c13",
        muted_text="#6d5c49",
        highlight="#120e09",
        hover="#d4d4d4",
        active="#d4bd87",
        success="#35764f",
        warning="#8a5e14",
        error="#9c3f46",
    ),
    asset_overrides=_STANDARD_ASSET_OVERRIDES,
)


# Compatibility exports for code that imported the former two definitions.
FUTURISTIC_THEME = CYBER_FORGE_THEME
SOFT_ELEGANT_THEME = VELVET_ROSE_THEME


THEMES: Mapping[str, ThemeDefinition] = MappingProxyType(
    {
        CYBER_FORGE_THEME.theme_id: CYBER_FORGE_THEME,
        OBSIDIAN_FORGE_THEME.theme_id: OBSIDIAN_FORGE_THEME,
        VELVET_ROSE_THEME.theme_id: VELVET_ROSE_THEME,
        CELESTIAL_ROSE_THEME.theme_id: CELESTIAL_ROSE_THEME,
        BASIC_DARK_THEME.theme_id: BASIC_DARK_THEME,
        BASIC_LIGHT_THEME.theme_id: BASIC_LIGHT_THEME,
    }
)


def normalize_theme_id(
    value: object,
    themes: Mapping[str, ThemeDefinition] = THEMES,
) -> str:
    candidate = str(value or "").strip().casefold()
    candidate = _LEGACY_THEME_ALIASES.get(candidate, candidate)
    return candidate if candidate in themes else DEFAULT_THEME_ID


def theme_family_for_id(theme_id: object) -> str:
    normalized = normalize_theme_id(theme_id)
    for family, theme_ids in THEME_FAMILIES.items():
        if normalized in theme_ids:
            return family
    return "forge"


class ThemeService(QtCore.QObject):
    """Own theme selection, Qt styles, and safe decorative asset lookup."""

    theme_changed = QtCore.Signal(str, object)

    def __init__(
        self,
        project_root: str | Path,
        *,
        settings: SettingsStore | None = None,
        themes: Mapping[str, ThemeDefinition] | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.settings = settings or SettingsStore(self.project_root)
        self._themes = self._validated_registry(THEMES if themes is None else themes)
        self.font_load_report = load_application_fonts(self.project_root)
        self._bindings: WeakKeyDictionary[
            QtWidgets.QWidget,
            str,
        ] = WeakKeyDictionary()
        self._semantic_roots: WeakKeyDictionary[
            QtWidgets.QWidget,
            bool,
        ] = WeakKeyDictionary()
        self._semantic_style_sources: WeakKeyDictionary[
            QtWidgets.QWidget,
            str,
        ] = WeakKeyDictionary()
        self._semantic_style_outputs: WeakKeyDictionary[
            QtWidgets.QWidget,
            str,
        ] = WeakKeyDictionary()
        self._semantic_font_sources: WeakKeyDictionary[
            QtWidgets.QWidget,
            QtGui.QFont,
        ] = WeakKeyDictionary()
        self._semantic_refresh_pending: WeakKeyDictionary[
            QtWidgets.QWidget,
            bool,
        ] = WeakKeyDictionary()
        self._reported_missing_assets: set[tuple[str, str]] = set()

        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

        stored = self.settings.get(THEME_SETTINGS_KEY, DEFAULT_THEME_ID)
        self._theme_id = normalize_theme_id(stored, self._themes)
        updates: dict[str, object] = {}
        if stored != self._theme_id:
            updates[THEME_SETTINGS_KEY] = self._theme_id
        if self.current.uses_image_buttons:
            family = theme_family_for_id(self._theme_id)
            if self.settings.get(THEME_FAMILY_SETTINGS_KEY, "") != family:
                updates[THEME_FAMILY_SETTINGS_KEY] = family
        if updates:
            self.settings.update_fields(updates)

    @staticmethod
    def _validated_registry(
        themes: Mapping[str, ThemeDefinition],
    ) -> Mapping[str, ThemeDefinition]:
        registry: dict[str, ThemeDefinition] = {}
        for key, definition in dict(themes).items():
            if not isinstance(definition, ThemeDefinition):
                raise TypeError("Theme registries must contain ThemeDefinition values.")
            normalized_key = str(key or "").strip().casefold()
            if normalized_key != definition.theme_id:
                raise ValueError(
                    "Theme registry keys must match their theme identifiers."
                )
            if normalized_key in registry:
                raise ValueError(
                    f"Duplicate theme identifier: {normalized_key!r}."
                )
            registry[normalized_key] = definition
        if DEFAULT_THEME_ID not in registry:
            raise ValueError(
                f"Theme registry must include {DEFAULT_THEME_ID!r}."
            )
        return MappingProxyType(registry)

    @property
    def theme_id(self) -> str:
        return self._theme_id

    @property
    def current(self) -> ThemeDefinition:
        return self._themes[self._theme_id]

    @property
    def tokens(self) -> ThemeTokens:
        return self.current.tokens

    @property
    def app_font_family(self) -> str:
        return resolve_registered_family(self.current.app_font_family)

    @property
    def user_entry_font_family(self) -> str:
        return resolve_registered_family(self.current.user_entry_font_family)

    def available_themes(self) -> tuple[ThemeDefinition, ...]:
        return tuple(self._themes.values())

    def set_theme(
        self,
        theme_id: object,
        *,
        persist: bool = True,
    ) -> ThemeDefinition:
        requested = str(theme_id or "").strip().casefold()
        selected = _LEGACY_THEME_ALIASES.get(requested, requested)
        if selected not in self._themes:
            raise ValueError(f"Unknown theme identifier: {theme_id!r}.")
        if persist:
            updates: dict[str, object] = {THEME_SETTINGS_KEY: selected}
            if self._themes[selected].uses_image_buttons:
                updates[THEME_FAMILY_SETTINGS_KEY] = theme_family_for_id(selected)
            self.settings.update_fields(updates)
        if selected == self._theme_id:
            return self.current

        self._theme_id = selected
        self._refresh_bindings()
        definition = self.current
        self.theme_changed.emit(definition.theme_id, definition)
        return definition

    def save(self) -> dict[str, object]:
        """Persist and reapply the selected theme for an explicit Save action."""
        updates: dict[str, object] = {THEME_SETTINGS_KEY: self._theme_id}
        if self.current.uses_image_buttons:
            updates[THEME_FAMILY_SETTINGS_KEY] = theme_family_for_id(
                self._theme_id
            )
        snapshot = self.settings.update_fields(updates)
        self._refresh_bindings()
        return snapshot

    def build_stylesheet(self) -> str:
        """Build role-based QSS from the current semantic token set."""
        c = self.tokens
        app_font = _qss_font_family(self.app_font_family)
        user_entry_font = _qss_font_family(self.user_entry_font_family)
        font_size_adjustment = self.current.font_size_adjustment
        tier_styles = _button_tier_stylesheet(
            "",
            font_size_adjustment=font_size_adjustment,
            small_buttons_bold=self.current.small_buttons_bold,
            tier_styles=(
                BUTTON_TIER_STYLES
                if self.current.uses_image_buttons
                else BASIC_BUTTON_TIER_STYLES
            ),
            include_geometry_constraints=self.current.uses_image_buttons,
        )
        basic_button_states = ""
        if not self.current.uses_image_buttons:
            basic_button_states = f"""
QPushButton[themeRole="button"]:pressed,
QToolButton[themeRole="button"]:pressed,
QPushButton[themeRole="button"]:checked,
QToolButton[themeRole="button"]:checked {{
    background: {c.active};
    border-color: {c.secondary};
}}
QPushButton[themeRole="button"]:disabled,
QToolButton[themeRole="button"]:disabled {{
    background: {c.card_background};
    border-color: {c.border};
    color: {c.disabled_text};
}}
""".strip()
        return f"""
QWidget[themeRole="background"], QMainWindow[themeRole="background"] {{
    background: {c.background};
    color: {c.text};
}}
QPushButton, QToolButton {{ text-align: center; }}
QWidget[themeRole="panel"] {{
    background: {c.panel_background};
    border: 1px solid {c.border};
}}
QWidget[themeRole="card"] {{
    background: {c.card_background};
    border: 1px solid {c.border};
}}
QWidget[themeRole="secondarySurface"] {{
    background: {c.secondary_surface};
    border: 1px solid {c.control_border};
}}
QLabel[themeRole="text"] {{ color: {c.text}; }}
QLabel[themeRole="mutedText"] {{ color: {c.muted_text}; }}
QLabel[themeRole="headingText"], QLabel[themeRole="highlightText"] {{
    color: {c.heading_text};
}}
QLabel[themeRole="headingText"] {{
    font-family: {app_font};
    font-size: {TAB_HEADING_FONT_POINT_SIZE + font_size_adjustment:g}pt;
    font-weight: 700;
}}
QPushButton[themeRole="button"], QToolButton[themeRole="button"] {{
    background: {c.control_background};
    border: 1px solid {c.border};
    color: {c.text};
    text-align: center;
}}
QPushButton[themeRole="button"]:hover,
QToolButton[themeRole="button"]:hover {{
    background: {c.hover};
    border-color: {c.accent};
}}
QPushButton[themeRole="clearAction"],
QToolButton[themeRole="clearAction"] {{
    background: {c.clear_action_background};
    border: 1px solid {c.clear_action_border};
    color: {c.text};
}}
QPushButton[themeRole="clearAction"]:hover,
QToolButton[themeRole="clearAction"]:hover {{
    background: {c.clear_action_hover};
    border-color: {c.accent};
}}
{basic_button_states}
QPushButton[themeRole="accentButton"],
QToolButton[themeRole="accentButton"] {{
    background: {c.control_background};
    border: 1px solid {c.accent};
    color: {c.heading_text};
}}
QPushButton[themeRole="accentButton"]:hover,
QToolButton[themeRole="accentButton"]:hover {{ background: {c.active}; }}
QWidget[themeRole="success"] {{ color: {c.success}; }}
QWidget[themeRole="warning"] {{ color: {c.warning}; }}
QWidget[themeRole="error"] {{ color: {c.error}; }}
QWidget[themeRole="selectedState"] {{
    background: {c.selected_background};
    color: {c.selected_text};
    border-color: {c.secondary_accent};
}}
QLineEdit[themeRole="selectedState"] {{
    background: {c.selected_background};
    color: {c.selected_text};
    border: 1px solid {c.secondary_accent};
    border-radius: 5px;
    padding: 5px;
    selection-background-color: {c.selected_background};
    selection-color: {c.selected_text};
}}
QLineEdit[themeRole="selectedState"]:focus {{
    border-color: {c.input_focus};
}}
QLineEdit[themeRole="input"], QComboBox[themeRole="input"] {{
    background: {c.control_background};
    border: 1px solid {c.border};
    color: {c.text};
    selection-background-color: {c.active};
}}
{tier_styles}
*[themeFontRole="application"] {{ font-family: {app_font}; }}
*[themeFontRole="userEntry"] {{ font-family: {user_entry_font}; }}
""".strip()

    def build_ordinary_stylesheet(self) -> str:
        """Build complete ordinary-control styling for a scoped themed page."""
        c = self.tokens
        app_font = _qss_font_family(self.app_font_family)
        user_entry_font = _qss_font_family(self.user_entry_font_family)
        rose_family = theme_family_for_id(self._theme_id) == "rose"
        radius = 9 if rose_family else 4
        small_radius = 6 if rose_family else 3
        border_width = 1 if rose_family else 2
        font_size_adjustment = self.current.font_size_adjustment
        tier_styles = _button_tier_stylesheet(
            'QWidget[ordinaryThemeRoot="true"] ',
            font_size_adjustment=font_size_adjustment,
            small_buttons_bold=self.current.small_buttons_bold,
            tier_styles=(
                BUTTON_TIER_STYLES
                if self.current.uses_image_buttons
                else BASIC_BUTTON_TIER_STYLES
            ),
            include_geometry_constraints=self.current.uses_image_buttons,
        )
        basic_checked_state = ""
        if not self.current.uses_image_buttons:
            basic_checked_state = f"""
QWidget[ordinaryThemeRoot="true"] QPushButton:checked,
QWidget[ordinaryThemeRoot="true"] QToolButton:checked {{
    background: {c.button_pressed};
    border-color: {c.border_active};
}}
""".strip()
        return f"""
QWidget[ordinaryThemeRoot="true"] {{
    background-color: {c.window_background};
    color: {c.text_primary};
}}
QWidget[ordinaryThemeRoot="true"] QLabel {{
    color: {c.text_primary};
}}
QWidget[ordinaryThemeRoot="true"] QLabel[themeRole="headingText"] {{
    color: {c.heading_text};
    font-family: {app_font};
    font-size: {TAB_HEADING_FONT_POINT_SIZE + font_size_adjustment:g}pt;
    font-weight: 700;
}}
QWidget[ordinaryThemeRoot="true"] QPushButton,
QWidget[ordinaryThemeRoot="true"] QToolButton {{
    background: {c.button_background};
    color: {c.text_primary};
    border: {border_width}px solid {c.border_normal};
    border-radius: {radius}px;
    text-align: center;
}}
QWidget[ordinaryThemeRoot="true"] QPushButton:hover,
QWidget[ordinaryThemeRoot="true"] QToolButton:hover {{
    background: {c.button_hover};
    border-color: {c.border_hover};
    color: {c.text_secondary};
}}
QWidget[ordinaryThemeRoot="true"] QPushButton[themeRole="clearAction"],
QWidget[ordinaryThemeRoot="true"] QToolButton[themeRole="clearAction"] {{
    background: {c.clear_action_background};
    color: {c.text_primary};
    border-color: {c.clear_action_border};
}}
QWidget[ordinaryThemeRoot="true"] QPushButton[themeRole="clearAction"]:hover,
QWidget[ordinaryThemeRoot="true"] QToolButton[themeRole="clearAction"]:hover {{
    background: {c.clear_action_hover};
    border-color: {c.accent_bright};
}}
QWidget[ordinaryThemeRoot="true"] QPushButton:pressed,
QWidget[ordinaryThemeRoot="true"] QToolButton:pressed {{
    background: {c.button_pressed};
    border-color: {c.border_active};
}}
{basic_checked_state}
QWidget[ordinaryThemeRoot="true"] QPushButton:disabled,
QWidget[ordinaryThemeRoot="true"] QToolButton:disabled {{
    background: {c.button_disabled};
    color: {c.text_disabled};
    border-color: {c.border_subtle};
}}
{tier_styles}
QWidget[ordinaryThemeRoot="true"] QLineEdit,
QWidget[ordinaryThemeRoot="true"] QTextEdit,
QWidget[ordinaryThemeRoot="true"] QPlainTextEdit,
QWidget[ordinaryThemeRoot="true"] QSpinBox,
QWidget[ordinaryThemeRoot="true"] QDoubleSpinBox,
QWidget[ordinaryThemeRoot="true"] QComboBox,
QWidget[ordinaryThemeRoot="true"] QListView,
QWidget[ordinaryThemeRoot="true"] QListWidget,
QWidget[ordinaryThemeRoot="true"] QTreeView,
QWidget[ordinaryThemeRoot="true"] QTableView {{
    background: {c.input_background};
    color: {c.text_primary};
    border: 1px solid {c.input_border};
    border-radius: {small_radius}px;
    selection-background-color: {c.selection_background};
    selection-color: {c.selection_text};
}}
QWidget[ordinaryThemeRoot="true"] QLineEdit:hover,
QWidget[ordinaryThemeRoot="true"] QTextEdit:hover,
QWidget[ordinaryThemeRoot="true"] QPlainTextEdit:hover,
QWidget[ordinaryThemeRoot="true"] QSpinBox:hover,
QWidget[ordinaryThemeRoot="true"] QComboBox:hover {{
    border-color: {c.input_hover};
}}
QWidget[ordinaryThemeRoot="true"] QLineEdit:focus,
QWidget[ordinaryThemeRoot="true"] QTextEdit:focus,
QWidget[ordinaryThemeRoot="true"] QPlainTextEdit:focus,
QWidget[ordinaryThemeRoot="true"] QSpinBox:focus,
QWidget[ordinaryThemeRoot="true"] QComboBox:focus {{
    border-color: {c.input_focus};
}}
QWidget[ordinaryThemeRoot="true"] QLineEdit[themeRole="selectedState"] {{
    background: {c.selection_background};
    color: {c.selection_text};
    border-color: {c.secondary_accent};
}}
QWidget[ordinaryThemeRoot="true"] QLineEdit[themeRole="selectedState"]:focus {{
    border-color: {c.input_focus};
}}
QWidget[ordinaryThemeRoot="true"] QComboBox::drop-down {{
    border: none;
    border-left: 1px solid {c.separator};
}}
QWidget[ordinaryThemeRoot="true"] QCheckBox,
QWidget[ordinaryThemeRoot="true"] QRadioButton {{
    color: {c.text_primary};
    spacing: 6px;
}}
QWidget[ordinaryThemeRoot="true"] QCheckBox::indicator,
QWidget[ordinaryThemeRoot="true"] QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    background: {c.input_background};
    border: 1px solid {c.input_border};
}}
QWidget[ordinaryThemeRoot="true"] QCheckBox::indicator {{
    border-radius: {small_radius}px;
}}
QWidget[ordinaryThemeRoot="true"] QRadioButton::indicator {{
    border-radius: 8px;
}}
QWidget[ordinaryThemeRoot="true"] QCheckBox::indicator:checked,
QWidget[ordinaryThemeRoot="true"] QRadioButton::indicator:checked {{
    background: {c.accent_primary};
    border-color: {c.accent_bright};
}}
QWidget[ordinaryThemeRoot="true"] QSlider::groove:horizontal {{
    height: 5px;
    background: {c.scrollbar_track};
    border: 1px solid {c.border_subtle};
    border-radius: 2px;
}}
QWidget[ordinaryThemeRoot="true"] QSlider::handle:horizontal {{
    width: 15px;
    margin: -6px 0;
    background: {c.accent_primary};
    border: 1px solid {c.accent_bright};
    border-radius: 7px;
}}
QWidget[ordinaryThemeRoot="true"] QScrollBar:vertical {{
    background: {c.scrollbar_track};
    width: 10px;
}}
QWidget[ordinaryThemeRoot="true"] QScrollBar::handle:vertical {{
    background: {c.scrollbar_handle};
    border-radius: 4px;
    min-height: 24px;
}}
QWidget[ordinaryThemeRoot="true"] QScrollBar::handle:vertical:hover {{
    background: {c.scrollbar_hover};
}}
QWidget[ordinaryThemeRoot="true"] QScrollBar::add-line:vertical,
QWidget[ordinaryThemeRoot="true"] QScrollBar::sub-line:vertical,
QWidget[ordinaryThemeRoot="true"] QScrollBar::add-page:vertical,
QWidget[ordinaryThemeRoot="true"] QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
    height: 0;
}}
QWidget[ordinaryThemeRoot="true"] QMenu {{
    background: {c.surface_raised};
    color: {c.text_primary};
    border: 1px solid {c.border_normal};
}}
QWidget[ordinaryThemeRoot="true"] QMenu::item:selected {{
    background: {c.selection_background};
    color: {c.selection_text};
}}
QWidget[ordinaryThemeRoot="true"] QToolTip {{
    background: {c.tooltip_background};
    color: {c.tooltip_text};
    border: 1px solid {c.tooltip_border};
    padding: 4px 7px;
}}
QWidget[ordinaryThemeRoot="true"] *[themeFontRole="application"] {{
    font-family: {app_font};
}}
QWidget[ordinaryThemeRoot="true"] *[themeFontRole="userEntry"] {{
    font-family: {user_entry_font};
}}
""".strip()

    def apply_semantic_styles(self, root: QtWidgets.QWidget) -> None:
        """Apply semantic colors and centralized typography to one UI tree."""
        if not isinstance(root, QtWidgets.QWidget):
            raise TypeError("Semantic themes can only style QWidget instances.")
        if self._is_theme_independent(root, stop=None):
            return

        self._semantic_roots[root] = True
        root.setProperty("ordinaryThemeRoot", True)
        widgets = (root, *root.findChildren(QtWidgets.QWidget))
        for widget in widgets:
            if widget not in self._semantic_font_sources:
                self._semantic_font_sources[widget] = QtGui.QFont(widget.font())
        stop = root.parentWidget()
        replaceable_families = self._replaceable_ui_font_families()
        for widget in widgets:
            if self._is_theme_independent(widget, stop=stop):
                continue
            font_role = self._font_role_in_hierarchy(widget, stop=stop)
            letter_content = font_role == LETTER_CONTENT_FONT_ROLE.casefold()
            current_style = widget.styleSheet()
            previous_output = self._semantic_style_outputs.get(widget)
            if widget not in self._semantic_style_sources or current_style != previous_output:
                self._semantic_style_sources[widget] = current_style
            source = self._semantic_style_sources.get(widget, "")
            font_family = (
                self.user_entry_font_family
                if font_role == USER_ENTRY_FONT_ROLE.casefold()
                else self.app_font_family
            )
            themed = self._translate_semantic_qss(
                source,
                font_family=None if letter_content else font_family,
            )
            if (
                not letter_content
                and themed.strip()
                and _QSS_INTERFACE_FONT_PATTERN.search(source)
                and "{" in source
                and "}" in source
            ):
                themed = f"{themed.strip()}\n{self._font_role_stylesheet()}"
            if widget is root:
                ordinary_qss = self.build_ordinary_stylesheet()
                themed = f"{themed.strip()}\n{ordinary_qss}" if themed.strip() else ordinary_qss
            self._semantic_style_outputs[widget] = themed
            if current_style != themed:
                widget.setStyleSheet(themed)
            widget.setProperty("letterSmithTheme", self._theme_id)
            if not letter_content:
                self._apply_widget_typography(
                    widget,
                    font_family,
                    font_role,
                    replaceable_families,
                )
            self._apply_button_geometry(widget)
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
            if not self.current.uses_image_buttons:
                self._apply_button_geometry(widget)
            widget.update()

    def _apply_button_geometry(self, widget: QtWidgets.QWidget) -> None:
        if not isinstance(widget, QtWidgets.QAbstractButton):
            return
        tier_value = str(widget.property(BUTTON_TIER_PROPERTY) or "").strip()
        if not tier_value:
            return
        try:
            tier = ButtonTier(tier_value)
        except ValueError:
            return
        use_full_tier_geometry = bool(
            widget.property(BUTTON_FULL_TIER_GEOMETRY_PROPERTY)
        )
        styles = (
            BUTTON_TIER_STYLES
            if self.current.uses_image_buttons or use_full_tier_geometry
            else BASIC_BUTTON_TIER_STYLES
        )
        widget.setFixedSize(styles[tier].size)
        widget.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Fixed,
        )
        widget.updateGeometry()

    def _font_role_stylesheet(self) -> str:
        return (
            "*[themeFontRole=\"application\"]{font-family:"
            f"{_qss_font_family(self.app_font_family)};}}"
            "*[themeFontRole=\"userEntry\"]{font-family:"
            f"{_qss_font_family(self.user_entry_font_family)};}}"
        )

    def _translate_semantic_qss(
        self,
        stylesheet: str,
        *,
        font_family: str | None = None,
    ) -> str:
        tokens = self.tokens

        def replacement(match: re.Match[str]) -> str:
            color = match.group(0).casefold()
            if len(color) == 4:
                color = "#" + "".join(channel * 2 for channel in color[1:])
            role = _CYBER_UI_COLOR_ROLES.get(color)
            if role is None:
                return match.group(0)
            return str(getattr(tokens, role))

        translated = _QSS_HEX_COLOR_PATTERN.sub(
            replacement,
            str(stylesheet or ""),
        )
        if font_family:
            translated = _QSS_INTERFACE_FONT_PATTERN.sub(
                _qss_font_family(font_family),
                translated,
            )
            translated = _adjust_qss_font_sizes(
                translated,
                self.current.font_size_adjustment,
            )
        return translated

    @staticmethod
    def _font_role_in_hierarchy(
        widget: QtWidgets.QWidget,
        *,
        stop: QtWidgets.QWidget | None,
    ) -> str:
        current: QtWidgets.QWidget | None = widget
        inherited_role = ""
        while current is not None and current is not stop:
            role = str(
                current.property(THEME_FONT_ROLE_PROPERTY) or ""
            ).strip().casefold()
            if role == LETTER_CONTENT_FONT_ROLE.casefold():
                return role
            if not inherited_role and role:
                inherited_role = role
            current = current.parentWidget()
        return inherited_role

    def _apply_widget_typography(
        self,
        widget: QtWidgets.QWidget,
        family: str,
        font_role: str,
        replaceable_families: frozenset[str],
    ) -> None:
        if (
            font_role not in {
                APPLICATION_FONT_ROLE.casefold(),
                USER_ENTRY_FONT_ROLE.casefold(),
            }
            and not isinstance(widget, _TEXT_WIDGET_TYPES)
        ):
            return
        source_font = self._semantic_font_sources.get(widget)
        font = QtGui.QFont(source_font or widget.font())
        explicit_role = font_role in {
            APPLICATION_FONT_ROLE.casefold(),
            USER_ENTRY_FONT_ROLE.casefold(),
        }
        if (
            not explicit_role
            and font.family().strip().casefold() not in replaceable_families
        ):
            return
        font.setFamily(family)
        point_size = font.pointSizeF()
        if point_size > 0:
            font.setPointSizeF(
                max(1.0, point_size + self.current.font_size_adjustment)
            )
        elif font.pixelSize() > 0:
            font.setPixelSize(
                max(1, round(font.pixelSize() + self.current.font_size_adjustment))
            )
        if (
            widget.property(BUTTON_TIER_PROPERTY) == ButtonTier.SMALL.value
            and not self.current.small_buttons_bold
        ):
            font.setWeight(QtGui.QFont.Weight.Normal)
        widget.setProperty(
            _VELVET_FONT_ADJUSTED_PROPERTY,
            bool(self.current.font_size_adjustment),
        )
        widget.setFont(font)

    def _replaceable_ui_font_families(self) -> frozenset[str]:
        families = {
            "segoe ui",
            "segoe ui semibold",
        }
        application = QtWidgets.QApplication.instance()
        if application is not None:
            default_family = application.font().family().strip().casefold()
            if default_family:
                families.add(default_family)
        for definition in self._themes.values():
            families.add(definition.app_font_family.casefold())
            families.add(definition.user_entry_font_family.casefold())
        return frozenset(families)

    @staticmethod
    def _is_theme_independent(
        widget: QtWidgets.QWidget,
        *,
        stop: QtWidgets.QWidget | None,
    ) -> bool:
        current: QtWidgets.QWidget | None = widget
        while current is not None and current is not stop:
            if bool(current.property("themeIndependent")):
                return True
            current = current.parentWidget()
        return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """Keep ordinary dialogs and runtime-created controls on the active theme."""
        if (
            event.type() == QtCore.QEvent.Type.Show
            and isinstance(watched, QtWidgets.QDialog)
            and self._belongs_to_theme_host(watched)
            and not self._is_theme_independent(watched, stop=None)
        ):
            try:
                self.apply_semantic_styles(watched)
            except (RuntimeError, TypeError, ValueError):
                _LOGGER.exception(
                    "Theme styles could not be applied to %s.",
                    type(watched).__name__,
                )
        if isinstance(watched, QtWidgets.QWidget) and event.type() in (
            QtCore.QEvent.Type.ChildAdded,
            QtCore.QEvent.Type.StyleChange,
        ):
            root = self._semantic_root_for(watched)
            if root is not None:
                current_style = watched.styleSheet()
                previous_output = self._semantic_style_outputs.get(watched)
                if (
                    event.type() == QtCore.QEvent.Type.ChildAdded
                    or current_style != previous_output
                ):
                    self._queue_semantic_refresh(root)
        return super().eventFilter(watched, event)

    def _semantic_root_for(
        self,
        widget: QtWidgets.QWidget,
    ) -> QtWidgets.QWidget | None:
        current: QtWidgets.QWidget | None = widget
        while current is not None:
            if current in self._semantic_roots:
                return current
            current = current.parentWidget()
        return None

    def _queue_semantic_refresh(self, root: QtWidgets.QWidget) -> None:
        if self._semantic_refresh_pending.get(root, False):
            return
        self._semantic_refresh_pending[root] = True
        QtCore.QTimer.singleShot(
            0,
            lambda semantic_root=root: self._refresh_semantic_root(semantic_root),
        )

    def _refresh_semantic_root(self, root: QtWidgets.QWidget) -> None:
        self._semantic_refresh_pending.pop(root, None)
        try:
            self.apply_semantic_styles(root)
        except RuntimeError:
            self._semantic_roots.pop(root, None)

    def _belongs_to_theme_host(self, widget: QtWidgets.QWidget) -> bool:
        current: QtWidgets.QWidget | None = widget
        while current is not None:
            if str(current.property("letterSmithTheme") or ""):
                return True
            current = current.parentWidget()
        return False

    def apply(
        self,
        widget: QtWidgets.QWidget,
        *,
        additional_qss: str = "",
    ) -> QtWidgets.QWidget:
        if not isinstance(widget, QtWidgets.QWidget):
            raise TypeError("Themes can only be applied to QWidget instances.")
        self._bindings[widget] = str(additional_qss or "")
        self._apply_to_widget(widget)
        return widget

    def unbind(self, widget: QtWidgets.QWidget) -> None:
        self._bindings.pop(widget, None)

    def _apply_to_widget(self, widget: QtWidgets.QWidget) -> None:
        additional_qss = self._bindings.get(widget, "")
        stylesheet = self.build_stylesheet()
        if additional_qss.strip():
            themed_additional_qss = _adjust_qss_font_sizes(
                additional_qss.strip(),
                self.current.font_size_adjustment,
            )
            stylesheet = f"{themed_additional_qss}\n{stylesheet}"
        widget.setProperty("letterSmithTheme", self._theme_id)
        widget.setStyleSheet(stylesheet)
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()

    def _refresh_bindings(self) -> None:
        for widget in tuple(self._bindings.keys()):
            try:
                self._apply_to_widget(widget)
            except RuntimeError:
                self._bindings.pop(widget, None)
        for root in tuple(self._semantic_roots.keys()):
            try:
                self.apply_semantic_styles(root)
            except RuntimeError:
                self._semantic_roots.pop(root, None)

    def resolve_asset(
        self,
        logical_name: str | Path,
        *,
        fallback: str | Path | None = None,
        allow_baseline: bool = True,
    ) -> Path:
        """Resolve active-theme artwork, then the Cyber Forge baseline."""
        return self.resolve_first_asset(
            (logical_name,),
            fallback=fallback,
            allow_baseline=allow_baseline,
        )

    def resolve_button_asset(
        self,
        asset_id: str,
        *,
        broken: bool = False,
        long_form: bool = False,
        allow_baseline: bool = True,
    ) -> Path:
        """Resolve one fixed themed-button assignment without scanning a pool."""
        safe_asset_id = _safe_relative_path(
            asset_id,
            label="Button asset identifier",
        )
        requested = PurePosixPath(safe_asset_id)
        if len(requested.parts) != 1 or not requested.stem:
            raise ValueError("Button asset identifiers must be plain filenames.")
        stem = requested.stem
        requested_suffix = requested.suffix.casefold()
        supported = (".png", ".webp", ".jpg", ".jpeg", ".gif")
        if requested_suffix and requested_suffix not in supported:
            raise ValueError(f"Unsupported button artwork format: {requested_suffix}")
        suffixes = (requested_suffix,) if requested_suffix else supported

        subdirectories = ["buttons"]
        if long_form:
            subdirectories.append("long")
        if broken:
            subdirectories.append("broken")

        paths = application_paths(self.project_root)
        themes_root = paths.app_resource_path("themes").resolve()
        relative_description = "/".join((*subdirectories, f"{stem}.*"))

        def find(theme_id: str) -> Path | None:
            theme_root = (themes_root / theme_id).resolve()
            self._require_within(theme_root, themes_root, label="Theme root")
            current = theme_root
            for directory_name in subdirectories:
                if not current.is_dir():
                    return None
                match = next(
                    (
                        child
                        for child in current.iterdir()
                        if child.is_dir()
                        and child.name.casefold() == directory_name.casefold()
                    ),
                    None,
                )
                if match is None:
                    return None
                current = match.resolve()
                self._require_within(
                    current,
                    theme_root,
                    label="Button asset directory",
                )
            for suffix in suffixes:
                expected = f"{stem}{suffix}".casefold()
                match = next(
                    (
                        child.resolve()
                        for child in current.iterdir()
                        if child.is_file() and child.name.casefold() == expected
                    ),
                    None,
                )
                if match is not None:
                    self._require_within(match, theme_root, label="Button asset")
                    return match
            return None

        themed = find(self._theme_id)
        if themed is not None:
            return themed

        missing_key = (self._theme_id, relative_description)
        if self.current.uses_image_buttons and missing_key not in self._reported_missing_assets:
            self._reported_missing_assets.add(missing_key)
            _LOGGER.warning(
                "Theme button asset is missing%s: "
                "theme=%s asset=%s",
                "; using the Cyber Forge baseline" if allow_baseline else "",
                self._theme_id,
                relative_description,
            )

        if not allow_baseline:
            suffix = suffixes[0]
            return themes_root / self._theme_id / Path(*subdirectories) / f"{stem}{suffix}"

        baseline = find(DEFAULT_THEME_ID)
        if baseline is not None:
            return baseline
        suffix = suffixes[0]
        return themes_root / DEFAULT_THEME_ID / Path(*subdirectories) / f"{stem}{suffix}"

    def resolve_first_asset(
        self,
        logical_names: tuple[str | Path, ...],
        *,
        fallback: str | Path | None = None,
        allow_baseline: bool = True,
    ) -> Path:
        """Resolve candidates within the active theme before its fallback."""
        logicals = tuple(
            _safe_relative_path(logical_name, label="Asset name")
            for logical_name in logical_names
        )
        if not logicals:
            raise ValueError("At least one asset candidate is required.")
        fallback_relative = (
            _safe_relative_path(fallback, label="Fallback asset")
            if fallback is not None
            else None
        )
        paths = application_paths(self.project_root)
        resource_root = paths.resource_root
        themes_root = paths.app_resource_path("themes").resolve()
        theme_root = (themes_root / self._theme_id).resolve()
        self._require_within(theme_root, themes_root, label="Theme root")
        for logical in logicals:
            relative_override = self.current.asset_overrides.get(logical, logical)
            relative_override = _safe_relative_path(
                relative_override,
                label="Asset override",
            )
            themed_path = (theme_root / relative_override).resolve()
            self._require_within(themed_path, theme_root, label="Themed asset")
            if themed_path.is_file():
                return themed_path

        missing_key = (self._theme_id, " | ".join(logicals))
        if (
            self.current.uses_image_buttons
            and missing_key not in self._reported_missing_assets
        ):
            self._reported_missing_assets.add(missing_key)
            _LOGGER.warning(
                "Theme asset is missing%s: "
                "theme=%s asset=%s",
                "; using the Cyber Forge baseline" if allow_baseline else "",
                self._theme_id,
                missing_key[1],
            )

        if not allow_baseline:
            if fallback_relative is not None:
                fallback_path = (resource_root / fallback_relative).resolve()
                self._require_within(
                    fallback_path,
                    resource_root,
                    label="Fallback asset",
                )
                return fallback_path
            first_override = self.current.asset_overrides.get(logicals[0], logicals[0])
            missing_path = (theme_root / first_override).resolve()
            self._require_within(
                missing_path,
                theme_root,
                label="Themed asset",
            )
            return missing_path

        baseline = self._themes[DEFAULT_THEME_ID]
        baseline_root = (themes_root / DEFAULT_THEME_ID).resolve()
        self._require_within(
            baseline_root,
            themes_root,
            label="Baseline theme root",
        )
        baseline_candidates: list[Path] = []
        for logical in logicals:
            baseline_relative = baseline.asset_overrides.get(logical, logical)
            baseline_relative = _safe_relative_path(
                baseline_relative,
                label="Baseline asset override",
            )
            baseline_path = (baseline_root / baseline_relative).resolve()
            self._require_within(
                baseline_path,
                baseline_root,
                label="Baseline theme asset",
            )
            baseline_candidates.append(baseline_path)
            if baseline_path.is_file():
                return baseline_path

        if fallback is not None:
            assert fallback_relative is not None
            fallback_path = (resource_root / fallback_relative).resolve()
            self._require_within(
                fallback_path,
                resource_root,
                label="Fallback asset",
            )
            return fallback_path
        return baseline_candidates[0]

    @staticmethod
    def _require_within(path: Path, root: Path, *, label: str) -> None:
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"{label} escapes its allowed directory.") from error


__all__ = [
    "APPLICATION_FONT_ROLE",
    "BASIC_BUTTON_TIER_STYLES",
    "BASIC_DARK_THEME",
    "BASIC_LIGHT_THEME",
    "BUTTON_FULL_TIER_GEOMETRY_PROPERTY",
    "BUTTON_TIER_PROPERTY",
    "BUTTON_TIER_STYLES",
    "BUTTON_WEIGHT_PROPERTY",
    "ButtonTier",
    "ButtonTierStyle",
    "CELESTIAL_ROSE_THEME",
    "CYBER_FORGE_THEME",
    "DEFAULT_THEME_ID",
    "DEFAULT_FORGE_THEME_ID",
    "DEFAULT_ROSE_THEME_ID",
    "FUTURISTIC_THEME",
    "HELP_THEME_ASSET_CANDIDATES",
    "LETTER_CONTENT_FONT_ROLE",
    "LayoutMetrics",
    "MAXIMIZE_THEME_ASSET",
    "OBSIDIAN_FORGE_THEME",
    "REQUIRED_CONTENT_THEME_ASSETS",
    "REQUIRED_THEME_ASSETS",
    "RESTORE_THEME_ASSET_CANDIDATES",
    "ROW_LAYOUT_SPACING",
    "PRIMARY_PAGE_LAYOUT",
    "SECTION_LAYOUT_SPACING",
    "SOUND_PAGE_LAYOUT",
    "SOFT_ELEGANT_THEME",
    "TAB_HEADING_FONT_POINT_SIZE",
    "THEMES",
    "THEME_FAMILIES",
    "THEME_FAMILY_SETTINGS_KEY",
    "THEME_FONT_ROLE_PROPERTY",
    "THEME_SETTINGS_KEY",
    "USER_ENTRY_FONT_ROLE",
    "VELVET_ROSE_THEME",
    "ThemeDefinition",
    "ThemeService",
    "ThemeTokens",
    "apply_button_tier",
    "apply_tab_heading_style",
    "normalize_theme_id",
    "theme_family_for_id",
]
