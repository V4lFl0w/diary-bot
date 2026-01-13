# app/handlers/premium.py
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.user import User
from app.models.journal import JournalEntry
from app.models.reminder import Reminder
from app.config import settings

from app.services.payments.coinbase import create_coinbase_charge, build_pay_kb

router = Router()

TEXTS = {
    "ru": {
        "trial_on":  "🎁 Подарок: {hours} ч Премиума активированы! Доступны экспорт и расширенные отчёты.",
        "status_on": "💎 Премиум активен до {until} ({tz}). Цена после — ${price}/мес.",
        "status_off":"Премиум не активен. ${price}/мес. Откроются экспорт и расширенные отчёты.",
        "start":     "/start",
    },
    "uk": {
        "trial_on":  "🎁 Подарунок: Преміум на {hours} год активовано! Доступні експорт і розширені звіти.",
        "status_on": "💎 Преміум активний до {until} ({tz}). Далі — ${price}/міс.",
        "status_off":"Преміум не активний. ${price}/міс. Відкриються експорт і розширені звіти.",
        "start":     "/start",
    },
    "en": {
        "trial_on":  "🎁 Gift: Premium for {hours}h activated! Export & advanced reports unlocked.",
        "status_on": "💎 Premium is active until {until} ({tz}). After that — ${price}/mo.",
        "status_off":"Premium is inactive. ${price}/mo. Export & advanced reports will be available.",
        "start":     "/start",
    },
}

def _t(lang: str, key: str, **kw) -> str:
    return TEXTS.get(lang, TEXTS["ru"]).get(key, key).format(**kw)

def _pick_lang(user: User | None, fallback: str = None) -> str:
    loc = (getattr(user, "locale", None) or fallback or settings.default_locale or "ru").lower()
    return loc if loc in TEXTS else "ru"

def is_premium_active(user: User, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return bool(user.premium_until and user.premium_until > now)

async def maybe_grant_trial(session: AsyncSession, user: User, bot) -> None:
    """
    Даём триал 1 раз, когда у юзера ≥2 записей и ≥1 напоминание.
    Длительность берётся из settings.premium_trial_hours.
    """
    if user.premium_trial_granted:
        return

    j_count = (await session.execute(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user.id)
    )).scalar() or 0

    r_count = (await session.execute(
        select(func.count()).select_from(Reminder).where(Reminder.user_id == user.id)
    )).scalar() or 0

    if j_count < 2 or r_count < 1:
        return

    user.premium_trial_granted = True
    user.premium_until = datetime.now(timezone.utc) + timedelta(hours=settings.premium_trial_hours)
    session.add(user)
    await session.commit()

    lang = _pick_lang(user)
    try:
        await bot.send_message(
            user.tg_id,
            _t(lang, "trial_on", hours=settings.premium_trial_hours),
        )
    except Exception:
        pass

@router.message(Command("premium"))
async def premium_status(m: Message, session: AsyncSession, lang: str | None = None):
    res = await session.execute(select(User).where(User.tg_id == m.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        return await m.answer(_t("ru", "start"))

    lang = lang or _pick_lang(user)

    if is_premium_active(user):
        try:
            tz = ZoneInfo(user.tz or settings.default_tz)
        except Exception:
            tz = ZoneInfo(settings.default_tz)
        local_until = user.premium_until.astimezone(tz)
        await m.answer(
            _t(
                lang,
                "status_on",
                until=local_until.strftime("%Y-%m-%d %H:%M"),
                tz=(user.tz or settings.default_tz),
                price=settings.premium_price_usd,
            )
        )
    else:
        await m.answer(_t(lang, "status_off", price=settings.premium_price_usd))

def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    if lang == "uk":
        return uk
    if lang == "en":
        return en
    return ru

from sqlalchemy import select
from app.models.user import User

@router.message(Command("buy"))
async def premium_buy(m: Message, session: AsyncSession, lang: str | None = None):
    user = (await session.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one_or_none()
    if not user:
        return await m.answer("/start")
    lang = lang or (getattr(user, "locale", None) or "ru")
    price = settings.premium_price_usd
    try:
        pay, hosted = await create_coinbase_charge(session=session, user=user, plan="monthly", amount_usd=price, description="Diary Assistant Premium — 1 month")
    except Exception:
        return await m.answer(_tr(lang, "Ошибка при создании счёта. Попробуйте позже.", "Помилка при створенні рахунку. Спробуйте пізніше.", "Failed to create invoice. Try again later."))
    await m.answer(_tr(lang, f"Премиум на 30 дней — ${price}. Нажми, чтобы оплатить:", f"Преміум на 30 днів — ${price}. Натисни, щоб оплатити:", f"Premium for 30 days — ${price}. Tap to pay:"), reply_markup=build_pay_kb(hosted))
