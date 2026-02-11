from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging

import subprocess

def _get_commit_sha():
    for k in ("GIT_SHA", "GIT_COMMIT", "SOURCE_VERSION", "HEROKU_SLUG_COMMIT"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v[:12]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"

STARTUP_COMMIT_SHA = _get_commit_sha()

import os
import pkgutil
import re
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from sqlalchemy import text

import app.hooks  # noqa: F401
import app.models as _models_pkg
from app.bot import bot
from app.config import settings
from app.db import Base, engine
from app.db import async_session as SessionLocal
from app.handlers import export
from app.handlers import premium_bridge_v2
from app.handlers import premium_webapp
from app.handlers.proactive_checkin import router as proactive_checkin_router
from app.handlers import media_nav  # или прямой импорт пути, как у тебя принято
from app.logging_setup import setup_logging
from app.middlewares.ban import BanMiddleware
from app.middlewares.last_seen import LastSeenMiddleware
from app.middlewares.policy_gate import PolicyGateMiddleware
from app.middlewares.rate_limit import RateLimitMiddleware
from app.middlewares.trace import TraceUpdateMiddleware

# ---------- роутеры ----------

from app.features import router as features_router
from app.handlers import (
    data_privacy,
    journal,
    language,
    motivation,
    payments_stars,  # ✅ Stars
    premium,
    premium_reset,
    privacy,
    proactive,
    refund,
    refund_ui,
    reminders,
    report,
    start,
)


async def log_db_info() -> None:
    try:
        url = str(engine.url)
        url = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
        logging.getLogger("root").info("DB_URL=%s", url)

        async with engine.begin() as conn:
            r = await conn.execute(text("select version()"))
            logging.getLogger("root").info("DB_VERSION=%s", r.scalar())
    except Exception as e:
        logging.getLogger("root").exception("DB_INFO_FAILED: %r", e)


# ---------- сервисы ----------

try:
    from app.services.reminders import tick_reminders
except Exception:

    async def tick_reminders(*_a, **_kw) -> None:
        return None


# ✅ renewal reminders (подписка скоро закончится)
try:
    from app.jobs.renewal_reminders import run_renewal_reminders
except Exception:

    async def run_renewal_reminders(*_a, **_kw) -> None:
        return None


# ✅ proactive morning/evening
try:
    from app.services.proactive_loop import proactive_loop
except Exception:

    async def proactive_loop(*_a, **_kw) -> None:
        return None


try:
    from app.scheduler import ensure_started  # type: ignore
except Exception:
    ensure_started = None  # type: ignore

# меню (открывает подменю: Журнал/Медиа/Настройки/Премиум и т.д.)
try:
    from app.handlers.menus import router as menus_router  # type: ignore
except Exception:
    menus_router = None  # type: ignore

# assistant
try:
    from app.handlers.assistant import router as assistant_router  # type: ignore
except Exception:
    assistant_router = None  # type: ignore

# медитация / музыка (как отдельные модули)
try:
    from app.handlers.meditation import router as meditation_router  # type: ignore
except Exception:
    meditation_router = None  # type: ignore

try:
    from app.handlers.music import router as music_router  # type: ignore
except Exception:
    music_router = None  # type: ignore

# admin опционально
try:
    from app.handlers import admin  # type: ignore
except Exception:
    admin = None  # type: ignore


# ---------- команды ----------
logger = logging.getLogger(__name__)


def _has_calories_feature() -> bool:
    try:
        importlib.import_module("app.features.calories")
        return True
    except Exception:
        return False


def _build_commands(include_admin: bool, include_calories: bool) -> Dict[str, list[BotCommand]]:
    ru = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="journal", description="Сделать запись"),
        BotCommand(command="today", description="Записи за 24 часа"),
        BotCommand(command="history", description="История записей"),
        BotCommand(command="week", description="Итоги недели"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="remind", description="Создать напоминание"),
        BotCommand(command="premium", description="Премиум-статус"),
        BotCommand(command="privacy", description="Политика и конфиденциальность"),
    ]
    uk = [
        BotCommand(command="start", description="Почати"),
        BotCommand(command="journal", description="Зробити запис"),
        BotCommand(command="today", description="Записи за 24 години"),
        BotCommand(command="history", description="Історія записів"),
        BotCommand(command="week", description="Підсумки тижня"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="remind", description="Створити нагадування"),
        BotCommand(command="premium", description="Преміум-статус"),
        BotCommand(command="privacy", description="Політика і конфіденційність"),
    ]
    en = [
        BotCommand(command="start", description="Start"),
        BotCommand(command="journal", description="New journal entry"),
        BotCommand(command="today", description="Entries from last 24h"),
        BotCommand(command="history", description="Recent entries"),
        BotCommand(command="week", description="Weekly summary"),
        BotCommand(command="stats", description="Stats"),
        BotCommand(command="remind", description="Create reminder"),
        BotCommand(command="premium", description="Premium"),
        BotCommand(command="privacy", description="Policy & privacy"),
    ]

    if include_calories:
        ru.insert(8, BotCommand(command="calories", description="Калории"))
        uk.insert(8, BotCommand(command="calories", description="Калорії"))
        en.insert(8, BotCommand(command="calories", description="Calories"))

    if include_admin:
        ru.append(BotCommand(command="admin", description="Админ-панель"))
        uk.append(BotCommand(command="admin", description="Адмін-панель"))
        en.append(BotCommand(command="admin", description="Admin panel"))

    return {"ru": ru, "uk": uk, "en": en}


# ---------- миддлвари ----------

try:
    from app.middlewares.lang import LangMiddleware as LangMiddlewareImpl
except Exception:
    LangMiddlewareImpl = None  # type: ignore


class _FallbackLangMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        data.setdefault("lang", "ru")
        return await handler(event, data)


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        async with SessionLocal() as session:
            data["session"] = session
            try:
                return await handler(event, data)
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    pass
                raise


# ---------- утилиты ----------


async def _ensure_db() -> None:
    for _, name, _ in pkgutil.iter_modules(_models_pkg.__path__, _models_pkg.__name__ + "."):
        importlib.import_module(name)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _set_commands(include_admin: bool, include_calories: bool) -> None:
    cmds = _build_commands(include_admin=include_admin, include_calories=include_calories)
    with contextlib.suppress(Exception):
        await bot.set_my_commands(cmds["ru"], language_code="ru")
        await bot.set_my_commands(cmds["uk"], language_code="uk")
        await bot.set_my_commands(cmds["en"], language_code="en")


async def _reminders_loop() -> None:
    tick = max(
        1,
        int(os.getenv("REMINDER_TICK_SEC", str(getattr(settings, "reminder_tick_sec", 5)))),
    )

    try:
        while True:
            try:
                async with SessionLocal() as session:
                    await tick_reminders(session, bot)
            except Exception:
                logging.exception("reminders_loop error")
            await asyncio.sleep(tick)
    except asyncio.CancelledError:
        return


# ✅ loop для уведомлений о продлении подписки
async def _renewal_reminders_loop() -> None:
    # чтобы не стрелять сразу при старте
    await asyncio.sleep(int(os.getenv("RENEWAL_START_DELAY_SEC", "60")))

    every_sec = max(
        60,
        int(os.getenv("RENEWAL_TICK_SEC", str(6 * 60 * 60))),  # 6h default
    )

    try:
        while True:
            try:
                async with SessionLocal() as session:
                    await run_renewal_reminders(bot, session)
            except Exception:
                logging.exception("renewal_reminders_loop error")
            await asyncio.sleep(every_sec)
    except asyncio.CancelledError:
        return


async def _safe_start_scheduler() -> None:
    if not ensure_started:
        return
    try:
        res = ensure_started()
        if inspect.isawaitable(res):
            asyncio.create_task(res, name="scheduler_ensure_started")
    except Exception:
        logging.exception("ensure_started error")


# ---------- сборка dispatcher ----------


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(TraceUpdateMiddleware(logger))

    # 1) Сначала сессия БД (чтобы policy_gate мог читать acceptance)
    dp.update.outer_middleware(DBSessionMiddleware())

    from app.middlewares.log_context import LogContextMiddleware

    dp.update.outer_middleware(LogContextMiddleware())

    # ✅ ВОТ ТУТ
    from app.middlewares.user_sync import UserSyncMiddleware

    dp.update.outer_middleware(UserSyncMiddleware())

    # 2) Язык/контекст (если надо)
    dp.update.outer_middleware(LangMiddlewareImpl() if LangMiddlewareImpl is not None else _FallbackLangMiddleware())

    dp.update.outer_middleware(LastSeenMiddleware(min_update_seconds=60))

    # 3) Политика — железно глобально
    dp.message.outer_middleware(PolicyGateMiddleware())
    dp.callback_query.outer_middleware(PolicyGateMiddleware())

    dp.message.outer_middleware(BanMiddleware())
    dp.callback_query.outer_middleware(BanMiddleware())

    # 4) Бан/секьюрность

    dp.message.middleware(RateLimitMiddleware(max_events=25, per_seconds=10))
    dp.callback_query.middleware(RateLimitMiddleware(max_events=40, per_seconds=10))

    # ----- роутеры -----
    dp.include_router(premium.router)
    dp.include_router(premium_bridge_v2.router)
    dp.include_router(premium_reset.router)
    dp.include_router(refund_ui.router)
    dp.include_router(payments_stars.router)
    dp.include_router(premium_webapp.router)
    dp.include_router(refund.router)

    if admin and getattr(admin, "router", None):
        dp.include_router(admin.router)

    dp.include_router(privacy.router)
    dp.include_router(data_privacy.router)
    dp.include_router(start.router)

    if assistant_router is not None:
        dp.include_router(assistant_router)

    if menus_router is not None:
        dp.include_router(menus_router)

    dp.include_router(journal.router)
    dp.include_router(report.router)

    dp.include_router(proactive.router)

    dp.include_router(proactive_checkin_router)

    dp.include_router(motivation.router)
    dp.include_router(media_nav.router)

    if meditation_router is not None:
        dp.include_router(meditation_router)
    if music_router is not None:
        dp.include_router(music_router)

    dp.include_router(features_router)
    dp.include_router(reminders.router)
    dp.include_router(export.router)
    dp.include_router(language.router)

    return dp


# ---------- main ----------


async def main() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    setup_logging()
    logging.getLogger().setLevel(level)

    logging.getLogger("aiogram.event").setLevel(logging.DEBUG)
    logging.getLogger("aiogram.dispatcher").setLevel(logging.DEBUG)
    logging.getLogger("aiogram.middlewares").setLevel(logging.DEBUG)

    for name in ("aiosqlite", "apscheduler", "sqlalchemy.engine"):
        with contextlib.suppress(Exception):
            logging.getLogger(name).setLevel(logging.WARNING)

    await _ensure_db()
    with contextlib.suppress(Exception):
        await _safe_start_scheduler()

    dp = build_dispatcher()

    include_admin = bool(admin and getattr(admin, "router", None))
    include_calories = _has_calories_feature()
    await _set_commands(include_admin=include_admin, include_calories=include_calories)

    with contextlib.suppress(Exception):
        await bot.delete_webhook(drop_pending_updates=True)

    with contextlib.suppress(Exception):
        me = await bot.get_me()
        logging.info("Connected as @%s id: %s", me.username, me.id)

    reminders_task = asyncio.create_task(_reminders_loop(), name="reminders_loop")
    renewal_task = asyncio.create_task(_renewal_reminders_loop(), name="renewal_reminders_loop")
    proactive_task = asyncio.create_task(proactive_loop(bot, SessionLocal), name="proactive_loop")

    await log_db_info()
    logging.info("✅ Bot is up. Starting polling… | COMMIT={{STARTUP_COMMIT_SHA}}")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        reminders_task.cancel()
        renewal_task.cancel()
        proactive_task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await reminders_task
        with contextlib.suppress(asyncio.CancelledError):
            await renewal_task
        with contextlib.suppress(asyncio.CancelledError):
            await proactive_task

        with contextlib.suppress(Exception):
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("👋 Bot stopped by user (Ctrl+C)")
    except SystemExit:
        pass
