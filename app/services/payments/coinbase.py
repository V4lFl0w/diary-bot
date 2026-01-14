# app/services/payments/coinbase.py
from __future__ import annotations

import aiohttp, json, uuid
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings
from app.models.user import User
from app.models.payment import Payment

API_BASE = getattr(settings, "coinbase_api_base", "https://api.commerce.coinbase.com")
COINBASE_CHARGES = "/charges"

def _headers(idempotency_key: str | None = None) -> dict:
    h = {
        "X-CC-Api-Key": settings.coinbase_api_key,
        "X-CC-Version": "2018-03-22",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h

async def create_coinbase_charge(
    session: AsyncSession,
    user: User,
    *,
    plan: str = "monthly",
    amount_usd: Optional[float] = None,
    description: Optional[str] = None,
) -> Tuple[Payment, str]:
    amount_usd = float(amount_usd if amount_usd is not None else settings.premium_price_usd)
    amount_cents = int(round(amount_usd * 100))

    pay = Payment(
        user_id=user.id,
        provider=PaymentProvider.CRYPTO,
        plan=plan,
        amount_cents=amount_cents,
        currency="USD",
        status=PaymentStatus.PENDING,
        payload=f"user:{user.id};plan:{plan}",
    )
    session.add(pay)
    await session.commit()
    await session.refresh(pay)

    body = {
        "name": "Premium 1 month",
        "description": description or "Diary Assistant Premium — 1 month access",
        "pricing_type": "fixed_price",
        "local_price": {"amount": f"{amount_usd:.2f}", "currency": "USD"},
        "metadata": {"payment_id": str(pay.id), "user_id": str(user.id), "plan": plan},
    }

    timeout = aiohttp.ClientTimeout(total=25)
    idem = str(uuid.uuid4())

    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(f"{API_BASE}{COINBASE_CHARGES}", headers=_headers(idem), data=json.dumps(body)) as resp:
            data = await resp.json()
            if resp.status >= 300:
                # отметим ошибку в платеже и пробросим исключение
                pay.status = PaymentStatus.FAILED
                pay.payload = (pay.payload or "") + f";err:{resp.status}"
                await session.commit()
                raise RuntimeError(f"Coinbase create charge error {resp.status}: {data}")

    charge = data.get("data") or {}
    hosted_url = charge.get("hosted_url")
    pay.external_id = charge.get("id")
    await session.commit()
    return pay, hosted_url

def build_pay_kb(url: str, lang: str = "ru") -> InlineKeyboardMarkup:
    text = {
        "ru": "💎 Оплатить Premium (Crypto)",
        "uk": "💎 Оплатити Premium (Crypto)",
        "en": "💎 Pay Premium (Crypto)",
    }.get((lang or "ru")[:2], "💎 Pay Premium (Crypto)")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])