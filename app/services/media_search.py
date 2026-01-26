from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

TMDB_API = "https://api.themoviedb.org/3"


TMDB_IMG = "https://image.tmdb.org/t/p"

def _tmdb_image_url(path: Optional[str], *, size: str = "w342") -> str:
    if not path:
        return ""
    path = str(path).strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"{TMDB_IMG}/{size}{path}"

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

async def tmdb_search_multi(query: str, *, lang: str = "ru-RU", limit: int = 5) -> List[Dict[str, Any]]:
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
        out.append({
            "media_type": mt,
            "id": it.get("id"),
            "title": title,
            "year": year,
            "overview": it.get("overview") or "",
            "popularity": it.get("popularity") or 0,
            "vote_average": it.get("vote_average") or 0,
            "poster_path": it.get("poster_path") or "",
            "backdrop_path": it.get("backdrop_path") or "",
            "poster_url": _tmdb_image_url(it.get("poster_path"), size="w342"),
            "backdrop_url": _tmdb_image_url(it.get("backdrop_path"), size="w780"),
        })
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
        poster = (it.get("poster_url") or "").strip()
        poster_str = f"\n   🖼 {poster}" if poster else ""
        lines.append(f"{i}) [{mt}] {t} ({y}) — {ov}{poster_str}")
    return "\n".join(lines)
