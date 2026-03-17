from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.quota_usage import QuotaUsage
from app.models.kv_cache import KVCache


# ====== ПЛАНЫ ======
def _is_premium_active(user: User | None) -> bool:
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


def _norm_user_plan(user: User | None) -> str:
    if not user:
        return "free"

    if not _is_premium_active(user):
        return "free"

    raw = str(getattr(user, "premium_plan", "") or "").strip().lower()

    if raw == "pro":
        return "pro"
    if raw in {"max", "pro_max", "promax", "pro-max"}:
        return "max"
    if raw in {"basic", "trial"}:
        return "basic"
    if raw == "free":
        return "free"

    return "basic"


FEATURE_ALIASES = {
    # SerpAPI aliases used across media pipeline
    "media_serp": "serpapi_web",
    "media_lens": "serpapi_lens",
    # safety aliases (если где-то всплывут)
    "serp": "serpapi_web",
    "lens": "serpapi_lens",
}


def _norm_feature(feature: str | None) -> str:
    f = (feature or "").strip()
    return FEATURE_ALIASES.get(f, f)


# ====== UNITS COST (сколько "стоит" один вызов) ======
UNIT_COST = {
    "serpapi_web": 1,  # Web
    "serpapi_lens": 1,  # Movies/Frames (Lens)
}

# ====== ДНЕВНЫЕ ЛИМИТЫ ПО ПЛАНАМ (units / YYYY-MM-DD) ======
# Настраивай под себя: смысл — НЕ дать сожрать SerpAPI/OpenAI за день.
PLAN_DAILY_UNITS = {
    "free": {
        "serpapi_web": 0,
        "serpapi_lens": 0,
        "openai_calories_text": 0,
        "openai_calories_vision": 0,
    },
    "basic": {
        "serpapi_web": 2,
        "serpapi_lens": 2,
        "openai_calories_text": 10,
        "openai_calories_vision": 3,
    },
    "pro": {
        "serpapi_web": 6,
        "serpapi_lens": 6,
        "openai_calories_text": 25,
        "openai_calories_vision": 10,
    },
    "max": {
        "serpapi_web": 15,
        "serpapi_lens": 15,
        "openai_calories_text": 60,
        "openai_calories_vision": 25,
    },
    "trial": {
        "serpapi_web": 1,
        "serpapi_lens": 1,
        "openai_calories_text": 5,
        "openai_calories_vision": 1,
    },
}


def _day_bucket_utc() -> str:
    d = datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


async def _get_or_create_row(session: AsyncSession, user_id: int, feature: str, bucket: str) -> QuotaUsage:
    q = select(QuotaUsage).where(
        QuotaUsage.user_id == user_id,
        QuotaUsage.feature == feature,
        QuotaUsage.bucket_date == bucket,
    )
    res = await session.execute(q)
    row = res.scalar_one_or_none()
    if row:
        return row
    row = QuotaUsage(user_id=user_id, feature=feature, bucket_date=bucket, used_units=0)
    session.add(row)
    await session.flush()
    return row


async def enforce_and_add_units(session: AsyncSession, user: User, feature: str, add_units: int) -> None:
    plan = _norm_user_plan(user)
    feature = _norm_feature(feature)

    limits = PLAN_DAILY_UNITS.get(plan, PLAN_DAILY_UNITS["free"])
    limit = int(limits.get(feature, 0))

    bucket = _day_bucket_utc()
    row = await _get_or_create_row(session, user.id, feature, bucket)

    add_units = int(add_units)

    # REFUND PATH (на ошибках/откатах)
    if add_units < 0:
        row.used_units = max(0, int(row.used_units) + add_units)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return

    # ENFORCE PATH
    if limit > 0 and (row.used_units + add_units) > limit:
        raise PermissionError(f"Quota exceeded: {feature}. Plan={plan}. Used={row.used_units}/{limit} (+{add_units})")

    row.used_units = int(row.used_units) + add_units
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()


def cache_key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:28]


async def cache_get_json(session: AsyncSession, namespace: str, key: str) -> dict | list | None:
    q = select(KVCache).where(KVCache.namespace == namespace, KVCache.key == key)
    res = await session.execute(q)
    row = res.scalar_one_or_none()
    if not row:
        return None
    # expires_at can be NULL (treat as non-expiring)
    if row.expires_at is not None:
        exp = row.expires_at
        # make sure exp is timezone-aware for comparison
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
    try:
        return json.loads(row.value_json)
    except Exception:
        return None


async def cache_set_json(session: AsyncSession, namespace: str, key: str, obj: dict | list, *, ttl_sec: int) -> None:
    expires = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=int(ttl_sec))
    payload = json.dumps(obj, ensure_ascii=False)

    q = select(KVCache).where(KVCache.namespace == namespace, KVCache.key == key)
    res = await session.execute(q)
    row = res.scalar_one_or_none()
    if row:
        row.value_json = payload
        row.expires_at = expires
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = KVCache(
            namespace=namespace, key=key, value_json=payload, expires_at=expires, updated_at=datetime.now(timezone.utc)
        )
        session.add(row)
    await session.commit()
