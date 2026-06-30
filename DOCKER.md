# Running Echo / MyTube in Docker — راهنمای داکر

## English

### What you get
A single GPU-enabled container that serves the whole app on
`http://localhost:8420`. Your **videos** and your **app data** stay on the host,
so rebuilding the image never loses anything.

### Prerequisites
* **NVIDIA GPU + driver** on the host.
* **Docker** with GPU support:
  * **Windows:** Docker Desktop with the WSL2 backend (recent NVIDIA drivers
    expose the GPU to WSL2 automatically — no extra toolkit needed).
  * **Linux:** install the `nvidia-container-toolkit` package, then
    `sudo systemctl restart docker`.
* Verify the GPU is visible to Docker:
  ```bash
  docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
  ```
  If that prints your GPU table, you're good.

### Start it
From the project folder (the one with the `Dockerfile`):

```bash
# point this at your real video folder first:
#   Windows PowerShell:  $env:LIBRARY_PATH="D:/English/youtube_english"
#   Linux/macOS:         export LIBRARY_PATH=/home/you/videos
docker compose up --build
```

Then open <http://localhost:8420>.

The first run is slow: it downloads PyTorch/CUDA wheels at build time, and the
StyleTTS2 + Whisper model weights on first use (these are cached in the
`echo_cache` volume, so later runs are fast).

### Where your stuff lives
| Inside container | What | Persisted as |
|---|---|---|
| `/library` | your videos (read) | bind mount → `LIBRARY_PATH` |
| `/data` | config, sessions, dictionary, generated TTS, watch progress | named volume `echo_data` |
| `/cache` | downloaded ML models | named volume `echo_cache` |

### Confirm GPU is actually used
Watch the logs (`docker compose logs -f`) the first time you use each feature:
* TTS: `StyleTTS2 device = GPU (...)`
* STT: `Whisper '...' ready on cuda.`
Or open `http://localhost:8420/api/conversation/whisper_info` → `"device": "cuda"`.

### Common commands
```bash
docker compose up -d --build     # run in background
docker compose logs -f           # follow logs
docker compose down              # stop (keeps your volumes/data)
docker compose down -v           # stop AND delete echo_data/echo_cache (wipes data!)
```

### Local LLMs (Ollama / llama models)
The compose stack **includes Ollama** as a second container, so you can run
local language models (llama3.2, qwen, mistral, …) on your GPU — no API key
needed. The app reaches it automatically at `http://ollama:11434`.

Pull a model once (stored in the `ollama_models` volume):
```bash
docker compose exec ollama ollama pull llama3.2:3b
```
Then in the app's **AI settings** choose provider **Ollama** and that model.
(You can also pull models from inside the AI-settings page.)

Don't want local LLMs (you'll use OpenRouter/Gemini/OpenAI keys instead)?
Delete the `ollama:` service and the `depends_on: [ollama]` line from
`docker-compose.yml` — the app runs fine without it.

### No NVIDIA GPU? (CPU-only)
It still works, just slower. Remove the `deploy:` blocks from
`docker-compose.yml` and, in the `Dockerfile`, swap the base image line to
`FROM python:3.12-slim` and change the torch line to
`pip install torch torchaudio` (CPU wheels). Everything else is identical.
(Ollama also runs on CPU, just slower.)

---

## فارسی

### چی به دست می‌آوری
یک کانتینر GPU‌دار که کل برنامه را روی `http://localhost:8420` بالا می‌آورد.
**ویدیوها** و **دیتای برنامه** بیرون کانتینر (روی هاست) می‌مانند، پس هر بار build
دوباره چیزی پاک نمی‌شود.

### پیش‌نیازها
* کارت **NVIDIA** و درایورش روی هاست.
* **Docker** با پشتیبانی GPU:
  * **ویندوز:** Docker Desktop با بک‌اند WSL2 (درایورهای جدید NVIDIA خودشان GPU را
    به WSL2 می‌دهند؛ نیازی به نصب چیز اضافه نیست).
  * **لینوکس:** پکیج `nvidia-container-toolkit` را نصب کن و داکر را ری‌استارت کن.
* تست اینکه داکر GPU را می‌بیند:
  ```bash
  docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
  ```
  اگر جدول کارت گرافیک را نشان داد، آماده‌ای.

### اجرا
از داخل پوشهٔ پروژه (همان که `Dockerfile` دارد):

```bash
# اول مسیر واقعی ویدیوهایت را بده:
#   PowerShell ویندوز:  $env:LIBRARY_PATH="D:/English/youtube_english"
#   لینوکس/مک:          export LIBRARY_PATH=/home/you/videos
docker compose up --build
```

بعد <http://localhost:8420> را باز کن.

اجرای اول کند است: موقع build وزن‌های PyTorch/CUDA دانلود می‌شوند و مدل‌های
StyleTTS2 و Whisper هم در اولین استفاده (که در ولوم `echo_cache` کش می‌شوند، پس
دفعات بعد سریع است).

### چیزهایت کجا می‌مانند
| داخل کانتینر | چی | ماندگار با |
|---|---|---|
| `/library` | ویدیوها (خواندن) | بایند مونت → `LIBRARY_PATH` |
| `/data` | کانفیگ، سشن‌ها، دیکشنری، صداهای ساخته‌شده، پیشرفت تماشا | ولوم `echo_data` |
| `/cache` | مدل‌های دانلودشده | ولوم `echo_cache` |

### مطمئن شو GPU واقعاً استفاده می‌شود
لاگ‌ها را ببین (`docker compose logs -f`)؛ اولین بار که هر قابلیت را اجرا کنی:
* TTS: `StyleTTS2 device = GPU (...)`
* STT: `Whisper '...' ready on cuda.`
یا `http://localhost:8420/api/conversation/whisper_info` را باز کن → باید
`"device": "cuda"` باشد.

### دستورهای پرکاربرد
```bash
docker compose up -d --build     # اجرا در پس‌زمینه
docker compose logs -f           # دیدن زندهٔ لاگ‌ها
docker compose down              # توقف (دیتا/ولوم‌ها می‌مانند)
docker compose down -v           # توقف و حذف echo_data/echo_cache (دیتا پاک می‌شود!)
```

### مدل‌های زبانی لوکال (Ollama / مدل‌های llama)
کانفیگ compose یک کانتینر دوم به نام **Ollama** هم دارد، پس می‌توانی مدل‌های
زبانی لوکال (llama3.2، qwen، mistral و...) را روی GPU اجرا کنی — بدون API key.
برنامه خودکار از طریق `http://ollama:11434` به آن وصل می‌شود.

یک مدل را یک‌بار دانلود کن (در ولوم `ollama_models` ذخیره می‌شود):
```bash
docker compose exec ollama ollama pull llama3.2:3b
```
بعد در **AI settings** برنامه، provider را **Ollama** و همان مدل را انتخاب کن
(از داخل خود صفحهٔ AI settings هم می‌توانی مدل دانلود کنی).

مدل لوکال نمی‌خواهی (با کلید OpenRouter/Gemini/OpenAI کار می‌کنی)؟ سرویس
`ollama:` و خط `depends_on: [ollama]` را از `docker-compose.yml` حذف کن — برنامه
بدون آن هم درست کار می‌کند.

### کارت NVIDIA نداری؟ (فقط CPU)
باز هم کار می‌کند، فقط کندتر. بخش‌های `deploy:` را از `docker-compose.yml` بردار و
در `Dockerfile` خط ایمیج پایه را به `FROM python:3.12-slim` و خط torch را به
`pip install torch torchaudio` (نسخهٔ CPU) تغییر بده. بقیه‌اش همان است.
(Ollama هم روی CPU اجرا می‌شود، فقط کندتر.)
