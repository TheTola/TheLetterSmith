from __future__ import annotations

import argparse
import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "release"
MANIFEST_PATH = RELEASE_ROOT / "release_manifest.json"
SPEC_PATH = RELEASE_ROOT / "LetterSmith.spec"
ENTITLEMENTS_PATH = RELEASE_ROOT / "macos_entitlements.plist"
MACOS_ROOT = RELEASE_ROOT / "macos"
BUILD_ROOT = MACOS_ROOT / "build"
DIST_ROOT = MACOS_ROOT / "dist"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from release.build_release import (
    ReleaseValidationError,
    _load_json,
    _validate_frozen_payload,
    _validate_identity,
    _validate_private_binary_markers,
    _validate_private_text,
    validate_release_inputs,
)


class MacOSReleaseError(RuntimeError):
    pass


def _macos_configuration(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("macos")
    if not isinstance(value, dict):
        raise MacOSReleaseError("The macOS release configuration is missing.")
    return value


def _exact_case_project_path(relative: object) -> Path:
    value = Path(str(relative or ""))
    if not str(value) or value.is_absolute() or ".." in value.parts:
        raise MacOSReleaseError(f"Unsafe macOS release path: {relative!r}")
    current = PROJECT_ROOT
    for part in value.parts:
        if not current.is_dir():
            raise MacOSReleaseError(f"macOS release input is missing: {value}")
        match = next((child for child in current.iterdir() if child.name == part), None)
        if match is None:
            raise MacOSReleaseError(
                f"macOS release path has missing or incorrect filename case: {value}"
            )
        current = match
    return current.resolve()


def _validate_tool(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise MacOSReleaseError(f"Required macOS audio tool is missing: {path}")
    if sys.platform == "darwin" and not os.access(path, os.X_OK):
        raise MacOSReleaseError(f"macOS audio tool is not executable: {path}")
    try:
        result = subprocess.run(
            [str(path), "-version"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MacOSReleaseError(f"macOS audio tool could not run: {path}") from error
    expected = f"{path.name} version"
    if result.returncode != 0 or expected not in result.stdout.casefold():
        raise MacOSReleaseError(f"macOS audio tool validation failed: {path}")


def validate_macos_configuration(*, require_tools: bool = False) -> dict[str, str]:
    from application_identity import (
        APPLICATION_VERSION,
        MACOS_APP_NAME,
        MACOS_BUNDLE_IDENTIFIER,
        MACOS_DISK_IMAGE_NAME,
        MACOS_EXECUTABLE_NAME,
    )

    manifest = _load_json(MANIFEST_PATH)
    try:
        _validate_identity(manifest)
    except ReleaseValidationError as error:
        raise MacOSReleaseError(str(error)) from error
    macos = _macos_configuration(manifest)
    expected = {
        "app_name": MACOS_APP_NAME,
        "executable": MACOS_EXECUTABLE_NAME,
        "bundle_identifier": MACOS_BUNDLE_IDENTIFIER,
        "dmg_name": MACOS_DISK_IMAGE_NAME,
        "minimum_system_version": "13.0",
    }
    for key, value in expected.items():
        if macos.get(key) != value:
            raise MacOSReleaseError(f"Invalid macOS release setting {key!r}.")
    if macos.get("supported_architectures") != ["arm64", "x86_64", "universal2"]:
        raise MacOSReleaseError("macOS architectures must cover arm64, x86_64, and universal2.")

    icon = _exact_case_project_path(macos.get("icon"))
    try:
        header = icon.read_bytes()[:4]
    except OSError as error:
        raise MacOSReleaseError(f"Unreadable macOS icon: {icon}") from error
    if icon.suffix != ".icns" or header != b"icns" or icon.stat().st_size < 100_000:
        raise MacOSReleaseError("The macOS application icon is missing or invalid.")

    try:
        entitlements = plistlib.loads(ENTITLEMENTS_PATH.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise MacOSReleaseError("The macOS entitlements file is invalid.") from error
    required_entitlements = {
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
    }
    if any(entitlements.get(key) is not True for key in required_entitlements):
        raise MacOSReleaseError("Qt WebEngine runtime entitlements are incomplete.")

    binaries = macos.get("binaries")
    if not isinstance(binaries, list) or len(binaries) != 2:
        raise MacOSReleaseError("macOS FFmpeg binary configuration is incomplete.")
    configured_names: set[str] = set()
    for item in binaries:
        if not isinstance(item, dict) or item.get("destination") != "tools":
            raise MacOSReleaseError("Invalid macOS FFmpeg binary destination.")
        source = Path(str(item.get("source", "")))
        if source.parent.as_posix() != "tools/macos" or source.name not in {
            "ffmpeg",
            "ffprobe",
        }:
            raise MacOSReleaseError("Invalid macOS FFmpeg binary source.")
        configured_names.add(source.name)
        if require_tools:
            _validate_tool(_exact_case_project_path(source))
    if configured_names != {"ffmpeg", "ffprobe"}:
        raise MacOSReleaseError("macOS FFmpeg and FFprobe must both be configured.")

    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    for required in ("BUNDLE(", "bundle_identifier", "macos_entitlements.plist"):
        if required not in spec_text:
            raise MacOSReleaseError(f"PyInstaller macOS bundle setting is missing: {required}")

    return {
        "app": MACOS_APP_NAME,
        "bundle_identifier": MACOS_BUNDLE_IDENTIFIER,
        "dmg": MACOS_DISK_IMAGE_NAME,
        "version": APPLICATION_VERSION,
        "tools": "validated" if require_tools else "required on macOS build host",
    }


def _clean_output_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != MACOS_ROOT.resolve():
        raise MacOSReleaseError(f"Refusing to clean unsafe path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _run_checked(command: list[str], *, label: str) -> None:
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise MacOSReleaseError(f"{label} failed with exit code {result.returncode}.")


def _validate_bundle(app_path: Path, manifest: dict[str, Any]) -> Path:
    macos = _macos_configuration(manifest)
    contents = app_path / "Contents"
    plist_path = contents / "Info.plist"
    try:
        info = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise MacOSReleaseError("The macOS app bundle has an invalid Info.plist.") from error
    expected_info = {
        "CFBundleIdentifier": macos["bundle_identifier"],
        "CFBundleExecutable": macos["executable"],
        "CFBundleShortVersionString": manifest["product"]["version"],
        "LSMinimumSystemVersion": macos["minimum_system_version"],
    }
    for key, value in expected_info.items():
        if str(info.get(key, "")) != value:
            raise MacOSReleaseError(f"macOS bundle metadata differs at {key}.")

    executable = contents / "MacOS" / macos["executable"]
    frameworks = contents / "Frameworks"
    internal = frameworks / "_internal"
    if not executable.is_file() or executable.stat().st_size < 1_000_000:
        raise MacOSReleaseError("The macOS application executable is incomplete.")
    if not internal.is_dir():
        raise MacOSReleaseError("The macOS application framework payload is missing.")

    contents_root = contents.resolve()
    for path in contents.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(contents_root)
        except (OSError, ValueError) as error:
            raise MacOSReleaseError(f"Unsafe macOS bundle link: {path}") from error

    for relative in (
        "resources/stock/stock_manifest.json",
        "resources/examples/example_letter/lettersmith-metadata.json",
        "gallery/app/icons/folder/lsmith.png",
        "tools/ffmpeg",
        "tools/ffprobe",
    ):
        if not (internal / relative).is_file():
            raise MacOSReleaseError(f"The macOS app is missing {relative}.")
    if not any(path.is_file() for path in contents.rglob("QtWebEngineProcess")):
        raise MacOSReleaseError("Qt WebEngine helper is missing from the macOS app.")
    if not any(path.is_file() for path in contents.rglob("libqcocoa.dylib")):
        raise MacOSReleaseError("Qt Cocoa platform plugin is missing from the macOS app.")

    try:
        _validate_frozen_payload(internal, manifest, "darwin")
    except ReleaseValidationError as error:
        raise MacOSReleaseError(str(error)) from error
    files = tuple(
        path for path in contents.rglob("*") if path.is_file() and not path.is_symlink()
    )
    _validate_private_text(
        files,
        context="macOS app bundle",
        include_secret_literals=False,
    )
    _validate_private_binary_markers(files)
    _run_checked(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)],
        label="macOS code-signature verification",
    )
    return executable


def _create_dmg(app_path: Path, dmg_path: Path, *, identity: str) -> None:
    dmg_path.unlink(missing_ok=True)
    _run_checked(
        [
            "hdiutil",
            "create",
            "-volname",
            "Letter Smith",
            "-srcfolder",
            str(app_path),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ],
        label="macOS disk-image creation",
    )
    if identity:
        _run_checked(
            ["codesign", "--force", "--timestamp", "--sign", identity, str(dmg_path)],
            label="macOS disk-image signing",
        )


def _notarize(dmg_path: Path) -> None:
    profile = os.environ.get("LETTER_SMITH_MACOS_NOTARY_PROFILE", "").strip()
    if not profile:
        raise MacOSReleaseError(
            "Set LETTER_SMITH_MACOS_NOTARY_PROFILE to a notarytool keychain profile."
        )
    _run_checked(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(dmg_path),
            "--keychain-profile",
            profile,
            "--wait",
        ],
        label="Apple notarization",
    )
    _run_checked(
        ["xcrun", "stapler", "staple", str(dmg_path)],
        label="Apple notarization stapling",
    )
    _run_checked(
        ["xcrun", "stapler", "validate", str(dmg_path)],
        label="Apple notarization validation",
    )


def build_macos_release(*, notarize: bool = False) -> tuple[Path, Path]:
    if sys.platform != "darwin":
        raise MacOSReleaseError("Native Letter Smith macOS builds require macOS.")
    if importlib.util.find_spec("PyInstaller") is None:
        raise MacOSReleaseError("PyInstaller is not installed on the macOS build host.")
    summary = validate_macos_configuration(require_tools=True)
    try:
        validate_release_inputs("darwin")
    except ReleaseValidationError as error:
        raise MacOSReleaseError(str(error)) from error

    architecture = os.environ.get("LETTER_SMITH_MACOS_TARGET_ARCH", "").strip()
    manifest = _load_json(MANIFEST_PATH)
    allowed_architectures = set(_macos_configuration(manifest)["supported_architectures"])
    if architecture and architecture not in allowed_architectures:
        raise MacOSReleaseError(f"Unsupported macOS target architecture: {architecture}")
    identity = os.environ.get("LETTER_SMITH_MACOS_CODESIGN_IDENTITY", "").strip()
    if notarize and not identity:
        raise MacOSReleaseError("Apple notarization requires a Developer ID identity.")

    MACOS_ROOT.mkdir(parents=True, exist_ok=True)
    _clean_output_directory(BUILD_ROOT)
    _clean_output_directory(DIST_ROOT)
    environment = dict(os.environ)
    environment["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_ROOT),
            "--workpath",
            str(BUILD_ROOT),
            str(SPEC_PATH),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise MacOSReleaseError(f"PyInstaller failed with exit code {result.returncode}.")

    app_path = DIST_ROOT / summary["app"]
    _validate_bundle(app_path, manifest)
    dmg_path = MACOS_ROOT / summary["dmg"]
    _create_dmg(app_path, dmg_path, identity=identity)
    if notarize:
        _notarize(dmg_path)
    return app_path, dmg_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or build the native Letter Smith macOS release."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    parser.add_argument("--confirm-package", action="store_true")
    parser.add_argument("--notarize", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.build and not args.confirm_package:
        print("ERROR: --build requires --confirm-package.", file=sys.stderr)
        return 2
    if args.notarize and not args.build:
        print("ERROR: --notarize requires --build.", file=sys.stderr)
        return 2
    try:
        summary = validate_macos_configuration(require_tools=False)
        print(
            "macOS configuration validated: "
            f"app={summary['app']}, bundle_id={summary['bundle_identifier']}"
        )
        if not args.build:
            print("Native packaging was not run; it requires a macOS build host.")
            return 0
        app_path, dmg_path = build_macos_release(notarize=args.notarize)
        print(f"Built and verified: {app_path}")
        print(f"Disk image: {dmg_path}")
        return 0
    except (MacOSReleaseError, ReleaseValidationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
