from __future__ import annotations

from app.services.media.query import _normalize_tmdb_query, _tmdb_sanitize_query

# app/services/assistant.py
import re
from time import time as _time_now
from typing import Any, Optional

from app.services.intent_router import Intent


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

MEDIA_CTX_TTL_SEC = 20 * 60  # 20 minutes

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
