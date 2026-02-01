from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, FrozenSet

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

# --- аналитика (мягко, без падений) ---
try:
    from app.services.analytics_v2 import log_event_v2
except Exception:  # pragma: no cover

    async def log_event_v2(*_a: Any, **_k: Any):
        return None


# --- синк премиум-флага (если сервис есть) ---
try:
    from app.services.subscriptions import sync_user_premium_flags
except Exception:  # pragma: no cover

    async def sync_user_premium_flags(*_a: Any, **_k: Any):
        return None


# ---------------------------------------------------------------------
# FEATURE FLAGS (CANONICAL + ALIASES)
# ---------------------------------------------------------------------
# Принцип:
# 1) В списках — только каноничные имена.
# 2) Старые/альтернативные имена живут в FEATURE_ALIASES.
# 3) has_feature() проверяет только канон.
# ---------------------------------------------------------------------

BASIC_FEATURES: FrozenSet[str] = frozenset(
    {
        "journal_basic",
        "remind_basic",
        "calories_text",
        "music_basic",
        "meditations_basic",
    }
)

PREMIUM_FEATURES: FrozenSet[str] = frozenset(
    {
        # reminders / meditations / stats / helper
        "premium_reminders",
        "premium_meditations",
        "premium_playlists",
        "premium_stats",
        "premium_helper",
        # journal premium (канон)
        "journal_search",
        "journal_range",
        "journal_history_extended",
        # calories photo (канон)
        "calories_photo",
        # служебные/админские
        "admin_panel",
        "analytics_dashboard",
    }
)

# Все старые и альтернативные ключи
FEATURE_ALIASES: Mapping[str, str] = {
    # journal v2 -> v1 канон
    "premium_journal_search": "journal_search",
    "premium_journal_range": "journal_range",
    "premium_journal_history_extended": "journal_history_extended",
    # stats: стягиваем всё в один канон premium_stats
    "journal_stats": "premium_stats",
    "premium_journal_stats": "premium_stats",
    # calories
    "premium_calories_photo": "calories_photo",
    # на случай старых названий/экспериментов
    "premium_history_extended": "journal_history_extended",
}

SUPPORTED_LANGS = {"ru", "uk", "en"}
CB_OPEN_PREMIUM = "open_premium"


# ---------------------------------------------------------------------
# I18N
# ---------------------------------------------------------------------


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    loc = _normalize_lang(lang)
    return uk if loc == "uk" else en if loc == "en" else ru


def _premium_btn_text(lang: str) -> str:
    return _tr(lang, "💎 Премиум", "💎 Преміум", "💎 Premium")


def _detect_lang(user: Optional[User], m: Optional[Message] = None) -> str:
    tg_lang = getattr(getattr(m, "from_user", None), "language_code", None) if m else None
    return _normalize_lang(
        (getattr(user, "locale", None) if user else None) or (getattr(user, "lang", None) if user else None) or tg_lang
    )


# ---------------------------------------------------------------------
# CORE
# ---------------------------------------------------------------------


def resolve_feature(feature: str) -> str:
    key = (feature or "").strip()
    if not key:
        return ""
    return FEATURE_ALIASES.get(key, key)


def _user_has_premium(user: Optional[User]) -> bool:
    """
    Единая проверка премиума:
    - is_premium (legacy-флаг, Stars и всё остальное)
    - premium_until в будущем (новый слой с подписками / оплатами)
    """
    if not user:
        return False

    # 1) прямой флаг is_premium
    if hasattr(user, "is_premium"):
        try:
            if bool(getattr(user, "is_premium")):
                return True
        except Exception:
            pass

    # 2) по времени действия premium_until
    pu = getattr(user, "premium_until", None)
    if pu:
        try:
            now = datetime.now(timezone.utc)
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False

    return False


def has_feature(user: Optional[User], feature: str) -> bool:
    """
    Строгая проверка доступа:
    - неизвестные фичи закрыты
    - user может быть None
    """
    key = resolve_feature(feature)
    if not key:
        return False

    if key in BASIC_FEATURES:
        return True

    if key in PREMIUM_FEATURES:
        return _user_has_premium(user)

    return False


async def require_feature_v2(
    m: Message,
    session: AsyncSession,
    user: Optional[User],
    feature: str,
    *,
    event_on_fail: str | None = None,
    props: Dict[str, Any] | None = None,
) -> bool:
    """
    Унифицированный v2-гейт:
    - BASIC_FEATURES -> ok
    - PREMIUM_FEATURES -> требует активный премиум (is_premium/premium_until)
    - алиасы автоматически приводятся к канону
    - нет доступа -> upsell + (опционально) analytics event
    """

    feature_key = resolve_feature(feature)
    if not feature_key:
        return False

    # если user отсутствует — мягкий выход
    if user is None:
        lang_code = _detect_lang(None, m)
        await m.answer(
            _tr(
                lang_code,
                "Нажми /start, чтобы активировать профиль.",
                "Натисни /start, щоб активувати профіль.",
                "Press /start to initialize your profile.",
            )
        )
        return False

    # синк премиума, чтобы "только оплатил" сразу открыло доступ
    try:
        await sync_user_premium_flags(session, user)
    except Exception:
        pass

    if has_feature(user, feature_key):
        return True

    lang_code = _detect_lang(user, m)

    text = _tr(
        lang_code,
        # RU
        "🔒 Эта функция доступна в премиум-доступе.\n\n"
        "Премиум открывает:\n"
        "• расширенные напоминания\n"
        "• поиск и фильтры в журнале\n"
        "• расширенную статистику\n"
        "• плейлисты и медитации\n"
        "• калории по фото\n"
        "• приоритетную поддержку\n\n"
        "Оформить премиум можно оплатой картой или через Telegram Stars.",
        # UK
        "🔒 Ця функція доступна у преміум-доступі.\n\n"
        "Преміум відкриває:\n"
        "• розширені нагадування\n"
        "• пошук і фільтри в журналі\n"
        "• розширену статистику\n"
        "• плейлисти та медитації\n"
        "• калорії з фото\n"
        "• пріоритетну підтримку\n\n"
        "Оформити преміум можна оплатою карткою або через Telegram Stars.",
        # EN
        "🔒 This feature is available in Premium.\n\n"
        "Premium unlocks:\n"
        "• advanced reminders\n"
        "• journal search & filters\n"
        "• extended statistics\n"
        "• playlists & meditations\n"
        "• photo calories\n"
        "• priority support\n\n"
        "You can get Premium by paying with a card or via Telegram Stars.",
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_premium_btn_text(lang_code),
                    callback_data=CB_OPEN_PREMIUM,
                )
            ],
        ]
    )

    # аналитика фейла (если включена)
    try:
        if getattr(user, "id", None):
            event_name = event_on_fail or "feature_locked"
            payload: Dict[str, Any] = {"feature": feature_key}
            if props:
                payload.update(props)

            await log_event_v2(
                session,
                user_id=user.id,
                event=event_name,
                props=payload,
            )
    except Exception:
        # аналитика не должна ломать флоу
        pass

    await m.answer(text, reply_markup=kb)
    return False


# ---------------------------------------------------------------------
# BACKWARD COMPAT
# ---------------------------------------------------------------------
# Чтобы старые импорты не ломались:
# from app.services.features_v2 import require_feature
require_feature = require_feature_v2


__all__ = [
    "BASIC_FEATURES",
    "PREMIUM_FEATURES",
    "FEATURE_ALIASES",
    "CB_OPEN_PREMIUM",
    "resolve_feature",
    "has_feature",
    "require_feature_v2",
    "require_feature",
]
