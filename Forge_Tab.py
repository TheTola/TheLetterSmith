# ===============================
# File: Forge_Tab.py
# Purpose: Forge tab — deterministic Play build + Load (Recipient → Title)
#
# FINAL BUTTON BEHAVIOR:
# - Generate:
#     Builds Play bundle, opens browser (index.html). Does NOT open folders.
# - Seal the Letter:
#     Builds Play bundle, opens the Play folder (GitHub Pages target).
#
# LOAD (FINAL SPEC):
# - Clicking Load opens a drop-down menu (QMenu)
# - Menu lists ALL recipient folders under: output/Play/<recipient>/
# - Hovering a recipient expands a submenu listing ALL titles (from <title> in index.html)
# - Clicking a title loads that build back into canonical SOURCE:
#     gallery/user/pages/*
#     gallery/user/message/*
#     gallery/user/sounds/music.mp3   (ONLY music.mp3)
# - Does NOT touch flips/gliss/appssong/controls
# - Updates settings.json:
#     recipient_name, recipient_title
#
# NEW:
# - When a saved letter is loaded, ForgeTab emits a signal so Nexus can immediately
#   refresh the Forge preview (cover.png) + caption (project title).
# ===============================

from __future__ import annotations

import json
import re
import shutil
import traceback
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QTextEdit
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QUrl

import generate  # <-- IMPORTANT: module import only

from config import (
    SETTINGS_FILE,
    OUTPUT_PLAY_DIR,
    ensure_output_dirs,
    validate_required_images,
    MESSAGE_HTML_FILE,
    USER_PAGES_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
    MUSIC_FILE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_html_title(index_html: Path) -> str:
    txt = _read_text(index_html)
    m = re.search(r"<title>\s*(.*?)\s*</title>", txt, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return index_html.parent.name
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t or index_html.parent.name


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


def _load_settings(root: Path) -> dict:
    p = root / SETTINGS_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _write_settings(root: Path, data: dict) -> None:
    p = root / SETTINGS_FILE
    try:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _humanize_slug(s: str) -> str:
    s2 = s.replace("_", " ").replace("-", " ").strip()
    s2 = re.sub(r"\s+", " ", s2)
    return s2.title() if s2 else s


def _get_generate_fn():
    """
    Robust resolver for the Generate function.
    We do NOT hard-import a symbol because your file has shifted names before.
    """
    fn = getattr(generate, "generate_play_bundle", None)
    if callable(fn):
        return fn

    fn = getattr(generate, "generate_gallery", None)
    if callable(fn):
        return fn

    return None


def _open_folder(path: Path) -> None:
    QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


# ─────────────────────────────────────────────────────────────────────────────
# ForgeTab
# ─────────────────────────────────────────────────────────────────────────────

class ForgeTab(QtWidgets.QWidget):
    """
    - Generate:
        Builds Play bundle, opens browser
    - Seal the Letter:
        Builds Play bundle, opens Play folder (GitHub Pages target)
    - Load:
        Dropdown menu: Recipient -> Title -> Load build into gallery/user/*
    """

    # Nexus should connect to this to refresh preview/caption immediately after load.
    letter_loaded = QtCore.Signal(dict)  # payload includes recipient_name, recipient_title, play_dir

    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = Path(project_root)
        self._init_ui()

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("🛠️ Generate Animated Letter")
        title.setFont(QFont("Segoe UI Semibold", 18))
        title.setStyleSheet("color: #00d0ff;")
        title.setAlignment(Qt.AlignCenter)
        title.setGraphicsEffect(self._shadow_effect(12))
        layout.addWidget(title)

        # Top row: Load menu button only
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addStretch(1)

        self.load_btn = self._tiny_button("Load")
        self.load_btn.setToolTip("Load a saved letter from output/Play (Recipient → Title)")
        self.load_btn.clicked.connect(self._open_load_menu)
        top_row.addWidget(self.load_btn, 0, Qt.AlignRight)

        layout.addLayout(top_row)

        # Main action buttons
        btns = QHBoxLayout()
        btns.setSpacing(12)

        self.generate_btn = self._styled_button("Generate", "#ff8800", "#ffaa00", "yellow")
        self.generate_btn.setToolTip("Build the Play bundle and preview in your browser")
        self.generate_btn.clicked.connect(self.generate)
        btns.addWidget(self.generate_btn)

        self.seal_btn = self._styled_button("📜 Seal the Letter", "#6a5acd", "#836fff", "white")
        self.seal_btn.setToolTip("Build the Play bundle and open the Play folder (GitHub Pages target)")
        self.seal_btn.clicked.connect(self.seal_the_letter)
        btns.addWidget(self.seal_btn)

        layout.addLayout(btns)

        # Status console
        self.status = QTextEdit(readOnly=True)
        self.status.setFont(QFont("Segoe UI", 11))
        self.status.setStyleSheet(
            "background:#1a1a1a; border:1px solid #00d0ff;"
            " border-radius:4px; color:#ddd;"
        )
        layout.addWidget(self.status)

        # Quick open output folder
        self.open_output_btn = self._utility_button("📂 Open Output Folder")
        self.open_output_btn.clicked.connect(self.open_output_folder)
        layout.addWidget(self.open_output_btn)

        self._log("Ready.")

    # ─────────────────────────────────────────────────────────────────────
    # Load menu (Recipient → Title)
    # ─────────────────────────────────────────────────────────────────────
    def _open_load_menu(self) -> None:
        menu = self._build_load_menu()
        if menu is None:
            return

        gp = self.load_btn.mapToGlobal(QtCore.QPoint(0, self.load_btn.height()))
        menu.popup(gp)

    def _build_load_menu(self) -> Optional[QtWidgets.QMenu]:
        ensure_output_dirs(self.project_root)
        base = (self.project_root / OUTPUT_PLAY_DIR).resolve()
        base.mkdir(parents=True, exist_ok=True)

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #0f0f12;
                color: #e6e6e6;
                border: 1px solid #2b3344;
                padding: 6px;
            }
            QMenu::item {
                padding: 6px 18px 6px 18px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #113945;
            }
            QMenu::separator {
                height: 1px;
                background: #2b3344;
                margin: 6px 6px;
            }
        """)

        recipients = [p for p in base.iterdir() if p.is_dir()]
        recipients.sort(key=lambda p: p.name.lower())

        if not recipients:
            act = menu.addAction("No saved letters found")
            act.setEnabled(False)
            return menu

        for recip_dir in recipients:
            recip_slug = recip_dir.name
            recip_display = _humanize_slug(recip_slug)

            title_dirs = [p for p in recip_dir.iterdir() if p.is_dir()]
            valid_titles: list[tuple[str, Path]] = []
            for td in sorted(title_dirs, key=lambda p: p.name.lower()):
                idx = td / "index.html"
                gal = td / "gallery"
                if idx.is_file() and gal.is_dir():
                    valid_titles.append((_extract_html_title(idx), td))

            if not valid_titles:
                continue

            sub = QtWidgets.QMenu(recip_display, menu)
            sub.setStyleSheet(menu.styleSheet())
            menu.addMenu(sub)

            for display_title, play_dir in valid_titles:
                action = sub.addAction(display_title)
                action.setData({
                    "recipient_slug": recip_slug,
                    "recipient_display": recip_display,
                    "title_display": display_title,
                    "play_dir": str(play_dir),
                })
                action.triggered.connect(lambda _=False, a=action: self._load_from_action(a))

        if not menu.actions():
            act = menu.addAction("No valid letters found")
            act.setEnabled(False)

        return menu

    def _load_from_action(self, action: QtGui.QAction) -> None:
        data = action.data()
        if not isinstance(data, dict):
            return

        play_dir = Path(str(data.get("play_dir", ""))).resolve()
        recip_display = str(data.get("recipient_display", "")).strip()
        title_display = str(data.get("title_display", "")).strip()
        recip_slug = str(data.get("recipient_slug", "")).strip()

        if not play_dir.exists():
            self._log("❌ Selected folder is missing.")
            return

        # Source runtime folders
        src_gallery = play_dir / "gallery"
        src_pages = src_gallery / "pages"
        src_message = src_gallery / "message"
        src_sounds = src_gallery / "sounds"
        src_music = src_sounds / MUSIC_FILE

        if not src_pages.is_dir():
            self._log("❌ Invalid build: missing gallery/pages/")
            return
        if not src_message.is_dir():
            self._log("❌ Invalid build: missing gallery/message/")
            return
        if not src_music.is_file():
            self._log("❌ Invalid build: missing gallery/sounds/music.mp3")
            return

        # Dest canonical SOURCE folders
        dst_pages = (self.project_root / USER_PAGES_DIR).resolve()
        dst_message = (self.project_root / USER_MESSAGE_DIR).resolve()
        dst_sounds = (self.project_root / USER_SOUNDS_DIR).resolve()
        dst_music = (dst_sounds / MUSIC_FILE).resolve()

        dst_pages.mkdir(parents=True, exist_ok=True)
        dst_message.mkdir(parents=True, exist_ok=True)
        dst_sounds.mkdir(parents=True, exist_ok=True)

        # Clear ONLY pages/ and message/ contents
        pf, pd = _safe_clear_dir_contents(dst_pages)
        mf, md = _safe_clear_dir_contents(dst_message)

        # Copy pages
        copied_pages = 0
        for p in src_pages.iterdir():
            if p.is_file():
                shutil.copy2(p, dst_pages / p.name)
                copied_pages += 1

        # Copy message folder contents
        copied_msg = 0
        for p in src_message.iterdir():
            if p.is_file():
                shutil.copy2(p, dst_message / p.name)
                copied_msg += 1
            elif p.is_dir():
                shutil.copytree(p, dst_message / p.name, dirs_exist_ok=True)
                copied_msg += 1

        # Copy ONLY music.mp3
        shutil.copy2(src_music, dst_music)

        # Update settings.json (recipient/title)
        settings = _load_settings(self.project_root)
        settings["recipient_name"] = recip_display if recip_display else _humanize_slug(recip_slug)
        settings["recipient_title"] = title_display
        _write_settings(self.project_root, settings)

        self._log(
            "✅ Loaded saved letter\n"
            f"Recipient: {settings['recipient_name']}\n"
            f"Title: {settings['recipient_title']}\n"
            f"From: {play_dir}\n\n"
            f"Cleared: pages ({pf} files, {pd} dirs), message ({mf} files, {md} dirs)\n"
            f"Copied: pages ({copied_pages}), message ({copied_msg}), music.mp3 (1)\n\n"
            "Preview will update immediately."
        )

        payload = {
            "recipient_name": str(settings.get("recipient_name", "")).strip(),
            "recipient_title": str(settings.get("recipient_title", "")).strip(),
            "play_dir": str(play_dir),
        }
        QtCore.QTimer.singleShot(0, lambda: self.letter_loaded.emit(payload))

    # ─────────────────────────────────────────────────────────────────────
    # Utility actions
    # ─────────────────────────────────────────────────────────────────────
    def open_output_folder(self) -> None:
        ensure_output_dirs(self.project_root)
        out_parent = (self.project_root / OUTPUT_PLAY_DIR).parent
        _open_folder(out_parent)

    # ─────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────
    def generate(self) -> None:
        self._run_pipeline(mode="generate")

    def seal_the_letter(self) -> None:
        self._run_pipeline(mode="seal")

    def _run_pipeline(self, *, mode: str) -> None:
        ensure_output_dirs(self.project_root)

        gen_fn = _get_generate_fn()
        if gen_fn is None:
            self._log("❌ generate.py is missing generate_play_bundle/generate_gallery.")
            return

        missing = validate_required_images(self.project_root)
        if missing:
            self._log(
                f"❌ Cannot proceed: missing {', '.join(missing)}\n"
                f"Expected in: {self.project_root / 'gallery/user/pages'}"
            )
            return

        msg_path = (self.project_root / MESSAGE_HTML_FILE).resolve()
        message_html = self._read_message_html(msg_path)

        if not message_html.strip():
            self._log(
                "❌ Cannot proceed: message is empty or missing.\n"
                f"Expected: {msg_path}\n"
                "Open the editor and Save your message."
            )
            return

        try:
            if mode == "generate":
                play_dir = gen_fn(
                    str(self.project_root),
                    message_html=message_html,
                    open_in_browser=True,
                )
                self._log(
                    "✅ Play bundle updated and opened in browser.\n"
                    f"• Play: {play_dir}\n"
                    f"• Message: {msg_path}"
                )
                return

            if mode == "seal":
                play_dir = gen_fn(
                    str(self.project_root),
                    message_html=message_html,
                    open_in_browser=False,
                )

                self._log(
                    "✅ Play bundle updated.\n"
                    f"• Play (GitHub target): {play_dir}\n\n"
                    "Opening Play folder now."
                )

                _open_folder(Path(play_dir))
                return

            self._log(f"❌ Internal error: unknown mode '{mode}'")

        except Exception as e:
            tb = traceback.format_exc(limit=30)
            self._log(f"❌ Pipeline error: {type(e).__name__}: {e}\n\n{tb}")

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────
    def _read_message_html(self, msg_path: Path) -> str:
        try:
            if not msg_path.exists():
                return ""
            return msg_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _log(self, text: str) -> None:
        self.status.setPlainText(text)

    # ─────────────────────────────────────────────────────────────────────
    # Button styles
    # ─────────────────────────────────────────────────────────────────────
    def _styled_button(self, text: str, bg_color: str, border_color: str, text_color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 14))
        btn.setMinimumHeight(52)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"background:{bg_color}; border:2px solid {border_color};"
            f"border-radius:10px; padding:14px 20px;"
            f"color:{text_color}; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{border_color}; }}"
        )
        btn.setGraphicsEffect(self._shadow_effect(16))
        return btn

    def _utility_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 12))
        btn.setMinimumHeight(44)
        btn.setStyleSheet(
            "QPushButton { background:#0f0f12; color:#e6e6e6;"
            "border:1px solid #00d0ff; border-radius:8px; padding:10px 14px; }"
            "QPushButton:hover { background:#113945; }"
        )
        btn.setGraphicsEffect(self._shadow_effect(12))
        return btn

    def _tiny_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        btn.setFixedHeight(30)
        btn.setStyleSheet(
            "QPushButton { background:#0f0f12; color:#e6e6e6;"
            "border:1px solid #00d0ff; border-radius:8px; padding:6px 12px; }"
            "QPushButton:hover { background:#113945; }"
        )
        return btn

    def _shadow_effect(self, blur_radius: int) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur_radius)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        return shadow
