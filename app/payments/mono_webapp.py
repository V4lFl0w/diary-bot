from __future__ import annotations

import os
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.db import async_session
from app.models.user import User
from app.models.payment import Payment, PaymentPlan, PaymentStatus

router = APIRouter(prefix="/api/mono", tags=["mono"])
logger = logging.getLogger(__name__)

MONO_CREATE_URL = "https://api.monobank.ua/api/merchant/invoice/create"
MONO_PUBKEY_URL = "https://api.monobank.ua/api/merchant/pubkey"
_MONO_PUBKEY = None


async def get_mono_pubkey() -> Any:
    global _MONO_PUBKEY
    if _MONO_PUBKEY is None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(MONO_PUBKEY_URL)
            r.raise_for_status()
            key_str = r.json()["key"]
            _MONO_PUBKEY = serialization.load_pem_public_key(key_str.encode())
    return _MONO_PUBKEY


# FastAPI Dependency для безопасного получения сессии БД
async def get_db_session():
    async with async_session() as session:
        yield session


class MonoInvoiceIn(BaseModel):
    tg_id: int = Field(..., gt=0)
    kind: str = Field(..., min_length=1, max_length=32)
    amount_uah: int = Field(..., gt=0, le=200_000)
    title: str = Field(default="DiaryBot Premium")
    description: str = Field(default="Оплата через Monobank")
    redirect_url: Optional[str] = None


class MonoInvoiceOut(BaseModel):
    invoice_url: str


@router.post("/invoice", response_model=MonoInvoiceOut)
async def create_mono_invoice(body: MonoInvoiceIn) -> MonoInvoiceOut:
    token = os.getenv("MONO_TOKEN") or os.getenv("MONOBANK_TOKEN") or os.getenv("MONO_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MONO_TOKEN/MONOBANK_TOKEN not set")

    amount = int(body.amount_uah) * 100

    payload: dict = {
        "amount": amount,
        "merchantPaymInfo": {
            "reference": f"webapp:{body.kind}:{body.tg_id}",
            "destination": body.title,
            "comment": body.description,
        },
    }

    if body.redirect_url:
        payload["redirectUrl"] = body.redirect_url

    base_url = (os.getenv("PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base_url:
        payload["webHookUrl"] = f"{base_url}/api/mono/webhook"

    headers = {"X-Token": token}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(MONO_CREATE_URL, headers=headers, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=500, detail=f"mono api error: {r.status_code}: {r.text}")

        data = r.json()
        url = data.get("pageUrl") or data.get("invoiceUrl") or data.get("url")
        if not url:
            raise HTTPException(status_code=500, detail="mono response missing invoice url")

        return MonoInvoiceOut(invoice_url=url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mono invoice failed: {type(e).__name__}: {e}")


@router.post("/webhook")
async def mono_webhook(
    request: Request,
    x_sign: str = Header(None),
    session: AsyncSession = Depends(get_db_session)
):
    """
    Обработчик вебхуков от Monobank.
    """
    raw_body = await request.body()
    if not x_sign:
        logger.warning("Mono webhook: missing X-Sign header")
        return PlainTextResponse("bad request", status_code=400)

    # 1. Проверяем подпись Монобанка
    try:
        pubkey = await get_mono_pubkey()
        pubkey.verify(base64.b64decode(x_sign), raw_body, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as e:
        logger.error(f"Mono signature invalid: {e}")
        return PlainTextResponse("forbidden", status_code=403)

    # 2. Читаем JSON
    try:
        data = json.loads(raw_body)
    except Exception:
        return PlainTextResponse("invalid json", status_code=400)

    invoice_id = data.get("invoiceId")
    status = data.get("status")
    reference = data.get("reference", "")

    logger.info(f"Mono webhook received: invoice={invoice_id}, status={status}, ref={reference}")

    if status != "success":
        # Если статус created/processing/reversed, просто отвечаем ОК
        return PlainTextResponse("ok")

    # 3. Разбираем reference (формат: webapp:sub:basic:month:319145673)
    parts = reference.split(":")
    if len(parts) < 4 or parts[0] != "webapp":
        return PlainTextResponse("ok")

    pay_type = parts[1]  # 'sub' или 'tokens'
    plan_id = ""
    period = ""
    tg_id = 0

    try:
        if pay_type == "sub" and len(parts) >= 5:
            plan_id = parts[2]
            period = parts[3]
            tg_id = int(parts[4])
        elif pay_type == "tokens" and len(parts) >= 4:
            plan_id = parts[2]
            tg_id = int(parts[3])
            period = "topup"
        else:
            return PlainTextResponse("ok")
    except ValueError:
        return PlainTextResponse("ok")

    # 4. Ищем юзера в БД
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        logger.error(f"Mono webhook: User {tg_id} not found for invoice {invoice_id}")
        return PlainTextResponse("ok")

    # 5. Проверяем, не начислили ли мы уже за этот invoice
    existing_pay = (await session.execute(
        select(Payment).where(Payment.external_id == invoice_id)
    )).scalar_one_or_none()

    if existing_pay and existing_pay.status == PaymentStatus.PAID:
        return PlainTextResponse("ok")

    plan_enum = PaymentPlan.MONTH
    if period == "quarter": plan_enum = PaymentPlan.QUARTER
    elif period == "year": plan_enum = PaymentPlan.YEAR
    elif period == "topup": plan_enum = PaymentPlan.TOPUP

    amount_cents = int(data.get("amount", 0))

    if not existing_pay:
        existing_pay = Payment.create_mono_subscription(
            user_id=user.id,
            plan=plan_enum,
            amount_cents=amount_cents,
            external_id=invoice_id
        )
        session.add(existing_pay)

    existing_pay.mark_paid()
    existing_pay.sku = f"{pay_type}:{plan_id}:{period}"
    existing_pay.payload = json.dumps(data, ensure_ascii=False)

    # 6. ВЫДАЕМ ПРЕМИУМ
    now = datetime.now(timezone.utc)

    if pay_type == "sub":
        days = 30
        if period == "quarter": days = 90
        elif period == "year": days = 365

        current_until = getattr(user, "premium_until", None)
        if not current_until or current_until < now:
            current_until = now

        user.premium_until = current_until + timedelta(days=days)
        user.is_premium = True
        
        if hasattr(user, "assistant_plan"):
            user.assistant_plan = plan_id

        # Бонусные токены
        bonus = {"basic": 60, "pro": 180, "max": 450}.get(plan_id, 0)
        if bonus > 0 and hasattr(user, "tokens_balance"):
            current_tokens = getattr(user, "tokens_balance", 0) or 0
            user.tokens_balance = current_tokens + bonus

    elif pay_type == "tokens":
        tokens_to_add = {"t100": 100, "t300": 300, "t800": 800}.get(plan_id, 0)
        if tokens_to_add > 0 and hasattr(user, "tokens_balance"):
            current_tokens = getattr(user, "tokens_balance", 0) or 0
            user.tokens_balance = current_tokens + tokens_to_add

    await session.commit()

    # 7. Отправляем уведомление
    try:
        from aiogram import Bot
        bot_token = os.getenv("BOT_TOKEN")
        if bot_token:
            bot = Bot(token=bot_token)
            if pay_type == "tokens":
                msg = f"🎉 <b>Оплата прошла успешно!</b>\n\nТокены зачислены на баланс."
            else:
                msg = f"🎉 <b>Оплата прошла успешно!</b>\n\nТариф <b>{plan_id.upper()}</b> активирован. Нажмите /start, чтобы обновить меню."
            await bot.send_message(tg_id, msg, parse_mode="HTML")
            await bot.session.close()
    except Exception as e:
        logger.warning(f"Mono webhook: failed to send telegram notification: {e}")

    return PlainTextResponse("ok")
