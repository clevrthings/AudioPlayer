from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Slot
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMessageBox

from audioplayer.waveform import WaveformJob


class WaveformController:
    def __init__(self, host) -> None:
        self.host = host

    def __getattr__(self, name):
        return getattr(self.host, name)

    def __setattr__(self, name, value):
        if name == "host":
            object.__setattr__(self, name, value)
            return
        host = object.__getattribute__(self, "host")
        if hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return
        setattr(host, name, value)
    def _align_wave_arrays(self, x: np.ndarray, amplitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_arr = np.asarray(x, dtype=np.float32).reshape(-1)
        amp_arr = np.asarray(amplitude, dtype=np.float32).reshape(-1)
        if x_arr.size == 0 or amp_arr.size == 0:
            return x_arr[:0], amp_arr[:0]
        n = min(x_arr.size, amp_arr.size)
        return x_arr[:n], amp_arr[:n]

    def _align_wave_channels(self, x: np.ndarray, amplitudes) -> tuple[np.ndarray, np.ndarray]:
        x_arr = np.asarray(x, dtype=np.float32).reshape(-1)
        amp_arr = np.asarray(amplitudes, dtype=np.float32)
        if amp_arr.ndim == 0:
            amp_arr = amp_arr.reshape(1, 1)
        elif amp_arr.ndim == 1:
            amp_arr = amp_arr.reshape(-1, 1)
        elif amp_arr.ndim > 2:
            amp_arr = amp_arr.reshape(amp_arr.shape[0], -1)
        if x_arr.size == 0 or amp_arr.size == 0:
            return x_arr[:0], np.empty((0, 1), dtype=np.float32)
        n = min(x_arr.size, amp_arr.shape[0])
        x_arr = x_arr[:n]
        amp_arr = np.ascontiguousarray(amp_arr[:n], dtype=np.float32)
        amp_arr = np.nan_to_num(amp_arr, nan=0.0, posinf=1.0, neginf=0.0, copy=False)
        amp_arr = np.clip(amp_arr, 0.0, 1.0)
        return x_arr, amp_arr

    @staticmethod
    def _sanitize_wave_array(values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return arr
        # Keep waveform rendering stable even when source data briefly contains non-finite values.
        return np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0, copy=False)

    def _safe_set_step_wave_item(
        self,
        item: pg.PlotDataItem,
        x_edges: np.ndarray,
        y_values: np.ndarray,
        fill_level: float = 0.0,
    ) -> None:
        x_arr = self._sanitize_wave_array(x_edges)
        y_arr = self._sanitize_wave_array(y_values)
        n = max(0, min(y_arr.size, max(0, x_arr.size - 1)))
        if n <= 0:
            item.setData([], [], connect="all")
            return
        x_arr = np.ascontiguousarray(x_arr[: n + 1], dtype=np.float32)
        y_arr = np.ascontiguousarray(y_arr[:n], dtype=np.float32)
        # pyqtgraph clipping currently breaks stepMode center by slicing x/y equally.
        # Ensure this item always renders with safe settings.
        if item.opts.get("clipToView"):
            item.setClipToView(False)
        item.setData(x_arr, y_arr, connect="all", fillLevel=float(fill_level))

    def _compute_wave_edges(self, x_values: np.ndarray) -> np.ndarray:
        x_arr = np.asarray(x_values, dtype=np.float32).reshape(-1)
        if x_arr.size == 0:
            return x_arr
        if x_arr.size == 1:
            width = max(self.duration_s, self.min_window_s, 1.0)
            return np.array(
                [max(0.0, float(x_arr[0]) - (width / 2.0)), float(x_arr[0]) + (width / 2.0)],
                dtype=np.float32,
            )

        dx = np.diff(x_arr)
        mids = (x_arr[:-1] + x_arr[1:]) / 2.0
        first = max(0.0, float(x_arr[0]) - float(dx[0]) / 2.0)
        last = float(x_arr[-1]) + float(dx[-1]) / 2.0
        return np.concatenate(([first], mids, [last])).astype(np.float32, copy=False)

    @staticmethod
    def _combine_channels_to_single(amplitudes: np.ndarray) -> np.ndarray:
        amp_arr = np.asarray(amplitudes, dtype=np.float32)
        if amp_arr.ndim == 1:
            return amp_arr
        if amp_arr.size == 0:
            return np.asarray([], dtype=np.float32)
        return np.max(amp_arr, axis=1).astype(np.float32, copy=False)

    def _ensure_channel_wave_items(self, channel_count: int) -> None:
        target_count = max(0, int(channel_count))
        while len(self._channel_wave_items) > target_count:
            top_item, bottom_item = self._channel_wave_items.pop()
            self.plot.removeItem(top_item)
            self.plot.removeItem(bottom_item)
        while len(self._channel_wave_items) < target_count:
            top_item = self.plot.plot(
                [],
                [],
                pen=pg.mkPen(width=1.0, color=self._wave_top_color),
                fillLevel=0,
                brush=pg.mkBrush(self._wave_fill_color),
                stepMode="center",
            )
            bottom_item = self.plot.plot(
                [],
                [],
                pen=pg.mkPen(width=1.0, color=self._wave_bottom_color),
                fillLevel=0,
                brush=pg.mkBrush(self._wave_fill_color),
                stepMode="center",
            )
            top_item.setClipToView(False)
            bottom_item.setClipToView(False)
            self._channel_wave_items.append((top_item, bottom_item))
        self._apply_channel_wave_item_styles()

    def _apply_channel_wave_item_styles(self) -> None:
        total = max(1, len(self._channel_wave_items))
        for index, (top_item, bottom_item) in enumerate(self._channel_wave_items):
            fade = 1.0 if total == 1 else (0.95 - (index / (total - 1)) * 0.22)
            fill = QColor(self._wave_fill_color)
            fill.setAlpha(max(36, min(225, int(fill.alpha() * fade))))
            top_item.setPen(pg.mkPen(width=1.0, color=self._wave_top_color))
            bottom_item.setPen(pg.mkPen(width=1.0, color=self._wave_bottom_color))
            top_item.setBrush(pg.mkBrush(fill))
            bottom_item.setBrush(pg.mkBrush(fill))

    def _set_waveform_multichannel(self, x: np.ndarray, amplitudes: np.ndarray) -> None:
        x_arr, amp_arr = self._align_wave_channels(x, amplitudes)
        if x_arr.size == 0 or amp_arr.size == 0:
            self._clear_waveform_plot()
            return

        edges = self._compute_wave_edges(x_arr)
        n = max(0, min(amp_arr.shape[0], max(0, edges.size - 1)))
        if n <= 0:
            self._clear_waveform_plot()
            return

        self.wave_top.setData([], [], connect="all")
        self.wave_bottom.setData([], [], connect="all")

        channel_count = amp_arr.shape[1]
        self._ensure_channel_wave_items(channel_count)
        edges = edges[: n + 1]
        amp_arr = amp_arr[:n]

        slot_height = 1.8 / max(1, channel_count)
        band_half = slot_height * 0.42
        for channel_index, (top_item, bottom_item) in enumerate(self._channel_wave_items):
            center = 0.9 - (channel_index + 0.5) * slot_height
            channel_amp = amp_arr[:, channel_index]
            top_values = center + (channel_amp * band_half)
            bottom_values = center - (channel_amp * band_half)
            self._safe_set_step_wave_item(top_item, edges, top_values, fill_level=center)
            self._safe_set_step_wave_item(bottom_item, edges, bottom_values, fill_level=center)

    def _set_waveform_from_channels(self, x: np.ndarray, amplitudes) -> None:
        x_arr, amp_arr = self._align_wave_channels(x, amplitudes)
        if x_arr.size == 0 or amp_arr.size == 0:
            self._clear_waveform_plot()
            return
        if self._waveform_view_mode == "channels":
            self._set_waveform_multichannel(x_arr, amp_arr)
            return

        combined = self._combine_channels_to_single(amp_arr)
        for top_item, bottom_item in self._channel_wave_items:
            top_item.setData([], [], connect="all")
            bottom_item.setData([], [], connect="all")
        self._set_waveform_amplitude(x_arr, combined)

    def _set_waveform_amplitude(self, x: np.ndarray, amplitude: np.ndarray) -> None:
        x_arr, amp_arr = self._align_wave_arrays(x, amplitude)
        if x_arr.size == 0 or amp_arr.size == 0:
            self._clear_waveform_plot()
            return

        edges = self._compute_wave_edges(x_arr)

        if edges.size != amp_arr.size + 1:
            n = max(0, min(edges.size - 1, amp_arr.size))
            edges = edges[: n + 1]
            amp_arr = amp_arr[:n]
            if n == 0:
                self._clear_waveform_plot()
                return

        self._safe_set_step_wave_item(self.wave_top, edges, amp_arr)
        self._safe_set_step_wave_item(self.wave_bottom, edges, -amp_arr)

    def _set_waveform_resolution(self, points: int, save: bool = True) -> None:
        points = max(1200, min(int(points), 24000))
        if points == self._waveform_points:
            if save:
                self._save_preferences()
            return

        self._waveform_points = points
        self.wave_cache.clear()
        self.wave_partial.clear()

        current_path = self._current_track_path()
        self._stop_active_wave_worker(wait_ms=120)
        self._stop_preload_worker(requeue=False, wait_ms=120)

        if current_path:
            self._load_waveform_for_track(current_path)
            self._enqueue_preload([t.path for i, t in enumerate(self.tracks) if i != (self.current_index or 0)])

        if save:
            self._save_preferences()

    def _set_waveform_view_mode(self, mode: str, save: bool = True) -> None:
        if mode not in {"combined", "channels"}:
            return
        if mode == self._waveform_view_mode:
            if save:
                self._save_preferences()
            return

        self._waveform_view_mode = mode
        current_path = self._current_track_path()
        if current_path:
            signature = ""
            try:
                signature = self._file_signature(current_path, self._waveform_points)
            except Exception:  # noqa: BLE001
                signature = ""

            cached = self._cache_get(current_path, signature)
            if cached:
                self._set_waveform_from_channels(cached[1], cached[2])
            else:
                if not self._render_partial_for_path(current_path, signature):
                    self._clear_waveform_plot()
        else:
            self._clear_waveform_plot()

        if save:
            self._save_preferences()

    def _fit_track_view(self) -> None:
        if self.duration_s <= 0:
            return
        self.current_window_s = max(self.min_window_s, self.duration_s)
        self._set_view(0.0, self.current_window_s)

    def _current_track_path(self) -> str:
        if self.current_index is None:
            return ""
        if self.current_index < 0 or self.current_index >= len(self.tracks):
            return ""
        return self.tracks[self.current_index].path

    def _render_partial_for_path(self, path: str, signature: str) -> bool:
        partial = self.wave_partial.get(path)
        if not partial or partial[0] != signature:
            return False

        x, amp = self._align_wave_channels(partial[1], partial[2])
        filled_bins = int(partial[3])
        total_bins = int(partial[4])
        if total_bins <= 0:
            total_bins = max(1, amp.shape[0])
        filled_bins = max(0, min(filled_bins, total_bins))
        upto = max(0, min(filled_bins, x.size, amp.shape[0]))
        if upto <= 0:
            self._clear_waveform_plot()
        else:
            self._set_waveform_from_channels(x[:upto], amp[:upto])
        pct = int(max(0, min(100, (filled_bins / max(1, total_bins)) * 100)))
        self.wave_load_label.setText(f"Waveform laden: {pct}%")
        return True

    def _load_waveform_for_track(self, path: str) -> None:
        try:
            signature = self._file_signature(path, self._waveform_points)
        except Exception:  # noqa: BLE001
            signature = ""

        self._remove_from_preload_queue(path)

        cached = self._cache_get(path, signature)
        if cached:
            self._set_waveform_from_channels(cached[1], cached[2])
            self.wave_load_label.setText("")
            self._start_next_preload()
            return

        if self._active_wave_path == path and self._active_wave_thread is not None:
            if self._render_partial_for_path(path, signature):
                return
            self._clear_waveform_plot()
            self.wave_load_label.setText("Waveform laden...")
            return

        if self._preload_path == path and self._preload_thread is not None:
            self._preload_thread.emit_progress = True
            if self._render_partial_for_path(path, signature):
                return
            self._clear_waveform_plot()
            self.wave_load_label.setText("Waveform laden...")
            return

        self._clear_waveform_plot()
        self.wave_load_label.setText("Waveform laden: 0%")

        if self._active_wave_thread is None:
            self._start_active_wave_worker(path, signature, emit_progress=True, points=self._waveform_points)
            return

        if self._preload_thread is None:
            self._start_preload_wave_worker(path, signature, emit_progress=True, points=self._waveform_points)
            return

        if path not in self._preload_set:
            self._preload_queue.insert(0, path)
            self._preload_set.add(path)
        self.wave_load_label.setText("Waveform in wachtrij...")

    def _start_active_wave_worker(self, path: str, signature: str, emit_progress: bool, points: int) -> None:
        self._active_wave_request_id += 1
        request_id = self._active_wave_request_id
        self._active_wave_path = path
        self._active_wave_signature = signature
        self._active_wave_failed = False

        thread = WaveformJob(
            request_id=request_id,
            path=path,
            points=points,
            emit_progress=emit_progress,
            progress_interval=0.12,
        )
        thread.progressReady.connect(self._on_active_wave_progress)
        thread.resultReady.connect(self._on_active_wave_finished)
        thread.errorRaised.connect(self._on_active_wave_failed)
        thread.finished.connect(lambda rid=request_id: self._on_active_wave_thread_finished(rid))

        self._active_wave_thread = thread
        thread.start()

    def _start_preload_wave_worker(self, path: str, signature: str, emit_progress: bool, points: int) -> None:
        self._preload_request_id += 1
        request_id = self._preload_request_id
        self._preload_path = path
        self._preload_signature = signature

        thread = WaveformJob(
            request_id=request_id,
            path=path,
            points=points,
            emit_progress=emit_progress,
            progress_interval=0.16,
        )
        thread.progressReady.connect(self._on_preload_progress)
        thread.resultReady.connect(self._on_preload_finished)
        thread.errorRaised.connect(self._on_preload_failed)
        thread.finished.connect(lambda rid=request_id: self._on_preload_wave_thread_finished(rid))

        self._preload_thread = thread
        thread.start()

    def _stop_active_wave_worker(self, wait_ms: int = 80) -> None:
        if self._active_wave_thread is not None:
            self._active_wave_thread.cancel()
            if not self._active_wave_thread.wait(wait_ms):
                self._active_wave_thread.wait(3000)

        self._active_wave_thread = None
        self._active_wave_path = ""
        self._active_wave_signature = ""
        self._active_wave_failed = False

    @Slot(int, str, object, object, int, int)
    def _on_active_wave_progress(
        self,
        request_id: int,
        path: str,
        x_obj,
        amp_obj,
        filled_bins: int,
        total_bins: int,
    ) -> None:
        if request_id != self._active_wave_request_id or path != self._active_wave_path:
            return

        x, amp = self._align_wave_channels(np.asarray(x_obj, dtype=np.float32), np.asarray(amp_obj, dtype=np.float32))
        total_bins = int(total_bins)
        if total_bins <= 0:
            total_bins = max(1, amp.shape[0])
        filled_bins = max(0, min(int(filled_bins), total_bins))
        self.wave_partial[path] = (self._active_wave_signature, x, amp, filled_bins, total_bins)

        if self._current_track_path() == path:
            if (
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and time.monotonic() < self._suppress_waveform_render_until
            ):
                return
            self._render_partial_for_path(path, self._active_wave_signature)

    @Slot(int, str, object, object)
    def _on_active_wave_finished(self, request_id: int, path: str, x_obj, amp_obj) -> None:
        if request_id != self._active_wave_request_id or path != self._active_wave_path:
            return

        self._active_wave_failed = False
        x, amplitudes = self._align_wave_channels(np.asarray(x_obj, dtype=np.float32), np.asarray(amp_obj, dtype=np.float32))

        self.wave_partial.pop(path, None)
        self._cache_store(path, self._active_wave_signature, x, amplitudes)

        if self._current_track_path() == path:
            self._set_waveform_from_channels(x, amplitudes)
            self.wave_load_label.setText("")

    @Slot(int, str, str)
    def _on_active_wave_failed(self, request_id: int, path: str, error_message: str) -> None:
        if request_id != self._active_wave_request_id or path != self._active_wave_path:
            return

        self._active_wave_failed = True
        self.wave_partial.pop(path, None)
        if self._current_track_path() == path:
            self._clear_waveform_plot()
            self.wave_load_label.setText("")
            QMessageBox.warning(self, "Waveform fout", error_message)

    def _on_active_wave_thread_finished(self, request_id: int) -> None:
        if request_id != self._active_wave_request_id:
            return

        finished_path = self._active_wave_path
        finished_signature = self._active_wave_signature
        ended_with_error = self._active_wave_failed
        has_final = bool(finished_path and self._cache_get(finished_path, finished_signature))

        self._active_wave_thread = None
        self._active_wave_path = ""
        self._active_wave_signature = ""
        self._active_wave_failed = False

        if (
            finished_path
            and not ended_with_error
            and not has_final
            and self._current_track_path() == finished_path
            and self._active_wave_thread is None
        ):
            self.wave_load_label.setText("Waveform laden: 0%")
            self._start_active_wave_worker(
                finished_path,
                finished_signature,
                emit_progress=True,
                points=self._waveform_points,
            )
            return

        self._start_next_preload()

    def _remove_from_preload_queue(self, path: str) -> None:
        if path in self._preload_set:
            self._preload_set.remove(path)
            self._preload_queue = [p for p in self._preload_queue if p != path]

    def _enqueue_preload(self, paths: list[str]) -> None:
        for path in paths:
            if path == self._active_wave_path or path == self._preload_path:
                continue
            if path in self._preload_set:
                continue

            try:
                signature = self._file_signature(path, self._waveform_points)
            except Exception:  # noqa: BLE001
                continue

            if self._cache_get(path, signature):
                continue

            self._preload_queue.append(path)
            self._preload_set.add(path)

        self._start_next_preload()

    def _start_next_preload(self) -> None:
        if self._preload_thread is not None:
            return

        while self._preload_queue:
            path = self._preload_queue.pop(0)
            self._preload_set.discard(path)

            if path == self._active_wave_path:
                continue

            try:
                signature = self._file_signature(path, self._waveform_points)
            except Exception:  # noqa: BLE001
                continue

            if self._cache_get(path, signature):
                continue

            current_path = self._current_track_path()
            is_current = path == current_path
            self._start_preload_wave_worker(
                path,
                signature,
                emit_progress=is_current,
                points=self._waveform_points,
            )
            return

    def _stop_preload_worker(self, requeue: bool, wait_ms: int = 80) -> None:
        path_to_requeue = self._preload_path if requeue else ""

        if self._preload_thread is not None:
            self._preload_thread.cancel()
            if not self._preload_thread.wait(wait_ms):
                self._preload_thread.wait(3000)

        self._preload_thread = None

        if requeue and path_to_requeue and path_to_requeue not in self._preload_set:
            self._preload_queue.insert(0, path_to_requeue)
            self._preload_set.add(path_to_requeue)

        self._preload_path = ""
        self._preload_signature = ""

    @Slot(int, str, object, object, int, int)
    def _on_preload_progress(
        self,
        request_id: int,
        path: str,
        x_obj,
        amp_obj,
        filled_bins: int,
        total_bins: int,
    ) -> None:
        if request_id != self._preload_request_id or path != self._preload_path:
            return

        x, amp = self._align_wave_channels(np.asarray(x_obj, dtype=np.float32), np.asarray(amp_obj, dtype=np.float32))
        total_bins = int(total_bins)
        if total_bins <= 0:
            total_bins = max(1, amp.shape[0])
        filled_bins = max(0, min(int(filled_bins), total_bins))
        self.wave_partial[path] = (self._preload_signature, x, amp, filled_bins, total_bins)

        if self._current_track_path() == path:
            if (
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and time.monotonic() < self._suppress_waveform_render_until
            ):
                return
            self._render_partial_for_path(path, self._preload_signature)

    @Slot(int, str, object, object)
    def _on_preload_finished(self, request_id: int, path: str, x_obj, amp_obj) -> None:
        if request_id != self._preload_request_id or path != self._preload_path:
            return

        x, amplitudes = self._align_wave_channels(np.asarray(x_obj, dtype=np.float32), np.asarray(amp_obj, dtype=np.float32))

        self.wave_partial.pop(path, None)
        self._cache_store(path, self._preload_signature, x, amplitudes)

        if self._current_track_path() == path:
            self._set_waveform_from_channels(x, amplitudes)
            self.wave_load_label.setText("")

    @Slot(int, str, str)
    def _on_preload_failed(self, request_id: int, path: str, _error_message: str) -> None:
        if request_id != self._preload_request_id or path != self._preload_path:
            return

        self.wave_partial.pop(path, None)
        if self._current_track_path() == path:
            self._clear_waveform_plot()
            self.wave_load_label.setText("")

    def _on_preload_wave_thread_finished(self, request_id: int) -> None:
        if request_id != self._preload_request_id:
            return

        self._preload_thread = None
        self._preload_path = ""
        self._preload_signature = ""
        self._start_next_preload()
