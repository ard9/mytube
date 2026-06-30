"""
main.py
-------
Entry point. Kept tiny on purpose: it just starts the server. The actual
application (routers, middleware, logging) is built in ``app.py``.

Run with:  python backend/main.py        (or use the start scripts)
       or:  uvicorn app:app --app-dir backend
"""

from __future__ import annotations

import config
from app import app  # noqa: F401  (imported so `uvicorn main:app` also works)
from logging_setup import get_logger

log = get_logger("main")


def main() -> None:
    import uvicorn

    cfg = config.load_config()
    log.info("Library: %s", cfg["library_path"])
    log.info("Open http://%s:%s in your browser", cfg["host"], cfg["port"])
    uvicorn.run(app, host=cfg["host"], port=int(cfg["port"]))


if __name__ == "__main__":
    main()
