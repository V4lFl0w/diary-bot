# app/urls.py
import os
from urllib.parse import urlencode

from app.webapp.urls import WEBAPP_PREMIUM_ENTRY


def _public_base() -> str:
    public = (os.getenv("PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return public


def pay_url(tg_id: int, lang: str | None = None) -> str | None:
    public = _public_base()
    if not public.startswith("http"):
        return None

    params: dict[str, str | int] = {"tg_id": tg_id}
    if lang:
        params["lang"] = lang

    return f"{public}{WEBAPP_PREMIUM_ENTRY}?{urlencode(params)}"


# Backward-compatible alias
public_pay_url = pay_url
