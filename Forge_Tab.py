from __future__ import annotations

import logging
import traceback
import weakref
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Callable, Optional
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

import generate
from curtain_controls import CurtainStyleComboBox, CurtainStyleController
from image_button import ArtworkButton
from config import MESSAGE_HTML_FILE, ensure_output_dirs
from message_html import read_text_normalized
from publishing import GitHubPagesPublisher, PublishResult
from publishing.expiration import (
    PUBLISHED_EXPIRES_AT_KEY,
    is_publication_expiration_malformed,
    is_publication_expired,
    publication_expiry_label,
    publication_status,
)
from publishing.github_auth import (
    GitHubAccount,
    GitHubConnectionService,
    GitHubConnectionSnapshot,
    GitHubConnectionState,
    GitHubOperationError,
    GitHubPublishingAccess,
    GitHubSession,
    github_connection_service,
)
from publishing.github_config import github_application_configuration
from publishing.github_pages import PUBLIC_WARNING_KEY
from publishing.github_ui import GitHubAccountDialog, GitHubAuthenticationDialog
from readiness import ReadinessResult, evaluate_readiness
from project_paths import ProjectPathResolver, application_paths
from project_timestamps import PROJECT_PUBLISHED_AT_KEY
from protected_projects import (
    PROTECTED_PROJECT_KIND_KEY,
    is_protected_project as settings_is_protected_project,
)
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
    save_published_snapshot,
    update_saved_metadata,
    update_saved_publication_metadata,
)
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
    CURTAIN_STYLE_LABELS,
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    SettingsStore,
    normalize_published_page_url,
)
from ui_dialogs import LetterSmithConfirmationDialog, LetterSmithMessageDialog
from ui_help import set_control_help
from ui_sounds import UiSound, play_ui_sound
from ui_theme import (
    CYBER_FORGE_THEME,
    BUTTON_FULL_TIER_GEOMETRY_PROPERTY,
    BUTTON_GEOMETRY_SCALE_PROPERTY,
    ButtonTier,
    ThemeService,
    ThemeTokens,
    apply_button_tier,
    apply_tab_heading_style,
    minimum_button_text_size,
)
from transactional_io import file_change_token


PREVIEW_MODE_KEY = "forge_preview_mode"
FORGE_ACTION_FONT_POINT_SIZE = 13.0
FORGE_GEOMETRY_SCALE = 0.9
PREVIEW_MODES = (
    ("Portrait", "portrait"),
    ("Landscape", "landscape"),
    ("Browser", "window"),
)
PREVIEW_MODE_DESCRIPTIONS = {
    "portrait": " ",
    "landscape": " ",
    "window": "",
}
_GITHUB_WARNING_BACKGROUND = "#0d1117"
_GITHUB_WARNING_BORDER = "#58a6ff"
_GITHUB_WARNING_TEXT = "#f0f6fc"
RECENT_SAVED_LETTER_LIMIT = 15
_CATALOG_INTERNAL_CHANGE_GRACE_SECONDS = 1.0
_FORGE_RELEVANT_SETTING_KEYS = frozenset(
    {
        "recipient_id",
        "recipient_name",
        "recipient_title",
        "project_id",
        "starting_volume",
        "music_volume",
        "music_required",
        "curtain_style",
        "message_overlay_preset",
        "message_overlay_opacity",
        "required_features",
        PREVIEW_MODE_KEY,
        PUBLISHED_PAGE_URL_KEY,
        PUBLISHED_PUBLIC_PATH_KEY,
        PUBLISHED_AT_KEY,
        PUBLISHED_EXPIRES_AT_KEY,
        PUBLICATION_PROVIDER_KEY,
        PUBLICATION_VERIFIED_KEY,
        PUBLISHED_SOURCE_FINGERPRINT_KEY,
        PUBLISHED_GITHUB_OWNER_KEY,
        PUBLISHED_GITHUB_REPOSITORY_KEY,
        PROTECTED_PROJECT_KIND_KEY,
    }
)
_LOGGER = logging.getLogger(__name__)
_COVER_PIXMAP_CACHE_LIMIT = 64
_MAX_GITHUB_OPERATION_RETRIES = 3
_COVER_PIXMAP_CACHE: OrderedDict[
    tuple[str, int, int, int, int, int],
    QtGui.QPixmap,
] = OrderedDict()


def _cover_cache_identity(
    path: Path | None,
    width: int,
    height: int,
) -> tuple[Path, tuple[str, int, int, int, int, int]] | None:
    if path is None:
        return None
    try:
        resolved = path.resolve()
        stat_result = resolved.stat()
    except OSError:
        return None
    return (
        resolved,
        (
            str(resolved).casefold(),
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            file_change_token(resolved, stat_result=stat_result),
            int(width),
            int(height),
        ),
    )


def _cached_cover_pixmap(
    key: tuple[str, int, int, int, int, int],
) -> QtGui.QPixmap | None:
    cached = _COVER_PIXMAP_CACHE.get(key)
    if cached is None:
        return None
    _COVER_PIXMAP_CACHE.move_to_end(key)
    return QtGui.QPixmap(cached)


def _cache_cover_image(
    key: tuple[str, int, int, int, int, int],
    image: QtGui.QImage,
) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap.fromImage(image)
    _COVER_PIXMAP_CACHE[key] = QtGui.QPixmap(pixmap)
    _COVER_PIXMAP_CACHE.move_to_end(key)
    while len(_COVER_PIXMAP_CACHE) > _COVER_PIXMAP_CACHE_LIMIT:
        _COVER_PIXMAP_CACHE.popitem(last=False)
    return pixmap


def _decode_scaled_cover_image(
    path: Path,
    width: int,
    height: int,
) -> QtGui.QImage:
    reader = QtGui.QImageReader(str(path))
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid() and not source_size.isEmpty():
        source_size.scale(width, height, Qt.KeepAspectRatio)
        reader.setScaledSize(source_size)
    image = reader.read()
    if not image.isNull() and (
        image.width() > width or image.height() > height
    ):
        image = image.scaled(
            width,
            height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
    return image


def _scaled_cover_pixmap(
    path: Path | None,
    width: int,
    height: int,
) -> QtGui.QPixmap:
    identity = _cover_cache_identity(path, width, height)
    if identity is None:
        return QtGui.QPixmap()
    resolved, key = identity
    cached = _cached_cover_pixmap(key)
    if cached is not None:
        return cached
    return _cache_cover_image(
        key,
        _decode_scaled_cover_image(resolved, width, height),
    )


class _CoverDecodeTask(QtCore.QRunnable):
    """Decode one scaled saved-letter cover without blocking the GUI thread."""

    def __init__(
        self,
        completed: object,
        generation: int,
        key: tuple[str, int, int, int, int, int],
        path: Path,
        width: int,
        height: int,
    ) -> None:
        super().__init__()
        self._completed = completed
        self._generation = int(generation)
        self._key = key
        self._path = path
        self._width = int(width)
        self._height = int(height)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            image = _decode_scaled_cover_image(
                self._path,
                self._width,
                self._height,
            )
        except Exception:
            image = QtGui.QImage()
        self._completed.emit(self._generation, self._key, image)


class _CatalogReconcileTask(QtCore.QRunnable):
    """Perform a full saved-letter reconciliation away from the GUI thread."""

    def __init__(
        self,
        completed: object,
        generation: int,
        mode: str,
        project_root: Path,
        stock_only: bool,
    ) -> None:
        super().__init__()
        self._completed = completed
        self._generation = int(generation)
        self._mode = str(mode)
        self._project_root = project_root
        self._stock_only = bool(stock_only)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            entries = SavedLetterCatalog(
                self._project_root,
                stock_only=self._stock_only,
            ).list_entries(force_refresh=True)
            error = ""
        except Exception:
            entries = None
            error = traceback.format_exc()
        self._completed.emit(
            self._generation,
            self._mode,
            entries,
            error,
        )


def _color_name(value: str) -> str:
    return QtGui.QColor(value).name()


def _darker(value: str, factor: int) -> str:
    return QtGui.QColor(value).darker(factor).name()


def _lighter(value: str, factor: int) -> str:
    return QtGui.QColor(value).lighter(factor).name()


def _rgba(value: str, alpha: int) -> str:
    color = QtGui.QColor(value)
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


def _relative_luminance(value: str) -> float:
    color = QtGui.QColor(value)

    def linear(channel: int) -> float:
        normalized = channel / 255.0
        return (
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )

    return (
        0.2126 * linear(color.red())
        + 0.7152 * linear(color.green())
        + 0.0722 * linear(color.blue())
    )


def _contrast_ratio(foreground: str, background: str) -> float:
    foreground_luminance = _relative_luminance(foreground)
    background_luminance = _relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _readable_text(backgrounds: tuple[str, ...], preferred: str) -> str:
    preferred_contrast = min(
        _contrast_ratio(preferred, background)
        for background in backgrounds
    )
    if preferred_contrast >= 4.5:
        return preferred
    candidates = ("#fffefc", "#080a0d")
    return max(
        candidates,
        key=lambda candidate: min(
            _contrast_ratio(candidate, background)
            for background in backgrounds
        ),
    )


def _forge_source_fingerprint(project_root: Path) -> str:
    try:
        return str(generate.build_source_fingerprint(project_root))
    except Exception:
        _LOGGER.exception("Forge source fingerprint could not be calculated.")
        return ""


def _publication_fields_from_result(publish_result: PublishResult) -> dict:
    publication = {
        PUBLISHED_PAGE_URL_KEY: normalize_published_page_url(
            getattr(publish_result, "url", "")
        ),
        PUBLISHED_PUBLIC_PATH_KEY: str(
            getattr(publish_result, "public_path", "")
        ).strip(),
        PUBLISHED_AT_KEY: str(
            getattr(publish_result, "published_at", "")
        ).strip(),
        PUBLISHED_EXPIRES_AT_KEY: str(
            getattr(publish_result, "expires_at", "")
        ).strip(),
        PUBLICATION_PROVIDER_KEY: str(
            getattr(publish_result, "provider", "")
        ).strip(),
        PUBLICATION_VERIFIED_KEY: (
            getattr(publish_result, "verified", False) is True
        ),
        PUBLISHED_SOURCE_FINGERPRINT_KEY: str(
            getattr(publish_result, "source_fingerprint", "")
        ).strip(),
        PUBLISHED_GITHUB_OWNER_KEY: str(
            getattr(publish_result, "owner", "")
        ).strip(),
        PUBLISHED_GITHUB_REPOSITORY_KEY: str(
            getattr(publish_result, "repository", "")
        ).strip(),
    }
    publication[PROJECT_PUBLISHED_AT_KEY] = publication[PUBLISHED_AT_KEY]
    return publication


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

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.apply_theme_tokens(CYBER_FORGE_THEME.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._normal_background = QtGui.QColor(colors.panel_background)
        self._hover_background = QtGui.QColor(colors.hover)
        self._selected_background = QtGui.QColor(colors.active)
        self._text_color = QtGui.QColor(colors.text)
        self._detail_color = QtGui.QColor(colors.muted_text)
        self._selected_detail_color = QtGui.QColor(colors.highlight)

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        del option, index
        return QtCore.QSize(150, 52)

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
            self._selected_background
            if selected
            else self._hover_background
            if hovered
            else self._normal_background
        )
        painter.fillRect(option.rect.adjusted(2, 2, -2, -2), background)

        text_color = self._text_color
        detail_color = (
            self._selected_detail_color
            if selected
            else self._detail_color
        )
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
        self.setFixedWidth(144)
        self.setMinimumHeight(34)
        self.setMaxVisibleItems(len(PREVIEW_MODES))
        view = QtWidgets.QListView(self)
        view.setObjectName("PreviewFormatMenu")
        view.setMouseTracking(True)
        view.setSpacing(2)
        view.setUniformItemSizes(True)
        view.setMinimumWidth(144)
        self.setView(view)
        self.setItemDelegate(_PreviewModeDelegate(self))
        self._arrow = QtWidgets.QLabel("⌄", self)
        self._arrow.setObjectName("PreviewFormatArrow")
        self._arrow.setAlignment(Qt.AlignCenter)
        self._arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.apply_theme_tokens(CYBER_FORGE_THEME.tokens)

    def apply_theme_assets(self, service: ThemeService) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        background_top = _lighter(colors.control_background, 112)
        background_bottom = _darker(colors.control_background, 112)
        self.setStyleSheet(
            "QComboBox#PreviewFormatSelector{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {background_top},stop:1 {background_bottom});"
            f"color:{colors.text};border:1px solid {colors.accent};border-radius:8px;"
            "padding:6px 34px 6px 11px;font:600 10pt 'Segoe UI';}"
            "QComboBox#PreviewFormatSelector:hover{"
            f"background:{colors.hover};border-color:{colors.highlight};}}"
            "QComboBox#PreviewFormatSelector:focus{"
            f"border:2px solid {colors.accent};padding:5px 33px 5px 10px;}}"
            "QComboBox#PreviewFormatSelector:disabled{"
            f"background:{colors.control_background};color:{colors.muted_text};"
            f"border:1px solid {colors.border};}}"
            "QComboBox#PreviewFormatSelector::drop-down{width:31px;border:none;"
            f"border-left:1px solid {_rgba(colors.border, 170)};}}"
            "QComboBox#PreviewFormatSelector::down-arrow{image:none;width:0;height:0;}"
            "QListView#PreviewFormatMenu{"
            f"background:{colors.panel_background};color:{colors.text};"
            f"border:1px solid {colors.accent};"
            "border-radius:8px;padding:4px;outline:0;}"
            f"QLabel#PreviewFormatArrow{{color:{colors.accent};background:transparent;"
            "font:700 15pt 'Segoe UI Symbol';}"
        )
        delegate = self.itemDelegate()
        if isinstance(delegate, _PreviewModeDelegate):
            delegate.apply_theme_tokens(colors)
        self.setProperty("previewFormatTheme", colors.accent)
        self.update()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._arrow.setGeometry(self.width() - 30, 1, 28, self.height() - 2)
        self._arrow.raise_()
        super().resizeEvent(event)

    def showPopup(self) -> None:
        self.view().setMinimumWidth(self.width())
        super().showPopup()


class SavedLetterCard(QtWidgets.QFrame):
    """Compact, keyboard-accessible saved-letter selector."""

    selected = QtCore.Signal(object)
    activated = QtCore.Signal(object)
    delete_requested = QtCore.Signal(object)

    def __init__(
        self,
        entry: SavedLetter,
        parent=None,
        *,
        cover_requester: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self._delete_mode = False
        self._cover_requester = cover_requester
        self._cover_request_generation = 0
        self._cover_request_path = ""
        self._cover_cache_key: tuple[str, int, int, int, int, int] | None = None
        self.setObjectName("SavedLetterCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(184, 214)
        self.setToolTip(
            f"{entry.title} — {entry.recipient}\n"
            f"{entry.path}\n"
            + (
                "Bundled example master. Double-click or press Enter to load."
                if entry.example
                else "Double-click or press Enter to load."
            )
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(3)

        self.delete_button = QtWidgets.QToolButton(self)
        self.delete_button.setObjectName("SavedLetterDelete")
        self.delete_button.setText("−")
        set_control_help(
            self.delete_button,
            "Permanently remove this saved letter from your library.",
            accessible_name="Delete saved letter",
        )
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
        self.cover.setText("No cover")
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

        publication_label, publication_status_name = (
            self._publication_presentation(entry)
        )
        self.status_label = QtWidgets.QLabel(publication_label)
        self.status_label.setObjectName(publication_status_name)
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
            "QLabel#ExpiredStatus{color:#ffc4a8;background:#302018;"
            "border:1px solid #8d5940;border-radius:8px;"
            "font:600 8pt 'Segoe UI';}"
            "QLabel#ExampleStatus{color:#ffe6a0;background:#2d2512;"
            "border:1px solid #8d7330;border-radius:8px;"
            "font:600 8pt 'Segoe UI';}"
            "QLabel#StockStatus{color:#e6d4ff;background:#271b38;"
            "border:1px solid #7b52a8;border-radius:8px;"
            "font:600 8pt 'Segoe UI';}"
        )
        self._request_cover(entry.cover_path)

    @staticmethod
    def _publication_presentation(entry: SavedLetter) -> tuple[str, str]:
        if entry.example:
            return "Example", "ExampleStatus"
        if entry.stock:
            return "published", "StockStatus"
        if entry.published:
            return "Published", "PublishedStatus"
        if entry.expired:
            return "Expired", "ExpiredStatus"
        return "Local", "LocalStatus"

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
        self._delete_mode = bool(enabled)
        self.delete_button.setVisible(self._delete_mode and not self.entry.example)

    @staticmethod
    def _cover_path_identity(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve()).casefold()
        except (OSError, RuntimeError, ValueError):
            return str(path).casefold()

    def _request_cover(self, path: Path | None) -> None:
        previous_path = self._cover_request_path
        expected_path = self._cover_path_identity(path)
        self._cover_request_generation += 1
        request_generation = self._cover_request_generation
        self._cover_request_path = expected_path
        if expected_path != previous_path:
            self._cover_cache_key = None
            self.cover.clear()
            if not expected_path:
                self.cover.setText("No cover")
        if self._cover_requester is not None:
            self._cover_requester(
                self,
                path,
                request_generation,
                expected_path,
            )
            return
        pixmap = _scaled_cover_pixmap(path, 168, 92)
        self.apply_cover_result(
            request_generation,
            expected_path,
            None,
            pixmap,
        )

    def has_current_cover(
        self,
        request_generation: int,
        expected_path: str,
        key: tuple[str, int, int, int, int, int],
    ) -> bool:
        return (
            request_generation == self._cover_request_generation
            and expected_path == self._cover_request_path
            and key == self._cover_cache_key
        )

    def apply_cover_result(
        self,
        request_generation: int,
        expected_path: str,
        key: tuple[str, int, int, int, int, int] | None,
        pixmap: QtGui.QPixmap,
    ) -> bool:
        if (
            request_generation != self._cover_request_generation
            or expected_path != self._cover_request_path
        ):
            return False
        self._cover_cache_key = key
        self.cover.clear()
        if pixmap.isNull():
            self.cover.setText("No cover")
        else:
            self.cover.setPixmap(pixmap)
        return True

    def cancel_cover_request(self) -> None:
        self._cover_request_generation += 1
        self._cover_request_path = ""

    def update_entry(self, entry: SavedLetter) -> None:
        """Refresh one retained card after its saved metadata changes."""
        self.entry = entry
        self.setToolTip(
            f"{entry.title} — {entry.recipient}\n"
            f"{entry.path}\n"
            + (
                "Bundled example master. Double-click or press Enter to load."
                if entry.example
                else "Double-click or press Enter to load."
            )
        )
        self._request_cover(entry.cover_path)
        display_name = f"{entry.title} — {entry.recipient}"
        self.name_label.setText(display_name)
        self.name_label.setToolTip(display_name)
        self.title_label.setText(f"Title: {self._shorten(entry.title, 25)}")
        self.title_label.setToolTip(entry.title)
        self.recipient_label.setText(
            f"Recipient: {self._shorten(entry.recipient, 21)}"
        )
        self.recipient_label.setToolTip(entry.recipient)
        publication_label, status_name = self._publication_presentation(entry)
        self.status_label.setText(publication_label)
        if self.status_label.objectName() != status_name:
            self.status_label.setObjectName(status_name)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
        self.set_delete_mode(self._delete_mode)

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
        self._theme_tokens = CYBER_FORGE_THEME.tokens
        self._last_result: ReadinessResult | None = None
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
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.percentage = QtWidgets.QLabel()
        top.addWidget(self.percentage)
        top.addStretch(1)
        self.status = QtWidgets.QLabel()
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.status)
        layout.addLayout(top)

        self.divider = QtWidgets.QFrame()
        self.divider.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(self.divider)

        self.items = QtWidgets.QWidget(self)
        self.items_layout = QtWidgets.QVBoxLayout(self.items)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(3)
        self.items_scroll = QtWidgets.QScrollArea(self)
        self.items_scroll.setObjectName("ReadinessItemsScroll")
        self.items_scroll.setWidgetResizable(True)
        self.items_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.items_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items_scroll.setWidget(self.items)
        layout.addWidget(self.items_scroll, 1)

        self._missing_buttons: dict[str, QtWidgets.QPushButton] = {}
        for item in evaluate_readiness(self.project_root).items:
            button = QtWidgets.QPushButton(item.label)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, tab=item.correction_tab,
                target=item.correction_target:
                self._request_correction(tab, target)
            )
            self.items_layout.addWidget(button)
            self._missing_buttons[item.key] = button
        self._apply_theme_base()

    def apply_theme_assets(self, service: ThemeService) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._theme_tokens = colors
        self._apply_theme_base()
        if self._last_result is not None:
            self.refresh(self._last_result)

    def _apply_theme_base(self) -> None:
        colors = self._theme_tokens
        self.setStyleSheet(
            "QFrame#ProjectReadiness{"
            f"background:{colors.panel_background};"
            f"border:1px solid {colors.border};border-radius:9px;}}"
            "QLabel{background:transparent;font-weight:700;}"
            "QPushButton{font-weight:700;}"
        )
        self.percentage.setStyleSheet(
            f"color:{colors.text};font:700 13px 'Segoe UI';"
        )
        self.divider.setStyleSheet(f"color:{colors.border};")
        self.items_scroll.setStyleSheet(
            "QScrollArea#ReadinessItemsScroll{background:transparent;border:none;}"
            "QScrollArea#ReadinessItemsScroll>QWidget>QWidget{background:transparent;}"
            f"QScrollBar:vertical{{background:{colors.panel_background};"
            "width:9px;margin:0;}"
            f"QScrollBar::handle:vertical{{background:{colors.border};"
            "border-radius:4px;min-height:24px;}"
        )

    def _request_correction(self, tab: str, target: str) -> None:
        self.hide()
        self.correction_requested.emit(tab, target)

    def refresh(self, result: ReadinessResult) -> None:
        self._last_result = result
        colors = self._theme_tokens
        self.percentage.setText(f"{result.completion_percentage}%")
        percentage_color = (
            colors.success
            if result.completion_percentage >= 100
            else colors.text
        )
        self.percentage.setStyleSheet(
            f"color:{percentage_color};font:700 13px 'Segoe UI';"
        )
        self.status.setText(result.status)
        self.status.setStyleSheet(
            f"color:{colors.success if result.status != 'Not Ready' else colors.error};"
            "font:700 10pt 'Segoe UI';"
        )

        missing = {item.key: item for item in result.missing_items}
        for key, button in self._missing_buttons.items():
            item = missing.get(key)
            button.setVisible(item is not None)
            if item is None:
                continue
            color = colors.error if item.required else colors.warning
            button.setText(item.label)
            set_control_help(button, item.detail)
            button.setStyleSheet(
                "QPushButton{text-align:center;padding:6px 8px;font-weight:700;"
                f"border:1px solid {color};border-radius:5px;"
                f"background:{colors.control_background};color:{color};}}"
                f"QPushButton:hover{{background:{colors.hover};"
                f"border-color:{colors.accent};}}"
                f"QPushButton:focus{{border:1px solid {colors.accent};}}"
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
    preview_failed = QtCore.Signal()
    preview_files_release_requested = QtCore.Signal()
    project_files_release_requested = QtCore.Signal()
    restore_activity_changed = QtCore.Signal(bool, str)
    publication_activity_changed = QtCore.Signal(bool, str, str)
    preview_visibility_changed = QtCore.Signal(bool)
    published_url_changed = QtCore.Signal(str)
    _settings_refresh_requested = QtCore.Signal()
    _curtain_style_refresh_requested = QtCore.Signal()
    _cover_decode_completed = QtCore.Signal(int, object, object)
    _catalog_reconcile_completed = QtCore.Signal(int, str, object, str)
    _github_state_changed = QtCore.Signal(object)

    def __init__(
        self,
        project_root: str | Path,
        *,
        project_state: ProjectStateController | None = None,
        project_paths: ProjectPathResolver | None = None,
        curtain_styles: CurtainStyleController | None = None,
        github_service: GitHubConnectionService | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self._owns_curtain_styles = curtain_styles is None
        self.curtain_styles = curtain_styles or CurtainStyleController(
            self.settings,
            self,
        )
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
        self.stock_catalog = SavedLetterCatalog(
            self.project_root,
            stock_only=True,
        )
        self.restorer = SavedLetterRestorer(
            self.project_root,
            resolver=self.project_paths,
        )
        self.saved_page_url = ""
        self._last_play_dir: Optional[Path] = None
        self._preview_mode = self._saved_preview_mode()
        self._readiness_result = evaluate_readiness(self.project_root)
        self._review_complete = False
        self._busy = False
        self._busy_operation = ""
        self._shutdown = False
        self._worker: Optional[_TaskWorker] = None
        self._worker_thread: Optional[QtCore.QThread] = None
        self._operation_success: Optional[Callable[[object], None]] = None
        self._operation_failure: Optional[Callable[[], None]] = None
        self._operation_error_message = ""
        self._restore_operation_active = False
        self._publication_operation = ""
        self._project_release_error = ""
        self._github_service = github_service or github_connection_service()
        self._github_snapshot = self._github_service.snapshot
        self._github_account: GitHubAccount | None = (
            self._github_snapshot.account
            if self._github_snapshot.state == GitHubConnectionState.CONNECTED
            else None
        )
        self._github_session: GitHubSession | None = self._github_snapshot.session
        self._github_account_checked = False
        self._github_account_checking = False
        self._github_account_error = ""
        self._github_account_worker: Optional[_TaskWorker] = None
        self._github_account_thread: Optional[QtCore.QThread] = None
        self._github_sign_in_cancelled = False
        self._pending_publish_context: tuple | None = None
        self._pending_unpublish_context: tuple[dict, Path | None] | None = None
        self._pending_publish_retry_attempt = 0
        self._pending_unpublish_retry_attempt = 0
        self._selected_saved_letter: Optional[SavedLetter] = None
        self._pending_recipient_entry: Optional[SavedLetter] = None
        self._saved_cards: list[SavedLetterCard] = []
        self._archived_entries: list[SavedLetter] = []
        self._archive_groups: dict[str, tuple[SavedLetter, ...]] = {}
        self._saved_delete_mode = False
        self._saved_panel_mode = "saved"
        # A persisted catalog is already reconciled by explicit app operations;
        # watcher events and invalidation paths mark it dirty when needed.
        self._catalog_dirty = False
        self._catalog_rendered = False
        self._rendered_catalog_entries: tuple[SavedLetter, ...] = ()
        self._rendered_catalog_mode = ""
        self._catalog_watch_suppressed_until = 0.0
        self._cover_decode_pool = QtCore.QThreadPool(self)
        self._cover_decode_pool.setMaxThreadCount(2)
        self._cover_decode_pool.setExpiryTimeout(10_000)
        self._cover_decode_generation = 0
        self._cover_decode_waiters: dict[tuple, list[tuple]] = {}
        self._cover_decode_jobs: set[tuple] = set()
        self._cover_decode_completed.connect(self._cover_decode_finished)
        self._catalog_reconcile_pool = QtCore.QThreadPool(self)
        self._catalog_reconcile_pool.setMaxThreadCount(1)
        self._catalog_reconcile_pool.setExpiryTimeout(10_000)
        self._catalog_reconcile_generation = 0
        self._catalog_reconcile_active = False
        self._catalog_reconcile_mode = ""
        self._catalog_refresh_queued = False
        self._catalog_reconcile_completed.connect(
            self._catalog_reconcile_finished
        )
        self._github_state_changed.connect(self._apply_github_state)
        self._github_state_listener = self._github_state_changed.emit
        self._github_service.subscribe(self._github_state_listener)
        self._project_fingerprint = _forge_source_fingerprint(self.project_root)
        self._source_revision = 0
        try:
            self._preview_refresh_pending = not generate.is_play_bundle_current(
                self.project_root,
                source_fingerprint=self._project_fingerprint or None,
            )
        except Exception:
            _LOGGER.exception("Forge build currency could not be determined.")
            self._preview_refresh_pending = True
        self._preview_refresh_requested = False
        self._protected_published = False
        self._readiness_requested = False
        self._tab_active = False
        self._pending_scroll_position = (0, 0)
        self._pending_metadata_update: Optional[
            tuple[Path, ReadinessResult, bool]
        ] = None

        self.readiness_window = ReadinessWindow(self.project_root)
        self.readiness_window.correction_requested.connect(
            self._handle_readiness_correction
        )
        self.github_account_dialog = GitHubAccountDialog(self)
        self.github_account_dialog.sign_in_requested.connect(
            self.sign_in_github
        )
        self.github_account_dialog.sign_out_requested.connect(
            self.sign_out_github
        )
        self.github_auth_dialog = GitHubAuthenticationDialog(self)
        self.github_device_dialog = self.github_auth_dialog
        self.github_auth_dialog.connected.connect(
            self._finish_github_sign_in
        )
        self.github_auth_dialog.authentication_failed.connect(
            self._github_sign_in_failed
        )
        self.github_auth_dialog.cancel_requested.connect(
            self._cancel_github_sign_in
        )
        self._init_ui()

        self._card_layout_timer = QtCore.QTimer(self)
        self._card_layout_timer.setSingleShot(True)
        self._card_layout_timer.timeout.connect(self._layout_saved_cards)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh_project_state)
        self._settings_refresh_requested.connect(self.schedule_refresh)
        self._curtain_style_refresh_requested.connect(
            self._curtain_style_changed
        )
        self.curtain_styles.styleCommitted.connect(
            self._curtain_style_committed
        )
        self.settings.changed.connect(self._on_settings_changed)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self.status.clear)

        self._github_reconnect_timer = QtCore.QTimer(self)
        self._github_reconnect_timer.setSingleShot(True)
        self._github_reconnect_timer.timeout.connect(
            self._validate_github_account_async
        )

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

        self.refresh_project_state(refresh_source=False)
        QtCore.QTimer.singleShot(0, self._validate_github_account_async)

    def _saved_preview_mode(self, snapshot: dict | None = None) -> str:
        if snapshot is None:
            value = self.settings.get(PREVIEW_MODE_KEY, "landscape")
        else:
            value = snapshot.get(PREVIEW_MODE_KEY, "landscape")
        value = str(value).strip()
        valid = {mode for _label, mode in PREVIEW_MODES}
        return value if value in valid else "landscape"

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
        self._compact_layout = False
        self._main_layout.setContentsMargins(65, 20, 65, 11)
        self._main_layout.setSpacing(11)
        self._action_geometry_timer = QtCore.QTimer(self)
        self._action_geometry_timer.setSingleShot(True)
        self._action_geometry_timer.timeout.connect(self._sync_long_action_button_aspect_ratios)

        heading_row = QtWidgets.QHBoxLayout()
        self._heading_row = heading_row
        heading_row.setContentsMargins(0, 0, 0, 5)
        heading_row.setSpacing(9)
        self._heading_balance = QtWidgets.QWidget()
        self._heading_balance.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        heading_row.addWidget(self._heading_balance, 1)
        heading_row.addStretch(0)
        self.heading_title = QtWidgets.QLabel("Review and forge your letter")
        apply_tab_heading_style(self.heading_title)
        self.heading_title.setAlignment(Qt.AlignCenter)
        self.heading_title.setWordWrap(True)
        heading_row.addWidget(self.heading_title, 4)
        heading_row.addStretch(0)

        self._readiness_controls = QtWidgets.QWidget()
        readiness_row = QtWidgets.QHBoxLayout(self._readiness_controls)
        readiness_row.setContentsMargins(0, 0, 0, 0)
        readiness_row.setSpacing(9)
        self.readiness_summary = QtWidgets.QLabel()
        self.readiness_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.readiness_summary.setWordWrap(True)
        readiness_row.addWidget(self.readiness_summary)
        self.readiness_btn = self._tier_button(
            "Review",
            ButtonTier.SMALL,
            "BButton.png",
            bold=True,
        )
        self.readiness_btn.setProperty(
            BUTTON_FULL_TIER_GEOMETRY_PROPERTY,
            True,
        )
        self.readiness_btn.set_preserve_visual_when_disabled(True)
        self.readiness_btn.set_text_word_wrap(True)
        set_control_help(
            self.readiness_btn,
            "Review missing or optional letter items before previewing or publishing.",
        )
        self.readiness_btn.clicked.connect(self.show_readiness_window)
        readiness_row.addWidget(self.readiness_btn)
        heading_row.addWidget(self._readiness_controls)
        self._main_layout.addLayout(heading_row)

        self.load_saved_btn = self._tier_button(
            "Load Letters",
            ButtonTier.STANDARD,
            "AButton.png",
        )
        set_control_help(
            self.load_saved_btn,
            "Open your saved-letter library and load a previous project.",
        )
        self.load_saved_btn.clicked.connect(self.show_saved_letters)
        self.load_stock_btn = self._tier_button(
            "Stock",
            ButtonTier.STANDARD,
            "AButton.png",
        )
        set_control_help(
            self.load_stock_btn,
            "Open the bundled stock letters and load one as a new working project.",
        )
        self.load_stock_btn.clicked.connect(self.show_stock_letters)
        self.unpublish_btn = self._tier_button(
            "",
            ButtonTier.SMALL,
            "BButton.png",
            bold=True,
        )
        self.unpublish_btn.setProperty(
            BUTTON_FULL_TIER_GEOMETRY_PROPERTY,
            True,
        )
        self.unpublish_btn.set_theme_artwork_path("Unpub/Unpub.png")
        self.unpublish_btn.setAccessibleName("Unpublish Letter")
        self.unpublish_btn.hide()
        set_control_help(
            self.unpublish_btn,
            "Remove the online copy while preserving the local letter.",
        )
        self.unpublish_btn.clicked.connect(self.unpublish_letter)
        self._unpublish_balance = QtWidgets.QWidget()
        self._unpublish_controls = QtWidgets.QWidget()
        unpublish_controls_layout = QtWidgets.QHBoxLayout(
            self._unpublish_controls
        )
        unpublish_controls_layout.setContentsMargins(0, 0, 0, 0)
        unpublish_controls_layout.addWidget(
            self.unpublish_btn,
            alignment=Qt.AlignCenter,
        )
        saved_holder = QtWidgets.QHBoxLayout()
        saved_holder.setSpacing(5)
        saved_holder.setContentsMargins(0, 0, 0, 0)
        saved_holder.addWidget(self._unpublish_balance)
        saved_holder.addStretch(1)
        saved_holder.addWidget(self.load_saved_btn)
        saved_holder.addWidget(self.load_stock_btn)
        saved_holder.addStretch(1)
        saved_holder.addWidget(self._unpublish_controls)
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
        self.saved_heading = QtWidgets.QLabel("Saved Letters")
        self.saved_heading.setStyleSheet(
            "color:#dff9ff;font:600 11pt 'Segoe UI';"
        )
        saved_header.addWidget(self.saved_heading)
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
        set_control_help(
            self.saved_delete_toggle,
            "Show delete controls for saved letters and archived versions.",
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
        set_control_help(
            self.saved_archive_recipient,
            "Choose a recipient to view that recipient's archived letter versions.",
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
        set_control_help(
            self.saved_archive_list,
            "Select an archived letter version to load or delete it.",
        )
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
        set_control_help(
            self.saved_archive_delete,
            "Permanently remove the selected archived letter version.",
        )
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
        self.identity_panel.setMaximumWidth(1359)
        self.identity_panel.setStyleSheet(
            "QFrame#ForgeIdentity{background:#111921;"
            "border:1px solid #253d49;border-radius:7px;}"
        )
        identity_row = QtWidgets.QHBoxLayout(self.identity_panel)
        identity_row.setContentsMargins(9, 6, 9, 6)
        identity_row.setSpacing(9)
        identity_row.addWidget(self._muted_label("Title"))
        self.identity_title = QtWidgets.QLabel()
        self.identity_title.setStyleSheet("color:#f2fbff;font:600 10pt 'Segoe UI';")
        identity_row.addWidget(self.identity_title, 1)
        identity_row.addWidget(self._muted_label("Recipient"))
        self.identity_recipient = QtWidgets.QLabel()
        self.identity_recipient.setStyleSheet(
            "color:#f2fbff;font:600 10pt 'Segoe UI';"
        )
        for label in (self.identity_title, self.identity_recipient):
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            label.setWordWrap(True)
            label.setMaximumHeight(46)
        identity_row.addWidget(self.identity_recipient, 1)
        identity_holder = QtWidgets.QHBoxLayout()
        self._identity_holder = identity_holder
        identity_holder.setContentsMargins(0, 0, 0, 0)
        identity_holder.addStretch(1)
        identity_holder.addWidget(self.identity_panel, 1)
        identity_holder.addStretch(1)
        self._main_layout.addLayout(identity_holder)

        self.preview_format_panel = QtWidgets.QFrame(self)
        self.preview_format_panel.setObjectName("ForgePreviewFormat")
        self.preview_format_panel.setFixedWidth(468)
        self.preview_format_panel.setStyleSheet(
            "QFrame#ForgePreviewFormat{background:rgba(13,31,40,.86);"
            "border:1px solid #315c69;border-radius:10px;}"
        )
        format_row = QtWidgets.QHBoxLayout(self.preview_format_panel)
        format_row.setContentsMargins(11, 6, 9, 6)
        format_row.setSpacing(11)
        preview_format_controls = QtWidgets.QVBoxLayout()
        preview_format_controls.setContentsMargins(0, 0, 0, 0)
        preview_format_controls.setSpacing(4)
        self.preview_format_label = QtWidgets.QLabel("Preview Format")
        self.preview_format_label.setStyleSheet(
            "color:#dffbff;font:600 10pt 'Segoe UI';"
        )
        preview_format_controls.addWidget(self.preview_format_label)
        self.preview_mode = _PreviewModeCombo()
        set_control_help(
            self.preview_mode,
            "Choose the screen shape used when previewing the finished letter.",
        )
        self.preview_mode.setEnabled(False)
        self.preview_mode.currentIndexChanged.connect(
            self._preview_mode_changed
        )
        preview_format_controls.addWidget(self.preview_mode)
        format_row.addLayout(preview_format_controls)

        curtain_controls = QtWidgets.QVBoxLayout()
        curtain_controls.setContentsMargins(0, 0, 0, 0)
        curtain_controls.setSpacing(4)
        self.curtain_style_label = QtWidgets.QLabel("Choose Curtains")
        self.curtain_style_label.setStyleSheet(
            "color:#dffbff;font:600 10pt 'Segoe UI';"
        )
        curtain_controls.addWidget(self.curtain_style_label)
        self.curtain_style_selector = CurtainStyleComboBox(
            self,
            object_name="ForgeCurtainStyleSelector",
        )
        self.curtain_style_selector.setMinimumSize(225, 34)
        set_control_help(
            self.curtain_style_selector,
            "Choose white, normal, complementary, or normal/complementary "
            "light or dark curtains.",
            accessible_name="Choose Curtains",
        )
        self.curtain_styles.bind(self.curtain_style_selector)
        self.curtain_style_selector.set_choices_available(False)
        self.curtain_style_selector.setEnabled(False)
        curtain_controls.addWidget(self.curtain_style_selector)
        format_row.addLayout(curtain_controls, 1)
        self._sync_curtain_style()

        publishing_row = QtWidgets.QHBoxLayout()
        publishing_row.setContentsMargins(0, 0, 0, 0)
        publishing_row.setSpacing(8)
        publishing_row.addWidget(self._muted_label("Online hosting"))
        self.publishing_provider_label = QtWidgets.QLabel("GitHub Pages")
        self.publishing_provider_label.setStyleSheet(
            "color:#e8f9ff;font:600 10pt 'Segoe UI';"
        )
        publishing_row.addWidget(self.publishing_provider_label)
        self.github_account_summary = QtWidgets.QLabel()
        self.github_account_summary.setObjectName(
            "ForgeGitHubAccountSummary"
        )
        self.github_account_summary.setStyleSheet(
            "color:#9fcbd5;font:600 9pt 'Segoe UI';"
        )
        self.github_account_summary.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        self.github_account_summary.setWordWrap(True)
        self.github_account_summary.setMaximumHeight(46)
        publishing_row.addWidget(self.github_account_summary, 1)
        publishing_row.addStretch(0)
        self.github_account_btn = self._tier_button(
            "GitHub Account",
            ButtonTier.SMALL,
            "BButton.png",
        )
        self.github_account_btn.setProperty(
            BUTTON_FULL_TIER_GEOMETRY_PROPERTY,
            True,
        )
        set_control_help(
            self.github_account_btn,
            "Sign in with GitHub or review the connected GitHub account.",
            accessible_name="GitHub Account",
        )
        self.github_account_btn.clicked.connect(self.show_github_account)
        publishing_row.addWidget(self.github_account_btn)
        self._main_layout.addLayout(publishing_row)

        actions = QtWidgets.QHBoxLayout()
        self._actions_layout = actions
        actions.setSpacing(9)
        self.preview_btn = self._action_button(
            "Preview Letter", "ForgePreviewAction", "ALbutton.png"
        )
        set_control_help(
            self.preview_btn,
            "Build and open a local preview of the finished letter.",
        )
        self.preview_btn.clicked.connect(self.preview_letter)
        actions.addWidget(self.preview_btn, 10)
        self.publish_btn = self._action_button(
            "Publish Letter", "ForgePublishAction", "BLbutton.png"
        )
        set_control_help(
            self.publish_btn,
            "Publish the finished letter and create its shareable link.",
        )
        self.publish_btn.clicked.connect(self.publish_letter)
        actions.addWidget(self.publish_btn, 10)
        self.open_published_btn = self._action_button(
            "Open Letter", "ForgeOpenAction", "CLbutton.png"
        )
        set_control_help(
            self.open_published_btn,
            "Open the verified published letter in your web browser.",
        )
        self.open_published_btn.clicked.connect(self.open_published_letter)
        actions.addWidget(self.open_published_btn, 9)
        self._long_action_artwork = (
            (self.preview_btn, "ALbutton.png"),
            (self.publish_btn, "BLbutton.png"),
            (self.open_published_btn, "CLbutton.png"),
        )
        long_action_ratio = self._artwork_aspect_ratio(
            self.preview_btn.artwork_path
        )
        self._long_action_aspect_ratios = {
            button: long_action_ratio
            for button, _filename in self._long_action_artwork
        }
        self._long_action_width_scales = {
            self.preview_btn: 1.0,
            self.publish_btn: 1.0,
            self.open_published_btn: 0.9,
        }
        for button, _filename in self._long_action_artwork:
            button.set_artwork_stretch(True)
            button.installEventFilter(self)
        self._long_action_row = QtWidgets.QHBoxLayout()
        self._long_action_row.setContentsMargins(0, 0, 0, 0)
        self._long_action_row.setSpacing(0)
        # Reduce the existing centered group's geometry by 10%, keeping its fonts.
        self._long_action_row.addStretch(2506)
        self._long_action_row.addLayout(actions, 7488)
        self._long_action_row.addStretch(2506)
        self._main_layout.addLayout(self._long_action_row)
        self._sync_publishing_controls()

        self.status = QtWidgets.QLabel()
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
        self._apply_forge_theme(CYBER_FORGE_THEME.tokens)

    def _sync_heading_balance(self) -> None:
        self.unpublish_btn.setAccessibleName("Unpublish Letter")
        self._heading_balance.setMaximumWidth(
            self._readiness_controls.sizeHint().width()
        )
        self.heading_title.setMaximumWidth(
            self.heading_title.fontMetrics().horizontalAdvance(
                self.heading_title.text()
            ) + 4
        )
        unpublish_width = self.unpublish_btn.width()
        self._unpublish_balance.setFixedWidth(unpublish_width)
        self._unpublish_controls.setFixedWidth(unpublish_width)

    def set_compact_layout(self, compact: bool) -> None:
        """Reflow beside the preview when the window cannot fit both vertically."""
        if self._compact_layout == compact:
            return
        self._compact_layout = compact
        self._heading_balance.setVisible(not compact)
        self._unpublish_balance.setVisible(not compact)
        self._identity_holder.setStretch(0, 0 if compact else 1)
        self._identity_holder.setStretch(2, 0 if compact else 1)
        self._heading_row.setContentsMargins(0, 0, 0, 0 if compact else 5)
        self._main_layout.setSpacing(3 if compact else 11)
        self.identity_panel.layout().setContentsMargins(9, 4 if compact else 6, 9, 4 if compact else 6)
        self._actions_layout.setSpacing(2 if compact else 9)
        self._update_layout_margins()
        self._action_geometry_timer.start(0)

    def _update_layout_margins(self) -> None:
        side_margin = 2 if self._compact_layout else min(94, max(22, round(self.width() * 0.0495)))
        self._main_layout.setContentsMargins(
            side_margin, 0 if self._compact_layout else 20,
            side_margin, 0 if self._compact_layout else 11,
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._update_layout_margins()
        self._layout_saved_cards()
        self._card_layout_timer.start(0)
        self._sync_heading_balance()
        super().resizeEvent(event)
        self._action_geometry_timer.start(0)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if (event.type() == QtCore.QEvent.Resize
                and watched in self._long_action_aspect_ratios
                and event.size().width() != event.oldSize().width()):
            self._action_geometry_timer.start(0)
        return super().eventFilter(watched, event)

    @staticmethod
    def _muted_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color:#839da8;font:9pt 'Segoe UI';")
        return label

    def _tier_button(
        self,
        text: str,
        tier: ButtonTier,
        artwork_filename: str,
        *,
        bold: bool | None = None,
    ) -> QtWidgets.QPushButton:
        button = ArtworkButton(
            text,
            self.project_root,
            artwork_filename,
            tier=tier,
        )
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty("themeRole", "button")
        button.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, FORGE_GEOMETRY_SCALE)
        apply_button_tier(button, tier, bold=bold)
        return button

    def _action_button(
        self,
        text: str,
        object_name: str,
        artwork_filename: str,
    ) -> QtWidgets.QPushButton:
        button = ArtworkButton(
            text,
            self.project_root,
            artwork_filename,
            broken_artwork_filename=artwork_filename,
            long_form=True,
            tier=None,
        )
        button.setObjectName(object_name)
        button.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, FORGE_GEOMETRY_SCALE)
        button.setMinimumHeight(63)
        button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        button.set_artwork_stretch(True)
        button.setCursor(Qt.PointingHandCursor)
        return button

    @staticmethod
    def _artwork_aspect_ratio(path: str | Path) -> float:
        size = QtGui.QImageReader(str(path)).size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            return 0.0
        return size.width() / size.height()

    def _sync_long_action_button_aspect_ratios(self) -> None:
        buttons = tuple(
            button
            for button, _filename in self._long_action_artwork
            if not button.isHidden()
        )
        if not buttons:
            return
        for button in buttons:
            ratio = self._long_action_aspect_ratios.get(button, 0.0)
            content_width = max(1, button.width() - 4)
            height = round(content_width / ratio) + 4 if ratio > 0 else 63
            button.setFixedHeight(max(height, minimum_button_text_size(button).height()))

    def apply_theme_assets(self, service: ThemeService) -> None:
        self._apply_forge_theme(
            service.tokens,
            app_font_family=service.app_font_family,
        )
        self.github_auth_dialog.apply_theme_assets(service)
        self.github_account_dialog.apply_theme_assets(service)
        for button in (
            self.readiness_btn,
            self.load_saved_btn,
            self.load_stock_btn,
            self.github_account_btn,
            self.preview_btn,
            self.publish_btn,
            self.unpublish_btn,
            self.open_published_btn,
        ):
            button.apply_theme_assets(service)
        self._sync_publishing_controls()
        ratio = 0.0
        if service.current.uses_image_buttons:
            artwork = service.resolve_button_asset(
                "ALbutton.png",
                long_form=True,
                allow_baseline=False,
            )
            ratio = self._artwork_aspect_ratio(artwork)
        for button, _filename in self._long_action_artwork:
            self._long_action_aspect_ratios[button] = ratio
        QtCore.QTimer.singleShot(0, self._sync_heading_balance)
        self._action_geometry_timer.start(0)

    def _apply_forge_theme(
        self,
        colors: ThemeTokens,
        *,
        app_font_family: str = "Segoe UI",
    ) -> None:
        self._theme_tokens = colors
        self.preview_format_panel.setStyleSheet(
            "QFrame#ForgePreviewFormat{"
            f"background:{_rgba(colors.panel_background, 235)};"
            f"border:1px solid {colors.border};border-radius:10px;}}"
        )
        self.preview_format_label.setStyleSheet(
            f"color:{colors.highlight};font:600 10pt 'Segoe UI';"
        )
        self.curtain_style_label.setStyleSheet(
            f"color:{colors.highlight};font:600 10pt 'Segoe UI';"
        )
        self.preview_mode.apply_theme_tokens(colors)
        self.curtain_style_selector.apply_theme_tokens(colors)
        self.readiness_window.apply_theme_tokens(colors)

        uses_dark_text = _relative_luminance(colors.text) < 0.5
        if uses_dark_text:
            action_specs = (
                (
                    self.preview_btn,
                    _color_name(colors.primary),
                    _lighter(colors.primary, 112),
                    _color_name(colors.active),
                    colors.primary,
                ),
                (
                    self.publish_btn,
                    _color_name(colors.secondary),
                    _lighter(colors.secondary, 108),
                    _color_name(colors.active),
                    colors.secondary,
                ),
                (
                    self.open_published_btn,
                    _color_name(colors.control_background),
                    _color_name(colors.hover),
                    _color_name(colors.active),
                    colors.accent,
                ),
            )
        else:
            action_specs = (
                (
                    self.preview_btn,
                    _darker(colors.primary, 190),
                    _darker(colors.primary, 160),
                    _darker(colors.primary, 228),
                    colors.primary,
                ),
                (
                    self.publish_btn,
                    _darker(colors.secondary, 190),
                    _darker(colors.secondary, 165),
                    _darker(colors.secondary, 228),
                    colors.secondary,
                ),
                (
                    self.open_published_btn,
                    _color_name(colors.control_background),
                    _color_name(colors.hover),
                    _darker(colors.control_background, 120),
                    colors.accent,
                ),
            )
        for button, background, hover, pressed, border in action_specs:
            foreground = _readable_text(
                (background, hover, pressed),
                colors.text,
            )
            name = button.objectName()
            button.setStyleSheet(
                f"QPushButton#{name}{{background:{background};color:{foreground};"
                f"border:1px solid {border};border-radius:9px;"
                f"font:700 {FORGE_ACTION_FONT_POINT_SIZE:g}pt '{app_font_family}';"
                "padding:9px 16px;}"
                f"QPushButton#{name}:hover{{background:{hover};"
                f"border-color:{colors.highlight};}}"
                f"QPushButton#{name}:pressed{{background:{pressed};}}"
                f"QPushButton#{name}:focus{{border:2px solid {colors.accent};}}"
                f"QPushButton#{name}:disabled{{background:{colors.card_background};"
                f"color:{colors.muted_text};border-color:{colors.border};}}"
            )
            font = QtGui.QFont(button.font())
            font.setFamily(app_font_family)
            font.setPointSizeF(FORGE_ACTION_FONT_POINT_SIZE)
            font.setWeight(QtGui.QFont.Weight.Bold)
            button.setFont(font)
            button.setProperty("forgeActionBackground", background)
            button.setProperty("forgeActionHover", hover)
            button.setProperty("forgeActionText", foreground)

    def _on_settings_changed(
        self,
        settings: dict,
        keys: tuple[str, ...],
    ) -> None:
        if "curtain_style" in keys:
            self._curtain_style_refresh_requested.emit()
        if _FORGE_RELEVANT_SETTING_KEYS.intersection(keys):
            self._settings_refresh_requested.emit()

    def _sync_curtain_style(self, settings: dict | None = None) -> None:
        snapshot = self.settings.snapshot() if settings is None else settings
        self.curtain_styles.sync_from_settings(snapshot)

    def set_curtain_preview_colors(
        self,
        colors: dict[str, tuple[int, int, int]],
    ) -> None:
        self.curtain_styles.set_preview_colors(colors)

    def _set_curtain_style(self, style: str) -> None:
        self.curtain_styles.set_style(style)

    @QtCore.Slot()
    def _curtain_style_changed(self) -> None:
        self._sync_curtain_style()
        self._refresh_source_fingerprint()
        if self._tab_active and not self._shutdown:
            self.ensure_preview_current()

    @QtCore.Slot(str)
    def _curtain_style_committed(self, style: str) -> None:
        self._set_status(
            f"Curtain style set to {CURTAIN_STYLE_LABELS[style]}."
        )

    def _sync_publishing_controls(self) -> None:
        configuration = github_application_configuration()
        snapshot = self._github_snapshot
        state = snapshot.state
        account = snapshot.account
        if state == GitHubConnectionState.CONNECTING or self._github_account_checking:
            summary = "Checking connection…"
        elif state == GitHubConnectionState.AUTHORIZING:
            summary = "Connecting…"
        elif state == GitHubConnectionState.CONNECTED and account is not None:
            summary = f"Connected as {account.login}"
        elif state == GitHubConnectionState.RECONNECTING and account is not None:
            summary = f"{account.login} — Reconnecting automatically…"
        elif state == GitHubConnectionState.INSTALL_REQUIRED and account is not None:
            summary = f"{account.login} — Installation Required"
        elif state == GitHubConnectionState.ACTION_REQUIRED and account is not None:
            summary = f"{account.login} — Action Required"
        elif state == GitHubConnectionState.GITHUB_UNAVAILABLE or self._github_account_error:
            summary = "Connection unavailable"
        elif not configuration.configured:
            summary = "Developer setup required"
        else:
            summary = "Not signed in to GitHub"
        sign_in_warning = bool(
            state == GitHubConnectionState.DISCONNECTED
            and configuration.configured
            and not self._github_account_checking
        )
        self._set_github_account_summary(
            summary,
            sign_in_warning=sign_in_warning,
        )
        connected = bool(
            account is not None
            and state in {
                GitHubConnectionState.CONNECTED,
                GitHubConnectionState.RECONNECTING,
            }
        )
        theme_definition = getattr(
            getattr(self.github_account_btn, "_theme_service", None),
            "current",
            None,
        )
        image_theme = bool(
            getattr(theme_definition, "uses_image_buttons", True)
        )
        basic_connected_artwork = bool(connected and not image_theme)
        if basic_connected_artwork:
            self.github_account_btn.set_presentation_artwork(None)
            self.github_account_btn.set_theme_artwork_path(
                "git/ConnectButton.png"
            )
        else:
            self.github_account_btn.set_theme_artwork_path(None)
            self.github_account_btn.set_presentation_artwork(
                "ConnectButton.png" if connected and image_theme else None,
                broken=connected and image_theme,
            )
        uses_artwork = self.github_account_btn.uses_artwork_presentation
        if connected:
            button_text = ""
        elif state == GitHubConnectionState.DISCONNECTED:
            button_text = "Sign in"
        elif uses_artwork:
            button_text = ""
        elif state == GitHubConnectionState.CONNECTED:
            button_text = "GitHub Connected"
        elif (
            state == GitHubConnectionState.RECONNECTING
            and account is not None
        ):
            button_text = "GitHub Reconnecting"
        else:
            button_text = "Connect GitHub"
        self.github_account_btn.setText(button_text)
        apply_button_tier(self.github_account_btn, ButtonTier.SMALL)
        self.github_account_btn.setEnabled(
            not self._busy
            and state not in {
                GitHubConnectionState.CONNECTING,
                GitHubConnectionState.AUTHORIZING,
            }
        )
        self.github_account_btn.setAccessibleName(
            "GitHub Account — connected"
            if connected
            else "Sign in to GitHub"
            if state == GitHubConnectionState.DISCONNECTED
            else "GitHub Account — not connected"
        )
        self.github_account_dialog.set_connection(snapshot)
        if hasattr(self, "readiness_summary"):
            self._update_readiness_summary(
                self._readiness_result,
                sign_in_warning=sign_in_warning,
            )
            self._update_review_button_state(self._readiness_result)
            self._sync_heading_balance()
        if hasattr(self, "publish_btn"):
            self._update_letter_action_button_states()

    def _set_github_account_summary(
        self,
        summary: str,
        *,
        sign_in_warning: bool,
    ) -> None:
        self.github_account_summary.setText(summary)
        self.github_account_summary.setAccessibleName(summary)
        self.github_account_summary.setProperty(
            "githubSignInWarning",
            sign_in_warning,
        )
        if sign_in_warning:
            self.github_account_summary.setToolTip(
                "Sign in to GitHub before publishing this letter."
            )
            self.github_account_summary.setStyleSheet(
                "QLabel#ForgeGitHubAccountSummary{"
                f"color:{_GITHUB_WARNING_TEXT};"
                f"background:{_GITHUB_WARNING_BACKGROUND};"
                f"border:1px solid {_GITHUB_WARNING_BORDER};"
                "border-radius:8px;padding:4px 9px;"
                "font:700 9pt 'Segoe UI';}"
            )
            self.github_account_summary.setMaximumWidth(
                self.github_account_summary.fontMetrics().horizontalAdvance(summary) + 20
            )
            return
        colors = getattr(self, "_theme_tokens", CYBER_FORGE_THEME.tokens)
        self.github_account_summary.setToolTip(summary)
        self.github_account_summary.setStyleSheet(
            "QLabel#ForgeGitHubAccountSummary{"
            f"color:{colors.muted_text};background:transparent;"
            "border:none;padding:0;font:600 9pt 'Segoe UI';}"
        )
        self.github_account_summary.setMaximumWidth(
            self.github_account_summary.fontMetrics().horizontalAdvance(summary)
        )

    @QtCore.Slot(object)
    def _apply_github_state(self, result: object) -> None:
        if self._shutdown or not isinstance(result, GitHubConnectionSnapshot):
            return
        previous_state = self._github_snapshot.state
        self._github_snapshot = result
        self._github_session = result.session
        self._github_account = (
            result.account
            if result.state in {
                GitHubConnectionState.CONNECTED,
                GitHubConnectionState.RECONNECTING,
            }
            else None
        )
        self._github_account_error = (
            result.message
            if result.state == GitHubConnectionState.GITHUB_UNAVAILABLE
            else ""
        )
        retry_timer = getattr(self, "_github_reconnect_timer", None)
        if result.state == GitHubConnectionState.RECONNECTING:
            self._schedule_github_reconnect(result)
        elif retry_timer is not None:
            retry_timer.stop()
        if (
            result.state == GitHubConnectionState.CONNECTED
            and (
                self._pending_publish_context is not None
                or self._pending_unpublish_context is not None
            )
            and result.session is not None
        ):
            QtCore.QTimer.singleShot(
                0,
                lambda session=result.session: self._defer_until_idle(
                    lambda: self._resume_pending_github_operations(session)
                ),
            )
        elif result.state == GitHubConnectionState.GITHUB_UNAVAILABLE:
            if self._pending_publish_context is not None:
                self._abort_publication_activity("publish")
            elif self._pending_unpublish_context is not None:
                self._abort_publication_activity("unpublish")
        elif (
            result.state
            in {
                GitHubConnectionState.DISCONNECTED,
                GitHubConnectionState.INSTALL_REQUIRED,
                GitHubConnectionState.ACTION_REQUIRED,
            }
            and not self._busy
            and (
                self._pending_publish_context is not None
                or self._pending_unpublish_context is not None
            )
        ):
            self._update_publication_activity(
                "Waiting for GitHub authorization…"
            )
            QtCore.QTimer.singleShot(
                0,
                lambda: self._defer_until_idle(self.sign_in_github),
            )
        if (
            result.state == GitHubConnectionState.CONNECTED
            and previous_state != GitHubConnectionState.CONNECTED
        ):
            play_ui_sound(UiSound.GITHUB_CONNECTED)
        elif (
            result.state == GitHubConnectionState.DISCONNECTED
            and previous_state
            in {
                GitHubConnectionState.CONNECTED,
                GitHubConnectionState.RECONNECTING,
            }
        ):
            play_ui_sound(UiSound.GITHUB_DISCONNECTED)
        self._sync_publishing_controls()

    def _schedule_github_reconnect(
        self,
        snapshot: GitHubConnectionSnapshot,
    ) -> None:
        timer = getattr(self, "_github_reconnect_timer", None)
        if self._shutdown or timer is None or timer.isActive():
            return
        delay_ms = max(1, round(snapshot.retry_after_seconds * 1000))
        _LOGGER.info(
            "[GitHub] Qt reconnect timer armed: retry=%s delay_ms=%s",
            snapshot.retry_attempt,
            delay_ms,
        )
        timer.start(delay_ms)

    def _queue_github_operation_retry(self, operation: str) -> bool:
        snapshot = self._github_service.snapshot
        if (
            snapshot.state != GitHubConnectionState.RECONNECTING
            or snapshot.session is None
        ):
            return False
        if operation == "publishing":
            if self._pending_publish_context is None:
                return False
            attempt = self._pending_publish_retry_attempt + 1
            self._pending_publish_retry_attempt = attempt
        elif operation == "unpublishing":
            if self._pending_unpublish_context is None:
                return False
            attempt = self._pending_unpublish_retry_attempt + 1
            self._pending_unpublish_retry_attempt = attempt
        else:
            raise ValueError(f"Unsupported GitHub operation: {operation}")
        if attempt > _MAX_GITHUB_OPERATION_RETRIES:
            return False
        self._set_status(
            "GitHub is temporarily unavailable. "
            f"Retrying {operation} automatically "
            f"({attempt}/{_MAX_GITHUB_OPERATION_RETRIES})…",
            timeout_ms=0,
        )
        self._update_publication_activity(
            "GitHub paused briefly; reconnecting automatically…"
        )
        self._schedule_github_reconnect(snapshot)
        return True

    def show_github_account(self) -> None:
        self._sync_publishing_controls()
        owner = self.window()
        self.github_account_dialog.show()
        center = owner.mapToGlobal(owner.rect().center())
        frame = self.github_account_dialog.frameGeometry()
        frame.moveCenter(center)
        self.github_account_dialog.move(frame.topLeft())
        self.github_account_dialog.raise_()
        self.github_account_dialog.activateWindow()
        if not self._github_account_checking:
            self._validate_github_account_async()

    def _validate_github_account_async(self) -> None:
        if self._shutdown or self._github_account_checking:
            return
        thread = self._github_account_thread
        if thread is not None and thread.isRunning():
            return
        self._github_account_checking = True
        self._sync_publishing_controls()
        thread = QtCore.QThread(self)
        worker = _TaskWorker(
            self._github_service.restore
        )
        worker.moveToThread(thread)
        self._github_account_thread = thread
        self._github_account_worker = worker
        thread.started.connect(worker.run)
        worker.succeeded.connect(
            self._github_account_validation_succeeded,
            Qt.QueuedConnection,
        )
        worker.failed.connect(
            self._github_account_validation_failed,
            Qt.QueuedConnection,
        )
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(
            self._github_account_validation_finished,
            Qt.QueuedConnection,
        )
        thread.start()

    @QtCore.Slot(object)
    def _github_account_validation_succeeded(self, result: object) -> None:
        if self._shutdown:
            return
        if not isinstance(result, GitHubConnectionSnapshot):
            raise TypeError("GitHub returned invalid connection state.")
        self._apply_github_state(result)
        self._github_account_checked = True

    @QtCore.Slot(str, str, bool)
    def _github_account_validation_failed(
        self,
        message: str,
        technical: str,
        _user_safe: bool,
    ) -> None:
        if self._shutdown:
            return
        self._github_account_checked = True
        self._github_account_error = (
            "Could not reach GitHub. Local Letter Smith features remain available."
        )
        _LOGGER.warning(
            "GitHub account validation failed: %s\n%s",
            message,
            technical,
        )
        self.github_account_dialog.set_connection(self._github_snapshot)
        if self._publication_operation:
            self._abort_publication_activity(self._publication_operation)

    @QtCore.Slot()
    def _github_account_validation_finished(self) -> None:
        if self._shutdown:
            return
        thread = self._github_account_thread
        self._github_account_worker = None
        self._github_account_thread = None
        self._github_account_checking = False
        self._sync_publishing_controls()
        if self._github_snapshot.state == GitHubConnectionState.RECONNECTING:
            self._schedule_github_reconnect(self._github_snapshot)
        if thread is not None:
            thread.deleteLater()

    def _run_github_task(self, task: Callable[[], object]) -> object:
        try:
            return task()
        except GitHubOperationError as error:
            if error.technical_details:
                _LOGGER.error(
                    "GitHub operation failed: code=%s details=%s",
                    error.code,
                    error.technical_details,
                )
            raise _ForgeOperationError(error.user_message) from error

    @QtCore.Slot()
    def sign_in_github(self) -> None:
        if self._busy or self.github_auth_dialog.isVisible():
            return
        configuration = github_application_configuration()
        if not configuration.configured:
            message = (
                "GitHub publishing is not configured in this Letter Smith build."
            )
            self.github_account_dialog.set_account(None, message=message)
            self._set_status(message, error=True)
            if self._publication_operation:
                self._abort_publication_activity(self._publication_operation)
            self.show_github_account()
            return
        self._github_sign_in_cancelled = False
        snapshot = self._github_service.snapshot
        if snapshot.state == GitHubConnectionState.RECONNECTING:
            self._set_status(
                snapshot.message or "GitHub is reconnecting automatically…",
                error=False,
                timeout_ms=0,
            )
            self._schedule_github_reconnect(snapshot)
            return
        if (
            snapshot.state == GitHubConnectionState.GITHUB_UNAVAILABLE
            and snapshot.session is not None
        ):
            self._validate_github_account_async()
            return
        if snapshot.state in {
            GitHubConnectionState.INSTALL_REQUIRED,
            GitHubConnectionState.ACTION_REQUIRED,
        }:
            access = snapshot.access
            if access is None:
                self._set_status("Restoring GitHub authorization…", timeout_ms=0)
                self.github_auth_dialog.begin_authentication(self._github_service)
                return
            if access is not None and not access.setup_url:
                message = access.message or (
                    "GitHub publishing setup is incomplete in this Letter Smith build."
                )
                self.github_account_dialog.set_connection(snapshot)
                self._set_status(message, error=True, timeout_ms=0)
                if self._publication_operation:
                    self._abort_publication_activity(
                        self._publication_operation
                    )
                return
            self._set_status("Restoring GitHub access…", timeout_ms=0)
            self.github_auth_dialog.begin_publishing_access(
                self._github_service,
                snapshot,
            )
            return
        self._set_status("Requesting GitHub sign-in…", timeout_ms=0)
        self.github_auth_dialog.begin_authentication(self._github_service)

    def _begin_github_access_setup(
        self,
        session: GitHubSession,
        access: GitHubPublishingAccess,
    ) -> bool:
        snapshot = self._github_service.snapshot
        if snapshot.session != session:
            raise _ForgeOperationError(
                "GitHub publishing setup is incomplete in this Letter Smith build."
            )
        if not access.setup_url:
            message = access.message or (
                "GitHub publishing setup is incomplete in this Letter Smith build."
            )
            self.github_account_dialog.set_connection(snapshot)
            self._set_status(message, error=True, timeout_ms=0)
            return False
        self._update_publication_activity(
            "Waiting for GitHub publishing access…"
        )
        self.github_auth_dialog.begin_publishing_access(
            self._github_service,
            snapshot,
        )
        return True

    def _finish_github_sign_in(self, session: GitHubSession) -> None:
        self._apply_github_state(self._github_service.snapshot)
        self._github_account_checked = True
        self._github_account_error = ""
        self._sync_publishing_controls()
        self._set_status(f"Connected as {session.account.login}.")

    def _github_sign_in_failed(self, message: str = "") -> None:
        self._sync_publishing_controls()
        if self._github_account is None:
            self.github_account_dialog.set_account(
                None,
                message=message or "GitHub sign-in was not completed.",
            )
        self._set_status(
            message or "GitHub sign-in was not completed.",
            error=True,
            timeout_ms=0,
        )
        operation = self._publication_operation
        if operation:
            self._abort_publication_activity(operation)

    @QtCore.Slot()
    def _cancel_github_sign_in(self) -> None:
        self._github_sign_in_cancelled = True
        if hasattr(self, "_pending_publish_context"):
            self._pending_publish_context = None
            self._pending_publish_retry_attempt = 0
        if hasattr(self, "_pending_unpublish_context"):
            self._pending_unpublish_context = None
            self._pending_unpublish_retry_attempt = 0
        self._finish_publication_activity()
        self._sync_publishing_controls()
        self._set_status("GitHub sign-in canceled.")

    @QtCore.Slot()
    def sign_out_github(self) -> None:
        if self._busy or self._publication_operation:
            return
        try:
            self._github_service.sign_out()
        except GitHubOperationError as error:
            _LOGGER.error(
                "GitHub sign-out failed: code=%s details=%s",
                error.code,
                error.technical_details,
            )
            self._set_status(error.user_message, error=True)
            return
        self._apply_github_state(self._github_service.snapshot)
        self._github_account_checked = True
        self._github_account_error = ""
        self._pending_publish_context = None
        self._pending_unpublish_context = None
        self._pending_publish_retry_attempt = 0
        self._pending_unpublish_retry_attempt = 0
        self._finish_publication_activity()
        self._sync_publishing_controls()
        self._set_status(
            "Signed out of GitHub. Published letters and saved links were not changed."
        )

    def _defer_until_idle(self, callback: Callable[[], None]) -> None:
        if self._shutdown:
            return
        if self._busy:
            QtCore.QTimer.singleShot(
                50,
                lambda: self._defer_until_idle(callback),
            )
            return
        callback()
    def _refresh_source_fingerprint(self) -> bool:
        current = _forge_source_fingerprint(self.project_root)
        if current == self._project_fingerprint:
            return False
        self._project_fingerprint = current
        self._source_revision += 1
        self._preview_refresh_pending = True
        return True

    def schedule_refresh(self) -> None:
        if self._shutdown or not self._tab_active:
            return
        self._refresh_timer.start()

    def refresh_project_state(self, *, refresh_source: bool = True) -> None:
        if refresh_source:
            self._refresh_source_fingerprint()
        snapshot = self.settings.snapshot()
        self._sync_curtain_style(snapshot)
        self._sync_publishing_controls()
        preview_mode = self._saved_preview_mode(snapshot)
        preview_mode_changed = preview_mode != self._preview_mode
        self._preview_mode = preview_mode
        title = str(snapshot.get("recipient_title", "")).strip()
        recipient = str(snapshot.get("recipient_name", "")).strip()
        self.identity_title.setText(title or "Untitled")
        self.identity_title.setToolTip(title)
        self.identity_recipient.setText(recipient or "No recipient")
        self.identity_recipient.setToolTip(recipient)
        self.refresh_saved_page_url(snapshot)
        self.refresh_readiness()
        if preview_mode_changed and not self._preview_refresh_pending:
            self.request_preview()

    def is_protected_project(self, snapshot: dict | None = None) -> bool:
        return settings_is_protected_project(
            snapshot if snapshot is not None else self.settings.snapshot()
        )

    def show_readiness_window(self) -> None:
        if self.is_protected_project():
            self._readiness_requested = False
            self.readiness_window.hide()
            self._set_status(
                "Readiness does not apply to Stock or Example Letters."
            )
            return
        result = self.refresh_readiness()
        if (
            result.completion_percentage >= 100
            and self._github_sign_in_warning_active()
        ):
            self._readiness_requested = False
            self.readiness_window.hide()
            self.show_github_account()
            return
        if self.readiness_window.isVisible():
            self._readiness_requested = False
            self.readiness_window.hide()
            return
        self._readiness_requested = True
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

    def dismiss_readiness(self) -> None:
        self._readiness_requested = False
        self.readiness_window.hide()

    def _handle_readiness_correction(self, tab: str, target: str) -> None:
        self.dismiss_readiness()
        self.correction_requested.emit(tab, target)

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
        self._sync_preview_option_controls(result)
        protected = self.is_protected_project()
        self._readiness_controls.setVisible(not protected)
        self._update_review_button_state(result)
        if protected:
            self.readiness_summary.clear()
            self._readiness_requested = False
            self.readiness_window.hide()
            self._sync_heading_balance()
            self._update_letter_action_button_states(result)
            return result
        self._update_readiness_summary(result)
        if result.completion_percentage >= 100:
            self._readiness_requested = False
            self.readiness_window.hide()
        self._sync_heading_balance()
        self._update_letter_action_button_states(result)
        return result

    def _sync_preview_option_controls(self, readiness: ReadinessResult) -> None:
        available = readiness.can_preview
        with QtCore.QSignalBlocker(self.preview_mode):
            if available:
                if self.preview_mode.count() == 0:
                    for label, mode in PREVIEW_MODES:
                        self.preview_mode.addItem(label, mode)
                        self.preview_mode.setItemData(
                            self.preview_mode.count() - 1,
                            PREVIEW_MODE_DESCRIPTIONS[mode],
                            Qt.UserRole + 1,
                        )
                current = self.preview_mode.findData(self._preview_mode)
                self.preview_mode.setCurrentIndex(current)
            else:
                self.preview_mode.clear()
        self.preview_mode.setEnabled(available and not self._busy)
        self.preview_mode._arrow.setVisible(available)
        self.curtain_style_selector.set_choices_available(available)
        self.curtain_style_selector.setEnabled(available and not self._busy)

    def _github_sign_in_warning_active(self) -> bool:
        return bool(
            self._github_snapshot.state == GitHubConnectionState.DISCONNECTED
            and github_application_configuration().configured
            and not self._github_account_checking
        )

    def _update_readiness_summary(
        self,
        result: ReadinessResult,
        *,
        sign_in_warning: bool | None = None,
    ) -> None:
        if self.is_protected_project():
            self.readiness_summary.clear()
            self.readiness_summary.setToolTip("")
            self.readiness_summary.setProperty("githubSignInWarning", False)
            return
        if sign_in_warning is None:
            sign_in_warning = self._github_sign_in_warning_active()
        self.readiness_summary.setProperty(
            "githubSignInWarning",
            sign_in_warning,
        )
        if sign_in_warning:
            self.readiness_summary.setText("Sign in to GitHub to publish")
            self.readiness_summary.setToolTip(
                "Sign in to GitHub before publishing this letter."
            )
            self.readiness_summary.setStyleSheet(
                f"color:{_GITHUB_WARNING_TEXT};"
                f"background:{_GITHUB_WARNING_BACKGROUND};"
                f"border:1px solid {_GITHUB_WARNING_BORDER};"
                "border-radius:8px;padding:4px 9px;"
                "font:700 9pt 'Segoe UI';"
            )
            return
        colors = getattr(self, "_theme_tokens", CYBER_FORGE_THEME.tokens)
        color = colors.success if result.status != "Not Ready" else colors.error
        self.readiness_summary.setText(
            f"{result.completion_percentage}%  {result.status}"
        )
        self.readiness_summary.setToolTip("")
        self.readiness_summary.setStyleSheet(
            f"color:{color};background:transparent;border:none;"
            "padding:0;font:700 10pt 'Segoe UI';"
        )

    def _update_review_button_state(self, result: ReadinessResult) -> None:
        locally_complete = bool(
            not self.is_protected_project()
            and result.completion_percentage >= 100
        )
        sign_in_required = bool(
            locally_complete and self._github_sign_in_warning_active()
        )
        complete = locally_complete and not sign_in_required
        changed = complete != self._review_complete
        self._review_complete = complete
        if sign_in_required:
            self.readiness_btn.set_presentation_artwork(None, animate=changed)
            self.readiness_btn.setText("Sign in to publish")
            apply_button_tier(
                self.readiness_btn,
                ButtonTier.SMALL,
                bold=True,
            )
            set_control_help(
                self.readiness_btn,
                "Sign in to GitHub before publishing this letter.",
                accessible_name="Sign in to GitHub to publish",
            )
            self.readiness_btn.setEnabled(True)
            return
        self.readiness_btn.set_presentation_artwork(
            "git/CompButton.png" if complete else "git/ReviewButton.png",
            animate=changed,
            theme_relative=True,
        )
        self.readiness_btn.setText(
            ""
            if self.readiness_btn.has_artwork
            else "Complete"
            if complete
            else "Review"
        )
        apply_button_tier(
            self.readiness_btn,
            ButtonTier.SMALL,
            bold=True,
        )
        set_control_help(
            self.readiness_btn,
            "Every readiness item is complete."
            if complete
            else "Review missing or optional letter items before previewing or publishing.",
            accessible_name=(
                "Project readiness complete"
                if complete
                else "Review project readiness"
            ),
        )
        self.readiness_btn.setEnabled(not complete)

    def _update_letter_action_button_states(
        self,
        result: ReadinessResult | None = None,
    ) -> None:
        readiness = result or self._readiness_result
        snapshot = self.settings.snapshot()
        protected = self.is_protected_project(snapshot)
        publication_valid = self._known_valid_publication(snapshot)
        published = not protected and publication_valid
        published_fingerprint = str(
            snapshot.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
        ).strip()
        current_fingerprint = str(self._project_fingerprint).strip()
        publication_changed = bool(
            published
            and (
                not published_fingerprint
                or not current_fingerprint
                or published_fingerprint != current_fingerprint
            )
        )
        publication_locked = published and not publication_changed
        self.publish_btn.setText(
            "Update Published Letter"
            if publication_changed
            else "Published"
            if published
            else "Publish Letter"
        )
        self.publish_btn.setAccessibleName(self.publish_btn.text())
        set_control_help(
            self.publish_btn,
            "Update the existing online letter without changing its public link."
            if publication_changed
            else "This letter is published and matches the current local version."
            if published
            else "Publish the finished letter and create its shareable link.",
        )
        self.preview_btn.set_action_state(
            broken=not protected and not readiness.can_preview,
            invisible=self._busy,
        )
        self.publish_btn.set_action_state(
            broken=(
                not protected
                and not readiness.can_publish
                and not publication_locked
            ),
            invisible=self._busy or publication_locked,
        )
        self.open_published_btn.set_action_state(
            broken=not publication_valid,
            invisible=self._busy,
        )
        publishing_active = bool(
            self._pending_publish_context is not None
            or self._busy_operation
            in {
                "Preparing letter for publishing…",
                "Publishing letter…",
            }
        )
        primary_visibility_changed = any(
            button.isHidden() != publishing_active
            for button in (
                self.preview_btn,
                self.publish_btn,
                self.open_published_btn,
            )
        )
        for button in (
            self.preview_btn,
            self.publish_btn,
            self.open_published_btn,
        ):
            button.setVisible(not publishing_active)
        self.unpublish_btn.setVisible(published)
        self.unpublish_btn.setAccessibleName("Unpublish Letter")
        self.unpublish_btn.set_action_state(
            broken=not published,
            invisible=self._busy,
        )
        if primary_visibility_changed:
            self._action_geometry_timer.start(0)

    def _known_valid_publication(self, snapshot: dict | None = None) -> bool:
        state = self.settings.snapshot() if snapshot is None else snapshot
        if self.is_protected_project(state):
            index = self._current_play_index()
            return bool(
                self._protected_published
                and index is not None
                and index.is_file()
            )
        return bool(
            publication_status(state) == "published"
            and normalize_published_page_url(
                state.get(PUBLISHED_PAGE_URL_KEY, "")
            )
            and not self._published_url_unavailable(state)
        )

    def show_saved_letters(self) -> None:
        self._saved_panel_mode = "saved"
        self._show_letter_panel()

    def show_stock_letters(self) -> None:
        self._saved_panel_mode = "stock"
        self._show_letter_panel()

    def _show_letter_panel(self) -> None:
        if self._busy:
            return
        self._set_saved_delete_mode(False)
        stock_mode = self._saved_panel_mode == "stock"
        self.saved_heading.setText(
            "Stock Letters" if stock_mode else "Saved Letters"
        )
        self.saved_delete_toggle.setVisible(not stock_mode)
        self.refresh_saved_letters(
            force_reconcile=not stock_mode or self._catalog_dirty
        )
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

    def repair_duplicate_autosave_ids(self) -> tuple[tuple[Path, str], ...]:
        """Repair independent autosave copies while preserving the active path."""
        snapshot = self.settings.snapshot()
        context = self.project_paths.context_from_settings(snapshot)
        repaired = self.project_paths.repair_duplicate_autosave_ids(
            active_autosave_directory=context.autosave_directory,
        )
        if repaired:
            self.catalog = SavedLetterCatalog(self.project_root)
            self._catalog_dirty = True
            if self.saved_panel.isVisible():
                self.refresh_saved_letters(force_reconcile=True)
            self.refresh_project_state()
        return repaired

    def refresh_saved_letters(self, *, force_reconcile: bool = False) -> None:
        mode = self._saved_panel_mode
        catalog = self.stock_catalog if mode == "stock" else self.catalog
        needs_reconcile = bool(
            force_reconcile
            or (self._catalog_dirty and mode != "stock")
            or catalog.requires_reconciliation
        )
        if self._catalog_reconcile_active:
            if needs_reconcile or mode != self._catalog_reconcile_mode:
                self._catalog_refresh_queued = True
            return

        if needs_reconcile:
            self._start_catalog_reconciliation(catalog, mode)
            return

        if catalog.is_loaded:
            entries = catalog.list_entries()
        elif catalog.stock_only:
            self._start_catalog_reconciliation(catalog, mode)
            return
        else:
            # Loading a valid persistent index is bounded JSON/stat work. If it
            # is absent or corrupt, defer the fallback directory scan instead
            # of allowing list_entries() to perform it on the GUI thread.
            try:
                entries = catalog.load_persisted_entries()
            except Exception:
                entries = None
                _LOGGER.exception("The saved-letter index could not be loaded.")
            if entries is None:
                self._start_catalog_reconciliation(catalog, mode)
                return

        if mode != "stock":
            self._catalog_dirty = False
        self._render_saved_letter_entries(entries, mode)

    def _render_saved_letter_entries(
        self,
        entries: tuple[SavedLetter, ...],
        mode: str,
    ) -> None:
        if mode != self._saved_panel_mode:
            return
        selected_path = (
            str(self._selected_saved_letter.path)
            if self._selected_saved_letter is not None
            else ""
        )
        horizontal = self.saved_scroll.horizontalScrollBar().value()
        vertical = self.saved_scroll.verticalScrollBar().value()
        if (
            self._catalog_rendered
            and self._rendered_catalog_mode == mode
            and entries == self._rendered_catalog_entries
        ):
            self._watch_saved_letter_paths(entries)
            return
        recent_entries = entries[:RECENT_SAVED_LETTER_LIMIT]
        archived_entries = entries[RECENT_SAVED_LETTER_LIMIT:]
        retained_cards = {
            card.entry.path: card
            for card in self._saved_cards
        }
        self._saved_cards = []
        self._selected_saved_letter = None

        for entry in recent_entries:
            card = retained_cards.pop(entry.path, None)
            if card is None:
                card = SavedLetterCard(
                    entry,
                    self.saved_cards_widget,
                    cover_requester=self._request_saved_card_cover,
                )
                card.selected.connect(self._select_saved_letter)
                card.activated.connect(self._activate_saved_letter)
                card.delete_requested.connect(self._delete_saved_letter)
            else:
                card.update_entry(entry)
            card.setEnabled(not self._busy)
            card.set_delete_mode(self._saved_delete_mode)
            if str(entry.path) == selected_path:
                self._selected_saved_letter = entry
                card.set_selected(True)
            else:
                card.set_selected(False)
            card.show()
            self._saved_cards.append(card)

        for card in retained_cards.values():
            card.cancel_cover_request()
            card.hide()
            self.saved_cards_layout.removeWidget(card)
            card.deleteLater()

        self._refresh_saved_archive(
            archived_entries,
            selected_path,
            all_entries=entries,
        )
        self._layout_saved_cards()
        self._watch_saved_letter_paths(entries)
        self._rendered_catalog_entries = entries
        self._rendered_catalog_mode = mode
        self._catalog_rendered = True
        self._pending_scroll_position = (horizontal, vertical)
        self._scroll_restore_timer.start(0)

    def _start_catalog_reconciliation(
        self,
        catalog: SavedLetterCatalog,
        mode: str,
    ) -> None:
        if self._shutdown or self._catalog_reconcile_active:
            return
        self._catalog_reconcile_generation += 1
        self._catalog_reconcile_active = True
        self._catalog_reconcile_mode = mode
        self._catalog_refresh_queued = False
        self._set_catalog_interaction_enabled(False)
        self._catalog_reconcile_pool.start(
            _CatalogReconcileTask(
                self._catalog_reconcile_completed,
                self._catalog_reconcile_generation,
                mode,
                catalog.project_root,
                catalog.stock_only,
            )
        )

    @QtCore.Slot(int, str, object, str)
    def _catalog_reconcile_finished(
        self,
        generation: int,
        mode: str,
        entries: tuple[SavedLetter, ...] | None,
        error: str,
    ) -> None:
        if self._shutdown or generation != self._catalog_reconcile_generation:
            return
        self._catalog_reconcile_active = False
        queued_refresh = self._catalog_refresh_queued
        self._catalog_refresh_queued = False
        if error or entries is None:
            self._catalog_dirty = mode != "stock"
            _LOGGER.error(
                "Saved-letter reconciliation failed.%s",
                f"\n{error}" if error else "",
            )
            self._set_status(
                "Saved letters could not be refreshed.",
                error=True,
            )
        else:
            catalog = (
                self.stock_catalog if mode == "stock" else self.catalog
            )
            entries = catalog.accept_reconciled_entries(entries)
            if mode != "stock" and not queued_refresh:
                self._catalog_dirty = False
            if mode == self._saved_panel_mode and not queued_refresh:
                self._render_saved_letter_entries(entries, mode)
        if queued_refresh and not self._shutdown:
            QtCore.QTimer.singleShot(
                0,
                lambda: self.refresh_saved_letters(force_reconcile=True),
            )
            return
        self._set_catalog_interaction_enabled(True)

    def _stop_catalog_reconcile_tasks(self, timeout_ms: int | None) -> bool:
        self._catalog_reconcile_generation += 1
        self._catalog_reconcile_active = False
        self._catalog_reconcile_mode = ""
        self._catalog_refresh_queued = False
        self._catalog_reconcile_pool.clear()
        self._set_catalog_interaction_enabled(True)
        if timeout_ms is None:
            return bool(self._catalog_reconcile_pool.waitForDone())
        return bool(
            self._catalog_reconcile_pool.waitForDone(max(0, int(timeout_ms)))
        )

    def _set_catalog_interaction_enabled(self, enabled: bool) -> None:
        active = bool(enabled) and not self._busy
        for card in self._saved_cards:
            card.setEnabled(active)
        for widget in (
            self.saved_delete_toggle,
            self.saved_archive_recipient,
            self.saved_archive_list,
            self.saved_archive_delete,
        ):
            widget.setEnabled(active)

    def _request_saved_card_cover(
        self,
        card: SavedLetterCard,
        path: Path | None,
        request_generation: int,
        expected_path: str,
    ) -> None:
        if self._shutdown:
            return
        identity = _cover_cache_identity(path, 168, 92)
        if identity is None:
            card.apply_cover_result(
                request_generation,
                expected_path,
                None,
                QtGui.QPixmap(),
            )
            return
        resolved, key = identity
        if card.has_current_cover(request_generation, expected_path, key):
            return
        cached = _cached_cover_pixmap(key)
        if cached is not None:
            card.apply_cover_result(
                request_generation,
                expected_path,
                key,
                cached,
            )
            return

        waiters = self._cover_decode_waiters.setdefault(key, [])
        waiters[:] = [
            waiter
            for waiter in waiters
            if waiter[0]() is not card
        ]
        waiters.append(
            (
                weakref.ref(card),
                int(request_generation),
                expected_path,
            )
        )
        if key in self._cover_decode_jobs:
            return
        self._cover_decode_jobs.add(key)
        self._cover_decode_pool.start(
            _CoverDecodeTask(
                self._cover_decode_completed,
                self._cover_decode_generation,
                key,
                resolved,
                168,
                92,
            )
        )

    @QtCore.Slot(int, object, object)
    def _cover_decode_finished(
        self,
        generation: int,
        key: tuple[str, int, int, int, int, int],
        image: QtGui.QImage,
    ) -> None:
        if self._shutdown or generation != self._cover_decode_generation:
            return
        self._cover_decode_jobs.discard(key)
        waiters = self._cover_decode_waiters.pop(key, ())
        pixmap = _cache_cover_image(key, image)
        for card_reference, request_generation, expected_path in waiters:
            card = card_reference()
            if card is None:
                continue
            try:
                card.apply_cover_result(
                    request_generation,
                    expected_path,
                    key,
                    pixmap,
                )
            except RuntimeError:
                # The Qt card may have been deleted after the worker started.
                continue

    def _stop_cover_decode_tasks(self, timeout_ms: int | None) -> bool:
        self._cover_decode_generation += 1
        self._cover_decode_waiters.clear()
        self._cover_decode_jobs.clear()
        for card in self._saved_cards:
            card.cancel_cover_request()
        self._cover_decode_pool.clear()
        if timeout_ms is None:
            return bool(self._cover_decode_pool.waitForDone())
        return bool(
            self._cover_decode_pool.waitForDone(max(0, int(timeout_ms)))
        )

    def _refresh_catalog_entry(self, play_dir: str | Path) -> None:
        """Apply a known build change without scanning unrelated letters."""
        self._catalog_watch_suppressed_until = (
            monotonic() + _CATALOG_INTERNAL_CHANGE_GRACE_SECONDS
        )
        if self._catalog_reconcile_active:
            self._catalog_dirty = True
            self._catalog_refresh_queued = True
            return
        entries = self.catalog.refresh_entry(play_dir)
        if entries is None:
            self._catalog_dirty = True
            return
        self._catalog_dirty = False
        if self.saved_panel.isVisible():
            self.refresh_saved_letters()

    def reset_after_project_wipe(self) -> None:
        """Drop live references to project content removed by Command."""
        self.preview_files_release_requested.emit()
        self._refresh_timer.stop()
        self._metadata_timer.stop()
        self._pending_metadata_update = None
        self._tab_active = False
        self._readiness_requested = False
        self._last_play_dir = None
        self._selected_saved_letter = None
        self._project_fingerprint = _forge_source_fingerprint(self.project_root)
        self._source_revision += 1
        self._preview_refresh_pending = True
        self._preview_refresh_requested = False
        self._protected_published = False
        self.saved_page_url = ""
        self.saved_panel.hide()
        self.readiness_window.hide()
        self._sync_published_url()
        self.catalog.invalidate()
        self._catalog_dirty = True
        self.refresh_saved_letters(force_reconcile=True)
        self.refresh_project_state()
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
        set_control_help(
            self.saved_delete_toggle,
            tooltip,
            accessible_name=accessible,
        )
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
        *,
        all_entries: tuple[SavedLetter, ...] | None = None,
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

        recipient_first_dates: dict[str, date] = {}
        for entry in all_entries or entries:
            if entry.example:
                continue
            recipient = " ".join(entry.recipient.split()) or "Unknown recipient"
            key = recipient.casefold()
            current = recipient_first_dates.get(key)
            created = entry.created_sort_date
            if current is None or created < current:
                recipient_first_dates[key] = created

        self._archive_groups = {
            key: tuple(group)
            for key, group in groups.items()
        }
        self.saved_archive_recipient.blockSignals(True)
        self.saved_archive_recipient.clear()
        self.saved_archive_recipient.addItem("Choose recipient…", "")
        ordered_keys = sorted(
            groups,
            key=lambda key: (
                -recipient_first_dates[key].toordinal(),
                display_names[key].casefold(),
            ),
        )
        for key in ordered_keys:
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
                cover = _scaled_cover_pixmap(entry.cover_path, 38, 48)
                if not cover.isNull():
                    item.setIcon(QtGui.QIcon(cover))
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
        if not self._release_project_files_for_restore():
            self._finish_restore_activity()
            return
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
        if not self._release_project_files_for_restore():
            self._finish_restore_activity()
            self._restore_recipient_requirement()
            return False
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
        if self._saved_panel_mode == "stock":
            self._set_status("Stock letters cannot be deleted.", error=True)
            return
        if entry.example:
            self._set_status(
                "The bundled Example Letter cannot be deleted.",
                error=True,
            )
            return
        confirmation = LetterSmithConfirmationDialog(
            self.saved_panel,
            title="Delete Saved Letter",
            question=(
                f'Delete "{entry.title}" for {entry.recipient}?\n\n'
                "This cannot be undone."
            ),
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
        )
        if confirmation.exec() != QtWidgets.QDialog.Accepted:
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
        self._refresh_catalog_entry(deleted)
        self._set_status("Saved letter deleted.")
        play_ui_sound(UiSound.REMOVED)

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
                "No stock letters available."
                if self._saved_panel_mode == "stock"
                else "No saved letters yet. Preview a letter to create one."
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
        paths = application_paths(self.project_root)
        candidates = {
            paths.documents_root,
            paths.stock_root,
            self.catalog.play_root,
            self.catalog.recovery_root,
            self.stock_catalog.stock_root,
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
        if self._shutdown:
            return
        if monotonic() < self._catalog_watch_suppressed_until:
            return
        if self._catalog_reconcile_active:
            self._catalog_dirty = True
            self._catalog_refresh_queued = True
            return
        self.catalog.invalidate()
        self._catalog_dirty = True
        if self.saved_panel.isVisible():
            self._catalog_refresh_timer.start()

    def _restore_saved_scroll_position(self) -> None:
        horizontal, vertical = self._pending_scroll_position
        self.saved_scroll.horizontalScrollBar().setValue(horizontal)
        self.saved_scroll.verticalScrollBar().setValue(vertical)

    def report_project_file_release_failure(self, message: str) -> None:
        self._project_release_error = str(message or "").strip()

    def _release_project_files_for_restore(self) -> bool:
        """Release live viewers and media before replacing project folders."""
        self._project_release_error = ""
        stop_cover_tasks = getattr(self, "_stop_cover_decode_tasks", None)
        covers_released = (
            bool(stop_cover_tasks(timeout_ms=1200))
            if callable(stop_cover_tasks)
            else True
        )
        stop_catalog_tasks = getattr(
            self,
            "_stop_catalog_reconcile_tasks",
            None,
        )
        catalog_released = (
            bool(stop_catalog_tasks(timeout_ms=1200))
            if callable(stop_catalog_tasks)
            else True
        )
        self.preview_files_release_requested.emit()
        self.project_files_release_requested.emit()
        if (
            (not covers_released or not catalog_released)
            and not self._project_release_error
        ):
            self._project_release_error = (
                "Saved-letter background work did not stop before restore."
            )
        if self._project_release_error:
            _LOGGER.error(
                "Project files could not be released for restore: %s",
                self._project_release_error,
            )
            self._set_status(
                "Background media work must finish before this letter can load.",
                error=True,
                timeout_ms=0,
            )
            return False
        return True

    def _begin_restore_activity(self, activity: str) -> None:
        self._restore_operation_active = True
        self.restore_activity_changed.emit(True, activity)

    def _finish_restore_activity(self) -> None:
        if not self._restore_operation_active:
            return
        self._restore_operation_active = False
        self.restore_activity_changed.emit(False, "")

    def _begin_publication_activity(
        self,
        operation: str,
        detail: str,
    ) -> None:
        normalized = str(operation).strip().lower()
        if normalized not in {"publish", "unpublish"}:
            raise ValueError(f"Unsupported publication operation: {operation}")
        self._publication_operation = normalized
        self.publication_activity_changed.emit(True, normalized, str(detail))

    def _update_publication_activity(self, detail: str) -> None:
        operation = self._publication_operation
        if not operation:
            return
        self.publication_activity_changed.emit(True, operation, str(detail))

    def _finish_publication_activity(self, operation: str = "") -> None:
        active_operation = self._publication_operation
        if not active_operation:
            return
        expected = str(operation).strip().lower()
        if expected and expected != active_operation:
            return
        self._publication_operation = ""
        self.publication_activity_changed.emit(False, active_operation, "")

    def _abort_publication_activity(self, operation: str) -> None:
        normalized = str(operation).strip().lower()
        if normalized == "publish":
            self._pending_publish_context = None
            self._pending_publish_retry_attempt = 0
        elif normalized == "unpublish":
            self._pending_unpublish_context = None
            self._pending_unpublish_retry_attempt = 0
        self._finish_publication_activity(normalized)

    def _publication_connection_snapshot(self) -> GitHubConnectionSnapshot:
        snapshot = self._github_service.snapshot
        if (
            snapshot.state == GitHubConnectionState.CONNECTED
            and snapshot.session is not None
            and snapshot.access is not None
            and snapshot.access.ready
        ):
            return snapshot
        return self._github_service.restore()

    def _ready_publication_session(
        self,
        operation: str,
    ) -> GitHubSession | None:
        snapshot = self._github_service.snapshot
        if snapshot.state == GitHubConnectionState.RECONNECTING:
            self._update_publication_activity(
                "Reconnecting to GitHub automatically…"
            )
            self._schedule_github_reconnect(snapshot)
            return None
        if snapshot.state == GitHubConnectionState.GITHUB_UNAVAILABLE:
            self._abort_publication_activity(operation)
            return None
        if snapshot.state in {
            GitHubConnectionState.DISCONNECTED,
            GitHubConnectionState.INSTALL_REQUIRED,
            GitHubConnectionState.ACTION_REQUIRED,
        }:
            self._update_publication_activity(
                "Waiting for GitHub authorization…"
            )
            self._defer_until_idle(self.sign_in_github)
            return None
        if snapshot.state in {
            GitHubConnectionState.CONNECTING,
            GitHubConnectionState.AUTHORIZING,
        }:
            self._update_publication_activity(
                "Waiting for GitHub authorization…"
            )
            return None
        if (
            snapshot.state != GitHubConnectionState.CONNECTED
            or snapshot.session is None
            or snapshot.access is None
            or not snapshot.access.ready
        ):
            self._set_status(
                snapshot.message
                or "GitHub publishing access changed. Please try again.",
                error=True,
                timeout_ms=0,
            )
            self._abort_publication_activity(operation)
            return None
        return snapshot.session

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
        self._last_play_dir = None
        if self.is_protected_project():
            self._protected_published = False
        self.project_state.transition(
            ApplicationState.PROJECT_READY,
            identity=restored.identity,
        )
        self._pending_recipient_entry = None
        self.refresh_project_state()
        self._preview_refresh_pending = True
        self._refresh_catalog_entry(Path(restored.play_dir).resolve())
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
        if not self._readiness_result.can_preview:
            return
        mode = str(self.preview_mode.currentData() or "landscape")
        self._preview_mode = mode
        self.settings.update_fields({PREVIEW_MODE_KEY: mode})
        self.request_preview()

    def _current_play_index(self) -> Optional[Path]:
        try:
            index = generate.play_bundle_directory(self.project_root) / "index.html"
        except Exception:
            _LOGGER.exception("The current Forge play directory could not be resolved.")
            return None
        return index if index.is_file() else None

    def current_play_index(self) -> Optional[Path]:
        """Return the current playable viewer entry point, when available."""
        if self._preview_refresh_pending or not self._readiness_result.can_preview:
            return None
        return self._current_play_index()

    @property
    def preview_mode_value(self) -> str:
        return self._preview_mode

    @property
    def preview_refresh_pending(self) -> bool:
        return self._preview_refresh_pending

    @property
    def operation_in_progress(self) -> bool:
        return self._busy

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

    def _flush_prompt_writer_state(self) -> bool:
        hook = getattr(self.window(), "flush_prompt_writer_state", None)
        if not callable(hook):
            return True
        try:
            saved = bool(hook())
        except Exception:
            _LOGGER.exception("Prompt Writer state could not be flushed before saving.")
            saved = False
        if not saved:
            self._set_status(
                "Prompt Writer state could not be saved. The letter was not updated.",
                error=True,
            )
        return saved

    def preview_letter(self) -> None:
        self._prepare_preview(open_in_browser=True)

    def ensure_preview_current(self) -> None:
        """Build or refresh the embedded preview before it is displayed."""
        self._refresh_source_fingerprint()
        if not self.refresh_readiness().can_preview:
            self._preview_refresh_pending = True
            return
        if self._busy:
            self._preview_refresh_pending = True
            self._preview_refresh_requested = True
            return
        if (
            not self._preview_refresh_pending
            and self._current_play_index() is not None
        ):
            self.request_preview()
            return
        self._prepare_preview(open_in_browser=False)

    def _prepare_preview(
        self,
        *,
        open_in_browser: bool,
        protected_publish: bool = False,
    ) -> None:
        if self._busy:
            return
        if not self._flush_prompt_writer_state():
            return
        self._refresh_source_fingerprint()
        readiness = self._required_gate(for_publish=protected_publish)
        if readiness is None:
            return
        ensure_output_dirs(self.project_root)
        message_path = self.project_root / MESSAGE_HTML_FILE
        try:
            message = (
                read_text_normalized(message_path)
                if message_path.is_file()
                else ""
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
                    source_fingerprint=requested_fingerprint or None,
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
                self._protected_publish_completed
                if protected_publish
                else (
                    self._preview_completed
                    if open_in_browser
                    else self._embedded_preview_completed
                )
            ),
            "Preview could not be updated. The previous preview was preserved.",
            on_failure=self.preview_failed.emit,
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

    def _protected_publish_completed(self, result: object) -> None:
        _play_dir, index = self._finish_preview(
            result,
            record_activity=False,
        )
        self._protected_published = index.is_file()
        self._sync_published_url()
        if self._protected_published:
            play_ui_sound(UiSound.PUBLISH_COMPLETE)
            self._set_status(
                "Published for this demonstration. Open Letter is ready."
            )
        else:
            self._set_status(
                "The demonstration preview could not be opened.",
                error=True,
            )

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
        if self._busy or self._publication_operation:
            return
        if self.is_protected_project():
            self._prepare_preview(
                open_in_browser=False,
                protected_publish=True,
            )
            return
        if not self._flush_prompt_writer_state():
            return
        self._refresh_source_fingerprint()
        readiness = self._required_gate(for_publish=True)
        if readiness is None:
            return
        if not bool(self.settings.get(PUBLIC_WARNING_KEY, False)):
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Publish Letter",
                question=(
                    "Publishing makes the finished letter available to anyone "
                    "who has its link. Continue?"
                ),
                primary_text="Yes",
                secondary_text="No",
                click_outside_dismiss=False,
            )
            if confirmation.exec() != QtWidgets.QDialog.Accepted:
                self._set_status("Publishing canceled.")
                return
            self.settings.update_fields({PUBLIC_WARNING_KEY: True})

        message_path = self.project_root / MESSAGE_HTML_FILE
        try:
            message = (
                read_text_normalized(message_path)
                if message_path.is_file()
                else ""
            )
        except Exception:
            _LOGGER.exception("Could not read the current message.")
            self._set_status("Message content could not be read.", error=True)
            return

        source_revision = self._source_revision
        requested_fingerprint = self._project_fingerprint

        def task() -> tuple:
            try:
                play_dir, _rebuilt = generate.ensure_play_bundle(
                    self.project_root,
                    message_html=message,
                    force=False,
                    source_fingerprint=requested_fingerprint or None,
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
            metadata["source_fingerprint"] = requested_fingerprint
            record_saved_letter_activity(play_path)
            snapshot = self._publication_connection_snapshot()
            session = snapshot.session
            access = snapshot.access
            return (
                play_path,
                readiness,
                metadata,
                source_revision,
                requested_fingerprint,
                _forge_source_fingerprint(self.project_root),
                session,
                access,
            )

        self.preview_files_release_requested.emit()
        self._begin_publication_activity(
            "publish",
            "Preparing the finished letter…",
        )
        self._start_operation(
            "Preparing letter for publishing…",
            task,
            self._publish_prepared,
            "Publishing could not start. The local build was preserved.",
            on_failure=lambda: self._abort_publication_activity("publish"),
        )

    def _publish_prepared(self, result: object) -> None:
        values = tuple(result)
        if len(values) != 8:
            raise TypeError("The prepared publication result is invalid.")
        (
            play_dir,
            readiness,
            metadata,
            source_revision,
            requested_fingerprint,
            completed_fingerprint,
            session,
            access,
        ) = values
        self._last_play_dir = Path(play_dir)
        self._record_active_play_dir(self._last_play_dir)
        self._project_fingerprint = str(completed_fingerprint)
        source_changed = (
            self._source_revision != int(source_revision)
            or str(completed_fingerprint) != str(requested_fingerprint)
        )
        self._preview_refresh_pending = source_changed
        if source_changed:
            self._preview_refresh_requested = True
        else:
            self.request_preview()
        self._refresh_catalog_entry(Path(play_dir))
        self._pending_publish_context = (
            Path(play_dir),
            readiness,
            dict(metadata),
            int(source_revision),
            str(requested_fingerprint),
            str(completed_fingerprint),
        )
        self._pending_publish_retry_attempt = 0
        if not isinstance(session, GitHubSession):
            self._set_status("Sign in with GitHub to continue publishing.", timeout_ms=0)
            self._update_publication_activity("Waiting for GitHub sign-in…")
            self._defer_until_idle(self.sign_in_github)
            return
        self._github_session = session
        snapshot = self._github_service.snapshot
        self._apply_github_state(snapshot)
        if snapshot.state == GitHubConnectionState.RECONNECTING:
            self._set_status(
                "GitHub is temporarily unavailable. Reconnecting automatically…",
                timeout_ms=0,
            )
            self._update_publication_activity(
                "Reconnecting to GitHub automatically…"
            )
            self._schedule_github_reconnect(snapshot)
            return
        if snapshot.state == GitHubConnectionState.GITHUB_UNAVAILABLE:
            self._set_status(
                snapshot.message or "GitHub is temporarily unavailable.",
                error=True,
                timeout_ms=0,
            )
            self._abort_publication_activity("publish")
            return
        if not isinstance(access, GitHubPublishingAccess):
            raise TypeError("GitHub returned invalid publishing access details.")
        if not access.ready:
            if not self._begin_github_access_setup(session, access):
                self._abort_publication_activity("publish")
            return
        self._update_publication_activity("Starting the secure upload…")

    def _resume_pending_github_operations(self, session: GitHubSession) -> None:
        if self._pending_publish_context is not None:
            self._resume_pending_publish(session)
            return
        if self._pending_unpublish_context is not None:
            self._resume_pending_unpublish(session)

    def _resume_pending_publish(self, session: GitHubSession) -> None:
        if self._busy:
            return
        context = self._pending_publish_context
        if context is None:
            self._finish_publication_activity("publish")
            return
        current_session = self._ready_publication_session("publish")
        if current_session is None:
            return
        session = current_session
        (
            play_dir,
            readiness,
            metadata,
            source_revision,
            requested_fingerprint,
            _prepared_fingerprint,
        ) = context

        def task() -> tuple:
            api = self._github_service.authorized_api(session)
            connection_snapshot = self._github_service.snapshot
            effective_session = connection_snapshot.session or session
            publisher = GitHubPagesPublisher(
                self.project_root,
                effective_session,
                api=api,
                auth_service=self._github_service,
                connection_generation=connection_snapshot.generation,
                cancelled=lambda: (
                    QtCore.QThread.currentThread().isInterruptionRequested()
                ),
            )
            publish_result = publisher.publish(Path(play_dir), dict(metadata))
            snapshot_path = None
            snapshot_failed = False
            if getattr(publish_result, "success", False):
                publication = _publication_fields_from_result(publish_result)
                if publication_status(publication) == "published":
                    try:
                        snapshot_path = save_published_snapshot(
                            play_dir,
                            self.project_root,
                            publication,
                        )
                    except Exception:
                        _LOGGER.exception(
                            "The published recovery copy could not be saved."
                        )
                        snapshot_failed = True
            return (
                Path(play_dir),
                readiness,
                dict(metadata),
                publish_result,
                int(source_revision),
                str(requested_fingerprint),
                _forge_source_fingerprint(self.project_root),
                snapshot_path,
                snapshot_failed,
            )

        self._update_publication_activity(
            "Uploading files and verifying the public page…"
        )
        self._start_operation(
            "Publishing letter…",
            task,
            self._publish_completed,
            "Publishing failed. The local build was preserved.",
            on_failure=lambda: self._abort_publication_activity("publish"),
        )

    def _publish_completed(self, result: object) -> None:
        values = tuple(result)
        play_dir, readiness, metadata, publish_result = values[:4]
        snapshot_path = Path(values[7]) if len(values) >= 9 and values[7] else None
        snapshot_failed = bool(values[8]) if len(values) >= 9 else False
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
        self._refresh_catalog_entry(Path(play_dir))
        if snapshot_path is not None:
            self._refresh_catalog_entry(snapshot_path)
        if not getattr(publish_result, "success", False):
            if (
                not source_changed
                and self._queue_github_operation_retry("publishing")
            ):
                return
            self._pending_publish_context = None
            self._pending_publish_retry_attempt = 0
            error_code = str(
                getattr(publish_result, "error_code", "")
            ).strip()
            details = str(getattr(publish_result, "technical_details", ""))
            if details:
                _LOGGER.error("Publishing failed: %s", details)
            self._set_status(
                str(getattr(publish_result, "message", ""))
                or "Publishing failed. The local build was preserved.",
                error=True,
            )
            self._finish_publication_activity("publish")
            self._show_publish_failure(publish_result)
            return
        self._pending_publish_context = None
        self._pending_publish_retry_attempt = 0
        publication = _publication_fields_from_result(publish_result)
        url = publication[PUBLISHED_PAGE_URL_KEY]
        if publication_status(publication) != "published":
            _LOGGER.error("Publisher returned incomplete verification metadata.")
            self._set_status(
                "Publishing completed without a verified public page.",
                error=True,
            )
            self._finish_publication_activity("publish")
            return
        self.settings.update_fields(publication)
        self.published_url_changed.emit(url)
        self.refresh_project_state()
        try:
            update_saved_publication_metadata(
                Path(play_dir),
                self.project_root,
            )
        except Exception:
            _LOGGER.exception(
                "Verified publication metadata could not be saved for %s",
                play_dir,
            )
            self._set_status(
                "The letter is online, but its publication details could not be saved.",
                error=True,
            )
            self._finish_publication_activity("publish")
            return
        self._refresh_catalog_entry(Path(play_dir))
        self._sync_publishing_controls()
        self._set_status(
            "The letter is online, but its local recovery copy could not be saved."
            if snapshot_failed
            else "Published. Publish again to include newer project changes."
            if source_changed
            else "The letter is published.",
            error=snapshot_failed,
        )
        play_ui_sound(UiSound.PUBLISH_COMPLETE)
        self._finish_publication_activity("publish")

    def unpublish_letter(self) -> None:
        if (
            self._busy
            or self._publication_operation
            or self.is_protected_project()
        ):
            return
        metadata = self.settings.snapshot()
        if publication_status(metadata) != "published":
            self._set_status("This letter is not published.")
            return
        confirmation = LetterSmithConfirmationDialog(
            self,
            title="Unpublish Letter",
            question=(
                "Remove this letter from the web? The local letter and all of "
                "its files will remain untouched."
            ),
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
        )
        if confirmation.exec() != QtWidgets.QDialog.Accepted:
            self._set_status("Unpublishing canceled.")
            return
        index = self._current_play_index()
        play_dir = index.parent if index is not None else None

        def task() -> tuple:
            snapshot = self._publication_connection_snapshot()
            return dict(metadata), play_dir, snapshot.session, snapshot.access

        self._begin_publication_activity(
            "unpublish",
            "Preparing to withdraw the online copy…",
        )
        self._start_operation(
            "Preparing to remove the online copy…",
            task,
            self._unpublish_prepared,
            "Unpublishing could not start. The local letter was preserved.",
            on_failure=lambda: self._abort_publication_activity("unpublish"),
        )

    def _unpublish_prepared(self, result: object) -> None:
        metadata, play_dir, session, access = tuple(result)
        self._pending_unpublish_context = (
            dict(metadata),
            Path(play_dir) if play_dir is not None else None,
        )
        self._pending_unpublish_retry_attempt = 0
        if not isinstance(session, GitHubSession):
            self._set_status("Sign in with GitHub to unpublish this letter.", timeout_ms=0)
            self._update_publication_activity("Waiting for GitHub sign-in…")
            self._defer_until_idle(self.sign_in_github)
            return
        snapshot = self._github_service.snapshot
        self._apply_github_state(snapshot)
        if snapshot.state == GitHubConnectionState.RECONNECTING:
            self._set_status(
                "GitHub is temporarily unavailable. Reconnecting automatically…",
                timeout_ms=0,
            )
            self._update_publication_activity(
                "Reconnecting to GitHub automatically…"
            )
            self._schedule_github_reconnect(snapshot)
            return
        if snapshot.state == GitHubConnectionState.GITHUB_UNAVAILABLE:
            self._set_status(
                snapshot.message or "GitHub is temporarily unavailable.",
                error=True,
                timeout_ms=0,
            )
            self._abort_publication_activity("unpublish")
            return
        if not isinstance(access, GitHubPublishingAccess):
            raise TypeError("GitHub returned invalid publishing access details.")
        if not access.ready:
            if not self._begin_github_access_setup(session, access):
                self._abort_publication_activity("unpublish")
            return
        self._update_publication_activity("Starting the secure withdrawal…")

    def _resume_pending_unpublish(self, session: GitHubSession) -> None:
        if self._busy:
            return
        context = self._pending_unpublish_context
        if context is None:
            self._finish_publication_activity("unpublish")
            return
        current_session = self._ready_publication_session("unpublish")
        if current_session is None:
            return
        session = current_session
        metadata, play_dir = context

        def task() -> tuple:
            api = self._github_service.authorized_api(session)
            connection_snapshot = self._github_service.snapshot
            effective_session = connection_snapshot.session or session
            publisher = GitHubPagesPublisher(
                self.project_root,
                effective_session,
                api=api,
                auth_service=self._github_service,
                connection_generation=connection_snapshot.generation,
                cancelled=lambda: (
                    QtCore.QThread.currentThread().isInterruptionRequested()
                ),
            )
            return play_dir, publisher.unpublish(dict(metadata))

        self._update_publication_activity(
            "Removing the public copy and verifying withdrawal…"
        )
        self._start_operation(
            "Removing online copy…",
            task,
            self._unpublish_completed,
            "Unpublishing failed. The local letter was preserved.",
            on_failure=lambda: self._abort_publication_activity("unpublish"),
        )

    def _unpublish_completed(self, result: object) -> None:
        play_dir, unpublish_result = tuple(result)
        if not getattr(unpublish_result, "success", False):
            if self._queue_github_operation_retry("unpublishing"):
                return
            self._pending_unpublish_context = None
            self._pending_unpublish_retry_attempt = 0
            details = str(
                getattr(unpublish_result, "technical_details", "")
            ).strip()
            if details:
                _LOGGER.error("Unpublishing failed: %s", details)
            self._set_status(
                str(getattr(unpublish_result, "message", ""))
                or "Unpublishing failed. The local letter was preserved.",
                error=True,
            )
            self._finish_publication_activity("unpublish")
            self._show_unpublish_failure(unpublish_result)
            return
        self._pending_unpublish_context = None
        self._pending_unpublish_retry_attempt = 0
        self.published_url_changed.emit("")
        self.refresh_project_state()
        if play_dir is not None and Path(play_dir).is_dir():
            try:
                update_saved_publication_metadata(
                    Path(play_dir),
                    self.project_root,
                )
                self._refresh_catalog_entry(Path(play_dir))
            except Exception:
                _LOGGER.exception(
                    "Cleared publication metadata could not be saved for %s",
                    play_dir,
                )
                self._set_status(
                    "The online copy was removed, but the saved letter's publication status could not be updated.",
                    error=True,
                )
                self._finish_publication_activity("unpublish")
                return
        self._sync_publishing_controls()
        self._set_status(
            str(getattr(unpublish_result, "message", ""))
            or "The online copy was removed. The local letter was preserved."
        )
        play_ui_sound(UiSound.REMOVED)
        self._finish_publication_activity("unpublish")

    def _show_unpublish_failure(self, unpublish_result: object) -> None:
        play_ui_sound(UiSound.ERROR)
        details = str(
            getattr(unpublish_result, "technical_details", "")
        ).strip()
        dialog = LetterSmithMessageDialog(
            self,
            title="Letter Was Not Unpublished",
            message=(
                str(getattr(unpublish_result, "message", "")).strip()
                or "The online copy could not be removed."
            ),
            detail=(
                "The local letter and its files were preserved. "
                "You can retry unpublishing."
            ),
            technical_details=details,
        )
        dialog.exec()

    def _show_publish_failure(self, publish_result: object) -> None:
        play_ui_sound(UiSound.ERROR)
        message = (
            str(getattr(publish_result, "message", "")).strip()
            or "Publishing failed. The local letter was preserved."
        )
        error_code = str(getattr(publish_result, "error_code", "")).strip()
        details = str(getattr(publish_result, "technical_details", "")).strip()
        if error_code == "authentication":
            detail = (
                "Sign in with GitHub to continue. The generated local letter was preserved."
            )
            action_text = "Sign in with GitHub"
        elif error_code == "permission":
            detail = (
                "Your GitHub sign-in is valid, but Letter Smith or its managed "
                "publishing project does not have the required access."
            )
            action_text = "Review GitHub Access"
        elif error_code == "app_not_configured":
            detail = (
                "GitHub publishing must be configured by the Letter Smith developer."
            )
            action_text = "Open GitHub Account"
        else:
            action_text = ""
            detail = (
                "The generated local letter was preserved. You can retry publishing."
            )
        dialog = LetterSmithMessageDialog(
            self,
            title="Letter Was Not Published",
            message=message,
            detail=detail,
            technical_details=details,
            action_text=action_text,
        )
        dialog.exec()
        if dialog.action_requested:
            if error_code == "authentication":
                self.sign_in_github()
            elif error_code == "permission":
                self._repair_github_publishing_access()
            else:
                self.show_github_account()

    def _repair_github_publishing_access(self) -> None:
        if self._busy:
            self._defer_until_idle(self._repair_github_publishing_access)
            return
        session = self._github_session
        if not isinstance(session, GitHubSession):
            self.sign_in_github()
            return
        self._start_operation(
            "Checking GitHub publishing access…",
            self._github_service.restore,
            lambda snapshot: self._github_publishing_access_checked(snapshot),
            "GitHub publishing access could not be checked.",
        )

    def _github_publishing_access_checked(
        self,
        snapshot: object,
    ) -> None:
        if not isinstance(snapshot, GitHubConnectionSnapshot):
            raise TypeError("GitHub returned invalid connection state.")
        session = snapshot.session
        access = snapshot.access
        if not isinstance(session, GitHubSession):
            self.sign_in_github()
            return
        if not isinstance(access, GitHubPublishingAccess):
            raise TypeError("GitHub returned invalid publishing access details.")
        if access.ready:
            self._set_status(
                "GitHub access is active. Review the publishing project settings.",
                error=True,
                timeout_ms=0,
            )
            if access.setup_url:
                QtGui.QDesktopServices.openUrl(QUrl(access.setup_url))
            return
        self._begin_github_access_setup(session, access)

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
        record_activity: bool = False,
    ) -> None:
        if not self._flush_prompt_writer_state():
            return
        try:
            update_saved_metadata(
                play_dir,
                self.project_root,
                readiness,
            )
            if record_activity:
                record_saved_letter_activity(play_dir)
            self._refresh_catalog_entry(play_dir)
        except Exception:
            _LOGGER.exception(
                "Playable build metadata could not be updated for %s",
                play_dir,
            )

    def refresh_saved_page_url(self, snapshot: dict | None = None) -> str:
        if snapshot is None:
            snapshot = self.settings.snapshot()
        self.saved_page_url = normalize_published_page_url(
            snapshot.get(PUBLISHED_PAGE_URL_KEY, "")
        )
        self._sync_published_url(snapshot)
        return (
            ""
            if self._published_url_unavailable(snapshot)
            else self.saved_page_url
        )

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

    def _sync_published_url(self, snapshot: dict | None = None) -> None:
        if snapshot is None:
            snapshot = self.settings.snapshot()
        if self.is_protected_project(snapshot):
            available = self._known_valid_publication(snapshot)
            set_control_help(
                self.open_published_btn,
                (
                    "Open this demonstration's local letter preview."
                    if available
                    else "Publish this demonstration to open its local preview."
                ),
            )
            self._update_letter_action_button_states()
            return
        available = self._known_valid_publication(snapshot)
        if available:
            if snapshot is None:
                snapshot = self.settings.snapshot()
            label = publication_expiry_label(
                snapshot.get(PUBLISHED_EXPIRES_AT_KEY, "")
            )
            detail = f" {label}" if label else ""
            set_control_help(
                self.open_published_btn,
                f"Open the saved published letter link in your web browser.{detail} {self.saved_page_url}",
            )
        elif is_publication_expired(snapshot.get(PUBLISHED_EXPIRES_AT_KEY, "")):
            set_control_help(
                self.open_published_btn,
                "This legacy publication has expired; publish the letter again to open it.",
            )
        else:
            disabled = (
                "Enter a valid Published Page URL in Message or publish "
                "the letter to create a link."
            )
            set_control_help(self.open_published_btn, disabled)
        self._update_letter_action_button_states()

    def _published_url_unavailable(self, snapshot: dict | None = None) -> bool:
        if snapshot is None:
            snapshot = self.settings.snapshot()
        expiry = snapshot.get(PUBLISHED_EXPIRES_AT_KEY, "")
        return is_publication_expired(expiry) or is_publication_expiration_malformed(expiry)

    def open_published_letter(self) -> None:
        if self.is_protected_project():
            index = self._current_play_index()
            if (
                not self._protected_published
                or index is None
                or not index.is_file()
            ):
                self._set_status(
                    "Publish this demonstration before opening it.",
                    error=True,
                )
                return
            if not QtGui.QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(index.resolve()))
            ):
                self._set_status(
                    "The demonstration preview could not be opened.",
                    error=True,
                )
                return
            self._set_status("Published demonstration opened.")
            return
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
        if self._shutdown or self._busy:
            return
        self._busy = True
        self._busy_operation = activity
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
        if self._shutdown:
            return
        callback = self._operation_success
        self._operation_success = None
        self._operation_failure = None
        if callback is None:
            if self._publication_operation:
                self._abort_publication_activity(self._publication_operation)
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
            if self._publication_operation:
                self._abort_publication_activity(self._publication_operation)

    @QtCore.Slot(str, str, bool)
    def _operation_failed_on_ui(
        self,
        message: str,
        technical: str,
        user_safe: bool,
    ) -> None:
        if self._shutdown:
            return
        sign_in_cancelled = (
            self._github_sign_in_cancelled
            and message == "GitHub sign-in was canceled."
        )
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
        if self._publication_operation:
            self._abort_publication_activity(self._publication_operation)
        if sign_in_cancelled:
            self._github_sign_in_cancelled = False
            _LOGGER.info("GitHub sign-in canceled by the user.")
            self._set_status("GitHub sign-in canceled.")
            self.request_preview()
            return
        play_ui_sound(UiSound.ERROR)
        _LOGGER.error("Forge operation failed: %s\n%s", message, technical)
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
        if self._shutdown:
            return
        thread = self._worker_thread
        self._worker = None
        self._worker_thread = None
        self._operation_error_message = ""
        self._operation_failure = None
        self._busy = False
        self._busy_operation = ""
        self._set_busy(False)
        self._finish_restore_activity()
        if thread is not None:
            thread.deleteLater()
        refresh_requested = self._preview_refresh_requested
        self._preview_refresh_requested = False
        if refresh_requested and self._tab_active:
            QtCore.QTimer.singleShot(0, self.ensure_preview_current)

    def _set_busy(self, busy: bool) -> None:
        for card in self._saved_cards:
            card.setEnabled(not busy)
        self.saved_archive_recipient.setEnabled(not busy)
        self.saved_archive_list.setEnabled(not busy)
        self.saved_archive_delete.setEnabled(not busy)
        self.load_saved_btn.setEnabled(not busy)
        self.load_stock_btn.setEnabled(not busy)
        self.saved_delete_toggle.setEnabled(not busy)
        self.github_account_btn.setEnabled(not busy)
        self.refresh_readiness()
        self._sync_published_url()
        self._sync_publishing_controls()

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

    def activate_for_tab_change(self) -> None:
        if self._shutdown or self._tab_active:
            return
        self._tab_active = True
        self.refresh_project_state()
        if not self._github_account_checked and not self._github_account_checking:
            self._validate_github_account_async()
        self.ensure_preview_current()
        self.preview_visibility_changed.emit(True)

    def deactivate_for_tab_change(self) -> None:
        if not self._tab_active:
            return
        self._tab_active = False
        self._preview_refresh_requested = False
        self._refresh_timer.stop()
        self._catalog_refresh_timer.stop()
        self._card_layout_timer.stop()
        self._scroll_restore_timer.stop()
        self.saved_panel.hide()
        self.preview_visibility_changed.emit(False)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.activate_for_tab_change()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.deactivate_for_tab_change()
        super().hideEvent(event)

    def shutdown_operations(self, timeout_ms: int | None = None) -> bool:
        deadline = (
            None
            if timeout_ms is None
            else monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        )
        cover_timeout_ms = (
            None
            if deadline is None
            else max(0, int((deadline - monotonic()) * 1000))
        )
        if not self._stop_cover_decode_tasks(cover_timeout_ms):
            return False
        catalog_timeout_ms = (
            None
            if deadline is None
            else max(0, int((deadline - monotonic()) * 1000))
        )
        if not self._stop_catalog_reconcile_tasks(catalog_timeout_ms):
            return False
        threads = tuple(
            thread
            for thread in (
                self._worker_thread,
                self._github_account_thread,
            )
            if thread is not None and thread.isRunning()
        )
        for thread in threads:
            thread.requestInterruption()
            thread.quit()
        stopped = True
        for thread in threads:
            remaining_ms = (
                None
                if deadline is None
                else max(0, int((deadline - monotonic()) * 1000))
            )
            thread_stopped = (
                thread.wait()
                if remaining_ms is None
                else thread.wait(remaining_ms)
            )
            stopped = bool(thread_stopped) and stopped
        if stopped:
            self._worker = None
            self._worker_thread = None
            self._github_account_worker = None
            self._github_account_thread = None
            self._operation_success = None
            self._operation_failure = None
            self._operation_error_message = ""
            self._busy = False
            self._busy_operation = ""
            self._finish_restore_activity()
            finish_publication = getattr(
                self,
                "_finish_publication_activity",
                None,
            )
            if callable(finish_publication):
                finish_publication()
        return stopped

    def shutdown(self, timeout_ms: int | None = 5000) -> bool:
        """Stop Forge-owned work, callbacks, timers, watchers, and popups."""
        if self._shutdown:
            return True
        if not self.shutdown_operations(timeout_ms=timeout_ms):
            return False

        self.deactivate_for_tab_change()
        timers = (
            self._card_layout_timer,
            self._refresh_timer,
            self._status_timer,
            self._catalog_refresh_timer,
            self._scroll_restore_timer,
            self._metadata_timer,
        )
        for timer in (*timers, getattr(self, "_github_reconnect_timer", None)):
            if timer is None:
                continue
            timer.stop()

        if self._pending_metadata_update is not None:
            try:
                self._run_pending_metadata_update()
            except Exception:
                _LOGGER.exception("Pending Forge metadata could not be finalized.")

        self._shutdown = True
        github_service = getattr(self, "_github_service", None)
        github_listener = getattr(self, "_github_state_listener", None)
        if github_service is not None and github_listener is not None:
            github_service.unsubscribe(github_listener)
        self._preview_refresh_requested = False
        self._pending_metadata_update = None
        self._pending_publish_context = None
        self._pending_unpublish_context = None
        self._pending_publish_retry_attempt = 0
        self._pending_unpublish_retry_attempt = 0
        self._operation_success = None
        self._operation_failure = None
        self._operation_error_message = ""
        self._finish_restore_activity()
        finish_publication = getattr(
            self,
            "_finish_publication_activity",
            None,
        )
        if callable(finish_publication):
            finish_publication()

        watched = (
            self._catalog_watcher.directories()
            + self._catalog_watcher.files()
        )
        if watched:
            self._catalog_watcher.removePaths(watched)
        self._catalog_watcher.blockSignals(True)
        try:
            self.settings.changed.disconnect(self._on_settings_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self._settings_refresh_requested.disconnect(self.schedule_refresh)
        except (RuntimeError, TypeError):
            pass
        curtain_refresh = getattr(
            self,
            "_curtain_style_refresh_requested",
            None,
        )
        curtain_styles = getattr(self, "curtain_styles", None)
        if curtain_refresh is not None:
            try:
                curtain_refresh.disconnect(self._curtain_style_changed)
            except (RuntimeError, TypeError):
                pass
        if curtain_styles is not None:
            try:
                curtain_styles.styleCommitted.disconnect(
                    self._curtain_style_committed
                )
            except (RuntimeError, TypeError):
                pass
            if getattr(self, "_owns_curtain_styles", False):
                curtain_styles.close()

        self.saved_panel.close()
        self.github_account_dialog.close()
        github_auth_dialog = getattr(
            self,
            "github_auth_dialog",
            getattr(self, "github_device_dialog", None),
        )
        if github_auth_dialog is not None:
            github_auth_dialog.finish()
        self.readiness_window.shutdown()
        return True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self.shutdown(timeout_ms=5000):
            event.ignore()
            self._set_status(
                "Finish the current Forge operation before closing.",
                error=True,
                timeout_ms=0,
            )
            return
        super().closeEvent(event)
