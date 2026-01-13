
from __future__ import annotations

from typing import Any, Dict, Awaitable, Callable, Optional, Tuple

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from app.models.user import User
from app.db import async_session
from app.i18n import set_locale

SUPPORTED_LOCALES = {"ru", "uk", "en"}


def _extract_from_event(event: Any) -> Tuple[Optional[int], str]:
    tg_id: Optional[int] = None
    lc: str = ""
    if isinstance(event, Message) and event.from_user:
        tg_id = event.from_user.id
        lc = (event.from_user.language_code or "")[:2].lower()
    elif isinstance(event, CallbackQuery) and event.from_user:
        tg_id = event.from_user.id
        lc = (event.from_user.language_code or "")[:2].lower()
    else:
        fu = getattr(event, "event_from_user", None)
        if fu:
            tg_id = getattr(fu, "id", None)
            lc = (getattr(fu, "language_code", "") or "")[:2].lower()
    if lc == "ua":
        lc = "uk"
    return tg_id, lc


class LangMiddleware(BaseMiddleware):
    def __init__(self, default_locale: str = "ru") -> None:
        super().__init__()
        self.default_locale = (
            default_locale if default_locale in SUPPORTED_LOCALES else "ru"
        )

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        tg_id, tele_lc = _extract_from_event(event)

        user: Optional[User] = data.get("user")
        if not user and tg_id:
            session = data.get("session")
            if session:
                user = (
                    await session.execute(
                        select(User).where(User.tg_id == tg_id)
                    )
                ).scalar_one_or_none()
            else:
                async with async_session() as s:
                    user = (
                        await s.execute(
                            select(User).where(User.tg_id == tg_id)
                        )
                    ).scalar_one_or_none()
            if user:
                data["user"] = user

        loc = ""
        if user:
            loc = (
                getattr(user, "locale", None)
                or getattr(user, "lang", None)
                or ""
            ).lower()
        loc = loc[:2]

        locale = (
            loc
            if loc in SUPPORTED_LOCALES
            else (tele_lc if tele_lc in SUPPORTED_LOCALES else self.default_locale)
        )

        # прокидываем в handler и в i18n
        data["lang"] = locale
        set_locale(locale)

        # таймзона (если есть)
        if user and getattr(user, "tz", None):
            data.setdefault("tz", user.tz)

        return await handler(event, data)
