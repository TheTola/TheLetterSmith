from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "release"
MANIFEST_PATH = RELEASE_ROOT / "release_manifest.json"
SPEC_PATH = RELEASE_ROOT / "LetterSmith.spec"
DIST_ROOT = RELEASE_ROOT / "dist"
BUILD_ROOT = RELEASE_ROOT / "build"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from button_artwork import application_resource_names

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".iss",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".qss",
    ".spec",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_SUFFIXES = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png"}
PRIVATE_IDENTITY_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
SECRET_TEXT_PATTERNS = (
    re.compile(
        r"[\"']?(?:access_token|api_key|api_token|aws_access_key_id|"
        r"aws_secret_access_key|client_secret|password|refresh_token)"
        r"[\"']?\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(
        r"[\"']?(?:publication_url|published_page_url|verified_url)[\"']?"
        r"\s*[:=]\s*[\"']https?://[^\"'\r\n]+[\"']",
        re.IGNORECASE,
    ),
)


class ReleaseValidationError(RuntimeError):
    pass


def _release_platform(platform_name: str | None = None) -> str:
    platform = str(platform_name or sys.platform).casefold()
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "macos"
    raise ReleaseValidationError(f"Unsupported release platform: {platform}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseValidationError(f"Unreadable JSON: {path}") from error
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"JSON root must be an object: {path}")
    return value


def _project_path(relative: object) -> Path:
    text = str(relative or "").strip()
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        raise ReleaseValidationError(f"Unsafe release path: {text or '<empty>'}")
    resolved = (PROJECT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ReleaseValidationError(f"Release path escapes the project: {text}") from error
    return resolved


def _safe_destination(value: object) -> Path:
    text = str(value or "").strip()
    destination = Path(text)
    if (
        not text
        or destination.is_absolute()
        or destination.drive
        or ".." in destination.parts
    ):
        raise ReleaseValidationError(
            f"Unsafe frozen distribution destination: {text or '<empty>'}"
        )
    return destination


def _sanitation_settings(manifest: dict[str, Any]) -> dict[str, Any]:
    settings = manifest.get("release_sanitation")
    if not isinstance(settings, dict):
        raise ReleaseValidationError("Release sanitation settings are missing.")
    required = (
        "production_source_roots",
        "distribution_allowed_root_entries",
        "distribution_forbidden_path_parts",
        "distribution_forbidden_file_names",
        "distribution_forbidden_suffixes",
    )
    for key in required:
        values = settings.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ReleaseValidationError(
                f"Release sanitation setting {key!r} must be a non-empty string list."
            )
        normalized = {value.strip().casefold() for value in values}
        if len(normalized) != len(values):
            raise ReleaseValidationError(
                f"Release sanitation setting {key!r} contains duplicates."
            )
    if any(
        Path(value).name != value
        for value in settings["distribution_allowed_root_entries"]
    ):
        raise ReleaseValidationError(
            "Distribution root allowlist entries must be plain names."
        )
    if any(
        not value.startswith(".") or Path(value).name != value
        for value in settings["distribution_forbidden_suffixes"]
    ):
        raise ReleaseValidationError(
            "Forbidden distribution suffixes must be simple dot-prefixed values."
        )
    return settings


def _manifest_entries(
    manifest: dict[str, Any],
) -> Iterator[tuple[Path, str]]:
    app_relative_root = _safe_destination(
        manifest.get("application_resources_root")
    )
    app_root = _project_path(app_relative_root)
    for relative_dir, names in manifest["application_resources"].items():
        destination = str(app_relative_root / relative_dir)
        for name in application_resource_names(app_root, relative_dir, names):
            yield (app_root / relative_dir / name).resolve(), destination

    prompt_root = PROJECT_ROOT / "resources" / "prompt_writer"
    for name in manifest["prompt_writer_files"]:
        yield (prompt_root / name).resolve(), "resources/prompt_writer"

    for item in manifest["data_directories"] + manifest["data_files"]:
        yield _project_path(item["source"]), str(item["destination"])


def _binary_entries(
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> Iterator[tuple[Path, str]]:
    platform = _release_platform(platform_name)
    items = (
        manifest["binaries"]
        if platform == "windows"
        else manifest["macos"]["binaries"]
    )
    for item in items:
        yield _project_path(item["source"]), str(item["destination"])


def _files_under(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        yield from (candidate for candidate in path.rglob("*") if candidate.is_file())


def _all_payload_files(
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> tuple[Path, ...]:
    files: dict[Path, None] = {}
    for source, _destination in (
        list(_manifest_entries(manifest))
        + list(_binary_entries(manifest, platform_name))
    ):
        for path in _files_under(source):
            files[path.resolve()] = None
    return tuple(files)


def _production_source_files(manifest: dict[str, Any]) -> tuple[Path, ...]:
    settings = _sanitation_settings(manifest)
    files: dict[Path, None] = {}
    for relative in settings["production_source_roots"]:
        root = _project_path(relative)
        if not root.is_dir() or root.is_symlink():
            raise ReleaseValidationError(
                f"Production source root is missing or unsafe: {root}"
            )
        candidates = root.glob("*.py") if root == PROJECT_ROOT else root.rglob("*.py")
        for candidate in candidates:
            if candidate.is_symlink():
                raise ReleaseValidationError(
                    f"Production source cannot contain links: {candidate}"
                )
            if candidate.is_file():
                files[candidate.resolve()] = None
    entrypoint = _project_path(manifest.get("entrypoint"))
    if entrypoint not in files:
        raise ReleaseValidationError(
            "The release entrypoint is outside the production source allowlist."
        )
    return tuple(files)


def _expected_payload_destinations(
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> dict[str, Path]:
    expected: dict[str, Path] = {}
    entries = list(_manifest_entries(manifest)) + list(
        _binary_entries(manifest, platform_name)
    )
    for source, destination_text in entries:
        destination = _safe_destination(destination_text)
        if source.is_file():
            source_files = ((source, Path(source.name)),)
        else:
            source_files = (
                (candidate, candidate.relative_to(source))
                for candidate in _files_under(source)
            )
        for candidate, relative in source_files:
            frozen_relative = (destination / relative).as_posix()
            previous = expected.get(frozen_relative)
            if previous is not None and previous != candidate:
                raise ReleaseValidationError(
                    f"Release inputs collide at {frozen_relative}."
                )
            expected[frozen_relative] = candidate.resolve()
    return expected


def _expected_binary_destinations(
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> set[str]:
    expected: set[str] = set()
    for source, destination_text in _binary_entries(manifest, platform_name):
        destination = _safe_destination(destination_text)
        if source.is_file():
            source_files = ((source, Path(source.name)),)
        else:
            source_files = (
                (candidate, candidate.relative_to(source))
                for candidate in _files_under(source)
            )
        for _candidate, relative in source_files:
            expected.add((destination / relative).as_posix())
    return expected


def _validate_identity(manifest: dict[str, Any]) -> None:
    from application_identity import (
        APPLICATION_NAME,
        APPLICATION_VERSION,
        EXECUTABLE_NAME,
        MACOS_APP_NAME,
        MACOS_BUNDLE_IDENTIFIER,
        MACOS_DISK_IMAGE_NAME,
        MACOS_EXECUTABLE_NAME,
        PUBLISHER_NAME,
    )

    expected = {
        "name": APPLICATION_NAME,
        "version": APPLICATION_VERSION,
        "publisher": PUBLISHER_NAME,
        "executable": EXECUTABLE_NAME,
    }
    if manifest.get("schema_version") != "1.0":
        raise ReleaseValidationError("Release manifest schema must be 1.0.")
    if manifest.get("product") != expected:
        raise ReleaseValidationError(
            "Release manifest product identity differs from application_identity.py."
        )
    expected_macos_identity = {
        "app_name": MACOS_APP_NAME,
        "executable": MACOS_EXECUTABLE_NAME,
        "bundle_identifier": MACOS_BUNDLE_IDENTIFIER,
        "dmg_name": MACOS_DISK_IMAGE_NAME,
    }
    macos = manifest.get("macos")
    if not isinstance(macos, dict) or any(
        macos.get(key) != value for key, value in expected_macos_identity.items()
    ):
        raise ReleaseValidationError(
            "Release manifest macOS identity differs from application_identity.py."
        )
    entrypoint = _project_path(manifest.get("entrypoint"))
    if entrypoint != (PROJECT_ROOT / "Main.py").resolve() or not entrypoint.is_file():
        raise ReleaseValidationError("Release entrypoint must be Main.py.")
    excluded_modules = manifest.get("excluded_modules")
    required_exclusions = {"_pytest", "pytest", "test", "tests", "tkinter"}
    if (
        not isinstance(excluded_modules, list)
        or any(not isinstance(value, str) for value in excluded_modules)
        or not required_exclusions.issubset(excluded_modules)
    ):
        raise ReleaseValidationError(
            "Release module exclusions must block test and development packages."
        )

    version_text = (RELEASE_ROOT / "version_info.txt").read_text(encoding="utf-8")
    for value in expected.values():
        if value not in version_text:
            raise ReleaseValidationError(
                f"Windows version metadata is missing {value!r}."
            )


def _validate_allowlist(
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> tuple[Path, ...]:
    blocked = {str(value).casefold() for value in manifest["forbidden_path_parts"]}
    seen_destinations: set[tuple[str, str]] = set()
    entries = list(_manifest_entries(manifest)) + list(
        _binary_entries(manifest, platform_name)
    )
    for source, destination in entries:
        _safe_destination(destination)
        if not source.exists():
            raise ReleaseValidationError(f"Release input is missing: {source}")
        if source.is_symlink():
            raise ReleaseValidationError(f"Release input cannot be a link: {source}")
        key = (str(source).casefold(), destination.casefold())
        if key in seen_destinations:
            raise ReleaseValidationError(f"Duplicate release input: {source}")
        seen_destinations.add(key)
        for candidate in source.rglob("*") if source.is_dir() else ():
            if candidate.is_symlink():
                raise ReleaseValidationError(
                    f"Release input cannot contain links: {candidate}"
                )

    payload_files = _all_payload_files(manifest, platform_name)
    for path in payload_files:
        relative = path.relative_to(PROJECT_ROOT)
        if any(part.casefold() in blocked for part in relative.parts):
            raise ReleaseValidationError(f"Forbidden release path: {relative}")
        if path.suffix.casefold() in {".pyc", ".pyo"}:
            raise ReleaseValidationError(f"Bytecode cannot be bundled: {relative}")
    _expected_payload_destinations(manifest, platform_name)
    return payload_files


def _validate_application_resources(manifest: dict[str, Any]) -> None:
    gallery_root = _project_path(manifest.get("application_resources_root"))
    if gallery_root != (PROJECT_ROOT / "gallery" / "app").resolve():
        raise ReleaseValidationError(
            "Application controls, icons, pages, and sounds must be owned by gallery/app."
        )

    resources_root = PROJECT_ROOT / "resources" / "app"
    allowed_resource_families = {"fonts", "themes"}
    actual_resource_families = {
        path.name.casefold()
        for path in resources_root.iterdir()
        if path.is_dir()
    }
    if actual_resource_families != allowed_resource_families:
        raise ReleaseValidationError(
            "resources/app must contain exactly fonts and themes; "
            f"actual={sorted(actual_resource_families)}"
        )
    unexpected = sorted(
        path.relative_to(resources_root).as_posix()
        for path in resources_root.rglob("*")
        if path.is_file()
        and path.relative_to(resources_root).parts[0].casefold()
        not in allowed_resource_families
    )
    if unexpected:
        raise ReleaseValidationError(
            "Only fonts and themes may remain in resources/app; "
            f"unexpected={unexpected}"
        )


def _validate_prompt_writer(manifest: dict[str, Any]) -> None:
    root = PROJECT_ROOT / "resources" / "prompt_writer"
    expected = set(manifest["prompt_writer_files"])
    actual = {path.name for path in root.iterdir() if path.is_file()}
    if actual != expected:
        raise ReleaseValidationError(
            "Bundled Prompt Writer libraries do not match the release manifest."
        )
    for path in root.iterdir():
        if not path.is_file():
            raise ReleaseValidationError(
                f"Unexpected Prompt Writer resource directory: {path}"
            )
        text = path.read_text(encoding="utf-8-sig")
        if "-User Added" in text or "user_colors" in path.name.casefold():
            raise ReleaseValidationError(
                f"User Prompt Writer content cannot be bundled: {path}"
            )


def _validate_stock_resources() -> None:
    from PIL import Image

    root = PROJECT_ROOT / "resources" / "stock"
    stock_manifest = _load_json(root / "stock_manifest.json")
    images = stock_manifest.get("images")
    if not isinstance(images, dict) or set(images) != {"cover", "letter", "wall", "back"}:
        raise ReleaseValidationError("Stock image categories are invalid.")

    image_paths: list[Path] = []
    for category in ("cover", "letter", "wall", "back"):
        names = images.get(category)
        if not isinstance(names, list) or len(names) != 3:
            raise ReleaseValidationError(
                f"Stock category {category!r} must contain exactly three images."
            )
        image_paths.extend((root / str(name)).resolve() for name in names)

    actual_images = {path.resolve() for path in (root / "images").rglob("*.png")}
    if len(image_paths) != 12 or set(image_paths) != actual_images:
        raise ReleaseValidationError("Exactly 12 manifested stock PNGs are required.")
    for path in image_paths:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ReleaseValidationError(f"Unreadable stock PNG: {path}") from error

    music = stock_manifest.get("music")
    if not isinstance(music, list) or len(music) != 3:
        raise ReleaseValidationError("Exactly three stock songs are required.")
    music_paths = {
        (root / str(item.get("filename", ""))).resolve()
        for item in music
        if isinstance(item, dict)
    }
    actual_music = {
        path.resolve() for path in (root / "music").iterdir() if path.is_file()
    }
    if len(music_paths) != 3 or music_paths != actual_music:
        raise ReleaseValidationError(
            "Stock music files do not exactly match stock_manifest.json."
        )


def _validate_saved_letter_resources(manifest: dict[str, Any]) -> None:
    from saved_letters import validate_saved_letter_bundle

    stock_root = PROJECT_ROOT / "resources" / "stock" / "letters"
    expected_stock = set(manifest["stock_letters"])
    actual_stock = {path.name for path in stock_root.iterdir() if path.is_dir()}
    if actual_stock != expected_stock:
        raise ReleaseValidationError(
            f"Packaged Stock Letters must be exactly {sorted(expected_stock)}."
        )

    for title in sorted(expected_stock):
        bundle = stock_root / title
        metadata = validate_saved_letter_bundle(bundle, PROJECT_ROOT)
        if metadata.get("schema_version") != "1.0":
            raise ReleaseValidationError(f"{title} is not schema 1.0.")
        if metadata.get("recipient_title") != f"Stock: Letter {title[-1]}":
            raise ReleaseValidationError(f"{title} has an invalid visible title.")
        if (
            metadata.get("recipient_display_name") != "NONE"
            or metadata.get("recipient_name") != "NONE"
            or metadata.get("recipient_normalized_key") != "none"
        ):
            raise ReleaseValidationError(f"{title} must use recipient NONE.")
        if str(metadata.get("published_page_url", "")).strip():
            raise ReleaseValidationError(f"{title} contains a publication URL.")

    examples_root = PROJECT_ROOT / "resources" / "examples"
    examples = [path for path in examples_root.iterdir() if path.is_dir()]
    if len(examples) != 1 or examples[0].name != "example_letter":
        raise ReleaseValidationError("Exactly one Example Letter is required.")
    metadata = validate_saved_letter_bundle(examples[0], PROJECT_ROOT)
    if metadata.get("schema_version") != "1.0":
        raise ReleaseValidationError("Example Letter is not schema 1.0.")
    if metadata.get("recipient_title") != "Example Letter":
        raise ReleaseValidationError("Example Letter title is invalid.")
    if not bool(metadata.get("example_master")):
        raise ReleaseValidationError("Example Letter is not marked as a master.")
    if str(metadata.get("published_page_url", "")).strip():
        raise ReleaseValidationError("Example Letter contains a publication URL.")


def _validate_images(payload_files: Iterable[Path]) -> None:
    from PIL import Image

    for path in payload_files:
        if path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ReleaseValidationError(f"Unreadable image resource: {path}") from error


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
        raise ReleaseValidationError(f"Bundled tool could not run: {path}") from error


def _validate_audio_tools(platform_name: str | None = None) -> None:
    platform = _release_platform(platform_name)
    from release.provision_ffmpeg import (
        FFmpegProvisionError,
        validate_staged_tools,
    )

    target_architecture = (
        os.environ.get("LETTER_SMITH_MACOS_TARGET_ARCH", "").strip()
        if platform == "macos"
        else None
    )
    try:
        validate_staged_tools(platform, target_architecture)
    except FFmpegProvisionError as error:
        raise ReleaseValidationError(str(error)) from error
    suffix = ".exe" if platform == "windows" else ""
    tool_root = PROJECT_ROOT / "tools"
    if platform == "macos":
        tool_root /= "macos"
    ffmpeg = tool_root / f"ffmpeg{suffix}"
    ffprobe = tool_root / f"ffprobe{suffix}"
    for path in (ffmpeg, ffprobe):
        result = _run_tool(path, "-version")
        expected_banner = f"{path.stem} version"
        if result.returncode != 0 or expected_banner not in result.stdout.casefold():
            raise ReleaseValidationError(f"Bundled tool validation failed: {path}")

    stock_manifest = _load_json(
        PROJECT_ROOT / "resources" / "stock" / "stock_manifest.json"
    )
    audio_files = list((PROJECT_ROOT / "gallery/app/sounds").glob("*.mp3"))
    audio_files.extend(
        PROJECT_ROOT / "resources" / "stock" / item["filename"]
        for item in stock_manifest["music"]
    )
    for path in audio_files:
        result = _run_tool(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        )
        try:
            duration = float(result.stdout.strip())
        except ValueError as error:
            raise ReleaseValidationError(f"Unreadable audio resource: {path}") from error
        if result.returncode != 0 or duration <= 0:
            raise ReleaseValidationError(f"Unreadable audio resource: {path}")


def _validate_dependencies(manifest: dict[str, Any]) -> None:
    missing: list[str] = []
    for name in manifest["required_python_modules"]:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        raise ReleaseValidationError(
            "Missing runtime dependencies: " + ", ".join(sorted(missing))
        )


def _validate_github_configuration() -> None:
    from publishing.github_config import (
        GITHUB_APP_CLIENT_ID,
        GITHUB_APP_SLUG,
        GitHubApplicationConfiguration,
    )

    configuration = GitHubApplicationConfiguration(
        client_id=GITHUB_APP_CLIENT_ID,
        app_slug=GITHUB_APP_SLUG,
    )
    if not configuration.configured:
        raise ReleaseValidationError(
            "Set the public GitHub App client ID and app slug in "
            "publishing/github_config.py before building a release."
        )


def _private_text_patterns(
    *,
    include_secret_literals: bool,
) -> tuple[re.Pattern[str], ...]:
    patterns = list(PRIVATE_IDENTITY_PATTERNS)
    if include_secret_literals:
        patterns.extend(SECRET_TEXT_PATTERNS)
    local_values = {
        str(PROJECT_ROOT),
        str(Path.home()),
        Path.home().name,
    }
    for value in sorted(local_values):
        text = value.strip()
        if len(text) >= 4:
            patterns.append(re.compile(re.escape(text), re.IGNORECASE))
    return tuple(patterns)


def _validate_private_text(
    files: Iterable[Path],
    *,
    context: str,
    include_secret_literals: bool = True,
) -> None:
    patterns = _private_text_patterns(
        include_secret_literals=include_secret_literals,
    )
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as error:
            raise ReleaseValidationError(f"Unreadable {context} file: {path}") from error
        if path.suffix.casefold() not in TEXT_SUFFIXES or size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ReleaseValidationError(f"Unreadable {context} text: {path}") from error
        for pattern in patterns:
            if pattern.search(text):
                raise ReleaseValidationError(
                    f"Private or development text found in {context}: {path}"
                )


def _private_binary_markers() -> tuple[bytes, ...]:
    values = {
        str(PROJECT_ROOT),
        str(PROJECT_ROOT).replace("\\", "/"),
        str(Path.home()),
        str(Path.home()).replace("\\", "/"),
        Path.home().name,
    }
    markers: set[bytes] = set()
    for value in values:
        text = value.strip()
        if len(text) < 4:
            continue
        markers.add(text.encode("utf-8").lower())
        markers.add(text.encode("utf-16-le").lower())
    return tuple(sorted(markers, key=len, reverse=True))


def _validate_private_binary_markers(files: Iterable[Path]) -> None:
    markers = _private_binary_markers()
    overlap = max(len(marker) for marker in markers) - 1
    for path in files:
        carry = b""
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    data = carry + chunk
                    lowered = data.lower()
                    if any(marker in lowered for marker in markers):
                        raise ReleaseValidationError(
                            "Private development identity found in frozen distribution: "
                            f"{path}"
                        )
                    carry = data[-overlap:] if overlap else b""
        except OSError as error:
            raise ReleaseValidationError(
                f"Unreadable frozen distribution file: {path}"
            ) from error


def validate_release_inputs(
    platform_name: str | None = None,
) -> dict[str, int]:
    manifest = _load_json(MANIFEST_PATH)
    _validate_identity(manifest)
    source_files = _production_source_files(manifest)
    payload_files = _validate_allowlist(manifest, platform_name)
    _validate_application_resources(manifest)
    _validate_prompt_writer(manifest)
    _validate_stock_resources()
    _validate_saved_letter_resources(manifest)
    _validate_images(payload_files)
    _validate_audio_tools(platform_name)
    _validate_dependencies(manifest)
    _validate_private_text(
        (*source_files, *payload_files),
        context="release source or resource",
    )
    _validate_github_configuration()
    return {
        "source_files": len(source_files),
        "payload_files": len(payload_files),
        "stock_images": 12,
        "stock_music": 3,
        "stock_letters": 3,
        "example_letters": 1,
    }


def _clean_build_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != RELEASE_ROOT.resolve():
        raise ReleaseValidationError(f"Refusing to clean unsafe path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _files_equal(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as first_stream, second.open("rb") as second_stream:
        while True:
            first_chunk = first_stream.read(1024 * 1024)
            second_chunk = second_stream.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def _validate_frozen_payload(
    internal: Path,
    manifest: dict[str, Any],
    platform_name: str | None = None,
) -> None:
    expected = _expected_payload_destinations(manifest, platform_name)
    rewritten_binary_paths = (
        _expected_binary_destinations(manifest, platform_name)
        if _release_platform(platform_name) == "macos"
        else set()
    )
    managed_roots = {"resources", "tools"}
    expected_paths = {
        relative
        for relative in expected
        if Path(relative).parts[0].casefold() in managed_roots
    }
    actual_paths = {
        path.relative_to(internal).as_posix()
        for path in internal.rglob("*")
        if path.is_file()
        and path.relative_to(internal).parts[0].casefold() in managed_roots
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)[:10]
        unexpected = sorted(actual_paths - expected_paths)[:10]
        raise ReleaseValidationError(
            "Frozen resource allowlist mismatch; "
            f"missing={missing}, unexpected={unexpected}"
        )
    for relative in sorted(expected_paths):
        # PyInstaller rewrites Mach-O load commands and replaces signatures while
        # collecting binaries. Their packaged bytes cannot equal the approved
        # source bytes; the macOS release verifier checks architecture, minimum
        # OS, dependency closure, and signatures after collection instead.
        if relative in rewritten_binary_paths:
            continue
        frozen = internal / Path(relative)
        source = expected[relative]
        try:
            matches = _files_equal(source, frozen)
        except OSError as error:
            raise ReleaseValidationError(
                f"Could not verify frozen release input: {relative}"
            ) from error
        if not matches:
            raise ReleaseValidationError(
                f"Frozen release input differs from its approved source: {relative}"
            )


def _validate_distribution_sanitation(
    distribution: Path,
    manifest: dict[str, Any],
) -> tuple[Path, ...]:
    settings = _sanitation_settings(manifest)
    configured_roots = set(settings["distribution_allowed_root_entries"])
    required_roots = {manifest["product"]["executable"], "_internal"}
    if configured_roots != required_roots:
        raise ReleaseValidationError(
            "Frozen distribution root allowlist must contain only LetterSmith.exe "
            "and _internal."
        )
    actual_roots = {path.name for path in distribution.iterdir()}
    if actual_roots != configured_roots:
        raise ReleaseValidationError(
            "Frozen distribution root allowlist mismatch; "
            f"missing={sorted(configured_roots - actual_roots)}, "
            f"unexpected={sorted(actual_roots - configured_roots)}"
        )

    blocked_parts = {
        value.casefold()
        for value in settings["distribution_forbidden_path_parts"]
    }
    blocked_names = {
        value.casefold()
        for value in settings["distribution_forbidden_file_names"]
    }
    blocked_suffixes = {
        value.casefold()
        for value in settings["distribution_forbidden_suffixes"]
    }
    files: list[Path] = []
    for path in distribution.rglob("*"):
        relative = path.relative_to(distribution)
        if path.is_symlink():
            raise ReleaseValidationError(
                f"Frozen distribution cannot contain links: {relative}"
            )
        if any(part.casefold() in blocked_parts for part in relative.parts):
            raise ReleaseValidationError(
                f"Forbidden development or user-data path in distribution: {relative}"
            )
        if not path.is_file():
            continue
        approved_resource = (
            len(relative.parts) >= 2
            and relative.parts[0].casefold() == "_internal"
            and relative.parts[1].casefold() == "resources"
        )
        if path.name.casefold() in blocked_names and not approved_resource:
            raise ReleaseValidationError(
                f"Forbidden development or user-data file in distribution: {relative}"
            )
        if path.suffix.casefold() in blocked_suffixes:
            raise ReleaseValidationError(
                f"Forbidden development artifact in distribution: {relative}"
            )
        files.append(path)

    _validate_private_text(
        files,
        context="frozen distribution",
        include_secret_literals=False,
    )
    _validate_private_binary_markers(files)
    return tuple(files)


def _validate_distribution(manifest: dict[str, Any]) -> Path:
    distribution = DIST_ROOT / "LetterSmith"
    executable = distribution / manifest["product"]["executable"]
    internal = distribution / "_internal"
    if not distribution.is_dir() or not internal.is_dir():
        raise ReleaseValidationError(
            "The one-folder Letter Smith distribution is incomplete."
        )
    if not executable.is_file() or executable.stat().st_size < 1_000_000:
        raise ReleaseValidationError("LetterSmith.exe was not produced correctly.")
    _validate_distribution_sanitation(distribution, manifest)
    for relative in (
        "resources/stock/stock_manifest.json",
        "resources/examples/example_letter/lettersmith-metadata.json",
        "gallery/app/icons/folder/lsmith.ico",
        "tools/ffmpeg.exe",
        "tools/ffprobe.exe",
    ):
        if not (internal / relative).is_file():
            raise ReleaseValidationError(
                f"Frozen distribution is missing {relative}."
            )
    if not any(internal.rglob("QtWebEngineProcess.exe")):
        raise ReleaseValidationError("Qt WebEngine process is missing from the build.")
    if not any(internal.rglob("qwindows.dll")):
        raise ReleaseValidationError("Qt Windows platform plugin is missing from the build.")
    _validate_frozen_payload(internal, manifest)
    return executable


def validate_distribution() -> Path:
    validate_release_inputs()
    manifest = _load_json(MANIFEST_PATH)
    return _validate_distribution(manifest)


def _pyinstaller_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment_by_name = {
        key.casefold(): value
        for key, value in environment.items()
    }
    python_root = Path(sys.base_prefix).resolve()
    windows_root = Path(
        environment_by_name.get("systemroot")
        or environment_by_name.get("windir")
        or r"C:\Windows"
    ).resolve()
    candidates = (
        Path(sys.executable).resolve().parent,
        python_root,
        python_root / "DLLs",
        python_root / "Scripts",
        windows_root / "System32",
        windows_root,
    )
    trusted: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        normalized = os.path.normcase(os.path.normpath(str(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        trusted.append(str(candidate))
    for key in tuple(environment):
        if key.casefold() == "path":
            del environment[key]
    environment["PATH"] = os.pathsep.join(trusted)
    return environment


def build_release(
    *,
    sign: bool = False,
    ffmpeg_source_dir: str | Path | None = None,
) -> Path:
    if sys.platform != "win32":
        raise ReleaseValidationError("Letter Smith release builds require Windows.")
    from release.provision_ffmpeg import FFmpegProvisionError, provision_ffmpeg

    try:
        provision_ffmpeg("windows", source_dir=ffmpeg_source_dir)
    except FFmpegProvisionError as error:
        raise ReleaseValidationError(str(error)) from error
    if importlib.util.find_spec("PyInstaller") is None:
        raise ReleaseValidationError(
            "PyInstaller is not installed. Install requirements.txt only "
            "after packaging is approved."
        )
    _clean_build_directory(BUILD_ROOT)
    _clean_build_directory(DIST_ROOT)
    command = [
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
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        env=_pyinstaller_environment(),
    )
    if result.returncode != 0:
        raise ReleaseValidationError(
            f"PyInstaller failed with exit code {result.returncode}."
        )
    executable = validate_distribution()
    if sign:
        from release.windows_signing import (
            WindowsSigningError,
            load_signing_configuration,
            sign_file,
        )

        try:
            sign_file(executable, load_signing_configuration())
        except WindowsSigningError as error:
            raise ReleaseValidationError(str(error)) from error
        validate_distribution()
    return executable


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or build the Letter Smith 1.0.0 Windows release."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Run release gates without creating package output (default).",
    )
    mode.add_argument(
        "--build",
        action="store_true",
        help="Create the one-folder PyInstaller distribution.",
    )
    parser.add_argument(
        "--confirm-package",
        action="store_true",
        help="Required with --build to prevent an accidental package run.",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Authenticode-sign and verify LetterSmith.exe for public distribution.",
    )
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
    if args.sign and not args.build:
        print("ERROR: --sign requires --build.", file=sys.stderr)
        return 2
    try:
        from release.provision_ffmpeg import FFmpegProvisionError, provision_ffmpeg

        provision_ffmpeg("windows", source_dir=args.ffmpeg_source_dir)
        summary = validate_release_inputs()
        print(
            "Release inputs validated: "
            + ", ".join(f"{key}={value}" for key, value in summary.items())
        )
        if not args.build:
            state = (
                "available"
                if importlib.util.find_spec("PyInstaller") is not None
                else "not installed"
            )
            print(f"PyInstaller: {state}; packaging was not run.")
            return 0
        executable = build_release(
            sign=args.sign,
            ffmpeg_source_dir=args.ffmpeg_source_dir,
        )
        print(f"Built and verified: {executable}")
        return 0
    except (ReleaseValidationError, FFmpegProvisionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
