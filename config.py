"""
Central config. Copy .env.config to .env and fill in your local values.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_optional(key: str) -> str | int | None:
    value = os.environ.get(key, "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return value


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


# --- Local model servers ---
WHISPER_SERVER_URL = _env("WHISPER_SERVER_URL", "http://127.0.0.1:8080/inference")
LLAMA_SERVER_URL = _env("LLAMA_SERVER_URL", "http://127.0.0.1:8081/completion")
LLAMA_MODEL_NAME = _env("LLAMA_MODEL_NAME", "qwen2.5-3b-instruct-q4_k_m")

# --- Wake word ---
WAKE_WORD_MODEL = _env("WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_THRESHOLD = float(_env("WAKE_WORD_THRESHOLD", "0.5"))

# --- Audio device ---
MIC_DEVICE = _env_optional("ASSISTANT_MIC_DEVICE")
SPEAKER_DEVICE = _env_optional("ASSISTANT_SPEAKER_DEVICE")
SAMPLE_RATE = 16000

# --- Piper TTS ---
PIPER_BIN = _env("PIPER_BIN", "piper")
PIPER_VOICE = _env("PIPER_VOICE")

# --- Navidrome (self-hosted music) ---
NAVIDROME_URL = _env("NAVIDROME_URL", "http://127.0.0.1:4533")
NAVIDROME_USER = _env("NAVIDROME_USER")
NAVIDROME_PASS = _env("NAVIDROME_PASS")

# --- Weather ---
WEATHER_LAT = _env_float("WEATHER_LAT", 45.4215)
WEATHER_LON = _env_float("WEATHER_LON", -75.6972)
WEATHER_LOCATION_NAME = _env("WEATHER_LOCATION_NAME", "Ottawa, ON")

# --- Store hours (Google Places) ---
GOOGLE_PLACES_API_KEY = _env("GOOGLE_PLACES_API_KEY")
