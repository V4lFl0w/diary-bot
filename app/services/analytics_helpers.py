from __future__ import annotations

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.analytics_v2 import log_event_v2


def _user_lang(user: Optional[User], tg_lang: Optional[str]) -> str:
    loc = (getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _is_premium_user(user: Optional[User]) -> bool:
    return bool(user and (getattr(user, "is_premium", False) or getattr(user, "has_premium", False)))


async def log_ui(
    session: AsyncSession,
    *,
    user: Optional[User],
    user_id: Optional[int],
    event: str,
    source: str,
    tg_lang: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    props = {
        "lang": _user_lang(user, tg_lang),
        "is_premium": _is_premium_user(user),
        "source": source,  # menu|button|command|auto
        **(extra or {}),
    }
    await log_event_v2(session, user_id=user_id, event=event, props=props)