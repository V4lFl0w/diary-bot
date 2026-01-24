from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from typing import Optional, Union, Literal

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

Mode = Literal["off", "morning", "evening", "both"]
View = Literal["main", "how", "sample_morning", "sample_evening"]


class ProactiveStates(StatesGroup):
    waiting_time = State()


@dataclass
class ScreenRef:
    chat_id: int
    message_id: int


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


def _get_mode(u: User) -> Mode:
    m = bool(getattr(u, "morning_auto", False))
    e = bool(getattr(u, "evening_auto", False))
    if m and e:
        return "both"
    if m:
        return "morning"
    if e:
        return "evening"
    return "off"


def _set_mode(u: User, mode: Mode) -> None:
    if mode == "off":
        u.morning_auto = False
        u.evening_auto = False
    elif mode == "morning":
        u.morning_auto = True
        u.evening_auto = False
    elif mode == "evening":
        u.morning_auto = False
        u.evening_auto = True
    else:
        u.morning_auto = True
        u.evening_auto = True


def _cycle_mode(mode: Mode) -> Mode:
    return {
        "off": "morning",
        "morning": "evening",
        "evening": "both",
        "both": "off",
    }[mode]


def _mode_label(mode: Mode) -> str:
    return {
        "off": "⛔️ Выкл",
        "morning": "☀️ Утро",
        "evening": "🌙 Вечер",
        "both": "⚡️ Утро + Вечер",
    }[mode]


def _main_text(u: User) -> str:
    mode = _get_mode(u)
    mt = _fmt_time(getattr(u, "morning_time", None))
    et = _fmt_time(getattr(u, "evening_time", None))

    return (
        "⚡️ **Проактивность**\n"
        "Режим, где я *сам* пишу тебе и держу в тонусе день.\n\n"
        f"**Режим:** {_mode_label(mode)}\n"
        f"**Время:** ☀️ {mt}   •   🌙 {et}\n\n"
        "Сделаем это *лёгким*, но стабильным.\n"
        "Один клик — выбрать режим. Два клика — настроить время."
    )


def _how_text() -> str:
    return (
        "ℹ️ **Как работает**\n\n"
        "☀️ Утро — короткий старт:\n"
        "• 1 приоритет\n"
        "• 3 шага\n"
        "• 2 минуты начать\n\n"
        "🌙 Вечер — закрыть день:\n"
        "• 1 победа\n"
        "• 1 урок\n\n"
        "Важно:\n"
        "• если поменял время — не “стреляет сразу”, начнёт с *следующего дня*\n"
        "• уведомления приходят *в окно после времени*, без ночного спама"
    )


def _sample_morning_text() -> str:
    return (
        "☀️ **Утренний импульс (пример)**\n\n"
        "1) 🎯 1 приоритет (что даст максимум)\n"
        "2) ✅ 3 шага (самые короткие)\n"
        "3) ⚡️ старт на 2 минуты\n\n"
        "Ответь одной строкой: **какой приоритет?**\n"
        "(/cancel — отмена)"
    )


def _sample_evening_text() -> str:
    return (
        "🌙 **Вечерний чек-ин (пример)**\n\n"
        "1) 🧠 как день (1 фраза)\n"
        "2) 🏆 1 победа\n"
        "3) 🧩 1 урок\n\n"
        "Ответь одним сообщением в формате:\n"
        "победа: ...\n"
        "урок: ...\n"
        "(/cancel — отмена)"
    )


def _kb(u: User, view: View = "main"):
    kb = InlineKeyboardBuilder()
    mode = _get_mode(u)

    if view == "main":
        kb.button(text=f"🧠 Режим: {_mode_label(mode)}", callback_data="proactive:mode:cycle")
        kb.button(text=f"🕘 Утро: {_fmt_time(u.morning_time)}", callback_data="proactive:time:morning")
        kb.button(text=f"🕘 Вечер: {_fmt_time(u.evening_time)}", callback_data="proactive:time:evening")

        kb.button(text="🧪 Пример утра", callback_data="proactive:view:sample_morning")
        kb.button(text="🧪 Пример вечера", callback_data="proactive:view:sample_evening")

        kb.button(text="ℹ️ Как работает", callback_data="proactive:view:how")
        kb.button(text="⬅️ Назад", callback_data="menu:home")

        kb.adjust(1, 2, 2, 2)

    else:
        # secondary views
        kb.button(text="⬅️ Назад к настройкам", callback_data="proactive:view:main")
        kb.adjust(1)

    return kb.as_markup()


async def _render(
    *,
    message: Optional[Message] = None,
    cb_message: Optional[Message] = None,
    session: AsyncSession,
    view: View = "main",
) -> None:
    # source message to edit/send
    src = cb_message or message
    if not src or not src.from_user:
        return

    user = await _get_user(session, src.from_user.id)
    if not user:
        if message:
            await message.answer("Нажми /start", parse_mode=None)
        return

    if view == "how":
        text = _how_text()
    elif view == "sample_morning":
        text = _sample_morning_text()
    elif view == "sample_evening":
        text = _sample_evening_text()
    else:
        text = _main_text(user)

    markup = _kb(user, view=view)

    # If coming from callback — always edit same message for "clean UI"
    if cb_message:
        await cb_message.edit_text(text, reply_markup=markup, parse_mode=None)
        return

    # If command/menu message — send one screen
    await message.answer(text, reply_markup=markup, parse_mode=None)


async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru", *_a, **_k):
    await _render(message=message, session=session, view="main")


@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    await show_proactive_screen(m, session)


# ========= VIEWS =========

@router.callback_query(F.data.startswith("proactive:view:"))
async def proactive_view(cb: CallbackQuery, session: AsyncSession):
    view = cb.data.split(":")[-1]
    mapping: dict[str, View] = {
        "main": "main",
        "how": "how",
        "sample_morning": "sample_morning",
        "sample_evening": "sample_evening",
    }
    v = mapping.get(view, "main")
    if cb.message:
        await _render(cb_message=cb.message, session=session, view=v)
    await cb.answer()


# ========= MODE =========

@router.callback_query(F.data == "proactive:mode:cycle")
async def proactive_mode_cycle(cb: CallbackQuery, session: AsyncSession):
    if not cb.message or not cb.from_user:
        return
    user = await _get_user(session, cb.from_user.id)
    if not user:
        await cb.answer("Нажми /start")
        return

    current = _get_mode(user)
    new_mode = _cycle_mode(current)
    _set_mode(user, new_mode)

    await session.commit()
    await _render(cb_message=cb.message, session=session, view="main")
    await cb.answer("Готово")


# ========= SET TIME =========

@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]
    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(
        part=part,
        screen_chat_id=cb.message.chat.id if cb.message else None,
        screen_message_id=cb.message.message_id if cb.message else None,
    )

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

    user = await _get_user(session, message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Нажми /start", parse_mode=None)
        return

    new_time = dtime(hh, mm)
    now_utc = datetime.now(timezone.utc)

    # анти-“поставил время которое уже прошло → улетело сразу”
    if part == "morning":
        user.morning_time = new_time
        user.morning_auto = True
        user.morning_last_sent_at = now_utc
    else:
        user.evening_time = new_time
        user.evening_auto = True
        user.evening_last_sent_at = now_utc

    await session.commit()
    await state.clear()

    # обновляем главный экран (если есть reference)
    chat_id = data.get("screen_chat_id")
    msg_id = data.get("screen_message_id")
    if chat_id and msg_id:
        try:
            # редактируем старый экран
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=_main_text(user),
                reply_markup=_kb(user, view="main"),
                parse_mode=None,
            )
            await message.answer("✅ Сохранено.", parse_mode=None)
            return
        except Exception:
            pass

    await message.answer("✅ Сохранено.", parse_mode=None)
    await show_proactive_screen(message, session)


__all__ = ["router", "show_proactive_screen"]
