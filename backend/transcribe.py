"""
transcribe.py
-------------
Local, offline subtitle generation using faster-whisper (a fast re-implementation
of OpenAI's Whisper speech-recognition model). Used as a fallback for videos
that don't have any subtitle file — works for *any* spoken language Whisper
supports, and can optionally translate everything to English.

No API key, no internet connection needed once the model is downloaded the
first time. Runs fully on this machine; speed depends on CPU/GPU.

Jobs follow the same in-memory job-registry pattern as downloader.py so the
frontend can poll progress the same way it already does for downloads.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# IMPORTANT: this must run before ctranslate2/faster_whisper/numpy/torch get
# imported anywhere in the process (including transitively). On machines
# where Python also has Anaconda/MKL-linked numpy or scipy installed (common
# when running from an Anaconda "base" environment, as opposed to a clean
# venv), more than one copy of the Intel OpenMP runtime (libiomp5md.dll on
# Windows) ends up loaded into the same process. That conflict either crashes
# the whole process outright ("OMP: Error #15: Initializing libiomp5md.dll,
# but found libiomp5md.dll already initialized") or corrupts in-flight
# computations in subtler ways that can surface as seemingly unrelated
# errors (e.g. "tuple index out of range" from code that received a garbled
# array shape). Setting this before the conflicting libraries load tells
# OpenMP to allow multiple runtimes rather than aborting/corrupting — see
# https://www.intel.com/content/www/us/en/developer/articles/technical/recommendations-multi-threading.html
# This is the official upstream workaround; it doesn't fix the duplicate
# linking itself, but makes it safe in practice for this use case (a single
# CPU/GPU job runs at a time here, so the performance risk it warns about
# doesn't really apply).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import logging
import re
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config import get_library_path

log = logging.getLogger("mytube.transcribe")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# Lazily imported + cached so the app still starts fine if faster-whisper
# isn't installed; we only fail when someone actually tries to use it.
_model_cache: dict[str, object] = {}

_cuda_dlls_registered = False


def _register_cuda_dll_dirs() -> None:
    """
    On Windows, pip-installed NVIDIA packages (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) drop their DLLs inside the Python environment's
    site-packages, e.g. .../site-packages/nvidia/cublas/bin/*.dll — but
    Windows has no idea to look there, so ctranslate2 (which faster-whisper
    uses under the hood) fails to find cublas64_*.dll / cudnn64_*.dll even
    though they're sitting right there on disk. This finds those folders
    and adds them to the DLL search path before any GPU load is attempted.

    No-op on non-Windows platforms, and harmless if the nvidia packages
    aren't installed (GPU just won't be available, same as before).
    """
    global _cuda_dlls_registered
    if _cuda_dlls_registered or sys.platform != "win32":
        return
    _cuda_dlls_registered = True

    try:
        import nvidia  # the namespace package created by nvidia-cublas-cu12 / nvidia-cudnn-cu12
    except ImportError:
        return

    found = []
    for base in nvidia.__path__:
        base_path = Path(base)
        if not base_path.exists():
            continue
        # Each sub-package (cublas, cudnn, cuda_nvrtc, ...) ships its DLLs
        # in its own "bin" folder.
        for sub in base_path.iterdir():
            bin_dir = sub / "bin"
            if bin_dir.is_dir():
                found.append(bin_dir)

    for bin_dir in found:
        path_str = str(bin_dir)
        # os.add_dll_directory is the modern (Python 3.8+) way to extend
        # the DLL search path on Windows; PATH alone isn't always enough.
        try:
            os.add_dll_directory(path_str)
        except (OSError, AttributeError):
            pass
        if path_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = path_str + os.pathsep + os.environ.get("PATH", "")

    if found:
        log.info("Registered %d NVIDIA DLL director%s for GPU acceleration: %s",
                  len(found), "y" if len(found) == 1 else "ies",
                  ", ".join(str(d) for d in found))

MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"


def is_available() -> bool:
    """Whether faster-whisper is installed in this environment."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _load_with_fallback(target: str, **kwargs):
    """
    Try loading a WhisperModel with the requested device settings; if that
    fails for *any* reason while attempting GPU, fall back to plain CPU
    instead of failing the whole job.

    We deliberately catch broadly here (not just CUDA-keyword errors) —
    ctranslate2's GPU initialization can fail in ways that don't mention
    "cuda"/"cublas" at all (e.g. a driver/runtime mismatch surfacing as a
    generic `tuple index out of range` while it tries to read GPU device
    properties). Since CPU mode is always a safe, working fallback, there's
    no good reason to let an unrecognized GPU error kill the whole job —
    we'd rather transcribe slower than not at all.

    The returned model is tagged with `_mytube_device` ("cuda" or "cpu") so
    callers can later tell, without guessing, whether a cached model is
    running on GPU or CPU (used by _run to decide whether a later inference
    failure is worth retrying on CPU).
    """
    from faster_whisper import WhisperModel

    _register_cuda_dll_dirs()

    try:
        model = WhisperModel(target, device="auto", compute_type="auto", **kwargs)
        model._mytube_device = "cuda"
        return model
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "GPU acceleration unavailable (%s: %s) — falling back to CPU. "
            "This usually means the GPU/CUDA setup on this machine isn't "
            "fully compatible; CPU mode still works, just slower.",
            type(exc).__name__, exc,
        )
        try:
            model = WhisperModel(target, device="cpu", compute_type="int8", **kwargs)
            model._mytube_device = "cpu"
            return model
        except Exception:
            # If CPU loading *also* fails, that's a real problem (bad model
            # files, wrong path, etc.) — let the original, more specific
            # error surface rather than the generic CPU one.
            raise exc


def _get_model(model_size: str, job: dict | None = None, model_path: str = ""):
    """
    Load (and cache) a faster-whisper model. Raises ImportError if not installed.

    If `model_path` is given, it's used directly as `model_size_or_path` —
    this lets someone point at a model they already downloaded by hand
    (e.g. a folder with model.bin/config.json/tokenizer.json/vocabulary.txt,
    or a local CTranslate2-converted model), completely skipping any network
    access. Otherwise falls back to the named preset size, which faster-whisper
    auto-downloads from Hugging Face the first time and caches for next time.

    GPU is tried first (faster) and automatically falls back to CPU if CUDA
    isn't properly set up on this machine — see _load_with_fallback.
    """
    cache_key = model_path.strip() or model_size
    if cache_key not in _model_cache:
        if job is not None:
            with _lock:
                job["stage"] = "loading_model"
        if model_path.strip():
            log.info("Loading Whisper model from local path '%s'...", model_path)
            target = model_path.strip()
            local_dir = Path(target)
            if not local_dir.exists():
                raise RuntimeError(
                    f"Local model path not found: '{target}'. "
                    "Check the folder path and that it contains model.bin, "
                    "config.json, tokenizer.json, and vocabulary.txt."
                )
            # local_files_only=True guarantees no network access at all when
            # pointing at an existing local folder.
            _model_cache[cache_key] = _load_with_fallback(target, local_files_only=True)
        else:
            log.info("Loading Whisper model '%s' (first use downloads it; this can take a while)...", model_size)
            _model_cache[cache_key] = _load_with_fallback(model_size)
    return _model_cache[cache_key]


def _fmt_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _new_job(video_rel_path: str, language: str, model_size: str, task: str, model_path: str = "") -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "video_path": video_rel_path,
        "language": language,         # "" = auto-detect, else a language code like "fa", "en"
        "model": model_size,
        "model_path": model_path,     # "" = use the named preset; else a local folder/path
        "task": task,                 # "transcribe" | "translate" (translate -> English)
        "status": "queued",           # queued | running | done | error | cancelled
        "stage": "queued",            # queued | loading_model | analyzing | transcribing | done
        "percent": 0.0,
        "segments_written": 0,
        "detected_language": "",
        "error": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "subtitle_path": "",          # set on success
    }


def _probe_duration(video: Path) -> float:
    """
    Get the video's real duration via ffprobe, independent of Whisper/VAD.
    faster-whisper's `info.duration` can be unreliable for progress math when
    vad_filter is on (it sometimes reflects only the speech-detected portion),
    so we get a stable total from ffprobe instead. Returns 0.0 if unavailable.
    """
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _transcribe_to_srt(model, video: Path, job: dict, total_duration: float, root: Path) -> Path:
    """
    Run model.transcribe() and write the result to an .srt file, updating
    job progress as it goes. Raised exceptions propagate to the caller
    (which may retry on CPU if this was attempted on GPU).
    """
    lang = job["language"] or None  # None = auto-detect
    segments, info = model.transcribe(
        str(video),
        language=lang,
        task=job["task"],
        vad_filter=True,            # skip silence, much faster on long videos
    )

    with _lock:
        job["detected_language"] = getattr(info, "language", "") or ""
        job["stage"] = "transcribing"
        if not total_duration:
            total_duration = getattr(info, "duration", None) or 0.0

    out_path = video.with_suffix(".srt")
    # Avoid clobbering a pre-existing manual/auto subtitle silently.
    if out_path.exists():
        out_path = video.with_name(video.stem + ".whisper.srt")

    # faster-whisper yields segments lazily as it decodes — this is where
    # GPU inference actually happens, one segment at a time, so an error
    # caused by a broken GPU/driver setup can surface here even if the
    # model object itself was constructed successfully.
    with out_path.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            if job["status"] == "cancelled":
                break
            text = (seg.text or "").strip()
            if not text:
                continue
            f.write(f"{i}\n")
            f.write(f"{_fmt_srt_timestamp(seg.start)} --> {_fmt_srt_timestamp(seg.end)}\n")
            f.write(f"{text}\n\n")
            f.flush()
            with _lock:
                job["segments_written"] = i
                if total_duration:
                    job["percent"] = min(99.0, round(seg.end / total_duration * 100, 1))

    return out_path


def _run(job: dict) -> None:
    with _lock:
        job["status"] = "running"
        job["stage"] = "loading_model"

    try:
        if not is_available():
            raise RuntimeError(
                "faster-whisper is not installed. Run "
                "'pip install faster-whisper' on the server, then restart MyTube."
            )

        root = get_library_path().resolve()
        video = (root / job["video_path"]).resolve()
        if root not in video.parents or not video.exists():
            raise FileNotFoundError("Video file not found")

        # Get a reliable total duration up front (independent of Whisper/VAD)
        # so percent math doesn't depend on faster-whisper's own estimate.
        total_duration = _probe_duration(video)

        model_path = job.get("model_path", "")
        model = _get_model(job["model"], job, model_path)

        with _lock:
            job["stage"] = "analyzing"

        try:
            out_path = _transcribe_to_srt(model, video, job, total_duration, root)
        except Exception as exc:  # noqa: BLE001
            # The model loaded fine, but something failed during actual GPU
            # inference (this is where errors like a generic "tuple index
            # out of range" from a broken CUDA/driver setup tend to show up
            # — they don't always happen at model-construction time). If we
            # were on GPU, retry the *entire* transcription on a fresh CPU
            # model rather than failing the job outright.
            cache_key = model_path.strip() or job["model"]
            was_gpu = getattr(_model_cache.get(cache_key), "_mytube_device", None) != "cpu"
            if not was_gpu or job["status"] == "cancelled":
                raise
            log.warning(
                "Transcription failed during GPU inference (%s: %s) — "
                "retrying on CPU.", type(exc).__name__, exc,
            )
            # Force a CPU rebuild of this model (bypass the cache, which
            # currently holds the broken GPU instance) and retry once.
            from faster_whisper import WhisperModel
            cpu_kwargs = {"local_files_only": True} if model_path.strip() else {}
            target = model_path.strip() or job["model"]
            model = WhisperModel(target, device="cpu", compute_type="int8", **cpu_kwargs)
            model._mytube_device = "cpu"
            _model_cache[cache_key] = model
            with _lock:
                job["stage"] = "analyzing"
                job["percent"] = 0.0
                job["segments_written"] = 0
            out_path = _transcribe_to_srt(model, video, job, total_duration, root)

        with _lock:
            if job["status"] != "cancelled":
                job["status"] = "done"
                job["stage"] = "done"
                job["percent"] = 100.0
                job["subtitle_path"] = str(out_path.relative_to(root).as_posix())
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")

    except Exception as exc:  # noqa: BLE001
        log.warning("Transcription job %s failed: %s", job["id"], exc)
        with _lock:
            job["status"] = "error"
            job["error"] = str(exc)
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")


def start_job(
    video_rel_path: str, language: str = "", model_size: str = DEFAULT_MODEL,
    task: str = "transcribe", model_path: str = "",
) -> dict:
    model_size = model_size if model_size in MODEL_SIZES else DEFAULT_MODEL
    task = task if task in ("transcribe", "translate") else "transcribe"
    job = _new_job(video_rel_path, language.strip(), model_size, task, (model_path or "").strip())
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
