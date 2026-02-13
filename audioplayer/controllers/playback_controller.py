from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QStyle


class PlaybackController:
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

    def _on_audio_outputs_changed(self) -> None:
        self._apply_audio_preferences(update_status=False)

    def _start_playback_smooth(self, from_track_start: bool = False) -> None:
        # Avoid heavy waveform redraw bursts right at the first playback moments.
        if from_track_start or self.player.position() <= 250:
            self._suppress_waveform_render_until = time.monotonic() + 0.8
        else:
            self._suppress_waveform_render_until = 0.0

        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadingMedia:
            self._play_when_ready = True
            return

        self._play_when_ready = False
        self.player.play()

    def toggle_play(self) -> None:
        if self.current_index is None and self.tracks:
            self._autoplay_on_load = True
            self.playlist.setCurrentRow(0)
            return

        if self._play_when_ready:
            self._play_when_ready = False
            return

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._play_when_ready = False
            self.player.pause()
            return

        self._start_playback_smooth(from_track_start=self.player.position() <= 250)

    def on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText(self._txt("Pauze", "Pause"))
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_button.setText("Play")
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._play_when_ready and status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        }:
            self._play_when_ready = False
            self.player.play()
            return

        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._play_when_ready = False
            return

        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        self._handle_track_end()

    def _handle_track_end(self) -> None:
        if self.current_index is None or not self.tracks:
            return

        if self._repeat_mode == "one":
            self.player.setPosition(0)
            self._last_ui_ms = 0
            self._set_playhead_pos(0.0)
            self.player.play()
            return

        last_index = len(self.tracks) - 1
        has_next = self.current_index < last_index

        if has_next and (self._auto_continue_enabled or self._repeat_mode == "all"):
            self._autoplay_on_load = True
            self.playlist.setCurrentRow(self.current_index + 1)
            return

        if not has_next and self._repeat_mode == "all":
            self._autoplay_on_load = True
            self.playlist.setCurrentRow(0)

    def on_position_changed(self, position_ms: int) -> None:
        if self.duration_s <= 0:
            return

        duration_ms = int(self.duration_s * 1000)
        is_edge = position_ms <= 0 or position_ms >= duration_ms
        if not is_edge and abs(position_ms - self._last_ui_ms) < 40:
            return
        self._last_ui_ms = position_ms

        pos_s = max(0.0, min(position_ms / 1000.0, self.duration_s))

        if abs(float(self.playhead.value()) - pos_s) > 0.01:
            self._set_playhead_pos(pos_s)

        self.status.setText(f"{self.format_time(pos_s)} / {self.format_time(self.duration_s)}")

        if self._follow_playhead and self.current_window_s < self.duration_s:
            x0, x1 = self.plot.viewRange()[0]
            if pos_s < x0 or pos_s > x1:
                self._set_view(max(0.0, pos_s - self.current_window_s * 0.2), self.current_window_s)

    def stop(self) -> None:
        self._play_when_ready = False
        self._suppress_waveform_render_until = 0.0
        self.player.stop()
        self.player.setPosition(0)
        self._last_ui_ms = 0
        self._set_playhead_pos(0.0)
        self.status.setText(f"{self.format_time(0)} / {self.format_time(self.duration_s)}")

    def previous_track(self) -> None:
        if not self.tracks:
            return
        row = self.playlist.currentRow()
        if row <= 0:
            row = 0
        else:
            row -= 1
        self._autoplay_on_load = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.playlist.setCurrentRow(row)

    def next_track(self) -> None:
        if not self.tracks:
            return
        row = self.playlist.currentRow()
        if row < 0:
            row = 0
        else:
            row = min(len(self.tracks) - 1, row + 1)
        self._autoplay_on_load = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.playlist.setCurrentRow(row)

    def zoom_in(self) -> None:
        self._zoom(1 / 1.6)

    def zoom_out(self) -> None:
        self._zoom(1.6)

    def _zoom(self, factor: float) -> None:
        if self.duration_s <= 0:
            return

        x0, x1 = self.plot.viewRange()[0]
        current_window = max(self.min_window_s, x1 - x0)
        center = float(self.playhead.value())

        new_window = current_window * factor
        new_window = max(self.min_window_s, min(new_window, self.duration_s))
        new_start = center - (new_window / 2.0)
        self._set_view(new_start, new_window)

    def _set_view(self, start_s: float, window_s: float) -> None:
        if self.duration_s <= 0:
            return

        window_s = max(self.min_window_s, min(window_s, self.duration_s))
        max_start = max(0.0, self.duration_s - window_s)
        start_s = max(0.0, min(start_s, max_start))
        end_s = start_s + window_s

        self.current_window_s = window_s
        self._adjusting_view = True
        self.plot.setXRange(start_s, end_s, padding=0)
        self._adjusting_view = False

    def _set_playhead_pos(self, pos_s: float) -> None:
        self._updating_playhead = True
        self.playhead.setPos(pos_s)
        self._updating_playhead = False

    def _on_plot_click(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or self.duration_s <= 0:
            return

        mouse_point = self.plot.plotItem.vb.mapSceneToView(ev.scenePos())
        new_pos_s = max(0.0, min(mouse_point.x(), self.duration_s))

        self.player.setPosition(int(new_pos_s * 1000))
        self._last_ui_ms = int(new_pos_s * 1000)
        self._set_playhead_pos(new_pos_s)
        self.status.setText(f"{self.format_time(new_pos_s)} / {self.format_time(self.duration_s)}")

    def _on_playhead_seek_finished(self) -> None:
        if self.duration_s <= 0 or self._updating_playhead:
            return

        pos_s = max(0.0, min(float(self.playhead.value()), self.duration_s))
        self.player.setPosition(int(pos_s * 1000))
        self._last_ui_ms = int(pos_s * 1000)
        self.status.setText(f"{self.format_time(pos_s)} / {self.format_time(self.duration_s)}")

    def _on_xrange_changed(self, _, ranges) -> None:
        if self._adjusting_view or self.duration_s <= 0:
            return

        x0, x1 = ranges
        window = x1 - x0
        if window <= 0:
            return

        window = max(self.min_window_s, min(window, self.duration_s))
        clamped_start = max(0.0, min(x0, self.duration_s - window))
        clamped_end = clamped_start + window

        self.current_window_s = window

        if abs(clamped_start - x0) > 1e-6 or abs(clamped_end - x1) > 1e-6:
            self._adjusting_view = True
            self.plot.setXRange(clamped_start, clamped_end, padding=0)
            self._adjusting_view = False
