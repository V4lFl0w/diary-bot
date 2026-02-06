from __future__ import annotations

import os

from app.utils.app_version import get_app_version
from app.webapp.urls import (
    webapp_base_url,
    abs_url,
    versioned_abs_url,
    WEBAPP_MUSIC_ENTRY,
)

APP_VERSION = get_app_version()


def base_url() -> str:
    return webapp_base_url()


def webapp_url(path: str) -> str:
    return abs_url(path)


WEBAPP_MUSIC_URL = versioned_abs_url(WEBAPP_MUSIC_ENTRY)


def _is_truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def get_focus_sleep() -> bool:
    return _is_truthy(os.getenv("MUSIC_FOCUS_SLEEP") or os.getenv("FOCUS_SLEEP"))
