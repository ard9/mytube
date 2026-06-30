"""
routers/config.py
-----------------
Read and update the server's settings (library path, yt-dlp binary, ...).
Backed by ``config.py`` -> ``config.json``.
"""

from __future__ import annotations

from fastapi import APIRouter

import config
from logging_setup import get_logger
from schemas import ConfigUpdate

log = get_logger("api.config")
router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def api_get_config():
    return config.load_config()


@router.post("/config")
def api_set_config(update: ConfigUpdate):
    changed = update.model_dump(exclude_none=True)
    log.info("Updating config: %s", ", ".join(changed) or "(nothing)")
    return config.save_config(changed)
