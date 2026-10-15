# =============================================================================================
# File: sound_tab.py
# Purpose: Single-track-first Sound tab with optional playlists and archive
# =============================================================================================

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from time import monotonic
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from audio_tools import (
    AudioToolError,
    audio_tool_filename,
    convert_to_mp3,
    probe_audio,
    toolchain_available,
)

from config import (
    MAX_AUDIO_MB,
    SETTINGS_FILE,
    STARTING_VOLUME,
    USER_SOUNDS_DIR,
)
from image_button import (
    ArtworkButton as ThemedArtworkButton,
    set_control_invisible,
)
from project_paths import ProjectPathResolver, application_paths
from project_save import ProjectSaveService
from project_state import ProjectStateController
from project_session import peek_project_session
from recent_media import recent_media, remember_media

from sound_model import (
    ProjectSoundState,
    TrackRecord,
    analysis_cache_path,
    analysis_dir,
    atomic_write_json,
    current_music_path,
    display_title_from_name,
    ensure_sound_dirs,
    hash_file,
    library_path,
    load_library,
    load_project_state,
    originals_dir,
    processed_dir,
    project_sound_path,
    resolve_track_path,
    safe_filename,
    save_library,
    save_project_state,
    sync_current_compatibility,
    utc_now_text,
)

from sound_preview import SoundPreviewWidget
from performance_trace import performance_timed
from transactional_io import file_change_token
from ui_dialogs import (
    LetterSmithConfirmationDialog,
    LetterSmithInputDialog,
    show_lettersmith_message,
)
from ui_sounds import UiSound, play_ui_sound
from ui_theme import (
    BUTTON_GEOMETRY_SCALE_PROPERTY,
    ROW_LAYOUT_SPACING,
    SECTION_LAYOUT_SPACING,
    SOUND_PAGE_LAYOUT,
    ButtonTier,
    apply_button_tier,
    apply_tab_heading_style,
    ensure_button_text_fits,
)


_LOGGER = logging.getLogger(__name__)
AudioAnalysisManager = None
_ANALYSIS_RUNTIME_STATUS: Optional[Callable[..., tuple[bool, str]]] = None
_ANALYSIS_IMPORT_ATTEMPTED = False
_ANALYSIS_IMPORT_ERROR = ""


def analysis_runtime_status(project_root=None) -> tuple[bool, str]:
    """Load the optional NumPy/FFmpeg analysis stack only when playback needs it."""
    global AudioAnalysisManager
    global _ANALYSIS_RUNTIME_STATUS
    global _ANALYSIS_IMPORT_ATTEMPTED
    global _ANALYSIS_IMPORT_ERROR
    if not _ANALYSIS_IMPORT_ATTEMPTED:
        _ANALYSIS_IMPORT_ATTEMPTED = True
        try:
            from sound_analyzer import (
                AudioAnalysisManager as manager_type,
                analysis_runtime_status as status_function,
            )

            AudioAnalysisManager = manager_type
            _ANALYSIS_RUNTIME_STATUS = status_function
        except Exception as error:
            _ANALYSIS_IMPORT_ERROR = str(error)
            _LOGGER.warning("Sound analysis unavailable: %s", error)
    if _ANALYSIS_RUNTIME_STATUS is None:
        return (
            False,
            _ANALYSIS_IMPORT_ERROR
            or "Sound analysis module could not be imported.",
        )
    return _ANALYSIS_RUNTIME_STATUS(project_root)


VALID_AUDIO_EXTS = {
    ".mp3",
    ".wav",
    ".ogg",
    ".aac",
    ".m4a",
    ".flac",
}

ANALYSIS_SETTINGS_KEY = "enable_sound_analysis"
LAST_MUSIC_FOLDER_KEY = "last_music_folder"
CROSSFADE_MS = 1000
LOOP_DELAY_MS = 1200
_ARTWORK_ALPHA_THRESHOLD = 10
_VISIBLE_ALPHA_TABLE = bytes(
    0 if alpha <= _ARTWORK_ALPHA_THRESHOLD else 1
    for alpha in range(256)
)


def _format_duration(
    seconds: float,
) -> str:
    total = max(
        0,
        int(
            round(
                seconds
            )
        ),
    )

    return (
        f"{total // 60}:"
        f"{total % 60:02d}"
    )


def _downloads_directory() -> str:
    location = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.DownloadLocation
    )
    if location:
        return location

    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


def _format_ms(
    milliseconds: int,
) -> str:
    return _format_duration(
        max(
            0,
            int(
                milliseconds
            ),
        )
        / 1000.0
    )


def _atomic_copy(
    source: Path,
    destination: Path,
) -> None:
    source_path = Path(
        source
    ).resolve()

    destination_path = Path(
        destination
    ).resolve()

    if not source_path.is_file():
        raise FileNotFoundError(
            f"Audio file does not exist: {source_path}"
        )

    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=str(
            destination_path.parent
        ),
    )

    os.close(
        file_descriptor
    )

    temporary_path = Path(
        temporary_name
    )

    try:
        with (
            source_path.open(
                "rb"
            ) as read_handle,
            temporary_path.open(
                "wb"
            ) as write_handle,
        ):
            shutil.copyfileobj(
                read_handle,
                write_handle,
                1024 * 1024,
            )

            write_handle.flush()

            os.fsync(
                write_handle.fileno()
            )

        os.replace(
            temporary_path,
            destination_path,
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


def _read_settings(
    project_root: Path,
) -> dict:
    path = (
        project_root
        / SETTINGS_FILE
    )

    try:
        if path.is_file():
            payload = json.loads(
                path.read_text(
                    encoding="utf-8",
                )
            )

        else:
            payload = {}

    except (
        OSError,
        json.JSONDecodeError,
    ):
        payload = {}

    if isinstance(
        payload,
        dict,
    ):
        return payload

    return {}


def _write_settings(
    project_root: Path,
    settings: dict,
) -> None:
    atomic_write_json(
        project_root
        / SETTINGS_FILE,
        settings,
    )


def _analysis_requested(
    project_root: Path,
) -> bool:
    return (
        _read_settings(
            project_root
        ).get(
            ANALYSIS_SETTINGS_KEY
        )
        is True
    )


class CleanSlider(
    QtWidgets.QSlider
):
    """
    Horizontal slider without the platform's inactive gray rail.
    """

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            Qt.Horizontal,
            parent,
        )
        self._theme_tokens: object | None = None

        self.setAttribute(
            Qt.WA_TranslucentBackground,
            True,
        )

        self.setMinimumHeight(
            28
        )

        self.setMouseTracking(
            True
        )

    def apply_theme_assets(self, service: object | None = None) -> None:
        self._theme_tokens = getattr(service, "tokens", None)
        self.update()

    def sizeHint(
        self,
    ) -> QtCore.QSize:
        hint = super().sizeHint()

        return QtCore.QSize(
            max(
                120,
                hint.width(),
            ),
            28,
        )

    def paintEvent(
        self,
        event: QtGui.QPaintEvent,
    ) -> None:
        del event

        painter = QtGui.QPainter(
            self
        )

        painter.setRenderHint(
            QtGui.QPainter.Antialiasing,
            True,
        )

        radius = 8.0
        left = radius + 1.0

        right = max(
            left,
            float(
                self.width()
            )
            - radius
            - 1.0,
        )

        span = max(
            0.0,
            right - left,
        )

        minimum = self.minimum()
        maximum = self.maximum()

        if maximum <= minimum:
            ratio = 0.0

        else:
            ratio = (
                self.value()
                - minimum
            ) / float(
                maximum
                - minimum
            )

        ratio = max(
            0.0,
            min(
                1.0,
                ratio,
            ),
        )

        center_x = (
            left
            + span * ratio
        )

        center_y = (
            self.height()
            / 2.0
        )

        tokens = self._theme_tokens
        active_color = QtGui.QColor(
            str(getattr(tokens, "primary", "#00c8ff"))
        )
        active_color.setAlpha(210 if self.isEnabled() else 90)

        handle_color = QtGui.QColor(
            str(getattr(tokens, "accent", "#7f9099"))
        )
        handle_color.setAlpha(255 if self.isEnabled() else 120)

        outline_color = QtGui.QColor(
            str(getattr(tokens, "control_border", "#58636a"))
        )
        outline_color.setAlpha(255 if self.isEnabled() else 110)

        if center_x > left:
            pen = QtGui.QPen(
                active_color,
                3.0,
                Qt.SolidLine,
                Qt.RoundCap,
            )

            painter.setPen(
                pen
            )

            painter.drawLine(
                QtCore.QPointF(
                    left,
                    center_y,
                ),
                QtCore.QPointF(
                    center_x,
                    center_y,
                ),
            )

        painter.setPen(
            QtGui.QPen(
                outline_color,
                1.0,
            )
        )

        painter.setBrush(
            handle_color
        )

        painter.drawEllipse(
            QtCore.QPointF(
                center_x,
                center_y,
            ),
            radius,
            radius,
        )

class ArtworkButton(QtWidgets.QPushButton):
    """PNG-backed standard-tier button."""

    def __init__(
        self,
        text: str,
        artwork_path: str | Path,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            text,
            parent,
        )

        requested_path = Path(
            artwork_path
        )
        self._baseline_artwork_path = requested_path.resolve()
        self._logical_artwork_name = (
            Path("buttons")
            / self._baseline_artwork_path.name
        )
        self._artwork_path = self._baseline_artwork_path
        self._artwork = QtGui.QPixmap()
        self._use_artwork = True
        self._set_artwork(
            self._baseline_artwork_path
        )

        self._scaled_artwork_cache: dict[
            tuple[int, int],
            QtGui.QPixmap,
        ] = {}

        self.setCursor(
            Qt.PointingHandCursor
        )
        apply_button_tier(self, ButtonTier.STANDARD)

        self.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: #f2fbff;
            }
            """
        )

    def _set_artwork(
        self,
        artwork_path: str | Path,
    ) -> None:
        path = Path(
            artwork_path
        )
        if (
            path == self._artwork_path
            and not self._artwork.isNull()
        ):
            return
        artwork = QtGui.QPixmap(
            str(path)
        )
        if not artwork.isNull():
            artwork = self._crop_transparent_edges(
                artwork
            )
        self._artwork_path = path
        self._artwork = artwork
        self.update()

    def apply_theme_assets(
        self,
        service: object | None = None,
    ) -> Path:
        """Refresh cloud artwork from the active theme."""
        theme_service = service
        if theme_service is None:
            host = self.parentWidget()
            window = (
                host.window()
                if host is not None
                else self.window()
            )
            theme_service = getattr(
                window,
                "theme_service",
                None,
            )

        artwork_path = self._baseline_artwork_path
        self._use_artwork = bool(
            getattr(
                getattr(theme_service, "current", None),
                "uses_image_buttons",
                True,
            )
        )
        if not self._use_artwork:
            self._artwork_path = artwork_path
            self._artwork = QtGui.QPixmap()
            self._scaled_artwork_cache.clear()
            self.setStyleSheet("")
            self.update()
            return self._artwork_path

        resolver = getattr(
            theme_service,
            "resolve_asset",
            None,
        )
        if callable(resolver):
            try:
                candidate = Path(
                    resolver(
                        self._logical_artwork_name.as_posix()
                    )
                )
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                candidate = artwork_path
            if candidate.is_file():
                artwork_path = candidate

        self._set_artwork(
            artwork_path
        )
        self.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: #f2fbff;
            }
            """
        )
        return self._artwork_path

    @property
    def artwork_path(self) -> Path:
        return self._artwork_path

    @staticmethod
    def _crop_transparent_edges(
        pixmap: QtGui.QPixmap,
    ) -> QtGui.QPixmap:
        """
        Remove transparent space surrounding the PNG artwork.
        """

        image = pixmap.toImage().convertToFormat(
            QtGui.QImage.Format_RGBA8888
        )

        width = image.width()
        height = image.height()

        if width <= 0 or height <= 0:
            return pixmap

        alpha_mask = bytes(
            image.constBits()
        )[3::4].translate(
            _VISIBLE_ALPHA_TABLE
        )

        first_visible = alpha_mask.find(
            b"\x01"
        )

        if first_visible < 0:
            return pixmap

        last_visible = alpha_mask.rfind(
            b"\x01"
        )

        top = first_visible // width
        bottom = last_visible // width
        left = width
        right = -1

        for y in range(
            top,
            bottom + 1,
        ):
            row = alpha_mask[
                y * width:
                (y + 1) * width
            ]

            row_left = row.find(
                b"\x01"
            )

            if row_left < 0:
                continue

            left = min(
                left,
                row_left,
            )

            right = max(
                right,
                row.rfind(
                    b"\x01"
                ),
            )

        if (
            right < left
            or bottom < top
        ):
            return pixmap

        crop_rect = QtCore.QRect(
            left,
            top,
            right - left + 1,
            bottom - top + 1,
        )

        cropped_image = image.copy(
            crop_rect
        )

        return QtGui.QPixmap.fromImage(
            cropped_image
        )

    def sizeHint(
        self,
    ) -> QtCore.QSize:
        return self.minimumSize()

    def minimumSizeHint(
        self,
    ) -> QtCore.QSize:
        return self.sizeHint()

    def paintEvent(
        self,
        event: QtGui.QPaintEvent,
    ) -> None:
        if not self._use_artwork:
            super().paintEvent(event)
            return
        del event

        painter = QtGui.QPainter(
            self
        )

        painter.setRenderHint(
            QtGui.QPainter.Antialiasing,
            True,
        )

        painter.setRenderHint(
            QtGui.QPainter.SmoothPixmapTransform,
            True,
        )

        button_rect = self.rect().adjusted(
            0,
            0,
            0,
            0,
        )

        if not self._artwork.isNull():
            artwork_width = max(
                1,
                button_rect.width(),
            )

            artwork_height = max(
                1,
                button_rect.height(),
            )

            target_size = (
                artwork_width,
                artwork_height,
            )

            artwork = self._scaled_artwork_cache.get(
                target_size
            )

            if artwork is None:
                artwork = self._artwork.scaled(
                    artwork_width,
                    artwork_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )

                self._scaled_artwork_cache[
                    target_size
                ] = artwork

            artwork_rect = QtCore.QRect(
                button_rect.center().x()
                - artwork.width() // 2,
                button_rect.center().y()
                - artwork.height() // 2,
                artwork.width(),
                artwork.height(),
            )

            painter.save()

            painter.setClipRect(
                button_rect
            )

            if not self.isEnabled():
                painter.setOpacity(
                    0.42
                )

            elif self.isDown():
                painter.setOpacity(
                    0.78
                )

            else:
                painter.setOpacity(
                    1.0
                )

            painter.drawPixmap(
                artwork_rect,
                artwork,
            )

            painter.restore()

        else:
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(
                        "#38506b"
                    ),
                    1,
                )
            )

            painter.setBrush(
                QtGui.QColor(
                    "#1c2430"
                )
            )

            painter.drawRoundedRect(
                button_rect,
                9,
                9,
            )

        if self.isDown():
            painter.setPen(
                Qt.NoPen
            )

            painter.setBrush(
                QtGui.QColor(
                    0,
                    0,
                    0,
                    55,
                )
            )

            painter.drawRoundedRect(
                button_rect,
                9,
                9,
            )

        elif self.underMouse():
            painter.setPen(
                Qt.NoPen
            )

            painter.setBrush(
                QtGui.QColor(
                    0,
                    220,
                    255,
                    28,
                )
            )

            painter.drawRoundedRect(
                button_rect,
                9,
                9,
            )

        painter.setFont(self.font())

        painter.setPen(
            QtGui.QColor(
                0,
                0,
                0,
                205,
            )
        )

        painter.drawText(
            self.rect().translated(
                1,
                2,
            ),
            Qt.AlignCenter,
            self.text(),
        )

        if self.isEnabled():
            text_color = QtGui.QColor(
                240,
                252,
                255,
            )

        else:
            text_color = QtGui.QColor(
                170,
                180,
                188,
            )

        painter.setPen(
            text_color
        )

        painter.drawText(
            self.rect(),
            Qt.AlignCenter,
            self.text(),
        )

        if self.hasFocus():
            painter.setBrush(
                Qt.NoBrush
            )

            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(
                        0,
                        220,
                        255,
                        220,
                    ),
                    2,
                )
            )

            painter.drawRoundedRect(
                button_rect,
                9,
                9,
            )

# Compatibility export: all live Sound controls use the centralized resolver.
ArtworkButton = ThemedArtworkButton


class SoundLibrary(
    QtCore.QObject
):
    changed = QtCore.Signal()

    def __init__(
        self,
        project_root: Path,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.project_root = Path(
            project_root
        ).resolve()

        ensure_sound_dirs(
            self.project_root
        )

        self.records: dict[
            str,
            TrackRecord,
        ] = load_library(
            self.project_root
        )
        self._available_track_ids_cache: frozenset[str] | None = None

        self._migrate_legacy_processed_files()

    def invalidate_availability(self) -> None:
        self._available_track_ids_cache = None

    def replace_records(self, records: dict[str, TrackRecord]) -> None:
        self.records = dict(records)
        self.invalidate_availability()

    def _migrate_legacy_processed_files(
        self,
    ) -> None:
        known_files = {
            Path(
                record.processed_file
            ).name
            for record
            in self.records.values()
        }

        changed = False

        for path in processed_dir(
            self.project_root
        ).glob(
            "*.mp3"
        ):
            if path.name in known_files:
                continue

            try:
                content_hash = hash_file(
                    path
                )

            except OSError:
                continue

            existing = self.find_by_hash(
                content_hash
            )

            if existing is not None:
                continue

            track_id = content_hash[:16]

            while track_id in self.records:
                track_id = content_hash[
                    :min(
                        64,
                        len(
                            track_id
                        )
                        + 4,
                    )
                ]

            duration = 0.0

            try:
                duration = probe_audio(
                    path
                ).duration_seconds

            except AudioToolError:
                pass

            record = TrackRecord(
                track_id=track_id,
                content_hash=content_hash,
                display_title=display_title_from_name(
                    path.name
                ),
                original_name=path.name,
                original_file="",
                processed_file=path.name,
                duration_seconds=duration,
                added_at=utc_now_text(),
            )

            self.records[
                record.track_id
            ] = record

            changed = True

        if changed:
            save_library(
                self.project_root,
                self.records,
            )
            self.invalidate_availability()

    def all_records(
        self,
        sort_mode: str = "recent",
    ) -> list[TrackRecord]:
        records = list(
            self.records.values()
        )

        if sort_mode == "name":
            return sorted(
                records,
                key=lambda item: (
                    item.display_title.casefold()
                ),
            )

        if sort_mode == "duration":
            return sorted(
                records,
                key=lambda item: (
                    item.duration_seconds,
                    item.display_title.casefold(),
                ),
            )

        return sorted(
            records,
            key=lambda item: item.added_at,
            reverse=True,
        )

    def get(
        self,
        track_id: str,
    ) -> Optional[TrackRecord]:
        return self.records.get(
            str(
                track_id
            )
        )

    def path_for(
        self,
        track_id: str,
    ) -> Optional[Path]:
        record = self.get(
            track_id
        )

        if record is None:
            return None

        normalized_id = str(track_id)
        if normalized_id not in self.available_track_ids({normalized_id}):
            return None

        return resolve_track_path(
            self.project_root,
            record,
        )

    def available_track_ids(
        self,
        track_ids: Optional[set[str]] = None,
    ) -> set[str]:
        """
        Return only archive IDs whose processed audio file still exists.
        """

        if self._available_track_ids_cache is None:
            self._available_track_ids_cache = frozenset(
                track_id
                for track_id, record in self.records.items()
                if resolve_track_path(self.project_root, record).is_file()
            )
        if track_ids is None:
            return set(self._available_track_ids_cache)
        return set(
            self._available_track_ids_cache.intersection(
                str(track_id) for track_id in track_ids
            )
        )

    def is_available(
        self,
        track_id: str,
    ) -> bool:
        return self.path_for(str(track_id)) is not None

    def refresh_availability(self, track_ids: set[str]) -> None:
        """Recheck only the active assignment, retaining the library cache."""
        available = self.available_track_ids()
        for track_id in track_ids:
            record = self.records.get(track_id)
            if record is not None and resolve_track_path(self.project_root, record).is_file():
                available.add(track_id)
            else:
                available.discard(track_id)
        self._available_track_ids_cache = frozenset(available)

    def find_by_hash(
        self,
        content_hash: str,
    ) -> Optional[TrackRecord]:
        for record in self.records.values():
            if (
                record.content_hash
                == content_hash
            ):
                return record

        return None

    def register_imports(
        self,
        payloads: list[dict],
    ) -> list[str]:
        selected: list[str] = []
        changed = False

        for payload in payloads:
            existing_id = str(
                payload.get(
                    "existing_track_id",
                    "",
                )
            )

            if (
                existing_id
                and existing_id
                in self.records
            ):
                selected.append(
                    existing_id
                )

                continue

            record_payload = payload.get(
                "record"
            )

            if not isinstance(
                record_payload,
                dict,
            ):
                continue

            record = TrackRecord.from_dict(
                record_payload
            )

            self.records[
                record.track_id
            ] = record

            selected.append(
                record.track_id
            )

            changed = True

        if changed:
            save_library(
                self.project_root,
                self.records,
            )

            self.invalidate_availability()

            self.changed.emit()

        return selected

    def rename_display_title(
        self,
        track_id: str,
        title: str,
    ) -> None:
        record = self.get(
            track_id
        )

        clean = " ".join(
            str(
                title
            ).split()
        ).strip()

        if (
            record is None
            or not clean
            or record.source_kind == "stock"
        ):
            return

        record.display_title = clean

        save_library(
            self.project_root,
            self.records,
        )

        self.changed.emit()

    def delete_track(
        self,
        track_id: str,
    ) -> bool:
        record = self.records.get(
            track_id
        )

        if record is None or record.source_kind == "stock":
            return False

        processed = resolve_track_path(
            self.project_root,
            record,
        )

        try:
            processed.unlink(
                missing_ok=True
            )

        except OSError as error:
            logging.getLogger(
                __name__
            ).warning(
                "Could not delete processed track %s: %s",
                processed,
                error,
            )

            return False

        self.records.pop(
            track_id,
            None,
        )
        self.invalidate_availability()

        original_path: Optional[Path]

        if record.original_file:
            original_path = (
                originals_dir(
                    self.project_root
                )
                / Path(
                    record.original_file
                ).name
            )

        else:
            original_path = None

        analysis_path = analysis_cache_path(
            self.project_root,
            record.processed_file,
        )

        for path in (
            original_path,
            analysis_path,
        ):
            if path is None:
                continue

            try:
                path.unlink(
                    missing_ok=True
                )

            except OSError:
                pass

        save_library(
            self.project_root,
            self.records,
        )

        self.changed.emit()

        return True


class ProjectSound(
    QtCore.QObject
):
    changed = QtCore.Signal()

    def __init__(
        self,
        project_root: Path,
        library: SoundLibrary,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.project_root = Path(
            project_root
        ).resolve()

        self.library = library

        self.state = load_project_state(self.project_root)
        self._persisted_state = (
            self.state.to_dict()
            if project_sound_path(self.project_root).is_file()
            else None
        )
        self._normalize_available_tracks()
        self._missing_ids = tuple(self.missing_track_ids())

        self._migrate_legacy_current_track()
        self.save()

    def _referenced_track_ids(self) -> set[str]:
        return {
            track_id
            for track_id in (
                self.state.single_track_id,
                self.state.selected_track_id,
                *self.state.playlist,
            )
            if track_id
        }

    def _normalize_available_tracks(self) -> None:
        # Normalize format/duplicate rules without erasing missing resources.
        self.state.normalize()

    def missing_track_ids(self) -> list[str]:
        return [track_id for track_id in self.state.ordered_track_ids()
                if not self.library.is_available(track_id)]

    def _migrate_legacy_current_track(
        self,
    ) -> None:
        if self.state.ordered_track_ids():
            return

        music = current_music_path(
            self.project_root
        )

        if not music.is_file():
            return

        try:
            digest = hash_file(
                music
            )

        except OSError:
            return

        record = self.library.find_by_hash(
            digest
        )

        if (
            record is None
            or self.library.path_for(
                record.track_id
            )
            is None
        ):
            return

        self.state.single_track_id = (
            record.track_id
        )

        self.state.selected_track_id = (
            record.track_id
        )

    def save(
        self,
    ) -> bool:
        self._normalize_available_tracks()
        current_state = self.state.to_dict()
        changed = current_state != self._persisted_state
        if changed:
            self.persist_state()

        sync_current_compatibility(
            self.project_root,
            self.state,
            self.library.records,
        )
        if changed:
            self.changed.emit()
        return changed

    def persist_state(self) -> None:
        """Persist the current assignment and update the no-op write guard."""
        save_project_state(
            self.project_root,
            self.state,
        )
        self._persisted_state = self.state.to_dict()

    def reconcile_missing_files(
        self,
    ) -> bool:
        """Refresh availability without changing the authored assignment."""
        self.library.refresh_availability(self._referenced_track_ids())
        missing = tuple(self.missing_track_ids())
        changed = missing != self._missing_ids
        self._missing_ids = missing
        if changed:
            sync_current_compatibility(
                self.project_root,
                self.state,
                self.library.records,
            )
        return changed

    def reload_from_disk(self) -> bool:
        previous = self.state.to_dict()
        loaded = load_project_state(self.project_root)
        persisted = (
            loaded.to_dict()
            if project_sound_path(self.project_root).is_file()
            else None
        )
        self.state = loaded
        self._normalize_available_tracks()
        current = self.state.to_dict()
        if current != persisted:
            save_project_state(self.project_root, self.state)
        self._persisted_state = current
        sync_current_compatibility(
            self.project_root,
            self.state,
            self.library.records,
        )
        return current != previous

    def set_single(
        self,
        track_id: str,
    ) -> None:
        self.state.mode = "single"

        if (
            self.library.path_for(
                track_id
            )
            is not None
        ):
            self.state.single_track_id = (
                track_id
            )

        else:
            self.state.single_track_id = ""

        self.state.playlist = []

        self.state.selected_track_id = (
            self.state.single_track_id
        )

        self.save()

    def create_playlist(
        self,
    ) -> None:
        if (
            self.library.path_for(
                self.state.single_track_id
            )
            is not None
        ):
            first = (
                self.state
                .single_track_id
            )

        else:
            first = ""

        self.state.mode = "playlist"

        if first:
            self.state.playlist = [
                first
            ]

        else:
            self.state.playlist = []

        self.state.single_track_id = ""
        self.state.selected_track_id = first
        self.state.playlist_expanded = True

        self.save()

    def add_to_playlist(
        self,
        track_ids: list[str],
    ) -> None:
        if self.state.mode != "playlist":
            self.create_playlist()

        for track_id in track_ids:
            if (
                self.library.path_for(
                    track_id
                )
                is not None
                and track_id
                not in self.state.playlist
            ):
                self.state.playlist.append(
                    track_id
                )

        if (
            not self.state.selected_track_id
            and self.state.playlist
        ):
            self.state.selected_track_id = (
                self.state.playlist[0]
            )

        self.save()

    def reorder_playlist(
        self,
        track_ids: list[str],
    ) -> None:
        # Reordering must retain unavailable entries just like playable ones.
        if len(track_ids) != len(self.state.playlist) or set(track_ids) != set(self.state.playlist):
            return
        self.state.playlist = list(track_ids)

        self.save()

    def remove_from_playlist(
        self,
        track_id: str,
    ) -> None:
        was_present = track_id in self.state.playlist
        self.state.playlist = [
            item
            for item
            in self.state.playlist
            if item != track_id
        ]

        if (
            self.state.selected_track_id
            == track_id
        ):
            if self.state.playlist:
                self.state.selected_track_id = (
                    self.state.playlist[0]
                )

            else:
                self.state.selected_track_id = ""

        changed = self.save()
        if was_present and changed:
            play_ui_sound(UiSound.REMOVED)

    def select_track(
        self,
        track_id: str,
    ) -> None:
        if (
            track_id
            in self.state
            .ordered_track_ids()
        ):
            self.state.selected_track_id = (
                track_id
            )

            self.save()

    def convert_to_single(
        self,
        track_id: str = "",
    ) -> None:
        if track_id in self.state.playlist:
            chosen = track_id

        else:
            chosen = (
                self.state
                .selected_track_id
            )

        if (
            self.library.path_for(
                chosen
            )
            is None
        ):
            chosen = next(
                (
                    playlist_track_id
                    for playlist_track_id
                    in self.state.playlist
                    if self.library.path_for(
                        playlist_track_id
                    )
                    is not None
                ),
                "",
            )

        self.set_single(
            chosen
        )

    def clear(
        self,
    ) -> None:
        self.state = ProjectSoundState()
        self.save()

    def remove_usage(
        self,
        track_id: str,
    ) -> None:
        self.state.remove_usage(
            track_id
        )

        self.save()

    def ordered_ids(
        self,
    ) -> list[str]:
        return (
            self.state
            .ordered_track_ids()
        )


class _ImportWorker(
    QtCore.QObject
):
    finished = QtCore.Signal(
        list
    )

    failed = QtCore.Signal(
        str
    )

    def __init__(
        self,
        project_root: Path,
        paths: list[str],
        known_hashes: dict[str, str],
    ) -> None:
        super().__init__()

        self.project_root = (
            project_root
        )

        self.paths = [
            Path(
                path
            ).resolve()
            for path
            in paths
        ]

        self.known_hashes = dict(
            known_hashes
        )

        self._cancelled = False

    def _validated_duration(self, path: Path) -> float:
        """Validate staged audio without starting audible playback."""
        if application_paths().tool_path(audio_tool_filename("ffprobe")).is_file():
            duration = probe_audio(path).duration_seconds
            if not 0 < duration < float("inf"):
                raise AudioToolError("The audio file has no playable duration.")
            return duration
        # MP3 imports historically work without the optional conversion tools.
        # Use the installed playback backend to check those files as well.
        if QtCore.QCoreApplication.instance() is None:
            raise AudioToolError("Audio validation requires the playback backend or bundled FFprobe.")
        player = QMediaPlayer()
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        timer.setInterval(50)
        deadline = monotonic() + 30.0
        result: dict[str, object] = {"duration": None, "error": ""}

        def inspect(*_args) -> None:
            if self._cancelled:
                result["error"] = "Audio import canceled."
            elif player.mediaStatus() == QMediaPlayer.InvalidMedia:
                result["error"] = "The audio file is damaged or unsupported."
            elif player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and player.duration() > 0:
                result["duration"] = player.duration() / 1000.0
            elif monotonic() >= deadline:
                result["error"] = "Audio validation exceeded the 30-second safety limit."
            if result["error"] or result["duration"] is not None:
                loop.quit()

        player.mediaStatusChanged.connect(inspect)
        player.durationChanged.connect(inspect)
        timer.timeout.connect(inspect)
        try:
            player.setSource(QUrl.fromLocalFile(str(path)))
            inspect()
            if not result["error"] and result["duration"] is None:
                timer.start()
                loop.exec()
            if result["error"]:
                raise AudioToolError(str(result["error"]))
            return float(result["duration"])
        finally:
            timer.stop()
            player.stop()
            player.setSource(QUrl())
            player.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(player, QtCore.QEvent.DeferredDelete)

    @QtCore.Slot()
    def run(
        self,
    ) -> None:
        results: list[dict] = []
        created_artifacts: list[Path] = []

        try:
            for source in self.paths:
                if self._cancelled:
                    raise RuntimeError(
                        "Audio import canceled."
                    )

                if not source.is_file():
                    raise FileNotFoundError(
                        f"Audio file does not exist: {source}"
                    )

                if (
                    source.suffix.casefold()
                    not in VALID_AUDIO_EXTS
                ):
                    raise ValueError(
                        f"Unsupported audio format: {source.suffix}"
                    )

                if (
                    source.stat().st_size
                    > MAX_AUDIO_MB
                    * 1024
                    * 1024
                ):
                    raise ValueError(
                        f"{source.name} exceeds the "
                        f"{MAX_AUDIO_MB} MB limit."
                    )

                content_hash = hash_file(
                    source
                )

                existing_id = (
                    self.known_hashes.get(
                        content_hash
                    )
                )

                if existing_id:
                    results.append(
                        {
                            "existing_track_id": (
                                existing_id
                            )
                        }
                    )

                    continue

                track_id = content_hash[:24]

                safe_name = safe_filename(
                    source.name
                )

                original_name = (
                    f"{track_id}__{safe_name}"
                )

                processed_name = (
                    f"{track_id}.mp3"
                )

                original_destination = (
                    originals_dir(
                        self.project_root
                    )
                    / original_name
                )

                processed_destination = (
                    processed_dir(
                        self.project_root
                    )
                    / processed_name
                )

                created_original = (
                    not original_destination
                    .is_file()
                )

                created_processed = (
                    not processed_destination
                    .is_file()
                )

                if created_original:
                    created_artifacts.append(
                        original_destination
                    )

                if created_processed:
                    created_artifacts.append(
                        processed_destination
                    )

                try:
                    staging_root = application_paths(self.project_root).temporary_root
                    staging_root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix="audio-import-", dir=staging_root) as staging:
                        staged_original = Path(staging) / safe_name
                        staged_processed = Path(staging) / "validated.mp3"
                        _atomic_copy(source, staged_original)
                        if hash_file(staged_original) != content_hash:
                            raise AudioToolError("The source changed during import. Please try again.")
                        if source.suffix.casefold() == ".mp3":
                            _atomic_copy(staged_original, staged_processed)
                        else:
                            if not toolchain_available():
                                raise AudioToolError(
                                    "This file needs conversion, "
                                    f"but tools/{audio_tool_filename('ffmpeg')} and "
                                    f"tools/{audio_tool_filename('ffprobe')} are missing."
                                )
                            convert_to_mp3(
                                staged_original, staged_processed,
                                cancel_check=lambda: self._cancelled,
                            )
                        duration = self._validated_duration(staged_processed)
                        if self._cancelled:
                            raise AudioToolError("Audio import canceled.")
                        if created_original:
                            _atomic_copy(staged_original, original_destination)
                        if created_processed:
                            _atomic_copy(staged_processed, processed_destination)

                    record = TrackRecord(
                        track_id=track_id,
                        content_hash=content_hash,
                        display_title=(
                            display_title_from_name(
                                source.name
                            )
                        ),
                        original_name=source.name,
                        original_file=original_name,
                        processed_file=processed_name,
                        duration_seconds=duration,
                        added_at=utc_now_text(),
                    )

                    results.append(
                        {
                            "record": (
                                record.to_dict()
                            )
                        }
                    )

                    self.known_hashes[
                        content_hash
                    ] = track_id

                except Exception:
                    if created_original:
                        original_destination.unlink(
                            missing_ok=True
                        )

                    if created_processed:
                        processed_destination.unlink(
                            missing_ok=True
                        )

                    raise

            self.finished.emit(
                results
            )

        except Exception as error:
            for artifact in created_artifacts:
                try:
                    artifact.unlink(
                        missing_ok=True
                    )

                except OSError:
                    pass

            self.failed.emit(
                str(
                    error
                )
            )

    @QtCore.Slot()
    def cancel(
        self,
    ) -> None:
        self._cancelled = True


class _RepairWorker(
    QtCore.QObject
):
    finished = QtCore.Signal(
        int,
        list,
    )

    failed = QtCore.Signal(
        str
    )

    def __init__(
        self,
        project_root: Path,
    ) -> None:
        super().__init__()

        self.project_root = Path(
            project_root
        ).resolve()

        self._cancelled = False

    @QtCore.Slot()
    def run(
        self,
    ) -> None:
        repaired = 0
        issues: list[str] = []

        records = load_library(
            self.project_root
        )

        try:
            folders = (
                originals_dir(
                    self.project_root
                ),
                processed_dir(
                    self.project_root
                ),
                analysis_dir(
                    self.project_root
                ),
            )

            for folder in folders:
                for temporary in folder.glob(
                    ".*.tmp*"
                ):
                    if self._cancelled:
                        raise RuntimeError(
                            "Archive repair canceled."
                        )

                    try:
                        temporary.unlink()
                        repaired += 1

                    except OSError:
                        issues.append(
                            "Could not remove "
                            f"temporary file: "
                            f"{temporary.name}"
                        )

            for record in records.values():
                if self._cancelled:
                    raise RuntimeError(
                        "Archive repair canceled."
                    )

                processed = resolve_track_path(
                    self.project_root,
                    record,
                )

                if processed.is_file():
                    continue

                if record.original_file:
                    original = (
                        originals_dir(
                            self.project_root
                        )
                        / Path(
                            record.original_file
                        ).name
                    )

                else:
                    original = None

                if (
                    original is None
                    or not original.is_file()
                ):
                    issues.append(
                        "Missing audio files for "
                        f"{record.display_title}"
                    )

                    continue

                try:
                    if (
                        original.suffix.casefold()
                        == ".mp3"
                    ):
                        _atomic_copy(
                            original,
                            processed,
                        )

                    else:
                        convert_to_mp3(
                            original,
                            processed,
                            cancel_check=(
                                lambda: self._cancelled
                            ),
                        )

                    record.duration_seconds = (
                        probe_audio(
                            processed
                        ).duration_seconds
                    )

                    repaired += 1

                except (
                    AudioToolError,
                    OSError,
                ) as error:
                    issues.append(
                        f"{record.display_title}: "
                        f"{error}"
                    )

            save_library(
                self.project_root,
                records,
            )

            self.finished.emit(
                repaired,
                issues,
            )

        except Exception as error:
            self.failed.emit(
                str(
                    error
                )
            )

    @QtCore.Slot()
    def cancel(
        self,
    ) -> None:
        self._cancelled = True


class PlaylistPlayer(
    QtCore.QObject
):
    trackChanged = QtCore.Signal(
        str
    )

    playbackChanged = QtCore.Signal(
        bool
    )
    transportStateChanged = QtCore.Signal(str)

    positionChanged = QtCore.Signal(
        int,
        int,
    )

    activePlayerChanged = QtCore.Signal(
        object
    )

    error = QtCore.Signal(
        str
    )

    finished = QtCore.Signal()

    def __init__(
        self,
        path_resolver: Callable[
            [str],
            Optional[Path],
        ],
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.path_resolver = (
            path_resolver
        )
        self._shutdown = False
        self._play_requested = False
        self._last_error = ""
        self._transport_state = "unavailable"
        self._reported_playing = False
        self._source_generations = [0, 0]
        self._player_connections: list[list[tuple[object, object]]] = [[], []]

        self.players = [
            QMediaPlayer(
                self
            ),
            QMediaPlayer(
                self
            ),
        ]

        self.outputs = [
            QAudioOutput(
                self
            ),
            QAudioOutput(
                self
            ),
        ]

        for index, player in enumerate(
            self.players
        ):
            player.setAudioOutput(
                self.outputs[
                    index
                ]
            )

        self.queue: list[str] = []
        self.current_index = -1
        self.active_slot = 0
        self._durations = [0, 0]

        self._volume = max(
            0.0,
            min(
                1.0,
                float(
                    STARTING_VOLUME
                )
                / 100.0,
            ),
        )

        self._muted = False

        self._crossfade_timer = QtCore.QTimer(
            self
        )

        self._crossfade_timer.setInterval(
            40
        )

        self._crossfade_timer.timeout.connect(
            self._crossfade_step
        )

        self._loop_timer = QtCore.QTimer(
            self
        )

        self._loop_timer.setSingleShot(
            True
        )

        self._loop_timer.setInterval(
            LOOP_DELAY_MS
        )

        self._loop_timer.timeout.connect(
            self._advance_after_loop_delay
        )

        self._crossfade_elapsed = 0
        self._crossfade_from = -1
        self._crossfade_to = -1
        self._transitioning = False
        self._preview_playback: tuple[tuple[int, ...], bool] | None = None

        self._apply_output_state()

    @property
    def transport_state(self) -> str:
        return self._transport_state

    def _disconnect_player(self, slot: int) -> None:
        self._source_generations[slot] += 1
        for signal, callback in self._player_connections[slot]:
            signal.disconnect(callback)
        self._player_connections[slot].clear()

    def _bind_player(self, slot: int) -> None:
        self._disconnect_player(slot)
        generation = self._source_generations[slot]
        player = self.players[slot]

        def connect(signal, callback) -> None:
            def deliver(*args) -> None:
                if not self._shutdown and generation == self._source_generations[slot]:
                    callback(*args)
            signal.connect(deliver)
            self._player_connections[slot].append((signal, deliver))

        connect(player.positionChanged, lambda value: self._on_position(slot, value))
        connect(player.durationChanged, lambda value: self._on_duration(slot, value))
        connect(player.mediaStatusChanged, lambda value: self._on_status(slot, value))
        connect(player.playbackStateChanged, lambda _value: self._publish_transport_state())
        connect(player.errorOccurred, lambda _error, text: self._on_error(slot, text))

    def _set_player_source(self, slot: int, source: QUrl) -> None:
        self._disconnect_player(slot)
        player = self.players[slot]
        player.stop()
        player.setPosition(0)
        self._durations[slot] = 0
        self._bind_player(slot)
        player.setSource(source)
        # Qt keeps a cached source when its URL is unchanged and does not emit
        # durationChanged again. Preserve its duration for the next crossfade.
        self._durations[slot] = max(0, int(player.duration()))

    def _publish_transport_state(self) -> None:
        playing = self.is_playing()
        if playing != self._reported_playing:
            self._reported_playing = playing
            self.playbackChanged.emit(playing)
        if self._last_error:
            state = "error"
        elif not self.current_track_id or self.player.source().isEmpty():
            state = "unavailable"
        elif playing:
            state = "playing"
        elif self.player.playbackState() == QMediaPlayer.PausedState:
            state = "paused"
        elif self._player_connections[self.active_slot] and self.player.mediaStatus() in (
            QMediaPlayer.LoadingMedia, QMediaPlayer.BufferingMedia, QMediaPlayer.StalledMedia,
        ):
            state = "loading"
        else:
            state = "stopped"
        if state != self._transport_state:
            self._transport_state = state
            self.transportStateChanged.emit(state)

    @property
    def player(
        self,
    ) -> QMediaPlayer:
        return self.players[
            self.active_slot
        ]

    @property
    def current_track_id(
        self,
    ) -> str:
        if (
            0
            <= self.current_index
            < len(
                self.queue
            )
        ):
            return self.queue[
                self.current_index
            ]

        return ""

    def set_queue(
        self,
        track_ids: list[str],
        selected_track_id: str = "",
    ) -> None:
        was_playing = self.is_playing()

        self.stop(
            reset_position=True
        )

        self.queue = list(track_ids)

        if selected_track_id in self.queue:
            self.current_index = (
                self.queue.index(
                    selected_track_id
                )
            )

        else:
            if self.queue:
                self.current_index = 0

            else:
                self.current_index = -1

        self._load_active_source()

        if (
            was_playing
            and self.queue
        ):
            self.play()

    def _load_active_source(
        self,
    ) -> None:
        player = self.players[
            self.active_slot
        ]

        if self.current_track_id:
            path = self.path_resolver(
                self.current_track_id
            )

        else:
            path = None

        self._last_error = ""
        self._set_player_source(
            self.active_slot, QUrl.fromLocalFile(str(path)) if path else QUrl()
        )

        self.activePlayerChanged.emit(
            player
        )

        self.trackChanged.emit(
            self.current_track_id
        )

        self.positionChanged.emit(
            0,
            0,
        )
        self._publish_transport_state()

    def set_volume(
        self,
        percent: int,
    ) -> None:
        self._volume = max(
            0.0,
            min(
                1.0,
                int(
                    percent
                )
                / 100.0,
            ),
        )

        self._apply_output_state()

    def set_muted(
        self,
        muted: bool,
    ) -> None:
        self._muted = bool(
            muted
        )

        self._apply_output_state()

    def toggle_mute(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted

    def is_muted(self) -> bool:
        """
        Return the current mute state.

        Nexus uses this when synchronizing the Sound volume and mute state
        with the Forge preview.
        """
        return bool(self._muted)

    def _apply_output_state(self) -> None:

        for output in self.outputs:
            output.setMuted(
                self._muted
            )

            if not self._transitioning:
                output.setVolume(
                    self._volume
                )

    def is_playing(
        self,
    ) -> bool:
        return any(
            player.playbackState()
            == QMediaPlayer.PlayingState
            for player
            in self.players
        )

    def play(
        self,
    ) -> None:
        if self._shutdown or self._preview_playback is not None:
            return
        self._loop_timer.stop()

        if (
            not self.queue
            or self.current_index < 0
        ):
            return

        if self.path_resolver(self.current_track_id) is None:
            self.stop()
            self._load_active_source()
            self.error.emit("This track is unavailable. Repair or reimport it, or remove it from the playlist.")
            return

        if self.player.source().isEmpty():
            self._load_active_source()
        if not self._player_connections[self.active_slot]:
            self._bind_player(self.active_slot)
        self._last_error = ""
        self._play_requested = True

        self.outputs[
            self.active_slot
        ].setVolume(
            self._volume
        )

        self.player.play()

        self._publish_transport_state()

    def pause(
        self,
    ) -> None:
        self._play_requested = False
        if self._preview_playback is not None:
            self._preview_playback = ((), False)
        self._loop_timer.stop()

        self._cancel_crossfade(
            keep_active=True
        )

        self.player.pause()

        self._publish_transport_state()

    def pause_for_preview(self) -> None:
        """Lend playback to a popup without unloading either crossfade source."""
        if self._preview_playback is not None:
            return
        playing_slots = tuple(
            slot for slot, player in enumerate(self.players)
            if player.playbackState() == QMediaPlayer.PlayingState
        )
        self._preview_playback = (playing_slots, self._loop_timer.isActive())
        self._play_requested = False
        self._loop_timer.stop()
        self._crossfade_timer.stop()
        for slot in playing_slots:
            self.players[slot].pause()
        self._publish_transport_state()

    def resume_after_preview(self, *, resume: bool = True) -> None:
        playback = self._preview_playback
        self._preview_playback = None
        if playback is None or not resume:
            return
        playing_slots, loop_pending = playback
        self._play_requested = bool(playing_slots or loop_pending)
        for slot in playing_slots:
            self.players[slot].play()
        if self._transitioning and playing_slots:
            self._crossfade_timer.start()
        if loop_pending:
            self._loop_timer.start()
        self._publish_transport_state()

    def stop(
        self,
        reset_position: bool = True,
    ) -> None:
        self._play_requested = False
        self._last_error = ""
        for slot in range(len(self.players)):
            self._disconnect_player(slot)
        if self._preview_playback is not None:
            self._preview_playback = ((), False)
        self._loop_timer.stop()

        self._cancel_crossfade(
            keep_active=True
        )

        for player in self.players:
            player.stop()

            if reset_position:
                player.setPosition(
                    0
                )

        self._publish_transport_state()

        self.positionChanged.emit(
            0,
            self._durations[
                self.active_slot
            ],
        )

    def seek(
        self,
        position: int,
    ) -> None:
        self.player.setPosition(
            max(
                0,
                int(
                    position
                ),
            )
        )

    def select_track(
        self,
        track_id: str,
        autoplay: bool = False,
    ) -> None:
        if track_id not in self.queue:
            return

        self.stop(
            reset_position=True
        )

        self.current_index = (
            self.queue.index(
                track_id
            )
        )

        self._load_active_source()

        if autoplay:
            self.play()

    def next(
        self,
        autoplay: bool = True,
    ) -> None:
        if not self.queue:
            self.stop(
                reset_position=True
            )

            return

        self.stop(
            reset_position=True
        )

        self.current_index = (
            self.current_index + 1
        ) % len(
            self.queue
        )
        self._load_active_source()

        if autoplay:
            self.play()

    def previous(
        self,
    ) -> None:
        if (
            self.player.position()
            > 3000
            or self.current_index <= 0
        ):
            self.seek(
                0
            )

            return

        self.stop(
            reset_position=True
        )

        self.current_index -= 1
        self._load_active_source()
        self.play()

    def _on_position(
        self,
        slot: int,
        position: int,
    ) -> None:
        if slot != self.active_slot:
            return

        duration = self._durations[
            slot
        ]

        self.positionChanged.emit(
            position,
            duration,
        )

        if (
            not self._transitioning
            and self._preview_playback is None
            and self.is_playing()
            and len(
                self.queue
            )
            > 1
            and duration
            > CROSSFADE_MS + 500
            and 0
            < duration - position
            <= CROSSFADE_MS
        ):
            self._begin_crossfade()

    def _on_duration(
        self,
        slot: int,
        duration: int,
    ) -> None:
        self._durations[
            slot
        ] = max(
            0,
            int(
                duration
            ),
        )

        if slot == self.active_slot:
            self.positionChanged.emit(
                self.players[
                    slot
                ].position(),
                self._durations[
                    slot
                ],
            )

    def _on_status(
        self,
        slot: int,
        status: QMediaPlayer.MediaStatus,
    ) -> None:
        self._publish_transport_state()
        if (
            status
            != QMediaPlayer.EndOfMedia
            or not self._play_requested
            or self._shutdown
            or slot != self.active_slot
            or self._transitioning
            or self._preview_playback is not None
            or self._loop_timer.isActive()
        ):
            return

        if self.queue:
            self._loop_timer.start()

            return

        self.stop(
            reset_position=True
        )

        self.finished.emit()

    def _advance_after_loop_delay(
        self,
    ) -> None:
        if self.queue and self._play_requested and not self._shutdown and self._preview_playback is None:
            self.next(
                autoplay=True
            )

    def _on_error(
        self,
        slot: int,
        text: str,
    ) -> None:
        if (
            (slot == self.active_slot or self._transitioning and slot == self._crossfade_to)
            and not self._shutdown
            and self._player_connections[slot]
            and self._preview_playback is None
            and text
        ):
            self.stop()
            self._last_error = str(text)
            self._publish_transport_state()
            self.error.emit(
                text
            )

    def _begin_crossfade(
        self,
    ) -> None:
        if self._shutdown or not self._play_requested or len(self.queue) < 2:
            return
        next_index = (
            self.current_index + 1
        ) % len(
            self.queue
        )

        next_slot = (
            1 - self.active_slot
        )

        path = self.path_resolver(
            self.queue[
                next_index
            ]
        )

        if path is None:
            return

        standby = self.players[
            next_slot
        ]

        self._transitioning = True
        self._crossfade_elapsed = 0
        self._crossfade_from = self.active_slot
        self._crossfade_to = next_slot
        self._set_player_source(next_slot, QUrl.fromLocalFile(str(path)))
        if not self._transitioning:
            return

        self.outputs[
            next_slot
        ].setMuted(
            self._muted
        )

        self.outputs[
            next_slot
        ].setVolume(
            0.0
        )

        standby.play()
        if self._transitioning:
            self._crossfade_timer.start()

    def _crossfade_step(
        self,
    ) -> None:
        if self._shutdown or not self._transitioning or self._preview_playback is not None:
            return
        self._crossfade_elapsed += (
            self._crossfade_timer
            .interval()
        )

        progress = min(
            1.0,
            self._crossfade_elapsed
            / float(
                CROSSFADE_MS
            ),
        )

        self.outputs[
            self._crossfade_from
        ].setVolume(
            self._volume
            * (
                1.0 - progress
            )
        )

        self.outputs[
            self._crossfade_to
        ].setVolume(
            self._volume
            * progress
        )

        if progress < 1.0:
            return

        old_slot = (
            self._crossfade_from
        )

        new_slot = (
            self._crossfade_to
        )

        self._crossfade_timer.stop()

        self.active_slot = new_slot
        self.current_index = (
            self.current_index + 1
        ) % len(
            self.queue
        )
        self._transitioning = False
        self._crossfade_from = -1
        self._crossfade_to = -1

        self.activePlayerChanged.emit(
            self.player
        )

        self.trackChanged.emit(
            self.current_track_id
        )

        # Rebind the preview while both sources are still playing. Stopping the
        # outgoing player first would briefly collapse the visualization.
        self.players[
            old_slot
        ].stop()

        self.players[
            old_slot
        ].setPosition(
            0
        )

        self.outputs[
            old_slot
        ].setVolume(
            self._volume
        )

    def _cancel_crossfade(
        self,
        keep_active: bool,
    ) -> None:
        if not self._transitioning:
            return

        self._crossfade_timer.stop()

        standby = self._crossfade_to

        if standby >= 0:
            self.players[
                standby
            ].stop()

            self.players[
                standby
            ].setPosition(
                0
            )

            self.outputs[
                standby
            ].setVolume(
                self._volume
            )

        if (
            keep_active
            and self._crossfade_from >= 0
        ):
            self.outputs[
                self._crossfade_from
            ].setVolume(
                self._volume
            )

        self._transitioning = False
        self._crossfade_from = -1
        self._crossfade_to = -1

    def release_current_file_handle(
        self,
    ) -> None:
        self.stop(
            reset_position=True
        )

        for player in self.players:
            player.setSource(
                QUrl()
            )
        self._publish_transport_state()

    def shutdown(
        self,
    ) -> None:
        self.release_current_file_handle()
        self._shutdown = True


def _music_popup_style(
    service: object | None = None,
) -> str:
    tokens = getattr(
        service,
        "tokens",
        None,
    )

    def color(
        name: str,
        fallback: str,
    ) -> str:
        value = str(
            getattr(
                tokens,
                name,
                fallback,
            )
            or fallback
        )
        return (
            value
            if QtGui.QColor(value).isValid()
            else fallback
        )

    panel = color("panel_background", "#11151c")
    background = color("background", "#0b0f15")
    control = color("control_background", "#1c2430")
    border = color("border", "#38506b")
    text = color("text", "#e7eef8")
    highlight = color("highlight", "#eefaff")
    hover = color("hover", "#233447")
    active = color("active", "#233447")
    accent = color("accent", "#00c8ff")

    return f"""
QDialog {{
    background: {panel};
    color: {text};
    border: 1px solid {border};
}}
QLabel#MusicPopupHeading {{
    color: {highlight};
    font-size: 18px;
    font-weight: 700;
    padding: 3px 2px 7px 2px;
}}
QLineEdit,
QComboBox,
QTableWidget,
QListWidget {{
    background: {background};
    color: {text};
    border: 1px solid {border};
}}
QTableWidget {{
    alternate-background-color: {control};
    gridline-color: {border};
    outline: none;
}}
QHeaderView::section {{
    background: {control};
    color: {highlight};
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    padding: 7px 9px;
    font-weight: 700;
}}
QTableCornerButton::section {{
    background: {control};
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}
QListWidget::item {{
    border-bottom: 1px solid {border};
    padding: 11px 12px;
}}
QListWidget::item:hover,
QListWidget::item:selected {{
    background: {active};
    color: {highlight};
}}
QTableWidget::item {{
    border-bottom: 1px solid {border};
    padding: 8px 10px;
}}
QTableWidget::item:selected {{
    background: #1c5275;
    color: #ffffff;
}}
QPushButton {{
    background: {control};
    color: {text};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 9px 13px;
    min-height: 22px;
}}
QPushButton:hover {{
    border-color: {accent};
    background: {hover};
}}
"""


class _ClickOutsidePopup(
    QtWidgets.QDialog
):
    """Frameless popup that dismisses without consuming the outside click."""

    previewRequested = QtCore.Signal()

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.Popup | Qt.FramelessWindowHint,
        )
        self._outside_filter_installed = False

    def showEvent(
        self,
        event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(
            event
        )
        application = QtWidgets.QApplication.instance()
        if application is not None and not self._outside_filter_installed:
            application.installEventFilter(
                self
            )
            self._outside_filter_installed = True

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        self._on_popup_hidden()
        self._remove_outside_filter()
        super().hideEvent(
            event
        )

    def _on_popup_hidden(self) -> None:
        """Release popup-owned resources before an outside dismissal."""

    def _init_preview_controls(self, layout: QtWidgets.QVBoxLayout) -> None:
        row = QtWidgets.QHBoxLayout()
        self.preview_btn = QtWidgets.QPushButton("Preview Track")
        self.stop_preview_btn = QtWidgets.QPushButton("Stop Preview")
        self.preview_label = QtWidgets.QLabel("Preview volume: 35%")
        self.preview_label.setWordWrap(True)
        self.preview_btn.clicked.connect(self._preview)
        self.stop_preview_btn.clicked.connect(self._stop_preview)
        row.addWidget(self.preview_btn)
        row.addWidget(self.stop_preview_btn)
        row.addWidget(self.preview_label, 1)
        layout.insertLayout(max(0, layout.count() - 1), row)
        self.preview_player.playbackStateChanged.connect(self._preview_state_changed)
        self._preview_state_changed(self.preview_player.playbackState())

    def _preview_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        record = self.library.get(self._previewing_id) if self._previewing_id else None
        playing = state == QMediaPlayer.PlayingState and record is not None
        self.preview_label.setText(
            f"Previewing: {record.display_title} • 35%" if playing else "Preview volume: 35%"
        )
        self.stop_preview_btn.setEnabled(playing)

    def apply_theme_assets(
        self,
        service: object | None = None,
    ) -> None:
        theme_service = service
        if theme_service is None:
            host = self.parentWidget()
            window = (
                host.window()
                if host is not None
                else self.window()
            )
            theme_service = getattr(
                window,
                "theme_service",
                None,
            )
        self.setStyleSheet(
            _music_popup_style(
                theme_service
            )
        )

    def _remove_outside_filter(self) -> None:
        application = QtWidgets.QApplication.instance()
        if application is not None and self._outside_filter_installed:
            application.removeEventFilter(
                self
            )
        self._outside_filter_installed = False

    def eventFilter(
        self,
        watched: QtCore.QObject,
        event: QtCore.QEvent,
    ) -> bool:
        if (
            self.isVisible()
            and event.type() == QtCore.QEvent.MouseButtonPress
        ):
            widget = watched if isinstance(watched, QtWidgets.QWidget) else None
            ancestor = widget
            while ancestor is not None:
                if ancestor is self:
                    return super().eventFilter(watched, event)
                ancestor = ancestor.parentWidget()
            if isinstance(event, QtGui.QMouseEvent):
                position = event.globalPosition().toPoint()
                if self.frameGeometry().contains(position):
                    return super().eventFilter(watched, event)
            self.reject()
        return super().eventFilter(
            watched,
            event,
        )


class ArchiveDialog(
    _ClickOutsidePopup
):
    tracksChosen = QtCore.Signal(
        list
    )

    def __init__(
        self,
        library: SoundLibrary,
        used_ids: Callable[
            [],
            set[str],
        ],
        delete_callback: Callable[
            [str],
            bool,
        ],
        *,
        multi_select: bool,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.library = library
        self.used_ids = used_ids
        self.delete_callback = (
            delete_callback
        )
        self.setWindowTitle(
            "Archive"
        )
        self.setAccessibleName("Archive")

        self.resize(
            760,
            500,
        )

        root = QtWidgets.QVBoxLayout(
            self
        )

        self.heading = QtWidgets.QLabel(
            "Archive"
        )
        self.heading.setObjectName(
            "MusicPopupHeading"
        )
        root.addWidget(
            self.heading
        )

        top = QtWidgets.QHBoxLayout()

        self.search = QtWidgets.QLineEdit()
        self.search.setToolTip(
            "Filter your archived music by title or filename."
        )

        self.search.setPlaceholderText(
            "Search archive…"
        )

        self.sort = QtWidgets.QComboBox()
        self.sort.setToolTip(
            "Choose how archived music is ordered."
        )

        self.sort.addItem(
            "Recently added",
            "recent",
        )

        self.sort.addItem(
            "Name",
            "name",
        )

        self.sort.addItem(
            "Duration",
            "duration",
        )

        top.addWidget(
            self.search,
            1,
        )

        top.addWidget(
            self.sort
        )

        root.addLayout(
            top
        )

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setToolTip(
            "Select a song to preview it; double-click to use it."
        )

        self.table.setHorizontalHeaderLabels(
            [
                "Title",
                "Duration",
                "Added",
            ]
        )
        self.table.verticalHeader().hide()

        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.Stretch,
        )

        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QtWidgets.QHeaderView.ResizeToContents,
            )

        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )

        if multi_select:
            selection_mode = (
                QtWidgets.QAbstractItemView
                .ExtendedSelection
            )

        else:
            selection_mode = (
                QtWidgets.QAbstractItemView
                .SingleSelection
            )

        self.table.setSelectionMode(
            selection_mode
        )

        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )

        self.table.setAlternatingRowColors(
            True
        )

        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellClicked.connect(lambda _row, _column: self._preview())
        self.table.doubleClicked.connect(lambda _index: self._choose())

        root.addWidget(
            self.table,
            1,
        )

        buttons = QtWidgets.QHBoxLayout()

        self.rename_btn = QtWidgets.QPushButton(
            "Rename Title"
        )
        self.rename_btn.setToolTip(
            "Change the displayed title without renaming the audio file."
        )

        self.delete_btn = QtWidgets.QPushButton(
            "Delete from Archive"
        )
        self.delete_btn.setToolTip(
            "Remove the selected music from your archive."
        )

        if multi_select:
            choose_text = "Add to Playlist"

        else:
            choose_text = "Use Track"

        self.choose_btn = QtWidgets.QPushButton(
            choose_text
        )
        self.choose_btn.setToolTip(
            "Add the selected archive music to this letter."
            if multi_select
            else "Use the selected archive song for this letter."
        )

        buttons.addWidget(
            self.rename_btn
        )

        buttons.addWidget(
            self.delete_btn
        )

        buttons.addStretch(
            1
        )

        buttons.addWidget(
            self.choose_btn
        )

        root.addLayout(
            buttons
        )

        self.preview_output = QAudioOutput(
            self
        )

        self.preview_output.setVolume(
            0.35
        )

        self.preview_player = QMediaPlayer(
            self
        )

        self.preview_player.setAudioOutput(
            self.preview_output
        )
        self.preview_player.setLoops(
            QMediaPlayer.Loops.Infinite
        )
        self.preview_player.errorOccurred.connect(
            self._preview_failed
        )

        self._previewing_id = ""

        self._init_preview_controls(root)

        self._filter_refresh_timer = QtCore.QTimer(self)
        self._filter_refresh_timer.setSingleShot(True)
        self._filter_refresh_timer.setInterval(150)
        self._filter_refresh_timer.timeout.connect(self.refresh)

        self.search.textChanged.connect(
            lambda _text: self._refresh_after_filter_change()
        )

        self.sort.currentIndexChanged.connect(
            lambda _index: self._refresh_after_filter_change()
        )

        self.rename_btn.clicked.connect(
            self._rename
        )

        self.delete_btn.clicked.connect(
            self._delete
        )

        self.choose_btn.clicked.connect(
            self._choose
        )

        self.library.changed.connect(
            self.refresh
        )

        self.refresh()

        self.apply_theme_assets()

    def _selection_changed(self, *, preview: bool = True) -> None:
        selected_ids = self.selected_ids()
        has_selection = bool(selected_ids)
        selected_records = [
            record
            for track_id in selected_ids
            if (record := self.library.get(track_id)) is not None
        ]
        editable = bool(selected_records) and all(
            record.source_kind != "stock"
            for record in selected_records
        )
        self.rename_btn.setEnabled(editable and len(selected_records) == 1)
        self.delete_btn.setEnabled(editable)
        self.choose_btn.setEnabled(has_selection)
        if has_selection and preview:
            self._preview()

    def _refresh_after_filter_change(self) -> None:
        self._stop_preview()
        self._filter_refresh_timer.start()

    def _preview_failed(self, _error: QMediaPlayer.Error, message: str) -> None:
        if not self._previewing_id:
            return
        _LOGGER.warning("Archive preview failed: %s", message)
        self._stop_preview()
        show_lettersmith_message(self, "Music Preview", "This track could not be previewed. Check the source or repair Music Archive.")

    def refresh(
        self,
    ) -> None:
        self._filter_refresh_timer.stop()
        query = (
            self.search
            .text()
            .strip()
            .casefold()
        )

        records = self.library.all_records(
            str(
                self.sort.currentData()
                or "recent"
            )
        )

        records = [
            record
            for record in records
            if record.source_kind != "stock"
        ]

        if query:
            records = [
                record
                for record
                in records
                if (
                    query
                    in record.display_title.casefold()
                    or query
                    in record.original_name.casefold()
                )
            ]

        selection_blocker = QtCore.QSignalBlocker(self.table)
        self.table.clearContents()
        self.table.setRowCount(
            len(
                records
            )
        )

        used = self.used_ids()

        for row, record in enumerate(
            records
        ):
            title = QtWidgets.QTableWidgetItem(
                record.display_title
            )

            title.setData(
                Qt.UserRole,
                record.track_id,
            )

            self.table.setItem(
                row,
                0,
                title,
            )

            self.table.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(
                    _format_duration(
                        record.duration_seconds
                    )
                ),
            )

            self.table.setItem(
                row,
                2,
                QtWidgets.QTableWidgetItem(
                    record.added_at[:10]
                ),
            )

        self.table.resizeRowsToContents()
        self.table.clearSelection()
        selection_model = self.table.selectionModel()
        model = self.table.model()
        if selection_model is not None and model is not None:
            selection = QtCore.QItemSelection()
            for row, record in enumerate(records):
                if record.track_id in used:
                    selection.select(
                        model.index(row, 0),
                        model.index(row, self.table.columnCount() - 1),
                    )
            if not selection.isEmpty():
                selection_model.select(
                    selection,
                    QtCore.QItemSelectionModel.ClearAndSelect
                    | QtCore.QItemSelectionModel.Rows,
                )
        del selection_blocker
        self._selection_changed(preview=False)

    def selected_ids(
        self,
    ) -> list[str]:
        rows = sorted(
            {
                index.row()
                for index
                in self.table
                .selectionModel()
                .selectedRows()
            }
        )

        result: list[str] = []

        for row in rows:
            item = self.table.item(
                row,
                0,
            )

            if item is not None:
                result.append(
                    str(
                        item.data(
                            Qt.UserRole
                        )
                        or ""
                    )
                )

        return [
            track_id
            for track_id
            in result
            if track_id
        ]

    def _choose(
        self,
    ) -> None:
        track_ids = self.selected_ids()

        if track_ids:
            self._stop_preview()
            self.tracksChosen.emit(
                track_ids
            )

            self.accept()

    def _preview(
        self,
    ) -> None:
        track_ids = self.selected_ids()

        if not track_ids:
            return

        track_id = track_ids[0]

        if (
            self._previewing_id == track_id
            and self.preview_player.playbackState()
            == QMediaPlayer.PlayingState
        ):
            return

        if self._previewing_id != track_id:
            self._stop_preview()

        path = self.library.path_for(
            track_id
        )

        if path is None:
            return

        self.previewRequested.emit()
        self.preview_player.setSource(
            QUrl.fromLocalFile(
                str(
                    path
                )
            )
        )

        self._previewing_id = (
            track_id
        )
        self.preview_player.play()

    def _rename(
        self,
    ) -> None:
        track_ids = self.selected_ids()

        if not track_ids:
            return

        record = self.library.get(
            track_ids[0]
        )

        if record is None:
            return
        if record.source_kind == "stock":
            return

        title, accepted = LetterSmithInputDialog.get_text(
            self,
            "Rename Display Title",
            "Display title:",
            text=record.display_title,
            accept_text="Rename",
        )

        if accepted and title.strip():
            self._stop_preview()
            self.library.rename_display_title(
                record.track_id,
                title,
            )

    def _stop_preview(self) -> None:
        self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        self._previewing_id = ""

    def _delete(
        self,
    ) -> None:
        track_ids = self.selected_ids()

        if not track_ids:
            return

        self._stop_preview()

        removed = False
        for track_id in track_ids:
            record = self.library.get(track_id)
            if record is None or record.source_kind == "stock":
                continue
            removed = self.delete_callback(track_id) or removed

        self.refresh()
        if removed:
            play_ui_sound(UiSound.REMOVED)

    def closeEvent(
        self,
        event: QtGui.QCloseEvent,
    ) -> None:
        self._filter_refresh_timer.stop()
        self._stop_preview()

        super().closeEvent(
            event
        )

    def _on_popup_hidden(self) -> None:
        self._filter_refresh_timer.stop()
        self._stop_preview()


class StockMusicDialog(
    _ClickOutsidePopup
):
    tracksChosen = QtCore.Signal(
        list
    )

    def __init__(
        self,
        library: SoundLibrary,
        *,
        multi_select: bool,
        parent: Optional[QtWidgets.QWidget] = None,
        recent_track_ids: tuple[str, ...] = (),
        allow_browse: bool = False,
    ) -> None:
        super().__init__(
            parent
        )
        self.library = library
        self.setWindowTitle(
            "Select Music" if allow_browse else "Stock Music"
        )
        self.setAccessibleName(
            "Select Music" if allow_browse else "Stock Music"
        )
        self._recent_track_ids = tuple(
            track_id for track_id in dict.fromkeys(recent_track_ids)
            if library.get(track_id) is not None
            and library.path_for(track_id) is not None
        ) if allow_browse else ()
        self.resize(
            430,
            340 if self._recent_track_ids else 180 if allow_browse else 300,
        )
        self.setMinimumWidth(430)
        self.browse_requested = False

        root = QtWidgets.QVBoxLayout(
            self
        )
        self.heading = QtWidgets.QLabel(
            "Select Music" if allow_browse else "Stock Music"
        )
        self.heading.setObjectName(
            "MusicPopupHeading"
        )
        root.addWidget(
            self.heading
        )
        if allow_browse:
            browse_btn = QtWidgets.QPushButton("Choose Music…")
            browse_btn.clicked.connect(self._browse)
            root.addWidget(browse_btn)

        self.track_list: QtWidgets.QListWidget | None = None
        if not allow_browse:
            self.track_list = QtWidgets.QListWidget()
            self.track_list.setObjectName("StockMusicList")
            self.track_list.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection
                if multi_select
                else QtWidgets.QAbstractItemView.SingleSelection
            )
            self.track_list.setEditTriggers(
                QtWidgets.QAbstractItemView.NoEditTriggers
            )
            self.track_list.setToolTip(
                "Select a bundled song to preview it."
            )
            self.track_list.itemSelectionChanged.connect(self._selection_changed)
            self.track_list.itemDoubleClicked.connect(
                lambda _item: self._choose()
            )
            root.addWidget(self.track_list, 1)
        self.recent_list: QtWidgets.QListWidget | None = None
        if self._recent_track_ids:
            root.addWidget(QtWidgets.QLabel("Recently Used"))
            self.recent_list = QtWidgets.QListWidget()
            self.recent_list.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection
                if multi_select
                else QtWidgets.QAbstractItemView.SingleSelection
            )
            self.recent_list.itemSelectionChanged.connect(
                self._recent_selection_changed
            )
            self.recent_list.itemDoubleClicked.connect(
                lambda _item: self._choose()
            )
            root.addWidget(self.recent_list, 1)

        choose_text = (
            "Add to Playlist"
            if multi_select
            else "Use Track"
        )
        self.choose_btn = QtWidgets.QPushButton(
            choose_text
        )
        self.choose_btn.setToolTip(
            "Add the selected music to this letter."
            if multi_select
            else "Use the selected song for this letter."
        )
        self.choose_btn.clicked.connect(
            self._choose
        )
        self.choose_btn.setEnabled(
            False
        )
        root.addWidget(
            self.choose_btn,
            0,
            Qt.AlignRight,
        )
        self.preview_output = QAudioOutput(
            self
        )
        self.preview_output.setVolume(
            0.35
        )
        self.preview_player = QMediaPlayer(
            self
        )
        self.preview_player.setAudioOutput(
            self.preview_output
        )
        self.preview_player.setLoops(
            QMediaPlayer.Loops.Infinite
        )
        self.preview_player.errorOccurred.connect(
            self._preview_failed
        )
        self._previewing_id = ""

        self._init_preview_controls(root)

        self.library.changed.connect(
            self.refresh
        )
        self.apply_theme_assets()
        self.refresh()

    def refresh(self) -> None:
        selected = set(
            self.selected_ids()
        )
        if self.track_list is not None:
            records = [
                record
                for record in self.library.all_records("name")
                if record.source_kind == "stock"
            ]
            self.track_list.clear()
            for record in records:
                item = QtWidgets.QListWidgetItem(record.display_title)
                item.setData(Qt.UserRole, record.track_id)
                item.setToolTip(f"Preview {record.display_title}.")
                self.track_list.addItem(item)
                if record.track_id in selected:
                    item.setSelected(True)
        if self.recent_list is not None:
            self.recent_list.clear()
            for track_id in self._recent_track_ids:
                record = self.library.get(track_id)
                if record is None or self.library.path_for(track_id) is None:
                    continue
                item = QtWidgets.QListWidgetItem(record.display_title)
                item.setData(Qt.UserRole, track_id)
                self.recent_list.addItem(item)
                if track_id in selected:
                    item.setSelected(True)
        self._selection_changed()

    def selected_ids(self) -> list[str]:
        if self.recent_list is not None and self.recent_list.selectedItems():
            return [
                str(item.data(Qt.UserRole))
                for item in self.recent_list.selectedItems()
            ]
        if self.track_list is None:
            return []
        return [
            str(item.data(Qt.UserRole) or "")
            for item in self.track_list.selectedItems()
            if str(item.data(Qt.UserRole) or "")
        ]

    def _selection_changed(self) -> None:
        has_selection = bool(
            self.selected_ids()
        )
        self.choose_btn.setEnabled(
            has_selection
        )
        if has_selection:
            self._preview()
        else:
            self._stop_preview()

    def _recent_selection_changed(self) -> None:
        self._selection_changed()

    def _browse(self) -> None:
        self._stop_preview()
        self.browse_requested = True
        self.accept()

    def _choose(self) -> None:
        track_ids = self.selected_ids()
        if not track_ids:
            return
        self._stop_preview()
        self.tracksChosen.emit(
            track_ids
        )
        self.accept()

    def _preview(self) -> None:
        track_ids = self.selected_ids()
        if not track_ids:
            return
        track_id = track_ids[0]
        if (
            self._previewing_id == track_id
            and self.preview_player.playbackState()
            == QMediaPlayer.PlayingState
        ):
            return
        if self._previewing_id != track_id:
            self._stop_preview()
        path = self.library.path_for(
            track_id
        )
        if path is None:
            return
        self.previewRequested.emit()
        self.preview_player.setSource(
            QUrl.fromLocalFile(
                str(path)
            )
        )
        self._previewing_id = track_id
        self.preview_player.play()

    def _preview_failed(
        self,
        _error: QMediaPlayer.Error,
        message: str,
    ) -> None:
        if not self._previewing_id:
            return
        _LOGGER.warning("Music preview failed: %s", message)
        self._stop_preview()
        show_lettersmith_message(self, "Music Preview", "This track could not be previewed. Check that its source is available.")

    def _stop_preview(self) -> None:
        self.preview_player.stop()
        self.preview_player.setSource(
            QUrl()
        )
        self._previewing_id = ""

    def closeEvent(
        self,
        event: QtGui.QCloseEvent,
    ) -> None:
        self._stop_preview()
        super().closeEvent(
            event
        )

    def _on_popup_hidden(self) -> None:
        self._stop_preview()


class PlaylistItemWidget(
    QtWidgets.QFrame
):
    removeRequested = QtCore.Signal(
        str
    )

    def __init__(
        self,
        record: TrackRecord,
        active: bool,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.track_id = (
            record.track_id
        )
        self.record_signature = (
            record.display_title,
            record.original_name,
            float(record.duration_seconds),
        )

        self.setObjectName(
            "playlistRow"
        )

        row = QtWidgets.QHBoxLayout(
            self
        )

        row.setContentsMargins(
            8,
            5,
            6,
            5,
        )
        row.setSpacing(5)

        drag = QtWidgets.QLabel(
            "≡"
        )
        drag.setFixedWidth(12)

        self._title_text = record.display_title
        title = QtWidgets.QLabel(self._title_text)
        self._title_label = title
        title.setToolTip(record.display_title)
        title.setAccessibleName(record.display_title)
        title.setMinimumWidth(0)
        title.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.playing_label = QtWidgets.QLabel("Playing")
        self.playing_label.setVisible(active)
        self.playing_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        source = "Stock" if record.source_kind == "stock" else "Music Archive"
        self.setToolTip(f"{record.display_title} • {source} • {record.original_name}")

        duration = QtWidgets.QLabel(
            _format_duration(
                record.duration_seconds
            )
        )

        remove = QtWidgets.QToolButton()
        remove.setObjectName("playlistRemove")

        remove.setText(
            "×"
        )

        remove.setToolTip(
            "Remove from Playlist; keep the source and archive item."
        )
        remove.setAccessibleName(f"Remove {record.display_title} from Playlist")

        remove.setFixedSize(
            24,
            24,
        )

        remove.clicked.connect(
            lambda:
                self.removeRequested.emit(
                    self.track_id
                )
        )

        row.addWidget(
            drag
        )

        row.addWidget(
            title,
            1,
        )
        source_label = QtWidgets.QLabel(source)
        source_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(source_label)

        row.addWidget(
            duration
        )
        row.addWidget(self.playing_label)

        row.addWidget(
            remove
        )
        self.setMinimumHeight(44)
        row.activate()
        self._elide_title()

        self._active: bool | None = None
        self._selected = False
        self._theme_tokens: object | None = None
        self.set_active(active)

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self.playing_label.setVisible(active)
        self._apply_theme_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected != bool(selected):
            self._selected = bool(selected)
            self._apply_theme_style()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide_title()

    def apply_theme_assets(self, service: object | None = None) -> None:
        self._theme_tokens = getattr(service, "tokens", None)
        self._apply_theme_style()
        self._elide_title()

    def _elide_title(self) -> None:
        self._title_label.setText(
            self._title_label.fontMetrics().elidedText(
                self._title_text, Qt.ElideRight, self._title_label.width()
            )
        )

    def _apply_theme_style(self) -> None:
        tokens = self._theme_tokens
        active = bool(self._selected)
        if tokens is None:
            border = "#7f9099" if active else "#41484d"
            background = "#2d3438" if active else "#1c2226"
            text = "#e8eff8"
            muted = "#cfd8e5"
            error = "#ff7777"
        else:
            border = str(
                getattr(
                    tokens,
                    "secondary_accent" if active else "control_border",
                    "#00c8ff",
                )
            )
            background = str(
                getattr(
                    tokens,
                    "selected_background" if active else "control_background",
                    "#151a22",
                )
            )
            text = str(
                getattr(
                    tokens,
                    "selected_text" if active else "text",
                    "#e8eff8",
                )
            )
            muted = str(getattr(tokens, "muted_text", "#cfd8e5"))
            error = str(getattr(tokens, "error", "#ff7777"))

        self.setStyleSheet(
            f"""
            QFrame#playlistRow {{
                background: {background};
                border: 1px solid {border};
                border-radius: 8px;
            }}

            QLabel {{
                color: {text};
            }}

            QToolButton#playlistRemove {{
                color: {muted};
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0;
                font-size: 16px;
            }}

            QToolButton#playlistRemove:hover {{
                color: {error};
                background: {border};
            }}
            """
        )

class _SoundLayout(QtWidgets.QVBoxLayout):
    def heightForWidth(self, width: int) -> int:
        height = super().heightForWidth(width)
        stack = getattr(self.parentWidget(), "mode_stack", None)
        if stack is None or stack.currentWidget() is None:
            return height
        margins = self.contentsMargins()
        panel_width = max(1, width - margins.left() - margins.right())
        panel = stack.currentWidget()
        active_height = max(panel.minimumSizeHint().height(), panel.heightForWidth(panel_width))
        # QStackedLayout reserves the largest hidden mode even with Ignored
        # size policy. Only the visible card belongs in this page's height.
        hidden_height = stack.layout().heightForWidth(panel_width)
        return max(self.minimumSize().height(), height - max(0, hidden_height - active_height))


class SoundTab(QtWidgets.QWidget):
    preview_widget = QtCore.Signal(QtWidgets.QWidget)
    _import_result_ready = QtCore.Signal(int, object)
    _import_error_ready = QtCore.Signal(int, str)

    # Nexus/Forge synchronization signals.
    volume_changed = QtCore.Signal(int)
    sound_state_changed = QtCore.Signal(str)

    def __init__(
        self,
        project_root: str | Path,
        *,
        project_state: ProjectStateController | None = None,
        project_paths: ProjectPathResolver | None = None,
    ) -> None:
        super().__init__()

        self.project_root = Path(
            project_root
        ).resolve()
        self.project_state = project_state
        if self.project_state is None:
            self.project_state = ProjectStateController(
                self.project_root
            )
            self.project_state.initialize()
        self.project_save_service = ProjectSaveService(
            self.project_root,
            self.project_state,
            resolver=project_paths,
        )

        self.library = SoundLibrary(
            self.project_root,
            self,
        )

        self.project_sound = ProjectSound(
            self.project_root,
            self.library,
            self,
        )

        self.player = PlaylistPlayer(
            self.library.path_for,
            self,
        )

        self._tab_active = False

        self._import_thread: Optional[
            QtCore.QThread
        ] = None

        self._import_worker: Optional[
            _ImportWorker
        ] = None

        self._repair_thread: Optional[
            QtCore.QThread
        ] = None

        self._repair_worker: Optional[
            _RepairWorker
        ] = None
        self._button_busy = False

        self._pending_import_mode = "single"
        self._analysis = None
        self._analysis_key = ""
        self._analysis_disabled = False
        self._shutdown = False
        self._background_generation = 0
        self._import_result_ready.connect(
            self._accept_import_finished, QtCore.Qt.QueuedConnection,
        )
        self._import_error_ready.connect(
            self._accept_import_failed, QtCore.Qt.QueuedConnection,
        )

        self._preview = SoundPreviewWidget(
            self.player.player,
            parent=self,
        )

        self._preview.set_tab_active(
            False
        )

        self._status_timer = QtCore.QTimer(
            self
        )

        self._status_timer.setSingleShot(
            True
        )

        self._status_timer.timeout.connect(
            lambda:
                self.status.setText("")
        )

        self._playback_pulse_timer = QtCore.QTimer(self)
        self._playback_pulse_timer.setInterval(520)
        self._playback_pulse_timer.timeout.connect(
            self._pulse_playback_button
        )

        self._compact_layout = False
        self._init_ui()
        self._connect_signals()
        self._reload_player_queue()
        self._refresh_ui(refresh_playlist=False, refresh_track=False)
        self._sound_disk_revision = self._current_sound_disk_revision()
        self._active_sound_revision = self._current_active_sound_revision()

        self.preview_widget.emit(
            self._preview
        )

    def apply_theme_assets(
        self,
        service: object | None = None,
    ) -> None:
        theme_service = service
        if theme_service is None:
            theme_service = getattr(
                self.window(),
                "theme_service",
                None,
            )
        for name in (
            "stock_btn",
            "archive_btn",
            "clear_btn",
            "single_action_btn",
            "create_playlist_btn",
        ):
            button = getattr(
                self,
                name,
                None,
            )
            if isinstance(button, ThemedArtworkButton):
                button.apply_theme_assets(
                    theme_service
                )

    def set_compact_layout(self, compact: bool) -> None:
        """Keep the transport and both music modes reachable in short windows."""
        if self._compact_layout == compact:
            return
        self._compact_layout = compact
        if compact:
            self.layout().setContentsMargins(12, 0, 12, 0)
            self.layout().setSpacing(2)
        else:
            SOUND_PAGE_LAYOUT.apply(self.layout())
            self.layout().setSpacing(8)
        self._header_layout.setContentsMargins(4, 0, 4, 0 if compact else 2)
        self._header_layout.setSpacing(8 if compact else SECTION_LAYOUT_SPACING)
        self.advanced_btn.setStyleSheet("padding: 2px 6px; min-height: 18px;")
        self._transport_layout.setContentsMargins(4, 2 if compact else 4,
                                                 4, 2 if compact else 4)
        self._status_layout.setContentsMargins(4, 0 if compact else 2, 4, 0)
        self.mode_stack.setMinimumHeight(0 if compact else 230)
        self.single_panel.layout().setContentsMargins(
            12 if compact else 24, 1 if compact else 24,
            12 if compact else 24, 1 if compact else 24,
        )
        self.single_panel.layout().setSpacing(4 if compact else SECTION_LAYOUT_SPACING + 2)
        self.playlist_panel.layout().setContentsMargins(
            8 if compact else 18, 2 if compact else 18,
            8 if compact else 18, 2 if compact else 18,
        )
        self.playlist_panel.layout().setSpacing(4 if compact else SECTION_LAYOUT_SPACING)
        self.playlist_list.setMinimumHeight(56 if compact else 110)
        for button in (self.playlist_preview_btn, self.move_up_btn, self.move_down_btn, self.remove_track_btn):
            button.setStyleSheet("padding: 3px 6px; min-height: 18px;" if compact else "")
        for button, tier in (
            (self.stock_btn, ButtonTier.STANDARD),
            (self.archive_btn, ButtonTier.STANDARD),
            (self.clear_btn, ButtonTier.STANDARD),
            (self.single_action_btn, ButtonTier.MEDIUM),
            (self.create_playlist_btn, ButtonTier.MEDIUM),
        ):
            button.setProperty(BUTTON_GEOMETRY_SCALE_PROPERTY, 0.7 if compact else 1.0)
            button.apply_theme_assets()
            apply_button_tier(button, tier)
        self.updateGeometry()

    def fit_mode_height(self, available_width: int) -> None:
        """Reserve the active card's contents, not the playlist's preferred size."""
        margins = self.layout().contentsMargins()
        width = max(1, available_width - margins.left() - margins.right())
        panel = self.mode_stack.currentWidget()
        height = max(panel.minimumSizeHint().height(), panel.heightForWidth(width))
        self.mode_stack.setMinimumHeight(max(0 if self._compact_layout else 230, height))

    def _init_analysis(
        self,
    ) -> None:
        if (
            not _analysis_requested(
                self.project_root
            )
        ):
            return

        ready, reason = (
            analysis_runtime_status(
                self.project_root
            )
        )

        if not ready:
            logging.getLogger(
                __name__
            ).warning(
                "Sound analysis disabled: %s",
                reason,
            )

            return

        if AudioAnalysisManager is None:
            return

        try:
            manager = AudioAnalysisManager(
                self.project_root,
                parent=self,
            )

            manager.processed_dir = (
                processed_dir(
                    self.project_root
                )
            )

            manager.analysis_dir = (
                analysis_dir(
                    self.project_root
                )
            )

            manager.analysisReady.connect(
                self._on_analysis_ready
            )

            manager.analysisFailed.connect(
                self._on_analysis_failed
            )

            self._analysis = manager

        except Exception as error:
            logging.getLogger(
                __name__
            ).warning(
                "Sound analysis disabled: %s",
                error,
            )

    def _init_ui(
        self,
    ) -> None:
        root = _SoundLayout(
            self
        )

        SOUND_PAGE_LAYOUT.apply(root)
        root.setSpacing(8)

        self.heading = QtWidgets.QLabel(
            "Available Music"
        )
        apply_tab_heading_style(self.heading)
        self.heading.setAlignment(Qt.AlignCenter)
        self.heading.setWordWrap(True)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 2)
        header.setSpacing(SECTION_LAYOUT_SPACING)
        self._header_layout = header
        header.addWidget(self.heading)

        self.stock_btn = ThemedArtworkButton(
            "Stock Music",
            self.project_root,
            "AButton.png",
        )

        self.stock_btn.setToolTip(
            "Choose a bundled stock song for this letter."
        )

        self.stock_btn.setAccessibleName(
            "Stock Music"
        )

        self.archive_btn = ThemedArtworkButton(
            "Music Archive",
            self.project_root,
            "EButton.png",
        )

        self.archive_btn.setToolTip(
            "Choose from music you previously imported."
        )

        self.archive_btn.setAccessibleName(
            "Music Archive"
        )

        self.clear_btn = ThemedArtworkButton(
            "Clear",
            self.project_root,
            "DButton.png",
            broken_artwork_filename="BButton.png",
        )
        self.clear_btn.setProperty("themeRole", "clearAction")

        self.clear_btn.setToolTip(
            "Remove all music assigned to this letter."
        )

        self.clear_btn.setAccessibleName(
            "Clear project music"
        )

        self.import_btn = QtWidgets.QPushButton("Import Music…")
        self.import_btn.setToolTip(
            "Import into Music Archive and assign to this project's playlist."
        )
        self.import_btn.clicked.connect(self._choose_new_files)

        header.addWidget(
            self.stock_btn
        )

        header.addStretch(
            1
        )

        header.addWidget(
            self.archive_btn
        )

        header.addWidget(
            self.import_btn
        )

        root.addLayout(
            header
        )

        playlist_header = QtWidgets.QHBoxLayout()
        self.playlist_heading = QtWidgets.QLabel("Current Playlist")
        playlist_header.addWidget(self.playlist_heading, 1)
        playlist_header.addWidget(self.clear_btn)
        root.addLayout(playlist_header)

        self.mode_stack = QtWidgets.QStackedWidget()
        # fit_mode_height reserves the active card's measured contents. Ignore
        # a playlist's preferred list height when distributing the spare space.
        self.mode_stack.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored,
        )
        self.mode_stack.setMinimumHeight(230)

        self.single_panel = (
            self._build_single_panel()
        )

        self.playlist_panel = (
            self._build_playlist_panel()
        )
        playlist_header.insertWidget(1, self.playlist_summary, 1)
        playlist_header.insertWidget(2, self.add_track_btn)

        self.mode_stack.addWidget(
            self.single_panel
        )

        self.mode_stack.addWidget(
            self.playlist_panel
        )

        root.addWidget(
            self.mode_stack,
            1,
        )

        self.playback_heading = QtWidgets.QLabel("Playback and Settings")
        playback_header = QtWidgets.QHBoxLayout()
        playback_header.addWidget(self.playback_heading, 1)
        root.addLayout(playback_header)

        self.now_playing = QtWidgets.QLabel(
            "No music selected"
        )
        self.now_playing.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

        self.now_playing.setStyleSheet(
            """
            color: #b7c9dc;
            font-size: 10pt;
            padding: 2px 4px;
            """
        )

        playback_header.addWidget(self.now_playing, 2)

        transport = QtWidgets.QHBoxLayout()
        transport.setContentsMargins(4, 4, 4, 4)
        transport.setSpacing(ROW_LAYOUT_SPACING)
        self._transport_layout = transport

        self.prev_btn = QtWidgets.QToolButton()

        self.prev_btn.setText(
            "⏮"
        )

        self.prev_btn.setToolTip(
            "Restart this song, or move to the previous playlist song."
        )

        self.play_btn = QtWidgets.QToolButton()

        self.play_btn.setObjectName(
            "soundPlayButton"
        )

        self.play_btn.setProperty(
            "playing",
            False,
        )

        self.play_btn.setProperty(
            "pulse",
            False,
        )

        self.play_btn.setText(
            "▶"
        )

        self.play_btn.setToolTip(
            "Play or pause the project playlist (Space)."
        )
        self.play_btn.setAccessibleName("Play or pause playlist")
        self.prev_btn.setAccessibleName("Previous track")

        self.next_btn = QtWidgets.QToolButton()

        self.next_btn.setText(
            "⏭"
        )

        self.next_btn.setToolTip(
            "Play the next song in the playlist."
        )
        self.next_btn.setAccessibleName("Next track")

        self.elapsed = QtWidgets.QLabel(
            "0:00"
        )
        self.elapsed.setStyleSheet(
            "font-size: 10pt;"
        )

        self.timeline = CleanSlider()
        self.timeline.setToolTip(
            "Drag to move to another point in the current song."
        )

        self.timeline.setRange(
            0,
            0,
        )

        self.total = QtWidgets.QLabel(
            "0:00"
        )
        self.total.setStyleSheet(
            "font-size: 10pt;"
        )

        self.mute_btn = QtWidgets.QToolButton()

        self.mute_btn.setText(
            "\U0001F50A"
        )

        self.mute_btn.setToolTip(
            "Mute preview (temporary; saved soundtrack volume is unchanged)"
        )
        self.mute_btn.setAccessibleName("Mute preview temporarily")

        self.mute_btn.setFont(
            QtGui.QFont(
                "Segoe UI Emoji",
                20,
            )
        )

        self.mute_btn.setFixedSize(
            58,
            46,
        )

        self.mute_btn.setStyleSheet(
            """
            QToolButton {
                background: #1b2430;
                border: 1px solid #33475f;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
                font-family: "Segoe UI Emoji";
                font-size: 20px;
            }

            QToolButton:hover {
                border-color: #00c8ff;
                background: #233447;
            }

            QToolButton#soundPlayButton[playing="true"] {
                color: #9bfffb;
                border-color: #00c8ff;
            }

            QToolButton#soundPlayButton[playing="true"][pulse="true"] {
                background: #294858;
                border-color: #d8ffff;
            }
            """
        )

        self.volume = CleanSlider()
        self.volume.setToolTip(
            "Project soundtrack volume, saved from 0 to 100."
        )
        self.volume.setAccessibleName("Project soundtrack volume")
        self.volume_value = QtWidgets.QLabel()

        self.volume.setRange(
            0,
            100,
        )

        self.volume.setValue(
            self._load_volume()
        )
        self._last_saved_volume = self.volume.value()

        self.volume.setFixedWidth(
            156
        )

        for button in (
            self.prev_btn,
            self.play_btn,
            self.next_btn,
        ):
            button.setFixedSize(
                48,
                42,
            )

            button.setFont(
                QtGui.QFont(
                    "Segoe UI Symbol",
                    18,
                )
            )

        transport.addWidget(
            self.prev_btn
        )

        transport.addWidget(
            self.play_btn
        )

        transport.addWidget(
            self.next_btn
        )

        transport.addWidget(
            self.elapsed
        )

        transport.addWidget(
            self.timeline,
            1,
        )

        transport.addWidget(
            self.total
        )

        transport.addWidget(
            self.mute_btn
        )

        transport.addWidget(
            self.volume
        )
        transport.addWidget(self.volume_value)
        self.volume_value.setText(str(self.volume.value()))
        self.volume.valueChanged.connect(lambda value: self.volume_value.setText(str(value)))

        root.addLayout(
            transport
        )

        self.advanced_btn = QtWidgets.QToolButton()
        self.advanced_btn.setText("Advanced Audio")
        self.advanced_btn.setCheckable(True)
        self.advanced_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_btn.setArrowType(Qt.RightArrow)
        playback_header.addWidget(self.advanced_btn)
        self.advanced_panel = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QHBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(4, 0, 4, 0)
        advanced_layout.addWidget(self.convert_single_btn)
        self.repair_archive_btn = QtWidgets.QPushButton("Repair Music Archive")
        self.repair_archive_btn.setToolTip("Check and repair imported audio using the existing archive tools.")
        self.repair_archive_btn.clicked.connect(self.repair_music_archive)
        advanced_layout.addWidget(self.repair_archive_btn)
        advanced_layout.addStretch(1)
        root.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        self.advanced_btn.toggled.connect(self.advanced_panel.setVisible)
        self.advanced_btn.toggled.connect(
            lambda expanded: self.advanced_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        )
        self._play_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Space"), self.playlist_list)
        self._play_shortcut.setContext(Qt.WidgetShortcut)
        self._play_shortcut.activated.connect(self._toggle_play)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(4, 2, 4, 0)
        status_row.setSpacing(ROW_LAYOUT_SPACING)
        self._status_layout = status_row

        self.status = QtWidgets.QLabel(
            ""
        )

        self.status.setStyleSheet(
            """
            color: #91a8bd;
            min-height: 22px;
            """
        )

        self.cancel_job_btn = QtWidgets.QPushButton(
            "Cancel"
        )
        self.cancel_job_btn.setToolTip(
            "Stop the active music import or archive repair."
        )

        self.cancel_job_btn.hide()

        status_row.addWidget(
            self.status,
            1,
        )

        status_row.addWidget(
            self.cancel_job_btn
        )

        root.addLayout(
            status_row
        )

        self.setAcceptDrops(
            True
        )

        self.setStyleSheet(
            """
            QWidget {
                color: #e7eef8;
            }

            QPushButton,
            QToolButton {
                background: #1b2430;
                border: 1px solid #33475f;
                border-radius: 8px;
                padding: 8px 12px;
                min-height: 22px;
            }

            QPushButton:hover,
            QToolButton:hover {
                border-color: #00c8ff;
                background: #233447;
            }

            QStackedWidget,
            QListWidget {
                background: transparent;
                border: none;
            }
            """
        )

    def _build_single_panel(
        self,
    ) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()

        panel.setObjectName(
            "singleCard"
        )

        layout = QtWidgets.QVBoxLayout(
            panel
        )

        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(SECTION_LAYOUT_SPACING + 2)

        self.single_title = QtWidgets.QLabel(
            "No music selected"
        )
        self.single_title.setWordWrap(True)

        self.single_title.setAlignment(
            Qt.AlignCenter
        )

        self.single_title.setStyleSheet(
            """
            font-size: 19px;
            font-weight: 650;
            color: #eaf7ff;
            """
        )

        self.single_detail = QtWidgets.QLabel(
            "No music is required. Add a track or keep this project silent."
        )

        self.single_detail.setAlignment(
            Qt.AlignCenter
        )

        self.single_detail.setWordWrap(
            True
        )

        self.single_detail.setStyleSheet(
            "color: #93a8bd;font-size:11pt;"
        )

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(ROW_LAYOUT_SPACING + 2)

        self.single_action_btn = ThemedArtworkButton(
            "Add Music",
            self.project_root,
            "BButton.png",
        )
        self.single_action_btn.setToolTip(
            "Choose music from your computer or recently used songs."
        )
        apply_button_tier(self.single_action_btn, ButtonTier.MEDIUM)
        ensure_button_text_fits(self.single_action_btn)

        self.create_playlist_btn = ThemedArtworkButton(
            "Create Playlist",
            self.project_root,
            "BButton.png",
        )
        self.create_playlist_btn.setToolTip(
            "Keep this song and add more tracks as a playlist."
        )
        apply_button_tier(self.create_playlist_btn, ButtonTier.MEDIUM)
        ensure_button_text_fits(self.create_playlist_btn)

        self.create_playlist_btn.hide()

        button_row.addStretch(
            1
        )

        button_row.addWidget(
            self.single_action_btn
        )

        button_row.addWidget(
            self.create_playlist_btn
        )

        button_row.addStretch(
            1
        )

        layout.addStretch(
            1
        )

        layout.addWidget(
            self.single_title
        )

        layout.addWidget(
            self.single_detail
        )

        layout.addLayout(
            button_row
        )

        layout.addStretch(
            1
        )

        panel.setStyleSheet(
            """
            QFrame#singleCard {
                background: #121820;
                border: 1px solid #2b3a4d;
                border-radius: 12px;
            }
            """
        )

        return panel

    def _build_playlist_panel(
        self,
    ) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()

        panel.setObjectName(
            "playlistPanel"
        )

        layout = QtWidgets.QVBoxLayout(
            panel
        )

        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(SECTION_LAYOUT_SPACING)

        self.playlist_summary = QtWidgets.QLabel(
            "Playlist"
        )

        self.add_track_btn = QtWidgets.QPushButton(
            "Add to Playlist"
        )
        self.add_track_btn.setToolTip(
            "Choose music from your computer or recently used songs."
        )

        self.convert_single_btn = QtWidgets.QPushButton(
            "Convert to Single Track"
        )
        self.convert_single_btn.setToolTip(
            "Keep the selected song and remove playlist mode."
        )

        for button in (
            self.add_track_btn,
            self.convert_single_btn,
        ):
            button.setMinimumHeight(
                40
            )

        self.playlist_list = QtWidgets.QListWidget()
        self.playlist_list.setObjectName("playlistList")
        self.playlist_list.setToolTip(
            "Select a track, then Preview Track or press Enter. Drag or Ctrl+Up/Down to reorder; Delete removes the assignment."
        )
        self.playlist_list.setAccessibleName("Current project playlist")
        self.playlist_list.setFlow(QtWidgets.QListView.TopToBottom)
        self.playlist_list.setWrapping(False)
        self.playlist_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.playlist_list.setMinimumHeight(110)
        self.playlist_list.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored,
        )
        self.playlist_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_list.setStyleSheet(
            "QListWidget#playlistList::item{padding:0;border:none;"
            "background:transparent;}"
        )

        self.playlist_list.setDragDropMode(
            QtWidgets.QAbstractItemView.InternalMove
        )

        self.playlist_list.setDefaultDropAction(
            Qt.MoveAction
        )

        self.playlist_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )

        self.playlist_list.setSpacing(6)

        self.playlist_list.model().rowsMoved.connect(
            self._playlist_rows_moved
        )

        layout.addWidget(
            self.playlist_list,
            1,
        )

        actions = QtWidgets.QHBoxLayout()
        self.playlist_preview_btn = QtWidgets.QPushButton("Preview Track")
        self.move_up_btn = QtWidgets.QPushButton("Move Up")
        self.move_down_btn = QtWidgets.QPushButton("Move Down")
        self.remove_track_btn = QtWidgets.QPushButton("Remove from Playlist")
        self.remove_track_btn.setToolTip("Remove this assignment; keep the source file and Music Archive item.")
        for button in (self.playlist_preview_btn, self.move_up_btn, self.move_down_btn, self.remove_track_btn):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.playlist_preview_btn.clicked.connect(self._preview_selected_track)
        self.move_up_btn.clicked.connect(lambda: self._move_selected_track(-1))
        self.move_down_btn.clicked.connect(lambda: self._move_selected_track(1))
        self.remove_track_btn.clicked.connect(self._remove_selected_track)
        self._playlist_shortcuts = []
        for key, callback in (
            ("Return", self._preview_selected_track),
            ("Ctrl+Up", lambda: self._move_selected_track(-1)),
            ("Ctrl+Down", lambda: self._move_selected_track(1)),
            ("Delete", self._remove_selected_track),
        ):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.playlist_list)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._playlist_shortcuts.append(shortcut)

        panel.setStyleSheet(
            """
            QFrame#playlistPanel {
                background: #111820;
                border: 1px solid #2b3a4d;
                border-radius: 12px;
            }
            """
        )

        return panel

    def _connect_signals(
        self,
    ) -> None:
        self.single_action_btn.clicked.connect(
            self._open_user_music_for_current_mode
        )

        self.create_playlist_btn.clicked.connect(
            self._create_playlist
        )

        self.add_track_btn.clicked.connect(
            self._open_user_music_for_current_mode
        )

        self.archive_btn.clicked.connect(
            self._open_archive_for_current_mode
        )

        self.stock_btn.clicked.connect(
            self._open_stock_for_current_mode
        )

        self.convert_single_btn.clicked.connect(
            self._convert_to_single
        )

        self.clear_btn.clicked.connect(
            self._clear_project_sound
        )

        self.prev_btn.clicked.connect(
            self.player.previous
        )

        self.play_btn.clicked.connect(
            self._toggle_play
        )

        self.next_btn.clicked.connect(
            lambda:
                self.player.next(
                    autoplay=True
                )
        )

        self.mute_btn.clicked.connect(
            self._toggle_mute
        )

        self.volume.valueChanged.connect(
            self._volume_changed
        )

        self.volume.sliderReleased.connect(
            self._save_volume
        )

        self.timeline.sliderMoved.connect(
            self.player.seek
        )

        self.playlist_list.currentItemChanged.connect(
            lambda item, _previous: self._playlist_item_selected(item)
        )

        self.playlist_list.itemDoubleClicked.connect(
            self._playlist_item_activated
        )

        self.cancel_job_btn.clicked.connect(
            self._cancel_background_job
        )

        self.player.trackChanged.connect(
            self._on_track_changed
        )

        self.player.playbackChanged.connect(
            lambda playing:
                self.play_btn.setText(
                    "⏸"
                    if playing
                    else "▶"
                )
        )

        self.player.playbackChanged.connect(
            self._on_playback_changed
        )
        self.player.transportStateChanged.connect(self._transport_state_changed)

        self.player.positionChanged.connect(
            self._on_position_changed
        )

        self.player.activePlayerChanged.connect(
            self._on_active_player_changed
        )

        self.player.error.connect(
            self._playback_failed
        )

        self.player.finished.connect(
            lambda:
                self._show_status(
                    "Playback finished."
                )
        )

        self.library.changed.connect(self._library_state_changed)

        self.project_sound.changed.connect(
            self._project_state_changed
        )

    def _on_playback_changed(
        self,
        playing: bool,
    ) -> None:
        self._transport_state_changed(self.player.transport_state)
        playing = bool(playing)
        self.play_btn.setProperty("playing", playing)
        self.play_btn.setProperty("pulse", False)
        if playing:
            self._playback_pulse_timer.start()
            if self._analysis is None and not self._analysis_disabled:
                self._init_analysis()
            self._prime_analysis_for_current()
        else:
            self._playback_pulse_timer.stop()
        self._polish_playback_button()

    def _pulse_playback_button(self) -> None:
        if not self.play_btn.property("playing"):
            self._playback_pulse_timer.stop()
            return
        self.play_btn.setProperty(
            "pulse",
            not bool(self.play_btn.property("pulse")),
        )
        self._polish_playback_button()

    def _polish_playback_button(self) -> None:
        self.play_btn.style().unpolish(self.play_btn)
        self.play_btn.style().polish(self.play_btn)
        self.play_btn.update()

    def _load_volume(
        self,
    ) -> int:
        settings = _read_settings(
            self.project_root
        )

        try:
            return max(
                0,
                min(
                    100,
                    int(
                        settings.get(
                            "music_volume",
                            STARTING_VOLUME,
                        )
                    ),
                ),
            )

        except (
            TypeError,
            ValueError,
        ):
            return int(
                STARTING_VOLUME
            )

    def _save_volume(
        self,
    ) -> None:
        session = peek_project_session(self.project_root)
        if session is not None and session.is_restoring:
            return
        value = self.volume.value()
        if value == getattr(self, "_last_saved_volume", None):
            return
        settings = _read_settings(
            self.project_root
        )

        settings["music_volume"] = value
        settings["starting_volume"] = value

        try:
            _write_settings(self.project_root, settings)
        except OSError:
            _LOGGER.exception("Could not save soundtrack volume")
            self._show_status("The soundtrack volume could not be saved. Please try again.", persistent=True)
            return
        self._last_saved_volume = value

    def _volume_changed(self, value: int) -> None:
        value = max(
            0,
            min(
                100,
                int(value),
            ),
        )

        self.player.set_volume(value)
        self._save_volume()

        # Notify Nexus so the Forge preview adopts the new volume.
        self.volume_changed.emit(value)

    def _toggle_mute(self) -> None:
        muted = self.player.toggle_mute()

        self.mute_btn.setText(
            "\U0001F507"
            if muted
            else "\U0001F50A"
        )

        self.mute_btn.setToolTip(
            "Unmute preview (temporary)"
            if muted
            else "Mute preview (temporary; saved soundtrack volume is unchanged)"
        )
        self.mute_btn.setAccessibleName(self.mute_btn.toolTip())

        # Notify Nexus so the Forge preview adopts the mute state.
        self.sound_state_changed.emit(
            "muted"
            if muted
            else "unmuted"
        )

    def _project_state_changed(self) -> None:
        self._sync_project_sound_autosave()
        self._reload_player_queue()
        self._refresh_ui(refresh_playlist=False, refresh_track=False)
        self._sound_disk_revision = self._current_sound_disk_revision()
        self._active_sound_revision = self._current_active_sound_revision()

        # Notify Nexus whenever the selected track or playlist changes.
        self.sound_state_changed.emit(
            "project-sound-changed"
        )

    def _library_state_changed(self) -> None:
        self._sound_disk_revision = self._current_sound_disk_revision()
        self._active_sound_revision = self._current_active_sound_revision()
        self._refresh_ui()

    def _sync_project_sound_autosave(self) -> None:
        if not self.project_state.is_project_ready:
            return
        source = project_sound_path(self.project_root)
        if not source.is_file():
            return
        try:
            self.project_save_service.copy_workspace_file(
                source,
                Path("sounds") / "project_sound.json",
            )
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Could not autosave project sound state: %s",
                error,
            )

    def _reload_player_queue(
        self,
    ) -> None:
        self.project_sound.reconcile_missing_files()

        self.player.set_queue(
            self.project_sound.ordered_ids(),
            self.project_sound.state.selected_track_id,
        )

        if hasattr(
            self,
            "volume",
        ):
            volume = self.volume.value()

        else:
            volume = self._load_volume()

        self.player.set_volume(
            volume
        )

    def _refresh_ui(
        self,
        *,
        refresh_playlist: bool = True,
        refresh_track: bool = True,
    ) -> None:
        state = self.project_sound.state
        self.playlist_summary.setVisible(state.mode == "playlist")
        self.add_track_btn.setVisible(state.mode == "playlist")

        if state.mode == "playlist":
            self.create_playlist_btn.hide()

            self.mode_stack.setCurrentWidget(
                self.playlist_panel
            )

            if refresh_playlist:
                self._refresh_playlist()

        else:
            self.mode_stack.setCurrentWidget(
                self.single_panel
            )

            record = self.library.get(
                state.single_track_id
            )

            if (
                self.library.path_for(
                    state.single_track_id
                )
                is None
            ):
                record = None

            has_single_track = (
                record is not None
            )

            self.create_playlist_btn.setVisible(
                has_single_track
            )

            if record is None:
                self.single_title.setText(
                    "Track unavailable" if state.single_track_id else "No music selected"
                )

                self.single_detail.setText(
                    "The assignment is retained. Restore the source or repair Music Archive."
                    if state.single_track_id else
                    "No music is required. Add a track or keep this project silent."
                )

                self.single_action_btn.setText(
                    "Add Music"
                )

            else:
                self.single_title.setText(
                    record.display_title
                )

                self.single_detail.setText(
                    f"{_format_duration(record.duration_seconds)}"
                    f"  •  {'Stock' if record.source_kind == 'stock' else 'Music Archive'}"
                    f"  •  {record.original_name}"
                )

                self.single_action_btn.setText(
                    "Replace Music"
                )

            ensure_button_text_fits(self.single_action_btn)

        self._sync_transport_enabled()

        if refresh_track:
            self._on_track_changed(
                self.player.current_track_id,
                persist_selection=False,
            )

    def _refresh_playlist(
        self,
    ) -> None:
        self.playlist_list.blockSignals(
            True
        )

        active = self.player.current_track_id if self.player.is_playing() else ""
        current = self.playlist_list.currentItem()
        selected = current.data(Qt.UserRole) if current is not None else ""
        missing = 0

        total_duration = 0.0
        rows: list[tuple[str, TrackRecord]] = []
        for track_id in (
            self.project_sound
            .state
            .playlist
        ):
            record = self.library.get(
                track_id
            )

            unavailable = self.library.path_for(track_id) is None
            if record is None:
                record = TrackRecord(track_id, "", "Missing track", "", "", "")
            if unavailable:
                missing += 1
                record = TrackRecord.from_dict({
                    **record.to_dict(),
                    "display_title": f"{record.display_title} — unavailable",
                })

            total_duration += (
                record.duration_seconds
            )
            rows.append((track_id, record))

        existing_matches = self.playlist_list.count() == len(rows)
        existing_widgets: list[PlaylistItemWidget] = []
        if existing_matches:
            for index, (track_id, record) in enumerate(rows):
                item = self.playlist_list.item(index)
                widget = self.playlist_list.itemWidget(item)
                signature = (
                    record.display_title,
                    record.original_name,
                    float(record.duration_seconds),
                )
                if (
                    item.data(Qt.UserRole) != track_id
                    or not isinstance(widget, PlaylistItemWidget)
                    or widget.record_signature != signature
                ):
                    existing_matches = False
                    break
                existing_widgets.append(widget)

        if existing_matches:
            for widget in existing_widgets:
                widget.set_active(widget.track_id == active)
                widget.set_selected(widget.track_id == selected)
        else:
            self.playlist_list.clear()
            for track_id, record in rows:
                item = QtWidgets.QListWidgetItem()
                item.setData(Qt.UserRole, track_id)
                widget = PlaylistItemWidget(
                    record,
                    track_id == active,
                )
                widget.apply_theme_assets(
                    getattr(self.window(), "theme_service", None)
                )
                widget.removeRequested.connect(
                    self.project_sound.remove_from_playlist
                )
                item.setSizeHint(QtCore.QSize(274, 44))
                self.playlist_list.addItem(item)
                self.playlist_list.setItemWidget(item, widget)
                item.setText(record.display_title)
                if track_id == selected:
                    self.playlist_list.setCurrentItem(item)
                    widget.set_selected(True)

        self.playlist_list.blockSignals(
            False
        )

        count = self.playlist_list.count()

        if count != 1:
            suffix = "s"

        else:
            suffix = ""

        self.playlist_summary.setText(
            f"Playlist • {count} track{suffix} • "
            f"{_format_duration(total_duration)}"
            + (f" • {missing} unavailable" if missing else "")
            if count else "No music is required. This project is silent."
        )
        self._playlist_item_selected(self.playlist_list.currentItem())

    def _playlist_item_selected(
        self,
        item: QtWidgets.QListWidgetItem | None,
    ) -> None:
        selected = item.data(Qt.UserRole) if item is not None else ""
        row = self.playlist_list.row(item) if item is not None else -1
        for index in range(self.playlist_list.count()):
            widget = self.playlist_list.itemWidget(self.playlist_list.item(index))
            if isinstance(widget, PlaylistItemWidget):
                widget.set_selected(widget.track_id == selected)
        self.playlist_preview_btn.setEnabled(bool(selected) and self.library.path_for(selected) is not None)
        self.remove_track_btn.setEnabled(bool(selected))
        self.move_up_btn.setEnabled(row > 0)
        self.move_down_btn.setEnabled(0 <= row < self.playlist_list.count() - 1)

    def _preview_selected_track(self) -> None:
        item = self.playlist_list.currentItem()
        if item is not None:
            self._playlist_item_activated(item)

    def _move_selected_track(self, offset: int) -> None:
        item = self.playlist_list.currentItem()
        if item is None or self._button_busy:
            return
        track_ids = self.project_sound.ordered_ids()
        track_id = item.data(Qt.UserRole)
        if track_id not in track_ids:
            return
        index = track_ids.index(track_id)
        target = index + offset
        if 0 <= target < len(track_ids):
            track_ids[index], track_ids[target] = track_ids[target], track_ids[index]
            self.project_sound.reorder_playlist(track_ids)
            self._refresh_playlist()

    def _remove_selected_track(self) -> None:
        item = self.playlist_list.currentItem()
        if item is not None and not self._button_busy:
            self.project_sound.remove_from_playlist(item.data(Qt.UserRole))

    def _playlist_item_activated(
        self,
        item: QtWidgets.QListWidgetItem,
    ) -> None:
        track_id = str(
            item.data(
                Qt.UserRole
            )
            or ""
        )

        if not track_id or not self._tab_active:
            return
        if self.library.path_for(track_id) is None:
            self._show_status("This track is unavailable. Restore the source or repair Music Archive.", persistent=True)
            return

        self.project_sound.select_track(
            track_id
        )

        self.player.select_track(
            track_id,
            autoplay=True,
        )

    def _playlist_rows_moved(
        self,
        *_arguments,
    ) -> None:
        track_ids: list[str] = []

        for row in range(
            self.playlist_list.count()
        ):
            item = self.playlist_list.item(
                row
            )

            track_ids.append(
                str(
                    item.data(
                        Qt.UserRole
                    )
                    or ""
                )
            )

        self.project_sound.reorder_playlist(
            [
                track_id
                for track_id
                in track_ids
                if track_id
            ]
        )

    def _sync_transport_enabled(
        self,
    ) -> None:
        ordered_ids = self.project_sound.ordered_ids()
        has_tracks = bool(ordered_ids)
        playlist_mode = self.project_sound.state.mode == "playlist"
        busy = self._button_busy
        playable = any(self.library.path_for(track_id) is not None for track_id in ordered_ids)

        set_control_invisible(
            self.play_btn,
            False,
            available=playable,
        )

        set_control_invisible(
            self.prev_btn,
            False,
            available=has_tracks,
        )

        set_control_invisible(
            self.next_btn,
            False,
            available=playlist_mode and len(ordered_ids) > 1,
        )

        self.timeline.setEnabled(
            has_tracks
        )
        self.mute_btn.setEnabled(playable)
        self.volume.setEnabled(True)
        self.import_btn.setEnabled(not busy)
        self.repair_archive_btn.setEnabled(not busy)

        self.clear_btn.set_action_state(
            broken=not has_tracks,
            invisible=busy,
        )
        for button in (
            self.single_action_btn,
            self.create_playlist_btn,
            self.archive_btn,
            self.stock_btn,
        ):
            button.set_action_state(
                broken=False,
                invisible=busy,
            )
        set_control_invisible(
            self.add_track_btn,
            busy,
            available=playlist_mode,
        )
        set_control_invisible(
            self.convert_single_btn,
            busy,
            available=playlist_mode and has_tracks,
        )

    def _choose_new_files(
        self,
    ) -> None:
        start = _downloads_directory()

        if (
            self.project_sound
            .state
            .mode
            == "playlist"
        ):
            paths, _selected_filter = (
                QtWidgets.QFileDialog
                .getOpenFileNames(
                    self,
                    "Add Playlist Tracks",
                    start,
                    (
                        "Audio Files "
                        "(*.mp3 *.wav *.ogg "
                        "*.aac *.m4a *.flac)"
                    ),
                )
            )

        else:
            path, _selected_filter = (
                QtWidgets.QFileDialog
                .getOpenFileName(
                    self,
                    "Choose Music",
                    start,
                    (
                        "Audio Files "
                        "(*.mp3 *.wav *.ogg "
                        "*.aac *.m4a *.flac)"
                    ),
                )
            )

            if path:
                paths = [
                    path
                ]

            else:
                paths = []

        if paths:
            self._remember_music_folder(
                paths[0]
            )

            self._start_import(
                paths
            )

    def _remember_music_folder(
        self,
        path: str,
    ) -> None:
        settings = _read_settings(
            self.project_root
        )

        settings[
            LAST_MUSIC_FOLDER_KEY
        ] = str(
            Path(
                path
            ).resolve().parent
        )

        _write_settings(
            self.project_root,
            settings,
        )

    def _start_import(
        self,
        paths: list[str],
    ) -> None:
        if self._import_thread is not None or self._repair_thread is not None:
            return

        self._import_context = self._project_context()
        self._pending_import_mode = self.project_sound.state.mode

        self.player.stop(
            reset_position=True
        )

        known = {
            record.content_hash:
                record.track_id
            for record
            in self.library.records.values()
            if self.library.path_for(record.track_id) is not None
        }

        thread = QtCore.QThread(
            self
        )

        worker = _ImportWorker(
            self.project_root,
            paths,
            known,
        )

        worker.moveToThread(
            thread
        )

        thread.started.connect(
            worker.run
        )

        generation = self._background_generation
        worker.finished.connect(
            lambda payload, value=generation:
                self._import_result_ready.emit(value, payload)
        )

        worker.failed.connect(
            lambda message, value=generation:
                self._import_error_ready.emit(value, message)
        )

        worker.finished.connect(
            thread.quit
        )

        worker.failed.connect(
            thread.quit
        )

        worker.finished.connect(
            worker.deleteLater
        )

        worker.failed.connect(
            worker.deleteLater
        )

        thread.finished.connect(
            thread.deleteLater
        )

        thread.finished.connect(
            self._clear_import_handles
        )

        self._import_thread = thread
        self._import_worker = worker

        if len(
            paths
        ) != 1:
            suffix = "s"

        else:
            suffix = ""

        self._set_busy(
            True,
            f"Importing {len(paths)} track{suffix} into Music Archive and this project…",
        )

        thread.start()

    @QtCore.Slot(int, object)
    def _accept_import_finished(
        self,
        generation: int,
        payloads: list[dict],
    ) -> None:
        if generation == self._background_generation and not self._shutdown:
            self._import_finished(payloads)

    @QtCore.Slot(int, str)
    def _accept_import_failed(self, generation: int, message: str) -> None:
        if generation == self._background_generation and not self._shutdown:
            self._import_failed(message)

    def _import_finished(
        self,
        payloads: list[dict],
    ) -> None:
        track_ids = self.library.register_imports(
            payloads
        )

        if getattr(self, "_import_context", self._project_context()) != self._project_context():
            self._show_status("Imported into Music Archive. The project changed; no tracks were assigned.", persistent=True)
            return

        if (
            self._pending_import_mode
            == "playlist"
        ):
            self.project_sound.add_to_playlist(
                track_ids
            )

        elif track_ids:
            self.project_sound.set_single(
                track_ids[0]
            )

        self._remember_tracks(track_ids)

        if len(
            track_ids
        ) != 1:
            suffix = "s"

        else:
            suffix = ""

        self._show_status(
            f"Imported {len(track_ids)} track{suffix} into Music Archive and assigned to this project."
        )
        if track_ids:
            play_ui_sound(UiSound.ADDED)

    def _import_failed(
        self,
        message: str,
    ) -> None:
        if "canceled" in message.casefold():
            self._show_status(
                "Import canceled."
            )

        else:
            _LOGGER.warning("Music import failed: %s", message)
            detail = "Music could not be imported. Check the file and audio conversion tools; existing music is unchanged."
            self._show_status(
                detail,
                persistent=True,
            )

            play_ui_sound(UiSound.ERROR)
            show_lettersmith_message(self, "Audio Import Error", detail)

    def _clear_import_handles(
        self,
    ) -> None:
        self._import_thread = None
        self._import_worker = None

        self._set_busy(
            False
        )

    def _set_busy(
        self,
        busy: bool,
        message: str = "",
    ) -> None:
        self._button_busy = bool(busy)
        self._sync_transport_enabled()

        self._set_repair_action_enabled(not busy)

        self.cancel_job_btn.setVisible(
            busy
        )

        if message:
            self.status.setText(
                message
            )

            self._status_timer.stop()

    def _cancel_background_job(
        self,
    ) -> None:
        if self._import_worker is not None:
            self._import_worker.cancel()

        if self._repair_worker is not None:
            self._repair_worker.cancel()

    def _open_archive_for_current_mode(
        self,
    ) -> None:
        dialog = ArchiveDialog(
            self.library,
            lambda:
                set(
                    self.project_sound
                    .ordered_ids()
                ),
            self._delete_archive_track,
            multi_select=(
                self.project_sound
                .state
                .mode
                == "playlist"
            ),
            parent=self,
        )

        self._exec_music_popup(dialog)

    def _open_stock_for_current_mode(
        self,
    ) -> None:
        self._open_music_selector(user_music=False)

    def _open_user_music_for_current_mode(self) -> None:
        self._open_music_selector(user_music=True)

    def _open_music_selector(self, *, user_music: bool) -> None:
        dialog = StockMusicDialog(
            self.library,
            multi_select=(
                self.project_sound
                .state
                .mode
                == "playlist"
            ),
            parent=self,
            recent_track_ids=tuple(
                track_id for track_id, _label in recent_media(
                    self.project_root,
                    self.project_state.identity.project_id,
                    "music",
                )
                if self.library.path_for(track_id) is not None
            ) if user_music else (),
            allow_browse=user_music,
        )

        self._exec_music_popup(dialog)

        if dialog.browse_requested:
            self._choose_new_files()

    def _exec_music_popup(self, dialog: ArchiveDialog | StockMusicDialog) -> None:
        context = self._project_context()
        chosen: list[str] = []
        dialog.previewRequested.connect(self.player.pause_for_preview)
        dialog.tracksChosen.connect(chosen.extend)
        try:
            dialog.exec()
        finally:
            # Release the preview before allowing normal playback to resume.
            dialog._stop_preview()
            self.player.resume_after_preview(
                resume=not self._shutdown and self._tab_active and context == self._project_context()
            )
            dialog.deleteLater()
        if chosen and not self._shutdown and context == self._project_context():
            # Applying a new letter selection retains its existing transport behavior.
            self._archive_tracks_chosen(chosen)

    def _project_context(self) -> tuple[str, int]:
        session = peek_project_session(self.project_root)
        identity = session.session_id if session is not None else self.project_state.identity.project_id
        return (identity, self._background_generation)

    def _remember_tracks(self, track_ids: list[str]) -> None:
        if not self.project_state.is_project_ready:
            return
        for track_id in track_ids:
            record = self.library.get(track_id)
            if record is None or self.library.path_for(track_id) is None:
                continue
            try:
                remember_media(
                    self.project_root,
                    self.project_state.identity.project_id,
                    "music",
                    track_id,
                    record.display_title,
                )
            except OSError:
                _LOGGER.exception("Could not save recent music history.")

    def _archive_tracks_chosen(
        self,
        track_ids: list[str],
    ) -> None:
        previous_ids = tuple(self.project_sound.ordered_ids())
        if (
            self.project_sound
            .state
            .mode
            == "playlist"
        ):
            self.project_sound.add_to_playlist(
                track_ids
            )

        elif track_ids:
            self.project_sound.set_single(
                track_ids[0]
            )

        self._remember_tracks(track_ids)

        if tuple(self.project_sound.ordered_ids()) != previous_ids:
            play_ui_sound(UiSound.ADDED)

    def _delete_archive_track(
        self,
        track_id: str,
    ) -> bool:
        record = self.library.get(
            track_id
        )

        if record is None:
            return False

        state_backup = (
            ProjectSoundState
            .from_dict(
                self.project_sound
                .state
                .to_dict()
            )
        )

        used = (
            self.project_sound
            .state
            .is_using(
                track_id
            )
        )

        if used:
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Track Is In Use",
                question=(
                    f"{record.display_title} is used by this letter. "
                    "Remove it from the letter, or delete it from the archive?"
                ),
                primary_text="Delete From Archive",
                secondary_text="Remove From This Letter",
                secondary_accepts=True,
                cancel_text="Cancel",
                destructive_primary=True,
                click_outside_dismiss=False,
                width=680,
            )
            confirmation.exec()

            if confirmation.choice == "secondary":
                self.project_sound.remove_usage(
                    track_id
                )

                play_ui_sound(UiSound.REMOVED)

                return False

            if confirmation.choice != "primary":
                return False

            self.project_sound.remove_usage(
                track_id
            )

        else:
            confirmation = LetterSmithConfirmationDialog(
                self,
                title="Delete Track",
                question=(
                    f"Delete {record.display_title} from the music archive?"
                ),
                primary_text="Yes",
                secondary_text="No",
                destructive_primary=True,
                click_outside_dismiss=False,
            )
            if confirmation.exec() != QtWidgets.QDialog.Accepted:
                return False

        self.player.release_current_file_handle()

        deleted = self.library.delete_track(
            track_id
        )

        if not deleted:
            self.project_sound.state = (
                state_backup
            )

            self.project_sound.save()

            self._show_status(
                (
                    "The track could not be deleted "
                    "because its file is still in use."
                ),
                persistent=True,
            )

            return False

        return True

    def repair_music_archive(self) -> None:
        """Start the single Settings-owned archive repair operation."""
        self._start_archive_repair()

    def _set_repair_action_enabled(self, enabled: bool) -> None:
        title_bar = getattr(self.window(), "title_bar", None)
        action = getattr(title_bar, "repair_music_action", None)
        if action is not None:
            action.setEnabled(bool(enabled))

    def _start_archive_repair(
        self,
    ) -> None:
        if self._repair_thread is not None or self._import_thread is not None:
            return

        thread = QtCore.QThread(
            self
        )

        worker = _RepairWorker(
            self.project_root
        )

        worker.moveToThread(
            thread
        )

        thread.started.connect(
            worker.run
        )

        generation = self._background_generation
        worker.finished.connect(
            lambda repaired, issues, value=generation:
                self._accept_repair_finished(value, repaired, issues)
        )

        worker.failed.connect(
            lambda message, value=generation:
                self._accept_repair_failed(value, message)
        )

        worker.finished.connect(
            thread.quit
        )

        worker.failed.connect(
            thread.quit
        )

        worker.finished.connect(
            worker.deleteLater
        )

        worker.failed.connect(
            worker.deleteLater
        )

        thread.finished.connect(
            thread.deleteLater
        )

        thread.finished.connect(
            self._clear_repair_handles
        )

        self._repair_thread = thread
        self._repair_worker = worker
        self._set_repair_action_enabled(False)

        self._set_busy(
            True,
            "Repairing music archive…",
        )

        thread.start()

    def _accept_repair_finished(
        self,
        generation: int,
        repaired: int,
        issues: list[str],
    ) -> None:
        if generation == self._background_generation and not self._shutdown:
            self._repair_finished(repaired, issues)

    def _accept_repair_failed(self, generation: int, message: str) -> None:
        if generation == self._background_generation and not self._shutdown:
            self._repair_failed(message)

    def _repair_finished(
        self,
        repaired: int,
        issues: list[str],
    ) -> None:
        self.library.replace_records(load_library(self.project_root))

        self.library.changed.emit()

        if issues:
            for issue in issues:
                _LOGGER.warning("Music archive repair: %s", issue)
            show_lettersmith_message(
                self,
                "Archive Repair",
                (
                    f"Repaired {repaired} item(s).\n\n"
                    f"{len(issues)} item(s) could not be repaired. Check their original files and audio tools."
                ),
            )

        else:
            self._show_status(
                (
                    "Archive repair completed: "
                    f"{repaired} item(s) repaired."
                )
            )

    def _repair_failed(
        self,
        message: str,
    ) -> None:
        _LOGGER.warning("Music archive repair failed: %s", message)
        self._show_status(
            "Music Archive could not be repaired. Check the original files and audio tools.",
            persistent=True,
        )
        play_ui_sound(UiSound.ERROR)

    def _clear_repair_handles(
        self,
    ) -> None:
        self._repair_thread = None
        self._repair_worker = None
        self._set_repair_action_enabled(True)

        self._set_busy(
            False
        )

    def _create_playlist(
        self,
    ) -> None:
        previous_mode = self.project_sound.state.mode
        self.project_sound.create_playlist()
        if self.project_sound.state.mode != previous_mode:
            play_ui_sound(UiSound.BLIP)

        if not self.project_sound.state.playlist:
            self._choose_new_files()

    def _convert_to_single(
        self,
    ) -> None:
        selected = (
            self.project_sound
            .state
            .selected_track_id
        )

        row = self.playlist_list.currentRow()

        if row >= 0:
            selected = str(
                self.playlist_list
                .item(
                    row
                )
                .data(
                    Qt.UserRole
                )
                or selected
            )

        previous_mode = self.project_sound.state.mode
        self.project_sound.convert_to_single(
            selected
        )
        if self.project_sound.state.mode != previous_mode:
            play_ui_sound(UiSound.BLIP)

    def _clear_project_sound(
        self,
    ) -> None:
        if not self.project_sound.ordered_ids():
            return

        confirmation = LetterSmithConfirmationDialog(
            self,
            title="Clear Music",
            question="Remove all music from this letter?",
            primary_text="Yes",
            secondary_text="No",
            destructive_primary=True,
            click_outside_dismiss=False,
        )

        if confirmation.exec() == QtWidgets.QDialog.Accepted:
            self.player.stop(
                reset_position=True
            )

            self.project_sound.clear()
            play_ui_sound(UiSound.REMOVED)

    def _toggle_play(
        self,
    ) -> None:
        if not self._tab_active:
            return

        if self.project_sound.reconcile_missing_files():
            self._reload_player_queue()
            self._refresh_ui(refresh_playlist=False, refresh_track=False)

        if not self.project_sound.ordered_ids():
            return

        if self.player.is_playing():
            self.player.pause()

        else:
            self.player.play()

    def _on_track_changed(
        self,
        track_id: str,
        *,
        persist_selection: bool = True,
    ) -> None:
        if (
            persist_selection
            and track_id
            and track_id
            in self.project_sound
            .ordered_ids()
            and track_id
            != self.project_sound
            .state
            .selected_track_id
        ):
            previous_track_id = (
                self.project_sound
                .state
                .selected_track_id
            )

            self.project_sound.state.selected_track_id = (
                track_id
            )

            try:
                self._persist_project_sound_state()
            except OSError as error:
                self.project_sound.state.selected_track_id = (
                    previous_track_id
                )

                logging.getLogger(
                    __name__
                ).warning(
                    "Could not save the selected sound track: %s",
                    error,
                )

                self._show_status(
                    "The sound selection could not be saved. "
                    "Please try again.",
                    persistent=True,
                )

        if track_id:
            path = self.library.path_for(
                track_id
            )

        else:
            path = None

        if path is not None:
            record = self.library.get(
                track_id
            )

        else:
            record = None

        if record is not None:
            self.now_playing.setText(
                f"{self.player.transport_state.capitalize()}: "
                f"{record.display_title}"
            )

        else:
            self.now_playing.setText(
                "No music selected"
            )

        if path is not None:
            preview_path = str(
                path
            )

        else:
            preview_path = ""

        next_analysis_key = str(path.resolve()) if path is not None else ""
        if next_analysis_key != self._analysis_key:
            self._analysis_key = ""
        self._preview.set_audio_file(
            preview_path
        )

        if (
            self.project_sound
            .state
            .mode
            == "playlist"
        ):
            self._refresh_playlist()

        if self.player.is_playing():
            self._prime_analysis_for_current()

    def _transport_state_changed(self, state: str) -> None:
        record = self.library.get(self.player.current_track_id)
        title = record.display_title if record is not None else "No music selected"
        self.now_playing.setText(f"{state.capitalize()}: {title}")
        self.now_playing.setToolTip(self.now_playing.text())
        if self.project_sound.state.mode == "playlist":
            self._refresh_playlist()

    def _playback_failed(self, message: str) -> None:
        _LOGGER.warning("Playlist playback failed: %s", message)
        self._show_status(
            "This track could not be played. Check the source or repair Music Archive.",
            persistent=True,
        )

    def _on_position_changed(
        self,
        position: int,
        duration: int,
    ) -> None:
        self.timeline.blockSignals(
            True
        )

        self.timeline.setRange(
            0,
            max(
                0,
                duration,
            ),
        )

        if duration > 0:
            maximum_position = duration

        else:
            maximum_position = position

        self.timeline.setValue(
            max(
                0,
                min(
                    position,
                    maximum_position,
                ),
            )
        )

        self.timeline.blockSignals(
            False
        )

        self.elapsed.setText(
            _format_ms(
                position
            )
        )

        self.total.setText(
            _format_ms(
                duration
            )
        )

    def _on_active_player_changed(
        self,
        player: QMediaPlayer,
    ) -> None:
        self._analysis_key = ""
        self._preview.set_analysis_payload(
            None
        )
        self._preview.set_media_player(
            player
        )

    def _prime_analysis_for_current(
        self,
    ) -> None:
        if (
            self._analysis is None
            or self._analysis_disabled
        ):
            self._analysis_key = ""
            return

        path = self.library.path_for(
            self.player.current_track_id
        )

        if path is None:
            self._analysis_key = ""
            return

        path_key = str(path.resolve())
        if path_key == self._analysis_key:
            return
        self._analysis_key = path_key

        try:
            self._analysis.ensure_analyzed(
                path,
                priority=True,
            )

        except Exception as error:
            self._disable_analysis(
                str(
                    error
                )
            )

    def _on_analysis_ready(
        self,
        path_key: str,
        payload: dict,
    ) -> None:
        if str(
            path_key
        ) == self._analysis_key:
            self._preview.set_analysis_payload(
                payload
            )

    def _on_analysis_failed(
        self,
        path_key: str,
        message: str,
    ) -> None:
        if str(path_key) != self._analysis_key:
            return
        self._disable_analysis(
            message
        )

    def _disable_analysis(
        self,
        message: str,
    ) -> None:
        if self._analysis_disabled:
            return

        self._analysis_disabled = True

        logging.getLogger(
            __name__
        ).warning(
            (
                "Sound analysis disabled "
                "for this session: %s"
            ),
            message,
        )

        self._shutdown_analysis(timeout_ms=2000)

    def _shutdown_analysis(self, timeout_ms: int | None = None) -> bool:
        manager = self._analysis
        if manager is None:
            return True
        try:
            stopped = manager.shutdown(timeout_ms=timeout_ms)
        except Exception:
            _LOGGER.exception("Sound analysis shutdown failed.")
            return False
        if not stopped:
            return False
        self._analysis = None
        self._analysis_key = ""
        manager.deleteLater()
        return True

    def _show_status(
        self,
        message: str,
        persistent: bool = False,
    ) -> None:
        self.status.setText(
            message
        )

        self._status_timer.stop()

        if not persistent:
            self._status_timer.start(
                4200
            )

    def dragEnterEvent(
        self,
        event: QtGui.QDragEnterEvent,
    ) -> None:
        urls = event.mimeData().urls()

        if any(
            Path(
                url.toLocalFile()
            ).suffix.casefold()
            in VALID_AUDIO_EXTS
            for url
            in urls
        ):
            event.acceptProposedAction()

        else:
            event.ignore()

    def dropEvent(
        self,
        event: QtGui.QDropEvent,
    ) -> None:
        paths = [
            url.toLocalFile()
            for url
            in event.mimeData().urls()
            if Path(
                url.toLocalFile()
            ).suffix.casefold()
            in VALID_AUDIO_EXTS
        ]

        if not paths:
            event.ignore()
            return

        if (
            self.project_sound
            .state
            .mode
            == "single"
        ):
            paths = paths[:1]

        self._remember_music_folder(
            paths[0]
        )

        self._start_import(
            paths
        )

        event.acceptProposedAction()

    def shared_preview_widget(
        self,
    ) -> QtWidgets.QWidget:
        return self._preview

    @staticmethod
    def _path_revision(path: Path) -> tuple[int, int, int]:
        try:
            stat_result = path.stat()
        except OSError:
            return (-1, -1, -1)
        return (
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            file_change_token(path, stat_result=stat_result),
        )

    def _current_sound_disk_revision(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            self._path_revision(path)
            for path in (
                library_path(self.project_root),
                project_sound_path(self.project_root),
                processed_dir(self.project_root),
            )
        )

    def _current_active_sound_revision(self) -> tuple[object, ...]:
        track_ids = tuple(self.project_sound.ordered_ids())
        paths = tuple(
            self._path_revision(path)
            for track_id in track_ids
            if (path := self.library.path_for(track_id)) is not None
        )
        return (self.project_sound.state.to_dict(), track_ids, paths)

    def activate_for_tab_change(self) -> None:
        if self._tab_active or self._shutdown:
            return

        self._tab_active = True

        try:
            self._preview.set_tab_active(
                True
            )

            if (
                self._current_sound_disk_revision()
                != self._sound_disk_revision
                or self._current_active_sound_revision()
                != self._active_sound_revision
            ):
                self.reload_project_from_disk()

        except Exception:
            self._tab_active = False
            self._preview.set_tab_active(
                False
            )
            raise

    def deactivate_for_tab_change(self) -> None:
        if not self._tab_active:
            return

        self._preview.set_tab_active(
            False
        )

        self.player.stop(
            reset_position=True
        )

        self.play_btn.setText(
            "▶"
        )

        self._save_volume()

        self._status_timer.stop()

        self._tab_active = False

    def release_current_file_handle(self) -> None:
        self.player.stop(
            reset_position=True
        )

        for media_player in self.player.players:
            media_player.setSource(
                QUrl()
            )

        music_popups = [
            *self.findChildren(ArchiveDialog),
            *self.findChildren(StockMusicDialog),
        ]
        for dialog in music_popups:
            try:
                dialog._stop_preview()
            except Exception:
                logging.getLogger(__name__).debug(
                    "Could not release archive preview player.",
                    exc_info=True,
                )

        preview = getattr(self, "_preview", None)
        if preview is not None:
            try:
                preview.set_audio_file("")
                preview.set_analysis_payload(None)
            except Exception:
                logging.getLogger(__name__).debug(
                    "Could not release sound preview state.",
                    exc_info=True,
                )

    def prepare_for_project_restore(self, timeout_ms: int = 5000) -> None:
        """Detach every Sound-owned reader before restoring project state."""
        self.deactivate_for_tab_change()
        self._background_generation += 1
        deadline = monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        if not self._stop_background_threads(
            max(0, int((deadline - monotonic()) * 1000))
        ):
            raise RuntimeError("Sound background work did not stop in time.")
        if not self._shutdown_analysis(
            max(0, int((deadline - monotonic()) * 1000))
        ):
            raise RuntimeError("Sound analysis did not stop in time.")

        self.release_current_file_handle()

    def reset_project_sound(self) -> None:
        """
        Clear the live Sound-tab assignment after Command erases the project.
        """

        self.player.stop(
            reset_position=True
        )

        self.player.set_muted(
            False
        )

        self.project_sound.clear()

        self._preview.set_audio_file(
            ""
        )

        self.now_playing.setText(
            "No music selected"
        )

        self.timeline.setRange(
            0,
            0,
        )

        self.timeline.setValue(
            0
        )

        self.elapsed.setText(
            "0:00"
        )

        self.total.setText(
            "0:00"
        )

        self.play_btn.setText(
            "▶"
        )

        self.mute_btn.setText(
            "\U0001F50A"
        )

        self.mute_btn.setToolTip(
            "Mute music"
        )

        self._refresh_ui()

        self.sound_state_changed.emit(
            "project-sound-cleared"
        )

    @performance_timed("sound.reload_project_from_disk")
    def reload_project_from_disk(self) -> bool:
        """
        Reload the archive and active project assignment from disk.

        Missing files retain their assignments and remain visible for repair.
        Restore volume without emitting edit callbacks or starting playback.
        """

        self.player.stop(
            reset_position=True
        )
        previous_active_revision = self._active_sound_revision

        with QtCore.QSignalBlocker(self.volume):
            self.volume.setValue(self._load_volume())
        self._last_saved_volume = self.volume.value()
        self.volume_value.setText(str(self.volume.value()))
        self.player.set_muted(False)
        self.mute_btn.setText("\U0001F50A")
        self.mute_btn.setToolTip("Mute preview (temporary; saved soundtrack volume is unchanged)")

        self.library.replace_records(load_library(self.project_root))

        state_changed = self.project_sound.reload_from_disk()

        self._reload_player_queue()
        self._refresh_ui(refresh_playlist=True, refresh_track=True)
        self._sound_disk_revision = self._current_sound_disk_revision()
        self._active_sound_revision = self._current_active_sound_revision()
        active_source_changed = (
            self._active_sound_revision != previous_active_revision
        )

        if state_changed or active_source_changed:
            self.sound_state_changed.emit(
                "sound-state-reloaded"
            )
        return state_changed or active_source_changed

    def _persist_project_sound_state(self) -> None:
        self.project_sound.persist_state()
        self._sync_project_sound_autosave()
        self._sound_disk_revision = self._current_sound_disk_revision()
        self._active_sound_revision = self._current_active_sound_revision()

    def refresh_from_disk(self) -> None:
        """
        Public refresh contract used by Nexus after restoring a project.
        """

        self.reload_project_from_disk()

    def focus_music_editor(self) -> None:
        """
        Focus the relevant Sound control when Forge readiness routes the user
        to the Sound tab.
        """

        if (
            self.project_sound.state.mode
            == "playlist"
        ):
            target = self.add_track_btn

        else:
            target = self.single_action_btn

        target.setFocus(
            Qt.OtherFocusReason
        )

    def showEvent(
        self,
        event: QtGui.QShowEvent,
    ) -> None:
        super().showEvent(
            event
        )

        self.apply_theme_assets()
        self.activate_for_tab_change()

    def hideEvent(
        self,
        event: QtGui.QHideEvent,
    ) -> None:
        self.deactivate_for_tab_change()

        super().hideEvent(
            event
        )

    def _stop_background_threads(
        self,
        timeout_ms: int | None = None,
    ) -> bool:
        self._cancel_background_job()

        stopped = True
        deadline = (
            None
            if timeout_ms is None
            else monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        )
        for operation, thread in (
            ("audio import", self._import_thread),
            ("archive repair", self._repair_thread),
        ):
            if (
                thread is None
                or not thread.isRunning()
            ):
                continue

            thread.requestInterruption()
            thread.quit()
            remaining_ms = (
                None
                if deadline is None
                else max(0, int((deadline - monotonic()) * 1000))
            )
            finished = (
                thread.wait()
                if remaining_ms is None
                else thread.wait(remaining_ms)
            )
            if not finished:
                stopped = False
                _LOGGER.warning(
                    "Sound %s worker did not stop within the shutdown timeout.",
                    operation,
                )
        return stopped

    def closeEvent(
        self,
        event: QtGui.QCloseEvent,
    ) -> None:
        if not self.shutdown(timeout_ms=5000):
            event.ignore()
            return

        super().closeEvent(
            event
        )

    def shutdown(self, timeout_ms: int | None = None) -> bool:
        if self._shutdown:
            return True
        deadline = (
            None
            if timeout_ms is None
            else monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        )
        remaining_ms = (
            None
            if deadline is None
            else max(0, int((deadline - monotonic()) * 1000))
        )
        self._background_generation += 1
        if not self._stop_background_threads(remaining_ms):
            return False
        remaining_ms = (
            None
            if deadline is None
            else max(0, int((deadline - monotonic()) * 1000))
        )
        if not self._shutdown_analysis(remaining_ms):
            _LOGGER.warning(
                "Sound analysis did not stop within the shutdown timeout."
            )
            return False
        self._shutdown = True

        self._tab_active = False
        self._status_timer.stop()
        self._playback_pulse_timer.stop()
        for dialog in (
            *self.findChildren(ArchiveDialog),
            *self.findChildren(StockMusicDialog),
        ):
            filter_timer = getattr(dialog, "_filter_refresh_timer", None)
            if filter_timer is not None:
                filter_timer.stop()
            dialog._stop_preview()
            dialog.close()
            dialog.deleteLater()
        try:
            self._preview.set_tab_active(False)
        except Exception:
            _LOGGER.exception("Sound preview deactivation failed.")
        try:
            self.release_current_file_handle()
        except Exception:
            _LOGGER.exception("Sound file handles could not be released.")

        try:
            self.player.shutdown()
        except Exception:
            _LOGGER.exception("Sound player shutdown failed.")
        try:
            self._preview.shutdown()
        except Exception:
            _LOGGER.exception("Sound preview shutdown failed.")

        return True


__all__ = [
    "SoundTab",
    "SoundLibrary",
    "ProjectSound",
    "PlaylistPlayer",
    "ArchiveDialog",
    "StockMusicDialog",
]
