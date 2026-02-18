from __future__ import annotations

import asyncio

from app.services.media.query import _clean_tmdb_query, _normalize_tmdb_query
from app.services.media.safety import _scrub_media_items
from app.services.media.session import _extract_year
from app.services.media_search import tmdb_search_multi

# app/services/assistant.py


def _dedupe_media(items: list[dict]) -> list[dict]:
    seen = set()
    out: list[dict] = []
    for it in items or []:
        key = (
            it.get("media_type"),
            it.get("id"),
            ((it.get("title") or "") + "|" + (it.get("name") or "")).lower(),
            it.get("year"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _sort_media(items: list[dict], query: str = "") -> list[dict]:
    def score(it: dict) -> float:
        try:
            base_s = float(it.get("popularity") or 0) * 0.8 + float(it.get("vote_average") or 0) * 2.0
            q_low = query.lower()
            t_low = (it.get("title") or it.get("name") or "").lower()

            # Если хотя бы одно слово из запроса (например, "Куба") есть в названии
            if q_low and any(word in t_low for word in q_low.split() if len(word) > 3):
                base_s += 150.0  # Огромный приоритет
            return base_s
        except Exception:
            return 0.0

    return sorted(items or [], key=score, reverse=True)


async def _tmdb_best_effort(query: str, *, limit: int = 5) -> list[dict]:
    q = _normalize_tmdb_query(_clean_tmdb_query(query))
    if not q:
        return []

    year = _extract_year(q)

    async def _safe(lang: str) -> list[dict]:
        try:
            return await tmdb_search_multi(q, lang=lang, limit=limit) or []
        except Exception:
            return []

    items_ru, items_en = await asyncio.gather(_safe("ru-RU"), _safe("en-US"))
    items = _dedupe_media((items_ru or []) + (items_en or []))
    items = _scrub_media_items(items)

    if year:
        filtered = [it for it in items if str(it.get("year") or "") == year]
        if filtered:
            items = filtered

    return _sort_media(items, query=q)[:limit]  # Передаем query для бонуса
