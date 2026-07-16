#!/usr/bin/env bash
# Python env for Ancilla - CPU torch only (Silero VAD / openWakeWord).
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${HOME}/jetson-build-logs/uv-sync-$(date +%Y%m%d-%H%M%S).log"

exec > >(tee -a "$LOG") 2>&1

echo "=== uv sync (CPU torch) started at $(date) ==="
cd "$REPO"

# Install CPU torch first so uv does not pull the CUDA lockfile wheels (~2GB+).
# Recreate venv if a prior partial CUDA sync left it broken.
if [[ -d .venv ]] && [[ ! -x .venv/bin/piper ]]; then
  echo "Removing incomplete .venv from earlier sync..."
  rm -rf .venv
fi

uv venv --python 3.11
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
uv pip install -e .

echo ""
echo "=== Verifying install ==="
uv run python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
test -x .venv/bin/piper && echo "piper OK: .venv/bin/piper"

echo ""
echo "=== uv sync finished at $(date) ==="
echo "Log: $LOG"
