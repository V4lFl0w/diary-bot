from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def log_admin_action(
    session: AsyncSession,
    admin_tg_id: int,
    action: str,
    target_tg_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {"ts": _now_utc_iso(), "action": action}
    if target_tg_id is not None:
        payload["target_tg_id"] = int(target_tg_id)
    if payment_id is not None:
        payload["payment_id"] = int(payment_id)
    if extra:
        payload.update(extra)

    session.add(
        Event(
            tg_id=int(admin_tg_id),
            name=f"admin:{action}",
            meta=json.dumps(payload, ensure_ascii=False),
        )
    )
    await session.commit()


# Backward-compatible alias (refund_flow imports this name)
log_admin_audit = log_admin_action
