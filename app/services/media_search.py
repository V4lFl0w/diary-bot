from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

TMDB_API = "https://api.themoviedb.org/3"

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default

async def tmdb_search_multi(query: str, *, lang: str = "ru-RU", limit: int = 5) -> List[Dict[str, Any]]:
    """
    Возвращает топ результатов по query из TMDb (movie+tv+person).
    Для бота по фильмам/сериалам нам достаточно movie/tv.
    """
    key = _env("TMDB_API_KEY")
    if not key:
        return []

    q = (query or "").strip()
    if not q:
        return []

    url = f"{TMDB_API}/search/multi"
    params = {"query": q, "language": lang, "include_adult": "false", "page": 1}
    headers = {"Authorization": f"Bearer {key}", "accept": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params, headers=headers)
        if r.status_code >= 400:
            return []
        data = r.json() or {}
        items = data.get("results") or []

    out: List[Dict[str, Any]] = []
    for it in items:
        mt = it.get("media_type")
        if mt not in ("movie", "tv"):
            continue

        title = it.get("title") if mt == "movie" else it.get("name")
        date = it.get("release_date") if mt == "movie" else it.get("first_air_date")
        year = (date or "")[:4] if date else ""
        out.append({
            "media_type": mt,
            "id": it.get("id"),
            "title": title,
            "year": year,
            "overview": it.get("overview") or "",
            "popularity": it.get("popularity") or 0,
            "vote_average": it.get("vote_average") or 0,
        })
        if len(out) >= limit:
            break
    return out

def build_media_context(items: List[Dict[str, Any]]) -> str:
    """
    Контекст для RAG. Модель НЕ должна выходить за пределы этого.
    """
    if not items:
        return "Ничего не найдено в базе источника."
    lines = ["Найденные кандидаты (TMDb):"]
    for i, it in enumerate(items, 1):
        t = it.get("title") or "?"
        y = it.get("year") or "?"
        mt = it.get("media_type") or "?"
        ov = (it.get("overview") or "").strip()
        ov = ov[:450] + ("…" if len(ov) > 450 else "")
        lines.append(f"{i}) [{mt}] {t} ({y}) — {ov}")
    return "\n".join(lines)
