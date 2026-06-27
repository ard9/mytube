# Echo — learn the language you're watching

> Formerly *MyTube*. Same engine, reimagined as a **language-learning platform**:
> turn a folder of downloaded videos into your own study material, capture the
> words and lines you hear, and drill them with a real **spaced-repetition**
> flashcard system.

A local web app that turns a folder of downloaded videos into a YouTube-style
library, and lets you download whole channels with `yt-dlp` from the browser.

- **Watch** authentic video with a proper player and captions (`.srt`/`.vtt`).
- **Capture** any word or sentence you hear straight into your **Word bank** —
  with the audio, a snapshot, or a video clip cut from that exact moment.
- **Study** what you captured with **Echo's spaced-repetition flashcards**
  (Again / Hard / Good / Easy) — hard cards come back daily, words you know
  spread out over weeks and months, just like Anki.
- **Browse** your videos in a grid (real thumbnails + durations); channels are
  auto-detected and sectioned. **Notes** on every video, saved server-side.
- **Add channel**: paste a YouTube URL and it downloads into your library,
  sorted by channel — using the same `yt-dlp` settings as your command.

---

## What's new

**Live conversation (speaking-practice agent)**
- A new **Live conversation** entry under *Learn* (🎙️) lets you *talk* with an
  AI English tutor about everyday life and strengthen your speaking.
- **Full voice loop:** your microphone → speech-to-text → the tutor (a Large
  Language Model) → its reply is shown live **and** spoken back to you.
- **Two input modes:** the browser's own live recognition (Google-powered, works
  great in Chrome, nothing to install) or **Whisper** on the server.
- **Two voice modes for the tutor:** the browser's built-in speech, or the app's
  **StyleTTS2** voice.
- **Learns like a tutor:** every turn it gently catches your real mistakes
  (grammar, word choice, naturalness) and explains each fix **in Persian**, shown
  in a tidy "Corrections" panel under its reply.
- **Many chats:** start a brand-new conversation any time; old ones are saved and
  listed so you can pick up where you left off.
- **Bring your own free key:** open **AI settings** and paste a key from
  **OpenRouter**, **Gemini**, or **any OpenAI-compatible** endpoint — no key is
  ever hard-coded, and keys are stored only on your machine. Pick your level
  (auto / beginner / intermediate / advanced) too.


**Text to speech (StyleTTS2, offline, English)**
- A new **Text to speech** entry under *Learn* turns any English text — even a
  whole article — into natural speech using **StyleTTS2**, running **entirely on
  your own machine** (no API key, offline after the model downloads once). Paste
  text, optionally name it, pick a voice, and press **Generate speech**.
- **Long text just works.** It's split into sentence-sized chunks, each is
  synthesised, and the pieces are stitched back together with a short natural
  pause — so you get one continuous audio file however long the input.
- **Two voice modes:** the built-in **default voice**, or **clone a voice** from
  a short (5–15s) clean clip you upload. Saved voices are reusable and selectable.
- Every clip is saved into a **library** on the page: play it inline, **download**
  it (mp3 when ffmpeg is present, else wav), or **add it straight to your Word
  bank** — a flashcard is created with the text and the generated audio attached,
  and it enters the spaced-repetition schedule just like any other card.
- **Read along while it plays.** Clips show a synced transcript: as the audio
  plays, the sentence being spoken is highlighted (karaoke-style) and scrolls
  into view. Tap any sentence to play from there.
- **Capture just the part you want.** From the transcript, tap a sentence and
  press *Add line to Word bank* — only that sentence is saved, with its **own
  audio clip trimmed to that exact moment** (cut with ffmpeg), and the edit box
  opens so you can type its **meaning**. Adding the whole text now opens the same
  box, so every TTS card can have a meaning.
- **Advanced options** trade quality for speed (diffusion steps) and dial
  expressiveness up or down. Progress is a background job, like downloads and
  subtitles.
- **Choose where audio is saved.** A *Save audio to folder* box on the page lets
  you point new clips at any folder you like (e.g. an external drive); leave it
  blank for the built-in default. Each clip remembers exactly where it was
  written, so changing the folder never breaks clips you already made.
- Needs the optional `styletts2` package (see Requirements). If it isn't
  installed, the page says exactly what to run. CPU works; a GPU is much faster.


**A spaced-repetition study system (the Word bank is now a real SRS deck)**
- Every saved word/sentence is a **flashcard** with its own schedule. The new
  **Study** screen runs a focused review session: you see the word (and hear its
  captured audio), try to recall it, flip the card, then rate how it went with
  **Again / Hard / Good / Easy** (or keys `1`–`4`; `Space` flips, then `Space`
  is *Good*).
- The scheduler is a faithful **SM-2** (the algorithm behind Anki): cards you
  keep missing stay in the **daily** pile, while cards you know well fly out to
  `1d → 2d → 1wk → 3wk → months`. Each rating button shows *when you'll see the
  card next* before you press it.
- The **Word bank** header shows your **🔥 day streak**, how many you reviewed
  today, and a **"Start studying"** button with the count of cards **due now**.
  Filter the bank by **All / Due / New / Learning / Mastered**, and every card
  shows a colour-coded status and its next review time.
- Schedules live in each entry's `srs` field inside `dictionary.json`; your daily
  review counts (for the streak) live in `srs_history.json`. Older dictionaries
  are migrated automatically the first time they load — nothing to do.

**A fresh look — "Echo"**
- New warm study-night theme, a bookish display face (Fraunces) for the words
  you're learning, and a **mastery colour spectrum** (Again → Hard → Good →
  Easy) reused everywhere so a card's colour always tells you how well you know
  it. The sidebar now leads with **Learn** (Study + Word bank).

**Dictionary / Word bank (words & sentences, with audio / snapshot / video clips)**
- A personal **Word bank** (the **📚** sidebar entry) for the words and sentences
  you're learning. Three ways to fill it:
  - **From a caption search inside a video** — when you use *Find in this
    video*, every matching line now has a small **📖+** button. Tap it and
    that exact sentence is saved to your dictionary. You choose what to
    capture from that moment: an **audio clip**, a **snapshot image**, a
    **video clip**, or any combination — all cut straight from the video
    with `ffmpeg` around the subtitle's own start/end times.
  - **From the library-wide CC search** — the same **📖+** button sits on
    each caption-search result card, so you can save a line without even
    opening the video.
  - **Manually** — the **Add word** button on the Dictionary page lets you
    type any word/sentence and its meaning by hand (no video needed). You can
    also **attach your own audio, image, or video file** from your computer to
    a manual entry — and you can add/replace/remove media on *any* entry later
    from its **Edit** dialog.
- Every entry has an editable **meaning / translation** field (leave it blank
  and fill it in later if you like), plays its saved audio/video inline, shows
  its snapshot, and links back to the **exact moment** in the source video it
  came from (click the source line to jump there). Filter the whole dictionary
  with the search box; edit or delete any entry.
- **Quick self-test on the Word bank** — meanings are **hidden by default**; tap
  a card's meaning area to reveal it (tap again to hide), so you can test
  yourself on the word/audio first. A **Show meanings / Hide meanings** button
  flips them all at once. For a proper scheduled review session, use **Study**
  (see above).
- The captured clips are stored independently of the source video (in
  `dict_media/`), so even if you later **rename or delete** the original
  video, the clip you saved keeps working. (Renaming a video also keeps the
  "jump to source" link pointing at the right place.)
- Clip capture needs **ffmpeg** (which you already have for downloads). If
  ffmpeg isn't available, entries still save fine as text + meaning — the app
  just tells you clips couldn't be cut.

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

**Optional — for the "Text to speech" page** (local StyleTTS2, English, works
fully offline after the first download):
```
pip install styletts2
```
Not needed for anything else in Echo. This pulls in PyTorch and other ML
packages, so it's a sizeable install. The first time you generate speech the
StyleTTS2 model is downloaded automatically (a few hundred MB) from Hugging Face
and cached. The default phonemizer is `gruut` (pure Python), so no system
`espeak` is needed. CPU works fine; an NVIDIA GPU (CUDA-enabled PyTorch) is much
faster. **English only** — the public StyleTTS2 models are English; there's no
official model for other languages. Upload a short clean reference clip on the
page to clone a specific voice, otherwise the built-in default voice is used.

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
│   ├── dictionary.py    word/sentence bank, ffmpeg clip capture + SM-2 spaced repetition
│   ├── progress.py      per-video watch progress (watch_state.json)
│   ├── downloader.py    yt-dlp subprocess wrapper + progress parsing (incl. subtitles)
│   ├── transcribe.py    local Whisper subtitle generation (optional, background jobs)
│   ├── tts.py           local StyleTTS2 text-to-speech (optional, background jobs) + saved audio/voices
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
├── dictionary.json      (created when you add a word; holds each card's SRS schedule)
├── srs_history.json     (per-day review counts — powers the study streak)
├── dict_media/          (audio/image/video clips saved into the word bank)
├── tts_library.json     (created when you generate speech — saved audio clips)
├── tts_media/           (the generated speech audio files)
├── tts_voices.json      (created when you save a voice to clone)
├── tts_voices/          (uploaded reference-voice samples)
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

