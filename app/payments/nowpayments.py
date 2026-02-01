import hashlib
import hmac
import os

import requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/create")
def create_invoice(tg_id: str = Query(...)):
    api = os.getenv("NOWP_API_KEY")
    public = os.getenv("PUBLIC_BASE_URL")
    price = float(os.getenv("NOWP_PRICE_USD", "5.00"))
    payload = {
        "price_amount": price,
        "price_currency": "USD",
        "order_id": f"tg_{tg_id}",
        "success_url": f"{public}/payments/success",
        "cancel_url": f"{public}/payments/cancel",
        "ipn_callback_url": f"{public}/payments/now/webhook",
    }
    r = requests.post(
        "https://api.nowpayments.io/v1/invoice",
        headers={"x-api-key": api, "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    return RedirectResponse(r.json()["invoice_url"], status_code=303)


@router.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    sign = request.headers.get("x-nowpayments-sig", "")
    check = hmac.new(os.getenv("NOWP_IPN_SECRET", "").encode(), body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(check, sign):
        return {"ok": False}
    data = await request.json()
    status = data.get("payment_status") or data.get("invoice_status")
    order_id = data.get("order_id", "")
    tg_id = order_id.replace("tg_", "")
    print("NOWP EVENT:", status, tg_id)
    if status in ("finished", "confirmed", "paid", "partially_paid"):
        # TODO: выдать премиум tg_id на 30 дней
        pass
    return {"ok": True}
