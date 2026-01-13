from __future__ import annotations

from typing import Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.services.features_v2 import require_feature_v2


router = Router(name="meditations_v2")


SUPPORTED_LANGS = {"ru", "uk", "en"}
FEATURE_PREMIUM_MEDITATIONS = "premium_meditations"


# -------------------- lang helpers --------------------

def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()

    if s.startswith(("ua", "uk")):
        s = "uk"
    elif s.startswith("en"):
        s = "en"
    else:
        s = "ru"

    return s if s in SUPPORTED_LANGS else "ru"


def _tr(lang: Optional[str], ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    return uk if l == "uk" else en if l == "en" else ru


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(
        select(User).where(User.tg_id == tg_id)
    )).scalar_one_or_none()


def _user_lang(user: Optional[User], tg_lang: Optional[str], fallback: Optional[str]) -> str:
    return _normalize_lang(
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or tg_lang
        or fallback
        or "ru"
    )


# -------------------- handlers --------------------

@router.message(Command("meditation_long"))
async def meditation_long_cmd(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Premium-фича: расширенная медитация.

    v2 UX-слой:
    - paywall через require_feature_v2
    - event на fail
    - продуктовые обещания будущих апдейтов
    """

    tg_lang = getattr(getattr(m, "from_user", None), "language_code", None)

    user = await _get_user(session, m.from_user.id)
    lang_code = _user_lang(user, tg_lang, lang)

    if not user:
        await m.answer(
            _tr(lang_code, "Нажми /start", "Натисни /start", "Press /start")
        )
        return

    ok = await require_feature_v2(
        m,
        session,
        user,
        FEATURE_PREMIUM_MEDITATIONS,
        event_on_fail="meditation_long_locked",
        props={"cmd": "meditation_long"},
    )
    if not ok:
        return

    await m.answer(
        _tr(
            lang_code,
            "🧘‍♂️ Длинная медитация открыта ✅\n\n"
            "Скоро добавим:\n"
            "• выбор длительности (10/20/30/45)\n"
            "• музыку и атмосферные сессии\n"
            "• сохранение прогресса",
            "🧘‍♂️ Довга медитація відкрита ✅\n\n"
            "Скоро додамо:\n"
            "• вибір тривалості (10/20/30/45)\n"
            "• музику та атмосферні сесії\n"
            "• збереження прогресу",
            "🧘‍♂️ Long meditation unlocked ✅\n\n"
            "Coming soon:\n"
            "• duration choice (10/20/30/45)\n"
            "• music and guided sessions\n"
            "• progress saving",
        )
    )


__all__ = ["router"]