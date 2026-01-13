from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import os, hmac, hashlib, json, logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

router = APIRouter()
log = logging.getLogger("payments.webhook")
WEBHOOK_SECRET = os.getenv("COINBASE_COMMERCE_WEBHOOK_SECRET","")
DATABASE_URL = os.getenv("DATABASE_URL","")
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None

async def grant_premium_days(tg_id: int, days: int = 31) -> bool:
    if not engine:
        return False
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE users "
                "SET premium_until = (CASE "
                "WHEN premium_until IS NOT NULL AND premium_until > (NOW() AT TIME ZONE 'utc') "
                "THEN premium_until ELSE (NOW() AT TIME ZONE 'utc') END) "
                " + (:days || ' days')::interval "
                "WHERE tg_id = :tg_id"
            ),
            {"tg_id": int(tg_id), "days": str(days)},
        )
        if res.rowcount == 0:
            try:
                await conn.execute(
                    text(
                        "INSERT INTO users (tg_id, premium_until) "
                        "VALUES (:tg_id, (NOW() AT TIME ZONE 'utc') + (:days || ' days')::interval)"
                    ),
                    {"tg_id": int(tg_id), "days": str(days)},
                )
            except Exception:
                pass
    return True

def _verify(sig_hdr: str, raw: bytes) -> bool:
    if not WEBHOOK_SECRET:
        return True
    digest = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig_hdr or "")

@router.post("/payments/coinbase/webhook")
async def coinbase_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-CC-Webhook-Signature","")
    if not _verify(sig, raw):
        return JSONResponse({"ok": False}, status_code=400)
    try:
        data = await request.json()
    except Exception:
        try:
            data = json.loads(raw.decode("utf-8","ignore"))
        except Exception:
            data = {}
    event = data.get("event") or {}
    etype = event.get("type")
    charge = event.get("data") or {}
    meta = charge.get("metadata") or {}
    tg_id = meta.get("tg_id")
    code = charge.get("code")
    log.info("coinbase_event type=%s code=%s tg_id=%s", etype, code, tg_id)
    ok_types = {"charge:confirmed","charge:resolved"}
    if etype in ok_types and tg_id:
        try:
            await grant_premium_days(int(tg_id), 31)
            log.info("premium_granted tg_id=%s", tg_id)
        except Exception as e:
            log.exception("premium_grant_failed tg_id=%s err=%s", tg_id, e)
            return JSONResponse({"ok": False}, status_code=500)
    return JSONResponse({"ok": True})
