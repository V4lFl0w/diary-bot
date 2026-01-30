from __future__ import annotations
# app/services/assistant.py



import os
import json
import re
import logging
from time import time as _time_now
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, cast

from zoneinfo import ZoneInfo
from sqlalchemy import select, desc
from app.services.media_text import YEAR_RE as _YEAR_RE, SXXEYY_RE as _SXXEYY_RE
from app.services.intent_router import detect_intent, Intent

from app.models.user import User
from app.models.journal import JournalEntry



def _media_ctx_should_stick(intent: Intent) -> bool:
    return intent in (Intent.MEDIA_IMAGE, Intent.MEDIA_TEXT)

async def _clear_sticky_media_if_any(state) -> None:
    # Best-effort: supports aiogram FSMContext (state.get_data / update_data)
    if state is None:
        return
    try:
        data = await state.get_data()
    except Exception:
        return
    # common keys we might have used for sticky media
    keys = ["sticky_media", "sticky", "st", "last_media", "media_ctx", "prev_q", "media_prev_q"]
    if not any(k in data for k in keys):
        return
    patch = {k: None for k in keys if k in data}
    try:
        await state.update_data(**patch)
    except Exception:
        return

def _clean_tmdb_query(q: str) -> str:
    t = (q or "").strip()

    # убираем префиксы
    t = re.sub(r"^(название\s+(фильма|сериала)\s*:\s*)", "", t, flags=re.I)
    t = re.sub(r"^(title\s*:\s*)", "", t, flags=re.I)

    # убираем кавычки-ёлочки и обычные
    t = t.replace("«", "").replace("»", "").replace('"', "").replace("“", "").replace("”", "")

    # убираем год в скобках
    t = re.sub(r"\(\s*\d{4}\s*\)\s*$", "", t)

    # финальная нормализация пробелов
    t = " ".join(t.split())
    return t


# --- Optional OpenAI import (server may not have it) ---

# --- Anti-hallucination prefix (local-only; do not import) ---
ANTI_HALLUCINATION_PREFIX: str = ""

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None  # type: ignore

# --- Models (imported at top) ---

# --- Project-level constants (fallbacks) ---
# Used by _is_generic_media_caption
_GENERIC_MEDIA_CAPTIONS: set[str] = {
    "откуда кадр",
    "откуда кадр?",
    "что за фильм",
    "что за фильм?",
    "что за сериал",
    "что за сериал?",
    "что за мультик",
    "что за мультик?",
    "как называется",
    "как называется?",
}

MEDIA_NOT_FOUND_REPLY_RU = (
    "Не могу уверенно найти по этому запросу.\n"
    "Дай 1–2 факта: актёр/актриса, примерный год, страна или что происходит в сцене."
)


# --- restored media helpers (from assistant.py.bak2) ---
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

def _sort_media(items: list[dict]) -> list[dict]:
    def score(it: dict) -> float:
        try:
            return (
                float(it.get("popularity") or 0) * 0.8
                + float(it.get("vote_average") or 0) * 2.0
            )
        except Exception:
            return 0.0

    return sorted(items or [], key=score, reverse=True)



# --- restored helpers (from assistant.py.bak2) ---
def _extract_title_like_from_model_text(text: str) -> str:
    """Try to extract a title from model explanation."""
    t = (text or "").strip()
    if not t:
        return ""

    # RU quotes: «...»
    m = re.search(r"[«](.+?)[»]", t)
    if m:
        cand = (m.group(1) or "").strip()
        if 2 <= len(cand) <= 80:
            return cand

    # EN quotes "..."
    m = re.search(r"\"(.+?)\"", t)
    if m:
        cand = (m.group(1) or "").strip()
        if 2 <= len(cand) <= 80:
            return cand

    # Title: / Название:
    m = re.search(r"(?im)^\s*(title|название)\s*:\s*(.+?)\s*$", t)
    if m:
        cand = (m.group(2) or "").strip()
        cand = re.sub(r"\s+", " ", cand)
        if 2 <= len(cand) <= 80:
            return cand

    return ""


# --- safety: scrub explicit overviews (TMDb sometimes returns NSFW text even with include_adult=false) ---
_EXPLICIT_OVERVIEW_WORDS = (
    # EN
    "sex",
    "sexual",
    "porn",
    "nude",
    "nudity",
    "tits",
    "boobs",
    "penis",
    "vagina",
    "rape",
    "incest",
    "blowjob",
    "handjob",
    # RU/UA (минимальный набор явных маркеров)
    "секс",
    "сексуал",
    "порно",
    "обнажен",
    "обнаж",
    "эрот",
    "трах",
    "член",
    "вагин",
    "грудь",
    "сиськ",
    "изнасил",
    # ES/other
    "tetas",
    "desnudo",
    "desnuda",
)

def _is_explicit_text(t: str) -> bool:
    tl = (t or "").lower()
    return any(w in tl for w in _EXPLICIT_OVERVIEW_WORDS)

def _scrub_media_item(it: dict) -> dict:
    # do not mutate original dict aggressively
    if not isinstance(it, dict):
        return it
    if it.get("adult"):
        return it
    ov = it.get("overview") or ""
    if ov and _is_explicit_text(str(ov)):
        it = dict(it)
        it["overview"] = ""
    return it

def _parse_media_hints(text: str) -> dict:
    t_raw = (text or "").strip()
    t = t_raw.lower()

    year = None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    if m:
        year = m.group(1)

    kind = None
    if "сериал" in t:
        kind = "tv"
    elif "фильм" in t or "кино" in t:
        kind = "movie"

    # актёры: поддержка кириллицы + латиницы
    cast_ru = re.findall(r"\b[А-ЯЁІЇЄ][а-яёіїє]+ [А-ЯЁІЇЄ][а-яёіїє]+\b", t_raw)
    cast_en = re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", t_raw)
    cast = (cast_ru + cast_en)[:2]

    keywords = re.sub(r"[^a-zA-Zа-яА-ЯёЁІіЇїЄє0-9 ]", " ", t_raw)
    keywords = " ".join(w for w in keywords.split() if len(w) > 3)[:80]

    return {"year": year, "kind": kind, "cast": cast, "keywords": keywords.strip()}

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



def build_media_context(items: list[dict]) -> str:
    """Numbered list for TMDb search results."""
    if not items:
        return MEDIA_NOT_FOUND_REPLY_RU
    lines: list[str] = ["Нашёл варианты:"]
    for i, it in enumerate(items[:10], 1):
        try:
            lines.append(f"\n{i}) {_format_one_media(it)}")
        except Exception:
            title = it.get("title") or it.get("name") or "Без названия"
            year = it.get("year") or ""
            lines.append(f"\n{i}) {title} {f'({year})' if year else ''}".strip())
    return "\n".join(lines)


# --- Services imports (try real, otherwise safe stubs) ---
try:
    from app.services.media_search import tmdb_search_multi  # expected existing
except Exception:  # pragma: no cover

    async def tmdb_search_multi(*args: Any, **kwargs: Any) -> list[dict]:
        return []


try:
    from app.services.media_web_pipeline import web_to_tmdb_candidates  # expected existing
except Exception:  # pragma: no cover

    async def web_to_tmdb_candidates(
        *args: Any, **kwargs: Any
    ) -> tuple[list[str], str]:
        return ([], "web_stub")


try:
    from app.services.media_web_pipeline import (
        image_bytes_to_tmdb_candidates,
    )  # expected existing
except Exception:  # pragma: no cover

    async def image_bytes_to_tmdb_candidates(
        *args: Any, **kwargs: Any
    ) -> tuple[list[str], str]:
        return ([], "lens_stub")


try:
    from app.services.media_id import trace_moe_identify  # expected existing
except Exception:  # pragma: no cover

    async def trace_moe_identify(*args: Any, **kwargs: Any) -> Optional[dict]:
        return None


try:
    from app.services.llm_usage import log_llm_usage  # expected existing
except Exception:  # pragma: no cover

    async def log_llm_usage(*args: Any, **kwargs: Any) -> None:
        return None


# --- Optional project prompts (safe fallback for workers/tests) ---

# --- TMDB query sanitizer: TMDB hates long "scene description" queries ---


def _clean_query_for_tmdb(q: str) -> str:
    """
    Clean noisy captions/hashtags/emojis before sending to TMDb.
    Keeps letters/digits/basic punctuation, strips hashtags and weird symbols.
    """
    q = (q or "").strip()
    if not q:
        return ""
    # remove hashtags like #anadearmas
    q = re.sub(r"#\w+", " ", q, flags=re.UNICODE)
    # remove excessive punctuation/emojis; keep words, spaces, dash and apostrophe
    q = re.sub(r"[^\w\s\-']", " ", q, flags=re.UNICODE)
    # collapse spaces
    q = re.sub(r"\s+", " ", q, flags=re.UNICODE).strip()
    # avoid too-short junk
    return q


def _looks_like_freeform_media_query(q: str) -> bool:
    ql = (q or "").lower().strip()
    if not ql:
        return False
    bad_words = (
        "сцена",
        "момент",
        "в конце",
        "в начале",
        "актёр",
        "актер",
        "в очках",
        "в костюмах",
        "про",
        "где",
        "когда",
        "как называется",
        "помогите найти",
        "полиция",
        "женщина",
        "мужчина",
        "сериал",
        "фильм",
        "серия",
        "эпизод",
    )
    if any(w in ql for w in bad_words):
        return True
    if len(ql) >= 45 or ql.count(" ") >= 6:
        return True
    return False


def _tmdb_sanitize_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)

    # remove common RU "scene" words and punctuation clutter
    q = re.sub(
        r"(?i)\b(сцена|что происходит|что происходит в сцене|факт|факта|актер|актёр|страна|язык|мем|meme)\b.*$",
        "",
        q,
    ).strip()
    q = re.sub(r"[\"“”‘’]+", "", q).strip()

    # Keep only: title-ish part + optional year
    year = None
    m = _YEAR_RE.search(q)
    if m:
        year = m.group(1)

    # If query has SxxEyy, transform into short canonical form
    m2 = _SXXEYY_RE.search(q)
    if m2:
        s = int(m2.group(1))
        e = int(m2.group(2))
        # remove SxxEyy tokens from base title
        base = _SXXEYY_RE.sub("", q).strip()
        base = re.sub(r"\s+", " ", base).strip(" -–—,:;")
        if base:
            return f"{base} S{s}E{e}"

    # Hard length cap (TMDB works best with short queries)
    q = q.strip(" -–—,:;")
    if year and year not in q:
        # don't append year blindly if it bloats; only if short
        if len(q) <= 40:
            q = f"{q} {year}"

    if len(q) > 60:
        q = q[:60].rsplit(" ", 1)[0].strip()

    return q


def _good_tmdb_cand(q: str) -> bool:
    q = (q or "").strip()
    if not q:
        return False

    # hard caps
    if len(q) > 70:
        return False

    ql = q.lower()

    # must contain letters
    if not any(ch.isalpha() for ch in q):
        return False

    # too many words => not a title
    if q.count(" ") >= 7:
        return False

    # reject short adjective-only phrases (often model prose, not a title)
    if (
        ql.startswith("легендарн")
        or ql.startswith("российск")
        or ql.startswith("советск")
    ) and q.count(" ") <= 1:
        return False

    # reject obvious list/headline queries
    bad = (
        "ведомост",
        "топ",
        "лучших",
        "подбор",
        "подборк",
        "список",
        "15 ",
        "10 ",
        "20 ",
    )
    if any(b in ql for b in bad):
        return False

    return True


def _is_generic_media_caption(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    t = re.sub(r"\s+", " ", t).strip()

    if t in _GENERIC_MEDIA_CAPTIONS:
        return True

    # legacy/common phrases (keep behavior, avoid unreachable)
    if t in {
        "откуда кадр",
        "откуда кадр?",
        "что за фильм",
        "что за фильм?",
        "что за сериал",
        "что за сериал?",
        "что за мультик",
        "что за мультик?",
        "как называется",
        "как называется?",
        "как называется фильм",
        "как называется сериал",
    }:
        return True

    return False


def _format_media_pick(item: dict) -> str:
    """
    Small, safe formatter for a picked TMDb item.
    item keys may vary (movie/tv). We keep it short.
    """
    title = item.get("title") or item.get("name") or "Без названия"
    year = ""
    d = item.get("release_date") or item.get("first_air_date") or ""
    if isinstance(d, str) and len(d) >= 4:
        year = d[:4]
    overview = (item.get("overview") or "").strip()
    if overview and len(overview) > 500:
        overview = overview[:500].rsplit(" ", 1)[0] + "…"
    media_type = item.get("media_type") or ("tv" if item.get("name") else "movie")
    tmdb_id = item.get("id")
    url = ""
    if tmdb_id:
        url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    lines = [f"🎬 {title}" + (f" ({year})" if year else "")]
    if overview:
        lines.append("")
        lines.append(overview)
    if url:
        lines.append("")
        lines.append(url)
    return "\n".join(lines)


def _lens_clean_candidate(s: str) -> str:
    """
    Clean Lens candidate line into something TMDb-friendly BEFORE normalize/sanitize.
    Examples:
      '✨ Film: Deep Water(2022) ...' -> 'Deep Water 2022'
      'Ben Affleck in underwear in the new film Deep Water (2022)' -> 'Deep Water 2022 Ben Affleck'
    """
    s = (s or "").strip()
    if not s:
        return ""

    # remove common prefixes
    s = re.sub(r"(?i)^\s*(✨\s*)?(film|movie|фильм|кино)\s*:\s*", "", s).strip()

    # remove platform-y suffixes
    s = re.sub(r"(?i)\b(official)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Extract Title (YEAR) preference
    m = re.search(r"(.+?)\s*\(\s*(19\d{2}|20\d{2})\s*\)", s)
    if m:
        title = (m.group(1) or "").strip(" -–—,:;")
        year = m.group(2)
        # keep short title + year only
        if title:
            return f"{title} {year}".strip()

    # If YEAR is present elsewhere, keep it (but avoid giant strings)
    m2 = re.search(r"\b(19\d{2}|20\d{2})\b", s)
    year = m2.group(1) if m2 else ""

    # If it contains "new film <Title>" pattern
    m3 = re.search(r"(?i)\bfilm\b\s+([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,5})", s)
    title2 = (m3.group(1) or "").strip() if m3 else ""

    # shorten aggressively
    s2 = s
    if len(s2) > 90:
        s2 = s2[:90].rsplit(" ", 1)[0].strip()

    # try to keep likely title-ish chunk: first 2–6 TitleCased words
    tokens = re.findall(r"[A-Za-z0-9'’\-]+", s2)
    # fallbacks
    base = ""
    if title2 and len(title2.split()) <= 6:
        base = title2
    elif 1 <= len(tokens) <= 10:
        base = " ".join(tokens[:6])
    else:
        base = " ".join(tokens[:6])

    base = base.strip(" -–—,:;")
    if year and year not in base and len(base) <= 45:
        base = f"{base} {year}".strip()

    return base.strip()


def _lens_bad_candidate(s: str) -> bool:
    """
    Lens часто возвращает:
      - каналы/эдиты/муз клипы
      - платформы (YouTube/TikTok/Instagram)
      - CTA (subscribe/like/share)
    Это не тайтлы.
    """
    sl = (s or "").lower()
    if not sl:
        return True

    bad = (
        "edits",
        "edit",
        "channel",
        "youtube",
        "tiktok",
        "instagram",
        "reels",
        "shorts",
        "subscribe",
        "like",
        "share",
        "official",
        "music video",
        "mood music",
        "compilation",
        "fanmade",
        "trailer",
        "clip",
        "status",
        "threads",
        "funny",
        "moments",
        "best",
        "scenes",
        "scene",
        "memes",
        "meme",
        "interview",
        "actor",
        "cast",
        "behind the scenes",
        "bts",
        "short",
    )
    if any(b in sl for b in bad):
        return True

    # too generic single words
    if sl.strip() in {"movie", "film", "series", "tv", "deep", "nothing"}:
        return True

    # looks like account/name rather than title (very short + non-title)
    if len(sl.strip()) <= 3:
        return True

    return False


def _lens_score_candidate(raw: str) -> int:
    """
    Higher is better.
    Prefer:
      - Title (YEAR) or Title YEAR
      - 1–6 words, not generic
      - contains some TitleCase / letters
    Penalize:
      - long sentences
      - platform words / edits
      - starts with movie/film
    """
    s = (raw or "").strip()
    if not s:
        return -999

    if _lens_bad_candidate(s):
        return -500

    score = 0

    # explicit (YEAR)
    if re.search(r"\(\s*(19\d{2}|20\d{2})\s*\)", s):
            score += 40
    if re.search(r"\b(19\d{2}|20\d{2})\b", s):
            score += 25

    # word count preference
    words = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє0-9'’\-]+", s)
    wc = len(words)
    if 1 <= wc <= 6:
        score += 80
    elif wc <= 10:
        score += 35
    else:
        score -= 60

    # starts with movie/film is suspicious
    if re.match(r"(?i)^\s*(movie|film|фильм|кино)\b", s):
        score -= 80

    # length penalty
    L = len(s)
    if L <= 35:
        score += 35
    elif L <= 60:
        score += 10
    else:
        score -= L - 60

    # must contain letters
    if not any(ch.isalpha() for ch in s):
        score -= 120

    return score


def _pick_best_lens_candidates(lens_cands: list[str], *, limit: int = 12) -> list[str]:
    """
    Returns candidates ordered by best-first.
    Includes cleaned variants; keeps uniqueness.
    """
    cands = [c for c in (lens_cands or []) if (c or "").strip()]
    ranked = sorted(cands, key=_lens_score_candidate, reverse=True)

    out: list[str] = []
    seen = set()

    for raw in ranked:
        if len(out) >= limit:
            break
        # use raw first, then cleaned
        for cand in (raw, _lens_clean_candidate(raw)):
            cand = (cand or "").strip()
            if not cand:
                continue
            key = cand.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)

    return out[:limit]


def _is_explicit_title(item: dict) -> bool:
    try:
        title = str(item.get("title") or item.get("name") or "")
    except Exception:
        return False
    return _is_explicit_text(title)


def _scrub_media_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("adult"):
            continue
        if isinstance(it, dict) and _is_explicit_title(it):
            continue
        out.append(_scrub_media_item(it) if isinstance(it, dict) else it)
    return out


def _title_tokens(x: str) -> set[str]:
    x = (x or "").lower()
    x = x.replace("ё", "е")
    out = []
    w = []
    for ch in x:
        if ch.isalnum() or ch in ("-", " "):
            w.append(ch)
        else:
            w.append(" ")
    x = "".join(w)
    x = " ".join(x.split())
    for t in x.split():
        if len(t) > 1:
            out.append(t)
    return set(out)

def _tmdb_score_item(query: str, it: dict, *, year_hint: str | None = None, lang_hint: str | None = None) -> tuple[float, str]:
    """Return (score 0..1, why_short)."""
    q = (query or "").strip()
    title = (it.get("title") or it.get("name") or "").strip()
    orig_lang = (it.get("original_language") or "").strip().lower()
    year = str(it.get("year") or "")[:4]

    ql = q.lower()
    tl = title.lower()

    score = 0.0
    why = []

    # title match
    if title and q:
        if tl == ql:
            score += 0.55
            why.append("точное совпадение названия")
        elif ql and (ql in tl or tl in ql):
            score += 0.40
            why.append("совпали ключевые слова в названии")
        else:
            qt = _title_tokens(q)
            tt = _title_tokens(title)
            if qt and tt:
                inter = len(qt & tt)
                uni = len(qt | tt)
                j = inter / max(1, uni)
                score += 0.35 * min(1.0, j * 1.8)
                if inter:
                    why.append("частичное совпадение слов")

    # year match
    if year_hint and year and year_hint == year:
        score += 0.18
        why.append("совпадает год")

    # stabilizers
    pop = float(it.get("popularity") or 0.0)
    vc = float(it.get("vote_count") or 0.0)
    score += min(0.12, (pop / 200.0) * 0.12)
    score += min(0.10, (vc / 5000.0) * 0.10)

    # language hint
    if lang_hint:
        lh = (lang_hint or "").lower().strip()
        if lh and orig_lang and lh == orig_lang:
            score += 0.05

    score = max(0.0, min(1.0, score))
    return score, (", ".join(why[:2]) if why else "похоже по общим признакам")

def _format_media_ranked(query: str, items: list[dict], *, year_hint: str | None = None, lang: str = "ru", source: str = "tmdb") -> str:
    """Best match + why + 2–3 alternatives. Threshold to avoid junk."""
    if not items:
        return MEDIA_NOT_FOUND_REPLY_RU

    scored = []
    for it in items:
        sc, why = _tmdb_score_item(query, it, year_hint=year_hint, lang_hint=("ru" if lang == "ru" else None))
        scored.append((sc, why, it))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_sc, best_why, best = scored[0]
    alts = scored[1:4]

    TH = 0.58
    if best_sc < TH:
        lines = ["🎬 Похоже, но уверенности мало.", ""]
        for i, (sc, why, it) in enumerate(scored[:2], start=1):
            t = (it.get("title") or it.get("name") or "—")
            y = (it.get("year") or "—")
            r = (it.get("vote_average") or "—")
            lines.append(f"{i}) {t} ({y}) — ⭐ {r} · {why}")
        lines += ["", "🧩 Уточни 1 деталь: год / актёр / страна / что происходит в сцене — и я добью точно."]
        return "\n".join(lines)

    t = (best.get("title") or best.get("name") or "—")
    y = (best.get("year") or "—")
    r = (best.get("vote_average") or "—")
    lines = [
        f"✅ Лучшее совпадение: {t} ({y}) — ⭐ {r}",
        f"Почему: {best_why}.",
    ]
    if alts:
        lines.append("")
        lines.append("Альтернативы:")
        for i, (sc, why, it) in enumerate(alts, start=1):
            tt = (it.get("title") or it.get("name") or "—")
            yy = (it.get("year") or "—")
            rr = (it.get("vote_average") or "—")
            lines.append(f"{i}) {tt} ({yy}) — ⭐ {rr}")
    lines += ["", "Кнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"]
    return "\n".join(lines)


def _media_confident(item: dict) -> bool:
    """Conservative confidence heuristic for Vision results."""
    try:
        pop = float(item.get("popularity") or 0)
        va = float(item.get("vote_average") or 0)
    except Exception:
        return False
    return (pop >= 25 and va >= 6.8) or (pop >= 60) or (va >= 7.6)


def _extract_media_kind_marker(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^__MEDIA_KIND__:(voice|video|video_note)\b", t)
    return m.group(1) if m else ""


MEDIA_VIDEO_STUB_REPLY_RU = (
    "Понял. По видео/кружку/голосу я скоро научусь находить фильмы/серии.\n"
    "Пока так: пришли 1 кадр (скрин) или опиши сцену текстом (1–2 факта) + год/актёр, если знаешь."
)


def _is_asking_for_title(text: str) -> bool:
    t = (text or "").strip().lower()
    pats = (
        "какое название",
        "как называется",
        "название фильма",
        "название у фильма",
        "как называется фильм",
        "как называется этот фильм",
        "что за название",
    )
    return any(x in t for x in pats)


def _is_affirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    return (
        bool(re.match(r"^(да|ага|угу)\b", t))
        or t.startswith("это ")
        or t.startswith("да,")
        or t.startswith("да ")
    )


def _extract_search_query_from_text(s: str) -> str:
    s = s or ""
    m = re.search(r"(?im)^\s*SEARCH_QUERY:\s*(.*)\s*$", s)
    if m:
        return (m.group(1) or "").strip()
    return ""


def _normalize_tmdb_query(q: str, *, max_len: int = 140) -> str:
    """
    TMDb search query must be short and clean.
    - collapse whitespace/newlines
    - strip quotes/markdown-ish noise
    - hard truncate
    """
    q = (q or "").strip()
    if not q:
        return ""

    # remove "SEARCH_QUERY:" if user pasted it
    q = re.sub(r"(?im)^\s*SEARCH_QUERY:\s*", "", q).strip()

    # collapse whitespace/newlines
    q = re.sub(r"\s+", " ", q).strip()

    # avoid super-long paragraphs (TMDb can return 400)
    if len(q) > max_len:
        q = q[:max_len].rsplit(" ", 1)[0].strip()

    # remove leading generic junk
    q = re.sub(r"^(что за|как называется)\s+", "", q, flags=re.I).strip()
    q = re.sub(r"^(фильм|сериал|мульт(ик)?|кино)\s+", "", q, flags=re.I).strip()
    return q


# --- BAD OCR / GENERIC QUERY FILTER FOR MEDIA SEARCH ---
BAD_MEDIA_QUERY_WORDS = {
    "news",
    "sport",
    "sports",
    "channel",
    "subscribe",
    "live",
    "official",
    "trailer",
    "shorts",
    "tiktok",
    "instagram",
    "reels",
    "главные",
    "новости",
    "канал",
    "подпишись",
    "подписаться",
    "смотрите",
    "запись",
    "обзор",
    "интервью",
    "edit",
    "edits",
    "compilation",
    "fanmade",
    "youtube",
    "music",
    "video",
}


GENERIC_TITLE_WORDS = {
    # EN
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "girl",
    "guy",
    "people",
    "person",
    "kid",
    "kids",
    "movie",
    "film",
    "series",
    "tv",
    "show",
    "clip",
    "scene",
    "video",
    "shorts",
    "trailer",
    # RU/UA
    "мужчина",
    "мужчины",
    "женщина",
    "женщины",
    "парень",
    "девушка",
    "люди",
    "человек",
    "ребенок",
    "ребёнок",
    "фильм",
    "кино",
    "сериал",
    "мульт",
    "мультик",
    "кадр",
    "сцена",
    "момент",
    "видео",
    "шортс",
    "трейлер",
}
# --- media query cleaning: turn human phrasing into search-friendly query ---
_MEDIA_LEADING_NOISE = (
    "название фильма",
    "название сериала",
    "название мультика",
    "как называется",
    "что за фильм",
    "что за сериал",
    "что за мультик",
    "какой фильм",
    "какой сериал",
    "какой мультик",
    "какой кинчик",
    "какой кенчик",
    "откуда этот отрывок",
    "что за хуйня",
    "че за хуйня",
    "шо за хуйня",
)

_MEDIA_NOISE_REGEX = [
    r"\bв главной роли\b",
    r"\bглавная роль\b",
    r"\bактер(ы|а)?\b",
    r"\bактриса\b",
    r"\bкто играет\b",
    r"\bнужно название\b",
    r"\bподскажи\b",
    r"\bпожалуйста\b",
]


def _media_clean_user_query(q: str) -> str:
    q0 = (q or "").strip()
    if not q0:
        return ""

    ql = q0.lower().strip()

    # remove leading canned phrases
    for p in _MEDIA_LEADING_NOISE:
        ql = ql.replace(p, " ")

    # remove other noise patterns
    for pat in _MEDIA_NOISE_REGEX:
        ql = re.sub(pat, " ", ql, flags=re.IGNORECASE)

    # normalize punctuation
    ql = re.sub(r"[“”\"'`]", " ", ql)
    ql = re.sub(r"[,.;:!?()\{\}<>/\\\\|_+=~\-]+", " ", ql)
    ql = re.sub(r"\s{2,}", " ", ql).strip()

    # fallback to original if we over-cleaned
    return ql if ql else q0


def _is_bad_media_query(q: str) -> bool:
    ql = (q or "").lower().strip()
    if not ql:
        return True

    # слишком короткий мусор
    if len(ql) < 3:
        return True

    words = ql.split()

    # ✅ одно слово — НЕ всегда мусор: допускаем короткие/брендовые тайтлы
    # но режем "news", "sport", "trailer", "subscribe" и т.п.
    if len(words) == 1:
        w = words[0]
        if w in GENERIC_TITLE_WORDS:
            return True
        # цифро-мусор / слишком мало букв
        letters = sum(ch.isalpha() for ch in w)
        digits = sum(ch.isdigit() for ch in w)
        if letters < 3:
            return True
        if digits > 0 and letters < 4:
            return True
        # стоп-слова
        for sw in BAD_MEDIA_QUERY_WORDS:
            if sw in w:
                return True
        return False

    # содержит стоп-слова
    for sw in BAD_MEDIA_QUERY_WORDS:
        if sw in ql:
            return True

    # слишком много цифр
    if sum(c.isdigit() for c in ql) > len(ql) * 0.4:
        return True

    return False


# --- media session cache (in-memory, no DB migrations) ---
MEDIA_CTX_TTL_SEC = 20 * 60  # 20 minutes



log = logging.getLogger("media")


def _d(event: str, **kw) -> None:
    """Structured debug logger for media/vision pipeline."""
    safe = {}
    for k, v in kw.items():
        try:
            import json as _json

            _json.dumps(v, ensure_ascii=False, default=str)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    try:
        log.info("[media] %s | %s", event, safe)
    except Exception:
        pass


_MEDIA_TTL_SEC = 10 * 60
_MEDIA_SESSIONS: dict[str, dict] = {}


def _media_uid(user: Any) -> str:
    # prefer tg_id, fallback to db id
    if not user:
        return ""
    v = getattr(user, "tg_id", None) or getattr(user, "id", None)
    return str(v) if v is not None else ""


def _media_get(uid: str) -> Optional[dict]:
    if not uid:
        return None
    s = _MEDIA_SESSIONS.get(uid)
    if not s:
        return None
    if (_time_now() - float(s.get("ts", 0))) > _MEDIA_TTL_SEC:
        _MEDIA_SESSIONS.pop(uid, None)
        return None
    return s


def _media_set(uid: str, query: str, items: list[dict]) -> None:
    if not uid:
        return
    q = _normalize_tmdb_query(query)
    _MEDIA_SESSIONS[uid] = {
        "query": _tmdb_sanitize_query(q),
        "items": items or [],
        "ts": _time_now(),
    }


def _looks_like_choice(text: str) -> bool:
    t = (text or "").strip()
    return bool(re.fullmatch(r"\d{1,2}", t))


def _looks_like_year_or_hint(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False

    # год
    if re.search(r"\b(19\d{2}|20\d{2})\b", t):
        return True

    # 1–2 слова (часто это уточнение: "Америка", "США", "комедия", "Netflix")
    parts = t.split()
    if 1 <= len(parts) <= 2 and len(t) <= 18:
        return True

    # короткие уточнения: актёр/страна/язык/год/серия/эпизод + страны/аббревиатуры
    hint_words = (
        "год",
        "акт",
        "актер",
        "актёр",
        "страна",
        "язык",
        "серия",
        "эпизод",
        "сезон",
        "сша",
        "америка",
        "usa",
        "us",
        "uk",
        "нетфликс",
        "netflix",
        "hbo",
        "amazon",
    )
    return any(w in t for w in hint_words)


def _extract_year(text: str) -> Optional[str]:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", (text or ""))
    return m.group(1) if m else None


def _format_one_media(item: dict) -> str:
    # items come from tmdb_search_multi(): title/year/media_type/overview/vote_average
    title = (item.get("title") or item.get("name") or "Без названия").strip()
    year = (item.get("year") or "").strip()
    overview = (item.get("overview") or "").strip()
    rating = item.get("vote_average", None)
    kind = (item.get("media_type") or "").strip()
    kind_ru = (
        "сериал" if kind == "tv" else "фильм" if kind == "movie" else kind or "медиа"
    )

    line = f"🎬 {title}"
    if year:
        line += f" ({year})"
    line += f" — {kind_ru}"

    if rating is not None:
        try:
            line += f" • ⭐ {float(rating):.1f}"
        except Exception:
            pass

    if overview:
        line += f"\n\n{overview[:700]}"
    return line


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default


def _pick_model() -> str:
    return _env("ASSISTANT_MODEL", "gpt-4.1-mini")


def _user_name(user: Optional[User]) -> str:
    for attr in ("first_name", "name", "username"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return "друг"


def _user_tz(user: Optional[User]) -> ZoneInfo:
    tz_name = getattr(user, "tz", None) or "UTC"
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo("UTC")


def _assistant_plan(user: Optional[User]) -> str:
    if not user:
        return "free"

    now = datetime.now(timezone.utc)
    # если premium_until есть и он истёк → FREE
    pu = getattr(user, "premium_until", None)
    if pu is not None:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        if pu <= now:
            return "free"

    # если premium_until нет и is_premium=False → FREE
    if pu is None and not bool(getattr(user, "is_premium", False)):
        return "free"

    # премиум есть → читаем тариф
    plan = str(getattr(user, "premium_plan", "") or "").strip().lower()
    if plan in {"basic", "pro"}:
        return plan

    # дефолтный тариф премиума
    return "basic"


def _now_str_user(user: Optional[User]) -> str:
    tz = _user_tz(user)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


def _is_media_query(text: str) -> bool:
    t = (text or "").lower()
    # ключевые слова + типичные запросы на поиск названия
    keys = (
        "фильм",
        "сериал",
        "кино",
        "мульт",
        "мультик",
        "лента",
        "кадр",
        "по кадру",
        "по этому кадру",
        "season",
        "episode",
        "movie",
        "tv",
        "series",
        "актёр",
        "актер",
        "режисс",
        "персонаж",
        "как называется",
        "что за фильм",
        "что за сериал",
        "что за мультик",
    )
    return any(k in t for k in keys)


def _is_noise(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    if letters == 0:
        return True

    # суперкороткое почти всегда мусор (но 1-2 слова иногда важны)
    if len(s) <= 3:
        return True

    tokens = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє]+", s.lower())
    if tokens:
        most = max(tokens.count(x) for x in set(tokens))
        if most / max(1, len(tokens)) >= 0.6 and len(tokens) >= 4:
            return True

        if len(tokens) >= 4:
            uniq = set(tokens)
            if len(uniq) <= 2 and all(tokens.count(t) >= 2 for t in uniq):
                return True

    # ник/идентификатор без пробелов с подчёркиванием (Pisya_Popa)
    if "_" in s and " " not in s and len(s) <= 20:
        return True

    return False


def _as_user_ts(user: Optional[User], ts: Any) -> str:
    """
    created_at из sqlite может быть naive.
    Считаем naive как UTC (это самый безопасный дефолт для серверного времени),
    потом переводим в tz юзера.
    """
    if ts is None:
        return "?"
    try:
        tz = _user_tz(user)
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return ts.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "?"


async def _fetch_recent_journal(
    session: Any,
    user: Optional[User],
    *,
    limit: int = 30,
    take: int = 5,
) -> list[tuple[str, str]]:
    if not session or not user:
        return []

    q = (
        select(JournalEntry.created_at, JournalEntry.text)
        .where(JournalEntry.user_id == user.id)
        .order_by(desc(JournalEntry.created_at))
        .limit(limit)
    )
    res = await session.execute(q)
    rows = res.all()

    out: list[tuple[str, str]] = []

    for created_at, text in rows:
        txt = (text or "").strip()
        if _is_noise(txt):
            continue

        created_str = _as_user_ts(user, created_at)
        out.append((created_str, txt[:700]))
        if len(out) >= take:
            break

    return out


async def build_context(
    session: Any, user: Optional[User], lang: str, plan: str
) -> str:
    parts: list[str] = []
    parts.append(f"Time now: {_now_str_user(user)}")

    if user:
        parts.append(
            "User: "
            f"id={getattr(user, 'id', None)}, "
            f"tg_id={getattr(user, 'tg_id', None)}, "
            f"name={_user_name(user)}, "
            f"tz={getattr(user, 'tz', None)}"
        )

        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used:
            parts.append(f"Assistant last used at: {last_used}")

        profile = getattr(user, "assistant_profile_json", None)
        if profile:
            parts.append("Assistant profile (long-term):")
            parts.append(str(profile)[:2000])

    take = 0 if plan in {"free", "basic"} else 5

    recent = await _fetch_recent_journal(session, user, limit=30, take=take)
    if recent:
        parts.append("Recent journal entries:")
        for ts, txt in recent:
            parts.append(f"- [{ts}] {txt}")

    return "\n".join(parts)


def _instructions(lang: str, plan: str) -> str:
    base_map = {
        "ru": (
            "Ты — личный помощник в Telegram. Пиши по-русски.\n"
            "Не оценивай настроение и не делай психоанализ.\n"
            "Если данных не хватает — задай 1 уточняющий вопрос.\n"
        ),
        "uk": (
            "Ти — особистий помічник у Telegram. Пиши українською.\n"
            "Не оцінюй настрій і не роби психоаналіз.\n"
            "Якщо бракує даних — постав 1 уточнювальне питання.\n"
        ),
        "en": (
            "You are a personal Telegram assistant. Reply in English.\n"
            "Do not psychoanalyze mood.\n"
            "If info is missing — ask 1 clarifying question.\n"
        ),
    }

    base = base_map.get(lang, base_map["en"])

    style = (
        "Правила ответа:\n"
        "- Не используй шаблоны 'Суть/План/Шаги' и нумерацию, если не просят.\n"
        "- Без психоанализа и диагнозов.\n"
        "- Коротко и по делу.\n"
    )

    if plan == "basic":
        return (
            base
            + style
            + (
                "Режим BASIC:\n"
                "- 2–6 предложений.\n"
                "- Без планов и стратегий без запроса.\n"
                "- Журнал не использовать как память.\n"
            )
        )

    return (
        base
        + style
        + (
            "Режим PRO:\n"
            "- Можно использовать последние записи журнала как контекст.\n"
            "- Можно предлагать чеклисты и структуру.\n"
            "- Можно задать до 2 уточняющих вопросов.\n"
            "- Стиль: умный близкий помощник.\n"
        )
    )


async def run_assistant(
    user: Optional[User],
    text: str,
    lang: str,
    *,
    session: Any = None,
    has_media: bool = False,
) -> str:
    if AsyncOpenAI is None:
        return "🤖 Ассистент временно недоступен (сервер без openai).\nПопробуй позже или напиши в поддержку."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {
            "uk": "❌ Не задано OPENAI_API_KEY. Додай ключ у .env / змінні середовища.",
            "en": "❌ OPENAI_API_KEY is missing. Add it to env/.env.",
            "ru": "❌ Не задан OPENAI_API_KEY. Добавь ключ в .env / переменные окружения.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()
    plan = _assistant_plan(user)

    kind_marker = _extract_media_kind_marker(text)
    if kind_marker:
        return MEDIA_VIDEO_STUB_REPLY_RU

    now = datetime.now(timezone.utc)

    # --- MEDIA state (DB + in-memory fallback) ---
    uid = _media_uid(user)
    st = _media_get(uid)  # in-memory session, survives even if session=None

    sticky_media_db = False
    if user:
        mode = getattr(user, "assistant_mode", None)
        until = getattr(user, "assistant_mode_until", None)
        if mode == "media" and until and until > now:
            sticky_media_db = True

    # --- INTENT gate (prevents media context from leaking into other topics) ---
    intent_res = detect_intent((text or '').strip() if text else None, has_media=bool(has_media))
    intent = getattr(intent_res, 'intent', None) or intent_res
    is_intent_media = intent in (Intent.MEDIA_IMAGE, Intent.MEDIA_TEXT)

    # If user message is NOT media-related, we must drop sticky media (DB + memory)
    if not is_intent_media:
        if uid:
            try:
                _MEDIA_SESSIONS.pop(uid, None)
            except Exception:
                pass
        if user is not None:
            try:
                mode = getattr(user, 'assistant_mode', None)
                if mode == 'media':
                    setattr(user, 'assistant_mode', None)
                    setattr(user, 'assistant_mode_until', now - timedelta(seconds=1))
                    if session:
                        await session.commit()
            except Exception:
                pass    # IMPORTANT: media mode should trigger ONLY for media intents (or real media message)
    # st/sticky are allowed to keep follow-ups ONLY when current intent is media.
    is_media = bool(has_media) or bool(is_intent_media) or (sticky_media_db and bool(is_intent_media)) or (bool(st) and bool(is_intent_media))


    if is_media:
        _d(
            "media.enter",
            is_media=is_media,
            sticky_media_db=sticky_media_db,
            has_st=bool(st),
            uid=uid,
        )  # DBG_MEDIA_RUN_ASSISTANT_V1
        raw_text = (text or "").strip()

        # 1) User picked an option number: "1", "2", ...
        if st and _looks_like_choice(raw_text):
            idx = int(raw_text) - 1
            opts = st.get("items") or []
            if 0 <= idx < len(opts):
                picked = opts[idx]
                return (
                    _format_media_pick(picked)
                    + "\n\nХочешь — напиши другое название/описание, я поищу ещё."
                )

        # 1.5) "Как называется/какое название" — это не новый поиск, показываем варианты
        if st and _is_asking_for_title(raw_text):
            opts = st.get("items") or []
            if not opts:
                return MEDIA_NOT_FOUND_REPLY_RU
            return build_media_context(opts) + "\n\nВыбери номер варианта."
        # 2) Build query (new query vs follow-up hint)# 2) Merge уточнение with previous query
        # 2) Build query (new query vs follow-up hint)
        raw = raw_text
        prev_q = ((st.get("query") if st else "") or "").strip()

        # не даём "ядовитым" фразам портить поисковую строку
        if st and re.search(
            r"(?i)\b(не\s*то|не\s*подходит|ничего\s*не|такого\s*фильма|не\s*существует)\b",
            raw,
        ):
            return MEDIA_NOT_FOUND_REPLY_RU

        # короткое уточнение (год/актёр/страна/язык/серия/эпизод) — добавляем к прошлому запросу
        raw = _normalize_tmdb_query(raw)
        if st and prev_q and _looks_like_year_or_hint(raw) and len(raw) <= 60:
            query = _tmdb_sanitize_query(_normalize_tmdb_query(f"{prev_q} {raw}"))
        else:
            query = _tmdb_sanitize_query(_normalize_tmdb_query(raw))
        _d("media.built_query", prev_q=prev_q, raw=raw, query=query)

        # 3) Too generic → ask 1 detail
        if len(query) < 6 and ("фильм" in query.lower() or "что за" in query.lower()):
            # keep media mode alive for follow-ups even without DB session
            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session:
                    await session.commit()
            return MEDIA_NOT_FOUND_REPLY_RU

        # 4) Best-effort TMDb search (ru first, fallback en, year filter, dedupe, sort)
        cleaned = _normalize_tmdb_query(query)
        query = _tmdb_sanitize_query(_normalize_tmdb_query(cleaned or query))

        try:
            items = []

            # 🔹 First try direct search by model/caption query
            items = await _tmdb_best_effort(query, limit=5)
            items = _scrub_media_items(items)
            _d(
                "media.tmdb.primary",
                q=query,
                n=len(items or []),
                top=((items or [{}])[0].get("title") or (items or [{}])[0].get("name"))
                if items
                else None,
            )

            # 🔹 If nothing found — use parsed hints
            hints = _parse_media_hints(query)
            if (not items) and hints.get("keywords"):
                items = await _tmdb_best_effort(hints["keywords"], limit=5)

            if not items and hints.get("cast"):
                from app.services.media_search import (
                    tmdb_search_person,
                    tmdb_discover_with_people,
                )

                for actor in hints["cast"]:
                    pid = await tmdb_search_person(actor)
                    if pid:
                        items = await tmdb_discover_with_people(
                            pid,
                            year=hints.get("year"),
                            kind=hints.get("kind"),
                        )
                        if items:
                            break

        except Exception:
            items = []

        # If user sent a long free-form scene description, TMDb guesses are often noisy.
        # In that case, force WEB pipeline to extract the real title.
        try:
            if items and raw and _looks_like_freeform_media_query(raw):
                items = []
        except Exception:
            pass

        # --- WEB fallback (cheap -> expensive) ---
        # порядок:
        # 1) wiki/brave (без SerpAPI)
        # 2) SerpAPI только если есть ключ
        if not items and query:
            query = _normalize_tmdb_query(query)
            async def _try_cands(cands: list[str]) -> list[dict]:
                out: list[dict] = []
                for c in (cands or [])[:15]:
                    if _is_bad_media_query(c):
                        continue
                    c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
                    if not _good_tmdb_cand(c):
                        continue
                    out = await _tmdb_best_effort(c, limit=5)
                    if out:
                        return out
                return out

            try:
                # --- media refinement guard ---
                # If user sends non-digit while media session is active, treat it as query refinement.
                if is_intent_media and (st or sticky_media_db) and text:
                    t = text.strip()
                    if t and (not re.fullmatch(r"\d+", t)) and (not t.startswith("/")):
                        query = t
                        items = []
                # --- end guard ---
                cands, tag = await web_to_tmdb_candidates(query, use_serpapi=False)
                _d(
                    "media.web.cands",
                    use_serpapi=False,
                    tag=tag,
                    n=len(cands or []),
                    sample=(cands or [])[:5],
                )
                items = await _try_cands(cands)
            except Exception:
                items = []

            # SerpAPI — только если всё ещё пусто и реально есть ключ
            if (not items) and (
                os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
            ):
                try:
                    cands, tag = await web_to_tmdb_candidates(query, use_serpapi=True)
                    _d(
                        "media.web.cands_serp",
                        use_serpapi=True,
                        tag=tag,
                        n=len(cands or []),
                        sample=(cands or [])[:5],
                    )
                    items = await _try_cands(cands)
                except Exception:
                    pass

        # keep sticky media mode (DB if possible)
        if user is not None:
            setattr(user, "assistant_mode", "media")
            setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
            if session:
                await session.commit()

        if not items:
            # keep last query in memory so next hint still treated as media
            if uid:
                _media_set(uid, query, [])
            return MEDIA_NOT_FOUND_REPLY_RU

        items = _scrub_media_items(items)
        if uid:
            _media_set(uid, query, items)
        return _format_media_ranked(query, items, year_hint=_parse_media_hints(query).get('year'), lang=lang, source='tmdb')

    # ---- Normal assistant (non-media) ----
    ctx = await build_context(session, user, lang, plan)

    prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
    if user:
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
            prev_id = None

    prompt = f"Context:\n{ctx}\n\nUser message:\n" + (text or "") + "\n"

    resp = await client.responses.create(
        previous_response_id=prev_id,
        model=model,
        instructions=_instructions(lang, plan),
        input=prompt,
        max_output_tokens=(260 if plan == "basic" else 650),
    )

    if session:
        await log_llm_usage(
            session,
            user_id=getattr(user, "id", None) if user else None,
            feature="assistant",
            model=model,
            plan=plan,
            resp=resp,
            meta={"lang": lang},
        )

    out_text = (getattr(resp, "output_text", None) or "").strip()

    resp_id = getattr(resp, "id", None)
    if session and user and resp_id:
        changed = False
        if user.assistant_prev_response_id != str(resp_id):
            user.assistant_prev_response_id = str(resp_id)
            changed = True
        user.assistant_last_used_at = datetime.now(timezone.utc)
        changed = True

        if changed:
            await session.commit()

    if out_text:
        return out_text

    try:
        return str(getattr(resp, "output", "")).strip() or "⚠️ Empty response."
    except Exception:
        return "⚠️ Не смог прочитать ответ модели."


def _extract_media_json_from_model_text(text: str) -> Optional[dict]:
    """
    Extract JSON object from model output.
    Supports:
      - strict first-line JSON (the very first line is {...})
      - fenced ```json ... ```
      - fenced ``` ... ```
      - marker MEDIA_JSON: { ... }
      - last-resort {...} block
    Returns dict or None.
    """
    t = (text or "").strip()
    if not t:
        return None

    # strict first-line JSON
    if t.startswith("{"):
        first = t.splitlines()[0].strip()
        if first.endswith("}"):
            try:
                obj = json.loads(first)
                return obj if isinstance(obj, dict) else None
            except Exception:
                pass

    # fenced ```json ... ```
    m = re.search(r"```json\s*(\{.*?\})\s*```", t, flags=re.S)
    if not m:
        m = re.search(r"```(?:\w+)?\s*(\{.*?\})\s*```", t, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    # marker MEDIA_JSON:
    m = re.search(r"(?im)^\s*MEDIA_JSON\s*:\s*(\{.*\})\s*$", t, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    # last resort: first {...}
    m = re.search(r"(\{.*\})", t, flags=re.S)
    if m:
        blob = m.group(1).strip()
        if len(blob) > 8000:
            blob = blob[:8000]
            blob = blob.rsplit("}", 1)[0] + "}"
        try:
            obj = json.loads(blob)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    return None


def _build_tmdb_queries_from_media_json(mj: Optional[dict]) -> list[str]:
    """
    Converts extracted media JSON into a prioritized list of short TMDb queries.
    Supports BOTH schemas:
      A) New vision schema:
         {"actors":[...],"title_hints":[...],"keywords":[...]}
      B) Legacy schema:
         {"title":..., "year":..., "alt_titles"/"aka":..., "cast":..., "keywords":...}
    """
    if not mj or not isinstance(mj, dict):
        return []

    out: list[str] = []

    def add(q: str) -> None:
        q = _tmdb_sanitize_query(_normalize_tmdb_query(q))
        if q and q not in out and _good_tmdb_cand(q):
            out.append(q)

    def norm_list(x: Any) -> list[str]:
        if not x:
            return []
        if isinstance(x, str):
            x = [x]
        if not isinstance(x, list):
            return []
        res: list[str] = []
        for it in x:
            s = (str(it) if it is not None else "").strip()
            if not s:
                continue
            s2 = _tmdb_sanitize_query(_normalize_tmdb_query(s))
            if s2 and s2 not in res:
                res.append(s2)
        return res

    # A) new schema
    actors = norm_list(mj.get("actors"))
    title_hints = norm_list(mj.get("title_hints"))
    keywords_new = norm_list(mj.get("keywords"))

    # B) legacy schema
    title = str(mj.get("title") or "").strip()
    year = str(mj.get("year") or "").strip()
    sxxeyy = str(mj.get("sxxeyy") or mj.get("episode") or "").strip()

    alts = mj.get("alt_titles") or mj.get("aka") or []
    if isinstance(alts, str):
        alts = [alts]

    cast_legacy = norm_list(mj.get("cast"))
    keywords_legacy = norm_list(mj.get("keywords"))

    # Priority 1: title hints / title
    for t in (title_hints or [])[:6]:
        add(t)

    if title:
        add(f"{title} {year}".strip() if year else title)
        if sxxeyy:
            add(f"{title} {sxxeyy}".strip())

    # alt titles
    if isinstance(alts, list):
        for a in alts[:5]:
            a = str(a).strip()
            if not a:
                continue
            add(f"{a} {year}".strip() if year else a)
            if sxxeyy:
                add(f"{a} {sxxeyy}".strip())

    # Priority 2: actors combo (2 names)
    if len(actors) >= 2:
        add(f"{actors[0]} {actors[1]}")
    elif len(actors) == 1:
        add(actors[0])

    # Priority 2b: legacy cast with title
    if title and cast_legacy:
        add(f"{title} {' '.join(cast_legacy[:2])}".strip())

    # Priority 3: keywords
    for k in (keywords_new or [])[:6]:
        k = (k or "").strip()
        if not k:
            continue
        kl = k.lower()
        # не даём общим словам попадать в TMDb
        if (" " not in kl) and (kl in GENERIC_TITLE_WORDS):
            continue
        if len(k) < 4:
            continue
        add(k)

    if not keywords_new:
        joined = " ".join((keywords_legacy or [])[:6]).strip()
        if joined:
            jl = joined.lower().strip()
            if jl in GENERIC_TITLE_WORDS:
                joined = ""
        if joined:
            add(joined[:80])

    return out[:12]


async def run_assistant_vision(
    user: Optional[User],
    image_bytes: bytes,
    caption: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    if AsyncOpenAI is None:
        return "🤖 Vision временно недоступен (сервер без openai)."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {
            "uk": "❌ Не задано OPENAI_API_KEY.",
            "en": "❌ OPENAI_API_KEY is missing.",
            "ru": "❌ Не задан OPENAI_API_KEY.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    plan = _assistant_plan(user)
    if plan != "pro":
        return {
            "ru": "Фото доступно только в PRO.",
            "uk": "Фото доступне лише в PRO.",
            "en": "Photos are PRO-only.",
        }.get(lang, "PRO-only.")

    client = AsyncOpenAI(api_key=api_key)

    prompt_text = (caption or "").strip() or {
        "ru": "Определи, что на фото. Если это кадр из фильма/сериала/мульта — попробуй определить источник.",
        "uk": "Визнач, що на фото. Якщо це кадр з фільму/серіалу/мультфільму — спробуй визначити джерело.",
        "en": "Identify what’s in the image. If it’s a movie/series/cartoon frame, try to identify the source.",
    }.get(
        lang,
        "Identify the image and, if it's a movie/series/cartoon frame, try to identify the source.",
    )

    hard_keywords = (
        "текст",
        "что написано",
        "прочитай",
        "скрин",
        "скриншот",
        "ошибка",
        "error",
        "traceback",
        "лог",
        "qr",
        "кьюар",
        "инструкция",
        "меню",
        "чек",
        "рецепт",
        "состав",
    )
    is_hard = any(k in prompt_text.lower() for k in hard_keywords)

    model_default = _env("ASSISTANT_VISION_MODEL", _pick_model())
    model_hard = _env("ASSISTANT_VISION_MODEL_HARD", model_default)
    model = model_hard if is_hard else model_default

    import base64

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    now = datetime.now(timezone.utc)

    instr = (
        ANTI_HALLUCINATION_PREFIX
        + _instructions(lang, plan)
        + "\n"
        + (
            "Ты видишь изображение.\n"
            "Если это кадр из фильма/сериала/мульта/аниме — помоги найти источник.\n"
            "Если не уверен — так и скажи. Не выдумывай детали.\n\n"
            "ВАЖНО: игнорируй хэштеги (#...), никнеймы (@...), эмодзи, UI-кнопки (Subscribe/Like/Share),\n"
            "названия каналов, музыку/название трека, лайки/просмотры и декоративный текст.\n\n"
            "Сначала верни СТРОГО JSON (без пояснений):\n"
            '{"actors":["..."],"title_hints":["..."],"keywords":["..."]}\n'
            "- actors: имена актёров/актрис (желательно латиницей), минимум 2 если распознаны\n"
            "- title_hints: возможное название/видимый тайтл (если есть)\n"
            "- keywords: 2–5 коротких слов про сцену/жанр (EN или RU)\n\n"
            "ПОТОМ (на новой строке) добавь:\n"
            "SEARCH_QUERY: <короткий запрос, максимум 6–8 слов>\n"
        )
    )

    try:
        resp = await client.responses.create(
            model=model,
            instructions=instr,
            input=cast(Any, [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ]),
            max_output_tokens=450,
        )
    except Exception as e:
        return {
            "ru": f"⚠️ Не смог обработать фото ({type(e).__name__}). Попробуй отправить фото меньшего размера или сжать скрин.",
            "uk": f"⚠️ Не зміг обробити фото ({type(e).__name__}). Спробуй надіслати менше фото або стиснути скрін.",
            "en": f"⚠️ I couldn’t process the photo ({type(e).__name__}). Try sending a smaller image or compress the screenshot.",
        }.get(lang, f"⚠️ Vision error: {type(e).__name__}")

    if session:
        await log_llm_usage(
            session,
            user_id=getattr(user, "id", None) if user else None,
            feature="vision",
            model=model,
            plan=plan,
            resp=resp,
            meta={"lang": lang},
        )

    out_text = (getattr(resp, "output_text", None) or "").strip()
    out_text = str(out_text)

    # trace.moe (anime) — только если модель явно сказала "аниме"
    if any(k in out_text.lower() for k in ("аниме", "anime")):
        try:
            result = await trace_moe_identify(image_bytes)
        except Exception:
            result = None

        if result:
            sim = float(result.get("similarity", 0) or 0)
            if sim >= 0.9:
                return (
                    "🎬 Это кадр из аниме.\n\n"
                    f"Название: {result.get('title')}\n"
                    f"Серия: {result.get('episode')}\n"
                    f"Совпадение: {sim:.1%}"
                )
    # иначе — не ломаем основной поток, просто идём дальше (TMDb)

    # --- Lens (bytes -> Spaces -> Google Lens -> candidates -> TMDb) ---
    # NOTE: works only if image_bytes is a real JPG/PNG; dummy bytes will produce empty cands.
    try:
        lens_cands, lens_tag = await image_bytes_to_tmdb_candidates(
            image_bytes,
            ext="jpg",
            use_serpapi_lens=True,
            hl=("ru" if (lang or "ru") == "ru" else "en"),
            prefix="frames",
        )
    except Exception:
        lens_cands, lens_tag = [], "lens_fail"
    _d(
        "vision.lens", lens_tag=lens_tag, lens_cands=(lens_cands or [])[:8]
    )  # DBG_VISION_LENS_V2
    best_lens_fallback: list[str] = []

    if lens_cands:
        try:
            items = []
            used_cand = ""

            ordered = _pick_best_lens_candidates(lens_cands, limit=12)
            ordered = (ordered or [])[:5]  # hard cap: 3–5 clean candidates

            best_lens_fallback = ordered[:8]
            _d("vision.lens.pick", ordered=ordered[:10])

            for cand in ordered:
                cand0 = cand

                # hard drop obvious junk BEFORE touching TMDb
                if _lens_bad_candidate(cand0):
                    continue

                cand0 = _normalize_tmdb_query(_clean_tmdb_query(cand0))
                if not cand0 or len(cand0) < 3:
                    continue
                if _is_bad_media_query(cand0):
                    continue

                cand0 = _tmdb_sanitize_query(_normalize_tmdb_query(cand0))
                if not _good_tmdb_cand(cand0):
                    continue
                items = await _tmdb_best_effort(cand0, limit=5)
                if items:
                    used_cand = cand0
                    break

        except Exception:
            items = []
            used_cand = ""

        if items:
            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session:
                    await session.commit()

            uid = _media_uid(user)
            if uid and used_cand:
                _media_set(uid, used_cand, items)

            return _format_media_ranked(used_cand, items, year_hint=_parse_media_hints(used_cand).get('year'), lang=lang, source='tmdb')

    # Vision → TMDb candidates (robust)

    # Vision → TMDb candidates (robust)
    caption_str = (caption or "").strip()
    _d(
        "vision.model_out",
        caption=caption_str[:120],
        out_text=(out_text or "")[:250],
        is_generic_caption=_is_generic_media_caption(caption_str),
    )  # DBG_VISION_MODEL_OUT_V2
    # Prefer explicit SEARCH_QUERY from model, then title extracted from the explanation.
    search_q = _normalize_tmdb_query(_extract_search_query_from_text(out_text))
    title_from_text = _normalize_tmdb_query(
        _extract_title_like_from_model_text(out_text)
    )
    _d(
        "vision.extract", search_q=search_q, title_from_text=title_from_text
    )  # DBG_VISION_EXTRACT_V2

    # CAND_LIST_JSON_PRIORITY_V1
    try:
        mj = _extract_media_json_from_model_text(out_text)
        json_queries = _build_tmdb_queries_from_media_json(mj)
        _d("vision.json", json_queries=(json_queries or [])[:10])
    except Exception as e:
        _d("vision.json.fail", err=type(e).__name__, msg=str(e)[:200])
        json_queries = []

    # Build candidate list in priority order (JSON -> model text)
    # Build candidate list in priority order:
    # 1) Vision JSON (actors/title_hints/keywords)
    # 2) Model SEARCH_QUERY / title extracted from text
    # 3) Caption (only if not generic)
    # 4) Lens fallback (only after Vision sources)
    cand_list: list[str] = []

    for c in (json_queries or []):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    for c in (search_q, title_from_text):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    # Caption is used ONLY if it is not a generic phrase like "Откуда кадр?"
    if caption_str and (not _is_generic_media_caption(caption_str)):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(caption_str))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    # Lens fallback goes LAST (weak source)
    for c in (best_lens_fallback or [])[:8]:
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    _d("vision.cand_list", cand_list=cand_list[:15])  # DBG_VISION_CAND_LIST_V3

    if not cand_list:
        return MEDIA_NOT_FOUND_REPLY_RU

    items: list[dict] = []
    used_query = ""

    # Try TMDb for each candidate
    for q in cand_list:
        if not _good_tmdb_cand(q):
            continue
        _d("vision.tmdb.try", q=q)  # DBG_VISION_TMBD_TRY_V2

        try:
            q = _normalize_tmdb_query(q)
            items = await _tmdb_best_effort(q, limit=5)
            items = [i for i in items if not i.get("adult")]
        except Exception:
            items = []
        if items:
            used_query = q
            try:
                top = items[0]
                _d(
                    "vision.tmdb.hit",
                    used_query=used_query,
                    top_title=(top.get("title") or top.get("name")),
                    top_year=top.get("year"),
                    top_type=top.get("media_type"),
                )
            except Exception:
                pass
            break

    # WEB fallback (only if still empty)
    if (not items) and cand_list:
        try:
            q0 = cand_list[0]
            use_serp = bool(os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY"))
            cands, tag = await web_to_tmdb_candidates(q0, use_serpapi=use_serp)

            for c in cands:
                if _is_bad_media_query(c):
                    continue
                c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
                c = _normalize_tmdb_query(c)
                if not _good_tmdb_cand(c):
                    continue
                items = await _tmdb_best_effort(c, limit=5)
                items = [i for i in items if not i.get("adult")]
                if items:
                    used_query = c
                    break
        except Exception:
            items = []

    if items:
        if user is not None:
            setattr(user, "assistant_mode", "media")
            setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
            if session:
                await session.commit()

        uid = _media_uid(user)
        if uid:
            _media_set(uid, used_query or (cand_list[0] if cand_list else ""), items)

        # Default: return title directly if confident (or single result)
        top = items[0]

        return _format_media_ranked(used_query or (cand_list[0] if cand_list else ''), items, year_hint=_parse_media_hints(used_query or (cand_list[0] if cand_list else '')).get('year'), lang=lang, source='tmdb')

    # --- Failsafe: Vision must always return text ---
    final_text = (out_text or "").strip()
    if final_text:
        return final_text
    return MEDIA_NOT_FOUND_REPLY_RU
