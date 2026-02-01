from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Update

from app.logging_setup import clear_log_context, set_log_context


class LogContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            update_id = getattr(event, "update_id", None)

            tg_id = None
            chat_id = None
            try:
                if event.message and event.message.from_user:
                    tg_id = event.message.from_user.id
                    chat_id = event.message.chat.id if event.message.chat else None
                elif event.callback_query and event.callback_query.from_user:
                    tg_id = event.callback_query.from_user.id
                    if (
                        event.callback_query.message
                        and event.callback_query.message.chat
                    ):
                        chat_id = event.callback_query.message.chat.id
            except Exception:
                pass

            set_log_context(tg_id=tg_id, chat_id=chat_id, update_id=update_id)

            try:
                return await handler(event, data)
            finally:
                # ВАЖНО: aiogram.event логирует "Update handled" после обработки,
                # поэтому чистим контекст на следующем тике цикла.
                try:
                    asyncio.get_running_loop().call_soon(clear_log_context)
                except Exception:
                    clear_log_context()

        return await handler(event, data)
