import os
import aiohttp

from typing import List, Dict, Any, Optional, AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import async_session

from app.models.user import User
from app.models.user_track import UserTrack

# MVP-авторизация: tg_id прилетает с фронта (Telegram WebApp initDataUnsafe.user.id)
# Дальше можно усилить до verify initData подписи.



router = APIRouter(prefix="/webapp/music/api", tags=["webapp-music"])

from typing import AsyncIterator
from app.db import async_session

async def session_dep() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session

import urllib.parse

TELEGRAM_API = "https://api.telegram.org"

async def _tg_get_file_url(file_id: str) -> str:
    token = (os.getenv("TG_TOKEN") or os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="Bot token is not set on server")

    file_id = (file_id or "").strip()
    if not file_id:
        raise HTTPException(status_code=400, detail="empty file_id")

    url = f"{TELEGRAM_API}/bot{token}/getFile"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params={"file_id": file_id}, timeout=15) as r:  # type: ignore[arg-type]
            data = await r.json()
            if r.status != 200 or not data.get("ok"):
                raise HTTPException(status_code=502, detail={"tg_getFile": data})

    file_path = ((data.get("result") or {}).get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(status_code=502, detail={"tg_getFile": "no file_path", "raw": data})

    return f"{TELEGRAM_API}/file/bot{token}/{urllib.parse.quote(file_path)}"


@router.get("/resolve")
async def resolve_track(
    tg_id: int = Query(..., description="Telegram user id"),
    track_id: int = Query(..., description="UserTrack.id"),
    session: AsyncSession = Depends(session_dep),
) -> Dict[str, Any]:
    # verify owner
    user: Optional[User] = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    t: Optional[UserTrack] = (
        (await session.execute(
            select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.id == track_id)
        )).scalar_one_or_none()
    )
    if not t:
        raise HTTPException(status_code=404, detail="track not found")

    fid = (t.file_id or "").strip()
    if not fid:
        raise HTTPException(status_code=400, detail="empty track file_id")

    # direct https link
    if fid.startswith("https://"):
        return {"ok": True, "url": fid, "kind": "url"}

    # telegram file_id -> direct file url
    url = await _tg_get_file_url(fid)
    return {"ok": True, "url": url, "kind": "tg_file"}



async def _session_dep() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


@router.get("/health")
async def health() -> Dict[str, str]:
    return {"ok": "1"}


@router.get("/my")
async def my_playlist(
    tg_id: int = Query(..., description="Telegram user id (from initDataUnsafe.user.id)"),
    session: AsyncSession = Depends(session_dep),
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


@router.get("/search")
async def search_youtube(q: str = Query(..., min_length=2), limit: int = 12):
    """Search music videos (original + remixes) via YouTube Data API."""
    api_key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY is not set")

    limit = max(1, min(int(limit or 12), 25))

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": q,
        "type": "video",
        "videoCategoryId": "10",  # Music
        "maxResults": str(limit),
        "safeSearch": "none",
        "key": api_key,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=15) as r:  # type: ignore[arg-type]
            data = await r.json()
            if r.status != 200:
                raise HTTPException(status_code=502, detail=data)

    items = []
    for it in data.get("items", []) or []:
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        thumbs = sn.get("thumbnails") or {}
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
        items.append({
            "video_id": vid,
            "title": sn.get("title") or "",
            "channel": sn.get("channelTitle") or "",
            "thumb": thumb,
            "published": sn.get("publishedAt"),
        })

    return {"q": q, "items": items}


async def _bot_token() -> str:
    token = (os.getenv("TG_TOKEN") or os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="Bot token is not set on server")
    return token


async def _tg_send_audio(chat_id: int, audio_ref: str, caption: str = "") -> Dict[str, Any]:
    """
    Send audio via Telegram Bot API.
    audio_ref can be:
      - Telegram file_id
      - direct https:// URL to audio file
    """
    token = await _bot_token()
    url = f"{TELEGRAM_API}/bot{token}/sendAudio"
    payload: Dict[str, Any] = {
        "chat_id": str(chat_id),
        "audio": audio_ref,
    }
    if caption:
        payload["caption"] = caption[:1024]

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload, timeout=20) as r:  # type: ignore[arg-type]
            data = await r.json()
            if r.status != 200 or not data.get("ok"):
                raise HTTPException(status_code=502, detail={"tg_sendAudio": data})
    return data


@router.post("/play")
async def play_track(
    tg_id: int = Query(..., description="Telegram user id"),
    track_id: int = Query(..., description="UserTrack.id"),
    session: AsyncSession = Depends(session_dep),
) -> Dict[str, Any]:
    """
    PROD flow:
    - MiniApp asks server to 'play' a saved track.
    - Server verifies owner and sends audio to user via Telegram.
    """
    user: Optional[User] = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    t: Optional[UserTrack] = (
        (await session.execute(
            select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.id == track_id)
        )).scalar_one_or_none()
    )
    if not t:
        raise HTTPException(status_code=404, detail="track not found")

    audio_ref = (t.file_id or "").strip()
    if not audio_ref:
        raise HTTPException(status_code=400, detail="empty track file_id")

    title = (t.title or "Track").strip()
    await _tg_send_audio(chat_id=tg_id, audio_ref=audio_ref, caption=f"🎧 {title}")

    return {"ok": True, "sent": True}

