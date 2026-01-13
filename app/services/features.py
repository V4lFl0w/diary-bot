from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Set, Dict

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

# Мягкая синхронизация премиума из старого слоя (если он вообще есть)
try:
    from app.services.subscriptions import sync_user_premium_flags
except Exception:  # pragma: no cover
    async def sync_user_premium_flags(*_a, **_kw):
        return None


# ---------------------------------------------------------------------
# FEATURE FLAGS (CANONICAL + ALIASES)
# ---------------------------------------------------------------------

# Канонические ключи free
BASIC_FEATURES: Set[str] = {
    # journal
    "journal_basic",

    # reminders
    "remind_basic",

    # calories (text)
    "calories_text",

    # медитации/музыка базовые
    "meditations_basic",
    "music_basic",
}

# Канонические ключи premium
PREMIUM_FEATURES: Set[str] = {
    # reminders / meditations / playlists / helper / stats
    "premium_reminders",
    "premium_meditations",
    "premium_playlists",
    "premium_stats",
    "premium_helper",

    # journal расширения
    "journal_search",
    "journal_range",
    "journal_history_extended",
    # ⚠️ важно: в хендлере журнала используется ключ "journal_stats"
    # добавляем как отдельный канон (чтобы не ломать вызовы)
    "journal_stats",

    # calories
    "calories_photo",

    # admin/analytics (если подключишь роутер)
    "admin_panel",
    "analytics_dashboard",
}

# Алиасы для плавной миграции (старые/новые имена → канон)
FEATURE_ALIASES: Dict[str, str] = {
    # journal v2 aliases
    "premium_journal_search": "journal_search",
    "premium_journal_range": "journal_range",
    "premium_journal_history_extended": "journal_history_extended",

    # stats aliases (чтобы /stats не поплыл)
    "journal_stats": "journal_stats",  # явный канон
    "premium_journal_stats": "journal_stats",
    "stats_extended": "journal_stats",

    # calories aliases
    "premium_calories_photo": "calories_photo",

    # если где-то в коде остались такие названия
    "journal_history_plus": "journal_history_extended",
}


# ---------------------------------------------------------------------
# I18N
# ---------------------------------------------------------------------

SUPPORTED_LANGS = {"ru", "uk", "en"}
CB_OPEN_PREMIUM = "open_premium"


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        s = "uk"
    if s.startswith("en"):
        s = "en"
    if s not in SUPPORTED_LANGS:
        s = "ru"
    return s


def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return uk
    if l == "en":
        return en
    return ru


def _detect_lang(user: Optional[User], m: Optional[Message] = None) -> str:
    tg_lang = None
    if m and getattr(m, "from_user", None):
        tg_lang = getattr(m.from_user, "language_code", None)

    raw = (
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or tg_lang
        or "ru"
    )
    return _normalize_lang(str(raw))


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
    - is_premium (legacy-флаг)
    - либо premium_until в будущем (новый слой с подписками)
    """
    if not user:
        return False

    # 1) прямой флаг
    if hasattr(user, "is_premium"):
        try:
            if bool(getattr(user, "is_premium")):
                return True
        except Exception:
            pass

    # 2) по времени действия
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
    Строгая проверка доступа.
    Неизвестные фичи закрыты по умолчанию.
    """
    key = resolve_feature(feature)
    if not key:
        return False

    if key in BASIC_FEATURES:
        return True

    if key in PREMIUM_FEATURES:
        return _user_has_premium(user)

    return False


async def require_feature(
    m: Message,
    session: AsyncSession,
    user: User,
    feature: str,
) -> bool:
    """
    Проверяет доступ к фиче.
    Если нет — показывает upsell и возвращает False.
    Если да — True.
    """

    # Подтянуть флаг премиума из legacy-слоя / подписок (если есть)
    try:
        await sync_user_premium_flags(session, user)
    except Exception:
        pass

    if has_feature(user, feature):
        return True

    lang_code = _detect_lang(user, m)

    text = _tr(
        lang_code,
        "🔒 Эта функция доступна в Premium.\n\n"
        "Открывает: поиск и диапазоны в журнале, расширенную историю, "
        "статистику, калории по фото, улучшенные медитации и плейлисты.\n\n"
        "Премиум можно оформить оплатой картой или через Stars.",
        "🔒 Ця функція доступна у Premium.\n\n"
        "Відкриває: пошук і діапазони в журналі, розширену історію, "
        "статистику, калорії з фото, покращені медитації та плейлисти.\n\n"
        "Преміум можна оформити оплатою карткою або через Stars.",
        "🔒 This feature is available in Premium.\n\n"
        "Unlocks: journal search & ranges, extended history, "
        "stats, photo calories, better meditations and playlists.\n\n"
        "You can get Premium by paying with card or via Stars.",
    )

    premium_btn = _tr(lang_code, "💎 Premium", "💎 Преміум", "💎 Premium")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=premium_btn, callback_data=CB_OPEN_PREMIUM)],
        ]
    )

    await m.answer(text, reply_markup=kb)
    return False


# v2 alias
require_feature_v2 = require_feature


__all__ = [
    "BASIC_FEATURES",
    "PREMIUM_FEATURES",
    "FEATURE_ALIASES",
    "CB_OPEN_PREMIUM",
    "resolve_feature",
    "has_feature",
    "require_feature",
    "require_feature_v2",
]