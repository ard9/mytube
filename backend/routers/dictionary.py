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
from logging_setup import get_logger
from schemas import DictionaryCreate, DictionaryReview, DictionaryUpdate

log = get_logger("api.dictionary")
router = APIRouter(prefix="/api/dictionary", tags=["dictionary"])


@router.get("")
def api_dictionary_all():
    return {
        "entries": dictionary.get_all(),
        "ffmpeg": dictionary.ffmpeg_available(),
        "stats": dictionary.stats(),
    }


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
