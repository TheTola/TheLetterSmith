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
import logging
import os
import sys
import shutil
from pathlib import Path
from typing import Callable, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from command_bar import CommandBarData, build_command_bar_data
from project_state import ApplicationState, ProjectStateController

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


RESET_SETTINGS = {
    "starting_volume": 50,
    "music_volume": 50,
    "music_file": "",
    "last_audio": "none",
    "recipient_title_locked": False,
    "recipient_name_locked": False,
    "published_page_url_locked": False,
    "active_play_dir": "",
}

LOGGER = logging.getLogger(__name__)


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

        if hasattr(message_tab, "reset_identity_locks"):
            try:
                message_tab.reset_identity_locks()
            except Exception:
                LOGGER.exception("Could not clear Message identity locks during reset")

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
                settings["recipient_title_locked"] = False
                settings["recipient_name_locked"] = False
                settings["published_page_url_locked"] = False

        except Exception:
            pass

    except Exception:
        pass


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
    project_root: str | Path | None = None,
    parent: Optional[QtWidgets.QWidget] = None,
    project_state: ProjectStateController | None = None,
) -> Tuple[int, int]:
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else app_root()
    )

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

    # Clear live Message fields.
    _clear_message_tab_inputs(
        window
    )

    # Clear the live Sound tab immediately.
    _force_soundtab_no_audio(
        window
    )

    controller = project_state or ProjectStateController(root)
    controller.begin_new_project(
        additional_settings=RESET_SETTINGS
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

            QLabel#question {
                color: #ff4d4f;
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
                color: #ff4d4f;
            }

            QPushButton#danger:hover {
                border-color: rgba(255, 77, 79, 1.0);
            }

            QPushButton#cancel {
                color: #00e5ff;
            }

            QPushButton#cancel:hover {
                border-color: rgba(0, 229, 255, 0.9);
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
        label.setObjectName(
            "question"
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

        no_button.setObjectName(
            "cancel"
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


def _perform_confirmed_reset(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
    announce: bool = True,
) -> bool:
    previous_identity = (
        project_state.identity
        if project_state is not None
        else None
    )
    if (
        project_state is not None
        and project_state.state is not ApplicationState.PROJECT_CLEARING
    ):
        project_state.transition(
            ApplicationState.PROJECT_CLEARING
        )
    try:
        files, _directories = reset_everything(
            project_root=project_root,
            parent=parent,
            project_state=project_state,
        )
    except Exception:
        if (
            project_state is not None
            and previous_identity is not None
            and previous_identity.is_valid
            and project_state.state is ApplicationState.PROJECT_CLEARING
        ):
            project_state.transition(
                ApplicationState.PROJECT_READY,
                identity=previous_identity,
            )
        raise

    if announce:
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

    return True


def confirm_and_reset(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
    before_reset: Optional[Callable[[], bool]] = None,
) -> bool:
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

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False

    if before_reset is not None:
        try:
            if not before_reset():
                QtWidgets.QMessageBox.critical(
                    parent,
                    "Command reset failed",
                    "Prompt Writer could not be reset, so the command was not completed.",
                )
                return False
        except Exception:
            logging.getLogger(__name__).exception("Command pre-reset hook failed")
            QtWidgets.QMessageBox.critical(
                parent,
                "Command reset failed",
                "Prompt Writer could not be reset, so the command was not completed.",
            )
            return False

    return _perform_confirmed_reset(
        parent,
        project_root=project_root,
        project_state=project_state,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Press-only GO image button
# ─────────────────────────────────────────────────────────────────────────────
class _PressGoLabel(
    QtWidgets.QLabel
):
    clicked = QtCore.Signal()
    HOLD_DURATION_MS = 3000
    BURST_DURATION_MS = 280

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
        self._pix_gray: Optional[
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

        self._hold_timer = QtCore.QTimer(
            self
        )
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(
            self._complete_hold
        )

        self._burst_anim = QtCore.QVariantAnimation(
            self
        )
        self._burst_anim.setEasingCurve(
            QtCore.QEasingCurve.OutCubic
        )
        self._burst_anim.valueChanged.connect(
            self._apply_scale
        )
        self._burst_anim.finished.connect(
            self._emit_held
        )

        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(
            self
        )
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(
            self._opacity_effect
        )

        self._activity_anim = QtCore.QVariantAnimation(
            self
        )
        self._activity_anim.setDuration(900)
        self._activity_anim.setLoopCount(-1)
        self._activity_anim.setEasingCurve(
            QtCore.QEasingCurve.InOutSine
        )
        self._activity_anim.setKeyValueAt(0.0, 1.0)
        self._activity_anim.setKeyValueAt(0.5, 0.72)
        self._activity_anim.setKeyValueAt(1.0, 1.0)
        self._activity_anim.valueChanged.connect(
            self._apply_opacity
        )

        style = QtWidgets.QApplication.style()
        self._animations_enabled = bool(
            style
            and style.styleHint(
                QtWidgets.QStyle.SH_Widget_Animate,
                None,
                self,
            )
        )

        self._scale = 1.0
        self._busy = False
        self._holding = False
        self._hold_completed = False
        self._use_gray = False

    def set_base(
        self,
        base_rect: QtCore.QRect,
        pixmap: QtGui.QPixmap,
    ) -> None:
        self._base_rect = QtCore.QRect(
            base_rect
        )

        self._pix_base = pixmap
        self._pix_gray = self._make_gray_pixmap(
            pixmap
        )

        self._set_scaled_geometry_and_pixmap(
            self._scale
        )

    @staticmethod
    def _make_gray_pixmap(
        pixmap: QtGui.QPixmap,
    ) -> QtGui.QPixmap:
        if pixmap.isNull():
            return QtGui.QPixmap()
        source = pixmap.toImage().convertToFormat(
            QtGui.QImage.Format_ARGB32_Premultiplied
        )
        gray = source.convertToFormat(
            QtGui.QImage.Format_Grayscale8
        ).convertToFormat(
            QtGui.QImage.Format_ARGB32_Premultiplied
        )
        painter = QtGui.QPainter(
            gray
        )
        painter.setCompositionMode(
            QtGui.QPainter.CompositionMode_DestinationIn
        )
        painter.drawImage(
            0,
            0,
            source,
        )
        painter.end()
        return QtGui.QPixmap.fromImage(
            gray
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

        source = (
            self._pix_gray
            if self._use_gray and self._pix_gray is not None
            else self._pix_base
        )
        pixmap = source.scaled(
            new_width,
            new_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.setPixmap(
            pixmap
        )

        self.setGeometry(
            self._base_rect
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

        if not self._animations_enabled:
            self._apply_scale(1.0)
            return

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

    def _apply_opacity(
        self,
        value: object,
    ) -> None:
        try:
            opacity = float(value)
        except (TypeError, ValueError):
            opacity = 1.0
        self._opacity_effect.setOpacity(opacity)

    def set_busy(
        self,
        busy: bool,
    ) -> None:
        self.cancel_hold(
            animate=False
        )
        self._busy = bool(busy)
        self._use_gray = self._busy
        self._apply_scale(1.0)

        if self._busy:
            self._start_blink()
            return

        self._stop_blink()

    def _start_blink(self) -> None:
        self._activity_anim.stop()
        if self._animations_enabled:
            self._activity_anim.start()
        else:
            self._opacity_effect.setOpacity(0.78)

    def _stop_blink(self) -> None:
        self._activity_anim.stop()
        self._opacity_effect.setOpacity(1.0)

    def _start_hold(self) -> None:
        if self._busy or self._holding or self._hold_completed:
            return
        self.cancel_hold(
            animate=False
        )
        self._holding = True
        self._use_gray = True
        self._apply_scale(1.0)
        self._start_blink()
        self._hold_timer.start(
            int(self.HOLD_DURATION_MS)
        )

        if self._animations_enabled:
            self._scale_anim.stop()
            self._scale_anim.setEasingCurve(
                QtCore.QEasingCurve.Linear
            )
            self._scale_anim.setDuration(
                int(self.HOLD_DURATION_MS)
            )
            self._scale_anim.setStartValue(1.0)
            self._scale_anim.setEndValue(0.38)
            self._scale_anim.start()

    def cancel_hold(
        self,
        *,
        animate: bool = True,
    ) -> None:
        self._hold_timer.stop()
        self._scale_anim.stop()
        self._burst_anim.stop()
        self._holding = False
        self._hold_completed = False
        self._use_gray = False
        self._stop_blink()
        if animate and self._animations_enabled:
            self._animate_to(
                1.0,
                160,
            )
        else:
            self._apply_scale(1.0)

    def _complete_hold(self) -> None:
        if not self._holding:
            return
        self._scale_anim.stop()
        self._holding = False
        self._hold_completed = True
        self._use_gray = False
        self._stop_blink()

        if not self._animations_enabled:
            self._emit_held()
            return

        start_scale = max(
            0.38,
            min(1.0, self._scale),
        )
        self._burst_anim.stop()
        self._burst_anim.setDuration(
            int(self.BURST_DURATION_MS)
        )
        self._burst_anim.setStartValue(start_scale)
        self._burst_anim.setKeyValueAt(0.68, 1.24)
        self._burst_anim.setEndValue(1.0)
        self._burst_anim.start()

    def _emit_held(self) -> None:
        if not self._hold_completed:
            return
        self._hold_completed = False
        self._apply_scale(1.0)
        self.clicked.emit()

    def mousePressEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if event.button() == Qt.LeftButton:
            self._start_hold()
            event.accept()
            return

        super().mousePressEvent(
            event
        )

    def mouseReleaseEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if event.button() == Qt.LeftButton:
            if self._holding:
                self.cancel_hold()
            event.accept()
            return

        super().mouseReleaseEvent(
            event
        )

    def leaveEvent(
        self,
        event: QtCore.QEvent,
    ) -> None:
        if self._holding:
            self.cancel_hold()

        super().leaveEvent(
            event
        )

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        if self._holding or self._hold_completed:
            self.cancel_hold(
                animate=False
            )
        super().hideEvent(
            event
        )


# ─────────────────────────────────────────────────────────────────────────────
# Command tab
# ─────────────────────────────────────────────────────────────────────────────
class _ShockwaveWidget(
    QtWidgets.QWidget
):
    def __init__(
        self,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(
            parent
        )
        self.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.setAttribute(
            Qt.WA_TranslucentBackground,
            True,
        )
        self._progress = 0.0
        self._animation = QtCore.QVariantAnimation(
            self
        )
        self._animation.setDuration(520)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(
            QtCore.QEasingCurve.OutCubic
        )
        self._animation.valueChanged.connect(
            self._set_progress
        )
        self._animation.finished.connect(
            self.deleteLater
        )

    def start(
        self,
        *,
        animate: bool,
    ) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.deleteLater()
            return
        self.setGeometry(
            parent.rect()
        )
        self.show()
        self.raise_()
        if animate:
            self._animation.start()
            return
        self._progress = 0.72
        self.update()
        QtCore.QTimer.singleShot(
            180,
            self.deleteLater,
        )

    def _set_progress(
        self,
        value: object,
    ) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(
        self,
        _event: QtGui.QPaintEvent,
    ) -> None:
        painter = QtGui.QPainter(
            self
        )
        painter.setRenderHint(
            QtGui.QPainter.Antialiasing,
            True,
        )
        progress = max(
            0.0,
            min(1.0, self._progress),
        )
        shortest = min(
            self.width(),
            self.height(),
        )
        radius = shortest * (
            0.08 + 0.55 * progress
        )
        alpha = max(
            0,
            round(235 * (1.0 - progress)),
        )
        pen = QtGui.QPen(
            QtGui.QColor(255, 55, 60, alpha),
            max(2.0, 9.0 * (1.0 - progress)),
        )
        painter.setPen(
            pen
        )
        painter.setBrush(
            Qt.NoBrush
        )
        center = QtCore.QPointF(
            self.rect().center()
        )
        painter.drawEllipse(
            center,
            radius,
            radius,
        )
        if progress > 0.18:
            pen.setColor(
                QtGui.QColor(255, 160, 160, alpha // 2)
            )
            pen.setWidthF(
                max(1.0, pen.widthF() * 0.5)
            )
            painter.setPen(
                pen
            )
            inner_radius = radius * 0.78
            painter.drawEllipse(
                center,
                inner_radius,
                inner_radius,
            )


def _cover_pixmap(
    pixmap: QtGui.QPixmap,
    display_size: QtCore.QSize,
) -> QtGui.QPixmap:
    """Aspect-fill and center-crop artwork to one exact display size."""
    if pixmap.isNull() or display_size.isEmpty():
        return QtGui.QPixmap()

    scaled = pixmap.scaled(
        display_size,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    crop = QtCore.QRect(
        (scaled.width() - display_size.width()) // 2,
        (scaled.height() - display_size.height()) // 2,
        display_size.width(),
        display_size.height(),
    )
    return scaled.copy(crop)


class CommandTab(
    QtWidgets.QWidget
):
    wiped = QtCore.Signal()

    def __init__(
        self,
        project_root: Path,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        project_state: ProjectStateController | None = None,
    ):
        super().__init__(
            parent
        )

        self.project_root = Path(
            project_root
        ).resolve()
        self.project_state = project_state
        self._reset_in_progress = False

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
            "Hold Go for 3 seconds to wipe the letter"
        )

        self._interaction_state = "idle"
        self._confirm_dialog: Optional[_ConfirmDialog] = None
        self._command_bar_data: Optional[CommandBarData] = None

        self.go_btn.clicked.connect(
            self._do_reset
        )

        self._relayout()

    def _do_reset(
        self,
    ) -> None:
        if self._interaction_state != "idle":
            return

        try:
            self._command_bar_data = build_command_bar_data(
                project_root=self.project_root,
            )
        except Exception:
            LOGGER.exception("Could not capture the completed letter for Command Bar")
            _toast(
                self,
                "The completed letter could not be captured.",
                msecs=3000,
            )
            return

        self._interaction_state = "confirming"
        self.go_btn.setEnabled(False)
        self.go_btn.setToolTip(
            "Confirm or cancel the wipe"
        )
        self.go_btn.set_busy(True)

        dialog = _ConfirmDialog(
            self
        )
        self._confirm_dialog = dialog

        center = self.mapToGlobal(
            self.rect().center()
        )
        dialog.move(
            center.x() - dialog.width() // 2,
            center.y() - dialog.height() // 2,
        )

        dialog.accepted.connect(
            self._begin_reset
        )
        dialog.rejected.connect(
            self._cancel_reset
        )
        dialog.finished.connect(
            self._release_confirm_dialog
        )
        shockwave = _ShockwaveWidget(
            self
        )
        shockwave.start(
            animate=self.go_btn._animations_enabled
        )
        dialog.open()

    def _begin_reset(
        self,
    ) -> None:
        if self._interaction_state != "confirming":
            return
        self._interaction_state = "running"
        self.go_btn.setToolTip(
            "Wiping the letter"
        )
        QtCore.QTimer.singleShot(
            0,
            self._execute_reset,
        )

    def _execute_reset(
        self,
    ) -> None:
        if self._interaction_state != "running":
            return
        try:
            hook = getattr(self.window(), "reset_prompt_writer_state", None)
            if callable(hook) and not hook():
                raise RuntimeError("Prompt Writer reset was not completed")
            opener = getattr(
                self.window(),
                "open_command_bar_and_close_editor",
                None,
            )
            if not callable(opener):
                raise RuntimeError("Command Bar integration is unavailable")
            if not _perform_confirmed_reset(
                self,
                project_root=self.project_root,
                project_state=self.project_state,
                announce=False,
            ):
                raise RuntimeError("The project reset was not completed")
            data = self._command_bar_data
            if data is None:
                raise RuntimeError("Command Bar data was not captured")
            if not opener(data):
                raise RuntimeError("Command Bar could not be opened")
            self._command_bar_data = None
        except Exception as error:
            LOGGER.exception(
                "Command reset failed"
            )
            detail = str(error).strip() or type(error).__name__
            _toast(
                self,
                f"Wipe failed: {detail}",
                msecs=4000,
            )
        finally:
            self._finish_interaction()

    def _cancel_reset(
        self,
    ) -> None:
        if self._interaction_state != "confirming":
            return
        _toast(
            self,
            "Wipe cancelled.",
            msecs=900,
        )
        self._command_bar_data = None
        self._finish_interaction()

    def _release_confirm_dialog(
        self,
        _result: int,
    ) -> None:
        dialog = self._confirm_dialog
        self._confirm_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _finish_interaction(
        self,
    ) -> None:
        self.go_btn.set_busy(False)
        self.go_btn.setEnabled(True)
        self.go_btn.setToolTip(
            "Hold Go for 3 seconds to wipe the letter"
        )
        self._interaction_state = "idle"

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

        if self._bg_pix.isNull() or self._go_pix.isNull():
            self.bg_label.clear()
            self.go_btn.set_base(
                QtCore.QRect(),
                QtGui.QPixmap(),
            )
            return

        display_size = QtCore.QSize(
            width,
            height,
        )
        background = _cover_pixmap(
            self._bg_pix,
            display_size,
        )
        self.bg_label.setPixmap(
            background
        )

        go_base = _cover_pixmap(
            self._go_pix,
            display_size,
        )
        base_rect = QtCore.QRect(
            QtCore.QPoint(),
            display_size,
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
