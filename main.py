"""Assistant entrypoint and startup checks for local and Jetson runs."""
import logging
import re
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

from audio.wake_word import (
  listen_for_wake_word,
  listen_for_interrupt,
  strip_wake_phrase,
  is_wake_phrase_only,
)
from audio.capture import record_utterance, warmup as warmup_vad
from services import stt_client, tts_client
import intent_router
from conversation import ConversationHistory
from log_fmt import info as log_line
from config import (
  WHISPER_SERVER_URL,
  LLAMA_SERVER_URL,
  PIPER_BIN,
  PIPER_VOICE,
  WAKE_WORD_MODEL,
  NAVIDROME_USER,
  NAVIDROME_PASS,
  WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS,
  WAKE_WORD_PRE_RECORD_DELAY_SECONDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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
  log.info("Preloading audio models...")
  t0 = time.perf_counter()
  warmup_vad()
  log.info("  VAD ready (%.2fs)", time.perf_counter() - t0)
  if PIPER_VOICE:
    t0 = time.perf_counter()
    tts_client.warmup()
    log.info("  TTS ready (%.2fs)", time.perf_counter() - t0)
  from config import (
    WEATHER_LOCATION_NAME,
    TIMEZONE,
    ASSISTANT_NAME,
    DEFAULTS,
    has_default_location,
    location_full_address,
  )
  if DEFAULTS:
    if has_default_location():
      log.info(
        "Defaults loaded: location=%s, timezone=%s, assistant=%s",
        WEATHER_LOCATION_NAME or location_full_address(),
        TIMEZONE,
        ASSISTANT_NAME,
      )
    else:
      log.info(
        "Defaults loaded: no default location, timezone=%s, assistant=%s",
        TIMEZONE,
        ASSISTANT_NAME,
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


def process_interaction(conversation: ConversationHistory) -> str:
  """
  Handle one user utterance (after wake word, or follow-up without wake word).
  Returns:
    "barge_in"  — user interrupted mid-response; listen again immediately
    "follow_up" — assistant asked a question; listen again without wake word
    "done"      — interaction finished; require wake word for next request
    "empty"     — no usable speech detected
  """
  log_line(log, "Listen", "recording...")
  t_start = time.perf_counter()

  t0 = time.perf_counter()
  pcm = record_utterance()
  t_record = time.perf_counter() - t0

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
    f"record={t_record:.2f}s  stt={t_stt:.2f}s  tts={t_tts:.2f}s  total={time.perf_counter() - t_start:.2f}s",
  )
  if reply_parts:
    label = "Reply" if not interrupted else "Reply"
    log_line(log, label, reply_text)

  if interrupted:
    log_line(log, "Status", "barge-in, listening again")
    return "barge_in"
  if reply_text and _assistant_asked_question(reply_text):
    return "follow_up"
  return "done"


def run() -> None:
  startup_check()
  conversation = ConversationHistory()

  while True:
    log_line(log, "Status", "ready, waiting for wake word")
    listen_for_wake_word()
    log_line(log, "Wake", "detected")
    time.sleep(WAKE_WORD_PRE_RECORD_DELAY_SECONDS)

    while True:
      result = process_interaction(conversation)
      if result == "barge_in":
        continue
      if result == "follow_up":
        log_line(log, "Status", "listening for your reply (no wake word needed)")
        time.sleep(WAKE_WORD_PRE_RECORD_DELAY_SECONDS)
        continue
      break

    time.sleep(WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS)


if __name__ == "__main__":
  run()
