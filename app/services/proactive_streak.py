from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proactive_entry import ProactiveEntry


async def compute_streak(session: AsyncSession, user_id: int, today: date) -> int:
    rows = await session.execute(
        select(ProactiveEntry.local_date)
        .where(ProactiveEntry.user_id == user_id)
        .group_by(ProactiveEntry.local_date)
        .order_by(ProactiveEntry.local_date.desc())
    )

    dates = {r[0] for r in rows.all()}
    streak = 0
    cur = today

    while cur in dates:
        streak += 1
        cur -= timedelta(days=1)

    return streak
