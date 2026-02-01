from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserSyncMiddleware(BaseMiddleware):
    """
    Sync Telegram user fields -> DB
    + puts `user` into data for handlers
    """

    # чтобы не коммитить last_seen на каждый чих
    _LAST_SEEN_THROTTLE_SEC = 60

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        session: Optional[AsyncSession] = data.get("session")
        if not session:
            return await handler(event, data)

        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
        else:
            tg_user = getattr(event, "from_user", None)

        if not tg_user:
            return await handler(event, data)

        tg_id = int(tg_user.id)

        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()

        changed = False
        if user is None:
            user = User(tg_id=tg_id)
            session.add(user)
            changed = True

        # Не затираем на None, обновляем только если пришло значение
        if tg_user.username and user.username != tg_user.username:
            user.username = tg_user.username
            changed = True

        if tg_user.first_name and user.first_name != tg_user.first_name:
            user.first_name = tg_user.first_name
            changed = True

        if tg_user.last_name and user.last_name != tg_user.last_name:
            user.last_name = tg_user.last_name
            changed = True

        lang = getattr(tg_user, "language_code", None)
        if lang and user.lang != lang:
            user.lang = lang
            changed = True

        # last_seen_at: datetime UTC + throttling
        now = _now_utc()
        if user.last_seen_at is None:
            user.last_seen_at = now
            changed = True
        else:
            try:
                dt = (now - user.last_seen_at).total_seconds()
            except Exception:
                # если вдруг в базе/модели мусор (например str), перезапишем
                user.last_seen_at = now
                changed = True
            else:
                if dt >= self._LAST_SEEN_THROTTLE_SEC:
                    user.last_seen_at = now
                    changed = True

        # важно: положим юзера в data, чтобы хэндлеры не лезли в БД снова
        data["user"] = user

        if changed:
            await session.commit()

        return await handler(event, data)
