import os
import json
import sqlite3
import base64
import requests
from datetime import datetime, date
from fastapi import FastAPI, Request, Header, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse, JSONResponse
from dotenv import load_dotenv
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature

load_dotenv(".env")
MONO_TOKEN = os.getenv("MONO_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://your.domain")
SUB_PRICE_UAH = int(float(os.getenv("SUB_PRICE_UAH", "99")) * 100)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin")
MONO_BASE = "https://api.monobank.ua"

con = sqlite3.connect("data/payments.sqlite3", check_same_thread=False)
con.execute(
    "create table if not exists invoices(invoice_id text primary key, tg_id text, amount int, ccy int, reference text, status text, created_at text, paid_at text, refunded int default 0, refund_amount int default 0)"
)
con.execute(
    "create table if not exists events(id integer primary key autoincrement, invoice_id text, kind text, payload text, created_at text)"
)
con.commit()

app = FastAPI()
_pubkey = None


def pubkey():
    global _pubkey
    if _pubkey is None:
        r = requests.get(f"{MONO_BASE}/api/merchant/pubkey", timeout=15)
        r.raise_for_status()
        _pubkey = serialization.load_pem_public_key(r.json()["key"].encode())
    return _pubkey


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/pay")
def pay(tg_id: str = Query(...)):
    return RedirectResponse(f"/pay/mono?tg_id={tg_id}", status_code=303)


@app.get("/pay/mono")
def pay_mono(tg_id: str = Query(...)):
    if not MONO_TOKEN:
        raise HTTPException(500, "MONO_TOKEN missing")
    amount = SUB_PRICE_UAH
    reference = f"sub_{tg_id}_{date.today():%Y-%m}"
    payload = {
        "amount": amount,
        "ccy": 980,
        "merchantPaymInfo": {"reference": reference, "destination": "FlowDiary Premium"},
        "redirectUrl": f"{PUBLIC_BASE_URL}/payments/mono/return",
        "successUrl": f"{PUBLIC_BASE_URL}/payments/success",
        "failUrl": f"{PUBLIC_BASE_URL}/payments/fail",
        "webHookUrl": f"{PUBLIC_BASE_URL}/api/mono/webhook",
        "validity": 3600,
        "paymentType": "debit",
    }
    r = requests.post(
        f"{MONO_BASE}/api/merchant/invoice/create",
        headers={"X-Token": MONO_TOKEN, "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    data = r.json()
    if r.status_code != 200 or "pageUrl" not in data:
        raise HTTPException(502, detail=data)
    con.execute(
        "insert or replace into invoices(invoice_id,tg_id,amount,ccy,reference,status,created_at) values(?,?,?,?,?,?,?)",
        (data["invoiceId"], tg_id, amount, 980, reference, "created", datetime.utcnow().isoformat()),
    )
    con.commit()
    return RedirectResponse(data["pageUrl"], status_code=303)


@app.post("/api/mono/webhook")
async def mono_webhook(request: Request, x_sign: str | None = Header(None)):
    raw = await request.body()
    if not x_sign:
        raise HTTPException(400, "X-Sign missing")
    try:
        pubkey().verify(base64.b64decode(x_sign), raw, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise HTTPException(403, "Bad signature")
    event = json.loads(raw.decode())
    invoice_id = event.get("invoiceId")
    status = event.get("status")
    con.execute(
        "insert into events(invoice_id,kind,payload,created_at) values(?,?,?,?)",
        (invoice_id, "webhook", json.dumps(event, ensure_ascii=False), datetime.utcnow().isoformat()),
    )
    if invoice_id and status:
        con.execute("update invoices set status=? where invoice_id=?", (status, invoice_id))
        if status == "success":
            con.execute("update invoices set paid_at=? where invoice_id=?", (datetime.utcnow().isoformat(), invoice_id))
        if status in ("reversed", "refunded"):
            con.execute("update invoices set refunded=1 where invoice_id=?", (invoice_id,))
    con.commit()
    return PlainTextResponse("ok")


@app.get("/payments/mono/return")
def mono_return():
    return HTMLResponse("<h3>Спасибо! Проверяем платёж…</h3>")


@app.get("/payments/success")
def success():
    return HTMLResponse("<h3>Оплата принята. Можно закрыть вкладку.</h3>")


@app.get("/payments/fail")
def fail():
    return HTMLResponse("<h3>Оплата не прошла или отменена.</h3>")


@app.post("/admin/mono/refund")
async def admin_refund(request: Request, x_admin_token: str | None = Header(None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "forbidden")
    body = await request.json()
    invoice_id = body.get("invoiceId")
    amount = body.get("amount")
    if not invoice_id:
        raise HTTPException(400, "invoiceId required")
    payload = {"invoiceId": invoice_id}
    if amount is not None:
        payload["amount"] = int(amount)
    r = requests.post(
        f"{MONO_BASE}/api/merchant/return",
        headers={"X-Token": MONO_TOKEN, "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if r.status_code != 200:
        raise HTTPException(502, detail=r.json() if r.content else {})
    con.execute(
        "update invoices set refunded=1, refund_amount=? where invoice_id=?", (payload.get("amount", 0), invoice_id)
    )
    con.commit()
    return JSONResponse({"ok": True, "data": r.json()})


@app.get("/admin/mono/status")
def admin_status(invoiceId: str, x_admin_token: str | None = Header(None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "forbidden")
    r = requests.get(
        f"{MONO_BASE}/api/merchant/invoice/status",
        params={"invoiceId": invoiceId},
        headers={"X-Token": MONO_TOKEN},
        timeout=15,
    )
    return JSONResponse(r.json())
