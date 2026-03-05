from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.subscriptions import (
    EV_EXPIRES_TODAY,
    EV_RENEW_1D,
    EV_RENEW_3D,
    _already_notified,
    get_subscriptions_for_renewal_reminders,
    log_event,
    utcnow,
)
from app.urls import public_pay_url


def _normalize_lang(code: str | None) -> str:
    l = (code or "ru").strip().lower()
    if l.startswith(("ua", "uk")):
        return "uk"
    if l.startswith("en"):
        return "en"
    return "ru"


def _stars_label(lang: str) -> str:
    loc = _normalize_lang(lang)
    return {
        "ru": "⭐ Оплатить Stars",
        "uk": "⭐ Оплатити Stars",
        "en": "⭐ Pay with Stars",
    }[loc]


def _pay_kb_job(lang: str, tg_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from aiogram.types.web_app_info import WebAppInfo
    import os

    loc = _normalize_lang(lang)
    pay_text = {"ru": "💎 Открыть меню Premium", "uk": "💎 Відкрити меню Premium", "en": "💎 Open Premium Menu"}[loc]

    rows = []

    # Берем ссылку на твой WebApp из .env (или собираем вручную)
    base_url = (os.getenv("PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or "https://diarybot.com").strip().rstrip("/")
    webapp_url = f"{base_url}/premium?tg_id={tg_id}"
    
    # Кнопка WebApp (как в главном меню)
    rows.append([InlineKeyboardButton(text=pay_text, web_app=WebAppInfo(url=webapp_url))])

    # Кнопку Stars пока оставляем, если она нужна
    rows.append([InlineKeyboardButton(text=_stars_label(loc), callback_data="pay_stars")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _msg(lang: str, key: str, date_str: str) -> str:
    l = _normalize_lang(lang)
    if key == EV_RENEW_3D:
        return {
            "ru": f"⏳ Премиум закончится {date_str}.\nЧтобы не потерять доступ — продли подписку 👇",
            "uk": f"⏳ Преміум закінчиться {date_str}.\nЩоб не втратити доступ — продовж підписку 👇",
            "en": f"⏳ Premium expires on {date_str}.\nRenew to keep access 👇",
        }[l]
    if key == EV_RENEW_1D:
        return {
            "ru": f"⚠️ Завтра заканчивается премиум ({date_str}). Продлить? 👇",
            "uk": f"⚠️ Завтра закінчується преміум ({date_str}). Продовжити? 👇",
            "en": f"⚠️ Premium ends tomorrow ({date_str}). Renew? 👇",
        }[l]
    # today
    return {
        "ru": "🚫 Премиум заканчивается сегодня. Чтобы функции не закрылись — продли 👇",
        "uk": "🚫 Преміум закінчується сьогодні. Щоб функції не закрились — продовж 👇",
        "en": "🚫 Premium expires today. Renew to keep features 👇",
    }[l]


async def run_renewal_reminders(
    bot: Bot,
    session: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> None:
    now_dt = utcnow() if now is None else now  # если захочешь подставлять время в тестах

    buckets = await get_subscriptions_for_renewal_reminders(session, now=now_dt)

    # дедуп-окно: сутки
    since = now_dt - timedelta(hours=28)

    async def _send(sub, event_name: str):
        # грузим юзера по user_id из sub
        res = await session.execute(select(User).where(User.id == sub.user_id))
        u = res.scalar_one_or_none()
        if not u:
            return

        # не шлём, если недавно уже слали этот тип уведомления
        if await _already_notified(session, u.id, event_name, since=since):
            return

        lang = getattr(u, "lang", None) or getattr(u, "locale", None) or "ru"
        lang = _normalize_lang(lang)

        exp = sub.expires_at
        date_str = exp.strftime("%Y-%m-%d") if exp else ""

        text = _msg(lang, event_name, date_str)

        # показываем оплату (и cancel-кнопка сама появится, если is_premium=True — но тут нам не важно)
        kb = _pay_kb_job(lang, u.tg_id)

        try:
            await bot.send_message(u.tg_id, text, reply_markup=kb)
        except Exception:
            # не валим job если юзер заблокировал бота
            return

        await log_event(
            session,
            user_id=u.id,
            event=event_name,
            props={
                "sub_id": sub.id,
                "plan": sub.plan,
                "expires_at": exp.isoformat() if exp else None,
                "status": sub.status,
            },
        )

    # отправляем
    for s in buckets["3d"]:
        await _send(s, EV_RENEW_3D)
    for s in buckets["1d"]:
        await _send(s, EV_RENEW_1D)
    for s in buckets["today"]:
        await _send(s, EV_EXPIRES_TODAY)

    await session.commit()
