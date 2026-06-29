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
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "llama3.2:3b",
    "temperature": 0.7,
    "proxy": "",                         # optional HTTP proxy, e.g. http://127.0.0.1:10809
    # learner preferences (defaults; the UI can override per request)
    "voice_mode": "browser",             # browser | styletts2
    "stt_mode": "browser",               # browser | whisper
    "whisper_model": "base",             # tiny | base | small | medium | large-v3
    "whisper_partial_model": "tiny",     # fast model used only for live partials
    "whisper_device": "auto",            # auto | cuda | cpu
    "whisper_compute": "auto",           # auto | float16 | int8_float16 | int8
    "level": "auto",                     # auto | beginner | intermediate | advanced
    "explain_language": "Persian",       # language used to explain corrections
    "vad_silence_ms": 800,               # hands-free: pause before auto-send
    "vad_sensitivity": 0.5,              # hands-free: 0..1 (higher = more sensitive)
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


def _http_post_json(url: str, payload: dict, headers: dict, timeout: int = 90,
                    proxy: str = "", direct: bool = False) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)

    # `direct` forces a no-proxy connection (used for local Ollama, which must
    # never be routed through a VPN/proxy). Otherwise: an explicit proxy is used
    # when set, and an empty proxy lets urllib fall back to the OS/system proxy.
    if direct:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        do_open = opener.open
    elif proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        do_open = opener.open
    else:
        do_open = urllib.request.urlopen

    try:
        with do_open(req, timeout=timeout) as resp:
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
        reason = str(getattr(exc, "reason", exc))
        if "refused" in reason.lower() or "10061" in reason:
            raise LLMError(
                "Could not connect to the local model server. Is Ollama running? "
                "Install it from ollama.com and start it (or run 'ollama serve')."
            ) from exc
        if "getaddrinfo" in reason or "Name or service" in reason or "Temporary failure" in reason:
            raise LLMError(
                "Could not resolve the AI provider's address — the server has no working "
                "internet route to it (often DNS filtering). Turn on your VPN/proxy in "
                "system-wide / TUN mode, or set a Proxy in AI settings, then try again."
            ) from exc
        raise LLMError(f"Could not reach the AI provider: {reason}") from exc
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


def _openai_style_chat(messages: list[dict], settings: dict, base_url: str, key: str,
                       model: str, force_direct: bool = False) -> str:
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
    data = _http_post_json(url, payload, headers,
                          proxy=settings.get("proxy", ""), direct=force_direct)
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

    data = _http_post_json(url, payload, headers={}, proxy=settings.get("proxy", ""))
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
    if provider == "ollama":
        base = _ollama_base(settings) + "/v1"
        if not settings.get("ollama_model"):
            raise LLMError("No Ollama model set. Choose or download one in the AI settings.")
        return _openai_style_chat(
            messages, settings, base, "ollama",
            settings.get("ollama_model", ""), force_direct=True,
        )
    # default: openrouter
    return _openai_style_chat(
        messages, settings,
        "https://openrouter.ai/api/v1",
        settings.get("openrouter_key", ""),
        settings.get("openrouter_model", ""),
    )


# --------------------------------------------------------------------------- #
# Local models via Ollama (auto-download by name, runs on the user's GPU)
# --------------------------------------------------------------------------- #
def _ollama_base(settings: dict | None = None) -> str:
    settings = settings or load_settings()
    return (settings.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")


def _local_get(url: str, timeout: int = 5):
    """GET a localhost URL, always bypassing any proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def ollama_status(base_url: str = "") -> dict:
    """Is Ollama reachable, and which models are already installed?"""
    base = (base_url or _ollama_base()).rstrip("/")
    try:
        data = _local_get(base + "/api/tags", timeout=4)
        models = [
            {"name": m.get("name", ""), "size": m.get("size", 0)}
            for m in (data.get("models") or [])
        ]
        models.sort(key=lambda m: m["name"])
        return {"running": True, "base_url": base, "models": models}
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "base_url": base, "models": [], "error": str(exc)}


# pull (download) jobs --------------------------------------------------------
_pull_jobs: dict[str, dict] = {}
_pull_lock = threading.Lock()


def start_pull(model: str, base_url: str = "") -> dict:
    base = (base_url or _ollama_base()).rstrip("/")
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "model": model, "status": "starting",
        "percent": 0.0, "detail": "", "error": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _pull_lock:
        _pull_jobs[job_id] = job
    threading.Thread(target=_run_pull, args=(job, base, model), daemon=True).start()
    return job


def _run_pull(job: dict, base: str, model: str) -> None:
    try:
        payload = json.dumps({"name": model, "stream": True}).encode("utf-8")
        req = urllib.request.Request(base + "/api/pull", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with _pull_lock:
            job["status"] = "running"
        # Ollama streams newline-delimited JSON progress events.
        with opener.open(req, timeout=3600) as resp:
            for raw_line in resp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                status = ev.get("status", "")
                total = ev.get("total")
                completed = ev.get("completed")
                with _pull_lock:
                    if status:
                        job["detail"] = status
                    if total and completed:
                        job["percent"] = round(completed / total * 100, 1)
                    if ev.get("error"):
                        job["status"] = "error"
                        job["error"] = ev["error"]
                    if status == "success":
                        job["status"] = "done"
                        job["percent"] = 100.0
        with _pull_lock:
            if job["status"] not in ("error", "done"):
                job["status"] = "done"
                job["percent"] = 100.0
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        with _pull_lock:
            job["status"] = "error"
            if "refused" in reason.lower() or "10061" in reason:
                job["error"] = ("Could not connect to Ollama. Install it from ollama.com "
                                "and make sure it's running.")
            else:
                job["error"] = reason
    except Exception as exc:  # noqa: BLE001
        with _pull_lock:
            job["status"] = "error"
            job["error"] = str(exc)


def get_pull(job_id: str) -> dict | None:
    with _pull_lock:
        job = _pull_jobs.get(job_id)
        return dict(job) if job else None



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
_whisper_device_used: str | None = None


_whisper_available_cache: bool | None = None


def whisper_available() -> bool:
    global _whisper_available_cache
    if _whisper_available_cache is None:
        try:
            import faster_whisper  # noqa: F401
            _whisper_available_cache = True
        except ImportError:
            _whisper_available_cache = False
    return _whisper_available_cache


def cuda_available() -> bool:
    """True if ctranslate2 can see a CUDA GPU (this is what faster-whisper uses)."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _resolve_device(settings: dict) -> tuple[str, str]:
    dev = (settings.get("whisper_device") or "auto").lower()
    comp = (settings.get("whisper_compute") or "auto").lower()
    if dev == "auto":
        if cuda_available():
            dev = "cuda"
            comp = "float16" if comp == "auto" else comp
        else:
            dev = "cpu"
            comp = "int8" if comp == "auto" else comp
    elif comp == "auto":
        comp = "float16" if dev == "cuda" else "int8"
    return dev, comp


def _get_whisper(model_size: str):
    settings = load_settings()
    model_size = model_size or settings.get("whisper_model", "base")
    dev, comp = _resolve_device(settings)
    key = f"{model_size}|{dev}|{comp}"
    global _whisper_device_used
    with _whisper_lock:
        if key not in _whisper_cache:
            from faster_whisper import WhisperModel
            try:
                log.info("Loading Whisper '%s' on %s (%s)…", model_size, dev, comp)
                m = WhisperModel(model_size, device=dev, compute_type=comp)
                _whisper_device_used = dev
                log.info("Whisper '%s' ready on %s.", model_size, dev)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Whisper '%s' failed to load on %s (%s). Falling back to CPU (int8) — "
                    "this is much slower. For GPU, install CUDA libs: "
                    "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
                    model_size, dev, exc,
                )
                m = WhisperModel(model_size, device="cpu", compute_type="int8")
                _whisper_device_used = "cpu"
            _whisper_cache[key] = m
        return _whisper_cache[key]


def whisper_info() -> dict:
    s = load_settings()
    dev, comp = _resolve_device(s)
    return {
        "available": whisper_available(),
        "cuda": cuda_available(),
        "device": _whisper_device_used or dev,    # actual once loaded, else resolved
        "compute": comp,
        "model": s.get("whisper_model", "base"),
        "partial_model": s.get("whisper_partial_model", "tiny"),
        "loaded": _whisper_device_used is not None,
    }


def transcribe_audio(file_path: Path, model_size: str = "", language: str = "en",
                     vad_filter: bool = True, partial: bool = False) -> str:
    """Transcribe a clip to text. `partial` uses a fast model + settings for live streaming."""
    if not whisper_available():
        raise RuntimeError(
            "faster-whisper is not installed on the server. "
            "Run 'pip install faster-whisper' or use the browser's speech recognition instead."
        )
    settings = load_settings()
    if partial:
        size = settings.get("whisper_partial_model") or "tiny"
    else:
        size = model_size or settings.get("whisper_model", "base")
    model = _get_whisper(size)
    kwargs = dict(
        language=language or None,
        vad_filter=vad_filter,
        beam_size=1 if partial else 5,
        condition_on_previous_text=False,
    )
    if partial:
        kwargs["best_of"] = 1
        kwargs["temperature"] = 0.0
    segments, _info = model.transcribe(str(file_path), **kwargs)
    return " ".join((seg.text or "").strip() for seg in segments).strip()
