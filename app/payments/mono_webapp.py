from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/mono", tags=["mono"])

MONO_CREATE_URL = "https://api.monobank.ua/api/merchant/invoice/create"


class MonoInvoiceIn(BaseModel):
    tg_id: int = Field(..., gt=0)

    # subscription: basic/pro/max, tokens: t100/t300/t800
    kind: str = Field(..., min_length=1, max_length=32)

    # сумма в гривнах
    amount_uah: int = Field(..., gt=0, le=200_000)

    title: str = Field(default="DiaryBot Premium")
    description: str = Field(default="Оплата через Monobank")

    # куда вернуть пользователя после оплаты (можно твой домен webapp)
    redirect_url: Optional[str] = None


class MonoInvoiceOut(BaseModel):
    invoice_url: str


@router.post("/invoice", response_model=MonoInvoiceOut)
async def create_mono_invoice(body: MonoInvoiceIn) -> MonoInvoiceOut:
    token = os.getenv("MONO_TOKEN") or os.getenv("MONOBANK_TOKEN") or os.getenv("MONO_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MONO_TOKEN/MONOBANK_TOKEN not set")

    # mono expects amount in kopiykas
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
