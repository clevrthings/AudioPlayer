from __future__ import annotations

import json
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPen,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audioplayer.constants import (
    APP_VERSION,
    FEEDBACK_WORKER_DEFAULT_URL,
    FEEDBACK_WORKER_ENV_KEY,
    FEEDBACK_WORKER_ENV_URL,
    MIDI_ACTION_IDS,
    ROUTING_CHANNEL_LABELS,
)
from audioplayer.controllers import AudioRoutingController, MidiController, PlaybackController, PlaylistController, WaveformController
from audioplayer.models import Track
from audioplayer.services.feedback_service import post_feedback_issue
from audioplayer.ui.settings_dialog import open_settings_dialog as open_settings_dialog_view
from audioplayer.ui.theme import (
    build_auto_next_icon,
    build_dark_style,
    build_follow_icon,
    build_light_style,
    build_moon_icon,
    build_repeat_mode_icon,
    build_sun_icon,
    make_playhead_pen,
    qss_rgba,
    resolve_playhead_color,
    system_prefers_dark,
)
from audioplayer.waveform import TimeAxisItem, WaveformJob
from audioplayer.widgets import PlaylistWidget


class WaveformPlayer(QMainWindow):
    midiNoteReceived = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Audio Player")
        self.resize(1260, 420)
        self.setMinimumSize(900, 280)
        self.setAcceptDrops(True)

        self.audio_output = QAudioOutput()
        self._media_devices = QMediaDevices()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)

        self.tracks: list[Track] = []
        self.current_index: int | None = None
        self.duration_s: float = 0.0
        self.sample_rate: int = 0
        self.current_window_s: float = 0.0
        self.min_window_s: float = 0.5
        self._autoplay_on_load = False
        self._updating_playhead = False
        self._adjusting_view = False
        self._play_when_ready = False
        self._suppress_waveform_render_until = 0.0
        self._settings = QSettings("RicoVanderhallen", "AudioPlayer")
        self._language = "nl"
        self._accent_color = "#7ED298"
        self._effective_accent_color = ""
        self._default_theme_mode = "system"
        self._theme_mode = "system"
        self._effective_theme = ""
        self._applying_theme = False
        self._last_ui_ms = -10_000
        self._default_repeat_mode = "off"
        self._repeat_mode = "off"
        self._default_auto_continue_enabled = True
        self._auto_continue_enabled = True
        self._default_autoplay_on_add = False
        self._autoplay_on_add = False
        self._default_follow_playhead = True
        self._follow_playhead = True
        self._playhead_color = ""
        self._playhead_width = 2.0
        self._effective_playhead_color = ""
        self._effective_playhead_width = 0.0
        self._waveform_points = 4200
        self._waveform_view_mode = "combined"
        self._audio_output_device_key = ""
        self._effective_audio_route_note = ""
        self._midi_enabled = False
        self._midi_input_name = ""
        self._midi_channel = -1
        self._midi_note_map = {action_id: -1 for action_id in MIDI_ACTION_IDS}
        self._midi_input_port = None
        self._midi_capture_callback: Callable[[int], bool] | None = None
        self._midi_last_note_at: dict[int, float] = {}
        self._feedback_worker_url = os.getenv(FEEDBACK_WORKER_ENV_URL, "").strip() or FEEDBACK_WORKER_DEFAULT_URL
        self._feedback_worker_key = os.getenv(FEEDBACK_WORKER_ENV_KEY, "").strip()
        self._wave_top_color = QColor("#72cfff")
        self._wave_bottom_color = QColor("#49a9de")
        self._wave_fill_color = QColor(93, 183, 234, 110)

        self._active_wave_request_id = 0
        self._active_wave_thread: WaveformJob | None = None
        self._active_wave_path = ""
        self._active_wave_signature = ""
        self._active_wave_failed = False

        self._preload_request_id = 0
        self._preload_thread: WaveformJob | None = None
        self._preload_path = ""
        self._preload_signature = ""
        self._preload_queue: list[str] = []
        self._preload_set: set[str] = set()

        self.wave_cache: dict[str, tuple[str, np.ndarray, np.ndarray]] = {}
        self.wave_partial: dict[str, tuple[str, np.ndarray, np.ndarray, int, int]] = {}
        self._duration_cache: dict[str, float] = {}
        self._channel_wave_items: list[tuple[pg.PlotDataItem, pg.PlotDataItem]] = []
        self._routed_audio_cache: dict[str, str] = {}
        self._session_routed_files: set[str] = set()
        self._routed_audio_dir = Path(tempfile.gettempdir()) / "AudioPlayer" / "routed"
        self._routed_audio_dir.mkdir(parents=True, exist_ok=True)
        self.waveform_controller = WaveformController(self)
        self.playback_controller = PlaybackController(self)
        self.playlist_controller = PlaylistController(self)
        self.audio_routing_controller = AudioRoutingController(self)
        self.midi_controller = MidiController(self)
        self._cleanup_stale_routed_files(max_age_s=18 * 3600)

        self.sun_icon = self._build_sun_icon()
        self.moon_icon = self._build_moon_icon()

        self._load_preferences()
        self._build_ui()
        self._connect_signals()
        self._apply_language()
        self.set_theme_mode(self._theme_mode)
        self._refresh_midi_input(update_status=False)

        self._system_theme_timer = QTimer(self)
        self._system_theme_timer.setInterval(1200)
        self._system_theme_timer.timeout.connect(self._refresh_system_theme)
        self._system_theme_timer.start()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        left_panel = QWidget()
        left_panel.setObjectName("ToolbarSection")
        left_panel.setFixedWidth(420)
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.open_button = QPushButton("Open audio")
        left_layout.addWidget(self.open_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Verwijder geselecteerde track uit playlist")
        left_layout.addWidget(self.remove_button)

        self.sort_button = QToolButton()
        self.sort_button.setObjectName("ToolbarButton")
        self.sort_button.setText("Rangschik")
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_menu = QMenu(self)
        self.sort_by_name_action = QAction("Naam (A-Z)", self)
        self.sort_by_duration_action = QAction("Tijd (kort -> lang)", self)
        self.sort_by_duration_desc_action = QAction("Tijd (lang -> kort)", self)
        self.sort_menu.addAction(self.sort_by_name_action)
        self.sort_menu.addAction(self.sort_by_duration_action)
        self.sort_menu.addAction(self.sort_by_duration_desc_action)
        self.sort_button.setMenu(self.sort_menu)
        left_layout.addWidget(self.sort_button)
        left_layout.addStretch(1)

        center_panel = QWidget()
        center_panel.setObjectName("ToolbarSection")
        center_layout = QHBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(6)

        self.prev_button = QPushButton("Prev")
        self.prev_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        center_layout.addWidget(self.prev_button)

        self.play_button = QPushButton("Play")
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        center_layout.addWidget(self.play_button)

        self.next_button = QPushButton("Next")
        self.next_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        center_layout.addWidget(self.next_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        center_layout.addWidget(self.stop_button)
        center_layout.addSpacing(12)

        self.repeat_button = QPushButton()
        self.repeat_button.setObjectName("IconControl")
        self.repeat_button.setFixedWidth(36)
        self.repeat_button.setToolTip("Wissel repeat modus: uit, één track, hele playlist")
        center_layout.addWidget(self.repeat_button)

        self.auto_next_button = QPushButton()
        self.auto_next_button.setObjectName("IconControl")
        self.auto_next_button.setFixedWidth(36)
        self.auto_next_button.setCheckable(True)
        self.auto_next_button.setToolTip("Ga automatisch naar de volgende track bij einde")
        center_layout.addWidget(self.auto_next_button)

        self.follow_button = QPushButton()
        self.follow_button.setObjectName("IconControl")
        self.follow_button.setFixedWidth(36)
        self.follow_button.setCheckable(True)
        self.follow_button.setToolTip("Houd playhead in beeld tijdens afspelen")
        center_layout.addWidget(self.follow_button)

        right_panel = QWidget()
        right_panel.setObjectName("ToolbarSection")
        right_panel.setFixedWidth(220)
        right_layout = QHBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch(1)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_button.setToolTip("Thema")
        right_layout.addWidget(self.theme_button)

        self.theme_menu = QMenu(self)
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)

        self.theme_actions: dict[str, QAction] = {}
        self.theme_actions["system"] = QAction("Systeem", self, checkable=True)
        self.theme_actions["dark"] = QAction("Donker", self, checkable=True)
        self.theme_actions["light"] = QAction("Licht", self, checkable=True)

        for mode in ("system", "dark", "light"):
            action = self.theme_actions[mode]
            action.triggered.connect(lambda checked, m=mode: self.set_theme_mode(m))
            self.theme_group.addAction(action)
            self.theme_menu.addAction(action)
        self.theme_button.setMenu(self.theme_menu)

        toolbar_content = QWidget()
        toolbar_content.setObjectName("ToolbarContent")
        toolbar_content_layout = QHBoxLayout(toolbar_content)
        toolbar_content_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_content_layout.setSpacing(0)
        toolbar_content_layout.addWidget(left_panel)
        toolbar_content_layout.addStretch(1)
        toolbar_content_layout.addWidget(center_panel)
        toolbar_content_layout.addStretch(1)
        toolbar_content_layout.addWidget(right_panel)
        toolbar.addWidget(toolbar_content)

        main = QWidget()
        self.setCentralWidget(main)
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        main_layout.addWidget(splitter)

        self.playlist = PlaylistWidget()
        self.playlist.setMinimumWidth(220)
        self.playlist.setMaximumWidth(330)
        self.playlist.setDragEnabled(True)
        self.playlist.setAcceptDrops(True)
        self.playlist.setDropIndicatorShown(True)
        self.playlist.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.playlist.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        splitter.addWidget(self.playlist)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setSpacing(8)
        right_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        wave_info = QWidget()
        wave_info_layout = QHBoxLayout(wave_info)
        wave_info_layout.setContentsMargins(0, 0, 0, 0)
        wave_info_layout.setSpacing(8)
        right_layout.addWidget(wave_info, stretch=1)

        wave_panel = QWidget()
        wave_panel_layout = QVBoxLayout(wave_panel)
        wave_panel_layout.setContentsMargins(0, 0, 0, 0)
        wave_panel_layout.setSpacing(6)
        wave_info_layout.addWidget(wave_panel, stretch=1)

        self.plot = pg.PlotWidget(axisItems={"bottom": TimeAxisItem(orientation="bottom")})
        self.plot.showGrid(x=True, y=False, alpha=0.12)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setLabel("bottom", "Tijd (mm:ss)")
        self.plot.hideAxis("left")
        self.plot.plotItem.hideButtons()
        self.plot.setYRange(-1.02, 1.02, padding=0)
        self.plot.setLimits(yMin=-1.1, yMax=1.1)
        wave_panel_layout.addWidget(self.plot, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        self.wave_load_label = QLabel("")
        bottom_row.addWidget(self.wave_load_label)
        bottom_row.addStretch(1)

        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Toon de volledige track (vervangt de pyqtgraph 'A' knop)")
        self.fit_button.setFixedWidth(44)
        bottom_row.addWidget(self.fit_button)

        self.zoom_out_button = QPushButton("-")
        self.zoom_out_button.setFixedWidth(34)
        bottom_row.addWidget(self.zoom_out_button)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(34)
        bottom_row.addWidget(self.zoom_in_button)
        wave_panel_layout.addLayout(bottom_row)

        info = QFrame()
        info.setObjectName("InfoCard")
        info.setFixedWidth(292)
        info_layout = QFormLayout(info)
        info_layout.setContentsMargins(10, 10, 10, 10)
        info_layout.setLabelAlignment(Qt.AlignmentFlag.AlignTop)
        info_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        info_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        info_layout.setHorizontalSpacing(8)
        wave_info_layout.addWidget(info)

        self.lbl_file = self._new_info_value()
        self.lbl_format = self._new_info_value()
        self.lbl_duration = self._new_info_value()
        self.lbl_sr = self._new_info_value()
        self.lbl_channels = self._new_info_value()
        self.lbl_size = self._new_info_value()

        self._add_info_row(info_layout, "Bestand", self.lbl_file)
        self._add_info_row(info_layout, "Formaat", self.lbl_format)
        self._add_info_row(info_layout, "Duur", self.lbl_duration)
        self._add_info_row(info_layout, "Sample rate", self.lbl_sr)
        self._add_info_row(info_layout, "Channels", self.lbl_channels)
        self._add_info_row(info_layout, "Bestandsgrootte", self.lbl_size)

        self.status = QLabel("00:00.000 / 00:00.000")
        right_layout.addWidget(self.status)

        self.wave_top = self.plot.plot(
            [],
            [],
            pen=pg.mkPen(width=1.1, color="#72cfff"),
            fillLevel=0,
            brush=pg.mkBrush(93, 183, 234, 110),
            stepMode="center",
        )
        self.wave_bottom = self.plot.plot(
            [],
            [],
            pen=pg.mkPen(width=1.1, color="#49a9de"),
            fillLevel=0,
            brush=pg.mkBrush(93, 183, 234, 110),
            stepMode="center",
        )
        # Keep clipping disabled for stepMode-center plots.
        # pyqtgraph currently clips x/y equally, which breaks the required len(x)=len(y)+1.
        self.wave_top.setClipToView(False)
        self.wave_bottom.setClipToView(False)

        initial_playhead_pen = QPen(QColor("#80d6ff"))
        initial_playhead_pen.setWidthF(2.0)
        initial_playhead_pen.setCosmetic(True)
        self.playhead = pg.InfiniteLine(
            pos=0,
            angle=90,
            movable=True,
            pen=initial_playhead_pen,
        )
        self.playhead.setBounds((0, 1))
        self.plot.addItem(self.playhead)
        self._apply_playhead_pen(QColor("#80d6ff"), 2.0)
        self._update_repeat_button_text()
        self._set_auto_continue_enabled(self._auto_continue_enabled, save=False)
        self._set_follow_playhead_enabled(self._follow_playhead, save=False)
        self._build_menus()

    def _connect_signals(self) -> None:
        self.open_button.clicked.connect(self.open_files)
        self.remove_button.clicked.connect(self.remove_selected_track)
        self.sort_by_name_action.triggered.connect(self.sort_playlist_by_name)
        self.sort_by_duration_action.triggered.connect(self.sort_playlist_by_time_asc)
        self.sort_by_duration_desc_action.triggered.connect(self.sort_playlist_by_time_desc)
        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button.clicked.connect(self.stop)
        self.prev_button.clicked.connect(self.previous_track)
        self.next_button.clicked.connect(self.next_track)
        self.repeat_button.clicked.connect(self._cycle_repeat_mode)
        self.auto_next_button.toggled.connect(self._set_auto_continue_enabled)
        self.follow_button.toggled.connect(self._set_follow_playhead_enabled)
        self.fit_button.clicked.connect(self._fit_track_view)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button.clicked.connect(self.zoom_out)

        self.player.playbackStateChanged.connect(self.on_playback_state)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self.playlist.currentRowChanged.connect(self.load_track)
        self.playlist.reordered.connect(self._sync_tracks_from_playlist)

        self.plot.scene().sigMouseClicked.connect(self._on_plot_click)
        self.playhead.sigPositionChangeFinished.connect(self._on_playhead_seek_finished)
        self.plot.plotItem.vb.sigXRangeChanged.connect(self._on_xrange_changed)

        self.space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.space_shortcut.activated.connect(self.toggle_play)

        self.backspace_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        self.backspace_shortcut.activated.connect(self._remove_selected_track_with_shortcut)

        self.left_arrow_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.left_arrow_shortcut.activated.connect(self.previous_track)

        self.right_arrow_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.right_arrow_shortcut.activated.connect(self.next_track)

        self.enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self.enter_shortcut.activated.connect(self.stop)

        self.keypad_enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        self.keypad_enter_shortcut.activated.connect(self.stop)

        # MIDI callbacks can arrive on a non-Qt thread; dispatch safely to UI thread.
        self.midiNoteReceived.connect(self._handle_midi_note_input)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._close_midi_input()
        self._stop_active_wave_worker(wait_ms=1500)
        self._stop_preload_worker(requeue=False, wait_ms=1500)
        self._cleanup_session_routed_files()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        watched = {QEvent.Type.PaletteChange, QEvent.Type.StyleChange}
        app_palette_change = getattr(QEvent.Type, "ApplicationPaletteChange", None)
        if app_palette_change is not None:
            watched.add(app_palette_change)
        theme_change = getattr(QEvent.Type, "ThemeChange", None)
        if theme_change is not None:
            watched.add(theme_change)

        if event.type() in watched:
            self._refresh_system_theme()

    @staticmethod
    def _to_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _default_routing_matrix() -> list[list[int]]:
        size = len(ROUTING_CHANNEL_LABELS)
        matrix = [[0 for _ in range(size)] for _ in range(size)]
        for idx in range(size):
            matrix[idx][idx] = 1
        return matrix

    @staticmethod
    def _clone_routing_matrix(matrix: list[list[int]]) -> list[list[int]]:
        size = len(ROUTING_CHANNEL_LABELS)
        out = [[0 for _ in range(size)] for _ in range(size)]
        for row_idx in range(min(size, len(matrix))):
            row = matrix[row_idx]
            for col_idx in range(min(size, len(row))):
                out[row_idx][col_idx] = 1 if int(row[col_idx]) else 0
        return out

    @staticmethod
    def _serialize_routing_matrix(matrix: list[list[int]]) -> str:
        safe = WaveformPlayer._clone_routing_matrix(matrix)
        rows: list[str] = []
        for row in safe:
            rows.append("".join("1" if int(cell) else "0" for cell in row))
        return "|".join(rows)

    @staticmethod
    def _parse_routing_matrix(raw: str) -> list[list[int]]:
        if not raw:
            return WaveformPlayer._default_routing_matrix()

        size = len(ROUTING_CHANNEL_LABELS)
        rows = [segment for segment in raw.split("|") if segment]
        matrix = [[0 for _ in range(size)] for _ in range(size)]
        for row_idx in range(min(size, len(rows))):
            row_text = rows[row_idx]
            for col_idx in range(min(size, len(row_text))):
                matrix[row_idx][col_idx] = 1 if row_text[col_idx] == "1" else 0
        return matrix

    @staticmethod
    def _default_midi_note_map() -> dict[str, int]:
        return {
            "previous_track": 1,
            "toggle_play": 6,
            "play": -1,
            "pause": 3,
            "next_track": 2,
            "stop": 4,
            "repeat_mode": -1,
            "auto_next_toggle": -1,
        }

    @staticmethod
    def _normalize_midi_note_map(raw_map) -> dict[str, int]:
        normalized = WaveformPlayer._default_midi_note_map()
        if not isinstance(raw_map, dict):
            return normalized
        for action_id in MIDI_ACTION_IDS:
            raw_value = raw_map.get(action_id, -1)
            try:
                note_value = int(raw_value)
            except Exception:  # noqa: BLE001
                note_value = -1
            normalized[action_id] = note_value if 0 <= note_value <= 127 else -1
        return normalized

    @staticmethod
    def _midi_note_label(note: int) -> str:
        value = int(note)
        if value < 0 or value > 127:
            return "-"
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        octave = (value // 12) - 1
        return f"{names[value % 12]}{octave} ({value})"

    def _midi_action_label(self, action_id: str) -> str:
        labels = {
            "previous_track": self._txt("Vorige track", "Previous track"),
            "toggle_play": self._txt("Play / Pauze", "Play / Pause"),
            "play": self._txt("Play", "Play"),
            "pause": self._txt("Pauze", "Pause"),
            "next_track": self._txt("Volgende track", "Next track"),
            "stop": self._txt("Stop", "Stop"),
            "repeat_mode": self._txt("Repeat modus", "Repeat mode"),
            "auto_next_toggle": self._txt("Auto next toggle", "Auto next toggle"),
        }
        return labels.get(action_id, action_id)

    def _midi_channel_label(self, channel_value: int) -> str:
        if int(channel_value) < 0:
            return self._txt("Alle kanalen", "All channels")
        return self._txt(f"Kanaal {int(channel_value) + 1}", f"Channel {int(channel_value) + 1}")

    def _midi_input_names(self) -> list[str]:
        return self.midi_controller._midi_input_names()

    def _close_midi_input(self) -> None:
        return self.midi_controller._close_midi_input()

    def _refresh_midi_input(self, update_status: bool = False) -> None:
        return self.midi_controller._refresh_midi_input(update_status=update_status)

    def _on_midi_message(self, message) -> None:
        return self.midi_controller._on_midi_message(message)

    def _handle_midi_note_input(self, note: int) -> None:
        return self.midi_controller._handle_midi_note_input(note)

    def _trigger_midi_action(self, action_id: str) -> None:
        return self.midi_controller._trigger_midi_action(action_id)

    @staticmethod
    def _routing_matrix_target_channels(matrix: list[list[int]]) -> int:
        size = len(ROUTING_CHANNEL_LABELS)
        highest = -1
        for row in range(min(size, len(matrix))):
            current = matrix[row]
            for col in range(min(size, len(current))):
                if int(current[col]):
                    highest = max(highest, col)
        return highest + 1

    @staticmethod
    def _routing_matrix_preset(target_channels: int) -> list[list[int]]:
        size = len(ROUTING_CHANNEL_LABELS)
        matrix = [[0 for _ in range(size)] for _ in range(size)]
        if target_channels <= 0:
            return WaveformPlayer._default_routing_matrix()
        target = max(1, min(size, int(target_channels)))
        if target == 2:
            # Simple stereo fold-down routing.
            left_sources = {0, 2, 4, 6, 8, 10}
            right_sources = {1, 2, 5, 7, 9, 11}
            for src in left_sources:
                matrix[src][0] = 1
            for src in right_sources:
                matrix[src][1] = 1
            matrix[3][0] = 1
            matrix[3][1] = 1
            return matrix
        for idx in range(size):
            matrix[idx][min(idx, target - 1)] = 1
        return matrix

    @staticmethod
    def _is_identity_routing(matrix: np.ndarray) -> bool:
        if matrix.ndim != 2 or matrix.size == 0:
            return False
        rows, cols = matrix.shape
        if rows != cols:
            return False
        eye = np.eye(rows, dtype=np.float32)
        return np.array_equal(np.asarray(matrix, dtype=np.float32), eye)

    def _routing_requires_processing(self) -> bool:
        return self.audio_routing_controller._routing_requires_processing()

    def _effective_output_channels(self, source_channels: int) -> int:
        return self.audio_routing_controller._effective_output_channels(source_channels)

    def _build_runtime_routing_matrix(self, source_channels: int, output_channels: int) -> np.ndarray:
        return self.audio_routing_controller._build_runtime_routing_matrix(source_channels, output_channels)

    def _resolve_playback_source(self, source_path: str) -> str:
        return self.audio_routing_controller._resolve_playback_source(source_path)

    def _cleanup_stale_routed_files(self, max_age_s: int) -> None:
        return self.audio_routing_controller._cleanup_stale_routed_files(max_age_s)

    def _trim_routed_audio_cache(self, max_entries: int) -> None:
        return self.audio_routing_controller._trim_routed_audio_cache(max_entries)

    def _cleanup_session_routed_files(self) -> None:
        return self.audio_routing_controller._cleanup_session_routed_files()

    
    def _audio_device_key_for(self, device: QAudioDevice) -> str:
        return self.audio_routing_controller._audio_device_key_for(device)

    
    def _channel_layout_label(self, channels: int) -> str:
        return self.audio_routing_controller._channel_layout_label(channels)

    def _routing_target_channels(
        self,
        mode: str,
        matrix_enabled: bool | None = None,
        matrix: list[list[int]] | None = None,
    ) -> int:
        return self.audio_routing_controller._routing_target_channels(mode, matrix_enabled=matrix_enabled, matrix=matrix)

    def _routing_mode_label(self, mode: str) -> str:
        return self.audio_routing_controller._routing_mode_label(mode)

    def _audio_output_devices(self) -> list[QAudioDevice]:
        return self.audio_routing_controller._audio_output_devices()

    def _resolve_audio_device(
        self,
        preferred_key: str,
        routing_mode: str,
        matrix_enabled: bool | None = None,
        matrix: list[list[int]] | None = None,
        devices: list[QAudioDevice] | None = None,
    ) -> tuple[QAudioDevice, QAudioDevice, bool, int]:
        return self.audio_routing_controller._resolve_audio_device(
            preferred_key,
            routing_mode,
            matrix_enabled=matrix_enabled,
            matrix=matrix,
            devices=devices,
        )

    def _audio_route_note(
        self,
        preferred: QAudioDevice,
        effective: QAudioDevice,
        switched_for_routing: bool,
        target_channels: int,
        matrix_enabled: bool | None = None,
    ) -> str:
        return self.audio_routing_controller._audio_route_note(
            preferred,
            effective,
            switched_for_routing,
            target_channels,
            matrix_enabled=matrix_enabled,
        )

    def _apply_audio_preferences(self, update_status: bool, refresh_source: bool = True) -> None:
        return self.audio_routing_controller._apply_audio_preferences(update_status, refresh_source=refresh_source)

    def _refresh_current_playback_source(self) -> None:
        return self.audio_routing_controller._refresh_current_playback_source()

    def _load_preferences(self) -> None:
        language = str(self._settings.value("language", self._language))
        if language in {"nl", "en"}:
            self._language = language

        accent = str(self._settings.value("accent_color", self._accent_color))
        if QColor(accent).isValid():
            self._accent_color = accent

        theme = str(self._settings.value("default_theme", self._default_theme_mode))
        if theme in {"system", "dark", "light"}:
            self._default_theme_mode = theme

        repeat = str(self._settings.value("default_repeat", self._default_repeat_mode))
        if repeat in {"off", "one", "all"}:
            self._default_repeat_mode = repeat

        self._default_auto_continue_enabled = self._to_bool(
            self._settings.value("default_auto_continue", self._default_auto_continue_enabled),
            self._default_auto_continue_enabled,
        )
        self._default_autoplay_on_add = self._to_bool(
            self._settings.value("default_autoplay_on_add", self._default_autoplay_on_add),
            self._default_autoplay_on_add,
        )
        self._default_follow_playhead = self._to_bool(
            self._settings.value(
                "default_follow_playhead",
                self._settings.value("follow_playhead", self._default_follow_playhead),
            ),
            self._default_follow_playhead,
        )

        playhead_color = str(self._settings.value("playhead_color", self._playhead_color)).strip()
        self._playhead_color = playhead_color if playhead_color and QColor(playhead_color).isValid() else ""
        try:
            width_value = float(self._settings.value("playhead_width", self._playhead_width))
            self._playhead_width = max(1.0, min(width_value, 6.0))
        except Exception:  # noqa: BLE001
            self._playhead_width = 2.0

        try:
            points_value = int(self._settings.value("waveform_points", self._waveform_points))
            self._waveform_points = max(1200, min(points_value, 24000))
        except Exception:  # noqa: BLE001
            self._waveform_points = 4200
        waveform_view_mode = str(self._settings.value("waveform_view_mode", self._waveform_view_mode))
        if waveform_view_mode in {"combined", "channels"}:
            self._waveform_view_mode = waveform_view_mode

        audio_output_device = str(self._settings.value("audio_output_device", self._audio_output_device_key)).strip().lower()
        if len(audio_output_device) % 2 == 0 and all(ch in "0123456789abcdef" for ch in audio_output_device):
            self._audio_output_device_key = audio_output_device
        else:
            self._audio_output_device_key = ""

        self._midi_enabled = self._to_bool(
            self._settings.value("midi_enabled", self._midi_enabled),
            self._midi_enabled,
        )
        self._midi_input_name = str(self._settings.value("midi_input_name", self._midi_input_name)).strip()
        try:
            midi_channel_value = int(self._settings.value("midi_channel", self._midi_channel))
        except Exception:  # noqa: BLE001
            midi_channel_value = -1
        self._midi_channel = midi_channel_value if -1 <= midi_channel_value <= 15 else -1
        midi_map_raw = str(self._settings.value("midi_note_map", ""))
        midi_map_value: dict[str, int] = self._default_midi_note_map()
        if midi_map_raw:
            try:
                parsed_map = json.loads(midi_map_raw)
            except Exception:  # noqa: BLE001
                parsed_map = {}
            midi_map_value = self._normalize_midi_note_map(parsed_map)
        self._midi_note_map = midi_map_value

        self._theme_mode = self._default_theme_mode
        self._repeat_mode = self._default_repeat_mode
        self._auto_continue_enabled = self._default_auto_continue_enabled
        self._autoplay_on_add = self._default_autoplay_on_add
        self._follow_playhead = self._default_follow_playhead

    def _save_preferences(self) -> None:
        self._settings.setValue("language", self._language)
        self._settings.setValue("accent_color", self._accent_color)
        self._settings.setValue("default_theme", self._default_theme_mode)
        self._settings.setValue("default_repeat", self._default_repeat_mode)
        self._settings.setValue("default_auto_continue", self._default_auto_continue_enabled)
        self._settings.setValue("default_autoplay_on_add", self._default_autoplay_on_add)
        self._settings.setValue("default_follow_playhead", self._default_follow_playhead)
        self._settings.setValue("playhead_color", self._playhead_color)
        self._settings.setValue("playhead_width", self._playhead_width)
        self._settings.setValue("waveform_points", self._waveform_points)
        self._settings.setValue("waveform_view_mode", self._waveform_view_mode)
        self._settings.setValue("audio_output_device", self._audio_output_device_key)
        self._settings.setValue("midi_enabled", self._midi_enabled)
        self._settings.setValue("midi_input_name", self._midi_input_name)
        self._settings.setValue("midi_channel", self._midi_channel)
        self._settings.setValue("midi_note_map", json.dumps(self._normalize_midi_note_map(self._midi_note_map), sort_keys=True))

    def _txt(self, nl_text: str, en_text: str) -> str:
        return en_text if self._language == "en" else nl_text

    def _refresh_system_theme(self) -> None:
        if self._theme_mode != "system" or self._applying_theme:
            return
        effective = "dark" if self._system_prefers_dark() else "light"
        if effective != self._effective_theme:
            self._apply_effective_theme()

    def _new_info_value(self) -> QLabel:
        lbl = QLabel("-")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        return lbl

    def _add_info_row(self, info_layout: QFormLayout, title: str, value_label: QLabel) -> None:
        key_label = QLabel(title)
        key_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        key_label.setMinimumWidth(96)
        key_label.setMaximumWidth(96)
        key_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        info_layout.addRow(key_label, value_label)

    def _set_info_value(self, label: QLabel, value: str) -> None:
        wrapped = textwrap.fill(value, width=26, break_long_words=True, break_on_hyphens=True)
        label.setText(wrapped)
        label.setToolTip(value)

    def _clear_track_ui(self) -> None:
        self._clear_waveform_plot()
        self.wave_load_label.setText("")
        self.status.setText("00:00.000 / 00:00.000")
        self.playhead.setBounds((0, 1))
        self._set_playhead_pos(0.0)
        self._set_info_value(self.lbl_file, "-")
        self._set_info_value(self.lbl_format, "-")
        self._set_info_value(self.lbl_duration, "-")
        self._set_info_value(self.lbl_sr, "-")
        self._set_info_value(self.lbl_channels, "-")
        self._set_info_value(self.lbl_size, "-")

    def _clear_waveform_plot(self) -> None:
        self.wave_top.setData([], [], connect="all")
        self.wave_bottom.setData([], [], connect="all")
        for wave_top_item, wave_bottom_item in self._channel_wave_items:
            wave_top_item.setData([], [], connect="all")
            wave_bottom_item.setData([], [], connect="all")

    def _align_wave_arrays(self, x: np.ndarray, amplitude: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.waveform_controller._align_wave_arrays(x, amplitude)

    def _align_wave_channels(self, x: np.ndarray, amplitudes) -> tuple[np.ndarray, np.ndarray]:
        return self.waveform_controller._align_wave_channels(x, amplitudes)

    
    def _sanitize_wave_array(values: np.ndarray) -> np.ndarray:
        return WaveformController._sanitize_wave_array(values)

    def _safe_set_step_wave_item(
        self,
        item: pg.PlotDataItem,
        x_values: np.ndarray,
        y_values: np.ndarray,
        fill_level: float = 0.0,
    ) -> None:
        return self.waveform_controller._safe_set_step_wave_item(item, x_values, y_values, fill_level=fill_level)

    def _compute_wave_edges(self, x_values: np.ndarray) -> np.ndarray:
        return self.waveform_controller._compute_wave_edges(x_values)

    
    def _combine_channels_to_single(amplitudes: np.ndarray) -> np.ndarray:
        return WaveformController._combine_channels_to_single(amplitudes)

    def _ensure_channel_wave_items(self, channel_count: int) -> None:
        return self.waveform_controller._ensure_channel_wave_items(channel_count)

    def _apply_channel_wave_item_styles(self) -> None:
        return self.waveform_controller._apply_channel_wave_item_styles()

    def _set_waveform_multichannel(self, x: np.ndarray, amplitudes: np.ndarray) -> None:
        return self.waveform_controller._set_waveform_multichannel(x, amplitudes)

    def _set_waveform_from_channels(self, x: np.ndarray, amplitudes) -> None:
        return self.waveform_controller._set_waveform_from_channels(x, amplitudes)

    def _set_waveform_amplitude(self, x: np.ndarray, amplitude: np.ndarray) -> None:
        return self.waveform_controller._set_waveform_amplitude(x, amplitude)

    def _build_menus(self) -> None:
        self.settings_menu = self.menuBar().addMenu("")
        self.about_action = QAction("", self)
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self.open_about_dialog)
        self.settings_menu.addAction(self.about_action)

        self.preferences_action = QAction("", self)
        self.preferences_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.preferences_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self.preferences_action.triggered.connect(self.open_settings_dialog)
        self.settings_menu.addAction(self.preferences_action)

    def _apply_language(self) -> None:
        self.setWindowTitle(self._txt("Audio Player", "Audio Player"))
        self.open_button.setText(self._txt("Open audio", "Open audio"))
        self.open_button.setToolTip(self._txt("Open één of meerdere audiobestanden", "Open one or more audio files"))
        self.remove_button.setText(self._txt("Remove", "Remove"))
        self.remove_button.setToolTip(self._txt("Verwijder geselecteerde track uit playlist", "Remove selected track from playlist"))
        self.sort_button.setText(self._txt("Rangschik", "Sort"))
        self.sort_by_name_action.setText(self._txt("Naam (A-Z)", "Name (A-Z)"))
        self.sort_by_duration_action.setText(self._txt("Tijd (kort -> lang)", "Time (short -> long)"))
        self.sort_by_duration_desc_action.setText(self._txt("Tijd (lang -> kort)", "Time (long -> short)"))
        self.prev_button.setText(self._txt("Prev", "Prev"))
        self.next_button.setText(self._txt("Next", "Next"))
        self.stop_button.setText(self._txt("Stop", "Stop"))
        self.fit_button.setText(self._txt("Fit", "Fit"))
        self.plot.setLabel("bottom", self._txt("Tijd (mm:ss)", "Time (mm:ss)"))
        self.theme_button.setToolTip(self._txt("Thema", "Theme"))
        self.theme_actions["system"].setText(self._txt("Systeem", "System"))
        self.theme_actions["dark"].setText(self._txt("Donker", "Dark"))
        self.theme_actions["light"].setText(self._txt("Licht", "Light"))
        self.settings_menu.setTitle(self._txt("Instellingen", "Settings"))
        self.about_action.setText("About")
        self.preferences_action.setText(self._txt("Voorkeuren...", "Preferences..."))
        self._update_repeat_button_text()
        self._set_auto_continue_enabled(self._auto_continue_enabled, save=False)
        self._set_follow_playhead_enabled(self._follow_playhead, save=False)
        self._apply_audio_preferences(update_status=False)
        self.on_playback_state(self.player.playbackState())

    def open_about_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("About")
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Audio Player")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        version = QLabel(self._txt(f"Versie {APP_VERSION}", f"Version {APP_VERSION}"))
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(version)

        info = QLabel(
            self._txt(
                "Desktop audio player met waveform en playlist.",
                "Desktop audio player with waveform and playlist.",
            )
        )
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        close_button = QPushButton(self._txt("Sluiten", "Close"))
        close_button.clicked.connect(dialog.accept)
        close_button.setDefault(True)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignCenter)

        dialog.exec()

    def _post_feedback_issue(
        self,
        issue_kind: str,
        title: str,
        details: str,
        reporter_name: str,
        guest_mode: bool,
    ) -> tuple[bool, str, str]:
        return post_feedback_issue(
            issue_kind=issue_kind,
            title=title,
            details=details,
            reporter_name=reporter_name,
            guest_mode=guest_mode,
            language=self._language,
            worker_url=self._feedback_worker_url,
            worker_key=self._feedback_worker_key,
            txt=self._txt,
        )

    def open_feedback_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self._txt("Meld een probleem of feature", "Report issue or feature"))
        dialog.setMinimumWidth(700)
        dialog.resize(760, 540)

        layout = QVBoxLayout(dialog)
        intro = QLabel(
            self._txt(
                "Verstuur bug reports of feature requests via de feedback service.",
                "Send bug reports or feature requests through the feedback service.",
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        issue_type_combo = QComboBox()
        issue_type_combo.addItem(self._txt("Bug / probleem", "Bug / problem"), "bug")
        issue_type_combo.addItem(self._txt("Feature request", "Feature request"), "feature")
        form.addRow(self._txt("Type", "Type"), issue_type_combo)

        title_edit = QLineEdit()
        title_edit.setPlaceholderText(self._txt("Korte titel", "Short title"))
        form.addRow(self._txt("Titel", "Title"), title_edit)

        details_edit = QTextEdit()
        details_edit.setPlaceholderText(
            self._txt(
                "Beschrijf het probleem of je gewenste feature zo concreet mogelijk.",
                "Describe the problem or requested feature as clearly as possible.",
            )
        )
        details_edit.setMinimumHeight(220)
        form.addRow(self._txt("Beschrijving", "Description"), details_edit)

        guest_checkbox = QCheckBox(self._txt("Verstuur als gast", "Submit as guest"))
        guest_checkbox.setChecked(True)
        form.addRow("", guest_checkbox)

        reporter_edit = QLineEdit()
        reporter_edit.setPlaceholderText(self._txt("Naam (optioneel)", "Name (optional)"))
        reporter_edit.setEnabled(False)
        form.addRow(self._txt("Naam", "Name"), reporter_edit)

        def _update_reporter_edit(enabled_as_named: bool) -> None:
            reporter_edit.setEnabled(not enabled_as_named)
            if enabled_as_named:
                reporter_edit.setText("")

        guest_checkbox.toggled.connect(_update_reporter_edit)

        layout.addLayout(form)

        helper = QLabel(self._txt("Doel: GitHub Issues", "Target: GitHub Issues"))
        helper.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(helper)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        submit_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if submit_button is not None:
            submit_button.setText(self._txt("Versturen", "Submit"))
            submit_button.setDefault(True)
        cancel_button = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText(self._txt("Annuleren", "Cancel"))
        layout.addWidget(button_box)

        def submit_feedback() -> None:
            issue_kind = str(issue_type_combo.currentData() or "bug")
            title = title_edit.text().strip()
            details = details_edit.toPlainText().strip()
            guest_mode = guest_checkbox.isChecked()
            reporter_name = reporter_edit.text().strip()

            ok, message, issue_url = self._post_feedback_issue(
                issue_kind,
                title,
                details,
                reporter_name,
                guest_mode,
            )
            if not ok:
                QMessageBox.warning(dialog, self._txt("Feedback verzenden mislukt", "Feedback submit failed"), message)
                return

            if issue_url:
                message = f"{message}\n{issue_url}"
            QMessageBox.information(dialog, self._txt("Feedback verstuurd", "Feedback sent"), message)
            dialog.accept()

        button_box.accepted.connect(submit_feedback)
        button_box.rejected.connect(dialog.reject)
        dialog.exec()

    def open_settings_dialog(self) -> None:
        open_settings_dialog_view(self)

    def _set_waveform_resolution(self, points: int, save: bool = True) -> None:
        return self.waveform_controller._set_waveform_resolution(points, save=save)

    def _set_waveform_view_mode(self, mode: str, save: bool = True) -> None:
        return self.waveform_controller._set_waveform_view_mode(mode, save=save)

    def _remove_selected_track_with_shortcut(self) -> None:
        return self.playlist_controller._remove_selected_track_with_shortcut()

    def remove_selected_track(self) -> None:
        return self.playlist_controller.remove_selected_track()

    def _track_duration(self, path: str) -> float:
        return self.playlist_controller._track_duration(path)

    def _rebuild_playlist_items(self, selected_path: str) -> None:
        return self.playlist_controller._rebuild_playlist_items(selected_path)

    def sort_playlist_by_name(self) -> None:
        return self.playlist_controller.sort_playlist_by_name()

    def sort_playlist_by_time_asc(self) -> None:
        return self.playlist_controller.sort_playlist_by_time_asc()

    def sort_playlist_by_time_desc(self) -> None:
        return self.playlist_controller.sort_playlist_by_time_desc()

    def _sync_tracks_from_playlist(self) -> None:
        return self.playlist_controller._sync_tracks_from_playlist()

    def _update_repeat_button_text(self) -> None:
        self.repeat_button.setText("")
        self.repeat_button.setIcon(self._build_repeat_mode_icon(self._repeat_mode))
        tooltip_map = {
            "off": self._txt("Repeat: uit", "Repeat: off"),
            "one": self._txt("Repeat: huidige track", "Repeat: current track"),
            "all": self._txt("Repeat: hele playlist", "Repeat: whole playlist"),
        }
        self.repeat_button.setToolTip(tooltip_map.get(self._repeat_mode, "Repeat: uit"))

    def _cycle_repeat_mode(self) -> None:
        order = ("off", "one", "all")
        try:
            idx = order.index(self._repeat_mode)
        except ValueError:
            idx = 0
        self._repeat_mode = order[(idx + 1) % len(order)]
        self._update_repeat_button_text()

    def _set_auto_continue_enabled(self, enabled: bool, save: bool = False) -> None:
        enabled = bool(enabled)
        self._auto_continue_enabled = enabled
        if self.auto_next_button.isChecked() != enabled:
            self.auto_next_button.setChecked(enabled)
        self.auto_next_button.setText("")
        self.auto_next_button.setIcon(self._build_auto_next_icon(enabled))
        self.auto_next_button.setToolTip(
            self._txt("Auto next: aan (ga automatisch naar volgende track)", "Auto next: on (go to next track)")
            if enabled
            else self._txt("Auto next: uit (stop op einde van track)", "Auto next: off (stop at track end)")
        )
        if save:
            self._save_preferences()

    def _set_follow_playhead_enabled(self, enabled: bool, save: bool = False) -> None:
        enabled = bool(enabled)
        self._follow_playhead = enabled
        if self.follow_button.isChecked() != enabled:
            self.follow_button.setChecked(enabled)
        self.follow_button.setText("")
        self.follow_button.setIcon(self._build_follow_icon(enabled))
        self.follow_button.setToolTip(
            self._txt("Playhead volgen: aan", "Follow playhead: on")
            if enabled
            else self._txt("Playhead volgen: uit", "Follow playhead: off")
        )
        if save:
            self._save_preferences()

    @staticmethod
    def _qss_rgba(color: QColor, alpha: int) -> str:
        return qss_rgba(color, alpha)

    def _build_dark_style(self, accent: QColor) -> str:
        return build_dark_style(accent)

    def _build_light_style(self, accent: QColor) -> str:
        return build_light_style(accent)

    def _build_repeat_mode_icon(self, mode: str) -> QIcon:
        return build_repeat_mode_icon(mode, self.palette().buttonText().color())

    def _build_auto_next_icon(self, enabled: bool) -> QIcon:
        return build_auto_next_icon(enabled, self.palette().buttonText().color())

    def _build_follow_icon(self, enabled: bool) -> QIcon:
        return build_follow_icon(enabled, self.palette().buttonText().color())

    def _build_sun_icon(self) -> QIcon:
        return build_sun_icon()

    def _build_moon_icon(self) -> QIcon:
        return build_moon_icon()

    def _system_prefers_dark(self) -> bool:
        return system_prefers_dark(self)

    def _resolve_playhead_color(self, effective_theme: str, accent: QColor) -> QColor:
        return resolve_playhead_color(self._playhead_color, effective_theme, accent)

    @staticmethod
    def _make_playhead_pen(color: QColor, width: float) -> QPen:
        return make_playhead_pen(color, width)

    def _apply_playhead_pen(self, color: QColor, width: float) -> None:
        base_pen = self._make_playhead_pen(color, width)
        hover_color = QColor(color)
        if hover_color.lightness() < 130:
            hover_color = hover_color.lighter(135)
        else:
            hover_color = hover_color.darker(135)
        hover_pen = self._make_playhead_pen(hover_color, min(6.0, width + 0.6))
        self.playhead.setPen(base_pen)
        self.playhead.setHoverPen(hover_pen)
        # Force refresh even if setPen happened during hover state.
        self.playhead.currentPen = self.playhead.pen
        self.playhead.update()

    def set_theme_mode(self, mode: str) -> None:
        if mode not in {"system", "dark", "light"}:
            return
        self._theme_mode = mode
        self.theme_actions[mode].setChecked(True)
        self._apply_effective_theme()

    def _apply_effective_theme(self) -> None:
        if self._applying_theme:
            return

        if self._theme_mode == "system":
            effective = "dark" if self._system_prefers_dark() else "light"
        else:
            effective = self._theme_mode

        if (
            effective == self._effective_theme
            and self._accent_color == self._effective_accent_color
            and self._playhead_color == self._effective_playhead_color
            and abs(self._playhead_width - self._effective_playhead_width) < 0.01
            and self.styleSheet()
        ):
            return

        self._applying_theme = True
        try:
            accent = QColor(self._accent_color)
            if not accent.isValid():
                accent = QColor("#7ED298")

            playhead_color = self._resolve_playhead_color(effective, accent)
            playhead_width = max(1.0, min(float(self._playhead_width), 6.0))

            if effective == "light":
                self.setStyleSheet(self._build_light_style(accent))
                self.plot.setBackground("#ffffff")
                axis_pen = pg.mkPen("#5a6d84")
                wave_top_color = accent.darker(145)
                wave_bottom_color = accent.darker(120)
                fill_color = QColor(accent)
                fill_color.setAlpha(90)
                load_label_color = accent.darker(180)
                self._wave_top_color = QColor(wave_top_color)
                self._wave_bottom_color = QColor(wave_bottom_color)
                self._wave_fill_color = QColor(fill_color)
                self.wave_top.setPen(pg.mkPen(width=1.1, color=wave_top_color))
                self.wave_bottom.setPen(pg.mkPen(width=1.1, color=wave_bottom_color))
                self.wave_top.setBrush(pg.mkBrush(fill_color))
                self.wave_bottom.setBrush(pg.mkBrush(fill_color))
                self._apply_playhead_pen(playhead_color, playhead_width)
                self.theme_button.setIcon(self.sun_icon)
                self.wave_load_label.setStyleSheet(f"color: {load_label_color.name()};")
            else:
                self.setStyleSheet(self._build_dark_style(accent))
                self.plot.setBackground("#181818")
                axis_pen = pg.mkPen("#9da8b5")
                wave_top_color = accent.lighter(125)
                wave_bottom_color = accent
                fill_color = QColor(accent)
                fill_color.setAlpha(118)
                load_label_color = accent.lighter(145)
                self._wave_top_color = QColor(wave_top_color)
                self._wave_bottom_color = QColor(wave_bottom_color)
                self._wave_fill_color = QColor(fill_color)
                self.wave_top.setPen(pg.mkPen(width=1.1, color=wave_top_color))
                self.wave_bottom.setPen(pg.mkPen(width=1.1, color=wave_bottom_color))
                self.wave_top.setBrush(pg.mkBrush(fill_color))
                self.wave_bottom.setBrush(pg.mkBrush(fill_color))
                self._apply_playhead_pen(playhead_color, playhead_width)
                self.theme_button.setIcon(self.moon_icon)
                self.wave_load_label.setStyleSheet(f"color: {load_label_color.name()};")

            axis_bottom = self.plot.getAxis("bottom")
            axis_bottom.setTextPen(axis_pen)
            axis_bottom.setPen(axis_pen)
            self._apply_channel_wave_item_styles()
            self._update_repeat_button_text()
            self._set_auto_continue_enabled(self._auto_continue_enabled, save=False)
            self._set_follow_playhead_enabled(self._follow_playhead, save=False)
            self._effective_theme = effective
            self._effective_accent_color = self._accent_color
            self._effective_playhead_color = self._playhead_color
            self._effective_playhead_width = self._playhead_width
        finally:
            self._applying_theme = False

    @staticmethod
    def _is_supported_audio_path(path: str) -> bool:
        return PlaylistController._is_supported_audio_path(path)

    @staticmethod
    def _extract_local_paths_from_mime(mime_data) -> list[str]:
        return PlaylistController._extract_local_paths_from_mime(mime_data)

    def _normalize_input_paths(self, paths: list[str]) -> list[str]:
        return self.playlist_controller._normalize_input_paths(paths)

    def add_files(self, paths: list[str], select_first_new: bool) -> int:
        return self.playlist_controller.add_files(paths, select_first_new)

    def open_files(self) -> None:
        return self.playlist_controller.open_files()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        return self.playlist_controller.dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        return self.playlist_controller.dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        return self.playlist_controller.dropEvent(event)

    def _file_signature(self, path: str, points: int | None = None) -> str:
        stat = os.stat(path)
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        if points is not None:
            signature = f"{signature}|p{points}"
        return signature

    def _cache_get(self, path: str, signature: str):
        cached = self.wave_cache.get(path)
        if cached and cached[0] == signature:
            return cached
        return None

    def _cache_store(self, path: str, signature: str, x: np.ndarray, amplitudes: np.ndarray) -> None:
        self.wave_cache[path] = (signature, x, amplitudes)
        if len(self.wave_cache) > 40:
            oldest = next(iter(self.wave_cache))
            self.wave_cache.pop(oldest, None)

    def _fit_track_view(self) -> None:
        return self.waveform_controller._fit_track_view()

    def _current_track_path(self) -> str:
        return self.waveform_controller._current_track_path()

    def _render_partial_for_path(self, path: str, signature: str) -> bool:
        return self.waveform_controller._render_partial_for_path(path, signature)

    def load_track(self, row: int) -> None:
        return self.playlist_controller.load_track(row)

    def _load_waveform_for_track(self, path: str) -> None:
        return self.waveform_controller._load_waveform_for_track(path)

    def _start_active_wave_worker(self, path: str, signature: str, emit_progress: bool, points: int) -> None:
        return self.waveform_controller._start_active_wave_worker(path, signature, emit_progress, points)

    def _start_preload_wave_worker(self, path: str, signature: str, emit_progress: bool, points: int) -> None:
        return self.waveform_controller._start_preload_wave_worker(path, signature, emit_progress, points)

    def _stop_active_wave_worker(self, wait_ms: int = 80) -> None:
        return self.waveform_controller._stop_active_wave_worker(wait_ms=wait_ms)

    def _on_active_wave_progress(
        self,
        request_id: int,
        path: str,
        x_obj,
        amp_obj,
        filled_bins: int,
        total_bins: int,
    ) -> None:
        return self.waveform_controller._on_active_wave_progress(
            request_id,
            path,
            x_obj,
            amp_obj,
            filled_bins,
            total_bins,
        )

    def _on_active_wave_finished(self, request_id: int, path: str, x_obj, amp_obj) -> None:
        return self.waveform_controller._on_active_wave_finished(request_id, path, x_obj, amp_obj)

    def _on_active_wave_failed(self, request_id: int, path: str, error_message: str) -> None:
        return self.waveform_controller._on_active_wave_failed(request_id, path, error_message)

    def _on_active_wave_thread_finished(self, request_id: int) -> None:
        return self.waveform_controller._on_active_wave_thread_finished(request_id)

    def _remove_from_preload_queue(self, path: str) -> None:
        return self.waveform_controller._remove_from_preload_queue(path)

    def _enqueue_preload(self, paths: list[str]) -> None:
        return self.waveform_controller._enqueue_preload(paths)

    def _start_next_preload(self) -> None:
        return self.waveform_controller._start_next_preload()

    def _stop_preload_worker(self, requeue: bool, wait_ms: int = 80) -> None:
        return self.waveform_controller._stop_preload_worker(requeue, wait_ms=wait_ms)

    def _on_preload_progress(
        self,
        request_id: int,
        path: str,
        x_obj,
        amp_obj,
        filled_bins: int,
        total_bins: int,
    ) -> None:
        return self.waveform_controller._on_preload_progress(
            request_id,
            path,
            x_obj,
            amp_obj,
            filled_bins,
            total_bins,
        )

    def _on_preload_finished(self, request_id: int, path: str, x_obj, amp_obj) -> None:
        return self.waveform_controller._on_preload_finished(request_id, path, x_obj, amp_obj)

    def _on_preload_failed(self, request_id: int, path: str, _error_message: str) -> None:
        return self.waveform_controller._on_preload_failed(request_id, path, _error_message)

    def _on_preload_wave_thread_finished(self, request_id: int) -> None:
        return self.waveform_controller._on_preload_wave_thread_finished(request_id)

    def _on_audio_outputs_changed(self) -> None:
        return self.playback_controller._on_audio_outputs_changed()

    def _start_playback_smooth(self, from_track_start: bool = False) -> None:
        return self.playback_controller._start_playback_smooth(from_track_start=from_track_start)

    def toggle_play(self) -> None:
        return self.playback_controller.toggle_play()

    def on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        return self.playback_controller.on_playback_state(state)

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        return self.playback_controller.on_media_status_changed(status)

    def _handle_track_end(self) -> None:
        return self.playback_controller._handle_track_end()

    def on_position_changed(self, position_ms: int) -> None:
        return self.playback_controller.on_position_changed(position_ms)

    def stop(self) -> None:
        return self.playback_controller.stop()

    def previous_track(self) -> None:
        return self.playback_controller.previous_track()

    def next_track(self) -> None:
        return self.playback_controller.next_track()

    def zoom_in(self) -> None:
        return self.playback_controller.zoom_in()

    def zoom_out(self) -> None:
        return self.playback_controller.zoom_out()

    def _zoom(self, factor: float) -> None:
        return self.playback_controller._zoom(factor)

    def _set_view(self, start_s: float, window_s: float) -> None:
        return self.playback_controller._set_view(start_s, window_s)

    def _set_playhead_pos(self, pos_s: float) -> None:
        return self.playback_controller._set_playhead_pos(pos_s)

    def _on_plot_click(self, ev) -> None:
        return self.playback_controller._on_plot_click(ev)

    def _on_playhead_seek_finished(self) -> None:
        return self.playback_controller._on_playhead_seek_finished()

    def _on_xrange_changed(self, _, ranges) -> None:
        return self.playback_controller._on_xrange_changed(_, ranges)

    @staticmethod
    def format_time(seconds: float) -> str:
        milliseconds = int(round(seconds * 1000))
        ms = milliseconds % 1000
        s_total = milliseconds // 1000
        s = s_total % 60
        m = (s_total // 60) % 60
        h = s_total // 3600
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        return f"{m:02d}:{s:02d}.{ms:03d}"
