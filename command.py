# ===============================
# File: command.py
# Purpose: "The Commandment" — project reset + compatibility helpers
# Exports: CommandTab (for Nexus), confirm_and_reset, reset_everything, open_saved_letters
# Framework: PySide6
# ===============================

from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

__all__ = [
    "CommandTab",
    "confirm_and_reset",
    "reset_everything",
    "open_saved_letters",
]

# ─────────────────────────────────────────────────────────────────────────────
# Config integration (safe fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

# We prefer pulling paths from config, but gracefully fall back to literals.
try:
    from config import (
        SETTINGS_FILE,
        GALLERY_DIR,
        SOUNDS_DIR,
        OUTPUT_PLAY_DIR,
        OUTPUT_FILE_DIR,
        OUTPUT_ZIP_DIR,
        ensure_output_dirs,
    )
except Exception:
    SETTINGS_FILE = "settings.json"
    GALLERY_DIR = "gallery"
    SOUNDS_DIR = "sounds"
    OUTPUT_PLAY_DIR = Path("output") / "Play"
    OUTPUT_FILE_DIR = Path("output") / "File"
    OUTPUT_ZIP_DIR = Path("output") / "Zip"

    def ensure_output_dirs(_root: Path) -> None:
        for p in (OUTPUT_PLAY_DIR, OUTPUT_FILE_DIR, OUTPUT_ZIP_DIR):
            (_root / p).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Root resolver
# ─────────────────────────────────────────────────────────────────────────────

def app_root() -> Path:
    """
    Resolve the project root robustly:
      1) Frozen bundle base (PyInstaller) if present
      2) Folder that contains Main.py or Nexus.py (search upward)
      3) Parent of this file
    """
    # 1) PyInstaller bundle directory
    base = getattr(sys, "_MEIPASS", None)
    if base:
        base = Path(base)
        if (base / "Main.py").exists() or (base / "Nexus.py").exists():
            return base

    # 2) Walk upward from this file
    here = Path(__file__).resolve()
    for up in (here.parent, here.parent.parent, here.parent.parent.parent):
        if (up / "Main.py").exists() or (up / "Nexus.py").exists():
            return up

    # 3) Fallback
    return here.parent


# ─────────────────────────────────────────────────────────────────────────────
# Reset policy (whitelists)
# ─────────────────────────────────────────────────────────────────────────────

# Only delete these files inside gallery/
GALLERY_DELETE_FILES = [
    "cover.png",
    "letter.png",
    "wall.png",
    "back.png",
    "message.html",
    "message.png",
    "music.mp3",
]

# Only these directories inside gallery/
GALLERY_DELETE_DIRS = [
    SOUNDS_DIR,  # contains flip 1–10, glissando, etc. (rebuilt as needed)
]

# Output pipeline: clear contents but keep the folders themselves.
OUTPUT_FOLDERS_TO_CLEAR = [
    OUTPUT_PLAY_DIR,
    OUTPUT_FILE_DIR,
    OUTPUT_ZIP_DIR,
]

# Temp/build artifacts safe to remove; recreated by the app as needed.
TEMP_ITEMS_TO_CLEAR = [
    Path("converted64"),
    Path("build_temp"),
    Path("dist_temp"),
    Path("MAX") / "temp",
]
TEMP_FILES_TO_CLEAR = [
    Path("converted64") / "convert64.py",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_clear_dir_contents(dir_path: Path) -> Tuple[int, int]:
    """
    Remove all files and subfolders inside dir_path, keeping dir_path itself.
    Returns (files_deleted, dirs_deleted).
    """
    files_deleted = 0
    dirs_deleted = 0
    if not dir_path.exists() or not dir_path.is_dir():
        return files_deleted, dirs_deleted

    # Iterate defensively; ignore per-entry errors
    for entry in dir_path.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink(missing_ok=True)
                files_deleted += 1
            elif entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                dirs_deleted += 1
        except Exception:
            # Non-fatal; continue
            pass
    return files_deleted, dirs_deleted


def _wipe_gallery(gallery_dir: Path) -> Tuple[int, int]:
    files_deleted = 0
    dirs_deleted = 0

    # Delete whitelisted files
    for name in GALLERY_DELETE_FILES:
        target = gallery_dir / name
        if target.exists() and target.is_file():
            try:
                target.unlink()
                files_deleted += 1
            except Exception:
                pass

    # Delete whitelisted subdirectories (e.g., gallery/sounds/)
    for name in GALLERY_DELETE_DIRS:
        target = gallery_dir / name
        if target.exists() and target.is_dir():
            try:
                shutil.rmtree(target, ignore_errors=True)
                dirs_deleted += 1
            except Exception:
                pass

    return files_deleted, dirs_deleted


def _wipe_outputs(root: Path) -> Tuple[int, int]:
    files_deleted = 0
    dirs_deleted = 0
    # Ensure output folder skeleton exists before clearing
    ensure_output_dirs(root)
    for rel in OUTPUT_FOLDERS_TO_CLEAR:
        target = (root / rel).resolve()
        f, d = _safe_clear_dir_contents(target)
        files_deleted += f
        dirs_deleted += d
    return files_deleted, dirs_deleted


def _wipe_temps(root: Path) -> Tuple[int, int]:
    files_deleted = 0
    dirs_deleted = 0

    # Remove listed directories/files if present
    for rel in TEMP_ITEMS_TO_CLEAR:
        target = (root / rel).resolve()
        if target.exists():
            if target.is_dir():
                try:
                    shutil.rmtree(target, ignore_errors=True)
                    dirs_deleted += 1
                except Exception:
                    pass
            else:
                try:
                    target.unlink(missing_ok=True)
                    files_deleted += 1
                except Exception:
                    pass

    for rel in TEMP_FILES_TO_CLEAR:
        target = (root / rel).resolve()
        if target.exists() and target.is_file():
            try:
                target.unlink(missing_ok=True)
                files_deleted += 1
            except Exception:
                pass

    return files_deleted, dirs_deleted


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def reset_everything(*, parent: Optional[QtWidgets.QWidget] = None) -> Tuple[int, int]:
    """
    Wipe all progress artifacts according to the policy above.
    Returns (files_deleted, dirs_deleted).
    """
    root = app_root()
    total_files = 0
    total_dirs = 0

    # 1) Gallery (whitelist-based)
    gallery = root / GALLERY_DIR
    f, d = _wipe_gallery(gallery)
    total_files += f
    total_dirs += d

    # 2) Output pipeline (clear contents, keep folders)
    f, d = _wipe_outputs(root)
    total_files += f
    total_dirs += d

    # 3) Temp/build artifacts
    f, d = _wipe_temps(root)
    total_files += f
    total_dirs += d

    return total_files, total_dirs


def confirm_and_reset(parent: Optional[QtWidgets.QWidget] = None) -> None:
    """
    Frameless, minimal confirmation dialog.
    On "Yes": perform reset and show a brief toast. On "No": close silently.
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
    dlg.setModal(True)
    dlg.setAttribute(Qt.WA_TranslucentBackground, True)
    dlg.setObjectName("CommandmentDialog")

    # ── Styling ─────────────────────────────────────────────────────────────
    container = QtWidgets.QFrame()
    container.setObjectName("container")
    container.setStyleSheet("""
        QFrame#container {
            background-color: #1b1b1d;
            border: 1px solid #2b2b2e;
            border-radius: 10px;
        }
        QLabel#title {
            color: #eaeaf0;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton {
            background: #2a2a2e;
            border: 1px solid #3a3a3f;
            border-radius: 8px;
            padding: 6px 14px;
            color: #e2e2e8;
        }
        QPushButton:hover { border-color: #56565f; }
        QPushButton:pressed { background: #222226; }
        QPushButton#danger {
            background: #3a1f23;
            border-color: #6b2a31;
            color: #ffdee2;
        }
        QPushButton#danger:hover { background: #4a272c; }
    """)

    v = QtWidgets.QVBoxLayout(container)
    v.setContentsMargins(16, 16, 16, 12)
    v.setSpacing(10)

    label = QtWidgets.QLabel("Are you sure? This will erase everything.")
    label.setObjectName("title")
    label.setWordWrap(True)
    v.addWidget(label)

    btns = QtWidgets.QHBoxLayout()
    btns.setSpacing(10)
    btn_no = QtWidgets.QPushButton("No")
    btn_yes = QtWidgets.QPushButton("Yes")
    btn_yes.setObjectName("danger")
    btn_no.setAutoDefault(True)   # Default focus on “No” to reduce accidents
    btns.addStretch(1)
    btns.addWidget(btn_no)
    btns.addWidget(btn_yes)
    v.addLayout(btns)

    root_layout = QtWidgets.QVBoxLayout(dlg)
    root_layout.setContentsMargins(6, 6, 6, 6)
    root_layout.addWidget(container)

    # ── Wiring & keys ───────────────────────────────────────────────────────
    btn_no.clicked.connect(dlg.reject)
    btn_yes.clicked.connect(dlg.accept)

    # Escape → No, Enter → Yes
    dlg.reject = lambda: QtWidgets.QDialog.reject(dlg)  # type: ignore
    dlg.accept = lambda: QtWidgets.QDialog.accept(dlg)  # type: ignore
    dlg.setTabOrder(btn_no, btn_yes)

    # Size/position small and centered over parent
    dlg.resize(380, 120)
    if parent is not None:
        cp = parent.mapToGlobal(parent.rect().center())
        dlg.move(cp.x() - dlg.width() // 2, cp.y() - dlg.height() // 2)

    if dlg.exec() == QtWidgets.QDialog.Accepted:
        files, dirs = reset_everything(parent=parent)
        _toast(parent, f"Reset complete — removed {files} files, {dirs} folders.")
    else:
        # No-op
        pass


def _toast(parent: Optional[QtWidgets.QWidget], text: str, msecs: int = 1800) -> None:
    """
    Simple ephemeral toast using a frameless, auto-closing dialog.
    """
    tip = QtWidgets.QDialog(parent)
    tip.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip)
    tip.setAttribute(Qt.WA_TranslucentBackground, True)

    body = QtWidgets.QFrame()
    body.setObjectName("toast")
    body.setStyleSheet("""
        QFrame#toast {
            background-color: #17171a;
            border: 1px solid #2c2c31;
            border-radius: 8px;
        }
        QLabel {
            color: #eaeaf0;
            padding: 8px 12px;
        }
    """)
    lbl = QtWidgets.QLabel(text)
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.addWidget(lbl)

    outer = QtWidgets.QVBoxLayout(tip)
    outer.setContentsMargins(6, 6, 6, 6)
    outer.addWidget(body)

    # Position near bottom-right of parent (or screen)
    if parent is not None:
        pos = parent.mapToGlobal(parent.rect().bottomRight())
    else:
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        pos = scr.bottomRight()

    tip.adjustSize()
    tip.move(pos.x() - tip.width() - 24, pos.y() - tip.height() - 24)

    QtCore.QTimer.singleShot(msecs, tip.close)
    tip.show()


def open_saved_letters() -> None:
    """
    Legacy helper kept for compatibility with older Forge_Tab.py.
    Opens output/File in the OS file explorer.
    """
    root = app_root()
    target = root / OUTPUT_FILE_DIR if isinstance(OUTPUT_FILE_DIR, Path) else root / Path(OUTPUT_FILE_DIR)
    target.mkdir(parents=True, exist_ok=True)

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform.startswith("darwin"):
            import subprocess
            subprocess.run(["open", str(target)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception:
        # Best-effort fallback
        QtWidgets.QDesktopWidget()  # ensure QApplication exists if possible
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# UI: CommandTab (for Nexus import)
# ─────────────────────────────────────────────────────────────────────────────

class CommandTab(QtWidgets.QWidget):
    """
    Minimal, professional Command tab that shows a short explanation and a single
    “Commandment” button. Clicking it opens the frameless confirmation dialog and,
    on Yes, wipes progress (gallery whitelist, outputs, temps).
    """
    def __init__(self, project_root: Path, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project_root = Path(project_root)

        self.setObjectName("CommandTab")
        self.setStyleSheet("""
        QWidget#CommandTab {
            background-color: #111113;
        }
        QLabel#title {
            color: #eaeaf0;
            font-size: 14px;
            font-weight: 600;
        }
        QLabel#desc {
            color: #b9bac4;
        }
        QPushButton {
            background: #232327;
            border: 1px solid #37373d;
            border-radius: 10px;
            color: #e8e8ee;
            padding: 10px 14px;
            font-weight: 600;
        }
        QPushButton:hover { border-color: #54545c; }
        QPushButton:pressed { background: #1d1d21; }
        QPushButton#danger {
            background: #3a1f23;
            border-color: #6b2a31;
            color: #ffdde2;
        }
        QPushButton#danger:hover { background: #4a272c; }
        """)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        title = QtWidgets.QLabel("The Commandment")
        title.setObjectName("title")
        desc = QtWidgets.QLabel(
            "Reset the project’s progress artifacts. This erases gallery images (whitelist), "
            "output folders’ contents, and temp/build caches. Source code and icons are untouched."
        )
        desc.setObjectName("desc")
        desc.setWordWrap(True)

        v.addWidget(title)
        v.addWidget(desc)

        btn = QtWidgets.QPushButton("Commandment — Reset Now")
        btn.setObjectName("danger")
        btn.setFixedHeight(40)
        btn.clicked.connect(lambda: confirm_and_reset(self))

        v.addSpacing(6)
        v.addWidget(btn, 0, Qt.AlignLeft)
        v.addStretch(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint (optional)
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Running this module directly will show the confirmation dialog and perform reset.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    confirm_and_reset(None)
    QtCore.QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()