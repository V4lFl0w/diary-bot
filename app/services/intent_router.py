# app/services/intent_router.py
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Intent(str, Enum):
    MEDIA_IMAGE = "media_image"
    MEDIA_TEXT = "media_text"
    WEATHER = "weather"
    SHOP = "shop"
    GENERAL = "general"


_MEDIA_KW = re.compile(
    r"\b("
    r"что за фильм|как называется фильм|название фильма|откуда кадр|кадр|постер|трейлер|"
    r"акт[её]р|актрис|режисс[её]р|год[ау]?\b|"
    r"сюжет|описание фильма|в главной роли|сцена|эпизод"
    r")\b",
    re.IGNORECASE,
)

_WEATHER_KW = re.compile(
    r"\b(погод|прогноз|температур|осадк|ветер|дожд|снег|гисметео|meteofor)\b",
    re.IGNORECASE,
)
_WEATHER_TIME = re.compile(
    r"\b(сегодня|завтра|послезавтра|на\s*недел)\b", re.IGNORECASE
)

_SHOP_KW = re.compile(
    r"\b(шмот|одежд|кросс|кед|куртк|джинс|футболк|худ|свитшот|размер|бренд|лук|стил)\b",
    re.IGNORECASE,
)

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    reason: str


def detect_intent(text: Optional[str], *, has_media: bool) -> IntentResult:
    if has_media:
        return IntentResult(Intent.MEDIA_IMAGE, 1.0, "has_media")

    t = (text or "").strip()
    if not t:
        return IntentResult(Intent.GENERAL, 0.5, "empty_text")

    # weather
    if _WEATHER_KW.search(t) and (
        _WEATHER_TIME.search(t) or "во " in t.lower() or "в " in t.lower()
    ):
        return IntentResult(Intent.WEATHER, 0.9, "weather_keywords")

    # shop
    if _SHOP_KW.search(t):
        return IntentResult(Intent.SHOP, 0.85, "shop_keywords")

    # media text
    if _MEDIA_KW.search(t) or _YEAR.search(t):
        return IntentResult(Intent.MEDIA_TEXT, 0.8, "media_keywords_or_year")

    return IntentResult(Intent.GENERAL, 0.7, "fallback")
