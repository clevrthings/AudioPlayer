from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QListWidget


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
