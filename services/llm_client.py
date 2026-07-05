"""
Talks to a running llama-server (from llama.cpp), started separately with:

  ~/llama.cpp/build/bin/llama-server \
    -m ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8081 --n-gpu-layers 999 --ctx-size 2048

Uses the OpenAI-compatible /v1/chat/completions endpoint so llama-server
applies the model's native chat template (fixes token leaking).
"""
import json
import re
import time
import logging
from collections.abc import Iterator

import requests

from config import LLAMA_SERVER_URL, LLAMA_MODEL_NAME, ASSISTANT_NAME
from log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.llm")


def _system_prompt() -> str:
    return (
        f"You are {ASSISTANT_NAME}, a concise voice assistant. Replies are spoken aloud, "
        "so keep them to 1-2 short sentences, no markdown, no lists. If given factual "
        "data to relay (weather, hours, search results), state it plainly and naturally."
    )

TOOL_SELECT_PROMPT = (
    "You are a routing assistant. Given a user question, reply with ONLY one word: "
    "web_search or answer. Use web_search for current events, recent news, live data, "
    "sports scores, stock prices, or anything that needs up-to-date information. "
    "Use answer for general knowledge, jokes, definitions, math, timeless facts, and "
    "anything about you, the assistant, or the person talking to you."
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _build_messages(
    user_text: str,
    context: str | None = None,
    history: list[dict] | None = None,
) -> list[dict]:
    system = _system_prompt()
    if context:
        system += f"\nUse this verified real-time data to answer: {context}"
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages


def _chat_payload(messages: list[dict], *, max_tokens: int = 150, stream: bool = False) -> dict:
    payload = {
        "model": LLAMA_MODEL_NAME,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    return payload


def ask(
    user_text: str,
    context: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """Single-shot reply. context: pre-fetched factual data to phrase naturally."""
    try:
        response = requests.post(
            LLAMA_SERVER_URL,
            json=_chat_payload(_build_messages(user_text, context, history)),
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except requests.RequestException:
        return "I'm having trouble connecting to my brain right now."


def select_tool(user_text: str, history: list[dict] | None = None) -> str:
    """Returns 'web_search' or 'answer'."""
    messages: list[dict] = [{"role": "system", "content": TOOL_SELECT_PROMPT}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": user_text})
    log_line(log, "LLM", f"tool-select ({LLAMA_MODEL_NAME})")
    t0 = time.perf_counter()
    try:
        response = requests.post(
            LLAMA_SERVER_URL,
            json=_chat_payload(messages, max_tokens=8),
            timeout=10,
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]["message"]["content"].strip().lower()
        picked = "web_search" if "web_search" in choice else "answer"
        log_line(log, "LLM", f"tool-select -> {picked} ({time.perf_counter() - t0:.2f}s)")
        if "web_search" in choice:
            return "web_search"
    except requests.RequestException as exc:
        log_warn(log, "LLM", f"tool-select failed: {exc}")
    return "answer"


def _yield_sentences(buffer: str) -> tuple[list[str], str]:
    """Split buffer on sentence boundaries; return complete sentences and remainder."""
    parts = _SENTENCE_END.split(buffer)
    if len(parts) == 1:
        return [], buffer
    complete = [p.strip() for p in parts[:-1] if p.strip()]
    return complete, parts[-1]


def ask_stream(
    user_text: str,
    context: str | None = None,
    history: list[dict] | None = None,
) -> Iterator[str]:
    """Stream LLM reply, yielding speakable sentence chunks."""
    ctx = " with context" if context else ""
    hist = f", {len(history)} prior turn(s)" if history else ""
    log_line(log, "LLM", f"streaming ({LLAMA_MODEL_NAME}){ctx}{hist}")
    try:
        response = requests.post(
            LLAMA_SERVER_URL,
            json=_chat_payload(_build_messages(user_text, context, history), stream=True),
            timeout=60,
            stream=True,
        )
        response.raise_for_status()
        # SSE responses often omit a charset, so requests falls back to
        # ISO-8859-1 and mangles UTF-8 (e.g. "Türkiye" -> "TÃ¼rkiye").
        response.encoding = "utf-8"
    except requests.RequestException:
        yield "I'm having trouble connecting to my brain right now."
        return

    buffer = ""
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            delta = chunk["choices"][0].get("delta", {})
            token = delta.get("content", "")
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        if not token:
            continue
        buffer += token
        complete, buffer = _yield_sentences(buffer)
        yield from complete

    remainder = buffer.strip()
    if remainder:
        yield remainder
    elif not buffer:
        yield "I couldn't complete that request."
