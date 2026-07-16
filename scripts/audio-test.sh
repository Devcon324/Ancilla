#!/usr/bin/env bash
# Test Bluetooth speaker + USB microphone on the Jetson (over SSH).
#
# Usage:
#   bash scripts/audio-test.sh
#   bash scripts/audio-test.sh --no-playback   # detect only, no sound/record
#   bash scripts/audio-test.sh --connect         # run bt-soundblade first
#
# Env overrides:
#   BT_SPEAKER_MAC, BT_SPEAKER_NAME, MIC_USB_ID, MIC_NAME_MATCH
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

BT_MAC="${BT_SPEAKER_MAC:-F0:13:C3:41:98:60}"
BT_NAME="${BT_SPEAKER_NAME:-SOUNDBLADE}"
MIC_USB_ID="${MIC_USB_ID:-3142:a010}"          # Fifine USB id from lsusb
MIC_NAME_MATCH="${MIC_NAME_MATCH:-fifine}"     # match in arecord / sounddevice
WAV_SAMPLE="${WAV_SAMPLE:-/usr/share/sounds/alsa/Front_Center.wav}"
RECORD_SECS="${RECORD_SECS:-3}"

DO_PLAYBACK=1
DO_CONNECT=0
for arg in "$@"; do
  case "$arg" in
    --no-playback) DO_PLAYBACK=0 ;;
    --connect) DO_CONNECT=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
  esac
done

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export PATH="${HOME}/bin:${HOME}/.local/bin:${PATH}"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  PASS  $*"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL  $*"; FAIL=$((FAIL + 1)); }
warn() { echo "  WARN  $*"; WARN=$((WARN + 1)); }

echo "=== Jetson audio hardware test ==="
echo "Repo: $REPO"
echo "Speaker: $BT_NAME ($BT_MAC)"
echo "Mic USB id: $MIC_USB_ID  match: $MIC_NAME_MATCH"
echo ""

# ---------------------------------------------------------------------------
# Bluetooth speaker
# ---------------------------------------------------------------------------
echo "--- Bluetooth speaker ---"

if [[ "$DO_CONNECT" -eq 1 ]] && [[ -x "${HOME}/bin/bt-soundblade" ]]; then
  echo "  Running bt-soundblade..."
  "${HOME}/bin/bt-soundblade" || true
fi

if ! command -v bluetoothctl >/dev/null; then
  bad "bluetoothctl not installed"
else
  if ! bluetoothctl info "$BT_MAC" >/tmp/jetson-bt-info.txt 2>/dev/null; then
    bad "speaker $BT_MAC not known to bluez (pair it first)"
  else
    connected="$(awk -F': ' '/Connected:/ {print $2; exit}' /tmp/jetson-bt-info.txt)"
    paired="$(awk -F': ' '/Paired:/ {print $2; exit}' /tmp/jetson-bt-info.txt)"
    name="$(awk -F': ' '/Name:/ {print $2; exit}' /tmp/jetson-bt-info.txt)"
    echo "  Device: ${name:-?}  Paired=${paired:-?}  Connected=${connected:-?}"
    if [[ "$paired" == "yes" ]]; then
      ok "paired"
    else
      bad "not paired"
    fi
    if [[ "$connected" == "yes" ]]; then
      ok "connected over Bluetooth"
    else
      bad "not connected - run: bt-soundblade   or: bash $0 --connect"
    fi
  fi
fi

sink_id=""
if command -v wpctl >/dev/null; then
  if ! wpctl status >/tmp/jetson-wpctl.txt 2>/dev/null; then
    bad "wpctl cannot reach PipeWire (check XDG_RUNTIME_DIR / ~/.bashrc)"
  else
    sink_id="$(
      awk -v name="$BT_NAME" '
        /Sinks:/ {in_sinks=1; next}
        /Sources:/ {in_sinks=0}
        in_sinks && index($0, name) && match($0, /[0-9]+\./) {
          print substr($0, RSTART, RLENGTH - 1)
          exit
        }
      ' /tmp/jetson-wpctl.txt
    )"
    if [[ -n "$sink_id" ]]; then
      ok "PipeWire sink present: $BT_NAME (id $sink_id)"
      wpctl set-default "$sink_id" >/dev/null 2>&1 || true
      wpctl set-volume "$sink_id" 1.0 >/dev/null 2>&1 || true
      wpctl status >/tmp/jetson-wpctl.txt 2>/dev/null || true
      if grep -A20 'Sinks:' /tmp/jetson-wpctl.txt | grep '\*' | grep -F "$BT_NAME" >/dev/null; then
        ok "default sink is $BT_NAME"
      else
        warn "sink exists but may not be default - check: wpctl status"
      fi
    else
      bad "no PipeWire sink named $BT_NAME (GDM may own BlueZ - see docs/jetson/bluetooth-speaker-ssh.md)"
    fi
  fi
else
  bad "wpctl not found"
fi

if [[ "$DO_PLAYBACK" -eq 1 ]]; then
  if [[ ! -f "$WAV_SAMPLE" ]]; then
    bad "test wav missing: $WAV_SAMPLE"
  elif [[ -z "$sink_id" ]]; then
    warn "skipping speaker playback - no sink"
  elif command -v pw-play >/dev/null; then
    echo "  Playing test sample on $BT_NAME (listen for audio)..."
    if pw-play "$WAV_SAMPLE"; then
      ok "pw-play finished (did you hear it?)"
    else
      bad "pw-play failed"
    fi
  else
    bad "pw-play not found"
  fi
fi

echo ""

# ---------------------------------------------------------------------------
# USB microphone (Fifine)
# ---------------------------------------------------------------------------
echo "--- USB microphone ---"

if lsusb 2>/dev/null | grep -qi "$MIC_USB_ID"; then
  ok "lsusb sees $MIC_USB_ID ($(lsusb | grep -i "$MIC_USB_ID" | sed 's/.*ID //'))"
elif lsusb 2>/dev/null | grep -qi "$MIC_NAME_MATCH"; then
  ok "lsusb sees name match '$MIC_NAME_MATCH'"
  lsusb | grep -i "$MIC_NAME_MATCH" | sed 's/^/         /'
else
  bad "microphone not in lsusb (plug in Fifine USB - expect id $MIC_USB_ID)"
fi

alsa_card=""
if arecord -l 2>/dev/null | grep -qi "$MIC_NAME_MATCH"; then
  alsa_card="$(arecord -l | grep -i "$MIC_NAME_MATCH" | head -1 | sed -n 's/.*card \([0-9]*\):.*/\1/p')"
  ok "ALSA capture device present (card ${alsa_card:-?})"
  arecord -l | grep -A1 -i "$MIC_NAME_MATCH" | sed 's/^/         /'
else
  bad "not in arecord -l (kernel did not register USB Audio for $MIC_NAME_MATCH)"
fi

pa_index=""
pa_name=""
if [[ -x "$REPO/.venv/bin/python" ]] || command -v uv >/dev/null; then
  pa_out="$(
    export PATH="${HOME}/.local/bin:${PATH}"
    cd "$REPO"
    uv run python - <<PY 2>/dev/null
import sounddevice as sd
match = "${MIC_NAME_MATCH}".lower()
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0 and match in d["name"].lower():
        print(f"{i}\t{d['name']}\t{d['max_input_channels']}")
        break
else:
    raise SystemExit(1)
PY
  )" || pa_out=""
  if [[ -n "$pa_out" ]]; then
    pa_index="$(printf '%s\n' "$pa_out" | cut -f1)"
    pa_name="$(printf '%s\n' "$pa_out" | cut -f2)"
    pa_ch="$(printf '%s\n' "$pa_out" | cut -f3)"
    ok "PortAudio device: index=$pa_index channels=$pa_ch"
    echo "         $pa_name"
  else
    bad "PortAudio/sounddevice does not list a '$MIC_NAME_MATCH' input"
  fi
else
  warn "uv/.venv missing - skipped sounddevice check"
fi

# .env hint
env_mic="$(grep -E '^ASSISTANT_MIC_DEVICE=' "$REPO/.env" 2>/dev/null | cut -d= -f2- || true)"
if [[ -n "${env_mic// /}" ]]; then
  if [[ -n "$pa_name" ]] && [[ "$env_mic" == *"$MIC_NAME_MATCH"* || "$env_mic" == "$pa_index" ]]; then
    ok ".env ASSISTANT_MIC_DEVICE looks set for mic: $env_mic"
  else
    warn ".env ASSISTANT_MIC_DEVICE=$env_mic (confirm it still matches after reboot)"
  fi
else
  warn ".env ASSISTANT_MIC_DEVICE is blank - set it to the PortAudio name above"
fi

if [[ "$DO_PLAYBACK" -eq 1 ]] && [[ -n "$alsa_card" ]]; then
  rec="/tmp/jetson-mic-test-$$.wav"
  echo "  Recording ${RECORD_SECS}s from plughw:${alsa_card},0 - speak into the mic now..."
  if arecord -D "plughw:${alsa_card},0" -f S16_LE -r 16000 -c 1 -d "$RECORD_SECS" "$rec" 2>/tmp/jetson-arecord-err.txt; then
    bytes="$(wc -c < "$rec" | tr -d ' ')"
    if [[ "$bytes" -gt 1000 ]]; then
      ok "recorded $bytes bytes → $rec"
      if command -v pw-play >/dev/null && [[ -n "$sink_id" ]]; then
        echo "  Playing recording back on $BT_NAME..."
        if pw-play "$rec"; then
          ok "playback of mic recording finished (did you hear yourself?)"
        else
          bad "could not play mic recording"
        fi
      else
        warn "recorded OK but skipped playback (no speaker sink / pw-play)"
      fi
    else
      bad "recording file too small ($bytes bytes)"
    fi
  else
    bad "arecord failed: $(tr '\n' ' ' </tmp/jetson-arecord-err.txt)"
  fi
elif [[ "$DO_PLAYBACK" -eq 1 ]]; then
  warn "skipping mic record test - no ALSA card"
fi

echo ""
echo "=== Summary ==="
echo "  passed: $PASS   failed: $FAIL   warnings: $WARN"
if [[ "$FAIL" -gt 0 ]]; then
  echo "  Result: FAIL"
  echo "  Docs: docs/jetson/bluetooth-speaker-ssh.md , docs/jetson/microphones-ssh.md"
  exit 1
fi
echo "  Result: PASS"
exit 0
