# Nexus.py
"""
Main application shell for Letter Smith.

Nexus owns the frameless main window, tab navigation, preview surface,
help popover, Prompt Writer launcher, and cross-tab
signal wiring. Feature-specific work remains inside the individual tab modules:
Image_tab, sound_tab, Message_tab, Forge_Tab, and command.

The shell keeps the user-facing preview synchronized across tabs: images show
selected page art, Sound mounts its visualizer, Message displays message
previews, Forge shows the current cover/title, and Command hides the preview
during destructive reset actions.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl, QEvent, QSize, QPoint
from PySide6.QtGui import QColor, QPixmap, QIcon, QMouseEvent, QMovie, QFont
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QStatusBar, QLabel, QVBoxLayout, QHBoxLayout, QDialog,
    QFrame
)

# The Message preview uses Qt WebEngine to render authored HTML.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception as e:
    raise SystemExit(
        "Qt WebEngine is required for the HTML preview.\n"
        "Install it with:  pip install PySide6-Addons\n\n"
        f"Original error:\n{e}"
    )

from app_icon import apply_qt_window_icon, canonical_icon_paths
from config import MUSIC_FILE, USER_CONTROLS_DIR, USER_SOUNDS_DIR
from settings_store import SettingsStore

# Visual effects are centralized in anima.py; the shell can run without them.
try:
    from anima import ParticleBurst, TabSwitcher, install_click_fx
except Exception:
    ParticleBurst = None
    TabSwitcher = None
    def install_click_fx(_):  # type: ignore
        pass

# Shell-owned asset paths and viewport sizing.
REL_RETICLE_ICON = "gallery/app/icons/reticle.png"
REL_HELP_GIF = "gallery/app/icons/Help.gif"
REL_HELP_HOVER = "gallery/app/icons/HHelp.gif"
REL_HELP_PNG = "gallery/app/icons/Help.png"

WIN_W, WIN_H = 1400, 900
_PREVIEW_AR = 169 / 253
HELP_ICON_PX = 125

class _DoubleClickFilter(QtCore.QObject):
    """Routes double-clicks on the message preview into the full preview dialog."""
    def __init__(self, nexus: "Nexus"):
        super().__init__(nexus)
        self.nexus = nexus

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonDblClick:
            if isinstance(ev, QMouseEvent) and ev.button() == Qt.LeftButton:
                self.nexus._on_message_double_click()
                return True
        return False

class _HoverTabSwitchFilter(QtCore.QObject):
    """Activates a tab after the cursor rests on it briefly."""
    def __init__(self, tabbar: QtWidgets.QTabBar, delay_ms: int = 333):
        super().__init__(tabbar)
        self.tabbar = tabbar
        self._pending_index = -1
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._activate_pending_tab)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.tabbar:
            t = event.type()
            if t in (QEvent.MouseMove, QEvent.HoverMove, QEvent.Enter, QEvent.HoverEnter):
                pos = self._event_pos(event)
                if pos is not None:
                    self._schedule_index(self.tabbar.tabAt(pos))
            elif t in (QEvent.Leave, QEvent.HoverLeave):
                self._cancel()
        return super().eventFilter(watched, event)

    def _event_pos(self, event: QtCore.QEvent) -> Optional[QtCore.QPoint]:
        if hasattr(event, "position"):
            return event.position().toPoint()
        if hasattr(event, "pos"):
            return event.pos()
        return self.tabbar.mapFromGlobal(QtGui.QCursor.pos())

    def _schedule_index(self, index: int) -> None:
        if index < 0 or index == self.tabbar.currentIndex():
            self._cancel()
            return
        if index != self._pending_index:
            self._pending_index = index
            self._timer.start()

    def _cancel(self) -> None:
        self._pending_index = -1
        self._timer.stop()

    def _activate_pending_tab(self) -> None:
        index = self._pending_index
        cursor_index = self.tabbar.tabAt(self.tabbar.mapFromGlobal(QtGui.QCursor.pos()))
        self._cancel()
        if index >= 0 and index == cursor_index and index != self.tabbar.currentIndex():
            self.tabbar.setCurrentIndex(index)

class TitleBar(QtWidgets.QWidget):
    """Frameless title bar with app title, target launcher, and window controls."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        app_icon = QtWidgets.QLabel(self)
        app_icon.setObjectName("AppIcon")
        app_icon.setFixedSize(30, 30)
        app_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        png_path, _ = canonical_icon_paths(self.parent.project_root)
        pixmap = QPixmap(str(png_path))
        if not pixmap.isNull():
            app_icon.setPixmap(
                pixmap.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            layout.addWidget(app_icon)

        title = QtWidgets.QLabel("The Silver-Tongued Lettersmith", self)
        title.setStyleSheet("color:#00ffff; font:16px 'Segoe UI Semibold'; letter-spacing:1px;")
        layout.addWidget(title)
        layout.addStretch()

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

        btn_min = QtWidgets.QPushButton("–")
        btn_min.setFixedSize(32, 32)
        btn_min.setStyleSheet(
            "QPushButton{color:#ccc; background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(0,255,255,0.15);}"
        )
        btn_min.clicked.connect(self.parent.showMinimized)
        layout.addWidget(btn_min)

        self.btn_max = QtWidgets.QPushButton("□")
        self.btn_max.setFixedSize(32, 32)
        self.btn_max.setStyleSheet(
            "QPushButton{color:#ccc; background:transparent; border:none; padding:0;}"
            "QPushButton:hover{background:rgba(0,255,255,0.15);}"
        )
        self.btn_max.clicked.connect(self._toggle_max_restore)
        layout.addWidget(self.btn_max)

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

class HelpPopover(QFrame):
    """Floating help card positioned beside the active tab help icon."""
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("HelpPopover")
        self.setVisible(False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setWindowFlag(Qt.ToolTip, True)

        self._fixed_width = 420

        self.setMinimumWidth(self._fixed_width)
        self.setMaximumWidth(self._fixed_width)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(8)
        lay.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)

        self.header = QLabel("")
        self.header.setObjectName("HelpHeader")
        self.header.setTextFormat(Qt.PlainText)
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setWordWrap(True)
        self.header.setAutoFillBackground(False)
        self.header.setAttribute(Qt.WA_NoSystemBackground, True)
        self.header.setTextInteractionFlags(Qt.NoTextInteraction)
        self.header.setFixedWidth(self._fixed_width - 28)

        hf = QFont("Segoe UI Semibold", 14)
        hf.setBold(True)
        self.header.setFont(hf)

        self.body = QLabel("")
        self.body.setObjectName("HelpBody")
        self.body.setTextFormat(Qt.PlainText)
        self.body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.body.setWordWrap(True)
        self.body.setAutoFillBackground(False)
        self.body.setAttribute(Qt.WA_NoSystemBackground, True)
        self.body.setTextInteractionFlags(Qt.NoTextInteraction)
        self.body.setFixedWidth(self._fixed_width - 28)

        bf = QFont("Segoe UI", 10)
        bf.setBold(False)
        self.body.setFont(bf)

        lay.addWidget(self.header)
        lay.addWidget(self.body)

        self.setStyleSheet("""
            QFrame#HelpPopover {
                background: #2b2b2b;
                border: 2px solid #2f7474;
                border-radius: 8px;
            }

            QLabel#HelpHeader {
                background: transparent;
                border: none;
                color: #e0ffff;
                font-size: 14px;
                font-weight: 800;
                padding: 0;
                margin: 0;
            }

            QLabel#HelpBody {
                background: transparent;
                border: none;
                color: #e0ffff;
                font-size: 10px;
                font-weight: 400;
                padding: 0;
                margin: 0;
            }
        """)

    def set_header_text(self, text: str) -> None:
        self.header.setText(str(text or ""))
        self._stabilize_size()

    def set_help_text(self, body_text: str) -> None:
        self.body.setText(str(body_text or ""))
        self._stabilize_size()

    def _stabilize_size(self) -> None:
        """
        Keep the popover width stable so hover events, movie-frame swaps,
        and label repaints cannot make the body text reflow sideways.
        """
        self.header.setFixedWidth(self._fixed_width - 28)
        self.body.setFixedWidth(self._fixed_width - 28)
        self.adjustSize()
        self.resize(self._fixed_width, self.sizeHint().height())

    def popup_at(self, anchor_global: QPoint, prefer_left: bool, parent: QtWidgets.QWidget, icon_px: int = HELP_ICON_PX):
        """
        Place the popover adjacent to the help icon.

        anchor_global: icon center in GLOBAL coords.
        If this widget is a top-level (Qt.ToolTip), move in GLOBAL coords.
        If it is a child widget, move in PARENT-LOCAL coords.
        """
        self._stabilize_size()

        margin = 8
        is_tooltip = bool(self.windowFlags() & Qt.ToolTip)

        if is_tooltip:
            try:
                win = parent.window().windowHandle()
                screen = win.screen() if win else QtGui.QGuiApplication.primaryScreen()
            except Exception:
                screen = QtGui.QGuiApplication.primaryScreen()
            sgeo = screen.availableGeometry() if screen else QtGui.QGuiApplication.primaryScreen().availableGeometry()

            x_right_g = anchor_global.x() + (icon_px // 2) + margin
            x_left_g = anchor_global.x() - (icon_px // 2) - margin - self.width()

            xg = x_right_g
            if prefer_left or (x_right_g + self.width() > sgeo.right() - margin):
                xg = max(sgeo.left() + margin, x_left_g)

            yg = max(sgeo.top() + margin, anchor_global.y() - (self.height() // 2))
            if yg + self.height() > sgeo.bottom() - margin:
                yg = sgeo.bottom() - margin - self.height()

            self.move(xg, yg)
        else:
            anchor_local = parent.mapFromGlobal(anchor_global)

            x_right = anchor_local.x() + (icon_px // 2) + margin
            x_left = anchor_local.x() - (icon_px // 2) - margin - self.width()
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

class Nexus(QtWidgets.QMainWindow):
    """Top-level window that coordinates Letter Smith tabs and shared preview state."""
    def __init__(self, project_root: str | Path):
        super().__init__()
        self.project_root = str(project_root)
        self._shutdown = False
        self._child_processes: list[subprocess.Popen] = []
        self.setWindowTitle("Letter Smith")
        apply_qt_window_icon(self, self.project_root)

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

        main_widget = QtWidgets.QWidget(self)
        main_layout = QtWidgets.QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = TitleBar(self)
        main_layout.addWidget(self.title_bar)

        self.tabbar = QtWidgets.QTabBar()
        for name in ("Images", "Sound", "Message", "Forge", "Command"):
            self.tabbar.addTab(name)
        self.tabbar.setDrawBase(False)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.setMouseTracking(True)
        self.tabbar.setAttribute(Qt.WA_Hover, True)
        self._hover_tab_switch = _HoverTabSwitchFilter(self.tabbar)
        self.tabbar.installEventFilter(self._hover_tab_switch)
        main_layout.addWidget(self.tabbar)

        self.body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        self.preview_frame = QtWidgets.QWidget()
        self.preview_frame.setObjectName("PreviewFrame")
        pf_layout = QVBoxLayout(self.preview_frame)
        pf_layout.setContentsMargins(6, 6, 6, 6)

        self.preview_stack = QtWidgets.QStackedWidget(self.preview_frame)
        self.preview_stack.addWidget(QtWidgets.QWidget())

        self.image_preview = QtWidgets.QLabel(alignment=Qt.AlignCenter)
        self.image_preview.setStyleSheet("background:transparent;")
        self.preview_stack.addWidget(self.image_preview)

        self.html_preview = QWebEngineView()
        self.html_preview.setStyleSheet("background-color:#1e1e1e;")
        self.html_preview.page().setBackgroundColor(QColor("#1e1e1e"))
        self.preview_stack.addWidget(self.html_preview)

        pf_layout.addWidget(self.preview_stack)
        body_layout.addWidget(self.preview_frame, alignment=Qt.AlignHCenter)

        self.preview_caption = QLabel("", alignment=Qt.AlignCenter)
        self.preview_caption.setVisible(False)
        self.preview_caption.setStyleSheet(
            "color:#e0ffff; font:12px 'Segoe UI Semibold'; padding:4px 6px;"
        )
        body_layout.addWidget(self.preview_caption, alignment=Qt.AlignHCenter)

        # Help icon and hover popover for the active feature tab.
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.setSpacing(0)
        help_row.addStretch(1)

        self.help_icon = QLabel()
        self.help_icon.setObjectName("HelpIcon")
        self.help_icon.setAutoFillBackground(False)
        self.help_icon.setAttribute(Qt.WA_NoSystemBackground, True)
        self.help_icon.setAttribute(Qt.WA_TranslucentBackground, True)
        self.help_icon.setStyleSheet("""
            QLabel#HelpIcon {
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }
        """)
        self.help_icon.setCursor(Qt.WhatsThisCursor)
        self.help_icon.setAccessibleName("Help — instructions for this tab")
        self.help_icon.setToolTip("Help")
        self.help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self.help_icon.setMouseTracking(True)
        self.help_icon.setAttribute(Qt.WA_Hover, True)

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
                    mv.start()
                    return mv
            return None

        self._help_movie_idle  = _load_movie(_abs(REL_HELP_GIF))
        self._help_movie_hover = _load_movie(_abs(REL_HELP_HOVER))

        # Prefer animated help assets; use the static PNG or text only if needed.
        if self._help_movie_idle:
            self.help_icon.setMovie(self._help_movie_idle)
        elif self._help_movie_hover:
            self.help_icon.setMovie(self._help_movie_hover)
        else:
            self._set_static_help_icon(_abs(REL_HELP_PNG))

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

        self.image_tab   = ImageTab(self.project_root)
        self.sound_tab   = SoundTab(self.project_root)
        self.message_tab = MessageTab(self.project_root)
        self.forge_tab   = ForgeTab(self.project_root)
        self.command_tab = CommandTab(self.project_root)
        for w in (self.image_tab, self.sound_tab, self.message_tab, self.forge_tab, self.command_tab):
            self.page_stack.addWidget(w)
        body_layout.addWidget(self.page_stack)

        self.forge_tab.letter_loaded.connect(lambda _payload=None: self._show_forge_preview())
        self.forge_tab.letter_loaded.connect(self._on_letter_loaded)
        self.forge_tab.fix_requested.connect(self._fix_readiness_item)
        self.forge_tab.preview_requested.connect(self._load_forge_preview)
        self.forge_tab.preview_restart_requested.connect(self._restart_forge_preview)
        self.forge_tab.preview_mute_requested.connect(self._mute_forge_preview)
        self.forge_tab.preview_report_requested.connect(self._report_forge_preview_assets)
        self.forge_tab.project_will_open.connect(self._prepare_workspace_audio_change)
        self.forge_tab.project_opened.connect(self._on_project_opened)
        try:
            self.forge_tab.letter_loaded.connect(lambda _payload=None: self.message_tab._sync_inputs_from_settings())
        except Exception:
            pass
        try:
            self.message_tab.published_page_url_changed.connect(self.forge_tab.set_saved_page_url)
        except Exception:
            pass

        main_layout.addWidget(self.body)
        self.setCentralWidget(main_widget)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._toast = QLabel(self)
        self._toast.setStyleSheet(
            "QLabel{background:rgba(0,0,0,0.7); color:#e0ffff; border-radius:6px; padding:8px 12px;}"
        )
        self._toast.setVisible(False)
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast.setVisible(False))

        self._project_autosave_timer = QtCore.QTimer(self)
        self._project_autosave_timer.setInterval(60000)
        self._project_autosave_timer.timeout.connect(self.forge_tab.autosave_project)
        self._project_autosave_timer.start()

        self._fade_timer: Optional[QtCore.QTimer] = None
        self._fade_anim: Optional[QtCore.QPropertyAnimation] = None

        self._spark = ParticleBurst(self.body) if ParticleBurst else None
        if self._spark:
            self._spark.setGeometry(self.body.rect())
            self._spark.hide()

        self._tabswitch = TabSwitcher(self.page_stack) if TabSwitcher else None

        self._sound_preview_widget: Optional[QtWidgets.QWidget] = None
        self._sound_preview_index: Optional[int] = None

        self._last_pixmap: Optional[QPixmap] = None

        self.image_tab.image_selected.connect(self._show_image)
        self.image_tab.hover_preview_image.connect(self._show_image)

        try:
            self.image_tab.clear_preview.connect(self._on_image_tab_clear_preview)
        except Exception:
            pass

        self.message_tab.preview_image.connect(self._show_image)
        self.message_tab.text_selected.connect(self._show_html)

        self.sound_tab.preview_widget.connect(self._mount_sound_preview)

        try:
            self.command_tab.wiped.connect(self._on_command_wiped)
        except Exception:
            pass

        try:
            self.message_tab.wall_file_selected.connect(self._on_wall_selected)
        except Exception:
            pass

        self._prompt_writer_win: Optional[QtWidgets.QWidget] = None

        self.setMinimumSize(1180, 820)
        self.resize(WIN_W, WIN_H)
        self.tabbar.setCurrentIndex(0)
        self._tab_changed(0)

        self._install_shortcuts()
        try:
            install_click_fx(self)
        except Exception:
            pass

        self._dbl_filter = _DoubleClickFilter(self)
        self.html_preview.installEventFilter(self._dbl_filter)

        self.status("Ready.")
        self.toast("Welcome to Letter Smith")

    def _install_shortcuts(self) -> None:
        """Register shell-level keyboard shortcuts."""
        # Ctrl+Alt+P opens Prompt Writer
        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+P"), self)
        sc.activated.connect(self.open_prompt_writer)

        # Ctrl+H toggles help popover
        sc2 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+H"), self)
        sc2.activated.connect(lambda: (self._show_help_from_icon() if not self.help_pop.isVisible() else self._hide_help_popover()))

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

    def _on_wall_selected(self, path: str) -> None:
        """Store Message-tab wall selections in ImageTab slot 4."""
        try:
            # ImageTab has set_image_path(slot, path)
            self.image_tab.set_image_path(4, path)
        except Exception:
            pass

    def _on_letter_loaded(self, _payload=None) -> None:
        """Refresh live audio after a transactional saved-letter load."""
        try:
            self.sound_tab.wave.release_current_file_handle()
            self.sound_tab._current_processed = ""
            self.sound_tab.refresh_from_workspace()
        except Exception:
            pass

    def _prepare_workspace_audio_change(self) -> None:
        try:
            self.sound_tab.wave.release_current_file_handle()
            self.sound_tab._preview.set_audio_file("")
        except Exception:
            pass

    def _on_project_opened(self, _payload=None) -> None:
        try:
            self.image_tab.refresh_from_workspace()
        except Exception:
            pass
        try:
            self.message_tab._sync_inputs_from_settings()
            self.message_tab._check_existing()
        except Exception:
            pass
        self._on_letter_loaded(_payload)
        try:
            self.forge_tab.refresh_saved_page_url()
            self.forge_tab.refresh_readiness()
        except Exception:
            pass

    def _fix_readiness_item(self, key: str) -> None:
        image_slots = {"cover": 1, "letter": 2, "wall": 3, "back": 4}
        if key in image_slots:
            self.tabbar.setCurrentIndex(0)
            QtCore.QTimer.singleShot(
                0, lambda slot=image_slots[key]: self.image_tab._pick_image_dialog(slot)
            )
            return
        if key == "music":
            self.tabbar.setCurrentIndex(1)
            QtCore.QTimer.singleShot(0, self.sound_tab.select_music)
            return
        if key in {"message", "recipient", "title"}:
            self.tabbar.setCurrentIndex(2)
            if key == "message":
                QtCore.QTimer.singleShot(0, self.message_tab.select_file)
            elif key == "recipient":
                QtCore.QTimer.singleShot(0, self.message_tab.name_input.setFocus)
            else:
                QtCore.QTimer.singleShot(0, self.message_tab.title_input.setFocus)
            return
        if key == "sfx":
            self.tabbar.setCurrentIndex(1)
            self.status("Add the missing app sound effects, then return to Forge.")
            return
        if key == "controls":
            controls = Path(self.project_root) / USER_CONTROLS_DIR
            controls.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(controls.resolve())))
            self.status("Add the missing viewer controls to the opened folder.")

    def _mount_sound_preview(self, widget: QtWidgets.QWidget) -> None:
        """Mount SoundTab's visualizer widget into the shared preview stack."""
        if widget is None:
            return
        try:
            if self._sound_preview_widget is widget and self._sound_preview_index is not None:
                return

            self._sound_preview_widget = widget
            self._sound_preview_index = self.preview_stack.addWidget(widget)

            if self.tabbar.currentIndex() == 1:
                self.preview_stack.setCurrentIndex(self._sound_preview_index)
        except Exception as ex:
            self.status(f"⚠️ Sound preview mount failed: {ex}")

    def _on_message_double_click(self):
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Message Preview")
            dlg.resize(900, 700)

            lay = QVBoxLayout(dlg)
            view = QWebEngineView(dlg)
            lay.addWidget(view)

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

    def _tab_changed(self, idx: int):
        """Switch the active feature page and rebuild the shared preview state."""
        # Switching tabs resets the shared preview before the active tab repopulates it.
        self._last_pixmap = None
        self._clear_preview()
        self.preview_stack.setCurrentIndex(0)
        self.preview_caption.setVisible(False)

        is_command = idx == 4
        self.preview_frame.setVisible(not is_command)
        self.help_icon.setVisible(not is_command)
        self._status.setVisible(not is_command)

        body_layout = self.body.layout()
        if body_layout is not None:
            margin = 0 if is_command else 12
            body_layout.setContentsMargins(margin, margin, margin, margin)
            body_layout.setSpacing(0 if is_command else 10)
            body_layout.activate()

        # The Prompt Writer FAB is window-parented, so visibility is controlled by tab.
        try:
            if hasattr(self, "image_tab") and hasattr(self.image_tab, "pwrite_fab"):
                show_fab = idx == 0
                self.image_tab.pwrite_fab.setVisible(show_fab)
                if show_fab:
                    self.image_tab.pwrite_fab.raise_()
        except Exception:
            pass

        if self._tabswitch:
            self._tabswitch.go_to(idx)
        else:
            self.page_stack.setCurrentIndex(idx)

        tab_name = self.tabbar.tabText(idx)
        self.status(f"Switched to: {tab_name}")

        # Each feature tab owns content generation; Nexus owns the shared preview surface.
        if idx == 0:
            self.preview_stack.setCurrentIndex(0)

        elif idx == 1:
            if self._sound_preview_index is not None:
                self.preview_stack.setCurrentIndex(self._sound_preview_index)
            else:
                self.preview_stack.setCurrentIndex(0)

        elif idx == 2:
            QtCore.QTimer.singleShot(0, self._request_message_preview)

        elif idx == 3:
            QtCore.QTimer.singleShot(0, self._show_forge_preview)

        else:
            self.preview_stack.setCurrentIndex(0)

        if self.help_pop.isVisible():
            self._refresh_help_text(idx)
            self._reposition_help_popover()

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
        """Redundancy: Command tab wiped data; also hard-clear any preview residue."""
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

    def _show_forge_preview(self) -> None:
        """Forge tab: show the finished viewer, or the cover before the first build."""
        index = self.forge_tab._current_play_index()
        if index is not None:
            self._load_forge_preview(str(index), self.forge_tab._preview_mode)
            return

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

    def _load_forge_preview(self, index_path: str, mode: str) -> None:
        index = Path(index_path)
        if not index.is_file():
            self.status("Finished viewer is missing. Generate the letter again.")
            return
        self._forge_preview_mode = mode
        self._resize_preview_frame()
        self._clear_preview()
        self.preview_caption.setVisible(False)
        self.html_preview.setUrl(QUrl.fromLocalFile(str(index.resolve())))
        self.html_preview.page().setAudioMuted(bool(self.forge_tab._preview_muted))
        self.preview_stack.setCurrentWidget(self.html_preview)
        self.status(f"Finished viewer preview: {mode.replace('-', ' ')}")

    def _restart_forge_preview(self) -> None:
        if self.preview_stack.currentWidget() is self.html_preview:
            self.html_preview.reload()

    def _mute_forge_preview(self, muted: bool) -> None:
        self.html_preview.page().setAudioMuted(bool(muted))

    def _report_forge_preview_assets(self) -> None:
        if self.preview_stack.currentWidget() is not self.html_preview:
            self.forge_tab.show_preview_report(["No finished viewer is loaded."])
            return
        missing_on_disk: list[str] = []
        index_path = Path(self.html_preview.url().toLocalFile())
        if index_path.is_file():
            try:
                sources = "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in (
                        index_path,
                        index_path.with_name("styles.css"),
                        index_path.with_name("script.js"),
                    )
                    if path.is_file()
                )
                referenced = {
                    match.split("?", 1)[0]
                    for match in re.findall(r"gallery/[A-Za-z0-9_./-]+", sources)
                    if Path(match.split("?", 1)[0]).suffix
                }
                missing_on_disk = sorted(
                    relative
                    for relative in referenced
                    if not (index_path.parent / Path(relative)).is_file()
                )
            except Exception:
                pass
        script = """
            (() => {
              const missing = [];
              document.querySelectorAll('img').forEach((asset) => {
                if (asset.complete && asset.naturalWidth === 0) {
                  missing.push(asset.getAttribute('src') || '[image without source]');
                }
              });
              document.querySelectorAll('audio').forEach((asset) => {
                if (asset.error) {
                  missing.push(asset.currentSrc || asset.getAttribute('src') || '[audio without source]');
                }
              });
              return [...new Set(missing)];
            })();
        """
        self.html_preview.page().runJavaScript(
            script,
            lambda browser_missing: self.forge_tab.show_preview_report(
                sorted(set(missing_on_disk + list(browser_missing or [])))
            ),
        )

    def _resize_preview_frame(self) -> None:
        mode = getattr(self, "_forge_preview_mode", "")
        available_h = max(180, int(self.height() * 0.35))
        if self.tabbar.currentIndex() == 3 and mode == "phone-portrait":
            h = available_h
            w = int(h * 9 / 16)
        elif self.tabbar.currentIndex() == 3 and mode in {"desktop", "phone-landscape"}:
            h = available_h
            w = int(h * 16 / 9)
        else:
            h = available_h
            w = int(h * _PREVIEW_AR)
        self.preview_frame.setFixedSize(max(160, w + 12), max(120, h + 12))

    def _read_project_title(self) -> str:
        """Read recipient_title from settings.json (best available 'project title' signal)."""
        try:
            data = SettingsStore(self.project_root).as_dict()
            return str(data.get("recipient_title", "")).strip()
        except Exception:
            pass
        return ""

    def resizeEvent(self, event):
        self._resize_preview_frame()

        if self._toast.isVisible():
            self.toast(self._toast.text(), ms=(self._toast_timer.remainingTime() or 800))

        if self._spark:
            self._spark.setGeometry(self.body.rect())

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

        if self.help_pop.isVisible():
            self._reposition_help_popover()

        super().resizeEvent(event)

    def moveEvent(self, event):
        # Keep help popover glued to the Help icon while the window moves
        if self.help_pop.isVisible():
            self._reposition_help_popover()
        super().moveEvent(event)

    def open_target_browser(self):
        """Launch target.py from the title bar reticle button."""
        try:
            script = os.path.join(self.project_root, "target.py")
            if not os.path.exists(script):
                self.status("❌ target.py not found")
                self.toast("target.py missing")
                return
            pos = QtGui.QCursor.pos()
            process = subprocess.Popen(
                [sys.executable, script, "--x", str(pos.x()), "--y", str(pos.y())],
                close_fds=True
            )
            self._child_processes.append(process)
            self.status("Target Browser opened.")
            self.toast("Target Browser launched")
        except Exception as ex:
            self.status(f"❌ Could not open Target Browser: {ex}")
            self.toast("Target launch failed")

    def open_prompt_writer(self):
        """
        Open Prompt Writer inline as an overlay panel (if available).
        The visible launcher button is owned by Image_tab.py; Ctrl+Alt+P also opens it.
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

        try:
            from PromptWriterPanel import PromptWriterPanel
            w = PromptWriterPanel(self)
            self._prompt_writer_win = w

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

        # External Prompter process is used only when the inline panel cannot be created.
        try:
            prompter = Path(self.project_root) / "Prompter" / "prompter.py"
            if prompter.exists():
                process = subprocess.Popen([sys.executable, str(prompter)], close_fds=True)
                self._child_processes.append(process)
                self.status("Prompt Writer launched (Prompter).")
                self.toast("Prompter launched")
                return
        except Exception as ex:
            print(f"[PromptWriter] Prompter launch failed: {ex}")

        self.status("❌ Prompt Writer could not be opened.")
        self.toast("Prompt Writer unavailable")

    def _set_static_help_icon(self, png_path: str):
        if os.path.exists(png_path):
            pm = QPixmap(png_path)
            if not pm.isNull():
                pm = pm.scaled(HELP_ICON_PX, HELP_ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.help_icon.setPixmap(pm)
                return
        self.help_icon.setText("Help")
        self.help_icon.setStyleSheet("""
            QLabel#HelpIcon {
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
                color: #8feaff;
                font-weight: 800;
            }
        """)

    def _refresh_help_text(self, idx: int):
        if idx == 0:
            header = "The Image Tab"
            body = ("Here you choose the three pictures that make up your letter. "
                    "The cover is the front image, the letter is the main picture inside, and the back is the closing image at the end. "
                    "Each time you select one, it appears in the preview so you can see exactly how it will look in the final letter.")
        elif idx == 1:
            header = "The Sound Tab"
            body = ("This tab lets you add background music to your letter. "
                    "Pick an MP3 file, and it will play while someone reads. "
                    "If you don’t want music, you can leave it empty and the letter will stay silent.")
        elif idx == 2:
            header = "The Message Tab"
            body = ("Here is where you load your words. Click Load Message File and select your note — "
                    "it can be a text file, a Word document, or a PDF. "
                    "The program will bring in up to a thousand words and display them in the preview so you can review the message before finishing.")
        elif idx == 3:
            header = "The Forge Tab"
            body = ("This is where you build the finished letter. "
                    "Click Generate to build the Play bundle and open it in your browser. "
                    "Click Seal the Letter to build the Play bundle and open the Play folder. "
                    "Use Load to bring an existing saved letter back into the editor.")
        else:
            header, body = "", ""

        self.help_pop.set_header_text(header)
        self.help_pop.set_help_text(body)

    def _reposition_help_popover(self):
        global_center = self.help_icon.mapToGlobal(self.help_icon.rect().center())
        prefer_left = True
        self.help_pop.popup_at(global_center, prefer_left, self.body, icon_px=HELP_ICON_PX)

    def _show_help_from_icon(self):
        idx = self.tabbar.currentIndex()
        if idx == 4:
            return
        self._refresh_help_text(idx)
        self._reposition_help_popover()

    def _hide_help_popover(self):
        self.help_pop.popdown()

    def shutdown(self) -> None:
        """Stop owned background work and release resources exactly once."""
        if self._shutdown:
            return
        self._shutdown = True

        prompt_writer = self._prompt_writer_win
        if isinstance(prompt_writer, QtWidgets.QWidget):
            try:
                if hasattr(prompt_writer, "shutdown"):
                    prompt_writer.shutdown()  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                prompt_writer.close()
            except Exception:
                pass
        self._prompt_writer_win = None

        if hasattr(self, "sound_tab") and hasattr(self.sound_tab, "shutdown"):
            try:
                self.sound_tab.shutdown()
            except Exception:
                pass

        try:
            self.forge_tab.autosave_project()
        except Exception:
            pass

        for timer in self.findChildren(QtCore.QTimer):
            timer.stop()
        for animation in self.findChildren(QtCore.QAbstractAnimation):
            animation.stop()

        for movie in (self._help_movie_idle, self._help_movie_hover):
            if movie is not None:
                movie.stop()
        try:
            self.help_icon.setMovie(None)
        except Exception:
            pass
        try:
            self.help_pop.close()
        except Exception:
            pass

        try:
            self.html_preview.stop()
            self.html_preview.setUrl(QUrl("about:blank"))
        except Exception:
            pass

        for process in self._child_processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
            except Exception:
                pass
        self._child_processes.clear()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        icon = getattr(self, "help_icon", None)
        pop  = getattr(self, "help_pop", None)

        if icon is not None and watched is icon:
            t = event.type()
            if t in (QEvent.Enter, QEvent.HoverEnter):
                self._help_hide_timer.stop()
                self._help_show_timer.start()
                if self._help_movie_hover:
                    self.help_icon.setMovie(self._help_movie_hover)
            elif t in (QEvent.Leave, QEvent.HoverLeave):
                self._help_show_timer.stop()
                self._help_hide_timer.start()
                if self._help_movie_idle:
                    self.help_icon.setMovie(self._help_movie_idle)

        elif pop is not None and watched is pop:
            t = event.type()
            if t == QEvent.Enter:
                self._help_hide_timer.stop()
            elif t == QEvent.Leave:
                self._help_hide_timer.start()

        return super().eventFilter(watched, event)
