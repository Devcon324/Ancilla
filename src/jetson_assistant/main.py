"""Assistant entrypoint and startup checks for local and Jetson runs."""
import logging
import re
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

from jetson_assistant.audio.wake_word import (
    listen_for_interrupt,
    listen_wake_then_utterance,
    strip_wake_phrase,
    is_wake_phrase_only,
    warmup as warmup_wake_word,
)
from jetson_assistant.audio.capture import record_utterance, warmup as warmup_vad
from jetson_assistant.services import stt_client, tts_client
from jetson_assistant import intent_router
from jetson_assistant.conversation import ConversationHistory
from jetson_assistant.log_fmt import info as log_line, warning as log_warn, setup_logging
from jetson_assistant.config import (
    WHISPER_SERVER_URL,
    LLAMA_SERVER_URL,
    PIPER_BIN,
    PIPER_VOICE,
    WAKE_WORD_MODEL,
    NAVIDROME_USER,
    NAVIDROME_PASS,
    WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS,
    WAKE_WORD_PRE_RECORD_DELAY_SECONDS,
    RESOURCE_LOG_INTERVAL_SECONDS,
)
from jetson_assistant.resource_monitor import ResourceMonitor
setup_logging()
log = logging.getLogger("assistant")

_QUESTION_END = re.compile(r"[^.!?]*\?\s*$")

# Quiet noisy HTTP loggers from ddgs/httpx during web search
for _logger_name in ("ddgs", "httpx", "httpcore", "primp"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)


def _warn_missing_env() -> None:
    required = {
        "PIPER_BIN": PIPER_BIN,
        "PIPER_VOICE": PIPER_VOICE,
        "WAKE_WORD_MODEL": WAKE_WORD_MODEL,
    }
    optional = {
        "NAVIDROME_USER": NAVIDROME_USER,
        "NAVIDROME_PASS": NAVIDROME_PASS,
    }

    missing_required = [name for name, value in required.items() if not value]
    missing_optional = [name for name, value in optional.items() if not value]

    for name in missing_required:
        log_warn(log, "Config", f"missing required: {name}")
    for name in missing_optional:
        log_line(log, "Config", f"not configured: {name}")


def _health_url(service_url: str) -> str:
    parsed = urlparse(service_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def _check_service(name: str, service_url: str) -> None:
    health_url = _health_url(service_url)
    try:
        response = requests.get(health_url, timeout=2)
        if response.ok:
            log_line(
                log, "Check",
                f"{name} reachable at {health_url} (HTTP {response.status_code})",
            )
        else:
            log_warn(
                log, "Check",
                f"{name} returned HTTP {response.status_code} at {health_url}",
            )
    except requests.RequestException as exc:
        log_warn(log, "Check", f"{name} not reachable at {health_url}: {exc}")


def startup_check() -> None:
    log_line(log, "Startup", "running checks...")
    _warn_missing_env()
    _check_service("whisper-server", WHISPER_SERVER_URL)
    _check_service("llama-server", LLAMA_SERVER_URL)
    log_line(log, "Startup", "preloading audio models...")
    t0 = time.perf_counter()
    warmup_vad()
    log_line(log, "Startup", f"VAD ready ({time.perf_counter() - t0:.2f}s)")
    t0 = time.perf_counter()
    warmup_wake_word()
    log_line(log, "Startup", f"wake-word ready ({time.perf_counter() - t0:.2f}s)")
    if PIPER_VOICE:
        t0 = time.perf_counter()
        tts_client.warmup()
        log_line(log, "Startup", f"TTS ready ({time.perf_counter() - t0:.2f}s)")
    from jetson_assistant.config import (
        WEATHER_LOCATION_NAME,
        TIMEZONE,
        ASSISTANT_NAME,
        DEFAULTS,
        has_default_location,
        location_full_address,
    )
    if DEFAULTS:
        if has_default_location():
            log_line(
                log, "Startup",
                f"defaults: location={WEATHER_LOCATION_NAME or location_full_address()}, "
                f"timezone={TIMEZONE}, assistant={ASSISTANT_NAME}",
            )
        else:
            log_line(
                log, "Startup",
                f"defaults: no location, timezone={TIMEZONE}, assistant={ASSISTANT_NAME}",
            )


def _logged_reply_chunks(chunks, parts: list[str]):
    """Pass reply chunks through to TTS while logging the spoken text."""
    for chunk in chunks:
        text = chunk.strip()
        if text:
            parts.append(text)
            log_line(log, "Speak", text)
        yield chunk


def _speak_with_barge_in(chunks, *, enable_barge_in: bool) -> bool:
    """
    Play TTS, optionally listening for wake-word barge-in.
    Returns True if user interrupted with a new wake word.
    """
    if not enable_barge_in:
        tts_client.speak_stream(chunks)
        return False

    stop_listener = threading.Event()
    interrupted = threading.Event()

    def _wake_listener():
        if listen_for_interrupt(stop_listener):
            interrupted.set()
            tts_client.stop()

    listener = threading.Thread(target=_wake_listener, daemon=True)
    listener.start()
    try:
        tts_client.speak_stream(chunks)
    finally:
        stop_listener.set()
        listener.join(timeout=2.0)

    return interrupted.is_set()


def _assistant_asked_question(reply: str) -> bool:
    """True when the spoken reply ends with a question."""
    return bool(_QUESTION_END.search(reply.strip()))


def process_interaction(
    conversation: ConversationHistory,
    *,
    pcm=None,
) -> str:
    """
    Handle one user utterance (after wake word, or follow-up without wake word).
    If pcm is provided, skip recording (used for continuous wake→utterance capture).
    Returns:
        "barge_in"  — user interrupted mid-response; listen again immediately
        "follow_up" — assistant asked a question; listen again without wake word
        "done"      — interaction finished; require wake word for next request
        "empty"     — no usable speech detected
    """
    t_start = time.perf_counter()

    if pcm is None:
        log_line(log, "Listen", "recording...")
        t0 = time.perf_counter()
        pcm = record_utterance()
        t_record = time.perf_counter() - t0
    else:
        t_record = 0.0
        log_line(log, "Listen", "using continuous wake→utterance capture")

    t0 = time.perf_counter()
    raw_text = stt_client.transcribe(pcm)
    t_stt = time.perf_counter() - t0
    if not raw_text:
        log_line(log, "Heard", "(nothing usable)")
        return "empty"

    if is_wake_phrase_only(raw_text):
        log_line(log, "Heard", "(wake phrase only)")
        return "done"

    user_text = strip_wake_phrase(raw_text)
    if not user_text:
        log_line(log, "Heard", "(wake phrase only)")
        return "done"

    log_line(log, "Heard", user_text)

    chunks = intent_router.iter_reply(user_text, history=conversation.as_list())
    reply_parts: list[str] = []

    t0 = time.perf_counter()
    interrupted = _speak_with_barge_in(
        _logged_reply_chunks(chunks, reply_parts),
        enable_barge_in=intent_router.allows_barge_in(user_text),
    )
    t_tts = time.perf_counter() - t0

    reply_text = " ".join(reply_parts)
    if reply_text:
        conversation.add_exchange(user_text, reply_text)

    log_line(
        log, "Timing",
        f"record={t_record:.2f}s  stt={t_stt:.2f}s  tts={t_tts:.2f}s  "
        f"total={time.perf_counter() - t_start:.2f}s",
    )

    if interrupted:
        log_line(log, "Status", "barge-in, listening again")
        return "barge_in"
    if reply_text and _assistant_asked_question(reply_text):
        return "follow_up"
    return "done"


def run() -> None:
    startup_check()
    conversation = ConversationHistory()
    resource_monitor = None
    if RESOURCE_LOG_INTERVAL_SECONDS > 0:
        resource_monitor = ResourceMonitor(RESOURCE_LOG_INTERVAL_SECONDS)
        resource_monitor.start()
        log_line(
            log, "Startup",
            f"resource logging every {RESOURCE_LOG_INTERVAL_SECONDS:.0f}s",
        )

    while True:
        log_line(log, "Status", "ready, waiting for wake word")
        # One mic stream: detect wake and keep capturing the spoken query.
        pcm = listen_wake_then_utterance()
        log_line(log, "Wake", "detected")

        result = process_interaction(conversation, pcm=pcm)
        while result in ("barge_in", "follow_up"):
            if result == "follow_up":
                log_line(log, "Status", "listening for your reply (no wake word needed)")
                time.sleep(WAKE_WORD_PRE_RECORD_DELAY_SECONDS)
            result = process_interaction(conversation)

        # Short cooldown so TTS echo does not re-trigger wake (do not also
        # ignore the first N seconds inside the wake listener).
        time.sleep(WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS)


if __name__ == "__main__":
    run()
