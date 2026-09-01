from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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
LIPO_PATH = "/usr/bin/lipo"
OTOOL_PATH = "/usr/bin/otool"
CODESIGN_PATH = "/usr/bin/codesign"
HDIUTIL_PATH = "/usr/bin/hdiutil"
XCRUN_PATH = "/usr/bin/xcrun"
SPCTL_PATH = "/usr/sbin/spctl"
DITTO_PATH = "/usr/bin/ditto"
_MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
}
_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){1,2}$")

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


def _sanitized_macos_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(
        (
            str(Path(sys.executable).resolve().parent),
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )
    return environment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_otool_dependencies(output: str) -> tuple[str, ...]:
    dependencies: list[str] = []
    for line in output.splitlines():
        if not line[:1].isspace():
            continue
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if dependency:
            dependencies.append(dependency)
    return tuple(dependencies)


def _inspect_macho_dependencies(path: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            [OTOOL_PATH, "-L", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_sanitized_macos_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MacOSReleaseError(
            f"Could not inspect macOS binary dependencies: {path}"
        ) from error
    if result.returncode != 0:
        raise MacOSReleaseError(
            f"Could not inspect macOS binary dependencies: {path}"
        )
    return _parse_otool_dependencies(result.stdout)


def _unsafe_bundle_dependencies(dependencies: tuple[str, ...]) -> tuple[str, ...]:
    """Return explicit dependency references that cannot be bundle-portable.

    Tokenized dyld references remain subject to their loader's LC_RPATH stack and
    require a native launch test; this check only rejects direct host paths.
    """
    allowed_prefixes = (
        "/usr/lib/",
        "/System/Library/",
        "@rpath/",
        "@loader_path/",
        "@executable_path/",
    )
    return tuple(
        sorted(
            dependency
            for dependency in dependencies
            if not dependency.startswith(allowed_prefixes)
        )
    )


def _validate_tool_dependencies(path: Path) -> None:
    dependencies = _inspect_macho_dependencies(path)
    unsafe = sorted(
        dependency
        for dependency in dependencies
        if not dependency.startswith(("/usr/lib/", "/System/Library/"))
    )
    if unsafe:
        raise MacOSReleaseError(
            f"macOS audio tool has non-system dependencies: {path}; {unsafe}"
        )


def _project_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def _validate_tool(path: Path) -> dict[str, str]:
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
            env=_sanitized_macos_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MacOSReleaseError(f"macOS audio tool could not run: {path}") from error
    expected = f"{path.name} version"
    if result.returncode != 0 or expected not in result.stdout.casefold():
        raise MacOSReleaseError(f"macOS audio tool validation failed: {path}")
    if sys.platform == "darwin":
        _validate_tool_dependencies(path)
    version_line = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()),
        "",
    )
    return {
        "path": _project_relative_path(path),
        "version": version_line,
        "sha256": _sha256(path),
    }


def _ffmpeg_provenance_summary(architecture: str) -> dict[str, Any]:
    manifest = _load_json(RELEASE_ROOT / "ffmpeg_manifest.json")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict):
        raise MacOSReleaseError("The shared FFmpeg provisioning manifest is invalid.")
    keys = (
        ("macos-arm64", "macos-x86_64")
        if architecture == "universal2"
        else (f"macos-{architecture}",)
    )
    configurations: list[dict[str, Any]] = []
    for key in keys:
        value = platforms.get(key)
        if not isinstance(value, dict):
            raise MacOSReleaseError(f"FFmpeg provisioning is missing {key}.")
        configurations.append(value)
    provenance_path = PROJECT_ROOT / "tools" / "FFmpeg-PROVENANCE.txt"
    if not provenance_path.is_file() or provenance_path.is_symlink():
        raise MacOSReleaseError("The staged FFmpeg provenance record is missing.")
    return {
        "target_architecture": architecture,
        "staging_record": {
            "path": _project_relative_path(provenance_path),
            "sha256": _sha256(provenance_path),
        },
        "inputs": [
            {
                "provider": str(configuration["provider"]),
                "version": str(configuration["version"]),
                "license": str(configuration["license"]),
                "artifacts": [
                    {
                        "name": str(item["name"]),
                        "url": str(item["url"]),
                        "archive_sha256": str(item["archive_sha256"]),
                        "binary_sha256": str(item["binary_sha256"]),
                    }
                    for item in configuration["tools"]
                ],
            }
            for configuration in configurations
        ],
    }


def _normalized_target_architecture(target_architecture: str) -> str:
    target = target_architecture.strip().casefold()
    if not target:
        target = platform.machine().strip().casefold()
    if target == "aarch64":
        target = "arm64"
    if target not in {"arm64", "x86_64", "universal2"}:
        raise MacOSReleaseError(f"Unsupported macOS build architecture: {target}")
    return target


def _required_architectures(target_architecture: str) -> set[str]:
    target = _normalized_target_architecture(target_architecture)
    if target == "universal2":
        return {"arm64", "x86_64"}
    return {target}


def _validate_binary_architectures(path: Path, required: set[str]) -> None:
    try:
        result = subprocess.run(
            [LIPO_PATH, "-archs", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_sanitized_macos_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MacOSReleaseError(f"Could not inspect macOS binary: {path}") from error
    actual = set(result.stdout.casefold().split())
    if result.returncode != 0 or not required.issubset(actual):
        raise MacOSReleaseError(
            f"macOS binary architecture mismatch: {path}; "
            f"required={sorted(required)}, actual={sorted(actual)}"
        )


def _is_macho_binary(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in _MACHO_MAGICS
    except OSError:
        return False


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not _VERSION_PATTERN.fullmatch(value):
        raise MacOSReleaseError(f"Invalid macOS version: {value}")
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0])[:3])  # type: ignore[return-value]


def _parse_macos_minimum_versions(output: str) -> tuple[str, ...]:
    versions: list[str] = []
    active_command = ""
    for line in output.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            active_command = value[4:]
            continue
        if active_command == "LC_BUILD_VERSION" and value.startswith("minos "):
            versions.append(value.split(None, 1)[1])
            active_command = ""
        elif active_command == "LC_VERSION_MIN_MACOSX" and value.startswith("version "):
            versions.append(value.split(None, 1)[1])
            active_command = ""
    return tuple(versions)


def _validate_binary_minimum_system_version(path: Path, maximum: str) -> None:
    try:
        result = subprocess.run(
            [OTOOL_PATH, "-l", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_sanitized_macos_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MacOSReleaseError(
            f"Could not inspect macOS deployment target: {path}"
        ) from error
    versions = _parse_macos_minimum_versions(result.stdout)
    if result.returncode != 0 or not versions:
        raise MacOSReleaseError(f"Could not inspect macOS deployment target: {path}")
    maximum_version = _version_tuple(maximum)
    unsupported = sorted(
        version for version in versions if _version_tuple(version) > maximum_version
    )
    if unsupported:
        raise MacOSReleaseError(
            f"macOS binary requires newer than macOS {maximum}: {path}; "
            f"minimum_versions={unsupported}"
        )


def _validate_bundle_macho_files(
    contents: Path,
    *,
    required_architectures: set[str],
    minimum_system_version: str,
) -> tuple[Path, ...]:
    binaries = tuple(
        path
        for path in contents.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() != ".a"
        and _is_macho_binary(path)
    )
    if not binaries:
        raise MacOSReleaseError("The macOS app contains no Mach-O binaries.")
    for path in binaries:
        _validate_binary_architectures(path, required_architectures)
        _validate_binary_minimum_system_version(path, minimum_system_version)
        unsafe_dependencies = _unsafe_bundle_dependencies(
            _inspect_macho_dependencies(path)
        )
        if unsafe_dependencies:
            raise MacOSReleaseError(
                "macOS bundle binary has non-portable dependency references: "
                f"{path}; {list(unsafe_dependencies)}"
            )
    return binaries


def _dmg_name_for_architecture(configured_name: str, architecture: str) -> str:
    if architecture == "universal2":
        return configured_name
    path = Path(configured_name)
    return f"{path.stem}-{architecture}{path.suffix}"


def validate_macos_configuration(
    *,
    require_tools: bool = False,
    target_architecture: str = "",
) -> dict[str, Any]:
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
        "minimum_system_version": "15.0",
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
        "com.apple.security.cs.disable-library-validation",
    }
    if any(entitlements.get(key) is not True for key in required_entitlements):
        raise MacOSReleaseError("Qt WebEngine runtime entitlements are incomplete.")

    binaries = macos.get("binaries")
    if not isinstance(binaries, list) or len(binaries) != 2:
        raise MacOSReleaseError("macOS FFmpeg binary configuration is incomplete.")
    configured_names: set[str] = set()
    required_architectures = (
        _required_architectures(target_architecture) if require_tools else set()
    )
    tool_details: list[dict[str, str]] = []
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
            tool_path = _exact_case_project_path(source)
            _validate_binary_architectures(tool_path, required_architectures)
            tool_details.append(_validate_tool(tool_path))
    if configured_names != {"ffmpeg", "ffprobe"}:
        raise MacOSReleaseError("macOS FFmpeg and FFprobe must both be configured.")
    tool_provenance = (
        _ffmpeg_provenance_summary(
            _normalized_target_architecture(target_architecture)
        )
        if require_tools
        else {}
    )

    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    for required in (
        "BUNDLE(",
        "bundle_identifier",
        "CFBundleVersion",
        "macos_entitlements.plist",
    ):
        if required not in spec_text:
            raise MacOSReleaseError(f"PyInstaller macOS bundle setting is missing: {required}")

    return {
        "app": MACOS_APP_NAME,
        "bundle_identifier": MACOS_BUNDLE_IDENTIFIER,
        "dmg": MACOS_DISK_IMAGE_NAME,
        "version": APPLICATION_VERSION,
        "architecture": (
            _normalized_target_architecture(target_architecture)
            if require_tools
            else "not checked"
        ),
        "tool_details": tool_details,
        "tool_provenance": tool_provenance,
        "tools": "validated" if require_tools else "required on macOS build host",
    }


def _clean_output_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != MACOS_ROOT.resolve():
        raise MacOSReleaseError(f"Refusing to clean unsafe path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _run_checked(command: list[str], *, label: str) -> None:
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        env=_sanitized_macos_environment(),
    )
    if result.returncode != 0:
        raise MacOSReleaseError(f"{label} failed with exit code {result.returncode}.")


def _run_captured(command: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_sanitized_macos_environment(),
    )
    if result.returncode != 0:
        raise MacOSReleaseError(f"{label} failed with exit code {result.returncode}.")
    return result


def _validate_developer_id_identity(identity: str) -> None:
    if not identity.startswith("Developer ID Application:"):
        raise MacOSReleaseError(
            "Public macOS releases require a Developer ID Application identity."
        )
    result = _run_captured(
        ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
        label="macOS signing-identity validation",
    )
    if identity not in result.stdout:
        raise MacOSReleaseError(
            "The configured Developer ID Application identity is unavailable."
        )


def _is_approved_resource_path(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return parts[:2] == ("resources", "resources") or any(
        parts[index : index + 2] == ("_internal", "resources")
        for index in range(max(0, len(parts) - 1))
    )


def _validate_bundle_sanitation(
    contents: Path,
    manifest: dict[str, Any],
) -> tuple[Path, ...]:
    settings = manifest.get("release_sanitation")
    if not isinstance(settings, dict):
        raise MacOSReleaseError("Release sanitation settings are missing.")
    blocked_parts = {
        str(value).casefold()
        for value in settings.get("distribution_forbidden_path_parts", ())
    }
    blocked_names = {
        str(value).casefold()
        for value in settings.get("distribution_forbidden_file_names", ())
    }
    blocked_suffixes = {
        str(value).casefold()
        for value in settings.get("distribution_forbidden_suffixes", ())
    }
    files: list[Path] = []
    for path in contents.rglob("*"):
        relative = path.relative_to(contents)
        if path.is_symlink():
            continue
        if any(part.casefold() in blocked_parts for part in relative.parts):
            raise MacOSReleaseError(
                f"Forbidden development or user-data path in macOS app: {relative}"
            )
        if not path.is_file():
            continue
        if (
            path.name.casefold() in blocked_names
            and not _is_approved_resource_path(relative)
        ):
            raise MacOSReleaseError(
                f"Forbidden development or user-data file in macOS app: {relative}"
            )
        if path.suffix.casefold() in blocked_suffixes:
            raise MacOSReleaseError(
                f"Forbidden development artifact in macOS app: {relative}"
            )
        files.append(path)
    return tuple(files)


def _parse_codesign_details(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        flags = re.search(r"(?:^|\s)flags=([^\s]+)", line, flags=re.IGNORECASE)
        if flags:
            values.setdefault("flags", flags.group(1))
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip().casefold()
        if normalized_key == "signature size":
            normalized_key = "signature"
        if normalized_key in {
            "authority",
            "flags",
            "signature",
            "teamidentifier",
            "timestamp",
        }:
            values.setdefault(normalized_key, value.strip())
    return {
        "authority": values.get("authority", ""),
        "flags": values.get("flags", ""),
        "signature": values.get("signature", ""),
        "team_identifier": values.get("teamidentifier", ""),
        "timestamp": values.get("timestamp", ""),
    }


def _signature_details(path: Path) -> dict[str, str]:
    result = _run_captured(
        [CODESIGN_PATH, "--display", "--verbose=4", str(path)],
        label=f"macOS signature inspection for {path.name}",
    )
    return _parse_codesign_details(result.stdout + "\n" + result.stderr)


def _validate_bundle_signatures(
    app_path: Path,
    binaries: tuple[Path, ...],
    *,
    developer_identity: str,
) -> dict[str, str]:
    team_identifiers: set[str] = set()
    authorities: set[str] = set()
    for path in binaries:
        _run_checked(
            [CODESIGN_PATH, "--verify", "--strict", "--verbose=2", str(path)],
            label=f"macOS nested signature verification for {path.name}",
        )
        details = _signature_details(path)
        team = details["team_identifier"]
        if team and team.casefold() != "not set":
            team_identifiers.add(team)
        if details["authority"]:
            authorities.add(details["authority"])
        if developer_identity and (
            details["authority"] != developer_identity
            or "runtime" not in details["flags"].casefold()
            or not details["timestamp"]
        ):
            raise MacOSReleaseError(
                "Nested macOS code lacks the expected Developer ID, hardened "
                f"runtime, or timestamp: {path}"
            )
    if len(team_identifiers) > 1:
        raise MacOSReleaseError(
            "Nested macOS binaries were signed by different Apple teams."
        )
    if developer_identity and not team_identifiers:
        raise MacOSReleaseError(
            "Developer ID signing did not produce a TeamIdentifier."
        )
    if developer_identity and len(authorities) != 1:
        raise MacOSReleaseError(
            "Nested macOS binaries were not signed with one Developer ID authority."
        )
    _run_checked(
        [
            CODESIGN_PATH,
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(app_path),
        ],
        label="macOS app code-signature verification",
    )
    app_details = _signature_details(app_path)
    app_team = app_details["team_identifier"]
    if developer_identity and (
        app_details["authority"] != developer_identity
        or "runtime" not in app_details["flags"].casefold()
        or not app_details["timestamp"]
    ):
        raise MacOSReleaseError(
            "The macOS app lacks the expected Developer ID, hardened runtime, or timestamp."
        )
    if team_identifiers and app_team not in team_identifiers:
        raise MacOSReleaseError(
            "The macOS app and its nested binaries have inconsistent signing teams."
        )
    return {
        "mode": "Developer ID" if developer_identity else "ad-hoc",
        "authority": app_details["authority"] or "ad-hoc",
        "team_identifier": app_team or "not set",
    }


def _validate_bundle(
    app_path: Path,
    manifest: dict[str, Any],
    *,
    required_architectures: set[str],
    developer_identity: str,
) -> dict[str, Any]:
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
        "CFBundleVersion": manifest["product"]["version"],
        "LSMinimumSystemVersion": macos["minimum_system_version"],
    }
    for key, value in expected_info.items():
        if str(info.get(key, "")) != value:
            raise MacOSReleaseError(f"macOS bundle metadata differs at {key}.")

    executable = contents / "MacOS" / macos["executable"]
    frameworks = contents / "Frameworks"
    resources = contents / "Resources"
    if not executable.is_file() or executable.stat().st_size < 1_000_000:
        raise MacOSReleaseError("The macOS application executable is incomplete.")
    if not os.access(executable, os.X_OK):
        raise MacOSReleaseError("The macOS application executable is not executable.")

    resource_markers = (
        "resources/stock/stock_manifest.json",
        "resources/examples/example_letter/lettersmith-metadata.json",
        "gallery/app/icons/folder/lsmith.png",
        "tools/FFmpeg-PROVENANCE.txt",
    )
    resource_internal = next(
        (
            root
            for root in (resources / "_internal", resources)
            if root.is_dir()
            and all((root / relative).is_file() for relative in resource_markers)
        ),
        None,
    )
    framework_internal = next(
        (
            root
            for root in (frameworks / "_internal", frameworks)
            if root.is_dir()
            and all((root / "tools" / name).is_file() for name in ("ffmpeg", "ffprobe"))
        ),
        None,
    )
    if resource_internal is None or framework_internal is None:
        raise MacOSReleaseError(
            "The macOS Frameworks/Resources payload layout is incomplete."
        )

    icon_name = str(info.get("CFBundleIconFile", "")).strip()
    bundle_icon = resources / Path(icon_name).name
    try:
        icon_header = bundle_icon.read_bytes()[:4]
    except OSError as error:
        raise MacOSReleaseError("The macOS bundle icon is missing.") from error
    if not icon_name or icon_header != b"icns" or bundle_icon.stat().st_size < 100_000:
        raise MacOSReleaseError("The macOS bundle icon is invalid.")

    contents_root = contents.resolve()
    for path in contents.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(contents_root)
        except (OSError, ValueError) as error:
            raise MacOSReleaseError(f"Unsafe macOS bundle link: {path}") from error

    packaged_tools: list[dict[str, str]] = []
    for name in ("ffmpeg", "ffprobe"):
        path = framework_internal / "tools" / name
        if not path.is_file() or path.is_symlink():
            raise MacOSReleaseError(f"The macOS app is missing tools/{name}.")
        packaged_tools.append(_validate_tool(path))

    webengine_helpers = tuple(
        path
        for path in contents.rglob("QtWebEngineProcess")
        if path.is_file() and not path.is_symlink()
    )
    if not webengine_helpers:
        raise MacOSReleaseError("Qt WebEngine helper is missing from the macOS app.")
    if any(not os.access(path, os.X_OK) for path in webengine_helpers):
        raise MacOSReleaseError("A Qt WebEngine helper is not executable.")
    if not any(
        path.is_file() and not path.is_symlink()
        for path in contents.rglob("libqcocoa.dylib")
    ):
        raise MacOSReleaseError("Qt Cocoa platform plugin is missing from the macOS app.")
    required_webengine_resources = {
        "icudtl.dat",
        "qtwebengine_resources.pak",
        "qtwebengine_resources_100p.pak",
        "qtwebengine_resources_200p.pak",
    }
    present_webengine_resources = {
        path.name.casefold()
        for path in contents.rglob("*")
        if path.is_file() and path.name.casefold() in required_webengine_resources
    }
    if present_webengine_resources != required_webengine_resources:
        missing = sorted(required_webengine_resources - present_webengine_resources)
        raise MacOSReleaseError(f"Qt WebEngine resources are incomplete: {missing}")
    if not any(
        path.is_file()
        and path.suffix.casefold() == ".pak"
        and "qtwebengine_locales" in {part.casefold() for part in path.parts}
        for path in contents.rglob("*.pak")
    ):
        raise MacOSReleaseError("Qt WebEngine locale resources are missing.")

    try:
        _validate_frozen_payload(resource_internal, manifest, "darwin")
    except ReleaseValidationError as error:
        raise MacOSReleaseError(str(error)) from error
    files = _validate_bundle_sanitation(contents, manifest)
    binaries = _validate_bundle_macho_files(
        contents,
        required_architectures=required_architectures,
        minimum_system_version=macos["minimum_system_version"],
    )
    _validate_private_text(
        files,
        context="macOS app bundle",
        include_secret_literals=False,
    )
    _validate_private_binary_markers(files)
    signing = _validate_bundle_signatures(
        app_path,
        binaries,
        developer_identity=developer_identity,
    )
    return {
        "executable": executable,
        "macho_file_count": len(binaries),
        "packaged_tools": packaged_tools,
        "signing": signing,
    }


def _create_dmg(app_path: Path, dmg_path: Path, *, identity: str) -> None:
    dmg_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="lettersmith-dmg-") as directory:
        staging_root = Path(directory)
        staged_app = staging_root / app_path.name
        _run_checked(
            [DITTO_PATH, str(app_path), str(staged_app)],
            label="macOS disk-image app staging",
        )
        try:
            (staging_root / "Applications").symlink_to(
                "/Applications",
                target_is_directory=True,
            )
        except OSError as error:
            raise MacOSReleaseError(
                "Could not create the macOS disk-image Applications link."
            ) from error
        _run_checked(
            [
                HDIUTIL_PATH,
                "create",
                "-volname",
                "Letter Smith",
                "-srcfolder",
                str(staging_root),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ],
            label="macOS disk-image creation",
        )
    if identity:
        _run_checked(
            [
                CODESIGN_PATH,
                "--force",
                "--timestamp",
                "--sign",
                identity,
                str(dmg_path),
            ],
            label="macOS disk-image signing",
        )
        _run_checked(
            [CODESIGN_PATH, "--verify", "--strict", "--verbose=2", str(dmg_path)],
            label="macOS disk-image signature verification",
        )
    _run_checked(
        [HDIUTIL_PATH, "verify", str(dmg_path)],
        label="macOS disk-image verification",
    )


def _notarize(dmg_path: Path) -> dict[str, str]:
    profile = os.environ.get("LETTER_SMITH_MACOS_NOTARY_PROFILE", "").strip()
    if not profile:
        raise MacOSReleaseError(
            "Set LETTER_SMITH_MACOS_NOTARY_PROFILE to a notarytool keychain profile."
        )
    result = _run_captured(
        [
            XCRUN_PATH,
            "notarytool",
            "submit",
            str(dmg_path),
            "--keychain-profile",
            profile,
            "--wait",
            "--output-format",
            "json",
        ],
        label="Apple notarization",
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MacOSReleaseError("Apple notarization returned invalid JSON.") from error
    status = str(response.get("status", "")).strip()
    submission_id = str(response.get("id", "")).strip()
    if status.casefold() != "accepted" or not submission_id:
        raise MacOSReleaseError(
            f"Apple notarization was not accepted: status={status or 'unknown'}"
        )
    _run_checked(
        [XCRUN_PATH, "stapler", "staple", str(dmg_path)],
        label="Apple notarization stapling",
    )
    _run_checked(
        [XCRUN_PATH, "stapler", "validate", str(dmg_path)],
        label="Apple notarization validation",
    )
    return {"status": status, "submission_id": submission_id}


def _validate_dmg_mount(
    dmg_path: Path,
    app_name: str,
    *,
    assess_gatekeeper: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="lettersmith-dmg-") as directory:
        mount_point = Path(directory) / "volume"
        mount_point.mkdir()
        _run_checked(
            [
                HDIUTIL_PATH,
                "attach",
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                str(mount_point),
                str(dmg_path),
            ],
            label="macOS disk-image mount",
        )
        try:
            mounted_app = mount_point / app_name
            if not mounted_app.is_dir():
                raise MacOSReleaseError(
                    f"The mounted disk image is missing {app_name}."
                )
            if assess_gatekeeper:
                _run_checked(
                    [
                        SPCTL_PATH,
                        "--assess",
                        "--type",
                        "execute",
                        "--verbose=2",
                        str(mounted_app),
                    ],
                    label="Gatekeeper application assessment",
                )
        finally:
            _run_checked(
                [HDIUTIL_PATH, "detach", str(mount_point)],
                label="macOS disk-image detach",
            )


def _directory_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in path.rglob("*")
        if candidate.is_file() and not candidate.is_symlink()
    )


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        if candidate.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(candidate).encode("utf-8"))
            continue
        if not candidate.is_file():
            digest.update(b"directory\0")
            continue
        digest.update(b"file\0")
        with candidate.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(path: Path) -> dict[str, Any]:
    if path.is_dir():
        size = _directory_size(path)
        checksum = _tree_sha256(path)
        checksum_kind = "sha256-tree"
    else:
        size = path.stat().st_size
        checksum = _sha256(path)
        checksum_kind = "sha256"
    return {
        "path": _project_relative_path(path),
        "size_bytes": size,
        checksum_kind: checksum,
    }


def _release_report_path(architecture: str) -> Path:
    return MACOS_ROOT / f"release-report-{architecture}.json"


def _write_release_report(
    *,
    architecture: str,
    app_path: Path,
    dmg_path: Path,
    manifest: dict[str, Any],
    source_tools: list[dict[str, str]],
    tool_provenance: dict[str, Any],
    bundle_validation: dict[str, Any],
    notarization: dict[str, str],
) -> Path:
    macos = _macos_configuration(manifest)
    report = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "product": {
            "name": manifest["product"]["name"],
            "version": manifest["product"]["version"],
            "bundle_identifier": macos["bundle_identifier"],
            "minimum_system_version": macos["minimum_system_version"],
            "architecture": architecture,
        },
        "build_host": {
            "macos": platform.mac_ver()[0],
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "source_tools": source_tools,
        "tool_provenance": tool_provenance,
        "packaged_tools": bundle_validation["packaged_tools"],
        "macho_file_count": bundle_validation["macho_file_count"],
        "signing": bundle_validation["signing"],
        "notarization": notarization,
        "artifacts": {
            "application": _artifact_record(app_path),
            "disk_image": _artifact_record(dmg_path),
        },
    }
    report_path = _release_report_path(architecture)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def build_macos_release(
    *,
    notarize: bool = False,
    ffmpeg_source_dir: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    if sys.platform != "darwin":
        raise MacOSReleaseError("Native Letter Smith macOS builds require macOS.")
    if importlib.util.find_spec("PyInstaller") is None:
        raise MacOSReleaseError("PyInstaller is not installed on the macOS build host.")
    configured_architecture = os.environ.get(
        "LETTER_SMITH_MACOS_TARGET_ARCH",
        "",
    ).strip()
    if notarize and not configured_architecture:
        raise MacOSReleaseError(
            "Notarized builds require LETTER_SMITH_MACOS_TARGET_ARCH."
        )
    architecture = _normalized_target_architecture(configured_architecture)
    from release.provision_ffmpeg import FFmpegProvisionError, provision_ffmpeg

    try:
        provision_ffmpeg(
            "darwin",
            architecture,
            source_dir=ffmpeg_source_dir,
        )
    except FFmpegProvisionError as error:
        raise MacOSReleaseError(str(error)) from error
    summary = validate_macos_configuration(
        require_tools=True,
        target_architecture=architecture,
    )
    try:
        validate_release_inputs("darwin")
    except ReleaseValidationError as error:
        raise MacOSReleaseError(str(error)) from error

    manifest = _load_json(MANIFEST_PATH)
    allowed_architectures = set(_macos_configuration(manifest)["supported_architectures"])
    if architecture not in allowed_architectures:
        raise MacOSReleaseError(f"Unsupported macOS target architecture: {architecture}")
    required_architectures = _required_architectures(architecture)
    identity = os.environ.get("LETTER_SMITH_MACOS_CODESIGN_IDENTITY", "").strip()
    if notarize and not identity:
        raise MacOSReleaseError("Apple notarization requires a Developer ID identity.")
    if notarize:
        _validate_developer_id_identity(identity)
    if notarize and not os.environ.get("LETTER_SMITH_MACOS_NOTARY_PROFILE", "").strip():
        raise MacOSReleaseError(
            "Set LETTER_SMITH_MACOS_NOTARY_PROFILE to a notarytool keychain profile."
        )

    MACOS_ROOT.mkdir(parents=True, exist_ok=True)
    _clean_output_directory(BUILD_ROOT)
    _clean_output_directory(DIST_ROOT)
    environment = _sanitized_macos_environment()
    environment["MACOSX_DEPLOYMENT_TARGET"] = _macos_configuration(manifest)[
        "minimum_system_version"
    ]
    environment["LETTER_SMITH_MACOS_TARGET_ARCH"] = architecture
    environment["PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR"] = "1"
    environment["PYINSTALLER_VERIFY_BUNDLE_SIGNATURE"] = "1"
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
    bundle_validation = _validate_bundle(
        app_path,
        manifest,
        required_architectures=required_architectures,
        developer_identity=identity,
    )
    dmg_path = MACOS_ROOT / _dmg_name_for_architecture(summary["dmg"], architecture)
    _create_dmg(app_path, dmg_path, identity=identity)
    notarization = {"status": "not requested", "submission_id": ""}
    if notarize:
        notarization = _notarize(dmg_path)
        _run_checked(
            [
                SPCTL_PATH,
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=2",
                str(dmg_path),
            ],
            label="Gatekeeper disk-image assessment",
        )
    _validate_dmg_mount(
        dmg_path,
        summary["app"],
        assess_gatekeeper=notarize,
    )
    report_path = _write_release_report(
        architecture=architecture,
        app_path=app_path,
        dmg_path=dmg_path,
        manifest=manifest,
        source_tools=summary["tool_details"],
        tool_provenance=summary["tool_provenance"],
        bundle_validation=bundle_validation,
        notarization=notarization,
    )
    return app_path, dmg_path, report_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or build the native Letter Smith macOS release."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configuration-only", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    parser.add_argument("--confirm-package", action="store_true")
    parser.add_argument("--notarize", action="store_true")
    parser.add_argument(
        "--ffmpeg-source-dir",
        help="Stage pinned FFmpeg/FFprobe from this directory instead of downloading.",
    )
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
        if args.configuration_only:
            summary = validate_macos_configuration(require_tools=False)
            print(
                "macOS configuration validated: "
                f"app={summary['app']}, bundle_id={summary['bundle_identifier']}"
            )
            print("Native tools, architectures, signing, and packaging were not checked.")
            return 0
        if sys.platform != "darwin":
            raise MacOSReleaseError(
                "Native macOS validation and builds require macOS; "
                "use --configuration-only for a cross-platform metadata check."
            )
        if not args.build:
            architecture = _normalized_target_architecture(
                os.environ.get("LETTER_SMITH_MACOS_TARGET_ARCH", "")
            )
            from release.provision_ffmpeg import (
                FFmpegProvisionError,
                provision_ffmpeg,
            )

            try:
                provision_ffmpeg(
                    "darwin",
                    architecture,
                    source_dir=args.ffmpeg_source_dir,
                )
            except FFmpegProvisionError as error:
                raise MacOSReleaseError(str(error)) from error
            summary = validate_macos_configuration(
                require_tools=True,
                target_architecture=architecture,
            )
            validate_release_inputs("darwin")
            print(
                "Native macOS release inputs validated: "
                f"app={summary['app']}, architecture={architecture}"
            )
            return 0
        app_path, dmg_path, report_path = build_macos_release(
            notarize=args.notarize,
            ffmpeg_source_dir=args.ffmpeg_source_dir,
        )
        print(f"Built and verified: {app_path}")
        print(f"Disk image: {dmg_path}")
        print(f"Release report: {report_path}")
        return 0
    except (MacOSReleaseError, ReleaseValidationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
