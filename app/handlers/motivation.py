from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

router = Router(name="motivation")

@router.message(F.text.in_(("🔥 Мотивация", "Мотивация", "Motivation")))
async def motivation_open(m: Message, session: AsyncSession):
    await m.answer(
        "🔥 **Мотивация**\n\nВыбери режим:\n• Поддержка\n• Пинок\n• План на день",
        parse_mode="Markdown",
    )
