from __future__ import annotations

import time

from PySide6.QtMultimedia import QMediaPlayer

from audioplayer.constants import MIDI_ACTION_IDS

try:
    import mido  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    mido = None


class MidiController:
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
    def _midi_input_names(self) -> list[str]:
        if mido is None:
            return []
        try:
            names = list(mido.get_input_names())
        except Exception:  # noqa: BLE001
            return []
        cleaned = [str(name).strip() for name in names if str(name).strip()]
        return cleaned

    def _close_midi_input(self) -> None:
        if self._midi_input_port is None:
            return
        try:
            self._midi_input_port.close()
        except Exception:  # noqa: BLE001
            pass
        self._midi_input_port = None

    def _refresh_midi_input(self, update_status: bool = False) -> None:
        if mido is None:
            self._close_midi_input()
            return

        if not self._midi_enabled:
            self._close_midi_input()
            return

        input_names = self._midi_input_names()
        if not input_names:
            self._close_midi_input()
            if update_status and hasattr(self, "status"):
                self.status.setText(self._txt("Geen MIDI input gevonden", "No MIDI input found"))
            return

        target_name = self._midi_input_name if self._midi_input_name in input_names else input_names[0]
        if not self._midi_input_name or self._midi_input_name not in input_names:
            self._midi_input_name = target_name

        current_name = ""
        if self._midi_input_port is not None:
            current_name = str(getattr(self._midi_input_port, "name", "") or "")
        if self._midi_input_port is not None and current_name == target_name:
            return

        self._close_midi_input()
        try:
            self._midi_input_port = mido.open_input(target_name, callback=self._on_midi_message)
            if update_status and hasattr(self, "status"):
                self.status.setText(self._txt(f"MIDI actief: {target_name}", f"MIDI active: {target_name}"))
        except Exception as exc:  # noqa: BLE001
            self._midi_input_port = None
            if update_status and hasattr(self, "status"):
                self.status.setText(self._txt(f"MIDI fout: {exc}", f"MIDI error: {exc}"))

    def _on_midi_message(self, message) -> None:
        try:
            msg_type = str(getattr(message, "type", ""))
        except Exception:  # noqa: BLE001
            msg_type = ""
        if msg_type != "note_on":
            return
        try:
            velocity = int(getattr(message, "velocity", 0))
            note = int(getattr(message, "note", -1))
            msg_channel = int(getattr(message, "channel", -1))
        except Exception:  # noqa: BLE001
            return
        if velocity <= 0 or note < 0 or note > 127:
            return
        if self._midi_channel >= 0 and msg_channel != self._midi_channel:
            return
        try:
            self.midiNoteReceived.emit(note)
        except Exception:  # noqa: BLE001
            pass

    def _handle_midi_note_input(self, note: int) -> None:
        note_value = int(note)
        if note_value < 0 or note_value > 127:
            return

        now = time.monotonic()
        last_at = self._midi_last_note_at.get(note_value, 0.0)
        if now - last_at < 0.09:
            return
        self._midi_last_note_at[note_value] = now

        if self._midi_capture_callback is not None:
            try:
                consumed = bool(self._midi_capture_callback(note_value))
            except Exception:  # noqa: BLE001
                consumed = False
            if consumed:
                return

        if not self._midi_enabled:
            return

        for action_id in MIDI_ACTION_IDS:
            if int(self._midi_note_map.get(action_id, -1)) != note_value:
                continue
            self._trigger_midi_action(action_id)
            return

    def _trigger_midi_action(self, action_id: str) -> None:
        if action_id == "previous_track":
            self.previous_track()
            return
        if action_id == "toggle_play":
            self.toggle_play()
            return
        if action_id == "play":
            if self.current_index is None and self.tracks:
                self._autoplay_on_load = True
                self.playlist.setCurrentRow(0)
                return
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._start_playback_smooth(from_track_start=self.player.position() <= 250)
            return
        if action_id == "pause":
            self._play_when_ready = False
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            return
        if action_id == "next_track":
            self.next_track()
            return
        if action_id == "stop":
            self.stop()
            return
        if action_id == "repeat_mode":
            self._cycle_repeat_mode()
            return
        if action_id == "auto_next_toggle":
            self._set_auto_continue_enabled(not self._auto_continue_enabled, save=False)
            return
