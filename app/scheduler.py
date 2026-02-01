from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import utc

scheduler = AsyncIOScheduler(timezone=utc)


def ensure_started():
    # Если луп ещё не запущен (импорт времени) — выходим молча.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    if not scheduler.running:
        scheduler.start()
