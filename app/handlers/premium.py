"""
Премиум-модуль:
- показ меню премиума
- выдача 24 часов за подписку на канал
- сброс премиума админом


Важно:
- поддерживает callback_data="open_premium" (нужно для features_v2)
- даёт кнопки:
    • оплата картой (веб)
    • оплата через Telegram Stars (встроенный invoice)
"""

from __future__ import annotations

import os

from app.webapp.urls import (
    webapp_base_url,
    with_version,
    versioned_abs_url,
    WEBAPP_PREMIUM_ENTRY,
)

from datetime import datetime, timedelta, timezone

from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.filters.buttons import Btn
from app.jobs.renewal_reminders import run_renewal_reminders
from app.utils.aiogram_guards import cb_reply


def _safe_btn(*, text: str, url: str | None = None, cb: str | None = None) -> InlineKeyboardButton:
    """Telegram запрещает text-only inline-кнопки: нужна url или callback_data."""
    if url:
        return InlineKeyboardButton(text=text, url=url)
    if cb:
        return InlineKeyboardButton(text=text, callback_data=cb)
    raise ValueError(f"Inline button '{text}' has no url/callback")


# ✅ главный клава-генератор
try:
    from app.keyboards import get_main_kb  # type: ignore
except Exception:

    def get_main_kb(lang: str, is_premium: bool = False, is_admin: bool = False):
        return None


# ✅ единая логика админа (как в start/admin)
try:
    from app.handlers.admin import is_admin_tg
except Exception:

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


router = Router()

SUPPORTED_LANGS = {"ru", "uk", "en"}

CB_OPEN_PREMIUM = "open_premium"
CB_PREMIUM_CHECK = "premium:check"
CB_SUB_CANCEL = "sub:cancel"
CB_SUB_CANCEL_CONFIRM = "sub:cancel:confirm"
CB_TRIAL_START = "premium:trial:start"


CB_PREMIUM_DETAILS = "premium:details"


def _normalize_lang(code: Optional[str]) -> str:
    """Приводим код языка к ru/uk/en с учётом ua → uk."""
    loc = (code or "ru").strip().lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _webapp_url(tg_id: int, lang: str) -> str:
    loc = _normalize_lang(lang)
    # Telegram WebApp button requires ABSOLUTE https:// URL
    base = webapp_base_url()
    if base:
        # Use canonical entry and cache-bust by git sha via ?v=
        url = versioned_abs_url(WEBAPP_PREMIUM_ENTRY)
    else:
        url = with_version("https://coral-app-jxzy5.ondigitalocean.app/static/mini/premium/premium.html")
    sep = "&" if "?" in url else "?"
    # Keep your runtime params (do NOT use v= here; v is reserved for git sha cache bust)
    return f"{url}{sep}tg_id={tg_id}&lang={loc}&ts={int(datetime.now(timezone.utc).timestamp())}"


TEXTS: Dict[str, Dict[str, str]] = {
    "presale_lines": {
        "ru": "🔋 Больше мощности каждый день\n— быстрее и без пауз\n— токены на тяжёлые функции\n— Pro/Max для активного режима\n— Доступ к расширенным функциям\n— Удобное взаимодействие",
        "uk": "🔋 Більше потужності щодня\n— швидше і без пауз\n— токени на важкі функції\n— Pro/Max для активного режиму\n— Доступ до розширених функцій\n— Зручна взаємодія",
        "en": "🔋 More power every day\n— faster, no pauses\n— tokens for heavy features\n— Pro/Max for active mode\n— Access to advanced features\n— Smooth interaction",
    },
    "sub_given": {
        "ru": "Поздравляю! Подписка подтверждена — премиум активирован на 24 часа ✅",
        "uk": "Вітаю! Підписку підтверджено — преміум активовано на 24 години ✅",
        "en": "Congrats! Subscription confirmed — Premium activated for 24 hours ✅",
    },
    "sub_not_found": {
        "ru": "Не вижу подписки. Нажми «Подписаться», затем «Проверить». Если всё равно не срабатывает — оформи премиум через «🚀 Выбрать тариф» ниже 💳✨",
        "uk": "Не бачу підписки. Натисни «Підписатися», потім «Перевірити». Якщо все одно не спрацьовує — оформи преміум через «🚀 Обрати тариф» нижче 💳✨",
        "en": "I can’t see your subscription. Tap “Subscribe”, then “Check”. If it still doesn’t work — purchase a plan via “🚀 Choose plan” below 💳✨",
    },
    "trial_used": {
        "ru": "Бесплатный день уже использован. Чтобы пользоваться премиумом дальше, нужно оформить платную подписку — выбрать тариф ниже 💳✨",
        "uk": "Безкоштовний день уже використано. Щоб і надалі користуватися преміумом, потрібно оформити платну підписку — обрати тариф нижче 💳✨",
        "en": "Your free trial has already been used. To keep using Premium, please purchase a plan — choose one below 💳✨",
    },
    # Чётко обозначаем, что это именно оплата картой (Stars — отдельная кнопка)
    "btn_pay": {"ru": "🚀 Выбрать тариф", "uk": "🚀 Обрати тариф", "en": "🚀 Choose plan"},
    "btn_open": {"ru": "🚀 Выбрать тариф", "uk": "🚀 Обрати тариф", "en": "🚀 Choose plan"},
    "btn_more": {"ru": "ℹ️ Подробнее", "uk": "ℹ️ Детальніше", "en": "ℹ️ Details"},
    "presale": {
        "ru": "🔥 Предпродажа: зафиксируй цену + забери бонус-токены",
        "uk": "🔥 Передпродаж: зафіксуй ціну та бонуси",
        "en": "🔥 Pre-sale: lock price + bonuses",
    },
    "short_b1": {
        "ru": "⚡️ Без пауз: больше лимиты и скорость",
        "uk": "⚡️ Без пауз: більше лімітів і швидкість",
        "en": "⚡️ No pauses: higher limits & speed",
    },
    "short_b2": {
        "ru": "🎬 Тяжёлые функции: фото/видео/документы",
        "uk": "🎬 Важкі функції: фото/відео/документи",
        "en": "🎬 Heavy: images/video/docs",
    },
    "short_cta": {
        "ru": "Жми «🚀 Выбрать тариф» — и забирай бонусы.",
        "uk": "Тицяй «🚀 Обрати тариф» — і забирай бонуси.",
        "en": "Tap “🚀 Choose plan” to claim bonuses.",
    },
    "btn_sub": {"ru": "Подписаться", "uk": "Підписатися", "en": "Subscribe"},
    "btn_check": {"ru": "Проверить", "uk": "Перевірити", "en": "Check"},
}


def t_local(lang: str, key: str, **fmt: Any) -> str:
    """Локализатор для premium.py (ru/uk/en)."""
    loc = _normalize_lang(lang)

    v = None

    # key-first: TEXTS[key][lang]
    base = TEXTS.get(key)
    if isinstance(base, dict):
        v = base.get(loc) or base.get("ru") or base.get("uk") or base.get("en")

    # lang-first (на случай старого формата): TEXTS[lang][key]
    if v is None:
        lang_map = TEXTS.get(loc)
        if isinstance(lang_map, dict):
            v = lang_map.get(key)

    # list/tuple -> string
    if isinstance(v, (list, tuple)):
        v = "".join(str(x) for x in v)

    if isinstance(v, str):
        return v.format(**fmt) if fmt else v

    return key


CHANNEL_USERNAME = getattr(settings, "premium_channel", None) or os.getenv("PREMIUM_CHANNEL") or "@NoticesDiarY"
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"


def _lang_of(
    user: dict | None,
    obj: Message | CallbackQuery | None,
    fallback: Optional[str] = None,
) -> str:
    """
    Приоритет:
    1) user.locale
    2) user.lang
    3) Telegram language_code
    4) fallback (из middleware)
    5) settings.default_locale / 'ru'
    """
    code: Optional[str] = None

    if user:
        code = user.get("locale") or user.get("lang")

    if not code and obj:
        fu = getattr(obj, "from_user", None)
        if not fu and isinstance(obj, CallbackQuery):
            fu = getattr(getattr(obj, "message", None), "from_user", None)
        code = getattr(fu, "language_code", None) if fu else None

    if not code:
        code = fallback or getattr(settings, "default_locale", "ru")

    return _normalize_lang(code)


async def _ensure_user_columns(session: AsyncSession) -> None:
    """
    SQLite-safe guard.
    В проде на Postgres эти ALTER могут падать — потому best-effort.
    """
    ddls = (
        "ALTER TABLE users ADD COLUMN locale TEXT",
        "ALTER TABLE users ADD COLUMN lang TEXT",
        "ALTER TABLE users ADD COLUMN premium_until TIMESTAMP",
        "ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN premium_trial_given INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN tz TEXT",
        "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
    )
    for ddl in ddls:
        try:
            await session.execute(sql_text(ddl))
        except Exception:
            pass
    try:
        await session.commit()
    except Exception:
        await session.rollback()


async def _fetch_user(session: AsyncSession, tg_id: int) -> dict:
    """Забираем юзера как dict (минимальный срез)."""
    await _ensure_user_columns(session)

    q = sql_text(
        "SELECT id, tg_id, locale, lang, is_premium, premium_until, "
        "premium_trial_given, tz, is_admin "
        "FROM users WHERE tg_id=:tg"
    )

    row = (await session.execute(q, {"tg": tg_id})).first()
    default_tz = getattr(settings, "default_tz", "Europe/Kyiv")
    default_lang = _normalize_lang(getattr(settings, "default_locale", "ru"))

    if row:
        m = row._mapping  # type: ignore[attr-defined]
        locale = m.get("locale")
        lang = m.get("lang")
        is_premium = m.get("is_premium") or 0
        premium_until = m.get("premium_until")
        premium_trial_given = m.get("premium_trial_given") or 0
        tz = m.get("tz") or default_tz
        is_admin = m.get("is_admin") or 0

        lang_final = _normalize_lang(lang or locale or default_lang)

        return {
            "id": m.get("id"),
            "tg_id": m.get("tg_id"),
            "lang": lang_final,
            "locale": locale or lang_final,
            "is_premium": bool(is_premium),
            "premium_until": premium_until,
            "premium_trial_given": int(premium_trial_given),
            "tz": tz,
            "is_admin": bool(is_admin),
        }

    # Юзер ещё не создан — создаём базовую запись
    await session.execute(
        sql_text(
            "INSERT INTO users (tg_id, locale, lang, is_premium, premium_trial_given, tz, is_admin) "
            "VALUES (:tg, :loc, :lang, 0, 0, :tz, 0)"
        ),
        {"tg": tg_id, "loc": default_lang, "lang": default_lang, "tz": default_tz},
    )
    await session.commit()

    return {
        "id": None,
        "tg_id": tg_id,
        "lang": default_lang,
        "locale": default_lang,
        "is_premium": False,
        "premium_until": None,
        "premium_trial_given": 0,
        "tz": default_tz,
        "is_admin": False,
    }


def _to_dt_aware(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_active(user: Dict[str, Any]) -> bool:
    if not user.get("is_premium"):
        return False
    until = _to_dt_aware(user.get("premium_until"))
    if until is None:
        return True  # бессрочный премиум
    return datetime.now(timezone.utc) < until


def _fmt_local(dt_utc: datetime, tz_name: str) -> str:
    try:
        return dt_utc.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_utc.astimezone(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M")


def _resolve_is_admin(tg_id: int, user: Dict[str, Any] | None = None) -> bool:
    """Единый итоговый флаг админа для клавиатуры."""
    if is_admin_tg(tg_id):
        return True
    if user:
        return bool(user.get("is_admin"))
    return False


def _stars_label(lang: str) -> str:
    """Подпись для кнопки оплаты через Stars."""
    loc = (lang or "ru")[:2].lower()
    if loc == "ua":
        loc = "uk"
    labels = {
        "ru": "⭐ Оплатить Stars",
        "uk": "⭐ Оплатити Stars",
        "en": "⭐ Pay with Stars",
    }
    return labels.get(loc, labels["ru"])


def _pay_kb(lang: str, tg_id: int, is_premium: bool = False) -> InlineKeyboardMarkup:
    """
    Инлайн-кнопки оплаты:
    - картой (через внешний /pay)
    - через Telegram Stars (внутри бота)
    - отмена подписки (если премиум активен)
    """
    base = (getattr(settings, "public_url", "") or "").strip()
    if not base.startswith("https://"):
        base = (getattr(settings, "public_url", "") or "").strip().rstrip("/")
    # PUBLIC_URL not set -> show without payment button
    if not base.startswith("https://"):
        base = ""

    rows = [
        [
            InlineKeyboardButton(
                text=t_local(lang, "btn_pay"),
                web_app=WebAppInfo(url=_webapp_url(tg_id, lang)),
            )
        ],
    ]

    if is_premium:
        rows.append([
            InlineKeyboardButton(
                text=_t_cancel_label(lang),
                web_app=WebAppInfo(url=_webapp_url(tg_id, lang)),
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text={
                    "ru": "💸 Возврат средств",
                    "uk": "💸 Повернення коштів",
                    "en": "💸 Refund",
                }.get(lang, "💸 Возврат средств"),
                callback_data="refund:open",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _active_premium_kb(lang: str, tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_t_cancel_label(lang), web_app=WebAppInfo(url=_webapp_url(tg_id, lang)))],
            [InlineKeyboardButton(text={'ru': '💸 Возврат средств', 'uk': '💸 Повернення коштів', 'en': '💸 Refund'}.get(lang, '💸 Возврат средств'), callback_data='refund:open')],
        ]
    )


def _subscribe_kb(
    lang: str, tg_id: int, show_trial: bool = True, show_details: bool = True, show_stars: bool = True
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t_local(lang, "btn_sub"), url=CHANNEL_URL)],
    ]

    if show_trial:
        rows.append([InlineKeyboardButton(text="🎁 Пробный доступ (24h)", callback_data=CB_TRIAL_START)])

    # check
    rows.append([InlineKeyboardButton(text=t_local(lang, "btn_check"), callback_data=CB_PREMIUM_CHECK)])

    # pay by card (webapp)
    rows.append(
        [InlineKeyboardButton(text=t_local(lang, "btn_open"), web_app=WebAppInfo(url=_webapp_url(tg_id, lang)))]
    )
    if show_details:
        rows.append([InlineKeyboardButton(text=t_local(lang, "btn_more"), callback_data=CB_PREMIUM_DETAILS)])

    # refund
    rows.append(
        [
            InlineKeyboardButton(
                text={
                    "ru": "💸 Возврат средств",
                    "uk": "💸 Повернення коштів",
                    "en": "💸 Refund",
                }.get(lang, "💸 Возврат средств"),
                callback_data="refund:open",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _t_cancel_label(lang: str) -> str:
    loc = (lang or "ru")[:2].lower()
    if loc == "ua":
        loc = "uk"
    labels = {
        "ru": "🔄 Продлить подписку",
        "uk": "🔄 Продовжити підписку",
        "en": "🔄 Renew subscription",
    }
    return labels.get(loc, labels["ru"])


def _fmt_left(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m = seconds // 60
    h = m // 60
    m = m % 60
    if h > 0:
        return f"{h}ч {m}м"
    if m > 0:
        return f"{m}м"
    return f"{seconds}с"


async def _user_is_channel_member(bot, user_id: int) -> bool:
    """Проверяем, подписан ли юзер на премиум-канал."""
    try:
        cm = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        status = getattr(cm, "status", None)
        status = getattr(status, "value", status)
        return str(status) in {"member", "administrator", "creator"}
    except Exception:
        return False


async def _grant_24h(session: AsyncSession, tg_id: int) -> bool:
    """Выдаём 24 часа премиума, если триал ещё не использован."""
    user = await _fetch_user(session, tg_id)
    if user.get("premium_trial_given"):
        return False

    until = datetime.now(timezone.utc) + timedelta(days=1)
    await session.execute(
        sql_text("UPDATE users SET is_premium=True, premium_until=:u, premium_trial_given=True WHERE tg_id=:tg"),
        {"u": until, "tg": tg_id},
    )
    await session.commit()
    return True


async def _cancel_subscription(session: AsyncSession, tg_id: int) -> bool:
    """
    MVP отмены: отключаем auto_renew и переводим активную подписку в canceled.
    Не режем доступ сразу — он останется до expires_at.
    """
    # тут предполагаем, что tg_id = user_id в твоих SQLAlchemy моделях может отличаться,
    # поэтому работаем через users.id:
    user = await _fetch_user(session, tg_id)
    user_db_id = user.get("id")

    if not user_db_id:
        return False

    # берём активную подписку по user_id
    q = sql_text("UPDATE subscriptions SET auto_renew=false, status='canceled' WHERE user_id=:uid AND status='active' ")
    res = await session.execute(q, {"uid": user_db_id})
    await session.commit()

    # Если реально ничего не обновилось — активной подписки нет
    try:
        return (res.rowcount or 0) > 0
    except Exception:
        return True  # fallback для редких драйверов


async def maybe_grant_trial(session: AsyncSession, tg_id: int) -> None:
    """
    Мягко выдать триал, если:
    - премиум не активен
    - триал ещё не использован
    """
    user = await _fetch_user(session, tg_id)
    if not _is_active(user) and not user.get("premium_trial_given"):
        await _grant_24h(session, tg_id)


async def _log_event(session: AsyncSession, tg_id: int, name: str, meta: str | None = None) -> None:
    payloads = [
        (
            "INSERT INTO events (tg_id, name, meta, created_at) VALUES (:tg, :n, :m, :ts)",
            {"tg": tg_id, "n": name, "m": meta, "ts": datetime.now(timezone.utc)},
        ),
        (
            "INSERT INTO events (tg_id, name, created_at) VALUES (:tg, :n, :ts)",
            {"tg": tg_id, "n": name, "ts": datetime.now(timezone.utc)},
        ),
        ("INSERT INTO events (tg_id, name) VALUES (:tg, :n)", {"tg": tg_id, "n": name}),
    ]
    for q, params in payloads:
        try:
            await session.execute(sql_text(q), params)
            await session.commit()
            return
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass


def _build_menu_short(lang: str, user: Dict[str, Any]) -> str:
    """
    Укороченный апсейл-экран для предпродажи (только если премиум НЕ активен).
    Цель: быстро объяснить ценность и дать 1 сильный CTA.
    """
    loc = _normalize_lang(lang)
    title = {"ru": "💎 Премиум-доступ", "uk": "💎 Преміум-доступ", "en": "💎 Premium access"}.get(
        loc, "💎 Премиум-доступ"
    )

    return f"{title}\n\n{t_local(loc, 'presale_lines')}"


def _build_menu(lang: str, user: Dict[str, Any]) -> str:
    """Текст меню премиума (локализованный)."""
    loc = _normalize_lang(lang)
    active = _is_active(user)
    tz_name = user.get("tz") or getattr(settings, "default_tz", "Europe/Kyiv")
    until = _to_dt_aware(user.get("premium_until"))

    left_line = ""
    if active and user.get("premium_trial_given") and until:
        now = datetime.now(timezone.utc)
        if now < until:
            left = int((until - now).total_seconds())
            if loc == "en":
                left_line = f"⏳ Free left: {_fmt_left(left)}"
            elif loc == "uk":
                left_line = f"⏳ Безкоштовно лишилось: {_fmt_left(left)}"
            else:
                left_line = f"⏳ Осталось бесплатно: {_fmt_left(left)}"

    if loc == "uk":
        title = "💎 Преміум-доступ"
        free_title = "Безкоштовно:"
        premium_title = "Преміум:"
        free = [
            "✅ Щоденний журнал /journal",
            "✅ Нагадування /remind",
            "✅ Базова статистика /stats",
            "✅ Базові медитації та музика",
            "✅ Калорії текстом",
        ]
        premium = [
            "🔐 Розширені нагадування",
            "🔐 Преміум-медитації та плейлисти",
            "🔐 Розширена статистика",
            "🔐 Калорії з фото",
            "🔐 Пріоритетна підтримка",
        ]
        unlocked_cta = "У тебе вже є преміум — всі функції розблоковані 💚"
        locked_cta = "Щоб відкрити замочки, оформи преміум нижче — обери тариф нижче 👇"
        trial_hint = "Можна отримати 24 години преміуму: підпишись на канал і натисни «Перевірити»."
        status_active = (
            f"Статус: активний до {_fmt_local(until, tz_name)} ({tz_name}) ✅" if until else "Статус: активний ✅"
        )
        status_inactive = "Статус: не активний 🔒"

    elif loc == "en":
        title = "💎 Premium access"
        free_title = "Free:"
        premium_title = "Premium:"
        free = [
            "✅ Daily journal /journal",
            "✅ Reminders /remind",
            "✅ Basic statistics /stats",
            "✅ Basic meditations and music",
            "✅ Text calories",
        ]
        premium = [
            "🔐 Advanced reminders",
            "🔐 Premium meditations and playlists",
            "🔐 Extended statistics",
            "🔐 Photo calories",
            "🔐 Priority support",
        ]
        unlocked_cta = "You already have Premium — everything is unlocked 💚"
        locked_cta = "To unlock everything, activate Premium below — choose a plan below 👇"
        trial_hint = "You can get 24 hours of Premium: subscribe to the channel and tap “Check”."
        status_active = (
            f"Status: active until {_fmt_local(until, tz_name)} ({tz_name}) ✅" if until else "Status: active ✅"
        )
        status_inactive = "Status: not active 🔒"

    else:
        title = "💎 Премиум-доступ"
        free_title = "Бесплатно:"
        premium_title = "Премиум:"
        free = [
            "✅ Ежедневный журнал /journal",
            "✅ Напоминания /remind",
            "✅ Базовая статистика /stats",
            "✅ Базовые медитации и музыка",
            "✅ Калории текстом",
        ]
        premium = [
            "🔐 Расширенные напоминания",
            "🔐 Премиум-медитации и плейлисты",
            "🔐 Расширенная статистика",
            "🔐 Калории по фото",
            "🔐 Приоритетная поддержка",
        ]
        unlocked_cta = "У тебя уже есть премиум — все функции разблокированы 💚\n\n<i>💸 Возврат средств доступен в течение 48 часов после оплаты.</i>"
        locked_cta = "Чтобы открыть замочки, оформи премиум ниже — выбором тарифа ниже 👇"
        trial_hint = "Можно получить 24 часа премиума: подпишись на канал и нажми «Проверить»."
        status_active = (
            f"Статус: активен до {_fmt_local(until, tz_name)} ({tz_name}) ✅" if until else "Статус: активен ✅"
        )
        status_inactive = "Статус: не активен 🔒"

    free_block = "\n".join(free)

    if active:
        premium_block = "\n".join(s.replace("🔐", "✅") for s in premium)
        cta = unlocked_cta
        status_line = status_active
    else:
        premium_block = "\n".join(premium)
        cta = locked_cta
        status_line = status_inactive

    return (
        f"{title}\n\n"
        f"{status_line}\n" + (f"{left_line}\n" if left_line else "") + "\n"
        f"{free_title}\n{free_block}\n\n"
        f"{premium_title}\n{premium_block}\n\n"
        f"{cta}\n\n" + (f"{trial_hint}" if not active else "")
    )


# ===== Хэндлеры =====


@router.message(StateFilter("*"), Command("premium"))
@router.message(F.text.lower().in_({"💎 premium", "premium", "премиум", "💎 премиум", "преміум", "💎 преміум"}))
async def cmd_premium(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, m.from_user.id)
    lang_code = _lang_of(user, m, fallback=lang)
    text = _build_menu(lang_code, user) if _is_active(user) else _build_menu_short(lang_code, user)
    active = _is_active(user)
    _resolve_is_admin(m.from_user.id, user)

    if active:
        kb = _active_premium_kb(lang_code, m.from_user.id)
    else:
        kb = _subscribe_kb(
            lang_code,
            m.from_user.id,
            show_trial=not user.get("premium_trial_given"),
            show_details=True,
            show_stars=False,
        )

    await m.answer(text, reply_markup=kb, parse_mode="HTML")


# ✅ КРИТИЧНО ДЛЯ V2: обработчик апсейл-кнопки
@router.callback_query(F.data == CB_OPEN_PREMIUM)
async def open_premium_cb(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)
    text = _build_menu(lang_code, user) if _is_active(user) else _build_menu_short(lang_code, user)
    active = _is_active(user)
    _resolve_is_admin(c.from_user.id, user)

    if active:
        kb = _active_premium_kb(lang_code, c.from_user.id)
    else:
        kb = _subscribe_kb(
            lang_code,
            c.from_user.id,
            show_trial=not user.get("premium_trial_given"),
            show_details=True,
            show_stars=False,
        )

    await c.answer()
    if c.message:
        await cb_reply(c, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == CB_PREMIUM_DETAILS)
async def premium_details_cb(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)

    text = _build_menu(lang_code, user)
    kb = _subscribe_kb(
        lang_code, c.from_user.id, show_trial=not user.get("premium_trial_given"), show_details=False, show_stars=True
    )

    await c.answer()
    if c.message:
        await cb_reply(c, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == CB_TRIAL_START)
async def trial_start_cb(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)

    await c.answer()
    await _log_event(session, c.from_user.id, "trial_click")
    if not c.message:
        return

    await cb_reply(
        c,
        {
            "ru": "🎁 Пробный доступ на 24 часа:\n1) Подпишись на канал\n2) Нажми «Проверить» ✅",
            "uk": "🎁 Пробний доступ на 24 години:\n1) Підпишись на канал\n2) Натисни «Перевірити» ✅",
            "en": "🎁 24h trial:\n1) Subscribe to the channel\n2) Tap “Check” ✅",
        }.get(lang_code, "🎁 Trial: subscribe then tap Check ✅"),
        reply_markup=_subscribe_kb(lang_code, c.from_user.id, show_trial=not user.get("premium_trial_given")),
    )


@router.callback_query(F.data == CB_PREMIUM_CHECK)
async def premium_check(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)
    is_admin = _resolve_is_admin(c.from_user.id, user)

    try:
        is_member = await _user_is_channel_member(c.bot, c.from_user.id)
        await c.answer()
    except Exception:
        is_member = False
        await c.answer()

    if not c.message:
        return

    if is_member:
        granted = await _grant_24h(session, c.from_user.id)
        if granted:
            await _log_event(session, c.from_user.id, "trial_granted")
            await cb_reply(
                c,
                t_local(lang_code, "sub_given"),
                reply_markup=get_main_kb(lang_code, is_premium=True, is_admin=is_admin),
            )
        else:
            await _log_event(session, c.from_user.id, "trial_denied", meta="used")
            await cb_reply(
                c,
                t_local(lang_code, "trial_used"),
                reply_markup=_pay_kb(lang_code, c.from_user.id, is_premium=_is_active(user)),
            )
    else:
        await _log_event(session, c.from_user.id, "trial_denied", meta="not_member")
        await cb_reply(
            c,
            t_local(lang_code, "sub_not_found"),
            reply_markup=_subscribe_kb(
                lang_code,
                c.from_user.id,
                show_trial=not user.get("premium_trial_given"),
            ),
        )


def _cancel_confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отменить", callback_data=CB_SUB_CANCEL_CONFIRM)],
            [InlineKeyboardButton(text="↩️ Назад", callback_data=CB_OPEN_PREMIUM)],
        ]
    )


@router.callback_query(F.data == CB_SUB_CANCEL)
async def sub_cancel_ask(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)

    await c.answer()
    if c.message:
        await cb_reply(
            c,
            {
                "ru": "Автопродления нет 🙅‍♂️\nПодписка просто завершится в срок. Чтобы продлить её, нажми «💎 Премиум».",
                "uk": "Точно вимикаємо автопродовження? Преміум буде активним до кінця оплаченого періоду.",
                "en": "Confirm cancel auto-renew? Premium will stay active until the end of the paid period.",
            }.get(lang_code, "Точно отменяем автопродление?"),
            reply_markup=_cancel_confirm_kb(lang_code),
        )


@router.callback_query(F.data == CB_SUB_CANCEL_CONFIRM)
async def sub_cancel_confirm(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    user = await _fetch_user(session, c.from_user.id)
    lang_code = _lang_of(user, c, fallback=lang)

    ok = await _cancel_subscription(session, c.from_user.id)
    # ЗУПИНКА АВТОСПИСАННЯ В МОНОБАНКУ
    import httpx
    token = os.getenv("MONO_TOKEN")
    # Шукаємо останню активну підписку (external_id має починатися на s2_)
    # Тут логіка має знайти pay.external_id для підписки
    try:
        # Для MVP: якщо у нас збережений subscriptionId, шлемо його в /subscription/cancel
        # (Потребує збереження subscriptionId в БД при оплаті)
        pass
    except Exception: pass


    await c.answer()
    if not c.message:
        return

    if not ok:
        await cb_reply(
            c,
            {
                "ru": "У тебя нет активной подписки 🙂",
                "uk": "У тебе немає активної підписки 🙂",
                "en": "You have no active subscription 🙂",
            }.get(lang_code, "No active subscription 🙂"),
        )
        return

    await cb_reply(
        c,
        {
            "ru": "✅ Автопродление отключено. Премиум действует до конца оплаченного периода.",
            "uk": "✅ Автопродовження вимкнено. Преміум діє до кінця оплаченого періоду.",
            "en": "✅ Auto-renew is off. Premium stays active until the end of the paid period.",
        }.get(lang_code, "✅ Done"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💎 Премиум", callback_data=CB_OPEN_PREMIUM)]]
        ),
    )


@router.message(Command("premium_reset"))
async def premium_reset(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    # ✅ единая проверка админа
    if not is_admin_tg(m.from_user.id):
        await m.answer("Доступ к сбросу премиума есть только у администратора.")
        return

    parts = (m.text or "").split()
    target_id = m.from_user.id
    hard = False

    for p in parts[1:]:
        if p.lower() == "hard":
            hard = True
        elif p.lstrip("+-").isdigit():
            target_id = int(p)

    await _ensure_user_columns(session)

    if hard:
        sql = "UPDATE users SET is_premium=0, premium_until=NULL, premium_trial_given=0 WHERE tg_id=:tg"
    else:
        sql = "UPDATE users SET is_premium=0, premium_until=NULL WHERE tg_id=:tg"

    await session.execute(sql_text(sql), {"tg": target_id})
    await session.commit()

    if target_id == m.from_user.id:
        msg = "Премиум и триал полностью сброшены для твоего аккаунта." if hard else "Премиум сброшен."
    else:
        msg = (
            f"Премиум и триал полностью сброшены для пользователя {target_id}."
            if hard
            else f"Премиум сброшен для пользователя {target_id}."
        )

    await m.answer(msg)


def is_premium_btn(text: str) -> bool:
    if not text:
        return False
    t_ = text.strip()
    return t_ in (
        "💎 Премиум",
        "💎 Преміум",
        "💎 Premium",
        "Премиум",
        "Преміум",
        "Premium",
    )


@router.message(F.text.func(lambda s: s and is_premium_btn(s)))
async def premium_button(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    await cmd_premium(m, session, lang)


# Регистрация через кастомный фильтр кнопок (если он есть)
try:
    router.message.register(
        cmd_premium,
        StateFilter("*"),
        Btn("btn_premium"),  # type: ignore
    )
    router.message.register(
        cmd_premium,
        StateFilter("*"),
        Btn("menu_premium"),  # type: ignore
    )
except Exception:
    pass


@router.message(Command("remind_run"))
async def remind_run(m: Message, session: AsyncSession):
    if not is_admin_tg(m.from_user.id):
        await m.answer("Только админ.")
        return
    if m.bot is None:
        await m.answer("Бот не доступен в этом контексте.")
        return
    await run_renewal_reminders(m.bot, session)
    await m.answer("✅ Reminders job выполнен.")
