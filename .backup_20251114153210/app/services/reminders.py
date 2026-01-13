# app/services/reminders.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple

from aiogram import Bot
from croniter import croniter
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.models.reminder import Reminder
from app.models.user import User

log = logging.getLogger("reminders")

# Сколько сообщений максимум отправляем за один тик
SEND_LIMIT_PER_TICK = 100
# Небольшая пауза между отправками, чтобы бережно относиться к лимитам Telegram
SEND_SLEEP_SECONDS = 0.03


async def compute_next_run(
    cron: Optional[str],
    from_dt_utc: datetime,
    user_tz: Optional[str] = "UTC",
) -> Optional[datetime]:
    """
    Вернёт ближайшее время (UTC) по cron-выражению, заданному в ЛОКАЛЬНОМ времени пользователя.
    """
    if not cron:
        return None
    try:
        tz = ZoneInfo(user_tz or "UTC")
        base_local = from_dt_utc.astimezone(tz)
        it = croniter(cron, base_local)
        next_local = datetime.fromtimestamp(it.get_next(), tz=tz)
        return next_local.astimezone(timezone.utc)
    except Exception as e:
        log.warning("compute_next_run failed for cron=%r tz=%r: %s", cron, user_tz, e)
        return None


async def _seed_missing_next_runs(session: AsyncSession, now_utc: datetime) -> int:
    """
    Проставляет next_run всем активным периодическим напоминаниям, где он ещё пуст.
    """
    q = (
        select(Reminder.id, Reminder.cron, User.tz)
        .join(User, User.id == Reminder.user_id)
        .where(
            and_(
                Reminder.is_active.is_(True),
                Reminder.cron.is_not(None),
                Reminder.next_run.is_(None),
            )
        )
        .limit(500)
    )
    res = await session.execute(q)
    rows: Sequence[Tuple[int, str, Optional[str]]] = res.all()

    updated = 0
    for rid, cron_expr, tz in rows:
        nxt = await compute_next_run(cron_expr, now_utc, tz)
        if nxt:
            await session.execute(
                update(Reminder).where(Reminder.id == rid).values(next_run=nxt)
            )
            updated += 1

    if updated:
        await session.commit()
    return updated


async def tick_reminders(session: AsyncSession, bot: Bot, now: Optional[datetime] = None):
    """
    Основной тикер:
      1) Досеять next_run там, где его нет у периодических.
      2) Забрать due-напоминания под блокировкой (skip locked), чтобы не дублировать при нескольких воркерах.
      3) Отправить, перепланировать (cron) или деактивировать (one-shot).
    """
    now_utc = now or datetime.now(timezone.utc)

    # 1) сиддинг
    try:
        await _seed_missing_next_runs(session, now_utc)
    except Exception as e:
        log.exception("seed_missing_next_runs failed: %s", e)

    # 2) забираем due под блокировкой
    q = (
        select(Reminder, User.tg_id, User.tz)
        .join(User, User.id == Reminder.user_id)
        .where(
            and_(
                Reminder.is_active.is_(True),
                Reminder.next_run.is_not(None),
                Reminder.next_run <= now_utc,
            )
        )
        .order_by(Reminder.next_run.asc())
        .limit(SEND_LIMIT_PER_TICK)
        .with_for_update(skip_locked=True)
    )

    # начинаем транзакцию, чтобы блокировка имела смысл
    async with session.begin():
        res = await session.execute(q)
        due: Sequence[Tuple[Reminder, int, Optional[str]]] = res.all()

        if not due:
            return

        for r, chat_id, tz in due:
            try:
                await bot.send_message(chat_id, f"🔔 {r.title}")
            except Exception as send_err:
                # Не фейлим весь тик — просто логируем
                log.warning("Failed to send reminder id=%s to %s: %s", r.id, chat_id, send_err)

            # Перепланирование/деактивация
            if r.cron:
                nxt = await compute_next_run(r.cron, now_utc, tz)
                if nxt:
                    r.next_run = nxt
                else:
                    r.is_active = False
                    r.next_run = None
            else:
                # одноразовое — выключаем после отправки
                r.is_active = False
                r.next_run = None

            session.add(r)
            await asyncio.sleep(SEND_SLEEP_SECONDS)
        # Коммит произойдёт по выходу из context manager