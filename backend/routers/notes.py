"""
routers/notes.py
----------------
Per-video free-text notes. Thin wrapper over the ``notes`` service module.
"""

from __future__ import annotations

from fastapi import APIRouter

import notes
from logging_setup import get_logger
from schemas import NoteUpdate

log = get_logger("api.notes")
router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("")
def api_notes_all():
    return notes.get_all()


@router.put("")
def api_note_set(update: NoteUpdate):
    notes.set_note(update.path, update.text)
    return {"ok": True}
