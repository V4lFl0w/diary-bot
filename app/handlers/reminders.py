"""
Хэндлеры напоминаний:
- /remind — помощь/примеры
- авто-парсинг текста с триггерами (напомни/enable/disable)
- создание, включение/выключение, список
- UX: без спама, управление напоминаниями (перенести/изменить/удалить/пауза)
"""

from __future__ import annotations

import re
import os
import tempfile
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import httpx

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import and_, delete, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import Reminder
from app.models.user import User
from app.services.nlp import parse_any
from app.services.reminders import (
    compute_next_run,
    to_local,
    to_utc,
)
from app.services.reminders import (
    now_utc as now_utc_fn,
)

from app.services.daily_limits import add_daily_usage, check_daily_available, get_voice_seconds_limit

# premium trial hook (мягко, без падений)

try:
    from app.handlers.premium import maybe_grant_trial as _maybe_grant_trial_any
except Exception:
    _maybe_grant_trial_any = None  # type: ignore


async def _maybe_grant_trial_safe(*args: Any, **kwargs: Any) -> bool:
    fn = _maybe_grant_trial_any
    if not fn:
        return False
    try:
        await fn(*args, **kwargs)
        return True
    except Exception:
        return False


# feature-gates (мягко, без падений)

try:
    from app.services.features_v2 import require_feature_v2 as _require_feature_v2_any
except Exception:
    _require_feature_v2_any = None  # type: ignore


async def _require_feature_v2_safe(*args: Any, **kwargs: Any) -> bool:
    fn = _require_feature_v2_any
    if not fn:
        return True
    try:
        return bool(await fn(*args, **kwargs))
    except Exception:
        return True


router = Router(name="reminders")

# ---------------------------------------------------------------------
# Callback helpers (reply/edit) — локально, чтобы не тянуть зависимости
# ---------------------------------------------------------------------


async def cb_reply(c: CallbackQuery, text: str, **kwargs: Any) -> None:
    try:
        if c.message:
            await c.message.answer(text, **kwargs)
        else:
            await c.answer(text)
    except Exception:
        try:
            await c.answer()
        except Exception:
            pass


async def cb_edit(c: CallbackQuery, text: str, **kwargs: Any) -> None:
    try:
        if c.message:
            await c.message.edit_text(text, **kwargs)
        else:
            await c.answer()
    except Exception:
        try:
            await cb_reply(c, text, **kwargs)
        except Exception:
            pass


# анти-двойной тап по callback (Telegram иногда шлёт два раза)
_CB_COOLDOWN_SEC = 0.9
_last_cb: Dict[Tuple[int, str], float] = {}

# pending actions (перенос/изменение) — без FSM, лёгкий in-memory стейт
# tg_id -> {"action": "move"|"edit", "rid": int, "ts": float}
_pending: Dict[int, Dict[str, Any]] = {}
_PENDING_TTL_SEC = 180.0


# ---------------------------------------------------------------------
# I18N
# ---------------------------------------------------------------------


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _tr(lang: Optional[str], ru: str, uk: str, en: str) -> str:
    lc = _normalize_lang(lang)
    return uk if lc == "uk" else en if lc == "en" else ru


def _reminders_help_kb(lang: str):
    kb = InlineKeyboardBuilder()
    kb.button(text=_tr(lang, "📋 Список", "📋 Список", "📋 List"), callback_data="rem:list")
    kb.button(
        text=_tr(lang, "⛔️ Выкл всё", "⛔️ Вимк все", "⛔️ Disable all"),
        callback_data="rem:disable_all",
    )
    kb.button(
        text=_tr(lang, "🔔 Вкл всё", "🔔 Увімк все", "🔔 Enable all"),
        callback_data="rem:enable_all",
    )
    kb.adjust(2, 1)
    return kb.as_markup()


def _reminder_row_kb(lang: str, rid: int, is_active: bool):
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_tr(lang, "🕒 Перенести", "🕒 Перенести", "🕒 Reschedule"),
        callback_data=f"rem:move:{rid}",
    )
    kb.button(
        text=_tr(lang, "✏️ Изменить", "✏️ Змінити", "✏️ Edit"),
        callback_data=f"rem:edit:{rid}",
    )
    kb.button(
        text=_tr(lang, "🗑️ Удалить", "🗑️ Видалити", "🗑️ Delete"),
        callback_data=f"rem:del:{rid}",
    )
    kb.button(
        text=_tr(
            lang,
            "⏸️ Пауза" if is_active else "▶️ Включить",
            "⏸️ Пауза" if is_active else "▶️ Увімкнути",
            "⏸️ Pause" if is_active else "▶️ Enable",
        ),
        callback_data=f"rem:toggle:{rid}",
    )
    kb.button(text=_tr(lang, "↩️ Назад", "↩️ Назад", "↩️ Back"), callback_data="rem:list")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


async def _get_lang(session: AsyncSession, m: Message, fallback: Optional[str] = None) -> str:
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


async def _get_lang_cb(session: AsyncSession, c: CallbackQuery, fallback: Optional[str] = None) -> str:
    tg_id = getattr(getattr(c, "from_user", None), "id", None)
    tg_code = getattr(getattr(c, "from_user", None), "language_code", None)

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


def _policy_ok(user: User) -> bool:
    if not user:
        return False
    if bool(getattr(user, "policy_accepted", False)):
        return True
    return bool(getattr(user, "consent_accepted_at", None))


def _user_tz_name(user: User) -> str:
    return getattr(user, "tz", None) or "Europe/Kyiv"


def _fmt_local(dt_utc: datetime, tz_name: str) -> str:
    return to_local(dt_utc, tz_name).strftime("%Y-%m-%d %H:%M")


def _rid_of(r: Reminder) -> int:
    for name in ("id", "reminder_id", "rid"):
        v = getattr(r, name, None)
        if isinstance(v, int):
            return v
    raise AttributeError("Reminder has no integer id field")


def _title_of(r: Reminder) -> str:
    return getattr(r, "title", "") or ""


def _active_of(r: Reminder) -> bool:
    return bool(getattr(r, "is_active", False))


def _cron_of(r: Reminder) -> Optional[str]:
    c = getattr(r, "cron", None)
    return c if isinstance(c, str) and c.strip() else None


def _next_run_of(r: Reminder) -> Optional[datetime]:
    dt = getattr(r, "next_run", None)
    return dt if isinstance(dt, datetime) else None


def _desc_line(lang: str, r: Reminder, tz_name: str, now_utc: datetime) -> str:
    status = "✅" if _active_of(r) else "⏸️"
    title = _title_of(r)

    when = "-"
    nr = _next_run_of(r)
    cron = _cron_of(r)

    if nr:
        if nr.tzinfo is None:
            nr = nr.replace(tzinfo=timezone.utc)
        when = _fmt_local(nr, tz_name)
        if nr <= now_utc and _active_of(r):
            when += " ⚠️"
    elif cron and _active_of(r):
        nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
        when = _fmt_local(nxt, tz_name) if nxt else "-"

    return f"{status} {title} — {when}"


async def _load_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


# ---------------------------------------------------------------------
# VOICE GUARDS
# ---------------------------------------------------------------------


async def _reminders_voice_precheck(
    m: Message,
    session: AsyncSession,
    user: Optional[User],
    lang_code: str,
) -> bool:
    if not m.voice:
        await m.answer(
            _tr(
                lang_code,
                "Не вижу голосовое сообщение.",
                "Не бачу голосове повідомлення.",
                "I can't see a voice message.",
            ),
            parse_mode=None,
        )
        return False

    if not user:
        await m.answer(
            _tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"),
            parse_mode=None,
        )
        return False

    voice_seconds = int(getattr(m.voice, "duration", 0) or 0)
    voice_limit = int(get_voice_seconds_limit(user))

    if voice_seconds > voice_limit:
        await m.answer(
            _tr(
                lang_code,
                f"⛔️ Голосовое слишком длинное: {voice_seconds} сек. Лимит: {voice_limit} сек.",
                f"⛔️ Голосове занадто довге: {voice_seconds} сек. Ліміт: {voice_limit} сек.",
                f"⛔️ Voice message is too long: {voice_seconds}s. Limit: {voice_limit}s.",
            ),
            parse_mode=None,
        )
        return False

    ok_daily, used_daily, limit_daily = await check_daily_available(session, user, "reminders_daily", 1)
    if not ok_daily:
        await m.answer(
            _tr(
                lang_code,
                f"⛔️ Лимит напоминаний на сегодня исчерпан: {used_daily}/{limit_daily}.",
                f"⛔️ Ліміт нагадувань на сьогодні вичерпано: {used_daily}/{limit_daily}.",
                f"⛔️ Daily reminders limit reached: {used_daily}/{limit_daily}.",
            ),
            parse_mode=None,
        )
        return False

    return True


async def _transcribe_voice_for_reminders(
    m: Message,
    session: AsyncSession,
    user: Optional[User],
    lang_code: str,
    wait_text: str,
) -> str:
    ok = await _reminders_voice_precheck(m, session, user, lang_code)
    if not ok:
        return ""

    wait_msg = await m.answer(wait_text)
    try:
        text = await _transcribe_voice_free(m, lang_code)
    finally:
        try:
            await wait_msg.delete()
        except Exception:
            pass

    return text.strip()


# ---------------------------------------------------------------------
# VOICE STT (OpenAI)
# ---------------------------------------------------------------------


async def _transcribe_voice_free(message: Message, lang_code: str = "ru") -> str:
    """Надежный перевод голоса через OpenAI Whisper"""
    if not message.voice:
        return ""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ""

    try:
        f = await message.bot.get_file(message.voice.file_id)
        if not f.file_path:
            return ""

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as ogg_file:
            ogg_path = ogg_file.name

        await message.bot.download_file(f.file_path, destination=ogg_path)

        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(ogg_path, "rb") as audio_file:
                r = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                    data={"model": "whisper-1"},
                )
                r.raise_for_status()
                text = r.json().get("text", "")

        os.remove(ogg_path)
        return text
    except Exception as e:
        logging.error(f"Whisper STT Error: {e}")
        if os.path.exists(ogg_path):
            os.remove(ogg_path)
        return ""


# ---------------------------------------------------------------------
# HELP
# ---------------------------------------------------------------------


@router.message(Command("remind"))
async def remind_help(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    if not m.from_user:
        return

    lang_code = await _get_lang(session, m, fallback=lang)
    user = await _load_user(session, m.from_user.id)

    if not user:
        await m.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"), parse_mode=None)
        return

    if not _policy_ok(user):
        await m.answer(
            _tr(
                lang_code,
                "Нужно принять политику: нажми 🔒 Политика",
                "Потрібно прийняти політику: натисни 🔒 Політика",
                "You need to accept the policy: tap 🔒 Privacy",
            ),
            parse_mode=None,
        )
        return

    await m.answer(
        _tr(
            lang_code,
            (
                "⏰ Напоминания без напряга\n\n"
                "Скинь задачу и время текстом или голосовым 🎙 — я напомню.\n\n"
                "Примеры:\n"
                "• Вода в 12:00\n"
                "• Отчёт по будням в 10:00\n"
                "• Через 30 минут лечь спать\n\n"
                "Подсказка: напиши «Покажи напоминания» чтобы управлять ими."
            ),
            (
                "⏰ Нагадування без напруги\n\n"
                "Надішли задачу й час текстом або голосовим 🎙 — я нагадаю.\n\n"
                "Приклади:\n"
                "• Вода о 12:00\n"
                "• Звіт по буднях о 10:00\n"
                "• Через 30 хвилин лягти спати\n\n"
                "Підказка: напиши «Покажи нагадування» щоб керувати ними."
            ),
            (
                "⏰ Reminders without pressure\n\n"
                "Send the task and time (text or voice 🎙) — I’ll remind you.\n\n"
                "Examples:\n"
                "• Water at 12:00\n"
                "• Report weekdays at 10:00\n"
                "• Go to sleep in 30 minutes\n\n"
                "Tip: send “Show reminders” to manage them."
            ),
        ),
        parse_mode=None,
        reply_markup=_reminders_help_kb(lang_code),
    )


# ---------------------------------------------------------------------
# TRIGGERS
# ---------------------------------------------------------------------

_TRIGGER_WORDS: tuple[str, ...] = (
    "напомни",
    "нагадай",
    "remind",
    "включи",
    "вкл",
    "увімкни",
    "enable",
    "on",
    "выключи",
    "выкл",
    "відключи",
    "вимкни",
    "disable",
    "off",
)


def _has_trigger(s: Optional[str]) -> bool:
    return bool(s) and any(w in s.lower() for w in _TRIGGER_WORDS)


# РОБАСТНЫЙ ПАРСЕР ДЛЯ ВСЕХ 3 ЯЗЫКОВ
_time_re = re.compile(
    r"(?ix)"
    r"(?:^|\s)"
    r"(?:в|у|о|об|at)\s*\d{1,2}(?::\d{2})?"
    r"|"
    r"(?:через|in|за)\s+\d+\s*(?:мин|хв|хвилин|minute|minutes|час|год|годин|hour|hours|дн|днів|день|day|days)"
    r"|"
    r"(?:завтра|tomorrow|сегодня|сьогодні|today|послезавтра|післязавтра)\b"
    r"|"
    r"\d{1,2}[\./-]\d{1,2}(?:[\./-]\d{2,4})?"
    r"|"
    r"(?:в\s+это\s+|в\s+|у\s+|на\s+|this\s+|next\s+|on\s+)?(?:понедельник|понеділок|monday|вторник|вівторок|tuesday|сред[уа]|середу|wednesday|четверг|четвер|thursday|пятниц[уа]|пʼятницю|friday|суббот[уа]|суботу|saturday|воскресенье|неділю|sunday)\b"
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

    strong = (
        "по будням",
        "по выходным",
        "ежедневно",
        "раз в",
        "каждый",
        "каждую",
        "каждое",
        "каждые",
        "щодня",
        "по буднях",
        "кожного",
        "на вихідних",
        "кожні",
        "кожна",
        "кожен",
        "every ",
        "weekdays",
        "weekends",
        "daily",
        "everyday",
    )
    return any(x in t for x in strong)


def _is_list_alias(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return ("покажи" in t or "список" in t or "list" in t or "show" in t) and (
        "напомин" in t or "remind" in t or "нагадуван" in t or "нагадай" in t
    )


def _should_parse(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    if t in ["⏰ напоминания", "напоминания", "⏰ нагадування", "нагадування", "⏰ reminders", "reminders"]:
        return False
    return _has_trigger(text) or _looks_like_reminder(text) or _is_list_alias(text)


def _clean_reminder_title(raw: str) -> str:
    s = (raw or "").strip()

    s = re.sub(
        r"^(напомни(?:ть)?|нагадай|remind)\b[:,]?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()

    s = re.sub(r"\s+", " ", s).strip(" .,:;!-–—")

    bad_exact = {
        "напомни",
        "напомнить",
        "remind",
        "нагадай",
        "сходить",
        "сделать",
        "купить",
        "позвонить",
        "написать",
        "починить",
    }

    if s.lower() in bad_exact:
        return ""

    return s


def _has_any_time_hint(text: str) -> bool:
    return bool(_time_re.search((text or "").strip().lower()))


# ---------------------------------------------------------------------
# PARSE FLOW (Текст + Голос)
# ---------------------------------------------------------------------


@router.message(F.voice)
async def remind_parse_voice(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    """Перехват голоса для создания напоминаний. Если не подходит — отдаем в Журнал/Ассистент"""
    if not m.from_user:
        return

    lang_code = await _get_lang(session, m, fallback=lang)
    user = await _load_user(session, m.from_user.id)

    text = await _transcribe_voice_for_reminders(
        m,
        session,
        user,
        lang_code,
        "🎧 Анализирую голос...",
    )

    # Если precheck не прошёл — уже ответили пользователю
    if not text:
        return

    new_m = m.model_copy(update={"text": text})
    await new_m.answer(f"🗣 <i>«{text}»</i>", parse_mode="HTML")
    await remind_parse(new_m, session, lang)


@router.message(F.text.func(_should_parse))
async def remind_parse(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    if not m.from_user:
        return

    user = await _load_user(session, m.from_user.id)
    lang_code = await _get_lang(session, m, fallback=lang)

    if not user:
        await m.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"), parse_mode=None)
        return

    if not _policy_ok(user):
        await m.answer(
            _tr(
                lang_code,
                "Нужно принять политику: нажми 🔒 Политика",
                "Потрібно прийняти політику: натисни 🔒 Політика",
                "You need to accept the policy: tap 🔒 Privacy",
            ),
            parse_mode=None,
        )
        return

    tz_name = _user_tz_name(user)
    now_utc = now_utc_fn()
    now_local = now_utc.astimezone(ZoneInfo(tz_name))

    if _is_list_alias(m.text or ""):
        await reminders_list(m, session, lang=lang)
        return

    raw_text = m.text or ""
    parsed = parse_any(raw_text, user_tz=tz_name, now=now_local)
    if not parsed:
        if _has_trigger(raw_text):
            title_guess = _clean_reminder_title(raw_text)
            if title_guess and not _has_any_time_hint(raw_text):
                await m.answer(
                    _tr(
                        lang_code,
                        f"Ок, задачу понял: «{title_guess}». Когда напомнить?",
                        f"Ок, задачу зрозумів: «{title_guess}». Коли нагадати?",
                        f"Got it: “{title_guess}”. When should I remind you?",
                    ),
                    parse_mode=None,
                )
                return

        await m.answer(
            _tr(
                lang_code,
                "Не понял. Пример: «вода в 12:00» или «напомни воду в 12:00».",
                "Не зрозумів. Приклад: «вода о 12:00» або «нагадай воду о 12:00».",
                "Didn't understand. Example: “water at 12:00” or “remind water at 12:00”.",
            ),
            parse_mode=None,
        )
        return

    # ENABLE / DISABLE
    if parsed.intent in ("enable", "disable"):
        action_enable = parsed.intent == "enable"
        toggle = getattr(parsed, "toggle", None)

        q = (getattr(toggle, "query", "") or "").strip()
        is_all = bool(getattr(toggle, "all", False))

        filters: List[Any] = [Reminder.user_id == user.id]

        if not is_all and q:
            cond = getattr(Reminder.title, "ilike", None)
            filters.append(cond(f"%{q}%") if cond else Reminder.title.like(f"%{q}%"))

        to_update = (await session.execute(select(Reminder).where(and_(*filters)))).scalars().all()
        if not to_update:
            await m.answer(
                _tr(lang_code, "Ничего не нашёл.", "Нічого не знайшов.", "Found nothing."),
                parse_mode=None,
            )
            return

        await session.execute(update(Reminder).where(and_(*filters)).values(is_active=action_enable))

        if action_enable:
            for r in to_update:
                if _cron_of(r) and (_next_run_of(r) is None or ((nr := _next_run_of(r)) is not None and nr <= now_utc)):
                    cron = _cron_of(r)
                    nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
                    if nxt:
                        r.next_run = nxt
                        session.add(r)

        await session.commit()

        cnt = len(to_update)
        await m.answer(
            _tr(
                lang_code,
                f"{'Включил' if action_enable else 'Выключил'} {cnt} напоминаний.",
                f"{'Увімкнув' if action_enable else 'Вимкнув'} {cnt} нагадувань.",
                f"{'Enabled' if action_enable else 'Disabled'} {cnt} reminder(s).",
            ),
            parse_mode=None,
        )
        return

    # CREATE
    pr = getattr(parsed, "reminder", None)
    if not pr:
        await m.answer(
            _tr(lang_code, "Не удалось разобрать.", "Не вдалося розібрати.", "Couldn't parse."),
            parse_mode=None,
        )
        return

    next_run_utc: Optional[datetime] = None
    cron: Optional[str] = None

    if getattr(pr, "cron", None):
        cron = pr.cron
        next_run_utc = compute_next_run(cron, now_utc, tz_name) if cron else None
        if not next_run_utc:
            await m.answer(
                _tr(
                    lang_code,
                    "Не понял расписание. Пример: «каждый день в 09:00».",
                    "Не зрозумів розклад. Приклад: «щодня о 09:00».",
                    "Couldn't compute schedule. Example: “daily at 09:00”.",
                ),
                parse_mode=None,
            )
            return
    else:
        dt = getattr(pr, "next_run_utc", None)
        if not isinstance(dt, datetime):
            await m.answer(
                _tr(
                    lang_code,
                    "Не понял время. Пример: «в 12:30», «завтра в 9», «через 15 минут».",
                    "Не зрозумів час. Приклад: «о 12:30», «завтра о 9», «через 15 хвилин».",
                    "Couldn't recognise time. Example: “at 12:30”, “tomorrow 9”, “in 15 minutes”.",
                ),
                parse_mode=None,
            )
            return
        next_run_utc = to_utc(dt, tz_name)

    what = _clean_reminder_title((getattr(pr, "what", None) or "").strip())
    if not what:
        await m.answer(
            _tr(
                lang_code,
                "Я понял время, но не понял саму задачу. Что именно напомнить?",
                "Я зрозумів час, але не зрозумів саму задачу. Що саме нагадати?",
                "I understood the time, but not the task itself. What exactly should I remind you about?",
            ),
            parse_mode=None,
        )
        return

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
                lang_code,
                f"Обновил: «{what}»\n🕒 {local_str}",
                f"Оновив: «{what}»\n🕒 {local_str}",
                f"Updated: “{what}”\n🕒 {local_str}",
            ),
            parse_mode=None,
        )
        return

    ok_daily, used_daily, limit_daily = await check_daily_available(session, user, "reminders_daily", 1)
    if not ok_daily:
        await m.answer(
            _tr(
                lang_code,
                f"⛔️ Лимит напоминаний на сегодня исчерпан: {used_daily}/{limit_daily}.\n"
                "Попробуй завтра или обнови тариф.",
                f"⛔️ Ліміт нагадувань на сьогодні вичерпано: {used_daily}/{limit_daily}.\n"
                "Спробуй завтра або онови тариф.",
                f"⛔️ Daily reminders limit reached: {used_daily}/{limit_daily}.\n"
                "Try again tomorrow or upgrade your plan.",
            ),
            parse_mode=None,
        )
        return

    r = Reminder(user_id=user.id, title=what, cron=cron, next_run=next_run_utc, is_active=True)
    session.add(r)
    await session.commit()
    await add_daily_usage(session, user, "reminders_daily", 1)

    try:
        await _maybe_grant_trial_safe(session, user.tg_id)
    except Exception:
        pass

    local_str = _fmt_local(next_run_utc, tz_name)
    await m.answer(
        _tr(
            lang_code,
            f"Готово ✅ «{what}»\n🕒 {local_str}",
            f"Готово ✅ «{what}»\n🕒 {local_str}",
            f"Done ✅ “{what}”\n🕒 {local_str}",
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
    tg_id_override: Optional[int] = None,
) -> None:
    tg_id = tg_id_override or getattr(getattr(m, "from_user", None), "id", None)
    if not tg_id:
        return

    user = await _load_user(session, tg_id)
    lang_code = await _get_lang(session, m, fallback=lang)
    if not user:
        await m.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"), parse_mode=None)
        return

    tz_name = _user_tz_name(user)
    now_utc = now_utc_fn()

    rows = (await session.execute(select(Reminder).where(Reminder.user_id == user.id))).scalars().all()
    if not rows:
        await m.answer(
            _tr(
                lang_code,
                "Пока нет напоминаний. Напиши: «вода в 12:00» или надиктуй.",
                "Поки немає нагадувань. Напиши: «вода о 12:00» або надиктуй.",
                "No reminders yet. Send: “water at 12:00” or use voice.",
            ),
            parse_mode=None,
        )
        return

    def _sort_key(r: Reminder) -> tuple[int, float]:
        active_flag = 0 if _active_of(r) else 1
        nr = _next_run_of(r)
        if nr is None:
            return active_flag, float("inf")
        if nr.tzinfo is None:
            nr = nr.replace(tzinfo=timezone.utc)
        return active_flag, nr.timestamp()

    rows.sort(key=_sort_key)

    top = _tr(lang_code, "📋 Твои напоминания:", "📋 Твої нагадування:", "📋 Your reminders:")
    lines = [top]
    for r in rows[:10]:
        lines.append(_desc_line(lang_code, r, tz_name, now_utc))

    kb = InlineKeyboardBuilder()
    for r in rows[:10]:
        rid = _rid_of(r)
        line = _desc_line(lang_code, r, tz_name, now_utc)
        kb.button(text=line[:64], callback_data=f"rem:open:{rid}")

    kb.button(
        text=_tr(lang_code, "🔔 Вкл всё", "🔔 Увімк все", "🔔 Enable all"),
        callback_data="rem:enable_all",
    )
    kb.button(
        text=_tr(lang_code, "⛔️ Выкл всё", "⛔️ Вимк все", "⛔️ Disable all"),
        callback_data="rem:disable_all",
    )
    kb.adjust(1, 1, 1, 2, 1)

    await m.answer("\n".join(lines), parse_mode=None, reply_markup=kb.as_markup())


@router.message(
    F.text.func(lambda t: t and any(x in t.lower() for x in ["напоминания", "нагадування", "reminders"])),
    StateFilter("*"),
)
async def reminders_menu(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    await remind_help(m, session, lang=lang)


# ---------------------------------------------------------------------
# PENDING INPUT HANDLER (перенести/изменить) ТЕКСТ + ГОЛОС
# ---------------------------------------------------------------------


@router.message(F.voice & F.from_user.id.func(lambda uid: uid in _pending))
async def reminders_pending_voice(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    if not m.from_user:
        return

    lang_code = await _get_lang(session, m, fallback=lang)
    user = await _load_user(session, m.from_user.id)

    text = await _transcribe_voice_for_reminders(
        m,
        session,
        user,
        lang_code,
        "🎧 Расшифровываю голос...",
    )

    if not text:
        return

    new_m = m.model_copy(update={"text": text})
    await new_m.answer(f"🗣 <i>«{text}»</i>", parse_mode="HTML")
    await reminders_pending_input(new_m, session, lang)


@router.message(F.text & F.from_user.id.func(lambda uid: uid in _pending))
async def reminders_pending_input(m: Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    if not m.from_user:
        return

    tg_id = m.from_user.id
    p = _pending.get(tg_id)
    if not p:
        return

    if monotonic() - float(p.get("ts", 0.0)) > _PENDING_TTL_SEC:
        _pending.pop(tg_id, None)
        return

    user = await _load_user(session, tg_id)
    lang_code = await _get_lang(session, m, fallback=lang)
    if not user:
        _pending.pop(tg_id, None)
        return

    if not _policy_ok(user):
        _pending.pop(tg_id, None)
        return

    rid = int(p["rid"])
    action = str(p["action"])
    tz_name = _user_tz_name(user)
    now_utc = now_utc_fn()
    now_local = now_utc.astimezone(ZoneInfo(tz_name))

    r = (
        await session.execute(select(Reminder).where(and_(Reminder.user_id == user.id, Reminder.id == rid)))
    ).scalar_one_or_none()

    if not r:
        _pending.pop(tg_id, None)
        await m.answer(
            _tr(
                lang_code,
                "Не нашёл напоминание.",
                "Не знайшов нагадування.",
                "Reminder not found.",
            ),
            parse_mode=None,
        )
        return

    text = (m.text or "").strip()
    if not text:
        return

    if action == "edit":
        r.title = text
        session.add(r)
        await session.commit()
        _pending.pop(tg_id, None)

        await m.answer(
            _tr(
                lang_code,
                f"Ок ✅ Изменил на: «{text}»",
                f"Ок ✅ Змінив на: «{text}»",
                f"Ok ✅ Updated to: “{text}”",
            ),
            parse_mode=None,
        )
        return

    if action == "move":
        fake = f"напомни tmp {text}"
        parsed = parse_any(fake, user_tz=tz_name, now=now_local)
        pr = getattr(parsed, "reminder", None) if parsed else None

        if not pr:
            await m.answer(
                _tr(
                    lang_code,
                    "Не понял время. Пример: «в 12:30», «завтра в 9», «через 15 минут».",
                    "Не зрозумів час. Приклад: «о 12:30», «завтра о 9», «через 15 хвилин».",
                    "Couldn't recognise time. Example: “at 12:30”, “tomorrow 9”, “in 15 minutes”.",
                ),
                parse_mode=None,
            )
            return

        if getattr(pr, "cron", None):
            r.cron = pr.cron
            cron = r.cron
            nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
            r.next_run = nxt
        else:
            dt = getattr(pr, "next_run_utc", None)
            if not isinstance(dt, datetime):
                await m.answer(
                    _tr(
                        lang_code,
                        "Не понял время.",
                        "Не зрозумів час.",
                        "Couldn't recognise time.",
                    ),
                    parse_mode=None,
                )
                return
            r.cron = None
            r.next_run = to_utc(dt, tz_name)

        r.is_active = True

        session.add(r)
        await session.commit()
        _pending.pop(tg_id, None)

        nr = _next_run_of(r) or now_utc
        local_str = _fmt_local(nr if nr.tzinfo else nr.replace(tzinfo=timezone.utc), tz_name)

        await m.answer(
            _tr(
                lang_code,
                f"Перенёс ✅\n🕒 {local_str}",
                f"Переніс ✅\n🕒 {local_str}",
                f"Rescheduled ✅\n🕒 {local_str}",
            ),
            parse_mode=None,
        )
        return


# ---------------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------------


@router.callback_query(F.data.startswith("rem:"))
async def reminders_callbacks(c: CallbackQuery, session: AsyncSession, lang: Optional[str] = None) -> None:
    if not c.from_user:
        return

    data = (c.data or "").strip().lower()

    key = (c.from_user.id, data)
    ts = monotonic()
    prev = _last_cb.get(key, 0.0)
    if ts - prev < _CB_COOLDOWN_SEC:
        try:
            await c.answer()
        except Exception:
            pass
        return
    _last_cb[key] = ts

    try:
        await c.answer()
    except Exception:
        pass

    user = await _load_user(session, c.from_user.id)
    lang_code = await _get_lang_cb(session, c, fallback=lang)

    if not user:
        if c.message:
            await cb_reply(
                c,
                _tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"),
                parse_mode=None,
            )
        return

    if not _policy_ok(user):
        if c.message:
            await cb_reply(
                c,
                _tr(
                    lang_code,
                    "Нужно принять политику: нажми 🔒 Политика",
                    "Потрібно прийняти політику: натисни 🔒 Політика",
                    "You need to accept the policy: tap 🔒 Privacy",
                ),
                parse_mode=None,
            )
        return

    if data == "rem:list":
        if c.message:
            await reminders_list(c.message, session, lang=lang_code, tg_id_override=c.from_user.id)
        return

    if data in {"rem:disable_all", "rem:enable_all"}:
        action_enable = data == "rem:enable_all"

        await session.execute(update(Reminder).where(Reminder.user_id == user.id).values(is_active=action_enable))

        tz_name = _user_tz_name(user)
        now_utc = now_utc_fn()

        if action_enable:
            rows = (await session.execute(select(Reminder).where(Reminder.user_id == user.id))).scalars().all()
            for r in rows:
                if _cron_of(r) and (_next_run_of(r) is None or ((nr := _next_run_of(r)) is not None and nr <= now_utc)):
                    cron = _cron_of(r)
                    nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
                    if nxt:
                        r.next_run = nxt
                        session.add(r)

        await session.commit()

        try:
            await c.answer(
                _tr(
                    lang_code,
                    "✅ Все напоминания включены." if action_enable else "⛔️ Все напоминания выключены.",
                    "✅ Усі нагадування увімкнено." if action_enable else "⛔️ Усі нагадування вимкнено.",
                    "✅ Enabled all reminders." if action_enable else "⛔️ Disabled all reminders.",
                ),
                show_alert=False,
            )
        except Exception:
            pass

        if c.message:
            await reminders_list(c.message, session, lang=lang_code, tg_id_override=c.from_user.id)
        return

    if data == "rem:example":
        tz_name = _user_tz_name(user)
        now_utc = now_utc_fn()
        now_local = now_utc.astimezone(ZoneInfo(tz_name))

        dt_local = (now_local + timedelta(minutes=10)).replace(second=0, microsecond=0)
        next_run_utc = to_utc(dt_local, tz_name)

        title = _tr(lang_code, "выпить воды", "випити води", "drink water")
        r = Reminder(
            user_id=user.id,
            title=title,
            cron=None,
            next_run=next_run_utc,
            is_active=True,
        )
        session.add(r)
        await session.commit()

        local_str = _fmt_local(next_run_utc, tz_name)
        if c.message:
            await cb_reply(
                c,
                _tr(
                    lang_code,
                    f"Сделал пример ✅\n«{title}»\n🕒 {local_str}",
                    f"Зробив приклад ✅\n«{title}»\n🕒 {local_str}",
                    f"Example created ✅\n“{title}”\n🕒 {local_str}",
                ),
                parse_mode=None,
            )
        return

    if data.startswith("rem:open:"):
        rid = int(data.split(":")[-1])
        r = (
            await session.execute(select(Reminder).where(and_(Reminder.user_id == user.id, Reminder.id == rid)))
        ).scalar_one_or_none()

        if not r:
            try:
                await c.answer(_tr(lang_code, "Не нашёл.", "Не знайшов.", "Not found."), show_alert=False)
            except Exception:
                pass
            return

        tz_name = _user_tz_name(user)
        now_utc = now_utc_fn()

        title = _title_of(r)
        cron = _cron_of(r)
        nr = _next_run_of(r)

        if nr and nr.tzinfo is None:
            nr = nr.replace(tzinfo=timezone.utc)

        when = "-"
        if nr:
            when = _fmt_local(nr, tz_name)
            if nr <= now_utc and _active_of(r):
                when += " ⚠️"
        elif cron and _active_of(r):
            nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
            when = _fmt_local(nxt, tz_name) if nxt else "-"

        body = _tr(
            lang_code,
            f"⏰ Напоминание\n\n«{title}»\n🕒 {when}\n{'🔁 ' + cron if cron else ''}",
            f"⏰ Нагадування\n\n«{title}»\n🕒 {when}\n{'🔁 ' + cron if cron else ''}",
            f"⏰ Reminder\n\n“{title}”\n🕒 {when}\n{'🔁 ' + cron if cron else ''}",
        ).strip()

        if c.message:
            await cb_edit(
                c,
                body,
                parse_mode=None,
                reply_markup=_reminder_row_kb(lang_code, rid, _active_of(r)),
            )
        return

    if data.startswith("rem:toggle:"):
        rid = int(data.split(":")[-1])
        r = (
            await session.execute(select(Reminder).where(and_(Reminder.user_id == user.id, Reminder.id == rid)))
        ).scalar_one_or_none()

        if not r:
            return

        r.is_active = not _active_of(r)

        tz_name = _user_tz_name(user)
        now_utc = now_utc_fn()

        if (
            r.is_active
            and _cron_of(r)
            and (_next_run_of(r) is None or ((nr := _next_run_of(r)) is not None and nr <= now_utc))
        ):
            cron = _cron_of(r)
            nxt = compute_next_run(cron, now_utc, tz_name) if cron else None
            if nxt:
                r.next_run = nxt

        session.add(r)
        await session.commit()

        try:
            await c.answer(
                _tr(
                    lang_code,
                    "✅ Включено" if r.is_active else "⏸️ На паузе",
                    "✅ Увімкнено" if r.is_active else "⏸️ На паузі",
                    "✅ Enabled" if r.is_active else "⏸️ Paused",
                ),
                show_alert=False,
            )
        except Exception:
            pass

        if c.message:
            await reminders_callbacks(
                CallbackQuery(
                    id=c.id,
                    from_user=c.from_user,
                    chat_instance=c.chat_instance,
                    message=c.message,
                    data=f"rem:open:{rid}",
                ),
                session,
                lang=lang_code,
            )
        return

    if data.startswith("rem:del:"):
        rid = int(data.split(":")[-1])

        await session.execute(delete(Reminder).where(and_(Reminder.user_id == user.id, Reminder.id == rid)))
        await session.commit()

        try:
            await c.answer(_tr(lang_code, "🗑️ Удалено", "🗑️ Видалено", "🗑️ Deleted"), show_alert=False)
        except Exception:
            pass

        if c.message:
            await reminders_list(c.message, session, lang=lang_code, tg_id_override=c.from_user.id)
        return

    if data.startswith("rem:move:") or data.startswith("rem:edit:"):
        parts = data.split(":")
        action = parts[1]  # move / edit
        rid = int(parts[2])

        _pending[c.from_user.id] = {"action": action, "rid": rid, "ts": monotonic()}

        prompt = _tr(
            lang_code,
            "Ок. Пришли новое время (например: «в 12:30», «через 15 минут») или надиктуй голосом 🎙."
            if action == "move"
            else "Ок. Пришли новый текст напоминания или надиктуй голосом 🎙.",
            "Ок. Надішли новий час (наприклад: «о 12:30», «через 15 хвилин») або надиктуй голосом 🎙."
            if action == "move"
            else "Ок. Надішли новий текст нагадування або надиктуй голосом 🎙.",
            "Ok. Send new time (e.g. “at 12:30”, “in 15 minutes”) or send a voice message 🎙."
            if action == "move"
            else "Ok. Send new reminder text or voice message 🎙.",
        )

        if c.message:
            await cb_reply(c, prompt, parse_mode=None)
        return


__all__ = ["router"]
