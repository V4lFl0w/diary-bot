# app/web.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.http import router as http_router
from app.payments.now import router as now_router
from app.hooks import init_hooks

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# базовые http роуты
app.include_router(http_router)
app.include_router(now_router)

# 🔥 подключаем hooks, чтобы /pay реально добавился
init_hooks()