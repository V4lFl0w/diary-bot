from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

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


def _doc_id(m: Message) -> int:
    """Best-effort unique audio/document id for dedupe across many channels."""
    try:
        doc = getattr(m, "document", None)
        if doc and getattr(doc, "id", None):
            return int(doc.id)
    except Exception:
        pass
    return 0


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
    seen_docs_in_one: set[int] = set()
    seen_titles_in_one: set[str] = set()
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
                            doc_id = _doc_id(m)
                            t = _pick_title(m, q)
                            nt = _norm_text(t)
                            if doc_id and doc_id in seen_docs_in_one:
                                continue
                            if nt in seen_titles_in_one:
                                continue
                            if doc_id:
                                seen_docs_in_one.add(doc_id)
                            seen_titles_in_one.add(nt)
                            return FoundAudio(
                                chat_id=chat_id,
                                message_id=int(m.id),
                                title=t,
                                chat_ref=str(chat),
                            )
                except Exception:
                    continue
    return None


# ===== Storage bridge (UserBot -> Storage Channel) =====
def _storage_peer() -> tuple[object, int]:
    """
    Returns:
      (peer_for_telethon, numeric_chat_id_for_cache)
    Config:
      MUSIC_STORAGE_CHAT_ID = -100...
      MUSIC_STORAGE_CHAT    = @DiaryMusicTOP (optional fallback)
    """
    sid = (os.getenv("MUSIC_STORAGE_CHAT_ID") or "").strip()
    sref = (os.getenv("MUSIC_STORAGE_CHAT") or os.getenv("MUSIC_STORAGE_CHAT_REF") or "").strip()

    chat_id = 0
    if sid:
        try:
            chat_id = int(sid)
        except Exception:
            chat_id = 0

    if chat_id:
        return chat_id, chat_id

    sref = _normalize_chat_ref(sref)
    if not sref:
        raise RuntimeError("STORAGE_NOT_CONFIGURED: set MUSIC_STORAGE_CHAT_ID or MUSIC_STORAGE_CHAT")
    return sref, 0


async def forward_to_storage(*, src: str | int, message_id: int) -> dict:
    """
    Telethon forward from donor chat/channel -> our Storage channel.
    Returns:
      {
        "storage_peer": <int|-100... or '@username'>,
        "storage_chat_id": <int or 0>,
        "storage_message_id": <int>
      }
    """
    storage_peer, storage_chat_id = _storage_peer()
    src_peer = _normalize_chat_ref(str(src)) if not isinstance(src, int) else src

    mid = int(message_id)
    async with _client() as client:
        # IMPORTANT: never do plain forward without as_copy (it reveals source).
        # 1) try as_copy=True
        # 2) fallback: download+reupload (also no-source)
        msgs = None
        try:
            msgs = await client.forward_messages(storage_peer, mid, from_peer=src_peer, as_copy=True)
        except Exception:
            msgs = None

        if not msgs:
            src_msg = await client.get_messages(src_peer, ids=mid)
            if not src_msg:
                raise RuntimeError("FORWARD_FAILED: source message not found")
            data = await client.download_media(src_msg, file=bytes)
            if not data:
                raise RuntimeError("FORWARD_FAILED: cannot download media")
            caption = (getattr(src_msg, "message", None) or "").strip()
            sent = await client.send_file(storage_peer, file=data, caption=caption or None)
            msgs = sent
        # telethon: may return Message or list[Message]
        m0 = msgs[0] if isinstance(msgs, list) else msgs
        smid = int(getattr(m0, "id", 0) or 0)
        if not smid:
            raise RuntimeError("FORWARD_FAILED: cannot extract storage message id")

    return {
        "storage_peer": storage_peer,
        "storage_chat_id": int(storage_chat_id or 0),
        "storage_message_id": smid,
    }


def _norm_text(x: str) -> str:
    x = (x or "").lower()
    x = re.sub(r"\s+", " ", x).strip()
    return x


def _tokens(x: str) -> list[str]:
    x = _norm_text(x)
    # оставим слова/цифры/кириллица/латиница
    x = re.sub(r"[^0-9a-zа-яё\s]+", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    return [t for t in x.split(" ") if t]


def _match_all_tokens(title: str, query: str) -> bool:
    tt = _tokens(title)
    qt = _tokens(query)
    if not qt:
        return True
    tset = set(tt)
    return all(t in tset for t in qt)


def _looks_like_variant(title: str, base: str) -> bool:
    # base = "жизнь" или другое слово
    t = _norm_text(title)
    b = _norm_text(base)
    if not b:
        return False
    if b in t:
        return True
    # ремиксы/версии часто идут как "(remix)" или " - remix" и т.п.
    return False


async def search_audio_many_in_tg(
    *,
    query: str,
    title: str | None = None,
    per_chat_limit: int = 60,
    max_tracks: int = 6,
    strict_tokens: bool = False,
) -> list[FoundAudio]:
    """
    Возвращает НЕ 1 трек, а список (до max_tracks).
    strict_tokens=False: мягко (под "Вектор А" отдаём топовые совпадения)
    strict_tokens=True: строго (все токены запроса должны быть в тайтле/атрибутах)
    """
    chats = _env_list("USERBOT_AUDIO_CHATS")
    if not chats:
        raise RuntimeError("USERBOT_AUDIO_CHATS_EMPTY")

    variants = _query_variants(title, query)
    if not variants:
        return []
    seen: set[tuple[int, int]] = set()
    seen_docs: set[int] = set()
    seen_titles: set[str] = set()
    out: list[FoundAudio] = []

    async with _client() as client:
        for q in variants:
            for chat in chats:
                try:
                    async for m in client.iter_messages(chat, search=q, limit=int(per_chat_limit)):
                        if not m:
                            continue
                        if not _is_audio_message(m):
                            continue
                        chat_id = int(getattr(m, "chat_id", 0) or 0)
                        if not chat_id:
                            continue
                        key = (chat_id, int(m.id))
                        if key in seen:
                            continue

                        doc_id = _doc_id(m)
                        if doc_id and doc_id in seen_docs:
                            continue

                        t = _pick_title(m, q)
                        nt = _norm_text(t)
                        if nt in seen_titles:
                            continue

                        if strict_tokens and not _match_all_tokens(t, query):
                            continue

                        seen.add(key)
                        if doc_id:
                            seen_docs.add(doc_id)
                        seen_titles.add(nt)
                        out.append(
                            FoundAudio(
                                chat_id=chat_id,
                                message_id=int(m.id),
                                title=t,
                                chat_ref=str(chat),
                            )
                        )
                        if len(out) >= int(max_tracks):
                            return out
                except Exception:
                    continue
    return out


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
                                chat_row["hits"].append(
                                    {
                                        "q": q,
                                        "chat_id": chat_id,
                                        "message_id": int(m.id),
                                        "title": _pick_title(m, q),
                                    }
                                )
                                hits += 1
                                if hits >= per_chat_hits:
                                    break
                    except Exception as e:
                        chat_row["errors"].append({"q": q, "err": type(e).__name__})
            except Exception as e:
                chat_row["errors"].append({"err": type(e).__name__})
            out["results"].append(chat_row)

    return out
