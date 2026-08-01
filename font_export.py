from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from message_html import extract_font_families, rewrite_font_families


FONT_EXPORT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
BUNDLED_FONT_DIR_CANDIDATES = (
    Path("gallery/app/fonts"),
    Path("gallery/user/fonts"),
    Path("gallery/fonts"),
    Path("fonts"),
    Path("assets/fonts"),
)
FONT_REGISTRY_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")
FONT_DISPLAY_NAME_SUFFIXES = (
    "",
    " Regular",
    " Roman",
    " Italic",
    " Oblique",
    " Bold",
    " Bold Italic",
    " Bold Oblique",
)
FONT_STYLE_TOKENS = {
    "thin",
    "extralight",
    "ultralight",
    "light",
    "semilight",
    "demilight",
    "book",
    "normal",
    "regular",
    "roman",
    "medium",
    "demibold",
    "semibold",
    "bold",
    "extrabold",
    "ultrabold",
    "black",
    "heavy",
    "italic",
    "oblique",
    "font",
}


@dataclass(frozen=True)
class ResolvedFontFace:
    display_name: str
    source_path: Path
    weight: int
    style: str


@dataclass(frozen=True)
class FontFamilyInspection:
    family: str
    faces: tuple[ResolvedFontFace, ...]

    @property
    def status(self) -> str:
        if not self.faces:
            return "Not found"
        return "Ready to embed"


@dataclass(frozen=True)
class FontExportResult:
    html: str
    css: str
    report: dict[str, tuple[str, ...]]


class FontExportError(RuntimeError):
    def __init__(self, message: str, report: dict[str, tuple[str, ...]]) -> None:
        super().__init__(message)
        self.report = report


def _normalize_font_display_name(value: str) -> str:
    return re.sub(r"\s+", " ", FONT_REGISTRY_SUFFIX_RE.sub("", (value or "").strip())).strip()


def _font_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _font_file_match_keys(path: Path) -> set[str]:
    parts = [part for part in re.split(r"[\s_\-.]+", path.stem) if part]
    filtered = [part for part in parts if part.casefold() not in FONT_STYLE_TOKENS]
    keys = {_font_match_key(path.stem)}
    if filtered:
        keys.add(_font_match_key(" ".join(filtered)))
    return {key for key in keys if key}


def _font_search_dirs() -> tuple[Path, ...]:
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    result = [windows_dir]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        result.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    return tuple(result)


def _resolve_font_file_path(value: str) -> Optional[Path]:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_file():
        return candidate.resolve()

    for base in _font_search_dirs():
        for probe in (base / raw, base / candidate.name):
            if probe.is_file():
                return probe.resolve()
    return None


@lru_cache(maxsize=1)
def _load_font_registry() -> tuple[tuple[str, Path], ...]:
    try:
        import winreg
    except Exception:
        return ()

    registry_keys = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    entries: list[tuple[str, Path]] = []
    seen: set[tuple[str, str]] = set()

    for root, key_path in registry_keys:
        try:
            key = winreg.OpenKey(root, key_path)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                display_name = _normalize_font_display_name(name)
                source_path = _resolve_font_file_path(str(value))
                if not display_name or source_path is None:
                    continue
                identity = (display_name.casefold(), str(source_path).casefold())
                if identity not in seen:
                    seen.add(identity)
                    entries.append((display_name, source_path))
        finally:
            winreg.CloseKey(key)

    return tuple(entries)


def _classify_font_face(display_name: str) -> tuple[int, str]:
    tokens = set(part for part in re.split(r"[\s-]+", display_name.casefold()) if part)
    if {"black", "heavy"} & tokens:
        weight = 900
    elif {"extrabold", "ultrabold"} & tokens:
        weight = 800
    elif "bold" in tokens:
        weight = 700
    elif {"demibold", "semibold"} & tokens:
        weight = 600
    elif "medium" in tokens:
        weight = 500
    elif {"light", "book"} & tokens:
        weight = 300
    elif {"thin", "extralight", "ultralight"} & tokens:
        weight = 200
    else:
        weight = 400

    if "italic" in tokens:
        style = "italic"
    elif "oblique" in tokens:
        style = "oblique"
    else:
        style = "normal"
    return weight, style


def _is_style_suffix_only(display_name: str, family: str) -> bool:
    normalized_name = _normalize_font_display_name(display_name)
    normalized_family = _normalize_font_display_name(family)
    if normalized_name.casefold() == normalized_family.casefold():
        return True
    if not normalized_name.casefold().startswith(normalized_family.casefold() + " "):
        return False
    suffix = normalized_name[len(normalized_family):].strip()
    tokens = [part for part in re.split(r"[\s-]+", suffix.casefold()) if part]
    return bool(tokens) and all(token in FONT_STYLE_TOKENS for token in tokens)


def _bundled_font_files(project_root: Path, family: str) -> list[Path]:
    family_key = _font_match_key(family)
    matches: list[Path] = []
    seen: set[str] = set()
    for relative_dir in BUNDLED_FONT_DIR_CANDIDATES:
        folder = (project_root / relative_dir).resolve()
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in FONT_EXPORT_EXTENSIONS:
                continue
            if family_key not in _font_file_match_keys(path):
                continue
            identity = str(path.resolve()).casefold()
            if identity not in seen:
                seen.add(identity)
                matches.append(path.resolve())
    return matches


def resolve_font_faces_for_family(project_root: Path, family: str) -> tuple[ResolvedFontFace, ...]:
    family_name = _normalize_font_display_name(family)
    if not family_name:
        return ()

    resolved: dict[tuple[int, str], ResolvedFontFace] = {}

    def register_face(display_name: str, source_path: Path) -> None:
        if source_path.suffix.casefold() not in FONT_EXPORT_EXTENSIONS:
            return
        weight, style = _classify_font_face(display_name)
        key = (weight, style)
        if key not in resolved:
            resolved[key] = ResolvedFontFace(
                display_name=display_name,
                source_path=source_path,
                weight=weight,
                style=style,
            )

    for path in _bundled_font_files(Path(project_root), family_name):
        register_face(path.stem, path)

    registry_entries = _load_font_registry()
    registry_map = {name.casefold(): path for name, path in registry_entries}
    for suffix in FONT_DISPLAY_NAME_SUFFIXES:
        display_name = f"{family_name}{suffix}"
        source_path = registry_map.get(display_name.casefold())
        if source_path is not None:
            register_face(display_name, source_path)
    for display_name, source_path in registry_entries:
        if _is_style_suffix_only(display_name, family_name):
            register_face(display_name, source_path)

    return tuple(sorted(resolved.values(), key=lambda face: (face.weight, face.style, face.display_name.casefold())))


def inspect_font_family(project_root: Path, family: str) -> FontFamilyInspection:
    return FontFamilyInspection(
        family=_normalize_font_display_name(family),
        faces=resolve_font_faces_for_family(Path(project_root), family),
    )


def _font_face_format(path: Path) -> str:
    return {
        ".ttf": "truetype",
        ".otf": "opentype",
        ".woff": "woff",
        ".woff2": "woff2",
    }[path.suffix.casefold()]


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _clean_exported_fonts(fonts_dir: Path) -> None:
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for path in fonts_dir.iterdir():
        if path.is_file() and path.name.startswith("ls-font-"):
            path.unlink()


def build_embedded_font_payload(
    project_root: Path,
    message_html: str,
    fonts_dir: Path,
) -> FontExportResult:
    families = extract_font_families(message_html)
    inspections = [inspect_font_family(Path(project_root), family) for family in families]
    missing = tuple(item.family for item in inspections if not item.faces)
    report = {
        "embedded": tuple(item.family for item in inspections if item.faces),
        "files": (),
        "fallback": missing,
    }

    if missing:
        details: list[str] = []
        details.append("Font files were not found for: " + ", ".join(missing))
        details.append(
            "Choose another font or place a TTF, OTF, WOFF, or WOFF2 file "
            "in gallery/user/fonts."
        )
        raise FontExportError("\n".join(details), report)

    _clean_exported_fonts(Path(fonts_dir))
    aliases: dict[str, str] = {}
    css_rules: list[str] = []
    copied_files: list[str] = []

    for family_index, inspection in enumerate(inspections, start=1):
        alias = f"LetterSmithFont{family_index}"
        aliases[inspection.family] = alias
        for face_index, face in enumerate(inspection.faces, start=1):
            digest = hashlib.sha256(face.source_path.read_bytes()).hexdigest()[:12]
            output_name = (
                f"ls-font-{family_index}-{face_index}-{digest}"
                f"{face.source_path.suffix.casefold()}"
            )
            _atomic_copy_file(face.source_path, Path(fonts_dir) / output_name)
            copied_files.append(output_name)
            css_rules.append(
                "@font-face{"
                f"font-family:'{alias}';"
                f"src:url('gallery/fonts/{output_name}') format('{_font_face_format(face.source_path)}');"
                f"font-style:{face.style};"
                f"font-weight:{face.weight};"
                "font-display:block;"
                "}"
            )

    report = {
        **report,
        "files": tuple(copied_files),
    }
    return FontExportResult(
        html=rewrite_font_families(message_html, aliases),
        css="\n".join(css_rules),
        report=report,
    )
