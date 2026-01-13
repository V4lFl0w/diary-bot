from __future__ import annotations

import re
from datetime import time
from typing import Any

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.keyboards import get_main_kb


router = Router()

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class ProactiveStates(StatesGroup):
    waiting_time = State()


async def _get_user(session: AsyncSession, tg_id: int) -> User:
    return (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one()


def _fmt_time(t: time | None) -> str:
    if not t:
        return "—"
    return f"{t.hour:02d}:{t.minute:02d}"


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

    kb.button(text="⬅️ Назад", callback_data="menu:home")
    kb.adjust(1)
    return kb.as_markup()


# ================= ENTRY =================

async def show_proactive_screen(
    message: Message,
    session: AsyncSession,
    lang: str = "ru",
):
    user = await _get_user(session, message.from_user.id)

    # 3. Inline экран
    await message.answer(
        "⚡️ **Проактивность**\n\n"
        "Настрой утренний briefing и вечерний чек-ин.\n"
        "Важно: если выключено — бот сам не пишет.",
        reply_markup=proactive_kb(user),
        parse_mode="Markdown",
    )


# ================= TOGGLE =================

@router.callback_query(F.data.startswith("proactive:toggle:"))
async def proactive_toggle(cb: CallbackQuery, session: AsyncSession):
    user = await _get_user(session, cb.from_user.id)
    part = cb.data.split(":")[-1]

    if part == "morning":
        user.morning_auto = not user.morning_auto
    elif part == "evening":
        user.evening_auto = not user.evening_auto

    await session.commit()
    await cb.message.edit_reply_markup(reply_markup=proactive_kb(user))
    await cb.answer("Готово")


# ================= SET TIME =================

@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]

    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(part=part)

    await cb.message.answer(
        f"🕘 Введи время для **{'утра' if part == 'morning' else 'вечера'}**\n"
        "Формат: HH:MM\n\n"
        "Отмена: /cancel",
        parse_mode="Markdown",
    )
    await cb.answer()

@router.message(ProactiveStates.waiting_time, Command("cancel"))
async def proactive_cancel(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    await show_proactive_screen(message, session)


@router.message(ProactiveStates.waiting_time)
async def proactive_time_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
):
    txt = (message.text or "").strip()

    m = _TIME_RE.match(txt)
    if not m:
        await message.answer("❌ Формат HH:MM, пример 09:30")
        return

    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        await message.answer("❌ Время вне диапазона 00:00–23:59")
        return

    data = await state.get_data()
    part = data["part"]

    user = await _get_user(session, message.from_user.id)
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

