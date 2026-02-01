from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentPlan, PaymentStatus
from app.models.user import User
from app.services.analytics_v2 import log_event_v2
from app.services.pricing import get_spec
from app.services.subscriptions import activate_subscription_from_payment

PLAN_DAYS = {
    PaymentPlan.TRIAL: 1,
    PaymentPlan.MONTH: 30,
    PaymentPlan.YEAR: 365,
    # lifetime — отдельная ветка
}


def _val(x):
    """Enum -> str для логов."""
    return getattr(x, "value", x)


async def _log_analytics_safe(
    session: AsyncSession,
    user: Optional[User],
    payment: Payment,
    result: str,
) -> None:
    await log_event_v2(
        session,
        user_id=getattr(user, "id", None),
        event="premium_payment_applied",
        props={
            "provider": _val(payment.provider),
            "plan": _val(payment.plan),
            "status": _val(payment.status),
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
            "payment_id": payment.id,
            "result": result,
        },
    )


async def apply_payment_to_premium(
    session: AsyncSession,
    payment: Payment,
    *,
    commit: bool = True,
) -> bool:
    """
    Применяем PAID-платёж к подписке:
    - TOPUP игнорируем
    - SKU (basic/pro × month/quarter/year) приоритетнее plan
    - TRIAL/MONTH/YEAR/LIFETIME -> activate_subscription_from_payment()
    - всё логируем в analytics_v2
    """

    # 1) базовая валидация
    if payment.status != PaymentStatus.PAID:
        await _log_analytics_safe(
            session, user=None, payment=payment, result="skip_not_paid"
        )
        if commit:
            await session.commit()
        return False

    if not payment.user_id:
        await _log_analytics_safe(
            session, user=None, payment=payment, result="skip_no_user"
        )
        if commit:
            await session.commit()
        return False

    if payment.plan == PaymentPlan.TOPUP:
        await _log_analytics_safe(
            session, user=None, payment=payment, result="skip_topup"
        )
        if commit:
            await session.commit()
        return False

    # 2) грузим user
    user = await session.get(User, payment.user_id)
    if not user:
        await _log_analytics_safe(
            session, user=None, payment=payment, result="skip_user_not_found"
        )
        if commit:
            await session.commit()
        return False

    # 3) применяем SKU (basic/pro × month/quarter/year), если он есть
    sku = (getattr(payment, "sku", None) or "").strip().lower()
    spec = get_spec(sku) if sku else None

    if spec and int(spec.days or 0) > 0:
        await activate_subscription_from_payment(
            session,
            user,
            payment,
            plan=sku,
            duration_days=int(spec.days),
            auto_renew=False,
        )
        # фиксируем tier на пользователе (если поле есть)
        try:
            user.premium_plan = spec.tier
        except Exception:
            pass
        result = f"sku_{sku}"

    else:
        # 4) применяем plan -> подписка
        if payment.plan == PaymentPlan.LIFETIME:
            await activate_subscription_from_payment(
                session,
                user,
                payment,
                plan=PaymentPlan.LIFETIME,
                duration_days=None,
                auto_renew=False,
            )
            result = "lifetime_granted"

        else:
            days = PLAN_DAYS.get(payment.plan)
            if not days:
                await _log_analytics_safe(
                    session, user=user, payment=payment, result="skip_unknown_plan"
                )
                if commit:
                    await session.commit()
                return False

            await activate_subscription_from_payment(
                session,
                user,
                payment,
                plan=_val(
                    payment.plan
                ),  # "trial" / "month" / "year" / "quarter" (если придёт как строка)
                duration_days=None,  # PLAN_DAYS_MAP решит по ключу или ставь days, если хочешь жёстко
                auto_renew=False,
            )
            result = f"extended_{days}_days"

    # 5) логируем
    await _log_analytics_safe(session, user=user, payment=payment, result=result)

    if commit:
        await session.commit()

    return True
