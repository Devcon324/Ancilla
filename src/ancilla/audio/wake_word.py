"""Wake word listener using openwakeword. Model is loaded once at import time."""
from __future__ import annotations

import logging
import re
import threading

import numpy as np
import sounddevice as sd
from openwakeword.model import Model
from openwakeword.utils import download_models

from ancilla.audio import capture as capture_mod
from ancilla.config import (
    SAMPLE_RATE,
    MIC_DEVICE,
    WAKE_WORD_MODEL,
    WAKE_WORD_THRESHOLD,
    WAKE_WORD_CONSECUTIVE_HITS,
    WAKE_WORD_INTERRUPT_THRESHOLD,
    WAKE_WORD_INTERRUPT_GRACE_SECONDS,
    WAKE_WORD_STARTUP_GRACE_SECONDS,
    SILENCE_TIMEOUT_SECONDS,
    NO_SPEECH_TIMEOUT_SECONDS,
    MAX_RECORD_SECONDS,
)
from ancilla.log_fmt import info as log_line
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
        latency="low",
    )
    stream.start()
    return stream


def _listen_until_wake_word(
    *,
    threshold: float,
    consecutive_needed: int,
    grace_chunks: int,
    stop_event: threading.Event | None = None,
    keep_recent_seconds: float = 0.0,
) -> np.ndarray | bool:
    """
    Returns True/False when keep_recent_seconds<=0 (legacy).
    When keep_recent_seconds>0, returns recent PCM on wake, or None if stopped.
    """
    consecutive_hits = 0
    recent: list[np.ndarray] = []
    recent_samples = 0
    recent_limit = int(keep_recent_seconds * SAMPLE_RATE) if keep_recent_seconds > 0 else 0
    stream = _open_input_stream(blocksize=WAKE_CHUNK_SAMPLES)
    try:
        chunk_index = 0
        while stop_event is None or not stop_event.is_set():
            chunk, _ = stream.read(WAKE_CHUNK_SAMPLES)
            flat = chunk.flatten().astype(np.int16, copy=False)
            chunk_index += 1
            if recent_limit:
                recent.append(flat.copy())
                recent_samples += len(flat)
                while recent_samples > recent_limit and recent:
                    dropped = recent.pop(0)
                    recent_samples -= len(dropped)
            if chunk_index <= grace_chunks:
                continue
            score = _score_pcm(flat)
            if score > threshold:
                consecutive_hits += 1
                if consecutive_hits >= consecutive_needed:
                    log_line(
                        log, "Wake",
                        f"confirmed score={score:.2f} threshold={threshold:.2f}",
                    )
                    if recent_limit:
                        return np.concatenate(recent).astype(np.int16) if recent else flat.copy()
                    return True
            else:
                consecutive_hits = 0
        return None if recent_limit else False
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


def listen_for_interrupt(stop_event: threading.Event) -> np.ndarray | None:
    """
    Detect a wake-word candidate while TTS plays; return recent mic PCM for
    confirmation (STT must hear \"jarvis\"), or None if stop_event wins first.
    """
    grace_chunks = int(WAKE_WORD_INTERRUPT_GRACE_SECONDS * SAMPLE_RATE / WAKE_CHUNK_SAMPLES)
    consecutive = max(WAKE_WORD_CONSECUTIVE_HITS, 3)
    result = _listen_until_wake_word(
        threshold=WAKE_WORD_INTERRUPT_THRESHOLD,
        consecutive_needed=consecutive,
        grace_chunks=grace_chunks,
        stop_event=stop_event,
        keep_recent_seconds=1.8,
    )
    if isinstance(result, np.ndarray):
        return result
    return None


def _vad_speech(chunk: np.ndarray) -> bool:
    """True when Silero thinks this 512-sample chunk is speech."""
    return capture_mod.speech_prob(chunk) > 0.5


def listen_wake_then_utterance(
    *,
    on_wake=None,
    startup_grace_seconds: float | None = None,
) -> np.ndarray:
    """
    One continuous mic stream: wait for wake word, then record until silence.

    Utterance capture starts on the *first* wake-score hit (not after
    confirmation), so \"hey jarvis {command}\" in one breath is not clipped.
    Optional on_wake() runs the instant wake is confirmed (e.g. duck music).
    """
    capture_mod.warmup()
    oww = _ensure_model()
    # Clear prior predictions so a previous "hey jarvis" / TTS echo cannot
    # immediately re-trigger when the mic opens again.
    oww.reset()

    grace = (
        WAKE_WORD_STARTUP_GRACE_SECONDS
        if startup_grace_seconds is None
        else max(0.0, float(startup_grace_seconds))
    )
    grace_chunks = int(grace * SAMPLE_RATE / VAD_CHUNK_SAMPLES)

    stream = _open_input_stream(blocksize=VAD_CHUNK_SAMPLES)
    wake_pending: list[np.ndarray] = []
    wake_samples = 0
    consecutive_hits = 0
    # Audio from first wake hit onward — no gap before "recording" starts.
    utterance: list[np.ndarray] = []
    capturing = False
    chunk_index = 0

    try:
        # --- Phase 1: wake word (capture starts on first hit) ---
        while True:
            chunk, _ = stream.read(VAD_CHUNK_SAMPLES)
            flat = chunk.flatten()
            chunk_index += 1
            in_grace = chunk_index <= grace_chunks

            if capturing:
                utterance.append(flat.copy())
            wake_pending.append(flat)
            wake_samples += len(flat)

            while wake_samples >= WAKE_CHUNK_SAMPLES:
                combined = np.concatenate(wake_pending)
                window = combined[:WAKE_CHUNK_SAMPLES]
                remainder = combined[WAKE_CHUNK_SAMPLES:]
                wake_pending = [remainder] if len(remainder) else []
                wake_samples = len(remainder)

                if in_grace:
                    consecutive_hits = 0
                    capturing = False
                    utterance = []
                    # Still run predict so the model stays warm / aligned.
                    _score_pcm(window)
                    continue

                score = _score_pcm(window)
                if score > WAKE_WORD_THRESHOLD:
                    consecutive_hits += 1
                    if not capturing:
                        # Begin keeping audio immediately (this window + leftover).
                        capturing = True
                        utterance = [window.astype(np.int16).copy()]
                        if len(remainder):
                            utterance.append(remainder.astype(np.int16).copy())
                    if consecutive_hits >= WAKE_WORD_CONSECUTIVE_HITS:
                        log_line(
                            log, "Wake",
                            f"confirmed score={score:.2f} "
                            f"threshold={WAKE_WORD_THRESHOLD:.2f}",
                        )
                        if on_wake is not None:
                            try:
                                on_wake()
                            except Exception:
                                pass
                        break
                else:
                    if consecutive_hits:
                        # False start — discard speculative capture.
                        consecutive_hits = 0
                        capturing = False
                        utterance = []
            else:
                continue
            break

        # --- Phase 2: same stream, VAD endpointing (already capturing) ---
        silence_chunks = 0
        silence_needed = int(SILENCE_TIMEOUT_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        no_speech_chunks = int(NO_SPEECH_TIMEOUT_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        max_chunks = int(MAX_RECORD_SECONDS * SAMPLE_RATE / VAD_CHUNK_SAMPLES)
        # Command speech often overlaps the wake windows already in `utterance`.
        heard_speech = any(
            len(frame) >= VAD_CHUNK_SAMPLES and _vad_speech(frame[:VAD_CHUNK_SAMPLES])
            for frame in utterance
        )
        # Don't treat wake-phrase-only silence as end-of-utterance yet.
        silence_chunks = 0

        for chunk_index in range(max_chunks):
            chunk, _ = stream.read(VAD_CHUNK_SAMPLES)
            flat = chunk.flatten()
            utterance.append(flat.copy())

            if _vad_speech(flat):
                heard_speech = True
                silence_chunks = 0
            elif heard_speech:
                silence_chunks += 1
                if silence_chunks >= silence_needed:
                    break
            elif chunk_index >= no_speech_chunks:
                break

        if not utterance:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(utterance).astype(np.int16)
    finally:
        stream.stop()
        stream.close()
