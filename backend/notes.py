"""
notes.py
--------
Per-video notes, stored in `notes.json` in the project root and keyed by the
video's relative path. Simple, file-backed, and easy to inspect/back up.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger("mytube.notes")

NOTES_FILE = DATA_DIR / "notes.json"
_lock = threading.Lock()


def _read() -> dict:
    if NOTES_FILE.exists():
        try:
            return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read notes.json: %s", exc)
    return {}


def _write(data: dict) -> None:
    try:
        NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.error("Could not write notes.json: %s", exc)


def get_all() -> dict:
    """Return the full {path: note} mapping."""
    with _lock:
        return _read()


def get_note(path: str) -> str:
    with _lock:
        return _read().get(path, "")


def set_note(path: str, text: str) -> None:
    """Save (or clear, if text is empty) the note for a video path."""
    with _lock:
        data = _read()
        if text and text.strip():
            data[path] = text
        else:
            data.pop(path, None)
        _write(data)


def delete_key(path: str) -> None:
    """Remove any note stored for `path` (used when a video is deleted)."""
    with _lock:
        data = _read()
        if data.pop(path, None) is not None:
            _write(data)


def rename_key(old_path: str, new_path: str) -> None:
    """Move a note from `old_path` to `new_path` (used when a video is renamed)."""
    with _lock:
        data = _read()
        if old_path in data:
            data[new_path] = data.pop(old_path)
            _write(data)
