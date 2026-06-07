# command.py
"""
Reset controller for Letter Smith.

The Command tab performs the app's destructive current-letter reset. It clears
current user page images, the active message folder, selected music, live tab
state, Prompt Writer state, and active editor state while preserving saved
letters and app-owned sound effects. After every reset, the canonical blank
message.html is restored from gallery/app/pages/Emessage.docx through
message_html.ensure_message_html_from_emessage().

The public entry points are CommandTab, confirm_and_reset(), and
reset_everything().
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

__all__ = [
    "CommandTab",
    "confirm_and_reset",
    "reset_everything",
]

from config import (
    SETTINGS_FILE,
    PUBLISHED_PAGE_URL_KEY,
    CURTAIN_STYLE_KEY,
    CURTAIN_STYLE_WHITE,
    USER_PAGES_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
    MESSAGE_HTML_FILE,
    MUSIC_FILE,
)
from message_html import ensure_message_html_from_emessage

def app_root() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cwd = Path.cwd()
        if (cwd / "gallery").exists() or (cwd / SETTINGS_FILE).exists():
            return cwd
        return Path(base)

    here = Path(__file__).resolve()
    for up in (here.parent, here.parent.parent, here.parent.parent.parent):
        if (up / SETTINGS_FILE).exists() or (up / "gallery").exists():
            return up
    return here.parent

def _safe_clear_dir_contents(dir_path: Path) -> Tuple[int, int]:
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
                shutil.rmtree(entry, ignore_errors=True)
                dirs_deleted += 1
        except Exception:
            pass

    return files_deleted, dirs_deleted

def _safe_delete_file(path: Path) -> int:
    try:
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)
            return 1
    except Exception:
        pass
    return 0

def _read_settings(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _write_settings(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def _get_nexus_window(parent: Optional[QtWidgets.QWidget]) -> Optional[QtWidgets.QWidget]:
    if parent is None:
        return None
    try:
        return parent.window()
    except Exception:
        return None

def _hard_stop_sound_system(win: Optional[QtWidgets.QWidget]) -> None:
    """
    Stop every live audio handle that can lock the selected music file.
    """
    if win is None:
        return

    try:
        st = getattr(win, "sound_tab", None)
        if st is not None and hasattr(st, "wave"):
            wave = getattr(st, "wave", None)
            if wave is not None and hasattr(wave, "release_current_file_handle"):
                wave.release_current_file_handle()
    except Exception:
        pass

    try:
        st = getattr(win, "sound_tab", None)
        if st is not None and hasattr(st, "_preview"):
            pv = getattr(st, "_preview", None)
            p = getattr(pv, "_player", None)
            if p is not None:
                try:
                    p.stop()
                except Exception:
                    pass
                try:
                    p.setSource(QUrl())
                except Exception:
                    pass
    except Exception:
        pass

def _force_soundtab_no_audio(win: Optional[QtWidgets.QWidget]) -> None:
    """
    Put SoundTab into the same state as an app session with no selected music.
    """
    if win is None:
        return

    try:
        st = getattr(win, "sound_tab", None)
        if st is None:
            return

        if hasattr(st, "_on_current_changed"):
            try:
                st._on_current_changed("")  # type: ignore[attr-defined]
            except Exception:
                pass

        try:
            if hasattr(st, "playpause_btn"):
                st.playpause_btn.setText("▶️ Play")
        except Exception:
            pass

        try:
            if hasattr(st, "status"):
                st.status.setText("No audio loaded.")
        except Exception:
            pass
    except Exception:
        pass

def _clear_message_tab_inputs(win: Optional[QtWidgets.QWidget]) -> None:
    """
    Clear the Message tab fields that mirror reset-sensitive settings.
    """
    if win is None:
        return

    try:
        mt = getattr(win, "message_tab", None)
        if mt is None:
            return

        try:
            if hasattr(mt, "title_input"):
                mt.title_input.setText("")  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "name_input"):
                mt.name_input.setText("")  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "set_published_page_url"):
                mt.set_published_page_url("", persist=False, announce=False)  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "settings") and isinstance(mt.settings, dict):  # type: ignore[attr-defined]
                mt.settings["recipient_title"] = ""
                mt.settings["recipient_name"] = ""
                mt.settings[PUBLISHED_PAGE_URL_KEY] = ""
        except Exception:
            pass

        try:
            if hasattr(mt, "_save_settings"):
                mt._save_settings()  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        pass

PROMPT_WRITER_CHECK_KEYS = (
    "black",
    "white",
    "frame",
    "vignette",
    "polaroid",
    "cardshadow",
    "real",
    "paint",
    "minimal",
    "forbid_text",
    "clean_composition",
    "strong_focal_point",
    "dynamic_angle",
    "cinematic_framing",
    "close_up_focus",
    "full_body_view",
    "wide_scene",
    "simplified_details",
)

def _blank_prompt_writer_state() -> dict:
    return {
        "version": 2,
        "type": "",
        "subject": "",
        "color": "",
        "global": "",
        "cover": "",
        "letter": "",
        "wall": "",
        "back": "",
        "checks": {key: False for key in PROMPT_WRITER_CHECK_KEYS},
        "generated_prompts": {},
        "reference_images": [],
    }

def _reset_prompt_writer_state_on_disk(root: Path) -> None:
    """
    Persist an explicitly blank Prompt Writer state for the next panel load.
    """
    try:
        state_path = (root / "prompt_writer_state.json").resolve()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = state_path.with_name(f".{state_path.name}.tmp")
        temp_path.write_text(
            json.dumps(_blank_prompt_writer_state(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(state_path)
    except Exception:
        pass

def _is_prompt_writer_panel(widget: object) -> bool:
    try:
        if getattr(widget, "objectName", lambda: "")() == "PromptWriterPanel":
            return True
    except Exception:
        pass

    try:
        if widget.__class__.__name__ == "PromptWriterPanel":
            return True
    except Exception:
        pass

    return bool(
        hasattr(widget, "_state_path")
        and hasattr(widget, "_checkbox_state_specs")
        and hasattr(widget, "preview_widgets")
    )

def _iter_prompt_writer_panels(win: Optional[QtWidgets.QWidget]):
    seen = set()

    def add_candidate(obj: object):
        if obj is None or not _is_prompt_writer_panel(obj):
            return

        key = id(obj)
        if key in seen:
            return

        seen.add(key)
        yield obj

    if win is not None:
        for attr in (
            "prompt_writer_panel",
            "prompt_writer",
            "promptWriterPanel",
            "prompt_writer_window",
            "prompter_panel",
            "prompter",
        ):
            try:
                yield from add_candidate(getattr(win, attr, None))
            except Exception:
                pass

        try:
            for child in win.findChildren(QtWidgets.QWidget):
                yield from add_candidate(child)
        except Exception:
            pass

    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            for top in app.topLevelWidgets():
                yield from add_candidate(top)

                try:
                    for child in top.findChildren(QtWidgets.QWidget):
                        yield from add_candidate(child)
                except Exception:
                    pass
    except Exception:
        pass

def _clear_prompt_writer_panel_by_widgets(panel: object) -> None:
    """Clear a live Prompt Writer panel that exposes individual widgets instead of one reset method."""
    try:
        timer = getattr(panel, "_persist_timer", None)
        if timer is not None:
            timer.stop()
    except Exception:
        pass

    try:
        for name in ("cmb_type", "cmb_color"):
            combo = getattr(panel, name, None)
            if combo is not None:
                combo.setCurrentIndex(-1)
    except Exception:
        pass

    try:
        combo = getattr(panel, "cmb_subject", None)
        if combo is not None:
            combo.setCurrentText("")
            try:
                if combo.lineEdit() is not None:
                    combo.lineEdit().clear()
            except Exception:
                pass
    except Exception:
        pass

    try:
        specs = panel._checkbox_state_specs()  # type: ignore[attr-defined]
    except Exception:
        specs = []

    for checkbox, _key in specs:
        try:
            checkbox.setChecked(False)
        except Exception:
            pass

    for name in ("txt_global", "txt_cover", "txt_letter", "txt_wall", "txt_back"):
        try:
            editor = getattr(panel, name, None)
            if editor is not None:
                editor.clear()
        except Exception:
            pass

    try:
        panel._generated_prompts = {}  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        panel._reference_images = []  # type: ignore[attr-defined]
        refresher = getattr(panel, "_refresh_reference_image_panel", None)
        if callable(refresher):
            refresher()
    except Exception:
        pass

    try:
        panel._last_focused_widget = None  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        previews = getattr(panel, "preview_widgets", {})
        if isinstance(previews, dict):
            for w in previews.values():
                try:
                    w.clear()
                except Exception:
                    pass
    except Exception:
        pass

    try:
        saver = getattr(panel, "_persist_state_now", None)
        if callable(saver):
            saver()
    except Exception:
        pass

def _clear_prompt_writer(win: Optional[QtWidgets.QWidget], root: Path) -> None:
    """Clear Prompt Writer state on disk and in any live Prompt Writer panel."""
    _reset_prompt_writer_state_on_disk(root)

    for panel in _iter_prompt_writer_panels(win):
        try:
            resetter = getattr(panel, "reset_to_blank", None)
            if callable(resetter):
                resetter(persist=True)
            else:
                _clear_prompt_writer_panel_by_widgets(panel)
        except Exception:
            pass

EDITOR_DOCK_FILE_NAMES = (
    "editor_dock.json",
    "editor_dock_state.json",
    "editor_state.json",
    "dock_state.json",
    "letter_editor_dock.json",
    "letter_editor_state.json",
    "message_editor_dock.json",
    "message_editor_state.json",
    "current_editor_dock.json",
    "current_editor_state.json",
)

EDITOR_DOCK_SEARCH_DIRS = (
    "",
    "gallery/user",
    "gallery/user/message",
    "gallery/user/pages",
    "gallery/user/editor",
    "gallery/user/dock",
    "gallery/user/state",
)

def _delete_editor_dock_files(root: Path) -> int:
    """Delete current editor dock/state files without touching saved letters."""
    deleted = 0
    seen = set()

    def delete_candidate(path: Path) -> None:
        nonlocal deleted

        try:
            resolved = path.resolve()
        except Exception:
            resolved = path

        key = str(resolved)
        if key in seen:
            return

        seen.add(key)
        deleted += _safe_delete_file(resolved)

    for rel_dir in EDITOR_DOCK_SEARCH_DIRS:
        base = (root / rel_dir).resolve() if rel_dir else root.resolve()
        for name in EDITOR_DOCK_FILE_NAMES:
            delete_candidate(base / name)

    # Remove only files whose names identify them as current editor dock/state data.
    for rel_dir in (
        "gallery/user",
        "gallery/user/message",
        "gallery/user/editor",
        "gallery/user/dock",
        "gallery/user/state",
    ):
        base = (root / rel_dir).resolve()
        if not base.exists() or not base.is_dir():
            continue

        try:
            for path in base.glob("*.json"):
                name = path.name.casefold()
                if "editor" in name and ("dock" in name or "state" in name or "current" in name):
                    delete_candidate(path)
        except Exception:
            pass

    return deleted

def _is_editor_widget(widget: object) -> bool:
    try:
        object_name = getattr(widget, "objectName", lambda: "")() or ""
    except Exception:
        object_name = ""

    try:
        class_name = widget.__class__.__name__
    except Exception:
        class_name = ""

    text = f"{object_name} {class_name}".casefold()

    if "promptwriter" in text or "prompt_writer" in text or "sound" in text:
        return False

    if "editor" in text:
        return True

    return bool(
        hasattr(widget, "dock_file")
        or hasattr(widget, "dock_path")
        or hasattr(widget, "dock_state_path")
        or hasattr(widget, "_dock_file")
        or hasattr(widget, "_dock_path")
        or hasattr(widget, "_dock_state_path")
        or hasattr(widget, "editor_dock_path")
        or hasattr(widget, "_editor_dock_path")
    )

def _iter_editor_widgets(win: Optional[QtWidgets.QWidget]):
    seen = set()

    def add_candidate(obj: object):
        if obj is None or not _is_editor_widget(obj):
            return

        key = id(obj)
        if key in seen:
            return

        seen.add(key)
        yield obj

    if win is not None:
        for attr in (
            "editor",
            "editor_tab",
            "letter_editor",
            "letter_editor_tab",
            "message_editor",
            "message_editor_tab",
            "dock_editor",
            "editor_panel",
            "editor_window",
            "text_editor",
            "writer_editor",
        ):
            try:
                yield from add_candidate(getattr(win, attr, None))
            except Exception:
                pass

        try:
            for child in win.findChildren(QtWidgets.QWidget):
                yield from add_candidate(child)
        except Exception:
            pass

    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            for top in app.topLevelWidgets():
                yield from add_candidate(top)

                try:
                    for child in top.findChildren(QtWidgets.QWidget):
                        yield from add_candidate(child)
                except Exception:
                    pass
    except Exception:
        pass

def _call_first_available(obj: object, names: Tuple[str, ...]) -> bool:
    for name in names:
        try:
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except TypeError:
                    try:
                        fn(True)
                        return True
                    except Exception:
                        pass
        except Exception:
            pass

    return False

def _clear_editor_widget(editor: object) -> None:
    """Reset a live editor widget without touching saved letters."""
    try:
        for attr in (
            "_persist_timer",
            "_autosave_timer",
            "autosave_timer",
            "save_timer",
            "_save_timer",
        ):
            timer = getattr(editor, attr, None)
            if timer is not None and hasattr(timer, "stop"):
                timer.stop()
    except Exception:
        pass

    if _call_first_available(
        editor,
        (
            "reset_to_blank",
            "reset_to_empty",
            "clear_all",
            "clear_editor",
            "clear_document",
            "new_blank_document",
            "new_document",
            "reset_editor",
        ),
    ):
        return

    # Widget-level reset path for editor panels that do not expose a public reset method.
    try:
        if isinstance(editor, QtWidgets.QWidget):
            for widget in editor.findChildren(QtWidgets.QTextEdit):
                try:
                    widget.clear()
                except Exception:
                    pass

            for widget in editor.findChildren(QtWidgets.QPlainTextEdit):
                try:
                    widget.clear()
                except Exception:
                    pass

            for widget in editor.findChildren(QtWidgets.QLineEdit):
                try:
                    widget.clear()
                except Exception:
                    pass

            for widget in editor.findChildren(QtWidgets.QCheckBox):
                try:
                    widget.setChecked(False)
                except Exception:
                    pass

            for widget in editor.findChildren(QtWidgets.QComboBox):
                try:
                    widget.setCurrentIndex(-1)
                except Exception:
                    pass
    except Exception:
        pass

    for attr in (
        "current_file",
        "current_path",
        "current_document",
        "current_doc",
        "dock_file",
        "dock_path",
        "dock_state_path",
        "editor_dock_path",
        "_current_file",
        "_current_path",
        "_current_document",
        "_current_doc",
        "_dock_file",
        "_dock_path",
        "_dock_state_path",
        "_editor_dock_path",
    ):
        try:
            if hasattr(editor, attr):
                setattr(editor, attr, None)
        except Exception:
            pass

def _clear_editor(win: Optional[QtWidgets.QWidget], root: Path) -> int:
    """Clear live editor UI and remove current editor dock/state files."""
    for editor in _iter_editor_widgets(win):
        try:
            _clear_editor_widget(editor)
        except Exception:
            pass

    # Delete after clearing too, in case a clear signal caused a blank dock save.
    return _delete_editor_dock_files(root)

def _reset_settings_on_disk(root: Path) -> None:
    settings_path = (root / SETTINGS_FILE).resolve()
    data = _read_settings(settings_path)

    data["recipient_name"] = ""
    data["recipient_title"] = ""
    data[PUBLISHED_PAGE_URL_KEY] = ""

    data["starting_volume"] = 50
    data["music_volume"] = 50

    data["music_file"] = ""
    data["last_audio"] = "none"
    data[CURTAIN_STYLE_KEY] = CURTAIN_STYLE_WHITE

    _write_settings(settings_path, data)

def _delete_music_and_manifest(root: Path) -> int:
    """
    Delete the active user-selected music file and its current-selection manifest.
    """
    total = 0

    user_snd_dir = (root / USER_SOUNDS_DIR).resolve()
    user_music = (user_snd_dir / MUSIC_FILE).resolve()
    current_manifest = (user_snd_dir / "appssong" / "current.json").resolve()

    total += _safe_delete_file(user_music)
    total += _safe_delete_file(current_manifest)

    return total

def _write_minimal_blank_message_html(root: Path) -> Path:
    target = (root / MESSAGE_HTML_FILE).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<p></p>", encoding="utf-8")
    return target

def _restore_default_message_html(root: Path) -> Path:
    """
    Recreate gallery/user/message/message.html from the app-owned Emessage.docx template.
    """
    try:
        return ensure_message_html_from_emessage(root, overwrite=True)
    except Exception:
        return _write_minimal_blank_message_html(root)

def _refresh_message_tab_after_restore(win: Optional[QtWidgets.QWidget], root: Path) -> None:
    if win is None:
        return

    try:
        mt = getattr(win, "message_tab", None)
        if mt is None:
            return

        html_path = (root / MESSAGE_HTML_FILE).resolve()
        html = html_path.read_text(encoding="utf-8") if html_path.is_file() else "<p></p>"

        try:
            mt.current_html = html
        except Exception:
            pass

        try:
            if hasattr(mt, "edit_btn"):
                mt.edit_btn.setEnabled(True)
        except Exception:
            pass

        try:
            if hasattr(mt, "_ensure_wall_exists"):
                mt._ensure_wall_exists()  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "text_selected"):
                mt.text_selected.emit(html)  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "_generate_image"):
                mt._generate_image(html)  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            if hasattr(mt, "_emit_best_preview"):
                mt._emit_best_preview()  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        pass

def reset_everything(*, parent: Optional[QtWidgets.QWidget] = None) -> Tuple[int, int]:
    """Reset the active letter workspace and return deleted file/directory counts."""
    root = app_root()

    pages_dir = (root / USER_PAGES_DIR).resolve()
    msg_dir = (root / USER_MESSAGE_DIR).resolve()

    pages_dir.mkdir(parents=True, exist_ok=True)
    msg_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_dirs = 0

    win = _get_nexus_window(parent)

    _hard_stop_sound_system(win)

    f, d = _safe_clear_dir_contents(pages_dir)
    total_files += f
    total_dirs += d

    f, d = _safe_clear_dir_contents(msg_dir)
    total_files += f
    total_dirs += d

    _restore_default_message_html(root)

    total_files += _delete_music_and_manifest(root)

    _reset_settings_on_disk(root)

    _clear_message_tab_inputs(win)

    _refresh_message_tab_after_restore(win, root)

    _clear_prompt_writer(win, root)

    total_files += _clear_editor(win, root)

    _force_soundtab_no_audio(win)

    return total_files, total_dirs

class _ConfirmDialog(QtWidgets.QDialog):
    """Frameless confirmation dialog for the destructive reset action."""
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("panel")
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
            QPushButton:hover { border-color: rgba(255, 77, 79, 0.85); }
            QPushButton#danger { border-color: rgba(255, 77, 79, 0.55); }
            QPushButton#danger:hover { border-color: rgba(255, 77, 79, 1.0); }
            """
        )

        inner = QtWidgets.QVBoxLayout(panel)
        inner.setContentsMargins(18, 16, 18, 14)
        inner.setSpacing(12)

        label = QtWidgets.QLabel("Are you sure? This will erase everything.")
        label.setWordWrap(True)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)

        btn_no = QtWidgets.QPushButton("No")
        btn_yes = QtWidgets.QPushButton("Yes")
        btn_yes.setObjectName("danger")

        row.addWidget(btn_no)
        row.addWidget(btn_yes)

        inner.addWidget(label)
        inner.addLayout(row)

        outer.addWidget(panel)

        btn_no.clicked.connect(self.reject)
        btn_yes.clicked.connect(self.accept)

        self.resize(420, 140)

def _toast(parent: Optional[QtWidgets.QWidget], text: str, msecs: int = 1400) -> None:
    tip = QtWidgets.QDialog(parent)
    tip.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip)
    tip.setAttribute(Qt.WA_TranslucentBackground, True)

    outer = QtWidgets.QVBoxLayout(tip)
    outer.setContentsMargins(0, 0, 0, 0)

    body = QtWidgets.QFrame()
    body.setStyleSheet(
        """
        QFrame {
            background: rgba(15, 17, 22, 246);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
        }
        QLabel {
            color:#e6e6e6;
            padding: 10px 12px;
            font-weight: 700;
        }
        """
    )

    lbl = QtWidgets.QLabel(text)

    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(lbl)

    outer.addWidget(body)

    tip.adjustSize()

    if parent is not None:
        pos = parent.mapToGlobal(parent.rect().bottomRight())
        tip.move(pos.x() - tip.width() - 22, pos.y() - tip.height() - 22)
    else:
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        tip.move(scr.right() - tip.width() - 22, scr.bottom() - tip.height() - 22)

    QtCore.QTimer.singleShot(msecs, tip.close)
    tip.show()

def confirm_and_reset(parent: Optional[QtWidgets.QWidget] = None) -> None:
    """Ask for confirmation, run the reset, and notify the active Command tab."""
    dlg = _ConfirmDialog(parent)

    if parent is not None:
        cp = parent.mapToGlobal(parent.rect().center())
        dlg.move(cp.x() - dlg.width() // 2, cp.y() - dlg.height() // 2)

    if dlg.exec() == QtWidgets.QDialog.Accepted:
        files, _dirs = reset_everything(parent=parent)
        _toast(parent, f"Wiped. ({files} files)")

        try:
            if parent is not None and hasattr(parent, "wiped"):
                parent.wiped.emit()  # type: ignore[attr-defined]
        except Exception:
            pass

class _PressGoLabel(QtWidgets.QLabel):
    """Image-backed reset button with press-scale feedback."""
    clicked = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._base_rect = QtCore.QRect(0, 0, 0, 0)
        self._pix_base: Optional[QtGui.QPixmap] = None

        self._scale_anim = QtCore.QVariantAnimation(self)
        self._scale_anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
        self._scale_anim.valueChanged.connect(self._apply_scale)

        self._scale = 1.0
        self._pressed = False

    def set_base(self, base_rect: QtCore.QRect, pix: QtGui.QPixmap) -> None:
        self._base_rect = QtCore.QRect(base_rect)
        self._pix_base = pix
        self._set_scaled_geometry_and_pixmap(1.0)

    def _set_scaled_geometry_and_pixmap(self, scale: float) -> None:
        if self._pix_base is None or self._pix_base.isNull():
            self.setGeometry(self._base_rect)
            self.clear()
            return

        scale = float(scale)

        bw = self._base_rect.width()
        bh = self._base_rect.height()

        nw = max(1, int(round(bw * scale)))
        nh = max(1, int(round(bh * scale)))

        cx = self._base_rect.x() + bw / 2
        cy = self._base_rect.y() + bh / 2

        pm = self._pix_base.scaled(nw, nh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        pw = pm.width()
        ph = pm.height()

        x = int(round(cx - pw / 2))
        y = int(round(cy - ph / 2))

        self.setPixmap(pm)
        self.setGeometry(x, y, pw, ph)

    def _apply_scale(self, v: object) -> None:
        try:
            self._scale = float(v)
        except Exception:
            self._scale = 1.0

        self._set_scaled_geometry_and_pixmap(self._scale)

    def _animate_to(self, target: float, ms: int) -> None:
        self._scale_anim.stop()
        self._scale_anim.setDuration(int(ms))
        self._scale_anim.setStartValue(float(self._scale))
        self._scale_anim.setEndValue(float(target))
        self._scale_anim.start()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._animate_to(0.92, 85)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pressed and event.button() == Qt.LeftButton:
            self._pressed = False
            self._animate_to(1.0, 110)

            if self.rect().contains(event.position().toPoint()):
                QtCore.QTimer.singleShot(0, self.clicked.emit)

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._pressed:
            self._pressed = False
            self._animate_to(1.0, 110)

        super().leaveEvent(event)

class CommandTab(QtWidgets.QWidget):
    """Command page containing the image-backed reset control."""
    wiped = QtCore.Signal()

    def __init__(self, project_root: Path, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

        self.project_root = Path(project_root).resolve()

        self.setObjectName("CommandTab")
        self.setStyleSheet("QWidget#CommandTab { background:#0b0c10; }")

        icons_dir = self.project_root / "gallery" / "app" / "icons"
        self._bg_path = (icons_dir / "command.png").resolve()
        self._go_path = (icons_dir / "GO.png").resolve()

        self._bg_pix = QtGui.QPixmap(str(self._bg_path))
        self._go_pix = QtGui.QPixmap(str(self._go_path))

        self.bg_label = QtWidgets.QLabel(self)
        self.bg_label.setAlignment(Qt.AlignCenter)
        self.bg_label.setScaledContents(False)
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.go_btn = _PressGoLabel(self)
        self.go_btn.setToolTip("Wipe the letter")
        self.go_btn.clicked.connect(lambda: self._do_reset())

        self._relayout()

    def _do_reset(self) -> None:
        confirm_and_reset(self)

        try:
            self.wiped.emit()
        except Exception:
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())

        self.bg_label.setGeometry(0, 0, w, h)

        if not self._bg_pix.isNull():
            bg_scaled = self._bg_pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.bg_label.setPixmap(bg_scaled)

        if self._go_pix.isNull():
            self.go_btn.set_base(QtCore.QRect(0, 0, 0, 0), self._go_pix)
            return

        target = int(min(w, h) * 0.28)
        target = max(140, min(460, target))

        go_base = self._go_pix.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        bw = go_base.width()
        bh = go_base.height()

        base_rect = QtCore.QRect((w - bw) // 2, (h - bh) // 2, bw, bh)

        self.go_btn.set_base(base_rect, go_base)
        self.go_btn.raise_()

def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    confirm_and_reset(None)
    QtCore.QTimer.singleShot(0, app.quit)
    app.exec()

if __name__ == "__main__":
    main()
