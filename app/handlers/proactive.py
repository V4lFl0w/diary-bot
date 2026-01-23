from __future__ import annotations

import re
from datetime import datetime, time as dtime, timezone
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
    waiting_probe = State()


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
    m_on = bool(getattr(u, "morning_auto", False))
    e_on = bool(getattr(u, "evening_auto", False))
    mt = _fmt_time(getattr(u, "morning_time", None))
    et = _fmt_time(getattr(u, "evening_time", None))

    m_mark = "✅" if m_on else "⛔️"
    e_mark = "✅" if e_on else "⛔️"

    return (
        "⚡️ Проактивность\n\n"
        f"☀️ Утро: {m_mark}   🕘 {mt}\n"
        f"🌙 Вечер: {e_mark}   🕘 {et}\n\n"
        "Я сам напишу тебе в выбранное время.\n"
        "Нажми тумблер → задай время → готово."
    )


def proactive_kb(u: User):
    kb = InlineKeyboardBuilder()

    kb.button(
        text=f"☀️ Утро: {'✅ Вкл' if bool(u.morning_auto) else '⛔️ Выкл'}",
        callback_data="proactive:toggle:morning",
    )
    kb.button(
        text=f"🕘 Время утра: {_fmt_time(u.morning_time)}",
        callback_data="proactive:time:morning",
    )

    kb.button(
        text=f"🌙 Вечер: {'✅ Вкл' if bool(u.evening_auto) else '⛔️ Выкл'}",
        callback_data="proactive:toggle:evening",
    )
    kb.button(
        text=f"🕘 Время вечера: {_fmt_time(u.evening_time)}",
        callback_data="proactive:time:evening",
    )

    kb.button(text="🧪 Пробник утра", callback_data="proactive:test:morning")
    kb.button(text="🧪 Пробник вечера", callback_data="proactive:test:evening")

    kb.button(text="ℹ️ Как работает", callback_data="proactive:how")
    kb.button(text="⬅️ Назад", callback_data="menu:home")

    kb.adjust(1, 1, 1, 1, 2, 2)
    return kb.as_markup()


def _briefing_probe_text() -> str:
    return (
        "☀️ Утренний импульс\n\n"
        "1) 🎯 1 приоритет (что даст максимум)\n"
        "2) ✅ 3 шага (самые короткие)\n"
        "3) ⚡️ старт на 2 минуты\n\n"
        "Ответь одной строкой: *какой приоритет?*"
    )


def _checkin_probe_text() -> str:
    return (
        "🌙 Вечерний чек-ин\n\n"
        "1) 🧠 как день (1 фраза)\n"
        "2) 🏆 1 победа\n"
        "3) 🧩 1 урок\n\n"
        "Ответь одним сообщением в формате:\n"
        "победа: ...\n"
        "урок: ..."
    )


def _how_text() -> str:
    return (
        "ℹ️ Как это работает\n\n"
        "• Утром — короткий фокус: 1 приоритет → 3 шага → старт 2 минуты\n"
        "• Вечером — закрываем день: победа + урок\n\n"
        "Важно:\n"
        "• если время поменял — бот не стреляет “сразу”, а начнёт со следующего дня\n"
        "• уведомления приходят в окно после времени (без ночного спама)"
    )


async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru", *_a, **_k):
    if not message.from_user:
        return
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Нажми /start", parse_mode=None)
        return

    await message.answer(
        _screen_text(user),
        reply_markup=proactive_kb(user),
        parse_mode=None,
    )


@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    await show_proactive_screen(m, session)


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
    now_utc = datetime.now(timezone.utc)

    # ВАЖНО: анти-“поставил время которое уже прошло → мгновенно прислал”
    if part == "morning":
        user.morning_time = new_time
        user.morning_auto = True
        user.morning_last_sent_at = now_utc  # блокируем отправку “сразу сегодня”
    else:
        user.evening_time = new_time
        user.evening_auto = True
        user.evening_last_sent_at = now_utc  # блокируем отправку “сразу сегодня”

    await session.commit()
    await state.clear()
    await show_proactive_screen(message, session)


# ========= PROBES =========

@router.callback_query(F.data.startswith("proactive:test:"))
async def proactive_test(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]

    await state.set_state(ProactiveStates.waiting_probe)
    await state.update_data(part=part)

    if part == "morning":
        await cb.message.answer(_briefing_probe_text(), parse_mode="Markdown")
    else:
        await cb.message.answer(_checkin_probe_text(), parse_mode=None)

    await cb.answer("Ок")


@router.message(ProactiveStates.waiting_probe, Command("cancel"))
async def proactive_probe_cancel(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    await show_proactive_screen(message, session)


async def _try_log_probe(session: AsyncSession, user: User, kind: str, text: str) -> None:
    # Логируем в Event, если модель есть. Если нет — молча работаем дальше.
    try:
        from app.models.event import Event  # type: ignore
    except Exception:
        return

    try:
        payload = {"text": text.strip()[:2000]}
        ev = Event(user_id=user.id, type=f"proactive:{kind}", payload=payload)  # type: ignore
        session.add(ev)
        await session.commit()
    except Exception:
        # не валим UX из-за аналитики
        return


@router.message(ProactiveStates.waiting_probe)
async def proactive_probe_input(message: Message, session: AsyncSession, state: FSMContext):
    if not message.from_user:
        return

    user = await _get_user(session, message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Нажми /start", parse_mode=None)
        return

    data = await state.get_data()
    part = data.get("part") or "unknown"
    txt = (message.text or "").strip()

    if not txt:
        await message.answer("Напиши текстом 🙂 (или /cancel)", parse_mode=None)
        return

    await _try_log_probe(session, user, part, txt)

    if part == "morning":
        await message.answer("✅ Принято. Первый шаг — начни с 2 минут прямо сейчас.", parse_mode=None)
    else:
        await message.answer("✅ Принято. День закрыт: победа + урок зафиксированы.", parse_mode=None)

    await state.clear()
    await show_proactive_screen(message, session)


# ========= HOW =========

@router.callback_query(F.data == "proactive:how")
async def proactive_how(cb: CallbackQuery):
    await cb.message.answer(_how_text(), parse_mode=None)
    await cb.answer()


__all__ = ["router", "show_proactive_screen"]
