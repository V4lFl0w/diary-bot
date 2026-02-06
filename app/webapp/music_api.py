from __future__ import annotations

from typing import List, Dict, Any, Optional, AsyncIterator
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import async_session

from app.models.user import User
from app.models.user_track import UserTrack

# MVP-авторизация: tg_id прилетает с фронта (Telegram WebApp initDataUnsafe.user.id)
# Дальше можно усилить до verify initData подписи.


for _mod, _name in (
    ("app.db", "get_db_session"),
    ("app.db.session", "get_db_session"),
    ("app.db", "get_session"),
    ("app.db.session", "get_session"),
    ("app.db", "get_async_session"),
    ("app.db.session", "get_async_session"),
):
    try:
        m = __import__(_mod, fromlist=[_name])
        fn = getattr(m, _name, None)
        if fn:
            get_db_session = fn  # type: ignore
            break
    except Exception:
        continue

router = APIRouter(prefix="/webapp/music/api", tags=["webapp-music"])


async def _session_dep() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


@router.get("/health")
async def health() -> Dict[str, str]:
    return {"ok": "1"}


@router.get("/my")
async def my_playlist(
    tg_id: int = Query(..., description="Telegram user id (from initDataUnsafe.user.id)"),
    session: AsyncSession = Depends(_session_dep),
) -> Dict[str, Any]:
    user: Optional[User] = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        return {"ok": True, "items": []}

    rows = (
        (
            await session.execute(
                select(UserTrack).where(UserTrack.user_id == user.id).order_by(UserTrack.id.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )

    items: List[Dict[str, Any]] = []
    for t in rows:
        fid = (t.file_id or "").strip()
        items.append(
            {
                "id": t.id,
                "title": (t.title or "Track"),
                "file_id": fid,
                "is_url": fid.startswith("http://") or fid.startswith("https://"),
            }
        )

    return {"ok": True, "items": items}
