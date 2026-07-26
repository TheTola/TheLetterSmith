# ===============================
# File: sound_analyzer.py
# ===============================
from __future__ import annotations

import base64
import json
import math
import os
import shutil
import tempfile

from config import USER_SOUNDS_DIR

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Deque
from collections import deque

from PySide6 import QtCore


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Analysis format
# - We store quantized uint8 arrays packed as bytes -> zlib -> base64
# - This keeps files small and load fast.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _b64z_pack_u8(raw_u8: bytes, level: int = 6) -> str:
    comp = zlib.compress(raw_u8, level)
    return base64.b64encode(comp).decode("ascii")


def _b64z_unpack_u8(s: str) -> bytes:
    comp = base64.b64decode(s.encode("ascii"))
    return zlib.decompress(comp)


def _safe_mtime_size(path: Path) -> Tuple[float, int]:
    try:
        st = path.stat()
        return float(st.st_mtime), int(st.st_size)
    except Exception:
        return 0.0, -1


def _same_audio_identity(cached_mtime: float, cached_size: int, current_mtime: float, current_size: int) -> bool:
    """
    Decide whether an analysis cache still belongs to the current audio file.

    Exact mtime matching is too brittle on Windows because copying/normalizing a
    file can preserve size while shifting timestamp precision. Size mismatch is
    a real invalidation. Same size with only timestamp drift is accepted so the
    app can keep using an existing cache instead of forcing ffmpeg/ffprobe to
    re-analyze a song that is already cached.
    """
    if int(cached_size) != int(current_size):
        return False
    if current_size < 0:
        return False
    if abs(float(current_mtime) - float(cached_mtime)) <= 1e-3:
        return True
    return True


def _find_audio_tool(env_name: str, exe_name: str) -> str:
    """Locate ffmpeg/ffprobe without importing pydub."""
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


def _ffmpeg_pair() -> Tuple[str, str]:
    exe_ffmpeg = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    exe_ffprobe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    return (
        _find_audio_tool("FFMPEG_BIN", exe_ffmpeg),
        _find_audio_tool("FFPROBE_BIN", exe_ffprobe),
    )


def _has_ffmpeg_pair() -> bool:
    ffmpeg, ffprobe = _ffmpeg_pair()
    return bool(ffmpeg and ffprobe)


def _missing_ffmpeg_message(path: Path) -> str:
    return (
        "Offline audio analysis is disabled because ffmpeg/ffprobe was not found. "
        "Qt playback can still play the MP3. Existing .analysis.json cache files will still be used. "
        "Install ffmpeg later if you want new songs analyzed automatically. "
        f"Skipped: {path}"
    )


def _percentile_fallback(vals: List[float], pct: float) -> float:
    if not vals:
        return 0.0
    x = sorted(vals)
    k = (len(x) - 1) * (pct / 100.0)
    i = int(k)
    j = min(len(x) - 1, i + 1)
    t = k - i
    return (1.0 - t) * x[i] + t * x[j]


def _norm_by_percentiles(x, p_lo=5.0, p_hi=95.0):
    """Normalize values to 0..1 using percentiles.

    Supports numpy arrays or Python sequences. If numpy is available, always
    normalizes in numpy-space (converting sequences to np.ndarray). Falls back
    to a pure-Python percentile estimate otherwise.
    """
    try:
        import numpy as np  # type: ignore

        x_arr = np.asarray(x, dtype=np.float32)
        lo = float(np.percentile(x_arr, p_lo))
        hi = float(np.percentile(x_arr, p_hi))
        if hi <= lo + 1e-12:
            return np.zeros_like(x_arr, dtype=np.float32)

        y = (x_arr - lo) / (hi - lo)
        y = np.clip(y, 0.0, 1.0).astype(np.float32)
        return y
    except Exception:
        lo = _percentile_fallback(list(x), p_lo)
        hi = _percentile_fallback(list(x), p_hi)
        if hi <= lo + 1e-12:
            return [0.0 for _ in x]
        out = []
        inv = 1.0 / (hi - lo)
        for v in x:
            y = (float(v) - lo) * inv
            if y < 0.0:
                y = 0.0
            elif y > 1.0:
                y = 1.0
            out.append(float(y))
        return out


def _beat_from_bass(bass_norm, hop_ms: int) -> List[float]:
    """
    Simple onset/beat cue:
    - Use positive derivative of bass energy
    - Adaptive threshold via median + MAD
    - Peaks spaced by >= 220ms
    Returns beat pulse array in 0..1.
    """
    n = len(bass_norm)
    if n <= 2:
        return [0.0] * n

    try:
        import numpy as np  # type: ignore
        b = np.asarray(bass_norm, dtype=np.float32)
        d = np.diff(b, prepend=b[0])
        d = np.maximum(d, 0.0)

        med = float(np.median(d))
        mad = float(np.median(np.abs(d - med))) + 1e-6
        thr = med + 3.0 * mad

        min_sep = max(1, int(round(220.0 / max(1, hop_ms))))
        beats = np.zeros(n, dtype=np.float32)

        last = -10_000
        for i in range(1, n - 1):
            if i - last < min_sep:
                continue
            if d[i] > thr and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
                beats[i] = 1.0
                last = i

        # soften pulse: small decay around the beat
        for i in range(n):
            if beats[i] > 0.5:
                for k, v in ((1, 0.55), (2, 0.25), (3, 0.12)):
                    if i + k < n:
                        beats[i + k] = max(beats[i + k], v)
                    if i - k >= 0:
                        beats[i - k] = max(beats[i - k], v)

        return beats.tolist()
    except Exception:
        # fallback: very simple peak marking
        d = [0.0] * n
        for i in range(1, n):
            dv = bass_norm[i] - bass_norm[i - 1]
            d[i] = dv if dv > 0.0 else 0.0

        med = _percentile_fallback(d, 50.0)
        mad = _percentile_fallback([abs(v - med) for v in d], 50.0) + 1e-6
        thr = med + 3.0 * mad

        min_sep = max(1, int(round(220.0 / max(1, hop_ms))))
        beats = [0.0] * n
        last = -10_000
        for i in range(1, n - 1):
            if i - last < min_sep:
                continue
            if d[i] > thr and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
                beats[i] = 1.0
                last = i
        return beats


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
            self._out_file.parent.mkdir(parents=True, exist_ok=True)
            self._out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.finished.emit(str(self._path), payload)
        except Exception as e:
            self.failed.emit(str(self._path), f"{type(e).__name__}: {e}")

    # --- analysis core ---
    def _decode_audio_segment(self, path: Path):
        """
        Decode audio through pydub/ffmpeg.

        Qt playback can succeed even when the offline analyzer fails, because they
        use different backends. For stubborn filenames or paths, retry by copying
        the file to a short ASCII temp path before giving up.
        """
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        ffmpeg, ffprobe = _ffmpeg_pair()
        if not ffmpeg or not ffprobe:
            raise RuntimeError(_missing_ffmpeg_message(path))

        try:
            from pydub import AudioSegment  # type: ignore
            import pydub  # type: ignore
            pydub.AudioSegment.converter = ffmpeg  # type: ignore[attr-defined]
            pydub.AudioSegment.ffprobe = ffprobe  # type: ignore[attr-defined]
        except Exception as e:
            raise RuntimeError(f"pydub missing: {e!r}")

        try:
            return AudioSegment.from_file(str(path))
        except Exception as first_error:
            tmp_dir: Optional[Path] = None
            try:
                tmp_dir = Path(tempfile.mkdtemp(prefix="lettersmith_audio_"))
                suffix = path.suffix.lower() or ".mp3"
                tmp_path = tmp_dir / f"input{suffix}"
                shutil.copy2(path, tmp_path)
                return AudioSegment.from_file(str(tmp_path))
            except Exception as second_error:
                raise RuntimeError(
                    "Audio decode failed. The file exists and can still play through Qt, "
                    "but the offline analyzer could not decode it. If the message includes "
                    "WinError 2, Windows is usually missing ffmpeg/ffprobe - not the MP3. "
                    "Install ffmpeg or keep using the cached analysis when available. "
                    f"File: {path} | First error: {type(first_error).__name__}: {first_error} | "
                    f"Retry error: {type(second_error).__name__}: {second_error}"
                ) from second_error
            finally:
                if tmp_dir is not None:
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass

    def _analyze_mp3(self, path: Path, hop_ms: int, nbands: int) -> dict:
        mtime, size = _safe_mtime_size(path)

        seg = self._decode_audio_segment(path)
        seg = seg.set_channels(1)

        # Target SR for analysis (keep light, still believable)
        sr = 22050
        seg = seg.set_frame_rate(sr)

        sw = int(seg.sample_width)
        if sw <= 0:
            sw = 2

        # Samples -> float32 [-1, 1]
        try:
            import numpy as np  # type: ignore
        except Exception:
            np = None  # type: ignore

        if np is None:
            # fallback: no numpy means no FFT bands; still return envelope-ish analysis
            samples_i = seg.get_array_of_samples()
            denom = float(2 ** (8 * sw - 1))
            samples = [float(v) / denom for v in samples_i]
            return self._fallback_envelope_only(path, mtime, size, sr, hop_ms, samples)

        samples_i = np.array(seg.get_array_of_samples(), dtype=np.float32)
        denom = float(2 ** (8 * sw - 1))
        samples = samples_i / denom

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

    def _fallback_envelope_only(self, path: Path, mtime: float, size: int, sr: int, hop_ms: int, samples: List[float]) -> dict:
        hop = int(round(sr * (hop_ms / 1000.0)))
        hop = max(64, hop)
        win = max(hop * 2, 1024)

        n = len(samples)
        if n <= 0:
            raise RuntimeError("Empty audio")

        frames = 1 + max(0, (n - 1) // hop)

        lvl = [0.0] * frames
        for i in range(frames):
            start = i * hop
            end = min(n, start + win)
            if start >= n:
                break
            acc = 0.0
            cnt = 0
            for v in samples[start:end]:
                acc += v * v
                cnt += 1
            rms = math.sqrt(acc / max(1, cnt))
            lvl[i] = 20.0 * math.log10(rms + 1e-9)
            if i % 200 == 0:
                self.progress.emit(str(path), int(round(100.0 * (i / max(1, frames)))))

        lvl_n = _norm_by_percentiles(lvl, 5.0, 95.0)
        # in fallback mode, reuse lvl as all bands; beat is 0
        lvl_u8 = bytes([int(max(0, min(255, round(v * 255.0)))) for v in lvl_n])
        z = _b64z_pack_u8(lvl_u8)

        payload = {
            "version": 1,
            "codec": "b64z_u8",
            "src": {"path": str(path), "mtime": mtime, "size": size},
            "sr": sr,
            "hop_ms": hop_ms,
            "frames": int(frames),
            "nbands": 0,
            "edges_hz": [],
            "q": {"lvl": z, "bass": z, "mid": z, "high": z, "beat": _b64z_pack_u8(bytes([0] * frames)), "spec": ""},
            "profile": {"lvl_mean": float(sum(lvl_n) / max(1, len(lvl_n))), "bass_mean": 0.0, "bright_mean": 0.0, "bass_ratio": 0.0},
        }
        self.progress.emit(str(path), 100)
        return payload


class AudioAnalysisManager(QtCore.QObject):
    """
    One-at-a-time queue manager (keeps UI responsive, avoids CPU spikes).

    - ensure_analyzed(path, priority=True/False): enqueue if missing/stale
    - enqueue_missing(processed_dir): enqueue any mp3 lacking analysis
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

        # Current Letter Smith archive path. Older builds used "archive"; the
        # active Sound tab archive uses "appssong". Keeping the manager aligned
        # prevents analysis jobs from searching a dead folder.
        base = self.project_root / USER_SOUNDS_DIR / "appssong"
        self.processed_dir = base / "processed"
        self.analysis_dir = base / "analysis"
        self.processed_dir.mkdir(parents=True, exist_ok=True)
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
        self._decoder_missing_notified: set[str] = set()

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ public â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def is_busy(self) -> bool:
        return self._busy

    def shutdown(self) -> None:
        self._queue.clear()
        self._pending.clear()
        self._cleanup_thread()
        self._set_busy(False)

    def enqueue_missing(self) -> None:
        """
        Archive-wide analysis is intentionally conservative.

        If ffmpeg/ffprobe is unavailable, do not enqueue anything. Cached files
        are loaded on demand, and playback still works through Qt. This prevents
        repeated failed analyzer jobs every time the Sound tab opens.
        """
        if not self.processed_dir.exists():
            return
        if not _has_ffmpeg_pair():
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

        if not _has_ffmpeg_pair():
            if key not in self._decoder_missing_notified:
                self._decoder_missing_notified.add(key)
                self.analysisFailed.emit(key, _missing_ffmpeg_message(p))
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
            cached_mtime = float(src.get("mtime", -1.0))
            cached_size = int(src.get("size", -1))
            cur_mtime, cur_size = _safe_mtime_size(p)

            if not _same_audio_identity(cached_mtime, cached_size, cur_mtime, cur_size):
                return None

            src["path"] = str(p)
            src["mtime"] = cur_mtime
            src["size"] = cur_size
            payload["src"] = src

            try:
                out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except Exception:
                pass

            return payload
        except Exception:
            return None

    # internals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def analysis_file_for_debug(self, path: Path) -> Path:
        return self._analysis_file_for(path)

    def _analysis_file_for(self, path: Path) -> Path:
        p = Path(path).resolve()
        return self.analysis_dir / f"{p.name}.analysis.json"

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
                self._thread.wait(50)
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

