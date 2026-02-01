from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from typing import Any

from fastapi import APIRouter

try:
    from fastapi import FastAPI
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore

try:
    from starlette.applications import Starlette
except Exception:  # pragma: no cover
    Starlette = None  # type: ignore

from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse


def _register(app: Any) -> None:
    try:
        r = APIRouter()

        @r.get("/pay", response_class=HTMLResponse)
        async def pay(tg_id: str | None = None):
            p = os.path.join(os.path.dirname(__file__), "templates", "pay.html")
            with open(p, "r", encoding="utf-8") as f:
                return HTMLResponse(f.read())

        @r.get("/pay-crypto")
        async def pay_crypto(tg_id: str | None = None):
            if not tg_id:
                return PlainTextResponse("tg_id required", status_code=400)
            return RedirectResponse(
                url=f"/payments/now/create?tg_id={tg_id}", status_code=303
            )

        routes = getattr(app, "routes", None)
        if routes is None:
            router = getattr(app, "router", None)
            routes = getattr(router, "routes", []) if router is not None else []

        existing = {getattr(rt, "path", "") for rt in (routes or [])}
        need = {"/pay"}
        if not need.issubset(existing):
            app.include_router(r)
    except Exception:
        pass


def _is_asgi_app(obj: Any) -> bool:
    if FastAPI is not None and isinstance(obj, FastAPI):
        return True
    if Starlette is not None and isinstance(obj, Starlette):
        return True
    return False


def _scan_and_patch() -> None:
    try:
        import app as _app_pkg
    except Exception:
        return

    for m in pkgutil.walk_packages(_app_pkg.__path__, "app."):
        try:
            mod = importlib.import_module(m.name)
        except Exception:
            continue

        for _, obj in inspect.getmembers(mod):
            if _is_asgi_app(obj):
                _register(obj)


def init_hooks() -> None:
    """
    Run hooks explicitly (do NOT run on module import).
    Safe to call multiple times.
    """
    _scan_and_patch()
