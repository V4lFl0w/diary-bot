
import os, pkgutil, importlib, inspect
try:
    from fastapi import FastAPI, APIRouter
    from fastapi.responses import HTMLResponse, PlainTextResponse
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
        @r.get("/pay-mono")
        async def pay_mono(tg_id: str | None = None):
            return PlainTextResponse("MonoPay stub OK", status_code=200)
        @r.get("/pay-crypto")
        async def pay_crypto(tg_id: str | None = None):
            return PlainTextResponse("Crypto stub OK", status_code=200)
        existing = {getattr(rt, "path", "") for rt in getattr(app, "routes", []) or getattr(app.router, "routes", [])}
        need = {"/pay","/pay-mono","/pay-crypto"}
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
