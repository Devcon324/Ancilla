"""
Music playback via mpv.

Backends (first match wins):
  1. Navidrome / Subsonic — your own library (set NAVIDROME_* in .env)
  2. SomaFM — listener-supported, commercial-free internet radio
  3. Radio Browser — open community radio directory (https://api.radio-browser.info)

Needs: sudo apt install mpv
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import socket
import string
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from jetson_assistant.config import NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS
from jetson_assistant.log_fmt import info as log_line, warning as log_warn
from jetson_assistant.services.http import SESSION

log = logging.getLogger("assistant.music")

_player_process: subprocess.Popen | None = None
_pending_url: str | None = None
_duck_restore: float | None = None
DUCK_PERCENT = 10

RADIO_BROWSER_MIRRORS = (
    "https://api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
)
SOMAFM_CHANNELS_URL = "https://somafm.com/channels.json"
USER_AGENT = "jetson-assistant/0.1 (local hobby project)"

_SOMA_HINT = re.compile(r"\bsoma(?:\s*fm)?\b", re.I)
_VAGUE_PLAY = re.compile(
    r"^(?:(?:me|us|my|some|any)\s+)*(?:music|something|a\s+song|songs|radio)"
    r"(?:\s+please)?$",
    re.I,
)
_LOFI_HINT = re.compile(r"\b(?:lo[\s-]?fi|lofihop|chillhop|study\s+beats?|hip\s*hop)\b", re.I)


def _navidrome_configured() -> bool:
    return bool(NAVIDROME_USER and NAVIDROME_PASS and NAVIDROME_URL)


def _mpv_available() -> bool:
    return shutil.which("mpv") is not None


def _auth_params() -> dict:
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    token = hashlib.md5((NAVIDROME_PASS + salt).encode()).hexdigest()
    return {
        "u": NAVIDROME_USER,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "jetson-assistant",
        "f": "json",
    }


def _urlencode_auth() -> str:
    return urlencode(_auth_params())


def _ipc_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "mpv-jarvis.sock")


def _stop_player() -> None:
    global _player_process, _duck_restore
    if _player_process is not None:
        _player_process.terminate()
        try:
            _player_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _player_process.kill()
        _player_process = None
    _duck_restore = None
    path = _ipc_path()
    try:
        os.unlink(path)
    except OSError:
        pass


def _start_stream(url: str, *, volume: int = 100) -> None:
    global _player_process, _duck_restore
    if not _mpv_available():
        raise RuntimeError("mpv is not installed (sudo apt install mpv)")
    _stop_player()
    _duck_restore = None
    _fix_wireplumber_mpv_channel_map()
    ipc = _ipc_path()
    # Force stereo + Pulse/PipeWire so Bluetooth A2DP sinks actually output audio.
    # (WirePlumber can restore mpv as MONO / Music as FC, which silences stereo BT.)
    _player_process = subprocess.Popen(
        [
            "mpv",
            "--no-video",
            "--really-quiet",
            "--no-terminal",
            "--ao=pulse",
            "--audio-channels=stereo",
            f"--input-ipc-server={ipc}",
            f"--volume={max(0, min(100, int(volume)))}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _fix_wireplumber_mpv_channel_map() -> None:
    """Remove broken WirePlumber stream restores that silence stereo Bluetooth."""
    state_dir = Path.home() / ".local/state/wireplumber"
    path = state_dir / "restore-stream"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(True)
    except OSError:
        return
    keep: list[str] = []
    removed = 0
    for line in lines:
        if "application.name:mpv:channelMap=" in line or "media.role:Music:channelMap=" in line:
            removed += 1
            continue
        keep.append(line)
    if not removed:
        return
    try:
        path.write_text("".join(keep), encoding="utf-8")
        log_line(log, "Music", f"cleared {removed} bad WirePlumber channel map(s)")
    except OSError as exc:
        log_warn(log, "Music", f"could not fix WirePlumber restore-stream: {exc}")


def _speakable_title(name: str, *, limit: int = 48) -> str:
    """Shorten radio station titles so TTS does not run for 15+ seconds."""
    cleaned = re.sub(r"\s*\|\|.*$", "", name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip(" -|,;/") + "…"


def _queue_stream(url: str) -> None:
    """Defer playback until after Jarvis finishes speaking the play announcement."""
    global _pending_url
    _pending_url = url


def has_queued_play() -> bool:
    return _pending_url is not None


def cancel_queued_play() -> None:
    global _pending_url
    _pending_url = None


def begin_queued_play() -> bool:
    """Start a play that was resolved during routing. Returns True if started."""
    global _pending_url
    url = _pending_url
    _pending_url = None
    if not url:
        return False
    _start_stream(url, volume=100)
    log_line(log, "Music", "playback started after announcement")
    return True


def _mpv_ipc(command: list) -> dict | None:
    path = _ipc_path()
    if not os.path.exists(path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(path)
            sock.sendall((json.dumps({"command": command}) + "\n").encode())
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        line = data.decode(errors="replace").split("\n", 1)[0].strip()
        return json.loads(line) if line else None
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        log_warn(log, "Music", f"mpv ipc failed: {exc}")
        return None


def get_player_volume() -> int | None:
    result = _mpv_ipc(["get_property", "volume"])
    # mpv JSON IPC uses error: "success" on success.
    if not result or result.get("error") != "success":
        return None
    try:
        return int(round(float(result["data"])))
    except (KeyError, TypeError, ValueError):
        return None


def set_player_volume(percent: int) -> None:
    percent = max(0, min(100, int(percent)))
    _mpv_ipc(["set_property", "volume", percent])


def duck(to_percent: int = DUCK_PERCENT) -> bool:
    """Lower only the music player (not TTS/sink) while Jarvis is active."""
    global _duck_restore
    if not is_playing():
        return False
    # Wait briefly for IPC socket on a freshly started stream.
    for _ in range(10):
        if os.path.exists(_ipc_path()):
            break
        time.sleep(0.05)
    if _duck_restore is None:
        current = get_player_volume()
        _duck_restore = 100.0 if current is None else float(current)
    set_player_volume(to_percent)
    log_line(log, "Music", f"ducked to {to_percent}% (was {int(_duck_restore)}%)")
    return True


def unduck() -> None:
    """Restore music volume after Jarvis finishes the interaction."""
    global _duck_restore
    if _duck_restore is None:
        return
    restore = int(_duck_restore)
    _duck_restore = None
    if is_playing():
        set_player_volume(restore)
        log_line(log, "Music", f"unducked to {restore}%")


def search_track(query: str) -> dict | None:
    resp = SESSION.get(
        f"{NAVIDROME_URL}/rest/search3",
        params={**_auth_params(), "query": query},
        timeout=5,
    )
    resp.raise_for_status()
    result = resp.json()["subsonic-response"].get("searchResult3", {})
    songs = result.get("song", [])
    return songs[0] if songs else None


def _play_navidrome(query: str) -> str:
    track = search_track(query)
    if not track:
        return f"I couldn't find '{query}' in your library."
    stream_url = f"{NAVIDROME_URL}/rest/stream?id={track['id']}&{_urlencode_auth()}"
    _queue_stream(stream_url)
    title = track.get("title", query)
    artist = track.get("artist", "unknown artist")
    log_line(log, "Music", f"Navidrome {title!r} by {artist}")
    return f"Playing {_speakable_title(title)} by {_speakable_title(artist, limit=32)}."


def _somafm_channels() -> list[dict]:
    resp = SESSION.get(
        SOMAFM_CHANNELS_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json().get("channels", [])


def _somafm_stream_url(channel: dict) -> str | None:
    """Prefer higher-quality HTTPS MP3/AAC playlist."""
    playlists = channel.get("playlists") or []

    def _quality_key(p: dict) -> tuple:
        fmt = str(p.get("format", "")).lower()
        raw_q = p.get("quality")
        try:
            q = int(raw_q)
        except (TypeError, ValueError):
            label = str(raw_q or "").lower()
            q = {"highest": 900, "high": 700, "low": 100}.get(label, 0)
        return (0 if fmt in ("mp3", "aac", "aacp") else 1, -q)

    ranked = sorted(playlists, key=_quality_key)
    for pl in ranked:
        url = pl.get("url")
        if url:
            return url
    return None


def _match_somafm(query: str) -> dict | None:
    q = _SOMA_HINT.sub(" ", query).strip().lower()
    q = re.sub(r"[^a-z0-9\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    channels = _somafm_channels()
    if not q:
        # "play somafm" with no channel → Groove Salad as a safe default
        for ch in channels:
            if ch.get("id") == "groovesalad":
                return ch
        return channels[0] if channels else None

    tokens = [t for t in q.split() if len(t) > 1]
    best: tuple[int, dict] | None = None
    for ch in channels:
        hay = " ".join(
            [
                str(ch.get("id", "")),
                str(ch.get("title", "")),
                str(ch.get("description", "")),
                " ".join(ch.get("genre", "").split("|")),
            ]
        ).lower()
        score = 0
        if q and q in hay:
            score += 10
        score += sum(3 for t in tokens if t in hay)
        if ch.get("id", "").lower().replace("-", " ") == q:
            score += 20
        if score and (best is None or score > best[0]):
            best = (score, ch)
    return best[1] if best and best[0] > 0 else None


def _play_somafm(query: str) -> str | None:
    try:
        channel = _match_somafm(query)
        if not channel:
            return None
        url = _somafm_stream_url(channel)
        if not url:
            return None
        _queue_stream(url)
        title = channel.get("title") or channel.get("id") or "SomaFM"
        log_line(log, "Music", f"SomaFM {title!r}")
        return f"Playing SomaFM {_speakable_title(str(title))}."
    except Exception as exc:
        log_warn(log, "Music", f"SomaFM lookup failed: {exc}")
        return None


def _radio_browser_search(query: str) -> dict | None:
    params = {
        "name": query,
        "limit": 8,
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
    }
    headers = {"User-Agent": USER_AGENT}
    last_exc: Exception | None = None
    for base in RADIO_BROWSER_MIRRORS:
        try:
            resp = SESSION.get(
                f"{base}/json/stations/search",
                params=params,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            stations = resp.json()
            if stations:
                return stations[0]
            # Fallback: search by tag/genre
            resp = SESSION.get(
                f"{base}/json/stations/bytag/{query}",
                params={"limit": 8, "hidebroken": "true", "order": "clickcount", "reverse": "true"},
                headers=headers,
                timeout=8,
            )
            if resp.ok:
                stations = resp.json()
                if stations:
                    return stations[0]
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        log_warn(log, "Music", f"Radio Browser failed: {last_exc}")
    return None


def _play_radio_browser(query: str) -> str:
    station = _radio_browser_search(query)
    if not station:
        return (
            f"I couldn't find a radio station for '{query}'. "
            "Try a genre like jazz, or say play SomaFM groove salad."
        )
    url = station.get("url_resolved") or station.get("url")
    if not url:
        return f"I found {station.get('name', query)}, but it has no stream URL."
    _queue_stream(url)
    name = station.get("name") or query
    bitrate = station.get("bitrate")
    spoken = _speakable_title(str(name))
    extra = f" ({bitrate} kbps)" if bitrate else ""
    log_line(log, "Music", f"Radio Browser {name!r}{extra}")
    return f"Playing {spoken}."


def play_track(query: str) -> str:
    """Play music for a voice query (library track or internet radio)."""
    query = (query or "").strip().rstrip("?.!")
    if not query:
        return "What would you like me to play?"

    if not _mpv_available():
        return "Music playback needs mpv. Install it with: sudo apt install mpv"

    try:
        if _navidrome_configured():
            return _play_navidrome(query)

        # Vague "play music" / lofi → reliable SomaFM defaults (ad-free).
        if _VAGUE_PLAY.match(query):
            soma = _play_somafm("groove salad")
            if soma:
                return soma
        if _LOFI_HINT.search(query):
            soma = _play_somafm("groove salad")
            if soma:
                return soma

        # Explicit SomaFM request.
        if _SOMA_HINT.search(query):
            soma = _play_somafm(query)
            if soma:
                return soma
            return (
                "I couldn't match that SomaFM channel. "
                "Try play SomaFM groove salad, or play SomaFM drone zone."
            )

        # Strong SomaFM channel name match (ad-free).
        if _somafm_strong_match(query):
            soma = _play_somafm(query)
            if soma:
                return soma

        radio = _play_radio_browser(query)
        if radio.startswith("I couldn't find"):
            # Prefer a known-good SomaFM default over a weak token match
            # (e.g. "music" → random channel like Boot Liquor).
            if _somafm_strong_match(query):
                soma = _play_somafm(query)
                if soma:
                    return soma
            soma = _play_somafm("groove salad")
            if soma:
                return soma
        return radio
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:
        log_warn(log, "Music", f"play failed: {exc}")
        return "I couldn't start playback right now."


def _somafm_strong_match(query: str) -> bool:
    """True when query clearly names a SomaFM channel (not a vague one-token hit)."""
    try:
        ch = _match_somafm(query)
    except Exception:
        return False
    if not ch:
        return False
    q = re.sub(r"[^a-z0-9\s]", " ", query.lower())
    q = re.sub(r"\s+", " ", q).strip()
    title = str(ch.get("title", "")).lower()
    cid = str(ch.get("id", "")).lower().replace("-", " ")
    return q in title or title in q or q == cid or cid in q


def is_playing() -> bool:
    global _player_process
    if _player_process is None:
        return False
    if _player_process.poll() is not None:
        _player_process = None
        return False
    return True


def stop() -> str:
    """Stop background music/radio (mpv). Safe to call when nothing is playing."""
    had_pending = has_queued_play()
    cancel_queued_play()
    if is_playing() or had_pending:
        _stop_player()
        return "Stopped."
    _stop_player()
    return "Nothing's playing."
