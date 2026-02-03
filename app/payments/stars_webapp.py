from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aiogram import Bot
from aiogram.types import LabeledPrice

router = APIRouter(prefix="/api/stars", tags=["stars"])


class StarsInvoiceIn(BaseModel):
    # сколько Stars списать (целое число)
    stars: int = Field(..., gt=0, le=10_000)

    # произвольный payload, но лучше чтобы начинался с "stars:buy:"
    payload: str = Field(..., min_length=3, max_length=256)

    title: str = Field(default="Premium")
    description: str = Field(default="Оплата Premium через Telegram Stars")
    photo_url: Optional[str] = None


class StarsInvoiceOut(BaseModel):
    invoice_link: str


@router.post("/invoice", response_model=StarsInvoiceOut)
async def create_stars_invoice(body: StarsInvoiceIn) -> StarsInvoiceOut:
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="BOT_TOKEN/TELEGRAM_BOT_TOKEN not set")

    bot = Bot(token=token)

    try:
        prices = [LabeledPrice(label="Telegram Stars", amount=int(body.stars))]

        # IMPORTANT: Stars = currency "XTR", provider_token пустой
        link = await bot.create_invoice_link(
            title=body.title,
            description=body.description,
            payload=body.payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            photo_url=body.photo_url,
        )
        return StarsInvoiceOut(invoice_link=link)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create_invoice_link failed: {type(e).__name__}: {e}")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
