from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = PROJECT_ROOT / "tools"
FFMPEG_PATH = TOOLS_DIR / "ffmpeg.exe"
FFPROBE_PATH = TOOLS_DIR / "ffprobe.exe"


class AudioToolError(RuntimeError):
    """Raised when the bundled FFmpeg toolchain cannot complete an operation."""


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float


def _tool_path(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AudioToolError(
            f"{label} was not found at the required project location:\n{resolved}"
        )
    return resolved


def ffmpeg_path() -> Path:
    return _tool_path(FFMPEG_PATH, "FFmpeg")


def ffprobe_path() -> Path:
    return _tool_path(FFPROBE_PATH, "FFprobe")


def toolchain_available() -> bool:
    return FFMPEG_PATH.is_file() and FFPROBE_PATH.is_file()


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    text: bool,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            check=False,
            timeout=max(1, int(timeout_seconds)),
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioToolError(
            f"Audio processing exceeded the {timeout_seconds}-second safety limit."
        ) from exc
    except OSError as exc:
        raise AudioToolError(f"Unable to start the bundled audio tool: {exc}") from exc


def _error_text(proc: subprocess.CompletedProcess) -> str:
    raw = proc.stderr or proc.stdout or "Audio tool failed without an error message."
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    return text[-3000:] if len(text) > 3000 else text


def probe_audio(path: str | Path, *, timeout_seconds: int = 30) -> AudioInfo:
    source = Path(path).resolve()
    if not source.is_file():
        raise AudioToolError(f"Audio file does not exist: {source}")

    proc = _run(
        [
            str(ffprobe_path()),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(source),
        ],
        timeout_seconds=timeout_seconds,
        text=True,
    )
    if proc.returncode != 0:
        raise AudioToolError(_error_text(proc))

    try:
        payload = json.loads(proc.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioToolError("FFprobe returned invalid duration metadata.") from exc

    if duration < 0:
        raise AudioToolError("FFprobe returned a negative duration.")
    return AudioInfo(duration_seconds=duration)


def convert_to_mp3(
    source: str | Path,
    destination: str | Path,
    *,
    timeout_seconds: int = 180,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not src.is_file():
        raise AudioToolError(f"Audio file does not exist: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dst.stem}.", suffix=".tmp.mp3", dir=str(dst.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.unlink(missing_ok=True)

    command = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(src),
        "-map_metadata", "-1",
        "-vn",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-codec:a", "libmp3lame",
        "-q:a", "2",
        "-f", "mp3",
        str(tmp),
    ]

    process = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_creation_flags(),
        )
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                raise AudioToolError("Audio import canceled.")
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=2)
                raise AudioToolError(
                    f"Audio processing exceeded the {timeout_seconds}-second safety limit."
                )
            time.sleep(0.05)

        stderr = process.stderr.read() if process.stderr is not None else ""
        if process.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
            text = (stderr or "FFmpeg conversion failed.").strip()
            raise AudioToolError(text[-3000:])
        os.replace(tmp, dst)
    except OSError as exc:
        raise AudioToolError(f"Unable to start the bundled audio tool: {exc}") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except Exception:
                pass
        tmp.unlink(missing_ok=True)


def decode_mono_pcm16(
    source: str | Path,
    *,
    sample_rate: int = 22050,
    timeout_seconds: int = 180,
    cancel_check: Callable[[], bool] | None = None,
) -> bytes:
    src = Path(source).resolve()
    if not src.is_file():
        raise AudioToolError(f"Audio file does not exist: {src}")

    fd, tmp_name = tempfile.mkstemp(prefix=".lettersmith-analysis.", suffix=".pcm")
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.unlink(missing_ok=True)

    command = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", str(max(8000, int(sample_rate))),
        "-f", "s16le",
        "-codec:a", "pcm_s16le",
        str(tmp),
    ]

    process = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_creation_flags(),
        )
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                raise AudioToolError("Audio analysis canceled.")
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=2)
                raise AudioToolError(
                    f"Audio processing exceeded the {timeout_seconds}-second safety limit."
                )
            time.sleep(0.05)

        stderr = process.stderr.read() if process.stderr is not None else ""
        if process.returncode != 0:
            text = (stderr or "FFmpeg audio decode failed.").strip()
            raise AudioToolError(text[-3000:])
        data = tmp.read_bytes() if tmp.is_file() else b""
        if not data:
            raise AudioToolError("FFmpeg decoded no audio samples.")
        return data
    except OSError as exc:
        raise AudioToolError(f"Unable to start the bundled audio tool: {exc}") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except Exception:
                pass
        tmp.unlink(missing_ok=True)

