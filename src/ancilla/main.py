"""Assistant entrypoint and startup checks for local and Jetson runs."""
import logging
import re
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

from ancilla.audio.wake_word import (
    listen_for_interrupt,
    listen_wake_then_utterance,
    strip_wake_phrase,
    is_wake_phrase_only,
    warmup as warmup_wake_word,
)
from ancilla.audio.capture import record_utterance, record_fixed_seconds, warmup as warmup_vad
from ancilla.services import stt_client, tts_client, music_client, volume_client
from ancilla import intent_router
from ancilla.conversation import ConversationHistory
from ancilla.log_fmt import info as log_line, warning as log_warn, setup_logging
from ancilla.config import (
    WHISPER_SERVER_URL,
    LLAMA_SERVER_URL,
    PIPER_BIN,
    PIPER_VOICE,
    WAKE_WORD_MODEL,
    NAVIDROME_USER,
    NAVIDROME_PASS,
    WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS,
    RESOURCE_LOG_INTERVAL_SECONDS,
    ASSISTANT_BARGE_IN,
    ASSISTANT_STARTUP_VOLUME_PERCENT,
)
from ancilla.resource_monitor import ResourceMonitor
setup_logging()
log = logging.getLogger("assistant")

_QUESTION_END = re.compile(r"[^.!?]*\?\s*$")
_WAKE_IN_TEXT = re.compile(r"\b(?:hey\s+)?jarvis\b", re.IGNORECASE)

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
    # Safe default before any TTS / music (ear-rape failsafe).
    try:
        level = max(0, min(100, int(ASSISTANT_STARTUP_VOLUME_PERCENT)))
        applied = volume_client.set_percent(level)
        log_line(log, "Startup", f"volume set to {applied}%")
    except Exception as exc:
        log_warn(log, "Startup", f"could not set startup volume: {exc}")
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
    from ancilla.config import (
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
    log_line(
        log, "Startup",
        f"barge-in={'on' if ASSISTANT_BARGE_IN else 'off'}",
    )


def _logged_reply_chunks(chunks, parts: list[str]):
    """Pass reply chunks through to TTS while logging the spoken text."""
    for chunk in chunks:
        text = chunk.strip()
        if text:
            parts.append(text)
            log_line(log, "Speak", text)
        yield chunk


def _confirm_barge_in_wake_phrase(candidate_pcm=None) -> bool:
    """
    Confirm barge-in only if STT hears the wake phrase ("jarvis").

    Uses the mic audio around the wake-word candidate first (already contains
    a real "hey Jarvis"). Speaker-bleed false alarms transcribe as TTS/noise
    without "jarvis" and are ignored — no need to cut the reply.
    """
    pcm = candidate_pcm
    if pcm is None or len(pcm) < 3200:
        time.sleep(0.35)
        pcm = record_fixed_seconds(1.3)
    text = (stt_client.transcribe(pcm) or "").strip()
    if not text:
        log_line(log, "Wake", "barge-in rejected (no speech in candidate audio)")
        return False
    if _WAKE_IN_TEXT.search(text):
        log_line(log, "Wake", f"barge-in confirmed ({text!r})")
        return True
    log_line(log, "Wake", f"barge-in rejected (heard {text!r})")
    return False


def _speak_with_barge_in(chunks, *, enable_barge_in: bool) -> bool:
    """
    Play TTS, optionally listening for wake-word barge-in.
    Returns True if user interrupted with a confirmed wake phrase.
    """
    if not enable_barge_in:
        tts_client.speak_stream(chunks)
        return False

    stop_listener = threading.Event()
    confirmed = threading.Event()

    def _wake_listener():
        # Loop so a rejected candidate can be followed by a real wake later.
        while not stop_listener.is_set():
            candidate_pcm = listen_for_interrupt(stop_listener)
            if candidate_pcm is None or stop_listener.is_set():
                return
            log_line(log, "Wake", "barge-in candidate — confirming wake phrase")
            # Pause TTS so speaker bleed does not poison the STT confirm.
            tts_client.pause()
            try:
                if stop_listener.is_set():
                    return
                if _confirm_barge_in_wake_phrase(candidate_pcm):
                    confirmed.set()
                    tts_client.stop()
                    return
            finally:
                if not confirmed.is_set() and not stop_listener.is_set():
                    tts_client.resume()
            # False alarm — keep speaking; resume listening for a real wake.

    listener = threading.Thread(target=_wake_listener, daemon=True)
    listener.start()
    try:
        tts_client.speak_stream(chunks)
    finally:
        stop_listener.set()
        listener.join(timeout=3.0)

    return confirmed.is_set()


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

    if intent_router.is_dismiss_command(user_text):
        log_line(log, "Route", "dismiss (no reply)")
        return "done"

    chunks = intent_router.iter_reply(user_text, history=conversation.as_list())
    reply_parts: list[str] = []
    turn_ends = intent_router.ends_turn(user_text)

    t0 = time.perf_counter()
    interrupted = _speak_with_barge_in(
        _logged_reply_chunks(chunks, reply_parts),
        enable_barge_in=ASSISTANT_BARGE_IN and intent_router.allows_barge_in(user_text),
    )
    t_tts = time.perf_counter() - t0

    # Start music only after the spoken announcement, so playback does not
    # bleed into the mic and trigger another conversational turn.
    if music_client.begin_queued_play():
        turn_ends = True

    reply_text = " ".join(reply_parts)
    if reply_text:
        conversation.add_exchange(user_text, reply_text)

    log_line(
        log, "Timing",
        f"record={t_record:.2f}s  stt={t_stt:.2f}s  tts={t_tts:.2f}s  "
        f"total={time.perf_counter() - t_start:.2f}s",
    )

    if interrupted and not turn_ends:
        log_line(log, "Status", "barge-in, listening again")
        return "barge_in"
    if turn_ends:
        return "done"
    if reply_text and _assistant_asked_question(reply_text):
        return "follow_up"
    return "done"


def run() -> None:
    import atexit
    import signal as signal_mod

    def _cleanup(_signum=None, _frame=None) -> None:
        music_client.shutdown()

    def _cleanup_and_exit(signum, _frame) -> None:
        _cleanup()
        raise SystemExit(128 + int(signum))

    atexit.register(_cleanup)
    for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
        try:
            signal_mod.signal(sig, _cleanup_and_exit)
        except (ValueError, OSError):
            pass

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

    try:
        while True:
            log_line(log, "Status", "ready, waiting for wake word")
            ducked = False

            def _on_wake():
                nonlocal ducked
                log_line(log, "Wake", "detected — listening")
                if music_client.is_playing():
                    ducked = music_client.duck(music_client.DUCK_PERCENT)

            # One mic stream: capture starts on first wake hit (no gap).
            pcm = listen_wake_then_utterance(on_wake=_on_wake)

            result = "done"
            try:
                result = process_interaction(conversation, pcm=pcm)
                follow_ups = 0
                while result in ("barge_in", "follow_up"):
                    if result == "follow_up":
                        follow_ups += 1
                        if follow_ups > 5:
                            log_line(log, "Status", "follow-up limit reached")
                            result = "done"
                            break
                        log_line(log, "Status", "listening for your reply (no wake word needed)")
                    result = process_interaction(conversation)
            except Exception as exc:
                log_warn(log, "Error", f"interaction failed: {exc}")
                result = "done"
            finally:
                if ducked:
                    music_client.unduck()

            # Cooldown so TTS / BT echo does not immediately re-trigger wake.
            cooldown = WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS
            if result == "empty":
                # False wake (music/TTS bleed) — wait a bit longer before listening again.
                cooldown = max(cooldown, 2.0)
            time.sleep(cooldown)
    finally:
        music_client.shutdown()
        if resource_monitor is not None:
            resource_monitor.stop()


if __name__ == "__main__":
    run()
