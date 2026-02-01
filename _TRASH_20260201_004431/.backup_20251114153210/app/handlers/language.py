# app/handlers/language.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import get_main_kb, LANGUAGE_LABELS
from app.models.user import User

router = Router()

# Разрешённые коды (ввод RU/UK/UA/EN -> сохраняем ru/uk/en)
LANGS = {"RU": "ru", "UK": "uk", "UA": "uk", "EN": "en"}

PROMPTS = {
    "ru": "Выбери язык: RU / UK / EN (отправь одним словом)",
    "uk": "Вибери мову: RU / UK / EN (відправ одним словом)",
    "en": "Choose language: RU / UK / EN (send one word)",
}

def _pick_locale(user: User | None) -> str:
    loc = (user.locale if user and getattr(user, "locale", None) else "ru")
    return loc if loc in PROMPTS else "ru"

async def _get_or_create_user(session: AsyncSession, tg_id: int) -> User:
    user = (await session.execute(
        select(User).where(User.tg_id == tg_id)
    )).scalar_one_or_none()
    if not user:
        user = User(tg_id=tg_id, locale="ru")
        session.add(user)
        await session.flush()
    return user

# Открыть выбор языка: /language
@router.message(Command("language"))
async def language_cmd(m: Message, session: AsyncSession):
    user = await _get_or_create_user(session, m.from_user.id)
    await m.answer(PROMPTS[_pick_locale(user)], reply_markup=None)

# Открыть выбор языка: по кнопке из главного меню (с эмодзи)
@router.message(F.text.in_(LANGUAGE_LABELS))
async def language_btn(m: Message, session: AsyncSession):
    user = await _get_or_create_user(session, m.from_user.id)
    await m.answer(PROMPTS[_pick_locale(user)], reply_markup=None)

# Открыть выбор языка: если человек вручную написал "language|язык|мова"
@router.message(F.text.regexp(r"(?i)^\s*(language|язык|мова)\s*$"))
async def language_word(m: Message, session: AsyncSession):
    user = await _get_or_create_user(session, m.from_user.id)
    await m.answer(PROMPTS[_pick_locale(user)], reply_markup=None)

# Установка языка одним словом: RU/UK/UA/EN (регистр неважен)
@router.message(F.text.regexp(r"(?i)^\s*(ru|uk|ua|en)\s*$"))
async def language_set(m: Message, session: AsyncSession):
    code = LANGS.get(m.text.strip().upper(), "ru")

    user = await _get_or_create_user(session, m.from_user.id)
    if user.locale != code:
        user.locale = code
        session.add(user)
        await session.commit()

    msg = {
        "ru": "Готово. Язык обновлён.",
        "uk": "Готово. Мову оновлено.",
        "en": "Done. Language updated.",
    }.get(code, "Done.")
    await m.answer(msg, reply_markup=get_main_kb(code))