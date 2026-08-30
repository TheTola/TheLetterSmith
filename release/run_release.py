from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TypeVar


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ReleasePipelineError(RuntimeError):
    pass


T = TypeVar("T")


def _stage(name: str, action: Callable[[], T]) -> T:
    print(f"\n==> {name}", flush=True)
    result = action()
    print(f"<== {name}: passed", flush=True)
    return result


def _release_platform(platform_name: str | None = None) -> str:
    platform = str(platform_name or sys.platform).casefold()
    if platform == "win32":
        return "win32"
    if platform == "darwin":
        return "darwin"
    raise ReleasePipelineError(
        "Production releases are supported only on native Windows or macOS hosts."
    )


def _preflight_release(platform_name: str) -> None:
    from release.provision_ffmpeg import validate_provisioning_manifest

    validate_provisioning_manifest()
    if importlib.util.find_spec("PyInstaller") is None:
        raise ReleasePipelineError(
            "PyInstaller is not installed. Install requirements.txt before releasing."
        )

    if platform_name == "win32":
        from release.build_installer import (
            _find_iscc,
            validate_installer_configuration,
        )
        from release.windows_signing import load_signing_configuration

        validate_installer_configuration()
        if _find_iscc() is None:
            raise ReleasePipelineError("Inno Setup 6.3 or newer is not installed.")
        load_signing_configuration()
        return

    from release.build_macos import (
        _normalized_target_architecture,
        _validate_developer_id_identity,
        validate_macos_configuration,
    )

    validate_macos_configuration(require_tools=False)
    architecture = os.environ.get(
        "LETTER_SMITH_MACOS_TARGET_ARCH",
        "",
    ).strip()
    if not architecture:
        raise ReleasePipelineError(
            "Set LETTER_SMITH_MACOS_TARGET_ARCH for a production macOS release."
        )
    _normalized_target_architecture(architecture)
    identity = os.environ.get(
        "LETTER_SMITH_MACOS_CODESIGN_IDENTITY",
        "",
    ).strip()
    if not identity:
        raise ReleasePipelineError(
            "Set LETTER_SMITH_MACOS_CODESIGN_IDENTITY for a production release."
        )
    if not os.environ.get("LETTER_SMITH_MACOS_NOTARY_PROFILE", "").strip():
        raise ReleasePipelineError(
            "Set LETTER_SMITH_MACOS_NOTARY_PROFILE for a production release."
        )
    _validate_developer_id_identity(identity)


def _run_test_suite() -> None:
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "test",
            "-p",
            "test_*.py",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise ReleasePipelineError(
            f"The complete test suite failed with exit code {result.returncode}."
        )


def _provision_release_tools(
    platform_name: str,
    ffmpeg_source_dir: str | Path | None,
) -> None:
    from release.provision_ffmpeg import provision_ffmpeg

    if platform_name == "win32":
        provision_ffmpeg("windows", source_dir=ffmpeg_source_dir)
        return
    architecture = os.environ["LETTER_SMITH_MACOS_TARGET_ARCH"].strip()
    provision_ffmpeg(
        "darwin",
        architecture,
        source_dir=ffmpeg_source_dir,
    )


def _validate_release_sources(platform_name: str) -> None:
    from release.build_release import validate_release_inputs

    validate_release_inputs(platform_name)


def _build_windows_frozen(
    ffmpeg_source_dir: str | Path | None,
) -> Path:
    from release.build_release import build_release, validate_distribution

    executable = build_release(
        sign=True,
        ffmpeg_source_dir=ffmpeg_source_dir,
    )
    if validate_distribution() != executable:
        raise ReleasePipelineError("The verified Windows executable path changed.")
    return executable


def _build_windows_installer() -> Path:
    from release.build_installer import build_installer

    return build_installer(sign=True)


def _verify_windows_package(installer: Path) -> None:
    from release.windows_signing import (
        load_signing_configuration,
        verify_signature,
    )

    if not installer.is_file() or installer.stat().st_size < 1_000_000:
        raise ReleasePipelineError("The Windows installer is missing or incomplete.")
    verify_signature(installer, load_signing_configuration())


def _build_macos_package(
    ffmpeg_source_dir: str | Path | None,
) -> tuple[Path, Path, Path]:
    from release.build_macos import build_macos_release

    return build_macos_release(
        notarize=True,
        ffmpeg_source_dir=ffmpeg_source_dir,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_macos_package(result: tuple[Path, Path, Path]) -> None:
    app_path, dmg_path, report_path = result
    if not app_path.is_dir() or not dmg_path.is_file() or not report_path.is_file():
        raise ReleasePipelineError("The macOS release artifacts are incomplete.")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        notarization = report["notarization"]
        disk_image = report["artifacts"]["disk_image"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ReleasePipelineError("The macOS release report is invalid.") from error
    if not isinstance(notarization, dict) or not isinstance(disk_image, dict):
        raise ReleasePipelineError("The macOS release report is invalid.")
    if (
        str(notarization.get("status", "")).casefold() != "accepted"
        or not str(notarization.get("submission_id", "")).strip()
    ):
        raise ReleasePipelineError("The macOS package was not notarized successfully.")
    try:
        package_matches = (
            disk_image.get("size_bytes") == dmg_path.stat().st_size
            and disk_image.get("sha256") == _sha256(dmg_path)
        )
    except OSError as error:
        raise ReleasePipelineError("The macOS disk image is unreadable.") from error
    if not package_matches:
        raise ReleasePipelineError("The macOS disk image differs from its release report.")


def run_release(
    *,
    platform_name: str | None = None,
    ffmpeg_source_dir: str | Path | None = None,
) -> tuple[Path, ...]:
    platform = _release_platform(platform_name)
    _stage("Validate release environment", lambda: _preflight_release(platform))
    _stage("Run complete test suite", _run_test_suite)
    _stage(
        "Prepare checksum-pinned FFmpeg tools",
        lambda: _provision_release_tools(platform, ffmpeg_source_dir),
    )
    _stage(
        "Validate release sources and resources",
        lambda: _validate_release_sources(platform),
    )

    if platform == "win32":
        executable = _stage(
            "Build, validate, and sign frozen application",
            lambda: _build_windows_frozen(ffmpeg_source_dir),
        )
        installer = _stage(
            "Build and sign installer",
            _build_windows_installer,
        )
        _stage(
            "Verify installer package",
            lambda: _verify_windows_package(installer),
        )
        return executable, installer

    result = _stage(
        "Build, validate, sign, notarize, and verify application and DMG",
        lambda: _build_macos_package(ffmpeg_source_dir),
    )
    _stage("Verify macOS release report", lambda: _verify_macos_package(result))
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete signed Letter Smith release pipeline."
    )
    parser.add_argument(
        "--confirm-release",
        action="store_true",
        help="Required to build signed public release artifacts.",
    )
    parser.add_argument(
        "--ffmpeg-source-dir",
        help="Stage reviewed FFmpeg/FFprobe inputs instead of downloading them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.confirm_release:
        print("ERROR: --confirm-release is required.", file=sys.stderr)
        return 2
    try:
        artifacts = run_release(ffmpeg_source_dir=args.ffmpeg_source_dir)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("\nRelease complete:")
    for artifact in artifacts:
        print(f"- {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
