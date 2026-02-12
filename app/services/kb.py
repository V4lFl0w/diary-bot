from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_item import KBItem


def _compact(s: str, max_len: int = 260) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) <= max_len:
        return s
    return s[:max_len].rsplit(" ", 1)[0].strip() + "…"


async def kb_add(
    session: AsyncSession,
    *,
    user_id: int,
    content: str,
    title: str | None = None,
    tags: str | None = None,
) -> KBItem:
    item = KBItem(user_id=int(user_id), content=(content or "").strip(), title=(title or None), tags=(tags or None))
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def kb_search(
    session: AsyncSession,
    *,
    user_id: int,
    q: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not q:
        return []
    qn = q.strip()
    stmt = (
        select(KBItem)
        .where(KBItem.user_id == int(user_id))
        .where(KBItem.content.ilike(f"%{qn}%"))
        .order_by(desc(KBItem.created_at))
        .limit(int(limit))
    )
    res = await session.execute(stmt)
    items = [row[0] for row in res.all()]
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "id": int(it.id),
                "title": (it.title or "").strip() or None,
                "content": _compact(it.content or ""),
                "tags": (it.tags or "").strip() or None,
                "created_at": getattr(it, "created_at", None),
            }
        )
    return out
