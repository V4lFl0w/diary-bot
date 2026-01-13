from __future__ import annotations
from typing import Optional

from app.models.user import User
from app.i18n import detect_lang

SUPPORTED = {"ru", "uk", "en"}

def safe_loc(user: Optional[User], tg_lang: Optional[str] = None) -> str:
    raw = (
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or tg_lang
        or "ru"
    )
    s = str(raw).lower().strip()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    # fallback to telegram detect
    return detect_lang(tg_lang)