# ================================
# File: sound_tab_archive_patch.py
# ================================
"""
Non-invasive installer for the Sound Archive system.

Usage pattern in your existing Sound tab file (sound_tab.py):

    from pathlib import Path
    from sound_archive import SoundArchiveManager, install_archive_dropdown
    from sound_tab_archive_patch import attach_archive_to_sound_tab

    class SoundTab(QtWidgets.QWidget):
        def __init__(self, project_root: Path, parent=None):
            super().__init__(parent)
            self.project_root = Path(project_root).resolve()
            # ... your existing UI ...
            # At the end of your __init__ once layouts exist:
            self._archive_mgr = SoundArchiveManager(self.project_root)
            attach_archive_to_sound_tab(self, self._archive_mgr)

        # Wherever you currently handle "Add Song":
        def on_add_song_clicked(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add Song", str(self.project_root), "Audio (*.mp3 *.wav *.ogg)")
            if path:
                result = self._archive_mgr.add_song_from_path(Path(path))
                # OPTIONAL: show result.message in your status bar / tooltip / log
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), result.message, self)

This keeps your current Sound tab intact and only adds the Archive behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from sound_archive import SoundArchiveManager, install_archive_dropdown


def attach_archive_to_sound_tab(sound_tab: QtWidgets.QWidget, mgr: SoundArchiveManager) -> None:
    """
    Adds the Archive button + dropdown to the given sound_tab widget.
    Does not remove or modify existing controls. The button is added as a right-justified row
    at the end of the tab's top-level layout. If there is no top-level layout yet, a VBox is created.
    """
    install_archive_dropdown(sound_tab, mgr)

    # Optional: expose a convenience method on the tab for your existing code to call.
    # This avoids touching a lot of code paths elsewhere.
    def add_song_from_path(path: str | Path) -> None:
        res = mgr.add_song_from_path(Path(path))
        # You can replace this tooltip with your logger/toast system
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), res.message, sound_tab)

    # Attach as a bound method if not already present
    if not hasattr(sound_tab, "add_song_from_path"):
        setattr(sound_tab, "add_song_from_path", add_song_from_path)

    # Optional: connect archive/current change signals to any status label you might have
    # (We keep it passive to avoid assumptions about your UI.)
    # mgr.archive_changed.connect(lambda: ...)
    # mgr.current_changed.connect(lambda name: ...)
