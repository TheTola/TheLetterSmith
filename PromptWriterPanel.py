# PromptWriterPanel.py
# Prompt Writer for eLetter — generates four separate prompts (cover, letter, wall, back)
# Windowed layout (stacked "windows"), per-window copy buttons, global Copy All.
from __future__ import annotations

import sys
import random
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEasingCurve, QUrl
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

# Optional visual helper if present
try:
    from anima import ButtonPulseFilter
except Exception:
    class ButtonPulseFilter(QtCore.QObject):
        def eventFilter(self, *_): return False


# ---------------------------
# Visionary link (CTA)
# ---------------------------
VISIONARY_URL = "https://chatgpt.com/g/g-68ce5925196c8191a222e24d29323813-the-visionary"


# ---------------------------
# Robust file discovery & reading + cache
# ---------------------------

# simple module-level cache to avoid re-reading files each Generate
_FILE_CACHE: Dict[str, Tuple[List[str], Optional[Path]]] = {}

PROMPTER_ROOT = Path(__file__).resolve().parent

# =========================
# COLOR SYSTEM (UI + Prompt Preview)
# =========================

# Selector label colors
COL_TYPE    = "#ff3b30"  # RED    (Graphics & Illustration (type))
COL_SUBJECT = "#ff2d55"  # PINK   (Subject)
COL_SCHEME  = "#af52de"  # PURPLE (Color Scheme)

# Per-image label colors
COL_COVER   = "#0a84ff"  # BLUE
COL_LETTER  = "#32ade6"  # CYAN
COL_WALL    = "#30d158"  # BLUE-GREEN
COL_BACK    = "#64d2ff"  # SKY BLUE

# Checkbox checkmark color
COL_CHECK   = "#00d0ff"  # BLUE

# Map image prompt name -> color
IMAGE_COLOR_MAP = {
    "Cover Prompt": COL_COVER,
    "Letter Prompt": COL_LETTER,
    "Wall Prompt": COL_WALL,
    "Back Prompt": COL_BACK,
}

def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _span(text: str, color: str, bold: bool = False) -> str:
    t = _html_escape(text)
    if bold:
        return f'<span style="color:{color}; font-weight:900;">{t}</span>'
    return f'<span style="color:{color};">{t}</span>'

def render_prompt_html(
    *,
    image_name: str,
    subject: str,
    type_choice: str | None,
    scheme: str | None,
    global_extra: str,
    image_extra: str,
    role_sentence: str,
    baseline: str,
    effort_line: str,
    guidance_lines: list[str],
    format_paragraph: str,
) -> str:
    """
    OLD FORMAT (paragraph order + phrasing), but with colored VALUES.
    No extra labels like 'Subject:' or 'Global ideas:'.
    """
    col_img = IMAGE_COLOR_MAP.get(image_name, COL_BACK)

    parts: list[str] = []

    # 1) First paragraph: role sentence + subject appended inline
    first = role_sentence.strip()
    if subject.strip():
        first = (first + " " if first else "") + _span(subject.strip(), COL_SUBJECT, bold=True)
    if first:
        parts.append(first)

    # 2) Baseline paragraph
    if baseline.strip():
        parts.append(_html_escape(baseline.strip()))

    # 3) Color scheme line (old phrasing) with colored value
    if scheme:
        parts.append("The color scheme is " + _span(scheme, COL_SCHEME, bold=True) + ".")

    # 4) Type/style line (old phrasing) with colored value
    if type_choice:
        parts.append("Create in a " + _span(type_choice, COL_TYPE, bold=True) + " design & illustration style.")

    # 5) Global motifs line (old phrasing)
    if global_extra.strip():
        parts.append("Motifs to add: " + _span(global_extra.strip(), COL_SCHEME))

    # 6) Per-image extras line (old phrasing)
    if image_extra.strip():
        parts.append("Additionally: " + _span(image_extra.strip(), col_img))

    # 7) Effort line (old phrasing)
    if effort_line.strip():
        parts.append(_html_escape(f"When making this image, {effort_line.strip()}"))

    # 8) Guidance block
    if guidance_lines:
        lines = [x.strip() for x in guidance_lines if x and x.strip()]
        if lines:
            g = "<br>".join(_html_escape(f"- {x}") for x in lines)
            parts.append(_html_escape("Guidance:") + "<br>" + g)

    # 9) Format paragraph
    if format_paragraph.strip():
        parts.append(_html_escape(format_paragraph.strip()))

    return "<br><br>".join(p for p in parts if str(p).strip())


CHECKBOX_QSS = f"""
QCheckBox {{ color: #dcdce0; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #2b2b31;
    background: #0d0e11;
}}
QCheckBox::indicator:checked {{
    background: {COL_CHECK};
    border: 1px solid {COL_CHECK};
}}
"""

# ---------------------------------------
# Baseline descriptions per image role
# ---------------------------------------
_IMAGE_BASELINES: Dict[str, str] = {
    "cover": "The Cover Page is a bold, decorative opening image that captures attention and sets the tone.",
    "letter": "The Letter Page is a subtle, elegant backdrop that frames the main written message without distraction.",
    "wall": "The Wall Page is a calm, minimalist background designed to support large blocks of text.",
    "back": "The Back Page is a simple, graceful closing image that echoes the cover while providing a sense of finality.",
}

def _baseline_for(image_name: str) -> str:
    key = image_name.strip().lower()
    if "cover" in key:
        return _IMAGE_BASELINES["cover"]
    if "letter" in key:
        return _IMAGE_BASELINES["letter"]
    if "wall" in key:
        return _IMAGE_BASELINES["wall"]
    if "back" in key:
        return _IMAGE_BASELINES["back"]
    return ""


def _candidate_paths_for(name: str) -> List[Path]:
    candidates: List[Path] = []
    candidates.append(PROMPTER_ROOT / "Prompter" / "modules" / name)
    candidates.append(PROMPTER_ROOT / "modules" / name)
    candidates.append(PROMPTER_ROOT / name)
    p = PROMPTER_ROOT
    for _ in range(3):
        p = p.parent
        candidates.append(p / "Prompter" / "modules" / name)
        candidates.append(p / "modules" / name)
        candidates.append(p / name)
    cwd = Path.cwd()
    candidates.append(cwd / "Prompter" / "modules" / name)
    candidates.append(cwd / "modules" / name)
    candidates.append(cwd / name)
    seen = set()
    out = []
    for c in candidates:
        try:
            key = str(c.resolve())
        except Exception:
            key = str(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _read_list_file(name: str) -> Tuple[List[str], Optional[Path]]:
    for p in _candidate_paths_for(name):
        try:
            if p.exists() and p.is_file():
                with p.open("r", encoding="utf-8-sig") as fh:
                    lines = [ln.rstrip("\r\n") for ln in fh.readlines()]
                    return lines, p
        except Exception:
            continue
    return [], None


def _read_list_file_cached(name: str) -> Tuple[List[str], Optional[Path]]:
    if name in _FILE_CACHE:
        return _FILE_CACHE[name]
    lines, path = _read_list_file(name)
    _FILE_CACHE[name] = (lines, path)
    return lines, path


def _clean_choice_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    if s.startswith("- ") or s.startswith("• "):
        s = s[2:].strip()
    return s


def _pick_random_nonempty_line(name: str) -> Tuple[Optional[str], Optional[Path]]:
    lines, used_path = _read_list_file_cached(name)
    cleaned = [_clean_choice_line(l) for l in lines if l and l.strip()]
    if not cleaned:
        return None, used_path
    return random.choice(cleaned), used_path


def _pick_random_order(name: str) -> Tuple[Optional[List[str]], Optional[Path]]:
    pick, used_path = _pick_random_nonempty_line(name)
    if not pick:
        return None, used_path
    if "," in pick:
        items = [t.strip() for t in pick.split(",") if t.strip()]
    elif "|" in pick:
        items = [t.strip() for t in pick.split("|") if t.strip()]
    elif ";" in pick:
        items = [t.strip() for t in pick.split(";") if t.strip()]
    else:
        items = [pick.strip()]
    return items or None, used_path


def _random_combo_choice(combo: QtWidgets.QComboBox) -> Optional[str]:
    valid = []
    for i in range(combo.count()):
        txt = combo.itemText(i).strip()
        item = combo.model().item(i)
        if txt in ("— none —", "", "────────", "(no color entries found)"):
            continue
        if item is not None and not item.isEnabled():
            continue
        valid.append((i, txt))
    if not valid:
        return None
    idx, txt = random.choice(valid)
    combo.setCurrentIndex(idx)
    return txt


# ---------------------------
# Build single-image prompt (plain text, for copy/export)
# ---------------------------

def assemble_prompt_for_image(subject: str,
                              data: dict,
                              image_name: str,
                              *,
                              type_choice: Optional[str] = None,
                              color_choice: Optional[str] = None,
                              guidance: Optional[List[str]] = None,
                              global_extra: Optional[str] = None,
                              image_extra: Optional[str] = None) -> Tuple[str, dict]:
    paragraphs: List[str] = []

    baseline_text = _baseline_for(image_name)

    dbg = {
        "image": image_name,
        "role": data.get("role", ""),
        "order": data.get("order", []),
        "effort": data.get("effort", ""),
        "type": type_choice or "",
        "color": color_choice or "",
        "guidance": ", ".join(guidance or []),
        "global_extra": (global_extra or "").strip(),
        "image_extra": (image_extra or "").strip(),
        "baseline": baseline_text,
    }

    role = data.get("role", "Artist")
    order = data.get("order", [])
    effort_line = data.get("effort", "")
    format_paragraph = data.get("format", "")

    role_text = role.strip()
    if role_text and not role_text.endswith("."):
        role_text = role_text + "."
    role_sentence = f"You are {role_text}" if role_text else ""

    order_core = ""
    if order:
        joined = ' '.join(item.strip().rstrip(' .') for item in order).strip()
        if joined:
            order_core = joined[0].upper() + joined[1:] if len(joined) > 0 else joined

    subject_core = ""
    if subject:
        s = subject.strip().rstrip(' .')
        if s:
            subject_core = s.lower()

    combined_fragment = ""
    if order_core and subject_core:
        combined_fragment = f"{order_core} {subject_core}"
    elif order_core:
        combined_fragment = order_core
    elif subject_core:
        combined_fragment = subject_core

    if role_sentence and combined_fragment:
        paragraphs.append(f"{role_sentence}  {combined_fragment}")
    elif role_sentence:
        paragraphs.append(role_sentence)
    elif combined_fragment:
        paragraphs.append(combined_fragment)

    if baseline_text:
        paragraphs.append(baseline_text)

    if color_choice:
        paragraphs.append(f"The color scheme is {color_choice}.")
    if type_choice:
        paragraphs.append(f"Create in a {type_choice} design & illustration style.")

    if global_extra and global_extra.strip():
        paragraphs.append("Motifs to add: " + global_extra.strip())

    if image_extra and image_extra.strip():
        paragraphs.append(f"Additionally: " + image_extra.strip())

    if effort_line:
        paragraphs.append(f"When making this image, {effort_line.strip()}")

    if guidance:
        lines = [g.strip() for g in guidance if g and g.strip()]
        if lines:
            guidance_block = "Guidance:\n" + "\n".join(f"- {line}" for line in lines)
            paragraphs.append(guidance_block)

    if format_paragraph:
        paragraphs.append(format_paragraph.strip())

    final = "\n".join(p for p in paragraphs if p.strip()).replace("\n\n\n", "\n\n")
    return final, dbg


# ---------------------------
# Focusable rich editor for previews (HTML rendering)
# ---------------------------
class FocusablePlainTextEdit(QtWidgets.QTextEdit):
    focused = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptRichText(True)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()


# ---------------------------
# Widget: PromptWriterPanel
# ---------------------------
class PromptWriterPanel(QtWidgets.QWidget):
    closed = QtCore.Signal()
    prompts_generated = QtCore.Signal(dict, dict)  # prompts_map, debug_map

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("PromptWriterPanel")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # --- window move / maximize helpers ---
        self._drag_pos: Optional[QtCore.QPoint] = None
        self._is_maximized: bool = False
        self._normal_geometry: Optional[QtCore.QRect] = None
        self._header_draggable_height = 44

        # animation placeholders
        self._geom_anim = QtCore.QPropertyAnimation(self, b"geometry", self)
        self._fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)

        # initial seeded data — may be overwritten each Generate from files
        role_lines, _ = _read_list_file_cached("role.txt")
        seeded_role = _clean_choice_line(role_lines[0]) if role_lines and any(l.strip() for l in role_lines) else "Artist"

        default_format = (
            "The image must be produced in portrait orientation at exactly 2048×3072 pixels. "
            "Its layout should remain graceful and well-proportioned, with a clear visual order that ensures "
            "the subject can be recognized both at thumbnail scale and in full size. The color scheme should feel "
            "unified and harmonious. The subject’s outline must stay sharp and distinct, with lighting handled "
            "consistently in direction, strength, and temperature. Depth should emerge through perspective and "
            "atmospheric layering, while unnecessary clutter is avoided. Fine detail is required, and margins must be "
            "respected to prevent cropping. Colors should look smooth and natural, steering clear of harsh banding or "
            "oversaturation unless otherwise specified. Negative space should be used deliberately to protect readability "
            "and focus. If any demands conflict, resolution fidelity, clarity, and cohesion take precedence."
        )

        ultra_effort = (
            "Operate at the absolute highest standard. Think at the highest creative and technical level, "
            "reason through composition, lighting, color, texture, and mood in exhaustive detail, and produce "
            "output that fully documents the rationale and choices made — maximum verbosity and forensic clarity."
        )

        self._data = {
            "role": seeded_role,
            "order": ["composition", "lighting", "mood"],
            "effort": ultra_effort,
            "format": default_format
        }

        self._images = ["Cover Prompt", "Letter Prompt", "Wall Prompt", "Back Prompt"]

        self._colors_path_used: Optional[Path] = None
        self._last_focused_widget: Optional[QtWidgets.QTextEdit] = None
        self.preview_widgets: Dict[str, QtWidgets.QTextEdit] = {}

        self._build_ui()
        self._apply_styles()
        self._connect_signals()

        self._load_colors_into_combo()
        self._start_visionary_pulse()

    # -----------------------
    # UI construction
    # -----------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        container = QtWidgets.QFrame(self)
        container.setObjectName("container")
        cl = QtWidgets.QVBoxLayout(container)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(10)
        root.addWidget(container)

        # Header
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        self.btn_generate = QtWidgets.QPushButton("Generate")
        self.btn_generate.setObjectName("gen_btn")
        self.btn_generate.setFixedHeight(28)

        self.btn_random = QtWidgets.QPushButton("Random")
        self.btn_random.setFixedHeight(28)

        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.setFixedHeight(28)

        self.btn_copy = QtWidgets.QPushButton("Copy All")
        self.btn_copy.setFixedHeight(28)

        self.btn_erase = QtWidgets.QPushButton("Erase All")
        for b in (self.btn_generate, self.btn_random, self.btn_refresh, self.btn_copy, self.btn_erase):
            b.setFixedHeight(28)

        header.addWidget(self.btn_generate)
        header.addWidget(self.btn_random)
        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_copy)
        header.addWidget(self.btn_erase)

        self.lbl_visionary_prefix = QtWidgets.QLabel("For best results, use")
        self.lbl_visionary_prefix.setStyleSheet("color:#cfd3da; padding-left:6px;")
        header.addWidget(self.lbl_visionary_prefix)

        self.btn_visionary = QtWidgets.QPushButton("The Visionary")
        self.btn_visionary.setObjectName("visionary_btn")
        self.btn_visionary.setCursor(Qt.PointingHandCursor)
        self.btn_visionary.setFlat(True)
        self.btn_visionary.setToolTip("Open The Visionary (recommended guide)")
        self.btn_visionary.setStyleSheet(
            "QPushButton#visionary_btn{"
            "background:transparent;border:none;padding:0 6px;"
            "font-weight:700;text-decoration:underline;color:#2d6bff;"
            "}"
            "QPushButton#visionary_btn:hover{opacity:0.95;}"
        )
        self._visionary_effect = QGraphicsDropShadowEffect(self.btn_visionary)
        self._visionary_effect.setBlurRadius(32)
        self._visionary_effect.setOffset(0, 0)
        self._visionary_effect.setColor(QColor("#2d6bff"))
        self.btn_visionary.setGraphicsEffect(self._visionary_effect)
        self.btn_visionary.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(VISIONARY_URL)))

        header.addWidget(self.btn_visionary)
        header.addStretch(1)

        self.btn_close = QtWidgets.QPushButton("✕")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setToolTip("Close")
        header.addWidget(self.btn_close)

        cl.addLayout(header)

        # Main split
        main_h = QtWidgets.QHBoxLayout()
        main_h.setSpacing(12)
        cl.addLayout(main_h, 1)

        # Left column
        left_v = QtWidgets.QVBoxLayout()
        left_v.setSpacing(10)
        main_h.addLayout(left_v, 1)

        sel_grid = QtWidgets.QGridLayout()
        sel_grid.setHorizontalSpacing(8)
        sel_grid.setVerticalSpacing(8)

        lbl_type = QtWidgets.QLabel("Graphic & Illustration (type)")
        lbl_type.setStyleSheet(f"font-weight:900; color:{COL_TYPE};")
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.setEditable(False)

        types, _ = _read_list_file_cached("type.txt")
        self.cmb_type.addItem("— none —")
        for t in types:
            s = t.rstrip("\r\n")
            if not s.strip():
                idx = self.cmb_type.count()
                self.cmb_type.addItem("────────")
                it = self.cmb_type.model().item(idx)
                if it is not None:
                    it.setEnabled(False)
            elif s.startswith("-") or s.startswith("•"):
                idx = self.cmb_type.count()
                self.cmb_type.addItem(s.lstrip("-• ").strip())
                it = self.cmb_type.model().item(idx)
                if it is not None:
                    it.setEnabled(False)
            else:
                self.cmb_type.addItem(s.strip())
        sel_grid.addWidget(lbl_type, 0, 0)
        sel_grid.addWidget(self.cmb_type, 0, 1)

        lbl_subject = QtWidgets.QLabel("Subject")
        lbl_subject.setStyleSheet(f"font-weight:900; color:{COL_SUBJECT};")
        self.cmb_subject = QtWidgets.QComboBox()
        self.cmb_subject.setEditable(True)
        subjects, _ = _read_list_file_cached("topic.txt")
        for s in [l for l in subjects if l.strip()]:
            self.cmb_subject.addItem(s)
        sel_grid.addWidget(lbl_subject, 1, 0)
        sel_grid.addWidget(self.cmb_subject, 1, 1)

        lbl_color = QtWidgets.QLabel("Color Scheme (optional)")
        lbl_color.setStyleSheet(f"font-weight:900; color:{COL_SCHEME};")
        self.cmb_color = QtWidgets.QComboBox()
        self.cmb_color.setEditable(False)
        self.cmb_color.addItem("— none —")
        sel_grid.addWidget(lbl_color, 2, 0)
        sel_grid.addWidget(self.cmb_color, 2, 1)

        self.lbl_color_status = QtWidgets.QLabel("")
        self.lbl_color_status.setStyleSheet("color: #aab0b8; font-size: 11px;")
        sel_grid.addWidget(self.lbl_color_status, 3, 0, 1, 2)

        left_v.addLayout(sel_grid)

        self.gb_helpful = QtWidgets.QGroupBox("Helpful option")
        self.gb_helpful.setStyleSheet("QGroupBox { font-weight:900; }")
        self.gb_helpful.setObjectName("gb_helpful")

        gb_layout = QtWidgets.QGridLayout()
        gb_layout.setContentsMargins(10, 10, 10, 10)
        gb_layout.setHorizontalSpacing(12)
        gb_layout.setVerticalSpacing(6)
        self.gb_helpful.setLayout(gb_layout)

        lbl_edges = QtWidgets.QLabel("Edges")
        lbl_edges.setStyleSheet("font-weight:700;")
        gb_layout.addWidget(lbl_edges, 0, 0, 1, 2)

        self.cb_black = QtWidgets.QCheckBox("black border")
        self.cb_white = QtWidgets.QCheckBox("white border")
        self.cb_frame = QtWidgets.QCheckBox("Decorative frame")
        self.cb_vignette = QtWidgets.QCheckBox("Soft vignette edges")
        self.cb_polaroid = QtWidgets.QCheckBox("Polaroid-style margin")
        self.cb_cardshadow = QtWidgets.QCheckBox("Card drop shadow")

        gb_layout.addWidget(self.cb_black, 1, 0)
        gb_layout.addWidget(self.cb_white, 1, 1)
        gb_layout.addWidget(self.cb_frame, 2, 0)
        gb_layout.addWidget(self.cb_vignette, 2, 1)
        gb_layout.addWidget(self.cb_polaroid, 3, 0)
        gb_layout.addWidget(self.cb_cardshadow, 3, 1)

        lbl_style = QtWidgets.QLabel("Style bias")
        lbl_style.setStyleSheet("font-weight:700; margin-top:8px;")
        gb_layout.addWidget(lbl_style, 4, 0, 1, 2)

        self.cb_real = QtWidgets.QCheckBox("Bias toward photorealism")
        self.cb_paint = QtWidgets.QCheckBox("Bias toward painterly")
        self.cb_minimal = QtWidgets.QCheckBox("Bias toward minimalistic")

        gb_layout.addWidget(self.cb_real, 5, 0)
        gb_layout.addWidget(self.cb_paint, 5, 1)
        gb_layout.addWidget(self.cb_minimal, 6, 0)

        lbl_policy = QtWidgets.QLabel("Policy / Detail")
        lbl_policy.setStyleSheet("font-weight:700; margin-top:8px;")
        gb_layout.addWidget(lbl_policy, 7, 0, 1, 2)

        self.cb_forbid = QtWidgets.QCheckBox("No text in the image")
        gb_layout.addWidget(self.cb_forbid, 8, 0, 1, 2)

        left_v.addWidget(self.gb_helpful)

        lbl_global = QtWidgets.QLabel("Global ideas")
        lbl_global.setStyleSheet("font-weight:900; color:#ffffff;")
        left_v.addWidget(lbl_global)
        self.txt_global = QtWidgets.QPlainTextEdit()
        self.txt_global.setPlaceholderText("Write ideas that should apply across the whole project (theme, time-of-day, mood, etc.)")
        self.txt_global.setMaximumHeight(120)
        left_v.addWidget(self.txt_global)

        per_lbl = QtWidgets.QLabel("Per-image extras")
        per_lbl.setStyleSheet("font-weight:900; color:#ffffff;")
        left_v.addWidget(per_lbl)

        self.txt_cover = QtWidgets.QPlainTextEdit()
        self.txt_cover.setPlaceholderText("cover.png — extra instructions (e.g. morning lighting, prominent border)")
        self.txt_cover.setMaximumHeight(70)
        self.txt_letter = QtWidgets.QPlainTextEdit()
        self.txt_letter.setPlaceholderText("letter.png — extra instructions")
        self.txt_letter.setMaximumHeight(70)
        self.txt_wall = QtWidgets.QPlainTextEdit()
        self.txt_wall.setPlaceholderText("wall.png — extra instructions")
        self.txt_wall.setMaximumHeight(70)
        self.txt_back = QtWidgets.QPlainTextEdit()
        self.txt_back.setPlaceholderText("back.png — extra instructions")
        self.txt_back.setMaximumHeight(70)

        lbl = QtWidgets.QLabel("cover.png")
        lbl.setStyleSheet(f"font-weight:900; color:{COL_COVER};")
        left_v.addWidget(lbl)
        left_v.addWidget(self.txt_cover)

        lbl = QtWidgets.QLabel("letter.png")
        lbl.setStyleSheet(f"font-weight:900; color:{COL_LETTER};")
        left_v.addWidget(lbl)
        left_v.addWidget(self.txt_letter)

        lbl = QtWidgets.QLabel("wall.png")
        lbl.setStyleSheet(f"font-weight:900; color:{COL_WALL};")
        left_v.addWidget(lbl)
        left_v.addWidget(self.txt_wall)

        lbl = QtWidgets.QLabel("back.png")
        lbl.setStyleSheet(f"font-weight:900; color:{COL_BACK};")
        left_v.addWidget(lbl)
        left_v.addWidget(self.txt_back)

        # Right column
        right_v = QtWidgets.QVBoxLayout()
        right_v.setSpacing(8)
        main_h.addLayout(right_v, 1)

        self._preview_scroll = QtWidgets.QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        preview_container = QtWidgets.QWidget()
        self._preview_layout = QtWidgets.QVBoxLayout(preview_container)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout.setSpacing(12)

        for img in self._images:
            block = QtWidgets.QFrame()
            block.setFrameShape(QtWidgets.QFrame.Box)
            block.setFrameShadow(QtWidgets.QFrame.Plain)
            block.setStyleSheet("QFrame { border: 1px solid #222228; border-radius:6px; background: #0c0d10; }")
            bl = QtWidgets.QVBoxLayout(block)
            bl.setContentsMargins(8, 8, 8, 8)
            bl.setSpacing(6)

            header_row = QtWidgets.QHBoxLayout()
            header_label = QtWidgets.QLabel(img)
            header_label.setStyleSheet("font-weight:700; color: #dcdce0;")
            header_row.addWidget(header_label)
            header_row.addStretch(1)
            copy_btn = QtWidgets.QPushButton("Copy")
            copy_btn.setFixedSize(64, 24)
            copy_btn.setToolTip(f"Copy {img}")
            header_row.addWidget(copy_btn)
            bl.addLayout(header_row)

            editor = FocusablePlainTextEdit()
            editor.setReadOnly(True)
            editor.setAcceptRichText(True)
            editor.setPlaceholderText(f"Prompt for {img}")
            editor.setMinimumHeight(140)
            editor.setMaximumHeight(260)
            bl.addWidget(editor)

            self._preview_layout.addWidget(block)
            self.preview_widgets[img] = editor

            copy_btn.clicked.connect(lambda _, w=editor: QtWidgets.QApplication.clipboard().setText(w.toPlainText()))
            editor.focused.connect(lambda ed=editor: self._set_last_focused(ed))

        self._preview_layout.addStretch(1)
        self._preview_scroll.setWidget(preview_container)
        right_v.addWidget(self._preview_scroll, 1)

    # -----------------------
    # Styling
    # -----------------------
    def _apply_styles(self):
        self.setStyleSheet("""
        QWidget#PromptWriterPanel { background: rgba(0,0,0,0); }
        QFrame#container {
            background-color: #131318;
            border: 1px solid #23232a;
            border-radius: 10px;
        }
        QGroupBox#gb_helpful, QGroupBox {
            border: 1px solid #26262b;
            border-radius: 6px;
            padding: 8px;
        }
        QLabel { color: #dcdce0; }
        QPushButton {
            padding: 6px 12px;
            background: #1e1f24;
            border: 1px solid #2b2b31;
            color: #eaeaf0;
            border-radius: 6px;
        }
        QPushButton:hover { border-color: #3b7cf0; }

        QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
            background: #0d0e11;
            color: #e8e8ea;
            border: 1px solid #242428;
            border-radius: 6px;
            padding: 6px;
        }
        """ + CHECKBOX_QSS)

    # -----------------------
    # Signals
    # -----------------------
    def _connect_signals(self):
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_random.clicked.connect(self._on_random)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_copy.clicked.connect(self._copy_all_prompts)
        self.btn_erase.clicked.connect(self._on_erase_all)
        self.btn_close.clicked.connect(self._on_close)

    # -----------------------
    # Colors loader & status
    # -----------------------
    def _load_colors_into_combo(self):
        lines, used_path = _read_list_file_cached("colors.txt")
        if not lines:
            lines, used_path = _read_list_file_cached("color.txt")
        self._colors_path_used = used_path
        while self.cmb_color.count() > 1:
            self.cmb_color.removeItem(1)
        entries = []
        for c in lines:
            if not c.strip():
                entries.append(("SEPARATOR", "────────"))
            else:
                entries.append(("ITEM", c.strip()))
        item_count = sum(1 for t, _ in entries if t == "ITEM")
        if item_count == 0:
            idx = self.cmb_color.count()
            self.cmb_color.addItem("(no color entries found)")
            it = self.cmb_color.model().item(idx)
            if it is not None:
                it.setEnabled(False)
            status = "No color entries found."
            if used_path:
                status += f" looked in: {str(used_path)}"
            else:
                status += " no colors.txt/color.txt found in candidate locations."
            self.lbl_color_status.setText(status)
            return
        for tag, val in entries:
            if tag == "SEPARATOR":
                idx = self.cmb_color.count()
                self.cmb_color.addItem("────────")
                it = self.cmb_color.model().item(idx)
                if it is not None:
                    it.setEnabled(False)
            else:
                self.cmb_color.addItem(val)
        if used_path:
            self.lbl_color_status.setText(f"Loaded {item_count} color(s) from: {str(used_path)}")
        else:
            self.lbl_color_status.setText(f"Loaded {item_count} color(s) from unknown location")

    # -----------------------
    # Helper: guidance mapping
    # -----------------------
    def _collect_guidance(self) -> List[str]:
        phrases: List[str] = []
        if getattr(self, "cb_black", None) and self.cb_black.isChecked():
            phrases.append("add a thin black border around the image")
        if getattr(self, "cb_white", None) and self.cb_white.isChecked():
            phrases.append("add a thin white border around the image")
        if getattr(self, "cb_frame", None) and self.cb_frame.isChecked():
            phrases.append("add a decorative frame around the image")
        if getattr(self, "cb_vignette", None) and self.cb_vignette.isChecked():
            phrases.append("add a subtle edge vignette to focus attention")
        if getattr(self, "cb_polaroid", None) and self.cb_polaroid.isChecked():
            phrases.append("add a polaroid-style white margin, slightly wider at the bottom")
        if getattr(self, "cb_cardshadow", None) and self.cb_cardshadow.isChecked():
            phrases.append("render as a card with a soft drop shadow on a neutral backdrop")

        if getattr(self, "cb_real", None) and self.cb_real.isChecked():
            phrases.append("bias toward photorealism")
        if getattr(self, "cb_paint", None) and self.cb_paint.isChecked():
            phrases.append("bias toward painterly style")
        if getattr(self, "cb_minimal", None) and self.cb_minimal.isChecked():
            phrases.append("bias toward minimalistic composition")

        if getattr(self, "cb_forbid", None) and self.cb_forbid.isChecked():
            phrases.append("No text, no letters, no numbers, no glyphs, no typography, no captions, no signage, no logos, no watermarks.")
        return phrases

    def _get_color_choice(self) -> Optional[str]:
        idx = self.cmb_color.currentIndex()
        text = self.cmb_color.currentText().strip()
        if text in ("— none —", "", "────────", "(no color entries found)"):
            count = self.cmb_color.count()
            for i in range(idx + 1, count):
                item = self.cmb_color.model().item(i)
                if item is not None and item.isEnabled():
                    self.cmb_color.setCurrentIndex(i)
                    return self.cmb_color.currentText()
            for i in range(idx - 1, -1, -1):
                item = self.cmb_color.model().item(i)
                if item is not None and item.isEnabled():
                    self.cmb_color.setCurrentIndex(i)
                    return self.cmb_color.currentText()
            return None
        return text

    # -----------------------
    # Focus handling
    # -----------------------
    def _set_last_focused(self, widget: QtWidgets.QTextEdit):
        self._last_focused_widget = widget

    # -----------------------
    # Actions
    # -----------------------
    def _on_random(self):
        _random_combo_choice(self.cmb_type)
        _random_combo_choice(self.cmb_color)

        role_pick, _ = _pick_random_nonempty_line("role.txt")
        if role_pick:
            self._data["role"] = role_pick

        order_pick, _ = _pick_random_order("order.txt")
        if order_pick:
            self._data["order"] = order_pick

        effort_pick, _ = _pick_random_nonempty_line("effort.txt")
        if effort_pick:
            self._data["effort"] = effort_pick

        format_pick, _ = _pick_random_nonempty_line("format.txt")
        if format_pick:
            self._data["format"] = format_pick

        color_src = str(self._colors_path_used) if self._colors_path_used else "none"
        t = self.cmb_type.currentText() if self.cmb_type.currentIndex() >= 0 else ""
        c = self._get_color_choice()
        self.lbl_color_status.setText(f"role={self._data.get('role','')} | type={t or ''} | color={c or ''} | color_src={color_src}")

    def _on_refresh(self):
        _FILE_CACHE.clear()

        self.cmb_type.clear()
        self.cmb_type.addItem("— none —")
        types, _ = _read_list_file_cached("type.txt")
        for t in types:
            s = t.rstrip("\r\n")
            if not s.strip():
                idx = self.cmb_type.count()
                self.cmb_type.addItem("────────")
                it = self.cmb_type.model().item(idx)
                if it is not None:
                    it.setEnabled(False)
            elif s.startswith("-") or s.startswith("•"):
                idx = self.cmb_type.count()
                self.cmb_type.addItem(s.lstrip("-• ").strip())
                it = self.cmb_type.model().item(idx)
                if it is not None:
                    it.setEnabled(False)
            else:
                self.cmb_type.addItem(s.strip())

        self.cmb_subject.clear()
        subjects, _ = _read_list_file_cached("topic.txt")
        for s in [l for l in subjects if l.strip()]:
            self.cmb_subject.addItem(s)

        self._load_colors_into_combo()

    def _on_generate(self):
        # Validate subject (user-chosen)
        subject = self.cmb_subject.currentText().strip()
        if not subject:
            QtWidgets.QMessageBox.warning(self, "Missing subject", "Please enter a Subject.")
            return

        # type & color are user-selected (same across all images)
        t = self.cmb_type.currentText()
        if t in ("— none —", "────────"):
            t = None
        c = self._get_color_choice()

        guidance = self._collect_guidance()
        global_extra = self.txt_global.toPlainText().strip()
        per_extras = {
            "Cover Prompt": self.txt_cover.toPlainText().strip(),
            "Letter Prompt": self.txt_letter.toPlainText().strip(),
            "Wall Prompt": self.txt_wall.toPlainText().strip(),
            "Back Prompt": self.txt_back.toPlainText().strip(),
        }

        prompts: Dict[str, str] = {}
        debug_map: Dict[str, dict] = {}
        per_data_map: Dict[str, dict] = {}

        # Each image gets its own randomized role/order/effort/format roll.
        for img in self._images:
            per_image_data = self._roll_data_for_image()
            per_data_map[img] = per_image_data

            prompt, dbg = assemble_prompt_for_image(
                subject,
                per_image_data,
                img,
                type_choice=t,
                color_choice=c,
                guidance=guidance,
                global_extra=global_extra,
                image_extra=per_extras.get(img, ""),
            )
            prompts[img] = prompt
            debug_map[img] = dbg

        # Render OLD-format preview, but color-coded values
        for img in self._images:
            w = self.preview_widgets.get(img)
            if not w:
                continue

            d = per_data_map.get(img, {})
            html_view = render_prompt_html(
                image_name=img,
                subject=subject,
                type_choice=t,
                scheme=c,
                global_extra=global_extra,
                image_extra=per_extras.get(img, ""),
                role_sentence=f"You are {d.get('role','').strip().rstrip('.')}. Create a richly detailed image of ----",
                baseline=_baseline_for(img),
                effort_line=d.get("effort", ""),
                guidance_lines=guidance or [],
                format_paragraph=d.get("format", ""),
            )
            w.setHtml(html_view)

        # emit signal for external consumers
        self.prompts_generated.emit(prompts, debug_map)

    def _roll_data_for_image(self) -> dict:
        data = dict(self._data)

        role_pick, _ = _pick_random_nonempty_line("role.txt")
        if role_pick:
            data["role"] = role_pick

        order_pick, _ = _pick_random_order("order.txt")
        if order_pick:
            data["order"] = order_pick

        effort_pick, _ = _pick_random_nonempty_line("effort.txt")
        if effort_pick:
            data["effort"] = effort_pick

        format_pick, _ = _pick_random_nonempty_line("format.txt")
        if format_pick:
            data["format"] = format_pick

        return data

    def _copy_all_prompts(self):
        parts: List[str] = []
        for key in self._images:
            w = self.preview_widgets.get(key)
            if w:
                text = w.toPlainText().strip()
                parts.append(f"--- {key} ---\n\n{text}" if text else f"--- {key} ---\n\n")
        all_text = "\n\n".join(parts).strip()
        if all_text:
            QtWidgets.QApplication.clipboard().setText(all_text)

    def _on_erase_all(self):
        self.cmb_subject.setCurrentText("")
        self.cmb_type.setCurrentIndex(0)
        self.cmb_color.setCurrentIndex(0)
        for name in ("cb_black","cb_white","cb_frame","cb_vignette","cb_polaroid","cb_cardshadow",
                     "cb_real","cb_paint","cb_minimal","cb_forbid"):
            cb = getattr(self, name, None)
            if isinstance(cb, QtWidgets.QCheckBox):
                cb.setChecked(False)
        self.txt_global.clear()
        self.txt_cover.clear()
        self.txt_letter.clear()
        self.txt_wall.clear()
        self.txt_back.clear()
        for w in self.preview_widgets.values():
            w.clear()
        self._load_colors_into_combo()

    def _on_close(self):
        self.hide()
        self.closed.emit()

    # -----------------------
    # Visionary pulse
    # -----------------------
    def _start_visionary_pulse(self):
        try:
            self._visionary_colors = [
                QColor("#2d6bff"),
                QColor("#03d5ff"),
                QColor("#ffffff"),
                QColor("#03d5ff"),
            ]
            self._visionary_index = 0
            self._visionary_timer = QtCore.QTimer(self)
            self._visionary_timer.setInterval(520)
            self._visionary_timer.timeout.connect(self._tick_visionary_pulse)
            self._visionary_timer.start()
        except Exception:
            pass

    def _tick_visionary_pulse(self):
        try:
            self._visionary_index = (self._visionary_index + 1) % len(self._visionary_colors)
            col = self._visionary_colors[self._visionary_index]
        except Exception:
            return

        eff = getattr(self, "_visionary_effect", None)
        if isinstance(eff, QGraphicsDropShadowEffect):
            eff.setColor(col)

        style = (
            "QPushButton#visionary_btn{"
            "background:transparent;border:none;padding:0 6px;"
            "font-weight:700;text-decoration:underline;color:%s;"
            "}"
            "QPushButton#visionary_btn:hover{color:#ffffff;}"
        ) % col.name()

        try:
            self.btn_visionary.setStyleSheet(style)
        except Exception:
            pass

    # -----------------------
    # Popup / hide animations
    # -----------------------
    def popup(self):
        if self.isVisible():
            return
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
        avail = screen_obj.availableGeometry() if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry()

        w = min(int(avail.width() * 0.72), 980)
        h = min(int(avail.height() * 0.86), 900)
        target = QtCore.QRect(avail.x() + 40, avail.y() + 40, w, h)
        off = QtCore.QRect(target.x() - w, target.y(), w, h)

        self.setGeometry(off)
        self._geom_anim.stop()
        self._geom_anim.setDuration(260)
        self._geom_anim.setStartValue(off)
        self._geom_anim.setEndValue(target)
        self._geom_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._geom_anim.start()

        self._fade_anim.stop()
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def popdown(self):
        if not self.isVisible():
            return
        geom = self.geometry()
        off = QtCore.QRect(geom.x() - geom.width() - 20, geom.y(), geom.width(), geom.height())
        self._geom_anim.stop()
        self._geom_anim.setDuration(200)
        self._geom_anim.setEasingCurve(QEasingCurve.InCubic)
        self._geom_anim.setStartValue(geom)
        self._geom_anim.setEndValue(off)
        self._geom_anim.start()

        self._fade_anim.stop()
        self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        QtCore.QTimer.singleShot(220, self.hide)

    # -----------------------
    # Frameless move & maximize support
    # -----------------------
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            y = event.position().y() if hasattr(event, "position") else event.pos().y()
            if event.button() == Qt.LeftButton and y <= self._header_draggable_height:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        except Exception:
            pass
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            if self._drag_pos and not self._is_maximized:
                new_top_left = event.globalPosition().toPoint() - self._drag_pos

                screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
                screen_geom = (screen_obj.availableGeometry()
                               if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry())

                w, h = self.width(), self.height()
                x = max(screen_geom.left(), min(new_top_left.x(), screen_geom.right() - w))
                y = max(screen_geom.top(), min(new_top_left.y(), screen_geom.bottom() - h))
                self.move(x, y)
                event.accept()
                return
        except Exception:
            pass
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            self._drag_pos = None
        except Exception:
            pass
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            y = event.position().y() if hasattr(event, "position") else event.pos().y()
            if y <= self._header_draggable_height:
                screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
                avail = (screen_obj.availableGeometry()
                         if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry())
                if not self._is_maximized:
                    self._normal_geometry = self.geometry()
                    self.setGeometry(avail)
                    self._is_maximized = True
                else:
                    if self._normal_geometry:
                        self.setGeometry(self._normal_geometry)
                    self._is_maximized = False
                event.accept()
                return
        except Exception:
            pass
        super().mouseDoubleClickEvent(event)

    def hide(self):
        try:
            super().hide()
        finally:
            self.closed.emit()


# ---------------------------
# Test harness
# ---------------------------
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = PromptWriterPanel()
    w.popup()
    w.show()
    sys.exit(app.exec())
