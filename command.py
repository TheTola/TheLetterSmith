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
#     configured active-project current sound manifest
# - does NOT delete:
#     glissando.mp3
#     flip1..flip10.mp3
#     persistent Music Archive originals, processed data, and analysis
# ===============================

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from command_bar import CommandBarData, build_command_bar_data
from protected_projects import (
    PROTECTED_PROJECT_KIND_KEY,
    PROTECTED_PROJECT_MASTER_PATH_KEY,
)
from project_paths import (
    PROJECT_METADATA_FILE,
    ProjectPathResolver,
    application_paths,
)
from project_state import ApplicationState, ProjectStateController
from settings_store import DEFAULT_CURTAIN_STYLE, DEFAULT_SETTINGS, SettingsStore
from sound_model import (
    ProjectSoundState,
    current_manifest_path,
    current_music_path,
    project_sound_path,
)
from transactional_io import PathTransaction, atomic_write_json
from ui_fonts import (
    COMMAND_FONT_FAMILY,
    load_application_fonts,
    resolve_registered_family,
)
from ui_help import set_control_help

__all__ = [
    "CommandTab",
    "confirm_and_reset",
    "delete_project",
    "reset_everything",
    "start_developer_reset",
    "start_new_project",
]


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
try:
    from config import (
        MESSAGE_ASSETS_DIR,
        USER_PAGES_DIR,
        USER_MESSAGE_DIR,
        MUSIC_FILE,
    )
except Exception:
    MESSAGE_ASSETS_DIR = "gallery/message_assets"
    USER_PAGES_DIR = "gallery/user/pages"
    USER_MESSAGE_DIR = "gallery/user/message"
    MUSIC_FILE = "music.mp3"


PROJECT_RESET_SETTINGS = {
    "curtain_style": DEFAULT_CURTAIN_STYLE,
    "message_overlay_preset": "paper",
    "message_overlay_opacity": 68,
    "forge_preview_mode": "portrait",
    "required_features": [],
    "music_required": False,
    "music_muted": False,
    "starting_volume": 50,
    "music_volume": 50,
    "music_file": "",
    "last_audio": "none",
    "recipient_title_locked": False,
    "recipient_name_locked": False,
    "published_page_url_locked": False,
    "active_play_dir": "",
    "prompt_writer_state": {},
    "published_public_path": "",
    "published_at": "",
    "published_expires_at": "",
    "publication_provider": "",
    "publication_verified": False,
    "published_source_fingerprint": "",
    "published_github_owner": "",
    "published_github_repository": "",
    "last_sealed_path": "",
    PROTECTED_PROJECT_KIND_KEY: "",
    PROTECTED_PROJECT_MASTER_PATH_KEY: "",
}

RESET_SETTINGS = dict(PROJECT_RESET_SETTINGS)

NEW_PROJECT_SETTINGS = {
    key: value
    for key, value in PROJECT_RESET_SETTINGS.items()
    if key not in {"starting_volume", "music_volume"}
}

DEVELOPER_RESET_SETTINGS = {
    **NEW_PROJECT_SETTINGS,
    "application_theme": "",
    "theme_family": "",
}

LOGGER = logging.getLogger(__name__)


def _command_font_family() -> str:
    return resolve_registered_family(COMMAND_FONT_FAMILY)


def _apply_command_font(widget: QtWidgets.QWidget) -> None:
    font = QtGui.QFont(widget.font())
    font.setFamily(_command_font_family())
    widget.setFont(font)
    widget.setProperty("themeIndependent", True)


# ─────────────────────────────────────────────────────────────────────────────
# Root resolver
# ─────────────────────────────────────────────────────────────────────────────
def app_root() -> Path:
    return application_paths().workspace_root


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────
def _path_counts(path: Path) -> Tuple[int, int]:
    if path.is_file() or path.is_symlink():
        return 1, 0
    if not path.is_dir():
        return 0, 0
    files = 0
    directories = 0
    for child in path.rglob("*"):
        if child.is_dir() and not child.is_symlink():
            directories += 1
        else:
            files += 1
    return files, directories


def _empty_prompt_writer_state() -> dict:
    from PromptWriterPanel import empty_prompt_writer_state

    return empty_prompt_writer_state()


def _active_autosave_directories(
    root: Path,
    settings: Mapping[str, object],
    *,
    all_recipients: bool = False,
) -> tuple[Path, ...]:
    project_id = str(settings.get("project_id", "")).strip()
    if not project_id:
        return ()
    resolver = ProjectPathResolver(root)
    recipient_id = (
        ""
        if all_recipients
        else str(settings.get("recipient_id", "")).strip()
    )
    paths = resolver.find_autosave_directories(
        project_id,
        recipient_id=recipient_id or None,
    )
    safe_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(resolver.autosave_root)
        except ValueError:
            raise RuntimeError(
                f"Active autosave escaped its canonical root: {resolved}"
            ) from None
        if resolved != resolver.autosave_root:
            safe_paths.append(resolved)
    return tuple(dict.fromkeys(safe_paths))


def _saved_project_paths(
    root: Path,
    settings: Mapping[str, object],
) -> tuple[object | None, tuple[Path, ...]]:
    """Resolve only managed saved-letter copies owned by the active project."""
    project_id = str(settings.get("project_id", "")).strip()
    if not project_id:
        return None, ()

    from saved_letters import SavedLetterCatalog

    catalog = SavedLetterCatalog(root)
    resolver = ProjectPathResolver(root)
    paths = application_paths(root)
    matches: list[Path] = []
    for managed_root in (
        paths.saved_letters_root.resolve(),
        paths.recovery_root.resolve(),
    ):
        if not managed_root.is_dir():
            continue
        for metadata_path in managed_root.rglob(PROJECT_METADATA_FILE):
            candidate = metadata_path.parent.resolve()
            try:
                relative = candidate.relative_to(managed_root)
            except ValueError:
                continue
            if (
                not relative.parts
                or metadata_path.is_symlink()
                or metadata_path.parent.is_symlink()
                or resolver._directory_project_id(candidate) != project_id
            ):
                continue
            matches.append(candidate)

    selected: list[Path] = []
    for candidate in sorted(
        dict.fromkeys(matches),
        key=lambda path: len(path.parts),
    ):
        if any(candidate.is_relative_to(parent) for parent in selected):
            continue
        selected.append(candidate)
    return catalog, tuple(selected)


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
            release = getattr(
                sound_tab,
                "release_current_file_handle",
                None,
            )

            if callable(release):
                release()

    except Exception:
        pass


def _release_image_system(
    win: Optional[QtWidgets.QWidget],
) -> None:
    """Release animated image handles before active page files are cleared."""
    if win is None:
        return
    try:
        image_tab = getattr(win, "image_tab", None)
        release = getattr(
            image_tab,
            "prepare_for_project_restore",
            None,
        )
        if callable(release):
            release()
    except Exception:
        pass


def _release_active_project_media(
    win: Optional[QtWidgets.QWidget],
) -> None:
    if win is None:
        return
    prepare_reset = getattr(
        win,
        "prepare_for_project_reset",
        None,
    )
    if callable(prepare_reset):
        prepare_reset()
        return
    release_preview = getattr(
        win,
        "_release_forge_preview_files",
        None,
    )
    if callable(release_preview):
        release_preview()
    release_project_files = getattr(
        win,
        "_release_project_files_for_restore",
        None,
    )
    if callable(release_project_files):
        release_project_files()
        return
    _hard_stop_sound_system(win)
    _release_image_system(win)


def _reset_active_paths(
    root: Path,
    *,
    settings_before: Mapping[str, object],
    controller: ProjectStateController,
    settings_updates: Mapping[str, object],
    delete_saved_project: bool = False,
) -> Tuple[int, int]:
    directory_targets = [
        (root / USER_PAGES_DIR).resolve(),
        (root / USER_MESSAGE_DIR).resolve(),
        (root / MESSAGE_ASSETS_DIR).resolve(),
    ]
    saved_catalog: object | None = None
    saved_project_paths: tuple[Path, ...] = ()
    if delete_saved_project:
        saved_catalog, saved_project_paths = _saved_project_paths(
            root,
            settings_before,
        )

    deletion_targets = [
        current_music_path(root).resolve(),
        (root / "gallery" / "sounds" / MUSIC_FILE).resolve(),
        current_manifest_path(root).resolve(),
        (root / "gallery" / "user" / "cache" / "curtains").resolve(),
        *_active_autosave_directories(
            root,
            settings_before,
            all_recipients=delete_saved_project,
        ),
        *saved_project_paths,
    ]
    deletion_targets = list(dict.fromkeys(deletion_targets))
    json_targets = [
        (
            project_sound_path(root).resolve(),
            ProjectSoundState().to_dict(),
        ),
        (
            (root / "prompt_writer_state.json").resolve(),
            _empty_prompt_writer_state(),
        ),
    ]

    files_removed = 0
    directories_removed = 0
    for target in (*directory_targets, *deletion_targets):
        files, directories = _path_counts(target)
        files_removed += files
        directories_removed += directories

    transactions: list[tuple[PathTransaction, bool]] = []
    committed: list[PathTransaction] = []
    settings_store = SettingsStore(root)
    try:
        for target in directory_targets:
            transaction = PathTransaction(
                target,
                staging_suffix=".new-project-staging",
                backup_suffix=".new-project-backup",
                unique_staging=True,
            )
            staging = transaction.prepare()
            staging.mkdir(parents=True)
            transactions.append((transaction, True))

        for target, payload in json_targets:
            transaction = PathTransaction(
                target,
                staging_suffix=".new-project-staging",
                backup_suffix=".new-project-backup",
                unique_staging=True,
            )
            staging = transaction.prepare()
            atomic_write_json(staging, payload)
            transactions.append((transaction, True))

        for target in deletion_targets:
            transaction = PathTransaction(
                target,
                staging_suffix=".new-project-staging",
                backup_suffix=".new-project-backup",
                unique_staging=True,
            )
            transaction.prepare()
            transactions.append((transaction, False))

        for transaction, replace in transactions:
            transaction.commit(
                replace=replace,
                keep_backup=True,
            )
            committed.append(transaction)

        controller.begin_new_project(
            additional_settings=settings_updates,
        )
    except Exception:
        LOGGER.exception("New Project transaction failed; rolling back.")
        try:
            settings_store.replace_snapshot(settings_before)
        except Exception:
            LOGGER.exception("Could not restore settings after New Project failure.")
        for transaction in reversed(committed):
            try:
                transaction.rollback()
            except Exception:
                LOGGER.exception(
                    "Could not roll back New Project path: %s",
                    transaction.final_path,
                )
        for transaction, _replace in transactions:
            try:
                transaction.abort()
            except Exception:
                LOGGER.exception(
                    "Could not clean New Project staging path: %s",
                    transaction.staging_path,
                )
        raise

    if saved_catalog is not None:
        refresh_entry = getattr(saved_catalog, "refresh_entry", None)
        if callable(refresh_entry):
            for path in saved_project_paths:
                try:
                    refresh_entry(path)
                except Exception:
                    LOGGER.exception(
                        "Could not refresh the saved-letter catalog after "
                        "deleting %s.",
                        path,
                    )

    for transaction, _replace in transactions:
        try:
            transaction.finalize()
        except OSError:
            LOGGER.exception(
                "Could not clean New Project backup: %s",
                transaction.backup_path,
            )
    return files_removed, directories_removed


# ─────────────────────────────────────────────────────────────────────────────
# Public reset action
# ─────────────────────────────────────────────────────────────────────────────
def reset_everything(
    *,
    project_root: str | Path | None = None,
    parent: Optional[QtWidgets.QWidget] = None,
    project_state: ProjectStateController | None = None,
    settings_updates: Mapping[str, object] | None = None,
    delete_saved_project: bool = False,
) -> Tuple[int, int]:
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else app_root()
    )

    window = _get_nexus_window(
        parent
    )
    controller = project_state or ProjectStateController(root)
    forge_tab = getattr(window, "forge_tab", None)
    if (
        forge_tab is not None
        and bool(getattr(forge_tab, "operation_in_progress", False))
    ):
        raise RuntimeError(
            "Finish the current Forge operation before starting a new project."
        )
    _release_active_project_media(window)
    settings_before = SettingsStore(root).snapshot()
    total_files, total_dirs = _reset_active_paths(
        root,
        settings_before=settings_before,
        controller=controller,
        settings_updates=(
            RESET_SETTINGS
            if settings_updates is None
            else settings_updates
        ),
        delete_saved_project=delete_saved_project,
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

        _apply_command_font(self)

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
        _apply_command_font(label)

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
        _apply_command_font(no_button)
        _apply_command_font(yes_button)

        set_control_help(
            no_button,
            "Cancel and keep the current letter unchanged.",
        )

        set_control_help(
            yes_button,
            "Confirm the reset and permanently clear the current letter workspace.",
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

    _apply_command_font(toast)

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
    _apply_command_font(label)

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
    settings_updates: Mapping[str, object] | None = None,
    delete_saved_project: bool = False,
) -> bool:
    previous_state = (
        project_state.state
        if project_state is not None
        else None
    )
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
            settings_updates=settings_updates,
            delete_saved_project=delete_saved_project,
        )
    except Exception:
        if project_state is not None and previous_state is not None:
            try:
                if (
                    previous_state is ApplicationState.PROJECT_READY
                    and previous_identity is not None
                    and previous_identity.is_valid
                ):
                    project_state.transition(
                        ApplicationState.PROJECT_READY,
                        identity=previous_identity,
                    )
                elif previous_state is ApplicationState.RECIPIENT_REQUIRED:
                    project_state.transition(
                        ApplicationState.RECIPIENT_REQUIRED,
                    )
            except Exception:
                LOGGER.exception(
                    "Could not restore project state after reset failure."
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


def start_new_project(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
) -> bool:
    """Clear only active-project data while preserving application preferences."""
    return _perform_confirmed_reset(
        parent,
        project_root=project_root,
        project_state=project_state,
        announce=False,
        settings_updates=NEW_PROJECT_SETTINGS,
    )


def delete_project(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
) -> bool:
    """Delete every managed local copy of the active editable project."""
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else app_root()
    )
    settings = SettingsStore(root).snapshot()
    if project_state is not None and not project_state.is_project_ready:
        raise RuntimeError("There is no active project to delete.")
    if not str(settings.get("project_id", "")).strip():
        raise RuntimeError("There is no active project to delete.")

    from protected_projects import is_protected_project

    if is_protected_project(settings):
        raise RuntimeError("Stock and Example Letters cannot be deleted.")

    return _perform_confirmed_reset(
        parent,
        project_root=root,
        project_state=project_state,
        announce=False,
        settings_updates=NEW_PROJECT_SETTINGS,
        delete_saved_project=True,
    )


def start_developer_reset(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
) -> bool:
    """Clear active-project data and restore first-run theme selection."""
    return _perform_confirmed_reset(
        parent,
        project_root=project_root,
        project_state=project_state,
        announce=False,
        settings_updates=DEVELOPER_RESET_SETTINGS,
    )


def confirm_and_reset(
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    project_root: str | Path | None = None,
    project_state: ProjectStateController | None = None,
    before_reset: Optional[Callable[[], bool]] = None,
) -> bool:
    if project_root is not None:
        load_application_fonts(project_root)
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
        *,
        countdown_sound_path: Optional[Path] = None,
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

        self._countdown_timer = QtCore.QTimer(
            self
        )
        self._countdown_timer.timeout.connect(
            self._advance_countdown
        )

        self._countdown_label = QtWidgets.QLabel(
            self
        )
        self._countdown_label.setObjectName(
            "GoHoldCountdown"
        )
        self._countdown_label.setAlignment(
            Qt.AlignCenter
        )
        self._countdown_label.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self._countdown_label.setStyleSheet(
            "color: #ffb0bb; background: transparent; "
            f"font: 900 92px '{_command_font_family()}';"
        )
        self._countdown_label.hide()

        self._countdown_player: Optional[QMediaPlayer] = None
        self._countdown_output: Optional[QAudioOutput] = None
        if countdown_sound_path is not None:
            sound_path = Path(
                countdown_sound_path
            ).resolve()
            if sound_path.is_file():
                self._countdown_output = QAudioOutput(
                    self
                )
                self._countdown_output.setVolume(
                    1.0
                )
                self._countdown_player = QMediaPlayer(
                    self
                )
                self._countdown_player.setAudioOutput(
                    self._countdown_output
                )
                self._countdown_player.setSource(
                    QtCore.QUrl.fromLocalFile(
                        str(sound_path)
                    )
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
        self._countdown_value = 0

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
        self._start_countdown()

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

    def _start_countdown(self) -> None:
        self._countdown_timer.stop()
        self._countdown_timer.setInterval(
            max(
                1,
                int(self.HOLD_DURATION_MS) // 3,
            )
        )
        self._show_countdown_step(
            3,
            play_sound=False,
        )
        self._countdown_timer.start()

    def _advance_countdown(self) -> None:
        next_value = self._countdown_value - 1
        if next_value <= 0:
            self._countdown_timer.stop()
            return
        self._show_countdown_step(next_value)

    def _show_countdown_step(
        self,
        value: int,
        *,
        play_sound: bool = True,
    ) -> None:
        self._countdown_value = int(value)
        self._countdown_label.setText(
            str(self._countdown_value)
        )
        self._countdown_label.setAccessibleName(
            f"Go countdown {self._countdown_value}"
        )
        self._countdown_label.show()
        self._countdown_label.raise_()
        if play_sound:
            self._play_countdown_sound()

    def _play_countdown_sound(self) -> None:
        if self._countdown_player is None:
            return
        self._countdown_player.stop()
        self._countdown_player.setPosition(
            0
        )
        self._countdown_player.play()

    def cancel_hold(
        self,
        *,
        animate: bool = True,
    ) -> None:
        self._hold_timer.stop()
        self._countdown_timer.stop()
        self._scale_anim.stop()
        self._burst_anim.stop()
        self._holding = False
        self._hold_completed = False
        self._use_gray = False
        self._countdown_value = 0
        self._countdown_label.hide()
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
        self._countdown_timer.stop()
        self._play_countdown_sound()
        self._countdown_value = 0
        self._countdown_label.hide()
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

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(
            event
        )
        self._countdown_label.setGeometry(
            self.rect()
        )

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
class _CommandEntryNotice(
    QtWidgets.QWidget
):
    AUTO_DISMISS_MS = 10_000
    FADE_DURATION_MS = 4_000

    def __init__(
        self,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(
            parent
        )
        self.setObjectName(
            "CommandEntryNotice"
        )
        self.setAttribute(
            Qt.WA_StyledBackground,
            True,
        )
        self.setStyleSheet(
            "QWidget#CommandEntryNotice { background: transparent; }"
        )

        self.panel = QtWidgets.QFrame(
            self
        )
        self.panel.setObjectName(
            "CommandEntryNoticePanel"
        )
        self.panel.setAttribute(
            Qt.WA_StyledBackground,
            True,
        )
        self.panel.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.panel.setStyleSheet(
            "QFrame#CommandEntryNoticePanel {"
            "background: #4a090c; border: 2px solid #b72330; "
            "border-radius: 16px; }"
        )
        layout = QtWidgets.QVBoxLayout(
            self.panel
        )
        layout.setContentsMargins(
            34,
            28,
            34,
            28,
        )
        layout.setSpacing(18)

        self.completion_message = QtWidgets.QLabel(
            "WHEN YOU ARE COMPLETELY DONE",
            self.panel,
        )
        self.completion_message.setObjectName(
            "CommandEntryNoticeCompletionText"
        )
        self.completion_message.setAlignment(
            Qt.AlignCenter
        )
        self.completion_message.setWordWrap(
            True
        )
        self.completion_message.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.completion_message.setStyleSheet(
            "QLabel#CommandEntryNoticeCompletionText { color: #ffb0bb; "
            "background: transparent; "
            f"font: 600 30px '{_command_font_family()}'; }}"
        )
        layout.addWidget(
            self.completion_message
        )

        self.message = QtWidgets.QLabel(
            "Hold Go for 3 seconds to save this finished letter and clear "
            "the workspace for your next project.\n\n"
            "The saved letter remains available in Command Bar. You can "
            "preview it, open its published letter, and copy its published "
            "link.\n\n"
            "Only do this when you are ready to send the letter and begin "
            "your next project.\n\n"
            "Click anywhere to dismiss.",
            self.panel,
        )
        self.message.setObjectName(
            "CommandEntryNoticeText"
        )
        self.message.setAlignment(
            Qt.AlignHCenter | Qt.AlignTop
        )
        self.message.setWordWrap(
            True
        )
        self.message.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.message.setStyleSheet(
            "QLabel#CommandEntryNoticeText { color: #ffb0bb; "
            "background: transparent; "
            f"font: 600 15px '{_command_font_family()}'; }}"
        )
        self.message_scroll = QtWidgets.QScrollArea(
            self.panel
        )
        self.message_scroll.setObjectName(
            "CommandEntryNoticeScroll"
        )
        self.message_scroll.setWidgetResizable(
            True
        )
        self.message_scroll.setFrameShape(
            QtWidgets.QFrame.NoFrame
        )
        self.message_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.message_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.message_scroll.setStyleSheet(
            "QScrollArea#CommandEntryNoticeScroll, "
            "QScrollArea#CommandEntryNoticeScroll > QWidget > QWidget {"
            "background: transparent; border: none;}"
        )
        self.message_scroll.setWidget(
            self.message
        )
        layout.addWidget(
            self.message_scroll,
            1,
        )

        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(
            self
        )
        self._opacity_effect.setOpacity(
            1.0
        )
        self.setGraphicsEffect(
            self._opacity_effect
        )

        self._fade_animation = QtCore.QPropertyAnimation(
            self._opacity_effect,
            b"opacity",
            self,
        )
        self._fade_animation.setDuration(
            self.FADE_DURATION_MS
        )
        self._fade_animation.setStartValue(
            1.0
        )
        self._fade_animation.setEndValue(
            0.0
        )
        self._fade_animation.setEasingCurve(
            QtCore.QEasingCurve.InOutSine
        )
        self._fade_animation.finished.connect(
            self.dismiss
        )

        self._fade_timer = QtCore.QTimer(
            self
        )
        self._fade_timer.setSingleShot(
            True
        )
        self._fade_timer.setInterval(
            self.AUTO_DISMISS_MS - self.FADE_DURATION_MS
        )
        self._fade_timer.timeout.connect(
            self._begin_fade
        )

        self._dismiss_timer = QtCore.QTimer(
            self
        )
        self._dismiss_timer.setSingleShot(
            True
        )
        self._dismiss_timer.setInterval(
            self.AUTO_DISMISS_MS
        )
        self._dismiss_timer.timeout.connect(
            self.dismiss
        )
        self.hide()

    def present(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(
            parent.rect()
        )
        self._position_panel()
        self.show()
        self.raise_()
        self._fade_animation.stop()
        self._opacity_effect.setOpacity(1.0)
        self._fade_timer.start()
        self._dismiss_timer.start()

    def dismiss(self) -> None:
        self._fade_timer.stop()
        self._dismiss_timer.stop()
        self._fade_animation.stop()
        self._opacity_effect.setOpacity(1.0)
        self.hide()

    def _begin_fade(self) -> None:
        if not self.isVisible():
            return
        self._fade_animation.stop()
        self._fade_animation.setStartValue(
            self._opacity_effect.opacity()
        )
        self._fade_animation.start()

    def _position_panel(self) -> None:
        panel_width = max(
            1,
            min(920, self.width() - 64),
        )
        self.panel.setFixedWidth(
            panel_width
        )
        content_width = max(1, panel_width - 68)

        def wrapped_text_height(label: QtWidgets.QLabel) -> int:
            document = QtGui.QTextDocument()
            document.setDefaultFont(label.font())
            document.setDocumentMargin(0)
            document.setPlainText(label.text())
            document.setTextWidth(content_width)
            return max(
                label.fontMetrics().lineSpacing(),
                int(document.size().height() + 0.999),
            )

        completion_height = wrapped_text_height(self.completion_message)
        message_height = wrapped_text_height(self.message)
        completion_widget_height = completion_height + 8
        message_widget_height = message_height + 8
        self.completion_message.setMinimumHeight(completion_widget_height)
        self.message.setMinimumHeight(message_widget_height)
        desired_height = (
            28
            + completion_widget_height
            + 18
            + message_widget_height
            + 28
            + 4
        )
        panel_height = max(
            1,
            min(desired_height, self.height() - 64),
        )
        self.panel.setFixedHeight(panel_height)
        self.panel.move(
            max(0, (self.width() - self.panel.width()) // 2),
            max(0, (self.height() - self.panel.height()) // 2),
        )

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(
            event
        )
        self._position_panel()

    def mousePressEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        self.dismiss()
        event.accept()


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
        load_application_fonts(self.project_root)
        _apply_command_font(self)
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

        app_paths = application_paths(
            self.project_root
        )
        icons_dir = app_paths.app_resource_path(
            "icons"
        )
        countdown_sound_path = app_paths.app_resource_path(
            "sounds/Blip.mp3"
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
            self,
            countdown_sound_path=countdown_sound_path,
        )
        set_control_help(
            self.go_btn,
            "Hold Go for three seconds to finish this letter and clear the workspace for a new project.",
            accessible_name="Finish letter and start new project",
        )

        self._interaction_state = "idle"
        self._confirm_dialog: Optional[_ConfirmDialog] = None
        self._command_bar_data: Optional[CommandBarData] = None

        self.go_btn.clicked.connect(
            self._do_reset
        )

        self._entry_notice = _CommandEntryNotice(
            self
        )

        self._relayout()

    def showEvent(
        self,
        event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(
            event
        )
        QtCore.QTimer.singleShot(
            0,
            self._entry_notice.present,
        )

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        self._entry_notice.dismiss()
        self.go_btn.cancel_hold(
            animate=False
        )
        super().hideEvent(
            event
        )

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
        set_control_help(
            self.go_btn,
            "Confirm or cancel clearing the current letter in the open dialog.",
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
        set_control_help(
            self.go_btn,
            "Clearing the completed letter and preparing a new workspace.",
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
        set_control_help(
            self.go_btn,
            "Hold Go for three seconds to finish this letter and clear the workspace for a new project.",
            accessible_name="Finish letter and start new project",
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
        if self._entry_notice.isVisible():
            self._entry_notice.setGeometry(
                self.rect()
            )
            self._entry_notice.raise_()


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
