<div align="center">

# 🎧 Zimanova

### Learn the language you're watching.

Zimanova turns a folder of downloaded videos into your own private language course —
**watch** authentic video with captions, **capture** any word or line you hear
(with the audio, a snapshot, or a clip cut from that exact moment), and **drill**
it all with a real **spaced-repetition** flashcard system.

Local-first. No accounts, no cloud, no tracking — everything lives in a folder on
your machine.

<!-- Optional badges — adjust to your repo -->
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![No build step](https://img.shields.io/badge/frontend-vanilla%20JS-f7df1e)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## Why Zimanova?

Watching real video — interviews, podcasts, shows — is one of the best ways to
learn a language. The problem is **you forget 90% of the new words by tomorrow.**

Zimanova closes that loop:

1. **Watch** a video and search inside its captions.
2. **Tap a line** to save it to your **Word bank** — Zimanova cuts the matching
   **audio / screenshot / video clip** straight from that moment with `ffmpeg`.
3. **Study** your saved cards. Zimanova schedules them with the **SM-2** algorithm
   (the one behind Anki): the words you struggle with come back **daily**, and
   the ones you know spread out over **weeks and months** automatically.

You're not memorizing a word list — you're reviewing the exact moment a real
person said it.

---

## 📸 Screenshots

> _Add your own screenshots here — drop the images in a `docs/` folder and the
> links below will work._

| Study session | Word bank |
|:---:|:---:|
| ![Study](docs/study.png) | ![Word bank](docs/wordbank.png) |

| Watch + capture from captions | Library |
|:---:|:---:|
| ![Capture](docs/capture.png) | ![Library](docs/library.png) |

---

## ✨ Features

### Spaced-repetition study (the heart of Zimanova)
- A focused, full-screen **Study** session: see the word, hear its captured
  audio, try to recall it, flip the card, then rate it **Again / Hard / Good /
  Easy**.
- Faithful **SM-2 scheduler** — hard cards stay in the daily pile; mastered cards
  jump `1d → 2d → 1wk → 3wk → months`. Each rating button shows **when you'll see
  the card next** before you press it.
- **Keyboard-first:** `Space` flips the card, then `1`–`4` (or `Space` for
  *Good*) rates it. Cards you mark *Again* come back later in the same session.
- A **🔥 day-streak**, a "reviewed today" counter, and a **"due now"** badge keep
  you honest. Filter your bank by **All / Due / New / Learning / Mastered**.

### Capture words from real video
- Search inside a single video's captions ("**Find in this video**") or across
  your whole library, and save any matching line with one tap.
- Each saved card can carry an **audio clip**, a **snapshot**, and/or a **video
  clip**, cut around the subtitle's own timing. Clips are stored independently,
  so they keep working even if you later rename or delete the source video.
- Add words **manually** too, and attach your own audio/image/video files.
- Every card links back to the **exact moment** in the source video — click to
  jump straight there.

### A local YouTube for your downloads
- **Download** whole channels, playlists, or single videos by pasting a URL —
  Zimanova drives `yt-dlp` for you and sorts everything into `Category / Channel /
  video`.
- **Browse** in a clean grid with real thumbnails and durations; channels are
  auto-detected from metadata. Continue-watching, watched/unwatched filters, and
  per-video **notes**.
- A proper **player** with captions, playback-speed control (0.5×–2×), keyboard
  shortcuts, and autoplay of the next video.

### Subtitles, even when there are none
- Pull YouTube's own captions (manual or auto-generated) in any language while
  downloading.
- Or generate subtitles **offline** for any video with **Whisper**
  (`faster-whisper`) — pick the spoken language and model size, optionally
  translate to English. *(Optional feature; install only if you want it.)*

---

## 🚀 Quick start

### Prerequisites
- **Python 3.10+**
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — for downloading.
- **[ffmpeg](https://ffmpeg.org/)** — for thumbnails and for cutting the
  audio/image/video clips. _(Without it, text cards still work; you just can't
  capture clips.)_

### Install & run

```bash
# 1. Clone
git clone https://github.com/<you>/Zimanova.git
cd Zimanova

# 2. Install the Python deps
pip install -r backend/requirements.txt

# 3. Run
#    Windows:        start.bat
#    macOS / Linux:  bash start.sh
```

Then open **http://127.0.0.1:8420** in your browser.

On first run, open **Settings** and point **Library folder** at the directory
where your videos live (or where you want downloads to go).

> 💡 Optional offline subtitles: `pip install faster-whisper` and the **Generate
> subtitles** button appears on the watch page.

---

## ⌨️ Keyboard shortcuts

**While studying**
| Key | Action |
|---|---|
| `Space` | Flip the card (then = *Good*) |
| `1` / `2` / `3` / `4` | Again / Hard / Good / Easy |
| `Esc` | End the session |

**While watching**
| Key | Action |
|---|---|
| `Space` / `K` | Play / pause |
| `←` / `→` | Seek 5s · `J` / `L` seek 10s |
| `↑` / `↓` | Volume · `M` mute · `F` fullscreen |

---

## 🔒 Data & privacy

Zimanova is **local-first**. It runs entirely on your machine and stores everything
as plain files next to the app:

| File | What's in it |
|---|---|
| `dictionary.json` | Your word bank — each card and its SRS schedule |
| `srs_history.json` | Per-day review counts (powers the streak) |
| `dict_media/` | The audio / image / video clips you captured |
| `notes.json` | Per-video notes |
| `watch_state.json` | Watch progress |
| `config.json` | Your library path and settings |

Back up the whole folder and you've backed up everything. Nothing is sent
anywhere.

---

## 🛠️ How it works

```
Zimanova/
├── backend/            FastAPI app (Python)
│   ├── main.py         routes + range video streaming
│   ├── dictionary.py   word bank + ffmpeg clip capture + SM-2 spaced repetition
│   ├── library.py      scan/group videos, rename/delete, caption search
│   ├── downloader.py   yt-dlp wrapper + progress
│   ├── transcribe.py   optional offline Whisper subtitles
│   └── notes.py · progress.py · config.py
├── frontend/           Vanilla JS — no build step
│   ├── index.html
│   ├── css/styles.css
│   └── js/  (api · state · ui · app · download)
└── start.bat · start.sh
```

The scheduler is a compact, faithful SM-2. A card you keep failing stays at
`interval = 0` (due today); a card you keep passing grows by its *ease factor*
(`interval × ease`), so it accelerates out to weeks and months. Lapses lower the
ease and send the card back to relearning. See `backend/dictionary.py`.

### API (selected)
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dictionary/study` | the cards due now, with next-interval previews |
| `POST` | `/api/dictionary/{id}/review` | `{rating}` (1–4) — reschedule a card |
| `GET` | `/api/dictionary/stats` | due / new / learning / mastered / streak |
| `POST` | `/api/dictionary` | add a card (cuts clips when given a source + timing) |
| `POST` | `/api/download` | start a yt-dlp download |

---

## 🗺️ Roadmap ideas

- [ ] Import / export the word bank (CSV / Anki `.apkg`)
- [ ] Per-deck study (by language, channel, or tag)
- [ ] "Type the answer" and listening-only card modes
- [ ] Review heatmap / progress charts
- [ ] Optional dark/light themes

PRs and ideas welcome.

---

## ⚖️ Responsible use

Zimanova is a personal study tool. Only download content you have the right to
download, and respect each platform's Terms of Service and the rights of
creators. You are responsible for how you use `yt-dlp`.

---

## 🙏 Built with

[FastAPI](https://fastapi.tiangolo.com/) · [yt-dlp](https://github.com/yt-dlp/yt-dlp)
· [faster-whisper](https://github.com/SYSTRAN/faster-whisper) ·
[Fraunces](https://fonts.google.com/specimen/Fraunces) &
[Inter](https://fonts.google.com/specimen/Inter)

## 📄 License

MIT — see [`LICENSE`](LICENSE). _(Add a LICENSE file; MIT is a good default for a
project like this.)_

<div align="center">
<sub>Made for anyone learning a language the fun way — by actually watching.</sub>
</div>