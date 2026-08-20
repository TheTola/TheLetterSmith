from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "release"
MANIFEST_PATH = RELEASE_ROOT / "release_manifest.json"
INSTALLER_SCRIPT = RELEASE_ROOT / "LetterSmith.iss"
DIST_ROOT = RELEASE_ROOT / "dist" / "LetterSmith"
OUTPUT_ROOT = RELEASE_ROOT / "installer"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class InstallerValidationError(RuntimeError):
    pass


def _load_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallerValidationError(
            f"Unreadable release manifest: {MANIFEST_PATH}"
        ) from error
    if not isinstance(value, dict):
        raise InstallerValidationError("Release manifest root must be an object.")
    return value


def _read_installer_script() -> str:
    try:
        return INSTALLER_SCRIPT.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InstallerValidationError(
            f"Unreadable Inno Setup script: {INSTALLER_SCRIPT}"
        ) from error


def _definitions(text: str) -> dict[str, str]:
    return {
        name: value
        for name, value in re.findall(
            r'^#define\s+([A-Za-z][A-Za-z0-9_]*)\s+"([^"]*)"\s*$',
            text,
            flags=re.MULTILINE,
        )
    }


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"(?ims)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[[^\]]+\]\s*$|\Z)",
        text,
    )
    if match is None:
        raise InstallerValidationError(f"Missing [{name}] section.")
    return match.group(1)


def _directive(text: str, name: str) -> str:
    setup = _section(text, "Setup")
    matches = re.findall(
        rf"(?im)^{re.escape(name)}\s*=\s*(.*?)\s*$",
        setup,
    )
    if len(matches) != 1:
        raise InstallerValidationError(
            f"Expected one {name} directive, found {len(matches)}."
        )
    return matches[0]


def _require_directive(text: str, name: str, expected: str) -> None:
    actual = _directive(text, name)
    if actual != expected:
        raise InstallerValidationError(
            f"{name} must be {expected!r}, found {actual!r}."
        )


def _active_lines(text: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    )


def validate_installer_configuration() -> dict[str, str]:
    from application_identity import (
        APPLICATION_NAME,
        APPLICATION_VERSION,
        EXECUTABLE_NAME,
        INSTALLER_APP_ID,
        INSTALLER_NAME,
        PUBLISHER_NAME,
    )

    manifest = _load_manifest()
    expected_product = {
        "name": APPLICATION_NAME,
        "version": APPLICATION_VERSION,
        "publisher": PUBLISHER_NAME,
        "executable": EXECUTABLE_NAME,
    }
    expected_installer = {
        "name": INSTALLER_NAME,
        "app_id": INSTALLER_APP_ID,
        "start_menu_name": APPLICATION_NAME,
        "desktop_shortcut_default": False,
    }
    if manifest.get("product") != expected_product:
        raise InstallerValidationError(
            "Release product identity differs from application_identity.py."
        )
    if manifest.get("installer") != expected_installer:
        raise InstallerValidationError(
            "Release installer identity differs from application_identity.py."
        )

    text = _read_installer_script()
    definitions = _definitions(text)
    expected_definitions = {
        "MyAppName": APPLICATION_NAME,
        "MyAppVersion": APPLICATION_VERSION,
        "MyAppPublisher": PUBLISHER_NAME,
        "MyAppExeName": EXECUTABLE_NAME,
        "MyInstallerBaseName": INSTALLER_NAME.removesuffix(".exe"),
    }
    if definitions != expected_definitions:
        raise InstallerValidationError(
            "Inno Setup definitions differ from the authoritative identity."
        )

    required_directives = {
        "AppId": "{" + INSTALLER_APP_ID,
        "AppName": "{#MyAppName}",
        "AppVersion": "{#MyAppVersion}",
        "AppPublisher": "{#MyAppPublisher}",
        "DefaultDirName": r"{autopf}\Infini Works\Letter Smith",
        "DefaultGroupName": "{#MyAppName}",
        "OutputDir": "installer",
        "OutputBaseFilename": "{#MyInstallerBaseName}",
        "SetupIconFile": r"..\resources\app\icons\folder\lsmith.ico",
        "CloseApplications": "yes",
        "RestartApplications": "no",
        "PrivilegesRequired": "admin",
        "ArchitecturesAllowed": "x64compatible",
        "ArchitecturesInstallIn64BitMode": "x64compatible",
        "MinVersion": "10.0.10240",
        "UsePreviousAppDir": "yes",
        "UsePreviousGroup": "yes",
        "UsePreviousTasks": "yes",
        "CreateUninstallRegKey": "yes",
        "Uninstallable": "yes",
        "SetupLogging": "yes",
        "ChangesAssociations": "no",
        "ChangesEnvironment": "no",
        "VersionInfoVersion": "1.0.0.0",
        "VersionInfoProductVersion": "1.0.0.0",
        "VersionInfoProductTextVersion": "{#MyAppVersion}",
    }
    for name, expected in required_directives.items():
        _require_directive(text, name, expected)

    source_lines = re.findall(r"(?im)^Source:\s*.+$", text)
    expected_source = (
        'Source: "dist\\LetterSmith\\*"; DestDir: "{app}"; '
        "Flags: ignoreversion recursesubdirs createallsubdirs"
    )
    if source_lines != [expected_source]:
        raise InstallerValidationError(
            "The installer must consume only release\\dist\\LetterSmith."
        )

    tasks = _section(text, "Tasks")
    if not re.search(
        r'(?im)^Name:\s*"desktopicon";.*?Flags:\s*unchecked\s*$',
        tasks,
    ):
        raise InstallerValidationError(
            "The optional desktop shortcut must default to unchecked."
        )

    icons = _section(text, "Icons")
    expected_start_menu = (
        'Name: "{autoprograms}\\{#MyAppName}"; '
        'Filename: "{app}\\{#MyAppExeName}"; WorkingDir: "{app}"'
    )
    if expected_start_menu not in icons:
        raise InstallerValidationError("The Start Menu shortcut is missing.")
    if "[UninstallDelete]" in text:
        raise InstallerValidationError(
            "The installer must not delete user data during uninstall."
        )

    active_text = "\n".join(_active_lines(text)).casefold()
    forbidden_user_paths = (
        "{localappdata}",
        "{userappdata}",
        "{userdocs}",
        "%localappdata%",
        "%userprofile%",
        r"documents\letter smith",
    )
    for value in forbidden_user_paths:
        if value.casefold() in active_text:
            raise InstallerValidationError(
                f"Installer actions must not target writable user data: {value}"
            )

    icon_path = (RELEASE_ROOT / _directive(text, "SetupIconFile")).resolve()
    expected_icon = (
        PROJECT_ROOT / "resources" / "app" / "icons" / "folder" / "lsmith.ico"
    ).resolve()
    if icon_path != expected_icon or not icon_path.is_file():
        raise InstallerValidationError("The installer icon is missing or unsafe.")

    return {
        "name": INSTALLER_NAME,
        "app_id": INSTALLER_APP_ID,
        "payload": str(DIST_ROOT),
    }


def _find_iscc() -> Path | None:
    candidates: list[Path] = []
    override = os.environ.get("LETTER_SMITH_ISCC", "").strip()
    if override:
        candidates.append(Path(override))
    discovered = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if discovered:
        candidates.append(Path(discovered))
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable, "").strip()
        if root:
            candidates.extend(
                Path(root) / directory / "ISCC.exe"
                for directory in ("Inno Setup 7", "Inno Setup 6")
            )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    return None


def _distribution_executable() -> Path:
    from release.build_release import (
        ReleaseValidationError,
        validate_distribution,
    )

    try:
        return validate_distribution()
    except ReleaseValidationError as error:
        raise InstallerValidationError(
            "The one-folder distribution did not pass the release sanitation gate: "
            f"{error}"
        ) from error


def _prepare_output_directory() -> None:
    resolved = OUTPUT_ROOT.resolve()
    if resolved.parent != RELEASE_ROOT.resolve():
        raise InstallerValidationError(f"Refusing to clean unsafe path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def build_installer() -> Path:
    from application_identity import INSTALLER_NAME

    if sys.platform != "win32":
        raise InstallerValidationError("Letter Smith installers require Windows.")
    _distribution_executable()
    compiler = _find_iscc()
    if compiler is None:
        raise InstallerValidationError(
            "Inno Setup 6.3 or newer is not installed. Install it only after "
            "packaging is approved."
        )
    _prepare_output_directory()
    result = subprocess.run(
        [str(compiler), "/Qp", str(INSTALLER_SCRIPT)],
        cwd=RELEASE_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise InstallerValidationError(
            f"Inno Setup failed with exit code {result.returncode}."
        )
    output = OUTPUT_ROOT / INSTALLER_NAME
    if not output.is_file() or output.stat().st_size < 1_000_000:
        raise InstallerValidationError("The Windows installer was not produced correctly.")
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or build the Letter Smith 1.0.0 Windows installer."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate installer configuration without creating output (default).",
    )
    mode.add_argument(
        "--build",
        action="store_true",
        help="Create the Inno Setup installer from the frozen distribution.",
    )
    parser.add_argument(
        "--confirm-package",
        action="store_true",
        help="Required with --build to prevent an accidental package run.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.build and not args.confirm_package:
        print("ERROR: --build requires --confirm-package.", file=sys.stderr)
        return 2
    try:
        summary = validate_installer_configuration()
        print(
            "Installer configuration validated: "
            f"name={summary['name']}, app_id={summary['app_id']}"
        )
        if not args.build:
            state = "available" if _find_iscc() is not None else "not installed"
            print(f"Inno Setup: {state}; packaging was not run.")
            return 0
        output = build_installer()
        print(f"Built and verified: {output}")
        return 0
    except InstallerValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
