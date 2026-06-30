"""
deps.py
-------
Small HTTP helpers shared by more than one router.

``safe_path`` and ``range_stream`` used to sit in ``main.py``; they belong here
so the library router (and anything else serving files) can import them without
dragging in the whole app.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

import config
from logging_setup import get_logger

log = get_logger("api.files")


def safe_path(rel_path: str) -> Path:
    """
    Resolve a client-supplied relative path against the library root and refuse
    anything that escapes it (path-traversal guard).
    """
    root = config.get_library_path().resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        log.warning("Blocked path outside library: %r", rel_path)
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
