from __future__ import annotations

"""
Обработка оплаты премиума через Telegram Stars.

Flow:
1) Пользователь нажимает кнопку «⭐ Оплатить Telegram Stars» (callback_data="pay_stars").
2) Мы создаём Payment(provider=PaymentProvider.STARS, currency="XTR") со статусом pending.
3) Шлём invoice через answer_invoice (currency="XTR", amount = кол-во звёзд).
4) Обрабатываем successful_payment:
   - помечаем Payment как paid
   - создаём/продлеваем Subscription через activate_subscription_from_payment()
   - синхронизируем user.premium_until
   - показываем основное меню с уже активным премиумом.
"""

from datetime import datetime, timezone
from typing import Optional
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.payment import Payment, PaymentPlan, PaymentProvider, PaymentStatus
from app.models.user import User
from app.services.pricing import get_spec
from app.services.subscriptions import activate_subscription_from_payment, log_event
from app.utils.aiogram_guards import cb_reply

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

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


router = Router(name="payments_stars")

# must match premium.py
CB_PAY_STARS = "pay_stars"
CB_STARS_BUY_PREFIX = "stars:buy:"

# pricing
PREMIUM_STARS_PLAN = PaymentPlan.MONTH  # must match your plan mapping in subscriptions service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_lang(code: Optional[str]) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith(("ua", "uk")):
        return "uk"
    if c.startswith("en"):
        return "en"
    return "ru"


def _lang_of(user: User, obj: Message | CallbackQuery) -> str:
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
    _lang_of(user, c)

    # показываем выбор пакета
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    def btn(text: str, sku: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"{CB_STARS_BUY_PREFIX}{sku}")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Basic • Month", "basic_month"), btn("Pro • Month", "pro_month")],
            [
                btn("Basic • Quarter", "basic_quarter"),
                btn("Pro • Quarter", "pro_quarter"),
            ],
            [btn("Basic • Year", "basic_year"), btn("Pro • Year", "pro_year")],
        ]
    )
    await cb_reply(c, "⭐ Выбери пакет Stars:", reply_markup=kb)
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


@router.callback_query(F.data.startswith(CB_STARS_BUY_PREFIX))
async def buy_stars_package(c: CallbackQuery, session: AsyncSession) -> None:
    if not c.from_user or not c.message:
        await c.answer()
        return

    sku = (c.data or "").split(CB_STARS_BUY_PREFIX, 1)[-1].strip().lower()
    spec = get_spec(sku)

    if not spec or int(spec.stars or 0) <= 0:
        await c.answer("Пакет недоступен", show_alert=True)
        return

    period_to_plan = {
        "trial": PaymentPlan.TRIAL,
        "month": PaymentPlan.MONTH,
        "quarter": PaymentPlan.QUARTER,
        "year": PaymentPlan.YEAR,
    }
    plan_enum = period_to_plan.get((spec.period or "").strip().lower(), PaymentPlan.MONTH)

    user = await _get_or_create_user(session, c.from_user.id)
    lang = _lang_of(user, c)

    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        plan=plan_enum,
        amount_cents=int(spec.stars),  # для XTR храним кол-во звёзд тут
        currency="XTR",
        sku=sku,
        payload=str({"sku": sku, "tier": spec.tier, "period": spec.period, "days": spec.days}),
        status=PaymentStatus.PENDING,
    )
    session.add(payment)
    await session.flush()  # чтобы точно получить id без сюрпризов
    await session.commit()

    prices = [LabeledPrice(label="XTR", amount=int(spec.stars))]
    payload = f"premium_stars:{payment.id}:{sku}"

    await c.message.answer_invoice(
        title=_invoice_title(lang),
        description=_invoice_desc(lang),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await c.answer()


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

    # payload: premium_stars:<payment_id>:<sku>
    parts = payload.split(":")
    if len(parts) != 3:
        await m.answer(_error_text("ru"))
        return

    try:
        payment_id = int(parts[1])
        sku = (parts[2] or "").strip().lower()
    except Exception:
        await m.answer(_error_text("ru"))
        return

    # 1) достаём payment из БД и проверяем совпадения (главная защита)
    payment = await session.get(Payment, payment_id)
    if not payment:
        await m.answer(_error_text("ru"))
        return

    if payment.provider != PaymentProvider.STARS or payment.currency != "XTR":
        await m.answer(_error_text("ru"))
        return

    db_sku = (getattr(payment, "sku", None) or "").strip().lower()
    if db_sku != sku:
        await m.answer(_error_text("ru"))
        return

    # 2) проверяем сумму по SKU
    spec = get_spec(sku)
    if not spec or int(spec.stars or 0) <= 0:
        await m.answer(_error_text("ru"))
        return

    expected = int(spec.stars)
    if int(sp.total_amount or 0) != expected:
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
    await session.flush()

    # load user
    user = await session.get(User, payment.user_id) if payment.user_id else None
    if not user and m.from_user:
        user = await _get_or_create_user(session, m.from_user.id)

    if not user:
        await session.commit()
        await m.answer(_error_text("ru"))
        return

    # duration from sku
    sku = db_sku
    spec = get_spec(sku)
    if not spec or int(spec.days or 0) <= 0:
        duration = None
    else:
        duration = int(spec.days)

    sub = await activate_subscription_from_payment(
        session,
        user=user,
        payment=payment,
        plan=(payment.plan or PREMIUM_STARS_PLAN),  # enum: TRIAL/MONTH/QUARTER/YEAR
        duration_days=duration,  # реальная длительность из SKU
        auto_renew=False,
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
            "plan": (payment.plan or PREMIUM_STARS_PLAN).value,
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
