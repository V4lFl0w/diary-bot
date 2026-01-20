from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional, Iterable

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.journal import JournalEntry

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

router = Router(name="motivation")

BTN_SUPPORT = "💬 Поддержка"
BTN_PUSH = "⚡ Пинок"
BTN_PLAN = "🗓 План дня"
BTN_STREAK = "🏁 Серия"
BTN_ANTISLIP = "🧩 Антислив"
BTN_QUOTE = "🪶 Цитата"
BTN_BACK = "⬅️ Назад"

OPEN_TRIGGERS = ("🥇 Мотивация", "🥇 Мотивація", "🥇 Motivation", "Мотивация", "Мотивація", "Motivation")


def _kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_PUSH)],
        [KeyboardButton(text=BTN_PLAN), KeyboardButton(text=BTN_STREAK)],
        [KeyboardButton(text=BTN_ANTISLIP), KeyboardButton(text=BTN_QUOTE)],
        [KeyboardButton(text=BTN_BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], tg_lang: Optional[str]) -> str:
    loc = (getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _user_tz(user: Optional[User]):
    tz_name = getattr(user, "tz", None) or "Europe/Kyiv"
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _t(lang: str, ru: str, uk: str, en: str) -> str:
    if lang == "uk":
        return uk
    if lang == "en":
        return en
    return ru


async def _last_entry_dt(session: AsyncSession, user_id: int) -> Optional[datetime]:
    q = (
        select(JournalEntry.created_at)
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.created_at.desc())
        .limit(1)
    )
    return (await session.execute(q)).scalar_one_or_none()


def _unique_days(dts: Iterable[datetime], tz) -> list:
    days = []
    seen = set()
    for dt in dts:
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(tz)
        d = local.date()
        if d not in seen:
            seen.add(d)
            days.append(d)
    return days


async def _calc_streak(session: AsyncSession, user_id: int, tz) -> int:
    q = (
        select(JournalEntry.created_at)
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.created_at.desc())
        .limit(500)
    )
    rows = (await session.execute(q)).scalars().all()
    days = _unique_days(rows, tz)
    if not days:
        return 0

    streak = 1
    for i in range(1, len(days)):
        prev = days[i - 1]
        cur = days[i]
        if (prev - cur).days == 1:
            streak += 1
        else:
            break
    return streak


@router.message(F.text.in_(OPEN_TRIGGERS))
async def motivation_open(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await m.answer(
        _t(
            lang,
            "Мотивация. Выбери режим ниже.",
            "Мотивація. Обери режим нижче.",
            "Motivation. Choose a mode below.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text == BTN_SUPPORT)
async def motivation_support(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    if not user:
        await m.answer(_t(lang, "Нажми /start", "Натисни /start", "Press /start"))
        return

    last_dt = await _last_entry_dt(session, user.id)
    now = datetime.now(timezone.utc)

    if not last_dt:
        msg = _t(
            lang,
            "Я рядом. Начнём с малого: напиши одну строку — что сейчас чувствуешь.",
            "Я поруч. Почнемо з малого: напиши один рядок — що зараз відчуваєш.",
            "I'm here. Start small: write one line — what do you feel right now?",
        )
    else:
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        hours = int((now - last_dt).total_seconds() // 3600)
        if hours >= 48:
            msg = _t(
                lang,
                f"Ты давно не писал ({hours} ч). Это ок. Без вины: одна короткая запись — и ты снова в игре.",
                f"Ти давно не писав ({hours} год). Це ок. Без провини: один короткий запис — і ти знову в грі.",
                f"You've been away ({hours}h). It's ok. No guilt: one short entry and you're back.",
            )
        else:
            msg = _t(
                lang,
                "Мягкий режим: одна мысль, один факт, одно действие. Ты справишься.",
                "М'який режим: одна думка, один факт, одна дія. Ти впораєшся.",
                "Soft mode: one thought, one fact, one action. You’ve got this.",
            )

    await m.answer(msg)


@router.message(F.text == BTN_PUSH)
async def motivation_push(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    prompts = [
        _t(lang, "Что сделаем за 15 минут прямо сейчас?", "Що зробимо за 15 хвилин прямо зараз?", "What will we do in 15 minutes right now?"),
        _t(lang, "Одна задача. Без идеала. Только начать.", "Одна задача. Без ідеалу. Просто почати.", "One task. No perfection. Just start."),
        _t(lang, "Выбери: тело, голова или порядок. Что подтянем за 15 минут?", "Обери: тіло, голова чи порядок. Що підтягнемо за 15 хвилин?", "Pick: body, mind, or order. What do we improve in 15 minutes?"),
    ]
    await m.answer(random.choice(prompts))


@router.message(F.text == BTN_PLAN)
async def motivation_plan(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await m.answer(
        _t(
            lang,
            "План на день:\n1) 3 задачи (коротко)\n2) 1 микро-шаг на 5 минут\n3) Что может помешать?\n\nОтветь одним сообщением по пунктам.",
            "План на день:\n1) 3 задачі (коротко)\n2) 1 мікро-крок на 5 хвилин\n3) Що може завадити?\n\nВідповідай одним повідомленням по пунктах.",
            "Day plan:\n1) 3 tasks (short)\n2) 1 micro-step (5 min)\n3) What may block you?\n\nReply in one message.",
        )
    )


@router.message(F.text == BTN_STREAK)
async def motivation_streak(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    if not user:
        await m.answer(_t(lang, "Нажми /start", "Натисни /start", "Press /start"))
        return

    tz = _user_tz(user)
    streak = await _calc_streak(session, user.id, tz)

    if streak <= 0:
        msg = _t(
            lang,
            "Серия пока 0. Начни сегодня: одна короткая запись и серия запустится.",
            "Серія поки 0. Почни сьогодні: один короткий запис і серія запуститься.",
            "Streak is 0. Start today: one short entry and it begins.",
        )
    elif streak < 3:
        msg = _t(
            lang,
            f"Серия: {streak} дня. Дожмём до 3 — и станет легче держаться.",
            f"Серія: {streak} дні. Дотиснемо до 3 — і буде легше триматися.",
            f"Streak: {streak} days. Push to 3 and it gets easier.",
        )
    elif streak < 7:
        msg = _t(
            lang,
            f"Серия: {streak} дней. Это уже дисциплина. Награда: ты держишь слово себе.",
            f"Серія: {streak} днів. Це вже дисципліна. Нагорода: ти тримаєш слово собі.",
            f"Streak: {streak} days. That’s discipline. Reward: you keep your promise to yourself.",
        )
    else:
        msg = _t(
            lang,
            f"Серия: {streak} дней. Это мощно. Не ломай — просто продолжай.",
            f"Серія: {streak} днів. Це сильно. Не ламай — просто продовжуй.",
            f"Streak: {streak} days. Strong. Don’t break it — just continue.",
        )

    await m.answer(msg)


@router.message(F.text == BTN_ANTISLIP)
async def motivation_antisink(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await m.answer(
        _t(
            lang,
            "Антислив:\n• Вернуться можно без вины.\n• Напиши «Я вернулся» и одну строку — что сейчас важно.",
            "Антизлив:\n• Повернутися можна без провини.\n• Напиши «Я повернувся» і один рядок — що зараз важливо.",
            "No-slip:\n• Come back with zero guilt.\n• Write “I’m back” + one line about what matters now.",
        )
    )


@router.message(F.text == BTN_QUOTE)
async def motivation_quote(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    quotes_ru = [
        "Сначала — шаг. Потом — скорость.",
        "Не жди идеального состояния. Делай в текущем.",
        "Маленькая дисциплина каждый день сильнее мотивации раз в неделю.",
        "Ты не обязан быть идеальным, чтобы быть стабильным.",
    ]
    quotes_uk = [
        "Спочатку — крок. Потім — швидкість.",
        "Не чекай ідеального стану. Роби в поточному.",
        "Маленька дисципліна щодня сильніша за мотивацію раз на тиждень.",
        "Ти не мусиш бути ідеальним, щоб бути стабільним.",
    ]
    quotes_en = [
        "First the step. Then the speed.",
        "Don’t wait for perfect. Act in your current state.",
        "Small daily discipline beats weekly motivation.",
        "You don’t need perfection to be consistent.",
    ]

    if lang == "uk":
        q = random.choice(quotes_uk)
    elif lang == "en":
        q = random.choice(quotes_en)
    else:
        q = random.choice(quotes_ru)

    await m.answer(q)
