"""
After the wake word fires, this records until the person stops talking
(silence-based endpointing via Silero VAD) instead of a fixed-length clip.

pip install sounddevice numpy torch silero-vad
"""
import numpy as np
import sounddevice as sd
import torch

from config import SAMPLE_RATE, MIC_DEVICE, SILENCE_TIMEOUT_SECONDS, NO_SPEECH_TIMEOUT_SECONDS, MAX_RECORD_SECONDS

_vad_model = None


def warmup() -> None:
    """Load Silero VAD at startup so the first recording is not delayed."""
    global _vad_model
    if _vad_model is None:
        try:
            _vad_model, _utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
            )
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Silero VAD requires torchaudio. Install torchaudio and run uv sync again."
            ) from exc


def record_utterance() -> np.ndarray:
    """Records mic audio until silence follows speech. Returns int16 PCM."""
    global _vad_model

    warmup()

    frames = []
    silence_chunks = 0
    chunk_size = 512  # 32ms @ 16kHz, what Silero VAD expects
    silence_chunks_needed = int(SILENCE_TIMEOUT_SECONDS * SAMPLE_RATE / chunk_size)
    no_speech_chunks = int(NO_SPEECH_TIMEOUT_SECONDS * SAMPLE_RATE / chunk_size)
    max_chunks = int(MAX_RECORD_SECONDS * SAMPLE_RATE / chunk_size)

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        device=MIC_DEVICE,
    )
    stream.start()
    heard_speech = False

    for chunk_index in range(max_chunks):
        chunk, _ = stream.read(chunk_size)
        frames.append(chunk)

        # Flatten the chunk to 1D (512,) so Silero can read the sample length properly
        audio_float = chunk.flatten().astype(np.float32) / 32768.0

        # Double check to guard against fractional chunks from the audio hardware
        if len(audio_float) >= 512:
            speech_prob = _vad_model(torch.from_numpy(audio_float[:512]), SAMPLE_RATE).item()
        else:
            speech_prob = 0.0

        if speech_prob > 0.5:
            heard_speech = True
            silence_chunks = 0
        elif heard_speech:
            silence_chunks += 1
            if silence_chunks >= silence_chunks_needed:
                break
        elif chunk_index >= no_speech_chunks:
            # Nothing spoken within a few seconds — stop waiting (don't hang 12s)
            break

    stream.stop()
    stream.close()
    return np.concatenate(frames).flatten()
