# ===============================
# File: command.py
# Purpose: Erase/Reset command for eLetter
#
# Fixes:
# 1) Recipient/title not clearing (UI + settings)
# 2) Music still plays after erase
# 3) Sound tab retaining deleted track/playlist selections
#
# Guarantees after reset:
# - settings.json:
#     recipient_name = ""
#     recipient_title = ""
#     music_file = ""
#     last_audio = "none"
#     starting_volume = 50
#     music_volume = 50
# - project_sound.json:
#     single_track_id = ""
#     playlist = []
#     selected_track_id = ""
# - deletes:
#     gallery/user/sounds/music.mp3
#     gallery/sounds/music.mp3
#     gallery/user/sounds/appssong/current.json
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
# Config
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

    for up in (
        here.parent,
        here.parent.parent,
        here.parent.parent.parent,
    ):
        if (up / SETTINGS_FILE).exists() or (up / "gallery").exists():
            return up

    return here.parent


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────
def _safe_clear_dir_contents(
    dir_path: Path,
) -> Tuple[int, int]:
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
                shutil.rmtree(
                    entry,
                    ignore_errors=True,
                )
                dirs_deleted += 1

        except Exception:
            pass

    return files_deleted, dirs_deleted


def _safe_delete_file(
    path: Path,
) -> int:
    try:
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
            return 1

    except Exception:
        pass

    return 0


def _read_settings(
    path: Path,
) -> dict:
    try:
        if path.exists():
            return json.loads(
                path.read_text(
                    encoding="utf-8",
                )
            )

    except Exception:
        pass

    return {}


def _write_settings(
    path: Path,
    data: dict,
) -> None:
    try:
        path.write_text(
            json.dumps(
                data,
                indent=2,
            ),
            encoding="utf-8",
        )

    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# UI and runtime hooks
# ─────────────────────────────────────────────────────────────────────────────
def _get_nexus_window(
    parent: Optional[QtWidgets.QWidget],
) -> Optional[QtWidgets.QWidget]:
    if parent is None:
        return None

    try:
        return parent.window()

    except Exception:
        return None


def _hard_stop_sound_system(
    win: Optional[QtWidgets.QWidget],
) -> None:
    """
    Stop and detach the Sound tab players before deleting files.
    """

    if win is None:
        return

    try:
        sound_tab = getattr(
            win,
            "sound_tab",
            None,
        )

        if sound_tab is not None:
            wave = getattr(
                sound_tab,
                "wave",
                None,
            )

            if (
                wave is not None
                and hasattr(
                    wave,
                    "release_current_file_handle",
                )
            ):
                wave.release_current_file_handle()

    except Exception:
        pass

    try:
        sound_tab = getattr(
            win,
            "sound_tab",
            None,
        )

        if sound_tab is not None:
            preview = getattr(
                sound_tab,
                "_preview",
                None,
            )

            player = getattr(
                preview,
                "_player",
                None,
            )

            if player is not None:
                try:
                    player.stop()

                except Exception:
                    pass

                try:
                    player.setSource(
                        QUrl()
                    )

                except Exception:
                    pass

    except Exception:
        pass


def _force_soundtab_no_audio(
    win: Optional[QtWidgets.QWidget],
) -> None:
    """
    Force the live Sound tab to immediately show no selected audio.
    """

    if win is None:
        return

    try:
        sound_tab = getattr(
            win,
            "sound_tab",
            None,
        )

        if sound_tab is None:
            return

        if hasattr(
            sound_tab,
            "reset_project_sound",
        ):
            try:
                sound_tab.reset_project_sound()
                return

            except Exception:
                pass

        if hasattr(
            sound_tab,
            "_on_current_changed",
        ):
            try:
                sound_tab._on_current_changed("")

            except Exception:
                pass

        try:
            if hasattr(
                sound_tab,
                "playpause_btn",
            ):
                sound_tab.playpause_btn.setText(
                    "▶ Play"
                )

        except Exception:
            pass

        try:
            if hasattr(
                sound_tab,
                "status",
            ):
                sound_tab.status.setText(
                    "No audio loaded."
                )

        except Exception:
            pass

    except Exception:
        pass


def _clear_message_tab_inputs(
    win: Optional[QtWidgets.QWidget],
) -> None:
    """
    Clear the Message tab's visible and in-memory fields.
    """

    if win is None:
        return

    try:
        message_tab = getattr(
            win,
            "message_tab",
            None,
        )

        if message_tab is None:
            return

        try:
            if hasattr(
                message_tab,
                "title_input",
            ):
                message_tab.title_input.setText("")

        except Exception:
            pass

        try:
            if hasattr(
                message_tab,
                "name_input",
            ):
                message_tab.name_input.setText("")

        except Exception:
            pass

        try:
            if hasattr(
                message_tab,
                "set_published_page_url",
            ):
                message_tab.set_published_page_url(
                    "",
                    persist=False,
                    announce=False,
                )

        except Exception:
            pass

        try:
            if hasattr(
                message_tab,
                "current_html",
            ):
                message_tab.current_html = ""

            if hasattr(
                message_tab,
                "_content_has_intentional_formatting",
            ):
                message_tab._content_has_intentional_formatting = False

            if hasattr(
                message_tab,
                "_update_message_summary",
            ):
                message_tab._update_message_summary("")

        except Exception:
            pass

        try:
            settings = getattr(
                message_tab,
                "settings",
                None,
            )

            if isinstance(
                settings,
                dict,
            ):
                settings["recipient_title"] = ""
                settings["recipient_name"] = ""
                settings[PUBLISHED_PAGE_URL_KEY] = ""

        except Exception:
            pass

        try:
            if hasattr(
                message_tab,
                "_save_settings",
            ):
                message_tab._save_settings()

        except Exception:
            pass

    except Exception:
        pass


def _reset_settings_on_disk(
    root: Path,
) -> None:
    settings_path = (
        root / SETTINGS_FILE
    ).resolve()

    data = _read_settings(
        settings_path
    )

    data["recipient_name"] = ""
    data["recipient_title"] = ""
    data[PUBLISHED_PAGE_URL_KEY] = ""

    data["starting_volume"] = 50
    data["music_volume"] = 50

    data["music_file"] = ""
    data["last_audio"] = "none"

    _write_settings(
        settings_path,
        data,
    )


def _reset_project_sound_state(
    root: Path,
) -> None:
    """
    Clear the Sound tab's persisted single-track and playlist assignment.

    The music archive itself is preserved.
    """

    state_path = (
        root
        / USER_SOUNDS_DIR
        / "appssong"
        / "project_sound.json"
    ).resolve()

    try:
        state_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "mode": "single",
                    "single_track_id": "",
                    "playlist": [],
                    "playlist_expanded": True,
                    "selected_track_id": "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    except Exception:
        pass


def _delete_music_and_manifest(
    root: Path,
) -> int:
    """
    Delete the active compatibility music files and manifest.
    """

    total = 0

    user_sound_dir = (
        root / USER_SOUNDS_DIR
    ).resolve()

    user_music = (
        user_sound_dir / MUSIC_FILE
    ).resolve()

    runtime_music = (
        root
        / "gallery"
        / "sounds"
        / MUSIC_FILE
    ).resolve()

    current_manifest = (
        user_sound_dir
        / "appssong"
        / "current.json"
    ).resolve()

    total += _safe_delete_file(
        user_music
    )

    total += _safe_delete_file(
        runtime_music
    )

    total += _safe_delete_file(
        current_manifest
    )

    return total


# ─────────────────────────────────────────────────────────────────────────────
# Public reset action
# ─────────────────────────────────────────────────────────────────────────────
def reset_everything(
    *,
    parent: Optional[QtWidgets.QWidget] = None,
) -> Tuple[int, int]:
    root = app_root()

    pages_dir = (
        root / USER_PAGES_DIR
    ).resolve()

    message_dir = (
        root / USER_MESSAGE_DIR
    ).resolve()

    pages_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    message_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    total_files = 0
    total_dirs = 0

    window = _get_nexus_window(
        parent
    )

    # Stop all active audio before deleting files.
    _hard_stop_sound_system(
        window
    )

    files, directories = _safe_clear_dir_contents(
        pages_dir
    )

    total_files += files
    total_dirs += directories

    files, directories = _safe_clear_dir_contents(
        message_dir
    )

    total_files += files
    total_dirs += directories

    # Remove active generated music and compatibility manifest.
    total_files += _delete_music_and_manifest(
        root
    )

    # Clear the selected track or playlist.
    _reset_project_sound_state(
        root
    )

    # Reset settings.json.
    _reset_settings_on_disk(
        root
    )

    # Clear live Message fields.
    _clear_message_tab_inputs(
        window
    )

    # Clear the live Sound tab immediately.
    _force_soundtab_no_audio(
        window
    )

    return total_files, total_dirs


# ─────────────────────────────────────────────────────────────────────────────
# Frameless confirmation dialog
# ─────────────────────────────────────────────────────────────────────────────
class _ConfirmDialog(
    QtWidgets.QDialog
):
    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(
            parent
        )

        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )

        self.setModal(
            True
        )

        self.setAttribute(
            Qt.WA_TranslucentBackground,
            True,
        )

        outer = QtWidgets.QVBoxLayout(
            self
        )

        outer.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        panel = QtWidgets.QFrame(
            self
        )

        panel.setObjectName(
            "panel"
        )

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

            QPushButton:hover {
                border-color: rgba(255, 77, 79, 0.85);
            }

            QPushButton#danger {
                border-color: rgba(255, 77, 79, 0.55);
            }

            QPushButton#danger:hover {
                border-color: rgba(255, 77, 79, 1.0);
            }
            """
        )

        inner = QtWidgets.QVBoxLayout(
            panel
        )

        inner.setContentsMargins(
            18,
            16,
            18,
            14,
        )

        inner.setSpacing(
            12
        )

        label = QtWidgets.QLabel(
            "Are you sure? This will erase everything."
        )

        label.setWordWrap(
            True
        )

        row = QtWidgets.QHBoxLayout()

        row.addStretch(
            1
        )

        no_button = QtWidgets.QPushButton(
            "No"
        )

        yes_button = QtWidgets.QPushButton(
            "Yes"
        )

        yes_button.setObjectName(
            "danger"
        )

        row.addWidget(
            no_button
        )

        row.addWidget(
            yes_button
        )

        inner.addWidget(
            label
        )

        inner.addLayout(
            row
        )

        outer.addWidget(
            panel
        )

        no_button.clicked.connect(
            self.reject
        )

        yes_button.clicked.connect(
            self.accept
        )

        self.resize(
            420,
            140,
        )


def _toast(
    parent: Optional[QtWidgets.QWidget],
    text: str,
    msecs: int = 1400,
) -> None:
    toast = QtWidgets.QDialog(
        parent
    )

    toast.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.ToolTip
    )

    toast.setAttribute(
        Qt.WA_TranslucentBackground,
        True,
    )

    outer = QtWidgets.QVBoxLayout(
        toast
    )

    outer.setContentsMargins(
        0,
        0,
        0,
        0,
    )

    body = QtWidgets.QFrame()

    body.setStyleSheet(
        """
        QFrame {
            background: rgba(15, 17, 22, 246);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
        }

        QLabel {
            color: #e6e6e6;
            padding: 10px 12px;
            font-weight: 700;
        }
        """
    )

    label = QtWidgets.QLabel(
        text
    )

    layout = QtWidgets.QVBoxLayout(
        body
    )

    layout.setContentsMargins(
        0,
        0,
        0,
        0,
    )

    layout.addWidget(
        label
    )

    outer.addWidget(
        body
    )

    toast.adjustSize()

    if parent is not None:
        position = parent.mapToGlobal(
            parent.rect().bottomRight()
        )

        toast.move(
            position.x() - toast.width() - 22,
            position.y() - toast.height() - 22,
        )

    else:
        screen = (
            QtWidgets.QApplication
            .primaryScreen()
            .availableGeometry()
        )

        toast.move(
            screen.right() - toast.width() - 22,
            screen.bottom() - toast.height() - 22,
        )

    QtCore.QTimer.singleShot(
        msecs,
        toast.close,
    )

    toast.show()


def confirm_and_reset(
    parent: Optional[QtWidgets.QWidget] = None,
) -> None:
    dialog = _ConfirmDialog(
        parent
    )

    if parent is not None:
        center = parent.mapToGlobal(
            parent.rect().center()
        )

        dialog.move(
            center.x() - dialog.width() // 2,
            center.y() - dialog.height() // 2,
        )

    if (
        dialog.exec()
        == QtWidgets.QDialog.Accepted
    ):
        files, _directories = reset_everything(
            parent=parent
        )

        _toast(
            parent,
            f"Wiped. ({files} files)",
        )

        try:
            if (
                parent is not None
                and hasattr(
                    parent,
                    "wiped",
                )
            ):
                parent.wiped.emit()

        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Press-only GO image button
# ─────────────────────────────────────────────────────────────────────────────
class _PressGoLabel(
    QtWidgets.QLabel
):
    clicked = QtCore.Signal()

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(
            parent
        )

        self.setAlignment(
            Qt.AlignCenter
        )

        self.setCursor(
            Qt.PointingHandCursor
        )

        self.setAttribute(
            Qt.WA_TranslucentBackground,
            True,
        )

        self.setAutoFillBackground(
            False
        )

        self.setStyleSheet(
            "background: transparent;"
        )

        self.setAttribute(
            Qt.WA_NoSystemBackground,
            True,
        )

        self._base_rect = QtCore.QRect(
            0,
            0,
            0,
            0,
        )

        self._pix_base: Optional[
            QtGui.QPixmap
        ] = None

        self._scale_anim = (
            QtCore.QVariantAnimation(
                self
            )
        )

        self._scale_anim.setEasingCurve(
            QtCore.QEasingCurve.InOutQuad
        )

        self._scale_anim.valueChanged.connect(
            self._apply_scale
        )

        self._scale = 1.0
        self._pressed = False

    def set_base(
        self,
        base_rect: QtCore.QRect,
        pixmap: QtGui.QPixmap,
    ) -> None:
        self._base_rect = QtCore.QRect(
            base_rect
        )

        self._pix_base = pixmap

        self._set_scaled_geometry_and_pixmap(
            1.0
        )

    def _set_scaled_geometry_and_pixmap(
        self,
        scale: float,
    ) -> None:
        if (
            self._pix_base is None
            or self._pix_base.isNull()
        ):
            self.setGeometry(
                self._base_rect
            )

            self.clear()
            return

        scale = float(
            scale
        )

        base_width = self._base_rect.width()
        base_height = self._base_rect.height()

        new_width = max(
            1,
            int(
                round(
                    base_width * scale
                )
            ),
        )

        new_height = max(
            1,
            int(
                round(
                    base_height * scale
                )
            ),
        )

        center_x = (
            self._base_rect.x()
            + base_width / 2
        )

        center_y = (
            self._base_rect.y()
            + base_height / 2
        )

        pixmap = self._pix_base.scaled(
            new_width,
            new_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        width = pixmap.width()
        height = pixmap.height()

        x = int(
            round(
                center_x - width / 2
            )
        )

        y = int(
            round(
                center_y - height / 2
            )
        )

        self.setPixmap(
            pixmap
        )

        self.setGeometry(
            x,
            y,
            width,
            height,
        )

    def _apply_scale(
        self,
        value: object,
    ) -> None:
        try:
            self._scale = float(
                value
            )

        except Exception:
            self._scale = 1.0

        self._set_scaled_geometry_and_pixmap(
            self._scale
        )

    def _animate_to(
        self,
        target: float,
        milliseconds: int,
    ) -> None:
        self._scale_anim.stop()

        self._scale_anim.setDuration(
            int(
                milliseconds
            )
        )

        self._scale_anim.setStartValue(
            float(
                self._scale
            )
        )

        self._scale_anim.setEndValue(
            float(
                target
            )
        )

        self._scale_anim.start()

    def mousePressEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True

            self._animate_to(
                0.92,
                85,
            )

            event.accept()
            return

        super().mousePressEvent(
            event
        )

    def mouseReleaseEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if (
            self._pressed
            and event.button() == Qt.LeftButton
        ):
            self._pressed = False

            self._animate_to(
                1.0,
                110,
            )

            if self.rect().contains(
                event.position().toPoint()
            ):
                QtCore.QTimer.singleShot(
                    0,
                    self.clicked.emit,
                )

            event.accept()
            return

        super().mouseReleaseEvent(
            event
        )

    def leaveEvent(
        self,
        event: QtCore.QEvent,
    ) -> None:
        if self._pressed:
            self._pressed = False

            self._animate_to(
                1.0,
                110,
            )

        super().leaveEvent(
            event
        )


# ─────────────────────────────────────────────────────────────────────────────
# Command tab
# ─────────────────────────────────────────────────────────────────────────────
class CommandTab(
    QtWidgets.QWidget
):
    wiped = QtCore.Signal()

    def __init__(
        self,
        project_root: Path,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(
            parent
        )

        self.project_root = Path(
            project_root
        ).resolve()

        self.setObjectName(
            "CommandTab"
        )

        self.setStyleSheet(
            """
            QWidget#CommandTab {
                background: #0b0c10;
            }
            """
        )

        icons_dir = (
            self.project_root
            / "gallery"
            / "app"
            / "icons"
        )

        self._bg_path = (
            icons_dir / "command.png"
        ).resolve()

        self._go_path = (
            icons_dir / "GO.png"
        ).resolve()

        self._bg_pix = QtGui.QPixmap(
            str(
                self._bg_path
            )
        )

        self._go_pix = QtGui.QPixmap(
            str(
                self._go_path
            )
        )

        self.bg_label = QtWidgets.QLabel(
            self
        )

        self.bg_label.setAlignment(
            Qt.AlignCenter
        )

        self.bg_label.setScaledContents(
            False
        )

        self.bg_label.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )

        self.go_btn = _PressGoLabel(
            self
        )

        self.go_btn.setToolTip(
            "Wipe the letter"
        )

        self.go_btn.clicked.connect(
            self._do_reset
        )

        self._relayout()

    def _do_reset(
        self,
    ) -> None:
        confirm_and_reset(
            self
        )

        try:
            self.wiped.emit()

        except Exception:
            pass

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(
            event
        )

        self._relayout()

    def _relayout(
        self,
    ) -> None:
        width = max(
            1,
            self.width(),
        )

        height = max(
            1,
            self.height(),
        )

        self.bg_label.setGeometry(
            0,
            0,
            width,
            height,
        )

        if not self._bg_pix.isNull():
            background = self._bg_pix.scaled(
                width,
                height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

            self.bg_label.setPixmap(
                background
            )

        if self._go_pix.isNull():
            self.go_btn.set_base(
                QtCore.QRect(
                    0,
                    0,
                    0,
                    0,
                ),
                self._go_pix,
            )

            return

        target = int(
            min(
                width,
                height,
            ) * 0.28
        )

        target = max(
            140,
            min(
                460,
                target,
            ),
        )

        go_base = self._go_pix.scaled(
            target,
            target,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        button_width = go_base.width()
        button_height = go_base.height()

        base_rect = QtCore.QRect(
            (width - button_width) // 2,
            (height - button_height) // 2,
            button_width,
            button_height,
        )

        self.go_btn.set_base(
            base_rect,
            go_base,
        )

        self.go_btn.raise_()


def main() -> None:
    application = (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(
            sys.argv
        )
    )

    confirm_and_reset(
        None
    )

    QtCore.QTimer.singleShot(
        0,
        application.quit,
    )

    application.exec()


if __name__ == "__main__":
    main()