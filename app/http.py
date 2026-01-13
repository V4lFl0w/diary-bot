
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from pathlib import Path
import os, httpx

app = FastAPI(title="Diary HTTP")

def _load_env_chain():
    candidates = [
        "scripts/dev.env", "dev.env", ".env", ".env.local",
        "config/dev.env", "config/.env", "env", "local.env"
    ]
    for rel in candidates:
        p = Path(rel)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

_load_env_chain()

def _base():
    return os.getenv("PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or ""

@app.get("/env-check")
async def env_check():
    keys = ["PUBLIC_URL","PUBLIC_BASE_URL","SUB_PRICE_UAH","SUB_PRICE_USD","NOWP_API_KEY","MONO_TOKEN"]
    return {k: bool(os.getenv(k)) for k in keys}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/pay", response_class=HTMLResponse)
async def pay(tg_id: str = Query(...)):
    html = f"""<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;margin:0}}
.wrap{{max-width:740px;margin:32px auto;padding:0 16px}}
h1{{font-size:28px;margin:0 0 24px}}
a.btn{{display:block;width:100%;padding:18px 20px;border:0;border-radius:16px;font-size:20px;color:#fff;text-decoration:none;text-align:center;margin:14px 0}}
a.card{{background:#5b39f0}} a.crypto{{background:#14a37f}}
</style>
<div class="wrap">
  <h1>Выберите способ оплаты</h1>
  <a class="btn card"   href="/pay-mono?tg_id={tg_id}">Картой (MonoPay, Apple/Google Pay)</a>
  <a class="btn crypto" href="/pay-crypto?tg_id={tg_id}">Криптой (USDT TRC20)</a>
</div>"""
    return HTMLResponse(html)

@app.get("/pay-crypto")
async def pay_crypto(tg_id: str = Query(...)):
    api_key = os.getenv("NOWP_API_KEY","")
    amount_usd = float(os.getenv("SUB_PRICE_USD", os.getenv("SUB_PRICE_USDT","10")))
    success = _base()+"/payments/success"
    cancel  = _base()+"/payments/cancel"
    if not api_key:
        return PlainTextResponse("NOWPayments API key missing", status_code=501)
    payload = {
        "price_amount": round(amount_usd, 2),
        "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "order_id": f"tg_{tg_id}",
        "success_url": success,
        "cancel_url": cancel
    }
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post("https://api.nowpayments.io/v1/invoice",
                           json=payload, headers={"x-api-key": api_key})
    data = r.json()
    if r.status_code != 200 or "invoice_url" not in data:
        raise HTTPException(502, detail=data)
    return RedirectResponse(data["invoice_url"], status_code=303)

@app.get("/pay-mono")
async def pay_mono(tg_id: str = Query(...)):
    token = os.getenv("MONO_TOKEN","")
    amount_uah = float(os.getenv("SUB_PRICE_UAH","99"))
    amount = int(round(amount_uah*100))
    success = _base()+"/payments/success"
    webhook = _base()+"/payments/mono-callback"
    if not token:
        return PlainTextResponse("Monobank merchant token missing", status_code=501)
    payload = {
        "amount": amount,
        "ccy": 980,
        "merchantPaymInfo": {"reference": f"tg_{tg_id}", "destination": "Diary Assistant Premium"},
        "redirectUrl": success,
        "webHookUrl": webhook
    }
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post("https://api.monobank.ua/api/merchant/invoice/create",
                           headers={"X-Token": token, "Content-Type":"application/json"},
                           json=payload)
    data = r.json()
    if r.status_code != 200 or "pageUrl" not in data:
        raise HTTPException(502, detail=data)
    return RedirectResponse(data["pageUrl"], status_code=303)

@app.post("/payments/mono-callback")
async def mono_cb(body: dict):
    return {"ok": True}

@app.get("/payments/success", response_class=HTMLResponse)
async def ok():
    return "<h1>Оплачено ✅</h1>"

@app.get("/payments/cancel", response_class=HTMLResponse)
async def cancel():
    return "<h1>Платёж отменён</h1>"
