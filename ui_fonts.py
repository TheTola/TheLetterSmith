from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from PySide6 import QtGui

from project_paths import application_paths


_LOGGER = logging.getLogger(__name__)


COMMAND_FONT_FAMILY = "Press Start 2P"
THEME_FONT_FAMILIES = (
    "Exo 2",
    "Aldrich",
    "Rajdhani",
    "Cinzel",
    "Montserrat",
    "Cormorant Garamond",
    "Manrope",
    "Lora",
    "Source Sans 3",
    "Source Serif 4",
    "IBM Plex Sans",
    "Spectral",
)
REQUIRED_APPLICATION_FONT_FAMILIES = (
    *THEME_FONT_FAMILIES,
    COMMAND_FONT_FAMILY,
)
APPLICATION_FONT_SUFFIXES = frozenset({".otf", ".ttc", ".ttf"})


@dataclass(frozen=True)
class ApplicationFontLoadReport:
    font_directory: Path
    loaded_files: tuple[Path, ...]
    failed_files: tuple[Path, ...]
    registered_families: tuple[str, ...]
    missing_families: tuple[str, ...]


_FONT_LOCK = threading.RLock()
_FONT_REPORTS: dict[Path, ApplicationFontLoadReport] = {}
_REGISTERED_FAMILIES: dict[str, str] = {}
_REPORTED_FALLBACKS: set[tuple[str, str]] = set()


def _family_key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _safe_system_family(preferred: str) -> str:
    candidate = " ".join(str(preferred or "").split())
    if candidate:
        try:
            if QtGui.QFontDatabase.hasFamily(candidate):
                return candidate
        except RuntimeError:
            pass

    try:
        system = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.GeneralFont
        ).family()
    except RuntimeError:
        system = ""
    return " ".join(str(system or "").split()) or candidate or "Sans Serif"


def _application_font_directory(project_root: str | Path) -> Path:
    return application_paths(project_root).app_resource_path("fonts").resolve()


def load_application_fonts(
    project_root: str | Path,
) -> ApplicationFontLoadReport:
    """Register bundled interface fonts once and report any invalid payload."""
    font_directory = _application_font_directory(project_root)
    with _FONT_LOCK:
        cached = _FONT_REPORTS.get(font_directory)
        if cached is not None:
            return cached

        font_files = (
            tuple(
                sorted(
                    (
                        path.resolve()
                        for path in font_directory.rglob("*")
                        if path.is_file()
                        and path.suffix.casefold() in APPLICATION_FONT_SUFFIXES
                    ),
                    key=lambda path: path.as_posix().casefold(),
                )
            )
            if font_directory.is_dir()
            else ()
        )
        loaded_files: list[Path] = []
        failed_files: list[Path] = []
        directory_families: dict[str, str] = {}

        for font_path in font_files:
            try:
                font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
            except RuntimeError:
                font_id = -1
            if font_id < 0:
                failed_files.append(font_path)
                continue

            try:
                returned = QtGui.QFontDatabase.applicationFontFamilies(font_id)
            except RuntimeError:
                returned = ()
            families = tuple(
                family
                for family in (
                    " ".join(str(value or "").split()) for value in returned
                )
                if family
            )
            if not families:
                failed_files.append(font_path)
                continue

            loaded_files.append(font_path)
            for family in families:
                key = _family_key(family)
                directory_families.setdefault(key, family)
                _REGISTERED_FAMILIES.setdefault(key, family)

        missing_families = tuple(
            family
            for family in REQUIRED_APPLICATION_FONT_FAMILIES
            if _family_key(family) not in directory_families
        )
        report = ApplicationFontLoadReport(
            font_directory=font_directory,
            loaded_files=tuple(loaded_files),
            failed_files=tuple(failed_files),
            registered_families=tuple(directory_families.values()),
            missing_families=missing_families,
        )
        _FONT_REPORTS[font_directory] = report

    if not font_directory.is_dir():
        _LOGGER.warning(
            "Application font directory is missing; using a system fallback: %s",
            font_directory,
        )
    elif failed_files:
        _LOGGER.warning(
            "Application font files could not be registered: %s",
            ", ".join(path.name for path in failed_files),
        )
    if missing_families:
        _LOGGER.warning(
            "Bundled application font families are unavailable; affected families "
            "will use a system fallback: %s",
            ", ".join(missing_families),
        )
    return report


def resolve_registered_family(
    preferred: object,
    fallback: str = "Segoe UI",
) -> str:
    """Return a registered family or a safe installed system fallback."""
    preferred_name = " ".join(str(preferred or "").split())
    fallback_name = " ".join(str(fallback or "").split()) or "Segoe UI"
    with _FONT_LOCK:
        registered = _REGISTERED_FAMILIES.get(_family_key(preferred_name))
        if registered:
            return registered

    try:
        if preferred_name and QtGui.QFontDatabase.hasFamily(preferred_name):
            return preferred_name
    except RuntimeError:
        pass

    with _FONT_LOCK:
        registered_fallback = _REGISTERED_FAMILIES.get(
            _family_key(fallback_name)
        )
    resolved = registered_fallback or _safe_system_family(fallback_name)
    warning_key = (_family_key(preferred_name), _family_key(resolved))
    with _FONT_LOCK:
        should_report = bool(preferred_name) and warning_key not in _REPORTED_FALLBACKS
        if should_report:
            _REPORTED_FALLBACKS.add(warning_key)
    if should_report:
        _LOGGER.warning(
            "Application font family %r is unavailable; using %r.",
            preferred_name,
            resolved,
        )
    return resolved


__all__ = [
    "APPLICATION_FONT_SUFFIXES",
    "ApplicationFontLoadReport",
    "COMMAND_FONT_FAMILY",
    "REQUIRED_APPLICATION_FONT_FAMILIES",
    "THEME_FONT_FAMILIES",
    "load_application_fonts",
    "resolve_registered_family",
]
