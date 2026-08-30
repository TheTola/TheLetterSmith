from __future__ import annotations

import hashlib
import os
import re
import struct
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Optional

from message_html import extract_font_families, rewrite_font_families
from project_paths import application_paths
from transactional_io import atomic_copy_file, atomic_write_bytes, file_change_token


FONT_EXPORT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
BUNDLED_FONT_DIR_CANDIDATES = (
    Path("resources/app/fonts"),
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
TTC_SIGNATURE = b"ttcf"
TTC_VERSIONS = {0x00010000, 0x00020000}
SFNT_VERSIONS = {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"}
SFNT_CHECKSUM_MAGIC = 0xB1B0AFBA
MAX_COLLECTION_FACES = 4096
MAX_SFNT_TABLES = 4096
MAX_NAME_TABLE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ResolvedFontFace:
    display_name: str
    source_path: Path
    weight: int
    style: str
    collection_index: int | None = None


@dataclass(frozen=True)
class _SfntTableRecord:
    tag: bytes
    checksum: int
    offset: int
    length: int


@dataclass(frozen=True)
class _FontCollectionFace:
    index: int
    family_names: tuple[str, ...]
    display_name: str
    subfamily: str
    weight: int
    style: str


@dataclass(frozen=True)
class _FontFaceExport:
    suffix: str
    digest: str
    size: int
    payload: bytes | None = None


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
    # Google Fonts variable files append axis tags such as ``[wght]`` or
    # ``[opsz,wght]``. Those tags describe the face, not the family name.
    normalized_stem = re.sub(r"\[[^\]]+\]", "", path.stem)
    parts = [part for part in re.split(r"[\s_\-.]+", normalized_stem) if part]
    filtered = [part for part in parts if part.casefold() not in FONT_STYLE_TOKENS]
    keys = {_font_match_key(normalized_stem)}
    if filtered:
        keys.add(_font_match_key(" ".join(filtered)))
    return {key for key in keys if key}


def _font_search_dirs() -> tuple[Path, ...]:
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
        )
    if os.name != "nt":
        return (
            Path.home() / ".local" / "share" / "fonts",
            Path("/usr/local/share/fonts"),
            Path("/usr/share/fonts"),
        )
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
    if os.name != "nt":
        entries: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for directory in _font_search_dirs():
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if (
                    not path.is_file()
                    or path.suffix.casefold() not in FONT_EXPORT_EXTENSIONS
                ):
                    continue
                resolved = path.resolve()
                identity = str(resolved).casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                if resolved.suffix.casefold() == ".ttc":
                    for face in _font_collection_faces(resolved):
                        for family_name in face.family_names:
                            display_name = " ".join(
                                part for part in (family_name, face.subfamily) if part
                            )
                            entries.append((display_name or family_name, resolved))
                else:
                    entries.append((resolved.stem, resolved))
        return tuple(entries)

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
    normalized_name = re.sub(r"\[[^\]]+\]", "", display_name)
    tokens = set(
        part for part in re.split(r"[\s-]+", normalized_name.casefold()) if part
    )
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


def _read_exact(
    stream: BinaryIO,
    offset: int,
    length: int,
    file_size: int,
) -> bytes:
    if offset < 0 or length < 0 or offset > file_size - length:
        raise ValueError("Font table points outside the collection.")
    stream.seek(offset)
    value = stream.read(length)
    if len(value) != length:
        raise ValueError("Font collection ended unexpectedly.")
    return value


def _font_collection_offsets(stream: BinaryIO, file_size: int) -> tuple[int, ...]:
    header = _read_exact(stream, 0, 12, file_size)
    signature, version, count = struct.unpack(">4sII", header)
    if signature != TTC_SIGNATURE or version not in TTC_VERSIONS:
        raise ValueError("Unsupported TrueType collection header.")
    if count < 1 or count > MAX_COLLECTION_FACES:
        raise ValueError("TrueType collection has an invalid face count.")
    offsets = struct.unpack(
        f">{count}I",
        _read_exact(stream, 12, count * 4, file_size),
    )
    minimum_offset = 12 + count * 4
    if any(offset < minimum_offset or offset > file_size - 12 for offset in offsets):
        raise ValueError("TrueType collection has an invalid face offset.")
    return tuple(offsets)


def _sfnt_table_records(
    stream: BinaryIO,
    sfnt_offset: int,
    file_size: int,
) -> tuple[bytes, tuple[_SfntTableRecord, ...]]:
    header = _read_exact(stream, sfnt_offset, 12, file_size)
    sfnt_version, table_count, _search_range, _entry_selector, _range_shift = (
        struct.unpack(">4sHHHH", header)
    )
    if sfnt_version not in SFNT_VERSIONS:
        raise ValueError("TrueType collection contains an unsupported font flavor.")
    if table_count < 1 or table_count > MAX_SFNT_TABLES:
        raise ValueError("TrueType collection face has an invalid table count.")
    directory = _read_exact(
        stream,
        sfnt_offset + 12,
        table_count * 16,
        file_size,
    )
    records: list[_SfntTableRecord] = []
    for index in range(table_count):
        tag, checksum, offset, length = struct.unpack_from(
            ">4sIII",
            directory,
            index * 16,
        )
        if offset > file_size - length:
            raise ValueError("TrueType collection contains an invalid table range.")
        records.append(
            _SfntTableRecord(
                tag=tag,
                checksum=checksum,
                offset=offset,
                length=length,
            )
        )
    return sfnt_version, tuple(records)


def _decode_font_name(platform_id: int, value: bytes) -> str:
    if platform_id in {0, 3}:
        if len(value) % 2:
            return ""
        encoding = "utf-16-be"
    elif platform_id == 1:
        encoding = "mac_roman"
    else:
        encoding = "latin-1"
    try:
        decoded = value.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return ""
    return _normalize_font_display_name(decoded.replace("\x00", ""))


def _name_record_score(platform_id: int, language_id: int) -> int:
    platform_score = {3: 30, 0: 20, 1: 10}.get(platform_id, 0)
    is_english = (
        (platform_id == 3 and language_id == 0x0409)
        or (platform_id == 1 and language_id == 0)
    )
    return platform_score + (10 if is_english else 0)


def _sfnt_names(
    stream: BinaryIO,
    record: _SfntTableRecord,
    file_size: int,
) -> dict[int, tuple[tuple[int, str], ...]]:
    if record.length < 6 or record.length > MAX_NAME_TABLE_BYTES:
        raise ValueError("Font collection has an invalid name table size.")
    table = _read_exact(stream, record.offset, record.length, file_size)
    table_format, count, string_offset = struct.unpack_from(">HHH", table, 0)
    records_end = 6 + count * 12
    minimum_string_offset = records_end
    if table_format == 1:
        if records_end > len(table) - 2:
            raise ValueError("Font collection has an invalid name table.")
        language_tag_count = struct.unpack_from(">H", table, records_end)[0]
        minimum_string_offset += 2 + language_tag_count * 4
    if (
        table_format not in {0, 1}
        or records_end > len(table)
        or minimum_string_offset > len(table)
        or string_offset < minimum_string_offset
        or string_offset > len(table)
    ):
        raise ValueError("Font collection has an invalid name table.")

    names: dict[int, list[tuple[int, str]]] = {}
    for index in range(count):
        (
            platform_id,
            _encoding_id,
            language_id,
            name_id,
            length,
            offset,
        ) = struct.unpack_from(">HHHHHH", table, 6 + index * 12)
        start = string_offset + offset
        if start > len(table) - length:
            continue
        decoded = _decode_font_name(platform_id, table[start:start + length])
        if decoded:
            names.setdefault(name_id, []).append(
                (_name_record_score(platform_id, language_id), decoded)
            )
    return {name_id: tuple(values) for name_id, values in names.items()}


def _preferred_font_name(
    names: dict[int, tuple[tuple[int, str], ...]],
    *name_ids: int,
) -> str:
    for name_id in name_ids:
        values = names.get(name_id, ())
        if values:
            return max(values, key=lambda item: (item[0], item[1].casefold()))[1]
    return ""


def _font_family_names(
    names: dict[int, tuple[tuple[int, str], ...]],
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for name_id in (16, 1):
        for _score, value in sorted(names.get(name_id, ()), reverse=True):
            identity = value.casefold()
            if identity not in seen:
                seen.add(identity)
                result.append(value)
    return tuple(result)


def _font_collection_face_metadata(
    stream: BinaryIO,
    file_size: int,
    index: int,
    sfnt_offset: int,
) -> _FontCollectionFace:
    _sfnt_version, records = _sfnt_table_records(stream, sfnt_offset, file_size)
    record_by_tag = {record.tag: record for record in records}
    name_record = record_by_tag.get(b"name")
    if name_record is None:
        raise ValueError("Font collection face has no name table.")
    names = _sfnt_names(stream, name_record, file_size)
    family_names = _font_family_names(names)
    if not family_names:
        raise ValueError("Font collection face has no family name.")

    subfamily = _preferred_font_name(names, 17, 2)
    full_name = _preferred_font_name(names, 4)
    display_name = full_name or " ".join(
        part for part in (family_names[0], subfamily) if part
    )
    weight, style = _classify_font_face(
        " ".join(part for part in (family_names[0], subfamily) if part)
    )

    os2_record = record_by_tag.get(b"OS/2")
    if os2_record is not None and os2_record.length >= 6:
        os2 = _read_exact(
            stream,
            os2_record.offset,
            min(os2_record.length, 64),
            file_size,
        )
        os2_weight = struct.unpack_from(">H", os2, 4)[0]
        if 1 <= os2_weight <= 1000:
            weight = os2_weight
        if len(os2) >= 64:
            selection = struct.unpack_from(">H", os2, 62)[0]
            if selection & (1 << 9):
                style = "oblique"
            elif selection & 1:
                style = "italic"

    return _FontCollectionFace(
        index=index,
        family_names=family_names,
        display_name=display_name,
        subfamily=subfamily,
        weight=weight,
        style=style,
    )


@lru_cache(maxsize=128)
def _load_font_collection_faces_cached(
    path_value: str,
    file_size: int,
    modified_ns: int,
    change_token: int,
) -> tuple[_FontCollectionFace, ...]:
    del modified_ns, change_token
    result: list[_FontCollectionFace] = []
    with Path(path_value).open("rb") as stream:
        offsets = _font_collection_offsets(stream, file_size)
        for index, offset in enumerate(offsets):
            try:
                result.append(
                    _font_collection_face_metadata(
                        stream,
                        file_size,
                        index,
                        offset,
                    )
                )
            except (OSError, ValueError, struct.error):
                continue
    return tuple(result)


def _font_collection_faces(path: Path) -> tuple[_FontCollectionFace, ...]:
    try:
        stat_result = path.stat()
        return _load_font_collection_faces_cached(
            str(path.resolve()),
            stat_result.st_size,
            stat_result.st_mtime_ns,
            file_change_token(path, stat_result=stat_result),
        )
    except (OSError, ValueError, struct.error):
        return ()


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


def _font_collection_has_family(path: Path, family_key: str) -> bool:
    return any(
        family_key in {_font_match_key(name) for name in face.family_names}
        for face in _font_collection_faces(path)
    )


def _bundled_font_files(project_root: Path, family: str) -> list[Path]:
    family_key = _font_match_key(family)
    matches: list[Path] = []
    seen: set[str] = set()
    paths = application_paths(project_root)
    for base in dict.fromkeys((paths.resource_root, paths.workspace_root)):
        for relative_dir in BUNDLED_FONT_DIR_CANDIDATES:
            folder = (base / relative_dir).resolve()
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file() or path.suffix.casefold() not in FONT_EXPORT_EXTENSIONS:
                    continue
                if (
                    family_key not in _font_file_match_keys(path)
                    and not (
                        path.suffix.casefold() == ".ttc"
                        and _font_collection_has_family(path, family_key)
                    )
                ):
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
    family_key = _font_match_key(family_name)

    resolved: dict[tuple[int, str], ResolvedFontFace] = {}

    def register_face(display_name: str, source_path: Path) -> None:
        if source_path.suffix.casefold() not in FONT_EXPORT_EXTENSIONS:
            return
        if source_path.suffix.casefold() == ".ttc":
            for collection_face in _font_collection_faces(source_path):
                if family_key not in {
                    _font_match_key(name)
                    for name in collection_face.family_names
                }:
                    continue
                key = (collection_face.weight, collection_face.style)
                if key not in resolved:
                    resolved[key] = ResolvedFontFace(
                        display_name=collection_face.display_name,
                        source_path=source_path,
                        weight=collection_face.weight,
                        style=collection_face.style,
                        collection_index=collection_face.index,
                    )
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


def _sfnt_checksum(value: bytes | bytearray) -> int:
    total = 0
    padded_length = (len(value) + 3) & ~3
    for offset in range(0, padded_length, 4):
        chunk = value[offset:offset + 4]
        if len(chunk) < 4:
            chunk = chunk + (b"\0" * (4 - len(chunk)))
        total = (total + int.from_bytes(chunk, "big")) & 0xFFFFFFFF
    return total


def _extract_font_collection_face(
    source_path: Path,
    collection_index: int,
) -> tuple[bytes, str]:
    file_size = source_path.stat().st_size
    with source_path.open("rb") as stream:
        offsets = _font_collection_offsets(stream, file_size)
        if collection_index < 0 or collection_index >= len(offsets):
            raise ValueError("TrueType collection face index is out of range.")
        sfnt_version, records = _sfnt_table_records(
            stream,
            offsets[collection_index],
            file_size,
        )
        records = tuple(record for record in records if record.tag != b"DSIG")
        tags = [record.tag for record in records]
        if len(set(tags)) != len(tags):
            raise ValueError("TrueType collection face has duplicate table tags.")
        if b"head" not in tags:
            raise ValueError("TrueType collection face has no head table.")

        sorted_records = sorted(records, key=lambda record: record.tag)
        table_count = len(sorted_records)
        entry_selector = table_count.bit_length() - 1
        search_range = (1 << entry_selector) * 16
        range_shift = table_count * 16 - search_range
        output = bytearray(
            struct.pack(
                ">4sHHHH",
                sfnt_version,
                table_count,
                search_range,
                entry_selector,
                range_shift,
            )
            + (b"\0" * (table_count * 16))
        )
        head_output_offset: int | None = None
        for index, record in enumerate(sorted_records):
            table = bytearray(
                _read_exact(stream, record.offset, record.length, file_size)
            )
            if record.tag == b"head":
                if len(table) < 12:
                    raise ValueError("TrueType collection face has an invalid head table.")
                struct.pack_into(">I", table, 8, 0)
            output_offset = len(output)
            output.extend(table)
            output.extend(b"\0" * ((-len(table)) % 4))
            struct.pack_into(
                ">4sIII",
                output,
                12 + index * 16,
                record.tag,
                _sfnt_checksum(table),
                output_offset,
                len(table),
            )
            if record.tag == b"head":
                head_output_offset = output_offset

    if head_output_offset is None:
        raise ValueError("TrueType collection face has no head table.")
    checksum_adjustment = (SFNT_CHECKSUM_MAGIC - _sfnt_checksum(output)) & 0xFFFFFFFF
    struct.pack_into(">I", output, head_output_offset + 8, checksum_adjustment)
    suffix = ".otf" if sfnt_version == b"OTTO" else ".ttf"
    return bytes(output), suffix


def _font_face_format(path: Path) -> str:
    return {
        ".ttf": "truetype",
        ".otf": "opentype",
        ".woff": "woff",
        ".woff2": "woff2",
    }[path.suffix.casefold()]


def _atomic_copy_file(source: Path, destination: Path) -> None:
    atomic_copy_file(source, destination)


def _atomic_write_bytes(destination: Path, value: bytes) -> None:
    atomic_write_bytes(destination, value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _font_face_export(face: ResolvedFontFace) -> _FontFaceExport:
    if face.collection_index is not None:
        payload, suffix = _extract_font_collection_face(
            face.source_path,
            face.collection_index,
        )
        return _FontFaceExport(
            suffix=suffix,
            digest=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            payload=payload,
        )
    if face.source_path.suffix.casefold() == ".ttc":
        raise ValueError("TrueType collection face index is required for export.")
    return _FontFaceExport(
        suffix=face.source_path.suffix.casefold(),
        digest=_file_sha256(face.source_path),
        size=face.source_path.stat().st_size,
    )


def _clean_exported_fonts(fonts_dir: Path) -> None:
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for path in fonts_dir.iterdir():
        if path.is_file() and path.name.startswith("ls-font-"):
            path.unlink()


def build_embedded_font_payload(
    project_root: Path,
    message_html: str,
    fonts_dir: Path,
    *,
    reuse_fonts_dir: Path | None = None,
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
            "Choose another font or place a TTF, OTF, TTC, WOFF, or WOFF2 file "
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
            export = _font_face_export(face)
            digest = export.digest[:12]
            output_name = (
                f"ls-font-{family_index}-{face_index}-{digest}"
                f"{export.suffix}"
            )
            destination = Path(fonts_dir) / output_name
            reusable = (
                Path(reuse_fonts_dir) / output_name
                if reuse_fonts_dir is not None
                else None
            )
            if (
                reusable is not None
                and not reusable.is_symlink()
                and reusable.is_file()
                and reusable.stat().st_size == export.size
                and _file_sha256(reusable) == export.digest
            ):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.unlink(missing_ok=True)
                try:
                    os.link(reusable, destination)
                except OSError:
                    _atomic_copy_file(reusable, destination)
            elif export.payload is not None:
                _atomic_write_bytes(destination, export.payload)
            else:
                _atomic_copy_file(face.source_path, destination)
            copied_files.append(output_name)
            css_rules.append(
                "@font-face{"
                f"font-family:'{alias}';"
                f"src:url('gallery/fonts/{output_name}') format('{_font_face_format(Path(output_name))}');"
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
