from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_usage import LLMUsage

# prices: USD per 1M tokens (input, output)
# source: OpenAI model selection/pricing table
_MODEL_PRICES_PER_1M = {
    "gpt-4o": (5.00, 15.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def _price_for_model(model: str | None):
    m = (model or "").strip().lower()
    if not m:
        return None
    for key, price in _MODEL_PRICES_PER_1M.items():
        if m == key or m.startswith(key + "-"):
            return price
    return None


def _calc_cost_usd_micros(model: str | None, input_tokens: int, output_tokens: int) -> int:
    price = _price_for_model(model)
    if not price:
        return 0
    in_p, out_p = price
    cost = (input_tokens / 1_000_000.0) * in_p + (output_tokens / 1_000_000.0) * out_p
    return int(round(cost * 1_000_000))


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
        cost_usd_micros=int((cost_usd_micros or 0) or _calc_cost_usd_micros(model, input_tokens, output_tokens)),
        meta=meta or {},
    )
    session.add(row)
    await session.flush()
    # IMPORTANT: on prod the session may not be auto-committed anywhere
    # so LLMUsage rows could be lost. Best-effort commit.
    try:
        await session.commit()
    except Exception:
        pass
