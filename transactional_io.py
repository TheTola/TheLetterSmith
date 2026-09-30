from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _WindowsFileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    _WINDOWS_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WINDOWS_KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _WINDOWS_KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _WINDOWS_KERNEL32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _WINDOWS_KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _WINDOWS_KERNEL32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _WINDOWS_KERNEL32.GetFileAttributesW.restype = wintypes.DWORD
    _WINDOWS_KERNEL32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _WINDOWS_KERNEL32.SetFileAttributesW.restype = wintypes.BOOL
    _WINDOWS_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _WINDOWS_KERNEL32.CloseHandle.restype = wintypes.BOOL


DirectoryValidator = Callable[[Path], bool | None]
_LOGGER = logging.getLogger(__name__)
_DIRECTORY_REPLACE_TIMEOUT_SECONDS = 2.0
_PATH_REMOVE_TIMEOUT_SECONDS = 2.0
_TRANSIENT_RETRY_DELAYS = (0.0, 0.05, 0.1, 0.2, 0.4, 0.8)
_INTERNAL_METADATA_FILENAMES = frozenset(
    {
        ".lettersmith-snapshot-manifest.json",
        "lettersmith-build.json",
        "lettersmith-images.json",
        "lettersmith-metadata.json",
        "lettersmith-publication.json",
        "lettersmith-sound.json",
        "project_sound.json",
        "prompt_writer_state.json",
    }
)
_INTERNAL_INVALID_PREFIXES = (
    "lettersmith-images.invalid.",
    "project_sound.invalid.",
    "prompt_writer_state.invalid.",
    "settings.invalid.",
)


def _is_transient_windows_error(error: BaseException) -> bool:
    if isinstance(error, PermissionError):
        return True
    return (
        isinstance(error, OSError)
        and getattr(error, "winerror", None) in {5, 32}
    )


def _with_transient_retry(operation: Callable[[], Any]) -> Any:
    for attempt, delay in enumerate(_TRANSIENT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return operation()
        except OSError as error:
            if not _is_transient_windows_error(error) or attempt == len(_TRANSIENT_RETRY_DELAYS) - 1:
                raise
    raise RuntimeError("Transient operation retry exhausted.")


def _temporary_path(target: Path) -> Path:
    # Keep nested generated assets below the Windows path-length limit.
    return target.with_name(f".tmp.{uuid.uuid4().hex}")


def file_change_token(
    path: str | Path,
    *,
    stat_result: os.stat_result | None = None,
) -> int:
    """Return a metadata token that changes for in-place content edits."""
    source = Path(path)
    current = stat_result if stat_result is not None else source.stat()
    if os.name != "nt":
        return int(current.st_ctime_ns)

    handle = _WINDOWS_KERNEL32.CreateFileW(
        str(source),
        0x0080,  # FILE_READ_ATTRIBUTES
        0x0001 | 0x0002 | 0x0004,  # share read, write, and delete
        None,
        3,  # OPEN_EXISTING
        0x0080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        return int(current.st_ctime_ns)
    try:
        info = _WindowsFileBasicInfo()
        if not _WINDOWS_KERNEL32.GetFileInformationByHandleEx(
            handle,
            0,  # FileBasicInfo
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            return int(current.st_ctime_ns)
        return int(info.ChangeTime)
    finally:
        _WINDOWS_KERNEL32.CloseHandle(handle)


def set_path_hidden(path: str | Path, hidden: bool = True) -> bool:
    """Set or clear the Windows hidden attribute without changing other flags."""
    target = Path(path)
    if os.name != "nt" or not target.exists():
        return False

    attributes = int(_WINDOWS_KERNEL32.GetFileAttributesW(str(target)))
    if attributes == 0xFFFFFFFF:
        return False
    hidden_flag = 0x00000002
    updated = (
        attributes | hidden_flag
        if hidden
        else attributes & ~hidden_flag
    )
    if updated == attributes:
        return True
    if updated == 0:
        updated = 0x00000080  # FILE_ATTRIBUTE_NORMAL
    if not _WINDOWS_KERNEL32.SetFileAttributesW(str(target), updated):
        _LOGGER.warning(
            "Could not update hidden attribute for %s (Windows error %s).",
            target,
            ctypes.get_last_error(),
        )
        return False
    return True


def is_internal_metadata_path(path: str | Path) -> bool:
    candidate = Path(path)
    name = candidate.name.casefold()
    parent_name = candidate.parent.name.casefold()
    if name in _INTERNAL_METADATA_FILENAMES:
        return True
    if name.startswith(_INTERNAL_INVALID_PREFIXES):
        return True
    if name.endswith(".analysis.json") and parent_name == "analysis":
        return True
    if name == "current.json" and parent_name == "appssong":
        return True
    if name == "library.json" and parent_name in {"appssong", "music archive"}:
        return True
    if name == "settings.json" and parent_name in {"active project", "settings"}:
        return True
    return name.startswith("storage-layout-") and parent_name == "migrations"


def _is_viewer_controls_directory(path: Path) -> bool:
    if path.name.casefold() != "controls":
        return False
    parts = tuple(part.casefold() for part in path.parts)
    return (
        len(parts) >= 4
        and parts[-4:] == ("gallery", "user", "card", "controls")
    ) or (
        len(parts) >= 2
        and parts[-2:] == ("gallery", "controls")
    )


def enforce_internal_tree_visibility(root: str | Path) -> tuple[Path, ...]:
    """Hide Letter Smith implementation files without hiding user content."""
    directory = Path(root)
    if os.name != "nt" or not directory.is_dir():
        return ()
    hidden_paths: list[Path] = []
    for candidate in directory.rglob("*"):
        if candidate.is_symlink():
            continue
        should_hide = (
            candidate.is_file() and is_internal_metadata_path(candidate)
        ) or (
            candidate.is_dir() and _is_viewer_controls_directory(candidate)
        )
        if should_hide and set_path_hidden(candidate):
            hidden_paths.append(candidate)
    return tuple(hidden_paths)


def _path_is_hidden(path: Path) -> bool:
    if os.name != "nt" or not path.exists():
        return False
    attributes = int(_WINDOWS_KERNEL32.GetFileAttributesW(str(path)))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x00000002)


def _replace_file_path(
    source: Path,
    destination: Path,
    *,
    hidden: bool = False,
) -> None:
    destination_was_hidden = _path_is_hidden(destination)
    should_hide = (
        hidden
        or destination_was_hidden
        or is_internal_metadata_path(destination)
    )
    set_path_hidden(destination, False)
    try:
        _with_transient_retry(lambda: os.replace(source, destination))
    except Exception:
        if destination_was_hidden:
            set_path_hidden(destination)
        raise
    if should_hide:
        set_path_hidden(destination)


def _replace_directory_path(source: Path, destination: Path) -> None:
    """Retry brief Windows sharing violations while viewers release files."""
    _with_transient_retry(lambda: os.replace(source, destination))


def atomic_write_bytes(path: str | Path, value: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(target)
    try:
        with temporary.open("wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_file_path(temporary, target)
    finally:
        _remove_path(temporary)
    return target


def atomic_copy_file(source: str | Path, destination: str | Path) -> Path:
    """Stream one file through a sibling temporary before replacement."""
    source_path = Path(source).resolve()
    target = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")
    source_stat = source_path.stat()
    source_hidden = _path_is_hidden(source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(target)
    try:
        with source_path.open("rb") as read_stream, temporary.open("wb") as write_stream:
            shutil.copyfileobj(read_stream, write_stream, length=1024 * 1024)
            write_stream.flush()
            os.fsync(write_stream.fileno())
        # Preserve timestamps without carrying a packaged source's Windows
        # read-only attribute onto a generated file that must be replaceable.
        os.utime(
            temporary,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
        )
        _replace_file_path(temporary, target, hidden=source_hidden)
    finally:
        _remove_path(temporary)
    return target


def is_link_or_reparse_point(path: str | Path) -> bool:
    """Return True for links and path-redirecting Windows reparse points."""
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            return True
        is_junction = getattr(candidate, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        path_stat = candidate.lstat()
        attributes = int(getattr(path_stat, "st_file_attributes", 0))
    except OSError:
        return False
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400))
    if not attributes & reparse_flag:
        return False
    reparse_tag = int(getattr(path_stat, "st_reparse_tag", 0))
    if not reparse_tag:
        return True
    # Name-surrogate tags redirect path resolution (for example symlinks and
    # mount points). Data-only tags include OneDrive Files-On-Demand markers
    # and remain subject to the resolved-containment checks during copying.
    return bool(reparse_tag & 0x20000000)


def copy_directory_tree_no_links(
    source: str | Path,
    destination: str | Path,
) -> Path:
    """Copy a regular directory tree while rejecting every linked entry."""
    source_path = Path(source)
    target_root = Path(destination)
    if is_link_or_reparse_point(source_path):
        raise ValueError(f"Linked directories are not supported: {source_path}")
    try:
        source_root = source_path.resolve(strict=True)
    except OSError as error:
        raise FileNotFoundError(f"Source directory does not exist: {source_path}") from error
    if not source_root.is_dir():
        raise NotADirectoryError(f"Source directory does not exist: {source_path}")
    if target_root.exists() or target_root.is_symlink():
        raise FileExistsError(f"Copy destination already exists: {target_root}")
    target_root.mkdir(parents=True)

    pending: list[tuple[Path, Path]] = [(source_root, target_root)]
    while pending:
        current_source, current_target = pending.pop()
        for child in sorted(
            current_source.iterdir(),
            key=lambda item: item.name.casefold(),
        ):
            if is_link_or_reparse_point(child):
                raise ValueError(f"Linked saved-letter content is not supported: {child}")
            try:
                child_stat = child.lstat()
                resolved = child.resolve(strict=True)
                resolved.relative_to(source_root)
            except (OSError, ValueError) as error:
                raise ValueError(f"Source path escaped its directory: {child}") from error
            target = current_target / child.name
            if stat.S_ISDIR(child_stat.st_mode):
                target.mkdir()
                pending.append((child, target))
            elif stat.S_ISREG(child_stat.st_mode):
                atomic_copy_file(child, target)
            else:
                raise ValueError(f"Special files are not supported: {child}")
    return target_root


def atomic_write_text(
    path: str | Path,
    value: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    return atomic_write_bytes(path, value.encode(encoding))


def atomic_write_json(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    indent: int = 2,
) -> Path:
    payload = json.dumps(dict(value), indent=indent, ensure_ascii=False) + "\n"
    return atomic_write_text(path, payload)


def safe_write_json(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    validator: Optional[Callable[[Mapping[str, Any]], None]] = None,
    indent: int = 2,
) -> Path:
    """Validate JSON from a private staging file before replacing ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(target)
    payload = json.dumps(dict(value), indent=indent, ensure_ascii=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        parsed = json.loads(temporary.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("validated JSON must contain an object")
        if validator is not None:
            validator(parsed)
        _replace_file_path(temporary, target)
    finally:
        _remove_path(temporary)
    return target


def create_staging_directory(
    parent: str | Path,
    *,
    prefix: str = ".lettersmith-staging-",
) -> Path:
    parent_path = Path(parent).resolve()
    parent_path.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent_path)).resolve()


def _safe_child(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


def _remove_path(path: Path) -> None:
    def remove() -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, onexc=_remove_readonly_and_retry)
        elif path.exists() or path.is_symlink():
            path.unlink()

    _with_transient_retry(remove)


def _remove_readonly_and_retry(
    function: Callable[..., Any],
    path: str,
    error: BaseException,
) -> None:
    if not isinstance(error, PermissionError):
        raise error
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    function(path)


def validate_directory(
    directory: str | Path,
    *,
    required_paths: Iterable[str | Path] = (),
    validator: Optional[DirectoryValidator] = None,
) -> Path:
    root = Path(directory).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Directory does not exist: {root}")

    missing: list[str] = []
    for relative in required_paths:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Required path must remain inside the directory: {relative}")
        if not (root / relative_path).exists():
            missing.append(relative_path.as_posix())
    if missing:
        raise ValueError("Directory is missing required paths: " + ", ".join(missing))

    if validator is not None and validator(root) is False:
        raise ValueError(f"Directory validation failed: {root}")
    return root


def cleanup_abandoned_staging(
    parent: str | Path,
    *,
    prefix: str = ".lettersmith-staging-",
    older_than_seconds: float = 24 * 60 * 60,
    now: Optional[float] = None,
) -> tuple[Path, ...]:
    if not prefix:
        raise ValueError("A non-empty staging prefix is required.")
    parent_path = Path(parent).resolve()
    if not parent_path.is_dir():
        return ()

    cutoff = (time.time() if now is None else now) - max(0.0, older_than_seconds)
    removed: list[Path] = []
    for candidate in sorted(parent_path.iterdir(), key=lambda path: path.name.casefold()):
        resolved = candidate.resolve()
        if (
            not candidate.name.startswith(prefix)
            or not candidate.is_dir()
            or not _safe_child(parent_path, resolved)
        ):
            continue
        try:
            modified = candidate.stat().st_mtime
        except OSError:
            continue
        if modified > cutoff:
            continue
        _remove_path(candidate)
        removed.append(resolved)
    return tuple(removed)


def cleanup_abandoned_temp_files(
    roots: Iterable[str | Path],
    *,
    older_than_seconds: float = 24 * 60 * 60,
    now: Optional[float] = None,
    recursive: bool = True,
) -> tuple[Path, ...]:
    """Remove only stale Letter Smith-style temporary files from known roots."""
    cutoff = (time.time() if now is None else now) - max(0.0, older_than_seconds)
    removed: list[Path] = []
    visited: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root).resolve()
        if root in visited or not root.is_dir():
            continue
        visited.add(root)
        iterator = root.rglob("*") if recursive else root.iterdir()
        candidates = (path for path in iterator if path.is_file())
        for candidate in candidates:
            name = candidate.name.casefold()
            if ".tmp" not in name:
                continue
            resolved = candidate.resolve()
            if not _safe_child(root, resolved):
                continue
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
                candidate.unlink()
                removed.append(resolved)
                _LOGGER.info("Removed abandoned temporary file: %s", resolved)
            except OSError:
                _LOGGER.exception("Could not remove abandoned temporary file: %s", resolved)
    return tuple(removed)


def recover_stale_transactions(
    destinations: Iterable[str | Path],
    *,
    older_than_seconds: float = 24 * 60 * 60,
    now: Optional[float] = None,
) -> tuple[Path, ...]:
    """Conservatively recover abandoned load transactions.

    A backup is restored only when its normal destination is missing. Existing
    destinations are left untouched; this avoids deleting an unknown user's
    data merely because a transaction sidecar remains after a crash.
    """
    cutoff = (time.time() if now is None else now) - max(0.0, older_than_seconds)
    recovered: list[Path] = []
    for raw_destination in destinations:
        destination = Path(raw_destination).resolve()
        backup = destination.with_name(destination.name + ".load-backup")
        if backup.exists():
            if not destination.exists():
                try:
                    _replace_directory_path(backup, destination)
                    recovered.append(destination)
                    _LOGGER.info("Recovered abandoned transaction: %s", destination)
                except OSError:
                    _LOGGER.exception("Could not recover transaction backup: %s", backup)
            else:
                _LOGGER.info("Retained transaction backup because destination is valid: %s", backup)

        pattern = destination.name + ".load-staging.*"
        for staging in sorted(destination.parent.glob(pattern)):
            if not staging.exists():
                continue
            try:
                if staging.stat().st_mtime > cutoff:
                    continue
                _remove_path(staging)
                _LOGGER.info("Removed stale transaction staging: %s", staging)
            except OSError:
                _LOGGER.exception("Could not remove stale transaction staging: %s", staging)
    return tuple(recovered)


class PathTransaction:
    """Replace one file or directory through sibling staging and rollback paths."""

    def __init__(
        self,
        final_path: str | Path,
        *,
        staging_suffix: str = ".staging",
        backup_suffix: str = ".backup",
        unique_staging: bool = False,
    ) -> None:
        self.final_path = Path(final_path).resolve()
        unique_suffix = f".{uuid.uuid4().hex}" if unique_staging else ""
        self.staging_path = self.final_path.with_name(
            self.final_path.name + staging_suffix + unique_suffix
        )
        self.backup_path = self.final_path.with_name(self.final_path.name + backup_suffix)
        self._committed = False
        self._validate_paths()

    def _validate_paths(self) -> None:
        parent = self.final_path.parent
        if not self.final_path.name or parent == self.final_path:
            raise ValueError(f"Unsafe transaction target: {self.final_path}")
        for path in (self.staging_path, self.backup_path):
            if path.parent != parent or path == self.final_path:
                raise ValueError(f"Transaction path escaped target parent: {path}")

    def prepare(self) -> Path:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        if self.backup_path.exists():
            if self.final_path.exists():
                _remove_path(self.backup_path)
            else:
                _replace_directory_path(
                    self.backup_path,
                    self.final_path,
                )
        _remove_path(self.staging_path)
        self._committed = False
        return self.staging_path

    def commit(
        self,
        *,
        replace: bool = True,
        keep_backup: bool = False,
        validator: Optional[DirectoryValidator] = None,
    ) -> None:
        if replace and not self.staging_path.exists():
            raise FileNotFoundError(f"Transaction staging path is missing: {self.staging_path}")
        if replace and validator is not None:
            validate_directory(self.staging_path, validator=validator)

        _remove_path(self.backup_path)
        if self.final_path.exists():
            _replace_directory_path(
                self.final_path,
                self.backup_path,
            )

        try:
            if replace:
                _replace_directory_path(
                    self.staging_path,
                    self.final_path,
                )
            self._committed = True
        except Exception:
            if self.backup_path.exists() and not self.final_path.exists():
                _replace_directory_path(
                    self.backup_path,
                    self.final_path,
                )
            raise

        if not keep_backup:
            self.finalize()

    def rollback(self) -> None:
        if self._committed and self.final_path.exists():
            _remove_path(self.final_path)
        if self.backup_path.exists():
            _replace_directory_path(
                self.backup_path,
                self.final_path,
            )
        self._committed = False
        _remove_path(self.staging_path)

    def finalize(self) -> None:
        _remove_path(self.backup_path)
        _remove_path(self.staging_path)
        self._committed = False

    def abort(self) -> None:
        if self._committed:
            self.rollback()
        else:
            _remove_path(self.staging_path)


def replace_directory(
    staging_directory: str | Path,
    destination: str | Path,
    *,
    validator: Optional[DirectoryValidator] = None,
) -> Path:
    source = validate_directory(staging_directory, validator=validator)
    transaction = PathTransaction(destination)
    if source == transaction.staging_path:
        raise ValueError("Use an external staging directory for directory replacement.")
    prepared = transaction.prepare()
    shutil.copytree(source, prepared)
    try:
        transaction.commit(keep_backup=True, validator=validator)
    except Exception:
        transaction.abort()
        raise
    transaction.finalize()
    return transaction.final_path


__all__ = [
    "PathTransaction",
    "atomic_copy_file",
    "atomic_write_bytes",
    "atomic_write_json",
    "safe_write_json",
    "atomic_write_text",
    "cleanup_abandoned_staging",
    "cleanup_abandoned_temp_files",
    "copy_directory_tree_no_links",
    "create_staging_directory",
    "enforce_internal_tree_visibility",
    "file_change_token",
    "is_internal_metadata_path",
    "is_link_or_reparse_point",
    "recover_stale_transactions",
    "replace_directory",
    "set_path_hidden",
    "validate_directory",
]
