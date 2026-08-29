from __future__ import annotations

import os
import sys
from pathlib import Path


if sys.platform == "win32":
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    dll_directories = tuple(
        path
        for path in (
            bundle_root / "PySide6",
            bundle_root / "shiboken6",
        )
        if path.is_dir()
    )
    sys._lettersmith_dll_directory_handles = tuple(
        os.add_dll_directory(os.fspath(path)) for path in dll_directories
    )
    existing_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(
        [*(os.fspath(path) for path in dll_directories), existing_path]
    )
