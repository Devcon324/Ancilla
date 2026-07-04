"""
Talks to a running whisper-server (from whisper.cpp), started separately with:

  ~/whisper.cpp/build/bin/whisper-server \
    -m ~/whisper.cpp/models/ggml-base.en.bin \
    --host 127.0.0.1 --port 8080

pip install requests numpy scipy
"""
import io
import wave

import numpy as np
import requests

from config import WHISPER_SERVER_URL, SAMPLE_RATE


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
    response = requests.post(
        WHISPER_SERVER_URL,
        files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
        data={"response_format": "text"},
        timeout=10,
    )
    response.raise_for_status()
    return response.text.strip()
