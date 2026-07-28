# ===============================
# File: command.py
# Purpose: Erase/Reset command for eLetter
#
# Fixes:
# 1) Recipient/title not clearing (UI + settings)
# 2) Music still plays after erase (WaveHandler player not parented + archive manifest fallback)
#
# Guarantees after reset:
# - settings.json:
#     recipient_name = ""
#     recipient_title = ""
#     music_file = ""
#     last_audio = "none"
#     starting_volume = 50
#     music_volume = 50
# - deletes:
#     gallery/user/sounds/music.mp3
#     gallery/sounds/music.mp3   (if it exists in your build)
#     gallery/user/sounds/appssong/current.json   (critical)
# - does NOT delete:
#     glissando.mp3
#     flip1..flip10.mp3
#     appssong/originals, processed, analysis
# ===============================

from __future__ import annotations

import json
import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

__all__ = [
    "CommandTab",
    "confirm_and_reset",
    "reset_everything",
]

# ─────────────────────────────────────────────────────────────────────────────
# Config (prefer real project config; safe fallbacks)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from config import (
        SETTINGS_FILE,
        PUBLISHED_PAGE_URL_KEY,
        USER_PAGES_DIR,
        USER_MESSAGE_DIR,
        USER_SOUNDS_DIR,
        MUSIC_FILE,
    )
except Exception:
    SETTINGS_FILE = "settings.json"
    PUBLISHED_PAGE_URL_KEY = "published_page_url"
    USER_PAGES_DIR = "gallery/user/pages"
    USER_MESSAGE_DIR = "gallery/user/message"
    USER_SOUNDS_DIR = "gallery/user/sounds"
    MUSIC_FILE = "music.mp3"


# ─────────────────────────────────────────────────────────────────────────────
# Root resolver
# ─────────────────────────────────────────────────────────────────────────────
def app_root() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cwd = Path.cwd()
        if (cwd / "gallery").exists() or (cwd / SETTINGS_FILE).exists():
            return cwd
        return Path(base)

    here = Path(__file__).resolve()
    for up in (here.parent, here.parent.parent, here.parent.parent.parent):
        if (up / SETTINGS_FILE).exists() or (up / "gallery").exists():
            return up
    return here.parent


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────
def _safe_clear_dir_contents(dir_path: Path) -> Tuple[int, int]:
    files_deleted = 0
    dirs_deleted = 0
    if not dir_path.exists() or not dir_path.is_dir():
        return files_deleted, dirs_deleted

    for entry in dir_path.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink(missing_ok=True)
                files_deleted += 1
            elif entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                dirs_deleted += 1
        except Exception:
            pass

    return files_deleted, dirs_deleted


def _safe_delete_file(path: Path) -> int:
    try:
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
            return 1
    except Exception:
        pass
    return 0


def _read_settings(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_settings(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# UI / runtime hooks (this is the part that actually fixes the audio + inputs)
# ─────────────────────────────────────────────────────────────────────────────
def _get_nexus_window(parent: Optional[QtWidgets.QWidget]) -> Optional[QtWidgets.QWidget]:
    if parent is None:
        return None
    try:
        return parent.window()
    except Exception:
        return None


def _hard_stop_sound_system(win: Optional[QtWidgets.QWidget]) -> None:
    """
    SoundTab uses WaveHandler.player = QMediaPlayer() with NO parent.
    findChildren() will NOT find it. So we must reach it through Nexus.
    """
    if win is None:
        return

    # Stop SoundTab WaveHandler music player (and detach source)
    try:
        st = getattr(win, "sound_tab", None)
        if st is not None and hasattr(st, "wave"):
            wave = getattr(st, "wave", None)
            if wave is not None and hasattr(wave, "release_current_file_handle"):
                wave.release_current_file_handle()
    except Exception:
        pass

    # If preview widget uses another player, stop it too (best-effort)
    try:
        st = getattr(win, "sound_tab", None)
        if st is not None and hasattr(st, "_preview"):
            pv = getattr(st, "_preview", None)
            # Some implementations store player as pv._player
            p = getattr(pv, "_player", None)
            if p is not None:
                try:
                    p.stop()
                except Exception:
                    pass
                try:
                    p.setSource(QUrl())
                except Exception:
                    pass
    except Exception:
        pass


def _force_soundtab_no_audio(win: Optional[QtWidgets.QWidget]) -> None:
    """
    After wiping files/manifests, force SoundTab UI to reflect "no audio"
    and clear its internal current selection so Play cannot silently use archive.
    """
    if win is None:
        return
    try:
        st = getattr(win, "sound_tab", None)
        if st is None:
            return

        # Clear the "current processed" pointer and refresh preview/analyzer
        if hasattr(st, "_on_current_changed"):
            try:
                st._on_current_changed("")  # type: ignore[attr-defined]
            except Exception:
                pass

        # Reset visible UI labels if present
        try:
            if hasattr(st, "playpause_btn"):
                st.playpause_btn.setText("▶️ Play")
        except Exception:
            pass
        try:
            if hasattr(st, "status"):
                st.status.setText("No audio loaded.")
        except Exception:
            pass
    except Exception:
        pass


def _clear_message_tab_inputs(win: Optional[QtWidgets.QWidget]) -> None:
    """
    The Message tab fields are not guaranteed to have objectNames,
    so clear them explicitly via message_tab.title_input/name_input if present.
    """
    if win is None:
        return
    try:
        mt = getattr(win, "message_tab", None)
        if mt is None:
            return

        # Clear UI inputs
        try:
            if hasattr(mt, "title_input"):
                mt.title_input.setText("")  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if hasattr(mt, "name_input"):
                mt.name_input.setText("")  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if hasattr(mt, "set_published_page_url"):
                mt.set_published_page_url("", persist=False, announce=False)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if hasattr(mt, "current_html"):
                mt.current_html = ""  # type: ignore[attr-defined]
            if hasattr(mt, "_content_has_intentional_formatting"):
                mt._content_has_intentional_formatting = False  # type: ignore[attr-defined]
            if hasattr(mt, "_update_message_summary"):
                mt._update_message_summary("")  # type: ignore[attr-defined]
        except Exception:
            pass

        # Clear its in-memory settings dict so it won't re-save old values later
        try:
            if hasattr(mt, "settings") and isinstance(mt.settings, dict):  # type: ignore[attr-defined]
                mt.settings["recipient_title"] = ""
                mt.settings["recipient_name"] = ""
                mt.settings[PUBLISHED_PAGE_URL_KEY] = ""
        except Exception:
            pass

        # Force it to write the cleared state if it has a saver
        try:
            if hasattr(mt, "_save_settings"):
                mt._save_settings()  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        pass


def _reset_settings_on_disk(root: Path) -> None:
    settings_path = (root / SETTINGS_FILE).resolve()
    data = _read_settings(settings_path)

    # Explicitly blank (don’t just pop; other code expects keys to exist)
    data["recipient_name"] = ""
    data["recipient_title"] = ""
    data[PUBLISHED_PAGE_URL_KEY] = ""

    # Volumes to 50%
    data["starting_volume"] = 50
    data["music_volume"] = 50

    # Neutralize music selection (prevents accidental reload)
    data["music_file"] = ""
    data["last_audio"] = "none"

    _write_settings(settings_path, data)


def _delete_music_and_manifest(root: Path) -> int:
    """
    Deletes:
      - gallery/user/sounds/music.mp3
      - gallery/sounds/music.mp3 (if present)
      - gallery/user/sounds/appssong/current.json   <-- critical
    """
    total = 0

    user_snd_dir = (root / USER_SOUNDS_DIR).resolve()
    user_music = (user_snd_dir / MUSIC_FILE).resolve()

    runtime_music = (root / "gallery" / "sounds" / MUSIC_FILE).resolve()  # optional location

    current_manifest = (user_snd_dir / "appssong" / "current.json").resolve()

    total += _safe_delete_file(user_music)
    total += _safe_delete_file(runtime_music)
    total += _safe_delete_file(current_manifest)

    return total


# ─────────────────────────────────────────────────────────────────────────────
# Public: reset action
# ─────────────────────────────────────────────────────────────────────────────
def reset_everything(*, parent: Optional[QtWidgets.QWidget] = None) -> Tuple[int, int]:
    root = app_root()

    pages_dir = (root / USER_PAGES_DIR).resolve()
    msg_dir = (root / USER_MESSAGE_DIR).resolve()

    pages_dir.mkdir(parents=True, exist_ok=True)
    msg_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_dirs = 0

    win = _get_nexus_window(parent)

    # 1) Stop audio FIRST (buffering + unparented player)
    _hard_stop_sound_system(win)

    # 2) Wipe user pages + message
    f, d = _safe_clear_dir_contents(pages_dir)
    total_files += f
    total_dirs += d

    f, d = _safe_clear_dir_contents(msg_dir)
    total_files += f
    total_dirs += d

    # 3) Delete music.mp3 AND clear current.json (prevents archive fallback play)
    total_files += _delete_music_and_manifest(root)

    # 4) Reset settings.json on disk
    _reset_settings_on_disk(root)

    # 5) Clear live UI fields so the app visually resets immediately
    _clear_message_tab_inputs(win)

    # 6) Force SoundTab to "no audio loaded" state
    _force_soundtab_no_audio(win)

    return total_files, total_dirs


# ─────────────────────────────────────────────────────────────────────────────
# Reset flow continues below
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Frameless confirm dialog (no title bar; only Yes/No)
# ─────────────────────────────────────────────────────────────────────────────
class _ConfirmDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            """
            QFrame#panel {
                background: rgba(15, 17, 22, 246);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }
            QLabel {
                color: #e6e6e6;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton {
                background: rgba(27, 31, 42, 1.0);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 10px;
                padding: 8px 14px;
                color: #e6e6e6;
                font-weight: 700;
                min-width: 86px;
            }
            QPushButton:hover { border-color: rgba(255, 77, 79, 0.85); }
            QPushButton#danger { border-color: rgba(255, 77, 79, 0.55); }
            QPushButton#danger:hover { border-color: rgba(255, 77, 79, 1.0); }
        """
        )

        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(18, 16, 18, 14)
        inner.setSpacing(12)

        label = QtWidgets.QLabel("Are you sure? This will erase everything.")
        label.setWordWrap(True)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        btn_no = QtWidgets.QPushButton("No")
        btn_yes = QtWidgets.QPushButton("Yes")
        btn_yes.setObjectName("danger")
        row.addWidget(btn_no)
        row.addWidget(btn_yes)

        inner.addWidget(label)
        inner.addLayout(row)

        outer.addWidget(panel)

        btn_no.clicked.connect(self.reject)
        btn_yes.clicked.connect(self.accept)

        self.resize(420, 140)


def _toast(parent: Optional[QtWidgets.QWidget], text: str, msecs: int = 1400) -> None:
    tip = QtWidgets.QDialog(parent)
    tip.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip)
    tip.setAttribute(Qt.WA_TranslucentBackground, True)

    outer = QtWidgets.QVBoxLayout(tip)
    outer.setContentsMargins(0, 0, 0, 0)

    body = QtWidgets.QFrame()
    body.setStyleSheet(
        """
        QFrame {
            background: rgba(15, 17, 22, 246);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
        }
        QLabel {
            color:#e6e6e6;
            padding: 10px 12px;
            font-weight: 700;
        }
    """
    )
    lbl = QtWidgets.QLabel(text)
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(lbl)
    outer.addWidget(body)

    tip.adjustSize()

    if parent is not None:
        pos = parent.mapToGlobal(parent.rect().bottomRight())
        tip.move(pos.x() - tip.width() - 22, pos.y() - tip.height() - 22)
    else:
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        tip.move(scr.right() - tip.width() - 22, scr.bottom() - tip.height() - 22)

    QtCore.QTimer.singleShot(msecs, tip.close)
    tip.show()


def confirm_and_reset(parent: Optional[QtWidgets.QWidget] = None) -> None:
    dlg = _ConfirmDialog(parent)
    if parent is not None:
        cp = parent.mapToGlobal(parent.rect().center())
        dlg.move(cp.x() - dlg.width() // 2, cp.y() - dlg.height() // 2)

    if dlg.exec() == QtWidgets.QDialog.Accepted:
        files, _dirs = reset_everything(parent=parent)
        _toast(parent, f"Wiped. ({files} files)")

        # Signal to Nexus (if connected) to hard-clear preview
        try:
            if parent is not None and hasattr(parent, "wiped"):
                parent.wiped.emit()  # type: ignore[attr-defined]
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Press-only GO button (depress on mouse down, pop back on release)
# ─────────────────────────────────────────────────────────────────────────────
class _PressGoLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

        # True transparency
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._base_rect = QtCore.QRect(0, 0, 0, 0)
        self._pix_base: Optional[QtGui.QPixmap] = None

        self._scale_anim = QtCore.QVariantAnimation(self)
        self._scale_anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
        self._scale_anim.valueChanged.connect(self._apply_scale)

        self._scale = 1.0
        self._pressed = False

    def set_base(self, base_rect: QtCore.QRect, pix: QtGui.QPixmap) -> None:
        self._base_rect = QtCore.QRect(base_rect)
        self._pix_base = pix
        self._set_scaled_geometry_and_pixmap(1.0)

    def _set_scaled_geometry_and_pixmap(self, scale: float) -> None:
        if self._pix_base is None or self._pix_base.isNull():
            self.setGeometry(self._base_rect)
            self.clear()
            return

        scale = float(scale)
        bw = self._base_rect.width()
        bh = self._base_rect.height()

        nw = max(1, int(round(bw * scale)))
        nh = max(1, int(round(bh * scale)))

        cx = self._base_rect.x() + bw / 2
        cy = self._base_rect.y() + bh / 2

        pm = self._pix_base.scaled(nw, nh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        pw = pm.width()
        ph = pm.height()
        x = int(round(cx - pw / 2))
        y = int(round(cy - ph / 2))

        self.setPixmap(pm)
        self.setGeometry(x, y, pw, ph)

    def _apply_scale(self, v: object) -> None:
        try:
            self._scale = float(v)
        except Exception:
            self._scale = 1.0
        self._set_scaled_geometry_and_pixmap(self._scale)

    def _animate_to(self, target: float, ms: int) -> None:
        self._scale_anim.stop()
        self._scale_anim.setDuration(int(ms))
        self._scale_anim.setStartValue(float(self._scale))
        self._scale_anim.setEndValue(float(target))
        self._scale_anim.start()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._animate_to(0.92, 85)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pressed and event.button() == Qt.LeftButton:
            self._pressed = False
            self._animate_to(1.0, 110)

            if self.rect().contains(event.position().toPoint()):
                QtCore.QTimer.singleShot(0, self.clicked.emit)

            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._pressed:
            self._pressed = False
            self._animate_to(1.0, 110)
        super().leaveEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# CommandTab: command.png background + centered GO.png press-animated button
# ─────────────────────────────────────────────────────────────────────────────
class CommandTab(QtWidgets.QWidget):
    wiped = QtCore.Signal()

    def __init__(self, project_root: Path, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.setObjectName("CommandTab")
        # TabSwitcher reads these properties. Any transition entering or
        # leaving Command becomes a pure fade at twice normal tab speed.
        self.setProperty("anima.Transition", "command-fade")
        self.setProperty("anima.TransitionDurationMultiplier", 2.0)
        self.setStyleSheet("QWidget#CommandTab { background:#0b0c10; }")

        icons_dir = self.project_root / "gallery" / "app" / "icons"
        self._bg_path = (icons_dir / "command.png").resolve()
        self._go_path = (icons_dir / "GO.png").resolve()

        self._bg_pix = QtGui.QPixmap(str(self._bg_path))
        self._go_pix = QtGui.QPixmap(str(self._go_path))

        self.bg_label = QtWidgets.QLabel(self)
        self.bg_label.setAlignment(Qt.AlignCenter)
        self.bg_label.setScaledContents(False)
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.go_btn = _PressGoLabel(self)
        self.go_btn.setToolTip("Wipe the letter")
        self.go_btn.clicked.connect(lambda: self._do_reset())

        self._relayout()

    def _do_reset(self) -> None:
        confirm_and_reset(self)
        # bubble signal for any listeners
        try:
            self.wiped.emit()
        except Exception:
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())

        self.bg_label.setGeometry(0, 0, w, h)
        if not self._bg_pix.isNull():
            bg_scaled = self._bg_pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.bg_label.setPixmap(bg_scaled)

        if self._go_pix.isNull():
            self.go_btn.set_base(QtCore.QRect(0, 0, 0, 0), self._go_pix)
            return

        target = int(min(w, h) * 0.28)
        target = max(140, min(460, target))

        go_base = self._go_pix.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        bw = go_base.width()
        bh = go_base.height()

        base_rect = QtCore.QRect((w - bw) // 2, (h - bh) // 2, bw, bh)
        self.go_btn.set_base(base_rect, go_base)
        self.go_btn.raise_()


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    confirm_and_reset(None)
    QtCore.QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
