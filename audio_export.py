from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _find_audio_tool(env_name: str, exe_name: str) -> str:
    env_path = os.environ.get(env_name, "").strip().strip('"')
    if env_path and os.path.isfile(env_path):
        return env_path

    found = shutil.which(exe_name)
    if found:
        return found

    root = Path(__file__).resolve().parent
    candidates = [
        root / "ffmpeg" / "bin" / exe_name,
        root / "tools" / "ffmpeg" / "bin" / exe_name,
        root / "gallery" / "app" / "ffmpeg" / "bin" / exe_name,
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return ""


def _load_pydub_tools():
    try:
        from pydub import AudioSegment, effects  # type: ignore
        import pydub  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"pydub is not available: {exc}") from exc

    ffmpeg = _find_audio_tool("FFMPEG_BIN", "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    ffprobe = _find_audio_tool("FFPROBE_BIN", "ffprobe.exe" if os.name == "nt" else "ffprobe")

    if ffmpeg:
        pydub.AudioSegment.converter = ffmpeg  # type: ignore[attr-defined]
    if ffprobe:
        pydub.AudioSegment.ffprobe = ffprobe  # type: ignore[attr-defined]

    return AudioSegment, effects


def _export_with_pydub(src: Path, tmp: Path) -> None:
    AudioSegment, effects = _load_pydub_tools()
    audio = AudioSegment.from_file(str(src))
    audio = audio.set_frame_rate(44100).set_channels(2)
    audio = effects.normalize(audio)
    audio.export(
        tmp,
        format="mp3",
        bitrate="192k",
        parameters=["-write_xing", "0"],
    )


def _export_with_ffmpeg(src: Path, tmp: Path) -> None:
    ffmpeg = _find_audio_tool("FFMPEG_BIN", "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available")

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-write_xing",
        "0",
        "-map_metadata",
        "-1",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-f",
        "mp3",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        stderr = ""
        try:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        except Exception:
            stderr = ""
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"ffmpeg export failed{detail}") from exc


def _export_apple_safe_mp3(src: Path, dst: Path) -> None:
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"Missing audio source: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    pydub_error: Exception | None = None

    try:
        try:
            _export_with_pydub(src, tmp)
        except Exception as exc:
            pydub_error = exc
            _export_with_ffmpeg(src, tmp)
        os.replace(tmp, dst)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        if pydub_error is not None and exc is not pydub_error:
            raise RuntimeError(
                f"Failed to export Apple-safe MP3 from {src} to {dst}: "
                f"pydub failed ({pydub_error}); ffmpeg fallback failed ({exc})"
            ) from exc
        raise RuntimeError(f"Failed to export Apple-safe MP3 from {src} to {dst}: {exc}") from exc
