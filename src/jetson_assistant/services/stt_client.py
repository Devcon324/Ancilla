"""
Talks to a running whisper-server (from whisper.cpp), started separately with:

  ~/whisper.cpp/build/bin/whisper-server \
    -m ~/whisper.cpp/models/ggml-base.en.bin \
    --host 127.0.0.1 --port 8080

pip install requests numpy scipy
"""
import io
import re
import wave

import numpy as np

from jetson_assistant.config import WHISPER_SERVER_URL, SAMPLE_RATE
from jetson_assistant.services.http import SESSION

# whisper.cpp emits these when the clip is silence, music, or unusable
_NO_SPEECH = re.compile(
    r"^(\[[\w\s'.,_-]+\]|\([\w\s'.,_-]+\))$",
    re.IGNORECASE,
)
# e.g. "upbeat music" without brackets (rare)
_MUSIC_NOISE = re.compile(
    r"^(?:upbeat\s+|soft\s+|loud\s+|background\s+|instrumental\s+)?music"
    r"(?:\s+playing)?$",
    re.IGNORECASE,
)


def _pcm_to_wav_bytes(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def transcribe(pcm: np.ndarray) -> str:
    wav_bytes = _pcm_to_wav_bytes(pcm)
    response = SESSION.post(
        WHISPER_SERVER_URL,
        files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
        data={"response_format": "text"},
        timeout=10,
    )
    response.raise_for_status()
    text = response.text.strip()
    if not text or _NO_SPEECH.match(text) or _MUSIC_NOISE.match(text):
        return ""
    # Collapse internal newlines/whitespace so logs and matching stay single-line
    text = " ".join(text.split())
    # Drop transcripts that are only bracketed noise tags after cleanup
    if _NO_SPEECH.match(text) or _MUSIC_NOISE.match(text):
        return ""
    return text
