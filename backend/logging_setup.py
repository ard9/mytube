"""
logging_setup.py
----------------
One place that decides *how* the whole server logs, so every module looks the
same in the console and is easy to grep when you are chasing a bug.

Why this exists
===============
The point of splitting the server into modules (see ``routers/``) is that when
something breaks you can look at — or send to someone — just the one file that
owns that feature. Logs only help with that if every line tells you **which**
module produced it. So:

  * every module uses ``logging.getLogger("mytube.<area>")`` (e.g.
    ``mytube.api.tts``, ``mytube.conversation``). The log line prints that name,
    so a failing request points straight at the file to open.
  * each HTTP request gets a short id (e.g. ``a1b2c3``). All log lines emitted
    while handling that request carry the same id, so you can follow one request
    from start to crash even when several happen at once.

Turning the dials (no code change needed)
=========================================
  * ``MYTUBE_LOGLEVEL=DEBUG``   – more detail (default INFO).
  * ``MYTUBE_LOGFILE=server.log`` – also write logs to that file (rotating).
  * ``MYTUBE_LOG_<AREA>=DEBUG`` – raise the level of just one area while leaving
    the rest quiet, e.g. ``MYTUBE_LOG_TTS=DEBUG`` only makes the TTS code noisy.
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import uuid
from pathlib import Path

# The id of the request currently being handled (set by the access-log
# middleware in app.py). Empty string when we're not inside a request, e.g.
# during start-up or background jobs.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Base name every project logger hangs off, e.g. "mytube.api.library".
ROOT_LOGGER = "mytube"

_configured = False


def new_request_id() -> str:
    """A short, human-readable id to tag one request's log lines with."""
    return uuid.uuid4().hex[:6]


def set_request_id(value: str) -> contextvars.Token:
    """Mark the current request id; returns a token to restore the previous one."""
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


def get_logger(area: str) -> logging.Logger:
    """
    Get the logger a module should use. ``area`` is the short feature name, e.g.
    ``get_logger("api.tts")`` -> logger ``mytube.api.tts``.
    """
    return logging.getLogger(f"{ROOT_LOGGER}.{area}")


class _RequestIdFilter(logging.Filter):
    """Attach the current request id to every record so the format can show it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


def configure(level: str | None = None, logfile: str | None = None) -> None:
    """
    Set up console (and optionally file) logging for the whole process.

    Safe to call more than once — only the first call does the work, so the two
    entry points (``main.py`` and ``app.create_app``) can both call it.
    """
    global _configured
    if _configured:
        return
    _configured = True

    level = (level or os.environ.get("MYTUBE_LOGLEVEL", "INFO")).upper()
    logfile = logfile or os.environ.get("MYTUBE_LOGFILE", "").strip()

    # "12:00:01  INFO    [a1b2c3] mytube.api.tts  message"
    fmt = "%(asctime)s  %(levelname)-7s [%(request_id)s] %(name)s  %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
    id_filter = _RequestIdFilter()

    root = logging.getLogger()
    root.setLevel(level)
    # Clear handlers a previous basicConfig() may have installed so we don't
    # print every line twice.
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(id_filter)
    root.addHandler(console)

    if logfile:
        try:
            path = Path(logfile).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(id_filter)
            root.addHandler(file_handler)
            get_logger("logging").info("Also writing logs to %s", path)
        except OSError as exc:  # pragma: no cover - just a convenience feature
            get_logger("logging").warning("Could not open log file %s: %s", logfile, exc)

    # Per-area overrides: MYTUBE_LOG_TTS=DEBUG -> logger "mytube.api.tts" etc.
    for env_key, env_val in os.environ.items():
        if env_key.startswith("MYTUBE_LOG_") and env_key != "MYTUBE_LOGFILE" and env_key != "MYTUBE_LOGLEVEL":
            area = env_key[len("MYTUBE_LOG_"):].lower().replace("__", ".")
            logging.getLogger(f"{ROOT_LOGGER}.{area}").setLevel(env_val.upper())
            # Also try the api.<area> namespace, where most routers live.
            logging.getLogger(f"{ROOT_LOGGER}.api.{area}").setLevel(env_val.upper())

    # Third-party libraries are noisy at INFO; keep them at WARNING unless the
    # user explicitly wants DEBUG everywhere.
    if level != "DEBUG":
        for noisy in ("uvicorn.access", "multipart", "httpx", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
