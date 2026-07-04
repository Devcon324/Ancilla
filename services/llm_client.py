"""
Talks to a running llama-server (from llama.cpp), started separately with:

  ~/llama.cpp/build/bin/llama-server \
    -m ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048

pip install requests
"""
import requests

from config import LLAMA_SERVER_URL

SYSTEM_PROMPT = (
    "You are a concise voice assistant. Replies are spoken aloud, so keep them "
    "to 1-2 short sentences, no markdown, no lists. If given factual data to "
    "relay (weather, hours, search results), state it plainly and naturally."
)


def ask(user_text: str, context: str | None = None) -> str:
    """context: pre-fetched factual data (weather/hours/etc) to phrase naturally."""
    prompt_parts = [f"<|system|>\n{SYSTEM_PROMPT}\n"]
    if context:
        prompt_parts.append(f"<|system|>\nData to relay: {context}\n")
    prompt_parts.append(f"<|user|>\n{user_text}\n<|assistant|>\n")

    response = requests.post(
        LLAMA_SERVER_URL,
        json={
            "prompt": "".join(prompt_parts),
            "n_predict": 120,
            "temperature": 0.6,
            "stop": ["<|user|>", "<|system|>"],
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["content"].strip()
