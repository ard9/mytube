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
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config.json"

# Video / subtitle extensions we recognise.
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv"}
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
    """Read config.json, falling back to DEFAULTS for any missing key."""
    data = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read config.json (%s); using defaults", exc)
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
    """Readable logging so the server is easy to debug from the console."""
    logging.basicConfig(
        level=os.environ.get("MYTUBE_LOGLEVEL", "INFO"),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
