from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

import httpx


API_URL = "https://api.api-ninjas.com/v1/nutrition"
API_ENV_PRIMARY = "NINJAS_API_KEY"
API_ENV_FALLBACK = "NUTRITION_API_KEY"


class NutritionError(Exception):
    pass


def _get_api_key() -> str:
    key = os.getenv(API_ENV_PRIMARY) or os.getenv(API_ENV_FALLBACK)
    if not key:
        raise NutritionError("Nutrition API key is not configured (NINJAS_API_KEY)")
    return key


_RU_TO_EN = {
    "молоко": "milk",
    "банан": "banana",
    "арахис": "peanuts",
    "арахіс": "peanuts",
    "гречка": "buckwheat",
    "рис": "rice",
    "курица": "chicken",
    "курка": "chicken",
    "яйцо": "egg",
    "яйца": "eggs",
    "хлеб": "bread",
    "хліб": "bread",
    "сыр": "cheese",
    "сир": "cheese",
    "творог": "cottage cheese",
    "йогурт": "yogurt",
    "овсянка": "oatmeal",
    "сырник": "cottage cheese pancake",
    "сырники": "cottage cheese pancakes",
}


def _has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁёІіЇїЄє]", s))


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
    return re.sub(r"(\d)([A-Za-zА-Яа-яЁёІіЇїЄє])", r"\1 \2", s)


def _cleanup_separators(s: str) -> str:
    out = s.replace("\n", " ").replace(";", ",")
    out = re.sub(r"\s*,\s*", ", ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _prepare_query(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    s = _insert_space_between_number_and_word(s)
    s = _normalize_units(s)
    s = _cleanup_separators(s)
    if _has_cyrillic(s):
        s = _apply_ru_food_map(s)
    s = _cleanup_separators(s)
    return s


async def _call_api(query: str, api_key: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            API_URL,
            params={"query": query},
            headers={"X-Api-Key": api_key},
        )
        resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        raise NutritionError("Unexpected response format from nutrition API")
    return data


def _sum_totals(data: List[Dict[str, Any]]) -> Dict[str, float]:
    total = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbohydrates": 0.0}
    for item in data:
        total["calories"] += float(item.get("calories", 0) or 0)
        total["protein"] += float(item.get("protein_g", 0) or 0)
        total["fat"] += float(item.get("fat_total_g", 0) or 0)
        total["carbohydrates"] += float(item.get("carbohydrates_total_g", 0) or 0)
    return total


async def fetch_nutrition(query: str) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    api_key = _get_api_key()
    raw_query = (query or "").strip()
    if not raw_query:
        raise NutritionError("Empty nutrition query")

    prepared = _prepare_query(raw_query)

    # 1) как есть
    try:
        data = await _call_api(raw_query, api_key)
        if data:
            return _sum_totals(data), data
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else None
        # 2) если клиентская ошибка и есть нормализованный вариант — пробуем ещё раз
        if prepared and prepared != raw_query and status and 400 <= status < 500:
            data2 = await _call_api(prepared, api_key)
            if data2:
                return _sum_totals(data2), data2
        raise NutritionError(f"HTTP error from nutrition API: {status}") from e
    except httpx.HTTPError as e:
        raise NutritionError("Network error while calling nutrition API") from e

    # если получили пусто
    if prepared and prepared != raw_query:
        data2 = await _call_api(prepared, api_key)
        if data2:
            return _sum_totals(data2), data2

    raise NutritionError("No nutrition data found")
