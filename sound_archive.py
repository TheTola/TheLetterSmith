# =========================
# File: sound_archive.py
# =========================
"""
Sound Archive System — Professional, additive module for the Sound tab.

Purpose (no features removed):
- Archive every added song once at gallery/sounds/archive/
- Set current track by creating music.mp3 pointers (prefer hard link, then symlink, else atomic copy)
  in BOTH locations: <project root>/music.mp3 and gallery/sounds/music.mp3
- Animated Archive dropdown UI to browse/use archived songs
- Rename dialog when a same-name but different-size file is added
- Strict dedupe rule you requested:
    * same name AND same size => skip creating a duplicate
    * same name BUT different size => prompt to rename, then archive
- current.json record for debugging and audits

Drop-in usage:
    from sound_archive import SoundArchiveManager, install_archive_dropdown

    mgr = SoundArchiveManager(project_root=Path(...))
    install_archive_dropdown(parent_tab_widget, mgr)  # adds an Archive button + dropdown panel

If your existing Sound tab already has an "Add Song" action, just call:
    mgr.add_song_from_path(Path("C:/path/to/file.mp3"))

If you need to mark an archived file as current:
    mgr.set_current("filename.mp3")  # must exist in archive

This module is fully standalone and additive.
"""

from __future__ import annotations

import os
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt


# ------------------------------
# Utility: safe, atomic file ops
# ------------------------------

def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with src.open("rb") as rf, tmp.open("wb") as wf:
        shutil.copyfileobj(rf, wf, length=1024 * 1024)  # 1 MB chunks
        wf.flush()
        os.fsync(wf.fileno())
    os.replace(tmp, dst)  # atomic replace on NTFS/Windows


def _unlink_if_exists(p: Path) -> None:
    try:
        if p.exists() or p.is_symlink():
            p.unlink()
    except Exception:
        pass


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return -1


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------
# Archive Manager (logic)
# ------------------------------

@dataclass
class AddResult:
    action: str          # "skipped" | "added" | "renamed"
    archive_name: str    # final filename in archive (or original if skipped)
    size: int
    message: str


class SoundArchiveManager(QtCore.QObject):
    """
    Core logic for archiving songs and managing the current track pointers.

    Public API:
        add_song_from_path(path: Path) -> AddResult
        set_current(archive_filename: str) -> str   # returns link mode used
        list_archive() -> List[Dict]  # filename, size, added (timestamp string)
        archive_dir  (Path)
        project_root (Path)

    Signals:
        archive_changed: emitted after archive updates (add/rename/delete future expansion)
        current_changed(str): emitted after setting current, with the archive filename
    """
    archive_changed = QtCore.Signal()
    current_changed = QtCore.Signal(str)

    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self._archive_dir = (self.project_root / "gallery" / "sounds" / "archive")
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._current_manifest = (self.project_root / "gallery" / "sounds" / "current.json")

        # Dual target locations for current music.mp3 (kept for compatibility)
        self._current_targets = [
            (self.project_root / "music.mp3"),
            (self.project_root / "gallery" / "sounds" / "music.mp3"),
        ]

    # -------- Properties --------
    @property
    def archive_dir(self) -> Path:
        return self._archive_dir

    # -------- Public Methods --------
    def list_archive(self) -> List[Dict]:
        out: List[Dict] = []
        if not self._archive_dir.exists():
            return out
        for p in sorted(self._archive_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file():
                continue
            out.append({
                "filename": p.name,
                "size": _file_size(p),
                "added": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return out

    def add_song_from_path(self, source: Path) -> AddResult:
        """
        Archive the given file (with dedupe & rename flow) and set it as current.
        This is the single entry point to use from your existing Sound tab when a new song is added.
        """
        source = Path(source)
        if not source.exists() or not source.is_file():
            return AddResult(action="error", archive_name=source.name, size=-1,
                             message="Source file does not exist or is not a file.")

        size = _file_size(source)
        final_name, action = self._compute_archive_target_name(source.name, size)
        if action == "cancel":
            return AddResult(action="canceled", archive_name=source.name, size=size,
                             message="User canceled rename due to name conflict.")
        dest = self._archive_dir / final_name

        # If skipped (same name & size), don't copy again
        if action == "skipped":
            # Still set as current to match your UX
            link_mode = self._retarget_to_archive(dest)
            self._write_current_manifest(dest.name, size, link_mode)
            self.current_changed.emit(dest.name)
            return AddResult(action="skipped", archive_name=dest.name, size=size,
                             message="Duplicate by name and size; archived copy not created. Set as current.")

        # Perform safe copy to archive
        try:
            _atomic_copy(source, dest)
        except Exception as e:
            return AddResult(action="error", archive_name=final_name, size=size,
                             message=f"Failed to archive: {e!r}")

        # Set as current (create hardlink/symlink/copy to both targets)
        link_mode = self._retarget_to_archive(dest)
        self._write_current_manifest(dest.name, size, link_mode)
        self.archive_changed.emit()
        self.current_changed.emit(dest.name)
        return AddResult(action=("renamed" if action == "renamed" else "added"),
                         archive_name=dest.name, size=size,
                         message=f"Archived as {dest.name} and set current via {link_mode}.")

    def set_current(self, archive_filename: str) -> str:
        """
        Point both music.mp3 targets at the given archived file.
        Returns the link/copy mode used ("hardlink"|"symlink"|"copy").
        """
        src = (self._archive_dir / archive_filename)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"Not in archive: {archive_filename}")
        link_mode = self._retarget_to_archive(src)
        self._write_current_manifest(archive_filename, _file_size(src), link_mode)
        self.current_changed.emit(archive_filename)
        return link_mode

    # -------- Internal helpers --------
    def _compute_archive_target_name(self, desired_name: str, size: int) -> Tuple[str, str]:
        """
        Decide final archive filename based on dedupe rule:
          - Same name AND same size => "skipped"
          - Same name BUT different size => prompt rename -> returns new name or "cancel"
          - Otherwise => use desired_name as-is
        Returns (final_name, action)
        """
        desired = self._archive_dir / desired_name
        if desired.exists():
            existing_size = _file_size(desired)
            if existing_size == size:
                return desired_name, "skipped"
            # same name, different size -> ask user to rename
            new_name = self._prompt_rename(desired_name)
            if not new_name:
                return desired_name, "cancel"
            return new_name, "renamed"
        return desired_name, "added"

    def _prompt_rename(self, current_name: str) -> Optional[str]:
        dlg = RenameDialog(current_name, parent=None)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            candidate = dlg.final_name()
            # Ensure unique (if still collides, append increasingly)
            base = Path(candidate).stem
            ext = Path(candidate).suffix
            i = 2
            final = candidate
            while (self._archive_dir / final).exists():
                final = f"{base} ({i}){ext}"
                i += 1
            return final
        return None

    def _retarget_to_archive(self, src_archive_file: Path) -> str:
        """
        For each target in self._current_targets:
           try hardlink -> symlink -> atomic copy
        Return the mode used for the first successful operation (may mix per-target,
        but we return a representative string).
        """
        mode_used = None
        for dst in self._current_targets:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Remove existing target (file or link)
            _unlink_if_exists(dst)

            # 1) Hard link
            try:
                os.link(src_archive_file, dst)
                mode_used = mode_used or "hardlink"
                continue
            except Exception:
                pass

            # 2) Symlink
            try:
                rel = os.path.relpath(src_archive_file, start=dst.parent)
                os.symlink(rel, dst)
                mode_used = mode_used or "symlink"
                continue
            except Exception:
                pass

            # 3) Atomic copy fallback
            _atomic_copy(src_archive_file, dst)
            mode_used = mode_used or "copy"

        return mode_used or "copy"

    def _write_current_manifest(self, archive_name: str, size: int, mode: str) -> None:
        data = {
            "current_rel": f"gallery/sounds/archive/{archive_name}",
            "link_mode": mode,
            "size": size,
            "set_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._current_manifest.parent.mkdir(parents=True, exist_ok=True)
        self._current_manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ------------------------------
# UI: Rename Dialog
# ------------------------------

class RenameDialog(QtWidgets.QDialog):
    """
    Frameless, minimal dialog asking the user to rename an incoming file.
    Keeps original extension; validates name and collisions.
    """
    def __init__(self, current_name: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._current_name = current_name

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet("""
            #panel { background: rgba(18,18,24,240); border-radius: 14px; border: 1px solid #2b3344; }
            QLabel { color: #e6e6e6; }
            QLineEdit { background: #1e2230; border: 1px solid #2b3344; border-radius: 8px; padding: 6px 8px; color: #e6e6e6; }
            QPushButton { background: #222530; border: 1px solid #2b3344; border-radius: 8px; padding: 6px 10px; color: #e6e6e6; }
            QPushButton:hover { border-color: #00c8ff; }
        """)
        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(10)

        lbl = QtWidgets.QLabel(
            f"A file named <b>{current_name}</b> already exists with a different size.<br>"
            "Please enter a new name for the incoming song:"
        )
        self._edit = QtWidgets.QLineEdit(current_name)
        self._edit.selectAll()

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_save = QtWidgets.QPushButton("Save")
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)

        inner.addWidget(lbl)
        inner.addWidget(self._edit)
        inner.addLayout(btns)
        outer.addWidget(panel)

        btn_cancel.clicked.connect(self.reject)
        btn_save.clicked.connect(self._on_save)

        self.resize(460, 160)

    def _on_save(self) -> None:
        name = self._edit.text().strip()
        if not name:
            return
        # Preserve extension of original
        ext = Path(self._current_name).suffix
        if not name.endswith(ext):
            name += ext
        # Disallow path separators
        if any(ch in name for ch in ('/', '\\')):
            return
        self._final = name
        self.accept()

    def final_name(self) -> str:
        return getattr(self, "_final", self._current_name)


# ------------------------------
# UI: Animated Archive Dropdown
# ------------------------------

class ArchiveDropdown(QtWidgets.QFrame):
    """
    Stylized animated dropdown panel listing archived songs.
    Emits 'use_requested(filename: str)' when the user chooses a song.
    """
    use_requested = QtCore.Signal(str)

    def __init__(self, mgr: SoundArchiveManager, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ArchiveDropdown")
        self._mgr = mgr

        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet("""
            #panel { background: rgba(15,17,22,240); border-radius: 12px; border: 1px solid #2b3344; }
            QLabel { color: #dcdfe4; }
            QScrollArea { border: none; background: transparent; }
            QPushButton { background: #1b1f2a; border: 1px solid #2a2f3e; border-radius: 8px; padding: 5px 8px; color: #e6e6e6; }
            QPushButton:hover { border-color: #00c8ff; }
        """)

        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Archive")
        title.setStyleSheet("font-weight:600; font-size:14px;")
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.setFixedHeight(26)
        refresh.clicked.connect(self.refresh)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_holder = QtWidgets.QWidget()
        self._list_layout = QtWidgets.QVBoxLayout(self._list_holder)
        self._list_layout.setContentsMargins(0,0,0,0)
        self._list_layout.setSpacing(6)
        self._scroll.setWidget(self._list_holder)

        inner.addLayout(header)
        inner.addWidget(self._scroll, 1)
        outer.addWidget(panel)

        self._mgr.archive_changed.connect(self.refresh)
        self.refresh()

        # Simple fade-in animation on show
        self._opacity = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._anim = QtCore.QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)

    def show_at(self, global_pos: QtCore.QPoint) -> None:
        self.move(global_pos)
        self._opacity.setOpacity(0.0)
        super().show()
        self._anim.start()

    def refresh(self) -> None:
        # Clear list
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        items = self._mgr.list_archive()
        for it in items:
            self._list_layout.addWidget(self._make_row(it["filename"], it["size"], it["added"]))
        self._list_layout.addStretch(1)

    def _make_row(self, name: str, size: int, added: str) -> QtWidgets.QWidget:
        w = QtWidgets.QFrame()
        w.setObjectName("row")
        w.setStyleSheet("""
            #row { background: #121520; border: 1px solid #2b3344; border-radius: 8px; }
        """)
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(8,6,8,6)
        h.setSpacing(8)

        lbl_name = QtWidgets.QLabel(name)
        lbl_name.setToolTip(name)
        lbl_meta = QtWidgets.QLabel(f"{size} bytes  —  {added}")
        lbl_meta.setStyleSheet("color:#9aa4b2;")

        btn_use = QtWidgets.QPushButton("Use")
        btn_use.setFixedHeight(26)
        btn_use.clicked.connect(lambda: self.use_requested.emit(name))

        h.addWidget(lbl_name, 1)
        h.addWidget(lbl_meta, 0)
        h.addStretch(1)
        h.addWidget(btn_use, 0)
        return w


# ------------------------------
# Installer: add Archive button + dropdown to an existing tab
# ------------------------------

def install_archive_dropdown(host_tab: QtWidgets.QWidget, mgr: SoundArchiveManager) -> QtWidgets.QPushButton:
    """
    Adds a compact "Archive" button to the top-right of host_tab and wires a popup dropdown.
    Returns the created button so you can place/position it as needed if you already
    have a toolbar layout.
    """
    # Create the button (small footprint)
    btn = QtWidgets.QPushButton("Archive", host_tab)
    btn.setObjectName("btn_sound_archive")
    btn.setFixedHeight(28)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet("""
        QPushButton#btn_sound_archive { background:#1e2230; border:1px solid #2b3344; border-radius: 8px; color:#e6e6e6; padding: 4px 10px; }
        QPushButton#btn_sound_archive:hover { border-color:#00c8ff; }
    """)

    # Try to find a top-level layout to place the button neatly
    if host_tab.layout() is None:
        lay = QtWidgets.QVBoxLayout(host_tab)
        lay.setContentsMargins(10,10,10,10)
        lay.setSpacing(8)
    else:
        lay = host_tab.layout()

    # Put the button in a right-justified row
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    row.addWidget(btn, 0)
    lay.addLayout(row)

    dropdown = ArchiveDropdown(mgr, parent=host_tab)

    def on_click():
        # Position dropdown just under the button
        gp = btn.mapToGlobal(QtCore.QPoint(0, btn.height()))
        dropdown.refresh()
        dropdown.show_at(gp)

    btn.clicked.connect(on_click)

    # When user chooses an item, set it current
    def on_use(filename: str):
        try:
            mode = mgr.set_current(filename)
            # Optionally, show a transient toast
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Set current via {mode}: {filename}", btn)
        except Exception as e:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Error: {e}", btn)
        dropdown.hide()

    dropdown.use_requested.connect(on_use)
    return btn
