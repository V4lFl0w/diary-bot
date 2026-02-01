from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def is_policy_accepted(session: AsyncSession | None, tg_id: int) -> bool:
    if session is None:
        return False

    q = await session.execute(select(User).where(User.tg_id == tg_id))
    user = q.scalar_one_or_none()
    if not user:
        return False

    return bool(user.policy_accepted or user.consent_accepted_at)
