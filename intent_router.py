"""
Cheap keyword-based routing before the LLM ever gets involved. This is
deliberately simple rather than clever: a 3B model has no business guessing
store hours or track names from memory, so anything factual or actionable
gets routed to a real data source, and the LLM's only job is turning that
data (or a general question) into a natural spoken sentence.

If your intents get more varied than this, swap the keyword matching for
Qwen's function-calling / tool-use format instead of adding more if/elif.
"""
import re
from datetime import datetime

from services import weather_client, store_hours_client, music_client, llm_client

_STORE_HOURS_PATTERN = re.compile(
    r"\b(is|are)\s+(.+?)\s+(open|closed)\b", re.IGNORECASE
)
_PLAY_PATTERN = re.compile(r"\bplay\s+(.+)", re.IGNORECASE)


def _format_time() -> str:
    now = datetime.now()
    hour = now.strftime("%I").lstrip("0") or "12"
    return f"It's {hour}:{now.strftime('%M %p')}."


def handle(user_text: str) -> str:
    text = user_text.strip().lower()

    if "what time" in text or "what's the time" in text:
        return _format_time()

    if "weather" in text:
        weather_data = weather_client.get_current_weather()
        return llm_client.ask(user_text, context=weather_data)

    hours_match = _STORE_HOURS_PATTERN.search(text)
    if hours_match:
        store_name = hours_match.group(2)
        hours_data = store_hours_client.is_store_open(store_name)
        return llm_client.ask(user_text, context=hours_data)

    if text in ("stop", "stop music", "pause"):
        return music_client.stop()

    play_match = _PLAY_PATTERN.search(text)
    if play_match:
        return music_client.play_track(play_match.group(1))

    # Fall through: general knowledge / conversation, no external data needed
    return llm_client.ask(user_text)
