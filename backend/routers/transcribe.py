"""
routers/transcribe.py
---------------------
Offline subtitle generation with Whisper (faster-whisper). All the model work
runs in background jobs inside the ``transcribe`` service module; these routes
just start jobs and report their status.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import config
import transcribe
from logging_setup import get_logger
from schemas import TranscribeRequest

log = get_logger("api.transcribe")
router = APIRouter(prefix="/api/transcribe", tags=["transcribe"])


@router.get("/available")
def api_transcribe_available():
    return {
        "available": transcribe.is_available(),
        "models": list(transcribe.MODEL_SIZES),
        "default_model": transcribe.DEFAULT_MODEL,
    }


@router.post("")
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

    log.info("Start transcribe: %s (lang=%s, model=%s, translate=%s)",
             req.path, req.language or "auto", req.model or transcribe.DEFAULT_MODEL, req.translate)
    job = transcribe.start_job(
        req.path, req.language, req.model or transcribe.DEFAULT_MODEL,
        "translate" if req.translate else "transcribe", req.model_path,
    )
    return job


@router.get("/jobs")
def api_transcribe_jobs():
    return transcribe.list_jobs()


@router.get("/{job_id}")
def api_transcribe_status(job_id: str):
    job = transcribe.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel")
def api_transcribe_cancel(job_id: str):
    log.info("Cancel transcribe job %s", job_id)
    return {"cancelled": transcribe.cancel_job(job_id)}
