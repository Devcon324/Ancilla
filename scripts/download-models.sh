#!/usr/bin/env bash
# Download STT, LLM, and Piper voice models for Ancilla.
# Verifies SHA-256 against pinned digests (fail closed).
set -euo pipefail
umask 022

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHISPER_DIR="${REPO}/models/whisper"
LLM_DIR="${REPO}/models/llm"
PIPER_DIR="${REPO}/models/piper/en/british"

# Pinned digests measured on the Ancilla reference Jetson build.
# Update these only when intentionally changing model files.
WHISPER_SHA256="a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"
LLM_SHA256="626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"
PIPER_ONNX_SHA256="57a219ae8e638873db7d18893304be5069c42868f392bb95c3ff17f0690d0689"
PIPER_JSON_SHA256="69557ed3d974463453e9b0c09dd99a7ed0e52b8b87b64b357dbeeb2540a97d47"

mkdir -p "$WHISPER_DIR" "$LLM_DIR" "$PIPER_DIR"

verify_sha256() {
  local file="$1" expect="$2"
  local got
  got="$(sha256sum "$file" | awk '{print $1}')"
  if [[ "$got" != "$expect" ]]; then
    echo "SHA-256 mismatch for $file" >&2
    echo "  expected: $expect" >&2
    echo "  got:      $got" >&2
    rm -f "$file"
    exit 1
  fi
  echo "OK  $(basename "$file")"
}

download_checked() {
  local url="$1" dest="$2" expect="$3"
  local tmp="${dest}.partial.$$"
  if [[ -f "$dest" ]]; then
    if sha256sum "$dest" | awk '{print $1}' | grep -qx "$expect"; then
      echo "Already present and verified: $dest"
      return 0
    fi
    echo "Existing file failed digest check; re-downloading: $dest"
    rm -f "$dest"
  fi
  # Download to a temp name, verify, then atomic move.
  wget -c --progress=dot:giga -O "$tmp" "$url"
  verify_sha256 "$tmp" "$expect"
  mv -f "$tmp" "$dest"
  chmod 644 "$dest"
}

echo "=== Whisper ggml-base.en.bin ==="
WHISPER_MODEL="${WHISPER_DIR}/ggml-base.en.bin"
download_checked \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" \
  "$WHISPER_MODEL" \
  "$WHISPER_SHA256"

echo ""
echo "=== Qwen2.5-3B-Instruct Q4_K_M GGUF (~2GB) ==="
LLM_MODEL="${LLM_DIR}/qwen2.5-3b-instruct-q4_k_m.gguf"
download_checked \
  "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" \
  "$LLM_MODEL" \
  "$LLM_SHA256"

echo ""
echo "=== Piper en_GB-northern_english_male-medium ==="
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium"
download_checked \
  "${PIPER_BASE}/en_GB-northern_english_male-medium.onnx" \
  "${PIPER_DIR}/en_GB-northern_english_male-medium.onnx" \
  "$PIPER_ONNX_SHA256"
download_checked \
  "${PIPER_BASE}/en_GB-northern_english_male-medium.onnx.json" \
  "${PIPER_DIR}/en_GB-northern_english_male-medium.onnx.json" \
  "$PIPER_JSON_SHA256"

echo ""
echo "=== Model download complete (all digests verified) ==="
ls -lh "$WHISPER_MODEL" "$LLM_MODEL" "${PIPER_DIR}/"*
