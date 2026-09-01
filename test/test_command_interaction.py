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

from PySide6 import QtCore, QtGui, QtWidgets

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
        from settings_store import DEFAULT_CURTAIN_STYLE, SettingsStore

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
                "curtain_style": "complementary_dark",
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
        self.assertEqual(
            current["curtain_style"],
            DEFAULT_CURTAIN_STYLE,
        )
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

    def test_command_reset_restores_normal_curtains(self) -> None:
        from project_state import ProjectStateController
        from settings_store import DEFAULT_CURTAIN_STYLE, SettingsStore

        root = Path(self.temp_dir.name) / "command-reset-curtain"
        settings = SettingsStore(root)
        settings.update_fields(curtain_style="pure_white")
        controller = ProjectStateController(root)
        controller.initialize()
        controller.establish_project("Amanda Miller")

        command.reset_everything(
            project_root=root,
            project_state=controller,
        )

        self.assertEqual(
            settings.snapshot()["curtain_style"],
            DEFAULT_CURTAIN_STYLE,
        )

    def test_delete_project_removes_all_active_local_copies_only(self) -> None:
        from project_paths import ProjectPathResolver
        from project_state import ApplicationState, ProjectStateController
        from settings_store import SettingsStore

        root = Path(self.temp_dir.name) / "delete-project"
        page = root / "gallery" / "user" / "pages" / "cover.png"
        page.parent.mkdir(parents=True)
        page.write_bytes(b"active")
        shared = root / "Prompter" / "modules" / "role.txt"
        shared.parent.mkdir(parents=True)
        shared.write_text("keep", encoding="utf-8")

        settings = SettingsStore(root)
        controller = ProjectStateController(root)
        controller.initialize()
        identity = controller.establish_project("Amanda Miller")
        settings.update_fields({"recipient_title": "Temporary Letter"})
        resolver = ProjectPathResolver(root)
        autosave = resolver.ensure_autosave_storage(
            resolver.context_from_settings(settings.snapshot())
        )
        (autosave / "draft.txt").write_text("delete", encoding="utf-8")

        saved = root / "output" / "Play" / "Amanda Miller" / "Temporary Letter"
        recovery = root / "output" / "Recovery" / "Amanda Miller" / "Temporary Letter"
        unrelated = root / "output" / "Play" / "Amanda Miller" / "Keep Letter"
        for path in (saved, recovery, unrelated):
            path.mkdir(parents=True)
            (path / "index.html").write_text(path.name, encoding="utf-8")
            (path / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "project_id": (
                            identity.project_id
                            if path != unrelated
                            else "fdb11045-b56b-4778-b1dd-c96b8f34bba5"
                        )
                    }
                ),
                encoding="utf-8",
            )

        catalog = mock.Mock()
        with mock.patch(
            "saved_letters.SavedLetterCatalog",
            return_value=catalog,
        ):
            self.assertTrue(
                command.delete_project(
                    project_root=root,
                    project_state=controller,
                )
            )

        self.assertFalse(page.exists())
        self.assertFalse(autosave.exists())
        self.assertFalse(saved.exists())
        self.assertFalse(recovery.exists())
        self.assertTrue(unrelated.is_dir())
        self.assertEqual(shared.read_text(encoding="utf-8"), "keep")
        self.assertEqual(controller.state, ApplicationState.RECIPIENT_REQUIRED)
        self.assertFalse(controller.identity.is_valid)
        self.assertEqual(settings.get("project_id"), "")
        self.assertEqual(catalog.refresh_entry.call_count, 2)
        catalog.refresh_entry.assert_has_calls(
            [mock.call(recovery.resolve()), mock.call(saved.resolve())],
            any_order=True,
        )
        self.assertTrue(identity.is_valid)

    def test_saved_project_paths_match_only_the_active_project_id(self) -> None:
        from project_paths import PROJECT_METADATA_FILE

        root = Path(self.temp_dir.name) / "saved-project-paths"
        active_play = root / "output" / "Play" / "Recipient" / "Active"
        active_recovery = root / "output" / "Recovery" / "Active"
        other = root / "output" / "Play" / "Recipient" / "Other"
        example = root / "resources" / "examples" / "Example"
        active_id = "8e0a0fb8-fe43-46e7-b81a-12458235660b"
        other_id = "79e08d8b-89af-41e4-a638-dc88f2074aa4"
        for path, project_id in (
            (active_play, active_id),
            (active_recovery, active_id),
            (other, other_id),
            (example, active_id),
        ):
            path.mkdir(parents=True)
            (path / PROJECT_METADATA_FILE).write_text(
                json.dumps({"project_id": project_id}),
                encoding="utf-8",
            )

        resolved_catalog, paths = command._saved_project_paths(
            root,
            {"project_id": active_id},
        )

        self.assertIsNotNone(resolved_catalog)
        self.assertEqual(
            paths,
            (active_recovery.resolve(), active_play.resolve()),
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
        from Nexus import TITLE_BAR_CONTROL_PX, TITLE_BAR_ICON_PX, TitleBar

        class Host(QtWidgets.QMainWindow):
            def __init__(self, project_root: Path):
                super().__init__()
                self.project_root = str(project_root)
                self.new_project_calls = 0
                self.delete_project_calls = 0
                self.close_events = 0

            def start_new_project(self) -> None:
                self.new_project_calls += 1

            def delete_project(self) -> None:
                self.delete_project_calls += 1

            def status(self, _message: str) -> None:
                pass

            def closeEvent(self, event) -> None:
                self.close_events += 1
                event.accept()

        host = Host(Path(self.temp_dir.name))
        source_theme = (
            Path(__file__).resolve().parents[1]
            / "resources/app/themes/cyber_forge"
        )
        target_theme = (
            Path(host.project_root)
            / "resources/app/themes/cyber_forge"
        )
        shutil.copytree(source_theme, target_theme)
        source_settings = (
            Path(__file__).resolve().parents[1]
            / "gallery/app/settings"
        )
        target_settings = Path(host.project_root) / "gallery/app/settings"
        shutil.copytree(source_settings, target_settings)
        title_bar = TitleBar(host)
        actions = {action.text(): action for action in title_bar.settings_menu.actions()}

        self.assertIn("App Theme", actions)
        self.assertIn("New Project", actions)
        self.assertIn("Delete Project", actions)
        self.assertIn("About Letter Smith…", actions)
        self.assertIn("Exit", actions)
        project_actions = [
            action.text()
            for action in title_bar.settings_menu.actions()
            if not action.isSeparator()
        ]
        self.assertEqual(
            project_actions[project_actions.index("New Project") + 1],
            "Delete Project",
        )
        self.assertEqual(
            project_actions[project_actions.index("Delete Project") + 1],
            "About Letter Smith…",
        )
        theme_entries = [
            None if action.isSeparator() else action.text()
            for action in title_bar.themes_menu.actions()
        ]
        self.assertEqual(
            theme_entries,
            [
                "Cyber Forge",
                "Obsidian Forge",
                "Velvet Rose",
                "Celestial Rose",
                None,
                "Dark",
                "Light",
            ],
        )
        self.assertFalse(hasattr(title_bar, "btn_target"))
        self.assertEqual(title_bar.settings_window_divider.width(), 1)
        self.assertEqual(
            title_bar.settings_window_divider.height(),
            (TITLE_BAR_CONTROL_PX * 3) // 5,
        )
        divider_container = title_bar.settings_window_divider.parentWidget()
        self.assertEqual(
            ((divider_container.width() - 1) // 2)
            + title_bar.layout().spacing(),
            TITLE_BAR_CONTROL_PX // 2,
        )
        self.assertEqual(title_bar.settings_button.text(), "")
        self.assertFalse(title_bar.settings_button.icon().isNull())
        shared_movie_path = (target_settings / "Settings.gif").resolve()
        shared_icon = QtGui.QIcon(str(target_settings / "settings.png"))
        shared_icon_image = shared_icon.pixmap(
            title_bar.settings_button.iconSize()
        ).toImage()
        self.assertEqual(
            Path(title_bar._settings_movie_path).resolve(),
            shared_movie_path,
        )
        self.assertFalse(title_bar._settings_icon_animated)
        for button in (
            title_bar.settings_button,
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

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
            "dark",
            "light",
        ):
            title_bar.theme_service.set_theme(theme_id, persist=False)
            title_bar.apply_theme(title_bar.theme_service)
            with self.subTest(theme_id=theme_id):
                self.assertEqual(title_bar.settings_button.text(), "")
                self.assertFalse(title_bar.settings_button.icon().isNull())
                self.assertEqual(
                    Path(title_bar._settings_movie_path).resolve(),
                    shared_movie_path,
                )
                self.assertEqual(
                    title_bar._settings_static_icon.pixmap(
                        title_bar.settings_button.iconSize()
                    ).toImage(),
                    shared_icon_image,
                )
        for button in (
            title_bar.btn_minimize,
            title_bar.btn_max,
            title_bar.btn_close,
        ):
            self.assertFalse(button.text() == "")
            self.assertTrue(button.icon().isNull())

        title_bar.theme_service.set_theme("cyber_forge", persist=False)
        title_bar.apply_theme(title_bar.theme_service)

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
        from curtain_color import curtain_display_palette
        from curtain_controls import (
            CURTAIN_BACKGROUND_ROLE,
            CURTAIN_FOREGROUND_ROLE,
            CURTAIN_STYLE_ROLE,
            CurtainStyleComboBox,
            CurtainStyleMenuSelector,
        )
        from Forge_Tab import ForgeTab
        from settings_store import CURTAIN_STYLE_OPTIONS

        curtain_styles = title_bar.curtain_styles
        curtain_styles.set_style("average_color")
        forge = ForgeTab(
            host.project_root,
            curtain_styles=curtain_styles,
        )
        settings_selector = title_bar.curtain_style_selector
        forge_selector = forge.curtain_style_selector
        self.assertEqual(forge.preview_format_label.text(), "Preview Format")
        self.assertEqual(forge.curtain_style_label.text(), "Choose Curtains")
        self.assertIs(forge.curtain_styles, curtain_styles)
        self.assertIsInstance(settings_selector, CurtainStyleMenuSelector)
        self.assertIsInstance(forge_selector, CurtainStyleComboBox)
        self.assertIs(settings_selector.model(), curtain_styles.model)
        self.assertIs(forge_selector.model(), curtain_styles.model)

        expected_options = (
            ("pure_white", "White Curtains"),
            ("average_color", "Normal Curtains"),
            ("complementary_average_color", "Complementary Curtains"),
            ("normal_light", "Normal Light Curtains"),
            ("complementary_light", "Complementary Light Curtains"),
            ("normal_dark", "Normal Dark Curtains"),
            ("complementary_dark", "Complementary Dark Curtains"),
        )
        self.assertEqual(CURTAIN_STYLE_OPTIONS, expected_options)
        self.assertEqual(curtain_styles.model.rowCount(), 7)
        for row, (style, label) in enumerate(expected_options):
            index = curtain_styles.model.index(row, 0)
            self.assertEqual(index.data(), label)
            self.assertEqual(index.data(CURTAIN_STYLE_ROLE), style)

        settings_curtain_menu = settings_selector.menu()
        forge_curtain_menu = forge_selector.popup_menu()

        def menu_signature(menu: QtWidgets.QMenu) -> list[object]:
            return [
                None
                if action.isSeparator()
                else (action.text(), action.menu() is not None)
                for action in menu.actions()
            ]

        expected_hierarchy = [
            ("White Curtains", False),
            ("Normal Curtains", False),
            ("Complementary Curtains", False),
            None,
            ("Light Curtains", True),
            ("Dark Curtains", True),
        ]
        for selector, menu in (
            (settings_selector, settings_curtain_menu),
            (forge_selector, forge_curtain_menu),
        ):
            self.assertEqual(selector.count(), 7)
            self.assertEqual(selector.current_style(), "average_color")
            self.assertEqual(
                selector.currentData(CURTAIN_STYLE_ROLE),
                "average_color",
            )
            self.assertEqual(menu_signature(menu), expected_hierarchy)
            self.assertEqual(
                [
                    (action.data(), action.text())
                    for action in menu.light_menu.actions()
                ],
                list(expected_options[3:5]),
            )
            self.assertEqual(
                [
                    (action.data(), action.text())
                    for action in menu.dark_menu.actions()
                ],
                list(expected_options[5:]),
            )
            for style, label in expected_options:
                action = menu.leaf_action(style)
                widget = menu.leaf_widget(style)
                self.assertIsNotNone(action)
                self.assertIsNotNone(widget)
                self.assertEqual(action.data(), style)
                self.assertEqual(action.text(), label)
                self.assertIs(action.defaultWidget(), widget)
                self.assertEqual(widget.curtain_style, style)
                self.assertEqual(widget.accessibleName(), label)

        self.assertIsNot(settings_curtain_menu, forge_curtain_menu)
        self.assertIsNot(
            settings_curtain_menu.light_menu,
            forge_curtain_menu.light_menu,
        )
        self.assertIsNot(
            settings_curtain_menu.dark_menu,
            forge_curtain_menu.dark_menu,
        )
        for style, _label in expected_options:
            self.assertIsNot(
                settings_curtain_menu.leaf_action(style),
                forge_curtain_menu.leaf_action(style),
            )
            self.assertIsNot(
                settings_curtain_menu.leaf_widget(style),
                forge_curtain_menu.leaf_widget(style),
            )

        curtain_colors = {
            "pure_white": (255, 255, 255),
            "average_color": (12, 34, 56),
            "complementary_average_color": (90, 110, 130),
            "normal_light": (180, 190, 200),
            "complementary_light": (200, 180, 160),
            "normal_dark": (25, 35, 45),
            "complementary_dark": (60, 25, 80),
        }
        curtain_styles.set_preview_colors(curtain_colors)
        expected_palette = curtain_display_palette(curtain_colors)

        def model_palette(
            expected: dict[str, object],
        ) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
            rows = []
            for row, (style, _label) in enumerate(expected_options):
                index = curtain_styles.model.index(row, 0)
                background = index.data(CURTAIN_BACKGROUND_ROLE)
                foreground = index.data(CURTAIN_FOREGROUND_ROLE)
                self.assertTrue(background.isValid())
                self.assertTrue(foreground.isValid())
                self.assertEqual(
                    background.getRgb()[:3],
                    expected[style].background,
                )
                self.assertEqual(
                    foreground.getRgb()[:3],
                    expected[style].foreground,
                )
                rows.append((background.getRgb(), foreground.getRgb()))
            return tuple(rows)

        model_palette(expected_palette)

        def exact_color_pixels(
            widget: QtWidgets.QWidget,
            color: tuple[int, int, int],
        ) -> int:
            widget.resize(widget.sizeHint())
            image = widget.grab().toImage()
            return sum(
                1
                for y in range(image.height())
                for x in range(image.width())
                if image.pixelColor(x, y).getRgb()[:3] == color
            )

        for menu in (settings_curtain_menu, forge_curtain_menu):
            for style, _label in expected_options:
                self.assertGreater(
                    exact_color_pixels(
                        menu.leaf_widget(style),
                        expected_palette[style].background,
                    ),
                    100,
                )

        self.assertEqual(
            settings_selector.current_display_colors(),
            expected_palette["average_color"],
        )
        self.assertEqual(
            forge_selector.current_display_colors(),
            expected_palette["average_color"],
        )
        committed: list[str] = []
        curtain_styles.styleCommitted.connect(committed.append)
        with (
            mock.patch.object(
                curtain_styles.settings,
                "update_fields",
                wraps=curtain_styles.settings.update_fields,
            ) as update_fields,
            mock.patch.object(
                forge,
                "_refresh_source_fingerprint",
                return_value=True,
            ),
        ):
            forge_curtain_menu.leaf_widget("complementary_light").click()
            self.app.processEvents()
            self.assertEqual(
                settings_selector.current_style(),
                "complementary_light",
            )
            self.assertEqual(
                forge_selector.current_style(),
                "complementary_light",
            )

            settings_curtain_menu.leaf_widget(
                "complementary_dark"
            ).click()
            self.app.processEvents()
            self.assertEqual(
                settings_selector.current_style(),
                "complementary_dark",
            )
            self.assertEqual(
                forge_selector.current_style(),
                "complementary_dark",
            )

        self.assertEqual(
            update_fields.call_args_list,
            [
                mock.call(curtain_style="complementary_light"),
                mock.call(curtain_style="complementary_dark"),
            ],
        )
        self.assertEqual(
            committed,
            ["complementary_light", "complementary_dark"],
        )

        refreshed_colors = {
            "pure_white": (255, 255, 255),
            "average_color": (42, 82, 122),
            "complementary_average_color": (174, 134, 94),
            "normal_light": (192, 212, 232),
            "complementary_light": (232, 212, 192),
            "normal_dark": (18, 38, 58),
            "complementary_dark": (58, 38, 18),
        }
        popup_was_visible: list[bool] = []

        def refresh_popup_colors() -> None:
            popup_was_visible.append(forge_curtain_menu.isVisible())
            curtain_styles.set_preview_colors(refreshed_colors)

        curtain_styles.previewColorsRequested.connect(refresh_popup_colors)
        forge_curtain_menu.aboutToShow.emit()
        self.assertEqual(popup_was_visible, [False])
        normal_index = curtain_styles.model.index(1, 0)
        self.assertEqual(
            normal_index.data(CURTAIN_BACKGROUND_ROLE).getRgb()[:3],
            refreshed_colors["average_color"],
        )
        refreshed_palette = curtain_display_palette(refreshed_colors)
        self.assertEqual(
            settings_selector.current_display_colors(),
            refreshed_palette["complementary_dark"],
        )
        self.assertEqual(
            forge_selector.current_display_colors(),
            refreshed_palette["complementary_dark"],
        )

        palette_before_theme = model_palette(refreshed_palette)
        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            with self.subTest(theme_id=theme_id):
                definition = title_bar.theme_service.set_theme(
                    theme_id,
                    persist=False,
                )
                title_bar.apply_theme(title_bar.theme_service)
                forge.apply_theme_assets(title_bar.theme_service)
                self.assertEqual(
                    model_palette(refreshed_palette),
                    palette_before_theme,
                )
                self.assertIn(
                    definition.tokens.border,
                    settings_curtain_menu.styleSheet(),
                )
                self.assertIn(
                    definition.tokens.accent,
                    forge_selector.styleSheet(),
                )
                selected = refreshed_palette["complementary_dark"]
                self.assertIn(
                    QtGui.QColor(*selected.background).name(),
                    forge_selector.styleSheet(),
                )
                self.assertIn(
                    QtGui.QColor(*selected.foreground).name(),
                    forge_selector.styleSheet(),
                )

        selected_background = refreshed_colors["complementary_dark"]
        selected_image = forge_selector.grab().toImage()
        selected_pixel_count = sum(
            1
            for y in range(selected_image.height())
            for x in range(selected_image.width())
            if selected_image.pixelColor(x, y).getRgb()[:3]
            == selected_background
        )
        self.assertGreater(selected_pixel_count, 100)

        forge.shutdown_operations()
        forge.close()
        with mock.patch.object(
            curtain_styles.settings,
            "update_fields",
            wraps=curtain_styles.settings.update_fields,
        ) as update_after_forge_close:
            curtain_styles.set_style("pure_white")
            self.app.processEvents()
        update_after_forge_close.assert_called_once_with(
            curtain_style="pure_white"
        )
        self.assertEqual(settings_selector.current_style(), "pure_white")

        curtain_styles.close()
        with mock.patch.object(
            curtain_styles.settings,
            "update_fields",
            wraps=curtain_styles.settings.update_fields,
        ) as update_after_close:
            settings_curtain_menu.leaf_widget("normal_light").click()
            self.app.processEvents()
        update_after_close.assert_not_called()

        actions["New Project"].trigger()
        self.assertEqual(host.new_project_calls, 1)
        actions["Delete Project"].trigger()
        self.assertEqual(host.delete_project_calls, 1)
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
        curtain_styles = mock.Mock()
        curtain_styles.sync_from_settings.return_value = "average_color"
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
            curtain_styles=curtain_styles,
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
        curtain_styles.sync_from_settings.assert_called_once_with()
        curtain_styles.set_preview_colors.assert_called_once_with({})

    def test_curtain_setting_invalidates_source_before_preview(self) -> None:
        from Forge_Tab import ForgeTab

        calls: list[str] = []
        harness = types.SimpleNamespace(
            _sync_curtain_style=mock.Mock(
                side_effect=lambda: calls.append("sync")
            ),
            _refresh_source_fingerprint=mock.Mock(
                side_effect=lambda: calls.append("source")
            ),
            ensure_preview_current=mock.Mock(
                side_effect=lambda: calls.append("preview")
            ),
            _tab_active=True,
            _shutdown=False,
            _settings_refresh_requested=types.SimpleNamespace(
                emit=mock.Mock(side_effect=lambda: calls.append("scheduled"))
            ),
        )
        harness._curtain_style_refresh_requested = types.SimpleNamespace(
            emit=mock.Mock(
                side_effect=lambda: ForgeTab._curtain_style_changed(harness)
            )
        )

        ForgeTab._on_settings_changed(
            harness,
            {"curtain_style": "normal_dark"},
            ("curtain_style",),
        )

        self.assertEqual(calls, ["sync", "source", "preview", "scheduled"])
        harness._sync_curtain_style.assert_called_once_with()
        harness._refresh_source_fingerprint.assert_called_once_with()
        harness.ensure_preview_current.assert_called_once_with()

    def test_frameless_resize_hit_regions_cover_edges_and_corners(self) -> None:
        from window_chrome import native_resize_hit_test, resize_edges_at_point

        size = QtCore.QSize(640, 480)
        self.assertEqual(
            resize_edges_at_point(size, QtCore.QPoint(0, 0)),
            QtCore.Qt.TopEdge | QtCore.Qt.LeftEdge,
        )
        self.assertEqual(
            resize_edges_at_point(size, QtCore.QPoint(639, 0)),
            QtCore.Qt.TopEdge | QtCore.Qt.RightEdge,
        )
        self.assertEqual(
            resize_edges_at_point(size, QtCore.QPoint(0, 479)),
            QtCore.Qt.BottomEdge | QtCore.Qt.LeftEdge,
        )
        self.assertEqual(
            resize_edges_at_point(size, QtCore.QPoint(639, 479)),
            QtCore.Qt.BottomEdge | QtCore.Qt.RightEdge,
        )
        self.assertEqual(
            resize_edges_at_point(size, QtCore.QPoint(320, 0)),
            QtCore.Qt.TopEdge,
        )
        self.assertFalse(
            resize_edges_at_point(size, QtCore.QPoint(320, 240))
        )
        expected_native_hits = {
            QtCore.Qt.LeftEdge: 10,
            QtCore.Qt.RightEdge: 11,
            QtCore.Qt.TopEdge: 12,
            QtCore.Qt.TopEdge | QtCore.Qt.LeftEdge: 13,
            QtCore.Qt.TopEdge | QtCore.Qt.RightEdge: 14,
            QtCore.Qt.BottomEdge: 15,
            QtCore.Qt.BottomEdge | QtCore.Qt.LeftEdge: 16,
            QtCore.Qt.BottomEdge | QtCore.Qt.RightEdge: 17,
        }
        for edges, expected in expected_native_hits.items():
            with self.subTest(edges=edges):
                self.assertEqual(native_resize_hit_test(edges), expected)


if __name__ == "__main__":
    unittest.main()
