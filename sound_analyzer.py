# ===============================
# File: sound_analyzer.py
# ===============================
from __future__ import annotations

import base64
import json
import math
import os
import tempfile

from config import USER_SOUNDS_DIR
from audio_tools import AudioToolError, decode_mono_pcm16

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Deque
from collections import deque

from PySide6 import QtCore

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Analysis format
# - We store quantized uint8 arrays packed as bytes -> zlib -> base64
# - This keeps files small and load fast.
# ─────────────────────────────────────────────────────────────────────────────

def _b64z_pack_u8(raw_u8: bytes, level: int = 6) -> str:
    comp = zlib.compress(raw_u8, level)
    return base64.b64encode(comp).decode("ascii")


def _safe_mtime_size(path: Path) -> Tuple[float, int]:
    try:
        st = path.stat()
        return float(st.st_mtime), int(st.st_size)
    except OSError:
        return 0.0, -1


def analysis_runtime_status() -> tuple[bool, str]:
    """Return whether the optional analysis runtime can operate."""
    if np is None:
        return False, "NumPy is not installed."
    return True, ""


def _require_numpy():
    available, reason = analysis_runtime_status()
    if not available:
        raise RuntimeError(reason)
    return np


def _norm_by_percentiles(values, p_lo: float = 5.0, p_hi: float = 95.0):
    numpy = _require_numpy()
    array = numpy.asarray(values, dtype=numpy.float32)
    lo = float(numpy.percentile(array, p_lo))
    hi = float(numpy.percentile(array, p_hi))
    if hi <= lo + 1e-12:
        return numpy.zeros_like(array, dtype=numpy.float32)
    return numpy.clip((array - lo) / (hi - lo), 0.0, 1.0).astype(numpy.float32)


def _beat_from_bass(bass_norm, hop_ms: int) -> List[float]:
    numpy = _require_numpy()
    bass = numpy.asarray(bass_norm, dtype=numpy.float32)
    count = len(bass)
    if count <= 2:
        return [0.0] * count

    derivative = numpy.maximum(numpy.diff(bass, prepend=bass[0]), 0.0)
    median = float(numpy.median(derivative))
    mad = float(numpy.median(numpy.abs(derivative - median))) + 1e-6
    threshold = median + 3.0 * mad
    minimum_separation = max(1, int(round(220.0 / max(1, hop_ms))))
    beats = numpy.zeros(count, dtype=numpy.float32)

    last = -10_000
    for index in range(1, count - 1):
        if index - last < minimum_separation:
            continue
        if (
            derivative[index] > threshold
            and derivative[index] >= derivative[index - 1]
            and derivative[index] >= derivative[index + 1]
        ):
            beats[index] = 1.0
            last = index

    for index in range(count):
        if beats[index] <= 0.5:
            continue
        for offset, value in ((1, 0.55), (2, 0.25), (3, 0.12)):
            if index + offset < count:
                beats[index + offset] = max(beats[index + offset], value)
            if index - offset >= 0:
                beats[index - offset] = max(beats[index - offset], value)
    return beats.tolist()


def _atomic_write_json(path: Path, payload: dict) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class _Job:
    path: Path
    priority: bool = False


class AudioAnalysisWorker(QtCore.QObject):
    progress = QtCore.Signal(str, int)          # path, percent (0..100)
    finished = QtCore.Signal(str, dict)         # path, payload
    failed = QtCore.Signal(str, str)            # path, error msg

    def __init__(self, path: Path, out_file: Path, hop_ms: int = 20, nbands: int = 32):
        super().__init__()
        self._path = Path(path).resolve()
        self._out_file = Path(out_file).resolve()
        self._hop_ms = int(max(10, hop_ms))
        self._nbands = int(max(16, nbands))

        self._abort = False

    def abort(self) -> None:
        self._abort = True

    # --- worker entrypoint ---
    @QtCore.Slot()
    def run(self) -> None:
        try:
            payload = self._analyze_mp3(self._path, hop_ms=self._hop_ms, nbands=self._nbands)
            if self._abort:
                return
            _atomic_write_json(self._out_file, payload)
            self.finished.emit(str(self._path), payload)
        except Exception as e:
            if self._abort:
                return
            self.failed.emit(str(self._path), f"{type(e).__name__}: {e}")

    # --- analysis core ---
    def _analyze_mp3(self, path: Path, hop_ms: int, nbands: int) -> dict:
        mtime, size = _safe_mtime_size(path)

        numpy = _require_numpy()
        sr = 22050
        try:
            pcm = decode_mono_pcm16(path, sample_rate=sr, cancel_check=lambda: self._abort)
        except AudioToolError as exc:
            raise RuntimeError(str(exc)) from exc

        samples = numpy.frombuffer(pcm, dtype=numpy.int16).astype(numpy.float32) / 32768.0
        if samples.size <= 0:
            raise RuntimeError("Empty audio")

        hop = int(round(sr * (hop_ms / 1000.0)))
        hop = max(64, hop)

        win = 2048
        if win < hop * 2:
            win = 2 ** int(math.ceil(math.log2(hop * 2)))

        window = np.hanning(win).astype(np.float32)

        # Frames count
        n = int(samples.shape[0])
        if n <= 0:
            raise RuntimeError("Empty audio")

        n_frames = 1 + max(0, (n - 1) // hop)
        # Edges: 20..8000 Hz log spaced
        f_lo = 20.0
        f_hi = min(8000.0, sr * 0.45)
        edges = np.geomspace(f_lo, f_hi, nbands + 1).astype(np.float32)

        # FFT bin freqs
        freqs = np.fft.rfftfreq(win, d=1.0 / sr).astype(np.float32)
        # Precompute bin ranges per band
        band_bins: List[Tuple[int, int]] = []
        for i in range(nbands):
            lo = float(edges[i])
            hi = float(edges[i + 1])
            a = int(np.searchsorted(freqs, lo, side="left"))
            b = int(np.searchsorted(freqs, hi, side="right"))
            a = max(0, min(a, freqs.shape[0] - 1))
            b = max(a + 1, min(b, freqs.shape[0]))
            band_bins.append((a, b))

        # Bass/mid/high bin ranges
        bass_hi = 150.0
        mid_hi = 2000.0
        bass_a = int(np.searchsorted(freqs, 20.0, side="left"))
        bass_b = int(np.searchsorted(freqs, bass_hi, side="right"))
        mid_a = int(np.searchsorted(freqs, bass_hi, side="left"))
        mid_b = int(np.searchsorted(freqs, mid_hi, side="right"))
        high_a = int(np.searchsorted(freqs, mid_hi, side="left"))
        high_b = int(np.searchsorted(freqs, f_hi, side="right"))

        bass_a = max(0, min(bass_a, freqs.shape[0] - 1))
        bass_b = max(bass_a + 1, min(bass_b, freqs.shape[0]))
        mid_a = max(0, min(mid_a, freqs.shape[0] - 1))
        mid_b = max(mid_a + 1, min(mid_b, freqs.shape[0]))
        high_a = max(0, min(high_a, freqs.shape[0] - 1))
        high_b = max(high_a + 1, min(high_b, freqs.shape[0]))

        # Allocate
        lvl = np.zeros(n_frames, dtype=np.float32)
        bass = np.zeros(n_frames, dtype=np.float32)
        mid = np.zeros(n_frames, dtype=np.float32)
        high = np.zeros(n_frames, dtype=np.float32)
        spec = np.zeros((n_frames, nbands), dtype=np.float32)

        # Blocked STFT
        block = 256
        for f0 in range(0, n_frames, block):
            if self._abort:
                return {}
            f1 = min(n_frames, f0 + block)

            # Build frame matrix [B, win] without huge memory spikes
            frames = np.zeros((f1 - f0, win), dtype=np.float32)
            for i, fi in enumerate(range(f0, f1)):
                start = fi * hop
                end = start + win
                if start >= n:
                    break
                chunk = samples[start:min(end, n)]
                frames[i, : chunk.shape[0]] = chunk

            frames *= window[None, :]

            # RMS (lvl)
            lvl[f0:f1] = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)

            # FFT mags
            mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32) + 1e-12

            # band sums (log-ish)
            for bi, (a, b) in enumerate(band_bins):
                spec[f0:f1, bi] = np.mean(mag[:, a:b], axis=1)

            bass[f0:f1] = np.mean(mag[:, bass_a:bass_b], axis=1)
            mid[f0:f1] = np.mean(mag[:, mid_a:mid_b], axis=1)
            high[f0:f1] = np.mean(mag[:, high_a:high_b], axis=1)

            # progress per file
            pct = int(round(100.0 * (f1 / max(1, n_frames))))
            self.progress.emit(str(path), min(100, max(0, pct)))

        # Log scale for better dynamics
        lvl_db = 20.0 * np.log10(lvl + 1e-9)
        bass_db = 20.0 * np.log10(bass + 1e-9)
        mid_db = 20.0 * np.log10(mid + 1e-9)
        high_db = 20.0 * np.log10(high + 1e-9)
        spec_db = 20.0 * np.log10(spec + 1e-9)

        # Normalize by percentiles so quiet stays quiet and loud stays loud
        lvl_n = _norm_by_percentiles(lvl_db, 5.0, 95.0)
        bass_n = _norm_by_percentiles(bass_db, 5.0, 95.0)
        mid_n = _norm_by_percentiles(mid_db, 5.0, 95.0)
        high_n = _norm_by_percentiles(high_db, 5.0, 95.0)

        # Normalize spec per-band
        spec_n = np.zeros_like(spec_db, dtype=np.float32)
        for bi in range(nbands):
            spec_n[:, bi] = _norm_by_percentiles(spec_db[:, bi], 5.0, 95.0)

        # Beat cue from bass
        beat = _beat_from_bass(bass_n.tolist(), hop_ms=hop_ms)
        beat_n = beat
        try:
            beat_n = _norm_by_percentiles(beat_n, 0.0, 100.0)
        except Exception:
            pass

        # Profile summary for visuals
        bass_mean = float(np.mean(bass_n))
        lvl_mean = float(np.mean(lvl_n))
        bright_mean = float(np.mean(high_n))
        bass_ratio = float(bass_mean / max(1e-6, (bass_mean + bright_mean)))

        # Quantize to u8
        def q_u8(arr) -> bytes:
            # Accept numpy arrays or Python sequences (e.g., lists from beat detection).
            a = np.asarray(arr, dtype=np.float32)
            a = np.clip(a, 0.0, 1.0)
            return (a * 255.0 + 0.5).astype(np.uint8).tobytes()

        lvl_u8 = q_u8(lvl_n)
        bass_u8 = q_u8(bass_n)
        mid_u8 = q_u8(mid_n)
        high_u8 = q_u8(high_n)
        beat_u8 = q_u8(beat_n)
        spec_u8 = q_u8(spec_n.reshape(-1))

        payload = {
            "version": 1,
            "codec": "b64z_u8",
            "src": {"path": str(path), "mtime": mtime, "size": size},
            "sr": sr,
            "hop_ms": hop_ms,
            "frames": int(n_frames),
            "nbands": int(nbands),
            "edges_hz": [float(x) for x in edges.tolist()],
            "q": {
                "lvl": _b64z_pack_u8(lvl_u8),
                "bass": _b64z_pack_u8(bass_u8),
                "mid": _b64z_pack_u8(mid_u8),
                "high": _b64z_pack_u8(high_u8),
                "beat": _b64z_pack_u8(beat_u8),
                "spec": _b64z_pack_u8(spec_u8),
            },
            "profile": {
                "lvl_mean": lvl_mean,
                "bass_mean": bass_mean,
                "bright_mean": bright_mean,
                "bass_ratio": bass_ratio,
            },
        }
        return payload



class AudioAnalysisManager(QtCore.QObject):
    """
    One-at-a-time queue manager (keeps UI responsive, avoids CPU spikes).

    - ensure_analyzed(path, priority=True/False): enqueue if missing/stale
    - enqueue_missing(): optional maintenance sweep explicitly requested by a caller
    - load_cached(path): read analysis now (fast) if present/valid

    Signals:
    - batchProgress(done, total, current_name, current_pct)
    - analysisReady(path, payload)
    - analysisFailed(path, msg)
    - busyChanged(bool)
    """
    batchProgress = QtCore.Signal(int, int, str, int)
    analysisReady = QtCore.Signal(str, dict)
    analysisFailed = QtCore.Signal(str, str)
    busyChanged = QtCore.Signal(bool)

    def __init__(self, project_root: Path, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()

        base = self.project_root / USER_SOUNDS_DIR / "appssong"
        self.processed_dir = base / "processed"
        self.analysis_dir = base / "analysis"
        self.analysis_dir.mkdir(parents=True, exist_ok=True)

        self._queue: Deque[_Job] = deque()
        self._pending: set[str] = set()

        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[AudioAnalysisWorker] = None

        self._total = 0
        self._done = 0
        self._busy = False

        self._hop_ms = 20
        self._nbands = 32

    # ───────────────────── public ─────────────────────
    def is_busy(self) -> bool:
        return self._busy

    def shutdown(self) -> None:
        self._queue.clear()
        self._pending.clear()
        self._cleanup_thread()
        self._set_busy(False)

    def enqueue_missing(self) -> None:
        if not self.processed_dir.exists():
            return

        mp3s = sorted(self.processed_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in mp3s:
            if self._needs_analysis(p):
                self.ensure_analyzed(p, priority=False)

    def ensure_analyzed(self, path: Path, priority: bool = True) -> None:
        p = Path(path).resolve()
        key = str(p)

        # Already valid -> emit ready immediately
        cached = self.load_cached(p)
        if cached is not None:
            self.analysisReady.emit(key, cached)
            return

        if key in self._pending:
            return

        job = _Job(p, priority=bool(priority))
        if job.priority:
            self._queue.appendleft(job)
        else:
            self._queue.append(job)
        self._pending.add(key)

        # total/done for this run
        self._recount_totals()

        if not self._busy:
            self._start_next()

    def load_cached(self, path: Path) -> Optional[dict]:
        p = Path(path).resolve()
        out = self._analysis_file_for(p)
        if not out.exists():
            return None

        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            return None

        try:
            src = payload.get("src", {})
            mtime = float(src.get("mtime", -1.0))
            size = int(src.get("size", -1))
            cur_mtime, cur_size = _safe_mtime_size(p)
            if abs(cur_mtime - mtime) > 1e-6 or cur_size != size:
                return None
        except Exception:
            return None

        return payload

    # ───────────────────── internals ─────────────────────
    def _analysis_file_for(self, path: Path) -> Path:
        # name-based, but also invalidated by mtime/size check in payload
        return self.analysis_dir / (path.name + ".analysis.json")

    def _needs_analysis(self, path: Path) -> bool:
        return self.load_cached(path) is None

    def _recount_totals(self) -> None:
        # total is the number of unique jobs pending + already running remainder
        q = list(self._queue)
        self._total = len(q) + (0 if self._worker is None else 1) + self._done
        if self._total < self._done:
            self._total = self._done

    def _set_busy(self, v: bool) -> None:
        v = bool(v)
        if v == self._busy:
            return
        self._busy = v
        self.busyChanged.emit(v)

    def _start_next(self) -> None:
        # cleanup old thread
        self._cleanup_thread()

        if not self._queue:
            self._pending.clear()
            self._set_busy(False)
            # keep last totals consistent
            self.batchProgress.emit(self._done, max(self._done, 1), "", 100)
            return

        job = self._queue.popleft()
        key = str(job.path)

        # It might have become cached while waiting
        cached = self.load_cached(job.path)
        if cached is not None:
            self._pending.discard(key)
            self._done += 1
            self._recount_totals()
            self.batchProgress.emit(self._done, max(self._total, 1), job.path.name, 100)
            self.analysisReady.emit(key, cached)
            QtCore.QTimer.singleShot(0, self._start_next)
            return

        out = self._analysis_file_for(job.path)

        self._thread = QtCore.QThread(self)
        self._worker = AudioAnalysisWorker(job.path, out, hop_ms=self._hop_ms, nbands=self._nbands)
        self._worker.moveToThread(self._thread)

        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.started.connect(self._worker.run)

        self._set_busy(True)
        self.batchProgress.emit(self._done, max(self._total, 1), job.path.name, 0)
        self._thread.start()

    def _cleanup_thread(self) -> None:
        try:
            if self._worker is not None:
                self._worker.abort()
        except Exception:
            pass

        if self._thread is not None:
            try:
                self._thread.quit()
                self._thread.wait(2000)
            except Exception:
                pass

        self._worker = None
        self._thread = None

    def _on_progress(self, path_str: str, pct: int) -> None:
        name = Path(path_str).name
        self.batchProgress.emit(self._done, max(self._total, 1), name, int(pct))

    def _on_finished(self, path_str: str, payload: dict) -> None:
        self._pending.discard(path_str)
        self._done += 1
        self._recount_totals()
        self.batchProgress.emit(self._done, max(self._total, 1), Path(path_str).name, 100)
        self.analysisReady.emit(path_str, payload)

        QtCore.QTimer.singleShot(0, self._start_next)

    def _on_failed(self, path_str: str, msg: str) -> None:
        self._pending.discard(path_str)
        self._done += 1
        self._recount_totals()
        self.batchProgress.emit(self._done, max(self._total, 1), Path(path_str).name, 100)
        self.analysisFailed.emit(path_str, msg)

        QtCore.QTimer.singleShot(0, self._start_next)


__all__ = ["AudioAnalysisManager", "analysis_runtime_status"]
