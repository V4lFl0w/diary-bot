from __future__ import annotations

from typing import Any, Optional, Dict, List, Tuple

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

router = Router(name="premium_bridge_v2")


@router.callback_query(F.data == "open_premium")
async def open_premium_cb(
    c: CallbackQuery,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Универсальный мост:
    любая кнопка с callback_data="open_premium"
    открывает меню премиума.
    """
    try:
        # пробуем использовать твой основной модуль премиума
        from app.handlers.premium import cmd_premium  # type: ignore
        await c.answer()
        # cmd_premium уже умеет строить меню и брать locale
        await cmd_premium(c.message, session, lang)  # type: ignore[arg-type]
        return
    except Exception:
        pass

    # fallback если вдруг модуль премиума не импортируется
    try:
        await c.answer("Premium menu is unavailable", show_alert=True)
    except Exception:
        pass
