"""
app.py
------
Builds the FastAPI application: configures logging, installs the request-logging
middleware and a catch-all exception handler, wires up every router, and mounts
the frontend.

This is deliberately small. Each feature's endpoints live in ``routers/<area>.py``;
this file is just the assembly point. The two things worth reading here are the
middleware and the exception handler — they are what make debugging easier:

  * every request gets a short id and an access-log line with its status and how
    long it took, e.g.  ``GET /api/library -> 200 in 12.4ms``;
  * any *unhandled* error (a real bug, not an HTTPException you raised on
    purpose) is logged with a full traceback tagged with that same id, and the
    client gets a clean JSON 500 that includes the id — so a user can tell you
    "request a1b2c3 failed" and you can find the exact stack trace.
"""

from __future__ import annotations

import os

# Must be set before any numerical library (numpy/torch/ctranslate2, pulled in
# transitively by faster-whisper) is imported anywhere in this process.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import time
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import logging_setup
from logging_setup import get_logger
from routers import all_routers

FRONTEND_DIR = config.ROOT_DIR / "frontend"


def create_app() -> FastAPI:
    logging_setup.configure()
    log = get_logger("app")
    access_log = get_logger("access")

    app = FastAPI(title="MyTube Local")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    # --- request logging + a request id on every line ---------------------- #
    @app.middleware("http")
    async def access_logger(request: Request, call_next):
        rid = logging_setup.new_request_id()
        token = logging_setup.set_request_id(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handler below logs the traceback; here we just note
            # timing and re-raise so the handler turns it into a 500.
            took = (time.perf_counter() - start) * 1000
            access_log.error("%s %s -> 500 in %.1fms", request.method, request.url.path, took)
            raise
        else:
            took = (time.perf_counter() - start) * 1000
            # Surface the id to the client so users can quote it when reporting bugs.
            response.headers["X-Request-ID"] = rid
            level = access_log.warning if response.status_code >= 400 else access_log.info
            level("%s %s -> %s in %.1fms", request.method, request.url.path,
                  response.status_code, took)
            return response
        finally:
            # Reset only after the access line is logged, so that line still
            # carries the request id.
            logging_setup.reset_request_id(token)

    # --- turn unhandled bugs into a logged traceback + clean JSON ---------- #
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        rid = logging_setup._request_id.get() or "-"
        log.error(
            "Unhandled error on %s %s [%s]\n%s",
            request.method, request.url.path, rid,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": rid,
                "error": f"{type(exc).__name__}: {exc}",
            },
            headers={"X-Request-ID": rid},
        )

    # HTTPExceptions are expected control flow (404/400/...). Let FastAPI's
    # default handler format them; the access log already records the status.

    # --- wire up every feature router -------------------------------------- #
    for r in all_routers:
        app.include_router(r)

    # --- frontend (added last so /api/* wins) ------------------------------ #
    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")

    log.info("Application ready with %d routers", len(all_routers))
    return app


# A module-level instance so you can run `uvicorn app:app` directly.
app = create_app()
