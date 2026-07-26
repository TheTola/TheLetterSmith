from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6 import QtCore
from PySide6.QtCore import QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from playlist import CROSSFADE_MS


class PlaylistPlayer(QtCore.QObject):
    """Two-player playlist controller with a fixed one-second crossfade."""

    active_player_changed = Signal(object)
    track_changed = Signal(int, str)
    playback_changed = Signal(bool)

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.crossfade_ms = CROSSFADE_MS
        self.players = (QMediaPlayer(self), QMediaPlayer(self))
        self.audio_outputs = (QAudioOutput(self), QAudioOutput(self))
        for player, output in zip(self.players, self.audio_outputs):
            player.setAudioOutput(output)
        self._active_slot = 0
        self._tracks: tuple[Path, ...] = ()
        self.current_index = 0
        self.repeat = True
        self._volume = 0.31
        self._muted = False
        self._crossfade_next: Optional[int] = None
        self._crossfade_elapsed = 0
        self._shutdown = False
        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setInterval(40)
        self._fade_timer.timeout.connect(self._fade_tick)

        for slot, player in enumerate(self.players):
            player.positionChanged.connect(
                lambda position, slot=slot: self._on_position(slot, position)
            )
            player.mediaStatusChanged.connect(
                lambda status, slot=slot: self._on_media_status(slot, status)
            )
            player.playbackStateChanged.connect(lambda _state: self._emit_playback())
        self.set_volume(self._volume)

    @property
    def active_player(self) -> QMediaPlayer:
        return self.players[self._active_slot]

    @property
    def active_output(self) -> QAudioOutput:
        return self.audio_outputs[self._active_slot]

    @property
    def current_path(self) -> Path | None:
        if not self._tracks or not 0 <= self.current_index < len(self._tracks):
            return None
        return self._tracks[self.current_index]

    @property
    def tracks(self) -> tuple[Path, ...]:
        return self._tracks

    def set_tracks(self, paths: Iterable[str | Path], *, repeat: Optional[bool] = None) -> None:
        new_tracks = tuple(Path(path).resolve() for path in paths)
        if repeat is not None:
            self.repeat = bool(repeat)
        old_path = self.current_path
        was_playing = self.is_playing()
        old_index = self.current_index
        self._cancel_crossfade()
        self._tracks = new_tracks
        if not new_tracks:
            self.stop()
            self.current_index = 0
            return
        if old_path is not None and old_path in new_tracks:
            self.current_index = new_tracks.index(old_path)
        else:
            self.current_index = min(old_index, len(new_tracks) - 1)
        self._load_active()
        if was_playing:
            self.play()

    def set_repeat(self, repeat: bool) -> None:
        self.repeat = bool(repeat)

    def next_index(self, index: Optional[int] = None) -> int | None:
        if not self._tracks:
            return None
        selected = self.current_index if index is None else index
        if selected + 1 < len(self._tracks):
            return selected + 1
        return 0 if self.repeat else None

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._crossfade_next is None:
            self.active_output.setVolume(self._volume)
            self.audio_outputs[1 - self._active_slot].setVolume(0.0)
        else:
            self._apply_fade_volumes()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        for output in self.audio_outputs:
            output.setMuted(self._muted)

    def play(self) -> None:
        if self._shutdown or not self._tracks:
            return
        if self.active_player.source().isEmpty():
            self._load_active()
        self.active_player.play()
        if self._crossfade_next is not None:
            self.players[1 - self._active_slot].play()
            self._fade_timer.start()

    def pause(self) -> None:
        for player in self.players:
            player.pause()
        self._fade_timer.stop()

    def start_over(self) -> None:
        if not self._tracks:
            return
        self._cancel_crossfade()
        self.current_index = 0
        self._load_active()
        self.active_player.play()

    def stop(self) -> None:
        self._cancel_crossfade()
        for player in self.players:
            player.stop()
        self._emit_playback()

    def is_playing(self) -> bool:
        return any(
            player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            for player in self.players
        )

    def release_file_handles(self) -> None:
        self._cancel_crossfade()
        for player in self.players:
            player.stop()
            player.setSource(QUrl())

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.release_file_handles()
        for player in self.players:
            player.setAudioOutput(None)

    def _load_active(self) -> None:
        if not self._tracks:
            return
        path = self._tracks[self.current_index]
        self.active_player.stop()
        self.active_player.setSource(QUrl.fromLocalFile(str(path)))
        self.active_output.setMuted(self._muted)
        self.active_output.setVolume(self._volume)
        standby_slot = 1 - self._active_slot
        self.players[standby_slot].stop()
        self.players[standby_slot].setSource(QUrl())
        self.audio_outputs[standby_slot].setVolume(0.0)
        self.active_player_changed.emit(self.active_player)
        self.track_changed.emit(self.current_index, str(path))

    def _on_position(self, slot: int, position: int) -> None:
        if slot != self._active_slot or self._crossfade_next is not None:
            return
        if len(self._tracks) < 2:
            return
        duration = self.active_player.duration()
        if duration > self.crossfade_ms and duration - position <= self.crossfade_ms:
            next_index = self.next_index()
            if next_index is not None and next_index != self.current_index:
                self._begin_crossfade(next_index)

    def _begin_crossfade(self, next_index: int) -> None:
        if self._crossfade_next is not None or not self._tracks:
            return
        standby_slot = 1 - self._active_slot
        standby = self.players[standby_slot]
        standby.stop()
        standby.setSource(QUrl.fromLocalFile(str(self._tracks[next_index])))
        self.audio_outputs[standby_slot].setMuted(self._muted)
        self.audio_outputs[standby_slot].setVolume(0.0)
        self._crossfade_next = next_index
        self._crossfade_elapsed = 0
        standby.play()
        self._fade_timer.start()

    def _fade_tick(self) -> None:
        self._advance_fade(self._fade_timer.interval())

    def _advance_fade(self, elapsed_ms: int) -> None:
        if self._crossfade_next is None:
            return
        self._crossfade_elapsed += max(0, int(elapsed_ms))
        self._apply_fade_volumes()
        if self._crossfade_elapsed < self.crossfade_ms:
            return

        old_slot = self._active_slot
        new_slot = 1 - old_slot
        old_player = self.players[old_slot]
        old_player.stop()
        old_player.setSource(QUrl())
        self.audio_outputs[old_slot].setVolume(0.0)
        self._active_slot = new_slot
        self.current_index = self._crossfade_next
        self._crossfade_next = None
        self._crossfade_elapsed = 0
        self._fade_timer.stop()
        self.active_output.setVolume(self._volume)
        self.active_player_changed.emit(self.active_player)
        path = self.current_path
        if path is not None:
            self.track_changed.emit(self.current_index, str(path))

    def _apply_fade_volumes(self) -> None:
        if self._crossfade_next is None:
            return
        progress = max(0.0, min(1.0, self._crossfade_elapsed / self.crossfade_ms))
        self.audio_outputs[self._active_slot].setVolume(self._volume * (1.0 - progress))
        self.audio_outputs[1 - self._active_slot].setVolume(self._volume * progress)

    def _cancel_crossfade(self) -> None:
        self._fade_timer.stop()
        standby_slot = 1 - self._active_slot
        self.players[standby_slot].stop()
        self.players[standby_slot].setSource(QUrl())
        self.audio_outputs[standby_slot].setVolume(0.0)
        self._crossfade_next = None
        self._crossfade_elapsed = 0
        self.active_output.setVolume(self._volume)

    def _on_media_status(
        self,
        slot: int,
        status: QMediaPlayer.MediaStatus,
    ) -> None:
        if slot != self._active_slot or status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if self._crossfade_next is not None:
            return
        next_index = self.next_index()
        if next_index is None:
            self.stop()
            return
        if next_index == self.current_index:
            self.active_player.setPosition(0)
            self.active_player.play()
            return
        self.current_index = next_index
        self._load_active()
        self.active_player.play()

    def _emit_playback(self) -> None:
        self.playback_changed.emit(self.is_playing())


__all__ = ["PlaylistPlayer"]
