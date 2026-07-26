# ===============================
# File: sound_tab.py
# Purpose: Sound tab (archive + playback + analysis + visualizer)


from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict

# --- Qt ---
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,
    QFileDialog, QMessageBox
)

from audio_export import _export_apple_safe_mp3
from settings_store import SettingsStore

# --- Visualizer (shared preview widget) ---
from sound_preview import SoundPreviewWidget

# --- Analysis (offline MP3 -> cached) ---
try:
    from sound_analyzer import AudioAnalysisManager
except Exception:
    AudioAnalysisManager = None  # type: ignore

# --- Audio utils ---
from mutagen import File as MutagenFile

# --- Project config ---
from config import (
    MAX_AUDIO_MB,
    MUSIC_FILE,
    GLISS_FILE,
    FLIP_PREFIX,
    FLIP_COUNT,
    USER_SOUNDS_DIR,
    USER_AUDIO_ORIGINALS_DIR,
    USER_AUDIO_PROCESSED_DIR,
    USER_AUDIO_ANALYSIS_DIR,
    USER_AUDIO_MANIFEST_FILE,
)


# Supported formats
VALID_AUDIO_EXTS = [".mp3", ".wav", ".ogg", ".aac", ".m4a", ".flac"]


# Gliss always at 10% (independent of music volume)
GLISS_VOLUME_PERCENT = 10


# ─────────────────────────────────────────────────────────────────────────────
# NEW PATH MODEL (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

def _user_sounds_dir(project_root: Path) -> Path:
    # gallery/user/sounds
    return project_root / USER_SOUNDS_DIR


def _user_archive_originals(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/originals
    return project_root / USER_AUDIO_ORIGINALS_DIR


def _user_archive_processed(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/processed
    return project_root / USER_AUDIO_PROCESSED_DIR


def _user_archive_analysis(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/analysis
    return project_root / USER_AUDIO_ANALYSIS_DIR


def _user_current_manifest(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/current.json
    return project_root / USER_AUDIO_MANIFEST_FILE


def _user_current_music(project_root: Path) -> Path:
    # gallery/user/sounds/music.mp3
    return _user_sounds_dir(project_root) / MUSIC_FILE


def _user_gliss_path(project_root: Path) -> Path:
    return _user_sounds_dir(project_root) / GLISS_FILE


def _user_flip_path(project_root: Path, i: int) -> Path:
    return _user_sounds_dir(project_root) / f"{FLIP_PREFIX}{i}.mp3"


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_copy(src: Path, dst: Path) -> None:
    """Atomic write via *.tmp then os.replace."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with src.open("rb") as rf, tmp.open("wb") as wf:
        shutil.copyfileobj(rf, wf, length=1024 * 1024)
        wf.flush()
        os.fsync(wf.fileno())
    os.replace(tmp, dst)


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return -1


def _open_folder(path: Path) -> None:
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform.startswith("darwin"):
            import subprocess
            subprocess.run(["open", str(path)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ─────────────────────────────────────────────────────────────────────────────
# Archive manager (CURRENT target: gallery/user/sounds/music.mp3)
# Archive: gallery/user/sounds/appssong/{originals,processed}
# Manifest: gallery/user/sounds/appssong/current.json
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AddResult:
    action: str
    archive_original: Optional[str]
    archive_processed: Optional[str]
    size: int
    message: str


class SoundArchiveManager(QtCore.QObject):
    archive_changed = QtCore.Signal()
    current_changed = QtCore.Signal(str)  # processed filename

    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = Path(project_root).resolve()

        self.sounds_root = _user_sounds_dir(self.project_root)
        self.dir_originals = _user_archive_originals(self.project_root)
        self.dir_processed = _user_archive_processed(self.project_root)
        self.current_manifest = _user_current_manifest(self.project_root)

        self.sounds_root.mkdir(parents=True, exist_ok=True)
        self.dir_originals.mkdir(parents=True, exist_ok=True)
        self.dir_processed.mkdir(parents=True, exist_ok=True)

        # Single source of truth for live audio used by viewer
        self.current_target = _user_current_music(self.project_root)



    # Names-only listing (newest first by normal archive/file order)
    def list_archive(self) -> List[str]:
        names: List[str] = []
        if not self.dir_processed.exists():
            return names
        items = sorted(self.dir_processed.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in items:
            if p.is_file() and p.suffix.lower() == ".mp3":
                names.append(p.name)
        return names

    def current_processed_name(self) -> str:
        """Return the current processed archive filename from current.json, if valid."""
        try:
            if not self.current_manifest.exists():
                return ""
            data = json.loads(self.current_manifest.read_text(encoding="utf-8"))
            rel = str(data.get("current_processed_rel") or data.get("current_rel") or "")
            name = Path(rel).name if rel else ""
            if name and (self.dir_processed / name).is_file():
                return name
        except Exception:
            pass
        return ""

    def list_archive_for_display(self) -> List[str]:
        """Current song first; all other songs remain in normal archive order."""
        names = self.list_archive()
        current = self.current_processed_name()
        if current and current in names:
            return [current] + [name for name in names if name != current]
        return names

    def add_song_from_path(self, source: Path) -> AddResult:
        """
        Two-copy flow:
          • appssong/originals/<originalname.ext>
          • appssong/processed/<originalname>.mp3  (normalize→mp3; fallback copy if mp3)
        Then set current by copying to gallery/user/sounds/music.mp3 and write manifest.
        """
        source = Path(source)
        if not source.exists() or not source.is_file():
            return AddResult("error", None, None, -1, "Source file does not exist or is not a file.")

        incoming_size = _file_size(source)
        if incoming_size > MAX_AUDIO_MB * 1024 * 1024:
            return AddResult("error", None, None, incoming_size, f"File exceeds {MAX_AUDIO_MB} MB limit.")

        final_original_name, action = self._decide_original_name(source.name, incoming_size)
        if action == "cancel":
            return AddResult("canceled", None, None, incoming_size, "User canceled rename due to conflict.")

        orig_path = self.dir_originals / final_original_name
        if action != "skipped":
            try:
                _atomic_copy(source, orig_path)
            except Exception as e:
                return AddResult("error", None, None, incoming_size, f"Failed to archive original: {e!r}")

        processed_name = Path(final_original_name).stem + ".mp3"
        processed_path = self.dir_processed / processed_name

        try:
            _export_apple_safe_mp3(source, processed_path)
        except Exception as e:
            return AddResult(
                "error",
                final_original_name,
                None,
                incoming_size,
                f"Failed to export Apple-safe MP3: {e}",
            )

        # Set current (Windows-safe with short retries)
        try:
            self._set_current_by_copy(processed_path)
        except Exception as e:
            return AddResult(
                "error",
                final_original_name,
                processed_name,
                incoming_size,
                f"Failed to set current Apple-safe music: {e}",
            )
        self._write_manifest(processed_name, _file_size(processed_path), final_original_name)

        self.archive_changed.emit()
        self.current_changed.emit(processed_name)

        msg = {
            "skipped": "Duplicate by name and size; original not copied. Processed & set current.",
            "renamed": f"Original renamed to {final_original_name}. Processed & set current.",
            "added":   f"Archived original as {final_original_name}. Processed & set current.",
        }.get(action, "Processed & set current.")
        return AddResult(action, final_original_name, processed_name, incoming_size, msg)

    def set_current(self, processed_filename: str) -> None:
        src = self.dir_processed / processed_filename
        if not src.exists():
            raise FileNotFoundError(f"Not in processed archive: {processed_filename}")
        self._set_current_by_copy(src)
        self._write_manifest(processed_filename, _file_size(src), self._original_name_for_processed(processed_filename))
        self.current_changed.emit(processed_filename)

    def delete_processed(self, processed_filename: str) -> bool:
        """
        Delete one processed item by name. If it was current, fall back to newest remaining,
        else clear music.mp3 + manifest.
        """
        target = self.dir_processed / processed_filename
        if not target.exists():
            return False

        is_current = False
        try:
            data = json.loads(self.current_manifest.read_text(encoding="utf-8"))
            current_rel = data.get("current_processed_rel") or data.get("current_rel", "")
            expected_rel = f"{USER_AUDIO_PROCESSED_DIR}/{processed_filename}"
            is_current = (current_rel == expected_rel)
        except Exception:
            pass

        try:
            target.unlink()
        except Exception:
            return False

        if is_current:
            remaining = self.list_archive()
            if remaining:
                self.set_current(remaining[0])  # newest
            else:
                try:
                    if self.current_target.exists():
                        self.current_target.unlink()
                except Exception:
                    pass
                try:
                    if self.current_manifest.exists():
                        self.current_manifest.unlink()
                except Exception:
                    pass
                self.current_changed.emit("")

        self.archive_changed.emit()
        return True

    # ── internals ────────────────────────────────────────────
    def _decide_original_name(self, desired_name: str, size: int) -> Tuple[str, str]:
        desired = self.dir_originals / desired_name
        if desired.exists():
            existing_size = _file_size(desired)
            if existing_size == size:
                return desired_name, "skipped"
            new_name = self._prompt_rename(desired_name)
            if not new_name:
                return desired_name, "cancel"
            return new_name, "renamed"
        return desired_name, "added"

    def _prompt_rename(self, current_name: str) -> Optional[str]:
        dlg = RenameDialog(current_name)
        return dlg.run_and_get_name()

    def _set_current_by_copy(self, src: Path) -> None:
        src = src.resolve()
        dst = self.current_target.resolve()
        if src == dst:
            return

        tries = 5
        delay = 0.15
        for _ in range(tries):
            try:
                _export_apple_safe_mp3(src, dst)
                return
            except PermissionError:
                if os.name == "nt":
                    time.sleep(delay)
                    continue
                raise
            except OSError as e:
                if os.name == "nt" and e.errno in (errno.EACCES, errno.EPERM):
                    time.sleep(delay)
                    continue
                raise
        raise PermissionError(
            f"Unable to replace {dst} after {tries} attempts. "
            "Is it open in another app or being previewed/synced?"
        )

    def _original_name_for_processed(self, processed_name: str) -> str:
        exact = self.dir_originals / processed_name
        if exact.is_file():
            return processed_name

        stem = Path(processed_name).stem
        try:
            matches = [p for p in self.dir_originals.iterdir() if p.is_file() and p.stem == stem]
        except Exception:
            matches = []

        for p in matches:
            if p.suffix.lower() == ".mp3":
                return p.name
        if matches:
            return sorted(matches, key=lambda p: p.name.lower())[0].name
        return processed_name

    def _write_manifest(self, processed_name: str, size: int, original_name: Optional[str] = None) -> None:
        original_name = original_name or self._original_name_for_processed(processed_name)
        data = {
            "current_original_rel": f"{USER_AUDIO_ORIGINALS_DIR}/{original_name}",
            "current_processed_rel": f"{USER_AUDIO_PROCESSED_DIR}/{processed_name}",
            "current_live_rel": f"{USER_SOUNDS_DIR}/{MUSIC_FILE}",
            "current_rel": f"{USER_AUDIO_PROCESSED_DIR}/{processed_name}",
            "link_mode": "copy",
            "size": size,
            "set_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.current_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.current_manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Minimal frameless rename dialog (keeps ext)
# ─────────────────────────────────────────────────────────────────────────────

class RenameDialog(QtWidgets.QDialog):
    def __init__(self, current_name: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._current_name = current_name
        self._final = current_name

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "#panel { background: rgba(18,18,24,240); border-radius: 14px; border: 1px solid #2b3344; }"
            "QLabel { color: #e6e6e6; }"
            "QLineEdit { background: #1e2230; border: 1px solid #2b3344; border-radius: 8px; padding: 6px 8px; color: #e6e6e6; }"
            "QPushButton { background: #222530; border: 1px solid #2b3344; border-radius: 8px; padding: 6px 10px; color: #e6e6e6; }"
            "QPushButton:hover { border-color: #00c8ff; }"
        )
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

        ext = Path(self._current_name).suffix
        if not name.endswith(ext):
            name += ext

        if any(ch in name for ch in '<>:"/\\|?*'):
            return

        base = Path(name).stem.upper()
        if base in {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
        }:
            return

        self._final = name
        self.accept()

    def run_and_get_name(self) -> Optional[str]:
        return self._final if (self.exec() == QtWidgets.QDialog.Accepted) else None


# ─────────────────────────────────────────────────────────────────────────────
# Frameless confirm (“Are you sure?”) used by delete
# ─────────────────────────────────────────────────────────────────────────────

class ConfirmDialog(QtWidgets.QDialog):
    def __init__(self, message: str, parent=None):
        super().__init__(parent or None)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "#panel { background: rgba(15,17,22,240); border-radius: 12px; border: 1px solid #2b3344; }"
            "QLabel { color: #e6e6e6; }"
            "QPushButton { background: #1b1f2a; border: 1px solid #2a2f3e; border-radius: 8px; padding: 5px 10px; color:#e6e6e6; }"
            "QPushButton:hover { border-color:#00c8ff; }"
        )
        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(14, 14, 14, 14)
        inner.setSpacing(10)

        lbl = QtWidgets.QLabel(message)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        btn_no = QtWidgets.QPushButton("No")
        btn_yes = QtWidgets.QPushButton("Yes")
        row.addWidget(btn_no)
        row.addWidget(btn_yes)

        inner.addWidget(lbl)
        inner.addLayout(row)
        outer.addWidget(panel)

        btn_no.clicked.connect(self.reject)
        btn_yes.clicked.connect(self.accept)


# ─────────────────────────────────────────────────────────────────────────────
# Archive dropdown (movable + clamped + clickable song names + delete mode)
# ─────────────────────────────────────────────────────────────────────────────


class _ArchivePaintButton(QtWidgets.QWidget):
    """
    Custom-painted clickable control for the archive popup.

    This intentionally does NOT inherit QPushButton.
    Reason: global/app QPushButton:hover styles were overriding the archive text
    paint state and making labels appear to vanish on hover/highlight.
    """

    clicked = QtCore.Signal()

    def __init__(
        self,
        text: str,
        *,
        kind: str = "normal",
        elide: bool = False,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._elide = elide
        self._hover = False
        self._pressed = False
        self._active = False

        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(text)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        font = QtGui.QFont("Segoe UI", 9)
        if kind in {"danger", "delete"}:
            font.setWeight(QtGui.QFont.DemiBold)
        self.setFont(font)

        if kind == "danger":
            self.setFixedSize(26, 26)
        elif kind == "delete":
            self.setFixedSize(74, 28)
        elif kind == "song":
            self.setFixedHeight(30)
        else:
            self.setFixedHeight(28)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def is_active(self) -> bool:
        return self._active

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            was_pressed = self._pressed
            self._pressed = False
            self.update()
            if was_pressed and self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = 8 if self._kind != "song" else 6

        bg, border, fg = self._colors()

        if bg.alpha() > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)

        if border.alpha() > 0:
            pen = QtGui.QPen(border)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(fg)
        painter.setFont(self.font())

        if self._kind == "danger":
            text = self._text
            align = Qt.AlignCenter
            text_rect = rect
        elif self._kind == "delete":
            text = self._text
            align = Qt.AlignCenter
            text_rect = rect
        else:
            metrics = QtGui.QFontMetrics(self.font())
            usable = max(20, rect.width() - 16)
            text = metrics.elidedText(self._text, Qt.ElideRight, usable) if self._elide else self._text
            align = Qt.AlignVCenter | Qt.AlignLeft
            text_rect = rect.adjusted(8, 0, -8, 0)

        painter.drawText(text_rect, align, text)

    def _colors(self) -> Tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
        if self._kind == "song":
            bg = QtGui.QColor(0, 0, 0, 0)
            border = QtGui.QColor(0, 0, 0, 0)
            fg = QtGui.QColor("#e6e6e6")

            if self._active:
                # Current song: subtle, visible distinction without making the row loud.
                bg = QtGui.QColor(0, 200, 255, 28)
                border = QtGui.QColor(0, 200, 255, 92)
                fg = QtGui.QColor("#dffaff")

            if self._hover or self.hasFocus():
                bg = QtGui.QColor(0, 200, 255, 54 if self._active else 46)
                border = QtGui.QColor(0, 200, 255, 130 if self._active else 0)
                fg = QtGui.QColor("#ffffff")

            if self._pressed:
                bg = QtGui.QColor(0, 200, 255, 82 if self._active else 72)
                border = QtGui.QColor(0, 229, 255, 170 if self._active else 0)
                fg = QtGui.QColor("#ffffff")

            return bg, border, fg

        if self._kind == "delete":
            bg = QtGui.QColor("#1b1f2a")
            border = QtGui.QColor(255, 77, 79, 140)
            fg = QtGui.QColor("#ffd7d9")
            if self._hover or self.hasFocus():
                bg = QtGui.QColor("#3a1b20")
                border = QtGui.QColor("#ff6b6d")
                fg = QtGui.QColor("#ffffff")
            if self._pressed:
                bg = QtGui.QColor("#451e25")
                border = QtGui.QColor("#ff7c7e")
                fg = QtGui.QColor("#ffffff")
            return bg, border, fg

        if self._kind == "danger":
            if self._active:
                bg = QtGui.QColor("#2a171b")
                border = QtGui.QColor("#ff4d4f")
                fg = QtGui.QColor("#ffb8bd")
                if self._hover or self.hasFocus():
                    bg = QtGui.QColor("#3a1b20")
                    border = QtGui.QColor("#ff6b6d")
                    fg = QtGui.QColor("#ffffff")
                if self._pressed:
                    bg = QtGui.QColor("#451e25")
                    border = QtGui.QColor("#ff7c7e")
                    fg = QtGui.QColor("#ffffff")
                return bg, border, fg

            bg = QtGui.QColor("#1b1f2a")
            border = QtGui.QColor("#2a2f3e")
            fg = QtGui.QColor("#e6e6e6")
            if self._hover or self.hasFocus():
                bg = QtGui.QColor("#223040")
                border = QtGui.QColor("#00c8ff")
                fg = QtGui.QColor("#ffffff")
            if self._pressed:
                bg = QtGui.QColor("#182536")
                border = QtGui.QColor("#00e5ff")
                fg = QtGui.QColor("#ffffff")
            return bg, border, fg

        bg = QtGui.QColor("#1b1f2a")
        border = QtGui.QColor("#2a2f3e")
        fg = QtGui.QColor("#e6e6e6")
        if self._hover or self.hasFocus():
            bg = QtGui.QColor("#223040")
            border = QtGui.QColor("#00c8ff")
            fg = QtGui.QColor("#ffffff")
        if self._pressed:
            bg = QtGui.QColor("#182536")
            border = QtGui.QColor("#00e5ff")
            fg = QtGui.QColor("#ffffff")
        return bg, border, fg


class ArchiveDropdown(QtWidgets.QFrame):
    use_requested = QtCore.Signal(str)
    request_release = QtCore.Signal()  # ask parent to release player handle before file ops

    _STYLE = """
        QFrame#panel {
            background: rgba(15,17,22,242);
            border-radius: 12px;
            border: 1px solid #2b3344;
        }
        QFrame#row {
            background: #121520;
            border: 1px solid #2b3344;
            border-radius: 8px;
        }
        QLabel {
            color: #dcdfe4;
            background: transparent;
        }
        QScrollArea {
            border: none;
            background: transparent;
        }
        QScrollArea QWidget {
            background: transparent;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 5px;
            margin: 2px 0 2px 0;
        }
        QScrollBar::handle:vertical {
            background: rgba(255,255,255,42);
            border-radius: 2px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(0,200,255,90);
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
            background: transparent;
            border: none;
        }
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            height: 0px;
            background: transparent;
        }
        QScrollBar::handle:horizontal,
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            height: 0px;
            width: 0px;
            background: transparent;
            border: none;
        }
    """

    def __init__(self, mgr: SoundArchiveManager, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._mgr = mgr
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(520, 260)
        self.resize(600, 360)

        self._delete_mode = False
        self._drag_offset = QtCore.QPoint(0, 0)
        self._outside_filter_installed = False

        # Auto-refresh sources:
        # - manager signals after add/delete/current changes
        # - QFileSystemWatcher catches direct filesystem changes while the app is open
        # - show_at() still refreshes immediately before display
        self._refresh_pending = False
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule_refresh)
        self._watcher.fileChanged.connect(self._schedule_refresh)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self._panel = QtWidgets.QFrame(self)
        self._panel.setObjectName("panel")
        self._panel.setStyleSheet(self._STYLE)

        inner = QtWidgets.QVBoxLayout(self._panel)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Archive")
        title.setStyleSheet("font-weight:600; font-size:14px;")

        self.btn_delete_mode = _ArchivePaintButton("–", kind="danger", parent=self._panel)
        self.btn_delete_mode.setToolTip("Toggle delete mode")
        self.btn_delete_mode.clicked.connect(self._toggle_delete_mode)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.btn_delete_mode)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)

        self._list_holder = QtWidgets.QWidget()
        self._list_holder.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self._list_layout = QtWidgets.QVBoxLayout(self._list_holder)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._scroll.setWidget(self._list_holder)

        inner.addLayout(header)
        inner.addWidget(self._scroll, 1)
        outer.addWidget(self._panel)

        self._opacity = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._anim = QtCore.QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)

        self._mgr.archive_changed.connect(self._schedule_refresh)
        self._mgr.current_changed.connect(self._schedule_refresh)
        self._setup_auto_refresh()
        self.refresh()

    def _setup_auto_refresh(self) -> None:
        self._ensure_watched_paths()

    def _ensure_watched_paths(self) -> None:
        paths: list[str] = []
        for p in (
            self._mgr.dir_processed,
            self._mgr.dir_originals,
            self._mgr.current_manifest.parent,
            self._mgr.current_manifest,
        ):
            try:
                if p.exists():
                    paths.append(str(p.resolve()))
            except Exception:
                pass

        current = set(self._watcher.files()) | set(self._watcher.directories())
        wanted = set(paths)

        # Drop dead watches; QFileSystemWatcher can silently lose file watches
        # after atomic replace/delete, so rebuild the set conservatively.
        stale = [p for p in current if p not in wanted]
        if stale:
            try:
                self._watcher.removePaths(stale)
            except Exception:
                pass

        missing = [p for p in paths if p not in current]
        if missing:
            try:
                self._watcher.addPaths(missing)
            except Exception:
                for p in missing:
                    try:
                        self._watcher.addPath(p)
                    except Exception:
                        pass

    def _schedule_refresh(self, *_args) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QtCore.QTimer.singleShot(60, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        self._refresh_pending = False
        self._ensure_watched_paths()
        self.refresh()

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        return QtCore.QSize(600, 360)

    def _available_geom_at(self, global_pos: QtCore.QPoint) -> QtCore.QRect:
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if not screen:
            screen = QtGui.QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _clamp_to_screen(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        geom = self._available_geom_at(pos)
        hint = self.sizeHint()
        w = max(self.width(), hint.width())
        h = max(self.height(), hint.height())
        x = max(geom.left(), min(pos.x(), geom.right() - w))
        y = max(geom.top(),  min(pos.y(), geom.bottom() - h))
        return QtCore.QPoint(x, y)

    def _install_outside_filter(self) -> None:
        if self._outside_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._outside_filter_installed = True

    def _remove_outside_filter(self) -> None:
        if not self._outside_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._outside_filter_installed = False

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if not self.isVisible():
            return False

        et = event.type()
        if et == QtCore.QEvent.KeyPress:
            try:
                if event.key() == Qt.Key_Escape:  # type: ignore[attr-defined]
                    self.hide()
                    return True
            except Exception:
                return False

        if et in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
            try:
                global_pos = event.globalPosition().toPoint()  # type: ignore[attr-defined]
            except Exception:
                try:
                    global_pos = QtGui.QCursor.pos()
                except Exception:
                    return False

            if not self.frameGeometry().contains(global_pos):
                self.hide()
                return False

        return False

    def show_at(self, global_pos: QtCore.QPoint) -> None:
        self.refresh()
        p = self._clamp_to_screen(global_pos)
        self.move(p)
        self._opacity.setOpacity(0.0)
        super().show()
        self._install_outside_filter()
        self._anim.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self._remove_outside_filter()
        super().hideEvent(event)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.buttons() & Qt.LeftButton:
            new_pos = e.globalPosition().toPoint() - self._drag_offset
            self.move(self._clamp_to_screen(new_pos))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def refresh(self) -> None:
        self._ensure_watched_paths()

        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        names = self._mgr.list_archive_for_display()
        current = self._mgr.current_processed_name()
        if not names:
            empty = QtWidgets.QLabel("No archived music yet.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#8f98a8; padding:18px;")
            self._list_layout.addWidget(empty)
        else:
            for name in names:
                self._list_layout.addWidget(self._make_row(name, is_current=(name == current)))

        self._list_layout.addStretch(1)

    def _toggle_delete_mode(self) -> None:
        self._delete_mode = not self._delete_mode
        self.btn_delete_mode.set_active(self._delete_mode)
        self.refresh()

    def _make_row(self, name: str, *, is_current: bool = False) -> QtWidgets.QWidget:
        row = QtWidgets.QFrame()
        row.setObjectName("row")
        row.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        if is_current:
            row.setStyleSheet(
                "QFrame#row { "
                "background: rgba(0, 200, 255, 18); "
                "border: 1px solid rgba(0, 200, 255, 82); "
                "border-radius: 8px; "
                "}"
            )

        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(6, 5, 6, 5)
        h.setSpacing(6)

        btn_name = _ArchivePaintButton(name, kind="song", elide=True, parent=row)
        btn_name.set_active(is_current)
        if is_current:
            btn_name.setToolTip(f"Current song: {name}")
        btn_name.clicked.connect(lambda n=name: self._select_song(n))
        h.addWidget(btn_name, 1)

        if self._delete_mode:
            btn_del = _ArchivePaintButton("Delete", kind="delete", parent=row)
            btn_del.clicked.connect(lambda n=name: self._confirm_delete(n))
            h.addWidget(btn_del, 0)

        return row

    def _select_song(self, name: str) -> None:
        self.use_requested.emit(name)
        self.hide()

    def _confirm_delete(self, name: str) -> None:
        dlg = ConfirmDialog(f"Are you sure you want to remove “{name}” from archive?", self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.request_release.emit()
            ok = self._mgr.delete_processed(name)
            if not ok:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Delete failed.")
            else:
                self._schedule_refresh()


# ─────────────────────────────────────────────────────────────────────────────
# WaveHandler (player + persisted volume)
# Gliss policy: ALWAYS play the ORIGINAL file at fixed 10% volume.
# Supports overlapping one-shot SFX so a new play never cuts a previous one.
# ─────────────────────────────────────────────────────────────────────────────

class WaveHandler(QtCore.QObject):
    def __init__(
        self,
        project_root,
        archive_mgr: Optional[SoundArchiveManager] = None,
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.settings_store = SettingsStore(self.project_root)

        # Music player (persistent)
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)

        # One-shot SFX pool for gliss plays
        self._sfx_pool: List[Tuple[QMediaPlayer, QAudioOutput]] = []

        # Volume: load saved; else use STARTING_VOLUME from config
        self._music_volume = self._load_music_volume()  # 0.0..1.0
        self.audio_output.setVolume(self._music_volume)

        self._archive = archive_mgr
        self._muted = False
        self._shutdown = False

    def _load_music_volume(self) -> float:
        settings = self.settings_store.as_dict()
        if "music_volume" in settings:
            try:
                v = max(0, min(100, int(settings["music_volume"])))
                return v / 100.0
            except Exception:
                pass
        try:
            v = max(0, min(100, int(settings.get("starting_volume", 31))))
        except Exception:
            v = 31
        return v / 100.0

    def save_music_volume(self, volume_percent: int) -> None:
        v = max(0, min(100, int(volume_percent)))
        self.settings_store.update_fields(music_volume=v, starting_volume=v)

        self._music_volume = v / 100.0
        self.audio_output.setVolume(self._music_volume)

    def validate_audio(self, path: str) -> Tuple[bool, str]:
        if not os.path.exists(path):
            return False, "File does not exist."
        ext = os.path.splitext(path)[1].lower()
        if ext not in VALID_AUDIO_EXTS:
            return False, "Unsupported format."
        if os.path.getsize(path) > MAX_AUDIO_MB * 1024 * 1024:
            return False, f"File exceeds {MAX_AUDIO_MB}MB limit."
        try:
            audio = MutagenFile(path)
            duration = round(audio.info.length, 2) if audio and audio.info else "Unknown"
            return True, f"Valid audio file ({duration} seconds)"
        except Exception as e:
            return False, f"Metadata error: {e}"

    def load_audio(self, path: str) -> None:
        if self._shutdown:
            return
        self.player.setSource(QUrl.fromLocalFile(path))

    def play_audio(self) -> None:
        if self._shutdown:
            return
        self.audio_output.setVolume(self._music_volume)
        self.player.play()

    def pause_audio(self) -> None:
        self.player.pause()

    def start_over_audio(self) -> None:
        self.player.setPosition(0)
        self.audio_output.setVolume(self._music_volume)
        self.player.play()

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        self.audio_output.setMuted(self._muted)
        try:
            for _p, out in list(self._sfx_pool):
                out.setMuted(self._muted)
        except Exception:
            pass
        return self._muted

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def release_current_file_handle(self) -> None:
        """Stop playback and detach source to release OS file handle."""
        try:
            self.player.stop()
            self.player.setSource(QUrl())  # detach
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.release_current_file_handle()

        for player, output in list(self._sfx_pool):
            try:
                player.stop()
                player.setSource(QUrl())
                player.setAudioOutput(None)
            except Exception:
                pass
            player.deleteLater()
            output.deleteLater()
        self._sfx_pool.clear()

        try:
            self.player.setAudioOutput(None)
        except Exception:
            pass

    def play_gliss(self) -> bool:
        if self._shutdown:
            return False
        gliss_raw = _user_gliss_path(self.project_root)
        if not gliss_raw.exists():
            return False
        try:
            out = QAudioOutput(self)
            try:
                out.setDevice(self.audio_output.device())
            except Exception:
                pass
            out.setVolume(GLISS_VOLUME_PERCENT / 100.0)
            out.setMuted(self._muted)

            p = QMediaPlayer(self)
            p.setAudioOutput(out)
            p.setSource(QUrl.fromLocalFile(str(gliss_raw)))

            self._sfx_pool.append((p, out))

            def _cleanup_when_done(_status: QMediaPlayer.MediaStatus) -> None:
                if _status in (QMediaPlayer.EndOfMedia, QMediaPlayer.InvalidMedia, QMediaPlayer.NoMedia):
                    try:
                        p.stop()
                    except Exception:
                        pass
                    try:
                        self._sfx_pool.remove((p, out))
                    except Exception:
                        self._sfx_pool[:] = [(pp, oo) for (pp, oo) in self._sfx_pool if pp is not p]
                    p.deleteLater()
                    out.deleteLater()

            p.mediaStatusChanged.connect(_cleanup_when_done)
            p.play()
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# SoundTab
# ─────────────────────────────────────────────────────────────────────────────

class SoundTab(QtWidgets.QWidget):
    preview_widget = QtCore.Signal(QtWidgets.QWidget)  # visualizer widget

    def __init__(self, project_root):
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self._shutdown = False

        self._archive_mgr = SoundArchiveManager(self.project_root)
        self.wave = WaveHandler(project_root, archive_mgr=self._archive_mgr, parent=self)

        # Visualizer for Nexus preview
        self._preview = SoundPreviewWidget(self.wave.player, parent=self)
        self._archive_mgr.current_changed.connect(self._on_current_changed)

        # Analysis manager. The project archive lives under:
        #   gallery/user/sounds/appssong/{processed,analysis}
        # Keep this explicit so analysis never drifts back to the old archive path.
        self._analysis = AudioAnalysisManager(self.project_root, parent=self) if AudioAnalysisManager is not None else None
        self._analysis_bootstrapped = False
        self._current_processed = ""
        self._current_analysis_key = ""

        if self._analysis is not None:
            self._analysis.busyChanged.connect(self._on_analysis_busy)
            self._analysis.batchProgress.connect(self._on_analysis_progress)
            self._analysis.analysisReady.connect(self._on_analysis_ready)
            self._analysis.analysisFailed.connect(self._on_analysis_failed)

        self._init_ui()
        self._init_shortcuts()

        # Prime current track if present
        current_track = _user_current_music(self.project_root)
        if current_track.exists():
            self.wave.load_audio(str(current_track))
            self._current_processed = self._read_current_processed_from_manifest()

            stable_path = self._stable_audio_path_for_preview()
            if stable_path is not None:
                try:
                    self._preview.set_audio_file(str(stable_path))
                except Exception:
                    pass

            self._prime_analysis_for_current()

        # Hand to Nexus
        self.preview_widget.emit(self._preview)

        # Warn if SFX missing
        self._warn_if_missing_sfx()

    def _warn_if_missing_sfx(self) -> None:
        misses = []
        if not _user_gliss_path(self.project_root).exists():
            misses.append(str(_user_gliss_path(self.project_root)))
        for i in range(1, 11):
            p = _user_flip_path(self.project_root, i)
            if not p.exists():
                misses.append(str(p))
        if misses:
            try:
                self.status.setText("⚠ Missing sound assets:\n" + "\n".join(misses))
            except Exception:
                pass

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True

        if self._analysis is not None:
            try:
                self._analysis.shutdown()
            except Exception:
                pass
        try:
            self._preview.shutdown()
        except Exception:
            pass
        try:
            self.wave.shutdown()
        except Exception:
            pass
        try:
            self._dropdown.close()
        except Exception:
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────
    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        if self._shutdown:
            return
        self.preview_widget.emit(self._preview)

        if (self._analysis is not None) and (not self._analysis_bootstrapped):
            self._analysis_bootstrapped = True
            # Do not sweep/analyze every archived song just because the tab opened.
            # The current track is primed on demand, and cached tracks load instantly.
            try:
                self._prime_analysis_for_current()
                if not self._analysis.is_busy():
                    self.status.setText("Audio cache ready.")
            except Exception:
                pass

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        # Centered compact control card. The preview is owned by Nexus;
        # this card is intentionally narrow so the Sound tab does not sprawl.
        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(0)
        center_row.addStretch(1)

        self.sound_card = QtWidgets.QFrame(self)
        self.sound_card.setObjectName("soundControlCard")
        self.sound_card.setFixedWidth(620)
        self.sound_card.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Maximum)
        self.sound_card.setStyleSheet(
            "QFrame#soundControlCard {"
            " background:#151821;"
            " border:1px solid #2b3344;"
            " border-radius:14px;"
            "}"
            "QLabel#soundTitle {"
            " color:#e6f7ff;"
            " font-size:18px;"
            " font-weight:800;"
            " letter-spacing:0.5px;"
            "}"
            "QLabel#soundVolumeLabel {"
            " color:#b0e0e6;"
            " font-weight:700;"
            "}"
            "QLabel#soundVolumeValue {"
            " background:#222936;"
            " border:1px solid #3a4558;"
            " border-radius:7px;"
            " padding:3px 8px;"
            " color:#e6e6e6;"
            "}"
            "QPushButton {"
            " background:#1e2230;"
            " border:1px solid #2f3a4d;"
            " border-radius:9px;"
            " color:#e6e6e6;"
            " padding:7px 14px;"
            " min-height:22px;"
            "}"
            "QPushButton:hover {"
            " border-color:#00c8ff;"
            " background:#232b3a;"
            "}"
            "QPushButton:pressed {"
            " background:#10141d;"
            "}"
        )

        card = QVBoxLayout(self.sound_card)
        card.setContentsMargins(24, 18, 24, 20)
        card.setSpacing(14)

        title = QLabel("Sound")
        title.setObjectName("soundTitle")
        title.setAlignment(Qt.AlignCenter)
        card.addWidget(title)

        top_buttons = QHBoxLayout()
        top_buttons.setSpacing(12)
        top_buttons.addStretch(1)

        self.btn_archive = QPushButton("Archive")
        self.btn_archive.setObjectName("btn_sound_archive")
        self.btn_archive.setFixedHeight(34)
        self.btn_archive.setMinimumWidth(126)
        self.btn_archive.setCursor(Qt.PointingHandCursor)

        self.add_btn = QPushButton("🎵 Add Music")
        self.add_btn.setFixedHeight(34)
        self.add_btn.setMinimumWidth(144)
        self.add_btn.setCursor(Qt.PointingHandCursor)

        top_buttons.addWidget(self.btn_archive, 0)
        top_buttons.addWidget(self.add_btn, 0)
        top_buttons.addStretch(1)
        card.addLayout(top_buttons)

        self._dropdown = ArchiveDropdown(self._archive_mgr, parent=self)
        self.btn_archive.clicked.connect(self._open_archive_dropdown)
        self._dropdown.request_release.connect(self.wave.release_current_file_handle)

        volume_label = QLabel("Volume")
        volume_label.setObjectName("soundVolumeLabel")
        volume_label.setAlignment(Qt.AlignCenter)
        card.addWidget(volume_label)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)
        vol_row.addStretch(1)

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        init_percent = int(round(self.wave._music_volume * 100))
        self.vol_slider.setValue(init_percent)
        self.vol_slider.setFixedWidth(360)
        self.vol_slider.setStyleSheet(
            "QSlider::groove:horizontal { height:7px; background:#303744; border-radius:3px; }"
            "QSlider::handle:horizontal { width:16px; height:16px; margin:-5px 0; "
            "background:#00b2b2; border:1px solid #089; border-radius:8px; }"
            "QSlider::sub-page:horizontal { background:#0aa; border-radius:3px; }"
        )

        self.vol_value = QLabel(f"{init_percent}%")
        self.vol_value.setObjectName("soundVolumeValue")
        self.vol_value.setMinimumWidth(48)
        self.vol_value.setAlignment(Qt.AlignCenter)

        vol_row.addWidget(self.vol_slider, 0)
        vol_row.addWidget(self.vol_value, 0)
        vol_row.addStretch(1)
        card.addLayout(vol_row)

        playback_row = QHBoxLayout()
        playback_row.setSpacing(12)
        playback_row.addStretch(1)

        self.playpause_btn = QPushButton("▶️ Play")
        self.startover_btn = QPushButton("⏮️ Start Over")
        self.mute_btn = QPushButton("🔇 Mute")

        for btn in (self.playpause_btn, self.startover_btn, self.mute_btn):
            btn.setFixedHeight(36)
            btn.setMinimumWidth(124)
            btn.setCursor(Qt.PointingHandCursor)
            playback_row.addWidget(btn, 0)

        playback_row.addStretch(1)
        card.addLayout(playback_row)

        self.analysis_progress = QtWidgets.QProgressBar()
        self.analysis_progress.setRange(0, 100)
        self.analysis_progress.setValue(0)
        self.analysis_progress.setTextVisible(True)
        self.analysis_progress.setFormat("Analyzing audio… %p%")
        self.analysis_progress.setFixedHeight(18)
        self.analysis_progress.setStyleSheet(
            "QProgressBar { background:#0d0f14; border:1px solid #2b3344; border-radius:6px; color:#e6e6e6; }"
            "QProgressBar::chunk { background:#00c8ff; border-radius:6px; }"
        )
        self.analysis_progress.hide()
        card.addWidget(self.analysis_progress)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setFont(QtGui.QFont("Consolas", 10))
        self.status.setStyleSheet(
            "QLabel { background-color:#10131a; color:#bbb; border:1px solid #242c3a; "
            "border-radius:8px; padding:8px; }"
        )
        card.addWidget(self.status)

        center_row.addWidget(self.sound_card, 0, Qt.AlignTop | Qt.AlignHCenter)
        center_row.addStretch(1)
        root.addLayout(center_row)
        root.addStretch(1)

        self.add_btn.clicked.connect(self.select_music)
        self.playpause_btn.clicked.connect(self.play_pause_audio)
        self.startover_btn.clicked.connect(self.startover_audio)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self._dropdown.use_requested.connect(self._on_use_from_archive)

        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.vol_slider.sliderReleased.connect(self._on_volume_released)

        self.setAcceptDrops(True)
        self.update_status()

    def _open_archive_dropdown(self):
        gp = self.btn_archive.mapToGlobal(QtCore.QPoint(0, self.btn_archive.height()))
        self._dropdown.show_at(gp)

    def _init_shortcuts(self) -> None:
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key_Space), self, activated=self.play_pause_audio)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key_M), self, activated=self.toggle_mute)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+L"), self, activated=self.select_music)

    # ─────────────────────────────────────────────────────────────────────
    # Stable path policy (preview + analysis use same stable path)
    # ─────────────────────────────────────────────────────────────────────
    def _read_current_processed_from_manifest(self) -> str:
        try:
            manifest = _user_current_manifest(self.project_root)
            if not manifest.exists():
                return ""
            data = json.loads(manifest.read_text(encoding="utf-8"))
            rel = str(data.get("current_processed_rel") or data.get("current_rel") or "")
            return Path(rel).name if rel else ""
        except Exception:
            return ""

    def _processed_path_for(self, processed_filename: str) -> Path:
        return _user_archive_processed(self.project_root) / processed_filename

    def _resolve_manifest_path(self, rel_or_path: str) -> Optional[Path]:
        rel_or_path = str(rel_or_path or "").strip()
        if not rel_or_path:
            return None
        p = Path(rel_or_path)
        if not p.is_absolute():
            p = self.project_root / p
        return p.resolve()

    def _read_current_original_from_manifest(self) -> str:
        """
        Return the current original archive filename/path from current.json.
        Prefer current_original_rel.
        Fall back to current_processed_rel/current_rel only for old manifests.
        """
        try:
            manifest = _user_current_manifest(self.project_root)
            if not manifest.exists():
                return ""
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(
                data.get("current_original_rel")
                or data.get("current_processed_rel")
                or data.get("current_rel")
                or ""
            )
        except Exception:
            return ""

    def _original_path_for_processed(self, processed_filename: str) -> Optional[Path]:
        if not processed_filename:
            return None

        originals = _user_archive_originals(self.project_root)
        exact = originals / processed_filename
        if exact.is_file():
            return exact.resolve()

        stem = Path(processed_filename).stem
        try:
            matches = [p for p in originals.iterdir() if p.is_file() and p.stem == stem]
        except Exception:
            matches = []

        for p in matches:
            if p.suffix.lower() == ".mp3":
                return p.resolve()
        if matches:
            return sorted(matches, key=lambda p: p.name.lower())[0].resolve()
        return None

    def _current_original_path_for_preview(self) -> Optional[Path]:
        """
        Resolve the current original song path from the manifest.
        Normal expected path:
            gallery/user/sounds/appssong/originals/<song>.mp3
        """
        rel = self._read_current_original_from_manifest()
        p = self._resolve_manifest_path(rel)
        if p is not None and p.is_file() and "originals" in p.parts:
            return p

        if self._current_processed:
            p = self._original_path_for_processed(self._current_processed)
            if p is not None and p.is_file():
                return p

        processed_name = self._read_current_processed_from_manifest()
        if processed_name:
            p = self._original_path_for_processed(processed_name)
            if p is not None and p.is_file():
                return p

        return None

    def _stable_audio_path_for_preview(self) -> Optional[Path]:
        """
        Priority:
          1. gallery/user/sounds/appssong/originals/<current song>.mp3
          2. gallery/user/sounds/appssong/processed/<current song>.mp3
          3. gallery/user/sounds/music.mp3
        """
        p0 = self._current_original_path_for_preview()
        if p0 is not None and p0.exists():
            return p0

        if self._current_processed:
            p = self._processed_path_for(self._current_processed)
            if p.exists():
                return p.resolve()

        processed_name = self._read_current_processed_from_manifest()
        if processed_name:
            p = self._processed_path_for(processed_name)
            if p.exists():
                return p.resolve()

        p2 = _user_current_music(self.project_root)
        if p2.exists():
            return p2.resolve()
        return None

    def _push_analysis_payload(self, payload: Optional[dict]) -> None:
        try:
            self._preview.set_analysis_payload(payload)
        except Exception:
            try:
                vis = self._preview.visualizer()
                if hasattr(vis, "set_analysis_payload"):
                    vis.set_analysis_payload(payload)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _prime_analysis_for_current(self) -> None:
        if self._analysis is None:
            return

        stable = self._stable_audio_path_for_preview()
        if stable is None or not stable.exists():
            print("[Sound] Preview source missing.")
            return

        stable = stable.resolve()
        key = str(stable)
        self._current_analysis_key = key

        try:
            original = self._current_original_path_for_preview()
            if original is not None:
                print(f"[Sound] Current original: {original.resolve()}")
        except Exception:
            pass

        print(f"[Sound] Playback source: {_user_current_music(self.project_root).resolve()}")
        print(f"[Sound] Preview source: {stable}")
        print(f"[Sound] Analysis source: {stable}")

        try:
            cache_path = self._analysis.analysis_file_for_debug(stable)
            print(f"[Sound] Analysis cache path: {cache_path}")
        except Exception:
            pass

        cached = self._analysis.load_cached(stable)
        print(f"[Sound] Cache loaded: {'yes' if cached is not None else 'no'}")

        if cached is not None:
            self._push_analysis_payload(cached)
        else:
            self._push_analysis_payload(None)

        print(f"[Sound] Analysis started: {stable}")
        try:
            self._analysis.ensure_analyzed(stable, priority=True)
        except Exception as e:
            print(f"[Sound] Analysis failed: {stable} | {e!r}")

    # ─────────────────────────────────────────────────────────────────────
    # Analysis callbacks
    # ─────────────────────────────────────────────────────────────────────
    def _on_analysis_busy(self, busy: bool) -> None:
        try:
            if busy:
                try:
                    self.status.setText("⏳ Building audio analysis cache…")
                except Exception:
                    pass
                self.analysis_progress.show()
                self.analysis_progress.setValue(0)
            else:
                self.analysis_progress.hide()
                try:
                    t = self.status.text()
                except Exception:
                    t = ""
                if "Analysis failed" not in t:
                    self.status.setText("Audio cache ready.")
        except Exception:
            pass

    def _on_analysis_progress(self, done: int, total: int, current_name: str, current_pct: int) -> None:
        try:
            t = max(1, int(total))
            d = max(0, int(done))
            cp = max(0, min(100, int(current_pct)))
            overall = int(round(((d + (cp / 100.0)) / t) * 100.0))
            overall = max(0, min(100, overall))
            self.analysis_progress.setValue(overall)
            if current_name:
                self.analysis_progress.setFormat(f"Analyzing {current_name}… {overall}%")
            else:
                self.analysis_progress.setFormat(f"Analyzing audio… {overall}%")
        except Exception:
            pass

    def _on_analysis_ready(self, path_key: str, payload: dict) -> None:
        try:
            print(f"[Sound] Analysis ready: {path_key}")
            if self._current_analysis_key and str(path_key) == self._current_analysis_key:
                self._push_analysis_payload(payload)
                self.status.setText("Audio analysis ready.")
            else:
                print(f"[Sound] Ignored analysis payload. Current key: {self._current_analysis_key}")
        except Exception as e:
            print(f"[Sound] Analysis ready handler failed: {e!r}")

    def _on_analysis_failed(self, path_key: str, msg: str) -> None:
        print(f"[Sound] Analysis failed: {path_key} | {msg}")
        try:
            if self._current_analysis_key and (str(path_key) == self._current_analysis_key):
                self._push_analysis_payload(None)
            # Playback and analysis are separate systems. Qt may play a file even
            # when the offline analyzer cannot decode it. Do not stop playback.
            self.status.setText(f"⚠️ Analysis failed: {msg}\nPlayback can still continue.")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Current selection changes
    # ─────────────────────────────────────────────────────────────────────
    def _on_current_changed(self, processed_filename: str) -> None:
        self._current_processed = processed_filename or ""

        # Live playback is always music.mp3 (copy-mode)
        music_path = _user_current_music(self.project_root)
        if music_path.exists():
            self.wave.load_audio(str(music_path))

        # Preview must use stable path (processed if possible)
        stable = self._stable_audio_path_for_preview()
        if stable is not None:
            try:
                self._preview.set_audio_file(str(stable))
            except Exception:
                pass

        # Prime analysis and keep keys aligned
        self._prime_analysis_for_current()

        self.playpause_btn.setText("▶️ Play")
        self.status.setText(f"✅ Current set: {processed_filename or '(cleared)'}")

    def _on_use_from_archive(self, processed_filename: str) -> None:
        self.wave.release_current_file_handle()
        try:
            self._archive_mgr.set_current(processed_filename)
        except Exception as e:
            QMessageBox.critical(self, "Archive Error", str(e))
            return

    # ─────────────────────────────────────────────────────────────────────
    # Volume
    # ─────────────────────────────────────────────────────────────────────
    def _on_volume_changed(self, val: int) -> None:
        self.vol_value.setText(f"{val}%")
        self.wave.audio_output.setVolume(max(0, min(100, val)) / 100.0)

    def _on_volume_released(self) -> None:
        v = self.vol_slider.value()
        self.wave.save_music_volume(v)

    # ─────────────────────────────────────────────────────────────────────
    # UI actions
    # ─────────────────────────────────────────────────────────────────────
    def update_status(self):
        music_path = _user_current_music(self.project_root)
        if music_path.exists():
            self.status.setText(f"✅ Music loaded: {MUSIC_FILE}")
        else:
            self.status.setText("No audio loaded.")

    def select_music(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Music File", "", "Audio Files (*.mp3 *.wav *.ogg *.aac *.m4a *.flac)"
        )
        if path:
            self._process_file(path)

    def play_pause_audio(self):
        if self.wave.is_playing():
            self.wave.pause_audio()
            self.playpause_btn.setText("▶️ Play")
            self.status.setText("⏸️ Paused.")
            return

        if self.wave.player.source().isEmpty():
            music_path = _user_current_music(self.project_root)
            if music_path.exists():
                self.wave.load_audio(str(music_path))

        stable = self._stable_audio_path_for_preview()
        if stable is not None:
            try:
                self._preview.set_audio_file(str(stable))
            except Exception:
                pass
            self._prime_analysis_for_current()

        self.wave.play_audio()
        self.playpause_btn.setText("⏸️ Pause")
        self.status.setText("🎧 Playing")

    def startover_audio(self):
        music_path = _user_current_music(self.project_root)
        if music_path.exists():
            self.wave.load_audio(str(music_path))

        stable = self._stable_audio_path_for_preview()
        if stable is not None:
            try:
                self._preview.set_audio_file(str(stable))
            except Exception:
                pass
            self._prime_analysis_for_current()

        self.wave.start_over_audio()
        self.playpause_btn.setText("⏸️ Pause")
        self.status.setText("⏮️ Start over.")

    def toggle_mute(self):
        """Mute/unmute audio output without pausing playback or changing position."""
        muted = self.wave.toggle_mute()
        self.mute_btn.setText("🔊 Unmute" if muted else "🔇 Mute")

        # Mute must not alter the play/pause state. Keep the visible playback
        # button synced to the actual QMediaPlayer state.
        if self.wave.is_playing():
            self.playpause_btn.setText("⏸️ Pause")
            self.status.setText("🔇 Muted — playback continues." if muted else "🎧 Playing")
        else:
            self.playpause_btn.setText("▶️ Play")
            self.status.setText("🔇 Muted." if muted else "Audio unmuted.")

    def open_sounds_folder(self):
        _open_folder(self.project_root / USER_SOUNDS_DIR)

    # Drag & drop
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent):
        handled = False
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(tuple(VALID_AUDIO_EXTS)):
                self._process_file(path)
                handled = True
                break
        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _process_file(self, path):
        # IMPORTANT: release current handle before writing music.mp3 (Windows)
        self.wave.release_current_file_handle()

        valid, msg = self.wave.validate_audio(path)
        if not valid:
            self.status.setText(f"❌ {msg}")
            QMessageBox.warning(self, "Invalid Audio", msg)
            return
        try:
            res = self._archive_mgr.add_song_from_path(Path(path))
        except Exception as e:
            self.status.setText(f"❌ Failed to process audio: {e}")
            QMessageBox.critical(self, "Conversion Error", str(e))
            return

        if res.action == "error":
            self.status.setText(f"❌ {res.message}")
            QMessageBox.critical(self, "Audio Import Error", res.message)
            return

        if res.action == "canceled":
            self.status.setText("Import canceled.")
            return

        # Playback always points to live music.mp3
        music_path = _user_current_music(self.project_root)
        if music_path.exists():
            self.wave.load_audio(str(music_path))

        # Preview/analyzer should use stable processed path when possible
        self._current_processed = res.archive_processed or self._read_current_processed_from_manifest()
        stable = self._stable_audio_path_for_preview()
        if stable is not None:
            try:
                self._preview.set_audio_file(str(stable))
            except Exception:
                pass

        self._prime_analysis_for_current()

        try:
            self.status.setText(f"✅ {res.message}")
        except Exception:
            self.status.setText(f"✅ Music added: {music_path.name}")

        self.playpause_btn.setText("▶️ Play")
