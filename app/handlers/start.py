from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.privacy import privacy_soft_show
from app.keyboards import get_main_kb
from app.models.user import User
from app.services.premium_check import is_premium_active

# ✅ админ-доступ (единая логика)
try:
    from app.handlers.admin import is_admin_tg
except Exception:

    def is_admin_tg(tg_id: int, /) -> bool:
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
            "Прими политику и начнём 👇"
        ),
        "hello_new": "Добро пожаловать! 🎉 Начни с 📓 Журнал — запиши свою первую мысль.",
        "hello_ready": ("С возвращением! Можешь писать запись командой /journal.\nГлавное меню — внизу."),
    },
    "uk": {
        "hello_need_privacy": (
            "Привіт! Це щоденник-помічник.\n"
            "Спочатку прийми <b>🔒 Політику</b> — це займе 10 секунд.\n"
            "Прийми політику і почнемо 👇"
        ),
        "hello_new": "Ласкаво просимо! 🎉 Почни з 📓 Щоденник — запиши свою першу думку.",
        "hello_ready": ("З поверненням! Можеш писати запис командою /journal.\nГоловне меню — внизу."),
    },
    "en": {
        "hello_need_privacy": (
            "Hi! This is a journal assistant.\nFirst accept <b>🔒 Privacy</b> — takes 10 seconds.\nAccept the policy to get started 👇"
        ),
        "hello_new": "Welcome! 🎉 Start with 📓 Journal — write your first thought.",
        "hello_ready": ("Welcome back! You can write an entry with /journal.\nMain menu is below."),
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
    return is_premium_active(user)


def _policy_accepted(user: User | None) -> bool:
    return bool(getattr(user, "consent_accepted_at", None) or getattr(user, "policy_accepted", False))


@router.message(CommandStart())
async def cmd_start(m: Message, session: AsyncSession, user: User | None = None) -> None:
    if not m.from_user:
        return
    tg_id = int(m.from_user.id)

    # Fallback: если middleware не положил user, загрузим/создадим тут
    if user is None:
        from sqlalchemy import select

        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=tg_id)
            session.add(user)

    # user существует дальше всегда
    is_new = False
    try:
        ca = getattr(user, "created_at", None)
        if ca is not None:
            if getattr(ca, "tzinfo", None) is None:
                ca = ca.replace(tzinfo=timezone.utc)
            is_new = (datetime.now(timezone.utc) - ca) <= timedelta(seconds=30)
    except Exception:
        is_new = False

    # также считаем «новым», если политика принята только что (в течение 10 секунд)
    try:
        caa = getattr(user, "consent_accepted_at", None)
        if caa is not None:
            if getattr(caa, "tzinfo", None) is None:
                caa = caa.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - caa) <= timedelta(seconds=10):
                is_new = True
    except Exception:
        pass

    # deep-link + defaults
    lang_dl, tz_dl = _parse_start_payload(m.text or "")

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
        await privacy_soft_show(m, session)
        return

    key = "hello_new" if is_new else "hello_ready"
    text = _TEXTS.get(lang, _TEXTS["ru"])[key]
    await m.answer(text, reply_markup=kb, parse_mode="HTML")
