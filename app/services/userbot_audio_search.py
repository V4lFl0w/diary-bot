from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Iterable

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Message

@dataclass(frozen=True)
class FoundAudio:
    chat_id: int
    message_id: int
    title: str

def _env_list(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

def _is_audio_message(m: Message) -> bool:
    # audio as Document with mime audio/* or has .audio shortcut
    try:
        if getattr(m, "audio", None) is not None:
            return True
        doc = getattr(m, "document", None)
        if not doc:
            return False
        mime = (getattr(doc, "mime_type", None) or "").lower()
        return mime.startswith("audio/")
    except Exception:
        return False

def _pick_title(m: Message, fallback: str) -> str:
    # best-effort title
    try:
        # Telethon: m.audio may have attributes
        a = getattr(m, "audio", None)
        if a:
            t = getattr(a, "title", None)
            p = getattr(a, "performer", None)
            if t and p:
                return f"{p} — {t}"
            if t:
                return str(t)
        # caption/text fallback
        cap = (getattr(m, "message", None) or "").strip()
        if cap:
            return cap[:120]
    except Exception:
        pass
    return fallback[:120]

async def search_audio_in_tg(*, query: str, limit_per_chat: int = 30) -> Optional[FoundAudio]:
    """
    Ищем аудио в заранее заданных чатах/каналах.
    USERBOT_AUDIO_CHATS: csv (например: @my_audio_channel,-1001234567890)
    """
    api_id = (os.getenv("USERBOT_API_ID") or "").strip()
    api_hash = (os.getenv("USERBOT_API_HASH") or "").strip()
    session_str = (os.getenv("USERBOT_SESSION") or "").strip()

    chats = _env_list("USERBOT_AUDIO_CHATS")
    if not api_id or not api_hash or not session_str or not chats:
        return None

    q = (query or "").strip()
    if len(q) < 2:
        return None

    async with TelegramClient(StringSession(session_str), int(api_id), api_hash) as client:
        for chat in chats:
            try:
                # Telethon умеет search по сообщениям
                async for m in client.iter_messages(chat, search=q, limit=int(limit_per_chat)):
                    if not m:
                        continue
                    if _is_audio_message(m):
                        title = _pick_title(m, q)
                        cid = int(getattr(m, "peer_id", None).channel_id) * -1 if getattr(getattr(m, "peer_id", None), "channel_id", None) else None
                        # проще/надёжнее: брать chat id через m.chat_id
                        chat_id = int(getattr(m, "chat_id", None) or 0)
                        if not chat_id:
                            # fallback (редко)
                            chat_id = cid or 0
                        if not chat_id:
                            continue
                        return FoundAudio(chat_id=chat_id, message_id=int(m.id), title=title)
            except Exception:
                continue
    return None
