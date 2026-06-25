"""
main.py
-------
FastAPI application: serves the frontend, exposes the library/notes/download
API, and streams video files with HTTP range support (needed for seeking).

Run with:  python backend/main.py      (or use the start scripts)
"""

from __future__ import annotations

import os

# Must be set before any numerical library (numpy/torch/ctranslate2, pulled
# in transitively once transcribe.py's faster-whisper feature is used) gets
# imported anywhere in this process. See the longer explanation in
# transcribe.py — this is a second, earlier place to set it since main.py
# is the actual process entry point. Setting it twice is harmless.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import dictionary
import downloader
import library
import notes
import progress
import transcribe

config.setup_logging()
log = logging.getLogger("mytube.main")

FRONTEND_DIR = config.ROOT_DIR / "frontend"

app = FastAPI(title="MyTube Local")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def safe_path(rel_path: str) -> Path:
    """
    Resolve a client-supplied relative path against the library root and refuse
    anything that escapes it (path-traversal guard).
    """
    root = config.get_library_path().resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=403, detail="Path outside library")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def range_stream(path: Path, request: Request) -> StreamingResponse | FileResponse:
    """Serve a file honouring the Range header so the <video> can seek."""
    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(path, media_type=media_type)

    # Parse "bytes=start-end"
    try:
        units, _, rng = range_header.partition("=")
        start_s, _, end_s = rng.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad Range header")

    start = max(0, start)
    end = min(end, file_size - 1)
    length = end - start + 1

    def iterator(chunk: int = 1024 * 512):
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(iterator(), status_code=206, media_type=media_type, headers=headers)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ConfigUpdate(BaseModel):
    library_path: str | None = None
    ytdlp_bin: str | None = None
    default_quality: str | None = None


class NoteUpdate(BaseModel):
    path: str
    text: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "720"
    category: str = ""
    subtitles: str = ""


class ProgressUpdate(BaseModel):
    path: str
    position: float
    duration: float = 0.0


class WatchedUpdate(BaseModel):
    path: str
    watched: bool


class RenameRequest(BaseModel):
    path: str
    title: str


class DeleteRequest(BaseModel):
    path: str


class DictionaryCreate(BaseModel):
    text: str
    meaning: str = ""
    path: str = ""           # source video relative path ("" = manual entry)
    title: str = ""          # cached source title for display
    start: float = 0.0
    end: float = 0.0
    capture: list[str] = []  # any of "audio", "image", "video"


class DictionaryUpdate(BaseModel):
    text: str | None = None
    meaning: str | None = None


class DictionaryReview(BaseModel):
    rating: int        # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy


class TranscribeRequest(BaseModel):
    path: str
    language: str = ""      # "" = auto-detect; else a language code like "fa", "en", "es"
    model: str = ""         # "" = use default size
    translate: bool = False  # True = translate speech to English subtitles
    model_path: str = ""    # "" = auto-download by name; else a local folder with a pre-downloaded model


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@app.get("/api/config")
def api_get_config():
    return config.load_config()


@app.post("/api/config")
def api_set_config(update: ConfigUpdate):
    return config.save_config(update.model_dump(exclude_none=True))


# --------------------------------------------------------------------------- #
# Library
# --------------------------------------------------------------------------- #
@app.get("/api/library")
def api_library():
    return library.scan_library()


@app.get("/api/video")
def api_video(path: str, request: Request):
    return range_stream(safe_path(path), request)


@app.get("/api/thumb")
def api_thumb(path: str):
    """Serve an existing thumbnail image (already a sibling of the video)."""
    p = safe_path(path)
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "image/jpeg")


@app.get("/api/genthumb")
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
    import hashlib
    key = hashlib.md5(path.encode("utf-8")).hexdigest()
    out = cache_dir / f"{key}.jpg"

    if not out.exists():
        import shutil
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(status_code=404, detail="ffmpeg not available")
        import subprocess
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


@app.get("/api/subtitle")
def api_subtitle(path: str):
    """Serve a subtitle, converting .srt to WebVTT on the fly."""
    p = safe_path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".srt":
        import re
        text = "WEBVTT\n\n" + re.sub(
            r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text.replace("\r", "")
        )
    return HTMLResponse(content=text, media_type="text/vtt")


@app.get("/api/subtitle_search")
def api_subtitle_search(q: str):
    """Search inside every video's subtitle file for `q`, with timestamps."""
    return {"query": q, "results": library.search_subtitles(q)}


@app.get("/api/subtitle_search_in_video")
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


# --------------------------------------------------------------------------- #
# Manage: rename / delete
# --------------------------------------------------------------------------- #
@app.post("/api/rename")
def api_rename(req: RenameRequest):
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


@app.post("/api/delete")
def api_delete(req: DeleteRequest):
    try:
        removed = library.delete_video(req.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Video not found")
    notes.delete_key(req.path)
    progress.delete_key(req.path)
    return {"removed": removed}


# --------------------------------------------------------------------------- #
# Local subtitle generation (Whisper, offline, any spoken language)
# --------------------------------------------------------------------------- #
@app.get("/api/transcribe/available")
def api_transcribe_available():
    return {
        "available": transcribe.is_available(),
        "models": list(transcribe.MODEL_SIZES),
        "default_model": transcribe.DEFAULT_MODEL,
    }


@app.post("/api/transcribe")
def api_transcribe_start(req: TranscribeRequest):
    if not transcribe.is_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper is not installed on the server. "
                   "Run 'pip install faster-whisper' and restart MyTube.",
        )
    root = config.get_library_path().resolve()
    target = (root / req.path).resolve()
    if root not in target.parents or not target.exists():
        raise HTTPException(status_code=404, detail="Video not found")

    job = transcribe.start_job(
        req.path, req.language, req.model or transcribe.DEFAULT_MODEL,
        "translate" if req.translate else "transcribe", req.model_path,
    )
    return job


@app.get("/api/transcribe/jobs")
def api_transcribe_jobs():
    return transcribe.list_jobs()


@app.get("/api/transcribe/{job_id}")
def api_transcribe_status(job_id: str):
    job = transcribe.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/transcribe/{job_id}/cancel")
def api_transcribe_cancel(job_id: str):
    return {"cancelled": transcribe.cancel_job(job_id)}


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #
@app.get("/api/notes")
def api_notes_all():
    return notes.get_all()


@app.put("/api/notes")
def api_note_set(update: NoteUpdate):
    notes.set_note(update.path, update.text)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Dictionary (words / sentences, with optional audio/image/video clips)
# --------------------------------------------------------------------------- #
@app.get("/api/dictionary")
def api_dictionary_all():
    return {
        "entries": dictionary.get_all(),
        "ffmpeg": dictionary.ffmpeg_available(),
        "stats": dictionary.stats(),
    }


@app.get("/api/dictionary/stats")
def api_dictionary_stats():
    return dictionary.stats()


@app.get("/api/dictionary/study")
def api_dictionary_study(limit: int = 0, new: bool = True):
    """The cards due for review right now, plus a fresh stats snapshot."""
    return {
        "cards": dictionary.get_study(limit=limit, include_new=new),
        "stats": dictionary.stats(),
    }


@app.post("/api/dictionary/{entry_id}/review")
def api_dictionary_review(entry_id: str, req: DictionaryReview):
    try:
        entry = dictionary.review(entry_id, req.rating)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"entry": entry, "stats": dictionary.stats()}


@app.post("/api/dictionary")
def api_dictionary_add(req: DictionaryCreate):
    try:
        return dictionary.add_entry(
            req.text, req.meaning,
            path=req.path, title=req.title,
            start=req.start, end=req.end, capture=req.capture,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Source video not found")


@app.put("/api/dictionary/{entry_id}")
def api_dictionary_update(entry_id: str, req: DictionaryUpdate):
    try:
        return dictionary.update_entry(entry_id, req.text, req.meaning)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/dictionary/{entry_id}")
def api_dictionary_delete(entry_id: str):
    return {"deleted": dictionary.delete_entry(entry_id)}


@app.get("/api/dictionary/media")
def api_dictionary_media(file: str):
    p = dictionary.media_path(file)
    if not p:
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "application/octet-stream")


@app.post("/api/dictionary/{entry_id}/media")
def api_dictionary_attach_media(
    entry_id: str, kind: str = Form(...), file: UploadFile = File(...)
):
    """Attach an uploaded audio/image/video file to an entry (replaces any existing one)."""
    try:
        return dictionary.attach_media(entry_id, kind, file.file, file.filename or "")
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/dictionary/{entry_id}/media/{kind}")
def api_dictionary_remove_media(entry_id: str, kind: str):
    try:
        return dictionary.remove_media(entry_id, kind)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
@app.post("/api/download")
def api_download(req: DownloadRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    return downloader.start_download(req.url.strip(), req.quality, req.category, req.subtitles)


# --------------------------------------------------------------------------- #
# Watch progress
# --------------------------------------------------------------------------- #
@app.get("/api/watch")
def api_watch_all():
    return progress.get_all()


@app.put("/api/watch")
def api_watch_set(update: ProgressUpdate):
    return progress.set_progress(update.path, update.position, update.duration)


@app.put("/api/watch/flag")
def api_watch_flag(update: WatchedUpdate):
    return progress.set_watched(update.path, update.watched)


@app.get("/api/downloads")
def api_downloads():
    return downloader.list_jobs()


@app.get("/api/downloads/{job_id}")
def api_download_status(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/downloads/{job_id}/cancel")
def api_download_cancel(job_id: str):
    return {"cancelled": downloader.cancel_job(job_id)}


# --------------------------------------------------------------------------- #
# Frontend (mounted last so /api/* wins)
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    cfg = config.load_config()
    log.info("Library: %s", cfg["library_path"])
    log.info("Open http://%s:%s in your browser", cfg["host"], cfg["port"])
    uvicorn.run(app, host=cfg["host"], port=int(cfg["port"]))
