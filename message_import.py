from __future__ import annotations

import html
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from message_html import repair_common_mojibake, sanitize_message_html

try:
    import mammoth  # type: ignore
except Exception:
    mammoth = None

try:
    from docx import Document  # type: ignore
except Exception:
    Document = None  # type: ignore

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:
    PdfReader = None  # type: ignore


MESSAGE_IMPORT_WORKER_FLAG = "--lettersmith-message-import-worker"
MESSAGE_IMPORT_TIMEOUT_MS = 30_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_TEXT_INPUT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4_096
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 1_000
MAX_PDF_PAGES = 1_000
MAX_PDF_STREAM_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_CHARACTERS = 4_000_000
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
MAX_WORKER_CPU_SECONDS = 25
_TEXT_CHUNK_CHARACTERS = 64 * 1024
_SUPPORTED_EXTENSIONS = frozenset({".docx", ".htm", ".html", ".odt", ".pdf", ".txt"})
_ZIP_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_EOCD_MIN_SIZE = 22
_ZIP_MAX_COMMENT_SIZE = 65_535
_WINDOWS_JOB_HANDLE: object | None = None


class MessageImportError(RuntimeError):
    pass


def _bounded_source_bytes(path: Path, *, limit: int | None = None) -> bytes:
    byte_limit = MAX_INPUT_BYTES if limit is None else min(MAX_INPUT_BYTES, limit)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise MessageImportError("That file could not be read.") from error
    if size > byte_limit:
        raise MessageImportError("That file is too large to import.")
    try:
        with path.open("rb") as stream:
            payload = stream.read(byte_limit + 1)
    except OSError as error:
        raise MessageImportError("That file could not be read.") from error
    if len(payload) > byte_limit:
        raise MessageImportError("That file is too large to import.")
    return payload


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return repair_common_mojibake(payload.decode(encoding))
        except UnicodeDecodeError:
            continue
    return repair_common_mojibake(payload.decode("utf-8", errors="ignore"))


def _bounded_output(value: str) -> str:
    if len(value) > MAX_OUTPUT_CHARACTERS:
        raise MessageImportError("That document contains too much text to import.")
    return value


def _append_bounded(parts: list[str], value: str, total: int) -> int:
    updated = total + len(value)
    if updated > MAX_OUTPUT_CHARACTERS:
        raise MessageImportError("That document contains too much text to import.")
    parts.append(value)
    return updated


def _append_escaped_bounded(
    parts: list[str],
    value: str,
    total: int,
    *,
    preserve_line_breaks: bool = False,
) -> int:
    carry = ""
    for offset in range(0, len(value), _TEXT_CHUNK_CHARACTERS):
        chunk = carry + value[offset : offset + _TEXT_CHUNK_CHARACTERS]
        carry = ""
        if (
            preserve_line_breaks
            and offset + _TEXT_CHUNK_CHARACTERS < len(value)
            and chunk.endswith("\r")
        ):
            chunk = chunk[:-1]
            carry = "\r"
        if preserve_line_breaks:
            chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
        rendered = html.escape(chunk)
        if preserve_line_breaks:
            rendered = rendered.replace("\n", "<br>")
        total = _append_bounded(parts, rendered, total)
    if carry:
        total = _append_bounded(parts, "<br>", total)
    return total


def _escaped_text(value: str, *, preserve_line_breaks: bool = False) -> str:
    parts: list[str] = []
    _append_escaped_bounded(
        parts,
        value,
        0,
        preserve_line_breaks=preserve_line_breaks,
    )
    return "".join(parts)


def _preflight_zip_directory(payload: bytes) -> None:
    tail_start = max(0, len(payload) - _ZIP_EOCD_MIN_SIZE - _ZIP_MAX_COMMENT_SIZE)
    tail = payload[tail_start:]
    search_end = len(tail)
    record: tuple[int, int, int, int, int, int, int] | None = None
    while search_end:
        offset = tail.rfind(_ZIP_EOCD_SIGNATURE, 0, search_end)
        if offset < 0:
            break
        if offset + _ZIP_EOCD_MIN_SIZE <= len(tail):
            candidate = struct.unpack_from("<4H2LH", tail, offset + 4)
            comment_length = candidate[-1]
            if offset + _ZIP_EOCD_MIN_SIZE + comment_length == len(tail):
                record = candidate
                break
        search_end = offset
    if record is None:
        raise MessageImportError("That document could not be read.")

    (
        disk_number,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        _comment_length,
    ) = record
    if (
        disk_number != 0
        or directory_disk != 0
        or disk_entries != total_entries
        or total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
        or total_entries > MAX_ARCHIVE_ENTRIES
        or directory_size > len(payload)
        or directory_offset > len(payload) - directory_size
    ):
        raise MessageImportError("That document is too complex to import.")


def _validate_document_archive(payload: bytes) -> None:
    _preflight_zip_directory(payload)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile, NotImplementedError) as error:
        raise MessageImportError("That document could not be read.") from error
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise MessageImportError("That document is too complex to import.")

    expanded_total = 0
    seen: set[str] = set()
    for entry in entries:
        normalized_name = entry.filename.replace("\\", "/")
        relative = PurePosixPath(normalized_name)
        entry_key = normalized_name.casefold()
        entry_mode = int(entry.external_attr) >> 16
        if (
            not normalized_name
            or relative.is_absolute()
            or ".." in relative.parts
            or entry_key in seen
            or entry.flag_bits & 0x1
            or entry.compress_type not in _ZIP_COMPRESSION_METHODS
            or stat.S_ISLNK(entry_mode)
        ):
            raise MessageImportError("That document could not be read.")
        seen.add(entry_key)
        expanded_total += max(0, int(entry.file_size))
        if expanded_total > MAX_ARCHIVE_EXPANDED_BYTES:
            raise MessageImportError("That document is too large to import.")
        compressed = max(1, int(entry.compress_size))
        if entry.file_size > compressed * MAX_ARCHIVE_COMPRESSION_RATIO:
            raise MessageImportError("That document is too complex to import.")


def _extract_docx(payload: bytes) -> str:
    _validate_document_archive(payload)
    if mammoth is not None:
        try:
            result = mammoth.convert_to_html(io.BytesIO(payload))
            return _bounded_output(str(result.value or ""))
        except MessageImportError:
            raise
        except Exception:
            pass
    if Document is not None:
        try:
            document = Document(io.BytesIO(payload))
            parts: list[str] = []
            total = 0
            for paragraph in document.paragraphs:
                text = paragraph.text.strip()
                if text:
                    total = _append_escaped_bounded(parts, text, total)
                    total = _append_bounded(parts, "<br>", total)
            if parts:
                parts.pop()
            return "".join(parts)
        except MessageImportError:
            raise
        except Exception as error:
            raise MessageImportError("That document could not be read.") from error
    raise MessageImportError("DOCX import is not available in this installation.")


def _bounded_zlib_decompress(data: bytes) -> bytes:
    last_error: zlib.error | None = None
    for window_bits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32, -zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(window_bits)
            parts: list[bytes] = []
            total = 0
            for offset in range(0, len(data), 64 * 1024):
                pending = data[offset : offset + 64 * 1024]
                while pending:
                    remaining = MAX_PDF_STREAM_BYTES - total
                    decoded = decoder.decompress(pending, remaining + 1)
                    if len(decoded) > remaining:
                        raise MessageImportError(
                            "That PDF is too complex to import."
                        )
                    if decoded:
                        parts.append(decoded)
                        total += len(decoded)
                    next_pending = decoder.unconsumed_tail
                    if not next_pending:
                        break
                    if next_pending == pending:
                        raise zlib.error("PDF stream decompression stalled")
                    pending = next_pending
            remaining = MAX_PDF_STREAM_BYTES - total
            decoded = decoder.flush(remaining + 1)
            if len(decoded) > remaining:
                raise MessageImportError("That PDF is too complex to import.")
            if decoded:
                parts.append(decoded)
            if not decoder.eof:
                raise zlib.error("incomplete PDF stream")
            return b"".join(parts)
        except MessageImportError:
            raise
        except zlib.error as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise zlib.error("PDF stream could not be decompressed")


def _install_pdf_decompression_limit() -> None:
    try:
        import PyPDF2.filters as pdf_filters  # type: ignore
    except Exception:
        return
    pdf_filters.decompress = _bounded_zlib_decompress


def _extract_pdf(payload: bytes) -> str:
    if PdfReader is not None:
        try:
            _install_pdf_decompression_limit()
            reader = PdfReader(io.BytesIO(payload), strict=False)
            page_count = len(reader.pages)
            if page_count > MAX_PDF_PAGES:
                raise MessageImportError("That PDF has too many pages to import.")
            parts: list[str] = []
            total = 0
            for page in reader.pages:
                if parts:
                    total = _append_bounded(parts, "<br><br>", total)
                total = _append_escaped_bounded(
                    parts,
                    page.extract_text() or "",
                    total,
                    preserve_line_breaks=True,
                )
            return "".join(parts)
        except MessageImportError:
            raise
        except MemoryError as error:
            raise MessageImportError("That PDF is too complex to import.") from error
        except Exception:
            pass

    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(io.BytesIO(payload)) as document:
            if len(document.pages) > MAX_PDF_PAGES:
                raise MessageImportError("That PDF has too many pages to import.")
            parts = []
            total = 0
            for page in document.pages:
                if parts:
                    total = _append_bounded(parts, "<br><br>", total)
                total = _append_escaped_bounded(
                    parts,
                    page.extract_text() or "",
                    total,
                    preserve_line_breaks=True,
                )
            return "".join(parts)
    except MessageImportError:
        raise
    except MemoryError as error:
        raise MessageImportError("That PDF is too complex to import.") from error
    except Exception as error:
        if PdfReader is None:
            raise MessageImportError(
                "PDF import is not available in this installation."
            ) from error
        raise MessageImportError("That PDF could not be read.") from error


def _extract_odt(payload: bytes) -> str:
    _validate_document_archive(payload)
    try:
        from odf.opendocument import load as load_odt  # type: ignore
        from odf.text import P  # type: ignore

        document = load_odt(io.BytesIO(payload))
        parts: list[str] = []
        total = 0
        for paragraph in document.getElementsByType(P):
            paragraph_parts: list[str] = []
            paragraph_total = 0
            for node in paragraph.childNodes:
                value = str(getattr(node, "data", ""))
                paragraph_total += len(value)
                if paragraph_total > MAX_OUTPUT_CHARACTERS:
                    raise MessageImportError(
                        "That document contains too much text to import."
                    )
                paragraph_parts.append(value)
            text = "".join(paragraph_parts).strip()
            if not text:
                continue
            if parts:
                total = _append_bounded(parts, "<br>", total)
            total = _append_escaped_bounded(parts, text, total)
        return "".join(parts)
    except MessageImportError:
        raise
    except Exception as error:
        raise MessageImportError("That document could not be read.") from error


def extract_message_html(path: str | Path) -> str:
    source = Path(path)
    extension = source.suffix.casefold()
    if extension not in _SUPPORTED_EXTENSIONS or not source.is_file():
        raise MessageImportError("That message file is not supported.")
    limit = MAX_TEXT_INPUT_BYTES if extension in {".htm", ".html", ".txt"} else None
    payload = _bounded_source_bytes(source, limit=limit)
    if extension in {".html", ".htm"}:
        return _bounded_output(_decode_text(payload))
    if extension == ".txt":
        return _escaped_text(_decode_text(payload), preserve_line_breaks=True)
    if extension == ".docx":
        return _extract_docx(payload)
    if extension == ".pdf":
        return _extract_pdf(payload)
    if extension == ".odt":
        return _extract_odt(payload)
    raise MessageImportError("That message file is not supported.")


def create_result_path() -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="lettersmith-message-import-",
        suffix=".json",
    )
    os.close(descriptor)
    return Path(raw_path)


def worker_command(source: str | Path, result_path: str | Path) -> tuple[str, list[str]]:
    arguments = [
        MESSAGE_IMPORT_WORKER_FLAG,
        os.path.abspath(os.fspath(source)),
        os.path.abspath(os.fspath(result_path)),
    ]
    if getattr(sys, "frozen", False):
        return sys.executable, arguments
    return sys.executable, [str(Path(__file__).with_name("Main.py")), *arguments]


def _write_worker_result(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        encoded = json.dumps(
            {"ok": False, "error": "That document contains too much text to import."}
        ).encode("utf-8")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("Unsafe import result path")
    flags = os.O_WRONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("Unsafe import result path")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Could not write import result")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove_result_path(path: str | Path, *, attempts: int = 1) -> bool:
    candidate = Path(path)
    for attempt in range(max(1, int(attempts))):
        try:
            candidate.unlink(missing_ok=True)
            return True
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(0.025 * (attempt + 1))
    return False


def _apply_windows_worker_limits() -> None:
    global _WINDOWS_JOB_HANDLE
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise MessageImportError("That file could not be imported safely.")
    information = _ExtendedLimitInformation()
    information.BasicLimitInformation.PerProcessUserTimeLimit = (
        MAX_WORKER_CPU_SECONDS * 10_000_000
    )
    information.BasicLimitInformation.LimitFlags = (
        0x00000002  # JOB_OBJECT_LIMIT_PROCESS_TIME
        | 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    information.ProcessMemoryLimit = MAX_WORKER_MEMORY_BYTES
    if not kernel32.SetInformationJobObject(
        handle,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(information),
        ctypes.sizeof(information),
    ) or not kernel32.AssignProcessToJobObject(
        handle,
        kernel32.GetCurrentProcess(),
    ):
        kernel32.CloseHandle(handle)
        raise MessageImportError("That file could not be imported safely.")
    _WINDOWS_JOB_HANDLE = handle


def _apply_posix_worker_limits() -> None:
    try:
        import resource
    except ImportError as error:
        raise MessageImportError("That file could not be imported safely.") from error

    def apply_limit(resource_id: int, requested: int) -> None:
        soft, hard = resource.getrlimit(resource_id)
        infinity = resource.RLIM_INFINITY
        target = requested
        if hard != infinity:
            target = min(target, int(hard))
        if soft != infinity:
            target = min(target, int(soft))
        if target <= 0:
            raise MessageImportError("That file could not be imported safely.")
        resource.setrlimit(resource_id, (target, hard))

    memory_limit_names = (
        ("RLIMIT_DATA", "RLIMIT_AS")
        if sys.platform == "darwin"
        else ("RLIMIT_AS", "RLIMIT_DATA")
    )
    memory_limited = False
    for limit_name in memory_limit_names:
        memory_limit = getattr(resource, limit_name, None)
        if memory_limit is None:
            continue
        try:
            apply_limit(memory_limit, MAX_WORKER_MEMORY_BYTES)
        except (MessageImportError, OSError, ValueError):
            continue
        memory_limited = True
        break
    if not memory_limited and sys.platform != "darwin":
        raise MessageImportError("That file could not be imported safely.")
    try:
        cpu_limit = getattr(resource, "RLIMIT_CPU", None)
        if cpu_limit is not None:
            apply_limit(cpu_limit, MAX_WORKER_CPU_SECONDS)
    except (MessageImportError, OSError, ValueError) as error:
        if sys.platform == "darwin":
            return
        raise MessageImportError("That file could not be imported safely.") from error


def _apply_worker_resource_limits() -> None:
    if os.name == "nt":
        _apply_windows_worker_limits()
    else:
        _apply_posix_worker_limits()


def run_worker(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 2
    source = Path(arguments[0])
    result_path = Path(arguments[1])
    try:
        _apply_worker_resource_limits()
        value = sanitize_message_html(extract_message_html(source))
        response: dict[str, object] = {"ok": True, "html": value}
    except MessageImportError as error:
        response = {"ok": False, "error": str(error)}
    except BaseException:
        response = {"ok": False, "error": "That file could not be imported."}
    try:
        _write_worker_result(result_path, response)
    except OSError:
        return 3
    return 0


def read_worker_result(path: str | Path) -> str:
    result_path = Path(path)
    try:
        size = result_path.stat().st_size
        if size <= 0 or size > MAX_RESULT_BYTES:
            raise MessageImportError("That file could not be imported.")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MessageImportError("That file could not be imported.") from error
    if not isinstance(payload, dict):
        raise MessageImportError("That file could not be imported.")
    if payload.get("ok") is not True:
        message = str(payload.get("error") or "That file could not be imported.")
        raise MessageImportError(message)
    value = payload.get("html")
    if not isinstance(value, str) or len(value) > MAX_OUTPUT_CHARACTERS:
        raise MessageImportError("That file could not be imported.")
    return value


def import_message_sync(path: str | Path) -> str:
    result_path = create_result_path()
    program, arguments = worker_command(path, result_path)
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        try:
            completed = subprocess.run(
                [program, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=MESSAGE_IMPORT_TIMEOUT_MS / 1000,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as error:
            raise MessageImportError("That file took too long to import.") from error
        if completed.returncode != 0:
            raise MessageImportError("That file could not be imported.")
        return read_worker_result(result_path)
    finally:
        remove_result_path(result_path, attempts=3)


__all__ = [
    "MAX_ARCHIVE_COMPRESSION_RATIO",
    "MAX_ARCHIVE_ENTRIES",
    "MAX_ARCHIVE_EXPANDED_BYTES",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_CHARACTERS",
    "MAX_PDF_PAGES",
    "MAX_PDF_STREAM_BYTES",
    "MAX_RESULT_BYTES",
    "MAX_TEXT_INPUT_BYTES",
    "MAX_WORKER_CPU_SECONDS",
    "MAX_WORKER_MEMORY_BYTES",
    "MESSAGE_IMPORT_TIMEOUT_MS",
    "MESSAGE_IMPORT_WORKER_FLAG",
    "MessageImportError",
    "create_result_path",
    "extract_message_html",
    "import_message_sync",
    "read_worker_result",
    "remove_result_path",
    "run_worker",
    "worker_command",
]
