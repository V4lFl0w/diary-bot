from __future__ import annotations

import re
from datetime import time as dtime
from typing import Optional, Union

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


def _fmt_time(v: Union[None, dtime, str]) -> str:
    if v is None:
        return "—"
    if isinstance(v, dtime):
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
    morning = "✅" if bool(getattr(u, "morning_auto", False)) else "⛔️"
    evening = "✅" if bool(getattr(u, "evening_auto", False)) else "⛔️"
    mt = _fmt_time(getattr(u, "morning_time", None))
    et = _fmt_time(getattr(u, "evening_time", None))

    return (
        "⚡️ Проактивность\n\n"
        f"☀️ Утро: {morning}   🕘 {mt}\n"
        f"🌙 Вечер: {evening}   🕘 {et}\n\n"
        "Бот сам напишет тебе в выбранное время.\n"
        "Включи и задай часы — и всё."
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

    kb.button(text="⬅️ Назад", callback_data="menu:home")

    kb.adjust(1, 1, 1, 1, 1)
    return kb.as_markup()


async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru", *_a, **_k):
    if not message.from_user:
        return
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажми /start", parse_mode=None)
        return

    # Важно: не дублируем сообщения, если это повторный вход из меню — просто отправим 1 экран.
    await message.answer(
        _screen_text(user),
        reply_markup=proactive_kb(user),
        parse_mode=None,
    )


@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    await show_proactive_screen(m, session)


@router.callback_query(F.data == "proactive:open")
async def proactive_open(cb: CallbackQuery, session: AsyncSession):
    if not cb.message:
        return
    user = await _get_user(session, cb.from_user.id)
    if not user:
        await cb.answer("Нажми /start")
        return
    await cb.message.edit_text(_screen_text(user), reply_markup=proactive_kb(user), parse_mode=None)
    await cb.answer()


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
        await cb.message.edit_text(_screen_text(user), reply_markup=proactive_kb(user), parse_mode=None)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]
    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(part=part)

    await cb.message.answer(
        f"🕘 Введи время для {'утра' if part == 'morning' else 'вечера'}\n"
        "Формат: HH:MM\n"
        "Отмена: /cancel",
        parse_mode=None,
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

    new_time = dtime(hh, mm)

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

    await message.answer("✅ Сохранено.", parse_mode=None)
    await show_proactive_screen(message, session)


__all__ = ["router", "show_proactive_screen"]
