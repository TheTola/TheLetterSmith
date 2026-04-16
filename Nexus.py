# ===============================
# File: Nexus.py
# ===============================
"""
Nexus — main shell for Letter Smith
Clean placement • Robust overlay • Sound visualizer • NO Prompt Writer button
+ Help.gif (idle, plays constantly) swaps to HHelp.gif on hover
+ Per-tab Help popover header: ✨The Image tab✨ / ✨The sound tab✨ / ✨The message tab✨ / ✨The forge tab✨

Notes
- If Qt WebEngine is missing, we exit with a clear tip: pip install PySide6-Addons
- Animation helpers come from anima.py; we fall back safely if not found
"""

from __future__ import annotations

import os, sys, subprocess, json
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────
# Overlay integration (robust, guarded)
# ─────────────────────────────────────────────────────────────
try:
    from Over_Nexus import install_over_nexus, Config as OverConfig
except Exception:
    install_over_nexus = None  # type: ignore
    OverConfig = None          # type: ignore

# ─────────────────────────────────────────────────────────────
# Qt
# ─────────────────────────────────────────────────────────────
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl, QEvent, QSize, QPoint
from PySide6.QtGui import QColor, QPixmap, QIcon, QMouseEvent, QMovie, QFont
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QStatusBar, QLabel, QVBoxLayout, QHBoxLayout, QDialog,
    QDialogButtonBox, QPushButton, QFrame
)

# WebEngine (used for HTML preview)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception as e:
    raise SystemExit(
        "Qt WebEngine is required for the HTML preview.\n"
        "Install it with:  pip install PySide6-Addons\n\n"
        f"Original error:\n{e}"
    )

# Animations / FX (from anima.py)
try:
    from anima import ParticleBurst, TabSwitcher, install_click_fx
except Exception:
    ParticleBurst = None
    TabSwitcher = None
    def install_click_fx(_):  # type: ignore
        pass

# ─────────────────────────────────────────────────────────────
# Relative asset hints & sizing
# ─────────────────────────────────────────────────────────────

REL_RETICLE_ICON = "gallery/app/icons/reticle.png"   # optional (title bar icon)
REL_HELP_GIF     = "gallery/app/icons/Help.gif"      # idle (plays constantly)
REL_HELP_HOVER   = "gallery/app/icons/HHelp.gif"     # hover variant (plays on hover)
REL_HELP_PNG     = "gallery/app/icons/Help.png"      # final static fallback

WIN_W, WIN_H = 1400, 900
_PREVIEW_AR = 169 / 253  # preview frame aspect (matches your 169×253 scaling)

# Help icon display size
HELP_ICON_PX = 125
HELP_ICON_HALF = HELP_ICON_PX // 2


# ─────────────────────────────────────────────────────────────
# Event filter for message double-click on QWebEngineView
# ─────────────────────────────────────────────────────────────
class _DoubleClickFilter(QtCore.QObject):
    def __init__(self, nexus: "Nexus"):
        super().__init__(nexus)
        self.nexus = nexus

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonDblClick:
            if isinstance(ev, QMouseEvent) and ev.button() == Qt.LeftButton:
                self.nexus._on_message_double_click()
                return True
        return False


# ─────────────────────────────────────────────────────────────
# Custom Title Bar (frameless) — Target button opens target.py
# ─────────────────────────────────────────────────────────────
class TitleBar(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        title = QtWidgets.QLabel("The Silver-Tongued Lettersmith", self)
        title.setStyleSheet("color:#00ffff; font:16px 'Segoe UI Semibold'; letter-spacing:1px;")
        layout.addWidget(title)
        layout.addStretch()

        # Target (reticle) button
        btn_target = QtWidgets.QPushButton()
        btn_target.setFixedSize(32, 32)
        btn_target.setToolTip("Open Target Browser")
        ico = os.path.join(self.parent.project_root, REL_RETICLE_ICON)
        if os.path.exists(ico):
            btn_target.setIcon(QIcon(ico))
            btn_target.setIconSize(QSize(24, 24))
        else:
            btn_target.setText("＋")
        btn_target.setStyleSheet(
            "QPushButton{background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(0,0,0,0.0);}"
        )
        btn_target.clicked.connect(self.parent.open_target_browser)
        layout.addWidget(btn_target)

        # Minimize
        btn_min = QtWidgets.QPushButton("–")
        btn_min.setFixedSize(32, 32)
        btn_min.setStyleSheet(
            "QPushButton{color:#ccc; background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(0,255,255,0.15);}"
        )
        btn_min.clicked.connect(self.parent.showMinimized)
        layout.addWidget(btn_min)

        # Max/Restore
        self.btn_max = QtWidgets.QPushButton("□")
        self.btn_max.setFixedSize(32, 32)
        self.btn_max.setStyleSheet(
            "QPushButton{color:#ccc; background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(0,255,255,0.15);}"
        )
        self.btn_max.clicked.connect(self._toggle_max_restore)
        layout.addWidget(self.btn_max)

        # Close
        btn_close = QtWidgets.QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setStyleSheet(
            "QPushButton{color:#ccc; background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(255,0,0,0.25);}"
        )
        btn_close.clicked.connect(self.parent.close)
        layout.addWidget(btn_close)

        self._drag_start = QtCore.QPoint()

    def _toggle_max_restore(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.parent.isMaximized():
            delta = event.globalPosition().toPoint() - self._drag_start
            self.parent.move(self.parent.pos() + delta)
            self._drag_start = event.globalPosition().toPoint()


# ─────────────────────────────────────────────────────────────
# Small hover-help popover (top-level tooltip window)
# ─────────────────────────────────────────────────────────────
class HelpPopover(QFrame):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("HelpPopover")
        self.setVisible(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.ToolTip, True)  # native tooltip stacking behavior

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)

        self.header = QLabel("")  # dynamic per-tab header
        hf = QFont("Segoe UI Semibold", 11)
        self.header.setFont(hf)
        self.header.setWordWrap(True)

        self.body = QLabel("")
        bf = QFont("Segoe UI", 10)
        self.body.setFont(bf)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(self.header)
        lay.addWidget(self.body)

        self.setStyleSheet("""
            QFrame#HelpPopover {
                background: #2b2b2b;
                border: 2px solid #2f7474;
                border-radius: 8px;
            }
            QFrame#HelpPopover QLabel {
                color: #e0ffff;
            }
        """)

    def set_header_text(self, text: str) -> None:
        self.header.setText(text or "")
        self.header.adjustSize()
        self.adjustSize()

    def set_help_text(self, body_text: str) -> None:
        self.body.setText(body_text or "")
        self.body.adjustSize()
        self.adjustSize()

    def popup_at(self, anchor_global: QPoint, prefer_left: bool, parent: QtWidgets.QWidget, icon_px: int = HELP_ICON_PX):
        """
        Place the popover adjacent to the help icon.

        anchor_global: icon center in GLOBAL coords.
        If this widget is a top-level (Qt.ToolTip), we must move in GLOBAL coords.
        If it's a child widget, we move in PARENT-LOCAL coords.
        """
        self.adjustSize()
        width = min(420, max(320, self.sizeHint().width()))
        self.resize(width, self.sizeHint().height())

        margin = 8
        is_tooltip = bool(self.windowFlags() & Qt.ToolTip)

        if is_tooltip:
            # Top-level tooltip: use GLOBAL coords; clamp to the window's screen
            try:
                win = parent.window().windowHandle()
                screen = win.screen() if win else QtGui.QGuiApplication.primaryScreen()
            except Exception:
                screen = QtGui.QGuiApplication.primaryScreen()
            sgeo = screen.availableGeometry() if screen else QtGui.QGuiApplication.primaryScreen().availableGeometry()

            x_right_g = anchor_global.x() + (icon_px // 2) + margin
            x_left_g  = anchor_global.x() - (icon_px // 2) - margin - self.width()

            xg = x_right_g
            if prefer_left or (x_right_g + self.width() > sgeo.right() - margin):
                xg = max(sgeo.left() + margin, x_left_g)

            yg = max(sgeo.top() + margin, anchor_global.y() - (self.height() // 2))
            if yg + self.height() > sgeo.bottom() - margin:
                yg = sgeo.bottom() - margin - self.height()

            self.move(xg, yg)
        else:
            # Child widget: position in PARENT-LOCAL coords
            anchor_local = parent.mapFromGlobal(anchor_global)

            x_right = anchor_local.x() + (icon_px // 2) + margin
            x_left  = anchor_local.x() - (icon_px // 2) - margin - self.width()
            parent_rect = parent.rect()

            x = x_right
            if prefer_left or (x_right + self.width() > parent_rect.right() - margin):
                x = max(margin, x_left)

            y = max(margin, anchor_local.y() - (self.height() // 2))
            if y + self.height() > parent_rect.bottom() - margin:
                y = max(margin, parent_rect.bottom() - margin - self.height())

            self.move(x, y)

        self.setVisible(True)
        self.raise_()

    def popdown(self):
        self.setVisible(False)


# ─────────────────────────────────────────────────────────────
# Nexus Main Window
# ─────────────────────────────────────────────────────────────
class Nexus(QtWidgets.QMainWindow):
    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = str(project_root)


        # Frameless + QSS
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet("""
            QMainWindow, QWidget, QStackedWidget, QTabBar {
                background:#1e1e1e; color:#d0d0d0; font-family:'Segoe UI'; font-size:11px;
            }
            QLabel { color:#b0e0e6; font-weight:600; }
            QPushButton {
                background-color:transparent; color:#d0d0d0; border:1px solid #444;
                border-radius:4px; padding:6px 12px; font:11px 'Segoe UI';
            }
            QPushButton:hover { background:#2a2a2a; border-color:#00b2b2; color:#e0f7f7; }
            QPushButton:pressed{ background:#353535; border-color:#00a0a0; color:#c0f0f0; }

            QTabBar::tab {
                background:transparent; border:none; padding:8px 14px; margin-right:2px; color:#a0a0a0;
            }
            QTabBar::tab:selected { color:#e0fdfd; border-bottom:2px solid #00b2b2; }
            QTabBar::tab:hover    { color:#e0f7f7; }

            #PreviewFrame  { background:#2b2b2b; border:2px solid #444; border-radius:6px; }
        """)

        # Central layout
        main_widget = QtWidgets.QWidget(self)
        main_layout = QtWidgets.QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Title bar
        self.title_bar = TitleBar(self)
        main_layout.addWidget(self.title_bar)

        # Tab bar
        self.tabbar = QtWidgets.QTabBar()
        for name in ("Images", "Sound", "Message", "Forge", "Command"):
            self.tabbar.addTab(name)
        self.tabbar.setDrawBase(False)
        self.tabbar.currentChanged.connect(self._tab_changed)
        main_layout.addWidget(self.tabbar)

        # Body
        self.body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        # Preview frame (centered)
        self.preview_frame = QtWidgets.QWidget()
        self.preview_frame.setObjectName("PreviewFrame")
        pf_layout = QVBoxLayout(self.preview_frame)
        pf_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_stack = QtWidgets.QStackedWidget(self.preview_frame)
        self.preview_stack.addWidget(QtWidgets.QWidget())  # blank

        self.image_preview = QtWidgets.QLabel(alignment=Qt.AlignCenter)
        self.image_preview.setStyleSheet("background:transparent;")
        self.preview_stack.addWidget(self.image_preview)

        self.html_preview = QWebEngineView()
        self.html_preview.setStyleSheet("background-color:#1e1e1e;")
        self.html_preview.page().setBackgroundColor(QColor("#1e1e1e"))
        self.preview_stack.addWidget(self.html_preview)

        pf_layout.addWidget(self.preview_stack)
        body_layout.addWidget(self.preview_frame, alignment=Qt.AlignHCenter)

        # Caption under preview (Forge tab: project title)
        self.preview_caption = QLabel("", alignment=Qt.AlignCenter)
        self.preview_caption.setVisible(False)
        self.preview_caption.setStyleSheet(
            "color:#e0ffff; font:12px 'Segoe UI Semibold'; padding:4px 6px;"
        )
        body_layout.addWidget(self.preview_caption, alignment=Qt.AlignHCenter)

        # ─────────────────────────────────────────────────────────
        # Help (top-right above the feature panel) — dual-GIF swap
        # Idle = Help.gif (plays constantly), Hover = HHelp.gif
        # ─────────────────────────────────────────────────────────
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.setSpacing(0)
        help_row.addStretch(1)

        self.help_icon = QLabel()
        self.help_icon.setObjectName("HelpIcon")
        self.help_icon.setCursor(Qt.WhatsThisCursor)
        self.help_icon.setAccessibleName("Help — instructions for this tab")
        self.help_icon.setToolTip("Help")
        self.help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self.help_icon.setMouseTracking(True)
        self.help_icon.setAttribute(Qt.WA_Hover, True)

        # Movies: idle + hover
        self._help_movie_idle: Optional[QMovie] = None
        self._help_movie_hover: Optional[QMovie] = None

        def _abs(rel: str) -> str:
            return os.path.join(self.project_root, rel)

        def _load_movie(path: str) -> Optional[QMovie]:
            if os.path.exists(path):
                mv = QMovie(path)
                if mv.isValid():
                    mv.setCacheMode(QMovie.CacheAll)
                    mv.setSpeed(100)
                    mv.setScaledSize(QSize(HELP_ICON_PX, HELP_ICON_PX))
                    mv.start()  # play constantly
                    return mv
            return None

        self._help_movie_idle  = _load_movie(_abs(REL_HELP_GIF))
        self._help_movie_hover = _load_movie(_abs(REL_HELP_HOVER))

        # Initial visual: prefer idle movie → hover movie → PNG fallback → text
        if self._help_movie_idle:
            self.help_icon.setMovie(self._help_movie_idle)
        elif self._help_movie_hover:
            self.help_icon.setMovie(self._help_movie_hover)
        else:
            self._set_help_fallback_icon(_abs(REL_HELP_PNG))

        help_row.addWidget(self.help_icon, 0, Qt.AlignRight)
        body_layout.addLayout(help_row)

        # Help popover (top-level tooltip window)
        self.help_pop = HelpPopover(self.body)

        # Show/hide timers for hover UX
        self._help_show_timer = QtCore.QTimer(self)
        self._help_show_timer.setSingleShot(True)
        self._help_show_timer.setInterval(150)  # 120–160 ms
        self._help_show_timer.timeout.connect(self._show_help_from_icon)

        self._help_hide_timer = QtCore.QTimer(self)
        self._help_hide_timer.setSingleShot(True)
        self._help_hide_timer.setInterval(360)  # 300–400 ms
        self._help_hide_timer.timeout.connect(self._hide_help_popover)

        # Install event filters to manage hover persistence and GIF swap
        self.help_icon.installEventFilter(self)
        self.help_pop.installEventFilter(self)

        # Feature tabs
        self.page_stack = QtWidgets.QStackedWidget()
        try:
            from Image_tab import ImageTab
            from sound_tab import SoundTab
            from Message_tab import MessageTab
            from Forge_Tab import ForgeTab
            from command import CommandTab
        except Exception as ex:
            raise ImportError(f"Failed to import feature tabs: {ex}")

        self.image_tab   = ImageTab()
        self.sound_tab   = SoundTab(self.project_root)
        self.message_tab = MessageTab(self.project_root)
        self.forge_tab   = ForgeTab(self.project_root)
        self.command_tab = CommandTab(self.project_root)
        for w in (self.image_tab, self.sound_tab, self.message_tab, self.forge_tab, self.command_tab):
            self.page_stack.addWidget(w)
        body_layout.addWidget(self.page_stack)

        self.forge_tab.letter_loaded.connect(self._on_letter_loaded)

        main_layout.addWidget(self.body)
        self.setCentralWidget(main_widget)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # Toast overlay
        self._toast = QLabel(self)
        self._toast.setStyleSheet(
            "QLabel{background:rgba(0,0,0,0.7); color:#e0ffff; border-radius:6px; padding:8px 12px;}"
        )
        self._toast.setVisible(False)
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast.setVisible(False))

        # Preview fade state
        self._fade_timer: Optional[QtCore.QTimer] = None
        self._fade_anim: Optional[QtCore.QPropertyAnimation] = None

        # Overlay installer — show_prompter_launcher=False (no button)
        self._over = None
        if callable(install_over_nexus) and OverConfig is not None:
            try:
                self._over = install_over_nexus(
                    nexus=self,
                    project_root=self.project_root,
                    config=OverConfig(
                        TOPIC_FILE="topic.txt",
                        TYPE_FILE="type.txt",
                        COLOR_FILE="color.txt",
                        show_prompter_launcher=False,  # NO Prompt Writer button
                        require_seer=False
                    )
                )
            except Exception as ex:
                self._over = None
                print(f"[Overlay] WARNING: install_over_nexus failed: {ex}")

        self._connect_overlay_signals()

        # Optional spark overlay
        self._spark = ParticleBurst(self.body) if ParticleBurst else None
        if self._spark:
            self._spark.setGeometry(self.body.rect())
            self._spark.hide()

        # Optional tab switcher animations
        self._tabswitch = TabSwitcher(self.page_stack) if TabSwitcher else None

        # === Mounted Sound visualizer state ===
        self._sound_preview_widget: Optional[QtWidgets.QWidget] = None
        self._sound_preview_index: Optional[int] = None

        # Remember last image pixmap for proper re-scaling on resize
        self._last_pixmap: Optional[QPixmap] = None

        # Cross-tab preview wiring
        self.image_tab.image_selected.connect(self._show_image)
        self.image_tab.hover_preview_image.connect(self._show_image)

        # ImageTab reset -> clear preview immediately
        try:
            self.image_tab.clear_preview.connect(self._on_image_tab_clear_preview)
        except Exception:
            pass

        self.message_tab.preview_image.connect(self._show_image)
        self.message_tab.wall_preview.connect(self._show_image)
        self.message_tab.text_selected.connect(self._show_html)

        self.sound_tab.preview_movie.connect(self._show_movie)
        self.sound_tab.preview_widget.connect(self._mount_sound_preview)

        # Command: after wipe, hard-clear preview state (redundancy)
        try:
            self.command_tab.wiped.connect(self._on_command_wiped)
        except Exception:
            pass

        # Reflect wall image chosen in Message tab into Images tab as slot 4
        try:
            self.message_tab.wall_file_selected.connect(self._on_wall_selected)
        except Exception:
            pass

        # Keep a reference to Prompt Writer window if opened via shortcut
        self._prompt_writer_win: Optional[QtWidgets.QWidget] = None

        # Initial sizing & tab
        self.setMinimumSize(1180, 820)
        self.resize(WIN_W, WIN_H)
        self.tabbar.setCurrentIndex(0)
        self._tab_changed(0)

        # Shortcuts + click effects
        self._install_shortcuts()
        try:
            install_click_fx(self)
        except Exception:
            pass

        # Double-click filter for full message view
        self._dbl_filter = _DoubleClickFilter(self)
        self.html_preview.installEventFilter(self._dbl_filter)

        # Initial feedback
        self.status("Ready.")
        self.toast("Welcome to Letter Smith")

        # Diagnostics after event loop starts
        QtCore.QTimer.singleShot(0, self._post_init_diagnostics)

    # ─────────────────────────────────────────────────────────
    # Diagnostics: show key state once live
    # ─────────────────────────────────────────────────────────
    def _post_init_diagnostics(self) -> None:
        over = getattr(self, "_over", None)
        panel = getattr(over, "panel", None) if over is not None else None
        print(f"[Boot] Nexus visible={self.isVisible()} minimized={self.isMinimized()} "
              f"over_panel={'yes' if panel else 'no'}")

    # ─────────────────────────────────────────────────────────
    # Overlay signal plumbing (safe no-ops if overlay missing)
    # ─────────────────────────────────────────────────────────
    def _connect_overlay_signals(self) -> None:
        over = getattr(self, "_over", None)
        if over is None:
            return

        # If overlay has helper / wall buttons and we need to sync them, do it here.
        # Over_Nexus already wires these internally; this is kept for future add-ons.
        try:
            float_bar = getattr(over, "float_bar", None)
            if float_bar is not None and hasattr(self, "preview_frame"):
                self.preview_frame.installEventFilter(over)  # type: ignore[arg-type]
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # Shortcuts
    # ─────────────────────────────────────────────────────────
    def _install_shortcuts(self) -> None:
        # Ctrl+Alt+P opens prompt writer (no button)
        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+P"), self)
        sc.activated.connect(self.open_prompt_writer)

        # Ctrl+H toggles help popover
        sc2 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+H"), self)
        sc2.activated.connect(lambda: (self._show_help_from_icon() if not self.help_pop.isVisible() else self._hide_help_popover()))

    # ─────────────────────────────────────────────────────────
    # Status / Toast utilities
    # ─────────────────────────────────────────────────────────
    def status(self, msg: str) -> None:
        try:
            self._status.showMessage(msg, 5000)
        except Exception:
            pass

    def toast(self, msg: str, ms: int = 1200) -> None:
        if not msg:
            return
        try:
            self._toast.setText(msg)
            self._toast.adjustSize()
            # place top-center, below title bar
            x = max(10, (self.width() - self._toast.width()) // 2)
            y = 48
            self._toast.move(x, y)
            self._toast.setVisible(True)
            self._toast.raise_()
            self._toast_timer.start(ms)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # Wall chosen in Message tab → store in ImageTab slot 4 (source of truth)
    # ─────────────────────────────────────────────────────────
    def _on_wall_selected(self, path: str) -> None:
        try:
            # ImageTab has set_image_path(slot, path)
            self.image_tab.set_image_path(4, path)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # Sound preview mounting (widget-based visualizer)
    # ─────────────────────────────────────────────────────────
    def _mount_sound_preview(self, widget: QtWidgets.QWidget) -> None:
        if widget is None:
            return
        try:
            # Only mount once; replace if changed
            if self._sound_preview_widget is widget and self._sound_preview_index is not None:
                return

            self._sound_preview_widget = widget
            self._sound_preview_index = self.preview_stack.addWidget(widget)

            # If currently on Sound tab, show it now
            if self.tabbar.currentIndex() == 1:
                self.preview_stack.setCurrentIndex(self._sound_preview_index)
        except Exception as ex:
            self.status(f"⚠️ Sound preview mount failed: {ex}")

    def _show_movie(self, movie: QMovie):
        if movie is None:
            return
        try:
            self._clear_preview()
            self.image_preview.setMovie(movie)
            self.preview_stack.setCurrentWidget(self.image_preview)
            movie.start()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # Message double-click → full dialog preview
    # ─────────────────────────────────────────────────────────
    def _on_message_double_click(self):
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Message Preview")
            dlg.resize(900, 700)

            lay = QVBoxLayout(dlg)
            view = QWebEngineView(dlg)
            lay.addWidget(view)

            # Clone current html from main view
            url = getattr(self.message_tab, "last_preview_url", None)
            if isinstance(url, QUrl):
                view.setUrl(url)
            else:
                view.setHtml("<html><body style='background:#1e1e1e; color:#e0ffff; font-family:Segoe UI;'>"
                             "<h3>No preview URL available</h3></body></html>")

            self.status("Message preview opened.")
            dlg.exec()
        except Exception as ex:
            self.status(f"❌ Message preview failed: {ex}")

    # ─────────────────────────────────────────────────────────
    # Tabs — show correct preview per tab + update help visibility/content
    # ─────────────────────────────────────────────────────────
    def _tab_changed(self, idx: int):
        # Always hard-clear any previous preview when switching tabs.
        # (Sound tab is the only one that can intentionally re-mount its widget.)
        self._last_pixmap = None
        self._clear_preview()
        self.preview_stack.setCurrentIndex(0)  # blank
        self.preview_caption.setVisible(False)

        # Hide preview only on "Command" tab (index 4)
        self.preview_frame.setVisible(idx != 4)
        # Prompt Writer FAB: hide ONLY on Command tab
        try:
            if hasattr(self, "image_tab") and hasattr(self.image_tab, "pwrite_fab"):
                self.image_tab.pwrite_fab.setVisible(idx != 4)
                if idx != 4:
                    self.image_tab.pwrite_fab.raise_()
        except Exception:
            pass

        if self._tabswitch:
            self._tabswitch.go_to(idx)
        else:
            self.page_stack.setCurrentIndex(idx)

        tab_name = self.tabbar.tabText(idx)
        self.status(f"Switched to: {tab_name}")

        # Per-tab preview rules:
        # - Images: start blank; ImageTab will emit preview on hover/selection.
        # - Sound: show the mounted visualizer widget.
        # - Message: show current message state (message.png if present; else wall fallback).
        # - Forge: show cover.png + project title underneath.
        # - Command: preview hidden; still hard-cleared.
        if idx == 0:
            self.preview_stack.setCurrentIndex(0)  # blank

        elif idx == 1:
            if self._sound_preview_index is not None:
                self.preview_stack.setCurrentIndex(self._sound_preview_index)
            else:
                self.preview_stack.setCurrentIndex(0)

        elif idx == 2:
            # Let MessageTab decide what to show; it will emit preview_image.
            QtCore.QTimer.singleShot(0, self._request_message_preview)

        elif idx == 3:
            # Forge: show cover + title
            QtCore.QTimer.singleShot(0, self._show_forge_preview)

        else:
            self.preview_stack.setCurrentIndex(0)

        # Help: show on all tabs except Command
        self.help_icon.setVisible(idx != 4)
        if self.help_pop.isVisible():
            self._refresh_help_text(idx)
            self._reposition_help_popover()

    # ─────────────────────────────────────────────────────────
    # Preview rendering (image/html) + fade behavior
    # ─────────────────────────────────────────────────────────
    def _show_image(self, pixmap: QPixmap):
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return
        self._last_pixmap = pixmap
        self._clear_preview()
        scaled = pixmap.scaled(
            max(1, self.preview_frame.width() - 12),
            max(1, self.preview_frame.height() - 12),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_preview.setPixmap(scaled)
        self.preview_stack.setCurrentWidget(self.image_preview)
        # gentle fade after 30s idle
        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_preview)
        self._fade_timer.start(30000)

    def _show_html(self, html: str):
        self._clear_preview()
        self.html_preview.setHtml(html or "<p></p>")
        self.preview_stack.setCurrentWidget(self.html_preview)
        self.status("HTML preview updated.")

    def _fade_preview(self):
        effect = QGraphicsOpacityEffect(self.image_preview)
        self.image_preview.setGraphicsEffect(effect)
        self._fade_anim = QtCore.QPropertyAnimation(effect, b"opacity", self)
        self._fade_anim.setDuration(1000)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._clear_preview)
        self._fade_anim.start()

    def _on_image_tab_clear_preview(self) -> None:
        # Hard-clear: also drop last pixmap so resize can't resurrect it
        self._last_pixmap = None
        self._clear_preview()

    def _clear_preview(self):
        if hasattr(self, "_fade_timer") and self._fade_timer and self._fade_timer.isActive():
            self._fade_timer.stop()
        self._fade_timer = None
        if hasattr(self, "_fade_anim") and self._fade_anim and self._fade_anim.state() == QtCore.QPropertyAnimation.Running:
            self._fade_anim.stop()
        self._fade_anim = None
        eff = self.image_preview.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            self.image_preview.setGraphicsEffect(None)
        self.image_preview.clear()

    def _on_command_wiped(self) -> None:
        """Command tab wipe occurred.

        - Clear preview residue (existing behavior)
        - Clear Prompt Writer persistence so Prompt Writer resets on wipe (requested)
        """
        self._last_pixmap = None
        self._clear_preview()
        try:
            self.preview_stack.setCurrentIndex(0)
        except Exception:
            pass
        try:
            self.preview_caption.setVisible(False)
        except Exception:
            pass

        # Clear Prompt Writer persisted state
        try:
            from pathlib import Path

            root = Path(getattr(self, "project_root", "") or Path.cwd())
            state_path = root / "prompt_writer_state.json"
            if state_path.exists():
                try:
                    state_path.unlink()
                except Exception:
                    pass
        except Exception:
            pass


    def _request_message_preview(self) -> None:
        """Ask MessageTab to emit whichever preview it thinks is correct."""
        try:
            if hasattr(self.message_tab, "refresh_preview"):
                self.message_tab.refresh_preview()  # type: ignore[attr-defined]
                return
            if hasattr(self.message_tab, "_emit_best_preview"):
                self.message_tab._emit_best_preview()  # type: ignore[attr-defined]
                return
            if hasattr(self.message_tab, "_emit_preview"):
                self.message_tab._emit_preview()  # type: ignore[attr-defined]
                return
        except Exception:
            pass

    def _on_letter_loaded(self, payload=None) -> None:
        """When a saved Play build is loaded, push recipient/title into MessageTab UI and refresh preview."""
        try:
            name = ""
            title = ""
            if isinstance(payload, dict):
                name = str(payload.get("recipient_name", "")).strip()
                title = str(payload.get("recipient_title", "")).strip()

            # Fallback to disk if payload incomplete
            if not (name and title):
                try:
                    settings_path = Path(self.project_root) / "settings.json"
                    if settings_path.exists():
                        import json as _json
                        data = _json.loads(settings_path.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            name = name or str(data.get("recipient_name", "")).strip()
                            title = title or str(data.get("recipient_title", "")).strip()
                except Exception:
                    pass

            # Update MessageTab inputs live
            try:
                mt = getattr(self, "message_tab", None)
                if mt is not None:
                    if hasattr(mt, "title_input"):
                        mt.title_input.setText(title)
                    if hasattr(mt, "name_input"):
                        mt.name_input.setText(name)
                    if hasattr(mt, "_save_settings"):
                        mt._save_settings()
            except Exception:
                pass

            # Refresh Forge preview caption + image
            self._show_forge_preview()

            # Optional: refresh message preview if user is currently on Message tab
            try:
                if self.page_stack.currentWidget() is getattr(self, "message_tab", None):
                    self._request_message_preview()
            except Exception:
                pass

            try:
                if title or name:
                    self.toast(f"Loaded: {name} — {title}")
            except Exception:
                pass
        except Exception:
            # Never crash the UI from a load hook
            try:
                self._show_forge_preview()
            except Exception:
                pass

    def _show_forge_preview(self) -> None:
        """Forge tab: show cover.png and the project title underneath."""
        cover_path = os.path.join(self.project_root, "gallery", "user", "pages", "cover.png")
        pm = QPixmap(cover_path)
        if not pm.isNull():
            self._show_image(pm)
        else:
            self._last_pixmap = None
            self._clear_preview()
            self.preview_stack.setCurrentIndex(0)

        title_text = self._read_project_title()
        if title_text:
            self.preview_caption.setText(title_text)
            self.preview_caption.setVisible(True)
        else:
            self.preview_caption.setVisible(False)

    def _read_project_title(self) -> str:
        """Read recipient_title from settings.json (best available 'project title' signal)."""
        try:
            settings_path = os.path.join(self.project_root, "settings.json")
            if os.path.exists(settings_path):
                data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    t = str(data.get("recipient_title", "")).strip()
                    return t
        except Exception:
            pass
        return ""

    # ─────────────────────────────────────────────────────────
    # Resize/Move: keep preview aspect; keep popover aligned
    # ─────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        # Keep preview roughly 35% of window height; maintain 169:253 aspect
        h = int(self.height() * 0.35)
        w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

        # Reposition any visible toast
        if self._toast.isVisible():
            self.toast(self._toast.text(), ms=(self._toast_timer.remainingTime() or 800))

        # Cover the body with spark overlay if present
        if self._spark:
            self._spark.setGeometry(self.body.rect())

        # Rescale current image preview cleanly
        try:
            if self.preview_stack.currentWidget() is self.image_preview and self._last_pixmap and not self._last_pixmap.isNull():
                scaled = self._last_pixmap.scaled(
                    max(1, self.preview_frame.width() - 12),
                    max(1, self.preview_frame.height() - 12),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.image_preview.setPixmap(scaled)
        except Exception:
            pass

        # Keep help popover adjacent to the icon if visible
        if self.help_pop.isVisible():
            self._reposition_help_popover()

        super().resizeEvent(event)

    def moveEvent(self, event):
        # Keep help popover glued to the Help icon while the window moves
        if self.help_pop.isVisible():
            self._reposition_help_popover()
        super().moveEvent(event)

    # ─────────────────────────────────────────────────────────
    # Target Browser (title-bar button)
    # ─────────────────────────────────────────────────────────
    def open_target_browser(self):
        try:
            script = os.path.join(self.project_root, "target.py")
            if not os.path.exists(script):
                self.status("❌ target.py not found")
                self.toast("target.py missing")
                return
            pos = QtGui.QCursor.pos()
            subprocess.Popen(
                [sys.executable, script, "--x", str(pos.x()), "--y", str(pos.y())],
                close_fds=True
            )
            self.status("Target Browser opened.")
            self.toast("Target Browser launched")
        except Exception as ex:
            self.status(f"❌ Could not open Target Browser: {ex}")
            self.toast("Target launch failed")

    # ─────────────────────────────────────────────────────────
    # Prompt Writer opener (no visible button; shortcut only)
    # ─────────────────────────────────────────────────────────
    def open_prompt_writer(self):
        """
        Open Prompt Writer inline as an overlay panel (if available).
        No launcher button exists; this is only reachable via overlay request or Ctrl+Alt+P.
        """
        w = getattr(self, "_prompt_writer_win", None)
        if isinstance(w, QtWidgets.QWidget) and w.isVisible():
            try:
                if hasattr(w, "open_with_anim"):
                    w.open_with_anim()
                else:
                    w.show()
                w.raise_()
                w.activateWindow()
                self.status("Prompt Writer focused.")
                self.toast("Prompt Writer")
                return
            except Exception:
                self._prompt_writer_win = None

        # In-process overlay panel
        try:
            from PromptWriterPanel import PromptWriterPanel  # local module in project root
            w = PromptWriterPanel(self)                      # parent=self so it overlays inside the main window
            self._prompt_writer_win = w

            # Attempt to preload lists from Prompter/modules/ if present
            try:
                mods = Path(self.project_root) / "Prompter" / "modules"
                topic = mods / "topic.txt"
                typs  = mods / "type.txt"
                colr  = mods / "color.txt"
                if hasattr(w, "ui"):
                    w.ui.load_lists_from_files(
                        topic_path=str(topic if topic.exists() else "topic.txt"),
                        type_path=str(typs if typs.exists() else "type.txt"),
                        colors_path=str(colr if colr.exists() else "color.txt"),
                        add_none=True
                    )
            except Exception:
                pass

            try:
                w.open_with_anim()
            except Exception:
                try:
                    w.setGeometry(w._target_rect())  # type: ignore[attr-defined]
                except Exception:
                    w.setGeometry(self.body.geometry())
                w.show()
                w.raise_()

            try:
                w.closed.connect(lambda: setattr(self, "_prompt_writer_win", None))  # type: ignore[attr-defined]
            except Exception:
                pass

            self.status("Prompt Writer opened (inline).")
            self.toast("Prompt Writer")
            return

        except Exception as ex:
            print(f"[PromptWriter] In-process load failed: {ex}")

        # Last resort: launch Prompter app
        try:
            prompter = Path(self.project_root) / "Prompter" / "prompter.py"
            if prompter.exists():
                subprocess.Popen([sys.executable, str(prompter)], close_fds=True)
                self.status("Prompt Writer launched (Prompter).")
                self.toast("Prompter launched")
                return
        except Exception as ex:
            print(f"[PromptWriter] Prompter launch failed: {ex}")

        self.status("❌ Prompt Writer could not be opened.")
        self.toast("Prompt Writer unavailable")

    # ─────────────────────────────────────────────────────────
    # Help icon support
    # ─────────────────────────────────────────────────────────
    def _set_help_fallback_icon(self, png_path: str):
        if os.path.exists(png_path):
            pm = QPixmap(png_path)
            if not pm.isNull():
                pm = pm.scaled(HELP_ICON_PX, HELP_ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.help_icon.setPixmap(pm)
                return
        # Final fallback: small text
        self.help_icon.setText("Help")
        self.help_icon.setStyleSheet("QLabel{color:#8feaff; padding:2px 6px;}")

    def _refresh_help_text(self, idx: int):
        # Header (per tab)
        if idx == 0:
            header = "✨The Image tab✨"
            body = ("Here you choose the three pictures that make up your letter. "
                    "The cover is the front image, the letter is the main picture inside, and the back is the closing image at the end. "
                    "Each time you select one, it appears in the preview so you can see exactly how it will look in the final letter.")
        elif idx == 1:
            header = "✨The sound tab✨"
            body = ("This tab lets you add background music to your letter. "
                    "Pick an MP3 file, and it will play while someone reads. "
                    "If you don’t want music, you can leave it empty and the letter will stay silent.")
        elif idx == 2:
            header = "✨The message tab✨"
            body = ("Here is where you load your words. Click Load Message File and select your note — "
                    "it can be a text file, a Word document, or a PDF. "
                    "The program will bring in up to a thousand words and display them in the preview so you can review the message before finishing.")
        elif idx == 3:
            header = "✨The forge tab✨"
            body = ("This is where you build the finished letter. "
                    "If you want to preview it, click Generate and it will open in your browser. "
                    "To get the files directly, click Process Final and a folder will open with everything ready to use. "
                    "To create a neat package you can send, choose Seal the Letter and the program will create a zip file. "
                    "No matter which option you select, the app updates all outputs at the same time so everything stays current.")
        else:
            header, body = "", ""

        self.help_pop.set_header_text(header)
        self.help_pop.set_help_text(body)

    def _reposition_help_popover(self):
        # Place adjacent to the help icon; flip to left if near right edge
        global_center = self.help_icon.mapToGlobal(self.help_icon.rect().center())
        prefer_left = True  # prefer left so it doesn’t collide with right edge
        self.help_pop.popup_at(global_center, prefer_left, self.body, icon_px=HELP_ICON_PX)

    def _show_help_from_icon(self):
        idx = self.tabbar.currentIndex()
        if idx == 4:  # Command — hide help
            return
        self._refresh_help_text(idx)
        self._reposition_help_popover()

    def _hide_help_popover(self):
        self.help_pop.popdown()

    # ─────────────────────────────────────────────────────────
    # Global event filter for help hover (robust)
    # ─────────────────────────────────────────────────────────
    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Safe object lookups (avoid AttributeError if Qt routes to a different QObject)
        icon = getattr(self, "help_icon", None)
        pop  = getattr(self, "help_pop", None)

        if icon is not None and watched is icon:
            t = event.type()
            if t in (QEvent.Enter, QEvent.HoverEnter):
                self._help_hide_timer.stop()
                self._help_show_timer.start()
                # Swap to hover movie while over the icon
                if self._help_movie_hover:
                    self.help_icon.setMovie(self._help_movie_hover)
            elif t in (QEvent.Leave, QEvent.HoverLeave):
                self._help_show_timer.stop()
                # If we immediately entered the popover, it will cancel this timer
                self._help_hide_timer.start()
                # Swap back to idle movie when leaving icon
                if self._help_movie_idle:
                    self.help_icon.setMovie(self._help_movie_idle)

        elif pop is not None and watched is pop:
            t = event.type()
            if t == QEvent.Enter:
                self._help_hide_timer.stop()
            elif t == QEvent.Leave:
                self._help_hide_timer.start()

        return super().eventFilter(watched, event)
