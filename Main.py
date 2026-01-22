#!/usr/bin/env python3
# File: Main.py
# -*- coding: utf-8 -*-
"""
Letter Smith — Application Entry Point (Professional, Streamlined, Non-Destructive)

Goals (no feature removals):
• Create QApplication exactly once (reuse if embedding launches us).
• Resolve project root reliably (frozen EXE vs source run).
• Pick a sane app icon (multi-path fallback) and log the chosen one.
• Regenerate a base64 bundle module from gallery/sounds (source runs only), atomically.
• Verify a distributable EXE exists in MAX/ and write a timestamped backup (source runs only).
• Launch Nexus (Over_Nexus-driven shell).

Notes:
• No deprecated Qt6 HiDPI attributes are set (Qt6 handles DPI automatically).
• Logging is concise by default but includes a startup banner for quick diagnostics.
• All “maintenance” steps (bundle regen / backup) are best-effort and never fatal.
"""

from __future__ import annotations

import sys
import os
import json
import time
import shutil
import base64
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

APP_NAME         = "Letter Smith"
ORG_NAME         = "Infini Works"
ORG_DOMAIN       = "infini.works"

DIST_DIRNAME     = "MAX"
BACKUPS_DIRNAME  = "Backups"
CONVERTED_DIR    = "converted64"
CONVERTED_FILE   = "convert64.py"
SETTINGS_FILE    = "settings.json"

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".svg"}
_AUD_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def project_root_from_argv() -> Path:
    """
    Root rules:
      • Frozen (PyInstaller): exe directory
      • Source run          : script's parent directory
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def chdir_root(root: Path) -> None:
    """Best-effort CWD swap to project root (non-fatal if denied)."""
    try:
        os.chdir(str(root))
    except Exception:
        pass


def load_settings(root: Path) -> Dict:
    """Read settings.json (if present). Invalid JSON falls back to {}."""
    sfile = root / SETTINGS_FILE
    if not sfile.exists():
        return {}
    try:
        return json.loads(sfile.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def pick_icon(root: Path) -> Optional[Path]:
    """
    Resolve an .ico file with robust fallbacks (both naming & folder variants).
    Preference order reflects the project’s canonical convention.
    """
    candidates: Tuple[Path, ...] = (
        root / "gallery" / "icon"  / "ls-icon.ico",   # canonical path (preferred)
        root / "gallery" / "icon"  / "LSmith.ico",
        root / "gallery" / "icons" / "LSmith.ico",
        root / "gallery" / "icons" / "ls-icon.ico",
    )
    for p in candidates:
        if p.exists():
            logging.info(f"[Icon] Using: {p}")
            return p
    logging.info("[Icon] No icon found (window will use default).")
    return None


def _iter_files(folder: Path, include_exts: Iterable[str]) -> Iterable[Path]:
    """Yield files under folder (recursively) whose extension is in include_exts."""
    if not folder.exists():
        return []
    exts = {e.lower() for e in include_exts}
    for root, _, files in os.walk(folder):
        for f in files:
            if Path(f).suffix.lower() in exts:
                yield Path(root) / f


def _b64_of(path: Path) -> str:
    """Return standard Base64 (ascii) for file bytes."""
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def _atomic_write_text(target: Path, text: str, encoding: str = "utf-8") -> None:
    """
    Write a text blob atomically:
      • write to NamedTemporaryFile in same directory
      • os.replace() into final path
    Ensures readers never see a partially written module.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding=encoding, delete=False, dir=str(target.parent), suffix=".tmp") as tf:
        temp_name = tf.name
        tf.write(text)
        tf.flush()
        os.fsync(tf.fileno())
    os.replace(temp_name, target)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Base64 bundle (source runs only)
# ─────────────────────────────────────────────────────────────────────────────

def regenerate_base64_bundle(root: Path, settings: Dict) -> None:
    """
    Scan gallery/ and sounds/ (from settings.json), plus fallbacks,
    and write converted64/convert64.py with two dicts: IMAGES, AUDIO.

    The module is intentionally tiny and static so other modules can import it cheaply.
    Only runs on source launches (frozen builds embed their own resources).
    """
    if is_frozen():
        return

    gallery_dir = settings.get("gallery_dir", "gallery")
    icons_dir   = settings.get("icons_dir", "icons")
    sounds_dir  = settings.get("sounds_dir", "sounds")

    g_base   = root / gallery_dir
    g_icons  = g_base / icons_dir
    g_sounds = g_base / sounds_dir

    # Fallbacks (if settings are incomplete)
    scan_images = [g_base, g_icons]
    scan_sounds = [g_sounds, root / "sounds"]

    images: Dict[str, str] = {}
    audio:  Dict[str, str] = {}

    for folder in scan_images:
        for f in _iter_files(folder, _IMG_EXTS):
            rel = f.relative_to(root).as_posix()
            try:
                images[rel] = _b64_of(f)
            except Exception:
                # Skip unreadable files; keep regeneration non-fatal.
                pass

    for folder in scan_sounds:
        for f in _iter_files(folder, _AUD_EXTS):
            rel = f.relative_to(root).as_posix()
            try:
                audio[rel] = _b64_of(f)
            except Exception:
                pass

    out_dir = root / CONVERTED_DIR
    out_py  = out_dir / CONVERTED_FILE

    # Compose the module as a compact, deterministic string (stable sort for diff-friendliness)
    lines = []
    lines.append("# Auto-generated — DO NOT EDIT BY HAND")
    lines.append("# Provides: IMAGES (dict[str,str]), AUDIO (dict[str,str])")
    lines.append("")
    lines.append("IMAGES = {")
    for k in sorted(images.keys()):
        lines.append(f"    {k!r}: {images[k]!r},")
    lines.append("}")
    lines.append("")
    lines.append("AUDIO = {")
    for k in sorted(audio.keys()):
        lines.append(f"    {k!r}: {audio[k]!r},")
    lines.append("}")
    content = "\n".join(lines) + "\n"

    # Only rewrite if contents differ (avoid needless churn in git)
    if out_py.exists():
        try:
            current = out_py.read_text(encoding="utf-8")
        except Exception:
            current = ""
        if _sha1(current) == _sha1(content):
            logging.info(f"✅ Base64 bundle up-to-date at {CONVERTED_DIR}\\{CONVERTED_FILE} "
                         f"({len(images)} images, {len(audio)} audio).")
            return

    _atomic_write_text(out_py, content, encoding="utf-8")
    logging.info(f"✅ Wrote module with {len(images)} images & {len(audio)} audio URIs to "
                 f"{CONVERTED_DIR}\\{CONVERTED_FILE}")
    logging.info("[Base64] Regenerated base64 bundle.")


# ─────────────────────────────────────────────────────────────────────────────
# Build / Backup convenience (non-intrusive)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_built_exe_and_backup(root: Path) -> Optional[Path]:
    """
    If a distributable EXE exists in MAX/, log its presence and write a
    timestamped backup into Backups/. Non-fatal, non-blocking. Source runs only.
    """
    if is_frozen():
        return None

    dist_dir = root / DIST_DIRNAME
    dist_dir.mkdir(parents=True, exist_ok=True)

    preferred = dist_dir / f"{APP_NAME}.exe"
    exe_path: Optional[Path] = preferred if preferred.exists() else None

    if exe_path is None:
        cand = sorted(dist_dir.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        exe_path = cand[0] if cand else None

    if exe_path and exe_path.exists():
        logging.info(f"[Build] EXE present: {exe_path}")
        backups = root / BACKUPS_DIRNAME
        backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_exe = backups / f"{exe_path.stem}-{stamp}.exe"
        try:
            shutil.copy2(exe_path, backup_exe)
            logging.info(f"[Backup] Saved backup: {backup_exe}")
        except Exception as e:
            logging.info(f"[Backup] Skipped (copy failed): {e}")
        return exe_path

    # Not an error; just no exe yet.
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Qt bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_qt(app_icon_path: Optional[Path]) -> QtWidgets.QApplication:
    """
    Create and configure the QApplication once. No deprecated Qt6 attributes
    are used (Qt6 enables HiDPI automatically). We still set app name/domain.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(ORG_DOMAIN)

    if app_icon_path and app_icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(app_icon_path)))
    return app


def _startup_banner(root: Path, icon: Optional[Path]) -> None:
    """Emit a compact, informative startup banner to the log."""
    qt_ver   = QtCore.qVersion()
    py_ver   = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    mode     = "frozen" if is_frozen() else "source"
    icon_str = str(icon) if icon else "None"
    logging.info(f"— {APP_NAME} —")
    logging.info(f"[Env] Python {py_ver} | Qt {qt_ver} | Mode: {mode}")
    logging.info(f"[Root] {root}")
    logging.info(f"[Icon] {icon_str}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Logging: clean, concise, visible in terminal
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    root = project_root_from_argv()
    chdir_root(root)

    # Load settings (non-fatal if missing)
    settings = load_settings(root)

    # Icon selection (log happens inside pick_icon)
    app_icon = pick_icon(root)

    # Show a quick environment banner up front
    _startup_banner(root, app_icon)

    # Base64 bundle (source runs only; non-fatal)
    try:
        regenerate_base64_bundle(root, settings)
    except Exception as e:
        logging.info(f"[Base64] Skipped (error): {e}")

    # Presence + backup of a built EXE (non-blocking; source runs only)
    try:
        ensure_built_exe_and_backup(root)
    except Exception as e:
        logging.info(f"[Build] Skipped (error): {e}")

    # Create QApplication and launch Nexus
    app = bootstrap_qt(app_icon)

    # Import here to avoid early import errors if optional deps (e.g., WebEngine) are missing
    try:
        from Nexus import Nexus  # noqa: E402
    except Exception as ex:
        # If Nexus import fails, show a friendly dialog and exit gracefully.
        try:
            QtWidgets.QMessageBox.critical(
                None,
                f"{APP_NAME} — Startup Error",
                f"Failed to import main window (Nexus):\n\n{ex}"
            )
        finally:
            raise

    win = Nexus(root)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()