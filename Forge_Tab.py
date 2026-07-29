# ===============================
# File: Forge_Tab.py
# Purpose: Modern Forge workflow for Letter Smith
# ===============================

from __future__ import annotations

import json
import re
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

import generate
from config import (
    MESSAGE_HTML_FILE,
    MUSIC_FILE,
    OUTPUT_PLAY_DIR,
    PLAY_METADATA_FILE,
    PUBLISHED_PAGE_URL_KEY,
    SETTINGS_FILE,
    USER_MESSAGE_DIR,
    USER_PAGES_DIR,
    USER_SOUNDS_DIR,
    ensure_output_dirs,
    validate_required_images,
)
from message_html import read_text_normalized
from settings_store import (
    CURTAIN_STYLE_LABELS,
    DEFAULT_SETTINGS,
    SettingsStore,
)
from project_sync import project_fingerprint
from saved_letters import SavedLetter, SavedLetterCatalog
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    import_runtime_track,
    load_library,
    save_project_state,
    sync_current_compatibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _read_text(path: Path) -> str:
    try:
        return read_text_normalized(path)
    except Exception:
        return ""


def _extract_html_title(
    index_html: Path,
) -> str:
    text = _read_text(index_html)

    match = re.search(
        r"<title>\s*(.*?)\s*</title>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return index_html.parent.name

    title = re.sub(
        r"\s+",
        " ",
        match.group(1),
    ).strip()

    return title or index_html.parent.name


def _safe_clear_dir_contents(
    directory: Path,
) -> Tuple[int, int]:
    files_deleted = 0
    directories_deleted = 0

    if (
        not directory.exists()
        or not directory.is_dir()
    ):
        return (
            files_deleted,
            directories_deleted,
        )

    for entry in directory.iterdir():
        try:
            if (
                entry.is_file()
                or entry.is_symlink()
            ):
                entry.unlink(
                    missing_ok=True
                )
                files_deleted += 1

            elif entry.is_dir():
                shutil.rmtree(
                    entry,
                    ignore_errors=True,
                )
                directories_deleted += 1

        except Exception:
            pass

    return (
        files_deleted,
        directories_deleted,
    )


def _load_settings(
    root: Path,
) -> dict:
    path = root / SETTINGS_FILE

    try:
        if not path.exists():
            return {}

        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return (
            value
            if isinstance(value, dict)
            else {}
        )

    except Exception:
        return {}


def _write_settings(
    root: Path,
    data: dict,
) -> None:
    path = root / SETTINGS_FILE

    try:
        path.write_text(
            json.dumps(
                data,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _normalize_page_url(
    value: str,
) -> str:
    candidate = (
        value or ""
    ).strip()

    if not candidate:
        return ""

    parsed = QUrl.fromUserInput(
        candidate
    )

    if not parsed.isValid():
        return ""

    if (
        parsed.scheme().lower()
        not in {
            "http",
            "https",
        }
    ):
        return ""

    if not parsed.host():
        return ""

    return parsed.toString()


def _metadata_path(
    play_directory: Path,
) -> Path:
    return (
        play_directory
        / PLAY_METADATA_FILE
    )


def _read_play_metadata(
    play_directory: Path,
) -> dict:
    path = _metadata_path(
        play_directory
    )

    try:
        if not path.exists():
            return {}

        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        value = {}

    return (
        value
        if isinstance(value, dict)
        else {}
    )


def _humanize_slug(
    value: str,
) -> str:
    display = (
        value.replace("_", " ")
        .replace("-", " ")
        .strip()
    )

    display = re.sub(
        r"\s+",
        " ",
        display,
    )

    return (
        display.title()
        if display
        else value
    )


def _get_generate_fn():
    function = getattr(
        generate,
        "generate_play_bundle",
        None,
    )

    return (
        function
        if callable(function)
        else None
    )


def _open_folder(
    path: Path,
) -> None:
    QtGui.QDesktopServices.openUrl(
        QUrl.fromLocalFile(
            str(path.resolve())
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Saved-letter card
# ─────────────────────────────────────────────────────────────────────────────


class SavedLetterCard(
    QtWidgets.QFrame
):
    activated = QtCore.Signal(object)

    def __init__(
        self,
        entry: SavedLetter,
        parent: Optional[
            QtWidgets.QWidget
        ] = None,
    ) -> None:
        super().__init__(parent)

        self.entry = entry

        self.setObjectName(
            "savedLetterCard"
        )

        self.setCursor(
            Qt.PointingHandCursor
        )

        self.setFocusPolicy(
            Qt.StrongFocus
        )

        self.setMinimumSize(
            184,
            250,
        )

        self.setMaximumWidth(
            220
        )

        self.setAccessibleName(
            f"Load {entry.title} "
            f"for {entry.recipient}"
        )

        self.setToolTip(
            str(entry.path)
        )

        self.setStyleSheet(
            "QFrame#savedLetterCard {"
            "background:#111820;"
            "border:1px solid #334756;"
            "border-radius:10px;"
            "}"
            "QFrame#savedLetterCard:hover,"
            "QFrame#savedLetterCard:focus {"
            "background:#16242d;"
            "border:2px solid #00d0ff;"
            "}"
            "QLabel {"
            "background:transparent;"
            "}"
            "QPushButton {"
            "min-height:30px;"
            "background:#17313b;"
            "color:#e8fbff;"
            "border:1px solid #00b8d4;"
            "border-radius:6px;"
            "font-weight:700;"
            "}"
            "QPushButton:hover {"
            "background:#00b8d4;"
            "color:#081014;"
            "}"
        )

        root = QtWidgets.QVBoxLayout(
            self
        )

        root.setContentsMargins(
            9,
            9,
            9,
            9,
        )

        root.setSpacing(5)

        self.cover = QLabel(self)

        self.cover.setAlignment(
            Qt.AlignCenter
        )

        self.cover.setFixedHeight(
            148
        )

        self.cover.setMinimumWidth(
            150
        )

        self.cover.setStyleSheet(
            "background:#0b1016;"
            "border:1px solid #273745;"
            "border-radius:7px;"
            "color:#72808e;"
            "font-weight:700;"
        )

        self._set_cover(
            entry.cover_path
        )

        root.addWidget(
            self.cover
        )

        title = QLabel(
            entry.title
            or "Untitled Letter",
            self,
        )

        title.setWordWrap(True)

        title.setAlignment(
            Qt.AlignCenter
        )

        title.setStyleSheet(
            "color:#f1fbff;"
            "font:700 11px 'Segoe UI';"
        )

        title.setMaximumHeight(
            38
        )

        root.addWidget(title)

        recipient = QLabel(
            entry.recipient
            or "Unknown recipient",
            self,
        )

        recipient.setAlignment(
            Qt.AlignCenter
        )

        recipient.setStyleSheet(
            "color:#a9c3cf;"
            "font:9px 'Segoe UI';"
        )

        recipient.setMaximumHeight(
            18
        )

        root.addWidget(
            recipient
        )

        metadata_row = QHBoxLayout()

        metadata_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        metadata_row.setSpacing(4)

        date_label = QLabel(
            entry.modified_at.strftime(
                "%b %d, %Y"
            ),
            self,
        )

        date_label.setStyleSheet(
            "color:#778b98;"
            "font:8px 'Segoe UI';"
        )

        status_label = QLabel(
            (
                "Recovery"
                if entry.recovery
                else (
                    "Published"
                    if entry.published
                    else "Local"
                )
            ),
            self,
        )

        status_label.setAlignment(
            Qt.AlignCenter
        )

        if entry.recovery:
            status_css = (
                "color:#ffd8a8;"
                "background:#49341c;"
                "border:1px solid #9b6a28;"
            )

        elif entry.published:
            status_css = (
                "color:#9dffbd;"
                "background:#173925;"
                "border:1px solid #2d8a4d;"
            )

        else:
            status_css = (
                "color:#b9dfff;"
                "background:#173044;"
                "border:1px solid #326786;"
            )

        status_label.setStyleSheet(
            status_css
            + "border-radius:5px;"
            + "padding:2px 6px;"
            + "font:700 8px 'Segoe UI';"
        )

        metadata_row.addWidget(
            date_label
        )

        metadata_row.addStretch(1)

        metadata_row.addWidget(
            status_label
        )

        root.addLayout(
            metadata_row
        )

        load_button = QPushButton(
            "Load",
            self,
        )

        load_button.clicked.connect(
            lambda: self.activated.emit(
                self.entry
            )
        )

        root.addWidget(
            load_button
        )

    def _set_cover(
        self,
        cover_path: Optional[Path],
    ) -> None:
        if (
            cover_path is None
            or not cover_path.is_file()
        ):
            self.cover.setText(
                "No cover\navailable"
            )
            return

        pixmap = QtGui.QPixmap(
            str(cover_path)
        )

        if pixmap.isNull():
            self.cover.setText(
                "Cover could not\nbe loaded"
            )
            return

        self.cover.setPixmap(
            pixmap.scaled(
                154,
                144,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def mouseReleaseEvent(
        self,
        event: QtGui.QMouseEvent,
    ) -> None:
        if (
            event.button()
            == Qt.LeftButton
            and self.rect().contains(
                event.position().toPoint()
            )
        ):
            self.activated.emit(
                self.entry
            )
            event.accept()
            return

        super().mouseReleaseEvent(
            event
        )

    def keyPressEvent(
        self,
        event: QtGui.QKeyEvent,
    ) -> None:
        if event.key() in (
            Qt.Key_Return,
            Qt.Key_Enter,
            Qt.Key_Space,
        ):
            self.activated.emit(
                self.entry
            )
            event.accept()
            return

        super().keyPressEvent(
            event
        )


# ─────────────────────────────────────────────────────────────────────────────
# Saved-letter gallery
# ─────────────────────────────────────────────────────────────────────────────


class SavedLettersDialog(
    QtWidgets.QDialog
):
    letter_selected = QtCore.Signal(
        object
    )

    CARD_WIDTH = 202
    GRID_GAP = 10

    def __init__(
        self,
        project_root: Path,
        parent: Optional[
            QtWidgets.QWidget
        ] = None,
    ) -> None:
        super().__init__(parent)

        self.project_root = Path(
            project_root
        ).resolve()

        self.catalog = SavedLetterCatalog(
            self.project_root
        )

        self._signature: tuple = ()
        self._cards: list[
            SavedLetterCard
        ] = []

        self._last_columns = 0

        self.setWindowTitle(
            "Load Letters"
        )

        self.setModal(False)

        self.setMinimumSize(
            560,
            480,
        )

        self.resize(
            760,
            560,
        )

        self.setAttribute(
            Qt.WA_DeleteOnClose,
            False,
        )

        self.setStyleSheet(
            "QDialog {"
            "background:#0d1117;"
            "color:#e8f8ff;"
            "}"
            "QLabel#savedLettersTitle {"
            "color:#00d0ff;"
            "font:700 17px 'Segoe UI';"
            "}"
            "QLabel#savedLettersCount {"
            "color:#8ea6b3;"
            "font:9px 'Segoe UI';"
            "}"
            "QScrollArea {"
            "background:transparent;"
            "border:none;"
            "}"
            "QWidget#savedLettersCanvas {"
            "background:transparent;"
            "}"
        )

        root = QVBoxLayout(self)

        root.setContentsMargins(
            14,
            12,
            14,
            14,
        )

        root.setSpacing(8)

        header = QHBoxLayout()

        title = QLabel(
            "Load Letters",
            self,
        )

        title.setObjectName(
            "savedLettersTitle"
        )

        self.count_label = QLabel(
            "",
            self,
        )

        self.count_label.setObjectName(
            "savedLettersCount"
        )

        header.addWidget(title)
        header.addStretch(1)

        header.addWidget(
            self.count_label
        )

        root.addLayout(header)

        self.scroll = (
            QtWidgets.QScrollArea(
                self
            )
        )

        self.scroll.setWidgetResizable(
            True
        )

        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        self.canvas = QtWidgets.QWidget(
            self.scroll
        )

        self.canvas.setObjectName(
            "savedLettersCanvas"
        )

        self.grid = (
            QtWidgets.QGridLayout(
                self.canvas
            )
        )

        self.grid.setContentsMargins(
            2,
            2,
            2,
            2,
        )

        self.grid.setHorizontalSpacing(
            self.GRID_GAP
        )

        self.grid.setVerticalSpacing(
            self.GRID_GAP
        )

        self.grid.setAlignment(
            Qt.AlignTop
            | Qt.AlignLeft
        )

        self.scroll.setWidget(
            self.canvas
        )

        root.addWidget(
            self.scroll,
            1,
        )

        self.empty_label = QLabel(
            "No saved letters were found.",
            self.canvas,
        )

        self.empty_label.setAlignment(
            Qt.AlignCenter
        )

        self.empty_label.setStyleSheet(
            "color:#778996;"
            "font:11px 'Segoe UI';"
            "padding:40px;"
        )

        self.empty_label.hide()

        self.refresh_timer = (
            QtCore.QTimer(self)
        )

        self.refresh_timer.setInterval(
            1200
        )

        self.refresh_timer.timeout.connect(
            self.refresh_from_disk
        )

    @staticmethod
    def _entry_signature(
        entry: SavedLetter,
    ) -> tuple:
        cover_stamp = 0

        if entry.cover_path is not None:
            try:
                cover_stamp = (
                    entry.cover_path
                    .stat()
                    .st_mtime_ns
                )
            except OSError:
                cover_stamp = 0

        return (
            str(entry.path),
            entry.recipient,
            entry.title,
            entry.modified_at.timestamp(),
            entry.published_url,
            str(entry.cover_path or ""),
            cover_stamp,
            entry.recovery,
        )

    def showEvent(
        self,
        event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(event)

        self.refresh_from_disk(
            force=True
        )

        self.refresh_timer.start()

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        self.refresh_timer.stop()

        super().hideEvent(event)

    def closeEvent(
        self,
        event: QtGui.QCloseEvent,
    ) -> None:
        self.refresh_timer.stop()

        super().closeEvent(event)

    def resizeEvent(
        self,
        event: QtGui.QResizeEvent,
    ) -> None:
        super().resizeEvent(event)

        QtCore.QTimer.singleShot(
            0,
            self._reflow_cards,
        )

    def refresh_from_disk(
        self,
        *,
        force: bool = False,
    ) -> None:
        try:
            entries = (
                self.catalog
                .list_entries()
            )

        except Exception as error:
            self.count_label.setText(
                "Catalog unavailable"
            )

            self._show_empty(
                "Saved letters could not "
                f"be read:\n{error}"
            )

            return

        signature = tuple(
            self._entry_signature(
                entry
            )
            for entry in entries
        )

        if (
            not force
            and signature
            == self._signature
        ):
            return

        self._signature = signature

        self._clear_cards()

        for entry in entries:
            card = SavedLetterCard(
                entry,
                self.canvas,
            )

            card.activated.connect(
                self._select_entry
            )

            self._cards.append(
                card
            )

        count = len(entries)

        self.count_label.setText(
            f"{count} saved "
            f"letter{'s' if count != 1 else ''}"
        )

        if entries:
            self.empty_label.hide()

            self._reflow_cards(
                force=True
            )

        else:
            self._show_empty(
                "No saved letters were found."
            )

    def _clear_cards(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)

            widget = item.widget()

            if (
                widget is not None
                and widget
                is not self.empty_label
            ):
                widget.hide()
                widget.deleteLater()

        self._cards.clear()
        self._last_columns = 0

    def _show_empty(
        self,
        message: str,
    ) -> None:
        self.empty_label.setText(
            message
        )

        self.empty_label.show()

        self.grid.addWidget(
            self.empty_label,
            0,
            0,
            1,
            1,
        )

    def _reflow_cards(
        self,
        *,
        force: bool = False,
    ) -> None:
        if not self._cards:
            return

        viewport_width = max(
            1,
            self.scroll.viewport().width()
            - 8,
        )

        columns = max(
            1,
            viewport_width
            // (
                self.CARD_WIDTH
                + self.GRID_GAP
            ),
        )

        if (
            not force
            and columns
            == self._last_columns
        ):
            return

        self._last_columns = columns

        for index, card in enumerate(
            self._cards
        ):
            self.grid.addWidget(
                card,
                index // columns,
                index % columns,
            )

    def _select_entry(
        self,
        entry: SavedLetter,
    ) -> None:
        self.letter_selected.emit(
            entry
        )

        self.hide()


# ─────────────────────────────────────────────────────────────────────────────
# Forge tab
# ─────────────────────────────────────────────────────────────────────────────


class ForgeTab(
    QtWidgets.QWidget
):
    letter_loaded = QtCore.Signal(dict)
    project_restored = QtCore.Signal(dict)

    correction_requested = QtCore.Signal(
        str,
        str,
    )

    preview_requested = QtCore.Signal(
        str,
        str,
    )

    preview_files_release_requested = (
        QtCore.Signal()
    )

    preview_visibility_changed = (
        QtCore.Signal(bool)
    )

    published_url_changed = (
        QtCore.Signal(str)
    )

    sync_requested = QtCore.Signal(str)
    curtain_changed = QtCore.Signal(str)

    preview_restart_requested = (
        QtCore.Signal(str)
    )

    def __init__(
        self,
        project_root: str | Path,
    ) -> None:
        super().__init__()

        self.project_root = Path(
            project_root
        ).resolve()

        self.settings_store = SettingsStore(
            self.project_root
        )

        self.saved_page_url = ""

        self.preview_mode_value = (
            "portrait"
        )

        self.preview_refresh_pending = (
            True
        )

        self._current_play_dir: Optional[
            Path
        ] = None

        self._project_fingerprint = (
            project_fingerprint(
                self.project_root
            )
        )

        self._operation_active = False
        self._tab_active = False

        self._host_window: Optional[
            QtWidgets.QWidget
        ] = None

        self._saved_letters_dialog: Optional[
            SavedLettersDialog
        ] = None

        self._init_ui()

        self.refresh_saved_page_url()

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.setContentsMargins(
            20,
            16,
            20,
            18,
        )

        layout.setSpacing(12)

        title = QLabel("Forge")

        title.setFont(
            QFont(
                "Segoe UI Semibold",
                18,
            )
        )

        title.setStyleSheet(
            "color:#00d0ff;"
        )

        title.setAlignment(
            Qt.AlignCenter
        )

        title.setGraphicsEffect(
            self._shadow_effect(12)
        )

        layout.addWidget(title)

        self.preview_format_panel = (
            QtWidgets.QFrame()
        )

        self.preview_format_panel.setObjectName(
            "forgePreviewFormatPanel"
        )

        format_layout = QHBoxLayout(
            self.preview_format_panel
        )

        format_layout.setContentsMargins(
            7,
            4,
            7,
            4,
        )

        format_layout.setSpacing(6)

        format_label = QLabel(
            "Preview"
        )

        format_label.setStyleSheet(
            "color:#bfeef3;"
            "font-weight:700;"
        )

        self.preview_mode_combo = (
            QtWidgets.QComboBox(
                self.preview_format_panel
            )
        )

        self.preview_mode_combo.addItem(
            "Portrait",
            "portrait",
        )

        self.preview_mode_combo.addItem(
            "Landscape",
            "landscape",
        )

        self.preview_mode_combo.addItem(
            "Window",
            "window",
        )

        self.preview_mode_combo.currentIndexChanged.connect(
            self._preview_mode_changed
        )

        format_layout.addWidget(
            format_label
        )

        format_layout.addWidget(
            self.preview_mode_combo
        )

        self.preview_format_panel.setStyleSheet(
            "QFrame#forgePreviewFormatPanel {"
            "background:#121920;"
            "border:1px solid #31515f;"
            "border-radius:8px;"
            "}"
            "QComboBox {"
            "min-height:28px;"
            "padding:0 24px 0 8px;"
            "background:#1b252d;"
            "color:#e7fbff;"
            "border:1px solid #3b5966;"
            "border-radius:5px;"
            "}"
        )

        load_row = QHBoxLayout()

        load_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        load_row.setSpacing(
            10
        )

        load_row.addStretch(1)

        # This button is placed directly inside the visible Forge page.
        # Nexus cannot hide or relocate it.
        self.settings_btn = (
            QtWidgets.QToolButton(
                self
            )
        )

        self.settings_btn.setText(
            "Settings"
        )

        self.settings_btn.setAccessibleName(
            "Forge settings"
        )

        self.settings_btn.setCursor(
            Qt.PointingHandCursor
        )

        self.settings_btn.setPopupMode(
            QtWidgets.QToolButton
            .ToolButtonPopupMode
            .InstantPopup
        )

        self.settings_btn.setMinimumWidth(
            120
        )

        self.settings_btn.setFixedHeight(
            40
        )

        self.settings_btn.setStyleSheet(
            "QToolButton {"
            "background:#0f0f12;"
            "color:#e6e6e6;"
            "border:1px solid #00d0ff;"
            "border-radius:8px;"
            "padding:6px 12px;"
            "font:700 11px 'Segoe UI';"
            "}"

            "QToolButton:hover {"
            "background:#113945;"
            "}"

            "QToolButton::menu-indicator {"
            "image:none;"
            "}"
        )

        self.settings_btn.setMenu(
            self._create_forge_settings_menu()
        )

        load_row.addWidget(
            self.settings_btn
        )

        self.load_btn = (
            self._tiny_button(
                "Load Letters"
            )
        )

        self.load_btn.setMinimumWidth(
            178
        )

        self.load_btn.setFixedHeight(
            40
        )

        self.load_btn.setToolTip(
            "Browse saved letters by cover, "
            "title, recipient, and "
            "publication status"
        )

        self.load_btn.clicked.connect(
            self.open_saved_letters
        )

        load_row.addWidget(
            self.load_btn
        )

        layout.addLayout(
            load_row
        )

        actions = QHBoxLayout()

        actions.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        actions.setSpacing(12)

        self.generate_btn = (
            self._styled_button(
                "Preview Letter",
                "#d66f00",
                "#ff9d22",
                "#fff8df",
            )
        )

        self.generate_btn.setToolTip(
            "Rebuild the local letter and "
            "open it in the default browser"
        )

        self.generate_btn.clicked.connect(
            self.generate
        )

        actions.addWidget(
            self.generate_btn,
            1,
        )

        self.seal_btn = (
            self._styled_button(
                "Publish Letter",
                "#51459b",
                "#7868db",
                "#ffffff",
            )
        )

        self.seal_btn.setToolTip(
            "Build the local letter and "
            "publish it through the "
            "configured GitHub repository"
        )

        self.seal_btn.clicked.connect(
            self.seal_the_letter
        )

        actions.addWidget(
            self.seal_btn,
            1,
        )

        self.go_to_page_btn = (
            self._page_button(
                "Open Letter"
            )
        )

        self.go_to_page_btn.setMinimumWidth(
            210
        )

        self.go_to_page_btn.setMaximumWidth(
            16777215
        )

        self.go_to_page_btn.setToolTip(
            "Open the saved published URL, "
            "or the current local letter "
            "when no URL is saved"
        )

        self.go_to_page_btn.clicked.connect(
            self.go_to_page
        )

        actions.addWidget(
            self.go_to_page_btn,
            1,
        )

        self.preview_btn = (
            self.generate_btn
        )

        self.publish_btn = (
            self.seal_btn
        )

        self.open_letter_btn = (
            self.go_to_page_btn
        )

        layout.addLayout(actions)

        self.status = QTextEdit(
            readOnly=True
        )

        self.status.setFont(
            QFont(
                "Segoe UI",
                10,
            )
        )

        self.status.setMinimumHeight(
            150
        )

        self.status.setStyleSheet(
            "background:#11171d;"
            "border:1px solid #31515f;"
            "border-radius:7px;"
            "color:#dbe8ee;"
            "padding:6px;"
        )

        layout.addWidget(
            self.status,
            1,
        )

        utility_row = QHBoxLayout()

        utility_row.addStretch(1)

        self.go_to_gallery_btn = (
            self._utility_button(
                "Go to Gallery"
            )
        )

        self.go_to_gallery_btn.setToolTip(
            "Open the folder containing "
            "all published letters"
        )

        self.go_to_gallery_btn.clicked.connect(
            self.go_to_gallery
        )

        utility_row.addWidget(
            self.go_to_gallery_btn
        )

        utility_row.addStretch(1)

        layout.addLayout(
            utility_row
        )

        self._log("Ready.")

    # ─────────────────────────────────────────────────────────────────────
    # Forge settings
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _forge_menu_stylesheet(
        ) -> str:
        return """
            QMenu {
                background:#0f0f12;
                color:#e6e6e6;
                border:1px solid #2b3344;
                padding:6px;
            }

            QMenu::item {
                padding:7px 26px 7px 12px;
                border-radius:6px;
            }

            QMenu::item:selected {
                background:#113945;
            }

            QMenu::item:checked {
                color:#00d0ff;
                font-weight:700;
            }

            QMenu::separator {
                height:1px;
                background:#2b3344;
                margin:6px;
            }
        """

    def _create_forge_settings_menu(
        self,
    ) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(
            self.settings_btn
        )

        menu.setStyleSheet(
            self._forge_menu_stylesheet()
        )

        self.curtain_menu = (
            menu.addMenu(
                "Curtain"
            )
        )

        self.curtain_menu.setStyleSheet(
            self._forge_menu_stylesheet()
        )

        self._curtain_action_group = (
            QtGui.QActionGroup(
                self
            )
        )

        self._curtain_action_group.setExclusive(
            True
        )

        self._curtain_actions: dict[
            str,
            QtGui.QAction,
        ] = {}

        for (
            style,
            label,
        ) in CURTAIN_STYLE_LABELS.items():
            action = (
                self.curtain_menu.addAction(
                    label
                )
            )

            action.setCheckable(
                True
            )

            action.setData(
                style
            )

            self._curtain_action_group.addAction(
                action
            )

            action.triggered.connect(
                lambda _checked=False, selected_style=style:
                self._set_curtain_style(
                    selected_style
                )
            )

            self._curtain_actions[
                style
            ] = action

        menu.aboutToShow.connect(
            self._sync_curtain_actions
        )

        self._sync_curtain_actions()

        return menu

    def _current_curtain_style(
        self,
    ) -> str:
        default_style = str(
            DEFAULT_SETTINGS.get(
                "curtain_style",
                "pure_white",
            )
        )

        style = str(
            self.settings_store.get(
                "curtain_style",
                default_style,
            )
        )

        if style not in CURTAIN_STYLE_LABELS:
            return default_style

        return style

    def _sync_curtain_actions(
        self,
    ) -> None:
        current_style = (
            self._current_curtain_style()
        )

        current_label = (
            CURTAIN_STYLE_LABELS.get(
                current_style,
                "Pure White",
            )
        )

        for (
            style,
            action,
        ) in getattr(
            self,
            "_curtain_actions",
            {},
        ).items():
            action.setChecked(
                style == current_style
            )

        if hasattr(
            self,
            "settings_btn",
        ):
            self.settings_btn.setToolTip(
                "Forge settings\n"
                f"Current curtain: "
                f"{current_label}"
            )

    def _set_curtain_style(
        self,
        style: str,
    ) -> None:
        if style not in CURTAIN_STYLE_LABELS:
            return

        previous_style = (
            self._current_curtain_style()
        )

        if style == previous_style:
            self._sync_curtain_actions()
            return

        self.settings_store.update_fields(
            curtain_style=style
        )

        self._sync_curtain_actions()

        # Force the generated Forge files to be rebuilt.
        self.schedule_refresh()

        self.sync_requested.emit(
            "curtain"
        )

        self.curtain_changed.emit(
            style
        )

        if self._tab_active:
            QtCore.QTimer.singleShot(
                0,
                self._refresh_after_curtain_change,
            )

    def _refresh_after_curtain_change(
        self,
    ) -> None:
        if not self._tab_active:
            return

        try:
            self.ensure_preview_current()

        except Exception as error:
            self._log(
                "❌ Curtain update failed: "
                f"{type(error).__name__}: "
                f"{error}"
            )

    def _preview_mode_changed(
        self,
        index: int,
    ) -> None:
        mode = str(
            self.preview_mode_combo
            .itemData(index)
            or "portrait"
        )

        self.preview_mode_value = (
            mode
            if mode in {
                "portrait",
                "landscape",
                "window",
            }
            else "portrait"
        )

        current = (
            self.current_play_index()
        )

        if current is not None:
            self.preview_requested.emit(
                str(current),
                self.preview_mode_value,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Saved letters
    # ─────────────────────────────────────────────────────────────────────

    def open_saved_letters(
        self,
    ) -> None:
        dialog = (
            self._saved_letters_dialog
        )

        if dialog is None:
            dialog = SavedLettersDialog(
                self.project_root,
                self,
            )

            dialog.letter_selected.connect(
                self._load_saved_letter
            )

            self._saved_letters_dialog = (
                dialog
            )

        dialog.refresh_from_disk(
            force=True
        )

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_load_menu(
        self,
    ) -> None:
        self.open_saved_letters()

    @staticmethod
    def _first_existing_directory(
        *candidates: Path,
    ) -> Optional[Path]:
        return next(
            (
                candidate
                for candidate in candidates
                if candidate.is_dir()
            ),
            None,
        )

    def _load_saved_letter(
        self,
        entry: SavedLetter,
    ) -> None:
        play_directory = Path(
            entry.path
        ).resolve()

        if not play_directory.is_dir():
            self._log(
                "❌ The selected saved-letter "
                "folder is missing."
            )
            return

        source_gallery = (
            play_directory
            / "gallery"
        )

        source_pages = (
            self._first_existing_directory(
                source_gallery / "pages",
                source_gallery
                / "user"
                / "pages",
                play_directory / "pages",
            )
        )

        source_message = (
            self._first_existing_directory(
                source_gallery / "message",
                source_gallery
                / "user"
                / "message",
                play_directory / "message",
            )
        )

        source_sounds = (
            self._first_existing_directory(
                source_gallery / "sounds",
                source_gallery
                / "user"
                / "sounds",
                play_directory / "sounds",
            )
        )

        if source_pages is None:
            self._log(
                "❌ Invalid saved letter: "
                "page images were not found."
            )
            return

        if source_message is None:
            self._log(
                "❌ Invalid saved letter: "
                "message files were not found."
            )
            return

        destination_pages = (
            self.project_root
            / USER_PAGES_DIR
        ).resolve()

        destination_message = (
            self.project_root
            / USER_MESSAGE_DIR
        ).resolve()

        destination_sounds = (
            self.project_root
            / USER_SOUNDS_DIR
        ).resolve()

        destination_pages.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination_message.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination_sounds.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            self.preview_files_release_requested.emit()

            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ExcludeUserInputEvents
                | QtCore.QEventLoop.ExcludeSocketNotifiers
            )

            page_files, page_directories = (
                _safe_clear_dir_contents(
                    destination_pages
                )
            )

            (
                message_files,
                message_directories,
            ) = _safe_clear_dir_contents(
                destination_message
            )

            copied_pages = 0

            for source in source_pages.iterdir():
                target = (
                    destination_pages
                    / source.name
                )

                if source.is_file():
                    shutil.copy2(
                        source,
                        target,
                    )

                    copied_pages += 1

                elif source.is_dir():
                    shutil.copytree(
                        source,
                        target,
                        dirs_exist_ok=True,
                    )

                    copied_pages += 1

            copied_message = 0

            for source in source_message.iterdir():
                target = (
                    destination_message
                    / source.name
                )

                if source.is_file():
                    shutil.copy2(
                        source,
                        target,
                    )

                    copied_message += 1

                elif source.is_dir():
                    shutil.copytree(
                        source,
                        target,
                        dirs_exist_ok=True,
                    )

                    copied_message += 1

            self._restore_saved_sound(
                source_sounds
            )

            metadata = (
                _read_play_metadata(
                    play_directory
                )
            )

            settings = _load_settings(
                self.project_root
            )

            settings[
                "recipient_name"
            ] = str(
                metadata.get(
                    "recipient_name"
                )
                or entry.recipient
            ).strip()

            settings[
                "recipient_title"
            ] = str(
                metadata.get(
                    "recipient_title"
                )
                or entry.title
            ).strip()

            settings[
                PUBLISHED_PAGE_URL_KEY
            ] = _normalize_page_url(
                str(
                    metadata.get(
                        PUBLISHED_PAGE_URL_KEY
                    )
                    or metadata.get(
                        "published_url"
                    )
                    or entry.published_url
                    or ""
                )
            )

            _write_settings(
                self.project_root,
                settings,
            )

        except Exception as error:
            self._log(
                "❌ Could not load the "
                f"saved letter: {error}"
            )
            return

        self.refresh_saved_page_url()

        self._current_play_dir = (
            play_directory
        )

        self._project_fingerprint = (
            project_fingerprint(
                self.project_root
            )
        )

        self.schedule_refresh()

        payload = {
            "recipient_name": settings[
                "recipient_name"
            ],
            "recipient_title": settings[
                "recipient_title"
            ],
            "published_page_url": settings[
                PUBLISHED_PAGE_URL_KEY
            ],
            "play_dir": str(
                play_directory
            ),
        }

        state = (
            "Published"
            if settings[
                PUBLISHED_PAGE_URL_KEY
            ]
            else "Local"
        )

        self._log(
            "✅ Loaded saved letter\n"
            f"Recipient: "
            f"{settings['recipient_name']}\n"
            f"Title: "
            f"{settings['recipient_title']}\n"
            f"Status: {state}\n"
            f"From: {play_directory}\n\n"
            f"Replaced pages: "
            f"{page_files} files, "
            f"{page_directories} folders\n"
            f"Replaced message: "
            f"{message_files} files, "
            f"{message_directories} folders\n"
            f"Copied: "
            f"{copied_pages} page items, "
            f"{copied_message} message items"
        )

        QtCore.QTimer.singleShot(
            0,
            lambda: self.letter_loaded.emit(
                payload
            ),
        )

        QtCore.QTimer.singleShot(
            0,
            lambda: self.project_restored.emit(
                payload
            ),
        )

    def _restore_saved_sound(
        self,
        source_sounds: Optional[Path],
    ) -> None:
        sound_payload: dict = {}

        source_music: Optional[
            Path
        ] = None

        if source_sounds is not None:
            manifest = (
                source_sounds
                / BUILD_SOUND_MANIFEST_NAME
            )

            if manifest.is_file():
                try:
                    value = json.loads(
                        manifest.read_text(
                            encoding="utf-8"
                        )
                    )

                    sound_payload = (
                        value
                        if isinstance(
                            value,
                            dict,
                        )
                        else {}
                    )

                except (
                    OSError,
                    json.JSONDecodeError,
                ):
                    sound_payload = {}

            candidate_music = (
                source_sounds
                / MUSIC_FILE
            )

            if candidate_music.is_file():
                source_music = (
                    candidate_music
                )

        raw_tracks = sound_payload.get(
            "tracks",
            [],
        )

        if not isinstance(
            raw_tracks,
            list,
        ):
            raw_tracks = []

        if (
            not raw_tracks
            and source_music is not None
        ):
            raw_tracks = [
                {
                    "filename": (
                        source_music.name
                    ),
                    "display_title": "Music",
                }
            ]

        imported_ids: list[str] = []

        for raw_track in raw_tracks:
            if (
                not isinstance(
                    raw_track,
                    dict,
                )
                or source_sounds is None
            ):
                continue

            filename = Path(
                str(
                    raw_track.get(
                        "filename",
                        "",
                    )
                )
            ).name

            source = (
                source_sounds
                / filename
            )

            if (
                not filename
                or not source.is_file()
            ):
                continue

            record = import_runtime_track(
                self.project_root,
                source,
                display_title=str(
                    raw_track.get(
                        "display_title",
                        "",
                    )
                ),
                original_name=str(
                    raw_track.get(
                        "original_name",
                        filename,
                    )
                ),
                content_hash=str(
                    raw_track.get(
                        "content_hash",
                        "",
                    )
                ),
                duration_seconds=float(
                    raw_track.get(
                        "duration_seconds",
                        0.0,
                    )
                    or 0.0
                ),
            )

            imported_ids.append(
                record.track_id
            )

        mode = (
            "playlist"
            if str(
                sound_payload.get(
                    "mode",
                    "single",
                )
            )
            == "playlist"
            else "single"
        )

        sound_state = ProjectSoundState(
            mode=mode,
            single_track_id=(
                imported_ids[0]
                if (
                    mode == "single"
                    and imported_ids
                )
                else ""
            ),
            playlist=(
                imported_ids
                if mode == "playlist"
                else []
            ),
            playlist_expanded=True,
            selected_track_id=(
                imported_ids[0]
                if imported_ids
                else ""
            ),
        )

        save_project_state(
            self.project_root,
            sound_state,
        )

        sync_current_compatibility(
            self.project_root,
            sound_state,
            load_library(
                self.project_root
            ),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Utility actions
    # ─────────────────────────────────────────────────────────────────────

    def go_to_gallery(self) -> None:
        ensure_output_dirs(
            self.project_root
        )

        gallery_directory = (
            self.project_root
            / OUTPUT_PLAY_DIR
        ).resolve()

        try:
            gallery_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

        except OSError as error:
            self._log(
                "❌ Could not create the "
                f"gallery folder: {error}"
            )
            return

        opened = (
            QtGui.QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    str(
                        gallery_directory
                    )
                )
            )
        )

        if opened:
            self._log(
                "✅ Opened published-letter "
                "gallery.\n"
                f"• Folder: "
                f"{gallery_directory}"
            )
            return

        self._log(
            "❌ Could not open the "
            "published-letter gallery.\n"
            f"• Folder: "
            f"{gallery_directory}"
        )

    def open_output_folder(self) -> None:
        self.go_to_gallery()

    def _call_host_hook(
        self,
        *names: str,
    ) -> bool:
        host = self.window()

        for name in names:
            callback = getattr(
                host,
                name,
                None,
            )

            if not callable(callback):
                continue

            try:
                callback()
                return True

            except TypeError:
                continue

            except Exception:
                return False

        return False

    def sync_all_from_disk(
        self,
        *,
        force: bool = False,
        notify_host: bool = True,
    ) -> bool:
        before = (
            self._project_fingerprint
        )

        after = project_fingerprint(
            self.project_root
        )

        changed = (
            force
            or before != after
        )

        self.refresh_saved_page_url()

        self._project_fingerprint = (
            after
        )

        if changed:
            self.schedule_refresh()

        self.sync_requested.emit(
            "changed"
            if changed
            else "checked"
        )

        if notify_host:
            self._call_host_hook(
                "sync_all_project_state",
                "refresh_all_project_state",
            )

        return changed

    def schedule_refresh(
        self,
        *_args,
    ) -> None:
        self.preview_refresh_pending = (
            True
        )

    def attach_readiness_window(
        self,
        host: QtWidgets.QWidget,
    ) -> None:
        self._host_window = host

    def refresh_project_state(
        self,
    ) -> None:
        self.sync_all_from_disk(
            notify_host=False
        )

    def refresh_saved_letters(
        self,
    ) -> None:
        dialog = (
            self._saved_letters_dialog
        )

        if dialog is not None:
            dialog.refresh_from_disk(
                force=True
            )

    def current_play_index(
        self,
    ) -> Optional[Path]:
        if (
            self._current_play_dir
            is not None
        ):
            index = (
                self._current_play_dir
                / "index.html"
            )

            if index.is_file():
                return index

        latest = (
            self._discover_latest_play_dir()
        )

        if latest is not None:
            self._current_play_dir = (
                latest
            )

            index = (
                latest
                / "index.html"
            )

            if index.is_file():
                return index

        return None

    def _discover_latest_play_dir(
        self,
    ) -> Optional[Path]:
        root = (
            self.project_root
            / OUTPUT_PLAY_DIR
        ).resolve()

        if not root.is_dir():
            return None

        indexes = [
            path
            for path in root.rglob(
                "index.html"
            )
            if path.is_file()
        ]

        if not indexes:
            return None

        try:
            latest = max(
                indexes,
                key=lambda path: (
                    path.stat().st_mtime_ns
                ),
            )

        except OSError:
            latest = indexes[0]

        return latest.parent

    def ensure_preview_current(
        self,
    ) -> Optional[Path]:
        if (
            self.preview_refresh_pending
            or self.current_play_index()
            is None
        ):
            return self._run_pipeline(
                mode="refresh",
                announce=False,
            )

        index = (
            self.current_play_index()
        )

        if index is not None:
            self.preview_visibility_changed.emit(
                True
            )

            self.preview_requested.emit(
                str(index),
                self.preview_mode_value,
            )

        return (
            index.parent
            if index is not None
            else None
        )

    def shutdown_operations(
        self,
    ) -> bool:
        return not self._operation_active

    def restart_preview(
        self,
        reason: str,
    ) -> None:
        self.preview_restart_requested.emit(
            reason
        )

        self._call_host_hook(
            "restart_forge_preview",
            "restart_preview",
            "reset_forge_preview",
        )

    def activate_for_tab_change(
        self,
    ) -> None:
        if self._tab_active:
            return

        self._tab_active = True

        self.sync_all_from_disk(
            notify_host=False
        )

        self.restart_preview(
            "enter"
        )

    def deactivate_for_tab_change(
        self,
    ) -> None:
        if not self._tab_active:
            return

        self.sync_all_from_disk(
            notify_host=False
        )

        self.restart_preview(
            "leave"
        )

        self._tab_active = False

    def showEvent(
        self,
        event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(event)

        self.activate_for_tab_change()

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        self.deactivate_for_tab_change()

        super().hideEvent(event)

    def refresh_saved_page_url(
        self,
    ) -> str:
        settings = _load_settings(
            self.project_root
        )

        self.saved_page_url = (
            _normalize_page_url(
                str(
                    settings.get(
                        PUBLISHED_PAGE_URL_KEY,
                        "",
                    )
                ).strip()
            )
        )

        self._sync_go_to_page_button()

        return self.saved_page_url

    def set_saved_page_url(
        self,
        url: str,
    ) -> None:
        self.saved_page_url = (
            _normalize_page_url(url)
        )

        self._sync_go_to_page_button()

    def _sync_go_to_page_button(
        self,
    ) -> None:
        has_url = bool(
            self.saved_page_url
        )

        has_local = (
            self.current_play_index()
            is not None
            if hasattr(
                self,
                "go_to_page_btn",
            )
            else False
        )

        if hasattr(
            self,
            "go_to_page_btn",
        ):
            self.go_to_page_btn.setEnabled(
                has_url or has_local
            )

            if has_url:
                self.go_to_page_btn.setToolTip(
                    self.saved_page_url
                )

            elif has_local:
                self.go_to_page_btn.setToolTip(
                    "Open the current local letter"
                )

            else:
                self.go_to_page_btn.setToolTip(
                    "Preview or publish a letter first"
                )

    def go_to_page(self) -> None:
        url = (
            self.refresh_saved_page_url()
        )

        if url:
            opened = (
                QtGui.QDesktopServices
                .openUrl(
                    QUrl(url)
                )
            )

            if opened:
                self._log(
                    "✅ Opened published "
                    "letter.\n"
                    f"• URL: {url}"
                )
                return

            self._log(
                "❌ Could not open the "
                "saved page URL.\n"
                f"• URL: {url}"
            )
            return

        index = (
            self.current_play_index()
        )

        if index is None:
            self._log(
                "❌ No local or published "
                "letter is available. "
                "Select Preview Letter first."
            )
            return

        opened = (
            QtGui.QDesktopServices
            .openUrl(
                QUrl.fromLocalFile(
                    str(index.resolve())
                )
            )
        )

        if opened:
            self._log(
                "✅ Opened local letter.\n"
                f"• File: {index}"
            )
            return

        self._log(
            "❌ Could not open the "
            "local letter.\n"
            f"• File: {index}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────

    def generate(self) -> None:
        self._run_pipeline(
            mode="preview"
        )

    def seal_the_letter(
        self,
    ) -> None:
        self._run_pipeline(
            mode="publish"
        )

    def _set_operation_buttons_enabled(
        self,
        enabled: bool,
    ) -> None:
        buttons = (
            self.settings_btn,
            self.load_btn,
            self.generate_btn,
            self.seal_btn,
            self.go_to_page_btn,
            self.go_to_gallery_btn,
        )

        for button in buttons:
            if button is self.go_to_page_btn:
                button.setEnabled(
                    enabled
                    and (
                        bool(
                            self.saved_page_url
                        )
                        or self.current_play_index()
                        is not None
                    )
                )

            else:
                button.setEnabled(
                    enabled
                )

    def _build_local_bundle(
        self,
        *,
        open_in_browser: bool,
    ) -> Path:
        ensure_output_dirs(
            self.project_root
        )

        generate_function = (
            _get_generate_fn()
        )

        if generate_function is None:
            raise RuntimeError(
                "generate.py is missing "
                "generate_play_bundle."
            )

        missing = (
            validate_required_images(
                self.project_root
            )
        )

        if missing:
            raise FileNotFoundError(
                "Missing "
                f"{', '.join(missing)} "
                "in "
                f"{self.project_root / 'gallery/user/pages'}"
            )

        message_path = (
            self.project_root
            / MESSAGE_HTML_FILE
        ).resolve()

        message_html = (
            self._read_message_html(
                message_path
            )
        )

        if not message_html.strip():
            raise ValueError(
                "Message is empty or missing: "
                f"{message_path}"
            )

        self.preview_files_release_requested.emit()

        play_directory = Path(
            generate_function(
                str(self.project_root),
                message_html=message_html,
                open_in_browser=open_in_browser,
            )
        ).resolve()

        index = (
            play_directory
            / "index.html"
        )

        if not index.is_file():
            raise FileNotFoundError(
                "Generated letter is missing "
                f"index.html: {index}"
            )

        self._current_play_dir = (
            play_directory
        )

        self.preview_refresh_pending = (
            False
        )

        self._project_fingerprint = (
            project_fingerprint(
                self.project_root
            )
        )

        self.preview_visibility_changed.emit(
            True
        )

        self.preview_requested.emit(
            str(index),
            self.preview_mode_value,
        )

        self._sync_go_to_page_button()

        return play_directory

    def _run_pipeline(
        self,
        *,
        mode: str,
        announce: bool = True,
    ) -> Optional[Path]:
        if self._operation_active:
            if announce:
                self._log(
                    "A Forge operation is already "
                    "running. Wait for it to finish "
                    "before starting another."
                )

            return None

        self._operation_active = True

        self._set_operation_buttons_enabled(
            False
        )

        try:
            open_in_browser = (
                mode == "preview"
            )

            play_directory = (
                self._build_local_bundle(
                    open_in_browser=(
                        open_in_browser
                    )
                )
            )

            if mode in {
                "preview",
                "refresh",
            }:
                if announce:
                    message = (
                        "✅ Local letter generated "
                        "successfully."
                    )

                    if open_in_browser:
                        message += (
                            " It was opened in the "
                            "default browser."
                        )

                    message += (
                        "\n• Letter: "
                        f"{play_directory / 'index.html'}"
                    )

                    message += (
                        self._font_export_note()
                    )

                    self._log(message)

                return play_directory

            if mode == "publish":
                self._publish_with_github_cli(
                    play_directory
                )

                return play_directory

            raise RuntimeError(
                "Unknown Forge operation: "
                f"{mode}"
            )

        except Exception as error:
            if announce:
                traceback_text = (
                    traceback.format_exc(
                        limit=20
                    )
                )

                self._log(
                    "❌ Forge error: "
                    f"{type(error).__name__}: "
                    f"{error}\n\n"
                    f"{traceback_text}"
                )

            return None

        finally:
            self._operation_active = False

            self._set_operation_buttons_enabled(
                True
            )

    def _run_command(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        timeout: int = 90,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def _publish_with_github_cli(
        self,
        play_directory: Path,
    ) -> None:
        github_cli = shutil.which(
            "gh"
        )

        git = shutil.which(
            "git"
        )

        if not github_cli:
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                f"• Letter: "
                f"{play_directory / 'index.html'}"
                "\n\n"
                "Online publishing was not "
                "configured because GitHub CLI "
                "is required for first-time "
                "publishing. Preview Letter and "
                "Open Letter remain available."
            )
            return

        if not git:
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "continue because Git is not "
                "available. Preview Letter and "
                "Open Letter remain available."
            )
            return

        authentication = self._run_command(
            [
                github_cli,
                "auth",
                "status",
            ],
            cwd=self.project_root,
            timeout=30,
        )

        if authentication.returncode != 0:
            detail = (
                authentication.stderr
                or authentication.stdout
            ).strip()

            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "continue because GitHub CLI "
                "is not authenticated.\n"
                f"{detail or 'Run gh auth login, then select Publish Letter again.'}"
            )
            return

        repository_root_result = (
            self._run_command(
                [
                    git,
                    "rev-parse",
                    "--show-toplevel",
                ],
                cwd=self.project_root,
                timeout=20,
            )
        )

        if (
            repository_root_result
            .returncode
            != 0
        ):
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "continue because this project "
                "is not inside a configured Git "
                "repository. No background "
                "worker was left running."
            )
            return

        repository_root = Path(
            repository_root_result
            .stdout
            .strip()
        ).resolve()

        try:
            relative_play = (
                play_directory.relative_to(
                    repository_root
                )
            )

        except ValueError:
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "continue because the generated "
                "gallery is outside the configured "
                "Git repository."
            )
            return

        repository_view = (
            self._run_command(
                [
                    github_cli,
                    "repo",
                    "view",
                    "--json",
                    "nameWithOwner",
                ],
                cwd=repository_root,
                timeout=30,
            )
        )

        if repository_view.returncode != 0:
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "identify the GitHub repository. "
                "Confirm the repository has a "
                "GitHub remote, then retry."
            )
            return

        try:
            repository_data = json.loads(
                repository_view.stdout
            )

            name_with_owner = str(
                repository_data.get(
                    "nameWithOwner",
                    "",
                )
            ).strip()

        except (
            json.JSONDecodeError,
            AttributeError,
        ):
            name_with_owner = ""

        if "/" not in name_with_owner:
            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "resolve the GitHub repository "
                "owner and name."
            )
            return

        add_result = self._run_command(
            [
                git,
                "add",
                "--",
                relative_play.as_posix(),
            ],
            cwd=repository_root,
        )

        if add_result.returncode != 0:
            detail = (
                add_result.stderr
                or add_result.stdout
            ).strip()

            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing failed while "
                f"staging files:\n{detail}"
            )
            return

        diff_result = self._run_command(
            [
                git,
                "diff",
                "--cached",
                "--quiet",
                "--",
                relative_play.as_posix(),
            ],
            cwd=repository_root,
            timeout=30,
        )

        if diff_result.returncode == 1:
            commit_result = (
                self._run_command(
                    [
                        git,
                        "commit",
                        "-m",
                        (
                            "Publish Letter "
                            "Smith gallery"
                        ),
                        "--",
                        relative_play.as_posix(),
                    ],
                    cwd=repository_root,
                )
            )

            if commit_result.returncode != 0:
                detail = (
                    commit_result.stderr
                    or commit_result.stdout
                ).strip()

                self._log(
                    "✅ The local letter was "
                    "generated successfully.\n"
                    "Online publishing failed "
                    f"while committing:\n{detail}"
                )
                return

        elif diff_result.returncode not in {
            0,
            1,
        }:
            detail = (
                diff_result.stderr
                or diff_result.stdout
            ).strip()

            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing could not "
                "inspect staged changes:\n"
                f"{detail}"
            )
            return

        branch_result = self._run_command(
            [
                git,
                "branch",
                "--show-current",
            ],
            cwd=repository_root,
            timeout=20,
        )

        branch = (
            branch_result.stdout.strip()
            or "main"
        )

        push_result = self._run_command(
            [
                git,
                "push",
                "-u",
                "origin",
                branch,
            ],
            cwd=repository_root,
            timeout=120,
        )

        if push_result.returncode != 0:
            detail = (
                push_result.stderr
                or push_result.stdout
            ).strip()

            self._log(
                "✅ The local letter was "
                "generated successfully.\n"
                "Online publishing failed while "
                f"pushing to GitHub:\n{detail}"
            )
            return

        pages_result = self._run_command(
            [
                github_cli,
                "api",
                (
                    "repos/"
                    f"{name_with_owner}"
                    "/pages"
                ),
            ],
            cwd=repository_root,
            timeout=30,
        )

        if pages_result.returncode != 0:
            pages_result = (
                self._run_command(
                    [
                        github_cli,
                        "api",
                        "--method",
                        "POST",
                        (
                            "repos/"
                            f"{name_with_owner}"
                            "/pages"
                        ),
                        "-f",
                        (
                            "source[branch]="
                            f"{branch}"
                        ),
                        "-f",
                        "source[path]=/",
                    ],
                    cwd=repository_root,
                    timeout=45,
                )
            )

        if pages_result.returncode != 0:
            detail = (
                pages_result.stderr
                or pages_result.stdout
            ).strip()

            self._log(
                "✅ The local letter was "
                "generated and pushed to "
                "GitHub.\n"
                "GitHub Pages configuration "
                "did not complete, so no "
                "published URL was saved.\n"
                f"{detail}"
            )
            return

        owner, repository = (
            name_with_owner.split(
                "/",
                1,
            )
        )

        if (
            repository.casefold()
            == f"{owner}.github.io".casefold()
        ):
            base_url = (
                f"https://{owner}.github.io/"
            )

        else:
            base_url = (
                f"https://{owner}.github.io/"
                f"{repository}/"
            )

        relative_url = (
            relative_play.as_posix()
            .strip("/")
        )

        published_url = (
            base_url
            + (
                relative_url + "/"
                if relative_url
                else ""
            )
        )

        settings = _load_settings(
            self.project_root
        )

        settings[
            PUBLISHED_PAGE_URL_KEY
        ] = published_url

        _write_settings(
            self.project_root,
            settings,
        )

        self.saved_page_url = (
            published_url
        )

        self.published_url_changed.emit(
            published_url
        )

        self._sync_go_to_page_button()

        self._log(
            "✅ Local letter generated and "
            "pushed to GitHub.\n"
            f"• Local: "
            f"{play_directory / 'index.html'}\n"
            f"• Published URL: "
            f"{published_url}\n\n"
            "GitHub Pages may take a short "
            "time to finish deploying the "
            "update."
        )

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _read_message_html(
        self,
        message_path: Path,
    ) -> str:
        try:
            if not message_path.exists():
                return ""

            return read_text_normalized(
                message_path
            )

        except Exception:
            return ""

    def _font_export_note(
        self,
    ) -> str:
        reporter = getattr(
            generate,
            "get_last_font_export_report",
            None,
        )

        if not callable(reporter):
            return ""

        report = reporter()

        fallback = tuple(
            report.get(
                "fallback",
                (),
            )
        ) if isinstance(
            report,
            dict,
        ) else ()

        if not fallback:
            return ""

        return (
            "\n• Font fallback used: "
            + ", ".join(fallback)
        )

    def _log(
        self,
        text: str,
    ) -> None:
        self.status.setPlainText(
            text
        )

    def closeEvent(
        self,
        event: QtGui.QCloseEvent,
    ) -> None:
        if self.shutdown_operations():
            event.accept()
        else:
            event.ignore()

    # ─────────────────────────────────────────────────────────────────────
    # Button styles
    # ─────────────────────────────────────────────────────────────────────

    def _styled_button(
        self,
        text: str,
        background_color: str,
        border_color: str,
        text_color: str,
    ) -> QPushButton:
        button = QPushButton(text)

        button.setFont(
            QFont(
                "Segoe UI Semibold",
                14,
            )
        )

        button.setMinimumHeight(
            52
        )

        button.setStyleSheet(
            "QPushButton {"
            f"background:{background_color};"
            f"border:2px solid {border_color};"
            "border-radius:10px;"
            "padding:14px 20px;"
            f"color:{text_color};"
            "font-weight:bold;"
            "}"
            "QPushButton:hover {"
            f"background:{border_color};"
            "}"
            "QPushButton:disabled {"
            "background:#25282d;"
            "color:#777;"
            "border-color:#444;"
            "}"
        )

        button.setGraphicsEffect(
            self._shadow_effect(16)
        )

        return button

    def _page_button(
        self,
        text: str,
    ) -> QPushButton:
        button = QPushButton(text)

        button.setFont(
            QFont(
                "Segoe UI Semibold",
                13,
            )
        )

        button.setMinimumHeight(
            52
        )

        button.setMinimumWidth(
            164
        )

        button.setMaximumWidth(
            186
        )

        button.setStyleSheet(
            "QPushButton {"
            "background:#24292f;"
            "color:#f0f6fc;"
            "border:2px solid #57606a;"
            "border-radius:10px;"
            "padding:14px 16px;"
            "font-weight:700;"
            "}"
            "QPushButton:hover {"
            "background:#30363d;"
            "border-color:#8b949e;"
            "}"
            "QPushButton:disabled {"
            "background:#161b22;"
            "color:#6e7681;"
            "border-color:#30363d;"
            "}"
        )

        button.setGraphicsEffect(
            self._shadow_effect(16)
        )

        return button

    def _utility_button(
        self,
        text: str,
    ) -> QPushButton:
        button = QPushButton(text)

        button.setFont(
            QFont(
                "Segoe UI Semibold",
                12,
            )
        )

        button.setMinimumHeight(
            44
        )

        button.setStyleSheet(
            "QPushButton {"
            "background:#0f0f12;"
            "color:#e6e6e6;"
            "border:1px solid #00d0ff;"
            "border-radius:8px;"
            "padding:10px 14px;"
            "}"
            "QPushButton:hover {"
            "background:#113945;"
            "}"
            "QPushButton:disabled {"
            "color:#6e7681;"
            "border-color:#30363d;"
            "}"
        )

        button.setGraphicsEffect(
            self._shadow_effect(12)
        )

        return button

    def _tiny_button(
        self,
        text: str,
    ) -> QPushButton:
        button = QPushButton(text)

        button.setFont(
            QFont(
                "Segoe UI",
                11,
                QFont.DemiBold,
            )
        )

        button.setFixedHeight(
            30
        )

        button.setStyleSheet(
            "QPushButton {"
            "background:#0f0f12;"
            "color:#e6e6e6;"
            "border:1px solid #00d0ff;"
            "border-radius:8px;"
            "padding:6px 12px;"
            "}"
            "QPushButton:hover {"
            "background:#113945;"
            "}"
            "QPushButton:disabled {"
            "color:#6e7681;"
            "border-color:#30363d;"
            "}"
        )

        return button

    def _shadow_effect(
        self,
        blur_radius: int,
    ) -> QGraphicsDropShadowEffect:
        shadow = (
            QGraphicsDropShadowEffect(
                self
            )
        )

        shadow.setBlurRadius(
            blur_radius
        )

        shadow.setOffset(
            0,
            4,
        )

        shadow.setColor(
            QColor(
                0,
                0,
                0,
                160,
            )
        )

        return shadow


__all__ = [
    "ForgeTab",
    "SavedLetterCard",
    "SavedLettersDialog",
]