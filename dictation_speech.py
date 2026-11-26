"""Bounded offline dictation, shared by LetterSmith prompt fields and editors."""
from __future__ import annotations

from array import array
import json
import ctypes
import sys
from contextlib import contextmanager
from ctypes import wintypes
import math
import os
import queue
import re
import struct
import subprocess
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, Signal, Slot, QCoreApplication
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices, QtAudio

from dictation_models import verify_speech_pack


def tr(text):
    return QCoreApplication.translate("Dictation", text)


def open_microphone_privacy_settings():
    if sys.platform != "win32":
        return False
    try:
        os.startfile("ms-settings:privacy-microphone")
    except OSError:
        return False
    return True


_PCM_FORMATS = {"int16": ("h", 2, 32768), "int32": ("i", 4, 2147483648),
                "float": ("f", 4, 1), "uint8": ("B", 1, 128)}
_VAD_FRAME = 4000  # 250 ms at the recognizer's 16 kHz sample rate.
_END_SILENCE = 8000
_SPLIT_OVERLAP = 16000


class PCMConverter:
    """Streaming native PCM -> mono 16 kHz float32, retaining fractional phase."""
    def __init__(self, rate, channels, sample_format):
        if not 8000 <= rate <= 192000 or not 1 <= channels <= 8 or sample_format not in _PCM_FORMATS:
            raise ValueError("Unsupported microphone audio format")
        self.rate, self.channels, self.format = rate, channels, sample_format
        self.pending = bytearray()
        self.samples = []
        self.position = 0.0

    def convert(self, raw):
        code, size, divisor = _PCM_FORMATS[self.format]
        self.pending.extend(raw)
        usable = len(self.pending) // (size * self.channels) * size * self.channels
        values = array(code)
        values.frombytes(bytes(self.pending[:usable]))
        del self.pending[:usable]
        if self.rate == 16000 and self.channels == 1 and self.format == "float":
            for i, value in enumerate(values):
                if not -1.0 <= value <= 1.0:
                    values[i] = max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0
            return values
        for i in range(0, len(values), self.channels):
            value = sum(values[i:i + self.channels]) / self.channels
            if self.format == "uint8":
                value -= 128
            value /= divisor
            self.samples.append(max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0)
        result = array("f")
        step = self.rate / 16000
        while self.position + 1 < len(self.samples):
            index = int(self.position)
            fraction = self.position - index
            result.append(self.samples[index] * (1 - fraction) + self.samples[index + 1] * fraction)
            self.position += step
        consumed = min(int(self.position), len(self.samples))
        del self.samples[:consumed]
        self.position -= consumed
        return result


def remove_overlap(previous, text):
    """Used only at a forced audio-window split, never across natural pauses."""
    old = re.findall(r"\S+", previous)
    matches = list(re.finditer(r"\S+", text))
    clean = lambda word: word.strip(".,!?;:").casefold()
    for size in range(min(12, len(old), len(matches)), 0, -1):
        if [clean(w) for w in old[-size:]] == [clean(m.group()) for m in matches[:size]]:
            return text[matches[size - 1].end():].lstrip()
    return text


def _split_speech_window(samples):
    """Return audio to finalize, audio to keep, and whether those overlap."""
    quiet_end = None
    for end in range(len(samples), max(319, len(samples) - 48000), -320):
        start = end - 320
        # Search the last three seconds for at least 60 ms below -50 dBFS.
        if sum(x * x for x in samples[start:end]) > 320 * 1e-5:
            quiet_end = None
        else:
            if quiet_end is None:
                quiet_end = end
            if quiet_end - start >= 960:
                cut = (start + quiet_end) // 2
                return samples[:cut], samples[cut:], False
    # With no quiet gap, give the next decode context to recover a cut word.
    return samples, samples[-_SPLIT_OVERLAP:], True


@contextmanager
def external_library_search():
    """Do not pass PyInstaller's private DLL directory to the verified runtime."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    buffer = ctypes.create_unicode_buffer(32768)
    if kernel.GetDllDirectoryW(len(buffer), buffer) >= len(buffer):
        raise OSError("Cannot preserve the application DLL search directory")
    previous = buffer.value
    if not kernel.SetDllDirectoryW(None):
        raise OSError(ctypes.get_last_error(), "Cannot isolate model runtime libraries")
    try:
        yield
    finally:
        if not kernel.SetDllDirectoryW(previous or None):
            raise OSError(ctypes.get_last_error(), "Cannot restore application library search")


class ProcessJob:
    """Kill owned descendants if the supervising worker exits unexpectedly.

    A job bounds process lifetime; it is not a filesystem security sandbox.
    """

    def __init__(self, process, *, memory_limit=0) -> None:
        self.handle = None
        if os.name != "nt":
            return
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit:
            info.basic.flags |= 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY (committed bytes)
            info.process_memory = memory_limit
        if not handle:
            raise OSError(ctypes.get_last_error(), "Cannot create inference job")
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            process.kill()
            process.wait()
            raise OSError(error, "Cannot supervise inference process")
        self.kernel, self.handle = kernel, handle

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class NativeRecognizer:
    def __init__(self, pack, canceled):
        model, detector, runtime = verify_speech_pack(pack, canceled.is_set)
        command = [str(runtime / "dictation.exe"), str(runtime / "whisper.dll"), str(model), str(detector)]
        self.canceled = canceled
        self.responses = queue.Queue(maxsize=4)
        self.process = None
        self.job = None
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP"}}
        env["PATH"] = str(runtime) + os.pathsep + str(os.path.join(os.environ.get("SYSTEMROOT", "C:/Windows"), "System32"))
        try:
            with external_library_search():
                self.process = subprocess.Popen(command,
                                                cwd=runtime, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.job = ProcessJob(self.process, memory_limit=2 * 1024**3)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            if self.receive(60).get("event") != "ready":
                raise ValueError("Speech runtime could not start")
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while not self.canceled.is_set():
                line = self.process.stdout.readline(100001)
                if not line or len(line) > 100000:
                    break
                result = json.loads(line)
                self.responses.put_nowait(result)
        except (ValueError, OSError, queue.Full):
            pass
        try:
            self.responses.put_nowait({"event": "error"})
        except queue.Full:
            pass

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.canceled.is_set():
                raise InterruptedError()
            try:
                result = self.responses.get(timeout=.05)
            except queue.Empty:
                continue
            if not isinstance(result, dict) or result.get("event") not in {"ready", "result"}:
                raise ValueError("Speech runtime stopped unexpectedly")
            if result["event"] == "result" and (not isinstance(result.get("speech"), bool) or not isinstance(result.get("text"), str) or len(result["text"]) > 32000):
                raise ValueError("Invalid speech response")
            tiebreaks = result.get("base_tiebreaks", 0)
            if type(tiebreaks) is not int or not 0 <= tiebreaks <= 32000:
                raise ValueError("Invalid speech response")
            return result
        raise TimeoutError("The offline speech engine took too long to respond. Recording stopped.")

    def recognize(self, samples, *, detect=False):
        if not 512 <= len(samples) <= 480000:
            raise ValueError("Invalid audio window")
        flags = 0x80000000 if detect else 0
        self.process.stdin.write(struct.pack("<I", len(samples) | flags) + samples.tobytes())
        self.process.stdin.flush()
        return self.receive(15)

    def close(self):
        if self.job:
            self.job.close()
            self.job = None
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe:
                    pipe.close()


class SpeechWorker(QThread):
    event = Signal(str, dict)

    def __init__(self, identity, pack, audio_format, parent=None):
        super().__init__(parent)
        self.identity, self.pack = identity, pack
        self.converter = PCMConverter(*audio_format)
        self.canceled = threading.Event()
        self.stopping = threading.Event()
        rate, channels, kind = audio_format
        bytes_per_frame = channels * _PCM_FORMATS[kind][1]
        self.batch_bytes = (rate // 4) * bytes_per_frame
        self.capture_limit = 8 * rate * bytes_per_frame
        self.capture = bytearray()
        self.capture_changed = threading.Condition()
        self.overflow = threading.Event()
        self.runtime = None

    def cancel(self):
        self.canceled.set()
        with self.capture_changed:
            self.capture.clear()
            self.capture_changed.notify()
        # Wake a blocked pipe write as well as the polling read path.
        runtime = self.runtime
        if runtime and runtime.process and runtime.process.poll() is None:
            runtime.process.kill()

    def submit(self, data):
        # Device callbacks can contain just a few milliseconds of audio.
        # Keep an ordered duration of PCM irrespective of callback frequency.
        with self.capture_changed:
            if not data or self.canceled.is_set() or self.stopping.is_set() or self.overflow.is_set():
                return
            if len(data) > self.capture_limit - len(self.capture):
                self.overflow.set()
            else:
                self.capture.extend(data)
            if len(self.capture) >= self.batch_bytes or self.overflow.is_set():
                self.capture_changed.notify()

    def _take_audio(self):
        with self.capture_changed:
            if not self.capture_changed.wait_for(
                lambda: len(self.capture) >= self.batch_bytes or self.stopping.is_set()
                or self.canceled.is_set() or self.overflow.is_set(), timeout=.05
            ):
                return None
            if self.canceled.is_set():
                raise InterruptedError()
            if self.overflow.is_set():
                raise TimeoutError("The local speech recognizer fell eight seconds behind. Recording stopped. Finalized text has been kept.")
            chunk = self.capture[:self.batch_bytes]
            del self.capture[:len(chunk)]
            return chunk

    def emit_event(self, event, **values):
        if not self.canceled.is_set():
            self.event.emit(self.identity, {"event": event, **values})

    def run(self):
        runtime = None
        try:
            runtime = NativeRecognizer(self.pack, self.canceled)
            self.runtime = runtime
            self.emit_event("ready")
            pending, segment, preroll = array("f"), array("f"), array("f")
            silence = 0
            last_decode = 0
            preview_interval = 16000
            previous = ""
            overlap = False
            output_count = 0

            def decode(final=False):
                nonlocal last_decode, previous, overlap, output_count, preview_interval
                if len(segment) < 1600:
                    return
                started = time.monotonic()
                result = runtime.recognize(segment)
                text = result["text"].strip()
                if overlap:
                    text = remove_overlap(previous, text)
                last_decode = len(segment)
                if final:
                    output_count += len(text)
                    if output_count > 100000:
                        raise ValueError("Recording reached its text limit. Start a new recording.")
                    previous = text
                    self.emit_event("final", text=text)
                else:
                    # Leave time to catch up between increasingly costly previews.
                    preview_interval = max(24000, int((time.monotonic() - started) * 2 * 16000))
                    self.emit_event("partial", text=text)

            while not self.canceled.is_set():
                data = self._take_audio()
                if data is None:
                    continue
                if not data:
                    if pending or not overlap or len(segment) > _SPLIT_OVERLAP:
                        segment.extend(pending)
                        decode(True)
                    break
                pending.extend(self.converter.convert(data))
                if len(pending) > 480000:
                    raise ValueError("Audio buffer limit reached")
                while len(pending) >= _VAD_FRAME:
                    frame = pending[:_VAD_FRAME]
                    del pending[:_VAD_FRAME]
                    has_speech = runtime.recognize(preroll[-1600:] + frame, detect=True)["speech"]
                    if has_speech:
                        if not segment:
                            segment.extend(preroll)
                        silence = 0
                    else:
                        silence += len(frame)
                    if segment or has_speech:
                        segment.extend(frame)
                        if silence >= _END_SILENCE or len(segment) >= 320000:
                            tail = array("f")
                            repeats_audio = False
                            if silence < _END_SILENCE:
                                segment, tail, repeats_audio = _split_speech_window(segment)
                            decode(True)
                            segment = tail
                            last_decode = 0
                            preview_interval = 16000
                            overlap = repeats_audio
                        elif len(segment) - last_decode >= preview_interval and not self.stopping.is_set():
                            with self.capture_changed:
                                caught_up = len(self.capture) < self.batch_bytes
                            if caught_up:
                                decode()
                    preroll = frame[-3200:]
        except InterruptedError:
            pass
        except (TimeoutError, ValueError) as error:
            self.emit_event("error", message=tr(str(error)))
        except Exception:
            self.emit_event("error", message=tr("Offline speech failed. Check the model pack, microphone, and LetterSmith installation."))
        finally:
            self.stopping.set()
            with self.capture_changed:
                self.capture.clear()
            if runtime:
                runtime.close()
            self.runtime = None
            self.emit_event("done")


class SpeechService(QObject):
    busy_changed = Signal(bool)
    recording_changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.worker = None
        self.identity = None
        self.callback = None
        self.source = None
        self.stream = None
        self.workers = set()
        self.capture_allowed = lambda: True

    @property
    def busy(self):
        return self.worker is not None

    @property
    def recording(self):
        return self.stream is not None

    def start(self, callback):
        if self.busy:
            raise ValueError("Stop the current recording before starting another.")
        settings = self.store.load()
        identity = uuid4().hex
        self.identity, self.callback = identity, callback
        try:
            if not self.capture_allowed():
                raise ValueError("Recording is unavailable while the Windows session is locked or session monitoring is unavailable. Restart LetterSmith after unlocking Windows.")
            if not settings.get("dictation_model_pack"):
                raise ValueError("Set up the English speech model in Dictation settings.")
            devices = QMediaDevices.audioInputs()
            preferred = settings.get("dictation_microphone", "")
            device = next((d for d in devices if bytes(d.id()).hex() == preferred), None) if preferred else QMediaDevices.defaultAudioInput()
            if device is None or device.isNull():
                raise ValueError("No selected microphone is available. Connect one or select another in Dictation settings.")
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Float)
            if not device.isFormatSupported(fmt):
                fmt = device.preferredFormat()
            kind = {QAudioFormat.Int16: "int16", QAudioFormat.Int32: "int32", QAudioFormat.Float: "float", QAudioFormat.UInt8: "uint8"}.get(fmt.sampleFormat(), "invalid")
            worker = SpeechWorker(identity, settings["dictation_model_pack"], (fmt.sampleRate(), fmt.channelCount(), kind),
                                  self)
            self.worker = worker
            self.device, self.format = device, fmt
            self.workers.add(worker)
            worker.event.connect(self._event)
            worker.finished.connect(lambda: self._retire(worker))
            worker.start()
            self.busy_changed.emit(True)
        except ValueError as error:
            self.identity, self.callback = None, None
            callback({"event": "error", "message": tr(str(error))})
            callback({"event": "done"})
            return None
        return identity

    def _retire(self, worker):
        self.workers.discard(worker)
        worker.deleteLater()

    def _event(self, identity, event):
        if identity != self.identity:
            return
        if event["event"] == "ready":
            if self.worker.stopping.is_set():
                return
            self.source = QAudioSource(self.device, self.format, self)
            self.source.stateChanged.connect(self._audio_state)
            source = self.source
            stream = source.start()
            if identity != self.identity or self.source is not source:
                return
            if stream is None or source.error() != QtAudio.Error.NoError:
                self._audio_error(identity)
                return
            self.stream = stream
            self.stream.readyRead.connect(lambda: self._audio_ready(identity))
            self.recording_changed.emit()
        callback = self.callback
        if event["event"] == "done":
            self._stop_microphone()
            self.worker = None
            self.identity, self.callback = None, None
            self.busy_changed.emit(False)
        if callback:
            callback(event)

    def _audio_ready(self, identity):
        if identity == self.identity and self.stream and self.worker:
            self.worker.submit(bytes(self.stream.readAll()))

    @Slot()
    def _audio_state(self):
        # The Windows backend's QAudio::State argument isn't convertible on
        # every PySide6 build. Only the current source and its error matter.
        if (self.source is not None and self.sender() is self.source
                and self.source.error() != QtAudio.Error.NoError):
            self._audio_error(self.identity)

    def _audio_error(self, identity):
        if identity is None or identity != self.identity:
            return
        callback = self.callback
        opening = self.stream is None or self.source.error() == QtAudio.Error.OpenError
        self.cancel(identity)
        if opening:
            location = (tr("Couldn’t start the microphone. Windows microphone privacy settings have opened.")
                        if open_microphone_privacy_settings() else
                        tr("Couldn’t start the microphone. Open Windows Settings > Privacy & security > Microphone."))
            message = location + " " + tr("Check “Microphone access” and “Let desktop apps access your microphone”. "
                                          "LetterSmith may not be listed by name. If access is on, test your input "
                                          "under Windows Settings > System > Sound.")
        else:
            message = tr("The microphone stopped responding. Check its connection and test it under "
                         "Windows Settings > System > Sound > Input, then try recording again.")
        if callback:
            callback({"event": "error", "message": message})
            callback({"event": "done"})

    def _stop_microphone(self):
        was_recording = self.recording
        source, self.source = self.source, None
        self.stream = None
        if source:
            source.blockSignals(True)
            source.stop()
            source.deleteLater()
        if was_recording:
            self.recording_changed.emit()

    def stop(self, identity):
        if identity == self.identity and self.worker:
            self._audio_ready(identity)
            self._stop_microphone()
            self.worker.stopping.set()

    def cancel(self, identity=None):
        if identity is not None and identity != self.identity:
            return
        self._stop_microphone()
        worker, self.worker = self.worker, None
        self.identity, self.callback = None, None
        if worker:
            worker.cancel()
        self.busy_changed.emit(False)

    def dispose(self):
        self.cancel()
        for worker in list(self.workers):
            worker.cancel()
            worker.wait()
