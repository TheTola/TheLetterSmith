from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping
from weakref import WeakKeyDictionary

from PySide6 import QtCore, QtGui, QtWidgets

from settings_store import SettingsStore
from project_paths import application_paths


THEME_SETTINGS_KEY = "application_theme"
DEFAULT_THEME_ID = "futuristic"

_THEME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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

    def __post_init__(self) -> None:
        for token in fields(self):
            value = str(getattr(self, token.name) or "").strip()
            if not QtGui.QColor(value).isValid():
                raise ValueError(
                    f"Theme token {token.name!r} is not a valid Qt color: {value!r}"
                )
            object.__setattr__(self, token.name, value)


def _safe_relative_path(value: str | Path, *, label: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or ":" in candidate.parts[0]
    ):
        raise ValueError(f"{label} must be a safe relative path.")
    return candidate.as_posix()


@dataclass(frozen=True)
class ThemeDefinition:
    theme_id: str
    display_name: str
    tokens: ThemeTokens
    asset_overrides: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        theme_id = str(self.theme_id or "").strip().casefold()
        if not _THEME_ID_PATTERN.fullmatch(theme_id):
            raise ValueError(f"Invalid theme identifier: {self.theme_id!r}")

        display_name = str(self.display_name or "").strip()
        if not display_name:
            raise ValueError("A theme display name is required.")

        overrides: dict[str, str] = {}
        for logical_name, relative_path in dict(self.asset_overrides).items():
            logical = _safe_relative_path(logical_name, label="Asset name")
            override = _safe_relative_path(relative_path, label="Asset override")
            overrides[logical] = override

        object.__setattr__(self, "theme_id", theme_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(
            self,
            "asset_overrides",
            MappingProxyType(overrides),
        )


FUTURISTIC_THEME = ThemeDefinition(
    theme_id="futuristic",
    display_name="Futuristic",
    tokens=ThemeTokens(
        primary="#00b2b2",
        secondary="#00d0ff",
        accent="#00e5ff",
        background="#1e1e1e",
        panel_background="#101820",
        card_background="#111820",
        control_background="#14202c",
        border="#34485c",
        text="#f2fbff",
        muted_text="#91a7ba",
        highlight="#e0ffff",
        hover="#18323d",
        active="#17485a",
        success="#48b889",
        warning="#d7a845",
        error="#d85c6a",
    ),
)


SOFT_ELEGANT_THEME = ThemeDefinition(
    theme_id="soft_elegant",
    display_name="Soft Elegant",
    tokens=ThemeTokens(
        primary="#d8b57a",
        secondary="#a9859b",
        accent="#c9a76d",
        background="#241f26",
        panel_background="#302832",
        card_background="#382e39",
        control_background="#443848",
        border="#685668",
        text="#f7f0ea",
        muted_text="#bcaeb7",
        highlight="#ead4be",
        hover="#514254",
        active="#6b5366",
        success="#77a58a",
        warning="#d5a55f",
        error="#c86f78",
    ),
)


THEMES: Mapping[str, ThemeDefinition] = MappingProxyType(
    {
        FUTURISTIC_THEME.theme_id: FUTURISTIC_THEME,
        SOFT_ELEGANT_THEME.theme_id: SOFT_ELEGANT_THEME,
    }
)


def normalize_theme_id(
    value: object,
    themes: Mapping[str, ThemeDefinition] = THEMES,
) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in themes else DEFAULT_THEME_ID


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
        self._themes = self._validated_registry(themes or THEMES)
        self._bindings: WeakKeyDictionary[
            QtWidgets.QWidget,
            str,
        ] = WeakKeyDictionary()

        stored = self.settings.get(THEME_SETTINGS_KEY, DEFAULT_THEME_ID)
        self._theme_id = normalize_theme_id(stored, self._themes)
        if stored != self._theme_id:
            self.settings.update_fields(
                **{THEME_SETTINGS_KEY: self._theme_id}
            )

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

    def available_themes(self) -> tuple[ThemeDefinition, ...]:
        return tuple(self._themes.values())

    def set_theme(
        self,
        theme_id: object,
        *,
        persist: bool = True,
    ) -> ThemeDefinition:
        selected = normalize_theme_id(theme_id, self._themes)
        if persist:
            self.settings.update_fields(
                **{THEME_SETTINGS_KEY: selected}
            )
        if selected == self._theme_id:
            return self.current

        self._theme_id = selected
        self._refresh_bindings()
        definition = self.current
        self.theme_changed.emit(definition.theme_id, definition)
        return definition

    def save(self) -> dict[str, object]:
        """Persist and reapply the selected theme for an explicit Save action."""
        snapshot = self.settings.update_fields(
            **{THEME_SETTINGS_KEY: self._theme_id}
        )
        self._refresh_bindings()
        return snapshot

    def build_stylesheet(self) -> str:
        """Build role-based QSS from the current semantic token set."""
        c = self.tokens
        return f"""
QWidget[themeRole="background"], QMainWindow[themeRole="background"] {{
    background: {c.background};
    color: {c.text};
}}
QWidget[themeRole="panel"] {{
    background: {c.panel_background};
    border: 1px solid {c.border};
}}
QWidget[themeRole="card"] {{
    background: {c.card_background};
    border: 1px solid {c.border};
}}
QLabel[themeRole="text"] {{ color: {c.text}; }}
QLabel[themeRole="mutedText"] {{ color: {c.muted_text}; }}
QLabel[themeRole="highlightText"] {{ color: {c.highlight}; }}
QPushButton[themeRole="button"], QToolButton[themeRole="button"] {{
    background: {c.control_background};
    border: 1px solid {c.border};
    color: {c.text};
}}
QPushButton[themeRole="button"]:hover,
QToolButton[themeRole="button"]:hover {{
    background: {c.hover};
    border-color: {c.accent};
}}
QPushButton[themeRole="accentButton"],
QToolButton[themeRole="accentButton"] {{
    background: {c.control_background};
    border: 1px solid {c.accent};
    color: {c.highlight};
}}
QPushButton[themeRole="accentButton"]:hover,
QToolButton[themeRole="accentButton"]:hover {{ background: {c.active}; }}
QWidget[themeRole="success"] {{ color: {c.success}; }}
QWidget[themeRole="warning"] {{ color: {c.warning}; }}
QWidget[themeRole="error"] {{ color: {c.error}; }}
QLineEdit[themeRole="input"], QComboBox[themeRole="input"] {{
    background: {c.control_background};
    border: 1px solid {c.border};
    color: {c.text};
    selection-background-color: {c.active};
}}
""".strip()

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
            stylesheet = f"{stylesheet}\n{additional_qss.strip()}"
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

    def resolve_asset(
        self,
        logical_name: str | Path,
        *,
        fallback: str | Path | None = None,
    ) -> Path:
        """Return a themed asset when present, otherwise a safe legacy path."""
        logical = _safe_relative_path(logical_name, label="Asset name")
        relative_override = self.current.asset_overrides.get(logical, logical)
        relative_override = _safe_relative_path(
            relative_override,
            label="Asset override",
        )
        fallback_relative = (
            _safe_relative_path(fallback, label="Fallback asset")
            if fallback is not None
            else None
        )
        paths = application_paths(self.project_root)
        resource_root = paths.resource_root
        theme_root = (
            paths.app_resource_path("themes") / self._theme_id
        ).resolve()
        themed_path = (theme_root / relative_override).resolve()
        self._require_within(themed_path, theme_root, label="Themed asset")
        if themed_path.is_file():
            return themed_path

        if fallback is None:
            fallback_path = paths.app_resource_path(Path("icons") / logical)
        else:
            assert fallback_relative is not None
            fallback_path = (resource_root / fallback_relative).resolve()
            self._require_within(
                fallback_path,
                resource_root,
                label="Fallback asset",
            )
        return fallback_path

    @staticmethod
    def _require_within(path: Path, root: Path, *, label: str) -> None:
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"{label} escapes its allowed directory.") from error


__all__ = [
    "DEFAULT_THEME_ID",
    "FUTURISTIC_THEME",
    "SOFT_ELEGANT_THEME",
    "THEMES",
    "THEME_SETTINGS_KEY",
    "ThemeDefinition",
    "ThemeService",
    "ThemeTokens",
    "normalize_theme_id",
]
