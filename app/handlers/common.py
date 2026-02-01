# app/handlers/cancel.py
from __future__ import annotations
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards import get_main_kb

try:
    from app.handlers.admin import is_admin_tg
except Exception:

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


router = Router(name="cancel")


# Поддерживаем /cancel и текстовые варианты
_CANCEL_WORDS = {
    "ru": {"отмена", "стоп", "cancel", "/cancel"},
    "uk": {"скасувати", "відміна", "cancel", "/cancel"},
    "en": {"cancel", "stop", "/cancel"},
}

_MSG = {
    "ru": "Ок, отменил. Ты в главном меню.",
    "uk": "Ок, скасував. Ти в головному меню.",
    "en": "Okay, canceled. You're in the main menu.",
}


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _is_cancel_text(text: str) -> bool:
    t = _norm(text)
    universe = set().union(*_CANCEL_WORDS.values())
    return t in universe


@router.message(Command("cancel"))
@router.message(F.text.func(lambda t: _is_cancel_text(t)))
async def cancel_any(
    m: Message,
    state: FSMContext,
    lang: Optional[str] = None,
) -> None:
    """
    Глобальный /cancel:
    - очищает любое состояние
    - возвращает главное меню
    Lang приходит из middleware как параметр, но на всякий есть fallback.
    """
    if not m.from_user:
        return

    await state.clear()

    l = _normalize_lang(lang or getattr(m.from_user, "language_code", None))
    msg = _MSG.get(l, _MSG["ru"])

    is_admin = is_admin_tg(m.from_user.id)
    await m.answer(msg, reply_markup=get_main_kb(l, is_admin=is_admin))


__all__ = ["router"]
