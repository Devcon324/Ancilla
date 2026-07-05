"""
Minimal Subsonic-API client for Navidrome. Playback itself happens via a
local player (mpv is simplest) pointed at the streaming URL — the Jetson
doesn't need to know anything about audio decoding, just fetch-and-play.

pip install requests
Needs mpv installed: sudo apt install mpv
"""
import hashlib
import random
import string
import subprocess

from jetson_assistant.config import NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS
from jetson_assistant.services.http import SESSION

_player_process = None


def _auth_params():
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


def search_track(query: str) -> dict | None:
    resp = SESSION.get(
        f"{NAVIDROME_URL}/rest/search3", params={**_auth_params(), "query": query}, timeout=5
    )
    resp.raise_for_status()
    result = resp.json()["subsonic-response"].get("searchResult3", {})
    songs = result.get("song", [])
    return songs[0] if songs else None


def play_track(query: str) -> str:
    global _player_process
    track = search_track(query)
    if not track:
        return f"I couldn't find '{query}' in your library."

    stream_url = f"{NAVIDROME_URL}/rest/stream?id={track['id']}&{_urlencode_auth()}"

    if _player_process:
        _player_process.terminate()
    _player_process = subprocess.Popen(["mpv", "--no-video", stream_url])

    return f"Playing {track['title']} by {track.get('artist', 'unknown artist')}."


def stop() -> str:
    global _player_process
    if _player_process:
        _player_process.terminate()
        _player_process = None
        return "Stopped."
    return "Nothing's playing."


def _urlencode_auth() -> str:
    from urllib.parse import urlencode
    return urlencode(_auth_params())
