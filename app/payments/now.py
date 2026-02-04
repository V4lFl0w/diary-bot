import hashlib
import hmac
import os


def _now_desc() -> str:
    # Shown to user in NOWPayments invoice
    return "Premium: Бонус-токены на тяжёлые функции"


# VF_NOW_PRICE_V1
# Plan prices are defined in UAH (UI currency), converted to USD for NOWPayments.
PLAN_PRICES_UAH = {
    "basic": 199,
    "pro": 499,
    "max": 999,
}
# UI shows ~USD, we use a fixed approximate FX for invoice amount.
FX_USD = 40.0  # ~ UAH per $ (approx)


def _plan_price_usd(plan: str) -> float:
    p = (plan or "").strip().lower()
    uah = float(PLAN_PRICES_UAH.get(p, 0) or 0)
    if uah <= 0:
        return 10.0
    usd = uah / FX_USD
    # round to 2 decimals, min 1.00
    usd = max(1.0, round(usd, 2))
    return usd


import requests
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


def _base():
    return os.getenv("NOWP_BASE", "https://api.nowpayments.io/v1").rstrip("/")


def _pub():
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def _price():
    return os.getenv("NOWP_PRICE_USD", "1.00")


@router.get("/payments/now/create")
def now_create_invoice(tg_id: str, plan: str = "basic", period: str = "month"):
    api_key = os.getenv("NOWP_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="NOWP_API_KEY missing")
    payload = {
        "price_amount": _plan_price_usd(plan),
        "order_description": _now_desc(),
        "price_currency": "USD",
        "order_id": f"{tg_id}:{plan}:{period}",  # VF_NOW_ORDER_META_V1
        "ipn_callback_url": f"{_pub()}/payments/now/webhook",
        "success_url": f"{_pub()}/payments/now/thanks",
        "cancel_url": f"{_pub()}/payments/now/cancel",
    }
    r = requests.post(
        f"{_base()}/invoice",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    try:
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NOWP create failed: {e}")
    data = r.json()
    url = data.get("invoice_url")
    if not url:
        raise HTTPException(status_code=500, detail="invoice_url not found")
    return RedirectResponse(url, status_code=303)


@router.get("/payments/now/thanks")
def now_thanks():
    return {"ok": True, "status": "thanks"}


@router.get("/payments/now/cancel")
def now_cancel():
    return {"ok": True, "status": "cancel"}


@router.post("/payments/now/webhook")
async def now_webhook(request: Request, x_nowpayments_sig: str = Header(None)):
    raw = await request.body()
    secret = os.getenv("NOWP_IPN_SECRET", "")
    expect = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    if not x_nowpayments_sig or x_nowpayments_sig.lower() != expect.lower():
        raise HTTPException(status_code=400, detail="bad signature")

    body = await request.json()
    status = (body.get("payment_status") or "").lower()
    order_id = str(body.get("order_id") or "").strip()
    tg_id = (order_id.split(":", 1)[0] or "").strip()
    str(body.get("invoice_id") or body.get("payment_id") or "")

    # TODO: integrate DB idempotency by invoice_id

    if status in {"finished", "confirmed"} and tg_id.isdigit():
        try:
            # TODO: set_premium_until(int(tg_id), datetime.utcnow() + timedelta(days=30))
            pass
        except Exception as e:
            print("premium grant failed:", e)

    return {"ok": True}
