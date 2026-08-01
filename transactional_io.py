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


DirectoryValidator = Callable[[Path], bool | None]
_LOGGER = logging.getLogger(__name__)
_DIRECTORY_REPLACE_TIMEOUT_SECONDS = 2.0
_PATH_REMOVE_TIMEOUT_SECONDS = 2.0
_TRANSIENT_RETRY_DELAYS = (0.0, 0.05, 0.1, 0.2, 0.4, 0.8)


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
    return target.with_name(
        f".{target.name}.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
    )


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
        _with_transient_retry(lambda: os.replace(temporary, target))
    finally:
        _remove_path(temporary)
    return target


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
        _with_transient_retry(lambda: os.replace(temporary, target))
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
    "atomic_write_bytes",
    "atomic_write_json",
    "safe_write_json",
    "atomic_write_text",
    "cleanup_abandoned_staging",
    "create_staging_directory",
    "recover_stale_transactions",
    "replace_directory",
    "validate_directory",
]
