# Connect a Microphone on Jetson (over SSH)

Use this when you are SSHed into the Jetson Orin Nano and need Jarvis to hear
the wake word and your questions.

**This setup uses a Fifine USB microphone** (verified on this Jetson). Bluetooth
mics are covered at the end as an alternative.

The assistant captures audio with PortAudio (`sounddevice`). Point it at the
right input with `ASSISTANT_MIC_DEVICE` in `.env`.

All commands below run **on the Jetson** after:

```bash
ssh devjet@<jetson-host>
cd ~/github/jetson-nano-jarvis
export PATH="$HOME/.local/bin:$PATH"
```

If `wpctl` fails with `Could not connect to PipeWire`, load the SSH audio env
first (also handled by `~/.bashrc` after the Bluetooth speaker setup):

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
```

> **Note:** A plain USB mic like Fifine has no hardware AEC. Speaker bleed from
> the Bluetooth speaker can false-trigger “hey jarvis” more often than a
> ReSpeaker-style array. Wake-word thresholds in `.env` can help.

---

## Fifine USB mic — quick start (this Jetson)

### What it looks like when plugged in

| Check | Expected |
|-------|----------|
| `lsusb` | `ID 3142:a010  fifine Microphone` |
| `arecord -l` | `card 2: Microphone [fifine Microphone], device 0: USB Audio` |
| PortAudio name | `fifine Microphone: USB Audio (hw:2,0)` |
| PortAudio index (example) | `24` (can change after reboot — prefer the **name**) |
| PipeWire source | `fifine Microphone Analog Stereo` |

Card number (`hw:2,0`) can shift if you plug/unplug USB devices. Always re-check
with the commands below after a reboot.

### 1. Confirm the Fifine is present

```bash
lsusb | grep -i fifine
arecord -l | grep -A2 -i fifine
```

You want:

```text
Bus 001 Device …: ID 3142:a010  fifine Microphone
card 2: Microphone [fifine Microphone], device 0: USB Audio [USB Audio]
```

### 2. Find it for Jarvis (`sounddevice`)

```bash
uv run python -c "import sounddevice as sd
for i,d in enumerate(sd.query_devices()):
    if d['max_input_channels']>0 and 'fifine' in d['name'].lower():
        print(i, d['name'], 'in=', d['max_input_channels'])"
```

Example from this machine:

```text
24 fifine Microphone: USB Audio (hw:2,0) in= 2
```

### 3. Record a 3-second test

ALSA (use the card from `arecord -l`; here card **2**):

```bash
arecord -D plughw:2,0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/fifine-test.wav
# play back on your Bluetooth speaker (PipeWire default sink):
pw-play /tmp/fifine-test.wav
ls -lh /tmp/fifine-test.wav
```

PortAudio (set `DEVICE` to the index from step 2, or use the name):

```bash
uv run python - <<'PY'
import sounddevice as sd
import numpy as np
from scipy.io import wavfile

# Prefer name — stable across reboots when the card number changes
DEVICE = "fifine Microphone: USB Audio (hw:2,0)"
# Or: DEVICE = 24

fs = 16000
print("recording 3s from", DEVICE)
audio = sd.rec(int(3 * fs), samplerate=fs, channels=1, dtype="float32", device=DEVICE)
sd.wait()
peak = float(np.abs(audio).max())
wavfile.write("/tmp/fifine-pa-test.wav", fs, (audio[:, 0] * 32767).astype("int16"))
print(f"wrote /tmp/fifine-pa-test.wav  peak={peak:.4f}")
if peak < 0.01:
    print("WARNING: almost silent — check mute switch / gain knob on the Fifine")
PY
pw-play /tmp/fifine-pa-test.wav
```

Speak into the mic while it records. If `peak` stays near `0.0`, turn up the
Fifine’s gain knob and unmute any hardware mute switch.

### 4. Optional: set Fifine as the PipeWire default source

Useful for system tools; Jarvis still needs `.env` (next step).

```bash
wpctl status
# under Sources, note the id next to "fifine Microphone Analog Stereo"
wpctl set-default <SOURCE_ID>
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0
```

### 5. Point Jarvis at PipeWire (required for 16 kHz)

Fifine hardware only accepts **44100 / 48000 Hz**. openWakeWord and Silero VAD
need **16000 Hz**. Opening `fifine Microphone: USB Audio (hw:2,0)` at 16 kHz
crashes with:

```text
sounddevice.PortAudioError: Error opening InputStream: Invalid sample rate
```

Fix: set Fifine as the PipeWire **default source**, and tell Jarvis to capture
from `pipewire` (which resamples):

```bash
jarvis-audio    # connects SOUNDBLADE + sets Fifine as default mic source
```

In `.env`:

```bash
ASSISTANT_MIC_DEVICE=pipewire
ASSISTANT_SPEAKER_DEVICE=pipewire
```

Do **not** set `ASSISTANT_MIC_DEVICE` to the raw `hw:2,0` Fifine name on this mic.

### 6. Run the assistant

```bash
jarvis-audio    # speaker sink + Fifine source

bash ~/github/jetson-nano-jarvis/scripts/jetson-run.sh
# or, if servers already run:
cd ~/github/jetson-nano-jarvis && uv run jetson-assistant
```

Say **"hey jarvis"**, then *"what time is it"*.

---

## Ignore onboard APE “mics”

`query_devices()` lists many `NVIDIA Jetson Orin Nano APE` inputs. Those are
**not** your Fifine. Always pick the line that contains `fifine Microphone`.

---

## Prerequisites

```bash
groups    # should include: audio, plugdev
```

If PortAudio is missing (`OSError: PortAudio library not found`):

```bash
sudo apt-get install -y portaudio19-dev libportaudio2
```

---

## Fifine-specific tips

| Tip | Detail |
|-----|--------|
| Gain knob | Hardware gain on the mic body — raise it if recordings are quiet |
| Mute switch | Many Fifines have a mute button / LED — check it if peak ≈ 0 |
| USB port | Prefer a direct Jetson USB port; try the other port or a powered hub if it disconnects |
| Card number drift | `hw:2,0` today may be `hw:1,0` tomorrow — re-check `arecord -l` after reboot |
| Wake-word bleed | Bluetooth speaker audio can re-trigger wake word; raise `WAKE_WORD_INTERRUPT_THRESHOLD` or `WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS` in `.env` |
| Sample rate | Jarvis / VAD path uses 16 kHz capture — the tests above use `-r 16000` |

Wake-word tuning in `.env`:

```bash
WAKE_WORD_THRESHOLD=0.7
WAKE_WORD_CONSECUTIVE_HITS=4
WAKE_WORD_INTERRUPT_THRESHOLD=0.85
WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS=2.0
```

---

## Troubleshooting (Fifine)

| Symptom | What to try |
|---------|-------------|
| Not in `lsusb` | Unplug/replug; different USB port; `lsusb` should show `3142:a010` |
| In `lsusb` but not `arecord -l` | Wait a second; `sudo dmesg \| tail`; replug |
| In ALSA but not PortAudio | `systemctl --user restart pipewire pipewire-pulse`; re-run `query_devices()` |
| Recording peak ≈ 0 | Gain knob up; unmute; confirm you used the fifine device not APE |
| Wrong device / no wake word | `.env` still blank or pointing at APE — set `ASSISTANT_MIC_DEVICE=pipewire` and run `jarvis-audio` |
| `Invalid sample rate` (-9997) | Fifine opened as raw `hw:` device at 16 kHz — use `pipewire` in `.env` + `jarvis-audio` |
| Works in `arecord` but not Jarvis | Jarvis needs 16 kHz via PipeWire — not the raw Fifine PortAudio device |
| Index changed after reboot | Switch `.env` to the device **name**, not `24` |

---

## Option B — Bluetooth microphone / headset (alternative)

Only needed if you are not using the Fifine. Quality is lower (HFP ~8–16 kHz).

```bash
bluetoothctl
# pair / trust / connect as in docs/jetson-bluetooth-speaker-ssh.md
wpctl status    # set the BT *source* as default if desired
```

Then set `ASSISTANT_MIC_DEVICE` to the Bluetooth mic name from `query_devices()`.

Same-headset speaker+mic increases barge-in false triggers — prefer Fifine mic +
SOUNDBLADE speaker.

---

## Automated audio test

Checks Bluetooth speaker + Fifine mic, plays a sample, records 3s, plays it back:

```bash
bash ~/github/jetson-nano-jarvis/scripts/jetson-audio-test.sh
# reconnect speaker first if needed:
bash ~/github/jetson-nano-jarvis/scripts/jetson-audio-test.sh --connect
# detect only (no play/record):
bash ~/github/jetson-nano-jarvis/scripts/jetson-audio-test.sh --no-playback
```

## Related

- [Bluetooth speaker troubleshooting & permanent SSH setup](jetson-bluetooth-speaker-ssh.md)
- [Connect speakers over SSH](jetson-speakers-ssh.md)
