"""
routers package
---------------
One module per feature area, each exposing a FastAPI ``router``. ``all_routers``
is the single list ``app.py`` walks to wire them up, so adding a feature is:
create ``routers/<thing>.py`` with a ``router``, then add it here.

If a request misbehaves, the log line names the area (e.g. ``mytube.api.tts``)
which is exactly the file to open or send for review.
"""

from . import (
    config,
    conversation,
    dictionary,
    downloads,
    library,
    notes,
    transcribe,
    tts,
)

# Order is mostly cosmetic (it sets the order in the /docs page). The only hard
# rule — ``/api/tts/{job_id}`` coming after the literal /api/tts/* paths — is
# handled *inside* routers/tts.py, not here.
all_routers = [
    config.router,
    library.router,
    transcribe.router,
    tts.router,
    notes.router,
    dictionary.router,
    downloads.router,
    conversation.router,
]
