# MyTube — local YouTube for your downloads

A local web app that turns a folder of downloaded videos into a YouTube-style
library, and lets you download whole channels with `yt-dlp` from the browser.

- **Browse** your videos in a YouTube-like grid (real thumbnails + durations).
- **Channels** are auto-detected and sectioned (from `.info.json` metadata,
  falling back to the folder name).
- **Watch** with a proper player and captions (`.srt`/`.vtt`).
- **Notes** on every video, saved server-side in `notes.json`.
- **Add channel**: paste a YouTube URL and it downloads into your library,
  sorted by channel — using the same `yt-dlp` settings as your command.

---

## What's new

**Playback experience**
- **Playback speed control** (0.5x–2x) on the watch page, remembered between
  videos via your browser's local storage.
- **Keyboard shortcuts** while watching: `Space`/`K` play-pause, `←`/`→` seek
  5s, `J`/`L` seek 10s, `↑`/`↓` volume, `M` mute, `F` fullscreen.
- **Autoplay** the next "up next" video when one finishes (toggle on/off from
  the watch page; same-channel videos are preferred, falling back to the
  newest video overall).

**Library management**
- **Rename** a video from the watch page — renames the video file *and* its
  sibling thumbnail/subtitle/`.info.json`, and updates the title inside
  `.info.json` so the new title actually shows up in the library. Notes and
  watch progress carry over to the new path automatically.
- **Delete** a video from the watch page (with a confirmation dialog) —
  removes the video and its sibling files from disk, and cleans up any
  associated notes/watch-progress entries.

**Subtitle ("caption") search**
- Toggle the new **CC** button next to the search bar to search *inside* your
  `.srt`/`.vtt` caption files instead of just titles/channel/category. Matches
  show a highlighted snippet with a timestamp; clicking a result jumps the
  player straight to that moment. Great for finding the exact spot in a long
  podcast or lecture where something was said. This searches across your
  *entire* library at once.

**Find in this video**
- While watching a video that has a subtitle, a **Find in this video** box
  appears below the player. Type a word or phrase and it searches *only*
  this video's captions — useful when you already know which video you
  want and just need the exact moment(s) something was said (as opposed to
  the CC search above, which is for finding which video out of your whole
  library mentions something). Every matching line shows with its
  timestamp; click any result to jump the player straight there. If the
  phrase was said multiple times in the video, every occurrence is listed.

**Subtitles from YouTube (any language YouTube provides)**
- The **Add channel** panel now has a **Subtitles** section. Turning it on
  downloads YouTube's own captions alongside the video — manually-added ones
  if the uploader made them, otherwise YouTube's auto-generated captions —
  converted to `.srt` automatically. Pick "All languages", a quick preset
  (English / Persian / both), or type any comma-separated language codes
  (e.g. `en,fa,ar`). This is free and uses your existing yt-dlp setup; no
  extra service or API key needed.

**Local subtitle generation for videos with no captions (Whisper, any spoken language)**
- On the watch page, videos that don't have a subtitle file show a
  **🎙 Generate subtitles** button. This runs OpenAI's Whisper model
  (via `faster-whisper`) **entirely on your own machine** — free, offline,
  no API key — and works for any language Whisper supports (auto-detected,
  or you can specify one). You can also choose to translate straight to
  English subtitles instead of transcribing in the original language.
  Pick a model size (tiny → large) to trade off speed vs. accuracy; the
  result is saved as a new `.srt` next to the video and shows up immediately.
  - This needs the optional `faster-whisper` package — see Requirements
    below. If it isn't installed, the button explains exactly what to run.
  - Progress is tracked as a background job, same pattern as channel
    downloads, so you can keep browsing while it works. The status line
    shows what's actually happening — loading the model, reading the audio,
    or transcribing with a running line count — instead of just sitting at
    0% with no explanation (model loading and the first few moments of
    audio analysis can take a while before the percentage itself starts
    moving, especially for longer videos or a first-time model download).
  - **Already have a model downloaded?** Tick "Use a model I already
    downloaded" and point it at the local folder (containing `model.bin`,
    `config.json`, `tokenizer.json`, `vocabulary.txt`) — this skips any
    network access entirely and uses your files directly.
  - **GPU not set up right?** If you have an NVIDIA GPU, MyTube tries to use
    it automatically (much faster than CPU). On Windows, if you've installed
    the `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` pip packages, MyTube
    auto-detects their DLL folders and adds them to the search path itself —
    you don't need to manually edit your system PATH. If GPU loading or
    inference still fails for any reason (driver mismatch, missing
    libraries, etc. — shows up as an error like `cublas64_12.dll is not
    found` or a generic error during transcription), it automatically
    retries the whole job on CPU instead of failing. CPU mode is slower but
    works everywhere with no extra setup.
  - **Running from an Anaconda environment?** A known conflict (`OMP: Error
    #15: Initializing libiomp5md.dll, but found libiomp5md.dll already
    initialized`) can happen when Anaconda's own MKL-linked numpy/scipy and
    faster-whisper's OpenMP runtime both try to load in the same process —
    this can crash the process outright, or corrupt computations in subtler
    ways that show up as unrelated-looking errors (e.g. `tuple index out of
    range`). MyTube sets the official upstream workaround
    (`KMP_DUPLICATE_LIB_OK=TRUE`) automatically on startup, so this
    shouldn't need any manual environment variable setup on your end. If
    you still hit issues, running from a plain `venv` instead of Anaconda's
    `base` environment avoids the conflict entirely (see the venv setup
    note above).

---

## Requirements

1. **Python 3.9+**
2. **yt-dlp** on your PATH — https://github.com/yt-dlp/yt-dlp
   ```
   pip install -U yt-dlp
   ```
3. **ffmpeg** (needed by yt-dlp to merge video+audio into mp4, and used to
   convert downloaded subtitles to `.srt`) — https://ffmpeg.org/download.html.
   `ffprobe` (bundled with ffmpeg) is also used to get accurate progress for
   the "Generate subtitles" feature below.
4. **Optional — for the "Generate subtitles" button** (local Whisper,
   any spoken language, works fully offline):
   ```
   pip install faster-whisper
   ```
   Not needed for anything else in the app. The first time you generate a
   subtitle with a given model size, it's downloaded automatically (a few
   hundred MB to a few GB depending on size) from Hugging Face and cached
   in `~/.cache/huggingface` (or `%USERPROFILE%\.cache\huggingface` on
   Windows) for next time. CPU works fine for `tiny`/`base`/`small`; a GPU
   is recommended for `medium`/`large-v3` on longer videos.

   **For GPU acceleration** (NVIDIA only, optional but much faster), also
   install:
   ```
   pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
   ```
   MyTube automatically locates these packages' DLLs and uses the GPU if
   available; if anything about the GPU setup isn't right, it silently
   falls back to CPU rather than failing.

   **Downloading the model yourself instead** (e.g. on a machine with better
   internet, or if huggingface.co is blocked on this one): grab a
   `faster-whisper`-format model folder — for example from
   https://huggingface.co/Systran (models named `faster-whisper-tiny`,
   `-base`, `-small`, `-medium`, `-large-v3`) — and put the folder anywhere
   on disk. In the "Generate subtitles" panel, tick "Use a model I already
   downloaded" and paste that folder's path. This skips the network
   entirely and reads the files directly.

---

## Run

**Windows:** double-click `start.bat`
**macOS / Linux:** `bash start.sh`

Then open **http://127.0.0.1:8420** in **Chrome or Edge**.

(Or manually: `pip install -r backend/requirements.txt` then `python backend/main.py`.)

The first time, go to **Settings** and set your **library folder**
(e.g. `D:\English\youtube_english`). That folder is where videos are scanned
from and downloaded into.

---

## How downloading works

The **Add channel** panel runs this (mirrors your reference command):

```
yt-dlp -f "bv*[height<=720]+ba/b[height<=720]" \
       --merge-output-format mp4 \
       --download-archive "<library>/downloaded.txt" \
       -P "<library>" \
       -o "%(uploader)s/%(title)s.%(ext)s" \
       --no-overwrites --continue \
       --write-info-json --write-thumbnail --convert-thumbnails jpg \
       "<URL>"
```

`--write-info-json` gives the channel metadata used for sectioning;
`--write-thumbnail` gives real grid thumbnails. The `downloaded.txt` archive
means re-running a channel only fetches new videos.

You can change quality (4K / 1080p / 720p / 480p / audio-only) in the panel,
and the exact command is previewed and copy-able.

---

## Project structure

```
mytube/
├── backend/
│   ├── main.py          FastAPI app + routes + range video streaming
│   ├── config.py        config.json load/save, paths, logging
│   ├── library.py       scan folder, group by channel, rename/delete, subtitle search
│   ├── notes.py         per-video notes (notes.json)
│   ├── progress.py      per-video watch progress (watch_state.json)
│   ├── downloader.py    yt-dlp subprocess wrapper + progress parsing (incl. subtitles)
│   ├── transcribe.py    local Whisper subtitle generation (optional, background jobs)
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── api.js        REST client
│       ├── state.js      shared state + helpers
│       ├── ui.js         rendering (grid, sidebar, watch)
│       ├── download.js   add-channel panel + job polling
│       └── app.js        init, routing, events, keyboard shortcuts, rename/delete, Whisper UI
├── start.bat / start.sh
├── config.json          (created on first save)
├── notes.json           (created when you add a note)
└── watch_state.json     (created the first time you watch something)
```

Each layer is small and single-purpose, so it's easy to debug: backend logs to
the console, the download "Log" button shows raw yt-dlp output, and the JS is
split by responsibility.

---

## New API endpoints

| Method | Path                          | Purpose                                              |
|--------|--------------------------------|-------------------------------------------------------|
| GET    | `/api/subtitle_search`        | `?q=...` — search inside all subtitle files            |
| GET    | `/api/subtitle_search_in_video` | `?path=...&q=...` — search inside ONE video's subtitle |
| POST   | `/api/rename`                 | `{path, title}` — rename a video + siblings            |
| POST   | `/api/delete`                 | `{path}` — delete a video + siblings                   |
| GET    | `/api/transcribe/available`   | whether `faster-whisper` is installed + model list      |
| POST   | `/api/transcribe`             | `{path, language, model, translate, model_path}` — start a job |
| GET    | `/api/transcribe/jobs`        | list all transcription jobs                             |
| GET    | `/api/transcribe/{job_id}`    | poll a transcription job's status/progress              |
| POST   | `/api/transcribe/{job_id}/cancel` | cancel a running transcription job                  |

The `/api/download` endpoint also gained an optional `subtitles` field
(`""` = off, `"all"` = every language, or e.g. `"en,fa"`).

## 📄 License

MIT License — feel free to use and modify.