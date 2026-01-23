from __future__ import annotations

import re
from datetime import time
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

CB_TOGGLE_MORNING = "proactive:toggle:morning"
CB_TOGGLE_EVENING = "proactive:toggle:evening"
CB_TIME_MORNING = "proactive:time:morning"
CB_TIME_EVENING = "proactive:time:evening"
CB_TEST_MORNING = "proactive:test:morning"
CB_TEST_EVENING = "proactive:test:evening"
CB_SCREEN = "proactive:screen"

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
            h = int(parts[0])
            m = int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        return s
    return str(v)


def _screen_text(u: User) -> str:
    return (
        "⚡️ *Проактивность*\n\n"
        "Режим, где бот *первый* помогает держать курс:\n"
        "• ☀️ утром — фокус и старт\n"
        "• 🌙 вечером — закрыть день и вынести урок\n\n"
        "_Поставь время — и бот будет писать сам._\n"
        "Пробники ниже — просто пример, ничего не сохраняют."
    )


def proactive_kb(u: User):
    kb = InlineKeyboardBuilder()

    kb.button(
        text=f"☀️ Утро: {'✅ Вкл' if u.morning_auto else '⛔️ Выкл'}",
        callback_data=CB_TOGGLE_MORNING,
    )
    kb.button(
        text=f"🕘 Время утра: {_fmt_time(getattr(u, 'morning_time', None))}",
        callback_data=CB_TIME_MORNING,
    )

    kb.button(
        text=f"🌙 Вечер: {'✅ Вкл' if u.evening_auto else '⛔️ Выкл'}",
        callback_data=CB_TOGGLE_EVENING,
    )
    kb.button(
        text=f"🕘 Время вечера: {_fmt_time(getattr(u, 'evening_time', None))}",
        callback_data=CB_TIME_EVENING,
    )

    kb.button(text="🧪 Пробник утра", callback_data=CB_TEST_MORNING)
    kb.button(text="🧪 Пробник вечера", callback_data=CB_TEST_EVENING)

    kb.button(text="⬅️ Назад", callback_data="menu:home")

    kb.adjust(1, 1, 1, 1, 2, 1)
    return kb.as_markup()


def _preview_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад к настройкам", callback_data=CB_SCREEN)
    kb.adjust(1)
    return kb


def _briefing_text() -> str:
    return (
        "☀️ *Утренний импульс*\n"
        "_Чтобы день не съел тебя._\n\n"
        "1) 🎯 1 приоритет (что даст максимум)\n"
        "2) ✅ 3 шага (самые короткие действия)\n"
        "3) ⚡️ старт на 2 минуты — начни прямо сейчас\n\n"
        "Ответь одной строкой: *какой приоритет?*"
    )


def _checkin_text() -> str:
    return (
        "🌙 *Вечерний чек-ин*\n"
        "_Закрываем день без хаоса._\n\n"
        "1) 🧠 как день (1 фраза)\n"
        "2) 🏆 1 победа\n"
        "3) 🧩 1 урок\n\n"
        "Ответь: *победа / урок*"
    )


async def _render_screen(
    target: Union[Message, CallbackQuery],
    session: AsyncSession,
    lang: str = "ru",
):
    # lang оставлен для совместимости (меню уже передаёт lang)
    if isinstance(target, CallbackQuery):
        from_user = target.from_user
    else:
        from_user = target.from_user

    if not from_user:
        return

    user = await _get_user(session, from_user.id)
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("Нажми /start")
        else:
            await target.answer("Нажми /start", parse_mode=None)
        return

    text = _screen_text(user)
    markup = proactive_kb(user)

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup, parse_mode="Markdown")


# ========= ENTRY =========

@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    await _render_screen(m, session)


async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru"):
    # вызывается из menus.py
    await _render_screen(message, session, lang=lang)


@router.callback_query(F.data == CB_SCREEN)
async def proactive_screen(cb: CallbackQuery, session: AsyncSession):
    await _render_screen(cb, session)


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
    await _render_screen(cb, session)


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
    await _render_screen(message, session)


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
    elif part == "evening":
        user.evening_time = new_time
        user.evening_auto = True
        user.evening_last_sent_at = None
    else:
        await message.answer("❌ Что-то пошло не так. Открой меню ещё раз.", parse_mode=None)
        await state.clear()
        return

    await session.commit()
    await state.clear()
    await _render_screen(message, session)


# ========= TEST (preview without spam) =========

@router.callback_query(F.data.startswith("proactive:test:"))
async def proactive_test(cb: CallbackQuery):
    part = cb.data.split(":")[-1]
    text = _briefing_text() if part == "morning" else _checkin_text()

    if cb.message:
        await cb.message.edit_text(text, reply_markup=_preview_kb().as_markup(), parse_mode="Markdown")
    await cb.answer()
