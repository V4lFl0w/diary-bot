from __future__ import annotations

import os
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from app.utils.app_version import get_app_version


def public_base_url() -> str:
    """Public base URL for your app, like https://your-domain.com"""
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL") or os.getenv("WEBAPP_BASE_URL") or "").rstrip("/")


def _merge_query(url: str, add: dict[str, str]) -> str:
    sp = urlsplit(url)
    q = dict(parse_qsl(sp.query, keep_blank_values=True))
    q.update({k: v for k, v in add.items() if v is not None})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q, doseq=True), sp.fragment))


def versioned_url(path_or_url: str, *, extra: dict[str, str] | None = None) -> str:
    """Make URL cache-bust stable: always add v=<APP_VERSION>."""
    base = public_base_url()
    v = get_app_version()

    u = path_or_url.strip()
    if u.startswith("http://") or u.startswith("https://"):
        url = u
    else:
        if not base:
            # If no base set, return as-is (but still version it if it already looks like absolute).
            # In production you SHOULD set PUBLIC_BASE_URL.
            url = u
        else:
            url = f"{base}{u if u.startswith('/') else '/' + u}"

    params = {"v": v}
    if extra:
        params.update(extra)

    return _merge_query(url, params)


def webapp_static(path: str, *, extra: dict[str, str] | None = None) -> str:
    """Static webapp pages served by our FastAPI under /static/mini/..."""
    p = path if path.startswith("/") else "/" + path
    return versioned_url(p, extra=extra)


def webapp_page(path: str, *, extra: dict[str, str] | None = None) -> str:
    """Webapp pages served by our FastAPI under /webapp/..."""
    p = path if path.startswith("/") else "/" + path
    return versioned_url(p, extra=extra)


# Canonical entrypoints (use these everywhere)
WEBAPP_PREMIUM_ENTRY = webapp_static("/static/mini/premium/premium.html")
WEBAPP_MEDITATION_ENTRY = webapp_static("/static/mini/meditation/index.html")
