# app/web.py
from fastapi import FastAPI
from app.payments.stars_webapp import router as stars_router
from app.payments.mono_webapp import router as mono_router
from fastapi.staticfiles import StaticFiles

from app.http import router as http_router
from app.payments.now import router as now_router
from app.hooks import init_hooks

app = FastAPI()


# ✅ Telegram Stars invoices for WebApp
app.include_router(stars_router)
app.include_router(mono_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# базовые http роуты
app.include_router(http_router)
app.include_router(now_router)

# 🔥 подключаем hooks, чтобы /pay реально добавился
init_hooks()