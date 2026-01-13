import asyncio
import logging
from fastapi import FastAPI

from app.api.coinbase import router as coinbase_router
from app.main import main as run_bot  # <-- берем твой main() как запуск бота
from app.http import router as http_router

app = FastAPI(title="DiaryBot API DEPLOY_7575638", version="1.0")
app.include_router(coinbase_router)
app.include_router(http_router)

_bot_task: asyncio.Task | None = None

@app.on_event("startup")
async def _startup() -> None:
    global _bot_task
    logging.info("🌐 API startup: launching bot polling in background…")
    _bot_task = asyncio.create_task(run_bot(), name="bot_polling")

@app.on_event("shutdown")
async def _shutdown() -> None:
    global _bot_task
    if _bot_task:
        _bot_task.cancel()

@app.get("/healthz")
def healthz():
    return {"ok": True}
import os

@app.get("/_version")
def _version():
    return {"app": "web", "commit": os.getenv("GIT_SHA") or os.getenv("APP_COMMIT") or "unknown"}
