from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select


def _local_today(tz: str | None) -> date:
    try:
        z = ZoneInfo(tz or "Europe/Uzhgorod")
    except Exception:
        z = ZoneInfo("Europe/Uzhgorod")
    return datetime.now(z).date()


from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proactive_entry import ProactiveEntry
from app.services.proactive_streak import compute_streak


async def log_proactive_entry(
    session: AsyncSession, user, kind: str, payload: dict[str, Any]
) -> None:
    today = _local_today(getattr(user, "tz", None))

    # one entry per (user, kind, day) — update if already exists
    existing = (
        await session.execute(
            select(ProactiveEntry)
            .where(
                ProactiveEntry.user_id == user.id,
                ProactiveEntry.kind == kind,
                ProactiveEntry.local_date == today,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing:
        existing.payload = payload
    else:
        session.add(
            ProactiveEntry(
                user_id=user.id, kind=kind, local_date=today, payload=payload
            )
        )

    # recompute streak for this local day
    user.proactive_streak = await compute_streak(session, user.id, today)

    await session.commit()
