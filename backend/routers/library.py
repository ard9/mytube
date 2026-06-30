"""
routers/library.py
------------------
Everything about browsing and serving the local video library:

  * listing the library and serving video bytes (with seek support)
  * thumbnails (served or generated with ffmpeg)
  * subtitles (served as WebVTT) and subtitle search
  * renaming / deleting a video (and carrying its notes + progress along)

The heavy lifting lives in the ``library`` service module; this file is only
the HTTP layer in front of it.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

import config
import dictionary
import library
import notes
import progress
from deps import range_stream, safe_path
from logging_setup import get_logger
from schemas import DeleteRequest, RenameRequest

log = get_logger("api.library")
router = APIRouter(prefix="/api", tags=["library"])


# ----- browse / serve ----------------------------------------------------- #
@router.get("/library")
def api_library():
    return library.scan_library()


@router.get("/video")
def api_video(path: str, request: Request):
    return range_stream(safe_path(path), request)


@router.get("/thumb")
def api_thumb(path: str):
    """Serve an existing thumbnail image (already a sibling of the video)."""
    p = safe_path(path)
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "image/jpeg")


@router.get("/genthumb")
def api_genthumb(path: str):
    """
    Generate a thumbnail for a *video* path using ffmpeg and cache it under
    `<library>/.thumbs/`. Returns 404 if ffmpeg isn't available so the frontend
    can fall back to grabbing a frame in the browser.
    """
    video = safe_path(path)
    root = config.get_library_path().resolve()
    cache_dir = root / ".thumbs"
    cache_dir.mkdir(exist_ok=True)

    # Stable cache filename from the relative path.
    key = hashlib.md5(path.encode("utf-8")).hexdigest()
    out = cache_dir / f"{key}.jpg"

    if not out.exists():
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(status_code=404, detail="ffmpeg not available")
        # Grab a frame ~20s in (or near the start for short clips).
        cmd = [
            ffmpeg, "-y", "-ss", "20", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", str(out),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("ffmpeg thumbnail failed for %s: %s", video.name, exc)
        if not out.exists():
            # try again from the very start (some clips are shorter than 20s)
            try:
                subprocess.run(
                    [ffmpeg, "-y", "-i", str(video), "-frames:v", "1",
                     "-vf", "scale=480:-1", "-q:v", "4", str(out)],
                    capture_output=True, timeout=30,
                )
            except Exception:  # noqa: BLE001
                pass
        if not out.exists():
            raise HTTPException(status_code=404, detail="Could not generate thumbnail")

    return FileResponse(out, media_type="image/jpeg")


@router.get("/subtitle")
def api_subtitle(path: str):
    """Serve a subtitle, converting .srt to WebVTT on the fly."""
    p = safe_path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".srt":
        text = "WEBVTT\n\n" + re.sub(
            r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text.replace("\r", "")
        )
    return HTMLResponse(content=text, media_type="text/vtt")


@router.get("/subtitle_search")
def api_subtitle_search(q: str):
    """Search inside every video's subtitle file for `q`, with timestamps."""
    return {"query": q, "results": library.search_subtitles(q)}


@router.get("/subtitle_search_in_video")
def api_subtitle_search_in_video(path: str, q: str):
    """
    Search for `q` inside ONE video's subtitle only — every matching cue,
    with its timestamp. Used for "where in this video was X said?" within
    the video currently being watched, as opposed to /api/subtitle_search
    which searches across the whole library.
    """
    try:
        matches = library.search_subtitle_in_video(path, q)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"path": path, "query": q, "matches": matches}


# ----- manage: rename / delete -------------------------------------------- #
@router.post("/rename")
def api_rename(req: RenameRequest):
    log.info("Rename %r -> %r", req.path, req.title)
    try:
        result = library.rename_video(req.path, req.title)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Video not found")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Carry over notes + watch progress to the new path.
    notes.rename_key(req.path, result["new_path"])
    progress.rename_key(req.path, result["new_path"])
    # Keep any dictionary entry's "jump to source" link pointing at the video.
    dictionary.rename_source(req.path, result["new_path"])
    return result


@router.post("/delete")
def api_delete(req: DeleteRequest):
    log.info("Delete %r", req.path)
    try:
        removed = library.delete_video(req.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Video not found")
    notes.delete_key(req.path)
    progress.delete_key(req.path)
    return {"removed": removed}
