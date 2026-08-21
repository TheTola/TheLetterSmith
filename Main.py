#!/usr/bin/env python3
# File: Main.py
# -*- coding: utf-8 -*-

"""
Letter Smith — Application Entry Point

Responsibilities:
    1. Resolve the project root.
    2. Normalize the working directory and import path.
    3. Configure logging and Qt.
    4. Load Nexus.
    5. Display startup and runtime errors clearly.
    6. Cleanly shut down resources when supported.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from app_paths import (
    APP_NAME,
    APP_VERSION,
    ORG_DOMAIN,
    ORG_NAME,
    resolve_application_root,
    resolve_user_data_root,
)
from app_icon import configure_windows_app_identity, resolve_app_icon
from project_paths import PROJECTS_RELATIVE_PATH
from settings_store import SettingsStore
from startup_check import run_startup_self_check


# =============================================================================
# Application identity
# =============================================================================

# =============================================================================
# Environment and project root
# =============================================================================

def is_frozen() -> bool:
    """
    Return True when running from a frozen executable such as PyInstaller.
    """
    return bool(
        getattr(sys, "frozen", False)
        and hasattr(sys, "_MEIPASS")
    )


def resolve_project_root() -> Path:
    """
    Resolve the canonical Letter Smith project directory.

    Frozen application:
        Directory containing the executable.

    Source application:
        Directory containing Main.py.
    """
    return resolve_application_root()


def set_cwd(
    root: Path,
) -> None:
    """
    Set the process working directory to the project root.
    """
    try:
        os.chdir(
            str(root)
        )

    except Exception:
        logging.getLogger(__name__).exception("Could not set the application directory: %s", root)


def ensure_root_on_syspath(
    root: Path,
) -> None:
    """
    Ensure local project modules are imported from the project root.
    """
    root_string = str(root)

    if root_string not in sys.path:
        sys.path.insert(
            0,
            root_string,
        )


# =============================================================================
# Settings
# =============================================================================

def load_settings(
    root: Path,
) -> dict:
    """
    Load settings.json when it exists.

    Missing or invalid settings must not prevent application startup.
    """
    try:
        return SettingsStore(root).snapshot()
    except Exception:
        logging.getLogger(__name__).exception("Application settings could not be loaded.")
        return {}


# =============================================================================
# Logging
# =============================================================================

def setup_logging(
    settings: dict,
    root: Path,
) -> None:
    """
    Configure console logging.
    """
    debug = bool(
        settings.get(
            "debug",
            False,
        )
    )

    level = (
        logging.DEBUG
        if debug
        else logging.INFO
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        logs = resolve_user_data_root(root) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                logs / "lettersmith.log",
                maxBytes=1_000_000,
                backupCount=4,
                encoding="utf-8",
            )
        )
    except OSError:
        logging.getLogger(__name__).exception("Application log file could not be opened.")

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def configure_qt_logging() -> None:
    """
    Suppress unnecessary Qt Multimedia FFmpeg diagnostic output.
    """
    try:
        QtCore.QLoggingCategory.setFilterRules(
            "qt.multimedia.ffmpeg*=false"
        )

    except Exception:
        logging.getLogger(__name__).exception("Qt logging configuration failed.")


def log_startup(
    root: Path,
    icon: Optional[Path],
    settings: dict,
) -> None:
    """
    Print the application startup information.
    """
    qt_version = (
        QtCore.qVersion()
    )

    python_version = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )

    mode = (
        "frozen"
        if is_frozen()
        else "source"
    )

    logging.info(
        f"— {APP_NAME} {APP_VERSION} —"
    )

    logging.info(
        f"[Env] Python {python_version} "
        f"| Qt {qt_version} "
        f"| Mode: {mode}"
    )

    logging.info(
        f"[Root] {root}"
    )

    logging.info(
        f"[Icon] "
        f"{icon if icon else 'None'}"
    )

    if bool(
        settings.get(
            "debug",
            False,
        )
    ):
        logging.info(
            f"[Projects] {(root / PROJECTS_RELATIVE_PATH).resolve()}"
        )
        logging.info(
            f"[Args] {' '.join(sys.argv)}"
        )


# =============================================================================
# Icon selection
# =============================================================================

def pick_icon(
    root: Path,
    settings: dict,
) -> Optional[Path]:
    """
    Return the best available application icon.
    """
    override = settings.get(
        "app_icon"
    )

    if isinstance(override, str) and override.strip():
        path = Path(override)
        if not path.is_absolute():
            path = (root / path).resolve()
        if path.is_file():
            logging.info("[Icon] Using (settings override): %s", path)
            return path
        logging.info("[Icon] Override not found: %s", path)

    path = resolve_app_icon(root, prefer_png=False)
    if path is not None:
        logging.info("[Icon] Using canonical taskbar icon: %s", path)
        return path

    logging.info(
        "[Icon] No icon found "
        "(default will be used)."
    )

    return None


# =============================================================================
# Qt bootstrap
# =============================================================================

def bootstrap_qt(
    icon: Optional[Path],
) -> QtWidgets.QApplication:
    """
    Create or reuse the QApplication instance.
    """
    application = (
        QtWidgets.QApplication.instance()
    )

    if application is None:
        application = (
            QtWidgets.QApplication(
                sys.argv
            )
        )

    application.setApplicationName(
        APP_NAME
    )

    application.setOrganizationName(
        ORG_NAME
    )

    application.setOrganizationDomain(
        ORG_DOMAIN
    )

    if (
        icon is not None
        and icon.exists()
    ):
        application.setWindowIcon(
            QtGui.QIcon(
                str(icon)
            )
        )

    return application


# =============================================================================
# Error reporting
# =============================================================================

def _show_critical(
    title: str,
    message: str,
) -> None:
    """
    Display a critical error dialog without allowing reporting to crash.
    """
    try:
        QtWidgets.QMessageBox.critical(
            None,
            title,
            message,
        )

    except Exception:
        logging.getLogger(__name__).exception("Critical error dialog could not be displayed.")


def install_exception_hook(
    app_name: str,
) -> None:
    """
    Install a global exception hook for visible Qt callback failures.
    """

    def _hook(
        exception_type,
        exception,
        traceback_object,
    ) -> None:
        try:
            is_interrupt = issubclass(
                exception_type,
                KeyboardInterrupt,
            )

        except Exception:
            is_interrupt = (
                exception_type
                is KeyboardInterrupt
            )

        if is_interrupt:
            application = (
                QtWidgets.QApplication
                .instance()
            )

            if application is not None:
                try:
                    application.quit()

                except Exception:
                    logging.getLogger(__name__).exception("Application quit request failed.")

            return

        trace = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                traceback_object,
            )
        )

        logging.error(
            "[Crash] Unhandled exception:"
        )

        logging.error(
            trace.rstrip()
        )

        _show_critical(
            f"{app_name} — Crash",
            "An unexpected error occurred:"
            "\n\n"
            f"{exception}"
            "\n\n"
            "A traceback was printed "
            "to the console.",
        )

    sys.excepthook = _hook


# =============================================================================
# Shutdown
# =============================================================================

def connect_shutdown_handler(
    application: QtWidgets.QApplication,
    window: QtWidgets.QWidget,
) -> None:
    """
    Connect Nexus.shutdown when the loaded Nexus implementation provides it.

    This maintains compatibility with older Nexus versions while allowing newer
    versions to release WebEngine, sound, Forge, and auxiliary-window resources.
    """
    shutdown = getattr(
        window,
        "shutdown",
        None,
    )

    if not callable(shutdown):
        logging.info(
            "[Shutdown] Nexus has no "
            "shutdown handler; using Qt's "
            "default cleanup."
        )

        return

    def run_shutdown() -> None:
        try:
            shutdown()

        except Exception as error:
            logging.error(
                "[Shutdown] Cleanup failed: "
                f"{type(error).__name__}: "
                f"{error}"
            )

    application.aboutToQuit.connect(
        run_shutdown
    )


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    """
    Launch Letter Smith.
    """
    root = resolve_project_root()

    set_cwd(
        root
    )

    ensure_root_on_syspath(
        root
    )

    settings = load_settings(
        root
    )

    setup_logging(
        settings,
        root,
    )

    configure_qt_logging()

    # Windows assigns taskbar buttons to the process AppUserModelID. Set it
    # before QApplication creates any windows so Letter Smith is not grouped
    # under the default Python identity.
    configure_windows_app_identity()

    icon = pick_icon(
        root,
        settings,
    )

    log_startup(
        root,
        icon,
        settings,
    )

    application = bootstrap_qt(
        icon
    )

    startup_issues = run_startup_self_check(root)
    critical_issues = tuple(issue for issue in startup_issues if issue.critical)
    if critical_issues:
        message = "\n".join(issue.message for issue in critical_issues)
        logging.error("Startup self-check failed:\n%s", message)
        _show_critical(f"{APP_NAME} — Startup Error", message)
        raise SystemExit(1)

    install_exception_hook(
        APP_NAME
    )

    # Import Nexus only after logging and Qt have been initialized.
    try:
        from Nexus import Nexus

    except Exception as error:
        message = (
            "Failed to import Nexus:"
            "\n\n"
            f"{type(error).__name__}: "
            f"{error}"
        )

        logging.error(
            message
        )

        logging.error(
            traceback.format_exc().rstrip()
        )

        _show_critical(
            f"{APP_NAME} — Startup Error",
            message,
        )

        raise

    try:
        window = Nexus(
            root
        )

    except Exception as error:
        message = (
            "Failed to create the "
            "Letter Smith window:"
            "\n\n"
            f"{type(error).__name__}: "
            f"{error}"
        )

        logging.error(
            message
        )

        logging.error(
            traceback.format_exc().rstrip()
        )

        _show_critical(
            f"{APP_NAME} — Startup Error",
            message,
        )

        raise

    window.show()

    connect_shutdown_handler(
        application,
        window,
    )

    logging.info(
        "[Boot] "
        f"Nexus visible={window.isVisible()} "
        f"minimized={window.isMinimized()}"
    )

    exit_code = (
        application.exec()
    )

    raise SystemExit(
        exit_code
    )


if __name__ == "__main__":
    main()
