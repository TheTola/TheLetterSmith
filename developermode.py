from __future__ import annotations

"""Developer reset launcher for Letter Smith.

Run this file directly, or press Ctrl+Alt+Shift+D inside Letter Smith.
The reset preserves durable libraries and saved letters while clearing the
active project, GitHub authorization, and first-run theme-family preference.
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

import generate
from application_identity import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    ORGANIZATION_DOMAIN,
    PUBLISHER_NAME,
)
from config import MESSAGE_HTML_FILE, ensure_output_dirs
from message_html import read_text_normalized
from project_paths import (
    ApplicationPaths,
    application_paths,
    configure_application_paths,
)
from project_state import ProjectStateController
from protected_projects import is_protected_project
from readiness import evaluate_readiness
from saved_letters import record_saved_letter_activity, update_saved_metadata
from settings_store import SettingsStore
from startup_theme import _GlowToolButton
from ui_dialogs import show_lettersmith_message
from ui_fonts import load_application_fonts, resolve_registered_family


LOGGER = logging.getLogger(__name__)
DEVELOPER_MODE_SHORTCUT = "Ctrl+Alt+Shift+D"
DEVELOPER_MODE_ICON = Path("icons") / "Dev.png"


class DeveloperModeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeveloperResetResult:
    saved_letter: Path | None
    save_skipped_reason: str


class DeveloperModeDialog(QtWidgets.QDialog):
    """Single-action reset dialog using the supplied Developer Mode artwork."""

    def __init__(
        self,
        project_root: str | Path,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.setObjectName("DeveloperModeDialog")
        self.setWindowTitle("")
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Popup, True)
        self.setModal(True)
        self.setMinimumSize(920, 550)
        self.setStyleSheet(
            "QDialog#DeveloperModeDialog{background:#07090d;color:#f4f7fb;}"
            "QToolButton{background:transparent;border:none;padding:0;}"
            "QToolButton:hover{background:transparent;border:none;}"
            "QToolButton:pressed{background:transparent;border:none;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 26)
        layout.setSpacing(0)

        icon_size = QtCore.QSize(840, 473)
        self.developer_button = _GlowToolButton(
            "#32d8ff",
            self,
            icon_size=icon_size,
            depressed_icon_size=QtCore.QSize(756, 426),
        )
        self.developer_button.setObjectName("DeveloperModeButton")
        self.developer_button.setAccessibleName("Developer Mode")
        self.developer_button.setToolTip(
            "Run the Developer Mode reset. Close this window to cancel."
        )
        self.developer_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonIconOnly
        )
        self.developer_button.setFixedSize(860, 500)
        self.developer_button.setIconSize(icon_size)
        icon_path = application_paths(self.project_root).app_resource_path(
            DEVELOPER_MODE_ICON
        )
        icon = QtGui.QIcon(str(icon_path))
        if icon.isNull():
            self.developer_button.setText("Developer Mode")
            self.developer_button.setToolButtonStyle(
                QtCore.Qt.ToolButtonTextOnly
            )
            font = QtGui.QFont(
                resolve_registered_family("Cinzel"),
                32,
            )
            font.setBold(True)
            self.developer_button.setFont(font)
        else:
            self.developer_button.setText("")
            self.developer_button.setIcon(icon)
        self.developer_button.clicked.connect(self.accept)
        layout.addWidget(
            self.developer_button,
            0,
            QtCore.Qt.AlignCenter,
        )


def _save_current_letter_if_ready(
    project_root: Path,
    window: QtWidgets.QWidget | None,
) -> tuple[Path | None, str]:
    settings = SettingsStore(project_root).snapshot()
    if is_protected_project(settings):
        return None, "Stock and Example Letters are already preserved."

    if window is not None:
        flush = getattr(window, "flush_prompt_writer_state", None)
        if callable(flush) and not flush():
            raise DeveloperModeError(
                "Prompt Writer state could not be saved. Nothing was reset."
            )

    readiness = evaluate_readiness(project_root)
    if not readiness.can_preview:
        missing = next(
            (item.label for item in readiness.missing_items if item.required),
            "required letter content",
        )
        return None, f"The current letter was not ready to save: {missing}."

    release_preview = getattr(window, "_release_forge_preview_files", None)
    if callable(release_preview):
        release_preview()

    message_path = project_root / MESSAGE_HTML_FILE
    try:
        message = (
            read_text_normalized(message_path)
            if message_path.is_file()
            else ""
        )
        ensure_output_dirs(project_root)
        play_dir, _rebuilt = generate.ensure_play_bundle(
            project_root,
            message_html=message,
            force=True,
        )
        update_saved_metadata(play_dir, project_root, readiness)
        record_saved_letter_activity(play_dir)
    except Exception as error:
        raise DeveloperModeError(
            "The current letter was ready to save but could not be saved. "
            "Nothing was reset."
        ) from error
    return Path(play_dir).resolve(), ""


def _restore_github_credential(store: object, stored: object | None) -> None:
    if stored is None:
        return
    store.save(stored)


def perform_developer_reset(
    project_root: str | Path,
    *,
    window: QtWidgets.QWidget | None = None,
    project_state: ProjectStateController | None = None,
    credential_store: object | None = None,
    save_current: Callable[
        [Path, QtWidgets.QWidget | None],
        tuple[Path | None, str],
    ] = _save_current_letter_if_ready,
) -> DeveloperResetResult:
    """Perform the developer reset without showing or closing UI."""
    root = Path(project_root).resolve()
    forge_tab = getattr(window, "forge_tab", None)
    if forge_tab is not None and bool(
        getattr(forge_tab, "operation_in_progress", False)
    ):
        raise DeveloperModeError(
            "Finish the current Forge operation before using Developer Mode."
        )

    saved_letter, skipped_reason = save_current(root, window)

    from command import start_developer_reset

    if credential_store is None:
        from publishing.github_auth import GitHubCredentialStore

        store = GitHubCredentialStore()
    else:
        store = credential_store
    stored = store.load()
    store.clear()

    controller = project_state or getattr(window, "project_state", None)
    if controller is None:
        controller = ProjectStateController(root)
        controller.initialize()

    try:
        completed = start_developer_reset(
            window,
            project_root=root,
            project_state=controller,
        )
        if not completed:
            raise DeveloperModeError("The Developer Mode reset did not complete.")
    except Exception:
        try:
            _restore_github_credential(store, stored)
        except Exception:
            LOGGER.exception(
                "GitHub authorization could not be restored after reset failure."
            )
        raise

    return DeveloperResetResult(
        saved_letter=saved_letter,
        save_skipped_reason=skipped_reason,
    )


def run_developer_mode(
    *,
    project_root: str | Path,
    parent: QtWidgets.QWidget | None = None,
    credential_store: object | None = None,
) -> bool:
    dialog = DeveloperModeDialog(project_root, parent)
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False

    try:
        result = perform_developer_reset(
            project_root,
            window=parent,
            credential_store=credential_store,
        )
    except Exception as error:
        LOGGER.exception("Developer Mode reset failed.")
        show_lettersmith_message(
            parent,
            "Developer Mode",
            str(error).strip() or "Developer Mode could not reset Letter Smith.",
        )
        return False

    if result.saved_letter is not None:
        save_note = "The current letter was saved before reset."
    else:
        save_note = result.save_skipped_reason
    show_lettersmith_message(
        parent,
        "Developer Mode",
        f"Developer reset complete. {save_note}\n\n"
        "GitHub is disconnected. Letter Smith will close; the next launch "
        "will ask whether you are male or female.",
    )
    if parent is not None:
        QtCore.QTimer.singleShot(0, parent.close)
    return True


def _configure_standalone_paths() -> ApplicationPaths:
    resource_root = Path(__file__).resolve().parent
    paths = configure_application_paths(
        ApplicationPaths.for_runtime(resource_root)
    )
    paths.initialize(resource_root)
    return paths


def main() -> int:
    paths = _configure_standalone_paths()
    application = (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(sys.argv)
    )
    application.setApplicationName(APPLICATION_NAME)
    application.setApplicationVersion(APPLICATION_VERSION)
    application.setOrganizationName(PUBLISHER_NAME)
    application.setOrganizationDomain(ORGANIZATION_DOMAIN)
    application.setWindowIcon(
        QtGui.QIcon(str(paths.app_resource_path(DEVELOPER_MODE_ICON)))
    )
    load_application_fonts(paths.workspace_root)
    run_developer_mode(project_root=paths.workspace_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
