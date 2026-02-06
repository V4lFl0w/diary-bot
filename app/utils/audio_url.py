from __future__ import annotations
import re

_AUDIO_EXT = re.compile(r"\.(mp3|m4a|ogg|aac|wav)(\?|$)", re.I)


def is_http_url(u: str) -> bool:
    u = (u or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def is_audio_url(u: str) -> bool:
    return is_http_url(u) and bool(_AUDIO_EXT.search(u))
