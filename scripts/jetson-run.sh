#!/usr/bin/env bash
# Start whisper-server, llama-server, and the voice assistant (run after builds finish).
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
REPO="${HOME}/github/jetson-nano-jarvis"
WHISPER_BIN="${HOME}/whisper.cpp/build/bin/whisper-server"
LLAMA_BIN="${HOME}/llama.cpp/build/bin/llama-server"
WHISPER_MODEL="${REPO}/models/whisper/ggml-base.en.bin"
LLM_MODEL="${REPO}/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf"

for f in "$WHISPER_BIN" "$LLAMA_BIN" "$WHISPER_MODEL" "$LLM_MODEL"; do
  if [[ ! -e "$f" ]]; then
    echo "Missing: $f" >&2
    exit 1
  fi
done

cd "$REPO"

echo "Starting whisper-server on :8080..."
"$WHISPER_BIN" -m "$WHISPER_MODEL" --host 127.0.0.1 --port 8080 &
WHISPER_PID=$!

echo "Starting llama-server on :8081..."
"$LLAMA_BIN" -m "$LLM_MODEL" --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048 &
LLAMA_PID=$!

cleanup() {
  kill "$WHISPER_PID" "$LLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 3
echo "Running startup check..."
uv run python -c "from jetson_assistant.main import startup_check; startup_check()"

echo ""
echo "Starting jetson-assistant (Ctrl+C to stop all)..."
uv run jetson-assistant
