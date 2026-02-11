# Audio Player (macOS-ready)

Een strakke desktop audio player in Python met:

- Meerdere audiofiles openen in een playlist
- Gevulde waveform met live playhead
- Asynchroon/progressief waveform laden (zonder UI-freeze)
- Achtergrond preloading + caching van andere tracks
- Bestanden openen via drag-and-drop op venster of app-icoon (macOS)
- Seek via playhead (klik/sleep)
- Zoom `+` / `-` rechtsonder onder de waveform
- Volledige file direct passend in beeld bij selectie
- Tijdas onderaan in minuten/seconden (`mm:ss`)
- Bestandsinfo met nette tekst-wrap (ook bij lange namen)
- Transport controls gecentreerd bovenaan
- Spatiebalk voor play/pauze
- Thema-knop rechtsboven met Licht/Donker/Systeem
- Compact, lang venster (resizable)

## 1. Installatie

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Starten

```bash
python app.py
```

## 3. Standalone mac app build

Voor een `.app` bundle kun je PyInstaller gebruiken:

```bash
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name AudioPlayer \
  --icon assets/AudioPlayer.icns \
  --add-data "assets/app_icon.png:assets" \
  app.py
```

Output staat in:

- `dist/AudioPlayer.app`

## Opmerking over audio-formaten

- Waveform + metadata worden uitgelezen via `soundfile` (libsndfile).
- Playback gaat via Qt Multimedia.
- Op macOS werken WAV/AIFF/FLAC meestal direct; voor sommige gecomprimeerde formaten kan codec-support afhangen van je systeem.
