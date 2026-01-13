# app/handlers/erase.py
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router
from aiogram.filters import StateFilter, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.journal import JournalEntry
from app.models.reminder import Reminder
from app.models.bug_report import BugReport

router = Router()


class EraseFSM(StatesGroup):
    confirm = State()


_L10N = {
    "prompt": {
        "ru": "❗️Удалить все твои записи/репорты/напоминания?\n\nНапиши: <code>{phrase}</code>\nИли /cancel — чтобы отменить.\n\n(Код действует 2 минуты.)",
        "uk": "❗️Видалити всі твої записи/репорти/нагадування?\n\nНапиши: <code>{phrase}</code>\nАбо /cancel — щоб скасувати.\n\n(Код діє 2 хвилини.)",
        "en": "❗️Delete all your entries/reports/reminders?\n\nType: <code>{phrase}</code>\nOr /cancel to abort.\n\n(The code is valid for 2 minutes.)",
    },
    "canceled": {
        "ru": "Отменил. Ничего не удалял.",
        "uk": "Скасував. Нічого не видаляв.",
        "en": "Canceled. Nothing was deleted.",
    },
    "need_start": {
        "ru": "Нажми /start",
        "uk": "Натисни /start",
        "en": "Press /start",
    },
    "expired": {
        "ru": "⏱ Срок действия кода истёк. Вызови /erase снова.",
        "uk": "⏱ Термін дії коду минув. Виклич /erase ще раз.",
        "en": "⏱ The code expired. Run /erase again.",
    },
    "wrong": {
        "ru": "Нужно написать ровно: <code>{phrase}</code> — или /cancel",
        "uk": "Потрібно написати рівно: <code>{phrase}</code> — або /cancel",
        "en": "You must type exactly: <code>{phrase}</code> — or /cancel",
    },
    "done": {
        "ru": "✅ Готово. Все твои данные удалены.\n(Аккаунт оставил, можно продолжать пользоваться ботом.)\n\nУдалено: заметок — {j}, напоминаний — {r}, репортов — {b}.",
        "uk": "✅ Готово. Усі твої дані видалено.\n(Акаунт лишив, можна далі користуватися ботом.)\n\nВидалено: нотаток — {j}, нагадувань — {r}, репортів — {b}.",
        "en": "✅ Done. All your data is deleted.\n(Account kept; you can continue using the bot.)\n\nDeleted: entries — {j}, reminders — {r}, reports — {b}.",
    },
}


def _lang(m: Message, user: Optional[User]) -> str:
    return (getattr(user, "language", None) or getattr(getattr(m, "from_user", None), "language_code", None) or "ru").lower()


def _t(lang: str, key: str, fallback: dict) -> str:
    """Простой безопасный перевод для локальных _L10N:
    без зависимостей от _BAD_I18N и app.i18n.
    """
    loc = (lang or "ru")[:2].lower()
    if loc == "ua":
        loc = "uk"
    return fallback.get(loc, fallback.get("ru", key))
@router.message(Command("erase"))
async def erase_start(m: Message, state: FSMContext):
    # Генерим одноразовую фразу вида "DELETE ABC123"
    token = secrets.token_hex(3).upper()  # 6 символов
    phrase = f"DELETE {token}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)

    await state.set_state(EraseFSM.confirm)
    await state.update_data(phrase=phrase, expires_at=expires_at.isoformat())

    # Язык возьмём из профиля позже, тут пока без session
    await m.answer(_t("prompt", _lang(m, None), phrase=phrase), parse_mode="HTML")


@router.message(Command("cancel"))
async def erase_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(_t("canceled", _lang(m, None)))


@router.message(EraseFSM.confirm)
async def erase_do(m: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    phrase = data.get("phrase")
    expires_at_s = data.get("expires_at")

    # На всякий случай
    if not phrase or not expires_at_s:
        await state.clear()
        return await m.answer(_t("expired", _lang(m, None)))

    try:
        expires_at = datetime.fromisoformat(expires_at_s)
    except Exception:
        await state.clear()
        return await m.answer(_t("expired", _lang(m, None)))

    # Ищем пользователя
    user = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()
    lang = _lang(m, user)

    if not user:
        await state.clear()
        return await m.answer(_t(lang, "need_start"))

    text = (m.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        return await m.answer(_t(lang, "canceled"))

    # Проверяем срок действия кода
    if datetime.now(timezone.utc) > expires_at:
        await state.clear()
        return await m.answer(_t(lang, "expired"))

    # Точное совпадение
    if text != phrase:
        return await m.answer(_t("wrong", lang, phrase=phrase), parse_mode="HTML")

    # Посчитаем, чтобы отчитаться
    j_cnt = await session.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user.id)
    )
    r_cnt = await session.scalar(
        select(func.count()).select_from(Reminder).where(Reminder.user_id == user.id)
    )
    b_cnt = await session.scalar(
        select(func.count()).select_from(BugReport).where(BugReport.user_id == user.id)
    )

    # Удаляем
    async with session.begin():
        await session.execute(
            delete(JournalEntry).where(JournalEntry.user_id == user.id).execution_options(synchronize_session=False)
        )
        await session.execute(
            delete(Reminder).where(Reminder.user_id == user.id).execution_options(synchronize_session=False)
        )
        await session.execute(
            delete(BugReport).where(BugReport.user_id == user.id).execution_options(synchronize_session=False)
        )

    await state.clear()
    await m.answer(_t("done", lang, j=j_cnt or 0, r=r_cnt or 0, b=b_cnt or 0))