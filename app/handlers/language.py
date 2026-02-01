from __future__ import annotations

from typing import Optional

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import t
from app.keyboards import get_main_kb, is_language_btn
from app.models.user import User

router = Router()

ALIASES = {
    "ru": "ru",
    "rus": "ru",
    "ру": "ru",
    "рус": "ru",
    "русский": "ru",
    "російська": "ru",
    "uk": "uk",
    "ua": "uk",
    "ук": "uk",
    "укр": "uk",
    "українська": "uk",
    "украинский": "uk",
    "en": "en",
    "eng": "en",
    "англ": "en",
    "английский": "en",
    "англійська": "en",
    "english": "en",
}


def _safe_loc(user: Optional[User], tg_lang: str | None = None) -> str:
    raw = (
        getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru"
    )
    s = str(raw).lower().strip()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _is_admin(user: Optional[User], tg_id: int) -> bool:
    # 1) если у тебя есть поле is_admin
    if user and bool(getattr(user, "is_admin", False)):
        return True

    # 2) если у тебя есть список админов в конфиге
    try:
        from app.config import settings as cfg

        ids = getattr(cfg, "admin_ids", None)
        if ids and tg_id in set(ids):
            return True
    except Exception:
        pass

    # 3) fallback через env
    import os

    raw = os.getenv("ADMIN_IDS", "")
    if raw:
        try:
            ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
            if tg_id in ids:
                return True
        except Exception:
            pass

    return False


def _is_premium(user: Optional[User]) -> bool:
    # подстрой под свою модель
    if not user:
        return False
    return bool(
        getattr(user, "is_premium", False) or getattr(user, "premium_until", None)
    )


@router.message(
    F.text.func(is_language_btn)
    | F.text.regexp(r"(?i)^\s*(/language|language|язык|мова)\s*$")
)
async def language_start(m: Message, session: AsyncSession):
    user = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()

    loc = _safe_loc(user, getattr(m.from_user, "language_code", None))
    await m.answer(t("choose_lang", loc), reply_markup=None)


@router.message(F.text.func(lambda s: (s or "").strip().lower() in ALIASES))
async def language_set(m: Message, session: AsyncSession):
    code = ALIASES[(m.text or "").strip().lower()]

    user = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()

    if not user:
        user = User(tg_id=m.from_user.id, locale=code, lang=code)
        session.add(user)
    else:
        user.locale = code
        try:
            await session.execute(
                sql_text("UPDATE users SET lang=:lang WHERE tg_id=:tg"),
                {"lang": code, "tg": m.from_user.id},
            )
        except Exception:
            user.lang = code

    await session.commit()

    is_admin = _is_admin(user, m.from_user.id)
    is_premium = _is_premium(user)

    await m.answer(
        t("lang_updated", code),
        reply_markup=get_main_kb(code, is_premium=is_premium, is_admin=is_admin),
    )


__all__ = ["router"]
