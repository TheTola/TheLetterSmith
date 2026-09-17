from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from PySide6.QtMultimedia import QMediaPlayer

from Forge_Tab import FORGE_ACTION_FONT_POINT_SIZE, ForgeTab
from Image_tab import ImageTab
from Message_tab import MessageTab
from config import USER_PAGES_DIR, validate_required_images
from generate import build_source_fingerprint
from image_animation import build_runtime_image_assets
from image_button import (
    BUTTON_STATE_FADE_MS,
    BUTTON_STATE_STABILIZATION_MS,
    ArtworkButton,
    ButtonSemanticState,
)
from publishing.expiration import (
    GITHUB_PAGES_PROVIDER_ID,
    clear_publication_state,
)
from publishing.github_auth import (
    GitHubAccount,
    GitHubConnectionSnapshot,
    GitHubConnectionState,
    GitHubPublishingAccess,
    GitHubSession,
    GitHubToken,
)
from readiness import (
    ReadinessResult,
    evaluate_project_save_eligibility,
    evaluate_readiness,
)
from settings_store import (
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    REQUIRED_FEATURES_KEY,
    SettingsStore,
)
from sound_tab import SoundTab
from ui_theme import (
    APPLICATION_FONT_ROLE,
    BASIC_BUTTON_TIER_STYLES,
    BUTTON_TIER_STYLES,
    ButtonTier,
    TAB_HEADING_FONT_POINT_SIZE,
    THEME_FONT_ROLE_PROPERTY,
    ThemeService,
)
from ui_sounds import (
    SOUND_FILES,
    UI_SOUND_VOLUME,
    UI_SOUND_VOLUMES,
    UiSound,
    UiSoundPlayer,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UiSoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_interface_sound_roles_use_the_requested_files(self) -> None:
        self.assertEqual(
            SOUND_FILES,
            {
                UiSound.ADDED: "Chime.mp3",
                UiSound.BROKEN: "Ching.mp3",
                UiSound.OPENED: "Open.mp3",
                UiSound.REMOVED: "Bounc.mp3",
                UiSound.SAVED: "Save.mp3",
                UiSound.GITHUB_CONNECTED: "connect.mp3",
                UiSound.GITHUB_DISCONNECTED: "deconn.mp3",
                UiSound.PUBLISH_COMPLETE: "Success.mp3",
                UiSound.TAB_SWITCHED: "Switch.mp3",
                UiSound.BLIP: "Blip.mp3",
                UiSound.ERROR: "error.mp3",
            },
        )
        player = UiSoundPlayer(PROJECT_ROOT)
        for sound in UiSound:
            self.assertTrue(player.sound_path(sound).is_file())

    def test_broken_artwork_button_attempt_plays_broken_sound(self) -> None:
        player = UiSoundPlayer(PROJECT_ROOT)
        button = ArtworkButton(
            "Unavailable",
            PROJECT_ROOT,
            "AButton.png",
            broken_artwork_filename="AButton.png",
        )
        button.set_unavailable(True)
        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(1, 1),
            QtCore.QPointF(1, 1),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        with mock.patch.object(player, "play") as play:
            player.eventFilter(button, event)
        play.assert_called_once_with(UiSound.BROKEN)
        with mock.patch.object(player, "play") as play:
            player.eventFilter(QtWidgets.QPushButton("Ordinary"), event)
        play.assert_not_called()

    def test_open_sound_is_limited_to_the_editor_window(
        self,
    ) -> None:
        player = UiSoundPlayer(PROJECT_ROOT)
        editor = QtWidgets.QDialog()
        editor.setObjectName("LetterEditor")
        dialog = QtWidgets.QDialog()
        secondary_window = QtWidgets.QWidget()
        primary_window = QtWidgets.QMainWindow()
        primary_window.setObjectName("NexusWindow")
        menu = QtWidgets.QMenu()
        child = QtWidgets.QWidget(secondary_window)
        event = QtCore.QEvent(QtCore.QEvent.Type.Show)

        with mock.patch.object(player, "play") as play:
            for window in (
                editor,
                dialog,
                secondary_window,
                primary_window,
                menu,
                child,
            ):
                player.eventFilter(window, event)

        self.assertEqual(
            play.call_args_list,
            [mock.call(UiSound.OPENED)],
        )

    def test_interface_sounds_use_semantic_volume_levels(self) -> None:
        player = UiSoundPlayer(PROJECT_ROOT)
        self.addCleanup(player.deleteLater)
        self.addCleanup(player.stop_all)
        self.assertLessEqual(UI_SOUND_VOLUME, 0.1)
        self.assertEqual(set(UI_SOUND_VOLUMES), set(UiSound))
        self.assertAlmostEqual(
            UI_SOUND_VOLUMES[UiSound.TAB_SWITCHED],
            UI_SOUND_VOLUME * 0.8,
        )
        self.assertEqual(UI_SOUND_VOLUMES[UiSound.ADDED], 0.09)
        self.assertLess(
            UI_SOUND_VOLUMES[UiSound.BLIP],
            UI_SOUND_VOLUMES[UiSound.TAB_SWITCHED],
        )
        self.assertGreater(
            UI_SOUND_VOLUMES[UiSound.PUBLISH_COMPLETE],
            max(
                volume for role, volume in UI_SOUND_VOLUMES.items()
                if role != UiSound.PUBLISH_COMPLETE
            ),
        )
        self.assertTrue(player.play(UiSound.TAB_SWITCHED))
        self.assertAlmostEqual(
            player._outputs[UiSound.TAB_SWITCHED].volume(),
            UI_SOUND_VOLUME * 0.8,
            places=3,
        )
        self.assertTrue(player.play(UiSound.ERROR))
        self.assertAlmostEqual(
            player._outputs[UiSound.ERROR].volume(),
            UI_SOUND_VOLUMES[UiSound.ERROR],
            places=3,
        )
        self.assertTrue(player.play(UiSound.BLIP))
        self.assertAlmostEqual(
            player._outputs[UiSound.BLIP].volume(),
            UI_SOUND_VOLUMES[UiSound.BLIP],
            places=3,
        )

    def test_rapid_tab_switches_do_not_stack_sounds(self) -> None:
        player = UiSoundPlayer(PROJECT_ROOT)
        self.addCleanup(player.deleteLater)
        self.addCleanup(player.stop_all)
        with mock.patch("ui_sounds.monotonic", side_effect=(100, 100.03, 100.09)):
            self.assertTrue(player.play(UiSound.TAB_SWITCHED))
            first_player = player._players[UiSound.TAB_SWITCHED]
            self.assertFalse(player.play(UiSound.TAB_SWITCHED))
            self.assertIs(player._players[UiSound.TAB_SWITCHED], first_player)
            self.assertTrue(player.play(UiSound.TAB_SWITCHED))
            self.assertIsNot(player._players[UiSound.TAB_SWITCHED], first_player)

    def test_overlapping_sound_fades_without_changing_playback_rate(self) -> None:
        player = UiSoundPlayer(PROJECT_ROOT)
        self.addCleanup(player.deleteLater)
        self.addCleanup(player.stop_all)
        with mock.patch("ui_sounds.monotonic", side_effect=(100, 100.1)):
            self.assertTrue(player.play(UiSound.TAB_SWITCHED))
            first_player = player._players[UiSound.TAB_SWITCHED]
            first_output = player._outputs[UiSound.TAB_SWITCHED]
            starting_volume = first_output.volume()

            self.assertTrue(player.play(UiSound.TAB_SWITCHED))
        second_player = player._players[UiSound.TAB_SWITCHED]

        self.assertIsNot(second_player, first_player)
        self.assertIn(first_player, player._fade_groups)
        fade_group = player._fade_groups[first_player]
        fade_group.setCurrentTime(fade_group.duration() // 2)
        self.app.processEvents()
        self.assertLess(first_output.volume(), starting_volume)
        self.assertEqual(first_player.playbackRate(), 1.0)

        fade_group.setCurrentTime(fade_group.duration())
        self.app.processEvents()
        self.assertNotIn(first_player, player._fade_groups)
        self.assertIn(
            first_player.playbackState(),
            {
                QMediaPlayer.PlaybackState.PausedState,
                QMediaPlayer.PlaybackState.StoppedState,
            },
        )
        self.assertAlmostEqual(first_output.volume(), starting_volume)
        with mock.patch("ui_sounds.monotonic", return_value=100.2):
            self.assertTrue(player.play(UiSound.TAB_SWITCHED))
        self.assertIs(player._players[UiSound.TAB_SWITCHED], first_player)


class ThemedButtonStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix=".button-state-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _make_preview_ready(self) -> None:
        SettingsStore(self.root).update_fields(
            {
                "recipient_name": "Ada Lovelace",
                "recipient_title": "Analytical Engine",
                REQUIRED_FEATURES_KEY: [],
            }
        )
        pages = self.root / USER_PAGES_DIR
        pages.mkdir(parents=True, exist_ok=True)
        for filename in ("cover.png", "letter.png", "wall.png", "back.png"):
            image = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#ffffff"))
            self.assertTrue(image.save(str(pages / filename)))

    def _mark_published(self, fingerprint: str = "published-fingerprint") -> None:
        SettingsStore(self.root).update_fields(
            {
                PUBLISHED_PAGE_URL_KEY: "https://ada.github.io/stable-link/",
                PUBLISHED_PUBLIC_PATH_KEY: "stable-link",
                PUBLISHED_AT_KEY: "2026-08-28T12:00:00+00:00",
                PUBLICATION_PROVIDER_KEY: GITHUB_PAGES_PROVIDER_ID,
                PUBLICATION_VERIFIED_KEY: True,
                PUBLISHED_SOURCE_FINGERPRINT_KEY: fingerprint,
                PUBLISHED_GITHUB_OWNER_KEY: "ada",
                PUBLISHED_GITHUB_REPOSITORY_KEY: "ada.github.io",
            }
        )

    def _write_button_artwork(self, path: Path, color: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QtGui.QImage(160, 64, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor(color))
        self.assertTrue(image.save(str(path)))

    def test_visible_button_state_stabilizes_and_crossfades_both_ways(
        self,
    ) -> None:
        normal = (
            self.root
            / "resources/app/themes/cyber_forge/buttons/AButton.png"
        )
        broken = (
            self.root
            / "resources/app/themes/cyber_forge/buttons/broken/AButton.png"
        )
        self._write_button_artwork(normal, "#f3f7fb")
        self._write_button_artwork(broken, "#57212b")

        button = ArtworkButton(
            "Clear",
            self.root,
            "AButton.png",
            broken_artwork_filename="AButton.png",
        )
        self.addCleanup(button.deleteLater)
        button.show()
        self.app.processEvents()

        self.assertEqual(
            button._state_delay_timer.interval(),
            BUTTON_STATE_STABILIZATION_MS,
        )
        self.assertEqual(
            button._artwork_transition.duration(),
            BUTTON_STATE_FADE_MS,
        )
        self.assertEqual(button.artwork_path, normal.resolve())

        button.set_action_state(broken=True, invisible=False)
        self.assertFalse(button.isEnabled())
        self.assertEqual(button.semantic_state, ButtonSemanticState.BROKEN)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertTrue(button._state_delay_timer.isActive())

        QtTest.QTest.qWait(BUTTON_STATE_STABILIZATION_MS - 250)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertEqual(button.artwork_path, normal.resolve())

        QtTest.QTest.qWait(400)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(button.artwork_path, broken.resolve())
        self.assertEqual(
            button._artwork_transition.state(),
            QtCore.QAbstractAnimation.State.Running,
        )
        self.assertEqual(button._artwork_transition_from_path, normal.resolve())
        self.assertEqual(button._artwork_transition_to_path, broken.resolve())

        QtTest.QTest.qWait(BUTTON_STATE_FADE_MS + 100)
        self.assertEqual(
            button._artwork_transition.state(),
            QtCore.QAbstractAnimation.State.Stopped,
        )

        button.set_action_state(broken=False, invisible=False)
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertTrue(button._state_delay_timer.isActive())

        QtTest.QTest.qWait(BUTTON_STATE_STABILIZATION_MS + 150)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertEqual(button.artwork_path, normal.resolve())
        self.assertEqual(
            button._artwork_transition.state(),
            QtCore.QAbstractAnimation.State.Running,
        )
        self.assertEqual(button._artwork_transition_from_path, broken.resolve())
        self.assertEqual(button._artwork_transition_to_path, normal.resolve())

        QtTest.QTest.qWait(BUTTON_STATE_FADE_MS + 100)
        button.set_action_state(broken=True, invisible=False)
        QtTest.QTest.qWait(100)
        button.set_action_state(broken=False, invisible=False)
        self.assertFalse(button._state_delay_timer.isActive())
        self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertEqual(
            button._artwork_transition.state(),
            QtCore.QAbstractAnimation.State.Stopped,
        )

    def test_reenabled_artwork_button_cancels_pending_invisible_fade(self) -> None:
        button = ArtworkButton("Settings", self.root, "AButton.png")
        self.addCleanup(button.deleteLater)
        button.show()
        self.app.processEvents()

        button.setEnabled(False)
        self.assertTrue(button.is_invisible)
        self.assertEqual(
            button._opacity_transition.state(),
            QtCore.QAbstractAnimation.State.Running,
        )
        button.setEnabled(True)
        self.assertFalse(button.is_invisible)

        QtTest.QTest.qWait(BUTTON_STATE_FADE_MS + 100)
        self.assertTrue(button.isEnabled())
        self.assertAlmostEqual(button._visual_opacity, 0.94)
        self.assertEqual(
            button._opacity_transition.state(),
            QtCore.QAbstractAnimation.State.Stopped,
        )

    def test_fixed_control_mappings_and_empty_states(self) -> None:
        image = ImageTab(self.root)
        sound = SoundTab(self.root)
        forge = ForgeTab(self.root)
        message = MessageTab(str(self.root))
        self.addCleanup(image.deleteLater)
        self.addCleanup(sound.deleteLater)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(message.deleteLater)
        self.addCleanup(lambda: sound.shutdown(timeout_ms=1000))
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        self.addCleanup(lambda: message.shutdown(timeout_ms=1000))

        self.assertEqual(image.reset_btn._artwork_filename, "CButton.png")
        self.assertEqual(image.open_btn._artwork_filename, "CButton.png")
        self.assertFalse(image.reset_btn.isEnabled())
        self.assertFalse(image.open_btn.isEnabled())
        self.assertEqual(
            image.reset_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            image.reset_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            image.open_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        clear_spy = QtTest.QSignalSpy(image.cards[1].clear_requested)
        image.cards[1].clear_btn.click()
        self.assertEqual(clear_spy.count(), 0)
        with mock.patch("Image_tab.clear_slot_asset") as clear_slot:
            image.clear_image(1)
        clear_slot.assert_not_called()
        for card in image.cards.values():
            self.assertEqual(card.clear_btn._artwork_filename, "AButton.png")
            self.assertFalse(card.clear_btn.isEnabled())
            self.assertEqual(
                card.clear_btn.semantic_state,
                ButtonSemanticState.BROKEN,
            )

        self.assertEqual(sound.single_action_btn._artwork_filename, "BButton.png")
        self.assertEqual(sound.create_playlist_btn._artwork_filename, "BButton.png")
        self.assertEqual(sound.archive_btn._artwork_filename, "EButton.png")
        self.assertEqual(sound.clear_btn._artwork_filename, "DButton.png")
        self.assertEqual(sound.single_action_btn.text(), "Add Music")
        self.assertTrue(sound.single_action_btn.isEnabled())
        self.assertTrue(sound.stock_btn.isEnabled())
        self.assertTrue(sound.archive_btn.isEnabled())
        self.assertFalse(sound.clear_btn.isEnabled())
        self.assertFalse(sound.play_btn.isEnabled())
        self.assertFalse(sound.mute_btn.isEnabled())
        self.assertTrue(sound.create_playlist_btn.isHidden())
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        sound.project_sound.state.single_track_id = "loaded-track"
        sound._sync_transport_enabled()
        self.assertTrue(sound.clear_btn.isEnabled())
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )
        sound.project_sound.state.single_track_id = ""
        sound._sync_transport_enabled()
        self.assertFalse(sound.clear_btn.isEnabled())
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )

        SettingsStore(self.root).update_fields(
            {REQUIRED_FEATURES_KEY: ["music"]}
        )
        sound._sync_transport_enabled()
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )

        self.assertEqual(forge.readiness_btn._artwork_filename, "BButton.png")
        self.assertEqual(forge.load_saved_btn._artwork_filename, "AButton.png")
        self.assertEqual(forge.load_stock_btn._artwork_filename, "AButton.png")
        self.assertEqual(forge.github_account_btn._artwork_filename, "BButton.png")
        self.assertEqual(forge.preview_btn._artwork_filename, "ALbutton.png")
        self.assertEqual(forge.publish_btn._artwork_filename, "BLbutton.png")
        self.assertEqual(forge.unpublish_btn._artwork_filename, "BButton.png")
        self.assertEqual(
            forge.unpublish_btn._theme_artwork_relative_path,
            Path("Unpub/Unpub.png"),
        )
        self.assertEqual(forge.unpublish_btn.text(), "")
        self.assertEqual(forge.unpublish_btn.accessibleName(), "Unpublish Letter")
        self.assertTrue(forge.unpublish_btn.isHidden())
        self.assertEqual(forge.unpublish_btn.size(), forge.readiness_btn.size())
        self.assertIs(
            forge.unpublish_btn.parentWidget(),
            forge._unpublish_controls,
        )
        self.assertEqual(forge.open_published_btn._artwork_filename, "CLbutton.png")
        self.assertEqual(
            tuple(forge._long_action_row.stretch(index) for index in range(3)),
            (2506, 7488, 2506),
        )
        self.assertEqual(forge._long_action_width_scales[forge.preview_btn], 1.0)
        self.assertEqual(forge._long_action_width_scales[forge.publish_btn], 1.0)
        self.assertNotIn(forge.unpublish_btn, forge._long_action_width_scales)
        self.assertEqual(
            forge._long_action_width_scales[forge.open_published_btn],
            0.9,
        )
        self.assertEqual(forge.preview_btn.minimumHeight(), 63)
        self.assertIn("font:700 13pt", forge.preview_btn.styleSheet())
        self.assertEqual(
            forge.preview_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            forge.publish_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )

        self.assertEqual(message.btn._artwork_filename, "FButton.png")
        self.assertEqual(message.edit_btn._artwork_filename, "GButton.png")
        self.assertEqual(message.edit_btn.text(), "Write Letter")
        self.assertEqual(message.revisions_btn._artwork_filename, "HButton.png")
        for button in (message.btn, message.edit_btn, message.revisions_btn):
            self.assertTrue(button.isEnabled())
            self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)

        expected_headings = (
            (image.heading, "Select images for your letter"),
            (sound.heading, "Select music for your letter"),
            (message.heading, "Write your letter’s message"),
            (forge.heading_title, "Review and forge your letter"),
        )
        for heading, text in expected_headings:
            self.assertEqual(heading.text(), text)
            self.assertEqual(
                heading.property(THEME_FONT_ROLE_PROPERTY),
                APPLICATION_FONT_ROLE,
            )
            self.assertEqual(heading.property("themeRole"), "headingText")
            self.assertAlmostEqual(
                heading.font().pointSizeF(),
                TAB_HEADING_FONT_POINT_SIZE,
            )
            self.assertTrue(heading.font().bold())

    def test_unpublish_artwork_resolves_from_all_six_themes(self) -> None:
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
            "dark",
            "light",
        ):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            with self.subTest(theme_id=theme_id):
                self.assertEqual(
                    forge.unpublish_btn.artwork_path,
                    (
                        repository
                        / "resources"
                        / "app"
                        / "themes"
                        / theme_id
                        / "Unpub"
                        / "Unpub.png"
                    ).resolve(),
                )
                self.assertFalse(forge.unpublish_btn._artwork.isNull())

    def test_parent_loading_lock_does_not_stick_buttons_in_invisible_state(
        self,
    ) -> None:
        parent = QtWidgets.QWidget()
        button = ArtworkButton("Import", self.root, "FButton.png", parent)
        self.addCleanup(parent.deleteLater)
        parent.show()
        self.app.processEvents()

        parent.setEnabled(False)
        self.app.processEvents()

        self.assertFalse(button.isEnabled())
        self.assertFalse(button.is_invisible)

        parent.setEnabled(True)
        self.app.processEvents()

        self.assertTrue(button.isEnabled())
        self.assertFalse(button.is_invisible)

    def test_image_availability_uses_only_selected_content(self) -> None:
        image = ImageTab(self.root)
        self.addCleanup(image.deleteLater)
        pages = self.root / USER_PAGES_DIR
        pages.mkdir(parents=True, exist_ok=True)
        cover = pages / "cover.png"
        self.assertTrue(
            QtGui.QImage(12, 12, QtGui.QImage.Format_ARGB32).save(str(cover))
        )

        image.refresh_cards()
        self.assertTrue(image.has_meaningful_images())
        self.assertTrue(image.cards[1].clear_btn.isEnabled())
        self.assertTrue(image.reset_btn.isEnabled())
        self.assertTrue(image.open_btn.isEnabled())

        image._image_import_thread = object()
        image._image_import_index = 2
        image._sync_image_action_state()
        self.assertEqual(
            image.cards[1].clear_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertEqual(
            image.cards[2].clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            image.cards[3].clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            image.reset_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertTrue(image.cards[1].clear_btn.is_invisible)
        self.assertTrue(image.cards[2].clear_btn.is_invisible)
        self.assertTrue(image.cards[3].clear_btn.is_invisible)
        self.assertTrue(image.reset_btn.is_invisible)
        self.assertEqual(
            image.open_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertTrue(image.open_btn.is_invisible)
        for card in image.cards.values():
            self.assertFalse(card.thumbnail.isEnabled())
            effect = card.thumbnail.graphicsEffect()
            self.assertIsInstance(effect, QtWidgets.QGraphicsOpacityEffect)
            self.assertAlmostEqual(effect.opacity(), 0.52)
        self.assertFalse(image.reset_btn.isEnabled())
        image._image_import_thread = None
        image._image_import_index = None
        image._sync_image_action_state()

        cover.unlink()
        (pages / "lettersmith-images.json").write_text(
            '{"schema_version": 1, "slots": {}}',
            encoding="utf-8",
        )
        image.refresh_cards()
        self.assertFalse(image.has_meaningful_images())
        self.assertFalse(image.cards[1].clear_btn.isEnabled())
        self.assertFalse(image.reset_btn.isEnabled())
        self.assertFalse(image.open_btn.isEnabled())
        self.assertEqual(
            image.open_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )

    def test_review_uses_theme_artwork_without_becoming_invisible(self) -> None:
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        service.set_theme("cyber_forge", persist=False)
        forge.apply_theme_assets(service)
        forge.show()
        self.app.processEvents()

        incomplete = ReadinessResult((), 89, "Not Ready")
        forge._update_review_button_state(incomplete)
        self.assertEqual(forge.readiness_btn.text(), "")
        self.assertTrue(forge.readiness_btn.isEnabled())
        self.assertFalse(forge.readiness_btn.is_invisible)
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "reviewbutton.png",
        )

        forge._busy = True
        forge._set_busy(True)
        self.assertTrue(forge.readiness_btn.isEnabled())
        self.assertFalse(forge.readiness_btn.is_invisible)
        forge._busy = False
        forge._set_busy(False)

        complete = ReadinessResult((), 100, "Ready")
        forge._update_review_button_state(complete)
        self.assertEqual(forge.readiness_btn.text(), "")
        self.assertFalse(forge.readiness_btn.isEnabled())
        self.assertFalse(forge.readiness_btn.is_invisible)
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "compbutton.png",
        )
        self.assertEqual(forge.readiness_btn._artwork_transition.duration(), 400)

        forge._update_review_button_state(incomplete)
        self.assertEqual(forge.readiness_btn.text(), "")
        self.assertTrue(forge.readiness_btn.isEnabled())
        self.assertFalse(forge.readiness_btn.is_invisible)
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "reviewbutton.png",
        )

    def test_all_themes_switch_review_and_complete_artwork(self) -> None:
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        account = GitHubAccount("lettersmith", 1)
        forge._apply_github_state(
            GitHubConnectionSnapshot(
                GitHubConnectionState.CONNECTED_READY,
                session=GitHubSession(account, GitHubToken("ghu_access")),
                access=GitHubPublishingAccess(True, "ready"),
            )
        )
        incomplete = ReadinessResult((), 89, "Not Ready")
        complete = ReadinessResult((), 100, "Ready")

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
            "light",
            "dark",
        ):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            forge._update_review_button_state(incomplete)
            forge._update_review_button_state(complete)
            with self.subTest(theme_id=theme_id):
                self.assertEqual(forge.readiness_btn.text(), "")
                self.assertFalse(forge.readiness_btn.isEnabled())
                self.assertTrue(forge.readiness_btn.has_artwork)
                self.assertEqual(
                    forge.readiness_btn.size(),
                    QtCore.QSize(118, 40),
                )
                self.assertEqual(
                    forge.readiness_btn.artwork_path,
                    (
                        repository
                        / "resources"
                        / "app"
                        / "themes"
                        / theme_id
                        / "git"
                        / "CompButton.png"
                    ).resolve(),
                )

            forge._update_review_button_state(incomplete)
            self.assertEqual(forge.readiness_btn.text(), "")
            self.assertTrue(forge.readiness_btn.isEnabled())
            self.assertTrue(forge.readiness_btn.has_artwork)
            self.assertEqual(
                forge.readiness_btn.artwork_path,
                (
                    repository
                    / "resources"
                    / "app"
                    / "themes"
                    / theme_id
                    / "git"
                    / "ReviewButton.png"
                ).resolve(),
            )

    def test_sound_busy_preserves_clear_artwork_and_acquisition_states(self) -> None:
        sound = SoundTab(self.root)
        self.addCleanup(sound.deleteLater)
        self.addCleanup(lambda: sound.shutdown(timeout_ms=1000))

        sound._set_busy(True, "Importing…")
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertTrue(sound.clear_btn.is_invisible)
        for button in (
            sound.single_action_btn,
            sound.stock_btn,
            sound.archive_btn,
        ):
            self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)
            self.assertTrue(button.is_invisible)

        sound._set_busy(False)
        self.assertEqual(
            sound.clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertFalse(sound.clear_btn.is_invisible)
        self.assertTrue(sound.single_action_btn.isEnabled())
        self.assertTrue(sound.stock_btn.isEnabled())
        self.assertTrue(sound.archive_btn.isEnabled())

    def test_sound_tab_uses_spacious_shared_layout_metrics(self) -> None:
        sound = SoundTab(self.root)
        self.addCleanup(sound.deleteLater)
        self.addCleanup(lambda: sound.shutdown(timeout_ms=1000))

        margins = sound.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (28, 22, 28, 24),
        )
        self.assertEqual(sound.layout().spacing(), 18)
        self.assertGreaterEqual(sound.single_panel.layout().spacing(), 16)
        self.assertGreaterEqual(sound.playlist_panel.layout().spacing(), 14)

        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        tiered_buttons = tuple(
            button
            for button in sound.findChildren(QtWidgets.QAbstractButton)
            if button.property("buttonTier")
        )
        for theme_id in ("light", "dark"):
            service.set_theme(theme_id, persist=False)
            sound.apply_theme_assets(service)
            service.apply_semantic_styles(sound)
            self.app.processEvents()
            for button in tiered_buttons:
                tier = ButtonTier(str(button.property("buttonTier")))
                with self.subTest(theme_id=theme_id, button=button.text()):
                    self.assertEqual(
                        button.size(),
                        BASIC_BUTTON_TIER_STYLES[tier].size,
                    )

        service.set_theme("obsidian_forge", persist=False)
        sound.apply_theme_assets(service)
        service.apply_semantic_styles(sound)
        self.app.processEvents()
        for button in tiered_buttons:
            tier = ButtonTier(str(button.property("buttonTier")))
            with self.subTest(theme_id="obsidian_forge", button=button.text()):
                self.assertGreaterEqual(
                    button.width(),
                    BUTTON_TIER_STYLES[tier].width,
                )
                self.assertGreaterEqual(
                    button.height(),
                    BUTTON_TIER_STYLES[tier].height,
                )

    def test_image_utility_buttons_use_full_custom_and_compact_basic_sizes(self) -> None:
        image = ImageTab(self.root)
        self.addCleanup(image.deleteLater)
        image.show()
        settings_button = image.cards[1].settings_btn
        settings_button.setVisible(True)
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(
            repository,
            settings=SettingsStore(self.root),
        )

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            service.set_theme(theme_id, persist=False)
            image.apply_theme_assets(service)
            service.apply_semantic_styles(image)
            self.app.processEvents()
            with self.subTest(theme_id=theme_id):
                self.assertEqual(settings_button.size(), QtCore.QSize(44, 44))
                self.assertEqual(
                    image.reset_btn.size(),
                    BUTTON_TIER_STYLES[ButtonTier.LARGE].size,
                )
                self.assertEqual(image.open_btn.size(), image.reset_btn.size())
                self.assertAlmostEqual(
                    image.reset_btn.font().pointSizeF(),
                    BUTTON_TIER_STYLES[ButtonTier.LARGE].font_point_size,
                )

        for theme_id in ("dark", "light"):
            service.set_theme(theme_id, persist=False)
            image.apply_theme_assets(service)
            service.apply_semantic_styles(image)
            self.app.processEvents()
            with self.subTest(theme_id=theme_id):
                self.assertEqual(settings_button.size(), QtCore.QSize(44, 44))
                self.assertEqual(
                    image.reset_btn.size(),
                    BASIC_BUTTON_TIER_STYLES[ButtonTier.LARGE].size,
                )
                self.assertEqual(image.open_btn.size(), image.reset_btn.size())
                self.assertAlmostEqual(
                    image.reset_btn.font().pointSizeF(),
                    BUTTON_TIER_STYLES[ButtonTier.LARGE].font_point_size,
                )

    def test_forge_long_action_typography_tracks_all_six_themes(self) -> None:
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
            "dark",
            "light",
        ):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            service.apply_semantic_styles(forge)
            self.app.processEvents()

            for button in (
                forge.preview_btn,
                forge.publish_btn,
                forge.open_published_btn,
            ):
                with self.subTest(theme_id=theme_id, button=button.objectName()):
                    self.assertEqual(
                        button.font().family(),
                        service.app_font_family,
                    )
                    self.assertAlmostEqual(
                        button.font().pointSizeF(),
                        FORGE_ACTION_FONT_POINT_SIZE,
                    )
                    self.assertEqual(
                        button.font().weight(),
                        QtGui.QFont.Weight.Bold,
                    )

    def test_github_ready_state_uses_connected_artwork_and_is_reversible(self) -> None:
        with mock.patch.object(ForgeTab, "_validate_github_account_async"):
            forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        service.set_theme("obsidian_forge", persist=False)
        forge.apply_theme_assets(service)

        account = GitHubAccount("lettersmith", 1)
        forge._apply_github_state(
            GitHubConnectionSnapshot(
                GitHubConnectionState.CONNECTED_READY,
                session=GitHubSession(account, GitHubToken("ghu_access")),
                access=GitHubPublishingAccess(True, "ready"),
            )
        )
        self.assertTrue(forge.github_account_btn.isEnabled())
        self.assertEqual(
            forge.github_account_btn.artwork_path.name.casefold(),
            "connectbutton.png",
        )
        self.assertEqual(forge.github_account_summary.text(), "Connected as lettersmith")
        self.assertFalse(
            forge.github_account_summary.property("githubSignInWarning")
        )
        complete = ReadinessResult((), 100, "Ready")
        forge._readiness_result = complete
        forge._update_readiness_summary(complete)
        forge._update_review_button_state(complete)
        self.assertEqual(forge.readiness_summary.text(), "100%  Ready")
        self.assertFalse(forge.readiness_btn.isEnabled())
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "compbutton.png",
        )

        forge._github_account_checking = True
        forge._sync_publishing_controls()
        self.assertEqual(
            forge.github_account_btn.artwork_path.name.casefold(),
            "connectbutton.png",
        )
        forge._github_account_checking = False

        forge._apply_github_state(
            GitHubConnectionSnapshot(
                GitHubConnectionState.RECONNECTING,
                session=GitHubSession(account, GitHubToken("ghu_access")),
                access=GitHubPublishingAccess(True, "ready"),
                retry_attempt=5,
                retry_after_seconds=60,
            )
        )
        self.assertIn("Reconnecting automatically", forge.github_account_summary.text())
        self.assertEqual(
            forge.github_account_btn.artwork_path.name.casefold(),
            "connectbutton.png",
        )
        self.assertTrue(forge._github_reconnect_timer.isActive())

        forge._apply_github_state(
            GitHubConnectionSnapshot(
                GitHubConnectionState.AUTHENTICATED_INSUFFICIENT_PERMISSION,
                session=GitHubSession(account, GitHubToken("ghu_access")),
                access=GitHubPublishingAccess(
                    False,
                    "installation_permissions",
                    message="Publishing access required.",
                ),
            )
        )
        self.assertNotIn("Connected as", forge.github_account_summary.text())
        self.assertIn("Action Required", forge.github_account_summary.text())
        self.assertFalse(forge._github_reconnect_timer.isActive())
        self.assertEqual(
            forge.github_account_btn.artwork_path.name.casefold(),
            "bbutton.png",
        )

        forge._apply_github_state(
            GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED)
        )
        self.assertTrue(forge.github_account_btn.isEnabled())
        self.assertEqual(forge.github_account_btn.text(), "Sign in")
        self.assertEqual(
            forge.github_account_btn.accessibleName(),
            "Sign in to GitHub",
        )
        self.assertEqual(
            forge.github_account_summary.text(),
            "Not signed in to GitHub",
        )
        self.assertNotIn("%", forge.github_account_summary.text())
        self.assertTrue(
            forge.github_account_summary.property("githubSignInWarning")
        )
        self.assertIn("#0d1117", forge.github_account_summary.styleSheet())
        self.assertIn("#58a6ff", forge.github_account_summary.styleSheet())
        self.assertEqual(
            forge.github_account_btn.artwork_path.name.casefold(),
            "bbutton.png",
        )
        self.assertEqual(
            forge.readiness_summary.text(),
            "Sign in to GitHub to publish",
        )
        self.assertNotIn("%", forge.readiness_summary.text())
        self.assertTrue(
            forge.readiness_summary.property("githubSignInWarning")
        )
        self.assertEqual(forge.readiness_btn.text(), "Sign in to publish")
        self.assertEqual(
            forge.readiness_btn.accessibleName(),
            "Sign in to GitHub to publish",
        )
        self.assertTrue(forge.readiness_btn.isEnabled())
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "bbutton.png",
        )
        text_bounds = forge.readiness_btn.fontMetrics().boundingRect(
            QtCore.QRect(
                0,
                0,
                forge.readiness_btn.width() - 6,
                forge.readiness_btn.height() - 8,
            ),
            QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
            forge.readiness_btn.text(),
        )
        self.assertLessEqual(text_bounds.width(), forge.readiness_btn.width() - 6)
        self.assertLessEqual(text_bounds.height(), forge.readiness_btn.height() - 8)
        with (
            mock.patch.object(
                forge,
                "refresh_readiness",
                return_value=complete,
            ),
            mock.patch.object(forge, "show_github_account") as show_account,
        ):
            forge.readiness_btn.click()
        show_account.assert_called_once_with()

        forge._apply_github_state(
            GitHubConnectionSnapshot(
                GitHubConnectionState.CONNECTED_READY,
                session=GitHubSession(account, GitHubToken("ghu_access")),
                access=GitHubPublishingAccess(True, "ready"),
            )
        )
        self.assertEqual(forge.readiness_summary.text(), "100%  Ready")
        self.assertFalse(
            forge.readiness_summary.property("githubSignInWarning")
        )
        self.assertEqual(forge.readiness_btn.text(), "")
        self.assertFalse(forge.readiness_btn.isEnabled())
        self.assertEqual(
            forge.readiness_btn.artwork_path.name.casefold(),
            "compbutton.png",
        )

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
            "dark",
            "light",
        ):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            forge._apply_github_state(
                GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED)
            )
            with self.subTest(theme_id=theme_id):
                self.assertEqual(forge.github_account_btn.text(), "Sign in")
                self.assertEqual(
                    forge.github_account_summary.text(),
                    "Not signed in to GitHub",
                )
                self.assertTrue(
                    forge.github_account_summary.property(
                        "githubSignInWarning"
                    )
                )

        for theme_id in ("dark", "light"):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            service.apply_semantic_styles(forge)
            self.app.processEvents()
            self.assertEqual(forge.github_account_btn.text(), "Sign in")
            self.assertFalse(forge.github_account_btn.has_artwork)
            self.assertEqual(
                forge.github_account_btn.size(),
                QtCore.QSize(118, 40),
            )
            self.assertEqual(
                forge.unpublish_btn.size(),
                QtCore.QSize(118, 40),
            )

            self.assertEqual(forge.preview_btn.minimumHeight(), 63)
            self.assertEqual(forge.publish_btn.minimumHeight(), 63)
            self.assertEqual(forge.open_published_btn.minimumHeight(), 63)

    def test_basic_themes_use_their_connected_github_artwork_without_text(
        self,
    ) -> None:
        with mock.patch.object(ForgeTab, "_validate_github_account_async"):
            forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        account = GitHubAccount("lettersmith", 1)
        connected = GitHubConnectionSnapshot(
            GitHubConnectionState.CONNECTED_READY,
            session=GitHubSession(account, GitHubToken("ghu_access")),
            access=GitHubPublishingAccess(True, "ready"),
        )

        for theme_id in ("light", "dark"):
            service.set_theme(theme_id, persist=False)
            forge.apply_theme_assets(service)
            forge._apply_github_state(connected)
            with self.subTest(theme_id=theme_id):
                self.assertEqual(forge.github_account_btn.text(), "")
                self.assertTrue(forge.github_account_btn.has_artwork)
                self.assertEqual(
                    forge.github_account_btn.size(),
                    QtCore.QSize(118, 40),
                )
                self.assertEqual(
                    forge.unpublish_btn.size(),
                    QtCore.QSize(118, 40),
                )
                self.assertEqual(
                    forge.github_account_btn.artwork_path,
                    (
                        repository
                        / "resources"
                        / "app"
                        / "themes"
                        / theme_id
                        / "git"
                        / "ConnectButton.png"
                    ).resolve(),
                )
                self.assertEqual(
                    forge.github_account_btn.accessibleName(),
                    "GitHub Account — connected",
                )

    def test_long_action_buttons_keep_width_and_artwork_aspect_ratio(self) -> None:
        artwork_root = (
            self.root / "resources/app/themes/cyber_forge/buttons/Long"
        )
        artwork_root.mkdir(parents=True, exist_ok=True)
        broken_root = artwork_root / "broken"
        broken_root.mkdir(parents=True, exist_ok=True)
        for filename in ("ALbutton.png", "BLbutton.png", "CLbutton.png"):
            image = QtGui.QImage(300, 100, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#ffffff"))
            self.assertTrue(image.save(str(artwork_root / filename)))
            broken = QtGui.QImage(300, 180, QtGui.QImage.Format_ARGB32)
            broken.fill(QtGui.QColor("#222222"))
            self.assertTrue(broken.save(str(broken_root / filename)))

        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        service = ThemeService(self.root)
        service.set_theme("cyber_forge", persist=False)
        forge.resize(1200, 820)
        forge.show()
        forge.apply_theme_assets(service)
        QtWidgets.QApplication.processEvents()
        forge.resizeEvent(QtGui.QResizeEvent(forge.size(), forge.size()))
        forge._main_layout.activate()

        layout_widths = {
            button: button.width()
            for button in (
                forge.preview_btn,
                forge.publish_btn,
                forge.open_published_btn,
            )
        }
        forge._sync_long_action_button_aspect_ratios()

        base_width = min(
            layout_widths[button] / forge._long_action_width_scales[button]
            for button in layout_widths
        )
        for button in layout_widths:
            expected_width = round(
                base_width * forge._long_action_width_scales[button]
            )
            self.assertAlmostEqual(button.width(), expected_width, delta=1)
            self.assertEqual(
                button.height(),
                round((button.width() - 4) / 3) + 4,
            )
            self.assertTrue(button._artwork_stretch)
        self.assertAlmostEqual(forge.preview_btn.width(), forge.publish_btn.width(), delta=1)
        self.assertEqual(forge.preview_btn.height(), forge.publish_btn.height())
        self.assertLess(
            forge.open_published_btn.width(),
            forge.preview_btn.width(),
        )

        enabled_sizes = {
            button: button.size()
            for button in layout_widths
        }
        not_ready = type(
            "NotReady",
            (),
            {"can_preview": False, "can_publish": False},
        )()
        forge._update_letter_action_button_states(not_ready)
        for button in layout_widths:
            button._commit_requested_action_state()
        forge._sync_long_action_button_aspect_ratios()
        for button, enabled_size in enabled_sizes.items():
            self.assertEqual(button.size(), enabled_size)

    def test_forge_action_states_prioritize_readiness_over_busy(self) -> None:
        artwork_root = self.root / "resources/app/themes/cyber_forge/buttons/Long"
        broken_root = artwork_root / "broken"
        broken_root.mkdir(parents=True, exist_ok=True)
        for directory in (artwork_root, broken_root):
            for filename in ("ALbutton.png", "BLbutton.png", "CLbutton.png"):
                image = QtGui.QImage(300, 100, QtGui.QImage.Format_ARGB32)
                image.fill(QtGui.QColor("#ffffff"))
                self.assertTrue(image.save(str(directory / filename)))

        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        service = ThemeService(self.root)
        forge.apply_theme_assets(service)

        ready = type("Ready", (), {"can_preview": True, "can_publish": True})()
        not_ready = type(
            "NotReady",
            (),
            {"can_preview": False, "can_publish": False},
        )()

        forge._busy = True
        forge._update_letter_action_button_states(ready)
        for button in (forge.preview_btn, forge.publish_btn):
            self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)
            self.assertTrue(button.is_invisible)
            self.assertFalse(button.isEnabled())
            button._commit_requested_action_state()
            self.assertNotIn("broken", button.artwork_path.as_posix().casefold())

        forge._update_letter_action_button_states(not_ready)
        for button in (forge.preview_btn, forge.publish_btn):
            self.assertEqual(
                button.semantic_state,
                ButtonSemanticState.BROKEN,
            )
            self.assertTrue(button.is_invisible)
            self.assertFalse(button.isEnabled())
            button._commit_requested_action_state()
            self.assertIn("broken", button.artwork_path.as_posix().casefold())

    def test_preview_readiness_uses_only_required_prerequisites(self) -> None:
        self._make_preview_ready()
        readiness = evaluate_readiness(self.root)
        self.assertTrue(readiness.can_preview)
        self.assertTrue(readiness.can_publish)
        self.assertFalse(
            next(item for item in readiness.items if item.key == "message").ready
        )
        self.assertFalse(
            next(item for item in readiness.items if item.key == "music").required
        )

        pages = self.root / USER_PAGES_DIR
        for filename in ("cover.png", "letter.png", "wall.png", "back.png"):
            path = pages / filename
            content = path.read_bytes()
            path.unlink()
            with self.subTest(missing=filename):
                self.assertFalse(evaluate_readiness(self.root).can_preview)
            path.write_bytes(content)

        settings = SettingsStore(self.root)
        settings.update_fields({"recipient_name": ""})
        self.assertFalse(evaluate_readiness(self.root).can_preview)
        settings.update_fields({"recipient_name": "Ada Lovelace"})
        settings.update_fields({"recipient_title": ""})
        self.assertFalse(evaluate_readiness(self.root).can_preview)
        settings.update_fields({"recipient_title": "Analytical Engine"})
        settings.update_fields({REQUIRED_FEATURES_KEY: ["music"]})
        self.assertFalse(evaluate_readiness(self.root).can_preview)
        settings.update_fields({REQUIRED_FEATURES_KEY: []})
        self.assertTrue(evaluate_readiness(self.root).can_preview)

    def test_open_state_is_independent_of_local_readiness_and_network_errors(self) -> None:
        self._make_preview_ready()
        self._mark_published()
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        forge._project_fingerprint = "published-fingerprint"

        forge._update_letter_action_button_states(evaluate_readiness(self.root))
        self.assertEqual(forge.preview_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(forge.publish_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )

        (self.root / USER_PAGES_DIR / "cover.png").unlink()
        forge._update_letter_action_button_states(evaluate_readiness(self.root))
        self.assertEqual(forge.preview_btn.semantic_state, ButtonSemanticState.BROKEN)
        self.assertEqual(forge.publish_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertTrue(forge.publish_btn.is_invisible)
        self.assertFalse(forge.publish_btn.isEnabled())
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )

        SettingsStore(self.root).update_fields(
            {"recipient_name": "", "recipient_title": ""}
        )
        forge._github_account_error = "Temporary network failure"
        forge._sync_publishing_controls()
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )

        cover = self.root / USER_PAGES_DIR / "cover.png"
        image = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("#ffffff"))
        self.assertTrue(image.save(str(cover)))
        SettingsStore(self.root).update_fields(
            {
                "recipient_name": "Ada Lovelace",
                "recipient_title": "Analytical Engine",
            }
        )
        SettingsStore(self.root).update_fields(clear_publication_state())
        restored = evaluate_readiness(self.root)
        forge._update_letter_action_button_states(restored)
        self.assertEqual(forge.preview_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(forge.publish_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )

    def test_invisible_layer_preserves_and_retargets_underlying_artwork(self) -> None:
        self._make_preview_ready()
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        ready = evaluate_readiness(self.root)
        forge._update_letter_action_button_states(ready)
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )

        forge._busy = True
        forge._busy_operation = "Publishing letter…"
        forge._update_letter_action_button_states(ready)
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertTrue(button.is_invisible)
            self.assertFalse(button.isEnabled())
        self.assertEqual(forge.preview_btn.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )

        self._mark_published()
        forge._update_letter_action_button_states(ready)
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )

        forge._busy = False
        forge._busy_operation = ""
        forge._update_letter_action_button_states(ready)
        self.assertFalse(forge.open_published_btn.is_invisible)
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertFalse(
            forge.open_published_btn._state_delay_timer.isActive()
        )

        forge._busy = True
        forge._busy_operation = "Removing online copy…"
        forge._update_letter_action_button_states(ready)
        self.assertTrue(forge.open_published_btn.is_invisible)
        SettingsStore(self.root).update_fields(clear_publication_state())
        forge._update_letter_action_button_states(ready)
        self.assertEqual(
            forge.open_published_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        forge._busy = False
        forge._busy_operation = ""
        forge._update_letter_action_button_states(ready)
        self.assertEqual(
            forge.open_published_btn.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )

    def test_open_click_does_not_apply_invisible_state(self) -> None:
        self._make_preview_ready()
        self._mark_published()
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        forge._update_letter_action_button_states(evaluate_readiness(self.root))

        with mock.patch.object(
            QtGui.QDesktopServices,
            "openUrl",
            return_value=True,
        ):
            forge.open_published_letter()

        self.assertFalse(forge._busy)
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertFalse(button.is_invisible)

    def test_forge_distinguishes_published_and_changed_states(self) -> None:
        forge = ForgeTab(self.root)
        self.addCleanup(forge.deleteLater)
        self.addCleanup(lambda: forge.shutdown(timeout_ms=1000))
        forge._project_fingerprint = "published-fingerprint"
        self._mark_published()

        ready = type("Ready", (), {"can_preview": True, "can_publish": True})()
        forge._update_letter_action_button_states(ready)

        self.assertEqual(forge.publish_btn.text(), "Published")
        self.assertTrue(forge.publish_btn.is_invisible)
        self.assertFalse(forge.publish_btn.isEnabled())
        self.assertFalse(forge.unpublish_btn.isHidden())
        self.assertTrue(forge.unpublish_btn.isEnabled())
        self.assertEqual(forge.unpublish_btn.text(), "")
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertFalse(button.isHidden())

        forge._busy = True
        forge._busy_operation = "Publishing letter…"
        forge._update_letter_action_button_states(ready)
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertTrue(button.isHidden())

        forge._busy = False
        forge._busy_operation = ""
        forge._update_letter_action_button_states(ready)
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertFalse(button.isHidden())

        forge._pending_publish_context = ("pending",)
        forge._update_letter_action_button_states(ready)
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertTrue(button.isHidden())
        forge._cancel_github_sign_in()
        for button in (
            forge.preview_btn,
            forge.publish_btn,
            forge.open_published_btn,
        ):
            self.assertFalse(button.isHidden())

        forge._project_fingerprint = "changed-fingerprint"
        forge._update_letter_action_button_states(ready)

        self.assertEqual(forge.publish_btn.text(), "Update Published Letter")
        self.assertTrue(forge.publish_btn.isEnabled())
        self.assertFalse(forge.publish_btn.is_invisible)

    def test_missing_wall_keeps_images_incomplete(self) -> None:
        built_in = self.root / "gallery/app/pages/Dmessage.png"
        built_in.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(
            QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32).save(str(built_in))
        )
        pages = self.root / USER_PAGES_DIR
        pages.mkdir(parents=True, exist_ok=True)

        image = ImageTab(self.root)
        self.addCleanup(image.deleteLater)
        self.assertIsNone(image.image_paths[3])
        self.assertFalse(image.cards[3].clear_btn.isEnabled())
        self.assertEqual(
            image.cards[3].clear_btn.semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertFalse(image.reset_btn.isEnabled())
        self.assertFalse(image.open_btn.isEnabled())

        for name in ("cover.png", "letter.png", "back.png"):
            self.assertTrue(
                QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32).save(
                    str(pages / name)
                )
            )

        message = MessageTab.__new__(MessageTab)
        message.project_root = str(self.root)
        self.assertFalse(MessageTab._ensure_wall_exists(message))
        self.assertEqual(MessageTab._render_wall_path(message), pages / "wall.png")
        self.assertFalse((pages / "wall.png").exists())
        generated: list[str] = []
        message._generate_image = generated.append
        MessageTab._ensure_message_exists(message)
        self.assertEqual(generated, [])
        self.assertFalse(message._png_path().exists())
        self.assertEqual(validate_required_images(self.root), ["wall.png"])
        self.assertNotIn(
            "images",
            evaluate_project_save_eligibility(self.root).completed_tabs,
        )

        fingerprint = build_source_fingerprint(self.root)
        self.assertTrue(
            QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32).save(str(built_in))
        )
        self.assertEqual(
            build_source_fingerprint(self.root),
            fingerprint,
        )

        runtime = self.root / "runtime-pages"
        with self.assertRaisesRegex(FileNotFoundError, "wall.png"):
            build_runtime_image_assets(pages, runtime)
        self.assertFalse((pages / "wall.png").exists())


if __name__ == "__main__":
    unittest.main()
