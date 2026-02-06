from __future__ import annotations

import os
from app.utils.app_version import get_app_version


def _base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL") or os.getenv("WEBAPP_BASE_URL") or "").rstrip("/")


def _version() -> str:
    # ставь это на деплое (например, git sha), либо оставь пустым
    return (os.getenv("WEBAPP_VERSION") or os.getenv("GIT_SHA") or "").strip()


def webapp_url(path: str) -> str:
    base = _base_url()
    p = "/" + (path or "").lstrip("/")
    url = f"{base}{p}" if base else p

    v = _version()
    if not v:
        return url

    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={v}"


APP_VERSION = get_app_version()
WEBAPP_MUSIC_URL = webapp_url(f"/webapp/music/index.html?v={APP_VERSION}")


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
