from __future__ import annotations

"""Shared, semantic interface sounds for Letter Smith."""

import logging
from enum import Enum
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from project_paths import application_paths


_LOGGER = logging.getLogger(__name__)
_APP_PLAYER_ATTRIBUTE = "_lettersmith_ui_sound_player"
_SOUND_DIRECTORY = Path("sounds") / "App sounds"


class UiSound(str, Enum):
    ADDED = "added"
    BROKEN = "broken"
    REMOVED = "removed"
    SAVED = "saved"
    GITHUB_CONNECTED = "github_connected"
    GITHUB_DISCONNECTED = "github_disconnected"
    PUBLISH_COMPLETE = "publish_complete"
    TAB_SWITCHED = "tab_switched"


SOUND_FILES = {
    UiSound.ADDED: "Chime.mp3",
    UiSound.BROKEN: "Ching.mp3",
    UiSound.REMOVED: "Bounc.mp3",
    UiSound.SAVED: "Save.mp3",
    UiSound.GITHUB_CONNECTED: "connect.mp3",
    UiSound.GITHUB_DISCONNECTED: "deconn.mp3",
    UiSound.PUBLISH_COMPLETE: "Success.mp3",
    UiSound.TAB_SWITCHED: "Switch.mp3",
}


class UiSoundPlayer(QtCore.QObject):
    """Own reusable Qt multimedia players for short interface sounds."""

    OVERLAP_FADE_MS = 240
    OVERLAP_WARP_RATE = 0.92

    def __init__(
        self,
        project_root: str | Path,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self._sound_root = application_paths(
            self.project_root
        ).app_resource_path(_SOUND_DIRECTORY)
        self._players: dict[UiSound, QMediaPlayer] = {}
        self._outputs: dict[UiSound, QAudioOutput] = {}
        self._player_outputs: dict[QMediaPlayer, QAudioOutput] = {}
        self._player_roles: dict[QMediaPlayer, UiSound] = {}
        self._idle_players: dict[UiSound, list[QMediaPlayer]] = {}
        self._active_players: set[QMediaPlayer] = set()
        self._fade_groups: dict[
            QMediaPlayer,
            QtCore.QParallelAnimationGroup,
        ] = {}
        self._fade_pitch_compensation: dict[QMediaPlayer, bool] = {}
        self._missing_files: set[Path] = set()

    def sound_path(self, sound: UiSound | str) -> Path:
        role = UiSound(sound)
        return (self._sound_root / SOUND_FILES[role]).resolve()

    def play(self, sound: UiSound | str) -> bool:
        role = UiSound(sound)
        path = self.sound_path(role)
        if not path.is_file():
            if path not in self._missing_files:
                self._missing_files.add(path)
                _LOGGER.warning("Interface sound is missing: %s", path)
            return False

        self._fade_active_players()
        player = self._players.get(role)
        if (
            player is None
            or player in self._active_players
            or player in self._fade_groups
        ):
            idle_players = self._idle_players.get(role, [])
            player = (
                idle_players.pop()
                if idle_players
                else self._create_player(role, path)
            )
            self._players[role] = player
            self._outputs[role] = self._player_outputs[player]
        output = self._player_outputs[player]

        output.setVolume(1.0)
        player.setPlaybackRate(1.0)
        player.setPosition(0)
        self._active_players.add(player)
        player.play()
        return True

    def _create_player(self, role: UiSound, path: Path) -> QMediaPlayer:
        output = QAudioOutput(self)
        output.setVolume(1.0)
        player = QMediaPlayer(self)
        player.setAudioOutput(output)
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.playbackStateChanged.connect(
            lambda state, active_player=player: self._on_playback_state_changed(
                active_player,
                state,
            )
        )
        self._players[role] = player
        self._outputs[role] = output
        self._player_outputs[player] = output
        self._player_roles[player] = role
        return player

    def _on_playback_state_changed(
        self,
        player: QMediaPlayer,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._active_players.add(player)
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and player not in self._fade_groups
        ):
            self._active_players.discard(player)

    def _fade_active_players(self) -> None:
        for player in tuple(self._active_players):
            if player not in self._fade_groups:
                self._fade_player(player)

    def _fade_player(self, player: QMediaPlayer) -> None:
        output = self._player_outputs.get(player)
        if output is None:
            self._active_players.discard(player)
            return

        group = QtCore.QParallelAnimationGroup(self)
        volume_fade = QtCore.QPropertyAnimation(output, b"volume", group)
        volume_fade.setDuration(self.OVERLAP_FADE_MS)
        volume_fade.setStartValue(output.volume())
        volume_fade.setEndValue(0.0)
        volume_fade.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        warp = QtCore.QPropertyAnimation(player, b"playbackRate", group)
        warp.setDuration(self.OVERLAP_FADE_MS)
        warp.setStartValue(player.playbackRate())
        warp.setEndValue(self.OVERLAP_WARP_RATE)
        warp.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

        if hasattr(player, "pitchCompensation"):
            pitch_compensation = bool(player.pitchCompensation())
            self._fade_pitch_compensation[player] = pitch_compensation
            if pitch_compensation:
                player.setPitchCompensation(False)

        self._fade_groups[player] = group
        group.finished.connect(
            lambda active_player=player, active_group=group: self._finish_fade(
                active_player,
                active_group,
            )
        )
        group.start()

    def _finish_fade(
        self,
        player: QMediaPlayer,
        group: QtCore.QParallelAnimationGroup,
    ) -> None:
        if self._fade_groups.get(player) is not group:
            return
        self._fade_groups.pop(player, None)
        self._active_players.discard(player)
        output = self._player_outputs.get(player)
        player.pause()
        player.setPlaybackRate(1.0)
        if output is not None:
            output.setVolume(1.0)
        pitch_compensation = self._fade_pitch_compensation.pop(player, None)
        if pitch_compensation is not None:
            player.setPitchCompensation(pitch_compensation)

        role = self._player_roles.get(player)
        if role is not None and self._players.get(role) is not player:
            idle_players = self._idle_players.setdefault(role, [])
            if player not in idle_players:
                idle_players.append(player)
        group.deleteLater()

    def stop_all(self) -> None:
        """Stop every voice and restore its normal playback properties."""
        for group in tuple(self._fade_groups.values()):
            group.stop()
            group.deleteLater()
        self._fade_groups.clear()

        for player, output in self._player_outputs.items():
            player.pause()
            player.setPlaybackRate(1.0)
            output.setVolume(1.0)
            pitch_compensation = self._fade_pitch_compensation.pop(
                player,
                None,
            )
            if pitch_compensation is not None:
                player.setPitchCompensation(pitch_compensation)
        self._active_players.clear()

    def eventFilter(
        self,
        watched: QtCore.QObject,
        event: QtCore.QEvent,
    ) -> bool:
        if event.type() != QtCore.QEvent.Type.MouseButtonPress:
            return False
        if not isinstance(event, QtGui.QMouseEvent):
            return False
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False

        button = self._button_at_event(watched, event)
        if button is not None and self._is_broken(button):
            self.play(UiSound.BROKEN)
        return False

    @staticmethod
    def _button_at_event(
        watched: QtCore.QObject,
        event: QtGui.QMouseEvent,
    ) -> QtWidgets.QAbstractButton | None:
        if isinstance(watched, QtWidgets.QAbstractButton):
            return watched
        candidate: QtWidgets.QWidget | None = None
        application = QtWidgets.QApplication.instance()
        if application is not None:
            candidate = application.widgetAt(event.globalPosition().toPoint())
        if candidate is None and isinstance(watched, QtWidgets.QWidget):
            candidate = watched
        while candidate is not None:
            if isinstance(candidate, QtWidgets.QAbstractButton):
                return candidate
            candidate = candidate.parentWidget()
        return None

    @staticmethod
    def _is_broken(button: QtWidgets.QAbstractButton) -> bool:
        semantic_state = getattr(button, "semantic_state", None)
        semantic_value = getattr(semantic_state, "value", semantic_state)
        return (
            str(semantic_value or "").casefold() == "broken"
            or str(button.property("letterSmithSoundState") or "").casefold()
            == "broken"
        )


def install_ui_sounds(project_root: str | Path) -> UiSoundPlayer:
    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError(
            "QApplication must exist before interface sounds are installed."
        )

    existing = getattr(application, _APP_PLAYER_ATTRIBUTE, None)
    resolved_root = Path(project_root).resolve()
    if isinstance(existing, UiSoundPlayer):
        if existing.project_root == resolved_root:
            return existing
        application.removeEventFilter(existing)
        existing.stop_all()
        existing.deleteLater()

    player = UiSoundPlayer(resolved_root, application)
    application.installEventFilter(player)
    setattr(application, _APP_PLAYER_ATTRIBUTE, player)
    return player


def play_ui_sound(sound: UiSound | str) -> bool:
    application = QtWidgets.QApplication.instance()
    player = (
        getattr(application, _APP_PLAYER_ATTRIBUTE, None)
        if application is not None
        else None
    )
    return isinstance(player, UiSoundPlayer) and player.play(sound)


__all__ = [
    "SOUND_FILES",
    "UiSound",
    "UiSoundPlayer",
    "install_ui_sounds",
    "play_ui_sound",
]
