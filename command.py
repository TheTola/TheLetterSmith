# ===============================
# File: command.py
# Purpose: "The Commandment" — UI + surgical reset
#
# REQUIRED EXPORTS (Forge_Tab.py imports these):
#   - confirm_and_reset
#   - open_saved_letters
#
# POLICY (FINAL):
# ✅ Delete ONLY:
#   - contents of gallery/user/pages/
#   - contents of gallery/user/message/
#   - gallery/user/sounds/music.mp3
#
# ❌ Must NOT delete:
#   - glissando.mp3
#   - flip1..flip10.mp3
#   - gallery/user/sounds/appssong/ (or anything inside it)
#   - gallery/user/card/controls/
#
# Also:
# - Clear recipient + title in settings.json (recipient_name + recipient_title)
# - Force starting_volume to 30
# - If music_volume exists, force it to 30 as well
#
# COMMAND TAB UI (FINAL):
# - command.png is BACKGROUND (not clickable)
# - GO.png is the BUTTON, centered (clickable)
# - GO press animation only: depress on mouse down, pop back on release
# - GO is truly transparent (no gray square ever)
# - Entire tab is NOT clickable
# - Popup: single sentence only (no list)
# - Popup: no corner squares (zero outer margins + translucent)
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
    "open_saved_letters",
]

# ─────────────────────────────────────────────────────────────────────────────
# Config (authoritative; safe fallbacks)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from config import (
        SETTINGS_FILE,
        USER_PAGES_DIR,
        USER_MESSAGE_DIR,
        USER_SOUNDS_DIR,
        MUSIC_FILE,
        OUTPUT_FILE_DIR,
    )
except Exception:
    SETTINGS_FILE = "settings.json"
    USER_PAGES_DIR = "gallery/user/pages"
    USER_MESSAGE_DIR = "gallery/user/message"
    USER_SOUNDS_DIR = "gallery/user/sounds"
    MUSIC_FILE = "music.mp3"
    OUTPUT_FILE_DIR = os.path.join("output", "File")


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
        if (up / "Main.py").exists() or (up / "Nexus.py").exists() or (up / SETTINGS_FILE).exists():
            return up
    return here.parent


# ─────────────────────────────────────────────────────────────────────────────
# File helpers (surgical)
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


def _reset_settings(root: Path) -> None:
    """
    - Clear recipient/title
    - Force starting_volume to 30
    - If music_volume exists, force to 30
    """
    settings_path = (root / SETTINGS_FILE).resolve()
    data = _read_settings(settings_path)

    data["recipient_name"] = ""
    data["recipient_title"] = ""

    data["starting_volume"] = 30
    if "music_volume" in data:
        data["music_volume"] = 30

    _write_settings(settings_path, data)


def _poke_ui_clear_fields(parent: Optional[QtWidgets.QWidget]) -> None:
    """
    Best-effort UI cleanup: if the confirm dialog was launched from within the app,
    clear any LineEdits that look like recipient/title fields.
    """
    if parent is None:
        return
    win = parent.window()
    try:
        for e in win.findChildren(QtWidgets.QLineEdit):
            n = (e.objectName() or "").lower()
            if ("recipient" in n) or ("title" in n):
                e.setText("")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Public: reset action
# ─────────────────────────────────────────────────────────────────────────────
def reset_everything(*, parent: Optional[QtWidgets.QWidget] = None) -> Tuple[int, int]:
    root = app_root()

    pages_dir = (root / USER_PAGES_DIR).resolve()
    msg_dir = (root / USER_MESSAGE_DIR).resolve()
    snd_dir = (root / USER_SOUNDS_DIR).resolve()
    music_path = (snd_dir / MUSIC_FILE).resolve()

    pages_dir.mkdir(parents=True, exist_ok=True)
    msg_dir.mkdir(parents=True, exist_ok=True)
    snd_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_dirs = 0

    # Wipe user pages
    f, d = _safe_clear_dir_contents(pages_dir)
    total_files += f
    total_dirs += d

    # Wipe user message folder (THIS IS THE MISSING PIECE YOU ASKED FOR)
    f, d = _safe_clear_dir_contents(msg_dir)
    total_files += f
    total_dirs += d

    # Wipe only music.mp3 (do NOT touch appssong, glissando, flips)
    total_files += _safe_delete_file(music_path)

    _reset_settings(root)
    _poke_ui_clear_fields(parent)

    return total_files, total_dirs


# ─────────────────────────────────────────────────────────────────────────────
# Public: open_saved_letters (Forge_Tab.py import compatibility)
# ─────────────────────────────────────────────────────────────────────────────
def open_saved_letters(parent: Optional[QtWidgets.QWidget] = None) -> None:
    root = app_root()
    target = (root / OUTPUT_FILE_DIR).resolve()
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
        try:
            QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Frameless confirm dialog (single sentence, no list)
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
        panel.setStyleSheet("""
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
        """)

        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(18, 16, 18, 14)
        inner.setSpacing(12)

        title = QtWidgets.QLabel("Are you sure you want to wipe the letter?")
        title.setWordWrap(True)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        btn_no = QtWidgets.QPushButton("No")
        btn_yes = QtWidgets.QPushButton("Yes")
        btn_yes.setObjectName("danger")
        row.addWidget(btn_no)
        row.addWidget(btn_yes)

        inner.addWidget(title)
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
    body.setStyleSheet("""
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
    """)
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

        # IMPORTANT: tell Nexus to hard-clear preview (redundancy)
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

        # CRITICAL: true transparency (no gray square ever)
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
            self._animate_to(0.92, 85)  # depress
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pressed and event.button() == Qt.LeftButton:
            self._pressed = False
            self._animate_to(1.0, 110)  # pop back

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
    """
    - command.png is BACKGROUND only (fit, no crop)
    - GO.png is the CLICKABLE button (centered)
    - Press animation only (no bounce/idle motion)
    """

    wiped = QtCore.Signal()  # Nexus listens to this to hard-clear preview

    def __init__(self, project_root: Path, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.setObjectName("CommandTab")
        self.setStyleSheet("QWidget#CommandTab { background:#0b0c10; }")

        icons_dir = self.project_root / "gallery" / "app" / "icons"
        self._bg_path = (icons_dir / "command.png").resolve()
        self._go_path = (icons_dir / "GO.png").resolve()

        self._bg_pix = QtGui.QPixmap(str(self._bg_path))
        self._go_pix = QtGui.QPixmap(str(self._go_path))

        # Background label (NOT clickable)
        self.bg_label = QtWidgets.QLabel(self)
        self.bg_label.setAlignment(Qt.AlignCenter)
        self.bg_label.setScaledContents(False)
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # GO button label (clickable)
        self.go_btn = _PressGoLabel(self)
        self.go_btn.setToolTip("Wipe the letter")
        self.go_btn.clicked.connect(lambda: confirm_and_reset(self))

        self._relayout()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())

        # Background: fit, no crop, no overflow
        self.bg_label.setGeometry(0, 0, w, h)
        if not self._bg_pix.isNull():
            bg_scaled = self._bg_pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.bg_label.setPixmap(bg_scaled)

        # GO: centered, sized relative to window (never spills)
        if self._go_pix.isNull():
            self.go_btn.set_base(QtCore.QRect(0, 0, 0, 0), self._go_pix)
            return

        target = int(min(w, h) * 0.28)
        target = max(140, min(460, target))  # clamp

        go_base = self._go_pix.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        bw = go_base.width()
        bh = go_base.height()

        base_rect = QtCore.QRect((w - bw) // 2, (h - bh) // 2, bw, bh)
        self.go_btn.set_base(base_rect, go_base)
        self.go_btn.raise_()


# ─────────────────────────────────────────────────────────────────────────────
# Optional CLI entry
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    confirm_and_reset(None)
    QtCore.QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
