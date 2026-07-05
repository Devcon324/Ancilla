"""
Pipes text through Piper and plays the resulting audio through sounddevice.
Uses in-process PiperVoice (cached) to avoid ~3s subprocess spawn per utterance.
"""
import logging
import subprocess
import threading
from collections.abc import Iterator

import numpy as np
import sounddevice as sd

from config import PIPER_BIN, PIPER_VOICE, SPEAKER_DEVICE, PIPER_LENGTH_SCALE

log = logging.getLogger("assistant.tts")

_stop_event = threading.Event()
_play_lock = threading.Lock()
_piper_voice = None
_piper_sample_rate = 22050
_use_subprocess = False
_syn_config = None


def warmup() -> None:
    """Load Piper voice at startup so first reply is not delayed."""
    _ensure_voice()


def stop() -> None:
    """Abort any in-progress TTS playback."""
    _stop_event.set()
    sd.stop()


def _ensure_voice():
    global _piper_voice, _piper_sample_rate, _use_subprocess, _syn_config
    if _piper_voice is not None:
        return _piper_voice
    if not PIPER_VOICE:
        raise RuntimeError("PIPER_VOICE is not configured")
    try:
        from piper import PiperVoice

        _piper_voice = PiperVoice.load(PIPER_VOICE)
        _piper_sample_rate = _piper_voice.config.sample_rate
        if PIPER_LENGTH_SCALE != 1.0:
            try:
                from piper import SynthesisConfig

                _syn_config = SynthesisConfig(length_scale=PIPER_LENGTH_SCALE)
            except Exception as exc:  # older piper without SynthesisConfig
                log.warning("length_scale unsupported by this piper (%s)", exc)
        log.info(
            "Piper voice loaded in-process (sample_rate=%s, length_scale=%s)",
            _piper_sample_rate,
            PIPER_LENGTH_SCALE,
        )
        return _piper_voice
    except Exception as exc:
        log.warning("In-process Piper unavailable (%s), falling back to subprocess", exc)
        _use_subprocess = True
        return None


def _synthesize_subprocess(text: str) -> tuple[bytes, int]:
    cmd = [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw"]
    if PIPER_LENGTH_SCALE != 1.0:
        cmd += ["--length-scale", str(PIPER_LENGTH_SCALE)]
    piper = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    raw_pcm, _ = piper.communicate(input=text.encode("utf-8"))
    return raw_pcm, 22050


def _synthesize(text: str) -> tuple[bytes, int]:
    voice = _ensure_voice()
    if _use_subprocess or voice is None:
        return _synthesize_subprocess(text)
    if _syn_config is not None:
        chunks = list(voice.synthesize(text, syn_config=_syn_config))
    else:
        chunks = list(voice.synthesize(text))
    raw_pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
    return raw_pcm, _piper_sample_rate


def _play_pcm_blocking(raw_pcm: bytes, sample_rate: int) -> bool:
    """Play PCM audio. Returns False if stopped early."""
    if not raw_pcm or _stop_event.is_set():
        return False
    audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    block = 2048
    with sd.OutputStream(
        samplerate=sample_rate,
        device=SPEAKER_DEVICE,
        channels=1,
        dtype="float32",
    ) as stream:
        for i in range(0, len(audio), block):
            if _stop_event.is_set():
                return False
            stream.write(audio[i : i + block])
    return True


def speak(text: str) -> None:
    """Speak a single block of text (blocking unless stop() is called)."""
    with _play_lock:
        _stop_event.clear()
        raw_pcm, rate = _synthesize(text)
        _play_pcm_blocking(raw_pcm, rate)


def speak_stream(chunks: Iterator[str]) -> bool:
    """
    Speak sentence chunks as they arrive. Returns True if completed,
    False if stop() interrupted playback.
    """
    with _play_lock:
        _stop_event.clear()
        for chunk in chunks:
            if _stop_event.is_set():
                return False
            chunk = chunk.strip()
            if not chunk:
                continue
            raw_pcm, rate = _synthesize(chunk)
            if not _play_pcm_blocking(raw_pcm, rate):
                return False
        return True
