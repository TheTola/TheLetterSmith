from __future__ import annotations

"""Normalize and repair Letter Smith theme-button asset folders."""

import hashlib
import html
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PySide6 import QtCore, QtGui, QtWidgets


PROJECT_ROOT = Path(__file__).resolve().parent
THEMES_ROOT = PROJECT_ROOT / "resources" / "app" / "themes"
THEME_DISPLAY_NAMES = {
    "cyber_forge": "Cyber Forge",
    "obsidian_forge": "Obsidian Forge",
    "velvet_rose": "Velvet Rose",
    "celestial_rose": "Celestial Rose",
}
THEME_ORDER = tuple(THEME_DISPLAY_NAMES)
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".webp", ".jpg", ".jpeg", ".gif"})
EXPECTED_BUTTON_EXTENSION = ".png"
PROTECTED_SPECIAL_STEMS = frozenset({"connectbutton"})
REGULAR_BUTTON_PATTERN = re.compile(r"^[A-Z]Button$", re.IGNORECASE)
LONG_BUTTON_PATTERN = re.compile(r"^[A-Z]LButton$", re.IGNORECASE)


def expected_regular_buttons() -> tuple[str, ...]:
    return tuple(f"{letter}Button" for letter in "ABCDEFGH")


def expected_broken_buttons() -> tuple[str, ...]:
    return tuple(f"{letter}Button" for letter in "ABC")


def expected_long_buttons() -> tuple[str, ...]:
    return tuple(f"{letter}LButton" for letter in "ABC")


KNOWN_LONG_BUTTON_STEMS = tuple(f"{letter}LButton" for letter in "ABCDEFGHIJ")
ALL_EXPECTED_STEMS = frozenset(
    stem.casefold()
    for stem in (*expected_regular_buttons(), *KNOWN_LONG_BUTTON_STEMS)
)


@dataclass(frozen=True)
class ButtonSet:
    theme_id: str
    theme_name: str
    label: str
    directory: Path | None
    expected_stems: tuple[str, ...]
    sync_from_a: bool = False


@dataclass
class FolderInventory:
    recognized: dict[str, Path] = field(default_factory=dict)
    duplicates: list[Path] = field(default_factory=list)
    candidates: list[Path] = field(default_factory=list)
    incompatible_candidates: list[Path] = field(default_factory=list)
    protected: list[Path] = field(default_factory=list)
    out_of_range_buttons: list[Path] = field(default_factory=list)


@dataclass
class ButtonSetResult:
    button_set: ButtonSet
    found_before: int = 0
    missing_before: list[str] = field(default_factory=list)
    candidates_before: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    normalized: list[tuple[str, str]] = field(default_factory=list)
    synced: list[str] = field(default_factory=list)
    already_synced: list[str] = field(default_factory=list)
    missing_after: list[str] = field(default_factory=list)
    unused_candidates: list[str] = field(default_factory=list)
    incompatible_candidates: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    out_of_range_before: list[str] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    unquarantined_out_of_range: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    directory_missing: bool = False


@dataclass
class SpecialAssetResult:
    theme_id: str
    theme_name: str
    normalized: list[tuple[str, str]] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    missing_after: list[str] = field(default_factory=list)
    unused_candidates: list[str] = field(default_factory=list)
    incompatible_candidates: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    titlebar_images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _sort_paths(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=lambda path: (path.name.casefold(), path.name))


def _find_child_directory(parent: Path, name: str) -> Path | None:
    if not parent.is_dir():
        return None
    matches = _sort_paths(
        child
        for child in parent.iterdir()
        if child.is_dir() and child.name.casefold() == name.casefold()
    )
    return matches[0] if matches else None


def find_theme_directories(themes_root: Path = THEMES_ROOT) -> dict[str, Path]:
    """Return image-button theme folders using their real on-disk casing."""
    if not themes_root.is_dir():
        return {}
    available = {
        child.name.casefold(): child
        for child in themes_root.iterdir()
        if child.is_dir()
    }
    discovered: dict[str, Path] = {}
    for theme_id in THEME_ORDER:
        theme = available.get(theme_id)
        if theme is not None:
            discovered[theme_id] = theme
    for theme_id, theme in sorted(available.items()):
        if theme_id in discovered:
            continue
        if _find_child_directory(theme, "buttons") is not None:
            discovered[theme_id] = theme
    return discovered


def find_button_sets(theme_id: str, theme_directory: Path) -> tuple[ButtonSet, ...]:
    """Discover regular, broken, long, and broken-long folders."""
    theme_name = THEME_DISPLAY_NAMES.get(
        theme_id,
        theme_directory.name.replace("_", " ").title(),
    )
    buttons = _find_child_directory(theme_directory, "buttons")
    broken = _find_child_directory(buttons, "broken") if buttons else None
    long_buttons = _find_child_directory(buttons, "long") if buttons else None
    broken_long = (
        _find_child_directory(long_buttons, "broken") if long_buttons else None
    )
    return (
        ButtonSet(
            theme_id,
            theme_name,
            "Regular",
            buttons,
            expected_regular_buttons(),
            sync_from_a=theme_id == "obsidian_forge",
        ),
        ButtonSet(
            theme_id,
            theme_name,
            "Broken",
            broken,
            expected_broken_buttons(),
        ),
        ButtonSet(
            theme_id,
            theme_name,
            "Long",
            long_buttons,
            expected_long_buttons(),
        ),
        ButtonSet(
            theme_id,
            theme_name,
            "Broken Long",
            broken_long,
            expected_long_buttons(),
        ),
    )


def _is_hidden_or_system(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        attributes = int(getattr(path.stat(), "st_file_attributes", 0))
    except OSError:
        return True
    hidden = int(getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))
    system = int(getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0))
    return bool(attributes & (hidden | system))


def find_other_theme_images(theme_directory: Path) -> tuple[Path, ...]:
    """Return every visible theme image outside the managed button folders."""
    images: list[Path] = []
    for path in theme_directory.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(theme_directory)
        if relative.parts and relative.parts[0].casefold() == "buttons":
            continue
        if path.suffix.casefold() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        if _is_hidden_or_system(path):
            continue
        images.append(path)
    return tuple(
        sorted(
            images,
            key=lambda path: (
                path.relative_to(theme_directory).as_posix().casefold(),
                path.relative_to(theme_directory).as_posix(),
            ),
        )
    )


def _has_valid_image_signature(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError:
        return False
    suffix = path.suffix.casefold()
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def _direct_theme_images(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return _sort_paths(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS
        and not _is_hidden_or_system(path)
    )


def _replacement_quarantine_path(source: Path, project_root: Path) -> Path:
    root = project_root.resolve()
    quarantine_root = (root / "Quarantine").resolve()
    relative = source.resolve(strict=True).relative_to(root)
    destination = (quarantine_root / relative).resolve()
    destination.relative_to(quarantine_root)
    if not destination.exists():
        return destination
    counter = 2
    while True:
        candidate = destination.with_name(
            f"{destination.stem}.buttoner-{counter}{destination.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def _replace_special_asset(
    candidate: Path,
    existing: Iterable[Path],
    target: Path,
    result: SpecialAssetResult,
    project_root: Path,
) -> None:
    moved: list[tuple[Path, Path]] = []
    try:
        for source in _sort_paths(existing):
            destination = _replacement_quarantine_path(source, project_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append((source, destination))
        candidate.rename(target)
    except (OSError, RuntimeError, ValueError) as error:
        result.errors.append(f"Could not rename {candidate.name} to {target.name}: {error}")
        for source, destination in reversed(moved):
            try:
                if not source.exists() and destination.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    destination.rename(source)
            except OSError as restore_error:
                result.errors.append(
                    f"Could not restore {source.name} after rename failure: {restore_error}"
                )
        return
    result.renamed.append((candidate.name, target.name))
    result.quarantined.extend(
        (source.name, destination.relative_to(project_root.resolve()).as_posix())
        for source, destination in moved
    )


def _normalize_special_asset(
    source: Path,
    target: Path,
    result: SpecialAssetResult,
) -> None:
    if source.name == target.name:
        return
    try:
        _case_normalize_path(source, target)
    except OSError as error:
        result.errors.append(f"Could not normalize {source.name}: {error}")
    else:
        result.normalized.append((source.name, target.name))


def _valid_special_candidates(
    paths: Iterable[Path],
    allowed_extensions: frozenset[str],
    result: SpecialAssetResult,
) -> list[Path]:
    valid: list[Path] = []
    for path in paths:
        if path.suffix.casefold() not in allowed_extensions or not _has_valid_image_signature(path):
            result.incompatible_candidates.append(path.name)
        else:
            valid.append(path)
    return _sort_paths(valid)


def _process_single_png_asset(
    directory: Path | None,
    label: str,
    canonical_name: str,
    result: SpecialAssetResult,
    project_root: Path,
) -> None:
    if directory is None or not directory.is_dir():
        result.missing_after.append(f"{label}: {canonical_name}")
        result.errors.append(f"{label} directory is missing.")
        return
    files = _direct_theme_images(directory)
    recognized = [path for path in files if path.name.casefold() == canonical_name.casefold()]
    candidate_pool = [path for path in files if path not in recognized]
    candidates = _valid_special_candidates(
        candidate_pool,
        frozenset({".png"}),
        result,
    )
    target = directory / canonical_name
    if len(candidates) == 1:
        _replace_special_asset(candidates[0], recognized, target, result, project_root)
    elif len(candidates) > 1:
        result.unused_candidates.extend(path.name for path in candidates)
        result.warnings.append(
            f"{label}: multiple PNG candidates found; no file was renamed."
        )
    elif recognized:
        _normalize_special_asset(recognized[0], target, result)
    if not target.is_file():
        result.missing_after.append(f"{label}: {canonical_name}")


def _process_help_assets(
    directory: Path | None,
    result: SpecialAssetResult,
    project_root: Path,
) -> None:
    if directory is None or not directory.is_dir():
        result.missing_after.extend(("Help: Help.png or Help.gif", "Help: HHelp.png or HHelp.gif"))
        result.errors.append("Help directory is missing.")
        return
    files = _direct_theme_images(directory)
    recognized = {
        stem: [
            path
            for path in files
            if path.stem.casefold() == stem.casefold()
            and path.suffix.casefold() in {".png", ".gif"}
        ]
        for stem in ("Help", "HHelp")
    }
    recognized_paths = {path for paths in recognized.values() for path in paths}
    candidates = _valid_special_candidates(
        (path for path in files if path not in recognized_paths),
        frozenset({".png", ".gif"}),
        result,
    )
    if len(candidates) > 2:
        result.unused_candidates.extend(path.name for path in candidates)
        result.warnings.append("Help: more than two candidates found; no files were renamed.")
        candidates = []

    assigned_stems: set[str] = set()
    for stem, candidate in zip(("Help", "HHelp"), candidates):
        target = directory / f"{stem}{candidate.suffix.casefold()}"
        _replace_special_asset(candidate, recognized[stem], target, result, project_root)
        assigned_stems.add(stem)

    for stem in ("Help", "HHelp"):
        if stem in assigned_stems:
            continue
        paths = recognized[stem]
        if paths:
            source = paths[0]
            target = directory / f"{stem}{source.suffix.casefold()}"
            _normalize_special_asset(source, target, result)

    final_files = _direct_theme_images(directory)
    final_stems = {path.stem.casefold() for path in final_files}
    if "help" not in final_stems:
        result.missing_after.append("Help: Help.png or Help.gif")
    if "hhelp" not in final_stems:
        result.missing_after.append("Help: HHelp.png or HHelp.gif")


def _process_settings_assets(
    directory: Path | None,
    result: SpecialAssetResult,
    project_root: Path,
) -> None:
    if directory is None or not directory.is_dir():
        result.missing_after.extend(("Settings: settings.png", "Settings: Settings.gif"))
        result.errors.append("Settings directory is missing.")
        return
    files = _direct_theme_images(directory)
    protected = [path for path in files if path.name.casefold() == "settingz.gif"]
    result.protected.extend(path.name for path in protected)

    for extension, canonical_name in ((".png", "settings.png"), (".gif", "Settings.gif")):
        recognized = [
            path
            for path in files
            if path.name.casefold() == canonical_name.casefold()
            and path.suffix.casefold() == extension
        ]
        candidate_pool = [
            path
            for path in files
            if path.suffix.casefold() == extension
            and path not in recognized
            and path not in protected
        ]
        candidates = _valid_special_candidates(
            candidate_pool,
            frozenset({extension}),
            result,
        )
        target = directory / canonical_name
        if len(candidates) == 1:
            _replace_special_asset(candidates[0], recognized, target, result, project_root)
        elif len(candidates) > 1:
            result.unused_candidates.extend(path.name for path in candidates)
            result.warnings.append(
                f"Settings: multiple {extension.upper()} candidates found; no file was renamed."
            )
        elif recognized:
            _normalize_special_asset(recognized[0], target, result)
        if not target.is_file():
            result.missing_after.append(f"Settings: {canonical_name}")


def process_special_theme_images(
    theme_id: str,
    theme_directory: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> SpecialAssetResult:
    """Normalize supported non-button assets and inventory title-bar artwork."""
    result = SpecialAssetResult(
        theme_id,
        THEME_DISPLAY_NAMES.get(
            theme_id,
            theme_directory.name.replace("_", " ").title(),
        ),
    )
    new_directory = _find_child_directory(theme_directory, "New")
    prompt_directory = _find_child_directory(theme_directory, "prompt_writer")
    help_directory = _find_child_directory(theme_directory, "help")
    settings_directory = _find_child_directory(theme_directory, "settings")
    titlebar_directory = _find_child_directory(theme_directory, "titlebar")

    _process_single_png_asset(
        new_directory,
        "New Project",
        "New.png",
        result,
        project_root,
    )
    _process_single_png_asset(
        prompt_directory,
        "Prompt Writer",
        "Pwrite.png",
        result,
        project_root,
    )
    _process_help_assets(help_directory, result, project_root)
    _process_settings_assets(settings_directory, result, project_root)
    result.titlebar_images = [
        path.name for path in _direct_theme_images(titlebar_directory)
    ]
    if titlebar_directory is None:
        result.warnings.append("Title Bar directory is missing; no renaming was attempted.")
    return result


def inventory_button_folder(
    directory: Path,
    expected_stems: tuple[str, ...],
) -> FolderInventory:
    """Classify only direct child files of one authoritative button folder."""
    inventory = FolderInventory()
    expected = {stem.casefold(): stem for stem in expected_stems}
    expected_are_long = bool(expected_stems) and all(
        stem[1:].casefold() == "lbutton" for stem in expected_stems
    )
    expected_pattern = (
        LONG_BUTTON_PATTERN if expected_are_long else REGULAR_BUTTON_PATTERN
    )
    files = _sort_paths(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    )
    for path in files:
        stem_key = path.stem.casefold()
        suffix = path.suffix.casefold()
        if _is_hidden_or_system(path):
            inventory.protected.append(path)
            continue
        if stem_key in PROTECTED_SPECIAL_STEMS:
            inventory.protected.append(path)
            continue
        if (
            expected_pattern.fullmatch(path.stem)
            and stem_key not in expected
            and suffix in SUPPORTED_IMAGE_EXTENSIONS
        ):
            inventory.out_of_range_buttons.append(path)
            continue
        if stem_key in expected and suffix == EXPECTED_BUTTON_EXTENSION:
            if stem_key in inventory.recognized:
                inventory.duplicates.append(path)
            else:
                inventory.recognized[stem_key] = path
            continue
        if stem_key in ALL_EXPECTED_STEMS:
            inventory.protected.append(path)
            continue
        if REGULAR_BUTTON_PATTERN.fullmatch(path.stem) or LONG_BUTTON_PATTERN.fullmatch(
            path.stem
        ):
            inventory.protected.append(path)
            continue
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        if not _has_valid_image_signature(path):
            inventory.incompatible_candidates.append(path)
            continue
        if suffix != EXPECTED_BUTTON_EXTENSION:
            inventory.incompatible_candidates.append(path)
            continue
        inventory.candidates.append(path)
    return inventory


def find_missing_buttons(
    expected_stems: tuple[str, ...],
    inventory: FolderInventory,
) -> list[str]:
    return [
        stem
        for stem in expected_stems
        if stem.casefold() not in inventory.recognized
    ]


def quarantine_out_of_range_buttons(
    paths: Iterable[Path],
    project_root: Path,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Move canonical assets beyond a set's supported range to Quarantine."""
    root = project_root.resolve()
    quarantine_root = (root / "Quarantine").resolve()
    quarantined: list[tuple[str, str]] = []
    errors: list[str] = []
    for source in _sort_paths(paths):
        try:
            resolved_source = source.resolve(strict=True)
            relative = resolved_source.relative_to(root)
            destination = (quarantine_root / relative).resolve()
            destination.relative_to(quarantine_root)
            if destination.exists():
                raise FileExistsError(
                    f"quarantine destination already exists: {destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(resolved_source), str(destination))
        except (OSError, RuntimeError, ValueError) as error:
            errors.append(f"Could not quarantine {source.name}: {error}")
        else:
            quarantined.append(
                (source.name, destination.relative_to(root).as_posix())
            )
    return quarantined, errors


def _case_normalize_path(source: Path, target: Path) -> None:
    if source.name == target.name:
        return
    if target.exists():
        try:
            same_file = source.samefile(target)
        except OSError:
            same_file = False
        if not same_file:
            raise FileExistsError(f"canonical target already exists: {target.name}")
        temporary = source.with_name(f".Buttoner-{source.name}.tmp")
        counter = 1
        while temporary.exists():
            temporary = source.with_name(f".Buttoner-{counter}-{source.name}.tmp")
            counter += 1
        source.rename(temporary)
        try:
            temporary.rename(target)
        except OSError:
            temporary.rename(source)
            raise
        return
    source.rename(target)


def normalize_recognized_names(
    directory: Path,
    expected_stems: tuple[str, ...],
    inventory: FolderInventory,
) -> tuple[list[tuple[str, str]], list[str]]:
    normalized: list[tuple[str, str]] = []
    errors: list[str] = []
    for stem in expected_stems:
        source = inventory.recognized.get(stem.casefold())
        if source is None:
            continue
        target = directory / f"{stem}{EXPECTED_BUTTON_EXTENSION}"
        if source.name == target.name:
            continue
        try:
            _case_normalize_path(source, target)
        except OSError as error:
            errors.append(f"Could not normalize {source.name}: {error}")
        else:
            normalized.append((source.name, target.name))
    return normalized, errors


def fill_missing_buttons(
    directory: Path,
    missing_stems: list[str],
    candidates: list[Path],
) -> tuple[list[tuple[str, str]], list[str]]:
    renamed: list[tuple[str, str]] = []
    errors: list[str] = []
    for stem, candidate in zip(missing_stems, candidates):
        target = directory / f"{stem}{EXPECTED_BUTTON_EXTENSION}"
        if target.exists():
            errors.append(f"Skipped {candidate.name}: {target.name} already exists")
            continue
        try:
            candidate.rename(target)
        except OSError as error:
            errors.append(f"Could not rename {candidate.name}: {error}")
        else:
            renamed.append((candidate.name, target.name))
    return renamed, errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_exact_if_changed(master: Path, target: Path) -> bool:
    if target.is_file():
        try:
            if master.stat().st_size == target.stat().st_size and _sha256(master) == _sha256(target):
                return False
        except OSError:
            pass
    temporary = target.with_name(f".{target.name}.buttoner.tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary file already exists: {temporary.name}")
    try:
        shutil.copy2(master, temporary)
        os.replace(temporary, target)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise
    return True


def sync_obsidian_regular_buttons(
    button_set: ButtonSet,
    result: ButtonSetResult,
) -> None:
    """Synchronize only Obsidian Forge's main regular B-H files from A."""
    directory = button_set.directory
    if directory is None:
        return
    inventory = inventory_button_folder(directory, button_set.expected_stems)
    master = inventory.recognized.get("abutton")
    if master is None:
        result.errors.append("Obsidian Forge master AButton is missing. Synchronization skipped.")
        result.missing_after = find_missing_buttons(button_set.expected_stems, inventory)
        return
    for stem in button_set.expected_stems[1:]:
        target = directory / f"{stem}{EXPECTED_BUTTON_EXTENSION}"
        try:
            changed = _copy_exact_if_changed(master, target)
        except OSError as error:
            result.errors.append(f"Could not synchronize {target.name}: {error}")
        else:
            (result.synced if changed else result.already_synced).append(stem)


def process_button_set(
    button_set: ButtonSet,
    *,
    project_root: Path = PROJECT_ROOT,
) -> ButtonSetResult:
    result = ButtonSetResult(button_set)
    directory = button_set.directory
    if directory is None or not directory.is_dir():
        result.directory_missing = True
        result.missing_after = list(button_set.expected_stems)
        return result

    try:
        initial = inventory_button_folder(directory, button_set.expected_stems)
    except OSError as error:
        result.errors.append(f"Could not inventory directory: {error}")
        return result
    result.found_before = len(initial.recognized)
    result.missing_before = find_missing_buttons(button_set.expected_stems, initial)
    result.candidates_before = [path.name for path in initial.candidates]
    result.incompatible_candidates = [
        path.name for path in initial.incompatible_candidates
    ]
    result.protected = [path.name for path in initial.protected]
    result.out_of_range_before = [
        path.name for path in initial.out_of_range_buttons
    ]
    result.duplicates = [path.name for path in initial.duplicates]

    result.quarantined, quarantine_errors = quarantine_out_of_range_buttons(
        initial.out_of_range_buttons,
        project_root,
    )
    result.errors.extend(quarantine_errors)

    result.normalized, normalization_errors = normalize_recognized_names(
        directory,
        button_set.expected_stems,
        initial,
    )
    result.errors.extend(normalization_errors)

    try:
        inventory = inventory_button_folder(directory, button_set.expected_stems)
    except OSError as error:
        result.errors.append(f"Could not refresh inventory: {error}")
        return result

    if button_set.sync_from_a:
        sync_obsidian_regular_buttons(button_set, result)
    else:
        missing = find_missing_buttons(button_set.expected_stems, inventory)
        result.renamed, rename_errors = fill_missing_buttons(
            directory,
            missing,
            inventory.candidates,
        )
        result.errors.extend(rename_errors)

    try:
        final = inventory_button_folder(directory, button_set.expected_stems)
    except OSError as error:
        result.errors.append(f"Could not verify result: {error}")
        return result
    result.missing_after = find_missing_buttons(button_set.expected_stems, final)
    result.unused_candidates = [path.name for path in final.candidates]
    result.unquarantined_out_of_range = [
        path.name for path in final.out_of_range_buttons
    ]
    return result


def _joined(values: Iterable[str]) -> str:
    return ", ".join(values)


def button_set_report_lines(result: ButtonSetResult) -> list[str]:
    """Return the complete human-readable report for one button set."""
    lines: list[str] = []
    button_set = result.button_set
    if result.directory_missing:
        lines.append("Missing directory.")
        lines.append(f"Missing: {_joined(result.missing_after)}")
        return lines
    lines.append(f"Found: {result.found_before}/{len(button_set.expected_stems)}")
    if result.missing_before:
        lines.append(f"Missing: {_joined(result.missing_before)}")
    for candidate in result.candidates_before:
        lines.append(f"Candidate: {candidate}")
    for source, target in result.normalized:
        lines.append(f"Normalized: {source} -> {target}")
    for source, target in result.quarantined:
        lines.append(f"Quarantined: {source} -> {target}")
    if button_set.sync_from_a:
        if not any("master AButton" in error for error in result.errors):
            lines.append(f"Master: AButton{EXPECTED_BUTTON_EXTENSION}")
        if result.synced:
            lines.append(f"Synced: {_joined(result.synced)}")
        elif result.already_synced:
            lines.append("Already synchronized.")
    for source, target in result.renamed:
        lines.append(f"Renamed: {source} -> {target}")
    if result.missing_after:
        lines.append(f"Unresolved missing: {_joined(result.missing_after)}")
    if result.unused_candidates:
        lines.append(f"Unused candidates: {_joined(result.unused_candidates)}")
    if result.unquarantined_out_of_range:
        lines.append(
            "Unquarantined out-of-range buttons: "
            f"{_joined(result.unquarantined_out_of_range)}"
        )
    if result.incompatible_candidates:
        lines.append(
            "Skipped incompatible images: "
            f"{_joined(result.incompatible_candidates)}"
        )
    if result.protected:
        lines.append(f"Skipped protected files: {_joined(result.protected)}")
    if result.duplicates:
        lines.append(f"Duplicate recognized files: {_joined(result.duplicates)}")
    for error in result.errors:
        lines.append(f"ERROR: {error}")
    found_after = len(button_set.expected_stems) - len(result.missing_after)
    lines.append(f"Result: {found_after}/{len(button_set.expected_stems)}")
    if (
        not result.normalized
        and not result.quarantined
        and not result.synced
        and not result.renamed
        and not result.errors
        and not result.missing_after
    ):
        lines.append("No changes required.")
    return lines


def print_button_set_report(result: ButtonSetResult) -> None:
    button_set = result.button_set
    print(f"[Buttoner] {button_set.theme_name} / {button_set.label}")
    for line in button_set_report_lines(result):
        print(f"  {line}")


def special_asset_report_lines(result: SpecialAssetResult) -> list[str]:
    lines: list[str] = []
    for source, target in result.normalized:
        lines.append(f"Normalized: {source} -> {target}")
    for source, target in result.renamed:
        lines.append(f"Renamed: {source} -> {target}")
    for source, target in result.quarantined:
        lines.append(f"Backed up: {source} -> {target}")
    if result.missing_after:
        lines.append(f"Missing: {_joined(result.missing_after)}")
    if result.unused_candidates:
        lines.append(f"Unused candidates: {_joined(result.unused_candidates)}")
    if result.incompatible_candidates:
        lines.append(
            f"Skipped incompatible images: {_joined(result.incompatible_candidates)}"
        )
    if result.protected:
        lines.append(f"Protected legacy images: {_joined(result.protected)}")
    if result.titlebar_images:
        lines.append(
            "Title Bar (read-only): "
            f"{_joined(result.titlebar_images)}"
        )
    else:
        lines.append("Title Bar (read-only): no images found")
    for warning in result.warnings:
        lines.append(f"WARNING: {warning}")
    for error in result.errors:
        lines.append(f"ERROR: {error}")
    if not (
        result.normalized
        or result.renamed
        or result.quarantined
        or result.missing_after
        or result.unused_candidates
        or result.incompatible_candidates
        or result.warnings
        or result.errors
    ):
        lines.append("No special-image changes required.")
    return lines


def print_special_asset_report(result: SpecialAssetResult) -> None:
    print(f"[Buttoner] {result.theme_name} / Other Images")
    for line in special_asset_report_lines(result):
        print(f"  {line}")


def process_theme_button_sets(
    theme_id: str,
    theme_directory: Path,
    *,
    emit_report: bool = True,
    project_root: Path = PROJECT_ROOT,
) -> list[ButtonSetResult]:
    try:
        button_sets = find_button_sets(theme_id, theme_directory)
    except OSError as error:
        theme_name = THEME_DISPLAY_NAMES.get(
            theme_id,
            theme_directory.name.replace("_", " ").title(),
        )
        if emit_report:
            print(f"[Buttoner] {theme_name}")
            print(f"  ERROR: Could not inspect theme directories: {error}")
            print()
        return []

    results: list[ButtonSetResult] = []
    for button_set in button_sets:
        try:
            result = process_button_set(button_set, project_root=project_root)
        except Exception as error:
            result = ButtonSetResult(button_set)
            result.errors.append(f"Unexpected filesystem error: {error}")
        results.append(result)
        if emit_report:
            print_button_set_report(result)
            print()
    return results


def console_main() -> int:
    discovered = find_theme_directories()
    if not THEMES_ROOT.is_dir():
        print(f"[Buttoner] ERROR: Theme root is missing: {THEMES_ROOT}")
        return 1

    results: list[ButtonSetResult] = []
    special_results: list[SpecialAssetResult] = []
    for theme_id in THEME_ORDER:
        theme_directory = discovered.get(theme_id)
        if theme_directory is None:
            print(f"[Buttoner] {THEME_DISPLAY_NAMES[theme_id]}")
            print("  ERROR: Theme directory is missing.")
            continue
        results.extend(process_theme_button_sets(theme_id, theme_directory))
        special_result = process_special_theme_images(theme_id, theme_directory)
        special_results.append(special_result)
        print_special_asset_report(special_result)
        print()

    for theme_id, theme_directory in discovered.items():
        if theme_id in THEME_ORDER:
            continue
        results.extend(process_theme_button_sets(theme_id, theme_directory))

    return 1 if (
        any(result.errors for result in results)
        or any(result.errors for result in special_results)
    ) else 0


THEME_ACCENTS = {
    "cyber_forge": "#39d5ff",
    "obsidian_forge": "#d8b15a",
    "velvet_rose": "#ef78aa",
    "celestial_rose": "#a999ff",
}


class ThemePanel(QtWidgets.QFrame):
    def __init__(self, theme_id: str, theme_name: str) -> None:
        super().__init__()
        self.theme_id = theme_id
        self.accent = THEME_ACCENTS[theme_id]
        self.setObjectName("themePanel")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(theme_name)
        title.setObjectName("themeTitle")
        title.setStyleSheet(f"color: {self.accent};")
        self.status = QtWidgets.QLabel("Waiting")
        self.status.setObjectName("themeStatus")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        layout.addLayout(header)

        self.path_label = QtWidgets.QLabel()
        self.path_label.setObjectName("themePath")
        self.path_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_label.setWordWrap(True)
        self.path_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.path_label)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("themeTabs")

        self.report = QtWidgets.QTextBrowser()
        self.report.setObjectName("themeReport")
        self.report.setOpenExternalLinks(False)
        self.tabs.addTab(self.report, "Button Sets")

        images_page = QtWidgets.QWidget()
        images_layout = QtWidgets.QVBoxLayout(images_page)
        images_layout.setContentsMargins(0, 0, 0, 0)
        images_layout.setSpacing(6)

        self.image_activity = QtWidgets.QTextBrowser()
        self.image_activity.setObjectName("imageActivity")
        self.image_activity.setMaximumHeight(108)
        self.image_activity.setOpenExternalLinks(False)
        images_layout.addWidget(self.image_activity)

        self.images = QtWidgets.QTreeWidget()
        self.images.setObjectName("themeImages")
        self.images.setColumnCount(3)
        self.images.setHeaderLabels(("Image", "Dimensions", "Format"))
        self.images.setIconSize(QtCore.QSize(42, 42))
        self.images.setRootIsDecorated(True)
        self.images.setAlternatingRowColors(True)
        self.images.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.images.header().setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        self.images.header().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        self.images.header().setSectionResizeMode(
            2,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        images_layout.addWidget(self.images, 1)
        self.tabs.addTab(images_page, "Other Images")
        layout.addWidget(self.tabs, 1)

    def show_missing_theme(self) -> None:
        self.status.setText("Missing")
        self.status.setStyleSheet("color: #ff8080;")
        self.path_label.setText("Theme directory not found")
        self.report.setHtml(
            '<p style="color:#ff9b9b;">ERROR: Theme directory is missing.</p>'
        )
        self.images.clear()
        self.image_activity.clear()
        self.tabs.setTabText(1, "Other Images (0)")

    def show_results(
        self,
        directory: Path,
        results: list[ButtonSetResult],
        special_result: SpecialAssetResult,
    ) -> None:
        self.path_label.setText(str(directory))
        self.show_other_images(directory, special_result)
        errors = sum(len(result.errors) for result in results) + len(special_result.errors)
        unresolved = (
            sum(len(result.missing_after) for result in results)
            + len(special_result.missing_after)
        )
        complete = sum(not result.missing_after for result in results)
        if errors:
            self.status.setText(f"{errors} error{'s' if errors != 1 else ''}")
            self.status.setStyleSheet("color: #ff8080;")
        elif unresolved:
            self.status.setText(f"{unresolved} unresolved")
            self.status.setStyleSheet("color: #ffd27d;")
        else:
            self.status.setText(f"{complete}/{len(results)} complete")
            self.status.setStyleSheet("color: #83e6ae;")

        sections: list[str] = []
        for result in results:
            lines = html.escape("\n".join(button_set_report_lines(result)))
            if result.errors:
                state_color = "#ff8080"
                state = "ERROR"
            elif result.missing_after:
                state_color = "#ffd27d"
                state = "INCOMPLETE"
            else:
                state_color = "#83e6ae"
                state = "COMPLETE"
            sections.append(
                '<div style="margin-bottom:12px;">'
                f'<h3 style="color:{self.accent}; margin:0;">'
                f"{html.escape(result.button_set.label)} "
                f'<span style="color:{state_color}; font-size:10px;">{state}</span>'
                "</h3>"
                f'<pre style="color:#d9dde7; margin-top:5px;">{lines}</pre>'
                "</div>"
            )
        self.report.setHtml("".join(sections))

    def show_other_images(
        self,
        directory: Path,
        result: SpecialAssetResult,
    ) -> None:
        self.images.clear()
        paths = find_other_theme_images(directory)
        self.tabs.setTabText(1, f"Other Images ({len(paths)})")
        activity_lines = "<br>".join(
            html.escape(line) for line in special_asset_report_lines(result)
        )
        self.image_activity.setHtml(
            f'<div style="color:#d9dde7; margin:5px;">{activity_lines}</div>'
        )

        category_names = {
            "help": "Help",
            "new": "New Project",
            "prompt_writer": "Prompt Writer",
            "settings": "Settings",
            "titlebar": "Title Bar",
        }
        grouped: dict[str, list[Path]] = {}
        for path in paths:
            relative = path.relative_to(directory)
            category_key = (
                relative.parts[0].casefold()
                if len(relative.parts) > 1
                else "other"
            )
            grouped.setdefault(category_key, []).append(path)

        category_order = ("new", "help", "prompt_writer", "settings", "titlebar", "other")
        ordered_keys = [key for key in category_order if key in grouped]
        ordered_keys.extend(sorted(key for key in grouped if key not in category_order))
        for category_key in ordered_keys:
            category_paths = grouped[category_key]
            category = QtWidgets.QTreeWidgetItem(
                (category_names.get(category_key, category_key.replace("_", " ").title()), "", "")
            )
            category.setFirstColumnSpanned(True)
            category_font = category.font(0)
            category_font.setBold(True)
            category.setFont(0, category_font)
            category.setForeground(0, QtGui.QColor(self.accent))
            self.images.addTopLevelItem(category)

            for path in category_paths:
                relative = path.relative_to(directory)
                reader = QtGui.QImageReader(str(path))
                size = reader.size()
                dimensions = (
                    f"{size.width()} × {size.height()}"
                    if size.isValid()
                    else "Unreadable"
                )
                image_format = bytes(reader.format()).decode("ascii", errors="replace").upper()
                if not image_format:
                    image_format = path.suffix.lstrip(".").upper()
                item = QtWidgets.QTreeWidgetItem(
                    (relative.as_posix(), dimensions, image_format)
                )
                item.setToolTip(0, str(path))
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(0, QtGui.QIcon(pixmap))
                category.addChild(item)
            category.setExpanded(True)


class ButtonerWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Buttoner — Letter Smith Button Maintenance")
        self.resize(1240, 820)
        self.setMinimumSize(900, 640)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(12)

        toolbar = QtWidgets.QHBoxLayout()
        heading_layout = QtWidgets.QVBoxLayout()
        heading_layout.setSpacing(2)
        heading = QtWidgets.QLabel("Buttoner")
        heading.setObjectName("heading")
        self.summary = QtWidgets.QLabel("Preparing button inventory…")
        self.summary.setObjectName("summary")
        heading_layout.addWidget(heading)
        heading_layout.addWidget(self.summary)
        toolbar.addLayout(heading_layout)
        toolbar.addStretch(1)
        self.run_button = QtWidgets.QPushButton("Run Again")
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self.run_buttoner)
        toolbar.addWidget(self.run_button)
        outer.addLayout(toolbar)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        self.panels: dict[str, ThemePanel] = {}
        for index, theme_id in enumerate(THEME_ORDER):
            panel = ThemePanel(theme_id, THEME_DISPLAY_NAMES[theme_id])
            self.panels[theme_id] = panel
            grid.addWidget(panel, index // 2, index % 2)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid, 1)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #10131a; color: #e9edf5; }
            QLabel#heading { font-size: 24px; font-weight: 700; }
            QLabel#summary { color: #9ca6b8; font-size: 12px; }
            QFrame#themePanel {
                background: #171b24;
                border: 1px solid #303746;
                border-radius: 10px;
            }
            QLabel#themeTitle { font-size: 17px; font-weight: 700; }
            QLabel#themeStatus {
                background: #242a36;
                border-radius: 9px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#themePath { color: #8994a8; font-size: 10px; }
            QTextBrowser#themeReport {
                background: #11151c;
                border: 0;
                padding: 8px;
                selection-background-color: #38516c;
            }
            QTextBrowser#imageActivity {
                background: #11151c;
                border: 0;
                border-bottom: 1px solid #272e3b;
                padding: 5px;
                selection-background-color: #38516c;
            }
            QTabWidget#themeTabs::pane {
                background: #11151c;
                border: 1px solid #272e3b;
                border-radius: 7px;
                top: -1px;
            }
            QTabBar::tab {
                background: #202632;
                color: #9ea9bb;
                border: 1px solid #303746;
                padding: 6px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected { background: #303949; color: #ffffff; }
            QTreeWidget#themeImages {
                background: #11151c;
                alternate-background-color: #151a22;
                border: 0;
                color: #d9dde7;
                outline: 0;
            }
            QHeaderView::section {
                background: #202632;
                color: #aeb7c7;
                border: 0;
                border-right: 1px solid #303746;
                padding: 5px;
                font-weight: 600;
            }
            QPushButton#runButton {
                background: #2e70d1;
                border: 0;
                border-radius: 7px;
                color: white;
                font-weight: 700;
                padding: 9px 18px;
            }
            QPushButton#runButton:hover { background: #3c82e4; }
            QPushButton#runButton:disabled { background: #3b4351; color: #8992a1; }
            """
        )
        QtCore.QTimer.singleShot(0, self.run_buttoner)

    @QtCore.Slot()
    def run_buttoner(self) -> None:
        self.run_button.setEnabled(False)
        self.summary.setText("Checking and repairing theme buttons…")
        QtWidgets.QApplication.processEvents()

        discovered = find_theme_directories()
        all_results: list[ButtonSetResult] = []
        all_special_results: list[SpecialAssetResult] = []
        missing_themes = 0
        for theme_id in THEME_ORDER:
            panel = self.panels[theme_id]
            directory = discovered.get(theme_id)
            if directory is None:
                missing_themes += 1
                panel.show_missing_theme()
                continue
            results = process_theme_button_sets(
                theme_id,
                directory,
                emit_report=False,
            )
            try:
                special_result = process_special_theme_images(theme_id, directory)
            except Exception as error:
                special_result = SpecialAssetResult(
                    theme_id,
                    THEME_DISPLAY_NAMES[theme_id],
                )
                special_result.errors.append(
                    f"Unexpected special-image error: {error}"
                )
            all_results.extend(results)
            all_special_results.append(special_result)
            panel.show_results(directory, results, special_result)

        errors = (
            sum(len(result.errors) for result in all_results)
            + sum(len(result.errors) for result in all_special_results)
            + missing_themes
        )
        unresolved = (
            sum(len(result.missing_after) for result in all_results)
            + sum(len(result.missing_after) for result in all_special_results)
        )
        changes = sum(
            len(result.normalized)
            + len(result.quarantined)
            + len(result.synced)
            + len(result.renamed)
            for result in all_results
        )
        changes += sum(
            len(result.normalized)
            + len(result.quarantined)
            + len(result.renamed)
            for result in all_special_results
        )
        timestamp = QtCore.QDateTime.currentDateTime().toString("h:mm:ss AP")
        self.summary.setText(
            f"{len(all_results)} button sets + {len(all_special_results)} image sets checked  •  "
            f"{changes} changes  •  "
            f"{unresolved} unresolved  •  {errors} errors  •  {timestamp}"
        )
        self.run_button.setEnabled(True)


def gui_main() -> int:
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Buttoner")
    app.setOrganizationName("Infini Works")
    window = ButtonerWindow()
    window.show()
    return app.exec() if owns_app else 0


def main() -> int:
    if "--console" in sys.argv[1:]:
        return console_main()
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
