# File: Over_Nexus.py
# Purpose: Prompt Writer overlay + Clarifier (Wall) tools for Nexus
# Highlights:
#   • Theme tokens (no hardcoded colors) w/ consistent focus/hover styles
#   • Style dropdown with non-selectable section headers (from Type.txt)
#   • Single bootstrapping point (install_over_nexus)
#   • Anchored floating bar over preview (no per-button .move choreography)
#   • CopyBox micro-feedback
#   • Only the hidable buttons visible by default (Wall + Helper) using PNG icons:
#       - gallery/icons/wallB.png
#       - gallery/icons/cperp.png
#   • Optional Prompt Writer launcher toggle via Config (default: OFF)

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QSize, QSizeF, QRect, QTimer
from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QFont,
    QTextDocument,
    QTextOption,
    QIcon,
    QStandardItemModel,
    QStandardItem,
    QBrush,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QToolButton,
    QCheckBox,
)

# Optional: DropButton from Image_tab (drag-and-drop). Falls back to QPushButton if absent.
try:
    from Image_tab import DropButton  # type: ignore
except Exception:
    DropButton = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Theme & Configuration
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Theme:
    """Design tokens for dark theme."""
    bg: str = "#1b1b1b"
    fg: str = "#e6e6e6"
    border: str = "#2c2c2c"
    widget_bg: str = "#222222"
    widget_border: str = "#444444"
    copybox_border: str = "#333333"
    accent: str = "#8feaff"
    focus_ring: str = "#6bdcff"
    hover_bg: str = "#2a2a2a"
    press_bg: str = "#353535"
    hover_border: str = "#00b2b2"
    press_border: str = "#00a0a0"
    muted: str = "#d0d0d0"
    muted_hover: str = "#e0f7f7"
    danger: str = "#ff6b6b"


@dataclass(frozen=True)
class Config:
    """
    Behavior and layout configuration. Flip flags here (or pass overrides to
    install_over_nexus) to adjust features without touching code below.
    """
    # Files (case-sensitive on Unix; we also fall back for color.txt)
    TOPIC_FILE: str = "Topic.txt"
    TYPE_FILE: str = "Type.txt"
    COLOR_FILE: str = "Color.txt"       # will fall back to "color.txt" if missing

    # Icons (relative to project_root)
    ICON_WALL: str = os.path.join("gallery", "icons", "wallB.png")
    ICON_HELPER: str = os.path.join("gallery", "icons", "cperp.png")
    ICON_PROMPTER_CANDIDATES: Tuple[str, ...] = (
        os.path.join("gallery", "icons", "pwrite.png"),
        os.path.join("gallery", "icons", "Pwrite.png"),
    )

    # Dimensions, margins
    ICON_SIZE: QSize = QSize(96, 96)
    BTN_SMALL: QSize = QSize(30, 30)
    PANEL_MARGIN: int = 8
    GROUP_PADS: Tuple[int, int, int, int] = (8, 6, 8, 6)
    MIN_INPUT_WIDTH: int = 220
    MIN_COPYBOX_WIDTH: int = 280

    # Rendering settings for message.png
    RENDER_W: int = 2048
    RENDER_H: int = 3072
    RENDER_MARGIN: int = 100
    RENDER_TEXT_TOP: int = 100
    RENDER_FONT_FAMILY: str = "Lucida Handwriting"
    RENDER_FONT_PT: int = 12

    # UI feedback
    COPY_FEEDBACK_MS: int = 800

    # Features
    show_prompter_launcher: bool = False   # <— stays False per request (only hidable buttons)
    require_seer: bool = False             # if True and Seer missing → raise; if False → skip panel


SETTINGS_FIRST_RUN_KEY = "over_nexus/first_run_done"


def build_stylesheet(theme: Theme) -> str:
    """Central stylesheet (consumes Theme tokens)."""
    t = theme
    return f"""
    QWidget#PM_Embedded, QWidget, QGroupBox {{
        background-color: {t.bg};
        color: {t.fg};
        font-family: 'Segoe UI';
    }}
    QGroupBox {{
        border: 1px solid {t.border};
        border-radius: 6px;
        margin-top: 12px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t.accent};
    }}
    QLabel#SectionHeader {{
        color: {t.accent};
        font-weight: 600;
    }}
    QPlainTextEdit, QLineEdit, QComboBox {{
        background: {t.widget_bg};
        color: {t.fg};
        border: 1px solid {t.widget_border};
        border-radius: 6px;
        padding: 6px;
    }}
    QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{
        outline: none;
        border-color: {t.focus_ring};
        box-shadow: 0 0 0 2px {t.focus_ring}22;
    }}
    QComboBox QAbstractItemView {{
        background: {t.widget_bg};
        color: {t.fg};
        border: 1px solid {t.widget_border};
    }}
    QWidget#CopyBox {{
        border: 1px solid {t.copybox_border};
        border-radius: 8px;
    }}
    QPushButton {{
        background: transparent;
        color: {t.muted};
        border: 1px solid {t.widget_border};
        border-radius: 6px;
        padding: 6px 10px;
    }}
    QPushButton:hover {{
        background: {t.hover_bg};
        border-color: {t.hover_border};
        color: {t.muted_hover};
    }}
    QPushButton:pressed {{
        background: {t.press_bg};
        border-color: {t.press_border};
    }}
    QPushButton[accent="true"] {{
        border-color: {t.accent};
        color: {t.accent};
    }}
    QPushButton[invalid="true"], QLineEdit[invalid="true"], QComboBox[invalid="true"] {{
        border-color: {t.danger};
    }}
    QToolButton#HelpButton {{
        border: 1px solid {t.widget_border};
        border-radius: 10px;
        padding: 0;
        width: 20px;
        height: 20px;
        color: {t.muted};
    }}
    QToolButton#HelpButton:hover {{
        background: {t.hover_bg};
        border-color: {t.hover_border};
        color: {t.muted_hover};
    }}
    QScrollArea {{ background: transparent; }}
    QFrame#PrompterPanel {{
        background: #2b2b2b;
        border: 2px solid {t.widget_border};
        border-radius: 10px;
    }}
    """


# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────────────────────

def read_list_file(path: Path) -> List[str]:
    """
    Read a simple list text file:
      - Skip blanks and #comments
      - Debullet "- " or "• "
      - Preserve order
    """
    out: List[str] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        elif s.startswith("• "):
            s = s[2:].strip()
        out.append(s)
    return out


def validate_options(items: Iterable[str]) -> List[str]:
    """Remove empties/dupes while preserving order."""
    seen = set()
    cleaned: List[str] = []
    for s in (i.strip() for i in items):
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    return cleaned


def ensure_options_or_warn(
    combo: QtWidgets.QComboBox,
    items: Sequence[str],
    label_widget: Optional[QtWidgets.QLabel],
    empty_message: str,
) -> None:
    """Load items into a combo; mark invalid + tooltip if list is empty."""
    combo.clear()
    if items:
        for it in items:
            combo.addItem(it)
        combo.setProperty("invalid", False)
        combo.setToolTip("")
        if label_widget:
            label_widget.setToolTip("")
    else:
        combo.addItem("")  # safe blank sentinel
        combo.setProperty("invalid", True)
        combo.setToolTip(empty_message)
        if label_widget:
            label_widget.setToolTip(empty_message)
    combo.style().unpolish(combo)
    combo.style().polish(combo)


def make_help_button(text: str) -> QToolButton:
    """Small inline '?' button that pops an explainer."""
    btn = QToolButton()
    btn.setObjectName("HelpButton")
    btn.setText("?")
    btn.setToolTip(text)
    btn.clicked.connect(lambda: QMessageBox.information(btn, "Help", text))
    return btn


# Style sections parsing/model (headers non-selectable)

def parse_types_with_sections(raw_lines: List[str]) -> List[tuple[str, bool]]:
    """
    Convert Type.txt lines into (text, is_header).
      Header: any non-empty line that does not start with "- "
      Item:   line starting with "- " → returned without "- "
      Blanks are ignored.
    """
    out: List[tuple[str, bool]] = []
    for raw in raw_lines:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("- "):
            out.append((s[2:].strip(), False))
        else:
            out.append((s, True))
    return out


def build_style_model(
    combo: QtWidgets.QComboBox,
    entries: List[tuple[str, bool]],
    accent_css_hex: str,
    placeholder_text: str = "— Select a style —",
) -> None:
    """
    Populate a QComboBox with headers (disabled, bold, accent color) and items.
    Also injects a disabled placeholder (index 0).
    """
    model = QStandardItemModel(combo)

    # Placeholder (disabled, shows when closed)
    ph = QStandardItem(placeholder_text)
    ph.setFlags(Qt.NoItemFlags)
    model.appendRow(ph)

    accent_brush = QBrush(QtGui.QColor(accent_css_hex))

    for text, is_header in entries:
        it = QStandardItem(text)
        if is_header:
            it.setFlags(Qt.NoItemFlags)  # not selectable
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setForeground(accent_brush)
        else:
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        model.appendRow(it)

    combo.setModel(model)
    combo.setCurrentIndex(0)
    combo.setEditable(False)


def _set_png_icon(
    btn: QtWidgets.QPushButton,
    abs_path: Optional[str],
    size: QSize,
    fallback_text: Optional[str] = None,
) -> None:
    """Apply PNG icon to a button; safe fallback if missing."""
    try:
        if abs_path and os.path.exists(abs_path):
            ic = QIcon(abs_path)
            btn.setIcon(ic)
            btn.setIconSize(size)
            btn.setText("")
            btn.setFlat(True)
            btn.setFixedSize(size)
        elif fallback_text is not None:
            btn.setText(fallback_text)
            btn.setFlat(True)
            btn.setFixedSize(max(size.width(), 24), max(size.height(), 24))
    except Exception:
        if fallback_text is not None:
            btn.setText(fallback_text)
            btn.setFlat(True)
            btn.setFixedSize(max(size.width(), 24), max(size.height(), 24))


def _make_drop_button(label: str, slot_index: int, parent: QtWidgets.QWidget) -> QtWidgets.QPushButton:
    """
    Construct a DropButton if available, otherwise a plain QPushButton.
    Label is unused (icons only) but kept for DropButton signature parity.
    """
    if DropButton is None:
        btn = QtWidgets.QPushButton(parent)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet("QPushButton{border:none; background:transparent;}")
        return btn
    try:
        return DropButton(label, slot_index, parent=parent)  # type: ignore[misc]
    except TypeError:  # signature mismatch guard
        btn = QtWidgets.QPushButton(parent)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFlat(True)
        btn.setStyleSheet("QPushButton{border:none; background:transparent;}")
        return btn


# ──────────────────────────────────────────────────────────────────────────────
# CopyBox (output editors with micro “Copied ✓” feedback)
# ──────────────────────────────────────────────────────────────────────────────

class ClickLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class CopyBox(QtWidgets.QWidget):
    def __init__(self, title: str, theme: Theme, cfg: Config, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CopyBox")
        self._theme = theme
        self._cfg = cfg

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)

        self.header = ClickLabel(title)
        self.header.setObjectName("SectionHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setToolTip("Click to copy this prompt")
        top.addWidget(self.header)
        top.addStretch()

        self.copy_btn = QtWidgets.QPushButton("Copy")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setFixedHeight(22)
        self.copy_btn.setMaximumWidth(64)
        top.addWidget(self.copy_btn)
        v.addLayout(top)

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setPlaceholderText("Type or edit the prompt here…")
        self.text.setReadOnly(False)
        self.text.setMinimumWidth(self._cfg.MIN_COPYBOX_WIDTH)
        self.text.setUndoRedoEnabled(True)
        self.text.document().setMaximumBlockCount(0)
        self.text.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        v.addWidget(self.text)

        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        self.header.clicked.connect(self._copy_to_clipboard)

    def _copy_to_clipboard(self) -> None:
        try:
            QtWidgets.QApplication.clipboard().setText(self.text.toPlainText())
            old = self.copy_btn.text()
            self.copy_btn.setText("Copied ✓")
            self.copy_btn.setProperty("accent", True)
            self.copy_btn.style().unpolish(self.copy_btn)
            self.copy_btn.style().polish(self.copy_btn)
            QTimer.singleShot(
                self._cfg.COPY_FEEDBACK_MS,
                lambda: (
                    self.copy_btn.setText(old),
                    self.copy_btn.setProperty("accent", False),
                    self.copy_btn.style().unpolish(self.copy_btn),
                    self.copy_btn.style().polish(self.copy_btn),
                ),
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Prompt Writer UI (embedded widget)
# ──────────────────────────────────────────────────────────────────────────────

class PromptMakerUIEmbedded(QtWidgets.QWidget):
    """Full Prompt Maker UI as a QWidget (no QMainWindow)."""
    DEFAULT_SUBJECT_SENTINEL = "----"

    def __init__(self, project_root: str, theme: Theme, cfg: Config, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project_root = project_root
        self.theme = theme
        self.cfg = cfg
        self.prompter_root = Path(self._detect_prompter_root())
        self.modules_dir = self.prompter_root / "modules"

        self.setObjectName("PM_Embedded")
        self.setStyleSheet(build_stylesheet(self.theme))
        self._init_ui()

    def _detect_prompter_root(self) -> str:
        for sp in sys.path:
            p = Path(sp)
            if p.name.lower() == "prompter" and (p / "modules").exists():
                return str(p.resolve())
        return str((Path(self.project_root) / "Prompter").resolve())

    def _init_ui(self) -> None:
        root = QtWidgets.QGridLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setHorizontalSpacing(10)
        root.setVerticalSpacing(8)
        root.setColumnStretch(0, 1)
        root.setColumnStretch(1, 1)

        # Left column
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        # SUBJECT
        gb_subject = QtWidgets.QGroupBox("Subject")
        hs = QtWidgets.QHBoxLayout(gb_subject)
        hs.setContentsMargins(*self.cfg.GROUP_PADS)

        self.subject_combo = QtWidgets.QComboBox()
        self.subject_combo.setEditable(True)
        self.subject_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.subject_combo.setMinimumWidth(self.cfg.MIN_INPUT_WIDTH)
        self.subject_combo.lineEdit().setPlaceholderText(self.DEFAULT_SUBJECT_SENTINEL)

        topics = validate_options(read_list_file(self.modules_dir / self.cfg.TOPIC_FILE))
        lbl_subj = QtWidgets.QLabel("Subject:")
        ensure_options_or_warn(
            self.subject_combo,
            [self.DEFAULT_SUBJECT_SENTINEL] + [t for t in topics if t != self.DEFAULT_SUBJECT_SENTINEL],
            lbl_subj,
            f"No subjects found. Add items to Prompter/modules/{self.cfg.TOPIC_FILE}",
        )
        hs.addWidget(lbl_subj)
        hs.addWidget(self.subject_combo, 1)
        lv.addWidget(gb_subject)
        self.sets_combo = self.subject_combo  # controller alias

        # STYLE (Type.txt) — sectioned, headers not selectable
        gb_style = QtWidgets.QGroupBox("Art Directions")
        hs2 = QtWidgets.QHBoxLayout(gb_style)
        hs2.setContentsMargins(*self.cfg.GROUP_PADS)

        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.setEditable(False)
        self.style_combo.setMinimumWidth(self.cfg.MIN_INPUT_WIDTH)

        type_path = self.modules_dir / self.cfg.TYPE_FILE
        raw_type_lines: List[str] = type_path.read_text(encoding="utf-8", errors="ignore").splitlines() if type_path.exists() else []
        sectioned = parse_types_with_sections(raw_type_lines)
        lbl_style = QtWidgets.QLabel("Graphic & illustration styles:")

        if not sectioned:
            ensure_options_or_warn(
                self.style_combo,
                [""],
                lbl_style,
                f"No styles found. Add items to Prompter/modules/{self.cfg.TYPE_FILE}",
            )
        else:
            build_style_model(
                self.style_combo,
                sectioned,
                accent_css_hex=self.theme.accent,
                placeholder_text="— Select a style —",
            )

        hs2.addWidget(lbl_style)
        hs2.addWidget(self.style_combo, 1)
        hs2.addWidget(make_help_button("Choose a broad visual direction. Headers aren’t selectable."))
        lv.addWidget(gb_style)

        # SCHEME (Color.txt with fallback to color.txt)
        gb_colors = QtWidgets.QGroupBox("Color Scheme")
        hc = QtWidgets.QHBoxLayout(gb_colors)
        hc.setContentsMargins(*self.cfg.GROUP_PADS)
        self.colors_combo = QtWidgets.QComboBox()
        self.colors_combo.setEditable(False)
        self.colors_combo.setMinimumWidth(self.cfg.MIN_INPUT_WIDTH)

        colors_raw = read_list_file(self.modules_dir / self.cfg.COLOR_FILE)
        if not colors_raw:
            colors_raw = read_list_file(self.modules_dir / "color.txt")  # fallback
        colors = validate_options(colors_raw)

        lbl_scheme = QtWidgets.QLabel("Scheme:")
        ensure_options_or_warn(
            self.colors_combo,
            ["(None)"] + [c for c in colors if c.lower() != "(none)"],
            lbl_scheme,
            f"No color schemes found. Add items to Prompter/modules/{self.cfg.COLOR_FILE}",
        )
        hc.addWidget(lbl_scheme)
        hc.addWidget(self.colors_combo, 1)
        hc.addWidget(make_help_button("Pick a palette guide. “(None)” leaves color unconstrained."))
        lv.addWidget(gb_colors)

        # VISION
        gb_common = QtWidgets.QGroupBox("Describe your vision for this letter")
        vb = QtWidgets.QVBoxLayout(gb_common)
        vb.setContentsMargins(*self.cfg.GROUP_PADS)
        self.common_edit = QtWidgets.QPlainTextEdit()
        self.common_edit.setPlaceholderText("Describe the shared concept, mood, palette, symbols…")
        self.common_edit.setFixedHeight(120)
        self.common_edit.setReadOnly(False)
        self.common_edit.setUndoRedoEnabled(True)
        self.common_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        vb.addWidget(self.common_edit)
        vb.addWidget(make_help_button("A single paragraph to set mood, motifs, and constraints for all images."))
        lv.addWidget(gb_common)

        # PER-IMAGE
        gb_specific = QtWidgets.QGroupBox("Per-image briefs")
        gl = QtWidgets.QGridLayout(gb_specific)
        gl.setContentsMargins(*self.cfg.GROUP_PADS)
        gl.setHorizontalSpacing(8)
        gl.setVerticalSpacing(6)

        self.cover_edit  = QtWidgets.QPlainTextEdit(placeholderText="Cover — front image focus")
        self.letter_edit = QtWidgets.QPlainTextEdit(placeholderText="Letter — main interior scene")
        self.back_edit   = QtWidgets.QPlainTextEdit(placeholderText="Back — closing image")
        self.wall_edit   = QtWidgets.QPlainTextEdit(placeholderText="Wall — soft background for text")
        for ed in (self.cover_edit, self.letter_edit, self.back_edit, self.wall_edit):
            ed.setFixedHeight(72)
            ed.setReadOnly(False)
            ed.setUndoRedoEnabled(True)
            ed.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

        gl.addWidget(QtWidgets.QLabel("Cover"),  0, 0); gl.addWidget(self.cover_edit,  0, 1)
        gl.addWidget(QtWidgets.QLabel("Letter"), 1, 0); gl.addWidget(self.letter_edit, 1, 1)
        gl.addWidget(QtWidgets.QLabel("Back"),   2, 0); gl.addWidget(self.back_edit,   2, 1)
        gl.addWidget(QtWidgets.QLabel("Wall"),   3, 0); gl.addWidget(self.wall_edit,   3, 1)
        gl.addWidget(make_help_button("Each field refines one output. Keep it short and concrete."), 4, 1, 1, 1, Qt.AlignRight)
        lv.addWidget(gb_specific)

        # OPTIONS
        gb_help = QtWidgets.QGroupBox("Helpful options")
        fh = QtWidgets.QGridLayout(gb_help)
        fh.setContentsMargins(*self.cfg.GROUP_PADS)

        self.cb_black  = QCheckBox("Thin black border")
        self.cb_white  = QCheckBox("Thin white border")
        self.cb_frame  = QCheckBox("Decorative frame")
        self.cb_forbid = QCheckBox("Forbid text/signatures/watermarks")
        self.cb_detail = QCheckBox("Preserve fine detail")
        self.cb_real   = QCheckBox("Bias toward photorealism")
        self.cb_paint  = QCheckBox("Bias toward painterly")
        self.cb_unify  = QCheckBox("Unify format across all outputs")

        fh.addWidget(QtWidgets.QLabel("Edges:"),          0, 0)
        fh.addWidget(self.cb_black,                       0, 1)
        fh.addWidget(self.cb_white,                       0, 2)
        fh.addWidget(self.cb_frame,                       0, 3)
        fh.addWidget(QtWidgets.QLabel("Policy/Detail:"),  1, 0)
        fh.addWidget(self.cb_forbid,                      1, 1)
        fh.addWidget(self.cb_detail,                      1, 2)
        fh.addWidget(QtWidgets.QLabel("Style bias:"),     2, 0)
        fh.addWidget(self.cb_real,                        2, 1)
        fh.addWidget(self.cb_paint,                       2, 2)
        fh.addWidget(self.cb_unify,                       3, 1, 1, 2)
        fh.addWidget(make_help_button("Quick constraints and nudges. Toggle as needed."), 3, 3, alignment=Qt.AlignRight)
        lv.addWidget(gb_help)

        # Right column
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        self.out_cover  = CopyBox("Cover prompt",  self.theme, self.cfg)
        self.out_letter = CopyBox("Letter prompt", self.theme, self.cfg)
        self.out_back   = CopyBox("Back prompt",   self.theme, self.cfg)
        self.out_wall   = CopyBox("Wall prompt",   self.theme, self.cfg)

        for w in (self.out_cover, self.out_letter, self.out_back, self.out_wall):
            sp = w.sizePolicy()
            sp.setVerticalStretch(1)
            sp.setVerticalPolicy(QtWidgets.QSizePolicy.Preferred)
            w.setSizePolicy(sp)
            rv.addWidget(w)
        rv.addStretch(1)

        left_scroll = QtWidgets.QScrollArea(); left_scroll.setWidgetResizable(True); left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); left_scroll.setWidget(left)

        right_scroll = QtWidgets.QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); right_scroll.setWidget(right)

        root.addWidget(left_scroll,  0, 0)
        root.addWidget(right_scroll, 0, 1)

        # Header controls (for panel header)
        self.title_line   = QtWidgets.QLineEdit(); self.title_line.setPlaceholderText("Title"); self.title_line.setMinimumWidth(self.cfg.MIN_INPUT_WIDTH)
        self.gen_btn      = QtWidgets.QPushButton("Generate")
        self.export_btn   = QtWidgets.QPushButton("Copy all")
        self.rand_all_btn = QtWidgets.QPushButton("Randomize")
        self.erase_btn    = QtWidgets.QPushButton("Erase all")
        for b in (self.gen_btn, self.export_btn, self.rand_all_btn, self.erase_btn):
            b.setCursor(Qt.PointingHandCursor)

    def make_top_controls_widget(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.title_line.setMinimumWidth(self.cfg.MIN_INPUT_WIDTH)
        h.addWidget(self.title_line, 1)
        h.addWidget(self.gen_btn)
        h.addWidget(self.export_btn)
        h.addWidget(self.rand_all_btn)
        h.addWidget(self.erase_btn)
        return w


# ──────────────────────────────────────────────────────────────────────────────
# Prompter Panel (overlay)
# ──────────────────────────────────────────────────────────────────────────────

class PrompterPanel(QtWidgets.QFrame):
    """Floating overlay panel."""
    closed = QtCore.Signal()

    def __init__(self, project_root: str, theme: Theme, cfg: Config, seer_factory: Callable[[PromptMakerUIEmbedded], object], parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("PrompterPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setVisible(False)
        self._theme = theme
        self._cfg = cfg

        self.setStyleSheet(build_stylesheet(theme))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        self.ui = PromptMakerUIEmbedded(project_root, theme, cfg, self)
        header_row.addWidget(self.ui.make_top_controls_widget(), 1)

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedWidth(34)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._on_close_clicked)
        header_row.addWidget(close_btn, 0, alignment=Qt.AlignRight)
        root.addLayout(header_row)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(self.ui)
        root.addWidget(scroll, 1)

        self.controller = seer_factory(self.ui)
        self._maybe_show_first_run()

    def _maybe_show_first_run(self) -> None:
        settings = QtCore.QSettings()
        if not settings.value(SETTINGS_FIRST_RUN_KEY, False, type=bool):
            QMessageBox.information(
                self, "Welcome",
                "Quick tour:\n\n1) Pick Subject\n2) Choose Art Direction & Scheme\n3) Add Vision & per-image briefs\n4) Generate → Copy."
            )
            settings.setValue(SETTINGS_FIRST_RUN_KEY, True)

    def popup(self) -> None:
        self._resize_to_container()
        self.setVisible(True)
        self.raise_()

    def popdown(self) -> None:
        self.setVisible(False)

    def toggle(self) -> None:
        self.popup() if not self.isVisible() else self.popdown()

    def _on_close_clicked(self) -> None:
        self.popdown()
        self.closed.emit()

    def _resize_to_container(self) -> None:
        parent = self.parent()
        if not parent:
            return
        arect = parent.rect()
        self.setGeometry(QRect(
            arect.x() + self._cfg.PANEL_MARGIN,
            arect.y() + self._cfg.PANEL_MARGIN,
            max(720, arect.width() - self._cfg.PANEL_MARGIN * 2),
            max(480, arect.height() - self._cfg.PANEL_MARGIN * 2),
        ))

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        if self.isVisible():
            self._resize_to_container()
        super().resizeEvent(e)


# ──────────────────────────────────────────────────────────────────────────────
# Floating overlay bar anchored to preview_frame
# ──────────────────────────────────────────────────────────────────────────────

class FloatingOverlayBar(QtWidgets.QWidget):
    """Transparent container anchored to the preview frame, with left/right slots."""
    def __init__(self, host: QtWidgets.QWidget):
        super().__init__(host)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setVisible(False)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)

        self.left_box = QtWidgets.QHBoxLayout()
        self.left_box.setContentsMargins(0, 0, 0, 0)
        self.left_box.setSpacing(6)

        self.right_box = QtWidgets.QHBoxLayout()
        self.right_box.setContentsMargins(0, 0, 0, 0)
        self.right_box.setSpacing(6)

        lay.addLayout(self.left_box, 0)
        lay.addStretch(1)
        lay.addLayout(self.right_box, 0)

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if not parent:
            return
        self.setGeometry(parent.rect())


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class OverNexusController(QtCore.QObject):
    """
    Wires Prompt Writer overlay + Clarifier into existing Nexus.
    Tabs assumed: Images=0, Sound=1, Message=2, Forge=3, Command=4.
    """
    def __init__(
        self,
        nexus: object,
        project_root: str,
        theme: Theme,
        cfg: Config,
        seer_factory: Optional[Callable[[PromptMakerUIEmbedded], object]],
    ):
        super().__init__(nexus)
        self.nexus = nexus
        self.project_root = project_root
        self.theme = theme
        self.cfg = cfg

        # Optional Prompt Writer launcher (per config; defaults OFF)
        self.prompter_btn: Optional[QtWidgets.QPushButton] = None
        self.panel: Optional[PrompterPanel] = None

        if self.cfg.show_prompter_launcher and seer_factory is not None:
            host = getattr(self.nexus, "container", None)
            self.prompter_btn = QtWidgets.QPushButton(host if isinstance(host, QtWidgets.QWidget) else None)
            self.prompter_btn.setCursor(Qt.PointingHandCursor)
            self.prompter_btn.setFlat(True)
            self.prompter_btn.setStyleSheet("QPushButton{border:none; background:transparent;}")

            # First icon found among candidates wins
            icon_path = None
            for rel in self.cfg.ICON_PROMPTER_CANDIDATES:
                cand = os.path.join(project_root, rel)
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
            self.prompter_btn.clicked.connect(self._toggle_prompter)

            # Panel constructed only if we expose the launcher
            container = getattr(self.nexus, "container", None)
            parent_widget = container if isinstance(container, QtWidgets.QWidget) else None
            self.panel = PrompterPanel(project_root, theme, cfg, seer_factory, parent_widget or QtWidgets.QWidget())
            self.panel.closed.connect(lambda: None)

        # Floating bar over preview (hosts only the hidable buttons)
        self.float_bar = FloatingOverlayBar(self.nexus.preview_frame)

        # Wall button (left)
        self.wall_btn = _make_drop_button("", 4, self.float_bar)
        self.wall_btn.setObjectName("wallButton")
        self.wall_btn.setToolTip("Set clarifier (text wall) image")
        _set_png_icon(
            self.wall_btn,
            abs_path=os.path.join(self.project_root, self.cfg.ICON_WALL),
            size=self.cfg.BTN_SMALL,
            fallback_text="🧱",
        )
        self.wall_btn.clicked.connect(self._select_wall_image)
        if hasattr(self.wall_btn, "file_dropped"):
            self.wall_btn.file_dropped.connect(lambda p: self.nexus.image_tab.set_image_path(4, p))  # type: ignore[attr-defined]
        if hasattr(self.wall_btn, "hovered"):
            self.wall_btn.hovered.connect(lambda _=None: getattr(self.nexus.image_tab, "preview_from_gallery", lambda *_: None)(4))  # type: ignore[attr-defined]
        self.float_bar.left_box.addWidget(self.wall_btn)

        # Helper button (right)
        self.helper_btn = QtWidgets.QPushButton("", self.float_bar)
        self.helper_btn.setToolTip("Toggle message helper")
        _set_png_icon(
            self.helper_btn,
            abs_path=os.path.join(self.project_root, self.cfg.ICON_HELPER),
            size=self.cfg.BTN_SMALL,
            fallback_text="🞪",
        )
        if hasattr(self.nexus.message_tab, "toggle_title_sister_area"):
            self.helper_btn.clicked.connect(self.nexus.message_tab.toggle_title_sister_area)
        else:
            self.helper_btn.setEnabled(False)
        self.float_bar.right_box.addWidget(self.helper_btn)

        # Tabs & events
        self.nexus.tabbar.currentChanged.connect(self._on_tab_changed)
        if hasattr(self.nexus.tabbar, "tabBarDoubleClicked"):
            self.nexus.tabbar.tabBarDoubleClicked.connect(self._on_tab_doubled)

        self.nexus.preview_frame.installEventFilter(self)
        self.float_bar.sync_geometry()
        self._on_tab_changed(self.nexus.tabbar.currentIndex())

    # Tab visibility logic
    def _on_tab_changed(self, idx: int) -> None:
        is_images = (idx == 0)
        is_message = (idx == 2)

        if self.prompter_btn:
            self.prompter_btn.setVisible(is_images)
            if not is_images and self.panel and self.panel.isVisible():
                self.panel.popdown()

        self.float_bar.setVisible(is_message)
        if not is_message and hasattr(self.nexus.message_tab, "title_sister_container"):
            self.nexus.message_tab.title_sister_container.setVisible(False)

    def _on_tab_doubled(self, index: int) -> None:
        if index == 2:
            self.float_bar.setVisible(not self.float_bar.isVisible())

    # Panel control
    def _toggle_prompter(self) -> None:
        if not self.panel:
            return
        self.panel.toggle()
        if self.panel.isVisible():
            self.panel.raise_()

    # Clarifier pipeline
    def _select_wall_image(self) -> None:
        """
        Pick a clarifier image; write slot 4; if Message tab has HTML,
        composite text into a fresh message.png and update thumbnail.
        """
        parent_widget = getattr(self.nexus, "container", None)
        if not isinstance(parent_widget, QtWidgets.QWidget):
            parent_widget = None

        path, _ = QFileDialog.getOpenFileName(
            parent_widget, "Select Clarifier (Text Wall) Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return

        # Update slot 4 (Clarifier/Wall)
        try:
            self.nexus.image_tab.set_image_path(4, path)
        except Exception:
            pass

        html = getattr(self.nexus.message_tab, "current_html", None)
        if html and str(html).strip():
            try:
                canvas = QImage(self.cfg.RENDER_W, self.cfg.RENDER_H, QImage.Format_ARGB32)
                canvas.fill(Qt.transparent)
                painter = QPainter(canvas)

                # Draw gallery wall background if present
                gallery_wall = os.path.join(self.project_root, 'gallery', 'wall.png')
                if os.path.exists(gallery_wall):
                    wall_img = QImage(gallery_wall).scaled(
                        self.cfg.RENDER_W, self.cfg.RENDER_H,
                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                    )
                    painter.drawImage(0, 0, wall_img)

                # Layout text
                doc = QTextDocument()
                doc.setDefaultFont(QFont(self.cfg.RENDER_FONT_FAMILY, self.cfg.RENDER_FONT_PT))
                doc.setDefaultStyleSheet("body { color: white; background: transparent; }")
                text_w = self.cfg.RENDER_W - 2 * self.cfg.RENDER_MARGIN
                doc.setTextWidth(text_w)
                doc.setPageSize(QSizeF(text_w, self.cfg.RENDER_H - self.cfg.RENDER_TEXT_TOP - self.cfg.RENDER_MARGIN))
                doc.setHtml(html)

                painter.save()
                painter.translate(self.cfg.RENDER_MARGIN, self.cfg.RENDER_TEXT_TOP)
                painter.setClipRect(0, 0, text_w, self.cfg.RENDER_H - self.cfg.RENDER_TEXT_TOP - self.cfg.RENDER_MARGIN)
                doc.drawContents(painter, QtCore.QRectF(0, 0, text_w, self.cfg.RENDER_H))
                painter.restore()
                painter.end()

                # Save atomically
                png_path = os.path.join(self.project_root, 'message.png')
                tmp_path = png_path + ".tmp"
                if not canvas.save(tmp_path):
                    # Fallback direct save
                    canvas.save(png_path)
                else:
                    try:
                        os.replace(tmp_path, png_path)
                    except Exception:
                        canvas.save(png_path)

                # Update preview
                full_pix = QPixmap.fromImage(canvas)
                small_pix = full_pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if hasattr(self.nexus.message_tab, "preview_image"):
                    try:
                        self.nexus.message_tab.preview_image.emit(small_pix)
                    except Exception:
                        pass
                if hasattr(self.nexus.message_tab, "status") and self.nexus.message_tab.status is not None:
                    try:
                        self.nexus.message_tab.status.setText("✅ Wall updated; message.png refreshed.")
                    except Exception:
                        pass
            except Exception as ex:
                if hasattr(self.nexus.message_tab, "status") and self.nexus.message_tab.status is not None:
                    try:
                        self.nexus.message_tab.status.setText(f"❌ Failed regenerating message.png: {ex}")
                    except Exception:
                        pass
        else:
            # No HTML — still update wall preview thumbnail
            pix = QPixmap(path)
            if not pix.isNull():
                thumb = pix.scaled(169, 253, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if hasattr(self.nexus.message_tab, "wall_preview"):
                    try:
                        self.nexus.message_tab.wall_preview.emit(thumb)
                    except Exception:
                        pass
                if hasattr(self.nexus.message_tab, "status") and self.nexus.message_tab.status is not None:
                    try:
                        self.nexus.message_tab.status.setText("🔍 Wall preview updated.")
                    except Exception:
                        pass

    # Keep overlay anchored
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self.nexus.preview_frame and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
            self.float_bar.sync_geometry()
        return super().eventFilter(obj, event)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def install_over_nexus(
    nexus: object,
    project_root: str,
    theme: Optional[Theme] = None,
    config: Optional[Config] = None,
) -> OverNexusController:
    """
    Install the Prompt Writer overlay + Clarifier (Wall) tools into an existing Nexus.

    Behavior toggles via Config:
      - show_prompter_launcher: False → only the two hidable buttons (Wall + Helper)
      - require_seer: True     → raise if SeerController cannot be imported

    Returns:
        OverNexusController
    """
    theme = theme or Theme()
    cfg = config or Config()

    # Bootstrapping (only if we may need Seer)
    seer_factory: Optional[Callable[[PromptMakerUIEmbedded], object]] = None
    if cfg.show_prompter_launcher or cfg.require_seer:
        root = Path(project_root)
        prompter_root = root / "Prompter"
        for p in (prompter_root, prompter_root / "modules", prompter_root / "gui"):
            sp = str(p)
            if p.exists() and sp not in sys.path:
                sys.path.insert(0, sp)

        # Try import SeerController
        seer_mod = None
        try:
            from modules.seer import SeerController as _Seer  # type: ignore
            seer_mod = _Seer
        except Exception:
            try:
                from seer import SeerController as _Seer  # type: ignore
                seer_mod = _Seer
            except Exception:
                if cfg.require_seer:
                    raise RuntimeError(
                        "Unable to locate SeerController. Ensure Prompter/ (with modules/seer.py) is present."
                    )

        if seer_mod is not None:
            def _factory(ui: PromptMakerUIEmbedded) -> object:
                return seer_mod(ui)  # type: ignore
            seer_factory = _factory

    return OverNexusController(nexus, project_root, theme, cfg, seer_factory)