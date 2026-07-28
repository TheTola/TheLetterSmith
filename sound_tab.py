# ===============================
# File: sound_tab.py
# Purpose: Single-track-first Sound tab with optional playlists and archive
# ===============================

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from audio_tools import AudioToolError, convert_to_mp3, probe_audio, toolchain_available
from config import (
    MAX_AUDIO_MB,
    SETTINGS_FILE,
    STARTING_VOLUME,
    USER_SOUNDS_DIR,
)
from sound_model import (
    ProjectSoundState,
    TrackRecord,
    analysis_dir,
    atomic_write_json,
    current_manifest_path,
    current_music_path,
    display_title_from_name,
    ensure_sound_dirs,
    hash_file,
    load_library,
    load_project_state,
    originals_dir,
    processed_dir,
    resolve_track_path,
    safe_filename,
    save_library,
    save_project_state,
    utc_now_text,
)
from sound_preview import SoundPreviewWidget

try:
    from sound_analyzer import AudioAnalysisManager, analysis_runtime_status
except Exception as exc:
    logging.getLogger(__name__).warning("Sound analysis unavailable: %s", exc)
    AudioAnalysisManager = None  # type: ignore[assignment]

    def analysis_runtime_status() -> tuple[bool, str]:
        return False, "Sound analysis module could not be imported."


VALID_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".m4a", ".flac"}
ANALYSIS_SETTINGS_KEY = "enable_sound_analysis"
LAST_MUSIC_FOLDER_KEY = "last_music_folder"
CROSSFADE_MS = 1000


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _format_ms(milliseconds: int) -> str:
    return _format_duration(max(0, int(milliseconds)) / 1000.0)


def _atomic_copy(source: Path, destination: Path) -> None:
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with src.open("rb") as read_handle, tmp.open("wb") as write_handle:
            shutil.copyfileobj(read_handle, write_handle, 1024 * 1024)
            write_handle.flush()
            os.fsync(write_handle.fileno())
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _read_settings(project_root: Path) -> dict:
    path = project_root / SETTINGS_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write_settings(project_root: Path, settings: dict) -> None:
    atomic_write_json(project_root / SETTINGS_FILE, settings)


def _analysis_requested(project_root: Path) -> bool:
    return _read_settings(project_root).get(ANALYSIS_SETTINGS_KEY) is True


class CleanSlider(QtWidgets.QSlider):
    """Horizontal slider with no inactive/native groove.

    Qt's platform style can redraw a gray rail even when a stylesheet marks the
    add-page transparent. This widget paints the progress line and handle
    directly, so no gray bar can appear.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumHeight(28)
        self.setMouseTracking(True)

    def sizeHint(self) -> QtCore.QSize:  # type: ignore[override]
        hint = super().sizeHint()
        return QtCore.QSize(max(120, hint.width()), 28)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        radius = 8.0
        left = radius + 1.0
        right = max(left, float(self.width()) - radius - 1.0)
        span = max(0.0, right - left)
        minimum = self.minimum()
        maximum = self.maximum()
        ratio = 0.0 if maximum <= minimum else (self.value() - minimum) / float(maximum - minimum)
        ratio = max(0.0, min(1.0, ratio))
        center_x = left + (span * ratio)
        center_y = self.height() / 2.0

        active = QtGui.QColor(0, 200, 255, 210 if self.isEnabled() else 90)
        handle = QtGui.QColor(0, 229, 244, 255 if self.isEnabled() else 120)
        outline = QtGui.QColor(7, 91, 104, 255 if self.isEnabled() else 110)

        if center_x > left:
            pen = QtGui.QPen(active, 3.0, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(left, center_y), QtCore.QPointF(center_x, center_y))

        painter.setPen(QtGui.QPen(outline, 1.0))
        painter.setBrush(handle)
        painter.drawEllipse(QtCore.QPointF(center_x, center_y), radius, radius)


class SoundLibrary(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, project_root: Path, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        ensure_sound_dirs(self.project_root)
        self.records: dict[str, TrackRecord] = load_library(self.project_root)
        self._migrate_legacy_processed_files()

    def _migrate_legacy_processed_files(self) -> None:
        known_files = {Path(record.processed_file).name for record in self.records.values()}
        changed = False
        for path in processed_dir(self.project_root).glob("*.mp3"):
            if path.name in known_files:
                continue
            try:
                content_hash = hash_file(path)
            except OSError:
                continue
            existing = self.find_by_hash(content_hash)
            if existing is not None:
                continue
            track_id = content_hash[:16]
            while track_id in self.records:
                track_id = content_hash[: min(64, len(track_id) + 4)]
            duration = 0.0
            try:
                duration = probe_audio(path).duration_seconds
            except AudioToolError:
                pass
            record = TrackRecord(
                track_id=track_id,
                content_hash=content_hash,
                display_title=display_title_from_name(path.name),
                original_name=path.name,
                original_file="",
                processed_file=path.name,
                duration_seconds=duration,
                added_at=utc_now_text(),
            )
            self.records[record.track_id] = record
            changed = True
        if changed:
            save_library(self.project_root, self.records)

    def all_records(self, sort_mode: str = "recent") -> list[TrackRecord]:
        records = list(self.records.values())
        if sort_mode == "name":
            return sorted(records, key=lambda item: item.display_title.casefold())
        if sort_mode == "duration":
            return sorted(records, key=lambda item: (item.duration_seconds, item.display_title.casefold()))
        return sorted(records, key=lambda item: item.added_at, reverse=True)

    def get(self, track_id: str) -> Optional[TrackRecord]:
        return self.records.get(str(track_id))

    def path_for(self, track_id: str) -> Optional[Path]:
        record = self.get(track_id)
        if record is None:
            return None
        path = resolve_track_path(self.project_root, record)
        return path if path.is_file() else None

    def find_by_hash(self, content_hash: str) -> Optional[TrackRecord]:
        for record in self.records.values():
            if record.content_hash == content_hash:
                return record
        return None

    def register_imports(self, payloads: list[dict]) -> list[str]:
        selected: list[str] = []
        changed = False
        for payload in payloads:
            existing_id = str(payload.get("existing_track_id", ""))
            if existing_id and existing_id in self.records:
                selected.append(existing_id)
                continue
            record_payload = payload.get("record")
            if not isinstance(record_payload, dict):
                continue
            record = TrackRecord.from_dict(record_payload)
            self.records[record.track_id] = record
            selected.append(record.track_id)
            changed = True
        if changed:
            save_library(self.project_root, self.records)
            self.changed.emit()
        return selected

    def rename_display_title(self, track_id: str, title: str) -> None:
        record = self.get(track_id)
        clean = " ".join(str(title).split()).strip()
        if record is None or not clean:
            return
        record.display_title = clean
        save_library(self.project_root, self.records)
        self.changed.emit()

    def delete_track(self, track_id: str) -> bool:
        record = self.records.get(track_id)
        if record is None:
            return False
        processed = resolve_track_path(self.project_root, record)
        try:
            processed.unlink(missing_ok=True)
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not delete processed track %s: %s", processed, exc)
            return False
        self.records.pop(track_id, None)
        for path in (
            originals_dir(self.project_root) / Path(record.original_file).name if record.original_file else None,
            analysis_dir(self.project_root) / f"{Path(record.processed_file).stem}.json",
        ):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        save_library(self.project_root, self.records)
        self.changed.emit()
        return True



class ProjectSound(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, project_root: Path, library: SoundLibrary, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.library = library
        self.state = load_project_state(self.project_root, valid_ids=set(self.library.records))
        self._migrate_legacy_current_track()
        self.save()

    def _migrate_legacy_current_track(self) -> None:
        if self.state.ordered_track_ids():
            return
        music = current_music_path(self.project_root)
        if not music.is_file():
            return
        try:
            digest = hash_file(music)
        except OSError:
            return
        record = self.library.find_by_hash(digest)
        if record is None:
            return
        self.state.single_track_id = record.track_id
        self.state.selected_track_id = record.track_id

    def save(self) -> None:
        self.state.normalize(set(self.library.records))
        save_project_state(self.project_root, self.state)
        self._sync_compatibility_files()
        self.changed.emit()

    def _sync_compatibility_files(self) -> None:
        track_id = self.state.selected_track_id
        if track_id not in self.state.ordered_track_ids():
            ordered = self.state.ordered_track_ids()
            track_id = ordered[0] if ordered else ""
            self.state.selected_track_id = track_id
        music_path = current_music_path(self.project_root)
        manifest = current_manifest_path(self.project_root)
        source = self.library.path_for(track_id) if track_id else None
        if source is None:
            music_path.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            return
        record = self.library.get(track_id)
        if record is None:
            return
        try:
            current = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
        except (OSError, json.JSONDecodeError):
            current = {}
        try:
            music_matches = (
                music_path.is_file()
                and current.get("track_id") == track_id
                and music_path.stat().st_size == source.stat().st_size
            )
            if not music_matches:
                _atomic_copy(source, music_path)
            atomic_write_json(
                manifest,
                {
                    "current_rel": f"{USER_SOUNDS_DIR}/appssong/processed/{record.processed_file}",
                    "track_id": track_id,
                    "link_mode": "copy",
                },
            )
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not update compatibility music.mp3: %s", exc)

    def set_single(self, track_id: str) -> None:
        self.state.mode = "single"
        self.state.single_track_id = track_id if track_id in self.library.records else ""
        self.state.playlist = []
        self.state.selected_track_id = self.state.single_track_id
        self.save()

    def create_playlist(self) -> None:
        first = self.state.single_track_id if self.state.single_track_id in self.library.records else ""
        self.state.mode = "playlist"
        self.state.playlist = [first] if first else []
        self.state.single_track_id = ""
        self.state.selected_track_id = first
        self.state.playlist_expanded = True
        self.save()

    def add_to_playlist(self, track_ids: list[str]) -> None:
        if self.state.mode != "playlist":
            self.create_playlist()
        for track_id in track_ids:
            if track_id in self.library.records and track_id not in self.state.playlist:
                self.state.playlist.append(track_id)
        if not self.state.selected_track_id and self.state.playlist:
            self.state.selected_track_id = self.state.playlist[0]
        self.save()

    def reorder_playlist(self, track_ids: list[str]) -> None:
        self.state.playlist = [track_id for track_id in track_ids if track_id in self.library.records]
        self.save()

    def remove_from_playlist(self, track_id: str) -> None:
        self.state.playlist = [item for item in self.state.playlist if item != track_id]
        if self.state.selected_track_id == track_id:
            self.state.selected_track_id = self.state.playlist[0] if self.state.playlist else ""
        self.save()

    def select_track(self, track_id: str) -> None:
        if track_id in self.state.ordered_track_ids():
            self.state.selected_track_id = track_id
            self.save()

    def convert_to_single(self, track_id: str = "") -> None:
        chosen = track_id if track_id in self.state.playlist else self.state.selected_track_id
        if chosen not in self.library.records:
            chosen = self.state.playlist[0] if self.state.playlist else ""
        self.set_single(chosen)

    def clear(self) -> None:
        self.state = ProjectSoundState()
        self.save()

    def remove_usage(self, track_id: str) -> None:
        self.state.remove_usage(track_id)
        self.save()

    def ordered_ids(self) -> list[str]:
        return self.state.ordered_track_ids()


class _ImportWorker(QtCore.QObject):
    finished = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, project_root: Path, paths: list[str], known_hashes: dict[str, str]) -> None:
        super().__init__()
        self.project_root = project_root
        self.paths = [Path(path).resolve() for path in paths]
        self.known_hashes = dict(known_hashes)
        self._cancelled = False

    @QtCore.Slot()
    def run(self) -> None:
        results: list[dict] = []
        created_artifacts: list[Path] = []
        try:
            for source in self.paths:
                if self._cancelled:
                    raise RuntimeError("Audio import canceled.")
                if not source.is_file():
                    raise FileNotFoundError(f"Audio file does not exist: {source}")
                if source.suffix.casefold() not in VALID_AUDIO_EXTS:
                    raise ValueError(f"Unsupported audio format: {source.suffix}")
                if source.stat().st_size > MAX_AUDIO_MB * 1024 * 1024:
                    raise ValueError(f"{source.name} exceeds the {MAX_AUDIO_MB} MB limit.")
                content_hash = hash_file(source)
                existing_id = self.known_hashes.get(content_hash)
                if existing_id:
                    results.append({"existing_track_id": existing_id})
                    continue
                track_id = content_hash[:24]
                safe_name = safe_filename(source.name)
                original_name = f"{track_id}__{safe_name}"
                processed_name = f"{track_id}.mp3"
                original_destination = originals_dir(self.project_root) / original_name
                processed_destination = processed_dir(self.project_root) / processed_name
                created_original = not original_destination.is_file()
                created_processed = not processed_destination.is_file()
                if created_original:
                    created_artifacts.append(original_destination)
                if created_processed:
                    created_artifacts.append(processed_destination)
                try:
                    if created_original:
                        _atomic_copy(source, original_destination)
                    if source.suffix.casefold() == ".mp3":
                        _atomic_copy(source, processed_destination)
                    else:
                        if not toolchain_available():
                            raise AudioToolError(
                                "This file needs conversion, but tools/ffmpeg.exe and tools/ffprobe.exe are missing."
                            )
                        convert_to_mp3(
                            source,
                            processed_destination,
                            cancel_check=lambda: self._cancelled,
                        )
                    duration = 0.0
                    try:
                        duration = probe_audio(processed_destination).duration_seconds
                    except AudioToolError:
                        pass
                    record = TrackRecord(
                        track_id=track_id,
                        content_hash=content_hash,
                        display_title=display_title_from_name(source.name),
                        original_name=source.name,
                        original_file=original_name,
                        processed_file=processed_name,
                        duration_seconds=duration,
                        added_at=utc_now_text(),
                    )
                    results.append({"record": record.to_dict()})
                    self.known_hashes[content_hash] = track_id
                except Exception:
                    if created_original:
                        original_destination.unlink(missing_ok=True)
                    if created_processed:
                        processed_destination.unlink(missing_ok=True)
                    raise
            self.finished.emit(results)
        except Exception as exc:
            for artifact in created_artifacts:
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass
            self.failed.emit(str(exc))

    @QtCore.Slot()
    def cancel(self) -> None:
        self._cancelled = True


class _RepairWorker(QtCore.QObject):
    finished = QtCore.Signal(int, list)
    failed = QtCore.Signal(str)

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self._cancelled = False

    @QtCore.Slot()
    def run(self) -> None:
        repaired = 0
        issues: list[str] = []
        records = load_library(self.project_root)
        try:
            for folder in (originals_dir(self.project_root), processed_dir(self.project_root), analysis_dir(self.project_root)):
                for tmp in folder.glob(".*.tmp*"):
                    if self._cancelled:
                        raise RuntimeError("Archive repair canceled.")
                    try:
                        tmp.unlink()
                        repaired += 1
                    except OSError:
                        issues.append(f"Could not remove temporary file: {tmp.name}")
            for record in records.values():
                if self._cancelled:
                    raise RuntimeError("Archive repair canceled.")
                processed = resolve_track_path(self.project_root, record)
                if processed.is_file():
                    continue
                original = originals_dir(self.project_root) / Path(record.original_file).name if record.original_file else None
                if original is None or not original.is_file():
                    issues.append(f"Missing audio files for {record.display_title}")
                    continue
                try:
                    if original.suffix.casefold() == ".mp3":
                        _atomic_copy(original, processed)
                    else:
                        convert_to_mp3(original, processed, cancel_check=lambda: self._cancelled)
                    record.duration_seconds = probe_audio(processed).duration_seconds
                    repaired += 1
                except (AudioToolError, OSError) as exc:
                    issues.append(f"{record.display_title}: {exc}")
            save_library(self.project_root, records)
            self.finished.emit(repaired, issues)
        except Exception as exc:
            self.failed.emit(str(exc))

    @QtCore.Slot()
    def cancel(self) -> None:
        self._cancelled = True


class PlaylistPlayer(QtCore.QObject):
    trackChanged = QtCore.Signal(str)
    playbackChanged = QtCore.Signal(bool)
    positionChanged = QtCore.Signal(int, int)
    activePlayerChanged = QtCore.Signal(object)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, path_resolver: Callable[[str], Optional[Path]], parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.path_resolver = path_resolver
        self.players = [QMediaPlayer(self), QMediaPlayer(self)]
        self.outputs = [QAudioOutput(self), QAudioOutput(self)]
        for index, player in enumerate(self.players):
            player.setAudioOutput(self.outputs[index])
            player.positionChanged.connect(lambda position, i=index: self._on_position(i, position))
            player.durationChanged.connect(lambda duration, i=index: self._on_duration(i, duration))
            player.mediaStatusChanged.connect(lambda status, i=index: self._on_status(i, status))
            player.errorOccurred.connect(lambda _error, text, i=index: self._on_error(i, text))
        self.queue: list[str] = []
        self.current_index = -1
        self.active_slot = 0
        self._durations = [0, 0]
        self._volume = max(0.0, min(1.0, float(STARTING_VOLUME) / 100.0))
        self._muted = False
        self._crossfade_timer = QtCore.QTimer(self)
        self._crossfade_timer.setInterval(40)
        self._crossfade_timer.timeout.connect(self._crossfade_step)
        self._crossfade_elapsed = 0
        self._crossfade_from = -1
        self._crossfade_to = -1
        self._transitioning = False
        self._apply_output_state()

    @property
    def player(self) -> QMediaPlayer:
        return self.players[self.active_slot]

    @property
    def current_track_id(self) -> str:
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return ""

    def set_queue(self, track_ids: list[str], selected_track_id: str = "") -> None:
        was_playing = self.is_playing()
        self.stop(reset_position=True)
        self.queue = [track_id for track_id in track_ids if self.path_resolver(track_id) is not None]
        if selected_track_id in self.queue:
            self.current_index = self.queue.index(selected_track_id)
        else:
            self.current_index = 0 if self.queue else -1
        self._load_active_source()
        if was_playing and self.queue:
            self.play()

    def _load_active_source(self) -> None:
        player = self.players[self.active_slot]
        path = self.path_resolver(self.current_track_id) if self.current_track_id else None
        player.stop()
        player.setPosition(0)
        player.setSource(QUrl.fromLocalFile(str(path))) if path else player.setSource(QUrl())
        self.activePlayerChanged.emit(player)
        self.trackChanged.emit(self.current_track_id)
        self.positionChanged.emit(0, 0)

    def set_volume(self, percent: int) -> None:
        self._volume = max(0.0, min(1.0, int(percent) / 100.0))
        self._apply_output_state()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._apply_output_state()

    def toggle_mute(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted

    def _apply_output_state(self) -> None:
        for output in self.outputs:
            output.setMuted(self._muted)
            if not self._transitioning:
                output.setVolume(self._volume)

    def is_playing(self) -> bool:
        return any(player.playbackState() == QMediaPlayer.PlayingState for player in self.players)

    def play(self) -> None:
        if not self.queue or self.current_index < 0:
            return
        if self.player.source().isEmpty():
            self._load_active_source()
        self.outputs[self.active_slot].setVolume(self._volume)
        self.player.play()
        self.playbackChanged.emit(True)

    def pause(self) -> None:
        self._cancel_crossfade(keep_active=True)
        self.player.pause()
        self.playbackChanged.emit(False)

    def stop(self, reset_position: bool = True) -> None:
        self._cancel_crossfade(keep_active=True)
        for player in self.players:
            player.stop()
            if reset_position:
                player.setPosition(0)
        self.playbackChanged.emit(False)
        self.positionChanged.emit(0, self._durations[self.active_slot])

    def seek(self, position: int) -> None:
        self.player.setPosition(max(0, int(position)))

    def select_track(self, track_id: str, autoplay: bool = False) -> None:
        if track_id not in self.queue:
            return
        self.stop(reset_position=True)
        self.current_index = self.queue.index(track_id)
        self._load_active_source()
        if autoplay:
            self.play()

    def next(self, autoplay: bool = True) -> None:
        if self.current_index + 1 >= len(self.queue):
            self.stop(reset_position=True)
            return
        self.stop(reset_position=True)
        self.current_index += 1
        self._load_active_source()
        if autoplay:
            self.play()

    def previous(self) -> None:
        if self.player.position() > 3000 or self.current_index <= 0:
            self.seek(0)
            return
        self.stop(reset_position=True)
        self.current_index -= 1
        self._load_active_source()
        self.play()

    def _on_position(self, slot: int, position: int) -> None:
        if slot != self.active_slot:
            return
        duration = self._durations[slot]
        self.positionChanged.emit(position, duration)
        if (
            not self._transitioning
            and len(self.queue) > 1
            and self.current_index + 1 < len(self.queue)
            and duration > CROSSFADE_MS + 500
            and 0 < duration - position <= CROSSFADE_MS
        ):
            self._begin_crossfade()

    def _on_duration(self, slot: int, duration: int) -> None:
        self._durations[slot] = max(0, int(duration))
        if slot == self.active_slot:
            self.positionChanged.emit(self.players[slot].position(), self._durations[slot])

    def _on_status(self, slot: int, status: QMediaPlayer.MediaStatus) -> None:
        if status != QMediaPlayer.EndOfMedia or slot != self.active_slot or self._transitioning:
            return
        if self.current_index + 1 < len(self.queue):
            self.next(autoplay=True)
            return
        self.stop(reset_position=True)
        self.finished.emit()

    def _on_error(self, slot: int, text: str) -> None:
        if slot == self.active_slot and text:
            self.error.emit(text)

    def _begin_crossfade(self) -> None:
        next_index = self.current_index + 1
        if next_index >= len(self.queue):
            return
        next_slot = 1 - self.active_slot
        path = self.path_resolver(self.queue[next_index])
        if path is None:
            return
        standby = self.players[next_slot]
        standby.stop()
        standby.setSource(QUrl.fromLocalFile(str(path)))
        standby.setPosition(0)
        self.outputs[next_slot].setMuted(self._muted)
        self.outputs[next_slot].setVolume(0.0)
        standby.play()
        self._transitioning = True
        self._crossfade_elapsed = 0
        self._crossfade_from = self.active_slot
        self._crossfade_to = next_slot
        self._crossfade_timer.start()

    def _crossfade_step(self) -> None:
        self._crossfade_elapsed += self._crossfade_timer.interval()
        progress = min(1.0, self._crossfade_elapsed / float(CROSSFADE_MS))
        self.outputs[self._crossfade_from].setVolume(self._volume * (1.0 - progress))
        self.outputs[self._crossfade_to].setVolume(self._volume * progress)
        if progress < 1.0:
            return
        old_slot = self._crossfade_from
        new_slot = self._crossfade_to
        self._crossfade_timer.stop()
        self.players[old_slot].stop()
        self.players[old_slot].setPosition(0)
        self.outputs[old_slot].setVolume(self._volume)
        self.active_slot = new_slot
        self.current_index += 1
        self._transitioning = False
        self._crossfade_from = -1
        self._crossfade_to = -1
        self.activePlayerChanged.emit(self.player)
        self.trackChanged.emit(self.current_track_id)
        self.playbackChanged.emit(True)

    def _cancel_crossfade(self, keep_active: bool) -> None:
        if not self._transitioning:
            return
        self._crossfade_timer.stop()
        standby = self._crossfade_to
        if standby >= 0:
            self.players[standby].stop()
            self.players[standby].setPosition(0)
            self.outputs[standby].setVolume(self._volume)
        if keep_active and self._crossfade_from >= 0:
            self.outputs[self._crossfade_from].setVolume(self._volume)
        self._transitioning = False
        self._crossfade_from = -1
        self._crossfade_to = -1

    def release_current_file_handle(self) -> None:
        self.stop(reset_position=True)
        for player in self.players:
            player.setSource(QUrl())

    def shutdown(self) -> None:
        self.release_current_file_handle()


class ArchiveDialog(QtWidgets.QDialog):
    tracksChosen = QtCore.Signal(list)

    def __init__(
        self,
        library: SoundLibrary,
        used_ids: Callable[[], set[str]],
        delete_callback: Callable[[str], bool],
        repair_callback: Callable[[], None],
        *,
        multi_select: bool,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.used_ids = used_ids
        self.delete_callback = delete_callback
        self.repair_callback = repair_callback
        self.setWindowTitle("Music Archive")
        self.resize(760, 500)
        self.setModal(True)

        root = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search archive…")
        self.sort = QtWidgets.QComboBox()
        self.sort.addItem("Recently added", "recent")
        self.sort.addItem("Name", "name")
        self.sort.addItem("Duration", "duration")
        top.addWidget(self.search, 1)
        top.addWidget(self.sort)
        root.addLayout(top)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Title", "Duration", "Added", "Used"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection if multi_select else QtWidgets.QAbstractItemView.SingleSelection
        )
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _index: self._choose())
        root.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.preview_btn = QtWidgets.QPushButton("Preview")
        self.rename_btn = QtWidgets.QPushButton("Rename Title")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.original_btn = QtWidgets.QPushButton("Show Original")
        self.repair_btn = QtWidgets.QPushButton("Repair Archive")
        self.choose_btn = QtWidgets.QPushButton("Add Selected" if multi_select else "Use Selected")
        close_btn = QtWidgets.QPushButton("Close")
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.rename_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.original_btn)
        buttons.addWidget(self.repair_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.choose_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.preview_output = QAudioOutput(self)
        self.preview_output.setVolume(0.35)
        self.preview_player = QMediaPlayer(self)
        self.preview_player.setAudioOutput(self.preview_output)
        self._previewing_id = ""

        self.search.textChanged.connect(self.refresh)
        self.sort.currentIndexChanged.connect(self.refresh)
        self.preview_btn.clicked.connect(self._preview)
        self.rename_btn.clicked.connect(self._rename)
        self.delete_btn.clicked.connect(self._delete)
        self.original_btn.clicked.connect(self._show_original)
        self.repair_btn.clicked.connect(self._repair)
        self.choose_btn.clicked.connect(self._choose)
        close_btn.clicked.connect(self.reject)
        self.library.changed.connect(self.refresh)
        self.refresh()
        self.setStyleSheet(
            "QDialog{background:#11151c;color:#e7eef8;}"
            "QLineEdit,QComboBox,QTableWidget{background:#0b0f15;color:#e7eef8;border:1px solid #293445;}"
            "QPushButton{background:#1c2430;color:#eef6ff;border:1px solid #38506b;border-radius:7px;padding:9px 13px;min-height:22px;}"
            "QPushButton:hover{border-color:#00c8ff;background:#233447;}"
        )

    def refresh(self) -> None:
        query = self.search.text().strip().casefold()
        records = self.library.all_records(str(self.sort.currentData() or "recent"))
        if query:
            records = [record for record in records if query in record.display_title.casefold() or query in record.original_name.casefold()]
        self.table.setRowCount(len(records))
        used = self.used_ids()
        for row, record in enumerate(records):
            title = QtWidgets.QTableWidgetItem(record.display_title)
            title.setData(Qt.UserRole, record.track_id)
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(_format_duration(record.duration_seconds)))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(record.added_at[:10]))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("Current project" if record.track_id in used else ""))
        self.table.resizeRowsToContents()

    def selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        result: list[str] = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None:
                result.append(str(item.data(Qt.UserRole) or ""))
        return [track_id for track_id in result if track_id]

    def _choose(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.tracksChosen.emit(ids)
            self.accept()

    def _preview(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        track_id = ids[0]
        if self._previewing_id == track_id and self.preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.preview_player.stop()
            self.preview_btn.setText("Preview")
            return
        path = self.library.path_for(track_id)
        if path is None:
            return
        self.preview_player.stop()
        self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.preview_player.play()
        self._previewing_id = track_id
        self.preview_btn.setText("Stop Preview")

    def _rename(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        record = self.library.get(ids[0])
        if record is None:
            return
        title, ok = QtWidgets.QInputDialog.getText(self, "Rename Display Title", "Display title:", text=record.display_title)
        if ok and title.strip():
            self.library.rename_display_title(record.track_id, title)

    def _show_original(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        record = self.library.get(ids[0])
        if record is None or not record.original_file:
            return
        original = originals_dir(self.library.project_root) / Path(record.original_file).name
        if original.is_file():
            QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(original.parent)))

    def _repair(self) -> None:
        self.accept()
        self.repair_callback()

    def _delete(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        self._previewing_id = ""
        self.preview_btn.setText("Preview")
        for track_id in ids:
            self.delete_callback(track_id)
        self.refresh()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        super().closeEvent(event)


class PlaylistItemWidget(QtWidgets.QFrame):
    removeRequested = QtCore.Signal(str)

    def __init__(self, record: TrackRecord, active: bool, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.track_id = record.track_id
        self.setObjectName("playlistRow")
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(9, 6, 7, 6)
        row.setSpacing(8)
        drag = QtWidgets.QLabel("≡")
        title = QtWidgets.QLabel(record.display_title)
        title.setToolTip(record.original_name)
        duration = QtWidgets.QLabel(_format_duration(record.duration_seconds))
        remove = QtWidgets.QToolButton()
        remove.setText("×")
        remove.setToolTip("Remove from playlist")
        remove.setFixedSize(30, 30)
        remove.clicked.connect(lambda: self.removeRequested.emit(self.track_id))
        row.addWidget(drag)
        row.addWidget(title, 1)
        row.addWidget(duration)
        row.addWidget(remove)
        border = "#00c8ff" if active else "#2b3344"
        background = "rgba(0,200,255,28)" if active else "#151a22"
        self.setStyleSheet(
            f"QFrame#playlistRow{{background:{background};border:1px solid {border};border-radius:8px;}}"
            "QLabel{color:#e8eff8;}QToolButton{color:#cfd8e5;border:none;font-size:18px;}"
            "QToolButton:hover{color:#ff7777;}"
        )


class SoundTab(QtWidgets.QWidget):
    preview_widget = QtCore.Signal(QtWidgets.QWidget)

    def __init__(self, project_root: str | Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.library = SoundLibrary(self.project_root, self)
        self.project_sound = ProjectSound(self.project_root, self.library, self)
        self.player = PlaylistPlayer(self.library.path_for, self)
        self.wave = self.player  # compatibility for command.py and existing Nexus hooks
        self._tab_active = False
        self._import_thread: Optional[QtCore.QThread] = None
        self._import_worker: Optional[_ImportWorker] = None
        self._repair_thread: Optional[QtCore.QThread] = None
        self._repair_worker: Optional[_RepairWorker] = None
        self._pending_import_mode = "single"
        self._analysis = None
        self._analysis_key = ""
        self._analysis_disabled = False
        self._preview = SoundPreviewWidget(self.player.player, parent=self)
        self._preview.set_tab_active(False)
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status.setText(""))

        self._init_analysis()
        self._init_ui()
        self._connect_signals()
        self._reload_player_queue()
        self._refresh_ui()
        self.preview_widget.emit(self._preview)

    def _init_analysis(self) -> None:
        if not _analysis_requested(self.project_root) or AudioAnalysisManager is None:
            return
        ready, reason = analysis_runtime_status()
        if not ready:
            logging.getLogger(__name__).warning("Sound analysis disabled: %s", reason)
            return
        try:
            manager = AudioAnalysisManager(self.project_root, parent=self)
            manager.processed_dir = processed_dir(self.project_root)
            manager.analysis_dir = analysis_dir(self.project_root)
            manager.analysisReady.connect(self._on_analysis_ready)
            manager.analysisFailed.connect(self._on_analysis_failed)
            self._analysis = manager
        except Exception as exc:
            logging.getLogger(__name__).warning("Sound analysis disabled: %s", exc)

    def _init_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        # The Sound tab receives a little more vertical breathing room when the
        # Nexus preview is reduced in normal-window mode.
        root.setSpacing(16)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Sound")
        title.setStyleSheet("color:#dff8ff;font-size:18px;font-weight:700;")
        self.archive_btn = QtWidgets.QPushButton("Archive")
        self.archive_btn.setToolTip("Choose music from the archive")
        self.archive_btn.setMinimumHeight(40)
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.setToolTip("Clear music assigned to this letter")
        self.clear_btn.setMinimumHeight(40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.archive_btn)
        header.addWidget(self.clear_btn)
        root.addLayout(header)

        self.mode_stack = QtWidgets.QStackedWidget()
        self.single_panel = self._build_single_panel()
        self.playlist_panel = self._build_playlist_panel()
        self.mode_stack.addWidget(self.single_panel)
        self.mode_stack.addWidget(self.playlist_panel)
        root.addWidget(self.mode_stack, 1)

        self.now_playing = QtWidgets.QLabel("No music selected")
        self.now_playing.setStyleSheet("color:#b7c9dc;padding:6px 4px;")
        root.addWidget(self.now_playing)

        transport = QtWidgets.QHBoxLayout()
        transport.setContentsMargins(0, 4, 0, 4)
        transport.setSpacing(10)
        self.prev_btn = QtWidgets.QToolButton()
        self.prev_btn.setText("⏮")
        self.prev_btn.setToolTip("Previous track or restart current track")
        self.play_btn = QtWidgets.QToolButton()
        self.play_btn.setText("▶")
        self.play_btn.setToolTip("Play or pause")
        self.next_btn = QtWidgets.QToolButton()
        self.next_btn.setText("⏭")
        self.next_btn.setToolTip("Next track")
        self.elapsed = QtWidgets.QLabel("0:00")
        self.timeline = CleanSlider()
        self.timeline.setRange(0, 0)
        self.total = QtWidgets.QLabel("0:00")
        # QPushButton is used here instead of QToolButton because QToolButton
        # elides the Windows emoji glyph to "..." when its global padding is
        # applied. This button has its own zero-padding style, so the Unicode
        # speaker symbols remain centered and fully visible.
        self.mute_btn = QtWidgets.QPushButton("\U0001F50A")
        self.mute_btn.setObjectName("soundVolumeButton")
        self.mute_btn.setToolTip("Mute music")
        self.mute_btn.setAccessibleName("Mute or restore music")
        self.mute_btn.setFont(QtGui.QFont("Segoe UI Emoji", 18))
        self.mute_btn.setStyleSheet(
            "QPushButton#soundVolumeButton{padding:0;margin:0;min-height:0;"
            "background:#1b2430;border:1px solid #33475f;border-radius:8px;"
            "font-family:'Segoe UI Emoji';font-size:20px;}"
            "QPushButton#soundVolumeButton:hover{border-color:#00c8ff;background:#233447;}"
        )
        self.volume = CleanSlider()
        self.volume.setRange(0, 100)
        self.volume.setValue(self._load_volume())
        self.volume.setFixedWidth(156)
        for button in (self.prev_btn, self.play_btn, self.next_btn):
            button.setFixedSize(48, 42)
            button.setFont(QtGui.QFont("Segoe UI Symbol", 18))
        self.mute_btn.setFixedSize(52, 42)
        transport.addWidget(self.prev_btn)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.next_btn)
        transport.addWidget(self.elapsed)
        transport.addWidget(self.timeline, 1)
        transport.addWidget(self.total)
        transport.addWidget(self.mute_btn)
        transport.addWidget(self.volume)
        root.addLayout(transport)

        status_row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#91a8bd;min-height:24px;padding-top:2px;")
        self.cancel_job_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_job_btn.hide()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.cancel_job_btn)
        root.addLayout(status_row)

        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QWidget{color:#e7eef8;}"
            "QPushButton,QToolButton{background:#1b2430;border:1px solid #33475f;border-radius:8px;padding:8px 12px;min-height:22px;}"
            "QPushButton:hover,QToolButton:hover{border-color:#00c8ff;background:#233447;}"
            "QStackedWidget,QListWidget{background:transparent;border:none;}"
        )

    def _build_single_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("singleCard")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(12)
        self.single_title = QtWidgets.QLabel("No music selected")
        self.single_title.setAlignment(Qt.AlignCenter)
        self.single_title.setStyleSheet("font-size:17px;font-weight:650;color:#eaf7ff;")
        self.single_detail = QtWidgets.QLabel("Add one song for this letter.")
        self.single_detail.setAlignment(Qt.AlignCenter)
        self.single_detail.setWordWrap(True)
        self.single_detail.setStyleSheet("color:#93a8bd;")
        button_row = QtWidgets.QHBoxLayout()
        self.single_action_btn = QtWidgets.QPushButton("Add Music")
        self.single_action_btn.setMinimumSize(132, 40)
        self.create_playlist_btn = QtWidgets.QPushButton("Create Playlist")
        self.create_playlist_btn.setMinimumSize(156, 40)
        self.create_playlist_btn.hide()
        button_row.addStretch(1)
        button_row.addWidget(self.single_action_btn)
        button_row.addWidget(self.create_playlist_btn)
        button_row.addStretch(1)
        layout.addStretch(1)
        layout.addWidget(self.single_title)
        layout.addWidget(self.single_detail)
        layout.addLayout(button_row)
        layout.addStretch(1)
        panel.setStyleSheet(
            "QFrame#singleCard{background:#121820;border:1px solid #2b3a4d;border-radius:12px;}"
        )
        return panel

    def _build_playlist_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("playlistPanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(12)
        summary = QtWidgets.QHBoxLayout()
        self.playlist_summary = QtWidgets.QLabel("Playlist")
        self.expand_btn = QtWidgets.QPushButton("Collapse")
        self.add_track_btn = QtWidgets.QPushButton("Add Track")
        self.convert_single_btn = QtWidgets.QPushButton("Convert to Single Track")
        for button in (self.expand_btn, self.add_track_btn, self.convert_single_btn):
            button.setMinimumHeight(40)
        summary.addWidget(self.playlist_summary, 1)
        summary.addWidget(self.expand_btn)
        summary.addWidget(self.add_track_btn)
        summary.addWidget(self.convert_single_btn)
        layout.addLayout(summary)
        self.playlist_list = QtWidgets.QListWidget()
        self.playlist_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.playlist_list.setDefaultDropAction(Qt.MoveAction)
        self.playlist_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.playlist_list.setSpacing(6)
        self.playlist_list.model().rowsMoved.connect(self._playlist_rows_moved)
        layout.addWidget(self.playlist_list, 1)
        panel.setStyleSheet("QFrame#playlistPanel{background:#111820;border:1px solid #2b3a4d;border-radius:12px;}")
        return panel

    def _connect_signals(self) -> None:
        self.single_action_btn.clicked.connect(self._choose_new_files)
        self.create_playlist_btn.clicked.connect(self._create_playlist)
        self.add_track_btn.clicked.connect(self._choose_new_files)
        self.archive_btn.clicked.connect(self._open_archive_for_current_mode)
        self.expand_btn.clicked.connect(self._toggle_playlist_expanded)
        self.convert_single_btn.clicked.connect(self._convert_to_single)
        self.clear_btn.clicked.connect(self._clear_project_sound)
        self.prev_btn.clicked.connect(self.player.previous)
        self.play_btn.clicked.connect(self._toggle_play)
        self.next_btn.clicked.connect(lambda: self.player.next(autoplay=True))
        self.mute_btn.clicked.connect(self._toggle_mute)
        self.volume.valueChanged.connect(self._volume_changed)
        self.volume.sliderReleased.connect(self._save_volume)
        self.timeline.sliderMoved.connect(self.player.seek)
        self.playlist_list.itemClicked.connect(self._playlist_item_selected)
        self.playlist_list.itemDoubleClicked.connect(self._playlist_item_activated)
        self.cancel_job_btn.clicked.connect(self._cancel_background_job)
        self.player.trackChanged.connect(self._on_track_changed)
        self.player.playbackChanged.connect(lambda playing: self.play_btn.setText("⏸" if playing else "▶"))
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.activePlayerChanged.connect(self._on_active_player_changed)
        self.player.error.connect(lambda message: self._show_status(f"Playback error: {message}", persistent=True))
        self.player.finished.connect(lambda: self._show_status("Playback finished."))
        self.library.changed.connect(self._refresh_ui)
        self.project_sound.changed.connect(self._project_state_changed)

    def _load_volume(self) -> int:
        settings = _read_settings(self.project_root)
        try:
            return max(0, min(100, int(settings.get("music_volume", STARTING_VOLUME))))
        except (TypeError, ValueError):
            return int(STARTING_VOLUME)

    def _save_volume(self) -> None:
        settings = _read_settings(self.project_root)
        settings["music_volume"] = self.volume.value()
        settings["starting_volume"] = self.volume.value()
        _write_settings(self.project_root, settings)

    def _volume_changed(self, value: int) -> None:
        self.player.set_volume(value)

    def _toggle_mute(self) -> None:
        muted = self.player.toggle_mute()
        self.mute_btn.setText("\U0001F507" if muted else "\U0001F50A")
        self.mute_btn.setToolTip("Restore music" if muted else "Mute music")

    def _project_state_changed(self) -> None:
        self._reload_player_queue()
        self._refresh_ui()

    def _reload_player_queue(self) -> None:
        self.player.set_queue(self.project_sound.ordered_ids(), self.project_sound.state.selected_track_id)
        self.player.set_volume(self.volume.value() if hasattr(self, "volume") else self._load_volume())

    def _refresh_ui(self) -> None:
        state = self.project_sound.state
        if state.mode == "playlist":
            self.create_playlist_btn.hide()
            self.mode_stack.setCurrentWidget(self.playlist_panel)
            self._refresh_playlist()
        else:
            self.mode_stack.setCurrentWidget(self.single_panel)
            record = self.library.get(state.single_track_id)
            has_single_track = record is not None
            self.create_playlist_btn.setVisible(has_single_track)
            if record is None:
                self.single_title.setText("No music selected")
                self.single_detail.setText("Add one song for this letter.")
                self.single_action_btn.setText("Add Music")
            else:
                self.single_title.setText(record.display_title)
                self.single_detail.setText(f"{_format_duration(record.duration_seconds)}  •  {record.original_name}")
                self.single_action_btn.setText("Replace Music")
        self._sync_transport_enabled()
        self._on_track_changed(self.player.current_track_id)

    def _refresh_playlist(self) -> None:
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        active = self.project_sound.state.selected_track_id
        total_duration = 0.0
        for track_id in self.project_sound.state.playlist:
            record = self.library.get(track_id)
            if record is None:
                continue
            total_duration += record.duration_seconds
            item = QtWidgets.QListWidgetItem()
            item.setData(Qt.UserRole, track_id)
            widget = PlaylistItemWidget(record, track_id == active)
            widget.removeRequested.connect(self.project_sound.remove_from_playlist)
            item.setSizeHint(widget.sizeHint())
            self.playlist_list.addItem(item)
            self.playlist_list.setItemWidget(item, widget)
        self.playlist_list.blockSignals(False)
        count = self.playlist_list.count()
        self.playlist_summary.setText(f"Playlist • {count} track{'s' if count != 1 else ''} • {_format_duration(total_duration)}")
        expanded = self.project_sound.state.playlist_expanded
        self.playlist_list.setVisible(expanded)
        self.expand_btn.setText("Collapse" if expanded else "Expand")

    def _playlist_item_selected(self, item: QtWidgets.QListWidgetItem) -> None:
        track_id = str(item.data(Qt.UserRole) or "")
        if not track_id:
            return
        self.project_sound.select_track(track_id)

    def _playlist_item_activated(self, item: QtWidgets.QListWidgetItem) -> None:
        track_id = str(item.data(Qt.UserRole) or "")
        if not track_id:
            return
        self.project_sound.select_track(track_id)
        self.player.select_track(track_id, autoplay=True)

    def _playlist_rows_moved(self, *_args) -> None:
        ids: list[str] = []
        for row in range(self.playlist_list.count()):
            item = self.playlist_list.item(row)
            ids.append(str(item.data(Qt.UserRole) or ""))
        self.project_sound.reorder_playlist([track_id for track_id in ids if track_id])

    def _sync_transport_enabled(self) -> None:
        has_tracks = bool(self.project_sound.ordered_ids())
        self.play_btn.setEnabled(has_tracks)
        self.prev_btn.setEnabled(has_tracks)
        self.next_btn.setEnabled(self.project_sound.state.mode == "playlist" and len(self.project_sound.state.playlist) > 1)
        self.timeline.setEnabled(has_tracks)
        self.clear_btn.setEnabled(has_tracks)

    def _choose_new_files(self) -> None:
        settings = _read_settings(self.project_root)
        start = str(settings.get(LAST_MUSIC_FOLDER_KEY, ""))
        if self.project_sound.state.mode == "playlist":
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self,
                "Add Playlist Tracks",
                start,
                "Audio Files (*.mp3 *.wav *.ogg *.aac *.m4a *.flac)",
            )
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Choose Music",
                start,
                "Audio Files (*.mp3 *.wav *.ogg *.aac *.m4a *.flac)",
            )
            paths = [path] if path else []
        if paths:
            self._remember_music_folder(paths[0])
            self._start_import(paths)

    def _remember_music_folder(self, path: str) -> None:
        settings = _read_settings(self.project_root)
        settings[LAST_MUSIC_FOLDER_KEY] = str(Path(path).resolve().parent)
        _write_settings(self.project_root, settings)

    def _start_import(self, paths: list[str]) -> None:
        if self._import_thread is not None:
            return
        self.player.stop(reset_position=True)
        known = {record.content_hash: record.track_id for record in self.library.records.values()}
        thread = QtCore.QThread(self)
        worker = _ImportWorker(self.project_root, paths, known)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._import_finished)
        worker.failed.connect(self._import_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_import_handles)
        self._import_thread = thread
        self._import_worker = worker
        self._set_busy(True, f"Importing {len(paths)} track{'s' if len(paths) != 1 else ''}…")
        thread.start()

    def _import_finished(self, payloads: list[dict]) -> None:
        ids = self.library.register_imports(payloads)
        if self.project_sound.state.mode == "playlist":
            self.project_sound.add_to_playlist(ids)
        elif ids:
            self.project_sound.set_single(ids[0])
        self._show_status(f"Added {len(ids)} track{'s' if len(ids) != 1 else ''}.")

    def _import_failed(self, message: str) -> None:
        if "canceled" in message.casefold():
            self._show_status("Import canceled.")
        else:
            self._show_status(f"Import failed: {message}", persistent=True)
            QtWidgets.QMessageBox.critical(self, "Audio Import Error", message)

    def _clear_import_handles(self) -> None:
        self._import_thread = None
        self._import_worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.single_action_btn.setEnabled(not busy)
        self.add_track_btn.setEnabled(not busy)
        self.archive_btn.setEnabled(not busy)
        self.cancel_job_btn.setVisible(busy)
        if message:
            self.status.setText(message)
            self._status_timer.stop()

    def _cancel_background_job(self) -> None:
        if self._import_worker is not None:
            self._import_worker.cancel()
        if self._repair_worker is not None:
            self._repair_worker.cancel()

    def _open_archive_for_current_mode(self) -> None:
        dialog = ArchiveDialog(
            self.library,
            lambda: set(self.project_sound.ordered_ids()),
            self._delete_archive_track,
            self._start_archive_repair,
            multi_select=self.project_sound.state.mode == "playlist",
            parent=self,
        )
        dialog.tracksChosen.connect(self._archive_tracks_chosen)
        dialog.exec()

    def _archive_tracks_chosen(self, track_ids: list[str]) -> None:
        if self.project_sound.state.mode == "playlist":
            self.project_sound.add_to_playlist(track_ids)
        elif track_ids:
            self.project_sound.set_single(track_ids[0])

    def _delete_archive_track(self, track_id: str) -> bool:
        record = self.library.get(track_id)
        if record is None:
            return False
        state_backup = ProjectSoundState.from_dict(self.project_sound.state.to_dict())
        used = self.project_sound.state.is_using(track_id)
        if used:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Track Is In Use")
            box.setText(f"{record.display_title} is used by this letter.")
            remove_button = box.addButton("Remove From This Letter", QtWidgets.QMessageBox.ActionRole)
            delete_button = box.addButton("Delete From Archive", QtWidgets.QMessageBox.DestructiveRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked == remove_button:
                self.project_sound.remove_usage(track_id)
                return False
            if clicked != delete_button:
                return False
            self.project_sound.remove_usage(track_id)
        else:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Delete Track",
                f"Delete {record.display_title} from the music archive?",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        self.player.release_current_file_handle()
        deleted = self.library.delete_track(track_id)
        if not deleted:
            self.project_sound.state = state_backup
            self.project_sound.save()
            self._show_status("The track could not be deleted because its file is still in use.", persistent=True)
            return False
        self.project_sound.save()
        self._reload_player_queue()
        return True

    def _start_archive_repair(self) -> None:
        if self._repair_thread is not None:
            return
        thread = QtCore.QThread(self)
        worker = _RepairWorker(self.project_root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._repair_finished)
        worker.failed.connect(self._repair_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_repair_handles)
        self._repair_thread = thread
        self._repair_worker = worker
        self._set_busy(True, "Repairing music archive…")
        thread.start()

    def _repair_finished(self, repaired: int, issues: list[str]) -> None:
        self.library.records = load_library(self.project_root)
        self.library.changed.emit()
        if issues:
            QtWidgets.QMessageBox.warning(
                self,
                "Archive Repair",
                f"Repaired {repaired} item(s).\n\n" + "\n".join(issues[:12]),
            )
        else:
            self._show_status(f"Archive repair completed: {repaired} item(s) repaired.")

    def _repair_failed(self, message: str) -> None:
        self._show_status(f"Archive repair failed: {message}", persistent=True)

    def _clear_repair_handles(self) -> None:
        self._repair_thread = None
        self._repair_worker = None
        self._set_busy(False)

    def _create_playlist(self) -> None:
        self.project_sound.create_playlist()
        if not self.project_sound.state.playlist:
            self._choose_new_files()

    def _convert_to_single(self) -> None:
        selected = self.project_sound.state.selected_track_id
        row = self.playlist_list.currentRow()
        if row >= 0:
            selected = str(self.playlist_list.item(row).data(Qt.UserRole) or selected)
        self.project_sound.convert_to_single(selected)

    def _toggle_playlist_expanded(self) -> None:
        self.project_sound.state.playlist_expanded = not self.project_sound.state.playlist_expanded
        save_project_state(self.project_root, self.project_sound.state)
        self._refresh_playlist()

    def _clear_project_sound(self) -> None:
        if not self.project_sound.ordered_ids():
            return
        if QtWidgets.QMessageBox.question(self, "Clear Project Sound", "Remove all music from this letter?") == QtWidgets.QMessageBox.Yes:
            self.player.stop(reset_position=True)
            self.project_sound.clear()

    def _toggle_play(self) -> None:
        if not self._tab_active:
            return
        if self.player.is_playing():
            self.player.pause()
        else:
            self.player.play()
            self._prime_analysis_for_current()

    def _on_track_changed(self, track_id: str) -> None:
        if track_id and track_id in self.project_sound.ordered_ids():
            self.project_sound.state.selected_track_id = track_id
            save_project_state(self.project_root, self.project_sound.state)
        record = self.library.get(track_id)
        self.now_playing.setText(f"Now Playing: {record.display_title}" if record else "No music selected")
        path = self.library.path_for(track_id) if track_id else None
        self._preview.set_audio_file(str(path) if path else "")
        self._refresh_playlist() if self.project_sound.state.mode == "playlist" else None
        self._prime_analysis_for_current()

    def _on_position_changed(self, position: int, duration: int) -> None:
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(0, duration))
        self.timeline.setValue(max(0, min(position, duration if duration > 0 else position)))
        self.timeline.blockSignals(False)
        self.elapsed.setText(_format_ms(position))
        self.total.setText(_format_ms(duration))

    def _on_active_player_changed(self, player: QMediaPlayer) -> None:
        self._preview.set_media_player(player)

    def _prime_analysis_for_current(self) -> None:
        if self._analysis is None or self._analysis_disabled:
            return
        path = self.library.path_for(self.player.current_track_id)
        if path is None:
            return
        self._analysis_key = str(path.resolve())
        try:
            cached = self._analysis.load_cached(path)
            if cached is not None:
                self._preview.set_analysis_payload(cached)
            else:
                self._analysis.ensure_analyzed(path, priority=True)
        except Exception as exc:
            self._disable_analysis(str(exc))

    def _on_analysis_ready(self, path_key: str, payload: dict) -> None:
        if str(path_key) == self._analysis_key:
            self._preview.set_analysis_payload(payload)

    def _on_analysis_failed(self, _path_key: str, message: str) -> None:
        self._disable_analysis(message)

    def _disable_analysis(self, message: str) -> None:
        if self._analysis_disabled:
            return
        self._analysis_disabled = True
        logging.getLogger(__name__).warning("Sound analysis disabled for this session: %s", message)
        manager = self._analysis
        self._analysis = None
        try:
            if manager is not None:
                manager.shutdown()
        except Exception:
            pass

    def _show_status(self, message: str, persistent: bool = False) -> None:
        self.status.setText(message)
        self._status_timer.stop()
        if not persistent:
            self._status_timer.start(4200)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if any(Path(url.toLocalFile()).suffix.casefold() in VALID_AUDIO_EXTS for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.casefold() in VALID_AUDIO_EXTS
        ]
        if not paths:
            event.ignore()
            return
        if self.project_sound.state.mode == "single":
            paths = paths[:1]
        self._remember_music_folder(paths[0])
        self._start_import(paths)
        event.acceptProposedAction()

    def shared_preview_widget(self) -> QtWidgets.QWidget:
        return self._preview

    def activate_for_tab_change(self) -> None:
        self._tab_active = True
        self._preview.set_tab_active(True)

    def deactivate_for_tab_change(self) -> None:
        self._tab_active = False
        self.player.stop(reset_position=True)
        self._preview.set_tab_active(False)
        self.play_btn.setText("▶")

    def release_current_file_handle(self) -> None:
        self.player.stop(reset_position=True)
        for player in self.player.players:
            player.setSource(QUrl())

    def reset_project_sound(self) -> None:
        self.player.stop(reset_position=True)
        self.project_sound.clear()
        self._refresh_ui()

    def reload_project_from_disk(self) -> None:
        self.player.stop(reset_position=True)
        self.library.records = load_library(self.project_root)
        self.project_sound.state = load_project_state(
            self.project_root,
            valid_ids=set(self.library.records),
        )
        self._reload_player_queue()
        self._refresh_ui()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.activate_for_tab_change()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.deactivate_for_tab_change()
        super().hideEvent(event)

    def _stop_background_threads(self) -> None:
        self._cancel_background_job()
        for thread in (self._import_thread, self._repair_thread):
            if thread is None or not thread.isRunning():
                continue
            thread.quit()
            thread.wait(3500)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._stop_background_threads()
        self.player.shutdown()
        self._preview.shutdown()
        if self._analysis is not None:
            try:
                self._analysis.shutdown()
            except Exception:
                pass
        super().closeEvent(event)


__all__ = ["SoundTab", "SoundLibrary", "ProjectSound", "PlaylistPlayer"]
