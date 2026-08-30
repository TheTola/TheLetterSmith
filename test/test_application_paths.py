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

    def test_ffmpeg_provisioning_manifest_is_checksum_pinned(self) -> None:
        from release.provision_ffmpeg import validate_provisioning_manifest

        self.assertEqual(
            validate_provisioning_manifest(),
            {"windows": "9.0.1", "macos": "9.0.1"},
        )

    def test_windows_signing_uses_sha256_rfc3161_timestamping(self) -> None:
        from release import windows_signing

        with tempfile.TemporaryDirectory() as directory:
            signtool = Path(directory) / "signtool.exe"
            signtool.write_bytes(b"test")
            thumbprint = "A" * 40
            with (
                mock.patch.object(
                    windows_signing,
                    "find_signtool",
                    return_value=signtool,
                ),
                mock.patch.dict(
                    os.environ,
                    {"LETTER_SMITH_WINDOWS_CODESIGN_SHA1": thumbprint},
                ),
            ):
                configuration = windows_signing.load_signing_configuration()

            arguments = configuration.sign_arguments("LetterSmith.exe")
            self.assertEqual(arguments[arguments.index("/fd") + 1], "SHA256")
            self.assertEqual(arguments[arguments.index("/td") + 1], "SHA256")
            self.assertEqual(arguments[arguments.index("/sha1") + 1], thumbprint)
            self.assertIn("/tr", arguments)
            self.assertIn("$f", configuration.inno_definition())

    def test_macos_release_configuration_is_cross_platform_valid(self) -> None:
        from release.build_macos import (
            _dmg_name_for_architecture,
            _normalized_target_architecture,
            _parse_macos_minimum_versions,
            _required_architectures,
            _version_tuple,
            validate_macos_configuration,
        )

        summary = validate_macos_configuration(require_tools=False)

        self.assertEqual(summary["app"], "Letter Smith.app")
        self.assertEqual(summary["bundle_identifier"], "works.infini.lettersmith")
        self.assertEqual(summary["dmg"], "LetterSmith-1.0.0.dmg")
        self.assertEqual(_required_architectures("arm64"), {"arm64"})
        self.assertEqual(
            _required_architectures("universal2"),
            {"arm64", "x86_64"},
        )
        self.assertEqual(_normalized_target_architecture("aarch64"), "arm64")
        self.assertEqual(
            _dmg_name_for_architecture("LetterSmith-1.0.0.dmg", "arm64"),
            "LetterSmith-1.0.0-arm64.dmg",
        )
        self.assertEqual(
            _dmg_name_for_architecture("LetterSmith-1.0.0.dmg", "universal2"),
            "LetterSmith-1.0.0.dmg",
        )
        self.assertEqual(_version_tuple("13.0"), (13, 0, 0))
        self.assertEqual(
            _parse_macos_minimum_versions(
                """
                cmd LC_BUILD_VERSION
                minos 13.0
                cmd LC_VERSION_MIN_MACOSX
                version 12.3
                """
            ),
            ("13.0", "12.3"),
        )

    def test_macos_frozen_payload_allows_rewritten_binary_bytes(self) -> None:
        from release import build_release

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_binary = root / "source-ffmpeg"
            source_resource = root / "source.json"
            internal = root / "_internal"
            frozen_binary = internal / "tools" / "ffmpeg"
            frozen_resource = internal / "resources" / "source.json"
            frozen_binary.parent.mkdir(parents=True)
            frozen_resource.parent.mkdir(parents=True)
            source_binary.write_bytes(b"unsigned-source")
            frozen_binary.write_bytes(b"rewritten-and-signed")
            source_resource.write_bytes(b"same-resource")
            frozen_resource.write_bytes(b"same-resource")

            with (
                mock.patch.object(
                    build_release,
                    "_expected_payload_destinations",
                    return_value={
                        "tools/ffmpeg": source_binary,
                        "resources/source.json": source_resource,
                    },
                ),
                mock.patch.object(
                    build_release,
                    "_expected_binary_destinations",
                    return_value={"tools/ffmpeg"},
                ),
            ):
                build_release._validate_frozen_payload(
                    internal,
                    {},
                    "darwin",
                )

    def test_macos_otool_parser_handles_universal_output(self) -> None:
        from release.build_macos import (
            _parse_otool_dependencies,
            _unsafe_bundle_dependencies,
        )

        dependencies = _parse_otool_dependencies(
            """
/tmp/Letter Smith (architecture x86_64):
    /usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)
    @rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0, current version 6.8.0)
/tmp/Letter Smith (architecture arm64):
    /System/Library/Frameworks/AppKit.framework/Versions/C/AppKit (compatibility version 45.0.0, current version 2575.0.0)
    /opt/homebrew/lib/libforeign.dylib (compatibility version 1.0.0, current version 1.0.0)
            """
        )

        self.assertEqual(
            dependencies,
            (
                "/usr/lib/libSystem.B.dylib",
                "@rpath/QtCore.framework/Versions/A/QtCore",
                "/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit",
                "/opt/homebrew/lib/libforeign.dylib",
            ),
        )
        self.assertEqual(
            _unsafe_bundle_dependencies(dependencies),
            ("/opt/homebrew/lib/libforeign.dylib",),
        )

    def test_macos_bundle_checks_every_macho_dependency_set(self) -> None:
        from release import build_macos

        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory)
            safe = contents / "safe"
            unsafe = contents / "unsafe"
            safe.write_bytes(b"\xcf\xfa\xed\xfe-safe")
            unsafe.write_bytes(b"\xcf\xfa\xed\xfe-unsafe")

            def dependencies(path: Path) -> tuple[str, ...]:
                if path.name == "unsafe":
                    return ("/opt/homebrew/lib/libforeign.dylib",)
                return ("/usr/lib/libSystem.B.dylib",)

            with (
                mock.patch.object(build_macos, "_validate_binary_architectures"),
                mock.patch.object(
                    build_macos,
                    "_validate_binary_minimum_system_version",
                ),
                mock.patch.object(
                    build_macos,
                    "_inspect_macho_dependencies",
                    side_effect=dependencies,
                ) as inspect,
            ):
                with self.assertRaisesRegex(
                    build_macos.MacOSReleaseError,
                    "non-portable dependency references",
                ):
                    build_macos._validate_bundle_macho_files(
                        contents,
                        required_architectures={"arm64"},
                        minimum_system_version="13.0",
                    )

            self.assertEqual(inspect.call_count, 2)

    def test_macos_dmg_stages_app_at_volume_root(self) -> None:
        from release import build_macos

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "Letter Smith.app"
            app_path.mkdir()
            dmg_path = root / "LetterSmith.dmg"
            commands: list[list[str]] = []

            def record(command: list[str], *, label: str) -> None:
                commands.append(command)

            with (
                mock.patch.object(build_macos, "_run_checked", side_effect=record),
                mock.patch.object(Path, "symlink_to", autospec=True) as symlink,
            ):
                build_macos._create_dmg(app_path, dmg_path, identity="")

        ditto = next(command for command in commands if command[0] == build_macos.DITTO_PATH)
        create = next(
            command
            for command in commands
            if command[:2] == [build_macos.HDIUTIL_PATH, "create"]
        )
        source_root = Path(create[create.index("-srcfolder") + 1])
        self.assertNotEqual(source_root, app_path)
        self.assertEqual(Path(ditto[-1]), source_root / app_path.name)
        self.assertEqual(symlink.call_args.args[0].name, "Applications")
        self.assertEqual(symlink.call_args.args[1], "/Applications")

    def test_macos_codesign_parser_extracts_hardened_runtime_flags(self) -> None:
        from release.build_macos import _parse_codesign_details

        details = _parse_codesign_details(
            """
Executable=/Applications/Letter Smith.app/Contents/MacOS/Letter Smith
CodeDirectory v=20500 size=880 flags=0x10000(runtime) hashes=18+7 location=embedded
Signature size=9072
Authority=Developer ID Application: Infini Works (ABCDE12345)
Timestamp=Aug 30, 2026 at 12:00:00
TeamIdentifier=ABCDE12345
            """
        )

        self.assertEqual(details["flags"], "0x10000(runtime)")
        self.assertEqual(details["signature"], "9072")
        self.assertEqual(
            details["authority"],
            "Developer ID Application: Infini Works (ABCDE12345)",
        )
        self.assertEqual(details["team_identifier"], "ABCDE12345")
        self.assertTrue(details["timestamp"])

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

    def test_public_release_pipeline_runs_every_windows_gate_in_order(self) -> None:
        from release import run_release

        events: list[str] = []
        executable = Path("LetterSmith.exe")
        installer = Path("LetterSmith-Setup.exe")
        with (
            mock.patch.object(
                run_release,
                "_preflight_release",
                side_effect=lambda _platform: events.append("preflight"),
            ),
            mock.patch.object(
                run_release,
                "_run_test_suite",
                side_effect=lambda: events.append("tests"),
            ),
            mock.patch.object(
                run_release,
                "_provision_release_tools",
                side_effect=lambda _platform, _source: events.append("ffmpeg"),
            ),
            mock.patch.object(
                run_release,
                "_validate_release_sources",
                side_effect=lambda _platform: events.append("sources"),
            ),
            mock.patch.object(
                run_release,
                "_build_windows_frozen",
                side_effect=lambda _source: events.append("frozen") or executable,
            ),
            mock.patch.object(
                run_release,
                "_build_windows_installer",
                side_effect=lambda: events.append("installer") or installer,
            ),
            mock.patch.object(
                run_release,
                "_verify_windows_package",
                side_effect=lambda _path: events.append("verify"),
            ),
        ):
            artifacts = run_release.run_release(platform_name="win32")

        self.assertEqual(
            events,
            [
                "preflight",
                "tests",
                "ffmpeg",
                "sources",
                "frozen",
                "installer",
                "verify",
            ],
        )
        self.assertEqual(artifacts, (executable, installer))

    def test_public_release_requires_explicit_confirmation(self) -> None:
        from release import run_release

        with mock.patch.object(run_release, "run_release") as pipeline:
            result = run_release.main([])

        self.assertEqual(result, 2)
        pipeline.assert_not_called()

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
