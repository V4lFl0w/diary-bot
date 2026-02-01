# app/main.py
from __future__ import annotations
import asyncio
import logging
import os
import importlib
import pkgutil
import contextlib
from typing import Any, Dict, Awaitable, Callable

from aiogram import Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot import bot
from app.db import async_session as SessionLocal, engine, Base
from app.config import settings
import app.models as _models_pkg

try:
    from app.services.reminders import tick_reminders
except Exception:

    async def tick_reminders(*_a, **_kw):
        return None


try:
    from app.scheduler import ensure_started  # type: ignore
except Exception:
    ensure_started = None  # type: ignore

# — роутеры —
from app.handlers import start, language, privacy, journal, reminders, report, premium
from app.features import router as features_router

RU_COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="journal", description="Сделать запись"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="remind", description="Создать напоминание"),
    BotCommand(command="premium", description="Премиум-статус"),
]
UK_COMMANDS = [
    BotCommand(command="start", description="Почати"),
    BotCommand(command="journal", description="Зробити запис"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="remind", description="Створити нагадування"),
    BotCommand(command="premium", description="Преміум-статус"),
]
EN_COMMANDS = [
    BotCommand(command="start", description="Start"),
    BotCommand(command="journal", description="New journal entry"),
    BotCommand(command="stats", description="Stats"),
    BotCommand(command="remind", description="Create reminder"),
    BotCommand(command="premium", description="Premium"),
]

try:
    from app.middlewares.lang import LangMiddleware
except Exception:

    class LangMiddleware(BaseMiddleware):
        async def __call__(self, handler, event, data):
            data.setdefault("lang", "ru")
            return await handler(event, data)


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]], event, data: Dict[str, Any]
    ) -> Any:
        async with SessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


async def _ensure_db() -> None:
    for _, name, _ in pkgutil.iter_modules(_models_pkg.__path__, _models_pkg.__name__ + "."):
        importlib.import_module(name)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _set_commands() -> None:
    await bot.set_my_commands(RU_COMMANDS, language_code="ru")
    await bot.set_my_commands(UK_COMMANDS, language_code="uk")
    await bot.set_my_commands(EN_COMMANDS, language_code="en")


async def _reminders_loop() -> None:
    tick = max(1, int(os.getenv("REMINDER_TICK_SEC", str(getattr(settings, "reminder_tick_sec", 5)))))
    while True:
        try:
            async with SessionLocal() as session:
                await tick_reminders(session, bot)
        except Exception:
            logging.exception("reminders_loop error")
        await asyncio.sleep(tick)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DBSessionMiddleware())
    dp.update.outer_middleware(LangMiddleware())

    # порядок критичен: широкие «слушатели» — в самом конце
    dp.include_router(premium.router)
    dp.include_router(privacy.router)
    dp.include_router(language.router)
    dp.include_router(start.router)
    dp.include_router(journal.router)
    dp.include_router(report.router)
    dp.include_router(features_router)
    dp.include_router(reminders.router)  # самый последний
    return dp


async def main() -> None:
    logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    logging.info("Bootstrapping…")

    await _ensure_db()
    try:
        if ensure_started:
            ensure_started()
    except Exception:
        logging.exception("ensure_started failed")

    dp = build_dispatcher()
    await _set_commands()

    reminders_task = asyncio.create_task(_reminders_loop(), name="reminders_loop")
    logging.info("✅ Bot is up. Starting polling…")
    try:
        await dp.start_polling(bot)
    finally:
        reminders_task.cancel()
        with contextlib.suppress(Exception):
            await reminders_task


if __name__ == "__main__":
    asyncio.run(main())
