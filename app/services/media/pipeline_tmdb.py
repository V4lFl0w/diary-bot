from __future__ import annotations

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
        s = float(it.get("popularity") or 0) * 0.8 + float(it.get("vote_average") or 0) * 2.0
        
        # БОНУС: Если название фильма есть в поисковом запросе — поднимаем в топ
        title = (it.get("title") or it.get("name") or "").lower()
        if query.lower() in title or title in query.lower():
            s += 50.0 # Весомый бонус
            
        return s

    return sorted(items or [], key=score, reverse=True)


async def _tmdb_best_effort(query: str, *, limit: int = 5) -> list[dict]:
    """
    Best-effort TMDb retrieval (faster):
    - run ru-RU and en-US in parallel
    - dedupe + soft year filter + sort
    """
    import asyncio

    q = _normalize_tmdb_query(_clean_tmdb_query(query))
    if not q:
        return []

    year = _extract_year(q)

    async def _safe(lang: str) -> list[dict]:
        try:
            items = await tmdb_search_multi(q, lang=lang, limit=limit)
        except Exception:
            return []
        if items and isinstance(items[0], dict) and items[0].get("_error"):
            return []
        return items or []

    items_ru, items_en = await asyncio.gather(
        _safe("ru-RU"),
        _safe("en-US"),
        return_exceptions=False,
    )

    items = _dedupe_media((items_ru or []) + (items_en or []))

    # safety: drop adult + scrub explicit overview
    items = _scrub_media_items(items)

    if year:
        filtered = [it for it in items if str(it.get("year") or "") == year]
        if filtered:
            items = filtered

    return _sort_media(items)[:limit]
