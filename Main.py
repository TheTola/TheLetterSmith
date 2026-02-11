#!/usr/bin/env python3
# File: Main.py
# -*- coding: utf-8 -*-

"""
Letter Smith — Application Entry Point (Clean, Non-Legacy)

This file is intentionally boring.

It exists to:
  1) Put the process in a known-good execution state (root, sys.path).
  2) Bring up Qt safely (one QApplication, proper app identity, icon).
  3) Make crashes impossible to miss (console traceback + modal dialog).
  4) Launch the main window (Nexus) and hand over control to Qt.

What does NOT belong here:
  - Export pipeline logic (Forge/Generate/Transmuter)
  - Asset processing / base64 packing / build automation
  - Any filesystem mutation beyond "set CWD" and reading settings

If you ever feel tempted to add "just one more helper" here:
  - Put it in the module that owns the feature.
  - Keep this entrypoint as pure orchestration.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets


# =============================================================================
# App Identity
# =============================================================================
# These values become visible in OS dialogs / settings storage / etc.
# Keep them stable unless you are intentionally migrating user settings.

APP_NAME: str = "Letter Smith"
ORG_NAME: str = "Infini Works"
ORG_DOMAIN: str = "infini.works"

# Optional configuration file (safe to delete; app boots without it).
SETTINGS_FILE: str = "settings.json"


# =============================================================================
# Environment & Root Resolution
# =============================================================================

def is_frozen() -> bool:
    """
    Returns True if running from a frozen executable (e.g., PyInstaller).
    When frozen, __file__ may not behave like a normal source run.
    """
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resolve_project_root() -> Path:
    """
    The single most important invariant in the app: ROOT.

    Root rules:
      - Frozen: directory containing the executable
      - Source : directory containing Main.py

    Everything else in the project assumes "root-relative paths" from here.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def set_cwd(root: Path) -> None:
    """
    Best-effort sets process working directory to project root.

    Why:
      - Relative path code becomes predictable.
      - Running from IDE / terminal / file explorer behaves the same.

    Non-fatal on purpose:
      - If the OS denies it, we still allow boot to continue.
    """
    try:
        os.chdir(str(root))
    except Exception:
        pass


def ensure_root_on_syspath(root: Path) -> None:
    """
    Ensures local imports work even when launched from odd working directories.

    Why:
      - On Windows especially, sys.path can vary depending on how the user
        launches the program (IDE vs shell vs file association).
      - Injecting root at index 0 ensures the project modules are always found
        before any similarly-named installed packages.
    """
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


# =============================================================================
# Settings (Optional)
# =============================================================================

def load_settings(root: Path) -> dict:
    """
    Loads settings.json if present.

    Rules:
      - Missing settings file is normal and should not warn.
      - Invalid JSON should not prevent boot (return {}).
      - Settings should only influence cosmetic/boot-level toggles (debug, icon),
        not core behavior that could surprise users.
    """

    path = root / SETTINGS_FILE
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


# =============================================================================
# Logging
# =============================================================================

def setup_logging(settings: dict) -> None:
    """
    Configures console logging.

    Policy:
      - Keep default logs terse and readable.
      - Enable deeper logs only if settings.json sets "debug": true.

    Notes:
      - `force=True` ensures we control logging even if some imported module
        configured logging earlier (common in larger apps).
    """
    debug = bool(settings.get("debug", False))
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def log_startup(root: Path, icon: Optional[Path], settings: dict) -> None:
    """
    One clean startup banner.

    This is the fastest sanity check for:
      - Python version
      - Qt version
      - frozen/source mode
      - resolved root
      - chosen icon
    """
    qt_ver = QtCore.qVersion()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    mode = "frozen" if is_frozen() else "source"

    logging.info(f"— {APP_NAME} —")
    logging.info(f"[Env] Python {py_ver} | Qt {qt_ver} | Mode: {mode}")
    logging.info(f"[Root] {root}")
    logging.info(f"[Icon] {icon if icon else 'None'}")

    if bool(settings.get("debug", False)):
        logging.info(f"[Args] {' '.join(sys.argv)}")


# =============================================================================
# Icon Selection
# =============================================================================

def pick_icon(root: Path, settings: dict) -> Optional[Path]:
    """
    Returns the best icon file path, or None.

    Supports an explicit override:
      settings.json: { "app_icon": "relative/or/absolute/path/to.ico" }

    If override is missing/invalid, use conventional project fallbacks.

    Notes:
      - Windows expects .ico for application icons.
      - The selection order should match your canonical convention.
    """
    override = settings.get("app_icon")
    if isinstance(override, str) and override.strip():
        p = Path(override)
        if not p.is_absolute():
            p = (root / p).resolve()
        if p.exists():
            logging.info(f"[Icon] Using (settings override): {p}")
            return p
        logging.info(f"[Icon] Override not found: {p}")

    candidates: Tuple[Path, ...] = (
        # preferred canonical path
        root / "gallery" / "icon" / "ls-icon.ico",
        # compatibility fallbacks
        root / "gallery" / "icon" / "LSmith.ico",
        root / "gallery" / "icons" / "LSmith.ico",
        root / "gallery" / "icons" / "ls-icon.ico",
    )

    for p in candidates:
        if p.exists():
            logging.info(f"[Icon] Using: {p}")
            return p

    logging.info("[Icon] No icon found (default will be used).")
    return None


# =============================================================================
# Qt Bootstrap + Crash Visibility
# =============================================================================

def bootstrap_qt(icon: Optional[Path]) -> QtWidgets.QApplication:
    """
    Creates a QApplication exactly once.

    Why:
      - Some workflows embed the app or relaunch components.
      - Using QApplication.instance() prevents "QApplication already exists" errors.

    Also sets:
      - App identity (names/domains)
      - Window icon (if available)
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(ORG_DOMAIN)

    if icon and icon.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon)))

    return app


def _show_critical(title: str, message: str) -> None:
    """
    Best-effort modal error dialog. Never raises.

    Used for:
      - Startup failures (cannot import Nexus)
      - Unhandled exceptions after Qt event loop begins
    """
    try:
        QtWidgets.QMessageBox.critical(None, title, message)
    except Exception:
        # If Qt is in a broken state, still don't crash while trying to report.
        pass


def install_exception_hook(app_name: str) -> None:
    """
    Makes exceptions visible.

    Without this:
      - Exceptions inside Qt callbacks can get swallowed or become silent.
      - Users only see "it closed" or "it froze".

    With this:
      - Console gets full traceback
      - User gets a minimal modal dialog
    """
    def _hook(exc_type, exc, tb) -> None:
        # Let Ctrl+C behave normally in terminals/IDEs.
        if exc_type is KeyboardInterrupt:
            raise exc

        trace = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.error("[Crash] Unhandled exception:")
        logging.error(trace.rstrip())

        _show_critical(
            f"{app_name} — Crash",
            "An unexpected error occurred:\n\n"
            f"{exc}\n\n"
            "A traceback was printed to the console."
        )

    sys.excepthook = _hook


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    """
    Boot sequence (ordered intentionally):

      1) Resolve ROOT and normalize execution context (CWD + sys.path)
         - prevents path and import weirdness.

      2) Load settings (optional)
         - used only for logging verbosity and icon override.

      3) Initialize logging BEFORE most work
         - guarantees everything prints consistently.

      4) Pick icon and print the startup banner
         - immediate diagnostics when something is wrong.

      5) Create QApplication + install exception hook
         - Qt is now ready and crashes are visible.

      6) Late-import Nexus and show it
         - keeps entrypoint dependency surface small.
    """
    root = resolve_project_root()
    set_cwd(root)
    ensure_root_on_syspath(root)

    settings = load_settings(root)
    setup_logging(settings)

    icon = pick_icon(root, settings)
    log_startup(root, icon, settings)

    app = bootstrap_qt(icon)
    install_exception_hook(APP_NAME)


    # Late import:
    # - avoids importing half the app before logging + crash handling exists
    # - reduces failure blast radius on boot
    try:
        from Nexus import Nexus  # noqa: E402
    except Exception as ex:
        msg = f"Failed to import Nexus:\n\n{ex}"
        logging.error(msg)
        _show_critical(f"{APP_NAME} — Startup Error", msg)
        raise

    win = Nexus(root)
    win.show()

    # Control transfers to Qt; this call blocks until the app exits.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()