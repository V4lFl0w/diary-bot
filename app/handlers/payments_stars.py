from __future__ import annotations

"""
Обработка оплаты премиума через Telegram Stars.

Flow:
1) Пользователь нажимает кнопку «⭐ Оплатить Telegram Stars» (callback_data="pay_stars").
2) Мы создаём Payment(provider="STARS", currency="XTR") со статусом pending.
3) Шлём invoice через answer_invoice (currency="XTR", amount = кол-во звёзд).
4) Обрабатываем successful_payment:
   - помечаем Payment как paid
   - создаём/продлеваем Subscription через activate_subscription_from_payment()
   - синхронизируем user.premium_until
   - показываем основное меню с уже активным премиумом.
"""

from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    LabeledPrice,
    PreCheckoutQuery,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models.user import User
from app.models.payment import Payment, PaymentStatus
from app.services.subscriptions import activate_subscription_from_payment, log_event

# main kb (optional)
try:
    from app.keyboards import get_main_kb  # type: ignore
except Exception:  # pragma: no cover
    def get_main_kb(lang: str, is_premium: bool = False, is_admin: bool = False):
        return None

# reply-keyboard button detector (optional)
try:
    from app.keyboards import is_premium_stars_btn  # type: ignore
except Exception:  # pragma: no cover
    def is_premium_stars_btn(_: str) -> bool:
        return False

# admin checker (optional)
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover
    def is_admin_tg(_: int) -> bool:
        return False


router = Router(name="payments_stars")

# must match premium.py
CB_PAY_STARS = "pay_stars"

# pricing
PREMIUM_STARS_PRICE = 499  # stars count (XTR)
PREMIUM_STARS_PLAN = "month"  # must match your plan mapping in subscriptions service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_lang(code: Optional[str]) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith(("ua", "uk")):
        return "uk"
    if c.startswith("en"):
        return "en"
    return "ru"


def _lang_of(user: Optional[User], obj: Message | CallbackQuery) -> str:
    if user:
        if getattr(user, "locale", None):
            return _normalize_lang(user.locale)
        if getattr(user, "lang", None):
            return _normalize_lang(user.lang)

    fu = getattr(obj, "from_user", None)
    if not fu and isinstance(obj, CallbackQuery):
        fu = getattr(getattr(obj, "message", None), "from_user", None)

    if fu and getattr(fu, "language_code", None):
        return _normalize_lang(fu.language_code)

    return _normalize_lang(getattr(settings, "default_locale", "ru"))


def _success_text(lang: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return "Дякуємо за оплату ⭐\n\nПреміум активовано або продовжено ✅"
    if l == "en":
        return "Thanks for your payment ⭐\n\nPremium has been activated or extended ✅"
    return "Спасибо за оплату ⭐\n\nПремиум активирован или продлён ✅"


def _error_text(lang: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return "Не вдалося завершити оплату. Якщо повторюється — напишіть у підтримку."
    if l == "en":
        return "Failed to finalize the payment. If it persists, contact support."
    return "Не удалось завершить оплату. Если повторяется — напиши в поддержку."


async def _get_or_create_user(session: AsyncSession, tg_id: int) -> User:
    res = await session.execute(select(User).where(User.tg_id == tg_id))
    user = res.scalar_one_or_none()
    if user:
        return user

    default_lang = _normalize_lang(getattr(settings, "default_locale", "ru"))
    user = User(
        tg_id=tg_id,
        locale=default_lang,
        lang=default_lang,
        tz=getattr(settings, "default_tz", "Europe/Kyiv"),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _invoice_title(lang: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return "Місяць преміуму"
    if l == "en":
        return "1 month of Premium"
    return "Месяц премиума"


def _invoice_desc(lang: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return "Доступ до всіх преміум-функцій щоденника на 30 днів."
    if l == "en":
        return "Access to all premium journal features for 30 days."
    return "Доступ ко всем премиум-функциям дневника на 30 дней."


@router.callback_query(F.data == CB_PAY_STARS)
async def pay_premium_with_stars(c: CallbackQuery, session: AsyncSession) -> None:
    if not c.from_user or not c.message:
        await c.answer()
        return

    user = await _get_or_create_user(session, c.from_user.id)
    lang = _lang_of(user, c)

    # 1) create pending payment and persist it
    payment = Payment(
        user_id=user.id,
        provider="STARS",
        plan=PREMIUM_STARS_PLAN,
        amount_cents=int(PREMIUM_STARS_PRICE),  # for XTR we store stars count here
        currency="XTR",
        status=PaymentStatus.PENDING,
        external_id=None,
        payload=None,
    )
    session.add(payment)
    await session.flush()  # get id
    payment_id = payment.id
    await session.commit()  # IMPORTANT: persist before invoice

    payload = f"premium_stars:{payment_id}"
    prices = [LabeledPrice(label="XTR", amount=int(PREMIUM_STARS_PRICE))]

    await c.message.answer_invoice(
        title=_invoice_title(lang),
        description=_invoice_desc(lang),
        prices=prices,
        currency="XTR",
        provider_token="",  # Stars
        payload=payload,
    )

    # analytics: user clicked pay (invoice shown)
    await log_event(
        session,
        user_id=user.id,
        event="pay_click",
        props={
            "provider": "stars",
            "plan": PREMIUM_STARS_PLAN,
            "stars_amount": int(PREMIUM_STARS_PRICE),
            "currency": "XTR",
        },
    )
    await session.commit()

    await c.answer()


@router.message(F.text.func(is_premium_stars_btn))
async def pay_premium_with_stars_from_text(m: Message, session: AsyncSession) -> None:
    if not m.from_user:
        return

    class _FakeCallback:
        from_user = m.from_user
        message = m
        data = CB_PAY_STARS

        async def answer(self, *_, **__):
            pass

    await pay_premium_with_stars(_FakeCallback(), session=session)


@router.pre_checkout_query()
async def process_pre_checkout(q: PreCheckoutQuery) -> None:
    payload = q.invoice_payload or ""
    ok = payload.startswith("premium_stars:")
    await q.answer(ok=ok)


@router.message(F.successful_payment)
async def on_successful_payment_stars(m: Message, session: AsyncSession) -> None:
    sp = m.successful_payment
    if not sp:
        return

    # only Stars + our payload
    if sp.currency != "XTR":
        return

    payload = sp.invoice_payload or ""
    if not payload.startswith("premium_stars:"):
        return

    # amount check (anti-tamper)
    if int(sp.total_amount or 0) != int(PREMIUM_STARS_PRICE):
        await m.answer(_error_text("ru"))
        return

    try:
        payment_id = int(payload.split(":", 1)[1])
    except Exception:
        await m.answer(_error_text("ru"))
        return

    payment = await session.get(Payment, payment_id)
    if not payment:
        await m.answer(_error_text("ru"))
        return

    # idempotent: already paid -> just show success
    if payment.status == PaymentStatus.PAID:
        user = await session.get(User, payment.user_id) if payment.user_id else None
        if not user and m.from_user:
            user = await _get_or_create_user(session, m.from_user.id)

        lang = _lang_of(user, m) if user else "ru"
        is_admin = is_admin_tg(m.from_user.id) if m.from_user else False
        kb = get_main_kb(lang, is_premium=True, is_admin=is_admin) if get_main_kb else None
        await m.answer(_success_text(lang), reply_markup=kb)
        return

    # mark paid
    payment.status = PaymentStatus.PAID
    payment.paid_at = _utcnow()
    payment.external_id = sp.telegram_payment_charge_id
    session.add(payment)

    # load user
    user = await session.get(User, payment.user_id) if payment.user_id else None
    if not user and m.from_user:
        user = await _get_or_create_user(session, m.from_user.id)

    if not user:
        await session.commit()
        await m.answer(_error_text("ru"))
        return

    # activate / extend subscription
    sub = await activate_subscription_from_payment(
        session,
        user=user,
        payment=payment,
        plan=payment.plan or PREMIUM_STARS_PLAN,
        duration_days=None,     # let PLAN_DAYS_MAP decide
        auto_renew=False,       # Stars is one-time
    )


    # analytics
    await log_event(
        session,
        user_id=user.id,
        event="payment_stars_success",
        props={
            "payment_id": payment.id,
            "stars_amount": int(sp.total_amount or 0),
            "currency": sp.currency,
            "subscription_id": getattr(sub, "id", None),
            "tg_charge_id": sp.telegram_payment_charge_id,
        },
    )

    # analytics: payment finalized successfully
    await log_event(
        session,
        user_id=user.id,
        event="pay_success",
        props={
            "provider": "stars",
            "plan": payment.plan or PREMIUM_STARS_PLAN,
            "payment_id": payment.id,
            "currency": sp.currency,
            "tg_charge_id": sp.telegram_payment_charge_id,
        },
    )


    await session.commit()

    # UI
    lang = _lang_of(user, m)
    is_admin = is_admin_tg(m.from_user.id) if m.from_user else False
    kb = get_main_kb(lang, is_premium=True, is_admin=is_admin) if get_main_kb else None
    await m.answer(_success_text(lang), reply_markup=kb)