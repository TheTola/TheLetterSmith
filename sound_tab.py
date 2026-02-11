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

# --- Visualizer (shared preview widget) ---
from sound_preview import SoundPreviewWidget

# --- Analysis (offline MP3 -> cached) ---
try:
    from sound_analyzer import AudioAnalysisManager
except Exception:
    AudioAnalysisManager = None  # type: ignore

# --- Audio utils ---
from mutagen import File as MutagenFile
from pydub import AudioSegment, effects
import pydub

# Optional ffmpeg/ffprobe binding if not in PATH (uses env first, then common local)
_FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "")
_FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "")
if _FFMPEG_BIN and os.path.exists(_FFMPEG_BIN):
    pydub.AudioSegment.converter = _FFMPEG_BIN  # type: ignore[attr-defined]
if _FFPROBE_BIN and os.path.exists(_FFPROBE_BIN):
    pydub.AudioSegment.ffprobe = _FFPROBE_BIN  # type: ignore[attr-defined]

# --- Project config ---
from config import (
    SETTINGS_FILE,
    MAX_AUDIO_MB,
    STARTING_VOLUME,
    MUSIC_FILE,
    GLISS_FILE,
    FLIP_PREFIX,
    FLIP_COUNT,
    USER_SOUNDS_DIR,
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


def _user_archive_root(project_root: Path) -> Path:
    # gallery/user/sounds/appssong
    return _user_sounds_dir(project_root) / "appssong"


def _user_archive_originals(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/originals
    return _user_archive_root(project_root) / "originals"


def _user_archive_processed(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/processed
    return _user_archive_root(project_root) / "processed"


def _user_archive_analysis(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/analysis
    return _user_archive_root(project_root) / "analysis"


def _user_current_manifest(project_root: Path) -> Path:
    # gallery/user/sounds/appssong/current.json
    return _user_archive_root(project_root) / "current.json"


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



    # Names-only listing (newest first)
    def list_archive(self) -> List[str]:
        names: List[str] = []
        if not self.dir_processed.exists():
            return names
        items = sorted(self.dir_processed.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in items:
            if p.is_file() and p.suffix.lower() == ".mp3":
                names.append(p.name)
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
            audio = AudioSegment.from_file(str(source))
            audio = effects.compress_dynamic_range(audio, threshold=-18.0, ratio=2.0, attack=5.0, release=50.0)
            normalized = effects.normalize(audio)
            tmp_mp3 = processed_path.with_suffix(".mp3.tmp")
            normalized.export(tmp_mp3, format="mp3")
            os.replace(tmp_mp3, processed_path)
        except Exception as e:
            if source.suffix.lower() == ".mp3":
                try:
                    _atomic_copy(source, processed_path)
                except Exception as ee:
                    return AddResult("error", final_original_name, None, incoming_size, f"Copy fallback failed: {ee!r}")
            else:
                return AddResult("error", final_original_name, None, incoming_size, f"Failed to normalize/export: {e!r}")

        # Set current (Windows-safe with short retries)
        self._set_current_by_copy(processed_path)
        self._write_manifest(processed_name, _file_size(processed_path))

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
        self._write_manifest(processed_filename, _file_size(src))
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
            current_rel = data.get("current_rel", "")
            expected_rel = f"{USER_SOUNDS_DIR}/appssong/processed/{processed_filename}"
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
                _atomic_copy(src, dst)
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

    def _write_manifest(self, processed_name: str, size: int) -> None:
        data = {
            "current_rel": f"gallery/user/sounds/appssong/processed/{processed_name}",
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
# Archive dropdown (movable + clamped + names-only + delete mode)
# ─────────────────────────────────────────────────────────────────────────────

class ArchiveDropdown(QtWidgets.QFrame):
    use_requested = QtCore.Signal(str)
    request_release = QtCore.Signal()  # ask parent to release player handle before file ops

    def __init__(self, mgr: SoundArchiveManager, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._mgr = mgr
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._delete_mode = False
        self._drag_offset = QtCore.QPoint(0, 0)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "#panel { background: rgba(15,17,22,240); border-radius: 12px; border: 1px solid #2b3344; }"
            "QLabel { color: #dcdfe4; }"
            "QScrollArea { border: none; background: transparent; }"
            "QPushButton { background: #1b1f2a; border: 1px solid #2a2f3e; border-radius: 8px; padding: 5px 8px; color: #e6e6e6; }"
            "QPushButton:hover { border-color: #00c8ff; }"
        )

        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Archive")
        title.setStyleSheet("font-weight:600; font-size:14px;")
        btn_refresh = QtWidgets.QPushButton("Refresh")
        btn_refresh.setFixedHeight(26)
        btn_refresh.clicked.connect(self.refresh)

        self.btn_delete_mode = QtWidgets.QPushButton("–")
        self.btn_delete_mode.setFixedSize(26, 26)
        self.btn_delete_mode.setToolTip("Toggle delete mode")
        self.btn_delete_mode.clicked.connect(self._toggle_delete_mode)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.btn_delete_mode)
        header.addWidget(btn_refresh)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_holder = QtWidgets.QWidget()
        self._list_layout = QtWidgets.QVBoxLayout(self._list_holder)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._scroll.setWidget(self._list_holder)

        inner.addLayout(header)
        inner.addWidget(self._scroll, 1)
        outer.addWidget(panel)

        self._opacity = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._anim = QtCore.QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)

        self._mgr.archive_changed.connect(self.refresh)
        self.refresh()

    def _available_geom_at(self, global_pos: QtCore.QPoint) -> QtCore.QRect:
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if not screen:
            screen = QtGui.QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _clamp_to_screen(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        geom = self._available_geom_at(pos)
        w, h = self.sizeHint().width(), self.sizeHint().height()
        x = max(geom.left(), min(pos.x(), geom.right() - w))
        y = max(geom.top(),  min(pos.y(), geom.bottom() - h))
        return QtCore.QPoint(x, y)

    def show_at(self, global_pos: QtCore.QPoint) -> None:
        p = self._clamp_to_screen(global_pos)
        self.move(p)
        self._opacity.setOpacity(0.0)
        super().show()
        self._anim.start()

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
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for name in self._mgr.list_archive():
            self._list_layout.addWidget(self._make_row(name))
        self._list_layout.addStretch(1)

    def _toggle_delete_mode(self) -> None:
        self._delete_mode = not self._delete_mode
        self.btn_delete_mode.setStyleSheet("QPushButton { border-color:#ff4d4f; }" if self._delete_mode else "")
        self.refresh()

    def _make_row(self, name: str) -> QtWidgets.QWidget:
        row = QtWidgets.QFrame()
        row.setObjectName("row")
        row.setStyleSheet("#row { background: #121520; border: 1px solid #2b3344; border-radius: 8px; }")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

        lbl_name = QtWidgets.QLabel(name)
        lbl_name.setToolTip(name)

        btn_use = QtWidgets.QPushButton("Use")
        btn_use.setFixedHeight(26)
        btn_use.clicked.connect(lambda: self.use_requested.emit(name))

        h.addWidget(lbl_name, 1)

        if self._delete_mode:
            btn_del = QtWidgets.QPushButton("× Delete")
            btn_del.setFixedHeight(26)
            btn_del.setStyleSheet("QPushButton { border-color:#ff4d4f; }")
            btn_del.clicked.connect(lambda: self._confirm_delete(name))
            h.addWidget(btn_del, 0)

        h.addStretch(1)
        h.addWidget(btn_use, 0)
        return row

    def _confirm_delete(self, name: str) -> None:
        dlg = ConfirmDialog(f"Are you sure you want to remove “{name}” from archive?", self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.request_release.emit()
            ok = self._mgr.delete_processed(name)
            if not ok:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Delete failed.")
            self.refresh()


# ─────────────────────────────────────────────────────────────────────────────
# WaveHandler (player + persisted volume)
# Gliss policy: ALWAYS play the ORIGINAL file at fixed 10% volume.
# Supports overlapping one-shot SFX so a new play never cuts a previous one.
# ─────────────────────────────────────────────────────────────────────────────

class WaveHandler(QtCore.QObject):
    def __init__(self, project_root, archive_mgr: Optional[SoundArchiveManager] = None):
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.settings_path = self.project_root / SETTINGS_FILE

        # Music player (persistent)
        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)

        # One-shot SFX pool for gliss plays
        self._sfx_pool: List[Tuple[QMediaPlayer, QAudioOutput]] = []

        # Volume: load saved; else use STARTING_VOLUME from config
        self._music_volume = self._load_music_volume()  # 0.0..1.0
        self.audio_output.setVolume(self._music_volume)

        self._archive = archive_mgr
        self._muted = False

    def _read_settings(self) -> Dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_settings(self, settings: Dict) -> None:
        try:
            self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_music_volume(self) -> float:
        settings = self._read_settings()
        if "music_volume" in settings:
            try:
                v = max(0, min(100, int(settings["music_volume"])))
                return v / 100.0
            except Exception:
                pass
        try:
            v = max(0, min(100, int(STARTING_VOLUME)))
        except Exception:
            v = 100
        return v / 100.0

    def save_music_volume(self, volume_percent: int) -> None:
        v = max(0, min(100, int(volume_percent)))
        settings = self._read_settings()
        settings["music_volume"] = v
        settings["starting_volume"] = v
        self._write_settings(settings)

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
        self.player.setSource(QUrl.fromLocalFile(path))

    def play_audio(self) -> None:
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

    def play_gliss(self) -> bool:
        gliss_raw = _user_gliss_path(self.project_root)
        if not gliss_raw.exists():
            return False
        try:
            out = QAudioOutput()
            try:
                out.setDevice(self.audio_output.device())
            except Exception:
                pass
            out.setVolume(GLISS_VOLUME_PERCENT / 100.0)
            out.setMuted(self._muted)

            p = QMediaPlayer()
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

            p.mediaStatusChanged.connect(_cleanup_when_done)
            p.play()
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# SoundTab
# ─────────────────────────────────────────────────────────────────────────────

class SoundTab(QtWidgets.QWidget):
    preview_movie = QtCore.Signal(QtGui.QMovie)        # legacy signal (kept)
    preview_widget = QtCore.Signal(QtWidgets.QWidget)  # visualizer widget

    def __init__(self, project_root):
        super().__init__()
        self.project_root = Path(project_root).resolve()

        self._archive_mgr = SoundArchiveManager(self.project_root)
        self.wave = WaveHandler(project_root, archive_mgr=self._archive_mgr)

        # Visualizer for Nexus preview
        self._preview = SoundPreviewWidget(self.wave.player, parent=self)
        self._archive_mgr.current_changed.connect(self._on_current_changed)

        # Analysis manager (override its default dirs to appssong paths)
        self._analysis = AudioAnalysisManager(self.project_root, parent=self) if AudioAnalysisManager is not None else None
        self._analysis_bootstrapped = False
        self._current_processed = ""
        self._current_analysis_key = ""

        if self._analysis is not None:
            try:
                self._analysis.ensure_analyzed(stable, priority=True)

                _user_archive_analysis(self.project_root).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

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

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────
    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        self.preview_widget.emit(self._preview)

        if (self._analysis is not None) and (not self._analysis_bootstrapped):
            self._analysis_bootstrapped = True
            try:
                self.status.setText("⏳ Building audio analysis cache…")
            except Exception:
                pass

            # Kick sweep
            try:
                self._analysis.enqueue_missing()
            except Exception:
                pass

            try:
                if not self._analysis.is_busy():
                    self.status.setText("Audio cache ready.")
            except Exception:
                pass

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Sound")
        title.setStyleSheet("font-weight:600; font-size:14px;")
        self.btn_archive = QPushButton("Archive")
        self.btn_archive.setObjectName("btn_sound_archive")
        self.btn_archive.setFixedHeight(26)
        self.btn_archive.setCursor(Qt.PointingHandCursor)
        self.btn_archive.setStyleSheet(
            "QPushButton#btn_sound_archive { background:#1e2230; border:1px solid #2b3344; "
            "border-radius: 8px; color:#e6e6e6; padding: 3px 10px; }"
            "QPushButton#btn_sound_archive:hover { border-color:#00c8ff; }"
        )
        self._dropdown = ArchiveDropdown(self._archive_mgr, parent=self)
        self.btn_archive.clicked.connect(self._open_archive_dropdown)
        self._dropdown.request_release.connect(self.wave.release_current_file_handle)

        header.addWidget(title, 1)
        header.addWidget(self.btn_archive, 0)
        layout.addLayout(header)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)

        lbl_vol = QLabel("Volume")
        lbl_vol.setStyleSheet("QLabel{color:#b0e0e6; font-weight:600;}")

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        init_percent = int(round(self.wave._music_volume * 100))
        self.vol_slider.setValue(init_percent)
        self.vol_slider.setFixedWidth(260)
        self.vol_slider.setStyleSheet(
            "QSlider::groove:horizontal { height:6px; background:#333; border-radius:3px; }"
            "QSlider::handle:horizontal { width:14px; height:14px; margin:-5px 0; "
            "background:#00b2b2; border:1px solid #089; border-radius:7px; }"
            "QSlider::sub-page:horizontal { background:#0aa; border-radius:3px; }"
        )

        self.vol_value = QLabel(f"{init_percent}%")
        self.vol_value.setMinimumWidth(40)
        self.vol_value.setAlignment(Qt.AlignCenter)
        self.vol_value.setStyleSheet(
            "QLabel{background:#222; border:1px solid #444; border-radius:6px; padding:2px 6px;}"
        )

        vol_row.addWidget(lbl_vol, 0)
        vol_row.addWidget(self.vol_slider, 0)
        vol_row.addWidget(self.vol_value, 0)
        vol_row.addStretch(1)
        layout.addLayout(vol_row)

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(12)
        self.add_btn = QPushButton("🎵 Add Music")
        self.open_btn = QPushButton("📂 Open Sounds Folder")
        btn_row1.addWidget(self.add_btn)
        btn_row1.addWidget(self.open_btn)
        btn_row1.addStretch(1)
        layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(12)
        self.playpause_btn = QPushButton("▶️ Play")
        self.startover_btn = QPushButton("⏮️ Start Over")
        self.mute_btn = QPushButton("🔇 Mute")
        btn_row2.addWidget(self.playpause_btn)
        btn_row2.addWidget(self.startover_btn)
        btn_row2.addWidget(self.mute_btn)
        btn_row2.addStretch(1)
        layout.addLayout(btn_row2)

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
        layout.addWidget(self.analysis_progress)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignLeft)
        self.status.setFont(QtGui.QFont("Consolas", 10))
        self.status.setStyleSheet(
            "QLabel { background-color: #111; color: #bbb; border-top: 1px solid #00d0ff; padding: 8px; }"
        )
        layout.addWidget(self.status)

        self.add_btn.clicked.connect(self.select_music)
        self.open_btn.clicked.connect(self.open_sounds_folder)
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
        self._dropdown.refresh()
        self._dropdown.show_at(gp)

    def _init_shortcuts(self) -> None:
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key_Space), self, activated=self.play_pause_audio)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key_M), self, activated=self.toggle_mute)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+L"), self, activated=self.select_music)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+O"), self, activated=self.open_sounds_folder)

    # ─────────────────────────────────────────────────────────────────────
    # Stable path policy (preview + analysis use same stable path)
    # ─────────────────────────────────────────────────────────────────────
    def _read_current_processed_from_manifest(self) -> str:
        try:
            manifest = _user_current_manifest(self.project_root)
            if not manifest.exists():
                return ""
            data = json.loads(manifest.read_text(encoding="utf-8"))
            rel = str(data.get("current_rel", ""))
            return Path(rel).name if rel else ""
        except Exception:
            return ""

    def _processed_path_for(self, processed_filename: str) -> Path:
        return _user_archive_processed(self.project_root) / processed_filename

    def _stable_audio_path_for_preview(self) -> Optional[Path]:
        """
        Rule:
          - If we have a processed archive filename and it exists, use it.
          - Else fall back to gallery/user/sounds/music.mp3
        """
        if self._current_processed:
            p = self._processed_path_for(self._current_processed)
            if p.exists():
                return p

        p2 = _user_current_music(self.project_root)
        if p2.exists():
            return p2
        return None

    def _push_analysis_payload(self, payload: dict) -> None:
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
            return

        key = str(stable.resolve())
        self._current_analysis_key = key

        # Apply cached instantly
        try:
            cached = self._analysis.load_cached(stable)
            if cached is not None:
                self._push_analysis_payload(cached)
        except Exception:
            pass

        # Ensure analysis exists in background
        try:
            self._analysis.ensure_analyzed(stable, priority=True)
        except Exception:
            pass

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
            if self._current_analysis_key and (str(path_key) == self._current_analysis_key):
                self._push_analysis_payload(payload)
        except Exception:
            pass

    def _on_analysis_failed(self, _path_key: str, msg: str) -> None:
        try:
            self.status.setText(f"⚠️ Analysis failed: {msg}")
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
        self._on_current_changed(processed_filename)

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
        muted = self.wave.toggle_mute()
        self.mute_btn.setText("🔊 Unmute" if muted else "🔇 Mute")
        self.update_status()

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
