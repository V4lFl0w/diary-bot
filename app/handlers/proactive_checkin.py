from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.proactive_logger import log_proactive_entry

router = Router(name="proactive_checkin")


class ProactiveCheckinFSM(StatesGroup):
    waiting_a1 = State()
    waiting_a2 = State()
    waiting_a3 = State()


def _normalize_lang(code: str | None) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")): return "uk"
    if s.startswith("en"): return "en"
    return "ru"


def _flow(kind: str, lang: str):
    if kind == "morning":
        if lang == "uk":
            return [("main", "1) Для чого це мені сьогодні? (1 фраза)"), ("steps", "2) 3 маленькі кроки"), ("start", "3) О котрій стартую?")]
        elif lang == "en":
            return [("main", "1) What is my main goal today?"), ("steps", "2) 3 tiny steps"), ("start", "3) When do I start?")]
        return [("main", "1) Для чего это мне сегодня? (1 фраза)"), ("steps", "2) 3 маленьких шага"), ("start", "3) Во сколько стартую?")]
    else:
        if lang == "uk":
            return [("day", "1) Як пройшов день? (1 фраза)"), ("win", "2) 1 перемога/результат"), ("lesson", "3) 1 висновок на завтра")]
        elif lang == "en":
            return [("day", "1) How was your day? (1 phrase)"), ("win", "2) 1 win/result"), ("lesson", "3) 1 lesson for tomorrow")]
        return [("day", "1) Как прошёл день? (1 фраза)"), ("win", "2) 1 победа/результат за день"), ("lesson", "3) 1 вывод/урок на завтра")]


@router.callback_query(F.data.startswith("proactive:checkin:"))
async def proactive_checkin_start(cb: CallbackQuery, session: AsyncSession, state: FSMContext):
    tg = cb.from_user
    u = (await session.execute(select(User).where(User.tg_id == tg.id))).scalar_one_or_none()
    if not u:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    lang = _normalize_lang(getattr(u, "lang", None) or getattr(u, "locale", None) or tg.language_code)
    kind = cb.data.split(":")[-1]  # morning/evening
    
    await state.clear()
    await state.update_data(kind=kind, answers={}, lang=lang)

    k, q = _flow(kind, lang)[0]
    await state.set_state(ProactiveCheckinFSM.waiting_a1)
    
    header = "⚡ Проактивность • Утро"
    if lang == "uk": header = f"⚡ Проактивність • {'Ранок' if kind == 'morning' else 'Вечір'}"
    elif lang == "en": header = f"⚡ Proactivity • {'Morning' if kind == 'morning' else 'Evening'}"
    else: header = f"⚡ Проактивность • {'Утро' if kind == 'morning' else 'Вечер'}"
        
    await cb.message.answer(f"{header}\n\n{q}")
    await cb.answer()


@router.message(ProactiveCheckinFSM.waiting_a1, F.text)
async def proactive_a1(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    lang = data.get("lang", "ru")
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind, lang)[0]
    answers[k] = m.text.strip()

    await state.update_data(answers=answers)
    k2, q2 = _flow(kind, lang)[1]
    await state.set_state(ProactiveCheckinFSM.waiting_a2)
    await m.answer(q2)


@router.message(ProactiveCheckinFSM.waiting_a2, F.text)
async def proactive_a2(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    lang = data.get("lang", "ru")
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind, lang)[1]
    answers[k] = m.text.strip()

    await state.update_data(answers=answers)
    k3, q3 = _flow(kind, lang)[2]
    await state.set_state(ProactiveCheckinFSM.waiting_a3)
    await m.answer(q3)


@router.message(ProactiveCheckinFSM.waiting_a3, F.text)
async def proactive_finish(m: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    lang = data.get("lang", "ru")
    answers: dict[str, Any] = data.get("answers", {})

    k, _ = _flow(kind, lang)[2]
    answers[k] = m.text.strip()

    tg = m.from_user
    u = (await session.execute(select(User).where(User.tg_id == tg.id))).scalar_one_or_none()
    
    if not u:
        await m.answer("User not found" if lang == "en" else ("Користувача не знайдено" if lang == "uk" else "Пользователь не найден"))
        await state.clear()
        return

    await log_proactive_entry(session, u, kind, answers)
    await state.clear()

    msg = "✅ Готово. Ответ сохранён. Стрик обновлён."
    if lang == "uk": msg = "✅ Готово. Відповідь збережено. Серію оновлено."
    elif lang == "en": msg = "✅ Done. Answer saved. Streak updated."
    await m.answer(msg)