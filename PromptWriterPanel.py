# PromptWriterPanel.py
# Prompt Writer for eLetter — generates four separate prompts (cover, letter, wall, back)
# Windowed layout (stacked "windows"), per-window copy buttons, global Copy All.

from __future__ import annotations

import sys
import json
import random
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Callable

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
PROMPT_WRITER_STATE_VERSION = 2
MAX_STATE_TEXT_LENGTH = 24000
REFERENCE_IMAGE_MAX_COUNT = 3
REFERENCE_IMAGE_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
REFERENCE_IMAGE_ROLES: Tuple[str, ...] = (
    "Person Reference",
    "Style Reference",
    "Outfit Reference",
    "Location Reference",
    "Mood Reference",
    "Object Reference",
    "Other Reference",
)
DEFAULT_REFERENCE_IMAGE_ROLE = "Person Reference"


# =========================
# COLOR SYSTEM (UI + Prompt Preview)
# =========================

COL_TYPE = "#ff453a"       # GRAPHICS & ILLUSTRATION / CORAL RED
COL_SUBJECT = "#ff2d95"    # SUBJECT / HOT PINK
COL_SCHEME = "#30d158"     # COLOR SCHEME / GREEN
COL_HELPFUL = "#64d2ff"    # HELPFUL OPTIONS / CYAN
COL_GLOBAL = "#bf5af2"     # APPLY TO ALL / PURPLE

COL_COVER = "#ffd60a"      # COVER / YELLOW
COL_LETTER = "#a3ff12"     # LETTER / LIME
COL_WALL = "#0a84ff"       # WALL / STRONG BLUE
COL_BACK = "#ff9f0a"       # BACK / AMBER

COL_CHECK = "#005dff"
COL_CHECK_MARK = "#ffd60a"
COL_HEADER_TEXT = "#365be8"  # DARK CATEGORY BLUE, SLIGHTLY BRIGHTER THAN ROYAL BLUE
MANAGED_LIST_HEADER_ROLE = Qt.UserRole + 41
NONE_CHOICE_LABEL = "— none —"
USER_ADDED_HEADER = "User Added"

COLOR_GROUP_HEADERS: Tuple[str, ...] = (
    "Strange / Interpretive Color Schemes",
    "Themed / Occasion Color Schemes",
    "Basic Single-Color Schemes",
    "Two-Color Combinations",
    "Three-Color / Multi-Color Combinations",
)

COMMON_ENTRY_FIXES: Dict[str, str] = {
    "valentines": "Valentine's",
    "valentine day": "Valentine's Day",
    "valentines day": "Valentine's Day",
    "anime": "Anime",
    "ai": "AI",
    "sci fi": "Sci-Fi",
    "scifi": "Sci-Fi",
    "color": "Color",
    "colour": "Color",
}

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


@dataclass(frozen=True)
class ReferenceImage:
    id: str
    path: str
    filename: str
    role: str = DEFAULT_REFERENCE_IMAGE_ROLE
    added_at: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ManagedListEntry:
    text: str
    is_header: bool = False


class HeaderAwareItemDelegate(QtWidgets.QStyledItemDelegate):
    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        if bool(index.data(MANAGED_LIST_HEADER_ROLE)):
            painter.save()
            rect = option.rect.adjusted(10, 0, -8, 0)
            font = QtGui.QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(COL_HEADER_TEXT))
            painter.drawText(rect, Qt.AlignVCenter | Qt.TextSingleLine, index.data(Qt.DisplayRole) or "")
            painter.restore()
            return
        super().paint(painter, option, index)


class ListManagerDialog(QtWidgets.QDialog):
    entries_changed = QtCore.Signal(list)

    def __init__(
        self,
        *,
        title: str,
        entries: List[ManagedListEntry],
        allow_headers: bool = False,
        auto_user_header: Optional[str] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._entries: List[ManagedListEntry] = [ManagedListEntry(entry.text, entry.is_header) for entry in entries]
        self._allow_headers = bool(allow_headers)
        self._auto_user_header = _normalize_text(auto_user_header, strip=True, max_length=120)
        self.setWindowTitle(f"Manage {title}")
        self.setModal(False)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        screen = (parent.screen() if parent else QtGui.QGuiApplication.primaryScreen())
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 720)
        self._available_geometry = available
        default_height = max(260, min(available.height() - 240, 360))

        self.resize(500, default_height)
        self.setMinimumSize(400, 260)
        self.setMaximumHeight(default_height)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list_widget.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        root.addWidget(self.list_widget, 1)

        form = QtWidgets.QHBoxLayout()
        form.setSpacing(8)
        self.entry_edit = QtWidgets.QLineEdit(self)
        self.entry_edit.setPlaceholderText("Entry text")
        form.addWidget(self.entry_edit, 1)
        root.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)
        self.btn_add = QtWidgets.QPushButton("Add", self)
        self.btn_update = QtWidgets.QPushButton("Update", self)
        self.btn_remove = QtWidgets.QPushButton("Remove", self)
        button_row.addWidget(self.btn_add)
        button_row.addWidget(self.btn_update)
        button_row.addWidget(self.btn_remove)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.list_widget.currentRowChanged.connect(self._sync_editor_from_selection)
        self.list_widget.itemDoubleClicked.connect(lambda *_: self.entry_edit.setFocus())
        self.btn_add.clicked.connect(self._add_entry)
        self.btn_update.clicked.connect(self._update_entry)
        self.btn_remove.clicked.connect(self._remove_entry)
        self.entry_edit.returnPressed.connect(self._submit_from_enter)

        self.setStyleSheet(
            """
            QDialog {
                background: #12161d;
                border: 1px solid #283244;
                border-radius: 10px;
            }
            QLabel {
                color: #dce8f4;
            }
            QListWidget {
                background: #0b0f15;
                color: #ebf2ff;
                border: 1px solid #263142;
                border-radius: 8px;
                padding: 4px;
            }
            QLineEdit {
                background: #0b0f15;
                color: #ebf2ff;
                border: 1px solid #263142;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QPushButton {
                background: transparent;
                color: #ebf2ff;
                border: 1px solid #31435e;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(59, 124, 240, 0.12);
                border-color: #4d8dff;
            }
            """
        )

        self._refresh_list()
        self._sync_button_state()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self.isVisible() and event.type() == QtCore.QEvent.MouseButtonPress:
            try:
                pos = event.globalPosition().toPoint()
            except Exception:
                try:
                    pos = event.globalPos()
                except Exception:
                    pos = None
            if pos is not None and not self.frameGeometry().contains(pos):
                self.entry_edit.clear()
                self.close()
                return False
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.entry_edit.clear()
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def _selected_row(self) -> int:
        return self.list_widget.currentRow()

    def _selected_entry(self) -> Optional[ManagedListEntry]:
        row = self._selected_row()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _current_form_entry(self) -> Optional[ManagedListEntry]:
        text = _clean_user_added_entry(self.entry_edit.text())
        if not text:
            return None
        selected = self._selected_entry()
        return ManagedListEntry(text=text, is_header=bool(selected.is_header) if selected else False)

    def _is_selectable_row(self, row: int) -> bool:
        return 0 <= row < len(self._entries) and not self._entries[row].is_header

    def _nearest_selectable_row(self, preferred_row: int) -> int:
        if self._is_selectable_row(preferred_row):
            return preferred_row
        for offset in range(1, len(self._entries) + 1):
            forward = preferred_row + offset
            if self._is_selectable_row(forward):
                return forward
            backward = preferred_row - offset
            if self._is_selectable_row(backward):
                return backward
        return -1

    def _header_index(self) -> int:
        if not self._allow_headers or not self._auto_user_header:
            return -1
        for index, entry in enumerate(self._entries):
            if entry.is_header and entry.text.casefold() == self._auto_user_header.casefold():
                return index
        return -1

    def _insert_under_auto_header(self, entry: ManagedListEntry) -> int:
        header_index = self._header_index()
        if header_index < 0:
            self._entries.append(ManagedListEntry(self._auto_user_header, True))
            self._entries.append(entry)
            return len(self._entries) - 1

        insert_at = header_index + 1
        while insert_at < len(self._entries) and not self._entries[insert_at].is_header:
            insert_at += 1
        self._entries.insert(insert_at, entry)
        return insert_at

    def _cleanup_auto_header(self) -> None:
        header_index = self._header_index()
        if header_index < 0:
            return
        next_index = header_index + 1
        if next_index >= len(self._entries) or self._entries[next_index].is_header:
            del self._entries[header_index]

    def _position_within_screen(self) -> None:
        available = self._available_geometry
        parent = self.parentWidget()
        center = parent.frameGeometry().center() if parent is not None and parent.isVisible() else available.center()
        geo = self.frameGeometry()
        geo.moveCenter(center)

        max_x = available.left() + max(0, available.width() - geo.width())
        max_y = available.top() + max(0, available.height() - geo.height())
        self.move(
            max(available.left(), min(geo.x(), max_x)),
            max(available.top(), min(geo.y(), max_y)),
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._position_within_screen)

    def _refresh_list(self, preferred_row: Optional[int] = None) -> None:
        current_row = self._selected_row() if preferred_row is None else preferred_row
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self._entries:
            item = QtWidgets.QListWidgetItem(entry.text)
            item.setData(MANAGED_LIST_HEADER_ROLE, entry.is_header)
            if entry.is_header:
                item.setFlags(Qt.ItemIsEnabled)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(QColor(COL_HEADER_TEXT))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        if self._entries:
            row = self._nearest_selectable_row(max(0, min(current_row, len(self._entries) - 1)))
            if row >= 0:
                self.list_widget.setCurrentRow(row)
            else:
                self.list_widget.clearSelection()
                self.entry_edit.clear()
        else:
            self.entry_edit.clear()
        self._sync_button_state()

    def _sync_editor_from_selection(self, row: int) -> None:
        if self._is_selectable_row(row):
            entry = self._entries[row]
            self.entry_edit.setText(entry.text)
        else:
            self.entry_edit.clear()
        self._sync_button_state()

    def _sync_button_state(self) -> None:
        has_selection = self._is_selectable_row(self._selected_row())
        self.btn_update.setEnabled(has_selection)
        self.btn_remove.setEnabled(has_selection)

    def _emit_entries_changed(self) -> None:
        self.entries_changed.emit([ManagedListEntry(entry.text, entry.is_header) for entry in self._entries])

    def _submit_from_enter(self) -> None:
        if self._is_selectable_row(self._selected_row()):
            self._update_entry()
        else:
            self._add_entry()

    def _add_entry(self) -> None:
        entry = self._current_form_entry()
        if entry is None:
            return
        if self._allow_headers and self._auto_user_header:
            insert_at = self._insert_under_auto_header(ManagedListEntry(entry.text, False))
        else:
            row = self._selected_row()
            insert_at = row + 1 if row >= 0 else len(self._entries)
            self._entries.insert(insert_at, ManagedListEntry(entry.text, False))
        self._refresh_list(insert_at)
        self._emit_entries_changed()

    def _update_entry(self) -> None:
        row = self._selected_row()
        entry = self._current_form_entry()
        if row < 0 or entry is None:
            return
        self._entries[row] = entry
        self._refresh_list(row)
        self._emit_entries_changed()

    def _remove_entry(self) -> None:
        row = self._selected_row()
        if not self._is_selectable_row(row):
            return
        del self._entries[row]
        self._cleanup_auto_header()
        next_row = min(row, len(self._entries) - 1)
        self._refresh_list(next_row)
        self._emit_entries_changed()


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


def _normalize_reference_role(value: object) -> str:
    role = _normalize_text(value, strip=True, max_length=80)
    return role if role in REFERENCE_IMAGE_ROLES else DEFAULT_REFERENCE_IMAGE_ROLE


def _reference_image_extension(path_or_name: object) -> str:
    text = _normalize_text(path_or_name, strip=True, max_length=1024)
    return Path(text).suffix.lower() if text else ""


def _reference_image_exists(ref: ReferenceImage) -> bool:
    try:
        return bool(ref.path) and Path(ref.path).exists()
    except Exception:
        return False


def _clean_user_added_entry(value: object) -> str:
    text = _normalize_text(value, strip=True, max_length=300)
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" / ", " / ").replace("&", "&")

    lower = text.casefold()
    for wrong, fixed in COMMON_ENTRY_FIXES.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", fixed, text, flags=re.IGNORECASE)

    small_words = {"and", "or", "of", "the", "a", "an", "to", "in", "with", "for", "on", "at", "by"}

    def fix_word(word: str, *, first: bool = False) -> str:
        if not word:
            return word
        if any(ch.isdigit() for ch in word) or word.isupper():
            return word
        if word.casefold() in {v.casefold() for v in COMMON_ENTRY_FIXES.values()}:
            for fixed in COMMON_ENTRY_FIXES.values():
                if word.casefold() == fixed.casefold():
                    return fixed
        if not first and word.casefold() in small_words:
            return word.casefold()
        if "-" in word:
            return "-".join(fix_word(part, first=True) for part in word.split("-"))
        return word[:1].upper() + word[1:].lower()

    tokens = re.split(r"(\s+|/|&|,|:)", text)
    seen_word = False
    out: List[str] = []
    for token in tokens:
        if not token or re.fullmatch(r"\s+|/|&|,|:", token):
            out.append(token)
            continue
        out.append(fix_word(token, first=not seen_word))
        seen_word = True
    return "".join(out).strip()


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


def _clean_header_text(line: str) -> str:
    text = _normalize_text(line, strip=True, max_length=300)
    if not text:
        return ""
    if text.startswith("-"):
        text = text[1:].strip()
    elif text.startswith("•"):
        text = text[1:].strip()
    return text


def _parse_managed_list_entries(lines: List[str], *, allow_headers: bool) -> List[ManagedListEntry]:
    entries: List[ManagedListEntry] = []
    for raw_line in lines:
        stripped = _normalize_text(raw_line, strip=True, max_length=300)
        if not stripped:
            continue
        if stripped.startswith("-") or stripped.startswith("•"):
            if not allow_headers:
                continue
            header_text = _clean_header_text(stripped)
            if header_text:
                entries.append(ManagedListEntry(text=header_text, is_header=True))
            continue
        item_text = _clean_choice_line(stripped)
        if item_text:
            entries.append(ManagedListEntry(text=item_text, is_header=False))
    return entries




def _parse_color_list_entries(lines: List[str]) -> List[ManagedListEntry]:
    entries: List[ManagedListEntry] = []
    groups: List[Tuple[Optional[str], List[ManagedListEntry]]] = []
    current_header: Optional[str] = None
    current_items: List[ManagedListEntry] = []

    def flush() -> None:
        nonlocal current_header, current_items
        if current_header or current_items:
            groups.append((current_header, list(current_items)))
        current_header = None
        current_items = []

    for raw_line in lines:
        stripped = _normalize_text(raw_line, strip=True, max_length=300)
        if not stripped:
            flush()
            continue
        if stripped.startswith("-") or stripped.startswith("•"):
            flush()
            current_header = _clean_header_text(stripped) or None
            continue
        item_text = _clean_choice_line(stripped)
        if item_text:
            current_items.append(ManagedListEntry(item_text, False))
    flush()

    unnamed_index = 0
    user_items: List[ManagedListEntry] = []

    for header, items in groups:
        is_user = bool(header and header.casefold() == USER_ADDED_HEADER.casefold())
        if is_user:
            user_items.extend(items)
            continue
        if header:
            display_header = header
        else:
            display_header = (
                COLOR_GROUP_HEADERS[unnamed_index]
                if unnamed_index < len(COLOR_GROUP_HEADERS)
                else f"Color Group {unnamed_index + 1}"
            )
            unnamed_index += 1
        entries.append(ManagedListEntry(display_header, True))
        entries.extend(items)

    entries.append(ManagedListEntry(USER_ADDED_HEADER, True))
    entries.extend(user_items)
    return entries


def _serialize_managed_list_entries(entries: List[ManagedListEntry], *, allow_headers: bool) -> str:
    lines: List[str] = []
    for entry in entries:
        text = _normalize_text(entry.text, strip=True, max_length=300)
        if not text:
            continue
        if allow_headers and entry.is_header:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"-{text}")
        else:
            lines.append(text)
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


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
        parts.append("Motifs to add: " + _span(payload.global_extra.strip(), COL_GLOBAL))

    if payload.image_extra.strip():
        parts.append("Additionally: " + _span(payload.image_extra.strip(), col_img))

    if payload.effort_line.strip():
        parts.append(_html_escape(f"When making this image, {payload.effort_line.strip()}"))

    if payload.guidance_lines:
        g = "<br>".join(_span(f"- {line}", COL_HELPFUL) for line in payload.guidance_lines)
        parts.append(_span("Guidance:", COL_HELPFUL, bold=True) + "<br>" + g)

    if payload.format_paragraph.strip():
        parts.append(_html_escape(payload.format_paragraph.strip()))

    return "<br><br>".join(p for p in parts if str(p).strip())


CHECKBOX_QSS = """
QCheckBox { color: #dcdce0; spacing: 8px; }
"""


class GoldenCheckBox(QtWidgets.QCheckBox):
    """Checkbox with a blue selected box and a golden painted check mark."""

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        option = QtWidgets.QStyleOptionButton()
        option.initFrom(self)
        if self.isChecked():
            option.state |= QtWidgets.QStyle.State_On
        else:
            option.state |= QtWidgets.QStyle.State_Off

        style = self.style()
        indicator = style.subElementRect(QtWidgets.QStyle.SE_CheckBoxIndicator, option, self)
        contents = style.subElementRect(QtWidgets.QStyle.SE_CheckBoxContents, option, self)

        size = 16
        indicator = QtCore.QRect(indicator.x(), indicator.center().y() - size // 2, size, size)
        if not indicator.isValid() or indicator.x() < 0:
            indicator = QtCore.QRect(0, max(0, (self.height() - size) // 2), size, size)
            contents = QtCore.QRect(size + 8, 0, max(0, self.width() - size - 8), self.height())

        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        border = QColor(COL_CHECK_MARK if self.isChecked() else ("#3a4355" if hovered else "#2b2b31"))
        fill = QColor(COL_CHECK if self.isChecked() else ("#121722" if hovered else "#0d0e11"))

        painter.setPen(QtGui.QPen(border, 1.35))
        painter.setBrush(fill)
        painter.drawRoundedRect(QtCore.QRectF(indicator), 3, 3)

        if self.isChecked():
            pen = QtGui.QPen(QColor(COL_CHECK_MARK), 2.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            path = QtGui.QPainterPath()
            path.moveTo(indicator.left() + 3.5, indicator.center().y() + 0.5)
            path.lineTo(indicator.left() + 6.5, indicator.bottom() - 4.0)
            path.lineTo(indicator.right() - 3.0, indicator.top() + 4.0)
            painter.drawPath(path)

        painter.setPen(QColor("#dcdce0" if self.isEnabled() else "#777a84"))
        painter.drawText(contents, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()


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


def _start_file_url_drag(source: QtWidgets.QWidget, paths: List[Path]) -> bool:
    valid_paths = [Path(path) for path in paths if Path(path).exists() and Path(path).is_file()]
    if not valid_paths:
        return False

    mime_data = QtCore.QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in valid_paths])
    drag = QtGui.QDrag(source)
    drag.setMimeData(mime_data)
    drag.exec(Qt.CopyAction)
    return True


def _mime_local_paths(mime_data: QtCore.QMimeData) -> List[Path]:
    paths: List[Path] = []
    try:
        for url in mime_data.urls():
            if url.isLocalFile():
                paths.append(Path(url.toLocalFile()))
    except Exception:
        pass
    return paths


def _has_reference_image_path(paths: List[Path]) -> bool:
    for path in paths:
        try:
            if Path(path).suffix.lower() in REFERENCE_IMAGE_ALLOWED_EXTENSIONS:
                return True
        except Exception:
            continue
    return False


def _refresh_drop_style(widget: QtWidgets.QWidget) -> None:
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        pass


class ReferenceDropButton(QtWidgets.QPushButton):
    paths_dropped = QtCore.Signal(list)

    def __init__(self, text: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setProperty("referenceDropActive", False)

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("referenceDropActive", bool(active))
        _refresh_drop_style(self)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        paths = _mime_local_paths(event.mimeData())
        if paths:
            self._set_drop_active(_has_reference_image_path(paths))
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        paths = _mime_local_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_drop_active(False)
        paths = _mime_local_paths(event.mimeData())
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class ReferenceImagesDropArea(QtWidgets.QFrame):
    paths_dropped = QtCore.Signal(list)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setProperty("referenceDropActive", False)

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("referenceDropActive", bool(active))
        _refresh_drop_style(self)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        paths = _mime_local_paths(event.mimeData())
        if paths:
            self._set_drop_active(_has_reference_image_path(paths))
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        paths = _mime_local_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_drop_active(False)
        paths = _mime_local_paths(event.mimeData())
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class ReferenceImageCard(QtWidgets.QFrame):
    copy_requested = QtCore.Signal(object)

    def __init__(
        self,
        paths_provider: Callable[[], List[Path]],
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._paths_provider = paths_provider
        self._drag_start_pos: Optional[QtCore.QPoint] = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)

    def _show_copy_menu(self, pos: QtCore.QPoint) -> None:
        self._show_copy_menu_at(self.mapToGlobal(pos))

    def _show_copy_menu_at(self, global_pos: QtCore.QPoint) -> None:
        paths = self._paths_provider()
        if not paths:
            return
        menu = QtWidgets.QMenu(self)
        copy_action = menu.addAction("Copy")
        copy_action.triggered.connect(lambda: self.copy_requested.emit(paths[0]))
        menu.exec(global_pos)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return

        current_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (current_pos - self._drag_start_pos).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        _start_file_url_drag(self, self._paths_provider())


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
        self._reference_images: List[ReferenceImage] = []
        self._references_visible = True
        self._reference_html_encode_failures: List[str] = []
        self._colors_path_used: Optional[Path] = None
        self._last_focused_widget: Optional[QtWidgets.QTextEdit] = None
        self._list_manager_dialogs: Dict[str, ListManagerDialog] = {}

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

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.MouseButtonDblClick:
            list_key = obj.property("managed_list_key")
            if isinstance(list_key, str) and list_key:
                self._open_list_manager(list_key)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _managed_list_config(self, key: str) -> dict:
        configs = {
            "type": {
                "title": "Graphics & Illustration",
                "primary_name": "type.txt",
                "fallback_name": "type.txt",
                "allow_none": True,
                "allow_headers": True,
                "auto_user_header": USER_ADDED_HEADER,
            },
            "subject": {
                "title": "Subject",
                "primary_name": "topic.txt",
                "fallback_name": "topic.txt",
                "allow_none": False,
                "allow_headers": False,
                "auto_user_header": None,
            },
            "color": {
                "title": "Color Scheme",
                "primary_name": "color.txt",
                "fallback_name": "colors.txt",
                "allow_none": True,
                "allow_headers": True,
                "auto_user_header": USER_ADDED_HEADER,
            },
        }
        return configs[key]

    def _managed_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        combos = {
            "type": self.cmb_type,
            "subject": self.cmb_subject,
            "color": self.cmb_color,
        }
        return combos[key]

    def _default_modules_dir(self) -> Path:
        return self.project_root / "Prompter" / "modules"

    def _resolve_managed_list_path(self, key: str) -> Path:
        config = self._managed_list_config(key)
        primary_name = config["primary_name"]
        fallback_name = config["fallback_name"]
        if key == "color" and self._colors_path_used is not None:
            return self._colors_path_used
        _, used_path = _read_list_file_cached(primary_name)
        if used_path is not None:
            return used_path
        if fallback_name != primary_name:
            _, used_path = _read_list_file_cached(fallback_name)
            if used_path is not None:
                return used_path
        return self._default_modules_dir() / fallback_name

    def _read_managed_entries(self, key: str) -> List[ManagedListEntry]:
        config = self._managed_list_config(key)
        path = self._resolve_managed_list_path(key)
        lines, _ = _read_list_file_cached(path.name)
        if key == "color" and not lines:
            lines, _ = _read_list_file_cached("colors.txt")
        if key == "color":
            return _parse_color_list_entries(lines)
        return _parse_managed_list_entries(lines, allow_headers=bool(config["allow_headers"]))

    def _style_header_item(self, item: Optional[QtGui.QStandardItem]) -> None:
        if item is None:
            return
        item.setFlags(Qt.NoItemFlags)
        item.setData(True, MANAGED_LIST_HEADER_ROLE)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(COL_HEADER_TEXT))

    def _populate_managed_combo(
        self,
        combo: QtWidgets.QComboBox,
        entries: List[ManagedListEntry],
        *,
        allow_none: bool,
    ) -> None:
        current_text = combo.currentText().strip()
        is_editable = combo.isEditable()

        combo.blockSignals(True)
        combo.clear()
        if allow_none:
            combo.addItem(NONE_CHOICE_LABEL)
        for entry in entries:
            combo.addItem(entry.text)
            model_item = combo.model().item(combo.count() - 1)
            if entry.is_header:
                self._style_header_item(model_item)
            elif model_item is not None:
                model_item.setData(False, MANAGED_LIST_HEADER_ROLE)

        if current_text:
            restored_index = combo.findText(current_text)
            if restored_index >= 0:
                combo.setCurrentIndex(restored_index)
            elif is_editable:
                combo.setEditText(current_text)
            elif allow_none:
                combo.setCurrentIndex(0)
        elif allow_none and combo.count() > 0:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _reload_managed_list(self, key: str) -> None:
        config = self._managed_list_config(key)
        entries = self._read_managed_entries(key)
        combo = self._managed_combo_for_key(key)
        self._populate_managed_combo(combo, entries, allow_none=bool(config["allow_none"]))
        if key == "color":
            self._colors_path_used = self._resolve_managed_list_path("color")

    def _clear_managed_list_cache(self, path: Path) -> None:
        _FILE_CACHE.pop(path.name, None)
        if path.name in {"color.txt", "colors.txt"}:
            _FILE_CACHE.pop("color.txt", None)
            _FILE_CACHE.pop("colors.txt", None)

    def _apply_managed_list_changes(self, key: str, entries: List[ManagedListEntry]) -> None:
        config = self._managed_list_config(key)
        path = self._resolve_managed_list_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _serialize_managed_list_entries(entries, allow_headers=bool(config["allow_headers"])),
            encoding="utf-8",
        )
        self._clear_managed_list_cache(path)
        self._reload_managed_list(key)
        self._schedule_persist_state(0)

    def _open_list_manager(self, key: str) -> None:
        existing = self._list_manager_dialogs.get(key)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        config = self._managed_list_config(key)
        dialog = ListManagerDialog(
            title=config["title"],
            entries=self._read_managed_entries(key),
            allow_headers=bool(config["allow_headers"]),
            auto_user_header=config.get("auto_user_header"),
            parent=self,
        )
        dialog.entries_changed.connect(lambda entries, list_key=key: self._apply_managed_list_changes(list_key, entries))
        dialog.finished.connect(lambda *_args, list_key=key: self._list_manager_dialogs.pop(list_key, None))
        self._list_manager_dialogs[key] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _install_manage_trigger(self, widget: QtWidgets.QWidget, key: str) -> None:
        widget.setProperty("managed_list_key", key)
        widget.installEventFilter(self)

    def _schedule_persist_state(self, delay_ms: int = 350) -> None:
        try:
            self._persist_timer.start(int(delay_ms))
        except Exception:
            pass

    def _normalize_reference_images(self, value: object) -> List[ReferenceImage]:
        if not isinstance(value, list):
            return []

        refs: List[ReferenceImage] = []
        for raw in value:
            if len(refs) >= REFERENCE_IMAGE_MAX_COUNT:
                break
            if not isinstance(raw, dict):
                continue

            raw_path = _normalize_text(raw.get("path", ""), strip=True, max_length=4096)
            filename = _normalize_text(raw.get("filename", ""), strip=True, max_length=300)
            if not filename and raw_path:
                filename = Path(raw_path).name
            if not raw_path and filename:
                raw_path = filename
            if not raw_path and not filename:
                continue

            ext = _reference_image_extension(filename or raw_path)
            if ext not in REFERENCE_IMAGE_ALLOWED_EXTENSIONS:
                continue

            ref_id = _normalize_text(raw.get("id", ""), strip=True, max_length=120) or str(uuid.uuid4())
            added_at = _normalize_text(raw.get("added_at", ""), strip=True, max_length=80)
            notes = _normalize_text(raw.get("notes", ""), strip=True, max_length=500)
            refs.append(
                ReferenceImage(
                    id=ref_id,
                    path=raw_path,
                    filename=filename or Path(raw_path).name,
                    role=_normalize_reference_role(raw.get("role", DEFAULT_REFERENCE_IMAGE_ROLE)),
                    added_at=added_at,
                    notes=notes,
                )
            )
        return refs

    def _reference_images_to_state(self) -> List[dict]:
        return [
            {
                "id": ref.id,
                "path": ref.path,
                "filename": ref.filename,
                "role": ref.role,
                "added_at": ref.added_at,
                "notes": ref.notes,
            }
            for ref in self._reference_images[:REFERENCE_IMAGE_MAX_COUNT]
        ]

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

        reference_images = self._normalize_reference_images(state.get("reference_images", []))

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
            "reference_images": [
                {
                    "id": ref.id,
                    "path": ref.path,
                    "filename": ref.filename,
                    "role": ref.role,
                    "added_at": ref.added_at,
                    "notes": ref.notes,
                }
                for ref in reference_images
            ],
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
            if color_txt == NONE_CHOICE_LABEL:
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
            "reference_images": self._reference_images_to_state(),
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

            try:
                self._reference_images = self._normalize_reference_images(state.get("reference_images", []))
                self._refresh_reference_image_panel()
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
        self.btn_add_reference = ReferenceDropButton("Add Reference Image")
        self.btn_erase = QtWidgets.QPushButton("Erase All")
        for b in (self.btn_generate, self.btn_copy, self.btn_add_reference, self.btn_erase):
            b.setFixedHeight(28)
        header.addWidget(self.btn_generate)
        header.addWidget(self.btn_copy)
        header.addWidget(self.btn_add_reference)
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

        self._left_scroll = QtWidgets.QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._left_scroll.setObjectName("left_scroll")
        left_panel = QtWidgets.QWidget()
        left_v = QtWidgets.QVBoxLayout(left_panel)
        left_v.setContentsMargins(0, 0, 6, 0)
        left_v.setSpacing(10)
        self._left_scroll.setWidget(left_panel)
        main_h.addWidget(self._left_scroll, 1)

        sel_grid = QtWidgets.QGridLayout()
        sel_grid.setHorizontalSpacing(8)
        sel_grid.setVerticalSpacing(8)

        self.lbl_type = QtWidgets.QLabel("Graphics and Illustration")
        self.lbl_type.setStyleSheet(f"font-weight:900; color:{COL_TYPE};")
        _set_help(
            self.lbl_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.setEditable(False)
        self.cmb_type.setItemDelegate(HeaderAwareItemDelegate(self.cmb_type))
        _set_help(
            self.cmb_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )
        self._populate_managed_combo(self.cmb_type, self._read_managed_entries("type"), allow_none=True)
        self._install_manage_trigger(self.lbl_type, "type")
        self._install_manage_trigger(self.cmb_type, "type")
        sel_grid.addWidget(self.lbl_type, 0, 0)
        sel_grid.addWidget(self.cmb_type, 0, 1)

        self.lbl_subject = QtWidgets.QLabel("Subject")
        self.lbl_subject.setStyleSheet(f"font-weight:900; color:{COL_SUBJECT};")
        _set_help(
            self.lbl_subject,
            "Choose the main thing the images should be about. Double-click Subject to add, update, or remove subject entries.",
        )
        self.cmb_subject = QtWidgets.QComboBox()
        self.cmb_subject.setEditable(False)
        self.cmb_subject.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_subject.setItemDelegate(HeaderAwareItemDelegate(self.cmb_subject))
        _set_help(
            self.cmb_subject,
            "Choose the main thing the images should be about. Typing is disabled to prevent accidental edits; double-click to manage the subject list.",
        )
        self._populate_managed_combo(self.cmb_subject, self._read_managed_entries("subject"), allow_none=False)
        self._install_manage_trigger(self.lbl_subject, "subject")
        self._install_manage_trigger(self.cmb_subject, "subject")
        sel_grid.addWidget(self.lbl_subject, 1, 0)
        sel_grid.addWidget(self.cmb_subject, 1, 1)

        self.lbl_color = QtWidgets.QLabel("Color Scheme")
        self.lbl_color.setStyleSheet(f"font-weight:900; color:{COL_SCHEME};")
        _set_help(
            self.lbl_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self.cmb_color = QtWidgets.QComboBox()
        self.cmb_color.setEditable(False)
        self.cmb_color.setItemDelegate(HeaderAwareItemDelegate(self.cmb_color))
        _set_help(
            self.cmb_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self._reload_managed_list("color")
        self._install_manage_trigger(self.lbl_color, "color")
        self._install_manage_trigger(self.cmb_color, "color")
        sel_grid.addWidget(self.lbl_color, 2, 0)
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

        self.lbl_helpful = QtWidgets.QLabel("Helpful Options / Guidance")
        self.lbl_helpful.setStyleSheet(f"font-weight:900; color:{COL_HELPFUL};")
        _set_help(
            self.lbl_helpful,
            "Use these built-in options to refine composition, framing, image policy, and style. Group headers are visual only and are not copied into the final prompt. Some options are intentionally mutually exclusive so only combinations that make sense can stay active.",
        )
        gb_layout.addWidget(self.lbl_helpful, 0, 0, 1, 2)

        def add_helpful_header(row: int, title: str) -> None:
            label = QtWidgets.QLabel(title)
            label.setProperty("managedHeaderLabel", True)
            label.setStyleSheet(f"font-weight:900; color:{COL_HEADER_TEXT}; margin-top:8px;")
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            gb_layout.addWidget(label, row, 0, 1, 2)

        add_helpful_header(1, "Border & Frame")
        self.cb_black = GoldenCheckBox("Thin Black Border")
        self.cb_white = GoldenCheckBox("Thin White Border")
        self.cb_frame = GoldenCheckBox("Decorative Frame")
        self.cb_vignette = GoldenCheckBox("Subtle Edge Vignette")
        self.cb_polaroid = GoldenCheckBox("Polaroid-Style Margin")
        self.cb_cardshadow = GoldenCheckBox("Card With Soft Drop Shadow")
        gb_layout.addWidget(self.cb_black, 2, 0)
        gb_layout.addWidget(self.cb_white, 2, 1)
        gb_layout.addWidget(self.cb_frame, 3, 0)
        gb_layout.addWidget(self.cb_vignette, 3, 1)
        gb_layout.addWidget(self.cb_polaroid, 4, 0)
        gb_layout.addWidget(self.cb_cardshadow, 4, 1)

        add_helpful_header(5, "Style / Detail Bias")
        self.cb_real = GoldenCheckBox("Bias Toward Photorealism")
        self.cb_paint = GoldenCheckBox("Bias Toward Painterly Style")
        self.cb_minimal = GoldenCheckBox("Bias Toward Minimalistic Composition")
        self.cb_simplified_details = GoldenCheckBox("Simplified Details")
        gb_layout.addWidget(self.cb_real, 6, 0)
        gb_layout.addWidget(self.cb_paint, 6, 1)
        gb_layout.addWidget(self.cb_minimal, 7, 0)
        gb_layout.addWidget(self.cb_simplified_details, 7, 1)

        add_helpful_header(8, "Image Safety / Clean Output")
        self.cb_forbid = GoldenCheckBox("No Text in the Image")
        self.cb_clean_composition = GoldenCheckBox("Clean Composition")
        gb_layout.addWidget(self.cb_forbid, 9, 0, 1, 2)
        gb_layout.addWidget(self.cb_clean_composition, 10, 0, 1, 2)

        add_helpful_header(11, "Camera / Framing")
        self.cb_strong_focal_point = GoldenCheckBox("Strong Focal Point")
        _set_help(
            self.cb_strong_focal_point,
            "Use Strong Focal Point to keep the main subject as the clearest read in the composition without forcing a close-up.",
        )
        self.cb_dynamic_angle = GoldenCheckBox("Dynamic Angle")
        self.cb_cinematic_framing = GoldenCheckBox("Cinematic Framing")
        self.cb_close_up_focus = GoldenCheckBox("Close-Up Focus")
        self.cb_full_body_view = GoldenCheckBox("Full Body View")
        self.cb_wide_scene = GoldenCheckBox("Wide Scene")
        _set_help(
            self.cb_wide_scene,
            "Wide Scene means showing more environment inside the same portrait 2048×3072 frame. It does not change the aspect ratio.",
        )
        gb_layout.addWidget(self.cb_strong_focal_point, 12, 0, 1, 2)
        gb_layout.addWidget(self.cb_dynamic_angle, 13, 0)
        gb_layout.addWidget(self.cb_cinematic_framing, 13, 1)
        gb_layout.addWidget(self.cb_close_up_focus, 14, 0)
        gb_layout.addWidget(self.cb_full_body_view, 14, 1)
        gb_layout.addWidget(self.cb_wide_scene, 15, 0, 1, 2)

        left_v.addWidget(self.gb_helpful)

        lbl_global = QtWidgets.QLabel("Apply to All Images")
        lbl_global.setStyleSheet(f"font-weight:900; color:{COL_GLOBAL};")
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
        left_v.addStretch(1)

        right_v = QtWidgets.QVBoxLayout(); right_v.setSpacing(8)
        main_h.addLayout(right_v, 1)

        self.reference_strip = QtWidgets.QFrame()
        self.reference_strip.setObjectName("reference_strip")
        reference_v = QtWidgets.QVBoxLayout(self.reference_strip)
        reference_v.setContentsMargins(8, 8, 8, 8)
        reference_v.setSpacing(6)

        reference_header = QtWidgets.QHBoxLayout()
        self.lbl_reference_title = QtWidgets.QLabel("Reference Images")
        self.lbl_reference_title.setStyleSheet("font-weight:900; color:#ffffff;")
        reference_header.addWidget(self.lbl_reference_title)
        reference_header.addStretch(1)
        self.lbl_reference_status = QtWidgets.QLabel("")
        self.lbl_reference_status.setStyleSheet("color:#cfd3da;")
        reference_header.addWidget(self.lbl_reference_status)
        self.btn_toggle_references = QtWidgets.QPushButton("Hide")
        self.btn_toggle_references.setFixedHeight(24)
        self.btn_toggle_references.clicked.connect(self._toggle_reference_images_visible)
        reference_header.addWidget(self.btn_toggle_references)
        reference_v.addLayout(reference_header)

        self.reference_preview_widget = ReferenceImagesDropArea()
        reference_preview_layout = QtWidgets.QVBoxLayout(self.reference_preview_widget)
        reference_preview_layout.setContentsMargins(0, 0, 0, 0)
        reference_preview_layout.setSpacing(0)
        self.reference_cards_layout = QtWidgets.QHBoxLayout()
        self.reference_cards_layout.setSpacing(8)
        reference_preview_layout.addLayout(self.reference_cards_layout)
        reference_v.addWidget(self.reference_preview_widget)
        self.reference_strip.setVisible(False)
        right_v.addWidget(self.reference_strip)

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
            header_label.setStyleSheet(f"font-weight:900; color: {IMAGE_COLOR_MAP.get(img, '#dcdce0')};")
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
            QFrame#reference_strip {
                border: 1px solid #232834;
                border-radius: 6px;
                background: rgba(7, 9, 13, 0.42);
            }
            QFrame#reference_card {
                border: 1px solid #313543;
                border-radius: 6px;
                background: rgba(12, 15, 22, 0.72);
            }
            QPushButton[referenceDropActive="true"], QFrame[referenceDropActive="true"] {
                border-color: #ffd60a;
                background: rgba(255, 214, 10, 0.12);
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
            QScrollArea#left_scroll QWidget {
                background: transparent;
            }
            QToolTip {
                background: #10141d;
                color: #ecf2ff;
                border: 1px solid #2d4267;
                padding: 6px 8px;
            }
            """ + CHECKBOX_QSS
        )


    def _enforce_exclusive_group(self, checked_box: QtWidgets.QCheckBox, group: Tuple[QtWidgets.QCheckBox, ...]) -> None:
        if not checked_box.isChecked():
            return
        for other in group:
            if other is checked_box:
                continue
            if other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)

    def _wire_exclusive_group(self, group: Tuple[QtWidgets.QCheckBox, ...]) -> None:
        for checkbox in group:
            checkbox.stateChanged.connect(
                lambda *_args, cb=checkbox, grp=group: self._enforce_exclusive_group(cb, grp)
            )

    def _connect_signals(self):
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_copy.clicked.connect(self._copy_all_prompts)
        self.btn_add_reference.clicked.connect(lambda: self.add_reference_image())
        self.btn_add_reference.paths_dropped.connect(self._add_reference_image_paths)
        self.reference_preview_widget.paths_dropped.connect(self._add_reference_image_paths)
        self.btn_erase.clicked.connect(self._on_erase_all)

        try:
            self.cmb_type.currentTextChanged.connect(lambda *_: self._schedule_persist_state())
            self.cmb_subject.currentTextChanged.connect(lambda *_: self._schedule_persist_state())
            self.cmb_color.currentIndexChanged.connect(lambda *_: self._schedule_persist_state())
            self.txt_global.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_cover.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_letter.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_wall.textChanged.connect(lambda: self._schedule_persist_state())
            self.txt_back.textChanged.connect(lambda: self._schedule_persist_state())
            self._wire_exclusive_group((self.cb_black, self.cb_white, self.cb_frame, self.cb_polaroid, self.cb_cardshadow))
            self._wire_exclusive_group((self.cb_close_up_focus, self.cb_full_body_view, self.cb_wide_scene))
            self._wire_exclusive_group((self.cb_real, self.cb_paint, self.cb_minimal, self.cb_simplified_details))
            for checkbox, _ in self._checkbox_state_specs():
                checkbox.stateChanged.connect(lambda *_: self._schedule_persist_state())
        except Exception:
            pass

    def _show_reference_message(self, message: str) -> None:
        try:
            self.lbl_reference_status.setText(message)
            self.reference_strip.setVisible(True)
        except Exception:
            pass
        try:
            QtWidgets.QMessageBox.information(self, "Reference Image", message)
        except Exception:
            pass

    def _clear_reference_cards(self) -> None:
        try:
            while self.reference_cards_layout.count():
                item = self.reference_cards_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        except Exception:
            pass

    def _refresh_reference_image_panel(self) -> None:
        self._refresh_reference_images_ui()

    def _refresh_reference_images_ui(self) -> None:
        self._clear_reference_cards()
        count = len(self._reference_images)
        try:
            self.reference_strip.setVisible(count > 0)
            self.lbl_reference_status.setText(f"{count}/{REFERENCE_IMAGE_MAX_COUNT}")
            self.reference_preview_widget.setVisible(count > 0 and self._references_visible)
            self.btn_toggle_references.setText("Hide" if self._references_visible else "Show")
        except Exception:
            pass
        if count <= 0:
            return

        for index, ref in enumerate(self._reference_images):
            exists = _reference_image_exists(ref)
            card = ReferenceImageCard(
                lambda path=ref.path: [Path(path)] if Path(path).exists() and Path(path).is_file() else []
            )
            card.copy_requested.connect(self._copy_reference_image_to_clipboard)
            card.setObjectName("reference_card")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(6, 6, 6, 6)
            card_layout.setSpacing(6)

            thumb_frame = QtWidgets.QFrame()
            thumb_frame.setObjectName("reference_thumb_frame")
            thumb_layout = QtWidgets.QGridLayout(thumb_frame)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setSpacing(0)

            thumb = QtWidgets.QLabel()
            thumb.setFixedSize(104, 78)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet("border:1px solid #313543; border-radius:4px; color:#cfd3da;")
            if exists:
                pix = QtGui.QPixmap(ref.path)
                if pix.isNull():
                    thumb.setText("Preview\nUnavailable")
                else:
                    thumb.setPixmap(pix.scaled(104, 78, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb.setText("Missing\nReference")
            thumb_layout.addWidget(thumb, 0, 0)

            x_btn = QtWidgets.QPushButton("X")
            x_btn.setFixedSize(20, 20)
            x_btn.setToolTip("X")
            x_btn.setStyleSheet(
                "QPushButton { padding:0; font-weight:900; border-radius:10px; "
                "background:#1f2430; color:#ffffff; border:1px solid #596174; }"
                "QPushButton:hover { background:#3b4458; border-color:#8a94aa; }"
            )
            x_btn.clicked.connect(lambda _checked=False, row=index: self._remove_reference_image(row))
            thumb_layout.addWidget(x_btn, 0, 0, alignment=Qt.AlignTop | Qt.AlignRight)
            card_layout.addWidget(thumb_frame)

            filename = ref.filename or Path(ref.path).name or "reference image"
            display_name = filename if exists else f"Missing Reference \u2014 {filename}"
            name_label = QtWidgets.QLabel(display_name)
            name_label.setWordWrap(True)
            name_label.setMinimumWidth(104)
            name_label.setMaximumWidth(132)
            name_label.setStyleSheet("font-weight:700; color:#e8e8ea;")
            card_layout.addWidget(name_label)
            for menu_widget in (thumb_frame, thumb, name_label):
                menu_widget.setContextMenuPolicy(Qt.CustomContextMenu)
                menu_widget.customContextMenuRequested.connect(
                    lambda pos, source=menu_widget, image_card=card: image_card._show_copy_menu_at(
                        source.mapToGlobal(pos)
                    )
                )
            self.reference_cards_layout.addWidget(card)

        self.reference_cards_layout.addStretch(1)

    def _toggle_reference_images_visible(self) -> None:
        self._references_visible = not self._references_visible
        try:
            self.reference_preview_widget.setVisible(self._references_visible and bool(self._reference_images))
            self.btn_toggle_references.setText("Hide" if self._references_visible else "Show")
        except Exception:
            pass

    def _set_reference_image_role(self, reference_id: str, role: str) -> bool:
        normalized_role = _normalize_reference_role(role)
        for index, ref in enumerate(self._reference_images):
            if ref.id == reference_id:
                self._reference_images[index] = replace(ref, role=normalized_role)
                self._schedule_persist_state()
                return True
        return False

    def _reference_image_paths(self) -> List[Path]:
        paths: List[Path] = []
        for ref in self._reference_images[:REFERENCE_IMAGE_MAX_COUNT]:
            try:
                image_path = Path(ref.path)
            except Exception:
                continue
            if image_path.exists() and image_path.is_file():
                paths.append(image_path)
        return paths

    def _copy_reference_image_to_clipboard(self, path: object) -> bool:
        try:
            image_path = Path(path).expanduser().resolve()
        except Exception:
            self._set_reference_status("Missing Reference")
            return False

        if not image_path.exists() or not image_path.is_file():
            self._set_reference_status("Missing Reference")
            return False

        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(image_path))])

        image = QtGui.QImage(str(image_path))
        if not image.isNull():
            mime_data.setImageData(image)

        QtWidgets.QApplication.clipboard().setMimeData(mime_data)
        self._set_reference_status("Image copied.")
        return True

    def _set_reference_status(self, message: str) -> None:
        try:
            self.reference_strip.setVisible(True)
            self.lbl_reference_status.setText(message)
        except Exception:
            pass

    def _is_valid_reference_image_path(self, path: Path) -> bool:
        try:
            image_path = Path(path).expanduser()
            return (
                image_path.exists()
                and image_path.is_file()
                and image_path.suffix.lower() in REFERENCE_IMAGE_ALLOWED_EXTENSIONS
            )
        except Exception:
            return False

    def _reference_path_key(self, path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except Exception:
            return str(path).casefold()

    def _reference_image_already_added(self, path: Path) -> bool:
        key = self._reference_path_key(path)
        for ref in self._reference_images:
            try:
                if self._reference_path_key(Path(ref.path)) == key:
                    return True
            except Exception:
                continue
        return False

    def _add_reference_image_paths(self, paths: List[Path]) -> None:
        added = 0
        invalid_seen = False
        duplicate_seen = False
        limit_seen = False

        for raw_path in paths:
            if len(self._reference_images) >= REFERENCE_IMAGE_MAX_COUNT:
                limit_seen = True
                break

            try:
                image_path = Path(raw_path).expanduser()
            except Exception:
                invalid_seen = True
                continue

            if not self._is_valid_reference_image_path(image_path):
                invalid_seen = True
                continue

            image_path = image_path.resolve()
            if self._reference_image_already_added(image_path):
                duplicate_seen = True
                continue

            self._reference_images.append(
                ReferenceImage(
                    id=str(uuid.uuid4()),
                    path=str(image_path),
                    filename=image_path.name,
                    role=DEFAULT_REFERENCE_IMAGE_ROLE,
                    added_at=datetime.now(timezone.utc).isoformat(),
                    notes="",
                )
            )
            added += 1

        if added:
            self._refresh_reference_images_ui()
            self._schedule_persist_state(0)

        if limit_seen:
            self._set_reference_status("Reference image limit reached.")
        elif invalid_seen and not added:
            self._set_reference_status("Drop image files only.")
        elif duplicate_seen and not added:
            self._set_reference_status("Reference image already added.")
        elif added:
            self.lbl_reference_status.setText(f"{len(self._reference_images)}/{REFERENCE_IMAGE_MAX_COUNT}")

    def add_reference_image(self, path: Optional[str] = None) -> bool:
        if len(self._reference_images) >= REFERENCE_IMAGE_MAX_COUNT:
            self._set_reference_status("Reference image limit reached.")
            return False

        if not isinstance(path, (str, Path)) or not str(path).strip():
            start_dir = str(self.project_root)
            try:
                if self._reference_images:
                    last_dir = Path(self._reference_images[-1].path).parent
                    if last_dir.exists():
                        start_dir = str(last_dir)
            except Exception:
                pass
            selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Add Reference Image",
                start_dir,
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
            )
            path = selected

        if not path:
            return False

        before = len(self._reference_images)
        self._add_reference_image_paths([Path(str(path))])
        return len(self._reference_images) > before

    def remove_reference_image(self, reference_id: str) -> bool:
        return self._remove_reference_image(reference_id)

    def _remove_reference_image(self, path_or_index: object) -> bool:
        remove_index = -1
        if isinstance(path_or_index, int):
            remove_index = path_or_index
        elif isinstance(path_or_index, Path):
            key = self._reference_path_key(path_or_index)
            remove_index = next(
                (idx for idx, ref in enumerate(self._reference_images) if self._reference_path_key(Path(ref.path)) == key),
                -1,
            )
        else:
            text = _normalize_text(path_or_index, strip=True, max_length=4096)
            text_path_key = ""
            if text:
                try:
                    text_path_key = self._reference_path_key(Path(text))
                except Exception:
                    text_path_key = text.casefold()
            remove_index = next(
                (
                    idx
                    for idx, ref in enumerate(self._reference_images)
                    if ref.id == text or self._reference_path_key(Path(ref.path)) == text_path_key
                ),
                -1,
            )

        if remove_index < 0 or remove_index >= len(self._reference_images):
            return False

        del self._reference_images[remove_index]
        self._refresh_reference_images_ui()
        self._schedule_persist_state(0)
        return True

    def reorder_reference_image(self, reference_id: str, direction: object) -> bool:
        if isinstance(direction, str):
            direction = -1 if direction.lower() == "left" else 1 if direction.lower() == "right" else 0
        try:
            step = int(direction)
        except Exception:
            step = 0
        step = -1 if step < 0 else 1 if step > 0 else 0
        if step == 0:
            return False

        current_index = next((idx for idx, ref in enumerate(self._reference_images) if ref.id == reference_id), -1)
        new_index = current_index + step
        if current_index < 0 or new_index < 0 or new_index >= len(self._reference_images):
            return False

        self._reference_images[current_index], self._reference_images[new_index] = (
            self._reference_images[new_index],
            self._reference_images[current_index],
        )
        self._refresh_reference_image_panel()
        self._schedule_persist_state(0)
        return True

    def build_reference_image_copy_block(self) -> str:
        refs = self._reference_images[:REFERENCE_IMAGE_MAX_COUNT]
        if not refs:
            return "REFERENCE IMAGES:\nNo reference images attached."

        dash = "\u2014"
        lines = [
            "REFERENCE IMAGES:",
            "Attach the following reference image(s) before using the prompt:",
        ]
        for index, ref in enumerate(refs, start=1):
            filename = ref.filename or Path(ref.path).name or "reference image"
            missing = " [missing file]" if not _reference_image_exists(ref) else ""
            lines.append(f"{index}. {_normalize_reference_role(ref.role)} {dash} {filename}{missing}")

        lines.extend(
            [
                "",
                "Use these reference image(s) as visual guidance. Preserve the identity, facial structure, body type, hairstyle, outfit cues, color palette, pose language, and mood where relevant. Do not copy unwanted background clutter unless the prompt specifically asks for it.",
            ]
        )
        return "\n".join(lines)

    def _build_copy_all_text(self) -> str:
        parts: List[str] = []
        for key in self._images:
            text = self._generated_prompts.get(key, "").strip()
            if not text:
                w = self.preview_widgets.get(key)
                if w:
                    text = w.toPlainText().strip()
            parts.append(f"--- {key} ---\n\n{text}" if text else f"--- {key} ---\n\n")
        return "\n\n".join(parts).strip()

    def _build_all_prompts_copy_text(self) -> str:
        return self._build_copy_all_text()

    def copy_all_with_references(self) -> str:
        return self._build_copy_all_text()

    def _copy_all_prompts_text(self) -> str:
        all_text = self._build_copy_all_text()
        if not all_text:
            return ""

        try:
            QtWidgets.QApplication.clipboard().setText(all_text)
        except Exception:
            pass
        return all_text

    def _load_colors_into_combo(self):
        self._reload_managed_list("color")

    def _collect_guidance(self) -> List[str]:
        return [
            phrase
            for checkbox, phrase in self._guidance_checkbox_specs()
            if checkbox.isChecked()
        ]

    def _get_color_choice(self) -> Optional[str]:
        text = self.cmb_color.currentText().strip()
        if text in (NONE_CHOICE_LABEL, "", "(no color entries found)"):
            return None
        item = self.cmb_color.model().item(self.cmb_color.currentIndex())
        if item is not None and bool(item.data(MANAGED_LIST_HEADER_ROLE)):
            return None
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
        if t == NONE_CHOICE_LABEL:
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
        return self._copy_all_prompts_text()

    def _copy_prompt(self, image_name: str) -> None:
        text = self._generated_prompts.get(image_name, "").strip()
        if not text:
            widget = self.preview_widgets.get(image_name)
            if widget is not None:
                text = widget.toPlainText().strip()
        if text:
            QtWidgets.QApplication.clipboard().setText(text)

    def _on_erase_all(self):
        self.cmb_subject.setCurrentIndex(-1)
        self.cmb_type.setCurrentIndex(0)
        self.cmb_color.setCurrentIndex(0)
        for checkbox, _ in self._checkbox_state_specs():
            checkbox.setChecked(False)

        self._generated_prompts = {}
        self._reference_images = []
        self.txt_global.clear(); self.txt_cover.clear(); self.txt_letter.clear(); self.txt_wall.clear(); self.txt_back.clear()
        for w in self.preview_widgets.values():
            w.clear()

        self._load_colors_into_combo()
        self._refresh_reference_image_panel()
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
        h = min(int(avail.height() * 0.82), 820)
        target = QtCore.QRect(avail.x() + 24, avail.y() + 24, w, h)
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
