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

OPEN_TRIGGERS = ("🥇 Мотивация", "🥇 Мотивація", "🥇 Motivation", "Мотивация", "Мотивація", "Motivation")


# ---------- helpers ----------
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


# ---------- UI labels (per language) ----------
def _btns(lang: str) -> dict[str, str]:
    return {
        "support": _t(lang, "💬 Поддержка", "💬 Підтримка", "💬 Support"),
        "push": _t(lang, "⚡ Пинок", "⚡ Поштовх", "⚡ Kick"),
        "plan": _t(lang, "🗓 План дня", "🗓 План дня", "🗓 Day plan"),
        "streak": _t(lang, "🏁 Серия", "🏁 Серія", "🏁 Streak"),
        "reset": _t(lang, "🧩 Вернуться в игру", "🧩 Повернутись у гру", "🧩 Back in the game"),
        "quote": _t(lang, "🪶 Цитата", "🪶 Цитата", "🪶 Quote"),
        "back": _t(lang, "⬅️ Назад", "⬅️ Назад", "⬅️ Back"),
    }


def _kb(lang: str) -> ReplyKeyboardMarkup:
    b = _btns(lang)
    rows = [
        [KeyboardButton(text=b["support"]), KeyboardButton(text=b["push"])],
        [KeyboardButton(text=b["plan"]), KeyboardButton(text=b["streak"])],
        [KeyboardButton(text=b["reset"]), KeyboardButton(text=b["quote"])],
        [KeyboardButton(text=b["back"])],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------- business logic ----------
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


# ---------- quotes (curated + infinite generator) ----------
def _gen_quote(lang: str) -> str:
    # "almost infinite" — combos
    if lang == "en":
        a = ["Start", "Do", "Keep", "Choose", "Build", "Return"]
        b = ["small", "simple", "honest", "steady", "one clear", "real"]
        c = ["steps", "actions", "moves", "wins", "habits", "minutes"]
        d = ["today", "right now", "before you think", "without perfection", "with zero drama", "even when tired"]
        return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."

    if lang == "uk":
        a = ["Почни", "Зроби", "Тримай", "Обери", "Повернись", "Будуй"]
        b = ["малий", "простий", "чесний", "стабільний", "один чіткий", "реальний"]
        c = ["крок", "рух", "вчинок", "результат", "звичку", "15 хвилин"]
        d = ["сьогодні", "прямо зараз", "без ідеалу", "без драми", "навіть коли втомився", "до того як почнеш сумніватись"]
        return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."

    # ru
    a = ["Начни", "Сделай", "Держи", "Выбери", "Вернись", "Собери"]
    b = ["маленький", "простой", "честный", "стабильный", "один чёткий", "реальный"]
    c = ["шаг", "движ", "вклад", "результат", "привычку", "15 минут"]
    d = ["сегодня", "прямо сейчас", "без идеала", "без драмы", "даже когда устал", "до того как начнёшь сомневаться"]
    return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."


_CURATED = {
    "ru": [
        "Сначала — шаг. Потом — скорость.",
        "Не жди идеального состояния. Делай в текущем.",
        "Дисциплина — это когда ты держишь слово себе.",
        "Стабильность важнее вдохновения.",
        "Твой прогресс — это сумма маленьких повторов.",
    ],
    "uk": [
        "Спочатку — крок. Потім — швидкість.",
        "Не чекай ідеального стану. Роби в поточному.",
        "Дисципліна — це коли ти тримаєш слово собі.",
        "Стабільність важливіша за натхнення.",
        "Твій прогрес — це сума маленьких повторів.",
    ],
    "en": [
        "First the step. Then the speed.",
        "Don’t wait for perfect. Act in your current state.",
        "Discipline is keeping promises to yourself.",
        "Consistency beats inspiration.",
        "Progress is built from small repeats.",
    ],
}


def _curated(lang: str) -> str:
    return random.choice(_CURATED.get(lang, _CURATED["ru"]))


# ---------- open ----------
@router.message(F.text.in_(OPEN_TRIGGERS))
async def motivation_open(m: Message, session: AsyncSession):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    hook = _t(
        lang,
        "🥇 Мотивация — чтобы не сливать день и возвращаться без вины.\n"
        "Начни с ⚡ Пинок (15 минут) или 💬 Поддержка (1 строка).",
        "🥇 Мотивація — щоб не зливати день і повертатись без провини.\n"
        "Почни з ⚡ Поштовх (15 хвилин) або 💬 Підтримка (1 рядок).",
        "🥇 Motivation — to stop wasting days and come back with zero guilt.\n"
        "Start with ⚡ Kick (15 min) or 💬 Support (1 line).",
    )

    await m.answer(hook, reply_markup=_kb(lang))


# ---------- routes (text match per language) ----------
def _is_btn(lang: str, key: str, text: str) -> bool:
    return (text or "").strip() == _btns(lang)[key]


@router.message(F.text)
async def motivation_router(m: Message, session: AsyncSession):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))
    b = _btns(lang)
    txt = (m.text or "").strip()

    # Back
    if txt == b["back"]:
        # тут ты сам решишь: вернуть home-меню, или просто закрыть
        await m.answer(_t(lang, "Ок.", "Ок.", "Ok."))
        return

    # Need /start
    if not user:
        await m.answer(_t(lang, "Нажми /start", "Натисни /start", "Press /start"))
        return

    # Support
    if txt == b["support"]:
        await _handle_support(m, session, user, lang)
        return

    # Kick
    if txt == b["push"]:
        await _handle_push(m, lang)
        return

    # Plan
    if txt == b["plan"]:
        await _handle_plan(m, lang)
        return

    # Streak
    if txt == b["streak"]:
        await _handle_streak(m, session, user, lang)
        return

    # Reset (instead of anti-slip)
    if txt == b["reset"]:
        await _handle_reset(m, session, user, lang)
        return

    # Quote
    if txt == b["quote"]:
        await _handle_quote(m, lang)
        return


async def _handle_support(m: Message, session: AsyncSession, user: User, lang: str):
    last_dt = await _last_entry_dt(session, user.id)
    now = datetime.now(timezone.utc)

    if not last_dt:
        msg = _t(
            lang,
            "Я рядом. Начнём с малого: напиши одну строку — что сейчас чувствуешь.",
            "Я поруч. Почнемо з малого: напиши один рядок — що зараз відчуваєш.",
            "I’m here. Start small: one line — what do you feel right now?",
        )
    else:
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        hours = int((now - last_dt).total_seconds() // 3600)

        if hours >= 48:
            msg = _t(
                lang,
                f"Ты давно не писал ({hours} ч). Это ок.\n"
                "Без вины: одна короткая запись — и ты снова в игре.",
                f"Ти давно не писав ({hours} год). Це ок.\n"
                "Без провини: один короткий запис — і ти знову в грі.",
                f"You’ve been away ({hours}h). It’s ok.\n"
                "No guilt: one short entry — and you’re back.",
            )
        else:
            msg = _t(
                lang,
                "Мягкий режим: одна мысль, один факт, одно действие. Ты справишься.",
                "М’який режим: одна думка, один факт, одна дія. Ти впораєшся.",
                "Soft mode: one thought, one fact, one action. You’ve got this.",
            )

    await m.answer(msg)


async def _handle_push(m: Message, lang: str):
    prompts = [
        _t(lang,
           "⚡ 15 минут. Одна задача. Просто начни.\n\nНапиши: «делаю … 15 минут»",
           "⚡ 15 хвилин. Одна задача. Просто почни.\n\nНапиши: «роблю … 15 хвилин»",
           "⚡ 15 minutes. One task. Just start.\n\nReply: “I do … for 15 minutes”"),
        _t(lang,
           "Выбери одно: тело / голова / порядок.\nЧто подтянем за 15 минут?",
           "Обери одне: тіло / голова / порядок.\nЩо підтягнемо за 15 хвилин?",
           "Pick one: body / mind / order.\nWhat will we improve in 15 minutes?"),
        _t(lang,
           "Не идеал. Не мотивация. Действие.\nЧто сделаем прямо сейчас?",
           "Не ідеал. Не мотивація. Дія.\nЩо зробимо прямо зараз?",
           "No perfect. No motivation. Action.\nWhat do we do right now?"),
    ]
    await m.answer(random.choice(prompts))


async def _handle_plan(m: Message, lang: str):
    await m.answer(
        _t(
            lang,
            "🗓 План на день (1 сообщение):\n"
            "1) 3 задачи (коротко)\n"
            "2) 1 микро-шаг на 5 минут\n"
            "3) Что может помешать? + как обойдёшь\n\n"
            "Ответь по пунктам — и всё.",
            "🗓 План на день (1 повідомлення):\n"
            "1) 3 задачі (коротко)\n"
            "2) 1 мікро-крок на 5 хвилин\n"
            "3) Що може завадити? + як обійдеш\n\n"
            "Відповідай по пунктах — і все.",
            "🗓 Day plan (one message):\n"
            "1) 3 tasks (short)\n"
            "2) 1 micro-step (5 min)\n"
            "3) What may block you? + how you’ll bypass\n\n"
            "Reply in bullets.",
        )
    )


async def _handle_streak(m: Message, session: AsyncSession, user: User, lang: str):
    tz = _user_tz(user)
    streak = await _calc_streak(session, user.id, tz)

    if streak <= 0:
        msg = _t(
            lang,
            "🏁 Серия: 0.\nЗапусти сегодня: одна короткая запись — и серия начнётся.",
            "🏁 Серія: 0.\nЗапусти сьогодні: один короткий запис — і серія почнеться.",
            "🏁 Streak: 0.\nStart today: one short entry — and it begins.",
        )
    elif streak < 3:
        msg = _t(
            lang,
            f"🏁 Серия: {streak}.\nДожмём до 3 — дальше держаться легче.",
            f"🏁 Серія: {streak}.\nДотиснемо до 3 — далі легше триматись.",
            f"🏁 Streak: {streak}.\nPush to 3 — it gets easier.",
        )
    elif streak < 7:
        msg = _t(
            lang,
            f"🏁 Серия: {streak}.\nЭто уже дисциплина. Продолжай.",
            f"🏁 Серія: {streak}.\nЦе вже дисципліна. Продовжуй.",
            f"🏁 Streak: {streak}.\nThat’s discipline. Keep going.",
        )
    else:
        msg = _t(
            lang,
            f"🏁 Серия: {streak}.\nМощно. Не ломай — просто продолжай.",
            f"🏁 Серія: {streak}.\nСильно. Не ламай — просто продовжуй.",
            f"🏁 Streak: {streak}.\nStrong. Don’t break it — just continue.",
        )

    await m.answer(msg)


async def _handle_reset(m: Message, session: AsyncSession, user: User, lang: str):
    # Прод-логика: моментальный “возврат в игру” + 2 понятных действия
    last_dt = await _last_entry_dt(session, user.id)
    now = datetime.now(timezone.utc)

    away_line = ""
    if last_dt:
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        hours = int((now - last_dt).total_seconds() // 3600)
        if hours >= 12:
            away_line = _t(lang, f"Ты выпадал примерно на {hours} ч. Это нормально.\n", f"Ти випадав приблизно на {hours} год. Це нормально.\n", f"You were away ~{hours}h. That’s normal.\n")

    msg = _t(
        lang,
        "🧩 Вернуться в игру\n\n"
        f"{away_line}"
        "Выбирай самый лёгкий шаг:\n"
        "1) Напиши одну строку: «Сейчас важно …»\n"
        "2) Или возьми 15 минут: «делаю … 15 минут»\n\n"
        "Без идеала. Без вины. Просто старт.",
        "🧩 Повернутись у гру\n\n"
        f"{away_line}"
        "Обери найлегший крок:\n"
        "1) Напиши один рядок: «Зараз важливо …»\n"
        "2) Або 15 хвилин: «роблю … 15 хвилин»\n\n"
        "Без ідеалу. Без провини. Просто старт.",
        "🧩 Back in the game\n\n"
        f"{away_line}"
        "Pick the easiest step:\n"
        "1) One line: “Right now it matters …”\n"
        "2) Or 15 minutes: “I do … for 15 minutes”\n\n"
        "No perfect. No guilt. Just start.",
    )
    await m.answer(msg)


async def _handle_quote(m: Message, lang: str):
    strong = _curated(lang)
    gen = _gen_quote(lang)
    await m.answer(f"🪶 {strong}\n\n{gen}")
