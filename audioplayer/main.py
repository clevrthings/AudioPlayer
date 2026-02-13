from __future__ import annotations

import sys
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from audioplayer.env_utils import load_dotenv
from audioplayer.main_window import WaveformPlayer
from audioplayer.widgets import AudioPlayerApplication


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def main() -> None:
    load_dotenv()
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
