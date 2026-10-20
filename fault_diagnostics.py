"""Process-lifetime native fault output, available before Qt is imported."""

from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


_fault_fd: int | None = None
_fault_path: Path | None = None


def enable_fault_logging(log_directory: Path) -> Path | None:
    """Keep a dedicated descriptor open through Qt and interpreter teardown."""
    global _fault_fd, _fault_path
    if _fault_fd is not None:
        return _fault_path
    descriptor: int | None = None
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(
            prefix=f"native-fault-{os.getpid()}-", suffix=".log", dir=log_directory,
        )
        faulthandler.enable(file=descriptor, all_threads=True)
    except (OSError, RuntimeError, ValueError) as error:
        if descriptor is not None:
            os.close(descriptor)
        # Keep -X faulthandler's existing handler if file setup failed.
        try:
            if not faulthandler.is_enabled():
                faulthandler.enable(all_threads=True)
        except (OSError, RuntimeError, ValueError):
            pass
        if sys.stderr is not None:
            try:
                print(
                    f"[Faults] Native fault file unavailable ({type(error).__name__}); "
                    "use -X faulthandler and capture terminal output.",
                    file=sys.stderr, flush=True,
                )
            except (OSError, ValueError):
                pass
        return None

    # An integer descriptor has no Python finalizer. The OS closes it on exit;
    # logging.shutdown(), rotation, and late Qt destruction cannot close it.
    _fault_fd = descriptor
    _fault_path = Path(filename)
    record_fault_context(
        f"Python {sys.version.split()[0]} | platform={sys.platform} | pid={os.getpid()}"
    )
    return _fault_path


def record_fault_context(context: str) -> None:
    """Append a known lifecycle/version label; never pass user data or secrets."""
    if _fault_fd is None:
        return
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    label = str(context).replace("\r", " ").replace("\n", " ")[:400]
    try:
        os.write(_fault_fd, f"{timestamp} {label}\n".encode("utf-8"))
    except OSError:
        pass
