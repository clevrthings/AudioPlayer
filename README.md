# Audio Player

A modern desktop audio player focused on fast navigation, clear waveform visualization, and flexible routing.

## Core Features

- Open one or many audio files into a playlist
- Drag and drop files onto the window or app icon (macOS)
- Progressive waveform loading with background preloading and caching
- Filled waveform rendering with a moving playhead
- Zoom in/out and instant **Fit** to show the full track
- Timeline in `mm:ss` format
- Track metadata panel (file name, format, duration, sample rate, channels, file size)
- Keyboard-first control support (play/pause, stop, track switching, delete selected track)

## Playback and Playlist

- Centered transport controls: previous, play/pause, next, stop
- Repeat modes: off, repeat one, repeat all
- Auto-continue toggle for advancing to the next track
- Optional playhead-follow mode while playing
- Reorder playlist with drag-and-drop
- Remove tracks via button or keyboard shortcut
- Sort playlist by name or duration

## Waveform

- Full-track overview on selection
- Seek by clicking or dragging on the waveform
- Configurable waveform display mode (combined waveform or separate channels)
- Adjustable waveform resolution in settings

## Appearance and UX

- Light, dark, and system theme modes
- Accent color customization
- Playhead color and thickness customization
- Compact, resizable layout optimized for wide/tall workflows
- About and Preferences in the macOS menu bar
- Preferences include an **Apply** button to apply changes without closing the window

## Feedback from the App

- Users can report bugs or request features directly from Preferences
- Reports are posted as GitHub Issues to `clevrthings/AudioPlayer`
- Supports guest submissions (no GitHub account required for end users)
- Posting uses a central token loaded from `.env`:
- `AUDIOPLAYER_GITHUB_TOKEN=...`

## Audio and Routing

- Output device selection
- Routing matrix for mapping input channels to output channels
- Support for layouts up to 7.1.4 (12 channels)
