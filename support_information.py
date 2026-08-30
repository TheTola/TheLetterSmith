from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from PySide6 import QtCore

from application_identity import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    BUILD_ID,
    CREATOR_NAME,
)
from audio_tools import audio_tool_filename
from project_paths import application_paths
from readiness import evaluate_readiness
from settings_store import SettingsStore


UNAVAILABLE = "Unavailable"
MAX_LOG_BYTES = 12 * 1024
MAX_LOG_LINES = 40


@dataclass(frozen=True)
class SupportReportOptions:
    include_computer_information: bool = True
    include_diagnostic_information: bool = True


@dataclass(frozen=True)
class SupportRuntimeContext:
    active_theme: str = UNAVAILABLE
    application_state: str = UNAVAILABLE
    github_state: str = UNAVAILABLE
    publishing_service: str = UNAVAILABLE
    last_operation: str = UNAVAILABLE
    last_operation_result: str = UNAVAILABLE
    display_count: str = UNAVAILABLE
    primary_display_resolution: str = UNAVAILABLE
    primary_display_dpi: str = UNAVAILABLE
    primary_display_scale: str = UNAVAILABLE


_GITHUB_TOKEN_PATTERN = re.compile(
    r"\bgh[a-z]_[A-Za-z0-9]{16,}\b",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\"'}\]]+")
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)(\bAuthorization\b\s*[\"']?\s*[:=]\s*[\"']?).*$"
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"(?im)(\b(?:Cookie|Set-Cookie)\b\s*[\"']?\s*[:=]\s*[\"']?).*$"
)
_SECRET_FIELD_PATTERN = re.compile(
    r"(?ix)(\b(?:access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
    r"api[_ -]?token|oauth[_ -]?token|client[_ -]?secret|password|"
    r"session[_ -]?id|"
    r"secret[_ -]?access[_ -]?key|device[_ -]?code|user[_ -]?code)\b"
    r"\s*[\"']?\s*[:=]\s*[\"']?)[^\s,;&}\]\"']+"
)
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|client_secret|api_key|api_token|"
    r"password|cookie|session_id|device_code|user_code)=)[^&#\s]+"
)
_WINDOWS_HOME_PATTERN = re.compile(
    r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\r\n]+"
)
_UNIX_HOME_PATTERN = re.compile(r"(?i)(?<![\w.-])/(?:Users|home)/[^/\s]+")
_MAC_PATTERN = re.compile(r"(?i)\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b")
_UUID_PATTERN = re.compile(
    r"(?i)\b[0-9A-F]{8}-[0-9A-F]{4}-[1-5][0-9A-F]{3}-"
    r"[89AB][0-9A-F]{3}-[0-9A-F]{12}\b"
)
_PRODUCT_KEY_PATTERN = re.compile(r"(?i)\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b")
_SERIAL_FIELD_PATTERN = re.compile(
    r"(?im)(\b(?:serial(?: number)?|device uuid|hardware uuid|product key)\b"
    r"\s*[:=]\s*).+$"
)
_PERSONAL_FIELD_PATTERN = re.compile(
    r"(?im)(\b(?:recipient(?:[_ -]?(?:name|title|id))?|letter[_ -]?content|"
    r"message[_ -]?(?:text|content|body)|personal[_ -]?(?:document|filename))\b"
    r"\s*[\"']?\s*[:=]\s*[\"']?).*$"
)
_IPV4_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_IPV6_PATTERN = re.compile(
    r"(?i)(?<![0-9A-F:])(?:[0-9A-F]{0,4}:){2,7}[0-9A-F]{0,4}"
    r"(?![0-9A-F:])"
)


def _redact_ip_candidate(match: re.Match[str]) -> str:
    value = match.group(0)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    return "[REDACTED IP ADDRESS]"


def _redact_sensitive_url_query(match: re.Match[str]) -> str:
    return f"{match.group(1)}[REDACTED]"


def _path_prefixes(project_root: Path | None = None) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[Path, str]] = []
    for value, replacement in (
        (Path.home(), "<USER_HOME>"),
        (Path(tempfile.gettempdir()), "<TEMP>"),
    ):
        candidates.append((value, replacement))
    for environment_name in ("USERPROFILE", "HOME"):
        value = os.environ.get(environment_name, "").strip()
        if value:
            candidates.append((Path(value), "<USER_HOME>"))
    if project_root is not None:
        paths = application_paths(project_root)
        candidates.extend(
            (
                (paths.app_data_root, "<APP_DATA>"),
                (paths.documents_root, "<LETTER_SMITH_DOCUMENTS>"),
                (paths.temporary_root, "<APP_TEMP>"),
            )
        )
    prefixes: dict[str, str] = {}
    for path, replacement in candidates:
        try:
            text = str(path.expanduser().resolve())
        except OSError:
            text = str(path.expanduser())
        if text:
            prefixes[text] = replacement
            prefixes[text.replace("\\", "/")] = replacement
    return tuple(sorted(prefixes.items(), key=lambda item: len(item[0]), reverse=True))


def sanitize_support_text(
    value: object,
    *,
    project_root: str | Path | None = None,
    private_values: Sequence[object] = (),
) -> str:
    """Return email-safe diagnostics without secrets or identifying paths."""
    text = str(value).replace("\x00", "")
    text = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED CREDENTIALS]@", text)
    text = _SENSITIVE_QUERY_PATTERN.sub(_redact_sensitive_url_query, text)
    text = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)
    text = _COOKIE_HEADER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _GITHUB_TOKEN_PATTERN.sub("[REDACTED TOKEN]", text)
    text = _SECRET_FIELD_PATTERN.sub(r"\1[REDACTED]", text)
    text = _SERIAL_FIELD_PATTERN.sub(r"\1[REDACTED]", text)
    text = _PERSONAL_FIELD_PATTERN.sub(r"\1[REDACTED PERSONAL CONTENT]", text)
    text = _MAC_PATTERN.sub("[REDACTED MAC ADDRESS]", text)
    text = _UUID_PATTERN.sub("[REDACTED UNIQUE IDENTIFIER]", text)
    text = _PRODUCT_KEY_PATTERN.sub("[REDACTED PRODUCT KEY]", text)
    text = _IPV4_PATTERN.sub(_redact_ip_candidate, text)
    text = _IPV6_PATTERN.sub(_redact_ip_candidate, text)
    root = Path(project_root).resolve() if project_root is not None else None
    for prefix, replacement in _path_prefixes(root):
        text = re.sub(re.escape(prefix), replacement, text, flags=re.IGNORECASE)
    text = _WINDOWS_HOME_PATTERN.sub("<USER_HOME>", text)
    text = _UNIX_HOME_PATTERN.sub("<USER_HOME>", text)
    for private_value in private_values:
        private_text = str(private_value or "").strip()
        if len(private_text) < 3:
            continue
        text = re.sub(
            re.escape(private_text),
            "[REDACTED PERSONAL VALUE]",
            text,
            flags=re.IGNORECASE,
        )
    return text


def _format_bytes(value: int | float | None) -> str:
    if value is None or value < 0:
        return UNAVAILABLE
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return UNAVAILABLE


def _installed_memory_bytes() -> int | None:
    if sys.platform == "win32":
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.length = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
        return page_size * page_count
    except (AttributeError, OSError, ValueError):
        return None


def _run_local_command(command: Sequence[str], *, timeout: float = 4.0) -> str:
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _windows_hardware_details() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    script = (
        "$cpu=Get-CimInstance Win32_Processor|Select-Object -First 1 Name,"
        "NumberOfCores,NumberOfLogicalProcessors;"
        "$gpu=Get-CimInstance Win32_VideoController|Select-Object -First 1 Name,"
        "AdapterRAM,DriverVersion;"
        "@{cpu=$cpu;gpu=$gpu}|ConvertTo-Json -Compress -Depth 3"
    )
    output = _run_local_command(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
    )
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    return {
        "CPU Model": str(cpu.get("Name") or "").strip(),
        "Physical CPU Cores": str(cpu.get("NumberOfCores") or "").strip(),
        "Logical Processors": str(
            cpu.get("NumberOfLogicalProcessors") or ""
        ).strip(),
        "GPU Model": str(gpu.get("Name") or "").strip(),
        "Dedicated GPU Memory": _format_bytes(
            int(gpu["AdapterRAM"])
            if str(gpu.get("AdapterRAM") or "").isdigit()
            else None
        ),
        "GPU Driver": str(gpu.get("DriverVersion") or "").strip(),
    }


def _macos_gpu_details() -> dict[str, str]:
    if sys.platform != "darwin":
        return {}
    output = _run_local_command(
        ["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"],
        timeout=5.0,
    )
    try:
        payload = json.loads(output)
        displays = payload.get("SPDisplaysDataType", [])
        gpu = displays[0] if isinstance(displays, list) and displays else {}
    except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
        return {}
    return {
        "GPU Model": str(gpu.get("sppci_model") or "").strip(),
        "Dedicated GPU Memory": str(gpu.get("spdisplays_vram") or "").strip(),
        "GPU Driver": str(gpu.get("spdisplays_gmux-version") or "").strip(),
    }


def collect_computer_information(
    project_root: str | Path,
    context: SupportRuntimeContext,
) -> dict[str, str]:
    root = Path(project_root).resolve()
    details = {
        "Operating System": platform.system() or UNAVAILABLE,
        "OS Release": platform.release() or UNAVAILABLE,
        "OS Version / Build": platform.version() or UNAVAILABLE,
        "Architecture": platform.machine() or UNAVAILABLE,
        "Python Architecture": f"{struct.calcsize('P') * 8}-bit",
        "CPU Model": platform.processor() or os.environ.get(
            "PROCESSOR_IDENTIFIER", ""
        ) or UNAVAILABLE,
        "Physical CPU Cores": UNAVAILABLE,
        "Logical Processors": str(os.cpu_count() or UNAVAILABLE),
        "Installed Memory": _format_bytes(_installed_memory_bytes()),
        "GPU Model": UNAVAILABLE,
        "Dedicated GPU Memory": UNAVAILABLE,
        "GPU Driver": UNAVAILABLE,
        "Connected Displays": context.display_count,
        "Primary Display Resolution": context.primary_display_resolution,
        "Primary Display DPI": context.primary_display_dpi,
        "Primary Display Scale": context.primary_display_scale,
        "Available Storage": UNAVAILABLE,
    }
    platform_details = (
        _windows_hardware_details()
        if sys.platform == "win32"
        else _macos_gpu_details()
    )
    for label, value in platform_details.items():
        if value:
            details[label] = value
    try:
        details["Available Storage"] = _format_bytes(shutil.disk_usage(root).free)
    except OSError:
        pass
    return details


def _availability(module_name: str) -> str:
    try:
        return "Available" if importlib.util.find_spec(module_name) else UNAVAILABLE
    except (ImportError, ModuleNotFoundError, ValueError):
        return UNAVAILABLE


def _tail_text(path: Path, byte_limit: int) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            start = max(0, size - byte_limit)
            stream.seek(start)
            payload = stream.read(byte_limit)
    except OSError:
        return ""
    text = payload.decode("utf-8", errors="replace")
    if start and "\n" in text:
        text = text.split("\n", 1)[1]
    return text.strip()


def _truncate_utf8(value: str, byte_limit: int) -> str:
    payload = value.encode("utf-8", errors="replace")[:byte_limit]
    return payload.decode("utf-8", errors="ignore")


def recent_diagnostic_excerpt(project_root: str | Path) -> str:
    logs_root = application_paths(project_root).logs_root
    excerpts: list[str] = []
    remaining = MAX_LOG_BYTES
    for name in ("lettersmith.log", "editor_error.log"):
        if remaining <= 0:
            break
        text = _tail_text(logs_root / name, min(remaining, MAX_LOG_BYTES // 2))
        if not text:
            continue
        lines = [line for line in text.splitlines() if line.strip()]
        bounded = "\n".join(lines[-MAX_LOG_LINES:])
        if bounded:
            excerpts.append(f"[{name}]\n{bounded}")
            remaining -= len(bounded.encode("utf-8", errors="replace"))
    return _truncate_utf8("\n\n".join(excerpts), MAX_LOG_BYTES)


def _private_setting_values(settings: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        settings.get(key, "")
        for key in (
            "recipient_name",
            "recipient_title",
            "recipient_id",
            "project_id",
            "published_page_url",
            "published_public_path",
        )
        if settings.get(key)
    )


def collect_diagnostic_information(
    project_root: str | Path,
    context: SupportRuntimeContext,
) -> tuple[dict[str, str], tuple[object, ...]]:
    root = Path(project_root).resolve()
    paths = application_paths(root)
    try:
        settings = SettingsStore(root).snapshot()
    except (OSError, RuntimeError, TypeError, ValueError):
        settings = {}
    ffmpeg = paths.tool_path(audio_tool_filename("ffmpeg"))
    ffprobe = paths.tool_path(audio_tool_filename("ffprobe"))
    try:
        readiness = evaluate_readiness(root)
    except (OSError, RuntimeError, ValueError):
        readiness = None
    items = {item.key: item for item in readiness.items} if readiness else {}
    image_items = [items.get(key) for key in ("cover", "letter", "wall", "back")]
    image_ready = sum(bool(item and item.ready) for item in image_items)
    try:
        from publishing.expiration import publication_status

        publishing_status = publication_status(settings).replace("_", " ").title()
    except (ImportError, RuntimeError, TypeError, ValueError):
        publishing_status = UNAVAILABLE
    diagnostics = {
        "FFmpeg": "Available" if ffmpeg.is_file() else UNAVAILABLE,
        "FFprobe": "Available" if ffprobe.is_file() else UNAVAILABLE,
        "Audio Backend": _availability("pygame"),
        "DOCX Import": (
            "Available"
            if _availability("mammoth") == "Available"
            or _availability("docx") == "Available"
            else UNAVAILABLE
        ),
        "PDF Import": _availability("PyPDF2"),
        "Git": "Available" if shutil.which("git") else UNAVAILABLE,
        "GitHub Authentication": context.github_state,
        "Publishing Service": context.publishing_service,
        "Publishing Status": publishing_status,
        "Application State": context.application_state,
        "Images Loaded": f"{image_ready} / 4",
        "Message Loaded": "Yes" if items.get("message") and items["message"].ready else "No",
        "Sound Loaded": "Yes" if items.get("music") and items["music"].ready else "No",
        "Letter Readiness": readiness.status if readiness and readiness.status else UNAVAILABLE,
        "Last Operation": context.last_operation,
        "Last Operation Result": context.last_operation_result,
        "Recent Diagnostic Excerpt": recent_diagnostic_excerpt(root) or UNAVAILABLE,
    }
    return diagnostics, _private_setting_values(settings)


def _section(title: str, values: Mapping[str, object]) -> str:
    lines = [title, "-" * len(title)]
    for label, value in values.items():
        text = str(value or UNAVAILABLE).strip() or UNAVAILABLE
        if "\n" in text:
            lines.extend((f"{label}:", text))
        else:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def format_support_report(
    *,
    context: SupportRuntimeContext,
    computer_information: Mapping[str, object] | None = None,
    diagnostic_information: Mapping[str, object] | None = None,
) -> str:
    sections = [
        f"{APPLICATION_NAME.upper()} SUPPORT INFORMATION\n"
        "================================",
        _section(
            "APPLICATION",
            {
                "Application": APPLICATION_NAME,
                "Version": APPLICATION_VERSION,
                "Build": BUILD_ID,
                "Created by": CREATOR_NAME,
                "Python Version": platform.python_version(),
                "Qt Version": QtCore.qVersion(),
                "Application Mode": (
                    "Frozen / Installed"
                    if getattr(sys, "frozen", False)
                    else "Source"
                ),
                "Active Theme": context.active_theme,
            },
        ),
    ]
    if computer_information is not None:
        sections.append(_section("COMPUTER INFORMATION", computer_information))
    if diagnostic_information is not None:
        sections.append(_section("DIAGNOSTIC INFORMATION", diagnostic_information))
    sections.append(
        "PRIVACY\n-------\n"
        "This report was generated locally by Letter Smith. Sensitive authentication "
        "information, unique device identifiers, personal paths, recipient information, "
        "and letter content are intentionally excluded. Nothing was transmitted "
        "automatically."
    )
    return "\n\n".join(sections).strip() + "\n"


def build_support_report(
    project_root: str | Path,
    options: SupportReportOptions,
    context: SupportRuntimeContext,
    *,
    computer_collector: Callable[
        [str | Path, SupportRuntimeContext], Mapping[str, object]
    ] = collect_computer_information,
    diagnostic_collector: Callable[
        [str | Path, SupportRuntimeContext],
        tuple[Mapping[str, object], tuple[object, ...]],
    ] = collect_diagnostic_information,
) -> str:
    computer = None
    if options.include_computer_information:
        try:
            computer = computer_collector(project_root, context)
        except Exception:
            computer = {"Collection Status": UNAVAILABLE}
    private_values: tuple[object, ...] = ()
    diagnostic = None
    if options.include_diagnostic_information:
        try:
            diagnostic, private_values = diagnostic_collector(
                project_root,
                context,
            )
        except Exception:
            diagnostic = {"Collection Status": UNAVAILABLE}
    report = format_support_report(
        context=context,
        computer_information=computer,
        diagnostic_information=diagnostic,
    )
    return sanitize_support_text(
        report,
        project_root=project_root,
        private_values=private_values,
    )


__all__ = [
    "MAX_LOG_BYTES",
    "SupportReportOptions",
    "SupportRuntimeContext",
    "UNAVAILABLE",
    "build_support_report",
    "collect_computer_information",
    "collect_diagnostic_information",
    "format_support_report",
    "recent_diagnostic_excerpt",
    "sanitize_support_text",
]
