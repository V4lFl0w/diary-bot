from __future__ import annotations

<<<<<<< HEAD
# app/services/assistant.py
=======
from app.services.media.query import (
    _tmdb_sanitize_query, _normalize_tmdb_query, _good_tmdb_cand, GENERIC_TITLE_WORDS
)

# app/services/assistant.py (vision_parse.py)
>>>>>>> 50c59ea (fix(assistant/media): stabilize sticky queries + tmdb/vision parsing)
import json
import re
from typing import Any, Optional

from app.services.media.query import (
    GENERIC_TITLE_WORDS,
    _good_tmdb_cand,
    _normalize_tmdb_query,
    _tmdb_sanitize_query,
)

# --- compat exports for assistant.py (old imports) ---
try:
    from app.services.media_text import (
        is_generic_media_caption as _is_generic_media_caption,
    )  # type: ignore
except Exception:  # pragma: no cover

    def _is_generic_media_caption(text: str) -> bool:  # type: ignore
        t = (text or "").strip().lower()
        return t in {
            "откуда кадр",
            "откуда кадр?",
            "что за фильм",
            "что за фильм?",
            "как называется",
            "как называется?",
        }


# --- compat exports for assistant.py ---
try:
    from app.services.media.lens import _lens_bad_candidate  # type: ignore
except Exception:  # pragma: no cover

    def _lens_bad_candidate(x: str) -> bool:  # type: ignore
        return False


# --- compat exports for assistant.py ---
try:
    from app.services.media.lens import _pick_best_lens_candidates  # type: ignore
except Exception:  # pragma: no cover

    def _pick_best_lens_candidates(cands: list[str], limit: int = 12) -> list[str]:  # type: ignore
        return (cands or [])[:limit]


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


def _extract_search_query_from_text(s: str) -> str:
    s = s or ""
    m = re.search(r"(?im)^\s*SEARCH_QUERY:\s*(.*)\s*$", s)
    if m:
        return (m.group(1) or "").strip()
    return ""


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
    keywords_legacy = norm_list(mj.get("legacy_keywords"))

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
