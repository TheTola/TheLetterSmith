#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompter bootstrap — robust loader with rich diagnostics

- Import chain: Cyber_Interface → Interface → prompter_nexus → prompter_panel
- Auto-detect any QMainWindow subclass if expected names not found
- Single-instance: QLocalServer (QtNetwork) preferred; QLockFile fallback
- Dark, clear dialogs; full import report shows "classes present"
- Sanity check requires:
    Buttons: gen_btn, export_btn
    Editors: cover_edit (subject), letter_edit (type), back_edit (color)
    Outputs: out_cover, out_letter, out_back, out_wall (each has `.text.toPlainText()`)
"""

from __future__ import annotations

import os
import sys
import json
import hashlib
import traceback
from pathlib import Path
from typing import Optional, Tuple, List, Union

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QLockFile, QStandardPaths

# Prefer QtNetwork; tolerate absence
try:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    _HAVE_QTNETWORK = True
except Exception:
    QLocalServer = None   # type: ignore
    QLocalSocket = None   # type: ignore
    _HAVE_QTNETWORK = False

import importlib
import importlib.util
from importlib.machinery import SourceFileLoader

APP_ORG          = "InfiniWorks"
APP_DOMAIN       = "infinite.studio"
DEFAULT_APP_NAME = "Prompter"

REQ_BUTTONS = ("gen_btn", "export_btn")
REQ_EDITORS = ("cover_edit", "letter_edit", "back_edit")
REQ_OUTS    = ("out_cover", "out_letter", "out_back", "out_wall")

CONFIG_FILE = "prompter_config.json"


# ─────────────────────────────────────────────────────────────────────────────
# Paths & sys.path hygiene
# ─────────────────────────────────────────────────────────────────────────────
def detect_prompter_root() -> Path:
    return Path(__file__).resolve().parent

def ensure_sys_path(root: Path) -> None:
    for p in (root, root / "modules"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    try:
        os.chdir(str(root))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Dialog helpers
# ─────────────────────────────────────────────────────────────────────────────
def show_fatal_dialog(title: str, message: str, detail: str = "") -> None:
    dlg = QtWidgets.QMessageBox()
    dlg.setIcon(QtWidgets.QMessageBox.Critical)
    dlg.setWindowTitle(title or "Fatal Error")
    dlg.setText(f"<b>{title or 'Fatal Error'}</b><br><br>{message}")
    if detail:
        dlg.setDetailedText(detail)
    dlg.setStandardButtons(QtWidgets.QMessageBox.Ok)
    dlg.setStyleSheet("""
        QMessageBox { background: #1a1a1a; color: #e6e6e6; }
        QMessageBox QLabel { color: #e6e6e6; }
        QPushButton { background: #222; color: #e6e6e6; border: 1px solid #444; border-radius: 6px; padding: 6px 12px; }
        QPushButton:hover { border-color: #00d0ff; color: #e8feff; }
    """)
    dlg.exec()


# ─────────────────────────────────────────────────────────────────────────────
# Import helpers with auto-detect & deep diagnostics
# ─────────────────────────────────────────────────────────────────────────────
def _attempt_import(module_name: str, class_candidates: List[str], title_attr: str = "APP_TITLE") -> Tuple[Optional[type], Optional[str], str]:
    report = [f"— import {module_name} —"]
    try:
        mod = __import__(module_name, fromlist=class_candidates + [title_attr])
        # Try named candidates first
        ui_cls = None
        for cname in class_candidates:
            if hasattr(mod, cname):
                ui_cls = getattr(mod, cname)
                break
        # If not found, AUTO-DETECT any QMainWindow subclass
        if ui_cls is None:
            found_classes = []
            auto_pick = None
            try:
                from PySide6 import QtWidgets as _QtWidgets
                for k, v in vars(mod).items():
                    if isinstance(v, type):
                        found_classes.append(k)
                        try:
                            if issubclass(v, _QtWidgets.QMainWindow):
                                auto_pick = v
                        except Exception:
                            pass
            except Exception:
                pass
            if auto_pick is None:
                raise AttributeError(
                    f"{module_name} loaded, but none of {class_candidates} was found.\n"
                    f"Classes present: {', '.join(found_classes) if found_classes else '(none)'}"
                )
            ui_cls = auto_pick
            report.append(f"  ℹ auto-picked QMainWindow subclass: {ui_cls.__name__}")
        app_title = getattr(mod, title_attr, DEFAULT_APP_NAME)
        report.append("  ✓ success")
        return ui_cls, app_title, "\n".join(report)
    except Exception:
        report.append("  ✗ failed")
        report.append("  Reason:\n" + traceback.format_exc())
        return None, None, "\n".join(report)

def _attempt_file_load(root: Path, filename: str, module_alias: str, class_candidates: List[str], title_attr: str = "APP_TITLE") -> Tuple[Optional[type], Optional[str], str]:
    report = [f"— file load {filename} —"]
    path = root / filename
    if not path.exists():
        report.append(f"  ✗ not found: {path}")
        return None, None, "\n".join(report)
    try:
        loader = SourceFileLoader(module_alias, str(path))
        spec = importlib.util.spec_from_loader(module_alias, loader)
        if spec is None or spec.loader is None:
            raise ImportError("spec_from_loader returned None")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_alias] = mod
        spec.loader.exec_module(mod)  # type: ignore
        # Try named candidates
        ui_cls = None
        for cname in class_candidates:
            if hasattr(mod, cname):
                ui_cls = getattr(mod, cname)
                break
        # Auto-detect QMainWindow subclass if needed
        if ui_cls is None:
            from PySide6 import QtWidgets as _QtWidgets
            found_classes = []
            auto_pick = None
            for k, v in vars(mod).items():
                if isinstance(v, type):
                    found_classes.append(k)
                    try:
                        if issubclass(v, _QtWidgets.QMainWindow):
                            auto_pick = v
                    except Exception:
                        pass
            if auto_pick is None:
                raise AttributeError(
                    f"{filename} loaded, but none of {class_candidates} was found.\n"
                    f"Classes present: {', '.join(found_classes) if found_classes else '(none)'}"
                )
            ui_cls = auto_pick
            report.append(f"  ℹ auto-picked QMainWindow subclass: {ui_cls.__name__}")
        app_title = getattr(mod, title_attr, DEFAULT_APP_NAME)
        report.append("  ✓ success")
        return ui_cls, app_title, "\n".join(report)
    except Exception:
        report.append("  ✗ failed")
        report.append("  Reason:\n" + traceback.format_exc())
        return None, None, "\n".join(report)

def import_ui_and_title_with_report(root: Path) -> Tuple[type, str, str]:
    sections: List[str] = []
    # Try these modules in order — note: includes prompter_nexus and prompter_panel
    MODULES = [
        ("Cyber_Interface", ["PromptMakerUI"]),
        ("Interface",       ["PromptMakerUI"]),
        ("prompter_nexus",  ["PrompterNexus", "PromptMakerUI"]),
        ("prompter_panel",  ["PrompterNexus", "PromptMakerUI", "PromptWriterPanel"]),
    ]
    for mod_name, candidates in MODULES:
        ui_cls, title, rpt = _attempt_import(mod_name, candidates)
        sections.append(rpt)
        if ui_cls:
            return ui_cls, (title or DEFAULT_APP_NAME), "\n\n".join(sections)

    sections.append("— file-based fallback scan —")
    FILES = [
        ("Cyber_Interface.py", "_dyn_Cyber_Interface", ["PromptMakerUI"]),
        ("Interface.py",       "_dyn_Interface",       ["PromptMakerUI"]),
        ("prompter_nexus.py",  "_dyn_prompter_nexus",  ["PrompterNexus", "PromptMakerUI"]),
        ("prompter_panel.py",  "_dyn_prompter_panel",  ["PrompterNexus", "PromptMakerUI", "PromptWriterPanel"]),
    ]
    for fname, alias, candidates in FILES:
        ui_cls, title, rpt = _attempt_file_load(root, fname, alias, candidates)
        sections.append(rpt)
        if ui_cls:
            return ui_cls, (title or DEFAULT_APP_NAME), "\n\n".join(sections)

    raise ImportError("\n\n".join(sections))


# ─────────────────────────────────────────────────────────────────────────────
# Single-instance
# ─────────────────────────────────────────────────────────────────────────────
def make_instance_key(root: Path) -> str:
    h = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:10].upper()
    return f"LS_Prompter_{h}"

class SingleInstance(QtCore.QObject):
    def __init__(self, key: str, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: Optional[QLocalServer] = None
        self._lock: Optional[QLockFile] = None
        if _HAVE_QTNETWORK:
            self._server = QLocalServer(self)

    def already_running(self) -> bool:
        if _HAVE_QTNETWORK and self._server is not None:
            sock = QLocalSocket()
            sock.connectToServer(self._key, QtCore.QIODevice.ReadWrite)
            if sock.waitForConnected(80):
                sock.abort()
                return True
            try:
                QLocalServer.removeServer(self._key)
            except Exception:
                pass
            ok = self._server.listen(self._key)
            return not ok
        tmp = QStandardPaths.writableLocation(QStandardPaths.TempLocation) or os.getenv("TMP", os.getenv("TEMP", ""))
        if not tmp:
            tmp = "."
        lock_path = os.path.join(tmp, f"{self._key}.lock")
        self._lock = QLockFile(lock_path)
        self._lock.setStaleLockTime(0)
        return not self._lock.tryLock(1)


# ─────────────────────────────────────────────────────────────────────────────
# UI sanity & helpers
# ─────────────────────────────────────────────────────────────────────────────
def _has_attr(obj, name: str) -> bool:
    try:
        getattr(obj, name)
        return True
    except Exception:
        return False

def verify_required_widgets(ui_root: QtWidgets.QWidget) -> None:
    problems = []
    for key in REQ_BUTTONS:
        if not _has_attr(ui_root, key):
            problems.append(f"Missing button: {key}")
    for key in REQ_EDITORS:
        if not _has_attr(ui_root, key):
            problems.append(f"Missing editor: {key}")
    for key in REQ_OUTS:
        if not _has_attr(ui_root, key):
            problems.append(f"Missing output box: {key}")
            continue
        box = getattr(ui_root, key, None)
        text = getattr(box, "text", None)
        if text is None or not hasattr(text, "toPlainText"):
            problems.append(f"{key}.text is missing or not a text editor (needs toPlainText())")
    if problems:
        raise RuntimeError("UI Sanity Check Failed:\n\n" + "\n".join(f"• {p}" for p in problems))

def resolve_effective_panel(ui_obj: Union[QtWidgets.QWidget, QtWidgets.QMainWindow]) -> QtWidgets.QWidget:
    panel = getattr(ui_obj, "panel", None)
    if isinstance(panel, QtWidgets.QWidget):
        return panel
    return ui_obj


# ─────────────────────────────────────────────────────────────────────────────
# Settings (geometry/state persistence)
# ─────────────────────────────────────────────────────────────────────────────
def load_config(root: Path) -> dict:
    f = root / CONFIG_FILE
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}

def save_config(root: Path, data: dict) -> None:
    try:
        (root / CONFIG_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Optional wrapper window (if UI returns a panel widget)
# ─────────────────────────────────────────────────────────────────────────────
class PrompterWindow(QtWidgets.QMainWindow):
    def __init__(self, central: QtWidgets.QWidget, app_title: str, root: Path, cfg: dict, app_icon: Optional[str]):
        super().__init__()
        self._root = root
        self._cfg  = cfg
        self.setCentralWidget(central)
        self.setWindowTitle(app_title or DEFAULT_APP_NAME)

        if app_icon and Path(app_icon).exists():
            self.setWindowIcon(QtGui.QIcon(app_icon))

        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#1e1e1e"))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#e6e6e6"))
        self.setPalette(pal)
        self.resize(1100, 800)
        self._apply_geometry(cfg)

    def _apply_geometry(self, cfg: dict) -> None:
        try:
            if "geometry" in cfg:
                self.restoreGeometry(QtCore.QByteArray.fromHex(cfg["geometry"].encode("ascii")))
            if "windowState" in cfg:
                self.restoreState(QtCore.QByteArray.fromHex(cfg["windowState"].encode("ascii")))
        except Exception:
            pass

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        cfg = dict(self._cfg or {})
        try:
            cfg["geometry"] = bytes(self.saveGeometry().toHex()).decode("ascii")
            cfg["windowState"] = bytes(self.saveState().toHex()).decode("ascii")
            save_config(self._root, cfg)
        finally:
            super().closeEvent(e)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    # HiDPI
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    root = detect_prompter_root()
    ensure_sys_path(root)

    # App identity
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(DEFAULT_APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setOrganizationDomain(APP_DOMAIN)

    # Single-instance
    key = make_instance_key(root)
    guard = SingleInstance(key)
    if guard.already_running():
        QtWidgets.QMessageBox.information(
            None, "Already Running",
            "Another Prompter window for this root is already open."
        )
        return 0

    # Import UI with diagnostics
    try:
        UIClass, app_title, import_report = import_ui_and_title_with_report(root)
    except ImportError as ex:
        report = str(ex) or "(no report)"
        sys.stderr.write("\n=== Prompter UI Import Report ===\n" + report + "\n")
        sys.stderr.write("sys.path:\n  " + "\n  ".join(sys.path) + "\n")
        show_fatal_dialog(
            "Startup Error",
            "Failed to import a Prompt UI module. See Details for the full import report.",
            report + "\n\nsys.path:\n  " + "\n  ".join(sys.path)
        )
        return 1

    # Load config (optional icon)
    cfg = load_config(root)
    app_icon = cfg.get("app_icon")

    # Instantiate UI
    try:
        ui_obj = UIClass()
    except Exception:
        show_fatal_dialog("Startup Error", "Prompt UI failed to instantiate.", traceback.format_exc())
        return 1

    # If QMainWindow (e.g., PrompterNexus), verify its panel; otherwise verify the widget itself
    if isinstance(ui_obj, QtWidgets.QMainWindow):
        win = ui_obj
        if app_icon and Path(app_icon).exists():
            win.setWindowIcon(QtGui.QIcon(app_icon))
        panel = resolve_effective_panel(ui_obj)
        try:
            verify_required_widgets(panel)
        except Exception as ex:
            show_fatal_dialog("Wiring Error", str(ex), traceback.format_exc())
            return 1
        win.show()
        return app.exec_() if hasattr(app, "exec_") else app.exec()

    # QWidget panel path
    panel = ui_obj
    try:
        verify_required_widgets(panel)
    except Exception as ex:
        show_fatal_dialog("Wiring Error", str(ex), traceback.format_exc())
        return 1
    win = PrompterWindow(panel, app_title, root, cfg, app_icon)
    win.show()
    return app.exec_() if hasattr(app, "exec_") else app.exec()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        show_fatal_dialog("Fatal Startup Error", "An unrecoverable error occurred.", traceback.format_exc())
        raise
