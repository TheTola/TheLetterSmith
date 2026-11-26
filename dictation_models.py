"""Explicit, verified setup for LetterSmith's offline English dictation."""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QThread, Signal

from project_paths import runtime_application_paths


SPEECH_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
VAD_NAME = "ggml-silero-v6.2.0.bin"
VAD_ASSET = (
    "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v6.2.0.bin",
    "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
)


@dataclass(frozen=True)
class SpeechModel:
    filename: str
    sha256: str
    folder: str
    label: str

    def assets(self):
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{SPEECH_REVISION}/{self.filename}"
        return {self.filename: (url, self.sha256), VAD_NAME: VAD_ASSET}


BASE_EN = SpeechModel(
    "ggml-base.en.bin", "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
    "speech-en", "Base English (149 MB)",
)
SMALL_EN_Q5 = SpeechModel(
    "ggml-small.en-q5_1.bin", "bfdff4894dcb76bbf647d56263ea2a96645423f1669176f4844a1bf8e478ad30",
    "speech-small-en-q5", "Compact Small English (191 MB)",
)
SMALL_EN = SpeechModel(
    "ggml-small.en.bin", "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
    "speech-small-en", "Small English (489 MB)",
)
SPEECH_MODELS = (SMALL_EN_Q5, SMALL_EN, BASE_EN)


def digest(path, cancelled=lambda: False):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(4 * 1024**2):
            if cancelled():
                raise InterruptedError("Canceled")
            value.update(block)
    return value.hexdigest()


def model_root():
    return runtime_application_paths().app_data_root / "offline_models"


def speech_runtime_root():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "resources" / "dictation-runtime"
    return Path(__file__).resolve().parent / "build" / "dictation-runtime"


def verified_speech_weights(pack, cancelled=lambda: False, expected_model=None):
    root = Path(pack).resolve(strict=True)
    matches = [model for model in SPEECH_MODELS if (root / model.filename).is_file()]
    if len(matches) != 1:
        raise ValueError("Choose a folder containing one supported English Whisper model and its Silero speech detector.")
    model = matches[0]
    if expected_model is not None and model != expected_model:
        raise ValueError(f"Choose the verified {expected_model.label} model pack.")
    paths = {}
    for name, (_, expected) in model.assets().items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or digest(path, cancelled) != expected:
            raise ValueError("The dictation model failed verification. Download or choose a verified model pack.")
        paths[name] = path
    return paths[model.filename], paths[VAD_NAME]


def verify_speech_runtime(cancelled=lambda: False):
    if sys.platform != "win32":
        raise ValueError("Offline dictation currently requires Windows.")
    try:
        runtime = speech_runtime_root().resolve(strict=True)
        manifest = (runtime / "dictation_runtime_hashes.json" if getattr(sys, "frozen", False)
                    else Path(__file__).with_name("dictation_runtime_hashes.json"))
        hashes = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("The dictation engine is missing. Repair LetterSmith, or build it with tools/build_dictation.py in a source checkout.") from None
    if not isinstance(hashes, dict) or not {"dictation.exe", "whisper.dll"}.issubset(hashes):
        raise ValueError("The dictation engine manifest is invalid. Repair LetterSmith.")
    for name, expected in hashes.items():
        if Path(name).name != name:
            raise ValueError("The dictation engine manifest is invalid. Repair LetterSmith.")
        try:
            path = (runtime / name).resolve(strict=True)
        except OSError:
            raise ValueError("The dictation engine is incomplete. Repair LetterSmith. Model downloads have been kept.") from None
        if not path.is_relative_to(runtime) or digest(path, cancelled) != expected:
            raise ValueError("The dictation engine failed verification. Repair LetterSmith.")
    if any(path.suffix.lower() in {".exe", ".dll"} and path.name not in hashes for path in runtime.iterdir()):
        raise ValueError("An unrecognized executable is present in the dictation engine directory.")
    return runtime


def verify_speech_pack(pack, cancelled=lambda: False, *, expected_model=None):
    model, detector = verified_speech_weights(pack, cancelled, expected_model)
    return model, detector, verify_speech_runtime(cancelled)


def find_installed_pack():
    """Reuse supported local weights; never start a download during discovery."""
    candidates = [model_root() / model.folder for model in SPEECH_MODELS]
    if os.environ.get("LETTER_SMITH_PROFILE_ROOT", "").strip():
        for folder in candidates:
            try:
                verified_speech_weights(folder)
            except (OSError, ValueError):
                continue
            return str(folder)
        return None
    holo_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "HoloDesk"
    candidates.extend(holo_root / "offline_models" / model.folder for model in SPEECH_MODELS)
    try:
        existing = json.loads((holo_root / "settings.json").read_text(encoding="utf-8"))
        selected = existing.get("holo_voice_model_pack", "")
        if isinstance(selected, str) and selected:
            candidates.append(Path(selected))
    except (OSError, ValueError, AttributeError):
        pass
    for folder in dict.fromkeys(candidates):
        try:
            verified_speech_weights(folder)
        except (OSError, ValueError):
            continue
        return str(folder)
    return None


def download(path, url, expected, cancelled, progress, *, max_bytes=1024**3):
    """Resume a pinned download and replace its target only after verification."""
    path = Path(path)
    if path.exists() and digest(path, cancelled) == expected:
        return
    partial = path.with_suffix(path.suffix + ".part")
    if partial.exists() and digest(partial, cancelled) == expected:
        os.replace(partial, path)
        return
    for attempt in range(3):
        if cancelled():
            raise InterruptedError("Canceled")
        offset = partial.stat().st_size if partial.exists() else 0
        source = url
        if attempt:
            parts = urllib.parse.urlsplit(url)
            query = parts.query + ("&" if parts.query else "") + f"lettersmith_retry={time.time_ns()}"
            source = urllib.parse.urlunsplit(parts._replace(query=query))
        request = urllib.request.Request(source, headers={
            "Range": f"bytes={offset}-", "User-Agent": "LetterSmith-ModelSetup/1", "Accept-Encoding": "identity",
        })
        try:
            try:
                response = urllib.request.urlopen(request, timeout=30)
            except urllib.error.HTTPError as error:
                if error.code != 416 or not offset:
                    raise
                error.close()
                request.remove_header("Range")
                response = urllib.request.urlopen(request, timeout=30)
            with response:
                content_range = response.headers.get("Content-Range", "")
                if response.status == 206 and not content_range.startswith(f"bytes {offset}-"):
                    raise OSError("The server returned an unexpected download range.")
                append = offset > 0 and response.status == 206
                count = offset if append else 0
                length = response.headers.get("Content-Length")
                total = (int(content_range.rsplit("/", 1)[1]) if content_range and not content_range.endswith("/*")
                         else count + int(length) if length else 0)
                if total > max_bytes:
                    raise ValueError("The model download exceeds the supported size.")
                with partial.open("ab" if append else "wb") as stream:
                    while block := response.read(1024**2):
                        if cancelled():
                            raise InterruptedError("Canceled")
                        count += len(block)
                        if count > max_bytes:
                            raise ValueError("The model download exceeds the supported size.")
                        stream.write(block)
                        progress(count, total)
                if total and count != total:
                    raise OSError("The model download ended before the file was complete.")
            if digest(partial, cancelled) != expected:
                partial.unlink(missing_ok=True)
                if attempt == 2:
                    raise ValueError("Download verification failed. The model was not installed.")
                continue
            os.replace(partial, path)
            return
        except InterruptedError:
            raise
        except (OSError, http.client.HTTPException) as error:
            if isinstance(error, urllib.error.HTTPError):
                retryable = error.code in {403, 408, 429, 500, 502, 503, 504}
                error.close()
                if not retryable:
                    raise
            elif isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EROFS, errno.EDQUOT}:
                raise
            if attempt == 2:
                raise
            deadline = time.monotonic() + attempt + 1
            while time.monotonic() < deadline:
                if cancelled():
                    raise InterruptedError("Canceled")
                time.sleep(.1)


class ModelDownloadWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None, *, root=None, model=SMALL_EN_Q5):
        super().__init__(parent)
        self.model = model
        self.root = Path(root) if root else model_root() / model.folder
        self.canceled = threading.Event()

    def cancel(self):
        self.canceled.set()

    def run(self):
        try:
            verify_speech_runtime(self.canceled.is_set)
            self.root.mkdir(parents=True, exist_ok=True)
            for name, (url, expected) in self.model.assets().items():
                self.status.emit(f"Downloading {name}…")
                download(self.root / name, url, expected, self.canceled.is_set, self.progress.emit)
            self.status.emit("Verifying dictation model…")
            verify_speech_pack(self.root, self.canceled.is_set)
            if self.canceled.is_set():
                raise InterruptedError("Canceled")
            self.completed.emit(str(self.root))
        except InterruptedError:
            self.failed.emit("Dictation setup canceled. Partial downloads can be resumed.")
        except (OSError, ValueError, http.client.HTTPException) as error:
            self.failed.emit(str(error))
