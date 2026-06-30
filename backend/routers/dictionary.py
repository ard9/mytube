"""
routers/dictionary.py
---------------------
The "Word bank": saved words/sentences with spaced-repetition review and
optional audio/image/video clips attached. The SRS scheduling and storage live
in the ``dictionary`` service module.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import dictionary
import tts
from logging_setup import get_logger
from schemas import (
    DictionaryCreate, DictionaryReview, DictionaryUpdate, DictionaryWithAudio,
)

log = get_logger("api.dictionary")
router = APIRouter(prefix="/api/dictionary", tags=["dictionary"])


@router.get("")
def api_dictionary_all():
    return {
        "entries": dictionary.get_all(),
        "ffmpeg": dictionary.ffmpeg_available(),
        "stats": dictionary.stats(),
    }


@router.post("/with_audio")
def api_dictionary_with_audio(req: DictionaryWithAudio):
    """
    Create a Word-bank card from text and, when asked, synthesise its audio with
    a TTS engine and attach it. Powers the live-conversation "save this
    correction" button: the card ends up holding the corrected sentence and a
    clip that speaks it. If the audio step fails (e.g. gTTS offline, no engine
    installed) the text card is still created and a warning is returned.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    created = dictionary.add_entry(text, req.meaning)
    if not req.with_audio:
        return created

    try:
        clip = tts.synthesize_clip(text, req.engine, {
            "lang": req.lang, "tld": req.tld,
            "voice_id": req.voice_id, "slow": req.slow,
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("with_audio: synthesis failed for entry %s: %s", created["id"], exc)
        return {**created, "_warning": f"Saved the text, but couldn't make the audio: {exc}"}

    try:
        with open(clip, "rb") as f:
            updated = dictionary.attach_media(created["id"], "audio", f, clip.name)
        log.info("with_audio: created card %s with %s audio", created["id"], tts.resolve_engine(req.engine))
        return updated
    except Exception as exc:  # noqa: BLE001
        log.warning("with_audio: attaching audio for %s failed: %s", created["id"], exc)
        return {**created, "_warning": f"Saved the text, but the audio couldn't be attached: {exc}"}
    finally:
        try:
            clip.unlink()
        except OSError:
            pass


@router.get("/stats")
def api_dictionary_stats():
    return dictionary.stats()


@router.get("/study")
def api_dictionary_study(limit: int = 0, new: bool = True):
    """The cards due for review right now, plus a fresh stats snapshot."""
    return {
        "cards": dictionary.get_study(limit=limit, include_new=new),
        "stats": dictionary.stats(),
    }


@router.post("/{entry_id}/review")
def api_dictionary_review(entry_id: str, req: DictionaryReview):
    try:
        entry = dictionary.review(entry_id, req.rating)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"entry": entry, "stats": dictionary.stats()}


@router.post("")
def api_dictionary_add(req: DictionaryCreate):
    log.info("Add word-bank entry: %r (source=%s)", req.text[:40], req.path or "manual")
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


@router.put("/{entry_id}")
def api_dictionary_update(entry_id: str, req: DictionaryUpdate):
    try:
        return dictionary.update_entry(entry_id, req.text, req.meaning)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{entry_id}")
def api_dictionary_delete(entry_id: str):
    log.info("Delete word-bank entry %s", entry_id)
    return {"deleted": dictionary.delete_entry(entry_id)}


@router.get("/media")
def api_dictionary_media(file: str):
    p = dictionary.media_path(file)
    if not p:
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "application/octet-stream")


@router.post("/{entry_id}/media")
def api_dictionary_attach_media(
    entry_id: str, kind: str = Form(...), file: UploadFile = File(...)
):
    """Attach an uploaded audio/image/video file to an entry (replaces any existing one)."""
    log.info("Attach %s media to entry %s (%s)", kind, entry_id, file.filename)
    try:
        return dictionary.attach_media(entry_id, kind, file.file, file.filename or "")
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{entry_id}/media/{kind}")
def api_dictionary_remove_media(entry_id: str, kind: str):
    try:
        return dictionary.remove_media(entry_id, kind)
    except KeyError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
