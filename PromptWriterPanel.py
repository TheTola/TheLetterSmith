# PromptWriterPanel.py
# Prompt Writer for eLetter — generates four separate prompts (cover, letter, wall, back)
# Windowed layout (stacked "windows"), per-window copy buttons, global Copy All.

from __future__ import annotations

import sys
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEasingCurve, QUrl
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from window_chrome import StandardTitleBar


VISIONARY_URL = "https://chatgpt.com/g/g-68ce5925196c8191a222e24d29323813-the-visionary"


# ---------------------------
# Robust file discovery & reading + cache
# ---------------------------

_FILE_CACHE: Dict[str, Tuple[List[str], Optional[Path], Optional[Tuple[int, int]]]] = {}
PROMPTER_ROOT = Path(__file__).resolve().parent
PROMPT_WRITER_STATE_VERSION = 1
MAX_STATE_TEXT_LENGTH = 24000


# =========================
# COLOR SYSTEM (UI + Prompt Preview)
# =========================

COL_TYPE = "#ff3b30"      # RED
COL_SUBJECT = "#ff2d55"   # PINK
COL_SCHEME = "#af52de"    # PURPLE

COL_COVER = "#0a84ff"     # BLUE
COL_LETTER = "#32ade6"    # CYAN
COL_WALL = "#30d158"      # BLUE-GREEN
COL_BACK = "#64d2ff"      # SKY BLUE

COL_CHECK = "#00d0ff"

IMAGE_COLOR_MAP = {
    "Cover Prompt": COL_COVER,
    "Letter Prompt": COL_LETTER,
    "Wall Prompt": COL_WALL,
    "Back Prompt": COL_BACK,
}

POLICY_DETAIL_OPTION_SPECS: Tuple[Tuple[str, str, str], ...] = (
    (
        "forbid_text",
        "No text in the image",
        "No text, no letters, no numbers, no glyphs, no typography, no captions, no signage, no logos, no watermarks.",
    ),
    (
        "clean_composition",
        "Clean Composition",
        "keep the composition clean, readable, and free of visual clutter",
    ),
    (
        "strong_focal_point",
        "Strong Focal Point",
        "make the main subject read as the strongest focal point in the image",
    ),
    (
        "dynamic_angle",
        "Dynamic Angle",
        "use a dynamic camera angle that adds energy and visual interest",
    ),
    (
        "cinematic_framing",
        "Cinematic Framing",
        "use cinematic framing with deliberate composition and film-like staging",
    ),
    (
        "close_up_focus",
        "Close-Up Focus",
        "favor a close-up view that brings the subject nearer to the viewer",
    ),
    (
        "full_body_view",
        "Full Body View",
        "show the full subject from head to toe within the frame",
    ),
    (
        "wide_scene",
        "Wide Scene",
        "show more of the environment with a broader wide scene composition",
    ),
    (
        "simplified_details",
        "Simplified Details",
        "simplify fine details to reduce clutter and unnecessary visual noise",
    ),
)

BUILT_IN_CHECK_KEYS: Tuple[str, ...] = (
    "black",
    "white",
    "frame",
    "vignette",
    "polaroid",
    "cardshadow",
    "real",
    "paint",
    "minimal",
) + tuple(key for key, _, _ in POLICY_DETAIL_OPTION_SPECS)


@dataclass(frozen=True)
class PromptPayload:
    image_name: str
    role_sentence: str
    order_fragment: str
    subject_fragment: str
    baseline: str
    type_choice: str = ""
    color_choice: str = ""
    global_extra: str = ""
    image_extra: str = ""
    effort_line: str = ""
    guidance_lines: Tuple[str, ...] = ()
    format_paragraph: str = ""

    def first_paragraph(self) -> str:
        return _join_nonempty(self.role_sentence, self.order_fragment, self.subject_fragment)

    def to_plain_text(self) -> str:
        paragraphs: List[str] = []
        if self.first_paragraph():
            paragraphs.append(self.first_paragraph())
        if self.baseline.strip():
            paragraphs.append(self.baseline.strip())
        if self.color_choice.strip():
            paragraphs.append(f"The color scheme is {self.color_choice.strip()}.")
        if self.type_choice.strip():
            paragraphs.append(f"Create in a {self.type_choice.strip()} design & illustration style.")
        if self.global_extra.strip():
            paragraphs.append(f"Motifs to add: {self.global_extra.strip()}")
        if self.image_extra.strip():
            paragraphs.append(f"Additionally: {self.image_extra.strip()}")
        if self.effort_line.strip():
            paragraphs.append(f"When making this image, {self.effort_line.strip()}")
        if self.guidance_lines:
            paragraphs.append("Guidance:\n" + "\n".join(f"- {line}" for line in self.guidance_lines))
        if self.format_paragraph.strip():
            paragraphs.append(self.format_paragraph.strip())
        return "\n\n".join(part for part in paragraphs if part.strip())


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _span(text: str, color: str, bold: bool = False) -> str:
    t = _html_escape(text)
    if bold:
        return f'<span style="color:{color}; font-weight:900;">{t}</span>'
    return f'<span style="color:{color};">{t}</span>'


def _join_nonempty(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _normalize_text(value: object, *, strip: bool = False, max_length: int = MAX_STATE_TEXT_LENGTH) -> str:
    text = value if isinstance(value, str) else "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if strip:
        text = text.strip()
    if max_length > 0:
        text = text[:max_length]
    return text


def _normalize_guidance_lines(guidance: Optional[List[str]]) -> Tuple[str, ...]:
    return tuple(
        _normalize_text(line, strip=True, max_length=400)
        for line in (guidance or [])
        if _normalize_text(line, strip=True, max_length=400)
    )


def _set_help(widget: QtWidgets.QWidget, text: str) -> None:
    help_text = _normalize_text(text, strip=True, max_length=900)
    widget.setToolTip(help_text)
    widget.setStatusTip(help_text)
    widget.setWhatsThis(help_text)


def render_prompt_html(payload: PromptPayload) -> str:
    """Render the preview with colored values (preview should match emitted text)."""
    col_img = IMAGE_COLOR_MAP.get(payload.image_name, COL_BACK)
    parts: list[str] = []

    first = _join_nonempty(payload.role_sentence, payload.order_fragment)
    if payload.subject_fragment.strip():
        first = (first + " " if first else "") + _span(payload.subject_fragment.strip(), COL_SUBJECT, bold=True)
    if first:
        parts.append(first)

    if payload.baseline.strip():
        parts.append(_html_escape(payload.baseline.strip()))

    if payload.color_choice.strip():
        parts.append("The color scheme is " + _span(payload.color_choice.strip(), COL_SCHEME, bold=True) + ".")

    if payload.type_choice.strip():
        parts.append("Create in a " + _span(payload.type_choice.strip(), COL_TYPE, bold=True) + " design & illustration style.")

    if payload.global_extra.strip():
        parts.append("Motifs to add: " + _span(payload.global_extra.strip(), COL_SCHEME))

    if payload.image_extra.strip():
        parts.append("Additionally: " + _span(payload.image_extra.strip(), col_img))

    if payload.effort_line.strip():
        parts.append(_html_escape(f"When making this image, {payload.effort_line.strip()}"))

    if payload.guidance_lines:
        g = "<br>".join(_html_escape(f"- {line}") for line in payload.guidance_lines)
        parts.append(_html_escape("Guidance:") + "<br>" + g)

    if payload.format_paragraph.strip():
        parts.append(_html_escape(payload.format_paragraph.strip()))

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


def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


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
    out: List[Path] = []
    for c in candidates:
        try:
            key = str(c.resolve())
        except Exception:
            key = str(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _read_list_file(name: str) -> Tuple[List[str], Optional[Path], Optional[Tuple[int, int]]]:
    for p in _candidate_paths_for(name):
        try:
            if p.exists() and p.is_file():
                with p.open("r", encoding="utf-8-sig") as fh:
                    lines = [ln.rstrip("\r\n") for ln in fh.readlines()]
                    return lines, p, _file_signature(p)
        except Exception:
            continue
    return [], None, None


def _read_list_file_cached(name: str) -> Tuple[List[str], Optional[Path]]:
    cached = _FILE_CACHE.get(name)
    if cached:
        cached_lines, cached_path, cached_sig = cached
        if cached_path is not None and _file_signature(cached_path) == cached_sig:
            return cached_lines, cached_path
    lines, path, sig = _read_list_file(name)
    if path is not None:
        _FILE_CACHE[name] = (lines, path, sig)
    else:
        _FILE_CACHE.pop(name, None)
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


def _build_prompt_payload(
    subject: str,
    data: dict,
    image_name: str,
    *,
    type_choice: Optional[str] = None,
    color_choice: Optional[str] = None,
    guidance: Optional[List[str]] = None,
    global_extra: Optional[str] = None,
    image_extra: Optional[str] = None,
) -> Tuple[PromptPayload, dict]:
    baseline_text = _baseline_for(image_name)

    role = _normalize_text(data.get("role", "Artist"), strip=True)
    order = data.get("order", [])
    effort_line = _normalize_text(data.get("effort", ""), strip=True)
    format_paragraph = _normalize_text(data.get("format", ""), strip=True)
    type_text = _normalize_text(type_choice or "", strip=True)
    color_text = _normalize_text(color_choice or "", strip=True)
    global_text = _normalize_text(global_extra or "", strip=True)
    image_text = _normalize_text(image_extra or "", strip=True)
    guidance_lines = _normalize_guidance_lines(guidance)

    dbg = {
        "image": image_name,
        "role": role,
        "order": order,
        "effort": effort_line,
        "type": type_text,
        "color": color_text,
        "guidance": ", ".join(guidance_lines),
        "global_extra": global_text,
        "image_extra": image_text,
        "baseline": baseline_text,
    }

    role_text = role.strip()
    if role_text and not role_text.endswith("."):
        role_text = role_text + "."
    role_sentence = f"You are {role_text}" if role_text else ""

    order_core = ""
    if order:
        joined = " ".join(item.strip().rstrip(" .") for item in order).strip()
        if joined:
            order_core = joined[0].upper() + joined[1:] if len(joined) > 0 else joined

    subject_core = ""
    if subject:
        s = _normalize_text(subject, strip=True, max_length=300).rstrip(" .")
        if s:
            subject_core = s.lower()

    payload = PromptPayload(
        image_name=image_name,
        role_sentence=role_sentence,
        order_fragment=order_core,
        subject_fragment=subject_core,
        baseline=baseline_text,
        type_choice=type_text,
        color_choice=color_text,
        global_extra=global_text,
        image_extra=image_text,
        effort_line=effort_line,
        guidance_lines=guidance_lines,
        format_paragraph=format_paragraph,
    )
    return payload, dbg


def assemble_prompt_for_image(
    subject: str,
    data: dict,
    image_name: str,
    *,
    type_choice: Optional[str] = None,
    color_choice: Optional[str] = None,
    guidance: Optional[List[str]] = None,
    global_extra: Optional[str] = None,
    image_extra: Optional[str] = None,
) -> Tuple[str, PromptPayload, dict]:
    payload, dbg = _build_prompt_payload(
        subject,
        data,
        image_name,
        type_choice=type_choice,
        color_choice=color_choice,
        guidance=guidance,
        global_extra=global_extra,
        image_extra=image_extra,
    )
    return payload.to_plain_text(), payload, dbg


class FocusablePlainTextEdit(QtWidgets.QTextEdit):
    focused = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptRichText(True)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()


class PromptWriterPanel(QtWidgets.QWidget):
    closed = QtCore.Signal()
    prompts_generated = QtCore.Signal(dict, dict)  # prompts_map, debug_map

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, project_root: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("PromptWriterPanel")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.project_root = Path(project_root).resolve() if project_root else self._discover_project_root()

        # Prompt Writer persistence (separate file so other modules can\'t overwrite it)
        self._state_path = self.project_root / "prompt_writer_state.json"
        self._persist_timer = QtCore.QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._persist_state_now)

        self._drag_pos: Optional[QtCore.QPoint] = None
        self._is_maximized: bool = False
        self._normal_geometry: Optional[QtCore.QRect] = None
        self._header_draggable_height = 0

        self._geom_anim = QtCore.QPropertyAnimation(self, b"geometry", self)
        self._fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)

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
            "format": default_format,
        }

        self._images = ["Cover Prompt", "Letter Prompt", "Wall Prompt", "Back Prompt"]
        self.preview_widgets: Dict[str, QtWidgets.QTextEdit] = {}
        self._generated_prompts: Dict[str, str] = {}
        self._colors_path_used: Optional[Path] = None
        self._last_focused_widget: Optional[QtWidgets.QTextEdit] = None

        self._build_ui()
        self._apply_styles()
        self._connect_signals()

        self._load_colors_into_combo()
        self._start_visionary_pulse()
        self._restore_persisted_state()

    # -----------------------
    # Persistence
    # -----------------------
    def _discover_project_root(self) -> Path:
        here = Path(__file__).resolve()
        for up in (here.parent, here.parent.parent, here.parent.parent.parent):
            if (up / "settings.json").exists() or (up / "gallery").exists():
                return up
        cwd = Path.cwd()
        if (cwd / "settings.json").exists() or (cwd / "gallery").exists():
            return cwd
        return here.parent

    def _schedule_persist_state(self, delay_ms: int = 350) -> None:
        try:
            self._persist_timer.start(int(delay_ms))
        except Exception:
            pass

    def _persist_state_now(self) -> None:
        """Persist Prompt Writer selections + outputs.

        Writes to prompt_writer_state.json so other tabs writing settings.json
        cannot clobber Prompt Writer state.
        """
        try:
            state = self._capture_state()
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._state_path.with_name(f".{self._state_path.name}.tmp")
            temp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(self._state_path)
        except Exception:
            pass

    def _normalize_persisted_state(self, state: object) -> dict:
        if not isinstance(state, dict):
            return {}

        checks_raw = state.get("checks", {})
        if not isinstance(checks_raw, dict):
            checks_raw = {}

        generated_raw = state.get("generated_prompts", {})
        if not isinstance(generated_raw, dict):
            generated_raw = {}

        return {
            "version": PROMPT_WRITER_STATE_VERSION,
            "type": _normalize_text(state.get("type", ""), strip=True, max_length=300),
            "subject": _normalize_text(state.get("subject", ""), strip=True, max_length=300),
            "color": _normalize_text(state.get("color", ""), strip=True, max_length=300),
            "global": _normalize_text(state.get("global", "")),
            "cover": _normalize_text(state.get("cover", "")),
            "letter": _normalize_text(state.get("letter", "")),
            "wall": _normalize_text(state.get("wall", "")),
            "back": _normalize_text(state.get("back", "")),
            "checks": {key: bool(checks_raw.get(key, False)) for key in BUILT_IN_CHECK_KEYS},
            "generated_prompts": {
                image_name: _normalize_text(generated_raw.get(image_name, ""))
                for image_name in self._images
                if _normalize_text(generated_raw.get(image_name, "")).strip()
            },
        }

    def _checkbox_state_specs(self) -> Tuple[Tuple[QtWidgets.QCheckBox, str], ...]:
        return (
            (self.cb_black, "black"),
            (self.cb_white, "white"),
            (self.cb_frame, "frame"),
            (self.cb_vignette, "vignette"),
            (self.cb_polaroid, "polaroid"),
            (self.cb_cardshadow, "cardshadow"),
            (self.cb_real, "real"),
            (self.cb_paint, "paint"),
            (self.cb_minimal, "minimal"),
            (self.cb_forbid, "forbid_text"),
            (self.cb_clean_composition, "clean_composition"),
            (self.cb_strong_focal_point, "strong_focal_point"),
            (self.cb_dynamic_angle, "dynamic_angle"),
            (self.cb_cinematic_framing, "cinematic_framing"),
            (self.cb_close_up_focus, "close_up_focus"),
            (self.cb_full_body_view, "full_body_view"),
            (self.cb_wide_scene, "wide_scene"),
            (self.cb_simplified_details, "simplified_details"),
        )

    def _guidance_checkbox_specs(self) -> Tuple[Tuple[QtWidgets.QCheckBox, str], ...]:
        return (
            (self.cb_black, "add a thin black border around the image"),
            (self.cb_white, "add a thin white border around the image"),
            (self.cb_frame, "add a decorative frame around the image"),
            (self.cb_vignette, "add a subtle edge vignette to focus attention"),
            (self.cb_polaroid, "add a polaroid-style white margin, slightly wider at the bottom"),
            (self.cb_cardshadow, "render as a card with a soft drop shadow on a neutral backdrop"),
            (self.cb_real, "bias toward photorealism"),
            (self.cb_paint, "bias toward painterly style"),
            (self.cb_minimal, "bias toward minimalistic composition"),
            (self.cb_forbid, POLICY_DETAIL_OPTION_SPECS[0][2]),
            (self.cb_clean_composition, POLICY_DETAIL_OPTION_SPECS[1][2]),
            (self.cb_strong_focal_point, POLICY_DETAIL_OPTION_SPECS[2][2]),
            (self.cb_dynamic_angle, POLICY_DETAIL_OPTION_SPECS[3][2]),
            (self.cb_cinematic_framing, POLICY_DETAIL_OPTION_SPECS[4][2]),
            (self.cb_close_up_focus, POLICY_DETAIL_OPTION_SPECS[5][2]),
            (self.cb_full_body_view, POLICY_DETAIL_OPTION_SPECS[6][2]),
            (self.cb_wide_scene, POLICY_DETAIL_OPTION_SPECS[7][2]),
            (self.cb_simplified_details, POLICY_DETAIL_OPTION_SPECS[8][2]),
        )

    def _capture_state(self) -> dict:
        def _cb(cb: QtWidgets.QCheckBox) -> bool:
            try:
                return bool(cb.isChecked())
            except Exception:
                return False

        try:
            color_txt = self.cmb_color.currentText().strip()
            if color_txt == "— none —":
                color_txt = ""
        except Exception:
            color_txt = ""

        return {
            "version": PROMPT_WRITER_STATE_VERSION,
            "type": self.cmb_type.currentText().strip(),
            "subject": self.cmb_subject.currentText().strip(),
            "color": color_txt,
            "global": self.txt_global.toPlainText(),
            "cover": self.txt_cover.toPlainText(),
            "letter": self.txt_letter.toPlainText(),
            "wall": self.txt_wall.toPlainText(),
            "back": self.txt_back.toPlainText(),
            "checks": {
                state_key: _cb(checkbox)
                for checkbox, state_key in self._checkbox_state_specs()
            },
            "generated_prompts": {
                image_name: prompt
                for image_name, prompt in self._generated_prompts.items()
                if prompt.strip()
            },
        }

    def _restore_persisted_state(self) -> None:
        """Load persisted Prompt Writer state.

        Source of truth:
        1) prompt_writer_state.json
        """
        try:
            state = None

            try:
                if self._state_path.exists():
                    state = json.loads(self._state_path.read_text(encoding="utf-8"))
            except Exception:
                state = None

            state = self._normalize_persisted_state(state)
            if not state:
                return

            # Selects
            try:
                t = str(state.get("type", "")).strip()
                if t:
                    self.cmb_type.setCurrentText(t)
            except Exception:
                pass
            try:
                s = str(state.get("subject", "")).strip()
                if s:
                    self.cmb_subject.setCurrentText(s)
            except Exception:
                pass
            try:
                c = str(state.get("color", "")).strip()
                if c:
                    idx = self.cmb_color.findText(c)
                    if idx >= 0:
                        self.cmb_color.setCurrentIndex(idx)
            except Exception:
                pass

            # Text
            try:
                self.txt_global.setPlainText(str(state.get("global", "") or ""))
                self.txt_cover.setPlainText(str(state.get("cover", "") or ""))
                self.txt_letter.setPlainText(str(state.get("letter", "") or ""))
                self.txt_wall.setPlainText(str(state.get("wall", "") or ""))
                self.txt_back.setPlainText(str(state.get("back", "") or ""))
            except Exception:
                pass

            # Checkboxes
            checks = state.get("checks", {})
            if isinstance(checks, dict):
                for checkbox, state_key in self._checkbox_state_specs():
                    try:
                        checkbox.setChecked(bool(checks.get(state_key, False)))
                    except Exception:
                        pass

            # Generated previews
            try:
                self._generated_prompts = dict(state.get("generated_prompts", {}))
                for img, txt in self._generated_prompts.items():
                    w = self.preview_widgets.get(img)
                    if w is not None and txt.strip():
                        w.setPlainText(txt)
            except Exception:
                pass
        except Exception:
            pass

    # -----------------------
    # UI
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

        self.title_bar = StandardTitleBar(
            self,
            "Prompt Writer",
            show_minimize=False,
            on_close=self._on_close,
            on_minimize=self.showMinimized,
            on_toggle_maximize=self._toggle_max_restore,
            is_maximized=lambda: self._is_maximized,
        )
        cl.addWidget(self.title_bar)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        self.btn_generate = QtWidgets.QPushButton("Generate")
        self.btn_copy = QtWidgets.QPushButton("Copy All")
        self.btn_erase = QtWidgets.QPushButton("Erase All")
        for b in (self.btn_generate, self.btn_copy, self.btn_erase):
            b.setFixedHeight(28)
        header.addWidget(self.btn_generate)
        header.addWidget(self.btn_copy)
        header.addWidget(self.btn_erase)

        self.lbl_visionary_prefix = QtWidgets.QLabel("For best results, use")
        self.lbl_visionary_prefix.setStyleSheet("color:#cfd3da; padding-left:6px;")
        _set_help(
            self.lbl_visionary_prefix,
            "Open The Visionary if you want extra prompt-writing guidance before you generate the image set.",
        )
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
        _set_help(
            self.btn_visionary,
            "Open The Visionary for deeper prompt guidance and refinement ideas.",
        )

        header.addWidget(self.btn_visionary)
        header.addStretch(1)

        cl.addLayout(header)

        main_h = QtWidgets.QHBoxLayout()
        main_h.setSpacing(12)
        cl.addLayout(main_h, 1)

        left_v = QtWidgets.QVBoxLayout()
        left_v.setSpacing(10)
        main_h.addLayout(left_v, 1)

        sel_grid = QtWidgets.QGridLayout()
        sel_grid.setHorizontalSpacing(8)
        sel_grid.setVerticalSpacing(8)

        lbl_type = QtWidgets.QLabel("Graphics and Illustration")
        lbl_type.setStyleSheet(f"font-weight:900; color:{COL_TYPE};")
        _set_help(
            lbl_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.setEditable(False)
        _set_help(
            self.cmb_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )

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
        _set_help(
            lbl_subject,
            "Choose or type the main thing the images should be about. This is the core subject that appears across the prompt set.",
        )
        self.cmb_subject = QtWidgets.QComboBox()
        self.cmb_subject.setEditable(True)
        _set_help(
            self.cmb_subject,
            "Choose or type the main thing the images should be about. This is the core subject that appears across the prompt set.",
        )
        subjects, _ = _read_list_file_cached("topic.txt")
        for s in [l for l in subjects if l.strip()]:
            self.cmb_subject.addItem(s)
        sel_grid.addWidget(lbl_subject, 1, 0)
        sel_grid.addWidget(self.cmb_subject, 1, 1)

        lbl_color = QtWidgets.QLabel("Color Scheme")
        lbl_color.setStyleSheet(f"font-weight:900; color:{COL_SCHEME};")
        _set_help(
            lbl_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self.cmb_color = QtWidgets.QComboBox()
        self.cmb_color.setEditable(False)
        _set_help(
            self.cmb_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self.cmb_color.addItem("— none —")
        sel_grid.addWidget(lbl_color, 2, 0)
        sel_grid.addWidget(self.cmb_color, 2, 1)

        left_v.addLayout(sel_grid)

        self.gb_helpful = QtWidgets.QGroupBox("")
        self.gb_helpful.setStyleSheet("QGroupBox { font-weight:900; }")
        self.gb_helpful.setObjectName("gb_helpful")
        _set_help(
            self.gb_helpful,
            "Use these built-in options to refine composition, framing, image policy, and style. They add supporting instructions to the generated prompts without changing the main subject field.",
        )

        gb_layout = QtWidgets.QGridLayout()
        gb_layout.setContentsMargins(10, 10, 10, 10)
        gb_layout.setHorizontalSpacing(12)
        gb_layout.setVerticalSpacing(6)
        self.gb_helpful.setLayout(gb_layout)

        self.lbl_helpful = QtWidgets.QLabel("Helpful Options")
        self.lbl_helpful.setStyleSheet("font-weight:900; color:#ffffff;")
        _set_help(
            self.lbl_helpful,
            "Use these built-in options to refine composition, framing, image policy, and style. They add supporting instructions to the generated prompts without changing the main subject field.",
        )
        gb_layout.addWidget(self.lbl_helpful, 0, 0, 1, 2)

        lbl_edges = QtWidgets.QLabel("Edges")
        lbl_edges.setStyleSheet("font-weight:700;")
        gb_layout.addWidget(lbl_edges, 1, 0, 1, 2)

        self.cb_black = QtWidgets.QCheckBox("black border")
        self.cb_white = QtWidgets.QCheckBox("white border")
        self.cb_frame = QtWidgets.QCheckBox("Decorative frame")
        self.cb_vignette = QtWidgets.QCheckBox("Soft vignette edges")
        self.cb_polaroid = QtWidgets.QCheckBox("Polaroid-style margin")
        self.cb_cardshadow = QtWidgets.QCheckBox("Card drop shadow")

        gb_layout.addWidget(self.cb_black, 2, 0)
        gb_layout.addWidget(self.cb_white, 2, 1)
        gb_layout.addWidget(self.cb_frame, 3, 0)
        gb_layout.addWidget(self.cb_vignette, 3, 1)
        gb_layout.addWidget(self.cb_polaroid, 4, 0)
        gb_layout.addWidget(self.cb_cardshadow, 4, 1)

        lbl_style = QtWidgets.QLabel("Style bias")
        lbl_style.setStyleSheet("font-weight:700; margin-top:8px;")
        gb_layout.addWidget(lbl_style, 5, 0, 1, 2)

        self.cb_real = QtWidgets.QCheckBox("Bias toward photorealism")
        self.cb_paint = QtWidgets.QCheckBox("Bias toward painterly")
        self.cb_minimal = QtWidgets.QCheckBox("Bias toward minimalistic composition")

        gb_layout.addWidget(self.cb_real, 6, 0)
        gb_layout.addWidget(self.cb_paint, 6, 1)
        gb_layout.addWidget(self.cb_minimal, 7, 0)

        lbl_policy = QtWidgets.QLabel("Policy / Detail")
        lbl_policy.setStyleSheet("font-weight:700; margin-top:8px;")
        gb_layout.addWidget(lbl_policy, 8, 0, 1, 2)

        self.cb_forbid = QtWidgets.QCheckBox("No text in the image")
        self.cb_clean_composition = QtWidgets.QCheckBox("Clean Composition")
        self.cb_strong_focal_point = QtWidgets.QCheckBox("Strong Focal Point")
        self.cb_dynamic_angle = QtWidgets.QCheckBox("Dynamic Angle")
        self.cb_cinematic_framing = QtWidgets.QCheckBox("Cinematic Framing")
        self.cb_close_up_focus = QtWidgets.QCheckBox("Close-Up Focus")
        self.cb_full_body_view = QtWidgets.QCheckBox("Full Body View")
        self.cb_wide_scene = QtWidgets.QCheckBox("Wide Scene")
        self.cb_simplified_details = QtWidgets.QCheckBox("Simplified Details")

        gb_layout.addWidget(self.cb_forbid, 9, 0, 1, 2)
        gb_layout.addWidget(self.cb_clean_composition, 10, 0)
        gb_layout.addWidget(self.cb_strong_focal_point, 10, 1)
        gb_layout.addWidget(self.cb_dynamic_angle, 11, 0)
        gb_layout.addWidget(self.cb_cinematic_framing, 11, 1)
        gb_layout.addWidget(self.cb_close_up_focus, 12, 0)
        gb_layout.addWidget(self.cb_full_body_view, 12, 1)
        gb_layout.addWidget(self.cb_wide_scene, 13, 0)
        gb_layout.addWidget(self.cb_simplified_details, 13, 1)

        left_v.addWidget(self.gb_helpful)

        lbl_global = QtWidgets.QLabel("Apply to All Images")
        lbl_global.setStyleSheet("font-weight:900; color:#ffffff;")
        _set_help(
            lbl_global,
            "Anything written here is added to every generated image prompt, so use it for shared ideas, mood, setting, or details that should apply across the full set.",
        )
        left_v.addWidget(lbl_global)
        self.txt_global = QtWidgets.QPlainTextEdit()
        self.txt_global.setPlaceholderText("Add ideas that should be applied across every image in the set.")
        self.txt_global.setMaximumHeight(120)
        _set_help(
            self.txt_global,
            "Anything written here is added to every generated image prompt, so use it for shared ideas, mood, setting, or details that should apply across the full set.",
        )
        left_v.addWidget(self.txt_global)

        per_lbl = QtWidgets.QLabel("Details for Each Image")
        per_lbl.setStyleSheet("font-weight:900; color:#ffffff;")
        _set_help(
            per_lbl,
            "Use these fields for details that should affect only one specific image, not the whole image set.",
        )
        left_v.addWidget(per_lbl)

        self.txt_cover = QtWidgets.QPlainTextEdit(); self.txt_cover.setMaximumHeight(70)
        self.txt_letter = QtWidgets.QPlainTextEdit(); self.txt_letter.setMaximumHeight(70)
        self.txt_wall = QtWidgets.QPlainTextEdit(); self.txt_wall.setMaximumHeight(70)
        self.txt_back = QtWidgets.QPlainTextEdit(); self.txt_back.setMaximumHeight(70)

        self.txt_cover.setPlaceholderText("Add details that should apply only to cover.png.")
        self.txt_letter.setPlaceholderText("Add details that should apply only to letter.png.")
        self.txt_wall.setPlaceholderText("Add details that should apply only to wall.png.")
        self.txt_back.setPlaceholderText("Add details that should apply only to back.png.")

        lbl = QtWidgets.QLabel("cover.png"); lbl.setStyleSheet(f"font-weight:900; color:{COL_COVER};")
        _set_help(
            lbl,
            "Anything written here is added only to the cover.png prompt. Use it for cover-specific details, composition, or mood.",
        )
        _set_help(
            self.txt_cover,
            "Anything written here is added only to the cover.png prompt. Use it for cover-specific details, composition, or mood.",
        )
        left_v.addWidget(lbl); left_v.addWidget(self.txt_cover)
        lbl = QtWidgets.QLabel("letter.png"); lbl.setStyleSheet(f"font-weight:900; color:{COL_LETTER};")
        _set_help(
            lbl,
            "Anything written here is added only to the letter.png prompt. Use it for letter-specific details or layout direction.",
        )
        _set_help(
            self.txt_letter,
            "Anything written here is added only to the letter.png prompt. Use it for letter-specific details or layout direction.",
        )
        left_v.addWidget(lbl); left_v.addWidget(self.txt_letter)
        lbl = QtWidgets.QLabel("wall.png"); lbl.setStyleSheet(f"font-weight:900; color:{COL_WALL};")
        _set_help(
            lbl,
            "Anything written here is added only to the wall.png prompt. Use it for wall-specific environment or background details.",
        )
        _set_help(
            self.txt_wall,
            "Anything written here is added only to the wall.png prompt. Use it for wall-specific environment or background details.",
        )
        left_v.addWidget(lbl); left_v.addWidget(self.txt_wall)
        lbl = QtWidgets.QLabel("back.png"); lbl.setStyleSheet(f"font-weight:900; color:{COL_BACK};")
        _set_help(
            lbl,
            "Anything written here is added only to the back.png prompt. Use it for back-page details or closing visual accents.",
        )
        _set_help(
            self.txt_back,
            "Anything written here is added only to the back.png prompt. Use it for back-page details or closing visual accents.",
        )
        left_v.addWidget(lbl); left_v.addWidget(self.txt_back)

        right_v = QtWidgets.QVBoxLayout(); right_v.setSpacing(8)
        main_h.addLayout(right_v, 1)

        self._preview_scroll = QtWidgets.QScrollArea(); self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        preview_container = QtWidgets.QWidget()
        self._preview_layout = QtWidgets.QVBoxLayout(preview_container)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout.setSpacing(12)

        for img in self._images:
            block = QtWidgets.QFrame()
            block.setFrameShape(QtWidgets.QFrame.Box)
            block.setFrameShadow(QtWidgets.QFrame.Plain)
            block.setStyleSheet("QFrame { border: 1px solid #222228; border-radius:6px; background: transparent; }")
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

            copy_btn.clicked.connect(lambda _, image_name=img: self._copy_prompt(image_name))
            editor.focused.connect(lambda ed=editor: self._set_last_focused(ed))

        self._preview_layout.addStretch(1)
        self._preview_scroll.setWidget(preview_container)
        right_v.addWidget(self._preview_scroll, 1)

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QWidget#PromptWriterPanel { background: rgba(0,0,0,0); }
            QFrame#container {
                background-color: #131318;
                border: 1px solid #23232a;
                border-radius: 10px;
            }
            QGroupBox#gb_helpful, QGroupBox {
                border: none;
                background: transparent;
                padding: 0;
                margin-top: 0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 0px;
                padding: 0 0 6px 0;
            }
            QLabel {
                color: #dcdce0;
                background: transparent;
            }
            QPushButton {
                padding: 6px 12px;
                background: transparent;
                border: 1px solid #313543;
                color: #eaeaf0;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(59, 124, 240, 0.12);
                border-color: #3b7cf0;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
                background: rgba(7, 9, 13, 0.72);
                color: #e8e8ea;
                border: 1px solid #232834;
                border-radius: 6px;
                padding: 6px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QToolTip {
                background: #10141d;
                color: #ecf2ff;
                border: 1px solid #2d4267;
                padding: 6px 8px;
            }
            """ + CHECKBOX_QSS
        )

    def _connect_signals(self):
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_copy.clicked.connect(self._copy_all_prompts)
        self.btn_erase.clicked.connect(self._on_erase_all)

        try:
            self.cmb_type.currentTextChanged.connect(lambda *_: self._schedule_persist_state())
            self.cmb_subject.currentTextChanged.connect(lambda *_: self._schedule_persist_state())
            try:
                self.cmb_subject.editTextChanged.connect(lambda *_: self._schedule_persist_state())
            except Exception:
                pass
            self.cmb_color.currentIndexChanged.connect(lambda *_: self._schedule_persist_state())
            self.txt_global.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_cover.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_letter.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_wall.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_back.textChanged.connect(lambda: self._schedule_persist_state())
            for checkbox, _ in self._checkbox_state_specs():
                checkbox.stateChanged.connect(lambda *_: self._schedule_persist_state())
        except Exception:
            pass

    def _load_colors_into_combo(self):
        lines, used_path = _read_list_file_cached("colors.txt")
        if not lines:
            lines, used_path = _read_list_file_cached("color.txt")
        self._colors_path_used = used_path

        while self.cmb_color.count() > 1:
            self.cmb_color.removeItem(1)

        entries: List[Tuple[str, str]] = []
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

    def _collect_guidance(self) -> List[str]:
        return [
            phrase
            for checkbox, phrase in self._guidance_checkbox_specs()
            if checkbox.isChecked()
        ]

    def _get_color_choice(self) -> Optional[str]:
        text = self.cmb_color.currentText().strip()
        if text in ("— none —", "", "────────", "(no color entries found)"):
            return None
        item = self.cmb_color.model().item(self.cmb_color.currentIndex())
        if item is not None and not item.isEnabled():
            return None
        return text

    def _set_last_focused(self, widget: QtWidgets.QTextEdit):
        self._last_focused_widget = widget

    def _on_generate(self):
        subject = self.cmb_subject.currentText().strip()
        if not subject:
            QtWidgets.QMessageBox.warning(self, "Missing subject", "Please enter a Subject.")
            return

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

        for img in self._images:
            per_image_data = self._roll_data_for_image()
            per_data_map[img] = per_image_data
            prompt, payload, dbg = assemble_prompt_for_image(
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
            per_data_map[img]["payload"] = payload
            debug_map[img] = dbg

        self._generated_prompts = dict(prompts)

        for img in self._images:
            w = self.preview_widgets.get(img)
            if not w:
                continue
            payload = per_data_map.get(img, {}).get("payload")
            if not isinstance(payload, PromptPayload):
                continue
            html_view = render_prompt_html(payload)
            w.setHtml(html_view)

        self.prompts_generated.emit(prompts, debug_map)
        self._persist_state_now()

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
            text = self._generated_prompts.get(key, "").strip()
            if not text:
                w = self.preview_widgets.get(key)
                if w:
                    text = w.toPlainText().strip()
            parts.append(f"--- {key} ---\n\n{text}" if text else f"--- {key} ---\n\n")
        all_text = "\n\n".join(parts).strip()
        if all_text:
            QtWidgets.QApplication.clipboard().setText(all_text)

    def _copy_prompt(self, image_name: str) -> None:
        text = self._generated_prompts.get(image_name, "").strip()
        if not text:
            widget = self.preview_widgets.get(image_name)
            if widget is not None:
                text = widget.toPlainText().strip()
        if text:
            QtWidgets.QApplication.clipboard().setText(text)

    def _on_erase_all(self):
        self.cmb_subject.setCurrentText("")
        self.cmb_type.setCurrentIndex(0)
        self.cmb_color.setCurrentIndex(0)
        for checkbox, _ in self._checkbox_state_specs():
            checkbox.setChecked(False)

        self._generated_prompts = {}
        self.txt_global.clear(); self.txt_cover.clear(); self.txt_letter.clear(); self.txt_wall.clear(); self.txt_back.clear()
        for w in self.preview_widgets.values():
            w.clear()

        self._load_colors_into_combo()
        self._persist_state_now()

    def _toggle_max_restore(self) -> None:
        screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
        avail = screen_obj.availableGeometry() if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry()
        if not self._is_maximized:
            self._normal_geometry = self.geometry()
            self.setGeometry(avail)
            self._is_maximized = True
        else:
            if self._normal_geometry:
                self.setGeometry(self._normal_geometry)
            self._is_maximized = False
        self.title_bar.sync_window_state()

    def _on_close(self):
        self._persist_state_now()
        self.hide()

    def _start_visionary_pulse(self):
        try:
            self._visionary_colors = [QColor("#2d6bff"), QColor("#03d5ff"), QColor("#ffffff"), QColor("#03d5ff")]
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

    def popup(self):
        if self.isVisible():
            return
        self.setWindowOpacity(0.0)
        self.show(); self.raise_()
        self.title_bar.sync_window_state()

        screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
        avail = screen_obj.availableGeometry() if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry()

        w = min(int(avail.width() * 0.72), 980)
        h = min(int(avail.height() * 0.86), 900)
        target = QtCore.QRect(avail.x() + 40, avail.y() + 40, w, h)
        off = QtCore.QRect(target.x() - w, target.y(), w, h)

        self.setGeometry(off)
        self._geom_anim.stop(); self._geom_anim.setDuration(260)
        self._geom_anim.setStartValue(off); self._geom_anim.setEndValue(target)
        self._geom_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._geom_anim.start()

        self._fade_anim.stop(); self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0); self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def popdown(self):
        if not self.isVisible():
            return
        geom = self.geometry()
        off = QtCore.QRect(geom.x() - geom.width() - 20, geom.y(), geom.width(), geom.height())
        self._geom_anim.stop(); self._geom_anim.setDuration(200)
        self._geom_anim.setEasingCurve(QEasingCurve.InCubic)
        self._geom_anim.setStartValue(geom); self._geom_anim.setEndValue(off)
        self._geom_anim.start()

        self._fade_anim.stop(); self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(self.windowOpacity()); self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        QtCore.QTimer.singleShot(220, self.hide)

    # Frameless move/max
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            y = event.position().y() if hasattr(event, "position") else event.pos().y()
            if event.button() == Qt.LeftButton and y <= self._header_draggable_height:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept(); return
        except Exception:
            pass
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        try:
            if self._drag_pos and not self._is_maximized:
                new_top_left = event.globalPosition().toPoint() - self._drag_pos
                screen_obj = self.screen() or QtGui.QGuiApplication.primaryScreen()
                screen_geom = screen_obj.availableGeometry() if screen_obj else QtGui.QGuiApplication.primaryScreen().availableGeometry()
                w, h = self.width(), self.height()
                x = max(screen_geom.left(), min(new_top_left.x(), screen_geom.right() - w))
                y = max(screen_geom.top(), min(new_top_left.y(), screen_geom.bottom() - h))
                self.move(x, y)
                event.accept(); return
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
                self._toggle_max_restore()
                event.accept(); return
        except Exception:
            pass
        super().mouseDoubleClickEvent(event)

    def hide(self):
        try:
            super().hide()
        finally:
            self.closed.emit()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = PromptWriterPanel()
    w.popup(); w.show()
    sys.exit(app.exec())
