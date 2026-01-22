# File: prompter_nexus.py
# Prompt Anvil: streamlined twin of Letter Smith with matching chrome.
# - Frameless window using the shared TitleBar (no reticle here)
# - Relative icon resolution for window + title icon (gallery/icons/PAnvil.png)
# - Same tab underline animation + subtle press pulse
# - Simple compose/preview/export scaffold you can extend

from __future__ import annotations

import os
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEasingCurve, QRect, QTimer, QPropertyAnimation
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWebEngineWidgets import QWebEngineView

from titlebar import TitleBar
from anima import ButtonPulseFilter, ParticleBurst


# -----------------------------------------------------------------------------
# Path helper
# -----------------------------------------------------------------------------
def _resolve_asset(project_root: str | None, rel_path: str) -> str:
    """Resolve rel_path to an absolute file under project_root or this module folder."""
    if project_root:
        p = os.path.join(project_root, rel_path)
        if os.path.isfile(p):
            return p
    module_dir = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(module_dir, rel_path)
    return p if os.path.isfile(p) else ""


class PrompterNexus(QtWidgets.QMainWindow):
    """
    Prompt Anvil: lightweight prompt builder with consistent UI/animations.
    """
    def __init__(self, project_root: str):
        super().__init__()
        self.project_root = project_root
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setWindowTitle("Prompt Anvil")

        # ---------------------------------------------------------------------
        # Window/app icon (relative → absolute)
        # ---------------------------------------------------------------------
        panvil_rel = os.path.join("gallery", "icons", "PAnvil.png")
        panvil_abs = _resolve_asset(self.project_root, panvil_rel)
        if panvil_abs:
            self.setWindowIcon(QIcon(panvil_abs))
        else:
            # Not fatal; TitleBar will also try to resolve. Print once for debug clarity.
            print(f"[PrompterNexus] Prompt icon not found at '{panvil_rel}' (checked project_root & module dir)")

        # Stylesheet mirrored from Nexus for visual parity
        self.setStyleSheet("""
            QMainWindow, QWidget, QFrame, QStackedWidget, QTabBar {
                background:#1e1e1e; color:#d0d0d0; font-family:'Segoe UI'; font-size:11px;
            }
            QLabel { color:#b0e0e6; font-weight:600; }
            QPushButton { background:transparent; color:#d0d0d0; border:1px solid #444; border-radius:4px; padding:6px 12px; font:11px 'Segoe UI'; }
            QPushButton:hover { background:#2a2a2a; border-color:#00b2b2; color:#e0f7f7; }
            QPushButton:pressed { background:#353535; border-color:#00a0a0; color:#c0f0f0; }
            QTabBar::tab {
                background:#2b2b2b; color:#a0a0a0; padding:8px 20px; margin-right:2px;
                border:1px solid #444; border-bottom:none; border-top-left-radius:4px; border-top-right-radius:4px;
            }
            QTabBar::tab:last-of-type { margin-right:0; }
            QTabBar::tab:selected { background:#1e1e1e; border-color:#00b2b2; color:#e0fdfd; }
            QTabBar { draw-base:0; }
            QFrame { background:#2b2b2b; border:2px solid #444; border-radius:6px; }
            QWebEngineView { background-color:#1e1e1e; }
        """)

        # ---------------------------------------------------------------------
        # Chrome (TitleBar + Tabs)
        # ---------------------------------------------------------------------
        main_widget = QtWidgets.QWidget(self)
        main_layout = QtWidgets.QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # TitleBar: Prompt Anvil branding, NO reticle
        # Pass the resolved absolute path if we have it; otherwise, pass the relative path so
        # TitleBar’s own resolver can still attempt to find it.
        self.title_bar = TitleBar(
            self,
            title_text="Prompt Anvil",
            title_icon_path=panvil_abs or panvil_rel,
            show_reticle=False,
        )
        # If we have a fully resolved absolute path, force-apply it (skips any ambiguity)
        if panvil_abs:
            self.title_bar.setTitleIcon(panvil_abs)

        main_layout.addWidget(self.title_bar)

        # Tabs
        self.tabbar = QtWidgets.QTabBar()
        for name in ("Compose", "Drafts", "Export"):
            self.tabbar.addTab(name)
        self.tabbar.setDrawBase(False)
        main_layout.addWidget(self.tabbar)

        # Sliding underline indicator (matches Nexus)
        self._tab_indicator = QtWidgets.QFrame(self.tabbar)
        self._tab_indicator.setFixedHeight(3)
        self._tab_indicator.setStyleSheet("background-color:#00c8c8; border:none;")
        self._tab_indicator_anim = QPropertyAnimation(self._tab_indicator, b"geometry", self)
        self._tab_indicator_anim.setDuration(160)
        self._tab_indicator_anim.setEasingCurve(QEasingCurve.OutCubic)

        # ---------------------------------------------------------------------
        # Body: left (inputs) + right (live preview)
        # ---------------------------------------------------------------------
        self.container = QtWidgets.QWidget()
        container_layout = QtWidgets.QHBoxLayout(self.container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(10)

        # Left pane
        self.left_panel = QtWidgets.QFrame()
        self.left_panel.setMinimumWidth(320)
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        self.prompt_title = QtWidgets.QLineEdit()
        self.prompt_title.setPlaceholderText("Prompt Title…")
        self.prompt_body = QtWidgets.QPlainTextEdit()
        self.prompt_body.setPlaceholderText("Write or paste your prompt here…")
        self.btn_generate = QtWidgets.QPushButton("⚒️  Forge Prompt")

        left_layout.addWidget(self.prompt_title)
        left_layout.addWidget(self.prompt_body, 1)
        left_layout.addWidget(self.btn_generate)

        # Right pane: preview
        self.preview = QWebEngineView()
        self.preview.page().setBackgroundColor(QColor("#1e1e1e"))

        container_layout.addWidget(self.left_panel, 0)
        container_layout.addWidget(self.preview, 1)
        main_layout.addWidget(self.container)
        self.setCentralWidget(main_widget)

        # ---------------------------------------------------------------------
        # Overlays + subtle animations
        # ---------------------------------------------------------------------
        self.spark_overlay = ParticleBurst(self.container)
        self.spark_overlay.hide()

        # Subtle press pulse for all buttons in this window
        self._pulse_filter = ButtonPulseFilter(self)
        for btn in self.findChildren(QtWidgets.QPushButton):
            btn.installEventFilter(self._pulse_filter)

        # Signals
        self.tabbar.currentChanged.connect(self._tab_changed)
        QTimer.singleShot(0, lambda: self._move_tab_indicator(self.tabbar.currentIndex()))
        self.btn_generate.clicked.connect(self._on_generate_clicked)

        # Initial geometry
        self.setGeometry(120, 120, 980, 720)
        self.resizeEvent(None)

    # -------------------------------------------------------------------------
    # Tab underline animation
    # -------------------------------------------------------------------------
    def _move_tab_indicator(self, index: int):
        if index < 0:
            return
        rect = self.tabbar.tabRect(index)
        y = self.tabbar.height() - self._tab_indicator.height()
        target = QRect(rect.x(), y, rect.width(), self._tab_indicator.height())
        if not self._tab_indicator.geometry().isValid() or self._tab_indicator.geometry().isNull():
            self._tab_indicator.setGeometry(target)
            return
        self._tab_indicator_anim.stop()
        self._tab_indicator_anim.setStartValue(self._tab_indicator.geometry())
        self._tab_indicator_anim.setEndValue(target)
        self._tab_indicator_anim.start()

    def _tab_changed(self, idx: int):
        self._move_tab_indicator(idx)

    # -------------------------------------------------------------------------
    # Basic preview wiring (update right pane + a small spark effect)
    # -------------------------------------------------------------------------
    def _on_generate_clicked(self):
        title = self.prompt_title.text().strip() or "Untitled Prompt"
        body = self.prompt_body.toPlainText().strip().replace("\n", "<br>")
        html = f"""
            <html>
              <body style="background:#1e1e1e;color:#d0d0d0;font-family:'Segoe UI';">
                <h2 style="color:#00d0ff">{title}</h2>
                <div style="line-height:1.6">{body or '<i>(empty)</i>'}</div>
              </body>
            </html>
        """
        self.preview.setHtml(html)

        # Spark feedback over the right panel (relative to container)
        g = self.preview.geometry()
        origin = QtCore.QPoint(g.x() + g.width() // 2, g.y() + 10)
        self.spark_overlay.burst_at(origin, count=20)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.spark_overlay.setGeometry(self.container.rect())