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

# Твои родные точные импорты
from app.db import async_session
from app.models.payment import Payment, PaymentPlan, PaymentProvider, PaymentStatus
from app.models.user import User
from app.services.pricing import get_spec

router = APIRouter(prefix="/api/stars", tags=["stars"])


class StarsInvoiceIn(BaseModel):
    stars: int = Field(..., gt=0, le=10_000)
    
    # Данные от Mini App
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

    # 1. Формируем SKU (например "pro_month")
    sku = f"{body.plan_id}_{body.period}".strip().lower()
    spec = get_spec(sku)
    
    if not spec:
        logging.error(f"Invalid SKU received: {sku}")
        raise HTTPException(status_code=400, detail=f"Invalid SKU: {sku}")

    # 2. Открываем сессию к БД вручную через твой async_session
    async with async_session() as session:
        # Ищем юзера в БД по tg_id
        user = (await session.execute(select(User).where(User.tg_id == body.tg_id))).scalar_one_or_none()
        
        if not user:
            logging.error(f"User not found for tg_id: {body.tg_id}")
            raise HTTPException(status_code=404, detail="User not found")

        # 3. Мапим период (string) в твой Enum (PaymentPlan)
        period_to_plan = {
            "trial": PaymentPlan.TRIAL,
            "month": PaymentPlan.MONTH,
            "quarter": PaymentPlan.QUARTER,
            "year": PaymentPlan.YEAR,
            "lifetime": PaymentPlan.LIFETIME,
        }
        plan_enum = period_to_plan.get(body.period, PaymentPlan.MONTH)

        # 4. СОЗДАЕМ ПЛАТЕЖ В БД
        payment = Payment(
            user_id=user.id,
            provider=PaymentProvider.STARS,
            plan=plan_enum,
            amount_cents=int(body.stars), # Как у тебя в модели: "для Stars (XTR) — количество Stars"
            currency="XTR",
            sku=sku,
            payload=json.dumps({"sku": sku, "tier": spec.tier, "period": spec.period, "days": spec.days}),
            status=PaymentStatus.PENDING,
        )
        
        session.add(payment)
        await session.flush() # Получаем payment.id из базы
        
        # 5. Формируем ПРАВИЛЬНЫЙ payload для Телеграма (то, что ждет твой payments_stars.py)
        real_payload = f"premium_stars:{payment.id}:{sku}"
        
        await session.commit()

    # 6. Генерируем ссылку через Aiogram
    bot = Bot(token=token)
    try:
        prices = [LabeledPrice(label="Telegram Stars", amount=int(body.stars))]

        logging.info(f"Creating Stars invoice: {body.stars} XTR, payload={real_payload}")
        
        link = await bot.create_invoice_link(
            title=body.title,
            description=body.description,
            payload=real_payload, # Вшиваем правильный payload!
            provider_token="",    # Для Stars обязательно пустое
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