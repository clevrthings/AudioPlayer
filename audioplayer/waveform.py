from __future__ import annotations

import math
import time

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6.QtCore import QThread, Signal


def format_axis_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class TimeAxisItem(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [format_axis_time(float(v)) for v in values]


class WaveformJob(QThread):
    progressReady = Signal(int, str, object, object, int, int)
    resultReady = Signal(int, str, object, object)
    errorRaised = Signal(int, str, str)

    def __init__(
        self,
        request_id: int,
        path: str,
        points: int,
        emit_progress: bool,
        progress_interval: float = 0.12,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.points = points
        self.emit_progress = emit_progress
        self.progress_interval = progress_interval
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            info = sf.info(self.path)
            total_frames = int(info.frames)
            sample_rate = int(info.samplerate)
            channels = max(1, int(info.channels))

            if total_frames <= 0 or sample_rate <= 0:
                x = np.array([0.0], dtype=np.float32)
                amp = np.zeros((1, channels), dtype=np.float32)
                self.resultReady.emit(self.request_id, self.path, x, amp)
                return

            bucket = max(1, math.ceil(total_frames / self.points))
            bins = max(1, math.ceil(total_frames / bucket))
            x = np.linspace(0, total_frames / float(sample_rate), bins, dtype=np.float32)
            amp = np.zeros((bins, channels), dtype=np.float32)

            chunk_frames = min(max(bucket * 24, 8192), 262144)
            frame_pos = 0
            last_emit = 0.0

            with sf.SoundFile(self.path) as audio_file:
                while not self._cancelled:
                    chunk = audio_file.read(chunk_frames, dtype="float32", always_2d=True)
                    if chunk.size == 0:
                        break

                    abs_chunk = np.abs(chunk)
                    frame_count = abs_chunk.shape[0]
                    idx = (frame_pos + np.arange(frame_count, dtype=np.int64)) // bucket
                    idx[idx >= bins] = bins - 1
                    channel_count = min(amp.shape[1], abs_chunk.shape[1])
                    for channel_index in range(channel_count):
                        np.maximum.at(amp[:, channel_index], idx, abs_chunk[:, channel_index])
                    frame_pos += frame_count

                    if self.emit_progress:
                        now = time.monotonic()
                        if now - last_emit >= self.progress_interval:
                            filled = min(bins, math.ceil(frame_pos / bucket))
                            self.progressReady.emit(self.request_id, self.path, x, amp.copy(), filled, bins)
                            last_emit = now

            if self._cancelled:
                return

            self.resultReady.emit(self.request_id, self.path, x, amp)
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                self.errorRaised.emit(self.request_id, self.path, str(exc))
