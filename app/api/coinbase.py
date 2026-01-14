from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import hmac, hashlib, json

from app.config import settings
from app.db import async_session
from app.models.user import User
from app.models.payment import Payment

router = APIRouter(prefix="/payments/coinbase", tags=["payments"])

@router.post("/webhook")
async def coinbase_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-CC-Webhook-Signature", "")
    calc = hmac.new(settings.coinbase_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, sig):
        raise HTTPException(status_code=400, detail="bad signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")
    evt = payload.get("event") or {}
    if evt.get("type") != "charge:confirmed":
        return {"ok": True}
    data = evt.get("data") or {}
    metadata = data.get("metadata") or {}
    pricing_local = (data.get("pricing") or {}).get("local") or {}
    try:
        user_id = int(metadata["user_id"])
        ext_id = str(data["id"])
        amount_cents = int(round(float(pricing_local["amount"]) * 100))
        currency = str(pricing_local["currency"])
    except Exception:
        raise HTTPException(status_code=400, detail="bad payload")
    async with async_session() as session, session.begin():
        exists = await session.execute(select(Payment.id).where(Payment.external_id == ext_id))
        if exists.scalar_one_or_none():
            return {"ok": True}
        pay = Payment(
            user_id=user_id,
            provider=PaymentProvider.CRYPTO,
            plan=PaymentPlan.MONTH,
            amount_cents=amount_cents,
            currency=currency,
            status="succeeded",
            external_id=ext_id,
            payload=json.dumps(payload, ensure_ascii=False),
            paid_at=datetime.now(timezone.utc),
        )
        session.add(pay)
        await _extend_premium(session, user_id, months=1)
    return {"ok": True}

async def _extend_premium(session, user_id: int, months: int = 1):
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    now = datetime.now(timezone.utc)
    base = user.premium_until if getattr(user, "premium_until", None) and user.premium_until > now else now
    user.premium_until = base + timedelta(days=30 * months)
    session.add(user)
