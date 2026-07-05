"""
Hybrid intent routing: fast keyword/regex paths skip the LLM entirely;
ambiguous questions get a small LLM tool-selection step (web_search vs answer).

The 3B model never guesses facts - it only phrases verified data or selects tools.
"""
import re
import logging
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from jetson_assistant.config import TIMEZONE, TIME_FORMAT
from jetson_assistant.log_fmt import info as log_line
from jetson_assistant.services import weather_client, store_hours_client, music_client, llm_client, search_client
from jetson_assistant.services.store_hours_client import FILLER_RE as _STORE_FILLER

log = logging.getLogger("assistant.router")

# (pattern, intent, capture_group_index) — more specific patterns first
_STORE_INTENT_PATTERNS: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(r"\bhow\s+late\s+is\s+(.+?)\s+open\b", re.I), "closing", 1),
    (re.compile(r"\bwhen\s+(?:will|does|do|is)\s+(.+?)\s+clos(?:e|ing|es)\b", re.I), "closing", 1),
    (re.compile(r"\bwhat\s+time\s+(?:does|do|will)\s+(.+?)\s+clos(?:e|ing|es)\b", re.I), "closing", 1),
    (re.compile(r"\b(.+?)\s+closing\s+time\b", re.I), "closing", 1),
    (re.compile(r"\bclosing\s+time\s+(?:for|of|at)\s+(.+?)\b", re.I), "closing", 1),
    (re.compile(r"\bwhen\s+(?:will|does|do)\s+(.+?)\s+open(?:s|ing)?\b", re.I), "opening", 1),
    (re.compile(r"\bwhat\s+time\s+(?:does|do|will)\s+(.+?)\s+open(?:s|ing)?\b", re.I), "opening", 1),
    (re.compile(r"\b(.+?)\s+opening\s+time\b", re.I), "opening", 1),
    (re.compile(r"\bopening\s+time\s+(?:for|of|at)\s+(.+?)\b", re.I), "opening", 1),
    (re.compile(r"\bwhat\s+are\s+(?:the\s+)?hours\s+(?:for|of|at)\s+(.+?)\b", re.I), "hours", 1),
    (re.compile(r"\bhours\s+(?:for|of|at)\s+(.+?)\b", re.I), "hours", 1),
    (re.compile(r"\b(is|are)\s+(.+?)\s+(open|closed)\b", re.I), "status", 2),
]

_PLAY_PATTERN = re.compile(r"\bplay\s+(.+)", re.IGNORECASE)
_WEATHER_PLACE_PATTERN = re.compile(
    r"(?:weather|temperature|forecast|like).*?(?:in|at|for)\s+(.+?)(?:\?|$|\.)",
    re.IGNORECASE,
)
_TIME_QUERY = re.compile(
    r"(?:"
    r"what(?:'s|\s+is)\s+(?:the\s+)?time"
    r"|what\s+time"
    r"|time\s+is\s+it"
    r"|tell\s+me\s+(?:the\s+)?time"
    r"|current\s+time"
    r")",
    re.IGNORECASE,
)


def _is_time_query(text: str) -> bool:
    return bool(_TIME_QUERY.search(text))


def _format_time() -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    if TIME_FORMAT == "24h":
        return f"It's {now.strftime('%H:%M')}."
    hour = now.strftime("%I").lstrip("0") or "12"
    return f"It's {hour}:{now.strftime('%M %p')}."


def _clean_store_name(name: str) -> str:
    name = name.strip().rstrip("?.!,")
    name = _STORE_FILLER.sub("", name).strip()
    return name


def _extract_store_and_intent(text: str) -> tuple[str, str] | None:
    """Return (store_name, intent) for store-hours queries, or None."""
    normalized = text.strip().lower()
    for pattern, intent, group in _STORE_INTENT_PATTERNS:
        match = pattern.search(normalized)
        if match:
            store_name = _clean_store_name(match.group(group))
            if store_name:
                return store_name, intent
    return None


def _extract_weather_place(text: str) -> str | None:
    match = _WEATHER_PLACE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return None


def _needs_context_phrasing(user_text: str, text: str) -> tuple[str, str] | None:
    """Return (kind, context data) for tool-backed fast paths that need LLM phrasing."""
    if "weather" in text or "temperature" in text or "forecast" in text:
        place = _extract_weather_place(user_text)
        weather_data = weather_client.get_weather_for(place)
        return ("weather", weather_data)

    return None


def allows_barge_in(user_text: str) -> bool:
    """Only LLM-backed replies benefit from barge-in; instant paths skip it."""
    text = user_text.strip().lower()
    if _is_time_query(text):
        return False
    if text in ("stop", "stop music", "pause"):
        return False
    if _PLAY_PATTERN.search(text):
        return False
    if _extract_store_and_intent(text):
        return False
    return True


def iter_reply(
    user_text: str,
    history: list[dict] | None = None,
) -> Iterator[str]:
    """Yield speakable sentence chunks for TTS (supports streaming LLM path)."""
    text = user_text.strip().lower()

    # Instant replies - no LLM
    if _is_time_query(text):
        log_line(log, "Route", "time")
        yield _format_time()
        return

    if text in ("stop", "stop music", "pause"):
        log_line(log, "Route", "music stop")
        yield music_client.stop()
        return

    play_match = _PLAY_PATTERN.search(text)
    if play_match:
        log_line(log, "Route", "music play")
        yield music_client.play_track(play_match.group(1))
        return

    store_info = _extract_store_and_intent(text)
    if store_info:
        store_name, intent = store_info
        log_line(log, "Route", f"store hours ({intent})")
        yield store_hours_client.store_hours(store_name, intent)
        return

    # Tool-backed paths - LLM phrases verified data
    context_info = _needs_context_phrasing(user_text, text)
    if context_info:
        kind, context_data = context_info
        if kind == "weather" or weather_client.is_weather_error(context_data):
            log_line(log, "Route", "weather")
            yield context_data
            return
        log_line(log, "Route", f"{kind} + llm")
        yield from llm_client.ask_stream(user_text, context=context_data, history=history)
        return

    # Fallback: LLM tool selection
    log_line(log, "Route", "llm tool-select")
    tool = llm_client.select_tool(user_text, history=history)
    if tool == "web_search":
        log_line(log, "Route", "web search + llm")
        results = search_client.search(user_text)
        yield from llm_client.ask_stream(
            user_text,
            context=results.context,
            history=history,
            source_labels=results.sources or None,
        )
    else:
        log_line(log, "Route", "llm")
        yield from llm_client.ask_stream(user_text, history=history)
