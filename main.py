"""Assistant entrypoint and startup checks for local and Jetson runs."""
import logging
from urllib.parse import urlparse, urlunparse

import requests

from audio.wake_word import listen_for_wake_word
from audio.capture import record_utterance
from services import stt_client, tts_client
import intent_router
from config import (
    WHISPER_SERVER_URL,
    LLAMA_SERVER_URL,
    PIPER_BIN,
    PIPER_VOICE,
    WAKE_WORD_MODEL,
    GOOGLE_PLACES_API_KEY,
    NAVIDROME_USER,
    NAVIDROME_PASS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("assistant")


def _warn_missing_env() -> None:
    required = {
        "PIPER_BIN": PIPER_BIN,
        "PIPER_VOICE": PIPER_VOICE,
        "WAKE_WORD_MODEL": WAKE_WORD_MODEL,
    }
    optional = {
        "GOOGLE_PLACES_API_KEY": GOOGLE_PLACES_API_KEY,
        "NAVIDROME_USER": NAVIDROME_USER,
        "NAVIDROME_PASS": NAVIDROME_PASS,
    }

    missing_required = [name for name, value in required.items() if not value]
    missing_optional = [name for name, value in optional.items() if not value]

    for name in missing_required:
        log.warning("Missing required env/config value: %s", name)
    for name in missing_optional:
        log.info("Not configured yet: %s", name)


def _health_url(service_url: str) -> str:
    parsed = urlparse(service_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def _check_service(name: str, service_url: str) -> None:
    health_url = _health_url(service_url)
    try:
        response = requests.get(health_url, timeout=2)
        if response.ok:
            log.info("%s reachable at %s (HTTP %s)", name, health_url, response.status_code)
        else:
            log.warning("%s returned HTTP %s at %s", name, response.status_code, health_url)
    except requests.RequestException as exc:
        log.warning("%s is not reachable at %s: %s", name, health_url, exc)


def startup_check() -> None:
    log.info("Running startup checks...")
    _warn_missing_env()
    _check_service("whisper-server", WHISPER_SERVER_URL)
    _check_service("llama-server", LLAMA_SERVER_URL)


def run() -> None:
    startup_check()
    log.info("Assistant ready, waiting for wake word...")
    listen_for_wake_word(on_wake_word_detected)


def on_wake_word_detected():
    log.info("Wake word heard, listening...")
    pcm = record_utterance()

    user_text = stt_client.transcribe(pcm)
    if not user_text:
        log.info("Heard nothing usable, going back to sleep.")
        return
    log.info("Heard: %s", user_text)

    reply = intent_router.handle(user_text)
    log.info("Replying: %s", reply)

    tts_client.speak(reply)


if __name__ == "__main__":
    run()
