# Secure Jetson server releases

Ancilla is not a single static binary. The useful, shippable artifacts are the
**CUDA `whisper-server` / `llama-server` builds** for Jetson Orin, plus pinned
model downloads. The Python app still comes from this repo via `uv sync`.

## What we ship (and what we refuse to ship)

| Included | Excluded on purpose |
|----------|---------------------|
| `whisper-server`, `llama-server` | Model weights (too large; verify separately) |
| Matching `libggml*` / `libwhisper` / `libllama*` from the build dir | System CUDA (`libcudart`, `libcuda`, ...) |
| `SHA256SUMS`, `SYMLINKS.txt`, `PROVENANCE.txt` | `.env`, tokens, Navidrome passwords |
| `scripts/verify-and-install.sh` | Builder home-path rpaths |

## Maintainer: build a release on the Jetson

```bash
# optional: sign with your release key
export ANCILLA_RELEASE_GPG_KEY=YOUR_KEY_ID

./scripts/package-jetson-release.sh
```

Artifacts land in `dist/`:

- `ancilla-jetson-orin-<ver>-aarch64.tar.gz`
- `ancilla-jetson-orin-<ver>-aarch64.tar.gz.sha256`
- `ancilla-jetson-orin-<ver>-aarch64.tar.gz.asc` (only if GPG key set)

Upload **all of those** to a GitHub Release. Never publish only the tarball.

## User: verify, then install

Do not pipe `curl` into `sh`.

```bash
# 1) Check the archive digest (compare against the Release asset)
sha256sum -c ancilla-jetson-orin-0.1.0-aarch64.tar.gz.sha256

# 2) Optional but recommended: verify GPG signature
gpg --recv-keys YOUR_RELEASE_KEY_ID
gpg --verify ancilla-jetson-orin-0.1.0-aarch64.tar.gz.asc \
             ancilla-jetson-orin-0.1.0-aarch64.tar.gz

# 3) Extract and run the in-tree verifier/installer
tar -xzf ancilla-jetson-orin-0.1.0-aarch64.tar.gz
cd ancilla-jetson-orin-0.1.0-aarch64
./scripts/verify-and-install.sh --prefix ~/.local/ancilla-servers
```

`verify-and-install.sh` fail-closes if:

- any `SHA256SUMS` entry mismatches (`sha256sum --strict`)
- any symlink target drifts from `SYMLINKS.txt`
- a symlink points to an absolute path
- the machine is not `aarch64`
- the install prefix already exists (unless `--force`)

## Models

```bash
./scripts/download-models.sh
```

That script downloads over HTTPS and **deletes the file if the pinned SHA-256
does not match**. Digests are hardcoded in the script; change them only when
you intentionally change models.

## Trust model

1. GitHub Release assets + published checksum file
2. Optional GPG signature from a key you control and advertise out-of-band
3. In-archive `SHA256SUMS` after extraction (detects bitrot / tampering after download)
4. Pinned model digests independent of the server bundle

JetPack / CUDA must already match the build (this reference device uses JetPack
R39 / CUDA 13.2). These CUDA builds are **not** for Raspberry Pi.
