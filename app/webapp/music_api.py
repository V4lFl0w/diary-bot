from __future__ import annotations
import os
import aiohttp

from typing import List, Dict, Any, Optional, AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import async_session

from app.models.user import User
from app.models.user_track import UserTrack

# MVP-авторизация: tg_id прилетает с фронта (Telegram WebApp initDataUnsafe.user.id)
# Дальше можно усилить до verify initData подписи.



router = APIRouter(prefix="/webapp/music/api", tags=["webapp-music"])

def _parse_tgmsg(fid: str) -> Optional[tuple[int,int]]:
    fid = (fid or "").strip()
    if not fid.startswith("tgmsg:"):
        return None
    try:
        _, a, b = fid.split(":", 2)
        return (int(a), int(b))
    except Exception:
        return None


from typing import AsyncIterator
from app.db import async_session

async def session_dep() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session

import urllib.parse

TELEGRAM_API = "https://api.telegram.org"

async def _tg_copy_message(*, to_chat_id: int, from_chat_id: int, message_id: int) -> Dict[str, Any]:
    """
    Copy audio message from a source channel/chat to user chat via Bot API copyMessage.
    IMPORTANT: Bot must have access to the source chat/channel.
    """
    token = await _bot_token()
    url = f"{TELEGRAM_API}/bot{token}/copyMessage"
    payload: Dict[str, Any] = {
        "chat_id": str(int(to_chat_id)),
        "from_chat_id": str(int(from_chat_id)),
        "message_id": str(int(message_id)),
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload, timeout=20) as r:  # type: ignore[arg-type]
            data = await r.json()
            if r.status != 200 or not data.get("ok"):
                raise HTTPException(status_code=502, detail={"tg_copyMessage": data})
    return data


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
    tg_id: Optional[int] = Query(None, description="Telegram user id"),
    track_id: int = Query(..., description="UserTrack.id"),
    x_tg_id: Optional[int] = Header(None, alias="X-TG-ID"),
    session: AsyncSession = Depends(session_dep),
) -> Dict[str, Any]:
    tg_id = tg_id or x_tg_id
    if not tg_id:
        raise HTTPException(status_code=400, detail="tg_id missing (open mini app inside Telegram)")

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
    tg_id: Optional[int] = Query(None, description="Telegram user id (from initDataUnsafe.user.id)"),
    
    x_tg_id: Optional[int] = Header(None, alias="X-TG-ID"),
session: AsyncSession = Depends(session_dep),
) -> Dict[str, Any]:
    tg_id = tg_id or x_tg_id
    if not tg_id:
        raise HTTPException(status_code=400, detail="tg_id missing (open mini app inside Telegram)")

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

async def _tg_send_message(chat_id: int, text: str) -> Dict[str, Any]:
    token = await _bot_token()
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload: Dict[str, Any] = {"chat_id": str(chat_id), "text": (text or "")[:4096]}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload, timeout=20) as r:  # type: ignore[arg-type]
            data = await r.json()
            if r.status != 200 or not data.get("ok"):
                raise HTTPException(status_code=502, detail={"tg_sendMessage": data})
    return data



async def _tg_send_audio_file(chat_id: int, file_path, title: str = "", caption: str = "") -> Dict[str, Any]:
    """Send local audio file via Telegram Bot API (multipart). Returns raw API JSON."""
    token = await _bot_token()
    url = f"{TELEGRAM_API}/bot{token}/sendAudio"

    import aiohttp

    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    if caption:
        form.add_field("caption", caption[:1024])
    if title:
        form.add_field("title", title[:255])

    fp = str(file_path)
    with open(fp, "rb") as f:
        form.add_field(
            "audio",
            f,
            filename=(title or "track") + ".mp3",
            content_type="audio/mpeg",
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
              url,
              data=form,
              timeout=aiohttp.ClientTimeout(total=120),
        ) as r:
                data = await r.json()
                if r.status != 200 or not data.get("ok"):
                    raise HTTPException(status_code=502, detail={"tg_sendAudio_file": data})
                return data


from app.services.music_full_sender import send_or_fetch_full_track
from app.services.userbot_audio_search import search_audio_in_tg




from typing import Optional, Dict, Any
from fastapi import Query, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_track import UserTrack
from app.webapp.music_api import router, session_dep
from app.services.downloader import download_from_youtube
from aiogram.types import FSInputFile
from app.webapp.music_api import _tg_send_audio
from app.bot import bot


@router.post("/play")
async def play_track(
    tg_id: Optional[int] = Query(None),
    x_tg_id: Optional[int] = Header(None, alias="X-TG-ID"),
    track_id: Optional[int] = Query(None, description="UserTrack.id"),
    kind: str = Query("my", description="my|search"),
    title: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    video_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(session_dep),
) -> Dict[str, Any]:
    tg_id = tg_id or x_tg_id
    if not tg_id:
        raise HTTPException(status_code=400, detail="tg_id missing (open mini app inside Telegram)")

    user: Optional[User] = (
        await session.execute(select(User).where(User.tg_id == int(tg_id)))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    # ===== MY PLAYLIST =====
    if kind == "my":
        if not track_id:
            raise HTTPException(status_code=400, detail="track_id required")

        track: Optional[UserTrack] = (
            await session.execute(
                select(UserTrack).where(
                    UserTrack.user_id == user.id,
                    UserTrack.id == int(track_id),
                )
            )
        ).scalar_one_or_none()
        if not track:
            raise HTTPException(status_code=404, detail="track not found")

        fid = (track.file_id or "").strip()
        if not fid:
            raise HTTPException(status_code=409, detail="track has no file_id")

        await _tg_send_audio(
            chat_id=int(tg_id),
            audio_ref=fid,
            caption=f"🎧 {track.title or 'Track'}",
        )
        return {"ok": True, "source": "my"}

    # ===== SEARCH (yt-dlp → cache) =====
    if kind == "search":
        if not title or not query:
            raise HTTPException(status_code=400, detail="title and query required")

        # ===== TG SEARCH via UserBot (NO YouTube download) =====
        found = await search_audio_in_tg(query=query)
        if not found:
            raise HTTPException(status_code=409, detail={"code": "TG_AUDIO_NOT_FOUND", "hint": "Не нашёл аудио в Telegram-источниках. Добавь трек в плейлист через бота (скинь аудио/файл/прямую ссылку)."})
        # copyMessage -> user chat (background works)
        await _tg_copy_message(to_chat_id=int(tg_id), from_chat_id=int(found.chat_id), message_id=int(found.message_id))

        # cache as tgmsg:<chat_id>:<msg_id>
        fid = f"tgmsg:{int(found.chat_id)}:{int(found.message_id)}"
        track = UserTrack(
            user_id=user.id,
            tg_id=int(tg_id),
            title=title or found.title or query,
            file_id=fid,
        )
        session.add(track)
        await session.commit()
        return {"ok": True, "source": "tg_search", "cached": True}
        try:
            audio_path = download_from_youtube(query)
        except RuntimeError as e:
            code = str(e) or "YTDLP_ERROR"

            # IMPORTANT: do not "fail silently" in UI — send youtube link to Telegram chat
            if code == "YTDLP_YT_BOT_CHECK":
                yurl = f"https://www.youtube.com/watch?v={video_id}" if (video_id or "").strip() else None
                text = "⚠️ YouTube блокнул скачивание (anti-bot). Открой в YouTube:\n"
                text += (yurl or "https://www.youtube.com/")
                try:
                    await _tg_send_message(int(tg_id), text)
                except Exception:
                    pass
                return {"ok": False, "code": code, "youtube_url": yurl, "hint": "open_youtube"}

            if code == "YTDLP_NOT_INSTALLED":
                raise HTTPException(status_code=503, detail=code)

            raise HTTPException(status_code=502, detail=code)

        data = await _tg_send_audio_file(
            chat_id=int(tg_id),
            file_path=audio_path,
            title=title,
            caption=f"🎧 {title}",
        )

        try:
            file_id = (((data or {}).get("result") or {}).get("audio") or {}).get("file_id")
        except Exception:
            file_id = None
        if not file_id:
            raise HTTPException(status_code=502, detail={"no_file_id_in_sendAudio": data})

        # cache as new playlist item
        track = UserTrack(
            user_id=user.id,
            tg_id=int(tg_id),
            title=title,
            file_id=file_id,
        )
        session.add(track)
        await session.commit()

        return {"ok": True, "source": "search", "cached": True}

    raise HTTPException(status_code=400, detail="unknown kind")
