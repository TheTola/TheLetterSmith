from __future__ import annotations

import base64
import binascii
import hashlib
import html
import io
import shutil
import tempfile
import uuid
import warnings
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from PIL import Image

from message_html import sanitize_message_html
from message_import import MAX_OUTPUT_CHARACTERS, MessageImportError
from project_paths import application_paths


MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000
MAX_IMPORT_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMPORT_IMAGES = 256
_ASSET_DIRECTORY = PurePosixPath("gallery/message_assets")
_IMAGE_EXTENSIONS = {
    "PNG": ".png", "JPEG": ".jpg", "GIF": ".gif",
    "WEBP": ".webp", "BMP": ".bmp",
}


def _relative_image_path(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    decoded = unquote(parsed.path)
    if (
        not decoded
        or decoded.startswith(("/", "\\"))
        or any(character in decoded for character in '\\:<>"|?*')
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        return None
    parts = PurePosixPath(decoded).parts
    if not parts or ".." in parts:
        return None
    if any(part.endswith((".", " ")) for part in parts):
        return None
    if PurePosixPath(parts[-1]).suffix.casefold() not in _IMAGE_EXTENSIONS.values():
        if PurePosixPath(parts[-1]).suffix.casefold() != ".jpeg":
            return None
    return PurePosixPath(*parts).as_posix()


class _ResourceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        relative = _relative_image_path(dict(attrs).get("src") or "")
        if relative and relative not in self.resources:
            self.resources.append(relative)


def message_resources(value: str) -> list[str]:
    """Return unique safe local image paths, without reading any resources."""
    collector = _ResourceCollector()
    collector.feed(sanitize_message_html(value))
    collector.close()
    return collector.resources


@dataclass
class StagedMessageImport:
    html: str
    resources: list[str]
    notices: list[str]
    _project_root: Path = field(repr=False)
    _temporary: tempfile.TemporaryDirectory = field(repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def commit_resources(self) -> list[str]:
        """Publish validated assets before the caller changes its captured document."""
        if self._committed:
            return list(self.resources)
        if self._closed:
            raise MessageImportError("That import has already been discarded.")
        created: list[Path] = []
        try:
            destination = self._project_root.joinpath(*_ASSET_DIRECTORY.parts)
            if not destination.resolve().is_relative_to(self._project_root):
                raise MessageImportError("The message image folder is outside this project.")
            if self.resources:
                destination.mkdir(parents=True, exist_ok=True)
            for relative in self.resources:
                target = self._project_root / relative
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise MessageImportError("The message image destination is unsafe.")
                # Exclusive creation preserves every pre-existing project asset.
                with target.open("xb") as output:
                    created.append(target)
                    with (Path(self._temporary.name) / target.name).open("rb") as source:
                        shutil.copyfileobj(source, output)
        except (OSError, MessageImportError) as error:
            for path in created:
                try:
                    path.unlink()
                except OSError:
                    pass
            if isinstance(error, MessageImportError):
                raise
            raise MessageImportError("The imported images could not be saved.") from error
        self._committed = True
        return list(self.resources)

    def cleanup(self) -> None:
        """Discard only temporary files; committed project resources remain intact."""
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def __enter__(self) -> StagedMessageImport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.cleanup()


class _ImportImages(HTMLParser):
    def __init__(self, source_root: Path) -> None:
        super().__init__(convert_charrefs=False)
        self.source_root = source_root
        self.parts: list[str] = []
        self.assets: dict[str, bytes] = {}
        self.notices: list[str] = []
        self._source_cache: dict[str, str | None] = {}
        self._digest_cache: dict[str, str] = {}
        self._image_bytes = 0

    def _image(self, source: str) -> str | None:
        if source in self._source_cache:
            return self._source_cache[source]
        if len(self._source_cache) >= MAX_IMPORT_IMAGES:
            raise MessageImportError("That document contains too many images to import.")
        self._source_cache[source] = None
        label = "image"
        try:
            if source.casefold().startswith("data:"):
                encoded = source.partition(",")[2].replace("\r", "").replace("\n", "")
                if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3) + 4:
                    raise ValueError("image is too large")
                payload = base64.b64decode(encoded, validate=True)
                label = "embedded image"
            else:
                relative = _relative_image_path(source)
                if not relative:
                    raise ValueError("unsafe image path")
                image_path = (self.source_root / relative).resolve()
                if not image_path.is_relative_to(self.source_root):
                    raise ValueError("image is outside the source folder")
                label = Path(relative).name
                if not image_path.is_file():
                    raise FileNotFoundError
                if image_path.stat().st_size > MAX_IMAGE_BYTES:
                    raise ValueError("image is too large")
                with image_path.open("rb") as stream:
                    payload = stream.read(MAX_IMAGE_BYTES + 1)
            if not payload or len(payload) > MAX_IMAGE_BYTES:
                raise ValueError("image is empty or too large")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(payload), formats=list(_IMAGE_EXTENSIONS)) as image:
                    extension = _IMAGE_EXTENSIONS.get(image.format or "")
                    if extension is None or image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("unsupported or oversized image")
                    image.verify()
            digest = hashlib.sha256(payload).hexdigest()
            if digest in self._digest_cache:
                relative = self._digest_cache[digest]
            else:
                self._image_bytes += len(payload)
                if self._image_bytes > MAX_IMPORT_IMAGE_BYTES:
                    raise MessageImportError("The imported images are too large to save.")
                relative = (_ASSET_DIRECTORY / f"{uuid.uuid4().hex}-{digest[:16]}{extension}").as_posix()
                self.assets[relative] = payload
                self._digest_cache[digest] = relative
            self._source_cache[source] = relative
            return relative
        except (OSError, ValueError, SyntaxError, EOFError, binascii.Error,
                Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            reason = "file missing" if isinstance(error, FileNotFoundError) else "unreadable or unsafe image"
            self.notices.append(f"Image omitted ({reason}): {label}.")
            return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            self.parts.append(self.get_starttag_text())
            return
        attributes = dict(attrs)
        source = attributes.get("src") or ""
        relative = self._image(source) if source else None
        if not source:
            self.notices.append("An unsupported image was omitted.")
        if relative is None:
            if attributes.get("alt"):
                self.parts.append(html.escape(attributes["alt"]))
            return
        rendered = [f'src="{relative}"']
        for key in ("alt", "width", "height", "style"):
            if attributes.get(key):
                rendered.append(f'{key}="{html.escape(attributes[key], quote=True)}"')
        self.parts.append("<img " + " ".join(rendered) + ">")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


def stage_import_html(
    raw_html: str, *, source_path: str | Path, project_root: str | Path,
) -> StagedMessageImport:
    """Stage existing worker output without changing a letter or runtime assets."""
    if not isinstance(raw_html, str) or len(raw_html) > MAX_OUTPUT_CHARACTERS:
        raise MessageImportError("That document contains too much text to import.")
    root = Path(project_root).resolve()
    source = Path(source_path).resolve()
    parser = _ImportImages(source.parent)
    parser.feed(sanitize_message_html(raw_html))
    parser.close()
    rendered = sanitize_message_html("".join(parser.parts))
    temporary_root = application_paths(root).temporary_root
    try:
        temporary_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="message-import-", dir=temporary_root)
    except OSError as error:
        raise MessageImportError("The imported images could not be staged.") from error
    try:
        for relative, payload in parser.assets.items():
            (Path(temporary.name) / PurePosixPath(relative).name).write_bytes(payload)
    except OSError as error:
        temporary.cleanup()
        raise MessageImportError("The imported images could not be staged.") from error
    notices = list(dict.fromkeys(parser.notices))
    if source.suffix.casefold() in {".docx", ".odt", ".pdf"}:
        notices.insert(0, "Imported text is editable; some source formatting or graphics may differ.")
    return StagedMessageImport(rendered, list(parser.assets), notices, root, temporary)


__all__ = ["StagedMessageImport", "message_resources", "stage_import_html"]
