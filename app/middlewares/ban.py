from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User


def _is_admin_id(tg_id: int) -> bool:
    # settings.bot_admin_tg_id
    try:
        if getattr(settings, "bot_admin_tg_id", None) and int(settings.bot_admin_tg_id) == int(tg_id):
            return True
    except Exception:
        pass

    # ENV ADMIN_IDS=1,2,3
    raw = os.getenv("ADMIN_IDS", "")
    if raw:
        try:
            ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
            if tg_id in ids:
                return True
        except Exception:
            pass

    return False


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        session: Optional[AsyncSession] = data.get("session")
        if session is None:
            return await handler(event, data)

        tg_id: Optional[int] = None
        if isinstance(event, Message) and event.from_user:
            tg_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_id = event.from_user.id

        if tg_id is None:
            return await handler(event, data)

        # админов не баним через гейт
        if _is_admin_id(tg_id):
            return await handler(event, data)

        row = (await session.execute(select(User.is_admin, User.is_banned).where(User.tg_id == tg_id))).one_or_none()

        if row is None:
            return await handler(event, data)

        is_admin, is_banned = row

        if bool(is_admin):
            return await handler(event, data)

        if bool(is_banned):
            if isinstance(event, Message):
                await event.answer("⛔️ Доступ ограничен.")
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("⛔️ Доступ ограничен.", show_alert=True)
                except Exception:
                    pass
            return

        return await handler(event, data)
