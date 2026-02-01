# app/services/reminders.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from croniter import croniter  # pip install croniter
except Exception:  # fallback-стаб, чтобы не падать
    croniter = None  # type: ignore

# Модели
from app.models.reminder import Reminder
from app.models.user import User

# ---------- i18n мини-набор ----------
_TEXT = {
    "ding": {
        # Будет: ⏰ Напоминание (2025-12-03 12:30): вода
        "ru": "⏰ Напоминание ({time}): {title}",
        "uk": "⏰ Нагадування ({time}): {title}",
        "en": "⏰ Reminder ({time}): {title}",
    }
}


def _pick_lang(u: User | None) -> str:
    """Язык берём из locale/lang, приводим ua → uk, остальное в ru по дефолту."""
    cand = (getattr(u, "locale", None) or getattr(u, "lang", None) or "ru").lower()
    cand = cand.split("-")[0]  # en-US -> en
    if cand in {"ua", "uk"}:
        return "uk"
    if cand == "en":
        return "en"
    return "ru"


def _fmt_local(dt_utc: datetime, tz_name: str | None) -> str:
    """Форматируем UTC-время в строку в локальной TZ."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    try:
        return dt_utc.astimezone(ZoneInfo(tz_name or "Europe/Kyiv")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_utc.astimezone(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M")


# ---------- cron → next UTC ----------
def compute_next_run(cron: str, now_utc: datetime, tz_name: str) -> datetime | None:
    """
    cron: строка crontab, напр. "0 9 * * 1-5"
    now_utc: текущий момент (UTC, aware или naive)
    tz_name: имя TZ пользователя (Europe/Kyiv и т.п.)

    Возвращает следующий запуск в UTC (aware) либо None.
    """
    if croniter is None:
        return None
    try:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        base_local = now_utc.astimezone(ZoneInfo(tz_name or "UTC"))
        it = croniter(cron, base_local)
        nxt_local = it.get_next(datetime)  # aware в локальной TZ
        return nxt_local.astimezone(timezone.utc)
    except Exception:
        return None


# ---------- ядро тика ----------
async def _tick_with_session(session: AsyncSession, bot) -> None:
    """
    Обрабатывает просроченные/должные к отправке напоминания:
    - берём due в UTC
    - шлём пользователю
    - если периодическое — считаем next_run; если одноразовое — выключаем
    """
    now = now_utc()

    # Подтягиваем только нужные поля пользователя (tg_id/tz/locale/lang),
    # чтобы НЕ тянуть весь User (и не падать на отсутствующих колонках типа username)
    q = (
        select(
            Reminder,
            User.tg_id,
            User.tz,
            User.locale,
            User.lang,
        )
        .join(User, Reminder.user_id == User.id)
        .where(
            and_(
                Reminder.is_active.is_(True),
                Reminder.next_run.is_not(None),
                Reminder.next_run <= now,
            )
        )
        .order_by(Reminder.next_run.asc())
        .limit(100)
    )
    rows = (await session.execute(q)).all()
    for r, tg_id, tz, locale, lang in rows:
        # Без tg_id смысла слать нет — деактивируем
        if not tg_id:
            r.is_active = False
            session.add(r)
            continue

        loc = (locale or lang or "ru").lower()
        if loc.startswith(("ua", "uk")):
            lang = "uk"
        elif loc.startswith("en"):
            lang = "en"
        else:
            lang = "ru"
        local_time = _fmt_local(r.next_run or now, tz)
        template = _TEXT["ding"].get(lang, _TEXT["ding"]["ru"])
        txt = template.format(title=r.title or "reminder", time=local_time)

        try:
            await bot.send_message(tg_id, txt)
        except Exception:
            # Например, 403 (bot was blocked). Чтобы не забивать очередь — глушим напоминание.
            r.is_active = False
            session.add(r)
            continue

        # Переставляем next_run
        if r.cron:
            nxt = compute_next_run(
                r.cron,
                now + timedelta(seconds=1),
                tz or "Europe/Kyiv",
            )
            if nxt:
                r.next_run = nxt
            else:
                r.is_active = False  # не смогли посчитать — выключим
        else:
            # одноразовое
            r.is_active = False
            r.next_run = None

        session.add(r)

    if rows:
        await session.commit()


# ---------- внешний API с гибкой сигнатурой ----------
async def tick_reminders(*args):
    """
    Гибкий адаптер, чтобы не ломать main.py.

    Поддерживаем:
      - await tick_reminders(session, bot)
      - await tick_reminders(bot, async_sessionmaker)
      - await tick_reminders(async_sessionmaker, bot)
    """
    if len(args) != 2:
        raise TypeError("tick_reminders expects 2 args: (session, bot) OR (bot, sessionmaker)")

    a, b = args

    # Вариант 1: нам дали (session, bot) / (bot, session)
    if isinstance(a, AsyncSession) and hasattr(b, "send_message"):
        session, bot = a, b
        return await _tick_with_session(session, bot)

    if isinstance(b, AsyncSession) and hasattr(a, "send_message"):
        session, bot = b, a
        return await _tick_with_session(session, bot)

    # Вариант 2/3: нам дали бот и фабрику сессий (или наоборот)
    if hasattr(a, "send_message") and callable(b):
        bot, session_factory = a, b
    elif hasattr(b, "send_message") and callable(a):
        bot, session_factory = b, a
    else:
        raise TypeError("tick_reminders: unable to detect (session vs bot vs sessionmaker)")

    # Открываем сессию из фабрики (async_sessionmaker)
    async with session_factory() as session:
        return await _tick_with_session(session, bot)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime, tz_name: str | None) -> datetime:
    """Локальное -> UTC (aware). Если dt naive — считаем, что он в tz_name."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.astimezone(timezone.utc)


def to_local(dt_utc: datetime, tz_name: str | None) -> datetime:
    """UTC -> локальное (aware)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name or "Europe/Kyiv")
    except Exception:
        tz = ZoneInfo("Europe/Kyiv")
    return dt_utc.astimezone(tz)
