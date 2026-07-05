"""
Central config.

- config/defaults.json  — location, timezone, assistant name, units (copy from config/defaults.example.json)
- .env                  — secrets, local paths, machine-specific overrides (copy from .env.example)

Env vars override defaults.json when both define the same setting.
"""
import json
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


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


def _nested_get(data: dict, *keys: str, default: Any = None) -> Any:
    node = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _load_defaults() -> dict:
    defaults_file = _env("DEFAULTS_FILE", "config/defaults.json")
    path = Path(defaults_file)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        legacy = _PROJECT_ROOT / "defaults.json"
        if legacy.exists():
            path = legacy
        else:
            fallback = _PROJECT_ROOT / "config" / "defaults.example.json"
            path = fallback if fallback.exists() else path
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return _normalize_defaults(json.load(handle))


def _normalize_defaults(raw: dict) -> dict:
    """Strip _doc keys and unwrap { \"value\": ..., \"options\": [...] } objects."""

    def _unwrap(node: Any) -> Any:
        if isinstance(node, dict):
            if "value" in node and any(k in node for k in ("options", "description", "note")):
                return _unwrap(node["value"])
            return {
                key: _unwrap(value)
                for key, value in node.items()
                if not str(key).startswith("_")
            }
        return node

    return _unwrap(raw)


DEFAULTS: dict = _load_defaults()


def _setting(
    env_key: str,
    *json_keys: str,
    default: Any = None,
    cast: Callable[[Any], Any] | None = None,
) -> Any:
    """Resolve a setting: .env override first, then defaults.json, then fallback."""
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return cast(env_value) if cast else env_value
    json_value = _nested_get(DEFAULTS, *json_keys, default=default)
    if json_value is None:
        return default
    return cast(json_value) if cast else json_value


def _optional_str_setting(env_key: str, *json_keys: str) -> str:
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value
    json_value = _nested_get(DEFAULTS, *json_keys, default="")
    if json_value is None:
        return ""
    return str(json_value).strip()


def _optional_float_setting(env_key: str, *json_keys: str) -> float | None:
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return float(env_value)
    json_value = _nested_get(DEFAULTS, *json_keys, default=None)
    if json_value is None or json_value == "":
        return None
    return float(json_value)


def _normalize_llama_url(url: str) -> str:
    """Upgrade legacy /completion URLs to the chat-completions endpoint."""
    url = url.rstrip("/")
    if url.endswith("/completion"):
        return url[: -len("/completion")] + "/v1/chat/completions"
    return url


# --- Local model servers ---
WHISPER_SERVER_URL = _env("WHISPER_SERVER_URL", "http://127.0.0.1:8080/inference")
LLAMA_SERVER_URL = _normalize_llama_url(
    _env("LLAMA_SERVER_URL", "http://127.0.0.1:8081/v1/chat/completions")
)
LLAMA_MODEL_NAME = _env("LLAMA_MODEL_NAME", "qwen2.5-3b-instruct-q4_k_m")

# --- Wake word ---
WAKE_WORD_MODEL = _env("WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_THRESHOLD = float(_env("WAKE_WORD_THRESHOLD", "0.7"))
WAKE_WORD_CONSECUTIVE_HITS = int(_env("WAKE_WORD_CONSECUTIVE_HITS", "4"))
WAKE_WORD_INTERRUPT_THRESHOLD = float(_env("WAKE_WORD_INTERRUPT_THRESHOLD", "0.85"))
WAKE_WORD_INTERRUPT_GRACE_SECONDS = float(_env("WAKE_WORD_INTERRUPT_GRACE_SECONDS", "1.5"))
WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS = float(
    _env("WAKE_WORD_POST_SPEECH_COOLDOWN_SECONDS", "2.0")
)
WAKE_WORD_PRE_RECORD_DELAY_SECONDS = float(
    _env("WAKE_WORD_PRE_RECORD_DELAY_SECONDS", "0.15")
)

# --- Recording (VAD endpointing) ---
# Seconds of silence after speech before we stop recording and send to STT
SILENCE_TIMEOUT_SECONDS = float(_env("SILENCE_TIMEOUT_SECONDS", "0.7"))
NO_SPEECH_TIMEOUT_SECONDS = float(_env("NO_SPEECH_TIMEOUT_SECONDS", "3.0"))
MAX_RECORD_SECONDS = float(_env("MAX_RECORD_SECONDS", "12"))

# --- Audio device ---
MIC_DEVICE = _env_optional("ASSISTANT_MIC_DEVICE")
SPEAKER_DEVICE = _env_optional("ASSISTANT_SPEAKER_DEVICE")
SAMPLE_RATE = 16000

# --- Piper TTS ---
PIPER_BIN = _env("PIPER_BIN", "piper")
PIPER_VOICE = _env("PIPER_VOICE")
# Speaking cadence. <1.0 = faster speech, >1.0 = slower. 1.0 = model default.
PIPER_LENGTH_SCALE = float(_env("PIPER_LENGTH_SCALE", "1.0"))

# --- Navidrome (self-hosted music) ---
NAVIDROME_URL = _env("NAVIDROME_URL", "http://127.0.0.1:4533")
NAVIDROME_USER = _env("NAVIDROME_USER")
NAVIDROME_PASS = _env("NAVIDROME_PASS")

# --- From defaults.json (env vars still override) ---
TIMEZONE = _setting("TIMEZONE", "timezone", default="America/Toronto")
ASSISTANT_NAME = _setting("ASSISTANT_NAME", "assistant", "name", default="Jarvis")

LOCATION_STREET = _optional_str_setting("LOCATION_STREET", "location", "street")
LOCATION_CITY = _optional_str_setting("LOCATION_CITY", "location", "city")
LOCATION_PROVINCE_OR_STATE = _optional_str_setting(
    "LOCATION_PROVINCE_OR_STATE", "location", "province_or_state"
)
LOCATION_COUNTRY = _optional_str_setting("LOCATION_COUNTRY", "location", "country")
LOCATION_POSTAL_CODE = _optional_str_setting("LOCATION_POSTAL_CODE", "location", "postal_code")
WEATHER_LAT = _optional_float_setting("WEATHER_LAT", "location", "latitude")
WEATHER_LON = _optional_float_setting("WEATHER_LON", "location", "longitude")

LOCATION: dict[str, Any] = {
    "street": LOCATION_STREET,
    "city": LOCATION_CITY,
    "province_or_state": LOCATION_PROVINCE_OR_STATE,
    "country": LOCATION_COUNTRY,
    "postal_code": LOCATION_POSTAL_CODE,
    "latitude": WEATHER_LAT,
    "longitude": WEATHER_LON,
}


def has_default_location() -> bool:
    """True when defaults.json (or env) provides a home location for weather."""
    if WEATHER_LAT is not None and WEATHER_LON is not None:
        return True
    if LOCATION_CITY:
        return True
    legacy_name = _nested_get(DEFAULTS, "location", "name")
    return bool(legacy_name and str(legacy_name).strip())


def location_display_name(*, include_country: bool = False) -> str:
    """Short label for weather/time context, e.g. 'Ottawa, ON'."""
    legacy_name = _nested_get(DEFAULTS, "location", "name")
    if legacy_name and not os.environ.get("LOCATION_CITY", "").strip():
        return str(legacy_name)
    parts = [p for p in (LOCATION_CITY, LOCATION_PROVINCE_OR_STATE) if p]
    if include_country and LOCATION_COUNTRY:
        parts.append(LOCATION_COUNTRY)
    return ", ".join(parts)


def location_full_address() -> str:
    """Full address string from structured location fields."""
    line1 = LOCATION_STREET
    line2 = ", ".join(
        p
        for p in (
            LOCATION_CITY,
            LOCATION_PROVINCE_OR_STATE,
            LOCATION_POSTAL_CODE,
        )
        if p
    )
    parts = [p for p in (line1, line2, LOCATION_COUNTRY) if p]
    return ", ".join(parts)


def default_location_geocode_query() -> str:
    """Place name to geocode when lat/lon are not set but city is."""
    return ", ".join(
        p for p in (LOCATION_CITY, LOCATION_PROVINCE_OR_STATE, LOCATION_COUNTRY) if p
    )


def location_weather_label() -> str:
    """Full location for spoken weather: city, province/state, country."""
    legacy_name = _nested_get(DEFAULTS, "location", "name")
    if legacy_name and not os.environ.get("LOCATION_CITY", "").strip():
        return str(legacy_name)
    parts = [p for p in (LOCATION_CITY, LOCATION_PROVINCE_OR_STATE, LOCATION_COUNTRY) if p]
    return ", ".join(parts)


WEATHER_LOCATION_NAME = _env("WEATHER_LOCATION_NAME") or location_display_name()
TEMPERATURE_UNIT = _setting("TEMPERATURE_UNIT", "units", "temperature", default="celsius")
WIND_UNIT = _setting("WIND_UNIT", "units", "wind", default="km/h")
TIME_FORMAT = _setting("TIME_FORMAT", "time", "format", default="12h")

# --- Store hours (OpenStreetMap by default; optional Google Places) ---
STORE_SEARCH_RADIUS_KM = float(_env("STORE_SEARCH_RADIUS_KM", "10"))
GOOGLE_PLACES_API_KEY = _env("GOOGLE_PLACES_API_KEY", "").strip()
