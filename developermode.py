from __future__ import annotations

"""Existing developer shortcut, scoped to appearance preferences only.

Projects, recovery, libraries and secure account storage are never reset here.
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from application_identity import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    ORGANIZATION_DOMAIN,
    PUBLISHER_NAME,
)
from project_paths import (
    ApplicationPaths,
    application_paths,
    configure_application_paths,
)
from settings_store import SettingsStore
from ui_dialogs import show_lettersmith_message
from ui_fonts import load_application_fonts


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
    def __init__(self, project_root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reset Appearance Preferences")
        self.setMinimumWidth(400)
        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "Reset the application theme and theme-family choice?\n"
            "The default appearance applies now; the startup appearance choice returns next launch.\n"
            "Projects, libraries, window preferences and connected accounts are kept.")
        description.setWordWrap(True)
        layout.addWidget(description)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.developer_button = buttons.addButton("Reset Appearance Preferences", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def perform_developer_reset(
    project_root: str | Path, *, window=None, project_state=None,
    credential_store=None, save_current=None,
) -> DeveloperResetResult:
    """Reset only the two existing appearance preferences; compatibility args are inert."""
    from ui_theme import DEFAULT_THEME_ID
    settings = SettingsStore(project_root)
    settings.update_fields(application_theme="", theme_family="")
    theme = getattr(window, "theme_service", None)
    if theme is not None:
        theme.set_theme(DEFAULT_THEME_ID, persist=False)
    return DeveloperResetResult(None, "Appearance preferences reset. Projects and accounts are unchanged.")


def run_developer_mode(*, project_root, parent=None, credential_store=None) -> bool:
    dialog = DeveloperModeDialog(project_root, parent)
    try:
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return False
        try:
            result = perform_developer_reset(project_root, window=parent, credential_store=credential_store)
        except Exception as error:
            LOGGER.exception("Appearance preference reset failed.")
            show_lettersmith_message(parent, "Reset Appearance Preferences", str(error))
            return False
        status = getattr(parent, "status", None)
        if callable(status):
            status(result.save_skipped_reason)
        return True
    finally:
        dialog.deleteLater()


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
