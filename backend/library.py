"""
library.py
----------
Scans the library directory and groups videos by **category → channel**.

Folder-depth rules (relative to the library root):
  root/Category/Channel/video.mp4   -> category = Category, channel = Channel
  root/Channel/video.mp4            -> category = "Uncategorized", channel = Channel
  root/video.mp4                    -> category = "Uncategorized", channel = "Unsorted"

The channel name is overridden by `uploader` / `channel` from the sibling
`<name>.info.json` (written by yt-dlp) when present, so metadata wins over the
folder name — exactly as requested.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from config import (
    VIDEO_EXTS, AUDIO_EXTS, MEDIA_EXTS, SUBTITLE_EXTS, IMAGE_EXTS,
    get_library_path,
)

log = logging.getLogger("mytube.library")

DEFAULT_CATEGORY = "Uncategorized"


def _read_info_json(video: Path) -> dict:
    """Return the parsed `<stem>.info.json` next to a video, or {} if missing."""
    for cand in (video.with_name(video.stem + ".info.json"),):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Bad info.json %s: %s", cand, exc)
    return {}


def _find_sibling(video: Path, exts: set[str]) -> Optional[Path]:
    """Find a file with the same stem and one of `exts` next to the video."""
    for ext in exts:
        cand = video.with_name(video.stem + ext)
        if cand.exists():
            return cand
    return None


def _clean_title(name: str) -> str:
    """Turn a raw filename stem into a readable title (fallback when no info)."""
    title = re.sub(r"\s*[\[(][A-Za-z0-9_\-]{8,}[\])]\s*$", "", name)
    title = re.sub(r"[._]+", " ", title).strip()
    return title or name


def _rel(path: Path, root: Path) -> str:
    """POSIX-style path relative to the library root (used as an id / URL key)."""
    return path.relative_to(root).as_posix()


def _category_and_channel(rel_parts: tuple[str, ...]) -> tuple[str, str]:
    """Derive (category, channel) from a video's path parts relative to root."""
    depth = len(rel_parts)  # includes the filename
    if depth >= 3:
        return rel_parts[0], rel_parts[1]
    if depth == 2:
        return DEFAULT_CATEGORY, rel_parts[0]
    return DEFAULT_CATEGORY, "Unsorted"


def scan_library() -> dict:
    """
    Walk the library and return a nested structure:

        {
          "library_path": "...",
          "exists": bool,
          "total_videos": int,
          "categories": [
            {
              "name": "Music",
              "count": 12,
              "channels": [
                {"name": "Artist", "category": "Music", "count": 5, "videos": [ {video}, ... ]},
                ...
              ]
            },
            ...
          ]
        }
    """
    root = get_library_path()
    result = {
        "library_path": str(root),
        "exists": root.exists(),
        "total_videos": 0,
        "categories": [],
    }
    if not root.exists():
        log.info("Library path does not exist yet: %s", root)
        return result

    # (category, channel) -> list[video]
    groups: dict[tuple[str, str], list[dict]] = {}

    for video in sorted(root.rglob("*")):
        if not video.is_file() or video.suffix.lower() not in MEDIA_EXTS:
            continue
        # Skip our own thumbnail cache.
        if ".thumbs" in video.parts:
            continue

        is_audio = video.suffix.lower() in AUDIO_EXTS

        info = _read_info_json(video)
        rel_parts = video.relative_to(root).parts
        category, folder_channel = _category_and_channel(rel_parts)

        uploader = (
            info.get("uploader")
            or info.get("channel")
            or info.get("uploader_id")
            or folder_channel
        )

        # Audio files have no video frame to grab a thumbnail from, so don't
        # bother generating one (the frontend shows an audio icon instead). They
        # can still have a sibling cover image, which we'll happily use.
        thumb = _find_sibling(video, IMAGE_EXTS)
        subtitle = _find_sibling(video, SUBTITLE_EXTS)

        entry = {
            "id": _rel(video, root),
            "path": _rel(video, root),
            "filename": video.name,
            "title": info.get("title") or _clean_title(video.stem),
            "category": category,
            "uploader": uploader,
            "media_type": "audio" if is_audio else "video",
            "duration": info.get("duration"),
            "upload_date": info.get("upload_date"),
            "video_id": info.get("id"),
            "thumb": _rel(thumb, root) if thumb else None,
            "subtitle": _rel(subtitle, root) if subtitle else None,
            "has_info": bool(info),
        }
        groups.setdefault((category, uploader), []).append(entry)
        result["total_videos"] += 1

    # Build category -> channels -> videos.
    cats: dict[str, dict] = {}
    for (category, channel), vids in groups.items():
        vids.sort(key=lambda v: (v.get("upload_date") or "", v["title"]), reverse=True)
        cat = cats.setdefault(category, {"name": category, "count": 0, "channels": []})
        cat["count"] += len(vids)
        cat["channels"].append(
            {"name": channel, "category": category, "count": len(vids), "videos": vids}
        )

    for cat in cats.values():
        cat["channels"].sort(key=lambda c: c["name"].lower())

    # Sort categories alphabetically, but keep the default bucket last.
    ordered = sorted(cats.values(), key=lambda c: (c["name"] == DEFAULT_CATEGORY, c["name"].lower()))
    result["categories"] = ordered

    log.info(
        "Scanned %s videos across %s categories",
        result["total_videos"], len(result["categories"]),
    )
    return result


# --------------------------------------------------------------------------- #
# Manage (delete / rename)
# --------------------------------------------------------------------------- #
def _siblings(video: Path) -> list[Path]:
    """All files that belong to a video: itself + info.json + thumb + subtitle."""
    found = [video]
    for ext in (".info.json",):
        cand = video.with_name(video.stem + ext)
        if cand.exists():
            found.append(cand)
    for exts in (IMAGE_EXTS, SUBTITLE_EXTS):
        for ext in exts:
            cand = video.with_name(video.stem + ext)
            if cand.exists():
                found.append(cand)
    return found


def delete_video(rel_path: str) -> list[str]:
    """
    Delete a video file and its sibling files (thumbnail, subtitle, info.json).
    Returns the list of relative paths that were removed.
    """
    root = get_library_path().resolve()
    video = (root / rel_path).resolve()
    if root not in video.parents or not video.exists():
        raise FileNotFoundError(rel_path)

    removed = []
    for f in _siblings(video):
        try:
            f.unlink()
            removed.append(_rel(f, root))
        except OSError as exc:
            log.warning("Could not delete %s: %s", f, exc)
    log.info("Deleted video %s (%s files removed)", rel_path, len(removed))
    return removed


def rename_video(rel_path: str, new_title: str) -> dict:
    """
    Rename a video's filename stem (and its sibling files) to `new_title`,
    keeping each file's original extension. Returns the new relative path
    of the video file.
    """
    new_title = (new_title or "").strip()
    if not new_title:
        raise ValueError("New title cannot be empty")
    # Strip characters that are illegal in filenames on Windows/macOS/Linux.
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", new_title).strip().strip(".")
    if not safe_title:
        raise ValueError("New title is not a valid filename")

    root = get_library_path().resolve()
    video = (root / rel_path).resolve()
    if root not in video.parents or not video.exists():
        raise FileNotFoundError(rel_path)

    new_video = video.with_name(safe_title + video.suffix)
    if new_video.exists() and new_video != video:
        raise FileExistsError(f"A file named '{new_video.name}' already exists")

    renamed = {}
    for f in _siblings(video):
        target = f.with_name(safe_title + "".join(f.name[len(video.stem):]) if f.name.startswith(video.stem) else f.name)
        # The above keeps multi-part suffixes like ".info.json" intact.
        try:
            f.rename(target)
            renamed[_rel(f, root)] = _rel(target, root)
        except OSError as exc:
            log.warning("Could not rename %s -> %s: %s", f, target, exc)

    # Update the "title" field inside info.json (if any) so the new title is
    # actually reflected in the library, not just the filename — otherwise
    # the scanner would keep showing the *old* title (metadata wins over the
    # filename in scan_library()).
    new_info = new_video.with_name(safe_title + ".info.json")
    if new_info.exists():
        try:
            info_data = json.loads(new_info.read_text(encoding="utf-8"))
            info_data["title"] = new_title
            new_info.write_text(json.dumps(info_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not update title in %s: %s", new_info, exc)

    log.info("Renamed video %s -> %s", rel_path, renamed.get(_rel(video, root)))
    return {
        "old_path": rel_path,
        "new_path": renamed.get(_rel(video, root), rel_path),
        "renamed": renamed,
    }


# --------------------------------------------------------------------------- #
# Subtitle search
# --------------------------------------------------------------------------- #
_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def _parse_subtitle_cues(text: str) -> list[tuple[float, float, str]]:
    """
    Parse .srt/.vtt content into a list of (start_seconds, end_seconds, cue_text)
    tuples. Cue text has cue-internal newlines collapsed to spaces.
    """
    cues: list[tuple[float, float, str]] = []
    lines = text.replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        m = _TIMESTAMP_RE.search(lines[i])
        if m:
            start = (
                int(m.group(1)) * 3600 + int(m.group(2)) * 60
                + int(m.group(3)) + int(m.group(4)) / 1000
            )
            end = (
                int(m.group(5)) * 3600 + int(m.group(6)) * 60
                + int(m.group(7)) + int(m.group(8)) / 1000
            )
            i += 1
            body = []
            while i < len(lines) and lines[i].strip() and not _TIMESTAMP_RE.search(lines[i]):
                # Strip basic VTT/SRT formatting tags like <i>, <b>, <c.x>.
                body.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
                i += 1
            cue_text = " ".join(b for b in body if b)
            if cue_text:
                cues.append((start, end, cue_text))
        else:
            i += 1
    return cues


def search_subtitles(query: str, limit_per_video: int = 3) -> list[dict]:
    """
    Search every subtitle file in the library for `query` (case-insensitive).
    Returns a list of {video_path, matches:[{time, text}]} for videos with
    at least one match. `time` is the cue start time in seconds, usable to
    jump the player to that moment.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    root = get_library_path()
    if not root.exists():
        return []

    results = []
    for video in sorted(root.rglob("*")):
        if not video.is_file() or video.suffix.lower() not in MEDIA_EXTS:
            continue
        if ".thumbs" in video.parts:
            continue
        sub = _find_sibling(video, SUBTITLE_EXTS)
        if not sub:
            continue
        try:
            text = sub.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cues = _parse_subtitle_cues(text)
        matches = [
            {"time": start, "end": end, "text": cue_text}
            for start, end, cue_text in cues
            if q in cue_text.lower()
        ][:limit_per_video]
        if matches:
            results.append({"video_path": _rel(video, root), "matches": matches})
    return results


def search_subtitle_in_video(rel_path: str, query: str) -> list[dict]:
    """
    Search for `query` inside a single video's subtitle file only.
    Returns every matching cue (no limit, unlike the cross-library search),
    since the point here is "show me every time this was said in this
    video", not a short preview. Each match is {time, text}.
    Raises FileNotFoundError if the video doesn't exist, and returns an
    empty list (not an error) if the video has no subtitle file at all.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    root = get_library_path().resolve()
    video = (root / rel_path).resolve()
    if root not in video.parents or not video.exists():
        raise FileNotFoundError(rel_path)

    sub = _find_sibling(video, SUBTITLE_EXTS)
    if not sub:
        return []

    try:
        text = sub.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    cues = _parse_subtitle_cues(text)
    return [
        {"time": start, "end": end, "text": cue_text}
        for start, end, cue_text in cues
        if q in cue_text.lower()
    ]
