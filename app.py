from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import soundfile as sf
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _parse_dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        env_key = key.strip()
        if not env_key:
            continue
        env_value = _parse_dotenv_value(raw_value)
        os.environ.setdefault(env_key, env_value)


def _load_dotenv() -> None:
    candidates: list[Path] = []
    meipass_root = str(getattr(sys, "_MEIPASS", "")).strip()
    if meipass_root:
        try:
            candidates.append(Path(meipass_root) / ".env")
        except Exception:  # noqa: BLE001
            pass
    try:
        candidates.append(Path.cwd() / ".env")
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.append(Path(__file__).resolve().parent / ".env")
    except Exception:  # noqa: BLE001
        pass
    if sys.argv and sys.argv[0]:
        try:
            candidates.append(Path(sys.argv[0]).resolve().parent / ".env")
        except Exception:  # noqa: BLE001
            pass
    try:
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        _load_dotenv_file(candidate)


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


AUDIO_EXTENSIONS = {
    ".wav",
    ".wave",
    ".flac",
    ".ogg",
    ".aiff",
    ".aif",
    ".mp3",
    ".m4a",
    ".aac",
    ".wma",
}

ROUTING_TARGET_CHANNELS = {
    "auto": 0,
    "stereo": 2,
    "surround_5_1": 6,
    "surround_7_1": 8,
    "immersive_7_1_4": 12,
}

ROUTING_CHANNEL_LABELS = (
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
)

APP_VERSION = "0.0.1"
FEEDBACK_WORKER_ENV_URL = "AUDIOPLAYER_FEEDBACK_WORKER_URL"
FEEDBACK_WORKER_ENV_KEY = "AUDIOPLAYER_FEEDBACK_WORKER_KEY"


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


DARK_STYLE = """
QWidget {
    background: #1f1f1f;
    color: #d4d4d4;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}
QMainWindow {
    background: #181818;
}
QToolBar {
    spacing: 6px;
    background: #181818;
    border-bottom: 1px solid #2c2c2c;
    padding: 6px;
}
QToolBar::separator {
    width: 0px;
    background: transparent;
}
QPushButton {
    background: #2f2f2f;
    border: 1px solid #4a4a4a;
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}
QPushButton:hover {
    background: #3a3a3a;
}
QPushButton:pressed {
    background: #272727;
}
QPushButton:checked {
    background: #264f78;
    border: 1px solid #3d6d9a;
}
QListWidget {
    background: #1b1b1b;
    border: 1px solid #303030;
    border-radius: 10px;
    padding: 4px;
}
QListWidget::item {
    padding: 6px;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: #264f78;
}
QFrame#InfoCard {
    background: #1b1b1b;
    border: 1px solid #303030;
    border-radius: 10px;
}
QToolButton#ThemeButton {
    background: #2f2f2f;
    border: 1px solid #4a4a4a;
    border-radius: 16px;
    padding: 5px;
}
QToolButton#ThemeButton:hover {
    background: #3a3a3a;
}
QToolButton#ToolbarButton {
    background: #2f2f2f;
    border: 1px solid #4a4a4a;
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}
QToolButton#ToolbarButton:hover {
    background: #3a3a3a;
}
QPushButton#IconControl {
    background: transparent;
    border: none;
    padding: 2px;
}
QPushButton#IconControl:hover {
    background: #343434;
    border-radius: 6px;
}
QPushButton#IconControl:checked {
    background: #264f78;
    border-radius: 6px;
}
"""


LIGHT_STYLE = """
QWidget {
    background: #f4f7fb;
    color: #17212f;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}
QMainWindow {
    background: #edf2f8;
}
QToolBar {
    spacing: 6px;
    background: #f6f9ff;
    border-bottom: 1px solid #cbd9ec;
    padding: 6px;
}
QToolBar::separator {
    width: 0px;
    background: transparent;
}
QPushButton {
    background: #dce8f7;
    border: 1px solid #b7cbe2;
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}
QPushButton:hover {
    background: #c8dcf4;
}
QPushButton:pressed {
    background: #b7cee9;
}
QPushButton:checked {
    background: #a3c2e6;
    border: 1px solid #7ba4d2;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #bfd0e5;
    border-radius: 10px;
    padding: 4px;
}
QListWidget::item {
    padding: 6px;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: #bfd7f7;
}
QFrame#InfoCard {
    background: #ffffff;
    border: 1px solid #bfd0e5;
    border-radius: 10px;
}
QToolButton#ThemeButton {
    background: #dce8f7;
    border: 1px solid #b7cbe2;
    border-radius: 16px;
    padding: 5px;
}
QToolButton#ThemeButton:hover {
    background: #c8dcf4;
}
QToolButton#ToolbarButton {
    background: #dce8f7;
    border: 1px solid #b7cbe2;
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}
QToolButton#ToolbarButton:hover {
    background: #c8dcf4;
}
QPushButton#IconControl {
    background: transparent;
    border: none;
    padding: 2px;
}
QPushButton#IconControl:hover {
    background: #d9e6f8;
    border-radius: 6px;
}
QPushButton#IconControl:checked {
    background: #a3c2e6;
    border-radius: 6px;
}
"""


@dataclass
class Track:
    path: str
    name: str


class AudioPlayerApplication(QApplication):
    fileOpenRequested = Signal(str)

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._pending_file_opens: list[str] = []

    def event(self, event) -> bool:  # noqa: A003
        if event.type() == QEvent.Type.FileOpen:
            path = ""
            try:
                path = event.file()
            except Exception:  # noqa: BLE001
                path = ""
            if path:
                self._pending_file_opens.append(path)
                self.fileOpenRequested.emit(path)
            return True
        return super().event(event)

    def take_pending_file_opens(self) -> list[str]:
        items = list(self._pending_file_opens)
        self._pending_file_opens.clear()
        return items


class PlaylistWidget(QListWidget):
    reordered = Signal()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        super().dropEvent(event)
        self.reordered.emit()


class WaveformPlayer(QMainWindow):
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
        self._default_follow_playhead = True
        self._follow_playhead = True
        self._playhead_color = ""
        self._playhead_width = 2.0
        self._effective_playhead_color = ""
        self._effective_playhead_width = 0.0
        self._waveform_points = 4200
        self._waveform_view_mode = "combined"
        self._audio_output_device_key = ""
        self._audio_routing_mode = "auto"
        self._effective_audio_route_note = ""
        self._audio_matrix_enabled = False
        self._audio_routing_matrix = self._default_routing_matrix()
        self._feedback_worker_url = os.getenv(FEEDBACK_WORKER_ENV_URL, "").strip()
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
        self._cleanup_stale_routed_files(max_age_s=18 * 3600)

        self.sun_icon = self._build_sun_icon()
        self.moon_icon = self._build_moon_icon()

        self._load_preferences()
        self._build_ui()
        self._connect_signals()
        self._apply_language()
        self.set_theme_mode(self._theme_mode)

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

    def closeEvent(self, event) -> None:  # noqa: N802
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
        return bool(self._audio_matrix_enabled or self._audio_routing_mode != "auto")

    def _effective_output_channels(self, source_channels: int) -> int:
        source_count = max(1, int(source_channels))
        outputs = self._audio_output_devices()
        _, effective_device, _, target_channels = self._resolve_audio_device(
            self._audio_output_device_key,
            self._audio_routing_mode,
            matrix_enabled=self._audio_matrix_enabled,
            matrix=self._audio_routing_matrix,
            devices=outputs,
        )
        device_max = max(1, int(effective_device.maximumChannelCount()))
        if self._audio_matrix_enabled:
            # Keep channel layout stable in matrix mode to avoid CoreAudio/FFmpeg reinterpreting
            # mono/2.1/3.0/... streams as different speaker layouts on each route change.
            return max(2, min(device_max, len(ROUTING_CHANNEL_LABELS)))
        if target_channels <= 0:
            target_channels = source_count
        target_channels = max(1, min(int(target_channels), len(ROUTING_CHANNEL_LABELS)))
        return max(1, min(target_channels, device_max))

    def _build_runtime_routing_matrix(self, source_channels: int, output_channels: int) -> np.ndarray:
        source_count = max(1, int(source_channels))
        output_count = max(1, int(output_channels))
        if self._audio_matrix_enabled:
            base = self._clone_routing_matrix(self._audio_routing_matrix)
        else:
            if self._audio_routing_mode == "auto":
                base = self._default_routing_matrix()
            else:
                target = self._routing_target_channels(self._audio_routing_mode, matrix_enabled=False)
                base = self._routing_matrix_preset(target)

        base_rows = len(base)
        base_cols = len(base[0]) if base_rows else 0
        matrix = np.zeros((source_count, output_count), dtype=np.float32)

        for src_idx in range(source_count):
            src_base = min(src_idx, max(0, base_rows - 1))
            mapped = False
            for out_idx in range(output_count):
                if src_base < base_rows and out_idx < base_cols and int(base[src_base][out_idx]):
                    matrix[src_idx, out_idx] = 1.0
                    mapped = True
            if not mapped and not self._audio_matrix_enabled:
                matrix[src_idx, min(src_idx, output_count - 1)] = 1.0

        if not self._audio_matrix_enabled:
            # In preset mode we keep auto-normalization to avoid clipping surprises.
            # In matrix mode, behave like a patchbay: each checked crosspoint is unity gain.
            for src_idx in range(source_count):
                row_sum = float(np.sum(np.abs(matrix[src_idx, :])))
                if row_sum > 1.0:
                    matrix[src_idx, :] /= row_sum

            for out_idx in range(output_count):
                col_sum = float(np.sum(np.abs(matrix[:, out_idx])))
                if col_sum > 1.0:
                    matrix[:, out_idx] /= col_sum

        return matrix

    def _resolve_playback_source(self, source_path: str) -> str:
        if not self._routing_requires_processing():
            return source_path

        try:
            info = sf.info(source_path)
        except Exception:  # noqa: BLE001
            return source_path

        source_channels = max(1, int(info.channels))
        output_channels = self._effective_output_channels(source_channels)
        runtime_matrix = self._build_runtime_routing_matrix(source_channels, output_channels)

        if (
            not self._audio_matrix_enabled
            and self._audio_routing_mode == "auto"
            and output_channels == source_channels
            and self._is_identity_routing(runtime_matrix)
        ):
            return source_path

        try:
            file_signature = self._file_signature(source_path)
        except Exception:  # noqa: BLE001
            file_signature = source_path

        route_token = (
            f"v4|{self._audio_routing_mode}|m{int(self._audio_matrix_enabled)}|"
            f"{self._serialize_routing_matrix(self._audio_routing_matrix)}|"
            f"sc{source_channels}|oc{output_channels}"
        )
        matrix_digest = hashlib.sha1(runtime_matrix.tobytes()).hexdigest()[:12]
        cache_key = hashlib.sha1(f"{file_signature}|{route_token}|{matrix_digest}".encode("utf-8")).hexdigest()

        cached_path = self._routed_audio_cache.get(cache_key, "")
        if cached_path and os.path.isfile(cached_path):
            return cached_path

        routed_path = self._routed_audio_dir / f"{cache_key}.wav"
        routed_path_str = str(routed_path)
        if os.path.isfile(routed_path_str):
            self._routed_audio_cache[cache_key] = routed_path_str
            self._session_routed_files.add(routed_path_str)
            self._trim_routed_audio_cache(max_entries=16)
            return routed_path_str

        tmp_path = routed_path.with_suffix(".tmp.wav")
        previous_status = self.status.text()
        try:
            self.status.setText(self._txt("Audio routing toepassen...", "Applying audio routing..."))
            QApplication.processEvents()
            with sf.SoundFile(source_path) as src, sf.SoundFile(
                str(tmp_path),
                mode="w",
                samplerate=int(src.samplerate),
                channels=output_channels,
                format="WAV",
                subtype="PCM_24",
            ) as dst:
                while True:
                    chunk = src.read(262144, dtype="float32", always_2d=True)
                    if chunk.size == 0:
                        break
                    chunk_in = chunk[:, :source_channels]
                    routed = np.matmul(chunk_in, runtime_matrix[: chunk_in.shape[1], :])
                    routed = np.clip(routed, -1.0, 1.0, out=routed)
                    dst.write(routed)
            os.replace(tmp_path, routed_path)
            self._routed_audio_cache[cache_key] = routed_path_str
            self._session_routed_files.add(routed_path_str)
            self._trim_routed_audio_cache(max_entries=16)
            self.status.setText(previous_status)
            return routed_path_str
        except Exception:  # noqa: BLE001
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:  # noqa: BLE001
                pass
            self.status.setText(previous_status)
            return source_path

    def _cleanup_stale_routed_files(self, max_age_s: int) -> None:
        now = time.time()
        for candidate in self._routed_audio_dir.glob("*.wav"):
            try:
                age = now - candidate.stat().st_mtime
            except Exception:  # noqa: BLE001
                continue
            if age <= max_age_s:
                continue
            try:
                candidate.unlink()
            except Exception:  # noqa: BLE001
                pass
        for tmp_file in self._routed_audio_dir.glob("*.tmp.wav"):
            try:
                tmp_file.unlink()
            except Exception:  # noqa: BLE001
                pass

    def _trim_routed_audio_cache(self, max_entries: int) -> None:
        if max_entries < 1:
            max_entries = 1
        current_source = self.player.source().toLocalFile()
        current_source_abs = os.path.abspath(current_source) if current_source else ""

        while len(self._routed_audio_cache) > max_entries:
            delete_key = None
            delete_path = ""
            for key, path in self._routed_audio_cache.items():
                if current_source_abs and os.path.abspath(path) == current_source_abs:
                    continue
                delete_key = key
                delete_path = path
                break
            if delete_key is None:
                break
            self._routed_audio_cache.pop(delete_key, None)
            try:
                if delete_path and os.path.isfile(delete_path):
                    os.remove(delete_path)
            except Exception:  # noqa: BLE001
                pass
            self._session_routed_files.discard(delete_path)

    def _cleanup_session_routed_files(self) -> None:
        current_source = self.player.source().toLocalFile()
        current_source_abs = os.path.abspath(current_source) if current_source else ""
        for path in list(self._session_routed_files):
            if current_source_abs and os.path.abspath(path) == current_source_abs:
                continue
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except Exception:  # noqa: BLE001
                pass
            self._session_routed_files.discard(path)

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
        use_matrix = self._audio_matrix_enabled if matrix_enabled is None else bool(matrix_enabled)
        matrix_data = self._audio_routing_matrix if matrix is None else matrix
        if use_matrix:
            return self._routing_matrix_target_channels(matrix_data)
        return int(ROUTING_TARGET_CHANNELS.get(mode, 0))

    def _routing_mode_label(self, mode: str) -> str:
        labels = {
            "auto": self._txt("Automatisch (bron-layout)", "Automatic (source layout)"),
            "stereo": self._txt("Stereo downmix (2.0)", "Stereo downmix (2.0)"),
            "surround_5_1": self._txt("Surround 5.1", "Surround 5.1"),
            "surround_7_1": self._txt("Surround 7.1", "Surround 7.1"),
            "immersive_7_1_4": self._txt("Immersive 7.1.4", "Immersive 7.1.4"),
        }
        return labels.get(mode, labels["auto"])

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
        outputs = devices if devices is not None else self._audio_output_devices()
        default_device = QMediaDevices.defaultAudioOutput()

        preferred = default_device
        if preferred_key:
            for device in outputs:
                if self._audio_device_key_for(device) == preferred_key:
                    preferred = device
                    break

        effective = preferred
        target_channels = self._routing_target_channels(
            routing_mode,
            matrix_enabled=matrix_enabled,
            matrix=matrix,
        )
        switched_for_routing = False

        if target_channels > 0:
            preferred_max = max(1, int(preferred.maximumChannelCount()))
            if preferred_max < target_channels:
                candidates = [device for device in outputs if int(device.maximumChannelCount()) >= target_channels]
                if candidates:
                    candidates.sort(
                        key=lambda device: (
                            int(device.maximumChannelCount()) - target_channels,
                            device.description().lower(),
                        )
                    )
                    effective = candidates[0]
                    switched_for_routing = self._audio_device_key_for(effective) != self._audio_device_key_for(preferred)

        return preferred, effective, switched_for_routing, target_channels

    def _audio_route_note(
        self,
        preferred: QAudioDevice,
        effective: QAudioDevice,
        switched_for_routing: bool,
        target_channels: int,
        matrix_enabled: bool | None = None,
    ) -> str:
        use_matrix = self._audio_matrix_enabled if matrix_enabled is None else bool(matrix_enabled)
        preferred_name = preferred.description() or self._txt("Standaard apparaat", "Default device")
        effective_name = effective.description() or preferred_name
        effective_max = max(1, int(effective.maximumChannelCount()))
        effective_layout = self._channel_layout_label(effective_max)

        if target_channels <= 0:
            if use_matrix:
                route_text = self._txt("Routing profiel: matrix (geen output actief)", "Routing profile: matrix (no active outputs)")
            else:
                route_text = self._txt("Routing: automatisch", "Routing: automatic")
        else:
            route_layout = self._channel_layout_label(target_channels)
            route_text = self._txt(
                f"Routing profiel: {route_layout}",
                f"Routing profile: {route_layout}",
            )
        if use_matrix:
            matrix_text = self._txt("Matrix routing: actief", "Matrix routing: enabled")
        else:
            matrix_text = self._txt("Matrix routing: uit", "Matrix routing: off")

        if switched_for_routing:
            switch_text = self._txt(
                f"Uitvoer aangepast naar '{effective_name}' voor dit routing-profiel.",
                f"Output switched to '{effective_name}' for this routing profile.",
            )
        elif target_channels > 0 and effective_max < target_channels:
            switch_text = self._txt(
                f"Geselecteerde output ondersteunt max {effective_layout}; systeem zal downmixen.",
                f"Selected output supports max {effective_layout}; system downmix will be used.",
            )
        else:
            switch_text = self._txt(
                f"Actieve output: {effective_name} ({effective_layout})",
                f"Active output: {effective_name} ({effective_layout})",
            )

        first_line = self._txt(
            f"Voorkeur output: {preferred_name}",
            f"Preferred output: {preferred_name}",
        )
        support_line = self._txt(
            "Ondersteunt multichannel bestanden t/m 7.1.4 indien output/device dit toelaat.",
            "Supports multichannel files up to 7.1.4 when output/device allows it.",
        )
        engine_line = self._txt(
            "Routing matrix wordt direct op de audiodata toegepast (gecachete routed playback file).",
            "Routing matrix is applied directly to audio data (cached routed playback file).",
        )
        return "\n".join((first_line, route_text, matrix_text, switch_text, support_line, engine_line))

    def _apply_audio_preferences(self, update_status: bool, refresh_source: bool = True) -> None:
        outputs = self._audio_output_devices()
        preferred, effective, switched_for_routing, target_channels = self._resolve_audio_device(
            self._audio_output_device_key,
            self._audio_routing_mode,
            matrix_enabled=self._audio_matrix_enabled,
            matrix=self._audio_routing_matrix,
            devices=outputs,
        )
        self.audio_output.setDevice(effective)
        self._effective_audio_route_note = self._audio_route_note(
            preferred,
            effective,
            switched_for_routing,
            target_channels,
            matrix_enabled=self._audio_matrix_enabled,
        )
        if refresh_source:
            self._refresh_current_playback_source()
        self.status.setToolTip(self._effective_audio_route_note)
        if update_status:
            self.status.setText(self._txt("Audio routing bijgewerkt", "Audio routing updated"))

    def _refresh_current_playback_source(self) -> None:
        track_path = self._current_track_path()
        if not track_path:
            return

        desired_source = self._resolve_playback_source(track_path)
        current_source = self.player.source().toLocalFile()
        if desired_source and current_source and os.path.abspath(desired_source) == os.path.abspath(current_source):
            return

        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        current_position = self.player.position()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(desired_source))
        if self.duration_s > 0:
            duration_ms = int(self.duration_s * 1000)
            self.player.setPosition(max(0, min(current_position, duration_ms)))
        if was_playing:
            self.player.play()

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

        routing_mode = str(self._settings.value("audio_routing_mode", self._audio_routing_mode))
        if routing_mode in ROUTING_TARGET_CHANNELS:
            self._audio_routing_mode = routing_mode
        self._audio_matrix_enabled = self._to_bool(
            self._settings.value("audio_matrix_enabled", self._audio_matrix_enabled),
            self._audio_matrix_enabled,
        )
        matrix_raw = str(self._settings.value("audio_routing_matrix", ""))
        self._audio_routing_matrix = self._parse_routing_matrix(matrix_raw)

        self._theme_mode = self._default_theme_mode
        self._repeat_mode = self._default_repeat_mode
        self._auto_continue_enabled = self._default_auto_continue_enabled
        self._follow_playhead = self._default_follow_playhead

    def _save_preferences(self) -> None:
        self._settings.setValue("language", self._language)
        self._settings.setValue("accent_color", self._accent_color)
        self._settings.setValue("default_theme", self._default_theme_mode)
        self._settings.setValue("default_repeat", self._default_repeat_mode)
        self._settings.setValue("default_auto_continue", self._default_auto_continue_enabled)
        self._settings.setValue("default_follow_playhead", self._default_follow_playhead)
        self._settings.setValue("playhead_color", self._playhead_color)
        self._settings.setValue("playhead_width", self._playhead_width)
        self._settings.setValue("waveform_points", self._waveform_points)
        self._settings.setValue("waveform_view_mode", self._waveform_view_mode)
        self._settings.setValue("audio_output_device", self._audio_output_device_key)
        self._settings.setValue("audio_routing_mode", self._audio_routing_mode)
        self._settings.setValue("audio_matrix_enabled", self._audio_matrix_enabled)
        self._settings.setValue("audio_routing_matrix", self._serialize_routing_matrix(self._audio_routing_matrix))

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
                "Desktop audio player met waveform, playlist en geavanceerde routing.",
                "Desktop audio player with waveform, playlist, and advanced routing.",
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
        worker_url = self._feedback_worker_url or os.getenv(FEEDBACK_WORKER_ENV_URL, "").strip()
        if not worker_url:
            return (
                False,
                self._txt(
                    "Feedback service is niet geconfigureerd. Voeg AUDIOPLAYER_FEEDBACK_WORKER_URL toe in .env.",
                    "Feedback service is not configured. Add AUDIOPLAYER_FEEDBACK_WORKER_URL in .env.",
                ),
                "",
            )

        clean_title = title.strip()
        clean_details = details.strip()
        clean_reporter = reporter_name.strip()
        reporter = self._txt("Gast", "Guest") if guest_mode else (clean_reporter or self._txt("Onbekend", "Unknown"))
        if not clean_title or not clean_details:
            return (
                False,
                self._txt("Titel en beschrijving zijn verplicht.", "Title and description are required."),
                "",
            )

        issue_label = "bug" if issue_kind == "bug" else "enhancement"
        prefix = "Bug" if issue_kind == "bug" else "Feature"
        payload = {
            "kind": issue_label,
            "title": f"[{prefix}] {clean_title}",
            "details": clean_details,
            "reporter": reporter,
            "language": self._language,
            "app_version": APP_VERSION,
        }

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AudioPlayer-App",
        }
        worker_key = self._feedback_worker_key or os.getenv(FEEDBACK_WORKER_ENV_KEY, "").strip()
        if worker_key:
            headers["X-Feedback-Key"] = worker_key

        req = urllib.request.Request(
            worker_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                url = str(data.get("issue_url", ""))
                success_message = str(data.get("message", "")).strip()
                if not success_message:
                    success_message = self._txt("Issue succesvol geplaatst.", "Issue created successfully.")
                return True, success_message, url
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                raw = ""
            message = ""
            if raw:
                try:
                    parsed = json.loads(raw)
                    message = str(parsed.get("message", "")).strip()
                except Exception:  # noqa: BLE001
                    message = raw.strip()
            if not message:
                message = str(exc)
            return (
                False,
                self._txt(
                    f"Feedback service weigerde de aanvraag: {message}",
                    f"Feedback service rejected the request: {message}",
                ),
                "",
            )
        except Exception as exc:  # noqa: BLE001
            return (
                False,
                self._txt(
                    f"Kon feedback niet posten: {exc}",
                    f"Could not post feedback: {exc}",
                ),
                "",
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
        dialog = QDialog(self)
        dialog.setWindowTitle(self._txt("Voorkeuren", "Preferences"))
        dialog.setMinimumWidth(980)
        dialog.setMinimumHeight(700)
        dialog.resize(1060, 760)

        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        general_tab = QWidget()
        general_form = QFormLayout(general_tab)
        defaults_tab = QWidget()
        defaults_form = QFormLayout(defaults_tab)
        audio_tab = QWidget()
        audio_form = QFormLayout(audio_tab)
        tabs.addTab(general_tab, self._txt("Algemeen", "General"))
        tabs.addTab(defaults_tab, self._txt("Defaults", "Defaults"))
        tabs.addTab(audio_tab, self._txt("Audio", "Audio"))

        defaults_note = QLabel(self._txt("Deze instellingen gelden als opstart-standaard.", "These settings are startup defaults."))
        defaults_note.setWordWrap(True)
        defaults_form.addRow(defaults_note)

        language_combo = QComboBox()
        language_combo.addItem("Nederlands", "nl")
        language_combo.addItem("English", "en")
        language_combo.setCurrentIndex(0 if self._language == "nl" else 1)
        general_form.addRow(self._txt("Taal", "Language"), language_combo)

        accent_button = QPushButton(self._accent_color.upper())
        accent_button.setToolTip(self._txt("Kies accent kleur", "Choose accent color"))
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
        general_form.addRow(self._txt("Waveform resolutie", "Waveform resolution"), resolution_combo)

        waveform_view_combo = QComboBox()
        waveform_view_combo.addItem(self._txt("Standaard (gecombineerd)", "Default (combined)"), "combined")
        waveform_view_combo.addItem(self._txt("Per kanaal (gescheiden)", "Per channel (separate)"), "channels")
        waveform_view_combo.setCurrentIndex(0 if self._waveform_view_mode == "combined" else 1)
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
        general_form.addRow(self._txt("Playhead dikte", "Playhead thickness"), playhead_width_combo)

        report_button = QPushButton(self._txt("Probleem melden / Feature aanvragen", "Report issue / Request feature"))
        report_button.clicked.connect(self.open_feedback_dialog)
        general_form.addRow("", report_button)

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
        audio_form.addRow(self._txt("Output device", "Output device"), output_device_combo)

        routing_combo = QComboBox()
        routing_modes = (
            "auto",
            "stereo",
            "surround_5_1",
            "surround_7_1",
            "immersive_7_1_4",
        )
        selected_routing_index = 0
        for idx, mode in enumerate(routing_modes):
            routing_combo.addItem(self._routing_mode_label(mode), mode)
            if mode == self._audio_routing_mode:
                selected_routing_index = idx
        routing_combo.setCurrentIndex(selected_routing_index)
        audio_form.addRow(self._txt("Routing", "Routing"), routing_combo)

        matrix_enabled_checkbox = QCheckBox(self._txt("Gebruik matrix routing", "Use matrix routing"))
        matrix_enabled_checkbox.setChecked(self._audio_matrix_enabled)
        audio_form.addRow("", matrix_enabled_checkbox)

        matrix_container = QWidget()
        matrix_container_layout = QVBoxLayout(matrix_container)
        matrix_container_layout.setContentsMargins(0, 0, 0, 0)
        matrix_container_layout.setSpacing(6)

        matrix_info = QLabel(
            self._txt(
                "Input = rijen (kanalen uit het audiobestand). Output = kolommen (kanalen naar je output device). Ondersteunt tot 7.1.4.",
                "Input = rows (channels from the audio file). Output = columns (channels sent to your output device). Supports up to 7.1.4.",
            )
        )
        matrix_info.setWordWrap(True)
        matrix_container_layout.addWidget(matrix_info)

        matrix_controls = QWidget()
        matrix_controls_layout = QHBoxLayout(matrix_controls)
        matrix_controls_layout.setContentsMargins(0, 0, 0, 0)
        matrix_controls_layout.setSpacing(6)
        matrix_identity_button = QPushButton(self._txt("Identiteit", "Identity"))
        matrix_profile_button = QPushButton(self._txt("Volg profiel", "Use profile"))
        matrix_clear_button = QPushButton(self._txt("Leeg", "Clear"))
        matrix_controls_layout.addWidget(matrix_identity_button)
        matrix_controls_layout.addWidget(matrix_profile_button)
        matrix_controls_layout.addWidget(matrix_clear_button)
        matrix_controls_layout.addStretch(1)
        matrix_container_layout.addWidget(matrix_controls)

        matrix_grid_host = QWidget()
        matrix_grid = QGridLayout(matrix_grid_host)
        matrix_grid.setContentsMargins(0, 0, 0, 0)
        matrix_grid.setHorizontalSpacing(8)
        matrix_grid.setVerticalSpacing(6)

        channel_labels = list(ROUTING_CHANNEL_LABELS)
        matrix_size = len(channel_labels)
        matrix_cells: list[list[QCheckBox]] = [[None for _ in range(matrix_size)] for _ in range(matrix_size)]  # type: ignore[list-item]
        row_col_label = QLabel(self._txt("Input\\Output", "Input\\Output"))
        row_col_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_col_label.setMinimumWidth(106)
        matrix_grid.addWidget(row_col_label, 0, 0)
        for col_idx, label in enumerate(channel_labels):
            header = QLabel(f"O{label}")
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.setMinimumWidth(30)
            matrix_grid.addWidget(header, 0, col_idx + 1)
            matrix_grid.setColumnMinimumWidth(col_idx + 1, 30)

        for row_idx, row_label in enumerate(channel_labels):
            row_header = QLabel(f"I{row_label}")
            row_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            row_header.setMinimumWidth(36)
            matrix_grid.addWidget(row_header, row_idx + 1, 0)
            for col_idx in range(matrix_size):
                cell = QCheckBox()
                cell.setText("")
                cell.setTristate(False)
                cell.setFixedSize(22, 22)
                cell.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
                cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                cell.setToolTip(f"Input {row_label} -> Output {channel_labels[col_idx]}")
                matrix_grid.addWidget(cell, row_idx + 1, col_idx + 1, alignment=Qt.AlignmentFlag.AlignCenter)
                matrix_cells[row_idx][col_idx] = cell

        matrix_grid_host.adjustSize()
        matrix_grid_host.setMinimumWidth(106 + (matrix_size * 36) + 16)
        matrix_grid_host.setMinimumHeight(34 + (matrix_size * 28))

        matrix_scroll = QScrollArea()
        matrix_scroll.setWidgetResizable(False)
        matrix_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        matrix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        matrix_scroll.setMinimumWidth(560)
        matrix_scroll.setMinimumHeight(240)
        matrix_scroll.setWidget(matrix_grid_host)
        matrix_container_layout.addWidget(matrix_scroll)
        audio_form.addRow(self._txt("Routing matrix", "Routing matrix"), matrix_container)

        audio_preview_label = QLabel("")
        audio_preview_label.setWordWrap(True)
        audio_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        audio_preview_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        audio_form.addRow(self._txt("Audio status", "Audio status"), audio_preview_label)

        def matrix_values_from_ui() -> list[list[int]]:
            values = [[0 for _ in range(matrix_size)] for _ in range(matrix_size)]
            for row_idx in range(matrix_size):
                for col_idx in range(matrix_size):
                    values[row_idx][col_idx] = 1 if matrix_cells[row_idx][col_idx].isChecked() else 0
            return values

        def apply_matrix_to_ui(values: list[list[int]]) -> None:
            safe = self._clone_routing_matrix(values)
            for row_idx in range(matrix_size):
                for col_idx in range(matrix_size):
                    matrix_cells[row_idx][col_idx].blockSignals(True)
                    matrix_cells[row_idx][col_idx].setChecked(bool(safe[row_idx][col_idx]))
                    matrix_cells[row_idx][col_idx].blockSignals(False)

        def set_matrix_ui_enabled(enabled: bool) -> None:
            matrix_controls.setEnabled(enabled)
            matrix_scroll.setEnabled(enabled)

        def apply_identity_preset() -> None:
            apply_matrix_to_ui(self._default_routing_matrix())
            refresh_audio_preview()

        def apply_profile_preset() -> None:
            selected_mode = str(routing_combo.currentData() or "auto")
            target = int(ROUTING_TARGET_CHANNELS.get(selected_mode, 0))
            apply_matrix_to_ui(self._routing_matrix_preset(target))
            refresh_audio_preview()

        def apply_clear_preset() -> None:
            apply_matrix_to_ui([[0 for _ in range(matrix_size)] for _ in range(matrix_size)])
            refresh_audio_preview()

        apply_matrix_to_ui(self._audio_routing_matrix)

        def refresh_audio_preview() -> None:
            preferred_key = str(output_device_combo.currentData() or "")
            routing_mode = str(routing_combo.currentData() or "auto")
            matrix_enabled = matrix_enabled_checkbox.isChecked()
            matrix_data = matrix_values_from_ui()
            preferred, effective, switched, target = self._resolve_audio_device(
                preferred_key,
                routing_mode,
                matrix_enabled=matrix_enabled,
                matrix=matrix_data,
                devices=output_devices,
            )
            audio_preview_label.setText(
                self._audio_route_note(
                    preferred,
                    effective,
                    switched,
                    target,
                    matrix_enabled=matrix_enabled,
                )
            )
            if matrix_enabled and target <= 0:
                warning = self._txt(
                    "\nGeen output geselecteerd: resultaat is stilte.",
                    "\nNo output selected: result is silence.",
                )
                audio_preview_label.setText(audio_preview_label.text() + warning)

        output_device_combo.currentIndexChanged.connect(refresh_audio_preview)
        routing_combo.currentIndexChanged.connect(refresh_audio_preview)
        matrix_enabled_checkbox.toggled.connect(set_matrix_ui_enabled)
        matrix_enabled_checkbox.toggled.connect(refresh_audio_preview)
        matrix_identity_button.clicked.connect(apply_identity_preset)
        matrix_profile_button.clicked.connect(apply_profile_preset)
        matrix_clear_button.clicked.connect(apply_clear_preset)
        for row_idx in range(matrix_size):
            for col_idx in range(matrix_size):
                matrix_cells[row_idx][col_idx].stateChanged.connect(refresh_audio_preview)
        set_matrix_ui_enabled(matrix_enabled_checkbox.isChecked())
        refresh_audio_preview()

        theme_combo = QComboBox()
        theme_combo.addItem(self._txt("Systeem", "System"), "system")
        theme_combo.addItem(self._txt("Donker", "Dark"), "dark")
        theme_combo.addItem(self._txt("Licht", "Light"), "light")
        theme_combo.setCurrentIndex(max(0, ("system", "dark", "light").index(self._default_theme_mode)))
        defaults_form.addRow(self._txt("Default theme", "Default theme"), theme_combo)

        repeat_combo = QComboBox()
        repeat_combo.addItem(self._txt("Uit", "Off"), "off")
        repeat_combo.addItem(self._txt("Huidige track", "Current track"), "one")
        repeat_combo.addItem(self._txt("Hele playlist", "Whole playlist"), "all")
        repeat_combo.setCurrentIndex(max(0, ("off", "one", "all").index(self._default_repeat_mode)))
        defaults_form.addRow(self._txt("Default repeat", "Default repeat"), repeat_combo)

        auto_next_checkbox = QCheckBox(self._txt("Standaard auto volgende track", "Default auto next track"))
        auto_next_checkbox.setChecked(self._default_auto_continue_enabled)
        defaults_form.addRow("", auto_next_checkbox)

        follow_checkbox = QCheckBox(self._txt("Standaard playhead volgen", "Default follow playhead"))
        follow_checkbox.setChecked(self._default_follow_playhead)
        defaults_form.addRow("", follow_checkbox)

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
            self._default_follow_playhead = follow_checkbox.isChecked()
            self._audio_output_device_key = str(output_device_combo.currentData() or "").strip().lower()
            self._audio_routing_mode = str(routing_combo.currentData() or "auto")
            self._audio_matrix_enabled = matrix_enabled_checkbox.isChecked()
            self._audio_routing_matrix = self._clone_routing_matrix(matrix_values_from_ui())
            self._set_waveform_resolution(int(resolution_combo.currentData()), save=False)
            self._set_waveform_view_mode(str(waveform_view_combo.currentData()), save=False)
            self._apply_audio_preferences(update_status=False)
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
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"

    def _build_dark_style(self, accent: QColor) -> str:
        checked_bg = accent.darker(210)
        checked_border = accent.darker(165)
        list_selected = accent.darker(200)
        list_selected_border = self._qss_rgba(accent.darker(135), 195)
        icon_checked = accent.darker(185)
        button_bg = self._qss_rgba(accent, 36)
        button_border = self._qss_rgba(accent, 135)
        button_hover = self._qss_rgba(accent, 62)
        button_pressed = self._qss_rgba(accent, 86)
        list_border = self._qss_rgba(accent, 88)
        tab_bg = "#252628"
        tab_hover_bg = "#2f3135"
        tab_inactive_text = "#c4cfdb"
        tab_active_text = "#f4f8ff"
        tab_selected_bg = self._qss_rgba(accent, 76)
        tab_selected_border = self._qss_rgba(accent, 170)
        return f"""
QWidget {{
    background: #1f1f1f;
    color: #d4d4d4;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background: #181818;
}}
QToolBar {{
    spacing: 6px;
    background: #181818;
    border-bottom: 1px solid #2c2c2c;
    padding: 6px;
}}
QToolBar#MainToolbar > QWidget {{
    background: transparent;
    border: none;
}}
QToolBar::separator {{
    width: 0px;
    border: none;
    margin: 0px;
    padding: 0px;
    background: transparent;
}}
QWidget#ToolbarContent {{
    background: transparent;
    border: none;
}}
QWidget#ToolbarSection {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}}
QPushButton:hover {{
    background: {button_hover};
}}
QPushButton:pressed {{
    background: {button_pressed};
}}
QPushButton:checked {{
    background: {checked_bg.name()};
    border: 1px solid {checked_border.name()};
}}
QListWidget {{
    background: #1b1b1b;
    border: 1px solid {list_border};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px;
    border-radius: 8px;
}}
QListWidget::item:selected {{
    background: {self._qss_rgba(list_selected, 220)};
    color: #f4f8ff;
    border: 1px solid {list_selected_border};
}}
QListWidget::item:selected:!active {{
    background: {self._qss_rgba(list_selected, 220)};
    color: #f4f8ff;
    border: 1px solid {list_selected_border};
}}
QFrame#InfoCard {{
    background: #1b1b1b;
    border: 1px solid {list_border};
    border-radius: 10px;
}}
QTabWidget::pane {{
    border: 1px solid #30363d;
    border-radius: 8px;
    top: -1px;
    padding: 8px;
    background: #1b1b1b;
}}
QTabBar::tab {{
    background: {tab_bg};
    color: {tab_inactive_text};
    border: 1px solid #3a4047;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 10px;
    margin-right: 4px;
    min-width: 92px;
}}
QTabBar::tab:!selected:hover {{
    background: {tab_hover_bg};
    color: #dde7f3;
}}
QTabBar::tab:selected {{
    background: {tab_selected_bg};
    color: {tab_active_text};
    border: 1px solid {tab_selected_border};
}}
QToolButton#ThemeButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 16px;
    padding: 5px;
}}
QToolButton#ThemeButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #f3f3f3;
}}
QToolButton#ToolbarButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton::menu-indicator {{
    background: transparent;
    border: none;
    image: none;
    width: 0px;
}}
QPushButton#IconControl {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QPushButton#IconControl:hover {{
    background: #343434;
    border-radius: 6px;
}}
QPushButton#IconControl:checked {{
    background: {self._qss_rgba(icon_checked, 210)};
    border-radius: 6px;
}}
"""

    def _build_light_style(self, accent: QColor) -> str:
        checked_bg = accent.lighter(170)
        checked_border = accent.lighter(130)
        list_selected = accent.lighter(175)
        list_selected_border = self._qss_rgba(accent.darker(120), 165)
        icon_checked = accent.lighter(170)
        button_bg = self._qss_rgba(accent, 45)
        button_border = self._qss_rgba(accent.darker(120), 132)
        button_hover = self._qss_rgba(accent, 75)
        button_pressed = self._qss_rgba(accent, 102)
        list_border = self._qss_rgba(accent.darker(120), 92)
        tab_bg = "#e8f0fb"
        tab_hover_bg = "#ddeafc"
        tab_inactive_text = "#2e4966"
        tab_active_text = "#0f243b"
        tab_selected_bg = self._qss_rgba(accent, 115)
        tab_selected_border = self._qss_rgba(accent.darker(120), 150)
        return f"""
QWidget {{
    background: #f4f7fb;
    color: #17212f;
    font-family: "SF Pro Display", "Avenir Next", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}
QMainWindow {{
    background: #edf2f8;
}}
QToolBar {{
    spacing: 6px;
    background: #f6f9ff;
    border-bottom: 1px solid #cbd9ec;
    padding: 6px;
}}
QToolBar#MainToolbar > QWidget {{
    background: transparent;
    border: none;
}}
QToolBar::separator {{
    width: 0px;
    border: none;
    margin: 0px;
    padding: 0px;
    background: transparent;
}}
QWidget#ToolbarContent {{
    background: transparent;
    border: none;
}}
QWidget#ToolbarSection {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}}
QPushButton:hover {{
    background: {button_hover};
}}
QPushButton:pressed {{
    background: {button_pressed};
}}
QPushButton:checked {{
    background: {checked_bg.name()};
    border: 1px solid {checked_border.name()};
}}
QListWidget {{
    background: #ffffff;
    border: 1px solid {list_border};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px;
    border-radius: 8px;
}}
QListWidget::item:selected {{
    background: {self._qss_rgba(list_selected, 220)};
    color: #0f243b;
    border: 1px solid {list_selected_border};
}}
QListWidget::item:selected:!active {{
    background: {self._qss_rgba(list_selected, 220)};
    color: #0f243b;
    border: 1px solid {list_selected_border};
}}
QFrame#InfoCard {{
    background: #ffffff;
    border: 1px solid {list_border};
    border-radius: 10px;
}}
QTabWidget::pane {{
    border: 1px solid #c5d5e8;
    border-radius: 8px;
    top: -1px;
    padding: 8px;
    background: #ffffff;
}}
QTabBar::tab {{
    background: {tab_bg};
    color: {tab_inactive_text};
    border: 1px solid #c5d5e8;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 10px;
    margin-right: 4px;
    min-width: 92px;
}}
QTabBar::tab:!selected:hover {{
    background: {tab_hover_bg};
    color: #19334d;
}}
QTabBar::tab:selected {{
    background: {tab_selected_bg};
    color: {tab_active_text};
    border: 1px solid {tab_selected_border};
}}
QToolButton#ThemeButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 16px;
    padding: 5px;
}}
QToolButton#ThemeButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 8px;
    padding: 5px 10px;
    color: #13253a;
}}
QToolButton#ToolbarButton:hover {{
    background: {button_hover};
}}
QToolButton#ToolbarButton::menu-indicator {{
    background: transparent;
    border: none;
    image: none;
    width: 0px;
}}
QPushButton#IconControl {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QPushButton#IconControl:hover {{
    background: #d9e6f8;
    border-radius: 6px;
}}
QPushButton#IconControl:checked {{
    background: {self._qss_rgba(icon_checked, 220)};
    border-radius: 6px;
}}
"""

    def _build_repeat_mode_icon(self, mode: str) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen_color = self.palette().buttonText().color()
        if mode == "off":
            pen_color = QColor(pen_color)
            pen_color.setAlpha(110)
        base_pen = QPen(pen_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(base_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        top_path = QPainterPath()
        top_path.moveTo(4, 8)
        top_path.cubicTo(4, 4, 8, 3, 11, 3)
        top_path.lineTo(16, 3)
        painter.drawPath(top_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        head_top = QPainterPath()
        head_top.moveTo(16, 3)
        head_top.lineTo(13.0, 1.0)
        head_top.lineTo(13.0, 5.0)
        head_top.closeSubpath()
        painter.drawPath(head_top)
        painter.setPen(base_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        bottom_path = QPainterPath()
        bottom_path.moveTo(16, 12)
        bottom_path.cubicTo(16, 16, 12, 17, 9, 17)
        bottom_path.lineTo(4, 17)
        painter.drawPath(bottom_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        head_bottom = QPainterPath()
        head_bottom.moveTo(4, 17)
        head_bottom.lineTo(7.0, 15.0)
        head_bottom.lineTo(7.0, 19.0)
        head_bottom.closeSubpath()
        painter.drawPath(head_bottom)
        painter.setPen(base_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if mode == "one":
            font = painter.font()
            font.setBold(True)
            font.setPointSizeF(9.0)
            painter.setFont(font)
            painter.setPen(QPen(pen_color, 1.4))
            painter.drawText(8, 13, "1")
        painter.end()
        return QIcon(pix)

    def _build_auto_next_icon(self, enabled: bool) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fg = self.palette().buttonText().color()
        dim = QColor(fg)
        dim.setAlpha(95)
        active = fg if enabled else dim
        arc_pen = QPen(active, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(2, 2, 16, 16, 30 * 16, 300 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(active)
        head = QPainterPath()
        head.moveTo(16.0, 8.0)
        head.lineTo(18.6, 5.3)
        head.lineTo(13.5, 7.0)
        head.closeSubpath()
        painter.drawPath(head)

        painter.setBrush(active)
        play_path = QPainterPath()
        play_path.moveTo(8, 6)
        play_path.lineTo(14, 10)
        play_path.lineTo(8, 14)
        play_path.closeSubpath()
        painter.drawPath(play_path)
        painter.end()
        return QIcon(pix)

    def _build_follow_icon(self, enabled: bool) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fg = self.palette().buttonText().color()
        dim = QColor(fg)
        dim.setAlpha(100)
        color = fg if enabled else dim
        pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(4, 4, 12, 12)
        painter.drawLine(10, 2, 10, 6)
        painter.drawLine(10, 14, 10, 18)
        painter.drawLine(2, 10, 6, 10)
        painter.drawLine(14, 10, 18, 10)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(8, 8, 4, 4)
        painter.end()
        return QIcon(pix)

    def _build_sun_icon(self) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#0f1115"), 1.8))
        center_x = 10
        center_y = 10
        for dx, dy in ((0, -7), (0, 7), (-7, 0), (7, 0), (-5, -5), (5, -5), (-5, 5), (5, 5)):
            painter.drawLine(center_x + int(dx * 0.75), center_y + int(dy * 0.75), center_x + dx, center_y + dy)
        painter.setPen(QPen(QColor("#0f1115"), 1.4))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(5, 5, 10, 10)
        painter.end()
        return QIcon(pix)

    def _build_moon_icon(self) -> QIcon:
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(3, 3, 14, 14)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(8, 2, 11, 15)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(3, 3, 14, 14)
        painter.end()
        return QIcon(pix)

    def _system_prefers_dark(self) -> bool:
        try:
            hints = self.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
        except Exception:  # noqa: BLE001
            pass

        window_color = self.palette().window().color()
        return window_color.lightness() < 128

    def _resolve_playhead_color(self, effective_theme: str, accent: QColor) -> QColor:
        custom_color = QColor(self._playhead_color)
        if custom_color.isValid():
            return custom_color
        if effective_theme == "light":
            return accent.darker(160)
        return accent.lighter(170)

    @staticmethod
    def _make_playhead_pen(color: QColor, width: float) -> QPen:
        pen = QPen(color)
        pen.setWidthF(max(1.0, min(float(width), 6.0)))
        pen.setCosmetic(True)
        return pen

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

        if select_first_new and first_new_row is not None:
            self.playlist.setCurrentRow(first_new_row)
        elif self.current_index is None and self.tracks:
            self.playlist.setCurrentRow(0)

        self._enqueue_preload(new_paths)
        return len(new_paths)

    def open_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
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
                self.player.play()
                self._autoplay_on_load = False
        except Exception as exc:  # noqa: BLE001
            self._autoplay_on_load = False
            QMessageBox.critical(
                self,
                "Kon bestand niet laden",
                f"Bestand kon niet geladen worden:\n{path}\n\nFout: {exc}",
            )

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

    @Slot()
    def _on_audio_outputs_changed(self) -> None:
        self._apply_audio_preferences(update_status=False)

    def toggle_play(self) -> None:
        if self.current_index is None and self.tracks:
            self._autoplay_on_load = True
            self.playlist.setCurrentRow(0)
            return

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return

        self.player.play()

    def on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText(self._txt("Pauze", "Pause"))
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_button.setText("Play")
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
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


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def main() -> None:
    _load_dotenv()
    QApplication.setApplicationName("Audio Player")
    QApplication.setApplicationDisplayName("Audio Player")
    if sys.argv:
        sys.argv[0] = "Audio Player"
    app = AudioPlayerApplication(sys.argv)
    app.setApplicationName("Audio Player")
    app.setApplicationDisplayName("Audio Player")
    pg.setConfigOptions(antialias=False)
    icon_path = _resource_path("assets", "app_icon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = WaveformPlayer()
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))

    def handle_external_file(path: str) -> None:
        window.add_files([path], select_first_new=True)

    app.fileOpenRequested.connect(handle_external_file)
    window.show()

    startup_candidates = list(sys.argv[1:]) + app.take_pending_file_opens()
    startup_files: list[str] = []
    seen: set[str] = set()
    for candidate in startup_candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        startup_files.append(candidate)

    if startup_files:
        window.add_files(startup_files, select_first_new=True)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
