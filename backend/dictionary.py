"""
dictionary.py
-------------
A personal language dictionary of words / sentences, stored in
`dictionary.json` in the project root (same place as notes.json / watch_state.json).

Each entry is a small record:

    {
      "id": "ab12cd34ef56",
      "text": "the sentence or word",
      "meaning": "your translation / explanation",
      "created": "2026-06-24T10:30:00",
      "source": {                       # null for manually-added entries
        "path": "Category/Channel/video.mp4",   # source video (relative path)
        "title": "Video title",                 # cached for display
        "start": 12.3,
        "end": 15.6
      },
      "media": {                        # any of these may be null
        "audio": "ab12cd34ef56.mp3",
        "image": "ab12cd34ef56.jpg",
        "video": "ab12cd34ef56.mp4"
      }
    }

The media files (audio/image/video clips cut from the source video with
ffmpeg) live in `dict_media/` next to dictionary.json. They are independent
of the source video on purpose: even if you later delete or rename the
original video, the clip you saved into your dictionary keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from config import ROOT_DIR, get_library_path

log = logging.getLogger("mytube.dictionary")

DICT_FILE = ROOT_DIR / "dictionary.json"
MEDIA_DIR = ROOT_DIR / "dict_media"
HISTORY_FILE = ROOT_DIR / "srs_history.json"   # {"YYYY-MM-DD": reviews_that_day}
_lock = threading.Lock()
_hist_lock = threading.Lock()

# --------------------------------------------------------------------------- #
# Spaced-repetition (SM-2 style) tuning
# --------------------------------------------------------------------------- #
# Ratings, as sent by the UI: 1 = Again, 2 = Hard, 3 = Good, 4 = Easy.
START_EASE = 2.5      # fresh ease factor
MIN_EASE = 1.3        # never let a card's ease drop below this
GRAD_GOOD_DAYS = 1    # a brand-new card you "Good" graduates to: review tomorrow
GRAD_EASY_DAYS = 4    # a brand-new card you "Easy" jumps further out
HARD_MULT = 1.2       # "Hard" grows the interval only slightly
EASY_BONUS = 1.3      # "Easy" grows the interval extra on top of ease
MATURE_DAYS = 21      # an interval this long counts the card as "mastered"
NEW_PER_DAY = 20      # how many brand-new cards a study session introduces/day

# A little padding around the subtitle cue so clips don't clip the first/last
# syllable, and a sane minimum length for very short cues.
PAD_BEFORE = 0.25
PAD_AFTER = 0.40
MIN_DURATION = 1.0
MAX_DURATION = 30.0     # guard against a bad end time producing a huge clip

VALID_MEDIA = ("audio", "image", "video")


# --------------------------------------------------------------------------- #
# Storage (JSON file, list of entries — newest first)
# --------------------------------------------------------------------------- #
def _read() -> list[dict]:
    if DICT_FILE.exists():
        try:
            data = json.loads(DICT_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read dictionary.json: %s", exc)
    return []


def _write(data: list[dict]) -> None:
    try:
        DICT_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.error("Could not write dictionary.json: %s", exc)


def get_all() -> list[dict]:
    """Return every dictionary entry, newest first (with SRS state ensured)."""
    with _lock:
        data = _read()
        changed = False
        for entry in data:
            if _ensure_srs(entry):
                changed = True
        if changed:           # migrate older files that predate the SRS fields
            _write(data)
        return data


# --------------------------------------------------------------------------- #
# Media capture (ffmpeg) — best-effort; the entry is still created without it
# --------------------------------------------------------------------------- #
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _clip_window(start: float, end: float) -> tuple[float, float]:
    """Return (seek_start, duration) padded and clamped to sane bounds."""
    start = max(0.0, float(start or 0.0))
    end = float(end) if end else start + 4.0
    if end <= start:
        end = start + 4.0
    seek = max(0.0, start - PAD_BEFORE)
    duration = (end - start) + PAD_BEFORE + PAD_AFTER
    duration = max(MIN_DURATION, min(MAX_DURATION, duration))
    return seek, duration


def _run_ffmpeg(args: list[str], timeout: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg clip failed: %s", exc)
        return False


def _capture_media(
    entry_id: str, video: Path, start: float, end: float, kinds: list[str]
) -> tuple[dict, list[str]]:
    """
    Cut the requested media (any of "audio"/"image"/"video") from `video`
    around [start, end]. Returns (media_map, failed_kinds).
    """
    MEDIA_DIR.mkdir(exist_ok=True)
    seek, duration = _clip_window(start, end)
    media: dict[str, str | None] = {"audio": None, "image": None, "video": None}
    failed: list[str] = []

    src = str(video)

    if "audio" in kinds:
        out = MEDIA_DIR / f"{entry_id}.mp3"
        ok = _run_ffmpeg(
            ["-ss", f"{seek}", "-i", src, "-t", f"{duration}",
             "-vn", "-ac", "2", "-ar", "44100", "-b:a", "128k", str(out)],
            timeout=60,
        )
        if ok and out.exists():
            media["audio"] = out.name
        else:
            failed.append("audio")

    if "image" in kinds:
        # A representative frame from the middle of the cue.
        mid = seek + duration / 2
        out = MEDIA_DIR / f"{entry_id}.jpg"
        ok = _run_ffmpeg(
            ["-ss", f"{mid}", "-i", src, "-frames:v", "1",
             "-vf", "scale=640:-1", "-q:v", "3", str(out)],
            timeout=30,
        )
        if ok and out.exists():
            media["image"] = out.name
        else:
            failed.append("image")

    if "video" in kinds:
        out = MEDIA_DIR / f"{entry_id}.mp4"
        ok = _run_ffmpeg(
            ["-ss", f"{seek}", "-i", src, "-t", f"{duration}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-movflags", "+faststart", str(out)],
            timeout=120,
        )
        if ok and out.exists():
            media["video"] = out.name
        else:
            failed.append("video")

    return media, failed


def _delete_media_files(media: dict | None) -> None:
    if not media:
        return
    for name in media.values():
        if not name:
            continue
        f = MEDIA_DIR / name
        try:
            if f.exists():
                f.unlink()
        except OSError as exc:  # noqa: BLE001
            log.warning("Could not delete media %s: %s", f, exc)


def media_path(filename: str) -> Path | None:
    """
    Resolve a media filename to its path inside dict_media, refusing any
    path that tries to escape the folder. Returns None if it doesn't exist.
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    p = (MEDIA_DIR / filename).resolve()
    if MEDIA_DIR.resolve() not in p.parents:
        return None
    return p if p.exists() else None


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def add_entry(
    text: str,
    meaning: str = "",
    *,
    path: str = "",
    title: str = "",
    start: float = 0.0,
    end: float = 0.0,
    capture: list[str] | None = None,
) -> dict:
    """
    Create a dictionary entry. If `path` points at a real video in the library
    and `capture` lists any of "audio"/"image"/"video", the corresponding clips
    are cut with ffmpeg. Manual entries simply pass no path / no capture.

    Returns the created entry, plus a transient "_warning" key listing any
    media that was requested but couldn't be produced (e.g. ffmpeg missing).
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Text cannot be empty")

    capture = [k for k in (capture or []) if k in VALID_MEDIA]
    entry_id = uuid.uuid4().hex[:12]

    source = None
    media = {"audio": None, "image": None, "video": None}
    warning = ""

    if path:
        root = get_library_path().resolve()
        video = (root / path).resolve()
        if root not in video.parents or not video.exists():
            raise FileNotFoundError(path)
        source = {
            "path": path,
            "title": title or video.stem,
            "start": round(float(start or 0.0), 2),
            "end": round(float(end or 0.0), 2),
        }
        if capture:
            if not ffmpeg_available():
                warning = "ffmpeg is not installed, so no media clip was saved."
            else:
                media, failed = _capture_media(entry_id, video, start, end, capture)
                if failed:
                    warning = "Could not capture: " + ", ".join(failed)

    entry = {
        "id": entry_id,
        "text": text,
        "meaning": (meaning or "").strip(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "media": media,
        "srs": _default_srs(),
    }

    with _lock:
        data = _read()
        data.insert(0, entry)        # newest first
        _write(data)

    result = dict(entry)
    if warning:
        result["_warning"] = warning
    return result


def update_entry(entry_id: str, text: str | None = None, meaning: str | None = None) -> dict:
    """Edit an entry's text and/or meaning. Media/source are left untouched."""
    with _lock:
        data = _read()
        for entry in data:
            if entry["id"] == entry_id:
                if text is not None:
                    t = text.strip()
                    if not t:
                        raise ValueError("Text cannot be empty")
                    entry["text"] = t
                if meaning is not None:
                    entry["meaning"] = meaning.strip()
                _write(data)
                return entry
        raise KeyError(entry_id)


def delete_entry(entry_id: str) -> bool:
    """Remove an entry and its captured media files."""
    with _lock:
        data = _read()
        keep = []
        removed = None
        for entry in data:
            if entry["id"] == entry_id:
                removed = entry
            else:
                keep.append(entry)
        if removed is None:
            return False
        _write(keep)
    _delete_media_files(removed.get("media"))
    return True


def rename_source(old_path: str, new_path: str) -> None:
    """
    Keep the 'jump to source video' link working after a video is renamed.
    (The saved clips are independent and don't need touching.)
    """
    with _lock:
        data = _read()
        changed = False
        for entry in data:
            src = entry.get("source")
            if src and src.get("path") == old_path:
                src["path"] = new_path
                changed = True
        if changed:
            _write(data)


# --------------------------------------------------------------------------- #
# Uploaded media (attach a file from the user's computer to any entry —
# used for manually-added words, or to add media to an existing entry later)
# --------------------------------------------------------------------------- #
DEFAULT_EXT = {"audio": ".mp3", "image": ".jpg", "video": ".mp4"}


def _safe_ext(filename: str, kind: str) -> str:
    """A short, safe file extension taken from the upload's name (e.g. '.png')."""
    ext = os.path.splitext(filename or "")[1].lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else DEFAULT_EXT[kind]


def attach_media(entry_id: str, kind: str, fileobj, filename: str = "") -> dict:
    """
    Store an uploaded file as the `kind` ("audio"/"image"/"video") media for an
    entry, replacing any existing media of that kind. Streams to disk so large
    videos don't have to fit in memory. Returns the updated entry.
    """
    if kind not in VALID_MEDIA:
        raise ValueError("Unknown media kind")
    MEDIA_DIR.mkdir(exist_ok=True)
    ext = _safe_ext(filename, kind)
    out_name = f"{entry_id}_{kind}{ext}"
    out = MEDIA_DIR / out_name

    with _lock:
        data = _read()
        entry = next((e for e in data if e["id"] == entry_id), None)
        if entry is None:
            raise KeyError(entry_id)

        # Remove a previous file for this kind first (it may have a different ext).
        prev = (entry.get("media") or {}).get(kind)
        if prev and prev != out_name:
            pf = MEDIA_DIR / prev
            try:
                if pf.exists():
                    pf.unlink()
            except OSError:
                pass

        try:
            with open(out, "wb") as dst:
                shutil.copyfileobj(fileobj, dst)
        except OSError as exc:
            raise ValueError(f"Could not save upload: {exc}")

        entry.setdefault("media", {"audio": None, "image": None, "video": None})
        entry["media"][kind] = out_name
        _write(data)
        return entry


def remove_media(entry_id: str, kind: str) -> dict:
    """Detach (and delete the file for) one kind of media from an entry."""
    if kind not in VALID_MEDIA:
        raise ValueError("Unknown media kind")
    with _lock:
        data = _read()
        entry = next((e for e in data if e["id"] == entry_id), None)
        if entry is None:
            raise KeyError(entry_id)
        name = (entry.get("media") or {}).get(kind)
        if name:
            f = MEDIA_DIR / name
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
            entry["media"][kind] = None
            _write(data)
        return entry


# --------------------------------------------------------------------------- #
# Spaced repetition — scheduling, review, study queue, and stats
# --------------------------------------------------------------------------- #
# Each entry carries an "srs" record:
#
#     "srs": {
#       "state": "new" | "learning" | "review",
#       "due":   "2026-06-25",   # date the card is next due (<= today = due now)
#       "interval": 0,           # days until next review (0 = same day)
#       "ease": 2.5,             # SM-2 ease factor
#       "reps": 0,               # successful reviews in a row
#       "lapses": 0,             # times forgotten after graduating
#       "last": null             # ISO datetime of the last review
#     }
#
# The scheduler is a faithful, compact SM-2: cards you keep getting wrong stay
# in the daily pile, while cards you know well fly out to weeks and months.

def _today() -> date:
    return date.today()


def _default_srs() -> dict:
    return {
        "state": "new",
        "due": _today().isoformat(),
        "interval": 0,
        "ease": START_EASE,
        "reps": 0,
        "lapses": 0,
        "last": None,
    }


def _ensure_srs(entry: dict) -> bool:
    """Make sure an entry has a complete srs record. Returns True if it changed."""
    s = entry.get("srs")
    if not isinstance(s, dict):
        entry["srs"] = _default_srs()
        return True
    base = _default_srs()
    changed = False
    for k, v in base.items():
        if k not in s:
            s[k] = v
            changed = True
    return changed


def _schedule(srs: dict, rating: int) -> dict:
    """Return a *new* srs record advanced by one review with the given rating."""
    today = _today()
    state = srs.get("state", "new")
    ease = float(srs.get("ease", START_EASE))
    interval = int(srs.get("interval", 0))
    reps = int(srs.get("reps", 0))
    lapses = int(srs.get("lapses", 0))

    if state in ("new", "learning"):
        if rating == 1:                      # Again — keep it in today's pile
            new_state, new_int = "learning", 0
            reps = 0
        elif rating == 2:                    # Hard — still today, try again soon
            new_state, new_int = "learning", 0
        elif rating == 3:                    # Good — graduate to spaced review
            new_state, new_int = "review", GRAD_GOOD_DAYS
            reps += 1
        else:                                # Easy — graduate further out
            new_state, new_int = "review", GRAD_EASY_DAYS
            reps += 1
    else:                                    # already a graduated "review" card
        if rating == 1:                      # Again — a lapse: relearn from today
            ease = max(MIN_EASE, ease - 0.20)
            lapses += 1
            reps = 0
            new_state, new_int = "learning", 0
        elif rating == 2:                    # Hard — small growth, ease dips
            ease = max(MIN_EASE, ease - 0.15)
            new_state = "review"
            new_int = max(1, round(max(interval, 1) * HARD_MULT))
            reps += 1
        elif rating == 3:                    # Good — standard SM-2 growth
            new_state = "review"
            new_int = max(1, round(max(interval, 1) * ease))
            reps += 1
        else:                                # Easy — extra growth, ease rises
            ease = ease + 0.15
            new_state = "review"
            new_int = max(1, round(max(interval, 1) * ease * EASY_BONUS))
            reps += 1

    due = today if new_int == 0 else today + timedelta(days=new_int)
    return {
        "state": new_state,
        "due": due.isoformat(),
        "interval": new_int,
        "ease": round(ease, 3),
        "reps": reps,
        "lapses": lapses,
        "last": datetime.now().isoformat(timespec="seconds"),
    }


def _fmt_interval(days: int) -> str:
    """A short, human label for 'when you'll see this next' (e.g. '3d', '2wk')."""
    if days <= 0:
        return "now"
    if days == 1:
        return "1d"
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{round(days / 7)}wk"
    if days < 365:
        return f"{round(days / 30)}mo"
    years = days / 365
    return f"{years:.1f}yr" if years < 10 else f"{round(years)}yr"


def _previews(srs: dict) -> dict:
    """The next interval each rating would produce, as labels — for the buttons."""
    out = {}
    for rating in (1, 2, 3, 4):
        nxt = _schedule(srs, rating)
        out[str(rating)] = _fmt_interval(nxt["interval"])
    return out


# ----- review history (for the streak + 'reviewed today') ------------------ #
def _read_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_history(data: dict) -> None:
    try:
        HISTORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write srs_history.json: %s", exc)


def _log_review() -> None:
    with _hist_lock:
        h = _read_history()
        key = _today().isoformat()
        h[key] = int(h.get(key, 0)) + 1
        _write_history(h)


def reviewed_today() -> int:
    return int(_read_history().get(_today().isoformat(), 0))


def streak() -> int:
    """Consecutive days (ending today or yesterday) with at least one review."""
    h = _read_history()
    day = _today()
    # Today counts if studied; if not yet studied today, the streak is still
    # alive from yesterday backwards (it only breaks once a whole day is missed).
    if int(h.get(day.isoformat(), 0)) <= 0:
        day -= timedelta(days=1)
    count = 0
    while int(h.get(day.isoformat(), 0)) > 0:
        count += 1
        day -= timedelta(days=1)
    return count


# ----- public SRS API ------------------------------------------------------ #
def review(entry_id: str, rating: int) -> dict:
    """Apply a review rating (1=Again 2=Hard 3=Good 4=Easy) and reschedule."""
    rating = int(rating)
    if rating not in (1, 2, 3, 4):
        raise ValueError("rating must be 1, 2, 3 or 4")
    with _lock:
        data = _read()
        for entry in data:
            if entry["id"] == entry_id:
                _ensure_srs(entry)
                entry["srs"] = _schedule(entry["srs"], rating)
                _write(data)
                _log_review()
                entry = dict(entry)
                entry["_previews"] = _previews(entry["srs"])
                return entry
        raise KeyError(entry_id)


def _is_due(srs: dict, today_iso: str) -> bool:
    return srs.get("due", today_iso) <= today_iso


def get_study(limit: int = 0, include_new: bool = True) -> list[dict]:
    """
    The cards to study right now, ordered: due learning cards, then due review
    cards (soonest first), then a capped trickle of brand-new cards. Each card
    is returned with a `_previews` map so the UI can label the rating buttons.
    """
    data = get_all()
    today_iso = _today().isoformat()

    learning, reviewing, new = [], [], []
    for e in data:
        s = e["srs"]
        st = s["state"]
        if st == "new":
            new.append(e)
        elif st == "learning" and _is_due(s, today_iso):
            learning.append(e)
        elif st == "review" and _is_due(s, today_iso):
            reviewing.append(e)

    reviewing.sort(key=lambda e: e["srs"]["due"])
    # Oldest-captured new cards first (data is newest-first, so reverse).
    new = list(reversed(new))[:NEW_PER_DAY] if include_new else []

    queue = learning + reviewing + new
    if limit and limit > 0:
        queue = queue[:limit]

    out = []
    for e in queue:
        item = dict(e)
        item["_previews"] = _previews(e["srs"])
        out.append(item)
    return out


def stats() -> dict:
    """A snapshot for the dictionary header and study button."""
    data = get_all()
    today_iso = _today().isoformat()

    total = len(data)
    new = learning = review_cnt = mastered = due_review = 0
    for e in data:
        s = e["srs"]
        st = s["state"]
        if st == "new":
            new += 1
        elif st == "learning":
            learning += 1
            if _is_due(s, today_iso):
                due_review += 1
        else:
            review_cnt += 1
            if int(s.get("interval", 0)) >= MATURE_DAYS:
                mastered += 1
            if _is_due(s, today_iso):
                due_review += 1

    new_available = min(new, NEW_PER_DAY)
    return {
        "total": total,
        "new": new,
        "learning": learning,
        "review": review_cnt,
        "mastered": mastered,
        "due": due_review + new_available,   # what "Start studying" will show
        "due_review": due_review,
        "new_available": new_available,
        "reviewed_today": reviewed_today(),
        "streak": streak(),
    }
