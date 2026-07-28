# File: Over_Nexus.py
# Purpose: Nexus tab helper wiring + optional standalone PromptWriterPanel launcher.
#
# Current behavior:
# - No hidden Wall/Helper overlay buttons.
# - The Message details area shows automatically on the Message tab.
# - The Message details area hides automatically when leaving the Message tab.
# - The wall/text-background image is handled from Image_tab.py.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon

# Optional: Standalone PromptWriterPanel.
try:
    from PromptWriterPanel import PromptWriterPanel  # type: ignore
except Exception:
    PromptWriterPanel = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Theme & Configuration
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Theme:
    bg: str = "#1b1b1b"
    fg: str = "#e6e6e6"
    accent: str = "#8feaff"


@dataclass(frozen=True)
class Config:
    # Optional PromptWriter launcher icon candidates.
    ICON_PROMPTER_CANDIDATES: tuple[str, ...] = (
        os.path.join("gallery", "icons", "pwrite.png"),
        os.path.join("gallery", "icons", "Pwrite.png"),
        os.path.join("gallery", "app", "icons", "pwrite.png"),
        os.path.join("gallery", "app", "icons", "Pwrite.png"),
    )

    ICON_SIZE: QSize = QSize(96, 96)

    # Disabled by default. Kept only for compatibility with older Nexus setups.
    show_prompter_launcher: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class OverNexusController(QtCore.QObject):
    """
    Wires small Nexus-level behavior.

    Tabs assumed:
        Images  = 0
        Sound   = 1
        Message = 2
        Forge   = 3
        Command = 4
    """

    def __init__(self, nexus: object, project_root: str, theme: Theme, cfg: Config):
        super().__init__(nexus)

        self.nexus = nexus
        self.project_root = project_root
        self.theme = theme
        self.cfg = cfg

        host = getattr(self.nexus, "container", None)
        if not isinstance(host, QtWidgets.QWidget):
            host = self.nexus if isinstance(self.nexus, QtWidgets.QWidget) else None
        if host is None:
            raise RuntimeError("Over_Nexus: could not resolve a QWidget host.")

        self.prompter_btn: Optional[QtWidgets.QPushButton] = None
        self.prompt_panel: Optional[QtWidgets.QWidget] = None

        self._install_optional_promptwriter_launcher(host)

        self.nexus.tabbar.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.nexus.tabbar.currentIndex())

    # ──────────────────────────────────────────────────────────────────
    # Optional PromptWriter launcher
    # ──────────────────────────────────────────────────────────────────
    def _install_optional_promptwriter_launcher(self, host: QtWidgets.QWidget) -> None:
        if not (self.cfg.show_prompter_launcher and PromptWriterPanel is not None):
            return

        self.prompter_btn = QtWidgets.QPushButton(host)
        self.prompter_btn.setCursor(Qt.PointingHandCursor)
        self.prompter_btn.setFlat(True)
        self.prompter_btn.setStyleSheet("QPushButton{border:none; background:transparent;}")

        icon_path = None
        for rel in self.cfg.ICON_PROMPTER_CANDIDATES:
            cand = os.path.join(self.project_root, rel)
            if os.path.exists(cand):
                icon_path = cand
                break

        if icon_path:
            self.prompter_btn.setIcon(QIcon(icon_path))
            self.prompter_btn.setIconSize(self.cfg.ICON_SIZE)
            self.prompter_btn.setToolTip("Prompt Writer")
            self.prompter_btn.setFixedSize(self.cfg.ICON_SIZE)
        else:
            self.prompter_btn.setText("Prompt\nWriter")
            self.prompter_btn.setFixedSize(120, 96)

        self.prompter_btn.move(24, 24)
        self.prompter_btn.clicked.connect(self._toggle_promptwriter)
        self.prompter_btn.setVisible(False)

    def _toggle_promptwriter(self) -> None:
        if PromptWriterPanel is None:
            return

        if self.prompt_panel is None:
            try:
                try:
                    self.prompt_panel = PromptWriterPanel(parent=None, project_root=self.project_root)  # type: ignore[call-arg]
                except TypeError:
                    self.prompt_panel = PromptWriterPanel(parent=None)  # type: ignore[call-arg]
            except Exception:
                self.prompt_panel = None
                return

        try:
            if hasattr(self.prompt_panel, "popup"):
                self.prompt_panel.popup()  # type: ignore[attr-defined]
            else:
                self.prompt_panel.show()
                self.prompt_panel.raise_()
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # Automatic Message tab behavior
    # ──────────────────────────────────────────────────────────────────
    def _on_tab_changed(self, idx: int) -> None:
        is_images = idx == 0
        is_message = idx == 2

        if self.prompter_btn:
            self.prompter_btn.setVisible(is_images)

        self._set_message_details_visible(is_message)

    def _set_message_details_visible(self, visible: bool) -> None:
        message_tab = getattr(self.nexus, "message_tab", None)
        if message_tab is None:
            return

        details = getattr(message_tab, "title_recipient_container", None)
        if not isinstance(details, QtWidgets.QWidget):
            details = getattr(message_tab, "title_sister_container", None)
        if isinstance(details, QtWidgets.QWidget):
            details.setVisible(visible)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def install_over_nexus(
    nexus: object,
    project_root: str,
    theme: Optional[Theme] = None,
    config: Optional[Config] = None,
) -> OverNexusController:
    theme = theme or Theme()
    cfg = config or Config()
    return OverNexusController(nexus, project_root, theme, cfg)
