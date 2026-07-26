# ===============================
# File: Forge_Tab.py
# Purpose: Forge tab - deterministic Play build + Load (Recipient -> Title)
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
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QTextEdit
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QUrl

import generate  # <-- IMPORTANT: module import only
from message_html import read_text_normalized
from project_store import ProjectStore
from project_readiness import assess_project_readiness, project_is_ready
from portable_export import (
    create_publish_package,
    create_single_html,
    create_zip_package,
)
from settings_store import SettingsStore
from transactional_io import PathTransaction

from config import (
    PUBLISHED_PAGE_URL_KEY,
    PLAY_METADATA_FILE,
    OUTPUT_PLAY_DIR,
    OUTPUT_FILE_DIR,
    OUTPUT_PACKAGES_DIR,
    OUTPUT_PUBLISH_DIR,
    CURTAIN_STYLE_KEY,
    CURTAIN_STYLE_LABELS,
    CURTAIN_STYLE_WHITE,
    CURTAIN_STYLE_AVERAGE,
    CURTAIN_STYLE_COMPLEMENTARY,
    DEFAULT_CURTAIN_STYLE,
    VALID_CURTAIN_STYLES,
    ensure_output_dirs,
    play_bundle_path,
    validate_required_images,
    MESSAGE_HTML_FILE,
    USER_PAGES_DIR,
    USER_MESSAGE_DIR,
    USER_SOUNDS_DIR,
    REQUIRED_SLIDES,
    MUSIC_FILE,
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _read_text(path: Path) -> str:
    try:
        return read_text_normalized(path)
    except Exception:
        return ""


def _extract_html_title(index_html: Path) -> str:
    txt = _read_text(index_html)
    m = re.search(r"<title>\s*(.*?)\s*</title>", txt, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return index_html.parent.name
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t or index_html.parent.name


def _load_settings(root: Path) -> dict:
    return SettingsStore(root).as_dict()


def _write_settings(root: Path, data: dict) -> None:
    SettingsStore(root).update_fields(data)


def _normalize_page_url(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    parsed = QUrl.fromUserInput(candidate)
    if not parsed.isValid():
        return ""
    if parsed.scheme().lower() not in {"http", "https"}:
        return ""
    if not parsed.host():
        return ""
    return parsed.toString()


def _metadata_path(play_dir: Path) -> Path:
    return play_dir / PLAY_METADATA_FILE


def _read_play_metadata(play_dir: Path) -> dict:
    path = _metadata_path(play_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _humanize_slug(s: str) -> str:
    s2 = s.replace("_", " ").replace("-", " ").strip()
    s2 = re.sub(r"\s+", " ", s2)
    return s2.title() if s2 else s


def _curtain_style_from_settings(root: Path) -> str:
    settings = _load_settings(root)
    style = str(settings.get(CURTAIN_STYLE_KEY, DEFAULT_CURTAIN_STYLE)).strip().lower()
    aliases = {
        "white": CURTAIN_STYLE_WHITE,
        "pure white": CURTAIN_STYLE_WHITE,
        "pure_white": CURTAIN_STYLE_WHITE,
        "blank": CURTAIN_STYLE_WHITE,
        "original": CURTAIN_STYLE_WHITE,
        "average": CURTAIN_STYLE_AVERAGE,
        "average color": CURTAIN_STYLE_AVERAGE,
        "average_color": CURTAIN_STYLE_AVERAGE,
        "common": CURTAIN_STYLE_AVERAGE,
        "common color": CURTAIN_STYLE_AVERAGE,
        "complementary": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary average": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary average color": CURTAIN_STYLE_COMPLEMENTARY,
        "complementary_average_color": CURTAIN_STYLE_COMPLEMENTARY,
    }
    style = aliases.get(style, style)
    return style if style in VALID_CURTAIN_STYLES else DEFAULT_CURTAIN_STYLE


def _curtain_style_label(style: str) -> str:
    return CURTAIN_STYLE_LABELS.get(style, CURTAIN_STYLE_LABELS[DEFAULT_CURTAIN_STYLE])


def _forge_menu_style() -> str:
    return """
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
        QMenu::item:disabled {
            color: #7f8b9a;
        }
        QMenu::separator {
            height: 1px;
            background: #2b3344;
            margin: 6px 6px;
        }
    """


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


# ---------------------------------------------------------------------
# ForgeTab
# ---------------------------------------------------------------------
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
    fix_requested = QtCore.Signal(str)
    preview_requested = QtCore.Signal(str, str)
    preview_restart_requested = QtCore.Signal()
    preview_mute_requested = QtCore.Signal(bool)
    preview_report_requested = QtCore.Signal()
    project_will_open = QtCore.Signal()
    project_opened = QtCore.Signal(dict)

    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = Path(project_root)
        self.saved_page_url = ""
        self._preview_mode = "desktop"
        self._preview_muted = False
        self._last_play_dir: Optional[Path] = None
        self.project_store = ProjectStore(self.project_root)
        self._init_ui()
        self.refresh_saved_page_url()

    # ---------------------------------------------------------------------
    # UI
    # ---------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Generate Animated Letter")
        title.setFont(QFont("Segoe UI Semibold", 18))
        title.setStyleSheet("color: #00d0ff;")
        title.setAlignment(Qt.AlignCenter)
        title.setGraphicsEffect(self._shadow_effect(12))
        layout.addWidget(title)

        # Top row: settings and load menus
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addStretch(1)

        self.project_btn = self._tiny_button("Project")
        self.project_btn.setToolTip("Create, open, save, duplicate, or restore a project")
        self.project_btn.clicked.connect(self._open_project_menu)
        top_row.addWidget(self.project_btn, 0, Qt.AlignRight)

        self.settings_btn = self._tiny_button("Settings")
        self.settings_btn.setToolTip("Choose curtain style")
        self.settings_btn.clicked.connect(self._open_settings_menu)
        top_row.addWidget(self.settings_btn, 0, Qt.AlignRight)

        self.load_btn = self._tiny_button("Load")
        self.load_btn.setToolTip("Load a saved letter from output/Play (Recipient -> Title)")
        self.load_btn.clicked.connect(self._open_load_menu)
        top_row.addWidget(self.load_btn, 0, Qt.AlignRight)

        layout.addLayout(top_row)

        self._build_readiness_panel(layout)
        self._build_preview_controls(layout)

        # Main action buttons
        btns = QHBoxLayout()
        btns.setSpacing(12)

        self.generate_btn = self._styled_button("Generate", "#ff8800", "#ffaa00", "yellow")
        self.generate_btn.setToolTip("Build the Play bundle and preview in your browser")
        self.generate_btn.clicked.connect(self.generate)
        btns.addWidget(self.generate_btn)

        self.seal_btn = self._styled_button(" Seal the Letter", "#6a5acd", "#836fff", "white")
        self.seal_btn.setToolTip("Build the Play bundle and open the Play folder (GitHub Pages target)")
        self.seal_btn.clicked.connect(self.seal_the_letter)
        btns.addWidget(self.seal_btn)

        self.export_btn = self._styled_button("Export", "#147a68", "#20a78d", "white")
        self.export_btn.setToolTip("Export a Web Folder, ZIP, Single HTML, or Publish Package")
        self.export_btn.clicked.connect(self._open_export_menu)
        btns.addWidget(self.export_btn)

        self.go_to_page_btn = self._page_button("Go to Page")
        self.go_to_page_btn.clicked.connect(self.go_to_page)
        btns.addWidget(self.go_to_page_btn)

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
        self.open_output_btn = self._utility_button(" Open Output Folder")
        self.open_output_btn.clicked.connect(self.open_output_folder)
        layout.addWidget(self.open_output_btn)

        self._log("Ready.")
        self.refresh_readiness()

    def _open_project_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_forge_menu_style())
        active = self.project_store.active_project_id
        projects = {project.project_id: project for project in self.project_store.list_projects()}
        current_name = projects[active].name if active in projects else "Unsaved workspace"
        heading = menu.addAction(f"Current: {current_name}")
        heading.setEnabled(False)
        menu.addSeparator()
        menu.addAction("New Project", self._new_project)
        menu.addAction("Save", self._save_project)
        menu.addAction("Save As", self._save_project_as)
        duplicate = menu.addAction("Duplicate Project", self._duplicate_project)
        duplicate.setEnabled(bool(active))

        recent_menu = menu.addMenu("Recent Projects")
        for project in self.project_store.recent_projects():
            action = recent_menu.addAction(project.name)
            action.triggered.connect(
                lambda _checked=False, project_id=project.project_id: self._open_project(project_id)
            )
        if not recent_menu.actions():
            empty = recent_menu.addAction("No saved projects")
            empty.setEnabled(False)

        revision_menu = menu.addMenu("Restore Previous Message")
        for revision in self.project_store.list_message_revisions():
            label = revision.name.split("-", 2)
            display = f"{label[0]} {label[1]}" if len(label) > 1 else revision.stem
            action = revision_menu.addAction(display)
            action.setToolTip(str(revision))
            action.triggered.connect(
                lambda _checked=False, path=revision: self._restore_message_revision(path)
            )
        if not revision_menu.actions():
            empty = revision_menu.addAction("No message revisions")
            empty.setEnabled(False)

        gp = self.project_btn.mapToGlobal(QtCore.QPoint(0, self.project_btn.height()))
        menu.popup(gp)

    def _open_export_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_forge_menu_style())
        for label, kind in (
            ("Web Folder", "web"),
            ("ZIP Package", "zip"),
            ("Single HTML", "single"),
            ("Publish Package", "publish"),
        ):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, export_kind=kind: self._perform_portable_export(export_kind)
            )
        gp = self.export_btn.mapToGlobal(QtCore.QPoint(0, self.export_btn.height()))
        menu.popup(gp)

    def _perform_portable_export(self, kind: str) -> None:
        items = assess_project_readiness(self.project_root)
        missing = [item.label for item in items if item.required and not item.ready]
        if missing:
            self._log("[ERROR] Complete these required items first:\n- " + "\n- ".join(missing))
            return
        gen_fn = _get_generate_fn()
        if gen_fn is None:
            self._log("[ERROR] The Play generator is unavailable.")
            return
        message_html = self._read_message_html(self.project_root / MESSAGE_HTML_FILE)
        try:
            self.autosave_project()
            play_dir = Path(
                gen_fn(
                    str(self.project_root),
                    message_html=message_html,
                    open_in_browser=False,
                )
            )
            self._last_play_dir = play_dir
            if kind == "web":
                exported = play_dir
            elif kind == "zip":
                exported = create_zip_package(
                    play_dir, self.project_root / OUTPUT_PACKAGES_DIR
                )
            elif kind == "single":
                exported = create_single_html(
                    play_dir, self.project_root / OUTPUT_FILE_DIR
                )
            elif kind == "publish":
                exported = create_publish_package(
                    play_dir, self.project_root / OUTPUT_PUBLISH_DIR
                )
            else:
                raise ValueError(f"Unknown export format: {kind}")

            self.refresh_readiness()
            self.request_preview()
            self._log(f"[OK] Export created:\n{exported}")
            _open_folder(exported if exported.is_dir() else exported.parent)
        except Exception as error:
            self._log(f"[ERROR] Export failed: {type(error).__name__}: {error}")

    def _prompt_project_name(self, title: str, initial: str = "") -> str:
        name, accepted = QtWidgets.QInputDialog.getText(
            self, title, "Project name:", text=initial
        )
        return name.strip() if accepted else ""

    def _new_project(self) -> None:
        name = self._prompt_project_name("New Project")
        if not name:
            return
        try:
            if self.project_store.active_project_id:
                self.project_store.save_active()
            elif self._workspace_has_content():
                backup_name = "Workspace Backup " + QtCore.QDateTime.currentDateTime().toString(
                    "yyyy-MM-dd hh-mm-ss"
                )
                self.project_store.save_as(backup_name, activate=False)
            self.project_will_open.emit()
            project = self.project_store.create(name)
            self._finish_project_open(project)
        except Exception as error:
            self._log(f"[ERROR] Could not create project: {error}")

    def _save_project(self) -> None:
        try:
            project = self.project_store.save_active()
            if project is None:
                self._save_project_as()
                return
            self._log(f"[OK] Project saved: {project.name}")
            self.refresh_readiness()
        except Exception as error:
            self._log(f"[ERROR] Could not save project: {error}")

    def _save_project_as(self) -> None:
        name = self._prompt_project_name("Save Project As")
        if not name:
            return
        try:
            project = self.project_store.save_as(name, activate=True)
            self._finish_project_open(project)
        except Exception as error:
            self._log(f"[ERROR] Could not save project: {error}")

    def _duplicate_project(self) -> None:
        name = self._prompt_project_name("Duplicate Project")
        if not name:
            return
        try:
            project = self.project_store.duplicate_active(name)
            self._finish_project_open(project)
        except Exception as error:
            self._log(f"[ERROR] Could not duplicate project: {error}")

    def _open_project(self, project_id: str) -> None:
        try:
            if self.project_store.active_project_id:
                self.project_store.save_active()
            self.project_will_open.emit()
            project = self.project_store.open(project_id)
            self._finish_project_open(project)
        except Exception as error:
            self._log(f"[ERROR] Could not open project: {error}")

    def _restore_message_revision(self, revision: Path) -> None:
        try:
            html = self.project_store.restore_message_revision(revision)
            self._log(f"[OK] Restored message revision:\n{revision.name}")
            self.project_opened.emit(
                {
                    "project_id": self.project_store.active_project_id,
                    "message_html": html,
                    "restored_revision": str(revision),
                }
            )
            self.refresh_readiness()
        except Exception as error:
            self._log(f"[ERROR] Could not restore message revision: {error}")

    def _finish_project_open(self, project) -> None:
        self._last_play_dir = None
        self.refresh_saved_page_url()
        self.refresh_readiness()
        self._log(f"[OK] Active project: {project.name}\n{project.path}")
        self.project_opened.emit(
            {
                "project_id": project.project_id,
                "project_name": project.name,
                "project_path": str(project.path),
            }
        )

    def _workspace_has_content(self) -> bool:
        paths = (
            self.project_root / USER_PAGES_DIR,
            self.project_root / USER_MESSAGE_DIR,
            self.project_root / USER_SOUNDS_DIR / MUSIC_FILE,
        )
        return any(path.is_file() or (path.is_dir() and any(path.iterdir())) for path in paths)

    def autosave_project(self) -> None:
        if not self.project_store.active_project_id:
            return
        try:
            self.project_store.save_active()
        except Exception as error:
            self._log(f"[WARNING] Project autosave failed: {error}")

    def _build_preview_controls(self, parent_layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel("Finished viewer:")
        label.setStyleSheet("color:#b9c9d2;")
        row.addWidget(label)

        self._device_buttons: dict[str, QPushButton] = {}
        for mode, text in (
            ("desktop", "Desktop"),
            ("phone-portrait", "Phone portrait"),
            ("phone-landscape", "Phone landscape"),
        ):
            button = self._tiny_button(text)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, selected=mode: self.request_preview(selected)
            )
            row.addWidget(button)
            self._device_buttons[mode] = button

        row.addStretch(1)
        self.restart_preview_btn = self._tiny_button("Restart")
        self.restart_preview_btn.clicked.connect(self.preview_restart_requested.emit)
        row.addWidget(self.restart_preview_btn)

        self.mute_preview_btn = self._tiny_button("Mute")
        self.mute_preview_btn.setCheckable(True)
        self.mute_preview_btn.toggled.connect(self._set_preview_muted)
        row.addWidget(self.mute_preview_btn)

        self.open_preview_btn = self._tiny_button("Open externally")
        self.open_preview_btn.clicked.connect(self.open_preview_externally)
        row.addWidget(self.open_preview_btn)

        self.report_preview_btn = self._tiny_button("Report missing asset")
        self.report_preview_btn.clicked.connect(self.preview_report_requested.emit)
        row.addWidget(self.report_preview_btn)
        parent_layout.addLayout(row)
        self._sync_preview_controls(False)

    def _sync_preview_controls(self, available: bool) -> None:
        for mode, button in self._device_buttons.items():
            button.setEnabled(available)
            button.setChecked(mode == self._preview_mode)
        for button in (
            self.restart_preview_btn,
            self.mute_preview_btn,
            self.open_preview_btn,
            self.report_preview_btn,
        ):
            button.setEnabled(available)

    def _current_play_index(self) -> Optional[Path]:
        if self._last_play_dir is not None:
            index = self._last_play_dir / "index.html"
            if index.is_file():
                return index
        settings = _load_settings(self.project_root)
        recipient = str(settings.get("recipient_name") or "Friend").strip() or "Friend"
        title = str(settings.get("recipient_title") or f"Letter for {recipient}").strip()
        index = play_bundle_path(
            self.project_root, recipient=recipient, title=title
        ) / "index.html"
        return index if index.is_file() else None

    def request_preview(self, mode: Optional[str] = None) -> None:
        if mode in self._device_buttons:
            self._preview_mode = str(mode)
        index = self._current_play_index()
        self._sync_preview_controls(index is not None)
        if index is None:
            return
        self.preview_requested.emit(str(index.resolve()), self._preview_mode)

    def _set_preview_muted(self, muted: bool) -> None:
        self._preview_muted = bool(muted)
        self.mute_preview_btn.setText("Unmute" if muted else "Mute")
        self.preview_mute_requested.emit(self._preview_muted)

    def open_preview_externally(self) -> None:
        index = self._current_play_index()
        if index is None:
            self._log("Generate the letter before opening its finished viewer.")
            return
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(index.resolve())))

    def show_preview_report(self, missing_assets: list[str]) -> None:
        if missing_assets:
            self._log(
                "[WARNING] The finished preview could not load:\n"
                + "\n".join(f"- {asset}" for asset in missing_assets)
            )
        else:
            self._log("[OK] The finished preview reports no missing image or audio assets.")

    def _build_readiness_panel(self, parent_layout: QVBoxLayout) -> None:
        panel = QtWidgets.QFrame()
        panel.setObjectName("ReadinessPanel")
        panel.setStyleSheet(
            "#ReadinessPanel { background:#15191d; border:1px solid #2b5963; border-radius:7px; }"
            "QLabel { border:none; }"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(5)

        header = QHBoxLayout()
        caption = QLabel("Project readiness")
        caption.setFont(QFont("Segoe UI Semibold", 11))
        caption.setStyleSheet("color:#dffcff;")
        header.addWidget(caption)
        header.addStretch(1)
        self.readiness_summary = QLabel()
        self.readiness_summary.setStyleSheet("color:#aab7c4;")
        header.addWidget(self.readiness_summary)
        outer.addLayout(header)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        self._readiness_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        for index, item in enumerate(assess_project_readiness(self.project_root)):
            cell = QtWidgets.QWidget()
            row = QHBoxLayout(cell)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            state = QLabel()
            state.setMinimumWidth(145)
            row.addWidget(state, 1)
            fix = QPushButton("Fix")
            fix.setFixedSize(38, 22)
            fix.setStyleSheet(
                "QPushButton { padding:1px 5px; color:#8cecff; border:1px solid #39717c; }"
                "QPushButton:hover { background:#17343a; }"
            )
            fix.clicked.connect(
                lambda _checked=False, key=item.key: self.fix_requested.emit(key)
            )
            row.addWidget(fix)
            grid.addWidget(cell, index % 5, index // 5)
            self._readiness_rows[item.key] = (state, fix)
        outer.addLayout(grid)
        parent_layout.addWidget(panel)

    def refresh_readiness(self) -> None:
        items = assess_project_readiness(self.project_root)
        missing_required = sum(1 for item in items if item.required and not item.ready)
        for item in items:
            row = self._readiness_rows.get(item.key)
            if row is None:
                continue
            state, fix = row
            if item.ready:
                prefix, color = "✓", "#79e092"
            elif item.required:
                prefix, color = "✕", "#ff8080"
            else:
                prefix, color = "○", "#bdc7d0"
            optional = " (optional)" if not item.required else ""
            state.setText(f"{prefix} {item.label}{optional}")
            state.setStyleSheet(f"color:{color};")
            state.setToolTip(item.detail)
            fix.setVisible(not item.ready)
            fix.setToolTip(f"Fix {item.label.lower()}")

        if project_is_ready(items):
            self.readiness_summary.setText("Ready to generate")
            self.readiness_summary.setStyleSheet("color:#79e092;")
        else:
            suffix = "item" if missing_required == 1 else "items"
            self.readiness_summary.setText(f"{missing_required} required {suffix} missing")
            self.readiness_summary.setStyleSheet("color:#ff9a9a;")

    # ---------------------------------------------------------------------
    # Forge settings
    # ---------------------------------------------------------------------
    def _open_settings_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_forge_menu_style())

        header = menu.addAction("Curtain Style")
        header.setEnabled(False)
        menu.addSeparator()

        current = _curtain_style_from_settings(self.project_root)
        for style in (CURTAIN_STYLE_WHITE, CURTAIN_STYLE_AVERAGE, CURTAIN_STYLE_COMPLEMENTARY):
            action = menu.addAction(_curtain_style_label(style))
            action.setCheckable(True)
            action.setChecked(style == current)
            action.triggered.connect(lambda _=False, selected=style: self._set_curtain_style(selected))

        gp = self.settings_btn.mapToGlobal(QtCore.QPoint(0, self.settings_btn.height()))
        menu.popup(gp)

    def _set_curtain_style(self, style: str) -> None:
        if style not in VALID_CURTAIN_STYLES:
            style = DEFAULT_CURTAIN_STYLE

        _write_settings(self.project_root, {CURTAIN_STYLE_KEY: style})

        self._log(
            "Curtain style set to: "
            f"{_curtain_style_label(style)}\n\n"
            "This will be applied the next time you Generate or Seal the Letter."
        )

    # ---------------------------------------------------------------------
    # Load menu (Recipient -> Title)
    # ---------------------------------------------------------------------
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
        menu.setStyleSheet(_forge_menu_style())

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
            self._log("[ERROR] Selected folder is missing.")
            return

        # Source runtime folders
        src_gallery = play_dir / "gallery"
        src_pages = src_gallery / "pages"
        src_message = src_gallery / "message"
        src_sounds = src_gallery / "sounds"
        src_music = src_sounds / MUSIC_FILE

        if not src_pages.is_dir():
            self._log("[ERROR] Invalid build: missing gallery/pages/")
            return
        if not src_message.is_dir():
            self._log("[ERROR] Invalid build: missing gallery/message/")
            return
        missing_pages = [name for name in REQUIRED_SLIDES if not (src_pages / name).is_file()]
        if missing_pages:
            self._log("[ERROR] Invalid build: missing required pages: " + ", ".join(missing_pages))
            return

        # Dest canonical SOURCE folders
        dst_pages = (self.project_root / USER_PAGES_DIR).resolve()
        dst_message = (self.project_root / USER_MESSAGE_DIR).resolve()
        dst_sounds = (self.project_root / USER_SOUNDS_DIR).resolve()
        dst_music = (dst_sounds / MUSIC_FILE).resolve()

        dst_sounds.mkdir(parents=True, exist_ok=True)

        metadata = _read_play_metadata(play_dir)
        updates = {}
        updates["recipient_name"] = str(
            metadata.get("recipient_name")
            or recip_display
            or _humanize_slug(recip_slug)
        ).strip()
        updates["recipient_title"] = str(
            metadata.get("recipient_title")
            or title_display
        ).strip()
        updates[PUBLISHED_PAGE_URL_KEY] = _normalize_page_url(
            str(metadata.get(PUBLISHED_PAGE_URL_KEY, "")).strip()
        )
        metadata_curtain_style = str(metadata.get(CURTAIN_STYLE_KEY, "")).strip().lower()
        if metadata_curtain_style in VALID_CURTAIN_STYLES:
            updates[CURTAIN_STYLE_KEY] = metadata_curtain_style

        pages_tx = PathTransaction(dst_pages, staging_suffix=".load-staging", backup_suffix=".load-backup")
        message_tx = PathTransaction(dst_message, staging_suffix=".load-staging", backup_suffix=".load-backup")
        music_tx = PathTransaction(dst_music, staging_suffix=".load-staging", backup_suffix=".load-backup")
        transactions = (pages_tx, message_tx, music_tx)
        committed: list[PathTransaction] = []

        try:
            staged_pages = pages_tx.prepare()
            staged_message = message_tx.prepare()
            staged_music = music_tx.prepare()
            shutil.copytree(src_pages, staged_pages)
            shutil.copytree(src_message, staged_message)
            if src_music.is_file():
                shutil.copy2(src_music, staged_music)

            staged_missing = [
                name for name in REQUIRED_SLIDES if not (staged_pages / name).is_file()
            ]
            if staged_missing:
                raise RuntimeError("Staged load is missing pages: " + ", ".join(staged_missing))

            window = self.window()
            sound_tab = getattr(window, "sound_tab", None)
            wave = getattr(sound_tab, "wave", None)
            if wave is not None and hasattr(wave, "release_current_file_handle"):
                wave.release_current_file_handle()

            pages_tx.commit(keep_backup=True)
            committed.append(pages_tx)
            message_tx.commit(keep_backup=True)
            committed.append(message_tx)
            music_tx.commit(replace=src_music.is_file(), keep_backup=True)
            committed.append(music_tx)

            settings = SettingsStore(self.project_root).update_fields(updates)
        except Exception as error:
            for transaction in reversed(committed):
                try:
                    transaction.rollback()
                except Exception:
                    pass
            for transaction in transactions:
                try:
                    transaction.abort()
                except Exception:
                    pass
            self._log(f"[ERROR] Saved-letter load rolled back: {type(error).__name__}: {error}")
            return

        for transaction in transactions:
            try:
                transaction.finalize()
            except Exception:
                pass

        self.refresh_saved_page_url()

        copied_pages = sum(1 for path in src_pages.iterdir() if path.is_file())
        copied_msg = sum(1 for _path in src_message.iterdir())
        music_status = "restored" if src_music.is_file() else "cleared"

        self._log(
            "[OK] Loaded saved letter\n"
            f"Recipient: {settings['recipient_name']}\n"
            f"Title: {settings['recipient_title']}\n"
            f"From: {play_dir}\n\n"
            f"Transaction committed: pages ({copied_pages}), message ({copied_msg}), "
            f"music ({music_status})\n\n"
            "Preview will update immediately."
        )

        payload = {
            "recipient_name": str(settings.get("recipient_name", "")).strip(),
            "recipient_title": str(settings.get("recipient_title", "")).strip(),
            "published_page_url": str(settings.get(PUBLISHED_PAGE_URL_KEY, "")).strip(),
            "play_dir": str(play_dir),
        }
        self._last_play_dir = play_dir
        self.request_preview()
        QtCore.QTimer.singleShot(0, lambda: self.letter_loaded.emit(payload))

    # ---------------------------------------------------------------------
    # Utility actions
    # ---------------------------------------------------------------------
    def open_output_folder(self) -> None:
        ensure_output_dirs(self.project_root)
        out_parent = (self.project_root / OUTPUT_PLAY_DIR).parent
        _open_folder(out_parent)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh_saved_page_url()
        self.refresh_readiness()
        QtCore.QTimer.singleShot(0, self.request_preview)

    def refresh_saved_page_url(self) -> str:
        settings = _load_settings(self.project_root)
        self.saved_page_url = _normalize_page_url(str(settings.get(PUBLISHED_PAGE_URL_KEY, "")).strip())
        self._sync_go_to_page_button()
        return self.saved_page_url

    def set_saved_page_url(self, url: str) -> None:
        self.saved_page_url = _normalize_page_url(url)
        self._sync_go_to_page_button()

    def _sync_go_to_page_button(self) -> None:
        has_url = bool(self.saved_page_url)
        self.go_to_page_btn.setEnabled(has_url)
        if has_url:
            self.go_to_page_btn.setToolTip(self.saved_page_url)
        else:
            self.go_to_page_btn.setToolTip("No published page URL has been saved yet.")

    def go_to_page(self) -> None:
        url = self.refresh_saved_page_url()
        if not url:
            self._log("[ERROR] No page URL saved yet. Add it in the Message tab first.")
            return

        if QtGui.QDesktopServices.openUrl(QUrl(url)):
            self._log(f"[OK] Opened published page.\n- URL: {url}")
            return

        self._log(f"[ERROR] Could not open the saved page URL.\n- URL: {url}")

    # ---------------------------------------------------------------------
    # Actions
    # ---------------------------------------------------------------------
    def generate(self) -> None:
        self._run_pipeline(mode="generate")

    def seal_the_letter(self) -> None:
        self._run_pipeline(mode="seal")

    def _run_pipeline(self, *, mode: str) -> None:
        ensure_output_dirs(self.project_root)

        gen_fn = _get_generate_fn()
        if gen_fn is None:
            self._log("[ERROR] generate.py is missing generate_play_bundle/generate_gallery.")
            return

        missing = validate_required_images(self.project_root)
        if missing:
            self._log(
                f"[ERROR] Cannot proceed: missing {', '.join(missing)}\n"
                f"Expected in: {self.project_root / 'gallery/user/pages'}"
            )
            return

        msg_path = (self.project_root / MESSAGE_HTML_FILE).resolve()
        message_html = self._read_message_html(msg_path)

        if not message_html.strip():
            self._log(
                "[ERROR] Cannot proceed: message is empty or missing.\n"
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
                self.refresh_saved_page_url()
                self.refresh_readiness()
                self._last_play_dir = Path(play_dir)
                self.request_preview()
                self._log(
                    "[OK] Play bundle updated and opened in browser.\n"
                    f"- Play: {play_dir}\n"
                    f"- Message: {msg_path}"
                    f"{self._font_export_note()}"
                )
                return

            if mode == "seal":
                play_dir = gen_fn(
                    str(self.project_root),
                    message_html=message_html,
                    open_in_browser=False,
                )
                self.refresh_saved_page_url()
                self.refresh_readiness()
                self._last_play_dir = Path(play_dir)
                self.request_preview()

                self._log(
                    "[OK] Play bundle updated.\n"
                    f"- Play (GitHub target): {play_dir}"
                    f"{self._font_export_note()}\n\n"
                    "Opening Play folder now."
                )

                _open_folder(Path(play_dir))
                return

            self._log(f"[ERROR] Internal error: unknown mode '{mode}'")

        except Exception as e:
            tb = traceback.format_exc(limit=30)
            self._log(f"[ERROR] Pipeline error: {type(e).__name__}: {e}\n\n{tb}")

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    def _read_message_html(self, msg_path: Path) -> str:
        try:
            if not msg_path.exists():
                return ""
            return read_text_normalized(msg_path)
        except Exception:
            return ""

    def _font_export_note(self) -> str:
        reporter = getattr(generate, "get_last_font_export_report", None)
        if not callable(reporter):
            return ""

        report = reporter()
        embedded = tuple(report.get("embedded", ())) if isinstance(report, dict) else ()
        fallback = tuple(report.get("fallback", ())) if isinstance(report, dict) else ()
        notes: list[str] = []
        if embedded:
            notes.append("\n- Embedded fonts: " + ", ".join(embedded))
        if fallback:
            notes.append("\n- Font files not found: " + ", ".join(fallback))
        return "".join(notes)

    def _log(self, text: str) -> None:
        self.status.setPlainText(text)

    # ---------------------------------------------------------------------
    # Button styles
    # ---------------------------------------------------------------------
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

    def _page_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 13))
        btn.setMinimumHeight(52)
        btn.setMinimumWidth(164)
        btn.setMaximumWidth(186)
        btn.setStyleSheet(
            "QPushButton {"
            "background:#24292f; color:#f0f6fc;"
            "border:2px solid #57606a; border-radius:10px; padding:14px 16px;"
            "font-weight:700;"
            "}"
            "QPushButton:hover { background:#30363d; border-color:#8b949e; }"
            "QPushButton:disabled { background:#161b22; color:#6e7681; border-color:#30363d; }"
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
