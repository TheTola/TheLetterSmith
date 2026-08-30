from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THUMBPRINT_PATTERN = re.compile(r"[0-9A-F]{40}")
DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


class WindowsSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowsSigningConfiguration:
    signtool: Path
    certificate_sha1: str
    timestamp_url: str

    def sign_arguments(self, target: Path | str) -> list[str]:
        return [
            str(self.signtool),
            "sign",
            "/sha1",
            self.certificate_sha1,
            "/fd",
            "SHA256",
            "/tr",
            self.timestamp_url,
            "/td",
            "SHA256",
            "/d",
            "Letter Smith",
            str(target),
        ]

    def inno_definition(self) -> str:
        return (
            f"$q{self.signtool}$q sign /sha1 {self.certificate_sha1} "
            f"/fd SHA256 /tr {self.timestamp_url} /td SHA256 "
            "/d $qLetter Smith$q $f"
        )


def _version_key(path: Path) -> tuple[int, ...]:
    values = re.findall(r"\d+", path.parent.parent.name)
    return tuple(int(value) for value in values)


def find_signtool() -> Path | None:
    candidates: list[Path] = []
    override = os.environ.get("LETTER_SMITH_SIGNTOOL", "").strip()
    if override:
        candidates.append(Path(override))
    discovered = shutil.which("signtool.exe") or shutil.which("signtool")
    if discovered:
        candidates.append(Path(discovered))
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable, "").strip()
        if not root:
            continue
        kit_root = Path(root) / "Windows Kits" / "10" / "bin"
        candidates.extend(sorted(kit_root.glob("*/x64/signtool.exe"), key=_version_key, reverse=True))
        candidates.append(kit_root / "x64" / "signtool.exe")
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    return None


def load_signing_configuration() -> WindowsSigningConfiguration:
    signtool = find_signtool()
    if signtool is None:
        raise WindowsSigningError(
            "SignTool was not found. Install the Windows SDK or set LETTER_SMITH_SIGNTOOL."
        )
    thumbprint = re.sub(
        r"\s+",
        "",
        os.environ.get("LETTER_SMITH_WINDOWS_CODESIGN_SHA1", ""),
    ).upper()
    if THUMBPRINT_PATTERN.fullmatch(thumbprint) is None:
        raise WindowsSigningError(
            "Set LETTER_SMITH_WINDOWS_CODESIGN_SHA1 to the 40-digit SHA-1 "
            "thumbprint of the code-signing certificate in the Windows certificate store."
        )
    timestamp_url = os.environ.get(
        "LETTER_SMITH_WINDOWS_TIMESTAMP_URL",
        DEFAULT_TIMESTAMP_URL,
    ).strip()
    parsed = urlsplit(timestamp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(
        character.isspace() for character in timestamp_url
    ):
        raise WindowsSigningError("The Windows timestamp URL is invalid.")
    return WindowsSigningConfiguration(signtool, thumbprint, timestamp_url)


def _run(arguments: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WindowsSigningError(f"{label} could not run.") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        suffix = f" {detail}" if detail else ""
        raise WindowsSigningError(
            f"{label} failed with exit code {result.returncode}.{suffix}"
        )
    return result


def sign_file(path: Path, configuration: WindowsSigningConfiguration) -> None:
    target = path.resolve()
    if not target.is_file():
        raise WindowsSigningError(f"Cannot sign missing file: {target}")
    _run(configuration.sign_arguments(target), label=f"Signing {target.name}")
    verify_signature(target, configuration)


def verify_signature(
    path: Path,
    configuration: WindowsSigningConfiguration,
) -> None:
    target = path.resolve()
    _run(
        [
            str(configuration.signtool),
            "verify",
            "/pa",
            "/all",
            "/tw",
            str(target),
        ],
        label=f"Signature verification for {target.name}",
    )
