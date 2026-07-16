#!/usr/bin/env bash
# Overnight CUDA builds for whisper.cpp + llama.cpp on Jetson Orin Nano.
# Run inside tmux so SSH disconnect / PC shutdown does not kill the compile.
set -euo pipefail

LOG_DIR="${HOME}/jetson-build-logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
MAIN_LOG="${LOG_DIR}/overnight-${STAMP}.log"

exec > >(tee -a "$MAIN_LOG") 2>&1

echo "=== Jetson overnight build started at $(date) ==="
echo "Main log: $MAIN_LOG"
echo "CPU cores: $(nproc)  |  build parallelism: -j2"
echo ""

build_whisper() {
  echo "=== [1/2] whisper.cpp CUDA build (clean) ==="
  cd "${HOME}/whisper.cpp"

  echo "Removing stale whisper.cpp build dir..."
  rm -rf build

  echo "Configuring whisper.cpp (GGML_CUDA=1, arch=87)..."
  cmake -B build -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=87

  echo "Building whisper-server only (-j2). ggml-cuda.cu may sit at ~78% for 20-40+ min - expected."
  cmake --build build --target whisper-server -j2 --config Release --verbose

  if [[ -x build/bin/whisper-server ]]; then
    echo "whisper-server OK: $(ls -lh build/bin/whisper-server)"
  else
    echo "ERROR: whisper-server missing after build" >&2
    exit 1
  fi
}

build_llama() {
  echo ""
  echo "=== [2/2] llama.cpp CUDA build ==="
  cd "${HOME}"

  if [[ ! -d llama.cpp ]]; then
    echo "Cloning llama.cpp..."
    git clone https://github.com/ggerganov/llama.cpp
  fi

  cd llama.cpp
  rm -rf build

  echo "Configuring llama.cpp (GGML_CUDA=1, arch=87)..."
  cmake -B build -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=87

  echo "Building llama-server only (-j2). ggml-cuda.cu may sit at ~78% for 20-40+ min - expected."
  cmake --build build --target llama-server -j2 --config Release --verbose

  if [[ -x build/bin/llama-server ]]; then
    echo "llama-server OK: $(ls -lh build/bin/llama-server)"
  else
    echo "ERROR: llama-server missing after build" >&2
    exit 1
  fi
}

build_whisper
build_llama

echo ""
echo "=== All builds finished successfully at $(date) ==="
echo "whisper-server: ${HOME}/whisper.cpp/build/bin/whisper-server"
echo "llama-server:   ${HOME}/llama.cpp/build/bin/llama-server"
echo ""
echo "Reattach:  tmux attach -t jetson-build"
echo "Kill sess: tmux kill-session -t jetson-build"
