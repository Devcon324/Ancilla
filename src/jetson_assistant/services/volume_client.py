"""
System playback volume via PipeWire (wpctl).

Controls @DEFAULT_AUDIO_SINK@ so Bluetooth and wired speakers both follow
whatever jarvis-audio / bt-soundblade set as the default sink.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

from jetson_assistant.log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.volume")

_VOLUME_RE = re.compile(r"Volume:\s*([0-9.]+)", re.I)
_STEP = 10  # percent


def _wpctl_available() -> bool:
    return shutil.which("wpctl") is not None


def _run_wpctl(*args: str) -> str:
    result = subprocess.run(
        ["wpctl", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    return (result.stdout or "").strip()


def get_percent() -> int:
    """Current default-sink volume as 0–100 (clamped for speech)."""
    if not _wpctl_available():
        raise RuntimeError("wpctl is not installed")
    out = _run_wpctl("get-volume", "@DEFAULT_AUDIO_SINK@")
    match = _VOLUME_RE.search(out)
    if not match:
        raise RuntimeError(f"could not parse volume: {out!r}")
    raw = float(match.group(1))
    return max(0, min(100, int(round(raw * 100))))


def set_percent(percent: int) -> int:
    """Set absolute volume 0–100; raises ValueError if outside that range."""
    if not _wpctl_available():
        raise RuntimeError("wpctl is not installed")
    percent = int(percent)
    if percent > 100 or percent < 0:
        raise ValueError(f"volume out of range: {percent}")
    _run_wpctl("set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%")
    # Hard cap: never leave PipeWire above 100% (soft-boost / ear-rape).
    applied = get_percent()
    if applied > 100:
        _run_wpctl("set-volume", "@DEFAULT_AUDIO_SINK@", "100%")
        applied = 100
    log_line(log, "Volume", f"{applied}%")
    return applied


def set_volume_spoken(percent: int) -> str:
    """Set absolute volume and return a short spoken confirmation."""
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return "I couldn't understand that volume level."
    # Fail-safe: STT often hears "30%" as "230%" — never clamp up to max.
    if percent > 100:
        log_warn(log, "Volume", f"rejected {percent}% (over 100)")
        return (
            "I heard a volume over 100 percent, so I left it unchanged. "
            "Please say a level between 0 and 100."
        )
    if percent < 0:
        log_warn(log, "Volume", f"rejected {percent}% (negative)")
        return "Volume can't be negative. Please say a level between 0 and 100."
    try:
        new = set_percent(percent)
        return f"Volume is now {new} percent."
    except Exception as exc:
        log_warn(log, "Volume", f"set failed: {exc}")
        return "I couldn't change the volume right now."


def raise_volume(step: int = _STEP) -> str:
    try:
        step = max(1, min(100, int(step)))
        current = get_percent()
        if current >= 100:
            return "Volume is already at 100 percent."
        # Never jump past 100 via a large step.
        new = set_percent(min(100, current + step))
        return f"Volume is now {new} percent."
    except Exception as exc:
        log_warn(log, "Volume", f"raise failed: {exc}")
        return "I couldn't change the volume right now."


def lower_volume(step: int = _STEP) -> str:
    try:
        step = max(1, min(100, int(step)))
        current = get_percent()
        if current <= 0:
            return "Volume is already at 0 percent."
        new = set_percent(current - step)
        return f"Volume is now {new} percent."
    except Exception as exc:
        log_warn(log, "Volume", f"lower failed: {exc}")
        return "I couldn't change the volume right now."


def report_volume() -> str:
    try:
        return f"Volume is at {get_percent()} percent."
    except Exception as exc:
        log_warn(log, "Volume", f"get failed: {exc}")
        return "I couldn't read the volume right now."
