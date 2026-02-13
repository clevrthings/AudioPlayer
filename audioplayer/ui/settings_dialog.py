from __future__ import annotations

try:
    import mido  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    mido = None

try:
    import rtmidi  # type: ignore[import-not-found]  # noqa: F401
except Exception:  # noqa: BLE001
    rtmidi = None  # type: ignore[assignment]

if mido is not None and rtmidi is not None:
    try:
        mido.set_backend("mido.backends.rtmidi")
    except Exception:  # noqa: BLE001
        pass

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from audioplayer.constants import (
    APP_VERSION,
    MIDI_ACTION_IDS,
)
from audioplayer.services.update_service import compare_versions, latest_release_info


def open_settings_dialog(host) -> None:
    self = host
    dialog = QDialog(self)
    dialog.setWindowTitle(self._txt("Voorkeuren", "Preferences"))
    dialog.setMinimumWidth(1120)
    dialog.setMinimumHeight(700)
    dialog.resize(1200, 760)

    layout = QVBoxLayout(dialog)
    tabs = QTabWidget()
    layout.addWidget(tabs)
    midi_applied_state: dict[str, object] = {
        "enabled": self._midi_enabled,
        "input_name": self._midi_input_name,
        "channel": self._midi_channel,
    }

    general_tab = QWidget()
    general_form = QFormLayout(general_tab)
    audio_tab = QWidget()
    audio_form = QFormLayout(audio_tab)
    midi_tab = QWidget()
    midi_form = QFormLayout(midi_tab)

    def _configure_settings_form(form: QFormLayout, *, top_labels: bool = False) -> None:
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFormAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(
            (Qt.AlignmentFlag.AlignTop if top_labels else Qt.AlignmentFlag.AlignVCenter)
            | Qt.AlignmentFlag.AlignRight
        )
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setContentsMargins(24, 20, 24, 20)

    _configure_settings_form(general_form)
    _configure_settings_form(audio_form)
    _configure_settings_form(midi_form, top_labels=True)

    def _set_expanding_field(widget: QWidget, min_width: int = 520) -> None:
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())
        widget.setMinimumWidth(min_width)

    def _set_compact_field(widget: QWidget, width: int = 460) -> None:
        widget.setSizePolicy(QSizePolicy.Policy.Preferred, widget.sizePolicy().verticalPolicy())
        widget.setMinimumWidth(max(260, width - 120))
        widget.setMaximumWidth(width)

    tabs.addTab(general_tab, self._txt("Algemeen", "General"))
    tabs.addTab(audio_tab, self._txt("Audio", "Audio"))
    tabs.addTab(midi_tab, self._txt("MIDI", "MIDI"))

    language_combo = QComboBox()
    language_combo.addItem("Nederlands", "nl")
    language_combo.addItem("English", "en")
    language_combo.setCurrentIndex(0 if self._language == "nl" else 1)
    _set_compact_field(language_combo, 460)
    general_form.addRow(self._txt("Taal", "Language"), language_combo)

    accent_button = QPushButton(self._accent_color.upper())
    accent_button.setToolTip(self._txt("Kies accent kleur", "Choose accent color"))
    _set_compact_field(accent_button, 460)
    accent_color = QColor(self._accent_color)

    def choose_accent_color() -> None:
        nonlocal accent_color
        picked = QColorDialog.getColor(accent_color, dialog, self._txt("Kies accent kleur", "Choose accent color"))
        if not picked.isValid():
            return
        accent_color = picked
        accent_button.setText(accent_color.name().upper())
        if not playhead_color:
            refresh_playhead_color_button()

    accent_button.clicked.connect(choose_accent_color)
    general_form.addRow(self._txt("Accent kleur", "Accent color"), accent_button)

    resolution_combo = QComboBox()
    resolution_items = [
        (self._txt("Laag (sneller)", "Low (faster)"), 2400),
        (self._txt("Normaal", "Normal"), 4200),
        (self._txt("Hoog", "High"), 8000),
        (self._txt("Ultra", "Ultra"), 12000),
    ]
    selected_resolution_index = 0
    for idx, (label, points) in enumerate(resolution_items):
        resolution_combo.addItem(label, points)
        if points == self._waveform_points:
            selected_resolution_index = idx
    resolution_combo.setCurrentIndex(selected_resolution_index)
    _set_compact_field(resolution_combo, 460)
    general_form.addRow(self._txt("Waveform resolutie", "Waveform resolution"), resolution_combo)

    waveform_view_combo = QComboBox()
    waveform_view_combo.addItem(self._txt("Standaard (gecombineerd)", "Default (combined)"), "combined")
    waveform_view_combo.addItem(self._txt("Per kanaal (gescheiden)", "Per channel (separate)"), "channels")
    waveform_view_combo.setCurrentIndex(0 if self._waveform_view_mode == "combined" else 1)
    _set_compact_field(waveform_view_combo, 460)
    general_form.addRow(self._txt("Waveform weergave", "Waveform view"), waveform_view_combo)

    playhead_color = self._playhead_color if QColor(self._playhead_color).isValid() else ""
    playhead_color_row = QWidget()
    playhead_color_layout = QHBoxLayout(playhead_color_row)
    playhead_color_layout.setContentsMargins(0, 0, 0, 0)
    playhead_color_layout.setSpacing(6)
    playhead_color_button = QPushButton()
    playhead_reset_button = QPushButton(self._txt("Standaard", "Default"))

    def preview_button_style(color: QColor) -> str:
        text_color = "#0f1115" if color.lightness() >= 145 else "#f3f3f3"
        border_color = color.darker(145).name()
        return (
            "QPushButton {"
            f"background: {color.name()};"
            f"color: {text_color};"
            f"border: 1px solid {border_color};"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            "}"
        )

    def refresh_playhead_color_button() -> None:
        if playhead_color and QColor(playhead_color).isValid():
            selected = QColor(playhead_color)
            playhead_color_button.setText(playhead_color.upper())
            playhead_color_button.setStyleSheet(preview_button_style(selected))
        else:
            fallback = QColor(accent_color)
            playhead_color_button.setText(self._txt(f"Accent {fallback.name().upper()}", f"Accent {fallback.name().upper()}"))
            playhead_color_button.setStyleSheet(preview_button_style(fallback))

    def choose_playhead_color() -> None:
        nonlocal playhead_color
        base = QColor(playhead_color) if playhead_color else QColor(accent_color)
        picked = QColorDialog.getColor(base, dialog, self._txt("Kies playhead kleur", "Choose playhead color"))
        if not picked.isValid():
            return
        playhead_color = picked.name()
        refresh_playhead_color_button()

    def reset_playhead_color() -> None:
        nonlocal playhead_color
        playhead_color = ""
        refresh_playhead_color_button()

    playhead_color_button.clicked.connect(choose_playhead_color)
    playhead_reset_button.clicked.connect(reset_playhead_color)
    refresh_playhead_color_button()
    playhead_color_layout.addWidget(playhead_color_button)
    playhead_color_layout.addWidget(playhead_reset_button)
    _set_compact_field(playhead_color_row, 460)
    general_form.addRow(self._txt("Playhead kleur", "Playhead color"), playhead_color_row)

    playhead_width_combo = QComboBox()
    width_options: list[tuple[str, float]] = [
        ("1.0 px", 1.0),
        ("1.5 px", 1.5),
        ("2.0 px", 2.0),
        ("2.5 px", 2.5),
        ("3.0 px", 3.0),
        ("4.0 px", 4.0),
    ]
    selected_width_index = 0
    for idx, (label, value) in enumerate(width_options):
        playhead_width_combo.addItem(label, value)
        if abs(value - self._playhead_width) < 0.01:
            selected_width_index = idx
    playhead_width_combo.setCurrentIndex(selected_width_index)
    _set_compact_field(playhead_width_combo, 460)
    general_form.addRow(self._txt("Playhead dikte", "Playhead thickness"), playhead_width_combo)

    output_device_combo = QComboBox()
    output_devices = self._audio_output_devices()
    default_device = QMediaDevices.defaultAudioOutput()
    default_name = default_device.description() or self._txt("Standaard output", "Default output")
    output_device_combo.addItem(
        self._txt(f"Systeem standaard ({default_name})", f"System default ({default_name})"),
        "",
    )
    selected_output_index = 0
    for device in output_devices:
        device_key = self._audio_device_key_for(device)
        channel_text = self._channel_layout_label(int(device.maximumChannelCount()))
        output_device_combo.addItem(f"{device.description()} ({channel_text})", device_key)
        if device_key and device_key == self._audio_output_device_key:
            selected_output_index = output_device_combo.count() - 1

    if self._audio_output_device_key and selected_output_index == 0:
        output_device_combo.addItem(
            self._txt("Opgeslagen output (niet beschikbaar)", "Saved output (not available)"),
            self._audio_output_device_key,
        )
        selected_output_index = output_device_combo.count() - 1

    output_device_combo.setCurrentIndex(selected_output_index)
    _set_compact_field(output_device_combo, 620)
    audio_form.addRow(self._txt("Output device", "Output device"), output_device_combo)

    audio_preview_label = QLabel("")
    audio_preview_label.setWordWrap(True)
    audio_preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
    audio_preview_label.setMinimumHeight(90)
    audio_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    audio_preview_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    audio_form.addRow(self._txt("Audio status", "Audio status"), audio_preview_label)

    def refresh_audio_preview() -> None:
        preferred_key = str(output_device_combo.currentData() or "")
        preferred, effective, switched, target = self._resolve_audio_device(
            preferred_key,
            "auto",
            matrix_enabled=False,
            matrix=None,
            devices=output_devices,
        )
        audio_preview_label.setText(
            self._audio_route_note(
                preferred,
                effective,
                switched,
                target,
                matrix_enabled=False,
            )
        )

    output_device_combo.currentIndexChanged.connect(refresh_audio_preview)
    refresh_audio_preview()

    midi_enabled_checkbox = QCheckBox(self._txt("MIDI input activeren", "Enable MIDI input"))
    midi_enabled_checkbox.setChecked(self._midi_enabled)
    midi_form.addRow("", midi_enabled_checkbox)

    midi_device_combo = QComboBox()
    midi_refresh_button = QPushButton(self._txt("Vernieuwen", "Refresh"))
    midi_device_row = QWidget()
    midi_device_row_layout = QHBoxLayout(midi_device_row)
    midi_device_row_layout.setContentsMargins(0, 0, 0, 0)
    midi_device_row_layout.setSpacing(6)
    midi_device_row_layout.addWidget(midi_device_combo, 1)
    midi_device_row_layout.addWidget(midi_refresh_button)
    _set_compact_field(midi_device_row, 620)
    midi_form.addRow(self._txt("MIDI input", "MIDI input"), midi_device_row)

    midi_channel_combo = QComboBox()
    midi_channel_combo.addItem(self._txt("Alle kanalen", "All channels"), -1)
    for channel_index in range(16):
        midi_channel_combo.addItem(
            self._txt(f"Kanaal {channel_index + 1}", f"Channel {channel_index + 1}"),
            channel_index,
        )
    channel_index = 0
    for idx in range(midi_channel_combo.count()):
        if int(midi_channel_combo.itemData(idx)) == self._midi_channel:
            channel_index = idx
            break
    midi_channel_combo.setCurrentIndex(channel_index)
    _set_compact_field(midi_channel_combo, 520)
    midi_form.addRow(self._txt("MIDI kanaal", "MIDI channel"), midi_channel_combo)

    midi_status_label = QLabel("")
    midi_status_label.setWordWrap(True)
    midi_status_label.setMinimumWidth(560)
    midi_status_label.setMinimumHeight(36)
    midi_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
    midi_form.addRow(self._txt("Status", "Status"), midi_status_label)

    midi_capture_label = QLabel("")
    midi_capture_label.setWordWrap(True)
    midi_capture_label.setMinimumWidth(560)
    midi_capture_label.setMinimumHeight(36)
    midi_capture_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
    midi_form.addRow(self._txt("Learn", "Learn"), midi_capture_label)

    midi_mapping_container = QWidget()
    midi_mapping_layout = QGridLayout(midi_mapping_container)
    midi_mapping_layout.setContentsMargins(0, 0, 0, 0)
    midi_mapping_layout.setHorizontalSpacing(6)
    midi_mapping_layout.setVerticalSpacing(6)
    midi_mapping_layout.addWidget(QLabel(self._txt("Control", "Control")), 0, 0)
    midi_mapping_layout.addWidget(QLabel(self._txt("MIDI noot", "MIDI note")), 0, 1)
    midi_mapping_layout.addWidget(QLabel(self._txt("Label", "Label")), 0, 2)
    midi_mapping_layout.addWidget(QLabel(self._txt("Learn", "Learn")), 0, 3)
    midi_mapping_layout.addWidget(QLabel(self._txt("Clear", "Clear")), 0, 4)

    midi_note_map_working = self._normalize_midi_note_map(self._midi_note_map)
    midi_note_spinners: dict[str, QSpinBox] = {}
    midi_note_labels: dict[str, QLabel] = {}
    midi_learn_buttons: dict[str, QPushButton] = {}
    pending_learn_action: dict[str, str] = {"id": ""}

    def refresh_midi_mapping_row(action_id: str) -> None:
        note_value = int(midi_note_map_working.get(action_id, -1))
        if action_id in midi_note_spinners:
            spinner = midi_note_spinners[action_id]
            spinner.blockSignals(True)
            spinner.setValue(note_value)
            spinner.blockSignals(False)
        if action_id in midi_note_labels:
            midi_note_labels[action_id].setText(self._midi_note_label(note_value))
        if action_id in midi_learn_buttons:
            midi_learn_buttons[action_id].setText(
                self._txt("Wachten...", "Listening...")
                if pending_learn_action["id"] == action_id
                else self._txt("Learn", "Learn")
            )

    def refresh_midi_mapping_rows() -> None:
        for action_id in MIDI_ACTION_IDS:
            refresh_midi_mapping_row(action_id)

    def on_midi_note_changed(action_id: str, value: int) -> None:
        midi_note_map_working[action_id] = int(value) if 0 <= int(value) <= 127 else -1
        if pending_learn_action["id"] == action_id:
            pending_learn_action["id"] = ""
        refresh_midi_mapping_row(action_id)

    def on_start_midi_learn(action_id: str) -> None:
        pending_learn_action["id"] = action_id
        midi_capture_label.setText(
            self._txt(
                f"Learn actief voor '{self._midi_action_label(action_id)}'. Speel nu een MIDI noot.",
                f"Learn active for '{self._midi_action_label(action_id)}'. Play a MIDI note now.",
            )
        )
        refresh_midi_mapping_rows()

    def on_clear_midi_mapping(action_id: str) -> None:
        if pending_learn_action["id"] == action_id:
            pending_learn_action["id"] = ""
        midi_note_map_working[action_id] = -1
        refresh_midi_mapping_row(action_id)
        midi_capture_label.setText(self._txt("Mapping gewist.", "Mapping cleared."))

    def on_reset_midi_defaults() -> None:
        pending_learn_action["id"] = ""
        defaults = self._default_midi_note_map()
        for action_id in MIDI_ACTION_IDS:
            midi_note_map_working[action_id] = int(defaults.get(action_id, -1))
        refresh_midi_mapping_rows()
        midi_capture_label.setText(
            self._txt(
                "MIDI mapping hersteld naar standaard.",
                "MIDI mapping reset to defaults.",
            )
        )

    for row_offset, action_id in enumerate(MIDI_ACTION_IDS, start=1):
        action_label = QLabel(self._midi_action_label(action_id))
        spinner = QSpinBox()
        spinner.setRange(-1, 127)
        spinner.setSpecialValueText(self._txt("Geen", "None"))
        spinner.setValue(int(midi_note_map_working.get(action_id, -1)))
        note_label = QLabel(self._midi_note_label(int(midi_note_map_working.get(action_id, -1))))
        learn_button = QPushButton(self._txt("Learn", "Learn"))
        clear_button = QPushButton(self._txt("Clear", "Clear"))

        spinner.valueChanged.connect(lambda value, action=action_id: on_midi_note_changed(action, value))
        learn_button.clicked.connect(lambda _checked=False, action=action_id: on_start_midi_learn(action))
        clear_button.clicked.connect(lambda _checked=False, action=action_id: on_clear_midi_mapping(action))

        midi_note_spinners[action_id] = spinner
        midi_note_labels[action_id] = note_label
        midi_learn_buttons[action_id] = learn_button

        midi_mapping_layout.addWidget(action_label, row_offset, 0)
        midi_mapping_layout.addWidget(spinner, row_offset, 1)
        midi_mapping_layout.addWidget(note_label, row_offset, 2)
        midi_mapping_layout.addWidget(learn_button, row_offset, 3)
        midi_mapping_layout.addWidget(clear_button, row_offset, 4)

    midi_mapping_scroll = QScrollArea()
    midi_mapping_scroll.setWidgetResizable(True)
    midi_mapping_scroll.setMinimumWidth(620)
    midi_mapping_scroll.setMinimumHeight(280)
    midi_mapping_scroll.setWidget(midi_mapping_container)
    midi_form.addRow(self._txt("Mapping", "Mapping"), midi_mapping_scroll)

    midi_reset_defaults_button = QPushButton(self._txt("Herstel MIDI defaults", "Reset MIDI defaults"))
    midi_reset_defaults_button.clicked.connect(on_reset_midi_defaults)
    midi_form.addRow("", midi_reset_defaults_button)

    def refresh_midi_devices() -> None:
        midi_device_combo.blockSignals(True)
        midi_device_combo.clear()
        if mido is None:
            midi_device_combo.addItem(self._txt("MIDI library ontbreekt", "MIDI library missing"), "")
        else:
            names = self._midi_input_names()
            if names:
                for name in names:
                    midi_device_combo.addItem(name, name)
            else:
                midi_device_combo.addItem(self._txt("Geen MIDI input gevonden", "No MIDI input found"), "")

        selected_name = self._midi_input_name
        if selected_name:
            for idx in range(midi_device_combo.count()):
                if str(midi_device_combo.itemData(idx) or "") == selected_name:
                    midi_device_combo.setCurrentIndex(idx)
                    break
        midi_device_combo.blockSignals(False)

    def refresh_midi_status() -> None:
        if mido is None:
            midi_status_label.setText(
                self._txt(
                    "MIDI niet beschikbaar. Installeer 'mido' en 'python-rtmidi'.",
                    "MIDI not available. Install 'mido' and 'python-rtmidi'.",
                )
            )
            return
        if not midi_enabled_checkbox.isChecked():
            midi_status_label.setText(self._txt("MIDI staat uit.", "MIDI is disabled."))
            return
        selected_name = str(midi_device_combo.currentData() or "").strip()
        if not selected_name:
            midi_status_label.setText(self._txt("Geen MIDI input geselecteerd.", "No MIDI input selected."))
            return
        selected_channel_data = midi_channel_combo.currentData()
        selected_channel = int(selected_channel_data) if selected_channel_data is not None else -1
        midi_status_label.setText(
            self._txt(
                f"Geselecteerde input: {selected_name}\nLuistert op: {self._midi_channel_label(selected_channel)}",
                f"Selected input: {selected_name}\nListening on: {self._midi_channel_label(selected_channel)}",
            )
        )

    def apply_midi_preview_from_controls() -> None:
        if mido is None:
            return
        self._midi_enabled = bool(midi_enabled_checkbox.isChecked())
        self._midi_input_name = str(midi_device_combo.currentData() or "").strip()
        selected_channel_data = midi_channel_combo.currentData()
        self._midi_channel = int(selected_channel_data) if selected_channel_data is not None else -1
        self._refresh_midi_input(update_status=False)

    def midi_capture_handler(note: int) -> bool:
        note_value = int(note)
        midi_capture_label.setText(
            self._txt(
                f"MIDI noot ontvangen: {self._midi_note_label(note_value)}",
                f"MIDI note received: {self._midi_note_label(note_value)}",
            )
        )
        action_id = pending_learn_action["id"]
        if action_id:
            midi_note_map_working[action_id] = note_value
            pending_learn_action["id"] = ""
            refresh_midi_mapping_rows()
        # Always consume while preferences are open, so mapped controls do not trigger accidentally.
        return True

    def on_midi_dialog_finished(_result: int) -> None:
        if self._midi_capture_callback is midi_capture_handler:
            self._midi_capture_callback = None
        if _result != int(QDialog.DialogCode.Accepted):
            self._midi_enabled = bool(midi_applied_state.get("enabled", False))
            self._midi_input_name = str(midi_applied_state.get("input_name", "") or "")
            self._midi_channel = int(midi_applied_state.get("channel", -1))
            self._refresh_midi_input(update_status=False)

    self._midi_capture_callback = midi_capture_handler
    dialog.finished.connect(on_midi_dialog_finished)

    refresh_midi_devices()
    refresh_midi_mapping_rows()
    refresh_midi_status()
    midi_capture_label.setText(
        self._txt(
            "Klik op Learn en speel een MIDI noot om te mappen.",
            "Click Learn and play a MIDI note to map it.",
        )
    )
    midi_refresh_button.clicked.connect(refresh_midi_devices)
    midi_refresh_button.clicked.connect(refresh_midi_status)
    midi_refresh_button.clicked.connect(apply_midi_preview_from_controls)
    midi_device_combo.currentIndexChanged.connect(refresh_midi_status)
    midi_device_combo.currentIndexChanged.connect(apply_midi_preview_from_controls)
    midi_channel_combo.currentIndexChanged.connect(refresh_midi_status)
    midi_channel_combo.currentIndexChanged.connect(apply_midi_preview_from_controls)
    midi_enabled_checkbox.toggled.connect(refresh_midi_status)
    midi_enabled_checkbox.toggled.connect(apply_midi_preview_from_controls)
    apply_midi_preview_from_controls()
    if mido is None:
        midi_enabled_checkbox.setEnabled(False)
        midi_device_combo.setEnabled(False)
        midi_channel_combo.setEnabled(False)
        midi_refresh_button.setEnabled(False)
        midi_mapping_scroll.setEnabled(False)
        midi_reset_defaults_button.setEnabled(False)

    defaults_section_gap = QWidget()
    defaults_section_gap.setFixedHeight(8)
    general_form.addRow("", defaults_section_gap)

    defaults_section_title = QLabel(self._txt("Standaardwaarden", "Defaults"))
    defaults_section_title.setStyleSheet("font-weight: 600;")
    general_form.addRow("", defaults_section_title)

    defaults_note = QLabel(
        self._txt("Deze instellingen gelden als opstart-standaard.", "These settings are startup defaults.")
    )
    defaults_note.setWordWrap(True)
    defaults_note.setMaximumWidth(520)
    general_form.addRow("", defaults_note)

    theme_combo = QComboBox()
    theme_combo.addItem(self._txt("Systeem", "System"), "system")
    theme_combo.addItem(self._txt("Donker", "Dark"), "dark")
    theme_combo.addItem(self._txt("Licht", "Light"), "light")
    theme_combo.setCurrentIndex(max(0, ("system", "dark", "light").index(self._default_theme_mode)))
    _set_compact_field(theme_combo, 460)
    general_form.addRow(self._txt("Default theme", "Default theme"), theme_combo)

    repeat_combo = QComboBox()
    repeat_combo.addItem(self._txt("Uit", "Off"), "off")
    repeat_combo.addItem(self._txt("Huidige track", "Current track"), "one")
    repeat_combo.addItem(self._txt("Hele playlist", "Whole playlist"), "all")
    repeat_combo.setCurrentIndex(max(0, ("off", "one", "all").index(self._default_repeat_mode)))
    _set_compact_field(repeat_combo, 460)
    general_form.addRow(self._txt("Default repeat", "Default repeat"), repeat_combo)

    auto_next_checkbox = QCheckBox(self._txt("Standaard auto volgende track", "Default auto next track"))
    auto_next_checkbox.setChecked(self._default_auto_continue_enabled)
    general_form.addRow("", auto_next_checkbox)

    autoplay_on_add_checkbox = QCheckBox(
        self._txt("Standaard starten bij toevoegen", "Default start when adding tracks")
    )
    autoplay_on_add_checkbox.setChecked(self._default_autoplay_on_add)
    general_form.addRow("", autoplay_on_add_checkbox)

    follow_checkbox = QCheckBox(self._txt("Standaard playhead volgen", "Default follow playhead"))
    follow_checkbox.setChecked(self._default_follow_playhead)
    general_form.addRow("", follow_checkbox)

    tools_section_gap = QWidget()
    tools_section_gap.setFixedHeight(8)
    general_form.addRow("", tools_section_gap)

    tools_title = QLabel(self._txt("Updates en Feedback", "Updates and Feedback"))
    tools_title.setStyleSheet("font-weight: 600;")
    general_form.addRow("", tools_title)

    update_status_label = QLabel(self._txt(f"Huidige versie: {APP_VERSION}", f"Current version: {APP_VERSION}"))
    update_status_label.setWordWrap(True)
    update_status_label.setMaximumWidth(620)
    general_form.addRow("", update_status_label)

    update_buttons_row = QWidget()
    update_buttons_layout = QHBoxLayout(update_buttons_row)
    update_buttons_layout.setContentsMargins(0, 0, 0, 0)
    update_buttons_layout.setSpacing(8)
    check_updates_button = QPushButton(self._txt("Controleer op updates", "Check for updates"))
    download_update_button = QPushButton(self._txt("Download update", "Download update"))
    report_button = QPushButton(self._txt("Probleem melden / Feature aanvragen", "Report issue / Request feature"))
    download_update_button.setVisible(False)
    download_update_button.setEnabled(False)
    update_buttons_layout.addWidget(check_updates_button)
    update_buttons_layout.addWidget(download_update_button)
    update_buttons_layout.addWidget(report_button)
    update_buttons_layout.addStretch(1)
    _set_compact_field(update_buttons_row, 620)
    general_form.addRow("", update_buttons_row)

    update_target: dict[str, str] = {"url": ""}

    def on_check_updates() -> None:
        check_updates_button.setEnabled(False)
        update_status_label.setText(self._txt("Updates controleren...", "Checking for updates..."))
        download_update_button.setVisible(False)
        download_update_button.setEnabled(False)
        update_target["url"] = ""
        try:
            latest_version, download_url = latest_release_info()
            if not latest_version:
                update_status_label.setText(
                    self._txt("Kon geen releaseversie lezen.", "Could not read latest release version.")
                )
            else:
                comparison = compare_versions(APP_VERSION, latest_version)
                if comparison < 0:
                    update_target["url"] = download_url
                    update_status_label.setText(
                        self._txt(
                            f"Update beschikbaar: {latest_version} (huidig: {APP_VERSION})",
                            f"Update available: {latest_version} (current: {APP_VERSION})",
                        )
                    )
                    download_update_button.setVisible(True)
                    download_update_button.setEnabled(True)
                elif comparison == 0:
                    update_status_label.setText(
                        self._txt(
                            f"Je gebruikt de nieuwste release ({APP_VERSION}).",
                            f"You are using the latest release ({APP_VERSION}).",
                        )
                    )
                else:
                    update_status_label.setText(
                        self._txt(
                            f"Je gebruikt een nieuwere ontwikkelversie ({APP_VERSION}) dan de nieuwste release ({latest_version}).",
                            f"You are running a newer development version ({APP_VERSION}) than the latest release ({latest_version}).",
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            update_status_label.setText(
                self._txt(
                    f"Updatecheck mislukt: {exc}",
                    f"Update check failed: {exc}",
                )
            )
        finally:
            check_updates_button.setEnabled(True)

    def on_download_update() -> None:
        target_url = update_target.get("url", "").strip()
        if not target_url:
            return
        if not QDesktopServices.openUrl(QUrl(target_url)):
            QMessageBox.warning(
                dialog,
                self._txt("Download openen mislukt", "Failed to open download"),
                target_url,
            )

    check_updates_button.clicked.connect(on_check_updates)
    download_update_button.clicked.connect(on_download_update)
    report_button.clicked.connect(self.open_feedback_dialog)

    button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    apply_button = button_box.addButton(self._txt("Toepassen", "Apply"), QDialogButtonBox.ButtonRole.ApplyRole)
    layout.addWidget(button_box)

    def apply_settings(close_dialog: bool) -> None:
        self._language = str(language_combo.currentData())
        self._accent_color = accent_color.name()
        self._playhead_color = playhead_color if playhead_color and QColor(playhead_color).isValid() else ""
        self._playhead_width = max(1.0, min(float(playhead_width_combo.currentData()), 6.0))
        self._default_theme_mode = str(theme_combo.currentData())
        self._default_repeat_mode = str(repeat_combo.currentData())
        self._default_auto_continue_enabled = auto_next_checkbox.isChecked()
        self._default_autoplay_on_add = autoplay_on_add_checkbox.isChecked()
        self._default_follow_playhead = follow_checkbox.isChecked()
        self._autoplay_on_add = self._default_autoplay_on_add
        self._audio_output_device_key = str(output_device_combo.currentData() or "").strip().lower()
        self._midi_enabled = bool(midi_enabled_checkbox.isChecked()) and mido is not None
        self._midi_input_name = str(midi_device_combo.currentData() or "").strip()
        selected_channel_data = midi_channel_combo.currentData()
        self._midi_channel = int(selected_channel_data) if selected_channel_data is not None else -1
        self._midi_note_map = self._normalize_midi_note_map(midi_note_map_working)
        self._set_waveform_resolution(int(resolution_combo.currentData()), save=False)
        self._set_waveform_view_mode(str(waveform_view_combo.currentData()), save=False)
        self._apply_audio_preferences(update_status=False)
        self._refresh_midi_input(update_status=False)
        midi_applied_state["enabled"] = self._midi_enabled
        midi_applied_state["input_name"] = self._midi_input_name
        midi_applied_state["channel"] = self._midi_channel
        self._save_preferences()
        self._apply_language()
        self._apply_effective_theme()
        if close_dialog:
            dialog.accept()

    def on_accept() -> None:
        apply_settings(close_dialog=True)

    def on_apply() -> None:
        apply_settings(close_dialog=False)

    button_box.accepted.connect(on_accept)
    button_box.rejected.connect(dialog.reject)
    apply_button.clicked.connect(on_apply)

    dialog.exec()
