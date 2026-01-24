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

# mode: 0 off, 1 morning, 2 evening, 3 both
_MODE_CYCLE = [0, 1, 2, 3]
_MODE_LABEL = {
    0: "Выключено",
    1: "Утро",
    2: "Вечер",
    3: "Утро + Вечер",
}


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


def _current_mode(u: User) -> int:
    m = bool(getattr(u, "morning_auto", False))
    e = bool(getattr(u, "evening_auto", False))
    if m and e:
        return 3
    if m:
        return 1
    if e:
        return 2
    return 0


def _apply_mode(u: User, mode: int) -> None:
    # Важно: не трогаем время тут, только включатели
    u.morning_auto = mode in (1, 3)
    u.evening_auto = mode in (2, 3)


def _screen_text(u: User) -> str:
    mode = _current_mode(u)
    mt = _fmt_time(getattr(u, "morning_time", None))
    et = _fmt_time(getattr(u, "evening_time", None))

    # Никакого Markdown/HTML. Только чистый текст.
    return (
        "⚡️ Проактивность\n"
        "Режим, где я сам пишу тебе и держу в тонусе день.\n\n"
        f"Режим: {_MODE_LABEL.get(mode, '—')}\n"
        f"Утро: {mt}\n"
        f"Вечер: {et}\n\n"
        "Как это выглядит:\n"
        "• утром — 1 приоритет + 3 шага + старт на 2 минуты\n"
        "• вечером — 1 победа + 1 урок\n\n"
        "Настрой за 10 секунд: выбери режим → задай время."
    )


def proactive_kb(u: User):
    kb = InlineKeyboardBuilder()
    mode = _current_mode(u)

    kb.button(text=f"🧠 Режим: {_MODE_LABEL.get(mode)}", callback_data="proactive:mode")

    kb.button(text=f"🕘 Утро: {_fmt_time(getattr(u, 'morning_time', None))}", callback_data="proactive:time:morning")
    kb.button(text=f"🕘 Вечер: {_fmt_time(getattr(u, 'evening_time', None))}", callback_data="proactive:time:evening")

    kb.button(text="🧪 Пример утра", callback_data="proactive:sample:morning")
    kb.button(text="🧪 Пример вечера", callback_data="proactive:sample:evening")

    kb.button(text="⬅️ Назад", callback_data="menu:home")

    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()


async def _render_to_message(m: Message, u: User):
    await m.answer(_screen_text(u), reply_markup=proactive_kb(u), parse_mode=None)


async def _render_edit(msg: Message, u: User):
    # edit_text иногда падает (старое сообщение/удалено/и т.п.)
    try:
        await msg.edit_text(_screen_text(u), reply_markup=proactive_kb(u), parse_mode=None)
    except Exception:
        await msg.answer(_screen_text(u), reply_markup=proactive_kb(u), parse_mode=None)


@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    u = await _get_user(session, m.from_user.id)
    if not u:
        await m.answer("Нажми /start", parse_mode=None)
        return
    await _render_to_message(m, u)


# ВАЖНО: menus.py вызывает show_proactive_screen(m, session, lang)
async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru", *_a, **_k):
    if not message.from_user:
        return
    u = await _get_user(session, message.from_user.id)
    if not u:
        await message.answer("Нажми /start", parse_mode=None)
        return
    await _render_to_message(message, u)


@router.callback_query(F.data == "proactive:mode")
async def proactive_mode(cb: CallbackQuery, session: AsyncSession):
    if not cb.message:
        return
    u = await _get_user(session, cb.from_user.id)
    if not u:
        await cb.answer("Нажми /start")
        return

    cur = _current_mode(u)
    idx = _MODE_CYCLE.index(cur) if cur in _MODE_CYCLE else 0
    nxt = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
    _apply_mode(u, nxt)

    # чтобы не стрелял “сразу” после включения режима — сбрасываем last_sent_at
    if nxt in (1, 3):
        u.morning_last_sent_at = None
    if nxt in (2, 3):
        u.evening_last_sent_at = None

    await session.commit()
    await _render_edit(cb.message, u)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]
    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(part=part)

    await cb.message.answer(
        f"🕘 Введи время для {'утра' if part == 'morning' else 'вечера'} (HH:MM)\n"
        "Пример: 09:30\n"
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

    u = await _get_user(session, message.from_user.id)
    if not u:
        await state.clear()
        await message.answer("Нажми /start", parse_mode=None)
        return

    new_time = dtime(hh, mm)

    if part == "morning":
        u.morning_time = new_time
        u.morning_auto = True
        u.morning_last_sent_at = None
    else:
        u.evening_time = new_time
        u.evening_auto = True
        u.evening_last_sent_at = None

    await session.commit()
    await state.clear()

    await message.answer("✅ Сохранено.", parse_mode=None)
    await show_proactive_screen(message, session)


def _sample_morning() -> str:
    return (
        "☀️ Утро (пример)\n\n"
        "1) 1 приоритет на день\n"
        "2) 3 коротких шага\n"
        "3) старт на 2 минуты прямо сейчас\n\n"
        "Ответь одной строкой: какой приоритет?"
    )


def _sample_evening() -> str:
    return (
        "🌙 Вечер (пример)\n\n"
        "1) Как день? (1 фраза)\n"
        "2) 1 победа\n"
        "3) 1 урок\n\n"
        "Ответь: победа / урок"
    )


@router.callback_query(F.data.startswith("proactive:sample:"))
async def proactive_sample(cb: CallbackQuery):
    part = cb.data.split(":")[-1]
    if part == "morning":
        await cb.message.answer(_sample_morning(), parse_mode=None)
    else:
        await cb.message.answer(_sample_evening(), parse_mode=None)
    await cb.answer("Ок")


__all__ = ["router", "show_proactive_screen"]
