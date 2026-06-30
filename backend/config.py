"""
config.py
---------
Central configuration for the MyTube local server.

Everything that the user might want to change (where the video library lives,
which yt-dlp binary to use, etc.) is loaded from / saved to `config.json` in the
project root, so it can be edited from the UI without touching code.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("mytube.config")

# Project root = the directory that contains the `backend/` and `frontend/` dirs.
# This is always the *code* location (used to find the frontend).
ROOT_DIR = Path(__file__).resolve().parent.parent

# Where all writable state lives (config.json, conversation_data/, dictionary,
# tts_media/, notes, watch progress, ...). Defaults to the project root so a
# normal local install behaves exactly as before. In Docker we set
# MYTUBE_DATA_DIR=/data and mount a volume there, so the code stays in the image
# and the user's data survives container rebuilds.
DATA_DIR = Path(os.environ.get("MYTUBE_DATA_DIR") or ROOT_DIR).expanduser()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

CONFIG_FILE = DATA_DIR / "config.json"

# Deployment settings can also come from environment variables (handy for Docker,
# where you don't want to bake a config.json into the image). Env wins over the
# file when set. Maps ENV_NAME -> config key.
_ENV_OVERRIDES = {
    "MYTUBE_LIBRARY_PATH": "library_path",
    "MYTUBE_YTDLP_BIN": "ytdlp_bin",
    "MYTUBE_DEFAULT_QUALITY": "default_quality",
    "MYTUBE_TTS_OUTPUT_DIR": "tts_output_dir",
    "MYTUBE_HOST": "host",
    "MYTUBE_PORT": "port",
}

# Video / audio / subtitle extensions we recognise.
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv"}
# Audio-only files that live in the *same* library folder as the videos. They are
# browsed, played, transcribed, noted and added to the dictionary exactly like
# videos — the only practical difference is they have no picture, so the player
# shows an audio placeholder and no video frame can be captured from them.
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma"}
# Everything we treat as a playable "media" item in the library.
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
SUBTITLE_EXTS = {".srt", ".vtt"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Sensible defaults. The library path matches the example the user gave; change
# it from the Settings panel in the UI (it gets written back to config.json).
DEFAULTS = {
    "library_path": r"D:\English\youtube_english",
    "ytdlp_bin": "yt-dlp",
    "default_quality": "720",
    "tts_output_dir": "",          # "" = default (<project>/tts_media); else a folder of your choice
    "host": "127.0.0.1",
    "port": 8420,
}


def load_config() -> dict:
    """
    Read config.json, falling back to DEFAULTS for any missing key, then let
    environment variables (MYTUBE_*) override — so a container can be configured
    without editing or baking in a config.json.
    """
    data = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read config.json (%s); using defaults", exc)

    for env_name, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        if key == "port":
            try:
                data[key] = int(raw)
            except ValueError:
                log.warning("Ignoring non-integer %s=%r", env_name, raw)
        else:
            data[key] = raw
    return data


def save_config(updates: dict) -> dict:
    """Merge `updates` into the stored config and persist it."""
    data = load_config()
    data.update({k: v for k, v in updates.items() if v is not None})
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Failed to write config.json: %s", exc)
    return data


def get_library_path() -> Path:
    """Return the configured library directory as a Path (may not exist yet)."""
    return Path(load_config()["library_path"]).expanduser()


def get_tts_output_dir() -> Path | None:
    """
    The folder where generated speech is saved. Returns None when the user hasn't
    set one (callers then use the built-in default, <project>/tts_media).
    """
    raw = (load_config().get("tts_output_dir") or "").strip()
    return Path(raw).expanduser() if raw else None


def setup_logging() -> None:
    """
    Readable logging so the server is easy to debug from the console.

    Kept for backwards compatibility — the real setup now lives in
    ``logging_setup.configure()`` (request ids, per-area levels, optional file
    output). This just forwards to it.
    """
    import logging_setup
    logging_setup.configure()
