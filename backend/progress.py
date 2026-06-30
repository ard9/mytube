"""
progress.py
-----------
Tracks playback progress per video so the UI can:
  - resume where you left off,
  - draw a red "watched" bar on thumbnails (YouTube-style),
  - mark videos as fully watched.

Stored in `watch_state.json` in the project root, keyed by the video's
relative path:

    { "<path>": {"position": 123.4, "duration": 600.0, "watched": false,
                 "updated": "2024-..." } }
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

from config import DATA_DIR

log = logging.getLogger("mytube.progress")

PROGRESS_FILE = DATA_DIR / "watch_state.json"
WATCHED_RATIO = 0.9          # ≥90% counts as "watched"
_lock = threading.Lock()


def _read() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read watch_state.json: %s", exc)
    return {}


def _write(data: dict) -> None:
    try:
        PROGRESS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Could not write watch_state.json: %s", exc)


def get_all() -> dict:
    with _lock:
        return _read()


def set_progress(path: str, position: float, duration: float) -> dict:
    """Update playback position; auto-mark watched at ≥90%. Watched is sticky."""
    with _lock:
        data = _read()
        prev = data.get(path, {})
        watched = bool(prev.get("watched"))
        if duration and position >= duration * WATCHED_RATIO:
            watched = True
        data[path] = {
            "position": round(float(position), 1),
            "duration": round(float(duration), 1) if duration else prev.get("duration", 0),
            "watched": watched,
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        _write(data)
        return data[path]


def set_watched(path: str, watched: bool) -> dict:
    """Manually toggle the watched flag for a video."""
    with _lock:
        data = _read()
        entry = data.get(path, {"position": 0, "duration": 0})
        entry["watched"] = bool(watched)
        if not watched:
            entry["position"] = 0
        entry["updated"] = datetime.now().isoformat(timespec="seconds")
        data[path] = entry
        _write(data)
        return entry


def delete_key(path: str) -> None:
    """Remove any watch state stored for `path` (used when a video is deleted)."""
    with _lock:
        data = _read()
        if data.pop(path, None) is not None:
            _write(data)


def rename_key(old_path: str, new_path: str) -> None:
    """Move watch state from `old_path` to `new_path` (used on rename)."""
    with _lock:
        data = _read()
        if old_path in data:
            data[new_path] = data.pop(old_path)
            _write(data)
