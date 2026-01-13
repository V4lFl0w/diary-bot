# app/i18n.py
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, Any, Iterable
from functools import lru_cache
from app.config import settings

_BASE = Path(__file__).resolve().parent.parent / "i18n"

# если в .env DEFAULT_LOCALE=ru/uk/en — подтянем
_DEFAULT = (settings.default_locale or "ru").split("-")[0].lower()

# простая нормализация входящих кодов языка
_NORMALIZE = {
    "ua": "uk",
    "ru-ru": "ru",
    "uk-ua": "uk",
    "en-us": "en",
    "en-gb": "en",
}

class _SafeDict(dict):
    def __missing__(self, key):
        # не падаем на {missing_key}, а выводим как есть
        return "{" + key + "}"

def _norm_locale(locale: str | None) -> str:
    if not locale:
        return _DEFAULT
    l = locale.lower()
    return _NORMALIZE.get(l, l.split("-")[0])

def _dig(d: Dict[str, Any], path: Iterable[str]) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur

@lru_cache(maxsize=16)
def _load(locale: str) -> dict:
    p = _BASE / f"{locale}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text("utf-8"))

def available_locales() -> set[str]:
    return {f.stem for f in _BASE.glob("*.json")}

def t(key: str, locale: str | None = None, **kwargs) -> str:
    """
    t("menu.start", locale="uk", name="Валентин")
    • dot-keys поддерживаются: section.sub.key
    • безопасное format: пропущенные плейсхолдеры не роняют код
    • фолбэк: locale -> DEFAULT_LOCALE -> 'en' -> ключ
    """
    loc = _norm_locale(locale)
    fallbacks = [loc, _DEFAULT, "en"]
    path = key.split(".")

    text: str | None = None
    for lc in fallbacks:
        data = _load(lc)
        val = _dig(data, path)
        if isinstance(val, str):
            text = val
            break

    if text is None:
        # ничего не нашли — возвращаем сам ключ (удобно для отладки)
        text = key

    return text.format_map(_SafeDict(**kwargs))

def reload_locales() -> None:
    """Очистить кэш (например, в DEV после правки i18n/*.json)."""
    _load.cache_clear()