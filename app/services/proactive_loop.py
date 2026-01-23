from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from typing import Optional, Iterable

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User

log = logging.getLogger(__name__)


def _parse_hhmm(v: Optional[str]) -> Optional[time]:
    if not v:
        return None
    try:
        hh, mm = v.strip().split(":", 1)
        return time(int(hh), int(mm))
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


def _briefing_text() -> str:
    # ХУК + понятность + “маленький старт”
    return (
        "☀️ *Утренний импульс*\n"
        "Чтобы день не съел тебя.\n\n"
        "1) 🎯 *1 приоритет* (что даст максимум)\n"
        "2) ✅ *3 шага* (самые короткие действия)\n"
        "3) ⚡️ *Старт на 2 минуты* — начни прямо сейчас\n\n"
        "Ответь одной строкой: *какой приоритет?*"
    )


def _checkin_text() -> str:
    return (
        "🌙 *Вечерний чек-ин*\n"
        "Закрываем день без хаоса.\n\n"
        "1) 🧠 Как прошёл день (1 фраза)\n"
        "2) 🏆 1 победа\n"
        "3) 🧩 1 урок\n\n"
        "Ответь: *победа / урок*"
    )


async def proactive_loop(bot, Session: async_sessionmaker[AsyncSession]):
    """
    Цикл безопасный:
    - берём только тех, у кого включено утро/вечер
    - не коммитим внутри каждой отправки (один коммит на проход)
    - ошибок не боимся
    """
    while True:
        try:
            async with Session() as s:
                now_utc = datetime.now(timezone.utc)

                users = (
                    await s.execute(
                        select(User).where(
                            or_(
                                User.morning_auto.is_(True),
                                User.evening_auto.is_(True),
                            )
                        )
                    )
                ).scalars().all()

                changed = False

                for u in users:
                    tg_id = getattr(u, "tg_id", None)
                    if not tg_id:
                        continue

                    tz = _user_tz(u)
                    now_local = now_utc.astimezone(tz)

                    # ----- MORNING -----
                    if getattr(u, "morning_auto", False):
                        t = getattr(u, "morning_time", None)
                        if isinstance(t, str):
                            t = _parse_hhmm(t)
                        if isinstance(t, time):
                            due = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                            last = getattr(u, "morning_last_sent_at", None)

                            should_send = now_local >= due
                            if last:
                                should_send = should_send and not _same_local_day(last, now_utc, tz)

                            if should_send:
                                try:
                                    await bot.send_message(tg_id, _briefing_text(), parse_mode="Markdown")
                                    u.morning_last_sent_at = now_utc
                                    changed = True
                                except Exception:
                                    log.exception("proactive morning send failed (tg_id=%s)", tg_id)

                    # ----- EVENING -----
                    if getattr(u, "evening_auto", False):
                        t = getattr(u, "evening_time", None)
                        if isinstance(t, str):
                            t = _parse_hhmm(t)
                        if isinstance(t, time):
                            due = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                            last = getattr(u, "evening_last_sent_at", None)

                            should_send = now_local >= due
                            if last:
                                should_send = should_send and not _same_local_day(last, now_utc, tz)

                            if should_send:
                                try:
                                    await bot.send_message(tg_id, _checkin_text(), parse_mode="Markdown")
                                    u.evening_last_sent_at = now_utc
                                    changed = True
                                except Exception:
                                    log.exception("proactive evening send failed (tg_id=%s)", tg_id)

                if changed:
                    await s.commit()

        except Exception:
            log.exception("proactive_loop error")

        await asyncio.sleep(45)
