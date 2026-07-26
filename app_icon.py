from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


APP_ICON_PNG_REL = Path("gallery/app/icons/folder/LSmith.png")
APP_ICON_ICO_REL = Path("gallery/app/icons/folder/LSmith.ico")
APP_USER_MODEL_ID = "InfiniWorks.LetterSmith"


def canonical_icon_paths(project_root: str | Path) -> tuple[Path, Path]:
    root = Path(project_root).resolve()
    return root / APP_ICON_PNG_REL, root / APP_ICON_ICO_REL


def resolve_app_icon(
    project_root: str | Path,
    override: str | Path | None = None,
    *,
    prefer_png: bool = True,
) -> Optional[Path]:
    root = Path(project_root).resolve()

    if override:
        path = Path(override)
        if not path.is_absolute():
            path = (root / path).resolve()
        if path.is_file():
            return path

    png_path, ico_path = canonical_icon_paths(root)
    candidates = (
        (png_path, ico_path)
        if prefer_png
        else (ico_path, png_path)
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def build_qt_icon(icon_path: str | Path | None):
    from PySide6 import QtGui

    if not icon_path:
        return QtGui.QIcon()

    path = Path(icon_path)
    if not path.is_file():
        return QtGui.QIcon()
    return QtGui.QIcon(str(path))


def apply_qt_window_icon(
    window,
    project_root: str | Path,
    *,
    fallback_path: str | Path | None = None,
) -> bool:
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    icon = app.windowIcon() if app is not None else None
    if icon is None or icon.isNull():
        icon = build_qt_icon(fallback_path or resolve_app_icon(project_root, prefer_png=True))
    if icon.isNull():
        return False
    window.setWindowIcon(icon)
    return True


def configure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        return False
