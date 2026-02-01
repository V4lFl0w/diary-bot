import hashlib
import hmac
import json
import os

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter()


def _base():
    return os.getenv("NOWP_BASE", "https://api.nowpayments.io/v1")


def _verify_sig(secret: str, body: bytes, sig: str) -> bool:
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return False
    data = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    mac = hmac.new(secret.encode(), data.encode(), hashlib.sha512).hexdigest()
    return hmac.compare_digest(mac, (sig or "").lower())


@router.get("/payments/now/create")
def now_create(tg_id: str):
    key = os.getenv("NOWP_API_KEY")
    price = os.getenv("NOWP_PRICE_USD", "5.00")
    public = os.getenv(
        "PUBLIC_BASE_URL", "https://ilda-comelier-pliantly.ngrok-free.dev"
    )
    payload = {
        "price_amount": str(price),
        "price_currency": "USD",
        "order_id": str(tg_id),
        "ipn_callback_url": f"{public}/payments/now/webhook",
        "success_url": f"{public}/payments/now/thanks",
        "cancel_url": f"{public}/payments/now/cancel",
    }
    try:
        r = requests.post(
            f"{_base()}/invoice",
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        r.raise_for_status()
        inv = r.json()
        url = inv.get("invoice_url") or inv.get("data", {}).get("invoice_url")
        if not url:
            return JSONResponse(
                {"ok": False, "error": "no invoice_url", "resp": inv}, status_code=502
            )
        return RedirectResponse(url, status_code=303)
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", None)
        return JSONResponse(
            {"ok": False, "error": str(e), "resp": body}, status_code=502
        )


@router.post("/payments/now/webhook")
async def now_webhook(request: Request):
    secret = os.getenv("NOWP_IPN_SECRET", "")
    sig = request.headers.get("x-nowpayments-sig", "")
    body = await request.body()
    if not _verify_sig(secret, body, sig):
        return JSONResponse({"ok": False, "reason": "bad_signature"}, status_code=401)
    data = json.loads(body.decode("utf-8"))
    status = (data.get("payment_status") or "").lower()
    str(data.get("order_id") or "")
    if status in ("finished", "confirmed"):
        pass
    return JSONResponse({"ok": True})


@router.get("/payments/now/thanks")
def now_thanks():
    return {"ok": True, "status": "thanks"}


@router.get("/payments/now/cancel")
def now_cancel():
    return {"ok": False, "status": "cancel"}
