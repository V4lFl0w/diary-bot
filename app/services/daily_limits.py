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


def _day_bucket_for_user(user: Optional[User], now=None) -> str:
    d = get_bucket_date(user, now)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


DAILY_LIMITS = {
    "free": {
        "journal_entries_daily": 7,
        "journal_voice_daily": 3,
        "reminders_daily": 7,
        "calories_text_daily": 10,
        "calories_voice_daily": 3,
    },
    "basic": {
        "journal_entries_daily": 10,
        "journal_voice_daily": 5,
        "reminders_daily": 15,
        "calories_text_daily": 25,
        "calories_voice_daily": 8,
    },
    "pro": {
        "journal_entries_daily": 25,
        "journal_voice_daily": 12,
        "reminders_daily": 40,
        "calories_text_daily": 60,
        "calories_voice_daily": 20,
    },
    "max": {
        "journal_entries_daily": 60,
        "journal_voice_daily": 25,
        "reminders_daily": 100,
        "calories_text_daily": 150,
        "calories_voice_daily": 50,
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


async def get_daily_used(session: AsyncSession, user_id: int, feature: str, user: Optional[User] = None) -> int:
    bucket = _day_bucket_for_user(user)
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
    used = await get_daily_used(session, user.id, feature, user)
    limit = get_daily_limit(user, feature)
    ok = (used + need_units) <= limit
    return ok, used, limit


async def add_daily_usage(
    session: AsyncSession,
    user: User,
    feature: str,
    add_units: int = 1,
) -> None:
    bucket = _day_bucket_for_user(user)
    row = await _get_or_create_row(session, user.id, feature, bucket)
    row.used_units = max(0, int(row.used_units) + int(add_units))
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()


# -------------------- ETA HELPERS --------------------

from datetime import timedelta
from zoneinfo import ZoneInfo


def _user_tz(user):
    tz_name = getattr(user, "tz", None) or "Europe/Kyiv"
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo("UTC")


def _now_local(user, now=None):
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(_user_tz(user))


def get_bucket_date(user, now=None):
    return _now_local(user, now).date()


def format_eta(seconds: int, lang: str = "ru") -> str:
    sec = max(0, int(seconds))
    h = sec // 3600
    m = (sec % 3600) // 60

    if lang == "uk":
        return f"{h}г {m}хв" if h > 0 else f"{m}хв"
    if lang == "en":
        return f"{h}h {m}m" if h > 0 else f"{m}m"

    return f"{h}ч {m}м" if h > 0 else f"{m}м"


def get_daily_reset_eta_seconds(user, now=None):
    local_now = _now_local(user, now)
    next_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(0, int((next_midnight - local_now).total_seconds()))


def get_daily_reset_eta_text(user, lang="ru", now=None):
    return format_eta(get_daily_reset_eta_seconds(user, now), lang)
