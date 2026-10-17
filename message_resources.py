"""Restricted raster resources for page-bound message documents."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PySide6.QtGui import QImage, QImageReader, QTextDocument


_RASTER_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_RASTER_FORMATS = {b"bmp", b"gif", b"jpeg", b"jpg", b"png", b"webp"}
_DATA_IMAGE = re.compile(
    r"data:image/(?:png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)", re.I
)
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_PIXELS = 64 * 1024 * 1024


class MessageTextDocument(QTextDocument):
    """Preview document using the same restricted images as the bound editor."""

    def __init__(self, resource_roots: tuple[Path, ...], parent=None) -> None:
        super().__init__(parent)
        self.resource_roots = tuple(Path(root).resolve() for root in resource_roots)

    def loadResource(self, resource_type: int, name: QUrl):
        if resource_type == QTextDocument.ImageResource:
            return load_message_image(name.toString(), self.resource_roots)
        return QByteArray()


def resolve_message_resource(uri: str, resource_roots: tuple[Path, ...]) -> Path | None:
    """Resolve only relative raster paths that remain inside a supplied root."""
    try:
        value = unquote(str(uri))
        parsed = urlsplit(value)
        if (
            parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or "\\" in value or ":" in value
            or any(ord(character) < 32 for character in value)
        ):
            return None
        relative = Path(value)
        if (
            not value or relative.is_absolute() or ".." in relative.parts
            or relative.suffix.lower() not in _RASTER_SUFFIXES
        ):
            return None
        for resource_root in resource_roots:
            root = Path(resource_root).resolve()
            candidate = (root / relative).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return candidate
    except (OSError, ValueError):
        pass
    return None


def load_message_image(uri: str, resource_roots: tuple[Path, ...]) -> QImage:
    """Load bounded raster data; never invoke Qt's file/network fallback."""
    try:
        match = _DATA_IMAGE.fullmatch(str(uri))
        if match is not None:
            encoded = re.sub(r"\s+", "", match.group(1))
            if len(encoded) > ((_MAX_IMAGE_BYTES + 2) // 3) * 4:
                return QImage()
            data = base64.b64decode(encoded, validate=True)
        else:
            path = resolve_message_resource(uri, resource_roots)
            if path is None or path.stat().st_size > _MAX_IMAGE_BYTES:
                return QImage()
            with path.open("rb") as source:
                data = source.read(_MAX_IMAGE_BYTES + 1)
        if len(data) > _MAX_IMAGE_BYTES:
            return QImage()
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        if bytes(reader.format()).lower() not in _RASTER_FORMATS:
            return QImage()
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > _MAX_IMAGE_PIXELS:
            return QImage()
        return reader.read()
    except (OSError, ValueError):
        return QImage()
