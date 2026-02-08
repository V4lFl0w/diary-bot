from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession


@dataclass(frozen=True)
class FoundAudio:
    chat_id: int
    message_id: int
    title: str


def _normalize_chat_ref(x: str) -> str:
    """
    Accepts:
      - @username
      - username
      - https://t.me/username
      - t.me/username
      - -1001234567890
      - +invitehash (best-effort)
    Returns:
      - @username OR numeric id OR +hash
    """
    x = (x or "").strip()
    if not x:
        return ""
    x = re.sub(r"^https?://", "", x, flags=re.I)
    x = re.sub(r"^t\.me/", "", x, flags=re.I)
    x = x.split("?", 1)[0].strip().strip("/")
    if not x:
        return ""
    if re.fullmatch(r"-?\d+", x):
        return x
    if x.startswith("+"):
        return x
    if not x.startswith("@"):
        x = "@" + x.lstrip("@")
    return x


def _env_list(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\n,]+", raw)
    out: list[str] = []
    for part in parts:
        part = _normalize_chat_ref(part)
        if part:
            out.append(part)
    return out


def _safe_title(text: str, fallback: str) -> str:
    t = (text or "").strip()
    if not t:
        return fallback
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


async def search_audio_in_tg(*, query: str, limit_per_chat: int = 25) -> Optional[FoundAudio]:
    """
    Search audio in predefined Telegram chats/channels using Telethon userbot.
    Env required:
      USERBOT_API_ID
      USERBOT_API_HASH
      USERBOT_SESSION
      USERBOT_AUDIO_CHATS (csv: t.me/... or @user or -100id)
    Returns first match as FoundAudio(chat_id, message_id, title)
    """
    q = (query or "").strip()
    if not q:
        return None

    api_id = (os.getenv("USERBOT_API_ID") or "").strip()
    api_hash = (os.getenv("USERBOT_API_HASH") or "").strip()
    sess = (os.getenv("USERBOT_SESSION") or "").strip()
    if not api_id or not api_hash or not sess:
        return None

    chats = _env_list("USERBOT_AUDIO_CHATS")
    if not chats:
        return None

    client = TelegramClient(StringSession(sess), int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return None

        # iterate chats
        for chat in chats:
            try:
                async for m in client.iter_messages(chat, search=q, limit=int(limit_per_chat)):
                    if not m:
                        continue

                    # audio file
                    if getattr(m, "audio", None) is not None:
                        title = _safe_title(getattr(m, "text", "") or getattr(m.audio, "title", "") or "", q)
                        chat_id = int(getattr(m, "chat_id", 0) or 0)
                        if chat_id:
                            return FoundAudio(chat_id=chat_id, message_id=int(m.id), title=title)

                    # document that is an audio
                    doc = getattr(m, "document", None)
                    if doc is not None:
                        mime = (getattr(doc, "mime_type", "") or "").lower()
                        if mime.startswith("audio/"):
                            title = _safe_title(getattr(m, "text", "") or "", q)
                            chat_id = int(getattr(m, "chat_id", 0) or 0)
                            if chat_id:
                                return FoundAudio(chat_id=chat_id, message_id=int(m.id), title=title)

            except Exception:
                continue

        return None
    finally:
        await client.disconnect()
