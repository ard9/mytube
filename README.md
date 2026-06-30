# Zaravan — a local, private English‑learning workstation

Zaravan turns a folder of videos (and audio) into a complete, **offline‑first** English‑learning environment: watch with subtitles, generate subtitles when a video has none, turn any text into speech, build a spaced‑repetition flashcard deck with real audio/video clips, and practice **speaking** with an AI tutor that gently corrects your mistakes — all running on **your own machine**, with no account and no data leaving your computer.

It speaks to a local LLM (or any API you plug in), so the conversation tutor can run **fully on‑device** through [Ollama](https://ollama.com).

---

## ✨ Features

- **📺 Local library** — point Zaravan at a folder and it organizes your media by **category → channel**, reading metadata from yt‑dlp `*.info.json` when present. Audio files (mp3, m4a, wav, flac, …) sit right alongside videos and behave the same way.
- **🎬 Watch page** — resume where you left off, adjustable playback speed, autoplay next, captions, and **"find in this video"** to jump to any spoken phrase.
- **✍️ Offline subtitle generation** — no captions? Generate them locally with **Whisper** (faster‑whisper) for any spoken language, with optional translation to English. GPU‑accelerated when available, CPU fallback otherwise.
- **🔊 Text‑to‑speech (two engines)** — paste text and hear it:
  - **StyleTTS2** — fully offline neural voice, can **clone** a speaker from a short clip.
  - **gTTS** — tiny install, no API key, **many languages** and English accents (needs internet).
  - Long text is split into sentences and stitched back together, with **read‑along** highlighting.
- **📖 Word bank (spaced repetition)** — save words and sentences as flashcards with an **SRS** scheduler. Cards can carry an **audio / image / video clip** cut straight from the source, so you review the word *in context*, with sound.
- **🎤 Live conversation tutor** — speak (browser mic or local Whisper), the tutor replies in text **and voice**, and flags your mistakes as `original → corrected` with an explanation in your language. Each correction can be saved to the Word bank **with generated speech audio** in one click.
- **⬇️ Add from YouTube** — paste a URL or channel and Zaravan downloads it into the library (via yt‑dlp), with optional subtitles.
- **🔐 Private by design** — everything runs locally. Cloud LLMs are *optional*; with Ollama, even the tutor is offline.

---

## 🚀 Quick start

### Option A — Docker (recommended, GPU‑ready, includes Ollama)

Requirements: Docker, Docker Compose, and (for GPU) the NVIDIA Container Toolkit.

```bash
# 1) Point the library volume at your media folder (edit docker-compose.yml
#    or set LIBRARY_PATH), then:
docker compose up -d

# 2) Open the app
#    http://localhost:8420
```

This starts two containers: **`Zaravan-mytube`** (the app) and **`Zaravan-ollama`** (a local LLM server). They share a Docker network, so inside the app's **AI settings** set the Ollama base URL to:

```
http://Zaravan-ollama:11434
```

> The app runs *inside a container*, so `localhost:11434` would point at the app container itself — use the Ollama **container name**, not `localhost`.

### Option B — Run directly (no Docker)

Requirements: **Python 3.12+** and **ffmpeg** on your PATH.

```bash
# macOS / Linux
./start.sh

# Windows
start.bat

# …or manually
pip install -r backend/requirements.txt
python backend/main.py
```

Then open **http://127.0.0.1:8420**.

---

## 🧩 Optional features & their dependencies

Zaravan runs out of the box; the heavier features are opt‑in so you only install what you use.

| Feature | Install | Notes |
|---|---|---|
| Offline subtitles (Whisper) | `pip install faster-whisper` | First run downloads the model; GPU optional |
| TTS — StyleTTS2 (offline) | `pip install styletts2` | Pulls in torch; sizeable but no API key |
| TTS — gTTS (online) | `pip install gtts` | Tiny; needs internet, many languages |
| Conversation tutor | *nothing extra* | Uses any OpenAI‑compatible API, Gemini, or **Ollama** |
| YouTube downloads | `yt-dlp` on PATH | ffmpeg recommended for merges |

The Docker image bundles ffmpeg and the GPU stacks for you.

---

## 🤖 Setting up the conversation tutor

Open **Live conversation → AI settings** and pick a provider:

- **Ollama (local, private):** base URL `http://Zaravan-ollama:11434` (Docker) or `http://localhost:11434` (bare‑metal). Then set the **model name** to one you've actually pulled — verify with `ollama list`. A 3B‑class model or larger (e.g. `qwen2.5:3b`, `llama3.1:8b`) is recommended so the tutor reliably returns structured corrections; very small (1B) models often can't.
- **OpenRouter / OpenAI‑compatible / Gemini:** paste your API key. (Gemini is region‑restricted in some countries — if you get a `403`, use a proxy/VPN or switch to OpenRouter.)

**Loading your own GGUF into Ollama** (one‑time, only for hand‑downloaded models):

```bash
docker exec -it Zaravan-ollama ollama create my-model -f /root/.ollama/models/Modelfile
docker exec -it Zaravan-ollama ollama list
```

For catalog models, just `ollama pull <name>` — no Modelfile needed.

---

## ⚙️ Configuration

Settings live in **`config.json`** (editable from the UI) and can be overridden by environment variables — handy for Docker:

| Env var | Meaning | Default |
|---|---|---|
| `MYTUBE_LIBRARY_PATH` | Folder Zaravan scans for media | `D:\English\youtube_english` |
| `MYTUBE_DATA_DIR` | Where writable state is stored | project root (`/data` in Docker) |
| `MYTUBE_YTDLP_BIN` | yt‑dlp binary | `yt-dlp` |
| `MYTUBE_TTS_OUTPUT_DIR` | Where generated speech is saved | app default folder |
| `MYTUBE_OLLAMA_BASE_URL` | Default Ollama endpoint | — |
| `MYTUBE_HOST` / `MYTUBE_PORT` | Server bind address | `127.0.0.1` / `8420` |

**Library folder layout** (depth decides category/channel):

```
library/
  Category/Channel/video.mp4     → category = Category, channel = Channel
  Channel/video.mp4              → category = Uncategorized, channel = Channel
  video.mp4                      → category = Uncategorized, channel = Unsorted
```

---

## 🏗️ Architecture

A small **FastAPI** backend and a **dependency‑free, vanilla‑JS** frontend — no build step, no framework.

```
backend/
  main.py            ← entry point (starts the server)
  app.py             ← builds the app: logging, middleware, exception handler, wiring
  schemas.py         ← request body shapes (Pydantic)
  deps.py            ← shared HTTP helpers (safe paths, range streaming)
  routers/           ← one file per feature (this is what you open when debugging)
    config.py        ← /api/config
    library.py       ← /api/library, video, thumb, subtitle, rename, delete
    transcribe.py    ← /api/transcribe/*   (offline Whisper subtitles)
    tts.py           ← /api/tts/*          (StyleTTS2 + gTTS speech)
    notes.py         ← /api/notes
    dictionary.py    ← /api/dictionary/*   (word bank + spaced repetition)
    downloads.py     ← /api/download, /api/downloads/*, /api/watch/*
    conversation.py  ← /api/conversation/* (speaking agent + streaming STT)
  # service modules (the actual logic):
  library.py  transcribe.py  tts.py  dictionary.py  conversation.py
  downloader.py  notes.py  progress.py  streaming.py  config.py

frontend/
  index.html
  css/styles.css
  js/  api.js  state.js  app.js  ui.js  conversation.js  whisperStream.js  download.js
```

Design notes:

- The conversation tutor uses the **Python standard library** for its LLM calls — no LangChain/agent framework — so the request path stays transparent and easy to debug. Provider differences are a handful of small `if` branches.
- Long‑running work (transcription, TTS, downloads, model pulls) runs as **background jobs** the frontend polls for progress.
- Media is served with **HTTP range** support so the player can seek instantly.

---


## New API endpoints

| Method | Path                          | Purpose                                              |
|--------|--------------------------------|-------------------------------------------------------|
| GET    | `/api/subtitle_search`        | `?q=...` — search inside all subtitle files            |
| GET    | `/api/subtitle_search_in_video` | `?path=...&q=...` — search inside ONE video's subtitle |
| POST   | `/api/rename`                 | `{path, title}` — rename a video + siblings            |
| POST   | `/api/delete`                 | `{path}` — delete a video + siblings                   |
| GET    | `/api/dictionary`             | list all dictionary entries (+ whether ffmpeg is present) |
| POST   | `/api/dictionary`             | `{text, meaning, path?, start?, end?, capture[]}` — add an entry (cuts clips when `path`+`capture` given) |
| PUT    | `/api/dictionary/{id}`        | `{text?, meaning?}` — edit an entry                    |
| DELETE | `/api/dictionary/{id}`        | delete an entry and its captured media                  |
| POST   | `/api/dictionary/{id}/media`  | upload an audio/image/video file to attach (`kind` + `file`) |
| DELETE | `/api/dictionary/{id}/media/{kind}` | remove one attached audio/image/video               |
| GET    | `/api/dictionary/media`       | `?file=...` — serve a saved audio/image/video clip      |
| GET    | `/api/dictionary/stats`       | study snapshot: due, new, learning, mastered, streak, reviewed today |
| GET    | `/api/dictionary/study`       | `?limit=&new=` — the cards due for review now (each with next-interval previews) |
| POST   | `/api/dictionary/{id}/review` | `{rating}` (1=Again 2=Hard 3=Good 4=Easy) — reschedule a card (SM-2) |
| GET    | `/api/transcribe/available`   | whether `faster-whisper` is installed + model list      |
| POST   | `/api/transcribe`             | `{path, language, model, translate, model_path}` — start a job |
| GET    | `/api/transcribe/jobs`        | list all transcription jobs                             |
| GET    | `/api/transcribe/{job_id}`    | poll a transcription job's status/progress              |
| POST   | `/api/transcribe/{job_id}/cancel` | cancel a running transcription job                  |
| GET    | `/api/tts/available`          | whether `styletts2` is installed (+ ffmpeg, max chars) |
| POST   | `/api/tts`                    | `{text, title?, voice_id?, diffusion_steps?, embedding_scale?}` — start a speech job |
| GET    | `/api/tts/{job_id}`           | poll a speech job's status/progress                  |
| POST   | `/api/tts/{job_id}/cancel`    | cancel a running speech job                           |
| GET    | `/api/tts/library`            | list all generated-audio clips                        |
| DELETE | `/api/tts/library/{id}`       | delete a generated clip + its audio file              |
| POST   | `/api/tts/library/{id}/to_dictionary` | make a Word bank card from a clip (text + audio attached) |
| POST   | `/api/tts/library/{id}/segment_to_dictionary` | make a card from one sentence-range (trimmed audio clip + meaning) |
| GET    | `/api/tts/media`              | `?file=...` — serve a generated audio file            |
| GET    | `/api/tts/voices`             | list saved reference voices (for cloning)             |
| POST   | `/api/tts/voices`             | `name` + `file` — save a reference voice              |
| DELETE | `/api/tts/voices/{id}`        | delete a saved reference voice                         |

The `/api/download` endpoint also gained an optional `subtitles` field
(`""` = off, `"all"` = every language, or e.g. `"en,fa"`).

## 🔒 Privacy

Zaravan stores everything on your machine: your library, notes, flashcards, and conversation sessions. No telemetry, no account. The only time data leaves your computer is if **you** choose a cloud LLM provider or the online gTTS engine — and both have fully local alternatives (Ollama and StyleTTS2).

---

## 🗺️ Roadmap ideas

- A dedicated grammar‑audit pass (a separate, focused model call) so even small local models produce reliable corrections.
- Per‑level routing (beginner / advanced) and long‑term conversation memory.
- More TTS voices and languages.

---

## 🙏 Acknowledgements

Built on the shoulders of excellent open‑source projects: [FastAPI](https://fastapi.tiangolo.com/), [faster‑whisper](https://github.com/SYSTRAN/faster-whisper), [StyleTTS2](https://github.com/yl4579/StyleTTS2), [gTTS](https://github.com/pndurette/gTTS), [Ollama](https://ollama.com), and [yt‑dlp](https://github.com/yt-dlp/yt-dlp).

---

## 📄 License

Add your license of choice here (e.g. MIT). Until then, all rights reserved by the author.
---
