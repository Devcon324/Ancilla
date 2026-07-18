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

from ancilla.config import LLAMA_SERVER_URL, LLAMA_MODEL_NAME, ASSISTANT_NAME
from ancilla.log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.llm")


def _system_prompt() -> str:
    return (
        f"You are {ASSISTANT_NAME}, a helpful voice assistant. Replies are spoken aloud, "
        "so use plain sentences with no markdown, no bullet lists, and no URLs. "
        "Be specific: include place names, causes, and other concrete facts when you have them. "
        "For casual chat, keep it brief. For news or explanations, give a clear full answer."
    )

TOOL_SELECT_PROMPT = (
    "You are a routing assistant. Given a user question, reply with ONLY one word: "
    "web_search or answer. Use web_search for current events, recent news, live data, "
    "sports scores, stock prices, or anything that needs up-to-date information. "
    "Use answer for general knowledge, jokes, definitions, math, timeless facts, and "
    "anything about you, the assistant, or the person talking to you. "
    "Never use web_search for device controls (volume, music, stop/pause audio)."
)

SEARCH_QUERY_PROMPT = (
    "Rewrite the user's message as a short web search query (max 14 words). "
    "Keep named places and the core topic. Drop conversational filler. "
    "If they ask what is happening or why, keep words that find the cause. "
    "Output ONLY the search query, nothing else."
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _build_messages(
    user_text: str,
    context: str | None = None,
    history: list[dict] | None = None,
    *,
    source_labels: tuple[str, ...] | None = None,
) -> list[dict]:
    system = _system_prompt()
    if context:
        if source_labels:
            names = ", ".join(source_labels)
            system += (
                f"\nWeb search results:\n{context}\n\n"
                "Answer using these results. Prefer concrete facts: what happened, where, "
                "who is affected, and why if the sources say. "
                "Name specific places, people, or numbers when they appear in the results. "
                "Do not invent facts that are not in the results. "
                f"Start with 'According to [source],' using the best source from: {names}. "
                "Then give 3-5 spoken sentences with the useful detail. "
                "Lead with the cause or main finding, then the local effect. "
                "Do not just repeat a headline."
            )
        else:
            system += f"\nUse this verified real-time data to answer: {context}"
    messages: list[dict] = [{"role": "system", "content": system}]
    # Chat history can dilute a 3B model's attention on search facts.
    if history and not source_labels:
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
    except (requests.RequestException, KeyError, IndexError, TypeError, AttributeError) as exc:
        log_warn(log, "LLM", f"tool-select failed: {exc}")
    return "answer"


def rewrite_search_query(user_text: str) -> str:
    """Turn a conversational question into a tight DuckDuckGo query."""
    messages = [
        {"role": "system", "content": SEARCH_QUERY_PROMPT},
        {"role": "user", "content": user_text},
    ]
    t0 = time.perf_counter()
    try:
        response = requests.post(
            LLAMA_SERVER_URL,
            json=_chat_payload(messages, max_tokens=24),
            timeout=10,
        )
        response.raise_for_status()
        query = response.json()["choices"][0]["message"]["content"].strip()
        query = query.strip("\"'`").splitlines()[0].strip()
        # Drop accidental "Search query:" prefixes.
        for prefix in ("search query:", "query:"):
            if query.lower().startswith(prefix):
                query = query[len(prefix) :].strip()
        if 2 <= len(query.split()) <= 16:
            log_line(
                log, "LLM",
                f"search-query -> {query!r} ({time.perf_counter() - t0:.2f}s)",
            )
            return query
    except (requests.RequestException, KeyError, IndexError, TypeError, AttributeError) as exc:
        log_warn(log, "LLM", f"search-query rewrite failed: {exc}")
    return user_text


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
    *,
    source_labels: tuple[str, ...] | None = None,
) -> Iterator[str]:
    """Stream LLM reply, yielding speakable sentence chunks."""
    ctx = " with web search" if source_labels else (" with context" if context else "")
    hist = f", {len(history)} prior turn(s)" if history and not source_labels else ""
    if source_labels:
        log_line(log, "LLM", f"sources: {', '.join(source_labels)}")
    log_line(log, "LLM", f"streaming ({LLAMA_MODEL_NAME}){ctx}{hist}")
    # Search-backed answers need more room than casual chat.
    max_tokens = 320 if source_labels else 150
    try:
        response = requests.post(
            LLAMA_SERVER_URL,
            json=_chat_payload(
                _build_messages(user_text, context, history, source_labels=source_labels),
                max_tokens=max_tokens,
                stream=True,
            ),
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
