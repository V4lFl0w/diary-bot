from __future__ import annotations

import os
from typing import List

import httpx


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default


async def serpapi_search(query: str, *, count: int = 5) -> List[dict]:
    """
    SerpAPI search.
    Env:
      SERPAPI_KEY or SERPAPI_API_KEY
    """
    api_key = _env("SERPAPI_API_KEY") or _env("SERPAPI_KEY")
    if not api_key or not query:
        return []

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
        results.append(
            {
                "title": (it.get("title") or "").strip(),
                "snippet": (it.get("snippet") or "").strip(),
                "url": (it.get("link") or "").strip(),
                "provider": "serpapi",
            }
        )
    return results
