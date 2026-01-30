from __future__ import annotations

import os
import time
import contextvars
from typing import Any, Awaitable, Callable, Dict

TRACE_ENABLED = os.getenv("TRACE_ASSISTANT", "0") == "1"

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
trace_src_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_src", default="")

def _mk_trace_id(update: Any) -> str:
    try:
        uid = getattr(update, "update_id", None)
        if uid is not None:
            return f"u{uid}-{int(time.time()*1000)%100000}"
    except Exception:
        pass
    return f"t{int(time.time()*1000)}"

def tlog(logger, stage: str, **kv):
    if not TRACE_ENABLED:
        return
    tid = trace_id_var.get() or "-"
    src = trace_src_var.get() or "-"
    try:
        logger.info("[trace] %s | %s | %s | %s", tid, src, stage, kv)
    except Exception:
        pass

class TraceUpdateMiddleware:
    """Логирует жизненный цикл update:
    - вход
    - длительность
    - ошибки
    - плюс выставляет trace_id в contextvars
    """
    def __init__(self, logger):
        self.logger = logger

    async def __call__(self, handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]], event: Any, data: Dict[str, Any]) -> Any:
        if not TRACE_ENABLED:
            return await handler(event, data)

        tid = _mk_trace_id(event)
        token1 = trace_id_var.set(tid)
        token2 = trace_src_var.set("update")

        t0 = time.time()
        # базовые поля: тип апдейта, chat/user ids если можно
        u = None
        try:
            m = getattr(event, "message", None) or getattr(event, "callback_query", None)
            if m is not None:
                u = getattr(getattr(m, "from_user", None), "id", None)
        except Exception:
            pass

        tlog(self.logger, "update.in", update_id=getattr(event, "update_id", None), user_id=u)

        try:
            res = await handler(event, data)
            dt = int((time.time() - t0) * 1000)
            tlog(self.logger, "update.out", ms=dt)
            return res
        except Exception as e:
            dt = int((time.time() - t0) * 1000)
            tlog(self.logger, "update.err", ms=dt, err=str(e))
            raise
        finally:
            try:
                trace_id_var.reset(token1)
                trace_src_var.reset(token2)
            except Exception:
                pass
