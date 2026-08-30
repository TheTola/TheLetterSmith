from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).with_name("ffmpeg_manifest.json")
CACHE_ROOT = PROJECT_ROOT / "tools" / "_ffmpeg_download"
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
USER_AGENT = "LetterSmith-Release-Provisioner/1.0"
MACOS_LIPO = "/usr/bin/lipo"
MACOS_XATTR = "/usr/bin/xattr"
MACOS_CODESIGN = "/usr/bin/codesign"
EXPLICIT_SOURCE_MANIFEST = "ffmpeg-source.json"


class FFmpegProvisionError(RuntimeError):
    pass


def _load_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FFmpegProvisionError(
            f"Unreadable FFmpeg provisioning manifest: {MANIFEST_PATH}"
        ) from error
    if not isinstance(value, dict):
        raise FFmpegProvisionError("FFmpeg provisioning manifest must be an object.")
    return value


def _safe_project_path(value: object) -> Path:
    relative = Path(str(value or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise FFmpegProvisionError(f"Unsafe FFmpeg provisioning path: {value!r}")
    resolved = (PROJECT_ROOT / relative).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise FFmpegProvisionError(
            f"FFmpeg provisioning path escapes the project: {relative}"
        ) from error
    return resolved


def _validated_cache_filename(value: object, *, suffix: str = "") -> str:
    if not isinstance(value, str):
        raise FFmpegProvisionError(f"Unsafe FFmpeg cache filename: {value!r}")
    filename = value.strip()
    posix = PurePosixPath(filename)
    windows = PureWindowsPath(filename)
    if (
        not filename
        or filename != value
        or filename in {".", ".."}
        or any(character in filename for character in "\x00\r\n")
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.name != filename
        or windows.name != filename
        or (suffix and not filename.endswith(suffix))
    ):
        raise FFmpegProvisionError(f"Unsafe FFmpeg cache filename: {value!r}")
    return filename


def _validated_cache_root() -> Path:
    try:
        project_root = PROJECT_ROOT.resolve()
        parent = CACHE_ROOT.parent.resolve()
        parent.relative_to(project_root)
        if CACHE_ROOT.is_symlink():
            raise FFmpegProvisionError(
                f"FFmpeg cache root must not be a symlink: {CACHE_ROOT}"
            )
        resolved = parent / CACHE_ROOT.name
        resolved.mkdir(parents=False, exist_ok=True)
        if not resolved.is_dir() or resolved.is_symlink():
            raise FFmpegProvisionError(f"Unsafe FFmpeg cache root: {CACHE_ROOT}")
        resolved.relative_to(project_root)
        return resolved
    except FFmpegProvisionError:
        raise
    except (OSError, ValueError) as error:
        raise FFmpegProvisionError(f"Unsafe FFmpeg cache root: {CACHE_ROOT}") from error


def _cache_destination(value: object, *, suffix: str = "") -> Path:
    filename = _validated_cache_filename(value, suffix=suffix)
    cache_root = _validated_cache_root()
    candidate = cache_root / filename
    try:
        if candidate.is_symlink():
            raise FFmpegProvisionError(
                f"FFmpeg cache entry must not be a symlink: {candidate}"
            )
        resolved = candidate.resolve()
        resolved.relative_to(cache_root)
    except FFmpegProvisionError:
        raise
    except (OSError, ValueError) as error:
        raise FFmpegProvisionError(f"Unsafe FFmpeg cache path: {candidate}") from error
    if resolved.parent != cache_root:
        raise FFmpegProvisionError(f"Unsafe FFmpeg cache path: {candidate}")
    return resolved


def validate_provisioning_manifest() -> dict[str, str]:
    manifest = _load_manifest()
    if manifest.get("schema_version") != 1:
        raise FFmpegProvisionError("Unsupported FFmpeg provisioning schema.")
    provenance = _safe_project_path(manifest.get("provenance_target"))
    if provenance.parent != PROJECT_ROOT / "tools":
        raise FFmpegProvisionError("FFmpeg provenance must be staged under tools.")

    platforms = manifest.get("platforms")
    expected_platforms = {
        "windows-x86_64",
        "macos-arm64",
        "macos-x86_64",
    }
    if not isinstance(platforms, dict) or set(platforms) != expected_platforms:
        raise FFmpegProvisionError(
            "FFmpeg provisioning must define Windows x86_64 and both macOS architectures."
        )
    for platform_key, configuration in platforms.items():
        if not isinstance(configuration, dict):
            raise FFmpegProvisionError(
                f"Invalid FFmpeg provisioning configuration: {platform_key}"
            )
        for field in ("version", "provider", "license"):
            if not str(configuration.get(field, "")).strip():
                raise FFmpegProvisionError(
                    f"FFmpeg provisioning {platform_key} is missing {field}."
                )
        tools = configuration.get("tools")
        if not isinstance(tools, list) or len(tools) != 2:
            raise FFmpegProvisionError(
                f"FFmpeg provisioning {platform_key} must define two tools."
            )
        if {item.get("name") for item in tools if isinstance(item, dict)} != {
            "ffmpeg",
            "ffprobe",
        }:
            raise FFmpegProvisionError(
                f"FFmpeg provisioning {platform_key} must define ffmpeg and ffprobe."
            )
        expected_parent = (
            PROJECT_ROOT / "tools"
            if platform_key.startswith("windows-")
            else PROJECT_ROOT / "tools" / "macos"
        )
        for item in tools:
            if not isinstance(item, dict):
                raise FFmpegProvisionError(
                    f"Invalid FFmpeg tool entry for {platform_key}."
                )
            target = _safe_project_path(item.get("target"))
            url = str(item.get("url", ""))
            parsed = urlsplit(url)
            hashes = (item.get("archive_sha256"), item.get("binary_sha256"))
            expected_name = f"{item['name']}.exe" if platform_key.startswith(
                "windows-"
            ) else item["name"]
            if target.parent != expected_parent:
                raise FFmpegProvisionError(
                    f"Invalid FFmpeg target for {platform_key}: {target}"
                )
            if target.name != expected_name:
                raise FFmpegProvisionError(
                    f"Invalid FFmpeg target filename for {platform_key}: {target.name}"
                )
            if parsed.scheme != "https" or not parsed.netloc:
                raise FFmpegProvisionError(f"Unsafe FFmpeg download URL: {url}")
            _validated_cache_filename(item.get("archive_name"), suffix=".zip")
            if Path(str(item.get("member", ""))).name not in {
                item["name"],
                f"{item['name']}.exe",
            }:
                raise FFmpegProvisionError("Invalid FFmpeg archive member.")
            if any(
                not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None
                for value in hashes
            ):
                raise FFmpegProvisionError("Invalid FFmpeg SHA-256 digest.")
    if (
        platforms["macos-arm64"]["version"]
        != platforms["macos-x86_64"]["version"]
    ):
        raise FFmpegProvisionError(
            "Universal2 FFmpeg inputs must use the same version."
        )
    return {
        "windows": platforms["windows-x86_64"]["version"],
        "macos": platforms["macos-arm64"]["version"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_hash(path: Path, expected: str) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and _sha256(path) == expected
    except OSError:
        return False


def _download(item: dict[str, str]) -> Path:
    destination = _cache_destination(item["archive_name"], suffix=".zip")
    expected = item["archive_sha256"]
    if _matches_hash(destination, expected):
        return destination
    if destination.exists():
        destination.unlink()

    request = urllib.request.Request(
        item["url"],
        headers={"User-Agent": USER_AGENT},
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = _sha256(temporary)
        if actual != expected:
            raise FFmpegProvisionError(
                f"FFmpeg archive checksum mismatch: {destination.name}; "
                f"expected={expected}, actual={actual}"
            )
        os.replace(temporary, destination)
        temporary = None
        return destination
    except FFmpegProvisionError:
        raise
    except (OSError, TimeoutError) as error:
        raise FFmpegProvisionError(
            f"Could not download FFmpeg archive: {item['url']}"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _extract_verified(
    archive: Path,
    *,
    member: str,
    expected_hash: str,
    destination: Path,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with zipfile.ZipFile(archive) as bundle:
            try:
                entry = bundle.getinfo(member)
            except KeyError as error:
                raise FFmpegProvisionError(
                    f"FFmpeg archive is missing {member}: {archive}"
                ) from error
            if entry.is_dir() or Path(entry.filename).name != Path(member).name:
                raise FFmpegProvisionError(
                    f"Unsafe FFmpeg archive member: {entry.filename}"
                )
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.",
                suffix=".staging",
                dir=destination.parent,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with bundle.open(entry) as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        actual = _sha256(temporary)
        if actual != expected_hash:
            raise FFmpegProvisionError(
                f"FFmpeg binary checksum mismatch: {member}; "
                f"expected={expected_hash}, actual={actual}"
            )
        os.replace(temporary, destination)
        temporary = None
        return destination
    except (OSError, zipfile.BadZipFile) as error:
        raise FFmpegProvisionError(f"Could not extract FFmpeg archive: {archive}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _run_tool(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(path), *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FFmpegProvisionError(f"Provisioned tool could not run: {path}") from error


def _reported_version(path: Path) -> tuple[str, str]:
    result = _run_tool(path, "-version")
    first_line = result.stdout.splitlines()[0].strip() if result.stdout else ""
    fields = first_line.split()
    if result.returncode != 0 or len(fields) < 3 or fields[0] != path.stem:
        raise FFmpegProvisionError(f"Provisioned tool validation failed: {path}")
    return fields[2].split("-")[0], first_line


def _validate_capabilities(path: Path) -> None:
    encoders = _run_tool(path, "-hide_banner", "-encoders")
    filters = _run_tool(path, "-hide_banner", "-filters")
    if encoders.returncode != 0 or "libmp3lame" not in encoders.stdout:
        raise FFmpegProvisionError("Provisioned FFmpeg lacks the libmp3lame encoder.")
    if filters.returncode != 0 or "loudnorm" not in filters.stdout:
        raise FFmpegProvisionError("Provisioned FFmpeg lacks the loudnorm filter.")


def _normalize_architecture(value: str) -> str:
    architecture = value.strip().casefold()
    aliases = {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x64": "x86_64",
    }
    return aliases.get(architecture, architecture)


def _required_macos_architectures(architecture: str) -> set[str]:
    normalized = _normalize_architecture(architecture)
    if normalized == "universal2":
        return {"arm64", "x86_64"}
    if normalized in {"arm64", "x86_64"}:
        return {normalized}
    raise FFmpegProvisionError(f"Unsupported macOS FFmpeg architecture: {architecture}")


def _validate_macos_architectures(path: Path, required: set[str]) -> None:
    try:
        result = subprocess.run(
            [MACOS_LIPO, "-archs", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FFmpegProvisionError(f"Could not inspect macOS FFmpeg binary: {path}") from error
    actual = {_normalize_architecture(value) for value in result.stdout.split()}
    if result.returncode != 0 or not required.issubset(actual):
        raise FFmpegProvisionError(
            f"macOS FFmpeg architecture mismatch: {path}; "
            f"required={sorted(required)}, actual={sorted(actual)}"
        )


def _prepare_macos_executable(path: Path) -> None:
    path.chmod(0o755)
    subprocess.run(
        [MACOS_XATTR, "-d", "com.apple.quarantine", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    result = subprocess.run(
        [MACOS_CODESIGN, "--force", "--sign", "-", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FFmpegProvisionError(f"Could not ad-hoc sign macOS FFmpeg tool: {path}")


def _required_source_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise FFmpegProvisionError(f"Explicit FFmpeg source is missing {field}.")
    normalized = value.strip()
    if not normalized or any(character in normalized for character in "\x00\r\n"):
        raise FFmpegProvisionError(f"Explicit FFmpeg source is missing {field}.")
    return normalized


def _validate_explicit_source_manifest(
    value: object,
    *,
    expected_version: str,
) -> dict[str, Any]:
    required_fields = {
        "schema_version",
        "provider",
        "version",
        "license",
        "source_reference",
        "tools",
    }
    if not isinstance(value, dict) or set(value) != required_fields:
        raise FFmpegProvisionError(
            "Explicit FFmpeg source manifest has invalid fields."
        )
    if value.get("schema_version") != 1:
        raise FFmpegProvisionError("Unsupported explicit FFmpeg source schema.")

    provider = _required_source_text(value.get("provider"), "provider")
    version = _required_source_text(value.get("version"), "version")
    license_name = _required_source_text(value.get("license"), "license")
    source_reference = _required_source_text(
        value.get("source_reference"), "source_reference"
    )
    if version != expected_version:
        raise FFmpegProvisionError(
            "Explicit FFmpeg source version mismatch: "
            f"expected={expected_version}, actual={version}"
        )

    tools = value.get("tools")
    if not isinstance(tools, dict) or set(tools) != {"ffmpeg", "ffprobe"}:
        raise FFmpegProvisionError(
            "Explicit FFmpeg source must define exactly ffmpeg and ffprobe."
        )
    normalized_tools: dict[str, dict[str, str]] = {}
    for name in ("ffmpeg", "ffprobe"):
        tool = tools[name]
        if not isinstance(tool, dict) or set(tool) != {"sha256"}:
            raise FFmpegProvisionError(
                f"Invalid explicit FFmpeg source entry: {name}."
            )
        sha256 = tool.get("sha256")
        if not isinstance(sha256, str) or HASH_PATTERN.fullmatch(sha256) is None:
            raise FFmpegProvisionError(
                f"Invalid explicit FFmpeg source SHA-256: {name}."
            )
        normalized_tools[name] = {"sha256": sha256}

    return {
        "schema_version": 1,
        "provider": provider,
        "version": version,
        "license": license_name,
        "source_reference": source_reference,
        "tools": normalized_tools,
    }


def _load_explicit_source_manifest(
    source_root: Path,
    *,
    expected_version: str,
) -> dict[str, Any]:
    path = source_root / EXPLICIT_SOURCE_MANIFEST
    if not path.is_file() or path.is_symlink():
        raise FFmpegProvisionError(
            f"Explicit FFmpeg source manifest is missing or unsafe: {path}"
        )
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FFmpegProvisionError(
            f"Unreadable explicit FFmpeg source manifest: {path}"
        ) from error
    validated = _validate_explicit_source_manifest(
        value,
        expected_version=expected_version,
    )
    validated["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    return validated


def _require_hash_match(label: str, expected: str, actual: str) -> None:
    if actual != expected:
        raise FFmpegProvisionError(
            f"Explicit FFmpeg source checksum mismatch: {label}; "
            f"expected={expected}, actual={actual}"
        )


def _copy_source(
    source: Path,
    destination: Path,
    *,
    expected_hash: str,
) -> Path:
    if not source.is_file() or source.is_symlink():
        raise FFmpegProvisionError(f"Staged FFmpeg source is missing or unsafe: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".staging",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
        _require_hash_match(
            source.name,
            expected_hash,
            _sha256(temporary),
        )
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _configuration(platform_key: str) -> dict[str, Any]:
    manifest = _load_manifest()
    return manifest["platforms"][platform_key]


def _materialize(item: dict[str, str], platform_key: str) -> Path:
    cached = _cache_destination(f"{platform_key}-{item['name']}")
    if _matches_hash(cached, item["binary_sha256"]):
        return cached
    return _extract_verified(
        _download(item),
        member=item["member"],
        expected_hash=item["binary_sha256"],
        destination=cached,
    )


def _create_universal_tool(
    arm_item: dict[str, str],
    intel_item: dict[str, str],
    destination: Path,
) -> Path:
    arm = _materialize(arm_item, "macos-arm64")
    intel = _materialize(intel_item, "macos-x86_64")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".universal2",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
        result = subprocess.run(
            [
                MACOS_LIPO,
                "-create",
                str(arm),
                str(intel),
                "-output",
                str(temporary),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise FFmpegProvisionError(
                f"Could not create universal2 FFmpeg tool: {destination.name}"
            )
        _validate_macos_architectures(temporary, {"arm64", "x86_64"})
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _source_provenance_lines(
    configurations: Iterable[dict[str, Any]],
    explicit_source: dict[str, Any] | None,
) -> list[str]:
    if explicit_source is not None:
        lines = [
            f"Source manifest: {EXPLICIT_SOURCE_MANIFEST} "
            f"(sha256 {explicit_source['manifest_sha256']})",
            f"Provider: {explicit_source['provider']}",
            f"Expected version: {explicit_source['version']}",
            f"Upstream license: {explicit_source['license']}",
            f"Source reference: {explicit_source['source_reference']}",
        ]
        for name in ("ffmpeg", "ffprobe"):
            lines.append(
                f"Source artifact: {name} "
                f"(sha256 {explicit_source['tools'][name]['sha256']})"
            )
        return lines

    lines: list[str] = []
    for configuration in configurations:
        lines.extend(
            [
                f"Provider: {configuration['provider']}",
                f"Expected version: {configuration['version']}",
                f"Upstream license: {configuration['license']}",
            ]
        )
        for item in configuration["tools"]:
            lines.append(
                f"Artifact: {item['url']} "
                f"(archive sha256 {item['archive_sha256']}; "
                f"binary sha256 {item['binary_sha256']})"
            )
    return lines


def _write_provenance(
    platform_key: str,
    configurations: Iterable[dict[str, Any]],
    tools: Iterable[Path],
    *,
    explicit_source: dict[str, Any] | None,
) -> Path:
    manifest = _load_manifest()
    destination = _safe_project_path(manifest["provenance_target"])
    lines = [
        "Letter Smith FFmpeg provisioning record",
        f"Platform: {platform_key}",
        "Source mode: "
        + (
            "explicitly staged"
            if explicit_source is not None
            else "checksum-pinned download"
        ),
    ]
    lines.extend(_source_provenance_lines(configurations, explicit_source))
    for path in tools:
        version, banner = _reported_version(path)
        lines.append(f"Staged {path.stem}: sha256 {_sha256(path)}; {banner}")
        if not version:
            raise FFmpegProvisionError(f"Could not record FFmpeg version: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".staging",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write("\n".join(lines) + "\n")
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_staged_tools(
    platform_name: str | None = None,
    target_architecture: str | None = None,
) -> tuple[Path, Path]:
    platform_value = str(platform_name or sys.platform).casefold()
    is_windows = platform_value in {"win32", "windows"}
    is_macos = platform_value in {"darwin", "macos"}
    if not is_windows and not is_macos:
        raise FFmpegProvisionError(f"Unsupported FFmpeg platform: {platform_value}")
    if is_windows:
        platform_key = "windows-x86_64"
        configuration = _configuration(platform_key)
        required_architectures: set[str] | None = None
    else:
        architecture = _normalize_architecture(
            str(target_architecture or platform.machine())
        )
        required_architectures = _required_macos_architectures(architecture)
        platform_key = f"macos-{architecture}"
        configuration = _configuration(
            "macos-arm64" if architecture == "universal2" else platform_key
        )

    paths = tuple(
        _safe_project_path(item["target"])
        for item in configuration["tools"]
    )
    expected_version = configuration["version"]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise FFmpegProvisionError(f"Required provisioned tool is missing: {path}")
        actual_version, _banner = _reported_version(path)
        if actual_version != expected_version:
            raise FFmpegProvisionError(
                f"FFmpeg version mismatch: {path}; "
                f"expected={expected_version}, actual={actual_version}"
            )
        if required_architectures is not None:
            _validate_macos_architectures(path, required_architectures)
    _validate_capabilities(paths[0])
    return paths[0], paths[1]


def provision_ffmpeg(
    platform_name: str | None = None,
    target_architecture: str | None = None,
    source_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    validate_provisioning_manifest()
    platform_value = str(platform_name or sys.platform).casefold()
    is_windows = platform_value in {"win32", "windows"}
    is_macos = platform_value in {"darwin", "macos"}
    if not is_windows and not is_macos:
        raise FFmpegProvisionError(f"Unsupported FFmpeg platform: {platform_value}")
    if is_windows:
        architecture = "x86_64"
        platform_key = "windows-x86_64"
        configurations = [_configuration(platform_key)]
    else:
        architecture = _normalize_architecture(
            str(target_architecture or platform.machine())
        )
        _required_macos_architectures(architecture)
        platform_key = f"macos-{architecture}"
        configurations = (
            [_configuration("macos-arm64"), _configuration("macos-x86_64")]
            if architecture == "universal2"
            else [_configuration(platform_key)]
        )

    source_value = source_dir or os.environ.get(
        "LETTER_SMITH_FFMPEG_SOURCE_DIR", ""
    ).strip()
    staged = bool(source_value)
    tools: list[Path] = []
    explicit_source: dict[str, Any] | None = None
    if staged:
        source_root = Path(source_value).expanduser().resolve()
        if not source_root.is_dir():
            raise FFmpegProvisionError(
                f"FFmpeg source directory does not exist: {source_root}"
            )
        configuration = configurations[0]
        explicit_source = _load_explicit_source_manifest(
            source_root,
            expected_version=configuration["version"],
        )
        for item in configuration["tools"]:
            destination = _safe_project_path(item["target"])
            source = source_root / destination.name
            tools.append(
                _copy_source(
                    source,
                    destination,
                    expected_hash=explicit_source["tools"][item["name"]]["sha256"],
                )
            )
    elif is_macos and architecture == "universal2":
        arm_by_name = {
            item["name"]: item for item in configurations[0]["tools"]
        }
        intel_by_name = {
            item["name"]: item for item in configurations[1]["tools"]
        }
        for name in ("ffmpeg", "ffprobe"):
            destination = _safe_project_path(arm_by_name[name]["target"])
            tools.append(
                _create_universal_tool(
                    arm_by_name[name],
                    intel_by_name[name],
                    destination,
                )
            )
    else:
        configuration = configurations[0]
        for item in configuration["tools"]:
            destination = _safe_project_path(item["target"])
            if is_windows and _matches_hash(destination, item["binary_sha256"]):
                tools.append(destination)
                continue
            tools.append(
                _extract_verified(
                    _download(item),
                    member=item["member"],
                    expected_hash=item["binary_sha256"],
                    destination=destination,
                )
            )

    if is_macos:
        for path in tools:
            _prepare_macos_executable(path)
    expected_version = configurations[0]["version"]
    for path in tools:
        actual_version, _banner = _reported_version(path)
        if actual_version != expected_version:
            raise FFmpegProvisionError(
                f"FFmpeg version mismatch: {path}; "
                f"expected={expected_version}, actual={actual_version}"
            )
    if is_macos:
        required = _required_macos_architectures(architecture)
        for path in tools:
            _validate_macos_architectures(path, required)
    _validate_capabilities(tools[0])
    _write_provenance(
        platform_key,
        configurations,
        tools,
        explicit_source=explicit_source,
    )
    return tools[0], tools[1]


if __name__ == "__main__":
    try:
        ffmpeg, ffprobe = provision_ffmpeg()
        print(f"Provisioned: {ffmpeg}")
        print(f"Provisioned: {ffprobe}")
    except FFmpegProvisionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
