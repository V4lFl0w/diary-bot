from __future__ import annotations

# app/services/assistant.py






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
