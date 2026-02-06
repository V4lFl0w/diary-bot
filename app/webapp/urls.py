from __future__ import annotations

import os

from app.utils.app_version import get_app_version


def webapp_base_url() -> str:
    """Return public base URL (no trailing slash)."""
    return (
        (os.getenv("PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL") or os.getenv("WEBAPP_BASE_URL") or "")
        .strip()
        .rstrip("/")
    )


def with_version(path: str, *, v: str | None = None) -> str:
    """Add ?v=... (or &v=...) to ANY path."""
    vv = (v or get_app_version() or "").strip()
    if not vv:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}v={vv}"


def abs_url(path: str) -> str:
    """Convert a path to absolute URL if base is configured."""
    base = webapp_base_url()
    if not base:
        return path
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def versioned_url(path: str, *, v: str | None = None) -> str:
    """Path + version (still relative)."""
    return with_version(path, v=v)


def versioned_abs_url(path: str, *, v: str | None = None) -> str:
    """Absolute URL + version (recommended for Telegram WebApp buttons)."""
    return abs_url(with_version(path, v=v))


# Canonical entrypoints (paths)
WEBAPP_PREMIUM_ENTRY = "/static/mini/premium/premium.html"
WEBAPP_MEDITATION_ENTRY = "/static/mini/meditation/index.html"
WEBAPP_MUSIC_ENTRY = "/webapp/music/index.html"
