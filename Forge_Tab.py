# File: Forge_Tab.py — Forge tab (Load + Erase, full export pipeline, polished)
from __future__ import annotations

import os
import shutil
import zipfile
import re
from pathlib import Path

from PySide6 import QtWidgets, QtGui
from PySide6.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QTextEdit
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QUrl

# Command utilities
#  - open_saved_letters(project_root, parent)
#  - confirm_and_reset(parent) → frameless “Are you sure? This will erase everything.” dialog
from command import open_saved_letters, confirm_and_reset  #

# Build steps
from Generate import generate_gallery
from Transmuter import transmute  # expects index.html/styles.css/script.js at project root

from config import (
    GALLERY_DIR,
    OUTPUT_PLAY_DIR, OUTPUT_FILE_DIR, OUTPUT_ZIP_DIR,
    ensure_output_dirs,
)


class ForgeTab(QtWidgets.QWidget):
    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = str(project_root)
        self._init_ui()

    # ──────────────────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────────────────
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

        # Top row: tiny utility buttons on the right (Load | Erase)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addStretch(1)

        self.load_btn = self._tiny_button("Load")
        self.load_btn.setToolTip("Open Saved Letters — load/rename/delete previous builds")
        self.load_btn.clicked.connect(self.open_saved_letters_from_forge)
        top_row.addWidget(self.load_btn, 0, Qt.AlignRight)

        self.erase_btn = self._tiny_button("Erase")
        self.erase_btn.setToolTip("Erase everything (with confirmation)")
        self.erase_btn.clicked.connect(self.erase_everything_confirm)
        top_row.addWidget(self.erase_btn, 0, Qt.AlignRight)

        layout.addLayout(top_row)

        # Main action buttons
        btns = QHBoxLayout()
        btns.setSpacing(10)

        self.generate_btn = self._styled_button("Generate", "#ff8800", "#ffaa00", "yellow")
        self.generate_btn.setToolTip("Build the Play bundle and preview in your browser")
        self.generate_btn.clicked.connect(self.generate)
        btns.addWidget(self.generate_btn)

        self.save_btn = self._styled_button("Process Final", "#ff69b4", "#ff1493", "red")
        self.save_btn.setToolTip("Build all outputs, then open output/File")
        self.save_btn.clicked.connect(self.process_final)
        btns.addWidget(self.save_btn)

        self.download_btn = self._styled_button("📦 Seal the Letter 📦", "#6a5acd", "#836fff", "white")
        self.download_btn.setToolTip("Build all outputs, then open output/Zip")
        self.download_btn.clicked.connect(self.seal_the_letter)
        btns.addWidget(self.download_btn)

        layout.addLayout(btns)

        # Status console
        self.status = QTextEdit(readOnly=True)
        self.status.setFont(QFont("Segoe UI", 11))
        self.status.setStyleSheet(
            "background:#1a1a1a; border:1px solid #00d0ff;"
            " border-radius:4px; color:#ddd;"
        )
        layout.addWidget(self.status)

        # Quick opens
        self.open_root_btn = self._styled_button("📂 Open Project Folder", "#222", "#00d0ff", "#eee")
        self.open_root_btn.clicked.connect(self.open_project_folder)
        layout.addWidget(self.open_root_btn)

        self.open_output_btn = self._styled_button("📂 Open Output Folder", "#222", "#00d0ff", "#eee")
        self.open_output_btn.clicked.connect(self.open_output_folder)
        layout.addWidget(self.open_output_btn)

    # ──────────────────────────────────────────────────────────────────────────
    # Tiny actions
    # ──────────────────────────────────────────────────────────────────────────
    def open_saved_letters_from_forge(self) -> None:
        open_saved_letters(self.project_root, parent=self)  # reuses Command dialog

    def erase_everything_confirm(self) -> None:
        # Frameless Yes/No “Are you sure? This will erase everything.” dialog
        # (implemented in command.py)
        confirm_and_reset(self)  #

    def open_project_folder(self) -> None:
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(self.project_root))

    def open_output_folder(self) -> None:
        ensure_output_dirs(self.project_root)
        out_parent = (Path(self.project_root) / OUTPUT_PLAY_DIR).parent
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_parent)))

    # ──────────────────────────────────────────────────────────────────────────
    # Button styles
    # ──────────────────────────────────────────────────────────────────────────
    def _styled_button(self, text: str, bg_color: str, border_color: str, text_color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI Semibold", 14))
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {bg_color}; border: 2px solid {border_color};"
            f"  border-radius: 8px; padding: 12px;"
            f"  color: {text_color}; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{ background-color: {border_color}; color: #ffff99; }}"
        )
        btn.setGraphicsEffect(self._shadow_effect(16))
        return btn

    def _tiny_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            """
            QPushButton {
                background-color: #0f0f12;
                color: #e6e6e6;
                border: 1px solid #00d0ff;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #113945; }
            QPushButton:pressed { background-color: #0b252d; }
            """
        )
        btn.setGraphicsEffect(self._shadow_effect(12))
        return btn

    def _shadow_effect(self, blur_radius: int) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur_radius)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        return shadow

    # ──────────────────────────────────────────────────────────────────────────
    # Pipeline actions (all three buttons run the full pipeline)
    #   • Generate       → full build; open browser ONLY
    #   • Process Final  → full build; open output/File
    #   • Seal the Letter→ full build; open output/Zip
    # ──────────────────────────────────────────────────────────────────────────
    def generate(self) -> None:
        self._run_pipeline(open_browser=True, open_folder=None)

    def process_final(self) -> None:
        self._run_pipeline(open_browser=False, open_folder='file')

    def seal_the_letter(self) -> None:
        self._run_pipeline(open_browser=False, open_folder='zip')

    def _run_pipeline(self, open_browser: bool = False, open_folder: str | None = None) -> None:
        ensure_output_dirs(self.project_root)

        # Validate required slide images exist in /gallery
        missing = self._missing_required_images()
        if missing:
            self._log(f"❌ Cannot generate: missing {', '.join(missing)}")
            return

        try:
            # Pull message HTML from the shell’s message_tab (or fallback to message.html)
            _, message_html = self._resolve_message_html()

            # 1) Build Play bundle (under output/Play/Letter for <recipient>)
            play_dir = generate_gallery(self.project_root, message_html, open_in_browser=open_browser)  #

            # 2) Sync Play’s core files back to project root so Transmuter can inline
            self._sync_core_files_from_play(Path(play_dir))

            # 3) Build single-file (writes to /output/File)
            display_name = self._get_recipient_name()
            single_path = transmute(self.project_root, recipient_name=display_name)  # writes to OUTPUT_FILE_DIR

            # 4) Create Zip: include single-file + entire Play folder
            zip_path = self._zip_outputs(Path(play_dir), Path(single_path), display_name)

            # Status
            self._log(
                "✅ All outputs updated.\n"
                f"• Play: {play_dir}\n"
                f"• File: {single_path}\n"
                f"• ZIP:  {zip_path}"
            )

            # Open requested folder
            if open_folder == 'file':
                QtGui.QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(Path(self.project_root) / OUTPUT_FILE_DIR))
                )
            elif open_folder == 'zip':
                QtGui.QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(Path(self.project_root) / OUTPUT_ZIP_DIR))
                )

        except Exception as e:
            self._log(f"❌ Pipeline error: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────
    def _missing_required_images(self) -> list[str]:
        base = Path(self.project_root) / GALLERY_DIR
        required = ["cover.png", "letter.png", "wall.png", "back.png"]
        missing = [f for f in required if not (base / f).exists()]
        return missing

    def _resolve_message_html(self) -> tuple[dict, str]:
        """
        Try to fetch the live HTML from the shell’s message_tab.current_html.
        Fallback: read ./message.html (if present).
        """
        parent = self.parent()
        while parent and not hasattr(parent, 'message_tab'):
            parent = parent.parent()

        message_html = ""
        if parent:
            message_html = getattr(parent.message_tab, 'current_html', "") or ""

        if not message_html.strip():
            try:
                fp = Path(self.project_root) / "message.html"
                if fp.exists():
                    message_html = fp.read_text(encoding="utf-8")
            except Exception:
                message_html = ""

        return {}, message_html

    def _sync_core_files_from_play(self, play_dir: Path) -> None:
        """
        Ensure index.html, styles.css, and script.js exist at project root so
        Transmuter can inline them (it reads from project-root paths).
        """
        pr = Path(self.project_root)
        for fname in ("index.html", "styles.css", "script.js"):
            src = play_dir / fname
            if src.exists():
                shutil.copy2(src, pr / fname)

    def _zip_outputs(self, play_dir: Path, single_file_path: Path, display_name: str) -> Path:
        Path(self.project_root, OUTPUT_ZIP_DIR).mkdir(parents=True, exist_ok=True)
        safe = self._safe_name(display_name)
        zip_path = Path(self.project_root) / OUTPUT_ZIP_DIR / f"Letter for {safe}.zip"

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Include the single-file HTML at the zip root
            zf.write(str(single_file_path), os.path.basename(str(single_file_path)))
            # Include the entire Play bundle under “Play/…”
            for root, _, files in os.walk(play_dir):
                root_p = Path(root)
                for f in files:
                    full = root_p / f
                    rel = full.relative_to(play_dir)
                    zf.write(str(full), str(Path("Play") / rel))

        return zip_path

    def _get_recipient_name(self) -> str:
        parent = self.parent()
        while parent and not hasattr(parent, 'message_tab'):
            parent = parent.parent()
        if parent:
            raw = getattr(parent.message_tab, "name_input", None)
            if raw is not None:
                name = raw.text().strip()
                if name:
                    return name
        return "eLetter"

    def _safe_name(self, s: str) -> str:
        s = re.sub(r'[\\/:*?"<>|]', "_", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s or "eLetter"

    def _log(self, text: str) -> None:
        self.status.setPlainText(text)
