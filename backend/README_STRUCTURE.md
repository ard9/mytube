# Backend structure — debugging guide / راهنمای ساختار بک‌اند

## English

The old `main.py` was one ~960-line file holding every endpoint. It is now split
so each feature lives in its own small file. When a bug appears you open (or send)
only the file that owns that feature, not the whole codebase.

### Layout

```
backend/
  main.py            ← tiny entry point (just starts the server)
  app.py             ← builds the app: logging, middleware, exception handler, wiring
  logging_setup.py   ← how logging works (request ids, levels, optional log file)
  schemas.py         ← all request body shapes (Pydantic models)
  deps.py            ← shared HTTP helpers (safe_path, range_stream)
  routers/           ← ONE FILE PER FEATURE — this is what you send when debugging
    config.py        ← /api/config
    library.py       ← /api/library, video, thumb, subtitle, rename, delete
    transcribe.py    ← /api/transcribe/*   (offline Whisper subtitles)
    tts.py           ← /api/tts/*          (StyleTTS2 speech)
    notes.py         ← /api/notes
    dictionary.py    ← /api/dictionary/*   (word bank + spaced repetition)
    downloads.py     ← /api/download, /api/downloads/*, /api/watch/*
    conversation.py  ← /api/conversation/* (speaking agent + streaming STT socket)

  # Unchanged service modules (the actual logic; already one-per-feature):
  config.py conversation.py dictionary.py downloader.py library.py
  notes.py progress.py streaming.py transcribe.py tts.py
```

A router = the thin HTTP layer (URLs, status codes). A service module
(`tts.py`, `library.py`, …) = the real work. A bug is almost always in one of
those two files for the feature, so that pair is all you need to share.

### Logging — finding a bug fast

* Every log line is tagged with the feature name and a per-request id:
  `12:00:01  INFO  [a1b2c3] mytube.api.tts  Start TTS: 142 chars ...`
  The `mytube.api.tts` part tells you which file to open.
* Every response carries an `X-Request-ID` header, and every crash returns
  `{"detail": ..., "request_id": "a1b2c3", "error": "..."}`. A user can quote
  that id and you grep the console for it to see the full traceback.
* Unhandled errors are logged with a complete traceback that points at the exact
  line/file — no more silent 500s.

### Turning logging up (no code change)

```bash
MYTUBE_LOGLEVEL=DEBUG  python backend/main.py     # everything verbose
MYTUBE_LOG_TTS=DEBUG   python backend/main.py     # only the TTS area verbose
MYTUBE_LOGFILE=server.log python backend/main.py  # also write to a rotating file
```

### Running it (unchanged)

```bash
python backend/main.py
# or
uvicorn app:app --app-dir backend
```

Nothing else changed: same URLs, same behaviour, same `config.json`. Open
`/docs` to see every endpoint grouped by feature.

---

## فارسی

قبلاً همهٔ مسیرها (endpointها) داخل یک فایل ~۹۶۰ خطی `main.py` بود. حالا هر بخش
به فایل کوچک و جدای خودش منتقل شده. وقتی باگی پیش بیاد، فقط همون فایلِ مربوط به
اون قابلیت رو باز می‌کنی یا برای مدل می‌فرستی — نه کل کد رو.

* پوشهٔ `routers/` = یک فایل برای هر قابلیت (config، library، tts، dictionary، ...).
  همینه که موقع دیباگ می‌فرستی.
* فایل‌های سرویس (مثل `tts.py`، `library.py`) منطق اصلی هستن و دست‌نخورده موندن.
* باگ تقریباً همیشه توی همین دو فایلِ یک قابلیته، پس فقط همون جفت رو لازم داری بفرستی.

### لاگ‌ها

* هر خط لاگ، نام بخش و یک شناسهٔ درخواست داره:
  `[a1b2c3] mytube.api.tts ...` — قسمت `mytube.api.tts` می‌گه کدوم فایل رو باز کنی.
* هر پاسخ یک هدر `X-Request-ID` داره و هر خطای ۵۰۰ توی بدنه‌اش `request_id` و متن
  خطا رو برمی‌گردونه. کاربر اون شناسه رو می‌گه و تو با grep توی کنسول، traceback
  کامل رو پیدا می‌کنی.
* خطاهای مدیریت‌نشده با traceback کامل لاگ می‌شن و دقیقاً خط/فایل خراب رو نشون می‌دن.

### بیشتر/کمتر کردن لاگ (بدون تغییر کد)

```bash
MYTUBE_LOGLEVEL=DEBUG  python backend/main.py     # همه‌چیز با جزئیات
MYTUBE_LOG_TTS=DEBUG   python backend/main.py     # فقط بخش TTS با جزئیات
MYTUBE_LOGFILE=server.log python backend/main.py  # نوشتن لاگ توی فایل
```

### اجرا (مثل قبل)

```bash
python backend/main.py
```

هیچ‌چیز دیگه‌ای عوض نشده: همون URLها، همون رفتار، همون `config.json`. صفحهٔ `/docs`
هم همهٔ مسیرها رو گروه‌بندی‌شده بر اساس قابلیت نشون می‌ده.
