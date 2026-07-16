"""
Self-check for the TTS streaming pipeline (no test framework needed):

    uv run python tests/test_tts_stream.py

Verifies that speak_stream overlaps synthesis with playback correctly:
  1. playback order matches input order, and
  2. exactly ONE output stream is opened for the whole utterance
     (the perf property: no per-sentence device reopen).
"""
import numpy as np

from ancilla.services import tts_client


def run() -> None:
    inputs = ["one", "two", "three"]
    index_of = {text: i + 1 for i, text in enumerate(inputs)}

    synth_order: list[str] = []

    def fake_synth(text: str):
        synth_order.append(text)
        pcm = np.full(8, index_of[text], dtype=np.int16).tobytes()
        return pcm, 22050

    play_order: list[int] = []

    class FakeStream:
        opens = 0

        def __init__(self, **kwargs):
            FakeStream.opens += 1

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, audio):
            play_order.append(int(round(float(audio[0]) * 32768.0)))

    orig_rate = tts_client._output_sample_rate
    orig_synth = tts_client._synthesize
    orig_stream = tts_client.sd.OutputStream
    tts_client._output_sample_rate = lambda: 22050
    tts_client._synthesize = fake_synth
    tts_client.sd.OutputStream = FakeStream
    try:
        completed = tts_client.speak_stream(iter(inputs))
    finally:
        tts_client._output_sample_rate = orig_rate
        tts_client._synthesize = orig_synth
        tts_client.sd.OutputStream = orig_stream

    assert completed is True, "speak_stream should report completion"
    assert FakeStream.opens == 1, f"expected 1 output stream, opened {FakeStream.opens}"
    assert play_order == [1, 2, 3], f"playback order wrong: {play_order}"
    assert synth_order == inputs, f"synthesis order wrong: {synth_order}"
    print("tts pipeline self-check passed")


if __name__ == "__main__":
    run()
