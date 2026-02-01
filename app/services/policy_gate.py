from __future__ import annotations

from typing import Optional, Union

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.privacy import privacy_soft_show
from app.models.user import User

Event = Union[Message, CallbackQuery]


async def require_policy(
    e: Event,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> bool:
    tg_id = None

    if isinstance(e, Message) and e.from_user:
        tg_id = e.from_user.id
    elif isinstance(e, CallbackQuery) and e.from_user:
        tg_id = e.from_user.id

    if not tg_id:
        return True

    user: User | None = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()

    policy_ok = bool(user and (getattr(user, "policy_accepted", False) or getattr(user, "consent_accepted_at", None)))

    if policy_ok:
        return True

        # ⛔ политика не принята — ПОКАЗЫВАЕМ ЕЁ СРАЗУ
    if isinstance(e, Message):
        await privacy_soft_show(e, session)
        return False  # ⬅️ ВАЖНО: блокируем дальнейшее выполнение

    elif isinstance(e, CallbackQuery):
        msg = e.message
        if isinstance(msg, Message):
            await privacy_soft_show(msg, session)
            try:
                await e.answer()
            except Exception:
                pass
            return False  # блокируем, потому что показали политику

        # сообщение недоступно — не можем показать политику, не ломаем UX
        try:
            await e.answer()
        except Exception:
            pass
        return True
