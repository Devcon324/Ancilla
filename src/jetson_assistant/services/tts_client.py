"""
Pipes text through Piper and plays the resulting audio through sounddevice.
Uses in-process PiperVoice (cached) to avoid ~3s subprocess spawn per utterance.
"""
import logging
import queue
import subprocess
import threading
from collections.abc import Iterator

import numpy as np
import sounddevice as sd

from jetson_assistant.config import PIPER_BIN, PIPER_VOICE, SPEAKER_DEVICE, PIPER_LENGTH_SCALE
from jetson_assistant.log_fmt import info as log_line, warning as log_warn

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
                log_warn(log, "TTS", f"length_scale unsupported ({exc})")
        log_line(
            log, "TTS",
            f"voice loaded in-process (sample_rate={_piper_sample_rate}, "
            f"length_scale={PIPER_LENGTH_SCALE})",
        )
        return _piper_voice
    except Exception as exc:
        log_warn(log, "TTS", f"in-process unavailable ({exc}), using subprocess")
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


def _output_sample_rate() -> int:
    """Playback rate for the whole utterance (one open stream reused for it)."""
    voice = _ensure_voice()
    if _use_subprocess or voice is None:
        return 22050
    return _piper_sample_rate


def _write_blocking(stream, raw_pcm: bytes) -> bool:
    """Write PCM to an open stream in blocks. Returns False if stopped early."""
    if not raw_pcm:
        return True
    audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    block = 2048
    for i in range(0, len(audio), block):
        if _stop_event.is_set():
            return False
        stream.write(audio[i : i + block])
    return True


def speak_stream(chunks: Iterator[str]) -> bool:
    """
    Speak sentence chunks as they arrive, overlapping synthesis of the next
    sentence with playback of the current one on a single reused output stream.
    Returns True if completed, False if stop() interrupted playback.
    """
    with _play_lock:
        _stop_event.clear()
        rate = _output_sample_rate()

        # Small buffer: synthesize ahead by up to 2 sentences while playing.
        pcm_queue: queue.Queue = queue.Queue(maxsize=2)

        def _produce() -> None:
            try:
                for chunk in chunks:
                    if _stop_event.is_set():
                        break
                    text = chunk.strip()
                    if not text:
                        continue
                    raw_pcm, _ = _synthesize(text)
                    if _stop_event.is_set():
                        break
                    pcm_queue.put(raw_pcm)
            finally:
                pcm_queue.put(None)  # sentinel: no more audio

        producer = threading.Thread(target=_produce, daemon=True)
        producer.start()

        completed = True
        with sd.OutputStream(
            samplerate=rate,
            device=SPEAKER_DEVICE,
            channels=1,
            dtype="float32",
        ) as stream:
            while True:
                raw_pcm = pcm_queue.get()
                if raw_pcm is None:
                    break
                if _stop_event.is_set() or not _write_blocking(stream, raw_pcm):
                    completed = False
                    break

        if not completed:
            # Unblock a producer that may be parked on a full queue, then wait.
            _stop_event.set()
            try:
                while pcm_queue.get_nowait() is not None:
                    pass
            except queue.Empty:
                pass
        producer.join(timeout=2.0)
        return completed
