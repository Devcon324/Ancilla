"""Wake word listener using openwakeword. Model is loaded once at import time."""
import logging
import re
import threading

import sounddevice as sd
from openwakeword.model import Model
from openwakeword.utils import download_models

from jetson_assistant.config import (
    SAMPLE_RATE,
    MIC_DEVICE,
    WAKE_WORD_MODEL,
    WAKE_WORD_THRESHOLD,
    WAKE_WORD_CONSECUTIVE_HITS,
    WAKE_WORD_INTERRUPT_THRESHOLD,
    WAKE_WORD_INTERRUPT_GRACE_SECONDS,
    WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS,
)
from jetson_assistant.log_fmt import info as log_line

log = logging.getLogger("assistant.wake_word")

CHUNK_SIZE = 1280

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
    cleaned = _WAKE_PHRASE_PREFIX.sub("", text.strip(), count=1).strip()
    return cleaned


def is_wake_phrase_only(text: str) -> bool:
    """True when STT captured only the wake phrase, not a real command."""
    return bool(_WAKE_PHRASE_ONLY.match(text.strip()))


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


def _score_chunk(chunk) -> float:
    oww = _ensure_model()
    oww.predict(chunk.flatten())
    scores = []
    for mdl in oww.prediction_buffer:
        buf = oww.prediction_buffer[mdl]
        if buf:
            scores.append(buf[-1])
    return max(scores) if scores else 0.0


def _wake_word_detected(score: float, threshold: float) -> bool:
    return score > threshold


def _listen_until_wake_word(
    *,
    threshold: float,
    consecutive_needed: int,
    grace_chunks: int,
    stop_event: threading.Event | None = None,
) -> bool:
    """Returns True when wake word confirmed, False if stop_event is set first."""
    consecutive_hits = 0

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        device=MIC_DEVICE,
        blocksize=CHUNK_SIZE,
    )
    stream.start()
    try:
        chunk_index = 0
        while stop_event is None or not stop_event.is_set():
            chunk, _ = stream.read(CHUNK_SIZE)
            chunk_index += 1
            if chunk_index <= grace_chunks:
                continue

            score = _score_chunk(chunk)
            if _wake_word_detected(score, threshold):
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
    """Block until the wake word is detected."""
    grace_chunks = int(WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
    _listen_until_wake_word(
        threshold=WAKE_WORD_THRESHOLD,
        consecutive_needed=WAKE_WORD_CONSECUTIVE_HITS,
        grace_chunks=grace_chunks,
    )


def listen_for_interrupt(stop_event: threading.Event) -> bool:
    """
    Listen for wake word while another thread plays TTS.
    Returns True if wake word detected, False if stop_event is set first.
    Uses a grace period and higher threshold to avoid speaker-bleed false triggers.
    """
    grace_chunks = int(WAKE_WORD_INTERRUPT_GRACE_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
    return _listen_until_wake_word(
        threshold=WAKE_WORD_INTERRUPT_THRESHOLD,
        consecutive_needed=WAKE_WORD_CONSECUTIVE_HITS,
        grace_chunks=grace_chunks,
        stop_event=stop_event,
    )
