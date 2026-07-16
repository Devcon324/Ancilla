"""Expand abbreviations so Piper TTS pronounces them naturally."""
from __future__ import annotations

import re


def for_speech(text: str) -> str:
    """Rewrite unit abbreviations into speakable phrases."""
    if not text:
        return text

    # °C / degrees C → degrees Celsius (keep the number)
    text = re.sub(
        r"(?i)(\d+(?:\.\d+)?)\s*(?:°\s*C|degrees?\s*C)\b",
        r"\1 degrees Celsius",
        text,
    )
    text = re.sub(r"(?i)(?<!\w)°\s*C\b", "degrees Celsius", text)
    text = re.sub(r"(?i)(?<!\w)degrees?\s+C\b", "degrees Celsius", text)

    # °F / degrees F → degrees Fahrenheit
    text = re.sub(
        r"(?i)(\d+(?:\.\d+)?)\s*(?:°\s*F|degrees?\s*F)\b",
        r"\1 degrees Fahrenheit",
        text,
    )
    text = re.sub(r"(?i)(?<!\w)°\s*F\b", "degrees Fahrenheit", text)
    text = re.sub(r"(?i)(?<!\w)degrees?\s+F\b", "degrees Fahrenheit", text)

    # Wind speed
    text = re.sub(r"(?i)\bkm\s*/\s*h\b", "kilometers per hour", text)
    text = re.sub(r"(?i)\bkmh\b", "kilometers per hour", text)
    text = re.sub(r"(?i)\bkph\b", "kilometers per hour", text)
    text = re.sub(r"(?i)\bmph\b", "miles per hour", text)
    text = re.sub(r"(?i)\bmi\s*/\s*h\b", "miles per hour", text)

    return text
