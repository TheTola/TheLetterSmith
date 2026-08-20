from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

import command


class CommandInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.command_tab = command.CommandTab(
            Path(self.temp_dir.name)
        )
        self.command_tab.open_command_bar_and_close_editor = lambda _data: True
        self.command_tab.resize(900, 600)
        self.command_tab.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.command_tab.close()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def _drain_events(self) -> None:
        for _ in range(3):
            self.app.processEvents(
                QtCore.QEventLoop.AllEvents,
                50,
            )

    def test_rapid_activation_runs_once_and_restores_idle(self) -> None:
        calls = []

        def succeed(*args, **kwargs):
            calls.append((args, kwargs))
            return True

        with mock.patch.object(
            command,
            "_perform_confirmed_reset",
            side_effect=succeed,
        ):
            self.command_tab._do_reset()
            dialog = self.command_tab._confirm_dialog
            self.command_tab._do_reset()

            self.assertIsNotNone(dialog)
            self.assertIs(self.command_tab._confirm_dialog, dialog)
            self.assertEqual(
                self.command_tab._interaction_state,
                "confirming",
            )
            self.assertFalse(self.command_tab.go_btn.isEnabled())

            dialog.accept()
            self._drain_events()

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())
        self.assertFalse(self.command_tab.go_btn._busy)
        self.assertEqual(
            self.command_tab.go_btn._activity_anim.state(),
            QtCore.QAbstractAnimation.Stopped,
        )
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_cancel_does_not_begin_reset(self) -> None:
        with (
            mock.patch.object(
                command,
                "_perform_confirmed_reset",
            ) as perform,
            mock.patch.object(command, "_toast"),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.reject()
            self._drain_events()

        perform.assert_not_called()
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())

    def test_failure_is_reported_and_restores_control(self) -> None:
        messages = []
        opener = mock.Mock(return_value=True)
        self.command_tab.open_command_bar_and_close_editor = opener

        def fail(*args, **kwargs):
            raise OSError("simulated read-only project")

        def record_toast(_parent, text, **kwargs):
            messages.append(text)

        with (
            mock.patch.object(
                command,
                "_perform_confirmed_reset",
                side_effect=fail,
            ),
            mock.patch.object(command.LOGGER, "exception"),
            mock.patch.object(
                command,
                "_toast",
                side_effect=record_toast,
            ),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.accept()
            self._drain_events()

        self.assertEqual(
            messages,
            ["Wipe failed: simulated read-only project"],
        )
        opener.assert_not_called()
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())
        self.assertFalse(self.command_tab.go_btn._busy)
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_reduced_motion_uses_static_busy_state(self) -> None:
        self.command_tab.go_btn._animations_enabled = False

        self.command_tab.go_btn.set_busy(True)
        self.assertEqual(
            self.command_tab.go_btn._activity_anim.state(),
            QtCore.QAbstractAnimation.Stopped,
        )
        self.assertGreater(
            self.command_tab.go_btn._opacity_effect.opacity(),
            0.7,
        )
        self.assertLess(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

        self.command_tab.go_btn.set_busy(False)
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_confirmed_reset_emits_wiped_once(self) -> None:
        wiped = []
        self.command_tab.wiped.connect(
            lambda: wiped.append(True)
        )

        with (
            mock.patch.object(
                command,
                "reset_everything",
                return_value=(3, 0),
            ),
            mock.patch.object(command, "_toast"),
        ):
            self.assertTrue(
                command._perform_confirmed_reset(
                    self.command_tab
                )
            )

        self.assertEqual(wiped, [True])

    def test_confirmed_command_uses_the_canonical_project_reset(self) -> None:
        order = []
        self.command_tab.reset_prompt_writer_state = mock.Mock(
            side_effect=AssertionError("legacy Prompt Writer reset path used")
        )

        def project_reset(*args, **kwargs):
            order.append("project")
            return True

        with mock.patch.object(
            command,
            "_perform_confirmed_reset",
            side_effect=project_reset,
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.accept()
            self._drain_events()

        self.assertEqual(order, ["project"])
        self.command_tab.reset_prompt_writer_state.assert_not_called()

    def test_capture_reset_and_handoff_run_in_order(self) -> None:
        order = []
        captured = command.CommandBarData(
            "Amanda Miller",
            "Words of Encouragement",
            Path(self.temp_dir.name) / "output" / "Play" / "letter" / "index.html",
            "https://example.com/letter",
        )

        def reset(*args, **kwargs):
            order.append("reset")
            return True

        def open_bar(data):
            order.append("handoff")
            self.assertIs(data, captured)
            return True

        self.command_tab.open_command_bar_and_close_editor = open_bar
        with (
            mock.patch.object(
                command,
                "build_command_bar_data",
                side_effect=lambda **_kwargs: order.append("capture") or captured,
            ),
            mock.patch.object(
                command,
                "_perform_confirmed_reset",
                side_effect=reset,
            ),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.accept()
            self._drain_events()

        self.assertEqual(order, ["capture", "reset", "handoff"])

    def test_missing_handoff_does_not_reset(self) -> None:
        self.command_tab.open_command_bar_and_close_editor = None
        messages = []
        with (
            mock.patch.object(command, "_perform_confirmed_reset") as reset,
            mock.patch.object(
                command,
                "_toast",
                side_effect=lambda _parent, text, **_kwargs: messages.append(text),
            ),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.accept()
            self._drain_events()

        reset.assert_not_called()
        self.assertEqual(
            messages,
            ["Wipe failed: Command Bar integration is unavailable"],
        )

    def test_new_project_preserves_libraries_saved_letters_and_preferences(self) -> None:
        from project_paths import ProjectPathResolver
        from project_state import ApplicationState
        from project_state import ProjectStateController
        from settings_store import SettingsStore

        root = Path(self.temp_dir.name) / "new-project"
        active_page = root / "gallery" / "user" / "pages" / "cover.png"
        active_message = root / "gallery" / "user" / "message" / "message.html"
        active_page.parent.mkdir(parents=True)
        active_message.parent.mkdir(parents=True)
        active_page.write_bytes(b"active page")
        active_message.write_text("<p>active message</p>", encoding="utf-8")
        prompt_state = root / "prompt_writer_state.json"
        prompt_state.write_text(
            json.dumps(
                {
                    "version": 1,
                    "subject": "active subject",
                    "generated_prompts": {"cover": "exact active prompt"},
                }
            ),
            encoding="utf-8",
        )
        sound_root = root / "gallery" / "user" / "sounds"
        app_sound_root = sound_root / "appssong"
        app_sound_root.mkdir(parents=True)
        (sound_root / "music.mp3").write_bytes(b"active music")
        (app_sound_root / "current.json").write_text("{}", encoding="utf-8")
        (app_sound_root / "project_sound.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "mode": "playlist",
                    "playlist": ["active-track"],
                    "selected_track_id": "active-track",
                }
            ),
            encoding="utf-8",
        )
        runtime_music = root / "gallery" / "sounds" / "music.mp3"
        runtime_music.parent.mkdir(parents=True)
        runtime_music.write_bytes(b"runtime music")

        protected_files = (
            root / "output" / "Play" / "Saved" / "Letter" / "index.html",
            root / "output" / "Recovery" / "Backup" / "index.html",
            root / "Prompter" / "modules" / "role.txt",
            root / "Prompter" / "modules" / "user_colors.json",
            root / "gallery" / "user" / "sounds" / "appssong" / "library.json",
        )
        for path in protected_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("protected", encoding="utf-8")

        settings = SettingsStore(root)
        settings.update_fields(
            {
                "starting_volume": 21,
                "music_volume": 34,
                "curtain_style": "average_color",
                "message_overlay_preset": "black",
                "message_overlay_opacity": 97,
                "forge_preview_mode": "window",
                "required_features": ["music"],
                "published_page_url": "https://example.com/letter/",
                "published_at": "2026-01-01T00:00:00+00:00",
                "published_expires_at": "2026-01-31T00:00:00+00:00",
                "published_public_path": "active-publication",
                "publication_provider": "cloudflare_r2",
                "publication_verified": True,
                "published_source_fingerprint": "active-source",
                "last_music_folder": "C:/Music",
                "prompt_writer_state": {"subject": "legacy active state"},
            }
        )
        controller = ProjectStateController(root)
        controller.initialize()
        controller.establish_project("Amanda Miller")
        settings.update_fields({"recipient_title": "Active Letter"})
        resolver = ProjectPathResolver(root)
        autosave = resolver.ensure_autosave_storage(
            resolver.context_from_settings(settings.snapshot())
        )
        (autosave / "draft.txt").write_text("temporary", encoding="utf-8")

        self.assertTrue(
            command.start_new_project(
                project_root=root,
                project_state=controller,
            )
        )

        self.assertFalse(active_page.exists())
        self.assertFalse(active_message.exists())
        self.assertEqual(list(active_page.parent.iterdir()), [])
        self.assertEqual(list(active_message.parent.iterdir()), [])
        self.assertFalse(autosave.exists())
        self.assertFalse((sound_root / "music.mp3").exists())
        self.assertFalse((app_sound_root / "current.json").exists())
        self.assertFalse(runtime_music.exists())
        sound_state = json.loads(
            (app_sound_root / "project_sound.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sound_state["playlist"], [])
        self.assertEqual(sound_state["single_track_id"], "")
        reset_prompt_state = json.loads(prompt_state.read_text(encoding="utf-8"))
        self.assertEqual(reset_prompt_state["generated_prompts"], {})
        self.assertEqual(reset_prompt_state["subject"], "")
        for path in protected_files:
            self.assertTrue(path.is_file(), path)
        current = settings.snapshot()
        self.assertEqual(current["starting_volume"], 21)
        self.assertEqual(current["music_volume"], 34)
        self.assertEqual(current["last_music_folder"], "C:/Music")
        self.assertEqual(current["curtain_style"], "pure_white")
        self.assertEqual(current["message_overlay_preset"], "paper")
        self.assertEqual(current["message_overlay_opacity"], 68)
        self.assertEqual(current["forge_preview_mode"], "portrait")
        self.assertEqual(current["required_features"], [])
        self.assertEqual(current["published_page_url"], "")
        self.assertEqual(current["published_at"], "")
        self.assertEqual(current["published_expires_at"], "")
        self.assertEqual(current["published_public_path"], "")
        self.assertEqual(current["publication_provider"], "")
        self.assertFalse(current["publication_verified"])
        self.assertEqual(current["published_source_fingerprint"], "")
        self.assertEqual(current["prompt_writer_state"], {})
        self.assertEqual(current["recipient_name"], "")
        self.assertEqual(controller.state, ApplicationState.RECIPIENT_REQUIRED)
        self.assertFalse(controller.identity.is_valid)
        self.assertEqual(
            list(root.rglob("*.new-project-backup")),
            [],
        )

    def test_new_project_rolls_back_every_active_path_on_failure(self) -> None:
        from project_paths import ProjectPathResolver
        from project_state import ApplicationState, ProjectStateController
        from settings_store import SettingsStore

        root = Path(self.temp_dir.name) / "new-project-rollback"
        page = root / "gallery" / "user" / "pages" / "cover.png"
        message = root / "gallery" / "user" / "message" / "message.html"
        page.parent.mkdir(parents=True)
        message.parent.mkdir(parents=True)
        page.write_bytes(b"cover before failure")
        message.write_text("<p>message before failure</p>", encoding="utf-8")
        prompt = root / "prompt_writer_state.json"
        prompt.write_text(
            json.dumps({"subject": "preserve me"}),
            encoding="utf-8",
        )

        settings = SettingsStore(root)
        controller = ProjectStateController(root)
        controller.initialize()
        identity = controller.establish_project("Amanda Miller")
        settings.update_fields({"recipient_title": "Keep This Letter"})
        resolver = ProjectPathResolver(root)
        autosave = resolver.ensure_autosave_storage(
            resolver.context_from_settings(settings.snapshot())
        )
        (autosave / "draft.txt").write_text("keep", encoding="utf-8")
        settings_before = settings.snapshot()

        original_commit = command.PathTransaction.commit
        commits = 0

        def fail_second_commit(transaction, *args, **kwargs):
            nonlocal commits
            commits += 1
            if commits == 2:
                raise OSError("simulated locked project path")
            return original_commit(transaction, *args, **kwargs)

        with mock.patch.object(
            command.PathTransaction,
            "commit",
            new=fail_second_commit,
        ):
            with self.assertRaisesRegex(OSError, "locked project path"):
                command.start_new_project(
                    project_root=root,
                    project_state=controller,
                )

        self.assertEqual(page.read_bytes(), b"cover before failure")
        self.assertEqual(
            message.read_text(encoding="utf-8"),
            "<p>message before failure</p>",
        )
        self.assertEqual(
            json.loads(prompt.read_text(encoding="utf-8"))["subject"],
            "preserve me",
        )
        self.assertTrue((autosave / "draft.txt").is_file())
        self.assertEqual(settings.snapshot(), settings_before)
        self.assertEqual(controller.state, ApplicationState.PROJECT_READY)
        self.assertEqual(controller.identity, identity)
        self.assertEqual(
            [
                path
                for path in root.rglob("*")
                if ".new-project-" in path.name
            ],
            [],
        )

    def test_settings_menu_exposes_new_project_and_normal_exit(self) -> None:
        from Nexus import TitleBar

        class Host(QtWidgets.QMainWindow):
            def __init__(self, project_root: Path):
                super().__init__()
                self.project_root = str(project_root)
                self.new_project_calls = 0
                self.close_events = 0

            def start_new_project(self) -> None:
                self.new_project_calls += 1

            def open_target_browser(self) -> None:
                pass

            def status(self, _message: str) -> None:
                pass

            def closeEvent(self, event) -> None:
                self.close_events += 1
                event.accept()

        host = Host(Path(self.temp_dir.name))
        source_icons = Path(__file__).resolve().parents[1] / "gallery/app/icons"
        target_icons = Path(host.project_root) / "gallery/app/icons"
        target_icons.mkdir(parents=True)
        for name in (
            "settings.png",
            "Settings.gif",
            "reticle.png",
            "mini.png",
            "maxi.png",
            "Exi.png",
        ):
            shutil.copy2(source_icons / name, target_icons)
        title_bar = TitleBar(host)
        actions = {action.text(): action for action in title_bar.settings_menu.actions()}

        self.assertIn("New Project", actions)
        self.assertIn("Exit", actions)
        self.assertEqual(title_bar.settings_button.text(), "")
        self.assertFalse(title_bar.settings_button.icon().isNull())
        self.assertTrue(Path(title_bar._settings_movie_path).is_file())
        self.assertFalse(title_bar._settings_icon_animated)
        from Nexus import TITLE_BAR_CONTROL_PX, TITLE_BAR_ICON_PX

        for button in (
            title_bar.settings_button,
            title_bar.btn_target,
            title_bar.btn_minimize,
            title_bar.btn_max,
            title_bar.btn_close,
        ):
            self.assertEqual(
                button.size(),
                QtCore.QSize(TITLE_BAR_CONTROL_PX, TITLE_BAR_CONTROL_PX),
            )
            self.assertEqual(
                button.iconSize(),
                QtCore.QSize(TITLE_BAR_ICON_PX, TITLE_BAR_ICON_PX),
            )
        for button in (
            title_bar.btn_minimize,
            title_bar.btn_max,
            title_bar.btn_close,
        ):
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())

        title_bar._show_animated_settings_icon()
        animated_after_click = title_bar._settings_icon_animated
        movie_state_after_click = title_bar._settings_movie.state().name
        title_bar._show_static_settings_icon()
        self.assertTrue(animated_after_click)
        self.assertEqual(movie_state_after_click, "Running")
        QtWidgets.QApplication.sendEvent(
            title_bar.settings_button,
            QtCore.QEvent(QtCore.QEvent.Enter),
        )
        animated_after_hover = title_bar._settings_icon_animated
        QtWidgets.QApplication.sendEvent(
            title_bar.settings_button,
            QtCore.QEvent(QtCore.QEvent.Leave),
        )
        self.assertTrue(animated_after_hover)
        self.assertFalse(title_bar._settings_icon_animated)
        self.assertEqual(title_bar._settings_movie.fileName(), "")
        title_bar._settings_menu_shown()
        title_bar._show_static_settings_icon()
        title_bar._sync_settings_icon_state()
        animated_while_open = title_bar._settings_icon_animated
        title_bar._settings_menu_open = False
        title_bar._show_static_settings_icon()
        self.assertTrue(animated_while_open)
        self.assertFalse(title_bar._settings_icon_animated)
        from settings_store import SettingsStore

        SettingsStore(host.project_root).update_fields(
            curtain_style="average_color"
        )
        title_bar._sync_curtain_menu()
        title_bar.set_curtain_preview_colors(
            {
                "average_color": (12, 34, 56),
                "complementary_average_color": (90, 110, 130),
                "normal_light": (180, 190, 200),
                "complementary_light": (205, 210, 215),
                "normal_dark": (25, 35, 45),
                "complementary_dark": (65, 75, 85),
            }
        )
        normal_row = title_bar._curtain_actions["average_color"]
        complementary_row = title_bar._curtain_actions[
            "complementary_average_color"
        ]
        white_row = title_bar._curtain_actions["pure_white"]
        light_parent = title_bar._curtain_parent_actions["light"]
        dark_parent = title_bar._curtain_parent_actions["dark"]
        self.assertIsInstance(normal_row, QtWidgets.QPushButton)
        self.assertIn("background:#0c2238", normal_row.styleSheet())
        self.assertIn("color:#5a6e82", normal_row.styleSheet())
        self.assertIn("background:#101820", complementary_row.styleSheet())
        self.assertIn("background:#101820", white_row.styleSheet())
        self.assertEqual(light_parent.text(), "Light ▾")
        self.assertEqual(dark_parent.text(), "Dark ▾")
        self.assertIs(light_parent.menu(), title_bar.light_curtain_menu)
        self.assertIs(dark_parent.menu(), title_bar.dark_curtain_menu)
        self.assertEqual(len(title_bar.curtain_menu.actions()), 5)
        self.assertEqual(len(title_bar.light_curtain_menu.actions()), 2)
        self.assertEqual(len(title_bar.dark_curtain_menu.actions()), 2)
        self.assertIn("background:#101820", light_parent.styleSheet())

        title_bar._set_curtain_style("complementary_average_color")
        self.assertIn("background:#5a6e82", complementary_row.styleSheet())
        self.assertIn("color:#0c2238", complementary_row.styleSheet())
        self.assertIn("background:#101820", normal_row.styleSheet())

        title_bar._set_curtain_style("normal_light")
        normal_light_row = title_bar._curtain_actions["normal_light"]
        complementary_light_row = title_bar._curtain_actions[
            "complementary_light"
        ]
        self.assertIn("background:#b4bec8", normal_light_row.styleSheet())
        self.assertIn("color:#414b55", normal_light_row.styleSheet())
        self.assertIn("background:#b4bec8", light_parent.styleSheet())
        self.assertIn("color:#414b55", light_parent.styleSheet())
        self.assertIn(
            "background:#101820",
            complementary_light_row.styleSheet(),
        )
        self.assertIn("background:#101820", dark_parent.styleSheet())

        title_bar._set_curtain_style("complementary_light")
        self.assertIn(
            "background:#cdd2d7",
            complementary_light_row.styleSheet(),
        )
        self.assertIn("color:#19232d", complementary_light_row.styleSheet())
        self.assertIn("background:#cdd2d7", light_parent.styleSheet())

        title_bar._set_curtain_style("normal_dark")
        normal_dark_row = title_bar._curtain_actions["normal_dark"]
        self.assertIn("background:#19232d", normal_dark_row.styleSheet())
        self.assertIn("color:#cdd2d7", normal_dark_row.styleSheet())
        self.assertIn("background:#19232d", dark_parent.styleSheet())
        self.assertIn("background:#101820", light_parent.styleSheet())

        title_bar._set_curtain_style("complementary_dark")
        complementary_dark_row = title_bar._curtain_actions[
            "complementary_dark"
        ]
        self.assertIn("background:#414b55", complementary_dark_row.styleSheet())
        self.assertIn("color:#b4bec8", complementary_dark_row.styleSheet())
        self.assertIn("background:#414b55", dark_parent.styleSheet())
        self.assertIn("background:#101820", light_parent.styleSheet())
        self.assertIn("background:#101820", normal_light_row.styleSheet())

        title_bar._set_curtain_style("pure_white")
        self.assertIn("background:#101820", normal_row.styleSheet())
        self.assertIn("background:#ffffff", white_row.styleSheet())
        self.assertIn("color:#000000", white_row.styleSheet())
        actions["New Project"].trigger()
        self.assertEqual(host.new_project_calls, 1)
        host.show()
        actions["Exit"].trigger()
        self.app.processEvents()
        self.assertEqual(host.close_events, 1)

    def test_new_project_refreshes_every_live_project_surface(self) -> None:
        from Nexus import Nexus

        tabbar = QtWidgets.QTabBar()
        tabbar.addTab("Images")
        tabbar.addTab("Sound")
        tabbar.setCurrentIndex(1)
        page_stack = QtWidgets.QStackedWidget()
        page_stack.addWidget(QtWidgets.QWidget())
        page_stack.addWidget(QtWidgets.QWidget())
        page_stack.setCurrentIndex(1)
        preview_stack = QtWidgets.QStackedWidget()
        preview_stack.addWidget(QtWidgets.QWidget())
        preview_stack.setCurrentIndex(0)

        image_tab = types.SimpleNamespace(
            reset_project_images=mock.Mock(),
        )
        sound_tab = types.SimpleNamespace(
            reset_project_sound=mock.Mock(),
        )
        message_tab = types.SimpleNamespace(
            reset_project_message=mock.Mock(),
        )
        preview_format_panel = QtWidgets.QWidget()
        preview_format_panel.show()
        forge_tab = types.SimpleNamespace(
            reset_after_project_wipe=mock.Mock(),
            preview_format_panel=preview_format_panel,
        )
        title_bar = types.SimpleNamespace(
            _sync_curtain_menu=mock.Mock(),
            set_curtain_preview_colors=mock.Mock(),
        )
        harness = types.SimpleNamespace(
            _curtain_preparation_generation=1,
            _stop_curtain_preparation=mock.Mock(return_value=True),
            _prompt_writer_win=None,
            _project_tabs_initialized=True,
            image_tab=image_tab,
            sound_tab=sound_tab,
            message_tab=message_tab,
            forge_tab=forge_tab,
            _release_forge_preview_files=mock.Mock(),
            _set_command_immersive=mock.Mock(),
            tabbar=tabbar,
            page_stack=page_stack,
            _detach_sound_preview=mock.Mock(),
            _last_pixmap=object(),
            _clear_preview=mock.Mock(),
            preview_stack=preview_stack,
            preview_frame=QtWidgets.QWidget(),
            preview_caption=QtWidgets.QLabel(),
            help_icon=QtWidgets.QLabel(),
            title_bar=title_bar,
        )

        Nexus._on_command_wiped(harness)

        image_tab.reset_project_images.assert_called_once_with()
        sound_tab.reset_project_sound.assert_called_once_with()
        message_tab.reset_project_message.assert_called_once_with()
        forge_tab.reset_after_project_wipe.assert_called_once_with()
        self.assertEqual(tabbar.currentIndex(), 0)
        self.assertEqual(page_stack.currentIndex(), 0)
        self.assertIsNone(harness._last_pixmap)
        self.assertFalse(preview_format_panel.isVisible())
        title_bar.set_curtain_preview_colors.assert_called_once_with({})


if __name__ == "__main__":
    unittest.main()
