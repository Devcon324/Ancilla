"""
Pipes text through Piper and plays the resulting audio through sounddevice.
Piper is a local binary, not a server — this just shells out to it.
"""
import numpy as np
import subprocess
import sounddevice as sd

from config import PIPER_BIN, PIPER_VOICE, SPEAKER_DEVICE


def _play_raw_pcm(raw_pcm: bytes) -> None:
    audio = np.frombuffer(raw_pcm, dtype=np.int16)
    sd.play(audio, samplerate=22050, device=SPEAKER_DEVICE)
    sd.wait()


def speak(text: str) -> None:
    if not PIPER_VOICE:
        raise RuntimeError("PIPER_VOICE is not configured")

    piper = subprocess.Popen(
        [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    raw_pcm, _ = piper.communicate(input=text.encode("utf-8"))
    if raw_pcm:
        _play_raw_pcm(raw_pcm)
