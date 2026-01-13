from fastapi import FastAPI
from app.webhooks.coinbase import router as coinbase_router

app = FastAPI()
app.include_router(coinbase_router)

@app.get('/healthz')
async def healthz():
    return {'ok': True}
import os

@app.get("/_version")
def _version():
    return {
        "file": __file__,
        "app": getattr(globals().get("app", None), "title", None) or "unknown",
        "commit": os.getenv("GIT_SHA") or os.getenv("APP_COMMIT") or "unknown"
    }
