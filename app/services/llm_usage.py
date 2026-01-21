from __future__ import annotations
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from app.models.llm_usage import LLMUsage


def _safe_int(x: Any) -> int:
    try:
        return int(x or 0)
    except Exception:
        return 0


async def log_llm_usage(
    session: Optional[AsyncSession],
    *,
    user_id: int | None,
    feature: str,
    model: str,
    plan: str,
    resp: Any,
    meta: dict | None = None,
    cost_usd_micros: int = 0,
) -> None:
    if not session:
        return

    usage = getattr(resp, "usage", None) or {}

    def g(obj: Any, key: str, default: Any = 0) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    input_tokens = _safe_int(g(usage, "input_tokens", 0))
    output_tokens = _safe_int(g(usage, "output_tokens", 0))
    total_tokens = _safe_int(g(usage, "total_tokens", input_tokens + output_tokens))

    row = LLMUsage(
        user_id=user_id,
        feature=feature,
        model=model,
        plan=plan,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        response_id=str(getattr(resp, "id", "") or "") or None,
        cost_usd_micros=int(cost_usd_micros or 0),
        meta=meta or {},
    )
    session.add(row)
    await session.flush()
