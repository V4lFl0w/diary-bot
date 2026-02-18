# app/services/media_web_pipeline.py
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import List, Tuple, TYPE_CHECKING

from app.services.media_text import SXXEYY_RE as _SXXEYY_RE
from app.services.media_text import YEAR_RE as _YEAR_RE

# --- optional DB-aware serpapi gateway (typing-only imports) ---
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.user import User


def _extract_serp_titles(results, limit: int = 10) -> list[str]:
    """Best-effort normalize serpapi_search output into list[str]."""
    out: list[str] = []
    if results is None:
        return out

    # serpapi_search may return list[str] or list[dict] or dict
    if isinstance(results, dict):
        # common patterns
        for k in ("titles", "candidates", "results"):
            v = results.get(k)
            if isinstance(v, list):
                results = v
                break

    if isinstance(results, list):
        for it in results:
            if isinstance(it, str):
                s = it
            elif isinstance(it, dict):
                s = it.get("title") or it.get("query") or it.get("name") or ""
            else:
                continue
            s = _norm(s)
            if not s:
                continue
            out.append(s)
            if len(out) >= limit:
                break

    return _dedupe(out)[:limit]


async def _serpapi_candidates_db(q: str, session, user, limit: int = 6) -> list[str]:
    """Use serpapi_search if available (enforced quotas + kv_cache)."""
    try:
        from app.services.web_search import serpapi_search
    except Exception:
        return []
    if session is None or user is None:
        return []

    # feature separation (optional): try feature param, fallback if not supported
    try:
        res = await serpapi_search(session, user, q, count=limit, feature="media_serp")
    except TypeError:
        res = await serpapi_search(session, user, q, count=limit)
    return _extract_serp_titles(res, limit=limit)


async def _serpapi_lens_candidates_db(image_url: str, session, user, limit: int = 10, hl: str = "ru") -> list[str]:
    """Use serpapi_search for lens-style lookup if supported (separate feature)."""
    try:
        from app.services.web_search import serpapi_search
    except Exception:
        return []
    if session is None or user is None:
        return []

    # serpapi_search у тебя уже принимает q_or_url, так что URL картинки прокатывает
    try:
        res = await serpapi_search(session, user, image_url, count=limit, feature="media_lens")
    except TypeError:
        res = await serpapi_search(session, user, image_url, count=limit)
    return _extract_serp_titles(res, limit=limit)


# --- candidate cleanup: drop SEO/stock-image junk that often comes from web search ---
_SEO_TRASH_TOKENS = (
    "изображения",
    "картинки",
    "скачать",
    "download",
    "wallpaper",
    "обои",
    "png",
    "jpeg",
    "jpg",
    "free",
    "бесплатно",
    "stock",
    "shutterstock",
    "depositphotos",
    "pinterest",
    "unsplash",
    "pixabay",
    "istock",
    "отзывы",
    "форум",
    "инструкция",
    "как",
    "почему",
    "что значит",
)


def _looks_like_seo_trash_title(s: str) -> bool:
    sl = (s or "").lower()
    return any(t in sl for t in _SEO_TRASH_TOKENS)


def _clean_title_cands(cands: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for c in cands or []:
        c2 = (c or "").strip()
        if not c2:
            continue
        if _looks_like_seo_trash_title(c2):
            continue
        key = c2.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c2)
    return out


_LOG = logging.getLogger(__name__)
_DEBUG = os.getenv("MEDIA_WEB_DEBUG", "").lower() in ("1", "true", "yes", "on")


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
        headers=(headers or {})
        | {
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
            _LOG.warning("media_web_pipeline http_json FAIL url=%s err=%r", url, e)
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
    """Direct SerpAPI is forbidden here (must go through web_search.serpapi_search with cache+quota)."""
    return []


# --- Lens post-processing (cleanup titles -> TMDB-friendly short candidates) ---


def _is_bad_lens_title(t: str) -> bool:
    if not isinstance(t, str):
        return True
    t = _norm(t)
    if not t:
        return True

    tl = t.lower()

    bad_words = (
        "watch",
        "online",
        "stream",
        "full movie",
        "full hd",
        "hd",
        "1080",
        "720",
        "torrent",
        "netflix",
        "youtube",
        "youtu.be",
        "vk.com",
        "tiktok",
        "rutube",
        "ok.ru",
        "смотреть онлайн",
        "бесплатно",
        "фильм онлайн",
        "сериал онлайн",
        "биография",
        "интервью",
        "совместные работы",
        "премьера",
        "новости",
        "обзор",
        "рецензия",
        "википедия",
        "wiki",
        "кинопоиск",
        "актёр",
        "актер",
        "актриса",
        "режиссёр",
        "режиссер",
    )

    if any(x in tl for x in bad_words):
        return True

    # too long / too many words => likely article title
    if len(t) > 85:
        return True
    if t.count(" ") >= 12:
        return True

    # many Capitalized words often means news/article headline
    caps = sum(1 for w in t.split() if w[:1].isupper())
    if caps >= 4:
        return True

    return False


_LENS_SITE_TOKENS = {
    "imdb",
    "youtube",
    "netflix",
    "wikipedia",
    "reddit",
    "tiktok",
    "instagram",
    "facebook",
    "twitter",
    "x",
    "x.com",
    "kinopoisk",
    "hdrezka",
    "rezka",
    "torrent",
    "rutube",
    "ok.ru",
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

    for s in raw or []:
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
    """Direct SerpAPI Lens is forbidden here (must go through web_search.serpapi_search with cache+quota)."""
    return []


async def image_to_tmdb_candidates(
    image_url: str,
    use_serpapi_lens: bool = True,
    hl: str = "ru",
    session: AsyncSession | None = None,
    user: User | None = None,
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
        if session is not None and user is not None:
            lens = await _serpapi_lens_candidates_db(u, session, user, limit=10, hl=hl)
        else:
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
    session: AsyncSession | None = None,
    user: User | None = None,
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

    cands, tag = await image_to_tmdb_candidates(
        public_url, use_serpapi_lens=use_serpapi_lens, hl=hl, session=session, user=user
    )
    return cands, f"{tag}+spaces"


async def web_to_tmdb_candidates(
    query: str, use_serpapi: bool = False, session: AsyncSession | None = None, user: User | None = None
) -> Tuple[List[str], str]:
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
        if session is not None and user is not None:
            serp = await _serpapi_candidates_db(q, session, user, limit=6)
        else:
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
    cands = _clean_title_cands(list(cands or []))
    return cands, tag
