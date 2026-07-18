"""
Hobby YouTube audio via yt-dlp (search → YouTube watch URL → mpv).

Self-contained: delete this file and remove the small youtube_music hooks in
music_client.py / config.py / .env to fully remove the feature.

Requires: yt-dlp on PATH (uv add yt-dlp) and mpv.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ancilla.config import MUSIC_YOUTUBE_ENABLED
from ancilla.log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.youtube_music")

_YOUTUBE_HINT = re.compile(
    r"\b(?:on\s+)?(?:you\s*tube|youtube|yt)\b",
    re.IGNORECASE,
)
_STRIP_HINT = re.compile(
    r"\b(?:on\s+)?(?:you\s*tube|youtube|yt)\b",
    re.IGNORECASE,
)
_YOUTUBE_URL = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YoutubeClip:
    """Resolved clip for music_client to queue into mpv."""

    watch_url: str
    title: str

    @property
    def spoken(self) -> str:
        # Keep this short — long YouTube titles dominate Piper TTS time.
        title = self.title.strip() or "that"
        if len(title) > 40:
            title = title[:39].rstrip(" -|,;/") + "…"
        return f"Playing {title}."


def enabled() -> bool:
    return bool(MUSIC_YOUTUBE_ENABLED) and _yt_dlp_bin() is not None


def wants_youtube(query: str) -> bool:
    """True when the user explicitly asked for YouTube."""
    return bool(_YOUTUBE_HINT.search(query or ""))


def looks_like_youtube_url(query: str) -> bool:
    return bool(_YOUTUBE_URL.search(query or ""))


def yt_dlp_path() -> str | None:
    """Prefer venv yt-dlp (kept current) over a stale distro package."""
    venv_bin = Path(sys.prefix) / "bin" / "yt-dlp"
    if venv_bin.is_file():
        return str(venv_bin)
    return shutil.which("yt-dlp")


def _yt_dlp_bin() -> str | None:
    return yt_dlp_path()


def _clean_query(query: str) -> str:
    q = _STRIP_HINT.sub(" ", query or "")
    q = re.sub(r"\s+", " ", q).strip(" .,!?:;")
    return q


def resolve(query: str) -> YoutubeClip | None:
    """
    Resolve a voice query or YouTube URL to a watch URL + title.
    Returns None on failure (caller should fall through to other backends).
    """
    query = (query or "").strip()
    if not query:
        return None
    ytdlp = _yt_dlp_bin()
    if not ytdlp:
        log_warn(log, "YouTube", "yt-dlp not installed")
        return None

    if looks_like_youtube_url(query):
        target = query.strip()
    else:
        cleaned = _clean_query(query)
        if not cleaned:
            cleaned = query
        target = f"ytsearch1:{cleaned}"

    # ytsearch: --flat-playlist skips a full watch-page fetch (~2x faster).
    cmd = [ytdlp, "--no-playlist", "--skip-download", "-j"]
    if target.startswith("ytsearch"):
        cmd.append("--flat-playlist")
    cmd.append(target)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_warn(log, "YouTube", f"yt-dlp failed: {exc}")
        return None

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        log_warn(log, "YouTube", err[-1] if err else f"yt-dlp exit {proc.returncode}")
        return None

    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        return None
    try:
        data = json.loads(lines[0])
    except json.JSONDecodeError:
        log_warn(log, "YouTube", "could not parse yt-dlp JSON")
        return None

    vid = data.get("id")
    title = (data.get("title") or data.get("fulltitle") or query).strip()
    webpage = (
        data.get("webpage_url")
        or data.get("original_url")
        or data.get("url")
        or ""
    )
    if "youtube.com" in webpage or "youtu.be" in webpage:
        watch_url = webpage
    elif vid:
        watch_url = f"https://www.youtube.com/watch?v={vid}"
    else:
        return None

    log_line(log, "YouTube", f"{title!r}")
    return YoutubeClip(watch_url=watch_url, title=title)


def play_message(query: str) -> tuple[str, str] | str | None:
    """
    Resolve query for playback.

    Returns:
      (watch_url, spoken_message) on success
      error string if YouTube was explicitly requested but failed
      None to fall through to other music backends
    """
    if not enabled():
        return None

    explicit = wants_youtube(query) or looks_like_youtube_url(query)
    clip = resolve(query)
    if clip is None:
        if explicit:
            return "I couldn't find that on YouTube right now."
        return None
    return clip.watch_url, clip.spoken
