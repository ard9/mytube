"""
routers/conversation.py
-----------------------
The live speaking-practice agent: provider/Ollama setup, conversation sessions,
one-shot speech-to-text, and the real-time streaming-STT WebSocket.

The LLM calls and Whisper handling live in the ``conversation`` service module;
the streaming algorithm lives in ``streaming``. This file is the HTTP/WebSocket
surface in front of both.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter, File, Form, HTTPException, UploadFile,
    WebSocket, WebSocketDisconnect,
)

import conversation
import streaming
import tts
from logging_setup import get_logger
from schemas import (
    ConversationMessage, ConversationRename, ConversationSettings, OllamaPull,
)

log = get_logger("api.conversation")
router = APIRouter(prefix="/api/conversation", tags=["conversation"])


# ----- capability / setup ------------------------------------------------- #
@router.get("/available")
def api_conversation_available():
    return {
        "whisper": conversation.whisper_available(),
        "styletts": tts.is_available(),
        "providers": ["openrouter", "gemini", "openai", "ollama"],
    }


@router.get("/whisper_info")
def api_conversation_whisper_info():
    return conversation.whisper_info()


@router.get("/ollama/status")
def api_ollama_status(base_url: str = ""):
    return conversation.ollama_status(base_url)


@router.post("/ollama/pull")
def api_ollama_pull(req: OllamaPull):
    if not req.model.strip():
        raise HTTPException(status_code=400, detail="Model name is required")
    log.info("Ollama pull: %s", req.model.strip())
    return conversation.start_pull(req.model.strip(), req.base_url)


@router.get("/ollama/pull/{job_id}")
def api_ollama_pull_status(job_id: str):
    job = conversation.get_pull(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Pull job not found")
    return job


# ----- settings ----------------------------------------------------------- #
@router.get("/settings")
def api_conversation_get_settings():
    return conversation.public_settings()


@router.post("/settings")
def api_conversation_set_settings(update: ConversationSettings):
    changed = update.model_dump(exclude_none=True)
    log.info("Update conversation settings: %s", ", ".join(changed) or "(nothing)")
    conversation.save_settings(changed)
    return conversation.public_settings()


# ----- sessions ----------------------------------------------------------- #
@router.get("/sessions")
def api_conversation_sessions():
    return {"sessions": conversation.list_sessions()}


@router.post("/sessions")
def api_conversation_create():
    log.info("Create conversation session")
    return conversation.create_session()


@router.get("/sessions/{session_id}")
def api_conversation_get(session_id: str):
    s = conversation.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return s


@router.delete("/sessions/{session_id}")
def api_conversation_delete(session_id: str):
    log.info("Delete conversation session %s", session_id)
    return {"deleted": conversation.delete_session(session_id)}


@router.post("/sessions/{session_id}/rename")
def api_conversation_rename(session_id: str, req: ConversationRename):
    s = conversation.rename_session(session_id, req.title)
    if not s:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return s


@router.post("/sessions/{session_id}/greeting")
def api_conversation_greeting(session_id: str):
    try:
        return conversation.start_greeting(session_id)
    except conversation.LLMError as exc:
        log.warning("Greeting failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/message")
def api_conversation_message(session_id: str, req: ConversationMessage):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Message is empty")
    try:
        return conversation.send_message(session_id, req.text)
    except conversation.LLMError as exc:
        log.warning("Message failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ----- speech-to-text ----------------------------------------------------- #
@router.post("/stt")
def api_conversation_stt(file: UploadFile = File(...), model: str = Form(""),
                         vad_filter: str = Form("true"), partial: str = Form("false")):
    """Transcribe a clip with Whisper. `partial=true` is the fast live-streaming path."""
    if not conversation.whisper_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper is not installed. Run 'pip install faster-whisper' "
                   "or switch the input mode to the browser's speech recognition.",
        )
    suffix = Path(file.filename or "clip.webm").suffix or ".webm"
    tmp = Path(tempfile.gettempdir()) / f"echo_stt_{os.getpid()}_{id(file)}{suffix}"
    try:
        with open(tmp, "wb") as out:
            out.write(file.file.read())
        text = conversation.transcribe_audio(
            tmp, model_size=model,
            vad_filter=(vad_filter.lower() != "false"),
            partial=(partial.lower() == "true"),
        )
        return {"text": text}
    except RuntimeError as exc:
        log.warning("STT failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@router.websocket("/stt_stream")
async def ws_stt_stream(ws: WebSocket):
    """
    Real-time streaming STT over a WebSocket (LocalAgreement-2, see streaming.py).

    Protocol — client -> server:
      * binary frames: little-endian int16, 16 kHz, mono PCM (raw samples).
      * text frame {"type":"endpoint"}: end of utterance (VAD detected silence);
        server flushes a final result and resets for the next turn.
      * text frame {"type":"close"}: client is done; server closes.

    Server -> client (JSON text frames):
      * {"type":"partial","committed": "...", "pending": "..."}  (live updates)
      * {"type":"final","text": "..."}                            (per utterance)
      * {"type":"error","detail": "..."}
    """
    await ws.accept()
    if not conversation.whisper_available():
        await ws.send_json({"type": "error",
                            "detail": "faster-whisper is not installed on the server."})
        await ws.close()
        return

    # Building the streamer loads/uses the cached Whisper model — do it off the
    # event loop so the connection handshake isn't blocked by a cold model load.
    try:
        streamer = await asyncio.to_thread(streaming.make_streamer)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not start streaming STT: %s", exc)
        await ws.send_json({"type": "error", "detail": f"Could not start streaming: {exc}"})
        await ws.close()
        return

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            data = msg.get("bytes")
            if data is not None:
                streamer.insert_audio(streaming.pcm16_to_float32(data))
                if streamer.due():
                    # inference is blocking (GPU/CPU bound) -> run in a thread
                    committed, pending = await asyncio.to_thread(streamer.process)
                    await ws.send_json({"type": "partial",
                                        "committed": committed, "pending": pending})
                continue

            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except (ValueError, TypeError):
                    ctrl = {}
                kind = ctrl.get("type")
                if kind == "endpoint":
                    final = await asyncio.to_thread(streamer.finish)
                    await ws.send_json({"type": "final", "text": final})
                    # fresh streamer for the next utterance in the same session
                    streamer = await asyncio.to_thread(streaming.make_streamer)
                elif kind == "close":
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("Streaming STT socket error: %s", exc)
        try:
            await ws.send_json({"type": "error", "detail": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
