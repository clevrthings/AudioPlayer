from __future__ import annotations

import os
from pathlib import Path

import soundfile as sf
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from audioplayer.constants import AUDIO_EXTENSIONS
from audioplayer.models import Track


class PlaylistController:
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

    def _remove_selected_track_with_shortcut(self) -> None:
        if not self.playlist.hasFocus():
            return
        self.remove_selected_track()

    def remove_selected_track(self) -> None:
        row = self.playlist.currentRow()
        if row < 0 or row >= len(self.tracks):
            return

        removed_track = self.tracks[row]
        was_current = self.current_index == row
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

        self._remove_from_preload_queue(removed_track.path)
        self.wave_partial.pop(removed_track.path, None)
        if removed_track.path == self._active_wave_path:
            self._stop_active_wave_worker(wait_ms=80)
        if removed_track.path == self._preload_path:
            self._stop_preload_worker(requeue=False, wait_ms=80)

        self.tracks.pop(row)
        self.playlist.blockSignals(True)
        item = self.playlist.takeItem(row)
        del item
        self.playlist.blockSignals(False)

        if not self.tracks:
            self.current_index = None
            self.duration_s = 0.0
            self.sample_rate = 0
            self.player.stop()
            self.player.setSource(QUrl())
            self._clear_track_ui()
            return

        if was_current:
            next_row = min(row, len(self.tracks) - 1)
            self._autoplay_on_load = was_playing
            self.playlist.setCurrentRow(next_row)
            return

        if self.current_index is not None and row < self.current_index:
            self.current_index -= 1
            self.playlist.setCurrentRow(self.current_index)

    def _track_duration(self, path: str) -> float:
        cached = self._duration_cache.get(path)
        if cached is not None:
            return cached

        try:
            info = sf.info(path)
            duration = float(info.frames) / float(info.samplerate) if info.samplerate else 0.0
        except Exception:  # noqa: BLE001
            duration = 0.0
        self._duration_cache[path] = duration
        return duration

    def _rebuild_playlist_items(self, selected_path: str) -> None:
        self.playlist.blockSignals(True)
        self.playlist.clear()
        selected_row = -1
        for idx, track in enumerate(self.tracks):
            item = QListWidgetItem(track.name)
            item.setToolTip(track.path)
            item.setData(Qt.ItemDataRole.UserRole, track.path)
            self.playlist.addItem(item)
            if selected_row < 0 and track.path == selected_path:
                selected_row = idx
        self.playlist.blockSignals(False)

        if selected_row < 0 and self.tracks:
            selected_row = 0
        if selected_row >= 0:
            self.playlist.setCurrentRow(selected_row)

    def sort_playlist_by_name(self) -> None:
        if not self.tracks:
            return
        selected_path = self._current_track_path()
        self.tracks.sort(key=lambda track: track.name.lower())
        self._rebuild_playlist_items(selected_path)

    def sort_playlist_by_time_asc(self) -> None:
        if not self.tracks:
            return
        selected_path = self._current_track_path()
        self.tracks.sort(key=lambda track: self._track_duration(track.path))
        self._rebuild_playlist_items(selected_path)

    def sort_playlist_by_time_desc(self) -> None:
        if not self.tracks:
            return
        selected_path = self._current_track_path()
        self.tracks.sort(key=lambda track: self._track_duration(track.path), reverse=True)
        self._rebuild_playlist_items(selected_path)

    def _sync_tracks_from_playlist(self) -> None:
        if self.playlist.count() != len(self.tracks):
            return

        path_to_tracks: dict[str, list[Track]] = {}
        for track in self.tracks:
            path_to_tracks.setdefault(track.path, []).append(track)

        reordered_tracks: list[Track] = []
        for i in range(self.playlist.count()):
            item = self.playlist.item(i)
            path_data = item.data(Qt.ItemDataRole.UserRole)
            path = str(path_data) if path_data else item.toolTip()
            bucket = path_to_tracks.get(path, [])
            if bucket:
                track = bucket.pop(0)
            else:
                track = Track(path=path, name=item.text())
            item.setToolTip(track.path)
            item.setData(Qt.ItemDataRole.UserRole, track.path)
            item.setText(track.name)
            reordered_tracks.append(track)

        self.tracks = reordered_tracks
        row = self.playlist.currentRow()
        self.current_index = row if 0 <= row < len(self.tracks) else None

    @staticmethod
    def _is_supported_audio_path(path: str) -> bool:
        return Path(path).suffix.lower() in AUDIO_EXTENSIONS

    @staticmethod
    def _extract_local_paths_from_mime(mime_data) -> list[str]:
        if not mime_data.hasUrls():
            return []
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]

    def _normalize_input_paths(self, paths: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for raw in paths:
            if not raw:
                continue
            path = os.path.abspath(os.path.expanduser(raw))
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for name in files:
                        candidate = os.path.join(root, name)
                        if not self._is_supported_audio_path(candidate):
                            continue
                        if candidate in seen:
                            continue
                        seen.add(candidate)
                        normalized.append(candidate)
                continue

            if not os.path.isfile(path):
                continue
            if not self._is_supported_audio_path(path):
                continue
            if path in seen:
                continue

            seen.add(path)
            normalized.append(path)

        return normalized

    def add_files(self, paths: list[str], select_first_new: bool) -> int:
        new_paths = self._normalize_input_paths(paths)
        if not new_paths:
            return 0

        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        first_new_row: int | None = None
        for path in new_paths:
            track = Track(path=path, name=Path(path).name)
            self.tracks.append(track)
            item = QListWidgetItem(track.name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.playlist.addItem(item)
            if first_new_row is None:
                first_new_row = self.playlist.count() - 1

        should_activate_first_new = False
        if first_new_row is not None:
            if self._autoplay_on_add:
                should_activate_first_new = True
            elif was_playing:
                should_activate_first_new = False
            elif select_first_new:
                should_activate_first_new = True
            elif self.current_index is None and self.tracks:
                should_activate_first_new = True

        if should_activate_first_new and first_new_row is not None:
            if self._autoplay_on_add:
                self._autoplay_on_load = True
            self.playlist.setCurrentRow(first_new_row)
        elif self.current_index is None and self.tracks:
            self.playlist.setCurrentRow(0)

        self._enqueue_preload(new_paths)
        return len(new_paths)

    def open_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self.host,
            "Kies audio files",
            "",
            "Audio (*.wav *.flac *.ogg *.aiff *.aif *.mp3 *.m4a *.aac *.wma);;Alle bestanden (*)",
        )
        if files:
            self.add_files(files, select_first_new=self.current_index is None)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._extract_local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._extract_local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        dropped_paths = self._extract_local_paths_from_mime(event.mimeData())
        added = self.add_files(dropped_paths, select_first_new=True)
        if added > 0:
            event.acceptProposedAction()
            return
        event.ignore()

    def load_track(self, row: int) -> None:
        if row < 0 or row >= len(self.tracks):
            return

        self.current_index = row
        path = self.tracks[row].path

        try:
            info = sf.info(path)
            self.sample_rate = int(info.samplerate)
            frames = int(info.frames)
            channels = int(info.channels)
            fmt = f"{info.format}/{info.subtype}"
            self.duration_s = frames / float(self.sample_rate) if self.sample_rate else 0.0
            self._duration_cache[path] = self.duration_s

            self.player.stop()
            playback_source = self._resolve_playback_source(path)
            self.player.setSource(QUrl.fromLocalFile(playback_source))
            self.play_button.setText("Play")
            self.playhead.setBounds((0, max(0.001, self.duration_s)))
            self._set_playhead_pos(0.0)
            self._last_ui_ms = 0

            self.plot.setLimits(
                xMin=0,
                xMax=max(self.duration_s, self.min_window_s),
                minXRange=self.min_window_s,
                maxXRange=max(self.duration_s, self.min_window_s),
                yMin=-1.1,
                yMax=1.1,
            )
            self._fit_track_view()

            size_mb = os.path.getsize(path) / (1024 * 1024)
            self._set_info_value(self.lbl_file, Path(path).name)
            self._set_info_value(self.lbl_format, fmt)
            self._set_info_value(self.lbl_duration, self.format_time(self.duration_s))
            self._set_info_value(self.lbl_sr, f"{self.sample_rate} Hz")
            self._set_info_value(self.lbl_channels, f"{channels} ({self._channel_layout_label(channels)})")
            self._set_info_value(self.lbl_size, f"{size_mb:.2f} MB")
            self.status.setText(f"{self.format_time(0)} / {self.format_time(self.duration_s)}")

            self._load_waveform_for_track(path)
            self._enqueue_preload([t.path for i, t in enumerate(self.tracks) if i != row])

            if self._autoplay_on_load:
                self._start_playback_smooth(from_track_start=True)
                self._autoplay_on_load = False
        except Exception as exc:  # noqa: BLE001
            self._play_when_ready = False
            self._suppress_waveform_render_until = 0.0
            self._autoplay_on_load = False
            QMessageBox.critical(
                self.host,
                "Kon bestand niet laden",
                f"Bestand kon niet geladen worden:\n{path}\n\nFout: {exc}",
            )
