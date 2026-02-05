from __future__ import annotations

import os
import aiohttp
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.user_track import UserTrack

router = APIRouter(prefix="/api/music", tags=["music-webapp"])


# --- DB session dependency (auto-resolve) ---
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession

def _resolve_async_sessionmaker():
    # пробуем найти то, что реально экспортится в app.db
    import app.db as db
    for name in (
        "async_sessionmaker",
        "async_session_maker",
        "async_session_factory",
        "async_session",
        "SessionLocal",
        "async_session_local",
    ):
        sm = getattr(db, name, None)
        if sm is not None:
            return sm
    raise RuntimeError("Cannot resolve async sessionmaker in app.db (no known session factory exported)")

_ASYNC_SESSIONMAKER = _resolve_async_sessionmaker()

async def get_db_session() -> AsyncIterator[AsyncSession]:
    # поддержим и sessionmaker(), и callable factory
    async with _ASYNC_SESSIONMAKER() as session:
        yield session
# --- /DB session dependency ---
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


@router.get("/my")
async def my_tracks(uid: int, session: AsyncSession = Depends(get_db_session)):
    user = await session.scalar(select(User).where(User.tg_id == uid).limit(1))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    rows = (
        (await session.execute(
            select(UserTrack)
            .where(UserTrack.user_id == user.id)
            .order_by(UserTrack.id.desc())
            .limit(50)
        ))
        .scalars()
        .all()
    )

    out = []
    for t in rows:
        fid = (t.file_id or "").strip()
        if fid.startswith("https://"):
            stream_url = fid  # внешняя ссылка (mp3/ogg/m4a) — WebApp играет напрямую
        else:
            stream_url = f"/api/music/stream/{t.id}?uid={uid}"  # telegram file_id -> стрим через бэк

        out.append({"id": t.id, "title": t.title or "Track", "stream_url": stream_url})
    return out


@router.get("/stream/{track_id}")
async def stream_track(track_id: int, uid: int, session: AsyncSession = Depends(get_db_session)):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not set")

    user = await session.scalar(select(User).where(User.tg_id == uid).limit(1))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    track = await session.scalar(
        select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.id == track_id).limit(1)
    )
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    file_id = (track.file_id or "").strip()

    if file_id.startswith("https://"):
        raise HTTPException(status_code=400, detail="Track is external URL")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id}) as r:
            data = await r.json()
            if not data.get("ok"):
                raise HTTPException(status_code=502, detail=f"Telegram getFile failed: {data}")
            path = data["result"]["file_path"]

        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
        resp = await s.get(file_url)
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"Telegram file download failed: {resp.status}")

        headers = {"Cache-Control": "no-store"}
        return StreamingResponse(
            resp.content.iter_chunked(64 * 1024),
            media_type="audio/mpeg",
            headers=headers,
        )
