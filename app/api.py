import os

from fastapi import FastAPI

app = FastAPI(title="DiaryBot API DEPLOY_7575638")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/_version")
def _version():
    return {
        "file": __file__,
        "app": getattr(globals().get("app", None), "title", None) or "unknown",
        "commit": os.getenv("GIT_SHA") or os.getenv("APP_COMMIT") or "unknown",
    }
