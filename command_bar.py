from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

from config import canonical_play_root
from saved_letters import SavedLetter, SavedLetterCatalog
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
    SettingsStore,
    normalize_published_page_url,
)


_LOGGER = logging.getLogger(__name__)
_COMMAND_BAR_INSTANCE: Optional["CommandBarWindow"] = None


@dataclass(frozen=True)
class CommandBarData:
    recipient_name: str
    recipient_title: str
    local_preview_path: Path | None
    published_url: str


def _valid_preview_dir(value: object) -> Path | None:
    if not value:
        return None
    try:
        source = Path(str(value)).expanduser()
        if source.is_symlink():
            return None
        candidate = source.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    index = candidate / "index.html"
    if (
        candidate.is_dir()
        and not candidate.is_symlink()
        and index.is_file()
        and not index.is_symlink()
    ):
        return candidate
    return None


def _metadata_value(metadata: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = str(metadata.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _matching_saved_preview(
    catalog: SavedLetterCatalog,
    recipient: str,
    title: str,
) -> SavedLetter | None:
    if not recipient or not title:
        return None
    recipient_key = recipient.casefold()
    title_key = title.casefold()
    return next(
        (
            entry
            for entry in catalog.list_entries()
            if not entry.recovery
            and entry.recipient.casefold() == recipient_key
            and entry.title.casefold() == title_key
        ),
        None,
    )


def build_command_bar_data(
    project_root: str | Path,
) -> CommandBarData:
    """Capture the completed letter before Command reset clears live settings."""
    root = Path(project_root).resolve()
    settings = SettingsStore(root).snapshot()
    settings_recipient = str(settings.get("recipient_name", "") or "").strip()
    settings_title = str(settings.get("recipient_title", "") or "").strip()
    settings_url = normalize_published_page_url(
        settings.get("published_page_url", "")
    )
    catalog = SavedLetterCatalog(root)

    active_dir = _valid_preview_dir(settings.get(ACTIVE_PLAY_DIR_KEY, ""))
    if active_dir is not None:
        try:
            active_dir.relative_to(canonical_play_root(root).resolve())
        except ValueError:
            _LOGGER.warning(
                "Ignoring active Command Bar path outside output/Play: %s",
                active_dir,
            )
            active_dir = None
    matching_entry = None if active_dir is not None else _matching_saved_preview(
        catalog,
        settings_recipient,
        settings_title,
    )
    play_dir = active_dir or (matching_entry.path if matching_entry else None)
    metadata = catalog.metadata(play_dir) if play_dir is not None else {}

    recipient = _metadata_value(
        metadata,
        "recipient_name",
        "recipient_display_name",
    ) or (matching_entry.recipient if matching_entry else "") or settings_recipient
    title = _metadata_value(
        metadata,
        "recipient_title",
        "title",
    ) or (matching_entry.title if matching_entry else "") or settings_title
    published_url = normalize_published_page_url(
        _metadata_value(metadata, "published_page_url")
        or (matching_entry.published_url if matching_entry else "")
        or settings_url
    )

    return CommandBarData(
        recipient_name=recipient or "Recipient unavailable",
        recipient_title=title or "Untitled Letter",
        local_preview_path=(play_dir / "index.html") if play_dir else None,
        published_url=published_url,
    )


class _DragRegion(QtWidgets.QFrame):
    def __init__(self, owner: "CommandBarWindow", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._drag_offset: QtCore.QPoint | None = None
        self._paint_fallback_background = False

    def set_fallback_background(self, enabled: bool) -> None:
        self._paint_fallback_background = bool(enabled)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._paint_fallback_background:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QtGui.QColor(0, 0, 0, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        surface = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(surface, 14.0, 14.0)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            while child is not None and child is not self:
                if isinstance(child, QtWidgets.QAbstractButton):
                    super().mousePressEvent(event)
                    return
                child = child.parentWidget()
            self._drag_offset = (
                event.globalPosition().toPoint() - self._owner.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            global_position = event.globalPosition().toPoint()
            self._owner.move_within_screen(
                global_position - self._drag_offset,
                global_position=global_position,
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class _ElidingLabel(QtWidgets.QLabel):
    def __init__(
        self,
        text: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._update_elision()

    @property
    def full_text(self) -> str:
        return self._full_text

    def _update_elision(self) -> None:
        available = max(0, self.contentsRect().width())
        displayed = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            available,
        )
        super().setText(displayed)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._update_elision()
        super().resizeEvent(event)


class CommandBarWindow(QtWidgets.QWidget):
    TARGET_HEIGHT = 76
    MIN_WIDTH = 420
    MAX_WIDTH = 560

    def __init__(
        self,
        data: CommandBarData,
        project_root: str | Path,
        *,
        screen: QtGui.QScreen | None = None,
    ) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        recipient_name = str(data.recipient_name or "").strip()
        recipient_title = str(data.recipient_title or "").strip()
        self.data = CommandBarData(
            recipient_name=recipient_name or "Recipient unavailable",
            recipient_title=recipient_title or "Untitled Letter",
            local_preview_path=data.local_preview_path,
            published_url=normalize_published_page_url(data.published_url),
        )
        self.project_root = Path(project_root).resolve()
        self._screen_hint = screen
        self._closing = False
        self._quit_on_close = True
        self._copy_timer = QtCore.QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(2000)
        self._copy_timer.timeout.connect(self._restore_copy_button)
        self._copy_icon: QtGui.QIcon | None = None
        self._copy_text = ""
        self._movie: QtGui.QMovie | None = None
        self._background_native_size = QtCore.QSize()
        self._static_background = QtGui.QPixmap()
        self._background_mode = "fallback"

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("CommandBarWindow")
        self.setWindowTitle("Letter Smith Command Bar")

        self._background = QtWidgets.QLabel(self)
        self._background.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._background.setScaledContents(False)

        self._surface = _DragRegion(self, self)
        self._surface.setObjectName("CommandBarSurface")
        self._surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._surface.setStyleSheet(
            "QFrame#CommandBarSurface{background:transparent;border:none;}"
        )

        self._build_background()
        self._build_controls()
        self._set_size_from_background()
        self._position_on_screen()

    @property
    def movie(self) -> QtGui.QMovie | None:
        return self._movie

    def _build_background(self) -> None:
        icons_dir = self.project_root / "gallery" / "app" / "icons"
        gif_path = icons_dir / "bloodsnow.gif"
        movie = QtGui.QMovie(str(gif_path))
        if gif_path.is_file() and movie.isValid():
            movie.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
            if movie.jumpToFrame(0):
                self._background_native_size = movie.currentPixmap().size()
            movie.frameChanged.connect(self._update_movie_frame)
            self._movie = movie
            self._background_mode = "gif"
            return
        if gif_path.is_file():
            _LOGGER.warning("Command Bar GIF is invalid: %s", gif_path)
        png_path = icons_dir / "command_bar.png"
        pixmap = QtGui.QPixmap(str(png_path)) if png_path.is_file() else QtGui.QPixmap()
        if not pixmap.isNull():
            self._static_background = pixmap
            self._background_native_size = pixmap.size()
            self._background.setPixmap(pixmap)
            self._background_mode = "png"
            return
        _LOGGER.info("Command Bar decorative background is unavailable: %s", gif_path)
        self._surface.set_fallback_background(True)

    def _build_controls(self) -> None:
        layout = QtWidgets.QHBoxLayout(self._surface)
        layout.setContentsMargins(16, 7, 8, 7)
        layout.setSpacing(3)

        info = _DragRegion(self, self._surface)
        info.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        info.setMinimumWidth(185)
        info.setMaximumWidth(300)
        info_layout = QtWidgets.QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 4, 0)
        info_layout.setSpacing(0)

        self.recipient_label = _ElidingLabel(self.data.recipient_name, info)
        recipient_font = QtGui.QFont("Segoe UI", 12)
        recipient_font.setWeight(QtGui.QFont.Weight.DemiBold)
        self.recipient_label.setFont(recipient_font)
        self.recipient_label.setStyleSheet("color:#f0fbff;")
        self.title_label = _ElidingLabel(self.data.recipient_title, info)
        self.title_label.setFont(QtGui.QFont("Segoe UI", 10))
        self.title_label.setStyleSheet("color:#a6c2d7;")
        for label in (self.recipient_label, self.title_label):
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        info_layout.addWidget(self.recipient_label)
        info_layout.addWidget(self.title_label)
        layout.addWidget(info, 1)

        self.preview_button = self._make_button(
            "preview.png", "◉", "Preview", "Preview Letter", self._preview_letter
        )
        self.open_button = self._make_button(
            "open.png", "↗", "Open", "Open Published Letter", self._open_published
        )
        self.copy_button = self._make_button(
            "copy.png", "⧉", "Copy", "Copy Published Link", self._copy_published
        )
        self._copy_icon = self.copy_button.icon()
        self._copy_text = self.copy_button.text()
        self.minimize_button = self._make_button(
            "minimize.png", "—", "Minimize", "Minimize", self.showMinimized
        )
        self.close_button = self._make_button(
            "close.png", "×", "Close", "Close", self.close
        )
        for button in (
            self.preview_button,
            self.open_button,
            self.copy_button,
            self.minimize_button,
            self.close_button,
        ):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        preview_available = self._preview_path() is not None
        self._set_button_available(
            self.preview_button,
            preview_available,
            "Preview Letter",
            "Local preview is unavailable.",
        )
        url_available = bool(self.data.published_url)
        for button, tooltip in (
            (self.open_button, "Open Published Letter"),
            (self.copy_button, "Copy Published Link"),
        ):
            self._set_button_available(
                button,
                url_available,
                tooltip,
                "This letter does not have a published link.",
            )

    def _make_button(
        self,
        asset_name: str,
        unicode_fallback: str,
        text_fallback: str,
        tooltip: str,
        callback,
    ) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton(self._surface)
        button.setFixedSize(36, 36)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setAutoRaise(True)
        button.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:#d9f5ff;padding:2px;}"
            "QToolButton:hover{background:rgba(95,190,235,0.22);border-radius:8px;}"
            "QToolButton:pressed{background:rgba(15,45,72,0.55);border-radius:8px;}"
            "QToolButton:disabled{color:#58738b;background:transparent;}"
        )
        path = self.project_root / "gallery" / "app" / "icons" / asset_name
        pixmap = QtGui.QPixmap(str(path)) if path.is_file() else QtGui.QPixmap()
        if not pixmap.isNull():
            button.setIcon(QtGui.QIcon(pixmap))
            button.setIconSize(QtCore.QSize(27, 27))
        else:
            button.setText(unicode_fallback or text_fallback)
            button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _set_button_available(
        button: QtWidgets.QToolButton,
        available: bool,
        enabled_tooltip: str,
        disabled_tooltip: str,
    ) -> None:
        effect = QtWidgets.QGraphicsOpacityEffect(button)
        effect.setOpacity(1.0 if available else 0.38)
        button.setGraphicsEffect(effect)
        button.setEnabled(available)
        button.setToolTip(enabled_tooltip if available else disabled_tooltip)

    def _set_size_from_background(self) -> None:
        native = self._background_native_size
        if native.isValid() and native.height() > 0:
            width = round(self.TARGET_HEIGHT * native.width() / native.height())
        else:
            width = 480
        self.setFixedSize(max(self.MIN_WIDTH, min(self.MAX_WIDTH, width)), self.TARGET_HEIGHT)

    def _position_on_screen(self) -> None:
        screen = self._screen_hint or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = geometry.left() + max(0, (geometry.width() - self.width()) // 2)
        y = geometry.bottom() - self.height() - 38
        self.move_within_screen(QtCore.QPoint(x, y), screen=screen)

    def move_within_screen(
        self,
        requested: QtCore.QPoint,
        *,
        global_position: QtCore.QPoint | None = None,
        screen: QtGui.QScreen | None = None,
    ) -> None:
        target_screen = screen
        if target_screen is None and global_position is not None:
            target_screen = QtGui.QGuiApplication.screenAt(global_position)
        target_screen = target_screen or self.screen() or self._screen_hint
        target_screen = target_screen or QtGui.QGuiApplication.primaryScreen()
        if target_screen is None:
            self.move(requested)
            return
        geometry = target_screen.availableGeometry()
        max_x = max(geometry.left(), geometry.right() - self.width() + 1)
        max_y = max(geometry.top(), geometry.bottom() - self.height() + 1)
        self.move(
            min(max(requested.x(), geometry.left()), max_x),
            min(max(requested.y(), geometry.top()), max_y),
        )

    def _update_movie_frame(self, _frame: int = 0) -> None:
        if self._movie is None:
            return
        frame = self._movie.currentPixmap()
        if frame.isNull():
            return
        self._background.setPixmap(
            frame.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        )
        self._background.setGeometry(self.rect())
        self._background.lower()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QtGui.QColor(0, 0, 0, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        surface = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(surface, 14.0, 14.0)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._background.setGeometry(self.rect())
        if self._movie is None and not self._static_background.isNull():
            self._background.setPixmap(
                self._static_background.scaled(
                    self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
            )
        self._surface.setGeometry(self.rect())
        self._surface.raise_()
        super().resizeEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._closing = False
        self._position_on_screen()
        if self._movie is not None:
            self._movie.start()
            self._update_movie_frame()
        super().showEvent(event)

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def abort_launch(self) -> None:
        self._quit_on_close = False
        self.close()

    def _preview_path(self) -> Path | None:
        value = self.data.local_preview_path
        if value is None:
            return None
        try:
            path = Path(value)
            if path.is_symlink() or not path.is_file():
                return None
            return path.resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _preview_letter(self) -> None:
        path = self._preview_path()
        if path is None:
            return
        if not QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            _LOGGER.warning("Command Bar could not open local preview: %s", path)
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Could not open the local preview.", self)

    def _open_published(self) -> None:
        if not self.data.published_url:
            return
        if not QtGui.QDesktopServices.openUrl(QUrl(self.data.published_url)):
            _LOGGER.warning(
                "Command Bar could not open published URL: %s",
                self.data.published_url,
            )
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Could not open the published letter.", self)

    def _copy_published(self) -> None:
        if not self.data.published_url:
            return
        clipboard = QtWidgets.QApplication.clipboard()
        try:
            clipboard.setText(self.data.published_url)
        except Exception:
            _LOGGER.exception("Command Bar could not copy the published URL.")
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "Could not copy the published link.", self)
            return
        self.copy_button.setIcon(QtGui.QIcon())
        self.copy_button.setText("✓")
        self.copy_button.setToolTip("Link copied.")
        self._copy_timer.start()

    def _restore_copy_button(self) -> None:
        self.copy_button.setText(self._copy_text)
        if self._copy_icon is not None and not self._copy_icon.isNull():
            self.copy_button.setIcon(self._copy_icon)
        else:
            self.copy_button.setIcon(QtGui.QIcon())
            self.copy_button.setText(self._copy_text or "⧉")
        self.copy_button.setToolTip("Copy Published Link")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        self._copy_timer.stop()
        if self._movie is not None:
            self._movie.stop()
            self._movie.setFileName("")
        event.accept()
        if self._quit_on_close:
            QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)


def launch_command_bar(
    data: CommandBarData,
    project_root: str | Path,
    *,
    screen: QtGui.QScreen | None = None,
    show: bool = True,
) -> CommandBarWindow:
    """Show one top-level Command Bar without starting another process."""
    global _COMMAND_BAR_INSTANCE
    existing = _COMMAND_BAR_INSTANCE
    if existing is not None:
        try:
            if show:
                existing.present()
            return existing
        except RuntimeError:
            _COMMAND_BAR_INSTANCE = None

    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication is required before launching Command Bar")
    window = CommandBarWindow(data, project_root, screen=screen)
    _COMMAND_BAR_INSTANCE = window
    window.destroyed.connect(lambda: _clear_command_bar_instance(window))
    if show:
        restore_quit_on_last = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        window.present()
        QtCore.QTimer.singleShot(
            0,
            lambda: app.setQuitOnLastWindowClosed(restore_quit_on_last),
        )
    return window


def _clear_command_bar_instance(window: CommandBarWindow) -> None:
    global _COMMAND_BAR_INSTANCE
    if _COMMAND_BAR_INSTANCE is window:
        _COMMAND_BAR_INSTANCE = None


__all__ = [
    "CommandBarData",
    "CommandBarWindow",
    "build_command_bar_data",
    "launch_command_bar",
]
