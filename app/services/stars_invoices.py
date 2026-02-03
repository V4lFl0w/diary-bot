from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List

try:
    from aiogram import Bot
except Exception:
    Bot = None  # type: ignore

try:
    from aiogram.types import LabeledPrice
except Exception:
    LabeledPrice = None  # type: ignore


@dataclass(frozen=True)
class StarsInvoice:
    title: str
    description: str
    payload: str
    stars_amount: int  # integer stars
    photo_url: Optional[str] = None


async def create_stars_invoice_link(bot: "Bot", inv: StarsInvoice) -> str:
    """
    Returns invoice link for Telegram Stars (currency XTR).
    """
    if LabeledPrice is None:
        raise RuntimeError("aiogram.types.LabeledPrice not available")

    if inv.stars_amount <= 0:
        raise ValueError("stars_amount must be > 0")

    prices: List[LabeledPrice] = [LabeledPrice(label="Telegram Stars", amount=int(inv.stars_amount))]

    # IMPORTANT: for Stars use currency="XTR"
    link = await bot.create_invoice_link(
        title=inv.title,
        description=inv.description,
        payload=inv.payload,
        provider_token="",  # Stars don't need provider token
        currency="XTR",
        prices=prices,
        photo_url=inv.photo_url,
    )
    return link
