from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.payments.stars_webapp import router as stars_router
from app.payments.mono_webapp import router as mono_router
from app.webapp.music_api import router as music_api_router
from app.http import router as http_router
from app.payments.now import router as now_router
from app.hooks import init_hooks

app = FastAPI()

app.include_router(music_api_router)
app.include_router(stars_router)
app.include_router(mono_router)
app.include_router(http_router)
app.include_router(now_router)

BASE_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = BASE_DIR / "webapp"
if WEBAPP_DIR.exists():
    app.mount("/webapp", StaticFiles(directory=str(WEBAPP_DIR)), name="webapp")


def _no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


@app.middleware("http")
async def _webapp_no_cache(request: Request, call_next):
    resp: Response = await call_next(request)

    p = request.url.path

    # Любые /webapp/* — всегда NO-STORE (HTML, JS, CSS, API, картинки)
    if p.startswith("/webapp/"):
        for k, v in _no_cache_headers().items():
            resp.headers[k] = v

        # Убираем условное кеширование (304 Not Modified)
        # MutableHeaders не поддерживает .pop(), поэтому удаляем безопасно
        if 'etag' in resp.headers:
            del resp.headers['etag']
        if 'last-modified' in resp.headers:
            del resp.headers['last-modified']

    return resp


init_hooks()
