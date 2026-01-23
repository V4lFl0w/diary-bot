from __future__ import annotations

"""
Хэндлеры напоминаний:
- /remind — помощь/примеры
- авто-парсинг текста с триггерами (напомни/enable/disable)
- создание, включение/выключение, список
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Any, List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, text as sql_text

from app.models.user import User
from app.models.reminder import Reminder
from app.services.nlp import parse_any
from app.services.reminders import (
    compute_next_run,
    to_local,
    to_utc,
    now_utc as now_utc_fn,
)
from app.keyboards import is_reminders_btn, get_main_kb

# premium trial hook (мягко, без падений)
try:
    from app.handlers.premium import maybe_grant_trial
except Exception:
    async def maybe_grant_trial(*_a, **_k):
        return False

# feature-gates (мягко, без падений)
try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    async def require_feature_v2(*_a, **_k):
        return True


router = Router(name="reminders")


# ---------------------------------------------------------------------
# I18N (простая локализация)
# ---------------------------------------------------------------------

def _normalize_lang(code: Optional[str]) -> str:
    """
    Приводим код языка к ru/uk/en.
    Поддерживаем ua → uk, uk-UA, en-US и т.п.
    """
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _tr(lang: Optional[str], ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    return uk if l == "uk" else en if l == "en" else ru


async def _get_lang(
    session: AsyncSession,
    m: Message,
    fallback: Optional[str] = None,
) -> str:
    """
    Приоритет языка:
    1) users.locale
    2) users.lang
    3) Telegram language_code
    4) fallback
    5) ru

    Важно: тянем lang/locale через сырой SQL,
    чтобы не зависеть от полноты ORM модели User.
    """
    tg_id = getattr(getattr(m, "from_user", None), "id", None)
    tg_code = getattr(getattr(m, "from_user", None), "language_code", None)

    db_lang: Optional[str] = None
    db_locale: Optional[str] = None

    if tg_id:
        try:
            res = await session.execute(
                sql_text("SELECT lang, locale FROM users WHERE tg_id=:tg"),
                {"tg": tg_id},
            )
            row = res.first()
            if row:
                db_lang, db_locale = row[0], row[1]
        except Exception:
            db_lang = None
            db_locale = None

    return _normalize_lang(db_locale or db_lang or tg_code or fallback or "ru")


# ---------------------------------------------------------------------
# POLICY / TZ helpers
# ---------------------------------------------------------------------

def _policy_ok(user: Optional[User]) -> bool:
    """
    Унифицируем два варианта флага, которые уже встречаются в проекте:
    - policy_accepted (новее)
    - consent_accepted_at (старее)
    """
    if not user:
        return False
    if bool(getattr(user, "policy_accepted", False)):
        return True
    return bool(getattr(user, "consent_accepted_at", None))


def _user_tz_name(user: Optional[User]) -> str:
    return getattr(user, "tz", None) or "Europe/Kyiv"


def _fmt_local(dt_utc: datetime, tz_name: str) -> str:
    return to_local(dt_utc, tz_name).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------
# HELP
# ---------------------------------------------------------------------

def _reminders_help_kb(lang: str) -> InlineKeyboardMarkup:
    l = _normalize_lang(lang)

    def T(ru: str, uk: str, en: str) -> str:
        return uk if l == "uk" else en if l == "en" else ru

    # Кнопки отправляют текст — дальше сработают твои триггеры/парсер
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=T("➕ Создать пример", "➕ Створити приклад", "➕ Create example"),
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text=T("📋 Мои напоминания", "📋 Мої нагадування", "📋 My reminders"),
                callback_data="noop"
            ),
        ],
        [
            InlineKeyboardButton(
                text=T("⛔️ Выключить все", "⛔️ Вимкнути всі", "⛔️ Disable all"),
                callback_data="noop"
            ),
            InlineKeyboardButton(
                text=T("✅ Включить все", "✅ Увімкнути всі", "✅ Enable all"),
                callback_data="noop"
            ),
        ],
    ])

@router.message(Command("remind"))
async def remind_help(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    l = await _get_lang(session, m, fallback=lang)

    await m.answer(
        _tr(
            l,
            "Примеры:\n"
            "• «напомни воду в 12:00»\n"
            "• «напомни позвонить через 15 минут»\n"
            "• «напомни отчёт по будням в 10:00»\n"
            "• «выключи все напоминания» / «включи напоминания вода»",
            "Приклади:\n"
            "• «нагадай воду о 12:00»\n"
            "• «нагадай подзвонити через 15 хвилин»\n"
            "• «нагадай звіт по буднях о 10:00»\n"
            "• «вимкни всі нагадування» / «увімкни нагадування вода»",
            "Examples:\n"
            "• “remind water at 12:00”\n"
            "• “remind to call in 15 minutes”\n"
            "• “remind report weekdays at 10:00”\n"
            "• “disable all reminders” / “enable reminders water”",
        ),
        parse_mode=None,
        reply_markup=_reminders_help_kb(l),
    )


# ---------------------------------------------------------------------
# TRIGGERS
# ---------------------------------------------------------------------

_TRIGGER_WORDS: tuple[str, ...] = (
    # create
    "напомни", "нагадай", "remind",

    # enable
    "включи", "вкл", "увімкни", "enable", "on",

    # disable
    "выключи", "выкл", "відключи", "вимкни", "disable", "off",
)


def _has_trigger(s: Optional[str]) -> bool:
    return bool(s) and any(w in s.lower() for w in _TRIGGER_WORDS)

_TIME_HINT_WORDS: tuple[str, ...] = (
    # RU / UK
    "в ", "у ", "завтра", "сегодня", "послезавтра",
    "через", "каждый", "каждую", "каждое", "каждые",
    "по будням", "по выходным", "ежедневно", "раз в",
    "кожного", "щодня", "по буднях",
    # EN
    "at ", "tomorrow", "today", "in ", "every ", "weekdays", "daily",
)

_time_re = re.compile(
    r"(?ix)"
    r"(?:^|\s)"
    r"(?:в|у|at)\s*\d{1,2}(?::\d{2})?"
    r"|"
    r"(?:через|in)\s+\d+\s*(?:мин|minute|minutes|час|hour|hours|дн|day|days)"
    r"|"
    r"(?:завтра|tomorrow|сегодня|today|послезавтра)\b"
)

def _looks_like_reminder(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if not t or t.startswith("/"):
        return False
    if _has_trigger(t):
        return False
    if _time_re.search(t):
        return True
    return any(w in t for w in _TIME_HINT_WORDS)

def _is_list_alias(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return (
        ("покажи" in t or "список" in t or "list" in t or "show" in t)
        and ("напомин" in t or "remind" in t)
    )

def _should_parse(text: Optional[str]) -> bool:
    return _has_trigger(text) or _looks_like_reminder(text)


# ---------------------------------------------------------------------
# PARSE FLOW
# ---------------------------------------------------------------------

def _looks_like_time_phrase(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    keywords = (
        "через", "завтра", "сегодня", "послезавтра",
        "утром", "вечером", "ночью",
        "в ", " о "
    )
    return any(k in t for k in keywords)


@router.message(F.text.func(_should_parse))
async def remind_parse(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    if not m.from_user:
        return

    # 1) user
    user: Optional[User] = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()

    # язык — из БД, fallback — lang middleware
    l = await _get_lang(session, m, fallback=lang)

    # 2) policy guard
    if not _policy_ok(user):
        await m.answer(
            _tr(
                l,
                "Нужно принять политику: нажми 🔒 Политика",
                "Потрібно прийняти політику: натисни 🔒 Політика",
                "You need to accept the policy: tap 🔒 Privacy",
            ),
            parse_mode=None,
        )
        return

    # 3) feature-gate (если ты решишь сделать премиум-расширение)
    # Базовый remind остаётся доступным всегда, но расширенные сценарии
    # можно потом перевязать на отдельные фичи.

    tz_name = _user_tz_name(user)
    now_utc = now_utc_fn()
    now_local = now_utc.astimezone(ZoneInfo(tz_name))

    # 4) parse_any: create / enable / disable
    parsed = parse_any(m.text or "", user_tz=tz_name, now=now_local)
    if _is_list_alias(m.text or ""):
        await reminders_list(m, session, lang=lang)
        return
    if not parsed:
        await m.answer(
            _tr(
                l,
                "Не понял команду. Напиши: «напомни <что> в/через <когда>» "
                "или «включи/выключи напоминания [про <что>]».",
                "Не розпізнав команду. Напиши: «нагадай <що> о/через <коли>» "
                "або «увімкни/вимкни нагадування [про <що>]».",
                "Didn't understand. Use “remind <what> at/in <when>” "
                "or “enable/disable reminders [about <what>]”.",
            ),
            parse_mode=None,
        )
        return

    # -----------------------------------------------------------------
    # ENABLE / DISABLE
    # -----------------------------------------------------------------
    if parsed.intent in ("enable", "disable"):
        action_enable = parsed.intent == "enable"
        toggle = getattr(parsed, "toggle", None)

        q = (getattr(toggle, "query", "") or "").strip()
        is_all = bool(getattr(toggle, "all", False))

        filters: List[Any] = [Reminder.user_id == user.id]

        if not is_all and q:
            cond = getattr(Reminder.title, "ilike", None)
            filters.append(cond(f"%{q}%") if cond else Reminder.title.like(f"%{q}%"))

        to_update = (
            await session.execute(select(Reminder).where(and_(*filters)))
        ).scalars().all()

        if not to_update:
            await m.answer(
                _tr(
                    l,
                    "Ничего не нашёл по запросу.",
                    "Нічого не знайшов за запитом.",
                    "Found nothing to update.",
                ),
                parse_mode=None,
            )
            return

        await session.execute(
            update(Reminder).where(and_(*filters)).values(is_active=action_enable)
        )

        # если включаем — пересчитываем next_run для просроченных cron
        if action_enable:
            for r in to_update:
                if r.cron and (r.next_run is None or r.next_run <= now_utc):
                    nxt = compute_next_run(r.cron, now_utc, tz_name)
                    if nxt:
                        r.next_run = nxt
                        session.add(r)

        await session.commit()

        cnt = len(to_update)
        await m.answer(
            _tr(
                l,
                f"{'Включил' if action_enable else 'Выключил'} {cnt} напоминаний.",
                f"{'Увімкнув' if action_enable else 'Вимкнув'} {cnt} нагадувань.",
                f"{'Enabled' if action_enable else 'Disabled'} {cnt} reminder(s).",
            ),
            parse_mode=None,
        )
        return

    # -----------------------------------------------------------------
    # CREATE
    # -----------------------------------------------------------------
    pr = getattr(parsed, "reminder", None)
    if not pr:
        await m.answer(
            _tr(
                l,
                "Не удалось разобрать напоминание.",
                "Не вдалося розібрати нагадування.",
                "Couldn't parse the reminder.",
            ),
            parse_mode=None,
        )
        return

    next_run_utc: Optional[datetime] = None
    cron: Optional[str] = None

    if getattr(pr, "cron", None):
        cron = pr.cron
        next_run_utc = compute_next_run(cron, now_utc, tz_name)
        if not next_run_utc:
            await m.answer(
                _tr(
                    l,
                    "Не удалось вычислить расписание. Пример: «каждый день в 09:00», «по будням в 10:00».",
                    "Не вдалося обчислити розклад. Приклад: «щодня о 09:00», «по буднях о 10:00».",
                    "Couldn't compute schedule. E.g., “daily at 09:00”, “weekdays at 10:00”.",
                ),
                parse_mode=None,
            )
            return
    else:
        dt = getattr(pr, "next_run_utc", None)
        if not isinstance(dt, datetime):
            await m.answer(
                _tr(
                    l,
                    "Не понял время. Примеры: «в 12:30», «завтра в 9», «через 15 минут».",
                    "Не зрозумів час. Приклади: «о 12:30», «завтра о 9», «через 15 хвилин».",
                    "Couldn't recognise time. Examples: “at 12:30”, “tomorrow 9”, “in 15 minutes”.",
                ),
                parse_mode=None,
            )
            return
        next_run_utc = to_utc(dt, tz_name)

    what = (getattr(pr, "what", None) or "").strip()
    if not what:
        await m.answer(
            _tr(
                l,
                "Не понял, что именно нужно напомнить.",
                "Не зрозумів, що саме потрібно нагадати.",
                "I didn't understand what to remind about.",
            ),
            parse_mode=None,
        )
        return

    # Дедуп: активное с тем же заголовком и таким же типом расписания
    dup: Optional[Reminder] = (
        await session.execute(
            select(Reminder).where(
                and_(
                    Reminder.user_id == user.id,
                    Reminder.is_active.is_(True),
                    Reminder.title == what,
                    (Reminder.cron == cron) if cron else (Reminder.cron.is_(None)),
                )
            )
        )
    ).scalar_one_or_none()

    if dup:
        dup.next_run = next_run_utc
        session.add(dup)
        await session.commit()

        local_str = _fmt_local(next_run_utc, tz_name)
        await m.answer(
            _tr(
                l,
                f"Обновил напоминание: «{what}». Ближайшее: {local_str} ({tz_name}).",
                f"Оновив нагадування: «{what}». Найближче: {local_str} ({tz_name}).",
                f"Updated reminder: “{what}”. Next: {local_str} ({tz_name}).",
            ),
            parse_mode=None,
        )
        return

    r = Reminder(
        user_id=user.id,
        title=what,
        cron=cron,
        next_run=next_run_utc,
        is_active=True,
    )
    session.add(r)
    await session.commit()

    # trial hook — не ломаем основной флоу
    try:
        await maybe_grant_trial(session, user.tg_id)
    except Exception:
        pass

    local_str = _fmt_local(next_run_utc, tz_name)

    await m.answer(
        _tr(
            l,
            (
                f"Готово! {'Буду напоминать' if cron else 'Напомню'}: «{what}».\n"
                f"{'Первый раз' if cron else 'Время'}: {local_str} ({tz_name})."
            ),
            (
                f"Готово! {'Нагадуватиму' if cron else 'Нагадаю'}: «{what}».\n"
                f"{'Перший раз' if cron else 'Час'}: {local_str} ({tz_name})."
            ),
            (
                f"Done! {'I’ll remind regularly' if cron else 'I’ll remind'}: “{what}”.\n"
                f"{'First run' if cron else 'Time'}: {local_str} ({tz_name})."
            ),
        ),
        parse_mode=None,
    )


# ---------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------

@router.message(Command("reminders"))
async def reminders_list(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    if not m.from_user:
        return

    user: Optional[User] = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()

    l = await _get_lang(session, m, fallback=lang)

    if not user:
        await m.answer(
            _tr(l, "Нажми /start", "Натисни /start", "Press /start"),
            parse_mode=None,
        )
        return


    tz_name = _user_tz_name(user)
    now_utc = now_utc_fn()

    rows = (
        await session.execute(
            select(Reminder).where(Reminder.user_id == user.id)
        )
    ).scalars().all()

    if not rows:
        await m.answer(
            _tr(
                l,
                "Пока нет напоминаний. Пример: «напомни воду в 12:00».",
                "Поки немає нагадувань. Приклад: «нагадай воду о 12:00».",
                "No reminders yet. Example: “remind water at 12:00”.",
            ),
            parse_mode=None,
        )
        return

    # Активные вверх, затем ближайшее время; None в конец
    def _sort_key(r: Reminder) -> tuple[int, float]:
        active_flag = 0 if r.is_active else 1
        nr = r.next_run
        if nr is None:
            return active_flag, float("inf")
        if nr.tzinfo is None:
            nr = nr.replace(tzinfo=timezone.utc)
        return active_flag, nr.timestamp()

    rows.sort(key=_sort_key)

    lines: List[str] = []
    for r in rows[:10]:
        status = "✅" if r.is_active else "⏸️"

        when = "-"
        nr = r.next_run

        if nr:
            if nr.tzinfo is None:
                nr = nr.replace(tzinfo=timezone.utc)
            when = _fmt_local(nr, tz_name)
            if nr <= now_utc:
                when += " ⚠️"
        elif r.cron and r.is_active:
            nxt = compute_next_run(r.cron, now_utc, tz_name)
            when = _fmt_local(nxt, tz_name) if nxt else "-"

        lines.append(f"{status} {r.title} — {when}")

    await m.answer("\n".join(lines), parse_mode=None)


@router.message(F.text.func(is_reminders_btn))
async def reminders_menu(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Обработка кнопки ⏰ Напоминания из главного меню.
    Показываем краткую подсказку по тому, как ставить напоминания.
    """
    await remind_help(m, session, lang=lang)


__all__ = ["router"]



@router.message(
    F.text.func(_looks_like_time_phrase)
    & ~F.text.func(_has_trigger)
    & ~F.text.startswith("/")
)
async def remind_parse_implicit(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    # Мягкий автопарсинг без слова "напомни"
    m2 = Message(
        message_id=m.message_id,
        date=m.date,
        chat=m.chat,
        from_user=m.from_user,
        text="напомни " + (m.text or ""),
    )
    await remind_parse(m2, session, lang)
