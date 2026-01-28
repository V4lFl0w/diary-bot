# app/services/media_web_pipeline.py
from __future__ import annotations

import os
import logging
import re
import json
import urllib.parse
import urllib.request
from typing import List, Tuple
from app.services.media_text import YEAR_RE as _YEAR_RE, SXXEYY_RE as _SXXEYY_RE

_LOG = logging.getLogger(__name__)
_DEBUG = os.getenv('MEDIA_WEB_DEBUG', '').lower() in ('1','true','yes','on')
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

_SEASON_EP_RE = re.compile(r"(?i)\b(season|s)\s*(\d{1,2})\b.*\b(episode|e)\s*(\d{1,3})\b")

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
    except Exception as e:
        if _DEBUG:
            _LOG.warning('media_web_pipeline http_json FAIL url=%s err=%r', url, e)
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

def _serpapi_candidates(q: str, limit: int = 6) -> List[str]:
    key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
    if _DEBUG:
        _LOG.info('SERPAPI CALL q=%r limit=%s has_key=%s', q, limit, bool(key))
    if not key:
        _LOG.info('SERPAPI: enabled=%s (no key)', False)
        return []
    _LOG.info('SERPAPI: enabled=%s', True)
    q = _norm(q)
    if not q:
        return []

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

    def add_title(t):
        if not isinstance(t, str):
            return
        t = _norm(t)
        if not t:
            return
        tl = t.lower()
        bad = ("watch", "online", "stream", "full movie", "hd", "netflix", "torrent")
        if any(x in tl for x in bad):
            return
        if len(t) > 85:
            return
        if t.count(" ") >= 12:
            return
        out.append(t)

    kg = data.get("knowledge_graph") or {}
    if isinstance(kg, dict):
        add_title(kg.get("title"))

    ab = data.get("answer_box") or {}
    if isinstance(ab, dict):
        add_title(ab.get("title"))
        org = ab.get("organic_result") or {}
        if isinstance(org, dict):
            add_title(org.get("title"))

    for block_key in ("movie_results", "tv_results"):
        block = data.get(block_key) or {}
        if isinstance(block, dict):
            add_title(block.get("title") or block.get("name"))

    organic = data.get("organic_results") or []
    if isinstance(organic, list):
        for r in organic[:limit*3]:
            if not isinstance(r, dict):
                continue
            add_title(r.get("title"))
            if len(out) >= limit:
                break

    return _dedupe(out)[:limit]

# --- Lens post-processing (cleanup titles -> TMDB-friendly short candidates) ---

_LENS_SITE_TOKENS = {
    "imdb", "youtube", "netflix", "wikipedia", "reddit", "tiktok", "instagram", "facebook",
    "twitter", "x", "x.com", "kinopoisk", "hdrezka", "rezka", "torrent", "rutube", "ok.ru",
}

def _strip_site_suffix(s: str) -> str:
    s = _norm(s)
    if not s:
        return ""
    # Split like: "Title - 9-1-1 - YouTube" or "Something - IMDb"
    parts = [p.strip() for p in s.split(" - ") if p and p.strip()]
    if len(parts) >= 2:
        last = parts[-1].lower().strip(" .")
        if last in _LENS_SITE_TOKENS:
            parts = parts[:-1]
    # Also common " | Site" pattern
    parts2 = [p.strip() for p in re.split(r"\s+\|\s+", " - ".join(parts)) if p.strip()]
    if len(parts2) >= 2:
        last2 = parts2[-1].lower().strip(" .")
        if last2 in _LENS_SITE_TOKENS:
            parts2 = parts2[:-1]
    return _norm(" - ".join(parts2))

def _extract_known_titles(s: str) -> List[str]:
    """Extract extra short candidates from the phrase (series name/person name)."""
    out: List[str] = []
    s2 = _norm(s)
    if not s2:
        return out

    # Example: 9-1-1
    if re.search(r"\b9-1-1\b", s2, flags=re.I):
        out.append("9-1-1")

    # "Aisha Hinds - Actress" -> "Aisha Hinds"
    m = re.match(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*-\s*(actor|actress)\b", s2, flags=re.I)
    if m:
        out.append(m.group(1).strip())

    # If phrase contains an explicit "X Y" (two capitalized words) and it's short enough,
    # we may use it as a person candidate — but keep it conservative.
    m2 = re.search(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b", s2)
    if m2:
        cand = f"{m2.group(1)} {m2.group(2)}"
        if 5 <= len(cand) <= 40:
            out.append(cand)

    return out

def _clean_lens_candidates(raw: List[str], limit: int = 15) -> List[str]:
    cleaned: List[str] = []

    for s in (raw or []):
        base = _strip_site_suffix(s)

        # Drop super long garbage
        if not base:
            continue
        if len(base) > 120:
            continue

        # Collect extracted short titles first
        cleaned.extend(_extract_known_titles(base))

        # Also keep the base phrase itself (already without site suffix)
        cleaned.append(base)

    cleaned = _dedupe(cleaned)

    # If we have "9-1-1" — force it to be first (fast TMDB hit)
    key = "9-1-1"
    for i, x in enumerate(cleaned):
        if x.strip().lower() == key.lower():
            cleaned.insert(0, cleaned.pop(i))
            break

    return cleaned[:limit]

def _serpapi_lens_candidates(image_url: str, limit: int = 8, hl: str = "ru") -> List[str]:
    """
    SerpAPI Google Lens: по публичному URL картинки достаём кандидаты названий
    (visual_matches titles + related_content queries).
    """
    key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
    image_url = _norm(image_url)
    if _DEBUG:
        _LOG.info("SERPAPI LENS CALL url=%r limit=%s has_key=%s", image_url, limit, bool(key))
    if not key or not image_url:
        return []

    base = "https://serpapi.com/search.json"
    params = {
        "engine": "google_lens",
        "url": image_url,
        "api_key": key,
        "hl": hl,
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = _http_json(url, timeout=25)
    if not isinstance(data, dict):
        return []

    out: List[str] = []

    def add(s: str):
        if not isinstance(s, str):
            return
        s2 = _norm(s)
        if not s2:
            return
        sl = s2.lower()
        bad = (
            "watch", "online", "stream", "full movie", "hd", "netflix", "torrent",
            "смотреть", "онлайн", "hdrezka", "торрент",
        )
        if any(x in sl for x in bad):
            return
        if len(s2) > 100:
            return
        out.append(s2)

    vm = data.get("visual_matches") or []
    if isinstance(vm, list):
        for r in vm[: max(10, limit * 2)]:
            if not isinstance(r, dict):
                continue
            add(r.get("title") or "")
            if len(out) >= limit:
                break

    rc = data.get("related_content") or []
    if isinstance(rc, list) and len(out) < limit:
        for r in rc[: max(10, limit * 2)]:
            if not isinstance(r, dict):
                continue
            add(r.get("query") or "")
            if len(out) >= limit:
                break

    return _dedupe(out)[:limit]

async def image_to_tmdb_candidates(
    image_url: str,
    use_serpapi_lens: bool = True,
    hl: str = "ru",
) -> Tuple[List[str], str]:
    """
    Кадр/скрин (по URL) -> SerpAPI Lens -> список текстовых кандидатов,
    которые дальше можно гонять в TMDB search/multi.
    """
    u = _norm(image_url)
    if not u:
        return [], "empty_image_url"

    cands: List[str] = []

    if use_serpapi_lens:
        lens = _serpapi_lens_candidates(u, limit=10, hl=hl)
        cands.extend(lens)

    # post-process: raw lens titles -> TMDB-friendly candidates
    cands = _clean_lens_candidates(cands, limit=15)
    tag = "lens" if use_serpapi_lens else "no_lens"
    return cands, tag

async def image_bytes_to_tmdb_candidates(
    image_bytes: bytes,
    ext: str = "jpg",
    use_serpapi_lens: bool = True,
    hl: str = "ru",
    prefix: str = "frames",
) -> Tuple[List[str], str]:
    """
    Bytes (Telegram photo/file bytes) -> upload to Spaces -> public URL -> SerpAPI Lens -> TMDB-friendly candidates.
    Requires Spaces env vars (see app/services/s3_uploader.py).
    """
    if not image_bytes:
        return [], "empty_image_bytes"

    # Import lazily to avoid hard-fail if uploader not configured in some envs
    try:
        from app.services.s3_uploader import upload_bytes_get_url
    except Exception as e:
        if _DEBUG:
            _LOG.warning("media_web_pipeline: cannot import Spaces uploader: %r", e)
        return [], "no_spaces_uploader"

    try:
        public_url = await upload_bytes_get_url(image_bytes, ext=ext, prefix=prefix)
    except Exception as e:
        if _DEBUG:
            _LOG.warning("media_web_pipeline: Spaces upload failed: %r", e)
        return [], "spaces_upload_fail"

    cands, tag = await image_to_tmdb_candidates(public_url, use_serpapi_lens=use_serpapi_lens, hl=hl)
    return cands, f"{tag}+spaces"

async def web_to_tmdb_candidates(query: str, use_serpapi: bool = False) -> Tuple[List[str], str]:
    q = _norm(query)
    if not q:
        return [], "empty_query"

    year = None
    m = _YEAR_RE.search(q)
    if m:
        year = m.group(1)

    stripped = _strip_episode_tokens(q)

    cands: List[str] = []

    # 🎯 Если есть S02E10 — даём TMDB максимально понятные варианты
    m_sxe = _SXXEYY_RE.search(q)
    if m_sxe and stripped:
        s = int(m_sxe.group(1))
        e = int(m_sxe.group(2))
        cands.append(f"{stripped} S{s}E{e}")
        cands.append(f"{stripped} season {s} episode {e}")
        cands.append(f"{stripped} episode {e}")
        cands.append(f"{stripped} season {s}")

    # 🔥 1. SERPAPI ПЕРВЫМ (самый чистый источник названий)
    if use_serpapi:
        serp = _serpapi_candidates(q, limit=6)
        for t in serp:
            t2 = _norm(t)
            if not t2:
                continue
            cands.append(t2)
            if year and year not in t2:
                cands.append(f"{t2} {year}")

    # 2. Базовое очищенное название
    if stripped and stripped.lower() != q.lower():
        cands.append(stripped)

    # 3. Wikipedia
    wiki_qs = _dedupe([stripped, q])
    for wq in wiki_qs:
        if not wq:
            continue
        for t in _wiki_opensearch(wq, lang="ru", limit=5) + _wiki_opensearch(wq, lang="en", limit=5):
            t2 = _norm(t)
            if not t2:
                continue
            cands.append(t2)
            if year and year not in t2:
                cands.append(f"{t2} {year}")

    # всегда добавляем исходный запрос в конец
    cands.append(q)

    cands = _dedupe(cands)[:15]

    tag = "wiki"
    if use_serpapi:
        tag = "wiki+serpapi"
    return cands, tag