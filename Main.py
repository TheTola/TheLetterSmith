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

import json
import logging
import os
import re
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from application_identity import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    ORGANIZATION_DOMAIN,
    PUBLISHER_NAME,
)
from app_icon import configure_windows_app_identity, resolve_app_icon
from project_paths import (
    ApplicationPaths,
    application_paths,
    configure_application_paths,
)
from performance_trace import PerformanceTimer


# =============================================================================
# Application identity
# =============================================================================

APP_NAME: str = APPLICATION_NAME
APP_VERSION: str = APPLICATION_VERSION
ORG_NAME: str = PUBLISHER_NAME
ORG_DOMAIN: str = ORGANIZATION_DOMAIN

SETTINGS_FILE: str = "settings.json"
LOG_FILE_NAME: str = "lettersmith.log"

_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|client_secret|code|code_verifier|"
    r"code_challenge|device_code|state|user_code|x-amz-credential|x-amz-signature|"
    r"x-amz-security-token)=)[^&#\s]+"
)
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?ix)(\b(?:access_token|refresh_token|secret_access_key|access_key_id|"
    r"client_secret|code_verifier|device_code|user_code)\b\s*[\"']?\s*[:=]\s*[\"']?)"
    r"[^\"'\s,;&}\]]+"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?ix)(\bAuthorization\b\s*[\"']?\s*[:=]\s*[\"']?)"
    r"(?:Bearer\s+)?[^\"',;\r\n}\]]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\"'}\]]+")
_GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", re.IGNORECASE)


def redact_sensitive_diagnostics(value: object) -> str:
    """Remove authentication material from rendered diagnostic text."""
    text = str(value)
    text = _SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", text)
    text = _SENSITIVE_FIELD_PATTERN.sub(r"\1[REDACTED]", text)
    text = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    return _GITHUB_TOKEN_PATTERN.sub("[REDACTED]", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_diagnostics(super().format(record))


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
        PyInstaller's read-only bundled resource directory.

    Source application:
        Directory containing Main.py.
    """
    return ApplicationPaths.for_runtime().resource_root


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
        pass


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
    path = application_paths(root).settings_file

    if not path.exists():
        return {}

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        )

        return (
            value
            if isinstance(
                value,
                dict,
            )
            else {}
        )

    except Exception:
        return {}


# =============================================================================
# Logging
# =============================================================================

def setup_logging(
    settings: dict,
    log_directory: Path | None = None,
) -> None:
    """
    Configure console logging and a bounded diagnostic file.
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

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_RedactingFormatter("%(message)s"))
    handlers: list[logging.Handler] = [console_handler]
    file_error: OSError | None = None
    if log_directory is not None:
        try:
            log_directory.mkdir(
                parents=True,
                exist_ok=True,
            )
            file_handler = RotatingFileHandler(
                log_directory / LOG_FILE_NAME,
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                _RedactingFormatter(
                    "%(asctime)s %(levelname)s %(name)s %(message)s"
                )
            )
            handlers.append(file_handler)
        except OSError as error:
            file_error = error

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=handlers,
        force=True,
    )
    if file_error is not None:
        logging.warning(
            "[Logging] Diagnostic file is unavailable: %s",
            file_error,
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
        pass


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
        f"[Workspace] {root}"
    )

    paths = application_paths(root)
    logging.info(f"[Resources] {paths.resource_root}")
    logging.info(f"[Saved Letters] {paths.saved_letters_root}")

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
            f"[Autosave] {paths.autosave_root}"
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

    application.setApplicationVersion(
        APP_VERSION
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
        pass


def install_exception_hook(
    app_name: str,
    log_path: Path | None = None,
) -> None:
    """
    Record uncaught main-thread, background-thread, and unraisable failures.
    """

    def _record_failure(
        context: str,
        exception_type,
        exception,
        traceback_object,
    ) -> None:
        trace = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                traceback_object,
            )
        )
        logging.critical(
            "[Crash] %s\n%s",
            context,
            trace.rstrip(),
        )

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
                    pass

            return

        _record_failure(
            "Unhandled main-thread exception.",
            exception_type,
            exception,
            traceback_object,
        )

        details = (
            f"\n\nDetails were written to:\n{log_path}"
            if log_path is not None
            else "\n\nDetails were written to the Letter Smith diagnostic log."
        )

        _show_critical(
            f"{app_name} — Crash",
            "An unexpected error occurred. Letter Smith may need to close."
            "\n\n"
            f"{type(exception).__name__}: {exception}"
            f"{details}",
        )

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        thread_name = str(getattr(args.thread, "name", "") or "background")
        _record_failure(
            f"Unhandled exception in background thread {thread_name!r}.",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    def _unraisable_hook(args) -> None:
        context = str(args.err_msg or "Unraisable exception.")
        _record_failure(
            context,
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    sys.unraisablehook = _unraisable_hook


# =============================================================================
# Shutdown
# =============================================================================

def connect_shutdown_handler(
    application: QtWidgets.QApplication,
    window: QtWidgets.QWidget,
) -> None:
    """
    Connect the authoritative Nexus cleanup path to Qt application shutdown.
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

    completed = False

    def run_shutdown() -> None:
        nonlocal completed
        if completed:
            return
        completed = True
        logging.info("[Shutdown] Cleanup started.")
        try:
            shutdown()

        except Exception:
            logging.exception("[Shutdown] Cleanup failed.")
        else:
            logging.info("[Shutdown] Cleanup completed.")

    application.aboutToQuit.connect(
        run_shutdown
    )


# =============================================================================
# Main
# =============================================================================

def _resolve_startup_choices(
    root: Path,
    *,
    force_theme_prompt: bool,
) -> tuple[str, str]:
    from publishing.github_ui import prompt_for_github_startup
    from startup_theme import ensure_startup_theme_preference

    github_action = prompt_for_github_startup()
    startup_theme = ensure_startup_theme_preference(
        root,
        force_prompt=force_theme_prompt,
    )
    return github_action, startup_theme


def _upgrade_saved_letters_at_startup(project_root: Path) -> None:
    """Convert pre-release saved letters before the strict catalog opens."""
    try:
        from tools.upgrade_saved_letters import upgrade_saved_letter_library

        migrated, skipped = upgrade_saved_letter_library(project_root)
    except Exception:
        logging.exception("[Boot] Saved-letter schema upgrade failed.")
        return
    if migrated:
        logging.info(
            "[Boot] Upgraded %d saved letter(s) to the current schema.",
            migrated,
        )
    if skipped:
        logging.warning(
            "[Boot] Skipped %d incomplete or malformed saved letter(s).",
            skipped,
        )


def main() -> None:
    """
    Launch Letter Smith.
    """
    resource_root = resolve_project_root()
    paths = configure_application_paths(
        ApplicationPaths.for_runtime(resource_root)
    )
    setup_logging({}, paths.logs_root)
    try:
        paths.initialize(resource_root)
    except Exception:
        logging.exception("[Boot] Writable storage initialization failed.")
        raise
    root = paths.workspace_root

    set_cwd(
        root
    )

    ensure_root_on_syspath(
        resource_root
    )

    settings = load_settings(
        root
    )

    setup_logging(
        settings,
        paths.logs_root,
    )
    logging.info(
        "[Logging] Diagnostic log: %s",
        paths.logs_root / LOG_FILE_NAME,
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

    from ui_fonts import load_application_fonts

    load_application_fonts(root)

    install_exception_hook(
        APP_NAME,
        paths.logs_root / LOG_FILE_NAME,
    )

    _upgrade_saved_letters_at_startup(root)

    # GitHub connection is resolved before the theme prompt. Stored secure
    # credentials bypass the modal; disconnected users must connect or cancel.
    try:
        from config import FORCE_THEME_FAMILY_PROMPT

        _github_startup_action, startup_theme = _resolve_startup_choices(
            root,
            force_theme_prompt=FORCE_THEME_FAMILY_PROMPT,
        )
    except Exception as error:
        logging.exception("[Boot] Startup setup failed.")
        _show_critical(
            f"{APP_NAME} — Startup Error",
            f"Startup setup could not be completed: {error}",
        )
        raise
    if not startup_theme:
        logging.info("[Boot] Startup theme selection was canceled.")
        logging.shutdown()
        return

    nexus_startup_timer = PerformanceTimer("startup.nexus_visible")

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
    nexus_startup_timer.finish(
        project_ready=bool(
            getattr(getattr(window, "project_state", None), "is_project_ready", False)
        )
    )

    connect_shutdown_handler(
        application,
        window,
    )

    logging.info(
        "[Boot] "
        f"Nexus visible={window.isVisible()} "
        f"minimized={window.isMinimized()}"
    )

    try:
        exit_code = application.exec()
    except BaseException:
        logging.exception("[Crash] Qt event loop failed.")
        raise

    logging.info("[Shutdown] Qt event loop stopped with code %s.", exit_code)
    logging.shutdown()

    raise SystemExit(
        exit_code
    )


if __name__ == "__main__":
    main()
