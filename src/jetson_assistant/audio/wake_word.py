"""Wake word listener using openwakeword. Model is loaded once at import time."""
from __future__ import annotations

import logging
import re
import threading

import numpy as np
import sounddevice as sd
from openwakeword.model import Model
from openwakeword.utils import download_models

from jetson_assistant.audio import capture as capture_mod
from jetson_assistant.config import (
    SAMPLE_RATE,
    MIC_DEVICE,
    WAKE_WORD_MODEL,
    WAKE_WORD_THRESHOLD,
    WAKE_WORD_CONSECUTIVE_HITS,
    WAKE_WORD_INTERRUPT_THRESHOLD,
    WAKE_WORD_INTERRUPT_GRACE_SECONDS,
    SILENCE_TIMEOUT_SECONDS,
    NO_SPEECH_TIMEOUT_SECONDS,
    MAX_RECORD_SECONDS,
)
from jetson_assistant.log_fmt import info as log_line
log = logging.getLogger("assistant.wake_word")

# openWakeWord expects 80 ms @ 16 kHz; Silero VAD expects 32 ms.
WAKE_CHUNK_SAMPLES = 1280
VAD_CHUNK_SAMPLES = 512

_WAKE_PHRASE_PREFIX = re.compile(
    r"^(?:hey[\s,]*)?jarvis[\s,.!?]*",
    re.IGNORECASE,
)
_WAKE_PHRASE_ONLY = re.compile(
    r"^(?:hey[\s,]*)?jarvis[\s,.!?]*$",
    re.IGNORECASE,
)

_oww: Model | None = None
_init_lock = threading.Lock()


def strip_wake_phrase(text: str) -> str:
    """Remove a leading 'hey jarvis' from transcribed speech."""
    return _WAKE_PHRASE_PREFIX.sub("", text.strip(), count=1).strip()


def is_wake_phrase_only(text: str) -> bool:
    """True when STT captured only the wake phrase, not a real command."""
    return bool(_WAKE_PHRASE_ONLY.match(text.strip()))


def warmup() -> None:
    """Load openWakeWord at startup so the first listen is not delayed."""
    _ensure_model()


def _ensure_model() -> Model:
    global _oww
    if _oww is None:
        with _init_lock:
            if _oww is None:
                download_models(model_names=[WAKE_WORD_MODEL])
                _oww = Model(
                    wakeword_models=[WAKE_WORD_MODEL],
                    inference_framework="onnx",
                )
    return _oww


def _score_pcm(pcm: np.ndarray) -> float:
    oww = _ensure_model()
    oww.predict(pcm.astype(np.int16, copy=False).flatten())
    scores = []
    for mdl in oww.prediction_buffer:
        buf = oww.prediction_buffer[mdl]
        if buf:
            scores.append(buf[-1])
    return max(scores) if scores else 0.0


def _open_input_stream(*, blocksize: int) -> sd.InputStream:
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        device=MIC_DEVICE,
        blocksize=blocksize,
    )
    stream.start()
    return stream


def _listen_until_wake_word(
    *,
    threshold: float,
    consecutive_needed: int,
    grace_chunks: int,
    stop_event: threading.Event | None = None,
) -> bool:
    """Returns True when wake word confirmed, False if stop_event is set first."""
    consecutive_hits = 0
    stream = _open_input_stream(blocksize=WAKE_CHUNK_SAMPLES)
    try:
        chunk_index = 0
        while stop_event is None or not stop_event.is_set():
            chunk, _ = stream.read(WAKE_CHUNK_SAMPLES)
            chunk_index += 1
            if chunk_index <= grace_chunks:
                continue
            score = _score_pcm(chunk)
            if score > threshold:
                consecutive_hits += 1
                if consecutive_hits >= consecutive_needed:
                    log_line(
                        log, "Wake",
                        f"confirmed score={score:.2f} threshold={threshold:.2f}",
                    )
                    return True
            else:
                consecutive_hits = 0
        return False
    finally:
        stream.stop()
        stream.close()


def listen_for_wake_word() -> None:
    """Block until the wake word is detected (no post-TTS grace — that is timed separately)."""
    _listen_until_wake_word(
        threshold=WAKE_WORD_THRESHOLD,
        consecutive_needed=WAKE_WORD_CONSECUTIVE_HITS,
        grace_chunks=0,
    )


def listen_for_interrupt(stop_event: threading.Event) -> bool:
    """
    Listen for wake word while another thread plays TTS.
    Returns True if wake word detected, False if stop_event is set first.
    """
    grace_chunks = int(WAKE_WORD_INTERRUPT_GRACE_SECONDS * SAMPLE_RATE / WAKE_CHUNK_SAMPLES)
    return _listen_until_wake_word(
        threshold=WAKE_WORD_INTERRUPT_THRESHOLD,
        consecutive_needed=WAKE_WORD_CONSECUTIVE_HITS,
        grace_chunks=grace_chunks,
        stop_event=stop_event,
    )


def _vad_speech(chunk: np.ndarray) -> bool:
    """True when Silero thinks this 512-sample chunk is speech."""
    return capture_mod.speech_prob(chunk) > 0.5

def listen_wake_then_utterance() -> np.ndarray:
    """
    One continuous mic stream: wait for wake word, then record until silence.

    Avoids closing/reopening the mic (which drops the rest of
    \"hey jarvis {query}\" spoken in one breath).
    """
    capture_mod.warmup()
    _ensure_model()

    stream = _open_input_stream(blocksize=VAD_CHUNK_SAMPLES)
    wake_pending: list[np.ndarray] = []
    wake_samples = 0
    consecutive_hits = 0

    try:
        # --- Phase 1: wake word ---
        while True:
            chunk, _ = stream.read(VAD_CHUNK_SAMPLES)
            flat = chunk.flatten()
            wake_pending.append(flat)
            wake_samples += len(flat)

            while wake_samples >= WAKE_CHUNK_SAMPLES:
                combined = np.concatenate(wake_pending)
                window = combined[:WAKE_CHUNK_SAMPLES]
                remainder = combined[WAKE_CHUNK_SAMPLES:]
                wake_pending = [remainder] if len(remainder) else []
                wake_samples = len(remainder)

                score = _score_pcm(window)
                if score > WAKE_WORD_THRESHOLD:
                    consecutive_hits += 1
                    if consecutive_hits >= WAKE_WORD_CONSECUTIVE_HITS:
                        log_line(
                            log, "Wake",
                            f"confirmed score={score:.2f} "
                            f"threshold={WAKE_WORD_THRESHOLD:.2f}",
                        )
                        # Keep leftover samples — they may already be the query.
                        frames = [remainder.astype(np.int16)] if len(remainder) else []
                        break
                else:
                    consecutive_hits = 0
            else:
                continue
            break

        # --- Phase 2: same stream, VAD endpointing ---
        silence_chunks = 0
        silence_needed = int(SILENCE_TIMEOUT_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        no_speech_chunks = int(NO_SPEECH_TIMEOUT_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        max_chunks = int(MAX_RECORD_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        heard_speech = False
        for leftover in frames:
            if len(leftover) >= VAD_CHUNK_SAMPLES and _vad_speech(leftover[:VAD_CHUNK_SAMPLES]):
                heard_speech = True
                break

        for chunk_index in range(max_chunks):
            chunk, _ = stream.read(VAD_CHUNK_SAMPLES)
            flat = chunk.flatten()
            frames.append(flat.copy())

            if _vad_speech(flat):
                heard_speech = True
                silence_chunks = 0
            elif heard_speech:
                silence_chunks += 1
                if silence_chunks >= silence_needed:
                    break
            elif chunk_index >= no_speech_chunks:
                break

        if not frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(frames).astype(np.int16)
    finally:
        stream.stop()
        stream.close()
