from __future__ import annotations

import importlib.util
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from transactional_io import cleanup_abandoned_temp_files


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupCheckIssue:
    message: str
    critical: bool = True


def _writable_directory(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".lettersmith-write-check-{os.getpid()}-{time.time_ns()}.tmp"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            _LOGGER.exception("Startup write-check cleanup failed: %s", probe)
        return False


def run_startup_self_check(project_root: str | Path) -> tuple[StartupCheckIssue, ...]:
    root = Path(project_root).resolve()
    issues: list[StartupCheckIssue] = []
    for relative in ("Template.py", "styles.css", "gallery/app/icons"):
        expected = root / relative
        if not expected.exists():
            issues.append(
                StartupCheckIssue(
                    f"Required application resource is missing: {expected}"
                )
            )
    for relative in ("gallery/user", "output"):
        directory = root / relative
        try:
            writable = _writable_directory(directory)
        except OSError:
            writable = False
        if not writable:
            issues.append(
                StartupCheckIssue(
                    f"Letter Smith cannot write to its user directory: {directory}"
                )
            )
    try:
        webengine_available = (
            importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
        )
    except (ImportError, ModuleNotFoundError):
        webengine_available = False
    if not webengine_available:
        issues.append(
            StartupCheckIssue(
                "Qt WebEngine is unavailable. Install PySide6-Addons to enable previews."
            )
        )
    try:
        removed = list(
            cleanup_abandoned_temp_files(
                (root / "gallery" / "user", root / "output")
            )
        )
        removed.extend(cleanup_abandoned_temp_files((root,), recursive=False))
        if removed:
            _LOGGER.info("Removed %d abandoned temporary file(s).", len(removed))
    except OSError:
        _LOGGER.exception("Startup temporary-file cleanup failed.")
    return tuple(issues)


__all__ = ["StartupCheckIssue", "run_startup_self_check"]
