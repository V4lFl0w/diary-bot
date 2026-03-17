from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quota_usage import QuotaUsage
from app.models.user import User


def _is_premium_active(user: Optional[User]) -> bool:
    if not user:
        return False

    try:
        if bool(getattr(user, "is_premium", False)):
            return True
    except Exception:
        pass

    try:
        pu = getattr(user, "premium_until", None)
        if pu is None:
            return False
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        return pu > datetime.now(timezone.utc)
    except Exception:
        return False


def _norm_plan(user: Optional[User]) -> str:
    """
    Возвращает один из:
    free | basic | pro | max
    """
    if not user:
        return "free"

    if not _is_premium_active(user):
        return "free"

    raw = str(getattr(user, "premium_plan", "") or "").strip().lower()

    if raw in {"pro"}:
        return "pro"
    if raw in {"max", "pro_max", "promax", "pro-max"}:
        return "max"
    if raw in {"basic", "trial"}:
        return "basic"

    return "basic"


def _day_bucket_utc() -> str:
    d = datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


DAILY_LIMITS: dict[str, dict[str, int]] = {
    "free": {
        "reminders_daily": 7,
        "journal_entries_daily": 3,
        "journal_voice_daily": 3,
        "journal_ai_daily": 0,
    },
    "basic": {
        "reminders_daily": 30,
        "journal_entries_daily": 20,
        "journal_voice_daily": 20,
        "journal_ai_daily": 0,
    },
    "pro": {
        "reminders_daily": 100,
        "journal_entries_daily": 70,
        "journal_voice_daily": 70,
        "journal_ai_daily": 70,
    },
    "max": {
        "reminders_daily": 300,
        "journal_entries_daily": 200,
        "journal_voice_daily": 200,
        "journal_ai_daily": 200,
    },
}


VOICE_SECONDS_LIMIT: dict[str, int] = {
    "free": 30,
    "basic": 120,
    "pro": 300,
    "max": 900,
}


async def _get_or_create_row(
    session: AsyncSession,
    user_id: int,
    feature: str,
    bucket: str,
) -> QuotaUsage:
    q = select(QuotaUsage).where(
        QuotaUsage.user_id == user_id,
        QuotaUsage.feature == feature,
        QuotaUsage.bucket_date == bucket,
    )
    res = await session.execute(q)
    row = res.scalar_one_or_none()
    if row:
        return row

    row = QuotaUsage(
        user_id=user_id,
        feature=feature,
        bucket_date=bucket,
        used_units=0,
    )
    session.add(row)
    await session.flush()
    return row


async def get_daily_used(session: AsyncSession, user_id: int, feature: str) -> int:
    bucket = _day_bucket_utc()
    q = select(QuotaUsage).where(
        QuotaUsage.user_id == user_id,
        QuotaUsage.feature == feature,
        QuotaUsage.bucket_date == bucket,
    )
    res = await session.execute(q)
    row = res.scalar_one_or_none()
    return int(row.used_units) if row else 0


def get_daily_limit(user: Optional[User], feature: str) -> int:
    plan = _norm_plan(user)
    return int(DAILY_LIMITS.get(plan, DAILY_LIMITS["free"]).get(feature, 0))


def get_voice_seconds_limit(user: Optional[User]) -> int:
    plan = _norm_plan(user)
    return int(VOICE_SECONDS_LIMIT.get(plan, 30))


async def check_daily_available(
    session: AsyncSession,
    user: User,
    feature: str,
    need_units: int = 1,
) -> tuple[bool, int, int]:
    used = await get_daily_used(session, user.id, feature)
    limit = get_daily_limit(user, feature)
    ok = (used + need_units) <= limit
    return ok, used, limit


async def add_daily_usage(
    session: AsyncSession,
    user: User,
    feature: str,
    add_units: int = 1,
) -> None:
    bucket = _day_bucket_utc()
    row = await _get_or_create_row(session, user.id, feature, bucket)
    row.used_units = max(0, int(row.used_units) + int(add_units))
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
