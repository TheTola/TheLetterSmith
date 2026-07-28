from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

import generate
from config import MESSAGE_HTML_FILE, OUTPUT_PLAY_DIR, ensure_output_dirs
from message_html import read_text_normalized
from project_state import ensure_project_identity
from publishing import GitHubPagesPublisher
from publishing.github_pages import PUBLIC_WARNING_KEY
from readiness import ReadinessResult, evaluate_readiness
from saved_letters import (
    RestoredProject,
    SavedLetter,
    SavedLetterCatalog,
    SavedLetterRestorer,
    update_saved_metadata,
)
from settings_store import (
    PUBLISHED_PAGE_URL_KEY,
    SettingsStore,
    normalize_published_page_url,
)


PREVIEW_MODE_KEY = "forge_preview_mode"
PREVIEW_MODES = (
    ("Portrait", "portrait"),
    ("Landscape", "landscape"),
    ("Window / Browser", "window"),
)
_LOGGER = logging.getLogger(__name__)


class _ForgeOperationError(RuntimeError):
    """An operation failure whose message is safe to show in Forge."""


class _TaskWorker(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str, bool)
    finished = QtCore.Signal()

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._task())
        except Exception as error:
            self.failed.emit(
                str(error),
                traceback.format_exc(),
                isinstance(error, _ForgeOperationError),
            )
        finally:
            self.finished.emit()


class _StatusLabel(QtWidgets.QLabel):
    """Compact transient status with the legacy text accessor used by tests."""

    def toPlainText(self) -> str:
        return self.text()

    def setPlainText(self, text: str) -> None:
        self.setText(text)


class ReadinessWindow(QtWidgets.QDialog):
    correction_requested = QtCore.Signal(str, str)

    def __init__(self, project_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.user_closed = False
        self._positioned = False
        self.setWindowTitle("Project Readiness")
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(280)
        self.setMaximumWidth(350)
        self.setStyleSheet(
            "QDialog{background:#101820;border:1px solid #2e596a;}"
            "QLabel{background:transparent;}"
            "QPushButton{font:500 10pt 'Segoe UI';}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.percentage = QtWidgets.QLabel()
        self.percentage.setStyleSheet(
            "color:#dffcff;font:700 13px 'Segoe UI';"
        )
        top.addWidget(self.percentage)
        top.addStretch(1)
        self.status = QtWidgets.QLabel()
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.status)
        layout.addLayout(top)

        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        divider.setStyleSheet("color:#284451;")
        layout.addWidget(divider)

        self.items = QtWidgets.QWidget(self)
        self.items_layout = QtWidgets.QVBoxLayout(self.items)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(3)
        layout.addWidget(self.items)

        self._missing_buttons: dict[str, QtWidgets.QPushButton] = {}
        for item in evaluate_readiness(self.project_root).items:
            button = QtWidgets.QPushButton(item.label)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, tab=item.correction_tab,
                target=item.correction_target:
                self.correction_requested.emit(tab, target)
            )
            self.items_layout.addWidget(button)
            self._missing_buttons[item.key] = button

    def refresh(self, result: ReadinessResult) -> None:
        self.percentage.setText(f"{result.completion_percentage}%")
        self.status.setText(result.status)
        self.status.setStyleSheet(
            f"color:{'#7fe29a' if result.status != 'Not Ready' else '#ff8585'};"
            "font:600 10pt 'Segoe UI';"
        )

        missing = {item.key: item for item in result.missing_items}
        for key, button in self._missing_buttons.items():
            item = missing.get(key)
            button.setVisible(item is not None)
            if item is None:
                continue
            color = "#ff9b9b" if item.required else "#dcc979"
            border = "#6b3f49" if item.required else "#625b38"
            button.setText(item.label)
            button.setToolTip(item.detail)
            button.setStyleSheet(
                "QPushButton{text-align:left;padding:6px 8px;"
                f"border:1px solid {border};border-radius:5px;"
                f"background:#131e26;color:{color};}}"
                "QPushButton:hover{background:#182a35;border-color:#00cdec;}"
                "QPushButton:focus{border:1px solid #00d5f5;}"
            )

        self.items.setVisible(bool(missing))
        self.adjustSize()

    def position_near_image_area(self) -> None:
        if self._positioned:
            return
        owner = self.parentWidget()
        if owner is None:
            return
        target = owner.mapToGlobal(QtCore.QPoint(24, 96))
        tabbar = getattr(owner, "tabbar", None)
        if isinstance(tabbar, QtWidgets.QTabBar) and tabbar.count():
            rectangle = tabbar.tabRect(0)
            target = tabbar.mapToGlobal(
                QtCore.QPoint(rectangle.left() + 8, rectangle.bottom() + 10)
            )
        screen = owner.screen()
        if screen is not None:
            available = screen.availableGeometry()
            target.setX(
                max(
                    available.left(),
                    min(target.x(), available.right() - self.width()),
                )
            )
            target.setY(
                max(
                    available.top(),
                    min(target.y(), available.bottom() - self.height()),
                )
            )
        self.move(target)
        self._positioned = True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.user_closed = True
        super().closeEvent(event)


class ForgeTab(QtWidgets.QWidget):
    correction_requested = QtCore.Signal(str, str)
    project_restored = QtCore.Signal(dict)
    letter_loaded = QtCore.Signal(dict)
    preview_requested = QtCore.Signal(str, str)
    preview_visibility_changed = QtCore.Signal(bool)
    published_url_changed = QtCore.Signal(str)
    _settings_refresh_requested = QtCore.Signal()

    def __init__(self, project_root: str | Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.catalog = SavedLetterCatalog(self.project_root)
        self.restorer = SavedLetterRestorer(self.project_root)
        self.saved_page_url = ""
        self._last_play_dir: Optional[Path] = None
        self._preview_mode = self._saved_preview_mode()
        self._readiness_result = evaluate_readiness(self.project_root)
        self._busy = False
        self._worker: Optional[_TaskWorker] = None
        self._worker_thread: Optional[QtCore.QThread] = None

        self.readiness_window = ReadinessWindow(self.project_root, self.window())
        self.readiness_window.correction_requested.connect(
            self.correction_requested.emit
        )
        self._init_ui()

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh_project_state)
        self._settings_refresh_requested.connect(self._refresh_timer.start)
        self.settings.changed.connect(self._on_settings_changed)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self.status.clear)

        self.refresh_saved_letters()
        self.refresh_project_state()

    def _saved_preview_mode(self) -> str:
        value = str(self.settings.get(PREVIEW_MODE_KEY, "portrait")).strip()
        valid = {mode for _label, mode in PREVIEW_MODES}
        return value if value in valid else "portrait"

    def _init_ui(self) -> None:
        self.setObjectName("ForgeWorkflow")
        self.setStyleSheet(
            "QWidget#ForgeWorkflow{background:transparent;}"
            "QLabel{color:#d9e7ed;font:10pt 'Segoe UI';}"
            "QComboBox,QLineEdit{background:#121b23;color:#e8f9ff;"
            "border:1px solid #375463;border-radius:6px;padding:6px 8px;}"
            "QComboBox:focus,QLineEdit:focus{border-color:#00d2ef;}"
        )

        self._main_layout = QtWidgets.QVBoxLayout(self)
        self._main_layout.setContentsMargins(72, 20, 72, 12)
        self._main_layout.setSpacing(8)

        heading_row = QtWidgets.QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 6)
        heading_row.setSpacing(10)
        self._heading_balance = QtWidgets.QWidget()
        heading_row.addWidget(self._heading_balance)
        heading_row.addStretch(1)
        self.heading_title = QtWidgets.QLabel("Forge")
        self.heading_title.setAlignment(Qt.AlignCenter)
        self.heading_title.setStyleSheet(
            "color:#00d4f4;font:700 18pt 'Segoe UI';"
        )
        heading_row.addWidget(self.heading_title)
        heading_row.addStretch(1)

        self._readiness_controls = QtWidgets.QWidget()
        readiness_row = QtWidgets.QHBoxLayout(self._readiness_controls)
        readiness_row.setContentsMargins(0, 0, 0, 0)
        readiness_row.setSpacing(10)
        self.readiness_summary = QtWidgets.QLabel()
        self.readiness_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        readiness_row.addWidget(self.readiness_summary)
        self.readiness_btn = self._small_button("Review Readiness")
        self.readiness_btn.clicked.connect(self.show_readiness_window)
        readiness_row.addWidget(self.readiness_btn)
        heading_row.addWidget(self._readiness_controls)
        self._main_layout.addLayout(heading_row)

        self.saved_panel = QtWidgets.QFrame()
        self.saved_panel.setObjectName("ForgeSavedPanel")
        self.saved_panel.setMaximumWidth(820)
        self.saved_panel.setStyleSheet(
            "QFrame#ForgeSavedPanel{background:#101820;"
            "border:1px solid #284554;border-radius:7px;}"
        )
        saved_row = QtWidgets.QHBoxLayout(self.saved_panel)
        saved_row.setContentsMargins(10, 8, 10, 8)
        saved_row.setSpacing(8)
        saved_row.addWidget(QtWidgets.QLabel("Saved letter"))
        self.saved_selector = QtWidgets.QComboBox()
        self.saved_selector.setEditable(True)
        self.saved_selector.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.saved_selector.setMinimumWidth(360)
        self.saved_selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        completer = self.saved_selector.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        saved_row.addWidget(self.saved_selector, 1)
        self.load_saved_btn = self._small_button("Load Saved Letter")
        self.load_saved_btn.clicked.connect(self.load_selected_letter)
        saved_row.addWidget(self.load_saved_btn)
        self.refresh_saved_btn = self._small_button("Refresh")
        self.refresh_saved_btn.clicked.connect(self.refresh_saved_letters)
        saved_row.addWidget(self.refresh_saved_btn)

        saved_holder = QtWidgets.QHBoxLayout()
        saved_holder.setContentsMargins(0, 0, 0, 0)
        saved_holder.addStretch(1)
        saved_holder.addWidget(self.saved_panel, 1)
        saved_holder.addStretch(1)
        self._main_layout.addLayout(saved_holder)

        self.identity_panel = QtWidgets.QFrame()
        self.identity_panel.setObjectName("ForgeIdentity")
        self.identity_panel.setMaximumWidth(1510)
        self.identity_panel.setStyleSheet(
            "QFrame#ForgeIdentity{background:#111921;"
            "border:1px solid #253d49;border-radius:7px;}"
        )
        identity_row = QtWidgets.QHBoxLayout(self.identity_panel)
        identity_row.setContentsMargins(10, 7, 10, 7)
        identity_row.setSpacing(10)
        identity_row.addWidget(self._muted_label("Title"))
        self.identity_title = QtWidgets.QLabel()
        self.identity_title.setStyleSheet("color:#f2fbff;font:600 10pt 'Segoe UI';")
        identity_row.addWidget(self.identity_title, 1)
        identity_row.addWidget(self._muted_label("Recipient"))
        self.identity_recipient = QtWidgets.QLabel()
        self.identity_recipient.setStyleSheet(
            "color:#f2fbff;font:600 10pt 'Segoe UI';"
        )
        identity_row.addWidget(self.identity_recipient, 1)
        identity_row.addWidget(self._muted_label("Published URL"))
        self.published_url = QtWidgets.QLineEdit()
        self.published_url.setReadOnly(True)
        self.published_url.setMaximumWidth(380)
        self.published_url.setPlaceholderText(
            "Save the deployed URL in Message after publishing"
        )
        identity_row.addWidget(self.published_url, 1)
        self.copy_link_btn = self._small_button("Copy Published Link")
        self.copy_link_btn.clicked.connect(self.copy_published_link)
        identity_row.addWidget(self.copy_link_btn)

        identity_holder = QtWidgets.QHBoxLayout()
        identity_holder.setContentsMargins(0, 0, 0, 0)
        identity_holder.addStretch(1)
        identity_holder.addWidget(self.identity_panel, 1)
        identity_holder.addStretch(1)
        self._main_layout.addLayout(identity_holder)

        self.preview_format_panel = QtWidgets.QWidget(self)
        self.preview_format_panel.setObjectName("ForgePreviewFormat")
        self.preview_format_panel.setFixedWidth(232)
        self.preview_format_panel.setStyleSheet(
            "QWidget#ForgePreviewFormat{background:transparent;}"
            "QComboBox{background:#121b23;color:#e8f9ff;"
            "border:1px solid #00d2ef;border-radius:6px;padding:6px 8px;}"
            "QComboBox:focus{border-color:#8defff;}"
        )
        format_row = QtWidgets.QHBoxLayout(self.preview_format_panel)
        format_row.setContentsMargins(0, 0, 0, 0)
        format_row.setSpacing(8)
        format_row.addWidget(self._muted_label("Preview format"))
        self.preview_mode = QtWidgets.QComboBox()
        self.preview_mode.setMinimumWidth(132)
        for label, mode in PREVIEW_MODES:
            self.preview_mode.addItem(label, mode)
        current = self.preview_mode.findData(self._preview_mode)
        self.preview_mode.setCurrentIndex(max(0, current))
        self.preview_mode.currentIndexChanged.connect(
            self._preview_mode_changed
        )
        format_row.addWidget(self.preview_mode)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.preview_btn = self._action_button(
            "Preview Letter", "#b86600", "#f09b18"
        )
        self.preview_btn.clicked.connect(self.preview_letter)
        actions.addWidget(self.preview_btn, 4)
        self.publish_btn = self._action_button(
            "Publish Letter", "#5a45bb", "#7c67de"
        )
        self.publish_btn.clicked.connect(self.publish_letter)
        actions.addWidget(self.publish_btn, 4)
        self.open_published_btn = self._action_button(
            "Open Published Letter", "#17232d", "#426070"
        )
        self.open_published_btn.clicked.connect(self.open_published_letter)
        actions.addWidget(self.open_published_btn, 3)
        self._main_layout.addLayout(actions)

        self.status = _StatusLabel()
        self.status.setObjectName("ForgeStatus")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setMinimumHeight(24)
        self.status.setMaximumHeight(46)
        self.status.setStyleSheet(
            "QLabel#ForgeStatus{color:#a9c4cf;padding:3px 2px;}"
        )
        self._main_layout.addWidget(self.status)
        self._main_layout.addStretch(1)

    def _sync_heading_balance(self) -> None:
        self._heading_balance.setFixedWidth(
            self._readiness_controls.sizeHint().width()
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        side_margin = min(104, max(24, int(self.width() * 0.055)))
        self._main_layout.setContentsMargins(
            side_margin,
            20,
            side_margin,
            12,
        )
        identity_width = max(
            0,
            min(1510, self.width() - (side_margin * 2)),
        )
        if identity_width:
            self.identity_panel.setFixedWidth(identity_width)
        self._sync_heading_balance()
        super().resizeEvent(event)

    @staticmethod
    def _muted_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color:#839da8;font:9pt 'Segoe UI';")
        return label

    @staticmethod
    def _small_button(text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton{background:#15212b;color:#dff8ff;"
            "border:1px solid #365365;border-radius:6px;padding:6px 10px;}"
            "QPushButton:hover{border-color:#00d4f4;background:#192a35;}"
            "QPushButton:focus{border:1px solid #00d4f4;}"
            "QPushButton:disabled{color:#61727a;border-color:#293942;}"
        )
        return button

    @staticmethod
    def _action_button(
        text: str,
        background: str,
        hover: str,
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setMinimumHeight(42)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton{{background:{background};color:white;"
            "border:1px solid #566d79;border-radius:7px;"
            "font:700 10pt 'Segoe UI';padding:8px 14px;}"
            f"QPushButton:hover{{background:{hover};}}"
            "QPushButton:focus{border:2px solid #d9faff;}"
            "QPushButton:disabled{background:#182129;color:#64747c;"
            "border-color:#2d3b43;}"
        )
        return button

    def _on_settings_changed(
        self,
        _settings: dict,
        _keys: tuple[str, ...],
    ) -> None:
        self._settings_refresh_requested.emit()

    def schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def refresh_project_state(self) -> None:
        snapshot = self.settings.snapshot()
        title = str(snapshot.get("recipient_title", "")).strip()
        recipient = str(snapshot.get("recipient_name", "")).strip()
        self.identity_title.setText(title or "Untitled")
        self.identity_title.setToolTip(title)
        self.identity_recipient.setText(recipient or "No recipient")
        self.identity_recipient.setToolTip(recipient)
        self.refresh_saved_page_url()
        self.refresh_readiness()

    def show_readiness_window(self) -> None:
        self.refresh_readiness()
        self.readiness_window.user_closed = False
        self.readiness_window.show()
        self.readiness_window.position_near_image_area()
        self.readiness_window.raise_()
        self.readiness_window.activateWindow()

    def attach_readiness_window(self, owner: QtWidgets.QWidget) -> None:
        self.readiness_window.setParent(owner, Qt.Tool)
        self.readiness_window._positioned = False

    def refresh_readiness(self) -> ReadinessResult:
        self._readiness_result = evaluate_readiness(self.project_root)
        self.readiness_window.refresh(self._readiness_result)
        result = self._readiness_result
        color = "#7fe29a" if result.status != "Not Ready" else "#ff8585"
        self.readiness_summary.setText(
            f"{result.completion_percentage}%  {result.status}"
        )
        self.readiness_summary.setStyleSheet(
            f"color:{color};font:600 10pt 'Segoe UI';"
        )
        self._sync_heading_balance()
        self.preview_btn.setEnabled(not self._busy and result.can_preview)
        self.publish_btn.setEnabled(not self._busy and result.can_publish)
        return result

    def refresh_saved_letters(self) -> None:
        selected_path = ""
        current = self.saved_selector.currentData()
        if isinstance(current, SavedLetter):
            selected_path = str(current.path)
        entries = self.catalog.list_entries()
        self.saved_selector.clear()
        selected_index = -1
        for index, entry in enumerate(entries):
            saved = entry.modified_at.strftime("%b %d, %Y")
            state = "Published" if entry.published else "Local"
            prefix = "Recovery — " if entry.recovery else ""
            self.saved_selector.addItem(
                f"{prefix}{entry.title} — {entry.recipient}  •  "
                f"Saved {saved}  •  {state}",
                entry,
            )
            self.saved_selector.setItemData(
                index,
                str(entry.path),
                Qt.ToolTipRole,
            )
            if str(entry.path) == selected_path:
                selected_index = index
        if selected_index >= 0:
            self.saved_selector.setCurrentIndex(selected_index)
        available = bool(entries)
        if not available:
            self.saved_selector.addItem("No saved letters found", None)
        self.saved_selector.setEnabled(available and not self._busy)
        self.load_saved_btn.setEnabled(available and not self._busy)

    def load_selected_letter(self) -> None:
        entry = self.saved_selector.currentData()
        if not isinstance(entry, SavedLetter) or self._busy:
            return

        def task() -> RestoredProject:
            return self.restorer.restore(entry)

        self._start_operation(
            "Loading saved letter…",
            task,
            self._complete_restore,
            "The selected saved letter could not be restored.",
        )

    def _load_saved_letter(self, entry: SavedLetter) -> None:
        """Synchronous compatibility path used by focused service tests."""
        try:
            restored = self.restorer.restore(entry)
        except Exception:
            _LOGGER.exception("Saved-letter restore failed.")
            self._set_status(
                "The selected saved letter could not be restored.",
                error=True,
            )
            return
        self._complete_restore(restored)

    def _complete_restore(self, restored: object) -> None:
        if not isinstance(restored, RestoredProject):
            self._set_status(
                "The selected saved letter could not be restored.",
                error=True,
            )
            return
        self._last_play_dir = restored.play_dir
        self.refresh_project_state()
        self.refresh_saved_letters()
        self.request_preview()
        payload = restored.as_payload()
        self.project_restored.emit(payload)
        self.letter_loaded.emit(payload)
        self._set_status("Saved letter loaded.")

    def _preview_mode_changed(self) -> None:
        mode = str(self.preview_mode.currentData() or "portrait")
        self._preview_mode = mode
        self.settings.update_fields({PREVIEW_MODE_KEY: mode})
        self.request_preview()

    def _current_play_index(self) -> Optional[Path]:
        if self._last_play_dir is not None:
            index = self._last_play_dir / "index.html"
            if index.is_file():
                return index
        project_id = ensure_project_identity(self.project_root)
        index = self.project_root / OUTPUT_PLAY_DIR / project_id / "index.html"
        return index if index.is_file() else None

    def current_play_index(self) -> Optional[Path]:
        """Return the current playable viewer entry point, when available."""
        return self._current_play_index()

    @property
    def preview_mode_value(self) -> str:
        return self._preview_mode

    def request_preview(self) -> None:
        index = self._current_play_index()
        if index is not None:
            self.preview_requested.emit(str(index.resolve()), self._preview_mode)

    def _required_gate(self) -> Optional[ReadinessResult]:
        readiness = self.refresh_readiness()
        if readiness.can_preview and readiness.can_publish:
            return readiness
        missing = [
            item for item in readiness.missing_items if item.required
        ]
        if missing:
            self._set_status(f"{missing[0].label} is required.", error=True)
        return None

    def preview_letter(self) -> None:
        if self._busy:
            return
        readiness = self._required_gate()
        if readiness is None:
            return
        ensure_output_dirs(self.project_root)
        try:
            message = read_text_normalized(
                self.project_root / MESSAGE_HTML_FILE
            )
        except Exception:
            _LOGGER.exception("Could not read the current message.")
            self._set_status("Message content could not be read.", error=True)
            return

        def task() -> tuple[Path, bool, ReadinessResult]:
            play_dir, rebuilt = generate.ensure_play_bundle(
                self.project_root,
                message_html=message,
                force=False,
            )
            return Path(play_dir).resolve(), rebuilt, readiness

        self._start_operation(
            "Preparing preview…",
            task,
            self._preview_completed,
            "Preview could not be updated. The previous preview was preserved.",
        )

    def _preview_completed(self, result: object) -> None:
        play_dir, _rebuilt, readiness = result
        self._last_play_dir = Path(play_dir)
        self.request_preview()
        self._set_status("Preview updated.")
        QtCore.QTimer.singleShot(
            0,
            lambda: self._update_metadata_silently(
                Path(play_dir),
                readiness,
            ),
        )

    def publish_letter(self) -> None:
        if self._busy:
            return
        readiness = self._required_gate()
        if readiness is None:
            return
        if not bool(self.settings.get(PUBLIC_WARNING_KEY, False)):
            answer = QtWidgets.QMessageBox.question(
                self,
                "Publish Letter",
                "Published letters are placed in a public GitHub repository. "
                "Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self._set_status("Publishing canceled.")
                return
            self.settings.update_fields({PUBLIC_WARNING_KEY: True})

        try:
            message = read_text_normalized(
                self.project_root / MESSAGE_HTML_FILE
            )
        except Exception:
            _LOGGER.exception("Could not read the current message.")
            self._set_status("Message content could not be read.", error=True)
            return

        def task() -> tuple[Path, ReadinessResult, dict, object]:
            play_dir, _rebuilt = generate.ensure_play_bundle(
                self.project_root,
                message_html=message,
                force=True,
            )
            play_path = Path(play_dir).resolve()
            metadata = update_saved_metadata(
                play_path,
                self.project_root,
                readiness,
            )
            publisher = GitHubPagesPublisher(self.project_root)
            if not publisher.is_configured():
                configured = publisher.configure(None)
                if not configured.configured:
                    raise _ForgeOperationError(
                        configured.message or "Publishing is not configured."
                    )
            publish_result = publisher.publish(play_path, metadata)
            return play_path, readiness, metadata, publish_result

        self._start_operation(
            "Publishing letter…",
            task,
            self._publish_completed,
            "Publishing failed. The local build was preserved.",
        )

    def _publish_completed(self, result: object) -> None:
        play_dir, _readiness, _metadata, publish_result = result
        self._last_play_dir = Path(play_dir)
        self.request_preview()
        if not getattr(publish_result, "success", False):
            details = str(getattr(publish_result, "technical_details", ""))
            if details:
                _LOGGER.error("Publishing failed: %s", details)
            self._set_status(
                str(getattr(publish_result, "message", ""))
                or "Publishing failed. The local build was preserved.",
                error=True,
            )
            return
        url = normalize_published_page_url(
            getattr(publish_result, "url", "")
        )
        if not url:
            _LOGGER.error("Publisher returned an invalid public URL.")
            self._set_status(
                "Publishing completed without a valid public URL.",
                error=True,
            )
            return
        self.settings.update_fields({PUBLISHED_PAGE_URL_KEY: url})
        self.published_url_changed.emit(url)
        self.refresh_project_state()
        published_readiness = self._readiness_result
        self.refresh_saved_letters()
        self._update_metadata_silently(
            Path(play_dir),
            published_readiness,
            public_path=str(getattr(publish_result, "public_path", "")),
        )
        self._set_status("The letter has been sealed.")

    def _update_metadata_silently(
        self,
        play_dir: Path,
        readiness: ReadinessResult,
        *,
        public_path: str = "",
    ) -> None:
        try:
            update_saved_metadata(
                play_dir,
                self.project_root,
                readiness,
                public_path=public_path,
            )
            self.refresh_saved_letters()
        except Exception:
            _LOGGER.exception(
                "Playable build metadata could not be updated for %s",
                play_dir,
            )

    def refresh_saved_page_url(self) -> str:
        self.saved_page_url = normalize_published_page_url(
            self.settings.get(PUBLISHED_PAGE_URL_KEY, "")
        )
        self._sync_published_url()
        return self.saved_page_url

    def set_saved_page_url(self, url: str) -> None:
        self.saved_page_url = normalize_published_page_url(url)
        self._sync_published_url()
        self.refresh_readiness()

    def _sync_published_url(self) -> None:
        available = bool(self.saved_page_url)
        self.published_url.setText(self.saved_page_url)
        self.published_url.setCursorPosition(0)
        self.published_url.setToolTip(self.saved_page_url)
        self.open_published_btn.setEnabled(available)
        self.copy_link_btn.setEnabled(available)
        if available:
            self.open_published_btn.setToolTip(self.saved_page_url)
            self.copy_link_btn.setToolTip(self.saved_page_url)
        else:
            disabled = "Save a valid HTTP or HTTPS published URL in Message."
            self.open_published_btn.setToolTip(disabled)
            self.copy_link_btn.setToolTip(disabled)

    def copy_published_link(self) -> None:
        url = self.refresh_saved_page_url()
        if not url:
            self._set_status("No valid published link is available.", error=True)
            return
        QtWidgets.QApplication.clipboard().setText(url)
        self._set_status("Published link copied.")

    def open_published_letter(self) -> None:
        url = self.refresh_saved_page_url()
        if not url:
            self._set_status("No valid published link is available.", error=True)
            return
        if not QtGui.QDesktopServices.openUrl(QUrl(url)):
            self._set_status(
                "The published letter could not be opened.",
                error=True,
            )
            return
        self._set_status("Published letter opened.")

    def _start_operation(
        self,
        activity: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
        error_message: str,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_busy(True)
        self._set_status(activity, timeout_ms=0)

        thread = QtCore.QThread(self)
        worker = _TaskWorker(task)
        worker.moveToThread(thread)
        self._worker_thread = thread
        self._worker = worker
        thread.started.connect(worker.run)

        def succeeded(result: object) -> None:
            try:
                on_success(result)
            except Exception:
                _LOGGER.exception("Forge completion handling failed.")
                self._set_status(error_message, error=True)

        def failed(
            message: str,
            technical: str,
            user_safe: bool,
        ) -> None:
            _LOGGER.error(
                "Forge operation failed: %s\n%s",
                message,
                technical,
            )
            safe_message = (
                message
                if user_safe
                and isinstance(message, str)
                and message
                and len(message) <= 240
                else error_message
            )
            self._set_status(safe_message or error_message, error=True)

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._operation_finished)
        thread.start()

    def _operation_finished(self) -> None:
        thread = self._worker_thread
        self._worker = None
        self._worker_thread = None
        self._busy = False
        self._set_busy(False)
        if thread is not None:
            thread.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self.saved_selector.setEnabled(not busy and self.saved_selector.count() > 0)
        self.load_saved_btn.setEnabled(
            not busy and isinstance(self.saved_selector.currentData(), SavedLetter)
        )
        self.refresh_saved_btn.setEnabled(not busy)
        self.preview_mode.setEnabled(not busy)
        self.readiness_btn.setEnabled(not busy)
        if busy:
            self.preview_btn.setEnabled(False)
            self.publish_btn.setEnabled(False)
        else:
            self.refresh_readiness()

    def _set_status(
        self,
        message: str,
        *,
        error: bool = False,
        timeout_ms: int = 4500,
    ) -> None:
        self.status.setText(message)
        color = "#ff9a9a" if error else "#a9cbd6"
        self.status.setStyleSheet(
            f"QLabel#ForgeStatus{{color:{color};padding:3px 2px;}}"
        )
        self._status_timer.stop()
        if message and timeout_ms > 0:
            self._status_timer.start(timeout_ms)

    def _log(self, message: str) -> None:
        """Compatibility alias for older callers and focused UI tests."""
        self._set_status(message)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.refresh_project_state()
        self.refresh_saved_letters()
        self.request_preview()
        self.preview_visibility_changed.emit(True)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.preview_visibility_changed.emit(False)
        super().hideEvent(event)

    def shutdown_operations(self, timeout_ms: int = 5000) -> bool:
        thread = self._worker_thread
        if thread is None or not thread.isRunning():
            return True
        thread.requestInterruption()
        thread.quit()
        return thread.wait(timeout_ms)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.settings.changed.disconnect(self._on_settings_changed)
        self.readiness_window.close()
        if not self.shutdown_operations():
            event.ignore()
            self._set_status(
                "Finish the current Forge operation before closing.",
                error=True,
                timeout_ms=0,
            )
            return
        super().closeEvent(event)
