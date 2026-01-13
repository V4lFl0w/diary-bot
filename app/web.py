from fastapi import FastAPI
from app.http import router as http_router

app = FastAPI()

app.include_router(http_router)

@app.get("/healthz")
def healthz():
    return {"ok": True}
