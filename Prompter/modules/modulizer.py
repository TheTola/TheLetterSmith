# Create modulizer.py with robust parsing + Qt combo application utilities.
from pathlib import Path

modulizer_code = r'''# -*- coding: utf-8 -*-
# File: modulizer.py — Single source of truth for parsing "modules" *.txt lists
# Author: Letter Smith toolkit
#
# PURPOSE
#   Parse and normalize the three core list files used by the Prompter:
#     - type.txt   (headers + bullet/dash selectable items + blank-line separators)
#     - topic.txt  (simple selectable list; may contain blanks we keep as visual spacers)
#     - color.txt  (simple selectable list; blank lines preserved as spacers)
#   …and provide one function to apply them to Qt combo boxes exactly as specified,
#   so the UI code never has to fuss with formatting again.
#
# SPEC (requested behavior)
#   • TYPE (Graphics & Illustration styles):
#       - The first non-empty, non-comment line is a HEADER: bold + LARGER font.
#       - Any non-empty line WITHOUT a leading bullet/dash ('- ', '• ', '– ') is a HEADER
#         (unclickable). A trailing colon on headers is allowed and will be trimmed.
#       - Any line that STARTS WITH a bullet/dash ('- ', '• ', '– ') is a SELECTABLE OPTION.
#       - Blank lines render as VISIBLE empty lines in the dropdown (unclickable).
#   • TOPIC:
#       - Simple selectable list. Blank lines are preserved as non-selectable spacers.
#       - You may append new topics programmatically; this module includes add_topic().
#   • COLOR:
#       - Simple selectable list. Blank lines preserved as spacers.
#
# OPTIONALS
#   • add_none (False by default) can be used to insert a top "— none —" option for any list.
#
# Qt Notes
#   We build a QStandardItemModel to give headers bold text (and bigger for the very first header),
#   make spacers unselectable, and options selectable. We intentionally do NOT use
#   QComboBox.insertSeparator() because the spec asks for empty lines, not a horizontal rule.
#
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple, Iterable
from pathlib import Path

NONE_OPTION = "— none —"

# ─────────────────────────────────────────────────────────────
# Data model returned by all parsers
# ─────────────────────────────────────────────────────────────
@dataclass
class ComboItem:
    text: str
    separator: bool = False
    selectable: bool = True   # False for headers/spacers
    bold: bool = False        # True for headers
    color: Optional[str] = None  # e.g., "#cfefff" for headers

    @classmethod
    def header(cls, text: str, color: Optional[str] = "#cfefff", bold: bool = True) -> "ComboItem":
        return cls(text=text, separator=False, selectable=False, bold=bold, color=color)

    @classmethod
    def option(cls, text: str) -> "ComboItem":
        return cls(text=text, separator=False, selectable=True, bold=False, color=None)

    @classmethod
    def spacer(cls) -> "ComboItem":
        return cls(text=" ", separator=True, selectable=False, bold=False, color=None)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _is_bullet_start(s: str) -> bool:
    # Accept: "- ", "• ", "– " (en dash + space)
    return s.startswith("- ") or s.startswith("• ") or s.startswith("– ")


def _strip_bullet(s: str) -> str:
    if _is_bullet_start(s):
        return s[2:].strip()
    return s


def _strip_comment(line: str) -> str:
    s = line.rstrip("\n\r")
    # Full-line comments
    raw = s.strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return "" if raw.startswith("#") or raw.startswith("//") else s
    # Trailing comment markers (only if preceded by whitespace)
    for marker in (" #", " //", "\t#", "\t//"):
        idx = s.find(marker)
        if idx != -1:
            # Keep left part
            s = s[:idx].rstrip()
            break
    return s


def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return []


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# ─────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────
def parse_type_lines(lines: List[str], *, add_none: bool = False) -> List[ComboItem]:
    """
    Convert type.txt semantics into ComboItems following the spec.
    """
    items: List[ComboItem] = []
    first_header_applied = False

    if add_none:
        items.append(ComboItem.option(NONE_OPTION))

    for raw in lines:
        s = _strip_comment(raw).rstrip()
        # Preserve exact blank lines as spacers
        if s.strip() == "":
            items.append(ComboItem.spacer())
            continue

        # Bullet/dash option?
        if _is_bullet_start(s.strip()):
            label = _strip_bullet(s.strip())
            if label:
                items.append(ComboItem.option(label))
            continue

        # Otherwise it's a header; trim trailing ":" and whitespace
        hdr = s.strip()
        if hdr.endswith(":"):
            hdr = hdr[:-1].rstrip()
        if hdr:
            ci = ComboItem.header(hdr)
            # Mark the first header specially using a sentinel on text (handled in Qt apply)
            if not first_header_applied:
                ci.color = (ci.color or "#cfefff")  # keep color; bigger font applied later
                # We'll tag it by prefix to detect later (without polluting text content)
                ci.text = ci.text  # unchanged; enlargement handled in apply_to_combo
                first_header_applied = True
            items.append(ci)

    return items


def parse_topic_lines(lines: List[str], *, add_none: bool = False) -> List[ComboItem]:
    """
    Simple selectable list; preserve blank lines as spacers.
    """
    items: List[ComboItem] = []
    if add_none:
        items.append(ComboItem.option(NONE_OPTION))

    for raw in lines:
        s = _strip_comment(raw)
        if s.strip() == "":
            items.append(ComboItem.spacer())
            continue
        label = s.strip()
        if label:
            items.append(ComboItem.option(label))

    return items


def parse_color_lines(lines: List[str], *, add_none: bool = False) -> List[ComboItem]:
    """
    Simple selectable list; preserve blank lines as spacers.
    """
    # Reuse topic behavior
    return parse_topic_lines(lines, add_none=add_none)


# ─────────────────────────────────────────────────────────────
# Modules directory helpers
# ─────────────────────────────────────────────────────────────
def find_modules_dir(start: Path | str) -> Path:
    """
    Find the Prompter/modules directory starting from 'start' (file or dir).
    Tries:
      - <start>/Prompter/modules
      - <start>/modules
      - <start.parent>/Prompter/modules
      - <project_root>/Prompter/modules (when start is inside project)
    Falls back to <start>/Prompter/modules (even if missing).
    """
    start = Path(start).resolve()
    if start.is_file():
        base = start.parent
    else:
        base = start

    candidates = [
        base / "Prompter" / "modules",
        base / "modules",
        base.parent / "Prompter" / "modules",
    ]

    for c in candidates:
        if c.exists() and c.is_dir():
            return c

    # Fallback: best guess
    return base / "Prompter" / "modules"


def load_modules(modules_dir: Path | str) -> Dict[str, List[str]]:
    """
    Read raw text lines from the three canonical files (missing OK).
    Returns dict with keys: 'type', 'topic', 'color'.
    """
    modules_dir = Path(modules_dir)
    return {
        "type": _read_lines(modules_dir / "type.txt"),
        "topic": _read_lines(modules_dir / "topic.txt"),
        "color": _read_lines(modules_dir / "color.txt"),
    }


# ─────────────────────────────────────────────────────────────
# Topic writer
# ─────────────────────────────────────────────────────────────
def add_topic(modules_dir: Path | str, new_topic: str) -> bool:
    """
    Append a new topic to modules/topic.txt if it's not present (case-insensitive).
    Preserves file and keeps a trailing newline. Returns True if written.
    Blank/whitespace-only entries are ignored.
    """
    new_topic = (new_topic or "").strip()
    if not new_topic:
        return False

    modules_dir = Path(modules_dir)
    path = modules_dir / "topic.txt"

    lines = _read_lines(path) if path.exists() else []
    # Keep empties as they are; de-duplicate on non-empty entries only
    normalized = [ln.strip() for ln in lines if ln.strip()]
    if new_topic.casefold() in {t.casefold() for t in normalized}:
        return False

    # Ensure file exists
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        # If file not empty and last line not blank, add newline first
        if lines and not lines[-1].endswith(("\n", "\r")):
            f.write("\n")
        f.write(f"{new_topic}\n")
    return True


# ─────────────────────────────────────────────────────────────
# Qt: apply parsed items to a QComboBox
# ─────────────────────────────────────────────────────────────
def apply_to_combo(combo, items: List[ComboItem], *, enlarge_first_header: bool = True) -> None:
    """
    Build a QStandardItemModel for QComboBox honoring headers, options, and spacers.
    - Headers: bold, unselectable, colored (default '#cfefff'); FIRST header gets a larger font.
    - Spacers: show as a blank row (unclickable). We set a size hint so the line is visible.
    - Options: normal selectable rows.
    """
    from PySide6 import QtGui, QtCore
    from PySide6.QtCore import Qt

    model = QtGui.QStandardItemModel(combo)
    combo.setModel(model)
    model.clear()

    first_header_done = False
    base_font = combo.font()
    base_size = base_font.pointSize() if base_font.pointSize() > 0 else 11

    for it in items:
        if it.separator:
            row = QtGui.QStandardItem(" ")
            row.setFlags(QtCore.Qt.NoItemFlags)
            # Make spacer visually present (taller than 0)
            row.setData(QtCore.QSize(-1, max(8, int(base_size * 0.8))), QtCore.Qt.SizeHintRole)
            model.appendRow(row)
            continue

        row = QtGui.QStandardItem(it.text)
        if not it.selectable:
            # Header
            f = base_font
            f.setBold(True or it.bold)
            if enlarge_first_header and not first_header_done:
                f.setPointSize(base_size + 3)  # bump up visibly
                first_header_done = True
            row.setFont(f)
            if it.color:
                row.setForeground(QtGui.QBrush(QtGui.QColor(it.color)))
            row.setFlags(QtCore.Qt.NoItemFlags)  # not selectable
        else:
            # Option
            row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        model.appendRow(row)

    # Ensure currentIndex lands on a selectable row if any
    for r in range(model.rowCount()):
        idx = model.index(r, 0)
        fl = model.flags(idx)
        if (fl & Qt.ItemIsEnabled) and (fl & Qt.ItemIsSelectable):
            combo.setCurrentIndex(r)
            break


# ─────────────────────────────────────────────────────────────
# High-level helpers: parse and apply in one call
# ─────────────────────────────────────────────────────────────
def populate_prompt_maker(ui, modules_dir: Path | str, *, add_none: bool = False) -> Tuple[int, int, int]:
    """
    Parse all three lists from the modules folder and apply to a PromptMaker-like UI:
        ui.type_combo, ui.subj_combo (topics), ui.colors_combo.
    Returns tuple of counts (subjects, types, colors) based on raw line counts.
    """
    mods = load_modules(modules_dir)
    type_items  = parse_type_lines(mods["type"], add_none=add_none)
    topic_items = parse_topic_lines(mods["topic"], add_none=add_none)
    color_items = parse_color_lines(mods["color"], add_none=add_none)

    apply_to_combo(ui.type_combo, type_items, enlarge_first_header=True)
    apply_to_combo(ui.subj_combo, topic_items, enlarge_first_header=False)
    apply_to_combo(ui.colors_combo, color_items, enlarge_first_header=False)

    return (len([l for l in mods["topic"] if l is not None]),
            len([l for l in mods["type"] if l is not None]),
            len([l for l in mods["color"] if l is not None]))


# ─────────────────────────────────────────────────────────────
# CLI quick test (optional)
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Minimal smoketest: build an ephemeral combo-only window to visualize parsing.
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    # Resolve modules folder relative to this file
    guess = find_modules_dir(Path(__file__).resolve().parent)

    # Load
    src = load_modules(guess)

    # Compose window
    w = QtWidgets.QWidget()
    w.setWindowTitle("modulizer smoketest")
    lay = QtWidgets.QFormLayout(w)

    combo_type = QtWidgets.QComboBox()
    combo_topic = QtWidgets.QComboBox()
    combo_color = QtWidgets.QComboBox()

    apply_to_combo(combo_type,  parse_type_lines(src["type"], add_none=False))
    apply_to_combo(combo_topic, parse_topic_lines(src["topic"], add_none=False), enlarge_first_header=False)
    apply_to_combo(combo_color, parse_color_lines(src["color"], add_none=False), enlarge_first_header=False)

    lay.addRow("Type:", combo_type)
    lay.addRow("Topic:", combo_topic)
    lay.addRow("Color:", combo_color)

    w.resize(520, 300)
    w.show()
    app.exec()
'''
Path('/mnt/data/modulizer.py').write_text(modulizer_code, encoding='utf-8')
print("Saved modulizer.py to /mnt/data/modulizer.py")
