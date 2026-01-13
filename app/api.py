from fastapi import FastAPI
from app.webhooks.coinbase import router as coinbase_router

app = FastAPI()
app.include_router(coinbase_router)

@app.get('/healthz')
async def healthz():
    return {'ok': True}
