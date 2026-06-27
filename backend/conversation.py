"""
conversation.py
---------------
Live speaking-practice agent for Echo.

Pipeline (all wired up in the frontend, this module is the brain on the server):

    microphone --> speech-to-text --> THIS MODULE (an LLM tutor) --> reply text
    reply text --> text-to-speech --> played back to the learner

Responsibilities here:
  * Talk to a Large Language Model. Three back-ends are supported and chosen
    from the UI, with the API key entered there too (never hard-coded):
        - "openrouter"  -> https://openrouter.ai (lots of free models)
        - "gemini"      -> Google Gemini (free tier)
        - "openai"      -> any OpenAI-compatible endpoint (custom base URL)
  * Keep the tutor *persona*: a friendly English conversation partner that
    chats about everyday life AND gently corrects the learner's mistakes,
    explaining each fix in Persian so it's easy to understand.
  * Store chat sessions on disk so you can open a brand-new conversation any
    time and come back to old ones (same spirit as the rest of Echo).
  * Optional speech-to-text with faster-whisper, for when the browser's own
    live recognition isn't available or you prefer Whisper. (The browser's
    Google-powered live recognition is handled entirely on the frontend and
    needs nothing here.)

No new third-party dependency is required: HTTP calls use the standard library
(urllib). faster-whisper is reused only if it's already installed for the
"Generate subtitles" feature.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from config import ROOT_DIR

log = logging.getLogger("mytube.conversation")

# --------------------------------------------------------------------------- #
# On-disk layout
# --------------------------------------------------------------------------- #
CONV_DIR = ROOT_DIR / "conversation_data"
SETTINGS_FILE = CONV_DIR / "settings.json"
SESSIONS_DIR = CONV_DIR / "sessions"

_lock = threading.Lock()


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Settings (provider + keys + model + voice/STT preferences)
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS = {
    "provider": "openrouter",            # openrouter | gemini | openai
    "openrouter_key": "",
    "openrouter_model": "meta-llama/llama-3.3-70b-instruct:free",
    "gemini_key": "",
    "gemini_model": "gemini-2.0-flash",
    "openai_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini",
    "temperature": 0.7,
    # learner preferences (defaults; the UI can override per request)
    "voice_mode": "browser",             # browser | styletts2
    "stt_mode": "browser",               # browser | whisper
    "whisper_model": "base",             # tiny | base | small | medium | large-v3
    "level": "auto",                     # auto | beginner | intermediate | advanced
    "explain_language": "Persian",       # language used to explain corrections
}


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read conversation settings (%s); using defaults", exc)
    return data


def save_settings(updates: dict) -> dict:
    _ensure_dirs()
    data = load_settings()
    data.update({k: v for k, v in updates.items() if v is not None})
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Failed to write conversation settings: %s", exc)
    return data


def public_settings() -> dict:
    """
    Settings for the UI. Keys are included (this is a local, single-user app and
    you need to see/edit them), but we also expose `*_key_set` booleans so the UI
    can show a tidy "key saved" state without echoing the secret if you prefer.
    """
    s = load_settings()
    s["openrouter_key_set"] = bool(s.get("openrouter_key"))
    s["gemini_key_set"] = bool(s.get("gemini_key"))
    s["openai_key_set"] = bool(s.get("openai_key"))
    return s


# --------------------------------------------------------------------------- #
# The tutor persona
# --------------------------------------------------------------------------- #
def _system_prompt(settings: dict) -> str:
    level = settings.get("level") or "auto"
    explain = settings.get("explain_language") or "Persian"
    level_line = {
        "beginner": "The learner is a beginner — keep your vocabulary and sentences simple.",
        "intermediate": "The learner is intermediate — speak naturally but stay clear.",
        "advanced": "The learner is advanced — speak naturally, use richer vocabulary and idioms.",
        "auto": "Adapt your difficulty to how the learner writes.",
    }.get(level, "Adapt your difficulty to how the learner writes.")

    return (
        "You are Echo, a warm, encouraging English conversation partner and tutor. "
        "Your job is to help the learner practice everyday spoken English by having a "
        "real, flowing conversation about daily life (their day, plans, hobbies, work, "
        "feelings, opinions, etc.).\n\n"
        "Conversation style:\n"
        "- Your reply will be READ ALOUD by text-to-speech, so keep it natural and "
        "fairly short: usually 1-3 sentences.\n"
        "- Always keep the conversation going by ending with a friendly, relevant "
        "follow-up question.\n"
        "- Be supportive and patient. Never lecture.\n"
        f"- {level_line}\n\n"
        "Correcting mistakes:\n"
        "- Watch the learner's message for real mistakes in grammar, word choice, or "
        "naturalness. Ignore trivial things like capitalization or obvious typos.\n"
        "- For each meaningful mistake, give the original, a corrected/more natural "
        f"version, and a short explanation written in {explain}.\n"
        "- If there are no real mistakes, return an empty corrections list and, in your "
        "reply, you may briefly praise their good English.\n\n"
        "OUTPUT FORMAT — respond with ONLY a single valid JSON object, no markdown, no "
        "code fences, with exactly these fields:\n"
        '{\n'
        '  "reply": "your spoken conversational reply in English",\n'
        '  "corrections": [\n'
        '    {"original": "...", "fixed": "...", "explanation": "... (in '
        f'{explain})"}}\n'
        '  ]\n'
        '}\n'
        "Do not include any text before or after the JSON."
    )


def _greeting_prompt() -> str:
    return (
        "Start the conversation. Greet the learner warmly in one or two sentences and "
        "ask one simple, friendly opening question about their day or how they are. "
        "Respond in the required JSON format with an empty corrections list."
    )


# --------------------------------------------------------------------------- #
# LLM back-ends
# --------------------------------------------------------------------------- #
class LLMError(RuntimeError):
    pass


def _http_post_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        # Surface the provider's own message — it's usually very informative
        # (bad key, model not found, rate limit, no credits, etc.).
        msg = _extract_api_error(detail) or f"HTTP {exc.code} {exc.reason}"
        raise LLMError(msg) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Could not reach the AI provider: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError("The AI provider returned a response that wasn't valid JSON.") from exc


def _extract_api_error(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw[:300] if raw else ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err)[:300]
        if isinstance(err, str):
            return err
        if "message" in data:
            return str(data["message"])
    return raw[:300]


def _openai_style_chat(messages: list[dict], settings: dict, base_url: str, key: str, model: str) -> str:
    if not key:
        raise LLMError("No API key set for this provider. Add one in the AI settings.")
    if not model:
        raise LLMError("No model set for this provider. Add a model name in the AI settings.")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(settings.get("temperature", 0.7)),
    }
    headers = {"Authorization": f"Bearer {key}"}
    # OpenRouter likes these for attribution; harmless elsewhere.
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "http://localhost"
        headers["X-Title"] = "Echo"
    data = _http_post_json(url, payload, headers)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Unexpected response shape from the AI provider.") from exc


def _gemini_chat(messages: list[dict], settings: dict, key: str, model: str) -> str:
    if not key:
        raise LLMError("No Gemini API key set. Add one in the AI settings.")
    if not model:
        raise LLMError("No Gemini model set. Add a model name in the AI settings.")

    system_text = ""
    contents = []
    for m in messages:
        role = m.get("role")
        text = m.get("content", "")
        if role == "system":
            system_text = (system_text + "\n" + text).strip() if system_text else text
        else:
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": text}],
            })

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    )
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": float(settings.get("temperature", 0.7)),
            "responseMimeType": "application/json",
        },
    }
    if system_text:
        payload["system_instruction"] = {"parts": [{"text": system_text}]}

    data = _http_post_json(url, payload, headers={})
    try:
        cands = data.get("candidates") or []
        parts = cands[0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError) as exc:
        # Gemini may block content; surface a useful message.
        fb = data.get("promptFeedback") if isinstance(data, dict) else None
        if fb:
            raise LLMError(f"Gemini did not return text (promptFeedback: {fb}).") from exc
        raise LLMError("Unexpected response shape from Gemini.") from exc


def chat_complete(messages: list[dict], settings: dict | None = None) -> str:
    """Send `messages` (OpenAI chat format) to the configured provider; return raw text."""
    settings = settings or load_settings()
    provider = (settings.get("provider") or "openrouter").lower()

    if provider == "gemini":
        return _gemini_chat(messages, settings, settings.get("gemini_key", ""),
                            settings.get("gemini_model", ""))
    if provider == "openai":
        return _openai_style_chat(
            messages, settings,
            settings.get("openai_base_url", "https://api.openai.com/v1"),
            settings.get("openai_key", ""),
            settings.get("openai_model", ""),
        )
    # default: openrouter
    return _openai_style_chat(
        messages, settings,
        "https://openrouter.ai/api/v1",
        settings.get("openrouter_key", ""),
        settings.get("openrouter_model", ""),
    )


# --------------------------------------------------------------------------- #
# Parsing the tutor's JSON reply (defensively — free models aren't perfect)
# --------------------------------------------------------------------------- #
def _parse_tutor_reply(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {"reply": "", "corrections": []}

    # Strip ```json ... ``` fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        # after stripping backticks a leading "json" tag may remain
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the outermost {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict):
        # Give up gracefully: treat the whole thing as the spoken reply.
        return {"reply": (raw or "").strip(), "corrections": []}

    reply = parsed.get("reply") or parsed.get("message") or ""
    corrections = parsed.get("corrections") or []
    clean_corr = []
    if isinstance(corrections, list):
        for c in corrections:
            if not isinstance(c, dict):
                continue
            clean_corr.append({
                "original": str(c.get("original", "")).strip(),
                "fixed": str(c.get("fixed", c.get("corrected", ""))).strip(),
                "explanation": str(c.get("explanation", "")).strip(),
            })
    return {"reply": str(reply).strip(), "corrections": clean_corr}


# --------------------------------------------------------------------------- #
# Sessions (chat history on disk)
# --------------------------------------------------------------------------- #
def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def _read_session(session_id: str) -> dict | None:
    p = _session_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_session(session: dict) -> None:
    _ensure_dirs()
    session["updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        _session_path(session["id"]).write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.error("Failed to write session %s: %s", session.get("id"), exc)


def list_sessions() -> list[dict]:
    _ensure_dirs()
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        msgs = s.get("messages", [])
        out.append({
            "id": s.get("id"),
            "title": s.get("title") or "New conversation",
            "created": s.get("created"),
            "updated": s.get("updated"),
            "message_count": len(msgs),
        })
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out


def create_session(title: str = "") -> dict:
    _ensure_dirs()
    sid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    session = {
        "id": sid,
        "title": title or "New conversation",
        "created": now,
        "updated": now,
        "messages": [],
    }
    _write_session(session)
    return session


def get_session(session_id: str) -> dict | None:
    return _read_session(session_id)


def delete_session(session_id: str) -> bool:
    p = _session_path(session_id)
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def rename_session(session_id: str, title: str) -> dict | None:
    s = _read_session(session_id)
    if not s:
        return None
    s["title"] = title.strip() or s.get("title") or "New conversation"
    _write_session(s)
    return s


def _auto_title(text: str) -> str:
    t = " ".join((text or "").split())
    return (t[:40] + "…") if len(t) > 40 else (t or "New conversation")


def _llm_messages(session: dict, settings: dict, max_turns: int = 20) -> list[dict]:
    """Build the message list for the LLM: system prompt + recent history."""
    msgs = [{"role": "system", "content": _system_prompt(settings)}]
    history = session.get("messages", [])[-max_turns:]
    for m in history:
        role = m.get("role")
        if role in ("user", "assistant"):
            msgs.append({"role": role, "content": m.get("content", "")})
    return msgs


def send_message(session_id: str, user_text: str) -> dict:
    """
    Append the learner's message, ask the tutor, store + return the reply.
    Returns {"session": <id>, "reply": str, "corrections": [...], "user": {...}, "assistant": {...}}.
    """
    settings = load_settings()
    session = _read_session(session_id)
    if session is None:
        session = create_session()

    user_text = (user_text or "").strip()
    if not user_text:
        raise ValueError("Empty message.")

    user_msg = {"role": "user", "content": user_text,
                "ts": datetime.now().isoformat(timespec="seconds")}
    session.setdefault("messages", []).append(user_msg)

    # Auto-name a fresh session from its first user line.
    if session.get("title") in (None, "", "New conversation"):
        session["title"] = _auto_title(user_text)

    messages = _llm_messages(session, settings)
    raw = chat_complete(messages, settings)
    parsed = _parse_tutor_reply(raw)

    assistant_msg = {
        "role": "assistant",
        "content": parsed["reply"],
        "corrections": parsed["corrections"],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    session["messages"].append(assistant_msg)
    _write_session(session)

    return {
        "session_id": session["id"],
        "title": session["title"],
        "reply": parsed["reply"],
        "corrections": parsed["corrections"],
        "user": user_msg,
        "assistant": assistant_msg,
    }


def start_greeting(session_id: str) -> dict:
    """Have the tutor open the conversation (used when a new session starts)."""
    settings = load_settings()
    session = _read_session(session_id)
    if session is None:
        session = create_session()

    messages = [
        {"role": "system", "content": _system_prompt(settings)},
        {"role": "user", "content": _greeting_prompt()},
    ]
    raw = chat_complete(messages, settings)
    parsed = _parse_tutor_reply(raw)
    assistant_msg = {
        "role": "assistant",
        "content": parsed["reply"],
        "corrections": [],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    session.setdefault("messages", []).append(assistant_msg)
    _write_session(session)
    return {
        "session_id": session["id"],
        "title": session["title"],
        "reply": parsed["reply"],
        "corrections": [],
        "assistant": assistant_msg,
    }


# --------------------------------------------------------------------------- #
# Speech-to-text with faster-whisper (optional fallback to the browser engine)
# --------------------------------------------------------------------------- #
_whisper_cache: dict[str, object] = {}
_whisper_lock = threading.Lock()


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_whisper(model_size: str):
    model_size = model_size or "base"
    with _whisper_lock:
        if model_size not in _whisper_cache:
            from faster_whisper import WhisperModel
            log.info("Loading Whisper '%s' for live speech-to-text…", model_size)
            try:
                m = WhisperModel(model_size, device="auto", compute_type="auto")
            except Exception as exc:  # noqa: BLE001
                log.warning("Whisper GPU load failed (%s); using CPU.", exc)
                m = WhisperModel(model_size, device="cpu", compute_type="int8")
            _whisper_cache[model_size] = m
        return _whisper_cache[model_size]


def transcribe_audio(file_path: Path, model_size: str = "", language: str = "en") -> str:
    """Transcribe a short recorded clip to text. Raises RuntimeError if unavailable."""
    if not whisper_available():
        raise RuntimeError(
            "faster-whisper is not installed on the server. "
            "Run 'pip install faster-whisper' or use the browser's speech recognition instead."
        )
    settings = load_settings()
    model = _get_whisper(model_size or settings.get("whisper_model", "base"))
    segments, _info = model.transcribe(
        str(file_path),
        language=language or None,
        vad_filter=True,
    )
    return " ".join((seg.text or "").strip() for seg in segments).strip()
