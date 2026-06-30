"""
routers/downloads.py
--------------------
Two closely related things:

  * Downloading videos with yt-dlp (``downloader`` service, runs in background
    jobs).
  * Tracking watch progress / "watched" flags per video (``progress`` service).

They share this file because the frontend's download/library screens use both.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import downloader
import progress
from logging_setup import get_logger
from schemas import DownloadRequest, ProgressUpdate, WatchedUpdate

log = get_logger("api.downloads")
router = APIRouter(prefix="/api", tags=["downloads"])


# ----- downloads ---------------------------------------------------------- #
@router.post("/download")
def api_download(req: DownloadRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    log.info("Start download: %s (quality=%s, category=%s)", req.url.strip(), req.quality, req.category or "-")
    return downloader.start_download(req.url.strip(), req.quality, req.category, req.subtitles)


@router.get("/downloads")
def api_downloads():
    return downloader.list_jobs()


@router.get("/downloads/{job_id}")
def api_download_status(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/downloads/{job_id}/cancel")
def api_download_cancel(job_id: str):
    log.info("Cancel download job %s", job_id)
    return {"cancelled": downloader.cancel_job(job_id)}


# ----- watch progress ----------------------------------------------------- #
@router.get("/watch")
def api_watch_all():
    return progress.get_all()


@router.put("/watch")
def api_watch_set(update: ProgressUpdate):
    return progress.set_progress(update.path, update.position, update.duration)


@router.put("/watch/flag")
def api_watch_flag(update: WatchedUpdate):
    return progress.set_watched(update.path, update.watched)
