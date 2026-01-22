# modules/seer.py
from __future__ import annotations

import json
import sys
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt

# ──────────────────────────────────────────────────────────────────────────────
# Paths & constants
# ──────────────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
PROMPTER_ROOT = HERE.parent
if str(PROMPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(PROMPTER_ROOT))

# UI shell (your working Prompt UI)
from gui import PromptMakerUI, APP_TITLE  # type: ignore

MODULES_DIR = PROMPTER_ROOT / "modules"
CONFIG_PATH = PROMPTER_ROOT / "prompter_config.json"

# Canonical data files (your existing files)
PALETTE_PATH       = MODULES_DIR / "Topic.txt"   # unified Subject/Occasion
TYPE_STYLES_PATH   = MODULES_DIR / "Type.txt"    # headers + dash-options
COLOR_SCHEMES_PATH = MODULES_DIR / "color.txt"   # schemes with blank-line separators

DEFAULT_PALETTE_SENTINEL = "----"  # “no subject”
DEFAULT_SCHEME_SENTINEL  = "----"  # “no scheme”
AUTO_RELOAD_MS = 5 * 60 * 1000     # 5 minutes

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
def load_config() -> Dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "win_geometry": [180, 180, 1180, 860],
        "copy_on_build": True,
    }

# ──────────────────────────────────────────────────────────────────────────────
# IO helpers
# ──────────────────────────────────────────────────────────────────────────────
def read_lines_simple(path: Path) -> List[str]:
    """
    Generic list loader:
      - Strips blank lines.
      - Ignores #comments and [Section] headers.
      - Un-bullets lines beginning with '- ' or '• '.
      - If exactly one ':' is present and both sides non-empty, keep the right side.
    """
    if not path.exists():
        return []
    out: List[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if line.startswith(("- ", "• ")):
            line = line[2:].strip()
        if ":" in line:
            left, right = line.split(":", 1)
            if left.strip() and right.strip():
                line = right.strip()
        if line:
            out.append(line)
    return out


def read_block_text(path: Path) -> str:
    """Load entire file as a text block (for global constraints)."""
    if not path.exists():
        return ""
    return "\n".join(ln.rstrip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()).strip()


# ──────────────────────────────────────────────────────────────────────────────
# Occasion / Subject list (Topic.txt)
# ──────────────────────────────────────────────────────────────────────────────
def ensure_palette_file_has_default() -> None:
    p = PALETTE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(DEFAULT_PALETTE_SENTINEL + "\n", encoding="utf-8")
        return
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()]
    if DEFAULT_PALETTE_SENTINEL not in lines:
        lines.insert(0, DEFAULT_PALETTE_SENTINEL)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_palettes() -> List[str]:
    ensure_palette_file_has_default()
    items = read_lines_simple(PALETTE_PATH)
    ordered = [DEFAULT_PALETTE_SENTINEL] + [t for t in items if t and t != DEFAULT_PALETTE_SENTINEL]
    seen, out = set(), []
    for t in ordered:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def save_palettes(palettes: List[str]) -> None:
    p = PALETTE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    uniq, seen = [], set()
    for t in ([DEFAULT_PALETTE_SENTINEL] + [x for x in palettes if x and x != DEFAULT_PALETTE_SENTINEL]):
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    p.write_text("\n".join(uniq) + "\n", encoding="utf-8")


def load_color_schemes_raw() -> List[str]:
    """
    Raw schemes from color.txt; preserves blank lines for visual separators,
    and ensures DEFAULT_SCHEME_SENTINEL is the first row.
    """
    if not COLOR_SCHEMES_PATH.exists():
        return [DEFAULT_SCHEME_SENTINEL]
    lines = COLOR_SCHEMES_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    cleaned = [ln.rstrip("\r\n") for ln in lines]  # preserve empties as ""
    if not cleaned or cleaned[0] != DEFAULT_SCHEME_SENTINEL:
        cleaned = [DEFAULT_SCHEME_SENTINEL] + cleaned
    return cleaned


def parse_type_lines(path: Path) -> List[str]:
    """
    Return the RAW lines of Type.txt (headers, dash-items, empties preserved).
    We will render headers as bold/unclickable and dash-items as selectable.
    """
    if not path.exists():
        return []
    return [ln.rstrip("\r\n") for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt helpers
# ──────────────────────────────────────────────────────────────────────────────
def split_format_or_constraints(s: str) -> Tuple[str, str]:
    """
    Decide whether a line from format.txt is a short 'visual format' or
    a longer 'per-output constraint' chunk.
    """
    if not s:
        return "", ""
    raw = s.strip()
    if raw.lower().startswith("format:"):
        raw = raw[7:].strip()
    longish = (len(raw) > 180) or any(
        k in raw.lower() for k in ("pixel", "pixels", "px", "png", "watermark", "border")
    )
    return ("", raw) if longish else (raw, "")


def build_prompt(
    role: str,
    effort: str,
    order_line: str,
    brief: str,
    style_name: str,             # unified style; "" to omit
    color_scheme_name: str,      # color scheme; "" to omit
    color_palette_name: str,     # unified subject+preset; "" to omit
    visual_format: str,          # "" to omit
    per_output_extra: str,
    global_constraints: str,
) -> str:
    blocks: List[str] = []
    if role:
        blocks.append(role)
    if effort:
        blocks.append(effort)

    directive = order_line or "Create a richly detailed image of"
    blocks.append(f"{directive} {brief}".strip())

    # Unified Occasion line (replaces prior 'Letter subject:' legacy)
    if color_palette_name:
        blocks.append(f"Occasion: {color_palette_name}")

    style_bits: List[str] = []
    if style_name:
        style_bits.append(f"Style: {style_name}")
    if color_scheme_name.strip():
        style_bits.append(f"Color scheme: {color_scheme_name.strip()}")
    if visual_format:
        style_bits.append(f"Format: {visual_format}")
    if style_bits:
        blocks.append("; ".join(style_bits))

    for chunk in (per_output_extra, global_constraints):
        if chunk and chunk.strip():
            blocks.append(chunk.strip())

    return "\n\n".join([b for b in blocks if b.strip()])


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────
class SeerController(QtCore.QObject):
    """
    Prompt Maker logic (no presets; all lists pulled from your text files).
      • Subject/Occasion → Topic.txt
      • Graphic & Illustration → Type.txt (headers = bold+unclickable, dash-items selectable)
      • Color scheme → color.txt (blank lines become separators)
    """
    def __init__(self, ui: PromptMakerUI):
        super().__init__(ui)
        self.ui  = ui
        self.cfg = load_config()

        # Lists (other modules you already use; safe if missing)
        self.roles   = read_lines_simple(MODULES_DIR / "Role.txt")
        self.efforts = read_lines_simple(MODULES_DIR / "effort.txt")
        self.orders  = read_lines_simple(MODULES_DIR / "order.txt")
        self.formats = read_lines_simple(MODULES_DIR / "format.txt")
        self.global_constraints = read_block_text(MODULES_DIR / "constraints.txt")  # optional

        # Unified Occasions + schemes + style raw
        self.palettes: List[str] = load_palettes()
        self.schemes_raw: List[str] = load_color_schemes_raw()   # includes blanks + sentinel
        self.type_raw: List[str]    = parse_type_lines(TYPE_STYLES_PATH)

        # Actions
        self.ui.gen_btn.clicked.connect(self.on_generate)
        self.ui.export_btn.clicked.connect(self.copy_all_to_clipboard)  # Copy All
        self.ui.rand_all_btn.clicked.connect(self._on_rand_all)
        self.ui.erase_btn.clicked.connect(self.on_erase_all)

        # Helpful options
        self._wire_helpful_options()

        # Live: color scheme, Occasion, style
        self._init_scheme_combo()    # colors_combo (with separators)
        self._init_palette_combo()   # sets_combo
        self._init_style_combo()     # style_combo (headers + options)

        # Per-section copy (header click + small Copy button)
        self._wire_copy_interactions()

        # Bootstrap state
        self._bootstrap()

        # Auto-reload assets every 5 min
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(AUTO_RELOAD_MS)
        self._timer.timeout.connect(self._auto_reload_assets)
        self._timer.start()

        # Window geometry from config (if your UI uses a top-level window)
        x, y, w, h = self.cfg.get("win_geometry", [180, 180, 1180, 860])
        try:
            self.ui.setGeometry(int(x), int(y), int(w), int(h))
        except Exception:
            pass

    # ---------- utilities ----------
    @staticmethod
    def _rand(cands: List[str]) -> str:
        if not cands:
            return ""
        choices = [c for c in cands if c.strip() != ""]
        return random.choice(choices) if choices else ""

    def _scheme(self) -> str:
        try:
            val = (self.ui.colors_combo.currentText() or "").strip()
        except Exception:
            return ""
        return "" if val.lower() in {"", "(none)", "none", "-", DEFAULT_SCHEME_SENTINEL.lower()} else val

    def _palette(self) -> str:
        try:
            s = (self.ui.sets_combo.currentText() or "").strip()
        except Exception:
            return ""
        return "" if (not s or s == DEFAULT_PALETTE_SENTINEL) else s

    def _selected_style(self) -> str:
        try:
            return (self.ui.style_combo.currentText() or "").strip()
        except Exception:
            return ""

    def _boxes(self):
        return (self.ui.out_cover, self.ui.out_letter, self.ui.out_back, self.ui.out_wall)

    # ---------- tail plumbing ----------
    @staticmethod
    def _split_base_and_tail(text: str) -> Tuple[str, str]:
        if not text:
            return "", ""
        idx = text.rfind("\nAdditionally:")
        if idx == -1:
            if text.startswith("Additionally:"):
                return "", text.strip()
            return text.rstrip(), ""
        return text[:idx].rstrip(), text[idx:].strip()

    def _helpful_sentence(self) -> str:
        p: List[str] = []
        if self.ui.cb_black.isChecked():   p.append("add a thin black border at the outer edge")
        elif self.ui.cb_white.isChecked(): p.append("add a thin white border at the outer edge")
        elif self.ui.cb_frame.isChecked(): p.append("add a decorative frame that does not crowd the subject")
        if self.ui.cb_forbid.isChecked():  p.append("forbid any text, captions, watermarks, or signatures")
        if self.ui.cb_detail.isChecked():  p.append("preserve fine detail and guard against banding, aliasing, and moiré")
        if self.ui.cb_real.isChecked():    p.append("bias rendering toward photorealistic materials and lighting")
        if self.ui.cb_paint.isChecked():   p.append("bias rendering toward painterly, illustrated mark-making")
        if not p:
            return ""
        s = p[0][0].upper() + p[0][1:]
        for frag in p[1:]:
            s += "; " + frag
        if not s.endswith("."):
            s += "."
        return s

    def _with_tail(self, base: str) -> str:
        tail = self._helpful_sentence()
        return base if not tail else f"{base}\n\nAdditionally: {tail}"

    def _apply_to_all_boxes(self, transform_base) -> None:
        for box in self._boxes():
            original = box.text.toPlainText()
            base, _tail = self._split_base_and_tail(original)
            new_base = transform_base(base)
            new_text = self._with_tail(new_base)
            if new_text != original:
                box.text.setPlainText(new_text)

    def _refresh_all_outputs(self) -> None:
        for box in self._boxes():
            base, _tail = self._split_base_and_tail(box.text.toPlainText())
            box.text.setPlainText(self._with_tail(base))

    # ---------- live: scheme/palette/style ----------
    @staticmethod
    def _rewrite_style_line_with_scheme(style_line: str, scheme: str) -> str:
        parts = [p.strip() for p in style_line.split(";") if p.strip()]
        parts = [p for p in parts if not p.lower().startswith("color scheme:")]
        if scheme:
            parts.append(f"Color scheme: {scheme}")
        return "; ".join(parts)

    def _apply_or_remove_scheme(self, base: str, scheme: str) -> str:
        b = (base or "").rstrip()
        if not b.strip():
            return f"Color scheme: {scheme}".rstrip() if scheme else ""
        if b.lower().strip().startswith("color scheme:"):
            return (f"Color scheme: {scheme}" if scheme else "")
        lines = b.splitlines()
        idx: Optional[int] = None
        for i, ln in enumerate(lines):
            if ("Style:" in ln) or ("Format:" in ln) or ("Color scheme:" in ln) or ("Art style:" in ln):
                idx = i
                break
        if idx is not None:
            lines[idx] = self._rewrite_style_line_with_scheme(lines[idx], scheme)
            return "\n".join(lines).rstrip()
        if scheme:
            insert_at = 1 if len(lines) >= 1 else 0
            lines.insert(insert_at, f"Color scheme: {scheme}")
            return "\n".join(lines).rstrip()
        return b

    def _on_scheme_changed(self, _txt: str) -> None:
        scheme = self._scheme()
        self._apply_to_all_boxes(lambda base: self._apply_or_remove_scheme(base, scheme))

    @staticmethod
    def _rewrite_or_insert_palette(base: str, palette: str) -> str:
        b = (base or "").rstrip()
        lines = b.splitlines() if b else []
        for i, ln in enumerate(lines):
            low = ln.lower()
            if low.startswith("occasion:") or low.startswith("color palette:") or low.startswith("letter subject:"):
                if palette:
                    lines[i] = f"Occasion: {palette}"
                else:
                    del lines[i]
                return "\n".join(lines).rstrip()
        if palette:
            insert_at = 1 if len(lines) >= 1 else 0
            lines.insert(insert_at, f"Occasion: {palette}")
            return "\n".join(lines).rstrip()
        return b

    def _on_palette_changed(self, _txt: str) -> None:
        palette = self._palette()
        self._apply_to_all_boxes(lambda base: self._rewrite_or_insert_palette(base, palette))
        if palette:
            for box in self._boxes():
                if not (box.text.toPlainText() or "").strip():
                    box.text.setPlainText(self._with_tail(f"Occasion: {palette}"))

    @staticmethod
    def _rewrite_or_insert_style(base: str, style: str) -> str:
        b = (base or "").rstrip()
        lines = b.splitlines() if b else []
        for i, ln in enumerate(lines):
            if ("Style:" in ln) or ("Art style:" in ln):
                parts = [p.strip() for p in ln.split(";") if p.strip()]
                found_idx = None
                for j, p in enumerate(parts):
                    pj = p.lower()
                    if pj.startswith("style:") or pj.startswith("art style:"):
                        found_idx = j
                        break
                if found_idx is not None:
                    if style:
                        parts[found_idx] = f"Style: {style}"
                    else:
                        del parts[found_idx]
                elif style:
                    parts.insert(0, f"Style: {style}")
                new_line = "; ".join(parts)
                if new_line:
                    lines[i] = new_line
                else:
                    del lines[i]
                return "\n".join(lines).rstrip()
        if style:
            insert_at = 1 if len(lines) >= 1 else 0
            lines.insert(insert_at, f"Style: {style}")
            return "\n".join(lines).rstrip()
        return b

    def _on_style_changed(self, _txt: str) -> None:
        style = self._selected_style()
        self._apply_to_all_boxes(lambda base: self._rewrite_or_insert_style(base, style))

    # ---------- init combos ----------
    def _init_palette_combo(self) -> None:
        combo = getattr(self.ui, "sets_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.setEditable(True)
        items = self.palettes if self.palettes else [DEFAULT_PALETTE_SENTINEL]
        combo.addItems(items)
        combo.setCurrentText(DEFAULT_PALETTE_SENTINEL)
        combo.blockSignals(False)
        combo.currentTextChanged.connect(self._on_palette_changed)
        if combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText(DEFAULT_PALETTE_SENTINEL)
            combo.lineEdit().editingFinished.connect(self._maybe_add_palette_from_editor)

    def _maybe_add_palette_from_editor(self) -> None:
        combo = self.ui.sets_combo
        text = (combo.currentText() or "").strip()
        if not text:
            combo.setCurrentText(DEFAULT_PALETTE_SENTINEL)
            return
        if text not in self.palettes:
            self.palettes.append(text)
            save_palettes(self.palettes)
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(self.palettes)
            combo.setCurrentText(text)
            combo.blockSignals(False)
        self._on_palette_changed(text)

    def _init_style_combo(self) -> None:
        """
        Build the 'Graphic & Illustration' combo from Type.txt:
          - Lines WITHOUT a leading dash are headers: bold + disabled/unselectable.
          - Lines WITH a leading dash ('- ' or '• ') are selectable options (dash stripped).
          - Blank lines are inert spacers.
          - Row 0 is empty/selectable to allow “no explicit style”.
        """
        sc = getattr(self.ui, "style_combo", None)
        if sc is None:
            return

        sc.blockSignals(True)
        sc.clear()

        model = QtGui.QStandardItemModel(sc)
        sc.setModel(model)

        # Row 0: allow 'no explicit style'
        none_item = QtGui.QStandardItem("")
        none_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        model.appendRow(none_item)

        for raw in self.type_raw:
            line = raw.rstrip("\r\n")

            # blank -> visual spacer (inert)
            if not line.strip():
                spacer = QtGui.QStandardItem(" ")
                spacer.setFlags(Qt.NoItemFlags)
                model.appendRow(spacer)
                continue

            stripped = line.lstrip()

            # selectable option if dash-prefixed
            if stripped.startswith(("- ", "• ")):
                label = stripped[2:].strip()
                if not label:
                    continue
                opt = QtGui.QStandardItem(label)
                opt.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                model.appendRow(opt)
                continue

            # header (bold, disabled)
            hdr = QtGui.QStandardItem(line.strip())
            f = hdr.font()
            f.setBold(True)
            hdr.setFont(f)
            hdr.setFlags(Qt.NoItemFlags)
            hdr.setForeground(QtGui.QBrush(QtGui.QColor("#cfefff")))
            model.appendRow(hdr)

        sc.setCurrentIndex(0)
        sc.blockSignals(False)
        sc.currentTextChanged.connect(self._on_style_changed)

        # Optional label rename if your UI label says "Art style"
        try:
            for lbl in sc.parent().findChildren(QtWidgets.QLabel):
                if lbl.text().strip().lower().startswith("art style"):
                    lbl.setText("Graphic & Illustration:")
                    break
        except Exception:
            pass

    def _init_scheme_combo(self) -> None:
        """
        Build Color scheme combo from color.txt:
          - Keep the first sentinel row '----'
          - Blank lines become visual separators (non-selectable)
        """
        cc = getattr(self.ui, "colors_combo", None)
        if cc is None:
            return

        current = (cc.currentText() or "").strip()
        cc.blockSignals(True)
        cc.clear()

        for item in self.schemes_raw:
            if (item or "").strip() == "":
                cc.insertSeparator(cc.count())
            else:
                cc.addItem(item)

        if current and any(current == (cc.itemText(i) or "") for i in range(cc.count())):
            cc.setCurrentText(current)
        else:
            idx = cc.findText(DEFAULT_SCHEME_SENTINEL)
            cc.setCurrentIndex(idx if idx >= 0 else 0)

        cc.blockSignals(False)
        cc.currentTextChanged.connect(self._on_scheme_changed)

    # ---------- helpful options ----------
    def _wire_helpful_options(self) -> None:
        def on_edge(changed: str, checked: bool):
            if checked:
                mapping = {"black": self.ui.cb_black, "white": self.ui.cb_white, "frame": self.ui.cb_frame}
                for k, cb in mapping.items():
                    if k != changed and cb.isChecked():
                        cb.blockSignals(True)
                        cb.setChecked(False)
                        cb.blockSignals(False)
            self._refresh_all_outputs()

        self.ui.cb_black.toggled.connect(lambda v: on_edge("black", v))
        self.ui.cb_white.toggled.connect(lambda v: on_edge("white", v))
        self.ui.cb_frame.toggled.connect(lambda v: on_edge("frame", v))
        for cb in (self.ui.cb_forbid, self.ui.cb_detail, self.ui.cb_real, self.ui.cb_paint):
            cb.toggled.connect(self._refresh_all_outputs)

    # ---------- copy wiring ----------
    def _wire_copy_interactions(self) -> None:
        boxes = [self.ui.out_cover, self.ui.out_letter, self.ui.out_back, self.ui.out_wall]
        for box in boxes:
            box.copy_btn.clicked.connect(lambda _=None, b=box: self._copy_text(b.text.toPlainText()))
            if hasattr(box, "header"):
                box.header.clicked.connect(lambda _=None, b=box: self._copy_text(b.text.toPlainText()))

    @staticmethod
    def _copy_text(text: str) -> None:
        QtWidgets.QApplication.clipboard().setText(text or "")
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Copied")

    # ---------- Rand All ----------
    def _on_rand_all(self) -> None:
        """
        Randomizes:
          - Occasion only if current is '----' or empty
          - Style (always)
          - Color Scheme (skip separators)
          - Helpful Options
          - Optional 'unify format' if present
        """
        # Occasion
        if not self._palette():
            if self.palettes and len(self.palettes) > 1:
                cand = self._rand(self.palettes[1:])  # skip '----'
                self.ui.sets_combo.blockSignals(True)
                self.ui.sets_combo.setCurrentText(cand)
                self.ui.sets_combo.blockSignals(False)
                self._on_palette_changed(cand)

        # Style
        sc = self.ui.style_combo
        if sc.model() and sc.model().rowCount() > 0:
            # pick any enabled/selectable row except headers/spacers
            selectable_rows = []
            m = sc.model()
            for r in range(m.rowCount()):
                idx = m.index(r, 0)
                if m.flags(idx) & Qt.ItemIsSelectable and (m.data(idx) or "") != "":
                    selectable_rows.append(r)
            if selectable_rows:
                sc.blockSignals(True)
                sc.setCurrentIndex(random.choice(selectable_rows))
                sc.blockSignals(False)
                self._on_style_changed(sc.currentText())

        # Color scheme (skip blank/separators)
        combo = self.ui.colors_combo
        if combo.count() > 0:
            for _ in range(24):
                i = random.randrange(0, combo.count())
                if (combo.itemText(i) or "").strip() != "":
                    combo.setCurrentIndex(i)
                    break

        # Helpful options
        def set_cb(cb: QtWidgets.QCheckBox, state: bool):
            cb.blockSignals(True)
            cb.setChecked(state)
            cb.blockSignals(False)

        edge_choice = random.choice(["none", "black", "white", "frame"])
        set_cb(self.ui.cb_black, edge_choice == "black")
        set_cb(self.ui.cb_white, edge_choice == "white")
        set_cb(self.ui.cb_frame, edge_choice == "frame")
        set_cb(self.ui.cb_forbid, bool(random.getrandbits(1)))
        set_cb(self.ui.cb_detail, bool(random.getrandbits(1)))
        set_cb(self.ui.cb_real,   bool(random.getrandbits(1)))
        set_cb(self.ui.cb_paint,  bool(random.getrandbits(1)))

        # Optional unify-format checkbox (safe if not present)
        cb_unify = getattr(self.ui, "cb_ustyle", None)
        if isinstance(cb_unify, QtWidgets.QCheckBox):
            set_cb(cb_unify, bool(random.getrandbits(1)))

        self._refresh_all_outputs()

    # ---------- erase all ----------
    def on_erase_all(self) -> None:
        """
        Wipes everything the user would expect:
          - Clears inputs (vision + per-image briefs)
          - Clears outputs
          - Resets Occasion to '----'
          - Resets Style to empty
          - Resets Color Scheme to '----'
          - Unchecks Helpful Options (and unify-format if present)
        """
        # Inputs
        self.ui.common_edit.clear()
        self.ui.cover_edit.clear()
        self.ui.letter_edit.clear()
        self.ui.back_edit.clear()
        self.ui.wall_edit.clear()

        # Outputs
        self.ui.out_cover.text.clear()
        self.ui.out_letter.text.clear()
        self.ui.out_back.text.clear()
        self.ui.out_wall.text.clear()

        # Occasion
        try:
            combo = self.ui.sets_combo
            combo.blockSignals(True)
            combo.setCurrentText(DEFAULT_PALETTE_SENTINEL)
            combo.blockSignals(False)
            self._on_palette_changed(combo.currentText())
        except Exception:
            pass

        # Style (first item is empty)
        try:
            sc = self.ui.style_combo
            sc.blockSignals(True)
            sc.setCurrentIndex(0)
            sc.blockSignals(False)
            self._on_style_changed(sc.currentText())
        except Exception:
            pass

        # Color scheme → sentinel
        try:
            cc = self.ui.colors_combo
            cc.blockSignals(True)
            idx = cc.findText(DEFAULT_SCHEME_SENTINEL)
            cc.setCurrentIndex(idx if idx >= 0 else 0)
            cc.blockSignals(False)
            self._on_scheme_changed(cc.currentText())
        except Exception:
            pass

        # Options
        def _set_cb(cb: QtWidgets.QCheckBox, state: bool):
            cb.blockSignals(True)
            cb.setChecked(state)
            cb.blockSignals(False)
        try:
            _set_cb(self.ui.cb_black, False)
            _set_cb(self.ui.cb_white, False)
            _set_cb(self.ui.cb_frame, False)
            _set_cb(self.ui.cb_forbid, False)
            _set_cb(self.ui.cb_detail, False)
            _set_cb(self.ui.cb_real,   False)
            _set_cb(self.ui.cb_paint,  False)
            cb_unify = getattr(self.ui, "cb_ustyle", None)
            if isinstance(cb_unify, QtWidgets.QCheckBox):
                _set_cb(cb_unify, False)
        except Exception:
            pass

        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "All cleared")

    # ---------- generate ----------
    def on_generate(self) -> None:
        """
        Build prompts for each section using current selections/briefs.
        (This preserves your existing “vision + 4 boxes” behavior.)
        """
        role   = self._pick_first(self.roles)
        effort = self._pick_first(self.efforts)
        order  = self._pick_first(self.orders)

        style  = self._selected_style()
        scheme = self._scheme()
        pal    = self._palette()

        visual_format, per_output_extra = self._pick_format_and_tail()

        common = (self.ui.common_edit.toPlainText() or "").strip()
        cover  = (self.ui.cover_edit.toPlainText()  or "").strip()
        letter = (self.ui.letter_edit.toPlainText() or "").strip()
        back   = (self.ui.back_edit.toPlainText()   or "").strip()
        wall   = (self.ui.wall_edit.toPlainText()   or "").strip()

        # per-output briefs combine with the common vision
        def combine(brief: str) -> str:
            return (brief if brief else common) if not common else (f"{common}\n\n{brief}" if brief else common)

        global_constraints = self.global_constraints

        self.ui.out_cover.text.setPlainText(self._with_tail(build_prompt(
            role, effort, order, combine(cover), style, scheme, pal, visual_format, per_output_extra, global_constraints
        )))
        self.ui.out_letter.text.setPlainText(self._with_tail(build_prompt(
            role, effort, order, combine(letter), style, scheme, pal, visual_format, per_output_extra, global_constraints
        )))
        self.ui.out_back.text.setPlainText(self._with_tail(build_prompt(
            role, effort, order, combine(back), style, scheme, pal, visual_format, per_output_extra, global_constraints
        )))
        self.ui.out_wall.text.setPlainText(self._with_tail(build_prompt(
            role, effort, order, combine(wall), style, scheme, pal, visual_format, per_output_extra, global_constraints
        )))

    # ---------- export / copy ----------
    def copy_all_to_clipboard(self) -> None:
        parts = [
            self.ui.out_cover.text.toPlainText(),
            self.ui.out_letter.text.toPlainText(),
            self.ui.out_back.text.toPlainText(),
            self.ui.out_wall.text.toPlainText(),
        ]
        QtWidgets.QApplication.clipboard().setText("\n\n" + ("\n\n" + ("-" * 40) + "\n\n").join(parts))
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Copied all")

    # ---------- helpers ----------
    @staticmethod
    def _pick_first(items: List[str]) -> str:
        return items[0] if items else ""

    def _pick_format_and_tail(self) -> Tuple[str, str]:
        """
        Your format.txt may mix short format hints and longer constraint blocks.
        We pick the first short one as 'visual_format' and concatenate any long ones.
        """
        visual = ""
        tails: List[str] = []
        for raw in self.formats:
            fmt, extra = split_format_or_constraints(raw)
            if fmt and not visual:
                visual = fmt
            if extra:
                tails.append(extra)
        return visual, ("\n".join(tails).strip() if tails else "")

    # ---------- bootstrap & auto-reload ----------
    def _bootstrap(self) -> None:
        pal = self._palette()
        if pal:
            for box in self._boxes():
                base, _ = self._split_base_and_tail(box.text.toPlainText())
                if not base.strip():
                    box.text.setPlainText(self._with_tail(f"Occasion: {pal}"))
        self._on_scheme_changed(self._scheme())
        self._on_style_changed(self._selected_style())

    def _auto_reload_assets(self) -> None:
        # Reload lists from canonical files
        self.roles      = read_lines_simple(MODULES_DIR / "Role.txt")
        self.efforts    = read_lines_simple(MODULES_DIR / "effort.txt")
        self.orders     = read_lines_simple(MODULES_DIR / "order.txt")
        self.formats    = read_lines_simple(MODULES_DIR / "format.txt")
        self.schemes_raw = load_color_schemes_raw()
        self.type_raw    = parse_type_lines(TYPE_STYLES_PATH)

        current_palette = self._palette()
        current_style   = self._selected_style()
        current_scheme  = self._scheme()
        self.palettes   = load_palettes()

        # palette combo preserve selection (sets_combo)
        combo = getattr(self.ui, "sets_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.clear()
            combo.setEditable(True)
            combo.addItems(self.palettes if self.palettes else [DEFAULT_PALETTE_SENTINEL])
            combo.setCurrentText(current_palette if current_palette else DEFAULT_PALETTE_SENTINEL)
            combo.blockSignals(False)

        # style combo preserve selection (rebuild model)
        sc = getattr(self.ui, "style_combo", None)
        if sc is not None:
            prev = current_style
            self._init_style_combo()
            if prev:
                # try to reselect if present
                m = sc.model()
                for r in range(m.rowCount()):
                    idx = m.index(r, 0)
                    if (m.flags(idx) & Qt.ItemIsSelectable) and (idx.data() == prev):
                        sc.setCurrentIndex(r)
                        break

        # scheme combo preserve selection
        cc = getattr(self.ui, "colors_combo", None)
        if cc is not None:
            cc.blockSignals(True)
            cc.clear()
            for item in self.schemes_raw:
                if (item or "").strip() == "":
                    cc.insertSeparator(cc.count())
                else:
                    cc.addItem(item)
            if current_scheme and any(current_scheme == (cc.itemText(i) or "") for i in range(cc.count())):
                cc.setCurrentText(current_scheme)
            else:
                idx = cc.findText(DEFAULT_SCHEME_SENTINEL)
                cc.setCurrentIndex(idx if idx >= 0 else 0)
            cc.blockSignals(False)

        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Lists reloaded")
