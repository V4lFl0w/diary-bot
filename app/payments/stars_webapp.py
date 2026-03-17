from __future__ import annotations

import os
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aiogram import Bot
from aiogram.types import LabeledPrice
from sqlalchemy import select

from app.db import async_session
from app.models.payment import Payment, PaymentPlan, PaymentProvider, PaymentStatus
from app.models.user import User
from app.services.pricing import get_spec

router = APIRouter(prefix="/api/stars", tags=["stars"])


# 🔥 ИЗМЕНЕНО: Мы больше не просим фронтенд передавать нам сумму звезд!
class StarsInvoiceIn(BaseModel):
    tg_id: int
    plan_id: str
    period: str

    title: str = Field(default="Premium")
    description: str = Field(default="Оплата Premium через Telegram Stars")
    photo_url: Optional[str] = None


class StarsInvoiceOut(BaseModel):
    invoice_link: str


@router.post("/invoice", response_model=StarsInvoiceOut)
async def create_stars_invoice(body: StarsInvoiceIn) -> StarsInvoiceOut:
    """Создает запись Payment в БД и генерирует invoice_link для Stars"""

    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logging.error("create_stars_invoice: BOT_TOKEN is missing")
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")

    sku = f"{body.plan_id}_{body.period}".strip().lower()
    spec = get_spec(sku)

    if not spec:
        logging.error(f"Invalid SKU received: {sku}")
        raise HTTPException(status_code=400, detail=f"Invalid SKU: {sku}")

    # 🔥 ИЗМЕНЕНО: Бэкенд сам берет цену из pricing.py
    real_stars_price = int(spec.stars)
    if real_stars_price <= 0:
        raise HTTPException(status_code=400, detail=f"Invalid price for SKU: {sku}")

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.tg_id == body.tg_id))).scalar_one_or_none()

        if not user:
            logging.error(f"User not found for tg_id: {body.tg_id}")
            raise HTTPException(status_code=404, detail="User not found")

        period_to_plan = {
            "trial": PaymentPlan.TRIAL,
            "month": PaymentPlan.MONTH,
            "quarter": PaymentPlan.QUARTER,
            "year": PaymentPlan.YEAR,
            "lifetime": PaymentPlan.LIFETIME,
        }
        plan_enum = period_to_plan.get(body.period, PaymentPlan.MONTH)

        payment = Payment(
            user_id=user.id,
            provider=PaymentProvider.STARS,
            plan=plan_enum,
            amount_cents=real_stars_price,  # <-- Безопасная цена
            currency="XTR",
            sku=sku,
            payload=json.dumps({"sku": sku, "tier": spec.tier, "period": spec.period, "days": spec.days}),
            status=PaymentStatus.PENDING,
        )

        session.add(payment)
        await session.flush()
        real_payload = f"premium_stars:{payment.id}:{sku}"
        await session.commit()

    bot = Bot(token=token)
    try:
        # Телеграму тоже отдаем безопасную цену
        prices = [LabeledPrice(label="Telegram Stars", amount=real_stars_price)]
        logging.info(f"Creating Stars invoice: {real_stars_price} XTR, payload={real_payload}")

        link = await bot.create_invoice_link(
            title=body.title,
            description=body.description,
            payload=real_payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            photo_url=body.photo_url,
        )
        return StarsInvoiceOut(invoice_link=link)

    except Exception as e:
        logging.error(f"Failed to create Stars invoice link: {e}")
        raise HTTPException(status_code=500, detail=f"create_invoice_link failed: {e}")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
