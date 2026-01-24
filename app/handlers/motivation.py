from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional, Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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


# ----------------- FSM -----------------
class MotivationFSM(StatesGroup):
    waiting_support = State()
    waiting_push = State()
    waiting_plan = State()
    waiting_reset = State()


# ----------------- helpers -----------------
async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], tg_lang: Optional[str]) -> str:
    loc = (getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _t(lang: str, ru: str, uk: str, en: str) -> str:
    if lang == "uk":
        return uk
    if lang == "en":
        return en
    return ru


def _user_tz(user: Optional[User]):
    tz_name = getattr(user, "tz", None) or "Europe/Kyiv"
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


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
        d = dt.astimezone(tz).date()
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


# ----------------- UI -----------------
def _btns(lang: str) -> dict[str, str]:
    return {
        "support": _t(lang, "💬 Поддержка (1 строка)", "💬 Підтримка (1 рядок)", "💬 Support (1 line)"),
        "push": _t(lang, "⚡ Пинок (15 минут)", "⚡ Поштовх (15 хв)", "⚡ Kick (15 min)"),
        "plan": _t(lang, "🗓 План (3 пункта)", "🗓 План (3 пункти)", "🗓 Plan (3 bullets)"),
        "streak": _t(lang, "🏁 Серия (дни)", "🏁 Серія (дні)", "🏁 Streak (days)"),
        "reset": _t(lang, "🧩 Вернуться (без вины)", "🧩 Повернутись (без провини)", "🧩 Come back (no guilt)"),
        "quote": _t(lang, "🪶 Цитата (новая)", "🪶 Цитата (нова)", "🪶 Quote (new)"),
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


# ----------------- Quotes (variety) -----------------
_CURATED = {
    "ru": [
        "Дисциплина — это держать слово себе.",
        "Не надо идеально. Надо сегодня.",
        "Один честный шаг > ноль идеальных планов.",
        "Твоя сила — в повторах, а не в настроении.",
        "Начни с малого — и мозг подтянется следом.",
    ],
    "uk": [
        "Дисципліна — це тримати слово собі.",
        "Не треба ідеально. Треба сьогодні.",
        "Один чесний крок > нуль ідеальних планів.",
        "Твоя сила — у повторах, а не в настрої.",
        "Почни з малого — і мозок підтягнеться.",
    ],
    "en": [
        "Discipline is keeping promises to yourself.",
        "Not perfect. Today.",
        "One honest step beats zero perfect plans.",
        "Your power is repetition, not mood.",
        "Start small — your brain will follow.",
    ],
}


def _gen_quote(lang: str) -> str:
    # “бесконечность” через комбинации
    if lang == "en":
        a = ["Do", "Start", "Keep", "Choose", "Build", "Return"]
        b = ["one", "a small", "a simple", "a real", "a calm", "an honest"]
        c = ["step", "move", "action", "15 minutes", "tiny start", "repeat"]
        d = ["right now", "today", "without drama", "without perfection", "even tired", "before you overthink"]
        return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."
    if lang == "uk":
        a = ["Зроби", "Почни", "Тримай", "Обери", "Будуй", "Повернись"]
        b = ["один", "малий", "простий", "реальний", "спокійний", "чесний"]
        c = ["крок", "рух", "вчинок", "15 хвилин", "старт", "повтор"]
        d = ["прямо зараз", "сьогодні", "без драми", "без ідеалу", "навіть втомлений", "до того як засумніваєшся"]
        return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."
    # ru
    a = ["Сделай", "Начни", "Держи", "Выбери", "Собери", "Вернись"]
    b = ["один", "маленький", "простой", "реальный", "спокойный", "честный"]
    c = ["шаг", "движ", "вклад", "15 минут", "старт", "повтор"]
    d = ["прямо сейчас", "сегодня", "без драмы", "без идеала", "даже уставшим", "до того как начнёшь сомневаться"]
    return f"{random.choice(a)} {random.choice(b)} {random.choice(c)} {random.choice(d)}."


# ----------------- Open -----------------
@router.message(F.text.in_(OPEN_TRIGGERS))
async def motivation_open(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    hook = _t(
        lang,
        "🔥 Мотивация\n\n"
        "Я помогу тебе не сливаться и быстро возвращаться.\n"
        "Выбирай ниже — это занимает 30 секунд.",
        "🔥 Мотивація\n\n"
        "Я допоможу не зливатися і швидко повертатися.\n"
        "Обирай нижче — це займає 30 секунд.",
        "🔥 Motivation\n\n"
        "I’ll help you stop drifting and come back fast.\n"
        "Pick a button — 30 seconds.",
    )
    await m.answer(hook, reply_markup=_kb(lang))


@router.message(Command("cancel"))
async def motivation_cancel(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    await state.clear()
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))
    await m.answer(_t(lang, "Ок, отменил.", "Ок, скасував.", "Ok, cancelled."), reply_markup=_kb(lang))


# ----------------- Buttons -----------------
@router.message(F.text)
async def motivation_buttons(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))
    b = _btns(lang)
    txt = (m.text or "").strip()

    # support old labels compatibility
    legacy_support = txt in ("💬 Поддержка", "💬 Підтримка", "💬 Support")
    legacy_push = txt in ("⚡ Пинок", "⚡ Поштовх", "⚡ Kick")
    legacy_plan = txt in ("🗓 План дня", "🗓 План дня", "🗓 Day plan")
    legacy_streak = txt in ("🏁 Серия", "🏁 Серія", "🏁 Streak")
    legacy_reset = txt in ("🧩 Антислив", "🧩 Антизлив", "🧩 No-slip", "🧩 Антислив")
    legacy_quote = txt in ("🪶 Цитата", "🪶 Цитата", "🪶 Quote")

    # Back
    if txt == b["back"] or txt == "⬅️ Назад":
        await state.clear()
        await m.answer(_t(lang, "Ок.", "Ок.", "Ok."))
        return

    if not user:
        await state.clear()
        await m.answer(_t(lang, "Нажми /start", "Натисни /start", "Press /start"))
        return

    # SUPPORT
    if txt == b["support"] or legacy_support:
        await state.set_state(MotivationFSM.waiting_support)
        await m.answer(_t(
            lang,
            "Я рядом. Одна строка: что сейчас чувствуешь?\n\nОтмена: /cancel",
            "Я поруч. Один рядок: що зараз відчуваєш?\n\nСкасування: /cancel",
            "I’m here. One line: what do you feel right now?\n\nCancel: /cancel",
        ))
        return

    # PUSH
    if txt == b["push"] or legacy_push:
        await state.set_state(MotivationFSM.waiting_push)
        await m.answer(_t(
            lang,
            "Ок. 15 минут.\nНапиши так: «делаю … 15 минут».\n\nОтмена: /cancel",
            "Ок. 15 хвилин.\nНапиши так: «роблю … 15 хвилин».\n\nСкасування: /cancel",
            "Ok. 15 minutes.\nReply: “I do … for 15 minutes”.\n\nCancel: /cancel",
        ))
        return

    # PLAN
    if txt == b["plan"] or legacy_plan:
        await state.set_state(MotivationFSM.waiting_plan)
        await m.answer(_t(
            lang,
            "Пиши одним сообщением:\n"
            "1) 3 задачи\n"
            "2) 1 шаг на 5 минут\n"
            "3) что может помешать + как обойдёшь\n\nОтмена: /cancel",
            "Пиши одним повідомленням:\n"
            "1) 3 задачі\n"
            "2) 1 крок на 5 хв\n"
            "3) що може завадити + як обійдеш\n\nСкасування: /cancel",
            "One message:\n"
            "1) 3 tasks\n"
            "2) 5-min step\n"
            "3) blocker + workaround\n\nCancel: /cancel",
        ))
        return

    # STREAK
    if txt == b["streak"] or legacy_streak:
        tz = _user_tz(user)
        streak = await _calc_streak(session, user.id, tz)
        await m.answer(_t(
            lang,
            f"🏁 Серия: {streak}.\nХочешь — я буду напоминать утром/вечером: /proactive",
            f"🏁 Серія: {streak}.\nХочеш — я буду нагадувати ранок/вечір: /proactive",
            f"🏁 Streak: {streak}.\nWant reminders morning/evening? /proactive",
        ))
        return

    # RESET
    if txt == b["reset"] or legacy_reset:
        await state.set_state(MotivationFSM.waiting_reset)
        await m.answer(_t(
            lang,
            "Без вины. Одна строка:\n«Сейчас важно …»\n\nОтмена: /cancel",
            "Без провини. Один рядок:\n«Зараз важливо …»\n\nСкасування: /cancel",
            "No guilt. One line:\n“Right now it matters …”\n\nCancel: /cancel",
        ))
        return

    # QUOTE
    if txt == b["quote"] or legacy_quote:
        base = random.choice(_CURATED.get(lang, _CURATED["ru"]))
        gen = _gen_quote(lang)
        await m.answer(f"🪶 {base}\n{gen}")
        return


# ----------------- Answer handlers (FSM) -----------------
@router.message(MotivationFSM.waiting_support)
async def motivation_support_answer(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    txt = (m.text or "").strip()
    await state.clear()

    if not txt:
        await m.answer(_t(lang, "Скажи одной строкой, как оно.", "Скажи одним рядком, як воно.", "One line — how is it."))
        return

    # человеческая реакция + следующий шаг
    reply = _t(
        lang,
        f"Понял. «{txt}» — это нормально.\nДавай самый лёгкий шаг: что ты сделаешь за 2 минуты прямо сейчас?",
        f"Зрозумів. «{txt}» — це нормально.\nДавай найлегший крок: що зробиш за 2 хвилини просто зараз?",
        f"Got it. “{txt}” is valid.\nPick the easiest step: what will you do for 2 minutes right now?",
    )
    await m.answer(reply, reply_markup=_kb(lang))


@router.message(MotivationFSM.waiting_push)
async def motivation_push_answer(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    txt = (m.text or "").strip()
    await state.clear()

    if not txt:
        await m.answer(_t(lang, "Ок, напиши одной строкой.", "Ок, напиши одним рядком.", "Ok, one line."), reply_markup=_kb(lang))
        return

    # подтверждение + мини-план
    msg = _t(
        lang,
        f"🔥 Принято.\n{txt}\n\n"
        "Правило 15 минут:\n"
        "1) убери отвлекающее\n"
        "2) сделай самый простой кусок\n"
        "3) в конце — просто остановись (не добивай до идеала)\n\n"
        "Напиши «сделал», когда закончишь.",
        f"🔥 Прийнято.\n{txt}\n\n"
        "Правило 15 хв:\n"
        "1) прибери зайве\n"
        "2) зроби найпростіший шматок\n"
        "3) в кінці — просто зупинись (без ідеалу)\n\n"
        "Напиши «зробив», коли закінчиш.",
        f"🔥 Locked.\n{txt}\n\n"
        "15-min rule:\n"
        "1) remove distraction\n"
        "2) do the easiest chunk\n"
        "3) stop on time (no perfection)\n\n"
        "Reply “done” when finished.",
    )
    await m.answer(msg, reply_markup=_kb(lang))


@router.message(MotivationFSM.waiting_plan)
async def motivation_plan_answer(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    txt = (m.text or "").strip()
    await state.clear()

    if not txt:
        await m.answer(_t(lang, "Кидай план одним сообщением.", "Кидай план одним повідомленням.", "Send the plan in one message."), reply_markup=_kb(lang))
        return

    # превращаем в чеклист + якорь
    msg = _t(
        lang,
        "✅ Принято. Якорь на день:\n"
        "— выбери одну задачу №1 и начни с 5 минут.\n\n"
        "Твой план:\n" + txt,
        "✅ Прийнято. Якір на день:\n"
        "— обери задачу №1 і почни з 5 хв.\n\n"
        "Твій план:\n" + txt,
        "✅ Got it. Day anchor:\n"
        "— pick task #1 and start with 5 minutes.\n\n"
        "Your plan:\n" + txt,
    )
    await m.answer(msg, reply_markup=_kb(lang))


@router.message(MotivationFSM.waiting_reset)
async def motivation_reset_answer(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    txt = (m.text or "").strip()
    await state.clear()

    if not txt:
        await m.answer(_t(lang, "Одной строкой — что важно.", "Одним рядком — що важливо.", "One line — what matters."), reply_markup=_kb(lang))
        return

    msg = _t(
        lang,
        f"🧩 Ок. {txt}\n\n"
        "Сейчас сделай один шаг:\n"
        "— открой то, что связано с этим\n"
        "— и сделай 2 минуты без остановки\n\n"
        "Если хочешь, чтобы я сам напоминал и собирал прогресс → /proactive",
        f"🧩 Ок. {txt}\n\n"
        "Зараз зроби один крок:\n"
        "— відкрий те, що з цим пов’язано\n"
        "— і зроби 2 хв без зупинки\n\n"
        "Хочеш нагадування і прогрес → /proactive",
        f"🧩 Ok. {txt}\n\n"
        "Do one step now:\n"
        "— open what’s related\n"
        "— do 2 minutes non-stop\n\n"
        "Want reminders & progress? /proactive",
    )
    await m.answer(msg, reply_markup=_kb(lang))


__all__ = ["router"]
