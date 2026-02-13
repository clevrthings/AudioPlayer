# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AudioPlayer is a macOS desktop audio player built with PySide6 (Qt6) focused on waveform visualization, accurate navigation, and MIDI control. Current version: 0.1.0.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

# Build standalone macOS app (outputs to dist/)
.venv/bin/pyinstaller AudioPlayer.spec

# Build DMG for distribution (includes /Applications symlink for drag-to-install)
hdiutil create -volname "AudioPlayer" -fs HFS+ -srcfolder dist/AudioPlayer.app \
  -ov -format UDZO dist/AudioPlayer-<version>-mac.dmg
# Then add Applications symlink inside the DMG:
hdiutil attach dist/AudioPlayer-<version>-mac.dmg -readwrite -noverify
ln -s /Applications /Volumes/AudioPlayer/Applications
hdiutil detach /Volumes/AudioPlayer
hdiutil convert dist/AudioPlayer-<version>-mac.dmg -format UDZO \
  -o dist/AudioPlayer-<version>-mac.dmg -ov
```

There is no test suite, linter configuration, or CI pipeline.

GitHub releases are managed via `gh` CLI (repo: clevrthings/AudioPlayer).

## Architecture

The app uses an MVC-like pattern with a central hub (`WaveformPlayer` in `main_window.py`) delegating to specialized controllers.

**Entry flow:** `app.py` → `audioplayer/main.py` (creates QApplication + WaveformPlayer) → event loop

**Controller pattern:** Each controller in `audioplayer/controllers/` holds a `host` reference to the main window. Controllers use `__getattr__`/`__setattr__` to proxy attribute access to the host, keeping business logic in the controller while state lives on the main window.

Controllers:
- `PlaybackController` — play/pause, seek, position tracking, repeat modes
- `PlaylistController` — file loading, drag-drop, track ordering, switching
- `WaveformController` — waveform rendering coordination, zoom, display
- `MidiController` — MIDI device connection, note/CC mapping, learn mode
- `AudioRoutingController` — audio output device selection

**Waveform rendering** (`waveform.py`): `WaveformJob(QThread)` reads audio via `soundfile`, downsamples to configurable resolution, and emits progress signals back to the main thread. Results are numpy arrays displayed with `pyqtgraph`.

**Settings:** Persisted via `QSettings` (macOS plist at `~/Library/Preferences/RicoVanderhallen.AudioPlayer.plist`). Loaded on startup, saved on change.

**Theme system** (`ui/theme.py`): Generates QSS stylesheets dynamically, supports light/dark with customizable accent colors and system theme detection.

**Services:** `feedback_service.py` posts issue reports to a Cloudflare Workers endpoint. `update_service.py` checks GitHub releases API for new versions.

## Key Dependencies

- **PySide6** — Qt6 bindings (UI framework)
- **pyqtgraph** — waveform plot rendering
- **numpy** — audio data arrays
- **soundfile** — audio file I/O (WAV, FLAC, OGG, AIFF)
- **mido + python-rtmidi** — MIDI message handling and device I/O

## Notes

- `main_window.py` is ~1,500 lines and acts as the central state holder; all controllers read/write state through it
- The app targets macOS exclusively (DMG distribution, `.icns` icon, macOS-specific PyInstaller spec)
- Bilingual UI support: English and Dutch (NL)
- Audio formats supported: WAV, FLAC, OGG, AIFF, MP3, M4A, AAC, WMA
- The `.env.example` file documents optional environment variables (e.g., feedback endpoint URL)
