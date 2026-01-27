from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default


async def brave_search(query: str, *, count: int = 5) -> List[dict]:
    """
    Brave Search API (cheap default).
    Env:
      BRAVE_SEARCH_TOKEN
    """
    token = _env("BRAVE_SEARCH_TOKEN")
    if not token or not query:
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": token,
    }
    params = {"q": query, "count": str(int(count))}

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, headers=headers, params=params)
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []

    results = []
    web = (data or {}).get("web") or {}
    for it in (web.get("results") or [])[:count]:
        results.append({
            "title": (it.get("title") or "").strip(),
            "snippet": (it.get("description") or "").strip(),
            "url": (it.get("url") or "").strip(),
            "provider": "brave",
        })
    return results


async def serpapi_search(query: str, *, count: int = 5) -> List[dict]:
    """
    SerpAPI (fallback, more expensive).
    Env:
      SERPAPI_KEY
    """
    api_key = _env("SERPAPI_KEY")
    if not api_key or not query:
        return []

    # This uses SerpAPI generic endpoint; engine can be tuned later.
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
                return []
            data = r.json()
    except Exception:
        return []

    results = []
    for it in (data.get("organic_results") or [])[:count]:
        results.append({
            "title": (it.get("title") or "").strip(),
            "snippet": (it.get("snippet") or "").strip(),
            "url": (it.get("link") or "").strip(),
            "provider": "serpapi",
        })
    return results
