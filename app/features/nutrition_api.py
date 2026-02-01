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
        raise NutritionError("Nutrition API key is not configured")
    return key


# ---- минимальная нормализация RU -> EN для самых частых продуктов ----
# (не претендуем на идеальность, цель — уменьшить 400/пустые ответы)
_RU_TO_EN = {
    # базовые
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
    # спорные, но полезные
    "сырник": "cottage cheese pancake",
    "сырники": "cottage cheese pancakes",
}


def _has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", s))


def _apply_ru_food_map(s: str) -> str:
    # заменяем по словам/основам, аккуратно
    out = s

    # 1) сначала точные формы (сырники/яйца и т.п.)
    for ru, en in sorted(_RU_TO_EN.items(), key=lambda x: -len(x[0])):
        # простые границы слов для кириллицы
        out = re.sub(rf"(?i)\b{re.escape(ru)}\b", en, out)

    return out


def _normalize_units(s: str) -> str:
    """
    Приводим русские единицы к формату, который API обычно понимает.
    - 250 мл -> 250 ml
    - 40 г / 40 гр -> 40 g
    """
    out = s

    # "250мл", "250 мл" -> "250 ml"
    out = re.sub(r"(\d)\s*(мл)\b", r"\1 ml", out, flags=re.IGNORECASE)

    # "40г", "40 г", "40гр", "40 гр" -> "40 g"
    out = re.sub(r"(\d)\s*(г|гр)\b", r"\1 g", out, flags=re.IGNORECASE)

    return out


def _insert_space_between_number_and_word(s: str) -> str:
    """
    "2сырника" -> "2 сырника"
    "100gmilk" (редко) -> "100 gmilk" (потом unit нормализатор решит)
    """
    return re.sub(r"(\d)([A-Za-zА-Яа-яЁё])", r"\1 \2", s)


def _cleanup_separators(s: str) -> str:
    out = s.replace("\n", " ").replace(";", ",")
    out = re.sub(r"\s*,\s*", ", ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _prepare_query(raw: str) -> str:
    """
    Универсальная подготовка текста запроса.
    Делает строку более "API-friendly".
    """
    s = (raw or "").strip()
    if not s:
        return s

    s = _insert_space_between_number_and_word(s)
    s = _normalize_units(s)
    s = _cleanup_separators(s)

    # если есть кириллица — попробуем мягко заменить на англ продукты
    if _has_cyrillic(s):
        s = _apply_ru_food_map(s)

    # финальная чистка
    s = _cleanup_separators(s)
    return s


async def _call_api(query: str, api_key: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            API_URL,
            params={"query": query},
            headers={"X-Api-Key": api_key},
        )

    # явная обработка статуса
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        # пробрасываем наверх — выше решим, ретраить ли
        raise e

    try:
        data = resp.json()
    except ValueError as e:
        raise NutritionError("Invalid JSON from nutrition API") from e

    if not isinstance(data, list):
        raise NutritionError("Unexpected response format from nutrition API")

    return data


def _sum_totals(data: List[Dict[str, Any]]) -> Dict[str, float]:
    total = {
        "calories": 0.0,
        "protein": 0.0,
        "fat": 0.0,
        "carbohydrates": 0.0,
    }

    for item in data:
        total["calories"] += float(item.get("calories", 0) or 0)
        total["protein"] += float(item.get("protein_g", 0) or 0)
        total["fat"] += float(item.get("fat_total_g", 0) or 0)
        total["carbohydrates"] += float(item.get("carbohydrates_total_g", 0) or 0)

    return total


async def fetch_nutrition(query: str) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """
    Возвращает:
    - total: calories/protein/fat/carbohydrates
    - raw items

    Логика:
    1) пробуем как есть
    2) если 400/422/прочее клиентское и есть шанс, что дело в формате —
       пробуем подготовленный запрос
    """
    api_key = _get_api_key()

    raw_query = (query or "").strip()
    if not raw_query:
        raise NutritionError("Empty nutrition query")

    prepared = _prepare_query(raw_query)

    # 1) Первый вызов
    try:
        data = await _call_api(raw_query, api_key)
        if not data:
            raise NutritionError("No nutrition data found")
        return _sum_totals(data), data

    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else None

        # если prepared отличается и это клиентская ошибка — пробуем ещё раз
        if prepared and prepared != raw_query and status and 400 <= status < 500:
            try:
                data2 = await _call_api(prepared, api_key)
                if not data2:
                    raise NutritionError("No nutrition data found")
                return _sum_totals(data2), data2
            except httpx.HTTPError as e2:
                raise NutritionError(
                    f"HTTP error while calling nutrition API: {status}. "
                    f"Tried normalized query too."
                ) from e2

        raise NutritionError(f"HTTP error while calling nutrition API: {e}") from e

    except httpx.HTTPError as e:
        raise NutritionError(f"HTTP error while calling nutrition API: {e}") from e

    except NutritionError:
        raise

    except Exception as e:
        raise NutritionError("Unexpected error while calling nutrition API") from e
