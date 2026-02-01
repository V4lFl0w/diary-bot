from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w342"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _tmdb_auth_headers_and_params() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Поддержка:
    - v4 Read Access Token: Authorization: Bearer <token>  (TMDB_API_KEY)
    - v3 API key: ?api_key=<key>                          (TMDB_API_KEY_V3)
    """
    token = _env("TMDB_API_KEY")
    api_key_v3 = _env("TMDB_API_KEY_V3")

    headers: Dict[str, str] = {"accept": "application/json"}
    params: Dict[str, str] = {}

    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key_v3:
        params["api_key"] = api_key_v3

    return headers, params


async def tmdb_search_multi(
    query: str, *, lang: str = "ru-RU", limit: int = 5
) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    headers, base_params = _tmdb_auth_headers_and_params()
    if "Authorization" not in headers and "api_key" not in base_params:
        return []

    url = f"{TMDB_API}/search/multi"
    params = {
        **base_params,
        "query": q,
        "language": lang,
        "include_adult": "false",
        "page": 1,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params, headers=headers)

        if r.status_code in (401, 403):
            return [{"_error": f"TMDb auth error: {r.status_code}"}]
        if r.status_code == 429:
            return [{"_error": "TMDb rate limit (429). Try later."}]
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
        out.append(
            {
                "media_type": mt,
                "id": it.get("id"),
                "title": title,
                "year": year,
                "overview": it.get("overview") or "",
                "popularity": it.get("popularity") or 0,
                "vote_average": it.get("vote_average") or 0,
                "poster_path": it.get("poster_path") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def build_media_context(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "Ничего не найдено в базе источника."
    if len(items) == 1 and items[0].get("_error"):
        return f"TMDb error: {items[0]['_error']}"

    lines = ["Найденные кандидаты (TMDb):"]
    for i, it in enumerate(items, 1):
        t = it.get("title") or "?"
        y = it.get("year") or "?"
        mt = it.get("media_type") or "?"
        ov = (it.get("overview") or "").strip()
        ov = ov[:450] + ("…" if len(ov) > 450 else "")

        poster_path = (it.get("poster_path") or "").strip()
        poster_url = f"{TMDB_IMG}{poster_path}" if poster_path else ""

        lines.append(f"{i}) [{mt}] {t} ({y}) — {ov}")
        if poster_url:
            lines.append(f"🖼 {poster_url}")

    return "\n".join(lines)


async def tmdb_search_person(name: str):
    headers, base_params = _tmdb_auth_headers_and_params()
    url = f"{TMDB_API}/search/person"
    params = {**base_params, "query": name}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        res = data.get("results") or []
        return res[0]["id"] if res else None


async def tmdb_discover_with_people(
    person_id: int, *, year: str | None, kind: str | None
):
    headers, base_params = _tmdb_auth_headers_and_params()
    media_type = "movie" if kind != "tv" else "tv"
    url = f"{TMDB_API}/discover/{media_type}"

    params = {
        **base_params,
        "with_people": person_id,
        "sort_by": "popularity.desc",
        "page": 1,
    }

    if year:
        if media_type == "movie":
            params["primary_release_year"] = year
        else:
            params["first_air_date_year"] = year

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return []

        data = r.json() or {}
        items = data.get("results") or []

    out = []
    for it in items[:5]:
        out.append(
            {
                "media_type": media_type,
                "id": it.get("id"),
                "title": it.get("title") if media_type == "movie" else it.get("name"),
                "year": (it.get("release_date") or it.get("first_air_date") or "")[:4],
                "overview": it.get("overview") or "",
                "poster_path": it.get("poster_path"),
                "popularity": it.get("popularity") or 0,
                "vote_average": it.get("vote_average") or 0,
            }
        )
    return out
