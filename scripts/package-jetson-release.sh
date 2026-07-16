#!/usr/bin/env bash
# package-jetson-release.sh
#
# Security-minded Jetson Orin (aarch64 + CUDA) release bundle for Ancilla.
#
# Ships ONLY whisper-server / llama-server and their build-tree shared libs.
# Does NOT ship models, .env, secrets, or system CUDA.
#
# Hardening:
#   - umask 022; no setuid
#   - patchelf rpath = $ORIGIN (no builder home paths)
#   - SHA256SUMS for every regular file (sha256sum --strict)
#   - SYMLINKS.txt attested and included in SHA256SUMS
#   - reproducible-ish tar (sorted names, numeric uid/gid, fixed mtime)
#   - optional GPG detached signature via ANCILLA_RELEASE_GPG_KEY
#
# Usage:
#   ./scripts/package-jetson-release.sh
#   ANCILLA_RELEASE_GPG_KEY=KEYID ./scripts/package-jetson-release.sh

set -euo pipefail
umask 022

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

VERSION="${ANCILLA_RELEASE_VERSION:-$(python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
PY
)}"
ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" ]]; then
  echo "Refusing to package: expected aarch64 Jetson host, got ${ARCH}" >&2
  exit 1
fi

WHISPER_BIN_DIR="${ANCILLA_WHISPER_BIN_DIR:-$HOME/whisper.cpp/build/bin}"
LLAMA_BIN_DIR="${ANCILLA_LLAMA_BIN_DIR:-$HOME/llama.cpp/build/bin}"

for need in "$WHISPER_BIN_DIR/whisper-server" "$LLAMA_BIN_DIR/llama-server"; do
  if [[ ! -x "$need" ]]; then
    echo "Missing executable: $need" >&2
    exit 1
  fi
done

command -v patchelf >/dev/null 2>&1 || { echo "patchelf required (sudo apt install patchelf)" >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum required" >&2; exit 1; }
command -v readelf >/dev/null 2>&1 || { echo "readelf required" >&2; exit 1; }

SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$REPO" log -1 --pretty=%ct 2>/dev/null || date +%s)}"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/ancilla-release.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

NAME="ancilla-jetson-orin-${VERSION}-aarch64"
ROOT="$STAGING/$NAME"
mkdir -p "$ROOT/bin/whisper" "$ROOT/bin/llama" "$ROOT/scripts"

copy_closure() {
  local src_dir="$1" dest_dir="$2" entry="$3"
  python3 - "$src_dir" "$dest_dir" "$entry" <<'PY'
import os, shutil, subprocess, sys
from pathlib import Path

src_dir = Path(sys.argv[1]).resolve()
dest_dir = Path(sys.argv[2]).resolve()
entry = (src_dir / sys.argv[3]).resolve()
dest_dir.mkdir(parents=True, exist_ok=True)

needed_files: set[Path] = set()
seen: set[Path] = set()
queue = [entry]
while queue:
    path = queue.pop().resolve()
    if path in seen:
        continue
    seen.add(path)
    if path.parent != src_dir:
        # Never pull in system CUDA or other absolute deps.
        continue
    needed_files.add(path)
    out = subprocess.check_output(["ldd", str(path)], text=True, stderr=subprocess.DEVNULL)
    for line in out.splitlines():
        line = line.strip()
        if "not found" in line and " => " not in line:
            raise SystemExit(f"unresolved dependency for {path}: {line}")
        if " => " not in line:
            continue
        lib = line.split(" => ", 1)[1].split(" (", 1)[0].strip()
        if not lib or lib == "not found":
            continue
        lib_path = Path(lib).resolve()
        if lib_path.parent == src_dir:
            queue.append(lib_path)

# Copy real files first.
for path in sorted(needed_files, key=lambda p: p.name):
    dest = dest_dir / path.name
    shutil.copy2(path, dest)
    mode = path.stat().st_mode
    dest.chmod(0o755 if (mode & 0o111) else 0o644)

# Recreate soname symlinks that point at shipped files (relative only).
for link in sorted(src_dir.iterdir(), key=lambda p: p.name):
    if not link.is_symlink():
        continue
    target_name = os.readlink(link)
    if target_name.startswith("/"):
        continue
    target_resolved = (src_dir / target_name).resolve()
    if target_resolved not in needed_files and target_resolved.name not in {p.name for p in needed_files}:
        # Allow symlink to another symlink chain ending in needed file.
        try:
            final = link.resolve()
        except OSError:
            continue
        if final not in needed_files:
            continue
        target_name = final.name
    elif target_resolved not in needed_files:
        # Map through basename if versioned file was copied under final name.
        if not (dest_dir / Path(target_name).name).exists():
            continue
    dest_link = dest_dir / link.name
    if dest_link.exists() or dest_link.is_symlink():
        dest_link.unlink()
    dest_link.symlink_to(Path(target_name).name if "/" not in target_name else Path(target_name).name)
PY
}

echo "Collecting whisper-server closure..."
copy_closure "$WHISPER_BIN_DIR" "$ROOT/bin/whisper" "whisper-server"
echo "Collecting llama-server closure..."
copy_closure "$LLAMA_BIN_DIR" "$ROOT/bin/llama" "llama-server"

echo "Setting portable \$ORIGIN rpath..."
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -qE 'ELF.*(executable|shared object)'; then
    patchelf --set-rpath '$ORIGIN' "$f"
  fi
done < <(find "$ROOT/bin" -type f -print0)

if readelf -d "$ROOT/bin/whisper/whisper-server" "$ROOT/bin/llama/llama-server" \
  | grep -E '\(RPATH\)|\(RUNPATH\)' | grep -v '\$ORIGIN'; then
  echo "Refusing release: non-\$ORIGIN rpath remained" >&2
  exit 1
fi

# Symlink attestation (verified after SHA256SUMS).
(
  cd "$ROOT"
  : > SYMLINKS.txt
  find bin -type l -printf '%p\n' | LC_ALL=C sort | while read -r rel; do
    printf '%s -> %s\n' "$rel" "$(readlink "$rel")" >> SYMLINKS.txt
  done
)

{
  echo "name: $NAME"
  echo "ancilla_version: $VERSION"
  echo "built_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source_date_epoch: $SOURCE_DATE_EPOCH"
  echo "host_arch: $ARCH"
  echo "host_kernel: $(uname -r)"
  if [[ -r /etc/nv_tegra_release ]]; then
    echo "jetpack: $(head -1 /etc/nv_tegra_release | tr -d '\r')"
  fi
  if command -v nvcc >/dev/null 2>&1; then
    echo "nvcc: $(nvcc --version | awk '/release/ {print; exit}')"
  fi
  echo "ancilla_git: $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "whisper_cpp_git: $(git -C "$WHISPER_BIN_DIR/../.." rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "llama_cpp_git: $(git -C "$LLAMA_BIN_DIR/../.." rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "policy: no models; no .env; no system CUDA libs; rpath=\$ORIGIN only"
  echo "contents:"
  find "$ROOT/bin" -printf '  %P\n' | LC_ALL=C sort
} > "$ROOT/PROVENANCE.txt"

cat > "$ROOT/README.txt" <<EOF
Ancilla Jetson Orin server bundle (${VERSION})

Contains ONLY prebuilt whisper-server and llama-server (+ build-tree libs)
for aarch64 Jetson with a matching JetPack/CUDA stack.

NOT included: model weights, Python app, .env, system CUDA.

Verify (do this before running anything):
  sha256sum -c ancilla-jetson-orin-${VERSION}-aarch64.tar.gz.sha256
  gpg --verify ancilla-jetson-orin-${VERSION}-aarch64.tar.gz.asc \\
               ancilla-jetson-orin-${VERSION}-aarch64.tar.gz   # if a signature was published

Then:
  tar -xzf ancilla-jetson-orin-${VERSION}-aarch64.tar.gz
  cd ancilla-jetson-orin-${VERSION}-aarch64
  ./scripts/verify-and-install.sh --prefix ~/.local/ancilla-servers

Never pipe curl/wget into a shell for this install.
EOF

cat > "$ROOT/scripts/verify-and-install.sh" <<'EOF'
#!/usr/bin/env bash
# Verify SHA256SUMS + SYMLINKS.txt, then install to a user prefix.
set -euo pipefail
umask 022

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${HOME}/.local/ancilla-servers"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) echo "Usage: $0 [--prefix DIR] [--force]"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This bundle is for aarch64 Jetson only." >&2
  exit 1
fi

cd "$BUNDLE_ROOT"
[[ -f SHA256SUMS ]] || { echo "Missing SHA256SUMS" >&2; exit 1; }
[[ -f SYMLINKS.txt ]] || { echo "Missing SYMLINKS.txt" >&2; exit 1; }

echo "Verifying file checksums (sha256sum --strict)..."
sha256sum -c SHA256SUMS --strict

echo "Verifying symlink targets..."
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  rel="${line%% -> *}"
  want="${line#* -> }"
  if [[ ! -L "$rel" ]]; then
    echo "Missing symlink: $rel" >&2
    exit 1
  fi
  got="$(readlink "$rel")"
  if [[ "$got" != "$want" ]]; then
    echo "Symlink mismatch for $rel: got '$got' want '$want'" >&2
    exit 1
  fi
  # Reject absolute symlink targets.
  if [[ "$got" == /* ]]; then
    echo "Refusing absolute symlink: $rel -> $got" >&2
    exit 1
  fi
done < SYMLINKS.txt

if [[ -e "$PREFIX" && "$FORCE" -ne 1 ]]; then
  echo "Refusing to overwrite existing prefix: $PREFIX (pass --force)" >&2
  exit 1
fi

STAGE="${PREFIX}.new.$$"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a bin PROVENANCE.txt README.txt SHA256SUMS SYMLINKS.txt scripts "$STAGE/"

if [[ -e "$PREFIX" ]]; then
  rm -rf "${PREFIX}.bak"
  mv "$PREFIX" "${PREFIX}.bak"
fi
mv "$STAGE" "$PREFIX"

find "$PREFIX/bin" -type f -exec chmod go-w {} +
find "$PREFIX" -type d -exec chmod 755 {} +
chmod 755 "$PREFIX/scripts/verify-and-install.sh"

echo ""
echo "Installed to $PREFIX"
echo "  export PATH=\"$PREFIX/bin/whisper:$PREFIX/bin/llama:\$PATH\""
EOF
chmod 755 "$ROOT/scripts/verify-and-install.sh"

(
  cd "$ROOT"
  find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort | xargs -r sha256sum > SHA256SUMS
)

mkdir -p "$REPO/dist"
OUT_TAR="$REPO/dist/${NAME}.tar.gz"
OUT_SUM="$REPO/dist/${NAME}.tar.gz.sha256"

tar \
  --sort=name \
  --owner=0 --group=0 --numeric-owner \
  --mtime="@${SOURCE_DATE_EPOCH}" \
  -C "$STAGING" \
  -czf "$OUT_TAR" \
  "$NAME"

(
  cd "$REPO/dist"
  sha256sum "$(basename "$OUT_TAR")" > "$(basename "$OUT_SUM")"
)

if [[ -n "${ANCILLA_RELEASE_GPG_KEY:-}" ]]; then
  gpg --batch --yes --detach-sign --armor \
    --local-user "$ANCILLA_RELEASE_GPG_KEY" \
    -o "${OUT_TAR}.asc" \
    "$OUT_TAR"
  echo "Signed: ${OUT_TAR}.asc"
fi

# Final self-check of the tarball checksum file.
(
  cd "$REPO/dist"
  sha256sum -c "$(basename "$OUT_SUM")" --strict
)

echo ""
echo "Release artifacts:"
ls -lh "$OUT_TAR" "$OUT_SUM"
[[ -f "${OUT_TAR}.asc" ]] && ls -lh "${OUT_TAR}.asc"
echo ""
echo "Publish both the .tar.gz and .sha256 on GitHub Releases."
echo "If you signed: also upload the .asc and publish your public key."
