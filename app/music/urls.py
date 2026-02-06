from __future__ import annotations

import os


def _base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL") or os.getenv("WEBAPP_BASE_URL") or "").rstrip("/")


WEBAPP_MUSIC_URL = f"{_base_url()}/webapp/music/index.html"


def get_focus_sleep() -> tuple[str, str]:
    try:
        from app.config import settings as cfg
    except Exception:
        cfg = None

    focus = (
        getattr(cfg, "music_focus_url", None)
        or os.getenv("MUSIC_FOCUS_URL")
        or "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    )
    sleep = (
        getattr(cfg, "music_sleep_url", None)
        or os.getenv("MUSIC_SLEEP_URL")
        or "https://www.youtube.com/watch?v=5qap5aO4i9A"
    )
    return str(focus), str(sleep)
