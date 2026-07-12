#!/usr/bin/env bash
# Quick status of overnight setup jobs.
echo "=== tmux sessions ==="
tmux ls 2>/dev/null || echo "(none)"

echo ""
echo "=== build artifacts ==="
for bin in \
  "${HOME}/whisper.cpp/build/bin/whisper-server" \
  "${HOME}/llama.cpp/build/bin/llama-server" \
  "${HOME}/github/jetson-nano-jarvis/.venv/bin/piper"; do
  if [[ -x "$bin" ]]; then
    echo "OK  $bin"
  else
    echo "MISSING  $bin"
  fi
done

echo ""
echo "=== models ==="
for f in \
  "${HOME}/github/jetson-nano-jarvis/models/whisper/ggml-base.en.bin" \
  "${HOME}/github/jetson-nano-jarvis/models/llm/qwen2.5-3b-instruct-q4_k_m.gguf" \
  "${HOME}/github/jetson-nano-jarvis/models/piper/en/british/en_GB-northern_english_male-medium.onnx"; do
  if [[ -f "$f" ]]; then
    echo "OK  $(ls -lh "$f" | awk '{print $5, $9}')"
  else
    echo "MISSING  $f"
  fi
done

echo ""
echo "=== recent logs ==="
ls -lt "${HOME}/jetson-build-logs/" 2>/dev/null | head -8

echo ""
echo "=== active compile/download (if any) ==="
ps aux | grep -E "cmake|nvcc|gmake|wget|uv " | grep -v grep | head -10 || echo "(idle)"
