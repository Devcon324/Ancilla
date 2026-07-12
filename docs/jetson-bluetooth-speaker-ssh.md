# Bluetooth Speaker over SSH on Jetson — Troubleshooting & Permanent Setup

This note captures what it took to get a Bluetooth speaker (SOUNDBLADE) working
from an SSH session on the Jetson Orin Nano, and how to make that setup stick
so you do **not** re-export environment variables every login.

Related general speaker guide: [jetson-speakers-ssh.md](jetson-speakers-ssh.md).

---

## Symptoms we hit

| What you see | What it actually means |
|--------------|------------------------|
| `wpctl status` → `Could not connect to PipeWire` | SSH session has empty `XDG_RUNTIME_DIR`, so it cannot find `/run/user/<uid>/pipewire-0` |
| `play` / `aplay` “succeed” but silent | Audio is going to **Built-in Audio Analog Stereo** (Jetson APE) — no real speaker there |
| HDMI silent | No display connected (`card1-DP-1` disconnected) |
| Speaker paired in `bluetoothctl` but still silent | Device can be **Connected: yes** while PipeWire still has **no Bluetooth sink** |
| WirePlumber log: `RegisterProfile() failed: org.bluez.Error.NotPermitted` | **GDM’s PipeWire/WirePlumber** already owns BlueZ audio profiles; your user cannot register A2DP |
| `Node '48' not found` after helper script | Script used the BlueZ **device** id, not the PipeWire **sink** id (e.g. device `48` vs sink `50`) |
| `Failed to connect: org.bluez.Error.InProgress br-connection-busy` | `bluetoothctl connect` was run twice while a connect was already in progress — usually harmless |
| `play WARN alsa: can't encode 0-bit` | Harmless sox quirk; ignore if you hear audio |
| What finally worked for playback | `pw-play /usr/share/sounds/alsa/Front_Center.wav` once SOUNDBLADE was the default sink |

---

## Root causes (short)

1. **SSH does not set the user runtime dir** → PipeWire tools fail until you export `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS`.
2. **Default sink is onboard APE**, not the Bluetooth speaker → must connect BT and `wpctl set-default` the SOUNDBLADE **sink**.
3. **GDM runs its own PipeWire** on `seat0` and steals BlueZ profile registration → kill GDM’s audio stack (or disable logind arbitration) so your user session can create `bluez_output.*` sinks.

---

## One-time setup (do this once)

### 1. Allow your user to own Bluetooth audio (even if GDM is running)

```bash
mkdir -p ~/.config/wireplumber/bluetooth.lua.d
cat > ~/.config/wireplumber/bluetooth.lua.d/51-disable-logind.lua <<'EOF'
-- Allow this user session to own Bluetooth audio even if GDM also runs PipeWire.
bluez_monitor.properties["with-logind"] = false
EOF
```

Restart your audio stack after creating that file:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
systemctl --user restart pipewire pipewire-pulse wireplumber
```

### 2. Install the connect helper

The helper lives at `~/bin/bt-soundblade` on this Jetson. It:

- sets the runtime env if needed
- stops GDM PipeWire if it is stealing BlueZ
- connects the speaker only if needed
- waits for the real **sink** named `SOUNDBLADE`
- sets it as default and raises volume
- optionally plays a test tone with `--test`

Ensure `~/bin` is on your `PATH` (see `.bashrc` below).

Override MAC/name if you change speakers:

```bash
export BT_SPEAKER_MAC=AA:BB:CC:DD:EE:FF
export BT_SPEAKER_NAME="My Speaker"
```

### 3. Make SSH sessions “just work” — `.bashrc`

Add this block to `~/.bashrc` (only runs for interactive shells):

```bash
# --- Jetson audio / PipeWire over SSH ---
# PipeWire sockets live under the user runtime dir. SSH often leaves this unset.
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "${XDG_RUNTIME_DIR}/bus" ]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

# User scripts (bt-soundblade) and uv
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Optional: auto-connect Bluetooth speaker on every interactive login.
# Comment this out if you prefer to connect manually.
# if command -v bt-soundblade >/dev/null 2>&1; then
#   bt-soundblade >/tmp/bt-soundblade.log 2>&1 || true
# fi
```

Apply without logging out:

```bash
source ~/.bashrc
```

After this, `wpctl status` and `pw-play …` should work in new SSH sessions **without** manual `export` lines.

### 4. (Optional) Stop GDM from coming back and stealing BT

If after reboot Bluetooth sinks vanish again:

```bash
sudo pkill -u gdm pipewire
sudo pkill -u gdm wireplumber
systemctl --user restart pipewire pipewire-pulse wireplumber
bt-soundblade
```

For a headless Jarvis box, a more permanent option is to disable the graphical login manager so GDM never starts PipeWire:

```bash
# Only if you do not need a local desktop / HDMI GUI login
sudo systemctl set-default multi-user.target
# reboot when convenient
```

Or leave GDM enabled and rely on `51-disable-logind.lua` + the helper’s GDM kill.

### 5. Point Jarvis at the speaker

```bash
cd ~/github/jetson-nano-jarvis
uv run python -c "import sounddevice as sd; print(sd.query_devices())"
```

Put the SOUNDBLADE name or index in `.env`:

```bash
ASSISTANT_SPEAKER_DEVICE=SOUNDBLADE
```

Prefer the **name** over a numeric index (indexes shift).

---

## Everyday use (after setup)

```bash
ssh devjet@<jetson-host>

# If auto-connect is commented out in .bashrc:
bt-soundblade

# Verify
wpctl status          # look for "* … SOUNDBLADE" under Sinks
pw-play /usr/share/sounds/alsa/Front_Center.wav
```

With `--test`:

```bash
bt-soundblade --test
```

---

## Manual recovery checklist (if sound dies)

Run in order:

```bash
# 1. Runtime dir (skip if already in .bashrc)
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus

# 2. Free BlueZ from GDM
sudo pkill -u gdm pipewire; sudo pkill -u gdm wireplumber
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 2

# 3. Connect + set default sink
bt-soundblade

# 4. Test
pw-play /usr/share/sounds/alsa/Front_Center.wav
```

Confirm with:

```bash
bluetoothctl info F0:13:C3:41:98:60 | grep Connected
# Connected: yes

wpctl status
# Under Sinks, SOUNDBLADE should be marked with *
```

---

## Pairing a new Bluetooth speaker (SSH)

```bash
bluetoothctl
```

```text
power on
agent on
default-agent
scan on
# wait for: [NEW] Device AA:BB:CC:DD:EE:FF Name
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
scan off
quit
```

Then update `BT_SPEAKER_MAC` / `BT_SPEAKER_NAME` or edit `~/bin/bt-soundblade`, and run `bt-soundblade --test`.

---

## Why onboard / HDMI were red herrings

- **APE “Built-in Audio Analog Stereo”** is the Jetson audio graph card. On this board it is not a usable external speaker path for Jarvis demos.
- **HDA HDMI devices** only play if a display/receiver with audio is connected. Ours was `disconnected`.
- Real path for this setup: **BlueZ → PipeWire `bluez_output.*` sink → SOUNDBLADE**, tested with **`pw-play`**.

---

## Files involved on this Jetson

| Path | Purpose |
|------|---------|
| `~/.bashrc` | Persist `XDG_RUNTIME_DIR`, D-Bus, `PATH` |
| `~/bin/bt-soundblade` | Connect speaker + set default sink |
| `~/.config/wireplumber/bluetooth.lua.d/51-disable-logind.lua` | Stop logind from giving BT audio only to GDM |
| `~/.env` in the repo (`ASSISTANT_SPEAKER_DEVICE`) | Tell Jarvis which PortAudio device to use |

---

## Quick “am I good?” test

```bash
wpctl get-volume @DEFAULT_AUDIO_SINK@
pw-play /usr/share/sounds/alsa/Front_Center.wav
```

If you hear the sample on the Bluetooth speaker, audio over SSH is working.

Full speaker + Fifine mic check (play + record + playback):

```bash
bash ~/github/jetson-nano-jarvis/scripts/jetson-audio-test.sh
# or with reconnect:
bash ~/github/jetson-nano-jarvis/scripts/jetson-audio-test.sh --connect
```
