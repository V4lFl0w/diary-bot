from __future__ import annotations

import os
import re
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_URL = "https://api.api-ninjas.com/v1/nutrition"
API_ENV_PRIMARY = "NINJAS_API_KEY"
API_ENV_FALLBACK = "NUTRITION_API_KEY"

# ---------- fallback база на 100г ----------
FALLBACK: Dict[str, Dict[str, float]] = {
    "молок": dict(kcal=60, p=3.2, f=3.2, c=4.7),
    "milk": dict(kcal=60, p=3.2, f=3.2, c=4.7),

    "банан": dict(kcal=89, p=1.1, f=0.3, c=23.0),
    "banana": dict(kcal=89, p=1.1, f=0.3, c=23.0),

    "арахис": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "арахіс": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "peanut": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "peanuts": dict(kcal=567, p=26.0, f=49.0, c=16.0),

    "греч": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "гречк": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "buckwheat": dict(kcal=343, p=13.3, f=3.4, c=71.5),

    "яйц": dict(kcal=143, p=13.0, f=10.0, c=1.1),
    "egg": dict(kcal=143, p=13.0, f=10.0, c=1.1),
    "eggs": dict(kcal=143, p=13.0, f=10.0, c=1.1),

    "хлеб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "хліб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "bread": dict(kcal=250, p=9.0, f=3.0, c=49.0),

    "сыр": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "сир": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "cheese": dict(kcal=350, p=26.0, f=27.0, c=3.0),

    "сосиск": dict(kcal=300, p=12.0, f=27.0, c=2.0),
    "sausage": dict(kcal=300, p=12.0, f=27.0, c=2.0),

    "куриц": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "курк": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "chicken": dict(kcal=190, p=29.0, f=7.0, c=0.0),

    "свинин": dict(kcal=260, p=26.0, f=18.0, c=0.0),
    "шашлык": dict(kcal=250, p=22.0, f=18.0, c=0.0),
    "мяс": dict(kcal=230, p=23.0, f=15.0, c=0.0),
}

PIECE_GRAMS: Dict[str, int] = {
    "яйц": 50, "egg": 50, "eggs": 50,
    "банан": 120, "banana": 120,
    "хлеб": 30, "хліб": 30, "bread": 30,
    "сыр": 30, "сир": 30, "cheese": 30,
    "сосиск": 50, "sausage": 50,
    "куриц": 80, "курк": 80, "chicken": 80,
}

CAL_KEYS = list(FALLBACK.keys())

# ---- RU -> EN мапа (минимальная, но спасает Ninjas) ----
_RU_TO_EN = {
    "молоко": "milk",
    "банан": "banana",
    "арахис": "peanuts",
    "гречка": "buckwheat",
    "рис": "rice",
    "курица": "chicken",
    "яйцо": "egg",
    "яйца": "eggs",
    "хлеб": "bread",
    "сыр": "cheese",
    "творог": "cottage cheese",
    "йогурт": "yogurt",
    "овсянка": "oatmeal",
}

# ---------- простейший TTL-кэш ----------
# ключ: prepared_query -> (expires_at_epoch, result_dict)
_CACHE: Dict[str, Tuple[float, Dict[str, float]]] = {}
CACHE_TTL_SEC = 6 * 60 * 60  # 6 часов


class NutritionError(Exception):
    pass


def _get_api_key() -> Optional[str]:
    return os.getenv(API_ENV_PRIMARY) or os.getenv(API_ENV_FALLBACK)


def _has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", s))


def _apply_ru_food_map(s: str) -> str:
    out = s
    for ru, en in sorted(_RU_TO_EN.items(), key=lambda x: -len(x[0])):
        out = re.sub(rf"(?i)\b{re.escape(ru)}\b", en, out)
    return out


def _normalize_units(s: str) -> str:
    out = s
    out = re.sub(r"(\d)\s*(мл)\b", r"\1 ml", out, flags=re.IGNORECASE)
    out = re.sub(r"(\d)\s*(г|гр)\b", r"\1 g", out, flags=re.IGNORECASE)
    return out


def _insert_space_between_number_and_word(s: str) -> str:
    return re.sub(r"(\d)([A-Za-zА-Яа-яЁё])", r"\1 \2", s)


def _cleanup_separators(s: str) -> str:
    out = s.replace("\n", " ").replace(";", ",")
    out = re.sub(r"\s*,\s*", ", ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def prepare_query(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    s = _insert_space_between_number_and_word(s)
    s = _normalize_units(s)
    s = _cleanup_separators(s)
    if _has_cyrillic(s):
        s = _apply_ru_food_map(s)
    return _cleanup_separators(s)


def _sum_totals(items: List[Dict[str, Any]]) -> Dict[str, float]:
    kcal = sum(float(i.get("calories", 0) or 0) for i in items)
    p = sum(float(i.get("protein_g", 0) or 0) for i in items)
    f = sum(float(i.get("fat_total_g", 0) or 0) for i in items)
    c = sum(float(i.get("carbohydrates_total_g", 0) or 0) for i in items)
    return {"kcal": round(kcal), "p": round(p, 1), "f": round(f, 1), "c": round(c, 1)}


def _all_zero(res: Dict[str, float]) -> bool:
    return (res.get("kcal", 0) or 0) == 0 and (res.get("p", 0) or 0) == 0 and (res.get("f", 0) or 0) == 0 and (res.get("c", 0) or 0) == 0


async def _call_api(query: str, key: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(API_URL, params={"query": query}, headers={"X-Api-Key": key})

    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise NutritionError("Unexpected response format")
    return data


async def _fetch_from_api(raw: str) -> Dict[str, float]:
    key = _get_api_key()
    if not key:
        raise NutritionError("Nutrition API key missing")

    prepared = prepare_query(raw)
    candidates = [raw]
    if prepared and prepared != raw:
        candidates.append(prepared)

    last_err: Optional[Exception] = None

    for q in candidates:
        # ретраи на 429/5xx
        for attempt in range(4):
            try:
                items = await _call_api(q, key)
                if not items:
                    raise NutritionError("Empty nutrition response")
                res = _sum_totals(items)
                # если “всё нули” — считаем это фейлом и пробуем другой формат/фоллбек
                if _all_zero(res):
                    raise NutritionError("Nutrition response is all zeros")
                return res
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response else 0
                last_err = e
                if status in (429, 500, 502, 503, 504):
                    await __import__("asyncio").sleep(0.6 * (2 ** attempt))
                    continue
                break
            except Exception as e:
                last_err = e
                break

    raise NutritionError(str(last_err) if last_err else "Nutrition API failed")


def _fallback_calc(text: str) -> Dict[str, float]:
    low = (text or "").lower()
    grams_info: list[tuple[float, Dict[str, float]]] = []

    num = r"(\d+(?:[.,]\d+)?)"
    unit_re = r"(г|g|гр|ml|мл)"

    for name, meta in FALLBACK.items():
        safe_name = re.escape(name)
        pattern = rf"{num}\s*{unit_re}?\s*{safe_name}"

        for m in re.finditer(pattern, low):
            qty_raw = m.group(1).replace(",", ".")
            try:
                qty = float(qty_raw)
            except ValueError:
                continue

            unit = (m.group(2) or "").lower()
            if unit in ("г", "g", "гр", "ml", "мл"):
                g = qty
            else:
                piece_g = PIECE_GRAMS.get(name)
                g = qty * piece_g if piece_g else qty

            grams_info.append((float(g), meta))

        if name in PIECE_GRAMS and name in low and not re.search(pattern, low):
            grams_info.append((float(PIECE_GRAMS[name]), meta))

    kcal = p = f = c = 0.0
    for g, meta in grams_info:
        factor = g / 100.0
        kcal += meta["kcal"] * factor
        p += meta["p"] * factor
        f += meta["f"] * factor
        c += meta["c"] * factor

    return {"kcal": round(kcal), "p": round(p, 1), "f": round(f, 1), "c": round(c, 1)}


async def analyze_nutrition(text: str) -> Dict[str, float]:
    """
    Канон:
    1) TTL cache
    2) API (с нормализацией + ретраями)
    3) FALLBACK калькулятор
    """
    raw = (text or "").strip()
    if not raw:
        return {"kcal": 0, "p": 0.0, "f": 0.0, "c": 0.0}

    cache_key = prepare_query(raw).lower() or raw.lower()
    now = _time.time()
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        res = await _fetch_from_api(raw)
    except Exception:
        res = _fallback_calc(raw)

    _CACHE[cache_key] = (now + CACHE_TTL_SEC, res)
    return res
