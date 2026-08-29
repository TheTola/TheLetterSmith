from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from audio_tools import audio_tool_filename
from project_paths import ApplicationPaths, application_paths
from transactional_io import set_path_hidden


class ApplicationPathsTests(unittest.TestCase):
    def test_runtime_layout_separates_resources_app_data_and_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            resource_root = base / "bundle"
            local_app_data = base / "local"
            home = base / "profile"
            temporary = base / "temporary"

            paths = ApplicationPaths.for_runtime(
                resource_root,
                environ={
                    "LOCALAPPDATA": str(local_app_data),
                    "USERPROFILE": str(home),
                },
                home=home,
                temporary_base=temporary,
            )

            self.assertEqual(paths.resource_root, resource_root.resolve())
            self.assertEqual(
                paths.app_data_root,
                (local_app_data / "Infini Works" / "Letter Smith").resolve(),
            )
            self.assertEqual(
                paths.saved_letters_root,
                (home / "Documents" / "Letter Smith" / "Saved Letters").resolve(),
            )
            self.assertEqual(paths.temporary_root, temporary.resolve())
            self.assertEqual(
                paths.stock_music_root,
                resource_root.resolve() / "resources" / "stock" / "music",
            )
            self.assertEqual(
                paths.tool_path("ffmpeg.exe"),
                resource_root.resolve() / "tools" / "ffmpeg.exe",
            )

            paths.ensure_writable_roots()
            self.assertTrue(paths.settings_root.is_dir())
            self.assertTrue(paths.music_archive_root.is_dir())
            self.assertTrue(paths.saved_letters_root.is_dir())
            self.assertFalse(paths.stock_root.exists())

    def test_macos_runtime_uses_native_application_support_and_cache_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            resource_root = base / "bundle"

            paths = ApplicationPaths.for_runtime(
                resource_root,
                environ={"HOME": str(home)},
                platform_name="darwin",
            )

            self.assertEqual(
                paths.app_data_root,
                (
                    home
                    / "Library"
                    / "Application Support"
                    / "Infini Works"
                    / "Letter Smith"
                ).resolve(),
            )
            self.assertEqual(
                paths.cache_root,
                (
                    home
                    / "Library"
                    / "Caches"
                    / "Infini Works"
                    / "Letter Smith"
                ).resolve(),
            )
            self.assertEqual(
                paths.saved_letters_root,
                (home / "Documents" / "Letter Smith" / "Saved Letters").resolve(),
            )

    def test_audio_tool_names_are_platform_native(self) -> None:
        self.assertEqual(audio_tool_filename("ffmpeg", platform_name="win32"), "ffmpeg.exe")
        self.assertEqual(audio_tool_filename("ffprobe", platform_name="darwin"), "ffprobe")
        with self.assertRaises(ValueError):
            audio_tool_filename("nested/ffmpeg", platform_name="darwin")

    def test_explicit_project_layout_remains_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = ApplicationPaths.for_project(root)

            self.assertEqual(paths.workspace_root, root)
            self.assertEqual(paths.resource_root, root)
            self.assertEqual(paths.settings_file, root / "settings.json")
            self.assertEqual(paths.saved_letters_root, root / "output" / "Play")
            self.assertEqual(
                paths.music_archive_root,
                root / "gallery" / "user" / "sounds" / "appssong",
            )
            self.assertEqual(paths.temporary_root, root / "output")

    def test_app_resources_follow_gallery_and_resources_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gallery_icon = root / "gallery/app/icons/app.png"
            gallery_icon.parent.mkdir(parents=True)
            gallery_icon.write_bytes(b"gallery icon")
            resource_icon = root / "resources/app/icons/app.png"
            resource_icon.parent.mkdir(parents=True)
            resource_icon.write_bytes(b"compatibility icon")
            resource_font = root / "resources/app/fonts/app.ttf"
            resource_font.parent.mkdir(parents=True)
            resource_font.write_bytes(b"resource font")
            gallery_font = root / "gallery/app/fonts/app.ttf"
            gallery_font.parent.mkdir(parents=True)
            gallery_font.write_bytes(b"compatibility font")

            paths = ApplicationPaths.for_project(root)

            self.assertEqual(paths.app_resource_path("icons/app.png"), gallery_icon)
            self.assertEqual(paths.app_resource_path("fonts/app.ttf"), resource_font)

    def test_installer_configuration_accepts_canonical_gallery_icon(self) -> None:
        from release.build_installer import validate_installer_configuration

        summary = validate_installer_configuration()
        repository = Path(__file__).resolve().parents[1]

        self.assertEqual(summary["name"], "LetterSmith-Setup-1.0.0.exe")
        self.assertEqual(
            Path(summary["payload"]),
            (repository / "release" / "dist" / "LetterSmith").resolve(),
        )

    def test_macos_release_configuration_is_cross_platform_valid(self) -> None:
        from release.build_macos import validate_macos_configuration

        summary = validate_macos_configuration(require_tools=False)

        self.assertEqual(summary["app"], "Letter Smith.app")
        self.assertEqual(summary["bundle_identifier"], "works.infini.lettersmith")
        self.assertEqual(summary["dmg"], "LetterSmith-1.0.0.dmg")

    def test_release_rejects_foreign_icu_runtime_dlls(self) -> None:
        from release.build_release import (
            MANIFEST_PATH,
            ReleaseValidationError,
            _load_json,
            _validate_distribution_sanitation,
        )

        manifest = _load_json(MANIFEST_PATH)
        for name in ("icuuc.dll", "icudt78.dll"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                distribution = Path(directory)
                (distribution / "LetterSmith.exe").write_bytes(b"executable")
                internal = distribution / "_internal"
                internal.mkdir()
                (internal / name).write_bytes(b"foreign runtime")

                with self.assertRaisesRegex(
                    ReleaseValidationError,
                    "Forbidden development or user-data file",
                ):
                    _validate_distribution_sanitation(distribution, manifest)

    def test_release_build_uses_only_trusted_runtime_paths(self) -> None:
        from release import build_release

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_root = root / "Python"
            executable = python_root / "python.exe"
            windows_root = root / "Windows"
            foreign = root / "foreign" / "poppler"
            for path in (
                python_root / "DLLs",
                python_root / "Scripts",
                windows_root / "System32",
                foreign,
            ):
                path.mkdir(parents=True)
            executable.write_bytes(b"python")

            with (
                mock.patch.object(build_release.sys, "executable", str(executable)),
                mock.patch.object(build_release.sys, "base_prefix", str(python_root)),
                mock.patch.dict(
                    os.environ,
                    {
                        "PATH": str(foreign),
                        "SystemRoot": str(windows_root),
                        "LETTERSMITH_ENV_PROBE": "preserved",
                    },
                ),
            ):
                environment = build_release._pyinstaller_environment()

            path_entries = {
                Path(value).resolve()
                for value in environment["PATH"].split(os.pathsep)
            }
            self.assertNotIn(foreign.resolve(), path_entries)
            self.assertEqual(
                path_entries,
                {
                    python_root.resolve(),
                    (python_root / "DLLs").resolve(),
                    (python_root / "Scripts").resolve(),
                    (windows_root / "System32").resolve(),
                    windows_root.resolve(),
                },
            )
            self.assertEqual(environment["LETTERSMITH_ENV_PROBE"], "preserved")

    def test_frozen_runtime_uses_bundle_only_for_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = (base / "frozen-bundle").resolve()
            local_app_data = base / "local"
            home = base / "profile"

            with mock.patch(
                "project_paths.sys._MEIPASS",
                str(bundle),
                create=True,
            ):
                paths = ApplicationPaths.for_runtime(
                    environ={
                        "LOCALAPPDATA": str(local_app_data),
                        "USERPROFILE": str(home),
                    },
                    home=home,
                )

            self.assertEqual(paths.resource_root, bundle)
            self.assertEqual(
                paths.workspace_root,
                (
                    local_app_data
                    / "Infini Works"
                    / "Letter Smith"
                    / "Active Project"
                ).resolve(),
            )
            self.assertFalse(paths.workspace_root.is_relative_to(bundle))

    def test_configured_runtime_is_used_only_for_its_known_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths = ApplicationPaths.for_runtime(
                base / "bundle",
                environ={
                    "LOCALAPPDATA": str(base / "local"),
                    "USERPROFILE": str(base / "profile"),
                },
                home=base / "profile",
            )
            unrelated = base / "test-project"
            with mock.patch("project_paths._APPLICATION_PATHS", paths):
                self.assertIs(application_paths(paths.workspace_root), paths)
                self.assertIs(application_paths(paths.resource_root), paths)
                self.assertEqual(
                    application_paths(unrelated).settings_file,
                    unrelated.resolve() / "settings.json",
                )

    def test_legacy_migration_copies_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            legacy = base / "bundle"
            local_app_data = base / "local"
            home = base / "profile"
            (legacy / "gallery/user/pages").mkdir(parents=True)
            (legacy / "gallery/user/pages/cover.png").write_bytes(b"cover")
            (legacy / "gallery/user/sounds/appssong").mkdir(parents=True)
            (legacy / "gallery/user/sounds/appssong/library.json").write_text(
                '{"tracks": {}}',
                encoding="utf-8",
            )
            (legacy / "gallery/user/sounds/appssong/project_sound.json").write_text(
                '{"mode": "single"}',
                encoding="utf-8",
            )
            (legacy / "output/Play/A Friend/Welcome").mkdir(parents=True)
            (legacy / "output/Play/A Friend/Welcome/index.html").write_text(
                "<html></html>",
                encoding="utf-8",
            )
            (legacy / "Prompter/modules").mkdir(parents=True)
            (legacy / "Prompter/modules/type.txt").write_text(
                "Illustration\n",
                encoding="utf-8",
            )
            (legacy / "Prompter/modules/user_colors.json").write_text(
                '{"colors": ["Copper"]}',
                encoding="utf-8",
            )
            (legacy / "settings.json").write_text(
                '{"starting_volume": 42}',
                encoding="utf-8",
            )
            paths = ApplicationPaths.for_runtime(
                legacy,
                environ={
                    "LOCALAPPDATA": str(local_app_data),
                    "USERPROFILE": str(home),
                },
                home=home,
            )
            paths.initialize(legacy)

            self.assertEqual(
                json.loads(paths.settings_file.read_text(encoding="utf-8"))["starting_volume"],
                42,
            )
            self.assertEqual(
                (paths.workspace_root / "gallery/user/pages/cover.png").read_bytes(),
                b"cover",
            )
            self.assertTrue(
                (paths.saved_letters_root / "A Friend/Welcome/index.html").is_file()
            )
            self.assertTrue((paths.prompt_writer_content_root / "type.txt").is_file())
            self.assertTrue((paths.custom_palette_root / "user_colors.json").is_file())
            set_path_hidden(paths.settings_file, False)
            paths.settings_file.write_text('{"starting_volume": 77}', encoding="utf-8")
            paths.initialize(legacy)
            self.assertEqual(
                json.loads(paths.settings_file.read_text(encoding="utf-8"))["starting_volume"],
                77,
            )

    def test_resource_paths_reject_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ApplicationPaths.for_project(directory)
            with self.assertRaises(ValueError):
                paths.resource_path("../outside.txt")
            with self.assertRaises(ValueError):
                paths.tool_path("nested/ffmpeg.exe")


if __name__ == "__main__":
    unittest.main()
