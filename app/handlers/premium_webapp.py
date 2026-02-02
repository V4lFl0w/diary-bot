from __future__ import annotations

import json
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

router = Router(name="premium_webapp")

@router.message(F.web_app_data)
async def on_webapp_data(m: Message, session: AsyncSession) -> None:
    raw = getattr(getattr(m, "web_app_data", None), "data", None)
    if not raw:
        return

    try:
        data = json.loads(raw)
    except Exception:
        return

    action = (data.get("action") or "").strip().lower()

    # Это НЕ медитации, а наш premium webapp
    if action not in {"buy_subscription", "buy_tokens"}:
        return

    # Пока токены не подключены — честно говорим
    if action == "buy_tokens":
        await m.answer("⚠️ Покупка токенов пока не подключена. Можно оплатить Premium картой или через Stars.")
        return

    # buy_subscription: открываем меню премиума, где уже есть Stars и карта
    try:
        from app.handlers.premium import cmd_premium  # меню премиума
        await cmd_premium(m, session, lang=None)  # cmd_premium сам возьмёт locale/lang
    except Exception:
        await m.answer("⚠️ Не удалось открыть меню Premium. Попробуй команду /premium.")