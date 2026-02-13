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

APP_VERSION = "0.1.0"
RELEASE_LATEST_API_URL = "https://api.github.com/repos/clevrthings/AudioPlayer/releases/latest"
RELEASES_LATEST_PAGE_URL = "https://github.com/clevrthings/AudioPlayer/releases/latest"

FEEDBACK_WORKER_ENV_URL = "AUDIOPLAYER_FEEDBACK_WORKER_URL"
FEEDBACK_WORKER_ENV_KEY = "AUDIOPLAYER_FEEDBACK_WORKER_KEY"
FEEDBACK_WORKER_DEFAULT_URL = "https://audioplayer-issue-poster.clevrthings.workers.dev/report"

MIDI_ACTION_IDS = (
    "previous_track",
    "toggle_play",
    "play",
    "pause",
    "next_track",
    "stop",
    "repeat_mode",
    "auto_next_toggle",
)
