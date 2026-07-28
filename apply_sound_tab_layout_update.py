#!/usr/bin/env python3
"""Install the responsive Sound-tab layout update.

Usage:
    python apply_sound_tab_layout_update.py
    python apply_sound_tab_layout_update.py C:\\path\\to\\LetterSmith

Place this script beside the supplied sound_tab.py. It will:
1. Back up and replace the project's sound_tab.py.
2. Patch Nexus.py so the Sound preview becomes shorter only when the app is
   running in a normal, non-maximized window.
3. Leave maximized/full-screen preview sizing unchanged.
"""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import sys
import tempfile


TAB_CHANGE_ANCHOR = '''        self.preview_caption.setVisible(False)

        # Hide preview only on "Command" tab (index 4)
'''

TAB_CHANGE_REPLACEMENT = '''        self.preview_caption.setVisible(False)

        # Recalculate the preview immediately when the active tab changes.
        # The Sound tab uses a shorter preview in a normal window so its full
        # control panel remains visible without requiring maximized mode.
        QtCore.QTimer.singleShot(0, self._update_preview_geometry)

        # Hide preview only on "Command" tab (index 4)
'''

OLD_RESIZE_BLOCK = '''    def resizeEvent(self, event):
        # Keep preview roughly 35% of window height; maintain 169:253 aspect
        h = int(self.height() * 0.35)
        w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

'''

NEW_RESIZE_BLOCK = '''    def _update_preview_geometry(self) -> None:
        """Keep Sound usable in normal windows without changing full-screen layout."""
        window_height = max(1, self.height())
        try:
            current_tab = self.tabbar.currentIndex()
        except Exception:
            current_tab = -1

        sound_in_normal_window = (
            current_tab == 1
            and not self.isMaximized()
            and not self.isFullScreen()
        )

        if sound_in_normal_window:
            # The preview content is naturally 169 x 253. Capping it near that
            # native height returns roughly 60-75 px to the Sound controls.
            h = max(220, min(253, int(window_height * 0.30)))
        else:
            # Preserve the existing appearance in maximized/full-screen mode
            # and on every other tab.
            h = int(window_height * 0.35)

        w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

    def resizeEvent(self, event):
        self._update_preview_geometry()

'''


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _resolve_project_root(argument: str | None) -> Path:
    if argument:
        return Path(argument).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_file(root: Path, *names: str) -> Path:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Could not find any of {names!r} in {root}")


def _backup_once(path: Path, suffix: str) -> Path:
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def _patch_nexus(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = original

    if "def _update_preview_geometry(self)" not in updated:
        if OLD_RESIZE_BLOCK not in updated:
            raise RuntimeError(
                "Nexus.py does not contain the expected preview resize block. "
                "No Nexus changes were written."
            )
        updated = updated.replace(OLD_RESIZE_BLOCK, NEW_RESIZE_BLOCK, 1)

    if "singleShot(0, self._update_preview_geometry)" not in updated:
        if TAB_CHANGE_ANCHOR not in updated:
            raise RuntimeError(
                "Nexus.py does not contain the expected tab-change anchor. "
                "No Nexus changes were written."
            )
        updated = updated.replace(TAB_CHANGE_ANCHOR, TAB_CHANGE_REPLACEMENT, 1)

    if updated == original:
        return False

    compile(updated, str(path), "exec")
    _backup_once(path, ".bak_sound_layout")
    _atomic_write(path, updated)
    return True


def _install_sound_tab(source: Path, destination: Path) -> bool:
    supplied = source.read_text(encoding="utf-8")
    compile(supplied, str(destination), "exec")

    existing = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    if supplied == existing:
        return False

    if destination.is_file():
        _backup_once(destination, ".bak_sound_layout")
    _atomic_write(destination, supplied)
    return True


def main() -> int:
    root = _resolve_project_root(sys.argv[1] if len(sys.argv) > 1 else None)
    script_dir = Path(__file__).resolve().parent
    supplied_sound_tab = script_dir / "sound_tab.py"
    if not supplied_sound_tab.is_file():
        raise FileNotFoundError(
            "The supplied sound_tab.py must be beside this installer script."
        )

    nexus_path = _resolve_file(root, "Nexus.py", "nexus.py")
    sound_path = root / "sound_tab.py"

    sound_changed = _install_sound_tab(supplied_sound_tab, sound_path)
    nexus_changed = _patch_nexus(nexus_path)

    print(f"Project:   {root}")
    print(f"Sound tab: {'updated' if sound_changed else 'already current'}")
    print(f"Nexus:     {'updated' if nexus_changed else 'already current'}")
    print("Completed. Restart Letter Smith to load the changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
