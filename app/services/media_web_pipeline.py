# app/services/media_web_pipeline.py
from __future__ import annotations

import os
import re
import json
import urllib.parse
import urllib.request
from typing import List, Tuple


def _norm(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)
    return q


def _dedupe(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in seq or []:
        x = _norm(x)
        if not x:
            continue
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


_SXXEYY_RE = re.compile(r"(?i)\bS(\d{1,2})\s*E(\d{1,2})\b")
_SEASON_EP_RE = re.compile(r"(?i)\b(season|s)\s*(\d{1,2})\b.*\b(episode|e)\s*(\d{1,3})\b")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _strip_episode_tokens(q: str) -> str:
    q = _norm(q)
    q2 = _SXXEYY_RE.sub("", q)
    q2 = _SEASON_EP_RE.sub("", q2)
    q2 = re.sub(r"(?i)\b(ep|episode|серия|сезон)\b\s*\d{1,3}", "", q2)
    return _norm(q2)


def _http_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers=(headers or {}) | {
            "User-Agent": "ValFlowDiaryBot/1.0 (media lookup)",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _wiki_opensearch(q: str, lang: str = "en", limit: int = 6) -> List[str]:
    q = _norm(q)
    if not q:
        return []
    base = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": q,
        "limit": str(limit),
        "namespace": "0",
        "format": "json",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = _http_json(url, timeout=7)
    if not isinstance(data, list) or len(data) < 2:
        return []
    titles = data[1]
    if not isinstance(titles, list):
        return []
    return [t for t in titles if isinstance(t, str)]


def _brave_candidates(q: str, limit: int = 6) -> List[str]:
    key = os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    q = _norm(q)
    if not q:
        return []
    # Brave Search API endpoint
    base = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": q, "count": str(max(3, min(limit, 10)))}
    url = base + "?" + urllib.parse.urlencode(params)
    data = _http_json(url, headers={"X-Subscription-Token": key}, timeout=12)
    if not isinstance(data, dict):
        return []
    web = data.get("web") or {}
    results = web.get("results") or []
    out: List[str] = []
    if isinstance(results, list):
        for r in results[:limit]:
            if not isinstance(r, dict):
                continue
            title = r.get("title") or ""
            desc = r.get("description") or ""
            # title usually best
            if isinstance(title, str) and title.strip():
                out.append(title.strip())
            # sometimes description carries "Movie (2010)" etc.
            if isinstance(desc, str) and desc.strip() and len(out) < limit:
                out.append(desc.strip())
    return out


def _serpapi_candidates(q: str, limit: int = 6) -> List[str]:
    key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
    if not key:
        return []
    q = _norm(q)
    if not q:
        return []
    # SerpAPI Google Search JSON endpoint
    base = "https://serpapi.com/search.json"
    params = {
        "engine": "google",
        "q": q,
        "api_key": key,
        "num": str(max(3, min(limit, 10))),
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = _http_json(url, timeout=15)
    if not isinstance(data, dict):
        return []
    out: List[str] = []

    # organic_results: title/snippet
    organic = data.get("organic_results") or []
    if isinstance(organic, list):
        for r in organic[:limit]:
            if not isinstance(r, dict):
                continue
            t = r.get("title")
            s = r.get("snippet")
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
            if isinstance(s, str) and s.strip() and len(out) < limit:
                out.append(s.strip())

    # knowledge_graph title
    kg = data.get("knowledge_graph") or {}
    if isinstance(kg, dict):
        t = kg.get("title")
        if isinstance(t, str) and t.strip():
            out.insert(0, t.strip())

    return out[:limit]


async def web_to_tmdb_candidates(query: str, use_serpapi: bool = False) -> Tuple[List[str], str]:
    """
    Returns (candidates, tag).
    Order:
      - heuristics (strip SxxEyy)
      - Wikipedia OpenSearch (free)
      - Brave (if key)
      - SerpAPI (if key + use_serpapi=True)
    """
    q = _norm(query)
    if not q:
        return [], "empty_query"

    year = None
    m = _YEAR_RE.search(q)
    if m:
        year = m.group(1)

    stripped = _strip_episode_tokens(q)

    cands: List[str] = []
    if stripped and stripped.lower() != q.lower():
        cands.append(stripped)

    # free wiki baseline
    wiki_qs = _dedupe([stripped, q])
    wiki_titles: List[str] = []
    for wq in wiki_qs:
        if not wq:
            continue
        wiki_titles += _wiki_opensearch(wq, lang="ru", limit=6)
        wiki_titles += _wiki_opensearch(wq, lang="en", limit=6)

    for t in wiki_titles:
        t2 = _norm(t)
        if not t2:
            continue
        if year and year not in t2:
            cands.append(f"{t2} {year}")
        cands.append(t2)

    # Brave (if key)
    brave = _brave_candidates(q, limit=6)
    for t in brave:
        t2 = _norm(t)
        if not t2:
            continue
        cands.append(t2)
        if year and year not in t2:
            cands.append(f"{t2} {year}")

    # SerpAPI (only if requested)
    if use_serpapi:
        serp = _serpapi_candidates(q, limit=6)
        for t in serp:
            t2 = _norm(t)
            if not t2:
                continue
            cands.append(t2)
            if year and year not in t2:
                cands.append(f"{t2} {year}")

    # always include original query last
    cands.append(q)

    cands = _dedupe(cands)[:14]

    tag = "wiki"
    if brave and not use_serpapi:
        tag = "wiki+brave"
    if use_serpapi:
        tag = "wiki+brave+serpapi" if brave else "wiki+serpapi"

    return cands, tag
