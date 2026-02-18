from __future__ import annotations

import os
from typing import List

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.quota_units import (
    UNIT_COST,
    enforce_and_add_units,
    cache_key,
    cache_get_json,
    cache_set_json,
)


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default


async def serpapi_search(
    session: AsyncSession,
    user: User,
    query: str,
    *,
    count: int = 5,
    feature: str = "serpapi_web",
    cache_ttl_sec: int = 6 * 60 * 60,
) -> List[dict]:
    """
    SerpAPI search with:
      - DB quota enforcement (units / month by plan)
      - DB cache (TTL) to prevent repeated spend
    Env:
      SERPAPI_KEY or SERPAPI_API_KEY
    """
    api_key = _env("SERPAPI_API_KEY") or _env("SERPAPI_KEY")
    if not api_key or not query:
        return []

    # ---- Cache ----
    namespace = feature
    key = cache_key({"q": query, "count": int(count)})
    cached = await cache_get_json(session, namespace, key)
    if isinstance(cached, list):
        return cached  # already normalized list[dict]

    # ---- Quota (charge only on real external attempt; refund on fail/empty) ----
    add_units = int(UNIT_COST.get(feature, 1))
    plan = (getattr(user, 'premium_plan', None) or 'basic').strip().lower()
    try:
        await enforce_and_add_units(session, user, feature, add_units)
    except PermissionError:
        return [
            {
                "provider": "quota",
                "quota_exceeded": True,
                "feature": feature,
                "plan": plan,
            }
        ]

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": api_key,
        "num": str(int(count)),
    }

    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                await enforce_and_add_units(session, user, feature, -add_units)  # refund
                return []
            data = r.json()
    except Exception:
        await enforce_and_add_units(session, user, feature, -add_units)  # refund
        return []

    results: List[dict] = []
    for it in (data.get("organic_results") or [])[:count]:
        results.append(
            {
                "title": (it.get("title") or "").strip(),
                "snippet": (it.get("snippet") or "").strip(),
                "url": (it.get("link") or "").strip(),
                "provider": "serpapi",
            }
        )

    if not results:
        await enforce_and_add_units(session, user, feature, -add_units)  # refund
        return []

    # save cache
    await cache_set_json(session, namespace, key, results, ttl_sec=int(cache_ttl_sec))
    return results
