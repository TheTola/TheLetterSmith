"""Decode a compact overview of a track for the Sound timeline."""

from __future__ import annotations

from array import array
from collections import OrderedDict
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat


class SoundWaveform(QtCore.QObject):
    peaksChanged = QtCore.Signal(bytes)

    _SAMPLE_RATE = 8000
    _BUCKET_SAMPLES = 400
    _DISPLAY_POINTS = 160
    _CACHE_SIZE = 8

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._decoder: QAudioDecoder | None = None
        self._source: tuple[Path, int, int] | None = None
        self._cache: OrderedDict[tuple[Path, int, int], bytes] = OrderedDict()
        self._peaks: list[int] = []
        self._bucket_remaining = self._BUCKET_SAMPLES
        self._bucket_peak = 0

    def set_source(self, path: Path | None) -> None:
        source = None
        if path is not None:
            try:
                stat = path.stat()
                source = (path, stat.st_size, stat.st_mtime_ns)
            except OSError:
                pass
        if source == self._source:
            return
        self._source = source
        self._release_decoder()
        self._peaks.clear()
        self._bucket_remaining = self._BUCKET_SAMPLES
        self._bucket_peak = 0
        if source in self._cache:
            self._cache.move_to_end(source)
            self.peaksChanged.emit(self._cache[source])
            return
        self.peaksChanged.emit(b"")
        if source is None:
            return

        decoder = self._decoder = QAudioDecoder(self)
        audio_format = QAudioFormat()
        audio_format.setChannelCount(1)
        audio_format.setSampleRate(self._SAMPLE_RATE)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        decoder.setAudioFormat(audio_format)
        decoder.bufferReady.connect(self._read_buffer)
        decoder.finished.connect(self._finish)
        decoder.error.connect(self._fail)
        decoder.setSource(QtCore.QUrl.fromLocalFile(str(source[0])))
        decoder.start()

    @QtCore.Slot()
    def _read_buffer(self) -> None:
        decoder = self._decoder
        if decoder is None or self.sender() is not decoder:
            return
        buffer = decoder.read()
        if not buffer.isValid():
            return
        samples = array("h", buffer.constData().tobytes())
        offset = 0
        while offset < len(samples):
            count = min(self._bucket_remaining, len(samples) - offset)
            segment = samples[offset:offset + count]
            self._bucket_peak = max(self._bucket_peak, -min(segment), max(segment))
            self._bucket_remaining -= count
            offset += count
            if not self._bucket_remaining:
                self._peaks.append(self._bucket_peak)
                self._bucket_remaining = self._BUCKET_SAMPLES
                self._bucket_peak = 0

    @QtCore.Slot()
    def _finish(self) -> None:
        source = self._source
        if source is None or self._decoder is None or self.sender() is not self._decoder:
            return
        if self._bucket_remaining != self._BUCKET_SAMPLES:
            self._peaks.append(self._bucket_peak)
        count = len(self._peaks)
        points = min(count, self._DISPLAY_POINTS)
        scale = 255 / (max(self._peaks, default=0) or 1)
        peaks = bytes(
            round(scale * max(self._peaks[i * count // points:(i + 1) * count // points]))
            for i in range(points)
        )
        self._release_decoder()
        self._peaks.clear()
        self._cache[source] = peaks
        if len(self._cache) > self._CACHE_SIZE:
            self._cache.popitem(last=False)
        self.peaksChanged.emit(peaks)

    def _fail(self, _error: QAudioDecoder.Error) -> None:
        if self._decoder is not None and self.sender() is self._decoder:
            self._release_decoder()
            self._peaks.clear()

    def _release_decoder(self) -> None:
        decoder, self._decoder = self._decoder, None
        if decoder is not None:
            decoder.stop()
            decoder.deleteLater()
