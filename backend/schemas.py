"""
schemas.py
----------
Every request body the API accepts, in one place.

These used to live at the top of ``main.py``. Pulling them out means a router
file shows only its endpoints, and when a request fails validation you have a
single, short file to check the expected shape against.
"""

from __future__ import annotations

from pydantic import BaseModel


# ----- config ------------------------------------------------------------- #
class ConfigUpdate(BaseModel):
    library_path: str | None = None
    ytdlp_bin: str | None = None
    default_quality: str | None = None
    tts_output_dir: str | None = None


# ----- notes -------------------------------------------------------------- #
class NoteUpdate(BaseModel):
    path: str
    text: str


# ----- downloads / watch progress ----------------------------------------- #
class DownloadRequest(BaseModel):
    url: str
    quality: str = "720"
    category: str = ""
    subtitles: str = ""


class ProgressUpdate(BaseModel):
    path: str
    position: float
    duration: float = 0.0


class WatchedUpdate(BaseModel):
    path: str
    watched: bool


# ----- library management ------------------------------------------------- #
class RenameRequest(BaseModel):
    path: str
    title: str


class DeleteRequest(BaseModel):
    path: str


# ----- dictionary --------------------------------------------------------- #
class DictionaryCreate(BaseModel):
    text: str
    meaning: str = ""
    path: str = ""           # source video relative path ("" = manual entry)
    title: str = ""          # cached source title for display
    start: float = 0.0
    end: float = 0.0
    capture: list[str] = []  # any of "audio", "image", "video"


class DictionaryUpdate(BaseModel):
    text: str | None = None
    meaning: str | None = None


class DictionaryReview(BaseModel):
    rating: int        # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy


class DictionaryWithAudio(BaseModel):
    """Create a Word-bank card from plain text and (optionally) generate + attach
    its spoken audio with a TTS engine. Used by the live-conversation 'save this
    correction' button so a card carries the corrected sentence AND its audio."""
    text: str
    meaning: str = ""
    with_audio: bool = True
    engine: str = "gtts"             # "gtts" (online) | "styletts2" (offline); falls back if missing
    lang: str = "en"                 # gTTS language (corrections are English → "en")
    tld: str = "com"                 # gTTS English accent
    voice_id: str = ""               # StyleTTS2 saved voice id ("" = default)
    slow: bool = False               # gTTS slow speech


# ----- transcribe (offline Whisper) --------------------------------------- #
class TranscribeRequest(BaseModel):
    path: str
    language: str = ""      # "" = auto-detect; else a language code like "fa", "en", "es"
    model: str = ""         # "" = use default size
    translate: bool = False  # True = translate speech to English subtitles
    model_path: str = ""    # "" = auto-download by name; else a local folder with a pre-downloaded model


# ----- text-to-speech (StyleTTS2 / gTTS) ---------------------------------- #
class TTSRequest(BaseModel):
    text: str
    title: str = ""
    engine: str = "styletts2"        # "styletts2" (offline) | "gtts" (online, many languages)
    voice_id: str = ""               # "" = built-in default voice; else a saved voice id
    diffusion_steps: int = 5         # quality vs. speed (more = slower, more varied)
    embedding_scale: float = 1.0     # expressiveness (higher = more emotional)
    alpha: float = 0.3               # timbre blend toward the text vs. the reference voice
    beta: float = 0.7                # prosody blend toward the text vs. the reference voice
    # gTTS-only options:
    lang: str = "en"                 # language code (en, es, fr, fa, ...)
    tld: str = "com"                 # Google domain → English accent (com=US, co.uk=UK, ...)
    slow: bool = False               # speak slowly


class TTSToDictRequest(BaseModel):
    meaning: str = ""


class TTSSegmentToDict(BaseModel):
    from_index: int = 0
    to_index: int = 0
    meaning: str = ""


# ----- conversation (speaking-practice agent) ----------------------------- #
class ConversationSettings(BaseModel):
    provider: str | None = None
    openrouter_key: str | None = None
    openrouter_model: str | None = None
    gemini_key: str | None = None
    gemini_model: str | None = None
    openai_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    temperature: float | None = None
    proxy: str | None = None
    voice_mode: str | None = None
    stt_mode: str | None = None
    whisper_model: str | None = None
    whisper_partial_model: str | None = None
    whisper_device: str | None = None
    whisper_compute: str | None = None
    level: str | None = None
    explain_language: str | None = None
    vad_silence_ms: int | None = None
    vad_sensitivity: float | None = None


class OllamaPull(BaseModel):
    model: str
    base_url: str = ""


class ConversationMessage(BaseModel):
    text: str


class ConversationRename(BaseModel):
    title: str
