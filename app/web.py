from pathlib import Path

# app/web.py
from fastapi import FastAPI
from app.payments.stars_webapp import router as stars_router
from app.payments.mono_webapp import router as mono_router
from fastapi.staticfiles import StaticFiles
from app.webapp.music_api import router as music_api_router

from app.http import router as http_router
from app.payments.now import router as now_router
from app.hooks import init_hooks

app = FastAPI()
app.include_router(music_api_router)
# --- webapp static mount ---
BASE_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = BASE_DIR / "webapp"
if WEBAPP_DIR.exists():
    app.mount("/webapp", StaticFiles(directory=str(WEBAPP_DIR)), name="webapp")


# --- /webapp ---
def _no_cache_headers() -> dict:
    # HTML must not be cached, иначе часть юзеров увидит старую верстку/стили
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


# VF_NOSTORE_PREMIUM_HTML_V1
@app.middleware("http")
async def _vf_no_store_premium_html(request, call_next):
    resp = await call_next(request)
    try:
        path = str(getattr(getattr(request, "url", None), "path", "") or "")
    except Exception:
        path = ""
    if path.endswith("/static/mini/premium/premium.html"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# ✅ Telegram Stars invoices for WebApp
app.include_router(stars_router)
app.include_router(mono_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# базовые http роуты
app.include_router(http_router)
app.include_router(now_router)

# 🔥 подключаем hooks, чтобы /pay реально добавился
init_hooks()
