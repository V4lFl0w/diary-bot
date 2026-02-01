from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from sqlalchemy import select

from app.models.user import User


class LastSeenMiddleware(BaseMiddleware):
    """
    Обновляет users.last_seen_at.
    Чтобы не долбить БД — пишем не чаще, чем раз в N секунд на юзера.
    """

    def __init__(self, min_update_seconds: int = 60):
        self.min_update_seconds = max(5, int(min_update_seconds))

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        session = data.get("session")
        from_user = getattr(event, "from_user", None)
        if session is None or from_user is None:
            return await handler(event, data)

        tg_id = getattr(from_user, "id", None)
        if not tg_id:
            return await handler(event, data)

        try:
            res = await session.execute(select(User).where(User.tg_id == tg_id))
            user: Optional[User] = res.scalar_one_or_none()
            if user:
                now = datetime.now(timezone.utc)
                last = getattr(user, "last_seen_at", None)
                need = True
                if last:
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    need = (now - last) > timedelta(seconds=self.min_update_seconds)

                if need:
                    user.last_seen_at = now
                    await session.flush()
        except Exception:
            # не ломаем апдейт, если БД/коммит упал
            try:
                await session.rollback()
            except Exception:
                pass

        return await handler(event, data)
