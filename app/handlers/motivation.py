from __future__ import annotations

import random
from dataclasses import dataclass
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

# ---------- UI ----------
BTN_SUPPORT = "💬 Поддержка (1 строка)"
BTN_PUSH = "⚡ Старт на 15 минут"
BTN_PLAN = "🧭 Разгрузить голову (план)"
BTN_STREAK = "🔥 Серия (дни)"
BTN_RETURN = "🔄 Вернуться в игру"
BTN_QUOTE = "🪶 Цитата (новая)"
BTN_BACK = "⬅️ Назад"

OPEN_TRIGGERS = ("🥇 Мотивация", "🥇 Мотивація", "🥇 Motivation", "Мотивация", "Мотивація", "Motivation")


def _kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_PUSH)],
        [KeyboardButton(text=BTN_PLAN), KeyboardButton(text=BTN_STREAK)],
        [KeyboardButton(text=BTN_RETURN), KeyboardButton(text=BTN_QUOTE)],
        [KeyboardButton(text=BTN_BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------- i18n (минимум, по делу) ----------
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


# ---------- DB helpers ----------
async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


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


# ---------- FSM ----------
class MotFSM(StatesGroup):
    wait_support = State()
    wait_push = State()
    wait_plan = State()
    wait_return = State()


def _soft_open_text(lang: str) -> str:
    return _t(
        lang,
        "🔥 Мотивация\n\nЯ помогу быстро собраться, когда ты “плывёшь”.\nВыбери ниже — это реально займёт 30 секунд.\n\nЕсли хочешь просто начать — жми ⚡ «Старт на 15 минут».",
        "🔥 Мотивація\n\nЯ допоможу швидко зібратися, коли ти “пливеш”.\nОбери нижче — це займе 30 секунд.\n\nЯкщо хочеш просто почати — тисни ⚡ «Старт на 15 хвилин».",
        "🔥 Motivation\n\nI’ll help you get back on track fast.\nPick an option below — it takes ~30 seconds.\n\nIf you just want to start — tap ⚡ “15-minute start”.",
    )


def _cancel_hint(lang: str) -> str:
    return _t(lang, "Отмена: /cancel", "Скасування: /cancel", "Cancel: /cancel")


@router.message(F.text.in_(OPEN_TRIGGERS))
async def motivation_open(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await m.answer(_soft_open_text(lang), reply_markup=_kb())


@router.message(Command("cancel"))
async def motivation_cancel(m: Message, state: FSMContext):
    await state.clear()
    # клавиатуру возвращаем всегда
    await m.answer("Ок.", reply_markup=_kb())


# ---------- SUPPORT ----------
@router.message(F.text == BTN_SUPPORT)
async def motivation_support_start(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await state.set_state(MotFSM.wait_support)
    await m.answer(
        _t(
            lang,
            "Я рядом. Одна строка: что сейчас чувствуешь?\n(пример: «тревожно», «пусто», «злюсь», «страшно»)",
            "Я поруч. Один рядок: що ти зараз відчуваєш?\n(приклад: «тривожно», «порожньо», «злюся», «страшно»)",
            "I’m here. One line: what do you feel right now?",
        ) + "\n\n" + _cancel_hint(lang),
        reply_markup=_kb(),
    )


@router.message(MotFSM.wait_support, F.text)
async def motivation_support_reply(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    feeling = (m.text or "").strip()
    await state.clear()

    if not feeling:
        await m.answer(_t(lang, "Напиши одной строкой, как чувствуешь.", "Напиши одним рядком, як почуваєшся.", "Write one short line."), reply_markup=_kb())
        return

    # мягкий, понятный ответ + следующий шаг
    await m.answer(
        _t(
            lang,
            f"Понял: «{feeling}». Это нормально.\nДавай без героизма: выбери один вариант:\n1) ⚡ 15 минут — и отпускает\n2) 🧭 План — чтобы голова не шумела\n3) 🔄 Вернуться — если был срыв",
            f"Зрозумів: «{feeling}». Це нормально.\nБез героїзму: обери одне:\n1) ⚡ 15 хвилин — і відпускає\n2) 🧭 План — щоб голова не шуміла\n3) 🔄 Повернутися — якщо був зрив",
            f"Got it: “{feeling}”. That’s okay.\nPick one:\n1) ⚡ 15-minute start\n2) 🧭 Quick plan\n3) 🔄 Come back",
        ),
        reply_markup=_kb(),
    )


# ---------- PUSH 15 MIN ----------
@router.message(F.text == BTN_PUSH)
async def motivation_push_start(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await state.set_state(MotFSM.wait_push)
    await m.answer(
        _t(
            lang,
            "Ок. Выбери ОДНУ мини-задачу на 15 минут.\nНапиши так: «делаю: ...»\nПример: «делаю: зарядку 15 минут»",
            "Ок. Обери ОДНУ міні-задачу на 15 хвилин.\nНапиши так: «роблю: ...»\nПриклад: «роблю: зарядку 15 хвилин»",
            "Ok. Pick ONE 15-minute task.\nWrite: “doing: ...”",
        ) + "\n\n" + _cancel_hint(lang),
        reply_markup=_kb(),
    )


@router.message(MotFSM.wait_push, F.text)
async def motivation_push_reply(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    text = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Принято. {text}\n\nСделай просто старт на 2 минуты (не всё сразу).\nПотом напиши «готово» — я закреплю.",
            f"Прийнято. {text}\n\nЗроби просто старт на 2 хвилини (не все одразу).\nПотім напиши «готово» — я закріплю.",
            f"Locked in: {text}\n\nStart for 2 minutes (not the whole thing).\nThen reply “done”.",
        ),
        reply_markup=_kb(),
    )


# ---------- PLAN ----------
@router.message(F.text == BTN_PLAN)
async def motivation_plan_start(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await state.set_state(MotFSM.wait_plan)
    await m.answer(
        _t(
            lang,
            "Чтобы голова успокоилась — сделаем простой план.\nНапиши одним сообщением:\n1) Что надо сделать СЕГОДНЯ (1–3 пункта)\n2) Самый лёгкий шаг на 5 минут\n3) Что может помешать (1 штука)\n\nЯ отвечу коротко и по делу.",
            "Щоб голова заспокоїлась — зробимо простий план.\nНапиши одним повідомленням:\n1) Що треба зробити СЬОГОДНІ (1–3 пункти)\n2) Найлегший крок на 5 хвилин\n3) Що може завадити (1 штука)\n\nЯ відповім коротко і по ділу.",
            "Quick plan to calm the mind.\nReply in one message:\n1) 1–3 things to do today\n2) easiest 5-minute step\n3) one thing that may block you",
        ) + "\n\n" + _cancel_hint(lang),
        reply_markup=_kb(),
    )


@router.message(MotFSM.wait_plan, F.text)
async def motivation_plan_reply(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    txt = (m.text or "").strip()
    await state.clear()

    # супер-легкий разбор без умничанья
    first_line = txt.splitlines()[0] if txt else ""
    await m.answer(
        _t(
            lang,
            f"Ок. Главное — не идеал, а движение.\nПервый шаг прямо сейчас: сделай 2 минуты из «{first_line[:40]}…».\n\nЕсли хочешь — жми ⚡ «Старт на 15 минут».",
            f"Ок. Головне — не ідеал, а рух.\nПерший крок прямо зараз: зроби 2 хвилини з «{first_line[:40]}…».\n\nХочеш — тисни ⚡ «Старт на 15 хвилин».",
            f"Ok. Not perfection — motion.\nFirst step now: do 2 minutes of “{first_line[:40]}…”.",
        ),
        reply_markup=_kb(),
    )


# ---------- STREAK ----------
@router.message(F.text == BTN_STREAK)
async def motivation_streak(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    if not user:
        await m.answer(_t(lang, "Нажми /start", "Натисни /start", "Press /start"), reply_markup=_kb())
        return

    tz = _user_tz(user)
    streak = await _calc_streak(session, user.id, tz)

    if streak <= 0:
        msg = _t(
            lang,
            "Серия: 0.\nХочешь включить? Сделай одну короткую запись в дневник — и пойдёт.",
            "Серія: 0.\nХочеш увімкнути? Зроби один короткий запис — і піде.",
            "Streak: 0.\nWant to start it? Make one short journal entry today.",
        )
    else:
        msg = _t(
            lang,
            f"🔥 Серия: {streak} день(дней).\nНе ломай — просто один маленький шаг сегодня.",
            f"🔥 Серія: {streak} день(днів).\nНе ламай — просто один маленький крок сьогодні.",
            f"🔥 Streak: {streak} day(s).\nDon’t break it — one small step today.",
        )

    await m.answer(msg, reply_markup=_kb())


# ---------- RETURN (вместо «антислив») ----------
@router.message(F.text == BTN_RETURN)
async def motivation_return_start(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    await state.set_state(MotFSM.wait_return)
    await m.answer(
        _t(
            lang,
            "Бывает. Без стыда.\nОдна строка: что сейчас важно вернуть под контроль?",
            "Буває. Без сорому.\nОдин рядок: що зараз важливо повернути під контроль?",
            "It happens. No shame.\nOne line: what do you want to regain control of?",
        ) + "\n\n" + _cancel_hint(lang),
        reply_markup=_kb(),
    )


@router.message(MotFSM.wait_return, F.text)
async def motivation_return_reply(m: Message, session: AsyncSession, state: FSMContext):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    focus = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Ок. Возвращаем «{focus}».\nСделай самый лёгкий шаг на 2 минуты прямо сейчас.\n\nХочешь — жми ⚡ «Старт на 15 минут».",
            f"Ок. Повертаємо «{focus}».\nЗроби найлегший крок на 2 хвилини прямо зараз.\n\nХочеш — тисни ⚡ «Старт на 15 хвилин».",
            f"Ok. We’re regaining “{focus}”.\nDo the easiest 2-minute step now.",
        ),
        reply_markup=_kb(),
    )


# ---------- QUOTE ----------
@router.message(F.text == BTN_QUOTE)
async def motivation_quote(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    lang = _user_lang(user, getattr(m.from_user, "language_code", None))

    quotes_ru = [
        "Не нужен идеальный день. Нужен первый шаг.",
        "Стабильность — это маленькое действие, повторённое снова.",
        "Сделай проще. Начни раньше. Дыши.",
        "Ты не обязан быть в форме, чтобы сделать шаг.",
        "Две минуты старта решают больше, чем час размышлений.",
        "Сегодня достаточно: один честный маленький шаг.",
        "Ты не «ленивый». Ты уставший. Дай себе старт, а не приговор.",
        "Вернуться — тоже сила.",
        "Дисциплина — это забота о себе, а не наказание.",
    ]
    quotes_uk = [
        "Не потрібен ідеальний день. Потрібен перший крок.",
        "Стабільність — це маленька дія, повторена знову.",
        "Зроби простіше. Почни раніше. Дихай.",
        "Ти не мусиш бути в формі, щоб зробити крок.",
        "Дві хвилини старту вирішують більше, ніж година думок.",
        "Сьогодні досить: один чесний маленький крок.",
        "Ти не «лінивий». Ти втомився. Дай собі старт, а не вирок.",
        "Повернутися — теж сила.",
        "Дисципліна — це турбота про себе, а не покарання.",
    ]
    quotes_en = [
        "You don’t need a perfect day. You need a first step.",
        "Consistency is a small action repeated again.",
        "Make it simpler. Start earlier. Breathe.",
        "You don’t need to feel ready to take a step.",
        "Two minutes of starting beats an hour of thinking.",
        "Today is enough: one honest small step.",
        "You’re not lazy. You’re tired. Start gently.",
        "Coming back is strength.",
        "Discipline is care, not punishment.",
    ]

    q = random.choice(quotes_uk if lang == "uk" else quotes_en if lang == "en" else quotes_ru)
    await m.answer(q, reply_markup=_kb())


# ---------- BACK ----------
@router.message(F.text == BTN_BACK)
async def motivation_back(m: Message, state: FSMContext):
    await state.clear()
    # тут ты можешь дергать меню:home, но клаву убираем этой
    await m.answer("Главное меню — ниже.", reply_markup=_kb())
