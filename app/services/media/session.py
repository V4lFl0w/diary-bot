from __future__ import annotations

# --- FlowPatch: query cleaning & candidate filtering (media) ---
_TMDb_STOP = {
    "photo","<photo>","уточнение","уточнение:","уточни","дай","другие","варианты",
    "жанр","страна","год","серия","эпизод","сезон",
    "film","movie","series","tv","what","is","the","a","an",
    "drama","romance","prison","fence",  # частый шум из vision-json
}

def _mf_clean_query(q: str) -> str:
    if not q:
        return ""
    q = q.strip()
    # вырезаем "служебные" маркеры
    q = q.replace("<photo>", " ").replace("photo", " ")
    q = re.sub(r"(?i)\\bуточнение\\s*:\\s*", " ", q)
    q = re.sub(r"\\s+", " ", q).strip()

    # режем слишком длинные "простыни" (TMDb так и так не переварит)
    if len(q) > 120:
        q = q[:120].rsplit(" ", 1)[0].strip()

    return q

def _mf_is_worthy_tmdb(q: str) -> bool:
    if not q:
        return False
    qn = q.lower().strip()
    # одно слово типа "drama" / "romance" / "prison" — почти всегда мусор
    if " " not in qn and qn in _TMDb_STOP:
        return False
    # слишком короткое
    if len(qn) < 3:
        return False
    # выкидываем запросы, которые состоят в основном из стоп-слов
    toks = [t for t in re.split(r"[\\s,.;:!?()\\[\\]{}\"'«»]+", qn) if t]
    if toks and sum(1 for t in toks if t in _TMDb_STOP) / max(1, len(toks)) > 0.6:
        return False
    return True
# --- /FlowPatch ---


# app/services/assistant.py
import re
from time import time as _time_now
from typing import Any, Optional

from app.services.intent_router import Intent
from app.services.media.query import _normalize_tmdb_query, _tmdb_sanitize_query


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
    keys = [
        "sticky_media",
        "sticky",
        "st",
        "last_media",
        "media_ctx",
        "prev_q",
        "media_prev_q",
    ]
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


def _extract_year(text: str) -> Optional[str]:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", (text or ""))
    return m.group(1) if m else None
