from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, max_events: int = 25, per_seconds: int = 10):
        self.max_events = int(max_events)
        self.per_seconds = int(per_seconds)
        self._bucket: DefaultDict[int, Deque[float]] = defaultdict(deque)

    async def __call__(self, handler, event: TelegramObject, data: dict):
        tg_id = None
        if isinstance(event, Message) and event.from_user:
            tg_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_id = event.from_user.id

        if tg_id is None:
            return await handler(event, data)

        now = time.time()
        q = self._bucket[int(tg_id)]

        while q and (now - q[0]) > self.per_seconds:
            q.popleft()

        if len(q) >= self.max_events:
            # молча режем флуд (можно отвечать "слишком часто", но это будет спамить)
            return

        q.append(now)
        return await handler(event, data)
