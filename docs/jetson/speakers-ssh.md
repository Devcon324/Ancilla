# Connect Speakers on Jetson (over SSH)

Use this when you are SSHed into the Jetson Orin Nano and need Jarvis to play
TTS audio through a **Bluetooth speaker** or a **wired / HDMI speaker**.

The assistant plays audio with PortAudio (`sounddevice`). Point it at the right
output with `ASSISTANT_SPEAKER_DEVICE` in `.env`.

All commands below run **on the Jetson** after:

```bash
ssh devjet@<jetson-host>
cd ~/github/Ancilla
export PATH="$HOME/.local/bin:$PATH"
```

---

## 1. Prerequisites

```bash
# Bluetooth stack (for BT speakers)
systemctl is-active bluetooth          # should print: active
groups                                 # you should be in: audio, plugdev

# PipeWire (audio routing on this Jetson image)
systemctl --user is-active pipewire pipewire-pulse
```

If Bluetooth is inactive:

```bash
sudo systemctl enable --now bluetooth
```

If PortAudio is missing (sounddevice will fail):

```bash
sudo apt-get install -y portaudio19-dev libportaudio2
```

---

## 2. List playback devices the app can use

```bash
uv run python -c "import sounddevice as sd; print(sd.query_devices())"
```

Also useful:

```bash
aplay -l
wpctl status
```

Look for devices with **output channels > 0**. Typical names:

| Kind | Example name in `query_devices()` |
|------|-----------------------------------|
| HDMI TV / monitor | `NVIDIA Jetson Orin Nano HDA: HDMI 0` |
| Bluetooth speaker | device name from pairing (often via PipeWire `bluez_output…`) |
| USB DAC / wired amp | brand name under ALSA / PipeWire |
| System default | `default` |

Note the **device index** (left column) or the **exact name string**.

---

## Option A — Wired speaker / HDMI

### A1. Plug in hardware

- **3.5 mm / USB speaker or DAC** → plug into the Jetson USB or audio jack (if present).
- **HDMI / DisplayPort audio** → connect a display or AV receiver that has speakers.

### A2. Confirm ALSA sees it

```bash
aplay -l
```

### A3. Play a quick test tone

Replace `plughw:0,3` with a card/device from `aplay -l` (HDMI is often card 0):

```bash
# sine wave ~2 seconds (needs sox)
sudo apt-get install -y sox
play -n synth 2 sine 440

# or play a wav through a specific ALSA device
aplay -D plughw:0,3 /usr/share/sounds/alsa/Front_Center.wav
```

Piper smoke test through the default device:

```bash
echo "speaker test from jarvis" | uv run piper \
  --model models/piper/en/british/en_GB-northern_english_male-medium.onnx \
  --output-raw | aplay -r 22050 -f S16_LE -t raw -
```

### A4. Point Jarvis at that device

Edit `.env`:

```bash
nano .env
```

Set either the index or the exact name from `query_devices()`:

```bash
# Example: HDMI 0 was index 0
ASSISTANT_SPEAKER_DEVICE=0

# Or by name (preferred — survives reboot better than a shifting index)
ASSISTANT_SPEAKER_DEVICE=NVIDIA Jetson Orin Nano HDA: HDMI 0
```

Leave blank to use the system `default` device.

Restart the assistant after saving.

---

## Option B — Bluetooth speaker

### B1. Put the speaker in pairing mode

Usually hold the power/Bluetooth button until the LED flashes. Keep it within a few metres of the Jetson.

### B2. Pair and connect with `bluetoothctl`

```bash
bluetoothctl
```

Inside the interactive shell:

```text
power on
agent on
default-agent
scan on
```

Wait until you see your speaker, e.g. `[NEW] Device AA:BB:CC:DD:EE:FF My Speaker`.

```text
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
scan off
quit
```

Non-interactive one-liners (replace the MAC):

```bash
bluetoothctl power on
bluetoothctl agent on
bluetoothctl scan on
# wait ~10s, then Ctrl+C or run in another SSH session:
bluetoothctl pair AA:BB:CC:DD:EE:FF
bluetoothctl trust AA:BB:CC:DD:EE:FF
bluetoothctl connect AA:BB:CC:DD:EE:FF
```

Check connection:

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF | grep -E 'Name|Connected|UUID'
wpctl status
```

You want `Connected: yes` and an audio sink related to the speaker under PipeWire.

### B3. Set it as the default sink (optional but helpful)

```bash
wpctl status
# note the sink id number next to your speaker under Audio → Sinks
wpctl set-default <SINK_ID>
```

### B4. Test playback

```bash
# after setting default sink
play -n synth 2 sine 440

# or regenerate device list and confirm the BT device appears
uv run python -c "import sounddevice as sd; print(sd.query_devices())"
```

### B5. Point Jarvis at the Bluetooth device

```bash
nano .env
```

```bash
# Use the name or index from query_devices() after pairing
ASSISTANT_SPEAKER_DEVICE=My Speaker
```

Or leave blank if you set the PipeWire default sink and `default` routes correctly.

Restart the assistant.

### B6. Reconnect after reboot

Trusted devices usually reconnect with:

```bash
bluetoothctl connect AA:BB:CC:DD:EE:FF
```

To auto-connect on boot, a simple systemd user service or `@reboot` cron that runs `bluetoothctl connect …` is enough for a headless Jetson.

---

## Wire it into the assistant

1. Start whisper + llama servers (or use `scripts/run.sh`).
2. Confirm `.env` has `ASSISTANT_SPEAKER_DEVICE` set (or blank for default).
3. Run:

```bash
uv run ancilla
```

Say **"hey jarvis"**, then ask something that needs a spoken reply (e.g. *"what time is it"*).

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| No sound | `wpctl status` — is the correct sink default? Volume muted? `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8` |
| BT pairs but no audio sink | Disconnect/reconnect; ensure speaker is in A2DP mode; `bluetoothctl connect …` again |
| `PortAudio library not found` | `sudo apt-get install -y libportaudio2 portaudio19-dev` |
| Device index changed after reboot | Prefer device **name** in `.env`, not a bare index |
| HDMI silent | Display must support audio; try each HDMI entry from `query_devices()` |
| Works with `aplay` but not Jarvis | Jarvis uses PortAudio — set `ASSISTANT_SPEAKER_DEVICE` to a PortAudio-visible device from `query_devices()` |

Related:

- [Bluetooth speaker troubleshooting & permanent SSH setup](bluetooth-speaker-ssh.md) (PipeWire env, GDM conflict, `.bashrc`, `bt-soundblade`)
- [Connect a microphone over SSH](microphones-ssh.md)
