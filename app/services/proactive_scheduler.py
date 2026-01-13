from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User


def _parse_hhmm(v: Optional[str]) -> Optional[time]:
    if not v:
        return None
    try:
        parts = v.strip().split(":")
        hh = int(parts[0])
        mm = int(parts[1])
        return time(hh, mm)
    except Exception:
        return None


def _user_tz(user: User):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(getattr(user, "tz", None) or "Europe/Kyiv")
    except Exception:
        return timezone.utc


def _same_local_day(last_sent: datetime, now_utc: datetime, tz) -> bool:
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return last_sent.astimezone(tz).date() == now_utc.astimezone(tz).date()


async def proactive_loop(bot, Session: async_sessionmaker[AsyncSession]):
    while True:
        try:
            async with Session() as s:
                users = (await s.execute(select(User))).scalars().all()
                now_utc = datetime.now(timezone.utc)  # AWARE UTC

                for u in users:
                    tz = _user_tz(u)
                    now_local = now_utc.astimezone(tz)

                    # ----- MORNING -----
                    if getattr(u, "morning_auto", False):
                        t = getattr(u, "morning_time", None)
                        if isinstance(t, str):
                            t = _parse_hhmm(t)
                        if t:
                            due = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                            last = getattr(u, "morning_last_sent_at", None)

                            should_send = now_local >= due
                            if last:
                                should_send = should_send and not _same_local_day(last, now_utc, tz)

                            if should_send:
                                await bot.send_message(
                                    u.tg_id,
                                    "☀️ Утренний briefing:\n• 1 приоритет\n• 3 шага\n• 1 маленький старт (2 минуты)"
                                )
                                u.morning_last_sent_at = now_utc
                                await s.commit()
                                print("✅ morning sent, morning_last_sent_at set:", u.morning_last_sent_at)

                    # ----- EVENING -----
                    if getattr(u, "evening_auto", False):
                        t = getattr(u, "evening_time", None)
                        if isinstance(t, str):
                            t = _parse_hhmm(t)
                        if t:
                            due = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                            last = getattr(u, "evening_last_sent_at", None)

                            should_send = now_local >= due
                            if last:
                                should_send = should_send and not _same_local_day(last, now_utc, tz)

                            if should_send:
                                await bot.send_message(
                                    u.tg_id,
                                    "🌙 Вечерний чек-ин:\n1) Как день (1 фраза)\n2) 1 победа\n3) 1 урок"
                                )
                                u.evening_last_sent_at = now_utc
                                await s.commit()

        except Exception as e:
            import logging
            logging.exception("proactive_loop error: %r", e)

        await asyncio.sleep(45)
