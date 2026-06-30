"""
tts.py
------
Local, offline text-to-speech using StyleTTS2 (https://github.com/yl4579/StyleTTS2,
via the `styletts2` pip package). Turns any English text — even very long text —
into natural speech, fully on this machine. No API key.

Two voice modes:
  * Default built-in voice (a public-domain LibriVox sample that ships with the
    StyleTTS2 model).
  * Cloned voice — point it at a short reference audio clip you uploaded and it
    mimics that speaker.

Long text is split into sentence-sized chunks (StyleTTS2's text encoder is
limited to ~420 characters per call), each chunk is synthesised, and the pieces
are stitched back together with a small natural pause between them — so you can
paste a whole article and get one continuous audio file out.

Generated audio is saved into a small library (`tts_library.json` + `tts_media/`)
so it can be browsed, played, downloaded, or added to the flashcard dictionary
later — independent of anything else in the app.

Jobs follow the same in-memory job-registry / polling pattern as transcribe.py
and downloader.py, so the frontend tracks progress the same way it already does.
"""

from __future__ import annotations

import os

# StyleTTS2 pulls in torch/numpy transitively. As in transcribe.py / main.py,
# guard against the Anaconda duplicate-OpenMP crash up front (harmless if it's
# already set). See transcribe.py for the full explanation.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import json
import logging
import re
import shutil
import subprocess
import threading
import uuid
import wave
from datetime import datetime
from pathlib import Path

from config import DATA_DIR
import config

log = logging.getLogger("mytube.tts")

# --------------------------------------------------------------------------- #
# Storage layout (mirrors dictionary.py's style)
# --------------------------------------------------------------------------- #
LIBRARY_FILE = DATA_DIR / "tts_library.json"
MEDIA_DIR = DATA_DIR / "tts_media"          # the built-in default output folder
VOICES_FILE = DATA_DIR / "tts_voices.json"
VOICES_DIR = DATA_DIR / "tts_voices"

SAMPLE_RATE = 24000          # StyleTTS2 LibriTTS model output rate
MAX_CHUNK_CHARS = 400        # stay safely under StyleTTS2's ~420-char limit
GAP_SECONDS = 0.3            # short silence stitched between chunks
MAX_TEXT_CHARS = 50000       # generous guard so a paste-bomb can't hang forever

VALID_VOICE_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_store_lock = threading.Lock()

# The loaded StyleTTS2 instance is cached — building it (and downloading the
# model the first time) is the slow part, so we only do it once per process.
_model_cache: dict[str, object] = {}
_model_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def is_available() -> bool:
    """Whether the `styletts2` package is importable in this environment."""
    try:
        import styletts2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001  (some envs raise non-ImportError on bad installs)
        return False


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# --------------------------------------------------------------------------- #
# Model loading (cached)
# --------------------------------------------------------------------------- #
def _ensure_torch_load_compat() -> None:
    """
    StyleTTS2 ships official checkpoints (the LibriTTS model plus the ASR/F0/BERT
    helpers) that were pickled the old way. PyTorch 2.6 flipped `torch.load`'s
    default to `weights_only=True`, which rejects those files with a
    "Weights only load failed / Unsupported global" error. These checkpoints come
    from the StyleTTS2 author (a trusted source), so we restore the old behaviour
    by defaulting `weights_only=False` for loads that don't specify it. Idempotent.
    """
    import torch

    if getattr(torch.load, "_mytube_patched", False):
        return
    _orig_load = torch.load

    def _patched_load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return _orig_load(*args, **kwargs)

    _patched_load._mytube_patched = True
    _patched_load._orig = _orig_load
    torch.load = _patched_load


def _get_model(job: dict | None = None):
    """
    Load (and cache) the StyleTTS2 model. The first call downloads the LibriTTS
    checkpoint + helper models and can take a while; subsequent calls reuse the
    cached instance. Raises a clear error if the package isn't installed.
    """
    with _model_lock:
        if "model" not in _model_cache:
            if job is not None:
                with _lock:
                    job["stage"] = "loading_model"
            try:
                from styletts2 import tts as _styletts2_tts
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "StyleTTS2 is not installed on the server. Run "
                    "'pip install styletts2' and restart the app."
                ) from exc
            # Make the official (trusted) checkpoints loadable under PyTorch >= 2.6.
            _ensure_torch_load_compat()
            log.info("Loading StyleTTS2 model (first use downloads it; this can take a while)...")
            # phoneme_converter='gruut' is pure-Python and needs no system espeak.
            _model_cache["model"] = _styletts2_tts.StyleTTS2(phoneme_converter="gruut")
            _model_cache["module"] = _styletts2_tts
            log.info("StyleTTS2 model ready.")
            _log_device(_model_cache["model"])
        return _model_cache["model"], _model_cache["module"]


def _log_device(model) -> None:
    """
    Say plainly, in the console, whether StyleTTS2 ended up on the GPU or the CPU.

    StyleTTS2 picks its device itself with ``torch.cuda.is_available()`` (it has
    no ``device`` argument), so the *only* thing that decides GPU vs CPU is
    whether a CUDA-enabled PyTorch can see your GPU. If you ever wonder "is TTS
    using my GPU?", this log line is the answer.
    """
    try:
        import torch
        dev = getattr(model, "device", "?")
        if torch.cuda.is_available():
            try:
                name = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001
                name = "unknown GPU"
            log.info("StyleTTS2 device = GPU  (%s) | device=%s, torch %s, CUDA %s",
                     name, dev, torch.__version__, torch.version.cuda)
        else:
            log.warning(
                "StyleTTS2 device = CPU  (device=%s, torch %s). "
                "torch.cuda.is_available() is False, so no CUDA GPU is usable here "
                "and TTS will be slow. This is almost always a CPU-only PyTorch "
                "build. To use the GPU, reinstall torch from a CUDA wheel index "
                "(e.g. https://download.pytorch.org/whl/cu126).",
                dev, torch.__version__,
            )
    except Exception as exc:  # noqa: BLE001
        log.info("Could not determine StyleTTS2 device: %s", exc)


def _default_ref_style(model, module):
    """
    Pre-compute the style vector for the built-in default voice once, so every
    chunk reuses it instead of re-downloading/re-computing it. Returns None on
    any failure — callers then let StyleTTS2 fall back to its own per-call
    default handling, which still works.
    """
    try:
        from cached_path import cached_path
        default_path = str(cached_path(module.DEFAULT_TARGET_VOICE_URL))
        return model.compute_style(default_path)
    except Exception as exc:  # noqa: BLE001
        log.info("Could not pre-compute default voice style (%s); using per-call default.", exc)
        return None


# --------------------------------------------------------------------------- #
# Text chunking
# --------------------------------------------------------------------------- #
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def chunk_text(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split text into chunks no longer than `max_len` characters, preferring
    sentence boundaries. Sentences longer than `max_len` themselves are split
    on whitespace as a last resort. Returns a list of non-empty chunks.
    """
    text = (text or "").strip()
    if not text:
        return []

    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for sent in sentences:
        if len(sent) > max_len:
            flush()
            # Hard-split an over-long sentence on word boundaries.
            words, buf = sent.split(), ""
            for w in words:
                if len(buf) + len(w) + 1 > max_len:
                    if buf.strip():
                        chunks.append(buf.strip())
                    buf = w
                else:
                    buf = f"{buf} {w}".strip()
            if buf.strip():
                chunks.append(buf.strip())
            continue

        if len(current) + len(sent) + 1 > max_len:
            flush()
            current = sent
        else:
            current = f"{current} {sent}".strip()

    flush()
    return chunks


# Ultra-short fragments (e.g. "Yes.", "OK.") are merged into a neighbour so we
# don't make a separate, wasteful model call for two characters.
_MIN_UNIT_CHARS = 25


def _synthesis_units(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split text into the units we actually synthesise — one per sentence, so each
    has its own exact start/end time in the final audio (used for read-along
    highlighting and for clipping a single line into the Word bank). Sentences
    longer than `max_len` are hard-split; tiny fragments are merged forward.
    """
    text = (text or "").strip()
    if not text:
        return []

    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]

    # Hard-split any sentence that's too long for one inference call.
    expanded: list[str] = []
    for s in sentences:
        if len(s) <= max_len:
            expanded.append(s)
            continue
        words, buf = s.split(), ""
        for w in words:
            if len(buf) + len(w) + 1 > max_len:
                if buf.strip():
                    expanded.append(buf.strip())
                buf = w
            else:
                buf = f"{buf} {w}".strip()
        if buf.strip():
            expanded.append(buf.strip())

    # Merge ultra-short fragments into the previous unit when it fits.
    units: list[str] = []
    for s in expanded:
        if units and (len(s) < _MIN_UNIT_CHARS or len(units[-1]) < _MIN_UNIT_CHARS) \
                and len(units[-1]) + 1 + len(s) <= max_len:
            units[-1] = f"{units[-1]} {s}"
        else:
            units.append(s)
    return units


# --------------------------------------------------------------------------- #
# WAV / mp3 writing
# --------------------------------------------------------------------------- #
def _write_wav_int16(path: Path, audio, sample_rate: int) -> None:
    """Write a float32 numpy waveform (range ~[-1, 1]) as a 16-bit PCM WAV."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    # Guard against clipping / NaNs from a bad segment.
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1.0:
        arr = arr / peak
    pcm = (arr * 32767.0).astype("<i2")

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _to_mp3(wav_path: Path) -> Path | None:
    """Transcode a WAV to mp3 with ffmpeg (smaller file). Returns None on failure."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
            capture_output=True, timeout=180,
        )
        if proc.returncode == 0 and mp3_path.exists():
            return mp3_path
    except Exception as exc:  # noqa: BLE001
        log.warning("mp3 conversion failed: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Library storage
# --------------------------------------------------------------------------- #
def _read_library() -> list[dict]:
    if LIBRARY_FILE.exists():
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read tts_library.json: %s", exc)
    return []


def _write_library(data: list[dict]) -> None:
    try:
        LIBRARY_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.error("Could not write tts_library.json: %s", exc)


def get_library() -> list[dict]:
    """Return every generated-audio entry, newest first."""
    with _store_lock:
        return _read_library()


def get_entry(entry_id: str) -> dict | None:
    with _store_lock:
        return next((e for e in _read_library() if e["id"] == entry_id), None)


def _add_library_entry(entry: dict) -> None:
    with _store_lock:
        data = _read_library()
        data.insert(0, entry)
        _write_library(data)


def _output_dir() -> Path:
    """
    The folder new audio is written to: the user's configured folder if set,
    otherwise the built-in default (<project>/tts_media). Created if missing.
    Raises a clear error if the chosen folder can't be created/written.
    """
    chosen = config.get_tts_output_dir() or MEDIA_DIR
    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Couldn't use the save folder '{chosen}': {exc}. "
            f"Pick a different folder on the Text to speech page, or clear it to use the default."
        ) from exc
    return chosen


def entry_file_path(entry: dict) -> Path | None:
    """
    Resolve where an entry's audio file actually lives. Newer entries store an
    absolute `path`; older ones (or ones whose folder moved) fall back to the
    default tts_media/ folder by filename. Returns None if the file is gone.
    """
    if not entry:
        return None
    raw = entry.get("path")
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    name = entry.get("file")
    if name:
        p = MEDIA_DIR / name
        if p.exists():
            return p
    return None


def delete_entry(entry_id: str) -> bool:
    """Remove a generated-audio entry and its media file from disk."""
    with _store_lock:
        data = _read_library()
        keep, removed = [], None
        for e in data:
            if e["id"] == entry_id:
                removed = e
            else:
                keep.append(e)
        if removed is None:
            return False
        _write_library(keep)
    f = entry_file_path(removed)
    if f:
        try:
            f.unlink()
        except OSError as exc:  # noqa: BLE001
            log.warning("Could not delete tts media %s: %s", f, exc)
    return True


def media_path(entry_id: str) -> Path | None:
    """Resolve a generated-audio entry id to its file path on disk (or None)."""
    return entry_file_path(get_entry(entry_id))


def cut_clip(entry: dict, start: float, end: float) -> Path | None:
    """
    Cut the [start, end] (seconds) slice out of an entry's audio and return a path
    to a temporary file. Returns None only if every method fails. The caller owns
    the temp file (move/delete it).

    Strategy, most-reliable first:
      1. If the source is a WAV, slice it sample-accurately in pure Python — no
         ffmpeg needed at all, works on every machine.
      2. Otherwise (mp3), try a fast ffmpeg seek-and-trim.
      3. If that fails or returns the wrong length, decode the whole file to WAV
         (a plain transcode, no seek flags — the most universally-supported ffmpeg
         operation) and slice that in Python.
    Every result is validated against the expected duration, so a "whole file"
    result (the classic -ss/-to version bug) is rejected instead of attached.
    """
    src = entry_file_path(entry)
    if not src:
        log.warning("cut_clip: source audio for entry %s not found on disk", entry.get("id"))
        return None
    try:
        start = max(0.0, float(start))
        end = float(end)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    duration = round(end - start, 3)

    # 1) WAV source -> slice directly, no ffmpeg.
    if src.suffix.lower() == ".wav":
        seg = _slice_wav(src, start, end)
        if seg:
            return seg
        log.warning("cut_clip: direct WAV slice failed for entry %s", entry.get("id"))

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cut_clip: ffmpeg not found on PATH and source isn't WAV (%s)", src.suffix)
        return None

    # 2) Fast ffmpeg seek-and-trim (mp3 out). Output-seek `-ss` + `-t` duration is
    #    the form that behaves consistently across builds.
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="tts_seg_")
    os.close(fd)
    tmp_path = Path(tmp)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-codec:a", "libmp3lame", "-q:a", "4", str(tmp_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            log.warning("cut_clip ffmpeg trim failed (rc=%s): %s", proc.returncode, err[:300])
        elif tmp_path.exists() and tmp_path.stat().st_size >= 200:
            got = _probe_duration(tmp_path)
            if got is not None and duration > 0 and got > duration + 1.0 and got > duration * 1.8:
                log.warning("cut_clip ffmpeg trim gave %.2fs, expected ~%.2fs; falling back", got, duration)
            else:
                return tmp_path
    except Exception as exc:  # noqa: BLE001
        log.warning("cut_clip ffmpeg trim crashed: %s", exc)
    try:
        tmp_path.unlink()
    except OSError:
        pass

    # 3) Bulletproof fallback: decode the whole file to WAV (no seeking), slice it.
    dec = _decode_full_wav(src)
    if dec:
        try:
            seg = _slice_wav(dec, start, end)
        finally:
            try:
                dec.unlink()
            except OSError:
                pass
        if seg:
            return seg

    log.warning("cut_clip: all methods failed for entry %s (%.2f-%.2f)", entry.get("id"), start, end)
    return None


def _slice_wav(wav_path: Path, start: float, end: float) -> Path | None:
    """Sample-accurate slice of a WAV into a new temp WAV, using only the stdlib."""
    import tempfile
    try:
        with wave.open(str(wav_path), "rb") as w:
            sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            total = w.getnframes()
            a = max(0, int(round(start * sr)))
            b = min(total, int(round(end * sr)))
            if b <= a:
                return None
            w.setpos(a)
            frames = w.readframes(b - a)
        if not frames:
            return None
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tts_seg_")
        os.close(fd)
        out = Path(tmp)
        with wave.open(str(out), "wb") as o:
            o.setnchannels(nch)
            o.setsampwidth(sw)
            o.setframerate(sr)
            o.writeframes(frames)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("WAV slice failed: %s", exc)
        return None


def _decode_full_wav(src: Path) -> Path | None:
    """Transcode the whole source to a temp WAV (no seek flags). Most compatible op."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tts_dec_")
    os.close(fd)
    out = Path(tmp)
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", str(out)],
            capture_output=True, timeout=300,
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 44:
            return out
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        log.warning("decode-to-wav failed (rc=%s): %s", proc.returncode, err[:300])
    except Exception as exc:  # noqa: BLE001
        log.warning("decode-to-wav crashed: %s", exc)
    try:
        out.unlink()
    except OSError:
        pass
    return None


def _probe_duration(path: Path) -> float | None:
    """Best-effort duration (seconds) of an audio file via ffprobe; None if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=30,
        )
        val = (out.stdout or b"").decode("utf-8", "replace").strip()
        return float(val) if val else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Saved reference voices (for cloning)
# --------------------------------------------------------------------------- #
def _read_voices() -> list[dict]:
    if VOICES_FILE.exists():
        try:
            data = json.loads(VOICES_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read tts_voices.json: %s", exc)
    return []


def _write_voices(data: list[dict]) -> None:
    try:
        VOICES_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.error("Could not write tts_voices.json: %s", exc)


def get_voices() -> list[dict]:
    with _store_lock:
        return _read_voices()


def _voice_by_id(voice_id: str) -> dict | None:
    with _store_lock:
        return next((v for v in _read_voices() if v["id"] == voice_id), None)


def add_voice(name: str, fileobj, filename: str = "") -> dict:
    """Save an uploaded reference-voice clip and register it for cloning."""
    VOICES_DIR.mkdir(exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in VALID_VOICE_EXTS:
        ext = ".wav"
    voice_id = uuid.uuid4().hex[:12]
    out_name = f"{voice_id}{ext}"
    out = VOICES_DIR / out_name
    try:
        with open(out, "wb") as dst:
            shutil.copyfileobj(fileobj, dst)
    except OSError as exc:
        raise ValueError(f"Could not save voice sample: {exc}")

    entry = {
        "id": voice_id,
        "name": (name or "").strip() or "My voice",
        "file": out_name,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    with _store_lock:
        data = _read_voices()
        data.insert(0, entry)
        _write_voices(data)
    return entry


def delete_voice(voice_id: str) -> bool:
    with _store_lock:
        data = _read_voices()
        keep, removed = [], None
        for v in data:
            if v["id"] == voice_id:
                removed = v
            else:
                keep.append(v)
        if removed is None:
            return False
        _write_voices(keep)
    name = removed.get("file")
    if name:
        f = VOICES_DIR / name
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    return True


def _voice_sample_path(voice_id: str) -> Path | None:
    """Absolute path to a saved voice's audio file, or None for the default voice."""
    if not voice_id:
        return None
    v = _voice_by_id(voice_id)
    if not v:
        return None
    p = VOICES_DIR / v["file"]
    return p if p.exists() else None


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def _new_job(text: str, title: str, voice_id: str, voice_name: str, opts: dict) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "text": text,
        "chars": len(text),
        "voice_id": voice_id,
        "voice_name": voice_name,        # "" => default built-in voice
        "opts": opts,
        "status": "queued",              # queued | running | done | error | cancelled
        "stage": "queued",               # queued | loading_model | synthesizing | encoding | done
        "percent": 0.0,
        "chunks_total": 0,
        "chunks_done": 0,
        "error": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "entry_id": "",                  # set on success (library entry id)
    }


def _derive_title(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:7])
    if len(words) > 7:
        title += "…"
    return title or "Untitled"


def _run(job: dict) -> None:
    import numpy as np

    with _lock:
        job["status"] = "running"
        job["stage"] = "loading_model"

    try:
        if not is_available():
            raise RuntimeError(
                "StyleTTS2 is not installed. Run 'pip install styletts2' on the "
                "server, then restart MyTube."
            )

        text = job["text"].strip()
        if not text:
            raise ValueError("Text is empty.")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"Text is too long (limit {MAX_TEXT_CHARS:,} characters).")

        units = _synthesis_units(text)
        if not units:
            raise ValueError("Nothing to say — the text has no readable content.")
        with _lock:
            job["chunks_total"] = len(units)

        model, module = _get_model(job)

        # Resolve the voice: a cloned reference, or the built-in default.
        voice_path = _voice_sample_path(job["voice_id"])
        if job["voice_id"] and voice_path is None:
            raise FileNotFoundError("The selected voice sample is missing.")

        if voice_path is not None:
            ref_s = model.compute_style(str(voice_path))
        else:
            ref_s = _default_ref_style(model, module)  # may be None -> per-call default

        opts = job.get("opts") or {}
        kwargs = dict(
            output_sample_rate=SAMPLE_RATE,
            alpha=float(opts.get("alpha", 0.3)),
            beta=float(opts.get("beta", 0.7)),
            diffusion_steps=int(opts.get("diffusion_steps", 5)),
            embedding_scale=float(opts.get("embedding_scale", 1.0)),
        )

        with _lock:
            job["stage"] = "synthesizing"

        gap_samples = int(SAMPLE_RATE * GAP_SECONDS)
        gap = np.zeros(gap_samples, dtype=np.float32)
        pieces: list = []
        segments: list = []      # exact per-sentence timing for read-along + clipping
        cursor = 0               # running sample position in the final audio

        for i, unit in enumerate(units, start=1):
            if job["status"] == "cancelled":
                break
            try:
                audio = model.inference(unit, ref_s=ref_s, **kwargs)
            except TypeError:
                # Older styletts2 without ref_s kwarg: fall back to target path.
                audio = model.inference(
                    unit,
                    target_voice_path=str(voice_path) if voice_path else None,
                    **kwargs,
                )
            audio = np.asarray(audio, dtype=np.float32)
            n = len(audio)
            segments.append({
                "i": i - 1,
                "text": unit,
                "start": round(cursor / SAMPLE_RATE, 3),
                "end": round((cursor + n) / SAMPLE_RATE, 3),
            })
            pieces.append(audio)
            cursor += n
            if i < len(units):
                pieces.append(gap)
                cursor += gap_samples

            with _lock:
                job["chunks_done"] = i
                job["percent"] = min(99.0, round(i / len(units) * 100, 1))

        if job["status"] == "cancelled":
            with _lock:
                job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return

        with _lock:
            job["stage"] = "encoding"

        full = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
        duration = round(len(full) / SAMPLE_RATE, 2)

        out_dir = _output_dir()                  # configured folder or default
        entry_id = uuid.uuid4().hex[:12]
        wav_path = out_dir / f"{entry_id}.wav"
        _write_wav_int16(wav_path, full, SAMPLE_RATE)

        # Prefer a small mp3 when ffmpeg is around; otherwise keep the wav.
        final_path = _to_mp3(wav_path)
        if final_path is not None:
            try:
                wav_path.unlink()
            except OSError:
                pass
        else:
            final_path = wav_path

        entry = {
            "id": entry_id,
            "title": job["title"] or _derive_title(text),
            "text": text,
            "voice_id": job["voice_id"],
            "voice_name": job["voice_name"],
            "file": final_path.name,
            "path": str(final_path.resolve()),   # remember exactly where it was saved
            "folder": str(out_dir.resolve()),    # the folder, for display
            "sample_rate": SAMPLE_RATE,
            "duration": duration,
            "chars": len(text),
            "segments": segments,                # [{i, text, start, end}] for read-along + clipping
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        _add_library_entry(entry)

        with _lock:
            job["status"] = "done"
            job["stage"] = "done"
            job["percent"] = 100.0
            job["entry_id"] = entry_id
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")

    except Exception as exc:  # noqa: BLE001
        log.warning("TTS job %s failed: %s", job["id"], exc)
        with _lock:
            job["status"] = "error"
            job["error"] = str(exc)
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")


def start_job(
    text: str, title: str = "", voice_id: str = "", opts: dict | None = None
) -> dict:
    text = (text or "").strip()
    voice_id = (voice_id or "").strip()
    voice_name = ""
    if voice_id:
        v = _voice_by_id(voice_id)
        voice_name = v["name"] if v else ""
    job = _new_job(text, (title or "").strip(), voice_id, voice_name, opts or {})
    with _lock:
        _jobs[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def cancel_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job["status"] not in ("running", "queued"):
            return False
        job["status"] = "cancelled"
    return True


def get_job(job_id: str) -> dict | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    with _lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return jobs
