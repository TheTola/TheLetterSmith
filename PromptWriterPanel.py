# PromptWriterPanel.py
# Prompt Writer for eLetter — generates prompts for the selected page layout
# Windowed layout (stacked "windows"), per-window copy buttons, global Copy All.

from __future__ import annotations

from page_controls import PageControls, page_controller
from project_model import PAGE_TYPES
from project_session import get_project_session
from prompt_writer_project import PromptBatch, page_label
from page_layout import DEFAULT_PAGES, PAGE_KEYS, PAGE_INDICES, page_labels, pages_payload

import sys
import hashlib
import json
import logging
import random
import re
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QEasingCurve, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from app_icon import apply_qt_window_icon, configure_windows_app_identity
from external_urls import open_external_url
from image_button import set_control_invisible
from language_service import get_language_service
from project_paths import application_paths
from save_schema import PROMPT_WRITER_STATE_VERSION
from settings_store import (
    DEFAULT_VISIONARY_URL,
    SettingsStore,
    VISIONARY_URL_KEY,
)
from window_chrome import (
    MINIMIZE_SYMBOL, StandardTitleBar, bounded_window_geometry,
    place_window_on_launcher, screen_for_launcher,
)
from transactional_io import atomic_write_text, safe_write_json, set_path_hidden
from ui_dialogs import LetterSmithConfirmationDialog, show_lettersmith_message
from ui_help import set_control_help
from ui_sounds import UiSound, play_ui_sound

LOGGER = logging.getLogger(__name__)

# ---------------------------
# Robust file discovery & reading + cache
# ---------------------------

_FILE_CACHE: Dict[str, Tuple[List[str], Optional[Path], Optional[Tuple[int, int]]]] = {}
PROMPT_LANGUAGE_VERSION = 2
STATE_PERSIST_DEBOUNCE_MS = 350
MAX_INVALID_STATE_BACKUPS = 3

def _backup_invalid_prompt_writer_state(path: Path) -> None:
    if not path.is_file():
        return
    backup = path.with_name(
        "prompt_writer_state.invalid."
        f"{time.strftime('%Y%m%d-%H%M%S')}.{time.time_ns()}.json"
    )
    try:
        shutil.copy2(path, backup)
        set_path_hidden(backup)
    except OSError:
        LOGGER.exception("Invalid Prompt Writer state backup failed: %s", backup)
        return
    backups = sorted(
        path.parent.glob("prompt_writer_state.invalid.*.json"),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in backups[MAX_INVALID_STATE_BACKUPS:]:
        try:
            stale.unlink()
        except OSError:
            LOGGER.exception(
                "Could not remove old Prompt Writer state backup: %s",
                stale,
            )


MAX_STATE_TEXT_LENGTH = 24000
MAX_GENERATED_PROMPT_LENGTH = 24000
MAX_MANAGED_LIST_ENTRY_LENGTH = 300


# =========================
# COLOR SYSTEM (UI + Prompt Preview)
# =========================

PROMPT_COLORS: Dict[str, str] = {
    "type": "#B084FF",
    "subject": "#FF5C7A",
    "scheme": "#35D07F",
    "helpful": "#32C7E8",
    "global": "#E5E7EB",
    "cover": "#FFD54A",
    "letter": "#4D8DFF",
    "wall": "#FF8A3D",
    "back": "#F25ACD",
}


def prompt_color(semantic_key: str) -> str:
    return PROMPT_COLORS[semantic_key]


def _service_app_font_family(service: object) -> str:
    family = getattr(service, "app_font_family", "Segoe UI")
    if callable(family):
        family = family()
    return str(family or "Segoe UI").strip() or "Segoe UI"


def _qss_font_family(family: object) -> str:
    return str(family or "Segoe UI").replace("\\", "\\\\").replace("'", "\\'")


COL_HEADER_TEXT = "#72c8c8"
UI_ACCENT = "#00b2b2"
UI_ACCENT_SOFT = "#263535"
UI_TEXT_PRIMARY = "#edf7fb"
UI_TEXT_SECONDARY = "#93a7b3"
MANAGED_LIST_HEADER_ROLE = Qt.UserRole + 41
NONE_CHOICE_LABEL = "— none —"
USER_ADDED_HEADER = "User Added"
HIDDEN_STYLE_DEFAULT = (
    "Apply no additional style bias beyond the selected Graphics and Illustration style."
)
HIDDEN_FRAMING_DEFAULT = (
    "Use the framing that best serves the composition; do not force a close-up, full-body, or wide-scene view."
)

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

@dataclass
class PageSpec:
    key: str
    display_label: str
    output_filename: str
    color_key: str
    baseline: str
    detail_help: str
    detail_widget: Optional[QtWidgets.QPlainTextEdit] = None
    preview_widget: Optional[QtWidgets.QTextEdit] = None
    copy_button: Optional[QtWidgets.QPushButton] = None
    generate_button: Optional[QtWidgets.QPushButton] = None
    image_widget: Optional[QtWidgets.QLabel] = None
    detail_label: Optional[QtWidgets.QLabel] = None
    preview_title: Optional[QtWidgets.QLabel] = None
    preview_card: Optional[QtWidgets.QFrame] = None

    @property
    def preview_color(self) -> str:
        return prompt_color(self.color_key)


PAGE_SPECS: Tuple[PageSpec, ...] = (
    PageSpec(
        key="cover",
        display_label="Cover Prompt",
        output_filename="cover.png",
        color_key="cover",
        baseline="The Cover Page is a bold, decorative opening image that captures attention and sets the tone.",
        detail_help="Use it for cover-specific details, composition, or mood.",
    ),
    PageSpec(
        key="letter",
        display_label="Letter Prompt",
        output_filename="letter.png",
        color_key="letter",
        baseline="The Letter Page is a subtle, elegant backdrop that frames the main written message without distraction.",
        detail_help="Use it for letter-specific details or layout direction.",
    ),
    PageSpec(
        key="wall",
        display_label="Wall Prompt",
        output_filename="wall.png",
        color_key="wall",
        baseline="The Wall Page is a calm, minimalist background designed to support large blocks of text.",
        detail_help="Use it for wall-specific environment or background details.",
    ),
    PageSpec(
        key="back",
        display_label="End Prompt",
        output_filename="back.png",
        color_key="back",
        baseline="The End Page is a simple, graceful closing image that echoes the cover while providing a sense of finality.",
        detail_help="Use it for back-page details or closing visual accents.",
    ),
)


def _page_spec_for(identifier: object) -> Optional[PageSpec]:
    if isinstance(identifier, PageSpec):
        return identifier
    text = _normalize_text(identifier, strip=True, max_length=80).casefold()
    if text == "back prompt":
        text = "back"
    if not text:
        return None
    return next(
        (
            page
            for page in PAGE_SPECS
            if text
            in {
                page.key.casefold(),
                page.display_label.casefold(),
                page.output_filename.casefold(),
            }
        ),
        None,
    )

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

EXCLUSIVE_CHECK_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("black", "white"),
    ("real", "paint", "minimal"),
    ("close_up_focus", "full_body_view", "wide_scene"),
)


def _normalize_exclusive_check_states(checks_raw: object) -> Dict[str, bool]:
    raw = checks_raw if isinstance(checks_raw, dict) else {}
    def as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return False

    checks = {key: as_bool(raw.get(key, False)) for key in BUILT_IN_CHECK_KEYS}

    for group in EXCLUSIVE_CHECK_GROUPS:
        selected = next((key for key in group if checks[key]), None)
        for key in group:
            checks[key] = selected is not None and key == selected

    return checks


@dataclass(frozen=True)
class PromptPayload:
    page_key: str
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
    page_color_key: str = ""
    page_display_label: str = ""

    @property
    def image_name(self) -> str:
        page = _page_spec_for(self.page_key)
        return self.page_display_label or (page.display_label if page is not None else self.page_key)

    def first_paragraph(self) -> str:
        return _as_prompt_sentence(_join_nonempty(self.role_sentence, self.order_fragment, self.subject_fragment))

    def to_plain_text(self) -> str:
        paragraphs: List[str] = []
        if self.first_paragraph():
            paragraphs.append(self.first_paragraph())
        if self.baseline.strip():
            paragraphs.append(_as_prompt_sentence(self.baseline))
        if self.color_choice.strip():
            paragraphs.append(f"Use the {self.color_choice.strip()} palette.")
        if self.type_choice.strip():
            paragraphs.append(f"Use {self.type_choice.strip()} as the visual style.")
        if self.global_extra.strip():
            paragraphs.append(f"Shared visual direction: {self.global_extra.strip()}")
        if self.image_extra.strip():
            paragraphs.append(f"Page-specific direction: {self.image_extra.strip()}")
        if self.effort_line.strip():
            paragraphs.append(_format_effort_line(self.effort_line))
        if self.guidance_lines:
            paragraphs.append("Guidance:\n" + "\n".join(f"- {line}" for line in self.guidance_lines))
        if self.format_paragraph.strip():
            paragraphs.append(_as_prompt_sentence(self.format_paragraph))
        return "\n\n".join(part for part in paragraphs if part.strip())


@dataclass(frozen=True)
class ManagedListEntry:
    text: str
    is_header: bool = False
    is_user: bool = False


class HeaderAwareItemDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._header_color = QColor(COL_HEADER_TEXT)

    def apply_theme_tokens(self, tokens: object | None) -> None:
        self._header_color = QColor(
            str(getattr(tokens, "primary", COL_HEADER_TEXT))
        )

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
            painter.setPen(self._header_color)
            painter.drawText(rect, Qt.AlignVCenter | Qt.TextSingleLine, index.data(Qt.DisplayRole) or "")
            painter.restore()
            return
        super().paint(painter, option, index)


class ClickThroughNotice(QtWidgets.QLabel):
    """Frameless notice that observes clicks without intercepting them."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        flags = (
            Qt.ToolTip
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        super().__init__(parent, flags)
        self.setObjectName("clickThroughNotice")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMargin(10)
        self.setStyleSheet(
            "QLabel#clickThroughNotice {"
            "background: #101820;"
            "color: #dff7ff;"
            "border: 1px solid #35c8e6;"
            "border-radius: 8px;"
            "font-weight: 700;"
            "}"
        )
        self._filter_installed = False

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state_changed)

    def apply_theme_tokens(self, tokens: object | None) -> None:
        self.setStyleSheet(
            "QLabel#clickThroughNotice{"
            f"background:{getattr(tokens, 'panel_background', '#101820')};"
            f"color:{getattr(tokens, 'text', '#dff7ff')};"
            f"border:1px solid {getattr(tokens, 'accent', '#35c8e6')};"
            "border-radius:8px;font-weight:700;}"
        )

    def show_message(self, text: str, *, anchor: QtWidgets.QWidget) -> None:
        self.setText(text)
        width = min(360, max(240, self.fontMetrics().horizontalAdvance(text) + 28))
        self.setFixedWidth(width)
        self.adjustSize()

        anchor_top = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        anchor_center = anchor.mapToGlobal(anchor.rect().center())
        screen = screen_for_launcher(anchor)
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 720)
        x = anchor_center.x() - (self.width() // 2)
        y = anchor_top.y() - self.height() - 8
        if y < available.top():
            y = anchor_top.y() + anchor.height() + 8
        x = max(available.left(), min(x, available.right() - self.width() + 1))
        y = max(available.top(), min(y, available.bottom() - self.height() + 1))
        self.move(x, y)

        app = QtWidgets.QApplication.instance()
        if app is not None and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True
        self.show()
        self.raise_()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self.isVisible() and event.type() in (
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonDblClick,
        ):
            self.hide()
        return False

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None and self._filter_installed:
            app.removeEventFilter(self)
            self._filter_installed = False
        super().hideEvent(event)

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationActive:
            self.hide()


class ListManagerDialog(QtWidgets.QDialog):
    entries_changed = QtCore.Signal(list)

    def __init__(
        self,
        *,
        title: str,
        entries: List[ManagedListEntry],
        allow_headers: bool = False,
        auto_user_header: Optional[str] = None,
        user_owned_only: bool = False,
        app_font_family: str = "Segoe UI",
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._entries: List[ManagedListEntry] = [
            ManagedListEntry(entry.text, entry.is_header, entry.is_user)
            for entry in entries
        ]
        self._allow_headers = bool(allow_headers)
        self._auto_user_header = _normalize_text(auto_user_header, strip=True, max_length=120)
        self._user_owned_only = bool(user_owned_only)
        self._save_entries_callback: Optional[
            Callable[[List[ManagedListEntry], Optional[str]], bool]
        ] = None
        self._header_color = QColor(COL_HEADER_TEXT)
        self._duplicate_notice = ClickThroughNotice(self)
        self.setWindowTitle(f"Manage {title}")
        self.setModal(False)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        screen = screen_for_launcher(parent)
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 720)
        default_height = max(260, min(available.height() - 240, 360))

        self.resize(500, default_height)
        self.setMinimumSize(400, 260)
        self.setMaximumHeight(default_height)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title_label = QtWidgets.QLabel(f"Manage {title}", self)
        title_label.setObjectName("managerTitle")
        root.addWidget(title_label)

        hint_label = QtWidgets.QLabel(
            "Select an entry to update or remove it. New entries are saved to your library.",
            self,
        )
        hint_label.setObjectName("managerHint")
        hint_label.setWordWrap(True)
        root.addWidget(hint_label)

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
        set_control_help(
            self.entry_edit,
            "Enter the option text you want to add or use to update the selected entry.",
        )
        set_control_help(
            self.list_widget,
            "Select a saved option to edit or remove it.",
        )
        set_control_help(
            self.btn_add,
            "Add the entered option to this Prompt Writer list.",
        )
        set_control_help(
            self.btn_update,
            "Replace the selected option with the entered text.",
        )
        set_control_help(
            self.btn_remove,
            "Remove the selected option from this Prompt Writer list.",
        )
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

        self._base_stylesheet = """
            QDialog {
                background: #1a1b1d;
                border: 1px solid #343a3e;
                border-radius: 14px;
            }
            QLabel {
                color: #edf7fb;
                font-family: '__APP_FONT_FAMILY__';
            }
            QLabel#managerTitle {
                color: #f2fbff;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#managerHint {
                color: #93a7b3;
                font-size: 10px;
            }
            QListWidget {
                background: #151719;
                color: #edf7fb;
                border: 1px solid #373d42;
                border-radius: 10px;
                padding: 6px;
                outline: none;
                selection-background-color: #173f4d;
            }
            QLineEdit {
                min-height: 24px;
                background: #151719;
                color: #edf7fb;
                border: 1px solid #373d42;
                border-radius: 9px;
                padding: 6px 8px;
                selection-background-color: #17485a;
            }
            QLineEdit:focus {
                border-color: #00b2b2;
            }
            QPushButton {
                min-height: 26px;
                background: #25282b;
                color: #edf7fb;
                border: 1px solid #3a4045;
                border-radius: 10px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #293537;
                border-color: #00b2b2;
            }
            QPushButton:disabled {
                color: #61727a;
                background: #121a21;
                border-color: #293942;
            }
            """
        self.apply_app_font_family(app_font_family)

        self._refresh_list()
        self._sync_button_state()

    def apply_app_font_family(self, family: object) -> None:
        family_name = str(family or "Segoe UI").strip() or "Segoe UI"
        font = self.font()
        font.setFamily(family_name)
        self.setFont(font)
        self.setStyleSheet(
            self._base_stylesheet.replace(
                "'__APP_FONT_FAMILY__'",
                f"'{_qss_font_family(family_name)}'",
            )
        )

    def apply_theme_tokens(self, tokens: object | None) -> None:
        self._header_color = QColor(
            str(getattr(tokens, "primary", COL_HEADER_TEXT))
        )
        self._duplicate_notice.apply_theme_tokens(tokens)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if bool(item.data(MANAGED_LIST_HEADER_ROLE)):
                item.setForeground(self._header_color)

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
        self._duplicate_notice.hide()
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

    def _is_editable_row(self, row: int) -> bool:
        return self._is_selectable_row(row) and (
            not self._user_owned_only or self._entries[row].is_user
        )

    def _duplicate_conflict(self, text: str, *, exclude_row: int = -1) -> Optional[str]:
        candidate = _managed_entry_key(text)
        if not candidate:
            return None
        for index, entry in enumerate(self._entries):
            if index == exclude_row or entry.is_header:
                continue
            if _managed_entry_key(entry.text) == candidate:
                return entry.text
        return None

    def _show_duplicate_notice(self, conflict: str) -> None:
        self._duplicate_notice.show_message(
            f'"{conflict}" already exists.',
            anchor=self.entry_edit,
        )

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
        place_window_on_launcher(self)

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
                item.setForeground(self._header_color)
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
        self._sync_editor_from_selection(self._selected_row())
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
        editable = self._is_editable_row(self._selected_row())
        self.btn_update.setEnabled(editable)
        self.btn_remove.setEnabled(editable)

    def _emit_entries_changed(self, *, selected_text: Optional[str] = None) -> bool:
        entries = [
            ManagedListEntry(entry.text, entry.is_header, entry.is_user)
            for entry in self._entries
        ]
        if self._save_entries_callback is not None:
            if not self._save_entries_callback(entries, selected_text):
                return False
        self.entries_changed.emit(entries)
        return True

    def _submit_from_enter(self) -> None:
        if self._is_editable_row(self._selected_row()):
            self._update_entry()
        else:
            self._add_entry()

    def _add_entry(self) -> None:
        entry = self._current_form_entry()
        if entry is None:
            show_lettersmith_message(self, "Invalid entry", "Enter a non-empty option.")
            return
        conflict = self._duplicate_conflict(entry.text)
        if conflict is not None:
            self._show_duplicate_notice(conflict)
            return
        previous_entries = list(self._entries)
        previous_row = self._selected_row()
        draft_text = self.entry_edit.text()
        if self._allow_headers and self._auto_user_header:
            insert_at = self._insert_under_auto_header(ManagedListEntry(entry.text, False, True))
        else:
            row = self._selected_row()
            insert_at = row + 1 if row >= 0 else len(self._entries)
            self._entries.insert(insert_at, ManagedListEntry(entry.text, False, True))
        self._refresh_list(insert_at)
        if not self._emit_entries_changed(selected_text=entry.text):
            self._entries = previous_entries
            self._refresh_list(previous_row)
            self.entry_edit.setText(draft_text)
            return
        play_ui_sound(UiSound.ADDED)

    def _update_entry(self) -> None:
        row = self._selected_row()
        entry = self._current_form_entry()
        if row < 0 or entry is None or not self._is_editable_row(row):
            return
        conflict = self._duplicate_conflict(entry.text, exclude_row=row)
        if conflict is not None:
            self._show_duplicate_notice(conflict)
            return
        self._entries[row] = ManagedListEntry(entry.text, False, self._entries[row].is_user)
        self._refresh_list(row)
        self._emit_entries_changed()

    def _remove_entry(self) -> None:
        row = self._selected_row()
        if not self._is_editable_row(row):
            return
        del self._entries[row]
        self._cleanup_auto_header()
        next_row = min(row, len(self._entries) - 1)
        self._refresh_list(next_row)
        self._emit_entries_changed()
        play_ui_sound(UiSound.REMOVED)


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


def _normalize_prompt_fragment(value: object, *, max_length: int = MAX_STATE_TEXT_LENGTH) -> str:
    """Normalize prompt prose without changing the user's intended wording."""
    text = _normalize_text(value, strip=True, max_length=max_length)
    if not text:
        return ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _without_terminal_punctuation(value: object, *, max_length: int = MAX_STATE_TEXT_LENGTH) -> str:
    return re.sub(r"[.!?]+$", "", _normalize_prompt_fragment(value, max_length=max_length)).rstrip()


def _as_prompt_sentence(value: object, *, max_length: int = MAX_STATE_TEXT_LENGTH) -> str:
    text = _without_terminal_punctuation(value, max_length=max_length)
    return f"{text}." if text else ""


def _format_effort_line(value: object) -> str:
    text = _without_terminal_punctuation(value)
    text = re.sub(r"^(?:use|apply|prioritize|achieve)\s+", "", text, flags=re.IGNORECASE)
    return f"Render with {text}." if text else ""


def _format_order_fragment(order: object) -> str:
    if isinstance(order, (list, tuple)):
        items = [
            _without_terminal_punctuation(item)
            for item in order
            if _normalize_prompt_fragment(item, max_length=300)
        ]
    else:
        items = [_without_terminal_punctuation(order)] if _normalize_prompt_fragment(order, max_length=300) else []
    items = [item for item in items if item]
    if not items:
        return "Create a detailed image of"

    joined = " ".join(items).strip()
    # Older saved state used keyword-only values instead of an imperative line.
    if all(item.casefold() in {"composition", "lighting", "mood"} for item in items):
        return "Compose the image with intentional composition, lighting, and mood for"
    if not re.match(r"^(create|compose|render|design|produce|illustrate|make|depict|show|generate)\b", joined, re.I):
        joined = f"Create a detailed image of {joined}"
    return joined


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


def _managed_entry_key(value: object) -> str:
    text = _clean_user_added_entry(value)
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("`", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _normalize_guidance_lines(guidance: Optional[List[str]]) -> Tuple[str, ...]:
    return tuple(
        _as_prompt_sentence(line, max_length=400)
        for line in (guidance or [])
        if _normalize_prompt_fragment(line, max_length=400)
    )


def _set_help(widget: QtWidgets.QWidget, text: str) -> None:
    help_text = _normalize_text(text, strip=True, max_length=900)
    set_control_help(widget, help_text)


def _normalize_managed_list_line(line: object) -> str:
    text = _normalize_text(line, strip=True, max_length=0)
    if len(text) > MAX_MANAGED_LIST_ENTRY_LENGTH:
        LOGGER.warning(
            "Prompt Writer module-list entry rejected because it exceeds %d characters",
            MAX_MANAGED_LIST_ENTRY_LENGTH,
        )
        return ""
    return text


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
    seen: set[tuple[str, bool]] = set()
    for raw_line in lines:
        stripped = _normalize_managed_list_line(raw_line)
        if not stripped:
            continue
        if stripped.startswith("-") or stripped.startswith("•"):
            if not allow_headers:
                continue
            header_text = _clean_header_text(stripped)
            key = (header_text.casefold(), True)
            if header_text and key not in seen:
                entries.append(ManagedListEntry(text=header_text, is_header=True))
                seen.add(key)
            continue
        item_text = _clean_choice_line(stripped)
        key = (_managed_entry_key(item_text), False)
        if item_text and key not in seen:
            entries.append(ManagedListEntry(text=item_text, is_header=False))
            seen.add(key)
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
        stripped = _normalize_managed_list_line(raw_line)
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

    seen_headers: set[str] = set()
    seen_items: set[str] = set()
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
        header_key = display_header.casefold()
        if header_key not in seen_headers:
            entries.append(ManagedListEntry(display_header, True))
            seen_headers.add(header_key)
        for item in items:
            item_key = _managed_entry_key(item.text)
            if item_key and item_key not in seen_items:
                entries.append(ManagedListEntry(item.text, False, False))
                seen_items.add(item_key)

    if user_items:
        entries.append(ManagedListEntry(USER_ADDED_HEADER, True, True))
        for item in user_items:
            item_key = _managed_entry_key(item.text)
            if item_key and item_key not in seen_items:
                entries.append(ManagedListEntry(item.text, False, True))
                seen_items.add(item_key)
    return entries


def _serialize_managed_list_entries(entries: List[ManagedListEntry], *, allow_headers: bool) -> str:
    lines: List[str] = []
    for entry in entries:
        text = _normalize_managed_list_line(entry.text)
        if not text:
            continue
        if allow_headers and entry.is_header:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"-{text}")
        else:
            lines.append(text)
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def render_prompt_html(
    payload: PromptPayload,
    *,
    color_for: Callable[[str], str] = prompt_color,
) -> str:
    """Render the preview with colored values (preview should match emitted text)."""
    page = _page_spec_for(payload.page_key)
    col_img = color_for(payload.page_color_key) if payload.page_color_key else (page.preview_color if page is not None else color_for("back"))
    parts: list[str] = []

    first = _join_nonempty(payload.role_sentence, payload.order_fragment)
    if payload.subject_fragment.strip():
        first = (first + " " if first else "") + _span(
            payload.subject_fragment.strip(),
            color_for("subject"),
            bold=True,
        )
    if first:
        parts.append(first.rstrip(".!?") + ".")

    if payload.baseline.strip():
        parts.append(_html_escape(_as_prompt_sentence(payload.baseline)))

    if payload.color_choice.strip():
        parts.append(
            "Use the "
            + _span(payload.color_choice.strip(), color_for("scheme"), bold=True)
            + " palette."
        )

    if payload.type_choice.strip():
        parts.append(
            "Use "
            + _span(payload.type_choice.strip(), color_for("type"), bold=True)
            + " as the visual style."
        )

    if payload.global_extra.strip():
        parts.append(
            "Shared visual direction: "
            + _span(payload.global_extra.strip(), color_for("global"))
        )

    if payload.image_extra.strip():
        parts.append("Page-specific direction: " + _span(payload.image_extra.strip(), col_img))

    if payload.effort_line.strip():
        parts.append(_html_escape(_format_effort_line(payload.effort_line)))

    if payload.guidance_lines:
        helpful_color = color_for("helpful")
        g = "<br>".join(_span(f"- {line}", helpful_color) for line in payload.guidance_lines)
        parts.append(_span("Guidance:", helpful_color, bold=True) + "<br>" + g)

    if payload.format_paragraph.strip():
        parts.append(_html_escape(_as_prompt_sentence(payload.format_paragraph)))

    return "<br><br>".join(p for p in parts if str(p).strip())


CHECKBOX_QSS = """
QCheckBox {
    color: #d9e6ec;
    spacing: 8px;
    font-size: 10px;
}
QCheckBox:disabled { color: #667985; }
"""


class GoldenCheckBox(QtWidgets.QCheckBox):
    """Checkbox painted from the active application's semantic theme."""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._theme_tokens: object | None = None

    def apply_theme_tokens(self, tokens: object | None) -> None:
        self._theme_tokens = tokens
        self.update()

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
        tokens = self._theme_tokens
        border = QColor(
            str(
                getattr(
                    tokens,
                    "accent"
                    if self.isChecked()
                    else "primary"
                    if hovered
                    else "control_border",
                    UI_ACCENT if self.isChecked() else "#48616f" if hovered else "#344956",
                )
            )
        )
        fill = QColor(
            str(
                getattr(
                    tokens,
                    "selected_background"
                    if self.isChecked()
                    else "hover"
                    if hovered
                    else "control_background",
                    "#123d49" if self.isChecked() else "#17232c" if hovered else "#0c141a",
                )
            )
        )

        painter.setPen(QtGui.QPen(border, 1.35))
        painter.setBrush(fill)
        painter.drawRoundedRect(QtCore.QRectF(indicator), 3, 3)

        if self.isChecked():
            pen = QtGui.QPen(
                QColor(str(getattr(tokens, "selected_text", UI_TEXT_PRIMARY))),
                2.35,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
            painter.setPen(pen)
            path = QtGui.QPainterPath()
            path.moveTo(indicator.left() + 3.5, indicator.center().y() + 0.5)
            path.lineTo(indicator.left() + 6.5, indicator.bottom() - 4.0)
            path.lineTo(indicator.right() - 3.0, indicator.top() + 4.0)
            painter.drawPath(path)

        painter.setPen(
            QColor(
                str(
                    getattr(
                        tokens,
                        "text" if self.isEnabled() else "muted_text",
                        "#d9e6ec" if self.isEnabled() else "#667985",
                    )
                )
            )
        )
        painter.drawText(contents, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()


def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _candidate_paths_for(name: str | Path) -> List[Path]:
    path = Path(name)
    if not path.is_absolute():
        path = application_paths().bundled_prompt_writer_root / path.name
    return [path.resolve()]


def _read_list_file(name: str | Path) -> Tuple[List[str], Optional[Path], Optional[Tuple[int, int]]]:
    p = _candidate_paths_for(name)[0]
    try:
        if not p.exists() or not p.is_file():
            LOGGER.error("Prompt Writer module list is missing: %s", p)
            return [], None, None
        with p.open("r", encoding="utf-8-sig") as fh:
            lines = [ln.rstrip("\r\n") for ln in fh.readlines()]
            return lines, p, _file_signature(p)
    except (OSError, UnicodeError) as error:
        LOGGER.exception("Prompt Writer module list could not be read: %s (%s)", p, error)
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
    page_identifier: str | PageSpec,
    *,
    type_choice: Optional[str] = None,
    color_choice: Optional[str] = None,
    guidance: Optional[List[str]] = None,
    global_extra: Optional[str] = None,
    image_extra: Optional[str] = None,
) -> Tuple[PromptPayload, dict]:
    page = _page_spec_for(page_identifier)
    page_key = page.key if page is not None else _normalize_text(page_identifier, strip=True, max_length=80)
    display_label = page.display_label if page is not None else page_key
    baseline_text = _normalize_prompt_fragment(page.baseline if page is not None else "")

    role = _normalize_prompt_fragment(data.get("role", "Artist"), max_length=500)
    order = data.get("order", [])
    effort_line = _normalize_prompt_fragment(data.get("effort", ""), max_length=1200)
    format_paragraph = _normalize_prompt_fragment(data.get("format", ""), max_length=2400)
    type_text = _normalize_prompt_fragment(type_choice or "", max_length=300)
    color_text = _normalize_prompt_fragment(color_choice or "", max_length=300)
    global_text = _normalize_prompt_fragment(global_extra or "")
    image_text = _normalize_prompt_fragment(image_extra or "")
    guidance_lines = _normalize_guidance_lines(guidance)

    dbg = {
        "page": page_key,
        "image": display_label,
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

    role_text = _without_terminal_punctuation(role)
    role_sentence = f"You are {_as_prompt_sentence(role_text)}" if role_text else ""

    order_core = _format_order_fragment(order)
    if order_core:
        order_core = order_core[0].upper() + order_core[1:]

    subject_core = ""
    if subject:
        s = _without_terminal_punctuation(_normalize_text(subject, strip=True, max_length=300))
        if s:
            subject_core = s

    payload = PromptPayload(
        page_key=page_key,
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
        page_color_key=page.color_key if page is not None else "",
        page_display_label=display_label,
    )
    return payload, dbg


def assemble_prompt_for_image(
    subject: str,
    data: dict,
    page_identifier: str | PageSpec,
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
        page_identifier,
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


def empty_prompt_writer_state() -> dict:
    return {
        "version": PROMPT_WRITER_STATE_VERSION,
        "type": "",
        "subject": "",
        "color": "",
        "global": "",
        **{page.key: "" for page in PAGE_SPECS},
        "checks": {key: False for key in BUILT_IN_CHECK_KEYS},
        "resolved_instructions": {},
        "generated_prompts": {},
        "generated_input_signature": "",
        "approved_images": [],
    }


def reset_prompt_writer_state_file(project_root: str | Path) -> bool:
    path = Path(project_root).resolve() / "prompt_writer_state.json"
    try:
        safe_write_json(path, empty_prompt_writer_state())
        return True
    except (OSError, TypeError, ValueError) as error:
        LOGGER.exception("Prompt Writer reset state save failed: %s (%s)", path, error)
        return False


class PromptWriterPanel(QtWidgets.QWidget):
    dismissed = QtCore.Signal()
    project_changed = QtCore.Signal()
    session_event = QtCore.Signal(object)
    page_settings_requested = QtCore.Signal(str)
    provider_requested = QtCore.Signal()
    prompts_generated = QtCore.Signal(dict, dict)  # prompts_map, debug_map

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        project_root: Optional[str] = None,
        *,
        language_service: Optional[object] = None,
        page_id: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setObjectName("PromptWriterPanel")
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.project_root = Path(project_root).resolve() if project_root else self._discover_project_root()
        self.application_paths = application_paths(self.project_root)
        self._language_service = language_service or get_language_service(self.project_root)
        self.setWindowTitle("Letter Smith — Prompt Writer")
        apply_qt_window_icon(self, self.project_root)

        # Prompt Writer persistence (separate file so other modules can\'t overwrite it)
        self._state_path = self.application_paths.workspace_root / "prompt_writer_state.json"
        set_path_hidden(self._state_path)
        self._persist_timer = QtCore.QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._persist_state_now)
        self._state_persistence_suspended = False
        self._persistence_failed = False
        self._state_write_blocked = False
        self._shutdown = False

        self._normal_geometry: Optional[QtCore.QRect] = None

        self._geom_anim = QtCore.QPropertyAnimation(self, b"geometry", self)
        self._fade_anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._finish_close_animation)
        self._animation_generation = 0

        role_lines, _ = _read_list_file_cached(self._default_modules_dir() / "role.txt")
        seeded_role = _clean_choice_line(role_lines[0]) if role_lines and any(l.strip() for l in role_lines) else "Artist"

        default_format = (
            "Deliver a portrait image at exactly 2048×3072 pixels. Maintain accurate perspective, consistent lighting, "
            "clear silhouettes, safe margins, smooth color transitions, and deliberate negative space. Avoid clutter, "
            "banding, oversaturation, duplicated or merged objects, disconnected handles, heads, nibs, feathers, limbs, "
            "or other structural parts, malformed text, unintended symbols, and cropped essential details."
        )

        ultra_effort = (
            "use maximum visual fidelity, physically coherent construction, correct anatomy and object geometry, "
            "deliberate composition, and clearly defined materials and textures."
        )

        self._data = {
            "role": seeded_role,
            "order": ["composition", "lighting", "mood"],
            "effort": ultra_effort,
            "format": default_format,
        }

        self.session = get_project_session(self.project_root)
        self.page_controller = page_controller(self.project_root)
        self.page_controller.reload()
        self._view_page_id = None
        self._view_session_id = self.session.session_id
        self._writing = False
        self._active_batch = None
        self._last_batch = None
        self._view_spec = replace(PAGE_SPECS[0], key="current")
        self._page_specs = [self._view_spec]
        self._all_page_specs = {"current": self._view_spec}
        self._generated_prompts: Dict[str, str] = {}
        self._resolved_instructions: Dict[str, object] = {}
        self._generated_input_signature = ""
        self._generated_output_valid = False
        self._generation_in_progress = False
        self._site_request_in_progress = False
        self._browser = None
        self._image_tab = None
        self._approved_pages: set[str] = set()
        self._pending_approval_key = ""
        self._browser_targets = {}
        self._colors_path_used: Optional[Path] = None
        self._last_focused_widget: Optional[QtWidgets.QTextEdit] = None
        self._list_manager_dialogs: Dict[str, ListManagerDialog] = {}
        self._list_save_in_progress = False
        self._app_font_family = "Segoe UI"
        self._theme_tokens: object | None = None
        self._ui_header_color = QColor(COL_HEADER_TEXT)

        self._build_ui()
        self._apply_styles()
        theme_service = getattr(parent, "theme_service", None)
        if theme_service is not None:
            self.apply_theme_assets(theme_service)
        self.refresh_semantic_palette()
        self._connect_signals()
        self.session_event.connect(self._session_changed)
        self.session.add_listener(self.session_event.emit)
        listener = self.session_event.emit
        session = self.session
        self.destroyed.connect(lambda *_: session.remove_listener(listener))
        self._load_colors_into_combo()
        self._start_visionary_pulse()
        if page_id is not None:
            self.session.select_page(page_id)
        self.reload_project_state()

    def select_page(self, page_id):
        if self._state_persistence_suspended or page_id is None:
            return False
        if (self._persist_timer.isActive() or self._persistence_failed) and not self.persist_project_state():
            with QtCore.QSignalBlocker(self.target_selector):
                self.target_selector.setCurrentIndex(self.target_selector.findData(self._view_page_id))
            self.prompt_status.setText("Prompt draft could not be saved. Retry before changing pages.")
            return False
        try:
            self.session.select_page(page_id)
            self.reload_project_state()
            return True
        except KeyError:
            return False

    @QtCore.Slot(object)
    def _session_changed(self, event):
        if self._writing or self._state_persistence_suspended or event.session_id != self.session.session_id:
            return
        if event.kind == "persisted":
            return
        self.reload_project_state()

    def _can_edit_page(self):
        return (not self._state_persistence_suspended and not self.session.is_restoring
                and self._view_session_id == self.session.session_id
                and self._view_page_id in {page["id"] for page in self.session.project.pages})

    def _authored_change(self):
        self._refresh_status()
        self._schedule_persist_state()
        self.project_changed.emit()

    def _page_input_changed(self, *_args):
        if not self._can_edit_page():
            return
        page_id = self._view_page_id
        prompt = self.session.project.get_prompt(page_id)
        sender = self.sender()
        if sender is self._view_spec.detail_widget:
            patch = {"directions": self._view_spec.detail_widget.toPlainText()}
        else:
            overrides = dict(prompt["overrides"])
            if sender in (self.use_project_palette, self.page_palette):
                if self.use_project_palette.isChecked():
                    overrides.pop("color", None)
                else:
                    overrides["color"] = self.page_palette.currentText()
                self.page_palette.setEnabled(not self.use_project_palette.isChecked())
            elif sender in (self.use_project_style, self.page_style):
                if self.use_project_style.isChecked():
                    overrides.pop("type", None)
                else:
                    overrides["type"] = self.page_style.text()
                self.page_style.setEnabled(not self.use_project_style.isChecked())
            elif sender is self.page_subject:
                if self.page_subject.text():
                    overrides["subject"] = self.page_subject.text()
                else:
                    overrides.pop("subject", None)
            patch = {"overrides": overrides}
        self._writing = True
        try:
            self.session.project.update_prompt(page_id, patch)
        finally:
            self._writing = False
        self._authored_change()

    def _manual_prompt_changed(self):
        if not self._can_edit_page():
            return
        self._writing = True
        try:
            self.session.project.update_prompt(self._view_page_id, {"text": self._view_spec.preview_widget.toPlainText()})
        finally:
            self._writing = False
        self._authored_change()
        self._highlight_saved_prompt(self._view_spec)

    def _output_changed(self, *_args):
        if self._state_persistence_suspended:
            return
        self._writing = True
        try:
            self.session.project.update_project_data("presentation", {"artwork_output": {
                "width": self.output_width.value(), "height": self.output_height.value()}})
        finally:
            self._writing = False
        self._authored_change()

    def _refresh_status(self):
        project = self.session.project
        shared = project.to_dict()["prompt_settings"]
        self.style_summary.setText(" · ".join(str(shared.get(key) or "Not selected") for key in ("subject", "type", "color")))
        blockers = [QtCore.QSignalBlocker(self.page_palette), QtCore.QSignalBlocker(self.page_style)]
        if self.use_project_palette.isChecked():
            self.page_palette.setCurrentText(shared.get("color", ""))
        if self.use_project_style.isChecked():
            self.page_style.setText(shared.get("type", ""))
        self.page_subject.setPlaceholderText(shared.get("subject") or "Use project subject")
        del blockers
        if self._view_page_id is not None:
            page = project.get_page(self._view_page_id)
            prompt = page["prompt"]
            self._generated_prompts = {item["id"]: item["prompt"]["text"] for item in project.pages if item["prompt"]["text"] is not None}
            self._generated_output_valid = prompt["text"] is not None and not prompt["stale"]
            self.btn_generate.setText(f"Build Prompt for Page {project.prompt_context(page['id'])['position']}")
            self.prompt_status.setText("Settings changed since this prompt was built." if prompt["stale"] and prompt["text"] is not None
                                       else "Ready to build." if prompt["text"] is None else "Saved prompt — editable.")
            self.btn_restore.setEnabled(isinstance(page["recovery"].get("previous_prompt", {}).get("text"), str))
            try:
                geometry = project.prompt_geometry(page["id"])
                area = geometry["text_area"]
                text = f"{PAGE_TYPES[page['type']].label} · {geometry['width']} × {geometry['height']} · {geometry['aspect_ratio']}"
                if area is not None:
                    if area.get("mode"):
                        text += " · Text: responsive centered panel"
                    else:
                        scale, unit = (100, '%') if area['units'] == 'normalized' else (1, 'px')
                        text += (f" · Text area: left {area['x'] * scale:g}{unit}, top {area['y'] * scale:g}{unit}, "
                                 f"{area['width'] * scale:g}{unit} × {area['height'] * scale:g}{unit}")
                self.layout_summary.setText(text)
            except ValueError as error:
                self.layout_summary.setText(str(error))
        else:
            self.btn_restore.setEnabled(False)
            self.prompt_status.setText("Add a page in Images to begin.")
            self.layout_summary.clear()
        self._sync_action_button_states()

    def _restore_previous(self):
        if self._can_edit_page():
            self.session.project.restore_previous_prompt(self._view_page_id)
            self._authored_change()

    def _reroll_shared(self):
        confirm = LetterSmithConfirmationDialog(self, title="Reroll shared wording",
            question="Choose new shared wording for future builds? Existing prompts stay unchanged and become stale.",
            primary_text="Reroll", secondary_text="Cancel")
        if confirm.exec() == QtWidgets.QDialog.Accepted:
            self.session.project.resolve_prompt_components(self._roll_shared_prompt_data, reroll=True)
            self._authored_change()

    def _guidance_phrases(self):
        names = {id(widget): key for widget, key in self._checkbox_state_specs()}
        return {names[id(widget)]: phrase for widget, phrase in self._guidance_checkbox_specs()}

    def _choose_batch(self, all_pages=False):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Build Prompts for Selected Pages")
        layout = QtWidgets.QVBoxLayout(dialog)
        listing = QtWidgets.QListWidget()
        for page in self.session.project.pages:
            suffix = " — replace existing prompt" if page["prompt"]["text"] is not None else " — first build"
            item = QtWidgets.QListWidgetItem(page_label(self.session.project, page["id"]) + suffix)
            item.setData(Qt.UserRole, page["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if all_pages or page["id"] == self._view_page_id else Qt.Unchecked)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Build selected prompts")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(620, 340)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            ids = [listing.item(i).data(Qt.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.Checked]
            if ids:
                self._build_pages(ids)

    def _confirm_replacement(self, batch):
        if not any(item.replaces for item in batch.items):
            return True
        question = "\n".join(item.label + (" — replace existing prompt" if item.replaces else " — first build") for item in batch.items)
        question += "\n\nPrevious prompts will remain available through Restore Previous Prompt."
        dialog = LetterSmithConfirmationDialog(self, title="Build artwork prompts", question=question,
            primary_text="Build", secondary_text="Cancel", click_outside_dismiss=False, width=620)
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _build_pages(self, page_ids, *, confirm=True):
        if self._generation_in_progress:
            return None
        try:
            batch = PromptBatch(self.session, page_ids, self._roll_shared_prompt_data, self._guidance_phrases())
        except (KeyError, ValueError, RuntimeError) as error:
            show_lettersmith_message(self, "Cannot build prompts", str(error))
            return None
        if confirm and not self._confirm_replacement(batch):
            batch.cancel()
            for item in batch.items:
                batch.run_item(item)
            self._last_batch = batch
            self._show_batch_results(batch)
            return batch
        self._active_batch = batch
        self._set_generation_busy(True)
        progress = QtWidgets.QProgressDialog("Building artwork prompts…", "Cancel", 0, len(batch.items), self)
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(batch.cancel)
        self._batch_progress = progress
        index = 0
        def step():
            nonlocal index
            if self._shutdown:
                batch.cancel()
            if index < len(batch.items):
                batch.run_item(batch.items[index])
                index += 1
                progress.setValue(index)
                if index < len(batch.items):
                    QtCore.QTimer.singleShot(0, step)
                else:
                    step()
                return
            progress.close()
            self._active_batch = None
            self._last_batch = batch
            self._set_generation_busy(False)
            self.reload_project_state()
            self._show_batch_results(batch)
            if batch.items and batch.items[0].target.session_id == self.session.session_id:
                if not self._persist_state_now():
                    self.batch_status.setText(self.batch_status.text() + "\nSave failed; prompts remain in the open project. Retry Save.")
                self.project_changed.emit()
                self.prompts_generated.emit({item.page_id: self.session.project.get_prompt(item.page_id)["text"]
                                             for item in batch.items if item.status == "success" and item.page_id in {p['id'] for p in self.session.project.pages}}, {})
        step()
        return batch

    def _show_batch_results(self, batch):
        self.batch_status.setText("\n".join(f"{item.label}: {item.status} — {item.detail}" for item in batch.items))
        self._batch_results_scroll.setVisible(bool(batch.items))
        self._sync_action_button_states()

    def _retry_batch(self):
        batch = self._last_batch
        if batch is None or not batch.items or batch.items[0].target.session_id != self.session.session_id:
            return
        ids = {page["id"] for page in self.session.project.pages}
        failed = [item.page_id for item in batch.items if item.status in {"failed", "conflict"} and item.page_id in ids]
        if failed:
            self._build_pages(failed)

    def _page_layout_changed(self, persisted=False):
        self.reload_project_state()


    def _select_layout_page(self, key):
        from project_consumers import page_id_for_key
        page_id = page_id_for_key(self.project_root, key)
        if page_id is not None:
            self.select_page(page_id)


    def _module_path(self, name: str) -> Path:
        return self._builtin_modules_dir() / name

    # -----------------------
    # Persistence
    # -----------------------
    def _discover_project_root(self) -> Path:
        return application_paths().workspace_root

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
                "allow_none": True,
                "allow_headers": True,
                "auto_user_header": USER_ADDED_HEADER,
            },
            "subject": {
                "title": "Subject",
                "primary_name": "topic.txt",
                "allow_none": False,
                "allow_headers": False,
                "auto_user_header": None,
            },
            "color": {
                "title": "Color Scheme",
                "primary_name": "color.txt",
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
        return self._builtin_modules_dir()

    def _builtin_modules_dir(self) -> Path:
        return self.application_paths.bundled_prompt_writer_root.resolve()

    def _user_modules_dir(self) -> Path:
        return self.application_paths.prompt_writer_content_root.resolve()

    def _user_colors_path(self) -> Path:
        return self.application_paths.custom_palette_root / "user_colors.json"

    def _resolve_managed_list_path(self, key: str) -> Path:
        config = self._managed_list_config(key)
        filename = config["primary_name"]
        user_path = self._user_modules_dir() / filename
        return user_path if user_path.is_file() else self._builtin_modules_dir() / filename

    def _managed_list_write_path(self, key: str) -> Path:
        config = self._managed_list_config(key)
        return self._user_modules_dir() / config["primary_name"]

    def _load_user_colors(self) -> List[str]:
        path = self._user_colors_path()
        legacy_path = self._resolve_managed_list_path("color")

        def normalize(values: object) -> List[str]:
            if not isinstance(values, list):
                return []
            normalized: List[str] = []
            seen: set[str] = set()
            for value in values:
                text = _clean_user_added_entry(value)
                key = _managed_entry_key(text)
                if text and key not in seen:
                    normalized.append(text)
                    seen.add(key)
            return normalized

        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                values = payload.get("colors", []) if isinstance(payload, dict) else []
                if not isinstance(values, list):
                    raise ValueError("colors must be a list")
                return normalize(values)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                LOGGER.exception("User color storage could not be read: %s (%s)", path, error)
                return []
        legacy_lines, _ = _read_list_file_cached(legacy_path)
        legacy_entries = _parse_color_list_entries(legacy_lines)
        legacy_user = normalize([
            entry.text
            for entry in legacy_entries
            if entry.is_user and not entry.is_header
        ])
        if legacy_user:
            try:
                builtin_entries = [entry for entry in legacy_entries if not entry.is_user]
                migrated_text = _serialize_managed_list_entries(builtin_entries, allow_headers=True)
                verified_entries = _parse_color_list_entries(migrated_text.splitlines())
                if any(entry.is_user for entry in verified_entries):
                    raise ValueError("legacy User Added entries remain in migrated color data")
                safe_write_json(path, {"version": 1, "colors": legacy_user})
                if legacy_path.parent == self._user_modules_dir():
                    atomic_write_text(legacy_path, migrated_text)
                    _FILE_CACHE.pop(legacy_path, None)
                    _FILE_CACHE.pop(legacy_path.name, None)
                LOGGER.info("Migrated User Added colors to %s", path)
            except (OSError, ValueError, TypeError) as error:
                LOGGER.exception("User color migration failed: %s (%s)", path, error)
            return normalize(legacy_user)
        return []

    def _read_managed_entries(self, key: str) -> List[ManagedListEntry]:
        config = self._managed_list_config(key)
        path = self._resolve_managed_list_path(key)
        lines, _ = _read_list_file_cached(path)
        if key == "color":
            builtin_entries = [entry for entry in _parse_color_list_entries(lines) if not entry.is_user]
            builtin_keys = {
                _managed_entry_key(entry.text)
                for entry in builtin_entries
                if not entry.is_header
            }
            builtin_entries.append(ManagedListEntry(USER_ADDED_HEADER, True))
            for text in self._load_user_colors():
                item_key = _managed_entry_key(text)
                if item_key and item_key not in builtin_keys:
                    builtin_entries.append(ManagedListEntry(text, False, True))
                    builtin_keys.add(item_key)
            return builtin_entries
        return _parse_managed_list_entries(lines, allow_headers=bool(config["allow_headers"]))

    def _style_header_item(self, item: Optional[QtGui.QStandardItem]) -> None:
        if item is None:
            return
        item.setFlags(Qt.NoItemFlags)
        item.setData(True, MANAGED_LIST_HEADER_ROLE)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(self._ui_header_color)

    def _populate_managed_combo(
        self,
        combo: QtWidgets.QComboBox,
        entries: List[ManagedListEntry],
        *,
        allow_none: bool,
        normalized_match: bool = False,
    ) -> None:
        current_text = combo.currentText().strip()
        is_editable = combo.isEditable()

        signals_were_blocked = combo.blockSignals(True)
        try:
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
                restored_index = self._find_combo_text(
                    combo,
                    current_text,
                    normalized=normalized_match,
                )
                if restored_index >= 0:
                    combo.setCurrentIndex(restored_index)
                elif is_editable:
                    combo.setEditText(current_text)
                else:
                    # Keep a project-local historical selection available even
                    # when the permanent source library no longer contains it.
                    combo.addItem(current_text)
                    combo.setCurrentIndex(combo.count() - 1)
            elif allow_none and combo.count() > 0:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(signals_were_blocked)

    @staticmethod
    def _find_combo_text(
        combo: QtWidgets.QComboBox,
        value: str,
        *,
        normalized: bool,
    ) -> int:
        if not normalized:
            return combo.findText(value)
        target = _managed_entry_key(value)
        if not target:
            return -1
        for index in range(combo.count()):
            if _managed_entry_key(combo.itemText(index)) == target:
                return index
        return -1

    @staticmethod
    def _restore_combo_value(
        combo: QtWidgets.QComboBox,
        value: str,
        *,
        empty_index: int,
        normalized_match: bool = False,
    ) -> None:
        if not value:
            combo.setCurrentIndex(empty_index)
            return
        index = PromptWriterPanel._find_combo_text(
            combo,
            value,
            normalized=normalized_match,
        )
        if index < 0:
            combo.addItem(value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _reload_managed_list(self, key: str) -> None:
        config = self._managed_list_config(key)
        entries = self._read_managed_entries(key)
        combo = self._managed_combo_for_key(key)
        previous_text = combo.currentText().strip()
        self._populate_managed_combo(
            combo,
            entries,
            allow_none=bool(config["allow_none"]),
            normalized_match=key == "color",
        )
        if previous_text and combo.currentText().strip() != previous_text:
            self._invalidate_generated_output()
        if key == "color":
            self._colors_path_used = self._resolve_managed_list_path("color")
            if hasattr(self, "page_palette"):
                self._populate_managed_combo(self.page_palette, entries, allow_none=False, normalized_match=True)

    def _clear_managed_list_cache(self, path: Path) -> None:
        _FILE_CACHE.pop(path, None)
        _FILE_CACHE.pop(path.name, None)

    def _apply_managed_list_changes(
        self,
        key: str,
        entries: List[ManagedListEntry],
        *,
        selected_text: Optional[str] = None,
        target_combo=None,
    ) -> bool:
        if self._list_save_in_progress:
            LOGGER.warning("Prompt Writer list save ignored while another save is active: %s", key)
            return False
        self._list_save_in_progress = True
        config = self._managed_list_config(key)
        path = (
            self._user_colors_path()
            if key == "color"
            else self._managed_list_write_path(key)
        )
        try:
            if key == "color":
                values: List[str] = []
                seen: set[str] = set()
                for entry in entries:
                    if entry.is_header or not entry.is_user:
                        continue
                    value = _clean_user_added_entry(entry.text)
                    key_value = _managed_entry_key(value)
                    if value and key_value not in seen:
                        values.append(value)
                        seen.add(key_value)
                safe_write_json(path, {"version": 1, "colors": values})
            else:
                atomic_write_text(
                    path,
                    _serialize_managed_list_entries(entries, allow_headers=bool(config["allow_headers"])),
                )
            self._clear_managed_list_cache(path)
            self._reload_managed_list(key)
            if selected_text is not None:
                combo = target_combo if target_combo is not None else self._managed_combo_for_key(key)
                index = self._find_combo_text(
                    combo, selected_text, normalized=key == "color"
                )
                if index >= 0:
                    combo.setCurrentIndex(index)
            self._schedule_persist_state(0)
            return True
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            LOGGER.exception("Prompt Writer list save failed: %s (%s)", path, error)
            show_lettersmith_message(
                self,
                "Save failed",
                f"Could not save {config['title']} to:\n{path}\n\n{error}",
            )
            return False
        finally:
            self._list_save_in_progress = False

    def _open_list_manager(self, key: str, *, for_page=False) -> None:
        existing = self._list_manager_dialogs.get(key)
        if existing is not None and getattr(existing, "_for_page", False) != for_page:
            existing.close()
            existing = None
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        config = self._managed_list_config(key)
        dialog = ListManagerDialog(
            title=config["title"] + (" — This Page" if for_page else " — Project Style"),
            entries=self._read_managed_entries(key),
            allow_headers=bool(config["allow_headers"]),
            auto_user_header=config.get("auto_user_header"),
            user_owned_only=key == "color",
            app_font_family=self._app_font_family,
            parent=self,
        )
        dialog._for_page = for_page
        if for_page:
            self.use_project_palette.setChecked(False)
        dialog._save_entries_callback = (
            lambda entries, selected_text, list_key=key: self._apply_managed_list_changes(
                list_key, entries, selected_text=selected_text,
                target_combo=self.page_palette if for_page else None,
            )
        )
        dialog.finished.connect(lambda *_args, list_key=key: self._list_manager_dialogs.pop(list_key, None))
        self._list_manager_dialogs[key] = dialog
        dialog.apply_theme_tokens(self._theme_tokens)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _install_manage_trigger(self, widget: QtWidgets.QWidget, key: str) -> None:
        widget.setProperty("managed_list_key", key)
        widget.installEventFilter(self)

    def _schedule_persist_state(self, delay_ms: Optional[int] = None) -> None:
        try:
            if self._state_persistence_suspended or self._shutdown:
                return
            delay = STATE_PERSIST_DEBOUNCE_MS if delay_ms is None else max(0, int(delay_ms))
            self._persist_timer.start(delay)
        except (RuntimeError, TypeError, ValueError) as error:
            LOGGER.exception("Prompt Writer persistence scheduling failed: %s", error)

    def _persist_state_now(self) -> bool:
        self._persist_timer.stop()
        if self._state_persistence_suspended or self.session.is_restoring:
            return False
        try:
            self.session.save()
            self._persistence_failed = False
            return True
        except (OSError, ValueError, RuntimeError, TypeError):
            self._persistence_failed = True
            self.prompt_status.setText("Prompt draft could not be saved. Retry Save before continuing.")
            LOGGER.exception("Prompt Writer canonical save failed")
            return False


    def persist_project_state(self) -> bool:
        """Flush the current project-owned Prompt Writer workspace to disk."""
        return self._persist_state_now()

    def _normalize_persisted_state(self, state: object) -> dict:
        if not isinstance(state, dict):
            return {}

        checks_raw = state.get("checks", {})

        generated_raw = state.get("generated_prompts", {})
        if not isinstance(generated_raw, dict):
            generated_raw = {}

        generated_prompts: Dict[str, str] = {}
        page_details: Dict[str, str] = {}
        for page in self._all_page_specs.values():
            page_details[page.key] = _normalize_text(state.get(page.key, ""))
            generated_value = generated_raw.get(
                page.key,
                generated_raw.get(
                    page.display_label,
                    generated_raw.get("Back Prompt", "") if page.key == "back" else "",
                ),
            )
            generated_text = generated_value if isinstance(generated_value, str) else ""
            if generated_text:
                generated_prompts[page.key] = generated_text

        resolved_raw = state.get("resolved_instructions", {})
        resolved_instructions: Dict[str, object] = {}
        if isinstance(resolved_raw, dict):
            for key in ("role", "subject_lead_in", "effort", "format"):
                value = resolved_raw.get(key)
                if isinstance(value, str):
                    resolved_instructions[key] = value
                elif key == "subject_lead_in" and isinstance(value, list):
                    resolved_instructions[key] = [
                        item for item in value if isinstance(item, str)
                    ]

        approved_raw = state.get("approved_images", [])
        if not isinstance(approved_raw, list):
            approved_raw = []
        approved_images = [
            page.key for page in self._page_specs if page.key in approved_raw
        ]

        return {
            "version": PROMPT_WRITER_STATE_VERSION,
            "type": _normalize_text(state.get("type", ""), strip=True, max_length=300),
            "subject": _normalize_text(state.get("subject", ""), strip=True, max_length=300),
            "color": _normalize_text(state.get("color", ""), strip=True, max_length=300),
            "global": _normalize_text(state.get("global", "")),
            **page_details,
            "checks": _normalize_exclusive_check_states(checks_raw),
            "resolved_instructions": resolved_instructions,
            "generated_prompts": generated_prompts,
            "generated_output_stale": bool(state.get("generated_output_stale", False)),
            "approved_images": approved_images,
            "generated_input_signature": _normalize_text(
                state.get("generated_input_signature", ""),
                strip=True,
                max_length=64,
            ),
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

    def _current_prompt_input_signature(self) -> str:
        type_text = self.cmb_type.currentText().strip()
        if type_text == NONE_CHOICE_LABEL:
            type_text = ""
        selected_style = next(
            (key for checkbox, key in ((self.cb_real, "real"), (self.cb_paint, "paint"), (self.cb_minimal, "minimal")) if checkbox.isChecked()),
            "",
        )
        selected_framing = next(
            (key for checkbox, key in ((self.cb_close_up_focus, "close_up_focus"), (self.cb_full_body_view, "full_body_view"), (self.cb_wide_scene, "wide_scene")) if checkbox.isChecked()),
            "",
        )
        input_state = {
            "prompt_language_version": PROMPT_LANGUAGE_VERSION,
            "type": type_text,
            "subject": self.cmb_subject.currentText().strip(),
            "color": self._get_color_choice() or "",
            "global": self.txt_global.toPlainText().strip(),
            "checks": {
                state_key: bool(checkbox.isChecked())
                for checkbox, state_key in self._checkbox_state_specs()
            },
            "hidden_style_default": not bool(selected_style),
            "hidden_framing_default": not bool(selected_framing),
        }
        input_state.update(
            {
                page.key: page.detail_widget.toPlainText().strip()
                if page.detail_widget is not None
                else ""
                for page in self._page_specs
            }
        )
        if self.page_controller.pages != DEFAULT_PAGES:
            input_state["pages"] = pages_payload(self.page_controller.pages)
        encoded = json.dumps(
            input_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _capture_state(self) -> dict:
        def _cb(cb: QtWidgets.QCheckBox) -> bool:
            try:
                return bool(cb.isChecked())
            except Exception:
                return False

        try:
            type_txt = self.cmb_type.currentText().strip()
            if type_txt == NONE_CHOICE_LABEL:
                type_txt = ""
            color_txt = self.cmb_color.currentText().strip()
            if color_txt == NONE_CHOICE_LABEL:
                color_txt = ""
        except Exception:
            color_txt = ""

        page_details = {
            page.key: page.detail_widget.toPlainText()
            if page.detail_widget is not None
            else ""
            for page in self._all_page_specs.values()
        }

        return {
            "version": PROMPT_WRITER_STATE_VERSION,
            "type": type_txt,
            "subject": self.cmb_subject.currentText().strip(),
            "color": color_txt,
            "global": self.txt_global.toPlainText(),
            **page_details,
            "checks": {
                state_key: _cb(checkbox)
                for checkbox, state_key in self._checkbox_state_specs()
            },
            "resolved_instructions": dict(self._resolved_instructions),
            "generated_prompts": {
                image_name: prompt
                for image_name, prompt in self._generated_prompts.items()
                if isinstance(prompt, str)
            },
            "generated_output_stale": not self._generated_output_valid,
            "generated_input_signature": (
                self._generated_input_signature
                if self._generated_output_valid
                else ""
            ),
            "approved_images": sorted(self._approved_pages),
        }

    def reload_project_state(self) -> bool:
        if self.session.is_restoring:
            return False
        self._state_persistence_suspended = True
        try:
            project = self.session.project
            pages = project.pages
            selected = self.session.selected_page_id
            ids = [page["id"] for page in pages]
            if selected not in ids:
                selected = ids[0] if ids else None
            new_session = self._view_session_id != self.session.session_id
            new_binding = selected != self._view_page_id or new_session
            if new_binding:
                for dialog in tuple(self._list_manager_dialogs.values()):
                    dialog.close()
            if self._view_session_id != self.session.session_id:
                self._persist_timer.stop()
                self._browser_targets.clear()
                self._last_batch = None
                if self._active_batch is not None:
                    self._active_batch.cancel()
            self._view_page_id = selected
            self._view_session_id = self.session.session_id
            controls = [self.target_selector, self.cmb_subject, self.cmb_type, self.cmb_color,
                        self.txt_global, self._view_spec.detail_widget, self._view_spec.preview_widget,
                        self.page_palette, self.use_project_palette, self.page_subject,
                        self.page_style, self.use_project_style, self.output_width, self.output_height,
                        *(checkbox for checkbox, _ in self._checkbox_state_specs())]
            blockers = [QtCore.QSignalBlocker(widget) for widget in controls]
            self.target_selector.clear()
            for page in pages:
                self.target_selector.addItem(page_label(project, page["id"]), page["id"])
            self.target_selector.setCurrentIndex(ids.index(selected) if selected else -1)
            shared = project.to_dict()["prompt_settings"]
            for combo, key, empty in ((self.cmb_subject, "subject", -1), (self.cmb_type, "type", 0), (self.cmb_color, "color", 0)):
                self._restore_combo_value(combo, shared.get(key, ""), empty_index=empty, normalized_match=key == "color")
            self._restore_text(self.txt_global, shared.get("global", ""), new_session)
            checks = _normalize_exclusive_check_states(shared.get("checks", {}))
            for checkbox, key in self._checkbox_state_specs():
                checkbox.setChecked(checks.get(key, False))
            output = project.to_dict()["presentation"].get("artwork_output", {})
            self.output_width.setValue(output.get("width", 2048))
            self.output_height.setValue(output.get("height", 3072))
            self._generated_prompts = {page["id"]: page["prompt"]["text"] for page in pages if page["prompt"]["text"] is not None}
            self._resolved_instructions = shared.get("resolved_components", {})
            page = project.get_page(selected) if selected else None
            prompt = page["prompt"] if page else {"directions": "", "text": None, "overrides": {}, "stale": False}
            self._view_spec.key = selected or "current"
            self._view_spec.color_key = PAGE_TYPES[page["type"]].artwork_template if page else "cover"
            self._view_spec.display_label = page_label(project, selected) if page else "No page selected"
            self._view_spec.detail_label.setText("Page direction")
            self._view_spec.detail_widget.setPlaceholderText("Describe the subjects, scene and composition for this page.")
            _set_help(self._view_spec.detail_widget, "Directions for this page only. Message text is authored separately in Messages.")
            self._view_spec.preview_title.setText("Generated Prompt")
            self._restore_text(self._view_spec.detail_widget, prompt["directions"], new_binding)
            self._restore_text(self._view_spec.preview_widget, prompt["text"] or "", new_binding)
            overrides = prompt["overrides"]
            self.use_project_palette.setChecked("color" not in overrides)
            self.page_palette.setCurrentText(overrides.get("color", shared.get("color", "")))
            self.page_palette.setEnabled("color" in overrides)
            self.use_project_style.setChecked("type" not in overrides)
            self.page_style.setText(overrides.get("type", shared.get("type", "")))
            self.page_style.setEnabled("type" in overrides)
            self.page_subject.setText(overrides.get("subject", ""))
            self.page_subject.setPlaceholderText(shared.get("subject") or "Use project subject")
            self.this_page_card.setEnabled(page is not None)
            self._view_spec.preview_widget.setEnabled(page is not None)
            self._generated_output_valid = bool(prompt["text"] is not None and not prompt["stale"])
            del blockers
            self._refresh_status()
            self.refresh_semantic_palette()
            return True
        finally:
            self._state_persistence_suspended = False

    @staticmethod
    def _restore_text(widget, text, new_binding=False):
        # Qt normalizes displayed line endings. Identical refreshes must retain
        # the cursor, selection and undo history, including historical CRLF.
        displayed = text.replace("\r\n", "\n").replace("\r", "\n")
        if new_binding or widget.toPlainText() != displayed:
            widget.setPlainText(text)


    def _restore_persisted_state(self) -> bool:
        return self.reload_project_state()


    # -----------------------
    # UI
    # -----------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        container = QtWidgets.QFrame(self)
        container.setObjectName("container")
        cl = QtWidgets.QVBoxLayout(container)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(10)
        root.addWidget(container)

        self.title_bar = StandardTitleBar(
            self,
            "Prompt Writer",
            show_minimize=False,
            on_close=self._on_close,
            close_icon_asset="titlebar/minimize.png",
            close_fallback_symbol=MINIMIZE_SYMBOL,
            close_danger=False,
        )
        cl.addWidget(self.title_bar)

        target_row = QtWidgets.QHBoxLayout()
        self.target_selector = QtWidgets.QComboBox()
        self.target_selector.setAccessibleName("Target page")
        self.target_selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target_selector.setMinimumContentsLength(15)
        self.btn_page_settings = QtWidgets.QPushButton("Page settings")
        target_row.addWidget(QtWidgets.QLabel("Target page"))
        target_row.addWidget(self.target_selector, 1)
        target_row.addWidget(self.btn_page_settings)
        cl.addLayout(target_row)

        action_bar = QtWidgets.QFrame()
        action_bar.setObjectName("actionBar")
        header = QtWidgets.QHBoxLayout(action_bar)
        header.setContentsMargins(10, 8, 10, 8)
        header.setSpacing(8)

        self.btn_generate = QtWidgets.QPushButton("Build Prompt")
        self.btn_generate.setObjectName("primaryButton")
        self.btn_copy = QtWidgets.QPushButton("Copy Prompt")
        self.btn_copy.setObjectName("secondaryButton")
        self.btn_copy.setEnabled(False)
        self.btn_erase = QtWidgets.QPushButton("Erase All")
        self.btn_erase.setObjectName("dangerButton")
        for b in (self.btn_generate, self.btn_copy, self.btn_erase):
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
        set_control_help(
            self.btn_generate,
            "Build artwork instructions for the selected page. Existing text is retained for recovery.",
        )
        set_control_help(
            self.btn_copy,
            "Copy the selected page's exact prompt text.",
        )
        set_control_help(
            self.btn_erase,
            "Clear every Prompt Writer selection, detail, and generated prompt.",
        )
        header.addWidget(self.btn_generate)
        header.addWidget(self.btn_copy)
        header.addWidget(self.btn_erase)
        header.addStretch(1)

        self.lbl_visionary_prefix = QtWidgets.QLabel("For best results, use")
        self.lbl_visionary_prefix.setObjectName("visionaryPrefix")
        _set_help(
            self.lbl_visionary_prefix,
            "Open The Visionary if you want extra prompt-writing guidance before you generate the image set.",
        )
        header.addWidget(self.lbl_visionary_prefix)

        self.btn_visionary = QtWidgets.QPushButton("The Visionary")
        self.btn_visionary.setObjectName("visionary_btn")
        self.btn_visionary.setCursor(Qt.PointingHandCursor)
        self.btn_visionary.setFlat(True)
        self.btn_visionary.setFixedHeight(32)
        self.btn_visionary.setStyleSheet(
            "QPushButton#visionary_btn{"
            "background:#25282b;border:1px solid #00b2b2;border-radius:10px;padding:5px 12px;"
            "font-weight:700;font-size:10px;color:#dffbff;"
            "}"
            "QPushButton#visionary_btn:hover{background:#293537;color:#ffffff;}"
        )
        self._visionary_effect = QGraphicsDropShadowEffect(self.btn_visionary)
        self._visionary_effect.setBlurRadius(32)
        self._visionary_effect.setOffset(0, 0)
        self._visionary_effect.setColor(QColor(UI_ACCENT))
        self.btn_visionary.setGraphicsEffect(self._visionary_effect)
        self.btn_visionary.clicked.connect(self._open_visionary)
        _set_help(
            self.btn_visionary,
            "Open The Visionary for deeper prompt guidance and refinement ideas.",
        )

        header.addWidget(self.btn_visionary)
        cl.addWidget(action_bar)

        main_h = QtWidgets.QHBoxLayout()
        main_h.setSpacing(16)
        cl.addLayout(main_h, 1)

        self._left_scroll = QtWidgets.QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._left_scroll.setObjectName("left_scroll")
        left_panel = QtWidgets.QWidget()
        left_panel.setObjectName("leftPanel")
        left_v = QtWidgets.QVBoxLayout(left_panel)
        left_v.setContentsMargins(0, 0, 8, 0)
        left_v.setSpacing(12)
        self._left_scroll.setWidget(left_panel)
        main_h.addWidget(self._left_scroll, 10)

        controls_title = QtWidgets.QLabel("Project Style")
        controls_title.setObjectName("columnTitle")
        left_v.addWidget(controls_title)
        self.style_summary = QtWidgets.QLabel()
        self.style_summary.setWordWrap(True)
        left_v.addWidget(self.style_summary)
        self.btn_style_editor = QtWidgets.QPushButton("Edit project style")
        self.btn_style_editor.setCheckable(True)
        left_v.addWidget(self.btn_style_editor)
        self.style_editor = QtWidgets.QWidget()
        left_v.addWidget(self.style_editor)
        left_v.addStretch(1)
        left_column = left_v
        left_v = QtWidgets.QVBoxLayout(self.style_editor)
        left_v.setContentsMargins(0, 0, 0, 0)
        self.style_editor.hide()
        self.btn_style_editor.toggled.connect(self.style_editor.setVisible)
        controls_hint = QtWidgets.QLabel(
            "Choose the shared direction, then add optional guidance and page-specific details."
        )
        controls_hint.setObjectName("columnHint")
        controls_hint.setWordWrap(True)
        left_v.addWidget(controls_hint)

        selection_card = QtWidgets.QFrame()
        selection_card.setObjectName("sectionCard")
        selection_layout = QtWidgets.QVBoxLayout(selection_card)
        selection_layout.setContentsMargins(14, 12, 14, 14)
        selection_layout.setSpacing(10)

        selection_title = QtWidgets.QLabel("Image Set")
        selection_title.setObjectName("sectionTitle")
        selection_layout.addWidget(selection_title)
        selection_hint = QtWidgets.QLabel(
            "Double-click a label or menu to manage its saved options."
        )
        selection_hint.setObjectName("sectionHint")
        selection_hint.setWordWrap(True)
        selection_layout.addWidget(selection_hint)

        sel_grid = QtWidgets.QGridLayout()
        sel_grid.setHorizontalSpacing(12)
        sel_grid.setVerticalSpacing(6)
        sel_grid.setColumnStretch(0, 0)
        sel_grid.setColumnStretch(1, 1)

        self.lbl_type = QtWidgets.QLabel("Graphics and Illustration")
        self.lbl_type.setProperty("inputLabel", True)
        _set_help(
            self.lbl_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.setEditable(False)
        self.cmb_type.setFixedSize(238, 34)
        self.cmb_type.view().setMinimumWidth(320)
        self.cmb_type.setItemDelegate(HeaderAwareItemDelegate(self.cmb_type))
        _set_help(
            self.cmb_type,
            "Choose the overall visual or illustration style for the generated images. This changes how the full image set looks, not what the subject is.",
        )
        self._populate_managed_combo(self.cmb_type, self._read_managed_entries("type"), allow_none=True)
        self._install_manage_trigger(self.lbl_type, "type")
        self._install_manage_trigger(self.cmb_type, "type")
        sel_grid.addWidget(self.lbl_type, 0, 0)
        sel_grid.addWidget(self.cmb_type, 1, 0, Qt.AlignLeft)

        self.lbl_subject = QtWidgets.QLabel("Subject")
        self.lbl_subject.setProperty("inputLabel", True)
        _set_help(
            self.lbl_subject,
            "Choose the main thing the images should be about. Double-click Subject to add, update, or remove subject entries.",
        )
        self.cmb_subject = QtWidgets.QComboBox()
        self.cmb_subject.setEditable(True)
        self.cmb_subject.setFixedSize(238, 34)
        self.cmb_subject.view().setMinimumWidth(320)
        self.cmb_subject.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_subject.setItemDelegate(HeaderAwareItemDelegate(self.cmb_subject))
        _set_help(
            self.cmb_subject,
            "Choose or type the project subject. Double-click the label to manage reusable subjects.",
        )
        self._populate_managed_combo(self.cmb_subject, self._read_managed_entries("subject"), allow_none=False)
        self._install_manage_trigger(self.lbl_subject, "subject")
        self._install_manage_trigger(self.cmb_subject, "subject")
        sel_grid.addWidget(self.lbl_subject, 2, 0)
        sel_grid.addWidget(self.cmb_subject, 3, 0, Qt.AlignLeft)

        self.lbl_color = QtWidgets.QLabel("Color Scheme")
        self.lbl_color.setProperty("inputLabel", True)
        _set_help(
            self.lbl_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self.cmb_color = QtWidgets.QComboBox()
        self.cmb_color.setEditable(False)
        self.cmb_color.setFixedSize(238, 34)
        self.cmb_color.view().setMinimumWidth(320)
        self.cmb_color.setItemDelegate(HeaderAwareItemDelegate(self.cmb_color))
        _set_help(
            self.cmb_color,
            "Choose the main palette or color direction for the images. Leave it empty if you do not want to force a shared color mood.",
        )
        self._reload_managed_list("color")
        self._install_manage_trigger(self.lbl_color, "color")
        self._install_manage_trigger(self.cmb_color, "color")
        sel_grid.addWidget(self.lbl_color, 4, 0)
        sel_grid.addWidget(self.cmb_color, 5, 0, Qt.AlignLeft)

        selection_layout.addLayout(sel_grid)
        left_v.addWidget(selection_card)

        self.gb_helpful = QtWidgets.QGroupBox("")
        self.gb_helpful.setObjectName("helpfulCard")
        _set_help(
            self.gb_helpful,
            "Use these built-in options to refine composition, framing, image policy, and style. They add supporting instructions to the generated prompts without changing the main subject field.",
        )

        gb_layout = QtWidgets.QGridLayout()
        gb_layout.setContentsMargins(14, 12, 14, 14)
        gb_layout.setHorizontalSpacing(12)
        gb_layout.setVerticalSpacing(8)
        gb_layout.setColumnStretch(0, 1)
        gb_layout.setColumnStretch(1, 1)
        self.gb_helpful.setLayout(gb_layout)

        self.lbl_helpful = QtWidgets.QLabel("Helpful Options / Guidance")
        self.lbl_helpful.setObjectName("sectionTitle")
        _set_help(
            self.lbl_helpful,
            "Use these built-in options to refine composition, framing, image policy, and style. Group headers are visual only and are not copied into the final prompt. Some options are intentionally mutually exclusive so only combinations that make sense can stay active.",
        )
        gb_layout.addWidget(self.lbl_helpful, 0, 0, 1, 2)

        def helpful_group(title: str, object_name: str) -> Tuple[QtWidgets.QGroupBox, QtWidgets.QGridLayout]:
            group = QtWidgets.QGroupBox(title)
            group.setObjectName(object_name)
            group.setStyleSheet(
                f"QGroupBox#{object_name} {{"
                f"color: {COL_HEADER_TEXT};"
                "font-weight:700;font-size:10px;"
                "background: #1b1d20;"
                "border: 1px solid #303438;"
                "border-radius: 11px;"
                "margin-top: 8px;"
                "padding-top: 7px;"
                "}"
                f"QGroupBox#{object_name}::title {{"
                "subcontrol-origin: margin;"
                "left: 8px;"
                "padding: 0 4px;"
                "}"
            )
            layout = QtWidgets.QGridLayout(group)
            layout.setContentsMargins(8, 10, 8, 7)
            layout.setHorizontalSpacing(8)
            layout.setVerticalSpacing(4)
            return group, layout

        decorative_group, decorative_layout = helpful_group(
            "Decorative Frames",
            "helpful_decorative_frames",
        )
        style_group, style_layout = helpful_group("Style Bias", "helpful_style_bias")
        safety_group, safety_layout = helpful_group(
            "Image Safety / Clean Output",
            "helpful_image_safety",
        )
        camera_group, camera_layout = helpful_group(
            "Camera / Framing",
            "helpful_camera_framing",
        )

        self.cb_black = GoldenCheckBox("Thin Black Border")
        self.cb_white = GoldenCheckBox("Thin White Border")
        self.cb_frame = GoldenCheckBox("Decorative Frame")
        self.cb_vignette = GoldenCheckBox("Subtle Edge Vignette")
        self.cb_polaroid = GoldenCheckBox("Polaroid-Style Margin")
        self.cb_cardshadow = GoldenCheckBox("Card With Soft Drop Shadow")
        decorative_layout.addWidget(self.cb_black, 0, 0)
        decorative_layout.addWidget(self.cb_white, 1, 0)
        decorative_layout.addWidget(self.cb_frame, 2, 0)
        decorative_layout.addWidget(self.cb_vignette, 3, 0)
        decorative_layout.addWidget(self.cb_polaroid, 4, 0)
        decorative_layout.addWidget(self.cb_cardshadow, 5, 0)

        self.cb_real = GoldenCheckBox("Photorealistic")
        self.cb_paint = GoldenCheckBox("Painterly")
        self.cb_minimal = GoldenCheckBox("Minimalistic")
        self.cb_simplified_details = GoldenCheckBox("Simplified Details")
        style_layout.addWidget(self.cb_real, 0, 0)
        style_layout.addWidget(self.cb_paint, 1, 0)
        style_layout.addWidget(self.cb_minimal, 2, 0)

        self.cb_forbid = GoldenCheckBox("No Text in the Image")
        self.cb_clean_composition = GoldenCheckBox("Clean Composition")
        safety_layout.addWidget(self.cb_forbid, 0, 0)
        safety_layout.addWidget(self.cb_clean_composition, 1, 0)
        safety_layout.addWidget(self.cb_simplified_details, 2, 0)

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
            "Show more environment inside the selected output dimensions.",
        )
        camera_layout.addWidget(self.cb_strong_focal_point, 0, 0)
        camera_layout.addWidget(self.cb_dynamic_angle, 1, 0)
        camera_layout.addWidget(self.cb_cinematic_framing, 2, 0)
        camera_layout.addWidget(self.cb_close_up_focus, 3, 0)
        camera_layout.addWidget(self.cb_full_body_view, 4, 0)
        camera_layout.addWidget(self.cb_wide_scene, 5, 0)

        gb_layout.addWidget(decorative_group, 1, 0, 1, 2)
        gb_layout.addWidget(style_group, 2, 0, 1, 2)
        gb_layout.addWidget(safety_group, 3, 0, 1, 2)
        gb_layout.addWidget(camera_group, 4, 0, 1, 2)

        left_v.addWidget(self.gb_helpful)

        global_card = QtWidgets.QFrame()
        global_card.setObjectName("sectionCard")
        global_layout = QtWidgets.QVBoxLayout(global_card)
        global_layout.setContentsMargins(14, 12, 14, 14)
        global_layout.setSpacing(8)

        self.lbl_global = QtWidgets.QLabel("Apply to All Images")
        self.lbl_global.setObjectName("sectionTitle")
        _set_help(
            self.lbl_global,
            "Anything written here is added to every generated image prompt, so use it for shared ideas, mood, setting, or details that should apply across the full set.",
        )
        global_layout.addWidget(self.lbl_global)
        self.txt_global = QtWidgets.QPlainTextEdit()
        self.txt_global.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self.txt_global.setWordWrapMode(
            QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        self.txt_global.document().setDocumentMargin(6)
        self.txt_global.setPlaceholderText("Add ideas that should be applied across every image in the set.")
        self.txt_global.setMinimumHeight(92)
        self.txt_global.setMaximumHeight(132)
        _set_help(
            self.txt_global,
            "Anything written here is added to every generated image prompt, so use it for shared ideas, mood, setting, or details that should apply across the full set.",
        )
        global_layout.addWidget(self.txt_global)
        left_v.addWidget(global_card)

        output_form = QtWidgets.QFormLayout()
        self.output_width = QtWidgets.QSpinBox()
        self.output_height = QtWidgets.QSpinBox()
        for spin in (self.output_width, self.output_height):
            spin.setRange(1, 2147483647)
        output_form.addRow("Artwork width (pixels)", self.output_width)
        output_form.addRow("Artwork height (pixels)", self.output_height)
        left_v.addLayout(output_form)
        self.btn_reroll = QtWidgets.QPushButton("Reroll shared wording")
        left_v.addWidget(self.btn_reroll)
        self.btn_proofread_shared = QtWidgets.QPushButton("Proofread project direction")
        self.btn_proofread_shared.clicked.connect(lambda: self._proofread_direction(shared=True))
        left_v.addWidget(self.btn_proofread_shared)

        details_card = QtWidgets.QFrame()
        details_card.setObjectName("sectionCard")
        details_layout = QtWidgets.QVBoxLayout(details_card)
        details_layout.setContentsMargins(14, 12, 14, 14)
        details_layout.setSpacing(8)

        per_lbl = QtWidgets.QLabel("This Page")
        per_lbl.setObjectName("sectionTitle")
        _set_help(
            per_lbl,
            "Use these fields for details that should affect only one specific image, not the whole image set.",
        )
        details_layout.addWidget(per_lbl)
        self.this_page_card = details_card
        self.layout_summary = QtWidgets.QLabel()
        self.layout_summary.setWordWrap(True)
        details_layout.addWidget(self.layout_summary)
        override_form = QtWidgets.QFormLayout()
        self.page_subject = QtWidgets.QLineEdit()
        override_form.addRow("Subject override", self.page_subject)
        self.use_project_style = QtWidgets.QCheckBox("Use project visual style")
        self.page_style = QtWidgets.QLineEdit()
        override_form.addRow(self.use_project_style)
        override_form.addRow("Page visual style", self.page_style)
        self.use_project_palette = QtWidgets.QCheckBox("Use project palette")
        self.page_palette = QtWidgets.QComboBox()
        self.page_palette.setEditable(True)
        override_form.addRow(self.use_project_palette)
        palette_row = QtWidgets.QHBoxLayout()
        palette_row.addWidget(self.page_palette, 1)
        self.btn_page_palette = QtWidgets.QPushButton("Add palette")
        palette_row.addWidget(self.btn_page_palette)
        self.btn_page_palette.clicked.connect(lambda: self._open_list_manager("color", for_page=True))
        override_form.addRow("Page palette", palette_row)
        details_layout.addLayout(override_form)

        for page in self._page_specs:
            editor = QtWidgets.QPlainTextEdit()
            editor.setLineWrapMode(
                QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
            )
            editor.setWordWrapMode(
                QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
            )
            editor.document().setDocumentMargin(6)
            editor.setMinimumHeight(76)
            editor.setMaximumHeight(96)
            editor.setPlaceholderText(
                f"Add details that should apply only to {page.output_filename}."
            )
            help_text = (
                f"Anything written here is added only to the {page.output_filename} prompt. "
                f"{page.detail_help}"
            )
            label = QtWidgets.QLabel(page.display_label.removesuffix(" Prompt"))
            label.setProperty("pageDetailLabel", True)
            _set_help(label, help_text)
            _set_help(editor, help_text)
            details_layout.addWidget(label)
            details_layout.addWidget(editor)
            page.detail_widget = editor
            page.detail_label = label
            setattr(self, f"txt_{page.key}", editor)
        self.btn_proofread_page = QtWidgets.QPushButton("Proofread page direction")
        self.btn_proofread_page.clicked.connect(self._proofread_direction)
        details_layout.addWidget(self.btn_proofread_page)
        left_v.addStretch(1)

        output_panel = QtWidgets.QWidget()
        output_panel.setObjectName("outputPanel")
        right_v = QtWidgets.QVBoxLayout(output_panel)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(8)
        main_h.addWidget(output_panel, 11)

        left_column.insertWidget(left_column.count() - 1, details_card)
        output_title = QtWidgets.QLabel("Generated Prompt")
        output_title.setObjectName("columnTitle")
        right_v.addWidget(output_title)
        output_hint = QtWidgets.QLabel(
            "Build deliberately, edit freely, then copy the prompt to your image tool."
        )
        output_hint.setObjectName("columnHint")
        output_hint.setWordWrap(True)
        right_v.addWidget(output_hint)
        self.prompt_status = QtWidgets.QLabel()
        self.prompt_status.setWordWrap(True)
        right_v.addWidget(self.prompt_status)
        batch_row = QtWidgets.QGridLayout()
        self.btn_restore = QtWidgets.QPushButton("Restore Previous Prompt")
        self.btn_batch = QtWidgets.QPushButton("Build selected pages…")
        self.btn_all = QtWidgets.QPushButton("Build all pages…")
        self.btn_retry = QtWidgets.QPushButton("Retry failed pages…")
        for index, button in enumerate((self.btn_restore, self.btn_batch, self.btn_all, self.btn_retry)):
            batch_row.addWidget(button, index // 2, index % 2)
        right_v.addLayout(batch_row)
        self.batch_status = QtWidgets.QLabel()
        self.batch_status.setWordWrap(True)
        self._batch_results_scroll = QtWidgets.QScrollArea()
        self._batch_results_scroll.setWidgetResizable(True)
        self._batch_results_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._batch_results_scroll.setMaximumHeight(100)
        self._batch_results_scroll.setWidget(self.batch_status)
        self._batch_results_scroll.hide()
        right_v.addWidget(self._batch_results_scroll)

        output_nav = QtWidgets.QHBoxLayout()
        self.btn_prompt_list = QtWidgets.QPushButton("Prompts")
        self.btn_conversation = QtWidgets.QPushButton("Conversation")
        self.btn_provider = QtWidgets.QPushButton("Image service…")
        self.btn_provider.clicked.connect(self.provider_requested.emit)
        self.btn_conversation.setVisible(False)
        output_nav.addWidget(self.btn_prompt_list)
        output_nav.addWidget(self.btn_conversation)
        output_nav.addWidget(self.btn_provider)
        output_nav.addStretch()
        right_v.addLayout(output_nav)
        self._site_status = QtWidgets.QLabel("")
        self._site_status.setWordWrap(True)
        self._site_status.setVisible(False)
        right_v.addWidget(self._site_status)

        self._preview_scroll = QtWidgets.QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._preview_scroll.setObjectName("previewScroll")
        preview_container = QtWidgets.QWidget()
        preview_container.setObjectName("previewContainer")
        self._preview_layout = QtWidgets.QVBoxLayout(preview_container)
        self._preview_layout.setContentsMargins(0, 0, 8, 0)
        self._preview_layout.setSpacing(12)

        for page in self._page_specs:
            block = QtWidgets.QFrame()
            block.setObjectName("promptCard")
            block.setProperty("pageKey", page.key)
            block.setFrameShape(QtWidgets.QFrame.Box)
            block.setFrameShadow(QtWidgets.QFrame.Plain)
            bl = QtWidgets.QVBoxLayout(block)
            bl.setContentsMargins(12, 10, 12, 12)
            bl.setSpacing(8)

            header_row = QtWidgets.QHBoxLayout()
            header_label = QtWidgets.QLabel(page.display_label)
            header_label.setProperty("promptTitle", True)
            header_row.addWidget(header_label)
            header_row.addStretch(1)
            copy_btn = QtWidgets.QPushButton("Copy")
            copy_btn.setObjectName("promptCopyButton")
            copy_btn.setFixedSize(68, 28)
            copy_btn.setCursor(Qt.PointingHandCursor)
            set_control_help(
                copy_btn,
                f"Copy the generated prompt for {page.display_label} to the clipboard.",
            )
            copy_btn.setEnabled(False)
            header_row.addWidget(copy_btn)
            generate_btn = QtWidgets.QPushButton("Generate")
            generate_btn.setObjectName("promptGenerateButton")
            generate_btn.setCursor(Qt.PointingHandCursor)
            generate_btn.setVisible(False)
            set_control_help(
                generate_btn,
                f"Send the complete {page.display_label} to the selected image service.",
            )
            header_row.addWidget(generate_btn)
            bl.addLayout(header_row)

            editor = FocusablePlainTextEdit()
            editor.setObjectName("promptOutput")
            editor.setReadOnly(False)
            editor.setAcceptRichText(False)
            editor.setPlaceholderText(f"Prompt for {page.display_label}")
            editor.setMinimumHeight(140)
            editor.setMaximumHeight(16777215)
            editor.document().setDocumentMargin(8)
            bl.addWidget(editor)
            image_label = QtWidgets.QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setFixedSize(168, 155)
            image_label.setVisible(False)
            bl.addWidget(image_label, alignment=Qt.AlignHCenter)

            self._preview_layout.addWidget(block, 1)
            page.preview_widget = editor
            page.copy_button = copy_btn
            page.generate_button = generate_btn
            page.image_widget = image_label
            page.preview_title = header_label
            page.preview_card = block

            copy_btn.clicked.connect(lambda _: self._copy_prompt(self._view_page_id))
            generate_btn.clicked.connect(
                lambda _: self._generate_with_provider(self._view_page_id)
            )
            editor.focused.connect(lambda ed=editor: self._set_last_focused(ed))

        self._preview_scroll.setWidget(preview_container)
        self._output_stack = QtWidgets.QStackedWidget()
        self._output_stack.addWidget(self._preview_scroll)
        right_v.addWidget(self._output_stack, 1)
        self.btn_prompt_list.clicked.connect(
            lambda: self._output_stack.setCurrentWidget(self._preview_scroll)
        )
        self.btn_conversation.clicked.connect(self._show_conversation)

    def _apply_styles(self):
        stylesheet = """
            QWidget#PromptWriterPanel {
                background: rgba(0,0,0,0);
                color: #d9e6ec;
                font-family: '__APP_FONT_FAMILY__';
                font-size: 11px;
            }
            QFrame#container {
                background-color: #1a1b1d;
                border: 1px solid #303438;
                border-radius: 16px;
            }
            QFrame#actionBar {
                background: #1e2023;
                border: 1px solid #2b3034;
                border-radius: 12px;
            }
            QFrame#sectionCard,
            QGroupBox#helpfulCard {
                background: #202225;
                border: 1px solid #2c3034;
                border-radius: 14px;
            }
            QGroupBox#helpfulCard {
                padding: 0;
                margin: 0;
            }
            QGroupBox#helpfulCard::title {
                height: 0;
                padding: 0;
                margin: 0;
            }
            QLabel {
                color: #d9e6ec;
                background: transparent;
            }
            QLabel#columnTitle {
                color: #f2fbff;
                font: 650 14px '__APP_FONT_FAMILY__';
            }
            QLabel#columnHint,
            QLabel#sectionHint,
            QLabel#visionaryPrefix {
                color: #93a7b3;
                font: 10px '__APP_FONT_FAMILY__';
            }
            QLabel#sectionTitle {
                color: #eaf8fc;
                font: 700 12px '__APP_FONT_FAMILY__';
            }
            QLabel[inputLabel="true"] {
                color: #b9ccd5;
                font: 600 10px '__APP_FONT_FAMILY__';
            }
            QLabel[pageDetailLabel="true"],
            QLabel[promptTitle="true"] {
                font: 700 10px '__APP_FONT_FAMILY__';
            }
            QPushButton {
                padding: 6px 12px;
                background: #25282b;
                border: 1px solid #3a4045;
                color: #edf7fb;
                border-radius: 10px;
                font: 600 10px '__APP_FONT_FAMILY__';
            }
            QPushButton:hover {
                background: #293537;
                border-color: #00b2b2;
                color: #ffffff;
            }
            QPushButton:pressed {
                background: #191c1f;
            }
            QPushButton:disabled {
                color: #61727a;
                background: #1d1f21;
                border-color: #2c3033;
            }
            QPushButton#primaryButton {
                background: #13585d;
                border-color: #00b2b2;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover {
                background: #176c70;
                border-color: #74e1e1;
            }
            QPushButton#dangerButton {
                color: #ffb8bf;
                border-color: #65434a;
                background: #25191d;
            }
            QPushButton#dangerButton:hover {
                color: #ffffff;
                border-color: #d85c6a;
                background: #702f38;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
                background: #151719;
                color: #edf7fb;
                border: 1px solid #373d42;
                border-radius: 9px;
                padding: 4px 7px;
                selection-background-color: #215052;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus,
            QPlainTextEdit:focus, QTextEdit:focus {
                border-color: #00b2b2;
            }
            QComboBox {
                padding-right: 26px;
            }
            QComboBox::drop-down {
                width: 24px;
                border: none;
                border-left: 1px solid #343a3e;
            }
            QComboBox QAbstractItemView {
                background: #1c1f21;
                color: #edf7fb;
                border: 1px solid #3a4145;
                selection-background-color: #215052;
                outline: none;
            }
            QFrame#promptCard {
                background: #202225;
                border: 1px solid #34393d;
                border-radius: 14px;
            }
            QTextEdit#promptOutput {
                background: #151719;
                color: #dce9ef;
                border: 1px solid #343a3e;
                border-radius: 10px;
                padding: 0;
            }
            QTextEdit#promptOutput:focus {
                border-color: #00b2b2;
            }
            QPushButton#promptCopyButton, QPushButton#promptGenerateButton {
                padding: 4px 10px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea#left_scroll QWidget#leftPanel,
            QScrollArea#previewScroll QWidget#previewContainer {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px 0;
            }
            QScrollBar::handle:vertical {
                background: #315a68;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: #3b7181; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                height: 0;
            }
            QToolTip {
                background: #10141d;
                color: #ecf2ff;
                border: 1px solid #31505f;
                padding: 6px 8px;
            }
            """ + CHECKBOX_QSS
        self.setStyleSheet(
            stylesheet.replace(
                "'__APP_FONT_FAMILY__'",
                f"'{_qss_font_family(self._app_font_family)}'",
            )
        )

    def apply_theme_assets(self, service: object) -> None:
        """Refresh Prompt Writer chrome, colors, and application typography."""
        self._theme_tokens = getattr(service, "tokens", None)
        self._ui_header_color = QColor(
            str(getattr(self._theme_tokens, "primary", COL_HEADER_TEXT))
        )
        self._app_font_family = _service_app_font_family(service)
        font = self.font()
        font.setFamily(self._app_font_family)
        self.setFont(font)
        self._apply_styles()

        title_bar = getattr(self, "title_bar", None)
        if isinstance(title_bar, StandardTitleBar):
            title_bar.apply_theme_assets(service)

        for dialog in tuple(self._list_manager_dialogs.values()):
            try:
                dialog.apply_app_font_family(self._app_font_family)
                dialog.apply_theme_tokens(self._theme_tokens)
            except RuntimeError:
                continue

        for checkbox in self.findChildren(GoldenCheckBox):
            checkbox.apply_theme_tokens(self._theme_tokens)
        for combo in (self.cmb_type, self.cmb_subject, self.cmb_color):
            delegate = combo.itemDelegate()
            if isinstance(delegate, HeaderAwareItemDelegate):
                delegate.apply_theme_tokens(self._theme_tokens)
            model = combo.model()
            for row in range(combo.count()):
                item = model.item(row) if hasattr(model, "item") else None
                if item is not None and bool(item.data(MANAGED_LIST_HEADER_ROLE)):
                    item.setForeground(self._ui_header_color)
            combo.view().viewport().update()

        self._set_visionary_theme_colors()

        apply_semantic_styles = getattr(service, "apply_semantic_styles", None)
        if callable(apply_semantic_styles):
            apply_semantic_styles(self)
        self.refresh_semantic_palette()

    def _open_visionary(self) -> None:
        self._copy_all_prompts_text()
        url = str(
            SettingsStore(self.project_root).get(
                VISIONARY_URL_KEY,
                DEFAULT_VISIONARY_URL,
            )
        ).strip() or DEFAULT_VISIONARY_URL
        if not open_external_url(QUrl(url)):
            QtWidgets.QApplication.clipboard().setText(url)
            LOGGER.warning(
                "Could not open The Visionary at %s; the address was copied.",
                url,
            )


    def _exclusive_checkbox_groups(
        self,
    ) -> Tuple[Tuple[QtWidgets.QCheckBox, ...], ...]:
        return (
            (self.cb_black, self.cb_white),
            (self.cb_real, self.cb_paint, self.cb_minimal),
            (self.cb_close_up_focus, self.cb_full_body_view, self.cb_wide_scene),
        )

    def _enforce_exclusive_group(
        self,
        checked_box: QtWidgets.QCheckBox,
        group: Tuple[QtWidgets.QCheckBox, ...],
    ) -> None:
        if not checked_box.isChecked():
            return
        for other in group:
            if other is checked_box:
                continue
            if other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)

    def _wire_exclusive_group(
        self,
        group: Tuple[QtWidgets.QCheckBox, ...],
    ) -> None:
        for checkbox in group:
            checkbox.stateChanged.connect(
                lambda *_args, cb=checkbox, grp=group: self._enforce_exclusive_group(cb, grp)
            )

    def _normalize_exclusive_controls(self) -> None:
        for group in self._exclusive_checkbox_groups():
            selected = next((checkbox for checkbox in group if checkbox.isChecked()), None)
            if selected is None:
                continue
            for checkbox in group:
                should_check = checkbox is selected
                if checkbox.isChecked() != should_check:
                    checkbox.blockSignals(True)
                    checkbox.setChecked(should_check)
                    checkbox.blockSignals(False)

    def _connect_signals(self):
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_copy.clicked.connect(lambda: self._copy_prompt(self._view_page_id))
        self.btn_erase.clicked.connect(self._on_erase_all)
        self.target_selector.currentIndexChanged.connect(lambda: self.select_page(self.target_selector.currentData()))
        for combo in (self.cmb_type, self.cmb_subject, self.cmb_color):
            combo.currentTextChanged.connect(self._on_prompt_input_changed)
        self.txt_global.textChanged.connect(self._on_prompt_input_changed)
        for group in self._exclusive_checkbox_groups():
            self._wire_exclusive_group(group)
        for checkbox, _ in self._checkbox_state_specs():
            checkbox.stateChanged.connect(self._on_prompt_input_changed)
        self._view_spec.detail_widget.textChanged.connect(self._page_input_changed)
        self._view_spec.preview_widget.textChanged.connect(self._manual_prompt_changed)
        self.use_project_palette.toggled.connect(self._page_input_changed)
        self.page_palette.currentTextChanged.connect(self._page_input_changed)
        self.use_project_style.toggled.connect(self._page_input_changed)
        self.page_style.textChanged.connect(self._page_input_changed)
        self.page_subject.textChanged.connect(self._page_input_changed)
        self.output_width.valueChanged.connect(self._output_changed)
        self.output_height.valueChanged.connect(self._output_changed)
        self.btn_restore.clicked.connect(self._restore_previous)
        self.btn_batch.clicked.connect(lambda: self._choose_batch(False))
        self.btn_all.clicked.connect(lambda: self._choose_batch(True))
        self.btn_retry.clicked.connect(self._retry_batch)
        self.btn_reroll.clicked.connect(self._reroll_shared)
        self.btn_page_settings.clicked.connect(lambda: self.page_settings_requested.emit(self._view_page_id or ""))


    def attach_browser(self, browser: object, image_tab: object | None = None) -> None:
        """Display one live conversation for all generated prompts."""
        self._browser = browser
        self._image_tab = image_tab
        view = browser.view
        if self._output_stack.indexOf(view) < 0:
            self._output_stack.addWidget(view)
        self.btn_conversation.setText(browser.provider)
        self.btn_conversation.setVisible(True)
        self.lbl_visionary_prefix.setText("Create images with")
        self.btn_visionary.setText(browser.provider)
        self.btn_visionary.clicked.disconnect(self._open_visionary)
        self.btn_visionary.clicked.connect(self._show_conversation)
        set_control_help(self.btn_visionary, f"Open the {browser.provider} conversation in Prompt Writer.")
        if image_tab is not None:
            if hasattr(image_tab, "artwork_import_completed"):
                image_tab.artwork_import_completed.connect(self._on_artwork_import_completed)
            else:
                image_tab.image_import_completed.connect(self._on_image_import_completed)
            image_tab.images_changed.connect(self._refresh_approved_previews)
        if hasattr(browser, "imageReady"):
            browser.imageReady.connect(self._on_browser_image_ready)
            browser.imageFailed.connect(self._on_browser_image_failed)
            browser.imageApprovalStarted.connect(self._on_browser_image_started)
        self._refresh_approved_previews()
        self._sync_action_button_states()

    def _show_conversation(self) -> None:
        if self._browser is not None:
            self._output_stack.setCurrentWidget(self._browser.view)

    def _generate_with_provider(self, page_key: str) -> None:
        if self._browser is None or page_key is None:
            return
        prompt = self.session.project.get_prompt(page_key)["text"]
        if not prompt or self._site_request_in_progress:
            return
        self._browser_targets[page_key] = self.session.capture_target(page_key)
        if hasattr(self._browser, "set_approval_target"):
            self._browser.set_approval_target(page_key, page_label(self.session.project, page_key))
        self._site_request_in_progress = True
        self._site_status.setText(f"Sending {page_key} prompt to {self._browser.provider}...")
        self._site_status.setVisible(True)
        self._show_conversation()
        self._sync_action_button_states()
        self._browser.submit_prompt(prompt, self._on_provider_submission)

    def _on_provider_submission(self, ok: bool, message: str) -> None:
        self._site_request_in_progress = False
        if ok:
            message += " Right-click the finished image and choose Use image."
        self._site_status.setText(message)
        self._site_status.setVisible(True)
        self._sync_action_button_states()

    def _on_browser_image_started(self, page_key: str) -> None:
        self._site_status.setText("Saving the selected image to its original page...")
        self._site_status.setVisible(True)

    def _on_browser_image_failed(self, message: str) -> None:
        self._site_status.setText(message)
        self._site_status.setVisible(True)

    def _on_browser_image_ready(self, page_key: str, path: str) -> None:
        from project_consumers import session_for
        session = session_for(self.project_root)
        target = self._browser_targets.pop(page_key, None)
        if session is not None and (target is None or not session.target_is_current(target)):
            self._on_browser_image_failed("The original page changed or its project was replaced. Choose the image again for its intended page.")
            return
        if self._image_tab is None or self._pending_approval_key:
            self._on_browser_image_failed("Finish the current image before choosing another.")
            return
        import_page = getattr(self._image_tab, "import_image", None)
        index = self._page_image_index(page_key)
        if not callable(import_page) and not index:
            self._on_browser_image_failed("The selected image has no matching page.")
            return
        self._pending_approval_key = page_key
        try:
            if callable(import_page):
                started = import_page(page_key, path, target=target)
            else:
                started = self._image_tab.set_image_path(index, path)
        except Exception as error:
            LOGGER.exception("Prompt Writer image import failed: %s", error)
            started = False
        if not started:
            self._pending_approval_key = ""
            self._on_browser_image_failed("The image could not be saved to its page.")

    def _on_image_import_completed(self, index: int, path: str, error: str) -> None:
        page_key = self._pending_approval_key
        if not page_key or index != self._page_image_index(page_key):
            return
        self._on_artwork_import_completed(page_key, path, error)

    def _on_artwork_import_completed(self, page_key: str, path: str, error: str) -> None:
        if not page_key or page_key != self._pending_approval_key:
            return
        self._pending_approval_key = ""
        if not path:
            self._on_browser_image_failed(error or "The image could not be saved.")
            return
        self._approved_pages.add(page_key)
        self._refresh_approved_previews()
        self._output_stack.setCurrentWidget(self._preview_scroll)
        self._site_status.setText(f"{page_label(self.session.project, page_key)} image saved to Images.")
        self._site_status.setVisible(True)
        self.project_changed.emit()
        self._persist_state_now()

    def _page_image_index(self, page_id):
        try:
            key = self.session.project.get_page(page_id)["recovery"].get("legacy_key")
            return PAGE_INDICES.get(key, 0)
        except KeyError:
            return 0

    def _refresh_approved_previews(self, *_args):
        # Imported artwork never replaces the editable prompt document.
        self._view_spec.image_widget.setVisible(False)


    def _set_generated_output_valid(self, valid: bool) -> None:
        self._generated_output_valid = bool(valid)
        self._sync_action_button_states()


    def _has_erasable_content(self) -> bool:
        state = self._capture_state()
        return any(
            (
                str(state.get("type", "")).strip(),
                str(state.get("subject", "")).strip(),
                str(state.get("color", "")).strip(),
                str(state.get("global", "")).strip(),
                any(
                    str(state.get(page.key, "")).strip()
                    for page in self._page_specs
                ),
                any(bool(value) for value in dict(state.get("checks", {})).values()),
                bool(self._generated_prompts),
            )
        )

    def _sync_action_button_states(self) -> None:
        selected = getattr(self, "_view_page_id", None)
        busy = self._generation_in_progress
        text = self.session.project.get_prompt(selected)["text"] if selected else None
        choices = self.session.project.prompt_context(selected) if selected else None
        subject = ({**choices["shared"], **choices["prompt"]["overrides"]}.get("subject") if choices else "")
        for button, enabled in ((self.btn_generate, bool(selected and subject and str(subject).strip()) and not busy),
                                (self.btn_copy, text is not None and not busy),
                                (self.btn_erase, self._has_erasable_content() and not busy)):
            button.setEnabled(enabled)
            effect = button.graphicsEffect()
            if not isinstance(effect, QtWidgets.QGraphicsOpacityEffect):
                effect = QtWidgets.QGraphicsOpacityEffect(button)
                button.setGraphicsEffect(effect)
            effect.setOpacity(1.0 if enabled else 0.52)
        self.btn_batch.setEnabled(bool(self.session.project.pages) and not busy)
        self.btn_all.setEnabled(bool(self.session.project.pages) and not busy)
        self.btn_retry.setEnabled(not busy and self._last_batch is not None and bool(self._last_batch.items)
                                 and self._last_batch.items[0].target.session_id == self.session.session_id
                                 and any(item.status in {"failed", "conflict"} for item in self._last_batch.items))
        self._view_spec.copy_button.setEnabled(text is not None and not busy)
        self._view_spec.generate_button.setVisible(self._browser is not None)
        self._view_spec.generate_button.setEnabled(text is not None and not busy and not self._site_request_in_progress)


    def _invalidate_generated_output(self, *, discard: bool = False) -> None:
        if discard:
            self._generated_prompts = {}
            self._resolved_instructions = {}
        self._generated_input_signature = ""
        if discard:
            for page in self._all_page_specs.values():
                if page.preview_widget is not None:
                    page.preview_widget.clear()
        self._set_generated_output_valid(False)

    def _on_prompt_input_changed(self, *_args):
        if self._state_persistence_suspended or self.session.is_restoring:
            return
        state = self._capture_state()
        sender = self.sender()
        mapping = {self.cmb_subject: "subject", self.cmb_type: "type", self.cmb_color: "color", self.txt_global: "global"}
        keys = [mapping[sender]] if sender in mapping else ["checks"]
        self._writing = True
        try:
            self.session.project.update_project_data("prompt_settings", {key: state[key] for key in keys})
        finally:
            self._writing = False
        self._authored_change()


    def _build_copy_all_text(self) -> str:
        project = self.session.project
        return "\n\n".join(f"--- {page_label(project, page['id'])} ---\n\n{page['prompt']['text']}"
                            for page in project.pages if page["prompt"]["text"] is not None)


    def _copy_all_prompts_text(self) -> str:
        all_text = self._build_copy_all_text()
        if not all_text:
            return ""

        try:
            QtWidgets.QApplication.clipboard().setText(all_text)
        except (RuntimeError, OSError) as error:
            LOGGER.exception("Prompt Writer Copy All failed: %s", error)
            show_lettersmith_message(
                self,
                "Copy failed",
                "The generated prompts could not be copied to the clipboard.",
            )
            return ""
        return all_text

    def _load_colors_into_combo(self):
        self._reload_managed_list("color")

    def _collect_guidance(self) -> List[str]:
        guidance = [
            phrase
            for checkbox, phrase in self._guidance_checkbox_specs()
            if checkbox.isChecked()
        ]
        if not any(checkbox.isChecked() for checkbox in (self.cb_real, self.cb_paint, self.cb_minimal)):
            guidance.append(HIDDEN_STYLE_DEFAULT)
        if not any(checkbox.isChecked() for checkbox in (self.cb_close_up_focus, self.cb_full_body_view, self.cb_wide_scene)):
            guidance.append(HIDDEN_FRAMING_DEFAULT)
        return guidance

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

    @staticmethod
    def _color_preview_range(
        widget: QtWidgets.QTextEdit,
        start: int,
        length: int,
        color: str,
        *,
        bold: bool = False,
    ) -> None:
        if start < 0 or length <= 0:
            return
        # Qt positions use UTF-16 units; Python offsets use Unicode code points.
        plain = widget.toPlainText()
        cursor = QtGui.QTextCursor(widget.document())
        cursor.setPosition(len(plain[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(len(plain[:start + length].encode('utf-16-le')) // 2, QtGui.QTextCursor.KeepAnchor)
        text_format = QtGui.QTextCharFormat()
        text_format.setForeground(QColor(color))
        if bold:
            text_format.setFontWeight(QtGui.QFont.Bold)
        selection = QtWidgets.QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = text_format
        widget.setExtraSelections([*widget.extraSelections(), selection])

    def _highlight_saved_prompt(self, page: PageSpec) -> None:
        """Color the display without changing authored text, cursor or undo history."""
        widget = page.preview_widget
        if widget is None:
            return
        widget.setExtraSelections([])
        plain = widget.toPlainText()
        choices = {}
        if self._view_page_id:
            context = self.session.project.prompt_context(self._view_page_id)
            choices = context['prompt']['resolved_components'].get('choices') or {**context['shared'], **context['prompt']['overrides']}

        def color_exact(
            prefix: str,
            value: str,
            suffix: str,
            color: str,
            *,
            bold: bool = False,
        ) -> None:
            if not value:
                return
            needle = f"{prefix}{value}{suffix}"
            match = plain.find(needle)
            if match >= 0:
                self._color_preview_range(
                    widget,
                    match + len(prefix),
                    len(value),
                    color,
                    bold=bold,
                )

        subject = _without_terminal_punctuation(
            choices.get('subject', ''),
            max_length=300,
        )

        first_paragraph_end = plain.find("\n\n")
        first_paragraph = plain if first_paragraph_end < 0 else plain[:first_paragraph_end]
        subject_start = first_paragraph.rfind(subject) if subject else -1
        self._color_preview_range(
            widget,
            subject_start,
            len(subject),
            self._display_prompt_color("subject"),
            bold=True,
        )

        type_choice = choices.get('type', '').strip()
        if type_choice == NONE_CHOICE_LABEL:
            type_choice = ""
        type_choice = _normalize_prompt_fragment(type_choice, max_length=300)
        color_choice = _normalize_prompt_fragment(
            choices.get('color') or "",
            max_length=300,
        )
        global_extra = _normalize_prompt_fragment(choices.get('global', ''))
        image_extra = _normalize_prompt_fragment(
            page.detail_widget.toPlainText() if page.detail_widget is not None else ""
        )

        color_exact(
            "Use ",
            type_choice,
            " as the visual style.",
            self._display_prompt_color("type"),
            bold=True,
        )
        color_exact(
            "Use the ",
            color_choice,
            " palette.",
            self._display_prompt_color("scheme"),
            bold=True,
        )
        color_exact(
            "Shared visual direction: ",
            global_extra,
            "",
            self._display_prompt_color("global"),
        )
        color_exact("Page-specific direction: ", image_extra, "", page.preview_color)

        guidance_start = plain.find("Guidance:")
        if guidance_start >= 0:
            guidance_end = plain.find("\n\n", guidance_start)
            if guidance_end < 0:
                guidance_end = len(plain)
            self._color_preview_range(
                widget,
                guidance_start,
                guidance_end - guidance_start,
                self._display_prompt_color("helpful"),
            )
            self._color_preview_range(
                widget,
                guidance_start,
                len("Guidance:"),
                self._display_prompt_color("helpful"),
                bold=True,
            )

    def set_semantic_color(self, semantic_key: str, color: str) -> None:
        """Update one semantic color and immediately refresh the visible panel."""
        if semantic_key not in PROMPT_COLORS:
            raise KeyError(f"unknown Prompt Writer semantic color: {semantic_key}")
        parsed = QColor(color)
        if not parsed.isValid():
            raise ValueError(f"invalid Prompt Writer semantic color: {color}")
        PROMPT_COLORS[semantic_key] = parsed.name().upper()
        self.refresh_semantic_palette()

    def _display_prompt_color(self, semantic_key: str) -> str:
        configured = QColor(prompt_color(semantic_key))
        theme_text = QColor(
            str(getattr(self._theme_tokens, "text", configured.name()))
        )
        if not configured.isValid() or not theme_text.isValid():
            return prompt_color(semantic_key)
        configured_chroma = max(
            configured.red(),
            configured.green(),
            configured.blue(),
        ) - min(
            configured.red(),
            configured.green(),
            configured.blue(),
        )
        opposite_polarity = (
            configured.lightness() >= 160 > theme_text.lightness()
            or configured.lightness() <= 95 < theme_text.lightness()
        )
        if configured_chroma < 32 and opposite_polarity:
            return theme_text.name()
        return configured.name()

    def refresh_semantic_palette(self) -> None:
        """Apply the centralized semantic palette to every live consumer."""
        labels = (
            (getattr(self, "lbl_type", None), "type"),
            (getattr(self, "lbl_subject", None), "subject"),
            (getattr(self, "lbl_color", None), "scheme"),
            (getattr(self, "lbl_helpful", None), "helpful"),
            (getattr(self, "lbl_global", None), "global"),
        )
        for label, semantic_key in labels:
            if label is not None:
                label.setProperty("themeIndependent", True)
                label.setStyleSheet(
                    f"color:{self._display_prompt_color(semantic_key)};"
                )
                label.style().unpolish(label)
                label.style().polish(label)

        for page in getattr(self, "_page_specs", ()):
            page_color = page.preview_color
            if page.detail_label is not None:
                page.detail_label.setProperty("themeIndependent", True)
                page.detail_label.setStyleSheet(f"color:{page_color};")
            if page.preview_title is not None:
                page.preview_title.setProperty("themeIndependent", True)
                page.preview_title.setStyleSheet(f"color:{page_color};")
            if page.preview_card is not None:
                page.preview_card.setStyleSheet(
                    f"QFrame#promptCard {{ border-color:{page_color}; }}"
                )
            self._highlight_saved_prompt(page)

    def _set_generation_busy(self, busy: bool) -> None:
        self._generation_in_progress = bool(busy)
        self._sync_action_button_states()

    def _validate_generated_prompt_set(self, prompts: Dict[str, str]) -> None:
        expected = {page.key for page in self._page_specs}
        if set(prompts) != expected:
            raise ValueError("generation did not produce a prompt for every selected page")
        unresolved = re.compile(r"\{\{[^{}]+\}\}")
        for key, prompt in prompts.items():
            text = _normalize_text(prompt, strip=True, max_length=0)
            if not text or len(text) > MAX_GENERATED_PROMPT_LENGTH:
                raise ValueError(f"generated {key} prompt is empty or too long")
            if unresolved.search(text):
                raise ValueError(f"generated {key} prompt contains an unresolved placeholder")

    def _proofread_prompt_text(
        self,
        field_name: str,
        text: str,
        *,
        protected_terms: Tuple[str, ...] = (),
    ) -> str:
        try:
            corrected = self._language_service.correct_text(
                text,
                context="prompt",
                protected_terms=protected_terms,
            )
            if not isinstance(corrected, str):
                raise TypeError("language service returned non-text output")
            return corrected
        except Exception as error:
            LOGGER.exception(
                "Prompt Writer proofreading failed for %s: %s",
                field_name,
                error,
            )
            return text

    def _proofread_visible_prompt_fields(self, lifecycle: str) -> bool:
        # Loading, selecting and closing preserve authored input exactly.
        return False

    def _proofread_direction(self, _checked=False, *, shared=False):
        if not self._can_edit_page():
            return
        target = self.session.capture_target(self._view_page_id, fields=('prompt',))
        widget = self.txt_global if shared else self._view_spec.detail_widget
        original = widget.toPlainText()
        corrected = self._proofread_prompt_text("Project direction" if shared else "Page direction", original)
        if corrected == original:
            return
        def apply(model, page_id):
            if shared:
                model.update_project_data("prompt_settings", {"global": corrected})
            else:
                model.update_prompt(page_id, {"directions": corrected})
        if self.session.apply_result(target, apply):
            self._authored_change()


    @staticmethod
    def _replace_plain_text_as_one_edit(
        widget: QtWidgets.QPlainTextEdit,
        text: str,
    ) -> None:
        if widget.toPlainText() == text:
            return
        blocker = QtCore.QSignalBlocker(widget)
        cursor = QtGui.QTextCursor(widget.document())
        cursor.beginEditBlock()
        try:
            cursor.select(QtGui.QTextCursor.Document)
            cursor.insertText(text)
        finally:
            cursor.endEditBlock()
            blocker.unblock()

    def _on_generate(self):
        if self._view_page_id is not None:
            self._build_pages([self._view_page_id])


    def _roll_data_for_image(self) -> dict:
        return self._roll_shared_prompt_data()

    def _roll_shared_prompt_data(self) -> dict:
        data = dict(self._data)
        role_pick, _ = _pick_random_nonempty_line(self._module_path("role.txt"))
        if role_pick:
            data["role"] = role_pick
        order_pick, _ = _pick_random_order(self._module_path("order.txt"))
        if order_pick:
            data["order"] = order_pick
        effort_pick, _ = _pick_random_nonempty_line(self._module_path("effort.txt"))
        if effort_pick:
            data["effort"] = effort_pick
        format_pick, _ = _pick_random_nonempty_line(self._module_path("format.txt"))
        if format_pick:
            data["format"] = format_pick
        return data

    def _copy_all_prompts(self):
        return self._copy_all_prompts_text()

    def _copy_prompt(self, page_identifier):
        try:
            text = self.session.project.get_prompt(page_identifier)["text"]
            if text is not None:
                QtWidgets.QApplication.clipboard().setText(text)
        except (KeyError, RuntimeError, OSError) as error:
            show_lettersmith_message(self, "Copy failed", str(error))


    def reset_prompt_writer_state(self) -> bool:
        self._writing = True
        try:
            project = self.session.project
            project.update_project_data("prompt_settings", {"type": "", "subject": "", "color": "", "global": "", "checks": {}, "resolved_components": {}})
            for page in project.pages:
                project.replace_prompt(page["id"], {"text": None, "directions": "", "overrides": {}, "resolved_components": {}, "stale": False})
        finally:
            self._writing = False
        self.reload_project_state()
        self.project_changed.emit()
        return self._persist_state_now()


    def _on_erase_all(self):
        if any(
            (
                self.cmb_subject.currentText().strip(),
                self.txt_global.toPlainText().strip(),
                self._generated_prompts,
            )
        ):
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Erase Prompt Writer Content",
                question=(
                    "This clears the current Prompt Writer content but keeps "
                    "built-in and user-created options."
                ),
                primary_text="Yes",
                secondary_text="No",
                destructive_primary=True,
                click_outside_dismiss=False,
                width=540,
            )
            if confirmation.exec() != QtWidgets.QDialog.Accepted:
                return
        if not self.reset_prompt_writer_state():
            show_lettersmith_message(
                self,
                "Prompt Writer reset failed",
                "Prompt Writer could not be cleared or saved.",
            )

    def _on_close(self):
        if self._shutdown or self._hide_timer.isActive():
            return
        self._proofread_visible_prompt_fields("close")
        if not self._persist_state_now():
            show_lettersmith_message(
                self,
                "Prompt Writer",
                "The current Prompt Writer state could not be saved.",
            )
        self.popdown()
        self.dismissed.emit()

    close_with_anim = _on_close

    def _start_visionary_pulse(self):
        try:
            self._set_visionary_theme_colors()
            self._visionary_index = 0
            self._visionary_timer = QtCore.QTimer(self)
            self._visionary_timer.setInterval(520)
            self._visionary_timer.timeout.connect(self._tick_visionary_pulse)
            self._visionary_timer.start()
        except Exception:
            pass

    def _set_visionary_theme_colors(self) -> None:
        tokens = self._theme_tokens
        primary = QColor(str(getattr(tokens, "primary", "#6d7b82")))
        accent = QColor(str(getattr(tokens, "accent", "#aab4b9")))
        secondary = QColor(str(getattr(tokens, "secondary_accent", "#7f9099")))
        self._visionary_colors = [
            primary.darker(115),
            primary,
            accent,
            secondary,
        ]
        effect = getattr(self, "_visionary_effect", None)
        if isinstance(effect, QGraphicsDropShadowEffect):
            effect.setColor(primary)

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
            "background:#25282b;border:1px solid %s;border-radius:10px;padding:5px 12px;"
            "font-weight:700;font-size:10px;color:#dffbff;"
            "}"
            "QPushButton#visionary_btn:hover{background:#293537;color:#ffffff;}"
        ) % col.name()
        try:
            self.btn_visionary.setStyleSheet(style)
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        """A closing animation is already closed for toggle/show requests."""
        return not self._shutdown and self.isVisible() and not self._hide_timer.isActive()

    def popup(self, launcher: Optional[QtWidgets.QWidget] = None):
        was_open = self.is_open
        self._hide_timer.stop()
        self._animation_generation += 1
        if self._shutdown:
            return
        was_visible = self.isVisible()
        if not was_open and self._proofread_visible_prompt_fields("open"):
            if not self._persist_state_now():
                LOGGER.warning(
                    "Prompt Writer open-time proofreading could not be persisted"
                )
        visionary_timer = getattr(self, "_visionary_timer", None)
        if visionary_timer is not None and not visionary_timer.isActive():
            visionary_timer.start()
        self._geom_anim.stop()
        self._fade_anim.stop()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if self.isMinimized():
            self.showNormal()
        launcher = launcher if launcher is not None else self.parentWidget()
        screen_obj = screen_for_launcher(launcher)
        if screen_obj is None:
            return
        avail = screen_obj.availableGeometry()

        w = min(int(avail.width() * 0.816), 1104)
        h = min(int(avail.height() * 0.84), 860)
        target = QtCore.QRect(avail.x() + 24, avail.y() + 24, w, h)
        if launcher is not None:
            target.moveTopLeft(launcher.mapToGlobal(QtCore.QPoint(0, launcher.height() + 8)))
        if self._normal_geometry is not None and self._normal_geometry.isValid():
            target = QtCore.QRect(self._normal_geometry)
        if was_open and not self.isMaximized():
            target = self.geometry()
        self.setWindowOpacity(0.0 if not was_visible else min(1.0, self.windowOpacity()))
        place_window_on_launcher(self, launcher, geometry=target, near=True)
        self.show()
        self.raise_()
        target = place_window_on_launcher(self, launcher, geometry=target, near=True)
        if was_open or self.isMaximized():
            self.setWindowOpacity(1.0)
            return
        # Keep the slide animation on the same screen throughout its duration.
        off = bounded_window_geometry(target.translated(-32, 0), avail, self.minimumSize())
        if was_visible:
            off = target
        self.setGeometry(off)
        self._geom_anim.stop(); self._geom_anim.setDuration(260)
        self._geom_anim.setStartValue(off); self._geom_anim.setEndValue(target)
        self._geom_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._geom_anim.start()

        self._fade_anim.stop(); self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(self.windowOpacity()); self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    open_with_anim = popup

    def popdown(self):
        if not self.isVisible():
            return
        if self.isMaximized():
            normal_geometry = self.normalGeometry()
            if normal_geometry.isValid():
                self._normal_geometry = QtCore.QRect(normal_geometry)
            self.showNormal()
        visionary_timer = getattr(self, "_visionary_timer", None)
        if visionary_timer is not None:
            visionary_timer.stop()
        self._animation_generation += 1
        generation = self._animation_generation
        self._geom_anim.stop()
        self._fade_anim.stop()
        geom = self.geometry()
        self._normal_geometry = QtCore.QRect(geom)
        screen = self.screen()
        off = bounded_window_geometry(geom.translated(-32, 0), screen.availableGeometry()) if screen else geom
        self._geom_anim.stop(); self._geom_anim.setDuration(200)
        self._geom_anim.setEasingCurve(QEasingCurve.InCubic)
        self._geom_anim.setStartValue(geom); self._geom_anim.setEndValue(off)
        self._geom_anim.start()

        self._fade_anim.stop(); self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(self.windowOpacity()); self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        self._hide_timer.start(220)

    def _finish_close_animation(self) -> None:
        if self._shutdown:
            return
        self._hide_timer.stop()
        self._geom_anim.stop()
        self._fade_anim.stop()
        self.setWindowOpacity(0.0)
        self.hide()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def nativeEvent(self, event_type, message):
        title_bar = getattr(self, "title_bar", None)
        controller = getattr(title_bar, "window_controller", None)
        if controller is not None:
            handled, result = controller.native_event(event_type, message)
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self.session.remove_listener(self.session_event.emit)
        if self._active_batch is not None:
            self._active_batch.cancel()
        self._shutdown = True

        if self._browser is not None:
            view = self._browser.view
            if self._output_stack.indexOf(view) >= 0:
                self._output_stack.removeWidget(view)
                if hasattr(self._browser, "park_view"):
                    self._browser.park_view()
                else:
                    view.setParent(None)
                    view.hide()

        self._persist_timer.stop()
        try:
            self._persist_state_now()
        except Exception:
            pass
        visionary_timer = getattr(self, "_visionary_timer", None)
        if visionary_timer is not None:
            visionary_timer.stop()
        self._geom_anim.stop()
        self._fade_anim.stop()
        self._hide_timer.stop()

        for dialog in self.findChildren(QtWidgets.QDialog):
            try:
                dialog.close()
            except Exception:
                pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._shutdown:
            self._on_close()
            self._finish_close_animation()
        super().closeEvent(event)

    def hide(self):
        super().hide()


if __name__ == "__main__":
    configure_windows_app_identity()
    app = QtWidgets.QApplication(sys.argv)
    w = PromptWriterPanel()
    w.popup(); w.show()
    sys.exit(app.exec())
