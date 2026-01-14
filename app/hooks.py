
import os, pkgutil, importlib, inspect
try:
    from fastapi import FastAPI, APIRouter
    from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
    from starlette.applications import Starlette
except Exception:
    FastAPI = object
    Starlette = object

def _register(app):
    try:
        r = APIRouter()
        @r.get("/pay", response_class=HTMLResponse)
        async def pay(tg_id: str | None = None):
            p = os.path.join(os.path.dirname(__file__), "templates", "pay.html")
            return HTMLResponse(open(p, "r", encoding="utf-8").read())
        @r.get("/pay-crypto")
        async def pay_crypto(tg_id: str | None = None):
            if not tg_id:
                return PlainTextResponse("tg_id required", status_code=400)
            return RedirectResponse(url=f"/payments/now/create?tg_id={tg_id}", status_code=303)
        existing = {getattr(rt, "path", "") for rt in getattr(app, "routes", []) or getattr(app.router, "routes", [])}
        need = {"/pay"}
        if not need.issubset(existing):
            app.include_router(r)
    except Exception:
        pass

def _scan_and_patch():
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
            if isinstance(obj, (FastAPI, Starlette)):
                _register(obj)

_scan_and_patch()
