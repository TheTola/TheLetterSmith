from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path

from project_paths import application_paths


EDITOR_ERROR_LOG = "editor_error.log"
_MAX_LOG_BYTES = 1_000_000


def record_editor_failure(project_root: Path, operation: str, error: BaseException) -> Path:
    """Append an editor failure to the bounded application diagnostic directory."""
    log_path = application_paths(project_root).logs_root / EDITOR_ERROR_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.is_file() and log_path.stat().st_size >= _MAX_LOG_BYTES:
        previous_path = log_path.with_suffix(".previous.log")
        previous_path.unlink(missing_ok=True)
        log_path.replace(previous_path)

    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"[{timestamp}] {operation}\n{trace}\n")
    return log_path
