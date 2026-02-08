from __future__ import annotations

import os
import re
import asyncio
from dataclasses import dataclass
from typing import Optional, Iterable, List

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Message


def _normalize_chat_ref(x: str) -> str:
    """
    Accepts:
      - @username
      - username
      - https://t.me/username
      - t.me/username
      - -100123...
    Returns string reference suitable for Telethon iter_messages/chat resolution.
    """
    x = (x or "").strip()
    if not x:
        return ""
    x = re.sub(r"^https?://", "", x, flags=re.I)
    x = re.sub(r"^t\\.me/", "", x, flags=re.I)
    x = x.split("?", 1)[0].strip().strip("/")
    if not x:
        return ""
    if re.fullmatch(r"-?\\d+", x):
        return x
    if x.startswith("+"):
        return x
    if not x.startswith("@"):
        x = "@" + x.lstrip("@")
    return x


def _env_list(name: str) -> List[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,\n]+", raw)
    out: List[str] = []
    for x in parts:
        x = _normalize_chat_ref(x)
        if x:
            out.append(x)
    return out


def _clean_query(q: str) -> str:
    q = (q or "").strip()
    q = q.replace("—", "-")
    q = re.sub(r"\\s+", " ", q)
    q = re.sub(r"[\\[\\](){}]", " ", q)
    q = re.sub(r"\\s+", " ", q).strip()
    return q


def _query_variants(*qs: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for q in qs:
        q = (q or "").strip()
        if not q:
            continue

        cand = [q, _clean_query(q)]
        # ещё варианты: убрать двойные дефисы/пробелы
        cand.append(re.sub(r"\\s*-\\s*", " - ", _clean_query(q)))
        cand.append(re.sub(r"\\s*-\\s*", " ", _clean_query(q)))

        for c in cand:
            c = re.sub(r"\\s+", " ", (c or "").strip())
            if len(c) < 2:
                continue
            if c.lower() in seen:
                continue
            seen.add(c.lower())
            out.append(c)
    return out[:12]


def _is_audio_message(m: Message) -> bool:
    if not m:
        return False
    if getattr(m, "audio", None) is not None:
        return True
    if getattr(m, "document", None) is not None:
        mime = (getattr(getattr(m, "document", None), "mime_type", "") or "").lower()
        if mime.startswith("audio/"):
            return True
    return False


def _pick_title(m: Message, fallback: str) -> str:
    # Telethon: у audio обычно есть attributes с title/performer, но это не всегда
    audio = getattr(m, "audio", None)
    performer = getattr(audio, "performer", None) if audio else None
    title = getattr(audio, "title", None) if audio else None
    if performer and title:
        return f"{performer} - {title}".strip()
    if title:
        return str(title).strip()
    # пробуем caption/text
    txt = (getattr(m, "message", None) or "").strip()
    if txt:
        return txt.split("\n", 1)[0][:120]
    return (fallback or "Track").strip()


@dataclass(frozen=True)
class FoundAudio:
    chat_ref: str   # то, что мы брали из USERBOT_AUDIO_CHATS (например @muzykaa или -100...)
    chat_id: int    # numeric chat_id из сообщения (если есть)
    message_id: int
    title: str


async def search_audio_in_tg(
    *,
    query: str,
    title: Optional[str] = None,
    limit_per_chat: int = 40,
) -> Optional[FoundAudio]:
    """
    Search audio messages across USERBOT_AUDIO_CHATS using Telethon userbot.
    Returns first matched audio/document(audio/*).
    Env:
      USERBOT_API_ID
      USERBOT_API_HASH
      USERBOT_SESSION   (StringSession)
      USERBOT_AUDIO_CHATS  (csv: @chan1, https://t.me/chan2, -100...)
    """
    api_id = (os.getenv("USERBOT_API_ID") or "").strip()
    api_hash = (os.getenv("USERBOT_API_HASH") or "").strip()
    session_str = (os.getenv("USERBOT_SESSION") or "").strip()
    chats = _env_list("USERBOT_AUDIO_CHATS")

    if not api_id or not api_hash or not session_str:
        return None
    if not chats:
        return None

    qlist = _query_variants(title or "", query or "")

    client = TelegramClient(StringSession(session_str), int(api_id), api_hash)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None

        for chat_ref in chats:
            # на каждый чат — несколько попыток по разным вариантам
            for q in qlist:
                try:
                    async for m in client.iter_messages(chat_ref, search=q, limit=int(limit_per_chat)):
                        if not m:
                            continue
                        if not _is_audio_message(m):
                            continue
                        chat_id = int(getattr(m, "chat_id", 0) or 0)
                        return FoundAudio(
                            chat_ref=str(chat_ref),
                            chat_id=chat_id,
                            message_id=int(m.id),
                            title=_pick_title(m, q),
                        )
                except Exception:
                    continue
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
