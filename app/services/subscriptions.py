from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import AnalyticsEvent
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.user import User

# -------------------------------------------------
# Время
# -------------------------------------------------


def utcnow() -> datetime:
    """Текущий момент в UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """
    SQLite часто возвращает naive datetime.
    Приводим к aware UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# -------------------------------------------------
# Аналитика
# -------------------------------------------------


async def log_event(
    session: AsyncSession,
    user_id: Optional[int],
    event: str,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Сохраняем событие аналитики.
    Важно: commit СНАРУЖИ, чтобы можно было логировать в одной транзакции с платежом.
    """
    e = AnalyticsEvent(
        user_id=user_id,
        event=event,
        props=props or {},
    )
    session.add(e)


# -------------------------------------------------
# Утилиты подписки
# -------------------------------------------------


async def get_active_subscription(
    session: AsyncSession,
    user_id: int,
    now: Optional[datetime] = None,
) -> Optional[Subscription]:
    """
    Возвращает активную подписку пользователя (если есть).

    Условия:
    - status = 'active'
    - либо expires_at IS NULL (бессрочная / lifetime),
      либо expires_at > now.
    """
    now = now or utcnow()

    res = await session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            ((Subscription.expires_at.is_(None)) | (Subscription.expires_at > now)),
        )
    )
    return res.scalar_one_or_none()


async def get_current_subscription(
    session: AsyncSession,
    user_id: int,
    now: Optional[datetime] = None,
) -> Optional[Subscription]:
    """
    Текущая подписка (active или canceled), если она ещё действует.
    Нужна для продления после отмены автопродления.
    """
    now = now or utcnow()
    res = await session.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status.in_(("active", "canceled")),
            ((Subscription.expires_at.is_(None)) | (Subscription.expires_at > now)),
        )
        .order_by(Subscription.expires_at.desc().nullsfirst())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def sync_user_premium_flags(
    session: AsyncSession,
    user: User,
    now: Optional[datetime] = None,
) -> None:
    """
    Синхронизируем user.is_premium / user.premium_until на основе актуальной подписки.

    Логика:
    - если есть активная подписка:
        - expires_at is None  → бессрочный премиум (premium_until=None, is_premium=True)
        - expires_at > now    → премиум до expires_at
    - если подписки нет или она истекла:
        - premium_until=None
        - is_premium=False
    """
    now = now or utcnow()
    sub = await get_current_subscription(session, user.id, now=now)

    exp = _as_aware_utc(sub.expires_at) if sub else None
    now = _as_aware_utc(now) or datetime.now(timezone.utc)
    if sub and (exp is None or exp > now):
        # Активная подписка
        if sub.expires_at is None:
            # lifetime
            user.premium_until = None
        else:
            user.premium_until = sub.expires_at
        user.is_premium = True
        # update tier from subscription plan (basic_/pro_)
        try:
            p = str(getattr(sub, "plan", "") or "").lower()
            if p == "pro" or p.startswith("pro_"):
                user.premium_plan = "pro"
            elif p == "basic" or p.startswith("basic_"):
                user.premium_plan = "free"
        except Exception:
            pass
    else:
        # Нет действующей подписки (active/canceled и не истёкшей)
        # Если был временный премиум (trial/ручной до даты) и он истёк — сбрасываем.
        pu = _as_aware_utc(getattr(user, "premium_until", None))
        if pu is not None and pu <= now:
            user.is_premium = False
            user.premium_until = None
            user.premium_plan = "free"
        else:
            # lifetime (premium_until=None) или ещё не истёкший ручной премиум — не трогаем
            # Но если план завис на pro без активного премиума — чиним
            if (str(getattr(user, "premium_plan", "") or "").lower() == "pro") and (
                not bool(getattr(user, "is_premium", False))
            ):
                user.premium_plan = "free"
        session.add(user)
        return
    session.add(user)
    # commit — снаружи


def _as_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # sqlite часто возвращает naive — считаем, что это UTC
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# -------------------------------------------------
# Маппинг планов → длительность
# -------------------------------------------------

PLAN_DAYS_MAP: Dict[str, int] = {
    # базовые планы
    "trial": 1,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
    # stars-планы (пример, можно расширять)
    "stars_trial": 1,
    "stars_month": 30,
    "stars_year": 365,
    "quarter": 90,
    "stars_quarter": 90,
}


def _guess_duration_days(plan: str, explicit_days: Optional[int]) -> Optional[int]:
    """
    Выбираем длительность плана:
    1) если передали duration_days явно — используем его;
    2) иначе ищем по PLAN_DAYS_MAP;
    3) если не нашли — возвращаем None.
    """
    if explicit_days is not None:
        return explicit_days

    key = (plan or "").strip().lower()
    return PLAN_DAYS_MAP.get(key)


def _is_lifetime_plan(plan: str) -> bool:
    """
    Простая проверка "вечного" плана.
    При необходимости можно добавить свои алиасы.
    """
    p = (plan or "").strip().lower()
    return p in {"lifetime", "life", "permanent", "forever"}


# -------------------------------------------------
# Создание/продление подписки из платежа
# -------------------------------------------------


async def activate_subscription_from_payment(
    session: AsyncSession,
    user: User,
    payment: Payment,
    *,
    plan: Optional[str] = None,
    duration_days: Optional[int] = None,  # лучше None, чтобы PLAN_DAYS_MAP решал сам
    auto_renew: bool = False,
) -> Subscription:
    now = utcnow()
    plan_name = (getattr(payment, "sku", None) or "").strip() or plan or (payment.plan or "premium")

    # 1) Находим "текущую" подписку (active или canceled, но ещё действующая)
    existing_sub = await get_current_subscription(session, user.id, now=now)
    was_existing = bool(existing_sub)

    # 2) lifetime
    if _is_lifetime_plan(plan_name):
        if existing_sub:
            existing_sub.status = "active"
            existing_sub.expires_at = None
            existing_sub.auto_renew = False
            existing_sub.plan = plan_name
            sub = existing_sub
        else:
            sub = Subscription(
                user_id=user.id,
                plan=plan_name,
                status="active",
                started_at=now,
                expires_at=None,
                auto_renew=False,
                source=payment.provider,
            )
            session.add(sub)

        event_name = "sub_renewed" if was_existing else "sub_activated"

        await log_event(
            session,
            user_id=user.id,
            event=event_name,
            props={
                "plan": plan_name,
                "provider": payment.provider,
                "payment_id": payment.id,
                "duration_days": None,
                "auto_renew": False,
                "lifetime": True,
            },
        )

        await sync_user_premium_flags(session, user, now=now)
        return sub

    # 3) обычный план
    effective_days = _guess_duration_days(plan_name, duration_days)
    if not effective_days or effective_days <= 0:
        await log_event(
            session,
            user_id=user.id,
            event="sub_activate_unknown_plan",
            props={
                "plan": plan_name,
                "provider": payment.provider,
                "payment_id": payment.id,
                "duration_days": duration_days,
            },
        )
        effective_days = 1

    if existing_sub:
        # если было canceled — реактивируем
        existing_sub.status = "active"
        base_from = existing_sub.expires_at or now
        existing_sub.expires_at = base_from + timedelta(days=effective_days)
        existing_sub.auto_renew = auto_renew
        existing_sub.plan = plan_name
        sub = existing_sub
    else:
        sub = Subscription(
            user_id=user.id,
            plan=plan_name,
            status="active",
            started_at=now,
            expires_at=now + timedelta(days=effective_days),
            auto_renew=auto_renew,
            source=payment.provider,
        )
        session.add(sub)

    # 4) Лог: activated vs renewed
    await log_event(
        session,
        user_id=user.id,
        event="sub_renewed" if was_existing else "sub_activated",
        props={
            "plan": plan_name,
            "provider": payment.provider,
            "payment_id": payment.id,
            "duration_days": effective_days,
            "auto_renew": auto_renew,
        },
    )

    await sync_user_premium_flags(session, user, now=now)
    return sub


# -------------------------------------------------
# Отмена подписки (отключить автопродление)
# -------------------------------------------------


async def cancel_active_subscription(
    session: AsyncSession,
    user: User,
    now: Optional[datetime] = None,
) -> bool:
    """
    Отключает автопродление и помечает подписку canceled.
    Доступ НЕ режем сразу — действует до expires_at.
    """
    now = now or utcnow()

    sub = await get_current_subscription(session, user.id, now=now)
    if not sub:
        return False

    sub.auto_renew = False
    sub.status = "canceled"
    session.add(sub)

    await log_event(
        session,
        user_id=user.id,
        event="sub_canceled",
        props={
            "sub_id": sub.id,
            "plan": sub.plan,
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        },
    )

    await sync_user_premium_flags(session, user, now=now)
    return True


# какие события будем логировать
EV_RENEW_3D = "sub_renew_remind_3d"
EV_RENEW_1D = "sub_renew_remind_1d"
EV_EXPIRES_TODAY = "sub_expires_today"


async def _already_notified(
    session: AsyncSession,
    user_id: int,
    event: str,
    *,
    since: datetime,
) -> bool:
    """
    Дедуп: если событие уже логировали недавно — не шлём повторно.
    """
    res = await session.execute(
        select(AnalyticsEvent.id)
        .where(
            AnalyticsEvent.user_id == user_id,
            AnalyticsEvent.event == event,
            AnalyticsEvent.ts >= since,
        )
        .limit(1)
    )
    return res.first() is not None


async def get_subscriptions_for_renewal_reminders(
    session: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, list[Subscription]]:
    """
    Возвращает подписки, которым надо отправить напоминания:
      - 3 дня
      - 1 день
      - сегодня истекает
    """
    now = now or utcnow()

    # только те, у которых expires_at задан и ещё не истекли "давно"
    # (lifetime с expires_at=None не трогаем)
    base_q = select(Subscription).where(
        Subscription.status.in_(("active", "canceled")),  # canceled = авто-реню офф, но доступ может быть активен
        Subscription.expires_at.is_not(None),
        Subscription.expires_at > (now - timedelta(days=1)),  # чтобы "сегодня" поймать
    )

    subs = (await session.execute(base_q)).scalars().all()

    bucket_3d: list[Subscription] = []
    bucket_1d: list[Subscription] = []
    bucket_today: list[Subscription] = []

    for s in subs:
        exp = s.expires_at
        if exp:
            exp = ensure_utc(exp)
        if not exp:
            continue

        # если подписка уже кончилась (до now) — можно слать "истёк" отдельно, но MVP пропустим
        if exp <= now:
            # сегодня/уже истекла
            bucket_today.append(s)
            continue

        delta = exp - now
        days = delta.days

        # 3 дня (включая диапазон 72..95 часов нормально ловится по days==3)
        if days == 3:
            bucket_3d.append(s)
        elif days == 1:
            bucket_1d.append(s)
        elif days == 0:
            bucket_today.append(s)

    return {"3d": bucket_3d, "1d": bucket_1d, "today": bucket_today}
