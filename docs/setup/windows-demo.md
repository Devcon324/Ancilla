# Windows Demo Next Steps

This is the shortest path to getting the assistant working on your Windows
machine so you can test voice commands and hear spoken replies.

## What you are trying to achieve

You want this loop to work:

1. Wake word is heard.
2. The assistant records your speech.
3. Speech is sent to local STT.
4. The text is routed to weather, store hours, music, or the local LLM.
5. Piper speaks the response out loud.

If any of those pieces are missing, the assistant will not fully demo.

The wake phrase is **"hey jarvis"** (openWakeWord built-in model `hey_jarvis`).

## Step 1: Download and install prerequisites

| # | What | Why | Download |
|---|------|-----|----------|
| 1 | **uv** | Python env manager | https://docs.astral.sh/uv/getting-started/installation/ |
| 2 | **whisper.cpp** Windows binary | Local STT server | https://github.com/ggerganov/whisper.cpp/releases |
| 3 | **Whisper model** `ggml-base.en.bin` | STT model (~150MB) | https://huggingface.co/ggerganov/whisper.cpp/tree/main |
| 4 | **llama.cpp** Windows binary | Local LLM server | https://github.com/ggerganov/llama.cpp/releases |
| 5 | **LLM GGUF** Qwen2.5-3B-Instruct Q4_K_M (~2GB) | Matches Jetson target | https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF |
| 6 | **Piper voice** `.onnx` + `.onnx.json` | TTS voice | https://huggingface.co/rhasspy/piper-voices |
| 7 | **mpv** (optional) | Music playback | https://mpv.io/installation/ |
| 8 | **Navidrome** (optional) | Self-hosted music | https://github.com/navidrome/navidrome/releases |

Suggested folder layout on this machine:

```
D:\Applications\
  llama.cpp\llama-server.exe
  whisper.cpp\whisper-server.exe

D:\GitHub\Ancilla\
  models\
    whisper\ggml-base.en.bin
    llm\qwen2.5-3b-instruct-q4_k_m.gguf
    piper\en\american\en_US-norman-medium.onnx
    piper\en\american\en_US-norman-medium.onnx.json
  .venv\Scripts\piper.exe
```

You also need a working microphone and speakers or headphones.

## Step 2: Open the project folder

Open a terminal in the repo root:

`D:\GitHub\Ancilla`

All commands below assume you are in that folder.

## Step 3: Set up the Python environment

```powershell
uv sync
copy .env.example .env
```

Edit `.env` with your real paths. On this machine that means:

- `PIPER_VOICE` — `D:\GitHub\Ancilla\models\piper\en\american\en_US-norman-medium.onnx`
- `PIPER_BIN` — `D:\GitHub\Ancilla\.venv\Scripts\piper.exe`

Leave `ASSISTANT_MIC_DEVICE` and `ASSISTANT_SPEAKER_DEVICE` blank to use Windows defaults.

See [.env.example](../.env.example) for all available variables.

## Step 4: Configure Piper

Piper is the text-to-speech engine. The Python package `piper-tts` is installed
by `uv sync`; you still need a voice model file.

1. Download a voice from [piper-voices](https://huggingface.co/rhasspy/piper-voices)
   (e.g. `en_US-lessac-medium`).
2. Set `PIPER_VOICE` and `PIPER_BIN` in `.env`.

If `PIPER_VOICE` is empty, the assistant will warn you at startup and TTS will
not work.

## Step 5: Start whisper-server

Terminal 1 (leave this running):

```powershell
cd D:\GitHub\Ancilla
D:\Applications\whisper.cpp\whisper-server.exe -m models\whisper\ggml-base.en.bin --host 127.0.0.1 --port 8080
```

Use `ggml-base.en.bin` first — that is the target size for the Jetson too.

## Step 6: Start llama-server

Terminal 2 (leave this running):

```powershell
cd D:\GitHub\Ancilla
D:\Applications\llama.cpp\llama-server.exe -m models\llm\qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048
```

Stick to 1B–3B Q4 models even if your GPU can run larger ones.

## Step 7: Check your audio devices

```powershell
uv run python -c "import sounddevice as sd; print(sd.query_devices())"
```

Set `ASSISTANT_MIC_DEVICE` and `ASSISTANT_SPEAKER_DEVICE` in `.env` if defaults
are wrong (device index or exact name).

## Step 8: Optional `.env` variables

These are optional at first:

- `STORE_SEARCH_RADIUS_KM` — initial Overpass search radius for store hours (default 10 km)
- `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS` — music playback
- `WEATHER_LAT`, `WEATHER_LON`, `WEATHER_LOCATION_NAME` — weather location

Weather uses Open-Meteo and needs no API key.

## Step 9: Run the startup check

Terminal 3 (from repo root):

```powershell
cd D:\GitHub\Ancilla
uv run python -c "from ancilla.main import startup_check; startup_check()"
```

This reports:

- Missing required `.env` values (`PIPER_VOICE`, etc.)
- Whether `whisper-server` is reachable at `/health`
- Whether `llama-server` is reachable at `/health`

Fix anything it reports before continuing.

## Step 10: Start the assistant

```powershell
uv run ancilla
```

You should see a message that the assistant is ready and waiting for the wake
word.

## Step 11: Test the basic voice loop

Say **"hey jarvis"**, then try these commands one by one:

1. `what time is it`
2. `what's the weather`
3. `tell me a joke`
4. `play lo-fi music` (needs Navidrome + mpv)
5. `stop music`

What should happen:

- The wake word triggers recording.
- Your speech gets transcribed.
- The assistant chooses a route.
- The assistant speaks back through your speakers.

## Step 12: What to expect for each command

### Weather

Uses Open-Meteo (no API key). Needs llama-server running to phrase the reply.

### Time

Works without any external service or LLM.

### General chat

Goes to the local LLM. If llama-server is down, general questions fail.

### Store hours

Store hours use OpenStreetMap (Overpass). Set home location in `config/defaults.json`.

- [Google Cloud Console](https://console.cloud.google.com/)
- [Places API documentation](https://developers.google.com/maps/documentation/places/web-service)

### Music

Needs Navidrome reachable and `NAVIDROME_*` set in `.env`, plus `mpv` on PATH.

- [Navidrome releases](https://github.com/navidrome/navidrome/releases)
- [Navidrome documentation](https://www.navidrome.org/docs/)

### TTS

Needs `PIPER_BIN` and `PIPER_VOICE` set in `.env`.

## Step 13: If something fails

### If the assistant starts but does not speak

1. `PIPER_BIN` is correct in `.env`.
2. `PIPER_VOICE` is correct in `.env`.
3. Speaker device is selected correctly.

### If it records but does not transcribe

1. `whisper-server` is running.
2. The model file exists.
3. Health check passes: `http://127.0.0.1:8080/health`

### If it transcribes but does not answer

1. `llama-server` is running.
2. The model file exists.
3. Health check passes: `http://127.0.0.1:8081/health`

### If wake-word detection never triggers

1. The microphone is the correct one (`ASSISTANT_MIC_DEVICE` in `.env`).
2. `WAKE_WORD_MODEL=hey_jarvis` in `.env`.
3. You are saying **"hey jarvis"**, not just "jarvis".

## Step 14: PyTorch note

`uv sync` installs CPU `torch` by default. Silero VAD runs fine on CPU. If you
need CUDA PyTorch:

```powershell
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

## Step 15: After the Windows demo works

1. Keep model sizes small (3B Q4 LLM, base.en whisper).
2. Keep local servers separate from the assistant process.
3. Rebuild whisper.cpp and llama.cpp on the Jetson — do not copy Windows binaries.

For a custom wake word later: [openWakeWord](https://github.com/dscripka/openWakeWord).

## Quick success checklist

- `uv sync` finishes successfully.
- `.env` copied from `.env.example` and paths filled in.
- `uv run python -c "from ancilla.main import startup_check; startup_check()"` passes.
- `whisper-server` and `llama-server` are running.
- Piper voice configured in `.env`.
- `uv run ancilla` starts and waits for the wake word.
- Saying "hey jarvis" then a command produces a spoken reply.

That is the minimum demo state.
