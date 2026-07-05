# Jetson Voice Assistant

A local-first Alexa replacement: wake word → STT → hybrid intent routing
(time / weather / store hours / music / web search / general chat) → streaming TTS,
with barge-in support to interrupt mid-response.

The assistant code lives in the root-level [audio/](audio) and
[services/](services) packages, with the launcher entrypoint in
[src/jetson_assistant/cli.py](src/jetson_assistant/cli.py). The main runtime is
still the root-level Python modules plus the `jetson-assistant` console script.

This project already includes `piper-tts`, `sounddevice`, and the other runtime
Python dependencies in [pyproject.toml](pyproject.toml). After `uv sync`, you
should not need to add them again unless you change the dependency list.

The wake word phrase is **"hey jarvis"** (openWakeWord built-in model `hey_jarvis`).

## Architecture

```text
Mic input ─▶ audio/wake_word.py (preloaded model, always listening)
                │ wake word detected
                ▼
      audio/capture.py (VAD-based recording of your utterance)
                │
                ▼
    services/stt_client.py ──▶ whisper-server (whisper.cpp, port 8080)
                │ text
                ▼
         intent_router.py (hybrid routing)
            ├─ "what time" ─────────────▶ local clock (instant)
            ├─ "weather in X" ───────────▶ Open-Meteo geocode + forecast ──┐
            ├─ "is X open" ──────────────▶ Google Places ──────────────────┤
            ├─ "play X" / "stop" ────────▶ Navidrome (instant)              │
            └─ everything else ──▶ LLM tool-select ──┬─────────────────────┘
                                    web_search ──▶ ddgs ───────────────────┤
                                    answer ────────────────────────────────┘ │
                                                           ▼ ▼
                                                   services/llm_client.py ──▶ llama-server
                                                     (/v1/chat/completions, streaming)
                │ spoken reply (sentence-chunked)
                ▼
         services/tts_client.py ──▶ Piper ──▶ speaker
                │ (concurrent wake-word listener for barge-in)
                ▼
         Say "hey jarvis" mid-response to interrupt and ask a new question
```

The point of routing before the LLM: a 1-3B model has no business guessing
store hours or weather from memory. It only ever phrases real data into a
sentence, selects whether a web search is needed, or handles genuinely
open-ended questions.

## Windows-first setup

See [docs/windows-demo-next-steps.md](docs/windows-demo-next-steps.md) for the full
step-by-step guide. Summary:

**1. Sync the Python environment.**

```powershell
uv sync
copy .env.config .env
```

Edit `.env` with your local paths (Piper voice, optional audio device overrides).
Edit [defaults.json](defaults.json) for location, timezone, assistant name, and units
(copy from [defaults.example.json](defaults.example.json) if needed — the example lists
every allowed option for each setting).

**2. Download and install external tools** (not managed by `uv`):

- [whisper-server](https://github.com/ggerganov/whisper.cpp/releases) + `ggml-base.en.bin`
- [llama-server](https://github.com/ggerganov/llama.cpp/releases) + Qwen2.5-3B-Instruct Q4_K_M GGUF
- Piper voice `.onnx` file ([piper-voices](https://huggingface.co/rhasspy/piper-voices)); `piper.exe` may be at `.venv\Scripts\piper.exe` after sync

**3. Start the local servers** (two terminals — leave both running), then run
the startup check in a third:

```powershell
# Terminal 1 — STT
cd D:\GitHub\jetson-nano-jarvis
D:\Applications\whisper.cpp\whisper-server.exe -m models\whisper\ggml-base.en.bin --host 127.0.0.1 --port 8080

# Terminal 2 — LLM
cd D:\GitHub\jetson-nano-jarvis
D:\Applications\llama.cpp\llama-server.exe -m models\llm\qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048

# Terminal 3 — assistant
cd D:\GitHub\jetson-nano-jarvis
uv run python -c "import main; main.startup_check()"
uv run jetson-assistant
```

**4. Say "hey jarvis", then try a command like `what time is it`.**

Configuration is split between two files:

- **[defaults.json](defaults.json)** — location, timezone, assistant name, units
- **[.env](.env)** — secrets, local paths, machine-specific overrides (copy from [.env.config](.env.config))

Env vars override `defaults.json` when both define the same setting. Do not
edit `config.py` for local paths or API keys.

## Next Steps

Before the assistant can answer real requests, you still need to set up a few
things.

1. No API key is needed for weather or web search. [services/weather_client.py](services/weather_client.py)
  uses Open-Meteo with geocoding (ask "what's the weather in Berlin").
  [services/search_client.py](services/search_client.py) uses DuckDuckGo via `ddgs` for live lookups.
2. Store-hours lookup uses free OpenStreetMap data via Overpass — no API key.
  Set your home location in `defaults.json` (city or lat/lon) so the assistant
  can find the nearest store. Tune search radius with `STORE_SEARCH_RADIUS_KM` in `.env`.
3. Music playback needs a reachable Navidrome instance and valid
  `NAVIDROME_USER` / `NAVIDROME_PASS` values in `.env`.
4. Wake-word detection uses the built-in `hey_jarvis` model by default (set
  `WAKE_WORD_MODEL` in `.env` to change it).
5. Pick a local LLM that fits the Jetson Orin Nano 4GB target. A 1B to 3B model
  quantized to GGUF is the safe range. Good starting points are
  [Qwen2.5-3B-Instruct](https://huggingface.co/Qwen) or Llama 3.2 3B in a Q4
  quantization.
6. Start `whisper-server` with `ggml-base.en.bin` for the STT side. If memory is
  tight on the Jetson, drop to `tiny.en` later.
7. Keep Piper lightweight. Use a small single voice model and set `PIPER_BIN`
  and `PIPER_VOICE` in `.env`.
8. To build a Jetson bundle later, use PyInstaller on Jetson itself or another ARM64 Linux machine.

For local model setup, the minimal loop is:

1. Start whisper.cpp server on `127.0.0.1:8080`.
2. Start llama.cpp server on `127.0.0.1:8081`.
3. Run `uv run jetson-assistant`.

## Later Jetson setup

1. **Flash JetPack 6.x**, confirm CUDA is working (`nvidia-smi` equivalent — `tegrastats`).
2. **Build whisper.cpp with CUDA** and download `ggml-base.en.bin`.
3. **Build llama.cpp with CUDA** and download a GGUF of Qwen2.5-3B-Instruct
  (Q4_K_M quant) from [Qwen](https://huggingface.co/Qwen) or a community GGUF
  repack.
4. **Install Piper** and grab a voice like `en_US-lessac-medium.onnx`.
5. **Plug in the ReSpeaker USB Mic Array**, then set `ASSISTANT_MIC_DEVICE` in
   `.env` after verifying the Linux device IDs.
6. **Stand up Navidrome** — easiest on a different always-on box rather than
   the Jetson itself, so it's not competing for the same 4GB.
7. Train a custom openWakeWord model if you want a wake phrase other than "hey jarvis".

## Running it on Windows

```powershell
# Terminal 1 — STT (leave running)
cd D:\GitHub\jetson-nano-jarvis
D:\Applications\whisper.cpp\whisper-server.exe -m models\whisper\ggml-base.en.bin --host 127.0.0.1 --port 8080

# Terminal 2 — LLM (leave running)
cd D:\GitHub\jetson-nano-jarvis
D:\Applications\llama.cpp\llama-server.exe -m models\llm\qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048

# Terminal 3 — assistant
cd D:\GitHub\jetson-nano-jarvis
uv run jetson-assistant
```

## Running it on the Jetson

```bash
# Terminal 1
~/whisper.cpp/build/bin/whisper-server -m ~/whisper.cpp/models/ggml-base.en.bin \
  --host 127.0.0.1 --port 8080

# Terminal 2
~/llama.cpp/build/bin/llama-server -m ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048

# Terminal 3
python3 main.py
```

Eventually wrap the first two in systemd services so the whole thing survives
a reboot without you SSHing in.

## What's stubbed vs. real

- **Weather (place-aware), clock, store hours, web search, intent routing, streaming TTS,
  barge-in, STT, wake word, VAD**: functional code, should work close to as-is.
- **Store hours (OSM / Overpass)**: free, no API key. Needs a default location
  in `defaults.json` for nearest-store lookup. Ask "when will Canadian Tire
  close today", "is Walmart open", etc.
- **Music / Navidrome**: needs a reachable Navidrome instance and valid
  `NAVIDROME_USER` / `NAVIDROME_PASS` values in `.env`, plus `mpv` on PATH.
- **Wake word model**: defaults to built-in `hey_jarvis` ("hey jarvis"). Train a
  custom model via [openWakeWord](https://github.com/dscripka/openWakeWord) if you
  want a different phrase.
- **Audio device selection**: set `ASSISTANT_MIC_DEVICE` and
  `ASSISTANT_SPEAKER_DEVICE` in `.env`; leaving them blank uses system defaults.
- **Barge-in on Windows**: works via concurrent wake-word detection during TTS.
  No echo cancellation yet — the ReSpeaker on Jetson will improve this.
- **LLM endpoint**: uses `/v1/chat/completions` (OpenAI-compatible). Set
  `LLAMA_SERVER_URL` in `.env` if your server uses a different path.

- **Packaging**: the project is still source-first. A PyInstaller or Nuitka
  bundle for Jetson is possible later, but the local development path is plain
  Python plus `uv sync`.

## Known rough edges to expect

- 4GB is tight running whisper.cpp + llama.cpp back to back on the Jetson —
  if you see `cudaMalloc failed`, try `base.en` → `tiny.en` for whisper, or
  drop to the 1B LLM.
- The ReSpeaker's AEC will help a lot once music is playing, but test barge-in
  (say "hey jarvis" while the assistant is speaking) early on — without AEC on
  Windows, false triggers from speaker bleed are possible but rare with the
  specific "hey jarvis" phrase.
- `mpv` needs to be reachable on PATH; the music client kills any previous
  track before starting the next one — good enough for a single-user setup,
  not built for multi-room sync.
