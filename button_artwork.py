from __future__ import annotations

"""Strict discovery helpers retained for release-resource compatibility."""

from pathlib import Path
import re
from typing import Iterable


_BUTTON_ARTWORK_PATTERN = re.compile(
    r"^[A-Za-z]{1,3}Button\.(?:bmp|gif|jpe?g|png|tiff?|webp)$",
    re.IGNORECASE,
)


def discover_button_artwork_files(directory: str | Path) -> tuple[Path, ...]:
    """Return safely named button images from one artwork directory."""
    root = Path(directory).resolve()
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            (
                path.resolve()
                for path in root.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and _BUTTON_ARTWORK_PATTERN.fullmatch(path.name)
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
    )


def application_resource_names(
    app_root: str | Path,
    relative_directory: str | Path,
    manifested_names: Iterable[str],
) -> tuple[str, ...]:
    """Add strictly named button art to the release resource allowlist."""
    relative = Path(relative_directory)
    names = {str(name) for name in manifested_names}
    if relative.name.casefold() == "buttons":
        names.update(
            path.name
            for path in discover_button_artwork_files(Path(app_root) / relative)
        )
    return tuple(sorted(names, key=lambda name: (name.casefold(), name)))


def session_button_artwork_path(
    directory: str | Path,
    control_id: object,
    fallback_filename: str,
) -> Path:
    """Return the explicitly requested legacy path without random assignment."""
    root = Path(directory).resolve()
    key = str(control_id or "").strip().casefold()
    if not key:
        raise ValueError("A stable cloud-button control identifier is required.")
    return root / Path(fallback_filename).name


__all__ = [
    "application_resource_names",
    "discover_button_artwork_files",
    "session_button_artwork_path",
]
