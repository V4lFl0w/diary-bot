from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Iterable

def _normalize_chat_ref(x: str) -> str:
    """
    Accepts: @username, username, https://t.me/username, t.me/username, https://t.me/+hash (invite)
    Returns string reference suitable for Telethon client.iter_messages(...)
    """
    x = (x or "").strip()
    if not x:
        return ""
    # drop scheme/domain
    x = re.sub(r"^https?://", "", x, flags=re.I)
    x = re.sub(r"^t\.me/", "", x, flags=re.I)
    # keep invite links as-is (Telethon can often resolve via get_entity on +hash)
    x = x.strip()
    x = x.split("?", 1)[0]
    x = x.strip("/")
    if not x:
        return ""
    # if already numeric id
    if re.fullmatch(r"-?\d+", x):
        return x
    # ensure @ for usernames
    if x.startswith("+"):
        return x  # invite hash
    if not x.startswith("@"):
        x = "@" + x.lstrip("@")
    return x

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

    parts = re.split(r"[,\n]+", raw)
    out: list[str] = []
    for x in parts:
        x = (x or "").strip()
        if not x:
            continue
        x = _normalize_chat_ref(x)
        if x:
            out.append(x)
    return out
