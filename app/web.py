from fastapi import FastAPI
from app.http import router as http_router

app = FastAPI()

# базовые http роуты
app.include_router(http_router)

# 🔥 ВАЖНО: подключаем hooks, чтобы /pay реально добавился
import app.hooks as _hooks  # noqa: F401