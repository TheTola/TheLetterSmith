from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

import generate
from config import MESSAGE_HTML_FILE, ensure_output_dirs
from message_html import read_text_normalized
from publishing import GitHubPagesPublisher, PublishResult
from publishing.expiration import (
    PUBLISHED_EXPIRES_AT_KEY,
    is_publication_expiration_malformed,
    is_publication_expired,
    publication_expiry_label,
)
from publishing.github_pages import PUBLIC_WARNING_KEY
from readiness import ReadinessResult, evaluate_readiness
from project_paths import ProjectPathResolver
from project_state import (
    ApplicationState,
    ProjectIdentity,
    ProjectStateController,
)
from saved_letters import (
    RestoredProject,
    SavedLetter,
    SavedLetterCatalog,
    SavedLetterDeleteError,
    SavedLetterRestorer,
    record_saved_letter_activity,
    update_saved_metadata,
)
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
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
PREVIEW_MODE_DESCRIPTIONS = {
    "portrait": "Tall letter-card presentation",
    "landscape": "Wide cinematic presentation",
    "window": "Fit the available browser window",
}
RECENT_SAVED_LETTER_LIMIT = 15
_LOGGER = logging.getLogger(__name__)


def _forge_source_fingerprint(project_root: Path) -> str:
    try:
        return str(generate.build_source_fingerprint(project_root))
    except Exception:
        _LOGGER.exception("Forge source fingerprint could not be calculated.")
        return ""


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


class _PreviewModeDelegate(QtWidgets.QStyledItemDelegate):
    """Two-line, high-contrast entries for the Preview format menu."""

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        del option, index
        return QtCore.QSize(280, 52)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        painter.save()
        selected = bool(
            option.state & QtWidgets.QStyle.State_Selected
        )
        hovered = bool(
            option.state & QtWidgets.QStyle.State_MouseOver
        )
        background = (
            QtGui.QColor("#0fcbe8")
            if selected
            else QtGui.QColor("#17303b")
            if hovered
            else QtGui.QColor("#0d1a22")
        )
        painter.fillRect(option.rect.adjusted(2, 2, -2, -2), background)

        text_color = QtGui.QColor("#061217" if selected else "#f1fdff")
        detail_color = QtGui.QColor("#163943" if selected else "#9bcbd5")
        title_font = QtGui.QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSizeF(max(9.5, title_font.pointSizeF()))
        painter.setFont(title_font)
        painter.setPen(text_color)
        title_rect = option.rect.adjusted(12, 6, -10, -24)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, str(index.data()))

        detail_font = QtGui.QFont(option.font)
        detail_font.setPointSizeF(max(8.0, detail_font.pointSizeF() - 1.0))
        painter.setFont(detail_font)
        painter.setPen(detail_color)
        detail_rect = option.rect.adjusted(12, 27, -10, -5)
        detail = str(index.data(Qt.UserRole + 1) or "")
        painter.drawText(detail_rect, Qt.AlignLeft | Qt.AlignVCenter, detail)
        painter.restore()


class _PreviewModeCombo(QtWidgets.QComboBox):
    """Bright selector with a purpose-built popup instead of the native menu."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewFormatSelector")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(178, 38)
        self.setMaxVisibleItems(len(PREVIEW_MODES))
        view = QtWidgets.QListView(self)
        view.setObjectName("PreviewFormatMenu")
        view.setMouseTracking(True)
        view.setSpacing(2)
        view.setUniformItemSizes(True)
        view.setMinimumWidth(290)
        self.setView(view)
        self.setItemDelegate(_PreviewModeDelegate(self))
        self._arrow = QtWidgets.QLabel("⌄", self)
        self._arrow.setObjectName("PreviewFormatArrow")
        self._arrow.setAlignment(Qt.AlignCenter)
        self._arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QComboBox#PreviewFormatSelector{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #183747,stop:1 #102631);"
            "color:#f3fdff;border:1px solid #4fe5ff;border-radius:8px;"
            "padding:7px 38px 7px 12px;font:600 10pt 'Segoe UI';}"
            "QComboBox#PreviewFormatSelector:hover{background:#1a4050;border-color:#9af3ff;}"
            "QComboBox#PreviewFormatSelector:focus{border:2px solid #d0faff;padding:6px 37px 6px 11px;}"
            "QComboBox#PreviewFormatSelector::drop-down{width:34px;border:none;"
            "border-left:1px solid rgba(118,230,247,.45);}"
            "QComboBox#PreviewFormatSelector::down-arrow{image:none;width:0;height:0;}"
            "QListView#PreviewFormatMenu{background:#0d1a22;color:#f1fdff;"
            "border:1px solid #4fe5ff;border-radius:8px;padding:4px;outline:0;}"
            "QLabel#PreviewFormatArrow{color:#bff8ff;background:transparent;"
            "font:700 15pt 'Segoe UI Symbol';}"
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._arrow.setGeometry(self.width() - 33, 1, 31, self.height() - 2)
        self._arrow.raise_()
        super().resizeEvent(event)

    def showPopup(self) -> None:
        self.view().setMinimumWidth(max(290, self.width()))
        super().showPopup()


class _StatusLabel(QtWidgets.QLabel):
    """Compact transient status with the legacy text accessor used by tests."""

    def toPlainText(self) -> str:
        return self.text()

    def setPlainText(self, text: str) -> None:
        self.setText(text)


class SavedLetterCard(QtWidgets.QFrame):
    """Compact, keyboard-accessible saved-letter selector."""

    selected = QtCore.Signal(object)
    activated = QtCore.Signal(object)
    delete_requested = QtCore.Signal(object)

    def __init__(self, entry: SavedLetter, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("SavedLetterCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(184, 214)
        self.setToolTip(
            f"{entry.title} — {entry.recipient}\n"
            f"{entry.path}\nDouble-click or press Enter to load."
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(3)

        self.delete_button = QtWidgets.QToolButton(self)
        self.delete_button.setObjectName("SavedLetterDelete")
        self.delete_button.setText("−")
        self.delete_button.setToolTip("Delete saved letter")
        self.delete_button.setAccessibleName("Delete saved letter")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setFixedSize(24, 24)
        self.delete_button.hide()
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self.entry)
        )

        self.cover = QtWidgets.QLabel()
        self.cover.setObjectName("SavedLetterCover")
        self.cover.setFixedHeight(96)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setAttribute(Qt.WA_TransparentForMouseEvents)
        pixmap = (
            QtGui.QPixmap(str(entry.cover_path))
            if entry.cover_path is not None
            else QtGui.QPixmap()
        )
        if pixmap.isNull():
            self.cover.setText("No cover")
        else:
            self.cover.setPixmap(
                pixmap.scaled(
                    168,
                    92,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        layout.addWidget(self.cover)

        display_name = f"{entry.title} — {entry.recipient}"
        self.name_label = QtWidgets.QLabel(display_name)
        self.name_label.setObjectName("SavedLetterName")
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(34)
        self.name_label.setToolTip(display_name)
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.name_label)

        self.title_label = QtWidgets.QLabel(
            f"Title: {self._shorten(entry.title, 25)}"
        )
        self.title_label.setToolTip(entry.title)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)

        self.recipient_label = QtWidgets.QLabel(
            f"Recipient: {self._shorten(entry.recipient, 21)}"
        )
        self.recipient_label.setToolTip(entry.recipient)
        self.recipient_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.recipient_label)

        self.status_label = QtWidgets.QLabel(
            "Published" if entry.published else "Local"
        )
        self.status_label.setObjectName(
            "PublishedStatus" if entry.published else "LocalStatus"
        )
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFixedHeight(20)
        self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.status_label)

        self.setStyleSheet(
            "QFrame#SavedLetterCard{background:#111b23;"
            "border:1px solid #345160;border-radius:7px;}"
            "QFrame#SavedLetterCard:hover{border-color:#00b9d8;"
            "background:#14232d;}"
            "QFrame#SavedLetterCard[selected=\"true\"]{"
            "border:2px solid #00d4f4;background:#142630;}"
            "QFrame#SavedLetterCard:focus{border:2px solid #8defff;}"
            "QToolButton#SavedLetterDelete{background:rgba(8,16,21,.90);"
            "color:#ff9a9a;border:1px solid #74454b;border-radius:11px;"
            "font:700 14pt 'Segoe UI';padding:0;}"
            "QToolButton#SavedLetterDelete:hover{background:#702f38;"
            "color:#fff;border-color:#ff9a9a;}"
            "QToolButton#SavedLetterDelete:focus{border-color:#fff;}"
            "QLabel#SavedLetterCover{background:#091116;color:#78909a;"
            "border:1px solid #263e4a;border-radius:4px;"
            "font:9pt 'Segoe UI';}"
            "QLabel#SavedLetterName{color:#f0fbff;"
            "font:600 9pt 'Segoe UI';border:none;background:transparent;}"
            "QLabel{color:#aac0ca;font:8pt 'Segoe UI';"
            "border:none;background:transparent;}"
            "QLabel#LocalStatus{color:#b8c9d0;background:#1b2932;"
            "border:1px solid #3b515d;border-radius:8px;"
            "font:600 8pt 'Segoe UI';}"
            "QLabel#PublishedStatus{color:#8bf0aa;background:#122a21;"
            "border:1px solid #35734d;border-radius:8px;"
            "font:600 8pt 'Segoe UI';}"
        )

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return f"{value[:max(1, limit - 1)].rstrip()}…"

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_delete_mode(self, enabled: bool) -> None:
        self.delete_button.setVisible(bool(enabled))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self.delete_button.move(self.width() - 30, 7)
        self.delete_button.raise_()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self.selected.emit(self.entry)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.entry)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.entry)
            event.accept()
            return
        super().keyPressEvent(event)


class ReadinessWindow(QtWidgets.QFrame):
    correction_requested = QtCore.Signal(str, str)

    def __init__(self, project_root: str | Path, parent=None) -> None:
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint,
        )
        self.project_root = Path(project_root).resolve()
        self._owner = parent
        self._allow_close = False
        self._drag_offset: Optional[QtCore.QPoint] = None
        self.user_closed = False
        self.setObjectName("ProjectReadiness")
        self.setWindowTitle("")
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(400, 128)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Preferred,
        )
        self.setStyleSheet(
            "QFrame#ProjectReadiness{background:#101820;border:1px solid #2e596a;"
            "border-radius:9px;}"
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
        self.items_scroll = QtWidgets.QScrollArea(self)
        self.items_scroll.setObjectName("ReadinessItemsScroll")
        self.items_scroll.setWidgetResizable(True)
        self.items_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.items_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items_scroll.setStyleSheet(
            "QScrollArea#ReadinessItemsScroll{background:transparent;border:none;}"
            "QScrollArea#ReadinessItemsScroll>QWidget>QWidget{background:transparent;}"
            "QScrollBar:vertical{background:#101820;width:9px;margin:0;}"
            "QScrollBar::handle:vertical{background:#315a68;border-radius:4px;"
            "min-height:24px;}"
        )
        self.items_scroll.setWidget(self.items)
        layout.addWidget(self.items_scroll, 1)

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

        self.items_scroll.setVisible(bool(missing))
        self._resize_to_content()
        if self.isVisible():
            self.position_near_image_area()

    def position_near_image_area(self) -> None:
        """Anchor the tool below the preview without constraining Forge."""
        owner = self._owner or self.parentWidget()
        if owner is None:
            return
        preview = getattr(owner, "preview_frame", None)
        screen = owner.screen() or QtGui.QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry()
            if screen is not None
            else QtCore.QRect(0, 0, 1200, 800)
        )
        self._resize_to_content(available)

        if isinstance(preview, QtWidgets.QWidget) and preview.isVisible():
            lower_left = preview.mapToGlobal(
                QtCore.QPoint(0, preview.height())
            )
            lower_right = preview.mapToGlobal(
                QtCore.QPoint(preview.width(), preview.height())
            )
            x = lower_right.x() - self.width()
            y = lower_left.y() + 10
        else:
            origin = owner.mapToGlobal(QtCore.QPoint(18, 72))
            x = origin.x()
            y = origin.y()

        x = max(
            available.left() + 12,
            min(x, available.right() - self.width() - 11),
        )
        space_below = available.bottom() - y - 11
        if space_below >= self.minimumHeight():
            self.resize(self.width(), min(self.height(), space_below))
        else:
            y = available.bottom() - self.height() - 11
        y = max(available.top() + 12, y)
        self.move(x, y)

    def attach_to(self, owner: QtWidgets.QWidget) -> None:
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        self._owner = owner
        self.setParent(
            owner,
            Qt.Tool | Qt.FramelessWindowHint,
        )
        if was_visible:
            self.show()
            self.position_near_image_area()

    def _resize_to_content(
        self,
        available: Optional[QtCore.QRect] = None,
    ) -> None:
        if available is None:
            screen = (
                self._owner.screen()
                if isinstance(self._owner, QtWidgets.QWidget)
                else QtGui.QGuiApplication.primaryScreen()
            )
            available = (
                screen.availableGeometry()
                if screen is not None
                else QtCore.QRect(0, 0, 1200, 800)
            )
        self.layout().activate()
        self.items_layout.activate()
        visible_buttons = [
            button
            for button in self._missing_buttons.values()
            if not button.isHidden()
        ]
        widest_button = max(
            (button.sizeHint().width() for button in visible_buttons),
            default=320,
        )
        width = min(
            max(400, widest_button + 42),
            min(720, max(400, available.width() - 24)),
        )
        item_height = sum(
            button.sizeHint().height() for button in visible_buttons
        )
        if visible_buttons:
            item_height += self.items_layout.spacing() * (
                len(visible_buttons) - 1
            )
        header_height = max(
            self.percentage.sizeHint().height(),
            self.status.sizeHint().height(),
        )
        desired_height = 42 + header_height + item_height
        if visible_buttons:
            desired_height += 12
        height = min(
            max(self.minimumHeight(), desired_height),
            max(self.minimumHeight(), available.height() - 24),
        )
        self.resize(width, height)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._allow_close:
            event.ignore()
            return
        event.accept()

    def shutdown(self) -> None:
        self._allow_close = True
        self.user_closed = True
        self.hide()
        self.close()


class ForgeTab(QtWidgets.QWidget):
    correction_requested = QtCore.Signal(str, str)
    project_restored = QtCore.Signal(dict)
    letter_loaded = QtCore.Signal(dict)
    preview_requested = QtCore.Signal(str, str)
    preview_files_release_requested = QtCore.Signal()
    project_files_release_requested = QtCore.Signal()
    restore_activity_changed = QtCore.Signal(bool, str)
    preview_visibility_changed = QtCore.Signal(bool)
    published_url_changed = QtCore.Signal(str)
    _settings_refresh_requested = QtCore.Signal()

    def __init__(
        self,
        project_root: str | Path,
        *,
        project_state: ProjectStateController | None = None,
        project_paths: ProjectPathResolver | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.project_state = project_state
        if self.project_state is None:
            self.project_state = ProjectStateController(
                self.project_root
            )
            self.project_state.initialize()
        self.project_paths = project_paths or ProjectPathResolver(
            self.project_root
        )
        self.catalog = SavedLetterCatalog(self.project_root)
        self.restorer = SavedLetterRestorer(
            self.project_root,
            resolver=self.project_paths,
        )
        self.saved_page_url = ""
        self._last_play_dir: Optional[Path] = None
        self._preview_mode = self._saved_preview_mode()
        self._readiness_result = evaluate_readiness(self.project_root)
        self._busy = False
        self._worker: Optional[_TaskWorker] = None
        self._worker_thread: Optional[QtCore.QThread] = None
        self._operation_success: Optional[Callable[[object], None]] = None
        self._operation_failure: Optional[Callable[[], None]] = None
        self._operation_error_message = ""
        self._restore_operation_active = False
        self._selected_saved_letter: Optional[SavedLetter] = None
        self._pending_recipient_entry: Optional[SavedLetter] = None
        self._saved_cards: list[SavedLetterCard] = []
        self._archived_entries: list[SavedLetter] = []
        self._archive_groups: dict[str, tuple[SavedLetter, ...]] = {}
        self._saved_delete_mode = False
        self._project_fingerprint = _forge_source_fingerprint(self.project_root)
        self._source_revision = 0
        try:
            self._preview_refresh_pending = not generate.is_play_bundle_current(
                self.project_root
            )
        except Exception:
            _LOGGER.exception("Forge build currency could not be determined.")
            self._preview_refresh_pending = True
        self._preview_refresh_requested = False
        self._readiness_requested = False
        self._tab_active = False
        self._pending_scroll_position = (0, 0)
        self._pending_metadata_update: Optional[
            tuple[Path, ReadinessResult, bool]
        ] = None

        self.readiness_window = ReadinessWindow(self.project_root)
        self.readiness_window.correction_requested.connect(
            self.correction_requested.emit
        )
        self._init_ui()

        self._card_layout_timer = QtCore.QTimer(self)
        self._card_layout_timer.setSingleShot(True)
        self._card_layout_timer.timeout.connect(self._layout_saved_cards)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh_project_state)
        self._settings_refresh_requested.connect(self._refresh_timer.start)
        self.settings.changed.connect(self._on_settings_changed)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self.status.clear)

        self._catalog_refresh_timer = QtCore.QTimer(self)
        self._catalog_refresh_timer.setSingleShot(True)
        self._catalog_refresh_timer.setInterval(180)
        self._catalog_refresh_timer.timeout.connect(self.refresh_saved_letters)
        self._catalog_watcher = QtCore.QFileSystemWatcher(self)
        self._catalog_watcher.directoryChanged.connect(
            self._catalog_path_changed
        )
        self._catalog_watcher.fileChanged.connect(
            self._catalog_path_changed
        )

        self._scroll_restore_timer = QtCore.QTimer(self)
        self._scroll_restore_timer.setSingleShot(True)
        self._scroll_restore_timer.timeout.connect(
            self._restore_saved_scroll_position
        )
        self._metadata_timer = QtCore.QTimer(self)
        self._metadata_timer.setSingleShot(True)
        self._metadata_timer.timeout.connect(self._run_pending_metadata_update)

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

        self.load_saved_btn = self._small_button("Load Letters")
        self.load_saved_btn.setMinimumSize(150, 36)
        self.load_saved_btn.clicked.connect(self.show_saved_letters)
        saved_holder = QtWidgets.QHBoxLayout()
        saved_holder.setContentsMargins(0, 0, 0, 0)
        saved_holder.addStretch(1)
        saved_holder.addWidget(self.load_saved_btn)
        saved_holder.addStretch(1)
        self._main_layout.addLayout(saved_holder)

        self.saved_panel = QtWidgets.QFrame(
            self,
            Qt.Popup | Qt.FramelessWindowHint,
        )
        self.saved_panel.setObjectName("ForgeSavedPanel")
        self.saved_panel.setMinimumSize(560, 480)
        self.saved_panel.setMaximumSize(1100, 720)
        self.saved_panel.setStyleSheet(
            "QFrame#ForgeSavedPanel{background:#101820;"
            "border:1px solid #3b6678;border-radius:8px;}"
        )
        saved_layout = QtWidgets.QVBoxLayout(self.saved_panel)
        saved_layout.setContentsMargins(12, 10, 12, 12)
        saved_layout.setSpacing(8)
        saved_header = QtWidgets.QHBoxLayout()
        saved_header.setContentsMargins(0, 0, 0, 0)
        saved_heading = QtWidgets.QLabel("Saved Letters")
        saved_heading.setStyleSheet(
            "color:#dff9ff;font:600 11pt 'Segoe UI';"
        )
        saved_header.addWidget(saved_heading)
        saved_header.addStretch(1)
        self.saved_delete_toggle = QtWidgets.QToolButton()
        self.saved_delete_toggle.setObjectName("SavedLetterDeleteToggle")
        self.saved_delete_toggle.setText("−")
        self.saved_delete_toggle.setCheckable(True)
        self.saved_delete_toggle.setFixedSize(28, 28)
        self.saved_delete_toggle.setCursor(Qt.PointingHandCursor)
        self.saved_delete_toggle.setAccessibleName(
            "Show saved-letter delete controls"
        )
        self.saved_delete_toggle.setStyleSheet(
            "QToolButton{background:#15212b;color:#ffb2b2;"
            "border:1px solid #65434a;border-radius:13px;"
            "font:700 15pt 'Segoe UI';padding:0;}"
            "QToolButton:hover{background:#40272d;border-color:#ff9a9a;}"
            "QToolButton:checked{background:#702f38;color:#fff;"
            "border-color:#ffb2b2;}"
        )
        self.saved_delete_toggle.clicked.connect(
            self._set_saved_delete_mode
        )
        saved_header.addWidget(self.saved_delete_toggle)
        saved_layout.addLayout(saved_header)

        self.saved_scroll = QtWidgets.QScrollArea()
        self.saved_scroll.setObjectName("SavedLettersScroll")
        self.saved_scroll.setWidgetResizable(True)
        self.saved_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.saved_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saved_scroll.setStyleSheet(
            "QScrollArea#SavedLettersScroll{background:transparent;border:none;}"
            "QScrollBar:vertical{background:#0c151b;width:9px;margin:0;}"
            "QScrollBar::handle:vertical{background:#31505e;"
            "border-radius:4px;min-height:28px;}"
            "QScrollBar::handle:vertical:hover{background:#00a9c5;}"
        )
        self.saved_cards_widget = QtWidgets.QWidget()
        self.saved_cards_widget.setObjectName("SavedLetterCards")
        self.saved_cards_widget.setStyleSheet(
            "QWidget#SavedLetterCards{background:transparent;}"
        )
        self.saved_cards_layout = QtWidgets.QGridLayout(
            self.saved_cards_widget
        )
        self.saved_cards_layout.setContentsMargins(2, 2, 2, 2)
        self.saved_cards_layout.setHorizontalSpacing(8)
        self.saved_cards_layout.setVerticalSpacing(8)
        self.saved_cards_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.saved_scroll.setWidget(self.saved_cards_widget)
        saved_layout.addWidget(self.saved_scroll, 1)

        self.saved_archive = QtWidgets.QFrame()
        self.saved_archive.setObjectName("SavedLettersArchive")
        self.saved_archive.setStyleSheet(
            "QFrame#SavedLettersArchive{background:#0d151c;"
            "border:1px solid #294653;border-radius:7px;}"
            "QLabel{color:#bfeaf3;background:transparent;}"
        )
        archive_layout = QtWidgets.QVBoxLayout(self.saved_archive)
        archive_layout.setContentsMargins(8, 7, 8, 8)
        archive_layout.setSpacing(6)
        archive_header = QtWidgets.QHBoxLayout()
        archive_header.setContentsMargins(0, 0, 0, 0)
        self.saved_archive_label = QtWidgets.QLabel("Archive")
        self.saved_archive_label.setStyleSheet(
            "font:600 10pt 'Segoe UI';color:#dff9ff;"
        )
        archive_header.addWidget(self.saved_archive_label)
        self.saved_archive_recipient = QtWidgets.QComboBox()
        self.saved_archive_recipient.setAccessibleName(
            "Archived-letter recipient"
        )
        self.saved_archive_recipient.setMinimumWidth(230)
        self.saved_archive_recipient.setStyleSheet(
            "QComboBox{background:#13222c;color:#eafcff;"
            "border:1px solid #356072;border-radius:6px;padding:5px 9px;}"
            "QComboBox:hover{border-color:#00cce8;}"
            "QComboBox QAbstractItemView{background:#101b23;color:#eafcff;"
            "selection-background-color:#17485a;border:1px solid #356072;}"
        )
        self.saved_archive_recipient.currentIndexChanged.connect(
            self._show_archived_recipient
        )
        archive_header.addWidget(self.saved_archive_recipient, 1)
        archive_layout.addLayout(archive_header)

        self.saved_archive_list = QtWidgets.QListWidget()
        self.saved_archive_list.setObjectName("SavedLettersArchiveList")
        self.saved_archive_list.setIconSize(QtCore.QSize(38, 48))
        self.saved_archive_list.setMaximumHeight(164)
        self.saved_archive_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.saved_archive_list.setStyleSheet(
            "QListWidget#SavedLettersArchiveList{background:#091116;"
            "color:#edfaff;border:1px solid #223e4a;border-radius:5px;"
            "font:600 9pt 'Segoe UI';outline:none;}"
            "QListWidget#SavedLettersArchiveList::item{padding:4px 7px;}"
            "QListWidget#SavedLettersArchiveList::item:selected{"
            "background:#17485a;color:#ffffff;}"
        )
        self.saved_archive_list.itemSelectionChanged.connect(
            self._select_archived_letter
        )
        self.saved_archive_list.itemActivated.connect(
            self._activate_archived_letter
        )
        archive_layout.addWidget(self.saved_archive_list)

        self.saved_archive_delete = QtWidgets.QPushButton(
            "Delete selected archived letter"
        )
        self.saved_archive_delete.setCursor(Qt.PointingHandCursor)
        self.saved_archive_delete.setStyleSheet(
            "QPushButton{background:#25191d;color:#ffb2b2;"
            "border:1px solid #65434a;border-radius:5px;padding:5px 10px;}"
            "QPushButton:hover{background:#702f38;color:#fff;}"
        )
        self.saved_archive_delete.clicked.connect(
            self._delete_selected_archived_letter
        )
        self.saved_archive_delete.hide()
        archive_layout.addWidget(self.saved_archive_delete)
        self.saved_archive.hide()
        saved_layout.addWidget(self.saved_archive)

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
        identity_holder = QtWidgets.QHBoxLayout()
        identity_holder.setContentsMargins(0, 0, 0, 0)
        identity_holder.addStretch(1)
        identity_holder.addWidget(self.identity_panel, 1)
        identity_holder.addStretch(1)
        self._main_layout.addLayout(identity_holder)

        self.preview_format_panel = QtWidgets.QFrame(self)
        self.preview_format_panel.setObjectName("ForgePreviewFormat")
        self.preview_format_panel.setFixedWidth(350)
        self.preview_format_panel.setStyleSheet(
            "QFrame#ForgePreviewFormat{background:rgba(13,31,40,.86);"
            "border:1px solid #315c69;border-radius:10px;}"
        )
        format_row = QtWidgets.QHBoxLayout(self.preview_format_panel)
        format_row.setContentsMargins(12, 7, 10, 7)
        format_row.setSpacing(12)
        self.preview_format_label = QtWidgets.QLabel("Preview format")
        self.preview_format_label.setStyleSheet(
            "color:#dffbff;font:600 10pt 'Segoe UI';"
        )
        format_row.addWidget(self.preview_format_label)
        self.preview_mode = _PreviewModeCombo()
        for label, mode in PREVIEW_MODES:
            self.preview_mode.addItem(label, mode)
            self.preview_mode.setItemData(
                self.preview_mode.count() - 1,
                PREVIEW_MODE_DESCRIPTIONS[mode],
                Qt.UserRole + 1,
            )
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
            "Open Letter", "#17232d", "#426070"
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
        self._layout_saved_cards()
        self._card_layout_timer.start(0)
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

    def _refresh_source_fingerprint(self) -> bool:
        current = _forge_source_fingerprint(self.project_root)
        if current == self._project_fingerprint:
            return False
        self._project_fingerprint = current
        self._source_revision += 1
        self._preview_refresh_pending = True
        return True

    def schedule_refresh(self) -> None:
        self._refresh_source_fingerprint()
        self._refresh_timer.start()

    def refresh_project_state(self) -> None:
        self._refresh_source_fingerprint()
        snapshot = self.settings.snapshot()
        preview_mode = self._saved_preview_mode()
        preview_mode_changed = preview_mode != self._preview_mode
        self._preview_mode = preview_mode
        if self.preview_mode.currentData() != preview_mode:
            blocker = QtCore.QSignalBlocker(self.preview_mode)
            self.preview_mode.setCurrentIndex(
                max(0, self.preview_mode.findData(preview_mode))
            )
            del blocker
        title = str(snapshot.get("recipient_title", "")).strip()
        recipient = str(snapshot.get("recipient_name", "")).strip()
        self.identity_title.setText(title or "Untitled")
        self.identity_title.setToolTip(title)
        self.identity_recipient.setText(recipient or "No recipient")
        self.identity_recipient.setToolTip(recipient)
        self.refresh_saved_page_url()
        self.refresh_readiness()
        if preview_mode_changed and not self._preview_refresh_pending:
            self.request_preview()

    def show_readiness_window(self) -> None:
        if self.readiness_window.isVisible():
            self._readiness_requested = False
            self.readiness_window.hide()
            return
        self._readiness_requested = True
        result = self.refresh_readiness()
        if result.completion_percentage >= 100:
            self._readiness_requested = False
            self.readiness_window.hide()
            self._set_status("Project readiness is complete.")
            return
        self.readiness_window.user_closed = False
        self.readiness_window.position_near_image_area()
        self.readiness_window.show()
        self.readiness_window.position_near_image_area()
        self.readiness_window.raise_()
        self.readiness_window.activateWindow()

    def attach_readiness_window(self, owner: QtWidgets.QWidget) -> None:
        self.readiness_window.attach_to(owner)

    def set_readiness_context_visible(self, visible: bool) -> None:
        if not visible:
            self.readiness_window.hide()
            return
        if self._readiness_requested and not self.readiness_window.user_closed:
            self.refresh_readiness()
            self.readiness_window.position_near_image_area()
            self.readiness_window.show()
            self.readiness_window.position_near_image_area()
            self.readiness_window.raise_()

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
        if result.completion_percentage >= 100:
            self._readiness_requested = False
            self.readiness_window.hide()
        self._sync_heading_balance()
        self.preview_btn.setEnabled(not self._busy and result.can_preview)
        self.publish_btn.setEnabled(not self._busy and result.can_publish)
        return result

    def show_saved_letters(self) -> None:
        if self._busy:
            return
        self._set_saved_delete_mode(False)
        self.refresh_saved_letters()
        owner = self.window()
        screen = owner.screen() or QtGui.QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry()
            if screen is not None
            else QtCore.QRect(0, 0, 1200, 800)
        )
        width = min(
            self.saved_panel.maximumWidth(),
            max(self.saved_panel.minimumWidth(), int(owner.width() * 0.72)),
            max(1, available.width() - 32),
        )
        height = min(
            self.saved_panel.maximumHeight(),
            max(self.saved_panel.minimumHeight(), int(owner.height() * 0.74)),
            max(1, available.height() - 32),
        )
        self.saved_panel.resize(width, height)
        center = owner.mapToGlobal(owner.rect().center())
        x = max(
            available.left() + 16,
            min(
                center.x() - (width // 2),
                available.right() - width - 15,
            ),
        )
        y = max(
            available.top() + 16,
            min(
                center.y() - (height // 2),
                available.bottom() - height - 15,
            ),
        )
        self.saved_panel.move(x, y)
        self.saved_panel.show()
        self.saved_panel.raise_()
        self.saved_panel.activateWindow()
        self.saved_scroll.setFocus(Qt.PopupFocusReason)
        QtCore.QTimer.singleShot(0, self._layout_saved_cards)

    def repair_duplicate_project_ids(self) -> tuple[tuple[Path, str], ...]:
        """Repair independent saved-letter copies while preserving the active path."""
        snapshot = self.settings.snapshot()
        context = self.project_paths.context_from_settings(snapshot)
        repaired = self.project_paths.repair_duplicate_project_ids(
            active_project_directory=context.project_directory,
        )
        if repaired:
            self.catalog = SavedLetterCatalog(self.project_root)
            self.refresh_saved_letters()
            self.refresh_project_state()
        return repaired

    def refresh_saved_letters(self) -> None:
        selected_path = (
            str(self._selected_saved_letter.path)
            if self._selected_saved_letter is not None
            else ""
        )
        horizontal = self.saved_scroll.horizontalScrollBar().value()
        vertical = self.saved_scroll.verticalScrollBar().value()
        entries = self.catalog.list_entries()
        recent_entries = entries[:RECENT_SAVED_LETTER_LIMIT]
        archived_entries = entries[RECENT_SAVED_LETTER_LIMIT:]
        for card in self._saved_cards:
            card.hide()
            self.saved_cards_layout.removeWidget(card)
            card.deleteLater()
        self._saved_cards = []
        self._selected_saved_letter = None

        for entry in recent_entries:
            card = SavedLetterCard(entry, self.saved_cards_widget)
            card.setEnabled(not self._busy)
            card.set_delete_mode(self._saved_delete_mode)
            card.selected.connect(self._select_saved_letter)
            card.activated.connect(self._activate_saved_letter)
            card.delete_requested.connect(self._delete_saved_letter)
            if str(entry.path) == selected_path:
                self._selected_saved_letter = entry
                card.set_selected(True)
            self._saved_cards.append(card)

        self._refresh_saved_archive(archived_entries, selected_path)
        self._layout_saved_cards()
        self._watch_saved_letter_paths(entries)
        self._pending_scroll_position = (horizontal, vertical)
        self._scroll_restore_timer.start(0)

    def reset_after_project_wipe(self) -> None:
        """Drop live references to project content removed by Command."""
        self.preview_files_release_requested.emit()
        self._last_play_dir = None
        self._selected_saved_letter = None
        self._project_fingerprint = _forge_source_fingerprint(self.project_root)
        self._source_revision += 1
        self._preview_refresh_pending = True
        self._preview_refresh_requested = False
        self.saved_page_url = ""
        self.saved_panel.hide()
        self._sync_published_url()
        self.refresh_saved_letters()
        self.refresh_readiness()
        self.preview_visibility_changed.emit(False)
        self._set_status("Ready.")

    def _set_saved_delete_mode(self, enabled: bool) -> None:
        self._saved_delete_mode = bool(enabled)
        self.saved_delete_toggle.setChecked(self._saved_delete_mode)
        if self._saved_delete_mode:
            tooltip = "Hide saved-letter delete controls"
            accessible = tooltip
        else:
            tooltip = "Show saved-letter delete controls"
            accessible = tooltip
        self.saved_delete_toggle.setToolTip(tooltip)
        self.saved_delete_toggle.setAccessibleName(accessible)
        for card in self._saved_cards:
            card.set_delete_mode(self._saved_delete_mode)
        self.saved_archive_delete.setVisible(
            self._saved_delete_mode
            and self.saved_archive_list.currentItem() is not None
        )

    def _refresh_saved_archive(
        self,
        entries: tuple[SavedLetter, ...],
        selected_path: str,
    ) -> None:
        selected_recipient = self.saved_archive_recipient.currentText()
        groups: dict[str, list[SavedLetter]] = {}
        display_names: dict[str, str] = {}
        selected_group = ""
        for entry in entries:
            recipient = " ".join(entry.recipient.split()) or "Unknown recipient"
            key = recipient.casefold()
            groups.setdefault(key, []).append(entry)
            display_names.setdefault(key, recipient)
            if str(entry.path) == selected_path:
                selected_group = key

        self._archive_groups = {
            key: tuple(group)
            for key, group in groups.items()
        }
        self.saved_archive_recipient.blockSignals(True)
        self.saved_archive_recipient.clear()
        self.saved_archive_recipient.addItem("Choose recipient…", "")
        for key in groups:
            self.saved_archive_recipient.addItem(display_names[key], key)
        target_key = selected_group
        if not target_key and selected_recipient:
            previous = self.saved_archive_recipient.findText(
                selected_recipient,
                Qt.MatchFixedString,
            )
            if previous >= 0:
                target_key = str(
                    self.saved_archive_recipient.itemData(previous) or ""
                )
        target_index = self.saved_archive_recipient.findData(target_key)
        self.saved_archive_recipient.setCurrentIndex(max(0, target_index))
        self.saved_archive_recipient.blockSignals(False)
        self.saved_archive_label.setText(f"Archive ({len(entries)})")
        self.saved_archive.setVisible(bool(entries))
        self._show_archived_recipient(
            self.saved_archive_recipient.currentIndex(),
            selected_path=selected_path,
        )

    def _show_archived_recipient(
        self,
        index: int,
        *,
        selected_path: str = "",
    ) -> None:
        key = str(self.saved_archive_recipient.itemData(index) or "")
        self._archived_entries = list(self._archive_groups.get(key, ()))
        self.saved_archive_list.clear()
        selected_row = -1
        for row, entry in enumerate(self._archived_entries):
            item = QtWidgets.QListWidgetItem(entry.title)
            item.setToolTip(
                f"{entry.title}\n{entry.path}\nDouble-click or press Enter to load."
            )
            item.setSizeHint(QtCore.QSize(0, 56))
            if entry.cover_path is not None:
                cover = QtGui.QPixmap(str(entry.cover_path))
                if not cover.isNull():
                    item.setIcon(
                        QtGui.QIcon(
                            cover.scaled(
                                38,
                                48,
                                Qt.KeepAspectRatio,
                                Qt.SmoothTransformation,
                            )
                        )
                    )
            self.saved_archive_list.addItem(item)
            if str(entry.path) == selected_path:
                selected_row = row
                self._selected_saved_letter = entry
        self.saved_archive_list.setVisible(bool(self._archived_entries))
        if selected_row >= 0:
            self.saved_archive_list.setCurrentRow(selected_row)
        self.saved_archive_delete.setVisible(
            self._saved_delete_mode and selected_row >= 0
        )

    def _selected_archived_entry(self) -> Optional[SavedLetter]:
        row = self.saved_archive_list.currentRow()
        if 0 <= row < len(self._archived_entries):
            return self._archived_entries[row]
        return None

    def _select_archived_letter(self) -> None:
        entry = self._selected_archived_entry()
        if entry is None:
            self.saved_archive_delete.hide()
            return
        self._select_saved_letter(entry)
        self.saved_archive_delete.setVisible(self._saved_delete_mode)

    def _activate_archived_letter(
        self,
        _item: QtWidgets.QListWidgetItem,
    ) -> None:
        entry = self._selected_archived_entry()
        if entry is not None:
            self._activate_saved_letter(entry)

    def _delete_selected_archived_letter(self) -> None:
        entry = self._selected_archived_entry()
        if entry is not None:
            self._delete_saved_letter(entry)

    def load_selected_letter(self) -> None:
        entry = self._selected_saved_letter
        if not isinstance(entry, SavedLetter) or self._busy:
            return
        self.saved_panel.hide()
        if entry.needs_recipient_assignment:
            self._pending_recipient_entry = entry
            self.project_state.transition(
                ApplicationState.PROJECT_MIGRATING
            )
            self.project_state.transition(
                ApplicationState.RECIPIENT_REQUIRED
            )
            self._set_status(
                "Choose a recipient to finish loading this saved letter."
            )
            return

        previous_identity = self.project_state.identity
        activity = "Loading saved letter…"
        self._begin_restore_activity(activity)
        self._release_project_files_for_restore()
        self.project_state.transition(
            ApplicationState.PROJECT_LOADING
        )

        def task() -> RestoredProject:
            return self.restorer.restore(entry)

        self._start_restore_operation_deferred(
            activity,
            task,
            self._complete_restore,
            "The selected saved letter could not be restored.",
            on_failure=lambda: self._restore_loading_state(
                previous_identity
            ),
        )

    def has_pending_recipient_assignment(self) -> bool:
        return self._pending_recipient_entry is not None

    def assign_pending_recipient(
        self,
        recipient: str,
        *,
        custom_capitalization: bool = False,
    ) -> bool:
        entry = self._pending_recipient_entry
        if entry is None or self._busy:
            return False
        if self.project_state.state is ApplicationState.RECIPIENT_REQUIRED:
            self.project_state.transition(
                ApplicationState.PROJECT_MIGRATING
            )
        activity = "Assigning recipient and loading saved letter…"
        self._begin_restore_activity(activity)
        self._release_project_files_for_restore()
        self.project_state.transition(
            ApplicationState.PROJECT_LOADING
        )

        def task() -> RestoredProject:
            identified = self.restorer.assign_recipient(
                entry,
                recipient,
                custom_capitalization=custom_capitalization,
            )
            return self.restorer.restore(identified)

        self._start_restore_operation_deferred(
            activity,
            task,
            self._complete_restore,
            "The saved letter could not be assigned to that recipient.",
            on_failure=self._restore_recipient_requirement,
        )
        return True

    def _select_saved_letter(self, entry: object) -> None:
        if not isinstance(entry, SavedLetter):
            return
        self._selected_saved_letter = entry
        selected_path = entry.path
        for card in self._saved_cards:
            card.set_selected(card.entry.path == selected_path)

    def _activate_saved_letter(self, entry: object) -> None:
        self._select_saved_letter(entry)
        self.load_selected_letter()

    def _delete_saved_letter(self, entry: object) -> None:
        if not isinstance(entry, SavedLetter) or self._busy:
            return
        answer = QtWidgets.QMessageBox.question(
            self.saved_panel,
            "Delete Saved Letter",
            (
                f'Delete "{entry.title}" for {entry.recipient}?\n\n'
                "This cannot be undone."
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            deleted = self.catalog.delete(entry)
        except SavedLetterDeleteError as error:
            self._set_status(str(error), error=True)
            return
        if (
            self._last_play_dir is not None
            and self._last_play_dir.resolve() == deleted
        ):
            self._last_play_dir = None
        active_value = self.settings.get(ACTIVE_PLAY_DIR_KEY, "")
        try:
            active_path = Path(str(active_value)).resolve() if active_value else None
        except (OSError, RuntimeError, TypeError, ValueError):
            active_path = None
        if active_path == deleted:
            try:
                self.settings.update_fields({ACTIVE_PLAY_DIR_KEY: ""})
            except Exception:
                _LOGGER.exception(
                    "Could not clear the deleted active letter path: %s",
                    deleted,
                )
        if (
            self._selected_saved_letter is not None
            and self._selected_saved_letter.path == entry.path
        ):
            self._selected_saved_letter = None
        self._set_saved_delete_mode(False)
        self.refresh_saved_letters()
        self._set_status("Saved letter deleted.")

    def _layout_saved_cards(self) -> None:
        if not hasattr(self, "saved_cards_layout"):
            return
        while self.saved_cards_layout.count():
            item = self.saved_cards_layout.takeAt(0)
            widget = item.widget()
            if (
                widget is not None
                and not isinstance(widget, SavedLetterCard)
            ):
                widget.deleteLater()
        if not self._saved_cards:
            self.saved_cards_widget.setMinimumHeight(180)
            empty = QtWidgets.QLabel(
                "No saved letters yet. Preview a letter to create one."
            )
            empty.setObjectName("SavedLettersEmpty")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                "color:#8097a1;padding:42px 12px;background:transparent;"
            )
            self.saved_cards_layout.addWidget(empty, 0, 0)
            return

        available = max(184, self.saved_scroll.viewport().width() - 8)
        columns = max(1, available // 192)
        rows = (len(self._saved_cards) + columns - 1) // columns
        self.saved_cards_widget.setMinimumHeight((rows * 222) + 4)
        for index, card in enumerate(self._saved_cards):
            self.saved_cards_layout.addWidget(
                card,
                index // columns,
                index % columns,
                Qt.AlignTop,
            )

    def _watch_saved_letter_paths(
        self,
        _entries: tuple[SavedLetter, ...],
    ) -> None:
        watched = (
            self._catalog_watcher.directories()
            + self._catalog_watcher.files()
        )
        if watched:
            self._catalog_watcher.removePaths(watched)
        candidates = {
            self.project_root / "output",
            self.catalog.play_root,
            self.catalog.recovery_root,
        }
        # Watching build folders themselves can prevent transactional directory
        # replacement on Windows. Root watches plus explicit operation signals
        # keep the catalog current without holding saved-letter directories.
        paths = [
            str(path.resolve())
            for path in candidates
            if path.exists() and not path.is_symlink()
        ]
        if paths:
            self._catalog_watcher.addPaths(sorted(set(paths)))

    @QtCore.Slot(str)
    def _catalog_path_changed(self, _path: str) -> None:
        self._catalog_refresh_timer.start()

    def _restore_saved_scroll_position(self) -> None:
        horizontal, vertical = self._pending_scroll_position
        self.saved_scroll.horizontalScrollBar().setValue(horizontal)
        self.saved_scroll.verticalScrollBar().setValue(vertical)

    def _load_saved_letter(self, entry: SavedLetter) -> None:
        """Synchronous compatibility path used by focused service tests."""
        previous_identity = self.project_state.identity
        if entry.needs_recipient_assignment:
            self._pending_recipient_entry = entry
            self.project_state.transition(
                ApplicationState.PROJECT_MIGRATING
            )
            self.project_state.transition(
                ApplicationState.RECIPIENT_REQUIRED
            )
            return
        self._begin_restore_activity("Loading saved letter…")
        self._release_project_files_for_restore()
        self.project_state.transition(
            ApplicationState.PROJECT_LOADING
        )
        try:
            restored = self.restorer.restore(entry)
        except Exception:
            _LOGGER.exception("Saved-letter restore failed.")
            self._restore_loading_state(previous_identity)
            self._set_status(
                "The selected saved letter could not be restored.",
                error=True,
            )
            return
        finally:
            self._finish_restore_activity()
        self._complete_restore(restored)

    def _release_project_files_for_restore(self) -> None:
        """Release live viewers and media before replacing project folders."""
        self.preview_files_release_requested.emit()
        self.project_files_release_requested.emit()

    def _begin_restore_activity(self, activity: str) -> None:
        self._restore_operation_active = True
        self.restore_activity_changed.emit(True, activity)

    def _finish_restore_activity(self) -> None:
        if not self._restore_operation_active:
            return
        self._restore_operation_active = False
        self.restore_activity_changed.emit(False, "")

    def _start_restore_operation(
        self,
        activity: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
        error_message: str,
        *,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        if not self._restore_operation_active:
            self._begin_restore_activity(activity)
        try:
            self._start_operation(
                activity,
                task,
                on_success,
                error_message,
                on_failure=on_failure,
            )
        except Exception:
            self._finish_restore_activity()
            raise

    def _start_restore_operation_deferred(
        self,
        activity: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
        error_message: str,
        *,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        """Start after GUI media/source-detach events have been processed."""
        QtCore.QTimer.singleShot(
            0,
            lambda: self._start_restore_operation(
                activity,
                task,
                on_success,
                error_message,
                on_failure=on_failure,
            ),
        )

    def _complete_restore(self, restored: object) -> None:
        if not isinstance(restored, RestoredProject):
            self._set_status(
                "The selected saved letter could not be restored.",
                error=True,
            )
            return
        self._last_play_dir = Path(restored.play_dir).resolve()
        self._record_active_play_dir(self._last_play_dir)
        self.project_state.transition(
            ApplicationState.PROJECT_READY,
            identity=restored.identity,
        )
        self._pending_recipient_entry = None
        self.refresh_project_state()
        self._preview_refresh_pending = True
        self.refresh_saved_letters()
        payload = restored.as_payload()
        self.project_restored.emit(payload)
        self.letter_loaded.emit(payload)
        self._set_status("Saved letter loaded.")
        self.ensure_preview_current()

    def _restore_loading_state(
        self,
        previous_identity: ProjectIdentity,
    ) -> None:
        if previous_identity.is_valid:
            self.project_state.transition(
                ApplicationState.PROJECT_READY,
                identity=previous_identity,
            )
        else:
            self.project_state.transition(
                ApplicationState.RECIPIENT_REQUIRED
            )

    def _restore_recipient_requirement(self) -> None:
        if self.project_state.state is not ApplicationState.RECIPIENT_REQUIRED:
            self.project_state.transition(
                ApplicationState.RECIPIENT_REQUIRED
            )

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
        try:
            index = generate.play_bundle_directory(self.project_root) / "index.html"
        except Exception:
            _LOGGER.exception("The current Forge play directory could not be resolved.")
            return None
        return index if index.is_file() else None

    def current_play_index(self) -> Optional[Path]:
        """Return the current playable viewer entry point, when available."""
        if self._preview_refresh_pending:
            return None
        return self._current_play_index()

    @property
    def preview_mode_value(self) -> str:
        return self._preview_mode

    @property
    def preview_refresh_pending(self) -> bool:
        return self._preview_refresh_pending

    def request_preview(self) -> None:
        index = self.current_play_index()
        if index is not None:
            self.preview_requested.emit(str(index.resolve()), self._preview_mode)

    def _required_gate(
        self,
        *,
        for_publish: bool = False,
    ) -> Optional[ReadinessResult]:
        readiness = self.refresh_readiness()
        allowed = (
            readiness.can_publish
            if for_publish
            else readiness.can_preview
        )
        if allowed:
            return readiness
        missing = [
            item for item in readiness.missing_items if item.required
        ]
        if missing:
            self._set_status(f"{missing[0].label} is required.", error=True)
        return None

    def preview_letter(self) -> None:
        self._prepare_preview(open_in_browser=True)

    def ensure_preview_current(self) -> None:
        """Refresh an existing embedded preview before it is displayed."""
        if self._busy:
            self._preview_refresh_pending = True
            self._preview_refresh_requested = True
            return
        if self._current_play_index() is None:
            return
        self._prepare_preview(open_in_browser=False)

    def _prepare_preview(self, *, open_in_browser: bool) -> None:
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

        source_revision = self._source_revision
        requested_fingerprint = self._project_fingerprint

        def task() -> tuple[Path, bool, ReadinessResult, int, str, str]:
            try:
                play_dir, rebuilt = generate.ensure_play_bundle(
                    self.project_root,
                    message_html=message,
                    force=False,
                )
            except generate.FontExportError as error:
                raise _ForgeOperationError(str(error)) from error
            except PermissionError as error:
                raise _ForgeOperationError(
                    "The previous preview is still in use. Close any open "
                    "letter preview and try again."
                ) from error
            return (
                Path(play_dir).resolve(),
                rebuilt,
                readiness,
                source_revision,
                requested_fingerprint,
                _forge_source_fingerprint(self.project_root),
            )

        self._preview_refresh_pending = True
        self.preview_files_release_requested.emit()
        self._start_operation(
            "Preparing preview…",
            task,
            (
                self._preview_completed
                if open_in_browser
                else self._embedded_preview_completed
            ),
            "Preview could not be updated. The previous preview was preserved.",
        )

    def _finish_preview(
        self,
        result: object,
        *,
        record_activity: bool,
    ) -> tuple[Path, Path]:
        values = tuple(result)
        play_dir, _rebuilt, readiness = values[:3]
        source_changed = False
        if len(values) >= 6:
            source_revision = int(values[3])
            requested_fingerprint = str(values[4])
            completed_fingerprint = str(values[5])
            self._project_fingerprint = completed_fingerprint
            source_changed = (
                self._source_revision != source_revision
                or completed_fingerprint != requested_fingerprint
            )
        self._last_play_dir = Path(play_dir)
        self._record_active_play_dir(self._last_play_dir)
        index = self._last_play_dir / "index.html"
        self._preview_refresh_pending = source_changed
        if source_changed:
            self._preview_refresh_requested = True
        else:
            self.request_preview()
        self._pending_metadata_update = (
            Path(play_dir),
            readiness,
            record_activity,
        )
        self._metadata_timer.start(0)
        return self._last_play_dir, index

    def _preview_completed(self, result: object) -> None:
        _play_dir, index = self._finish_preview(
            result,
            record_activity=True,
        )
        opened = index.is_file() and QtGui.QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(index.resolve()))
        )
        if opened:
            self._set_status("Preview opened in your browser.")
        else:
            self._set_status(
                "The local preview was built, but the browser could not open it.",
                error=True,
            )

    def _embedded_preview_completed(self, result: object) -> None:
        _play_dir, index = self._finish_preview(
            result,
            record_activity=False,
        )
        if index.is_file():
            self._set_status("Preview updated.")

    def _run_pending_metadata_update(self) -> None:
        pending = self._pending_metadata_update
        self._pending_metadata_update = None
        if pending is not None:
            play_dir, readiness, record_activity = pending
            self._update_metadata_silently(
                play_dir,
                readiness,
                record_activity=record_activity,
            )

    def publish_letter(self) -> None:
        if self._busy:
            return
        readiness = self._required_gate(for_publish=True)
        if readiness is None:
            return
        if not bool(self.settings.get(PUBLIC_WARNING_KEY, False)):
            answer = QtWidgets.QMessageBox.question(
                self,
                "Publish Letter",
                "Publishing may create or update a public GitHub Pages "
                "repository using GitHub CLI. Continue?",
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

        source_revision = self._source_revision
        requested_fingerprint = self._project_fingerprint

        def task() -> tuple[Path, ReadinessResult, dict, object, int, str, str]:
            try:
                play_dir, _rebuilt = generate.ensure_play_bundle(
                    self.project_root,
                    message_html=message,
                    force=True,
                )
            except generate.FontExportError as error:
                raise _ForgeOperationError(str(error)) from error
            except PermissionError as error:
                raise _ForgeOperationError(
                    "The previous preview is still in use. Close any open "
                    "letter preview and try publishing again."
                ) from error
            play_path = Path(play_dir).resolve()
            metadata = update_saved_metadata(
                play_path,
                self.project_root,
                readiness,
            )
            record_saved_letter_activity(play_path)
            publisher = GitHubPagesPublisher(self.project_root)
            if not publisher.is_configured():
                configured = publisher.configure(None)
                if not configured.configured:
                    configuration_message = (
                        "Publishing requires GitHub CLI or a configured Git "
                        "remote. The local letter was generated successfully."
                    )
                    if configured.message:
                        configuration_message = (
                            f"{configured.message} "
                            "The local letter was generated successfully."
                        )
                    return (
                        play_path,
                        readiness,
                        metadata,
                        PublishResult(
                            False,
                            message=configuration_message,
                        ),
                        source_revision,
                        requested_fingerprint,
                        _forge_source_fingerprint(self.project_root),
                    )
            publish_result = publisher.publish(play_path, metadata)
            return (
                play_path,
                readiness,
                metadata,
                publish_result,
                source_revision,
                requested_fingerprint,
                _forge_source_fingerprint(self.project_root),
            )

        self.preview_files_release_requested.emit()
        self._start_operation(
            "Publishing letter…",
            task,
            self._publish_completed,
            "Publishing failed. The local build was preserved.",
        )

    def _publish_completed(self, result: object) -> None:
        values = tuple(result)
        play_dir, _readiness, _metadata, publish_result = values[:4]
        source_changed = False
        if len(values) >= 7:
            source_revision = int(values[4])
            requested_fingerprint = str(values[5])
            completed_fingerprint = str(values[6])
            self._project_fingerprint = completed_fingerprint
            source_changed = (
                self._source_revision != source_revision
                or completed_fingerprint != requested_fingerprint
            )
        self._last_play_dir = Path(play_dir)
        self._record_active_play_dir(self._last_play_dir)
        self._preview_refresh_pending = source_changed
        if source_changed:
            self._preview_refresh_requested = True
        else:
            self.request_preview()
        self.refresh_saved_letters()
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

    def _record_active_play_dir(self, play_dir: Path) -> None:
        candidate = Path(play_dir).resolve()
        if not candidate.is_dir() or not (candidate / "index.html").is_file():
            return
        try:
            self.settings.update_fields(
                {ACTIVE_PLAY_DIR_KEY: str(candidate)}
            )
        except Exception:
            _LOGGER.exception(
                "Could not persist the active generated letter path: %s",
                candidate,
            )

    def _update_metadata_silently(
        self,
        play_dir: Path,
        readiness: ReadinessResult,
        *,
        public_path: str = "",
        record_activity: bool = False,
    ) -> None:
        try:
            update_saved_metadata(
                play_dir,
                self.project_root,
                readiness,
                public_path=public_path,
            )
            if record_activity:
                record_saved_letter_activity(play_dir)
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
        return "" if self._published_url_unavailable() else self.saved_page_url

    def set_saved_page_url(self, url: str) -> None:
        previous_url = self.saved_page_url
        self.saved_page_url = normalize_published_page_url(url)
        self._sync_published_url()
        self.refresh_readiness()
        if self.saved_page_url and self.saved_page_url != previous_url:
            self._record_current_letter_activity()

    def _record_current_letter_activity(self) -> None:
        index = self._current_play_index()
        if index is None:
            return
        self._update_metadata_silently(
            index.parent,
            self._readiness_result,
            record_activity=True,
        )

    def _sync_published_url(self) -> None:
        expired = self._published_url_unavailable()
        available = bool(self.saved_page_url) and not expired
        self.open_published_btn.setEnabled(available and not self._busy)
        if available:
            label = publication_expiry_label(
                self.settings.get(PUBLISHED_EXPIRES_AT_KEY, "")
            )
            self.open_published_btn.setToolTip(
                f"{self.saved_page_url}\n{label}" if label else self.saved_page_url
            )
        elif expired:
            self.open_published_btn.setToolTip(
                "This published link is expired or malformed. Publish again."
            )
        else:
            disabled = "Save a valid HTTP or HTTPS published URL in Message."
            self.open_published_btn.setToolTip(disabled)

    def _published_url_unavailable(self) -> bool:
        expiry = self.settings.get(PUBLISHED_EXPIRES_AT_KEY, "")
        return is_publication_expired(expiry) or is_publication_expiration_malformed(expiry)

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
        *,
        on_failure: Callable[[], None] | None = None,
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
        self._operation_success = on_success
        self._operation_failure = on_failure
        self._operation_error_message = error_message
        thread.started.connect(worker.run)

        worker.succeeded.connect(
            self._operation_succeeded_on_ui,
            Qt.QueuedConnection,
        )
        worker.failed.connect(
            self._operation_failed_on_ui,
            Qt.QueuedConnection,
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(
            self._operation_finished,
            Qt.QueuedConnection,
        )
        thread.start()

    @QtCore.Slot(object)
    def _operation_succeeded_on_ui(self, result: object) -> None:
        callback = self._operation_success
        self._operation_success = None
        self._operation_failure = None
        if callback is None:
            return
        try:
            callback(result)
        except Exception:
            _LOGGER.exception("Forge completion handling failed.")
            self._set_status(
                self._operation_error_message
                or "The Forge operation could not be completed.",
                error=True,
            )

    @QtCore.Slot(str, str, bool)
    def _operation_failed_on_ui(
        self,
        message: str,
        technical: str,
        user_safe: bool,
    ) -> None:
        self._operation_success = None
        failure_callback = self._operation_failure
        self._operation_failure = None
        if failure_callback is not None:
            try:
                failure_callback()
            except Exception:
                _LOGGER.exception(
                    "Forge failure-state recovery failed."
                )
        _LOGGER.error(
            "Forge operation failed: %s\n%s",
            message,
            technical,
        )
        error_message = self._operation_error_message
        safe_message = (
            message
            if user_safe
            and isinstance(message, str)
            and message
            and len(message) <= 240
            else error_message
        )
        self._set_status(
            safe_message or "The Forge operation could not be completed.",
            error=True,
        )
        self.request_preview()

    def _operation_finished(self) -> None:
        thread = self._worker_thread
        self._worker = None
        self._worker_thread = None
        self._operation_error_message = ""
        self._operation_failure = None
        self._busy = False
        self._set_busy(False)
        self._finish_restore_activity()
        if thread is not None:
            thread.deleteLater()
        if self._preview_refresh_requested:
            self._preview_refresh_requested = False
            QtCore.QTimer.singleShot(0, self.ensure_preview_current)

    def _set_busy(self, busy: bool) -> None:
        for card in self._saved_cards:
            card.setEnabled(not busy)
        self.saved_archive_recipient.setEnabled(not busy)
        self.saved_archive_list.setEnabled(not busy)
        self.saved_archive_delete.setEnabled(not busy)
        self.load_saved_btn.setEnabled(not busy)
        self.saved_delete_toggle.setEnabled(not busy)
        self.preview_mode.setEnabled(not busy)
        self.readiness_btn.setEnabled(not busy)
        if busy:
            self.preview_btn.setEnabled(False)
            self.publish_btn.setEnabled(False)
            self.open_published_btn.setEnabled(False)
        else:
            self.refresh_readiness()
            self._sync_published_url()

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

    def activate_for_tab_change(self) -> None:
        if self._tab_active:
            return
        self._tab_active = True
        self.refresh_project_state()
        self.refresh_saved_letters()
        self.ensure_preview_current()
        self.preview_visibility_changed.emit(True)

    def deactivate_for_tab_change(self) -> None:
        if not self._tab_active:
            return
        self._tab_active = False
        self.saved_panel.hide()
        self.preview_visibility_changed.emit(False)
        self.preview_files_release_requested.emit()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.activate_for_tab_change()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.deactivate_for_tab_change()
        super().hideEvent(event)

    def shutdown_operations(self, timeout_ms: int = 5000) -> bool:
        thread = self._worker_thread
        if thread is None or not thread.isRunning():
            return True
        thread.requestInterruption()
        thread.quit()
        stopped = thread.wait(timeout_ms)
        if stopped:
            self._worker = None
            self._worker_thread = None
            self._operation_success = None
            self._operation_failure = None
            self._operation_error_message = ""
            self._busy = False
            self._finish_restore_activity()
        return stopped

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self.shutdown_operations():
            event.ignore()
            self._set_status(
                "Finish the current Forge operation before closing.",
                error=True,
                timeout_ms=0,
            )
            return
        self.settings.changed.disconnect(self._on_settings_changed)
        self.saved_panel.close()
        self.readiness_window.shutdown()
        super().closeEvent(event)
