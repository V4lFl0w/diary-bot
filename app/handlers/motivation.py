from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.journal import JournalEntry
from app.services.quotes_bank import generate_quote

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

router = Router(name="motivation")

# Кнопки — человеческие и понятные
BTN_SUPPORT = "💬 Поддержка (1 строка)"
BTN_JUMP = "⚡ Святой прыжок (15 минут)"
BTN_COMEBACK = "🔄 Вернуться (без вины)"
BTN_QUOTE = "🪶 Цитата (новая)"
BTN_BACK = "⬅️ Назад"

OPEN_TRIGGERS = (
    "🔥 Мотивация", "🔥 Мотивація", "🔥 Motivation",
    "Мотивация", "Мотивація", "Motivation",
)

class MotStates(StatesGroup):
    waiting_support = State()
    waiting_jump = State()
    waiting_comeback = State()


def _kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_JUMP)],
        [KeyboardButton(text=BTN_COMEBACK), KeyboardButton(text=BTN_QUOTE)],
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


@router.message(Command("cancel"))
async def motivation_cancel(m: Message, state: FSMContext):
    await state.clear()
    # Возвращаем меню мотивации
    await m.answer("Ок, отменил. Выбирай кнопку ниже 👇", reply_markup=_kb())


@router.message(F.text.in_(OPEN_TRIGGERS))
async def motivation_open(m: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    text = _t(
        lang,
        "🔥 Мотивация\n\n"
        "Я здесь, чтобы быстро вернуть тебе энергию и ясность.\n"
        "Не «правильно», не «идеально» — просто чтобы ты пошёл дальше.\n\n"
        "Выбери, что нужно прямо сейчас:",
        "🔥 Мотивація\n\n"
        "Я тут, щоб швидко повернути тобі енергію й ясність.\n"
        "Не «правильно», не «ідеально» — просто щоб ти рухався далі.\n\n"
        "Обери, що треба просто зараз:",
        "🔥 Motivation\n\n"
        "I’m here to quickly bring back your energy and clarity.\n"
        "Not perfect. Not polished. Just enough to move.\n\n"
        "Pick what you need right now:",
    )

    await m.answer(text, reply_markup=_kb())


@router.message(F.text == BTN_SUPPORT)
async def motivation_support_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_support)
    await m.answer(
        _t(
            lang,
            "💬 Поддержка\n\nНапиши ОДНУ строку: что сейчас внутри?\n(пример: «страшно», «злюсь», «пусто», «давит»)\n\nОтмена: /cancel",
            "💬 Підтримка\n\nНапиши ОДИН рядок: що зараз всередині?\n(приклад: «страшно», «злюсь», «порожньо», «тисне»)\n\nСкасування: /cancel",
            "💬 Support\n\nWrite ONE line: what’s inside right now?\n(example: “scared”, “angry”, “empty”, “pressure”)\n\nCancel: /cancel",
        )
    )


@router.message(MotStates.waiting_support, F.text)
async def motivation_support_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    txt = (m.text or "").strip()
    await state.clear()

    # Мягкий отклик + выбор следующего шага
    variants_ru = [
        f"Понял: «{txt}». Это нормально.\n\nДавай без героизма: выбери один шаг 👇\n1) ⚡ Святой прыжок (15 минут)\n2) 🔄 Вернуться (без вины)\n3) 🪶 Цитата (новая)",
        f"Слышу тебя: «{txt}».\n\nСейчас важно не доказывать, а поддержать себя.\nВыбери следующий шаг ниже 👇",
        f"Ок. «{txt}» — это сигнал, а не приговор.\n\nВыбери, что делаем дальше 👇",
    ]
    variants_uk = [
        f"Зрозумів: «{txt}». Це нормально.\n\nБез героїзму: обери один крок 👇\n1) ⚡ Святой прыжок (15 хв)\n2) 🔄 Повернутися (без провини)\n3) 🪶 Цитата (нова)",
        f"Чую тебе: «{txt}».\n\nЗараз важливо підтримати себе.\nОбери наступний крок нижче 👇",
        f"Ок. «{txt}» — це сигнал, не вирок.\n\nОбери, що робимо далі 👇",
    ]
    variants_en = [
        f"Got it: “{txt}”. That’s valid.\n\nNo hero mode: pick one step 👇\n1) ⚡ Holy jump (15 min)\n2) 🔄 Come back (no guilt)\n3) 🪶 New quote",
        f"I hear you: “{txt}”.\n\nRight now we support you — then we move.\nPick the next step 👇",
        f"Ok. “{txt}” is a signal, not a sentence.\n\nPick what we do next 👇",
    ]

    msg = random.choice(variants_uk if lang == "uk" else variants_en if lang == "en" else variants_ru)
    await m.answer(msg, reply_markup=_kb())


@router.message(F.text == BTN_JUMP)
async def motivation_jump_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_jump)
    await m.answer(
        _t(
            lang,
            "⚡ Святой прыжок (15 минут)\n\n"
            "Выбери ОДНУ мини-задачу на 15 минут и напиши её одной строкой.\n"
            "Пример: «делаю: 2 звонка» / «делаю: черновик 1 экрана»\n\n"
            "Отмена: /cancel",
            "⚡ Святой прыжок (15 хв)\n\n"
            "Обери ОДНУ міні-задачу на 15 хв і напиши одним рядком.\n"
            "Приклад: «роблю: 2 дзвінки» / «роблю: чернетку 1 екрану»\n\n"
            "Скасування: /cancel",
            "⚡ Holy jump (15 min)\n\n"
            "Pick ONE mini task for 15 minutes and write it in one line.\n"
            "Example: “doing: 2 calls” / “doing: draft 1 screen”\n\n"
            "Cancel: /cancel",
        )
    )


@router.message(MotStates.waiting_jump, F.text)
async def motivation_jump_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    task = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Принято ✅\n\nТвоя задача: «{task}»\n\n"
            "Сделай старт на 2 минуты прямо сейчас.\n"
            "Потом напиши: «Готово» — я закреплю смысл и дам следующий шаг.\n\n"
            "Если тяжко — нажми 💬 Поддержка.",
            f"Прийнято ✅\n\nТвоя задача: «{task}»\n\n"
            "Почни з 2 хвилин просто зараз.\n"
            "Потім напиши: «Готово» — я закріплю сенс і дам наступний крок.\n\n"
            "Якщо важко — натисни 💬 Підтримка.",
            f"Accepted ✅\n\nYour task: “{task}”\n\n"
            "Start with 2 minutes right now.\n"
            "Then reply: “Done” — I’ll lock the win and give the next step.\n\n"
            "If it’s heavy — tap 💬 Support.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text.casefold().in_({"готово", "done"}))
async def motivation_done(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Красавчик ✅\n"
            "Теперь самое важное: не потерять импульс.\n\n"
            "Выбери:\n"
            "1) ещё 15 минут (продолжаю)\n"
            "2) закрываю и фиксирую (стоп)\n\n"
            "Напиши: «ещё 15» или «стоп».",
            "Красень ✅\n"
            "Тепер головне: не втратити імпульс.\n\n"
            "Обери:\n"
            "1) ще 15 хв (продовжую)\n"
            "2) закриваю і фіксую (стоп)\n\n"
            "Напиши: «ще 15» або «стоп».",
            "Nice ✅\n"
            "Now the key: keep the impulse.\n\n"
            "Choose:\n"
            "1) another 15 min (continue)\n"
            "2) stop and lock it (stop)\n\n"
            "Reply: “another 15” or “stop”.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text.casefold().in_({"еще 15", "ещё 15", "another 15"}))
async def motivation_more_15(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Погнали 🔥\nПоставь таймер на 15 минут и просто делай.\nПосле — напиши «Готово».",
            "Погнали 🔥\nПостав таймер на 15 хв і просто роби.\nПісля — напиши «Готово».",
            "Let’s go 🔥\nSet a 15-min timer and just do it.\nAfter — reply “Done”.",
        )
    )


@router.message(F.text.casefold().in_({"стоп", "stop"}))
async def motivation_stop(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Зафиксировал ✅\n\nОдин честный шаг сделан.\nХочешь — возьми 🪶 Цитату (новая) для закрепления.",
            "Зафіксував ✅\n\nОдин чесний крок зроблено.\nХочеш — візьми 🪶 Цитату (нова) для закріплення.",
            "Locked ✅\n\nOne honest step is done.\nIf you want — grab 🪶 New quote to seal it.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text == BTN_COMEBACK)
async def motivation_comeback_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_comeback)
    await m.answer(
        _t(
            lang,
            "🔄 Вернуться (без вины)\n\n"
            "Одна строка: что сейчас важно вернуть под контроль?\n"
            "Пример: «сон», «деньги», «проект», «отношения», «здоровье»\n\n"
            "Отмена: /cancel",
            "🔄 Повернутися (без провини)\n\n"
            "Один рядок: що важливо повернути під контроль?\n"
            "Приклад: «сон», «гроші», «проєкт», «стосунки», «здоров’я»\n\n"
            "Скасування: /cancel",
            "🔄 Come back (no guilt)\n\n"
            "One line: what do you want back under control?\n"
            "Example: sleep, money, project, relationships, health\n\n"
            "Cancel: /cancel",
        )
    )


@router.message(MotStates.waiting_comeback, F.text)
async def motivation_comeback_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    focus = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Ок. Возвращаем «{focus}» ✅\n\n"
            "Сейчас — один микро-шаг на 2 минуты.\n"
            "Если хочешь, я дам толчок: нажми ⚡ Святой прыжок (15 минут).",
            f"Ок. Повертаємо «{focus}» ✅\n\n"
            "Зараз — один мікро-крок на 2 хвилини.\n"
            "Якщо хочеш, дам поштовх: натисни ⚡ Святой прыжок (15 хв).",
            f"Ok. We bring back “{focus}” ✅\n\n"
            "Now — one 2-minute micro step.\n"
            "If you want a push: tap ⚡ Holy jump (15 min).",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text == BTN_QUOTE)
async def motivation_quote(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    # “бесконечность”: каждый раз новая комбинация
    await m.answer(generate_quote(lang))


@router.message(F.text == BTN_BACK)
async def motivation_back(m: Message):
    # меню:home у тебя есть в другом модуле, тут не ломаем — просто текстом
    await m.answer("Ок. Возвращаю назад 👇", reply_markup=None)
