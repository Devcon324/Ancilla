# Ancilla

## Tech showcase

| Platform | Voice stack | Integrations |
|:---------|:------------|:-------------|
| <a href="https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/"><img src="https://img.shields.io/badge/NVIDIA-Jetson-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="NVIDIA Jetson" /></a> | <a href="https://github.com/ggerganov/whisper.cpp"><img src="https://img.shields.io/badge/whisper.cpp-STT-1C1C1C?style=for-the-badge&logo=openai&logoColor=white" alt="whisper.cpp" /></a> | <a href="https://pipewire.org/"><img src="https://img.shields.io/badge/PipeWire-Audio-7C3AED?style=for-the-badge&logo=linux&logoColor=white" alt="PipeWire" /></a> |
| <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" /></a> | <a href="https://github.com/ggerganov/llama.cpp"><img src="https://img.shields.io/badge/llama.cpp-LLM-000000?style=for-the-badge&logo=meta&logoColor=white" alt="llama.cpp" /></a> | <a href="https://mpv.io/"><img src="https://img.shields.io/badge/mpv-Music-6B46C1?style=for-the-badge&logo=vlcmediaplayer&logoColor=white" alt="mpv" /></a> |
| <a href="https://developer.nvidia.com/cuda-zone"><img src="https://img.shields.io/badge/CUDA-GPU_offload-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="CUDA" /></a> | <a href="https://qwenlm.github.io/"><img src="https://img.shields.io/badge/Qwen2.5-3B_Instruct-FF6A00?style=for-the-badge&logo=alibabadotcom&logoColor=white" alt="Qwen" /></a> | <a href="https://github.com/yt-dlp/yt-dlp"><img src="https://img.shields.io/badge/yt--dlp-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="yt-dlp" /></a> |
| <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/uv-package_manager-DE5FE9?style=for-the-badge&logo=python&logoColor=white" alt="uv" /></a> | <a href="https://github.com/rhasspy/piper"><img src="https://img.shields.io/badge/Piper-TTS-5B2C6F?style=for-the-badge&logo=soundcloud&logoColor=white" alt="Piper" /></a> | <a href="https://open-meteo.com/"><img src="https://img.shields.io/badge/Open--Meteo-Weather-0EA5E9?style=for-the-badge&logo=cloud&logoColor=white" alt="Open-Meteo" /></a> |
| | <a href="https://github.com/dscripka/openWakeWord"><img src="https://img.shields.io/badge/openWakeWord-hey_jarvis-0D9488?style=for-the-badge&logo=googleassistant&logoColor=white" alt="openWakeWord" /></a> | <a href="https://www.openstreetmap.org/"><img src="https://img.shields.io/badge/OpenStreetMap-Store_hours-7EBC6F?style=for-the-badge&logo=openstreetmap&logoColor=white" alt="OpenStreetMap" /></a> |
| | <a href="https://github.com/snakers4/silero-vad"><img src="https://img.shields.io/badge/Silero-VAD-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white" alt="Silero VAD" /></a> | <a href="https://duckduckgo.com/"><img src="https://img.shields.io/badge/DuckDuckGo-Search-DE5833?style=for-the-badge&logo=duckduckgo&logoColor=white" alt="DuckDuckGo" /></a> |
| | | <a href="https://www.navidrome.org/"><img src="https://img.shields.io/badge/Navidrome-Optional-039BE5?style=for-the-badge&logo=audiomack&logoColor=white" alt="Navidrome" /></a> |

---

![Architecture diagram](docs/architecture.svg)

**Ancilla** is a local-first, offline-friendly voice assistant. Say **"hey jarvis"**, ask a question, and get a spoken reply - wake word, speech-to-text, routing, language model, and text-to-speech all run on your own hardware.

Built for **NVIDIA Jetson Orin Nano** (also runnable on Raspberry Pi and Linux desktops). Develop on Windows if you like; deploy on ARM when you are ready.

The trick that keeps a small 3B model useful: a **hybrid intent router** answers factual requests (time, weather, store hours, music, volume, web search) from real data sources and only asks the LLM to *phrase* the answer or handle open-ended chat. The model never guesses store hours or weather from memory.

| Layer | What we use |
|-------|-------------|
| Hardware | NVIDIA Jetson Orin Nano (primary), Raspberry Pi / Linux x86 (CPU builds) |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) - `hey_jarvis` |
| End-of-speech | [Silero VAD](https://github.com/snakers4/silero-vad) |
| STT | [whisper.cpp](https://github.com/ggerganov/whisper.cpp) `whisper-server` + `ggml-base.en` |
| LLM | [llama.cpp](https://github.com/ggerganov/llama.cpp) `llama-server` + **Qwen2.5-3B-Instruct Q4_K_M** |
| TTS | [Piper](https://github.com/rhasspy/piper) ONNX voices (e.g. `en_US-hfc_male-medium`) |
| Audio I/O | PipeWire / ALSA via `sounddevice`; music via **mpv** |
| Music | SomaFM / Radio Browser, optional **Navidrome**, hobby **yt-dlp** YouTube |
| Facts | Open-Meteo, OpenStreetMap/Overpass, DuckDuckGo |

---

## Performance (measured)

Benchmarks collected on a **Jetson Orin Nano Super** (~7.4 GiB RAM, JetPack R39, aarch64) with the default stack:

| Component | Model / binary | On-disk size | Idle RSS |
|-----------|----------------|--------------|----------|
| STT server | `ggml-base.en.bin` via whisper-server `:8080` | ~142 MB | ~**710 MB** |
| LLM server | `qwen2.5-3b-instruct-q4_k_m.gguf` via llama-server `:8081` (`--n-gpu-layers 999`, ctx 2048) | ~2.0 GB | ~**2.8 GB** |
| Assistant process | wake + VAD + Piper after `startup_check` | - | ~**720 MB** |
| **System (all loaded)** | whisper + llama + assistant | - | ~**5.1 / 7.4 GiB** used (~2.3 GiB available) |

| Path | Latency (3 runs) |
|------|------------------|
| Whisper `base.en` STT (~1.5 s of audio) | **0.16-0.38 s** (avg **0.24 s**) |
| Qwen2.5-3B short chat | **0.40-0.50 s** (avg **0.44 s**) |
| Qwen2.5-3B weather-style phrasing | **0.77-1.35 s** (avg **1.09 s**) |
| Piper in-process synth (~3 s of speech) | **0.37-0.50 s** (avg **0.42 s**) |
| Fast-path time / volume | ~1 ms / ~50 ms (no LLM) |
| Weather API fetch | ~0.5 s (no LLM for data; LLM only for phrasing when used) |
| `startup_check` (VAD + wake + TTS load) | ~4 s after import |

Idle board power sample (`tegrastats`): ~**3.3 W** VDD_IN, GPU ~0% when waiting for wake word.

> Numbers are wall-clock on this device with warm servers. Piper CLI subprocess synth is slower (~4 s); the app uses in-process Piper. End-to-end "hey jarvis -> spoken answer" also includes mic capture and network for weather/search.

---

## Architecture

Flow: your voice is captured after the wake word, transcribed locally by whisper.cpp, and handed to the intent router. Instant intents (time, music, volume, store hours) answer without touching the LLM. Weather and web-search results are fetched from real APIs and passed to the LLM only for natural phrasing. Anything open-ended goes straight to the LLM. The reply is streamed sentence-by-sentence to Piper, and a concurrent wake-word listener lets you **barge in** mid-response (with STT confirmation so Bluetooth speaker bleed does not false-trigger).

### Pieces and how they connect

| Stage | Module | Talks to |
|-------|--------|----------|
| Wake word | `ancilla/audio/wake_word.py` | openWakeWord (`hey_jarvis`), always listening |
| Capture | `ancilla/audio/capture.py` | Silero VAD for end-of-speech detection |
| Speech-to-text | `ancilla/services/stt_client.py` | **whisper.cpp** `whisper-server` on `:8080` |
| Routing | `ancilla/intent_router.py` | keyword/regex fast paths + LLM tool-select |
| Language model | `ancilla/services/llm_client.py` | **llama.cpp** `llama-server` on `:8081` |
| Text-to-speech | `ancilla/services/tts_client.py` | **Piper** in-process + `.onnx` voice |
| Conversation | `ancilla/conversation.py` | short rolling history for follow-ups |
| Orchestration | `ancilla/main.py` / `cli.py` | wake -> record -> STT -> route -> speak loop |

### Tools the router can reach

| Tool | Module | Backend | API key |
|------|--------|---------|---------|
| Clock | `ancilla/intent_router.py` | local system time + timezone | none |
| Weather | `ancilla/services/weather_client.py` | Open-Meteo | none |
| Store hours | `ancilla/services/store_hours_client.py` | OpenStreetMap / Overpass | none |
| Web search | `ancilla/services/search_client.py` | DuckDuckGo via `ddgs` | none |
| Volume | `ancilla/services/volume_client.py` | PipeWire `wpctl` | none |
| Music | `ancilla/services/music_client.py` | Navidrome / YouTube / radio + `mpv` | Navidrome optional |
| General chat | `ancilla/services/llm_client.py` | local llama.cpp model | none |

## Common abilities

Say **"hey jarvis"** first, then any of these:

- **Time** - "what time is it" *(instant, no LLM)*
- **Weather** - "what's the weather", "what's the weather in Berlin" *(Open-Meteo)*
- **Store hours** - "is Walmart open", "when does Canadian Tire close today" *(OpenStreetMap)*
- **Volume** - "turn it up", "set volume to 30", "what's the volume"
- **Music** - "play lo-fi", "stop", "nevermind" *(radio / optional YouTube or Navidrome + mpv)*
- **Web search** - "who won the game last night" *(DuckDuckGo -> LLM)*
- **General chat** - "tell me a joke", "explain photosynthesis" *(local LLM)*
- **Follow-ups** - answer without repeating the wake word when Jarvis asks something back
- **Barge-in** - say "hey jarvis" while it is talking to interrupt (confirmed via STT)

## Configuration

Two files, kept out of source control:

- **`config/defaults.json`** - location, timezone, assistant name, units, time format. Copy from [`config/defaults.example.json`](config/defaults.example.json).
- **`.env`** - secrets, local paths, machine-specific overrides. Copy from [`.env.example`](.env.example).

Environment variables override `config/defaults.json` when both set the same value.

---

## Setup - Linux (Jetson Orin Nano or Raspberry Pi)

Works on **Jetson Orin Nano** (CUDA recommended) and **Raspberry Pi 4/5** (CPU builds; pick smaller models - see [footnotes](#footnotes---recommended-models-by-hardware)).

### 1. Prerequisites

```bash
# Jetson: flash JetPack 6.x first, then:
sudo apt update
sudo apt install -y git cmake build-essential ffmpeg mpv pipewire wireplumber

# Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Clone and create the Python env:

```bash
git clone https://github.com/Devcon324/Ancilla.git
cd Ancilla
uv sync
cp .env.example .env
cp config/defaults.example.json config/defaults.json
```

Edit `config/defaults.json` (city, timezone) and `.env` (`PIPER_VOICE`, audio devices).

### 2. Download models

```bash
mkdir -p models/whisper models/llm models/piper/en/american

# STT - base.en (~142 MB). Use tiny.en on 4GB Jetson / Pi if RAM is tight.
curl -L -o models/whisper/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

# LLM - Qwen2.5-3B Instruct Q4_K_M (~2 GB). Prefer 1.5B/1B on 4GB or Pi.
curl -L -o models/llm/qwen2.5-3b-instruct-q4_k_m.gguf \
  https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf

# TTS - Piper medium voice (~61 MB) + matching .json sidecar from Hugging Face
# https://huggingface.co/rhasspy/piper-voices
```

Set in `.env`:

```bash
PIPER_VOICE=/absolute/path/to/en_US-hfc_male-medium.onnx
PIPER_BIN=$(pwd)/.venv/bin/piper
LLAMA_MODEL_NAME=qwen2.5-3b-instruct-q4_k_m
```

### 3. Build whisper.cpp and llama.cpp

**Jetson (CUDA):**

```bash
git clone https://github.com/ggerganov/whisper.cpp ~/whisper.cpp
cmake -S ~/whisper.cpp -B ~/whisper.cpp/build -DGGML_CUDA=1
cmake --build ~/whisper.cpp/build -j"$(nproc)" --config Release

git clone https://github.com/ggerganov/llama.cpp ~/llama.cpp
cmake -S ~/llama.cpp -B ~/llama.cpp/build -DGGML_CUDA=1
cmake --build ~/llama.cpp/build -j"$(nproc)" --config Release
```

**Raspberry Pi / CPU-only Linux** - omit `-DGGML_CUDA=1` (plain `cmake -S ... -B ...` then build). Expect slower STT/LLM; use the smaller model set in the footnotes.

### 4. Audio devices

```bash
# List capture/playback devices
uv run python -c "import sounddevice as sd; print(sd.query_devices())"
```

On modern Jetson/Ubuntu, PipeWire is a good default:

```bash
ASSISTANT_MIC_DEVICE=pipewire
ASSISTANT_SPEAKER_DEVICE=pipewire
```

Leave blank to use the system default. Plug in a USB mic; Bluetooth speakers work but may need A2DP enabled (see `docs/jetson/bluetooth-speaker-ssh.md`).

### 5. Start servers and the assistant

Three terminals (or wrap the servers in `systemd`):

```bash
# Terminal 1 - STT
~/whisper.cpp/build/bin/whisper-server \
  -m "$PWD/models/whisper/ggml-base.en.bin" \
  --host 127.0.0.1 --port 8080

# Terminal 2 - LLM
~/llama.cpp/build/bin/llama-server \
  -m "$PWD/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf" \
  --host 127.0.0.1 --port 8081 \
  --n-gpu-layers 999 --ctx-size 2048
# On Pi/CPU: use --n-gpu-layers 0

# Terminal 3 - assistant
uv run ancilla
```

Say **"hey jarvis"**, then try `what time is it`.

Optional: set `RESOURCE_LOG_INTERVAL_SECONDS=30` in `.env` to watch CPU/RAM while tuning.

---

## Setup - Windows (development)

See [`docs/setup/windows-demo.md`](docs/setup/windows-demo.md) for the full walkthrough.

```powershell
uv sync
copy .env.example .env
copy config\defaults.example.json config\defaults.json
```

Download Windows `whisper-server` / `llama-server` binaries, point `.env` at your Piper voice, start both servers on `:8080` / `:8081`, then:

```powershell
uv run ancilla
```

---

## Project layout

```
src/ancilla/           installable Python package
  audio/               wake word, capture, VAD helpers
  services/            STT, LLM, TTS, weather, music, search, ...
  cli.py / main.py     entrypoints (uv run ancilla)
  intent_router.py     hybrid routing
  config.py            .env + config/defaults.json
config/                defaults.example.json (copy to defaults.json)
docs/                  architecture.svg, jetson/, setup/
scripts/               run, setup-env, download-models, audio-test, status, ...
models/                whisper / llm / piper (gitignored)
tests/
```

## Known rough edges

- **4GB Jetson / Pi 4**: whisper + 3B LLM together is tight - shrink models (footnotes below).
- Bluetooth speakers can bleed into the mic; barge-in uses STT confirmation of "jarvis".
- `mpv` must be on `PATH`; music is single-stream (new play replaces the previous track).
- YouTube music is a hobby path (`MUSIC_YOUTUBE_ENABLED`); turn it off or remove `youtube_music.py` if you do not want it.

## Releases (Jetson Orin servers)

Prebuilt CUDA `whisper-server` / `llama-server` bundles can be cut on a Jetson with:

```bash
./scripts/package-jetson-release.sh
# optional signing:
# ANCILLA_RELEASE_GPG_KEY=YOURKEYID ./scripts/package-jetson-release.sh
```

Artifacts go to `dist/` (`.tar.gz` + `.sha256`, optional `.asc`). Users must verify checksums before install. Full trust model and steps: [`docs/setup/secure-releases.md`](docs/setup/secure-releases.md). Models are downloaded separately with pinned SHA-256 via `./scripts/download-models.sh`.

---

## Footnotes - recommended models by hardware

Measured on Jetson Orin Nano Super (~7.4 GiB): with **Whisper base.en** (~710 MB RSS) + **Qwen2.5-3B Q4** (~2.8 GB RSS) + assistant (~720 MB), the board sits around **5.1 GiB used / ~2.3 GiB free** while idle. That leaves little headroom for browser tabs, Navidrome on-device, or a larger LLM - but it is comfortable for the voice loop itself.

### Pick a stack that fits your RAM

| Device | Whisper | LLM (GGUF) | Piper | Notes |
|--------|---------|------------|-------|-------|
| **Jetson Orin Nano 8GB / Super** | `base.en` | **Qwen2.5-3B Q4_K_M** (this repo's default) | medium | Best balance of quality vs speed on CUDA; ~5 GB resident |
| **Jetson Orin Nano 4GB** | `tiny.en` | **Qwen2.5-1.5B Q4** or **Llama 3.2 1B Q4** | medium or low | If `cudaMalloc` fails, drop STT or LLM first; keep ctx <=2048 |
| **Raspberry Pi 5 (8GB)** | `tiny.en` or `base.en` | **Qwen2.5-1.5B Q4** or **1B Q4** | medium | CPU-only; expect multi-second LLM replies; `--n-gpu-layers 0` |
| **Raspberry Pi 4 (4GB)** | `tiny.en` | **1B Q4** only | low/medium | Prefer fast paths (time/weather/music); keep chat short |
| **x86 laptop (16GB+)** | `base.en` or `small.en` | 3B-7B Q4/Q5 | medium | CUDA/Vulkan if available; larger whisper helps noisy mics |

### Why these choices

1. **STT dominates perceived snappiness less than you think** - `base.en` averaged **~0.24 s** on Jetson for a short clip; upgrading to `small.en` costs a lot of RAM for little everyday gain. Prefer `tiny.en` when memory is the bottleneck.
2. **LLM size dominates RAM** - the 3B Q4 weights are ~2 GB on disk and ~2.8 GB RSS with llama-server + GPU layers. A 7B model will crowd out whisper on 8GB; stick to **<=3B Q4** on Jetson Nano-class boards.
3. **Hybrid routing is the real win** - time, volume, music, and store hours skip the LLM. Weather uses Open-Meteo then a short phrasing call (~1 s). Investing in tools beats upgrading from 3B -> 7B for this assistant.
4. **Piper medium is cheap** - ~61 MB on disk; in-process synth ~**0.4 s** for ~3 s of audio. Prefer medium quality over a larger LLM if you must choose.
5. **Watch live usage** - set `RESOURCE_LOG_INTERVAL_SECONDS=30` and use `tegrastats` (Jetson) or `htop` (Pi) while saying a few wake phrases. If available RAM drops under ~500 MB, shrink whisper or the LLM before chasing latency.

### Suggested downloads

| Role | Conservative (4GB / Pi) | Balanced (8GB Jetson) | Roomier host |
|------|-------------------------|------------------------|--------------|
| Whisper | [ggml-tiny.en.bin](https://huggingface.co/ggerganov/whisper.cpp/tree/main) | [ggml-base.en.bin](https://huggingface.co/ggerganov/whisper.cpp/tree/main) | `small.en` |
| LLM | [Qwen2.5-1.5B Q4](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) or Llama 3.2 1B Q4 | [Qwen2.5-3B Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF) | 7B Q4 only if >=16 GB |
| Piper | [piper-voices](https://huggingface.co/rhasspy/piper-voices) `*-low` / `*-medium` | `*-medium` | any |

After swapping GGUF files, update `LLAMA_MODEL_NAME` in `.env` to match what llama-server reports, and keep `--ctx-size 2048` unless you have spare RAM.
