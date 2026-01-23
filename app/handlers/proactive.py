from __future__ import annotations

import re
from datetime import time
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

router = Router(name="proactive")

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class ProactiveStates(StatesGroup):
    waiting_time = State()


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _fmt_time(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, time):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return "—"
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            h = int(parts[0]); m = int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        return s
    return str(v)


def _screen_text(u: User) -> str:
    # ХУК + понятность + смысл
    return (
        "⚡️ Проактивность\n\n"
        "Это режим, где бот *первый* помогает тебе держать курс:\n"
        "• утром — фокус и маленький старт\n"
        "• вечером — закрыть день и вынести урок\n\n"
        "Сделаем так, чтобы это было *без напряга*, но стабильно.\n"
        "Выбери время и включи."
    )


def proactive_kb(u: User):
    kb = InlineKeyboardBuilder()

    kb.button(
        text=f"☀️ Утро: {'✅ Вкл' if u.morning_auto else '⛔️ Выкл'}",
        callback_data="proactive:toggle:morning",
    )
    kb.button(
        text=f"🕘 Время утра: {_fmt_time(u.morning_time)}",
        callback_data="proactive:time:morning",
    )

    kb.button(
        text=f"🌙 Вечер: {'✅ Вкл' if u.evening_auto else '⛔️ Выкл'}",
        callback_data="proactive:toggle:evening",
    )
    kb.button(
        text=f"🕘 Время вечера: {_fmt_time(u.evening_time)}",
        callback_data="proactive:time:evening",
    )

    kb.button(text="🧪 Пробник утра", callback_data="proactive:test:morning")
    kb.button(text="🧪 Пробник вечера", callback_data="proactive:test:evening")

    kb.button(text="💎 Зачем это / Pro", callback_data="proactive:about")
    kb.button(text="⬅️ Назад", callback_data="menu:home")

    kb.adjust(1, 1, 1, 1, 2, 2)
    return kb.as_markup()


def _briefing_text() -> str:
    return (
        "☀️ Утренний импульс\n"
        "Чтобы день не съел тебя.\n\n"
        "1) 🎯 1 приоритет\n"
        "2) ✅ 3 шага\n"
        "3) ⚡️ старт на 2 минуты\n\n"
        "Ответь одной строкой: какой приоритет?"
    )


def _checkin_text() -> str:
    return (
        "🌙 Вечерний чек-ин\n"
        "Закрываем день без хаоса.\n\n"
        "1) 🧠 как день (1 фраза)\n"
        "2) 🏆 1 победа\n"
        "3) 🧩 1 урок\n\n"
        "Ответь: победа / урок"
    )


def _about_text() -> str:
    # “воронка” — смысл + мягкий апселл
    return (
        "💎 Зачем это\n\n"
        "Проактивность — это не мотивация. Это *система*:\n"
        "• утром ты не думаешь “с чего начать”\n"
        "• вечером не проваливаешься в “день прошёл впустую”\n\n"
        "Pro-идея (если решишь монетизировать):\n"
        "• персональные шаблоны под цели\n"
        "• статистика ответов (сколько дней подряд)\n"
        "• “антислив” — если пропустил 2 дня, бот мягко возвращает\n"
    )


# ========= ENTRY =========

@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    await show_proactive_screen(m, session)


async def show_proactive_screen(message: Message, session: AsyncSession):
    if not message.from_user:
        return
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажми /start", parse_mode=None)
        return

    await message.answer(
        _screen_text(user),
        reply_markup=proactive_kb(user),
        parse_mode="Markdown",
    )


# ========= TOGGLE =========

@router.callback_query(F.data.startswith("proactive:toggle:"))
async def proactive_toggle(cb: CallbackQuery, session: AsyncSession):
    user = await _get_user(session, cb.from_user.id)
    if not user:
        await cb.answer("Нажми /start")
        return

    part = cb.data.split(":")[-1]
    if part == "morning":
        user.morning_auto = not bool(user.morning_auto)
    elif part == "evening":
        user.evening_auto = not bool(user.evening_auto)

    await session.commit()

    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=proactive_kb(user))
    await cb.answer("Готово")


# ========= SET TIME =========

@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]
    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(part=part)

    await cb.message.answer(
        f"🕘 Введи время для *{'утра' if part == 'morning' else 'вечера'}*\n"
        "Формат: HH:MM\n"
        "Отмена: /cancel",
        parse_mode="Markdown",
    )
    await cb.answer()


@router.message(ProactiveStates.waiting_time, Command("cancel"))
async def proactive_cancel(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    await show_proactive_screen(message, session)


@router.message(ProactiveStates.waiting_time)
async def proactive_time_input(message: Message, session: AsyncSession, state: FSMContext):
    if not message.from_user:
        return

    txt = (message.text or "").strip()
    m = _TIME_RE.match(txt)
    if not m:
        await message.answer("❌ Формат HH:MM, пример 09:30", parse_mode=None)
        return

    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        await message.answer("❌ Время вне диапазона 00:00–23:59", parse_mode=None)
        return

    data = await state.get_data()
    part = data.get("part")

    user = await _get_user(session, message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Нажми /start", parse_mode=None)
        return

    new_time = time(hh, mm)

    if part == "morning":
        user.morning_time = new_time
        user.morning_auto = True
        user.morning_last_sent_at = None
    else:
        user.evening_time = new_time
        user.evening_auto = True
        user.evening_last_sent_at = None

    await session.commit()
    await state.clear()
    await show_proactive_screen(message, session)


# ========= TEST / ABOUT =========

@router.callback_query(F.data.startswith("proactive:test:"))
async def proactive_test(cb: CallbackQuery):
    part = cb.data.split(":")[-1]
    if part == "morning":
        await cb.message.answer(_briefing_text(), parse_mode=None)
    else:
        await cb.message.answer(_checkin_text(), parse_mode=None)
    await cb.answer("Ок")


@router.callback_query(F.data == "proactive:about")
async def proactive_about(cb: CallbackQuery):
    await cb.message.answer(_about_text(), parse_mode=None)
    await cb.answer()
