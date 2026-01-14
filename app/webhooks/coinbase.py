# app/api/coinbase.py
from __future__ import annotations

import hmac, hashlib, json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException
from starlette.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.models.payment import Payment
from app.models.user import User
from app.services.payments.coinbase import create_coinbase_charge

router = APIRouter(prefix="/payments/coinbase", tags=["payments"])

def _verify_signature(raw: bytes, sig: str | None) -> None:
    secret = (settings.coinbase_webhook_secret or "").encode()
    if not secret:
        raise HTTPException(500, "webhook secret is not set")
    calc = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, (sig or "")):
        raise HTTPException(400, "bad signature")

@router.post("/webhook")
async def coinbase_webhook(request: Request):
    raw = await request.body()
    _verify_signature(raw, request.headers.get("X-CC-Webhook-Signature"))

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "bad json")

    evt = (payload.get("event") or {})
    etype = evt.get("type")
    data = (evt.get("data") or {})
    meta = (data.get("metadata") or {})
    ext_id = data.get("id")

    local_pricing = ((data.get("pricing") or {}).get("local") or {})
    local_amount = local_pricing.get("amount")
    local_currency = local_pricing.get("currency")

    async with async_session() as s:
        # 1) ищем платёж по external_id (и/или по metadata.payment_id как резерв)
        pay = (await s.execute(select(Payment).where(Payment.external_id == ext_id))).scalar_one_or_none()
        if not pay and meta.get("payment_id"):
            pay = (await s.execute(select(Payment).where(Payment.id == int(meta["payment_id"])))).scalar_one_or_none()

        if not pay:
            # неизвестный чардж — тихо игнорируем (идемпотентность)
            return {"ok": True}

        # 2) уже подтверждён? тоже идемпотентно выходим
        if pay.status == "succeeded":
            return {"ok": True}

        # 3) обновляем статус и продлеваем премиум
        if etype == "charge:confirmed":
            pay.status = "succeeded"
            pay.paid_at = datetime.now(timezone.utc)
            if local_amount and local_currency:
                try:
                    pay.amount_cents = int(round(float(local_amount) * 100))
                    pay.currency = local_currency
                except Exception:
                    pass

            # продлить премиум владельцу
            user = (await s.execute(select(User).where(User.id == pay.user_id))).scalar_one_or_none()
            if user:
                now = datetime.now(timezone.utc)
                base = user.premium_until if user.premium_until and user.premium_until > now else now
                user.premium_until = base + timedelta(days=30)
                s.add(user)

        elif etype in {"charge:failed", "charge:delayed", "charge:expired", "charge:unresolved"}:
            pay.status = "failed"

        # сохраняем небольшую «квитанцию» события (полный JSON в String(256) не влезет)
        pay.payload = json.dumps({"etype": etype, "ext_id": ext_id}, ensure_ascii=False)[:250]
        s.add(pay)
        await s.commit()

    return {"ok": True}

@router.get("/buy")
async def coinbase_buy(user_id: int | None = None, tg_id: int | None = None):
    if not user_id and not tg_id:
        raise HTTPException(400, "user_id or tg_id required")

    async with async_session() as s:
        if user_id:
            user = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        else:
            user = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if not user:
            raise HTTPException(404, "user not found")

        pay, hosted = await create_coinbase_charge(
            session=s,
            user=user,
            plan=PaymentPlan.MONTH,
            amount_usd=settings.premium_price_usd,
            description="Diary Assistant Premium — 1 month",
        )

    return RedirectResponse(hosted, status_code=302)