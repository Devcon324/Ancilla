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

from ancilla.config import TIMEZONE, TIME_FORMAT
from ancilla.log_fmt import info as log_line, warning as log_warn
from ancilla.services import (
    weather_client,
    store_hours_client,
    music_client,
    volume_client,
    llm_client,
    search_client,
    tts_client,
)
from ancilla.services.store_hours_client import FILLER_RE as _STORE_FILLER

log = logging.getLogger("assistant.router")

# Store capture takes the rest of the utterance (not (.+?)\b, which stopped after
# the first word — e.g. "hours of canadian tire in X" became just "canadian").
_STORE_CAPTURE = r"(.+?)(?:\s*[?.!]+)?$"
_STORE_INTENT_PATTERNS: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(r"\bhow\s+late\s+is\s+(.+?)\s+open\b", re.I), "closing", 1),
    (re.compile(r"\bwhen\s+(?:will|does|do|is)\s+(.+?)\s+clos(?:e|ing|es)\b", re.I), "closing", 1),
    (re.compile(r"\bwhat\s+time\s+(?:does|do|will)\s+(.+?)\s+clos(?:e|ing|es)\b", re.I), "closing", 1),
    (re.compile(r"\b(.+?)\s+closing\s+time\b", re.I), "closing", 1),
    (re.compile(r"\bclosing\s+time\s+(?:for|of|at)\s+" + _STORE_CAPTURE, re.I), "closing", 1),
    (re.compile(r"\bwhen\s+(?:will|does|do)\s+(.+?)\s+open(?:s|ing)?\b", re.I), "opening", 1),
    (re.compile(r"\bwhat\s+time\s+(?:does|do|will)\s+(.+?)\s+open(?:s|ing)?\b", re.I), "opening", 1),
    (re.compile(r"\b(.+?)\s+opening\s+time\b", re.I), "opening", 1),
    (re.compile(r"\bopening\s+time\s+(?:for|of|at)\s+" + _STORE_CAPTURE, re.I), "opening", 1),
    (
        re.compile(
            r"\bwhat\s+(?:is|are)\s+(?:the\s+)?(?:opening\s+and\s+closing\s+|opening\s+|closing\s+)?hours\s+(?:for|of|at)\s+"
            + _STORE_CAPTURE,
            re.I,
        ),
        "hours",
        1,
    ),
    (
        re.compile(
            r"\b(?:opening\s+and\s+closing\s+|opening\s+|closing\s+)?hours\s+(?:for|of|at)\s+"
            + _STORE_CAPTURE,
            re.I,
        ),
        "hours",
        1,
    ),
    (re.compile(r"\b(is|are)\s+(.+?)\s+(open|closed)\b", re.I), "status", 2),
]
# Pronouns / filler that match the status pattern but are not store names.
_BAD_STORE_NAMES = frozenset(
    {
        "it",
        "they",
        "them",
        "you",
        "he",
        "she",
        "we",
        "that",
        "this",
        "there",
        "here",
        "anyone",
        "someone",
        "everybody",
        "everything",
        "door",
        "store",
        "the store",
        "shop",
        "the shop",
        "place",
        "the place",
    }
)

_PLAY_PATTERN = re.compile(r"\bplay\s+(.+)", re.IGNORECASE)
_STOP_PATTERN = re.compile(
    r"^(?:please\s+)?(?:"
    r"stop(?:\s+(?:music|playing|playback|that|the\s+music|radio))?"
    r"|pause(?:\s+(?:music|playback|that))?"
    r"|cancel"
    r"|quiet"
    r"|be\s+quiet"
    r"|shut\s+up"
    r"|enough"
    r"|that'?s\s+enough"
    r")(?:\s+please)?$",
    re.IGNORECASE,
)
# Silent dismiss — return to wake wait with no spoken reply.
_DISMISS_PATTERN = re.compile(
    r"^(?:please\s+)?(?:"
    r"never\s*mind"
    r"|nvm"
    r"|forget\s+(?:it|that)"
    r"|disregard(?:\s+that)?"
    r"|nothing"
    r"|false\s+alarm"
    r"|don'?t\s+(?:worry|bother)"
    r"|cancel\s+(?:that|the\s+request)"
    r"|as\s+you\s+were"
    r")(?:\s+please)?$",
    re.IGNORECASE,
)

# Absolute: "set volume to 20%", "volume at 50 percent", "make volume 30"
_VOLUME_SET = re.compile(
    r"(?:"
    r"(?:set|change|adjust|put|make)\s+(?:(?:the|your)\s+)?volume\s*(?:to|at|=)?\s*"
    r"|volume\s*(?:to|at|=)\s*"
    # "turn the volume up to 50" / "volume down to 20" = absolute, not a step
    r"|(?:turn|put|set|change|adjust|make)\s+(?:(?:the|your)\s+)?volume\s+(?:up|down)\s+to\s+"
    r"|volume\s+(?:up|down)\s+to\s+"
    r")"
    r"(?P<num>\d{1,3})\s*(?:%|percent|per\s*cent)?\b",
    re.IGNORECASE,
)
_VOLUME_SET_WORDS = re.compile(
    r"(?:"
    r"(?:set|change|adjust|put|make)\s+(?:(?:the|your)\s+)?volume\s*(?:to|at)?\s*"
    r"|volume\s*(?:to|at)\s*"
    r")"
    r"(?P<word>max(?:imum)?|full|half|mute|zero|min(?:imum)?)\b"
    r"|\b(?P<word2>mute)\s+(?:(?:the|your)\s+)?volume\b"
    r"|\bvolume\s+(?P<word3>mute|max(?:imum)?|full|half|zero)\b",
    re.IGNORECASE,
)
_VOLUME_UP = re.compile(
    r"(?:"
    r"(?:turn|put)\s+(?:the\s+|your\s+)?(?:volume\s+)?up"
    r"|volume\s+up"
    r"|increase\s+(?:the\s+|your\s+)?volume"
    r"|raise\s+(?:the\s+|your\s+)?volume"
    r"|louder"
    r"|make\s+it\s+louder"
    r"|turn\s+it\s+up"
    r"|bump\s+(?:the\s+)?volume"
    r")",
    re.IGNORECASE,
)
_VOLUME_DOWN = re.compile(
    r"(?:"
    r"(?:turn|put)\s+(?:the\s+|your\s+)?(?:volume\s+)?down"
    r"|volume\s+down"
    r"|decrease\s+(?:the\s+|your\s+)?volume"
    r"|lower\s+(?:the\s+|your\s+)?volume"
    r"|quieter"
    r"|softer"
    r"|make\s+it\s+(?:quieter|softer)"
    r"|turn\s+it\s+down"
    r")",
    re.IGNORECASE,
)
_VOLUME_STEP = re.compile(
    r"(?:by\s+)?(?P<num>\d{1,3})\s*(?:%|percent|per\s*cent)?",
    re.IGNORECASE,
)
_VOLUME_QUERY = re.compile(
    r"(?:"
    r"what(?:'s|\s+is)\s+(?:the\s+|your\s+|our\s+)?volume"
    r"|what\s+volume\s+(?:are\s+you\s+at|is\s+it)"
    r"|how\s+loud(?:\s+(?:are\s+you|is\s+it))?"
    r"|current\s+volume"
    r"|volume\s+(?:level|percent|percentage)"
    r"|volume\s+at\s+(?:right\s+)?now"
    r"|tell\s+me\s+(?:the\s+|your\s+)?volume"
    r"|check\s+(?:the\s+)?volume"
    r")",
    re.IGNORECASE,
)
_VOLUME_WORD_LEVELS = {
    "max": 100,
    "maximum": 100,
    "full": 100,
    "half": 50,
    "mute": 0,
    "zero": 0,
    "min": 0,
    "minimum": 0,
}
_WEATHER_PLACE_PATTERN = re.compile(
    r"(?:weather|temperature|forecast|like).*?(?:in|at|for)\s+(.+?)(?:\?|$|\.)",
    re.IGNORECASE,
)
# Clock only — do not match "what time does X close/open".
_TIME_QUERY = re.compile(
    r"(?:"
    r"what(?:'s|\s+is)\s+(?:the\s+)?time(?:\s+is\s+it)?\b"
    r"|what\s+time\s+is\s+it\b"
    r"|time\s+is\s+it\b"
    r"|tell\s+me\s+(?:the\s+)?time\b"
    r"|current\s+time\b"
    r")",
    re.IGNORECASE,
)


def _normalize_command(text: str) -> str:
    """Lowercase and strip trailing punctuation for exact-ish command matching."""
    return re.sub(r"[?.!,]+$", "", text.strip().lower()).strip()


def _is_time_query(text: str) -> bool:
    return bool(_TIME_QUERY.search(text))


def _is_stop_command(text: str) -> bool:
    return bool(_STOP_PATTERN.match(_normalize_command(text)))


def is_dismiss_command(user_text: str) -> bool:
    """True for 'nevermind' / cancel-request — no spoken reply."""
    return bool(_DISMISS_PATTERN.match(_normalize_command(user_text)))


def _volume_step_from_text(text: str, *, default: int = 10) -> int:
    match = _VOLUME_STEP.search(text)
    if not match:
        return default
    try:
        return max(1, min(100, int(match.group("num"))))
    except (TypeError, ValueError):
        return default


def _handle_volume_command(text: str) -> str | None:
    """Fast-path volume control. Returns spoken reply, or None if not a volume command."""
    set_match = _VOLUME_SET.search(text)
    if set_match:
        return volume_client.set_volume_spoken(int(set_match.group("num")))

    word_match = _VOLUME_SET_WORDS.search(text)
    if word_match:
        word = (
            word_match.group("word")
            or word_match.group("word2")
            or word_match.group("word3")
            or ""
        ).lower()
        level = _VOLUME_WORD_LEVELS.get(word)
        if level is not None:
            return volume_client.set_volume_spoken(level)

    if re.search(r"\bunmute\b", text, re.I):
        return volume_client.set_volume_spoken(50)

    # Query before bare up/down so "what's the volume" isn't treated as change.
    if _VOLUME_QUERY.search(text) and not (
        _VOLUME_UP.search(text) or _VOLUME_DOWN.search(text) or _VOLUME_SET.search(text)
    ):
        return volume_client.report_volume()

    if _VOLUME_UP.search(text):
        return volume_client.raise_volume(_volume_step_from_text(text))

    if _VOLUME_DOWN.search(text):
        return volume_client.lower_volume(_volume_step_from_text(text))

    return None


def _stop_everything() -> str:
    """Halt music/radio and any TTS, then return a short spoken ack."""
    tts_client.stop()
    return music_client.stop()


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
            if store_name and store_name not in _BAD_STORE_NAMES:
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


def is_play_command(user_text: str) -> bool:
    return bool(_PLAY_PATTERN.search(user_text.strip().lower()))


def ends_turn(user_text: str) -> bool:
    """True for commands that must return to wake-word wait (no follow-up listen)."""
    text = user_text.strip().lower()
    if is_dismiss_command(text):
        return True
    if _is_stop_command(text):
        return True
    if is_play_command(text):
        return True
    if (
        _VOLUME_SET.search(text)
        or _VOLUME_SET_WORDS.search(text)
        or _VOLUME_UP.search(text)
        or _VOLUME_DOWN.search(text)
        or _VOLUME_QUERY.search(text)
        or re.search(r"\bunmute\b", text, re.I)
    ):
        return True
    return False


def allows_barge_in(user_text: str) -> bool:
    """True when mid-reply wake-word barge-in is useful.

    Instant one-liners (time / play / stop / volume) skip it. Longer replies
    (weather, store hours, LLM) allow it; false speaker-bleed is filtered by
    pause-and-confirm in main._speak_with_barge_in.
    """
    text = user_text.strip().lower()
    if _is_time_query(text):
        return False
    if ends_turn(text):
        return False
    return True


def iter_reply(
    user_text: str,
    history: list[dict] | None = None,
) -> Iterator[str]:
    """Yield speakable sentence chunks for TTS (supports streaming LLM path)."""
    text = user_text.strip().lower()

    # Store hours before clock time so "what time does X close" is not stolen.
    store_info = _extract_store_and_intent(text)
    if store_info:
        store_name, intent = store_info
        log_line(log, "Route", f"store hours ({intent})")
        try:
            yield store_hours_client.store_hours(store_name, intent)
        except Exception as exc:
            log_warn(log, "Route", f"store hours failed: {exc}")
            yield "I couldn't look up those store hours right now."
        return

    # Instant replies - no LLM
    if _is_time_query(text):
        log_line(log, "Route", "time")
        yield _format_time()
        return

    if _is_stop_command(text):
        log_line(log, "Route", "stop")
        # Stop music/TTS; main loop returns to wake-word wait after this turn.
        yield _stop_everything()
        return

    volume_reply = _handle_volume_command(text)
    if volume_reply is not None:
        log_line(log, "Route", "volume")
        yield volume_reply
        return

    play_match = _PLAY_PATTERN.search(text)
    if play_match:
        log_line(log, "Route", "music play")
        yield music_client.play_track(play_match.group(1))
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
        query = llm_client.rewrite_search_query(user_text)
        results = search_client.search(query)
        # Answer from search alone — chat history dilutes facts on a small model.
        yield from llm_client.ask_stream(
            user_text,
            context=results.context,
            history=None,
            source_labels=results.sources or None,
        )
    else:
        log_line(log, "Route", "llm")
        yield from llm_client.ask_stream(user_text, history=history)
