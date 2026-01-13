from fastapi import FastAPI

# Импортируй свой роутер с Coinbase вебхуками.
# Файл ты кидал как модуль с APIRouter — считаю, что он лежит в app/api/coinbase.py
# Если у тебя другое имя/путь — просто поправь импорт ниже.
from app.api.coinbase import router as coinbase_router

app = FastAPI(title="DiaryBot API", version="1.0")
app.include_router(coinbase_router)

@app.get("/healthz")
def healthz():
    return {"ok": True}
