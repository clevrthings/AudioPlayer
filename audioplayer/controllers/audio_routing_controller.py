from __future__ import annotations

import numpy as np
from PySide6.QtMultimedia import QAudioDevice, QMediaDevices


class AudioRoutingController:
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

    def _routing_requires_processing(self) -> bool:
        return False

    def _effective_output_channels(self, source_channels: int) -> int:
        return max(1, int(source_channels))

    def _build_runtime_routing_matrix(self, source_channels: int, output_channels: int) -> np.ndarray:
        channels = max(1, min(int(source_channels), int(output_channels)))
        return np.eye(channels, dtype=np.float32)

    def _resolve_playback_source(self, source_path: str) -> str:
        return source_path

    def _cleanup_stale_routed_files(self, max_age_s: int) -> None:
        _ = max_age_s
        routed_dir = getattr(self, "_routed_audio_dir", None)
        if not routed_dir:
            return
        try:
            for candidate in routed_dir.glob("*.wav"):
                try:
                    candidate.unlink()
                except Exception:  # noqa: BLE001
                    pass
            for candidate in routed_dir.glob("*.tmp.wav"):
                try:
                    candidate.unlink()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        try:
            self._routed_audio_cache.clear()
            self._session_routed_files.clear()
        except Exception:  # noqa: BLE001
            pass

    def _trim_routed_audio_cache(self, max_entries: int) -> None:
        _ = max_entries
        try:
            self._routed_audio_cache.clear()
        except Exception:  # noqa: BLE001
            pass

    def _cleanup_session_routed_files(self) -> None:
        try:
            self._session_routed_files.clear()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _audio_device_key_for(device: QAudioDevice) -> str:
        try:
            raw = bytes(device.id())
        except Exception:  # noqa: BLE001
            return ""
        if not raw:
            return ""
        return raw.hex()

    @staticmethod
    def _channel_layout_label(channels: int) -> str:
        count = max(1, int(channels))
        if count == 1:
            return "Mono (1.0)"
        if count == 2:
            return "Stereo (2.0)"
        if count == 6:
            return "Surround (5.1)"
        if count == 8:
            return "Surround (7.1)"
        if count == 12:
            return "Immersive (7.1.4)"
        return f"{count} ch"

    def _routing_target_channels(
        self,
        mode: str,
        matrix_enabled: bool | None = None,
        matrix: list[list[int]] | None = None,
    ) -> int:
        _ = (mode, matrix_enabled, matrix)
        return 0

    def _routing_mode_label(self, mode: str) -> str:
        _ = mode
        return self._txt("Automatisch (bron-layout)", "Automatic (source layout)")

    def _audio_output_devices(self) -> list[QAudioDevice]:
        try:
            return list(QMediaDevices.audioOutputs())
        except Exception:  # noqa: BLE001
            return []

    def _resolve_audio_device(
        self,
        preferred_key: str,
        routing_mode: str,
        matrix_enabled: bool | None = None,
        matrix: list[list[int]] | None = None,
        devices: list[QAudioDevice] | None = None,
    ) -> tuple[QAudioDevice, QAudioDevice, bool, int]:
        _ = (routing_mode, matrix_enabled, matrix)
        outputs = devices if devices is not None else self._audio_output_devices()
        default_device = QMediaDevices.defaultAudioOutput()

        preferred = default_device
        if preferred_key:
            for device in outputs:
                if self._audio_device_key_for(device) == preferred_key:
                    preferred = device
                    break

        return preferred, preferred, False, 0

    def _audio_route_note(
        self,
        preferred: QAudioDevice,
        effective: QAudioDevice,
        switched_for_routing: bool,
        target_channels: int,
        matrix_enabled: bool | None = None,
    ) -> str:
        _ = (switched_for_routing, target_channels, matrix_enabled)
        preferred_name = preferred.description() or self._txt("Standaard apparaat", "Default device")
        effective_name = effective.description() or preferred_name
        effective_max = max(1, int(effective.maximumChannelCount()))
        effective_layout = self._channel_layout_label(effective_max)
        first_line = self._txt(f"Voorkeur output: {preferred_name}", f"Preferred output: {preferred_name}")
        second_line = self._txt(
            f"Actieve output: {effective_name} ({effective_layout})",
            f"Active output: {effective_name} ({effective_layout})",
        )
        support_line = self._txt(
            "Ondersteunt multichannel bestanden t/m 7.1.4 indien output/device dit toelaat.",
            "Supports multichannel files up to 7.1.4 when output/device allows it.",
        )
        return "\n".join((first_line, second_line, support_line))

    def _apply_audio_preferences(self, update_status: bool, refresh_source: bool = True) -> None:
        _ = refresh_source
        outputs = self._audio_output_devices()
        preferred, effective, switched_for_routing, target_channels = self._resolve_audio_device(
            self._audio_output_device_key,
            "auto",
            matrix_enabled=False,
            matrix=None,
            devices=outputs,
        )
        self.audio_output.setDevice(effective)
        self._effective_audio_route_note = self._audio_route_note(
            preferred,
            effective,
            switched_for_routing,
            target_channels,
            matrix_enabled=False,
        )
        self.status.setToolTip(self._effective_audio_route_note)
        if update_status:
            self.status.setText(self._txt("Audio output bijgewerkt", "Audio output updated"))

    def _refresh_current_playback_source(self) -> None:
        return
