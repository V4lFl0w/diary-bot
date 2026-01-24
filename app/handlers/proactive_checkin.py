from __future__ import annotations

from typing import Any

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.services.proactive_logger import log_proactive_entry

router = Router(name="proactive_checkin")


class ProactiveCheckinFSM(StatesGroup):
    waiting_a1 = State()
    waiting_a2 = State()
    waiting_a3 = State()


_MORNING = [
    ("main",  "1) Для чего это мне сегодня? (1 фраза)"),
    ("steps", "2) 3 маленьких шага (можно через запятую)"),
    ("start", "3) Во сколько стартую? (пример: 10:30)"),
]

_EVENING = [
    ("day",    "1) Как прошёл день? (1 фраза)"),
    ("win",    "2) 1 победа/результат за день"),
    ("lesson", "3) 1 вывод/урок на завтра"),
]


def _flow(kind: str):
    return _MORNING if kind == "morning" else _EVENING


@router.callback_query(F.data.startswith("proactive:checkin:"))
async def proactive_checkin_start(cb: CallbackQuery, session: AsyncSession, state: FSMContext):
    tg = cb.from_user
    # user берём так же, как у тебя в других хендлерах (если есть util — замени)
    u = (await session.execute(select(User).where(User.tg_id == tg.id))).scalar_one_or_none()# если у тебя user.id != tg.id — скажешь, поправим точечно
    if not u:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    kind = cb.data.split(":")[-1]  # morning/evening
    await state.clear()
    await state.update_data(kind=kind, answers={})

    k, q = _flow(kind)[0]
    await state.set_state(ProactiveCheckinFSM.waiting_a1)
    await cb.message.answer(f"⚡ Проактивность • {'Утро' if kind=='morning' else 'Вечер'}\n\n{q}")
    await cb.answer("Ок")


@router.message(ProactiveCheckinFSM.waiting_a1, F.text)
async def proactive_a1(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind)[0]
    answers[k] = m.text.strip()

    await state.update_data(answers=answers)
    k2, q2 = _flow(kind)[1]
    await state.set_state(ProactiveCheckinFSM.waiting_a2)
    await m.answer(q2)


@router.message(ProactiveCheckinFSM.waiting_a2, F.text)
async def proactive_a2(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind)[1]
    answers[k] = m.text.strip()

    await state.update_data(answers=answers)
    k3, q3 = _flow(kind)[2]
    await state.set_state(ProactiveCheckinFSM.waiting_a3)
    await m.answer(q3)


@router.message(ProactiveCheckinFSM.waiting_a3, F.text)
async def proactive_finish(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind)[2]
    answers[k] = m.text.strip()

    tg = m.from_user
    u = (await session.execute(select(User).where(User.tg_id == tg.id))).scalar_one_or_none()# если маппинг другой — поправим
    if not u:
        await m.answer("Пользователь не найден")
        await state.clear()
        return

    await log_proactive_entry(session, u, kind, answers)
    await state.clear()

    await m.answer("✅ Готово. Ответ сохранён. Стрик обновлён.")
