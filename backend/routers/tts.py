"""
routers/tts.py
--------------
Text-to-speech with StyleTTS2 (offline). Covers generating speech, the saved
clip library, saving clips into the Word bank (dictionary), reference voices for
cloning, and serving the audio.

Route order matters: the ``/{job_id}`` routes are registered LAST so the literal
paths above them (``/jobs``, ``/library``, ``/media``, ``/voices``) are not
swallowed by the ``{job_id}`` placeholder.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import dictionary
import tts
from logging_setup import get_logger
from schemas import TTSRequest, TTSSegmentToDict, TTSToDictRequest

log = get_logger("api.tts")
router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/available")
def api_tts_available():
    return {
        "available": tts.is_available(),
        "ffmpeg": tts.ffmpeg_available(),
        "max_chars": tts.MAX_TEXT_CHARS,
    }


@router.post("")
def api_tts_start(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")
    if not tts.is_available():
        raise HTTPException(
            status_code=503,
            detail="StyleTTS2 is not installed on the server. "
                   "Run 'pip install styletts2' and restart Echo.",
        )
    opts = {
        "diffusion_steps": req.diffusion_steps,
        "embedding_scale": req.embedding_scale,
        "alpha": req.alpha,
        "beta": req.beta,
    }
    log.info("Start TTS: %d chars, voice=%s, steps=%d",
             len(req.text), req.voice_id or "default", req.diffusion_steps)
    return tts.start_job(req.text, req.title, req.voice_id, opts)


@router.get("/jobs")
def api_tts_jobs():
    return tts.list_jobs()


@router.get("/library")
def api_tts_library():
    return {"entries": tts.get_library(), "ffmpeg": tts.ffmpeg_available()}


@router.delete("/library/{entry_id}")
def api_tts_delete(entry_id: str):
    log.info("Delete TTS clip %s", entry_id)
    return {"deleted": tts.delete_entry(entry_id)}


@router.post("/library/{entry_id}/to_dictionary")
def api_tts_to_dictionary(entry_id: str, req: TTSToDictRequest):
    """Create a Word bank flashcard from a generated audio, with the audio attached."""
    entry = tts.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audio not found")
    media = tts.entry_file_path(entry)
    if not media:
        raise HTTPException(status_code=404, detail="Audio file missing on disk")

    created = dictionary.add_entry(entry["text"], req.meaning)
    try:
        with open(media, "rb") as f:
            updated = dictionary.attach_media(created["id"], "audio", f, media.name)
        return updated
    except Exception as exc:  # noqa: BLE001
        # The text entry still exists even if attaching the clip failed.
        log.warning("to_dictionary: attaching audio for %s failed: %s", entry_id, exc)
        return {**created, "_warning": f"Saved the text, but the audio couldn't be attached: {exc}"}


@router.post("/library/{entry_id}/segment_to_dictionary")
def api_tts_segment_to_dict(entry_id: str, req: TTSSegmentToDict):
    """Cut one sentence-range out of a generated clip and save it as a Word bank card."""
    entry = tts.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audio not found")
    segs = entry.get("segments") or []
    if not segs:
        raise HTTPException(status_code=400, detail="This clip has no timed segments")

    fi = max(0, min(req.from_index, len(segs) - 1))
    ti = max(fi, min(req.to_index, len(segs) - 1))
    start = segs[fi]["start"]
    end = segs[ti]["end"]
    text = " ".join(s["text"] for s in segs[fi:ti + 1]).strip()

    created = dictionary.add_entry(text, req.meaning)
    clip = tts.cut_clip(entry, start, end)
    if not clip:
        log.warning("segment_to_dictionary: could not cut audio for entry %s (%.2f-%.2f)", entry_id, start, end)
        return {**created, "_warning": "Saved the text, but couldn't cut the audio clip. Check that ffmpeg is installed and see the server console for details."}
    try:
        with open(clip, "rb") as f:
            updated = dictionary.attach_media(created["id"], "audio", f, f"segment{clip.suffix}")
        return updated
    except Exception as exc:  # noqa: BLE001
        log.warning("segment_to_dictionary: attaching clip for %s failed: %s", entry_id, exc)
        return {**created, "_warning": f"Saved the text, but the audio clip couldn't be attached: {exc}"}
    finally:
        try:
            clip.unlink()
        except OSError:
            pass


@router.get("/media")
def api_tts_media(id: str):
    p = tts.media_path(id)
    if not p:
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "audio/mpeg")


# ----- saved reference voices (for cloning) ----- #
@router.get("/voices")
def api_tts_voices():
    return {"voices": tts.get_voices()}


@router.post("/voices")
def api_tts_add_voice(name: str = Form(""), file: UploadFile = File(...)):
    log.info("Add reference voice: %s (%s)", name or "(unnamed)", file.filename)
    try:
        return tts.add_voice(name, file.file, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/voices/{voice_id}")
def api_tts_delete_voice(voice_id: str):
    log.info("Delete reference voice %s", voice_id)
    return {"deleted": tts.delete_voice(voice_id)}


# These {job_id} routes come last so the static paths above (jobs, library,
# media, voices) aren't swallowed by the {job_id} placeholder.
@router.get("/{job_id}")
def api_tts_status(job_id: str):
    job = tts.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel")
def api_tts_cancel(job_id: str):
    log.info("Cancel TTS job %s", job_id)
    return {"cancelled": tts.cancel_job(job_id)}
