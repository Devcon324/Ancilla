"""
Continuous wake-word listener. This is the only thing running at all times —
everything else (STT, LLM, TTS) only spins up after a wake word fires, which
is what keeps the Jetson's 4GB budget sane.

pip install openwakeword sounddevice numpy
"""
import numpy as np
import sounddevice as sd
from openwakeword.model import Model
from openwakeword.utils import download_models

from config import WAKE_WORD_MODEL, WAKE_WORD_THRESHOLD, SAMPLE_RATE, MIC_DEVICE

CHUNK_SAMPLES = 1280  # openWakeWord expects ~80ms chunks at 16kHz


def listen_for_wake_word(on_wake):
    """Blocks forever, calling on_wake() each time the wake word fires."""
    # This automatically pulls the missing ONNX files into the .venv library folder
    download_models(model_names=[WAKE_WORD_MODEL])
    oww = Model(
        wakeword_models=[WAKE_WORD_MODEL], 
        inference_framework="onnx"  # Forces openwakeword to use ONNX
    )
    def callback(indata, frames, time_info, status):
        audio = np.frombuffer(indata, dtype=np.int16)
        prediction = oww.predict(audio)
        score = prediction[WAKE_WORD_MODEL]
        if score > WAKE_WORD_THRESHOLD:
            on_wake()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        device=MIC_DEVICE,
        callback=callback,
    ):
        while True:
            sd.sleep(100)
