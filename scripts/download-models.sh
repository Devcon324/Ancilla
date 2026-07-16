#!/usr/bin/env bash
# Download STT, LLM, and Piper voice models for Ancilla.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHISPER_DIR="${REPO}/models/whisper"
LLM_DIR="${REPO}/models/llm"
PIPER_DIR="${REPO}/models/piper/en/british"

mkdir -p "$WHISPER_DIR" "$LLM_DIR" "$PIPER_DIR"

echo "=== Downloading Whisper ggml-base.en.bin ==="
WHISPER_MODEL="${WHISPER_DIR}/ggml-base.en.bin"
if [[ -f "$WHISPER_MODEL" ]]; then
  echo "Already exists: $WHISPER_MODEL"
else
  cd "${HOME}/whisper.cpp"
  bash ./models/download-ggml-model.sh base.en
  cp -v models/ggml-base.en.bin "$WHISPER_MODEL"
fi

echo ""
echo "=== Downloading Qwen2.5-3B-Instruct Q4_K_M GGUF (~2GB) ==="
LLM_MODEL="${LLM_DIR}/qwen2.5-3b-instruct-q4_k_m.gguf"
if [[ -f "$LLM_MODEL" ]]; then
  echo "Already exists: $LLM_MODEL"
else
  HF_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
  wget -c --progress=dot:giga -O "$LLM_MODEL" "$HF_URL"
fi

echo ""
echo "=== Downloading Piper voice en_GB-northern_english_male-medium ==="
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium"
for ext in onnx "onnx.json"; do
  dest="${PIPER_DIR}/en_GB-northern_english_male-medium.${ext}"
  if [[ -f "$dest" ]]; then
    echo "Already exists: $dest"
  else
    wget -c --progress=dot:giga -O "$dest" "${PIPER_BASE}/en_GB-northern_english_male-medium.${ext}"
  fi
done

echo ""
echo "=== Model download complete ==="
ls -lh "$WHISPER_MODEL" "$LLM_MODEL" "${PIPER_DIR}/"*
