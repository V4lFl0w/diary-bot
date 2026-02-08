
from __future__ import annotations

import os
import re
import asyncio
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Iterable, Tuple

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Message


def _normalize_chat_ref(x: str) -> str:
    """
    Accepts: @username, username, https://t.me/username, t.me/username, -100123..., https://t.me/+hash
    Returns string suitable for Telethon iter_messages/get_entity.
    """
    x = (x or "").strip()
    if not x:
        return ""
    x = re.sub(r"^https?://", "", x, flags=re.I)
    x = re.sub(r"^(t\.me/|telegram\.me/)", "", x, flags=re.I)
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
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _query_variants(title: str | None, query: str) -> List[str]:
    """
    Делает варианты для поиска по TG каналам:
    - оригинал
    - чистка скобок
    - если есть "Artist - Track", пробуем "Artist Track" и "Track"
    """
    variants: List[str] = []
    base = _clean_query(query)
    if base:
        variants.append(base)

    if title:
        t = _clean_query(title)
        if t and t not in variants:
            variants.append(t)

    # убрать скобки и их содержимое
    def strip_parens(s: str) -> str:
        s2 = re.sub(r"\([^)]*\)", "", s)
        s2 = re.sub(r"\[[^\]]*\]", "", s2)
        return _clean_query(s2)

    for s in list(variants):
        sp = strip_parens(s)
        if sp and sp not in variants:
            variants.append(sp)

    # варианты для "A - B"
    more: List[str] = []
    for s in list(variants):
        if " - " in s:
            a, b = [x.strip() for x in s.split(" - ", 1)]
            if a and b:
                more += [f"{a} {b}", b]
    for s in more:
        s = _clean_query(s)
        if s and s not in variants:
            variants.append(s)

    # укоротить слишком длинные (TG search хуже на 200+ символах)
    final: List[str] = []
    for s in variants:
        s = s[:96].strip()
        if s and s not in final:
            final.append(s)
    return final


def _is_audio_message(m: Message) -> bool:
    # audio/document with audio mimetype
    if getattr(m, "audio", None):
        return True
    doc = getattr(m, "document", None)
    if doc and getattr(doc, "mime_type", ""):
        mt = (doc.mime_type or "").lower()
        if mt.startswith("audio/"):
            return True
    return False


def _pick_title(m: Message, fallback: str) -> str:
    # пробуем из атрибутов, иначе текст
    try:
        if getattr(m, "audio", None):
            a = m.audio
            t = getattr(a, "title", None) or ""
            perf = getattr(a, "performer", None) or ""
            if perf and t:
                return f"{perf} - {t}".strip()
            if t:
                return t.strip()
        # document attributes
        doc = getattr(m, "document", None)
        if doc and getattr(doc, "attributes", None):
            for a in doc.attributes:
                cls = a.__class__.__name__.lower()
                if "documentattributeaudio" in cls:
                    t = getattr(a, "title", None) or ""
                    perf = getattr(a, "performer", None) or ""
                    if perf and t:
                        return f"{perf} - {t}".strip()
                    if t:
                        return t.strip()
    except Exception:
        pass
    text = (getattr(m, "message", None) or "").strip()
    return (text[:80] if text else fallback).strip()


@dataclass(frozen=True)
class FoundAudio:
    chat_id: int
    message_id: int
    title: str
    chat_ref: str  # что реально искали (например @channel или -100..)


def _client() -> TelegramClient:
    api_id = (os.getenv("USERBOT_API_ID") or "").strip()
    api_hash = (os.getenv("USERBOT_API_HASH") or "").strip()
    session_str = (os.getenv("USERBOT_SESSION") or "").strip()
    if not api_id or not api_hash or not session_str:
        raise RuntimeError("USERBOT_NOT_CONFIGURED")
    return TelegramClient(StringSession(session_str), int(api_id), api_hash)


async def search_audio_in_tg(
    *,
    query: str,
    title: str | None = None,
    limit_per_chat: int = 80,
    max_total_scans: int = 900,
) -> Optional[FoundAudio]:
    """
    Ищет аудио по списку USERBOT_AUDIO_CHATS (каналы/чаты).
    Возвращает ПЕРВОЕ найденное аудио.
    """
    chats = _env_list("USERBOT_AUDIO_CHATS")
    if not chats:
        raise RuntimeError("USERBOT_AUDIO_CHATS_EMPTY")

    variants = _query_variants(title, query)
    if not variants:
        return None

    scanned = 0
    async with _client() as client:
        for q in variants:
            for chat in chats:
                if scanned >= max_total_scans:
                    return None
                scanned += 1
                try:
                    async for m in client.iter_messages(chat, search=q, limit=int(limit_per_chat)):
                        if not m:
                            continue
                        if _is_audio_message(m):
                            chat_id = int(getattr(m, "chat_id", 0) or 0)
                            if not chat_id:
                                continue
                            return FoundAudio(
                                chat_id=chat_id,
                                message_id=int(m.id),
                                title=_pick_title(m, q),
                                chat_ref=str(chat),
                            )
                except Exception:
                    continue
    return None


async def debug_search_audio_in_tg(
    *,
    query: str,
    title: str | None = None,
    limit_per_chat: int = 40,
    per_chat_hits: int = 3,
) -> Dict[str, Any]:
    """
    Дебаг: показывает по каждому каналу:
    - какие варианты q пробовали
    - нашлось ли аудио
    - несколько первых кандидатов (title + msg_id)
    """
    chats = _env_list("USERBOT_AUDIO_CHATS")
    variants = _query_variants(title, query)

    out: Dict[str, Any] = {
        "query": query,
        "title": title,
        "variants": variants,
        "chats": chats,
        "results": [],
    }
    if not chats:
        out["error"] = "USERBOT_AUDIO_CHATS_EMPTY"
        return out
    if not variants:
        out["error"] = "EMPTY_QUERY"
        return out

    async with _client() as client:
        for chat in chats:
            chat_row: Dict[str, Any] = {"chat": chat, "hits": [], "errors": []}
            try:
                for q in variants:
                    try:
                        hits = 0
                        async for m in client.iter_messages(chat, search=q, limit=int(limit_per_chat)):
                            if not m:
                                continue
                            if _is_audio_message(m):
                                chat_id = int(getattr(m, "chat_id", 0) or 0)
                                if not chat_id:
                                    continue
                                chat_row["hits"].append({
                                    "q": q,
                                    "chat_id": chat_id,
                                    "message_id": int(m.id),
                                    "title": _pick_title(m, q),
                                })
                                hits += 1
                                if hits >= per_chat_hits:
                                    break
                    except Exception as e:
                        chat_row["errors"].append({"q": q, "err": type(e).__name__})
            except Exception as e:
                chat_row["errors"].append({"err": type(e).__name__})
            out["results"].append(chat_row)

    return out
