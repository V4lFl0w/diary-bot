# app/handlers/premium.py
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re, unicodedata as _ud

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters import or_f
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sql_text

from app.config import settings
from app.keyboards import get_main_kb, is_premium_btn

logger = logging.getLogger(__name__)
router = Router()

def _is_premium_trigger(text: str | None) -> bool:
    if not text:
        return False
    s = _ud.normalize('NFKC', str(text)).strip().lower()
    s = re.sub(r'[^\wа-яіїєґё]+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s in {'premium','премиум','преміум'}


# Канал для триала
CHANNEL_USERNAME = (
    os.getenv("PREMIUM_CHANNEL")
    or getattr(settings, "premium_channel", None)
    or "@NoticesDiarY"
)
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"

# i18n fallback
try:
    from app.i18n import t  # type: ignore
except Exception:  # pragma: no cover
    def t(lang: str, key: str, **fmt) -> str:  # type: ignore
        lang = (lang or "ru")[:2].lower()
        D = {
            "premium_on":      {"ru":"Премиум уже активен ✅","uk":"Преміум уже активний ✅","en":"Premium is already active ✅"},
            "premium_on_till": {"ru":"Премиум активен до {dt} ({tz}) ✅","uk":"Преміум активний до {dt} ({tz}) ✅","en":"Premium is active until {dt} ({tz}) ✅"},
            "subscribe_offer": {"ru":"Премиум не активен. Подпишись на наш канал — и получи 24 часа премиума 🎁","uk":"Преміум не активний. Підпишись на наш канал — і отримай 24 години преміуму 🎁","en":"Premium is off. Subscribe to our channel and get 24h of Premium 🎁"},
            "sub_given":       {"ru":"Поздравляю! Подписка подтверждена — премиум активирован на 24 часа ✅","uk":"Вітаю! Підписку підтверджено — преміум активовано на 24 години ✅","en":"Congrats! Subscription confirmed — Premium activated for 24 hours ✅"},
            "sub_not_found":   {"ru":"Не вижу подписки. Нажми «Подписаться», затем «Проверить».","uk":"Не бачу підписки. Натисни «Підписатися», потім «Перевірити».","en":"I can’t see your subscription. Tap “Subscribe” then “Check”."},
            "btn_pay":         {"ru":"Оплатить","uk":"Оплатити","en":"Pay"},
            "btn_sub":         {"ru":"Подписаться","uk":"Підписатися","en":"Subscribe"},
            "btn_check":       {"ru":"Проверить","uk":"Перевірити","en":"Check"},
        }
        val = D.get(key, {}).get(lang) or D.get(key, {}).get("ru") or key
        return val.format(**fmt) if fmt else val

_SUPPORTED = {"ru", "uk", "en"}

def _lang_of(user_row: dict | None, obj: Message | CallbackQuery | None) -> str:
    if isinstance(user_row, dict) and user_row.get("lang"):
        l = (user_row["lang"] or "ru")[:2].lower()
    else:
        code = None
        if isinstance(obj, Message):
            code = getattr(getattr(obj, "from_user", None), "language_code", None)
        elif isinstance(obj, CallbackQuery):
            code = getattr(getattr(obj, "from_user", None), "language_code", None) or \
                   getattr(getattr(getattr(obj, "message", None), "from_user", None), "language_code", None)
        l = (code or getattr(settings, "default_locale", "ru"))[:2].lower()
    return "uk" if l == "ua" else (l if l in _SUPPORTED else "ru")

async def _ensure_cols(session: AsyncSession) -> None:
    try:
        await session.execute(sql_text("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP"))
    except Exception:
        pass
    try:
        await session.execute(sql_text("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0"))
    except Exception:
        pass
    try:
        await session.commit()
    except Exception:
        await session.rollback()

async def _fetch_user(session: AsyncSession, tg_id: int) -> dict:
    await _ensure_cols(session)
    row = (await session.execute(sql_text(
        "SELECT id, tg_id, lang, is_premium, premium_until, tz FROM users WHERE tg_id=:tg"
    ), {"tg": tg_id})).first()
    if row:
        id_, tg, lang, is_premium, premium_until, tz = row
        return {"id": id_, "tg_id": tg, "lang": lang, "is_premium": bool(is_premium),
                "premium_until": premium_until, "tz": tz or getattr(settings, "default_tz", "Europe/Kyiv")}
    await session.execute(sql_text("INSERT INTO users (tg_id, lang, is_premium) VALUES (:tg, :lang, 0)"),
                          {"tg": tg_id, "lang": "ru"})
    await session.commit()
    return {"id": None, "tg_id": tg_id, "lang": "ru", "is_premium": False,
            "premium_until": None, "tz": getattr(settings, "default_tz", "Europe/Kyiv")}

def _aware(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _is_active(u: dict) -> bool:
    if not u.get("is_premium"):
        return False
    until = _aware(u.get("premium_until"))
    return True if until is None else (datetime.now(timezone.utc) < until)

def _fmt_local(dt_utc: datetime, tz_name: str) -> str:
    try:
        return dt_utc.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_utc.astimezone(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M")

def _pay_kb(lang: str, tg_id: int) -> InlineKeyboardMarkup:
    base = getattr(settings, "public_url", None) or os.environ.get("PUBLIC_URL", "").strip()
    if not base.startswith("https://"):
        base = "https://example.com"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_pay"), url=f"{base}/pay?tg_id={tg_id}")]
    ])

def _subscribe_kb(lang: str, tg_id: int) -> InlineKeyboardMarkup:
    base = getattr(settings, "public_url", "") or "https://example.com"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_sub"),   url=CHANNEL_URL)],
        [InlineKeyboardButton(text=t(lang, "btn_check"), callback_data="premium:check")],
        [InlineKeyboardButton(text=t(lang, "btn_pay"),   url=f"{base}/pay?tg_id={tg_id}")],
    ])

async def _grant_24h(session: AsyncSession, tg_id: int) -> None:
    until = datetime.now(timezone.utc) + timedelta(days=1)
    await session.execute(sql_text(
        "UPDATE users SET is_premium=1, premium_until=:u WHERE tg_id=:tg"
    ), {"u": until, "tg": tg_id})
    await session.commit()

# ===== Public API =====
async def maybe_grant_trial(session: AsyncSession, tg_id: int) -> None:
    u = await _fetch_user(session, tg_id)
    if not _is_active(u):
        await _grant_24h(session, tg_id)

# ===== Handlers =====
@router.message(or_f(Command("premium"), F.text.func(_is_premium_trigger)))
async def cmd_premium(m: Message, session: AsyncSession):
    u = await _fetch_user(session, m.from_user.id)
    lang = _lang_of(u, m)

    logger.info("premium_button hit: user=%s lang=%s text=%r", m.from_user.id, lang, m.text)

    if _is_active(u):
        until = _aware(u.get("premium_until"))
        if until:
            dt_local = _fmt_local(until, u.get("tz") or "Europe/Kyiv")
            await m.answer(t(lang, "premium_on_till", dt=dt_local, tz=(u.get("tz") or "Europe/Kyiv")),
                           reply_markup=get_main_kb(lang))
            return
        await m.answer(t(lang, "premium_on"), reply_markup=get_main_kb(lang))
        return

    await m.answer(t(lang, "subscribe_offer"), reply_markup=_subscribe_kb(lang, m.from_user.id))

@router.callback_query(F.data == "premium:check")
async def premium_check(c: CallbackQuery, session: AsyncSession):
    u = await _fetch_user(session, c.from_user.id)
    lang = _lang_of(u, c)

    try:
        cm = await c.bot.get_chat_member(CHANNEL_USERNAME, c.from_user.id)
        status = getattr(cm, "status", None)
        status = getattr(status, "value", status)
        is_member = str(status) in {"member", "administrator", "creator"}
    except Exception:
        is_member = False
    finally:
        try:
            await c.answer()
        except Exception:
            pass

    if is_member:
        await _grant_24h(session, c.from_user.id)
        await c.message.answer(t(lang, "sub_given"), reply_markup=get_main_kb(lang))
    else:
        await c.message.answer(t(lang, "sub_not_found"),
                               reply_markup=_subscribe_kb(lang, c.from_user.id))