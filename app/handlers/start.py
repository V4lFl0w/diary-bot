from __future__ import annotations

import re
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.handlers.privacy import privacy_soft_show
from app.keyboards import get_main_kb
from app.config import settings

# ✅ админ-доступ (единая логика)
try:
    from app.handlers.admin import is_admin_tg
except Exception:
    def is_admin_tg(_: int) -> bool:
        return False

# ✅ синхронизация премиума из подписок
try:
    from app.services.subscriptions import sync_user_premium_flags
except Exception:
    async def sync_user_premium_flags(*_args, **_kwargs):
        return None

# ✅ аналитика UI
try:
    from app.services.analytics_helpers import log_ui
except Exception:
    async def log_ui(*_a, **_k):
        return None


router = Router(name="start")

_TEXTS = {
    "ru": {
        "hello_need_privacy": (
            "Привет! Это дневник-помощник.\n"
            "Сначала прими <b>🔒 Политику</b> — это займёт 10 секунд.\n"
            "Главное меню — внизу."
        ),
        "hello_ready": (
            "С возвращением! Можешь писать запись командой /journal.\n"
            "Главное меню — внизу."
        ),
    },
    "uk": {
        "hello_need_privacy": (
            "Привіт! Це щоденник-помічник.\n"
            "Спочатку прийми <b>🔒 Політику</b> — це займе 10 секунд.\n"
            "Головне меню — внизу."
        ),
        "hello_ready": (
            "З поверненням! Можеш писати запис командою /journal.\n"
            "Головне меню — внизу."
        ),
    },
    "en": {
        "hello_need_privacy": (
            "Hi! This is a journal assistant.\n"
            "First accept <b>🔒 Privacy</b> — takes 10 seconds.\n"
            "Main menu is below."
        ),
        "hello_ready": (
            "Welcome back! You can write an entry with /journal.\n"
            "Main menu is below."
        ),
    },
}

_SUPPORTED = {"ru", "uk", "en"}


def _norm_locale(x: str | None) -> str:
    s = (x or "").split("-")[0].strip().lower()
    if s == "ua":
        s = "uk"
    return s if s in _SUPPORTED else "ru"


def _is_valid_tz(tz: str | None) -> bool:
    if not tz:
        return False
    try:
        ZoneInfo(tz)
        return True
    except Exception:
        return False


def _parse_start_payload(text: str | None) -> tuple[str | None, str | None]:
    """Deep-link: /start lang=uk tz=Europe/Kyiv -> (lang, tz)"""
    if not text:
        return None, None
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""
    if not payload:
        return None, None

    m_lang = re.search(r"(?:^|\s)lang=(ru|uk|en|ua)\b", payload, re.I)
    m_tz = re.search(r"(?:^|\s)tz=([\w/\-+]+)", payload, re.I)

    lang = _norm_locale(m_lang.group(1)) if m_lang else None
    tz = m_tz.group(1) if m_tz else None
    return lang, tz


def _calc_premium(user: User | None) -> bool:
    if not user:
        return False

    if bool(getattr(user, "has_premium", False) or getattr(user, "is_premium", False)):
        return True

    pu = getattr(user, "premium_until", None)
    if not pu:
        return False

    try:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        return pu > datetime.now(timezone.utc)
    except Exception:
        return False


def _policy_accepted(user: User | None) -> bool:
    if not user:
        return False
    return bool(getattr(user, "consent_accepted_at", None) or getattr(user, "policy_accepted", False))


@router.message(CommandStart())
async def cmd_start(m: Message, session: AsyncSession) -> None:
    if not m.from_user:
        return

    tg_id = m.from_user.id

    user: User | None = (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one_or_none()

    is_new = user is None

    # deep-link + defaults
    lang_dl, tz_dl = _parse_start_payload(m.text or "")
    lang_tele = _norm_locale(getattr(m.from_user, "language_code", None))
    lang_default = _norm_locale(getattr(settings, "default_locale", None) or "ru")
    tz_default = getattr(settings, "default_tz", None) or "Europe/Kyiv"

    # create/update base fields
    if not user:
        loc = lang_dl or lang_tele or lang_default
        tz = tz_dl if _is_valid_tz(tz_dl) else tz_default
        user = User(tg_id=tg_id, locale=loc, lang=loc, tz=tz)
        session.add(user)
        await session.flush()
    else:
        changed = False
        if lang_dl and getattr(user, "locale", None) != lang_dl:
            user.locale = lang_dl
            user.lang = lang_dl
            changed = True
        if tz_dl and _is_valid_tz(tz_dl) and getattr(user, "tz", None) != tz_dl:
            user.tz = tz_dl
            changed = True
        if changed:
            session.add(user)

    # sync premium
    try:
        await sync_user_premium_flags(session, user)
    except Exception:
        pass

    # analytics (перед общим commit)
    try:
        await log_ui(
            session,
            user=user,
            user_id=user.id,
            event="user_new" if is_new else "user_start",
            source="command",
            tg_lang=getattr(m.from_user, "language_code", None),
        )
    except Exception:
        pass

    await session.commit()

    lang = _norm_locale(getattr(user, "locale", None) or getattr(user, "lang", None) or "ru")
    is_premium = _calc_premium(user)
    kb = get_main_kb(lang=lang, is_premium=is_premium, is_admin=is_admin_tg(tg_id))

    if not _policy_accepted(user):
        # 1) явно показываем что бот живой + меню
        text = _TEXTS.get(lang, _TEXTS["ru"])["hello_need_privacy"]
        await m.answer(text, reply_markup=kb, parse_mode="HTML")

        # 2) отдельным сообщением показываем политику (без "висяка")
        await privacy_soft_show(m, session)
        return

    # normal start
    text = _TEXTS.get(lang, _TEXTS["ru"])["hello_ready"]
    await m.answer(text, reply_markup=kb, parse_mode="HTML")